"""Trusted adapter DI and synthetic native identities; real source/seal consumers."""
from copy import deepcopy
import json
import uuid

import pytest

from scidiscovery.research_loop import (contracts as c, controller, execution_inputs as inputs,
    formal_review as review, history, qualification as q)
from test_history_handoff import finish


def test_observer_declared_sources_bind_authors_and_never_upgrade_display_coverage(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Retain exact observed source contributions.')
    adapter=tmp_path/'observer.py';adapter.write_text('# Trusted adapter fixture; never executed\n')
    launcher=tmp_path/'codex';launcher.write_text('# Synthetic identity only; never executed\n');launcher.chmod(0o700)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)],max_tokens=1000,max_seconds=60,max_steps=5,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='external',
        formal_review_policy={'version':1},source_restrictions=[dict(source=str(objective),restriction='clear')],
        history_observer=dict(adapter=str(adapter),config={},source_contract=inputs.SOURCE_CONTRACT))
    original_seal=c.seal_result;calls=[]

    def seal(root,step):
        work=c.workspace(root,step);record=c.host_record(work);thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'observer-source-'+str(step)))
        home=tmp_path/'identity';session=home/'sessions'/(thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
        session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
        (record/'native.jsonl').write_text(json.dumps(dict(type='thread.started',thread_id=thread))+'\n')
        outcome=c.read(record/'native-outcome.json');outcome.update(thread_id=thread,
            native_identity=dict(participant_scope='codex_exec_single_agent_v1',home=str(home),path=str(session)))
        c.write(record/'native-outcome.json',outcome)
        return original_seal(root,step)

    def observe(config,request):
        calls.append(request['step'])
        origins=[row['origin'] for row in request['input_sources']['available'] if row['origin']['kind']=='producer']
        return dict(candidates=[],current_candidate=None,actual_submission=None,
            coverage=dict(status='complete',note='Display coverage alone is not source coverage.'),
            input_sources=dict(version=1,complete=True,sources=origins))

    monkeypatch.setattr(c,'seal_result',seal);monkeypatch.setattr(history,'read_adapter',observe)
    finish(root,1,'H',[],action='experiment',files={'source.txt':'An observed scientific source.'})
    job,_,record=finish(root,2,'W',[1],files={'derivative.txt':'An observed derivative.'})
    assert job.get('selected_inputs',[])==[]
    seal=c.read(record/'result-identity.json');observation=history.read_observation(root,job,seal)
    assert calls==[1,2] and q.output_status(root,2)['restriction']=='clear'
    people=review.authors(root,2)
    assert people['complete'] and set(map(tuple,review.authors(root,1)['participants']))<=set(map(tuple,people['participants']))
    monkeypatch.setattr(history,'read_adapter',lambda *args:pytest.fail('Readonly source/author checks must not rerun an adapter'))
    assert q.output_status(root,2)['restriction']=='clear' and review.authors(root,2)==people
    missing={key:value for key,value in observation.items() if key!='input_sources'}
    assert history.observer_input_sources(root,job,missing) is None
    changed=deepcopy(observation);changed['input_sources']['view']['sources']=[dict(kind='unknown')]
    assert history.observer_input_sources(root,job,changed)['complete'] is False
    changed=deepcopy(observation);changed['input_sources']['view']['sources']=[dict(kind='current_author',step=1)]
    with pytest.raises(q.IntegrityError,match='another job'):
        history.observer_input_sources(root,job,changed)
    changed=deepcopy(observation);changed['input_sources']['view']['sources'][0]['original']['producer_step']=2
    with pytest.raises(q.IntegrityError,match='frozen compiler source'):
        history.observer_input_sources(root,job,changed)
    # Removing the independently frozen trusted contract never interprets the
    # existing display 'complete' as source qualification.
    legacy=history.observer_config(dict(adapter=str(adapter),config={}))
    assert 'source_contract' not in legacy
    original=root/seal['observations']['path'];saved=original.read_bytes();original.chmod(0o600)
    original.write_bytes(saved+b' ')
    with pytest.raises((q.IntegrityError,history.ObserverIntegrityError),match='snapshot changed|receipt changed'):
        q.output_status(root,2)
