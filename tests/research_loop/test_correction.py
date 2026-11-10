"""Current advisory boundaries and exact correction-ledger acknowledgments."""
import copy

import pytest

from scidiscovery.research_loop import budget, cli, contracts as c, controller, correction
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol


@pytest.fixture
def scene(tmp_path, monkeypatch):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Explain the global unresolved mechanism.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=100_000,
        max_seconds=3600, max_steps=40, model='fake', reasoning_effort='high',
        correction_policy=correction.DEFAULT_POLICY)
    return root


@pytest.mark.parametrize('protocol', [1, 2, 3, 4, 5, 6, 7])
def test_legacy_a_write_schema_is_rejected_without_mutation(scene, protocol):
    paths = [scene / 'settings.json', scene / 'settings-identity.json']
    original = {path: path.read_bytes() for path in paths}
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('A', protocol)
    assert {path: path.read_bytes() for path in paths} == original
    assert 'progress_assessment' not in c.result_schema('A')['properties']


def test_current_ordinary_advice_cannot_create_a_progress_gate(scene):
    from test_history_handoff import finish as current_finish
    current_finish(scene,1,'W',[],stage_intent='handoff')
    current_finish(scene,2,'A',[1],action='continue_w',progress=dict(verdict='no_progress'))
    state=correction.project(scene)
    assert state['eligible']==[] and state['triggers']==[]
    assert state['counters']==dict(no_progress=0,unknown=0)
    assert state['review_request'] is None and state['current_pause'] is None


@pytest.fixture
def supplied_pause(scene,monkeypatch):
    """Test ACK mechanics against a supplied publisher projection, not an A verdict.

    The refs are produced from real current originals. The mocked projection
    isolates ledger handling without manufacturing a scientific progress route.
    """
    from test_history_handoff import finish as current_finish
    current_finish(scene,1,'W',[],stage_intent='handoff')
    _,_,record=current_finish(scene,2,'A',[1],action='continue_w')
    seal=c.read(record/'result-identity.json')
    trigger=correction._trigger(scene,2,seal,'assessment_gap','pause')
    correction._append(scene,[],None,kind='trigger',trigger=trigger)
    state=dict(current_pause=dict(trigger,published=True),permit=None,
        eligible=[dict(step=2,seal_ref=c.record_ref(record/'result-identity.json'))])
    monkeypatch.setattr(correction,'project',lambda *args,**kwargs:copy.deepcopy(state))
    monkeypatch.setattr(budget,'execution_blocked',lambda *args,**kwargs:False)
    return scene,trigger,state


def test_ack_retains_exact_trigger_cutoff_idempotence_and_consumed_originals(supplied_pause):
    root,trigger,state=supplied_pause
    paths=[root/'jobs/001.json',root/'jobs/002.json',
        c.host_record(c.workspace(root,1))/'native-intent.json',
        c.host_record(c.workspace(root,2))/'science-output.json']
    before={path:path.read_bytes() for path in paths}
    with controller._writer(root):
        receipt=correction.acknowledge(root,trigger['trigger_id'],'Review the exact retained pause')
        state['permit']=receipt
        assert correction.acknowledge(root,trigger['trigger_id'],'Retry cannot extend the cut')==receipt
    assert receipt['cutoff']==2 and receipt['trigger_id']==trigger['trigger_id']
    assert receipt['cutoff_seal_ref']==state['eligible'][-1]['seal_ref']
    assert len(correction._records(root)[0])==2
    assert all(path.read_bytes()==value for path,value in before.items())
    assert not (root/'jobs/003.json').exists()


@pytest.mark.parametrize('change',['missing','unpublished','different'])
def test_ack_requires_exact_current_published_pause_before_ledger_write(supplied_pause,change):
    root,trigger,state=supplied_pause
    if change=='missing':state['current_pause']=None
    elif change=='unpublished':state['current_pause']['published']=False
    else:state['current_pause']['trigger_id']='different-original'
    before=(root/'correction-ledger/000001.json').read_bytes()
    with controller._writer(root),pytest.raises(ValueError,match='exact current published pause'):
        correction.acknowledge(root,trigger['trigger_id'],'Operator decision')
    assert (root/'correction-ledger/000001.json').read_bytes()==before
    assert not (root/'correction-ledger/000002.json').exists()


def test_ack_active_or_unknown_original_is_rejected_before_write(supplied_pause,monkeypatch):
    root,trigger,_=supplied_pause
    monkeypatch.setattr(budget,'execution_blocked',lambda *args,**kwargs:True)
    with controller._writer(root),pytest.raises(RuntimeError,match='active or unknown'):
        correction.acknowledge(root,trigger['trigger_id'],'Operator decision')
    assert len(correction._records(root)[0])==1


@pytest.mark.parametrize('note',['机'*16000,'\"'*16000])
def test_oversized_serialized_ack_note_keeps_ledger_and_user_pause(supplied_pause,note):
    root,trigger,_=supplied_pause
    c.write(root/'pause.request',dict(reason='user_pause'))
    before=(root/'correction-ledger/000001.json').read_bytes()
    with controller._writer(root),pytest.raises(ValueError,match='serialized UTF-8 byte bound'):
        correction.acknowledge(root,trigger['trigger_id'],note)
    assert (root/'correction-ledger/000001.json').read_bytes()==before
    assert not (root/'correction-ledger/000002.json').exists()
    assert (root/'pause.request').exists()


def test_cli_ack_requires_writer_before_clearing_user_pause(supplied_pause,capsys):
    root,trigger,_=supplied_pause
    c.write(root/'pause.request',dict(reason='user_pause'))
    with controller._writer(root):
        assert cli.main(['continue','--root',str(root),'--ack-correction',trigger['trigger_id'],
            '--authorization-note','Operator decision'])==1
    capsys.readouterr()
    assert (root/'pause.request').exists() and len(correction._records(root)[0])==1


@pytest.mark.parametrize('changed',['settings_ref','parent_ref','duplicate_trigger'])
def test_ledger_identity_sequence_and_duplicate_trigger_fail_closed(supplied_pause,changed):
    root,trigger,_=supplied_pause
    path=root/'correction-ledger/000001.json'
    event=c.read(path)
    if changed=='settings_ref':event['settings_ref']=dict(path='foreign/settings.json')
    elif changed=='parent_ref':event['parent_ref']=dict(path='foreign/previous.json')
    else:
        correction._append(root,[event],c.record_ref(path),kind='trigger',trigger=trigger)
    if changed!='duplicate_trigger':c.write(path,event)
    with pytest.raises(ValueError,match='identity|sequence|duplicated'):correction._records(root)
