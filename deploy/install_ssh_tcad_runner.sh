#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROJECT_ROOT="${SCID_PROJECT_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
readonly PYTHON="${SCID_PYTHON:-$(command -v python3 || true)}"
readonly SSH_EXE="${SCID_SSH_EXE:-$(command -v ssh || true)}"
readonly IDENTITY="${SCID_SSH_IDENTITY:-}"
readonly DESTINATION_OVERRIDE="${SCID_SSH_DESTINATION:-}"
readonly DESTINATION_FALLBACK="${SCID_SSH_DESTINATION_FALLBACK:-}"
readonly VMRUN_EXE="${SCID_VMRUN_EXE:-}"
readonly VMX_PATH="${SCID_VMX_PATH:-}"
readonly HOST_KEY_ALIAS="${SCID_SSH_HOST_KEY_ALIAS:-}"
readonly KNOWN_HOSTS="${SCID_SSH_KNOWN_HOSTS:-${HOME}/.ssh/known_hosts}"
readonly REMOTE_ROOT="${SCID_REMOTE_RUNNER_ROOT:-/home/${USER}/scidiscovery-tcad}"
readonly RUNNER_SOURCE="${PROJECT_ROOT}/plugins/tcad_artifact/tcad_artifact/remote_runner_py36.py"
readonly EXAMPLE_CONFIG_SOURCE="${PROJECT_ROOT}/plugins/tcad_artifact/config/remote-runner.example.json"
readonly CONFIG_SOURCE="${SCID_REMOTE_RUNNER_CONFIG:-${EXAMPLE_CONFIG_SOURCE}}"
readonly TRANSPORT_CONFIG="${SCID_TCAD_TRANSPORT_CONFIG:-/etc/scidiscovery/tcad-transport.json}"

quote_posix_shell() {
    "$PYTHON" -c \
        'import shlex,sys; print(shlex.quote(sys.argv[1]))' "$1"
}

validate_runner_syntax() {
    "$PYTHON" - "$RUNNER_SOURCE" <<'PY'
from pathlib import Path
import py_compile
import sys
import tempfile

with tempfile.TemporaryDirectory(prefix="scid-tcad-runner-compile-") as directory:
    py_compile.compile(
        sys.argv[1],
        cfile=str(Path(directory) / "remote_runner.pyc"),
        doraise=True,
    )
PY
}

validate_local() {
    [[ -n "$PYTHON" && -x "$PYTHON" ]] || {
        printf 'Local Python is unavailable: %s\n' "$PYTHON" >&2
        exit 66
    }
    [[ -n "$SSH_EXE" && -x "$SSH_EXE" ]] || {
        printf 'Windows SSH client is unavailable: %s\n' "$SSH_EXE" >&2
        exit 66
    }
    [[ -f "$RUNNER_SOURCE" && -f "$CONFIG_SOURCE" ]] || {
        printf 'Runner sources are incomplete.\n' >&2
        exit 66
    }
    [[ "$REMOTE_ROOT" =~ ^/[A-Za-z0-9_./-]+$ ]] || {
        printf 'Remote runner root is not a safe absolute path: %s\n' "$REMOTE_ROOT" >&2
        exit 66
    }
    validate_runner_syntax
    "$PYTHON" - "$CONFIG_SOURCE" <<'PY'
from pathlib import Path
import json
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for name in ("exchange_root", "state_root", "result_root"):
    assert Path(config[name]).is_absolute()
assert config["tools"]
assert len({tool["profile_id"] for tool in config["tools"]}) == len(config["tools"])
allowed_solver_kinds = {"sprocess", "sdevice", "shell_runner", "deterministic_tool"}
assert all(tool["solver_kind"] in allowed_solver_kinds for tool in config["tools"])
assert all(isinstance(tool["release_evidence"], str) and tool["release_evidence"] for tool in config["tools"])
print("SSH TCAD runner local validation: pass")
PY
}

