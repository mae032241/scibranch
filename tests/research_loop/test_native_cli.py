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
payload = {key: None for key in task['final_response']}
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
assert 'Do not build attachment indexes' in prompt
assert 'inputs/global/objective.md' == job['global']['objective']
output=pathlib.Path(args[args.index('--output-last-message')+1])
payload={'summary':role+' bounded finding','scientific_result':'Evidence in artifacts/observation.txt',
         'limitations':['Only a deterministic test; no science validation.'],
         'implementation_selection':None}
if role in {'H','A'}:
    payload.update(next_action='experiment' if role=='H' else 'finish',route_reason='Exact test direction')
if role == 'W':
    payload.update(stage_intent='review',mechanism_findings=[{'mechanism':'Fixture mechanism',
      'basis':'blocked','finding':'No managed scientific observation in this transport fixture.'}])
    payload['candidate_nominations']={'representatives':[],'extras':[],
      'gaps':[{'mechanism_ref':row['mechanism_ref'],
        'reason':'No managed scientific candidate was produced by this transport fixture.'}
        for row in job['candidate_nomination_mechanisms'] if row['status']!='waived']}
if role == 'A':
    from scidiscovery.research_loop.contracts import AUTHOR_EVIDENCE_GUIDANCE as judgment
    assert 'scientific_judgment' not in job
    occurrences=sum(text.count(judgment) for text in [prompt,*developer])
    assert occurrences == 1, {'guidance_occurrences':occurrences,'developer_count':len(developer)}
    payload['candidate_recommendation'] = None
    payload['progress_assessment'] = None
    payload['decision_request'] = None
    payload['portfolio_review'] = None
    mechanisms=json.loads((work/job['lineage']['indexes']['mechanisms']).read_text())
    ref=mechanisms['mechanisms'][0]['mechanism_ref']
    payload['search_review']={'dispositions':[{'mechanism_ref':ref,'status':'pending',
      'reason':'No managed scientific observation in this transport fixture.'}], 'next_focus':None}
if role == 'H':
    payload['search_plan']={'scope':'Fixture scope','commitments':[{'label':'Fixture mechanism',
      'minimal_test':'Fixture measurement','existing_ref':None}],
      'focus':{'label':'Fixture mechanism','action':'screen'}}
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
        for step in range(1, 4):
            record = execution.origin(instance, step)
            stderr = record / 'native.stderr'
            if stderr.exists():
                with stderr.open('rb') as stream:
                    stream.seek(max(0, stderr.stat().st_size - 4096))
                    errors[str(step)] = stream.read(4096).decode('utf-8', errors='replace')
        pytest.fail(json.dumps(dict(result=result, native_stderr=errors), ensure_ascii=False))
    assert result['completed_steps'] == 3
    author_launch=contracts.read(contracts.workspace(instance,3)/'work/captured-launch.json')
    author_job=contracts.read(instance/'jobs/003.json')
    author_rules=author_job['native_role_instruction']+'\n'+contracts.AUTHOR_EVIDENCE_GUIDANCE
    assert author_launch['developer']==[author_rules]
    assert author_rules not in author_launch['prompt']
    assert contracts.read(Path(result['scientific_output_path']))['scientific_result'].startswith('Evidence')
    assert result['usage'] == dict(input_tokens=300, cached_input_tokens=240, output_tokens=15,
                                    observed_tokens=315, unknown_steps=[], accounted_tokens=315,
                                      accounting_basis='observed_lower_bound', blocking_unknown_steps=[],
                                      reserved_tokens=0, reserved_unknown_steps=[])
    assert [node['role'] for node in status(instance)['nodes']] == ['H', 'W', 'A']
    assert not (instance / 'registry.json').exists()
    for step in range(1, 4):
        work = contracts.workspace(instance, step)
        assert contracts.validate(work)['role'] == ('H', 'W', 'A')[step-1]
    assert controller.run(instance) == result


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
    intent = dict(task_sha256=contracts.digest(root / 'jobs/001.json'),
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
        '--script', 'experiments/<name>/run.py', '--', '<script args>']


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
    hold='runner' in sys.argv and path.name=='native-outcome.json' and path.parent.name=='002'
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
    record = execution.origin(instance, 2)
    original_matches = runtime.process_matches
    observed = []

    def release_observed_writer(receipt, binding):
        alive = original_matches(receipt, binding)
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
                if step != 2:
                    return original_wait(root, step)
                deadline = time.monotonic() + 8
                while not (record / 'native-outcome.json').exists():
                    assert time.monotonic() < deadline, 'Original W did not publish outcome'
                    time.sleep(.01)
                raise RuntimeError('Test controller detached after outcome publication')

            monkeypatch.setattr(native, 'wait_native', detach_after_outcome)
            assert controller.run(instance)['status'] == 'seal_pending'
            monkeypatch.setattr(native, 'wait_native', original_wait)
        monkeypatch.setattr(runtime, 'process_matches', release_observed_writer)
        result = controller.run(instance)
        assert result['status'] == 'finish', result
        assert result['completed_steps'] == 3
        assert observed, 'Control must observe the still-live original writer before sealing'
        assert result['usage'] == dict(input_tokens=300, cached_input_tokens=240, output_tokens=15,
                                      observed_tokens=315, unknown_steps=[], accounted_tokens=315,
                                      accounting_basis='observed_lower_bound', blocking_unknown_steps=[],
                                      reserved_tokens=0, reserved_unknown_steps=[])
        assert len(list(instance.rglob('native-intent.json'))) == 3
        assert len(list(instance.rglob('usage.json'))) == 3
        assert not (record / 'continuations').exists()
        assert controller.run(instance) == result
    finally:
        release.touch()
        runner = record / 'runner-process.json'
        if runner.exists():
            receipt = contracts.read(runner)
            deadline = time.monotonic() + 5
            while original_matches(receipt, receipt['task_sha256']):
                assert time.monotonic() < deadline, 'Released fixture runner did not exit'
                time.sleep(.01)


