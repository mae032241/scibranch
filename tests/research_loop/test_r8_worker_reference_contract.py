"""W sees cross-mechanism reference syntax before existing exact-call gates run."""
import copy

import pytest

from scidiscovery.research_loop import backends, contracts as c, guidance, worker_delivery
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_r7_shared_screen import REFS, comparison
from test_result_schema import rendered_contract


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_actual_worker_request_explains_optional_observed_calls_and_keeps_three_kib(tmp_path,backend_name):
    job=dict(role='W',handoff_protocol=c.HANDOFF_PROTOCOL,search={},
        scientific_response_fields=['mechanism_findings','candidate_nominations'],
        native_role_instruction=max((guidance.OPPORTUNITY['W'],c.ROLE_INSTRUCTIONS['W']),key=lambda value:len(value.encode())),
        scientific_work_unit=max(guidance.WORK_UNITS.values(),key=lambda value:len(value.encode())))
    task=c.response_instructions(job)
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent']),
        schema_transport='prompt' if backend_name=='codex' else None)
    diagnosis=OutputRepairRequired('worker_delivery_reference','D'*512).request()
    size,_,_,_=rendered_contract(tmp_path,job,task,dict(work_repair=diagnosis),backend,remaining={},
        execution_interface=dict(capabilities={},result_protocol='local-result-v1',executor='local_execution:command'),
        managed_command=['experiment'],budget_status=dict(shared_seconds_remaining=0,shared_observed_tokens_remaining=0))
    instruction=task['stage_response']
    assert 'Optional mechanism_findings' in instruction
    assert 'basis:"observed"' in instruction and 'call_id:exact_retained_id' in instruction
    assert 'Reuse comparative call for supported mechanisms' in instruction and 'basis:"blocked"' in instruction
    assert 'Optional candidate_nominations:{representatives:[{mechanism_ref:' in instruction
    assert instruction.count('candidate_nominations')==1
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072


def test_one_exact_comparative_call_supports_multiple_authored_observations(comparison):
    scene=comparison
    scene.terminal('0001-comparison')
    scene.capture()
    result=worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    assert result['status']=='complete' and result['missing']==[]
    assert len(scene.calls)==1
    assert {row['mechanism'] for row in scene.science['mechanism_findings']}==set(REFS)
    assert {row['call_id'] for row in scene.science['mechanism_findings']}=={'0001-comparison'}
    without_reference=copy.deepcopy(scene.science)
    for row in without_reference['mechanism_findings']:row.pop('call_id')
    with pytest.raises(OutputRepairRequired) as error:
        worker_delivery.require_delivery(scene.root,scene.job,without_reference,scene.record,inventory=scene.inventory)
    assert error.value.code=='worker_delivery_reference' and len(scene.calls)==1
