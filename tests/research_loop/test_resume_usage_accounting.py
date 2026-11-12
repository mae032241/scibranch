"""Current same-session attempts persist their proven delta once."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, continuation, controller, native, execution


BASELINE = dict(input_tokens=2017365, cached_input_tokens=1926656, output_tokens=22940)
DELTA = dict(input_tokens=368476, cached_input_tokens=253440, output_tokens=2042)


@pytest.mark.process_e2e
def test_real_resume_transport_counts_attempt_delta_and_retains_raw_terminal(tmp_path, monkeypatch):
    from test_native_cli import metering_instance
    from test_worker_resume import wait_runner
    root=metering_instance(tmp_path,monkeypatch,'resume',baseline=BASELINE,delta=DELTA)
    stopped=controller.run(root)
    first=wait_runner(root)
    outcome=c.read(first/'native-outcome.json')
    diagnostic={key:outcome.get(key) for key in ('returncode','stop_reason',
        'terminal_observed','technical_diagnostic','metering')}
    assert stopped['status']=='technical_pause',dict(result=stopped,original=diagnostic)
    assert outcome['returncode']==1 and outcome['stop_reason']=='native_failure_event',diagnostic
    assert outcome['native_started'] is True and outcome['terminal_observed'] is False
    assert outcome['cleanup']=='observed_group_cleaned'
    assert outcome['technical_diagnostic']==dict(phase='turn_completion',code='native_execution_stopped',
        original_call_state='terminal',thread_bound=True,turn_bound=True,
        next_action='inspect_terminal_original'),diagnostic
    session=outcome['native_identity']['session']
    assert session['source']=='native_metadata_hooks' and session['thread_id']=='metering-thread'
    assert session['workspace']==str(c.workspace(root,1)) and session['home']==str(root.parent/'native-home')
    assert c.read(first/'usage.json')==BASELINE
    assert outcome['budget_notification']['status']=='available'
    originals={path:path.read_bytes() for path in first.iterdir() if path.is_file()}
    # Explicit same-call recovery retains the failed native terminal and its
    # independent hook sum; it creates no replay of scientific managed work.
    result=continuation.start(root,1,after_interrupt=True,expected_attempt=0,
        native_intent_ref=c.record_ref(first/'native-intent.json'))
    assert result['role']=='H'
    current=wait_runner(root)
    assert c.read(current/'usage.json')==DELTA
    observation=c.read(current/'native-outcome.json')['metering']
    assert observation['coverage']=='terminal' and observation['reconciliation']=='matched'
    assert observation['live_tokens']==DELTA
    original_terminal=b'{"type":"turn.completed","native_terminal":true}\n'
    assert (current/'native.jsonl').read_bytes().endswith(original_terminal)
    work=c.workspace(root,1)
    assert (current/'science-output.json').read_bytes()==(work/'work/resumed-result.json').read_bytes()
    assert native.usage(root)['observed_tokens']==2410823
    assert budget._attempt_consumption(root,c.read(root/'jobs/001.json'),execution.records(root,1))[0]==2410823
    assert (work/'work/launches').read_text()=='exec\nresume\n'
    head=accepted_budget.read(root)
    assert head['max_local_step']==1 and head['dispatch_count']==2 and head['continuation_count']==0
    assert c.read(current/'native-intent.json')['continuation_kind']=='reentry'
    assert not (root/'jobs/002.json').exists() and budget.used_steps(root)==1
    assert all(path.read_bytes()==data for path,data in originals.items())
    current_originals={path:path.read_bytes() for path in current.iterdir() if path.is_file()}
    assert native.wait_native(root,1)==result
    assert all(path.read_bytes()==data for path,data in current_originals.items())
    assert (work/'work/launches').read_text()=='exec\nresume\n'
