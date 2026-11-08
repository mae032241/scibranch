"""Frozen native role advice and the supported Cov CLI fixture surface."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, guidance, native
from cov_execution import lineage as cov
from test_history_handoff import finish
from test_cov_lineage import wrapper
from execution_fixtures import execution_registry
from scidiscovery.research_loop.protocol import CURRENT


def prepare(tmp_path, advice=guidance.DEFAULT, *, research_strategy_version=None):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Adapt experiments to observed results within the scientific objective.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=100000,max_seconds=3600,max_steps=8,
        model='fake',reasoning_effort='high',search_policy=None,role_guidance=advice,
        research_strategy_version=research_strategy_version)
    return root


def test_only_current_role_literal_is_delivered_and_retained_bytes_are_stable(tmp_path,monkeypatch):
    root=prepare(tmp_path);stored=c.settings(root)['role_guidance']
    assert sum(len(text.encode()) for text in stored.values())<=2560
    assert 'exp run' not in stored['W'] and 'lineage-status' not in stored['W']
    for step,role in enumerate(['H','W','A','E'],1):
        job,work,record=finish(root,step,role,[step-1] if step>1 else [])
        view=c.science_view(job,root)
        assert job['native_role_instruction']==stored[role] and isinstance(job['native_role_instruction'],str)
        assert 'role_guidance' not in view  # Literal instructions travel through the native prompt.
        assert 'role_guidance' not in c.result_schema(role,CURRENT)['properties']
        prompt, _, schema = native.render_scientific_request(job, view, {}, backend_name='codex',
            instruction_transport='prompt')
        if role in {'H', 'W'}:
            assert prompt.count(guidance.STAGE_PLANNING[role]) == 1
        if role == 'H':
            assert 'H explores revisable high-level scientific methods/directions' in prompt
            assert 'Keep original task constraints/counts/commitments fixed' in prompt
            assert 'minimal_test' not in prompt
            assert 'task acceptance' not in prompt
            assert 'No downstream budget/call/audit caps' in prompt
        assert 'scientific_work_unit' not in schema['properties']
        originals={p:p.read_bytes() for p in (root/'jobs'/f'{step:03d}.json',work/'science-task.json',record/'result-identity.json')}
        monkeypatch.setitem(guidance.DEFAULT,role,'Changed future guidance; not this task')
        c.build_job(root,step,role,[step-1] if step>1 else [])
        assert c.validate(work)['summary']=='A contribution'
        assert all(p.read_bytes()==value for p,value in originals.items())


def test_old_job_without_guidance_is_not_rewritten(tmp_path):
    root=prepare(tmp_path,None);job,work,record=finish(root,1,'H',[])
    original={p:p.read_bytes() for p in (root/'settings.json',root/'jobs/001.json',work/'science-task.json',record/'result-identity.json')}
    assert 'role_guidance' not in job and 'role_guidance' not in c.science_view(job,root)
    c.build_job(root,1,'H',[]);c.validate(work)
    assert all(p.read_bytes()==data for p,data in original.items())


@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
@pytest.mark.parametrize('gate,role', [('author', 'H'), ('author', 'W'), ('author', 'A'), ('formal_review', 'A')])
def test_author_and_formal_review_requests_exclude_ordinary_batch_planning(tmp_path, backend_name, gate, role):
    root = prepare(tmp_path)
    job = c.build_job(root, 1, role, [])
    task = c.read(c.workspace(root, 1)/'science-task.json')
    intent, options = {}, {}
    if gate == 'author':
        intent['continuation_kind'] = 'author_closeout'
        options['author'] = dict(facts='inputs/author-facts.json',
            limits=dict(max_tokens=1000, max_seconds=60))
    else:
        job = dict(job, formal_review=dict(fixture='request rendering only'))
        task = dict(task, review_instruction='Independently assess the exact retained subject.')
    prompt, instruction, _ = native.render_scientific_request(job, task, intent,
        backend_name=backend_name, instruction_transport='prompt', **options)
    assert all(text not in prompt + instruction for text in guidance.STAGE_PLANNING.values())
    if gate == 'author':
        assert 'Completed originals only' in prompt
        assert 'stage_intent=decision' in prompt
        assert 'no consultation, handoff or continued work' in prompt
        assert 'no experiment/training/refinement/submission/replay/backend query/collection' in prompt
    else:
        assert 'Do not edit the subject' in prompt


@pytest.mark.parametrize('bad',[{},dict(guidance.DEFAULT,X='unexpected'),{**guidance.DEFAULT,'H':''},{**guidance.DEFAULT,'H':'机'*1000}])
def test_invalid_or_oversized_guidance_does_not_prepare(tmp_path,bad):
    with pytest.raises(ValueError):prepare(tmp_path,bad)
    assert not (tmp_path/'instance').exists()


def cli(wrapper,*args,default=None,expected=0):
    env=dict(os.environ,PYTHONPATH=str(Path(cov.__file__).parents[2]),EXP_WALL_CAP_S='1')
    env.pop('EXP_DEFAULT_SCRIPT',None)
    if default is not None:env['EXP_DEFAULT_SCRIPT']=str(default)
    reply=subprocess.run([sys.executable,'-m','cov_execution.lineage','--config',str(wrapper.config),*map(str,args)],
        env=env,cwd=wrapper.root,capture_output=True,text=True,timeout=20)
    assert reply.returncode==expected,(reply.stdout,reply.stderr)
    return json.loads(reply.stdout)


@pytest.mark.process_e2e
def test_implicit_default_and_explicit_override_use_same_call_receipts(wrapper):
    default=wrapper.script('default-source');explicit=wrapper.script('explicit-source')
    first=cli(wrapper,'run','--new-plan',default=default)
    second=cli(wrapper,'run','--script',explicit,'--parent-plan',first['candidate_id'],default=default)
    for result,source in [(first,default),(second,explicit)]:
        row=json.loads((wrapper.directory(result)/'row.json').read_text())
        assert row['script']==str(source)
        assert result['val_score']==row['val_score']
        if isinstance(row.get('metric'),str):assert result['metric']==row['metric']
        else:assert 'metric' not in result
        assert result['evaluation_key']=='fixture-public-v1'
        assert not any(key in json.dumps(result) for key in ('sha256','scope','proofs',str(wrapper.root)))
    before=wrapper.calls()
    query=cli(wrapper,'lineage-status')['calls']
    assert query[0]['metric']==first['val_score'] and wrapper.calls()==before
    assert query[0]['candidate_id']==first['candidate_id']
    assert query[0]['evaluation_key']==first['evaluation_key'] and query[0]['status']=='known_success'


@pytest.mark.process_e2e
@pytest.mark.parametrize('default',[None,'relative-script.py'])
def test_missing_or_relative_default_cannot_accept_an_experiment(wrapper,default):
    result=cli(wrapper,'run','--new-plan',default=default,expected=2)
    assert result['status']=='unresolved' and not wrapper.accepted() and not wrapper.calls()


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['exact','mismatch','missing'])
def test_observer_retains_public_actual_without_granting_audit_or_confirmation(tmp_path,mode):
    from test_lineage import scene
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    result=cov.run(config,script,new=True)
    raw_row=json.loads((Path(config['record_dir'])/'calls'/result['call_id']/'row.json').read_text())
    feedback=cov.public_feedback(config,result)
    assert feedback['metric']==raw_row['metric'] and feedback['val_score']==raw_row['val_score']
    submitted=root/'runs/submitted_model.pkl'
    if mode!='missing':
        shutil.copyfile(result['model']['path'],submitted)
        if mode=='mismatch':submitted.write_bytes(b'Not the frozen candidate model')
    (root/'runs/submitted.json').write_text(json.dumps(dict(run_id=result['run_id'],model_path=str(submitted),audit_score=None,leakage_flag=False)))
    request=dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config'],known_candidates=[],known_contexts=[])
    observed=cov.observe(request)
    assert observed['current_candidate'] is None
    actual=observed['actual_submission']
    if mode=='exact':assert actual['candidate_id']==result['candidate_id']
    elif mode=='mismatch':assert actual['candidate_id'] is None
    else:assert actual is None
    if mode!='exact':assert observed['coverage']['status']=='partial'
    assert 'qualified' not in json.dumps(actual) and 'confirmed' not in json.dumps(actual)
    finish(root,2,'W',[1])
    seal=c.read(execution.origin(root,2)/'result-identity.json')
    archived=c.read(root/seal['observations']['path'])['actual_submission']
    if mode!='missing':assert archived['artifact']['retention']=='archived'
