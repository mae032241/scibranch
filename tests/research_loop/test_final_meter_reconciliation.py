"""Host reconciliation preserves an unsubmitted FINAL and resumes no model."""
import json

import pytest

from scidiscovery.research_loop import budget, cli, contracts as c, controller, execution, native
from test_resume_usage_accounting import BASELINE, DELTA, TERMINAL
from test_worker_resume import instance


def scene(tmp_path, monkeypatch, *, max_tokens=3_000_000, steps=8):
    root = instance(tmp_path, max_tokens=max_tokens, cap=5_000_000, steps=steps)
    c.write(root/'host-started.json', budget.clock_stamp())
    budget.transition(root,'advance','deterministic original facts')
    job = c.build_job(root,1,'H',[])
    first = execution.origin(root,1)
    intent = dict(step=1,task_ref=c.record_ref(root/'jobs/001.json'),budget_slice=budget.accept_node(root,job))
    c.write(first/'native-intent.json',intent)
    c.write(first/'usage.json',BASELINE)
    c.write(first/'native-outcome.json',dict(returncode=7,native_started=False,stop_reason=None,
        metering=dict(coverage='terminal'),budget_slice=dict(budget.node_report(root,job,intent,BASELINE),usage_observed=True)))
    record = first/'continuations/001';record.mkdir(parents=True)
    resumed = dict(step=1,attempt=1,continuation_kind='repair',task_ref=intent['task_ref'],
        root_intent_ref=c.record_ref(first/'native-intent.json'),previous=execution.proof(first),
        session=dict(thread_id='exact-thread',path='/retained/session.jsonl'),
        budget_slice=budget.accept_continuation(root,job,[first]))
    c.write(record/'native-intent.json',resumed)
    metadata = dict(source='turn.completed',thread_id='exact-thread',coverage='terminal',
        live_coverage='live',reconciliation='terminal_covers_live',has_observation=True,
        baseline_tokens=BASELINE,live_tokens=DELTA,tokens=TERMINAL)
    c.write(record/'usage.json',TERMINAL)
    c.write(record/'usage-observation.json',metadata)
    c.write(record/'science-output.json',dict(summary='Retained bounded finding',
        scientific_result='Deterministic original facts, without scientific qualification.',
        limitations=['No model or solver was run'],implementation_selection=None,
        search_plan=None,next_action='analyze',route_reason='Return the original finding to A'))
    # A completed continuation retains its stopped process declarations even
    # in this deterministic fixture; no real process owns these dead identities.
    process_refs = {}
    for offset,name in enumerate(('runner-process.json','native-process.json')):
        pid = 999999990+offset
        c.write(record/name,dict(pid=pid,ppid=999999989,pgid=pid,sid=pid,start_ticks=1,
            boot_id=budget.clock_stamp()['boot_id'],task_ref=c.record_ref(record/'native-intent.json')))
        process_refs[name] = c.record_ref(record/name)
    c.write(record/'native-outcome.json',dict(returncode=0,native_started=True,stop_reason=None,
        cleanup='observed_group_cleaned',native_backend='codex',thread_id='exact-thread',
        native_identity=dict(path='/retained/session.jsonl'),metering=metadata,
        execution_processes=process_refs,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json'),
        budget_slice=dict(budget.node_report(root,job,resumed,TERMINAL),usage_observed=True)))
    c.seal_result(root,1)
    cursor = dict(role='A',parents=[1],instruction='',evidence_return=None,correction_request=None,
        author_correction=None,source_refs=[dict(step=1,seal_ref=c.record_ref(first/'result-identity.json'))])
    begin = budget.begin_closeout(root,1,2,'closeout_reserve_tokens',final_only=True,resume_cursor=cursor)
    final = c.build_job(root,2,'A',[1],final_report=dict(source_cutoff=1,stopped_original=None))
    old_total = sum(BASELINE[key]+TERMINAL[key] for key in ('input_tokens','output_tokens'))
    effective = native.usage(root)
    # Reproduce the old admission observation only; all tested recovery reads
    # use the real corrected fold and accepted execution chain below.
    with monkeypatch.context() as old:
        old.setattr(native,'usage',lambda *args,**kwargs:dict(effective,observed_tokens=old_total,accounted_tokens=old_total))
        final_intent = dict(step=2,task_ref=c.record_ref(root/'jobs/002.json'),budget_slice=budget.accept_node(root,final))
    origin = execution.origin(root,2)
    c.write(origin/'native-intent.json',final_intent)
    claim = budget.final_a_barrier(root)
    usage = native.usage(root)
    c.write(origin/'native-outcome.json',dict(returncode=None,native_started=False,stop_reason='native_error_unknown',
        thread_id=None,terminal_observed=False,native_activity_observed=False,
        metering=dict(coverage='unknown',has_observation=False),usage=usage,
        final_a_completion=budget.final_a_receipt(claim,final_intent,None,usage),
        budget_slice=dict(budget.node_report(root,final,final_intent,
            dict(input_tokens=0,cached_input_tokens=0,output_tokens=0)),usage_observed=False)))
    c.write(origin/'runner-process.json',dict(pid=999999999,ppid=999999998,pgid=999999999,sid=999999999,
        start_ticks=1,boot_id=budget.clock_stamp()['boot_id'],task_ref=final_intent['task_ref']))
    (origin/'native.jsonl').write_text('')
    (origin/'runner.stderr').write_text('Retained traceback: mandatory input validation failed before native dispatch.\n')
    files = dict(final=root/'control-ledger'/f'{begin["seq"]:06d}.json',
        claim=root/'control-ledger'/f'{claim["seq"]:06d}.json',job=root/'jobs/002.json',
        intent=origin/'native-intent.json',outcome=origin/'native-outcome.json',
        runner=origin/'runner-process.json',stderr=origin/'runner.stderr')
    report = dict(author='independent-engineer',reviewer='independent-reviewer',source_commit='fixture-source',
        source_flow='Reviewed source orders the retained validation failure before Popen and provider submission.',
        provider_submission='not_submitted',originals={key:c.file_ref(path) for key,path in files.items()},
        metering_sources=[str(record)])
    report_path = tmp_path/'reconciliation.json';c.write(report_path,report)
    return root,report_path,report,files


