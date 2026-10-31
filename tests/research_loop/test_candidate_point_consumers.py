"""Candidate consumers use accepted addresses without rebuilding old science."""
import json

import pytest

from scidiscovery.research_loop import (
    accepted_prefix, candidate_audit, candidate_refs, contracts as c, delivery,
    history, originals, shortlist,
)


def fact(identity, step):
    return dict(first_step=step, origin_identity={'goal_id': 'original'},
        observation=dict(id=identity, metric=None, evaluation_key=None,
            direction=None, status='ok'))


def test_normal_catalogue_freezes_only_explicit_preview_and_prefix(tmp_path, monkeypatch):
    prefix=dict(version=1, count=800, cutoff=800)
    snapshot=tmp_path/'snapshot.json'
    c.write(snapshot,dict(candidate_count=600, latest_a_step=799,
        current_candidate=dict(candidate_id='current'),
        a_recommended=dict(candidate_id='old'),
        historical_a_recommendation=dict(candidate_id='old'),actual_submission=None))
    job=dict(step=801,history=dict(prefix=prefix,breadth_snapshot=dict(path='snapshot.json'),
        view=dict(candidates=dict(nominations=dict(reps=[dict(candidate_id='nominated')],
            extras=[dict(candidate_id='old')])))))
    reads=[]
    monkeypatch.setattr(originals,'resolve_root_ref',lambda root,ref:snapshot)
    monkeypatch.setattr(accepted_prefix,'candidate',lambda root,cut,identity:
        reads.append(identity) or fact(identity,2 if identity=='old' else 800))
    monkeypatch.setattr(history,'rebuild',lambda *args,**kwargs:pytest.fail('Historical science rebuilt'))
    marker=shortlist.bind_normal(tmp_path,job)
    value=shortlist._screen(tmp_path,marker,job=job)
    assert reads==['current','old','nominated']
    assert value['source_prefix']==prefix
    assert [row['candidate_id'] for row in value['rows']]==['current','old','nominated']
    # A retained candidate outside this brief is still selectable.
    monkeypatch.setattr(candidate_refs,'is_available',lambda root,task,identity:identity=='other')
    job['selection']=marker
    assert shortlist.choice(tmp_path,job,dict(candidate_recommendation='other'))=='other'
    with pytest.raises(delivery.DeliveryUnresolved,match='selection_candidate_unavailable'):
        shortlist.choice(tmp_path,job,dict(candidate_recommendation='absent'))


def test_exact_candidate_producer_uses_one_membership(tmp_path, monkeypatch):
    prefix=dict(version=1,count=500,cutoff=500)
    job=dict(history=dict(prefix=prefix))
    selected=fact('retained',3);calls=[]
    monkeypatch.setattr(accepted_prefix,'candidate',lambda root,cut,identity:
        calls.append(('candidate',cut,identity)) or selected)
    def member(root,cut,step,*,origin):
        calls.append(('member',cut,step,origin))
        return dict(seal_ref=dict(path='exact-producer.json'))
    monkeypatch.setattr(accepted_prefix,'member',member)
    assert delivery.candidate_source_nodes(tmp_path,job,'retained')==[
        dict(step=3,seal_ref=dict(path='exact-producer.json'))]
    assert calls==[('candidate',prefix,'retained'),('member',prefix,3,selected['origin_identity'])]


def test_delivery_choice_does_not_enumerate_available_candidates(tmp_path, monkeypatch):
    job=dict(step=80,history=dict(prefix=dict(version=1)))
    monkeypatch.setattr(candidate_refs,'available_ids',lambda *args,**kwargs:pytest.fail('Candidate pool enumerated'))
    monkeypatch.setattr(candidate_refs,'is_available',lambda root,task,identity:identity=='old')
    monkeypatch.setattr(delivery,'_settings',lambda *args:dict(selection_policy=None))
    assert delivery._choice(tmp_path,job,dict(next_action='deliver',candidate_recommendation='old'))=='old'
    with pytest.raises(delivery.DeliveryUnresolved,match='selection_candidate_unavailable'):
        delivery._choice(tmp_path,job,dict(next_action='deliver',candidate_recommendation='absent'))


