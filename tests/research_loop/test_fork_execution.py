"""Real child controller/native process boundary; synthetic science, no models."""
import copy
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, fork_service, instance_control, native
from test_execution_authority import prepare
from test_history_handoff import finish
from execution_fixtures import execution_registry


@pytest.fixture
def science_errors(monkeypatch):
    """Keep complete host traces across both original and child process chains."""
    errors = []
    def diagnostic(function):
        def checked(*args, **kwargs):
            try:
                return function(*args, **kwargs)
            except Exception:
                errors.append(traceback.format_exc())
                if function.__name__ == 'seal_result':
                    from scidiscovery.research_loop import execution
                    try:
                        record = execution.record(args[0], args[1])
                        for name in ('native-outcome.json', 'native.stderr'):
                            path = record / name
                            if path.is_file():
                                errors.append(str(path) + '\n' + path.read_text()[-6000:])
                    except (OSError, ValueError, TypeError, KeyError):
                        errors.append(traceback.format_exc())
                raise
        return checked
    monkeypatch.setattr(c, 'validate', diagnostic(c.validate))
    monkeypatch.setattr(c, 'seal_result', diagnostic(c.seal_result))
    monkeypatch.setattr(native, 'seal_result', c.seal_result)
    monkeypatch.setattr(controller, '_route', diagnostic(controller._route))
    original_finish = controller._finish
    def finish_with_exception(*args, **kwargs):
        if sys.exc_info()[0] is not None:
            errors.append(traceback.format_exc())
        return original_finish(*args, **kwargs)
    monkeypatch.setattr(controller, '_finish', finish_with_exception)
    return errors


LAUNCHER = r'''import json,os,pathlib,sys,uuid
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
task=json.loads((work/'science-task.json').read_text())
role=task['role']
assert 'SOURCE_SELECTED_OUTPUT_SENTINEL' not in json.dumps(task)
assert 'resume' not in args
if task.get('parents'):assert (work/'inputs/parents/001/conclusion.json').is_file()
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);thread=str(uuid.uuid4())
(home/'sessions').mkdir(parents=True,exist_ok=True)
(home/'sessions/original.jsonl').write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
(work/'work/new-session.json').write_text(json.dumps(dict(thread=thread,home=str(home),args=args)))
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
payload=dict(summary='Fresh child '+role+' finding',scientific_result='The exact inherited input was available.',
 limitations=['Synthetic native transport only'],implementation_selection=None)
if role=='H':payload.update(search_plan=None,next_action='analyze',route_reason='Assess the bounded child evidence')
if role=='A':payload.update(candidate_recommendation=None,selection_explanation=None,search_review=None,
 progress_assessment=None,decision_request=None,portfolio_review=None,next_action='finish',route_reason='The bounded child fixture is complete')
if role=='W':payload.update(mechanism_findings=None,stage_intent='review')
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=17,cached_input_tokens=2,output_tokens=5))),flush=True)
'''


@pytest.mark.process_e2e
def test_controller_runs_selected_a_with_frozen_prefix_after_parent_removal(tmp_path, monkeypatch):
    launcher = tmp_path / 'native-fixture.py'
    launcher.write_text(LAUNCHER)
    parent, _ = prepare(tmp_path, enabled=False, launcher=[sys.executable, str(launcher)])
    with controller._writer(parent):
        pass
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _, _, record = finish(parent, step, role, parents, action=action,
            text='SOURCE_SELECTED_OUTPUT_SENTINEL' if step == 2 else 'Exact H evidence before selected A.')
        outcome = c.read(record / 'native-outcome.json')
        c.write(record / 'native-outcome.json', dict(outcome, native_started=False))
    original_task = c.read(parent / 'project/nodes/002/science-task.json')
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=2)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance, expected_preview_id=preview['preview_id'], operation_id='execute-a-once')
    assert not (child / 'host-started.json').exists()
    shutil.rmtree(parent)
    shutil.rmtree(tmp_path / 'public')
    sealing_errors = []
    original_seal = c.seal_result
    def checked_seal(*args, **kwargs):
        try:
            return original_seal(*args, **kwargs)
        except Exception:
            sealing_errors.append(traceback.format_exc())
            raise
    monkeypatch.setattr(c, 'seal_result', checked_seal)
    monkeypatch.setattr(native, 'seal_result', checked_seal)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', '\n'.join(sealing_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    assert [path.name for path in (child / 'jobs').iterdir()] == ['002.json']
    job = c.read(child / 'jobs/002.json')
    assert job['role'] == 'A' and job['step'] == 2
    assert job['fork_source']['job']['path'] == 'jobs/002.json'
    view = c.read(child / 'project/nodes/002/science-task.json')
    expected_task = copy.deepcopy(original_task)
    expected_task['public_root'] = job['public_root']
    expected_task['source_restrictions'] = job['source_restrictions']
    assert view == expected_task
    assert 'SOURCE_SELECTED_OUTPUT_SENTINEL' not in json.dumps(view)
    assert c.validate(c.workspace(child, 2))['summary'] == 'Fresh child A finding'
    assert native.usage(child)['observed_tokens'] == 22
    session = c.read(child / 'project/nodes/002/work/new-session.json')
    assert str(child) in session['home'] and 'resume' not in session['args']


