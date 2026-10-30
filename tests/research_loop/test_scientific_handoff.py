"""Current scientific choices, without role-authored control paths."""
import json
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, correction, execution, experiments, history, search, protocol, semantic
from execution_fixtures import execution_registry
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


@pytest.fixture
def registered_scene(tmp_path,execution_registry):
    return prepare_scene(tmp_path,node_budget_mode='soft')


def plan(action='screen', existing_ref=None):
    return dict(scope='Bounded global scientific question', commitments=[dict(label='Mechanism',
        minimal_test='Discriminating comparison', existing_ref=existing_ref)], focus=dict(index=1, action=action))


def focus(action='screen'):
    return dict(mechanism_ref='h001.m1', action=action)


def review(status='screened', action='improve'):
    return dict(dispositions=[dict(mechanism_ref='h001.m1', status=status,
        reason='Independent scientific assessment with declared limits')], next_focus=focus(action))


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_current_protocol_has_no_mechanical_reference_fields(role):
    schema = c.result_schema(role, protocol.CURRENT)
    assert 'implementation_selection' in schema['properties']
    assert 'implementation_selection' in schema['required']
    def strict(value):
        if isinstance(value, dict):
            if 'properties' in value:
                assert set(value['properties']) == set(value['required'])
                assert value['additionalProperties'] is False
            for item in value.values():
                strict(item)
        elif isinstance(value, list):
            for item in value:
                strict(item)
    strict(schema)
    if role == 'A':
        disposition = schema['properties']['search_review']['properties']['dispositions']['items']
        assert set(disposition['properties']) == {'mechanism_ref', 'status', 'reason'}
        assert set(schema['properties']['progress_assessment']['properties']) == {'verdict'}
    if role in {'H', 'A'}:
        field, key = ('search_plan', 'focus') if role == 'H' else ('search_review', 'next_focus')
        assert 'repair_source' not in schema['properties'][field]['properties'][key]['properties']


FIRST = 'inputs/parents/002/artifacts/a.py'
SECOND = 'inputs/parents/002/artifacts/b.py'
PENDING_FINDINGS = [dict(mechanism='h001.m1',basis='blocked',
    finding='Implementation files alone do not establish a discriminating observation; evaluation remains pending.')]


def original_worker(root):
    finish(root, 1, 'H', [], action='experiment', search_choice=plan())
    return finish(root, 2, 'W', [1], files={'a.py': 'FIRST_IMPLEMENTATION', 'b.py': 'SECOND_IMPLEMENTATION'},
        mechanism_findings=PENDING_FINDINGS)


def registered_worker(root,step,parents,*,text='Exact managed scientific contribution'):
    job=c.build_job(root,step,'W',parents)
    record=execution.record(root,step)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    c.write(record/'native-intent.json',dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),**anchor))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    original_intent=(record/'native-intent.json').read_bytes()
    output=root/f'local-config-{step}.json'
    config=experiments.configure(root,step,'local_execution:command',dict(run_argv=[sys.executable]),output)
    script=c.workspace(root,step)/'work/managed-input.py';script.write_text('print("bounded fixture observation")\n')
    result=experiments.dispatch(config,script,mechanism='h001.m1')
    assert result['status']=='known_success'
    binding=result['mechanism_binding']
    assert binding['mechanism_ref']=='h001.m1' and binding['source_h']==1
    assert binding['source_h_ref']==c.record_ref(execution.origin(root,1)/'result-identity.json')
    sealed=finish(root,step,'W',parents,text=text,mechanism_findings=[dict(mechanism='h001.m1',
        basis='observed',finding='The managed fixture produced an observation; target-condition validity is untested.')],
        preserve_native_intent=True)
    assert (record/'native-intent.json').read_bytes()==original_intent
    observation=c.read(root/c.read(sealed[2]/'result-identity.json')['observations']['path'])
    assert len(observation['managed_calls'])==1
    assert observation['managed_calls'][0]['mechanism_binding']==binding
    return sealed


def registered_original_worker(root):
    finish(root,1,'H',[],action='experiment',search_choice=plan())
    return registered_worker(root,2,[1])


def selected_paths(job):
    return [row['material']['path'] for row in job.get('selected_inputs', [])]


