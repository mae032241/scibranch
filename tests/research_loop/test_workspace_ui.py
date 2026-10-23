"""Real loopback configuration entry without a research instance or model call."""
from contextlib import contextmanager
import html
import http.client
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
from urllib.parse import urlencode, urlsplit

import pytest

from scidiscovery.research_loop import contracts as c, instance_ui
from test_execution_authority import prepare
from test_research_config import declaration


@contextmanager
def host(root, key, *, workspace):
    source = Path(instance_ui.__file__).resolve().parents[2]
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli', 'serve-ui',
        '--workspace' if workspace else '--root', str(root), '--approval-secret-file', str(key), '--port', '0']
    env = dict(os.environ, PYTHONPATH=str(source))
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    connection = None
    try:
        assert select.select([process.stdout], [], [], 8)[0], 'UI did not announce its endpoint'
        endpoint = json.loads(process.stdout.readline())
        assert endpoint['status'] == 'ui_listening'
        url = urlsplit(endpoint['management_url'])
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        yield connection, url, endpoint
    finally:
        if connection is not None:
            connection.close()
        if process.poll() is None:
            process.terminate()
        process.wait(5)
        process.stdout.close()
        process.stderr.close()


def request(connection, method, path, *, cookie=None, fields=None, origin=None):
    headers = {'Cookie': cookie} if cookie else {}
    body = None
    if fields is not None:
        headers.update({'Content-Type': 'application/x-www-form-urlencoded', 'Origin': origin})
        body = urlencode(fields)
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    return response.status, response.read(), dict(response.getheaders())


def login(connection, url):
    status, _, headers = request(connection, 'GET', url.path + '?' + url.query)
    assert status == 200
    assert headers['Referrer-Policy'] == 'no-referrer'
    return headers['Set-Cookie'].split(';', 1)[0]


