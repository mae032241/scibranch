"""Control ledger observations bind original settings; no native processes."""
import pytest

from scidiscovery.research_loop import budget, configuration_updates, contracts as c, controller


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


def test_events_rejects_another_settings_identity(tmp_path):
    root = ledger(tmp_path)
    path = root / 'control-ledger/000001.json'
    event = c.read(path)
    c.write(path, dict(event, settings_ref=dict(path=str(root / 'other-settings.json'))))
    with pytest.raises(ValueError, match='Control ledger identity'):
        budget._events(root)
