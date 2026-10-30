"""Original clean native endpoints resolve intermediate stream interruptions."""
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import backends, continuation, contracts as c, execution, native, native_recovery
import test_default_cli_output_repair as transport
from test_soft_node_budget import start_clock


NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
home=pathlib.Path(os.environ['CODEX_HOME'])
mode=json.loads(pathlib.Path(__file__).resolve().with_name('launcher-config.json').read_text())['fault']
assert 'resume' not in args
with (work/'work/launches').open('a') as stream:stream.write('exec\n')
thread='11111111-2222-4333-8444-000000000001'
session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
def emit(value):print(json.dumps(value),flush=True)
emit(dict(type='thread.started',thread_id=thread))
emit(dict(type='turn.started'))
notice=dict(type='error',message='The connection briefly interrupted; original stream remains open.')
emit(notice)
emit(dict(type='item.started',item=dict(id='original-command',type='command_execution',command='fixture',status='in_progress')))
emit(dict(type='item.completed',item=dict(id='original-command',type='command_execution',command='fixture',aggregated_output='retained original observation',exit_code=0,status='completed')))
payload=dict(summary='Retained original observation',scientific_result='A transport fixture supplies no scientific qualification.',
 limitations=['Only native transport is simulated.'],implementation_selection=None,
 next_action='analyze',route_reason='Independent interpretation of retained evidence.',search_plan=None)
raw=(json.dumps(payload,ensure_ascii=False,indent=2)+'\n').encode()
(work/'work/original-result.json').write_bytes(raw)
pathlib.Path(args[args.index('--output-last-message')+1]).write_bytes(raw)
emit(dict(type='item.completed',item=dict(id='final-message',type='agent_message',text=raw.decode())))
if mode in {'turn-failed','turn-incomplete','turn-interrupted'}:
 emit(dict(type=mode.replace('-', '.', 1),error=dict(code='stream_disconnected')))
if mode!='missing-terminal':
 emit(dict(type='turn.completed',usage=dict(input_tokens=103,cached_input_tokens=80,output_tokens=17)))
