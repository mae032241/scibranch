"""Pure successor control validation preserves exact stopped-attempt identities."""

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import author_closeout as author,budget,contracts as c,execution,native


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


def test_author_successor_allows_installed_tool_upgrade(pure_upgrade):
    root,old,value,code,_=pure_upgrade
    code.write_text('upgraded author runtime')
    assert author.validate_successor(root,value)==value
    c.write(author.successor_path(root,1),value)
    assert author.validate(root,old)==old
