"""Scientific nomination previews never shrink the exact retained model pool."""
import copy
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import (candidate_nominations as nominations,
    candidate_refs, cli, contracts as c, delivery, execution, experiments,
    generic_results, history, budget, search)
from execution_fixtures import execution_registry, remote_registry
from test_breadth_science import prepare, h, a, findings
from test_history_handoff import finish


def accepted_worker(root,tmp_path):
    """Mirror native acceptance before configuring the test's real interface."""
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    config=experiments.configure(root,2,'local_execution:command',dict(run_argv=[sys.executable]),tmp_path/'config-2.json')
    return job,config


def failed_worker_observation(root,tmp_path):
    """Retain a deterministic adapter observation; no external service runs."""
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    config=experiments.configure(root,2,'remote_execution:fixture',
        dict(outcome='known_failed',require_contract=True),tmp_path/'config-2.json')
    payload=c.workspace(root,2)/'work/failed-observation.json'
    c.write(payload,dict(message='remote-original'))
    reply=experiments.dispatch(config,payload,mechanism='P')
    assert reply['status']=='known_failed'


def nomination_job(monkeypatch, *, slots=2):
    monkeypatch.setattr(history,'managed_calls',lambda root,job:[])
    return dict(role='W',step=2,scientific_response_fields=['candidate_nominations'],
        candidate_nomination_policy=nominations.configuration(slots),
        candidate_nomination_mechanisms=[dict(mechanism_ref=f'h001.m{i}',status='deferred' if i==2 else 'screened')
            for i in range(1,6)],history=dict(candidate_ids=[f'model-{i}' for i in range(32)]))


def declaration():
    return dict(representatives=[dict(mechanism_ref=f'h001.m{i}',candidate=f'model-{i}',
        reason=f'This retained model represents the declared scientific approach {i}.') for i in range(1,6)],
        extras=[dict(candidate=f'model-{i}',reason='An alternative with distinct scientific limitations.') for i in (6,7)],gaps=[])


def test_five_mechanism_representatives_allow_seven_distinct_models_without_ranking(monkeypatch):
    job=nomination_job(monkeypatch);value=declaration();before=copy.deepcopy(value)
    nominations.require(None,job,dict(candidate_nominations=value))
    assert value==before and len(value['representatives'])+len(value['extras'])==7
    job['candidate_nomination_policy']=nominations.configuration(0)
    with pytest.raises(ValueError,match='extra_slots'):nominations.require(None,job,dict(candidate_nominations=value))


@pytest.mark.parametrize('damage',['missing','duplicate','unknown','extra-is-representative','too-many','reason'])
def test_structural_nominations_repair_without_scientific_scoring(monkeypatch,damage):
    job=nomination_job(monkeypatch);value=declaration()
    if damage=='missing':value['representatives'].pop(1)  # deferred still needs coverage
    if damage=='duplicate':value['representatives'].append(value['representatives'][0])
    if damage=='unknown':value['representatives'][0]['candidate']='unregistered'
    if damage=='extra-is-representative':value['extras'][0]['candidate']='model-1'
    if damage=='too-many':value['extras'].append(dict(candidate='model-9',reason='Additional science'))
    if damage=='reason':value['representatives'][0]['reason']=' '
    with pytest.raises(ValueError,match='candidate_nominations'):
        nominations.require(None,job,dict(candidate_nominations=value))


def test_gaps_and_shared_model_keep_mechanisms_without_inventing_candidates(monkeypatch):
    job=nomination_job(monkeypatch);value=declaration()
    value['representatives'][1].update(candidate='model-1',reason='This one model explicitly tests both mechanisms under the stated conditions.')
    value['gaps']=[dict(mechanism_ref=value['representatives'].pop()['mechanism_ref'],reason='The original model is unsuitable under the revised conditions.')]
    nominations.require(None,job,dict(candidate_nominations=value))
    job['history']['candidate_ids']=[]
    value=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],reason='No successful usable model is available.')
        for row in job['candidate_nomination_mechanisms']])
    nominations.require(None,job,dict(candidate_nominations=value))
    job['candidate_nomination_mechanisms'][-1]['status']='waived'
    value['gaps'].pop()
    nominations.require(None,job,dict(candidate_nominations=value))