def test_cli_records_one_resume_without_native_dispatch_or_original_rewrites(tmp_path,monkeypatch,capsys):
    root,report_path,report,files = scene(tmp_path,monkeypatch)
    assert budget.execution_blocked(root)
    originals = {path:path.read_bytes() for path in [root/'settings.json',*files.values()]}
    usage = native.usage(root);steps = budget.used_steps(root);seconds = budget.snapshot(root)['consumed_seconds']
    def forbidden(*args,**kwargs):raise AssertionError('Reconciliation must never dispatch native work')
    monkeypatch.setattr(native,'execute_native',forbidden)
    assert cli.main(['reconcile-final','--root',str(root),'--report',str(report_path)]) == 0
    assert json.loads(capsys.readouterr().out) == dict(status='final_reconciled',next_step=3)
    resume = budget.normal_resume(root)
    assert c.read(root/'host-recovery/final-resume/002.json') == report
    assert resume['reconciliation_ref'] == c.file_ref(root/'host-recovery/final-resume/002.json')
    assert budget.closeout_state(root)['begin'] is None and budget.final_a_barrier(root) is None
    assert native.usage(root) == usage and usage['unknown_steps'] == [2]
    assert budget.used_steps(root) == steps and budget.snapshot(root)['consumed_seconds'] >= seconds
    assert budget.snapshot(root)['clock_phase'] == 'paused' and not budget.execution_blocked(root)
    assert all(path.read_bytes() == value for path,value in originals.items())
    assert not (root/'jobs/003.json').exists()
    assert controller.reconcile_final(root,report_path)['next_step'] == 3
    assert budget.normal_resume(root) == resume
    job = c.build_job(root,3,'A',[1])
    assert not job.get('final_only') and [row['step'] for row in job['history']['nodes']] == [1]
    c._validate_inputs(root,c.workspace(root,3),job)
    events,_ = budget._events(root)
    assert len([event for event in events if event['kind']=='closeout_resume']) == 1
    assert not any(event['kind']=='budget_amend' for event in events)