def test_selected_candidate_outside_catalogue_preview_is_prepared(tmp_path, monkeypatch):
    class UnreadablePreview:
        def __iter__(self):pytest.fail('Candidate preview enumerated')
    job=dict(step=20,history=dict(prefix=dict(version=1)))
    refs=dict(catalogue=True,items=UnreadablePreview(),source_nodes=[])
    selected_nodes=[dict(step=2,seal_ref=dict(path='producer.json'))]
    prepared=dict(items=[dict(candidate_id='old',status='audit_pending')],source_nodes=selected_nodes)
    monkeypatch.setattr(candidate_refs,'is_available',lambda *args:True)
    monkeypatch.setattr(delivery,'candidate_source_nodes',lambda *args:selected_nodes)
    monkeypatch.setattr(delivery,'preparation_blocked',lambda root:False)
    monkeypatch.setattr(delivery.budget,'closeout_state',lambda root:dict(unresolved_operations=[]))
    calls=[]
    monkeypatch.setattr(delivery,'freeze_sources',lambda root,nodes,**kwargs:calls.append(kwargs) or prepared)
    monkeypatch.setattr(delivery,'validate_sources',lambda root,value,**kwargs:value)
    assert delivery.selected_sources(tmp_path,refs,'old',prepare=True,job=job)==prepared
    assert delivery.selected_sources(tmp_path,refs,'old',prepare=True,job=job)==prepared
    assert len(calls)==1
    assert calls[0]['candidate_id']=='old'


def test_unavailable_audit_identity_reads_only_selected_producer(tmp_path, monkeypatch):
    c.write(tmp_path/'settings.json',{})
    job=dict(step=30,history=dict(prefix=dict(version=1)))
    seal_ref=dict(path='producer.json');observation=dict(path='observation.json')
    monkeypatch.setattr(delivery,'candidate_source_nodes',lambda *args:[dict(step=2,seal_ref=seal_ref)])
    reads=[]
    monkeypatch.setattr(history,'_source',lambda root,step:
        reads.append(step) or ({},None,dict(observations=observation),seal_ref))
    value=json.loads(candidate_audit._unavailable_input(tmp_path,job,'retained',[]))
    assert reads==[2]
    assert value['sources']==[dict(step=2,seal_ref=seal_ref,observation=observation)]


def test_final_catalogue_reads_saved_navigation_without_science(tmp_path, monkeypatch):
    prefix=dict(version=1,count=3,cutoff=3)
    nodes=[dict(step=1,role='W',previous=None,
                entities=[dict(kind='candidate',reference='public-old')]),
        dict(step=2,role='A',previous='first.json',entities=[]),
        dict(step=3,role='W',previous='second.json',
            entities=[dict(kind='candidate',reference='public-new')])]
    c.write(tmp_path/'first.json',nodes[0]);c.write(tmp_path/'second.json',nodes[1])
    monkeypatch.setattr(accepted_prefix,'current',lambda root:prefix)
    monkeypatch.setattr(accepted_prefix,'visibility',lambda *args:tmp_path)
    monkeypatch.setattr(accepted_prefix,'navigation',lambda *args:nodes[-1])
    monkeypatch.setattr(accepted_prefix,'candidate',lambda root,cut,identity:
        fact('old' if identity=='public-old' else 'new',1 if identity=='public-old' else 3))
    source=object();seal_ref=dict(path='source-seal.json')
    c.write(tmp_path/'source-seal.json',dict(breadth_snapshot=dict(path='snapshot.json')))
    c.write(tmp_path/'snapshot.json',dict(latest_a_step=2,a_recommended=dict(candidate_id='public-old')))
    monkeypatch.setattr(accepted_prefix,'member',lambda *args:dict(origin_identity={},seal_ref=seal_ref))
    monkeypatch.setattr(originals,'for_origin',lambda *args,**kwargs:source)
    monkeypatch.setattr(history,'source_seal_ref',lambda owner:seal_ref)
    monkeypatch.setattr(originals,'source_record',lambda owner,name:tmp_path/'source-seal.json')
    monkeypatch.setattr(originals,'source_for_ref',lambda owner,ref:source)
    monkeypatch.setattr(originals,'resolve_ref',lambda owner,ref:tmp_path/'snapshot.json')
    from scidiscovery.research_loop import engineering
    monkeypatch.setattr(engineering,'steps',lambda *args:[])
    monkeypatch.setattr(history,'rebuild',lambda *args,**kwargs:pytest.fail('Historical science rebuilt'))
    value=shortlist._members(tmp_path,dict(source_cutoff=3))
    assert [row['candidate_id'] for row in value['members']]==['old','new']
    assert value['candidate_count']==2
    assert value['latest_a_step']==2 and value['a_recommended']=='old'
    assert value['unassessed_count']==1
