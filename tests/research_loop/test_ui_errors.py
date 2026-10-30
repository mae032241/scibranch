"""Public errors explain repairs without exposing private exception payloads."""
import html
import http.client
import json
from pathlib import Path
import re
import stat
import threading
from urllib.parse import urlencode

import pytest

from scidiscovery.browser_access import BrowserAccessDenied
from scidiscovery.plugin_configuration import PluginConfigurationError
from scidiscovery.research_loop import instance_ui, ui_errors
from test_execution_authority import prepare
from test_instance_ui import form, post, session
from test_workspace_ui import operator_key


def test_known_failures_have_status_field_and_repair_without_raw_exception(tmp_path):
    error = FileNotFoundError(2, 'missing private file', '/private/native/token-secret')
    problem = ui_errors.present(error, tmp_path, context='load-config')
    assert problem.status == 404 and problem.title == '配置文件不存在'
    assert problem.field == 'config_path' and problem.code == 'file_missing'
    assert problem.href == '/new' and '检查' in problem.hint
    assert 'private/native' not in problem.markup()
    auth = ui_errors.present(BrowserAccessDenied('private session diagnostic'), tmp_path)
    assert auth.status == 403 and 'management_url' in auth.hint and 'Cookie' in auth.hint
    assert 'private session' not in auth.markup()
    malformed = ui_errors.present(ValueError('Control form contains missing or duplicate fields'), tmp_path)
    assert malformed.status == 400 and malformed.code == 'invalid_request'


def test_plugin_error_contract_is_preserved_and_escaped(tmp_path):
    error = PluginConfigurationError('plugin_config_missing', '找不到 <config> 文件。',
        field_path='executor.config.config_file', hint='请选择 JSON 或 TOML 文件。', plugin_id='fixture')
    problem = ui_errors.present(error, tmp_path, context='configuration')
    assert problem.status == 422 and problem.code == error.code
    assert problem.field == error.field_path and problem.cause == error.public_message
    assert '&lt;config&gt;' in problem.markup() and '<config>' not in problem.markup()


def test_unknown_error_has_private_bounded_durable_diagnostic_and_public_reference(tmp_path, monkeypatch):
    secret = 'private-unlabelled-fixture-token'
    error = LookupError(secret + '\n' + 'trace detail ' * 20000)
    monkeypatch.setattr(ui_errors, '_MAX_RECORDS', 3)
    problems = [ui_errors.present(error, tmp_path, record_diagnostic=True) for _ in range(5)]
    assert len({problem.reference for problem in problems}) == 5
    records = list((tmp_path / '.scid-ui/errors').glob('*.json'))
    assert len(records) == 3
    for problem in problems:
        assert problem.status == 500 and problem.code == 'internal_error'
        assert re.fullmatch(r'[0-9a-f]{32}', problem.reference)
        assert secret not in problem.markup() and secret not in json.dumps(problem.payload())
        assert '.scid-ui/errors/' + problem.reference + '.json' in problem.hint
    assert stat.S_IMODE(records[0].parent.stat().st_mode) == 0o700
    for path in records:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and path.stat().st_size <= ui_errors._MAX_BYTES
        record = json.loads(path.read_text())
        assert record['reference'] == path.stem and record['exception'] == 'LookupError'
        assert secret in record['traceback']


def test_diagnostic_failure_cannot_reenter_error_handler(tmp_path):
    (tmp_path / '.scid-ui').write_text('not a directory')
    problem = ui_errors.present(AssertionError('private diagnostic'), tmp_path, record_diagnostic=True)
    assert problem.status == 500 and problem.reference and '诊断未能保存' in problem.hint
    assert 'private diagnostic' not in problem.markup()


