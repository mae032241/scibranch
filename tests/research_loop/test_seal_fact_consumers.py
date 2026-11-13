"""Sealing consumes its exact accepted policy, head and terminal receipts."""
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import (
    accepted_budget, accepted_prefix, budget, candidate_nominations,
    contracts as c, decision, formal_review, history, search, semantic,
)
from scidiscovery.research_loop.protocol import CURRENT
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_final_a_completion_contract import accept, complete, prepare


def forbid_control_loaders(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('A sealing consumer reopened produced control facts')

    monkeypatch.setattr(c, 'settings', unexpected)
    monkeypatch.setattr(accepted_budget, 'read', unexpected)
    monkeypatch.setattr(accepted_budget, 'load', unexpected)
    monkeypatch.setattr(budget, '_events', unexpected)
    return unexpected


def test_current_inventory_owner_consumes_terminal_writer_facts(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    job, intent = accept(root)
    record = c.host_record(c.workspace(root, job['step']))
    accepted_budget.accept_native(root, job['step'], record, intent)
    complete(root, job, intent)
    inventory = c.accept_current_inventory(root, job['step'])
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(accepted_budget.AcceptedState, 'row', unexpected)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'exists', unexpected)
        patch.setattr(Path, 'stat', unexpected)
        patch.setattr(Path, 'is_symlink', unexpected)
        c._validate_current_inventory_owner(root, job['step'], inventory, head=inventory.head)


@pytest.mark.parametrize('change', ['step', 'job_ref', 'native_record', 'status', 'current_native'])
def test_current_inventory_owner_rejects_changed_terminal_facts(tmp_path, monkeypatch, change):
    root = prepare(tmp_path)
    job, intent = accept(root)
    record = c.host_record(c.workspace(root, job['step']))
    accepted_budget.accept_native(root, job['step'], record, intent)
    complete(root, job, intent)
    inventory = c.accept_current_inventory(root, job['step'])
    head = accepted_budget.AcceptedState(root, copy.deepcopy(dict(inventory.head)), base=inventory.base)
    if change == 'step':
        head.current_job['step'] += 1
    elif change == 'job_ref':
        head.current_job['job_ref'] = dict(path='/different/job.json')
    elif change == 'native_record':
        head.current_job['native_record'] = 'different/native-record'
    elif change == 'status':
        head.current_job['status'] = 'prepared'
    else:
        head['current_native'] = dict(record=head.current_job['native_record'])
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(accepted_budget.AcceptedState, 'row', unexpected)

    with pytest.raises(ValueError, match='another task or attempt'):
        c._validate_current_inventory_owner(root, job['step'], inventory, head=head)


@pytest.mark.parametrize('status', ['terminal', 'sealed'])
def test_scientific_publication_job_consumes_supplied_record(tmp_path, monkeypatch, status):
    result = dict(path='/original/result.json')
    search_ref = dict(path='/original/breadth-snapshot.json')
    job = dict(step=1, role='E', job_ref=dict(path='/original/job.json'), status=status,
        result_ref=result, output_search_ref=search_ref)
    head = accepted_budget.AcceptedState(tmp_path,
        dict(current_job=job, current_native=None), base={})
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(accepted_budget.AcceptedState, 'row', unexpected)

    assert accepted_budget._scientific_publication_job(head, 1, job=job,
        job_ref=job['job_ref'], role='E', result_ref=result, search_ref=search_ref) is job


def final_receipt_facts(*, missing_meter=False):
    marker = dict(seq=7, record=dict(path='/original/control-ledger/000007.json'))
    intent = dict(budget_slice=dict(final_return=marker, previous_tokens=4))
    accepted = dict(step=3, anchor=dict(shared_limits=dict(max_tokens=100),
        limits=dict(max_observed_tokens=20)))
    meter = None if missing_meter else dict(input_tokens=10, cached_input_tokens=3,
        output_tokens=5)
    usage = dict(observed_tokens=120, accounted_tokens=120,
        unknown_steps=[3] if missing_meter else [])
    outcome = dict(returncode=0, stop_reason=None, cleanup='observed_group_cleaned',
        native_backend='codex', usage=usage, metering=dict(source='turn.completed',
            coverage='unknown' if missing_meter else 'terminal', tokens=meter))
    outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, usage)
    return intent, accepted, meter, outcome


@pytest.mark.parametrize('missing_meter', [False, True])
def test_final_receipt_consumes_produced_facts_without_reloading_or_refunding(
        monkeypatch, missing_meter):
    intent, accepted, meter, outcome = final_receipt_facts(missing_meter=missing_meter)
    original = copy.deepcopy((intent, accepted, meter, outcome))
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)

    assert budget._validate_final_a_receipt_from_facts(intent, outcome,
        policy=dict(native_launcher_argv=['codex']), meter=meter, accepted=accepted) == accepted

    receipt = outcome['final_a_completion']['final_overrun']
    assert receipt['shared_tokens_over_ceiling'] == 20
    assert receipt['observed_node_tokens'] == (None if missing_meter else 19)
    if missing_meter:
        assert receipt['usage_coverage'] == 'unknown'
    assert (intent, accepted, meter, outcome) == original


@pytest.mark.parametrize('change', ['receipt', 'cleanup', 'backend', 'source',
    'meter_tokens', 'missing_terminal_meter'])
