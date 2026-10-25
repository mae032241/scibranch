"""Real isolate/bwrap/leaf guards across delivery phases; zero model services."""
from scidiscovery.research_loop.protocol import CURRENT
import json
import os
from pathlib import Path
import subprocess

import pytest
from execution_fixtures import execution_registry
from scidiscovery.research_loop import budget,contracts as c,delivery
from test_cov_delivery_lineage import managed_scene,current_managed_scene
from test_delivery import accepted,closeout_decision


@pytest.mark.process_e2e
def test_unknown_submit_confirms_exact_pending_marker_with_read_only_targets(tmp_path,monkeypatch,execution_registry):
    from cov_execution import delivery as cov_delivery
    from scidiscovery.research_loop import history
    from test_direct_public_collect import stopped_attempt
    import sys
    import traceback
    observer_errors=[];capture=history._capture;run=subprocess.run
    def capture_with_diagnostics(root,job,*args,**kwargs):
        adapter=(c.settings(root,job).get('history_observer') or {}).get('adapter')
        expected=[sys.executable,'-I',str(Path(adapter))] if adapter else None
        stderr=[]
        def run_with_diagnostics(command,*options,**settings):
            try:
                return run(command,*options,**settings)
            finally:
                stream=settings.get('stderr')
                if command==expected and stream is not None:
                    position=None
                    try:
                        position=stream.tell();stream.seek(0,os.SEEK_END)
                        stream.seek(max(0,stream.tell()-8192))
                        tail=stream.read()
                        if tail:stderr.append(tail.decode(errors='replace'))
                    except (OSError,ValueError,AttributeError):
                        pass
                    finally:
                        if position is not None:
                            try:stream.seek(position)
                            except (OSError,ValueError):pass
        try:
            with monkeypatch.context() as context:
                context.setattr(subprocess,'run',run_with_diagnostics)
                return capture(root,job,*args,**kwargs)
        except Exception as error:
            observer_errors.append(dict(step=job['step'],failure_type=type(error).__name__,
                message=str(error)[:4096],traceback=traceback.format_exc(limit=8)[-8192:],stderr=stderr))
            raise
    monkeypatch.setattr(history,'_capture',capture_with_diagnostics)
    seal=c.seal_result
    def seal_synthetic_author(root,step):
        # These Node authors are presealed fixture records; scientific callback
        # calls and all delivery processes run with the real process validator.
        assert step in (1,2,3)
        job=c.read(root/'jobs'/f'{step:03d}.json')
        with monkeypatch.context() as context:
            stopped_attempt(root,job,c.host_record(c.workspace(root,step)),context)
            return seal(root,step)
    monkeypatch.setattr(c,'seal_result',seal_synthetic_author)
    root,nodes,first,*_=current_managed_scene(tmp_path,monkeypatch)
    phases=[];original=delivery._call;confirmed_mounts=[]
    marker=root/'runs/rootless-delivery-operation.json'
    pending=marker.with_name(marker.name+'.rootless-pending')
    def observe(spec,request,seconds,**kwargs):
        phases.append(request['phase'])
        if request['phase']=='confirm':
            # Inspect the production namespace arguments before running the
            # same real isolated entry. No harness or adapter bytes change.
            commands=[]
            with monkeypatch.context() as context:
                context.setattr(cov_delivery.os,'execv',lambda program,args:commands.append(args))
                cov_delivery.isolate(request,spec['adapter'])
            arguments=commands[0]
            assert '--bind' not in arguments
            for key in ('runs','submission'):
                assert any(arguments[index:index+3]==['--ro-bind',spec['target'][key],'/target/'+key]
                    for index in range(len(arguments)-2))
            confirmed_mounts.append(request['operation_id'])
        response=original(spec,request,seconds,**kwargs)
        if request['phase']=='promote':
            assert response['status']=='confirmed'
            data=marker.read_bytes()
            marker.replace(pending)
            assert pending.read_bytes()==data
            # The accepted call completed and cleaned up, but its response was
            # lost. Retain that unknown outcome and reconcile this operation.
            return None
        return response
    monkeypatch.setattr(delivery,'_call',observe)
    refs=delivery.freeze_sources(root,nodes)
    selected=[item for item in refs['items'] if item['candidate_id']==first['candidate_id']]
    if not selected:
        observations=[]
        for node in nodes:
            try:
                seal=c.read(c.host_record(c.workspace(root,node['step']))/'result-identity.json')
                proof=seal.get('observations')
                fact=c.read(root/proof['path']) if isinstance(proof,dict) and 'path' in proof else {}
                observations.append(dict(step=node['step'],proof=proof,
                    fact={key:fact[key] for key in ('coverage','diagnostic','candidate_diagnostics') if key in fact},
                    candidate_ids=[row['id'] for row in fact.get('candidates',[])],
                    contexts=[dict(call_id=row['call_id'],status=row['status']) for row in fact.get('contexts',[])],
                    managed_calls=[dict(call_id=row['call_id'],status=row['status']) for row in fact.get('managed_calls',[])]))
            except (OSError,ValueError,KeyError) as error:
                observations.append(dict(step=node['step'],diagnostic_error=type(error).__name__))
        diagnostic=dict(expected_candidate_id=first['candidate_id'],refs=refs,phases=phases,
            observations=observations,observer_errors=observer_errors)
        log=os.environ.get('SCID_TEST_LOG_DIR')
        if log:
            try:
                directory=Path(log);directory.mkdir(parents=True,exist_ok=True)
                (directory/'g4-delivery-missing-candidate.json').write_text(json.dumps(diagnostic,indent=2)+'\n')
            except OSError as error:
                diagnostic['log_error']=type(error).__name__
        assert selected,diagnostic
    chosen=selected[0]
    assert chosen['status']=='audit_pending'
    originals={root/proof['path'] for proof in (chosen['ledger'],*chosen['objects'].values(),chosen['artifact_origin'])}
    def snapshot(paths):
        return {path:(cov_delivery._generation(path),path.read_bytes()) for path in paths}
    original_before=snapshot(originals)
    _,_,bound=closeout_decision(root,refs,recommendation=first['candidate_id'])
    submit=accepted(root,bound)
    unknown=delivery.invoke(root,submit,20)
    assert unknown['status']=='unknown' and unknown['cleanup_confirmed']
    assert unknown['operation_id']==bound['operation_id']
    budget.action_outcome(root,submit,unknown)
    targets=(root/'runs/submitted.json',root/'runs/submitted_model.pkl',root/'submission/model.pkl',pending)
    target_before=snapshot(targets)
    assert not marker.exists()
    confirmation=accepted(root,bound,'confirm')
    assert confirmation['operation_id']==submit['operation_id'] and confirmation['binding']==submit['binding']
    result=delivery.invoke(root,confirmation,10)
    assert result['status']=='confirmed' and result['cleanup_confirmed']
    budget.action_outcome(root,confirmation,result)
    assert phases==['resolve','audit','promote','confirm']
    assert confirmed_mounts==[bound['operation_id']]
    assert snapshot(targets)==target_before and snapshot(originals)==original_before
    assert not marker.exists() and pending.is_file()
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert actions[0]['outcome']['result']==unknown


