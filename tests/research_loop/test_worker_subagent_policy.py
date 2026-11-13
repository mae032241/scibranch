"""Role-frozen native subagents: engineering fixtures, never provider/model runs."""
import copy
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import backends, continuation, contracts as c, controller
from scidiscovery.research_loop import native, profiles, research_config
# Registers source-lane execution interfaces; codex instances default to local execution.
from execution_fixtures import execution_registry  # noqa: F401
from test_consultation_continuity import PROFILE, original
from test_consultation_continuity_process import FAKE_NATIVE, invoke


def prepared(tmp_path, *, backend='codex', options=None, roles=None, workers=None):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Investigate a bounded public question.')
    launcher=tmp_path/('codex' if backend=='codex' else 'protocol-fixture')
    launcher.write_text('#!'+sys.executable+'\nraise SystemExit("Never launch this source fixture")\n')
    launcher.chmod(0o700)
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[str(launcher)],native_backend=backend,
        native_options=options,role_profiles=roles,worker_profiles=workers,model=PROFILE['model'],
        reasoning_effort=PROFILE['reasoning_effort'],service_tier=PROFILE['service_tier'],
        max_tokens=10000,max_seconds=100,max_steps=20,research_strategy_version=3)
    return root,c.settings(root)


@pytest.mark.parametrize('options,roles,expected',[
    (None,None,True),
    (dict(subagents=False),None,False),
    (dict(subagents=True),None,True),
    (None,dict(W=dict(subagents=False)),False),
    (dict(subagents=False),dict(W=dict(subagents=True)),True),
])
def test_new_supported_worker_freezes_exact_role_permission_and_roundtrips(tmp_path,options,roles,expected):
    root,settings=prepared(tmp_path,options=options,roles=roles)
    assert settings['role_profiles']['W']['subagents'] is expected
    model=profiles.selected(settings,'W')
    assert model==PROFILE and 'subagents' not in model
    job=dict(role='W',native_profile=model,native_subagents=expected)
    before=copy.deepcopy(settings)
    binding=profiles.binding(settings,job)
    assert binding==dict(native_profile=PROFILE,native_subagents=expected)
    assert profiles.validate_intent(settings,job,binding)==PROFILE
    backend=backends.selected(settings).for_job(job)
    assert backend.permissions(settings)['effective']['subagents'] is expected
    document=research_config.to_document(root,settings)
    assert document['agent']['roles']==dict(W=dict(subagents=expected))
    decoded=research_config.from_document(document,base=root)
    retained=controller.prepare(**decoded,validate_only=True,_existing_settings=settings)
    assert retained['role_profiles']['W']['subagents'] is expected
    changed=copy.deepcopy(document);changed['agent']['model']='changed-global-model'
    updated=controller.prepare(**research_config.from_document(changed,base=root),
        validate_only=True,_existing_settings=settings)
    assert profiles.selected(updated,'W')['model']=='changed-global-model'
    assert profiles.selected(updated,'H')['model']=='changed-global-model'
    assert settings==before


def test_explicit_worker_model_survives_global_edit_while_role_permission_roundtrips(tmp_path):
    root,settings=prepared(tmp_path,roles=dict(W=dict(model='explicit-role-model',subagents=False)))
    document=research_config.to_document(root,settings)
    assert document['agent']['roles']==dict(W=dict(model='explicit-role-model',subagents=False))
    document['agent']['model']='changed-global-model'
    updated=controller.prepare(**research_config.from_document(document,base=root),
        validate_only=True,_existing_settings=settings)
    assert profiles.selected(updated,'W')['model']=='explicit-role-model'
    assert profiles.selected(updated,'H')['model']=='changed-global-model'
    assert updated['role_profiles']['W']['subagents'] is False


def test_unsupported_default_keeps_small_work_with_worker_and_explicit_true_is_rejected(tmp_path):
    root,settings=prepared(tmp_path,backend='protocol')
    assert backends.selected(settings).subagent_capability()['status']=='unsupported'
    assert settings.get('role_profiles') is None
    assert profiles.native_subagents(settings,dict(role='W')) is None
    document=research_config.to_document(root,settings)
    assert 'subagents' not in document.get('agent',{}).get('roles',{}).get('W',{})
    denied=tmp_path/'denied';denied.mkdir()
    with pytest.raises(ValueError,match='[Ss]ubagent'):
        prepared(denied,backend='protocol',roles=dict(W=dict(subagents=True)))
    assert 'subagent_capability' not in backends.Backend.__abstractmethods__


