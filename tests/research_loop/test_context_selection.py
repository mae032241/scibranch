"""Role-selected originals and protected caveats, without model execution."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import checkpoint,contracts as c,controller,selection
from test_history_handoff import finish
from test_worker_fragments import fragment,science


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
    plan=dict(scope='Competing target explanations',commitments=[dict(label=f'M{i}',minimal_test='A distinguishable scientific prediction',existing_ref=None) for i in range(1,5)],focus=dict(label=None,action='screen'))
    caveats=['Original target condition is still unknown','A counterexample remains unresolved', '"\\\n'*1000, 'Later caveat must survive']
    finish(root,1,'H',[],action='experiment',search_choice=plan,text='BODY-CROWD '*2000,limitations=caveats)
    finish(root,2,'W',[1],text='One negative observation does not resolve the other mechanisms.',limitations=['Negative result is bounded','Feasibility elsewhere is unknown'],
        mechanism_findings=[dict(mechanism=f'M{i}',basis='blocked',finding='This view-only fixture has no domain execution originals; applicability of this mechanism remains unresolved.') for i in range(1,5)])
    finish(root,3,'A',[1,2],action='wait',recommendation='P',limitations=['A applicability judgment is not an observation'])
    c.build_job(root,4,'E',[3],instruction='Retrieve the requested counterevidence.',evidence_return=dict(step=3,role='A'))
    finish(root,4,'E',[3],limitations=['Requested original is unavailable'])
    return root


def indexed_rows(root, job, name):
    """Read the complete frozen index when a compact preview is omitted."""
    page=job['history']['view']['indexes'][name]
    rows=[]
    while page:
        path=c.workspace(root,job['step'])/page
        original=next(row for row in job['history']['files'] if row['path']==page)
        assert c.digest(path)==original['sha256']
        value=c.read(path)
        rows.extend(value[name]);page=value['next_page']
    return rows


@pytest.mark.parametrize('role,first',[('H','A'),('W','H'),('A','W'),('E','A')])
def test_role_material_priority_preserves_constraints_caveats_and_opportunities(tmp_path,role,first):
    root=context_scene(tmp_path)
    job=c.build_job(root,5,role,[1,2,3,4],instruction='Exact question about the target constraint.',
        evidence_return=dict(step=3,role='A') if role=='E' else None)
    original=copy.deepcopy(job)
    view=c.science_view(job,root)
    assert job==original and view==c.read(c.workspace(root,5)/'science-task.json')
    question='Exact question about the target constraint.'
    assert job['instruction']==question
    if role=='W':
        work_unit=('Screen the assigned H combination with cheap discriminating observations and your own findings or concrete obstacles. '
            'Preserve declared checks. A completed call alone is not scientific screening; '
            'report the combination when its assigned coverage is ready for A.')
        assert job['scientific_work_unit']==work_unit
        assert view['question']==question+'\nWork unit: '+work_unit
    else:
        assert 'scientific_work_unit' not in job
        assert view['question']==question
    assert view['parents'][0]['role']==first
    assert (c.workspace(root,5)/view['global']['objective']).read_text().startswith('Keep the original target constraint')
    h=next(row for row in view['parents'] if row['role']=='H')
    assert h['limitations']==['Original target condition is still unknown','A counterexample remains unresolved','Later caveat must survive']
    assert h['limitations_omitted_count']==1
    assert (c.workspace(root,5)/h['conclusion']).is_file()
    mechanisms=view['lineage']['mechanisms']
    originals=indexed_rows(root,job,'mechanisms')
    assert [row['mechanism_ref'] for row in originals]==[f'h001.m{index}' for index in range(1,5)]
    assert all(row['status']=='pending' and (c.workspace(root,5)/row['original']).is_file() for row in originals)
    if view['lineage'].get('view_omitted'):
        assert view['lineage']['omission_reason']
        assert mechanisms['count']==mechanisms['pending_count']==4
    else:
        mechanism=mechanisms['unfinished_plan']
        assert mechanism['remaining_count']==4
        assert (c.workspace(root,5)/mechanism['index']).is_file()
    candidates=view['lineage']['candidates']
    if view['lineage'].get('view_omitted'):
        assert candidates['coverage'] is None and candidates['a_recommended'] is None
        assert candidates['candidate_facts']==[]
        # The original A choice and negative observations remain readable.
        assert c.validate(c.workspace(root,3))['candidate_recommendation']=='P'
    else:
        assert candidates['failed_candidate_count']==candidates['unknown_candidate_count']==1
        assert {row['candidate_id'] for row in candidates['candidate_alerts']}=={'negative','unresolved'}
        assert candidates['coverage']=='partial'
        assert candidates['a_recommended']['candidate_id']=='P'
    assert 'target_best' not in candidates
    ids=['P','negative','unresolved']
    assert [row['id'] for row in candidates['candidate_facts']]==ids[:len(candidates['candidate_facts'])]
    assert len(candidates['candidate_facts'])+candidates['candidate_facts_omitted_count']==len(ids)
    originals=indexed_rows(root,job,'candidates')
    assert [row['id'] for row in originals]==ids
    assert {row['id']:row['status'] for row in originals}==dict(P='ok',negative='failed',unresolved='unknown')
    assert 'experiments' not in view
    assert len((json.dumps(view,ensure_ascii=False,indent=2)+'\n').encode())<12000
    assert 'BODY-CROWD '*2000 not in json.dumps(view)


def test_fragment_brief_preserves_whole_late_limitations_before_prose(fragment):
    root,job,record=fragment
    limitations=['Original target restriction','Observed counterexample remains', '"\\\n'*1500,'Unfinished question remains unknown']
    science(record,scientific_result='Verbose understanding '*1500,limitations=limitations,stage_intent='continue')
    checkpoint.capture(root,job,record)
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    view=checkpoint.brief(root,job,record)
    assert view['limitations']==[limitations[0],limitations[1],limitations[3]]
    assert view['omitted_limitations']==1 and view['truncated']
    assert view['stage_intent']=='continue'
    assert 'not mounted' in view['original_access']
    assert len(checkpoint._bytes(view))<=4096
    assert str(root) not in json.dumps(view) and 'sha256' not in json.dumps(view)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


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


def test_w_combination_view_uses_frozen_authorized_rows_not_single_focus(tmp_path):
    root=context_scene(tmp_path)
    job=c.build_job(root,5,'W',[1,2,3,4],instruction='Work within the authorized scientific questions.')
    # Pure projection contract input: acceptance and grants remain another owner.
    plan=c.read(c.workspace(root,1)/'conclusion.json')['search_plan']
    mechanisms=[dict(mechanism_ref=f'h001.m{index}',label=item['label'],source_h=1,
        source_h_sha256=c.digest(c.host_record(c.workspace(root,1))/'result-identity.json'),
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
        assert (c.workspace(root,5)/row['original']).is_file()
    public=json.dumps(view)
    assert 'source_h_sha256' not in public and 'baseline' not in public and 'accepted_count' not in public
    assert len((json.dumps(view['lineage'],ensure_ascii=False,indent=2)+'\n').encode())<=4096
    assert job==before
