"""Incremental scientific choices and bounded author correction; no models."""
from test_history_handoff import selection_explanation
import json
import sys

import pytest

from scidiscovery.research_loop import backends, budget, contracts as c, controller, correction, execution, experiments, native, search, semantic
from execution_fixtures import execution_registry
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
from test_breadth_science import run as run_command, findings


def native_intent(root, job):
    """Use real stage anchors once; an accepted intent is never overwritten."""
    record = execution.record(root, job['step'])
    path = record / 'native-intent.json'
    task = c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
    if not path.exists():
        node_anchor = budget.accept_node(root, job)
        anchor = search.accept(root, job) if node_anchor is None else None
        c.write(path, dict(step=job['step'], task_ref=task,
            **(dict(search_slice=anchor) if anchor is not None else {}),
            **(dict(budget_slice=node_anchor) if node_anchor is not None else {})))
    original = c.regular(path).read_bytes()
    intent = c.read(path)
    assert intent['task_ref'] == task
    if job['role'] == 'W' and job.get('search') and budget.node_limits(root, job) is None:
        assert search.stop_reason(root, job, intent, budget.snapshot(root),
            dict(input_tokens=0, output_tokens=0)) is None
    return original


def finish(root, step, role, parents, text='A bounded scientific finding', action='wait', files=None,
           summary='A contribution', limitations=None, recommendation=None, search_choice=None,
           progress=None, selection=None, mechanism_findings=None):
    """Current fake native output, preserving the exact accepted intent and budget."""
    path = root / 'jobs' / f'{step:03d}.json'
    previous = c.read(path) if path.exists() else {}
    job = c.build_job(root, step, role, parents, previous.get('instruction', ''),
        previous.get('evidence_return'), previous.get('correction_request'), previous.get('author_correction'),
        **(dict(final_report=previous['final_report']) if 'final_report' in previous else {}))
    work = c.workspace(root, step);record = execution.record(root, step)
    original_intent = native_intent(root, job)
    payload = dict(summary=summary, scientific_result=text,
        limitations=limitations if limitations is not None else ['Fixture only'], implementation_selection=selection)
    if role == 'W':
        if mechanism_findings is None:
            labels = {row['mechanism_ref']: row['label'] for row in job['search']['breadth']['mechanisms']}
            mechanism_findings = [dict(mechanism=labels[row['mechanism_ref']], basis='blocked',
                finding='This mechanical fixture has no experiments or observations for the mechanism.')
                for row in job['search']['coverage_obligation']]
        payload.update(stage_intent='review', mechanism_findings=mechanism_findings)
        task=c.read(work/'science-task.json')
        if task.get('candidate_nomination_mechanisms'):
            payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
                reason='This no-model recovery fixture declares no scientifically suitable candidate.')
                for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])
    if role == 'H':
        payload['search_plan'] = search_choice
    if role == 'A':
        payload.update(candidate_recommendation=recommendation, search_review=search_choice,
            progress_assessment=progress, decision_request=None, portfolio_review=None)
    if role in {'H', 'A'}:
        payload.update(next_action=action, route_reason='Exact scientific question')
    for name, contents in (files or {}).items():
        target = work / 'artifacts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record / 'science-output.json', payload)
    meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    c.write(record / 'usage.json',meter)
    terminal=dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json'))
    intent=c.read(record/'native-intent.json')
    if (intent.get('budget_slice') or {}).get('final_return') is not None:
        backend=backends.selected(c.settings(root,job))
        terminal.update(cleanup='observed_group_cleaned',native_backend=backend.name,
            metering=dict(coverage='terminal',reason=None,source=backend.terminal_source,tokens=meter))
        accepted=budget.final_a_return(root,job,intent)
        assert accepted is not None
        terminal['final_a_completion']=budget.final_a_receipt(accepted,intent,meter,terminal['usage'])
        c.write(record/'usage-observation.json',terminal['metering'])
    c.write(record/'native-outcome.json',terminal)
    c.seal_result(root, step)
    assert (record / 'native-intent.json').read_bytes() == original_intent
    return job, work, record


