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
    finish(root,3,'A',[1,2],action='continue_w',recommendation='P',limitations=['A applicability judgment is not an observation'])
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


def test_parent_view_projects_explicit_accepted_source_without_file_reads(monkeypatch):
    ref=dict(path='inputs/parents/002/conclusion.json',size_bytes=20)
    job=dict(direct_refs=[ref],direct_entry_points=[])
    source=dict(public=dict(limitations=['The observation remains bounded.']),
        facts=dict(job=dict(role='W')),
        member=dict(navigation=dict(path='nodes/exact-goal/002/node.json')))
    def forbidden(*args,**kwargs):
        pytest.fail('A parent projection must use its producer-supplied original facts')
    monkeypatch.setattr(c,'read',forbidden)
    view=c._parent_view(job,ref,source=source)
    assert view['history_alias']=='inputs/history/nodes/exact-goal/002/science.json'
    assert view['role']=='W' and view['limitations']==source['public']['limitations']
    with pytest.raises(ValueError,match='exact task binding'):
        c._parent_view(job,dict(ref,path='inputs/parents/003/conclusion.json'),source=source)




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