@pytest.mark.parametrize('direct',[False,True])
def test_named_worker_subagents_false_is_retained_unless_role_explicitly_overrides(tmp_path,direct):
    role=dict(worker='named')
    if direct:role['subagents']=True
    root,settings=prepared(tmp_path,roles=dict(W=role),workers=dict(named=dict(subagents=False)))
    assert settings['role_profiles']['W']['subagents'] is direct
    job=c.build_job(root,1,'W',[])
    assert job['native_subagents'] is direct and job['native_profile']==PROFILE
    assert backends.selected(settings).for_job(job).permissions(settings)['effective']['subagents'] is direct


def test_unsupported_named_worker_subagents_true_is_rejected_at_preparation(tmp_path):
    with pytest.raises(ValueError,match='[Ss]ubagent'):
        prepared(tmp_path,backend='protocol',roles=dict(W=dict(worker='named')),
            workers=dict(named=dict(subagents=True)))
    assert not (tmp_path/'instance/settings.json').exists()


def test_existing_unsupported_configuration_submit_cannot_enable_worker_subagents(tmp_path):
    from scidiscovery.research_loop import configuration_updates
    root,settings=prepared(tmp_path,backend='protocol')
    original_settings=(root/'settings.json').read_bytes()
    document=research_config.to_document(root,settings)
    document['agent']['roles']=dict(W=dict(subagents=True))
    with pytest.raises(ValueError,match='[Ss]ubagent'):
        configuration_updates.submit(root,document,expected_revision=0)
    assert configuration_updates.revisions(root)==[]
    assert (root/'settings.json').read_bytes()==original_settings
    assert c.settings(root)==settings


def test_role_permission_is_frozen_separately_and_other_roles_keep_original_default(tmp_path):
    root,settings=prepared(tmp_path)
    for role in ('H','A','E'):
        job=dict(role=role,native_profile=profiles.selected(settings,role))
        assert profiles.native_subagents(settings,job) is None
        backend=backends.selected(settings).for_job(job)
        assert backend.permissions(settings)['effective']['subagents'] is False
    job=dict(role='W',native_profile=PROFILE,native_subagents=True)
    intent=profiles.binding(settings,job)
    for broken in (dict(job,native_subagents=False),dict(job,native_subagents=1),
            {key:value for key,value in job.items() if key!='native_subagents'}):
        with pytest.raises(ValueError,match='[Ss]ubagent'):
            profiles.binding(settings,broken)
    for broken in (dict(intent,native_subagents=False),dict(intent,native_subagents=1),
            {key:value for key,value in intent.items() if key!='native_subagents'}):
        with pytest.raises(ValueError,match='[Ss]ubagent'):
            profiles.validate_intent(settings,job,broken)
    legacy=copy.deepcopy(settings)
    legacy['role_profiles']['W'].pop('subagents')
    legacy.pop('role_profile_declarations',None)
    legacy_job=dict(role='W',native_profile=PROFILE)
    assert profiles.binding(legacy,legacy_job)==dict(native_profile=PROFILE)
    assert backends.selected(legacy).for_job(legacy_job).permissions(legacy)['effective']['subagents'] is False
    exported=research_config.to_document(root,legacy)
    restored=controller.prepare(**research_config.from_document(exported,base=root),
        validate_only=True,_existing_settings=legacy)
    assert 'subagents' not in restored['role_profiles']['W']
    with pytest.raises(ValueError,match='[Ss]ubagent'):
        profiles.binding(legacy,job)
    for invalid in (None,0,1,'true'):
        with pytest.raises(ValueError,match='[Ss]ubagent'):
            profiles.normalize(dict(W=dict(subagents=invalid)),PROFILE)


@pytest.mark.parametrize('repair',[False,True])
def test_real_prepared_default_worker_subagents_contract_fits_existing_wire_budget(tmp_path,repair):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    from test_result_schema import rendered_contract
    root,settings=prepared(tmp_path)
    job=c.build_job(root,1,'W',[])
    assert job['native_subagents'] is True and job['native_profile']==PROFILE
    assert profiles.binding(settings,job)==dict(native_profile=PROFILE,native_subagents=True)
    task=c.response_instructions(job)
    intent=profiles.binding(settings,job)
    if repair:intent['output_repair']=OutputRepairRequired('fixture','D'*512).request()
    backend=backends.selected(settings,schema_transport='prompt').for_job(job)
    size,prompt,instruction,packet=rendered_contract(tmp_path,
        dict(job,scientific_work_unit=job.get('scientific_work_unit','')),task,intent,backend,
        remaining={} if repair else None,
        budget_status=dict(shared_seconds_remaining=0,shared_observed_tokens_remaining=0))
    assert size<=c.MAX_FIXED_RESPONSE_BYTES==3072,(repair,size)
    assert 'subagent' in (json.dumps(task)+prompt+instruction).lower()
    assert backends.selected(settings).permissions(settings)['effective']['subagents'] is False
    assert backend.permissions(settings)['effective']['subagents'] is True