def observed_worker(root, step, parents, *, observed):
    """Retain a prebuilt route while running real tiny registered local commands."""
    path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(path) if path.exists() else c.build_job(root, step, 'W', parents)
    assert job['role'] == 'W' and [int(row['path'].split('/')[2]) for row in job['direct_refs']] == parents
    record = execution.record(root, step)
    original_intent = native_intent(root, job)
    output = root.parent / f'semantic-execution-{step}.json'
    experiments.configure(root, step, 'local_execution:command', dict(run_argv=[sys.executable]), output)
    config = c.read(output)
    mechanisms = config['breadth']['mechanisms']
    assigned = {row['mechanism_ref'] for row in job['search']['coverage_obligation']}
    assert set(observed) <= assigned
    selected = [row for row in mechanisms if row['mechanism_ref'] in observed]
    assert len(selected) == len(observed)
    for row in selected:
        assert run_command(root, config, row['mechanism_ref'])['status'] == 'known_success'
    result = finish(root, step, 'W', parents, files={'candidate.py': 'def predict(): return 1\n'},
        mechanism_findings=findings(*observed,
            blocked=sorted(assigned - set(observed))))
    assert (record / 'native-intent.json').read_bytes() == original_intent
    return result


def prepare_scene(tmp_path, *, max_tokens=100_000, max_steps=32, correction_policy=None):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare mechanisms, preserve original findings and correct unsupported choices.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=max_tokens,
                       max_seconds=3600, max_steps=max_steps, model='fake', reasoning_effort='high',
                       correction_policy=correction_policy)
    return root


@pytest.fixture
def scene(tmp_path, execution_registry):
    return prepare_scene(tmp_path)


def commitment(label, existing_ref=None):
    return dict(label=label, minimal_test='Discriminating scientific comparison', existing_ref=existing_ref)


def plan(rows, label, action='screen'):
    return dict(scope='Global scientific comparison', commitments=rows,
                focus=dict(label=label, action=action) if label is not None else None)


def focus(ref='h001.m1', action='screen'):
    return dict(mechanism_ref=ref, action=action)


def disposition(ref, status):
    return dict(mechanism_ref=ref, status=status, reason='Independent scientific interpretation with limits')


def review(items=(), next_focus=None):
    return dict(dispositions=list(items), next_focus=next_focus)


def outcome(record):
    value = c.read(record / 'result-identity.json')['control_outcome']
    assert value['binding_protocol'] == CURRENT
    assert len(value['diagnostics']) <= 8
    assert all(set(row) == {'code', 'subject'} for row in value['diagnostics'])
    return value


def four_screened(root):
    finish(root, 1, 'H', [], action='experiment',
           search_choice=plan([commitment(f'Mechanism {i}') for i in range(1, 5)], 'Mechanism 1'))
    for index in range(1, 5):
        w_step, a_step = index * 2, index * 2 + 1
        parents = [1] if index == 1 else [1, w_step - 2, w_step - 1]
        observed_worker(root, w_step, parents, observed=[f'h001.m{index}'])
        finish(root, a_step, 'A', [1, w_step],
               action='revise_h' if index == 4 else 'continue_w',
               search_choice=review([disposition(f'h001.m{index}', 'screened')],
                                    focus(f'h001.m{index + 1}') if index < 4 else None))


def test_h_adds_fifth_mechanism_without_repeating_four_screened_originals(scene):
    four_screened(scene)
    originals = {step: (c.host_record(c.workspace(scene, step)) / 'science-output.json').read_bytes()
                 for step in range(1, 10)}
    job, _, record = finish(scene, 10, 'H', [1, 8, 9], action='experiment',
                            search_choice=plan([commitment('Mechanism 5')], 'Mechanism 5'))
    assert job['handoff_protocol'] == CURRENT
    assert outcome(record)['search_status'] == 'applied'
    state = search.project(scene, 11)
    assert {ref: row['status'] for ref, row in state['mechanisms'].items()} == {
        **{f'h001.m{i}': 'screened' for i in range(1, 5)}, 'h010.m1': 'pending'}
    assert state['focus']['mechanism_ref'] == 'h010.m1'
    assert len(state['mechanisms']) == 5
    assert all((c.host_record(c.workspace(scene, step)) / 'science-output.json').read_bytes() == raw
               for step, raw in originals.items())


@pytest.mark.parametrize('focus_label', ['Renamed mechanism', 'h001.m1'])
def test_known_identity_survives_rename_and_later_omission(scene, focus_label):
    four_screened(scene)
    finish(scene, 10, 'H', [1, 8, 9], action='experiment',
           search_choice=plan([commitment('Renamed mechanism', 'h001.m1')], 'h001.m1', 'improve'))
    finish(scene, 11, 'H', [10], action='experiment',
           search_choice=plan([], focus_label, 'improve'))
    state = search.project(scene, 12)
    assert set(state['mechanisms']) == {f'h001.m{i}' for i in range(1, 5)}
    assert state['mechanisms']['h001.m1']['label'] == 'Renamed mechanism'
    assert state['mechanisms']['h001.m1']['original_h'] == 1
    assert state['mechanisms']['h001.m1']['source_h'] == 10
    assert {ref: row['status'] for ref, row in state['mechanisms'].items()} == {
        'h001.m1': 'pending', **{f'h001.m{i}': 'screened' for i in range(2, 5)}}
    assert state['focus']['mechanism_ref'] == 'h001.m1'


