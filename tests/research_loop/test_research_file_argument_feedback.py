"""Research arguments cross the bridge as file selections, never JSON paths."""
import errno
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scidiscovery.research_loop import experiment_client, experiments as e, public_output
from scidiscovery.research_loop import worker_execution_bridge as bridge


@pytest.fixture
def research_bridge(tmp_path,monkeypatch):
    work=tmp_path/'task';(work/'code').mkdir(parents=True)
    (work/'code/explore.py').write_text('pass\n')
    (work/'work/experiment').mkdir(parents=True)
    private=tmp_path/'private';private.mkdir()
    claims=private/'claims';claims.mkdir()
    records=private/'records';(records/'calls').mkdir(parents=True)
    config=dict(record_dir=str(records),scope=dict(role='W',step=1),max_seconds=30)
    client=dict(worker_view_version=1,step=1,attempt=0)
    config_path=work/'work/experiment-config.json';config_path.write_text(json.dumps(client))
    server=SimpleNamespace(work=work,directory=work/'work/experiment',
        config_path=config_path,configuration=config,client=client,claims=claims,
        diagnostic=private/'latest-error.json',record=private,
        stop=threading.Event(),environment={},launch=Mock(),
        diagnostic_clean=public_output.cleaner(tmp_path,{}),real_main=e.main)
    # Exercise the real bridge configuration equality and exact input proofs;
    # task binding resolution and shared-budget accounting have other owners.
    monkeypatch.setattr(experiment_client,'resolve',lambda path,value:config)
    monkeypatch.setattr(experiment_client,'root_for_configuration',lambda *args,**kwargs:tmp_path)
    monkeypatch.setattr(bridge,'_bridge_deadline',lambda root,value:None)
    def command(argv):
        print(json.dumps(e.caller_feedback(e.CallerError('Fixture dispatch boundary'))))
        return 2
    main=Mock(side_effect=command);monkeypatch.setattr(e,'main',main)
    dispatch=Mock(side_effect=AssertionError('No real backend dispatch in this fixture'))
    monkeypatch.setattr(e,'_dispatch_script',dispatch)
    return server,main,dispatch


def submit(server,arguments,reference='S001',*,retained=False):
    raw=bridge._encoded(dict(submission_ref=reference,arguments=arguments))
    bridge._handle(server,raw,retained=retained)
    return json.loads((server.directory/'response.json').read_bytes()),raw


def assert_unaccepted(server,main,dispatch,response,*,error=None):
    result=response['result']
    assert response['code']==2
    assert result['status']=='caller_error' and result['accepted'] is False
    if error is not None:assert result['error']==error
    assert result['handoff']['action']=='correct_request'
    assert result['handoff']['reason']=='execution_not_accepted'
    assert 'call_id' not in result and 'call_id' not in result['handoff']
    assert list((Path(server.configuration['record_dir'])/'calls').iterdir())==[]
    assert json.loads((server.claims/response['submission_ref']/'outcome.json').read_bytes())['calls']==[]
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()
    diagnostic=result['diagnostic']
    assert diagnostic['path'].startswith('work/experiment/diagnostics/D')
    assert (server.work/diagnostic['path']).is_file()
    assert server.diagnostic.is_file()


