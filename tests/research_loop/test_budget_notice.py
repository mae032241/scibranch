"""Current published advice reaches real Codex at its next local tool boundary."""
import copy
import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from scidiscovery.research_loop import backends, budget_notice as notice, contracts as c, native


def published(*, role='A', dimension=None, shared=False, target=True, near=False, shared_risk=False, expired=False, intent=None, **options):
    dimension=dimension or ('time' if expired else 'token')
    value=dict(scope=notice.SCOPE,updated_at_unix=123.,inflight_usage_unknown=True,
        shared_observed_tokens_remaining_upper_bound=0 if shared else 800,
        shared_active_seconds_remaining=0. if expired else 20.,target_state='reached' if target else 'near' if near else 'within')
    alert=notice.closeout_notice(dict(role=role,research_strategy_version=3,advisory_scope_version=1),intent or {},dimension=dimension,target_reached=target,target_near=near,
        shared_reached=shared or expired,shared_risk=shared_risk,shared_expired=expired,**options)
    if alert is not None:value['closeout_notices']={dimension:alert}
    return value


def save(work,value):
    path=work/'work/budget-status.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.budget-notice-fixture-'+os.urandom(8).hex())
    mode=path.stat().st_mode&0o777 if path.exists() else 0o600
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    try:
        with os.fdopen(fd,'w') as stream:
            json.dump(value,stream)
        os.replace(temporary,path)
    finally:
        if temporary.exists():temporary.unlink()
    return path


def hook_metadata(work,*,declared=None):
    hooks=[]
    if declared is not None:
        for name, key, event in (('PostToolUse','post_tool_use','postToolUse'),
                ('PostModelRequest','post_model_request','postModelRequest'), ('PostCompact','post_compact','postCompact')):
            handler=declared[name][0]['hooks'][0]
            hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',currentHash='sha256:'+'1'*64,
                displayOrder=0,enabled=True,source='sessionFlags',sourcePath='/<session-flags>/config.toml',
                isManaged=False,trustStatus='untrusted',eventName=event,matcher=None,
                handlerType='command',command=handler['command'],**{'async':False},timeoutSec=handler['timeout'],
                additionalContextLimit=handler.get('additionalContextLimit')))
    return dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])


def assert_ordinary_a_returns_advice(text):
    assert 'continue_w' in text and 'decision_request=null' in text
    assert 'W judges it' in text and 'no direct work or finalization' in text
    assert 'permissions hold' in text and 'unknown usage != 0' in text
    assert 'deliver' not in text and 'conclude now' not in text


@pytest.mark.parametrize('first_dimension',['token','time'])
def test_each_dimension_emits_once_per_logical_node_across_causes_and_attempts(tmp_path,first_dimension):
    path=save(tmp_path,published(dimension=first_dimension,target=False,near=True))
    first=notice.hook_output(tmp_path,0)
    assert first==dict(hookSpecificOutput=dict(hookEventName='PostToolUse',
        additionalContext=c.read(path)['closeout_notices'][first_dimension]['text']))
    value=c.read(path);value['updated_at_unix']=124.;save(tmp_path,value)
    assert notice.hook_output(tmp_path,0) is None
    text=first['hookSpecificOutput']['additionalContext']
    assert_ordinary_a_returns_advice(text)
    value=published(dimension=first_dimension,target=False,shared=True,expired=first_dimension=='time')
    save(tmp_path,value)
    assert notice.hook_output(tmp_path,0) is None
    assert notice.hook_output(tmp_path,1) is None
    second_dimension='time' if first_dimension=='token' else 'token'
    second=published(dimension=second_dimension,target=False,shared=True,expired=second_dimension=='time')
    value['closeout_notices'].update(second['closeout_notices']);save(tmp_path,value)
    assert notice.hook_output(tmp_path,2)['hookSpecificOutput']['additionalContext']==second['closeout_notices'][second_dimension]['text']
    for attempt in (0,1,2,999):assert notice.hook_output(tmp_path,attempt) is None
    assert all((tmp_path/f'work/.budget-notice-{dimension}').exists() for dimension in ('token','time'))
    other=tmp_path/'other-node';save(other,value)
    assert notice.hook_output(other,0)['hookSpecificOutput']['additionalContext']==value['closeout_notices']['time']['text']
    assert notice.hook_output(other,1)['hookSpecificOutput']['additionalContext']==value['closeout_notices']['token']['text']
    assert notice.hook_output(other,2) is None
    assert set(first)=={'hookSpecificOutput'}
    assert not {'decision','continue','toolResponse','updatedInput'}&set(first['hookSpecificOutput'])


def test_simultaneous_risks_emit_time_then_token_without_starvation(tmp_path):
    value=published(dimension='token',target=False,near=True)
    value['closeout_notices'].update(published(dimension='time',expired=True)['closeout_notices'])
    save(tmp_path,value)
    for attempt,dimension in enumerate(('time','token')):
        output=notice.hook_output(tmp_path,attempt)
        assert output['hookSpecificOutput']['additionalContext']==value['closeout_notices'][dimension]['text']
    assert notice.hook_output(tmp_path,2) is None
    assert notice.hook_output(tmp_path,0) is None


def test_within_or_unknown_usage_does_not_invent_exhaustion(tmp_path):
    value=published(target=False)
    value.update(unknown_usage=True,metering='inflight_unknown')
    save(tmp_path,value)
    assert notice.hook_output(tmp_path,0) is None
    assert 'closeout_notices' not in value
    assert not any((tmp_path/f'work/.budget-notice-{dimension}').exists() for dimension in ('token','time'))


