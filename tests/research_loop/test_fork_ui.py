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
    text = page.decode()
    assert '选择第 200 阶段会重做第 200 阶段' in text
    assert '不会重跑前一阶段' in text and '新分支须填写新的有限预算' in text
    assert '原尝试不删除，已用预算不退还' in text
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


def test_failed_unsealed_current_task_forks_its_exact_inputs_into_paused_child(tmp_path, monkeypatch):
    from scidiscovery.research_loop import contracts as c, controller
    from test_history_handoff import finish
    parent, key = prepare(tmp_path, enabled=False)
    finish(parent, 1, 'H', [], action='analyze')
    # Accept the exact native intent, but never publish an A2 scientific seal.
    with monkeypatch.context() as patch:
        patch.setattr(c, 'seal_result', lambda root, step: None)
        _, _, record = finish(parent, 2, 'A', [1])
    c.write(record / 'native-outcome.json', dict(returncode=1, native_started=False,
        stop_reason='native_failure_event', terminal_observed=False, cleanup='observed_group_cleaned',
        native_failure=dict(category='permanent', codes=['insufficient_quota'], statuses=[429])))
    (record / 'science-output.json').unlink()
    c.write(record / 'automatic-recovery.json', dict(status='stopped', reason='automatic_recovery_permanent_failure'))
    with controller._writer(parent):
        pass
    originals = {path: path.read_bytes() for path in (parent / 'jobs/001.json', parent / 'jobs/002.json',
        parent / 'native-evidence/001/result-identity.json', record / 'native-intent.json', record / 'native-outcome.json')}
    assert not (record / 'result-identity.json').exists()
    ui, cookie = session(parent, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert '放弃本次尝试并分支重做' in page and 'href="/nodes/2/fork"' in page
    child = tmp_path / 'failed-task-branch'
    value = draft(ui, cookie, 'fork-preview', child)
    value['step'] = '2'
    status, page, _ = post(ui, cookie, 'fork-preview', value)
    assert status == 200 and b'/control/fork-create' in page
    assert not child.exists()
    value.update(expected_preview_id=re.search(rb'name="expected_preview_id" value="([^"]+)"', page)[1].decode())
    value.update(control_fields(ui, cookie, 'fork-create'))
    status, page, _ = post(ui, cookie, 'fork-create', value)
    assert status == 200 and '新分支已创建并暂停' in page.decode()
    manifest = c.read(child / 'host-runtime/fork/manifest.json')
    assert manifest['bootstrap']['next_step'] == 2
    assert [row['step'] for row in manifest['prefix_entries']] == [1]
    assert (child / 'pause.request').exists() and not (child / 'native-evidence').exists()
    assert c.read(child / 'host-runtime/fork/receipt.json')['state'] == 'paused'
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (record / 'result-identity.json').exists()


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
    original_create, failures = fork_service.create, []
    def capture_create(*args, **options):
        try:
            return original_create(*args, **options)
        except Exception as error:
            failures.append(dict(kind=type(error).__name__, code=getattr(error, 'code', ''), message=str(error)[:1200]))
            raise
    monkeypatch.setattr(fork_service, 'create', capture_create)
    status, page, _ = post(ui, cookie, 'fork-create', value)
    assert status >= 400 and '核对原操作结果' in page.decode()
    receipt_path = child / 'host-runtime/fork/receipt.json'
    assert receipt_path.exists(), dict(status=status, fork_failures=failures)
    receipt = c.read(receipt_path)
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


def test_ui_private_state_is_excluded_from_fork_inventory_but_unknown_roots_remain_gaps(tmp_path):
    from scidiscovery.research_loop import instance_archive
    root, _ = prepare(tmp_path, enabled=False)
    private = root / '.scid-ui'
    (private / 'errors').mkdir(parents=True)
    (private / 'translation-config.json').write_text('{"launcher_argv":["private-launcher-token"]}')
    (private / 'translations.sqlite3').write_bytes(b'private cached reading text')
    (private / 'errors/diagnostic.json').write_text('private diagnostic')
    unknown = root / '.scid-ui-copy'; unknown.mkdir()
    (unknown / 'SKILL.md').write_text('Unknown ownership is still a gap.')
    rows = list(instance_archive._inventory(root))
    excluded = next(row for row in rows if row['path'] == '.scid-ui')
    assert excluded['kind'] == 'excluded'
    assert excluded['reason'] == 'private_ui_cache_configuration_and_diagnostics'
    assert not any(row['path'].startswith('.scid-ui/') for row in rows)
    gap = next(row for row in rows if row['path'] == '.scid-ui-copy')
    assert gap['kind'] == 'gap' and gap['reason'] == 'unrecognized_framework_ownership_not_traversed'
    assert not any(row['path'].startswith('.scid-ui-copy/') for row in rows)
