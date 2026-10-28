"""Exact startup reconciliation permits one first model submission, never replay."""
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest

from scidiscovery.research_loop import budget,configuration_updates,contracts as c,controller,execution,native,profiles,research_config,runtime,startup_recovery as recovery
from test_history_handoff import finish


LAUNCHER = r'''import json,os,pathlib,sys
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
assert os.environ['CODEX_HOME']==HOME
with (work/'work/submission-count').open('a') as stream:stream.write('one\n')
if UNKNOWN:raise SystemExit(7)
payload=dict(summary='Fixture final conclusion',scientific_result='Existing accepted W is retained; performance goals remain unmeasured.',
 limitations=['Deterministic transport only; no scientific qualification.'],implementation_selection=None,
 next_action=ACTION,route_reason='Select existing evidence or explain unsubmitted result.',candidate_recommendation=None,
 search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=17,cached_input_tokens=3,output_tokens=5))),flush=True)
'''


def _scene(tmp_path, *, unknown=False, action='finish', legacy=False, review=False, skill_revision=False):
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Assess exact existing W evidence.')
    home=tmp_path/'native-home';home.mkdir();(home/'config.toml').write_text('fixture=true\n')
    launcher=tmp_path/'native.py';launcher.write_text('HOME='+repr(str(home))+'\nUNKNOWN='+repr(unknown)+'\nACTION='+repr(action)+'\n'+LAUNCHER)
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[sys.executable,str(launcher)],max_tokens=100 if legacy else 10000,max_seconds=60,max_steps=12,
        model='fixture-no-model',reasoning_effort='high',
        role_profiles={'A':dict(skill_set='initial')} if skill_revision else {},search_policy=None,
        skills=dict(sets={name:dict(items=[]) for name in ('initial','selected')}) if skill_revision else None,
        formal_review_policy={'version':1} if review else None,node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=10) for role in 'HWAE'})
    if skill_revision:
        for step,role,parents,action_ in [(1,'H',[],'experiment'),(2,'W',[1],'wait')]:
            job=c.build_job(root,step,role,parents)
            c.write(execution.origin(root,step)/'native-intent.json',dict(
                task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
                **profiles.binding(configuration_updates.for_job(root,job),job)))
            finish(root,step,role,parents,action=action_,preserve_native_intent=True)
        document=research_config.to_document(root)
        document['agent']['roles']['A']['skill_set']='selected'
        configuration_updates.submit(root,document,expected_revision=0)
    else:
        finish(root,1,'H',[],action='experiment');finish(root,2,'W',[1])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','fixture original startup')
    if legacy:
        job=c.build_job(root,3,'A',[1,2])
        settings=c.settings(root);limits=budget.snapshot(root)
        anchor=dict(task_ref=c.record_ref(root/'jobs/003.json'),role='A',boot_id=budget.clock_stamp()['boot_id'],
            advance_seconds=limits['advance_seconds'],limits=budget.node_limits(root,job),
            shared_limits={key:limits[key] for key in ('max_tokens','max_seconds')})
        events,parent=budget._events(root,settings)
        claim=budget._append(root,settings,events,parent,kind='final_a_return',version=1,step=3,attempt=0,
            basis='last_authorized_node',anchor=anchor,
            selection=dict(max_steps=12,used_steps=3,accounted_tokens=20,seconds_remaining=limits['seconds_remaining'],blocked_role=None,stopped_original=None),
            reserve=budget._final_a_reserve(settings,job,events))
        anchor=dict(anchor,final_return=dict(seq=claim['seq'],record=c.record_ref(root/'control-ledger'/f'{claim["seq"]:06d}.json')))
    else:
        budget.begin_closeout(root,2,3,'fixture final',final_only=True)
        job=c.build_job(root,3,'A',[1,2],final_report=dict(source_cutoff=2,stopped_original=None))
        anchor=budget.accept_node(root,job)
    return failed_original(tmp_path,root,job,anchor,home)


