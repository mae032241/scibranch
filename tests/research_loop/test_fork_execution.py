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
        search_policy=None, experiment_mode='external')
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


def routed_parent(tmp_path):
    launcher = tmp_path / 'native-fixture.py'
    launcher.write_text(LAUNCHER)
    parent, _ = prepare(tmp_path, enabled=False, launcher=[sys.executable, str(launcher)])
    with controller._writer(parent):
        pass
    finish(parent, 1, 'W', [], stage_intent='handoff')
    finish(parent, 2, 'W', [1], stage_intent='handoff')
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
    from scidiscovery.research_loop import accepted_budget, handoff_state, originals
    owner = originals.node(child, 1)
    with pytest.raises(FileNotFoundError) as refused:
        handoff_state.source_facts(child, 1, owner=owner, head=accepted_budget.read(child))
    assert Path(refused.value.filename) == original
    assert not (child / 'native-evidence/002/native-intent.json').exists()
    assert native.usage(child)['observed_tokens'] == 0


@pytest.mark.process_e2e
def test_mult_attempt_source_restarts_logical_w_in_new_session_without_scratch(tmp_path, monkeypatch):
    """Execute the selected W component without an unrelated ordinary A tail."""
    from scidiscovery.research_loop import budget, execution, fork_runtime
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
        search_policy=None, experiment_mode='external',
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn', output_repair=True),
        role_profiles={'W': dict(subagents=False)},
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
    with controller._writer(child):
        fork_runtime.build_selected_job(child, 1)
        (child / 'pause.request').unlink(missing_ok=True)
        started = child / 'host-started.json'
        if not started.exists():
            c.write(started, budget.clock_stamp())
        budget.transition(child, 'advance', 'Explicit selected W component')
        result = native.execute_native(child, 1)
        budget.inactive(child, 'Selected W original retained')
    assert result['role'] == 'W'
    assert len(execution.records(child, 1)) == 1
    work = c.workspace(child, 1)
    assert not (work / 'work/output-kind').exists()
    assert not (work / 'work/unretained-scratch').exists()
    fresh = c.read(work / 'work/new-session.json')
    assert fresh['thread'] != original_thread
    assert 'resume' not in fresh['args']
    assert native.usage(child)['observed_tokens'] == 22


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
        search_policy=None, experiment_mode='external',
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
    request = original_review.exact_request(parent, 3, 2, 'model.txt',
        'Review the exact inherited subject with neutral scientific inputs.')
    original_review.formal_job(parent, request, 2)
    native.execute_native(parent, 4)

    def execute_selected_review(root):
        from scidiscovery.research_loop import budget, fork_runtime
        with controller._writer(root):
            fork_runtime.build_selected_job(root, 4)
            (root / 'pause.request').unlink(missing_ok=True)
            started = root / 'host-started.json'
            if not started.exists():
                c.write(started, budget.clock_stamp())
            budget.transition(root, 'advance', 'Explicit selected formal review component')
            native.execute_native(root, 4)
            budget.inactive(root, 'Selected formal review original retained')
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
    execute_selected_review(child)
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
        execute_selected_review(grandchild)
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
        assert [path.name for path in sorted((grandchild / 'jobs').iterdir())] == ['004.json']