def test_retry_after_resume_publication_closes_the_original_clock_once(tmp_path,monkeypatch):
    root,path,_,_ = scene(tmp_path,monkeypatch)
    def crash(*args,**kwargs):raise RuntimeError('Crash after resume publication before inactive clock')
    with monkeypatch.context() as patch:
        patch.setattr(budget,'inactive',crash)
        with pytest.raises(RuntimeError,match='before inactive clock'):
            controller.reconcile_final(root,path)
    resume = budget.normal_resume(root)
    assert resume is not None and budget.snapshot(root)['clock_phase'] == 'advance'
    assert controller.reconcile_final(root,path)['next_step'] == 3
    assert budget.snapshot(root)['clock_phase'] == 'paused'
    assert budget.normal_resume(root) == resume
    events,_ = budget._events(root)
    assert len([event for event in events if event['kind']=='closeout_resume']) == 1

    # The same idempotent call cannot pause a later unknown native original.
    job = c.build_job(root,3,'A',[1]);record = execution.origin(root,3)
    c.write(record/'native-intent.json',dict(step=3,task_ref=c.record_ref(root/'jobs/003.json'),
        budget_slice=budget.accept_node(root,job)))
    c.write(record/'native-outcome.json',dict(native_started=True,returncode=None,stop_reason='native_error_unknown'))
    budget.transition(root,'advance','new unknown original')
    controller.reconcile_final(root,path)
    assert budget.execution_blocked(root) and budget.snapshot(root)['clock_phase'] == 'advance'
    assert budget.normal_resume(root) == resume


@pytest.mark.parametrize('fault',['same_author','no_source_flow','no_trace','started','runner_binding',
    'native_process','science','continuation','wrong_meter','later_job','pause','tokens','seconds','steps'])
def test_reconciliation_rejects_unproven_or_unavailable_original_route(tmp_path,monkeypatch,fault):
    root,path,report,files = scene(tmp_path,monkeypatch,
        max_tokens=2_300_000 if fault=='tokens' else 3_000_000,steps=2 if fault=='steps' else 8)
    origin = execution.origin(root,2)
    if fault=='same_author':report['reviewer']=report['author']
    if fault=='no_source_flow':report['source_flow']=''
    if fault=='no_trace':files['stderr'].write_text('')
    if fault=='started':
        outcome=c.read(files['outcome']);outcome['native_started']=True;c.write(files['outcome'],outcome)
    if fault=='runner_binding':c.write(files['runner'],dict(pid=999999999,task_ref=dict(path='/wrong-job')))
    if fault=='native_process':c.write(origin/'native-process.json',{})
    if fault=='science':c.write(origin/'science-output.json',{})
    if fault=='continuation':(origin/'continuations').mkdir()
    if fault=='wrong_meter':report['metering_sources']=[str(execution.origin(root,1))]
    if fault=='later_job':c.write(root/'jobs/003.json',{})
    if fault=='pause':(root/'pause.request').touch()
    if fault=='seconds':
        stamp=budget.clock_stamp();future=dict(stamp,at_unix=stamp['at_unix']+1000,at_boottime=stamp['at_boottime']+1000)
        monkeypatch.setattr(budget,'clock_stamp',lambda:future)
    report['originals']={key:c.file_ref(file) for key,file in files.items()};c.write(path,report)
    with pytest.raises((ValueError,RuntimeError)):
        controller.reconcile_final(root,path)
    assert budget.normal_resume(root) is None
    assert budget.closeout_state(root)['begin']['first_step'] == 2