@pytest.mark.parametrize('slots',[None,0,4,-1])
def test_official_cli_freezes_extra_slots_without_running_models(tmp_path,slots):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Compare original scientific mechanisms.')
    root=tmp_path/'instance'
    args=['prepare','--root',str(root),'--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json','["never-run"]','--max-tokens','10000','--max-seconds','60','--max-steps','8']
    if slots is not None:args+=['--candidate-extra-slots',str(slots)]
    assert cli.main(args)==(1 if slots==-1 else 0)
    if slots==-1:assert not root.exists()
    else:assert c.settings(root)['candidate_nomination_policy']==dict(version=1,extra_slots=2 if slots is None else slots)


@pytest.mark.process_e2e
def test_thirty_two_models_one_call_preview_seven_and_a_can_choose_unlisted_original(tmp_path,execution_registry,monkeypatch):
    root=prepare(tmp_path);h(root,labels=('P','Q','R','S','T'))
    job,config=accepted_worker(root,tmp_path);directory=c.workspace(root,2)/'experiments/models';directory.mkdir(parents=True)
    artifacts=[f'artifacts/model-{i}.bin' for i in range(32)]
    original=dict(version=1,metrics=[],outputs=[dict(path=path) for path in artifacts],
        candidates=[dict(artifact=path,label=f'Native model {i}') for i,path in enumerate(artifacts)])
    script=directory/'run.py'
    script.write_text('from pathlib import Path\n'
        'for i,path in enumerate('+repr(artifacts)+'): Path(path).write_bytes(("retained native model "+str(i)).encode())\n'
        'Path("artifacts/result.json").write_text('+repr(json.dumps(original))+')\n')
    reply=experiments.dispatch(config,script,mechanism='P',command_options=dict(experiment_directory=str(directory)))
    assert reply['status']=='known_success'
    locator=lambda i:dict(call_id=reply['call_id'],artifact=artifacts[i])
    value=dict(representatives=[dict(mechanism_ref=f'h001.m{i+1}',candidate=locator(i),
        reason=f'Model {i} scientifically represents mechanism {i+1}, despite sharing one execution call.') for i in range(5)],
        extras=[dict(candidate=locator(i),reason='Additional retained scientific alternative.') for i in (5,6)],gaps=[])
    original_intent=(execution.origin(root,2)/'native-intent.json').read_bytes()
    _,_,record=finish(root,2,'W',[1],candidate_nominations=value,mechanism_findings=findings('P',blocked=('Q','R','S','T')),
        preserve_native_intent=True)
    assert (record/'native-intent.json').read_bytes()==original_intent
    raw=(record/'science-output.json').read_bytes();seal=c.read(record/'result-identity.json')
    observed=c.read(root/seal['observations']['path'])
    assert len(observed['candidates'])==32 and len(observed['managed_calls'])==1
    judge=c.build_job(root,3,'A',[2]);work=c.workspace(root,3);view=c.science_view(judge,root)['lineage']
    assert len(judge['history']['candidate_ids'])==32 and len(judge['history']['candidate_nomination_ids'])==7
    assert not view['candidates']['candidate_facts']
    rows=[];page=view['indexes']['nominations']
    while page:
        part=c.read(work/page);rows.extend(part['nominations']);page=part['next_page']
    assert len(rows)==7 and all(row['candidate_id'].startswith('W002-C001/artifacts/') for row in rows)
    pool=[];page=view['indexes']['candidates']
    while page:
        part=c.read(work/page);pool.extend(part['candidates']);page=part['next_page']
    chosen=next(row for row in pool if row['label']=='Native model 31')
    assert chosen['id'] not in {row['candidate_id'] for row in rows}
    finish(root,3,'A',[2],recommendation=chosen['id'],action='wait')
    assert c.validate(work)['candidate_recommendation']==observed['candidates'][31]['id']
    resolved=generic_results.sources(root,observed,observed['candidates'][31]['id'])
    assert resolved['candidate_id']==observed['candidates'][31]['id']
    monkeypatch.setattr(delivery,'_adapter',lambda root,job=None:{})
    catalogue=delivery.catalogue_sources(root,judge['history']['nodes'])
    assert len(catalogue['items'])==32
    compact=delivery.public_sources(catalogue,nominated_ids=judge['history']['candidate_nomination_ids'])
    assert len(compact['items'])==7 and compact['omitted_count']==25
    assert (record/'science-output.json').read_bytes()==raw