def failed_original(tmp_path,root,job,anchor,home):
    from scidiscovery.research_loop import profiles
    record=execution.origin(root,3)
    intent=dict(step=3,task_ref=c.record_ref(root/'jobs/003.json'),
        **profiles.binding(configuration_updates.for_job(root,job),job),budget_slice=anchor)
    c.write(record/'native-intent.json',intent)
    identity=dict(pid=99999999,start_ticks=1,pgid=99999999,sid=99999999,ppid=1,
        boot_id=budget.clock_stamp()['boot_id'],task_ref=intent['task_ref'])
    for name in ('runner-process.json','native-process.json'):c.write(record/name,identity)
    (record/'native.jsonl').write_bytes(b'');(record/'native.stderr').write_text('Pinned fixture app bootstrap failed before provider submission.\n')
    observation=dict(coverage='unknown',has_observation=False,tokens=dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))
    c.write(record/'usage-observation.json',observation)
    outcome=dict(returncode=1,stop_reason=None,cleanup='observed_group_cleaned',thread_id=None,metering=observation,
        usage=native.usage(root),budget_slice=dict(advance_seconds=.01,usage_observed=False))
    outcome['final_a_completion']=budget.final_a_receipt(budget.final_a_barrier(root),intent,dict(input_tokens=0,cached_input_tokens=0,output_tokens=0),outcome['usage'])
    c.write(record/'native-outcome.json',outcome);budget.inactive(root,'fixture failed startup')
    old=tmp_path/'old-runtime';package=old/'lib/python3.12/site-packages/scidiscovery/research_loop';package.mkdir(parents=True,exist_ok=True)
    for name in ('formal_review.py','contracts.py','native.py','continuation.py','backends.py'):shutil.copyfile(Path(recovery.__file__).parent/name,package/name)
    originals=recovery._originals(root,3)
    report=dict(version=1,kind='native_startup_engineering_reconciliation',author='Independent fixture engineering reviewer',
        subject=dict(goal_id=c.settings(root)['goal_id'],step=3,role='A',attempt=0,task_ref=intent['task_ref'],final_return=anchor['final_return']),
        originals={name:originals['observation' if name=='usage_observation' else name] for name in
            ('settings','job','intent','outcome','stream','stderr','usage_observation')},
        engineering_judgment=dict(provider_submission='provider_not_submitted',provider_model_tokens=0,product_meter_remains_unknown=True),
        positive_source_flow_evidence=dict(sources=[dict(url='https://example.invalid/pinned-fixture-source',
            finding='Deterministic fixture source fails before its provider submission function; empty logs are not the proof.')]),
        native_implementation_attribution=dict(postcall_ref=c.record_ref(Path(sys.executable).resolve()),limitations=['Synthetic source attribution; no real provider claim.']))
    evidence=tmp_path/'reconciliation.json';c.write(evidence,report)
    options=dict(reconciliation=evidence,reconciliation_ref=c.file_ref(evidence),
        native_home=home,authorization_statement='Accept this exact independent engineering reconciliation and its limitations for one first model submission only.')
    return root,options


def scene(tmp_path, *, unknown=False, action='finish', legacy=False, review=False):
    if not review:return _scene(tmp_path,unknown=unknown,action=action,legacy=legacy)
    from scidiscovery.research_loop import formal_review
    from scidiscovery.operations import review_rules
    package=tmp_path/'old-runtime/lib/python3.12/site-packages/scidiscovery/research_loop'
    package.mkdir(parents=True)
    for name in ('formal_review.py','contracts.py','native.py','continuation.py','backends.py'):shutil.copyfile(Path(recovery.__file__).parent/name,package/name)
    rules=package.parent/'operations/review_rules.py';rules.parent.mkdir()
    shutil.copyfile(review_rules.__file__,rules)
    frozen=formal_review._contract()
    frozen['implementation']=[recovery._ref(file) for file in
        (package/'formal_review.py',package/'contracts.py',rules,package/'native.py',package/'continuation.py')]
    # This fixture originally ran with the old installation's exact contract;
    # the recovery below uses the current strict owner without any mock.
    with patch.object(formal_review,'_contract',return_value=frozen):
        return _scene(tmp_path,unknown=unknown,action=action,legacy=legacy,review=True)


def originals(root):
    record=execution.origin(root,3)
    return {file:file.read_bytes() for file in (root/'settings.json',root/'jobs/003.json',record/'native-intent.json',record/'native-outcome.json',record/'native.jsonl',record/'native.stderr')}


