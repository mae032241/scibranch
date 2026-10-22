"""Single bounded default view, original pages, and retired write rejection."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, history, lineage, protocol as current_protocol
from execution_fixtures import execution_registry
from test_history_handoff import finish


def scene(tmp_path, *, version=1, observer=None):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Compare scientific mechanisms using original evidence.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=100000,max_seconds=3600,max_steps=24,
        model='fake',reasoning_effort='high',search_policy=None,history_observer=observer,
        lineage_projection_version=version)
    return root


def written(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()


def pages(work,start,key):
    rows=[];path=start
    while path:
        original=work/path;value=c.read(original)
        assert original.stat().st_mode & 0o222 == 0
        assert len(original.read_bytes())<=history.MAX_PAGE_BYTES
        assert len(value[key])<=(8 if key=='records' else 16)
        rows.extend(value[key]);path=value['next_page']
    return rows


def test_all_roles_have_one_lineage_brief_and_exact_science_pages(tmp_path):
    root=scene(tmp_path)
    for step,role in enumerate(['H','W','A','E'],1):
        parents=[step-1] if step>1 else []
        job,work,record=finish(root,step,role,parents,text='Complete original scientific text '*30)
        view=c.read(work/'science-task.json')
        assert job['lineage_projection_version']==1
        assert 'lineage' in view and 'history' not in view and 'search' not in view
        assert 'implementation_options' not in view
        assert len(written(view['lineage']))<=4096
        if step>1:
            rows=pages(work,view['lineage']['indexes']['nodes'],'records')
            source=rows[-1]
            detail=c.read(work/source['conclusion_pages'][0])
            assert 'Complete original scientific text '*30 in detail['text']
        assert c.validate(work)['summary']=='A contribution'


def test_ninth_node_and_seventeenth_candidate_implementation_are_reachable(tmp_path):
    adapter=tmp_path/'observer.py'
    adapter.write_text('''import json,sys
r=json.load(sys.stdin)
rows=[dict(id=f'candidate-{i}',label=f'Public candidate {i}',implementation=None,evaluation_key='public-v1',metric=i/100,direction='max',status='ok',evidence=['project/global/objective.md']) for i in range(1,18)] if r['step']==1 else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Synthetic public observations'))))
''')
    root=scene(tmp_path,observer=dict(adapter=str(adapter),config={}))
    finish(root,1,'H',[],files={f'a{i:02}.py':f'implementation {i}' for i in range(17)})
    for step in range(2,10):finish(root,step,'H',[])
    job=c.build_job(root,10,'W',[9]);work=c.workspace(root,10);view=c.science_view(job,root)['lineage']
    nodes=pages(work,view['indexes']['nodes'],'records')
    assert [row['step'] for row in nodes]==list(range(1,10))
    assert pages(work,view['indexes']['candidates'],'candidates')[-1]['id']=='candidate-17'
    options=pages(work,nodes[0]['implementation_index'],'implementations')
    assert options[-1]['id']=='H001-I017'
    finish(root,10,'W',[9],selection=['H001-I017'])
    following=c.build_job(root,11,'A',[10])
    assert following['selected_inputs'][0]['origin_step']==1
    assert (c.workspace(root,11)/following['selected_inputs'][0]['material']['path']).read_text()=='implementation 16'
    relation=pages(c.workspace(root,11),following['history']['view']['indexes']['relations'],'relations')[0]
    assert relation['kind']=='reuse' and relation['implementation_id']=='H001-I017'
    assert not any(key in written(relation).decode() for key in ('sha256',str(root),'"source":'))
    assert not (c.workspace(root,11)/'inputs/parents/001/artifacts/a00.py').exists()


@pytest.mark.parametrize('damage',['missing','changed'])
def test_bound_lineage_page_damage_is_hard_before_acceptance(tmp_path,damage):
    root=scene(tmp_path);finish(root,1,'H',[])
    job=c.build_job(root,2,'W',[1]);work=c.workspace(root,2)
    path=work/job['history']['view']['indexes']['nodes']
    path.chmod(0o600)
    if damage=='missing':path.unlink()
    else:path.write_text('{}')
    with pytest.raises(ValueError):c._validate_inputs(root,work,job)
    assert not (execution.origin(root,2)/'native-intent.json').exists()


@pytest.mark.parametrize('protocol',range(1,9))
def test_retired_protocol_cannot_rebuild_lineage_or_mutate_originals(tmp_path,monkeypatch,protocol):
    root=scene(tmp_path);finish(root,1,'H',[],action='wait')
    settings=c.read(root/'settings.json');settings['handoff_protocol']=protocol
    c.write(root/'settings.json',settings)
    c.write(root/'settings-identity.json',dict(sha256=c.digest(root/'settings.json')))
    def snapshot():
        return {path.relative_to(root).as_posix():(path.stat().st_mode,path.read_bytes() if path.is_file() else None)
                for path in [root,*root.rglob('*')]}
    before=snapshot()
    def forbidden(*args,**kwargs):raise AssertionError('Retired protocol must not render current lineage')
    monkeypatch.setattr(lineage,'bind',forbidden)
    with pytest.raises(current_protocol.UnsupportedProtocol,match=f'current research-loop protocol {current_protocol.CURRENT}'):
        c.build_job(root,2,'W',[1])
    assert snapshot()==before


def test_thousand_node_multibyte_brief_bounds_every_lineage_domain():
    records=[dict(step=i,role='W',view=dict(summary='科学'*512,summary_omitted_chars=1,limitations=['不确定'*256],limitations_omitted_count=8,
        conclusion_pages=[f'inputs/history/nodes/{i:03d}/0001.json'])) for i in range(1,1001)]
    candidates=dict(candidate_index=[dict(id=f'candidate-{i}') for i in range(4096)],coverage=dict(status='partial',note='尚未确认'*64),
        current_candidate=dict(candidate_id='candidate-1',observed_at=999),a_recommended=dict(candidate_id='candidate-2',author_step=999),
        actual_submission=dict(candidate_id='candidate-3',observed_at=998,retention='unknown'),
        candidate_facts=[dict(id=f'candidate-{i}',evaluation_key='公开评估'*128,metric=.5,direction='max',status='ok') for i in range(8)],candidate_facts_omitted_count=20)
    state=dict(focus=dict(mechanism_ref='h001.m1',action='screen',repair_source=None),mechanisms={f'h001.m{i}':dict(status='pending') for i in range(1000)})
    value=lineage.brief(records,state,dict(relations=[{}]*1000,coverage='partial'),candidates,
        dict(nodes='inputs/history/pages/0001.json',candidates='inputs/history/candidates/0001.json',mechanisms='inputs/lineage/mechanisms/0001.json',relations='inputs/lineage/relations/0001.json'))
    assert len(written(value))<=4096 and value['node_count']==1000
    assert value['candidates']['current']['candidate_id']=='candidate-1'
    assert value['candidates']['a_recommended']['candidate_id']=='candidate-2'
    assert value['candidates']['actual']['candidate_id']=='candidate-3'
    assert value['recent_omitted_count']==1000-len(value['recent'])


@pytest.mark.process_e2e
def test_long_candidate_member_references_bound_brief_and_keep_complete_catalogue(tmp_path,execution_registry):
    from scidiscovery.research_loop import experiments
    root=scene(tmp_path);finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1])
    c.write(execution.origin(root,2)/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/002.json')))
    config=experiments.configure(root,2,'local_execution:command',dict(run_argv=[sys.executable]),
        c.workspace(root,2)/'work/experiment-config.json')
    directory=c.workspace(root,2)/'experiments/alternatives';directory.mkdir(parents=True)
    prefix='/'.join(f'p{index:02d}'+('x'*177) for index in range(9))
    artifacts=[f'artifacts/{prefix}/{name}.bin' for name in ('first','second')]
    declaration=dict(version=1,metrics=[],outputs=[dict(path=path) for path in artifacts],
        candidates=[dict(artifact=path) for path in artifacts])
    script=directory/'run.py'
    script.write_text('from pathlib import Path\n'
        'for path in '+repr(artifacts)+':\n'
        '    original=Path(path);original.parent.mkdir(parents=True,exist_ok=True);original.write_text(path)\n'
        'Path("artifacts/result.json").write_text('+repr(json.dumps(declaration))+')\n')
    result=experiments.dispatch(config,script,command_options=dict(experiment_directory=str(directory)))
    assert result['status']=='known_success'
    finish(root,2,'W',[1])
    job=c.build_job(root,3,'A',[2]);work=c.workspace(root,3)
    view=c.read(work/'science-task.json')['lineage']
    assert len(written(view))<=history.MAX_BRIEF_BYTES
    assert view['candidates']['candidate_facts_omitted_count']>0
    candidates=pages(work,view['indexes']['candidates'],'candidates')
    assert len(candidates)==2
    assert {row['id'] for row in candidates}==set(job['history']['candidate_references'].values())
    assert all(any(row['id'].endswith('/'+path) for row in candidates) for path in artifacts)


@pytest.mark.process_e2e
def test_known_failed_experiment_is_separate_from_original_scientific_conclusion(tmp_path):
    from test_lineage import scene as call_scene
    from scidiscovery.research_loop import cov_lineage as cov
    root,config,digest=call_scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('failed')
    failed=cov.run(config,digest,script,new=True)
    assert failed['status']=='known_failed' and failed['model'] is None
    script.write_text('success');successful=cov.run(config,digest,script,new=True)
    finish(root,2,'W',[1],summary='Valid bounded negative scientific conclusion')
    job=c.build_job(root,3,'A',[2]);work=c.workspace(root,3)
    candidates=pages(work,job['history']['view']['indexes']['candidates'],'candidates')
    assert next(row for row in candidates if row['id']==failed['candidate_id'])['execution_status']=='known_failed'
    assert next(row for row in candidates if row['id']==successful['candidate_id'])['execution_status']=='known_success'
    nodes=pages(work,job['history']['view']['indexes']['nodes'],'records')
    assert nodes[-1]['summary']=='Valid bounded negative scientific conclusion'
