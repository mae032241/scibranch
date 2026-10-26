"""Current H combinations, exact accepted originals and A scientific decisions."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import breadth, contracts as c, controller, execution, experiment_client, experiments, history, lineage, native, search
from cov_execution import lineage as cov_lineage
from execution_fixtures import execution_registry
from test_cov_lineage import BACKEND
from test_history_handoff import AUTO_COVERAGE, AUTO_PORTFOLIO, OMIT_PORTFOLIO, finish, fixture_final_coverage, fixture_portfolio_review


def prepare(tmp_path, *, cov=False, initial_calls=1, portfolio=False, qualified_local=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Compare scientific mechanisms; execution is not scientific qualification.')
    root=tmp_path/'instance'
    observer=dict(adapter=str(Path(cov_lineage.__file__).resolve()),contract_version=2,
        config=dict(runs='runs',calls='project/nodes',evaluation_key='fixture-public-v1')) if cov else None
    if qualified_local:
        from breadth_execution_fixtures import native_launcher
        assert not cov
    controller.prepare(root,objective,public,native_launcher() if qualified_local else ['never-call-a-model'],max_tokens=100000,max_seconds=600,max_steps=16,
        model='no-model',reasoning_effort='high',node_budget_mode='soft',
        search_policy=dict(search.DEFAULT_POLICY,initial_calls=initial_calls),history_observer=observer,
        **(dict(native_options=dict(workspace='workspace-write',network=False),
            execution_config=dict(executor='local_execution:command',
                configuration=dict(run_argv=[sys.executable],max_seconds=30))) if qualified_local else {}),
        portfolio_policy=dict(review_after_cycles=3) if portfolio else None)
    return root


def h(root, step=1, parents=(), labels=('P','Q','R'), existing=None):
    choice=dict(scope='Competing mechanisms for the original scientific question',
        commitments=[dict(label=label,minimal_test=f'Prediction for {label}; priority follows its discriminating observability',
            existing_ref=existing if index==0 else None) for index,label in enumerate(labels)],focus=dict(label=None,action='screen'))
    finish(root,step,'H',list(parents),action='experiment',search_choice=choice)


def worker(root, tmp_path, step, parents, *, cov=False, qualified_local=False):
    job=c.build_job(root,step,'W',parents);record=execution.record(root,step)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        search_slice=search.accept(root,job)))
    output=c.workspace(root,step)/'work/experiment-config.json' if qualified_local else tmp_path/f'config-{step}.json'
    assert not (cov and qualified_local)
    if cov:
        backend=tmp_path/'backend.py'
        if not backend.exists():backend.write_text(BACKEND)
        cov_lineage.configure(root,step,[sys.executable,str(backend),'--runs-root',str(root/'runs')],root/'runs',output,receipt_channel='backend_file')
    else:
        if qualified_local:
            selected=c.settings(root,job)['execution_config']
            configured=experiments.configure(root,step,selected['executor'],selected['configuration'],output)
        else:
            configured=experiments.configure(root,step,'local_execution:command',dict(run_argv=[sys.executable]),output)
    return job,configured if qualified_local else c.read(output)


def run(root, config, label, *, failed=False, client_path=None, qualified_local=False):
    if 'scope' not in config:
        if client_path is None:
            raise ValueError('Scientific execution client requires its exact configured path')
        owner,_=experiment_client.location(client_path)
        if owner!=Path(root).absolute():
            raise ValueError('Scientific execution client belongs to another instance')
        config=experiment_client.resolve(client_path,config)
    workspace=c.workspace(root,config['scope']['step'])
    code=workspace/'code';code.mkdir(exist_ok=True)
    index=len(experiments.Records(config).calls())
    source=code/f'{label}-{index}.payload'
    source.write_text(json.dumps(dict(mode='failed' if failed else 'success')) if 'runs_root' in config
        else 'raise SystemExit(3)\n' if failed else 'print("mechanical fixture only")\n')
    options=None
    if config['execution_interface']['executor']=='local_execution:command':
        runtime=workspace/'experiments'/'breadth-fixture';runtime.mkdir(parents=True,exist_ok=True)
        options=dict(experiment_directory=str(runtime))
    if qualified_local:
        from breadth_execution_fixtures import run as qualified_run
        return qualified_run(root,config,source,label,runtime)
    return experiments.dispatch(config,source,new=True,mechanism=label,command_options=options)


def findings(*observed,blocked=()):
    return [dict(mechanism=name,basis='observed',finding='The fixture observation is available; scientific adequacy remains for A.') for name in observed]+[
        dict(mechanism=name,basis='blocked',finding='This fixture deliberately leaves this direction untested and asks A to allocate its later work.') for name in blocked]


def a(root,step,parents,*,focus=None,dispositions=(),action='continue_w',mode='screen',portfolio_review=AUTO_PORTFOLIO,final_coverage=AUTO_COVERAGE):
    job=c.build_job(root,step,'A',parents);record=execution.record(root,step)
    payload=dict(summary='Assess exact scientific mechanisms',scientific_result='The original observations retain scientific limitations.',
        limitations=['Mechanical completion alone does not establish screening'],implementation_selection=None,
        candidate_recommendation=None,progress_assessment=None,portfolio_review=None,
        search_review=dict(dispositions=list(dispositions),next_focus=dict(mechanism_ref=focus,action=mode) if action=='continue_w' else None),
        next_action=action,route_reason='Resolve the mechanism comparison under the original constraints',
        decision_request=dict(decision='mechanism',question='Can the failed discriminating measurement be repaired?' if mode=='repair' else 'Which mechanism should receive the next independent comparison?',
            change_criterion='A trustworthy discriminating measurement would change the mechanism choice') if action in {'continue_w','revise_h'} else None)
    fixture_portfolio_review(root,job,payload,portfolio_review)
    fixture_final_coverage(root,job,payload,final_coverage)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,step)
    return job


def test_multi_mechanism_cov_originals_bind_candidates_and_a_screens_exactly(tmp_path,execution_registry):
    root=prepare(tmp_path,cov=True);h(root,labels=('P','Q'))
    job,config=worker(root,tmp_path,2,[1],cov=True)
    assert config['scope']['mechanism_ref'] is None
    p=run(root,config,'P',failed=True)
    assert p['status']=='known_failed'
    with pytest.raises(breadth.OpportunityRequired):run(root,config,'P')
    q=run(root,config,'Q')
    assert q['status']=='known_success'
    assert p['scope']==q['scope']==config['scope']
    assert p['mechanism_binding']['mechanism_ref']=='h001.m1'
    assert q['mechanism_binding']['mechanism_ref']=='h001.m2'
    finish(root,2,'W',[1],mechanism_findings=findings('P','Q'))
    state=search.project(root,3)
    assert all(row['status']=='pending' for row in state['mechanisms'].values())
    graph=lineage.build(root,3)
    assert graph['candidates'][p['candidate_id']]['mechanism_ref']=='h001.m1'
    assert graph['candidates'][q['candidate_id']]['mechanism_ref']=='h001.m2'
    a(root,3,[1,2],action='finish',dispositions=[dict(mechanism_ref=f'h001.m{i}',status='screened',
        reason='Scientific interpretation of the exact final W, including limitations') for i in (1,2)])
    final=search.project(root,4)
    assert {row['status'] for row in final['mechanisms'].values()}=={'screened'}
    assert final['order']['remaining']==[] and len(final['order']['seen_w'])==1
    before={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert lineage.build(root,4)['candidates'][q['candidate_id']]['mechanism_ref']=='h001.m2'
    assert before=={path:path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_large_h_combination_binds_complete_registered_w_scope(tmp_path, execution_registry):
    root = prepare(tmp_path)
    h(root, labels=tuple(f'Mechanism {index}' for index in range(1, 130)))
    job, config = worker(root, tmp_path, 2, [1])
    expected = [f'h001.m{index}' for index in range(1, 130)]
    assert [row['mechanism_ref'] for row in job['search']['breadth']['mechanisms']] == expected
    assert config['breadth'] == job['search']['breadth']
    assert [row['mechanism_ref'] for row in job['search']['coverage_obligation']] == expected
    assert len((json.dumps(c.science_view(job, root)['lineage'], ensure_ascii=False, indent=2) + '\n').encode()) <= 4096


def test_generic_cross_w_once_a_grant_and_same_ref_revision_do_not_refund(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root)
    _,first=worker(root,tmp_path,2,[1]);run(root,first,'P',failed=True)
    finish(root,2,'W',[1],mechanism_findings=findings('P',blocked=('Q','R')));a(root,3,[1,2],focus='h001.m1')
    _,second=worker(root,tmp_path,4,[1,2,3])
    assert second['breadth']['baseline'][0]['accepted_count']==1
    granted=run(root,second,'P')
    assert granted['mechanism_binding']['grant']['source_a']==3
    with pytest.raises(breadth.OpportunityRequired):run(root,second,'P')
    finish(root,4,'W',[1,2,3],mechanism_findings=findings('P',blocked=('Q','R')));a(root,5,[1,4])
    _,third=worker(root,tmp_path,6,[1,4,5])
    assert third['breadth']['grant']['used_calls']==1
    with pytest.raises(breadth.OpportunityRequired):run(root,third,'P')
    run(root,third,'Q');finish(root,6,'W',[1,4,5],mechanism_findings=findings('P','Q',blocked=('R',)))
    h(root,7,[1,6],labels=('Renamed P',),existing='h001.m1')
    _,fourth=worker(root,tmp_path,8,[7])
    count={row['mechanism_ref']:row['accepted_count'] for row in fourth['breadth']['baseline']}
    assert count=={'h001.m1':2,'h001.m2':1,'h001.m3':0}
    assert fourth['breadth']['grant'] is None
    with pytest.raises(breadth.OpportunityRequired):run(root,fourth,'Renamed P')
    run(root,fourth,'R')
    assert len(experiments.Records(fourth).calls())==1


def test_a_cannot_screen_unexecuted_branch(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root,labels=('P','Q'))
    _,config=worker(root,tmp_path,2,[1]);run(root,config,'P')
    finish(root,2,'W',[1],mechanism_findings=findings('P',blocked=('Q',)));job=c.build_job(root,3,'A',[1,2])
    result=search.interpret(root,job,dict(next_action='continue_w',route_reason='Assess the exact W',search_review=dict(
        dispositions=[dict(mechanism_ref='h001.m2',status='screened',reason='Claimed scientific screening')],next_focus=None)))
    assert dict(code='screen_source_unavailable',subject='h001.m2') in result['diagnostics']
    assert result['state']['mechanisms']['h001.m2']['status']=='pending'


def test_configurable_initial_opportunities_and_bounded_combination(tmp_path):
    assert search.policy(dict(search.DEFAULT_POLICY,initial_calls=2))['initial_calls']==2
    for invalid in (0,False,1.5):
        with pytest.raises(ValueError):search.policy(dict(search.DEFAULT_POLICY,initial_calls=invalid))
    root=prepare(tmp_path);h(root,labels=('P','Q','R','S'))
    h(root,2,[1],labels=('T','U'))
    job=c.build_job(root,3,'W',[2])
    combo=job['search']['breadth']['mechanisms']
    assert len(combo)==6
    assert [row['label'] for row in combo]==['P','Q','R','S','T','U']
    assert job['search']['breadth']['initial_calls']==1


def test_multi_mechanism_q_repair_uses_exact_w_and_preserves_single_science_cycle(tmp_path,execution_registry):
    root=prepare(tmp_path,portfolio=True);h(root,labels=('P','Q'))
    job,config=worker(root,tmp_path,2,[1])
    assert job['search']['focus']['mechanism_ref']=='h001.m1'
    run(root,config,'Q',failed=True);finish(root,2,'W',[1],mechanism_findings=findings('Q',blocked=('P',)))
    before=search.project(root,3)
    assessed=search.portfolio_view(before,3,prospective=True)
    assert assessed['last_assessed_branch']=='h001.m2'
    assert assessed['last_assessed_branches']==['h001.m2']
    a(root,3,[1,2],focus='h001.m2',mode='repair')
    state=search.project(root,4)
    assert not state['order']['seen_w'] and not state['portfolio']['seen_w']
    assert [row['mechanism_ref'] for row in state['order']['remaining']]==['h001.m1','h001.m2']
    repaired,next_config=worker(root,tmp_path,4,[1,2,3])
    assert repaired['search']['repair_binding']=='available' and repaired['search']['repair_root']==2
    assert search.launch_reason(root,repaired) is None
    run(root,next_config,'Q');finish(root,4,'W',[1,2,3])
    a(root,5,[1,4],dispositions=[dict(mechanism_ref='h001.m2',status='screened',reason='The repaired exact mechanism was scientifically assessed')])
    final=search.project(root,6)
    assert len(final['portfolio']['seen_w'])==1 and len(final['order']['seen_w'])==1
    assert final['portfolio']['invested']=={'h001.m2':1}
    assert final['portfolio']['deferred']=={'h001.m1':1}
    assert final['portfolio']['last_branch']=='h001.m2'
