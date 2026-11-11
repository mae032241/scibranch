"""Application host lifecycle checks; no scientific execution is started."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen

import pytest

from scidiscovery import app_cli
from scidiscovery.research_loop import web_service


@pytest.fixture
def service(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / 'src'
    monkeypatch.setenv('PYTHONPATH', str(source) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    state = tmp_path / 'state'
    workspace = tmp_path / 'workspace'
    yield dict(state_dir=state, workspace=workspace)
    web_service.stop(state_dir=state)


def test_status_and_stop_do_not_create_state(tmp_path):
    state = tmp_path / 'absent'
    assert web_service.status(state_dir=state) == {'status': 'stopped'}
    assert web_service.stop(state_dir=state) == {'status': 'stopped'}
    assert not state.exists()


@pytest.mark.parametrize('outcome', ['publish', 'release'])
def test_stop_waits_for_unpublished_startup_lock(tmp_path, monkeypatch, outcome):
    state = tmp_path / 'state'
    state.mkdir()
    lock = (state / 'service.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    observed = threading.Event()
    original_locked = web_service._locked
    terminated = []

    def observe_locked(directory):
        held = original_locked(directory)
        if held:
            observed.set()
        return held

    monkeypatch.setattr(web_service, '_locked', observe_locked)
    monkeypatch.setattr(web_service, '_terminate', terminated.append)
    # A real startup lock precedes publication. The handshake proves stop has
    # observed it before the test permits publication or startup abandonment.
    with ThreadPoolExecutor(max_workers=1) as pool:
        stopping = pool.submit(web_service.stop, state_dir=state)
        try:
            assert observed.wait(timeout=2)
            assert not stopping.done()
            if outcome == 'publish':
                owner = dict(web_service._identity(os.getpid()), status='running',
                    url='http://127.0.0.1:1/')
                web_service._write(state, owner)
            else:
                lock.close()
            assert stopping.result(timeout=2) == {'status': 'stopped'}
            assert terminated == ([owner] if outcome == 'publish' else [])
        finally:
            lock.close()


def test_stop_reports_unresolved_startup_instead_of_success(tmp_path, monkeypatch):
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(web_service, 'START_TIMEOUT', .05)
    with (state / 'service.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(RuntimeError, match='startup is unresolved'):
            web_service.stop(state_dir=state)


@pytest.mark.process_e2e
def test_background_repeated_start_restart_and_private_state(service, tmp_path, monkeypatch):
    first = web_service.start(**service)
    assert first['status'] == 'running'
    assert first['pid'] != os.getpid()
    with urlopen(first['url'], timeout=3) as response:
        assert response.status == 200
    # Reopening from another directory with another explicit start option still
    # addresses the sole running host, without creating a new workspace.
    monkeypatch.chdir(tmp_path)
    unused = tmp_path / 'unused'
    assert web_service.start(state_dir=service['state_dir'], workspace=unused) == first
    assert not unused.exists()
    state = service['state_dir']
    for filename in ('service.json', 'secret.key', 'service.log'):
        assert (state / filename).stat().st_mode & 0o777 == 0o600
    assert first['url'] not in (state / 'service.log').read_text()
    web_service.stop(state_dir=state)
    assert web_service.status(state_dir=state)['status'] == 'stopped'
    second = web_service.start(**service)
    assert second['pid'] != first['pid']
    assert second['url'].partition('token=')[2] != first['url'].partition('token=')[2]


@pytest.mark.process_e2e
def test_concurrent_starts_publish_one_owner(service):
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _: web_service.start(**service), range(2)))
    assert first == second
    assert web_service.status(state_dir=service['state_dir']) == first


@pytest.mark.process_e2e
def test_stop_preserves_independent_research_process(service):
    independent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        web_service.start(**service)
        web_service.stop(state_dir=service['state_dir'])
        assert independent.poll() is None
    finally:
        independent.terminate()
        independent.wait(timeout=5)


@pytest.mark.process_e2e
def test_stale_reused_pid_record_is_never_signalled(service, monkeypatch):
    independent = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    state = service['state_dir']
    state.mkdir()
    try:
        identity = web_service._identity(independent.pid)
        assert identity is not None
        with (state / 'service.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with monkeypatch.context() as startup_timeout:
                startup_timeout.setattr(web_service, 'START_TIMEOUT', .05)
                for altered in (dict(identity, start_ticks=identity['start_ticks'] + 1),
                        dict(identity, boot_id='previous-boot')):
                    web_service._write(state, dict(altered, status='running', url='http://127.0.0.1:1/'))
                    with pytest.raises(RuntimeError, match='startup is unresolved'):
                        web_service.stop(state_dir=state)
                    assert independent.poll() is None
        fresh = web_service.start(**service)
        assert fresh['pid'] != independent.pid
    finally:
        independent.terminate()
        independent.wait(timeout=5)


@pytest.mark.process_e2e
def test_unavailable_port_does_not_publish_running_url(service):
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0))
        occupied.listen()
        with pytest.raises(RuntimeError, match='failed to start'):
            web_service.start(**service, port=occupied.getsockname()[1])
    assert web_service.status(state_dir=service['state_dir']) == {'status': 'stopped'}
    assert not (service['state_dir'] / 'service.json').exists()
    diagnostic = (service['state_dir'] / 'service.log').read_text()
    assert 'Traceback (most recent call last)' in diagnostic
    assert 'instance_ui.py' in diagnostic


@pytest.mark.process_e2e
def test_foreground_has_same_discoverable_lifecycle(service):
    foreground = subprocess.Popen([sys.executable, '-m', 'scidiscovery.app_cli', 'web',
        '--foreground', '--workspace', str(service['workspace']), '--state-dir', str(service['state_dir'])],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + web_service.START_TIMEOUT
        current = {'status': 'stopped'}
        while current['status'] != 'running' and time.monotonic() < deadline:
            current = web_service.status(state_dir=service['state_dir'])
            if foreground.poll() is not None:
                pytest.fail(f'foreground host exited: {foreground.communicate()[1]}')
            time.sleep(.05)
        assert current['status'] == 'running'
        assert current['pid'] == foreground.pid
        assert web_service.start(**service) == current
        web_service.stop(state_dir=service['state_dir'])
        output, errors = foreground.communicate(timeout=5)
        assert foreground.returncode == 0, errors
        assert output.strip() == current['url']
    finally:
        if foreground.poll() is None:
            foreground.send_signal(signal.SIGTERM)
            foreground.communicate(timeout=5)


def test_cli_status_and_validation(tmp_path, capsys):
    assert app_cli.main(['web', 'status', '--state-dir', str(tmp_path / 'missing')]) == 0
    assert capsys.readouterr().out.strip() == 'Web service is stopped.'
    with pytest.raises(SystemExit):
        app_cli.main(['web', '--port', '70000'])
