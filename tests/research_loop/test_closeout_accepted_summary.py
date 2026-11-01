"""Closeout reports consume accepted slots without rebuilding candidate science."""
from pathlib import Path

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution
from scidiscovery.research_loop import handoff_state, history, lineage, native, originals


def _snapshot(root, step, *, actual=None, recommendation=None, latest_a=None, inherited=False):
    original = root / 'original-parent' if inherited else root
    identity = dict(goal_id='parent' if inherited else 'local',
        settings_ref=dict(path=str(original / 'settings.json')))
    storage = root / 'host-runtime/fork' if inherited else root
    key = originals.identity_key(identity)
    snapshot_path = f'native-evidence/{step:03d}/breadth-snapshot.json'
    seal_path = f'native-evidence/{step:03d}/result-identity.json'
    def stored(path):
        return storage / f'inherited/{key}/{path}' if inherited else storage / path
    c.write(stored(snapshot_path), dict(actual_submission=actual, a_recommended=recommendation,
        latest_a_step=latest_a))
    c.write(stored(seal_path), dict(breadth_snapshot=dict(origin_identity=identity,
        path=snapshot_path, size_bytes=stored(snapshot_path).stat().st_size)))
    entries = tuple(dict(origin_identity=identity, path=path,
        storage_path=f'inherited/{key}/{path}', size_bytes=stored(path).stat().st_size)
        for path in (snapshot_path, seal_path)) if inherited else None
    return originals.NodeSource(identity, step, str(original), storage, entries=entries)


def _forbid_history(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Closeout report reopened historical candidate science')
    for name in ('rebuild', 'candidate_overview'):
        monkeypatch.setattr(history, name, forbidden)
    monkeypatch.setattr(lineage, 'candidate_view', forbidden)
    monkeypatch.setattr(c, 'validate', forbidden)
    monkeypatch.setattr(c, 'validate_original', forbidden)


def test_accepted_slots_keep_historical_cut_and_output_shape(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    old = _snapshot(root, 2, actual=dict(candidate_id='old', observed_at=2,
        artifact=dict(retention='archived', path='private-control-path'), evidence=['saved']),
        recommendation=dict(candidate_id='old', author_step=1), latest_a=1)
    _snapshot(root, 8, actual=dict(candidate_id='new', observed_at=8),
        recommendation=dict(candidate_id='new', author_step=8), latest_a=8)
    seen = []
    def node(root, step):
        seen.append(step)
        assert step == 2
        return old
    monkeypatch.setattr(originals, 'node', node)
    _forbid_history(monkeypatch)
    assert controller._closeout_accepted_slots(root, 2) == dict(
        actual_submission=dict(candidate_id='old', observed_at=2, retention='archived'),
        a_recommended=dict(candidate_id='old', author_step=1), latest_a_step=1)
    assert controller._closeout_accepted_slots(root, 0) == dict(
        actual_submission=None, a_recommended=None, latest_a_step=None)
    assert seen == [2]


def test_inherited_slots_use_exact_snapshot_origin_without_double_binding(tmp_path, monkeypatch):
    root = tmp_path / 'child'
    source = _snapshot(root, 2, inherited=True,
        actual=dict(candidate_id='parent-candidate', observed_at=1),
        recommendation=dict(candidate_id='source:grandparent:earlier', author_step=2), latest_a=2)
    monkeypatch.setattr(originals, 'node', lambda root, step: source)
    _forbid_history(monkeypatch)
    slots = controller._closeout_accepted_slots(root, 2)
    assert slots['actual_submission'] == dict(candidate_id='source:parent:parent-candidate',
        observed_at=1, retention='unknown')
    assert slots['a_recommended'] == dict(candidate_id='source:grandparent:earlier', author_step=2)


def test_closeout_report_keeps_cutoff_and_selected_closed_updates(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    nodes = {
        2: _snapshot(root, 2, actual=dict(candidate_id='prior', observed_at=2),
            recommendation=dict(candidate_id='prior', author_step=2), latest_a=2),
        # Node 3 was a previous FINAL excluded by this phase's cutoff+closed cut.
        4: _snapshot(root, 4, actual=dict(candidate_id='excluded', observed_at=3),
            recommendation=dict(candidate_id='excluded', author_step=3), latest_a=3),
        # An accepted empty A choice must clear the prior recommendation.
        5: _snapshot(root, 5, actual=dict(candidate_id='excluded', observed_at=3), latest_a=5),
    }
    for step in (4, 5):
        c.write(root / 'jobs' / f'{step:03d}.json', dict(step=step, role='W' if step == 4 else 'A', purpose='closeout'))
        c.write(root / 'native-evidence' / f'{step:03d}' / 'native-outcome.json', {})
    state = dict(begin=dict(mode='final', first_step=4, source_cutoff=2, seq=1),
        actions=[], unresolved_operations=[])
    monkeypatch.setattr(originals, 'node', lambda root, step: nodes[step])
    monkeypatch.setattr(budget, 'closeout_state', lambda root: state)
    monkeypatch.setattr(budget, 'status_snapshot', lambda root: {})
    monkeypatch.setattr(budget, 'used_steps', lambda root: 5)
    monkeypatch.setattr(accepted_budget, 'read', lambda root: dict(max_local_step=5))
    monkeypatch.setattr(native, 'usage', lambda root: {})
    monkeypatch.setattr(controller.configuration_updates, 'current', lambda root: {})
    monkeypatch.setattr(handoff_state, 'read_sealed', lambda root, step:
        dict(role='W' if step == 4 else 'A', next_action='finish', limitations=['Accepted fixture limitation.']))
    monkeypatch.setattr(execution, 'raw', lambda root, step: Path(root) / f'science-{step}.json')
    monkeypatch.setattr(execution, 'record', lambda root, step: Path(root) / 'native-evidence' / f'{step:03d}')
    _forbid_history(monkeypatch)
    result = controller._closeout_result(root, 'delivery_unknown', read_only=True)
    assert result['status'] == 'delivery_unknown'
    assert result['source_cutoff'] == 2 and result['last_step'] == 5
    assert result['previous_submission_observation'] == dict(candidate_id='prior', observed_at=2, retention='unknown')
    assert result['existing_A_recommendation'] is None
    # With only W4 selected, its cumulative snapshot must not import excluded A3.
    (root / 'jobs/005.json').unlink()
    result = controller._closeout_result(root, 'delivery_unknown', read_only=True)
    assert result['last_step'] == 4
    assert result['existing_A_recommendation'] == dict(candidate_id='prior', author_step=2)
