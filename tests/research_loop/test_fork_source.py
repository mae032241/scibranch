"""Frozen logical-task cuts; these fixtures launch no participant or solver."""
import shutil

import pytest

from scidiscovery.research_loop import contracts as c, controller, fork_sources, originals
from test_execution_authority import prepare
from test_history_handoff import finish
from test_composed_execution_results import composed_registry


def completed_source(tmp_path):
    root, _ = prepare(tmp_path, enabled=False)
    for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
        _, _, record = finish(root, step, role, parents, action=action)
        # The synthetic helper runs no process; declare that fact rather than
        # manufacturing a clean terminal for a real native process.
        outcome = c.read(record / 'native-outcome.json')
        c.write(record / 'native-outcome.json', dict(outcome, native_started=False))
    with controller._writer(root):
        pass
    return root


@pytest.mark.parametrize('latest_finished', [True, False])
def test_parent_completion_uses_numeric_latest_step(tmp_path, monkeypatch, latest_finished):
    root = completed_source(tmp_path)
    endpoint_results = {}
    for step, finished in ((999, not latest_finished), (1000, latest_finished)):
        c.write(root / 'jobs' / f'{step:03d}.json', dict(step=step, role='A' if finished else 'H'))
        # Sparse markers isolate latest-job selection from full seal validation.
        c.write(root / 'native-evidence' / f'{step:03d}' / 'result-identity.json', {})
        endpoint_results[c.workspace(root, step)] = dict(next_action='finish' if finished else 'analyze')
    validate = c.validate
    validated = []

    def validate_endpoint(work):
        if work in endpoint_results:
            validated.append(work)
            return endpoint_results[work]
        return validate(work)

    monkeypatch.setattr(c, 'validate', validate_endpoint)
    blocked = fork_sources._parent_state(root)

    assert validated == [c.workspace(root, 1000)]
    assert any(row['code'] == 'source_not_completed' for row in blocked) is not latest_finished
    assert all(row['code'] == 'source_not_completed' for row in blocked)


def test_selected_cut_excludes_selected_output_and_keeps_exact_inputs(tmp_path):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    paths = {row['path'] for row in plan['entries']}
    assert 'native-evidence/001/science-output.json' in paths
    assert 'project/nodes/002/inputs/parents/001/conclusion.json' in paths
    assert 'native-evidence/002/science-output.json' not in paths
    assert 'project/nodes/002/conclusion.json' not in paths
    assert plan['bootstrap']['next_step'] == 2
    assert [row['step'] for row in plan['prefix_entries']] == [1]
    assert plan['source']['job_ref']['sha256'] == c.digest(root / 'jobs/002.json')


def test_missing_input_and_changed_exact_selector_fail_closed(tmp_path):
    root = completed_source(tmp_path)
    ref = originals.original_ref(originals.node(root, 2), 'jobs/002.json')
    with pytest.raises(ValueError, match='identity changed'):
        fork_sources.inspect_source(root, dict(ref, sha256='0' * 64))
    (root / 'project/nodes/002/inputs/parents/001/conclusion.json').unlink()
    with pytest.raises((ValueError, OSError)):
        fork_sources.inspect_source(root, ref)


def test_changed_sealed_native_terminal_is_not_a_completed_source(tmp_path):
    root = completed_source(tmp_path)
    record = root / 'native-evidence/002'
    c.write(record / 'native-outcome.json', dict(returncode=None, cleanup='unknown'))
    with pytest.raises(ValueError, match='identity changed|terminal'):
        fork_sources.inspect_source(root, 2)


@pytest.mark.process_e2e
def test_completed_parent_with_original_unknown_managed_call_cannot_fork(tmp_path, composed_registry, monkeypatch):
    from scidiscovery.research_loop import execution, managed_wait
    from test_composed_execution_results import case
    root, _, _, _, _ = case(tmp_path, dict(collect=False))
    # A retained historical pre-wait seal is a genuine unknown call; it is not
    # manufactured by changing a sealed terminal or reconciling future effects.
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait, 'before_seal', lambda *args: None)
        finish(root, 2, 'W', [1], preserve_native_intent=True, mechanism_findings=[
            dict(mechanism=label, basis='blocked', finding='Original backend call remains pending.')
            for label in ('P', 'Q')])
    finish(root, 3, 'A', [1, 2], action='finish')
    seal = c.read(execution.origin(root, 2) / 'result-identity.json')
    observed = c.read(root / seal['observations']['path'])
    assert observed['managed_calls'][0]['status'] == 'unknown'
    for step in (1, 2, 3):
        path = execution.origin(root, step) / 'native-outcome.json'
        c.write(path, dict(c.read(path), native_started=False))
    with controller._writer(root):
        pass
    plan = fork_sources.inspect_source(root, 3)
    assert not any(row['code'] == 'source_not_completed' for row in plan['blocked_reasons'])
    assert any(row['code'] == 'unknown_managed_effect' for row in plan['blocked_reasons'])
    with pytest.raises(ValueError, match='unresolved original effects'):
        fork_sources.copy_source(plan, tmp_path / 'copy')


def test_copied_source_survives_parent_removal_and_keeps_public_original(tmp_path):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    before = (root / 'jobs/002.json').read_bytes()
    child = tmp_path / 'child'
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    shutil.rmtree(root)
    selected = fork_sources.selected_task(child)
    assert originals.resolve_ref(selected['source'], selected['source_job_ref']).read_bytes() == before
    assert selected['job']['step'] == 2
    assert fork_sources.validate_manifest(child, full=True)['source']['step'] == 2
    assert (child / originals.FORK_DIRECTORY / 'public/objective.md').read_text() == plan['selected_settings']['objective']
    assert not (child / 'jobs').exists()
