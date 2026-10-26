"""Local history reuses saved original identities without retaining science again."""
from pathlib import Path
import subprocess

import pytest

from test_direct_public_collect import (execution_registry, fixed_code, fragment,
    observed_local, replace_equal_bytes, stopped_attempt, terminal_code)
from test_history_handoff import scene
from scidiscovery.research_loop import (atomic_files, contracts as c, experiments as e,
    history, native, shared_originals)


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_final_host_material_is_sealed_in_place_without_a_conclusion_copy(
        scene, monkeypatch, role):
    root = scene
    job = c.build_job(root, 1, role, [])
    work = c.workspace(root, 1)
    record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    payload = dict(summary='One bounded contribution', scientific_result='A fixture observation with unresolved target limits.',
        limitations=['Source contract fixture only'], implementation_selection=None)
    if role in {'H', 'A'}:
        payload.update(next_action='wait', route_reason='Retain the stated uncertainty')
    if role == 'W':
        payload.update(stage_intent='review', mechanism_findings=None, candidate_nominations=None)
    schema = c.result_schema(role, job['handoff_protocol'], **c.response_options(job))
    for field in schema['properties']:
        payload.setdefault(field, None)
    raw = record / 'science-output.json'
    c.write(raw, payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=True, cleanup='observed_group_cleaned', usage=native.usage(root), raw_ref=c.file_ref(raw)))
    artifact = work / 'artifacts/result.txt'
    artifact.write_bytes(b'The exact host result.')
    originals = {path: e.proof(path) for path in (raw, artifact)}
    modes = {path: path.stat().st_mode for path in originals}
    copies = []
    copy_original = atomic_files.copy_original
    def copied(source, target, **options):
        if Path(source) in originals:
            copies.append((Path(source), Path(target)))
        return copy_original(source, target, **options)
    def forbidden(*args, **options):
        pytest.fail('Host seal source fixture reached a process launch')
    monkeypatch.setattr(atomic_files, 'copy_original', copied)
    for name in ('Popen', 'run', 'call'):
        monkeypatch.setattr(subprocess, name, forbidden)

    # An outcome or chmod alone never supplies a stopped native identity.
    with pytest.raises(ValueError, match='writer|process|cleanup|unconfirmed'):
        c.seal_result(root, 1)
    assert not (record / 'result-identity.json').exists()
    assert {path: path.stat().st_mode for path in originals} == modes
    live, receipts = stopped_attempt(root, job, record, monkeypatch)
    live.add(receipts['native-process.json']['pid'])
    with pytest.raises(ValueError, match='writer|owns|stopp'):
        c.seal_result(root, 1)
    assert {path: path.stat().st_mode for path in originals} == modes
    live.clear()
    c.seal_result(root, 1)
    seal = c.read(record / 'result-identity.json')
    material = next(item for item in seal['materials'] if item['source'] == str(artifact))
    assert material['size_bytes'] == originals[artifact]['size_bytes']
    assert material['file_stat']==originals[artifact]['file_stat']
    assert seal['raw_ref'] == originals[raw]
    assert set(c.read(record / 'native-outcome.json')['raw_ref']) == {'path', 'size_bytes'}
    assert set(c.file_ref(raw)) == {'path', 'size_bytes'}
    assert all(path.stat().st_mode & 0o777 == 0o400 for path in originals)
    saved_seal = (record / 'result-identity.json').read_bytes()
    c.seal_result(root, 1)
    assert {path: e.proof(path) for path in originals} == originals and copies == []
    assert (record / 'result-identity.json').read_bytes() == saved_seal
    assert not (work / 'conclusion.json').exists()
    if role == 'H':
        from scidiscovery.research_loop import qualification
        successor = c.build_job(root, 2, 'W', [1])
        assert qualification.status(root, successor) == successor['source_restrictions']
        provenance = c.read(root / seal['source_provenance']['path'])
        assert provenance['raw_ref'] == c.file_ref(raw)
        assert set(provenance['raw_ref']) == {'path', 'size_bytes'}
        assert c.read(record / 'result-identity.json')['raw_ref'] == originals[raw]
        assert (record / 'result-identity.json').read_bytes() == saved_seal
    raw.chmod(0o600)
    raw.write_bytes(raw.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='changed|identity|original'):
        c.validate(work)
    if role == 'H':
        # This is a control-only source restriction query. It does not consume
        # the unselected raw scientific body; the actual consumer above rejects.
        assert qualification.status(root, successor) == successor['source_restrictions']
    assert (record / 'result-identity.json').read_bytes() == saved_seal


@pytest.mark.parametrize('alias_relative', ['work/result-alias.txt', 'artifacts/result-alias.txt'],
    ids=['scratch-alias', 'artifact-alias'])