@pytest.mark.parametrize('selection,expected', [
    (None, [FIRST]), ([], []), (['W002-I002'], [SECOND]),
])
def test_a_h_w_mult_parent_selection_carries_clears_or_replaces(scene, selection, expected):
    original_worker(scene)
    finish(scene, 3, 'A', [1, 2], action='revise_h', selection=['W002-I001'])
    hjob, _, _ = finish(scene, 4, 'H', [1, 2, 3], action='experiment',
                         search_choice=plan(existing_ref='h001.m1'), selection=selection)
    assert selected_paths(hjob) == [FIRST]
    successor = c.build_job(scene, 5, 'W', [4])
    assert selected_paths(successor) == expected
    for path in (FIRST, SECOND):
        assert (c.workspace(scene, 5) / path).exists() == (path in expected)
    assert [row['path'] for row in successor['direct_refs']] == ['inputs/parents/004/conclusion.json']
    assert c.build_job(scene, 5, 'W', [4]) == successor


@pytest.mark.parametrize('e_selection,expected', [
    (None, [FIRST]), ([], []), (['W002-I002'], [SECOND]),
])
def test_h_e_h_w_clear_is_not_revived_by_original_requester(scene, e_selection, expected):
    original_worker(scene)
    finish(scene, 3, 'A', [1, 2], action='revise_h', selection=['W002-I001'])
    finish(scene, 4, 'H', [1, 2, 3], action='extract',
           search_choice=plan(existing_ref='h001.m1'))
    c.build_job(scene, 5, 'E', [4], evidence_return=dict(role='H', step=4))
    e_job, _, _ = finish(scene, 5, 'E', [4], selection=e_selection)
    assert selected_paths(e_job) == [FIRST]
    h_job, _, _ = finish(scene, 6, 'H', [4, 5], action='experiment',
                         search_choice=plan(existing_ref='h001.m1'))
    assert selected_paths(h_job) == expected
    w_job = c.build_job(scene, 7, 'W', [6])
    assert selected_paths(w_job) == expected
    assert not (c.workspace(scene, 7) / (SECOND if expected == [FIRST] else FIRST)).exists()


