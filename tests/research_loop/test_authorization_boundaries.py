"""Permission/accounting reads stay independent of result eligibility."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import audit_recovery, budget, candidate_audit, contracts as c, controller, delivery, execution, native
from test_candidate_audit import audit_fixture, request
from test_author_closeout_successor import pure_upgrade


def _accepted_audit(root):
    job, payload = request(root)
    decision = candidate_audit._decision(root, 3)
    operation = candidate_audit._operation(root, decision, 'run-1')
    intent = dict(action_id=operation, action_kind='audit', operation_id=operation,
                  decision=decision, candidate_id='run-1', max_seconds=20,
                  advance_seconds=budget.snapshot(root)['advance_seconds'])
    candidate_audit._save(candidate_audit._directory(root, 3)/'intent.json', intent)
    return job, payload, intent


def _forbid_results(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Permission/accounting traversed execution results or current admission')
    result_names = {'science-output.json', 'result-identity.json', 'native-outcome.json',
                    'usage.json', 'outcome.json', 'process.json', 'dispatch.json',
                    'sources.json', 'binding.json', 'native.jsonl', 'native.stderr',
                    'usage-observation.json', 'runner-process.json', 'native-process.json', 'fixture-thread.jsonl',
                    'author-closeout-facts.json', 'author-closeout-science.json', 'author-science-context.json',
                    'accepted.json', 'completed.json', 'execution-observation.json', 'compiler.json'}
    original = c.read
    def guarded(path, *args, **kwargs):
        if Path(path).name in result_names:
            forbidden()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(c, 'read', guarded)
    for owner, name in ((c, 'validate'), (execution, 'records'), (execution, 'raw'),
                        (native, 'usage'), (budget, 'snapshot'),
                        (candidate_audit, '_outcome'), (audit_recovery, 'facts')):
        monkeypatch.setattr(owner, name, forbidden)


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['task', 'missing_original'])
def test_native_consumption_keeps_accepted_count_without_reopening_originals(tmp_path, monkeypatch, damage):
    from scidiscovery.research_loop import accepted_budget
    root = audit_fixture(tmp_path)
    job = c.build_job(root, 3, 'W', [2])
    record = execution.origin(root, 3)
    intent = dict(step=3, task_ref=c.record_ref(root/'jobs/003.json'))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, 3, record, intent)
    for name in ('native-outcome.json', 'usage.json'):
        c.write(record/name, 'Broken scientific result; never a budget input')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.used_steps(root) == 3
    if damage == 'task':
        c.write(record/'native-intent.json', dict(intent, task_ref=dict(path='/fixture/jobs/002.json')))
    else:
        (record/'native-intent.json').unlink()
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.used_steps(root) == 3
    if damage == 'task':
        with pytest.raises(ValueError, match='native intent identity changed'):
            list(execution.accepted_intents(root, 3))


def _final_permit(tmp_path):
    root = audit_fixture(tmp_path)
    budget.begin_closeout(root, 2, 3, 'fixture final', final_only=True)
    job = c.build_job(root, 3, 'A', [1, 2], final_report=dict(source_cutoff=2, stopped_original=None))
    intent = dict(step=3, task_ref=c.record_ref(root/'jobs/003.json'), budget_slice=budget.accept_node(root, job))
    c.write(execution.origin(root, 3)/'native-intent.json', intent)
    return root, job, intent


@pytest.mark.process_e2e
def test_final_permissions_never_read_execution_results(tmp_path, monkeypatch):
    root, job, intent = _final_permit(tmp_path)
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.final_a_return(root, job, intent)['step'] == 3
        assert budget.final_a_barrier(root)['step'] == 3


@pytest.mark.process_e2e
def test_final_cursor_binds_accepted_sources_without_revalidating_old_science(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    refs = [dict(step=step, seal_ref=c.record_ref(execution.origin(root, step)/'result-identity.json')) for step in (1, 2)]
    cursor = dict(role='A', parents=[1, 2], instruction='Review existing evidence', evidence_return=None,
                  correction_request=None, author_correction=None, source_refs=refs)
    begin = budget.begin_closeout(root, 2, 3, 'fixture final', final_only=True, resume_cursor=cursor)
    seal = execution.origin(root, 2)/'result-identity.json'
    seal.write_text('Broken scientific seal; permission replay must not read it')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.closeout_state(root)['begin'] == begin
    events, _ = budget._events(root)
    budget.require_cursor_eligibility(root, cursor, 3, events)
    changed = dict(cursor, source_refs=[refs[0], dict(refs[1], seal_ref=dict(path='/foreign/seal.json'))])
    with pytest.raises(ValueError, match='accepted normal science'):
        budget.require_cursor_eligibility(root, changed, 3, events)


@pytest.mark.parametrize('recovery,phase', [
    ('unsupported', 'audit'), ({}, 'audit'), ('control_retry', 'confirm'), ('none', None),
])
def test_delivery_action_recovery_rejects_unknown_classification_or_non_audit_context(recovery, phase):
    artifact = dict(path='/fixture/artifact.bin', size_bytes=8)
    intent = dict(operation_id='operation', binding=dict(candidate_id='candidate', source=dict(objects=dict(artifact=artifact))))
    result = dict(status='unknown', operation_id='operation', candidate_id='candidate', artifact_ref=artifact,
        note='Original outcome remains uncertain', cleanup_confirmed=True, recovery=recovery)
    if phase is not None:
        result['failure_phase'] = phase
    with pytest.raises(ValueError, match='exact accepted candidate and artifact'):
        budget._validate_action_result(intent, result)


@pytest.mark.process_e2e
def test_final_report_grant_replay_does_not_revalidate_stopped_results(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    c.build_job(root, 3, 'A', [1, 2])
    stopped = budget.stopped_report_original(root, 3)
    budget.begin_closeout(root, 2, 4, 'fixture stopped predecessor', final_only=True, stopped_original=stopped)
    job = c.build_job(root, 4, 'A', [1, 2], final_report=dict(source_cutoff=2, stopped_original=stopped))
    anchor = budget.accept_node(root, job)
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        pure.setattr(budget, 'validate_final_report_inputs', lambda *args, **kwargs: pytest.fail('Grant replay required result eligibility'))
        assert budget.accept_node(root, job) == anchor
        assert budget.final_a_barrier(root)['step'] == 4
        assert anchor['final_return']['seq'] == budget.final_a_barrier(root)['seq']


@pytest.mark.process_e2e
def test_final_delivery_scope_read_is_pure_but_acceptance_keeps_result_gate(tmp_path, monkeypatch):
    root, _, intent = _final_permit(tmp_path)
    decision = dict(step=3, job_ref=c.record_ref(root/'jobs/003.json'),
                    raw_ref=dict(path='/fixture/native-evidence/003/science-output.json'),
                    seal_ref=dict(path='/fixture/native-evidence/003/result-identity.json'))
    bound = dict(decision=decision, operation_id='c'*64)
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.final_a_delivery(root, decision, bound) == intent['budget_slice']['final_return']
    with pytest.raises((ValueError, FileNotFoundError)):
        budget.require_final_a_delivery_eligibility(root, decision, bound)
    assert not budget.closeout_state(root)['actions']


def test_author_permissions_never_read_outcomes_call_results_or_scientific_context(pure_upgrade, monkeypatch):
    from scidiscovery.research_loop import author_closeout as author
    root, original, successor, *_ = pure_upgrade
    c.write(author.successor_path(root, 1), successor)
    job = c.read(root/'jobs/001.json')
    intent = dict(attempt=2, continuation_kind='author_closeout', context_mode='new_context',
                  author_closeout=author._ref(root, author.successor_path(root, 1)), task_ref=c.record_ref(root/original['job']['path']),
                  author_closeout_facts=dict(path='native-evidence/001/continuations/002/author-closeout-facts.json',
                                             size_bytes=0))
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        pure.setattr(author, '_calls', lambda *args: pytest.fail('Permission enumerated retained execution results'))
        pure.setattr(author, 'validate_science_context', lambda *args: pytest.fail('Permission required scientific context results'))
        assert author.validate(root, original)==original
        assert author.validate_successor(root, successor)==successor
        assert author.validate_intent(root, job, intent)==successor


def test_author_bad_source_result_keeps_grant_but_fails_explicit_eligibility(pure_upgrade):
    from scidiscovery.research_loop import author_closeout as author
    root, original, successor, *_ = pure_upgrade
    c.write(author.successor_path(root, 1), successor)
    file=root/successor['source_originals']['native-outcome.json']['path']
    file.write_bytes(file.read_bytes()+b' ')
    assert author.validate_successor(root, successor)==successor
    assert author.validate(root, original)==original
    with pytest.raises(ValueError, match='original changed'):
        author.require_successor_eligibility(root, successor)
