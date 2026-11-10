"""Pure live declaration admission; no backend, model or solver execution."""
import copy
from contextlib import nullcontext
from pathlib import Path

import pytest

from scidiscovery.research_loop import breadth, protocol, worker_research as research, supervision
from current_strategy_fixtures import budget_facts, current_policy


def config(tmp_path, step=1):
    return dict(research_strategy_version=3,worker_research_goal_id='fixture',record_dir=str(tmp_path/'records'),breadth=None,
        scope=dict(step=step,role='W',job_ref=dict(path=str(tmp_path/'jobs'/f'{step:03d}.json')),
            execution_key=dict(path=str(tmp_path/'native-evidence'/f'{step:03d}'/'native-intent.json')),
            mechanism_ref=None,handoff_protocol=protocol.CURRENT))


def batch(ref='w001.m1', *, action='declare', question='Which retained input can distinguish this explanation?', **extra):
    return dict(declarations=[dict(mechanism_ref=ref,action=action,question=question,**extra)],
        mechanism_ref=ref,experiment_question=dict(question='Does the selected input contain this observable?',
            change_criterion='Choose a controlled comparison when the observable exists; retain missing input otherwise.'))


def test_first_w_explicit_unknown_exploration_can_bind_before_sealing(tmp_path):
    selected=config(tmp_path);initial=research.initial(selected)
    updated,binding=research.prepare(selected,initial,batch(exploration=True))
    assert initial['revision']==0 and updated['revision']==1
    assert binding['research_binding']['exploration'] is True
    assert binding['research_binding']['question']
    assert not (tmp_path/'records').exists()  # Pure request preparation does not publish.
    assert research.check_binding(binding,selected['scope'])==binding


def test_first_w_missing_optional_research_and_live_views_are_explicit_initial_facts(tmp_path):
    selected=config(tmp_path)
    assert research.load(selected)==research.initial(selected)
    assert research.load_investment(selected) is None
    assert not (tmp_path/'records').exists()


def test_only_explicit_empty_bounded_combination_denies_new_execution_without_catalog_reads(monkeypatch):
    empty=dict(version=3,initial_calls=1,mechanisms=[],baseline=[],grant=None,prior_unknown=False,
        outside_initial_owed=0,catalog=dict(prefix=dict(version=1,cutoff=2),max_ordinal=0),restricted_combination=True)
    assert breadth.validate(empty)==empty
    monkeypatch.setattr(breadth,'_catalog_root',lambda *args:pytest.fail('Readonly correction must refuse before live catalog acquisition'))
    for choice in (None,'1','w001.new'):
        with pytest.raises(breadth.OpportunityRequired,match='retained originals only'):
            breadth.admit(empty,choice,{},[])
    legacy=dict(empty);legacy.pop('restricted_combination')
    with pytest.raises(ValueError):breadth.validate(legacy)


@pytest.mark.parametrize('name',['research.json','live-status.json'])
@pytest.mark.parametrize('kind',['dangling','directory'])
def test_missing_optional_view_does_not_hide_dangling_or_nonregular_original(tmp_path,name,kind):
    selected=config(tmp_path);directory=Path(selected['record_dir']);directory.mkdir()
    path=directory/name
    if kind=='dangling':path.symlink_to(directory/'absent-target')
    else:path.mkdir()
    loader=research.load if name=='research.json' else research.load_investment
    with pytest.raises(ValueError):loader(selected)


@pytest.mark.parametrize('batch_request', [None,{},dict(mechanism_ref='w001.absent',experiment_question=dict(question='Q',change_criterion='C')),
    batch('w002.m1'),batch('h001.m1'),batch(question='  '),batch(exploration='unknown')])
def test_live_admission_requires_scoped_declared_identity_and_batch(tmp_path,batch_request):
    selected=config(tmp_path)
    with pytest.raises(breadth.OpportunityRequired):research.prepare(selected,research.initial(selected),batch_request)


def test_explicit_revision_preserves_old_call_binding(tmp_path):
    selected=config(tmp_path)
    initial=research.initial(selected)
    first,binding=research.prepare(selected,initial,batch())
    original=copy.deepcopy(binding)
    revised,new_binding=research.prepare(selected,first,batch(action='revise',question='Can the retained control distinguish an implementation failure?'))
    assert binding==original and binding['research_binding']['revision']==1
    assert new_binding['research_binding']['revision']==2
    assert binding['mechanism_ref']==new_binding['mechanism_ref']
    assert binding['research_binding']['origin_ref']==new_binding['research_binding']['origin_ref']
    assert research.check_binding(binding,selected['scope'])==original
    assert revised['mechanisms']['w001.m1']['question']!=first['mechanisms']['w001.m1']['question']


