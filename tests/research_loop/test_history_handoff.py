"""Exact implementation reuse through real thin seals; no model calls."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts, controller, native


@pytest.fixture
def scene(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Test a revised mechanism against a retained implementation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=1000,
        max_seconds=60, max_steps=6, model='fake', reasoning_effort='high',
        search_policy=None,
        max_output_item_bytes=64, max_output_total_bytes=96)
    return root


def selection_explanation(criteria='Compare the declared fixture observations within their stated limitations.'):
    return dict(criteria=criteria,criteria_change=dict(status='unchanged',prior_criteria=None,
        changes=None,evidence=None,accepted_tradeoffs=None))


def finish(root, step, role, parents, text='A bounded scientific finding', action='wait', files=None, summary='A contribution', limitations=None, recommendation=None, search_choice=None, progress=None, selection=None, mechanism_findings=None, candidate_nominations=None, preserve_native_intent=False):
    path = root / 'jobs' / f'{step:03d}.json'
    previous = contracts.read(path) if path.exists() else {}
    job = contracts.build_job(root, step, role, parents, previous.get('instruction', ''),
                              previous.get('evidence_return'), previous.get('correction_request'), previous.get('author_correction'))
    work = contracts.workspace(root, step)
    record = contracts.host_record(work)
    payload = dict(summary=summary, scientific_result=text, limitations=limitations if limitations is not None else ['Fixture only'])
    if job.get('handoff_protocol', 1) >= 7:
        payload['implementation_selection'] = selection
    if (role == 'A' and job.get('handoff_protocol', 1) >= 4) or recommendation is not None:
        payload['candidate_recommendation'] = recommendation
        payload['selection_explanation'] = selection_explanation() if recommendation is not None else None
    if job.get('handoff_protocol', 1) >= 5 and role in {'H', 'A'}:
        payload['search_plan' if role == 'H' else 'search_review'] = search_choice
    if job.get('handoff_protocol', 1) >= 6 and role == 'A':
        payload['progress_assessment'] = progress
    if role=='W':
        if candidate_nominations is None and job.get('candidate_nomination_mechanisms'):
            candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
                reason='Fixture declares no scientific candidate suitability; the retained pool remains available to A.')
                for row in job['candidate_nomination_mechanisms'] if row['status']!='waived'])
        payload.update(stage_intent='review',mechanism_findings=mechanism_findings,candidate_nominations=candidate_nominations)
    if role=='A':payload.update(decision_request=None,portfolio_review=None)
    if role in {'H', 'A'}:
        payload.update(next_action=action, route_reason='Exact scientific question')
    for name, contents in (files or {}).items():
        path = work / 'artifacts' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    task_ref=contracts.record_ref(root / 'jobs' / f'{step:03d}.json')
    if preserve_native_intent:
        assert contracts.read(contracts.regular(record/'native-intent.json'))['task_ref']==task_ref
    else:
        contracts.write(record / 'native-intent.json', dict(task_ref=task_ref))
    contracts.write(record / 'science-output.json', payload)
    contracts.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    contracts.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(root), raw_ref=contracts.file_ref(record / 'science-output.json')))
    contracts.seal_result(root, step)
    return job, work, record


def source(scene, files=None):
    finish(scene, 1, 'H', [], action='experiment')
    return finish(scene, 2, 'W', [1], files=files or {'candidate.py': 'def predict(): return 42\n', 'unused.txt': 'Do not forward me'})


REUSED = 'inputs/parents/002/artifacts/candidate.py'
MARKED = f'[retained implementation]({REUSED}#reuse-implementation)'


def test_real_scientific_route_reuses_only_selected_file_without_ancestry(scene, monkeypatch):
    from test_direct_input_bindings import stopped_seals
    from scidiscovery.research_loop import originals
    stopped_seals(monkeypatch)
    def execute(root, step):
        role = ('H', 'W', 'A', 'H', 'W', 'A')[step - 1]
        job = contracts.read(root / 'jobs' / f'{step:03d}.json')
        action = 'experiment' if role == 'H' else 'revise_h' if step == 3 else 'finish'
        text = MARKED if step == 4 else f'Ordinary [citation]({REUSED})' if step == 3 else 'Bounded finding'
        files = {'candidate.py': 'def predict(): return 42\n', 'unused.txt': 'Private to old direct handoff'} if step == 2 else None
        if step == 5:
            work = contracts.workspace(root, step)
            declared=[row for row in job['direct_materials'] if row['path']==REUSED]
            assert len(declared)==1
            assert originals.material_path(root,declared[0]).read_text() == 'def predict(): return 42\n'
            assert not (work / REUSED).exists()
            assert not (work / Path(REUSED).parent / 'unused.txt').exists()
            assert [ref['path'] for ref in job['direct_refs']] == ['inputs/parents/004/conclusion.json']
            view = contracts.read(work / 'science-task.json')
            assert view['reused_implementations'] == [dict(label='W002-I001', path=REUSED)]
            assert 'sha256' not in json.dumps(view) and 'origin_result' not in json.dumps(view)
        # build_job is idempotent for an identical request; use the actual route.
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        finish(root, step, role, parents, text, action, files, selection=['W002-I001'] if step == 4 else [] if step == 5 else None)
    errors = []
    def observed_execute(root, step):
        try:
            return execute(root, step)
        except Exception as error:
            errors.append(repr(error))
            raise
    monkeypatch.setattr(native, 'execute_native', observed_execute)
    result = controller.run(scene)
    assert result['status'] == 'finish' and result['completed_steps'] == 6, (result, errors)
    final = contracts.workspace(scene, 6)
    assert not (final / REUSED).exists()  # Retained H4 must not re-send to A6.
    assert contracts.read(contracts.host_record(final) / 'result-identity.json').get('selected_inputs', []) == []
    assert controller.run(scene)['status'] == 'finish'


def test_structured_selection_inherits_and_deduplicates_without_ancestry(scene,monkeypatch):
    from test_direct_input_bindings import stopped_seals
    stopped_seals(monkeypatch)
    source(scene)
    finish(scene, 3, 'A', [2], text=MARKED + ' ' + MARKED, selection=['W002-I001', 'W002-I001'])
    job, work, _ = finish(scene, 4, 'H', [3], text=MARKED)
    from scidiscovery.research_loop import originals
    assert len(job['selected_inputs']) == 1
    selected=job['selected_inputs'][0]['material']
    assert originals.material_path(scene,selected)==contracts.workspace(scene,2)/'artifacts/candidate.py'
    assert not (work/REUSED).exists()
    successor = contracts.build_job(scene, 5, 'W', [4])
    assert len(successor['selected_inputs']) == 1
    assert len([row for row in successor['direct_materials'] if row['path'] == REUSED]) == 1
    assert not any(row['path'].endswith('/unused.txt') for row in successor['direct_materials'])


@pytest.mark.parametrize('target', [
    'work/candidate.py', 'inputs/parents/002/artifacts/unused-missing.py',
    'inputs/parents/002/artifacts/../candidate.py',
    'inputs/parents/002/artifacts/%2e%2e/candidate.py',
    '/etc/passwd', 'https://example.org/code.py', REUSED + '?latest=1',
])
def test_old_marker_paths_do_not_select_or_grant_input_authority(scene, target):
    source(scene)
    _, work, record = finish(scene, 3, 'A', [2], text=f'[implementation]({target}#reuse-implementation)')
    seal = contracts.read(record / 'result-identity.json')
    assert not seal.get('selected_inputs') and not seal['entry_points']
    assert contracts.validate(work)['scientific_result'] == f'[implementation]({target}#reuse-implementation)'
    successor = contracts.build_job(scene, 4, 'H', [3])
    assert not successor['direct_materials'] and not successor.get('selected_inputs')


def test_ordinary_citation_does_not_forward_old_inputs(scene):
    source(scene)
    finish(scene, 3, 'A', [2], text=f'[citation]({REUSED})')
    job = contracts.build_job(scene, 4, 'H', [3])
    assert job['direct_materials'] == [] and not job.get('selected_inputs')


@pytest.mark.parametrize('changed', ['original', 'read_copy', 'receipt'])
def test_changed_bound_reuse_is_rejected(scene, changed):
    source(scene)
    _, work, record = finish(scene, 3, 'A', [2], text=MARKED, selection=['W002-I001'])
    if changed == 'receipt':
        seal = contracts.read(record / 'result-identity.json')
        seal['selected_inputs'][0]['label'] = 'Changed hidden selection'
        contracts.write(record / 'result-identity.json', seal)
    else:
        path = contracts.workspace(scene, 2) / 'artifacts/candidate.py' if changed == 'original' else work / REUSED
        path.chmod(0o600)
        path.write_text('different implementation')
    with pytest.raises(ValueError, match='changed'):
        contracts.build_job(scene, 4, 'H', [3])


def test_structured_selection_counts_all_unique_files_beyond_one_page(scene):
    source(scene, {f'file{i}.py': 'x' for i in range(17)})
    finish(scene, 3, 'A', [2], selection=[f'W002-I{index:03d}' for index in range(1, 18)])
    job = contracts.build_job(scene, 4, 'H', [3])
    assert len(job['selected_inputs']) == len(job['direct_materials']) == 17
    assert sum(row['size_bytes'] for row in job['direct_materials']) == 17


def test_selected_copy_cannot_follow_destination_directory_link(scene, tmp_path):
    source(scene)
    finish(scene, 3, 'A', [2], text=MARKED, selection=['W002-I001'])
    work = contracts.workspace(scene, 4)
    work.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (work / 'inputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='directories must not be links'):
        contracts.build_job(scene, 4, 'H', [3])
    assert not list(outside.rglob('candidate.py'))


def test_final_input_bound_includes_new_outputs_and_selected_old_files(scene):
    source(scene, {'candidate.py': 'x' * 48})
    finish(scene, 3, 'A', [2], text=MARKED, selection=['W002-I001'])
    finish(scene, 4, 'H', [3], text=MARKED, files={'revision.txt': 'y' * 64})
    with pytest.raises(ValueError, match='Combined input materials'):
        contracts.build_job(scene, 5, 'W', [4])
    assert not (scene / 'jobs/005.json').exists()
    assert not (contracts.workspace(scene, 5) / REUSED).exists()


def test_final_input_bound_deduplicates_direct_and_reused_original(scene):
    source(scene, {'candidate.py': 'x' * 64})
    finish(scene, 3, 'A', [2], text=MARKED, selection=['W002-I001'])
    job = contracts.build_job(scene, 4, 'H', [2, 3])
    assert len(job['direct_materials']) == 1
    assert job['direct_materials'][0]['size_bytes'] == 64


def test_current_prepared_packets_are_idempotent_and_old_write_schema_is_rejected(scene):
    from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
    job = contracts.build_job(scene, 1, 'H', [])
    finish(scene, 1, 'H', [], action='experiment')
    original = {path: path.read_bytes() for path in (
        scene / 'jobs/001.json', contracts.workspace(scene, 1) / 'science-task.json',
        contracts.host_record(contracts.workspace(scene, 1)) / 'result-identity.json')}
    assert job['handoff_protocol'] == CURRENT
    with pytest.raises(UnsupportedProtocol):
        contracts.result_schema('H', 1)
    assert contracts.build_job(scene, 1, 'H', []) == job
    assert all(path.read_bytes() == contents for path, contents in original.items())
    contracts.validate(contracts.workspace(scene, 1))


@pytest.mark.parametrize('origin', ['foreign', 'future'])
def test_selected_original_requires_same_instance_and_preceding_node(scene, origin):
    source(scene)
    job = contracts.build_job(scene, 3, 'A', [2])
    row = next(row for row in job['direct_materials'] if row['path'] == REUSED)
    job['selected_inputs'] = [dict(label='W002-I001', material=row)]
    row['source'] = (str(scene.parent / 'foreign/project/nodes/002/artifacts/candidate.py')
                     if origin == 'foreign' else str(contracts.workspace(scene, 4) / 'artifacts/candidate.py'))
    with pytest.raises(ValueError, match='original instance node|exact preceding node'):
        contracts.selected_inputs(contracts.workspace(scene, 3), {'scientific_result': MARKED, 'implementation_selection': None}, job)