def test_unknown_h_focus_keeps_science_but_does_not_apply_plan_or_invent_mechanism(scene):
    four_screened(scene)
    text = 'A scientific alternative was considered; the claimed focus has no declared identity.'
    _, work, record = finish(scene, 10, 'H', [1, 8, 9], text=text, action='experiment',
                              search_choice=plan([commitment('Mechanism 5')], 'Unbound textual focus'))
    control = outcome(record)
    assert control['search_status'] == 'unresolved'
    assert control['diagnostics'] == [dict(code='science_focus_unauthorized', subject='Unbound textual focus')]
    assert c.validate(work)['scientific_result'] == text
    state = search.project(scene, 11)
    assert set(state['mechanisms']) == {f'h001.m{i}' for i in range(1, 5)}
    assert all(row['status'] == 'screened' for row in state['mechanisms'].values())
    assert state['focus'] is None


@pytest.mark.parametrize('binding', ['unavailable', 'ambiguous'])
def test_bad_screen_binding_retains_science_without_applying_unresolved_dispositions(scene, binding):
    finish(scene, 1, 'H', [], action='analyze' if binding == 'unavailable' else 'experiment',
           search_choice=plan([commitment('Mechanism 1'), commitment('Mechanism 2')], 'Mechanism 1'))
    if binding == 'ambiguous':
        observed_worker(scene, 2, [1], observed=['h001.m1'])
        finish(scene, 3, 'A', [1, 2], action='continue_w', search_choice=review(next_focus=focus()))
        observed_worker(scene, 4, [1, 2, 3], observed=['h001.m1'])
        step, parents = 5, [1, 2, 4]
    else:
        step, parents = 2, [1]
    text = 'Interpret both mechanisms independently without manufacturing a screening receipt.'
    _, work, record = finish(scene, step, 'A', parents, text=text, action='continue_w',
                              search_choice=review([disposition('h001.m1', 'screened'),
                                                    disposition('h001.m2', 'deferred')], focus(action='improve')))
    control = outcome(record)
    assert control['search_status'] == 'unresolved'
    assert f'screen_source_{binding}' in {row['code'] for row in control['diagnostics']}
    assert c.validate(work)['scientific_result'] == text
    state = search.project(scene, step + 1)
    assert state['mechanisms']['h001.m1']['status'] == 'pending'
    assert state['mechanisms']['h001.m2']['status'] == 'pending'
    assert c.read(record / 'science-output.json')['search_review']['dispositions'][1] == disposition('h001.m2', 'deferred')


def test_invalid_selection_never_falls_back_to_predecessor_implementation(scene):
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
    finish(scene, 2, 'W', [1], files={'candidate.py': 'ORIGINAL_IMPLEMENTATION'})
    finish(scene, 3, 'A', [1, 2], action='revise_h', selection=['W002-I001'])
    job, _, record = finish(scene, 4, 'H', [1, 2, 3], action='experiment',
                            search_choice=plan([], 'h001.m1'), selection=['W002-I999'])
    old_path = 'inputs/parents/002/artifacts/candidate.py'
    assert [row['material']['path'] for row in job['selected_inputs']] == [old_path]
    control = outcome(record)
    assert control['selection_status'] == 'unresolved'
    assert 'implementation_unresolved' in {row['code'] for row in control['diagnostics']}
    assert c.read(record / 'result-identity.json').get('selected_inputs', []) == []
    next_job = c.build_job(scene, 5, 'W', [4])
    assert next_job.get('selected_inputs', []) == []
    assert not (c.workspace(scene, 5) / old_path).exists()


def finish_bound(root, step, **choices):
    """Stand-in native transport; production owns the actual job and sealing."""
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    parents = [int(row['path'].split('/')[2]) for row in job['direct_refs']]
    return finish(root, step, job['role'], parents, **choices)


