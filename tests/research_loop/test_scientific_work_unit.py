"""Frozen W task units reach actual native turns without changing stage control."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, native, search
from execution_fixtures import execution_registry
from test_history_handoff import finish


def assignment(tmp_path, *, action='improve', custom=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Preserve required scientific checks and original observations.')
    root=tmp_path/'instance'
    advice={role:'Original custom '+role+' scientific criteria.' for role in 'HWAE'} if custom else guidance.ADVISORY_WORKER_FIRST
    controller.prepare(root,objective,public,['never-launch'],max_tokens=100000,max_seconds=120,max_steps=6,
        model='fixture',reasoning_effort='high',search_policy=search.DEFAULT_POLICY,role_guidance=advice)
    finish(root,1,'H',[],action='experiment',search_choice=dict(
        commitments=[dict(label='Revisable explanation',existing_ref=None)],focus=dict(index=1,action=action)))
    return root


@pytest.mark.parametrize('action',['screen','improve'])
@pytest.mark.parametrize('backend_name',['codex','protocol'])
def test_current_worker_request_retains_frozen_work_unit_without_native_execution(tmp_path,action,backend_name):
    from scidiscovery.research_loop import backends
    root=assignment(tmp_path,action=action)
    job=c.build_job(root,2,'W',[1],instruction='Keep the required comparison and exact validation split.')
    work=c.workspace(root,2);task=c.read(work/'science-task.json')
    paths=[root/'jobs/002.json',work/'science-task.json']
    originals={path:path.read_bytes() for path in paths}
    literal=guidance.WORK_UNITS[action]
    assert job['scientific_work_unit']==literal
    assert task['question'].count(literal)==1
    assert task['question'].startswith(job['instruction']+'\n')
    backend=backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name=='codex' else ['independent']),
        schema_transport='prompt' if backend_name=='codex' else None)
    prompt,instruction,schema=native.render_scientific_request(job,task,{},backend_name=backend.name,
        instruction_transport=backend.instruction_transport)
    schema_path=work/'response.schema.json';c.write(schema_path,schema)
    packet=backend.request(prompt,schema=schema_path,profile={},work=work,response_schema=schema)
    delivered=packet.decode() if backend_name=='codex' else json.loads(packet)['prompt']
    assert delivered and instruction.startswith(job['native_role_instruction']+'\n')
    assert 'scientific_work_unit' not in schema['properties']
    assert all(path.read_bytes()==original for path,original in originals.items())


def test_cached_work_unit_and_custom_criteria_survive_future_default_change(tmp_path,monkeypatch):
    root=assignment(tmp_path,custom=True)
    frozen='Previously frozen bounded scientific search; preserve the original declared checks.'
    monkeypatch.setitem(guidance.WORK_UNITS,'improve',frozen)
    job=c.build_job(root,2,'W',[1],instruction='Original user-required check remains binding.')
    work=c.workspace(root,2)
    originals={path:path.read_bytes() for path in [root/'jobs/002.json',work/'science-task.json']}
    monkeypatch.setitem(guidance.WORK_UNITS,'improve','A future default must not replace the accepted literal.')
    rebuilt=c.build_job(root,2,'W',[1],instruction=job['instruction'])
    assert rebuilt['scientific_work_unit']==frozen
    assert rebuilt['native_role_instruction']=='Original custom W scientific criteria.'
    assert c.read(work/'science-task.json')['question'].count(frozen)==1
    assert all(path.read_bytes()==original for path,original in originals.items())


def test_retained_job_without_work_unit_is_not_backfilled(tmp_path,monkeypatch):
    root=assignment(tmp_path)
    units=guidance.WORK_UNITS
    monkeypatch.setattr(guidance,'WORK_UNITS',{})
    job=c.build_job(root,2,'W',[1])
    work=c.workspace(root,2)
    originals={path:path.read_bytes() for path in [root/'jobs/002.json',work/'science-task.json']}
    assert 'scientific_work_unit' not in job
    monkeypatch.setattr(guidance,'WORK_UNITS',units)
    rebuilt=c.build_job(root,2,'W',[1])
    assert 'scientific_work_unit' not in rebuilt
    assert 'Work unit:' not in c.read(work/'science-task.json')['question']
    assert all(path.read_bytes()==original for path,original in originals.items())
