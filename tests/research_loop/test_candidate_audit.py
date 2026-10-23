"""A-selected scratch audits through the real isolated provider, never promotion."""
import json
from pathlib import Path
import shutil
import sys

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, correction, delivery, history, native, search
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy
from test_history_handoff import finish


def audit_fixture(tmp_path, mode='ok', *, enabled=True, candidate_count=1, correction_enabled=False):
    if shutil.which('bwrap') is None:
        pytest.skip('Actual isolated adapter required')
    public = tmp_path / 'public'
    (public / 'harness').mkdir(parents=True)
    root = tmp_path / 'instance'
    for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
        path = public / 'harness' / name
        if name == 'exp_cli.py':
            path.write_text(FAKE_HARNESS)
        elif name == 'meta.json':
            c.write(path, dict(mode=mode, control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'hidden.json')))
        else:
            path.write_text('Public fixture data only\n')
    c.write(tmp_path / 'hidden.json', dict(secret='Must not enter audit'))
    objective = public / 'objective.md'
    objective.write_text('Decide scientifically after one bounded public audit; no automatic submission.')
    limits = {role: dict(max_observed_tokens=2000, max_seconds=60) for role in ('H', 'W', 'A', 'E')}
    controller.prepare(root, objective, public, ['fake-no-model'], max_tokens=100_000,
        max_seconds=400, max_steps=16, model='fixture', reasoning_effort='high', search_policy=search.DEFAULT_POLICY if correction_enabled else None,
        correction_policy=dict(review_after=1,pause_after=1) if correction_enabled else None,
        node_budget_policy=limits, delivery_policy=delivery_policy(descriptor(root, public)),
        candidate_audit_policy={'version':1} if enabled else None,
        history_observer=dict(adapter=str(Path(__file__).resolve().parents[2] / 'plugins/cov_execution/cov_execution/observer.py'),
                              config=dict(runs='runs', evaluation_key='fixture-public')))
    (root / 'runs').mkdir()
    (root / 'submission').mkdir()
    h_plan = dict(scope='Scientific discrimination', commitments=[dict(label='Mechanism',minimal_test='One controlled comparison',existing_ref=None)], focus=dict(label='Mechanism',action='screen')) if correction_enabled else None
    finish(root, 1, 'H', [], action='experiment',search_choice=h_plan)
    c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    model = root / 'runs/exact-model.bin'
    model.write_bytes(b'Exact audit model; never host-unpickled\n' * 20_000)
    script = c.workspace(root, 2) / 'artifacts/train.py'
    row = dict(ok=True, val_acc=.6, val_score=.6, model_path='/opt/exp/runs/exact-model.bin', script=str(script))
    (root / 'runs/results.jsonl').write_text(''.join(json.dumps(dict(row, run_id=i))+'\n' for i in range(1,candidate_count+1)))
    finish(root, 2, 'W', [1], files={'train.py':'def train(train_df,val_df): return None\n'},
        mechanism_findings=[dict(mechanism='Mechanism',basis='blocked',finding='Fixture has no managed scientific observation')] if correction_enabled else None)
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'fixture')
    return root


def request(root, candidate='run-1'):
    job, _, _ = finish(root, 3, 'A', [1, 2], action='audit', recommendation=candidate, progress={'verdict':'no_progress'})
    return job, c.validate(c.workspace(root, 3))


