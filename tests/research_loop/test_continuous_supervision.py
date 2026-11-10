"""A continuous W yields for independent direction review at real policy cuts."""
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, budget_notice, checkpoint, contracts, execution,
    experiments, protocol, supervision,
)
from current_strategy_fixtures import budget_facts


def review(state=None, fraction=.3, *, job=None, intent=None):
    return supervision.worker_review(job or dict(role='W'), intent or {},
        summary=state if state is not None else supervision.authored_question_revision(supervision.empty(),True),
        budget_fraction=fraction, selected_policy=supervision.DEFAULT_POLICY, budget_facts=budget_facts(fraction))










@pytest.mark.parametrize('job,intent',[
    (dict(role='A'),{}), (dict(role='H'),{}),
    (dict(role='W',formal_review=True),{}),
    (dict(role='W',engineering_repair=True),{}),
    (dict(role='W',purpose='worker_consultation'),{}),
    (dict(role='W',purpose='closeout'),{}),
    (dict(role='W',final_only=True),{}),
    (dict(role='W',final_report=True),{}),
    (dict(role='W'),dict(output_repair=True)),
    (dict(role='W'),dict(work_repair=True)),
    (dict(role='W'),dict(continuation_kind='author_closeout')),
    (dict(role='W'),dict(budget_slice=dict(final_return={}))),
])
def test_direction_checkpoint_preserves_special_scopes(job,intent):
    assert review(job=job,intent=intent) is None


def test_one_current_cut_publishes_both_admission_pause_and_w_advice(tmp_path,monkeypatch):
    settings=dict(managed_admission_version=experiments.ADMISSION_VERSION,
        max_tokens=1000,max_seconds=1000,search_policy={},
        supervision_policy=supervision.DEFAULT_POLICY,
        supervision_budget_basis=dict(max_tokens=1000,max_seconds=1000),
        worker_fragment_policy=dict(context='same_session',max_context_windows=2,context_handoff_version=2))
    job=dict(step=1,role='W',managed_admission_version=experiments.ADMISSION_VERSION)
    contracts.write(tmp_path/'jobs/001.json',job)
    task=contracts.record_ref(tmp_path/'jobs/001.json')
    record=tmp_path/'native-record';record.mkdir()
    contracts.write(record/'native-intent.json',dict(task_ref=task))
    contracts.write(record/'usage.json',dict(input_tokens=300,output_tokens=0))
    work=tmp_path/'node';(work/'work').mkdir(parents=True)
    class Head(dict):
        base=settings
    head=Head(search=dict(supervision=supervision.authored_question_revision(supervision.empty(),True),last_w=None))
    def forbidden(*args,**kwargs):pytest.fail('Advice consumer reopened its producer facts')
    monkeypatch.setattr(accepted_budget,'read',forbidden)
    monkeypatch.setattr(accepted_budget,'usage',forbidden)
    monkeypatch.setattr(budget,'_snapshot',forbidden)
    monkeypatch.setattr(budget,'node_mode',lambda root,**kwargs:'soft')
    monkeypatch.setattr(budget,'node_limits',lambda root,job,**kwargs:None)
    monkeypatch.setattr(budget,'delivery_reserve',lambda settings:None)
    monkeypatch.setattr(contracts,'settings',forbidden)
    monkeypatch.setattr(contracts,'workspace',lambda root,step:work)
    monkeypatch.setattr(protocol,'require_current',lambda *args:None)
    monkeypatch.setattr(protocol,'stage_scope',lambda root,job:dict(stage='original'))
    monkeypatch.setattr(execution,'record',lambda root,step:record)
    monkeypatch.setattr(execution,'records',lambda root,step:[record])
    monkeypatch.setattr(execution,'identity',lambda root,step:'original-native')
    monkeypatch.setattr(checkpoint,'enabled_for_policy',lambda job,**kwargs:True)
    observation=dict(context_windows=dict(coverage='live',windows_started=1))
    facts=dict(settings=settings,head=head,stamp=budget.clock_stamp(),task=task,
        scope=dict(stage='original'),native_attempt=dict(number=0,execution_key='original-native'),
        final_return=None,closeout_preparation_seconds=None,
        shared=dict(observed_tokens=300,accounted_tokens=300,unknown_steps=[]))
    limits=dict(max_tokens=1000,max_seconds=1000,seconds_remaining=1000,consumed_seconds=0,advance_seconds=0)
    budget.write_node_hint(tmp_path,job,dict(task_ref=task),limits,
        dict(input_tokens=300,cached_input_tokens=0,output_tokens=0),facts=facts,observed=True,
        context_observation=observation)
    published=contracts.read(tmp_path/'host-runtime/experiment-control/001/review.json')
    hint=contracts.read(work/'work/budget-status.json')
    assert published['pending'] and published['reason']=='worker_direction_review_due'
    assert published['direction_review']==hint['direction_review']
    assert hint['direction_review']['cutoff']['total_rounds']==0
    assert hint['worker_context_windows']['windows_started']==1
    assert hint['closeout_notices']['review']['text']==budget_notice.WORKER_REFLECTION_TEXT
    assert 'independent A' in hint['instruction'] and 'never replay' in hint['instruction']
    assert len((work/'work/budget-status.json').read_bytes())<=4096