def test_rename_requires_explicit_revision_and_retired_requires_resume(tmp_path):
    selected=config(tmp_path);state,_=research.prepare(selected,research.initial(selected),batch())
    with pytest.raises(breadth.OpportunityRequired):research.prepare(selected,state,batch(question='Changed display'))
    source=dict(path=str(tmp_path/'retire.json'))
    retired=research.evolve(state,[dict(mechanism_ref='w001.m1',action='retire')],scope=selected['scope'],original_ref=source)
    with pytest.raises(breadth.OpportunityRequired):research.prepare(selected,retired,dict(mechanism_ref='w001.m1',experiment_question=dict(question='Q',change_criterion='C')))
    resumed=research.evolve(retired,[dict(mechanism_ref='w001.m1',action='resume')],scope=selected['scope'],original_ref=source)
    assert resumed['mechanisms']['w001.m1']['status']=='active'
    assert resumed['mechanisms']['w001.m1']['origin_ref']==state['mechanisms']['w001.m1']['origin_ref']


def test_original_investment_deduplicates_query_recovery_and_continuation():
    selected=dict(supervision.DEFAULT_POLICY,mode='review',max_automatic_reviews=2,consecutive_calls=2)
    state=supervision.empty(selected)
    fact=dict(original_call_id='original-job:call-1',mechanism_ref='w001.m1',predecessors=[],completed_receipt=True)
    state=supervision.add_investment(state,fact)
    for _ in range(3):state=supervision.add_investment(state,fact)
    assert state['investment']['total_calls']==1 and not supervision.due(state)['due']
    state=supervision.add_investment(state,dict(fact,original_call_id='original-job:call-2'))
    assert state['investment']['total_calls']==2
    assert supervision.due(state,budget_facts=budget_facts(.2))['automatic_allowed']
    assert state['total_rounds']==0  # Receipts never manufacture scientific rounds.
    with pytest.raises(ValueError,match='changed'):supervision.add_investment(state,dict(fact,mechanism_ref='w002.renamed'))




def test_cross_w_seed_preserves_retired_identity_revision_and_explicit_resume(tmp_path):
    selected=config(tmp_path)
    state,binding=research.prepare(selected,research.initial(selected),batch(exploration=True))
    state=research.evolve(state,[dict(action='retire',mechanism_ref='w001.m1')],
        scope=selected['scope'],original_ref=dict(path=str(tmp_path/'retirement.json')))
    next_config=config(tmp_path,step=4)
    next_config['worker_research_seed']=list(state['mechanisms'].values())
    continued=research.initial(next_config)
    retained=continued['mechanisms']['w001.m1']
    assert retained['status']=='retired' and retained['exploration'] is True and retained['revision']==2
    resumed=research.evolve(continued,[dict(action='resume',mechanism_ref='w001.m1')],
        scope=next_config['scope'],original_ref=dict(path=str(tmp_path/'resume.json')))
    assert resumed['mechanisms']['w001.m1']['origin_ref']==binding['research_binding']['origin_ref']
    assert resumed['mechanisms']['w001.m1']['revision']==3


def test_merge_investment_is_original_union_even_when_parents_share_ancestry():
    state=supervision.empty(supervision.DEFAULT_POLICY)
    state=supervision.add_investment(state,dict(original_call_id='original:1',mechanism_ref='w001.root'))
    state=supervision.declare_investment(state,dict(mechanism_ref='w001.left',predecessors=['w001.root']))
    state=supervision.declare_investment(state,dict(mechanism_ref='w001.right',predecessors=['w001.root']))
    state=supervision.declare_investment(state,dict(mechanism_ref='w002.merged',predecessors=['w001.left','w001.right']))
    assert supervision.investment_counts(state)['w002.merged']==1
    assert state['investment']['total_calls']==1


