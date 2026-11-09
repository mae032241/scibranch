"""Exact original interruption reuses the native owner, without model calls."""
import copy
from contextlib import contextmanager
import html
import json
import os
import re
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (accepted_budget, cli, configuration_updates,
    contracts as c, controller, instance_control, instance_ui, native, native_recovery,
    runtime, ui_saved_status)
from scidiscovery.research_loop import budget, continuation, execution
from test_native_transport_stall import _scene, _transport_guard
from test_instance_ui import post, session
from test_worker_bridge_readiness import seed as seed_bridge
from execution_fixtures import execution_registry


def accepted_scene(tmp_path, monkeypatch, *, interrupt_support=True):
    root, record = _scene(tmp_path, monkeypatch, 'blocked')
    intent = c.read(record / 'native-intent.json')
    if interrupt_support:
        intent['operator_interrupt_version'] = native.OPERATOR_INTERRUPT_VERSION
    (record / 'native-intent.json').unlink()
    accepted_budget.accept_native(root, 1, record, intent)
    target = dict(step=1, expected_attempt=0,
        native_intent_ref=dict(path=str(record / 'native-intent.json')))
    return root, record, target


def test_interrupt_request_binds_original_and_preserves_consumption(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    original = (record / 'native-intent.json').read_bytes()
    head = accepted_budget.read(root)
    first = instance_control.request_interrupt(root, **target)
    request = (record / 'interrupt.request').read_bytes()
    again = instance_control.request_interrupt(root, **target)
    assert first == again
    assert first['status'] == 'interrupt_requested'
    assert first['cleanup'] == 'not_yet_confirmed'
    assert (root / 'pause.request').exists()
    assert (record / 'interrupt.request').read_bytes() == request
    assert (record / 'native-intent.json').read_bytes() == original
    after = accepted_budget.read(root)
    assert after['dispatch_count'] == head['dispatch_count']
    assert after['continuation_count'] == head['continuation_count']
    assert after['usage'] == head['usage']
    assert after['current_native'] == head['current_native']
    assert not (record / 'native-outcome.json').exists()


def test_stale_seen_target_never_interrupts_a_successor(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    head = copy.deepcopy(accepted_budget.read(root))
    head['current_native']['record'] += '/continuations/001'
    head['current_native']['intent']['attempt'] = 1
    head['current_job']['native_record'] = head['current_native']['record']
    monkeypatch.setattr(accepted_budget, 'load', lambda root, **kwargs: head)
    result = instance_control.request_interrupt(root, **target)
    assert result['status'] == 'interrupt_unavailable'
    assert result['reason'] == 'original_target_no_longer_current'
    assert (root / 'pause.request').exists()
    assert not (record / 'interrupt.request').exists()
    assert not (record / 'continuations').exists()


def test_idle_interrupt_only_pauses_and_invalid_reference_is_rejected(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match='exact original native intent'):
        instance_control.request_interrupt(root, **dict(target, native_intent_ref=dict(path='/wrong/original')))
    assert not (root / 'pause.request').exists()
    receipt = instance_control.request_interrupt(root)
    assert receipt == dict(status='pause_requested', accepted_work='no_native_target_requested')
    assert (root / 'pause.request').exists() and not (record / 'interrupt.request').exists()


def test_frozen_legacy_runner_only_pauses_without_claiming_interrupt_support(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch, interrupt_support=False)
    original = (record / 'native-intent.json').read_bytes()
    result = instance_control.request_interrupt(root, **target)
    assert result['status'] == 'interrupt_unsupported' and result['paused'] is True
    assert not (record / 'interrupt.request').exists()
    assert (record / 'native-intent.json').read_bytes() == original
    shown = ui_saved_status._node(root, 1, c.read(root / 'jobs/001.json'))
    assert shown['interrupt_support'] == 'unsupported' and 'interrupt_target' not in shown
    host = instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    markup = host._interrupt_form('manager', shown)
    assert '未声明立即中断支持' in markup and 'Ctrl+C' in markup
    assert '/control/interrupt' not in markup


def stopped_original(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    instance_control.request_interrupt(root, **target)
    monkeypatch.setattr(native.subprocess,'Popen',lambda *args,**kwargs:pytest.fail('unexpected native launch'))
    with pytest.raises(RuntimeError,match='before native submission'):
        native.run_native(root,1)
    return root,record,target


def resume_options(target, **options):
    return dict(after_interrupt=True,expected_attempt=target['expected_attempt'],
        native_intent_ref=target['native_intent_ref'],**options)


def test_explicit_operator_resume_preserves_missing_billing_and_exact_original(tmp_path,monkeypatch):
    root,record,target=stopped_original(tmp_path,monkeypatch)
    originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'native-outcome.json',record/'interrupt.request')}
    stopped=c.read(record/'native-outcome.json')
    launched=[]
    monkeypatch.setattr(runtime,'start',lambda *args:launched.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:dict(status='fixture_continued'))
    result=continuation.start(root,1,**resume_options(target))
    new=record/'continuations/001'
    intent=c.read(new/'native-intent.json')
    assert result['status']=='fixture_continued' and launched==[(root,1,1)]
    assert intent['continuation_kind']=='operator_resume' and intent['context_mode']=='new_context'
    assert intent['context_reason']=='native_resume_unsupported_backend'
    assert intent['budget_slice']['previous_seconds']==stopped['budget_slice']['advance_seconds']
    assert intent['budget_slice']['previous_tokens']==stopped['budget_slice']['observed_tokens']
    assert native.usage(root)['unknown_steps']==[1]
    assert not (record/'usage.json').exists()
    assert not (root/'pause.request').exists() and not (new/'interrupt.request').exists()
    assert {path:path.read_bytes() for path in originals}==originals
    # An exact repeated intent collects its sole accepted successor.
    continuation.start(root,1,**resume_options(target))
    assert launched==[(root,1,1)]
    with pytest.raises(continuation.ResumeUnavailable,match='source_attempt_changed'):
        continuation.start(root,1,**resume_options(target,new_context=True))


@pytest.mark.parametrize('failure',['non_operator','cleanup_unknown','attempt_stale','other_job_unknown','steps_exhausted'])
def test_explicit_operator_resume_rejects_unqualified_source_or_budget(tmp_path,monkeypatch,failure):
    root,record,target=stopped_original(tmp_path,monkeypatch)
    options=resume_options(target)
    outcome=c.read(record/'native-outcome.json')
    if failure=='non_operator':
        outcome['stop_reason']='native_error_unknown';c.write(record/'native-outcome.json',outcome)
    elif failure=='cleanup_unknown':
        outcome['cleanup']='cleanup_unknown';c.write(record/'native-outcome.json',outcome)
    elif failure=='attempt_stale':options['expected_attempt']=1
    elif failure=='other_job_unknown':
        usage=native.usage(root)
        monkeypatch.setattr(native,'usage',lambda *args,**kwargs:dict(usage,
            blocking_unknown_steps=[1,2],blocking_unknown_count=2))
    else:
        monkeypatch.setattr(budget,'used_steps',lambda *args,**kwargs:budget.step_limit(root))
    monkeypatch.setattr(runtime,'start',lambda *args:pytest.fail('unqualified successor launched'))
    with pytest.raises((continuation.ResumeUnavailable,budget.NodeBudgetUnavailable)):
        continuation.start(root,1,**options)
    assert not (record/'continuations').exists()
    assert (record/'interrupt.request').exists() and (root/'pause.request').exists()


def test_resume_ui_and_cli_bind_explicit_original_and_new_context_selection(tmp_path,monkeypatch,capsys):
    root,record,target=stopped_original(tmp_path,monkeypatch)
    key=tmp_path/'ui-secret';key.write_bytes(b'x'*64);key.chmod(0o600)
    host,cookie=session(root,key)
    calls=[]
    monkeypatch.setattr(instance_control,'start_background',lambda root,**kwargs:calls.append(kwargs) or dict(status='accepted'))
    try:
        current=ui_saved_status._node(root,1,c.read(root/'jobs/001.json'))
        assert current['operator_resume_target']['attempt']==0
        markup=host._operator_resume_form(host._browser(cookie),current['operator_resume_target'])
        assert '接续已中断的工作' in markup and '使用新上下文接续同一原任务' in markup
        first=re.search(r'<form.*?</form>',markup,re.S).group(0)
        fields={html.unescape(key):html.unescape(value) for key,value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">',first)}
        assert post(host,cookie,'resume',dict(fields,native_intent_ref='{}'))[0]==422
        assert post(host,cookie,'resume',fields)[0]==303
        assert calls[0]['after_interrupt'] is True and calls[0]['new_context'] is False
        assert calls[0]['expected_attempt']==0 and calls[0]['native_intent_ref']==target['native_intent_ref']
    finally:host.translation.close()
    seen=[]
    monkeypatch.setattr(controller,'worker_resume',lambda root,step,**kwargs:seen.append((step,kwargs)) or dict(status='paused'))
    assert cli.main(['worker-resume','--root',str(root),'--step','1','--after-interrupt',
        '--expected-attempt','0','--new-context'])==0
    capsys.readouterr()
    assert seen==[(1,resume_options(target,new_context=True))]


def test_operator_session_unknown_is_not_automatic_new_context_and_explicit_selection_is_retained(tmp_path,monkeypatch):
    from scidiscovery.research_loop import backends
    root,record,target=stopped_original(tmp_path,monkeypatch)
    select=c.settings_for_job
    monkeypatch.setattr(c,'settings_for_job',lambda *args,**kwargs:dict(select(*args,**kwargs),resume_policy=dict(max_attempts=2)))
    monkeypatch.setattr(backends.Protocol,'supports_resume',property(lambda self:True))
    def unavailable(*args,**kwargs):
        raise continuation.ResumeUnavailable('worker_resume_metadata_unavailable')
    monkeypatch.setattr(continuation,'_automatic_session',unavailable)
    launched=[]
    monkeypatch.setattr(runtime,'start',lambda *args:launched.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    with pytest.raises(continuation.ResumeUnavailable,match='metadata_unavailable'):
        continuation.start(root,1,**resume_options(target))
    assert not launched and not (record/'continuations').exists()
    continuation.start(root,1,**resume_options(target,new_context=True))
    intent=c.read(record/'continuations/001/native-intent.json')
    assert intent['context_reason']=='operator_selected_new_context' and intent['operator_new_context'] is True
    assert launched==[(root,1,1)]


def test_new_context_operator_resume_retains_context_history_before_any_new_submission(tmp_path,monkeypatch):
    from scidiscovery.research_loop import backends
    root,record,target=stopped_original(tmp_path,monkeypatch)
    windows=dict(coverage='observed',windows_started=2,compactions=1,reason=None)
    outcome=c.read(record/'native-outcome.json')
    outcome['metering']['context_windows']=windows
    c.write(record/'native-outcome.json',outcome)
    monkeypatch.setattr(runtime,'start',lambda *args:None)
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    continuation.start(root,1,**resume_options(target))
    successor=record/'continuations/001'
    instance_control.request_interrupt(root,step=1,expected_attempt=1,
        native_intent_ref=dict(path=str(successor/'native-intent.json')))
    values=[];meter=backends.Protocol.meter
    def captured(self,*args,**kwargs):
        values.append(copy.deepcopy(kwargs['context_state']))
        return meter(self,*args,**kwargs)
    monkeypatch.setattr(backends.Protocol,'meter',captured)
    with pytest.raises(RuntimeError,match='before native submission'):
        native.run_native(root,1,1)
    assert values==[windows]
    assert c.read(record/'native-outcome.json')['metering']['context_windows']==windows


@pytest.mark.parametrize('receipt',['missing','malformed','live'])
def test_positive_owner_cleanup_allows_missing_redundant_receipts_but_not_invalid_or_live_writer(tmp_path,monkeypatch,receipt):
    root,record,target=stopped_original(tmp_path,monkeypatch)
    outcome=c.read(record/'native-outcome.json')
    outcome.update(native_started=True,returncode=-15,cleanup='observed_group_cleaned')
    c.write(record/'native-outcome.json',outcome)
    if receipt=='malformed':
        c.write(record/'runner-process.json',dict(task_ref=dict(path=str(root/'jobs/001.json')),pid=0))
    elif receipt=='live':
        c.write(record/'runner-process.json',runtime.process_identity(os.getpid(),dict(path=str(root/'jobs/001.json'))))
    launches=[]
    monkeypatch.setattr(runtime,'start',lambda *args:launches.append(args))
    monkeypatch.setattr(native,'wait_native',lambda *args,**kwargs:{})
    if receipt=='missing':
        continuation.start(root,1,**resume_options(target))
        successor=c.read(record/'continuations/001/native-intent.json')
        assert successor['source_missing_process_receipts']==['runner-process.json','native-process.json']
        assert launches==[(root,1,1)]
    else:
        with pytest.raises((ValueError,continuation.ResumeUnavailable)):
            continuation.start(root,1,**resume_options(target))
        assert not launches and not (record/'continuations').exists()


def test_prelaunch_interrupt_never_submits_or_mints_terminal_success(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    instance_control.request_interrupt(root, **target)
    original = (record / 'native-intent.json').read_bytes()
    monkeypatch.setattr(native.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('interrupted original launched'))
    with pytest.raises(RuntimeError, match='before native submission'):
        native.run_native(root, 1)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['stop_reason'] == 'operator_interrupt'
    assert outcome['native_started'] is False and outcome['returncode'] is None
    assert outcome['terminal_observed'] is False and outcome['cleanup'] is None
    assert not native_recovery.never_started(outcome)
    assert not (record / 'science-output.json').exists()
    assert not (record / 'result-identity.json').exists()
    assert (record / 'native-intent.json').read_bytes() == original
    assert (record / 'interrupt.request').exists()


def test_operator_interrupt_stays_ineligible_after_pause_is_explicitly_cleared(tmp_path, monkeypatch):
    from scidiscovery.research_loop import continuation
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    instance_control.request_interrupt(root, **target)
    outcome = dict(stop_reason='operator_interrupt', terminal_observed=False,
        cleanup='observed_group_cleaned', native_started=True, returncode=-15,
        native_failure=dict(category='temporary', not_accepted=True), thread_id=None)
    c.write(record / 'native-outcome.json', outcome)
    (root / 'pause.request').unlink()
    original = configuration_updates.for_step(root, 1)
    monkeypatch.setattr(configuration_updates, 'for_step', lambda *args: dict(original,
        resume_policy=dict(automatic_recovery=native_recovery.DEFAULT_POLICY)))
    monkeypatch.setattr(runtime, 'node_state', lambda *args: 'terminal')
    decision = continuation.automatic_decision(root, 1)
    assert decision['action'] == 'stop' and decision['reason'] == 'operator_interrupt'
    assert not native_recovery.recoverable_source(outcome)
    assert (record / 'interrupt.request').exists()
    assert not (record / 'continuations').exists()


def test_saved_target_uses_loaded_intent_not_continuation_count(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    # A stray unaccepted numeric directory cannot acquire an interrupt target.
    (record / 'continuations/999').mkdir(parents=True)
    job = c.read(root / 'jobs/001.json')
    shown = ui_saved_status._node(root, 1, job)
    assert 'interrupt_target' not in shown
    (record / 'continuations/999').rmdir()
    shown = ui_saved_status._node(root, 1, job)
    assert shown['interrupt_target'] == dict(step=1, attempt=0,
        native_intent_ref=target['native_intent_ref'])
    instance_control.request_interrupt(root, **target)
    assert ui_saved_status._node(root, 1, job)['interrupt_requested'] is True


def test_cli_pause_keeps_default_and_immediate_requires_explicit_scope(tmp_path, monkeypatch, capsys):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    assert cli.main(['pause', '--root', str(root)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'pause_requested'
    assert not (record / 'interrupt.request').exists()
    assert cli.main(['pause', '--root', str(root), '--immediate', '--step', '1']) == 1
    assert 'requires' in json.loads(capsys.readouterr().out)['error']
    assert cli.main(['pause', '--root', str(root), '--immediate', '--step', '1', '--attempt', '0']) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['status'] == 'interrupt_requested' and receipt['cleanup'] == 'not_yet_confirmed'


def test_interrupt_ui_binds_nonce_to_seen_scope_and_checks_csrf(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    key = tmp_path / 'ui-secret'
    key.write_bytes(b'x' * 64); key.chmod(0o600)
    host, cookie = session(root, key)
    try:
        token = host._browser(cookie)
        current = ui_saved_status._node(root, 1, c.read(root / 'jobs/001.json'))
        markup = host._interrupt_form(token, current)
        fields = {html.unescape(key): html.unescape(value) for key, value in re.findall(
            r'<input type="hidden" name="([^"]+)" value="([^"]*)">', markup)}
        assert fields['step'] == '1' and fields['expected_attempt'] == '0'
        assert json.loads(fields['native_intent_ref']) == target['native_intent_ref']
        with pytest.raises(PermissionError, match='CSRF'):
            post(host, cookie, 'interrupt', dict(fields, csrf='forged'))
        code, response, _ = post(host, cookie, 'interrupt', dict(fields, expected_attempt='1'))
        assert code == 422 and '此表单绑定的原身份已变化' in response.decode()
        assert not (record / 'interrupt.request').exists()
        assert post(host, cookie, 'interrupt', fields)[0] == 303
        request = (record / 'interrupt.request').read_bytes()
        code, response, _ = post(host, cookie, 'interrupt', fields)
        assert code == 422 and '表单已过期或已提交' in response.decode()
        assert (record / 'interrupt.request').read_bytes() == request
        pending = ui_saved_status._node(root, 1, c.read(root / 'jobs/001.json'))
        assert '等待原任务停止' in host._interrupt_form(token, pending)
        assert '/control/interrupt' not in host._interrupt_form(token, pending)
    finally:
        host.translation.close()


@pytest.mark.parametrize('state', ['active', 'service_wait', 'pause_requested'])
def test_interrupt_ui_remains_reachable_after_next_node_pause_without_readonly_control(tmp_path, monkeypatch, state):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    host = instance_ui.InstanceUI.__new__(instance_ui.InstanceUI)
    host._technical_stop = lambda *args: None
    host._form = lambda token, action, fields, label: f'<form action="/control/{action}">{label}</form>'
    current = ui_saved_status._node(root, 1, c.read(root / 'jobs/001.json'))
    snapshot = dict(view=dict(nodes=[current], paused=state == 'pause_requested', correction={}))
    status = dict(state=state, display=dict(current=current))
    assert '/control/interrupt' in str(host._primary('manager', True, snapshot, status))
    assert '/control/interrupt' not in str(host._primary('reader', False, snapshot, status))


@pytest.mark.process_e2e
def test_native_owner_interrupt_keeps_usage_and_cleans_exact_original(tmp_path, monkeypatch):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    original = (record / 'native-intent.json').read_bytes()
    results = []
    errors = []
    def interrupt():
        try:
            until = time.monotonic() + 8
            while not (record / 'usage.json').exists():
                if time.monotonic() >= until:
                    raise TimeoutError('fixture never published original usage')
                time.sleep(.02)
            results.append(instance_control.request_interrupt(root, **target))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=interrupt, daemon=True)
    with controller._writer(root), _transport_guard():
        thread.start()
        with pytest.raises(RuntimeError, match='not a verified terminal result'):
            native.run_native(root, 1)
        thread.join(3)
    assert not thread.is_alive() and not errors
    assert results[0]['status'] == 'interrupt_requested'
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['stop_reason'] == 'operator_interrupt'
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert outcome['native_started'] and outcome['returncode'] != 0
    assert outcome['terminal_observed'] is False and outcome['metering']['coverage'] == 'unknown'
    assert c.read(record / 'usage.json') == dict(input_tokens=7, cached_input_tokens=2, output_tokens=1)
    assert outcome['budget_slice']['observed_tokens'] == 8
    assert (record / 'native-intent.json').read_bytes() == original
    assert (record / 'interrupt.request').exists() and (root / 'pause.request').exists()
    assert not (record / 'continuations').exists() and not (record / 'result-identity.json').exists()
    process = c.read(record / 'native-process.json')
    assert not runtime.process_matches(process, process['task_ref'])


@pytest.mark.process_e2e
@pytest.mark.parametrize('cleanup', ['observed_group_cleaned', 'cleanup_unknown'])
def test_interrupt_bridge_skips_future_operation_deadline_and_preserves_unknown(tmp_path, monkeypatch, cleanup):
    from scidiscovery.research_loop import budget, execution, experiment_client, experiments, worker_execution_bridge as bridge
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    work = c.workspace(root, 1)
    directory = work / bridge.SUBMISSION_DIRECTORY
    directory.mkdir(parents=True)
    private = root / 'private'
    claims = private / 'worker-submissions'
    claims.mkdir(parents=True)
    configuration = dict(record_dir=str(private / 'records'), scope=dict(step=1, role='H'))
    config_path = work / 'work/experiment-config.json'
    c.write(config_path, dict(step=1, attempt=0))
    seeded = SimpleNamespace(directory=directory, claims=claims, configuration=configuration)
    raw, claim = seed_bridge(seeded, known=False, arguments=['research-declare'])
    originals = {path: path.read_bytes() for path in (claim / 'submit.json', claim / 'binding.json')}
    marker = record / 'fixture-blocked-operation'
    monkeypatch.setattr(experiment_client, 'resolve', lambda *args: configuration)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args: root)
    monkeypatch.setattr(experiments, 'load', lambda *args, **kwargs: None)
    monkeypatch.setattr(bridge, '_configuration', lambda server: server.configuration)
    # The resolver already produced this exact original above. Isolate that
    # fact so the guard covers only serve's removed diagnostic fallback.
    monkeypatch.setattr(execution, 'record', lambda *args: record)
    monkeypatch.setattr(c, 'settings', lambda *args, **kwargs: pytest.fail('serve must not reopen settings'))
    monkeypatch.setattr(bridge, '_execute', lambda *args, **kwargs: pytest.fail('unknown original replayed'))
    monkeypatch.setattr(bridge, '_cleanup_bridge_children', lambda *args: cleanup)
    watch = bridge._watch
    def blocked_watch(server, stop, initial, *, on_ready=None):
        def ready():
            on_ready()
            c.write(record / 'bridge-operation.json', dict(boot_id=budget.clock_stamp()['boot_id'],
                deadline_boottime=time.clock_gettime(time.CLOCK_BOOTTIME) + 900))
            marker.touch()
            # Simulate an already accepted operation blocked below stop.wait.
            # SIGTERM must use the existing _BridgeStopped/cleanup path.
            while True:
                time.sleep(.1)
        watch(server, stop, initial, on_ready=ready)
    monkeypatch.setattr(bridge, '_watch', blocked_watch)
    entered = time.monotonic()
    def run():
        with bridge.serve(directory, config_path, work,
                launch=lambda *args: pytest.fail('unexpected local launch'),
                diagnostic_clean=None, cancelled=lambda: True):
            until = time.monotonic() + 2
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.01)
            assert marker.exists()
    with _transport_guard():
        if cleanup == 'cleanup_unknown':
            with pytest.raises(ValueError, match='cleanup is unconfirmed'):
                run()
        else:
            run()
    assert time.monotonic() - entered < 8
    assert c.read(record / 'bridge-outcome.json')['cleanup'] == cleanup
    assert json.loads((directory / 'response.json').read_bytes())['result']['status'] == 'unknown'
    assert {path: path.read_bytes() for path in originals} == originals
    assert (claim / 'submit.json').read_bytes() == raw
    assert not (claim / 'outcome.json').exists() and not (claim / 'response.json').exists()


@pytest.mark.process_e2e
def test_native_observes_interrupt_arriving_after_bridge_shutdown_begins(tmp_path, monkeypatch, execution_registry):
    from scidiscovery.research_loop import backends, budget, execution, profiles, worker_execution_bridge as bridge
    public = tmp_path / 'public'; public.mkdir()
    goal = public / 'goal'; goal.write_text('Late interrupt transport fixture only.')
    launcher = tmp_path / 'launcher.py'
    launcher.write_text('import json,sys\njson.loads(sys.stdin.read())\n'
        'print(json.dumps(dict(type="session",session_id="late-interrupt-original")),flush=True)\n'
        'print(json.dumps(dict(type="completed",usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=1))),flush=True)\n')
    root = tmp_path / 'instance'
    controller.prepare(root, goal, public, [sys.executable, str(launcher)], native_backend='protocol',
        max_tokens=10000, max_seconds=120, max_steps=6, model='fixture-no-model', reasoning_effort='high',
        search_policy=None, experiment_mode='local', node_budget_mode='soft',
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=20) for role in 'HWAE'})
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'late interrupt fixture')
    job = c.build_job(root, 1, 'W', [])
    assert job['worker_view_version'] == 1
    anchor = budget.accept_node(root, job)
    record = execution.origin(root, 1)
    intent = dict(step=1, task_ref=c.record_ref(root / 'jobs/001.json'),
        operator_interrupt_version=native.OPERATOR_INTERRUPT_VERSION,
        **profiles.binding(configuration_updates.for_job(root, job), job), budget_slice=anchor)
    accepted_budget.accept_native(root, 1, record, intent)
    original = (record / 'native-intent.json').read_bytes()
    target = dict(step=1, expected_attempt=0, native_intent_ref=dict(path=str(record / 'native-intent.json')))
    # There is no scientific experiment in this fixture. Keep the real native
    # owner/Backend/bridge lifecycle while isolating unused solver launch work.
    monkeypatch.setattr(backends.Protocol, 'prepare_execution',
        lambda self, *args, **kwargs: (args[5], lambda *args: pytest.fail('unexpected solver launch')))
    def busy_bridge(server, stop, initial, *, on_ready=None):
        c.write(record / 'bridge-operation.json', dict(boot_id=budget.clock_stamp()['boot_id'],
            deadline_boottime=time.clock_gettime(time.CLOCK_BOOTTIME) + 900))
        on_ready()
        while True:
            time.sleep(.1)
    monkeypatch.setattr(bridge, '_watch', busy_bridge)
    shutdown = threading.Event()
    serve = bridge.serve
    @contextmanager
    def tracked_serve(*args, **options):
        observe = options['cancelled']
        def at_shutdown():
            shutdown.set()
            return observe()
        options['cancelled'] = at_shutdown
        with serve(*args, **options) as directory:
            yield directory
    monkeypatch.setattr(bridge, 'serve', tracked_serve)
    results = []; errors = []
    def interrupt_after_shutdown():
        try:
            if not shutdown.wait(8):
                raise TimeoutError('native owner never entered bridge shutdown')
            assert not (record / 'interrupt.request').exists()
            results.append(instance_control.request_interrupt(root, **target))
        except BaseException as error:
            errors.append(error)
    thread = threading.Thread(target=interrupt_after_shutdown, daemon=True)
    started = time.monotonic()
    try:
        with controller._writer(root), _transport_guard():
            thread.start()
            with pytest.raises(RuntimeError, match='not a verified terminal result'):
                native.run_native(root, 1)
            thread.join(3)
    finally:
        if (record / 'bridge-process.json').exists():
            identity = c.read(record / 'bridge-process.json')
            if runtime.process_matches(identity, identity['task_ref']):
                runtime.stop_bound_group(identity, identity['task_ref'])
            try: os.waitpid(identity['pid'], os.WNOHANG)
            except ChildProcessError: pass
    assert not thread.is_alive() and not errors
    assert results[0]['status'] == 'interrupt_requested'
    assert time.monotonic() - started < 10
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['native_started'] is True and outcome['returncode'] == 0
    assert outcome['terminal_observed'] is True and outcome['stop_reason'] == 'operator_interrupt'
    assert outcome['cleanup'] == 'observed_group_cleaned'
    assert c.read(record / 'bridge-outcome.json')['cleanup'] == 'observed_group_cleaned'
    assert (record / 'native-intent.json').read_bytes() == original
    assert (record / 'interrupt.request').exists() and (root / 'pause.request').exists()
    assert not (record / 'science-output.json').exists() and not (record / 'result-identity.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('reported',[True,False])
def test_real_w_interrupt_explicit_resume_seals_and_returns_to_original_control(tmp_path,monkeypatch,reported):
    public=tmp_path/'public';public.mkdir()
    goal=public/'goal';goal.write_text('Mechanical interruption/continuation fixture; no scientific qualification.')
    launcher=tmp_path/'native.py'
    launcher.write_text('REPORT_USAGE='+repr(reported)+'\n'+r'''import json,os,pathlib,sys,time
request=json.loads(sys.stdin.read())
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
checkpoint=work/'work/original-checkpoint'
print(json.dumps(dict(type='session',session_id='protocol-original')),flush=True)
if not checkpoint.exists():
 checkpoint.write_text('retained same W work')
 if REPORT_USAGE:
  print(json.dumps(dict(type='usage',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=1))),flush=True)
 time.sleep(60)
else:
 assert checkpoint.read_text()=='retained same W work'
 assert 'Operator explicitly continued this same interrupted task' in request['prompt']
 payload=dict(summary='Continued original W',scientific_result='The original checkpoint was retained; transport fixture only.',
  limitations=['No scientific experiment or qualification in this mechanical fixture.'],implementation_selection=None,
  mechanism_findings=None,stage_intent='review',
  consultation_question='Does the retained checkpoint and explicit continuation record establish only transport continuity, with scientific evidence and qualification still missing?')
 task=json.loads((work/'science-task.json').read_text())
 nominations=task.get('candidate_nomination_mechanisms',[])
 if nominations:
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='No scientifically suitable candidate is established by this transport fixture.') for row in nominations if row['status']!='waived'])
 print(json.dumps(dict(type='result',result=payload)),flush=True)
 print(json.dumps(dict(type='completed',usage=dict(input_tokens=11,cached_input_tokens=2,output_tokens=3))),flush=True)
''')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[sys.executable,str(launcher)],native_backend='protocol',experiment_mode='external',
        max_tokens=10000,max_seconds=120,max_steps=6,model='fixture-no-model',reasoning_effort='high',search_policy=None,
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=20) for role in 'HWAE'})
    job=c.build_job(root,1,'W',[])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','operator resume fixture')
    record=execution.origin(root,1)
    target=dict(step=1,expected_attempt=0,native_intent_ref=dict(path=str(record/'native-intent.json')))
    errors=[]
    def interrupt():
        try:
            until=time.monotonic()+8
            while (not (c.workspace(root,1)/'work/original-checkpoint').exists()
                    or not (record/'native-process.json').exists()
                    or reported and not (record/'usage.json').exists()):
                if time.monotonic()>=until:raise TimeoutError('original W never reached its retained checkpoint')
                time.sleep(.02)
            assert instance_control.request_interrupt(root,**target)['status']=='interrupt_requested'
        except BaseException as error:errors.append(error)
    thread=threading.Thread(target=interrupt,daemon=True)
    with controller._writer(root),_transport_guard():
        thread.start()
        with pytest.raises((RuntimeError,ValueError)):native.execute_native(root,1)
        thread.join(3)
        assert not thread.is_alive() and not errors
        until=time.monotonic()+3
        while runtime.process_matches(c.read(record/'runner-process.json'),dict(path=str(root/'jobs/001.json'))):
            assert time.monotonic()<until
            time.sleep(.02)
        originals={path:path.read_bytes() for path in (record/'native-intent.json',record/'native-outcome.json',record/'interrupt.request')}
        if reported:originals[record/'usage.json']=(record/'usage.json').read_bytes()
        outcome=c.read(record/'native-outcome.json')
        assert outcome['stop_reason']=='operator_interrupt'
        assert outcome['native_started'] is True
        assert (record/'usage.json').exists() is reported
        next_calls=[]
        def next_control(instance):
            assert (record/'result-identity.json').exists()
            next_calls.append(instance)
            return dict(status='fixture_original_next_control')
        monkeypatch.setattr(controller,'_run',next_control)
        result=controller._worker_resume_locked(root,1,**resume_options(target))
    assert result['status']=='fixture_original_next_control' and next_calls==[root]
    successor=record/'continuations/001'
    intent=c.read(successor/'native-intent.json')
    assert intent['context_mode']=='new_context' and intent['continuation_kind']=='operator_resume'
    assert intent['budget_slice']['previous_tokens']==(8 if reported else 0)
    assert intent['budget_slice']['previous_seconds']==outcome['budget_slice']['advance_seconds']
    assert c.read(successor/'usage.json')==dict(input_tokens=11,cached_input_tokens=2,output_tokens=3)
    assert native.usage(root)['observed_tokens']==(22 if reported else 14)
    assert 1 in native.usage(root)['unknown_steps']
    assert (record/'usage.json').exists() is reported
    assert c.read(successor/'native-outcome.json')['native_permissions']==outcome['native_permissions']
    assert c.read(record/'result.json')['role']=='W'
    assert (c.workspace(root,1)/'work/original-checkpoint').read_text()=='retained same W work'
    assert {path:path.read_bytes() for path in originals}==originals
