"""Current sealed figure Intake attachments reach UI without author-supplied IDs."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from curve_figure_evidence.instance_views import build_presentation as figures
from scidiscovery.artifact_agent.approval_ui import presentation
from scidiscovery.artifact_agent.approval_ui.presentation_render import render_presentation
from scidiscovery.artifact_agent.schema.artifact import ArtifactRegistration
from scidiscovery.artifact_agent.schema.common import canonical_json
from tests.operations.test_instance_read_model import system, bind, run, approval
from tests.operations.test_instance_content_display import picture, register
from tests.operations.test_instance_browser_http import _request
from scidiscovery.artifact_agent.approval_ui.app import ApprovalUI
from scidiscovery.artifact_agent.approval_ui.access import access_cookie
from urllib.parse import urlencode
from tests.operations.test_instance_archive import archive_system, save_archive, restore_archive


@pytest.fixture
def figure_providers(monkeypatch):
    monkeypatch.setattr(presentation, 'entry_points', lambda **_: (
        SimpleNamespace(name='curve_figure_evidence', load=lambda: figures),))


def _seed_family(system):
    labels = {'operation_id': 'science.evidence.extract.figure.v3', 'operation_version': '1',
              'operation_digest': 'a' * 64, 'tool_producer_run': 'extract'}
    def save(name, payload, schema='opaque', media='application/json', parents=(), port='tool_evidence'):
        return system.artifacts.register(payload if isinstance(payload, bytes) else canonical_json(payload),
            ArtifactRegistration(kind='fixture', schema_id=schema, payload_schema_version=1,
                media_type=media, creator=system.actor, parent_refs=parents,
                labels={**labels, 'operation_output_port': port}), idempotency_key=name)
    images, records, bindings = [], [], {}
    for i, name in enumerate(('source_panels/main.png', 'audit_overlays/main--numeric-redraw.png',
                              'audit_overlays/main--identity-fidelity.png', 'curve_tables/main--line.csv'), 1):
        media = 'text/csv' if name.endswith('.csv') else 'image/png'
        image = save(name, b'x,y\n1,2\n2,3\n' if media == 'text/csv' else picture(('white', 'blue', 'red')[i-1]), media=media)
        images.append(image)
        alias = f'tool_evidence_{i:03d}'
        records.append({'alias': alias, 'artifact_ref': image.ref.model_dump(mode='json'),
            'media_type': media, 'size_bytes': image.size_bytes,
            'tool_name': 'worker_curve_figure_save', 'metadata': {'kind': 'figure_file', 'data_item': name}})
        bindings[alias] = {'port_name': 'tool_evidence', 'artifact_ref': image.ref.model_dump(mode='json')}
    manifest = save('manifest', {'records': records, 'bindings': bindings},
        schema='scidiscovery.tool-evidence-manifest.v1', parents=tuple(i.ref for i in images),
        port='recovery_manifest_output')
    intake = save('intake', {'scientific_foundation': {'summary': 'Extracted figure', 'evidence': []}},
        schema='scidiscovery.scientific-intake.v1', parents=(manifest.ref,), port='scientific_intake')
    bind(system, system.a, 'artifact', 'intake', intake.artifact_id)
    return system, intake, manifest, images


@pytest.fixture
def figure_context(system, figure_providers):
    family = _seed_family(system)
    producer = run(system, 'extract', output=family[1].ref)
    producer.operation_id = 'science.evidence.extract.figure.v3'
    producer.operation_version = '1'
    return family


@pytest.mark.parametrize('key', ['artifact:intake', 'run:extract'])
def test_sealed_intake_images_render_without_manual_evidence_citations(figure_context, key):
    s, intake, manifest, images = figure_context
    context = s.model.node_context(s.a, key)
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=context['focus_artifact_ids'])
    assert [f['label'] for f in result['figures']] == ['论文原图', 'CSV 数值重绘', '原图叠点检查']
    assert {f['artifact_id'] for f in result['figures']} == {i.artifact_id for i in images[:3]}
    assert all(f['source']['artifact_id'] == manifest.artifact_id for f in result['figures'])
    html = render_presentation(result, evidence_href=lambda aid, p: f'/evidence/{aid}?pointer={p}',
                               image_href=lambda aid: f'/image/{aid}')
    assert html.count('<img ') == 3
    assert '图件与结果对照' in html
    assert '下载 CSV' in html


@pytest.mark.parametrize('damage', ['missing_image', 'wrong_hash', 'wrong_size', 'wrong_binding',
    'missing_parent', 'duplicate_alias', 'different_operation', 'different_run', 'partial_manifest'])
def test_intake_does_not_display_unbound_or_partial_images(figure_context, damage):
    s, intake, manifest, images = figure_context
    context = deepcopy(s.model.node_context(s.a, 'run:extract'))
    cohort = context['artifacts']
    m = next(a for a in cohort if a['artifact_id'] == manifest.artifact_id)
    image = next(a for a in cohort if a['artifact_id'] == images[0].artifact_id)
    record = m['payload']['records'][0]
    if damage == 'missing_image': cohort.remove(image)
    elif damage == 'wrong_hash': image['ref']['sha256'] = 'b' * 64
    elif damage == 'wrong_size': image['size_bytes'] += 1
    elif damage == 'wrong_binding': m['payload']['bindings'][record['alias']]['port_name'] = 'other'
    elif damage == 'missing_parent': m['provenance'] = m['provenance'][1:]
    elif damage == 'duplicate_alias': m['payload']['records'].append(deepcopy(record))
    elif damage == 'different_operation': m['family']['operation_digest'] = 'b' * 64
    elif damage == 'different_run': m['family']['tool_producer_run'] = 'other_run'
    elif damage == 'partial_manifest': m['gaps'].append({'code': 'display_field_partial'})
    result = presentation.build_presentation(cohort, focus_artifact_ids=context['focus_artifact_ids'])
    assert images[0].artifact_id not in {f['artifact_id'] for f in result['figures']}
    assert any(g['code'].startswith('figure_intake_') for g in result['gaps'])
    assert not any(g['code'] == 'provider_unavailable' for g in result['gaps'])


@pytest.mark.parametrize('schema', ['scidiscovery.evidence-audit.v1', 'scidiscovery.curve-bundle.v1',
    'scidiscovery.scientific-foundation.v1', 'scidiscovery.experiment-review.v1'])
def test_downstream_deliveries_keep_exact_figure_ancestry(figure_context, schema):
    s, intake, manifest, images = figure_context
    register(s, 'downstream', schema, {}, parents=(intake.ref,))
    context = s.model.node_context(s.a, 'artifact:downstream')
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=context['focus_artifact_ids'])
    assert {f['artifact_id'] for f in result['figures']} == {i.artifact_id for i in images[:3]}
    assert all(f['source']['artifact_id'] == manifest.artifact_id for f in result['figures'])


def test_intake_http_images_csv_and_frozen_approval_scope(figure_context):
    s, intake, manifest, images = figure_context
    outsider = register(s, 'outsider', 'opaque', {}, raw=picture('green'), media='image/png', instance=s.b)
    launch = approval(s, 'figure-review', (intake.ref,))
    ui = ApprovalUI(s.approvals, bindings=s.bindings, read_model=s.model, instance_management_secret=b'p'*32)
    base = ui.start()
    cookie = access_cookie(s.a, ui.browser_access.issue(s.a))
    try:
        status, _, page = _request(base, 'GET', f'/instance/{s.a}/nodes/artifact%3Aintake', cookie=cookie)
        assert status == 200 and page.count(b'<img ') == 3 and '下载 CSV' in page.decode()
        for member in images:
            mode = 'download' if member.media_type == 'text/csv' else 'image'
            path = f'/instance/{s.a}/evidence/{member.artifact_id}?format={mode}'
            status, _, content = _request(base, 'GET', path, cookie=cookie)
            assert status == 200 and content == s.artifacts.read(member.ref)
            path = f'/review/figure-review/evidence/{member.artifact_id}?' + urlencode({'token':launch.access_token,'format':mode})
            assert _request(base, 'GET', path)[0] == 200
        path = f'/review/figure-review/evidence/{outsider.artifact_id}?' + urlencode({'token':launch.access_token,'format':'image'})
        assert _request(base, 'GET', path)[0] == 403
    finally:
        ui.stop()


def test_archived_figure_family_retains_rendering_and_exact_originals(archive_system, figure_providers):
    runtime, archive, instance, _ = archive_system
    s = SimpleNamespace(actor=runtime.actor, artifacts=runtime.artifacts,
        bindings=runtime.scheduler_bindings, a=instance)
    _, intake, manifest, files = _seed_family(s)
    originals = {f.ref: runtime.artifacts.read(f.ref) for f in files}
    save_archive(archive, instance)
    model = archive.archived_model(instance)
    context = model.node_context(instance, 'artifact:intake')
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=context['focus_artifact_ids'])
    assert len(result['figures']) == 3
    assert any(section.get('kind') == 'curve_evidence' for section in result['sections'])
    for ref, raw in originals.items():
        assert model.artifact_reference(instance, ref.artifact_id) == ref
        with model.artifacts.open_original(ref) as stream:
            assert stream.read() == raw
    restore_archive(archive, instance)
    assert all(runtime.artifacts.read(ref) == raw for ref, raw in originals.items())


@pytest.mark.parametrize('surface', ['run', 'artifact', 'approval'])
@pytest.mark.parametrize('wide_history', [False, True])
def test_resumed_figure_publication_uses_exact_owner_and_keeps_all_images(
        figure_context, surface, wide_history):
    s, old_intake, old_manifest, native_images = figure_context
    labels = {'operation_id': 'science.evidence.extract.figure.v3', 'operation_version': '1',
              'operation_digest': 'a' * 64, 'tool_producer_run': 'resumed'}
    def save(name, payload, *, schema='opaque', media='application/json', parents=(), port='tool_evidence'):
        return s.artifacts.register(payload if isinstance(payload, bytes) else canonical_json(payload),
            ArtifactRegistration(kind='fixture', schema_id=schema, payload_schema_version=1,
                media_type=media, creator=s.actor, parent_refs=parents,
                labels={**labels, 'operation_output_port': port}), idempotency_key=name)
    payload = json.loads(s.artifacts.read(old_manifest.ref))
    wide = tuple(save(f'background-{i}', {'text': 'history ' * 3500}).ref
                 for i in range(12 if wide_history else 0))
    overlays = []
    for name, color in [('Al.png', 'black'), ('Ga.png', 'red')]:
        image = save(name, picture(color), media='image/png', port='attachments')
        overlays.append(image)
        alias = 'attachment_' + str(len(overlays))
        payload['records'].append({'alias': alias, 'artifact_ref': image.ref.model_dump(mode='json'),
            'media_type': image.media_type, 'size_bytes': image.size_bytes, 'output_port': 'attachments',
            'metadata': {'file_name': name}})
        payload['bindings'][alias] = {'artifact_ref': image.ref.model_dump(mode='json'), 'port_name': 'attachments'}
    manifest = save('new-manifest', payload, schema='scidiscovery.tool-evidence-manifest.v1',
        parents=wide + tuple(x.ref for x in [*native_images, *overlays]), port='recovery_manifest_output')
    intake = save('new-intake', {'scientific_foundation': {'summary': 'Resumed extraction'}},
        schema='scidiscovery.scientific-intake.v1',
        parents=wide + (old_intake.ref, old_manifest.ref, manifest.ref), port='scientific_intake')
    bind(s, s.a, 'artifact', 'resumed.output', intake.artifact_id)
    status = run(s, 'resumed', output=intake.ref)
    status.operation_id, status.operation_version = labels['operation_id'], labels['operation_version']
    if surface == 'approval':
        launch = approval(s, 'resumed-review', (intake.ref,))
        context = s.model.approval_context(s.a, s.approvals.review('resumed-review',
            access_token=launch.access_token), presentation=True)
    else:
        context = s.model.node_context(s.a, 'run:resumed' if surface == 'run' else 'artifact:resumed.output')
    current = next(a for a in context['artifacts'] if a['artifact_id'] == intake.artifact_id)
    assert current['family']['presentation_manifest_match_count'] == 1
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=[intake.artifact_id])
    expected = {x.artifact_id for x in [*native_images[:3], *overlays]}
    assert {f['artifact_id'] for f in result['figures']} == expected
    assert len(result['figures']) == 5
    assert all(f['source']['artifact_id'] == manifest.artifact_id for f in result['figures'])
    html = render_presentation(result, evidence_href=lambda a, p: f'/evidence/{a}?pointer={p}',
        image_href=lambda a: f'/image/{a}')
    assert html.count('<img ') == 5
    if not wide_history:
        repeated = [f for f in result['figures'] if f.get('additional_sources')]
        assert len(repeated) == 3
        assert all({'artifact_id': old_manifest.artifact_id, 'json_pointer': f['source']['json_pointer']}
                   in f['additional_sources'] for f in repeated)


def test_duplicate_publications_do_not_use_up_figure_slots(figure_context, monkeypatch):
    s, intake, _, images = figure_context
    context = s.model.node_context(s.a, 'run:extract')
    def repeated(cohort):
        result = figures(cohort)
        result['figures'] = [deepcopy(result['figures'][0]) for _ in range(40)] + result['figures'][1:]
        return result
    monkeypatch.setattr(presentation, 'entry_points', lambda **_: (
        SimpleNamespace(name='repeated-publications', load=lambda: repeated),))
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=[intake.artifact_id])
    assert [f['artifact_id'] for f in result['figures']] == [i.artifact_id for i in images[:3]]
    assert not any(g['code'] == 'presentation_item_limit' and g.get('collection') == 'figures'
                   for g in result['gaps'])


@pytest.mark.parametrize("subject_order", [(0, 1), (1, 0)])
def test_csv_previews_do_not_starve_other_approval_subject_images(system, figure_providers, subject_order):
    s=system
    reports=[]; images=[]
    for n in range(2):
        labels={"operation_id":"science.evidence.extract.figure.v3","operation_version":"1",
            "operation_digest":"a"*64,"tool_producer_run":f"producer{n}"}
        def save(name,payload,schema="opaque",media="application/json",parents=(),port="tool_evidence"):
            return s.artifacts.register(payload if isinstance(payload,bytes) else canonical_json(payload),
                ArtifactRegistration(kind="fixture",schema_id=schema,payload_schema_version=1,
                    media_type=media,creator=s.actor,parent_refs=parents,
                    labels={**labels,"operation_output_port":port}),idempotency_key=f"{n}-{name}")
        image=save("image",picture("blue" if n else "red"),media="image/png"); images.append(image)
        members=[image]
        if n==0:
            members.extend(save(f"csv{i}",b"x,y\n"+b"1,2\n"*12799+str(i).encode(),media="text/csv") for i in range(3))
        records=[]; bindings={}
        for i,member in enumerate(members,1):
            alias=f"tool_evidence_{i:03d}"; ref=member.ref.model_dump(mode="json")
            records.append({"alias":alias,"artifact_ref":ref,"media_type":member.media_type,
                "size_bytes":member.size_bytes,"tool_name":"worker_curve_figure_save",
                "metadata":{"kind":"figure_file","data_item":"source_panels/main.png" if i==1 else f"curve_tables/{i}.csv"}})
            bindings[alias]={"port_name":"tool_evidence","artifact_ref":ref}
        manifest=save("manifest",{"records":records,"bindings":bindings},schema="scidiscovery.tool-evidence-manifest.v1",
            parents=tuple(x.ref for x in members),port="recovery_manifest_output")
        report=save("report",{"scientific_foundation":{"summary":"extracted"},
            "evidence":[{"source_type":"runtime_output","source_key":"tool_evidence_001"}]},
            schema="scidiscovery.scientific-intake.v1",parents=(manifest.ref,),port="scientific_intake")
        reports.append(report)
    launch=approval(s,"multi",tuple(reports[i].ref for i in subject_order))
    context=s.model.approval_context(s.a,s.approvals.review("multi",access_token=launch.access_token),presentation=True)
    result=presentation.build_presentation(context["artifacts"],focus_artifact_ids=[r.artifact_id for r in reports])
    assert {f["artifact_id"] for f in result["figures"]}=={x.artifact_id for x in images}, (context["gaps"],result["gaps"])


@pytest.mark.parametrize("missing_predecessor",[False,True])
def test_unconfirmed_owner_does_not_fall_back_to_partial_historical_manifest(system,figure_providers,missing_predecessor):
    s=system
    common={"operation_id":"science.evidence.extract.figure.v3","operation_version":"1","operation_digest":"a"*64}
    def save(name,payload,owner,*,schema="opaque",media="application/json",parents=(),port="tool_evidence"):
        return s.artifacts.register(payload if isinstance(payload,bytes) else canonical_json(payload),
            ArtifactRegistration(kind="fixture",schema_id=schema,payload_schema_version=1,
                media_type=media,creator=s.actor,parent_refs=parents,
                labels={**common,"tool_producer_run":owner,"operation_output_port":port}),idempotency_key=name)
    def publication(owner,color):
        image=save(owner+"-image",picture(color),owner,media="image/png")
        ref=image.ref.model_dump(mode="json"); alias="tool_evidence_001"
        manifest=save(owner+"-manifest",{"records":[{"alias":alias,"artifact_ref":ref,
            "media_type":"image/png","size_bytes":image.size_bytes,"tool_name":"worker_curve_figure_save",
            "metadata":{"kind":"figure_file","data_item":"source_panels/main.png"}}],
            "bindings":{alias:{"port_name":"tool_evidence","artifact_ref":ref}}},owner,
            schema="scidiscovery.tool-evidence-manifest.v1",parents=(image.ref,),port="recovery_manifest_output")
        return image,manifest
    oldimage,oldmanifest=publication("old","red")
    image,manifest=publication("current","blue")
    fillers=[save(f"filler{i}",{"text":"x"*50000},"old") for i in range(4)]
    report=save("report",{"scientific_foundation":{"summary":"current extraction"}},"current",
        schema="scidiscovery.scientific-intake.v1",parents=(oldmanifest.ref,oldimage.ref,*[x.ref for x in fillers],manifest.ref),port="scientific_intake")
    bind(s,s.a,"artifact","current",report.artifact_id)
    run(s,"current",output=report.ref)
    if not missing_predecessor: run(s,"old",output=None)
    context=s.model.node_context(s.a,"artifact:current")
    result=presentation.build_presentation(context["artifacts"],focus_artifact_ids=[report.artifact_id])
    control=next(x["family"] for x in context["artifacts"] if x["artifact_id"]==report.artifact_id)
    shown={f["artifact_id"] for f in result["figures"]}
    if missing_predecessor:
        assert control["presentation_manifest_scan_complete"] is False
        assert any(g["code"] == "figure_intake_manifest_missing_or_ambiguous" for g in result["gaps"])
        assert oldimage.artifact_id not in shown,(control,context["gaps"],result["figures"])
    else:
        assert image.artifact_id in shown


@pytest.mark.parametrize('control', ['scan_failed', 'ambiguous', 'missing_parent_manifest'])
def test_intake_cannot_reinfer_owner_from_a_partial_cohort(figure_context, control):
    s, intake, manifest, images = figure_context
    context = deepcopy(s.model.node_context(s.a, 'artifact:intake'))
    view = next(a for a in context['artifacts'] if a['artifact_id'] == intake.artifact_id)
    view['family'].pop('selected_run_id', None)
    if control == 'scan_failed':
        view['family']['presentation_manifest_scan_complete'] = False
    elif control == 'ambiguous':
        view['family']['presentation_manifest_match_count'] = 2
    else:
        # A background Intake may lack the focused report's control projection.
        view['family'].pop('presentation_manifest_scan_complete', None)
        view['family'].pop('presentation_manifest_match_count', None)
        missing = deepcopy(view['provenance'][0])
        missing['ref']['artifact_id'] = 'art_missing'
        view['provenance'].append(missing)
        view['parent_count'] += 1
    result = presentation.build_presentation(context['artifacts'], focus_artifact_ids=[intake.artifact_id])
    assert result['figures'] == []
    assert any(g['code'].startswith('figure_intake_manifest_') for g in result['gaps'])