def test_load_missing_configuration_preserves_path_and_new_form_nonce(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui = instance_ui.InstanceUI(workspace, secret_file=operator_key(tmp_path), workspace=True)
    ui.host = '127.0.0.1:18753'
    _, _, headers = ui.get('/manage?token=' + ui.management_token, host=ui.host)
    cookie = headers['Set-Cookie']
    token = ui._browser(cookie)
    nonce = ui._form_nonce(token, 'load-config')
    chosen = str(workspace / 'missing "quoted".toml')
    status, page, _ = post(ui, cookie, 'load-config', dict(nonce=nonce,
        csrf=ui._csrf(token, 'load-config', nonce), config_path=chosen))
    assert status == 404 and '配置文件不存在'.encode() in page
    assert ('value="' + html.escape(chosen, quote=True) + '"').encode() in page
    assert all(row[0] != nonce for row in ui.form_nonces.values()) and not (workspace / 'settings.json').exists()
    assert b'<details class="setup-tools" open>' in page
    ui.translation.close()


def test_budget_parse_failure_retains_all_fields_and_does_not_submit(tmp_path, monkeypatch):
    from scidiscovery.research_loop import budget
    from test_configuration_form import Controls
    from test_history_handoff import finish
    root, key = prepare(tmp_path, enabled=False)
    from scidiscovery.research_loop import contracts as c
    original_write = c.write
    def fixture_write(path, value, *args, **kwargs):
        if Path(path).name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return original_write(path, value, *args, **kwargs)
    with monkeypatch.context() as fixture:
        fixture.setattr(c, 'write', fixture_write)
        finish(root, 1, 'H', [], action='analyze')
    budget.amend(root, max_tokens=1, authorization_note='Exercise exhausted-budget recovery form.')
    ui, cookie = session(root, key)
    monkeypatch.setattr(instance_ui.control, 'request_budget_amendment', lambda *a, **k: pytest.fail('invalid budget submitted'))
    recovery = ui.get('/budget-recovery', host=ui.host, cookie=cookie)[1].decode()
    markup = re.search(r'<form method="post" action="/control/budget">(.*?)</form>', recovery, re.S).group(1)
    fields = dict(Controls(markup).fields, max_tokens='abc', max_seconds='120', max_steps='',
        authorization_note='Keep this explanation.', authorization_statement='Keep this statement.')
    def originals():
        paths = [path for path in root.iterdir() if path.is_file()]
        for directory in (root / 'control-ledger', root / 'host-control/operations'):
            if directory.exists():
                paths.extend(path for path in directory.iterdir() if path.is_file())
        return {path: path.read_bytes() for path in paths}
    before = originals()
    nonce = fields['nonce']
    status, page, _ = post(ui, cookie, 'budget', fields)
    assert status == 422 and 'tokens 上限无效'.encode() in page
    echoed = Controls(page.decode()).fields
    assert all(echoed[field] == fields[field] for field in (
        'max_tokens', 'max_seconds', 'max_steps', 'authorization_note', 'authorization_statement'))
    assert b'name="csrf"' in page and b'name="nonce"' in page
    assert echoed['nonce'] != nonce and originals() == before
    ui.translation.close()


@pytest.mark.parametrize('receipt', [dict(status='failed', error='private controller error'),
    dict(status='applied', result=dict(status='native_outcome_unsealed', reason='private backend reason'))])
def test_failed_control_receipt_is_failure_not_redirect_success(tmp_path, monkeypatch, receipt):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    monkeypatch.setattr(instance_ui.control, 'start_background', lambda *a, **k: receipt)
    status, page, headers = post(ui, cookie, 'start', form(ui, cookie, 'start'))
    assert status == 409 and '研究控制请求失败'.encode() in page
    assert '错误编号'.encode() in page and b'/operations' in page
    assert b'private' not in page and 'Location' not in headers
    assert b'action="/control/start"' not in page
    ui.translation.close()


def test_operation_rows_hide_arbitrary_backend_context_and_explain_stopped_receipts(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, _ = session(root, key)
    rows = [dict(operation_id='a' * 32, kind='controller', status='failed', error='unlabelled private backend context'),
        dict(kind='controller', status='applied', result=dict(status='native_outcome_unsealed'))]
    page = ui._operation_rows(rows)
    assert 'unlabelled private' not in page and '错误编号' not in page
    assert '请求编号：<code>' + 'a' * 32 + '</code>' in page
    assert '研究推进已停止，原任务待核对' in page and '已应用' not in page
    ui.translation.close()


def test_translation_parse_error_preserves_settings_without_provider_call(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    fields = dict(nonce=ui.translation_nonce, csrf=ui._csrf(token, 'translation-configure', ui.translation_nonce),
        enabled='true', backend='codex', model='chosen-model', target_language='ja', timeout_seconds='abc')
    monkeypatch.setattr(ui.translation, 'configure', lambda *a, **k: pytest.fail('invalid translation submitted'))
    status, page, _ = ui.post('/translation/configure', host=ui.host, origin='http://' + ui.host,
        cookie=cookie, content_type='application/x-www-form-urlencoded', body=urlencode(fields).encode())
    assert status == 422 and '翻译超时设置无效'.encode() in page
    assert b'value="abc"' in page and b'value="ja"' in page and b'value="chosen-model"' in page
    assert b'name="csrf"' in page and '<details open><summary>高级设置'.encode() in page
    ui.translation.close()


def test_handler_recovery_does_not_reread_damaged_settings_or_expose_traceback(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, _ = session(root, key)
    monkeypatch.setattr(instance_ui.display, 'settings', lambda *a: (_ for _ in ()).throw(AssertionError('recursive lookup')))
    status, page, headers = ui._error_response(AssertionError('private traceback and token'), '/bad')
    assert status == 500 and '工作台内部错误'.encode() in page and '错误编号'.encode() not in page
    assert b'private traceback' not in page and b'recursive lookup' not in page and b'<script' not in page
    status, value, headers = ui._error_response(BrowserAccessDenied('expired'), '/translation/lookup', json_response=True)
    error = json.loads(value)['error']
    assert status == 403 and headers['Content-Type'].startswith('application/json')
    assert error['code'] == 'browser_session_invalid' and 'management_url' in error['hint']
    ui.translation.close()


@pytest.mark.process_e2e
def test_real_handler_keeps_unknown_failures_private_and_sends_json_reason(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui = instance_ui.InstanceUI(root, secret_file=key)
    ready = threading.Event(); servers = []; errors = []
    server_type = instance_ui.ThreadingHTTPServer

    class Server(server_type):
        def serve_forever(self, **options):
            servers.append(self); ready.set(); super().serve_forever(**options)

    monkeypatch.setattr(instance_ui, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(instance_ui, 'InstanceUI', lambda *a, **k: ui)
    monkeypatch.setattr(instance_ui, 'print', lambda *a, **k: None, raising=False)
    original_get = ui.get

    def broken_get(target, **options):
        if target == '/translation/state':
            raise AssertionError('unlabelled private diagnostic')
        return original_get(target, **options)

    monkeypatch.setattr(ui, 'get', broken_get)

    def serve():
        try:
            instance_ui.serve(root, secret_file=key)
        except BaseException as error:
            errors.append(error); ready.set()

    worker = threading.Thread(target=serve); worker.start(); connection = None
    try:
        assert ready.wait(5) and not errors
        connection = http.client.HTTPConnection('127.0.0.1', servers[0].server_port, timeout=5)
        connection.request('GET', '/translation/state')
        response = connection.getresponse(); payload = response.read()
        assert response.status == 500 and response.getheader('Content-Type').startswith('application/json')
        problem = json.loads(payload)['error']
        assert problem['code'] == 'internal_error' and not problem['reference']
        assert b'unlabelled private' not in payload and b'Traceback' not in payload
        assert response.getheader('Cache-Control') == 'no-store'
        assert "default-src 'none'" in response.getheader('Content-Security-Policy')
        connection.request('GET', '/session')
        response = connection.getresponse(); problem = json.loads(response.read())['error']
        assert response.status == 403 and problem['code'] == 'browser_session_invalid'
        connection.request('POST', '/translation/lookup', body='{}', headers={
            'Content-Type': 'application/json', 'Origin': 'http://' + ui.host})
        response = connection.getresponse(); problem = json.loads(response.read())['error']
        assert response.status == 403 and problem['code'] == 'browser_session_invalid'
        assert 'management_url' in problem['hint']
        connection.request('GET', '/manage?token=' + ui.management_token)
        response = connection.getresponse(); response.read()
        cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        monkeypatch.setattr(instance_ui.display, 'settings', lambda *a: pytest.fail('session probe read instance settings'))
        connection.request('GET', '/session', headers={'Cookie': cookie})
        response = connection.getresponse(); payload = response.read()
        assert response.status == 200 and json.loads(payload) == {'authenticated': True}
        connection.request('GET', '/session?token=forbidden', headers={'Cookie': cookie})
        response = connection.getresponse(); problem = json.loads(response.read())['error']
        assert response.status == 400 and problem['code'] == 'invalid_request'
        connection.request('POST', '/control/start', body='missing-fields=true', headers={
            'Cookie': cookie, 'Content-Type': 'application/x-www-form-urlencoded', 'Origin': 'http://' + ui.host})
        response = connection.getresponse(); payload = response.read()
        assert response.status == 400 and '请求格式有误'.encode() in payload
    finally:
        if connection:
            connection.close()
        if servers:
            servers[0].shutdown()
        worker.join(5)
    assert not worker.is_alive() and not errors


def test_get_and_sse_failed_operation_projection_are_pure_and_stable(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    from scidiscovery.research_loop import contracts as c
    operation = root / 'host-control/operations' / ('a' * 32 + '.json')
    c.write(operation, dict(operation_id='a' * 32, sequence=1, kind='controller', status='failed', error='private arbitrary error'))
    token = ui._browser(cookie)
    originals = [root / 'settings.json', operation]
    before = {path: path.read_bytes() for path in originals}
    first = ui.get('/operations', host=ui.host, cookie=cookie)[1]
    assert first == ui.get('/operations', host=ui.host, cookie=cookie)[1]
    one = ui._live_snapshot(token, '', force=True)['fragments']['live-operations']
    two = ui._live_snapshot(token, '', force=True)['fragments']['live-operations']
    assert one == two and 'a' * 32 in one and 'private arbitrary' not in one
    assert before == {path: path.read_bytes() for path in originals}
    operation.write_text('{')
    assert ui._snapshot_or_error(force=True) == ui._snapshot_or_error(force=True)
    assert not (root / '.scid-ui/errors').exists()
    ui.translation.close()


@pytest.mark.parametrize('workspace', [False, True])
def test_unknown_routes_are_404_before_workspace_or_approval_capability(tmp_path, workspace):
    if workspace:
        root = tmp_path / 'workspace'; root.mkdir()
        key = operator_key(tmp_path)
        ui = instance_ui.InstanceUI(root, secret_file=key, workspace=True); ui.host = '127.0.0.1:18753'
        cookie = ui.get('/manage?token=' + ui.management_token, host=ui.host)[2]['Set-Cookie']
    else:
        root, key = prepare(tmp_path, enabled=False)
        ui, cookie = session(root, key)
    for path in ('/not-a-route', '/unknown/favicon'):
        try:
            ui.get(path, host=ui.host, cookie=cookie)
        except Exception as error:
            assert ui._error_response(error, path)[0] == 404
        else:
            pytest.fail('unknown route accepted')
    assert ui.get('/favicon.ico', host=ui.host, cookie=cookie)[0] == 204
    assert not (root / '.scid-ui/errors').exists()
    ui.translation.close()
