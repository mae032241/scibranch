"""Bounded real-browser coverage of installed Skill role selection."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import threading
import tomllib
from urllib.parse import parse_qs

import pytest

from scidiscovery.research_loop import configuration_form as form, instance_render as render, skill_supply


@pytest.mark.process_e2e
def test_headless_browser_skill_role_selection_roundtrips(tmp_path, monkeypatch):
    """Click production controls, submit FormData, and reimport exported TOML."""
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    if chrome is None:
        pytest.skip('No local Chrome executable for the explicit browser case')
    installed = tmp_path / 'installed-skills'
    descriptions = {'alpha': 'Compare exact alternatives.', 'beta': 'Retain evidence limitations.'}
    for name, description in descriptions.items():
        directory = installed / name
        directory.mkdir(parents=True)
        (directory / 'SKILL.md').write_text(
            f'---\nname: {name}\ndescription: {description}\n---\nOffline fixture.\n', encoding='utf-8')
    monkeypatch.setattr(skill_supply, 'source_root', lambda: installed)
    draft = form.imported(dict(version=1,
        research=dict(root='uncreated-instance', public_root='unused-materials', objective_text='Offline form fixture.'),
        budget=dict(max_tokens=1000, max_seconds=120, max_steps=8)), base=tmp_path)
    parsed_documents = []
    exported_documents = []
    server_errors = []
    driver = r'''
document.addEventListener('DOMContentLoaded', async () => {
  const result = document.createElement('output'); result.id = 'browser-result';
  const check = (condition, message) => { if (!condition) throw new Error(message); };
  // Closed details can retain layout rectangles while their contents are not drawn.
  const visible = element => Boolean(element?.checkVisibility());
  const checkbox = (role, name) => document.querySelector(
    'input[type="checkbox"][name^="skill_picker.item.' + role + '."][value="' + name + '"]');
  const selected = role => Array.from(document.querySelectorAll(
    'input[type="checkbox"][name^="skill_picker.item.' + role + '."]:checked')).map(input => input.value).sort();
  const post = async path => {
    const response = await fetch(path, {method:'POST', body:new URLSearchParams(
      new FormData(document.getElementById('research-config-form')))});
    const data = await response.json();
    check(response.ok && !data.error, data.error || 'Form submission failed');
    return data;
  };
  try {
    const picker = document.getElementById('research-skill-picker');
    const fold = picker?.closest('details');
    const summary = fold?.querySelector(':scope > summary');
    check(fold && !fold.open && visible(summary),
      'Research Skills must be collapsed with a visible summary by default');
    const boxes = picker.querySelectorAll('input[type="checkbox"]');
    check(boxes.length === 8, 'Expected two Skills across four roles');
    check(Array.from(boxes).every(input => !visible(input)),
      'Collapsed Research Skills must hide the selection controls');
    summary.click();
    check(fold.open && visible(picker), 'Research Skills did not expand');
    check(Array.from(boxes).every(input => !input.checked && visible(input)),
      'Expanded Skills must be visible and unselected by default');
    check(checkbox('W', 'alpha').closest('tr').querySelector('th').innerText.includes('Compare exact alternatives.'),
      'Alpha description is not visible');
    check(checkbox('E', 'beta').closest('tr').querySelector('th').innerText.includes('Retain evidence limitations.'),
      'Beta description is not visible');
    checkbox('W', 'alpha').click();
    checkbox('E', 'beta').click();
    checkbox('W', 'beta').click();
    checkbox('W', 'beta').click();
    check(document.getElementById('skill-picker-choice-W').value === 'custom' &&
      document.getElementById('skill-picker-choice-E').value === 'custom',
      'Production change handler did not select custom role assignment');
    check(document.getElementById('skill-picker-choice-H').value === 'keep' &&
      document.getElementById('skill-picker-choice-A').value === 'keep', 'Untouched role assignment changed');
    summary.click();
    check(!fold.open, 'Research Skills did not collapse before submission');
    const imported = await post('/roundtrip');
    document.getElementById('research-config-form').innerHTML = imported.markup;
    check(!document.getElementById('research-skill-picker').closest('details').open,
      'Reimported Research Skills must default to collapsed');
    check(JSON.stringify(selected('W')) === '["alpha"]' && JSON.stringify(selected('E')) === '["beta"]',
      'Exported and reimported role selections differ');
    check(selected('H').length === 0 && selected('A').length === 0, 'An unselected role acquired a Skill');
    await post('/verify');
    result.textContent = 'PASSED_SKILL_ROUNDTRIP';
  } catch (error) { result.textContent = 'FAILED_SKILL_ROUNDTRIP: ' + error.message; }
  document.body.append(result);
});
'''
    driver_csp = "'sha256-" + base64.b64encode(hashlib.sha256(driver.encode()).digest()).decode() + "'"
    page = render.page('Offline Skill browser fixture',
        '<form id="research-config-form" method="post" action="/control/configure">'
        + form.render(draft) + '</form><script>' + driver + '</script>')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, content_type, status=200):
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Security-Policy',
                render.CSP.replace('script-src ', 'script-src ' + driver_csp + ' ', 1))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == '/':
                self.reply(page, 'text/html; charset=utf-8')
            elif self.path == '/assets/configuration.js':
                self.reply(form.SCRIPT_SOURCE.encode(), 'text/javascript')
            elif self.path == '/assets/translation.js':
                self.reply(render.translation_client.SCRIPT.encode(), 'text/javascript')
            elif self.path == '/assets/style.css':
                self.reply(render.CSS.encode(), 'text/css')
            else:
                self.reply(b'{}', 'application/json', 404)

        def do_POST(self):
            try:
                if self.path not in {'/roundtrip', '/verify'}:
                    raise ValueError('Unexpected fixture request')
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2_000_000:
                    raise ValueError('Fixture form exceeds its bounded body limit')
                fields = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True, max_num_fields=4000)
                parsed = form.parse(fields, base=tmp_path)
                parsed_documents.append(form.declaration(parsed))
                if self.path == '/roundtrip':
                    exported = tomllib.loads(form.export_toml(parsed))
                    exported_documents.append(exported)
                    payload = dict(markup=form.render(form.imported(exported, base=tmp_path)))
                else:
                    payload = dict(ok=True)
                self.reply(json.dumps(payload).encode(), 'application/json')
            except Exception as error:
                server_errors.append(repr(error))
                self.reply(json.dumps(dict(error=str(error))).encode(), 'application/json', 500)

    with tempfile.TemporaryDirectory(prefix='scid-skill-browser-', dir='/tmp') as browser_directory, \
            ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        browser_base = Path(browser_directory)
        browser_env = dict(os.environ)
        for variable, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                ('XDG_STATE_HOME', 'state'), ('XDG_DATA_HOME', 'data'), ('XDG_RUNTIME_DIR', 'runtime'),
                ('TMPDIR', 'tmp')):
            directory = browser_base / name
            directory.mkdir(mode=0o700)
            browser_env[variable] = str(directory)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        command = [chrome, '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--disable-background-networking', '--disable-component-update', '--disable-default-apps',
            '--disable-extensions', '--no-first-run', '--no-default-browser-check',
            '--renderer-process-limit=1', '--disable-features=BackForwardCache',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
            '--user-data-dir=' + str(browser_base / 'profile'), '--virtual-time-budget=5000', '--dump-dom',
            f'http://127.0.0.1:{server.server_port}/']
        process = None
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=browser_env, start_new_session=True)
            output, error = process.communicate(timeout=20)
            assert process.returncode == 0, error[-2000:].decode(errors='replace')
            assert not server_errors, server_errors
            assert b'<output id="browser-result">PASSED_SKILL_ROUNDTRIP</output>' in output, \
                output[-6000:].decode(errors='replace')
        finally:
            if process is not None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                process.stdout.close()
                process.stderr.close()
            server.shutdown()
            thread.join(3)

    assert len(parsed_documents) == 2
    assert len(exported_documents) == 1
    for document in [*parsed_documents, *exported_documents]:
        roles = document['agent']['roles']
        assert set(roles) == {'W', 'E'}
        sets = document['skills']['sets']
        assert sets[roles['W']['skill_set']]['items'] == ['alpha']
        assert sets[roles['E']['skill_set']]['items'] == ['beta']
        assert len(sets) == 2


@pytest.mark.process_e2e
def test_workspace_skill_picker_preview_create_freezes_role_selection(tmp_path):
    """Send picker controls through the actual workspace configure/create routes."""
    from scidiscovery.research_loop import contracts
    from test_workspace_ui import controls, form as action_fields, host, login, operator_key, request

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    public = workspace / 'public'
    public.mkdir()
    created = workspace / 'selected-instance'
    objective = 'Prepare one explicitly selected research Skill without starting research.'
    key = operator_key(tmp_path)
    catalog = skill_supply.catalog()
    skill_index = next(index for index, item in enumerate(catalog['items']) if item['name'] == 'curve-analysis')

    with host(workspace, key, workspace=True) as (connection, url, _):
        cookie = login(connection, url)
        origin = 'http://' + url.netloc
        status, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        assert status == 200
        fields = controls(page)
        checkbox_name = f'skill_picker.item.W.{skill_index}'
        assert f'name="{checkbox_name}" value="curve-analysis"'.encode() in page
        assert not any(name.startswith('skill_picker.item.') for name in fields)
        assert fields['skill_picker.choice.W'] == 'keep'
        fields.update({'task_text': objective,
            'cfg.research.root': str(created), 'cfg.research.public_root': str(public),
            'cfg.agent.launcher': 'codex', 'cfg.budget.max_tokens': '1000',
            'cfg.budget.max_seconds': '120', 'cfg.budget.max_steps': '8',
            checkbox_name: 'curve-analysis'})
        status, preview, _ = request(connection, 'POST', '/control/configure',
            cookie=cookie, fields=fields, origin=origin)
        assert status == 200, preview.decode(errors='replace')
        assert b'action="/control/create"' in preview
        assert not created.exists()
        status, body, headers = request(connection, 'POST', '/control/create', cookie=cookie,
            fields=action_fields(preview, 'create'), origin=origin)
        assert status == 303, body.decode(errors='replace')
        assert headers['Location'].startswith('/instances/1/manage?token=')

    settings = contracts.settings(created)
    assert settings['objective'] == objective
    role_profiles = settings['role_profiles']
    sets = settings['skills']['sets']
    assert sets[role_profiles['W']['skill_set']]['items'] == ['curve-analysis']
    for role in 'HAE':
        assert role_profiles.get(role, {}).get('skill_set') is None
    assert not (created / 'jobs/001.json').exists()
    assert not (created / 'native-evidence/001/native-intent.json').exists()
