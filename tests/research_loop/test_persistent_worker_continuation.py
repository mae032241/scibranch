"""Ordinary W stages continue under default policy until independent review."""
import copy
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from scidiscovery.research_loop import (budget, checkpoint, configuration_updates,
    continuation, contracts as c, controller, execution, experiment_client,
    experiments, native, research_config)
from test_composed_execution_results import composed_registry
import test_managed_execution_wait as async_wait


PERSISTENT_CODEX = r'''import json,os,pathlib,subprocess,sys
from scidiscovery.research_loop import contracts as c
from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
args=sys.argv[1:]
if 'app-server' in args:
    sys.stderr.write('Fixture does not implement metadata-only app-server.\n')
    sys.exit(2)
prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
jobpath=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST'])
job=json.loads(jobpath.read_text());root=jobpath.parent.parent;role=job['role']
counter=work/'work/native-turns';number=int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(number+1))
if 'resume' in args:
    thread=args[-2]
    sessionfile=home/'sessions'/f'rollout-{thread}.jsonl'
    thread=json.loads(sessionfile.read_text().splitlines()[0])['payload']['id']
    assert thread in args
else:
    thread='11111111-2222-4333-8444-'+str(job['step']*100+number).zfill(12)
    sessionfile=home/'sessions'/f'rollout-{thread}.jsonl'
    sessionfile.parent.mkdir(parents=True,exist_ok=True)
    sessionfile.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
case=os.environ.get('SCID_PERSISTENT_CASE','normal')
repair='Fix diagnosed JSON/layout only;' in prompt
if role=='W':
    assert not (root/'jobs/003.json').exists(), 'A must wait for this logical W stage'
    def exp(arguments):
        response=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
            '--submission-directory',str(work/SUBMISSION_DIRECTORY),*arguments],capture_output=True,text=True)
        return response.returncode,json.loads(response.stdout)
    callsfile=work/'work/original-calls.json'
    calls=json.loads(callsfile.read_text()) if callsfile.exists() else []
    if calls:
        code,result=exp(['query','--call-id',calls[0]])
        assert code==0 and result['state']=='succeeded',result
        code,result=exp(['collect','--call-id',calls[0]])
        assert code==0 and result['status']=='collected',result
    package=work/'code/package'
    if number==0:
        package.parent.mkdir(exist_ok=True)
        package.write_text('retained original observation')
    code,result=exp(['run','--payload',str(package),'--new-plan'])
    if repair:
        assert code==2 and result['status']=='unavailable' and 'call_id' not in result,result
        (work/'work/repair-run-refusal.json').write_text(json.dumps(result))
    else:
        assert code==0 and result['status']=='known_success',result
        assert result['call_id'] not in calls
        calls.append(result['call_id']);callsfile.write_text(json.dumps(calls))
    if case=='context_due' and number==0:
        with sessionfile.open('a') as stream:
            stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(
                total_token_usage=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),
                last_token_usage=dict(total_tokens=850),model_context_window=1000))))+'\n')
        import time
        time.sleep(1.5)
        code,refused=exp(['run','--payload',str(package),'--new-plan'])
        assert code==2 and refused['status']=='review_required' and refused['accepted'] is False,refused
        assert refused['reason']=='worker_context_review_due' and 'call_id' not in refused,refused
        assert refused['handoff']['action']=='finalize_response',refused
        assert 'stage_intent=review' in refused['handoff']['instruction'],refused
        (work/'work/context-review-refusal.json').write_text(json.dumps(refused))
        code,query=exp(['query','--call-id',calls[0]])
        assert code==0 and query['state']=='succeeded',query
        code,collected=exp(['collect','--call-id',calls[0]])
        assert code==0 and collected['status']=='collected',collected
        (work/'work/context-original-collected').write_text(calls[0])
    if number and not repair:
        assert 'work/science-resume.json' in prompt
        assert json.loads((work/'work/science-resume.json').read_text())['science_status']=='present'
    if case=='unknown_usage' and number==0:sys.exit(0)
previous=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)
for line in sessionfile.read_text().splitlines():
    event=json.loads(line)
    if event.get('payload',{}).get('type')=='token_count':previous=event['payload']['info']['total_token_usage']
total={key:previous[key]+value for key,value in dict(input_tokens=10,cached_input_tokens=3,output_tokens=4).items()}
if not (role=='W' and case=='missing_context'):
    with sessionfile.open('a') as stream:
        stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(
            total_token_usage=total,last_token_usage=dict(total_tokens=100),model_context_window=1000))))+'\n')
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
if role=='W' and case=='missing' and number==0:sys.exit(0)
payload=dict(summary='One decision-relevant observation',scientific_result='A discriminating observation with target uncertainty.',
    limitations=['Internal no-model fixture'],implementation_selection=None)
if role=='H':payload.update(search_plan=None,next_action='experiment',route_reason='Compare a plausible mechanism')
if role=='W':
    turns=int(os.environ.get('SCID_PERSISTENT_TURNS','4'))
    payload.update(stage_intent='continue' if number+1<turns else 'review',mechanism_findings=None)
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,
    next_action='finish',route_reason='Independent assessment retains unresolved limits')
for field in ('decision_request','portfolio_review'):
    if field in c.result_schema(role,job.get('handoff_protocol',1))['properties']:payload[field]=None
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


@pytest.fixture
def persistent_instance(tmp_path, monkeypatch, composed_registry):
    monkeypatch.setenv('PYTHONPATH', str(Path(c.__file__).resolve().parents[2])
        + os.pathsep + os.environ.get('PYTHONPATH', ''))
    home = tmp_path / 'codex-home'
    home.mkdir()
    (home / 'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=true\n')
    monkeypatch.setenv('CODEX_HOME', str(home))
    launcher = tmp_path / 'codex'
    launcher.write_text('#!' + sys.executable + '\n' + PERSISTENT_CODEX)
    launcher.chmod(0o700)
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Explore mechanisms continuously and request independent evidence assessment.')

    def prepare(**changes):
        options = dict(root=tmp_path / 'instance', objective=objective,
            public_root=public, launcher=[str(launcher)], max_tokens=2000,
            max_seconds=120, max_steps=12, search_policy=None,
            execution_config=dict(executor='composed_execution:fixture',
                configuration=dict(synchronous=True, collect=True, terminal_status='known_success')))
        options.update(changes)
        research_config.prepare(**options)
        return options['root'], home
    return prepare


def jobs(root):
    return [c.read(root / 'jobs' / f'{step:03d}.json') for step in (1, 2, 3)]


def run_to_finish(root):
    result = controller.run(root)
    diagnostics = {}
    if result['status'] != 'finish':
        for step in (1, 2):
            for name in ('native-outcome.json', 'native.stderr', 'runner.stderr'):
                path = root / 'native-evidence' / f'{step:03d}' / name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(max(0, path.stat().st_size - 4000))
                        diagnostics[path.relative_to(root).as_posix()] = stream.read(4000).decode(errors='replace')
    assert result['status'] == 'finish', json.dumps(dict(result=result, diagnostics=diagnostics))
    return result


@pytest.mark.process_e2e
@pytest.mark.parametrize('context', ['same_session', 'new_context'],
    ids=['default_same_session', 'explicit_new_context'])
def test_standard_defaults_continue_one_w_session_beyond_failure_repair_limit(persistent_instance, context):
    options = {} if context == 'same_session' else dict(worker_fragment_policy=dict(context=context))
    root, home = persistent_instance(**options)
    settings = c.settings(root)
    assert settings['worker_fragment_policy'] == dict(context=context,max_context_windows=2)
    assert settings['resume_policy']['output_only'] is True
    run_to_finish(root)
    assert [job['role'] for job in jobs(root)] == ['H', 'W', 'A']
    chain = execution.records(root, 2)
    count=4 if context=='same_session' else 2
    assert len(chain) == count
    if context=='same_session':assert len(chain)>settings['resume_policy']['max_attempts']
    task = c.record_ref(root / 'jobs/002.json')
    intents = [c.read(record / 'native-intent.json') for record in chain]
    assert all(intent['task_ref'] == task for intent in intents)
    assert [intent['continuation_kind'] for intent in intents[1:]] == ['fragment'] * (count-1)
    assert all(intent['context_mode'] == context for intent in intents[1:])
    assert [intent['budget_slice']['previous_tokens'] for intent in intents[1:]] == [14*number for number in range(1,count)]
    homes = [execution.home(root, 2, number) for number in range(count)]
    threads = [c.read(record / 'native-outcome.json')['thread_id'] for record in chain]
    assert homes[0] == home
    if context == 'same_session':
        assert all(intent['session']['home'] == str(home) for intent in intents[1:])
        assert len(set(threads)) == len(set(homes)) == 1
    else:
        assert all(intent.get('session') is None for intent in intents[1:])
        assert homes[1:] == [root / 'native-homes/002' / f'context-{number:03d}' for number in range(1, count)]
        assert [intent['native_home'] for intent in intents[1:]] == [str(path) for path in homes[1:]]
        assert len(set(threads)) == len(set(homes)) == count
    assert native.usage(root)['observed_tokens'] == 14*(count+2)
    configs = [c.read(record / 'experiment-config.json') for record in chain]
    assert all(config['scope'] == configs[0]['scope'] for config in configs)
    calls = experiment_client.record_directory(root, 2) / 'calls'
    originals = sorted(c.read(path / 'accepted.json')['native_attempt']['number'] for path in calls.iterdir())
    assert originals == list(range(count))
    assert c.read(chain[0] / 'science-output.json')['stage_intent'] == 'continue'
    assert c.read(chain[0] / 'checkpoint.json')['binding']['stage_owner'] == task
    ended = checkpoint.stage_end(root, jobs(root)[1], chain[-1])
    assert ended['reason'] == ('worker_requested' if context=='same_session' else 'worker_context_review_due')
    assert not (chain[0] / 'stage-end.json').exists()
    assert c.validate(c.workspace(root, 2))['role'] == 'W'
    assert execution.validate_records(root, 2) == chain


@pytest.mark.process_e2e
def test_completed_output_repair_can_resume_ordinary_w_but_cannot_run_work_itself(
        persistent_instance, monkeypatch):
    monkeypatch.setenv('SCID_PERSISTENT_CASE', 'missing')
    root, _ = persistent_instance()
    run_to_finish(root)
    chain = execution.records(root, 2)
    intents = [c.read(record / 'native-intent.json') for record in chain]
    assert [intent['continuation_kind'] for intent in intents[1:]] == ['repair', 'fragment', 'fragment']
    assert intents[1]['output_repair']['code'] == 'missing_output'
    assert c.read(chain[1] / 'science-output.json')['stage_intent'] == 'continue'
    assert c.read(c.workspace(root, 2) / 'work/repair-run-refusal.json')['status'] == 'unavailable'
    calls = experiment_client.record_directory(root, 2) / 'calls'
    assert sorted(c.read(path / 'accepted.json')['native_attempt']['number'] for path in calls.iterdir()) == [0, 2, 3]
    assert [intent['budget_slice']['previous_tokens'] for intent in intents[1:]] == [14, 28, 42]
    assert [job['role'] for job in jobs(root)] == ['H', 'W', 'A']
    assert execution.validate_records(root, 2) == chain


@pytest.mark.process_e2e
def test_soft_time_target_requests_independent_a_at_stopped_checkpoint(persistent_instance, monkeypatch):
    # A retained legacy instance has no context-window field in its frozen
    # policy; preparing it directly preserves that historical absence.
    prepare=research_config.prepare
    def legacy_prepare(**options):
        normalized=research_config.normalize(options)
        normalized['worker_fragment_policy'].pop('max_context_windows')
        return controller.prepare(**normalized)
    monkeypatch.setattr(research_config,'prepare',legacy_prepare)
    root, _ = persistent_instance()
    monkeypatch.setattr(research_config,'prepare',prepare)
    original_duration = budget._node_duration

    def duration(selected_root, job, intent, snapshot):
        if job['role'] == 'W' and (execution.record(root, 2) / 'usage.json').exists():
            return c.settings(root)['node_budget_policy']['W']['max_seconds']
        return original_duration(selected_root, job, intent, snapshot)

    monkeypatch.setattr(budget, '_node_duration', duration)
    run_to_finish(root)
    chain = execution.records(root, 2)
    assert len(chain) == 1
    assert c.read(chain[0] / 'science-output.json')['stage_intent'] == 'continue'
    assert checkpoint.stage_end(root, jobs(root)[1], chain[0])['reason'] == 'worker_progress_review_due'
    assert [job['role'] for job in jobs(root)] == ['H', 'W', 'A']
    calls = experiment_client.record_directory(root, 2) / 'calls'
    assert len(list(calls.iterdir())) == 1


@pytest.mark.process_e2e
def test_soft_token_target_alone_keeps_continuing_w_stage(persistent_instance):
    root, _ = persistent_instance(node_budget_policy={
        role: dict(max_observed_tokens=14, max_seconds=120) for role in 'HWAE'})
    run_to_finish(root)
    assert len(execution.records(root, 2)) == 4
    assert checkpoint.stage_end(root, jobs(root)[1], execution.record(root, 2))['reason'] == 'worker_requested'


@pytest.mark.process_e2e
def test_context_boundary_refuses_new_work_but_collects_original_and_routes_a(persistent_instance, monkeypatch):
    monkeypatch.setenv('SCID_PERSISTENT_CASE','context_due')
    root, _ = persistent_instance(worker_fragment_policy=dict(context='same_session',max_context_windows=1))
    run_to_finish(root)
    chain=execution.records(root,2)
    assert len(chain)==1
    assert checkpoint.stage_end(root,jobs(root)[1],chain[0])['reason']=='worker_context_review_due'
    work=c.workspace(root,2)
    refused=c.read(work/'work/context-review-refusal.json')
    assert refused['accepted'] is False and 'call_id' not in refused
    calls=experiment_client.record_directory(root,2)/'calls'
    assert [path.name for path in calls.iterdir()]==[(work/'work/context-original-collected').read_text()]
    assert [job['role'] for job in jobs(root)]==['H','W','A']


@pytest.mark.process_e2e
def test_terminal_billing_fallback_keeps_context_unknown_and_blocks_next_fragment(persistent_instance, monkeypatch):
    monkeypatch.setenv('SCID_PERSISTENT_CASE','missing_context')
    root,_=persistent_instance()
    run_to_finish(root)
    chain=execution.records(root,2)
    assert len(chain)==1
    outcome=c.native_outcome(chain[0])
    assert outcome['metering']['coverage']=='terminal'
    assert outcome['metering']['context_windows']['coverage']=='unknown'
    assert checkpoint.stage_end(root,jobs(root)[1],chain[0])['reason']=='worker_context_observation_unknown'
    assert [job['role'] for job in jobs(root)]==['H','W','A']


@pytest.mark.process_e2e
def test_pause_before_next_fragment_seals_original_w_without_starting_a(persistent_instance, monkeypatch):
    from scidiscovery.research_loop import instance_control
    root, _ = persistent_instance()
    start = continuation.fragment_start
    originals = {}
    requested = []

    def pause_once(selected_root, step, *, inventory):
        assert selected_root == root and step == 2
        if not requested:
            paths = (root / 'jobs/002.json', inventory.record / 'native-intent.json',
                inventory.record / 'science-output.json', inventory.record / 'usage.json')
            originals.update({path: path.read_bytes() for path in paths})
            requested.append(instance_control.request_pause(root))
        return start(selected_root, step, inventory=inventory)

    monkeypatch.setattr(continuation, 'fragment_start', pause_once)
    result = controller.run(root)
    assert result['status'] == 'paused', result
    assert len(requested) == 1 and requested[0]['status'] == 'pause_requested'
    assert (root / 'pause.request').exists()
    chain = execution.records(root, 2)
    assert len(chain) == 1
    assert c.read(chain[0] / 'science-output.json')['stage_intent'] == 'continue'
    job = c.read(root / 'jobs/002.json')
    assert checkpoint.stage_end(root, job, chain[0])['reason'] == 'worker_resume_paused_or_closeout'
    assert (chain[0] / 'result-identity.json').exists()
    assert not (root / 'jobs/003.json').exists()
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert execution.validate_records(root, 2) == chain
    assert c.validate(c.workspace(root, 2))['role'] == 'W'


@pytest.mark.process_e2e
def test_pending_original_wakes_and_is_consumed_but_next_batch_waits_for_a(
        tmp_path, monkeypatch, composed_registry):
    # Cross the real logical-stage time target after the first dispatch was
    # accepted, while that original remains pending. No clock or admission
    # decisions are replaced in the controller or its child processes.
    source = async_wait.LAUNCHER
    first = "  (work/'work/original-call').write_text(result['call_id'])"
    source = source.replace(first, "  time.sleep(5.2)\n" + first, 1)
    batches = """   assert code==2 and next_call['status']=='running',next_call
   assert next_call['call_id'] not in [call['call_id'] for call in calls]
   calls.append(dict(call_id=next_call['call_id'],payload=package.read_text()))
   (work/'work/managed-calls.json').write_text(json.dumps(calls))
   (work/'work/new-call-accepted').write_text(next_call['call_id'])"""
    refusal = """   assert code==2 and next_call['status']=='review_required',next_call
   assert next_call['accepted'] is False and next_call['reason']=='worker_progress_review_due',next_call
   assert 'call_id' not in next_call and next_call['handoff']['action']=='finalize_response',next_call
   assert 'stage_intent=review' in next_call['handoff']['instruction']
   (work/'work/progress-review-refusal.json').write_text(json.dumps(next_call))
   code,query=request(['query','--call-id',calls[0]['call_id']])
   assert code==0 and query['state']=='succeeded',query
   code,collected=request(['collect','--call-id',calls[0]['call_id']])
   assert code==0 and collected['status']=='collected',collected
   (work/'work/progress-original-query.json').write_text(json.dumps(query))
   (work/'work/progress-original-collect.json').write_text(json.dumps(collected))"""
    assert batches in source
    monkeypatch.setattr(async_wait, 'LAUNCHER', source.replace(batches, refusal, 1))
    root = async_wait.instance(tmp_path, dict(collect=True, terminal_status='known_success',
        adaptive_batches=3), fragment=True, node_seconds=5)
    originals = {}
    waiting_calls = []

    def complete_original(record, config, call):
        assert not waiting_calls, 'No next batch may be accepted before independent A review'
        assert record == execution.origin(root, 2)
        assert not (root / 'jobs/003.json').exists()
        review = experiments.review_status(config)
        assert review['requested'] and review['enforced']
        assert review['reason'] == 'worker_progress_review_due'
        directory = experiments.Records(config).directory(call)
        originals.update({path: path.read_bytes() for path in
            (directory / 'accepted.json', directory / 'submission.json')})
        waiting_calls.append(call)

    reply = async_wait.run_cli(root, on_wait=complete_original)
    result = json.loads(reply.stdout)
    assert reply.returncode == 0 and result['status'] == 'wait', (reply.stdout, reply.stderr)
    assert len(waiting_calls) == 1
    chain = execution.records(root, 2)
    assert len(chain) == 2
    assert c.read(chain[1] / 'native-intent.json')['continuation_kind'] == 'execution_wake'
    assert c.read(chain[1] / 'native-intent.json')['budget_slice']['previous_tokens'] == 12
    work = c.workspace(root, 2)
    refused = c.read(work / 'work/progress-review-refusal.json')
    assert refused['accepted'] is False and refused['handoff']['reason'] == 'worker_progress_review_due'
    assert c.read(work / 'work/progress-original-query.json')['call_id'] == waiting_calls[0]
    assert c.read(work / 'work/progress-original-collect.json')['call_id'] == waiting_calls[0]
    assert (work / 'work/consumed-result').read_text() == 'known_success'
    assert not (work / 'work/new-call-accepted').exists()
    calls = experiment_client.record_directory(root, 2) / 'calls'
    assert [path.name for path in calls.iterdir()] == waiting_calls
    assert all(path.read_bytes() == value for path, value in originals.items())
    assert c.read(execution.raw(root, 2))['stage_intent'] == 'review'
    # The fixture explicitly retains the untested second mechanism. Its
    # scientific blocked exit remains intact after the watchdog stopped new work.
    assert checkpoint.stage_end(root, jobs(root)[1], chain[-1])['reason'] == 'scientific_blocked'
    assert [job['role'] for job in jobs(root)] == ['H', 'W', 'A']


@pytest.mark.process_e2e
@pytest.mark.parametrize('context', [None, 'new_context'], ids=['default-context', 'explicit-context'])
def test_completed_output_repair_wakes_only_previously_accepted_ordinary_work(
        tmp_path, monkeypatch, composed_registry, context):
    if context is not None:
        run = async_wait.subprocess.run

        def explicit_context(command, *args, **kwargs):
            if (isinstance(command, list) and 'scidiscovery.research_loop.cli' in command
                    and 'prepare' in command):
                command = [*command, '--worker-context', context]
            return run(command, *args, **kwargs)

        monkeypatch.setattr(async_wait.subprocess, 'run', explicit_context)
    source = async_wait.LAUNCHER
    start = " if not resume:\n  (work/'work/initial-prompt').write_text(prompt)"
    repair = """ if resume and 'Fix diagnosed JSON/layout only' in prompt:
  code,refused=request(['run','--payload',str(work/'code/package'),'--new-plan','--mechanism','1'])
  assert code==2 and refused['status']=='unavailable' and 'call_id' not in refused,refused
  (work/'work/repair-run-refusal.json').write_text(json.dumps(refused))
  payload.update(stage_intent='continue',mechanism_findings=[
   dict(mechanism='h001.m1',basis='blocked',finding='The accepted original remains pending consumption'),
   dict(mechanism='h001.m2',basis='blocked',finding='No second mechanism was executed')])
  print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10,cached_input_tokens=2,output_tokens=2))),flush=True)
  pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
  sys.exit(0)
"""
    assert start in source
    source = source.replace(start, repair + start, 1)
    output = "pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))"
    last_output = source.rfind(output)
    source = source[:last_output] + "if role=='W' and not resume:sys.exit(0)\n" + source[last_output:]
    monkeypatch.setattr(async_wait, 'LAUNCHER', source)
    root, home = async_wait.standard_instance(tmp_path, monkeypatch, adaptive_batches=1, same_focus=True)
    assert c.settings(root)['worker_fragment_policy'] == dict(context=context or 'same_session',max_context_windows=2)
    stopped = threading.Event()
    observed, errors = {}, []

    def complete_original():
        try:
            waiting = execution.origin(root, 2) / 'continuations/001/execution-wait.json'
            deadline = time.monotonic() + 15
            while not waiting.exists():
                if stopped.wait(.02):
                    return
                assert time.monotonic() < deadline, 'Completed repair did not retain the pending original'
            value = c.read(waiting)
            assert len(value['calls']) == 1
            item = value['calls'][0]
            accepted = c.read(Path(item['accepted']['path']))
            assert accepted['native_attempt']['number'] == 0
            assert c.read(waiting.parent / 'checkpoint.json')['science']['status'] == 'present'
            assert c.read(waiting.parent / 'native-intent.json')['output_repair']['code'] == 'missing_output'
            config = c.read(Path(item['config']['path']))
            directory = experiments.Records(config).directory(accepted['call_id'])
            observed.update(call=accepted['call_id'], originals={path: path.read_bytes() for path in
                (directory / 'accepted.json', directory / 'submission.json')})
            experiments.load(config).complete(accepted['call_id'])
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=complete_original)
    thread.start()
    try:
        reply = async_wait.run_cli(root)
    finally:
        stopped.set()
        thread.join(timeout=3)
    assert not errors and not thread.is_alive(), errors
    result = json.loads(reply.stdout)
    assert reply.returncode == 0 and result['status'] == 'wait', (reply.stdout, reply.stderr)
    chain = execution.records(root, 2)
    assert len(chain) == 3
    intents = [c.read(record / 'native-intent.json') for record in chain]
    assert [intent['continuation_kind'] for intent in intents[1:]] == ['repair', 'execution_wake']
    assert [intent['budget_slice']['previous_tokens'] for intent in intents[1:]] == [12, 24]
    assert all(intent['session']['home'] == str(home) for intent in intents[1:])
    work = c.workspace(root, 2)
    assert c.read(work / 'work/repair-run-refusal.json')['status'] == 'unavailable'
    assert (work / 'work/consumed-result').read_text() == 'known_success'
    assert [path.name for path in (experiment_client.record_directory(root, 2) / 'calls').iterdir()] == [observed['call']]
    assert all(path.read_bytes() == value for path, value in observed['originals'].items())
    assert [job['role'] for job in jobs(root)] == ['H', 'W', 'A']


@pytest.mark.process_e2e
def test_unknown_native_usage_does_not_create_fragment_or_a(persistent_instance, monkeypatch):
    monkeypatch.setenv('SCID_PERSISTENT_CASE', 'unknown_usage')
    root, _ = persistent_instance()
    result = controller.run(root)
    assert result['status'] == 'native_stopped'
    assert len(execution.records(root, 2)) == 1
    assert not (root / 'jobs/003.json').exists()
    assert not (execution.origin(root, 2) / 'result-identity.json').exists()


@pytest.mark.parametrize('context', ['same_session', 'new_context', None])
def test_explicit_fragment_context_keeps_default_resume_and_finite_targets(
        persistent_instance, context):
    policy = None if context is None else dict(context=context)
    root, _ = persistent_instance(worker_fragment_policy=policy)
    settings = c.settings(root)
    assert settings.get('worker_fragment_policy') == (dict(context=context,max_context_windows=2) if policy is not None else None)
    assert settings['resume_policy']['output_only'] is True
    assert set(settings['node_budget_policy']) == set('HWAE')
    assert all(value == dict(max_observed_tokens=2000, max_seconds=120)
        for value in settings['node_budget_policy'].values())


@pytest.mark.parametrize('backend,launcher', [
    ('codex', ['never-launched']), ('codex', [sys.executable, 'custom.py']),
    ('other', ['codex']),
])
def test_nonstandard_backend_or_launcher_gets_no_implicit_continuation(tmp_path, backend, launcher):
    options = dict(root=tmp_path / 'instance', objective=tmp_path / 'goal',
        public_root=tmp_path, native_backend=backend, launcher=launcher,
        model='declared-model', max_tokens=100, max_seconds=20, max_steps=4)
    original = copy.deepcopy(options)
    normalized = research_config.normalize(options)
    assert normalized.get('resume_policy') is None
    assert normalized.get('worker_fragment_policy') is None
    assert normalized.get('node_budget_policy') is None
    assert options == original


@pytest.mark.parametrize('changed', [dict(role='A'), dict(formal_review={'requested': True}),
    dict(final_only=True), dict(final_report={'source_cutoff': 0}), dict(purpose='closeout')])
def test_special_task_cannot_receive_ordinary_fragment(tmp_path, monkeypatch, changed):
    from scidiscovery.research_loop import protocol
    job = dict(dict(step=1, role='W'), **changed)
    settings = dict(worker_fragment_policy=dict(context='same_session'))
    c.write(tmp_path / 'jobs/001.json', job)
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(continuation, 'validate_launcher', lambda *args: dict(output_only=True))
    monkeypatch.setattr(continuation, 'validate_task', lambda *args, **kwargs: job)
    monkeypatch.setattr(configuration_updates, 'for_job', lambda *args: settings)
    monkeypatch.setattr(budget, 'transition', lambda *args: pytest.fail('Special task cannot spend another fragment'))
    assert not checkpoint.enabled_for_policy(job, settings=settings)
    with pytest.raises(continuation.ResumeUnavailable, match='worker_fragments_unavailable'):
        continuation.fragment_start(tmp_path, 1, inventory=None)
    prior, record = tmp_path / 'prior', tmp_path / 'current'
    c.write(record / 'native-intent.json', dict(continuation_kind='fragment'))
    with pytest.raises(ValueError, match='original ordinary W task'):
        continuation.validate_fragment_chain(tmp_path, 1, [prior, record])


def test_author_closeout_cannot_expand_to_ordinary_fragment(tmp_path, monkeypatch):
    from scidiscovery.research_loop import correction, fork_runtime, protocol
    job = dict(step=1, role='W')
    settings = dict(worker_fragment_policy=dict(context='same_session'))
    c.write(tmp_path / 'jobs/001.json', job)
    prior, record = tmp_path / 'prior', tmp_path / 'current'
    c.write(prior / 'native-intent.json', dict(continuation_kind='author_closeout'))
    c.write(record / 'native-intent.json', dict(continuation_kind='fragment'))
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(continuation, 'validate_launcher', lambda *args: dict(output_only=True))
    monkeypatch.setattr(continuation, 'validate_task', lambda *args, **kwargs: job)
    monkeypatch.setattr(configuration_updates, 'for_job', lambda *args: settings)
    monkeypatch.setattr(configuration_updates, 'for_step', lambda *args: settings)
    monkeypatch.setattr(budget, 'closeout_state', lambda *args: dict(begin=None))
    monkeypatch.setattr(budget, 'transition', lambda *args: pytest.fail('Author closeout cannot spend another fragment'))
    monkeypatch.setattr(correction, 'gate', lambda *args: None)
    monkeypatch.setattr(fork_runtime, 'local_steps', lambda *args, **kwargs: [1])
    monkeypatch.setattr(execution, 'records', lambda *args: [prior])
    with pytest.raises(continuation.ResumeUnavailable, match='worker_fragment_special_scope_unavailable'):
        continuation.fragment_start(tmp_path, 1, inventory=None)
    with pytest.raises(ValueError, match='author closeout scope'):
        continuation.validate_fragment_chain(tmp_path, 1, [prior, record])


@pytest.mark.parametrize('reason', ['worker_progress_review_due','worker_context_review_due',
    'worker_context_observation_unknown'])
def test_progress_review_refusal_finishes_response_without_repairing_or_replaying(reason):
    from scidiscovery.research_loop import managed_wait
    result = dict(status='review_required', accepted=False, reason=reason)
    handoff = managed_wait.handoff(None, result, 'run')
    assert handoff['action'] == 'finalize_response'
    assert handoff['reason'] == reason
    assert 'stage_intent=review' in handoff['instruction']
    assert 'separately accepted original calls' in handoff['instruction']
    assert 'repair this request' in handoff['instruction']


@pytest.mark.parametrize('result', [
    dict(status='review_required', accepted=False, reason='other_review'),
    dict(status='unknown', accepted=False, reason='worker_progress_review_due'),
    dict(status='review_required', reason='worker_progress_review_due'),
    dict(status='review_required', accepted=True, reason='worker_progress_review_due', call_id='original'),
])
def test_only_unaccepted_typed_progress_review_changes_handoff(result):
    from scidiscovery.research_loop import managed_wait
    assert managed_wait.handoff(None, result, 'run')['action'] == 'reconcile_or_stop'