def form(page, action):
    original = re.search(r'<form\b[^>]*\baction="/control/' + re.escape(action) + r'"[^>]*>(.*?)</form>',
        page.decode(), re.S).group(1)
    return {html.unescape(key): html.unescape(value) for key, value in
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', original)}


def controls(page, action='configure'):
    """Collect successful HTML controls as a browser submits the form."""
    from html.parser import HTMLParser

    class Form(HTMLParser):
        def __init__(self):
            super().__init__(); self.active = False; self.values = {}; self.current = None; self.parts = []
            self.options = []; self.option = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'form':
                self.active = attrs.get('action') == '/control/' + action
            if not self.active:
                return
            if tag == 'input' and attrs.get('name') and attrs.get('type') not in {'submit', 'button'}:
                if attrs.get('type') not in {'checkbox', 'radio'} or 'checked' in attrs:
                    self.values[attrs['name']] = attrs.get('value', 'on' if attrs.get('type') == 'checkbox' else '')
            if tag in {'textarea', 'select'}:
                self.current = (tag, attrs['name']); self.parts = []; self.options = []
            if tag == 'option':
                self.option = (attrs.get('value', ''), 'selected' in attrs)

        def handle_data(self, value):
            if self.active and self.current:
                self.parts.append(value)

        def handle_endtag(self, tag):
            if self.active and tag == 'option':
                self.options.append(self.option)
            if self.active and self.current and tag == self.current[0]:
                self.values[self.current[1]] = (next((value for value, selected in self.options if selected),
                    self.options[0][0]) if tag == 'select' else ''.join(self.parts))
                self.current = None
            if tag == 'form':
                self.active = False

    parser = Form(); parser.feed(page.decode())
    if action == 'configure': parser.values['mode'] = 'preview'
    return parser.values


def operator_key(tmp_path):
    key = tmp_path / 'operator.key'; key.write_bytes(b'workspace-browser-key-at-least-32bytes'); key.chmod(0o600)
    return key


@pytest.mark.process_e2e
def test_workspace_structured_form_preview_create_and_open_exact_instance(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    config = declaration(workspace)
    original = config.read_bytes()
    (workspace / 'public/objective.md').write_text('Cov task must never silently appear.')
    key = operator_key(tmp_path)
    existing_dir = tmp_path / 'existing'; existing_dir.mkdir()
    existing, _ = prepare(existing_dir, enabled=False)
    frozen = (existing / 'settings.json').read_bytes()
    with host(workspace, key, workspace=True) as (connection, url, endpoint):
        cookie = login(connection, url)
        origin = 'http://' + url.netloc
        _, home, _ = request(connection, 'GET', '/', cookie=cookie)
        assert '研究工作台'.encode() in home and '创建新研究'.encode() in home
        assert b'/control/start' not in home and b'Cov task' not in home
        _, page, headers = request(connection, 'GET', '/new', cookie=cookie)
        assert headers['Referrer-Policy'] == 'same-origin'
        assert b'Cov task' not in page and str(config).encode() not in page
        fields = controls(page)
        assert fields['task_text'] == '' and fields['cfg.budget.max_tokens'] == ''
        assert fields['cfg.budget.max_seconds'] == '' and fields['cfg.budget.max_steps'] == ''
        assert b'config_text' not in page and b'action="/control/create"' not in page
        # A configure token cannot authorize the distinct create action.
        assert request(connection, 'POST', '/control/create', cookie=cookie,
            fields=dict(form(page, 'configure'), selection='unselected'), origin=origin)[0] == 403
        _, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        fields = controls(page)
        fields.update({'task_text': 'Research a new task directly from the form.',
            'cfg.research.root': str(workspace / 'new-instance'), 'cfg.research.public_root': str(workspace / 'public'),
            'cfg.agent.launcher': 'never-launched', 'cfg.agent.model': 'form-model',
            'cfg.budget.max_tokens': '1234', 'cfg.budget.max_seconds': '120', 'cfg.budget.max_steps': '8'})
        assert request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin='null')[0] == 403
        assert request(connection, 'POST', '/control/configure', cookie=cookie,
            fields=dict(fields, csrf='forged'), origin=origin)[0] == 403
        status, invalid, _ = request(connection, 'POST', '/control/configure', cookie=cookie,
            fields=dict(fields, **{'cfg.budget.max_tokens': 'not-a-number'}), origin=origin)
        assert status == 422
        retained = controls(invalid)
        assert retained['task_text'] == fields['task_text'] and retained['cfg.agent.model'] == 'form-model'
        assert retained['cfg.research.root'] == str(workspace / 'new-instance')
        assert retained['cfg.budget.max_tokens'] == 'not-a-number'
        assert b'action="/control/create"' not in invalid
        fields = dict(retained, **{'cfg.budget.max_tokens': '1234'})
        status, page, _ = request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin=origin)
        assert status == 200 and b'Research a new task directly from the form.' in page
        assert b'form-model' in page and '确认创建研究'.encode() in page
        assert not (workspace / 'new-instance').exists()
        status, _, headers = request(connection, 'POST', '/control/create', cookie=cookie,
            fields=form(page, 'create'), origin=origin)
        assert status == 303 and headers['Location'].startswith('/instances/1/manage?token=')
        _, _, headers = request(connection, 'GET', headers['Location'])
        child_cookie = headers['Set-Cookie'].split(';', 1)[0]
        status, page, _ = request(connection, 'GET', '/instances/1/', cookie=child_cookie)
        assert status == 200 and b'action="/instances/1/control/start"' in page
        created = workspace / 'new-instance'
        assert c.settings(created)['objective'] == 'Research a new task directly from the form.'
        assert c.settings(created)['model'] == 'form-model' and c.settings(created)['max_tokens'] == 1234
        assert config.read_bytes() == original and not list(created.glob('jobs/*.json'))
        _, page, _ = request(connection, 'GET', '/', cookie=cookie)
        status, _, headers = request(connection, 'POST', '/control/open', cookie=cookie,
            fields=dict(form(page, 'open'), root=str(existing)), origin=origin)
        assert status == 303 and headers['Location'].startswith('/instances/2/manage?token=')
        _, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        assert controls(page)['task_text'] == ''
        reader = login(connection, urlsplit(endpoint['read_url']))
        assert request(connection, 'GET', '/new', cookie=reader)[0] == 403
        assert request(connection, 'POST', '/control/load-config', cookie=reader,
            fields=dict(form(page, 'load-config'), config_path=str(config)), origin=origin)[0] == 403
    assert (existing / 'settings.json').read_bytes() == frozen
    assert not list(existing.glob('jobs/*.json'))


