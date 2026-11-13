"""Inherited W nominations resolve against their exact frozen candidate pool."""
import copy
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    candidate_nominations, candidate_refs, contracts as c, experiments,
    generic_results, history, originals,
)


def declaration(candidate):
    return dict(representatives=[dict(mechanism_ref='h001.m1',candidate=candidate,reason='Retained original')],
        extras=[],gaps=[])


def unresolved(authored):
    value=copy.deepcopy(authored)
    for row in value['representatives']:row['candidate']=None
    return value


def frozen_source(tmp_path,monkeypatch,*,receipt_step=2,raw_step=2,status='known_success',retained=True):
    child=tmp_path/'child';parent=tmp_path/'unavailable-parent'
    storage=child/'host-runtime/fork'
    identity=dict(goal_id='origin',settings_ref=dict(path=str(parent/'settings.json')))
    job=dict(role='W',step=2,worker_view_version=1,experiment_mode='local',
        history=dict(candidate_ids=[],nodes=[]))
    call=dict(call_id='original-call',scope=dict(step=2),status='known_success',
        result_protocol=experiments.LOCAL_RESULT_PROTOCOL,completed=dict(path='completed.json'),
        outputs=[dict(name='candidate.json',path='candidate.json')],
        execution=dict(result=dict(status='available',
            candidates=[dict(artifact='artifacts/candidate.json',metric='loss')],
            outputs=[dict(path='artifacts/candidate.json')],metrics=[dict(name='loss',value=1.)])))
    candidates=generic_results.candidates([call]) if retained else []
    call['status']=status
    raw=dict(path=str(parent/f'native-evidence/{raw_step:03d}/continuations/001/science-output.json'),size_bytes=10)
    observation=dict(binding=history._observer_binding(raw,dict(adapter_ref='registered-originals-v1')),
        candidates=candidates,managed_calls=[call],current_candidate=None,actual_submission=None,contexts=[])
    seal=dict(raw_ref=dict(path=str(parent/'native-evidence/002/continuations/001/science-output.json'),size_bytes=10),
        observations=dict(path=f'host-history/observations/{receipt_step:03d}.json'))
    values={'settings.json':dict(goal_id='origin'),'jobs/002.json':job,
        'native-evidence/002/result-identity.json':seal,
        f'host-history/observations/{receipt_step:03d}.json':observation}
    entries=[]
    for relative,value in values.items():
        stored=f'inherited/origin/{relative}'
        c.write(storage/stored,value)
        entries.append(dict(origin_identity=identity,path=relative,storage_path=stored,
            size_bytes=(storage/stored).stat().st_size,visibility='host_proof'))
    source=originals.NodeSource(identity,2,str(parent),storage,tuple(entries))
    validated=[]
    def validate_managed(owner,task,rows,*,controls_only=False):
        assert owner is source and task==job and rows==[call] and controls_only is True
        validated.append(owner)
    monkeypatch.setattr(history,'validate_managed_original',validate_managed)
    def live_calls(*_args,**_kwargs):
        pytest.fail('Inherited nomination tried to recapture live managed records')
    monkeypatch.setattr(history,'managed_calls',live_calls)
    return child,source,job,call,validated


def test_inherited_nomination_reads_exact_frozen_source_without_child_or_parent_jobs(tmp_path,monkeypatch):
    child,source,job,call,validated=frozen_source(tmp_path,monkeypatch)
    chosen=dict(call_id=call['call_id'],artifact='artifacts/candidate.json')
    payload=dict(candidate_nominations=declaration(chosen))
    before=copy.deepcopy(payload)
    value=candidate_refs.control_payload(job,payload,root=child,source=source)
    assert value['candidate_nominations']['representatives'][0]['candidate']=='local-002-original-call-001'
    # The observation owner validates managed originals; this consumer does not repeat it.
    assert payload==before and validated==[]
    assert not (child/'jobs/002.json').exists()
    assert not (tmp_path/'unavailable-parent').exists()


