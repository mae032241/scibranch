"""Current sealed H/search + registered cov execution + A, without models."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, history, lineage, search
from cov_execution import lineage as cov_lineage
from execution_fixtures import execution_registry
from test_cov_lineage import BACKEND
from test_history_handoff import finish


@pytest.mark.process_e2e
def test_sealed_current_h_configures_registered_cov_and_final_a(tmp_path,execution_registry):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Bounded current scientific composition')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=100000,max_seconds=120,max_steps=8,
        model='no-model',reasoning_effort='high',search_policy=search.DEFAULT_POLICY,node_budget_mode='soft',
        history_observer=dict(adapter=str(Path(cov_lineage.__file__).resolve()),contract_version=2,
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    plan=dict(scope='One original mechanism',commitments=[dict(label='P',minimal_test='One bounded experiment',existing_ref=None)],focus=dict(index=None,action='screen'))
    finish(root,1,'H',[],action='experiment',search_choice=plan)
    assert 'exploration_order_version' not in c.settings(root)
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    c.write(record/'native-intent.json',dict(step=2,task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    original_intent=(record/'native-intent.json').read_bytes()
    assert job['search']['focus']['mechanism_ref']=='h001.m1'
    graph=lineage.build(root,2)
    assert graph['mechanisms']==search.project(root,2)['mechanisms']
    backend=tmp_path/'backend.py';backend.write_text(BACKEND)
    config=tmp_path/'experiment-config.json'
    cov_lineage.configure(root,2,[sys.executable,str(backend),'--runs-root',str(root/'runs')],root/'runs',config,receipt_channel='backend_file')
    frozen=c.read(config)
    assert frozen['scope']['mechanism_ref'] is None
    assert frozen['breadth']['mechanisms'][0]['mechanism_ref']=='h001.m1'
    script=c.workspace(root,2)/'work/payload.json';script.write_text('{}')
    env=dict(os.environ,PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''))
    command=[sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(config)]
    reply=subprocess.run([*command,'run','--script',str(script),'--new-plan','--mechanism','h001.m1'],env=env,capture_output=True,text=True,timeout=20)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    result=json.loads(reply.stdout)
    assert result['status']=='known_success'
    finish(root,2,'W',[1],mechanism_findings=[dict(mechanism='h001.m1',basis='observed',finding='The bounded fixture observation is available; target adequacy remains untested.')],
        candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref='h001.m1',
            reason='The exact public call is retained; this transport fixture makes no scientific suitability nomination.')]),
        preserve_native_intent=True)
    assert (record/'native-intent.json').read_bytes()==original_intent
    finish(root,3,'A',[1,2],action='finish',search_choice=dict(dispositions=[dict(
        mechanism_ref='h001.m1',status='screened',reason='The final W original was assessed')],next_focus=None))
    state=search.project(root,4);graph=lineage.build(root,4)
    assert graph['mechanisms']==state['mechanisms']
    assert state['mechanisms']['h001.m1']['status']=='screened'
    assert state['order']['remaining']==[] and len(state['order']['seen_w'])==1
    context=graph['candidates'][result['candidate_id']]['context']
    assert context['call_id']==result['call_id'] and context['scope']['mechanism_ref'] is None
    assert context['mechanism_binding']['mechanism_ref']=='h001.m1'
    assert context['status']=='known_success'
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert lineage.build(root,4)==graph
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage',['missing_protocol','different_owner'])
def test_context_requires_current_stable_stage_scope(tmp_path,execution_registry,damage):
    from test_lineage import scene
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    cov_lineage.run(config,script,new=True)
    request=dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config'],known_candidates=[],known_contexts=[])
    contexts=cov_lineage.observe(request)['contexts']
    wrong=json.loads(json.dumps(contexts))
    if damage=='missing_protocol':wrong[0]['scope'].pop('handoff_protocol')
    else:wrong[0]['scope']['execution_key']=c.record_ref(root/'jobs/001.json')
    def forbidden_archive(*args,**kwargs):raise AssertionError('Scope must be checked before asset archive')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(history.ObserverIntegrityError,match='Execution context differs'):
        lineage.contexts(root,c.read(root/'jobs/002.json'),wrong,forbidden_archive)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