def routed_parent(tmp_path):
    launcher = tmp_path / 'native-fixture.py'
    launcher.write_text(LAUNCHER)
    parent, _ = prepare(tmp_path, enabled=False, launcher=[sys.executable, str(launcher)])
    with controller._writer(parent):
        pass
    rows = [(1, 'H', [], 'experiment'), (2, 'W', [1], 'wait'),
        (3, 'A', [1, 2], 'extract'), (4, 'E', [1, 2, 3], 'wait'),
        (5, 'A', [1, 2, 3, 4], 'finish')]
    for step, role, parents, action in rows:
        if role == 'E':
            c.build_job(parent, step, role, parents, instruction='Extract the exact original evidence',
                evidence_return=dict(role='A', step=3))
        _, _, record = finish(parent, step, role, parents, action=action)
        c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'), native_started=False))
    return parent


@pytest.mark.process_e2e
@pytest.mark.parametrize('selected_step,selected_role,local_roles', [
    (1, 'H', ['H', 'A']), (2, 'W', ['W', 'A']),
    (3, 'A', ['A']), (4, 'E', ['E', 'A'])])
def test_each_ordinary_role_starts_at_exact_source_and_routes_child_successors(tmp_path, monkeypatch, selected_step, selected_role, local_roles):
    parent = routed_parent(tmp_path)
    child = tmp_path / 'child'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(parent, selected_step, child, budget=allowance)
    fork_service.create(parent, selected_step, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='role-' + selected_role)
    shutil.rmtree(parent)
    errors = []
    original_validate = c.validate
    def checked_validate(*args, **kwargs):
        try:
            return original_validate(*args, **kwargs)
        except Exception:
            errors.append(traceback.format_exc())
            raise
    monkeypatch.setattr(c, 'validate', checked_validate)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', '\n'.join(errors) or json.dumps(result, ensure_ascii=False, indent=2)
    jobs = [c.read(path) for path in sorted((child / 'jobs').iterdir())]
    assert [job['role'] for job in jobs] == local_roles
    assert [job['step'] for job in jobs] == list(range(selected_step, selected_step + len(local_roles)))
    assert jobs[0]['fork_source']['job']['path'] == f'jobs/{selected_step:03d}.json'
    assert all('fork_source' not in job for job in jobs[1:])
    for job in jobs:
        assert c.validate(c.workspace(child, job['step']))['role'] == job['role']
        session = c.read(c.workspace(child, job['step']) / 'work/new-session.json')
        assert 'resume' not in session['args'] and str(child) in session['home']
    assert native.usage(child)['observed_tokens'] == 22 * len(local_roles)


@pytest.mark.process_e2e
def test_nested_fork_runs_with_two_original_identities_after_both_parents_disappear(tmp_path):
    parent = routed_parent(tmp_path)
    child, grandchild = tmp_path / 'child', tmp_path / 'grandchild'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=4)
    first = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=first['preview_id'], operation_id='first-generation')
    shutil.rmtree(parent)
    assert instance_control.continue_instance(child)['status'] == 'finish'
    child_identity = c.settings(child)['goal_id']
    child_session = c.read(c.workspace(child, 3) / 'work/new-session.json')['thread']
    second = fork_service.preview(child, 3, grandchild, budget=allowance)
    fork_service.create(child, 3, grandchild, budget=allowance,
        expected_preview_id=second['preview_id'], operation_id='second-generation')
    shutil.rmtree(child)
    shutil.rmtree(tmp_path / 'public')
    result = instance_control.continue_instance(grandchild)
    assert result['status'] == 'finish', json.dumps(result, ensure_ascii=False, indent=2)
    job = c.read(grandchild / 'jobs/003.json')
    assert job['fork_source']['job']['origin_identity']['goal_id'] == child_identity
    manifest = c.read(grandchild / 'host-runtime/fork/manifest.json')
    assert len({row['origin_identity']['goal_id'] for row in manifest['prefix_entries']}) == 2
    assert c.validate(c.workspace(grandchild, 3))['role'] == 'A'
    assert native.usage(grandchild)['observed_tokens'] == 22
    assert c.read(c.workspace(grandchild, 3) / 'work/new-session.json')['thread'] != child_session


