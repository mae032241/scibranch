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
        assert c.digest(staged)==proof['sha256'] and staged.stat().st_size==proof['size_bytes']
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
    target_before={path:path.read_bytes() for path in (root/'runs/submitted.json',root/'runs/submitted_model.pkl',root/'submission/model.pkl')}
    confirmation=delivery.invoke(root,accepted(root,bound,'confirm'),10)
    assert confirmation['status']=='confirmed' and confirmation['cleanup_confirmed']
    assert phases==['resolve','audit','promote','confirm']
    expected=chosen['objects']['artifact']['sha256']
    assert c.digest(root/'submission/model.pkl')==expected
    assert c.digest(root/'runs/submitted_model.pkl')==expected
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