@pytest.mark.process_e2e
@pytest.mark.parametrize('legacy',[False,True])
def test_positive_reconciliation_runs_one_fresh_successor_and_preserves_old_unknown(tmp_path,legacy):
    root,options=scene(tmp_path,legacy=legacy);saved=originals(root);claim=budget.final_a_barrier(root)
    result=recovery.start(root,3,**options)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    assert all(file.read_bytes()==data for file,data in saved.items())
    assert not (execution.origin(root,3)/'usage.json').exists()
    assert budget.final_a_barrier(root)==claim
    assert len(execution.records(root,3))==2
    current=execution.record(root,3)
    intent=c.read(current/'native-intent.json')
    assert intent['attempt']==1 and intent['context_mode']=='new_context' and 'session' not in intent
    assert intent['budget_slice']==c.read(execution.origin(root,3)/'native-intent.json')['budget_slice']
    assert c.validate(c.workspace(root,3))['scientific_result'].startswith('Existing accepted W')
    assert native.usage(root)['unknown_steps']==[3]
    assert native.usage(root)['blocking_unknown_steps']==[]
    assert native.usage(root)['reconciled_not_submitted_attempts'][0]['attempt']==0
    assert native.usage(root)['observed_tokens']==42
    assert budget.used_steps(root)==4
    assert c.read(current/'native-outcome.json')['budget_slice']['advance_seconds']>=.01
    assert c.read(current/'native-outcome.json')['budget_slice']['dispatches']==2
    assert recovery.start(root,3,**options)['status']=='final_a_completed'
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
    assert not (execution.origin(root,3)/'continuations/002').exists()


@pytest.mark.process_e2e
def test_successor_unknown_remains_blocking_and_is_never_restarted(tmp_path):
    root,options=scene(tmp_path,unknown=True);saved=originals(root)
    first=recovery.start(root,3,**options)
    assert first['status']=='closeout_native_unsealed'
    assert native.usage(root)['unknown_steps']==[3]
    current=execution.record(root,3)
    outcome=(current/'native-outcome.json').read_bytes()
    assert recovery.start(root,3,**options)['status']=='closeout_native_unsealed'
    assert (current/'native-outcome.json').read_bytes()==outcome
    assert len(execution.records(root,3))==2
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
    assert all(file.read_bytes()==data for file,data in saved.items())
    assert not (execution.origin(root,3)/'continuations/002').exists()


def _accepted_before_spawn(tmp_path,monkeypatch):
    root,options=scene(tmp_path)
    def crash(*args,**kwargs):raise RuntimeError('fixture crash after accepted intent before spawn')
    with monkeypatch.context() as change:
        change.setattr(runtime,'start',crash)
        with pytest.raises(RuntimeError,match='accepted intent before spawn'):
            recovery.start(root,3,**options)
    current=execution.record(root,3)
    assert c.read(current/'native-intent.json')['continuation_kind']=='startup_successor'
    assert not any((current/name).exists() for name in
        ('runner-process.json','native-process.json','native-outcome.json'))
    return root,options,current


@pytest.mark.process_e2e
def test_accepted_successor_before_spawn_resumes_same_authorized_attempt(tmp_path,monkeypatch):
    root,options,current=_accepted_before_spawn(tmp_path,monkeypatch)
    saved=originals(root)
    intent=(current/'native-intent.json').read_bytes()
    permit=recovery.path(root,3).read_bytes()
    claim=budget.final_a_barrier(root)
    accepted_steps=budget.used_steps(root)
    result=recovery.start(root,3,**options)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    assert execution.record(root,3)==current
    assert (current/'native-intent.json').read_bytes()==intent
    assert recovery.path(root,3).read_bytes()==permit
    assert budget.final_a_barrier(root)==claim
    assert budget.used_steps(root)==accepted_steps
    assert all(file.read_bytes()==data for file,data in saved.items())
    assert c.read(current/'native-intent.json')['budget_slice']==c.read(execution.origin(root,3)/'native-intent.json')['budget_slice']
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
    assert recovery.start(root,3,**options)['status']=='final_a_completed'
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
    assert not (execution.origin(root,3)/'continuations/002').exists()


