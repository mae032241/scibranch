"""Finite Linux, standard-library-only execution receipt writer.

This file is copied into the original task container.  It imports no framework
code and has no listener, callback, package installation, or task-container
ownership.  Status reads never acquire the launch lock or signal a process.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import uuid


RECORD_BYTES = 128 * 1024
MAX_PROCESSES = 32768
POLL_SECONDS = .04
CLEANUP_SECONDS = 3.0
CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

# The R-only original image needs no Python bootstrap. This small native Bash
# transport owns the original timeout process group, not arbitrary detached
# same-user processes. The task container remains a trusted soft boundary.
SHELL_RUNNER = r'''#!/bin/bash
set -u
operation=$1
root=$2
control=$root/control
[[ -f "$control/request.sh" ]] || exit 3
source "$control/request.sh"
log_truncated=false

emit() {
  local state=$1 reason=$2 code=$3 cleaned=$4 started=${5:-null} finished=${6:-null}
  printf '{"call_id":"%s","state":"%s","reason":"%s","returncode":%s,"cleanup_confirmed":%s,"started_at":%s,"finished_at":%s,"deadline_epoch":%s,"native_identity":{"container_id":"%s"},"log_truncated":%s}\n' \
    "$call_id" "$state" "$reason" "$code" "$cleaned" "$started" "$finished" "$deadline_epoch" "$container_id" "$log_truncated"
}
status() {
  if [[ -f "$control/terminal.json" ]]; then cat "$control/terminal.json"; return; fi
  if [[ -f "$control/unresolved.json" ]]; then cat "$control/unresolved.json"; return; fi
  if [[ -f "$control/supervisor.pid" ]]; then
    local pid; read -r pid < "$control/supervisor.pid"
    if kill -0 "$pid" 2>/dev/null; then
      if [[ -f "$control/cancel" ]]; then emit cancelling cancelled null false
      else emit running running null false; fi
      return
    fi
  fi
  emit unknown original_supervisor_missing null false
}
case "$operation" in
  status) status ;;
  cancel)
    if [[ ! -f "$control/terminal.json" && ! -f "$control/unresolved.json" ]]; then
      : > "$control/cancel"
    fi
    status ;;
  launch)
    # A durable one-time claim survives any uncertain Docker reply. Neither
    # status nor an existing claim can start another scientific process.
    if mkdir "$control/launched" 2>/dev/null; then
      setsid /bin/bash "$0" supervise "$root" </dev/null >/dev/null 2>&1 &
    fi
    for ((i=0; i<40; i++)); do
      [[ -f "$control/supervisor.pid" || -f "$control/terminal.json" || -f "$control/unresolved.json" ]] && break
      sleep .05
    done
    status ;;
  supervise)
    printf '%s\n' "$$" > "$control/supervisor.pid.part"
    mv "$control/supervisor.pid.part" "$control/supervisor.pid"
    started=$(date +%s)
    reason=exited
    code=null
    clean=true
    remaining=$((deadline_seconds - started))
    : > "$control/stdout.raw"
    : > "$control/stderr.raw"
    if ((remaining <= 0)); then
      reason=timeout
      code=124
    elif [[ -f "$control/cancel" ]]; then
      reason=cancelled
    else
      mkfifo "$control/stdout.pipe" "$control/stderr.pipe" || exit 2
      cat < "$control/stdout.pipe" > "$control/stdout.raw" & out_reader=$!
      cat < "$control/stderr.pipe" > "$control/stderr.raw" & err_reader=$!
      cd "$root/work" || exit 2
      # GNU timeout retains the finite deadline even if this supervisor dies.
      # setsid gives this original command its own process group.
      setsid timeout --signal=TERM --kill-after=1s "${remaining}s" "${science_argv[@]}" \
        > "$control/stdout.pipe" 2> "$control/stderr.pipe" & science=$!
      while kill -0 "$science" 2>/dev/null; do
        if [[ -f "$control/cancel" ]]; then
          reason=cancelled
          kill -TERM -- "-$science" 2>/dev/null || true
          break
        fi
        if (( $(date +%s) >= deadline_seconds )); then reason=timeout; break; fi
        sleep .05
      done
      # Finish/stop only the original timeout group. No container inventory or
      # task filesystem discovery is performed.
      kill -TERM -- "-$science" 2>/dev/null || true
      sleep .2
      kill -KILL -- "-$science" 2>/dev/null || true
      until=$(( $(date +%s) + 3 ))
      while kill -0 "$science" 2>/dev/null; do
        if (( $(date +%s) >= until )); then clean=false; break; fi
        sleep .05
      done
      if [[ "$clean" == true ]]; then wait "$science"; code=$?; fi
      if [[ "$reason" == exited && ( "$code" == 124 || "$code" == 137 ) ]]; then reason=timeout; fi
      until=$(( $(date +%s) + 3 ))
      while kill -0 "$out_reader" 2>/dev/null || kill -0 "$err_reader" 2>/dev/null; do
        if (( $(date +%s) >= until )); then clean=false; break; fi
        sleep .05
      done
      if kill -0 -- "-$science" 2>/dev/null; then clean=false; fi
      if [[ "$clean" == true ]]; then
        wait "$out_reader"; wait "$err_reader"
      else
        kill -KILL "$out_reader" "$err_reader" 2>/dev/null || true
      fi
      rm -f "$control/stdout.pipe" "$control/stderr.pipe"
    fi
    state=failed
    [[ "$reason" == exited && "$code" == 0 ]] && state=succeeded
    [[ "$reason" == cancelled ]] && state=cancelled
    record=terminal.json
    if [[ "$clean" != true ]]; then
      state=unknown; reason=cleanup_unconfirmed; code=null; record=unresolved.json
    fi
    emit "$state" "$reason" "$code" "$clean" "$started" "$(date +%s)" > "$control/$record.part"
    mv "$control/$record.part" "$control/$record"
    ;;
  read-file)
    relative=$3
    [[ "$relative" != /* && "$relative" != *\\* ]] || exit 5
    IFS=/ read -ra parts <<< "$relative"
    path=$root
    for part in "${parts[@]}"; do
      [[ -n "$part" && "$part" != . && "$part" != .. ]] || exit 5
      path=$path/$part
      [[ ! -L "$path" ]] || exit 5
    done
    [[ -e "$path" ]] || exit 3
    [[ -f "$path" ]] || exit 5
    before=$(stat -c '%d:%i:%s:%y:%z' "$path") || exit 2
    size=$(stat -c %s "$path") || exit 2
    printf '%s\n' "$size"
    cat "$path" || exit 2
    after=$(stat -c '%d:%i:%s:%y:%z' "$path") || exit 2
    [[ "$before" == "$after" ]] || exit 4
    ;;
  *) exit 2 ;;
esac
'''


def atomic_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("record is not a regular file")
        raw = stream.read(RECORD_BYTES + 1)
    if len(raw) > RECORD_BYTES:
        raise ValueError("record exceeds its byte bound")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("record must be an object")
    return value


def relative_path(value):
    path = PurePosixPath(value)
    if not isinstance(value, str) or not value or "\x00" in value or path.is_absolute() or any(
        part in ("", ".", "..") for part in value.split("/")
    ):
        raise ValueError("path must be an explicit relative file")
    return path


def open_regular(root, relative):
    """Open one named file, refusing symlinks in every relative component."""
    parts = relative_path(relative).parts
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            next_directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError("requested output is not a regular file")
        return fd
    finally:
        os.close(directory)


def validate_request(request):
    if not CALL_ID.fullmatch(str(request.get("call_id", ""))):
        raise ValueError("invalid original call identity")
    relative_path(request.get("entry", ""))
    if not isinstance(request.get("arguments"), list) or not all(
        isinstance(value, str) and "\x00" not in value for value in request["arguments"]
    ):
        raise ValueError("invalid scientific arguments")
    python = request.get("python_argv")
    if not isinstance(python, list) or not python or not all(
        isinstance(value, str) and value and "\x00" not in value for value in python
    ) or not os.path.isabs(python[0]):
        raise ValueError("explicit original interpreter is required")
    deadline = request.get("deadline_epoch")
    if type(deadline) not in (float, int) or not math.isfinite(deadline):
        raise ValueError("finite original deadline is required")


def prepare(call_root, request):
    """Claim transfer once. An incomplete existing claim is never restarted."""
    validate_request(request)
    call_root = Path(call_root)
    try:
        call_root.mkdir(mode=0o700)
    except FileExistsError:
        return False
    for name in ("code", "work", "control"):
        (call_root / name).mkdir(mode=0o700)
    atomic_json(call_root / "control/request.json", request)
    for directory in (call_root, call_root.parent):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return True


def process_identity(pid):
    fields = Path("/proc/%s/stat" % pid).read_text().rsplit(")", 1)[1].split()
    return dict(pid=int(pid), start_ticks=int(fields[19]), pgid=int(fields[2]),
                ppid=int(fields[1]), state=fields[0],
                boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def process_matches(expected):
    try:
        current = process_identity(expected["pid"])
        return all(current[key] == expected[key] for key in ("start_ticks", "boot_id")) and current["state"] != "Z"
    except (OSError, ValueError, KeyError, IndexError):
        return False


def status(call_root):
    """Only the original records and recorded supervisor PID are observed."""
    control = Path(call_root) / "control"
    request = read_json(control / "request.json")
    base = dict(call_id=request["call_id"], state="unknown", reason="native_identity_missing",
                returncode=None, started_at=None, finished_at=None,
                deadline_epoch=request["deadline_epoch"], cleanup_confirmed=False,
                native_identity={"container_id": request.get("container_id")})
    try:
        terminal = read_json(control / "terminal.json")
    except FileNotFoundError:
        terminal = None
    if terminal is not None:
        if terminal.get("call_id") != request["call_id"]:
            raise ValueError("terminal original identity differs")
        return terminal
    try:
        unresolved = read_json(control / "unresolved.json")
    except FileNotFoundError:
        unresolved = None
    if unresolved is not None:
        if unresolved.get("call_id") != request["call_id"]:
            raise ValueError("unresolved original identity differs")
        return unresolved
    try:
        identity = read_json(control / "identity.json")
    except FileNotFoundError:
        return base
    if identity.get("call_id") != request["call_id"]:
        raise ValueError("native original identity differs")
    base.update(native_identity=identity, started_at=identity.get("started_at"))
    if process_matches(identity.get("supervisor", {})):
        base.update(state="cancelling" if (control / "cancel.json").exists() else "running", reason=None)
    else:
        base["reason"] = "original_supervisor_missing"
    return base


def launch(call_root):
    control = Path(call_root) / "control"
    request = read_json(control / "request.json")
    validate_request(request)
    fd = os.open(control / "launch.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        # A query never takes this lock. Only exact-call launchers serialize.
        fcntl.flock(fd, fcntl.LOCK_EX)
        if not (control / "launch.json").exists():
            atomic_json(control / "launch.json", dict(call_id=request["call_id"], claimed_at=time.time()))
            subprocess.Popen([sys.executable, "-B", str(Path(__file__).absolute()),
                              "_supervise", "--call-root", str(call_root)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
        # Native startup is bounded and does not hold a host Docker client open
        # for the scientific lifetime. An uncertain claim is never recreated.
        until = time.monotonic() + 2.0
        while time.monotonic() < until:
            state = status(call_root)
            if state["state"] != "unknown":
                return state
            time.sleep(.02)
        return status(call_root)
    finally:
        os.close(fd)


def cancel(call_root):
    control = Path(call_root) / "control"
    state = status(call_root)
    if state["state"] in ("succeeded", "failed", "cancelled", "unknown"):
        return state
    # Request cancellation of the same finite supervisor. No stale PID or
    # numeric group is signalled by a query/client process.
    if not (control / "cancel.json").exists():
        atomic_json(control / "cancel.json", dict(call_id=state["call_id"], requested_at=time.time()))
    return status(call_root)


def _subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER.
        raise OSError(ctypes.get_errno(), "native descendant ownership unavailable")


def _owned_descendants():
    # Only finite supervisor native-process metadata is enumerated, never the
    # task filesystem. Status does not perform this snapshot.
    snapshot = {}
    with os.scandir("/proc") as entries:
        for entry in entries:
            if not entry.name.isdecimal():
                continue
            if len(snapshot) >= MAX_PROCESSES:
                raise RuntimeError("native process snapshot exceeds its count bound")
            try:
                item = process_identity(int(entry.name))
                snapshot[item["pid"]] = item
            except (OSError, ValueError, IndexError):
                continue
    owned = {os.getpid()}
    while True:
        children = {pid for pid, item in snapshot.items() if item["ppid"] in owned}
        expanded = owned | children
        if expanded == owned:
            break
        owned = expanded
    return [snapshot[pid] for pid in owned if pid != os.getpid() and snapshot[pid]["state"] != "Z"]


def _signal_owned(items, sig):
    for item in items:
        if process_matches(item):
            try:
                os.kill(item["pid"], sig)
            except ProcessLookupError:
                pass


def _reap_adopted():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if not pid:
            return


def _execute(call_root):
    control = Path(call_root) / "control"
    request = read_json(control / "request.json")
    deadline_monotonic = time.monotonic() + max(0., request["deadline_epoch"] - time.time())
    # No authored instruction executes before its exact PID/start identity is
    # durable and the supervisor releases this bounded gate.
    while not (control / "execute.json").exists():
        if time.time() >= request["deadline_epoch"] or time.monotonic() >= deadline_monotonic:
            return 124
        time.sleep(.01)
    gate = read_json(control / "execute.json")
    if gate.get("call_id") != request["call_id"]:
        raise ValueError("execution gate original identity differs")
    entry = str(Path(call_root) / "code" / relative_path(request["entry"]))
    fd = open_regular(Path(call_root) / "code", request["entry"])
    os.close(fd)
    os.chdir(Path(call_root) / "work")
    argv = request["python_argv"] + [entry] + request["arguments"]
    os.execv(argv[0], argv)


def supervise(call_root):
    control = Path(call_root) / "control"
    request = read_json(control / "request.json")
    validate_request(request)
    started = time.time()
    deadline_monotonic = time.monotonic() + max(0., request["deadline_epoch"] - started)
    identity = dict(call_id=request["call_id"], container_id=request.get("container_id"),
                    started_at=started, supervisor=process_identity(os.getpid()))
    child = None
    selector = selectors.DefaultSelector()
    logs = {}
    sizes = {"stdout": 0, "stderr": 0}
    truncated = {"stdout": False, "stderr": False}
    reason, cleanup_confirmed, returncode = "runner_error", False, None
    cleanup_started = None
    try:
        _subreaper()
        atomic_json(control / "identity.json", identity)
        for name in sizes:
            fd = os.open(control / (name + ".raw"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            logs[name] = os.fdopen(fd, "wb")
        if time.time() >= request["deadline_epoch"]:
            reason, cleanup_confirmed = "timeout", True
        else:
            child = subprocess.Popen([sys.executable, "-B", str(Path(__file__).absolute()),
                                      "_execute", "--call-root", str(call_root)],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     cwd=Path(call_root) / "work", start_new_session=True, close_fds=True)
            identity["science"] = process_identity(child.pid)
            atomic_json(control / "identity.json", identity)
            atomic_json(control / "execute.json", dict(call_id=request["call_id"]))
            for name in sizes:
                stream = getattr(child, name)
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            reason = "exited"
            while True:
                now = time.time()
                if (control / "cancel.json").exists() and reason == "exited":
                    reason = "cancelled"
                if (now >= request["deadline_epoch"] or time.monotonic() >= deadline_monotonic) and reason == "exited":
                    reason = "timeout"
                returncode = child.poll()
                stopping = reason != "exited" or returncode is not None
                if stopping:
                    if cleanup_started is None:
                        cleanup_started = time.monotonic()
                    members = _owned_descendants()
                    _signal_owned(members, signal.SIGTERM if time.monotonic() - cleanup_started < .3 else signal.SIGKILL)
                    if returncode is not None:
                        _reap_adopted()
                    if not members and returncode is not None:
                        cleanup_confirmed = True
                    if time.monotonic() - cleanup_started >= CLEANUP_SECONDS:
                        # An unexplained live pipe is not stopping evidence,
                        # even when a process snapshot has become empty.
                        if selector.get_map():
                            cleanup_confirmed = False
                        break
                for key, _ in selector.select(POLL_SECONDS):
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    name = key.data
                    logs[name].write(raw)
                    sizes[name] += len(raw)
                if cleanup_confirmed and not selector.get_map():
                    break
    except BaseException:
        # Retain unknown cleanup on an internal failure. The exact Harbor
        # owner can quarantine/stop the original container; never fake exit 0.
        if child is None:
            cleanup_confirmed = True
        else:
            try:
                until = time.monotonic() + CLEANUP_SECONDS
                while time.monotonic() < until:
                    members = _owned_descendants()
                    _signal_owned(members, signal.SIGKILL)
                    returncode = child.poll()
                    if returncode is not None:
                        _reap_adopted()
                    if not members and returncode is not None:
                        cleanup_confirmed = True
                        break
                    time.sleep(POLL_SECONDS)
            except (OSError, RuntimeError):
                pass
        reason = "runner_error"
    finally:
        selector.close()
        for name in ("stdout", "stderr"):
            stream = getattr(child, name, None)
            if stream is not None:
                stream.close()
        for stream in logs.values():
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
    state = "unknown" if not cleanup_confirmed else "cancelled" if reason == "cancelled" else (
        "succeeded" if reason == "exited" and returncode == 0 else "failed")
    terminal = dict(call_id=request["call_id"], state=state,
                    reason=reason if cleanup_confirmed else "cleanup_unconfirmed",
                    returncode=returncode if cleanup_confirmed else None,
                    started_at=started, finished_at=time.time() if cleanup_confirmed else None,
                    deadline_epoch=request["deadline_epoch"], cleanup_confirmed=cleanup_confirmed,
                    native_identity=identity, log_bytes=sizes, log_truncated=truncated)
    atomic_json(control / ("terminal.json" if cleanup_confirmed else "unresolved.json"), terminal)
    return 0


def stream_file(call_root, relative, max_bytes=None):
    """Stream the exact opened original; legacy max_bytes is not a quota."""
    fd = open_regular(call_root, relative)
    with os.fdopen(fd, "rb") as source:
        before = os.fstat(source.fileno())
        sys.stdout.buffer.write(str(before.st_size).encode("ascii") + b"\n")
        remaining = before.st_size
        while remaining:
            block = source.read(min(65536, remaining))
            if not block:
                return 4
            sys.stdout.buffer.write(block)
            remaining -= len(block)
        if source.read(1):
            return 4
        after = os.fstat(source.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if any(getattr(before, field) != getattr(after, field) for field in fields):
            return 4
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("launch", "status", "cancel", "read-file", "_supervise", "_execute"))
    parser.add_argument("--call-root", required=True)
    parser.add_argument("--relative")
    parser.add_argument("--max-bytes", type=int)
    args = parser.parse_args(argv)
    try:
        if args.operation == "_supervise":
            return supervise(args.call_root)
        if args.operation == "_execute":
            return _execute(args.call_root)
        if args.operation == "read-file":
            try:
                return stream_file(args.call_root, args.relative, args.max_bytes)
            except ValueError:
                return 5
            except OSError as error:
                if error.errno in (errno.ELOOP, errno.ENOTDIR):
                    return 5
                raise
        value = {"launch": launch, "status": status, "cancel": cancel}[args.operation](args.call_root)
        print(json.dumps(value, allow_nan=False, sort_keys=True))
        return 0
    except FileNotFoundError:
        return 3
    except Exception as error:
        # Private transport diagnostics only; public adapter redacts them.
        print("native runner rejected operation: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