@pytest.mark.process_e2e
def test_audit_only_returns_to_a_and_never_promotes_or_replays(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    request(root)
    before = budget.used_steps(root)
    def complete(root, step):
        assert step == 4
        job = c.read(root / 'jobs/004.json')
        view = c.read(c.workspace(root, 4) / 'science-task.json')
        receipt = view['candidate_audit']
        assert receipt['status'] == 'audited' and receipt['candidate_id'] == 'run-1'
        assert receipt['scope'] == candidate_audit.SCOPE
        assert 'sha256' not in json.dumps(receipt) and 'leakage_flag' not in receipt
        assert correction._proofs(root,job,{'verdict':'progress'}) == {str(c.host_record(c.workspace(root,2))/'science-output.json')}
        assert not (root / 'runs/submitted.json').exists()
        assert not list((root / 'submission').iterdir())
        parents = [int(Path(row['path']).parts[2]) for row in job['direct_refs']]
        finish(root, step, 'A', parents, action='wait', recommendation=None)
    monkeypatch.setattr(native, 'execute_native', complete)
    result = controller.run(root)
    assert result['status'] == 'wait' and result['completed_steps'] == 4, result
    assert budget.used_steps(root) == before + 2  # One audit and one A, not submit/confirm.
    saved = {path: path.read_bytes() for path in (root/'native-evidence/003/candidate-audit').iterdir()}
    assert controller.run(root)['status'] == 'wait'
    assert all(path.read_bytes() == raw for path,raw in saved.items())
    assert budget.closeout_state(root)['begin'] is None


@pytest.mark.process_e2e
@pytest.mark.parametrize('candidate', [None, 'unknown-id'])
def test_unavailable_choice_preserves_science_and_returns_diagnostic(tmp_path, candidate):
    root = audit_fixture(tmp_path)
    job, payload = request(root, candidate)
    science = (c.workspace(root,3)/'conclusion.json').read_bytes()
    assert candidate_audit.execute(root,job,payload) is None
    assert candidate_audit.public_receipt(root,candidate_audit.receipt_ref(root,3))['status']=='unavailable'
    assert (c.workspace(root,3)/'conclusion.json').read_bytes()==science
    assert budget.used_steps(root)==3


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['skip','leak'])
def test_incomplete_and_rejected_audits_never_promote_or_claim_global_qualification(tmp_path, mode):
    root = audit_fixture(tmp_path, mode)
    job,payload = request(root)
    assert candidate_audit.execute(root,job,payload) is None
    receipt = candidate_audit.public_receipt(root,candidate_audit.receipt_ref(root,3))
    assert receipt['status']==('unclassified' if mode=='skip' else 'rejected')
    assert receipt['qualification']==('not_completed' if mode=='skip' else 'rejected')
    assert not (root/'runs/submitted.json').exists() and not list((root/'submission').iterdir())
    assert not budget.execution_blocked(root)


@pytest.mark.process_e2e
def test_unknown_accepted_audit_is_not_replayed_and_blocks_amendment(tmp_path):
    root = audit_fixture(tmp_path)
    job,payload = request(root)
    directory = candidate_audit._directory(root,3)
    operation = candidate_audit._operation(root,candidate_audit._decision(root,3),'run-1')
    candidate_audit._save(directory/'intent.json',dict(action_id=operation,action_kind='audit',operation_id=operation,
        decision=candidate_audit._decision(root,3),candidate_id='run-1',max_seconds=20,advance_seconds=budget.snapshot(root)['advance_seconds']))
    saved=(directory/'intent.json').read_bytes()
    assert candidate_audit.execute(root,job,payload)=='candidate_audit_unknown'
    assert controller.run(root)['status']=='candidate_audit_unknown'
    assert budget.execution_blocked(root) and budget.used_steps(root)==4
    with pytest.raises(RuntimeError, match='no running or unknown'):
        budget.amend(root,max_tokens=200000,authorization_note='Human cannot override unknown execution')
    assert (directory/'intent.json').read_bytes()==saved and not (directory/'sources.json').exists()


@pytest.mark.process_e2e
def test_filter_selects_early_candidate_without_changing_default_full_pool(tmp_path):
    root=audit_fixture(tmp_path,candidate_count=2)
    job,payload=request(root,'run-1')
    refs=delivery.freeze_sources(root,job['history']['nodes'])
    assert [row['candidate_id'] for row in refs['items']]==['run-1','run-2']
    selected=delivery.freeze_sources(root,job['history']['nodes'],candidate_id='run-1')
    assert [row['candidate_id'] for row in selected['items']]==['run-1']
    assert candidate_audit.execute(root,job,payload) is None


@pytest.mark.process_e2e
def test_current_job_and_schema_reject_retired_audit_protocol(tmp_path):
    from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
    root=audit_fixture(tmp_path,enabled=False)
    original=(root/'jobs/002.json').read_bytes()
    job=c.build_job(root,3,'A',[1,2])
    assert job['handoff_protocol']==CURRENT and 'audit' in c.actions('A',CURRENT)
    with pytest.raises(UnsupportedProtocol):c.result_schema('A',8)
    assert (root/'jobs/002.json').read_bytes()==original


@pytest.mark.process_e2e
def test_bound_receipt_or_sources_tamper_is_hard_integrity_failure(tmp_path):
    root=audit_fixture(tmp_path)
    job,payload=request(root)
    assert candidate_audit.execute(root,job,payload) is None
    following=c.build_job(root,4,'A',[1,2,3],instruction='Exact scientific question')
    path=candidate_audit._directory(root,3)/'adapter-result.json'
    value=c.read(path);value['status']='confirmed';c.write(path,value)
    with pytest.raises(ValueError):
        c._validate_inputs(root,c.workspace(root,4),following)


