"""Control ledger observations bind original settings; no native processes."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, configuration_updates, contracts as c, controller


def ledger(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the exact original control ledger identity.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=60, max_steps=4, model='fixture', reasoning_effort='high', search_policy=None)
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'source_fixture')
    return root


@pytest.mark.parametrize('resources', [False, True])
def test_events_reads_original_settings_once_without_latest_overlay(tmp_path, monkeypatch, resources):
    root = ledger(tmp_path)
    original = c.settings(root)
    settings = c.settings
    reads = []
    def read_original(selected, job=None):
        assert selected == root and job is None
        reads.append(selected)
        return settings(selected)
    monkeypatch.setattr(c, 'settings', read_original)
    monkeypatch.setattr(configuration_updates, 'current',
        lambda *args: pytest.fail('The immutable ledger cannot select a latest job overlay'))
    observed = budget._events(root, resources=resources)
    assert reads == [root]
    assert observed[0][-1]['settings_ref'] == c.record_ref(root / 'settings.json')
    assert settings(root) == original


@pytest.mark.parametrize('resources', [False, True])
def test_events_projects_explicit_accepted_facts_without_reloading(tmp_path, monkeypatch, resources):
    root = ledger(tmp_path)
    base = c.settings(root)
    state = accepted_budget.load(root, base=base)
    def forbidden(*args, **kwargs):
        pytest.fail('Projection must consume the supplied accepted facts, not reload a head or settings')
    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(accepted_budget, 'read', forbidden)
    monkeypatch.setattr(accepted_budget, 'load', forbidden)
    observed = budget._events_from_state(state, resources=resources)
    assert observed[0].state is state and observed[1] == state.parent
    assert observed[0][-1]['settings_ref'] == state['settings_ref']


@pytest.mark.parametrize('resources', [False, True])
@pytest.mark.parametrize('owner', ['head', 'published_tail'])
def test_events_rejects_another_settings_identity(tmp_path, resources, owner):
    root = ledger(tmp_path)
    head_path = root / accepted_budget.HEAD
    head = c.read(head_path)
    wrong = dict(path=str(root / 'other-settings.json'))
    if owner == 'head':
        c.write(head_path, dict(head, settings_ref=wrong))
        reason = 'Accepted budget head lost its original research identity'
    else:
        # Use the real clock publisher, then emulate a crash before head install.
        budget.transition(root, 'advance', 'published_tail_fixture')
        current = c.read(head_path)
        path = root / 'control-ledger' / f'{current["seq"]:06d}.json'
        event = c.read(path)
        c.write(head_path, head)
        c.write(path, dict(event, settings_ref=wrong))
        reason = 'New control event lost its accepted predecessor'
    retained = head_path.read_bytes()
    # The independent entry must acquire the damaged owner; a previously loaded
    # AcceptedEvents object would not exercise this integrity boundary.
    with pytest.raises(ValueError, match=reason):
        budget._events(root, resources=resources)
    # The explicit-facts acquisition path must enforce the same identity gate.
    with pytest.raises(ValueError, match=reason):
        budget._events_from_state(accepted_budget.load(root, base=c.settings(root)), resources=resources)
    assert head_path.read_bytes() == retained