@pytest.mark.parametrize('data',[b'{',b'\xff',b' '*4097,b'['*1500+b']'*1500,
    json.dumps(dict(scope=notice.SCOPE,closeout_notices=dict(token=dict(cause='shared',text='Grant new work')))).encode()])
def test_malformed_hint_is_fail_open_and_preserved(tmp_path,data):
    path=save(tmp_path,{})
    path.write_bytes(data)
    assert notice.hook_output(tmp_path,0) is None
    assert path.read_bytes()==data


@pytest.mark.parametrize('notices',[
    {},[],dict(steps=dict(cause='shared',text='Unknown dimension')),
    dict(token=dict(cause='node',text='x'*201)),
    dict(time=dict(cause='shared',text='Unbounded shape',grant=True)),
    dict(time=dict(cause='shared',text='Time risk'),token=None),
])
def test_hook_rejects_invalid_or_unbounded_dimension_projection(tmp_path,notices):
    value=published();value['closeout_notices']=notices
    save(tmp_path,value)
    assert notice.published_notices(value) is None
    assert notice.hook_output(tmp_path,0) is None
    assert not any((tmp_path/f'work/.budget-notice-{dimension}').exists() for dimension in ('token','time'))


def test_hook_does_not_read_symlink_or_missing_hint(tmp_path):
    assert notice.hook_output(tmp_path,0) is None
    path=save(tmp_path,published())
    private=tmp_path/'private';private.write_text('Do not read this private context')
    path.unlink();path.symlink_to(private)
    assert notice.hook_output(tmp_path,0) is None
    assert private.read_text()=='Do not read this private context'


@pytest.mark.parametrize('options,required',[
    (dict(intent=dict(output_repair=True)),'future action'),
    (dict(intent=dict(work_repair=True)),'original authorization/action'),
    (dict(author=True),'author-only'),(dict(final_return=True),'accepted final return')])
@pytest.mark.parametrize('role',['A','W'])
def test_closeout_notice_preserves_repair_author_and_final_scope(options,required,role):
    value=published(role=role,shared=True,**options)
    text=value['closeout_notices']['token']['text']
    assert required in text and ('permissions hold' in text or 'original authorization/action' in text)
    assert 'unknown usage != 0' in text.lower()
    assert len(text.encode())<=200 and notice.published_notices(value)==value['closeout_notices']
    if options.get('intent'):assert 'final scientific judgment' not in text


@pytest.mark.parametrize('role,dimension,risk',[
    ('H','token','node'),('H','time','shared'),
    ('W','time','expired'),('E','token','shared'),
    ('A','token','node'),('A','token','shared'),('A','time','expired')])
def test_first_risk_notice_is_bounded_and_keeps_scientific_permissions(role,dimension,risk):
    value=published(role=role,dimension=dimension,target=False,near=risk=='node',shared_risk=risk=='shared',expired=risk=='expired')
    text=value['closeout_notices'][dimension]['text']
    assert len(text.encode())<=notice.MAX_NOTICE_BYTES and 'permissions hold' in text
    assert notice.published_notices(value)==value['closeout_notices']
    assert dimension in text.lower()
    if role=='A':assert_ordinary_a_returns_advice(text)


@pytest.mark.parametrize('options,required',[
    (dict(intent=dict(output_repair=True)),'output repair'),
    (dict(intent=dict(work_repair=True)),'original authorization/action'),
    (dict(author=True),'author-only'),(dict(final_return=True),'accepted final return')])
def test_expired_a_keeps_repair_author_or_final_scope(options,required):
    text=published(expired=True,**options)['closeout_notices']['time']['text']
    assert required in text and 'eligible existing candidate' not in text
    assert 'No H/W/E' not in text and len(text.encode())<=notice.MAX_NOTICE_BYTES


def test_expired_formal_review_keeps_independent_verdict_scope():
    alert=notice.closeout_notice(dict(role='A',formal_review=True),{},dimension='time',target_reached=False,
        shared_reached=True,shared_expired=True)
    assert 'exact independent review' in alert['text'] and 'verdict authority' in alert['text']
    assert 'deliver' not in alert['text'] and len(alert['text'].encode())<=notice.MAX_NOTICE_BYTES


@pytest.mark.parametrize('started,used,current,coverage,near,due',[
    (1,89,True,'live',False,False),(1,90,True,'live',True,False),
    (2,99,True,'live',True,False),(3,70,True,'live',False,False),
    (3,71,True,'live',False,True),(3,99,False,'live',False,False),
    (3,None,True,'live',False,False),(3,99,True,'unknown',False,False),
])
def test_context_advice_and_handoff_use_current_occupancy_after_compaction(started,used,current,coverage,near,due):
    observation=dict(context_windows=dict(coverage=coverage,windows_started=started),
        context=dict(status='last_reported',total_tokens=used,model_context_window=100,
            current_window=current,observed_at=123.))
    facts=dict(active_seconds=10000,target_seconds=900,fragment_enabled=True,
        max_context_windows=2,context_observation=observation,context_handoff_version=2)
    assert notice.worker_context_near(2,observation,version=2) is near
    assert notice.worker_progress_due(dict(role='W'),{},**facts) is due
    alert=notice.worker_progress_notice(dict(role='W'),{},**facts)
    assert (alert is not None) is (near or due)
    if alert:
        assert alert['text']==(notice.WORKER_CONTEXT_TEXT if due else notice.WORKER_CONTEXT_90_TEXT)
        assert len(alert['text'].encode())<=notice.MAX_NOTICE_BYTES
    for job,intent,extra in [(dict(role='A'),{},{}),(dict(role='W'),dict(output_repair=True),{}),
            (dict(role='W'),dict(work_repair=True),{}),(dict(role='W'),{},dict(author=True)),
            (dict(role='W'),{},dict(final_return=True))]:
        assert notice.worker_progress_notice(job,intent,**dict(facts,**extra)) is None


