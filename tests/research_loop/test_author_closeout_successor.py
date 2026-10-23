"""Real successor through W seal; shared A/delivery is covered by first-author E2E.

Keep immutable failures and no new science calls across tool upgrades. Negative cases
retain the public controller entry; success exercises the real successor helper.
"""
import json
import os
import time
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import author_closeout as author,budget,contracts as c,controller,execution,native
from scidiscovery.research_loop.experiment_client import record_directory
from test_author_closeout_e2e import FAKE,scene


@pytest.fixture
def pure_upgrade(tmp_path):
    # Pure verifier fixtures only; these synthetic metadata records are not
    # accepted native execution or a sealed scientific result.
    root=tmp_path/'instance';root.mkdir();code=tmp_path/'launcher';code.write_text('old implementation')
    c.write(root/'settings.json',dict(native_launcher_argv=[str(code)]))
    c.write(root/'settings-identity.json',dict(path='settings.json'))
    c.write(root/'jobs/001.json',dict(step=1,role='W'))
    origin=execution.origin(root,1)
    for name,value in [('native-intent.json',{}),('native-outcome.json',dict(returncode=-15,stop_reason='token_limit',cleanup='observed_group_cleaned')),
                       ('usage.json',{}),('experiment-config.json',{})]:c.write(origin/name,value)
    calls=c.workspace(root,1)/'work/lineage/calls/original';calls.mkdir(parents=True);(calls/'accepted.json').write_text('{}')
    old=dict(version=1,scope='author_closeout_only',context='new_context',step=1,source_attempt=0,
        job=author._ref(root,root/'jobs/001.json'),settings=author._ref(root,root/'settings.json'),
        original_intent=author._ref(root,origin/'native-intent.json'),original_outcome=author._ref(root,origin/'native-outcome.json'),
        original_usage=author._ref(root,origin/'usage.json'),original_experiment_config=author._ref(root,origin/'experiment-config.json'),
        calls=author._calls(root,1),implementation=[dict(path=str(code))])
    permit=root/'host-recovery/author-closeout/001.json';c.write(permit,old)
    first=origin/'continuations/001'
    c.write(first/'native-intent.json',dict(attempt=1,continuation_kind='author_closeout',context_mode='new_context',
        root_intent_ref=c.record_ref(root/old['original_intent']['path']),author_closeout=author._ref(root,permit),task_ref=c.record_ref(root/old['job']['path']),
        budget_slice=dict(boot_id='synthetic-pure-validator-boot')))
    for name in ('runner-process.json','native-process.json'):
        c.write(first/name,dict(task_ref=c.record_ref(first/'native-intent.json'),boot_id='synthetic-pure-validator-boot'))
    processes={name:c.record_ref(first/name) for name in ('runner-process.json','native-process.json')}
    for name,value in [('native-outcome.json',dict(returncode=-15,stop_reason='author_closeout_wall_limit',cleanup='observed_group_cleaned',execution_processes=processes)),
                       ('usage.json',{}),('usage-observation.json',{}),('native.jsonl',{})]:c.write(first/name,value)
    code.write_text('new implementation')
    value=dict(version=1,scope='author_closeout_successor_only',context='new_context',step=1,source_attempt=1,attempt=2,
        max_tokens=300,max_seconds=20,authorization_note='Exactly one successor.',authorization_statement='Finish the same task.',
        original_permit=author._ref(root,permit),
        source_originals={name:author._ref(root,first/name) for name in
            ('native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl','runner-process.json','native-process.json')})
    return root,old,value,code,None


def test_exact_retained_upgrade_verifies_old_and_new_bytes_without_budget_sampling(pure_upgrade,monkeypatch):
    root,old,value,*_=pure_upgrade
    for module,name in [(budget,'snapshot'),(native,'usage'),(execution,'records')]:
        monkeypatch.setattr(module,name,lambda *a,**kw:pytest.fail('Pure upgrade validation must not sample usage or recurse'))
    assert author.validate_successor(root,value)==value
    c.write(author.successor_path(root,1),value)
    assert author.validate(root,old)==old