def assert_public_author_request(root, job, remaining):
    request = job['author_correction']
    view = c.science_view(job, root)
    public = view['control_request']
    assert set(public) == {'reason', 'source_step', 'original_science', 'initial_attempt_step',
                           'unresolved', 'remaining_author_corrections'}
    assert public['reason'] == request['reason']
    assert public['source_step'] == request['source']['step']
    assert public['initial_attempt_step'] == request['root']['step']
    assert public['original_science'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json'
    assert public['remaining_author_corrections'] == remaining
    text = json.dumps(view, ensure_ascii=False)
    for key in ('job_ref', 'raw_ref', 'seal_ref'):
        assert key not in text
        assert all(identity[key]['path'] not in text for identity in (request['root'], request['source']))


def original_bytes(root, steps):
    paths = []
    for step in steps:
        paths.append(root / 'jobs' / f'{step:03d}.json')
        paths.extend(path for base in (c.workspace(root, step), c.host_record(c.workspace(root, step)))
                     for path in base.rglob('*') if path.is_file())
    return {path: path.read_bytes() for path in paths}


def assert_original_bytes(originals):
    assert all(path.is_file() and path.read_bytes() == data for path, data in originals.items())


@pytest.mark.parametrize('last_error', ['focus', 'selection'])
def test_author_correction_cap_survives_new_prose_error_kind_and_restart(scene, monkeypatch, last_error):
    calls = []
    def execute(root, step):
        calls.append(step)
        if step > 1:
            assert_public_author_request(root, c.read(root / 'jobs' / f'{step:03d}.json'), 4 - step)
        label = f'Declared mechanism {step}'
        finish_bound(root, step, action='experiment', text=f'Different scientific prose {step}',
                     search_choice=plan([commitment(label)], label if step == 3 and last_error == 'selection'
                                        else f'Unbound focus {step}'),
                     selection=['W999-I001'] if step == 3 and last_error == 'selection' else None)
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert semantic.MAX_AUTHOR_CORRECTIONS == 2
    assert result['status'] == 'semantic_correction_required'
    assert result['reason'] == 'semantic_correction_limit'
    assert result['completed_steps'] == 3 and result['usage']['observed_tokens'] == 30
    assert calls == [1, 2, 3]
    requests = [c.read(scene / 'jobs' / f'{step:03d}.json')['author_correction'] for step in (2, 3)]
    assert requests[0]['root'] == requests[1]['root']
    assert requests[0]['root']['step'] == 1
    assert requests[0]['reason'] == requests[1]['reason']
    assert [item['source']['step'] for item in requests] == [1, 2]
    assert semantic.accepted(scene, requests[-1], 4) == 2
    assert not (scene / 'jobs/004.json').exists()
    originals = original_bytes(scene, range(1, 4))
    consumed = budget.snapshot(scene)['advance_seconds']
    assert consumed > 0
    assert controller.run(scene)['reason'] == 'semantic_correction_limit'
    assert calls == [1, 2, 3]
    assert budget.snapshot(scene)['advance_seconds'] == consumed
    assert_original_bytes(originals)


@pytest.mark.parametrize('limit,expected', [('tokens', 'closeout_reserve_tokens'), ('steps', 'bounded_steps_exhausted')])
def test_author_corrections_consume_original_token_and_node_budget(tmp_path, monkeypatch, limit, expected):
    root = prepare_scene(tmp_path, max_tokens=20 if limit == 'tokens' else 100_000,
                         max_steps=2 if limit == 'steps' else 32)
    calls = [];spent={};originals={}
    def execute(root, step):
        calls.append(step)
        job=c.read(root/'jobs'/f'{step:03d}.json')
        if job.get('final_only'):
            assert step==3 and job['role']=='A' and spent==dict(tokens=20,nodes=2)
            assert native.usage(root)['observed_tokens']==20
            originals.update(original_bytes(root,[1,2]))
            finish_bound(root,step,action='finish',text='The original author corrections consumed the exploration budget; no accepted W evidence or external submission is available.')
        else:
            finish_bound(root, step, action='experiment', search_choice=plan([commitment('Declared')], 'Undeclared'))
            if step==2:spent.update(tokens=native.usage(root)['observed_tokens'],nodes=budget.used_steps(root))
    monkeypatch.setattr(native, 'execute_native', execute)
    settings = (root / 'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status']=='final_a_completed'
    assert budget.closeout_state(root)['begin']['stop_reason']==expected
    assert result['completed_scientific_nodes']==3 and result['usage']['observed_tokens']==30
    assert result['submission_result']['status']=='unsubmitted' and budget.closeout_state(root)['actions']==[]
    assert calls==[1,2,3] and [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in calls]==['H','H','A']
    final=budget.final_a_barrier(root)
    assert final['step']==3 and final['selection']['accounted_tokens']==20
    record=execution.origin(root,3)
    assert c.read(record/'native-outcome.json')['final_a_completion']['final_return']==c.read(record/'native-intent.json')['budget_slice']['final_return']
    assert c.validate(c.workspace(root,3))['next_action']=='finish'
    assert not (root / 'jobs/004.json').exists()
    assert (root / 'settings.json').read_bytes() == settings
    assert_original_bytes(originals)
    originals.update(original_bytes(root,[3]))
    assert controller.run(root)['status']=='final_a_completed'
    assert calls==[1,2,3]
    assert_original_bytes(originals)


def test_a_returns_to_its_author_and_only_legal_correction_permits_next_w(scene, monkeypatch):
    calls = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if step == 1:
            finish_bound(root, step, action='analyze', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
        elif step == 2:
            finish_bound(root, step, action='continue_w',
                         search_choice=review([disposition('h001.m1', 'screened')], focus(action='improve')))
        elif step == 3:
            assert job['author_correction']['root']['step'] == 2
            finish_bound(root, step, action='continue_w',
                         search_choice=review([disposition('h001.m1', 'pending')], focus(action='improve')))
        elif job['role'] == 'W':
            finish_bound(root, step, text='Work after a bounded legal scientific clarification')
        else:
            finish_bound(root, step, action='finish')
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [(1, 'H'), (2, 'A'), (3, 'A'), (4, 'W'), (5, 'A')]
    assert outcome(c.host_record(c.workspace(scene, 2)))['search_status'] == 'unresolved'
    assert search.project(scene, 6)['mechanisms']['h001.m1']['status'] == 'pending'
    assert c.read(c.host_record(c.workspace(scene, 2)) / 'science-output.json')['search_review']['dispositions'] == [
        disposition('h001.m1', 'screened')]  # The unsupported original remains unchanged.

    originals = original_bytes(scene, range(1, 6))
    assert controller.run(scene)['status'] == 'finish'
    assert len(calls) == 5
    assert_original_bytes(originals)


def test_waived_focus_stays_unresolved_and_never_authorizes_a_worker(scene, monkeypatch):
    calls = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if step == 1:
            finish_bound(root, step, action='analyze', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
        else:
            assert job['role'] == 'A'
            finish_bound(root, step, action='continue_w',
                search_choice=review([disposition('h001.m1', 'waived')], focus(action='improve')))
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'semantic_correction_required' and result['reason'] == 'semantic_correction_limit'
    assert calls == [(1, 'H'), (2, 'A'), (3, 'A'), (4, 'A')]
    for step in (2, 3, 4):
        record = c.host_record(c.workspace(scene, step))
        control = outcome(record)
        assert control['search_status'] == 'unresolved'
        assert control['diagnostics'] == [dict(code='science_focus_waived', subject='h001.m1')]
        assert c.read(record / 'science-output.json')['search_review']['dispositions'] == [disposition('h001.m1', 'waived')]
    assert search.project(scene, 5)['mechanisms']['h001.m1']['status'] == 'pending'
    assert not (scene / 'jobs/005.json').exists()
    originals = original_bytes(scene, range(1, 5))
    assert controller.run(scene)['reason'] == 'semantic_correction_limit'
    assert len(calls) == 4
    assert_original_bytes(originals)


def test_invalid_selection_gets_h_correction_before_experiment_and_never_uses_old_fallback(scene, monkeypatch):
    calls = []
    old_path = 'inputs/parents/002/artifacts/candidate.py'
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if step == 1:
            finish_bound(root, step, action='experiment', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
        elif step == 2:
            finish_bound(root, step, files={'candidate.py': 'ORIGINAL_IMPLEMENTATION'})
        elif step == 3:
            finish_bound(root, step, action='revise_h', selection=['W002-I001'])
        elif step in {4, 5}:
            if step == 5:
                assert job['role'] == 'H' and job['author_correction']['root']['step'] == 4
                assert job.get('selected_inputs', []) == []
            finish_bound(root, step, action='experiment', search_choice=plan([], 'h001.m1'),
                         selection=['W002-I999'] if step == 4 else ['W002-I001'])
        elif job['role'] == 'W':
            assert [row['material']['path'] for row in job['selected_inputs']] == [old_path]
            assert (c.workspace(root, step) / old_path).read_text() == 'ORIGINAL_IMPLEMENTATION'
            finish_bound(root, step)
        else:
            finish_bound(root, step, action='finish')
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'H'), (5, 'H'), (6, 'W'), (7, 'A')]


def terminal_payload(root, step, payload):
    record = c.host_record(c.workspace(root, step))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json')))
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))