require_private_config_source() {
    [[ -n "${SCID_REMOTE_RUNNER_CONFIG:-}" ]] || {
        printf '%s\n' \
            'Set SCID_REMOTE_RUNNER_CONFIG to an administrator-edited private runner config.' >&2
        exit 66
    }
    "$PYTHON" - "$CONFIG_SOURCE" "$EXAMPLE_CONFIG_SOURCE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).resolve(strict=True)
example = Path(sys.argv[2]).resolve(strict=True)
if source == example:
    raise SystemExit(
        "refusing to install the tracked runner example as private configuration"
    )
if source.read_bytes() == example.read_bytes():
    raise SystemExit(
        "private runner config is an unedited byte-for-byte copy of the tracked example"
    )
PY
}

validate_code_upgrade() {
    [[ -n "$PYTHON" && -x "$PYTHON" && -f "$RUNNER_SOURCE" ]] || {
        printf 'Runner source or local Python is unavailable.\n' >&2
        exit 66
    }
    validate_runner_syntax
    PYTHONNOUSERSITE=1 \
        PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}/plugins/tcad_artifact" \
        "$PYTHON" - "$TRANSPORT_CONFIG" <<'PY'
import os
import stat
import sys
from pathlib import Path, PurePosixPath

from tcad_artifact.ssh_transport import SSHTCADTransportConfig

source = Path(sys.argv[1]).absolute()
metadata = os.lstat(source)
if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
    raise SystemExit("transport config must be a regular non-symlink file")
if stat.S_IMODE(metadata.st_mode) & 0o022:
    raise SystemExit("transport config must not be group/world writable")
config = SSHTCADTransportConfig.model_validate_json(source.read_bytes(), strict=True)
helper = PurePosixPath(config.remote_helper)
root = helper.parent.parent
if helper != root / "bin" / "scidiscovery-tcad-ssh-runner":
    raise SystemExit("remote_helper is not the canonical runner path")
if PurePosixPath(config.remote_config) != root / "config" / "runner.json":
    raise SystemExit("remote_config must remain under the runner root")
if PurePosixPath(config.remote_exchange_root) != root / "exchange":
    raise SystemExit("remote_exchange_root must remain under the runner root")
print("TCAD runner code-only upgrade validation: pass")
PY
}

load_code_upgrade_binding() {
    local output="$1"
    PYTHONNOUSERSITE=1 \
        PYTHONPATH="${PROJECT_ROOT}/src:${PROJECT_ROOT}/plugins/tcad_artifact" \
        "$PYTHON" - "$TRANSPORT_CONFIG" >"$output" <<'PY'
import sys
from pathlib import Path, PurePosixPath

from tcad_artifact.ssh_transport import SSHTCADTransportConfig, SSHRemoteClient

config = SSHTCADTransportConfig.model_validate_json(
    Path(sys.argv[1]).read_bytes(), strict=True
)
helper = PurePosixPath(config.remote_helper)
root = helper.parent.parent
destination = SSHRemoteClient(config)._destination()
values = (
    config.ssh_executable,
    config.identity_file or "",
    destination,
    str(config.port),
    config.host_key_alias or "",
    config.known_hosts_file or "",
    str(root),
    config.remote_helper,
    config.remote_config,
    str(config.connect_timeout_seconds),
    str(config.operation_timeout_seconds),
)
for value in values:
    sys.stdout.buffer.write(value.encode("utf-8") + b"\0")
PY
}