def test_latest_preview_preserves_waiver_reasons_and_pending_new_mechanisms():
    value=declaration();records=[dict(step=2,role='W',candidate_nominations=value)]
    state=dict(mechanisms={f'h001.m{i}':dict(status='deferred') for i in range(1,7)})
    state['mechanisms']['h001.m5']=dict(status='waived',disposition=dict(reason='Explicit scientific infeasibility under the original constraints.',source_a=3))
    projected=nominations.project(records,state)
    assert projected['pending_mechanisms']==['h001.m6'] and projected['waived'][0]['source_a']==3
    assert projected['representatives']==value['representatives']
    projected['representatives'][0]['reason']='Changed disposable preview'
    assert records[0]['candidate_nominations']==value


def test_typed_reference_projection_preserves_original_scientific_prose():
    value=dict(candidate_nominations=declaration(),scientific_result='model-1 is a scientific label')
    public=candidate_refs.public_science_fields(value,{'model-1':'W002-C001/artifacts/model.bin'})
    assert public['candidate_nominations']['representatives'][0]['candidate']=='W002-C001/artifacts/model.bin'
    assert public['scientific_result']==value['scientific_result'] and value['candidate_nominations']==declaration()


def test_long_reasons_preserve_every_representative_and_link_complete_conclusions():
    value=dict(source_step=2,**declaration(),pending_mechanisms=[],waived=[])
    value['representatives'][0]['reason']='Scientific condition '*10000
    rows=nominations.page_rows(value)
    assert len(rows)==7 and rows[0]['candidate_id']=='model-1'
    assert rows[0]['reason_truncated'] and rows[0]['original']=='inputs/history/nodes/002/0001.json'
    assert len(value['representatives'][0]['reason'])>100000


def test_plain_task_has_no_synthetic_mechanism_requirement(monkeypatch):
    monkeypatch.setattr(history,'managed_calls',lambda *args:pytest.fail('Plain task must not create a mechanism pool'))
    nominations.require(None,dict(role='W'),{})
    fields=c.response_fields(dict(role='W'),{})
    assert 'candidate_nominations' not in fields


@pytest.mark.parametrize('attempt',[0,1])
def test_same_node_interface_identity_does_not_read_execution_results(tmp_path,execution_registry,monkeypatch,attempt):
    root=prepare(tmp_path);h(root,labels=('P',))
    _,config=accepted_worker(root,tmp_path);origin=execution.origin(root,2)
    later=origin/'continuations/001';later.mkdir(parents=True)
    c.write(later/'native-intent.json',dict(attempt=1,task_ref=c.record_ref(root/'jobs/002.json'),
        root_intent_ref=c.record_ref(origin/'native-intent.json'),
        previous={'native-intent.json':c.record_ref(origin/'native-intent.json')}))
    c.write(later/'experiment-config.json',c.read(origin/'experiment-config.json'))
    for directory in (origin,later):
        for name in ('native-outcome.json','science-output.json','usage.json'):
            (directory/name).write_text('Intentionally invalid execution result; identity must not read it.')
    accepted=dict(native_attempt=dict(number=attempt),scope=config['scope'],interface=config['execution_interface'])
    def forbidden(*args,**kwargs):pytest.fail('Host interface identity traversed same-node execution results')
    with monkeypatch.context() as pure:
        for name in ('record','records','_terminal'):pure.setattr(execution,name,forbidden)
        for name in ('read','digest'):
            original=getattr(c,name)
            def guarded(path,*args,_original=original,**kwargs):
                if Path(path).name in {'native-outcome.json','science-output.json','usage.json','result-identity.json'}:forbidden()
                return _original(path,*args,**kwargs)
            pure.setattr(c,name,guarded)
        assert history._execution_interface(root,accepted)==config['execution_interface']
        wrong=dict(accepted,interface={})
        with pytest.raises(history.ObserverIntegrityError,match='differs from frozen'):
            history._execution_interface(root,wrong)
        wrong=dict(accepted,scope=dict(config['scope'],role='A'))
        with pytest.raises(history.ObserverIntegrityError,match='differs from frozen'):
            history._execution_interface(root,wrong)
    damaged=c.read(later/'native-intent.json');damaged['root_intent_ref']='0'*64
    c.write(later/'native-intent.json',damaged)
    with pytest.raises(ValueError,match='continuation identity changed'):
        history._execution_interface(root,accepted)


