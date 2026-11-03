"""A retained failed-turn meter does not close later authorized exploration."""
import sys

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, budget
from scidiscovery.research_loop import contracts as c, controller, execution, native
from check_accepted_budget_head import prepare
from test_automatic_recovery import AUTOMATIC_NATIVE
import test_default_cli_output_repair as default_cli


@pytest.mark.process_e2e
def test_capacity_failure_then_matched_continuation_admits_next_w(tmp_path, monkeypatch):
    policy = tmp_path/'delivery.json'
    c.write(policy,dict(closeout_a=dict(max_observed_tokens=1000,max_seconds=60),
        inflight_tokens=0,cleanup_seconds=0,submit_seconds=0,confirm_seconds=0,adapter=None))
    cli = default_cli.cli

    def prepare_with_delivery(root, *arguments, **options):
        assert arguments[0] == 'prepare'
        return cli(root,*arguments,'--delivery-policy',str(policy),**options)

    with monkeypatch.context() as setup:
        setup.setattr(default_cli,'cli',prepare_with_delivery)
        root, home, env = default_cli.prepare_cli(tmp_path,fault='retrycapacity')
    program = AUTOMATIC_NATIVE.replace(
        "next_action='analyze' if role=='H' else 'finish'",
        "next_action='analyze' if role=='H' else 'continue_w'")
    program = program.replace("if role=='W':payload['stage_intent']='review'",
        "if role=='W':payload.update(stage_intent='decision',mechanism_findings=None,candidate_nominations=None)")
    (tmp_path/'native.py').write_text('#!'+sys.executable+'\n'+program)
    for key in ('CODEX_HOME','PATH','PYTHONPATH'):
        monkeypatch.setenv(key,env[key])
    monkeypatch.setattr(controller,'_recovery_remaining',lambda *_:0.)
    settings = c.settings(root)
    assert settings['delivery_policy'] is not None
    frozen = {path:path.read_bytes() for path in (root/'settings.json',home/'config.toml',home/'auth.json')}
    failed_originals = {}
    wait = controller._wait_automatic_recovery

    def retain_then_resume(root, step, decision, *, inventory):
        assert step == 2 and decision['action'] == 'retry'
        record = execution.origin(root,step)
        failed_originals.update({record/name:(record/name).read_bytes() for name in
            ('native-intent.json','native-outcome.json','usage.json','native.jsonl')})
        return wait(root,step,decision,inventory=inventory)

    monkeypatch.setattr(controller,'_wait_automatic_recovery',retain_then_resume)
    execute = native.execute_native
    admitted = []

    def pause_after_next_w(root, step, **options):
        if step == 3:
            job = c.read(root/'jobs/003.json')
            assert job['role'] == 'W' and not job.get('final_only')
            assert budget.exploration_admission(root,job) is None
            assert budget.node_admission(root,job) is None
            admitted.append(step)
        result = execute(root,step,**options)
        if step == 3:
            (root/'pause.request').write_text('Pause after the recovered route admitted its next W.')
        return result

    monkeypatch.setattr(native,'execute_native',pause_after_next_w)
    result = controller.run(root)
    assert result['status'] == 'paused' and result['completed_steps'] == 3, result
    assert admitted == [3] and budget.final_a_barrier(root) is None
    assert not (root/'jobs/004.json').exists()
    first, recovered = execution.records(root,2)
    original = c.read(first/'native-outcome.json')
    completed = c.read(recovered/'native-outcome.json')
    assert original['returncode'] != 0 and original['native_failure']['codes'] == ['capacity_exceeded']
    assert original['metering']['coverage'] == 'unknown'
    assert c.read(first/'usage.json') == dict(input_tokens=10,cached_input_tokens=3,output_tokens=4)
    assert completed['returncode'] == 0 and completed['metering']['coverage'] == 'terminal'
    assert completed['metering']['reconciliation'] == 'matched'
    assert c.validate(c.workspace(root,2))['next_action'] == 'continue_w'
    request = c.read(recovered/'native-intent.json')
    assert request['previous'] == execution.proof(first)
    assert request['task_ref'] == c.read(first/'native-intent.json')['task_ref']
    assert request['budget_slice']['previous_tokens'] == 14
    usage = native.usage(root)
    assert usage['observed_tokens'] == usage['accounted_tokens'] == 56
    assert usage['unknown_steps'] == [2] and usage['unknown_step_count'] == 1
    assert usage['blocking_unknown_steps'] == [] and usage['blocking_unknown_count'] == 0
    assert usage['reserved_tokens'] == 0 and usage['reserved_unknown_steps'] == []
    assert budget.used_steps(root) == 4
    assert (c.workspace(root,2)/'work/launches').read_text() == 'exec\nresume\n'
    assert failed_originals and all(path.read_bytes() == data for path,data in failed_originals.items())
    assert all(path.read_bytes() == data for path,data in frozen.items())


def test_current_unknown_original_blocks_execution_without_a_metering_block(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    c.build_job(root,1,'H',[])
    record = execution.origin(root,1)
    accepted.accept_native(root,1,record,dict(step=1,task_ref=c.record_ref(root/'jobs/001.json')))
    meter = dict(input_tokens=17,cached_input_tokens=2,output_tokens=3)
    c.write(record/'usage.json',meter)
    c.write(record/'usage-observation.json',dict(coverage='unknown',reason='terminal_usage_unreported',tokens=meter))
    originals = {path:path.read_bytes() for path in (root/'jobs/001.json',record/'native-intent.json',record/'usage.json')}
    usage = native.usage(root)
    assert usage['unknown_steps'] == [1] and usage['observed_tokens'] == 20
    assert usage['blocking_unknown_count'] == 0 and usage['blocking_unknown_steps'] == []
    assert budget.execution_blocked(root)
    with pytest.raises(RuntimeError,match='running or unknown original execution'):
        budget.amend(root,max_tokens=1200,authorization_note='An unresolved original cannot be replaced.')
    monkeypatch.setattr(native.subprocess,'Popen',lambda *a,**k:pytest.fail('An unknown original must not be submitted again'))
    result = controller.run(root)
    assert result['status'] == 'native_outcome_unsealed', result
    assert not (record/'continuations').exists()
    assert not (root/'jobs/002.json').exists()
    assert all(path.read_bytes() == data for path,data in originals.items())
    assert native.usage(root) == usage
