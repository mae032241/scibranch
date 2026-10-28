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
def test_audit_permission_and_consumption_ignore_broken_results(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    _, _, intent = _accepted_audit(root)
    directory = candidate_audit._directory(root, 3)
    for path in (execution.origin(root, 3)/'science-output.json',
                 execution.origin(root, 3)/'usage.json',
                 directory/'outcome.json', directory/'dispatch.json', directory/'binding.json'):
        if path.exists():path.chmod(0o600)
        path.write_text('Broken result original; never an authorization input')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        candidate_audit.validate_intent(root, intent)
        assert list(candidate_audit.intents(root)) == [intent]
        assert budget.used_steps(root) == 4
    changed = dict(intent, decision=dict(intent['decision'], job_ref=dict(path='/fixture/jobs/002.json')))
    changed['operation_id'] = changed['action_id'] = candidate_audit._operation(root, changed['decision'], 'run-1')
    with pytest.raises(ValueError, match='declared requester'):
        candidate_audit._validate_request(root, changed)


@pytest.mark.process_e2e
def test_audit_new_acceptance_requires_valid_scientific_result(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    job, payload = request(root)
    raw = execution.raw(root, 3)
    raw.chmod(0o600)
    raw.write_bytes(raw.read_bytes()+b'\n')
    def no_effect(*args, **kwargs):
        pytest.fail('Invalid scientific decision reached adapter preparation/effects')
    monkeypatch.setattr(delivery, 'freeze_sources', no_effect)
    monkeypatch.setattr(delivery, '_call', no_effect)
    with pytest.raises(ValueError):
        candidate_audit.execute(root, job, payload)
    assert not (candidate_audit._directory(root, 3)/'intent.json').exists()


@pytest.mark.process_e2e
def test_accepted_audit_still_rejects_changed_result_before_effect(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    job, payload, intent = _accepted_audit(root)
    raw = execution.raw(root, 3)
    raw.chmod(0o600)
    raw.write_bytes(raw.read_bytes()+b'\n')
    candidate_audit.validate_intent(root, intent)
    assert budget.used_steps(root) == 4
    with controller._writer(root):
        assert candidate_audit.execute(root, job, payload) == 'candidate_audit_unknown'
    def no_effect(*args, **kwargs):
        pytest.fail('Changed scientific result reached adapter effects')
    monkeypatch.setattr(delivery, '_call', no_effect)
    bound = dict(intent, binding=dict(decision=intent['decision'], operation_id=intent['operation_id']))
    with pytest.raises(ValueError, match='scientific decision changed'):
        delivery.invoke(root, bound, 20, audit_only=True)
    with pytest.raises(ValueError, match='scientific author changed'):
        candidate_audit.require_eligibility(root, intent)


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['task', 'attempt', 'step', 'root', 'previous', 'gap', 'missing_original'])
def test_native_consumption_reads_accepted_intents_not_results(tmp_path, monkeypatch, damage):
    root = audit_fixture(tmp_path)
    record = execution.origin(root, 2)
    later = record/'continuations/001'
    later.mkdir(parents=True)
    intent = dict(step=2, attempt=1, task_ref=c.record_ref(root/'jobs/002.json'),
                  root_intent_ref=c.record_ref(record/'native-intent.json'),
                  previous={'native-intent.json': c.record_ref(record/'native-intent.json'),
                            'native-outcome.json': '0'*64, 'usage.json': '0'*64})
    c.write(later/'native-intent.json', intent)
    for name in ('native-outcome.json', 'usage.json'):
        (record/name).write_text('Broken result')
        (later/name).write_text('Broken result')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.used_steps(root) == 3
    if damage == 'task':
        c.write(later/'native-intent.json', dict(intent, task_ref=dict(path='/fixture/jobs/002.json')))
    elif damage == 'attempt':
        c.write(later/'native-intent.json', dict(intent, attempt=True))
    elif damage == 'step':
        c.write(later/'native-intent.json', dict(intent, step=True))
    elif damage == 'root':
        c.write(later/'native-intent.json', dict(intent, root_intent_ref='0'*64))
    elif damage == 'previous':
        c.write(later/'native-intent.json', dict(intent, previous={'native-intent.json': '0'*64}))
    elif damage == 'gap':
        later.rename(record/'continuations/002')
        later.mkdir()
        c.write(record/'continuations/002/native-intent.json', dict(intent, attempt=2))
    else:
        (record/'native-intent.json').unlink()
    with pytest.raises(ValueError, match='continuation identity'):
        budget.used_steps(root)


@pytest.mark.process_e2e
def test_audit_retry_consumption_ignores_results_but_rejects_bad_intent(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    operation = 'e'*64
    path = root/'host-delivery/audit-recovery'/operation/'intent.json'
    intent = dict(settings_ref=c.record_ref(root/'settings.json'), operation_id=operation,
                  attempt=1, reason='audit_source_missing', max_seconds=20, advance_seconds=0)
    c.write(path, intent)
    path.with_name('outcome.json').write_text('Broken result')
    path.with_name('process.json').write_text('Broken result')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert list(audit_recovery.intents(root)) == [intent]
        assert budget.used_steps(root) == 3
    c.write(path, dict(intent, attempt=True))
    with pytest.raises(ValueError, match='recovery identity changed'):
        budget.used_steps(root)


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
def test_final_cursor_permission_is_separate_from_sealed_source_eligibility(tmp_path, monkeypatch):
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
    with pytest.raises(ValueError):
        budget.require_cursor_eligibility(root, cursor, 3, events)


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