def h_payload(choice):
    return dict(summary='Scientific author finding', scientific_result='Retain the original scientific reasoning.',
                limitations=['Bounded control interpretation'], implementation_selection=None,
                next_action='experiment', route_reason='Exact scientific question', search_plan=choice)


def test_unknown_submission_is_never_rerouted_or_replayed(scene, monkeypatch):
    c.build_job(scene, 1, 'H', [])
    record = c.host_record(c.workspace(scene, 1))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(scene / 'jobs/001.json')))
    def forbidden(*args, **kwargs):
        pytest.fail('Unknown native submission must not dispatch a correction or replay')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    intent = (record / 'native-intent.json').read_bytes()
    for _ in range(2):
        assert controller.run(scene)['status'] == 'native_outcome_unsealed'
    assert (record / 'native-intent.json').read_bytes() == intent
    assert not (record / 'result-identity.json').exists()
    assert not (scene / 'jobs/002.json').exists()


@pytest.mark.parametrize('changed', ['raw', 'job', 'history_page'])
def test_integrity_corruption_is_not_semantic_author_recovery(scene, monkeypatch, changed):
    step=3 if changed=='history_page' else 1
    if step==3:
        finish(scene,1,'H',[],action='experiment',search_choice=plan([commitment('Declared')],'Declared'))
        route=c.read(c.host_record(c.workspace(scene,1))/'science-output.json')['route_reason']
        c.build_job(scene,2,'W',[1],instruction=route)
        finish(scene,2,'W',[1])
        route=controller._route(scene,2,'W',c.validate(c.workspace(scene,2)),c.read(scene/'jobs/002.json'),1,None)
        assert route[0]=='A' and route[2]
        job=c.build_job(scene,3,route[0],route[1],route[2],route[3])
        payload=dict(summary='Scientific attempt',scientific_result='Retain science, not unauthorized focus',limitations=['Fixture only'],
            implementation_selection=None,candidate_recommendation=None,progress_assessment=None,
            decision_request=None,portfolio_review=None,search_review=review(next_focus=focus('unknown')),
            next_action='continue_w',route_reason='Needs bounded author repair')
    else:
        job=c.build_job(scene,1,'H',[]);payload=h_payload(plan([commitment('Declared')],'Undeclared'))
    terminal_payload(scene,step,payload)
    record=c.host_record(c.workspace(scene,step))
    if changed == 'raw':
        (record / 'science-output.json').write_text('TAMPERED SCIENCE')
    elif changed == 'job':
        c.write(scene / 'jobs/001.json', {**job, 'instruction': 'Different immutable task'})
    else:
        page = c.workspace(scene, step) / job['history']['view']['indexes']['nodes']
        page.chmod(0o600)
        page.write_text('TAMPERED HISTORY')
    def forbidden(*args, **kwargs):
        pytest.fail('Integrity corruption cannot create a new scientific attempt')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    stopped=controller.run(scene)
    if changed=='history_page':
        assert stopped['status']=='seal_pending' and stopped['completed_steps']==2
        assert 'Bound scientific history page changed' in stopped['reason']
    else:
        assert stopped['status'] in {'native_outcome_unsealed','seal_pending'}
    assert not (record / 'result-identity.json').exists()
    assert not (scene / 'jobs' / f'{step+1:03d}.json').exists()