@pytest.mark.parametrize('receipt',['runner-process.json','native-process.json','native-outcome.json'])
def test_accepted_successor_with_any_receipt_never_restarts(tmp_path,monkeypatch,receipt):
    root,options,current=_accepted_before_spawn(tmp_path,monkeypatch)
    # Presence is enough to forbid this narrow restart. Generic collection must
    # retain responsibility for interpreting any original/unknown outcome.
    c.write(current/receipt,dict(fixture='original unknown receipt'))
    before=(current/receipt).read_bytes()
    monkeypatch.setattr(runtime,'start',lambda *args,**kwargs:pytest.fail('Existing execution receipt must prevent restart'))
    monkeypatch.setattr(controller,'_run',lambda root:dict(status='fixture collection only'))
    assert recovery.start(root,3,**options)['status']=='fixture collection only'
    assert (current/receipt).read_bytes()==before
    assert not (c.workspace(root,3)/'work/submission-count').exists()


@pytest.mark.parametrize('mutation',['no_positive_source','not_positive_judgment','wrong_original','missing_author'])
def test_empty_stream_null_thread_and_missing_meter_do_not_authorize_successor(tmp_path,mutation):
    root,options=scene(tmp_path)
    report=c.read(options['reconciliation'])
    if mutation=='no_positive_source':report['positive_source_flow_evidence']['sources']=[]
    elif mutation=='not_positive_judgment':report['engineering_judgment']['provider_submission']='unknown'
    elif mutation=='wrong_original':report['originals']['intent']['path']='/wrong/original-intent.json'
    else:report['author']=''
    c.write(options['reconciliation'],report);options['reconciliation_ref']=c.file_ref(options['reconciliation'])
    with pytest.raises(ValueError):recovery.start(root,3,**options)
    assert not recovery.path(root,3).exists()
    assert not (execution.origin(root,3)/'continuations/001/native-intent.json').exists()
    assert native.usage(root)['unknown_steps']==[3]
    assert len(execution.records(root,3))==1


def test_runtime_upgrade_preserves_original_result_but_configuration_tamper_fails(tmp_path):
    root,options=scene(tmp_path)
    result=recovery.start(root,3,**options)
    permit=recovery.validate(root,3)
    current=execution.record(root,3)
    outcome=(current/'native-outcome.json').read_bytes()
    reported=native.usage(root)['observed_tokens']
    original=tmp_path/'old-runtime/lib/python3.12/site-packages/scidiscovery/research_loop/native.py'
    original.unlink()
    assert recovery.start(root,3,**options)['status']==result['status']
    assert (current/'native-outcome.json').read_bytes()==outcome
    assert native.usage(root)['observed_tokens']==reported
    assert len(execution.records(root,3))==2
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
    home=Path(permit['native_home']);(home/'config.toml').write_text('changed permissions')
    with pytest.raises(ValueError,match='configuration changed'):recovery.start(root,3,**options)


def test_old_legacy_action_cannot_open_scientific_audit_or_wait_loop(tmp_path):
    root,options=scene(tmp_path,action='wait')
    result=recovery.start(root,3,**options)
    assert result['status']=='closeout_native_unsealed'
    assert not (execution.origin(root,3)/'result-identity.json').exists()
    assert not (root/'jobs/004.json').exists()
    assert not (execution.origin(root,3)/'continuations/002').exists()


@pytest.mark.parametrize('gate',['pause','unknown_external','correction'])
def test_permit_before_intent_crash_rechecks_original_gates(tmp_path,monkeypatch,gate):
    root,options=scene(tmp_path)
    write=c.write
    def publish_then_crash(file,value):
        write(file,value)
        if Path(file)==recovery.path(root,3):raise RuntimeError('fixture crash after permit before intent')
    with monkeypatch.context() as patch:
        patch.setattr(c,'write',publish_then_crash)
        with pytest.raises(RuntimeError,match='fixture crash'):recovery.start(root,3,**options)
    recovery.validate(root,3)
    if gate=='pause':(root/'pause.request').write_text('stop')
    elif gate=='unknown_external':monkeypatch.setattr(budget,'execution_blocked',lambda *args,**kwargs:True)
    else:
        from scidiscovery.research_loop import correction
        monkeypatch.setattr(correction,'gate',lambda *args,**kwargs:dict(reason='fixture original correction pause'))
    with pytest.raises(ValueError,match='pause|unknown external'):recovery.start(root,3,**options)
    assert not (execution.origin(root,3)/'continuations/001/native-intent.json').exists()
    assert not (c.workspace(root,3)/'work/submission-count').exists()