@pytest.mark.process_e2e
def test_cli_entrypoint_status_is_read_only(instance):
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[2] / 'src')}
    result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli',
                             'run', '--root', str(instance)], env=environment,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr + result.stdout
    assert json.loads(result.stdout)['completed_steps'] == 3
    before = {p: contracts.digest(p) for p in instance.rglob('*') if p.is_file()}
    result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli',
                             'status', '--root', str(instance)], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert {p: contracts.digest(p) for p in instance.rglob('*') if p.is_file()} == before


def test_pause_and_continue_between_nodes(instance, capsys):
    assert main(['pause', '--root', str(instance)]) == 0
    assert controller.run(instance)['status'] == 'paused'
    assert not (instance / 'jobs').exists()
    assert main(['continue', '--root', str(instance)]) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['completed_steps'] == 3


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
    before = contracts.digest(log)
    assert controller.run(instance)['status'] == 'native_outcome_unsealed'
    assert contracts.digest(log) == before


def test_tampered_direct_result_never_reaches_next_role(instance):
    result = controller.run(instance)
    assert result['status'] == 'finish', result
    work = contracts.workspace(instance, 3)
    (work / 'inputs/parents/002/conclusion.json').chmod(0o600)
    (work / 'inputs/parents/002/conclusion.json').write_text('{}')
    assert controller.run(instance)['status'] == 'sealed_node_invalid'


def test_actual_material_copy_cannot_change_while_parent_stays_valid(instance):
    result = controller.run(instance)
    assert result['status'] == 'finish', result
    work = contracts.workspace(instance, 3)
    original = work / 'inputs/parents/002/artifacts/observation.txt'
    original.chmod(0o600)
    original.write_text('Fabricated local evidence')
    assert contracts.validate(contracts.workspace(instance, 2))['role'] == 'W'
    with pytest.raises(ValueError, match='actually read'):
        contracts.validate(work)
    assert controller.run(instance)['status'] == 'sealed_node_invalid'


def test_next_role_receives_science_without_parent_bindings(instance):
    result = controller.run(instance)
    assert result['status'] == 'finish', json.dumps(result)
    work = contracts.workspace(instance, 3)
    value = contracts.read(work / 'inputs/parents/002/conclusion.json')
    assert set(value) == {'summary', 'scientific_result', 'limitations', 'implementation_selection',
                          'stage_intent', 'mechanism_findings', 'candidate_nominations'}
    nominations = value['candidate_nominations']
    assert nominations['representatives'] == [] and nominations['extras'] == []
    worker_task = contracts.read(contracts.workspace(instance, 2) / 'science-task.json')
    assert {row['mechanism_ref'] for row in nominations['gaps']} == {
        row['mechanism_ref'] for row in worker_task['candidate_nomination_mechanisms']
        if row['status'] != 'waived'}
    task = contracts.read(work / 'science-task.json')
    assert all(set(item) == {'conclusion', 'artifacts', 'role', 'limitations', 'limitations_omitted_count'}
               for item in task['parents'])
    assert 'sha256' not in json.dumps(task)
    assert not (work / 'inputs/parents/001/inputs').exists()


