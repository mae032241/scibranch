"""Historical contingency declarations stay immutable without estimated fees."""
import json

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts, controller, execution, native
from scidiscovery.research_loop.cli import main


@pytest.fixture
def unknown_original(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Finish retained observations without rerunning experiments.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=200, max_steps=5, model='fixture', reasoning_effort='high')
    contracts.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    record.mkdir(parents=True, exist_ok=True)
    accepted_budget.accept_native(root, 1, record, dict(step=1,
        task_ref=contracts.record_ref(root / 'jobs/001.json')))
    meter = dict(input_tokens=100, cached_input_tokens=50, output_tokens=20)
    observation = dict(coverage='unknown', reason='terminal_usage_unreported',
        thread_id='fixture-thread', tokens=meter)
    contracts.write(record / 'usage.json', meter)
    contracts.write(record / 'usage-observation.json', observation)
    contracts.write(record / 'native-outcome.json', dict(returncode=-15, stop_reason='token_limit',
        cleanup='observed_group_cleaned', thread_id='fixture-thread', metering=observation))
    (record / 'native.jsonl').write_text('{"type":"thread.started"}\n')
    session = root / 'native-homes/001/sessions/fixture-thread.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta', payload=dict(id='fixture-thread',
        cwd=str(contracts.workspace(root, 1))))) + '\n')
    monkeypatch.setattr(budget, 'execution_blocked', lambda *args, **kwargs: False)
    return root, record, session


def test_step_amendment_is_finite_audited_and_preserves_settings(unknown_original, capsys):
    root, record, session = unknown_original
    original = (root / 'settings.json').read_bytes()
    originals = {path:path.read_bytes() for path in [session, *record.iterdir()] if path.is_file()}
    assert main(['budget-amend', '--root', str(root), '--max-tokens', '2000', '--max-steps', '24',
        '--authorization-note', 'Permit finite review and audit steps.', '--authorization-statement', 'Finish this task.']) == 0
    operation = json.loads(capsys.readouterr().out)
    assert operation['status'] == 'applied' and operation['kind'] == 'budget_amend'
    assert operation == contracts.read(root / 'host-control/operations' / f'{operation["operation_id"]}.json')
    result = operation['result']
    assert result['status'] == 'budget_amended' and result['consumption'] == 'retained'
    assert result['before_max_steps'] == 5
    assert result['after_max_steps'] == 24
    assert result['max_steps_delta'] == 19
    assert budget.effective_max_steps(root) == 24
    assert budget.snapshot(root)['original_max_steps'] == 5
    assert budget.snapshot(root)['max_seconds'] == 200
    assert budget.used_steps(root) == 1
    assert (root / 'settings.json').read_bytes() == original
    assert all(path.read_bytes() == raw for path,raw in originals.items())
    assert native.usage(root)['observed_tokens'] == native.usage(root)['accounted_tokens'] == 120
    assert native.usage(root)['unknown_steps'] == [1]
    head = accepted_budget.read(root)
    event = head.event(head.row('operation/'+operation['operation_id'])['ref'])
    assert event['seq'] == result['amendment_seq']
    assert event['operation_id'] == operation['operation_id']
    assert event['before'] == dict(max_tokens=1000, max_seconds=200)
    assert event['after'] == dict(max_tokens=2000, max_seconds=200)
    assert event['authorization_statement'] == 'Finish this task.'
    assert event['max_steps_delta'] == 19
    ledger = {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    head = dict(accepted_budget.read(root))
    with pytest.raises(ValueError, match='positive integer'):
        budget.amend(root, max_steps=True, authorization_note='Invalid finite count')
    assert ledger == {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    assert dict(accepted_budget.read(root)) == head


def test_step_revision_requires_original_statement_without_appending(unknown_original):
    root, record, session = unknown_original
    ledger = {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    head = dict(accepted_budget.read(root))
    with pytest.raises(ValueError, match='original operator statement'):
        budget.amend(root, max_steps=24, authorization_note='A note without its original authorization')
    assert ledger == {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    assert dict(accepted_budget.read(root)) == head
