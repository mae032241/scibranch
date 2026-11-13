"""W sees cross-mechanism reference syntax before existing exact-call gates run."""
import copy

import pytest

from scidiscovery.research_loop import backends, contracts as c, guidance, worker_delivery
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_r7_shared_screen import REFS, comparison
from test_result_schema import rendered_contract


@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_actual_worker_request_explains_optional_observed_calls_and_keeps_three_kib(tmp_path,backend_name):
    job=dict(role='W',handoff_protocol=c.HANDOFF_PROTOCOL,research_strategy_version=3,advisory_scope_version=1,search={},
        scientific_response_fields=['mechanism_findings','candidate_nominations'],
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST['W'],
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
    assert 'research_memory?' in instruction
    assert 'review=A advice' in instruction and 'extract=E evidence' in instruction
    assert 'consultation_question' in instruction
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072


def test_one_exact_comparative_call_supports_multiple_authored_observations(comparison):
    scene=comparison
    scene.job.update(research_strategy_version=3,advisory_scope_version=1)
    call=scene.terminal('0001-comparison')
    source=scene.job['search']['breadth']['mechanisms'][-1]
    call['mechanism_binding'].update({key:source[key] for key in ('source_step','source_ref','source_role')})
    accepted_path=scene.root/call['accepted']['snapshot']
    accepted=c.read(accepted_path)
    accepted['mechanism_binding']=dict(call['mechanism_binding'])
    c.write(accepted_path,accepted)
    call['accepted']['size_bytes']=accepted_path.stat().st_size
    scene.capture()
    result=worker_delivery.require_delivery(scene.root,scene.job,scene.science,scene.record,inventory=scene.inventory)
    assert result['status']=='complete' and result['missing']==[]
    assert len(scene.calls)==1
    assert {row['mechanism'] for row in scene.science['mechanism_findings']}==set(REFS)
    assert {row['call_id'] for row in scene.science['mechanism_findings']}=={'0001-comparison'}
    without_reference=copy.deepcopy(scene.science)
    for row in without_reference['mechanism_findings']:row.pop('call_id')
    with pytest.raises(OutputRepairRequired) as missing:
        worker_delivery.require_delivery(scene.root,scene.job,without_reference,scene.record,inventory=scene.inventory)
    assert missing.value.code=='worker_delivery_reference'
    binding=copy.deepcopy(call['mechanism_binding'])
    for reference in [None,'0000-not-retained']:
        unresolved=copy.deepcopy(without_reference)
        unresolved['stage_intent']='continue'
        if reference is not None:
            for row in unresolved['mechanism_findings']:row['call_id']=reference
        with pytest.raises(OutputRepairRequired) as error:
            worker_delivery.require_delivery(scene.root,scene.job,unresolved,scene.record,inventory=scene.inventory)
        assert error.value.code=='worker_delivery_reference'
        assert len(scene.calls)==1 and call['mechanism_binding']==binding