@pytest.mark.parametrize('tamper',['old_permit','source_outcome','next_attempt','runner_process','native_process','rebound_process','process_boot','source_context','source_root_intent','source_step'])
def test_author_successor_rejects_tampered_old_new_or_source_identity(pure_upgrade,tamper):
    root,old,value,code,archive=pure_upgrade
    if tamper=='old_permit':c.write(root/value['original_permit']['path'],dict(old,step=2))
    elif tamper=='source_outcome':c.write(root/value['source_originals']['native-outcome.json']['path'],dict(returncode=0))
    elif tamper in {'source_context','source_root_intent'}:
        path=root/value['source_originals']['native-intent.json']['path'];changed=c.read(path)
        changed['context_mode' if tamper=='source_context' else 'root_intent_ref']='unapproved'
        c.write(path,changed);value['source_originals']['native-intent.json']=author._ref(root,path)
    elif tamper=='source_step':
        path=root/value['original_permit']['path'];c.write(path,dict(old,step=2));value['original_permit']=author._ref(root,path)
    elif tamper in {'runner_process','native_process','rebound_process','process_boot'}:
        name='native-process.json' if tamper=='native_process' else 'runner-process.json'
        path=root/value['source_originals'][name]['path'];changed=c.read(path);changed['boot_id']='unapproved-boot';c.write(path,changed)
        if tamper in {'rebound_process','process_boot'}:value['source_originals'][name]=author._ref(root,path)
        if tamper=='process_boot':
            outcome=root/value['source_originals']['native-outcome.json']['path'];changed=c.read(outcome)
            changed['execution_processes'][name]=c.record_ref(path);c.write(outcome,changed)
            value['source_originals']['native-outcome.json']=author._ref(root,outcome)
    else:value['attempt']=3
    if tamper in {'source_outcome','runner_process','native_process','rebound_process','process_boot'}:
        assert author.validate_successor(root,value)==value
        with pytest.raises(ValueError):author.require_successor_eligibility(root,value)
    else:
        with pytest.raises(ValueError):author.validate_successor(root,value)


FIRST_STOP=r'''
 if home.name=='context-002':
  facts=work/'work/author-closeout-science.json'
  old=work.parents[2]/'native-evidence'/work.name/'continuations/001/native-outcome.json'
  assert facts.is_file() and not old.exists()
  for mutate in (lambda:facts.write_text('{}'),lambda:facts.chmod(0o600),lambda:facts.unlink(),lambda:old.write_text('{}')):
   try:mutate()
   except OSError:pass
   else:raise AssertionError('Successor original or compact index writable')
  for action in ('compile','submit','query','collect','cancel'):
   denied=subprocess.run(['exp',action],capture_output=True,text=True)
   assert denied.returncode==2 and json.loads(denied.stdout)['accepted'] is False
 if home.name=='context-001':
  import time
  usage=dict(input_tokens=100,cached_input_tokens=20,output_tokens=3)
  with session.open('a') as stream:stream.write(json.dumps(dict(type='event_msg',payload=dict(type='token_count',info=dict(total_token_usage=usage,last_token_usage=usage))))+'\n')
  time.sleep(30)
 elif MODE=='successor_unknown':raise SystemExit(7)
 elif MODE=='successor_wall':
  import time
  print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
  time.sleep(30)
'''


@pytest.fixture
def successor_diagnostic(tmp_path):
    # Bounded mechanical progress only; no sampling thread or scientific claim.
    directory=Path(os.environ.get('SCID_TEST_LOG_DIR',str(tmp_path/'diagnostics')))
    directory.mkdir(parents=True,exist_ok=True)
    with (directory/'author-successor-stages.log').open('w') as stages:
        def mark(label):
            stages.write(f'{time.monotonic():.3f} {label}\n');stages.flush()
        yield mark