@pytest.mark.process_e2e
def test_mult_attempt_source_restarts_logical_w_in_new_session_without_scratch(tmp_path):
    from scidiscovery.research_loop import budget, execution
    from test_worker_resume import REPAIR_FAKE, instance, wait_runner
    combined = ('import os,pathlib\n'
        'work=pathlib.Path(os.environ["THIN_HWA_WORKSPACE"])\n'
        'if (work/"work/output-kind").exists():\n exec(' + repr(REPAIR_FAKE) + ')\n'
        'else:\n exec(' + repr(LAUNCHER) + ')\n')
    parent = instance(tmp_path, fake=combined, output_repair=True, attempts=2)
    c.build_job(parent, 1, 'W', [])
    (c.workspace(parent, 1) / 'work/output-kind').write_text('badjson')
    c.write(parent / 'host-started.json', budget.clock_stamp())
    with controller._writer(parent):
        budget.transition(parent, 'advance', 'fixture original output repair')
        assert native.execute_native(parent, 1)['role'] == 'W'
        wait_runner(parent)
        c.build_job(parent, 2, 'A', [1])
        assert native.execute_native(parent, 2)['next_action'] == 'finish'
        budget.inactive(parent, 'fixture completed')
    assert len(execution.records(parent, 1)) == 2
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(parent, 1, child, budget=allowance)
    fork_service.create(parent, 1, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='restart-logical-w')
    shutil.rmtree(parent)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', json.dumps(result, ensure_ascii=False, indent=2)
    assert len(execution.records(child, 1)) == 1
    work = c.workspace(child, 1)
    assert not (work / 'work/output-kind').exists()
    assert not (work / 'work/completed-experiment').exists()
    fresh = c.read(work / 'work/new-session.json')
    assert fresh['thread'] != '11111111-2222-4333-8444-555555555555'
    assert 'resume' not in fresh['args']
    assert native.usage(child)['observed_tokens'] == 44


@pytest.mark.process_e2e
@pytest.mark.parametrize('repair_child_output', [False, True])
def test_selected_final_a_rebuilds_child_phase_and_never_opens_normal_route(tmp_path, science_errors, repair_child_output):
    from scidiscovery.research_loop import budget, execution
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the bounded conclusion and explicit missing W evidence.')
    launcher = tmp_path / 'native-fixture.py'
    program = LAUNCHER
    if repair_child_output:
        program = program.replace('args=sys.argv[1:];sys.stdin.read()', 'args=sys.argv[1:];prompt=sys.stdin.read()')
        program = program.replace("assert 'resume' not in args", "resume='resume' in args\nif resume:assert 'Repair only your final response' in prompt")
        program = program.replace("home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);thread=str(uuid.uuid4())",
            "home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME']);thread=(json.loads((work/'work/new-session.json').read_text())['thread'] if resume else str(uuid.uuid4()))\nif resume:assert thread in args")
        program += "\nif (work.parents[2]/'host-runtime/fork/manifest.json').exists() and not resume:\n pathlib.Path(args[args.index('--output-last-message')+1]).write_text('{bad JSON')\n"
    launcher.write_text(program)
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, [sys.executable, str(launcher)],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external',
        resume_policy=(dict(max_attempts=1, protocol='codex_exec_resume', usage_scope='turn', output_repair=True)
            if repair_child_output else None),
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=5) for role in 'HWAE'})
    _, _, record = finish(parent, 1, 'H', [], action='analyze')
    c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'), native_started=False))
    c.write(parent / 'host-started.json', budget.clock_stamp())
    with controller._writer(parent):
        result = controller._start_budget_closeout(parent, 1, 2, 'A', 'fixture bounded closeout')
        assert result['status'] == 'final_a_completed', json.dumps(result, ensure_ascii=False, indent=2)
        budget.inactive(parent, 'fixture final completed')
    original = c.read(parent / 'jobs/002.json')
    assert original['final_only'] and original['role'] == 'A'
    child = tmp_path / 'child'
    actions = 2 if repair_child_output else 1
    allowance = dict(max_tokens=100, max_seconds=30, max_steps=actions)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='new-final-original')
    shutil.rmtree(parent)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'final_a_completed', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    assert [path.name for path in (child / 'jobs').iterdir()] == ['002.json']
    job = c.read(child / 'jobs/002.json')
    assert job['final_only'] and job['role'] == 'A'
    state = budget.closeout_state(child)
    assert state['begin']['mode'] == 'final' and state['begin']['source_cutoff'] == 1
    assert state['begin']['first_step'] == 2 and state['actions'] == []
    assert len(execution.records(child, 2)) == actions
    assert native.usage(child)['observed_tokens'] == 22 * actions
    assert budget.used_steps(child) == actions
    if repair_child_output:
        first, repaired = execution.records(child, 2)
        assert c.read(repaired / 'native-intent.json')['session']['thread_id'] == c.read(first / 'native-outcome.json')['thread_id']
        assert c.read(repaired / 'native-intent.json')['output_repair']['raw_sha256'] == c.digest(first / 'science-output.json')
    assert budget.final_a_barrier(child)['step'] == 2
    again = instance_control.continue_instance(child)
    assert again['status'] == 'final_a_completed'
    assert native.usage(child)['observed_tokens'] == 22 * actions


