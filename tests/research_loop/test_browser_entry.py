"""Real browser capability entry from another site; no model or controller runs."""
import base64
import hashlib
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scidiscovery.research_loop import instance_ui
from test_execution_authority import prepare


def test_entry_document_keeps_strict_cookie_and_a_secret_free_child_fallback(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui = instance_ui.InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root])
    ui.host = '127.0.0.1:18753'
    child = ui._child(1)
    for selected, prefix in ((ui, ''), (child, '/instances/1')):
        status, body, headers = ui.get(prefix + '/manage?token=' + selected.management_token, host=ui.host)
        assert status == 200 and 'Location' not in headers
        assert b'href="' + prefix.encode() + b'/"' in body
        assert selected.management_token.encode() not in body
        assert selected.read_token.encode() not in body
        assert str(key).encode() not in body and str(root).encode() not in body
        assert b'data-live-url' not in body
        assert headers['Referrer-Policy'] == 'no-referrer'
        assert 'HttpOnly' in headers['Set-Cookie'] and 'SameSite=Strict' in headers['Set-Cookie']
        scripts = re.findall(rb'<script>(.*?)</script>', body, re.S)
        assert b"location.replace('./');" in scripts
        for script in scripts:
            digest = base64.b64encode(hashlib.sha256(script).digest()).decode()
            assert "'sha256-" + digest + "'" in headers['Content-Security-Policy']
        assert "'unsafe-inline'" not in headers['Content-Security-Policy']
    restarted = instance_ui.InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root])
    restarted.host = ui.host
    for access, token in (('manage', ui.management_token), ('view', ui.read_token), ('manage', 'forged')):
        with pytest.raises(PermissionError):
            restarted.get('/' + access + '?token=' + token, host=restarted.host)