def test_invalid_original_inputs_fail_before_publishing_permit(tmp_path,monkeypatch):
    root,options=scene(tmp_path)
    def invalid(*args,**kwargs):raise ValueError('fixture exact input failure')
    monkeypatch.setattr(c,'_validate_inputs',invalid)
    with pytest.raises(ValueError,match='exact input failure'):recovery.start(root,3,**options)
    assert not recovery.path(root,3).exists()
    assert native.usage(root)['unknown_steps']==[3]
    assert len(execution.records(root,3))==1


def test_enabled_old_review_contract_is_exactly_bound_without_new_review(tmp_path):
    from scidiscovery.research_loop import formal_review,continuation
    root,options=scene(tmp_path,legacy=True,review=True)
    frozen=(root/'qualification/review-contract.json').read_bytes()
    assert continuation.validate_task(root,3)==c.read(root/'jobs/003.json')
    assert formal_review.contract(root)==c.settings(root)['formal_review_policy']['contract']
    assert not recovery.path(root,3).exists()
    result=recovery.start(root,3,**options)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    permit=recovery.validate(root,3)
    assert permit['review_contract']['path']==str(root/'qualification/review-contract.json')
    assert formal_review.contract(root)==c.settings(root)['formal_review_policy']['contract']
    assert (root/'qualification/review-contract.json').read_bytes()==frozen
    assert not any(c.read(file).get('formal_review') for file in (root/'jobs').glob('*.json'))
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'


@pytest.mark.parametrize('mutation',['old_code','scientific_semantics'])
def test_review_preserves_semantics_across_runtime_upgrades(tmp_path,mutation):
    root,options=scene(tmp_path,legacy=True,review=True)
    if mutation=='old_code':
        file=tmp_path/'old-runtime/lib/python3.12/site-packages/scidiscovery/research_loop/native.py'
        file.write_text(file.read_text()+'\n# changed original implementation\n')
    else:
        # Change the current scientific contract, leaving the old frozen receipt intact.
        from scidiscovery.research_loop import formal_review
        current=formal_review._contract();current['scope']='Changed scientific review scope'
        with patch.object(formal_review,'_contract',return_value=current):
            with pytest.raises(ValueError,match='scientific review semantics'):recovery.start(root,3,**options)
        assert not recovery.path(root,3).exists()
        return
    recovery.start(root,3,**options)
    assert recovery.path(root,3).exists()
    assert (execution.origin(root,3)/'continuations/001/native-intent.json').exists()


def external_startup(tmp_path):
    import test_closeout_protocol as protocol
    from scidiscovery.research_loop import delivery
    root=protocol.scene(tmp_path,[dict(action='deliver',candidate='run-1')])
    home=tmp_path/'native-home';home.mkdir();(home/'config.toml').write_text('fixture=true\n')
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','fixture original external startup')
    nodes=[dict(step=step,seal_ref=c.record_ref(execution.origin(root,step)/'result-identity.json')) for step in (1,2)]
    sources=delivery.catalogue_sources(root,nodes)
    budget.begin_closeout(root,2,3,'fixture final external',delivery_sources=sources,final_only=True)
    job=c.build_job(root,3,'A',[1,2],purpose='closeout',source_cutoff=2,delivery_sources=sources)
    return failed_original(tmp_path,root,job,budget.accept_node(root,job),home)


