"""A owns changing scientific criteria; selection_explanation is optional authored text."""
import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution
from test_candidate_response_contract import response
from test_history_handoff import selection_explanation
from test_worker_resume import REPAIR_FAKE, instance


@pytest.mark.parametrize('explanation',[None,selection_explanation(),
    dict(criteria='  ',criteria_change=dict(status='revised'))])
def test_selection_explanation_is_retained_without_control_scoring(tmp_path,explanation):
    # Control never scores or completes the scientific account; it keeps authored bytes.
    payload=response('W002-C001');payload['selection_explanation']=explanation
    raw=tmp_path/'science-output.json';c.write(raw,payload);original=raw.read_bytes()
    assert c.science_payload(raw,'A')==payload
    assert raw.read_bytes()==original


def test_planning_a_does_not_need_an_invented_selection(tmp_path):
    payload=response();payload.pop('selection_explanation')
    raw=tmp_path/'science-output.json';c.write(raw,payload)
    assert c.science_payload(raw,'A')==payload


def author_fake():
    program=REPAIR_FAKE.replace("candidate_recommendation=None,progress_assessment=None",
        "candidate_recommendation='unavailable-fixture',progress_assessment=None")
    return program


@pytest.mark.process_e2e
def test_final_selection_output_repair_does_not_retry_unknown_execution(tmp_path):
    root=instance(tmp_path,fake=author_fake(),output_repair=True,steps=1)
    budget.begin_closeout(root,0,1,'Exact final unknown fixture',final_only=True)
    c.build_job(root,1,'A',[],final_report=dict(source_cutoff=0,stopped_original=None))
    (c.workspace(root,1)/'work').mkdir(exist_ok=True)
    (c.workspace(root,1)/'work/output-kind').write_text('unknown')
    result=controller.run(root)
    assert result['status']=='closeout_native_unsealed'
    assert len(execution.records(root,1))==1
    assert (c.workspace(root,1)/'work/launches').read_text()=='exec\n'
