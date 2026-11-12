"""Saved handoff consumers; synthetic originals only, no native/model process."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_prefix, accepted_search, contracts as c
from scidiscovery.research_loop import controller, formal_review, fork_runtime, fork_service, lineage
from scidiscovery.research_loop import continuation, candidate_refs
from scidiscovery.research_loop import originals, semantic
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import scene, finish


def test_formal_review_choice_consumes_the_same_accepted_inventory(scene, monkeypatch):
    job=c.build_job(scene,1,'A',[])
    job['formal_review_choices']=dict(unavailable=[])
    selected=[dict(label='An accepted exact selection')]
    inventory=c.CurrentInventory(scene,job,scene/'native-evidence/001',0,[],{},selected_inputs=selected)
    def forbidden(*args,**kwargs):
        pytest.fail('The review consumer rebuilt selected inputs')
    def request(root,received,current):
        assert root==scene and current is job and received is selected
        return dict(subject=None)
    monkeypatch.setattr(c,'_accept_selected_inputs',forbidden)
    monkeypatch.setattr(formal_review,'_request_value',request)
    with pytest.raises(OutputRepairRequired,match='Select exactly one resolvable'):
        semantic.outcome(scene,job,dict(next_action='formal_review'),inventory=inventory)


def test_output_request_passes_the_same_inventory_to_review_choice(scene, monkeypatch):
    job=c.build_job(scene,1,'A',[])
    job['formal_review_choices']=dict(unavailable=[])
    record=scene/'native-evidence/001'
    raw=record/'science-output.json';c.write(raw,dict(next_action='formal_review'))
    selected=[dict(label='Accepted exact selection')]
    inventory=c.CurrentInventory(scene,job,record,0,[],{},selected_inputs=selected)
    assert inventory.output_error is None  # Exercise the consumer, not saved-error return.
    def task(root,step,*,inventory):
        assert root==scene and step==1 and inventory.selected_inputs is selected
        return job
    def control(current,payload,*,root,inventory):
        assert current is job and root==scene and inventory.selected_inputs is selected
        return payload
    def request(root,received,current):
        assert root==scene and current is job and received is selected
        return dict(subject=None)
    def forbidden(*args,**kwargs):
        pytest.fail('Output repair rebuilt the accepted selection')
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,
        usage=dict(unknown_steps=[]),raw_ref=c.file_ref(raw)))
    monkeypatch.setattr(continuation,'validate_task',task)
    monkeypatch.setattr(c,'science_payload',lambda *args,**kwargs:dict(next_action='formal_review'))
    monkeypatch.setattr(candidate_refs,'control_payload',control)
    monkeypatch.setattr(formal_review,'_request_value',request)
    monkeypatch.setattr(c,'_accept_selected_inputs',forbidden)
    repair=continuation.output_request(scene,1,record,inventory=inventory,source_attempt=0)
    assert repair['code']=='formal_review_unavailable'
    assert repair['raw_ref']==c.file_ref(raw)
    assert repair['outcome_ref']==c.record_ref(record/'native-outcome.json')
    assert inventory.selected_inputs is selected and inventory.output_error is None


def test_assessment_uses_frozen_none_context_without_reinterpreting_old_h(scene, monkeypatch):
    finish(scene,1,'H',[],action='experiment')
    c.build_job(scene,2,'W',[1],instruction='Exact scientific question')
    finish(scene,2,'W',[1])
    generate=c._decision_context
    def only_new_context(job,*,sources):
        assert job['step']==3,'An accepted W/H context was regenerated'
        return generate(job,sources=sources)
    monkeypatch.setattr(c,'_decision_context',only_new_context)
    job=c.build_job(scene,3,'A',[1,2])
    handoff=job['assessment_handoff']
    assert handoff['current_w']=='inputs/parents/002/conclusion.json'
    assert handoff['current_h']=='inputs/parents/001/conclusion.json'
    assert 'previous_a' not in handoff


def test_fork_worker_overview_uses_current_source_h_goal_not_child_goal(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Retain the exact declared mechanism.')
    root=tmp_path/'source'
    controller.prepare(root,objective,public,['fixture-never-launch'],max_tokens=100000,
        max_seconds=600,max_steps=8,model='fixture',reasoning_effort='high')
    plan=dict(scope='Original scientific H scope',commitments=[dict(label='Mechanism',
        minimal_test='A bounded discriminating measurement',existing_ref=None)],focus=dict(index=1,action='screen'))
    finish(root,1,'H',[],action='experiment',search_choice=plan)
    worker=c.build_job(root,2,'W',[1])
    before=c.read(originals.resolve_root_ref(root,worker['history']['breadth_snapshot']))
    with controller._writer(root):pass
    child=tmp_path/'child';limits=dict(max_tokens=10000,max_seconds=60,max_steps=8)
    preview=fork_service.preview(root,2,child,budget=limits)
    assert preview['status']=='fork_preview_ready',preview
    fork_service.create(root,2,child,budget=limits,
        expected_preview_id=preview['preview_id'],operation_id='overview-source-origin')
    job=fork_runtime.build_selected_job(child,2)
    head=accepted_budget.read(child)
    assert head['search']['table']==before['table']
    assert job['search']['breadth']['catalog']==dict(prefix=head['prefix'],max_ordinal=1)
    mechanism=head['search']['table']['mechanisms']['h001.m1']
    assert mechanism['ordinal']==1
    source=before['table']['mechanisms']['h001.m1']
    assert mechanism['source_ref']==source['source_ref']
    assert mechanism['source_step']==source['source_step']==1
    assert mechanism['source_role']==source['source_role']=='H'
    assert mechanism['source_origin_identity']==originals.local_identity(root)
    published=c.read(accepted_prefix.visibility(child,head['prefix'])/'breadth-table.json')
    assert published['mechanisms'][0]['ordinal']==1
    assert published['mechanisms'][0]['original']==f'inputs/history/nodes/{originals.local_identity(root)["goal_id"]}/001/scientific-original.json'
    assert str(root) not in json.dumps(published)
    link=c._role_lineage(job)['mechanisms']['overview'][0]['original']
    assert link==f'inputs/history/nodes/{originals.local_identity(root)["goal_id"]}/001/science.json'
    assert (accepted_prefix.visibility(child,job['history']['prefix'])/link.removeprefix('inputs/history/')).is_file()


def test_fork_seed_materializes_saved_candidate_and_work_public_pages(tmp_path, monkeypatch):
    """Transfer accepted control facts; this test grants no scientific verdict."""
    parent=tmp_path/'parent';child=tmp_path/'child';parent.mkdir();child.mkdir()
    origin=dict(goal_id='parent',settings_ref=dict(path=str(parent/'settings.json')))
    source=SimpleNamespace(origin_identity=origin)
    prefix=accepted_prefix.empty(parent,dict(goal_id='parent'))
    nav_path='nodes/parent/001/index.json'
    prefix.update(count=1,cutoff=1,tip=dict(navigation=dict(path=nav_path)))
    candidate=dict(origin_identity=origin,public_reference='W001-C001',first_step=1,
        observation=dict(id='candidate-one',status='known_success'))
    work=dict(work_ref='A001-D001',status='pending',mechanism_refs=['h001.m1'],source_a=1)
    pages=[accepted_prefix._key_file(Path('entities')/kind,identity).as_posix()
        for kind,identity in [('candidate','W001-C001'),('work','A001-D001')]]
    member=dict(origin_identity=origin,step=1,ordinal=1,branch=origin,
        navigation=dict(path=nav_path))
    research=lineage.research_navigation({},dict(step=1,role='H'),
        dict(summary='Saved authored mechanism',scientific_result='Open evidence question',limitations=['Missing measurement']),
        node_path='nodes/parent/001')
    for path,value in [('member.json',member),('navigation.json',dict(step=1,research=research,entities=[dict(page=path) for path in pages])),
            ('event.json',dict(kind='scientific_seal',step=1,index_updates={
                accepted_search.key('candidate','candidate-one'):dict(current=candidate),
                accepted_search.key('work','A001-D001'):dict(current=work)}))]:
        c.write(parent/path,value)
    (parent/'raw.json').write_text('{"original":"authored science"}')
    (parent/'result.json').write_text('{"original":"accepted result"}')
    c.write(parent/'result-identity.json',dict(raw_ref=dict(path='raw.json'),result_ref=dict(path='result.json')))
    entry=dict(step=1,origin_identity=origin,member_ref=dict(path='member.json'),
        scientific_event=dict(path='event.json'),navigation_ref=dict(path='navigation.json'))
    monkeypatch.setattr(originals,'_manifest',lambda root:dict(cut=dict(history=dict(prefix=prefix)),prefix_entries=[entry]))
    monkeypatch.setattr(originals,'selected_source',lambda root:source)
    monkeypatch.setattr(originals,'for_origin',lambda *args,**kwargs:source)
    monkeypatch.setattr(originals,'source_for_ref',lambda *args:source)
    monkeypatch.setattr(originals,'resolve_ref',lambda owner,ref:parent/ref['path'])
    monkeypatch.setattr(originals,'source_record',lambda owner,name:parent/name)
    seed=accepted_search.empty()
    frozen,rows,_=accepted_prefix.fork_seed(child,dict(goal_id='child'),search_seed=seed,identity_root=child)
    public=accepted_prefix.visibility(child,frozen)
    navigation=c.read(public/nav_path)
    assert [row['page'] for row in navigation['entities']]==pages
    assert navigation['research']==research
    assert c.read(public/Path(nav_path).parent/'research.json')==research
    assert (public/Path(nav_path).parent/'scientific-original.json').samefile(parent/'raw.json')
    documents=[c.read(public/page) for page in pages]
    assert documents[0]['observation']['id']=='W001-C001'
    assert documents[1]['work_ref']=='A001-D001'
    assert all(str(parent) not in json.dumps(value) for value in documents)
    assert len(rows)==2
