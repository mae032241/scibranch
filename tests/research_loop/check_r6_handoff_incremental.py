"""Explicit incremental handoff checks; no model, solver or live instance.

The sealed E findings below are authored synthetic unknowns. Their presence
does not supply scientific evidence, qualification or execution permission.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scidiscovery import archive_io
from scidiscovery.research_loop import contracts as c, controller, experiments, handoff_state, history, semantic
from scidiscovery.research_loop import instance_control, native, role_share, shared_input_views
from scidiscovery.research_loop import search
from scidiscovery.research_loop import qualification
from test_history_handoff import finish


def _binding():
    proof=dict(path='native-evidence/002/calls/call-1/accepted.json',size_bytes=11,
        file_stat=[1,2,11,123456789])
    retained=dict(proof,snapshot='retained/accepted.json',retention='archived')
    return proof,retained


@pytest.mark.parametrize('prepared',[False,True])
def test_native_proof_without_stat_retains_current_wildcard(tmp_path,prepared):
    proof,retained=_binding()
    proof.pop('file_stat')
    row=dict(accepted=retained,originals=[],outputs=[])
    lookup=history._retained_resolver(tmp_path,row) if prepared else None
    assert history._retained_original(tmp_path,row,proof,lookup=lookup) is retained


@pytest.mark.parametrize('prepared',[False,True])
def test_retained_proof_without_stat_retains_current_wildcard(tmp_path,prepared):
    proof,retained=_binding()
    retained.pop('file_stat')
    row=dict(accepted=retained,originals=[],outputs=[])
    lookup=history._retained_resolver(tmp_path,row) if prepared else None
    assert history._retained_original(tmp_path,row,proof,lookup=lookup) is retained


def test_stat_disambiguates_only_when_the_native_proof_has_it(tmp_path):
    proof,retained=_binding()
    other=dict(retained,file_stat=[1,3,11,123456789],snapshot='retained/other.json')
    row=dict(accepted=retained,originals=[other],outputs=[])
    lookup=history._retained_resolver(tmp_path,row)
    assert history._retained_original(tmp_path,row,proof,lookup=lookup) is retained
    with pytest.raises(history.ObserverIntegrityError,match='exact retained path binding'):
        history._retained_original(tmp_path,row,{key:value for key,value in proof.items() if key!='file_stat'},lookup=lookup)


def test_same_snapshot_duplicate_uses_the_last_matching_entry(tmp_path):
    proof,retained=_binding()
    last=dict(retained,name='last descriptor')
    row=dict(accepted=retained,originals=[dict(retained)],outputs=[last])
    lookup=history._retained_resolver(tmp_path,row)
    assert history._retained_original(tmp_path,row,proof,lookup=lookup) is last


def test_wildcard_stat_cannot_hide_a_different_matching_snapshot(tmp_path):
    proof,retained=_binding()
    other={key:value for key,value in dict(retained,snapshot='retained/other.json').items() if key!='file_stat'}
    row=dict(accepted=retained,originals=[],outputs=[other])
    lookup=history._retained_resolver(tmp_path,row)
    with pytest.raises(history.ObserverIntegrityError,match='exact retained path binding'):
        history._retained_original(tmp_path,row,proof,lookup=lookup)


def test_a_new_verification_does_not_reuse_a_mutated_rows_lookup(tmp_path):
    proof,retained=_binding()
    row=dict(accepted=retained,originals=[],outputs=[])
    assert history._retained_original(tmp_path,row,proof,lookup=history._retained_resolver(tmp_path,row)) is retained
    row['outputs'].append(dict(retained,snapshot='retained/new.json'))
    with pytest.raises(history.ObserverIntegrityError,match='exact retained path binding'):
        history._retained_original(tmp_path,row,proof,lookup=history._retained_resolver(tmp_path,row))


UNKNOWN='Synthetic evidence fixture: the scientific question remains unanswered.'


def _prefix(tmp_path,length):
    public=tmp_path/'public';public.mkdir(parents=True)
    objective=public/'objective.md'
    objective.write_text('Retain exact evidence findings; all scientific conclusions remain unknown.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture'],max_tokens=100000,
        max_seconds=120,max_steps=length+2,model='fixture',reasoning_effort='high',
        search_policy=None,experiment_mode='external')
    with handoff_state.scope(root):
        for step in range(1,length+1):
            finish(root,step,'E',[],text=UNKNOWN,
                limitations=['Authored synthetic fixture; no scientific evidence or qualification.'])
    return root


def _producer(root,path):
    try:parts=Path(path).absolute().relative_to(root).parts
    except (TypeError,ValueError):return None
    if len(parts)>1 and parts[0]=='native-evidence' and parts[1].isdecimal():return int(parts[1])
    if len(parts)>2 and parts[:2]==('project','nodes') and parts[2].isdecimal():return int(parts[2])
    if len(parts)==2 and parts[0]=='jobs' and Path(parts[1]).stem.isdecimal():return int(Path(parts[1]).stem)
    return None


@contextmanager
def _verification_counts(monkeypatch,root,length):
    """Count checks separately from permitted reads of already saved facts."""
    counts=Counter()
    def category(step):
        return 'ancient' if step is not None and step<length else 'direct' if step==length else 'current' if step==length+1 else 'other'
    with monkeypatch.context() as patch:
        validate=c.validate
        def counted_validate(work,*args,**kwargs):
            counts['old_validate' if int(work.name)<=length else 'current_validate']+=1
            return validate(work,*args,**kwargs)
        patch.setattr(c,'validate',counted_validate)
        validate_original=c.validate_original
        def counted_original(source,*args,**kwargs):
            counts['old_validate_original' if source.step<=length else 'current_validate_original']+=1
            return validate_original(source,*args,**kwargs)
        patch.setattr(c,'validate_original',counted_original)
        validate_inputs=c._validate_inputs
        def counted_inputs(root,work,job,*args,**kwargs):
            counts['old_input_validation' if job['step']<=length else 'current_input_validation']+=1
            return validate_inputs(root,work,job,*args,**kwargs)
        patch.setattr(c,'_validate_inputs',counted_inputs)
        validate_inputs_from_facts=c._validate_inputs_from_facts
        def counted_inputs_from_facts(root,work,job,*args,**kwargs):
            counts['old_input_validation' if job['step']<=length else 'current_input_validation']+=1
            return validate_inputs_from_facts(root,work,job,*args,**kwargs)
        patch.setattr(c,'_validate_inputs_from_facts',counted_inputs_from_facts)
        for name,entry in (('validate_managed','_validate_managed'),('validate_observation','validate_observation')):
            original=getattr(history,entry)
            def counted_history(root,job,*args,_original=original,_name=name,**kwargs):
                counts[f'old_{_name}' if job['step']<=length else f'current_{_name}']+=1
                return _original(root,job,*args,**kwargs)
            patch.setattr(history,entry,counted_history)
        managed_status=history.managed_status
        def counted_status(root,row,*args,**kwargs):
            counts['managed_status']+=1
            return managed_status(root,row,*args,**kwargs)
        patch.setattr(history,'managed_status',counted_status)
        managed_original=history.validate_managed_original
        def counted_managed_original(source,*args,**kwargs):
            counts['old_managed_original' if source.step<=length else 'current_managed_original']+=1
            return managed_original(source,*args,**kwargs)
        patch.setattr(history,'validate_managed_original',counted_managed_original)
        verify_observation=experiments.verify_observation
        def counted_observation(*args,**kwargs):
            counts['verify_observation']+=1
            return verify_observation(*args,**kwargs)
        patch.setattr(experiments,'verify_observation',counted_observation)
        stat_file=archive_io.stat_file
        def counted_stat(path,*args,**kwargs):
            counts[f"stat_{category(_producer(root,path))}"]+=1
            return stat_file(path,*args,**kwargs)
        patch.setattr(archive_io,'stat_file',counted_stat)
        read_science=c.read_scientific_source
        def counted_science(path,*args,**kwargs):
            counts[f"science_read_{category(_producer(root,path))}"]+=1
            return read_science(path,*args,**kwargs)
        patch.setattr(c,'read_scientific_source',counted_science)
        native_outcome=c.native_outcome
        def counted_outcome(record,*args,**kwargs):
            producer=_producer(root,record)
            counts['old_native_outcome' if producer is not None and producer<=length else 'current_native_outcome']+=1
            return native_outcome(record,*args,**kwargs)
        patch.setattr(c,'native_outcome',counted_outcome)
        restriction=qualification._restriction
        def counted_restriction(document,*args,**kwargs):
            counts['old_restriction_replay' if document['step']<=length else 'current_restriction']+=1
            return restriction(document,*args,**kwargs)
        patch.setattr(qualification,'_restriction',counted_restriction)
        provenance_output=qualification._output
        def counted_provenance(root,job,*args,**kwargs):
            counts['old_provenance_output' if job['step']<=length else 'current_provenance_output']+=1
            return provenance_output(root,job,*args,**kwargs)
        patch.setattr(qualification,'_output',counted_provenance)
        walk=shared_input_views._Inventory.walk
        def counted_walk(inventory,path):
            owner=category(_producer(root,path))
            if owner=='ancient':counts['inventory_ancient']+=1
            yield from walk(inventory,path)
        patch.setattr(shared_input_views._Inventory,'walk',counted_walk)
        yield counts


def _no_old_verification(counts):
    assert not {key:value for key,value in counts.items() if key.startswith('old_') and value},dict(counts)
    assert counts['managed_status']==counts['verify_observation']==0,dict(counts)


def test_cold_restore_and_next_node_keep_verification_constant(tmp_path,monkeypatch):
    """Saved prefix length may grow; only the declared new input gets checked.

    Cold restore is allowed to read all saved seals and scientific JSON. This
    measures original verification and metadata probes, not zero byte IO.
    """
    current_work=[]
    for length in (2,8,16):
        root=_prefix(tmp_path/f'prefix-{length}',length)
        assert handoff_state.current(root) is None
        with handoff_state.scope(root) as state:
            assert not state.sources and not state.results and not state.observations
            with _verification_counts(monkeypatch,root,length) as restored, monkeypatch.context() as cold:
                def forbidden(*args,**kwargs):
                    raise AssertionError('Cold restore reinterpreted already sealed scientific evidence')
                for name in ('science_payload','envelope','_sealed_science'):
                    cold.setattr(c,name,forbidden)
                cold.setattr(semantic,'outcome',forbidden)
                for step in range(1,length+1):
                    job,payload,seal,_=history._source(root,step)
                    assert payload['scientific_result']==UNKNOWN
                    assert handoff_state.read_sealed(root,step)['scientific_result']==UNKNOWN
                    history.read_observation(root,job,seal)
            _no_old_verification(restored)
            assert restored['stat_ancient']==restored['stat_direct']==0,dict(restored)
            assert restored['science_read_ancient']+restored['science_read_direct']==length
            with _verification_counts(monkeypatch,root,length) as current:
                job,_,_=finish(root,length+1,'E',[length],text=UNKNOWN,
                    limitations=['Authored synthetic fixture; the sole parent is the only direct input.'])
                shared_input_views.mount_arguments(root,job,c.workspace(root,length+1),include_skills=False)
            assert len(job['direct_refs'])==len(job['direct_originals'])==1
            _no_old_verification(current)
            assert current['stat_ancient']==current['science_read_ancient']==0,dict(current)
            assert current['inventory_ancient']==0,dict(current)
            assert current['current_input_validation']>0 and current['science_read_direct']>0,dict(current)
            current_work.append(dict(current))
    assert current_work[0]==current_work[1]==current_work[2],current_work


def test_new_input_changed_after_binding_is_still_rejected(tmp_path):
    root=_prefix(tmp_path,2)
    with handoff_state.scope(root):
        # The parent is a trusted old finding, then becomes a declared current
        # input. Its actual original remains mandatory at this new boundary.
        history._source(root,2)
        job=c.build_job(root,3,'E',[2])
        assert len(job['direct_originals'])==1
        original=root/'native-evidence/002/science-output.json'
        original.chmod(0o600)
        original.write_bytes(original.read_bytes()+b' ')
        with pytest.raises(ValueError,match='Direct producer original changed|Original size changed'):
            c._validate_inputs(root,c.workspace(root,3),job)


@pytest.mark.parametrize('change', ['replace', 'remove'])
def test_current_recovery_material_is_checked_without_reopening_history(tmp_path, change):
    root=tmp_path/'instance'
    source=root/'native-evidence/002/recovered-output.txt'
    source.parent.mkdir(parents=True)
    source.write_text('result')
    proof=experiments.proof(source)
    material=dict(proof,path='inputs/recovered/002/call-1/result.txt',source=str(source))
    binding=dict(nodes=[],execution_recoveries=[],execution_recovery_materials=[material])
    history.validate(root,c.workspace(root,3),binding)
    source.unlink()
    if change=='replace':source.write_text('different result')
    with pytest.raises((ValueError,OSError)):
        history.validate(root,c.workspace(root,3),binding)


def test_controller_restores_saved_wait_without_old_gates_and_checks_new_admission(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain an unanswered scientific question.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture'],max_tokens=100000,
        max_seconds=120,max_steps=6,model='fixture',reasoning_effort='high',
        search_policy=None,experiment_mode='external')
    launches=[]
    def execute(root,step):
        job=c.read(root/'jobs'/f'{step:03d}.json');launches.append(step)
        parents=[int(row['path'].split('/')[2]) for row in job['direct_refs']]
        finish(root,step,job['role'],parents,action='analyze' if step==1 else 'wait')
    monkeypatch.setattr(native,'execute_native',execute)
    assert controller.run(root)['status']=='wait'
    assert launches==[1,2]
    def forbidden(*args,**kwargs):
        raise AssertionError('Restoring a saved route revalidated sealed history')
    with monkeypatch.context() as cold:
        for name in ('validate','validate_original','_validate_inputs'):
            cold.setattr(c,name,forbidden)
        cold.setattr(semantic,'outcome',forbidden)
        assert controller.run(root)['status']=='wait'
        assert c.seal_result(root,2)['next_action']=='wait'
    calls=[];admission=role_share.effective_route
    def checked(root,step,result,job,route,**kwargs):
        calls.append((step,kwargs.get('allow_repeat',False)))
        return admission(root,step,result,job,route,**kwargs)
    monkeypatch.setattr(role_share,'effective_route',checked)
    assert instance_control.continue_instance(root)['status']=='wait'
    assert launches==[1,2,3]
    assert calls.count((2,True))==1


def test_search_checkpoint_keeps_latest_reconciliation_at_same_source_cut(tmp_path):
    # Derived-summary unit: a later reconciliation changes no scientific node.
    original=search._fold(tmp_path,[])
    latest=deepcopy(original)
    original['opportunity_unknown']=True
    ref=dict(path='execution-recovery/001/call-1/000001.json')
    with handoff_state.scope(tmp_path) as state:
        state.search[((1,),json.dumps([],sort_keys=True))]=original
        state.search[((1,),json.dumps([ref],sort_keys=True))]=latest
        handoff_state.publish_search(tmp_path)
    saved=c.read(tmp_path/'host-history/search-state.json')
    assert saved['recovery_refs']==[ref]
    assert handoff_state.decode_search(saved['state'])==latest