def test_terminal_unsealed_attempt_is_collected_before_new_author_work(scene, monkeypatch):
    c.build_job(scene, 1, 'H', [])
    terminal_payload(scene, 1, h_payload(plan([commitment('Declared')], 'Undeclared')))
    originals = original_bytes(scene, [1])
    calls = []
    def execute(root, step):
        calls.append(step)
        if step == 2:
            finish_bound(root, step, action='analyze', search_choice=plan([commitment('Corrected')], 'Corrected'))
        else:
            finish_bound(root, step, action='finish')
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [2, 3]
    assert outcome(c.host_record(c.workspace(scene, 1)))['search_status'] == 'unresolved'
    assert_original_bytes(originals)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [2, 3]


def test_current_terminal_tenth_attempt_survives_finite_author_correction_without_replay(scene, monkeypatch):
    role, parents, instruction, evidence_return, current_h, current_w = 'H', [], '', None, None, None
    for step in range(1, 10):
        job = c.build_job(scene, step, role, parents, instruction, evidence_return)
        if role == 'H':
            finish_bound(scene, step, action='experiment', search_choice=plan(
                [commitment(f'Mechanism {i}') for i in range(1, 5)], 'Mechanism 1'))
        elif role == 'A':
            index = (step - 1) // 2
            finish_bound(scene, step, action='revise_h' if index == 4 else 'continue_w',
                search_choice=review([disposition(f'h001.m{index}', 'screened')],
                                     focus(f'h001.m{index + 1}') if index < 4 else None))
        else:
            observed_worker(scene, step, parents, observed=[f'h001.m{step // 2}'])
        role, parents, instruction, evidence_return, current_h, current_w = controller._route(
            scene, step, role, c.validate(c.workspace(scene, step)), job, current_h, current_w)
    assert role == 'H' and parents == [1, 8, 9]
    c.build_job(scene, 10, role, parents, instruction, evidence_return)
    terminal_payload(scene, 10, h_payload(plan([commitment('Mechanism 5')], 'Undeclared mechanism')))
    assert not (c.host_record(c.workspace(scene, 10)) / 'result-identity.json').exists()
    originals = original_bytes(scene, range(1, 11))
    calls = []
    def execute(root, step):
        calls.append(step)
        # The retained tenth terminal turn is sealed before any new author acts.
        assert outcome(c.host_record(c.workspace(root, 10)))['search_status'] == 'unresolved'
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        if step == 11:
            assert job['role'] == 'H' and job['handoff_protocol'] == CURRENT
            assert job['author_correction']['root']['step'] == 10
            finish_bound(root, step, action='analyze', search_choice=plan([commitment('Mechanism 5')], 'Mechanism 5'))
        else:
            assert job['role'] == 'A'
            finish_bound(root, step, action='finish')
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [11, 12]
    assert c.read(scene / 'jobs/010.json')['handoff_protocol'] == CURRENT
    assert all(outcome(c.host_record(c.workspace(scene, step)))['binding_protocol'] == CURRENT
               for step in range(1, 10))
    assert_original_bytes(originals)
    assert controller.run(scene)['status'] == 'finish'
    assert calls == [11, 12]
    assert_original_bytes(originals)