def test_final_host_material_rejects_mutable_hardlinks_before_chmod(
        scene, monkeypatch, alias_relative):
    root = scene
    job = c.build_job(root, 1, 'W', [])
    work = c.workspace(root, 1)
    record = c.host_record(work)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    payload = dict(summary='One bounded contribution', scientific_result='A fixture observation with unresolved target limits.',
        limitations=['Source contract fixture only'], implementation_selection=None,
        stage_intent='review', mechanism_findings=None, candidate_nominations=None)
    schema = c.result_schema('W', job['handoff_protocol'], **c.response_options(job))
    for field in schema['properties']:
        payload.setdefault(field, None)
    raw = record / 'science-output.json'
    c.write(raw, payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=True, cleanup='observed_group_cleaned', usage=native.usage(root), raw_ref=c.file_ref(raw)))
    artifact = work / 'artifacts/result.txt'
    artifact.write_bytes(b'The exact host result.')
    artifact.chmod(0o600)
    alias = work / alias_relative
    alias.hardlink_to(artifact)
    assert alias.samefile(artifact) and artifact.stat().st_nlink == 2
    modes = {path: path.stat().st_mode for path in (artifact, alias)}
    def forbidden(*args, **options):
        pytest.fail('Host seal source fixture reached a process launch')
    for name in ('Popen', 'run', 'call'):
        monkeypatch.setattr(subprocess, name, forbidden)
    stopped_attempt(root, job, record, monkeypatch)

    with pytest.raises(ValueError, match='hard-link aliases'):
        c.seal_result(root, 1)
    assert {path: path.stat().st_mode for path in modes} == modes
    assert not (record / 'result-identity.json').exists()


def test_repeated_managed_proof_keeps_saved_original_identity(
        fragment, observed_local):
    root, job, _ = fragment
    row = observed_local()
    output = row['output']
    saved = e.proof(output)
    observed = row['observation']
    before = (row['directory'] / 'execution-observation.json').read_bytes()
    proof = history._managed_proof(root, output, saved)
    assert root / proof['snapshot'] == output
    assert proof['file_stat'] == saved['file_stat']
    calls = history.managed_calls(root, job)
    assert len(calls) == 1 and calls[0]['status'] == 'known_success'
    selected = history.scientific_outputs(root, job, calls[0])
    assert any(root / item['snapshot'] == output and item['file_stat'] == saved['file_stat']
        for item in selected)
    Path(row['accepted']['source_script']).unlink()
    assert not Path(row['accepted']['script']['path']).exists()
    for _ in range(2):
        assert history._managed_proof(root, output, saved) == proof
        assert history.managed_calls(root, job) == calls
        assert history.scientific_outputs(root, job, calls[0]) == selected
        assert e.reconcile_saved_observation(row['config'], row['adapter'], row['accepted']['call_id']) == observed
    assert (row['directory'] / 'execution-observation.json').read_bytes() == before
    assert e.proof(output) == saved

    # The current public file_ref remains path/size; private proofs do not
    # expand that public contract.
    public_source = c.workspace(root, job['step']) / 'work/public-source'
    public_source.write_bytes(b'current retained bytes')
    frozen = shared_originals.retain(root, public_source, source_kind='mutable',
        size_bytes=public_source.stat().st_size)
    public = c.file_ref(frozen)
    assert set(public) == {'path', 'size_bytes'}
    retained = history._managed_proof(root, frozen, public)
    history._validate_proof(root, retained)
    assert (root / retained['snapshot']).read_bytes() == public_source.read_bytes()
    assert c.file_ref(frozen) == public

    replace_equal_bytes(output)
    for read in (lambda: history._managed_proof(root, output, saved),
            lambda: history.scientific_outputs(root, job, calls[0])):
        with pytest.raises(ValueError, match='changed|original'):
            read()
    assert (row['directory'] / 'execution-observation.json').read_bytes() == before
    assert proof['file_stat'] == saved['file_stat']


def test_successor_reuses_frozen_history_bodies(tmp_path,monkeypatch):
    from test_direct_input_bindings import direct_instance, stopped_seals
    from scidiscovery.research_loop import originals, shared_input_views
    from test_history_handoff import finish
    root,_=direct_instance(tmp_path);stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment',text='An exact bounded conclusion. '*500)
    second,_,_=finish(root,2,'W',[1])
    prefix='inputs/history/nodes/001/'
    bodies=[row for row in second['history']['files'] if row['path'].startswith(prefix)
        and Path(row['path']).stem.isdecimal()]
    assert len(bodies)>=2
    saved={row['path']:Path(row['source']).read_bytes() for row in bodies}
    written=[];publish=atomic_files.publish_bytes
    def publishing(path,data,**options):
        if Path(path).is_relative_to(c.workspace(root,3)/prefix):written.append(Path(path))
        return publish(path,data,**options)
    monkeypatch.setattr(atomic_files,'publish_bytes',publishing)
    third=c.build_job(root,3,'A',[2]);work=c.workspace(root,3)
    assert c.build_job(root,3,'A',[2])==third
    reused=[row for row in third['history']['files'] if row['path'] in saved]
    assert reused==bodies and written==[]
    argv=shared_input_views.mount_arguments(root,third,work,include_skills=False)
    for row in reused:
        assert not (work/row['path']).exists()
        assert originals.resolve_root_ref(root,row).read_bytes()==saved[row['path']]
        assert any(argv[index:index+3]==['--ro-bind',row['source'],str(work/row['path'])]
            for index in range(len(argv)-2))