def test_live_investment_uses_exact_scope_and_does_not_recount_sealed_originals(tmp_path):
    selected=config(tmp_path);scope=selected['scope'];state=supervision.empty(supervision.DEFAULT_POLICY)
    _,binding=research.prepare(selected,research.initial(selected),batch())
    accepted=dict(call_id='call-1',scope=scope,mechanism_binding=binding)
    live=research.investment(scope,[accepted,accepted])
    assert live['total_calls']==1 and live['consecutive_calls']==1
    merged=supervision.with_live_investment(state,live,scope=scope)
    assert merged['investment']['total_calls']==1 and supervision.investment_counts(merged)=={'w001.m1':1}
    sealed=supervision.add_investment(state,research.call_fact(accepted,scope))
    assert supervision.with_live_investment(sealed,live,scope=scope)['investment']['total_calls']==1
    with pytest.raises(ValueError,match='Exact current task'):
        supervision.with_live_investment(state,live,scope=dict(scope,step=2))


def test_declaration_pointer_cannot_rebind_a_saved_call(tmp_path):
    selected=config(tmp_path);state=research.initial(selected)
    first,binding=research.prepare(selected,state,batch())
    research.publish(selected,state,first)
    assert research.load(selected)==first
    revised,_=research.prepare(selected,first,batch(action='revise',question='A revised explicit question'))
    research.publish(selected,first,revised)
    assert research.load(selected)==revised
    assert research.check_binding(binding,selected['scope'])['research_binding']['question']!=revised['mechanisms']['w001.m1']['question']


def test_unpublished_accepted_preparation_uses_saved_v3_binding_without_current_state(tmp_path,monkeypatch):
    from scidiscovery.research_loop import preparation_recovery as recovery
    selected=config(tmp_path);_,binding=research.prepare(selected,research.initial(selected),batch())
    prepared=dict(name='original-call',local_path=str(tmp_path/'source.py'),size_bytes=4)
    destination=Path(selected['record_dir'])/'calls'/'original-call'
    accepted=dict(call_id='original-call',scope=selected['scope'],native_attempt={'number':0},
        interface={'version':1},prepared=prepared,source_script=prepared['local_path'],
        script=dict(path=str(destination/'source.py'),size_bytes=4),snapshot_files=[],
        parent=dict(candidate_id=None),mechanism_binding=binding)
    retained=dict(records=selected['record_dir'],call_id='original-call',prepared=prepared,
        scope=selected['scope'],native_attempt=accepted['native_attempt'],interface=accepted['interface'],
        acceptance_base=recovery._acceptance_base(accepted),input_inventory=[])
    monkeypatch.setattr(recovery,'_json',lambda *args:copy.deepcopy(accepted))
    monkeypatch.setattr(recovery,'_local_unbound_acceptance',lambda *args:True)
    monkeypatch.setattr(recovery,'_direct_input',lambda *args:False)
    monkeypatch.setattr(recovery,'_tree',lambda *args,**kwargs:None)
    monkeypatch.setattr(research,'load',lambda *args:pytest.fail('Recovery must not reopen current declarations'))
    monkeypatch.setattr(breadth,'check_binding',lambda *args,**kwargs:pytest.fail('v3 recovery must not use legacy frozen breadth'))
    actual=recovery._accepted(tmp_path/'unpublished',retained,selected,dict(path='original-snapshot'))
    assert actual['mechanism_binding']==binding
    altered=copy.deepcopy(accepted);altered['mechanism_binding']['source_step']=2
    monkeypatch.setattr(recovery,'_json',lambda *args:copy.deepcopy(altered))
    with pytest.raises(ValueError):recovery._accepted(tmp_path/'unpublished',retained,selected,dict(path='original-snapshot'))