@pytest.mark.parametrize('protocol', [7, 8])
def test_retired_write_schema_is_rejected_without_changing_originals(scene, protocol):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan([commitment('Declared')], 'Declared'))
    originals = original_bytes(scene, [1])
    paths = {path for path in scene.rglob('*') if path.is_file()}
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('H', protocol)
    assert {path for path in scene.rglob('*') if path.is_file()} == paths
    assert_original_bytes(originals)


def test_more_than_sixteen_multibyte_mechanisms_have_bounded_readonly_navigation(scene):
    for step in range(1, 6):
        rows = [dict(label=f'{step}-{index}' + '机' * 70, minimal_test='证据' * 500, existing_ref=None)
                for index in range(4)]
        finish(scene, step, 'H', [step - 1] if step > 1 else [], action='analyze',
               search_choice=plan(rows, rows[0]['label']))
    job = c.build_job(scene, 6, 'A', [5])
    view = c.science_view(job,scene)['lineage']
    assert 'search' not in c.science_view(job,scene)
    assert view['mechanisms']['count'] == 20
    assert len(json.dumps(view, ensure_ascii=False, indent=2).encode()) <= search.MAX_VIEW_BYTES
    path, rows, pages = view['indexes']['mechanisms'], [], []
    while path:
        page_path = c.workspace(scene, 6) / path
        page = c.read(page_path)
        assert len(page['mechanisms']) <= 16
        assert page_path.stat().st_size <= 64_000
        assert not page_path.stat().st_mode & 0o222
        for row in page['mechanisms']:
            assert len(row['minimal_test'].encode()) <= 160 and row['minimal_test_truncated'] is True
            assert (c.workspace(scene, 6) / row['original']).is_file()
        rows.extend(page['mechanisms'])
        pages.append(page_path)
        path = page['next_page']
    assert len(pages) == 2 and len(rows) == 20
    assert {row['mechanism_ref'] for row in rows} == {f'h{step:03d}.m{index}'
                                                     for step in range(1, 6) for index in range(1, 5)}


@pytest.mark.parametrize('response', ['experiment', 'analyze'])
def test_author_h_retains_n4_request_and_only_real_work_can_resolve_review(tmp_path, monkeypatch, response):
    root = prepare_scene(tmp_path, correction_policy=dict(review_after=1, pause_after=2))
    calls = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if step == 1:
            finish_bound(root, step, action='experiment', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
        elif step == 2:
            finish_bound(root, step, text='Initial bounded scientific experiment')
        elif step == 3:
            finish_bound(root, step, action='continue_w', progress=dict(verdict='no_progress'),
                         search_choice=review(next_focus=focus()))
        elif step == 4:
            assert job['role'] == 'H' and job['correction_request'] is not None
            finish_bound(root, step, action='experiment', search_choice=plan([], 'Undeclared focus'))
        elif step == 5:
            assert job['role'] == 'H' and job['author_correction']['root']['step'] == 4
            assert job['correction_request'] == c.read(root / 'jobs/004.json')['correction_request']
            finish_bound(root, step, action=response, text='Scientific author clarification, not a new experiment',
                         search_choice=plan([], 'h001.m1'))
        elif job['role'] == 'W':
            finish_bound(root, step, text='New independently assessed global contribution')
        else:
            finish_bound(root, step, action='finish', progress=dict(verdict='progress'))
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(root)['status'] == 'finish'
    state = correction.project(root)
    assert state['current_pause'] is None
    h4, h5 = [c.read(root / 'jobs' / f'{step:03d}.json') for step in (4, 5)]
    assert h5['correction_request'] == h4['correction_request']
    correction.validate_job(root, h5)
    if response == 'experiment':
        assert calls == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'H'), (5, 'H'), (6, 'W'), (7, 'A')]
        assert state['counters'] == dict(no_progress=0, unknown=0)
        assert state['review_request'] is None
        a_job = c.read(root / 'jobs/007.json')
        assert correction._proofs(root, a_job, dict(verdict='progress')) == {
            c.file_ref(c.host_record(c.workspace(root, 6)) / 'science-output.json')['path']}
    else:
        assert calls == [(1, 'H'), (2, 'W'), (3, 'A'), (4, 'H'), (5, 'H'), (6, 'A')]
        a_job = c.read(root / 'jobs/006.json')
        assert correction._proofs(root, a_job, dict(verdict='progress')) == set()
        assert state['counters'] == dict(no_progress=1, unknown=0)
    originals = original_bytes(root, range(1, len(calls) + 1))
    assert controller.run(root)['status'] == 'finish'
    assert_original_bytes(originals)
    assert len(calls) == (7 if response == 'experiment' else 6)


