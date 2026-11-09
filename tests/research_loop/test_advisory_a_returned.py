"""A return marker belongs to control and never fills the scientific original."""
import copy
from unittest.mock import patch

import pytest

from scidiscovery.research_loop import (
    accepted_budget, contracts as c, controller, decision, guidance, supervision,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_adaptive_a_budget import _prepare_publication_instance, _publish_synthetic
from test_worker_e_consultation import publish


def test_effective_advice_without_redundant_marker_seals_and_covers_exact_cut(tmp_path):
    root=_prepare_publication_instance(tmp_path,
        supervision_policy=supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY)
    worker=c.build_job(root,1,'W',[])
    publish(root,worker,tokens=1100,stage_intent='handoff')
    produced=controller._supervision_facts(root,c.settings(root))
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    assessor=c.build_job(root,2,'A',[1],supervision_request=request)
    output=c.host_record(c.workspace(root,2))/'science-output.json';write=c.write
    def omit_marker(path,value):
        if path==output:
            value=dict(value);value.pop('supervision_result',None)
        return write(path,value)
    with patch.object(c,'write',omit_marker):
        _publish_synthetic(root,assessor,tokens=30)
    before=output.read_bytes();payload=c.read_scientific_output(output)
    assert decision.advice_returned(assessor,payload)
    assert decision.validate_supervision(assessor,payload)=='advice_returned'
    assert 'supervision_result' not in guidance.role_contract(assessor)
    c.seal_result(root,2)
    state=accepted_budget.read(root)['search']['supervision']
    assert state['last_covered_cutoff']['assessment_step']==2
    assert state['adaptive']['covered_question_revision']==request['adaptive_cutoff']['question_revision']
    assert state['automatic_reviews']==1 and state['adaptive']['automatic_cost']['tokens']==30
    assert output.read_bytes()==before and 'supervision_result' not in c.read_scientific_output(output)
    assert not (output.parent/'continuations').exists()


@pytest.mark.parametrize('change',[
    dict(supervision_result='request_evidence'),dict(supervision_result='finalize'),
    dict(supervision_result='unknown'),dict(next_action='extract'),
    dict(decision_request={}),dict(scientific_result=' '),
])
def test_conflicting_or_missing_scientific_advice_still_requires_author_correction(change):
    job=dict(role='A',research_strategy_version=3,advisory_scope_version=1,supervision_request={})
    payload=dict(scientific_result='A scoped evidence-backed concern.',next_action='continue_w',decision_request=None)
    payload.update(change);before=copy.deepcopy(payload)
    assert not decision.advice_returned(job,payload)
    with pytest.raises(OutputRepairRequired):decision.require_request(job,payload)
    assert payload==before


def test_legacy_supervision_still_requires_its_authored_result():
    job=dict(role='A',research_strategy_version=3,supervision_request={})
    payload=dict(scientific_result='Legacy assessment',next_action='continue_w',decision_request=None)
    assert not decision.advice_returned(job,payload)
    with pytest.raises(OutputRepairRequired):decision.validate_supervision(job,payload)
    assert decision.validate_supervision(job,dict(payload,supervision_result='continue_existing'))=='continue_existing'


def test_operational_only_automatic_request_cannot_bypass_job_admission(tmp_path):
    root=_prepare_publication_instance(tmp_path,
        supervision_policy=supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY)
    worker=c.build_job(root,1,'W',[])
    publish(root,worker,tokens=1100,stage_intent='handoff',research_memory=None)
    produced=controller._supervision_facts(root,c.settings(root))
    status=supervision.due(produced['summary'],budget_facts=produced['budget_facts'])
    assert status['operational_due'] and not status['fresh_input']
    assert not status['automatic_allowed']
    request=supervision.request(produced['summary'],automatic=True,budget_facts=produced['budget_facts'])
    with pytest.raises(ValueError,match='not eligible'):
        c.build_job(root,2,'A',[1],supervision_request=request)
    assert not (root/'jobs/002.json').exists()