@pytest.mark.parametrize('changed',[False,True])
def test_subagent_permission_preserves_main_thread_resume_or_reports_configuration_change(tmp_path,changed):
    root,job,settings,session,state=original(tmp_path)
    source=root/'native-evidence/001/native-intent.json'
    intent=c.read(source);intent['native_subagents']=True;c.write(source,intent)
    job['native_subagents']=not changed
    backend=backends.selected(settings).for_job(job)
    before=source.read_bytes()
    plan=continuation.consultation_launch(root,job,settings=settings,backend=backend,profile=PROFILE)
    if changed:
        assert plan['worker_continuation']['mode']=='new_context'
        assert plan['worker_continuation']['fallback_reason']=='consultation_resume_profile_changed'
        assert 'session' not in plan
    else:
        assert plan['worker_continuation']['mode']=='resume_requested' and plan['session']==session
        assert backend.complete_authorship is False
        assert backend.authorship_scope(dict(participant_scope='unsupported')) is False
        with pytest.raises(ValueError,match='authorship'):
            backend.authorship_proof_kind(dict(participant_scope='unsupported'))
    assert source.read_bytes()==before


@pytest.mark.process_e2e
def test_actual_backend_fake_rpc_enables_worker_only_and_resumes_main_thread_with_unknown_child_usage(tmp_path):
    root,job,settings,session,state=original(tmp_path)
    launcher=tmp_path/'codex';launcher.write_text('#!'+sys.executable+'\n'+FAKE_NATIVE);launcher.chmod(0o700)
    settings=dict(settings,native_launcher_argv=[str(launcher)])
    source=root/'native-evidence/001/native-intent.json'
    original_intent=c.read(source);original_intent['native_subagents']=True;c.write(source,original_intent)
    job['native_subagents']=True
    worker=backends.selected(settings).for_job(job)
    plan=continuation.consultation_launch(root,job,settings=settings,backend=worker,profile=PROFILE)
    assert plan['worker_continuation']['mode']=='resume_requested'
    consumed=continuation.consultation_intent(job,plan,settings=settings,backend=worker)
    tokens=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    result=invoke(worker,PROFILE,c.workspace(root,3),Path(session['home']),thread=session['thread_id'],
        turn='successor-turn',tokens=tokens,compactions=1,session=consumed['session'],
        context_state=consumed['worker_context_state'])
    assert result['code']==0,result['errors']
    assert [row['method'] for row in result['requests']]==['initialize','initialized','thread/resume','turn/start']
    assert 'features.multi_agent=true' in result['launch']['argv']
    assert result['requests'][2]['params']['threadId']==session['thread_id']
    assert result['observation']['tokens']==tokens
    assert result['observation']['usage_scope']=='native_main_thread'
    assert result['observation']['subagent_usage_coverage']=='unknown'
    identity=worker.identity(result['meter'],session['thread_id'])
    assert identity['participant_scope']=='unsupported' and worker.complete_authorship is False
    advisor=backends.selected(settings).for_job(dict(role='A'))
    advice=invoke(advisor,PROFILE,c.workspace(root,2),Path(session['home']),thread='independent-advisor',
        turn='advice-turn',tokens=tokens,compactions=0)
    assert advice['code']==0,advice['errors']
    assert 'features.multi_agent=false' in advice['launch']['argv']
    assert [row['method'] for row in advice['requests']]==['initialize','initialized','thread/start','turn/start']


def test_child_usage_unknown_cannot_become_a_complete_cycle_sample(tmp_path,monkeypatch):
    record=tmp_path/'attempt';record.mkdir()
    job=tmp_path/'jobs/001.json';job.parent.mkdir();c.write(job,dict(role='W',step=1))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(job)))
    c.write(record/'native-outcome.json',{})
    c.write(record/'usage.json',dict(input_tokens=10,cached_input_tokens=2,output_tokens=3))
    outcome=dict(returncode=0,metering=dict(coverage='terminal',subagent_usage_coverage='unknown',
        reporting_delay_unmeasured=False,inflight_usage_unknown=False))
    monkeypatch.setattr(native.execution,'records',lambda root,step:[record])
    monkeypatch.setattr(c,'native_outcome',lambda record:outcome)
    assert native._complete_reported_tokens(tmp_path,1)==(None,'subagent_usage_unreported')
