"""Retained startup ordering, without a provider, native launcher or solver.

The old-order counterexample deliberately schedules the real retained handler
between the real alias inventory and its recheck. It is not a historical-red
reproduction. Only the explicitly marked tests fork an inert bridge process.
"""
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import controller, contracts as c, experiments as e
from scidiscovery.research_loop import shared_input_views as views
from scidiscovery.research_loop import worker_execution_bridge as bridge


class FiniteStop:
    def __init__(self, polls=0):
        self.polls = polls
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        assert timeout == .1
        if self.stopped or not self.polls:
            return True
        self.polls -= 1
        return False


def forbidden(*args, **kwargs):
    pytest.fail('Retained reconciliation must not dispatch or discover new calls')


@pytest.fixture
def server(tmp_path, monkeypatch, execution_registry):
    def no_process(*args, **kwargs):
        pytest.fail('Readiness fixtures must not launch native, solver or other subprocesses')
    monkeypatch.setattr(subprocess, 'Popen', no_process)
    monkeypatch.setattr(subprocess, 'run', no_process)
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Inert retained-bridge startup ordering fixture.\n')
    root = tmp_path / 'instance'
    # Produce the current accepted prefix, original globals and material fields
    # through their owners. The canonical registry supplies source EntryPoint
    # metadata only; neither this native declaration nor local execution runs.
    controller.prepare(root, objective, public, ['NativeDummyCLI'],
        max_tokens=1000, max_seconds=60, max_steps=2,
        model='fixture', reasoning_effort='high', search_policy=None,
        experiment_mode='local')
    job = c.build_job(root, 1, 'W', [])
    work = c.workspace(root, 1)
    directory = work / bridge.SUBMISSION_DIRECTORY
    directory.mkdir(parents=True)
    (work / 'inputs').mkdir(exist_ok=True)
    source = work / 'inputs/protected.txt'
    source.write_bytes(b'exact protected original\n')
    source.chmod(0o400)
    record = root / 'native-evidence/001/continuations/001'
    record.mkdir(parents=True)
    claims = root / 'private/worker-submissions'
    claims.mkdir(parents=True)
    configuration = dict(record_dir=str(root / 'private/records'), scope=dict(step=1, role='W'))
    value = SimpleNamespace(root=root, job=job, work=work, directory=directory, record=record,
        claims=claims, diagnostic=record / 'diagnostic.json', protected=source,
        client=dict(step=1, attempt=1), configuration=configuration,
        identity=dict(path=str(root / 'jobs/001.json')), stop=FiniteStop(), launch=forbidden)
    # Isolate configuration loading, not claim proofs, cached-result verification,
    # publication, or alias acceptance. No scientific execution is accepted here.
    monkeypatch.setattr(bridge, '_configuration', lambda selected: selected.configuration)
    monkeypatch.setattr(bridge, '_execute', forbidden)
    monkeypatch.setattr(bridge, '_response_calls', forbidden)
    monkeypatch.setattr(e, 'main', forbidden)
    return value


def seed(server, *, known=True, arguments=None):
    request = dict(submission_ref='S001', arguments=arguments or ['capabilities'])
    raw = bridge._encoded(request)
    claim = server.claims / request['submission_ref']
    claim.mkdir()
    bridge._publish(claim / 'submit.json', raw)
    c.write(claim / 'binding.json', dict(step=1, attempt=0,
        configuration=server.configuration, retained_at_start=False,
        submission=e.proof(claim / 'submit.json')))
    response = dict(code=0, result=dict(capabilities=[]), submission_ref='S001', request=request)
    if known:
        bridge._publish(claim / 'response.json', bridge._encoded(response))
        c.write(claim / 'outcome.json', dict(submission=e.proof(claim / 'binding.json'),
            response=e.proof(claim / 'response.json'), calls=[]))
    bridge._publish(server.directory / 'submit.json', raw)
    bridge._publish(server.directory / 'response.json', bridge._encoded(response))
    return raw, claim


def mount_facts(server):
    # A prepared job needs no fabricated native intent/attempt or source scope.
    # Load its exact original/material facts through the production owner, then
    # feed them to the real Pi inventory alongside the explicit protected path.
    sources = views.load_source_facts(server.root, server.job, server.work)
    selected_code = views.materialize_selected_code(server.root,
        views.selected_code_from_facts(sources))
    return views.load_pi_mount_facts(server.root, server.job, server.work,
        protected_paths=[server.protected], source_facts=sources,
        selected_code=selected_code)