@pytest.mark.parametrize('mode',['success','unknown','over','wall','funds'])
def test_stopped_first_author_reserves_unknown_then_one_successor_seals_w_or_blocks(tmp_path,monkeypatch,execution_registry,successor_diagnostic,mode):
    successor_diagnostic('setup')
    import test_author_closeout_e2e as base
    fake=FAKE.replace(" if MODE=='unknown':raise SystemExit(7)",FIRST_STOP)
    # Session discovery intentionally matches the exact thread in the filename.
    fake=fake.replace("session=home/'sessions/current.jsonl'","session=home/'sessions'/('rollout-'+thread+'.jsonl')")
    fake=fake.replace("100 if MODE=='over'", "100 if MODE=='successor_over'")
    monkeypatch.setattr(base,'FAKE',fake)
    root,origin=scene(tmp_path,monkeypatch,'successor_'+mode)
    calls=record_directory(root,2)/'calls'
    successor_diagnostic('first start')
    with pytest.raises((ValueError,RuntimeError)):
        controller.worker_closeout(root,2,max_tokens=50,max_seconds=3,authorization_note='Exactly one initial author finite-wall fixture.')
    successor_diagnostic('first stopped')
    first=origin/'continuations/001'
    outcome=c.read(first/'native-outcome.json')
    assert outcome['stop_reason']=='author_closeout_wall_limit' and outcome['metering']['coverage']=='unknown'
    assert outcome['cleanup']=='observed_group_cleaned' and native.usage(root)['unknown_steps']==[2]
    assert native.usage(root)['blocking_unknown_steps']==[]
    assert not (first/'science-output.json').exists() and not (root/'jobs/003.json').exists()
    old_permit=root/'host-recovery/author-closeout/002.json'
    assert native.usage(root)['reserved_tokens']==0 and native.usage(root)['blocking_unknown_steps']==[]
    originals={p:p.read_bytes() for basepath in (origin,calls) for p in basepath.rglob('*')
        if p.is_file() and (p.is_relative_to(first) or p.parent==origin or p.is_relative_to(calls))}
    settings=(root/'settings.json').read_bytes();permit_bytes=old_permit.read_bytes()
    kwargs=dict(max_tokens=5 if mode=='over' else 1000,max_seconds=3 if mode=='wall' else 20,authorization_note='One explicit second author, never a retry loop.',
        successor=True,authorization_statement='Finish this same task once, without new training.')
    if mode=='funds':
        reserve=budget.delivery_reserve(c.settings(root));before=native.usage(root)
        budget.amend(root,max_tokens=before['accounted_tokens'],
            authorization_note='Actual reported usage has reached the shared token ceiling.')
        from scidiscovery.research_loop import runtime
        monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Insufficient funds must not dispatch'))
        with pytest.raises(ValueError,match='^Finite author successor and delivery reserve exceed remaining authorization$'):
            controller.worker_closeout(root,2,**kwargs)
        receipt=author.successor_path(root,2);assert receipt.is_file()
        assert author.validate_successor(root,c.read(receipt))['attempt']==2
        assert not (origin/'continuations/002').exists() and not (root/'jobs/003.json').exists()
        assert len(execution.records(root,2))==2 and native.usage(root)==before
        assert all(p.read_bytes()==raw for p,raw in originals.items())
        assert old_permit.read_bytes()==permit_bytes and (root/'settings.json').read_bytes()==settings
        return
    if mode in {'success','over'}:
        # Bound this test to the successor-specific production lifecycle. The
        # first-author E2E separately exercises the shared A-to-delivery path.
        successor_args={key:value for key,value in kwargs.items() if key!='successor'}
        def collect_successor():
            with controller._writer(root),controller._inactive_on_error(root,'author_closeout_failed'):
                return author.start_successor(root,2,**successor_args)
        successor_diagnostic('successor enter')
        result=collect_successor()
        successor_diagnostic('successor W sealed')
        assert result['stage_intent']=='review',json.dumps(result)
        current=origin/'continuations/002'
        assert c.read(current/'native-outcome.json')['stop_reason'] is None
        if mode=='over':
            assert c.read(current/'usage.json')['input_tokens']==100
            assert c.read(author.successor_path(root,2))['max_tokens']==5
        assert c.validate(c.workspace(root,2))['stage_intent']=='review'
        assert native.usage(root)['blocking_unknown_steps']==[]
        assert not (root/'jobs/003.json').exists() and budget.closeout_state(root)['begin'] is None
        current=origin/'continuations/002';facts=c.read(current/'author-closeout-facts.json')
        science_facts=c.read(current/'author-closeout-science.json')
        assert len(science_facts['calls'])==2 and all('originals' not in row for row in science_facts['calls'])
        assert facts['call_count']==2 and facts['omitted_calls']==0
        assert 'not scientific chronology' in facts['ordering']
        assert (current/'experiment-config.json').read_bytes()==(origin/'experiment-config.json').read_bytes()
        accepted=c.read(current/'native-intent.json')
        assert accepted['budget_slice']['previous_tokens']==123  # Original 20 plus stopped first author 103.
        from scidiscovery.research_loop import runtime
        monkeypatch.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Repeat must collect the accepted successor, never launch again'))
        assert collect_successor()['stage_intent']=='review'
        # Count this W's chain and current response; scientific-context checks
        # still verify preceding H chains independently. This read must not
        # sample current budget or turn a prior validation into a cached verdict.
        records=execution.records;science_payload=c.science_payload;call_inventory=author._calls
        checks=dict(chains=0,finals=0,inventories=0)
        def counted_records(root_arg,step,*args,**kwargs):
            if Path(root_arg)==root and step==2:checks['chains']+=1
            return records(root_arg,step,*args,**kwargs)
        def counted_inventory(*args,**kwargs):
            checks['inventories']+=1;return call_inventory(*args,**kwargs)
        def counted_science(path,*args,**kwargs):
            if Path(path)==current/'science-output.json':checks['finals']+=1
            return science_payload(path,*args,**kwargs)
        monkeypatch.setattr(execution,'records',counted_records)
        monkeypatch.setattr(author,'_calls',counted_inventory)
        monkeypatch.setattr(c,'science_payload',counted_science)
        monkeypatch.setattr(budget,'snapshot',lambda *a,**kw:pytest.fail('Historical record validation must not sample budget'))
        assert execution.record(root,2)==current
        assert checks==dict(chains=1,finals=1,inventories=1)
        job=c.read(root/'jobs/002.json')
        bad=dict(accepted,author_closeout=dict(accepted['author_closeout'],path='host-recovery/author-closeout/unapproved.json'))
        with pytest.raises(ValueError,match='^Author closeout original changed$'):
            author.validate_intent(root,job,bad)
        with pytest.raises(ValueError,match='^Author closeout accepted intent required$'):
            author.validate_intent(root,job,dict(accepted,context_mode='same_session'))
        with pytest.raises(ValueError,match='^Author closeout accepted a different scientific task$'):
            author.validate_intent(root,job,dict(accepted,task_ref=dict(path='/fixture/jobs/002.json')))
        injected=calls/'unexpected-new-call/accepted.json';c.write(injected,dict(call_id='not-authorized'))
        try:
            with pytest.raises(ValueError,match='^Author closeout cannot change or add managed calls$'):
                execution.record(root,2)
            with pytest.raises(ValueError,match='^Author closeout cannot change or add managed calls$'):
                author.require_intent_eligibility(root,c.read(root/'jobs/002.json'),accepted)
        finally:injected.unlink();injected.parent.rmdir()
        assert execution.record(root,2)==current
    else:
        with pytest.raises((ValueError,RuntimeError)):controller.worker_closeout(root,2,**kwargs)
        assert not (origin/'result-identity.json').exists() and not (root/'jobs/003.json').exists()
        if mode=='unknown':
            assert native.usage(root)['unknown_steps']==[2]
            assert native.usage(root)['blocking_unknown_steps']==[]
        else:
            outcome=c.read(origin/'continuations/002/native-outcome.json')
            assert outcome['stop_reason']=='author_closeout_wall_limit'
            assert outcome['cleanup']=='observed_group_cleaned'
    assert len(execution.records(root,2))==3 and not (origin/'continuations/003').exists()
    assert all(p.read_bytes()==raw for p,raw in originals.items())
    assert old_permit.read_bytes()==permit_bytes and (root/'settings.json').read_bytes()==settings


def test_author_successor_allows_installed_tool_upgrade(pure_upgrade):
    root,old,value,code,_=pure_upgrade
    code.write_text('upgraded author runtime')
    assert author.validate_successor(root,value)==value
    c.write(author.successor_path(root,1),value)
    assert author.validate(root,old)==old
