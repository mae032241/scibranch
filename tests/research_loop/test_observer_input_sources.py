"""Trusted adapter DI and synthetic native identities; real source/seal consumers."""
from copy import deepcopy

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
    calls=[]

    def participants(root,job,seal,*,source=None):
        # Exact native proof validation belongs to the formal-review tests.
        # Distinct participants here isolate source traversal and receipt binding.
        return dict(complete=True,participants=[['codex_exec',f'observer-source-{job["step"]}']],reason=None)

    def observe(config,request):
        calls.append(request['step'])
        origins=[row['origin'] for row in request['input_sources']['available'] if row['origin']['kind']=='producer']
        return dict(candidates=[],current_candidate=None,actual_submission=None,
            coverage=dict(status='complete',note='Display coverage alone is not source coverage.'),
            input_sources=dict(version=1,complete=True,sources=origins))

    monkeypatch.setattr(review,'participants',participants);monkeypatch.setattr(history,'read_adapter',observe)
    finish(root,1,'H',[],action='experiment',files={'source.txt':'An observed scientific source.'})
    job,_,record=finish(root,2,'W',[1],files={'derivative.txt':'An observed derivative.'})
    assert job.get('selected_inputs',[])==[]
    seal=c.read(record/'result-identity.json');observation=history.read_observation(root,job,seal)
    assert calls==[1,2] and q.output_status(root,2)['restriction']=='clear'
    people=review.authors(root,2)
    assert review.authors(root,1)['participants']==[['codex_exec','observer-source-1']]
    assert people['complete'] and people['participants']==[['codex_exec','observer-source-1'],['codex_exec','observer-source-2']]
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
