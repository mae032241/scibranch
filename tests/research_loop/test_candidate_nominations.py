"""Scientific nomination previews never shrink the exact retained model pool."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import (candidate_nominations as nominations,
    candidate_refs, cli, contracts as c, delivery, execution, experiments,
    generic_results, history, budget, search)
from execution_fixtures import execution_registry
from test_breadth_science import prepare, h, findings, run as run_observation
from test_history_handoff import finish


def accepted_worker(root,tmp_path):
    """Mirror native acceptance before configuring the test's real interface."""
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    node_anchor=budget.accept_node(root,job)
    anchor=dict(budget_slice=node_anchor) if node_anchor is not None else dict(search_slice=search.accept(root,job))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),**anchor))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root,2,record,c.read(record/'native-intent.json'))
    selected=c.settings(root,job).get('execution_config') or dict(executor='local_execution:command',configuration=dict(run_argv=[sys.executable],max_seconds=30))
    config=experiments.configure(root,2,selected['executor'],selected['configuration'],c.workspace(root,2)/'work/experiment-config.json')
    return job,config


def failed_worker_observation(root,tmp_path):
    """Capture a genuine nonzero Local exit through the original host owner."""
    _,config=accepted_worker(root,tmp_path)
    reply=run_observation(root,config,'P',failed=True)
    assert reply['status']=='known_failed'


def declaration():
    return dict(representatives=[dict(mechanism_ref=f'h001.m{i}',candidate=f'model-{i}',
        reason=f'This retained model represents the declared scientific approach {i}.') for i in range(1,6)],
        extras=[dict(candidate=f'model-{i}',reason='An alternative with distinct scientific limitations.') for i in (6,7)],gaps=[])


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
def test_thirty_two_models_one_call_optional_preview_keeps_unlisted_original(tmp_path,execution_registry,monkeypatch):
    from test_lineage_candidate_pages import _local_case, _run_candidates, _candidates
    root,_,config,server,launched=_local_case(tmp_path,monkeypatch)
    call_id=_run_candidates(root,server,count=32)
    assert launched==[call_id]
    locator=lambda i:dict(call_id=call_id,artifact=f'artifacts/candidate-{i:02d}.txt')
    value=dict(representatives=[dict(mechanism_ref='w002.m1',candidate=locator(0),
        reason='Model zero represents the authored question.')],
        extras=[dict(candidate=locator(i),reason='Additional retained scientific alternative.') for i in (1,2)],gaps=[])
    original_intent=(execution.origin(root,2)/'native-intent.json').read_bytes()
    _,_,record=finish(root,2,'W',[1],candidate_nominations=value,
        preserve_native_intent=True)
    assert (record/'native-intent.json').read_bytes()==original_intent
    raw=(record/'science-output.json').read_bytes();seal=c.read(record/'result-identity.json')
    observed=c.read(root/seal['observations']['path'])
    assert len(observed['candidates'])==32 and len(observed['managed_calls'])==1
    judge=c.build_job(root,3,'A',[2])
    retained=nominations.project([dict(step=2,role='W',candidate_nominations=c.read(record/'result.json')['candidate_nominations'])],None)
    rows=nominations.page_rows(retained)
    assert len(rows)==3
    _,accepted=_candidates(root,judge)
    pool=[dict(row['observation'],id=row['public_reference']) for row in accepted]
    assert len(pool)==32
    chosen=next(row for row in pool if row['label']=='retained-31')
    assert chosen['id'] not in {row['candidate_id'] for row in rows}
    assert accepted[-1]['observation']['id']==observed['candidates'][31]['id']
    resolved=generic_results.sources(root,observed,observed['candidates'][31]['id'])
    assert resolved['candidate_id']==observed['candidates'][31]['id']
    monkeypatch.setattr(delivery,'_adapter',lambda root,job=None:{})
    catalogue=delivery.catalogue_sources(root,[dict(step=2,seal_ref=c.record_ref(record/'result-identity.json'))])
    assert len(catalogue['items'])==32
    compact=delivery.public_sources(catalogue,nominated_ids=[row['candidate_id'] for row in rows])
    assert len(compact['items'])==3 and compact['omitted_count']==29
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
    nominations.require(None,dict(role='W'),{},inventory=None)
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
    # Accept the continuation through its real owner before result files exist.
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_terminal(root,2,origin,meter=None,unknown=True)
    accepted_budget.accept_native(root,2,later,c.read(later/'native-intent.json'))
    chain=execution.records(root,2)
    assert chain==[origin,later]
    for directory in (origin,later):
        for name in ('native-outcome.json','science-output.json','usage.json'):
            (directory/name).write_text('Intentionally invalid execution result; identity must not read it.')
    accepted=dict(native_attempt=dict(number=attempt),scope=config['scope'],interface=config['execution_interface'])
    def forbidden(*args,**kwargs):pytest.fail('Host interface identity traversed same-node execution results')
    with monkeypatch.context() as pure:
        for name in ('record','records','_terminal'):pure.setattr(execution,name,forbidden)
        for name in ('read',):
            original=getattr(c,name)
            def guarded(path,*args,_original=original,**kwargs):
                if Path(path).name in {'native-outcome.json','science-output.json','usage.json','result-identity.json'}:forbidden()
                return _original(path,*args,**kwargs)
            pure.setattr(c,name,guarded)
        assert history._execution_interface_from_chain(root,accepted,chain)==config['execution_interface']
        wrong=dict(accepted,interface={})
        with pytest.raises(history.ObserverIntegrityError,match='differs from frozen'):
            history._execution_interface_from_chain(root,wrong,chain)
        wrong=dict(accepted,scope=dict(config['scope'],role='A'))
        with pytest.raises(history.ObserverIntegrityError,match='differs from frozen'):
            history._execution_interface_from_chain(root,wrong,chain)
    damaged=c.read(later/'native-intent.json');damaged['root_intent_ref']='0'*64
    c.write(later/'native-intent.json',damaged)
    with pytest.raises(ValueError,match='continuation identity changed'):
        history._execution_interface(root,accepted)


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


@pytest.mark.parametrize('declaration',[{}, {'representatives':'optional prose'}, {'gaps':[{},None,{'mechanism_ref':'p'}]},
    {'extras':[{'candidate':{'unexpected':'value'}}]}])
def test_optional_sparse_preview_never_requires_nested_output_repair(declaration):
    records=[dict(step=2,role='W',candidate_nominations=declaration)]
    state=dict(mechanisms={'p':dict(status='pending')})
    value=nominations.project(records,state)
    assert nominations.page_rows(value) is not None
    assert nominations.preview(value,'index.json')['distinct_candidate_count']==0