@pytest.mark.process_e2e
@pytest.mark.parametrize('reuse_subject_author,nested', [
    pytest.param(False, False, id='False'), pytest.param(True, False, id='True'),
    pytest.param(False, True, id='nested')])
def test_selected_formal_review_keeps_neutral_input_and_checks_original_authors(tmp_path, monkeypatch, science_errors, reuse_subject_author, nested):
    from scidiscovery.research_loop import qualification as q
    import test_formal_review as original_review
    launcher_text = original_review.LAUNCHER.replace(
        "thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))",
        "thread=(os.environ['FORK_REVIEW_FORCE_THREAD'] if step==4 and os.environ.get('FORK_REVIEW_FORCE_THREAD') else str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(work))))")
    launcher_text = launcher_text.replace(
        "assert all(anchor.encode() not in path.read_bytes() for path in (work/'inputs').rglob('*') if path.is_file())",
        "assert all(anchor.encode() not in (work/name).read_bytes() for name in [task['review']['subject'],*task['global_inputs']])")
    monkeypatch.setattr(original_review, 'LAUNCHER', launcher_text)
    parent, _ = original_review.scripted(tmp_path, monkeypatch)
    result = controller.run(parent)
    assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    author_thread = c.read(parent / 'native-evidence/002/native-outcome.json')['thread_id']
    old_review_thread = c.read(parent / 'native-evidence/004/native-outcome.json')['thread_id']
    parent_identity = c.settings(parent)['goal_id']
    child = tmp_path / 'child'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(parent, 4, child, budget=allowance)
    fork_service.create(parent, 4, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='independent-review-' + str(reuse_subject_author))
    shutil.rmtree(parent)
    if reuse_subject_author:
        monkeypatch.setenv('FORK_REVIEW_FORCE_THREAD', author_thread)
        monkeypatch.setenv('REVIEW_REUSE_AUTHOR', '1')
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    job = c.read(child / 'jobs/004.json')
    assert job['purpose'] == 'formal_review' and job['formal_review']['origin_identity']
    task = c.read(c.workspace(child, 4) / 'science-task.json')
    assert 'review' in task and 'history' not in task and 'parents' not in task
    assert 'REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE' not in json.dumps(task)
    assert not (c.workspace(child, 4) / 'inputs/history').exists()
    seal = c.read(child / 'native-evidence/004/result-identity.json')
    proof = q._read(child, seal['formal_review'])
    assert proof['status'] == ('not_qualified' if reuse_subject_author else 'passed')
    assert proof['independence'] == ('overlap' if reuse_subject_author else 'independent')
    assert c.read(child / 'native-evidence/004/native-outcome.json')['thread_id'] != old_review_thread
    assert 'execution_authority' not in job
    if nested:
        child_identity = c.settings(child)['goal_id']
        child_thread = c.read(child / 'native-evidence/004/native-outcome.json')['thread_id']
        child_intent = c.read(child / 'native-evidence/004/native-intent.json')
        input_bytes = {name: (c.workspace(child, 4) / name).read_bytes()
            for name in [task['review']['subject'], *task['global_inputs']]}
        grandchild = tmp_path / 'grandchild'
        preview = fork_service.preview(child, 4, grandchild, budget=allowance)
        fork_service.create(child, 4, grandchild, budget=allowance,
            expected_preview_id=preview['preview_id'], operation_id='nested-independent-review')
        shutil.rmtree(child)
        assert not parent.exists()
        result = instance_control.continue_instance(grandchild)
        assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
        manifest = c.read(grandchild / 'host-runtime/fork/manifest.json')
        origins = {row['identity']['goal_id'] for row in manifest['origins'].values()}
        assert {parent_identity, child_identity} <= origins
        grandjob = c.read(grandchild / 'jobs/004.json')
        assert grandjob['fork_source']['job']['origin_identity']['goal_id'] == child_identity
        assert grandjob['purpose'] == 'formal_review' and 'execution_authority' not in grandjob
        grandtask = c.read(c.workspace(grandchild, 4) / 'science-task.json')
        assert grandtask['review'] == task['review']
        assert 'history' not in grandtask and 'parents' not in grandtask
        assert 'REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE' not in json.dumps(grandtask)
        assert not (c.workspace(grandchild, 4) / 'inputs/history').exists()
        for name, data in input_bytes.items():
            assert (c.workspace(grandchild, 4) / name).read_bytes() == data
        grandseal = c.read(grandchild / 'native-evidence/004/result-identity.json')
        grandproof = q._read(grandchild, grandseal['formal_review'])
        assert grandproof['status'] == 'passed' and grandproof['independence'] == 'independent'
        assert grandproof['authors'] == proof['authors']
        assert ['codex_exec', author_thread] in grandproof['authors']['participants']
        assert grandproof['subject'] == proof['subject']
        grandthread = c.read(grandchild / 'native-evidence/004/native-outcome.json')['thread_id']
        assert grandthread not in {author_thread, old_review_thread, child_thread}
        assert ['codex_exec', grandthread] in grandproof['reviewer']['participants']
        assert c.read(grandchild / 'native-evidence/004/native-intent.json')['task_sha256'] != child_intent['task_sha256']
        assert grandproof != proof and 'origin_identity' not in grandseal['formal_review']
        assert [path.name for path in sorted((grandchild / 'jobs').iterdir())] == ['004.json', '005.json']