@pytest.mark.parametrize('role', ['H', 'W', 'A', 'E'])
def test_bad_ordinary_links_do_not_change_selection_or_prevent_seal(scene, role):
    original_worker(scene)
    text = ' '.join(f'[unresolved {i}](artifacts/missing-{i}.py)' for i in range(100))
    text += ' [escape](artifacts/../hidden.py) [hidden](host-history/objects/private)'
    text += ' [old marker](history/nodes/999/artifacts/code.py#reuse-implementation)'
    text += ' [external](https://example.invalid/code.py)'
    choice = plan(existing_ref='h001.m1') if role == 'H' else None
    if role == 'E':
        finish(scene, 3, 'A', [1, 2], action='extract')
        c.build_job(scene, 4, 'E', [3], evidence_return=dict(role='A', step=3))
        step, parents = 4, [3]
    else:
        step, parents = 3, [1, 2]
    _, work, record = finish(scene, step, role, parents, text=text, search_choice=choice,
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
    _,work,record=finish(scene, 3, 'A', [1, 2], selection=selection)
    seal=c.read(record/'result-identity.json')
    assert seal.get('selected_inputs',[])==[]
    assert seal['control_outcome']['selection_status']=='unresolved'
    assert 'implementation_unresolved' in {row['code'] for row in seal['control_outcome']['diagnostics']}
    assert c.validate(work)['implementation_selection']==selection


@pytest.mark.parametrize('changed', ['original', 'history_page'])
def test_mechanical_hash_mismatch_still_blocks_scientific_seal(scene, changed):
    original_worker(scene)
    job = c.build_job(scene, 3, 'A', [1, 2])
    path = (c.workspace(scene, 2) / 'artifacts/a.py' if changed == 'original'
            else c.workspace(scene, 3) / job['history']['files'][0]['path'])
    if changed == 'history_page':
        path.chmod(0o600)  # Simulate host corruption of the otherwise read-only bound copy.
    path.write_text('CHANGED ORIGINAL')
    with pytest.raises(ValueError):
        finish(scene, 3, 'A', [1, 2], selection=['W002-I001'])
    assert not (c.host_record(c.workspace(scene, 3)) / 'result-identity.json').exists()


@pytest.mark.parametrize('source_count', [0, 2])
def test_first_screen_requires_one_real_same_mechanism_w_task(registered_scene, source_count):
    scene=registered_scene
    if source_count:
        registered_original_worker(scene)
        finish(scene, 3, 'A', [1, 2], action='continue_w',
               search_choice=dict(dispositions=[], next_focus=focus()))
        registered_worker(scene,4,[1,2,3],text='Second distinct screen original')
        step, parents = 5, [1, 2, 4]
    else:
        finish(scene, 1, 'H', [], action='analyze', search_choice=plan())
        step, parents = 2, [1]
    job, work, record = finish(scene, step, 'A', parents, search_choice=review())
    payload = c.science_payload(record / 'science-output.json', 'A', protocol.CURRENT)
    assert search.assess_payload(scene,job,payload)
    seal = c.read(record / 'result-identity.json')
    assert seal['control_outcome']['search_status'] == 'unresolved'
    assert seal['control_outcome']['diagnostics'][0]['code'] == (
        'screen_source_ambiguous' if source_count else 'screen_source_unavailable')
    assert search.project(scene, step + 1)['mechanisms']['h001.m1']['status'] == 'pending'
    assert (work / 'conclusion.json').read_bytes() == (record / 'science-output.json').read_bytes()
    c.validate(work)


@pytest.mark.parametrize('binding', ['unavailable', 'ambiguous'])
def test_repair_without_a_unique_bound_worker_stops_new_dispatch(registered_scene, binding):
    scene=registered_scene
    if binding == 'ambiguous':
        registered_original_worker(scene)
        finish(scene, 3, 'A', [1, 2], action='continue_w',
               search_choice=dict(dispositions=[], next_focus=focus()))
        registered_worker(scene,4,[1,2,3],text='Other same-mechanism worker')
        a_step, parents = 5, [1, 2, 4]
    else:
        original_worker(scene)  # A direct W exists, but no exact managed observation can be repaired.
        a_step, parents = 3, [1, 2]
    job,work,record=finish(scene, a_step, 'A', parents, action='continue_w',
        search_choice=dict(dispositions=[], next_focus=focus('repair')))
    outcome=c.read(record/'result-identity.json')['control_outcome']
    reason='search_repair_source_' + binding
    assert outcome['search_status']=='unresolved'
    assert outcome['diagnostics']==[dict(code=reason,subject='h001.m1')]
    payload=c.validate(work)
    assert payload['search_review']['next_focus']==focus('repair')
    route=semantic.effective_route(scene,a_step,payload,job,
        (*controller._route(scene,a_step,'A',payload,job,1,parents[-1]),None))
    assert route[0]=='A' and route[-1]['reason']==reason
    assert not (scene/'jobs'/f'{a_step+1:03d}.json').exists()
    assert not (c.host_record(c.workspace(scene, a_step + 1)) / 'native-intent.json').exists()


def test_repair_requests_bind_original_root_without_role_paths(registered_scene):
    scene=registered_scene
    registered_original_worker(scene)
    for a_step, w_step, source in [(3, 4, 2), (5, 6, 4)]:
        finish(scene, a_step, 'A', [1, source], action='continue_w',
               search_choice=dict(dispositions=[], next_focus=focus('repair')))
        job, _, _ = registered_worker(scene,w_step,[1,source,a_step])
        assert job['search']['repair_root'] == 2
        assert job['search']['repair_source']['step']==source
        assert job['search']['repair_source']['call']['scope']['step']==source
        assert job['search']['repair_source']['call']['mechanism_binding']['mechanism_ref']=='h001.m1'
        assert job['search']['repair_authorization']['step']==a_step
    finish(scene, 7, 'A', [1, 6], action='continue_w',
           search_choice=dict(dispositions=[], next_focus=focus('repair')))
    job = c.build_job(scene, 8, 'W', [1, 6, 7])
    assert search.launch_reason(scene, job) == 'search_repair_limit'
    assert search.project(scene, 8)['repairs'] == {2: 2}
    assert search.project(scene, 8)['repairs'] == {2: 2}


def test_progress_credit_is_current_worker_original_not_self_authored_a(scene):
    original_worker(scene)
    job, _, _ = finish(scene, 3, 'A', [1, 2], progress=dict(verdict='progress'))
    expected = c.file_ref(c.host_record(c.workspace(scene, 2)) / 'science-output.json')['path']
    assert correction._proofs(scene, job, dict(verdict='progress')) == {expected}
    next_job, _, _ = finish(scene, 4, 'A', [1, 2, 3], progress=dict(verdict='progress'))
    assert correction._proofs(scene, next_job, dict(verdict='progress')) == set()


def test_progress_binds_h_analysis_and_exact_extraction_return(scene):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan())
    job, _, _ = finish(scene, 2, 'A', [1], action='extract', progress=dict(verdict='progress'))
    assert correction._proofs(scene, job, dict(verdict='progress')) == {
        c.file_ref(c.host_record(c.workspace(scene, 1)) / 'science-output.json')['path']}
    c.build_job(scene, 3, 'E', [1, 2], evidence_return=dict(role='A', step=2))
    finish(scene, 3, 'E', [1, 2], text='Original bounded extraction')
    returned, _, _ = finish(scene, 4, 'A', [1, 2, 3], progress=dict(verdict='progress'))
    assert correction._proofs(scene, returned, dict(verdict='progress')) == {
        c.file_ref(c.host_record(c.workspace(scene, 3)) / 'science-output.json')['path']}


