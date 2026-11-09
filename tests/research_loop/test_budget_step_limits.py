"""Read-only step ceilings use immutable amendments, never current metering."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, native


@pytest.fixture
def amended(tmp_path):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Retain exact originals under finite amended limits.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=200, max_steps=5, model='fixture', reasoning_effort='high')
    return root


def originals(root):
    """Exact control owners only; no instance-tree discovery."""
    head = c.read(root / accepted_budget.HEAD)
    paths = [root / name for name in ('settings.json', 'settings-identity.json', accepted_budget.HEAD)]
    paths += [root / 'control-ledger' / f'{seq:06d}.json' for seq in range(1, head['seq'] + 2)
        if (root / 'control-ledger' / f'{seq:06d}.json').exists()]
    return {path: path.read_bytes() for path in paths}


def revise(root):
    budget.amend(root, max_tokens=1200, max_steps=24, authorization_note='Finite review allowance.',
        authorization_statement='Finish the retained work.')
    budget.amend(root, max_tokens=1300, authorization_note='Revise only the cumulative token ceiling.')
    budget.amend(root, max_steps=12, authorization_note='Reduce the remaining control ceiling.',
        authorization_statement='Keep the review bounded.')


@pytest.mark.parametrize('has_amendments', [False, True])
def test_step_ceiling_is_readonly_without_current_clock_usage_or_snapshot(amended, monkeypatch, has_amendments):
    root = amended
    if has_amendments:
        revise(root)
    expected = 12 if has_amendments else 5
    assert budget.snapshot(root)['max_steps'] == expected
    retained = originals(root)
    def forbidden(*args, **kwargs):
        pytest.fail('A read-only step ceiling must not sample current time or consumption')
    monkeypatch.setattr(budget, 'snapshot', forbidden)
    monkeypatch.setattr(budget, 'clock_stamp', forbidden)
    monkeypatch.setattr(native, 'usage', forbidden)
    read = c.read
    def accepted_only(path):
        if path.parent == root / 'control-ledger':
            pytest.fail('Accepted ceilings must not lazily replay retired ledger originals')
        return read(path)
    monkeypatch.setattr(c, 'read', accepted_only)
    assert budget.effective_max_steps(root) == expected
    assert budget.step_limit(root) == expected
    assert all(path.read_bytes() == raw for path, raw in retained.items())
    assert c.settings(root)['max_steps'] == 5


def test_cold_step_reader_recovers_one_valid_published_amendment_without_writes(amended):
    root = amended
    path = root / accepted_budget.HEAD
    previous = c.read(path)
    budget.amend(root, max_steps=24, authorization_note='Finite recovery allowance.',
        authorization_statement='Retain all original consumption.')
    c.write(path, previous)
    retained = originals(root)
    assert budget.effective_max_steps(root) == budget.snapshot(root)['max_steps'] == 24
    assert budget.historical_step_limit(root) == 24
    assert all(item.read_bytes() == raw for item, raw in retained.items())


@pytest.mark.parametrize('changed,reason', [
    ('tokens_predecessor', 'Budget amendment predecessor changed'),
    ('steps_predecessor', 'Budget step amendment predecessor changed'),
    ('step_delta', 'Audited step amendment delta changed'),
    ('settings', 'Frozen instance settings changed'),
])
def test_readonly_step_ceiling_and_snapshot_reject_the_same_amendment_tamper(amended, monkeypatch, changed, reason):
    root = amended
    budget.amend(root, max_steps=24, authorization_note='Finite review allowance.',
        authorization_statement='Keep the exact original work.')
    # Reproduce a real interrupted writer: its event is durable, but its accepted
    # head replacement has not happened. Each reader must independently acquire
    # and validate that one tail, not replay an ignored historical journal row.
    head_path = root / accepted_budget.HEAD
    previous = c.read(head_path)
    amended = budget.amend(root, max_tokens=1300, max_steps=12,
        authorization_note='Reduce the remaining control ceiling.',
        authorization_statement='Keep the review bounded.')
    path = root / 'control-ledger' / f'{amended["amendment_seq"]:06d}.json'
    value = c.read(path)
    c.write(head_path, previous)
    if changed == 'settings':
        path = root / 'settings-identity.json'
        value = c.read(path);value['path'] = 'other-settings.json'
    elif changed == 'tokens_predecessor':
        value['before']['max_tokens'] += 1
    elif changed == 'steps_predecessor':
        value['before_max_steps'] = 23
        value['max_steps_delta'] = value['after_max_steps'] - 23
    else:
        value['max_steps_delta'] += 1
    c.write(path, value)
    retained = originals(root)
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid immutable ceilings must be rejected before sampling current time')
    monkeypatch.setattr(budget, 'clock_stamp', forbidden)
    monkeypatch.setattr(native, 'usage', forbidden)
    for read in (budget.effective_max_steps, budget.snapshot):
        with pytest.raises(ValueError, match=reason):
            read(root)
    assert all(path.read_bytes() == raw for path, raw in retained.items())