@pytest.mark.process_e2e
@pytest.mark.parametrize('first_run_id',[1,10000])
def test_selected_engineering_repair_uses_new_local_receipt_and_declines_old_delivery(tmp_path, monkeypatch, science_errors, execution_registry,first_run_id):
    from scidiscovery.research_loop import budget, engineering, history
    import test_audit_engineering as original_repair
    import test_cov_delivery_lineage as managed_fixture
    original_scene=original_repair.managed_scene
    def declared_namespace(*args,**kwargs):
        return original_scene(*args,namespace_runs=tmp_path,**kwargs)
    monkeypatch.setattr(original_repair,'managed_scene',declared_namespace)
    targets = {name: tmp_path / 'delivery-target' / name for name in ('runs', 'submission')}
    for path in targets.values():
        path.mkdir(parents=True)
    original_descriptor = managed_fixture.descriptor
    def external_descriptor(*args, **kwargs):
        value = original_descriptor(*args, **kwargs)
        return dict(value, target={name: str(path) for name, path in targets.items()},
            source_roots=[str(tmp_path)],readonly_inputs_version=1)
    monkeypatch.setattr(managed_fixture, 'descriptor', external_descriptor)
    raw_parent=f'run-{first_run_id}'
    if first_run_id!=1:
        backend=original_repair.BACKEND.replace('n=len(rows)+1',f'n=len(rows)+{first_run_id}')
        monkeypatch.setattr(original_repair,'BACKEND',backend)
        monkeypatch.setattr(managed_fixture,'BACKEND',backend)
        original_finish=original_repair.finish
        def finish_original(*args,**kwargs):
            if kwargs.get('recommendation')=='run-1':kwargs['recommendation']=raw_parent
            return original_finish(*args,**kwargs)
        monkeypatch.setattr(original_repair,'finish',finish_original)
    monkeypatch.setenv('PYTHONPATH', str(Path(c.__file__).resolve().parents[2])
        + os.pathsep + os.environ.get('PYTHONPATH', ''))
    program = original_repair.NATIVE.replace(
        "assert json.loads(result.stdout)['candidate_id']=='run-3'",
        "assert json.loads(result.stdout)['candidate_id']==('run-1' if (root/'host-runtime/fork/manifest.json').exists() else 'run-3')")
    program = program.replace("assert task['role']=='A' and 'run-3' in json.dumps(task)", "assert task['role']=='A'")
    program = program.replace("result=subprocess.run([*base,'--parent-plan','run-1'",
        "wrong_parent=subprocess.run([*base,'--parent-plan','run-2','--change-intent','Wrong exact candidate'],capture_output=True,text=True);assert wrong_parent.returncode==2,(wrong_parent.stdout,wrong_parent.stderr)\n result=subprocess.run([*base,'--parent-plan','run-1'")
    program = program.replace("next_action='deliver',candidate_recommendation='run-3'",
        "next_action='deliver',candidate_recommendation=('run-1' if (root/'host-runtime/fork/manifest.json').exists() else 'run-3')")
    program=program.replace("'run-1'",repr(raw_parent)).replace("'run-2'",repr(f'run-{first_run_id+1}')).replace("'run-3'",repr(f'run-{first_run_id+2}'))
    monkeypatch.setattr(original_repair, 'NATIVE', program)
    parent, _ = original_repair.repair_scene(tmp_path, ordinary=True)
    frozen_adapter=c.settings(parent)['delivery_policy']['adapter']
    assert frozen_adapter['config']['namespace_runs']==str(tmp_path)
    assert frozen_adapter['source_roots']==[str(tmp_path)]
    for step in (1, 2, 3):
        outcome = parent / 'native-evidence' / f'{step:03d}' / 'native-outcome.json'
        c.write(outcome, dict(c.read(outcome), native_started=False))
    result = controller.run(parent)
    assert result['status'] == 'delivery_confirmed', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    parent_actions = budget.closeout_state(parent)['actions']
    parent_operations = {row['intent']['operation_id'] for row in parent_actions}
    parent_decisions = {row['intent']['decision']['job_sha256'] for row in parent_actions}
    target_marker = targets['runs'] / 'rootless-delivery-operation.json'
    parent_marker = target_marker.read_bytes()
    original = c.read(parent / 'jobs/004.json')
    assert original['engineering_repair']['candidate_id'] == raw_parent
    input_bytes = {name: (c.workspace(parent, 4) / name).read_bytes()
        for name in ('inputs/repair/source.py', 'inputs/repair/artifact')
        if (c.workspace(parent, 4) / name).exists()}
    assert 'inputs/repair/source.py' in input_bytes
    child = tmp_path / 'child'
    allowance = dict(max_tokens=100000, max_seconds=300, max_steps=12)
    preview = fork_service.preview(parent, 4, child, budget=allowance)
    fork_service.create(parent, 4, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='fresh-engineering-repair')
    assert budget.closeout_state(child)['actions'] == []
    assert target_marker.read_bytes() == parent_marker
    shutil.rmtree(parent)
    assert not parent.exists() and not (parent/'runs').exists()
    result = instance_control.continue_instance(child)
    if result['status']!='delivery_confirmed':
        directory=child/'host-delivery/leaf-output'
        for path in sorted(directory.glob('*.json'))[-4:]:
            receipt=c.read(path)
            science_errors.append(path.name+'\n'+json.dumps(receipt,ensure_ascii=False)[:2048])
            for stream in receipt.get('streams',{}).values():
                name=stream.get('path')
                if not isinstance(name,str) or Path(name).name!=name:continue
                saved=directory/name
                if saved.is_symlink() or not saved.is_file():continue
                with saved.open('rb') as source:
                    source.seek(max(0,saved.stat().st_size-2048))
                    science_errors.append(name+'\n'+source.read(2048).decode('utf-8',errors='replace'))
        science_errors.append(json.dumps(result,ensure_ascii=False,indent=2))
    assert result['status'] == 'delivery_confirmed', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    repair = c.read(child / 'jobs/004.json')
    assert repair['role'] == 'W' and repair['engineering_repair'] == original['engineering_repair']
    from scidiscovery.research_loop import candidate_refs, experiments
    configured=c.read(child/'native-evidence/004/experiment-config.json')
    expected=candidate_refs.in_instance(child,repair['engineering_repair']['source']['step'],
        original['engineering_repair']['candidate_id'])
    assert configured['engineering_repair']['candidate_id']==expected!=raw_parent
    assert (len(expected.encode())>80)==(first_run_id==10000)
    parents=[item for item in configured['parents'] if item['candidate_id']==expected]
    assert len(parents)==1
    retained=Path(parents[0]['model']['path'])
    assert retained.is_relative_to(child/'host-runtime/fork/inherited')
    assert c.digest(retained)==parents[0]['model']['sha256']
    assert retained.stat().st_size==parents[0]['model']['size_bytes']
    calls=experiments.Records(configured).calls()
    assert len(calls)==1
    assert experiments.Records(configured).accepted(calls[0].name)['parent']['candidate_id']==expected
    for name, data in input_bytes.items():
        assert (c.workspace(child, 4) / name).read_bytes() == data
    assert not (child / 'native-evidence/003/candidate-audit').exists()
    records = history.rebuild(child, 6, write_cache=False)
    old = [row for row in records if row['step'] == 2][0]['observation']['candidates']
    new = [row for row in records if row['step'] == 4][0]['observation']['candidates']
    assert old and new and not ({row['id'] for row in old} & {row['id'] for row in new})
    assert any(row['id'] == raw_parent for row in new)
    actions = budget.closeout_state(child)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    binding = actions[0]['intent']['binding']
    operation = binding['operation_id']
    assert operation not in parent_operations and binding['candidate_id'] == raw_parent
    final_step = budget.final_a_barrier(child)['step']
    assert final_step == 6
    decision = dict(step=final_step, job_sha256=c.digest(child / 'jobs/006.json'),
        raw_sha256=c.digest(child / 'native-evidence/006/science-output.json'),
        seal_sha256=c.digest(child / 'native-evidence/006/result-identity.json'))
    assert decision['job_sha256'] not in parent_decisions
    assert binding['decision'] == decision
    assert c.validate(c.workspace(child, final_step))['candidate_recommendation'] == raw_parent
    context = next(row for row in records if row['step'] == 4)['observation']['contexts']
    model = next(row for row in context if row['candidate_id'] == raw_parent)['model']
    artifact = binding['source']['objects']['artifact']
    assert (artifact['sha256'], artifact['size_bytes']) == (model['sha256'], model['size_bytes'])
    assert binding['source']['context']['row']['run_id'] == first_run_id
    assert all(row['intent']['binding'] == binding for row in actions)
    assert [row['outcome']['result']['status'] for row in actions] == ['confirmed', 'confirmed']
    audit_path = child / 'host-delivery/leaf-output' / (operation + '-audit-1.json')
    audit = c.read(audit_path)
    assert audit['outcome'] == 'exited' and audit['returncode'] == 0
    receipt = c.read(audit_path.parent / audit['streams']['stdout']['path'])
    assert {key: receipt[key] for key in ('status', 'operation_id', 'candidate_id', 'artifact_sha256')} == dict(
        status='audited', operation_id=operation, candidate_id=raw_parent, artifact_sha256=artifact['sha256'])
    marker = c.read(target_marker)
    assert marker['operation_id'] == operation and marker['candidate_id'] == raw_parent
    assert marker['artifact_sha256'] == c.digest(targets['submission'] / 'model.pkl') == artifact['sha256']
    accepted_actions = copy.deepcopy(actions)
    assert instance_control.continue_instance(child)['status'] == 'delivery_confirmed'
    assert budget.closeout_state(child)['actions'] == accepted_actions
    assert c.read(target_marker) == marker
    with pytest.raises(engineering.RepairUnavailable, match='exhausted'):
        engineering.admit(child, repair['engineering_repair'], 7)


