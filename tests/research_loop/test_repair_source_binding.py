"""Repair retains the exact old observation and binds current H authority."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, execution, experiments, history, search, semantic
from execution_fixtures import execution_registry
from test_breadth_science import a, findings, h, prepare, run, worker
from test_history_handoff import finish


def revision(*, label='P', minimal_test='A corrected discriminating prediction', scope='Revised scientific scope'):
    return dict(scope=scope, commitments=[dict(label=label, minimal_test=minimal_test, existing_ref='h001.m1')],
                focus=dict(label='h001.m1', action='repair'))


def original(tmp_path, *, cov=False, failed=True):
    root=prepare(tmp_path,cov=cov)
    h(root,labels=('P','Q','R','S'))
    _,config=worker(root,tmp_path,2,[1],cov=cov)
    observed=run(root,config,'P',failed=failed)
    for label in ('Q','R','S'):run(root,config,label)
    finish(root,2,'W',[1],mechanism_findings=findings('P','Q','R','S'))
    return root,config,observed


def revise(root, *, choice=None, parents=(1,2,3), selection=None):
    a(root,3,[1,2],action='revise_h',dispositions=[dict(mechanism_ref='h001.m2',status='screened',
        reason='The exact original W supplies this unchanged mechanism observation')])
    return finish(root,4,'H',list(parents),action='experiment',search_choice=choice or revision(),selection=selection)


def receipt(root,step):
    return c.read(execution.record(root,step)/'result-identity.json')['control_outcome']


@pytest.mark.parametrize('choice',[
    revision(),revision(label='Renamed P'),revision(minimal_test='Another discriminating measurement'),
    revision(scope='Original scientific scope with clarified scientific constraint')])
def test_cov_revised_h_repairs_exact_old_combination_without_inheriting_screening(tmp_path,execution_registry,choice):
    root,config,observed=original(tmp_path,cov=True)
    old_paths=[path for path in execution.record(root,2).rglob('*') if path.is_file()]
    old_paths+=[root/'host-history/observations/002.json']
    preserved={path:path.read_bytes() for path in old_paths}
    revise(root,choice=choice)
    assert receipt(root,4)['search_status']=='applied'
    assert receipt(root,4)['diagnostics']==[]
    state=search.project(root,5)
    assert state['mechanisms']['h001.m1']['source_h']==4
    assert state['mechanisms']['h001.m1']['status']=='pending'
    assert state['mechanisms']['h001.m2']['status']=='screened'
    job,repair=worker(root,tmp_path,5,[4],cov=True)
    subject=job['search']['repair_source'];authorization=job['search']['repair_authorization']
    assert subject['step']==2 and subject['call']['call_id']==observed['call_id']
    assert subject['call']['status']=='known_failed'
    assert subject['call']['mechanism_binding']['source_h']==1
    assert subject['call']['mechanism_binding']['source_h_ref']==c.record_ref(execution.record(root,1)/'result-identity.json')
    assert authorization['step']==4 and authorization['seal_ref']==c.record_ref(execution.record(root,4)/'result-identity.json')
    assert job['search']['repair_root']==2 and search.launch_reason(root,job) is None
    assert repair['breadth']['baseline']==[dict(mechanism_ref='h001.m1',accepted_count=1)]
    assert repair['breadth']['mechanisms'][0]['source_h']==4
    public=c.science_view(job,root)['repair_observation']
    assert public['source_step']==2 and public['authorization_step']==4
    assert 'sha256' not in json.dumps(public) and 'snapshot' not in json.dumps(public)
    restored=run(root,repair,choice['commitments'][0]['label'])
    assert restored['mechanism_binding']['source_h']==4
    finish(root,5,'W',[4])
    assert receipt(root,5)['search_status']=='applied'
    assert search.project(root,6)['focus']==dict(mechanism_ref='h001.m1',action='repair')
    assert search.project(root,6)['repairs']=={2:1}
    assert search.project(root,6)['opportunity_counts']['h001.m1']==2
    a(root,6,[4,5],focus='h001.m1',mode='improve',dispositions=[dict(mechanism_ref='h001.m1',status='screened',
        reason='The new W observation under the revised H supports scientific assessment')])
    assert receipt(root,6)['search_status']=='applied'
    later=c.build_job(root,7,'W',[4,5,6])
    assert search.launch_reason(root,later) is None
    assert later['search']['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert search.project(root,7)['repairs']=={2:1}
    assert search.project(root,7)['mechanisms']['h001.m1']['status']=='screened'
    assert preserved=={path:path.read_bytes() for path in preserved}


def test_known_success_transport_can_still_have_scientific_repair_intent(tmp_path,execution_registry):
    root,_,_=original(tmp_path,failed=False)
    revise(root)
    job=c.build_job(root,5,'W',[4])
    assert receipt(root,4)['search_status']=='applied'
    assert job['search']['repair_source']['call']['status']=='known_success'
    assert search.launch_reason(root,job) is None


def test_a_same_h_scope_requests_finite_repair_stage_without_h_revision(tmp_path,execution_registry):
    root,_,_=original(tmp_path)
    a(root,3,[1,2],focus='h001.m1',mode='repair')
    job=c.build_job(root,4,'W',[1,2,3])
    assert job['search']['repair_source']['step']==2
    assert job['search']['repair_authorization']['step']==3
    assert job['search']['breadth']['mechanisms'][0]['source_h']==1
    assert job['search']['breadth']['baseline']==[dict(mechanism_ref='h001.m1',accepted_count=1)]
    assert search.launch_reason(root,job) is None


def test_repair_missing_direct_source_is_unresolved_before_dependent_w(tmp_path,execution_registry):
    root,_,_=original(tmp_path)
    revise(root,parents=(1,3))
    assert receipt(root,4)['search_status']=='unresolved'
    diagnostic=dict(code='search_repair_source_not_direct',subject='h001.m1')
    assert diagnostic in receipt(root,4)['diagnostics']
    job=dict(step=5,role='W',search=search.bind(root,5,'W'))
    assert search.launch_reason(root,job)==diagnostic['code']
    result=c.read(execution.raw(root,4));author=c.read(root/'jobs/004.json')
    route=('W',[4],result['route_reason'],None,4,2,None)
    routed=semantic.effective_route(root,4,result,author,route)
    assert routed[0]=='H' and routed[-1]['reason']==diagnostic['code']
    correction=c.build_job(root,5,'H',[4],author_correction=routed[-1])
    assert diagnostic in semantic.public_request(root,correction)['unresolved']


def test_multiple_known_calls_require_exact_source_input_selection(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root,labels=('P',))
    _,config=worker(root,tmp_path,2,[1])
    first=run(root,config,'P',failed=True);second=run(root,config,'P',failed=True)
    finish(root,2,'W',[1],mechanism_findings=findings('P'))
    a(root,3,[1,2],action='revise_h')
    job=c.build_job(root,4,'H',[1,2,3])
    payload=dict(next_action='experiment',route_reason='Restore one exact discriminating observation',search_plan=revision())
    result=search.interpret(root,job,payload)
    assert result['diagnostics']==[dict(code='search_repair_source_ambiguous',subject='h001.m1')]
    assert first['call_id']!=second['call_id']


@pytest.mark.parametrize('direct',[False,True])
def test_exact_selected_input_selects_only_its_original_call(tmp_path,execution_registry,direct):
    root=prepare(tmp_path);h(root,labels=('P',))
    _,config=worker(root,tmp_path,2,[1])
    work=c.workspace(root,2)
    source=work/'artifacts'/'failure.py'
    source.write_text('raise SystemExit(3)\n')
    exact=experiments.dispatch(config,source,new=True,mechanism='P')
    # Distinct accepted input has a distinct source identity.
    other=work/'work'/'other.py';other.write_text('raise SystemExit(4)\n')
    experiments.dispatch(config,other,new=True,mechanism='P')
    finish(root,2,'W',[1],files={'failure.py':'raise SystemExit(3)\n'},mechanism_findings=findings('P'))
    a(root,3,[1,2],action='revise_h')
    selection=next(row['id'] for row in history.implementation_options(root,2) if row['label']=='failure.py')
    finish(root,4,'H',[1,2,3] if direct else [1,3],action='experiment',search_choice=revision(),selection=[selection])
    job=c.build_job(root,5,'W',[4])
    assert receipt(root,4)['search_status']=='applied'
    assert job['search']['repair_source']['call']['call_id']==exact['call_id']
    assert search.launch_reason(root,job) is None


def test_equal_output_bytes_do_not_select_a_different_accepted_input(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root,labels=('P',))
    _,config=worker(root,tmp_path,2,[1])
    run(root,config,'P',failed=True);run(root,config,'P',failed=True)
    finish(root,2,'W',[1],files={'copy.py':'raise SystemExit(3)\n'},mechanism_findings=findings('P'))
    a(root,3,[1,2],action='revise_h')
    selection=next(row['id'] for row in history.implementation_options(root,2) if row['label']=='copy.py')
    finish(root,4,'H',[1,2,3],action='experiment',search_choice=revision(),selection=[selection])
    assert receipt(root,4)['search_status']=='unresolved'
    assert dict(code='search_repair_source_ambiguous',subject='h001.m1') in receipt(root,4)['diagnostics']


def test_archive_alias_of_new_output_cannot_select_its_original_input_call(tmp_path,execution_registry):
    root=prepare(tmp_path);h(root,labels=('P',))
    job,config=worker(root,tmp_path,2,[1])
    experiment=c.workspace(root,2)/'work'/'generator';experiment.mkdir()
    generator=experiment/'run.py'
    generator.write_text('from pathlib import Path\nimport json\n'
        'out=Path("artifacts");out.mkdir(exist_ok=True)\n'
        '(out/"original.py").write_text("raise SystemExit(3)\\n")\n'
        '(out/"result.json").write_text(json.dumps(dict(version=1,metrics=[],outputs=['
        'dict(path="artifacts/original.py",label="A distinct output object")])))\n')
    generated=experiments.dispatch(config,generator,new=True,mechanism='P',
        command_options=dict(experiment_directory=str(experiment)))
    rows=history.managed_calls(root,job)
    output=next(item for item in history.scientific_outputs(root,job,rows[0]) if item['name']=='original.py')
    archive=root/output['path']
    executed=experiments.dispatch(config,archive,new=True,mechanism='P')
    assert generated['call_id']!=executed['call_id'] and executed['status']=='known_failed'
    intent=execution.record(root,2)/'native-intent.json';original_intent=intent.read_bytes()
    finish(root,2,'W',[1],mechanism_findings=findings('P'),preserve_native_intent=True)
    assert intent.read_bytes()==original_intent
    sealed=c.read(execution.record(root,2)/'result-identity.json')
    material=next(item for item in sealed['materials'] if item['path'].endswith('/original.py'))
    accepted=experiments.Records(config).accepted(executed['call_id'])
    # The physical path and content match, but this selected logical object is
    # another call's OUTPUT, not this call's accepted input material lineage.
    assert material['source']==accepted['source_script']==str(archive)
    assert Path(material['source']).read_bytes()==Path(accepted['script']['path']).read_bytes()
    assert '/'+generated['call_id']+'/' in material['path']
    a(root,3,[1,2],action='revise_h')
    selected=next(row['id'] for row in history.implementation_options(root,2) if row['label']=='original.py')
    finish(root,4,'H',[1,2,3],action='experiment',search_choice=revision(),selection=[selected])
    assert receipt(root,4)['search_status']=='unresolved'
    assert dict(code='search_repair_source_ambiguous',subject='h001.m1') in receipt(root,4)['diagnostics']
    planned=dict(step=5,role='W',search=search.bind(root,5,'W'))
    assert search.launch_reason(root,planned)=='search_repair_source_ambiguous'


@pytest.mark.parametrize('part',['accepted','terminal','source_h_seal'])
def test_repair_never_accepts_tampered_old_originals(tmp_path,execution_registry,part):
    root,_,_=original(tmp_path)
    revise(root)
    job=c.build_job(root,5,'W',[4])
    source=job['search']['repair_source']
    path=(execution.record(root,1)/'result-identity.json' if part=='source_h_seal'
          else root/source['call'][part]['snapshot'])
    value=c.read(path)
    value['tampered']=True
    path.chmod(0o600);c.write(path,value)
    with pytest.raises(ValueError):search.bind(root,5,'W')


def test_repair_subject_unknown_missing_and_exhausted_are_distinct(tmp_path,execution_registry,monkeypatch):
    root,_,_=original(tmp_path)
    a(root,3,[1,2],action='revise_h')
    job=c.build_job(root,4,'H',[1,2,3])
    payload=dict(next_action='experiment',route_reason='Restore the exact observation',search_plan=revision())
    state=search.project(root,4)
    subject=search._repair_choice(root,job,'h001.m1',payload=payload)
    assert subject['status']=='available'
    state['repairs'][2]=c.settings(root)['search_policy']['max_requested_repairs']
    exhausted=search.interpret(root,job,payload,state=state)
    assert exhausted['diagnostics']==[dict(code='search_repair_limit',subject='h001.m1')]
    assert state['repairs'][2]==c.settings(root)['search_policy']['max_requested_repairs']
    for status in ('unknown','missing_original','version_mismatch'):
        monkeypatch.setattr(search,'_repair_choice',lambda *args,_status=status,**kwargs:dict(status=_status,root=None,source=None))
        result=search.interpret(root,job,payload)
        assert result['diagnostics']==[dict(code='search_repair_source_'+status,subject='h001.m1')]