@pytest.mark.process_e2e
def test_optional_external_config_import_edit_save_and_changed_source_reconfirm(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    declaration(workspace)
    folder = tmp_path / 'other-project'; folder.mkdir()
    config = declaration(folder).rename(folder / 'chosen.toml')
    original = config.read_bytes()
    key = operator_key(tmp_path)
    with host(workspace, key, workspace=True) as (connection, url, endpoint):
        cookie = login(connection, url); origin = 'http://' + url.netloc
        _, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        assert request(connection, 'POST', '/control/load-config', cookie=cookie,
            fields=dict(form(page, 'load-config'), config_path=str(key)), origin=origin)[0] == 422
        _, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        status, page, _ = request(connection, 'POST', '/control/load-config', cookie=cookie,
            fields=dict(form(page, 'load-config'), config_path=str(config)), origin=origin)
        assert status == 200
        fields = controls(page); fields['cfg.budget.max_tokens'] = '2345'
        status, page, _ = request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin=origin)
        assert status == 200 and b'Compare alternatives and retain limitations.' in page
        assert config.read_bytes() == original
        config.write_text(config.read_text().replace('max_seconds = 120', 'max_seconds = 121'))
        status, page, _ = request(connection, 'POST', '/control/create', cookie=cookie,
            fields=form(page, 'create'), origin=origin)
        assert status == 200 and '原配置或任务已变化'.encode() in page
        assert not (folder / 'instance').exists() and b'action="/control/create"' not in page
        fields = controls(page); fields['cfg.budget.max_tokens'] = '2345'
        _, page, _ = request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin=origin)
        before_create = config.read_bytes()
        status, _, headers = request(connection, 'POST', '/control/create', cookie=cookie,
            fields=form(page, 'create'), origin=origin)
        assert status == 303 and c.settings(folder / 'instance')['max_tokens'] == 2345
        assert c.settings(folder / 'instance')['max_seconds'] == 121 and config.read_bytes() == before_create
        assert not (workspace / 'instance').exists()
        _, page, _ = request(connection, 'GET', '/new', cookie=cookie)
        fields = controls(page)
        fields.update({'task_text': 'Saved through form', 'cfg.research.root': str(workspace / 'saved-instance'),
            'cfg.research.public_root': str(workspace / 'public'), 'cfg.agent.launcher': 'never-launched',
            'cfg.budget.max_tokens': '100', 'cfg.budget.max_seconds': '10', 'cfg.budget.max_steps': '2',
            'save_path': str(folder / 'saved.toml'), 'mode': 'save'})
        status, exported, headers = request(connection, 'POST', '/control/configure', cookie=cookie,
            fields=dict(fields, mode='export'), origin=origin)
        assert status == 200 and 'attachment' in headers['Content-Disposition']
        assert b'Saved through form' in exported and not (folder / 'saved.toml').exists()
        # A download leaves the browser's original form usable for saving.
        status, page, _ = request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin=origin)
        assert status == 200 and (folder / 'saved.toml').exists()
        assert '配置已保存'.encode() in page and not (workspace / 'saved-instance').exists()
        saved = (folder / 'saved.toml').read_bytes()
        # The following mutating save consumed the original form once; a second
        # save cannot replay it, and the parsed draft remains available.
        status, retry, _ = request(connection, 'POST', '/control/configure', cookie=cookie, fields=fields, origin=origin)
        assert status == 422 and b'Saved through form' in retry
        assert (folder / 'saved.toml').read_bytes() == saved


