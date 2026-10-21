"""Maintenance/CSRF fork forms preserve drafts and never start new work."""
import html
import re
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import fork_service
from test_execution_authority import prepare
from test_instance_ui import session, control_fields, post


def draft(ui, cookie, action, child):
    return dict(control_fields(ui, cookie, action), step='200', child_root=str(child), max_tokens='100',
        max_seconds='20', max_steps='2', guidance_text='  Exact guidance\n\n', operation_id='one', expected_preview_id='')


def test_fork_page_requires_maintenance_and_never_creates_from_get(tmp_path):
    parent, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(parent, key)
    status, page, _ = ui.get('/nodes/200/fork', host=ui.host, cookie=cookie)
    assert status == 200 and b'/control/fork-preview' in page
    assert b'/control/fork-create' not in page
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    with pytest.raises(PermissionError):
        ui.get('/nodes/200/fork', host=ui.host, cookie=headers['Set-Cookie'])


def test_ui_uses_same_service_preserves_original_and_requires_preview(tmp_path, monkeypatch):
    parent, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(parent, key)
    child = tmp_path / 'branch'
    calls = []
    def preview(*args, **options):
        calls.append((args, options))
        return dict(status='fork_preview_ready', preview_id='exact-preview', source={}, budget=options['budget'])
    monkeypatch.setattr(fork_service, 'preview', preview)
    value = draft(ui, cookie, 'fork-preview', child)
    status, page, _ = post(ui, cookie, 'fork-preview', value)
    assert status == 200 and b'/control/fork-create' in page
    assert calls == [((parent, 200, child), dict(budget=dict(max_tokens=100, max_seconds=20., max_steps=2), guidance_text='  Exact guidance\n\n'))]
    assert b'value="exact-preview"' in page
    assert not child.exists()
    with pytest.raises(PermissionError, match='CSRF'):
        post(ui, cookie, 'fork-preview', value)


def test_ui_fork_error_retains_draft_and_escapes_guidance(tmp_path, monkeypatch):
    parent, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(parent, key)
    def fail(*args, **options):
        raise fork_service.ForkError('fork_preview_stale', 'Preview changed')
    monkeypatch.setattr(fork_service, 'create', fail)
    value = draft(ui, cookie, 'fork-create', tmp_path / 'branch')
    value['guidance_text'] = '  <script>original</script>\n'
    status, page, _ = post(ui, cookie, 'fork-create', value)
    assert status >= 400
    assert b'&lt;script&gt;original&lt;/script&gt;' in page
    assert b'<script>original</script>' not in page
    assert ('value="' + html.escape(value['child_root'], quote=True) + '"').encode() in page
    assert b'/control/fork-preview' in page


def test_real_ui_creates_paused_child_and_opens_explicit_start_page(tmp_path):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import contracts as c
    parent = completed_source(tmp_path)
    ui, cookie = session(parent, tmp_path / 'operator.key')
    child = tmp_path / 'branch'
    value = draft(ui, cookie, 'fork-preview', child)
    value['step'] = '2'
    status, page, _ = post(ui, cookie, 'fork-preview', value)
    assert status == 200
    match = re.search(rb'name="expected_preview_id" value="([^"]+)"', page)
    assert match and match[1]
    value = dict(value, expected_preview_id=match[1].decode(), **control_fields(ui, cookie, 'fork-create'))
    status, page, _ = post(ui, cookie, 'fork-create', value)
    assert status == 200 and '新分支已创建并暂停' in page.decode()
    assert ui.instance_roots[-1] == child and (child / 'pause.request').exists()
    assert not (child / 'native-evidence').exists()
    link = html.unescape(re.search(r'href="([^"]+/manage\?token=[^"]+)"', page.decode())[1])
    status, _, headers = ui.get(link, host=ui.host, cookie=cookie)
    assert status == 200 and 'Set-Cookie' in headers
    assert c.read(child / 'host-runtime/fork/receipt.json')['state'] == 'paused'


def test_web_reconciles_exact_published_request_after_ui_failure_and_parent_removal(tmp_path, monkeypatch):
    import shutil
    from test_fork_source import completed_source
    from scidiscovery.research_loop import contracts as c
    parent = completed_source(tmp_path)
    ui, cookie = session(parent, tmp_path / 'operator.key')
    child = tmp_path / 'branch'
    value = draft(ui, cookie, 'fork-preview', child)
    value['step'] = '2'
    _, page, _ = post(ui, cookie, 'fork-preview', value)
    value.update(expected_preview_id=re.search(rb'name="expected_preview_id" value="([^"]+)"', page)[1].decode())
    value.update(control_fields(ui, cookie, 'fork-create'))
    open_child = ui._child
    def fail_after_publication(index):
        raise RuntimeError('Injected UI initialization failure after publication')
    monkeypatch.setattr(ui, '_child', fail_after_publication)
    status, page, _ = post(ui, cookie, 'fork-create', value)
    assert status >= 400 and '核对原操作结果' in page.decode()
    receipt = c.read(child / 'host-runtime/fork/receipt.json')
    shutil.rmtree(parent)
    def no_source_read(*args, **options):
        pytest.fail('Web receipt reconciliation reread deleted parent')
    monkeypatch.setattr(fork_service.fork_sources, 'inspect_source', no_source_read)
    monkeypatch.setattr(ui, '_child', open_child)
    # Reopening the source node's fork page also recovers a lost POST response.
    status, page, _ = ui.get('/nodes/2/fork', host=ui.host, cookie=cookie)
    assert status == 200
    original = re.search(r'<form method="post" action="/control/fork-create">(.*?)</form>', page.decode(), re.S)[1]
    fields = {html.unescape(key): html.unescape(item) for key, item in
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', original)}
    for key in ('step', 'child_root', 'max_tokens', 'max_seconds', 'max_steps', 'guidance_text', 'operation_id', 'expected_preview_id'):
        assert fields[key] == value[key]
    status, page, _ = post(ui, cookie, 'fork-create', fields)
    assert status == 200 and '新分支已创建并暂停' in page.decode()
    assert c.read(child / 'host-runtime/fork/receipt.json') == receipt
    assert not (child / 'native-evidence').exists()
