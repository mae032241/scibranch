"""A continuing shallow task keeps its original soft target advisory."""
import sys

import pytest

from scidiscovery.research_loop import (contracts as c, controller, budget, native,
    continuation, checkpoint, execution, runtime, search, experiments, worker_delivery)
from execution_fixtures import execution_registry
from test_breadth_science import h, run, findings
from test_worker_delivery import output, stopped_native


def continuing_stage(tmp_path, *, max_tokens=100000):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Compare the original mechanisms within finite investment.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable],model='no-model',reasoning_effort='high',
        max_tokens=max_tokens,max_seconds=600,max_steps=16,search_policy=search.DEFAULT_POLICY,node_budget_mode='soft',
        node_budget_policy={role:dict(max_observed_tokens=10 if role=='W' else 100,max_seconds=60)
                            for role in ('H','W','A','E')},
        resume_policy=dict(max_attempts=1,protocol='codex_exec_resume',usage_scope='turn',output_repair=False),
        worker_fragment_policy=dict(context='new_context'))
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Known zero-model host start for original soft-target probe')
    h(root,labels=('P','Q'))
    job=c.build_job(root,2,'W',[1])
    anchor=budget.accept_node(root,job)
    record=execution.record(root,2)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=anchor))
    config=experiments.configure(root,2,'local_execution:command',dict(run_argv=[sys.executable]),tmp_path/'config-2.json')
    run(root,config,'h001.m1')
    record,_=output(root,findings('h001.m1'),intent='continue');stopped_native(root,record)
    meter=c.read(record/'usage.json');outcome=c.read(record/'native-outcome.json')
    outcome['budget_slice']=dict(role='W',limits=budget.node_limits(root,job),usage_observed=True,
        dispatches=1,advance_seconds=0.,observed_tokens=meter['input_tokens']+meter['output_tokens'],**meter)
    c.write(record/'native-outcome.json',outcome)
    checkpoint.capture(root,job,record)
    return root,job,config,record


def test_incomplete_continue_at_soft_target_accepts_same_stage_fragment(tmp_path,execution_registry,monkeypatch):
    root,job,config,record=continuing_stage(tmp_path)
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    starts=[]
    monkeypatch.setattr(runtime,'start',lambda *args:starts.append(args))
    review=continuation.fragment_review(root,job)
    assert review['requested'] is True and review['enforced'] is False
    with pytest.raises(ValueError,match='No original host exception'):
        worker_delivery.authorize_exit(root,job,record,review['reason'])
    assert continuation.seal_or_repair(root,2)==dict(fragment_continuation=True)
    assert starts==[(root,2,1)]
    assert not (record/'result-identity.json').exists() and not (record/'stage-end.json').exists()
    chain=execution.records(root,2)
    assert len(chain)==2 and len(experiments.Records(config).calls())==1
    assert c.read(chain[1]/'native-intent.json')['budget_slice']['previous_tokens']==10


@pytest.mark.parametrize('boundary', ['tokens', 'time'])
def test_shared_hard_boundary_seals_partial_and_retains_final_route(tmp_path,execution_registry,monkeypatch,boundary):
    root,job,config,record=continuing_stage(tmp_path,max_tokens=20 if boundary=='tokens' else 100000)
    if boundary=='time':
        stamp=budget.clock_stamp();stamp['at_boottime']+=601;stamp['at_unix']+=601
        monkeypatch.setattr(budget,'clock_stamp',lambda:dict(stamp))
    monkeypatch.setattr(runtime,'process_matches',lambda *args:False)
    def forbidden(*args,**kwargs):raise AssertionError('Shared exhaustion cannot start another ordinary fragment')
    monkeypatch.setattr(runtime,'start',forbidden)
    review=continuation.fragment_review(root,job)
    assert review['requested'] is True and review['enforced'] is True
    assert review['reason']==('reserve_reached' if boundary=='tokens' else 'review_deadline_reached')
    continuation.seal_or_repair(root,2)
    receipt=c.read(record/'result-identity.json')['control_outcome']['worker_delivery']
    assert receipt['status']=='partial' and receipt['reason']==review['reason']
    ending=checkpoint.stage_end(root,job,record)
    assert ending['work_exit']['kind']=='host_review'
    assert worker_delivery.validate_exit(root,job,record,ending['work_exit'])==review['reason']
    c.validate(c.workspace(root,2))
    assert len(execution.records(root,2))==1 and len(experiments.Records(config).calls())==1
    reason=budget.node_admission(root,dict(step=3,role='A'))
    assert reason is not None
    budget.begin_closeout(root,2,3,reason,final_only=True)
    assert budget.closeout_state(root)['begin']['mode']=='final'


@pytest.mark.parametrize('crossing', ['deadline', 'lease'])
@pytest.mark.parametrize('hard', [False, True])
def test_exit_archive_clock_crossing_uses_one_original_sample(tmp_path,execution_registry,monkeypatch,crossing,hard):
    root,job,config,record=continuing_stage(tmp_path,max_tokens=20 if hard else 100000)
    stamp=budget.clock_stamp()
    monkeypatch.setattr(budget,'clock_stamp',lambda:dict(stamp))
    configure=budget.review_configuration
    def window(*args):
        admission=configure(*args)
        path=budget._review_directory(root,job)/'review.json';value=c.read(path)
        value['deadline_boottime']=stamp['at_boottime']+(1 if crossing=='deadline' else 100)
        value['valid_until_boottime']=stamp['at_boottime']+(1 if crossing=='lease' else 100)
        c.write(path,value)
        return admission
    monkeypatch.setattr(budget,'review_configuration',window)
    archive=worker_delivery._original
    def crosses_clock(*args):
        original=archive(*args)
        stamp['at_boottime']+=2;stamp['at_unix']+=2
        return original
    monkeypatch.setattr(worker_delivery,'_original',crosses_clock)
    reason='reserve_reached' if hard else 'target_tokens_reached'
    if hard:
        proof=worker_delivery.authorize_exit(root,job,record,reason)
        assert proof['sample']['at_boottime']==stamp['at_boottime']-2
        assert worker_delivery.validate_exit(root,job,record,proof)==reason
        archived=c.read(root/proof['review']['snapshot'])
        assert experiments._review_observation(archived,stamp)['reason']!=reason
    else:
        with pytest.raises(ValueError,match='No original host exception'):
            worker_delivery.authorize_exit(root,job,record,reason)