def test_equal_worker_raws_keep_independent_original_progress_credit(correcting_scene):
    scene = correcting_scene
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan())
    finish(scene, 2, 'W', [1], text='Same original science bytes',mechanism_findings=PENDING_FINDINGS)
    finish(scene, 3, 'A', [1, 2], action='continue_w', progress=dict(verdict='progress'),
           search_choice=dict(dispositions=[], next_focus=focus()))
    finish(scene, 4, 'W', [1, 2, 3], text='Same original science bytes',mechanism_findings=PENDING_FINDINGS)
    finish(scene, 5, 'A', [1, 4], action='finish', progress=dict(verdict='progress'))
    assert (c.host_record(c.workspace(scene, 2)) / 'science-output.json').read_bytes() == (c.host_record(c.workspace(scene, 4)) / 'science-output.json').read_bytes()
    assert correction.project(scene)['counters'] == dict(no_progress=0, unknown=0)


@pytest.mark.parametrize('fault', [None, 'missing_page', 'changed_page'])
def test_seventeenth_implementation_is_paged_selected_and_hash_bound(scene, monkeypatch, fault):
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan())
    finish(scene, 2, 'W', [1], files={f'a{i:02d}.py': f'ORIGINAL_IMPLEMENTATION_{i}' for i in range(17)},
        mechanism_findings=PENDING_FINDINGS)
    job = c.build_job(scene, 3, 'A', [1, 2])
    work = c.workspace(scene, 3)
    index = c.read(work / job['history']['view']['indexes']['nodes'])
    worker = next(row for row in index['records'] if row['step'] == 2)
    assert worker['implementations_omitted_count'] == 1
    path, objects, pages = worker['implementation_index'], [], []
    while path:
        page_path = work / path
        page = c.read(page_path)
        assert len(page['implementations']) <= 16
        assert page_path.stat().st_size <= history.MAX_PAGE_BYTES
        assert not page_path.stat().st_mode & 0o222
        pages.append(page_path)
        objects.extend(page['implementations'])
        path = page['next_page']
    assert len(pages) == 2
    assert len(objects) == 17 and objects[-1]['id'] == 'W002-I017'
    assert objects[-1]['label'] == 'a16.py'
    assert len((json.dumps(job['history']['view'], ensure_ascii=False) + '\n').encode()) <= history.MAX_BRIEF_BYTES
    if fault:
        seal_result = c.seal_result
        def inject_then_seal(root, step):
            # Fault occurs after the job and native output are fixed, so the
            # assignment builder cannot recreate a missing page before seal.
            if fault == 'missing_page':
                pages[-1].unlink()
            else:
                pages[-1].chmod(0o600)
                pages[-1].write_text('CHANGED IMPLEMENTATION NAVIGATION')
            return seal_result(root, step)
        monkeypatch.setattr(c, 'seal_result', inject_then_seal)
        with pytest.raises(ValueError):
            finish(scene, 3, 'A', [1, 2], selection=['W002-I017'])
        assert not (c.host_record(work) / 'result-identity.json').exists()
        return
    finish(scene, 3, 'A', [1, 2], action='revise_h', selection=['W002-I017'])
    successor = c.build_job(scene, 4, 'H', [3])
    selected = 'inputs/parents/002/artifacts/a16.py'
    assert selected_paths(successor) == [selected]
    mounted = c.workspace(scene, 4) / selected
    assert mounted.read_text() == 'ORIGINAL_IMPLEMENTATION_16'
    assert not mounted.stat().st_mode & 0o222
    assert not (mounted.parent / 'a00.py').exists()
