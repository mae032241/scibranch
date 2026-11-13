"""Real CLI/subprocess handoff, with a local deterministic native stand-in."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import contracts, controller, execution, native, runtime
from scidiscovery.research_loop.cli import main, status
from execution_fixtures import execution_registry, remote_registry


# Retain the real CLI/runner reproduction shape from the local defect audit.
# This launcher supplies transport evidence only; no model or solver runs.
TERMINAL_LAUNCHER = r'''import json, os, pathlib, sys
mode = sys.argv[1]
args = sys.argv[2:]
assert args[0] == 'exec'
sys.stdin.read()
work = pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
task = json.loads((work / 'science-task.json').read_text())
role = task['role']
payload = {}
payload.update(summary='Local transport reproduction ' + role,
    scientific_result='Synthetic transport evidence only; no scientific finding.',
    limitations=['No model, API, remote service or solver was invoked.'],
    implementation_selection=None,
    next_action='analyze' if role == 'H' else 'finish',
    route_reason='Exercise the immediate successor of the retained H output.')
pending = pathlib.Path(args[args.index('--output-last-message') + 1])
if mode != 'late-output':
    pending.write_text(json.dumps(payload))
(work / 'work/launcher-payload.json').write_text(json.dumps(payload))
with (work / 'work/launcher-count.txt').open('a') as stream:
    stream.write('launch\n')
def event(kind, **fields):
    print(json.dumps(dict(type=kind, **fields)), flush=True)
event('thread.started', thread_id='local-reproduction-' + role)
if role == 'H' and mode == 'failed-before-completed':
    event('turn.failed', error=dict(message='Synthetic earlier failure'))
if role != 'H' or mode != 'missing-completed':
    event('turn.completed', usage=dict(input_tokens=12, cached_input_tokens=0, output_tokens=3))
if role == 'H':
    if mode in {'new-turn', 'failed-turn'}:
        event('turn.started')
        if mode == 'new-turn':
            event('item.started', item=dict(id='unfinished-item', type='agent_message', text='Unfinished new activity'))
        else:
            event('turn.failed', error=dict(message='Synthetic later turn failed'))
    elif mode == 'new-item':
        event('item.completed', item=dict(id='late-item', type='agent_message', text='Later output'))
    elif mode == 'new-thread':
        event('thread.started', thread_id='another-thread')
    elif mode == 'error':
        event('error', message='Synthetic transport error')
    elif mode == 'duplicate-completed':
        event('turn.completed', usage=dict(input_tokens=12, cached_input_tokens=0, output_tokens=3))
    elif mode == 'duplicate-unreported':
        event('turn.completed')
    elif mode == 'unterminated-new-turn':
        sys.stdout.write(json.dumps(dict(type='turn.started')))
        sys.stdout.flush()
    elif mode == 'nonzero-exit':
        sys.exit(7)
if mode == 'late-output':
    pending.write_text(json.dumps(payload))
'''


def _terminal_cli(root, *arguments):
    environment = dict(os.environ)
    environment['PYTHONPATH'] = os.pathsep.join(dict.fromkeys(
        [str(Path(__file__).parents[2] / 'src'), *map(str, sys.path), environment.get('PYTHONPATH', '')]))
    process = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli',
        *map(str, arguments), '--root', str(root)], env=environment,
        capture_output=True, text=True, timeout=25)
    assert process.stdout.strip(), process.stderr
    return process, json.loads(process.stdout.splitlines()[-1])


def _terminal_instance(tmp_path, mode):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the exact original transport result and its uncertainty.')
    launcher = tmp_path / 'local-native.py'
    launcher.write_text(TERMINAL_LAUNCHER)
    disabled_search = tmp_path / 'disabled-search.json'
    disabled_search.write_text('null')
    root = tmp_path / 'instance'
    process, result = _terminal_cli(root, 'prepare', '--objective', objective, '--public-root', public,
        '--native-launcher-json', json.dumps([sys.executable, str(launcher), mode]),
        '--native-backend', 'codex', '--experiment-mode', 'external',
        '--search-policy', disabled_search, '--model', 'local-fixture-no-model',
        '--reasoning-effort', 'high', '--max-tokens', '1000',
        '--max-seconds', '60', '--max-steps', '4')
    assert process.returncode == 0 and result['status'] == 'prepared', (result, process.stderr)
    return root


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode,reason', [
    ('new-turn', 'native_activity_after_completion'),
    ('failed-turn', 'native_activity_after_completion'),
    ('new-item', 'native_activity_after_completion'),
    ('new-thread', 'native_activity_after_completion'),
    ('error', 'native_failure_event'),
    ('failed-before-completed', 'native_failure_event'),
    ('duplicate-completed', 'native_completion_duplicated'),
    ('duplicate-unreported', 'native_completion_duplicated'),
    ('unterminated-new-turn', 'native_activity_after_completion'),
])
def test_cli_rejects_ambiguous_native_completion_without_seal_or_successor(tmp_path, mode, reason):
    root = _terminal_instance(tmp_path, mode)
    process, result = _terminal_cli(root, 'run')
    assert process.returncode == 2 and result['status'] == 'native_stopped', (result, process.stderr)
    record = execution.origin(root, 1)
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] == reason
    assert outcome['metering']['coverage'] == 'unknown'
    assert outcome['metering']['reason'] == reason
    assert contracts.read(record / 'usage.json') == dict(input_tokens=12, cached_input_tokens=0, output_tokens=3)
    assert native.usage(root)['observed_tokens'] == 15 and native.usage(root)['unknown_steps'] == [1]
    assert (record / 'native.jsonl').read_bytes()
    assert (record / 'native.stderr').is_file()
    assert (record / 'result.pending.json').is_file()
    assert not (record / 'science-output.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()
    work = contracts.workspace(root, 1)
    assert (work / 'work/launcher-count.txt').read_text() == 'launch\n'
    originals = {name: (record / name).read_bytes() for name in (
        'native.jsonl', 'native-outcome.json', 'usage.json', 'usage-observation.json')}
    repeated, result = _terminal_cli(root, 'run')
    assert repeated.returncode == 2 and result['status'] == 'native_outcome_unsealed'
    assert result['reason'] == 'Original execution lost its verified completion; preserved without replay'
    assert all((record / name).read_bytes() == raw for name, raw in originals.items())
    assert (work / 'work/launcher-count.txt').read_text() == 'launch\n'
    assert not (record / 'science-output.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode,expected_rc', [('missing-completed', 0), ('nonzero-exit', 7)])
def test_cli_missing_completion_or_nonzero_exit_retains_original(tmp_path, mode, expected_rc):
    root = _terminal_instance(tmp_path, mode)
    process, result = _terminal_cli(root, 'run')
    assert process.returncode == 2 and result['status'] == 'native_stopped', (result, process.stderr)
    record = execution.origin(root, 1)
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['returncode'] == expected_rc and outcome['stop_reason'] is None
    assert (record / 'result.pending.json').is_file()
    assert not (record / 'science-output.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert not (root / 'jobs/002.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['success', 'late-output'])
def test_cli_unique_native_completion_routes_original_including_late_output(tmp_path, mode):
    root = _terminal_instance(tmp_path, mode)
    process, result = _terminal_cli(root, 'run')
    assert process.returncode == 0 and result['status'] == 'finish', (result, process.stderr)
    assert result['completed_steps'] == 2
    for step in (1, 2):
        record = execution.origin(root, step)
        assert contracts.read(record / 'native-outcome.json')['metering']['coverage'] == 'terminal'
        assert (record / 'result-identity.json').is_file()
        assert (contracts.workspace(root, step) / 'work/launcher-count.txt').read_text() == 'launch\n'
    assert contracts.read(root / 'jobs/002.json')['role'] == 'A'


@pytest.fixture
def instance(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.txt'
    objective.write_text('Distinguish competing explanations; retain uncertainty.')
    launcher = tmp_path / 'native.py'
    launcher.write_text('''import json,os,pathlib,sys
args=sys.argv[1:]
role=os.environ['THIN_HWA_ROLE']
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
prompt=sys.stdin.read()
configs=[args[n+1] for n,value in enumerate(args) if value=='-c']
developer=[json.loads(value.split('=',1)[1]) for value in configs if value.startswith('developer_instructions=')]
(work/'work'/'captured-launch.json').write_text(json.dumps({'argv':args,'prompt':prompt,'developer':developer}))
assert 'MCP' not in prompt
job=json.loads((work/'science-task.json').read_text())
assert job['role']==role and 'global' in job
assert not (work/'science-result.schema.json').exists()
assert 'inputs/global/objective.md' == job['global']['objective']
output=pathlib.Path(args[args.index('--output-last-message')+1])
# Worker-first: W reports a decision stage, then the final A declines delivery.
payload={'summary':role+' bounded finding','scientific_result':'Evidence in artifacts/observation.txt',
         'limitations':['Only a deterministic test; no science validation.'],
         'implementation_selection':None}
if '--output-schema' in args:
    schema=json.loads(pathlib.Path(args[args.index('--output-schema')+1]).read_text())
    payload={**{key:None for key in schema.get('properties',{})},**payload}
if role == 'W':
    payload.update(stage_intent='decision',consultation_question=None,mechanism_findings=None,
        route_reason='Existing evidence can support a final assessment.')
    rows=[row for row in job.get('candidate_nomination_mechanisms') or [] if row['status']!='waived']
    payload['candidate_nominations']=({'representatives':[],'extras':[],'gaps':[{'mechanism_ref':row['mechanism_ref'],
        'reason':'No managed scientific candidate was produced by this transport fixture.'} for row in rows]}
        if rows else None)
if role == 'A':
    from scidiscovery.research_loop.contracts import AUTHOR_EVIDENCE_GUIDANCE as judgment
    assert 'scientific_judgment' not in job
    occurrences=sum(text.count(judgment) for text in [prompt,*developer])
    # Final delivery review uses its own route text; shared guidance is never duplicated.
    assert occurrences <= 1, {'guidance_occurrences':occurrences,'developer_count':len(developer)}
    payload.update(next_action='finish',candidate_recommendation=None,progress_assessment=None,
        decision_request=None,portfolio_review=None,search_review=None,
        route_reason='Decline delivery: the transport fixture supplies no qualified candidate.')
(work/'artifacts'/'observation.txt').write_text(role+' observation')
output.write_text(json.dumps(payload))
print(json.dumps({'type':'thread.started','thread_id':'native-'+role}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':5}}),flush=True)
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
                       max_tokens=2000, max_seconds=60, max_steps=6,
                       model='gpt-6.1-sol', reasoning_effort='high')
    return root


@pytest.mark.process_e2e
def test_real_native_process_completes_without_root_or_loopx(instance):
    result = controller.run(instance)
    if result['status'] != 'finish':
        errors = {}
        for step in range(1, 3):
            record = execution.origin(instance, step)
            stderr = record / 'native.stderr'
            if stderr.exists():
                with stderr.open('rb') as stream:
                    stream.seek(max(0, stderr.stat().st_size - 4096))
                    errors[str(step)] = stream.read(4096).decode('utf-8', errors='replace')
        pytest.fail(json.dumps(dict(result=result, native_stderr=errors), ensure_ascii=False))
    assert result['completed_steps'] == 2
    author_launch=contracts.read(contracts.workspace(instance,2)/'work/captured-launch.json')
    author_job=contracts.read(instance/'jobs/002.json')
    author_rules=author_job['native_role_instruction']+'\n'+contracts.AUTHOR_EVIDENCE_GUIDANCE
    assert author_launch['developer']==[author_rules]
    assert author_rules not in author_launch['prompt']
    assert contracts.read(Path(result['scientific_output_path']))['scientific_result'].startswith('Evidence')
    assert result['usage'] == dict(input_tokens=200, cached_input_tokens=160, output_tokens=10,
                                    observed_tokens=210, unknown_steps=[], accounted_tokens=210,
                                      accounting_basis='observed_lower_bound', blocking_unknown_steps=[],
                                      reserved_tokens=0, reserved_unknown_steps=[], unknown_step_count=0,
                                      blocking_unknown_count=0, unknown_steps_omitted_count=0)
    assert [node['role'] for node in status(instance)['nodes']] == ['W', 'A']
    assert not (instance / 'registry.json').exists()
    for step in range(1, 3):
        work = contracts.workspace(instance, step)
        assert contracts.validate(work)['role'] == ('W', 'A')[step-1]
    assert controller.run(instance) == result


@pytest.mark.process_e2e
@pytest.mark.parametrize('view_version,role,mode', [
    pytest.param(1, 'W', 'local', id='public-worker'),
    pytest.param(0, 'W', 'local', id='explicit-legacy-worker'),
    pytest.param(None, 'W', 'local', id='retained-legacy-worker'),
    pytest.param(1, 'W', 'external', id='external-worker'),
    pytest.param(1, 'H', 'local', id='hypothesis'),
    pytest.param(1, 'A', 'local', id='assessment'),
])
def test_native_stdin_uses_the_execution_entrypoint_for_its_scientific_view(tmp_path, view_version, role, mode):
    """Capture real stdin without submitting an experiment or sealing a verdict."""
    from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY

    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare the supplied scientific observations.')
    launcher = tmp_path / 'capture-native.py'
    launcher.write_text('''import json,os,pathlib,sys
args=sys.argv[1:]
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
(work/'work/native-prompt.txt').write_text(sys.stdin.read())
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps({'fixture':'transport only'}))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':7,'cached_input_tokens':2,'output_tokens':3}}),flush=True)
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable, str(launcher)],
        max_tokens=10000, max_seconds=60, max_steps=4,
        model='fixture-no-model', reasoning_effort='high', search_policy=None,
        experiment_mode=mode, node_budget_mode='soft')
    job = contracts.build_job(root, 1, role, [])
    work = contracts.workspace(root, 1)
    if view_version != 1:
        # Preserve the original absence/zero marker semantics of retained jobs.
        job.pop('worker_view_version')
        if view_version is not None:job['worker_view_version'] = view_version
        job.pop('direct_originals')
        job['history'].pop('candidate_references')
        contracts.write(root / 'jobs/001.json', job)
        contracts.write(work / 'science-task.json', contracts.science_view(job, root))
    intent = dict(task_ref=contracts.record_ref(root / 'jobs/001.json'),
        **native.profiles.binding(contracts.settings(root), job))
    anchor = native.control_budget.accept_node(root, job)
    if anchor is not None:intent['budget_slice'] = anchor
    contracts.write(execution.origin(root, 1) / 'native-intent.json', intent)

    outcome = native.run_native(root, 1)

    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    prompt = (work / 'work/native-prompt.txt').read_text()
    if role != 'W' or mode != 'local':
        assert 'Managed registered experiments:' not in prompt
        assert 'Managed local experiments:' not in prompt
        assert 'scidiscovery.research_loop.worker_execution_bridge' not in prompt
        assert 'scidiscovery.research_loop.experiments' not in prompt
        return
    registered, _ = json.JSONDecoder().raw_decode(prompt.split('Managed registered experiments: ', 1)[1])
    local, _ = json.JSONDecoder().raw_decode(prompt.split('Managed local experiments: run ', 1)[1])
    if view_version == 1:
        command = [sys.executable, '-m', 'scidiscovery.research_loop.worker_execution_bridge',
            '--submission-directory', SUBMISSION_DIRECTORY]
        assert contracts.read(work / 'work/experiment-config.json') == dict(worker_view_version=1, step=1, attempt=0)
        for private in ('--config', 'work/experiment-config.json', 'host-runtime', '/jobs/', 'experiment_client'):
            assert private not in prompt
    else:
        command = [sys.executable, '-m', 'scidiscovery.research_loop.experiments',
            '--config', 'work/experiment-config.json']
        assert 'scidiscovery.research_loop.worker_execution_bridge' not in prompt
    assert registered['command'] == command
    assert local == [*command, 'run', '--experiment-dir', 'experiments/<name>',
        '--script', 'code/run.py', '--', '<script args>']


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['screen','improve','repair','validate'])
def test_native_nomination_prompt_uses_only_the_frozen_job_scope(instance,tmp_path,remote_registry,mode):
    from scidiscovery.research_loop import candidate_nominations
    from test_breadth_science import h,a,findings
    from test_history_handoff import finish
    from test_candidate_nominations import failed_worker_observation

    h(instance,labels=('P','Q'))
    step=2;parents=[1]
    if mode!='screen':
        if mode=='repair':failed_worker_observation(instance,tmp_path)
        finish(instance,2,'W',[1],mechanism_findings=findings('P',blocked=('Q',)),preserve_native_intent=mode=='repair')
        a(instance,3,[1,2],focus='h001.m1',mode=mode)
        step=4;parents=[1,2,3]
    job=contracts.build_job(instance,step,'W',parents)
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action=mode)
    if mode=='repair':assert job['search']['repair_binding']=='available'
    expected=['h001.m1','h001.m2'] if mode=='screen' else ['h001.m1']
    assert [row['mechanism_ref'] for row in job['candidate_nomination_mechanisms']]==expected
    intent=dict(task_ref=contracts.record_ref(instance/'jobs'/f'{step:03d}.json'),
        **native.profiles.binding(contracts.settings(instance),job))
    anchor=native.control_budget.accept_node(instance,job)
    if anchor is not None:intent['budget_slice']=anchor
    else:intent['search_slice']=native.search.accept(instance,job)
    contracts.write(execution.origin(instance,step)/'native-intent.json',intent)

    outcome=native.run_native(instance,step)

    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    launch=contracts.read(contracts.workspace(instance,step)/'work/captured-launch.json')
    assert candidate_nominations.SCOPE_GUIDANCE in launch['prompt']
    assert 'every unwaived H mechanism' not in launch['prompt']
    assert 'Add at most '+str(job['candidate_nomination_policy']['extra_slots'])+' distinct extras' in launch['prompt']
    task=contracts.read(contracts.workspace(instance,step)/'science-task.json')
    assert [row['mechanism_ref'] for row in task['candidate_nomination_mechanisms']]==expected


@pytest.mark.parametrize('reconnect', [False, True])
def test_screen_waits_for_original_runner_after_outcome(instance, tmp_path, monkeypatch, reconnect):
    """Hold the real W runner after outcome publication until control observes it."""
    hook = tmp_path / 'hook'
    hook.mkdir()
    release = hook / 'release'
    held = hook / 'held'
    (hook / 'sitecustomize.py').write_text('''import os,pathlib,sys,time
from scidiscovery.research_loop import contracts as c
original=c.write
def held_write(path,value):
    hold='runner' in sys.argv and path.name=='native-outcome.json' and path.parent.name=='001'
    if hold:pathlib.Path(os.environ['SCID_TEST_HELD']).touch()
    original(path,value)
    if hold:
        deadline=time.monotonic()+10
        while not pathlib.Path(os.environ['SCID_TEST_RELEASE']).exists():
            if time.monotonic()>deadline:raise RuntimeError('Test controller did not observe original runner')
            time.sleep(.01)
c.write=held_write
''')
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(hook), str(Path(__file__).parents[2] / 'src')]))
    monkeypatch.setenv('SCID_TEST_RELEASE', str(release))
    monkeypatch.setenv('SCID_TEST_HELD', str(held))
    record = execution.origin(instance, 1)
    original_live = runtime.process_live
    observed = []

    def release_observed_writer(receipt, binding):
        alive = original_live(receipt, binding)
        runner = record / 'runner-process.json'
        if (alive and held.exists() and (record / 'native-outcome.json').exists()
                and runner.exists() and receipt == contracts.read(runner)):
            observed.append(receipt)
            release.touch()
        return alive

    try:
        if reconnect:
            original_wait = native.wait_native

            def detach_after_outcome(root, step):
                if step != 1:
                    return original_wait(root, step)
                deadline = time.monotonic() + 8
                while not (record / 'native-outcome.json').exists():
                    assert time.monotonic() < deadline, 'Original W did not publish outcome'
                    time.sleep(.01)
                raise RuntimeError('Test controller detached after outcome publication')

            monkeypatch.setattr(native, 'wait_native', detach_after_outcome)
            assert controller.run(instance)['status'] == 'seal_pending'
            monkeypatch.setattr(native, 'wait_native', original_wait)
        monkeypatch.setattr(runtime, 'process_live', release_observed_writer)
        result = controller.run(instance)
        assert result['status'] == 'finish', result
        assert result['completed_steps'] == 2
        assert held.exists(), 'The original runner did not publish its outcome through the held writer'
        assert observed, 'Control must observe the still-live original writer before sealing'
        assert result['usage'] == dict(input_tokens=200, cached_input_tokens=160, output_tokens=10,
                                      observed_tokens=210, unknown_steps=[], accounted_tokens=210,
                                      accounting_basis='observed_lower_bound', blocking_unknown_steps=[],
                                      reserved_tokens=0, reserved_unknown_steps=[], unknown_step_count=0,
                                      blocking_unknown_count=0, unknown_steps_omitted_count=0)
        for step in (1, 2):
            origin = execution.origin(instance, step)
            assert (origin / 'native-intent.json').is_file() and (origin / 'usage.json').is_file()
        assert not (instance / 'jobs/003.json').exists()
        assert not (record / 'continuations').exists()
        assert controller.run(instance) == result
    finally:
        release.touch()
        runner = record / 'runner-process.json'
        if runner.exists():
            receipt = contracts.read(runner)
            deadline = time.monotonic() + 5
            while original_live(receipt, receipt['task_ref']):
                assert time.monotonic() < deadline, 'Released fixture runner did not exit'
                time.sleep(.01)


@pytest.mark.process_e2e
def test_cli_entrypoint_status_is_read_only(instance):
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[2] / 'src')}
    result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli',
                             'run', '--root', str(instance)], env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)['completed_steps'] == 2
    before = {p: (p).read_bytes() for p in instance.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli',
                             'status', '--root', str(instance)], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert {p: (p).read_bytes() for p in instance.rglob('*') if p.is_file()} == before


def test_pause_and_continue_between_nodes(instance, capsys):
    assert main(['pause', '--root', str(instance)]) == 0
    assert controller.run(instance)['status'] == 'paused'
    assert not (instance / 'jobs').exists()
    assert main(['continue', '--root', str(instance)]) == 0
    result = json.loads(capsys.readouterr().out.splitlines()[-1])
    # Worker-first: W reaches its decision stage, then the final A declines delivery.
    assert result['completed_steps'] == 2, result


def test_role_route_is_exact_enum_not_prose(tmp_path):
    path = tmp_path / 'raw.json'
    value = dict(summary='A', scientific_result='Observed', limitations=[],
                 implementation_selection=None, candidate_recommendation=None,
                 progress_assessment=None, decision_request=None, portfolio_review=None,
                 search_review=None, next_action='continue_w', route_reason='A reason')
    path.write_text(json.dumps(value))
    assert contracts.science_payload(path, 'A')['route_reason'] == 'A reason'
    value['next_action'] = 'continue_w: more experiments'
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='next_action'):
        contracts.science_payload(path, 'A')


def test_native_without_usage_is_not_replayed(instance):
    settings = contracts.read(instance / 'settings.json')
    script = Path(settings['native_launcher_argv'][1])
    script.write_text(script.read_text().replace("'turn.completed'", "'usage_unavailable'"))
    assert controller.run(instance)['status'] == 'native_stopped'
    log = contracts.host_record(contracts.workspace(instance, 1)) / 'native.jsonl'
    before = (log).read_bytes()
    assert controller.run(instance)['status'] == 'native_outcome_unsealed'
    assert (log).read_bytes() == before


def test_next_role_receives_science_without_parent_bindings(instance):
    result = controller.run(instance)
    assert result['status'] == 'finish', json.dumps(result)
    # Parent conclusions are readonly views of the exact authored output.
    seal = contracts.read(execution.origin(instance, 1) / 'result-identity.json')
    value = contracts.read(Path(seal['raw_ref']['path']))
    assert value['summary'] == 'W bounded finding'
    assert not {'role', 'direct_refs', 'materials', 'job_ref'} & set(value)
    task = contracts.read(contracts.workspace(instance, 2) / 'science-task.json')
    assert [item['conclusion'] for item in task['parents']] == ['inputs/parents/001/conclusion.json']
    assert all(set(item) == {'conclusion', 'artifacts', 'role', 'limitations', 'limitations_omitted_count',
                             'history_alias'} for item in task['parents'])
    assert 'sha256' not in json.dumps(task)


def test_native_meter_rejects_impossible_caching():
    assert native._usage_event(dict(type='turn.completed', usage=dict(
        input_tokens=5, cached_input_tokens=6, output_tokens=1))) is None


def _terminal_collection_facts(monkeypatch, inventory):
    """Owner-loaded budget, inventory and recovery facts for one terminal original."""
    from scidiscovery.research_loop import continuation
    monkeypatch.setattr(native.control_budget,'prelaunch_budget_reason',lambda *a:None)
    monkeypatch.setattr(contracts,'accept_current_inventory',lambda *a:inventory)
    monkeypatch.setattr(continuation,'automatic_decision',lambda *a,**kw:dict(action='stop'))


@pytest.mark.parametrize('role',list('HWAE'))
def test_terminal_wait_seals_only_after_the_exact_original_runner_exits(tmp_path,monkeypatch,role):
    from scidiscovery.research_loop import checkpoint,continuation
    root=tmp_path/'instance';record=root/'native-evidence/001'
    contracts.write(root/'jobs/001.json',dict(role=role))
    runner=dict(pid=12345,task_ref='original-call')
    contracts.write(record/'runner-process.json',runner)
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    events=[];alive=iter([True,False])
    monkeypatch.setattr(execution,'record',lambda *a:record)
    monkeypatch.setattr(execution,'origin',lambda *a:record)
    monkeypatch.setattr(execution,'identity',lambda *a:'original-call')
    monkeypatch.setattr(runtime,'node_state',lambda *a:'terminal')
    monkeypatch.setattr(checkpoint,'enabled',lambda *a:False)
    inventory=object()
    _terminal_collection_facts(monkeypatch,inventory)
    def process_live(receipt,binding):
        assert receipt==runner and binding=='original-call'
        state=next(alive);events.append(('runner',state));return state
    monkeypatch.setattr(runtime,'process_live',process_live)
    monkeypatch.setattr(native.time,'sleep',lambda seconds:events.append(('wait',seconds)))
    monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Waiting cannot submit another runner'))
    def seal(root_arg,step,*,inventory=None):
        assert root_arg==root and step==1 and inventory is not None
        assert events==[('runner',True),('wait',.1),('runner',False)]
        events.append(('seal',role));return dict(role=role,original_retained=True)
    monkeypatch.setattr(continuation,'seal_or_repair',seal)
    assert native.wait_native(root,1)==dict(role=role,original_retained=True)
    assert events[-1]==('seal',role)
    assert {path:path.read_bytes() for path in root.rglob('*') if path.is_file()}==before


@pytest.mark.parametrize('state',['unknown','terminal'])
def test_wait_preserves_unknown_or_failed_original_without_resubmission(tmp_path,monkeypatch,state):
    from scidiscovery.research_loop import checkpoint,continuation
    root=tmp_path/'instance';record=root/'native-evidence/001'
    contracts.write(root/'jobs/001.json',dict(role='H'))
    contracts.write(record/'runner-process.json',dict(pid=12345,task_ref='original-call'))
    contracts.write(record/'native-intent.json',dict(task_ref=contracts.record_ref(root/'jobs/001.json')))
    if state=='terminal':contracts.write(record/'native-outcome.json',dict(returncode=7,stop_reason=None))
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    monkeypatch.setattr(execution,'record',lambda *a:record)
    monkeypatch.setattr(execution,'origin',lambda *a:record)
    monkeypatch.setattr(execution,'identity',lambda *a:'original-call')
    monkeypatch.setattr(runtime,'node_state',lambda *a:state)
    monkeypatch.setattr(runtime,'process_live',lambda *a:False)
    monkeypatch.setattr(checkpoint,'enabled',lambda *a:False)
    _terminal_collection_facts(monkeypatch,object())
    clock=iter([0,6])
    monkeypatch.setattr(native.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(native.time,'sleep',lambda *a:pytest.fail('No live owner or startup allowance remains'))
    monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Unknown or failed originals cannot be replayed'))
    seals=[]
    def refuse_failed_original(*args,**kwargs):
        seals.append(args);raise RuntimeError('Original failed execution retained')
    monkeypatch.setattr(continuation,'seal_or_repair',refuse_failed_original)
    with pytest.raises(RuntimeError,match='unknown; preserved without replay|Original failed execution retained'):
        native.wait_native(root,1)
    assert len(seals)==(1 if state=='terminal' else 0)
    assert {path:path.read_bytes() for path in root.rglob('*') if path.is_file()}==before


def test_actual_native_launch_rechecks_frozen_settings(instance, monkeypatch):
    contracts.build_job(instance, 1, 'W', [])
    # Frozen settings are bound by their exact locator; no content fingerprint is kept.
    contracts.write(instance / 'settings-identity.json', dict(path='replacement-settings.json'))
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='settings changed'):
        native.execute_native(instance, 1)
    assert not (contracts.host_record(contracts.workspace(instance, 1)) / 'native-intent.json').exists()


# Only the native metering wire is simulated. Qualification, staged hook
# invocation, publication, terminal reconciliation and continuation remain real.
METERING_NATIVE = r'''import json,os,pathlib,shlex,subprocess,sys,time,tomllib
args=sys.argv[1:]
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
config={}
for index,arg in enumerate(args[:-1]):
 if arg=='-c':
  value=tomllib.loads(args[index+1])
  if 'hooks' in value:config.setdefault('hooks',{}).update(value['hooks'])
  else:config.update(value)
scenario=json.loads(pathlib.Path(__file__).with_name('metering.json').read_text())
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():return json.loads(sys.stdin.readline())
assert receive()['method']=='initialize';send(dict(id=1,result={}))
assert receive()['method']=='initialized'
request=receive()
if request['method']=='hooks/list':
 hooks=[]
 for name,key,event in [('PostToolUse','post_tool_use','postToolUse'),('PostModelRequest','post_model_request','postModelRequest'),('PostCompact','post_compact','postCompact')]:
  if name not in config.get('hooks',{}):continue
  handler=config['hooks'][name][0]['hooks'][0]
  hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',eventName=event,
   enabled=True,isManaged=False,trustStatus='untrusted',currentHash='sha256:'+'0'*64,
   source='sessionFlags',sourcePath='/<session-flags>/config.toml',handlerType='command',
   command=handler['command'],timeoutSec=handler['timeout'],
   additionalContextLimit=handler.get('additionalContextLimit')))
 send(dict(id=2,result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
 raise SystemExit(0)
assert request['method'] in {'thread/start','thread/resume'}
with (work/'work/argv.jsonl').open('a') as stream:stream.write(json.dumps(args)+'\n')
with (work/'work/requests.jsonl').open('a') as stream:stream.write(json.dumps(request)+'\n')
resumed=request['method']=='thread/resume';thread='metering-thread'
assert not resumed or request['params']['threadId']==thread and request['params']['excludeTurns'] is True
with (work/'work/launches').open('a') as stream:stream.write('resume\n' if resumed else 'exec\n')
send(dict(id=2,result=dict(thread=dict(id=thread,turns=[]),model=request['params']['model'],
 cwd=str(work),approvalPolicy='never',sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=False),
 reasoningEffort=config['model_reasoning_effort'],serviceTier=config.get('service_tier'))))
request=receive();assert request['method']=='turn/start'
with (work/'work/requests.jsonl').open('a') as stream:stream.write(json.dumps(request)+'\n')
turn='resumed-turn' if resumed else 'initial-turn'
send(dict(id=3,result=dict(turn=dict(id=turn,items=[],status='inProgress'))))
home=pathlib.Path(os.environ.get('CODEX_HOME') or pathlib.Path.home()/'.codex');session=home/'sessions/opaque.jsonl'
session.parent.mkdir(parents=True,exist_ok=True)
if not resumed:
 session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n'
  +json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=
   dict(input_tokens=99999999,cached_input_tokens=80000000,output_tokens=999999)))))+'\n')
def report(tokens):
 handler=config['hooks']['PostModelRequest'][0]['hooks'][0]
 event=dict(session_id=thread,cwd=str(work),transcript_path=str(session),
  hook_event_name='PostModelRequest',status='completed',token_usage=tokens)
 result=subprocess.run(shlex.split(handler['command']),input=json.dumps(event),text=True,capture_output=True,timeout=5)
 assert result.returncode==0,result.stderr

def wait(name):
 until=time.monotonic()+15
 while not (work/('work/'+name)).exists():
  assert time.monotonic()<until,name
  time.sleep(.02)
mode=scenario['mode']
if mode=='stream':
 report(dict(input_tokens=30,cached_input_tokens=20,output_tokens=2))
 send(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId=turn,
  tokenUsage=dict(last=dict(totalTokens=22),modelContextWindow=128))))
 (work/'work/tokens-written').touch();wait('release')
 report(dict(input_tokens=70,cached_input_tokens=60,output_tokens=3));wait('finish')
elif mode=='home':
 assert (home/'auth.json').read_text()=='fixture credentials'
 (work/'work/native-env.json').write_text(json.dumps(dict(codex_home=os.environ.get('CODEX_HOME'),home=str(pathlib.Path.home()))))
 report(dict(input_tokens=100,cached_input_tokens=80,output_tokens=5))
elif mode=='unreported':
 report(dict(input_tokens=100,cached_input_tokens=80,output_tokens=5))
 report(dict(input_tokens=10,output_tokens=2))
elif mode=='profile':
 report(dict(input_tokens=7,cached_input_tokens=0,output_tokens=3))
else:
 assert mode=='resume'
 report(scenario['delta'] if resumed else scenario['baseline'])
 if not resumed:
  (work/'work/checkpoint').write_text('original checkpoint')
  send(dict(method='turn/completed',params=dict(threadId=thread,turn=dict(id=turn,status='failed'))))
  raise SystemExit(0)
 assert (work/'work/checkpoint').read_text()=='original checkpoint'
payload=dict(summary='Metered transport original',scientific_result='No scientific claim.',
 limitations=['Mechanical native transport only.'],implementation_selection=None,search_plan=None,
 next_action='wait',route_reason='Fixture complete')
raw=json.dumps(payload,indent=2)+'\n'
(work/('work/resumed-result.json' if resumed else 'work/original-result.json')).write_text(raw)
send(dict(method='item/completed',params=dict(threadId=thread,turnId=turn,
 item=dict(type='agentMessage',text=raw,phase='final_answer'))))
send(dict(method='turn/completed',params=dict(threadId=thread,turn=dict(id=turn,status='completed'))))
'''


def metering_instance(tmp_path, monkeypatch, mode, *, home=None, **parameters):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Mechanical metering; retain unknown usage.')
    if home is None:
        home=tmp_path/'native-home';home.mkdir()
        monkeypatch.setenv('CODEX_HOME',str(home))
    launcher=tmp_path/'codex';launcher.write_text('#!'+sys.executable+'\n'+METERING_NATIVE);launcher.chmod(0o700)
    contracts.write(tmp_path/'metering.json',dict(mode=mode,**parameters))
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)],max_tokens=5000000,
        max_seconds=60,max_steps=4,model='fixture-no-model',reasoning_effort='high',
        search_policy=None,experiment_mode='external',node_budget_mode='soft',
        native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
        node_budget_policy={role:dict(max_observed_tokens=5000000,max_seconds=30) for role in 'HWAE'},
        resume_policy=None if mode=='home' else dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=True,output_only=True,native_home=str(home)))
    contracts.build_job(root,1,'H',[])
    return root


@pytest.mark.process_e2e
def test_running_session_tokens_project_before_terminal_without_double_count(tmp_path, monkeypatch):
    root=metering_instance(tmp_path,monkeypatch,'stream')
    env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[2]/'src')}
    bootstrap=('import json,sys;from pathlib import Path;from scidiscovery.research_loop import native;'
        'print(json.dumps(native.execute_native(Path(sys.argv[1]),1)))')
    process=subprocess.Popen([sys.executable,'-c',bootstrap,str(root)],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    work=contracts.workspace(root,1);record=execution.origin(root,1)
    try:
        for marker,expected in [('tokens-written',32),('second',105)]:
            until=time.monotonic()+10
            while time.monotonic()<until:
                if (record/'usage.json').exists() and (work/'work/tokens-written').exists():
                    meter=contracts.read(record/'usage.json')
                    current=contracts.read(record/'usage-observation.json')
                    if (meter['input_tokens']+meter['output_tokens']==expected
                            and (marker!='tokens-written' or current['context'].get('total_tokens')==22)):break
                time.sleep(.02)
            else:pytest.fail('Current hook usage was not published')
            assert process.poll() is None and not (record/'native-outcome.json').exists()
            assert native.usage(root)['observed_tokens']==expected
            observation=contracts.read(record/'usage-observation.json')
            assert observation['coverage']=='live' and observation['tokens']==meter
            if marker=='tokens-written':
                assert meter==dict(input_tokens=30,cached_input_tokens=20,output_tokens=2)
                # Context is an independent typed current-turn report.
                assert observation['context']['total_tokens']==22
                assert observation['context']['model_context_window']==128
                (work/'work/release').touch()
            else:assert meter==dict(input_tokens=100,cached_input_tokens=80,output_tokens=5)
    finally:
        (work/'work/release').touch();(work/'work/finish').touch()
        stdout,stderr=process.communicate(timeout=20)
    assert process.returncode==0,stderr+stdout
    assert json.loads(stdout)['summary']=='Metered transport original'
    assert native.usage(root)['observed_tokens']==105
    outcome=contracts.read(record/'native-outcome.json')
    assert outcome['budget_notification']['status']=='available'
    assert outcome['metering']['coverage']=='terminal' and outcome['metering']['reconciliation']=='matched'


@pytest.mark.process_e2e
def test_terminal_billing_keeps_lower_bound_and_completed_scientific_original(tmp_path, monkeypatch):
    root=metering_instance(tmp_path,monkeypatch,'unreported')
    result=native.execute_native(root,1)
    record=execution.origin(root,1);work=contracts.workspace(root,1)
    outcome=contracts.read(record/'native-outcome.json')
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['cleanup']=='observed_group_cleaned'
    assert outcome['budget_notification']['status']=='available'
    assert outcome['metering']['coverage']=='unknown'
    assert outcome['metering']['reason']=='terminal_usage_unreported'
    assert contracts.read(record/'usage.json')==dict(input_tokens=100,cached_input_tokens=80,output_tokens=5)
    assert outcome['usage']['unknown_steps']==[1] and native.usage(root)['observed_tokens']==105
    assert (record/'science-output.json').read_bytes()==(work/'work/original-result.json').read_bytes()
    assert contracts.validate(work)==result
    originals={path:path.read_bytes() for path in record.iterdir() if path.is_file()}
    assert native.wait_native(root,1)==result
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert (work/'work/launches').read_text()=='exec\n'
    assert not (root/'jobs/002.json').exists() and not (record/'continuations').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('explicit_home', [True, False])
def test_raw_codex_live_usage_uses_actual_inherited_home_without_replacing_config(tmp_path, monkeypatch, explicit_home):
    user_home = tmp_path / 'user-home'
    user_home.mkdir()
    actual_home = tmp_path / 'configured-codex-home' if explicit_home else user_home / '.codex'
    actual_home.mkdir()
    (actual_home / 'config.toml').write_text('fixture configuration')
    (actual_home / 'auth.json').write_text('fixture credentials')
    originals = {path: path.read_bytes() for path in (
        actual_home / 'config.toml', actual_home / 'auth.json')}
    monkeypatch.setenv('HOME', str(user_home))
    if explicit_home:
        monkeypatch.setenv('CODEX_HOME', str(actual_home))
    else:
        monkeypatch.delenv('CODEX_HOME', raising=False)
    root=metering_instance(tmp_path,monkeypatch,'home',home=actual_home)
    result=native.execute_native(root,1)
    work=contracts.workspace(root,1)
    outcome=contracts.read(execution.origin(root,1)/'native-outcome.json')
    assert result['next_action']=='wait', {key:outcome.get(key) for key in (
        'stop_reason','technical_diagnostic','returncode','metering')}
    assert outcome['stop_reason'] is None
    assert outcome['usage']['observed_tokens']==105
    assert outcome['metering']['coverage']=='terminal'
    assert outcome['metering']['reconciliation']=='matched'
    assert outcome['native_identity']['home']==str(actual_home)
    assert outcome['native_identity']['session']['home']==str(actual_home)
    assert contracts.read(work/'work/native-env.json')==dict(
        codex_home=str(actual_home) if explicit_home else None,home=str(user_home))
    assert not (root/'native-homes/001/sessions').exists()
    assert all(path.read_bytes()==raw for path,raw in originals.items())
