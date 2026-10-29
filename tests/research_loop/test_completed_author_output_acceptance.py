"""Retired output acceptance is rejected before opening an instance."""
import pytest

from scidiscovery.research_loop import cli, contracts as c


def test_retired_worker_output_accept_is_rejected_before_root_access(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'missing-instance'

    def unexpected_access(*args, **kwargs):
        pytest.fail('A retired command must be rejected before reading instance settings.')

    monkeypatch.setattr(c, 'settings', unexpected_access)
    with pytest.raises(SystemExit) as caught:
        cli.main(['worker-output-accept', '--root', str(root)])
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert 'invalid choice' in error and 'worker-output-accept' in error
    assert not root.exists()
