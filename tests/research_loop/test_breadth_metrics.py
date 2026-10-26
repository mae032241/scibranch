"""Derived breadth arithmetic and identities, without model or execution claims."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, history, originals, search
from test_portfolio_contract import comparison


def state():
    return dict(mechanisms={ref:dict(label=ref,status='pending') for ref in ('a','b','c')},focus=None,
        order=dict(discriminating_work=[]),portfolio=dict(invested={},deferred={},seen_w=set(),pending_w=None,
            last_branch=None,last_decision_step=None,cycles=[],mainline_reviews=[]))


def w(value,step,branches,monkeypatch,*,mode='screen',repair_root=None,completed=True):
    job=dict(step=step,role='W',search=dict(focus=dict(mechanism_ref=branches[0],action=mode),
        breadth=True,repair_root=repair_root))
    monkeypatch.setattr(history,'read_observation',lambda *args:dict(managed_calls=[dict(mechanism_binding=dict(mechanism_ref=ref)) for ref in branches]))
    search._portfolio_update(None,job,dict(stage_intent='review' if completed else 'decision'),
        dict(observations=True),value,dict(path=f'/exact/seal-{step}'))


def a(value,step,w_step,*,main='a',action='continue_w',correction=None,final=False):
    review=comparison(action=action,main=main);review.update(competitor=None,no_competitor_reason='No viable fixture competitor')
    job=dict(step=step,role='A',direct_refs=[dict(path=f'inputs/parents/{w_step:03d}/conclusion.json')] if w_step else [],
        scientific_response_fields=['portfolio_review'])
    if correction:job['author_correction']=dict(root=dict(step=correction))
    if final:job['final_report']=dict(source_cutoff=step-1)
    search._portfolio_update(None,job,dict(portfolio_review=review,next_action=action,scientific_result='Fixture judgment'),
        dict(control_outcome=dict(search_status='applied',selection_status='applied',decision_status='applied')),value,dict(path=f'/exact/seal-{step}'))
    return job


def test_overlapping_cycles_pending_and_prospective_are_separate(monkeypatch):
    value=state();w(value,2,['a','b','c'],monkeypatch);a(value,3,2)
    w(value,4,['a'],monkeypatch);a(value,5,4)
    w(value,6,['a','b'],monkeypatch)
    before=copy.deepcopy(value)
    actual=search.portfolio_view(value,2);prospective=search.portfolio_view(value,2,prospective=True)
    assert actual['completed_cycle_count']==actual['completed_cycles']==2
    shares={row['mechanism_ref']:row for row in actual['participation']}
    assert [shares[ref]['participation_fraction'] for ref in ('a','b','c')]==[1,.5,.5]
    assert sum(row['participation_fraction'] for row in shares.values())==2
    assert actual['pending_assessment'] and actual['pending_w']['source_step']==6
    assert prospective['completed_cycle_count']==2 and prospective['cycles_if_assessed']==3
    assert actual['main_line']=='a' and actual['mainline_streak']==2
    assert actual['mainline_source']==dict(source_step=5,pointer='/portfolio_review/main_line')
    assert value==before
    a(value,7,None,main='a',action='finish',final=True)
    assert search.portfolio_view(value,2)['completed_cycle_count']==3


def test_unfinished_w_does_not_manufacture_cycle(monkeypatch):
    value=state();w(value,2,['a'],monkeypatch,completed=False)
    a(value,3,None,action='finish',final=True)
    view=search.portfolio_view(value,1,prospective=True)
    assert view['completed_cycle_count']==view['cycles_if_assessed']==0
    assert view['pending_assessment'] and view['pending_w']['completed'] is False
    assert all(row['participation_fraction'] is None for row in view['participation'])


def test_repairs_and_author_corrections_keep_one_cycle_and_exclude_cost_sample(monkeypatch,tmp_path):
    value=state();w(value,2,['a'],monkeypatch);original=a(value,3,2)
    monkeypatch.setattr(history,'_source',lambda root,step:(original,{}, {}, {}))
    a(value,4,2,correction=3)
    assert len(value['portfolio']['seen_w'])==1 and value['portfolio']['cycles'][0]['sample_exclusion']=='author_correction_cycle_consumption_incomplete'
    assert search.portfolio_view(value,2)['mainline_streak']==1
    identity=tmp_path/'result-identity.json';identity.write_text('{}')
    value['portfolio']['cycles'][0]['w_identity']=(2,str(identity))
    value['portfolio']['seen_w']={(2,str(identity))}
    monkeypatch.setattr(originals,'node',lambda root,step:None)
    monkeypatch.setattr(originals,'source_record',lambda *args:identity)
    monkeypatch.setattr(history,'source_seal_ref',lambda *args:dict(path=str(identity)))
    w(value,5,['a'],monkeypatch,mode='repair',repair_root=2);a(value,6,5)
    assert len(value['portfolio']['seen_w'])==1 and value['portfolio']['invested']=={'a':1}
    assert value['portfolio']['cycles'][0]['sample_exclusion']=='repair_cycle_consumption_incomplete'


def test_first_author_correction_closes_one_cycle_but_excludes_incomplete_cost(monkeypatch):
    value=state();w(value,2,['a'],monkeypatch)
    original=dict(step=3,role='A',direct_refs=[dict(path='inputs/parents/002/conclusion.json')])
    monkeypatch.setattr(history,'_source',lambda *args:(original,{}, {}, {}))
    a(value,4,2,correction=3)
    assert len(value['portfolio']['seen_w'])==1
    assert value['portfolio']['cycles'][0]['sample_exclusion']=='author_correction_cycle_consumption_incomplete'


def test_authored_mainline_ignores_w_branch_and_null_interrupts_streak(monkeypatch):
    value=state();w(value,2,['b','c'],monkeypatch);a(value,3,2,main='a',action='extract')
    assert not value['portfolio']['seen_w']
    assert search.portfolio_view(value,2)['mainline_streak']==1
    a(value,4,2,main=None,action='extract')
    assert search.portfolio_view(value,2)['mainline_streak']==0
    a(value,5,2,main='a')
    assert search.portfolio_view(value,2)['mainline_streak']==1


def test_inherited_repair_uses_origin_identity_not_archive_location(monkeypatch,tmp_path):
    value=state();w(value,2,['a'],monkeypatch);a(value,3,2)
    origin_path='/parent/native-evidence/002/result-identity.json'
    cycle=value['portfolio']['cycles'][0];cycle['w_identity']=(2,origin_path)
    value['portfolio']['seen_w']={(2,origin_path)}
    archive=tmp_path/'inherited/retained-seal.json';archive.parent.mkdir();archive.write_text('{}')
    monkeypatch.setattr(originals,'node',lambda *args:None)
    monkeypatch.setattr(originals,'source_record',lambda *args:archive)
    monkeypatch.setattr(history,'source_seal_ref',lambda *args:dict(path=origin_path))
    w(value,4,['a'],monkeypatch,mode='repair',repair_root=2);a(value,5,4)
    assert value['portfolio']['seen_w']=={(2,origin_path)}
    assert value['portfolio']['invested']=={'a':1} and len(value['portfolio']['cycles'])==1
    assert cycle['sample_exclusion']=='repair_cycle_consumption_incomplete'