def test_absent_optional_provider_capability_does_not_schedule_context_handoffs():
    observation=dict(context_windows=dict(coverage='unknown',windows_started=3,
        reason='context_capability_unavailable'),context=dict(status='unavailable'))
    facts=dict(active_seconds=10000,target_seconds=900,fragment_enabled=True,
        max_context_windows=2,context_observation=observation,context_handoff_version=2)
    assert notice.worker_context_reason(2,observation,version=2) is None
    assert notice.worker_progress_notice(dict(role='W'),{},**facts) is None


def test_current_90_percent_context_notice_reaches_the_hook(tmp_path):
    observation=dict(context_windows=dict(coverage='live',windows_started=2),
        context=dict(status='last_reported',total_tokens=90,model_context_window=100,
            current_window=True,observed_at=123.))
    alert=notice.worker_progress_notice(dict(role='W'),{},active_seconds=10,
        target_seconds=900,fragment_enabled=True,max_context_windows=2,
        context_observation=observation,context_handoff_version=2)
    value=dict(scope=notice.SCOPE,updated_at_unix=123.,inflight_usage_unknown=True,
        target_state='near',closeout_notices=dict(progress=alert))
    save(tmp_path,value)
    assert notice.hook_output(tmp_path,0)==dict(hookSpecificOutput=dict(
        hookEventName='PostToolUse',additionalContext=notice.WORKER_CONTEXT_90_TEXT))
    assert notice.hook_output(tmp_path,1) is None


@pytest.mark.parametrize('observation',[None,dict(context_windows=dict(coverage='unknown'))])
def test_elapsed_time_without_current_context_never_requests_worker_handoff(observation):
    facts=dict(active_seconds=100000,target_seconds=900,fragment_enabled=True,
        context_observation=observation,context_handoff_version=2)
    assert notice.worker_progress_due(dict(role='W'),{},**facts) is False
    assert notice.worker_progress_notice(dict(role='W'),{},**facts) is None


@pytest.mark.parametrize('dimension',['token','time'])
@pytest.mark.parametrize('risk',[dict(target_reached=True),dict(target_near=True)])
def test_ordinary_worker_has_no_time_or_token_soft_target_notice(dimension,risk):
    facts=dict(target_reached=False,shared_reached=False,followup_tokens=dict(A=40))
    facts.update(risk)
    assert notice.closeout_notice(dict(role='W'),{},dimension=dimension,**facts) is None


def test_ordinary_worker_shared_token_near_notice_keeps_work_and_context_handoff_separate(tmp_path):
    assert notice.closeout_notice(dict(role='W'),{},dimension='time',target_reached=False,
        shared_reached=False,shared_risk=True) is None
    value=published(role='W',target=False,shared_risk=True)
    value['shared_observed_tokens_remaining_upper_bound']=100
    alert=value['closeout_notices']['token']
    assert alert['cause']=='shared' and 'cumulative tokens >=90% of configured total' in alert['text']
    assert 'No handoff required' in alert['text'] and 'unknown usage != 0' in alert['text']
    assert 'suggested' not in alert['text'] and len(alert['text'].encode())<=notice.MAX_NOTICE_BYTES
    save(tmp_path,value)
    assert notice.hook_output(tmp_path,0)['hookSpecificOutput']['additionalContext']==alert['text']
    assert notice.hook_output(tmp_path,1) is None
    value.update(target_state='near')
    value['closeout_notices']['progress']=dict(cause='node',text=notice.WORKER_CONTEXT_90_TEXT)
    save(tmp_path,value)
    assert notice.hook_output(tmp_path,2)['hookSpecificOutput']['additionalContext']==notice.WORKER_CONTEXT_90_TEXT
    assert notice.hook_output(tmp_path,3) is None


@pytest.mark.parametrize('dimension',['token','time'])
def test_ordinary_worker_true_shared_exhaustion_still_returns_originals(dimension):
    alert=notice.closeout_notice(dict(role='W'),{},dimension=dimension,
        target_reached=False,shared_reached=True,shared_expired=dimension=='time',
        shared_exhausted=True,followup_tokens=dict(A=40))
    assert alert['cause']=='shared' and f'Shared {dimension} budget reached.' in alert['text']
    assert 'No new work/replay' in alert['text'] and 'suggested' not in alert['text']


def test_exhausted_final_a_retains_exact_delivery_judgment():
    alert=notice.closeout_notice(dict(role='A',research_strategy_version=3,
        evidence_return=dict(kind='delivery_review')),{},dimension='time',
        target_reached=False,shared_reached=True,shared_expired=True)
    assert 'exact deliver or reject all with finish' in alert['text']
    assert 'No correction/new W/E/extra FINAL' in alert['text']
    assert len(alert['text'].encode())<=notice.MAX_NOTICE_BYTES


def test_worker_delivery_correction_keeps_its_bounded_target_advice():
    alert=notice.closeout_notice(dict(role='W',research_strategy_version=3,
        evidence_return=dict(kind='delivery_correction')),{},dimension='token',
        target_reached=False,target_near=True,shared_reached=False)
    assert 'save the exact correction/gaps' in alert['text']
    assert 'No scope expansion/replay' in alert['text']


