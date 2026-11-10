"""Optional after-reading notes preserve execution originals and bridge recovery."""
from collections import Counter
import json
from pathlib import Path

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from test_mutable_run_inputs import fixed_code, live_fragment, live_terminal_code
from test_execution_fact_feedback import _remote_owner, _server
from scidiscovery.research_loop import contracts as c, experiments as e, research_notes
from scidiscovery.research_loop import worker_execution_bridge as bridge


def _note(server,call,note_id='note-1',text='After reading: the result changes my next test.',supersedes=None):
    source=server.work/'work/authored-note.txt';source.write_text(text)
    arguments=['note','--call-id',call,'--note-id',note_id,'--note-file',str(source)]
    if supersedes is not None:arguments.extend(['--supersedes',supersedes])
    return bridge._execute(server,arguments),arguments


@pytest.mark.parametrize(('entry','backend'),[('synchronous_run','protocol'),('synchronous_run','pi'),('collect','protocol')])
def test_note_after_reading_preserves_originals_and_no_provider_calls(
        tmp_path,remote_registry,fixed_code,monkeypatch,entry,backend,request):
    if entry=='synchronous_run':
        root,job,record,config,source,research=_remote_owner(tmp_path,backend=backend)
        server=_server(root,job,record,config)
        run=bridge._execute(server,['run','--script',str(source),'--research',str(research)])
        assert run['code']==0
        call=run['result']['call_id']
    else:
        root,job,record=request.getfixturevalue('live_fragment')
        terminal=request.getfixturevalue('live_terminal_code')();config=terminal['config']
        server=_server(root,job,record,config);call=terminal['accepted']['call_id']
        assert bridge._execute(server,['collect','--call-id',call])['code']==0
    directory=e.Records(config).directory(call)
    originals={name:(directory/name).read_bytes() for name in (
        'accepted.json','completed.json','execution-observation.json')}
    def forbidden(*args,**kwargs):pytest.fail('Note operation invoked provider or stopped inventory')
    monkeypatch.setattr(e,'load',forbidden)
    monkeypatch.setattr(c,'accept_current_inventory',forbidden)
    first,_=_note(server,call)
    assert first['code']==0 and first['result']['status']=='research_note_recorded'
    assert 'accepted' not in first['result']
    public=server.work/first['result']['note']['original']
    metadata=server.work/first['result']['note']['record']
    frozen=metadata.read_bytes()
    assert json.loads(frozen)['preview']['text'].startswith('After reading:')
    assert public.read_text().startswith('After reading:')
    repeated,_=_note(server,call)
    assert repeated==first and metadata.read_bytes()==frozen
    conflict,_=_note(server,call,text='Different bytes')
    assert conflict['code']==2 and conflict['result']['error']=='note_identity_conflict'
    assert public.read_text().startswith('After reading:')
    revised,_=_note(server,call,note_id='note-2',text='Revised interpretation',supersedes='note-1')
    assert revised['code']==0
    metadata=json.loads((server.work/revised['result']['note']['record']).read_text())
    assert metadata['supersedes']=='note-1'
    assert all((directory/name).read_bytes()==raw for name,raw in originals.items())
    assert str(root) not in json.dumps(first['result'])


def _claim_server(server):
    server.directory=server.work/bridge.SUBMISSION_DIRECTORY
    server.directory.mkdir(parents=True,exist_ok=True)
    server.claims=server.record/'note-test-claims';server.claims.mkdir()
    private=server.record/'note-test-diagnostics';private.mkdir()
    server.diagnostic=private/'latest.json'
    server.diagnostic_clean=lambda value,limit:value
    return server


