"""Role-selected originals and protected caveats, without model execution."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c,controller,selection
from test_history_handoff import finish


def context_scene(tmp_path):
    public=tmp_path/'public';public.mkdir();goal=public/'objective.md'
    goal.write_text('Keep the original target constraint and counterexamples.')
    observer=tmp_path/'public_observer.py'
    observer.write_text('''import json,sys
r=json.load(sys.stdin)
rows=[dict(id=cid,label=cid,implementation=None,evaluation_key='public-v1',metric=metric,direction='max',status=status,evidence=['project/global/objective.md']) for cid,metric,status in [('P',.8,'ok'),('negative',None,'failed'),('unresolved',None,'unknown')]] if r['step']==1 else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='partial',note='Synthetic observations leave applicability unknown'))))
''')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,['never-launched'],max_tokens=100000,max_seconds=300,max_steps=12,
        model='no-model',reasoning_effort='high',history_observer=dict(adapter=str(observer),config={}))
    plan=dict(scope='Competing target explanations',commitments=[dict(label=f'M{i}',minimal_test='A distinguishable scientific prediction',existing_ref=None) for i in range(1,5)],focus=dict(index=None,action='screen'))
    caveats=['Original target condition is still unknown','A counterexample remains unresolved', '"\\\n'*1000, 'Later caveat must survive']
    finish(root,1,'H',[],action='experiment',search_choice=plan,text='BODY-CROWD '*2000,limitations=caveats)
    finish(root,2,'W',[1],text='One negative observation does not resolve the other mechanisms.',limitations=['Negative result is bounded','Feasibility elsewhere is unknown'],
        mechanism_findings=[dict(mechanism=f'h001.m{i}',basis='blocked',finding='This view-only fixture has no domain execution originals; applicability of this mechanism remains unresolved.') for i in range(1,5)])
    finish(root,3,'A',[1,2],action='wait',recommendation='P',limitations=['A applicability judgment is not an observation'])
    c.build_job(root,4,'E',[3],instruction='Retrieve the requested counterevidence.',evidence_return=dict(step=3,role='A'))
    finish(root,4,'E',[3],limitations=['Requested original is unavailable'])
    return root


def bound_input_path(root, job, path):
    """Resolve a logical task input through its frozen producer binding."""
    from scidiscovery.research_loop import experiment_client,originals
    row=next(item for item in experiment_client._input_rows(job) if item['path']==path)
    bound=dict(row,source=row.get('source',str(c.workspace(root,job['step'])/originals.relative(path))))
    return originals.material_path(root,bound)


def indexed_rows(root, job, name, *, key=None):
    """Read the complete frozen index when a compact preview is omitted."""
    page=job['history']['view']['indexes'][name]
    rows=[]
    while page:
        path=bound_input_path(root,job,page)
        original=next(row for row in job['history']['files'] if row['path']==page)
        assert path.stat().st_size==original['size_bytes']
        value=c.read(path)
        rows.extend(value[key or name]);page=value['next_page']
    return rows


@pytest.mark.process_e2e
@pytest.mark.parametrize('role,first',[('H','A'),('W','H'),('A','W'),('E','A'),('E','H')])
def test_role_material_priority_preserves_constraints_caveats_and_opportunities(tmp_path,role,first):
    from scidiscovery.research_loop import execution
    root=context_scene(tmp_path)
    job=c.build_job(root,5,role,[1,2,3,4],instruction='Exact question about the target constraint.',
        evidence_return=dict(step=1 if first=='H' else 3,role=first) if role=='E' else None)
    original=copy.deepcopy(job)
    view=c.science_view(job,root)
    assert job==original and view==c.read(c.workspace(root,5)/'science-task.json')
    question='Exact question about the target constraint.'
    assert job['instruction']==question
    if role=='W':
        from scidiscovery.research_loop import guidance
        work_unit=guidance.WORK_UNITS['screen']
        assert job['scientific_work_unit']==work_unit
        assert view['question']==question+'\nWork unit: '+work_unit
    else:
        assert 'scientific_work_unit' not in job
        assert view['question']==question
    assert view['parents'][0]['role']==first
    assert bound_input_path(root,job,view['global']['objective']).read_text().startswith('Keep the original target constraint')
    h=next(row for row in view['parents'] if row['role']=='H')
    assert h['limitations']==['Original target condition is still unknown','A counterexample remains unresolved','Later caveat must survive']
    assert h['limitations_omitted_count']==1
    assert bound_input_path(root,job,h['conclusion']).is_file()
    for parent in view['parents']:
        step=int(Path(parent['conclusion']).parts[2])
        assert parent['history_alias']==f'inputs/history/nodes/{step:03d}/0001.json'
        assert bound_input_path(root,job,parent['history_alias']).is_file()
    # The compact task chooses one primary route; all historical text pages
    # remain bound and preserve even scientific content beyond the previews.
    primary={row['history_alias']:row['conclusion'] for row in view['parents']}
    for row in view['lineage']['recent']:
        assert row['original']==f'inputs/parents/{row["step"]:03d}/conclusion.json'
    historical=indexed_rows(root,job,'nodes',key='records')
    h_record=next(row for row in historical if row['step']==1)
    assert len(h_record['conclusion_pages'])>1
    chunks=[c.read(bound_input_path(root,job,path)) for path in h_record['conclusion_pages']]
    assert all(row['source']=='node:001/scientific-output' for row in chunks)
    complete=json.loads(''.join(row['text'] for row in chunks))
    direct=c.read_scientific_output(bound_input_path(root,job,h['conclusion']))
    assert complete['scientific_result']==direct['scientific_result']=='BODY-CROWD '*2000
    assert complete['limitations']==direct['limitations']
    assert complete['limitations'][-1]=='Later caveat must survive'
    assert complete['search_plan']==direct['search_plan']
    assert h_record['conclusion_pages'][0] in primary
    assert job==original
    mechanisms=view['lineage']['mechanisms']
    originals=indexed_rows(root,job,'mechanisms')
    assert [row['mechanism_ref'] for row in originals]==[f'h001.m{index}' for index in range(1,5)]
    assert all(row['status']=='pending' and bound_input_path(root,job,row['original']).is_file() for row in originals)
    if view['lineage'].get('view_omitted'):
        assert view['lineage']['omission_reason']
        assert mechanisms['count']==mechanisms['pending_count']==4
    else:
        mechanism=mechanisms['unfinished_plan']
        assert mechanism['remaining_count']==4
        assert bound_input_path(root,job,mechanism['index']).is_file()
    candidates=view['lineage']['candidates']
    # Unbound observer rows and authored advice cannot create registered candidates.
    assert c.read_scientific_output(execution.raw(root,3))['candidate_recommendation']=='P'
    assert candidates['count' if view['lineage'].get('view_omitted') else 'candidate_count']==0
    assert candidates['candidate_facts']==[]
    assert candidates['candidate_facts_omitted_count']==0
    assert indexed_rows(root,job,'candidates')==[]
    assert 'target_best' not in candidates
    assert 'experiments' not in view
    assert len((json.dumps(view,ensure_ascii=False,indent=2)+'\n').encode())<12000
    assert 'BODY-CROWD '*2000 not in json.dumps(view)


def test_primary_reading_routes_only_replace_verified_same_source_aliases():
    parent=dict(conclusion='inputs/parents/002/conclusion.json',
        history_alias='inputs/history/nodes/002/0001.json')
    history_parent=dict(step=2,original=parent['history_alias'])
    nonparent=dict(step=1,original='inputs/history/nodes/001/0001.json')
    other_path=dict(step=2,original='inputs/other/nodes/002/0001.json')
    view=dict(recent=[history_parent,nonparent,other_path],
        mechanisms=dict(overview=[history_parent,nonparent],
            unfinished_plan=dict(remaining=[nonparent,history_parent])),
        indexes=dict(nodes='inputs/history/pages/0001.json',mechanisms='inputs/lineage/mechanisms/0001.json'),
        candidates=dict(candidate_facts=[dict(id='late-negative',status='failed')]))
    job=dict(role='A',history=dict(view=view))
    before=copy.deepcopy(job)
    projected=c._role_lineage(job,[parent])
    assert projected['recent']==[dict(history_parent,original=parent['conclusion']),nonparent,other_path]
    assert projected['mechanisms']['overview']==[dict(history_parent,original=parent['conclusion']),nonparent]
    assert projected['mechanisms']['unfinished_plan']['remaining']==[nonparent,dict(history_parent,original=parent['conclusion'])]
    assert projected['indexes']==view['indexes'] and projected['candidates']==view['candidates']
    assert job==before


@pytest.mark.process_e2e
def test_parent_history_alias_requires_exact_source_seal(tmp_path):
    root=context_scene(tmp_path)
    job=c.build_job(root,5,'H',[3],instruction='Retain independent counterevidence.')
    ref=job['direct_refs'][0]
    assert c._parent_view(job,ref,root)['history_alias']=='inputs/history/nodes/003/0001.json'
    # A matching step or body basename cannot substitute for the frozen owner.
    changed=copy.deepcopy(job)
    row=next(row for row in changed['history']['nodes'] if row['step']==3)
    row['seal_ref']=dict(path='/foreign/native-evidence/003/result-identity.json')
    assert 'history_alias' not in c._parent_view(changed,ref,root)
    changed=copy.deepcopy(job)
    changed['history']['files']=[row for row in changed['history']['files']
        if row['path']!='inputs/history/nodes/003/0001.json']
    assert 'history_alias' not in c._parent_view(changed,ref,root)


def test_selection_preserves_late_failed_unknown_and_relevant_audit():
    candidates=[dict(id=f'candidate-{i}',label=f'candidate-{i}',evaluation_key='public-v1',metric=i,
        direction='max',status='ok') for i in range(20)]
    candidates.extend([dict(candidates[0],id='negative',label='negative',metric=None,status='failed'),
        dict(candidates[0],id='unknown',label='unknown',metric=None,status='unknown')])
    records=[dict(step=1,role='W',recommendation=None,coverage='partial',candidates=candidates,current_candidate=None,actual_submission=None),
        dict(step=2,role='A',recommendation='candidate-19',coverage=None,candidates=[],current_candidate=None,actual_submission=None)]
    before=copy.deepcopy(records)
    result=selection.project(records,audits=[dict(candidate_id='candidate-19',status='failed',artifact_bound=True,leakage_flag=False)])
    assert records==before
    assert result['public_audits'][0]==dict(candidate_id='candidate-19',status='public_audit_failed')
    assert {row['candidate_id']:row['status'] for row in result['candidate_alerts']}=={'negative':'failed','unknown':'unknown'}
    assert result['failed_candidate_count']==result['unknown_candidate_count']==1
    assert result['candidate_alerts_omitted_count']==0
    assert 'target_best' not in result and result['delivery']['state']=='unknown'
    assert result['a_recommended']['candidate_id']=='candidate-19'
    assert len(selection._bytes(result))<=4096


@pytest.mark.process_e2e
def test_w_combination_view_uses_frozen_authorized_rows_not_single_focus(tmp_path):
    from scidiscovery.research_loop import execution
    root=context_scene(tmp_path)
    job=c.build_job(root,5,'W',[1,2,3,4],instruction='Work within the authorized scientific questions.')
    # Pure projection contract input: acceptance and grants remain another owner.
    plan=c.read_scientific_output(execution.raw(root,1))['search_plan']
    mechanisms=[dict(mechanism_ref=f'h001.m{index}',label=item['label'],source_h=1,
        source_h_ref=c.record_ref(c.host_record(c.workspace(root,1))/'result-identity.json'),
        minimal_test=item['minimal_test'],status='pending') for index,item in enumerate(plan['commitments'],1)]
    job['search']['breadth']=dict(version=1,initial_calls=1,mechanisms=mechanisms,
        baseline=[dict(mechanism_ref=row['mechanism_ref'],accepted_count=1) for row in mechanisms],grant=None,prior_unknown=False)
    before=copy.deepcopy(job)
    view=c.science_view(job,root)
    scope=view['lineage']['mechanisms']
    assert scope['focus'] is None
    if view['lineage'].get('view_omitted'):
        assert view['lineage']['omission_reason']
        assert scope['count']==scope['pending_count']==len(mechanisms)==4
        rows=indexed_rows(root,job,'mechanisms')
    else:
        assert scope['authorized_count']==4 and scope['overview_omitted_count']==0
        assert scope['unfinished_plan']['remaining_count']==4
        rows=scope['overview']
    assert [row['mechanism_ref'] for row in rows]==[row['mechanism_ref'] for row in mechanisms]
    assert [row['label'] for row in rows]==[row['label'] for row in mechanisms]
    for row in rows:
        assert bound_input_path(root,job,row['original']).is_file()
    public=json.dumps(view)
    assert 'source_h_ref' not in public and 'baseline' not in public and 'accepted_count' not in public
    assert len((json.dumps(view['lineage'],ensure_ascii=False,indent=2)+'\n').encode())<=4096
    assert job==before