def test_followup_notice_does_not_copy_unbounded_configured_numbers():
    alert=notice.closeout_notice(dict(role='H'),{},dimension='token',target_reached=False,shared_reached=False,
        shared_risk=True,followup_tokens=dict(W=10**500,A=10**500))
    assert 'suggested W+A token targets' in alert['text'] and 'No reservation/grant' in alert['text']
    assert len(alert['text'].encode())<=notice.MAX_NOTICE_BYTES


def test_untrusted_internal_hook_qualifies_only_when_all_other_hooks_are_preserved_and_trusted(tmp_path):
    declared=notice.definition(tmp_path,0)
    before=hook_metadata(tmp_path)
    candidate=hook_metadata(tmp_path,declared=declared)
    assert notice.validate_hook_trust(before,candidate,declared,tmp_path)
    admin=dict(key='admin',currentHash='sha256:'+'2'*64,enabled=True,isManaged=False,trustStatus='trusted')
    before['data'][0]['hooks'].append(admin)
    candidate['data'][0]['hooks'].append(copy.deepcopy(admin))
    assert notice.validate_hook_trust(before,candidate,declared,tmp_path)
    for status in ('untrusted','modified','unknown'):
        altered=copy.deepcopy(candidate);altered['data'][0]['hooks'][-1]['trustStatus']=status
        with pytest.raises(ValueError):notice.validate_hook_trust(before,altered,declared,tmp_path)
    removed=copy.deepcopy(candidate);removed['data'][0]['hooks'].pop()
    with pytest.raises(ValueError):notice.validate_hook_trust(before,removed,declared,tmp_path)
    disabled=copy.deepcopy(before);disabled['data'][0]['hooks'][0].update(enabled=False,trustStatus='untrusted')
    kept=copy.deepcopy(candidate);kept['data'][0]['hooks'][-1]=disabled['data'][0]['hooks'][0]
    assert notice.validate_hook_trust(disabled,kept,declared,tmp_path)


@pytest.mark.parametrize('mutation',[
    dict(command='another command'),dict(timeoutSec=60),dict(additionalContextLimit=2500),
    dict(eventName='preToolUse'),dict(matcher='Bash'),dict(enabled=False),dict(**{'async':True})])
def test_internal_hook_whole_definition_must_match(tmp_path,mutation):
    declared=notice.definition(tmp_path,0)
    candidate=hook_metadata(tmp_path,declared=declared)
    candidate['data'][0]['hooks'][0].update(mutation)
    with pytest.raises(ValueError):notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)


def test_warning_alone_qualifies_exact_hooks_but_duplicate_does_not(tmp_path):
    declared=notice.definition(tmp_path,0)
    candidate=hook_metadata(tmp_path,declared=declared)
    candidate['data'][0]['warnings']=['Hook configuration changed']
    assert notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)
    candidate['data'][0]['warnings']=[]
    candidate['data'][0]['hooks']*=2
    with pytest.raises(ValueError):notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)


def test_metadata_only_hooks_omit_context_limit_and_qualify_native_null(tmp_path):
    declared=notice.definition(tmp_path,0)
    assert declared['PostToolUse'][0]['hooks'][0]['additionalContextLimit']==0
    for name in ('PostModelRequest','PostCompact'):
        handler=declared[name][0]['hooks'][0]
        assert 'additionalContextLimit' not in handler
        assert handler['async'] is False and handler['timeout']==5
    candidate=hook_metadata(tmp_path,declared=declared)
    assert {hook['eventName']:hook['additionalContextLimit'] for hook in candidate['data'][0]['hooks']}==dict(
        postToolUse=0,postModelRequest=None,postCompact=None)
    assert notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)
    for event in ('postModelRequest','postCompact'):
        altered=copy.deepcopy(candidate)
        next(hook for hook in altered['data'][0]['hooks'] if hook['eventName']==event)['additionalContextLimit']=0
        with pytest.raises(ValueError,match='definition did not qualify'):
            notice.validate_hook_trust(hook_metadata(tmp_path),altered,declared,tmp_path)


def test_native_warning_for_old_metadata_context_declaration_remains_unqualified(tmp_path):
    # Observed Codex 0.166.0 hooks/list response: invalid zero limits are ignored,
    # projected as null, and accompanied by warnings on metadata-only events.
    declared=notice.definition(tmp_path,0)
    for name in ('PostModelRequest','PostCompact'):
        declared[name][0]['hooks'][0]['additionalContextLimit']=0
    candidate=hook_metadata(tmp_path,declared=declared)
    for hook in candidate['data'][0]['hooks']:
        if hook['eventName'] in ('postModelRequest','postCompact'):
            hook['additionalContextLimit']=None
    candidate['data'][0]['warnings']=[
        'ignoring additionalContextLimit for '+name+' hook in /<session-flags>/config.toml: '
        'this event cannot emit additionalContext' for name in ('PostModelRequest','PostCompact')]
    with pytest.raises(ValueError,match='definition did not qualify'):
        notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)
    candidate['data'][0]['warnings']=[]
    with pytest.raises(ValueError,match='definition did not qualify'):
        notice.validate_hook_trust(hook_metadata(tmp_path),candidate,declared,tmp_path)


