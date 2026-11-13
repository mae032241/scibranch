"""Retired FINAL rescue cannot bypass ordinary routing or consumption checks."""
import pytest

from scidiscovery.research_loop import accepted_budget, budget, cli, contracts as c, continuation, execution, profiles, runtime


@pytest.mark.parametrize('command', ['reconcile-final', 'native-startup-successor', 'native-startup-collect'])
def test_retired_cli_command_is_rejected_before_opening_instance(tmp_path, command):
    root = tmp_path / 'absent-instance'
    with pytest.raises(SystemExit) as error:
        cli.main([command, '--root', str(root), '--report', str(tmp_path / 'absent-report')])
    assert error.value.code == 2
    assert not root.exists()


def test_saved_startup_successor_cannot_enter_supported_continuation_chain(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native_usage
    from test_reboot_reentry import THREAD, interrupted_instance
    accept_node = budget.accept_node
    def accept_original(root, job):
        anchor = accept_node(root, job)
        record = execution.origin(root, job['step'])
        intent = dict(step=job['step'], task_ref=c.record_ref(root/'jobs/001.json'),
            budget_slice=anchor, **profiles.binding(c.settings(root, job), job))
        c.write(record/'native-intent.json', intent)
        accepted_budget.accept_native(root, job['step'], record, intent)
        return anchor
    # Publish the exact original while the reboot fixture still owns its
    # launch clock; its later identity consumer must observe this acceptance.
    with monkeypatch.context() as fixture:
        fixture.setattr(budget, 'accept_node', accept_original)
        root, originals = interrupted_instance(tmp_path, monkeypatch)
    # Retain hook metadata for the fixture's actual original transcript. The
    # supported reentry path validates this binding and the original thread event.
    record = execution.origin(root, 1)
    home = execution.home(root, 1)
    session = native_usage.stream_binding(home/'sessions/original.jsonl')
    session_binding = dict(source='native_metadata_hooks', thread_id=THREAD,
        home=str(home), workspace=str(c.workspace(root, 1)),
        path=session['path'], identity=session['identity'], offset=session['size_bytes'])
    observation = record/'usage-observation.json'
    c.write(observation, dict(coverage='unknown', session_binding=session_binding))
    originals[observation] = observation.read_bytes()
    monkeypatch.setattr(runtime, 'start', lambda *args: None)
    retained = continuation.reenter(root, 1)
    assert retained['continuation_kind'] == 'reentry'
    assert retained['session'] == session_binding
    current = execution.record(root, 1)
    assert execution.validate_records(root, 1) == [record, current]
    intent = c.read(current / 'native-intent.json')
    intent['continuation_kind'] = 'startup_successor'
    c.write(current / 'native-intent.json', intent)
    with pytest.raises(ValueError, match='kind differs from its frozen policy'):
        execution.validate_records(root, 1)
    assert all(path.read_bytes() == value for path, value in originals.items())
