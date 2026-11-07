"""Host CLI supervisor using the container runner's Linux process helpers."""
from __future__ import annotations

import argparse
import math
from pathlib import Path
import signal
import subprocess
import sys
import time

if __package__:
    from .runner import atomic_json, _subreaper, _owned_descendants, _signal_owned, _reap_adopted
else:
    from runner import atomic_json, _subreaper, _owned_descendants, _signal_owned, _reap_adopted

_STOP = False


def _request_stop(_number, _frame):
    global _STOP
    _STOP = True


def supervise(argv, deadline_epoch, receipt):
    if sys.platform != "linux":
        raise RuntimeError("the host supervisor requires Linux")
    _subreaper()
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, _request_stop)
    started = time.time()
    cutoff = None if deadline_epoch is None else time.monotonic() + max(0., deadline_epoch - started)
    process = None
    reason, returncode, cleanup = "deadline", None, True
    if deadline_epoch is None or started < deadline_epoch:
        process = subprocess.Popen(argv, start_new_session=True, stdin=subprocess.DEVNULL)
        reason, cleanup = "exited", False
    try:
        while process is not None:
            returncode = process.poll()
            if _STOP or (cutoff is not None and (
                    time.monotonic() >= cutoff or time.time() >= deadline_epoch)):
                reason = "cancelled" if _STOP else "deadline"
                break
            if returncode is not None:
                break
            time.sleep(.05)
        stop_started = time.monotonic()
        while process is not None and time.monotonic() - stop_started < 8:
            returncode = process.poll()
            # Do not let generic adopted-child reaping consume Popen's status.
            if returncode is not None:
                _reap_adopted()
            observed = _owned_descendants()
            if not observed and returncode is not None:
                cleanup = True
                break
            number = signal.SIGTERM if time.monotonic() - stop_started < 2 else signal.SIGKILL
            _signal_owned(observed, number)
            time.sleep(.05)
    finally:
        atomic_json(receipt, {"reason": reason, "returncode": returncode,
            "cleanup_confirmed": cleanup, "started_at": started,
            "finished_at": time.time()})
    return returncode if reason == "exited" and cleanup and returncode is not None else 124


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deadline-epoch", type=float)
    parser.add_argument("--receipt", required=True, type=Path)
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not argv or (args.deadline_epoch is not None and (
            not math.isfinite(args.deadline_epoch) or args.deadline_epoch <= 0)):
        parser.error("a command and, when supplied, finite positive original deadline are required")
    return supervise(argv, args.deadline_epoch, args.receipt)


if __name__ == "__main__":
    raise SystemExit(main())