def test_hook_inventory_and_launch_preserve_native_config_and_only_qualified_bypass(tmp_path):
    settings=backends.configuration('codex',['codex'],dict(workspace='workspace-write',network=False,
        web='disabled',subagents=False))
    backend=backends.selected(settings)
    args=backend.launch(dict(model='fixture',reasoning_effort='high'),tmp_path,tmp_path/'out',tmp_path/'schema','Original rules')
    declared=notice.definition(tmp_path,0)
    candidate=backend.budget_hook_args(args,declared)
    import tomllib
    for name in ('PostToolUse','PostModelRequest','PostCompact'):
        hook_config=next(candidate[index+1] for index,item in enumerate(candidate[:-1])
            if item=='-c' and candidate[index+1].startswith('hooks.'+name+'='))
        assert tomllib.loads(hook_config)['hooks'][name]==declared[name]
    assert '--dangerously-bypass-hook-trust' not in candidate and args[-1]=='-'
    final=backend.budget_hook_args(args,declared,trusted=True)
    assert '--dangerously-bypass-hook-trust' in final
    inventory=backend.hook_inventory_args(candidate,tmp_path)
    assert inventory[-2:]==['app-server','--stdio'] and '--ignore-user-config' not in inventory
    for index,item in enumerate(candidate[:-1]):
        if item=='-c':assert candidate[index+1] in inventory
    assert 'sandbox_mode="workspace-write"' in inventory and 'model="fixture"' in inventory
    assert not (tmp_path/'config.toml').exists()


@pytest.mark.parametrize('resumed',[False,True])
def test_hook_flags_preserve_resume_model_in_ordinary_and_resumed_exec(tmp_path,resumed):
    backend=backends.selected(backends.configuration('codex',['codex'],{}))
    profile=dict(model='resume')
    args=backend.launch(profile,tmp_path,tmp_path/'out',tmp_path/'schema','Original rules')
    if resumed:args,_=backend.resume(args,profile,tmp_path,tmp_path/'out',tmp_path/'schema',dict(thread_id='original-thread'))
    original=list(args)
    result=backend.budget_hook_args(args,notice.definition(tmp_path,0),trusted=True)
    assert args==original
    assert result[:2]==args[:2] and result[9:]==args[2:]
    assert result[result.index('--model')+1]=='resume'
    assert result.count('resume')==(2 if resumed else 1)
    assert result[2]=='-c' and result[8]=='--dangerously-bypass-hook-trust'


def test_untrusted_admin_hook_preflight_keeps_original_invocation_runnable_without_bypass(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from scidiscovery.research_loop import shared_input_views
    backend=backends.selected(backends.configuration('codex',['codex'],{}))
    args=backend.launch(dict(model='fixture'),tmp_path,tmp_path/'out',tmp_path/'schema','Original rules')
    declared=notice.definition(tmp_path,0)
    before=hook_metadata(tmp_path);after=hook_metadata(tmp_path,declared=declared)
    admin=dict(key='admin',currentHash='sha256:'+'2'*64,enabled=True,isManaged=False,trustStatus='untrusted')
    before['data'][0]['hooks'].append(copy.deepcopy(admin));after['data'][0]['hooks'].append(copy.deepcopy(admin))
    observations=iter([before,after])
    monkeypatch.setattr(notice,'list_hooks',lambda *args,**kwargs:next(observations))
    views=[]
    def wrapped(facts,argv):
        views.append(dict(facts['mounts']['mappings']))
        return list(argv)
    monkeypatch.setattr(shared_input_views,'wrap_from_facts',wrapped)
    monkeypatch.setattr(native.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=notice.VISIBLE.encode()+b'\n'))
    facts=dict(mounts=dict(mappings={}),worker_policy=None)
    home=tmp_path/'native-home';home.mkdir();config=home/'config.toml';config.write_text('original user config')
    (tmp_path/'work').mkdir();notice.stage_helper(tmp_path)
    assert (tmp_path/'work'/notice.EMPTY_CONFIG).read_bytes()==b''
    original=copy.deepcopy(args)
    result,channel=native._budget_notice_launch(backend,args,tmp_path,0,dict(CODEX_HOME=str(home)),facts,SimpleNamespace(remaining_seconds=lambda:5))
    assert result==original and args==original
    assert channel==dict(status='unavailable',reason='hook_trust_unqualified')
    assert '--dangerously-bypass-hook-trust' not in result
    assert before['data'][0]['hooks']==[admin]
    assert all(view[str(config)]==tmp_path/'work'/notice.EMPTY_CONFIG for view in views[1:3])
    assert str(config) not in facts['mounts']['mappings'] and str(config) not in views[-1]
    assert config.read_text()=='original user config'


@pytest.mark.parametrize('kind',['symlink','directory'])
def test_nonordinary_native_config_leaves_scientific_argv_unchanged(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    from scidiscovery.research_loop import shared_input_views
    backend=backends.selected(backends.configuration('codex',['codex'],{}))
    args=backend.launch(dict(model='fixture'),tmp_path,tmp_path/'out',tmp_path/'schema','Original rules')
    home=tmp_path/'native-home';home.mkdir();config=home/'config.toml'
    original=tmp_path/'original-config';original.write_text('Original native config')
    if kind=='symlink':config.symlink_to(original)
    else:config.mkdir()
    monkeypatch.setattr(shared_input_views,'wrap_from_facts',lambda facts,argv:list(argv))
    monkeypatch.setattr(native.subprocess,'run',lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout=notice.VISIBLE.encode()+b'\n'))
    monkeypatch.setattr(notice,'list_hooks',lambda *args,**kwargs:pytest.fail('Nonordinary config must not enter metadata RPC'))
    facts=dict(mounts=dict(mappings={}),worker_policy=None);diagnostics=[]
    result,channel=native._budget_notice_launch(backend,args,tmp_path,0,dict(CODEX_HOME=str(home)),facts,
        SimpleNamespace(remaining_seconds=lambda:5),diagnostic=diagnostics.append)
    assert result==args and '--ignore-user-config' in result and '--dangerously-bypass-hook-trust' not in result
    assert channel==dict(status='unavailable',reason='hook_preflight_unavailable')
    assert diagnostics and b'directly ordinary' in diagnostics[0]
    assert original.read_text()=='Original native config'
    assert config.is_symlink() if kind=='symlink' else config.is_dir()