def delivery_report(root,tmp_path):
    original=recovery.validate(root,3)
    action=budget.closeout_state(root)['actions'][0]['intent']
    record=execution.origin(root,3);current=record/'continuations/001'
    files=dict(settings=root/'settings.json',settings_identity=root/'settings-identity.json',job=root/'jobs/003.json',
        startup_permit=recovery.path(root,3),final_claim=root/'control-ledger'/f'{original["final_return"]["seq"]:06d}.json',
        action_intent=root/'control-ledger'/f'{action["seq"]:06d}.json',origin_scientific_seal=record/'result-identity.json',
        successor_intent=current/'native-intent.json',successor_outcome=current/'native-outcome.json',
        successor_usage=current/'usage.json',successor_scientific_raw=current/'science-output.json')
    report=dict(version=1,kind='delivery_pre_dispatch_engineering_reconciliation',author='Independent deterministic fixture investigator',
        subject=dict(goal_id=c.settings(root)['goal_id'],step=3,native_attempt=1,action_seq=action['seq'],
            final_return=original['final_return'],**{key:action[key] for key in ('action_id','action_kind','operation_id','decision')}),
        originals={name:recovery._ref(file) for name,file in files.items()},
        accepted_implementation=original.get('implementation',[]),
        additional_read_implementation=[recovery._ref(Path(recovery.__file__).with_name('delivery.py'))],
        control_ledger_prefix=[recovery._ref(root/'control-ledger'/f'{number:06d}.json') for number in range(1,action['seq']+1)],
        engineering_judgment=dict(delivery_dispatch='not_dispatched'),
        positive_source_flow_evidence=dict(mandatory_failure_chain=['Fixture raises at mandatory synchronous transition before invoke'],
            first_external_dispatch='delivery.invoke after transition',conclusion='The deterministic injected failure occurs before first managed invoke.'))
    evidence=tmp_path/'delivery-reconciliation.json';c.write(evidence,report)
    return dict(reconciliation=evidence,reconciliation_ref=c.file_ref(evidence),
        authorization_statement='Accept exact independent engineering non-dispatch evidence for this original delivery only; preserve all usage and its original finite cap.')


def test_startup_successor_delivers_through_real_adapter_and_confirms_once(tmp_path):
    root,options=external_startup(tmp_path)
    result=recovery.start(root,3,**options)
    assert result['status']=='delivery_confirmed',json.dumps(result,indent=2)
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['outcome']['result']['status']=='confirmed' for row in actions)
    assert len({row['intent']['operation_id'] for row in actions})==1
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\n'
    assert c.read(root/'runs/submitted.json')['run_id']==1
    assert controller.run(root)['status']=='delivery_confirmed'
    assert len(budget.closeout_state(root)['actions'])==2
    assert len(execution.records(root,3))==2


def pending_delivery(tmp_path,monkeypatch):
    root,options=external_startup(tmp_path)
    transition=budget.transition
    def crash(root_,phase,reason,**kwargs):
        if reason=='delivery_submit':raise RuntimeError('fixture mandatory transition before first invoke')
        return transition(root_,phase,reason,**kwargs)
    with monkeypatch.context() as change:
        change.setattr(budget,'transition',crash)
        with pytest.raises(RuntimeError,match='before first invoke'):recovery.start(root,3,**options)
    assert len(budget.closeout_state(root)['actions'])==1
    assert budget.closeout_state(root)['actions'][0]['outcome'] is None
    assert not (root/'runs/submitted.json').exists()
    return root,delivery_report(root,tmp_path)


def test_proven_accepted_not_dispatched_delivery_collects_original_operation(tmp_path,monkeypatch):
    root,options=pending_delivery(tmp_path,monkeypatch)
    saved={file:file.read_bytes() for file in (recovery.path(root,3),execution.origin(root,3)/'result-identity.json',
        execution.record(root,3)/'native-outcome.json',root/'settings.json')}
    original=budget.closeout_state(root)['actions'][0]['intent']
    result=recovery.collect_delivery(root,3,**options)
    assert result['status']=='delivery_confirmed',json.dumps(result,indent=2)
    receipt=recovery.validate_collection(root,3)
    assert receipt['action_id']==original['action_id'] and receipt['operation_id']==original['operation_id']
    assert receipt['limits']==dict(max_seconds=original['max_seconds'],max_invocations=1,native_dispatches=0)
    read=c.read
    def no_result_read(file):
        if 'native-evidence' in Path(file).parts:pytest.fail('Authorization validator opened execution results')
        return read(file)
    with monkeypatch.context() as auth_only:
        auth_only.setattr(c,'read',no_result_read)
        assert recovery.validate_collection(root,3)==receipt
    assert budget.closeout_state(root)['actions'][0]['intent']==original
    assert recovery.collect_delivery(root,3,**options)['status']=='delivery_confirmed'
    assert len(budget.closeout_state(root)['actions'])==2
    assert all(file.read_bytes()==raw for file,raw in saved.items())
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\n'
    assert not (execution.origin(root,3)/'continuations/002').exists()


