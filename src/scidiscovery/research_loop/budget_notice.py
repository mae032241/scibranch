"""Bounded budget advice at a native tool boundary; never an execution grant.

This file is also the staged hook executable. Its hook path uses only stdlib,
one fixed current hint and one exclusive marker per logical node and dimension.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time

SCOPE = 'advice_only_not_authorization_or_scientific_evidence'
MAX_NOTICE_BYTES = 200
HELPER = '.scid-budget-notice.py'
EMPTY_CONFIG = '.scid-budget-notice-empty-config.toml'
VISIBLE = 'scid-budget-hook-visible-v1'
UNAVAILABLE_REASONS = {'not_preflighted', 'unsupported_backend', 'undeclared_native_permissions',
    'unsupported_namespace', 'hook_preflight_unavailable', 'hook_trust_unqualified', 'helper_unavailable'}


def public_channel(value):
    if isinstance(value, dict) and value == dict(status='available', mechanism='codex_post_tool_use'):
        return dict(value, limitation='Next local tool boundary only; running model requests and hosted web cannot be interrupted.')
    reason = value.get('reason') if isinstance(value, dict) else None
    return dict(status='unavailable', reason=reason if reason in UNAVAILABLE_REASONS else 'not_preflighted')


def closeout_notice(job, intent, *, dimension, target_reached, shared_reached, target_near=False,
        shared_risk=False, shared_expired=False, shared_exhausted=False, followup_tokens=None,
        author=False, final_return=False):
    """Render only already-loaded observations; unknown usage is never zero."""
    if dimension not in {'token', 'time'}:
        raise ValueError('Unknown budget advice dimension')
    # Global exhaustion changes ordinary A's advice, not this dimension's trigger.
    if not (target_reached or target_near or shared_reached or shared_risk):
        return None
    cause = 'shared' if shared_reached or shared_risk else 'node'
    risk = dimension.capitalize()+' budget risk.'
    if intent.get('output_repair'):
        text = (risk+' Finish output repair now; keep science/plans/limits/future action. '
            'No work/rerun/replay; permissions hold; unknown usage != 0.')
    elif intent.get('work_repair'):
        text = (risk+' Finish accepted missing work or report gaps now. '
            'Keep original authorization/action; no expansion/unknown replay; unknown usage != 0.')
    elif job.get('formal_review'):
        text = (risk+' Finish this exact independent review now from existing evidence; retain gaps and verdict authority. '
            'No new work; unknown usage != 0.')
    elif author:
        text = (risk+' Return author-only JSON from existing results now; retain gaps. '
            'No new work/replay; permissions hold; unknown usage != 0.')
    elif final_return:
        text = (risk+' Complete accepted final return now from existing results; retain gaps. '
            'No extra FINAL/new work; permissions hold; unknown usage != 0.')
    elif job['role'] == 'A' and not (job.get('purpose') == 'closeout' or job.get('final_report') or job.get('final_only')):
        if shared_expired or shared_reached or shared_exhausted:
            cause = 'shared'
            timing = ('Shared time expired.' if shared_expired else
                f'Shared {dimension} budget reached.' if shared_reached else risk+' Shared budget exhausted.')
            text = (timing+' A: conclude now; deliver an eligible existing candidate or finish with gaps. '
                'No H/W/E/extra FINAL; permissions hold; unknown usage != 0.')
        else:
            text = (risk+' A: end this assessment; retain unmet goals. '
                'Choose normal next_action by evidence/shared balance. '
                'Soft targets do not end research; permissions hold; unknown usage != 0.')
    elif followup_tokens:
        roles = '+'.join(followup_tokens)
        text = (risk+f' Wrap up for suggested {roles} token targets. '
            'No reservation/grant; return JSON with gaps; permissions hold; unknown usage != 0.')
    elif shared_reached:
        text = (f'Shared {dimension} budget reached. Return scientific JSON from existing results now; retain gaps. '
            'No new work/replay; permissions hold; unknown usage != 0.')
    else:
        text = (risk+' Finish bounded actions and return scientific JSON now with limits. '
            'Soft target; permissions hold; unknown usage != 0.')
    if len(text.encode('utf-8')) > MAX_NOTICE_BYTES:
        raise ValueError('Budget closeout notice exceeds its fixed response allowance')
    return dict(cause=cause, text=text)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def published_notices(value):
    """Accept the bounded current host projection from its sole producer."""
    if not isinstance(value, dict) or value.get('scope') != SCOPE:
        return None
    if not _finite(value.get('updated_at_unix')) or value.get('inflight_usage_unknown') is not True:
        return None
    notices = value.get('closeout_notices')
    if not isinstance(notices, dict) or not notices or not set(notices).issubset({'token', 'time'}):
        return None
    # Shared risk also includes the producer's follow-up advice and time ratio.
    # The hook consumes that bounded projection; it does not reload settings or
    # reconstruct the producer's plan to decide whether advice is warranted.
    for notice in notices.values():
        if not isinstance(notice, dict) or set(notice) != {'cause', 'text'}:
            return None
        if notice.get('cause') not in {'node', 'shared'} or not isinstance(notice.get('text'), str):
            return None
        if len(notice['text'].encode('utf-8')) > MAX_NOTICE_BYTES:
            return None
        if notice['cause'] == 'node' and value.get('target_state') not in {'near', 'reached'}:
            return None
    return notices


def read_hint(work):
    """The hook's sole input read: fixed name, ordinary file, <=4096 bytes."""
    path = Path(work) / 'work/budget-status.json'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            data = stream.read(4097)
        if len(data) > 4096:
            return None
        return published_notices(json.loads(data.decode('utf-8')))
    except (OSError, ValueError, UnicodeError, RecursionError):
        return None