@pytest.mark.process_e2e
@pytest.mark.parametrize('field,value', [('accepted',False),('candidate_id','run-2')])
def test_audit_outcome_single_field_tamper_is_hard_rejection(tmp_path,field,value):
    root=audit_fixture(tmp_path)
    job,payload=request(root)
    assert candidate_audit.execute(root,job,payload) is None
    path=candidate_audit._directory(root,3)/'outcome.json'
    original=c.read(path);original[field]=value;c.write(path,original)
    with pytest.raises(ValueError):
        candidate_audit.receipt_ref(root,3)


@pytest.mark.process_e2e
def test_published_correction_pause_blocks_new_audit_acceptance(tmp_path):
    root=audit_fixture(tmp_path,correction_enabled=True)
    review=dict(dispositions=[],next_focus=dict(mechanism_ref='h001.m1',action='screen'))
    finish(root,3,'A',[1,2],action='continue_w',search_choice=review,progress={'verdict':'no_progress'})
    state=correction.publish(root,3)
    trigger=state['review_request']
    assert trigger is not None and trigger['level']=='review'
    c.build_job(root,4,'H',[1,2,3],instruction='Exact scientific question',
                correction_request=dict(trigger_id=trigger['trigger_id']))
    plan=dict(scope='Reconsider scientific blind spots',commitments=[],focus=dict(label='h001.m1',action='screen'))
    finish(root,4,'H',[1,2,3],action='experiment',search_choice=plan)
    c.build_job(root,5,'W',[4],instruction='Exact scientific question')
    finish(root,5,'W',[4],mechanism_findings=[dict(mechanism='Mechanism',basis='blocked',finding='No new managed observation in this fixture')])
    finish(root,6,'A',[4,5],action='continue_w',search_choice=review,progress={'verdict':'no_progress'})
    state=correction.publish(root,6)
    pause=state['current_pause']
    assert pause is not None and pause['published'] is True
    assert correction.gate(root,6)['trigger_id']==pause['trigger_id']
    # A late accepted scientific result is sealed; it does not authorize a new audit.
    job,_,_=finish(root,7,'A',[4,5,6],action='audit',recommendation='run-1')
    payload=c.validate(c.workspace(root,7))
    assert candidate_audit.execute(root,job,payload)=='correction_paused'
    assert not (candidate_audit._directory(root,7)/'intent.json').exists()
    assert budget.used_steps(root)==7
    assert correction.project(root)['current_pause']['trigger_id']==pause['trigger_id']
    correction.acknowledge(root,pause['trigger_id'],'Explicitly allow one scientific reassessment')
    eligible=correction.project(root)['eligible']
    assert [row['step'] for row in eligible]==[3,6]
    assert candidate_audit.execute(root,job,payload) is None
    state=correction.project(root)
    assert state['eligible']==eligible and state['permit'] is not None
    c.build_job(root,8,'A',[4,5,7],instruction='Exact scientific question')
    finish(root,8,'A',[4,5,7],action='continue_w',search_choice=review,progress={'verdict':'no_progress'})
    state=correction.publish(root,8)
    assert [row['step'] for row in state['eligible']]==[3,6,8]
    assert state['permit'] is None and state['current_pause']['trigger_id']!=pause['trigger_id']


def _seal_existing(root,step,payload):
    if c.read(root/'jobs'/f'{step:03d}.json')['role']=='A':
        payload={'decision_request':None,'portfolio_review':None,**payload}
    record=c.host_record(c.workspace(root,step))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    from test_history_handoff import selection_explanation
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json')))
    return c.seal_result(root,step)