def test_pending_call_note_cached_ack_and_crash_before_reply_never_replay(
        tmp_path,remote_registry,fixed_code,monkeypatch):
    root,job,record,config,source,research=_remote_owner(tmp_path,backend='protocol',outcome='unknown')
    server=_claim_server(_server(root,job,record,config));counts=Counter()
    original=DifferentExternalIdentity.submit
    def submit(self,payload):counts['submit']+=1;return original(self,payload)
    monkeypatch.setattr(DifferentExternalIdentity,'submit',submit)
    run=bridge._execute(server,['run','--script',str(source),'--research',str(research)])
    call=run['result']['call_id'];assert run['result']['status']=='unknown'
    source=server.work/'work/note.txt';source.write_text('Unknown transport: preserve this original.')
    arguments=['note','--call-id',call,'--note-id','unknown-note','--note-file',str(source)]
    raw=bridge._encoded(bridge._client_request('S001',arguments))
    bridge._handle(server,raw)
    first=json.loads((server.directory/'response.json').read_text())
    assert first['result']['status']=='research_note_recorded'
    def forbidden(*args,**kwargs):pytest.fail('Cached note reinvoked an operation')
    execute=bridge._execute;monkeypatch.setattr(bridge,'_execute',forbidden)
    bridge._handle(server,raw)
    assert json.loads((server.directory/'response.json').read_text())==first
    monkeypatch.setattr(bridge,'_execute',execute)
    # Simulate a durable note and claimed request with an interrupted reply.
    (server.claims/'S001/response.json').unlink()
    (server.claims/'S001/outcome.json').unlink()
    bridge._handle(server,raw,retained=True)
    assert json.loads((server.directory/'response.json').read_text())['result']==first['result']
    assert counts==dict(submit=1)
    assert not (e.Records(config).directory(call)/'execution-observation.json').exists()


def test_missing_note_file_is_note_only_correction(live_fragment,live_terminal_code):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_server(root,job,record,config);call=terminal['accepted']['call_id']
    response=bridge._execute(server,['note','--call-id',call,'--note-id','missing',
        '--note-file','work/missing-note.txt'])
    assert response['code']==2 and response['result']['status']=='caller_error'
    assert response['result']['accepted'] is False
    assert not (Path(config['record_dir'])/'research-notes/missing').exists()


