"""One local Web host, independent of scientific controller lifetimes.

The lock is inherited through startup, while Linux boot/start identities prevent
stale records from targeting reused PIDs. This is a same-user soft boundary.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import secrets
import select
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import traceback


START_TIMEOUT = 20.0
STOP_TIMEOUT = 5.0


def _directory(value, variable, suffix):
    return Path(value).expanduser().resolve() if value is not None else (
        Path(os.environ.get(variable) or Path.home() / ('.local/state' if variable == 'XDG_STATE_HOME'
            else '.local/share')) / 'scidiscovery' / suffix).expanduser().resolve()


def state_directory(value=None):
    return _directory(value, 'XDG_STATE_HOME', 'web')


def _identity(pid):
    try:
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] in {'Z', 'X'}:
            return None
        return dict(pid=int(pid), boot_id=boot, start_ticks=int(fields[19]))
    except (OSError, ValueError, IndexError):
        return None


def _same_process(record):
    identity = _identity(record.get('pid', 0))
    return identity is not None and all(record.get(key) == value for key, value in identity.items())


def _open(path, flags):
    return os.open(path, flags | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)


def _read(directory):
    try:
        fd = _open(directory / 'service.json', os.O_RDONLY)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            value = json.loads(os.read(fd, 65536))
        finally:
            os.close(fd)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _write(directory, record):
    fd, name = tempfile.mkstemp(prefix='.service-', dir=directory)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, directory / 'service.json')
    finally:
        Path(name).unlink(missing_ok=True)


def _locked(directory):
    try:
        fd = _open(directory / 'service.lock', os.O_RDWR)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def status(*, state_dir=None):
    """Inspect existing state without creating directories, locks or credentials."""
    directory = state_directory(state_dir)
    record = _read(directory)
    if (record and record.get('status') == 'running' and isinstance(record.get('url'), str)
            and _same_process(record) and _locked(directory)):
        return record
    return dict(status='stopped')


def _event(directory, event, error=None):
    fd = _open(directory / 'service.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    try:
        os.fchmod(fd, 0o600)
        # HTTP request logging stays disabled in instance_ui. Startup failures
        # retain their original stack in this private operator diagnostic.
        os.write(fd, (json.dumps(dict(event=event, pid=os.getpid(), time=time.time(),
            **({'error_type': type(error).__name__} if error else {}))) + '\n').encode())
        if error is not None:
            os.write(fd, ''.join(traceback.format_exception(type(error), error, error.__traceback__)).encode())
    finally:
        os.close(fd)


def _signal(record, number):
    """Pin a process where supported; never signal a process group."""
    descriptor = None
    try:
        if hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'):
            try:
                descriptor = os.pidfd_open(record['pid'])
            except ProcessLookupError:
                return
            except OSError:
                pass  # Kernel or host policy can lack pidfds despite Python support.
        if not _same_process(record):
            return
        if descriptor is not None:
            signal.pidfd_send_signal(descriptor, number)
        else:
            os.kill(record['pid'], number)
    except ProcessLookupError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _terminate(record):
    _signal(record, signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while _same_process(record) and time.monotonic() < deadline:
        time.sleep(.05)
    if _same_process(record):
        _signal(record, signal.SIGKILL)
        deadline = time.monotonic() + 2
        while _same_process(record) and time.monotonic() < deadline:
            time.sleep(.05)
    if _same_process(record):
        raise RuntimeError('Web service did not stop within the timeout')


def stop(*, state_dir=None):
    directory = state_directory(state_dir)
    deadline = time.monotonic() + START_TIMEOUT
    while True:
        record = status(state_dir=directory)
        if record['status'] == 'running':
            _terminate(record)
            return dict(status='stopped')
        if not _locked(directory):
            return dict(status='stopped')
        if time.monotonic() >= deadline:
            raise RuntimeError('Web service startup is unresolved; stop could not confirm shutdown')
        time.sleep(.05)


def _run(directory, workspace, port, ready_fd=None):
    stopped = threading.Event()
    old_signals = {number: signal.signal(number, lambda *_: stopped.set())
        for number in (signal.SIGTERM, signal.SIGINT)}
    owner = _identity(os.getpid())
    if owner is None:
        raise RuntimeError('Web service requires Linux process identity support')
    published = False

    def ready(endpoint):
        nonlocal published, ready_fd
        record = dict(owner, status='running', workspace=str(workspace),
            url=endpoint['management_url'])
        _write(directory, record)
        _event(directory, 'listening')
        published = True
        if ready_fd is None:
            print(record['url'], flush=True)
        else:
            os.write(ready_fd, (json.dumps(record) + '\n').encode())
            os.close(ready_fd)
            ready_fd = None

    try:
        from . import instance_ui
        instance_ui.serve(workspace, secret_file=directory / 'secret.key', port=port,
            workspace=True, multi_workspace=True, approval_host=False,
            on_ready=ready, stop_event=stopped)
    except BaseException as error:
        _event(directory, 'failed', error)
        message = dict(error='Web host failed to start (' + type(error).__name__
            + '); diagnostic: ' + str(directory / 'service.log'))
        if ready_fd is not None:
            os.write(ready_fd, (json.dumps(message) + '\n').encode())
        raise RuntimeError(message['error']) from error
    finally:
        if ready_fd is not None:
            os.close(ready_fd)
        current = _read(directory)
        if published and current and all(current.get(key) == value for key, value in owner.items()):
            _write(directory, dict(owner, status='stopped'))
        _event(directory, 'stopped')
        for number, handler in old_signals.items():
            signal.signal(number, handler)
    return dict(status='stopped')


def start(*, workspace=None, port=None, state_dir=None, foreground=False):
    directory = state_directory(state_dir)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_fd = _open(directory / 'service.lock', os.O_RDWR | os.O_CREAT)
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                current = status(state_dir=directory)
                if current['status'] == 'running':
                    return current
                if time.monotonic() >= deadline:
                    raise RuntimeError('Web service startup is still in progress; retry status')
                time.sleep(.05)
        selected = _directory(workspace, 'XDG_DATA_HOME', 'workspace')
        selected.mkdir(parents=True, exist_ok=True)
        secret_fd = _open(directory / 'secret.key', os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            os.fchmod(secret_fd, 0o600)
            os.write(secret_fd, secrets.token_hex(32).encode())
        finally:
            os.close(secret_fd)
        if foreground:
            return _run(directory, selected, port or 0)
        reader, writer = os.pipe()
        child = None
        identity = None
        try:
            child = subprocess.Popen([sys.executable, '-m', __name__, '--child',
                '--workspace', str(selected), '--state-dir', str(directory),
                '--port', str(port or 0), '--lock-fd', str(lock_fd), '--ready-fd', str(writer)],
                pass_fds=(lock_fd, writer), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            os.close(writer)
            writer = None
            identity = _identity(child.pid)
            pending = b''
            deadline = time.monotonic() + START_TIMEOUT
            while b'\n' not in pending and time.monotonic() < deadline:
                if select.select([reader], [], [], max(0, deadline - time.monotonic()))[0]:
                    block = os.read(reader, 65536)
                    if not block:
                        break
                    pending += block
            message = json.loads(pending) if pending else {}
            if message.get('status') != 'running' or not _same_process(message):
                raise RuntimeError(message.get('error', 'Web service did not become ready within the timeout'))
            return message
        except BaseException:
            if child is not None:
                # This exact child belongs to this start, even before publication.
                if identity is not None:
                    _terminate(identity)
                child.wait(timeout=2)
            raise
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)
    finally:
        os.close(lock_fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--child', action='store_true', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--lock-fd', type=int, required=True)
    parser.add_argument('--ready-fd', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        _run(Path(args.state_dir), Path(args.workspace), args.port, args.ready_fd)
    finally:
        os.close(args.lock_fd)


if __name__ == '__main__':
    main()