@pytest.mark.process_e2e
def test_inherited_screen_opportunities_and_pending_w_investment_survive_new_budget(tmp_path, science_errors, execution_registry):
    from scidiscovery.research_loop import budget, search
    from test_breadth_science import a, findings, h, run, worker
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare the original P and Q mechanisms without resetting investment.')
    launcher = tmp_path / 'native-fixture.py'
    launcher.write_text(LAUNCHER)
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, [sys.executable, str(launcher)],
        max_tokens=100000, max_seconds=600, max_steps=16, model='no-model', reasoning_effort='high',
        search_policy=dict(search.DEFAULT_POLICY, initial_calls=1), portfolio_policy=dict(review_after_cycles=3))
    with controller._writer(parent):
        pass
    h(parent, labels=('P', 'Q'))
    _, config = worker(parent, tmp_path, 2, [1])
    for label in ('P', 'Q'):
        run(parent, config, label, client_path=tmp_path / 'config-2.json')
    finish(parent, 2, 'W', [1], mechanism_findings=findings('P', 'Q'))
    a(parent, 3, [1, 2], action='finish')
    for step in (1, 2, 3):
        path = parent / 'native-evidence' / f'{step:03d}' / 'native-outcome.json'
        c.write(path, dict(c.read(path), native_started=False))
    prefix = search.project(parent, 3)
    assert prefix['portfolio']['pending_w']['step'] == 2
    assert prefix['opportunity_counts'] == {'h001.m1': 1, 'h001.m2': 1}
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=1)
    preview = fork_service.preview(parent, 3, child, budget=allowance)
    fork_service.create(parent, 3, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='retain-scientific-investment')
    shutil.rmtree(parent)
    inherited = search.project(child, 3)
    assert inherited['opportunity_counts'] == prefix['opportunity_counts']
    assert inherited['portfolio']['pending_w']['step'] == 2
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    state = search.project(child, 4)
    assert state['opportunity_counts'] == prefix['opportunity_counts']
    assert state['portfolio']['invested'] == {'h001.m1': 1, 'h001.m2': 1}
    assert len(state['portfolio']['seen_w']) == 1 and state['portfolio']['pending_w'] is None
    assert budget.used_steps(child) == 1 and native.usage(child)['observed_tokens'] == 22