def test_collection_intent_before_effect_crash_never_submits_again(tmp_path,monkeypatch):
    from scidiscovery.research_loop import delivery
    root,options=pending_delivery(tmp_path,monkeypatch)
    def crash(*args,**kwargs):raise RuntimeError('fixture crash after collector intent')
    with monkeypatch.context() as change:
        change.setattr(delivery,'invoke',crash)
        with pytest.raises(RuntimeError,match='after collector intent'):recovery.collect_delivery(root,3,**options)
    assert recovery.collection_path(root,3).exists()
    with monkeypatch.context() as change:
        change.setattr(delivery,'invoke',lambda *args,**kwargs:pytest.fail('Original submit must never be invoked twice'))
        result=recovery.collect_delivery(root,3,**options)
    assert result['status']=='delivery_unknown'
    assert not (root/'runs/submitted.json').exists()
    assert (c.workspace(root,3)/'work/launches').read_text()=='exec\n'


def test_final_delivery_ledger_replay_never_reads_execution_results(tmp_path,monkeypatch):
    root,options=pending_delivery(tmp_path,monkeypatch)
    settings=c.settings(root)
    read=c.read
    forbidden={'science-output.json','result-identity.json','result.json','native-outcome.json','usage.json'}
    def guard_read(file):
        if Path(file).name in forbidden:pytest.fail('Authorization replay read execution results')
        return read(file)
    monkeypatch.setattr(c,'read',guard_read)
    events,_=budget._events(root,settings)
    assert events[-1]['kind']=='action_intent'


def test_delivery_eligibility_still_rejects_changed_scientific_raw(tmp_path,monkeypatch):
    from scidiscovery.research_loop import delivery
    root,options=pending_delivery(tmp_path,monkeypatch)
    job=c.read(root/'jobs/003.json');result=c.validate(c.workspace(root,3))
    raw=execution.raw(root,3);raw.write_text(raw.read_text()+'\n')
    with pytest.raises(ValueError,match='identity|changed|unverified'):delivery.binding(root,job,result)
    with pytest.raises(ValueError):recovery.collect_delivery(root,3,**options)
    assert not recovery.collection_path(root,3).exists()
    assert not (root/'runs/submitted.json').exists()


@pytest.mark.parametrize('attribution',[None,{},dict(postcall_ref='invalid_reference',limitations=['Unknown original flow.']),dict(postcall_ref=dict(path='/fixture/original-native'),limitations=[])])
def test_startup_requires_original_source_attribution_independent_of_current_tool(tmp_path,attribution):
    root,options=scene(tmp_path)
    report=c.read(options['reconciliation'])
    report['native_implementation_attribution']=attribution
    c.write(options['reconciliation'],report);options['reconciliation_ref']=c.file_ref(options['reconciliation'])
    with pytest.raises(ValueError,match='source attribution'):
        recovery.start(root,3,**options)
    assert not recovery.path(root,3).exists()


@pytest.mark.process_e2e
def test_startup_recovery_binds_original_skill_revision_instead_of_initial_or_latest(tmp_path):
    root,options=_scene(tmp_path,skill_revision=True)
    job=c.read(root/'jobs/003.json')
    assert job['skill_binding']['skill_set']=='selected'
    selected=root/job['configuration_revision']['path']
    selected_bytes=selected.read_bytes()
    document=research_config.to_document(root,configuration_updates.current(root))
    document['agent']['roles']['A']['skill_set']='initial'
    configuration_updates.submit(root,document,expected_revision=1)
    saved=originals(root)
    result=recovery.start(root,3,**options)
    assert result['status']=='final_a_completed',json.dumps(result,indent=2)
    permit=recovery.validate(root,3)
    assert permit['originals']['settings']['path']==str(selected)
    assert permit['originals']['settings']==c.file_ref(selected)
    assert selected.read_bytes()==selected_bytes
    assert configuration_updates.current(root)['role_profiles']['A']['skill_set']=='initial'
    intent=c.read(execution.record(root,3)/'native-intent.json')
    assert intent['skill_binding']==job['skill_binding']==permit['skill_binding']
    assert all(file.read_bytes()==data for file,data in saved.items())
    assert (c.workspace(root,3)/'work/submission-count').read_text()=='one\n'
