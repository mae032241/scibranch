"""Local configuration admission and diagnostics through the actual command boundary."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from scidiscovery.operation_contract import DiagnosticError
from tcad_artifact.command_adapter import (
    CommandAdapterConfig, CommandTCADExecutorAdapter, validate_command_configuration,
)
from tcad_artifact.ssh_transport import (
    SSHTCADTransportConfig, SSHTransportConfigurationError, read_transport_config,
)


def transport_config(tmp_path, **changes):
    path = tmp_path / "transport.json"
    path.write_text(json.dumps(dict(ssh_executable="/must/not/run/ssh",
        destination="user@private-host", remote_helper="/private/runner",
        remote_config="/private/config", remote_exchange_root="/private/exchange",
        identity_file="/private/key", max_transfer_bytes=2_000_000_000, **changes)))
    path.chmod(0o600)
    return path


def command_config(path):
    return CommandAdapterConfig(executable=sys.executable,
        arguments=("-m", "tcad_artifact.ssh_transport", "--config", str(path)),
        environment={"PYTHONPATH": os.pathsep.join(sys.path)})


def test_tcad_explicit_file_socket_preflight_is_read_only(tmp_path, monkeypatch):
    from scidiscovery.plugin_configuration import PluginConfigurationError
    from scidiscovery.research_loop import experiments
    from tcad_artifact import managed_execution as managed
    monkeypatch.setattr(experiments, 'registration', lambda executor: (managed.REGISTRATION, executor.partition(':')[2]))
    monkeypatch.setattr(managed, 'build_adapter', lambda *a, **k: pytest.fail('preflight constructed adapter'))
    plugin = tmp_path / 'tcad.toml'; plugin.write_text('transport = "socket"\nsocket_path = "./executor.sock"\n')
    before = sorted(tmp_path.iterdir())
    frozen = experiments.operator_configuration(dict(executor='tcad_execution:tcad', config_file=plugin.name), base=tmp_path)
    assert frozen['configuration'] == dict(transport='socket', socket_path=str(tmp_path / 'executor.sock'))
    assert sorted(tmp_path.iterdir()) == before
    plugin.unlink()
    assert experiments.validate_operator_configuration(frozen) == frozen
    with pytest.raises(PluginConfigurationError) as caught:
        experiments.operator_configuration(dict(executor='tcad_execution:tcad', configuration=frozen['configuration']))
    assert caught.value.code == 'plugin_config_file_required'


@pytest.mark.parametrize('problem', ['command_missing', 'command_invalid', 'ssh_missing', 'ssh_invalid', 'credential_missing', 'blank_socket', 'binding'])
def test_tcad_nested_preflight_reports_safe_action_before_runtime(tmp_path, monkeypatch, problem):
    from scidiscovery.plugin_configuration import PluginConfigurationError
    from scidiscovery.research_loop import experiments
    from tcad_artifact import managed_execution as managed
    monkeypatch.setattr(experiments, 'registration', lambda executor: (managed.REGISTRATION, executor.partition(':')[2]))
    monkeypatch.setattr(managed, 'build_adapter', lambda *a, **k: pytest.fail('preflight constructed adapter'))
    ssh = transport_config(tmp_path, transfer_chunk_bytes=0 if problem == 'ssh_invalid' else 65536)
    if problem == 'credential_missing':
        executable = tmp_path / 'ssh'
        executable.write_text('#!/bin/sh\nexit 99\n')
        executable.chmod(0o700)
        identity = tmp_path / 'identity'
        identity.write_text('must not read credential contents')
        transport = json.loads(ssh.read_text())
        transport.update(ssh_executable=str(executable), identity_file=str(identity))
        ssh.write_text(json.dumps(transport))
    command = tmp_path / 'command.json'
    body = command_config(ssh).model_dump(mode='json')
    body['arguments'][-1] = ssh.name
    command.write_text(json.dumps(body))
    plugin = tmp_path / 'tcad.toml'; plugin.write_text('transport = "command"\ncommand_config_path = "command.json"\n')
    if problem == 'command_missing': command.unlink()
    elif problem == 'command_invalid': command.write_text('{"private_token":"secret",')
    elif problem == 'ssh_missing': ssh.unlink()
    elif problem == 'credential_missing': identity.unlink()
    elif problem == 'blank_socket': plugin.write_text('transport = "socket"\nsocket_path = ""\n')
    selection = dict(executor='tcad_execution:' + ('misspelled' if problem == 'binding' else 'tcad'), config_file=str(plugin))
    before = sorted(tmp_path.iterdir())
    with pytest.raises(PluginConfigurationError) as caught:
        experiments.operator_configuration(selection)
    error = caught.value
    public = str(error) + error.hint
    assert error.plugin_id == 'tcad_execution'
    assert error.field_path == ('executor.config.executor' if problem == 'binding' else 'executor.config.config_file')
    if problem == 'ssh_invalid': assert 'transfer_chunk_bytes' in error.hint
    if problem == 'credential_missing': assert 'identity_file' in error.hint
    for private in (str(tmp_path), 'private-host', '/private/key', 'secret'):
        assert private not in public
    assert sorted(tmp_path.iterdir()) == before


def test_tcad_command_preflight_resolves_each_local_config_base(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments
    from tcad_artifact import managed_execution as managed
    from tcad_artifact.command_adapter import read_command_configuration, bundled_ssh_configuration
    directory = tmp_path / 'nested'; directory.mkdir()
    key = directory / 'key'; key.write_text('must not read credential contents')
    hosts = directory / 'hosts'; hosts.write_text('must not read known host contents')
    ssh = directory / 'ssh.toml'
    ssh.write_text(f'ssh_executable = {json.dumps(sys.executable)}\nidentity_file = "key"\nknown_hosts_file = "hosts"\ndestination = "user@configured-host"\nremote_helper = "/remote/helper"\nremote_config = "/remote/config"\nremote_exchange_root = "/remote/exchange"\nmax_transfer_bytes = 1024\n')
    command = directory / 'command.toml'
    command.write_text(f'executable = {json.dumps(sys.executable)}\narguments = ["-m", "tcad_artifact.ssh_transport", "--config", "ssh.toml"]\n')
    plugin = tmp_path / 'tcad.toml'; plugin.write_text('transport = "command"\ncommand_config_path = "nested/command.toml"\n')
    monkeypatch.setattr(experiments, 'registration', lambda executor: (managed.REGISTRATION, 'tcad'))
    monkeypatch.setattr(managed, 'build_adapter', lambda *a, **k: pytest.fail('preflight constructed adapter'))
    original = Path.open
    def no_credential_read(path, *args, **kwargs):
        if path in (key, hosts): pytest.fail('preflight read credential contents')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', no_credential_read)
    before = sorted(tmp_path.rglob('*'))
    frozen = experiments.operator_configuration(dict(executor='tcad_execution:tcad', config_file=str(plugin)))
    assert frozen['configuration']['command_config_path'] == str(command)
    assert bundled_ssh_configuration(read_command_configuration(command)) == ssh
    assert read_transport_config(ssh).identity_file == str(key)
    assert sorted(tmp_path.rglob('*')) == before


def test_optional_chunk_default_and_explicit_override(tmp_path):
    assert read_transport_config(transport_config(tmp_path)).transfer_chunk_bytes == 1024 * 1024
    assert read_transport_config(transport_config(tmp_path, transfer_chunk_bytes=65536)).transfer_chunk_bytes == 65536
    # The compatibility default never invents transfer authority.
    data = json.loads(transport_config(tmp_path).read_text())
    del data["max_transfer_bytes"]
    with pytest.raises(ValueError):
        SSHTCADTransportConfig.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize("value", [0, -1, "65536", True])
def test_invalid_explicit_chunk_is_rejected(tmp_path, value):
    with pytest.raises(SSHTransportConfigurationError):
        read_transport_config(transport_config(tmp_path, transfer_chunk_bytes=value))


@pytest.mark.parametrize("problem", ["invalid", "missing", "writable", "symlink"])
@pytest.mark.process_e2e
def test_command_failure_preserves_safe_repair_across_process_boundary(tmp_path, problem):
    path = transport_config(tmp_path, transfer_chunk_bytes=0 if problem == "invalid" else 65536)
    if problem == "missing":
        path.unlink()
    elif problem == "writable":
        path.chmod(0o666)
    elif problem == "symlink":
        target = path.with_suffix(".target")
        path.rename(target)
        path.symlink_to(target)
    adapter = CommandTCADExecutorAdapter(command_config(path), local_result_root=tmp_path / "results")
    with pytest.raises(DiagnosticError) as caught:
        adapter.capabilities()
    public = str(caught.value) + json.dumps(caught.value.details)
    assert caught.value.details[0]["code"] == "tcad_transport_configuration_invalid"
    assert caught.value.details[0]["repairable"] is False
    assert "administrator" in public and "before remote contact" in public
    for private in (str(tmp_path), "private-host", "/private/key", "identity_file"):
        assert private not in public
    # Full engineering facts stay available to the control plane.
    assert caught.value.engineering


@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("launcher", ["module", "script", "equals"])
def test_installer_checks_nested_configuration_before_writing(tmp_path, valid, launcher):
    path = transport_config(tmp_path, transfer_chunk_bytes=65536 if valid else 0)
    adapter = command_config(path)
    if launcher != "module":
        adapter = CommandAdapterConfig(executable="/not/executed/scidiscovery-tcad-transport",
            arguments=(f"--config={path}",) if launcher == "equals" else ("--config", str(path)))
    command_path = tmp_path / "command.json"
    command_path.write_text(adapter.model_dump_json())
    plugin = tmp_path / "plugin.json"
    plugin.write_text('{"previous":true}')
    script = Path(__file__).resolve().parents[2] / "plugins/tcad_artifact/deploy/configure_runtime.py"
    spec = importlib.util.spec_from_file_location("configure_tcad_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    arguments = dict(policy=tmp_path / "unused-policy", plugin_config=plugin,
        state_root=tmp_path, socket_path=tmp_path / "unused.sock", command_config=command_path)
    if valid:
        module.configure(**arguments)
        assert json.loads(plugin.read_text())["transport"] == "command"
    else:
        with pytest.raises(SSHTransportConfigurationError):
            module.configure(**arguments)
        assert json.loads(plugin.read_text()) == {"previous": True}


def test_generic_command_executor_has_no_assumed_ssh_configuration():
    validate_command_configuration(CommandAdapterConfig(executable="/external/executor"))


@pytest.mark.parametrize("existing_total", [None, 12345])
@pytest.mark.parametrize("existing_scope", [None, "scientific_subject"])
def test_local_policy_install_migrates_only_missing_cumulative_limit(tmp_path, existing_total, existing_scope):
    root = Path(__file__).resolve().parents[2] / "plugins/tcad_artifact"
    spec = importlib.util.spec_from_file_location("configure_tcad_budget_test", root / "deploy/configure_runtime.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = json.loads((root / "config/execution-policy.example.json").read_bytes())
    default_total = raw["agent_execution_policy"].pop("max_total_wall_time_seconds")
    default_scope = raw["debug"].pop("budget_scope")
    if existing_scope is not None:
        raw["debug"]["budget_scope"] = existing_scope
    raw["agent_execution_policy"]["max_wall_time_seconds"] = 900
    if existing_total is not None:
        raw["agent_execution_policy"]["max_total_wall_time_seconds"] = existing_total
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(raw))
    module.configure(policy=policy, plugin_config=tmp_path / "plugin.json", state_root=tmp_path,
        socket_path=tmp_path / "control.sock", command_config=None)
    expected = {**raw, "agent_execution_policy": {**raw["agent_execution_policy"],
        "max_total_wall_time_seconds": default_total if existing_total is None else existing_total},
        "debug": {**raw["debug"], "budget_scope": existing_scope or default_scope}}
    assert json.loads(policy.read_bytes()) == expected


@pytest.mark.parametrize("arguments", [(), ("--config",), ("--config", "relative.json"),
    ("--config", "/one", "--config", "/two")])
def test_bundled_command_requires_unambiguous_absolute_config(arguments):
    with pytest.raises(ValueError):
        validate_command_configuration(CommandAdapterConfig(
            executable="/usr/local/bin/scidiscovery-tcad-transport", arguments=arguments))
