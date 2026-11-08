"""Real sealing and accepted navigation reach UI without a model/executor."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, experiment_client, instance_ui, native, protocol, ui_components, ui_mechanisms, ui_view, worker_research
from test_workspace_ui import host, login, request


pytestmark=pytest.mark.process_e2e


def test_sealed_w_first_declaration_findings_and_selected_memory_reach_pages(tmp_path,monkeypatch):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Retain observations and explicit uncertainty.')
    observer=tmp_path/'observer.py'
    observer.write_text('''import json,sys
r=json.load(sys.stdin)
rows=[dict(id='observed-model',label='Retained candidate',implementation='history/nodes/001/artifacts/model.py',evaluation_key='author split',metric=None,direction=None,status='unknown',evidence=['project/nodes/001/artifacts/model.py'])]
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='partial',note='Presentation fixture only'))))
''')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],native_backend='protocol',
        model='ui-fixture',reasoning_effort='high',max_tokens=100000,max_seconds=120,max_steps=6,
        research_strategy_version=3,history_observer=dict(adapter=str(observer),config={}))
    job=c.build_job(root,1,'W',[])
    assert job['search']['breadth'] is None
    directory=experiment_client._record_directory_from_facts(root,1,job);directory.mkdir(parents=True,exist_ok=True)
    config=dict(record_dir=str(directory),scope=protocol.stage_scope_for_job(root,job,
        task_ref=c.record_ref(root/'jobs/001.json')),research_strategy_version=3,
        breadth=job['search']['breadth'],worker_research_goal_id=c.settings(root)['goal_id'])
    previous=worker_research.initial(config)
    state=worker_research.evolve(previous,[dict(action='declare',mechanism_ref='w001.no_call',
        question='Can a new independent input resolve the conditional failure?')],scope=config['scope'],
        original_ref=dict(path=str(directory/'research/000001.json')))
    worker_research.publish(config,previous,state)
    work=c.workspace(root,1);record=c.host_record(work)
    (work/'artifacts').mkdir(exist_ok=True)
    (work/'artifacts/model.py').write_text('# deterministic retained material; no scientific qualification\n')
    payload=dict(summary='A conditional observation with open questions',scientific_result='The fixture retains an observation only.',
        limitations=['No independent validation'],implementation_selection=None,stage_intent='handoff',
        mechanism_findings=[dict(mechanism='w001.no_call',basis='blocked',finding='The independent input is missing.')],
        research_memory=dict(question='Which missing observation changes the decision?',candidates=[dict(
            candidate='observed-model',data='Declared public rows',evaluation='Declared author split',
            failure_condition='Zero initialization',open_question='Does another initializer retain the effect?',
            limitations=['No independent final sample'],evidence=[dict(ref='model.py',relation='opposes',
                note='Failure occurred only at zero initialization.')])]))
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'))
    c.write(record/'native-intent.json',intent);accepted_budget.accept_native(root,1,record,intent)
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,1)
    frozen={path:path.read_bytes() for path in (root/'jobs/001.json',record/'science-output.json',record/'result-identity.json')}
    reader=ui_view.Reader(root)
    node=reader.node(1)
    diagnostics=dict(
        memory=node['research']['candidate_memory'],
        observation_candidates=(reader.observation(1) or {}).get('candidates'),
        coverage=(reader.observation(1) or {}).get('coverage'),
        candidate_diagnostics=(reader.observation(1) or {}).get('candidate_diagnostics'),
        observer_error=node['seal'].get('observer_error'),
        materials=[row.get('path') for row in node['seal'].get('materials',[])])
    assert node['research']['candidate_memory']['candidates'],json.dumps(diagnostics,ensure_ascii=False)
    assert reader.observation(1)['research_state']['mechanisms']['w001.no_call']['revision']==1
    # All following projections consume the owner's already loaded values.
    def no_reload(*args,**kwargs):raise AssertionError('UI consumer reopened an accepted record')
    monkeypatch.setattr(c,'read',no_reload)
    memory=ui_view.candidate_memory(node,lambda text,limit:text[:limit],loaded_nodes=reader._nodes.values())
    assert memory['candidates'][0]['evidence'][0]['href']=='/nodes/1/materials/1'
    assert 'Zero initialization' in ui_components.candidate_memory(memory)
    mechanism=ui_mechanisms.project(reader,[1],lambda text,limit:text[:limit])
    assert 'The independent input is missing.' in ui_mechanisms.render(mechanism)
    monkeypatch.undo()
    key=tmp_path/'operator.key';key.write_bytes(b'ui-sealed-navigation-fixture-key-32bytes');key.chmod(0o600)
    with host(root,key,workspace=False) as (connection,url,_):
        cookie=login(connection,url)
        for path in ('/results','/results/1','/candidates'):
            status,body,_=request(connection,'GET',path,cookie=cookie)
            if status!=200 and path=='/candidates':
                direct=instance_ui.InstanceUI(root,secret_file=key,approval_host=False)
                try:direct._candidate_history()
                finally:direct.translation.close()
            assert status==200,(path,body.decode(errors='replace'))
            page=body.decode()
            assert 'W 选择的候选证据记忆' in page and 'Zero initialization' in page
            assert 'No independent final sample' in page
        page=request(connection,'GET','/results',cookie=cookie)[1].decode()
        assert 'w001.no_call' in page and 'revision 1' in page and 'The independent input is missing.' in page
        assert str(root) not in page
    assert all(path.read_bytes()==raw for path,raw in frozen.items())