@pytest.mark.parametrize('choice',[
    dict(call_id='other-call',artifact='artifacts/candidate.json'),
    dict(call_id='original-call',artifact='artifacts/other.json'),
])
def test_inherited_nomination_never_guesses_unresolved_exact_candidate_choice(tmp_path,monkeypatch,choice):
    child,source,job,_,_=frozen_source(tmp_path,monkeypatch)
    authored=declaration(choice)
    before=copy.deepcopy(authored)
    # Unresolvable advice keeps its mechanism and reason but names no candidate.
    assert candidate_nominations.resolve(child,job,authored,source=source)==unresolved(authored)
    assert authored==before


@pytest.mark.parametrize('status,retained',[('unknown',True),('known_success',False)])
def test_inherited_nomination_cannot_upgrade_unknown_or_unretained_declaration(tmp_path,monkeypatch,status,retained):
    child,source,job,_,_=frozen_source(tmp_path,monkeypatch,status=status,retained=retained)
    authored=declaration(dict(call_id='original-call',artifact='artifacts/candidate.json'))
    assert candidate_nominations.resolve(child,job,authored,source=source)==unresolved(authored)


@pytest.mark.parametrize('options,message',[
    (dict(receipt_step=3),'lost its exact producer'),
    (dict(raw_step=3),'binding changed'),
])
def test_inherited_observation_owner_keeps_original_binding_checks(tmp_path,monkeypatch,options,message):
    _,source,job,_,_=frozen_source(tmp_path,monkeypatch,**options)
    seal=c.read(originals.source_record(source,'result-identity.json'))
    with pytest.raises(history.ObserverIntegrityError,match=message):
        history.validate_observation_original(source,job,seal,controls_only=True)


def test_inherited_nomination_rejects_different_source_task(tmp_path,monkeypatch):
    child,source,job,_,_=frozen_source(tmp_path,monkeypatch)
    authored=declaration(dict(call_id='original-call',artifact='artifacts/candidate.json'))
    with pytest.raises(ValueError,match='exact source task'):
        candidate_nominations.resolve(child,dict(job,step=3),authored,source=source)


def test_nomination_string_reference_receives_current_typed_source(tmp_path,monkeypatch):
    child,source,job,_,_=frozen_source(tmp_path,monkeypatch)
    resolved=[]
    def resolve(root,task,candidate,*,source=None):
        resolved.append((root,task,candidate,source))
        return 'exact-original-candidate'
    monkeypatch.setattr(candidate_refs,'resolve_reference',resolve)
    value=candidate_refs.control_payload(job,dict(candidate_nominations=declaration('H001-C001')),
        root=child,source=source)
    assert value['candidate_nominations']['representatives'][0]['candidate']=='exact-original-candidate'
    assert resolved==[(child,job,'H001-C001',source)]


@pytest.mark.parametrize('sealed',[False,True])
def test_local_nomination_preserves_sealed_receipt_and_live_call_paths(tmp_path,monkeypatch,sealed):
    root,_,job,call,_=frozen_source(tmp_path,monkeypatch)
    record=root/'native-evidence/002'
    monkeypatch.setattr(c,'host_record',lambda _work:record)
    inventory=None
    authored=declaration(dict(call_id='original-call',artifact='artifacts/candidate.json'))
    if sealed:
        c.write(record/'result-identity.json',dict(observations=dict(path='host-history/observations/002.json')))
        c.write(root/'host-history/observations/002.json',dict(managed_calls=[call]))
    else:
        # An unsealed node consumes only its owner-accepted inventory; never live records.
        with pytest.raises(ValueError,match='accepted inventory'):
            candidate_nominations.resolve(root,job,authored)
        inventory=SimpleNamespace(managed_calls=[call])
    result=candidate_nominations.resolve(root,job,authored,inventory=inventory)
    assert result['representatives'][0]['candidate']=='local-002-original-call-001'