def test_e_requested_at_author_cap_preserves_root_and_does_not_renew_author_count(scene, monkeypatch):
    calls = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if job['role'] == 'H':
            finish_bound(root, step, action='extract' if step == 3 else 'experiment',
                         search_choice=plan([commitment(f'Declared {step}')], f'Unbound {step}'))
        else:
            assert step == 4 and job['role'] == 'E'
            assert job['evidence_return'] == dict(role='H', step=3)
            finish_bound(root, step, text='Exact original extraction for the second author correction')
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'semantic_correction_required' and result['reason'] == 'semantic_correction_limit'
    assert calls == [(1, 'H'), (2, 'H'), (3, 'H'), (4, 'E')]
    assert result['completed_steps'] == 4 and result['usage']['observed_tokens'] == 40
    jobs = [c.read(scene / 'jobs' / f'{step:03d}.json') for step in (2, 3, 4)]
    requests = [job['author_correction'] for job in jobs]
    assert all(request['root'] == requests[0]['root'] for request in requests)
    assert all(request['reason'] == requests[0]['reason'] for request in requests)
    assert [request['source']['step'] for request in requests] == [1, 2, 3]
    assert semantic.accepted(scene, requests[-1], 5) == 2
    assert not (scene / 'jobs/005.json').exists()
    originals = original_bytes(scene, range(1, 5))
    assert controller.run(scene)['reason'] == 'semantic_correction_limit'
    assert calls == [(1, 'H'), (2, 'H'), (3, 'H'), (4, 'E')]
    assert_original_bytes(originals)


@pytest.mark.parametrize('changed', ['status', 'diagnostics'])
def test_tampered_control_outcome_is_integrity_failure_not_author_correction(scene, monkeypatch, changed):
    _, work, record = finish(scene, 1, 'H', [], action='experiment',
                             search_choice=plan([commitment('Declared')], 'Undeclared'))
    path = record / 'result-identity.json'
    seal = c.read(path)
    if changed == 'status':
        seal['control_outcome']['search_status'] = 'applied'
    else:
        seal['control_outcome']['diagnostics'][0]['subject'] = 'Different unbound choice'
    c.write(path, seal)
    corrupted = path.read_bytes()
    with pytest.raises(ValueError):
        c.validate(work)
    def forbidden(*args, **kwargs):
        pytest.fail('A forged control receipt cannot authorize a new author or experiment')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    assert controller.run(scene)['status'] == 'sealed_node_invalid'
    assert path.read_bytes() == corrupted
    assert not (scene / 'jobs/002.json').exists()


def test_prepared_old_w_is_preserved_when_effective_route_requires_author_h(scene, monkeypatch):
    calls, prepared = [], {}
    def execute(root, step):
        calls.append(step)
        assert step == 1
        finish_bound(root, step, action='experiment', search_choice=plan([commitment('Declared')], 'Undeclared'))
        # Represents a controller which froze its old W successor before the
        # semantic author route was implemented; that unsubmitted task is immutable.
        c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
        prepared.update(original_bytes(root, [2]))
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'retained_job_conflict'
    assert calls == [1]
    assert c.read(scene / 'jobs/002.json')['role'] == 'W'
    assert not (c.host_record(c.workspace(scene, 2)) / 'native-intent.json').exists()
    assert not (scene / 'jobs/003.json').exists()
    assert_original_bytes(prepared)
    assert controller.run(scene)['status'] == 'retained_job_conflict'
    assert calls == [1]
    assert_original_bytes(prepared)