@pytest.mark.parametrize('mode',['improve','repair','validate'])
def test_local_worker_nominates_only_its_assigned_mechanism_and_keeps_other_gaps(tmp_path,monkeypatch,remote_registry,mode):
    root=prepare(tmp_path);h(root,labels=('P','Q'))
    screen=c.build_job(root,2,'W',[1])
    assert {row['mechanism_ref'] for row in screen['candidate_nomination_mechanisms']}=={'h001.m1','h001.m2'}
    incomplete=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref='h001.m1',reason='No retained model')])
    with pytest.raises(nominations.OutputRepairRequired,match='cover every assigned'):
        nominations.require(root,screen,dict(candidate_nominations=incomplete))
    if mode=='repair':failed_worker_observation(root,tmp_path)
    finish(root,2,'W',[1],mechanism_findings=findings('P',blocked=('Q',)),preserve_native_intent=mode=='repair')
    a(root,3,[1,2],focus='h001.m1',mode=mode)
    local=c.build_job(root,4,'W',[1,2,3])
    assert local['search']['focus']==dict(mechanism_ref='h001.m1',action=mode)
    if mode=='repair':assert local['search']['repair_binding']=='available'
    assert [row['mechanism_ref'] for row in local['candidate_nomination_mechanisms']]==['h001.m1']
    monkeypatch.setattr(history,'managed_calls',lambda *args:[])
    with pytest.raises(nominations.OutputRepairRequired) as missing:
        nominations.require(root,local,dict(candidate_nominations=dict(representatives=[],extras=[],gaps=[])))
    assert missing.value.code=='candidate_nominations_incomplete'
    assert nominations.SCOPE_GUIDANCE in missing.value.diagnostic
    assert 'do not rerun or resubmit experiments' in missing.value.diagnostic
    value=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref='h001.m1',reason='Local observation remains missing')])
    nominations.require(root,local,dict(candidate_nominations=value))
    value['gaps'].append(dict(mechanism_ref='h001.m2',reason='This is outside the assigned task'))
    with pytest.raises(ValueError,match='assigned'):
        nominations.require(root,local,dict(candidate_nominations=value))


def test_local_preview_retains_each_other_mechanism_original_and_gap():
    global_value=dict(representatives=[dict(mechanism_ref='p',candidate='old-p',reason='Original p')],extras=[],
        gaps=[dict(mechanism_ref='q',reason='Original missing q')])
    local=dict(representatives=[dict(mechanism_ref='p',candidate='new-p',reason='Local p revision')],extras=[],gaps=[])
    records=[dict(step=2,role='W',candidate_nominations=global_value),dict(step=4,role='W',candidate_nominations=local)]
    state=dict(mechanisms={ref:dict(status='pending') for ref in ('p','q','r')})
    value=nominations.project(records,state)
    assert value['representatives']==local['representatives'] and value['gaps']==global_value['gaps']
    assert value['pending_mechanisms']==['r']
    rows=nominations.page_rows(value)
    assert rows[0]['source_step']==4 and rows[0]['original']=='inputs/history/nodes/004/0001.json'
    assert rows[1]['source_step']==2 and rows[1]['original']=='inputs/history/nodes/002/0001.json'
    assert records[0]['candidate_nominations']==global_value and records[1]['candidate_nominations']==local