def test_live_index_rebuild_preserves_counts_after_truncation_retry_and_missing_index(
        live_fragment,live_terminal_code):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_server(root,job,record,config);call=terminal['accepted']['call_id']
    index=server.work/'work/execution-results/research-notes/index.json'
    for number in range(8):
        reply,_=_note(server,call,note_id=f'note-{number}',text=f'Claim {number}')
        assert reply['code']==0
        view=json.loads(index.read_text())
        assert view['scope']['kind']=='current_worker'
        assert view['note_count']==number+1
        assert view['omitted_rows']+len(view['notes'])==number+1
    assert view['omitted_rows']>0
    directory=view['directory']
    assert directory['kind']=='directory' and 'later native attempts' in directory['scope']
    public=server.work/directory['path']
    entries={path.name for path in public.iterdir()}
    shown={row['note_id'] for row in view['notes']}
    assert {'note-0','note-7'}<=shown
    omitted_id=next(f'note-{number}' for number in range(8) if f'note-{number}' not in shown)
    assert omitted_id in entries
    assert (public/omitted_id/'original.raw').read_text()==f'Claim {omitted_id[5:]}'
    assert len((json.dumps(view,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))<=4096
    omitted,_=_note(server,call,note_id='note-0',text='Claim 0')
    assert omitted['code']==0 and json.loads(index.read_text())['note_count']==8
    same,_=_note(server,call,note_id='note-7',text='Claim 7')
    assert same['code']==0 and json.loads(index.read_text())['note_count']==8
    index.unlink()
    again,_=_note(server,call,note_id='note-7',text='Claim 7')
    rebuilt=json.loads(index.read_text())
    assert again==same and rebuilt['note_count']==8
    assert rebuilt['omitted_rows']+len(rebuilt['notes'])==8
    assert rebuilt['notes'][-1]['note_id']=='note-7'


def test_cached_note_original_tamper_is_strict(
        live_fragment,live_terminal_code):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_claim_server(_server(root,job,record,config));call=terminal['accepted']['call_id']
    source=server.work/'work/note.txt';source.write_text('Bound original note')
    arguments=['note','--call-id',call,'--note-id','bound-note','--note-file',str(source)]
    raw=bridge._encoded(bridge._client_request('S001',arguments))
    bridge._handle(server,raw)
    reply=json.loads((server.directory/'response.json').read_text())
    assert reply['result']['status']=='research_note_recorded'
    public=server.work/reply['result']['note']['record']
    public.chmod(0o600);public.write_text('{}')
    bridge._handle(server,raw)
    assert json.loads((server.directory/'response.json').read_text())['result']['status']=='integrity_error'


def test_stopped_note_loader_repairs_publish_before_public_reply_and_preserves_siblings(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_server(root,job,record,config);call=terminal['accepted']['call_id']
    assert _note(server,call,note_id='first')[0]['code']==0
    public=research_notes._publish_public
    class Crash(BaseException):pass
    def crash(*args,**kwargs):raise Crash()
    monkeypatch.setattr(research_notes,'_publish_public',crash)
    with pytest.raises(Crash):_note(server,call,note_id='interrupted',text='Retain this interruption claim')
    monkeypatch.setattr(research_notes,'_publish_public',public)
    captured=research_notes.load(config,accepted_calls={call:terminal['accepted']},
        accepted_attempts=[config['native_attempt']])
    assert captured['coverage']=='accepted_scope'
    assert {row['note_id'] for row in captured['records']}=={'first','interrupted'}
    interrupted=next(row for row in captured['records'] if row['note_id']=='interrupted')
    assert Path(interrupted['publication']['original']['path']).read_text()=='Retain this interruption claim'
    index=server.work/'work/execution-results/research-notes/index.json'
    assert json.loads(index.read_text())['note_count']==2
    # An unavailable optional sibling is explicit and cannot erase valid notes.
    missing=Path(config['record_dir'])/'research-notes/unavailable'
    missing.mkdir()
    captured=research_notes.load(config,accepted_calls={call:terminal['accepted']},
        accepted_attempts=[config['native_attempt']])
    assert len(captured['records'])==2 and captured['coverage']=='partial'
    assert captured['unavailable_notes']==[dict(note_id='unavailable',diagnostic='research_note_original_unavailable')]
    retry,_=_note(server,call,note_id='interrupted',text='Retain this interruption claim')
    # Optional unavailable sibling must not invalidate this already durable note's ack.
    assert retry['result'].get('note_id')=='interrupted'


@pytest.mark.parametrize('receipt',['before_ack','after_ack'])
@pytest.mark.parametrize('source_change',['replace','delete'])
@pytest.mark.parametrize('reentry',['same_attempt','later_attempt'])
def test_claimed_note_recovers_original_ack_without_mutable_source(
        live_fragment,live_terminal_code,monkeypatch,receipt,source_change,reentry):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_claim_server(_server(root,job,record,config));call=terminal['accepted']['call_id']
    source=server.work/'work/claim-note.txt';source.write_text('Original exact claim')
    arguments=['note','--call-id',call,'--note-id','claimed-note','--note-file',str(source)]
    raw=bridge._encoded(bridge._client_request('S001',arguments))
    if receipt=='before_ack':
        class Crash(BaseException):pass
        saved=bridge._save_note_ack
        def crash(*args,**kwargs):raise Crash()
        monkeypatch.setattr(bridge,'_save_note_ack',crash)
        with pytest.raises(Crash):bridge._handle(server,raw)
        monkeypatch.setattr(bridge,'_save_note_ack',saved)
        assert not (server.claims/'S001/note-publication.json').exists()
    else:
        bridge._handle(server,raw)
        for name in ('response.json','outcome.json'):(server.claims/'S001'/name).unlink()
    index=server.work/'work/execution-results/research-notes/index.json'
    index.unlink(missing_ok=True)
    if source_change=='replace':source.write_text('Later mutable bytes')
    else:source.unlink()
    execute=bridge._execute
    def forbidden(*args,**kwargs):pytest.fail('Recovery reread the mutable note request or invoked provider')
    monkeypatch.setattr(bridge,'_execute',forbidden)
    current_client=server.client
    configuration=bridge._configuration
    if reentry=='later_attempt':
        # The source boundary isolates a new bridge's current attempt from
        # the immutable old claim; recovery cannot invoke either operation.
        server.client=dict(current_client,attempt=1)
        monkeypatch.setattr(bridge,'_configuration',lambda owner:owner.configuration)
    bridge._handle(server,raw,retained=True)
    response=json.loads((server.directory/'response.json').read_text())
    assert response['code']==0 and response['result']['status']=='research_note_recorded'
    assert (server.work/response['result']['note']['original']).read_text()=='Original exact claim'
    assert str(root) not in json.dumps(response['result'])
    recovered=json.loads(index.read_text())
    assert recovered['note_count']==1 and recovered['omitted_rows']==0
    assert {row['note_id'] for row in recovered['notes']}=={'claimed-note'}
    assert len(index.read_bytes())<=4096
    bridge._handle(server,raw,retained=True)
    assert json.loads(index.read_text())==recovered
    # A new claimed request still compares new authored bytes against the id.
    monkeypatch.setattr(bridge,'_execute',execute)
    server.client=current_client
    monkeypatch.setattr(bridge,'_configuration',configuration)
    source.write_text('Different bytes in new request')
    bridge._handle(server,bridge._encoded(bridge._client_request('S002',arguments)))
    conflict=json.loads((server.directory/'response.json').read_text())
    assert conflict['code']==2 and conflict['result']['error']=='note_identity_conflict'


def test_live_note_index_marks_interrupted_private_sibling_unavailable(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_server(root,job,record,config);call=terminal['accepted']['call_id']
    public=research_notes._publish_public
    class Crash(BaseException):pass
    def crash(*args,**kwargs):raise Crash()
    monkeypatch.setattr(research_notes,'_publish_public',crash)
    with pytest.raises(Crash):_note(server,call,note_id='interrupted-private')
    monkeypatch.setattr(research_notes,'_publish_public',public)
    reply,_=_note(server,call,note_id='readable-note')
    assert reply['code']==0
    index=server.work/'work/execution-results/research-notes/index.json'
    view=json.loads(index.read_text())
    assert view['note_count']==2 and view['omitted_rows']==1 and view['coverage']=='partial'
    assert {row['note_id'] for row in view['notes']}=={'readable-note'}
    assert view['unavailable_notes']==[dict(note_id='interrupted-private',diagnostic='research_note_original_unavailable')]
    assert not (server.work/'work/execution-results/research-notes/interrupted-private/original.raw').exists()


def test_preview_is_created_once_before_freeze_and_retry_does_not_backfill(
        live_fragment,live_terminal_code,monkeypatch):
    root,job,record=live_fragment;terminal=live_terminal_code();config=terminal['config']
    server=_server(root,job,record,config);call=terminal['accepted']['call_id']
    preview=research_notes._preview;seen=[]
    def capture(path):
        seen.append(Path(path).name)
        assert Path(path).name=='original.raw'
        assert not (Path(path).parent/'note.json').exists()
        return preview(path)
    monkeypatch.setattr(research_notes,'_preview',capture)
    first,_=_note(server,call,text='中文\\\"'*100)
    original=server.work/first['result']['note']['original']
    metadata=server.work/first['result']['note']['record']
    frozen=metadata.read_bytes();raw=original.read_bytes()
    assert seen==['original.raw']
    assert json.loads(frozen)['preview']['truncated'] is True
    def forbidden(*args,**kwargs):pytest.fail('Retry regenerated a frozen preview')
    monkeypatch.setattr(research_notes,'_preview',forbidden)
    repeated,_=_note(server,call,text='中文\\\"'*100)
    assert repeated==first and metadata.read_bytes()==frozen and original.read_bytes()==raw
    # Model an already frozen legacy note without introducing a second fixture family.
    private=Path(config['record_dir'])/'research-notes/note-1/note.json'
    for path in (private,metadata):
        value=json.loads(path.read_text());value.pop('preview')
        path.chmod(0o600);c.write(path,value);path.chmod(0o400)
    legacy_private=private.read_bytes();legacy_public=metadata.read_bytes()
    loaded=research_notes.load(config,accepted_calls={call:terminal['accepted']},
        accepted_attempts=[config['native_attempt']])
    assert 'preview' not in loaded['records'][0]
    assert private.read_bytes()==legacy_private and metadata.read_bytes()==legacy_public
    assert original.read_bytes()==raw