@pytest.mark.process_e2e
@pytest.mark.parametrize('start_selected', [False, True])
def test_operator_closeout_uses_visible_fork_science_and_reserves_selected_slot(tmp_path, science_errors, start_selected):
    from scidiscovery.research_loop import budget
    parent = routed_parent(tmp_path)
    child = tmp_path / 'child'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(parent, 3, child, budget=allowance)
    fork_service.create(parent, 3, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='operator-closeout-' + str(start_selected))
    shutil.rmtree(parent)
    if start_selected:
        result = instance_control.continue_instance(child)
        assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    result = controller.closeout(child)
    if not start_selected:
        assert result['status'] == 'exported_unsubmitted' and result['reason'] == 'original_pause_requires_release'
        assert (child / 'pause.request').exists()
        assert not (child / 'jobs/003.json').exists() and not (child / 'jobs/004.json').exists()
        assert not (child / 'native-evidence/004/native-intent.json').exists()
        assert native.usage(child)['observed_tokens'] == 0 and budget.used_steps(child) == 0
        assert 'final_overrun' not in result
        result = instance_control.continue_instance(child)
    assert result['status'] == 'final_a_completed', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    state = budget.closeout_state(child)
    assert state['begin']['source_cutoff'] == (3 if start_selected else 2)
    assert state['begin']['first_step'] == 4 and state['actions'] == []
    jobs = [c.read(path) for path in sorted((child / 'jobs').iterdir())]
    assert [job['step'] for job in jobs] == ([3, 4] if start_selected else [4])
    assert jobs[-1]['final_only'] and jobs[-1]['role'] == 'A'
    assert [row['step'] for row in jobs[-1]['history']['nodes']] == ([1, 2, 3] if start_selected else [1, 2])
    assert native.usage(child)['observed_tokens'] == 22 * len(jobs)
    assert budget.used_steps(child) == len(jobs)
    assert c.read(child / 'host-runtime/fork/bootstrap.json')['next_step'] == 3


