"""Explicit FINAL routing regressions; synthetic sealed facts, no native launch.

Saved scientific findings may be read at these boundaries. They must never be
requalified because a FINAL cursor retains a longer accepted prefix.
"""
from contextlib import contextmanager
from copy import deepcopy

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, handoff_state
from scidiscovery.research_loop import experiments, fork_runtime, fork_service, history, native, originals
from test_history_handoff import finish


def _prefix(tmp_path, length):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact synthetic findings; the scientific question remains unanswered.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=100000,
        max_seconds=120, max_steps=length+4, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external',
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=30) for role in 'HWAE'})
    with handoff_state.scope(root):
        for step in range(1, length+1):
            finish(root, step, 'E', [], text='Authored synthetic finding: the scientific result is unknown.',
                limitations=['Synthetic fixture; no scientific evidence or qualification.'])
    cursor = dict(role='E', parents=[length], instruction='Retain the unanswered question.',
        evidence_return=None, correction_request=None, author_correction=None,
        source_refs=[dict(step=step, seal_ref=c.record_ref(root / 'native-evidence' / f'{step:03d}' / 'result-identity.json'))
            for step in range(1, length+1)])
    return root, cursor


@contextmanager
def _no_old_verification(monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError('FINAL routing reopened accepted scientific verification')
    with monkeypatch.context() as patch:
        for name in ('validate', 'validate_original', '_validate_inputs'):
            patch.setattr(c, name, forbidden)
        for name in ('_validate_managed', '_observe_managed_calls', '_observe_scientific_outputs',
                '_accept_managed_input_coverage', 'validate_observation', 'validate_managed_original'):
            patch.setattr(history, name, forbidden)
        patch.setattr(experiments, 'verify_observation', forbidden)
        patch.setattr(originals, '_check_identity', forbidden)
        yield calls
    assert calls == []


@pytest.mark.parametrize('length', [2, 8, 16])
def test_final_begin_and_normal_resume_trust_saved_prefix(tmp_path, monkeypatch, length):
    root, cursor = _prefix(tmp_path, length)
    assert handoff_state.current(root) is None
    with _no_old_verification(monkeypatch):
        begin = budget.begin_closeout(root, length, length+1, 'fixture_final',
            final_only=True, resume_cursor=cursor)
        assert begin['resume_cursor'] == cursor
        # Journal reload uses the same accepted facts with no warm scope.
        assert budget.closeout_state(root)['begin'] == begin
        with pytest.raises(ValueError, match='explicit raised threshold'):
            budget.resume_final(root, begin, length+2)
    budget.amend(root, max_tokens=100001, authorization_note='Explicit fixture ceiling increase.')
    with _no_old_verification(monkeypatch):
        resumed = budget.resume_final(root, begin, length+2)
        assert budget.resume_cursor(root, resumed) == cursor
        assert budget.scientific_steps(root, length+2, resume=resumed) == list(range(1, length+1))


@pytest.mark.parametrize('change', ['seal_ref', 'missing_source', 'source_order', 'parent', 'final_source'])
def test_final_cursor_keeps_exact_source_identity_and_sequence(tmp_path, monkeypatch, change):
    root, cursor = _prefix(tmp_path, 2)
    bad = deepcopy(cursor)
    if change == 'seal_ref':
        bad['source_refs'][0]['seal_ref'] = dict(path=str(root / 'native-evidence/002/result-identity.json'))
    elif change == 'missing_source':
        bad['source_refs'].pop(0)
    elif change == 'source_order':
        bad['source_refs'].reverse()
    elif change == 'parent':
        bad['parents'] = [3]
    else:
        path = root / 'jobs/002.json'
        c.write(path, dict(c.read(path), final_only=True))
    with _no_old_verification(monkeypatch), pytest.raises(ValueError, match='Final resume'):
        budget.begin_closeout(root, 2, 3, 'fixture_final', final_only=True, resume_cursor=bad)
    assert budget.closeout_state(root)['begin'] is None


def test_normal_resume_rechecks_current_token_admission(tmp_path, monkeypatch):
    root, cursor = _prefix(tmp_path, 2)
    begin = budget.begin_closeout(root, 2, 3, 'fixture_final', final_only=True, resume_cursor=cursor)
    budget.amend(root, max_tokens=100001, authorization_note='Explicit fixture ceiling increase.')
    budget.resume_final(root, begin, 4)
    observed = [0]
    samples = []
    def meter(root, **kwargs):
        samples.append(observed[0])
        return dict(observed_tokens=observed[0], accounted_tokens=observed[0], unknown_steps=[])
    monkeypatch.setattr(native, 'usage', meter)
    job = dict(step=4, role='E')
    assert budget.node_admission(root, job, normal_resume_check=True) is None
    observed[0] = 100001
    assert budget.node_admission(root, job, normal_resume_check=True) == 'node_budget_unavailable_tokens'
    assert samples == [0, 100001]


def test_fork_final_cursor_trusts_inherited_and_local_seals(tmp_path, monkeypatch):
    source, _ = _prefix(tmp_path, 2)
    with controller._writer(source):
        pass
    child = tmp_path / 'fork'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=3)
    preview = fork_service.preview(source, 2, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready', preview
    created = fork_service.create(source, 2, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='final-trusted-history')
    assert created['state'] == 'paused'
    selected = fork_runtime.build_selected_job(child, 2)
    # The selected fork task is already frozen, including its source binding.
    # Author terminal fixture receipts directly instead of rebuilding that job.
    record = c.host_record(c.workspace(child, selected['step']))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(child / 'jobs/002.json')))
    c.write(record / 'science-output.json', dict(summary='A synthetic fork contribution',
        scientific_result='The fork retains an unanswered synthetic question.',
        limitations=['Synthetic fixture; no scientific evidence or qualification.'],
        implementation_selection=None))
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(child), raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(child, selected['step'])
    identity = originals.local_identity(source)
    inherited = originals.node(child, 1)
    local = originals.node(child, 2)
    assert inherited.inherited and inherited.origin_identity == identity
    assert inherited.original_root == str(source)
    assert not local.inherited and local.original_root == str(child)
    cursor = dict(role='E', parents=[1, 2], instruction='Retain the declared original findings.',
        evidence_return=None, correction_request=None, author_correction=None,
        source_refs=[dict(step=owner.step, seal_ref=history.source_seal_ref(owner))
            for owner in (inherited, local)])
    assert cursor['source_refs'] == [
        dict(step=1, seal_ref=dict(path=str(source / 'native-evidence/001/result-identity.json'))),
        dict(step=2, seal_ref=dict(path=str(child / 'native-evidence/002/result-identity.json')))]
    assert handoff_state.current(child) is None
    with _no_old_verification(monkeypatch):
        for change in ('seal_ref', 'parent', 'sequence'):
            bad = deepcopy(cursor)
            if change == 'seal_ref':
                bad['source_refs'][0]['seal_ref'] = dict(path=str(child / 'native-evidence/001/result-identity.json'))
            elif change == 'parent':
                bad['parents'] = [3]
            else:
                bad['source_refs'].reverse()
            with pytest.raises(ValueError, match='Final resume'):
                budget.begin_closeout(child, 2, 3, 'fixture_final', final_only=True, resume_cursor=bad)
        begin = budget.begin_closeout(child, 2, 3, 'fixture_final', final_only=True, resume_cursor=cursor)
        assert budget.closeout_state(child)['begin']['resume_cursor'] == cursor
        with pytest.raises(ValueError, match='explicit raised threshold'):
            budget.resume_final(child, begin, 4)
    budget.amend(child, max_tokens=1001, authorization_note='Explicit fork fixture ceiling increase.')
    with _no_old_verification(monkeypatch):
        resumed = budget.resume_final(child, begin, 4)
        assert budget.resume_cursor(child, resumed) == cursor
        assert budget.scientific_steps(child, 4, resume=resumed) == [1, 2]


def test_usage_accounting_does_not_read_unused_ledger(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Reported usage accounting loaded an unused control ledger')
    monkeypatch.setattr(budget, '_events', forbidden)
    for observed in (0, 37):
        assert budget.usage_accounting(tmp_path, dict(observed_tokens=observed)) == dict(
            reserved_tokens=0, accounted_tokens=observed, reserved_unknown_steps=[],
            blocking_unknown_steps=[], accounting_basis='observed_lower_bound')