@pytest.mark.process_e2e
def test_real_isolated_resolve_audit_promote_and_confirm(tmp_path,monkeypatch,execution_registry):
    root,nodes,first,*_=current_managed_scene(tmp_path,monkeypatch)
    settings=(root/'settings.json').read_bytes();phases=[];resolved=[]
    original=delivery._call
    def observe(spec,request,seconds,**kwargs):
        phases.append(request['phase'])
        if request['phase'] in {'audit','smoke'}:assert 'target' not in request
        elif request['phase'] in {'promote','confirm'}:assert request['target']==spec['target']
        proof=next(p for p in request['inputs'] if p['label']=='scid-current-protocol')
        staged=Path(request['stage'])/'inputs/scid-current-protocol'
        assert staged.stat().st_size==proof['size_bytes']
        assert request['scope']['handoff_protocol']==CURRENT
        response=original(spec,request,seconds,**kwargs)
        if request['phase']=='resolve' and isinstance(response,dict):
            resolved.extend({key:item.get(key) for key in ('candidate_id','status','reason')} for item in response.get('items',[])[:8])
        return response
    monkeypatch.setattr(delivery,'_call',observe)
    refs=delivery.freeze_sources(root,nodes)
    chosen=next(item for item in refs['items'] if item['candidate_id']==first['candidate_id'])
    assert chosen['status']=='audit_pending', ({key:value for key,value in chosen.items() if key not in {'objects','source','row'}},resolved)
    _,_,bound=closeout_decision(root,refs,recommendation=first['candidate_id'])
    submit=accepted(root,bound)
    result=delivery.invoke(root,submit,20)
    assert result['status']=='confirmed' and result['cleanup_confirmed']
    budget.action_outcome(root,submit,result)
    from cov_execution import lineage as cov_lineage
    observed=cov_lineage.observe(dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config']))
    assert observed['actual_submission']['candidate_id']==first['candidate_id']
    target_before={path:path.read_bytes() for path in (root/'runs/submitted.json',root/'runs/submitted_model.pkl',root/'submission/model.pkl')}
    confirmation=delivery.invoke(root,accepted(root,bound,'confirm'),10)
    assert confirmation['status']=='confirmed' and confirmation['cleanup_confirmed']
    assert phases==['resolve','audit','promote','confirm']
    expected=(root/chosen['objects']['artifact']['path']).read_bytes()
    assert (root/'submission/model.pkl').read_bytes()==expected
    assert (root/'runs/submitted_model.pkl').read_bytes()==expected
    assert (root/'settings.json').read_bytes()==settings
    assert all(path.read_bytes()==data for path,data in target_before.items())


@pytest.mark.process_e2e
@pytest.mark.parametrize('phase',['resolve','audit','promote','confirm'])
def test_each_actual_leaf_rejects_tampered_protocol_before_target_write(tmp_path,monkeypatch,execution_registry,phase):
    _negative(tmp_path,monkeypatch,phase,'bytes')


@pytest.mark.process_e2e
@pytest.mark.parametrize('scope',[13,True])
def test_isolated_entry_rejects_retired_or_boolean_protocol(tmp_path,monkeypatch,execution_registry,scope):
    _negative(tmp_path,monkeypatch,'resolve','scope',scope)


@pytest.mark.process_e2e
def test_actual_leaf_requires_protocol_proof(tmp_path,monkeypatch,execution_registry):
    _negative(tmp_path,monkeypatch,'resolve','missing_proof')


def _negative(tmp_path,monkeypatch,phase,damage,scope=None):
    root,nodes,first,*_=managed_scene(tmp_path)
    original=delivery._call;seen=[]
    def call(spec,request,seconds,**kwargs):
        if request['phase']!=phase:return original(spec,request,seconds,**kwargs)
        request=dict(request)
        if damage=='bytes':
            protocol=Path(request['stage'])/'inputs/scid-current-protocol'
            protocol.chmod(0o600);protocol.write_bytes(protocol.read_bytes()+b'\n# altered frozen original\n')
        elif damage=='missing_proof':
            request['inputs']=[p for p in request['inputs'] if p['label']!='scid-current-protocol']
        else:request['scope']=dict(handoff_protocol=scope)
        before={p:p.read_bytes() for directory in (root/'runs',root/'submission') for p in directory.rglob('*') if p.is_file()}
        reply=subprocess.run([*spec['launcher_argv'],spec['adapter']],input=json.dumps(request),
            env={'PATH':os.defpath},capture_output=True,text=True,timeout=10,start_new_session=True)
        assert reply.returncode!=0 and not reply.stdout
        expected=f'requires current research-loop protocol {CURRENT}' if damage=='scope' else 'Frozen current protocol original differs'
        assert expected in reply.stderr and 'TypeError' not in reply.stderr
        assert before=={p:p.read_bytes() for directory in (root/'runs',root/'submission') for p in directory.rglob('*') if p.is_file()}
        seen.append(request['phase'])
        return None
    monkeypatch.setattr(delivery,'_call',call)
    if phase=='resolve':
        with pytest.raises(delivery.DeliveryUnresolved,match='delivery_resolver_unavailable'):
            delivery.freeze_sources(root,nodes)
    else:
        refs=delivery.freeze_sources(root,nodes)
        _,_,bound=closeout_decision(root,refs,recommendation=first['candidate_id'])
        submit=accepted(root,bound)
        result=delivery.invoke(root,submit,20)
        if phase=='confirm':
            assert result['status']=='confirmed'
            budget.action_outcome(root,submit,result)
            result=delivery.invoke(root,accepted(root,bound,'confirm'),10)
        assert result['status']==('unclassified' if phase=='audit' else 'unknown')
    assert seen==[phase]