def test_final_receipt_keeps_all_completion_and_accounting_gates(monkeypatch, change):
    intent, accepted, meter, outcome = final_receipt_facts()
    if change == 'receipt':
        outcome['final_a_completion']['final_overrun']['shared_tokens_over_ceiling'] = 0
    elif change == 'cleanup':
        outcome['cleanup'] = 'unknown'
    elif change == 'backend':
        outcome['native_backend'] = 'other'
    elif change == 'source':
        outcome['metering']['source'] = 'other.completed'
    elif change == 'meter_tokens':
        outcome['metering']['tokens'] = dict(meter, output_tokens=6)
    else:
        meter = None
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)

    with pytest.raises(ValueError, match='Final A completion'):
        budget._validate_final_a_receipt_from_facts(intent, outcome,
            policy=dict(native_launcher_argv=['codex']), meter=meter, accepted=accepted)


def test_normal_resume_projects_only_the_supplied_cut(tmp_path, monkeypatch):
    resume = dict(kind='closeout_resume', first_step=5, seq=8)
    events = [dict(kind='closeout_begin', first_step=3, seq=7), resume]
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)

    assert budget._normal_resume_from_facts(tmp_path, 4, events=events) is None
    assert budget._normal_resume_from_facts(tmp_path, 5, events=events) is resume


def test_current_input_resume_uses_the_same_accepted_head(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    job, _ = accept(root)
    base = c.settings(root)
    head = accepted_budget.load(root, base=base)
    policy = c.settings_for_job(root, job, base=base, head=head)
    changed = dict(job, normal_resume=dict(seq=999, record=dict(path='/other/resume')))
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(budget, 'normal_resume', unexpected)

    with pytest.raises(ValueError, match='exact normal resume boundary'):
        c._validate_inputs_from_facts(root, c.workspace(root, job['step']), changed,
            base=base, policy=policy, head=head)


@pytest.mark.parametrize('failure', ['missing', 'malformed', 'changed_raw'])
def test_seal_output_failures_consume_accepted_final_facts(tmp_path, monkeypatch, failure):
    root = prepare(tmp_path)
    job, intent = accept(root)
    record = c.host_record(c.workspace(root, job['step']))
    attempt_key = 'attempt/' + record.relative_to(root).as_posix().replace('/', ':')
    accepted = accepted_budget.accept_native(root, job['step'], record, intent)
    assert accepted.row(attempt_key)['intent'] == intent
    assert accepted.current_job['step'] == job['step']
    assert accepted.current_job['job_ref'] == intent['task_ref']
    meter, outcome = complete(root, job, intent)
    raw = record / 'science-output.json'
    if failure == 'missing':
        raw.unlink()
        outcome.update(output_status='missing', raw_ref=None)
    else:
        raw.write_text('{', encoding='utf-8')
        outcome['raw_ref'] = c.file_ref(raw)
    c.write(record / 'native-outcome.json', outcome)
    inventory = c.accept_current_inventory(root, job['step'])
    assert inventory.native_meter == meter
    terminal = inventory.head.row(attempt_key)
    assert terminal['intent'] == intent
    assert terminal['status'] == 'closed'
    assert terminal['meter'] == meter
    if failure == 'changed_raw':
        inventory.native_outcome['raw_ref']['size_bytes'] += 1
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    monkeypatch.setattr(c, '_native_output_identity', unexpected)
    monkeypatch.setattr(budget, 'final_a_return', unexpected)
    monkeypatch.setattr(budget, 'validate_final_a_receipt', unexpected)

    if failure == 'changed_raw':
        with pytest.raises(ValueError, match='Terminal scientific output'):
            c._seal_result(root, job['step'], inventory=inventory)
    else:
        with pytest.raises(OutputRepairRequired) as error:
            c._seal_result(root, job['step'], inventory=inventory)
        assert error.value.code == ('missing_output' if failure == 'missing' else 'invalid_json')
    assert not (record / 'result.json').exists()
    assert not (record / 'result-identity.json').exists()






@pytest.mark.parametrize('job,payload,policy', [
    (dict(role='W'), {}, dict(decision_contract_version=1)),
    (dict(role='A', purpose='closeout'), {}, dict(decision_contract_version=1)),
    (dict(role='A'), {}, dict(decision_contract_version=0)),
    (dict(role='A'), dict(next_action='finish'), dict(decision_contract_version=1)),
])
def test_public_decision_keeps_no_work_early_returns(tmp_path, monkeypatch,
        job, payload, policy):
    forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'settings', lambda root, selected: policy)
    assert decision.assess(tmp_path, job, payload) == []


def test_strict_consumers_reject_missing_owner_facts(tmp_path, monkeypatch):
    unexpected = forbid_control_loaders(monkeypatch)
    monkeypatch.setattr(c, 'read', unexpected)
    with pytest.raises(ValueError, match='produced accepted events'):
        budget._normal_resume_from_facts(tmp_path, events=None)
    with pytest.raises(ValueError, match='produced policy'):
        budget._validate_final_a_receipt_from_facts({}, {}, policy=None, meter=None, accepted=None)
    with pytest.raises(ValueError, match='produced policy, interpretation and accepted head'):
        decision._assess_from_facts(tmp_path, {}, {}, policy={}, interpretation={}, head=None)