@pytest.mark.process_e2e
@pytest.mark.parametrize('scope', ['workspace', 'child-management', 'child-read'])
def test_browser_entry_from_another_site(tmp_path, monkeypatch, scope):
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    node = shutil.which('node')
    if not chrome or not node:
        pytest.skip('Requires existing Chrome and Node; never installs either')
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui_type = instance_ui.InstanceUI
    ui = ui_type(workspace, secret_file=key, workspace=True, instance_roots=[root])
    ready = threading.Event(); servers = []; errors = []; requests = []

    class Server(ThreadingHTTPServer):
        def finish_request(self, request, client_address):
            original = self.RequestHandlerClass

            class Observe(original):
                def do_GET(self):
                    # Record only path, presence of cookies and status, never a capability.
                    self.entry_path = self.path.split('?', 1)[0]
                    self.has_cookie = bool(self.headers.get('Cookie'))
                    super().do_GET()

                def send_response(self, code, message=None):
                    requests.append((getattr(self, 'entry_path', ''), code,
                                     getattr(self, 'has_cookie', False)))
                    super().send_response(code, message)

            Observe(request, client_address, self)

        def serve_forever(self, **options):
            servers.append(self); ready.set(); super().serve_forever(**options)

    class Outside(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = b'<!doctype html><title>External navigation fixture</title><p>Outside site</p>'
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

    monkeypatch.setattr(instance_ui, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(instance_ui, 'InstanceUI', lambda selected, **options:
                        ui if Path(selected) == workspace else ui_type(selected, **options))
    monkeypatch.setattr(instance_ui, 'print', lambda *args, **options: None, raising=False)

    def serve():
        try: instance_ui.serve(workspace, secret_file=key, workspace=True)
        except BaseException as error: errors.append(error); ready.set()

    thread = threading.Thread(target=serve); thread.start()
    outside = ThreadingHTTPServer(('127.0.0.1', 0), Outside)
    outside_thread = threading.Thread(target=outside.serve_forever); outside_thread.start()
    browser = None
    with tempfile.TemporaryDirectory(prefix='scid-entry-', dir='/tmp') as directory:
        base = Path(directory)
        env = dict(os.environ)
        for variable in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME',
                         'XDG_DATA_HOME', 'XDG_RUNTIME_DIR', 'TMPDIR'):
            path = base / variable; path.mkdir(mode=0o700); env[variable] = str(path)
        try:
            assert ready.wait(5) and not errors
            child = ui._child(1)
            target = ui if scope == 'workspace' else child
            mount = '' if scope == 'workspace' else '/instances/1'
            access = 'view' if scope == 'child-read' else 'manage'
            token = target.read_token if access == 'view' else target.management_token
            env['SCID_ENTRY_URL'] = 'http://' + ui.host + mount + '/' + access + '?token=' + token
            env['SCID_ENTRY_PATH'] = mount + '/'
            env['SCID_ENTRY_SCOPE'] = scope
            env['SCID_OUTSIDE_URL'] = 'http://localhost:' + str(outside.server_port) + '/'
            profile = base / 'profile'
            with (tmp_path / 'browser-entry.stderr').open('wb') as diagnostics:
                browser = subprocess.Popen([chrome, '--headless', '--no-sandbox', '--disable-gpu',
                    '--disable-dev-shm-usage', '--disable-background-networking', '--disable-component-update',
                    '--no-first-run', '--no-default-browser-check', '--renderer-process-limit=2',
                    '--remote-debugging-port=0', '--user-data-dir=' + str(profile), 'about:blank'],
                    stdout=subprocess.DEVNULL, stderr=diagnostics, env=env, start_new_session=True)
                port_file = profile / 'DevToolsActivePort'
                deadline = time.monotonic() + 10
                while not port_file.exists() and browser.poll() is None and time.monotonic() < deadline:
                    time.sleep(.05)
                assert port_file.exists(), 'Chrome did not expose its debugging endpoint'
                env['SCID_CHROME_PORT'] = port_file.read_text().splitlines()[0]
                script = r'''
const targets = await (await fetch('http://127.0.0.1:'+process.env.SCID_CHROME_PORT+'/json/list')).json();
const ws = new WebSocket(targets.find(target=>target.type==='page').webSocketDebuggerUrl);
await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject;});
let id=0;const pending=new Map();const blocked=[];
ws.onmessage=event=>{const message=JSON.parse(event.data);if(message.method==='Network.requestWillBeSentExtraInfo'){for(const item of message.params.associatedCookies || [])blocked.push(...item.blockedReasons);}const waiter=pending.get(message.id);if(waiter){pending.delete(message.id);message.error?waiter.reject(message.error):waiter.resolve(message.result);}};
function call(method,params={}){return new Promise((resolve,reject)=>{const next=++id;pending.set(next,{resolve,reject});ws.send(JSON.stringify({id:next,method,params}));});}
async function evaluate(expression){const answer=await call('Runtime.evaluate',{expression,returnByValue:true});if(answer.exceptionDetails)throw new Error('Browser expression failed');return answer.result.value;}
async function waitFor(expression){for(let index=0;index<80;index++){await new Promise(resolve=>setTimeout(resolve,100));if(await evaluate(expression))return;}throw new Error('Entry failed: '+await evaluate('JSON.stringify({path:location.pathname,text:document.body?.innerText?.slice(0,1200)})')+'; blocked cookie reasons: '+JSON.stringify(blocked));}
await call('Network.enable');
await call('Page.navigate',{url:process.env.SCID_OUTSIDE_URL});
await waitFor('document.title === "External navigation fixture"');
// A renderer-initiated navigation preserves the cross-site redirect chain.
// Page.navigate from about:blank does not reproduce Strict-cookie withholding.
await evaluate('location.assign('+JSON.stringify(process.env.SCID_ENTRY_URL)+');true');
const protectedContent=process.env.SCID_ENTRY_SCOPE==='workspace'?'#live-instances':'#live-hero';
await waitFor('location.pathname === '+JSON.stringify(process.env.SCID_ENTRY_PATH)+' && location.search === "" && document.readyState === "complete" && document.querySelector('+JSON.stringify(protectedContent)+') !== null');
if(process.env.SCID_ENTRY_SCOPE==='workspace'){
  if(!(await evaluate(`document.querySelector('a[href="/new"]') !== null`)))throw new Error('Workspace maintenance scope missing');
}else{
  const manager=await evaluate(`document.querySelector('form[action="/instances/1/control/start"]') !== null`);
  if(manager!==(process.env.SCID_ENTRY_SCOPE==='child-management'))throw new Error('Child scope changed');
  const result=await call('Runtime.evaluate',{expression:'fetch("/", {credentials:"same-origin"}).then(response=>response.status)',returnByValue:true,awaitPromise:true});
  if(result.result.value!==403)throw new Error('Child cookie escaped its instance');
}
ws.close();
'''
                result = subprocess.run([node, '--input-type=module', '-e', script], env=env,
                    capture_output=True, text=True, timeout=25)
                assert result.returncode == 0, result.stderr + '\nHTTP (path, status, cookie present): ' + repr(requests)
                assert (mount + '/', 200, True) in requests, requests
                assert not list(root.glob('**/accepted.json'))
                assert not (root / 'pause.request').exists()
        finally:
            if browser is not None:
                try: os.killpg(browser.pid, signal.SIGTERM)
                except ProcessLookupError: pass
                try: browser.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(browser.pid, signal.SIGKILL); browser.wait(timeout=3)
            outside.shutdown(); outside.server_close(); outside_thread.join(5)
            if servers: servers[0].shutdown(); servers[0].server_close()
            thread.join(5)
    assert not thread.is_alive() and not outside_thread.is_alive() and not errors