@pytest.mark.process_e2e
def test_closeout_audit_keeps_exact_prefix_returns_a_without_promotion(tmp_path,monkeypatch):
    root=audit_fixture(tmp_path)
    nodes=[dict(step=i,seal_ref=c.record_ref(c.host_record(c.workspace(root,i))/'result-identity.json')) for i in (1,2)]
    refs=delivery.freeze_sources(root,nodes)
    budget.begin_closeout(root,2,3,'Original explicit closeout',delivery_sources=refs)
    c.build_job(root,3,'A',[1,2],purpose='closeout',source_cutoff=2,delivery_sources=refs)
    payload=dict(summary='Audit before deciding',scientific_result='A needs public qualification without claiming target suitability',
        limitations=['Audit is not target evidence'],implementation_selection=None,search_review=None,progress_assessment=None,
        candidate_recommendation='run-1',decision_request=None,portfolio_review=None,next_action='audit',route_reason='Reassess after exact audit')
    _seal_existing(root,3,payload)
    calls=[]
    def complete(root,step):
        calls.append(step)
        assert step==4
        job=c.read(root/'jobs/004.json')
        assert job['purpose']=='closeout' and job['source_cutoff']==2 and job['delivery_sources']==refs
        view=c.read(c.workspace(root,4)/'science-task.json')
        assert view['candidate_audit']['status']=='audited'
        assert not (root/'runs/submitted.json').exists() and not list((root/'submission').iterdir())
        assert budget.closeout_state(root)['actions']==[]
        _seal_existing(root,step,dict(payload,next_action='deliver'))
    monkeypatch.setattr(native,'execute_native',complete)
    result=controller.run(root)
    assert result['status']=='delivery_confirmed', result
    assert calls==[4] and len(budget.closeout_state(root)['actions'])==2
    assert (root/'runs/submitted.json').exists()
    before=budget.used_steps(root)
    assert controller.run(root)['status']=='delivery_confirmed'
    assert calls==[4] and budget.used_steps(root)==before


@pytest.mark.process_e2e
def test_unchanged_unavailable_candidate_stops_repeated_audit_route(tmp_path,monkeypatch):
    root=audit_fixture(tmp_path)
    job,payload=request(root)
    calls=[]
    def unavailable(root,nodes,*,candidate_id=None,job=None):
        calls.append(candidate_id)
        return dict(items=[dict(candidate_id=candidate_id,status='unavailable',reason='delivery_managed_original_missing_or_ambiguous')],
            omitted_count=0,source_nodes=nodes)
    monkeypatch.setattr(delivery,'freeze_sources',unavailable)
    assert candidate_audit.execute(root,job,payload) is None
    first=candidate_audit._outcome(root,3)
    assert first['result']['note']=='candidate_originals_unavailable'
    assert 'Repair the original binding' in first['result']['diagnostic']
    c.build_job(root,4,'A',[1,2,3],instruction=payload['route_reason'])
    finish(root,4,'A',[1,2,3],action='audit',recommendation='run-1',progress={'verdict':'no_progress'})
    job=c.read(root/'jobs/004.json');payload=c.validate(c.workspace(root,4))
    assert candidate_audit.execute(root,job,payload)=='candidate_audit_originals_unavailable'
    second=candidate_audit._outcome(root,4)
    assert second['unavailable_input_ref']==first['unavailable_input_ref']
    assert second['result']['note']=='candidate_audit_unchanged_originals_unavailable'
    before=len(calls)
    assert candidate_audit.execute(root,job,payload)=='candidate_audit_originals_unavailable'
    assert len(calls)==before
    assert not (candidate_audit._directory(root,4)/'dispatch.json').exists()
    monkeypatch.setattr(native,'execute_native',lambda *a:pytest.fail('Unchanged unavailable audit must not launch another A'))
    result=controller.run(root)
    assert result['status']=='candidate_audit_originals_unavailable'
    assert 'Repair the original binding' in result['reason']
    assert not (root/'jobs/005.json').exists()


@pytest.mark.process_e2e
def test_unavailable_audit_does_not_block_a_different_exact_candidate(tmp_path,monkeypatch):
    root=audit_fixture(tmp_path,candidate_count=2)
    job,payload=request(root)
    def unavailable(root,nodes,*,candidate_id=None,job=None):
        return dict(items=[dict(candidate_id=candidate_id,status='unavailable',reason='delivery_code_source_unavailable')],
            omitted_count=0,source_nodes=nodes)
    monkeypatch.setattr(delivery,'freeze_sources',unavailable)
    assert candidate_audit.execute(root,job,payload) is None
    c.build_job(root,4,'A',[1,2,3],instruction=payload['route_reason'])
    finish(root,4,'A',[1,2,3],action='audit',recommendation='run-2',progress={'verdict':'no_progress'})
    job=c.read(root/'jobs/004.json');payload=c.validate(c.workspace(root,4))
    assert candidate_audit.execute(root,job,payload) is None
    first,second=candidate_audit._outcome(root,3),candidate_audit._outcome(root,4)
    assert first['unavailable_input_ref']!=second['unavailable_input_ref']
    assert second['result']['note']=='candidate_originals_unavailable'