def after_workspace_walk(monkeypatch, server, action):
    original = views._Inventory.walk
    fired = []
    def walk(inventory, path):
        yield from original(inventory, path)
        if path == server.work:
            assert not fired
            fired.append(True)
            action()
    monkeypatch.setattr(views._Inventory, 'walk', walk)
    return fired


def test_old_ready_first_schedule_rejects_legitimate_cached_response(server, monkeypatch):
    raw, claim = seed(server)
    reply = server.directory / 'response.json'
    original = e.proof(reply)
    cached = e.proof(claim / 'response.json')
    events = []
    def retained_replacement():
        events.append('retained')
        bridge._watch(server, FiniteStop(), raw)
        assert reply.read_bytes() == (claim / 'response.json').read_bytes()
        assert e.proof(reply)['file_stat'][:2] != original['file_stat'][:2]
    fired = after_workspace_walk(monkeypatch, server, retained_replacement)
    # This is the old process's ready -> parent inventory -> initial _watch
    # schedule, forced without threads, sleeps, subprocesses or fake valpasses.
    events.append('ready')
    with pytest.raises(e.UnsupportedExecution, match='workspace alias changed during launch inventory'):
        mount_facts(server)
    assert fired == [True] and events == ['ready', 'retained']
    assert e.proof(claim / 'response.json') == cached


def test_watch_settles_atomic_cached_publish_before_ready_and_deduplicates(server, monkeypatch):
    raw, claim = seed(server)
    reply = server.directory / 'response.json'
    before = e.proof(reply)
    originals = {path: e.proof(path) for path in claim.iterdir()}
    calls = []
    handle = bridge._handle
    def tracked(selected, message, *, retained=False):
        calls.append((message, retained))
        handle(selected, message, retained=retained)
    monkeypatch.setattr(bridge, '_handle', tracked)
    observations = []
    def ready():
        assert calls == [(raw, True)]
        assert reply.read_bytes() == (claim / 'response.json').read_bytes()
        assert e.proof(reply)['file_stat'][:2] != before['file_stat'][:2]
        facts = mount_facts(server)
        assert str(server.protected) in facts['mappings']
        observations.append(e.proof(reply))
    bridge._watch(server, FiniteStop(polls=3), raw, on_ready=ready)
    assert len(observations) == 1 and e.proof(reply) == observations[0]
    assert calls == [(raw, True)]  # Unchanged retained bytes never become fresh.
    assert {path: e.proof(path) for path in originals} == originals


def test_watch_without_initial_signals_ready_once(server):
    ready = []
    bridge._watch(server, FiniteStop(polls=3), None, on_ready=lambda: ready.append(True))
    assert ready == [True]
    assert not (server.directory / 'response.json').exists()


@pytest.mark.parametrize('state', ['unknown', 'unclaimed', 'damaged'])
def test_retained_unknown_or_error_never_grants_fresh_dispatch(server, state):
    raw, claim = seed(server, known=False, arguments=['research-declare'])
    if state == 'unclaimed':
        for path in claim.iterdir():
            path.unlink()
        claim.rmdir()
    elif state == 'damaged':
        bridge._publish(claim / 'submit.json', b'different original bytes\n')
    originals = {path: e.proof(path) for path in claim.iterdir()} if claim.exists() else {}
    responses = []
    publications = []
    def ready():
        responses.append(json.loads((server.directory / 'response.json').read_bytes()))
        publications.append(e.proof(server.directory / 'response.json'))
    bridge._watch(server, FiniteStop(polls=3), raw, on_ready=ready)
    assert len(responses) == 1
    assert e.proof(server.directory / 'response.json') == publications[0]
    response = responses[0]
    assert response['submission_ref'] == 'S001'
    assert response['request'] == json.loads(raw)
    assert response['result']['status'] == ('integrity_error' if state == 'damaged' else 'unknown')
    assert {path: e.proof(path) for path in originals} == originals
    if state == 'unclaimed':
        assert c.read(claim / 'binding.json')['retained_at_start'] is True
        assert c.read(claim / 'outcome.json')['calls'] == []