resolve_destination() {
    local address user
    if [[ -n "$DESTINATION_OVERRIDE" ]]; then
        printf '%s\n' "$DESTINATION_OVERRIDE"
        return
    fi
    if [[ -n "$DESTINATION_FALLBACK" && ( -z "$VMRUN_EXE" || -z "$VMX_PATH" || ! -x "$VMRUN_EXE" ) ]]; then
        printf '%s\n' "$DESTINATION_FALLBACK"
        return
    fi
    if [[ -z "$DESTINATION_FALLBACK" || -z "$VMRUN_EXE" || -z "$VMX_PATH" ]]; then
        printf 'Set SCID_SSH_DESTINATION, or set SCID_SSH_DESTINATION_FALLBACK, SCID_VMRUN_EXE, and SCID_VMX_PATH.\n' >&2
        return 66
    fi
    address="$("$VMRUN_EXE" getGuestIPAddress "$VMX_PATH" -wait)" || {
        printf 'VMware guest address discovery failed.\n' >&2
        return 66
    }
    address="${address//$'\r'/}"
    [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
        printf 'VMware guest address is invalid: %s\n' "$address" >&2
        return 66
    }
    user="${DESTINATION_FALLBACK%@*}"
    printf '%s@%s\n' "$user" "$address"
}