@pytest.mark.parametrize('command',['run','research-declare'])
@pytest.mark.parametrize('equals',[False,True])
@pytest.mark.parametrize('value',[
    json.dumps(dict(mechanism_ref='sparse_symbolic',experiment_question={'question':'q'*4000})),
    '{unfinished JSON',
])
def test_inline_json_is_rejected_before_path_probe(research_bridge,monkeypatch,command,equals,value):
    server,main,dispatch=research_bridge
    resolve=Path.resolve
    def ordinary_path(path,*args,**kwargs):
        assert value not in str(path),'Inline JSON must never become a filesystem path'
        return resolve(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',ordinary_path)
    arguments=[command,*(['--script','code/explore.py'] if command=='run' else [])]
    arguments.extend([f'--research={value}'] if equals else ['--research',value])
    response,raw=submit(server,arguments)
    assert_unaccepted(server,main,dispatch,response,error='research_file_required')
    assert '--research work/research.json' in response['result']['note']
    claim=server.claims/'S001'
    assert (claim/'submit.json').read_bytes()==raw
    saved={name:(claim/name).read_bytes() for name in ('submit.json','binding.json','response.json','outcome.json')}
    bridge._handle(server,raw)
    assert all((claim/name).read_bytes()==data for name,data in saved.items())
    main.assert_not_called()


@pytest.mark.parametrize('command',['run','research-declare'])
def test_research_path_too_long_is_an_argument_error(research_bridge,command):
    server,main,dispatch=research_bridge
    arguments=[command,*(['--script','code/explore.py'] if command=='run' else []),
        '--research','work/'+('x'*300)+'.json']
    response,_=submit(server,arguments)
    assert_unaccepted(server,main,dispatch,response,error='research_file_required')


@pytest.mark.parametrize('command',['run','research-declare'])
def test_ordinary_research_file_keeps_exact_reference_and_permissions(research_bridge,command):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json'
    research.write_text(json.dumps(dict(declarations=[])))
    research.chmod(0o400);original=e.proof(research)
    arguments=[command,*(['--script','code/explore.py'] if command=='run' else []),
        '--research','work/research.json']
    submit(server,arguments)
    main.assert_called_once()
    argv=main.call_args.args[0]
    assert argv[argv.index('--research')+1]==str(research)
    assert experiment_client.input_reference(server.configuration,research)==original
    assert e.proof(research)==original and research.stat().st_mode&0o777==0o400
    dispatch.assert_not_called();server.launch.assert_not_called()
    assert list((Path(server.configuration['record_dir'])/'calls').iterdir())==[]


@pytest.mark.parametrize('selection',['missing','symlink','escape'])
def test_existing_input_guards_remain_before_dispatch(research_bridge,selection):
    server,main,dispatch=research_bridge
    selected='work/research.json'
    if selection=='symlink':
        target=server.work/'work/original.json';target.write_text('{}')
        (server.work/selected).symlink_to(target)
    elif selection=='escape':selected='../research.json'
    response,_=submit(server,['run','--script','code/explore.py','--research',selected])
    assert_unaccepted(server,main,dispatch,response,
        error='scientific_input_not_found' if selection=='missing' else 'invalid_arguments')


def test_original_client_tamper_remains_integrity_and_never_replays(research_bridge):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    response,raw=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    assert response['result']['accepted'] is False
    claim=server.claims/'S001'
    saved={name:(claim/name).read_bytes() for name in ('submit.json','binding.json','response.json','outcome.json')}
    main.reset_mock()
    server.config_path.write_text(json.dumps(dict(server.client,attempt=1)))
    bridge._handle(server,raw)
    response=json.loads((server.directory/'response.json').read_bytes())
    assert response['code']==3 and response['result']['status']=='integrity_error'
    assert response['result']['handoff']['action']=='reconcile_or_stop'
    assert 'accepted' not in response['result']
    assert all((claim/name).read_bytes()==data for name,data in saved.items())
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()


def test_bound_research_original_tamper_remains_integrity(research_bridge,monkeypatch):
    server,main,dispatch=research_bridge
    original=server.claims.parent/'original-research.json';original.write_text('{}')
    material=dict(path='work/research.json',source=str(original),size_bytes=2)
    facts=experiment_client.client_bindings_from_facts(server.claims.parent,server.work,
        dict(direct_materials=[material]),material_paths={material['path']:original},material_sources={})
    monkeypatch.setattr(experiment_client,'load_client_facts',lambda config:facts)
    original.write_text('{"changed":true}')
    response,_=submit(server,['run','--script','code/explore.py','--research',material['path']])
    assert response['code']==3 and response['result']['status']=='integrity_error'
    assert 'accepted' not in response['result']
    assert response['result']['handoff']['action']=='reconcile_or_stop'
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()
    assert list((Path(server.configuration['record_dir'])/'calls').iterdir())==[]


def test_other_research_path_oserror_keeps_integrity_classification(research_bridge,monkeypatch):
    server,main,dispatch=research_bridge
    selected=server.work/'work/research.json'
    resolve=Path.resolve
    def unavailable(path,*args,**kwargs):
        if path==selected:raise OSError(errno.EACCES,'Fixture unreadable path')
        return resolve(path,*args,**kwargs)
    monkeypatch.setattr(Path,'resolve',unavailable)
    response,_=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    assert response['code']==3 and response['result']['status']=='integrity_error'
    assert 'accepted' not in response['result']
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()


def test_retained_unknown_request_never_executes_or_becomes_unaccepted(research_bridge):
    server,main,dispatch=research_bridge
    response,raw=submit(server,['run','--script','code/explore.py','--research','work/research.json'],retained=True)
    assert response['result']['status']=='unknown' and 'accepted' not in response['result']
    bridge._handle(server,raw)
    response=json.loads((server.directory/'response.json').read_bytes())
    assert response['result']['status']=='unknown' and 'accepted' not in response['result']
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()


@pytest.mark.parametrize('command',['run','research-declare'])
def test_direct_cli_inline_error_retains_concrete_file_instruction(command,capsys):
    value=json.dumps({'declarations':[]})
    # The nonexistent config proves parsing refuses before configuration I/O.
    assert e.main(['--config','/no-such-config.json',command,
        *(['--script','code/explore.py'] if command=='run' else []),f'--research={value}'])==2
    result=json.loads(capsys.readouterr().out)
    assert result['status']=='caller_error' and result['accepted'] is False
    assert result['error']=='research_file_required'
    assert '--research work/research.json' in result['note']


@pytest.mark.parametrize('command',['run','research-declare'])
def test_help_explicitly_requires_json_file(command):
    usage=' '.join(e.cli_help(command)['usage'].split())
    assert 'work/research.json' in usage
    assert 'inline JSON is not supported' in usage


def test_caller_error_links_original_trace_and_keeps_reference_after_later_error(research_bridge):
    server,main,dispatch=research_bridge
    response,raw=submit(server,['research-declare','--research','{"declarations":[]}'])
    assert_unaccepted(server,main,dispatch,response,error='research_file_required')
    diagnostic=response['result']['diagnostic']
    public_path=server.work/diagnostic['path'];saved_public=public_path.read_bytes()
    private_path=server.diagnostic.parent/(diagnostic['reference']+'.json')
    saved_private=private_path.read_bytes();original=json.loads(saved_private)
    view=json.loads(saved_public)
    assert original['step']==view['step']==1 and original['attempt']==view['attempt']==0
    assert original['submission_ref']==view['submission_ref']=='S001'
    assert 'CallerError' in original['traceback'] and 'research_file_argument' in original['traceback']
    assert 'CallerError' in view['traceback'] and 'research_file_argument' in view['traceback']
    assert str(server.work.parent) not in json.dumps(response)
    assert str(server.work.parent) not in saved_public.decode()
    assert all(set(frame)=={'module','function','line'} for frame in view['frames'])
    assert '/' not in diagnostic['source_location']['module']
    later,_=submit(server,['research-declare','--research','[1,2]'],'S002')
    assert later['result']['diagnostic']['reference']!=diagnostic['reference']
    assert json.loads(server.diagnostic.read_bytes())['diagnostic_ref']==later['result']['diagnostic']['reference']
    assert public_path.read_bytes()==saved_public and private_path.read_bytes()==saved_private
    bridge._handle(server,raw)
    cached=json.loads((server.directory/'response.json').read_bytes())
    assert cached['result']['diagnostic']==diagnostic


def test_generic_exception_links_full_chain_but_redacts_host_paths_and_credentials(research_bridge):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    credential='sensitive-diagnostic-value'
    def fail(argv):
        try:raise OSError(errno.EIO,f'Original host path {server.claims} api_key={credential}')
        except OSError as error:raise RuntimeError('Unusual adapter failure') from error
    main.side_effect=fail
    response,_=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    assert response['code']==3 and response['result']['status']=='integrity_error'
    assert 'accepted' not in response['result']
    diagnostic=response['result']['diagnostic']
    view=(server.work/diagnostic['path']).read_text()
    private=(server.diagnostic.parent/(diagnostic['reference']+'.json')).read_text()
    assert 'OSError' in view and 'RuntimeError' in view and 'direct cause' in view
    assert credential in private and str(server.claims) in private
    assert credential not in view and str(server.work.parent) not in view
    assert credential not in json.dumps(response) and str(server.work.parent) not in json.dumps(response)
    dispatch.assert_not_called();server.launch.assert_not_called()
    assert list((Path(server.configuration['record_dir'])/'calls').iterdir())==[]


@pytest.mark.parametrize('generic',[False,True])
@pytest.mark.parametrize('failure',['private','public','public_symlink'])
def test_diagnostic_write_failure_preserves_classification_without_fake_link(research_bridge,monkeypatch,generic,failure):
    from scidiscovery.research_loop import contracts as c
    server,main,dispatch=research_bridge
    if failure=='public_symlink':
        external=server.claims.parent/'outside';external.mkdir()
        (server.directory/'diagnostics').symlink_to(external,target_is_directory=True)
    else:
        write=c.write
        def refused(path,value):
            if ((failure=='private' and path.parent==server.diagnostic.parent)
                    or (failure=='public' and path.parent==server.directory/'diagnostics')):
                raise OSError(errno.ENOSPC,'Fixture diagnostic write unavailable')
            return write(path,value)
        monkeypatch.setattr(c,'write',refused)
    if generic:
        def fail(server,arguments,*,submission_ref=None):raise RuntimeError('Unexpected fixture error')
        monkeypatch.setattr(bridge,'_execute',fail)
        arguments=['run','--script','code/explore.py']
    else:arguments=['run','--script','code/explore.py','--research','{}']
    response,_=submit(server,arguments)
    assert 'diagnostic' not in response['result']
    assert response['result']['status']==('integrity_error' if generic else 'caller_error')
    assert response['code']==(3 if generic else 2)
    if not generic:assert response['result']['accepted'] is False
    main.assert_not_called();dispatch.assert_not_called();server.launch.assert_not_called()
    if failure=='public_symlink':assert list(external.iterdir())==[]


def test_main_original_exception_owner_reports_once_without_duplicate_private_write(research_bridge,monkeypatch):
    from contextlib import nullcontext
    from scidiscovery.research_loop import protocol, instance_owner
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    monkeypatch.setattr(e,'main',server.real_main)
    monkeypatch.setattr(protocol,'require_scope',lambda scope:None)
    monkeypatch.setattr(instance_owner,'lease_for_configuration',lambda config:nullcontext())
    response,_=submit(server,['research-declare','--research','work/research.json'])
    result=response['result']
    assert result['status']=='caller_error' and result['accepted'] is False
    assert 'frozen W-first strategy' in result['note']
    diagnostic=result['diagnostic']
    view=json.loads((server.work/diagnostic['path']).read_bytes())
    assert 'Live declarations require the frozen W-first strategy' in view['traceback']
    originals=[path for path in server.diagnostic.parent.iterdir() if path.name.startswith('D') and path.suffix=='.json']
    assert len(originals)==1 and 'related_diagnostics' not in result
    assert not (server.diagnostic.parent/'execution-diagnostics').exists()
    dispatch.assert_not_called();server.launch.assert_not_called()


def test_diagnostic_reporter_deduplicates_same_actual_exception_within_execute(research_bridge):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    def classified(argv):
        try:raise RuntimeError('Provider exception already classified by its owner')
        except RuntimeError as error:
            e._report_diagnostic(error)
            note=e._diagnostic(error,'Preserve original execution',config=server.configuration)
        print(json.dumps(dict(status='unknown',note=note)))
        return 3
    main.side_effect=classified
    response,_=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    assert response['result']['status']=='unknown' and 'accepted' not in response['result']
    assert 'diagnostic' in response['result'] and 'related_diagnostics' not in response['result']
    originals=[path for path in server.diagnostic.parent.iterdir() if path.name.startswith('D') and path.suffix=='.json']
    assert len(originals)==1
    assert not (server.diagnostic.parent/'execution-diagnostics').exists()


def test_pending_cached_unknown_keeps_original_diagnostic_links(research_bridge,monkeypatch):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    def classified(argv):
        for note in ('First owner error','Second owner error'):
            try:raise RuntimeError(note)
            except RuntimeError as error:e._diagnostic(error,'Preserve original call',config=server.configuration)
        print(json.dumps(dict(status='unknown',accepted=True,call_id='original-call')))
        return 3
    main.side_effect=classified
    monkeypatch.setattr(bridge,'_response_calls',lambda *args:[dict(call_id='original-call')])
    monkeypatch.setattr(bridge,'_verify_calls',lambda *args,**kwargs:False)
    response,raw=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    original=response['result']['diagnostic'];related=response['result']['related_diagnostics']
    saved=(server.work/original['path']).read_bytes()
    bridge._handle(server,raw)
    cached=json.loads((server.directory/'response.json').read_bytes())
    assert cached['result']['status']=='unknown'
    assert cached['result']['diagnostic']==original
    assert cached['result']['related_diagnostics']==related
    assert (server.work/original['path']).read_bytes()==saved
    main.assert_called_once()


def test_exception_group_view_keeps_all_sixteen_original_members(research_bridge):
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    # Python 3.10 has no native ExceptionGroup; the runtime feature is optional.
    import builtins
    group_type=getattr(builtins,'ExceptionGroup',None)
    if group_type is None:pytest.skip('Native ExceptionGroup requires Python 3.11')
    def grouped(argv):
        errors=[]
        for index in range(16):
            try:raise RuntimeError(f'Unique group member {index:02d}')
            except RuntimeError as error:errors.append(error)
        raise group_type('Sixteen source-bearing failures',errors)
    main.side_effect=grouped
    response,_=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    diagnostic=response['result']['diagnostic']
    view=json.loads((server.work/diagnostic['path']).read_bytes())
    private=json.loads((server.diagnostic.parent/(diagnostic['reference']+'.json')).read_bytes())
    for index in range(16):
        assert f'Unique group member {index:02d}' in view['traceback']
        assert f'Unique group member {index:02d}' in private['traceback']
    assert len([frame for frame in view['frames'] if frame['function']=='grouped'])==17
    assert str(Path(__file__).resolve()) not in view['traceback']
    assert str(server.work.parent) not in json.dumps(view)
    dispatch.assert_not_called();server.launch.assert_not_called()


def test_detailed_owner_feedback_reuses_produced_cleaner_without_reloading(research_bridge,monkeypatch):
    from scidiscovery.research_loop import contracts as c
    server,main,dispatch=research_bridge
    research=server.work/'work/research.json';research.write_text('{}')
    def unexpected_read(*args,**kwargs):raise AssertionError('Diagnostic consumer must use its produced cleaner')
    monkeypatch.setattr(c,'settings',unexpected_read)
    monkeypatch.setattr(public_output,'cleaner',unexpected_read)
    def classified(argv):
        try:raise RuntimeError(f'Host original at {server.claims} token=private-owner-token')
        except RuntimeError as error:
            note=e._diagnostic(error,'Inspect declared execution options',config=server.configuration,detail=True)
        print(json.dumps(dict(status='unknown',note=note)))
        return 3
    main.side_effect=classified
    response,_=submit(server,['run','--script','code/explore.py','--research','work/research.json'])
    assert response['result']['status']=='unknown'
    assert '[private]' in response['result']['note'] and '[credential]' in response['result']['note']
    assert 'private-owner-token' not in response['result']['note']
    assert 'diagnostic' in response['result']
