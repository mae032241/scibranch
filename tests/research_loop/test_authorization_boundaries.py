"""Permission/accounting reads stay independent of result eligibility."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import audit_recovery, budget, candidate_audit, contracts as c, delivery, execution, native
from test_candidate_audit import audit_fixture, request
from test_operator_usage_reserve import unknown_original, historical_charge
from test_author_closeout_successor import pure_upgrade
from test_legacy_author_compiler_client import legacy_compiler


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
    for name in ('read', 'digest'):
        original = getattr(c, name)
        def guarded(path, *args, _original=original, **kwargs):
            if Path(path).name in result_names:
                forbidden()
            return _original(path, *args, **kwargs)
        monkeypatch.setattr(c, name, guarded)
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
        path.write_text('Broken result original; never an authorization input')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        candidate_audit.validate_intent(root, intent)
        assert list(candidate_audit.intents(root)) == [intent]
        assert budget.used_steps(root) == 4
    changed = dict(intent, decision=dict(intent['decision'], job_sha256='0'*64))
    changed['operation_id'] = changed['action_id'] = candidate_audit._operation(root, changed['decision'], 'run-1')
    with pytest.raises(ValueError, match='declared requester'):
        candidate_audit._validate_request(root, changed)


@pytest.mark.process_e2e
def test_audit_new_acceptance_requires_valid_scientific_result(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    job, payload = request(root)
    raw = execution.raw(root, 3)
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
    raw.write_bytes(raw.read_bytes()+b'\n')
    candidate_audit.validate_intent(root, intent)
    assert budget.used_steps(root) == 4
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
    intent = dict(step=2, attempt=1, task_sha256=c.digest(root/'jobs/002.json'),
                  root_intent_sha256=c.digest(record/'native-intent.json'),
                  previous={'native-intent.json': c.digest(record/'native-intent.json'),
                            'native-outcome.json': '0'*64, 'usage.json': '0'*64})
    c.write(later/'native-intent.json', intent)
    for name in ('native-outcome.json', 'usage.json'):
        (record/name).write_text('Broken result')
        (later/name).write_text('Broken result')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.used_steps(root) == 3
    if damage == 'task':
        c.write(later/'native-intent.json', dict(intent, task_sha256='0'*64))
    elif damage == 'attempt':
        c.write(later/'native-intent.json', dict(intent, attempt=True))
    elif damage == 'step':
        c.write(later/'native-intent.json', dict(intent, step=True))
    elif damage == 'root':
        c.write(later/'native-intent.json', dict(intent, root_intent_sha256='0'*64))
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
    intent = dict(settings_sha256=c.digest(root/'settings.json'), operation_id=operation,
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


def _startup_permit(tmp_path, monkeypatch):
    from scidiscovery.research_loop import startup_recovery as recovery
    from test_native_startup_recovery import scene
    root, options = scene(tmp_path)
    write = c.write
    def after_publication(file, value):
        write(file, value)
        if Path(file) == recovery.path(root, 3):
            raise RuntimeError('fixture stopped after authorization publication')
    with monkeypatch.context() as stopped:
        stopped.setattr(c, 'write', after_publication)
        with pytest.raises(RuntimeError, match='authorization publication'):
            recovery.start(root, 3, **options)
    return root, options, recovery.validate(root, 3)


def test_startup_and_final_permissions_never_read_execution_results(tmp_path, monkeypatch):
    from scidiscovery.research_loop import startup_recovery as recovery
    root, _, permit = _startup_permit(tmp_path, monkeypatch)
    original = c.read(Path(permit['originals']['intent']['path']))
    job = c.read(root/'jobs/003.json')
    intent = dict(step=3, attempt=1, continuation_kind='startup_successor', context_mode='new_context',
        task_sha256=permit['originals']['job']['sha256'], native_profile=permit['native_profile'],
        root_intent_sha256=permit['originals']['intent']['sha256'], previous=recovery.original_proof(root, 3),
        startup_recovery=recovery._ref(recovery.path(root, 3)), budget_slice=original['budget_slice'])
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        pure.setattr(recovery, 'reconciled_original', lambda *args, **kwargs: pytest.fail('Permission required result qualification'))
        assert recovery.validate(root, 3) == permit
        assert recovery.validate_intent(root, job, intent) == permit
        assert budget.final_a_return(root, job, intent)['step'] == 3
        assert budget.final_a_barrier(root)['step'] == 3


def test_startup_bad_original_keeps_permission_but_blocks_new_effects_and_classification(tmp_path, monkeypatch):
    from scidiscovery.research_loop import startup_recovery as recovery
    root, options, permit = _startup_permit(tmp_path, monkeypatch)
    outcome = Path(permit['originals']['outcome']['path'])
    outcome.write_bytes(outcome.read_bytes()+b' ')
    assert recovery.validate(root, 3) == permit
    with pytest.raises(ValueError, match='changed'):
        budget.usage_accounting(root, dict(observed_tokens=20, unknown_steps=[3]))
    def no_effect(*args, **kwargs):pytest.fail('Invalid original reached new native dispatch')
    from scidiscovery.research_loop import runtime
    monkeypatch.setattr(runtime, 'start', no_effect)
    with pytest.raises(ValueError, match='changed'):
        recovery.start(root, 3, **options)
    assert not (execution.origin(root, 3)/'continuations/001/native-intent.json').exists()


def test_startup_grant_does_not_prove_positive_engineering_facts(tmp_path, monkeypatch):
    from scidiscovery.research_loop import startup_recovery as recovery
    root, _, permit = _startup_permit(tmp_path, monkeypatch)
    # Deliberately construct a declaration with no positive result verdict.
    report = Path(permit['reconciliation']['path'])
    value = c.read(report)
    value['engineering_judgment']['provider_submission'] = 'unknown'
    c.write(report, value)
    permit['reconciliation'] = recovery._ref(report)
    c.write(recovery.path(root, 3), permit)
    recovery.validate(root, 3)
    with pytest.raises(ValueError, match='Positive independent'):
        budget.usage_accounting(root, dict(observed_tokens=20, unknown_steps=[3]))
    with pytest.raises(ValueError, match='Positive independent'):
        recovery.require_eligibility(root, 3)


@pytest.mark.process_e2e
def test_final_cursor_permission_is_separate_from_sealed_source_eligibility(tmp_path, monkeypatch):
    root = audit_fixture(tmp_path)
    refs = [dict(step=step, seal_sha256=c.digest(execution.origin(root, step)/'result-identity.json')) for step in (1, 2)]
    cursor = dict(role='A', parents=[1, 2], instruction='Review existing evidence', evidence_return=None,
                  correction_request=None, author_correction=None, source_refs=refs)
    begin = budget.begin_closeout(root, 2, 3, 'fixture final', final_only=True, resume_cursor=cursor)
    seal = execution.origin(root, 2)/'result-identity.json'
    seal.write_bytes(seal.read_bytes()+b' ')
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.closeout_state(root)['begin'] == begin
    events, _ = budget._events(root, c.settings(root))
    with pytest.raises(ValueError, match='accepted normal science'):
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
        assert budget.final_a_barrier(root)['step'] == 4
        assert anchor['final_return']['seq'] == budget.final_a_barrier(root)['seq']


def test_final_delivery_scope_read_is_pure_but_acceptance_keeps_result_gate(tmp_path, monkeypatch):
    from scidiscovery.research_loop import startup_recovery as recovery
    root, _, permit = _startup_permit(tmp_path, monkeypatch)
    decision = dict(step=3, job_sha256=permit['originals']['job']['sha256'], raw_sha256='a'*64, seal_sha256='b'*64)
    bound = dict(decision=decision, operation_id='c'*64)
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        assert budget.final_a_delivery(root, decision, bound) == permit['final_return']
    with pytest.raises((ValueError, FileNotFoundError)):
        budget.require_final_a_delivery_eligibility(root, decision, bound)
    assert not budget.closeout_state(root)['actions']


def test_historical_usage_reserve_permission_replay_does_not_read_results_or_charge(unknown_original, monkeypatch):
    root, record, session = unknown_original
    historical_charge(root, session)
    usage=native.usage(root)
    assert usage['observed_tokens']==usage['accounted_tokens']==120 and usage['reserved_tokens']==0
    snapshot = budget.snapshot
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        pure.setattr(budget, 'snapshot', snapshot)
        pure.setattr(budget, 'require_usage_reserve_eligibility', lambda *args, **kwargs: pytest.fail('Permission replay required usage qualification'))
        assert budget.snapshot(root)['max_tokens'] == 1000
        assert budget.used_steps(root) == 1
    (record/'usage.json').write_text('Broken usage original')
    assert budget.snapshot(root)['max_tokens'] == 1000
    with pytest.raises(ValueError, match='original changed'):
        budget.usage_accounting(root, dict(observed_tokens=120, unknown_steps=[1]))


def test_author_permissions_never_read_outcomes_call_results_or_scientific_context(pure_upgrade, monkeypatch):
    from scidiscovery.research_loop import author_closeout as author
    root, original, successor, *_ = pure_upgrade
    c.write(author.successor_path(root, 1), successor)
    job = c.read(root/'jobs/001.json')
    intent = dict(attempt=2, continuation_kind='author_closeout', context_mode='new_context',
                  author_closeout=author._ref(root, author.successor_path(root, 1)), task_sha256=original['job']['sha256'],
                  author_closeout_facts=dict(path='native-evidence/001/continuations/002/author-closeout-facts.json',
                                             sha256='a'*64, size_bytes=0))
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


def test_legacy_author_permission_does_not_validate_scientific_compilation(legacy_compiler, monkeypatch):
    from scidiscovery.research_loop import execution_inputs as inputs
    root, job, compiler, material, permit, *_ = legacy_compiler
    with monkeypatch.context() as pure:
        _forbid_results(pure)
        pure.setattr(inputs, 'verify_compilation', lambda *args, **kwargs: pytest.fail('Permission required scientific compiler output'))
        original=inputs._legacy_authorization(root, job, compiler, material, permit, 1, require_intent=True)
        assert original['job']['sha256']==c.digest(root/'jobs/002.json')


@pytest.mark.parametrize('failure', ['author', 'compiler'])
def test_legacy_client_requires_eligibility_before_publication(legacy_compiler, monkeypatch, failure):
    from scidiscovery.research_loop import author_closeout as author, execution_inputs as inputs
    root, job, compiler, material, permit, public, _ = legacy_compiler
    if failure=='author':
        def invalid(*args, **kwargs):raise ValueError('fixture author qualification failed')
        monkeypatch.setattr(author, 'require_original_eligibility', invalid)
    else:
        value=inputs.q._read(root,compiler)
        monkeypatch.setattr(inputs, 'verify_compilation', lambda *args, **kwargs:dict(value, job=dict(value['job'], sha256='0'*64)))
    with pytest.raises(ValueError):
        inputs.create_legacy_author_client(root, job, compiler, material, permit, 1, public)
    assert not public.exists()
    assert not (root/compiler['path']).with_name('author-client-001.json').exists()