@pytest.mark.parametrize('settings',[
    dict(native_backend='protocol',native_launcher_argv=['independent']),
    dict(native_backend='codex',native_launcher_argv=['wrapper','codex'],native_options={}),
    dict(native_backend='codex',native_launcher_argv=['codex'],native_options=None)])
def test_unsupported_launchers_cannot_claim_native_hook_channel(settings,tmp_path):
    backend=backends.selected(settings)
    if backend.name=='codex':
        with pytest.raises(ValueError):backend.budget_hook_args(['codex','exec','-'],notice.definition(tmp_path,0))
    assert notice.public_channel(dict(status='unavailable',reason='unsupported_backend'))['status']=='unavailable'


@pytest.mark.process_e2e
@pytest.mark.parametrize('terminal_event',['turn.completed','turn.failed','turn.interrupted'])
def test_native_terminal_retires_turn_input_before_late_receipt(tmp_path,monkeypatch,terminal_event):
    """Run the existing local launcher and real queue without a model call."""
    from scidiscovery.research_loop import accepted_budget,budget,execution,profiles,turn_input
    from test_node_budget import instance,policy
    from test_soft_node_budget import start_clock

    root=instance(tmp_path,limits=policy())
    job=c.build_job(root,1,'H',[])
    start_clock(root,'local terminal queue fixture')
    anchor=budget.accept_node(root,job)
    record=execution.origin(root,1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor,
        **profiles.binding(c.settings(root),job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    launcher=tmp_path/'native.py'
    code=launcher.read_text().replace("mode=config['mode']",
        "print(json.dumps({'type':'thread.started','thread_id':'11111111-2222-4333-8444-555555555555'}),flush=True)\n"
        "print(json.dumps({'type':'turn.started','turn_id':'fixture-turn'}),flush=True)\n"
        "mode=config['mode']")
    code=code.replace("'type':'turn.completed'","'type':config['terminal_event']")
    code=code.replace("(work/'work/ready').touch()",
        "print(json.dumps(config['late_receipt']),flush=True)\n(work/'work/ready').touch()")
    launcher.write_text(code)
    contexts=[];retired=[];late_receipts=[]
    def advice(identity):
        return dict(guidance_id=identity,revision=1,text='Retain the original evidence.',
            target='H',kind='advice',scope='research',effective_step=1,sequence=1,status='pending')
    def begin(self,root,job,work,attempt,*,task_ref,base):
        # The fake exec launcher supplies the same optional queue seam that a
        # supported live transport binds; all admission and receipt IO is real.
        context=turn_input.begin(root,job,work,attempt,task_ref=task_ref,base=base)
        assert context is not None
        turn_input.poll(context,thread='11111111-2222-4333-8444-555555555555',turn='fixture-turn')
        queued=turn_input.offer(root,advice('original-advice'))
        assert queued['turn_input']['status']=='queued'
        requests=c.read(context['inbox']/'inbox.json')
        assert len(requests)==1
        configuration=c.read(launcher.with_suffix('.json'))
        configuration.update(terminal_event=terminal_event,
            late_receipt=turn_input.Inbox.receipt(requests[0],'accepted'))
        c.write(launcher.with_suffix('.json'),configuration)
        contexts.append(context)
        return context
    retire=backends.Backend.retire_turn_input
    def retire_at_terminal(self,context):
        retire(self,context)
        assert context['accepting'] is False
        offered=turn_input.offer(root,advice('after-terminal'))
        assert offered['status']=='pending' and 'turn_input' not in offered
        assert len(c.read(context['inbox']/'inbox.json'))==1
        retired.append(True)
    poll=backends.Backend.poll_turn_input
    def poll_receipt(self,context,**kwargs):
        event=kwargs.get('event')
        if event is not None:
            assert retired and context['accepting'] is False
            late_receipts.append(event)
        return poll(self,context,**kwargs)
    monkeypatch.setattr(backends.Codex,'begin_turn_input',begin)
    monkeypatch.setattr(backends.Codex,'retire_turn_input',retire_at_terminal)
    monkeypatch.setattr(backends.Codex,'poll_turn_input',poll_receipt)
    if terminal_event=='turn.completed':
        outcome=native.run_native(root,1)
        assert outcome['terminal_observed'] and outcome['stop_reason'] is None
    else:
        with pytest.raises(RuntimeError,match='not a verified terminal result'):
            native.run_native(root,1)
        assert c.read(record/'native-outcome.json')['stop_reason']=='native_failure_event'
    assert len(contexts)==len(retired)==len(late_receipts)==1
    original=c.read(root/'host-control/guidance/original-advice.json')
    assert original['status']=='applied' and original['turn_input']['status']=='accepted'
    assert original['applied_step']==1 and original['applied_role']=='H'
    assert (contexts[0]['inbox']/'retired.json').exists()
    assert not (root/'host-control/guidance/after-terminal.json').exists()


@pytest.mark.process_e2e
def test_native_turn_input_end_error_still_cleans_original_process(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget,budget,execution,profiles,turn_input
    from test_node_budget import instance,policy
    from test_soft_node_budget import start_clock

    root=instance(tmp_path,limits=policy(),mode='hold')
    job=c.build_job(root,1,'H',[])
    start_clock(root,'local queue cleanup failure fixture')
    anchor=budget.accept_node(root,job)
    record=execution.origin(root,1)
    intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor,
        **profiles.binding(c.settings(root),job))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    def begin(self,*args,**kwargs):return turn_input.begin(*args,**kwargs)
    refusal=ValueError('Fixture queue writer lease changed')
    def refused_end(self,context):
        assert context is not None
        raise refusal
    stop_node=budget.stop_node
    def stop_after_observation(root,job,intent,limits,meter):
        return 'fixture_stop' if meter['input_tokens'] else stop_node(root,job,intent,limits,meter)
    cleaned=[]
    cleanup=native.runtime.cleanup_owned_group
    def cleanup_original(identity):
        result=cleanup(identity)
        cleaned.append(result)
        return result
    monkeypatch.setattr(backends.Codex,'begin_turn_input',begin)
    monkeypatch.setattr(backends.Codex,'end_turn_input',refused_end)
    monkeypatch.setattr(budget,'stop_node',stop_after_observation)
    monkeypatch.setattr(native.runtime,'cleanup_owned_group',cleanup_original)
    with pytest.raises(ValueError,match='queue writer lease changed') as failure:
        native.run_native(root,1)
    assert failure.value is refusal
    outcome=c.read(record/'native-outcome.json')
    assert outcome['native_started'] and outcome['returncode']<0
    assert outcome['stop_reason']=='native_error_unknown'
    assert outcome['cleanup']=='observed_group_cleaned' and cleaned==['observed_group_cleaned']
    assert (c.workspace(root,1)/'work/launches.txt').read_text().splitlines()==['launch']
    assert c.read(record/'usage.json')['input_tokens']==7


@pytest.mark.process_e2e
@pytest.mark.parametrize(('role','shared'),[('H',False),('A',True),('W',False)])
def test_real_codex_post_tool_notice_preserves_tool_output_in_final_scientific_namespace(tmp_path,monkeypatch,role,shared):
    """Local Responses fixture only: no external model, authentication or billing."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from scidiscovery.research_loop import accepted_budget,budget,controller,execution,profiles
    codex=shutil.which('codex')
    if codex is None or shutil.which('bwrap') is None:pytest.skip('Real installed Codex and bwrap are required')
    requests=[]
    expected_tool='get_goal'
    expected_result=dict(goal=None,remainingTokens=None,completionBudgetReport=None)
    final=dict(summary='Fixture completed',scientific_result='Tool output and closeout context received',limitations=['Transport fixture'],
        next_action='continue_w' if role=='A' else 'wait',route_reason='No scientific claim')
    if role=='A':final['decision_request']=None
    if role=='W':
        final.pop('next_action');final.pop('route_reason');final['stage_intent']='handoff'

    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=1_000_000:
                self.send_error(400);return
            request=json.loads(self.rfile.read(size))
            requests.append(request)
            number=len(requests)
            if number>2:
                self.send_error(400,'Only two deterministic fixture requests are supported');return
            if number==1:
                def functions(tools):
                    for tool in tools:
                        if tool.get('type')=='namespace':yield from functions(tool.get('tools',[]))
                        else:yield tool
                offered={tool.get('name'):tool for tool in functions(request.get('tools',[]))}
                if os.environ.get('SCID_TEST_LOG_DIR'):
                    report=Path(os.environ['SCID_TEST_LOG_DIR']);report.mkdir(parents=True,exist_ok=True)
                    (report/'budget-notice-offered-tools.json').write_text(json.dumps(
                        dict(offered_tool_names=sorted(str(name)[:96] for name in offered if name is not None)[:128]),indent=2))
                if expected_tool not in offered:
                    self.send_error(400,'Fixture requires actually offered read-only get_goal tool');return
                arguments={}
                item=dict(type='function_call',id='fc_fixture',call_id='call_fixture',name=expected_tool,arguments=json.dumps(arguments))
            else:
                item=dict(type='message',id='msg_fixture',role='assistant',status='completed',
                    content=[dict(type='output_text',text=json.dumps(final),annotations=[])])
            response=dict(id=f'resp_fixture_{number}',object='response',created_at=0,status='completed',model='fixture',output=[item],
                usage=dict(input_tokens=10,output_tokens=2,total_tokens=12))
            events=[dict(type='response.created',response=dict(response,status='in_progress',output=[])),
                dict(type='response.output_item.added',output_index=0,item=dict(item,arguments='') if number==1 else dict(item,content=[]))]
            if number==1:
                events.extend([dict(type='response.function_call_arguments.delta',item_id=item['id'],output_index=0,delta=item['arguments']),
                    dict(type='response.function_call_arguments.done',item_id=item['id'],output_index=0,arguments=item['arguments'])])
            else:
                part=item['content'][0]
                events.extend([dict(type='response.content_part.added',item_id=item['id'],output_index=0,content_index=0,part=dict(part,text='')),
                    dict(type='response.output_text.delta',item_id=item['id'],output_index=0,content_index=0,delta=part['text']),
                    dict(type='response.output_text.done',item_id=item['id'],output_index=0,content_index=0,text=part['text']),
                    dict(type='response.content_part.done',item_id=item['id'],output_index=0,content_index=0,part=part)])
            events.extend([dict(type='response.output_item.done',output_index=0,item=item),dict(type='response.completed',response=response)])
            payload=''.join('event: '+event['type']+'\ndata: '+json.dumps(event)+'\n\n' for event in events).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(payload)));self.end_headers()
            self.wfile.write(payload)

    provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    thread=threading.Thread(target=provider.serve_forever,daemon=True);thread.start()
    home=tmp_path/'native-home';home.mkdir()
    original_config='invalid_native_user_config = [\n'
    (home/'config.toml').write_text(original_config)
    monkeypatch.setenv('CODEX_HOME',str(home))
    for key in ('OPENAI_API_KEY','OPENAI_BASE_URL'):monkeypatch.delenv(key,raising=False)
    launch=backends.Codex.launch
    def fixture_provider(self,*args,**kwargs):
        argv=launch(self,*args,**kwargs)
        config=dict(name='scid-budget-local-fixture',base_url=f'http://127.0.0.1:{provider.server_port}/v1',
            wire_api='responses',requires_openai_auth=False)
        argv[-1:-1]=['-c','model_provider="scid_budget_fixture"',
            '-c','model_context_window=1000',
            '-c','model_providers.scid_budget_fixture='+backends._toml(config)]
        return argv
    monkeypatch.setattr(backends.Codex,'launch',fixture_provider)
    public=tmp_path/'public';public.mkdir();goal=public/'goal';goal.write_text('Deterministic budget notice fixture')
    root=tmp_path/'instance'
    controller.prepare(root,goal,public,[codex],native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False),
        model='fixture',reasoning_effort='high',max_tokens=10000,max_seconds=60,max_steps=3,search_policy=None,
        experiment_mode='external',node_budget_policy={role:dict(max_observed_tokens=1,max_seconds=30) for role in 'HWAE'})
    job=c.build_job(root,1,role,[])
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Local provider fixture')
    anchor=budget.accept_node(root,job)
    record=execution.origin(root,1)
    native_intent=dict(task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=anchor,
        **profiles.binding(c.settings(root),job))
    c.write(record/'native-intent.json',native_intent)
    accepted_budget.accept_native(root,1,record,native_intent)
    hint=published(role=role,shared=shared,target=role!='W',shared_risk=role=='W')
    if role=='W':hint['shared_observed_tokens_remaining_upper_bound']=1000
    write_hint=budget.write_node_hint
    def reached(*args,**kwargs):
        write_hint(*args,**kwargs)
        # Only this published current hint changes; the hook receives no
        # controller/history facts and the native invocation remains accepted.
        save(c.workspace(root,1),hint)
    monkeypatch.setattr(budget,'write_node_hint',reached)
    try:
        outcome=native.run_native(root,1)
    finally:
        provider.shutdown();provider.server_close();thread.join(timeout=2)
        if os.environ.get('SCID_TEST_LOG_DIR'):
            report=Path(os.environ['SCID_TEST_LOG_DIR']);report.mkdir(parents=True,exist_ok=True)
            for source,destination,limit,tail in (
                ('native.stderr','budget-notice-native.stderr',4096,True),
                ('native.jsonl','budget-notice-native.jsonl',4096,True),
                ('native-outcome.json','budget-notice-native-outcome.json',16384,False)):
                try:
                    with (record/source).open('rb') as stream:
                        if tail:stream.seek(0,os.SEEK_END);stream.seek(max(0,stream.tell()-limit))
                        (report/destination).write_bytes(stream.read(limit))
                except OSError:pass
    assert outcome['returncode']==0 and outcome['stop_reason'] is None
    assert outcome['budget_notification']==dict(status='available',mechanism='codex_post_tool_use'), (record/'native.stderr').read_text()[-2000:]
    identity=backends.participant_provider(outcome['native_identity']).validate_participant_event(
        outcome['native_thread_event'],outcome,native_intent,c.workspace(root,1))
    assert identity==('codex_exec',outcome['thread_id'])
    observation=c.read(record/'usage-observation.json')
    current=observation['context']
    assert current['status']=='last_reported' and current['current_window'] is True
    # Codex reports its effective window (95% of the configured capacity).
    assert current['model_context_window']==950 and current['total_tokens']==12
    assert current['ratio']==12/950
    assert len(requests)==2
    packet=requests[1]
    text=hint['closeout_notices']['token']['text']
    developer=[item for item in packet['input'] if item.get('role')=='developer']
    if os.environ.get('SCID_TEST_LOG_DIR'):
        report=Path(os.environ['SCID_TEST_LOG_DIR'])/'budget-notice-requests.json'
        report.parent.mkdir(parents=True,exist_ok=True)
        contexts=[item for item in developer if text in json.dumps(item)]
        report.write_text(json.dumps(dict(notice=text,notice_utf8_bytes=len(text.encode()),
            notice_developer_items=contexts,request_count=len(requests),
            tool_name=expected_tool,
            tool_outputs=[str(item.get('output',''))[:8192] for item in packet['input'] if item.get('type')=='function_call_output'],
            tool_outputs_truncated=any(len(str(item.get('output','')))>8192 for item in packet['input'] if item.get('type')=='function_call_output'),
            expected_result_preserved=any(json.loads(item['output'])==expected_result
                for item in packet['input'] if item.get('type')=='function_call_output')),
            indent=2))
    assert any(text in json.dumps(item) for item in developer)
    assert [part['text'] for item in developer for part in item.get('content',[])
        if part.get('type')=='input_text' and text in part.get('text','')]==[text]
    outputs=[item for item in packet['input'] if item.get('type')=='function_call_output']
    assert len(outputs)==1 and isinstance(outputs[0]['output'],str) and outputs[0]['output'].strip(),outputs
    assert json.loads(outputs[0]['output'])==expected_result and text not in outputs[0]['output'],outputs
    if role=='A':assert_ordinary_a_returns_advice(text)
    if role=='W':assert 'cumulative tokens >=90% of configured total' in text
    assert c.read(record/'science-output.json')==final
    assert (home/'config.toml').read_text()==original_config
    assert observation['tokens']['input_tokens']+observation['tokens']['output_tokens']==24