def test_live_usage_reducer_never_reopens_current_meter_or_accepted_rows(monkeypatch):
    class Head(dict):
        @property
        def current_job(self):return self['current_job']
        def row(self,key):pytest.fail('Pure usage reducer reopened an accepted row')
    head=Head(usage=dict(input_tokens=100,cached_input_tokens=20,output_tokens=10,
        unknown_count=0,first_unknown_step=None),current_job=dict(step=1),closeout=None,
        current_native=dict(step=1,record='native-evidence/001'))
    def forbidden(*args,**kwargs):pytest.fail('Pure usage reducer reopened a file')
    monkeypatch.setattr(contracts,'read',forbidden)
    result=accepted_budget.usage_from_facts(head,job_rows={1:dict(unknown_count=0)},
        current_native=dict(record='native-evidence/001',
            meter=dict(input_tokens=200,cached_input_tokens=50,output_tokens=30),
            metering=dict(coverage='live'),outcome_present=False,interrupted=False))
    assert result['observed_tokens']==340 and result['cached_input_tokens']==70
    assert result['unknown_steps']==[]


def test_advice_duration_projects_the_accepted_anchor_without_reacceptance(monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('Advice repeated native budget acceptance')
    monkeypatch.setattr(budget,'_node_duration',forbidden)
    monkeypatch.setattr(contracts,'record_ref',forbidden)
    monkeypatch.setattr(contracts,'read',forbidden)
    assert budget._advice_duration(dict(budget_slice=dict(advance_seconds=10,previous_seconds=5)),
        dict(advance_seconds=25))==20


def test_direction_notice_is_independent_of_early_context_advice_marker(tmp_path):
    work=tmp_path/'work';work.mkdir()
    path=work/'budget-status.json'
    hint=dict(scope=budget_notice.SCOPE,updated_at_unix=123.,inflight_usage_unknown=True,
        target_state='near',closeout_notices=dict(progress=dict(cause='node',
            text=budget_notice.WORKER_CONTEXT_NEAR_TEXT)))
    path.write_text(json.dumps(hint))
    assert budget_notice.hook_output(tmp_path,0)['hookSpecificOutput']['additionalContext']==budget_notice.WORKER_CONTEXT_NEAR_TEXT
    hint['target_state']='reached'
    hint['direction_review']=review()
    hint['closeout_notices']['review']=budget_notice.worker_direction_notice(hint['direction_review'])
    path.write_text(json.dumps(hint))
    assert budget_notice.hook_output(tmp_path,1)['hookSpecificOutput']['additionalContext']==budget_notice.WORKER_REFLECTION_TEXT
    assert budget_notice.hook_output(tmp_path,2) is None
    assert (work/'.budget-notice-progress').exists() and (work/'.budget-notice-review').exists()


def test_real_admission_refuses_new_work_but_cli_can_query_and_collect_original(tmp_path,monkeypatch,capsys):
    """Exercise Records' real review gate and the original-operation CLI seam."""
    from scidiscovery.research_loop import experiment_client, feedback, instance_owner, managed_wait, replay_check
    call_id='0001-'+'a'*32
    directory=tmp_path/'review';directory.mkdir()
    (directory/'accept.lock').touch(mode=0o444)
    records_path=tmp_path/'records';records_path.mkdir()
    scope=dict(step=1,role='W',handoff_protocol=protocol.CURRENT)
    task=dict(path='jobs/001.json')
    attempt=dict(number=0,execution_key=task)
    config=dict(record_dir=str(records_path),scope=scope,native_attempt=attempt,max_seconds=10,
        admission=dict(version=experiments.ADMISSION_VERSION,mode='soft',directory=str(directory),stage_anchor=task),
        execution_interface=dict(executor='fixture:original'))
    stamp=budget.clock_stamp()
    contracts.write(directory/'review.json',dict(scope=scope,stage_anchor=task,native_attempt=attempt,
        boot_id=stamp['boot_id'],deadline_boottime=None,valid_until_boottime=stamp['at_boottime']+100,
        pending=True,reason='worker_direction_review_due'))
    config_path=tmp_path/'experiment-config.json';contracts.write(config_path,config)
    source=tmp_path/'input.py';source.write_text('print("fixture")\n')
    payload=SimpleNamespace(local_path=str(source))
    operations=[]
    class OriginalProvider:
        def prepare(self,*args,**kwargs):pytest.fail('Direction checkpoint admitted new preparation')
        def status_details(self,identity):
            assert identity==call_id;operations.append('query')
            return dict(call_id=identity,state='running')
        def collect_original(self,identity):
            assert identity==call_id;operations.append('collect');return []
    adapter=OriginalProvider()
    monkeypatch.setattr(experiments.Records,'calls',lambda self:[])
    monkeypatch.setattr(experiments,'_pending_dispatch',lambda *args:None)
    records=experiments.Records(config)
    # The real preflight rejects before adapter.prepare or call acceptance.
    with pytest.raises(experiments._AdmissionReviewRequired,match='worker_direction_review_due'):
        experiments._prepare_descriptor(config,payload,adapter,records,[],client_facts=dict(root=None))
    assert operations==[]
    # Exercise the CLI's current _dispatch_script entry and real descriptor
    # admission path; only the synthetic provider's lifecycle facts are stubbed.
    monkeypatch.setattr(experiments,'load',lambda *args,**kwargs:adapter)
    monkeypatch.setattr(experiments,'original_adapter',lambda original,identity,operation:(original,adapter))
    monkeypatch.setattr(experiments,'lifecycle_capabilities',lambda provider:SimpleNamespace(
        atomic_admission=True,query=True,collect=True))
    monkeypatch.setattr(experiments.Records,'accepted',lambda self,identity,*args,**kwargs:dict(call_id=identity))
    monkeypatch.setattr(experiments.Records,'directory',lambda self,identity:tmp_path/'retained'/identity)
    monkeypatch.setattr(experiments,'record_observation',lambda *args,**kwargs:dict(status='known_success'))
    monkeypatch.setattr(experiments,'_candidate_view',lambda config,value:value)
    monkeypatch.setattr(protocol,'require_scope',lambda scope:None)
    monkeypatch.setattr(instance_owner,'lease_for_configuration',lambda config:nullcontext())
    monkeypatch.setattr(replay_check,'public',lambda *args:dict(status='not_applicable'))
    monkeypatch.setattr(feedback,'budget_hint',lambda work:None)
    monkeypatch.setattr(managed_wait,'handoff',lambda *args:dict(action='finalize_response'))
    assert experiments.main(['--config',str(config_path),'run','--script',str(source)])==2
    refusal=json.loads(capsys.readouterr().out)
    assert refusal['status']=='review_required' and refusal['accepted'] is False
    assert refusal['reason']=='worker_direction_review_due'
    for operation in ('query','collect'):
        assert experiments.main(['--config',str(config_path),operation,'--call-id',call_id])==0
        result=json.loads(capsys.readouterr().out)
        assert result['feedback']['review']['enforced']
        assert result['feedback']['review']['reason']=='worker_direction_review_due'
    assert operations==['query','collect']