def test_native_meter_rejects_impossible_caching():
    assert native._usage_event(dict(type='turn.completed', usage=dict(
        input_tokens=5, cached_input_tokens=6, output_tokens=1))) is None


@pytest.mark.parametrize('role',list('HWAE'))
def test_terminal_wait_seals_only_after_the_exact_original_runner_exits(tmp_path,monkeypatch,role):
    from scidiscovery.research_loop import checkpoint,continuation
    root=tmp_path/'instance';record=root/'native-evidence/001'
    contracts.write(root/'jobs/001.json',dict(role=role))
    runner=dict(pid=12345,task_sha256='original-call')
    contracts.write(record/'runner-process.json',runner)
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    events=[];alive=iter([True,False])
    monkeypatch.setattr(execution,'record',lambda *a:record)
    monkeypatch.setattr(execution,'origin',lambda *a:record)
    monkeypatch.setattr(execution,'identity',lambda *a:'original-call')
    monkeypatch.setattr(runtime,'node_state',lambda *a:'terminal')
    monkeypatch.setattr(checkpoint,'enabled',lambda *a:False)
    def process_matches(receipt,binding):
        assert receipt==runner and binding=='original-call'
        state=next(alive);events.append(('runner',state));return state
    monkeypatch.setattr(runtime,'process_matches',process_matches)
    monkeypatch.setattr(native.time,'sleep',lambda seconds:events.append(('wait',seconds)))
    monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Waiting cannot submit another runner'))
    def seal(root_arg,step):
        assert root_arg==root and step==1
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
    contracts.write(record/'runner-process.json',dict(pid=12345,task_sha256='original-call'))
    contracts.write(record/'native-intent.json',dict(task_sha256=contracts.digest(root/'jobs/001.json')))
    if state=='terminal':contracts.write(record/'native-outcome.json',dict(returncode=7,stop_reason=None))
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    monkeypatch.setattr(execution,'record',lambda *a:record)
    monkeypatch.setattr(execution,'origin',lambda *a:record)
    monkeypatch.setattr(execution,'identity',lambda *a:'original-call')
    monkeypatch.setattr(runtime,'node_state',lambda *a:state)
    monkeypatch.setattr(runtime,'process_matches',lambda *a:False)
    monkeypatch.setattr(checkpoint,'enabled',lambda *a:False)
    clock=iter([0,6])
    monkeypatch.setattr(native.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(native.time,'sleep',lambda *a:pytest.fail('No live owner or startup allowance remains'))
    monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Unknown or failed originals cannot be replayed'))
    seals=[]
    def refuse_failed_original(*args):
        seals.append(args);raise RuntimeError('Original failed execution retained')
    monkeypatch.setattr(continuation,'seal_or_repair',refuse_failed_original)
    with pytest.raises(RuntimeError,match='unknown; preserved without replay|Original failed execution retained'):
        native.wait_native(root,1)
    assert len(seals)==(1 if state=='terminal' else 0)
    assert {path:path.read_bytes() for path in root.rglob('*') if path.is_file()}==before


def test_actual_native_launch_rechecks_frozen_settings(instance, monkeypatch):
    contracts.build_job(instance, 1, 'H', [])
    policy = contracts.read(instance / 'settings.json')
    policy['model'] = 'unauthorized-replacement'
    contracts.write(instance / 'settings.json', policy)
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *a, **kw: pytest.fail('must not launch'))
    with pytest.raises(ValueError, match='settings changed'):
        native.execute_native(instance, 1)
    assert not (contracts.host_record(contracts.workspace(instance, 1)) / 'native-intent.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('history_entries', [0, 600], ids=['empty-home', 'crowded-home'])
def test_running_session_tokens_project_before_terminal_without_double_count(instance, history_entries):
    import time
    settings = contracts.read(instance / 'settings.json')
    script = Path(settings['native_launcher_argv'][1])
    original = "print(json.dumps({'type':'thread.started','thread_id':'native-'+role}),flush=True)"
    streaming = '''import time
thread='native-'+role
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
session=home/'sessions'/('rollout-'+thread+'.jsonl')
session.parent.mkdir(parents=True,exist_ok=True)
for index in range(HISTORY_ENTRIES):
    (session.parent/('rollout-history-%04d.jsonl'%index)).write_text('')
session.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
with session.open('a') as log:
    log.write(json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':30,'cached_input_tokens':20,'output_tokens':2},'last_token_usage':{'total_tokens':22},'model_context_window':128}}})+'\\n')
    log.flush()
if role == 'H':
    (work/'work/tokens-written').write_text(str(time.monotonic()))
    until=time.monotonic()+15
    while not (work/'work/release').exists() and time.monotonic()<until:time.sleep(.05)
with session.open('a') as log:
    log.write(json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':5},'last_token_usage':{'total_tokens':85},'model_context_window':128}}})+'\\n')
    log.flush()
if role == 'H':
    until=time.monotonic()+15
    while not (work/'work/finish').exists() and time.monotonic()<until:time.sleep(.05)
'''
    streaming = streaming.replace('HISTORY_ENTRIES', str(history_entries))
    script.write_text(script.read_text().replace(original, streaming))
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[2] / 'src')}
    contracts.build_job(instance, 1, 'H', [])
    bootstrap = ('import json,sys;from pathlib import Path;'
                 'from scidiscovery.research_loop import native;'
                 'print(json.dumps(native.execute_native(Path(sys.argv[1]),1)))')
    process = subprocess.Popen([sys.executable, '-c', bootstrap, str(instance)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    work = contracts.workspace(instance, 1)
    record = contracts.host_record(work)
    until = time.monotonic() + 10
    try:
        while time.monotonic() < until:
            if (record / 'usage-observation.json').exists():
                observation = contracts.read(record / 'usage-observation.json')
                if observation['coverage'] == 'live':
                    break
            time.sleep(.05)
        else:
            pytest.fail('running token projection was not published within 10 seconds')
        assert process.poll() is None
        assert time.monotonic() - float((work / 'work/tokens-written').read_text()) < 5
        assert contracts.read(record / 'usage.json') == dict(input_tokens=30, cached_input_tokens=20, output_tokens=2)
        assert native.usage(instance)['observed_tokens'] == 32
        assert observation['tokens']['output_tokens'] == 2
        assert observation['context']['total_tokens'] == 22
        assert observation['context']['model_context_window'] == 128
        assert observation['unreported_usage']
        assert not (record / 'native-outcome.json').exists()
        (work / 'work/release').touch()
        until = time.monotonic() + 10
        while time.monotonic() < until:
            if contracts.read(record / 'usage.json')['input_tokens'] == 100:
                break
            time.sleep(.05)
        else:
            pytest.fail('a second running token projection was not published within 10 seconds')
        assert process.poll() is None
        assert not (record / 'native-outcome.json').exists()
        assert contracts.read(record / 'usage.json') == dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
        assert native.usage(instance)['observed_tokens'] == 105
    finally:
        (work / 'work/release').touch()
        (work / 'work/finish').touch()
        stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stderr + stdout
    result = json.loads(stdout)
    assert result['summary'] == 'H bounded finding', result
    assert native.usage(instance)['observed_tokens'] == 105
    assert contracts.read(record / 'usage.json')['input_tokens'] == 100
    metering = contracts.read(record / 'native-outcome.json')['metering']
    assert metering['coverage'] == 'terminal'
    assert metering['reconciliation'] == 'matched'


@pytest.mark.process_e2e
@pytest.mark.parametrize('stream', ['billing_mismatch', 'known_then_unreported'])
def test_terminal_billing_keeps_lower_bound_and_completed_scientific_original(instance, stream):
    settings = contracts.read(instance / 'settings.json')
    script = Path(settings['native_launcher_argv'][1])
    original = "print(json.dumps({'type':'thread.started','thread_id':'native-'+role}),flush=True)"
    live_input = 100 if stream == 'known_then_unreported' else 150
    source = script.read_text().replace(original, '''thread='native-'+role
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
session=home/'sessions'/('rollout-'+thread+'.jsonl')
session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\\n'+json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':LIVE_INPUT,'cached_input_tokens':80,'output_tokens':5}}}})+'\\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)'''.replace('LIVE_INPUT', str(live_input)))
    if stream == 'known_then_unreported':
        source = source.replace("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':5}}),flush=True)",
            "print(json.dumps({'type':'turn.completed'}),flush=True)")
    script.write_text(source)
    contracts.build_job(instance, 1, 'H', [])
    result = native.execute_native(instance, 1)
    assert result['role'] == 'H' and result['summary'] == 'H bounded finding'
    record = contracts.host_record(contracts.workspace(instance, 1))
    outcome = contracts.read(record / 'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['metering']['coverage'] == 'unknown' and outcome['metering']['source'] == 'turn.completed'
    assert outcome['metering']['reason'] == ('terminal_usage_unreported' if stream == 'known_then_unreported'
        else 'terminal_usage_below_live_observation')
    assert contracts.read(record / 'usage.json')['input_tokens'] == live_input
    assert outcome['usage']['unknown_steps'] == [1]
    assert native.usage(instance)['observed_tokens'] == live_input + 5
    assert contracts.validate(contracts.workspace(instance, 1)) == result
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    assert native.wait_native(instance, 1) == result
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not (instance / 'jobs/002.json').exists() and not (record / 'continuations').exists()




@pytest.mark.process_e2e
@pytest.mark.parametrize('explicit_home', [True, False])
def test_raw_codex_live_usage_uses_actual_inherited_home_without_replacing_config(tmp_path, monkeypatch, explicit_home):
    import time
    user_home = tmp_path / 'user-home'
    user_home.mkdir()
    actual_home = tmp_path / 'configured-codex-home' if explicit_home else user_home / '.codex'
    actual_home.mkdir()
    (actual_home / 'config.toml').write_text('fixture configuration')
    (actual_home / 'auth.json').write_text('fixture credentials')
    monkeypatch.setenv('HOME', str(user_home))
    if explicit_home:
        monkeypatch.setenv('CODEX_HOME', str(actual_home))
    else:
        monkeypatch.delenv('CODEX_HOME', raising=False)
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.txt'
    objective.write_text('No model; meter the raw launcher from its actual native home.')
    launcher = tmp_path / 'codex'
    launcher.write_text('#!' + sys.executable + '\n' + '''import json,os,pathlib,sys,time
sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
# Mimic raw Codex storage; deliberately do not read THIN_HWA_NATIVE_HOME.
home=pathlib.Path(os.environ.get('CODEX_HOME') or (pathlib.Path.home()/'.codex'))
assert (home/'config.toml').read_text()=='fixture configuration'
assert (home/'auth.json').read_text()=='fixture credentials'
(work/'work/native-env.json').write_text(json.dumps({'codex_home':os.environ.get('CODEX_HOME'),'home':str(pathlib.Path.home())}))
thread='raw-native-home-thread'
session=home/'sessions'/('rollout-'+thread+'.jsonl')
session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\\n'+json.dumps({'type':'event_msg','payload':{'type':'token_count','info':{'total_token_usage':{'input_tokens':100,'cached_input_tokens':80,'output_tokens':5}}}})+'\\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
payload=dict(summary='Original native home retained',scientific_result='Transport fixture only',limitations=['No scientific claim'],implementation_selection=None,search_plan=None,next_action='wait',route_reason='Fixture complete')
pathlib.Path(sys.argv[sys.argv.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=100,cached_input_tokens=80,output_tokens=5))),flush=True)
''')
    launcher.chmod(0o755)
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [str(launcher)], max_tokens=50,
        max_seconds=10, max_steps=6, model='no-model', reasoning_effort='high')
    started = time.monotonic()
    result = controller.run(root)
    assert time.monotonic() - started < 5
    assert result['status'] == 'wait', result
    work = contracts.workspace(root, 1)
    outcome = contracts.read(contracts.host_record(work) / 'native-outcome.json')
    assert outcome['stop_reason'] is None
    assert outcome['usage']['observed_tokens'] == 105
    assert contracts.read(work / 'work/native-env.json') == dict(
        codex_home=str(actual_home) if explicit_home else None, home=str(user_home))
    assert not (root / 'native-homes/001/sessions').exists()
    assert (actual_home / 'config.toml').read_text() == 'fixture configuration'
    assert (actual_home / 'auth.json').read_text() == 'fixture credentials'
