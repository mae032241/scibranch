"""One optional real-browser flow; no install, native model or external service."""
import json
import os
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import instance_ui
from test_execution_authority import prepare
from test_history_handoff import finish


@pytest.mark.process_e2e
def test_installed_browser_read_scope_and_pause(tmp_path, monkeypatch):
    if os.environ.get('SCID_RUN_BROWSER_CHECK') != '1':
        pytest.skip('Real browser check is opt-in: SCID_RUN_BROWSER_CHECK=1; use the resource supervisor')
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    if chrome is None:
        pytest.skip('An existing Chrome/Chromium is required; never install for this test')
    root, key = prepare(tmp_path, enabled=False)
    _, _, record = finish(root, 1, 'H', [], action='analyze')
    originals = {path: path.read_bytes() for path in (record / 'science-output.json', record / 'result-identity.json')}
    sealed = json.loads(originals[record / 'science-output.json'])
    ui = instance_ui.InstanceUI(root, secret_file=key)
    ready = threading.Event(); servers = []; errors = []
    server_type = instance_ui.ThreadingHTTPServer

    class Server(server_type):
        def serve_forever(self, **kwargs):
            servers.append(self); ready.set(); super().serve_forever(**kwargs)

    monkeypatch.setattr(instance_ui, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(instance_ui, 'InstanceUI', lambda *args, **kwargs: ui)
    monkeypatch.setattr(instance_ui, 'print', lambda *args, **kwargs: None, raising=False)

    def serve():
        try:
            instance_ui.serve(root, secret_file=key)
        except BaseException as error:
            errors.append(error); ready.set()

    # AF_UNIX socket names have a small OS limit; the repository's nested
    # supervisor scratch path can exceed it. Keep this one profile local/short.
    browser_home = tempfile.TemporaryDirectory(prefix='scid-browser-', dir='/tmp')
    worker = threading.Thread(target=serve); worker.start()
    browser = None
    try:
        assert ready.wait(5) and not errors
        base = 'http://127.0.0.1:' + str(servers[0].server_port)
        # CDP's existing pipe interface avoids a new websocket/browser dependency.
        with (tmp_path / 'chrome.stderr').open('wb') as diagnostics:
            browser = subprocess.Popen(
                ['bash', '-c', 'exec 3<&0 4>&1; exec "$@"', 'browser', chrome,
                 '--headless', '--no-first-run', '--no-default-browser-check',
                 '--disable-background-networking', '--disable-component-update',
                 '--disable-extensions', '--disable-sync', '--disable-dev-shm-usage',
                 '--renderer-process-limit=1', '--disable-features=BackForwardCache',
                 '--remote-debugging-pipe', '--user-data-dir=' + browser_home.name + '/profile',
                 'about:blank'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=diagnostics, start_new_session=True,
                env=dict(os.environ, TMPDIR=browser_home.name, XDG_CONFIG_HOME=browser_home.name + '/config'))
            sequence = 0; pending = bytearray()

            def call(method, params=None, session=None):
                nonlocal sequence
                sequence += 1
                message = dict(id=sequence, method=method, params=params or {})
                if session is not None: message['sessionId'] = session
                browser.stdin.write(json.dumps(message).encode() + b'\0'); browser.stdin.flush()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    while b'\0' in pending:
                        packet, _, rest = pending.partition(b'\0'); pending[:] = rest
                        reply = json.loads(packet)
                        if reply.get('id') == sequence:
                            assert 'error' not in reply, reply
                            return reply['result']
                    if select.select([browser.stdout], [], [], .1)[0]:
                        chunk = os.read(browser.stdout.fileno(), 65536)
                        if not chunk:
                            with (tmp_path / 'chrome.stderr').open('rb') as failure:
                                failure.seek(max(0, os.fstat(failure.fileno()).st_size - 4000))
                                pytest.fail('Browser exited: ' + failure.read(4000).decode('utf-8', errors='replace'))
                        pending.extend(chunk)
                pytest.fail('Browser command timed out: ' + method)

            pages = [target for target in call('Target.getTargets')['targetInfos'] if target['type'] == 'page']
            assert len(pages) == 1 and pages[0]['url'] == 'about:blank', pages
            target = pages[0]['targetId']
            session = call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']

            def evaluate(expression):
                result = call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)
                assert 'exceptionDetails' not in result, result
                return result['result'].get('value')

            def wait_for(expression):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if evaluate(expression): return
                    time.sleep(.05)
                pytest.fail('Browser page did not reach expected state')

            for access, token in [('view', ui.read_token), ('manage', ui.management_token)]:
                call('Page.navigate', {'url': base + '/' + access + '?' + urlencode({'token': token})}, session)
                wait_for("location.pathname === '/' && document.readyState === 'complete' && "
                         "document.querySelectorAll('form[action=\"/control/pause\"]').length === "
                         + ('1' if access == 'manage' else '0'))
                controls = evaluate("document.querySelectorAll('form[action=\"/control/pause\"]').length")
                assert controls == (1 if access == 'manage' else 0)
                if access == 'view':
                    evaluate("document.querySelector('a[href=\"/results\"]').click(); true")
                    wait_for("location.pathname === '/results' && document.readyState === 'complete' && "
                             "Boolean(document.querySelector('a[href=\"/results/1\"]'))")
                    assert evaluate("document.querySelectorAll('form[action=\"/control/pause\"]').length") == 0
                    evaluate("document.querySelector('a[href=\"/results/1\"]').click(); true")
                    wait_for("location.pathname === '/results/1' && document.readyState === 'complete' && "
                             "Boolean(document.querySelector('details.full-text > summary'))")
                    evaluate("document.querySelector('details.full-text > summary').click(); true")
                    assert evaluate("Array.from(document.querySelectorAll('details.full-text[open] .finding'))"
                                    ".some(element => element.textContent === " + json.dumps(sealed['scientific_result']) + ")")
                    assert evaluate("document.querySelectorAll('form[action=\"/control/pause\"]').length") == 0
            assert not (root / 'pause.request').exists()
            evaluate("document.querySelector('form[action=\"/control/pause\"] button').click(); true")
            deadline = time.monotonic() + 5
            while not (root / 'pause.request').exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert (root / 'pause.request').exists()
            assert not list(root.glob('**/accepted.json'))
            assert all(path.read_bytes() == raw for path, raw in originals.items())
    finally:
        try:
            if browser is not None:
                try:
                    try: os.killpg(browser.pid, signal.SIGTERM)
                    except ProcessLookupError: pass
                    try: browser.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(browser.pid, signal.SIGKILL); browser.wait(timeout=3)
                finally:
                    browser.stdin.close(); browser.stdout.close()
        finally:
            try:
                if servers:
                    servers[0].shutdown(); servers[0].server_close()
            finally:
                try: worker.join(5)
                finally: browser_home.cleanup()
    assert not worker.is_alive() and not errors
