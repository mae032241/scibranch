"""Current scientific choices, without role-authored control paths."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, correction, handoff_state, originals, shared_input_views
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish


def prepare_scene(tmp_path, correction_policy=None, *, node_budget_mode='soft'):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Compare mechanisms and retain applicable controls.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=100_000, max_seconds=3600,
                       max_steps=32, model='fake', reasoning_effort='high',
                       correction_policy=correction_policy,node_budget_mode=node_budget_mode)
    return root


@pytest.fixture
def scene(tmp_path):
    return prepare_scene(tmp_path)


@pytest.fixture
def correcting_scene(tmp_path):
    return prepare_scene(tmp_path, correction.DEFAULT_POLICY)


def plan(action='screen', existing_ref=None):
    return dict(scope='Bounded global scientific question', commitments=[dict(label='Mechanism',
        minimal_test='Discriminating comparison', existing_ref=existing_ref)], focus=dict(index=1, action=action))


FIRST = 'inputs/parents/002/artifacts/a.py'
SECOND = 'inputs/parents/002/artifacts/b.py'
PENDING_FINDINGS = [dict(mechanism='h001.m1',basis='blocked',
    finding='Implementation files alone do not establish a discriminating observation; evaluation remains pending.')]


def original_worker(root):
    finish(root, 1, 'H', [], action='experiment', search_choice=plan())
    return finish(root, 2, 'W', [1], files={'a.py': 'FIRST_IMPLEMENTATION', 'b.py': 'SECOND_IMPLEMENTATION'},
        mechanism_findings=PENDING_FINDINGS)


def selected_paths(job):
    return [row['material']['path'] for row in job.get('selected_inputs', [])]


def parents(job):
    return [int(ref['path'].split('/')[2]) for ref in job['direct_refs']]


def consultation(root, requester_step, step):
    requester = c.read_job(root / 'jobs' / f'{requester_step:03d}.json')
    route = controller._route(root, requester_step, 'W', handoff_state.read_sealed(root, requester_step),
        requester, 1, None)
    return c.build_job(root, step, route.role, list(route.parents), instruction=route.instruction,
        evidence_return=route.evidence_return)


def consultation_return(root, step, job, next_step):
    route = controller._route(root, step, job['role'], handoff_state.read_sealed(root, step), job,
        1, job['evidence_return']['step'])
    return c.build_job(root, next_step, route.role, list(route.parents), instruction=route.instruction,
        evidence_return=route.evidence_return)


@pytest.mark.parametrize('selection,expected', [
    (None, [FIRST]), ([], []), (['W002-I002'], [SECOND]),
])
def test_w_selects_retained_materials_after_h_consultation(scene, selection, expected):
    original_worker(scene)
    finish(scene, 3, 'W', [1, 2], stage_intent='revise_h', selection=['W002-I001'])
    hjob = consultation(scene, 3, 4)
    finish(scene, 4, 'H', parents(hjob), action='wait', selection=[])
    assert selected_paths(hjob) == [FIRST]
    returned = consultation_return(scene, 4, hjob, 5)
    assert selected_paths(returned) == [FIRST]
    finish(scene, 5, 'W', parents(returned), stage_intent='handoff', selection=selection)
    successor = c.build_job(scene, 6, 'W', [5])
    assert selected_paths(successor) == expected
    assert [row['path'] for row in successor['direct_refs']] == ['inputs/parents/005/conclusion.json']
    assert c.build_job(scene, 6, 'W', [5]) == successor


def test_w_e_w_return_does_not_revive_a_cleared_selection(scene):
    original_worker(scene)
    finish(scene, 3, 'W', [1, 2], stage_intent='handoff', selection=['W002-I001'])
    finish(scene, 4, 'W', [3], stage_intent='extract', selection=[])
    e_job = consultation(scene, 4, 5)
    assert selected_paths(e_job) == []
    finish(scene, 5, 'E', parents(e_job), selection=['W002-I001'])
    w_job = consultation_return(scene, 5, e_job, 6)
    assert selected_paths(w_job) == []
    assert [row['path'] for row in w_job['direct_refs']] == [
        'inputs/parents/001/conclusion.json', 'inputs/parents/004/conclusion.json',
        'inputs/parents/005/conclusion.json']


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_bad_ordinary_links_do_not_change_selection_or_prevent_seal(scene, role):
    original_worker(scene)
    text = ' '.join(f'[unresolved {i}](artifacts/missing-{i}.py)' for i in range(100))
    text += ' [escape](artifacts/../hidden.py) [hidden](host-history/objects/private)'
    text += ' [old marker](history/nodes/999/artifacts/code.py#reuse-implementation)'
    text += ' [external](https://example.invalid/code.py)'
    choice = plan(existing_ref='h001.m1') if role == 'H' else None
    if role == 'E':
        finish(scene, 3, 'W', [1, 2], stage_intent='extract')
        job = consultation(scene, 3, 4)
        step, parent_steps = 4, parents(job)
    else:
        step, parent_steps = 3, [1, 2]
    _, work, record = finish(scene, step, role, parent_steps, text=text, search_choice=choice,
        mechanism_findings=PENDING_FINDINGS if role == 'W' else None)
    assert c.validate(work)['scientific_result'] == text
    seal = c.read(record / 'result-identity.json')
    assert seal.get('selected_inputs', []) == []
    assert seal['entry_points'] == []
    unresolved = seal['unresolved_links']
    assert len(unresolved['items']) == c.MAX_UNRESOLVED_LINKS
    assert unresolved['omitted_count'] > 0
    assert all(set(item) == {'label', 'target', 'reason'} for item in unresolved['items'])
    assert all(len(item['label'].encode()) <= 160 and len(item['target'].encode()) <= 512
               for item in unresolved['items'])
    assert all(not item['target'].startswith('https://') for item in unresolved['items'])
    assert (record / 'result-identity.json').stat().st_size < c.MAX_RESULT_BYTES


@pytest.mark.parametrize('selection', [['W999-I001'], ['W002-I999'], ['../../private']])
def test_invalid_explicit_implementation_ids_are_not_ordinary_link_diagnostics(scene, selection):
    original_worker(scene)
    original = c._execution_raw(scene, 2).read_bytes()
    with pytest.raises(OutputRepairRequired, match='implementation_selection'):
        finish(scene, 3, 'W', [1, 2], selection=selection)
    record = c.host_record(c.workspace(scene, 3))
    assert c.read(record / 'science-output.json')['implementation_selection'] == selection
    assert not (record / 'result-identity.json').exists()
    assert not (scene / 'jobs/004.json').exists()
    assert c._execution_raw(scene, 2).read_bytes() == original


def test_mechanical_hash_mismatch_still_blocks_scientific_seal(scene):
    original_worker(scene)
    c.build_job(scene, 3, 'W', [1, 2])
    path = c.workspace(scene, 2) / 'artifacts/a.py'
    path.chmod(0o600)
    path.write_text('CHANGED ORIGINAL')
    with pytest.raises(ValueError):
        finish(scene, 3, 'W', [1, 2], selection=['W002-I001'])
    assert not (c.host_record(c.workspace(scene, 3)) / 'result-identity.json').exists()


def test_progress_credit_is_current_worker_original_not_self_authored_a(scene):
    original_worker(scene)
    job, _, _ = finish(scene, 3, 'A', [1, 2], progress=dict(verdict='progress'))
    expected = c.file_ref(c.host_record(c.workspace(scene, 2)) / 'science-output.json')['path']
    assert correction._proofs(scene, job, dict(verdict='progress')) == {expected}
    next_job, _, _ = finish(scene, 4, 'A', [1, 2, 3], progress=dict(verdict='progress'))
    assert correction._proofs(scene, next_job, dict(verdict='progress')) == set()


def test_progress_binds_exact_h_analysis_original(scene):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan())
    job, _, _ = finish(scene, 2, 'A', [1], progress=dict(verdict='progress'))
    assert correction._proofs(scene, job, dict(verdict='progress')) == {
        c.file_ref(c.host_record(c.workspace(scene, 1)) / 'science-output.json')['path']}


def test_equal_worker_raws_keep_independent_original_progress_credit(correcting_scene):
    scene = correcting_scene
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan())
    finish(scene, 2, 'W', [1], text='Same original science bytes',mechanism_findings=PENDING_FINDINGS)
    first_a, _, _ = finish(scene, 3, 'A', [1, 2], progress=dict(verdict='progress'))
    finish(scene, 4, 'W', [1, 2, 3], text='Same original science bytes',mechanism_findings=PENDING_FINDINGS)
    second_a, _, _ = finish(scene, 5, 'A', [1, 4], progress=dict(verdict='progress'))
    assert (c.host_record(c.workspace(scene, 2)) / 'science-output.json').read_bytes() == (c.host_record(c.workspace(scene, 4)) / 'science-output.json').read_bytes()
    first_proofs = correction._proofs(scene, first_a, dict(verdict='progress'))
    second_proofs = correction._proofs(scene, second_a, dict(verdict='progress'))
    assert first_proofs == {c.file_ref(c.host_record(c.workspace(scene, 2)) / 'science-output.json')['path']}
    assert second_proofs == {c.file_ref(c.host_record(c.workspace(scene, 4)) / 'science-output.json')['path']}
    assert first_proofs.isdisjoint(second_proofs)
    assert correction.project(scene)['counters'] == dict(no_progress=0, unknown=0)


def test_seventeenth_implementation_is_visible_selected_and_bound_to_original(scene):
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan())
    finish(scene, 2, 'W', [1], files={f'a{i:02d}.py': f'ORIGINAL_IMPLEMENTATION_{i}' for i in range(17)},
        mechanism_findings=PENDING_FINDINGS)
    job = c.build_job(scene, 3, 'W', [1, 2])
    work = c.workspace(scene, 3)
    argv = shared_input_views.mount_arguments(scene, job, work, include_skills=False)
    public = next(Path(argv[index + 1]) for index in range(len(argv) - 2)
        if argv[index] == '--ro-bind' and argv[index + 2] == str(work / 'inputs/history'))
    def navigation(path):
        return public / Path(path).relative_to('inputs/history')
    index = c.read(navigation(job['history']['view']['indexes']['nodes']))
    assert index['step'] == 2 and index['role'] == 'W'
    objects = index['implementations']
    assert len(objects) == 17 and objects[-1]['id'] == 'W002-I017'
    assert objects[-1]['label'] == 'a16.py'
    finish(scene, 3, 'W', [1, 2], stage_intent='handoff', selection=['W002-I017'])
    successor = c.build_job(scene, 4, 'W', [3])
    selected = 'inputs/parents/002/artifacts/a16.py'
    assert selected_paths(successor) == [selected]
    material = next(row for row in successor['direct_materials'] if row['path'] == selected)
    mounted = originals.material_path(scene, material)
    assert mounted.read_text() == 'ORIGINAL_IMPLEMENTATION_16'
    assert not mounted.stat().st_mode & 0o222
    assert 'inputs/parents/002/artifacts/a00.py' not in {row['path'] for row in successor['direct_materials']}