def test_workspace_home_and_new_do_not_read_implicit_research_config(tmp_path, monkeypatch):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    config = declaration(workspace)
    config.write_text('Cov invalid research TOML must not be read')
    ui = instance_ui.InstanceUI(workspace, secret_file=operator_key(tmp_path), workspace=True)
    ui.host = '127.0.0.1:18753'
    _, _, headers = ui.get('/manage?token=' + ui.management_token, host=ui.host)
    monkeypatch.setattr(instance_ui.research_config, 'read_declaration',
        lambda *args: pytest.fail('Implicit research config was read'))
    for route in ('/', '/new', '/configuration'):
        status, page, _ = ui.get(route, host=ui.host, cookie=headers['Set-Cookie'])
        assert status == 200 and b'Cov invalid' not in page
    assert not (workspace / 'settings.json').exists()


@pytest.mark.process_e2e
def test_existing_root_cli_http_entry_retains_instance_mode(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    with host(root, key, workspace=False) as (connection, url, endpoint):
        assert endpoint['instance'] == c.settings(root)['goal_id']
        cookie = login(connection, url)
        status, page, _ = request(connection, 'GET', '/', cookie=cookie)
        assert status == 200 and b'action="/control/start"' in page
    assert not list(root.glob('jobs/*.json'))


@pytest.mark.process_e2e
def test_workspace_delete_uses_exact_service_and_reconciles_original_mount_after_lost_response(tmp_path):
    left = tmp_path / 'left'; left.mkdir()
    right = tmp_path / 'right'; right.mkdir()
    root, key = prepare(left, enabled=False)
    sibling, _ = prepare(right, enabled=False)
    sibling_original = (sibling / 'settings.json').read_bytes()
    workspace = tmp_path / 'workbench'; workspace.mkdir()
    with host(workspace, key, workspace=True) as (connection, url, endpoint):
        cookie = login(connection, url)
        origin = f'http://{url.netloc}'
        for selected in (root, sibling):
            _, page, _ = request(connection, 'GET', '/', cookie=cookie)
            values = dict(form(page, 'open'), root=str(selected))
            assert request(connection, 'POST', '/control/open', cookie=cookie, fields=values, origin=origin)[0] == 303
        _, page, _ = request(connection, 'GET', '/', cookie=cookie)
        manage = html.unescape(re.search(r'href="(/instances/1/manage\?token=[^"]+)"', page.decode())[1])
        _, _, headers = request(connection, 'GET', manage)
        child_cookie = headers['Set-Cookie'].split(';', 1)[0]
        _, page, _ = request(connection, 'GET', '/instances/1/delete', cookie=child_cookie)
        # controls() works on unmounted form actions; emulate the browser's
        # namespace here while retaining exactly the issued binding values.
        fields = controls(page.replace(b'action="/instances/1/control/delete"', b'action="/control/delete"'), 'delete')
        assert fields['root'] == str(root) and fields['instance_id'] == c.settings(root)['goal_id']
        original_operation = fields['operation_id']
        fields['confirmation'] = 'DELETE'
        deleted = request(connection, 'POST', '/instances/1/control/delete', cookie=child_cookie, fields=fields, origin=origin)
        assert deleted[0] == 303 and deleted[2]['Location'] == '/instances'
        assert not root.exists() and (sibling / 'settings.json').read_bytes() == sibling_original
        # Treat the response as lost: the original mount still offers exact
        # identity/operation reconciliation without reopening deleted settings.
        _, page, _ = request(connection, 'GET', '/instances/1/delete', cookie=child_cookie)
        retry = controls(page.replace(b'action="/instances/1/control/delete"', b'action="/control/delete"'), 'delete')
        assert retry['operation_id'] == original_operation and retry['root'] == str(root)
        retry['confirmation'] = 'DELETE'
        assert request(connection, 'POST', '/instances/1/control/delete', cookie=child_cookie, fields=retry, origin=origin)[0] == 303
        assert (sibling / 'settings.json').read_bytes() == sibling_original
        _, remaining, _ = request(connection, 'GET', '/', cookie=cookie)
        assert b'/instances/2/manage?token=' in remaining and b'/instances/1/manage?token=' not in remaining