if mode=='error-after-terminal':emit(notice)
if mode=='duplicate-terminal':emit(dict(type='turn.completed',usage=dict(input_tokens=103,cached_input_tokens=80,output_tokens=17)))
if mode=='activity-after-terminal':emit(dict(type='item.started',item=dict(id='late-command',type='command_execution',command='late fixture',status='in_progress')))
raise SystemExit(7 if mode=='nonzero-exit' else 0)
'''


def _failure(event):
    return backends.selected(dict(native_launcher_argv=['codex'])).event(event)['failure']


def _diagnostic(root,mode):
    destination=os.environ.get('SCID_TEST_LOG_DIR')
    if not destination:return
    record=execution.origin(root,1)
    evidence=dict(mode=mode,native_source=(root.parent/'native.py').read_text())
    for name in ('runner.stderr','native.stderr','native.jsonl'):
        path=record/name
        if path.is_file():
            with path.open('rb') as stream:evidence[name]=stream.read(8192).decode('utf-8',errors='replace')
    if (record/'native-outcome.json').is_file():
        outcome=c.read(record/'native-outcome.json')
        evidence['outcome']={key:outcome.get(key) for key in (
            'returncode','stop_reason','terminal_observed','native_failure','cleanup','metering','usage')}
    target=Path(destination);target.mkdir(parents=True,exist_ok=True)
    (target/('native-events-'+mode+'.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2))


@pytest.mark.parametrize('event',[
    dict(type='error',message='An arbitrary intermediate stream notice.'),
    dict(type='error',error=dict(code='stream_disconnected')),
    dict(type='error',error=dict(code='invalid_api_key')),
    dict(type='error',error=dict(code='connection_error',accepted=False)),
])
def test_clean_original_completion_resolves_top_level_stream_errors(event):
    failure=_failure(event)
    assert failure['event_type']=='error'
    assert native_recovery.combine([failure])==failure
    assert native_recovery.combine([failure],completed_before=1) is None


@pytest.mark.parametrize('event',[
    dict(type='turn.failed',error=dict(code='stream_disconnected')),
    dict(type='turn.incomplete'),
    dict(type='turn.interrupted'),
])
def test_failed_turns_survive_later_completion(event):
    failure=_failure(event)
    assert native_recovery.combine([failure],completed_before=1)==failure


def test_completion_never_resolves_a_later_error_or_changes_its_classification():
    before=_failure(dict(type='error',error=dict(code='connection_error')))
    after=_failure(dict(type='error',error=dict(code='insufficient_quota')))
    assert native_recovery.combine([before,after],completed_before=1)==after
    assert native_recovery.combine([after],completed_before=0)==after


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',[
    'reconnected','missing-terminal','turn-failed','turn-incomplete','turn-interrupted',
    'nonzero-exit','error-after-terminal','duplicate-terminal','activity-after-terminal',
])
def test_original_fake_cli_stream_seals_only_a_verified_completion(tmp_path,monkeypatch,mode):
    monkeypatch.setattr(transport,'NATIVE',NATIVE)
    root,home,env=transport.prepare_cli(tmp_path,fault=mode)
    for name in ('CODEX_HOME','PATH','PYTHONPATH'):monkeypatch.setenv(name,env[name])
    c.build_job(root,1,'H',[])
    frozen={path:path.read_bytes() for path in (root/'settings.json',root/'jobs/001.json',
        home/'config.toml',home/'auth.json')}
    start_clock(root,'original native event completion fixture')
    try:
        if mode=='reconnected':
            native.execute_native(root,1)
        else:
            with pytest.raises((RuntimeError,ValueError)):
                native.execute_native(root,1)
    finally:_diagnostic(root,mode)
    record,=execution.records(root,1)
    outcome=c.read(record/'native-outcome.json')
    assert continuation.automatic_source(root,1,record)==outcome
    work=c.workspace(root,1)
    assert (work/'work/launches').read_text()=='exec\n'
    events=[json.loads(line) for line in (record/'native.jsonl').read_text().splitlines()]
    kinds=[event['type'] for event in events]
    assert kinds[:6]==['thread.started','turn.started','error','item.started','item.completed','item.completed']
    expected_tail=([mode.replace('-','.',1)] if mode in {'turn-failed','turn-incomplete','turn-interrupted'} else [])
    if mode!='missing-terminal':expected_tail+=['turn.completed']
    expected_tail+={
        'error-after-terminal':['error'],'duplicate-terminal':['turn.completed'],
        'activity-after-terminal':['item.started'],
    }.get(mode,[])
    assert kinds[6:]==expected_tail
    assert all(path.read_bytes()==raw for path,raw in frozen.items())
    assert not (root/'jobs/002.json').exists()
    expected=(work/'work/original-result.json').read_bytes()
    if mode=='reconnected':
        assert outcome['stop_reason'] is None and outcome['native_failure'] is None
        assert outcome['returncode']==0 and outcome['cleanup']=='observed_group_cleaned'
        assert outcome['terminal_observed'] is True and outcome['metering']['coverage']=='terminal'
        assert (record/'science-output.json').read_bytes()==expected
        assert not (record/'result.pending.json').exists()
        assert (record/'result-identity.json').is_file()
        assert c.validate(work)['scientific_result']==json.loads(expected)['scientific_result']
    else:
        assert outcome['returncode']==(7 if mode=='nonzero-exit' else 0)
        assert outcome['cleanup']=='observed_group_cleaned'
        assert outcome['terminal_observed'] is (mode!='missing-terminal')
        assert outcome['stop_reason']=={
            'duplicate-terminal':'native_completion_duplicated',
            'activity-after-terminal':'native_activity_after_completion',
        }.get(mode,'native_failure_event')
        assert not (record/'result-identity.json').exists()
        assert not (record/'science-output.json').exists()
        assert (record/'result.pending.json').read_bytes()==expected
        if mode in {'missing-terminal','turn-failed','turn-incomplete','turn-interrupted','nonzero-exit','error-after-terminal'}:
            assert outcome['native_failure'] is not None
    if mode!='missing-terminal':
        assert c.read(record/'usage.json')==dict(input_tokens=103,cached_input_tokens=80,output_tokens=17)
        assert native.usage(root)['observed_tokens']==120
