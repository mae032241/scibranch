"""CLI shares fork service and retains exact user text; no native calls."""
import json

import pytest

from scidiscovery.research_loop import cli, fork_service
from test_execution_authority import prepare


def test_cli_preview_and_create_share_exact_request(tmp_path, monkeypatch, capsys):
    parent, _ = prepare(tmp_path, enabled=False)
    child = tmp_path / 'branch'
    guidance = tmp_path / 'original.txt'; guidance.write_text('  Exact text\n\n')
    calls = []
    def preview(*args, **options):
        calls.append(('preview', args, options))
        return dict(status='fork_preview_ready', preview_id='preview-one')
    def create(*args, **options):
        calls.append(('create', args, options))
        return dict(status='fork_created', state='paused')
    monkeypatch.setattr(fork_service, 'preview', preview)
    monkeypatch.setattr(fork_service, 'create', create)
    common = ['--root', str(parent), '--step', '200', '--child-root', str(child),
        '--max-tokens', '100', '--max-seconds', '20', '--max-steps', '2', '--guidance-file', str(guidance)]
    assert cli.main(['fork-preview', *common]) == 0
    assert json.loads(capsys.readouterr().out)['preview_id'] == 'preview-one'
    assert cli.main(['fork-create', *common, '--expected-preview-id', 'preview-one', '--operation-id', 'one']) == 0
    assert json.loads(capsys.readouterr().out)['state'] == 'paused'
    assert calls[0][1] == calls[1][1] == (parent, 200, child)
    assert calls[0][2]['guidance_text'] == calls[1][2]['guidance_text'] == '  Exact text\n\n'
    assert calls[1][2]['expected_preview_id'] == 'preview-one'
    assert not child.exists()


def test_cli_retains_machine_error_code(tmp_path, monkeypatch, capsys):
    parent, _ = prepare(tmp_path, enabled=False)
    def fail(*args, **options):
        raise fork_service.ForkError('fork_external_effect_unknown', 'Reconcile original call')
    monkeypatch.setattr(fork_service, 'preview', fail)
    assert cli.main(['fork-preview', '--root', str(parent), '--step', '2', '--child-root', str(tmp_path / 'child'),
        '--max-tokens', '100', '--max-seconds', '20', '--max-steps', '2']) == 1
    assert json.loads(capsys.readouterr().out)['code'] == 'fork_external_effect_unknown'


@pytest.mark.process_e2e
def test_real_cli_subprocess_creates_paused_and_reconciles_after_source_removal(tmp_path):
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import sys
    from test_fork_source import completed_source
    parent = completed_source(tmp_path)
    child = tmp_path / 'branch'
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2] / 'src')
    common = ['--root', str(parent), '--step', '2', '--child-root', str(child),
              '--max-tokens', '200', '--max-seconds', '30', '--max-steps', '2']
    def invoke(command, *extra):
        result = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli', command, *common, *extra],
            env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    preview = invoke('fork-preview')
    options = ['--expected-preview-id', preview['preview_id'], '--operation-id', 'process-one']
    receipt = invoke('fork-create', *options)
    assert receipt['state'] == 'paused' and (child / 'pause.request').exists()
    assert not (child / 'native-evidence').exists()
    shutil.rmtree(parent)
    assert invoke('fork-create', *options) == receipt