def test_authority_allocation_retains_full_preflight_identity_before_preparation(tmp_path,monkeypatch):
    from scidiscovery.research_loop import execution_authority as authority, accepted_budget
    selected=config(tmp_path);selected.update(native_attempt={'number':0},execution_interface={'version':1})
    _,binding=research.prepare(selected,research.initial(selected),batch())
    subject=dict(id='exact-reviewed-subject');review=dict(path='exact-review');alias=dict(path='exact-payload',size_bytes=1)
    body=dict(scope=selected['scope'],native_attempt=selected['native_attempt'],interface=selected['execution_interface'],
        subject=subject,review=review,payload_alias=alias,ordinal=1,mechanism_binding=binding)
    allocation=dict(binding=body)
    class ExistingAllocation:
        def get(self,call):return dict(allocation={'path':'original-allocation'})
    monkeypatch.setattr(authority,'_host_context',lambda config:(tmp_path,dict(step=1),{'task_ref':'original-intent'}))
    monkeypatch.setattr(authority,'_selected_subject',lambda *args:(subject,review,alias))
    monkeypatch.setattr(authority,'writer',lambda *args:nullcontext())
    monkeypatch.setattr(authority,'_remaining',lambda *args:({'boot_id':'fixture'},60))
    monkeypatch.setattr(accepted_budget,'read',lambda *args:{})
    monkeypatch.setattr(accepted_budget,'authority_calls',lambda *args:ExistingAllocation())
    monkeypatch.setattr(accepted_budget,'authority_original',lambda *args:allocation)
    published=[]
    monkeypatch.setattr(accepted_budget,'accept_authority',lambda *args,**kwargs:published.append(kwargs))
    monkeypatch.setattr(breadth,'admit',lambda *args,**kwargs:pytest.fail('v3 exact allocation must not resolve a legacy catalog'))
    monkeypatch.setattr(research,'load',lambda *args:pytest.fail('An allocation consumer must not reopen current declarations'))
    call=authority._allocate_call(selected,object(),1,binding)
    assert call.startswith('0001-') and published[0]['value']['binding']['mechanism_binding']==binding
    with pytest.raises(ValueError):authority._allocate_call(selected,object(),1,binding['mechanism_ref'])


def test_unprepared_recovery_admits_only_the_frozen_allocation_binding(tmp_path,monkeypatch):
    from scidiscovery.research_loop import experiments, execution_resume
    selected=config(tmp_path);prepared_state,binding=research.prepare(selected,research.initial(selected),batch())
    operation=object.__new__(execution_resume._RecoveryAdmission)
    operation.config=selected;operation.allocation=dict(binding=dict(mechanism_binding=binding));operation.started=False
    operation.research_original=dict(reference=binding['source_ref'],runtime=True,value=prepared_state)
    monkeypatch.setattr(research,'load',lambda *args:pytest.fail('Original recovery cannot reopen mutable research'))
    monkeypatch.setattr(breadth,'admit',lambda *args,**kwargs:pytest.fail('v3 recovery cannot use latest catalog admission'))
    operation.opportunity()  # This guard runs before an external prepare.
    records=experiments.Records(selected)
    monkeypatch.setattr(records,'calls',lambda:[])
    checked=[]
    actual=records.admission_binding('an-unrelated-current-display',expected_calls=[],
        recovery_admission=lambda:checked.append(True),recovery_binding=binding)
    assert checked==[True] and actual==binding
    bad=copy.deepcopy(binding);bad['job_ref']=dict(path='another-original')
    with pytest.raises(ValueError):records.admission_binding(None,expected_calls=[],
        recovery_admission=lambda:None,recovery_binding=bad)


def test_real_preflight_retains_declaration_before_an_unprepared_authority_stop(tmp_path,monkeypatch):
    from scidiscovery.research_loop import experiments, execution_resume
    selected=config(tmp_path);selected['max_seconds']=60
    records=experiments.Records(selected)
    monkeypatch.setattr(records,'calls',lambda:[])
    monkeypatch.setattr(records,'admission_lease',lambda **kwargs:nullcontext())
    monkeypatch.setattr(experiments,'review_status',lambda config:dict(requested=False,enforced=False))
    monkeypatch.setattr(experiments,'_require_new_execution',lambda config:None)
    binding=experiments.Records.preflight.__wrapped__(records,expected_calls=[],mechanism=batch())
    declaration=Path(binding['source_ref']['path'])
    assert declaration.is_file() and research.load(selected)['mechanisms']['w001.m1']['revision']==1
    original=declaration.read_bytes()
    operation=object.__new__(execution_resume._RecoveryAdmission)
    operation.config=selected;operation.allocation=dict(binding=dict(mechanism_binding=binding));operation.started=False
    operation.research_original=research.load_original(binding,selected['scope'])
    # A broken/missing current pointer cannot rewrite the frozen declaration.
    (Path(selected['record_dir'])/'research.json').unlink()
    monkeypatch.setattr(research,'load',lambda *args:pytest.fail('Recovery must consume its retained original'))
    operation.opportunity()
    assert declaration.read_bytes()==original and records.calls()==[]


