"""Single bounded default view, original pages, and retired write rejection."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import contracts as c, controller, execution, history, lineage, protocol as current_protocol
from execution_fixtures import execution_registry
from test_history_handoff import finish
from test_lineage import synthetic_host_launch


def scene(tmp_path, *, version=1, observer=None):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Compare scientific mechanisms using original evidence.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=100000,max_seconds=3600,max_steps=24,
        model='fake',reasoning_effort='high',search_policy=None,history_observer=observer,
        lineage_projection_version=version)
    return root


def written(value):
    return (json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()


def test_all_roles_have_one_lineage_brief_and_exact_scientific_return(tmp_path):
    root=scene(tmp_path)
    for step,role in enumerate(['H','W','A','E'],1):
        parents=[step-1] if step>1 else []
        job,work,record=finish(root,step,role,parents,text='Complete original scientific text '*30)
        view=c.read(work/'science-task.json')
        assert job['lineage_projection_version']==1
        assert 'lineage' in view and 'history' not in view and 'search' not in view
        assert 'implementation_options' not in view
        assert len(written(view['lineage']))<=4096
        assert c.read_scientific_output(record/'science-output.json')['scientific_result'] == 'Complete original scientific text '*30
        assert c.validate(work)['summary']=='A contribution'


@pytest.mark.parametrize('protocol',range(1,9))
def test_retired_protocol_cannot_rebuild_lineage_or_mutate_originals(tmp_path,monkeypatch,protocol):
    root=scene(tmp_path);finish(root,1,'H',[],action='wait')
    settings=c.read(root/'settings.json');settings['handoff_protocol']=protocol
    c.write(root/'settings.json',settings)
    c.write(root/'settings-identity.json',dict(path='settings.json'))
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