def hook_output(work, attempt):
    notices = read_hint(work)
    if notices is None:
        return None
    directory = Path(work) / 'work'
    try:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            for dimension in ('time', 'token'):
                if dimension not in notices:
                    continue
                try:
                    item = os.open('.budget-notice-'+dimension,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400, dir_fd=fd)
                except FileExistsError:
                    continue
                os.close(item)
                return dict(hookSpecificOutput=dict(hookEventName='PostToolUse', additionalContext=notices[dimension]['text']))
        finally:
            os.close(fd)
    except OSError:
        return None
    return None


def definition(work, attempt, *, python=None):
    command = shlex.join([python or sys.executable, str(Path(work) / 'work' / HELPER),
        '--work', str(work), '--attempt', str(attempt)])
    return dict(PostToolUse=[dict(hooks=[dict(type='command', command=command, timeout=5,
        additionalContextLimit=0, **{'async': False})])])


def stage_helper(work):
    """Publish this small stdlib executable before readonly namespace assembly."""
    directory = Path(work) / 'work'
    destination = directory / HELPER
    temporary = directory / (HELPER + '-' + os.urandom(8).hex())
    try:
        source = Path(__file__).read_bytes()
        if len(source) > 32768:
            raise ValueError('Budget hook helper exceeds its staging bound')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(source)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        empty = directory / EMPTY_CONFIG
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
        os.close(fd)
        os.replace(temporary, empty)
        return destination
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def inventory(value, work):
    """Validate one bounded hooks/list result without accepting warnings."""
    if not isinstance(value, dict) or set(value) != {'data'} or not isinstance(value['data'], list) or len(value['data']) != 1:
        raise ValueError('Hook inventory is unavailable')
    entry = value['data'][0]
    if (not isinstance(entry, dict) or set(entry) != {'cwd', 'hooks', 'warnings', 'errors'}
            or entry['cwd'] != str(work) or entry['warnings'] != [] or entry['errors'] != []
            or not isinstance(entry['hooks'], list) or len(entry['hooks']) > 128):
        raise ValueError('Hook inventory has warnings, errors or an unsupported shape')
    hooks = {}
    for hook in entry['hooks']:
        if (not isinstance(hook, dict) or not isinstance(hook.get('key'), str) or hook['key'] in hooks
                or type(hook.get('enabled')) is not bool or type(hook.get('isManaged')) is not bool
                or hook.get('trustStatus') not in {'trusted', 'managed', 'untrusted', 'modified'}
                or not isinstance(hook.get('currentHash'), str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', hook['currentHash'])):
            raise ValueError('Hook inventory is ambiguous')
        hooks[hook['key']] = hook
    return hooks


def validate_hook_trust(before, after, declared, work):
    """Only our entire hook may need the invocation-local trust bypass."""
    original, candidate = inventory(before, work), inventory(after, work)
    if any(candidate.get(key) != hook for key, hook in original.items()):
        raise ValueError('Existing native hooks changed')
    added = [hook for key, hook in candidate.items() if key not in original]
    if len(added) != 1:
        raise ValueError('Internal hook is absent or duplicated')
    own = added[0]
    handler = declared['PostToolUse'][0]['hooks'][0]
    if (own.get('source') != 'sessionFlags' or own.get('sourcePath') != '/<session-flags>/config.toml'
            or not re.fullmatch(r'/<session-flags>/config\.toml:post_tool_use:[0-9]+:[0-9]+', own['key'])
            or own.get('eventName') != 'postToolUse' or own.get('matcher') is not None
            or own['enabled'] is not True or own['isManaged'] is not False
            or own['trustStatus'] not in {'untrusted', 'trusted'}
            or own.get('handlerType') != 'command' or own.get('command') != handler['command']
            or own.get('async', False) is not False or own.get('timeoutSec') != handler['timeout']
            or own.get('additionalContextLimit') != handler['additionalContextLimit']
            or own.get('statusMessage') is not None or own.get('pluginId') is not None
            or own.get('environmentId') is not None):
        raise ValueError('The internal hook definition did not qualify')
    for key, hook in candidate.items():
        if key == own['key'] or not hook['enabled']:
            continue
        if hook['trustStatus'] != 'trusted' and not (hook['trustStatus'] == 'managed' and hook['isManaged']):
            raise ValueError('Another enabled hook is not already trusted or managed')
    return True


def list_hooks(argv, work, env, *, pass_fds=(), timeout=5):
    """Bounded metadata-only app-server session; never create a thread or turn."""
    process = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, pass_fds=pass_fds)
    pending = b''
    stderr_tail = b''
    total = 0
    initialized = False
    deadline = time.monotonic() + timeout
    def send(value):
        process.stdin.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
        process.stdin.flush()
    try:
        send(dict(id=1, method='initialize', params=dict(clientInfo=dict(name='scid-hook-capability', version='1'),
            capabilities=dict(experimentalApi=True))))
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            selector.register(process.stderr, selectors.EVENT_READ)
            while selector.get_map() and time.monotonic() < deadline:
                events = selector.select(max(0, deadline - time.monotonic()))
                if not events:
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    if key.fileobj is process.stderr:
                        stderr_tail = (stderr_tail + chunk)[-1024:]
                        continue
                    total += len(chunk)
                    if total > 131072:
                        raise ValueError('Native hook inventory exceeded its byte bound')
                    pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    message = json.loads(line)
                    if isinstance(message, dict) and 'error' in message:
                        error=message['error']
                        detail=({key:error[key] for key in ('code','message') if key in error}
                            if isinstance(error,dict) else {'message':'Malformed native RPC error'})
                        raise ValueError('Native hook inventory RPC error: '+json.dumps(detail,ensure_ascii=False)[:768])
                    if not isinstance(message, dict):
                        raise ValueError('Native hook inventory RPC failed')
                    if message.get('id') == 1 and not initialized and 'result' in message:
                        initialized = True
                        send(dict(method='initialized'))
                        send(dict(id=2, method='hooks/list', params=dict(cwds=[str(work)])))
                    elif message.get('id') == 2 and initialized and 'result' in message:
                        return message['result']
                    elif 'id' in message:
                        raise ValueError('Unexpected native hook inventory RPC')
        raise ValueError('Native hook inventory timed out or ended early; stderr='
            +stderr_tail.decode('utf-8',errors='replace'))
    finally:
        if process.stdin is not None:
            process.stdin.close()
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=1)
        process.stdout.close()
        process.stderr.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--attempt', type=int, required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    if not args.work.is_absolute() or not 0 <= args.attempt <= 1_000_000:
        return 0
    if args.check:
        # No scientific/control record reads and no side effects in qualification.
        if (not Path(__file__).is_symlink() and Path(__file__).is_file()
                and not stat.S_IMODE(Path(__file__).stat().st_mode) & 0o222
                and (args.work / 'work/budget-status.json').is_file()):
            print(VISIBLE)
        return 0
    output = hook_output(args.work, args.attempt)
    if output is not None:
        print(json.dumps(output, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