def test_recovery_missing_declaration_original_is_not_rebuilt_from_a_pointer(tmp_path):
    selected=config(tmp_path);state,binding=research.prepare(selected,research.initial(selected),batch())
    with pytest.raises(ValueError):research.load_original(binding,selected['scope'])
    assert not Path(binding['source_ref']['path']).exists()
    research.publish(selected,research.initial(selected),state)
    fact=research.load_original(binding,selected['scope'])
    assert research.check_original(binding,selected['scope'],fact)==fact
    wrong=copy.deepcopy(fact);wrong['value']['goal_id']='another-source-owner'
    with pytest.raises(ValueError,match='source owner'):research.check_original(binding,selected['scope'],wrong)
    wrong=copy.deepcopy(fact);wrong['value']['mechanisms']['w001.m1']['question']='Changed source statement'
    with pytest.raises(ValueError,match='frozen revision'):research.check_original(binding,selected['scope'],wrong)


def test_multilevel_no_call_merge_preserves_consecutive_original_investment(tmp_path):
    selected=config(tmp_path);state,binding=research.prepare(selected,research.initial(selected),batch())
    accepted=[dict(call_id=f'call-{number}',scope=selected['scope'],mechanism_binding=copy.deepcopy(binding)) for number in range(3)]
    source=dict(path=str(tmp_path/'declared.json'))
    state=research.evolve(state,[dict(action='merge',mechanism_ref='w001.m2',question='Merged question 2',predecessors=['w001.m1'])],
        scope=selected['scope'],original_ref=source)
    state=research.evolve(state,[dict(action='merge',mechanism_ref='w001.m3',question='Merged question 3',predecessors=['w001.m2'])],
        scope=selected['scope'],original_ref=source)
    state,last=research.prepare(selected,state,dict(mechanism_ref='w001.m3',experiment_question=dict(question='Q',change_criterion='C')))
    accepted.append(dict(call_id='call-3',scope=selected['scope'],mechanism_binding=last))
    live=research.investment(selected['scope'],accepted,research_state=state)
    assert live['total_calls']==4 and live['consecutive_calls']==4
    assert set(live['predecessors'])=={'w001.m1','w001.m2'}
    cumulative=supervision.empty(dict(supervision.DEFAULT_POLICY,mode='review',max_automatic_reviews=2))
    for row in state['mechanisms'].values():cumulative=supervision.declare_investment(cumulative,row)
    for row in accepted:cumulative=supervision.add_investment(cumulative,research.call_fact(row,selected['scope']))
    assert cumulative['investment']['consecutive_calls']==4
    assert not supervision.due(cumulative)['automatic_allowed']  # Missing consumption is not authorization.
    previous=supervision.empty(supervision.DEFAULT_POLICY)
    for row in state['mechanisms'].values():previous=supervision.declare_investment(previous,row)
    for row in accepted[:3]:previous=supervision.add_investment(previous,research.call_fact(row,selected['scope']))
    assert supervision.with_live_investment(previous,live,scope=selected['scope'])['investment']['consecutive_calls']==4


def test_identical_merge_retires_a_resumed_predecessor_again(tmp_path):
    selected=config(tmp_path);state,_=research.prepare(selected,research.initial(selected),batch())
    source=dict(path=str(tmp_path/'declared.json'))
    merge=dict(action='merge',mechanism_ref='w001.merged',question='Same merged question',predecessors=['w001.m1'])
    state=research.evolve(state,[merge],scope=selected['scope'],original_ref=source)
    state=research.evolve(state,[dict(action='resume',mechanism_ref='w001.m1')],scope=selected['scope'],original_ref=source)
    assert state['mechanisms']['w001.m1']['status']=='active'
    merged=research.evolve(state,[merge],scope=selected['scope'],original_ref=source)
    assert merged['mechanisms']['w001.m1']['status']=='retired' and merged['revision']==state['revision']+1
    assert research.evolve(merged,[merge],scope=selected['scope'],original_ref=source)==merged


@pytest.mark.parametrize('injected',[dict(research_strategy_version=3),dict(worker_research_seed=[]),dict(worker_research_goal_id='forged')])
def test_operator_configuration_cannot_inject_worker_first_control_facts(monkeypatch,injected):
    from types import SimpleNamespace
    from scidiscovery.research_loop import experiments
    from scidiscovery.plugin_configuration import PluginConfigurationError
    registration=SimpleNamespace(plugin_id='fixture',factory=SimpleNamespace(requires_config_file=False,configure=None))
    monkeypatch.setattr(experiments,'registration',lambda executor:(registration,'command'))
    with pytest.raises(PluginConfigurationError):
        experiments.operator_configuration(dict(executor='fixture:command',configuration=injected))
