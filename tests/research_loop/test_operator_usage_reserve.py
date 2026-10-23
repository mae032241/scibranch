"""Historical contingency declarations stay immutable without estimated fees."""
import json

import pytest

from scidiscovery.research_loop import budget, contracts, controller, execution, native
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
    contracts.write(record / 'native-intent.json', dict(step=1,
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


def historical_charge(root, session, tokens=500):
    """Frozen event fixture for the retired API, never a live accounting grant."""
    from scidiscovery.research_loop import qualification as q
    record=execution.origin(root,1)
    originals=dict(job=root/'jobs/001.json',intent=record/'native-intent.json',outcome=record/'native-outcome.json',
        usage=record/'usage.json',observation=record/'usage-observation.json',stream=record/'native.jsonl',session=session)
    fields=dict(kind='native_usage_reserve',step=1,attempt=0,record='native-evidence/001',tokens=tokens,
        scope='author_closeout_and_successor_review_delivery',accounting_basis='operator_contingency_not_actual_usage_upper_bound',
        authorization_note='Historical event fixture',authorization_statement='Original recorded operator declaration',
        originals={name:q._ref(root,path) for name,path in originals.items()})
    budget.require_usage_reserve_eligibility(root,fields)
    settings=contracts.settings(root);events,parent=budget._events(root,settings)
    return budget._append(root,settings,events,parent,**fields)


def test_historical_reserve_retains_original_unknown_without_charging(unknown_original):
    root, record, session = unknown_original
    originals = {path: path.read_bytes() for path in record.iterdir() if path.is_file()}
    settings = (root / 'settings.json').read_bytes()
    historical_charge(root, session)
    ledger={p:p.read_bytes() for p in (root/'control-ledger').glob('*.json')}
    result = native.usage(root)
    assert result['observed_tokens'] == result['accounted_tokens'] == 120
    assert result['reserved_tokens'] == 0
    assert result['reserved_unknown_steps'] == []
    assert result['blocking_unknown_steps'] == []
    assert native.usage(root)['unknown_steps'] == [1]
    assert native.usage(root)==result
    assert all(p.read_bytes()==raw for p,raw in ledger.items())
    assert (root / 'settings.json').read_bytes() == settings
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert budget.usage_accounting(root, native.usage(root))['accounted_tokens'] == 120
    assert budget.usage_accounting(root,dict(observed_tokens=155,unknown_steps=[1]))['accounted_tokens']==155


def test_estimated_usage_api_and_cli_are_retired(unknown_original):
    root,record,session=unknown_original
    assert not hasattr(budget,'reserve_unknown_usage')
    with pytest.raises(SystemExit) as error:
        main(['usage-reserve','--root',str(root),'--step','1','--tokens','500','--session',str(session),
            '--authorization-note','Retired estimate','--authorization-statement','No actual usage'])
    assert error.value.code==2
    assert not (root/'control-ledger').exists()
    assert native.usage(root)['unknown_steps']==[1]
    assert native.usage(root)['observed_tokens']==native.usage(root)['accounted_tokens']==120


@pytest.mark.parametrize('name', ['native-intent.json', 'native-outcome.json', 'usage.json', 'usage-observation.json', 'native.jsonl', 'session'])
def test_reserve_fails_closed_when_any_original_changes(unknown_original, name):
    root, record, session = unknown_original
    historical_charge(root, session)
    path = session if name == 'session' else record / name
    path.write_bytes(path.read_bytes() + b' ')
    if name == 'native-intent.json':
        with pytest.raises(ValueError):budget.snapshot(root)
    else:
        assert budget.snapshot(root)['max_tokens'] == 1000
    with pytest.raises(ValueError):
        budget.usage_accounting(root, dict(observed_tokens=120, unknown_steps=[1]))


def test_historical_reserve_never_estimates_new_unknown_in_same_step(unknown_original, monkeypatch):
    root, record, session = unknown_original
    historical_charge(root, session)
    later = record / 'continuations/001'
    later.mkdir(parents=True)
    contracts.write(later / 'native-intent.json', dict(attempt=1))
    monkeypatch.setattr(execution, 'records', lambda root, step: [record, later])
    accounting = budget.usage_accounting(root, dict(observed_tokens=120, unknown_steps=[1]))
    assert accounting['reserved_unknown_steps'] == []
    assert accounting['blocking_unknown_steps'] == []
    assert accounting['accounted_tokens'] == 120


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
    event = contracts.read(root / 'control-ledger/000001.json')
    assert event['seq'] == result['amendment_seq']
    assert event['operation_id'] == operation['operation_id']
    assert event['before'] == dict(max_tokens=1000, max_seconds=200)
    assert event['after'] == dict(max_tokens=2000, max_seconds=200)
    assert event['authorization_statement'] == 'Finish this task.'
    assert event['max_steps_delta'] == 19
    ledger = {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}
    with pytest.raises(ValueError, match='positive integer'):
        budget.amend(root, max_steps=True, authorization_note='Invalid finite count')
    assert ledger == {path:path.read_bytes() for path in (root/'control-ledger').glob('*.json')}


def test_step_revision_requires_original_statement_without_appending(unknown_original):
    root, record, session = unknown_original
    with pytest.raises(ValueError, match='original operator statement'):
        budget.amend(root, max_steps=24, authorization_note='A note without its original authorization')
    assert not (root / 'control-ledger').exists()