install_remote() {
    local stage remote_command remote_invocation destination
    local -a ssh_args
    destination="$(resolve_destination)"
    stage="$(mktemp -d)"
    trap "rm -rf -- $(printf '%q' "$stage")" EXIT
    install -d "$stage/bin" "$stage/config"
    install -m 0750 "$RUNNER_SOURCE" "$stage/bin/scidiscovery-tcad-ssh-runner"
    "$PYTHON" - "$CONFIG_SOURCE" "$stage/config/runner.json" "$REMOTE_ROOT" <<'PY'
from pathlib import Path
import json
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = sys.argv[3].rstrip("/")
config.update({
    "exchange_root": root + "/exchange",
    "state_root": root + "/state",
    "result_root": root + "/state/runs",
})
Path(sys.argv[2]).write_text(
    json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
    chmod 0640 "$stage/config/runner.json"
    remote_command="umask 027; mkdir -p '$REMOTE_ROOT/bin' '$REMOTE_ROOT/config' '$REMOTE_ROOT/exchange' '$REMOTE_ROOT/state/runs'; tar --warning=no-timestamp --no-same-owner -xf - -C '$REMOTE_ROOT'; chmod 0750 '$REMOTE_ROOT/bin/scidiscovery-tcad-ssh-runner'; chmod 0640 '$REMOTE_ROOT/config/runner.json'; python3 -m py_compile '$REMOTE_ROOT/bin/scidiscovery-tcad-ssh-runner'; printf 'SSH TCAD user runner installation: pass\\n'"
    ssh_args=(
        -o BatchMode=yes
        -o ConnectTimeout=5
        -o StrictHostKeyChecking=yes
        -o "UserKnownHostsFile=$KNOWN_HOSTS"
    )
    [[ -z "$IDENTITY" ]] || ssh_args+=( -i "$IDENTITY" )
    [[ -z "$HOST_KEY_ALIAS" ]] || ssh_args+=( -o "HostKeyAlias=$HOST_KEY_ALIAS" )
    remote_invocation="bash -lc $(quote_posix_shell "$remote_command")"
    tar --format=ustar --mtime=@0 --owner=0 --group=0 --numeric-owner \
        -C "$stage" -cf - bin config | "$SSH_EXE" "${ssh_args[@]}" \
        "$destination" "$remote_invocation"
    rm -rf -- "$stage"
    trap - EXIT
}

install_remote_from_transport() {
    local stage binding_file destination remote_root remote_runner remote_config
    local ssh_executable identity port host_key_alias known_hosts
    local connect_timeout operation_timeout remote_command remote_invocation
    local quoted_root quoted_runner quoted_config quoted_probe probe_request
    local -a binding ssh_args

    stage="$(mktemp -d)"
    binding_file="$(mktemp)"
    trap 'rm -rf -- "$stage"; rm -f -- "$binding_file"' EXIT
    load_code_upgrade_binding "$binding_file"
    mapfile -d '' -t binding <"$binding_file"
    [[ "${#binding[@]}" -eq 11 ]] || {
        printf 'Transport install binding is incomplete.\n' >&2
        return 66
    }
    ssh_executable="${binding[0]}"
    identity="${binding[1]}"
    destination="${binding[2]}"
    port="${binding[3]}"
    host_key_alias="${binding[4]}"
    known_hosts="${binding[5]}"
    remote_root="${binding[6]}"
    remote_runner="${binding[7]}"
    remote_config="${binding[8]}"
    connect_timeout="${binding[9]}"
    operation_timeout="${binding[10]}"

    install -d "$stage/bin" "$stage/config"
    install -m 0750 "$RUNNER_SOURCE" \
        "$stage/bin/scidiscovery-tcad-ssh-runner"
    "$PYTHON" - "$CONFIG_SOURCE" "$stage/config/runner.json" \
        "$remote_root" <<'PY'
from pathlib import Path
import json
import sys

config = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
root = sys.argv[3].rstrip("/")
config.update({
    "exchange_root": root + "/exchange",
    "state_root": root + "/state",
    "result_root": root + "/state/runs",
})
Path(sys.argv[2]).write_text(
    json.dumps(config, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY
    chmod 0640 "$stage/config/runner.json"
    probe_request='{"schema_version":2,"operation":"rpc","payload":{"request":{"jsonrpc":"2.0","id":"install-probe","method":"tools/call","params":{"name":"tcad_capabilities","arguments":{}}}}}'
    printf -v quoted_root '%q' "$remote_root"
    printf -v quoted_runner '%q' "$remote_runner"
    printf -v quoted_config '%q' "$remote_config"
    printf -v quoted_probe '%q' "$probe_request"
    read -r -d '' remote_command <<EOF || true
set -Eeuo pipefail
root=${quoted_root}
runner=${quoted_runner}
config=${quoted_config}
probe_request=${quoted_probe}
mkdir -p "\$root"
upgrade="\$(mktemp -d "\$root/.runner-install.XXXXXX")"
cleanup() { rm -rf -- "\$upgrade"; }
trap cleanup EXIT
tar --warning=no-timestamp --no-same-owner -xf - -C "\$upgrade"
candidate_runner="\$upgrade/bin/scidiscovery-tcad-ssh-runner"
candidate_config="\$upgrade/config/runner.json"
test -f "\$candidate_runner" && test ! -L "\$candidate_runner"
test -f "\$candidate_config" && test ! -L "\$candidate_config"
chmod 0750 "\$candidate_runner"
chmod 0640 "\$candidate_config"
python3 -m py_compile "\$candidate_runner"
probe="\$upgrade/capabilities.json"
printf '%s\n' "\$probe_request" | "\$candidate_runner" --config "\$candidate_config" >"\$probe"
python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); capabilities=value["payload"]["response"]["result"]["structuredContent"]["capabilities"]; assert value.get("ok") is True and capabilities and all(item.get("schema_version") == 2 for item in capabilities)' "\$probe"
had_runner=0
had_config=0
if test -f "\$runner" && test ! -L "\$runner"; then cp -p -- "\$runner" "\$upgrade/previous-runner"; had_runner=1; fi
if test -f "\$config" && test ! -L "\$config"; then cp -p -- "\$config" "\$upgrade/previous-config"; had_config=1; fi
committing=0
restore() {
    status=\$?
    if test "\$committing" = 1; then
        if test "\$had_runner" = 1; then cp -p -- "\$upgrade/previous-runner" "\$runner"; else rm -f -- "\$runner"; fi
        if test "\$had_config" = 1; then cp -p -- "\$upgrade/previous-config" "\$config"; else rm -f -- "\$config"; fi
    fi
    exit "\$status"
}
trap restore ERR INT TERM
mkdir -p "\$root/bin" "\$root/config" "\$root/exchange" "\$root/state/runs"
committing=1
mv -f -- "\$candidate_runner" "\$runner"
mv -f -- "\$candidate_config" "\$config"
chmod 0750 "\$runner"
chmod 0640 "\$config"
printf '%s\n' "\$probe_request" | "\$runner" --config "\$config" >"\$probe"
python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); capabilities=value["payload"]["response"]["result"]["structuredContent"]["capabilities"]; assert value.get("ok") is True and capabilities and all(item.get("schema_version") == 2 for item in capabilities)' "\$probe"
committing=0
trap - ERR INT TERM
runner_sha="\$(sha256sum -- "\$runner" | awk '{print \$1}')"
config_sha="\$(sha256sum -- "\$config" | awk '{print \$1}')"
printf 'SSH TCAD runner transport install: pass (runner_sha256=%s config_sha256=%s)\n' "\$runner_sha" "\$config_sha"
EOF
    remote_invocation="bash -lc $(quote_posix_shell "$remote_command")"
    ssh_args=(
        -o BatchMode=yes
        -o "ConnectTimeout=$connect_timeout"
        -o StrictHostKeyChecking=yes
        -p "$port"
    )
    [[ -z "$identity" ]] || ssh_args+=( -i "$identity" )
    [[ -z "$host_key_alias" ]] || \
        ssh_args+=( -o "HostKeyAlias=$host_key_alias" )
    [[ -z "$known_hosts" ]] || \
        ssh_args+=( -o "UserKnownHostsFile=$known_hosts" )
    tar --format=ustar --mtime=@0 --owner=0 --group=0 --numeric-owner \
        -C "$stage" -cf - bin config | \
        timeout --foreground "$operation_timeout" \
        "$ssh_executable" "${ssh_args[@]}" "$destination" "$remote_invocation"
    rm -rf -- "$stage"
    rm -f -- "$binding_file"
    trap - EXIT
}

upgrade_remote_code() {
    local stage binding_file destination remote_root remote_runner remote_config
    local ssh_executable identity port host_key_alias known_hosts
    local connect_timeout operation_timeout remote_command remote_invocation
    local quoted_root quoted_runner quoted_config quoted_probe
    local probe_request
    local -a binding ssh_args

    stage="$(mktemp -d)"
    binding_file="$(mktemp)"
    trap 'rm -rf -- "$stage"; rm -f -- "$binding_file"' EXIT
    load_code_upgrade_binding "$binding_file"
    mapfile -d '' -t binding <"$binding_file"
    [[ "${#binding[@]}" -eq 11 ]] || {
        printf 'Transport upgrade binding is incomplete.\n' >&2
        return 66
    }
    ssh_executable="${binding[0]}"
    identity="${binding[1]}"
    destination="${binding[2]}"
    port="${binding[3]}"
    host_key_alias="${binding[4]}"
    known_hosts="${binding[5]}"
    remote_root="${binding[6]}"
    remote_runner="${binding[7]}"
    remote_config="${binding[8]}"
    connect_timeout="${binding[9]}"
    operation_timeout="${binding[10]}"

    install -d "$stage/bin"
    install -m 0750 "$RUNNER_SOURCE" \
        "$stage/bin/scidiscovery-tcad-ssh-runner"
    probe_request='{"schema_version":2,"operation":"rpc","payload":{"request":{"jsonrpc":"2.0","id":"upgrade-probe","method":"tools/call","params":{"name":"tcad_capabilities","arguments":{}}}}}'
    printf -v quoted_root '%q' "$remote_root"
    printf -v quoted_runner '%q' "$remote_runner"
    printf -v quoted_config '%q' "$remote_config"
    printf -v quoted_probe '%q' "$probe_request"
    read -r -d '' remote_command <<EOF || true
set -Eeuo pipefail
root=${quoted_root}
runner=${quoted_runner}
config=${quoted_config}
probe_request=${quoted_probe}
test -f "\$config" && test ! -L "\$config"
config_before="\$(sha256sum -- "\$config" | awk '{print \$1}')"
upgrade="\$(mktemp -d "\$root/.runner-upgrade.XXXXXX")"
trap 'rm -rf -- "\$upgrade"' EXIT HUP INT TERM
tar --warning=no-timestamp --no-same-owner -xf - -C "\$upgrade"
candidate="\$upgrade/bin/scidiscovery-tcad-ssh-runner"
test -f "\$candidate" && test ! -L "\$candidate"
chmod 0750 "\$candidate"
python3 -m py_compile "\$candidate"
probe="\$upgrade/capabilities.json"
printf '%s\n' "\$probe_request" | "\$candidate" --config "\$config" >"\$probe"
python3 -c 'import json,sys; value=json.load(open(sys.argv[1])); capabilities=value["payload"]["response"]["result"]["structuredContent"]["capabilities"]; assert value.get("ok") is True and capabilities and all(item.get("schema_version") == 2 for item in capabilities)' "\$probe"
test "\$config_before" = "\$(sha256sum -- "\$config" | awk '{print \$1}')"
mkdir -p "\$root/bin"
mv -f -- "\$candidate" "\$runner"
chmod 0750 "\$runner"
test "\$config_before" = "\$(sha256sum -- "\$config" | awk '{print \$1}')"
runner_sha="\$(sha256sum -- "\$runner" | awk '{print \$1}')"
printf 'SSH TCAD runner code-only upgrade: pass (runner_sha256=%s config_sha256=%s)\n' "\$runner_sha" "\$config_before"
EOF
    remote_invocation="bash -lc $(quote_posix_shell "$remote_command")"
    ssh_args=(
        -o BatchMode=yes
        -o "ConnectTimeout=$connect_timeout"
        -o StrictHostKeyChecking=yes
        -p "$port"
    )
    [[ -z "$identity" ]] || ssh_args+=( -i "$identity" )
    [[ -z "$host_key_alias" ]] || \
        ssh_args+=( -o "HostKeyAlias=$host_key_alias" )
    [[ -z "$known_hosts" ]] || \
        ssh_args+=( -o "UserKnownHostsFile=$known_hosts" )
    tar --format=ustar --mtime=@0 --owner=0 --group=0 --numeric-owner \
        -C "$stage" -cf - bin | \
        timeout --foreground "$operation_timeout" \
        "$ssh_executable" "${ssh_args[@]}" "$destination" "$remote_invocation"
    rm -rf -- "$stage"
    rm -f -- "$binding_file"
    trap - EXIT
}

case "${1:---dry-run}" in
    --dry-run)
        validate_local
        printf '%s\n' \
            'No VM file, service, SSH key, or local service was changed.' \
            "Install target: ${DESTINATION_OVERRIDE:-${DESTINATION_FALLBACK:-<set SCID_SSH_DESTINATION>}}:${REMOTE_ROOT}"
        ;;
    install)
        validate_local
        require_private_config_source
        install_remote
        ;;
    install-from-transport)
        validate_local
        require_private_config_source
        validate_code_upgrade
        if [[ "${2:-}" == "--dry-run" ]]; then
            [[ "$#" -eq 2 ]] || exit 64
            printf '%s\n' \
                'No VM file, remote config, service, SSH key, or local service was changed.' \
                "Full runner install transport: ${TRANSPORT_CONFIG}" \
                "Private runner config: ${CONFIG_SOURCE}"
        else
            [[ "$#" -eq 1 ]] || exit 64
            install_remote_from_transport
        fi
        ;;
    upgrade-code)
        validate_code_upgrade
        if [[ "${2:-}" == "--dry-run" ]]; then
            [[ "$#" -eq 2 ]] || exit 64
            printf '%s\n' \
                'No VM file, remote config, service, SSH key, or local service was changed.' \
                "Code-only upgrade transport: ${TRANSPORT_CONFIG}"
        else
            [[ "$#" -eq 1 ]] || exit 64
            upgrade_remote_code
        fi
        ;;
    *)
        printf 'Usage: %s [--dry-run|install|install-from-transport [--dry-run]|upgrade-code [--dry-run]]\n' "$0" >&2
        exit 64
        ;;
esac
