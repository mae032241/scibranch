"""Coordinate the selected SSH runner with the main install transaction."""
import argparse
import base64
import json
from pathlib import Path
import shlex
import uuid

from tcad_artifact.command_adapter import CommandAdapterConfig, bundled_ssh_configuration
from tcad_artifact.ssh_transport import read_transport_config, SSHRemoteClient, _transport_environment
from scidiscovery.plugin_runtime.collection import CollectionContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "activate", "rollback", "finish"))
    parser.add_argument("--command-config", type=Path, required=True)
    parser.add_argument("--transaction-root", type=Path, required=True)
    parser.add_argument("--runner-config", type=Path)
    args = parser.parse_args()
    receipt_path = args.transaction_root / "remote-tcad.json"
    if args.phase == "rollback" and not receipt_path.exists():
        return
    adapter = CommandAdapterConfig.model_validate_json(args.command_config.read_bytes(), strict=True)
    path = bundled_ssh_configuration(adapter)
    if path is None:
        if args.runner_config is not None:
            raise ValueError("SCID_REMOTE_RUNNER_CONFIG requires the bundled SSH transport")
        print("TCAD command executor is externally managed; bundled runner deployment is not applicable.")
        return
    config = read_transport_config(path)
    transport_record = config.model_dump(mode="json")
    if args.phase == "prepare":
        receipt = {"transaction": uuid.uuid4().hex, "transport_config": str(path),
            "transport_record": transport_record, "state": "preparing"}
        receipt_path.write_text(json.dumps(receipt))
        receipt_path.chmod(0o600)
    else:
        receipt = json.loads(receipt_path.read_bytes())
        if receipt["transport_config"] != str(path) or receipt["transport_record"] != transport_record:
            raise RuntimeError("TCAD transport changed during installation")
    packet = {"phase": args.phase, "transaction": receipt["transaction"],
        "runner": config.remote_helper, "config": config.remote_config}
    if args.phase == "prepare":
        source = Path(__file__).resolve().parents[1] / "tcad_artifact/remote_runner_py36.py"
        packet["source"] = base64.b64encode(source.read_bytes()).decode()
        defaults = json.loads((source.parents[1] / "config/remote-runner.example.json").read_bytes())
        packet["policy_defaults"] = defaults["agent_execution_policy"]
        packet["debug_defaults"] = defaults["debug"]
        packet["configuration"] = base64.b64encode(args.runner_config.read_bytes()).decode() if args.runner_config else None
    script = Path(__file__).with_name("remote_deployment.py").read_text()
    client = SSHRemoteClient(config)
    client.context = CollectionContext.for_seconds(config.operation_timeout_seconds)
    command = [config.ssh_executable, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "ConnectTimeout=" + str(config.connect_timeout_seconds), "-p", str(config.port)]
    for flag, value in (("-i", config.identity_file), ("-o", "HostKeyAlias=" + config.host_key_alias if config.host_key_alias else None),
                        ("-o", "UserKnownHostsFile=" + config.known_hosts_file if config.known_hosts_file else None)):
        if value: command.extend((flag, value))
    command.extend((client._destination(), "python3 -c " + shlex.quote(script)))
    result = client._run(command, input=json.dumps(packet).encode(),
        timeout=config.operation_timeout_seconds, env=_transport_environment(config.ssh_executable))
    if result.returncode:
        # Installer output is administrator-facing; scientific Worker diagnostics are separate.
        raise RuntimeError("Remote TCAD deployment failed; check runner configuration (SCID_REMOTE_RUNNER_CONFIG for an explicit replacement): "
            + result.stderr.decode(errors="replace")[-2000:])
    response = json.loads(result.stdout)
    receipt.update(response)
    receipt_path.write_text(json.dumps(receipt))
    print("Remote TCAD deployment: " + json.dumps(response))


if __name__ == "__main__":
    main()