@pytest.mark.parametrize('failure', ['stop', 'exception'])
def test_unsettled_or_stopped_watch_does_not_signal_ready(server, monkeypatch, failure):
    raw, _ = seed(server)
    ready = []
    handle = bridge._handle
    def interrupted(selected, message, *, retained=False):
        if failure == 'exception':
            raise RuntimeError('retained startup failed')
        handle(selected, message, retained=retained)
        selected.stop.set()
    monkeypatch.setattr(bridge, '_handle', interrupted)
    if failure == 'exception':
        with pytest.raises(RuntimeError, match='retained startup failed'):
            bridge._watch(server, server.stop, raw, on_ready=lambda: ready.append(True))
    else:
        bridge._watch(server, server.stop, raw, on_ready=lambda: ready.append(True))
    assert ready == []


def test_invalid_initial_frame_does_not_signal_ready(server):
    ready = []
    with pytest.raises(ValueError):
        bridge._watch(server, FiniteStop(), b'{', on_ready=lambda: ready.append(True))
    assert ready == []


def test_retained_publication_failure_does_not_signal_ready(server, monkeypatch):
    raw, claim = seed(server)
    original = e.proof(claim / 'response.json')
    publish = bridge._publish
    def failed_publish(path, data):
        if path == server.directory / 'response.json':
            raise OSError('retained publication failed')
        publish(path, data)
    monkeypatch.setattr(bridge, '_publish', failed_publish)
    ready = []
    with pytest.raises(OSError, match='retained publication failed'):
        bridge._watch(server, FiniteStop(), raw, on_ready=lambda: ready.append(True))
    assert ready == [] and e.proof(claim / 'response.json') == original


def test_protected_source_replacement_after_ready_still_fails_closed(server, monkeypatch):
    raw, _ = seed(server)
    ready = []
    def replace_protected():
        assert ready == [True]
        bridge._publish(server.protected, b'exact protected original\n')
    fired = after_workspace_walk(monkeypatch, server, replace_protected)
    def inventory():
        ready.append(True)
        mount_facts(server)
    with pytest.raises(e.UnsupportedExecution, match='workspace alias changed during launch inventory'):
        bridge._watch(server, FiniteStop(), raw, on_ready=inventory)
    assert fired == [True]


@pytest.mark.process_e2e
@pytest.mark.skipif(sys.platform != 'linux', reason='Bridge lifecycle uses Linux process identities')
@pytest.mark.parametrize('completion', ['known', 'exception', 'stop'])
def test_real_bridge_ready_fd_follows_retained_settlement(server, monkeypatch, completion):
    """Exercise _bridge_process itself: moving only _watch cannot satisfy this."""
    raw, claim = seed(server)
    entered = server.record / 'retained-entered'
    release = server.record / 'release-retained'
    server.stop = threading.Event()
    handle = bridge._handle
    def blocked(selected, message, *, retained=False):
        assert retained is True
        entered.touch()
        deadline = time.monotonic() + 3
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('parent did not release retained fixture')
            time.sleep(.01)
        if completion == 'exception':
            raise RuntimeError('retained fixture failed')
        handle(selected, message, retained=retained)
        if completion == 'stop':
            selected.stop.set()
    monkeypatch.setattr(bridge, '_handle', blocked)
    read_ready, write_ready = os.pipe()
    parent_pid = os.getpid()
    pid = os.fork()
    if pid == 0:
        os.close(read_ready)
        bridge._bridge_process(server, raw, parent_pid, write_ready)
    os.close(write_ready)
    reaped = False
    try:
        deadline = time.monotonic() + 2
        while not entered.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        assert entered.exists(), 'Bridge did not reach retained reconciliation'
        assert not select.select([read_ready], [], [], 0)[0], 'Readiness preceded reconciliation'
        release.touch()
        assert select.select([read_ready], [], [], 3)[0], 'Readiness descriptor was not settled'
        assert os.read(read_ready, 1) == (b'1' if completion == 'known' else b'')
        if completion == 'known':
            assert (server.directory / 'response.json').read_bytes() == (claim / 'response.json').read_bytes()
    finally:
        release.touch()
        try:
            os.kill(pid, signal.SIGUSR1)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if os.waitpid(pid, os.WNOHANG)[0] == pid:
                reaped = True
                break
            time.sleep(.01)
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(read_ready)
    assert reaped, 'Forced termination is not a positive bridge cleanup receipt'
    bridge.stopped(server.record, server.identity)
    if completion == 'exception':
        assert c.read(server.diagnostic)['error'] == 'RuntimeError'
