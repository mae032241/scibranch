"""Durable local execution transport; scientific routing stays in controller.

Linux process groups cover ordinary children. Launchers must retain their own
isolation and cleanup guarantees for descendants which leave that group.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from . import budget, contracts, execution


def process_identity(pid, task_ref):
    # comm may contain spaces and parentheses; fields follow its final ')'.
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                start_ticks=int(fields[19]), pgid=int(fields[2]), sid=int(fields[3]),
                ppid=int(fields[1]), state=fields[0],
                task_ref=task_ref)


def process_matches(identity, task_ref):
    try:
        current = process_identity(identity['pid'], task_ref)
        return (current['state'] != 'Z' and identity['task_ref'] == task_ref
                and all(current[key] == identity[key]
                        for key in ('pid', 'boot_id', 'start_ticks', 'pgid', 'sid')))
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return False


def _subreaper():
    # Linux-only runtime: orphaned ordinary descendants remain owned by this
    # runner, so their PIDs cannot be recycled before we signal and reap them.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'Cannot establish native descendant ownership')


def cleanup_owned_group(child):
    """Clean surviving children by actual ownership, never a stale PGID."""
    deadline = time.monotonic() + 3
    while True:
        members = _observed_group_members(child)
        if not members:
            return 'observed_group_cleaned'
        for member in members:
            if member['ppid'] == os.getpid() and process_matches(member, child['task_ref']):
                try:
                    os.kill(member['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        # Killed direct children may themselves have ordinary children which
        # now become owned by this subreaper. Observe again before returning.
        if time.monotonic() >= deadline:
            return 'cleanup_unknown'
        time.sleep(.02)


def _observed_group_members(child):
    """Observe the existing group contract without granting signal authority."""
    members=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:
            member=process_identity(int(entry.name),child['task_ref'])
            if (member['pgid']==child['pgid'] and member['sid']==child['sid']
                    and member['boot_id']==child['boot_id']
                    and member['start_ticks']>=child['start_ticks'] and member['state']!='Z'):
                members.append(member)
        except (OSError,ValueError,IndexError):pass
    return members


def _bound_group_identity(identity, task_ref):
    return not (not isinstance(identity,dict) or identity.get('task_ref') != task_ref
            or not isinstance(identity.get('boot_id'),str) or not identity['boot_id']
            or any(type(identity.get(key)) is not int or identity[key] <= 0
                for key in ('pid','start_ticks','pgid','sid','ppid'))
            or identity['pid'] != identity['pgid'] or identity['pid'] != identity['sid'])


def bound_group_stopped(identity, task_ref):
    """Read-only positive absence of one exact owner and its original group."""
    return (_bound_group_identity(identity,task_ref)
        and not process_matches(identity,task_ref) and not _observed_group_members(identity))


def stop_bound_group(identity, task_ref):
    """Explicitly stop one exact detached owner, retaining uncertainty.

    Like the watchdog, capture identities while the original leader is live.
    Never signal a numeric PGID after that leader disappears or is reused.
    """
    if not _bound_group_identity(identity,task_ref):
        return 'cleanup_unknown'
    members=_observed_group_members(identity)
    if not process_matches(identity,task_ref):
        return 'observed_group_cleaned' if not members else 'cleanup_unknown'
    if not members or not process_matches(identity,task_ref):return 'cleanup_unknown'
    for member in members:
        if process_matches(member,task_ref):
            try:os.kill(member['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    until=time.monotonic()+.5
    while any(process_matches(member,task_ref) for member in members) and time.monotonic()<until:
        time.sleep(.02)
    for member in members:
        if process_matches(member,task_ref):
            try:os.kill(member['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    return cleanup_owned_group(identity)


def signal_observed_group(child):
    """Watchdog fallback: signal exact members while their original leader is live."""
    key=child['task_ref']
    if not process_matches(child,key):return 'cleanup_unknown'
    members=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():continue
        try:
            member=process_identity(int(entry.name),key)
            if (member['pgid']==child['pgid'] and member['sid']==child['sid']
                    and member['boot_id']==child['boot_id'] and member['start_ticks']>=child['start_ticks']):
                members.append(member)
        except (OSError,ValueError,IndexError):pass
    if not process_matches(child,key):return 'cleanup_unknown'
    for member in members:
        if process_matches(member,key):
            try:os.kill(member['pid'],signal.SIGKILL)
            except ProcessLookupError:pass
    return 'observed_group_signalled'  # Signalling alone never asserts cleanup or execution outcome.


def node_state(root, step):
    """Read original facts, retaining a reboot interruption as the same work."""
    record = execution.record(root, step)
    if (execution.origin(root, step) / 'result-identity.json').exists():
        return 'sealed'
    if (record / 'native-outcome.json').exists():
        from . import managed_wait
        if (record/managed_wait.NAME).exists():
            managed_wait.validate(root,contracts.read(root/'jobs'/f'{step:03d}.json'),record)
            return 'waiting'
        return 'terminal'
    if not (record / 'native-intent.json').exists():
        return 'unstarted'
    try:
        contracts.settings(root)
        binding = execution.identity(root, step)
        intent = contracts.read(record / 'native-intent.json')
        if intent['task_ref'] != contracts.record_ref(root / 'jobs' / f'{step:03d}.json'):
            return 'unknown'
        if intent.get('continuation_kind') in {'reentry','automatic_recovery'} and not any(
                (record / name).exists() for name in ('runner-process.json','native-process.json')):
            return 'unstarted'  # Reuse this accepted identity and its exact recovery scope.
        runner = contracts.read(record / 'runner-process.json')
        if process_matches(runner, binding):
            return 'running'
        child = record / 'native-process.json'
        if child.exists() and process_matches(contracts.read(child), binding):
            return 'running'
        if interrupted(record, binding):
            return 'interrupted'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return 'unknown'


def interrupted(record, binding):
    """A reboot ends these local writers; it says nothing about external calls."""
    if (record / 'native-outcome.json').exists():
        return False
    current_boot = budget.clock_stamp()['boot_id']
    try:
        receipts = [contracts.read(contracts.regular(record / name))
                    for name in ('runner-process.json', 'native-process.json')]
        return all(receipt.get('task_ref') == binding
                   and isinstance(receipt.get('boot_id'), str) and receipt['boot_id']
                   and receipt['boot_id'] != current_boot
                   and all(type(receipt.get(key)) is int and receipt[key] > 0
                           for key in ('pid', 'start_ticks', 'pgid', 'sid', 'ppid'))
                   for receipt in receipts) and receipts[0]['boot_id'] == receipts[1]['boot_id']
    except (OSError, ValueError, KeyError, TypeError):
        return False


from .instance_owner import effect as owner_effect


@owner_effect
def _spawn(root, step, mode, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    record = execution.record(root, step, attempt)
    bootstrap = ('import runpy,sys;sys.path.insert(0,sys.argv.pop(1));'
                 'runpy.run_module("scidiscovery.research_loop.runtime",run_name="__main__")')
    with (record / f'{mode}.stderr').open('ab') as err:
        return subprocess.Popen([sys.executable, '-c', bootstrap,
            str(Path(__file__).resolve().parents[2]), mode, str(root), str(step), str(attempt)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=err,
            start_new_session=True, close_fds=True)


def start(root, step, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    return _spawn(root, step, 'runner', attempt)


@owner_effect
def _watch(root, step, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    record = execution.record(root, step, attempt)
    binding = execution.identity(root, step, attempt)
    runner = contracts.read(record / 'runner-process.json')
    contracts.write(record / 'watchdog-process.json', process_identity(os.getpid(), binding))
    while process_matches(runner, binding):
        if (record / 'native-outcome.json').exists():
            return
        time.sleep(.1)
    if (record / 'native-outcome.json').exists():
        return
    cleanup = 'cleanup_unknown'
    try:
        child = contracts.read(record / 'native-process.json')
        if process_matches(child, binding) and child['pgid'] == child['pid']:
            # Capture exact current members before TERM. If the leader exits,
            # never send KILL to its now unverifiable/reusable numeric PGID.
            members = []
            for entry in Path('/proc').iterdir():
                if entry.name.isdecimal():
                    try:
                        member = process_identity(int(entry.name), binding)
                        if member['pgid'] == child['pgid']:
                            members.append(member)
                    except (OSError, ValueError, IndexError):
                        pass
            if process_matches(child, binding):
                os.killpg(child['pgid'], signal.SIGTERM)
                time.sleep(.5)
                for member in members:
                    if process_matches(member, binding):
                        os.kill(member['pid'], signal.SIGKILL)
                cleanup = 'observed_group_signalled'
    except (OSError, ValueError, KeyError, TypeError):
        pass
    managed=None
    try:
        job=contracts.read(root/'jobs'/f'{step:03d}.json')
        if job.get('experiment_mode')=='local' and job['role']=='W':
            from . import local_experiments
            managed=local_experiments.cleanup(root,job,watchdog=True)
    except (OSError,ValueError,KeyError,TypeError):managed='cleanup_unknown'
    contracts.write(record / 'runner-cleanup.json', dict(status=cleanup,
        **(dict(managed_experiments=managed) if managed is not None else {}),
        limitation='Only exactly observed original process-group members; detached descendants are not covered.'))


@contextmanager
def _runner_lease(record):
    fd = os.open(record / 'runner.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _complete_clock(root):
    # Hold the same host writer lock through the append. An unlocked append
    # after probing the lock could stop a newly accepted controller interval.
    from .controller import _writer
    try:
        with _writer(root):
            budget.inactive(root, 'runner_completion_without_controller')
    except RuntimeError:
        pass  # A live controller owns accepted work and the clock transition.


@owner_effect
def _run(root, step, attempt=0):
    from .protocol import require_current
    require_current(root)
    from . import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    record = execution.record(root, step, attempt)
    with _runner_lease(record):
        contracts.settings(root)
        binding = execution.identity(root, step, attempt)
        if contracts.read(record / 'native-intent.json').get('task_ref') != contracts.record_ref(root / 'jobs' / f'{step:03d}.json'):
            raise ValueError('Runner task binding changed')
        if any((record / name).exists() for name in
               ('runner-process.json', 'native-process.json', 'native-outcome.json')):
            raise RuntimeError('Original runner identity is retained; refusing duplicate execution')
        _subreaper()
        contracts.write(record / 'runner-process.json', process_identity(os.getpid(), binding))
        _spawn(root, step, 'watchdog', attempt)
        ready_until = time.monotonic() + 5
        while not (record / 'watchdog-process.json').exists():
            if time.monotonic() >= ready_until:
                raise RuntimeError('Local cleanup watchdog did not attach; no native launch')
            time.sleep(.02)
        if not process_matches(contracts.read(record / 'watchdog-process.json'), binding):
            raise RuntimeError('Local cleanup watchdog identity is unavailable; no native launch')
        from .native import run_native
        from . import handoff_state
        try:
            with handoff_state.scope(root):
                run_native(root, step, attempt)
        finally:
            _complete_clock(root)


def _main():
    mode, root, step, *tail = sys.argv[1:]
    root, step = Path(root), int(step)
    if mode == 'watchdog':
        _watch(root, step, int(tail[0]) if tail else 0)
    elif mode == 'runner':
        _run(root, step, int(tail[0]) if tail else 0)
    else:
        raise ValueError('Unknown local runtime mode')


if __name__ == '__main__':
    _main()
