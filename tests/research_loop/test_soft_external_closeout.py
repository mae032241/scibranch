"""Final A completion uses frozen policy, never additional shared allowance.

Native/adapter processes are deterministic transport fixtures, not science.
"""
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, execution, native, role_share
from test_history_handoff import finish
import test_closeout_protocol as protocol


TERMINAL = "print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)"
W_BRANCH = '''if job['role']=='W':
 payload=dict(summary='Returned the bounded original W finding',scientific_result='Retained observations only; no scientific qualification.',
  limitations=['Deterministic native transport fixture'],implementation_selection=None,mechanism_findings=None,stage_intent='review')
 pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
 print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=99500,cached_input_tokens=2,output_tokens=0))),flush=True)
 raise SystemExit(0)
'''


LIVE_W_BRANCH = r"""if job['role']=='W':
 import time
 thread='12345678-1111-4111-8111-123456789abc'
 print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
 home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
 path=home/'sessions'/('rollout-'+thread+'.jsonl');path.parent.mkdir(parents=True,exist_ok=True)
 path.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n'+json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=dict(input_tokens=TOKENS,cached_input_tokens=2,output_tokens=0)))))+'\n')
 time.sleep(5)
 raise SystemExit(0)
"""


def external_scene(tmp_path, monkeypatch, *, mode='soft', action='deliver', accounted_worker=None, max_steps=32, live_worker_tokens=99500, terminal_worker=True):
    worker = W_BRANCH.replace('input_tokens=99500', f'input_tokens={live_worker_tokens}')
    if not terminal_worker:
        worker = LIVE_W_BRANCH.replace('TOKENS', str(live_worker_tokens))
    source = protocol.NATIVE.replace("assert job['role']=='A'", worker + "assert job['role']=='A'")
    source = source.replace("index=manifest['step']-FIRST_STEP", 'index=0')
    source = source.replace(TERMINAL, "print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=280900,cached_input_tokens=100000,output_tokens=34))),flush=True)")
    monkeypatch.setattr(protocol, 'NATIVE', source)
    if accounted_worker is not None:
        def retained_original(root, step, role, parents, **kwargs):
            if role != 'W':
                return finish(root, step, role, parents, **kwargs)
            # Publish known terminal original facts before sealing, just as the
            # shared closeout fixture does; no sealed receipt is rewritten.
            if not (root/'jobs'/f'{step:03d}.json').exists():
                c.build_job(root, step, role, parents)
            work = c.workspace(root, step);record = execution.record(root, step)
            for name, content in kwargs.get('files', {}).items():
                path = work/'artifacts'/name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(content)
            c.write(record/'native-intent.json', dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
            payload = dict(summary='Retained W conclusion', scientific_result='Known original transport facts only',
                limitations=['Fixture originals; no scientific qualification'], implementation_selection=None,
                mechanism_findings=None, stage_intent='review')
            c.write(record/'science-output.json', payload)
            c.write(record/'usage.json', dict(input_tokens=accounted_worker, cached_input_tokens=2, output_tokens=0))
            c.write(record/'native-outcome.json', dict(returncode=0, stop_reason=None, usage=native.usage(root),
                raw_ref=c.file_ref(record/'science-output.json')))
            c.seal_result(root, step)
        monkeypatch.setattr(protocol, 'finish', retained_original)
    original_prepare = controller.prepare
    def prepare(*args, **kwargs):
        return original_prepare(*args, **dict(kwargs, max_steps=max_steps))
    monkeypatch.setattr(controller, 'prepare', prepare)
    return protocol.scene(tmp_path, [dict(action=action, candidate='run-1')], node_budget_mode=mode)


def expire_original_clock(root, monkeypatch):
    stamp = budget.clock_stamp()
    stamp = dict(stamp, at_unix=stamp['at_unix']-1000, at_boottime=stamp['at_boottime']-1000)
    c.write(root/'host-started.json', stamp)
    with monkeypatch.context() as patch:
        patch.setattr(budget, 'clock_stamp', lambda: stamp)
        budget.transition(root, 'advance', 'known original active time consumed')
    assert budget.snapshot(root)['seconds_remaining'] == 0


def assert_completed_original(root, result, *, step=3):
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    event = budget.final_a_barrier(root)
    assert event['basis'] == 'final_state' and event['step'] == step
    record = execution.record(root, step)
    outcome = c.read(record/'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['final_a_completion']['final_overrun']['shared_tokens_over_ceiling'] > 0
    assert c.validate(c.workspace(root, step))['scientific_result'].startswith('Exact public evidence')
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    assert all(row['intent']['final_return'] == c.read(record/'native-intent.json')['budget_slice']['final_return'] for row in actions)
    originals = {path: path.read_bytes() for path in (record/'native-intent.json', record/'native-outcome.json', record/'result-identity.json')}
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert len(budget.closeout_state(root)['actions']) == 2
    assert not (root/'jobs'/f'{step+1:03d}.json').exists()
    last = actions[-1]['intent']
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_a_delivery_already_confirmed'):
        budget.accept_action(root, 'confirm', last['operation_id'], last['decision'], last['binding'], 10)
    assert not any(c.read(path)['kind'] == 'budget_amend' for path in (root/'control-ledger').glob('*.json'))


def test_soft_external_real_w_crosses_target_and_reserve_then_final_a_delivers(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    finish(root, 3, 'H', [2], action='experiment')
    job = c.build_job(root, 4, 'W', [3])
    c.write(root/'host-started.json', budget.clock_stamp());budget.transition(root, 'advance', 'real soft W return')
    native.execute_native(root, 4)
    outcome = c.read(execution.record(root, 4)/'native-outcome.json')
    assert outcome['stop_reason'] is None and outcome['budget_slice']['observed_tokens'] == 99500
    assert budget.exploration_stop(root, job, budget.snapshot(root)) is None
    assert 0 < budget.snapshot(root)['max_tokens']-native.usage(root)['accounted_tokens'] < 1100
    assert c.validate(c.workspace(root, 4))['summary'] == 'Returned the bounded original W finding'
    assert_completed_original(root, controller.closeout(root), step=5)


@pytest.mark.parametrize('mode,worker_tokens', [('soft', 99990), ('soft', 100500)])
def test_final_a_enters_at_zero_or_negative_original_balance_and_delivers(tmp_path, monkeypatch, mode, worker_tokens):
    root = external_scene(tmp_path, monkeypatch, mode=mode, accounted_worker=worker_tokens)
    settings = (root/'settings.json').read_bytes()
    assert native.usage(root)['observed_tokens'] == worker_tokens+10
    assert native.usage(root)['observed_tokens'] >= budget.snapshot(root)['max_tokens']
    assert_completed_original(root, controller.closeout(root))
    assert (root/'settings.json').read_bytes() == settings


def test_final_a_and_original_actions_survive_exhausted_shared_wall_and_steps(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, max_steps=3)
    # All originally granted steps were consumed by retained known originals.
    finish(root, 3, 'H', [], action='wait')
    expire_original_clock(root, monkeypatch)
    settings = (root/'settings.json').read_bytes()
    assert_completed_original(root, controller.closeout(root), step=4)
    assert budget.effective_max_steps(root) == 3 and budget.used_steps(root) == 6
    assert budget.snapshot(root)['consumed_seconds'] > budget.snapshot(root)['max_seconds']
    hint = c.read(c.workspace(root, 4)/'work/budget-status.json')
    assert hint['current_work_active_seconds_available'] > 0
    assert 'shared time and step exhaustion do not revoke' in hint['instruction']
    assert native.usage(root)['observed_tokens'] > 280934
    assert (root/'settings.json').read_bytes() == settings


def test_final_a_cannot_decline_available_originals_with_a_casual_finish(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, action='finish', accounted_worker=100500)
    assert controller.closeout(root)['status'] == 'closeout_native_unsealed'
    assert c.read(execution.record(root, 3)/'science-output.json')['next_action'] == 'finish'
    assert not (execution.record(root, 3)/'result-identity.json').exists()
    assert budget.closeout_state(root)['actions'] == []
    assert not (root/'runs/submitted.json').exists()


def test_absent_completion_policy_does_not_grant_overbudget_author(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    settings = c.read(root/'settings.json');settings.pop('final_a_completion_policy')
    c.write(root/'settings.json', settings);c.write(root/'settings-identity.json', dict(path='settings.json'))
    assert controller.closeout(root)['status'] == 'closeout_budget_unavailable'
    assert not (root/'jobs/003.json').exists()
    assert budget.final_a_barrier(root) is None


@pytest.mark.parametrize('boundary', ['tokens', 'wall'])
def test_soft_accepted_author_finishes_over_shared_limit_then_only_final_submits(tmp_path, monkeypatch, boundary):
    source = protocol.NATIVE.replace("next_action=item['action']",
        "next_action='deliver' if manifest.get('final_only') else 'revise_h'")
    # The accepted ordinary A is still alive after crossing shared time. This
    # deterministic launcher performs no model request or scientific execution.
    if boundary == 'wall':
        source = source.replace("payload=dict(summary='A bounded choice'",
            "import time\nif not manifest.get('final_only'):time.sleep(6)\npayload=dict(summary='A bounded choice'")
    monkeypatch.setattr(protocol, 'NATIVE', source)
    root = external_scene(tmp_path, monkeypatch, accounted_worker=90000)
    if boundary == 'wall':
        # Retain the ordinary valid delivery reservation and record previously
        # consumed active time, leaving five seconds at the normal launch gate.
        stamp = budget.clock_stamp()
        stamp = dict(stamp, at_unix=stamp['at_unix']-595, at_boottime=stamp['at_boottime']-595)
        c.write(root/'host-started.json', stamp)
        with monkeypatch.context() as original_clock:
            original_clock.setattr(budget, 'clock_stamp', lambda: stamp)
            budget.transition(root, 'advance', 'Fixture previously consumed active time')
    settings = (root/'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    ordinary = execution.origin(root, 3)
    outcome = c.read(ordinary/'native-outcome.json')
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert outcome['metering']['coverage'] == 'terminal'
    assert c.read(ordinary/'usage.json')['input_tokens'] == 280900
    assert (ordinary/'result-identity.json').is_file()
    assert c.validate(c.workspace(root, 3))['next_action'] == 'revise_h'
    marker = budget.final_a_barrier(root)
    assert marker['step'] == 4
    assert c.read(root/'jobs/004.json')['final_only']
    assert sorted(path.stem for path in (root/'jobs').glob('*.json')) == ['001', '002', '003', '004']
    assert result['usage']['observed_tokens'] == 90010 + 2 * 280934
    assert result['usage']['blocking_unknown_steps'] == []
    assert result['usage']['observed_tokens'] > result['budget']['max_tokens']
    if boundary == 'wall':
        assert budget.snapshot(root)['consumed_seconds'] > budget.snapshot(root)['max_seconds']
    assert [row['intent']['action_kind'] for row in budget.closeout_state(root)['actions']] == ['submit', 'confirm']
    assert (root/'settings.json').read_bytes() == settings
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert not (root/'jobs/005.json').exists()


def test_unknown_final_delivery_preserves_operation_and_never_resubmits(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500)
    calls = []
    def unknown(root, intent, remaining, **kwargs):
        calls.append((intent['action_kind'], intent['operation_id']))
        return delivery._result(intent['binding'], 'unknown', 'Original operation remains unverified')
    monkeypatch.setattr(delivery, 'invoke', unknown)
    original = None
    for number in range(1, 4):
        assert controller.closeout(root)['status'] == 'delivery_unknown'
        # Each explicit request may reconcile the exact original operation once.
        assert [kind for kind, _ in calls] == ['submit'] + ['confirm'] * (number - 1)
        actions = budget.closeout_state(root)['actions']
        assert len(actions) == number
        if original is None:
            original = actions[0]
        assert actions[0] == original
        assert all(row['intent']['operation_id'] == original['intent']['operation_id']
            and row['intent']['binding'] == original['intent']['binding']
            and row['intent']['decision'] == original['intent']['decision']
            and row['outcome']['result']['status'] == 'unknown' for row in actions)
        assert len({operation for _, operation in calls}) == 1
    assert not (root/'jobs/004.json').exists()


@pytest.mark.parametrize('boundary', ['tokens', 'wall'])
def test_prepared_ordinary_a_is_retained_before_an_explicit_final_assignment(tmp_path, monkeypatch, boundary):
    root = external_scene(tmp_path, monkeypatch, accounted_worker=100500 if boundary == 'tokens' else None)
    job = c.build_job(root, 3, 'A', [1, 2])
    job_path = root/'jobs/003.json';original = job_path.read_bytes()
    assert job.get('purpose') is None and not (execution.record(root, 3)/'native-intent.json').exists()
    if boundary == 'wall':
        expire_original_clock(root, monkeypatch)
    result = controller.run(root)
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['basis'] == 'final_state'
    assert budget.final_a_barrier(root)['step'] == 4
    assert job_path.read_bytes() == original and not (root/'jobs/005.json').exists()
    assert not (execution.record(root, 3)/'native-intent.json').exists()
    assert len(budget.closeout_state(root)['actions']) == 2
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert job_path.read_bytes() == original


def test_prepared_adapter_off_a_is_not_replaced_when_tokens_already_exceeded(tmp_path):
    from test_final_a_completion import instance
    root = instance(tmp_path, max_tokens=5, max_steps=6, mode='soft', route='analyze')
    c.build_job(root, 2, 'A', [1], instruction='Exact scientific question')
    job_path = root/'jobs/002.json';original = job_path.read_bytes()
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['basis'] == 'final_state'
    assert budget.final_a_barrier(root)['step'] == 3
    assert job_path.read_bytes() == original and not (root/'jobs/004.json').exists()
    assert not (execution.record(root, 2)/'native-intent.json').exists()
    assert budget.closeout_state(root)['actions'] == []


def test_sealed_nonfinal_delivery_without_remaining_budget_gets_one_final_author(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    finish(root, 3, 'A', [1, 2], action='deliver', recommendation='run-1')
    original = (execution.record(root, 3)/'result-identity.json').read_bytes()
    expire_original_clock(root, monkeypatch)
    result = controller.run(root)
    assert result['status'] == 'delivery_confirmed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 4
    assert (execution.record(root, 3)/'result-identity.json').read_bytes() == original
    assert not (root/'jobs/005.json').exists()


@pytest.mark.parametrize('adapter_off_policy', [False, True])
def test_role_share_only_stop_still_returns_final_report(tmp_path, monkeypatch, adapter_off_policy):
    from test_final_a_completion import instance
    original_prepare = controller.prepare
    def prepare(*args, **kwargs):
        kwargs['role_budget_policy'] = dict(version=1, min_w_share=.99, max_thinking_share=.00001,
            thinking_node_target_tokens=100000)
        if adapter_off_policy:
            kwargs['delivery_policy'] = dict(closeout_a=dict(max_observed_tokens=275000, max_seconds=10),
                inflight_tokens=0, cleanup_seconds=0, submit_seconds=0, confirm_seconds=0, adapter=None)
        return original_prepare(*args, **kwargs)
    monkeypatch.setattr(controller, 'prepare', prepare)
    root = instance(tmp_path, max_steps=6, mode='soft', route='analyze')
    assert budget.snapshot(root)['max_tokens']-native.usage(root)['observed_tokens'] > 900000
    job = c.read(root/'jobs/001.json')
    result = c.validate(c.workspace(root, 1))
    assert role_share.usage(root, 1)['thinking'] == 10
    assert role_share.reason(root, 1, job, result, 'A') == 'role_share_thinking_reached'
    originals = {path:path.read_bytes() for path in (root/'settings.json', root/'jobs/001.json',
        execution.record(root, 1)/'usage.json', execution.record(root, 1)/'result-identity.json')}
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['basis'] == 'final_state'
    begin = budget.closeout_state(root)['begin']
    assert begin['stop_reason'] == 'role_share_thinking_reached' and begin['source_cutoff'] == 1
    assert (c.workspace(root, 2)/'work/launches').read_text() == 'A\n'
    assert budget.closeout_state(root)['actions'] == []
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not (root/'jobs/003.json').exists()


def test_postpreflight_budget_race_preserves_unused_w_and_returns_final_report(tmp_path, monkeypatch):
    from test_final_a_completion import instance
    root = instance(tmp_path, max_steps=6, mode='soft', route='experiment')
    original_build = c.build_job
    original_limit = controller._launch_limit
    unused = []
    def build(*args, **kwargs):
        job = original_build(*args, **kwargs)
        if job['role'] == 'W':
            unused.append((root/'jobs/002.json').read_bytes())
        return job
    def raced_limit(root, settings):
        return 'wall_limit' if unused else original_limit(root, settings)
    monkeypatch.setattr(c, 'build_job', build)
    monkeypatch.setattr(controller, '_launch_limit', raced_limit)
    result = controller.run(root)
    assert result['status'] == 'final_a_completed', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 3
    assert (root/'jobs/002.json').read_bytes() == unused[0]
    assert not (execution.record(root, 2)/'native-intent.json').exists()
    report = c.read(root/'jobs/003.json')['final_report']
    assert report['source_cutoff'] == 1 and report['stopped_original']['records'] == {}
    assert [ref['path'] for ref in c.read(root/'jobs/003.json')['direct_refs']] == ['inputs/parents/001/conclusion.json']
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_a_return_consumed'):
        native.execute_native(root, 2)
    assert not (execution.record(root, 2)/'native-intent.json').exists()


def retain_general_closeout(root):
    nodes = [dict(step=number, seal_ref=c.record_ref(execution.record(root, number)/'result-identity.json')) for number in range(1, 4)]
    budget.begin_closeout(root, 3, 4, 'retained_model_closeout', delivery_sources=delivery.catalogue_sources(root, nodes), authorization_step=3)


@pytest.mark.parametrize('ordinary,restart', [(False, False), (False, True), (True, True)])
def test_prepared_repair_budget_stop_preserves_unused_w_and_only_runs_final_a(tmp_path, monkeypatch, ordinary, restart):
    import test_audit_engineering as engineering_fixture
    program = "SCENARIO=[dict(action='deliver',candidate='run-1')]\nFIRST_STEP=5\n"+protocol.NATIVE
    monkeypatch.setattr(engineering_fixture, 'NATIVE', program)
    root, _ = engineering_fixture.repair_scene(tmp_path, ordinary=ordinary)
    if not ordinary:
        retain_general_closeout(root)
    execute = native.execute_native
    def stop_before_effect(root, step, **kwargs):
        job = c.read(root/'jobs'/f'{step:03d}.json')
        if job['role'] == 'W':
            raise RuntimeError('Fixture host interruption before unused W submission')
        return execute(root, step, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(native, 'execute_native', stop_before_effect)
        assert controller.run(root)['status'] in {'native_stopped', 'closeout_native_unsealed'}
    original = (root/'jobs/004.json').read_bytes()
    assert not (execution.record(root, 4)/'native-intent.json').exists()
    budget.amend(root, max_seconds=.0001, authorization_note='Fixture exhausts shared wall after preserving the unused W.')
    if restart:
        def stop_before_author(root, step, **kwargs):
            assert step == 5 and c.read(root/'jobs/005.json')['role'] == 'A'
            raise RuntimeError('Fixture interruption before final author submission')
        with monkeypatch.context() as patch:
            patch.setattr(native, 'execute_native', stop_before_author)
            assert controller.run(root)['status'] == 'closeout_native_unsealed'
        prepared = (root/'jobs/005.json').read_bytes()
        budget.amend(root, max_seconds=2000, authorization_note='Fixture adds time; unused W must remain retired.')
    result = controller.run(root)
    if ordinary and restart:
        assert result['status'] == 'engineering_repair_exhausted', json.dumps(result, indent=2)
        assert budget.closeout_state(root)['begin'] is None
        assert budget.normal_resume(root) is not None
        assert budget.final_a_barrier(root) is None
        assert not (execution.record(root,5)/'native-intent.json').exists()
        assert not (root/'jobs/006.json').exists()
        assert (root/'jobs/004.json').read_bytes() == original
        assert not (execution.record(root,4)/'native-intent.json').exists()
        assert (root/'jobs/005.json').read_bytes() == prepared
        return
    assert result['status'] == 'delivery_engineering_error', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 5
    assert (root/'jobs/004.json').read_bytes() == original
    if restart:
        assert (root/'jobs/005.json').read_bytes() == prepared
    assert not (execution.record(root, 4)/'native-intent.json').exists()
    assert not (root/'jobs/006.json').exists()
    assert 'science-task.json' in (c.workspace(root, 5)/'work/native-prompt').read_text()
    assert 'unmet objectives and missing work' in c.read(c.workspace(root, 5)/'science-task.json')['question']
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_a_return_consumed'):
        native.execute_native(root, 4)


@pytest.mark.parametrize('ordinary', [False, True])
def test_repair_native_admission_race_reaches_final_a_in_same_call(tmp_path, monkeypatch, ordinary):
    import test_audit_engineering as engineering_fixture
    monkeypatch.setattr(engineering_fixture, 'NATIVE',
        "SCENARIO=[dict(action='deliver',candidate='run-1')]\nFIRST_STEP=5\n"+protocol.NATIVE)
    root, _ = engineering_fixture.repair_scene(tmp_path, ordinary=ordinary)
    if not ordinary:
        retain_general_closeout(root)
    execute = native.execute_native
    def unavailable(root, step, **kwargs):
        if c.read(root/'jobs'/f'{step:03d}.json')['role'] == 'W':
            raise budget.NodeBudgetUnavailable('node_budget_unavailable_seconds')
        return execute(root, step, **kwargs)
    monkeypatch.setattr(native, 'execute_native', unavailable)
    result = controller.run(root)
    assert result['status'] == 'delivery_engineering_error', json.dumps(result, indent=2)
    assert budget.final_a_barrier(root)['step'] == 5
    assert not (execution.record(root, 4)/'native-intent.json').exists()
    assert not (root/'jobs/006.json').exists()


def test_unsubmitted_host_action_budget_race_selects_final_a_before_delivery(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    finish(root, 3, 'A', [1, 2], action='deliver', recommendation='run-1')
    accept = budget.accept_action;snapshot = budget.snapshot
    exhausted = [False]
    def limits(*args, **kwargs):
        value = snapshot(*args, **kwargs)
        if exhausted[0]:
            value['seconds_remaining'] = 0
        return value
    def action(*args, **kwargs):
        if not exhausted[0]:
            exhausted[0] = True
            raise budget.NodeBudgetUnavailable('closeout_budget_unavailable')
        return accept(*args, **kwargs)
    monkeypatch.setattr(budget, 'snapshot', limits)
    monkeypatch.setattr(budget, 'accept_action', action)
    result = controller.run(root)
    assert result['status'] == 'closeout_budget_unavailable', json.dumps(result, indent=2)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    assert budget.final_a_barrier(root)['step'] == 4
    assert [row['intent']['decision']['step'] for row in budget.closeout_state(root)['actions']] == [4, 4]
    assert not (root/'jobs/005.json').exists()


def test_confirm_budget_denial_after_submit_never_claims_unsubmitted_or_spawns_a(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    finish(root, 3, 'A', [1, 2], action='deliver', recommendation='run-1')
    accept = budget.accept_action
    def deny_confirmation(root, kind, *args, **kwargs):
        if kind == 'confirm':
            raise budget.NodeBudgetUnavailable('closeout_budget_unavailable')
        return accept(root, kind, *args, **kwargs)
    monkeypatch.setattr(budget, 'accept_action', deny_confirmation)
    for _ in range(2):
        result = controller.run(root)
        assert result['status'] == 'delivery_unknown'
        assert result.get('reason') != 'final_author_required'
        assert budget.final_a_barrier(root)['step'] == 4
        assert not (root/'jobs/005.json').exists()
        actions = budget.closeout_state(root)['actions']
        assert [row['intent']['action_kind'] for row in actions] == ['submit']
        assert actions[0]['outcome']['result']['status'] == 'confirmed'
    operation = actions[0]['intent']['operation_id']
    monkeypatch.setattr(budget, 'accept_action', accept)
    assert controller.run(root)['status'] == 'delivery_confirmed'
    actions = budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions] == ['submit', 'confirm']
    assert all(row['intent']['operation_id'] == operation for row in actions)
    assert not (root/'jobs/005.json').exists()
