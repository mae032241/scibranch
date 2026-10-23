from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from deploy.install_transaction import (
    _verify_managed_directory,
    begin_transaction,
    mark_managed_directory,
    remove_transaction_target,
    rollback_transaction,
)


@pytest.mark.process_e2e
def test_primary_installer_has_valid_shell_syntax() -> None:
    project_root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        ["bash", "-n", str(project_root / "deploy/install.sh")],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr










@pytest.mark.process_e2e
def test_installer_rejects_removed_case_plugin_before_installation(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    completed = subprocess.run(
        [str(project_root / "deploy/install.sh"), "--dry-run"],
        cwd=project_root,
        env={**os.environ, "SCID_PLUGINS": "tcad_artifact,ingaas_fig4",
             "SCID_WORKSPACE": str(workspace), "SCID_PYTHON": sys.executable},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode != 0
    assert "unknown local plugin: ingaas_fig4" in completed.stderr


def test_install_transaction_sqlite_snapshot_restore_remains_compatible(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "database/existing.sqlite3"
    existing.parent.mkdir(parents=True)
    with sqlite3.connect(existing) as connection:
        connection.execute("CREATE TABLE records (value TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO records VALUES ('before')")
    missing = tmp_path / "database/missing.sqlite3"
    outside = tmp_path / "outside-launcher"
    outside.write_text("old launcher\n", encoding="utf-8")
    launcher = tmp_path / "bin/launcher"
    launcher.parent.mkdir()
    launcher.symlink_to(outside)
    missing_config = tmp_path / "config/new.json"
    transaction = tmp_path / "transaction"
    begin_transaction(
        transaction,
        targets=(("launcher", launcher), ("missing-config", missing_config)),
        databases=(("existing", existing), ("missing", missing)),
    )
    launcher.unlink()
    launcher.write_text("new launcher\n", encoding="utf-8")
    missing_config.parent.mkdir()
    missing_config.write_text("new config\n", encoding="utf-8")
    with sqlite3.connect(existing) as connection:
        connection.execute("INSERT INTO records VALUES ('after')")
    with sqlite3.connect(missing) as connection:
        connection.execute("CREATE TABLE records (value TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO records VALUES ('after')")
    for database in (existing, missing):
        for suffix in ("-wal", "-shm"):
            Path(str(database) + suffix).write_bytes(suffix.encode())

    rollback_transaction(transaction)

    with sqlite3.connect(existing) as connection:
        assert connection.execute("SELECT value FROM records").fetchall() == [("before",)]
    assert not missing.exists()
    assert launcher.is_symlink()
    assert launcher.read_text(encoding="utf-8") == "old launcher\n"
    assert not missing_config.exists()
    for database in (existing, missing):
        assert not Path(str(database) + "-wal").exists()
        assert not Path(str(database) + "-shm").exists()


def test_tcad_deployment_examples_declare_both_direct_solvers() -> None:
    project_root = Path(__file__).resolve().parents[2]
    for relative in (
        "plugins/tcad_artifact/config/remote-runner.example.json",
        "plugins/tcad_artifact/config/execution-policy.example.json",
    ):
        value = json.loads((project_root / relative).read_text(encoding="utf-8"))
        direct = {
            item["solver_kind"]: item
            for item in value["tools"]
            if item["solver_kind"] in {"sprocess", "sdevice"}
        }
        assert set(direct) == {"sprocess", "sdevice"}
        assert direct["sprocess"]["executable"].endswith("/sprocess")
        assert direct["sdevice"]["executable"].endswith("/sdevice")
        assert len({item["profile_id"] for item in direct.values()}) == 2
        assert all(item["release_evidence"] for item in direct.values())


@pytest.mark.process_e2e
def test_ssh_runner_installer_cleans_up_without_scope_error(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    bytecode_before = set((project_root / "plugins/tcad_artifact/tcad_artifact").rglob("remote_runner_py36*.pyc"))
    private_config = tmp_path / "private-runner.json"
    private_config.write_bytes(
        (
            project_root
            / "plugins/tcad_artifact/config/remote-runner.example.json"
        ).read_bytes()
    )
    private_value = json.loads(private_config.read_text(encoding="utf-8"))
    private_value["tools"][0]["executable"] = "/private/bin/sprocess"
    private_value["tools"][1]["executable"] = "/private/bin/sdevice"
    private_config.write_text(json.dumps(private_value), encoding="utf-8")
    remote_root = tmp_path / "remote/.scidiscovery"
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        f"""#!{sys.executable}
import os
import pathlib
import sys
import tarfile

root = pathlib.Path(os.environ["SCID_FAKE_REMOTE_ROOT"])
root.mkdir(parents=True)
assert "tcad@192.0.2.34" in sys.argv
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    for member in archive:
        assert member.mtime == 0
        archive.extract(member, root)
print("fake SSH extraction: pass")
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o750)
    fake_vmrun = tmp_path / "vmrun"
    fake_vmrun.write_text(
        f"#!{sys.executable}\nimport sys\nsys.stdout.buffer.write(b'192.0.2.34\\r\\n')\n",
        encoding="utf-8",
    )
    fake_vmrun.chmod(0o750)
    environment = {
        **os.environ,
        "SCID_PROJECT_ROOT": str(project_root),
        "SCID_SSH_EXE": str(fake_ssh),
        "SCID_SSH_IDENTITY": "unused-test-key",
        "SCID_SSH_DESTINATION_FALLBACK": "tcad@192.0.2.10",
        "SCID_VMRUN_EXE": str(fake_vmrun),
        "SCID_VMX_PATH": "fixture.vmx",
        "SCID_SSH_HOST_KEY_ALIAS": "test-vm",
        "SCID_SSH_KNOWN_HOSTS": "unused-known-hosts",
        "SCID_REMOTE_RUNNER_ROOT": str(remote_root),
        "SCID_REMOTE_RUNNER_CONFIG": str(private_config),
        "SCID_FAKE_REMOTE_ROOT": str(remote_root),
    }
    completed = subprocess.run(
        [str(project_root / "deploy/install_ssh_tcad_runner.sh"), "install"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "unbound variable" not in completed.stderr
    assert "fake SSH extraction: pass" in completed.stdout
    runner = remote_root / "bin/scidiscovery-tcad-ssh-runner"
    assert runner.is_file()
    assert runner.read_bytes().startswith(b"#!/usr/bin/env python3\n")
    assert runner.stat().st_mode & 0o111
    config = json.loads((remote_root / "config/runner.json").read_text())
    assert config["exchange_root"] == str(remote_root / "exchange")
    assert config["result_root"] == str(remote_root / "state/runs")
    assert set(
        (project_root / "plugins/tcad_artifact/tcad_artifact").rglob(
            "remote_runner_py36*.pyc"
        )
    ) == bytecode_before


@pytest.mark.process_e2e
def test_ssh_runner_install_requires_an_explicit_private_config(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_ssh.chmod(0o750)

    completed = subprocess.run(
        [str(project_root / "deploy/install_ssh_tcad_runner.sh"), "install"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            key: value
            for key, value in {
                **os.environ,
                "SCID_PROJECT_ROOT": str(project_root),
                "SCID_PYTHON": sys.executable,
                "SCID_SSH_EXE": str(fake_ssh),
                "SCID_SSH_DESTINATION": "tcad@192.0.2.34",
            }.items()
            if key != "SCID_REMOTE_RUNNER_CONFIG"
        },
        timeout=10,
        check=False,
    )

    assert completed.returncode == 66
    assert "SCID_REMOTE_RUNNER_CONFIG" in completed.stderr


@pytest.mark.process_e2e
def test_ssh_runner_install_rejects_an_unedited_example_copy(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    private_config = tmp_path / "private-runner.json"
    private_config.write_bytes(
        (
            project_root
            / "plugins/tcad_artifact/config/remote-runner.example.json"
        ).read_bytes()
    )
    private_config.chmod(0o600)
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_ssh.chmod(0o750)

    completed = subprocess.run(
        [str(project_root / "deploy/install_ssh_tcad_runner.sh"), "install"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "SCID_PROJECT_ROOT": str(project_root),
            "SCID_PYTHON": sys.executable,
            "SCID_SSH_EXE": str(fake_ssh),
            "SCID_SSH_DESTINATION": "tcad@192.0.2.34",
            "SCID_REMOTE_RUNNER_CONFIG": str(private_config),
        },
        timeout=10,
        check=False,
    )

    assert completed.returncode != 0
    assert "unedited byte-for-byte copy" in completed.stderr


@pytest.mark.process_e2e
def test_ssh_runner_code_upgrade_reuses_transport_and_preserves_config(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    remote_root = tmp_path / "remote/scidiscovery-tcad"
    (remote_root / "bin").mkdir(parents=True)
    (remote_root / "config").mkdir()
    remote_runner = remote_root / "bin/scidiscovery-tcad-ssh-runner"
    remote_runner.write_bytes(b"#!/usr/bin/env python3\n# old runner\n")
    remote_runner.chmod(0o750)
    remote_config = remote_root / "config/runner.json"
    config_bytes = b'{"private":"must-remain-byte-identical"}\n'
    remote_config.write_bytes(config_bytes)

    resolver = tmp_path / "resolve-vm"
    resolver.write_text(
        f"#!{sys.executable}\nprint('192.0.2.34')\n",
        encoding="utf-8",
    )
    resolver.chmod(0o750)
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        f"""#!{sys.executable}
import os
import pathlib
import sys
import tarfile

root = pathlib.Path(os.environ["SCID_FAKE_REMOTE_ROOT"])
assert "tcad@192.0.2.34" in sys.argv
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|*") as archive:
    names = []
    for member in archive:
        names.append(member.name)
        archive.extract(member, root)
    assert names == [
        "bin",
        "bin/scidiscovery-tcad-ssh-runner",
    ]
    assert all(not name.startswith("config") for name in names)
print("fake SSH code-only extraction: pass")
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o750)
    transport = tmp_path / "tcad-transport.json"
    transport.write_text(
        json.dumps(
            {
                "max_transfer_bytes": 1024 * 1024,
                "transfer_chunk_bytes": 65536,
                "ssh_executable": str(fake_ssh),
                "identity_file": None,
                "destination": "tcad@192.0.2.10",
                "destination_resolver": [str(resolver)],
                "port": 22,
                "host_key_alias": "test-vm",
                "known_hosts_file": str(tmp_path / "known_hosts"),
                "remote_helper": str(remote_runner),
                "remote_config": str(remote_config),
                "remote_exchange_root": str(remote_root / "exchange"),
                "connect_timeout_seconds": 5,
                "operation_timeout_seconds": 30,
            }
        ),
        encoding="utf-8",
    )
    transport.chmod(0o640)

    completed = subprocess.run(
        [
            str(project_root / "deploy/install_ssh_tcad_runner.sh"),
            "upgrade-code",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "SCID_PROJECT_ROOT": str(project_root),
            "SCID_PYTHON": sys.executable,
            "SCID_TCAD_TRANSPORT_CONFIG": str(transport),
            "SCID_FAKE_REMOTE_ROOT": str(remote_root),
        },
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "fake SSH code-only extraction: pass" in completed.stdout
    assert remote_config.read_bytes() == config_bytes
    assert remote_runner.read_bytes() == (
        project_root
        / "plugins/tcad_artifact/tcad_artifact/remote_runner_py36.py"
    ).read_bytes()


@pytest.mark.process_e2e
def test_ssh_runner_code_upgrade_rejects_mismatched_remote_binding(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    fake_ssh.chmod(0o750)
    transport = tmp_path / "tcad-transport.json"
    transport.write_text(
        json.dumps(
            {
                "max_transfer_bytes": 1024 * 1024,
                "transfer_chunk_bytes": 65536,
                "ssh_executable": str(fake_ssh),
                "destination": "tcad@192.0.2.10",
                "remote_helper": "/home/tcad/scidiscovery-tcad/bin/scidiscovery-tcad-ssh-runner",
                "remote_config": "/home/tcad/other/config/runner.json",
                "remote_exchange_root": "/home/tcad/scidiscovery-tcad/exchange",
            }
        ),
        encoding="utf-8",
    )
    transport.chmod(0o640)

    completed = subprocess.run(
        [
            str(project_root / "deploy/install_ssh_tcad_runner.sh"),
            "upgrade-code",
            "--dry-run",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "SCID_PROJECT_ROOT": str(project_root),
            "SCID_PYTHON": sys.executable,
            "SCID_TCAD_TRANSPORT_CONFIG": str(transport),
        },
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert "remote_config must remain under the runner root" in completed.stderr


@pytest.mark.process_e2e
def test_ssh_runner_full_install_reuses_active_transport(
    tmp_path: Path,
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    remote_root = tmp_path / "remote/scidiscovery-tcad"
    remote_root.mkdir(parents=True)
    resolver = tmp_path / "resolve-vm"
    resolver.write_text(
        f"#!{sys.executable}\nprint('192.0.2.44')\n",
        encoding="utf-8",
    )
    resolver.chmod(0o750)
    fake_ssh = tmp_path / "ssh"
    fake_ssh.write_text(
        f"""#!{sys.executable}
import io
import os
import subprocess
import sys
import tarfile

assert "tcad@192.0.2.44" in sys.argv
payload = sys.stdin.buffer.read()
with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
    names = archive.getnames()
assert names == [
    "bin",
    "bin/scidiscovery-tcad-ssh-runner",
    "config",
    "config/runner.json",
]
completed = subprocess.run(
    sys.argv[-1],
    shell=True,
    input=payload,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
sys.stdout.buffer.write(completed.stdout)
sys.stderr.buffer.write(completed.stderr)
raise SystemExit(completed.returncode)
""",
        encoding="utf-8",
    )
    fake_ssh.chmod(0o750)
    remote_runner = remote_root / "bin/scidiscovery-tcad-ssh-runner"
    remote_config = remote_root / "config/runner.json"
    transport = tmp_path / "tcad-transport.json"
    transport.write_text(
        json.dumps(
            {
                "max_transfer_bytes": 1024 * 1024,
                "transfer_chunk_bytes": 65536,
                "ssh_executable": str(fake_ssh),
                "destination": "tcad@192.0.2.10",
                "destination_resolver": [str(resolver)],
                "remote_helper": str(remote_runner),
                "remote_config": str(remote_config),
                "remote_exchange_root": str(remote_root / "exchange"),
            }
        ),
        encoding="utf-8",
    )
    transport.chmod(0o640)
    private_config = tmp_path / "private-runner.json"
    value = json.loads(
        (
            project_root
            / "plugins/tcad_artifact/config/remote-runner.example.json"
        ).read_text(encoding="utf-8")
    )
    fake_tools = tmp_path / "tools"
    fake_tools.mkdir()
    for item in value["tools"]:
        executable = fake_tools / item["solver_kind"]
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o750)
        item["executable"] = str(executable)
        item["environment"] = {}
    private_config.write_text(json.dumps(value), encoding="utf-8")
    private_config.chmod(0o600)

    completed = subprocess.run(
        [
            str(project_root / "deploy/install_ssh_tcad_runner.sh"),
            "install-from-transport",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={
            **os.environ,
            "SCID_PROJECT_ROOT": str(project_root),
            "SCID_PYTHON": sys.executable,
            "SCID_TCAD_TRANSPORT_CONFIG": str(transport),
            "SCID_REMOTE_RUNNER_CONFIG": str(private_config),
            "SCID_FAKE_REMOTE_ROOT": str(remote_root),
        },
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "SSH TCAD runner transport install: pass" in completed.stdout
    installed = json.loads(remote_config.read_text(encoding="utf-8"))
    assert installed["exchange_root"] == str(remote_root / "exchange")
    assert installed["state_root"] == str(remote_root / "state")
    assert installed["result_root"] == str(remote_root / "state/runs")
    assert {item["solver_kind"] for item in installed["tools"]} == {
        "sprocess",
        "sdevice",
    }


def test_deployment_sources_are_machine_neutral() -> None:
    project_root = Path(__file__).resolve().parents[2]
    paths = [
        project_root / "deploy/install.sh",
        project_root / "deploy/install_ssh_tcad_runner.sh",
        *sorted((project_root / "plugins/tcad_artifact/config").glob("*.json")),
    ]
    rendered = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    assert not re.search(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b",
        rendered,
    )
    assert not re.search(
        r"(?:/home/(?!user(?:/|$)|tcad(?:/|$))[A-Za-z0-9._-]+/|"
        r"C:[/\\]Users[/\\](?!user(?:[/\\]|$))[A-Za-z0-9._-]+[/\\])",
        rendered,
        re.IGNORECASE,
    )
    assert 'SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"' in rendered
    assert "-name build" in (project_root / "deploy/install.sh").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("content", ["/home/" + "private-person/state",
                                       "192." + "168.1.1", "BEGIN " + "PRIVATE KEY"])
def test_release_scan_still_rejects_sensitive_test_fixtures(tmp_path, content):
    import runpy
    release = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/build_git_release.py"))
    path = tmp_path / "tests/fixtures/injected.txt"
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(RuntimeError, match="release scan failed"):
        release["_scan_release"](tmp_path)


def test_managed_directory_marker_names_owner_without_reading_scientific_contents(tmp_path,monkeypatch):
    target=tmp_path/"managed";target.mkdir()
    scientific=target/"data.csv";scientific.write_bytes(b"scientific original")
    nested=target/"skills";nested.mkdir();(nested/"SKILL.md").write_text("Exact selected Skill.")
    marker=target/".scidiscovery-managed.json"
    temporary=target/"..scidiscovery-managed.json.tmp"
    original_open=Path.open
    def open_selected(path,*args,**kwargs):
        assert path in (marker,temporary), "Must not read scientific originals or Skills"
        return original_open(path,*args,**kwargs)
    def no_scan(*args,**kwargs):
        pytest.fail("Ownership marker must not scan directory entries")
    monkeypatch.setattr(Path,"open",open_selected)
    monkeypatch.setattr(Path,"rglob",no_scan)
    monkeypatch.setattr(Path,"iterdir",no_scan)
    mark_managed_directory(target,name="selected-skills")
    _verify_managed_directory(target,name="selected-skills")
    assert json.loads(marker.read_bytes())==dict(manager="scidiscovery-install",name="selected-skills",schema_version=2)


@pytest.mark.parametrize("mutation",["manager","name","schema","extra","format"])
def test_managed_directory_rejects_changed_owner_marker(tmp_path,mutation):
    target=tmp_path/"managed";target.mkdir()
    mark_managed_directory(target,name="selected-skills")
    marker=target/".scidiscovery-managed.json"
    value=json.loads(marker.read_bytes())
    if mutation=="manager":value["manager"]="another-manager"
    elif mutation=="name":value["name"]="another-directory"
    elif mutation=="schema":value["schema_version"]=1
    elif mutation=="extra":value["content_sha256"]="retired-content-checksum"
    marker.write_bytes(json.dumps(value,sort_keys=True,separators=(",",":")).encode()+(b"\n" if mutation=="format" else b""))
    with pytest.raises(RuntimeError,match="ownership has changed"):
        _verify_managed_directory(target,name="selected-skills")


@pytest.mark.parametrize("operation",["mark","verify"])
@pytest.mark.parametrize("redirect",["root","marker"])
def test_managed_directory_rejects_symlink_root_or_marker(tmp_path,operation,redirect):
    actual=tmp_path/"actual";actual.mkdir()
    mark_managed_directory(actual,name="selected-skills")
    target=actual
    if redirect=="root":
        target=tmp_path/"linked";target.symlink_to(actual,target_is_directory=True)
    else:
        marker=actual/".scidiscovery-managed.json"
        external=tmp_path/"owner.json";marker.rename(external);marker.symlink_to(external)
    function=mark_managed_directory if operation=="mark" else _verify_managed_directory
    with pytest.raises(RuntimeError):function(target,name="selected-skills")


@pytest.mark.parametrize("mismatch",["name","path","owner"])
def test_managed_directory_removal_requires_exact_transaction_target_and_owner(tmp_path,mismatch):
    target=tmp_path/"managed";target.mkdir();(target/"data.csv").write_bytes(b"original")
    mark_managed_directory(target,name="selected-skills")
    transaction=tmp_path/"transaction"
    begin_transaction(transaction,targets=(("skills",target),))
    other=tmp_path/"other";other.mkdir();mark_managed_directory(other,name="selected-skills")
    with pytest.raises(RuntimeError):
        remove_transaction_target(transaction,name="different" if mismatch=="name" else "skills",
            path=other if mismatch=="path" else target,
            managed_directory_name="different-owner" if mismatch=="owner" else "selected-skills")
    assert target.is_dir() and (target/"data.csv").read_bytes()==b"original"
    assert other.is_dir()


def test_managed_directory_removal_uses_owner_marker_after_contents_change(tmp_path):
    target=tmp_path/"managed";target.mkdir();(target/"data.csv").write_bytes(b"original")
    mark_managed_directory(target,name="selected-skills")
    transaction=tmp_path/"transaction"
    begin_transaction(transaction,targets=(("skills",target),))
    (target/"data.csv").write_bytes(b"updated scientific bytes")
    remove_transaction_target(transaction,name="skills",path=target,managed_directory_name="selected-skills")
    assert not target.exists()
    rollback_transaction(transaction)
    assert (target/"data.csv").read_bytes()==b"original"
    _verify_managed_directory(target,name="selected-skills")