@pytest.mark.process_e2e
def test_explicit_step_raise_resumes_after_fork_final_with_inherited_roles(tmp_path, science_errors):
    from scidiscovery.research_loop import budget
    parent = routed_parent(tmp_path)
    child = tmp_path / 'child'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=1)
    preview = fork_service.preview(parent, 4, child, budget=allowance)
    fork_service.create(parent, 4, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='normal-resume-after-final')
    shutil.rmtree(parent)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'final_a_completed', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    phase = budget.closeout_state(child)['begin']
    assert phase['source_cutoff'] == 4 and phase['first_step'] == 5
    assert phase['resume_cursor']['role'] == 'A'
    assert [ref['step'] for ref in phase['resume_cursor']['source_refs']] == [1, 2, 3, 4]
    final_job = (child / 'jobs/005.json').read_bytes()
    final_output = (child / 'native-evidence/005/science-output.json').read_bytes()
    assert instance_control.continue_instance(child)['status'] == 'final_a_completed'
    assert not (child / 'jobs/006.json').exists()
    assert budget.normal_resume(child) is None
    assert native.usage(child)['observed_tokens'] == 44
    budget.amend(child, max_steps=4,
        authorization_note='Record the bounded fixture operator decision.',
        authorization_statement='Increase this synthetic child action allowance to four and continue its frozen normal route.')
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', '\n'.join(science_errors) or json.dumps(result, ensure_ascii=False, indent=2)
    assert [path.name for path in sorted((child / 'jobs').iterdir())] == ['004.json', '005.json', '006.json']
    resumed = c.read(child / 'jobs/006.json')
    assert resumed['role'] == 'A' and not resumed.get('final_only')
    assert resumed['normal_resume']['seq'] == budget.normal_resume(child)['seq']
    assert [row['step'] for row in resumed['history']['nodes']] == [1, 2, 3, 4]
    assert (child / 'jobs/005.json').read_bytes() == final_job
    assert (child / 'native-evidence/005/science-output.json').read_bytes() == final_output
    assert native.usage(child)['observed_tokens'] == 66 and budget.used_steps(child) == 3

