"""Backend-neutral native stop facts survive the owner, recovery and UI seams."""
import json

import pytest

from scidiscovery.research_loop import (accepted_budget, backends, contracts as c, native,
    native_recovery, native_usage, ui_errors, ui_saved_status)
from test_actionable_control_errors import completed_node, snapshot, summary
from test_execution_authority import prepare
from test_instance_ui import session
from test_native_transport_stall import _scene, _transport_guard


DIAGNOSTIC=dict(phase='context_report',code='turn_identity_changed',
    original_call_state='unknown',thread_bound=True,turn_bound=False)


def test_native_diagnostic_stays_typed_and_private_fields_never_become_ui_text():
    source=dict(DIAGNOSTIC,exception='/private/auth token',thread_id='private-original-id',
        next_action='resubmit everything',tools=['private tool output'])
    for backend in (backends.Codex,backends.Protocol):
        event=backend.event(None,dict(type='transport.diagnostic',diagnostic=source))
        assert event['transport_diagnostic']['next_action']=='reconcile_original_without_replay'
        public=ui_errors.native_stop_problem(event['transport_diagnostic']).markup()
        assert 'context_report' in public and 'turn_identity_changed' in public
        assert '状态未知' in public and '不要重新提交' in public
        assert 'private' not in public and 'resubmit everything' not in public
    for key,value in [('phase',[]),('code','private failure'),('original_call_state',{}),
            ('thread_bound',1),('turn_bound',None)]:
        assert native_usage.transport_diagnostic(dict(DIAGNOSTIC,**{key:value})) is None


def test_unknown_call_diagnosis_blocks_recovery_even_without_observed_activity():
    failure=dict(category='temporary',retry_after_seconds=None,not_accepted=True)
    outcome=dict(technical_diagnostic=DIAGNOSTIC,native_failure=failure,terminal_observed=False,
        stop_reason='native_failure_event',cleanup='observed_group_cleaned',thread_id=None)
    limits=native_recovery.policy({})
    used=dict(continues=0,retries=0,wait_seconds=0.)
    decision=native_recovery.decision(outcome,limits,used,session_available=False,activity_observed=False)
    assert decision['action']=='stop'
    assert decision['reason']=='automatic_recovery_original_call_unknown'
    assert decision['counters']==used
    assert not native_recovery.recoverable_source(outcome)
    assert not native_recovery.never_started(outcome)


def test_qualified_untyped_rejection_retains_existing_bounded_retry():
    outcome=dict(native_failure=dict(category='temporary',retry_after_seconds=None,not_accepted=True),
        terminal_observed=False,stop_reason='native_failure_event',
        cleanup='observed_group_cleaned',thread_id=None)
    limits=native_recovery.policy({})
    used=dict(continues=0,retries=0,wait_seconds=0.)
    decision=native_recovery.decision(outcome,limits,used,session_available=False,activity_observed=False)
    assert decision['action']=='retry' and decision['startup_retry'] is True
    assert decision['counters']==used
    assert decision['delay_seconds']==limits['initial_backoff_seconds']
    exhausted=dict(used,retries=limits['max_retries'])
    assert native_recovery.decision(outcome,limits,exhausted,session_available=False)['action']=='stop'


def test_saved_stop_and_attention_use_loaded_facts_without_reading_private_stream(tmp_path,monkeypatch):
    root,key=prepare(tmp_path,enabled=False)
    ui,cookie=session(root,key)
    record=root/'native-evidence/004'
    outcome=completed_node(returncode=1,terminal_observed=False,stop_reason='native_metadata_unavailable',
        technical_diagnostic=DIAGNOSTIC,budget_slice=dict(advance_seconds=7.25,observed_tokens=23))
    c.write(record/'native-outcome.json',outcome)
    original=(record/'native-outcome.json').read_bytes()
    # The new projection must not invoke legacy native-stream reconciliation.
    monkeypatch.setattr(c,'native_outcome',lambda *args:pytest.fail('reopened private native stream'))
    node=ui_saved_status._node(root,4,dict(role='W'))
    assert node['budget_report']==outcome['budget_slice']
    assert node['technical_diagnostic']['budgets_preserved'] is True
    assert node['technical_diagnostic']['original_records']==[
        'native-intent.json','native-outcome.json']
    rendered=str(ui._attention(ui._browser(cookie),False,snapshot(node),summary(node)))
    assert 'context_report' in rendered and 'turn_identity_changed' in rendered
    assert '不要重新提交' in rendered and '已用预算保留' in rendered
    assert '<details open>' in rendered
    assert (record/'native-outcome.json').read_bytes()==original
    ui.translation.close()


@pytest.mark.process_e2e
def test_owner_retains_neutral_process_diagnosis_usage_and_same_original(tmp_path,monkeypatch):
    root,record=_scene(tmp_path,monkeypatch,'closed')
    # Admit this exact prepared job through the real publisher while retaining
    # the original intent; acceptance does not recreate its source file.
    intent=c.read(record/'native-intent.json')
    accepted_budget.accept_native(root,1,record,intent)
    # Reuse the existing deterministic protocol-launch fixture and real owner;
    # the backend publishes a small diagnosis instead of private stderr prose.
    launcher=tmp_path/'launcher.py'
    launcher.write_text('import json,sys\njson.loads(sys.stdin.read())\n'+
        '\n'.join('print('+repr(json.dumps(event))+',flush=True)' for event in [
            dict(type='session',session_id='fixture-original'),
            dict(type='usage',usage=dict(input_tokens=19,cached_input_tokens=2,output_tokens=4)),
            dict(type='transport.diagnostic',diagnostic=DIAGNOSTIC)])+
        '\nraise SystemExit(7)\n')
    original=(record/'native-intent.json').read_bytes()
    with _transport_guard(),pytest.raises(RuntimeError,match='not a verified terminal result'):
        native.run_native(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert outcome['returncode']==7 and outcome['terminal_observed'] is False
    assert outcome['thread_id']=='fixture-original'
    assert outcome['technical_diagnostic']==native_usage.transport_diagnostic(DIAGNOSTIC)
    assert c.read(record/'usage.json')==dict(input_tokens=19,cached_input_tokens=2,output_tokens=4)
    assert outcome['metering']['coverage']=='unknown'
    assert outcome['budget_slice']['advance_seconds']>=0
    assert outcome['budget_slice']['observed_tokens']==23
    assert (record/'native-intent.json').read_bytes()==original
    assert not (record/'continuations').exists()
