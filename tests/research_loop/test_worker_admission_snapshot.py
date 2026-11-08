"""Dispatch consumes the declaration admitted before external preparation."""
import copy
from contextlib import nullcontext

import pytest

from scidiscovery.research_loop import experiments, worker_research as research
from test_worker_research_admission import batch, config


def test_dependent_merges_are_not_replayed_after_external_preparation(tmp_path,monkeypatch):
    selected=config(tmp_path);selected['max_seconds']=60
    initial=research.initial(selected)
    parent,_=research.prepare(selected,initial,batch())
    research.publish(selected,initial,parent)
    request=dict(declarations=[
        dict(action='merge',mechanism_ref='w001.bridge',question='Intermediate explanation',predecessors=['w001.m1']),
        dict(action='merge',mechanism_ref='w001.terminal',question='Final explanation',predecessors=['w001.bridge']),
    ],mechanism_ref='w001.terminal',experiment_question=dict(question='Which control changes the decision?',change_criterion='Change the comparison if the control is absent.'))
    records=experiments.Records(selected)
    monkeypatch.setattr(records,'calls',lambda:[])
    monkeypatch.setattr(records,'admission_lease',lambda **kwargs:nullcontext())
    monkeypatch.setattr(experiments,'review_status',lambda config:dict(requested=False,enforced=False))
    monkeypatch.setattr(experiments,'_require_new_execution',lambda config:None)
    binding=experiments.Records.preflight.__wrapped__(records,expected_calls=[],mechanism=request)
    saved=research.load(selected)
    assert saved['revision']==2 and saved['mechanisms']['w001.bridge']['status']=='retired'
    records._preparation_node_held=True
    monkeypatch.setattr(research,'load',lambda *args:pytest.fail('Dispatch already owns its admitted declaration facts'))
    monkeypatch.setattr(research,'prepare',lambda *args:pytest.fail('Do not replay declarations after external preparation'))
    admitted=records.admission_binding(request,expected_calls=[],preflight_binding=binding)
    assert admitted==binding and records._research_state==saved
    changed=copy.deepcopy(binding);changed['research_binding']['question']='A different prepared explanation'
    with pytest.raises(ValueError,match='changed its accepted'):
        records.admission_binding(request,expected_calls=[],preflight_binding=changed)
    records._preparation_node_held=False
    with pytest.raises(ValueError,match='original dispatch preflight'):
        records.admission_binding(request,expected_calls=[],preflight_binding=binding)


def test_prepared_snapshot_does_not_bypass_a_later_admission_stop(tmp_path,monkeypatch):
    selected=config(tmp_path);state,binding=research.prepare(selected,research.initial(selected),batch())
    records=experiments.Records(selected);records._research_state=state;records._preparation_node_held=True
    monkeypatch.setattr(records,'calls',lambda:[])
    monkeypatch.setattr(experiments,'review_status',lambda config:dict(requested=True,enforced=True,reason='direction_review_due'))
    with pytest.raises(experiments.ReviewRequired):
        records.admission_binding(None,expected_calls=[],preflight_binding=binding)
    monkeypatch.setattr(records,'calls',lambda:[tmp_path/'another-call'])
    with pytest.raises(experiments.ConcurrentExecution):
        records.admission_binding(None,expected_calls=[],preflight_binding=binding)
