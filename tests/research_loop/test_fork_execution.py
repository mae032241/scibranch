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
if role=='W':payload.update(mechanism_findings=None,stage_intent='decision')
text=json.dumps(payload)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(text)
print(json.dumps(dict(type='item.completed',item=dict(type='agent_message',phase='final_answer',
 text=pathlib.Path(args[args.index('--output-last-message')+1]).read_text()))),flush=True)
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=17,cached_input_tokens=2,output_tokens=5))),flush=True)
'''


def prepare(tmp_path, *, enabled=False, launcher):
    """Freeze legacy role routing deliberately, not the current v3 default."""
    assert enabled is False
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Assess one exact declared implementation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, launcher, max_tokens=1000,
        max_seconds=60, max_steps=6, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', research_strategy_version=None)
    return root, public


def app_server_launcher(*, repair_source=False, repair_child=False, science=None):
    """Use the maintained real RPC/hook fixture, with this file's science data.

    No output parser, writer, seal or capability flag is replaced. The helper
    receives native metadata through the same declared hooks as production.
    """
    from test_formal_review import LAUNCHER as rpc_launcher
    begin = rpc_launcher.index("if 'review' in task:")
    end = rpc_launcher.index("home=pathlib.Path(os.environ['CODEX_HOME'])")
    if science is None:
        science = LAUNCHER[LAUNCHER.index('\npayload=dict(summary=') + 1:LAUNCHER.index('text=json.dumps(payload)')]
    science = "role=task['role']\n" + science.replace('payload', 'value')
    science += "assert 'SOURCE_SELECTED_OUTPUT_SENTINEL' not in json.dumps(task)\n"
    science += "text=json.dumps(value)\n"
    science += (
        "repair_source=" + repr(repair_source) + "\n"
        "repair_child=" + repr(repair_child) + "\n"
        "repair=(repair_source and (work/'work/output-kind').exists() or "
        "repair_child and (work.parents[2]/'host-runtime/fork/manifest.json').exists())\n"
        "if repair and not resumed:text='{bad JSON'\n"
        "if resumed:assert 'Fix diagnosed JSON/layout only' in prompt\n")
    program = rpc_launcher[:begin] + science + rpc_launcher[end:]
    program = program.replace("start=request['params']", "start=request['params'];resumed=request['method']=='thread/resume'")
    program = program.replace(
        "thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))",
        "thread=start['threadId'] if resumed else str(uuid.uuid4())")
    program = program.replace("transcript=sessions/(thread+'.jsonl')",
        "transcript=sessions/(thread+'.jsonl')\n"
        "(work/'work/new-session.json').write_text(json.dumps(dict(thread=thread,home=str(home),args=args)))")
    program = program.replace('input_tokens=10,cached_input_tokens=0,output_tokens=5',
        'input_tokens=17,cached_input_tokens=2,output_tokens=5')
    program = program.replace('totalTokens=15', 'totalTokens=22')
    program = program.replace("text=json.dumps(value),phase='final_answer'", "text=text,phase='final_answer'")
    program = ("import pathlib,sys,traceback\n"
        "def fixture_failure(kind,value,trace):\n"
        " pathlib.Path(__file__).with_suffix('.failure.log').write_text(''.join(traceback.format_exception(kind,value,trace)))\n"
        " sys.__excepthook__(kind,value,trace)\n"
        "sys.excepthook=fixture_failure\n" + program)
    compile(program, '<fork native fixture>', 'exec')
    return program


def native_diagnostics(root, step):
    """Bounded, exact synthetic-native receipts; never discover a tree."""
    from scidiscovery.research_loop import execution
    record = execution.record(root, step)
    values = []
    for name in ('native-outcome.json', 'native.stderr', 'runner.stderr'):
        path = record / name
        if path.is_file():
            with path.open('rb') as stream:
                stream.seek(0, 2)
                stream.seek(max(0, stream.tell() - 8192))
                values.append(name + ':\n' + stream.read().decode(errors='replace'))
    failure = root.parent / 'codex.failure.log'
    if failure.is_file():
        with failure.open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 8192))
            values.append(stream.read().decode(errors='replace'))
    return '\n'.join(values)


def frozen_history_steps(root, job):
    """Follow this job's exact accepted navigation, checking each source owner."""
    from scidiscovery.research_loop import accepted_prefix, history, originals
    prefix = accepted_prefix.for_job(job)
    public = accepted_prefix.visibility(root, prefix)
    reference = prefix['tip']['navigation'] if prefix['tip'] is not None else None
    rows = []
    seen = set()
    while reference is not None:
        path = reference['path']
        assert path not in seen
        seen.add(path)
        row = c.read(c.regular(public / originals.relative(path)))
        member = accepted_prefix.member(root, prefix, row['step'])
        owner = originals.for_origin(root, member['origin_identity'], step=row['step'])
        assert member['navigation']['path'] == path
        assert member['seal_ref'] == history.source_seal_ref(owner)
        assert owner.origin_identity == member['origin_identity']
        rows.append(row['step'])
        reference = dict(path=row['previous']) if row['previous'] is not None else None
    assert len(rows) == prefix['count']
    return list(reversed(rows))


def remove_parent_controls(parent, child):
    """Remove recorded control originals/homes, never direct scientific inputs.

    The manifest is the exact finite inventory; no workspace discovery or
    blanket retention/copy of scientific bytes is involved.
    """
    manifest = c.read(child / 'host-runtime/fork/manifest.json')
    origins = {json.dumps(row['identity'], sort_keys=True): Path(row['original_root'])
        for row in manifest['origins'].values()}
    entries = [row for row in manifest['entries']
        if origins[json.dumps(row['origin_identity'], sort_keys=True)] == parent]
    science = [parent / row['path'] for row in entries
        if row['visibility'] in {'scientific_input', 'historical_science'}]
    identities = {path: path.stat() for path in science if path.is_file()}
    homes = set()
    for row in entries:
        path = parent / row['path']
        if row['visibility'] in {'host_proof', 'effect_only'} and path.name == 'native-outcome.json' and path.is_file():
            home = (c.read(path).get('native_identity') or {}).get('home')
            if home is not None:
                home = Path(home)
                assert home.is_absolute() and home.is_relative_to(parent.parent)
                assert not any(path.is_relative_to(home) for path in identities)
                homes.add(home)
    for row in entries:
        if row['visibility'] in {'host_proof', 'effect_only'}:
            (parent / row['path']).unlink(missing_ok=True)
    (parent / 'settings.json').unlink(missing_ok=True)
    for home in homes:
        if home.exists():
            shutil.rmtree(home)
    assert not (parent / 'settings.json').exists()
    for path, before in identities.items():
        after = path.stat()
        assert (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) == (
            before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)


@pytest.mark.process_e2e
def test_controller_runs_selected_a_with_frozen_prefix_after_parent_control_removal(tmp_path, monkeypatch):
    launcher = tmp_path / 'native-fixture.py'
    launcher.write_text(LAUNCHER)
    parent, _ = prepare(tmp_path, enabled=False, launcher=[sys.executable, str(launcher)])
    with controller._writer(parent):
        pass
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _, _, record = finish(parent, step, role, parents, action=action,
            text='SOURCE_SELECTED_OUTPUT_SENTINEL' if step == 2 else 'Exact H evidence before selected A.')
        assert c.read(record / 'native-outcome.json')['native_started'] is False
    original_task = c.read(parent / 'project/nodes/002/science-task.json')
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=2)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance, expected_preview_id=preview['preview_id'], operation_id='execute-a-once')
    assert not (child / 'host-started.json').exists()
    remove_parent_controls(parent, child)
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
        assert c.read(record / 'native-outcome.json')['native_started'] is False
    return parent


@pytest.mark.process_e2e
def test_missing_direct_parent_science_refuses_read_before_native_submission(tmp_path):
    parent = routed_parent(tmp_path)
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=2)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='missing-direct-science')
    remove_parent_controls(parent, child)
    original = parent / 'native-evidence/001/science-output.json'
    assert original.is_file()
    original.unlink()
    # Fork metadata preserves the exact missing-input binding. Acceptance of
    # that metadata is not evidence that a scientific original can be read.
    from scidiscovery.research_loop import handoff_state, originals
    owner = originals.node(child, 1)
    with pytest.raises(FileNotFoundError) as refused:
        handoff_state.source_facts(child, 1, owner=owner)
    assert Path(refused.value.filename) == original
    assert not (child / 'native-evidence/002/native-intent.json').exists()
    assert native.usage(child)['observed_tokens'] == 0


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
    remove_parent_controls(parent, child)
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
def test_nested_fork_runs_with_two_original_identities_after_both_parent_controls_disappear(tmp_path):
    parent = routed_parent(tmp_path)
    child, grandchild = tmp_path / 'child', tmp_path / 'grandchild'
    allowance = dict(max_tokens=2000, max_seconds=60, max_steps=4)
    first = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=first['preview_id'], operation_id='first-generation')
    remove_parent_controls(parent, child)
    assert instance_control.continue_instance(child)['status'] == 'finish'
    child_identity = c.settings(child)['goal_id']
    child_session = c.read(c.workspace(child, 3) / 'work/new-session.json')['thread']
    second = fork_service.preview(child, 3, grandchild, budget=allowance)
    fork_service.create(child, 3, grandchild, budget=allowance,
        expected_preview_id=second['preview_id'], operation_id='second-generation')
    remove_parent_controls(child, grandchild)
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
def test_mult_attempt_source_restarts_logical_w_in_new_session_without_scratch(tmp_path, monkeypatch):
    from scidiscovery.research_loop import budget, execution
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact W science without restoring its scratch work.')
    launcher = tmp_path / 'codex'
    launcher.write_text('#!' + sys.executable + '\n' + app_server_launcher(repair_source=True))
    launcher.chmod(0o700)
    home = tmp_path / 'actual-native-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, [str(launcher)],
        max_tokens=1000, max_seconds=60, max_steps=4, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', research_strategy_version=None,
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn', output_repair=True),
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=5) for role in 'HWAE'})
    c.build_job(parent, 1, 'W', [])
    (c.workspace(parent, 1) / 'work/output-kind').write_text('badjson')
    c.write(parent / 'host-started.json', budget.clock_stamp())
    with controller._writer(parent):
        budget.transition(parent, 'advance', 'fixture original output repair')
        try:
            assert native.execute_native(parent, 1)['role'] == 'W'
        except Exception as error:
            raise AssertionError(native_diagnostics(parent, 1)) from error
        c.build_job(parent, 2, 'A', [1])
        assert native.execute_native(parent, 2)['next_action'] == 'finish'
        budget.inactive(parent, 'fixture completed')
    assert len(execution.records(parent, 1)) == 2
    original_thread = c.read(c.workspace(parent, 1) / 'work/new-session.json')['thread']
    (c.workspace(parent, 1) / 'work/unretained-scratch').write_text('Scratch only; not scientific evidence.')
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=4)
    preview = fork_service.preview(parent, 1, child, budget=allowance)
    fork_service.create(parent, 1, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='restart-logical-w')
    remove_parent_controls(parent, child)
    result = instance_control.continue_instance(child)
    assert result['status'] == 'finish', json.dumps(result, ensure_ascii=False, indent=2)
    assert len(execution.records(child, 1)) == 1
    work = c.workspace(child, 1)
    assert not (work / 'work/output-kind').exists()
    assert not (work / 'work/unretained-scratch').exists()
    fresh = c.read(work / 'work/new-session.json')
    assert fresh['thread'] != original_thread
    assert 'resume' not in fresh['args']
    assert native.usage(child)['observed_tokens'] == 44


@pytest.mark.process_e2e
@pytest.mark.parametrize('repair_child_output', [False, True])
def test_selected_final_a_rebuilds_child_phase_and_never_opens_normal_route(tmp_path, monkeypatch, science_errors, repair_child_output):
    from scidiscovery.research_loop import budget, execution
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the bounded conclusion and explicit missing W evidence.')
    launcher = tmp_path / 'codex'
    launcher.write_text('#!' + sys.executable + '\n' + app_server_launcher(repair_child=repair_child_output))
    launcher.chmod(0o700)
    home = tmp_path / 'actual-native-home'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, [str(launcher)],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', research_strategy_version=None,
        resume_policy=(dict(max_attempts=1, protocol='codex_exec_resume', usage_scope='turn', output_repair=True)
            if repair_child_output else None),
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=5) for role in 'HWAE'})
    _, _, record = finish(parent, 1, 'H', [], action='analyze')
    assert c.read(record / 'native-outcome.json')['native_started'] is False
    c.write(parent / 'host-started.json', budget.clock_stamp())
    with controller._writer(parent):
        result = controller._start_budget_closeout(parent, 1, 2, 'A', 'fixture bounded closeout')
        assert result['status'] == 'final_a_completed', json.dumps(result, ensure_ascii=False, indent=2) + '\n' + native_diagnostics(parent, 2)
        budget.inactive(parent, 'fixture final completed')
    original = c.read(parent / 'jobs/002.json')
    assert original['final_only'] and original['role'] == 'A'
    child = tmp_path / 'child'
    actions = 2 if repair_child_output else 1
    allowance = dict(max_tokens=100, max_seconds=30, max_steps=actions)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='new-final-original')
    remove_parent_controls(parent, child)
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
        assert c.read(repaired / 'native-intent.json')['output_repair']['raw_ref'] == c.file_ref(first / 'science-output.json')
    assert budget.final_a_barrier(child)['step'] == 2
    again = instance_control.continue_instance(child)
    assert again['status'] == 'final_a_completed'
    assert native.usage(child)['observed_tokens'] == 22 * actions


@pytest.mark.process_e2e
@pytest.mark.parametrize('reuse_subject_author,nested', [
    pytest.param(False, False, id='False'), pytest.param(True, False, id='True'),
    pytest.param(False, True, id='nested')])
def test_selected_formal_review_keeps_neutral_input_and_checks_original_authors(tmp_path, monkeypatch, science_errors, reuse_subject_author, nested):
    from scidiscovery.research_loop import handoff_state, originals, qualification as q
    import test_formal_review as original_review
    launcher_text = original_review.LAUNCHER.replace(
        "thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))",
        "thread=(os.environ['FORK_REVIEW_FORCE_THREAD'] if step==4 and os.environ.get('FORK_REVIEW_FORCE_THREAD') else str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(work))))")
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
    remove_parent_controls(parent, child)
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
        inputs = handoff_state.material_inputs(child, job, sources={},
            source=originals.node(child, 4))
        materials = {row['path']: row for row in [*inputs['direct_materials'], *inputs['global_materials']]}
        input_bytes = {name: originals.material_path(child, materials[name]).read_bytes()
            for name in [task['review']['subject'], *task['global_inputs']]}
        grandchild = tmp_path / 'grandchild'
        preview = fork_service.preview(child, 4, grandchild, budget=allowance)
        fork_service.create(child, 4, grandchild, budget=allowance,
            expected_preview_id=preview['preview_id'], operation_id='nested-independent-review')
        remove_parent_controls(child, grandchild)
        assert not (parent / 'settings.json').exists()
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
        grandinputs = handoff_state.material_inputs(grandchild, grandjob, sources={},
            source=originals.node(grandchild, 4))
        grandmaterials = {row['path']: row for row in [*grandinputs['direct_materials'], *grandinputs['global_materials']]}
        for name, data in input_bytes.items():
            assert originals.material_path(grandchild, grandmaterials[name]).read_bytes() == data
        grandseal = c.read(grandchild / 'native-evidence/004/result-identity.json')
        grandproof = q._read(grandchild, grandseal['formal_review'])
        assert grandproof['status'] == 'passed' and grandproof['independence'] == 'independent'
        assert grandproof['authors'] == proof['authors']
        assert ['codex_exec', author_thread] in grandproof['authors']['participants']
        assert grandproof['subject'] == proof['subject']
        grandthread = c.read(grandchild / 'native-evidence/004/native-outcome.json')['thread_id']
        assert grandthread not in {author_thread, old_review_thread, child_thread}
        assert ['codex_exec', grandthread] in grandproof['reviewer']['participants']
        assert c.read(grandchild / 'native-evidence/004/native-intent.json')['task_ref'] != child_intent['task_ref']
        assert grandproof != proof and 'origin_identity' not in grandseal['formal_review']
        assert [path.name for path in sorted((grandchild / 'jobs').iterdir())] == ['004.json', '005.json']


def test_selected_w_cov_default_namespace_refuses_from_owner_facts_without_changing_fork_inputs(tmp_path, monkeypatch, execution_registry):
    """Mechanical source-unit guard, not dispatch or installed namespace coverage.

    Cov supports frozen code directories, but not the default scientific
    subprocess namespace. Only the kernel capability prerequisite is substituted
    to reach that executor guard; no installed namespace qualification follows.
    Command/cwd below are structural binding fixtures, not an accepted Cov call.
    No dispatch, review or replay is requested, and an ordinary W handoff is not
    promoted into a qualified Cov original. This does not replace the existing
    managed-Cov end-to-end positives.
    """
    import subprocess
    from cov_execution import lineage as cov_lineage, receipt as cov_receipt
    from cov_execution.experiments import CovExecution
    from scidiscovery.research_loop import accepted_budget, budget, execution
    from scidiscovery.research_loop import experiment_client, experiments, fork_runtime, originals
    from scidiscovery.research_loop import native_execution, shared_input_views
    from scidiscovery.research_loop.local_experiments import LocalExecution
    from scidiscovery.research_loop import worker_execution_bridge as bridge
    from test_worker_execution_hash_free import PUBLIC_CLI, PUBLIC_RUNNER

    launcher = tmp_path / 'never-launch.py'
    launcher.write_text('raise AssertionError("No native participant may start")\n')
    parent, public = prepare(tmp_path, launcher=[sys.executable, str(launcher)])
    finish(parent, 1, 'H', [], action='experiment')
    finish(parent, 2, 'W', [1], text='No qualified Cov experiment was available.')
    finish(parent, 3, 'A', [1, 2], action='finish')
    original_job = c.read(parent / 'jobs/002.json')
    original_task = c.read(c.workspace(parent, 2) / 'science-task.json')
    with controller._writer(parent):
        pass
    child = tmp_path / 'child'
    allowance = dict(max_tokens=1000, max_seconds=60, max_steps=1)
    preview = fork_service.preview(parent, 2, child, budget=allowance)
    fork_service.create(parent, 2, child, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='unsupported-cov-default-namespace')
    remove_parent_controls(parent, child)
    job = fork_runtime.build_selected_job(child, 2)
    work = c.workspace(child, 2)
    assert job['step'] == original_job['step'] == 2 and job['role'] == 'W'
    assert job['fork_source']['job']['path'] == 'jobs/002.json'
    assert job['fork_source']['job']['origin_identity'] == originals.selected_source(child).origin_identity
    assert job['direct_refs'] == original_job['direct_refs']
    assert Path(job['public_root']) == public
    assert 'execution_authority' not in job and 'execution_qualifications' not in job
    assert frozen_history_steps(child, job) == [1]
    expected_task = copy.deepcopy(original_task)
    expected_task['public_root'] = job['public_root']
    expected_task['source_restrictions'] = job['source_restrictions']
    assert c.read(work / 'science-task.json') == expected_task
    source_owner = originals.selected_source(child)
    assert originals.source_job(source_owner) == original_job
    inputs = [originals.resolve_ref(originals.node(child, 1),
        dict(path='native-evidence/001/science-output.json'))]

    record = execution.origin(child, 2)
    accepted_budget.accept_native(child, 2, record,
        dict(task_ref=c.record_ref(child / 'jobs/002.json')))
    harness = public / 'harness'
    harness.mkdir()
    (harness / 'exp_cli.py').write_text(PUBLIC_CLI)
    (harness / 'runner.py').write_text(PUBLIC_RUNNER)
    client = work / 'work/experiment-config.json'
    cov_lineage.configure(child, 2, [sys.executable, str(harness / 'exp_cli.py')],
        child / 'runs', client, receipt_channel='public_runner_main')
    config = experiment_client.resolve(client, c.read(client))
    assert experiment_client.root_for_configuration(config) == child
    assert config['execution_interface']['executor'] == 'cov_execution:experiment'
    assert experiment_client.supports_frozen_code_directory(config['execution_interface'])
    code = work / 'code'
    code.mkdir(exist_ok=True)
    source = code / 'refused.py'
    source.write_text('raise AssertionError("Cov namespace fixture must never execute")\n')
    # Only this structural prefix is consumed before the executor refusal.
    # No accepted.json, call ID, code version or execution permission is invented.
    command = [*config['run_argv'], 'run', '--script', str(source)]
    launch_bindings = dict(command=list(command), cwd=None)
    value = dict(workspace=str(work))
    source_facts = dict(root=child, work=work, target=work, job=job)
    frozen_paths = [*inputs, child / 'jobs/002.json', work / 'science-task.json',
        child / 'host-runtime/fork/manifest.json', client, record / 'experiment-config.json',
        record / 'native-intent.json', public / 'objective.md', source]
    saved_inputs = {path: path.read_bytes() for path in frozen_paths}
    records = experiments.Records(config)
    assert records.calls() == []
    native_records = execution.records(child, 2)
    used = budget.used_steps(child)
    observed_tokens = native.usage(child)['observed_tokens']
    head = (child / accepted_budget.HEAD).read_bytes()
    probes = []
    def kernel_prerequisite(*, context):
        # Source-only precondition, not evidence of installed readonly mounts.
        context.remaining_seconds()
        probes.append(context)
    monkeypatch.setattr(shared_input_views, 'ensure_capability', kernel_prerequisite)
    effects = []
    def forbidden(*args, **kwargs):
        effects.append((args, kwargs))
        pytest.fail('Namespace guard unexpectedly requested preparation, execution or a receipt')
    monkeypatch.setattr(LocalExecution, 'prepare', forbidden)
    monkeypatch.setattr(LocalExecution, 'submit', forbidden)
    monkeypatch.setattr(CovExecution, 'acceptance_metadata', forbidden)
    monkeypatch.setattr(CovExecution, 'replay_check', forbidden)
    monkeypatch.setattr(bridge, 'local_process_launch', forbidden)
    monkeypatch.setattr(native, 'execute_native', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(cov_receipt, 'publish', forbidden)
    for attempt in range(2):
        # Watch acquisition only within the consumer, not the owner/snapshots.
        with monkeypatch.context() as consumer:
            consumer.setattr(c, 'read', forbidden)
            consumer.setattr(c, 'read_job', forbidden)
            consumer.setattr(experiments, 'read', forbidden)
            consumer.setattr(experiments, 'proof', forbidden)
            consumer.setattr(Path, 'open', forbidden)
            consumer.setattr(Path, 'stat', forbidden)
            with pytest.raises(experiments.UnsupportedExecution,
                    match='^Selected executor has no default scientific subprocess namespace$'):
                native_execution._launch_from_facts(value, config, launch_bindings, command, None,
                    public=Path(job['public_root']), root=child, source_facts=source_facts,
                    selected_code=[], material_sources={}, projection={})
        assert len(probes) == attempt + 1
        assert records.calls() == []
        assert execution.records(child, 2) == native_records
        assert effects == []
        assert (child / accepted_budget.HEAD).read_bytes() == head
        assert budget.used_steps(child) == used
        assert native.usage(child)['observed_tokens'] == observed_tokens
        assert budget.closeout_state(child)['actions'] == []
        assert experiment_client.resolve(client, c.read(client)) == config
        assert all(path.read_bytes() == data for path, data in saved_inputs.items())
    assert not (record / 'result-identity.json').exists()
    assert not (child / 'runs/results.jsonl').exists()
    assert not (child / 'submission/model.pkl').exists()


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
    remove_parent_controls(parent, child)
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
    assert frozen_history_steps(child, jobs[-1]) == ([1, 2, 3] if start_selected else [1, 2])
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
    remove_parent_controls(parent, child)
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
    assert frozen_history_steps(child, resumed) == [1, 2, 3, 4, 5]
    assert (child / 'jobs/005.json').read_bytes() == final_job
    assert (child / 'native-evidence/005/science-output.json').read_bytes() == final_output
    assert native.usage(child)['observed_tokens'] == 66 and budget.used_steps(child) == 3

