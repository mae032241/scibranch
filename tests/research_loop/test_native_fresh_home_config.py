"""Real native configuration loading without a model or scientific submission."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts, controller, execution, native, native_execution


def _actual_native():
    if sys.platform != 'linux':
        pytest.skip('The real Linux sandbox configuration loader is required')
    explicit = os.environ.get('SCID_TEST_CODEX_NATIVE')
    if explicit:
        actual = Path(explicit)
        assert actual.is_file(), 'Explicit installed Codex native is missing'
        return actual.resolve()
    entry = shutil.which('codex')
    if entry is None:
        pytest.skip('An installed Codex native is required')
    package = Path(entry).resolve().parent.parent
    architecture, target = {
        'x86_64': ('x64', 'x86_64-unknown-linux-musl'),
        'aarch64': ('arm64', 'aarch64-unknown-linux-musl'),
    }[os.uname().machine]
    actual = package / 'node_modules/@openai' / ('codex-linux-' + architecture) / 'vendor' / target / 'bin/codex'
    if not actual.is_file():
        pytest.skip('Set SCID_TEST_CODEX_NATIVE to the installed native binary')
    return actual.resolve()


@pytest.mark.process_e2e
def test_fresh_home_loads_actual_native_overrides_and_rejects_retired_mcp_override(tmp_path, monkeypatch):
    actual = _actual_native()
    bwrap = shutil.which('bwrap')
    if bwrap is None:
        pytest.skip('The actual native scientific namespace requires bwrap')
    home = tmp_path / 'codex-home'
    home.mkdir(mode=0o700)
    config = home / 'config.toml'
    profile = native_execution.PERMISSION_PROFILE
    configuration = ('model = "no-provider-fixture"\n'
        'default_permissions=' + json.dumps(profile) + '\n'
        '[permissions.' + profile + ']\nextends=":workspace"\n'
        '[permissions.' + profile + '.network]\nenabled=false\n')
    config.write_text(configuration)
    monkeypatch.setenv('CODEX_HOME', str(home))
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Configuration loading only; no scientific or model work.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['codex'], max_tokens=1000,
        max_seconds=60, max_steps=2, model='no-provider-fixture', reasoning_effort='high',
        search_policy=None, node_budget_mode='soft')
    job = contracts.build_job(root, 1, 'H', [])
    intent = dict(task_ref=contracts.record_ref(root / 'jobs/001.json'),
        **native.profiles.binding(contracts.settings(root), job))
    anchor = native.control_budget.accept_node(root, job)
    if anchor is not None:
        intent['budget_slice'] = anchor
    contracts.write(execution.origin(root, 1) / 'native-intent.json', intent)
    captured = []

    def capture_before_submission(args, **kwargs):
        captured.extend(args)
        raise RuntimeError('Captured argv before native submission')

    # Exercise production argv construction without starting exec or a provider.
    with monkeypatch.context() as capture:
        capture.setattr(native.subprocess, 'Popen', capture_before_submission)
        with pytest.raises(RuntimeError, match='Captured argv before native submission'):
            native.run_native(root, 1)
    assert captured[:2] == ['codex', 'exec']
    configs = [captured[index + 1] for index, value in enumerate(captured) if value == '-c']
    assert 'features.multi_agent=false' in configs
    assert 'approval_policy="never"' in configs
    assert captured[captured.index('--sandbox') + 1] == 'workspace-write'

    # sandbox loads the same typed Codex configuration, then executes only true.
    # --help would bypass loading and could not detect the malformed MCP entry.
    work = contracts.workspace(root, 1)
    # Mirror native_execution._launch's proven outer namespace and current
    # vendor sandbox interface, including its private /task-tmp socket directory.
    command = [bwrap, '--unshare-user', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
        '--die-with-parent', '--new-session', '--clearenv', '--proc', '/proc',
        '--dev', '/dev', '--tmpfs', '/tmp']
    runtime_roots = [Path(path) for path in ('/usr', '/bin', '/lib', '/lib64')]
    for path in runtime_roots:
        if path.exists():
            command.extend(['--ro-bind', str(path), str(path)])
    if not any(actual.is_relative_to(path) for path in runtime_roots):
        command.extend(['--ro-bind', str(actual), str(actual)])
    command.extend(['--bind', str(work), str(work), '--ro-bind', str(config), '/task-native/config.toml',
        '--tmpfs', '/task-tmp', '--chdir', str(work)])
    environment = dict(PATH='/usr/bin:/bin', HOME='/task-tmp', CODEX_HOME='/task-native',
        TMPDIR='/task-tmp', XDG_RUNTIME_DIR='/task-tmp', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    for key, value in environment.items():
        command.extend(['--setenv', key, value])
    for name in ('.git', '.agents', '.codex', '.aws'):
        (work / name).mkdir(mode=0o700, exist_ok=True)
    command.extend(['/bin/sh', '-c', 'umask 077; chmod 700 /task-tmp; exec "$@"',
        'scid-scientific-sandbox', str(actual), 'sandbox', '-P', profile, '-C', str(work)])
    for setting in configs:
        command.extend(['-c', setting])
    command.extend(['-c', 'model=' + json.dumps(captured[captured.index('--model') + 1])])
    loaded = subprocess.run([*command, '--', '/usr/bin/true'], cwd=work,
        env=dict(PATH=os.defpath), capture_output=True, text=True, timeout=20)
    assert loaded.returncode == 0, loaded.stderr + loaded.stdout
    rejected = subprocess.run([*command, '-c', 'mcp_servers.scidiscovery.enabled=false',
        '--', '/usr/bin/true'], cwd=work, env=dict(PATH=os.defpath),
        capture_output=True, text=True, timeout=20)
    assert rejected.returncode != 0
    assert 'invalid transport' in rejected.stderr
    assert 'mcp_servers.scidiscovery' in rejected.stderr
    assert config.read_text() == configuration
    assert not (home / 'auth.json').exists()
    assert not (home / 'sessions').exists()
