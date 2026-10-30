"""Saved native endpoints explain manual collection without granting replay."""
import html
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
from urllib.parse import urlsplit

import pytest

from scidiscovery.research_loop import contracts as c, ui_saved_status, ui_view
from test_execution_authority import prepare
from test_instance_ui import session


def completed_node(**changes):
    return dict(dict(step=4, role='W', state='native_outcome_unsealed', continuations=0,
        native_started=True, native_backend='codex',
        returncode=0, terminal_observed=True, cleanup='observed_group_cleaned',
        stop_reason=None, native_failure=None), **changes)


def summary(node):
    presentation = dict(current=node, state=node['state'], completed=False, waiting=False, active=False)
    return dict(ui_view.status_summary(presentation, dict(paused=False)), display=presentation)


def snapshot(node):
    return dict(view=dict(nodes=[node], paused=False, correction={}, closeout=None), ui={})


def test_saved_effective_endpoint_is_read_only_cached_and_invalidated(tmp_path, monkeypatch):
    record = tmp_path / 'native-evidence/004'
    c.write(record / 'native-outcome.json', completed_node(stop_reason='native_failure_event',
        native_failure=dict(category='unknown')))
    c.write(record / 'automatic-recovery.json', dict(status='stopped', reason='automatic_recovery_unknown'))
    stream = record / 'native.jsonl'
    stream.write_text('original stream')
    calls = []

    def effective(path):
        calls.append(path)
        return completed_node()

    monkeypatch.setattr(c, 'native_outcome', effective)
    originals = {path: path.read_bytes() for path in (
        stream, record / 'native-outcome.json', record / 'automatic-recovery.json')}
    memo = {}
    first = ui_saved_status._node(tmp_path, 4, dict(role='W'), memo)
    assert ui_view.native_completed(first)
    assert first['state'] == 'native_outcome_unsealed' and 'automatic_recovery' not in first
    assert ui_saved_status._node(tmp_path, 4, dict(role='W'), memo) == first
    assert calls == [record]
    assert originals == {path: path.read_bytes() for path in originals}
    stream.write_text('changed original stream')
    ui_saved_status._node(tmp_path, 4, dict(role='W'), memo)
    assert calls == [record, record]
    for name in ('usage.json', 'science-output.json', 'result.pending.json'):
        (record / name).write_text('{}')
        ui_saved_status._node(tmp_path, 4, dict(role='W'), memo)
    assert calls == [record] * 5


def test_missing_native_source_does_not_upgrade_old_failure(tmp_path, monkeypatch):
    record = tmp_path / 'native-evidence/004'
    c.write(record / 'native-outcome.json', completed_node(stop_reason='native_failure_event',
        native_failure=dict(category='unknown')))
    monkeypatch.setattr(c, 'native_outcome', lambda path: pytest.fail('missing source cannot prove completion'))
    node = ui_saved_status._node(tmp_path, 4, dict(role='W'), {})
    assert not ui_view.native_completed(node)
    assert node['native_failure']['category'] == 'unknown'


def retained_reconnect(record, *, step=4, role='W'):
    from scidiscovery.research_loop import backends, native_recovery
    meter = dict(input_tokens=2, cached_input_tokens=0, output_tokens=1)
    error = dict(type='error', message='Reconnecting 1/5')
    science = '{"summary":"retained original"}'
    events = [dict(type='thread.started', thread_id='original-ui-thread'), error,
        dict(type='item.completed', item=dict(type='agent_message', text=science)),
        dict(type='turn.completed', usage=meter)]
    backend = backends.selected(dict(native_launcher_argv=['codex']))
    c.write(record / 'native-intent.json', {})
    c.write(record / 'usage.json', meter)
    (record / 'native.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
    (record / 'result.pending.json').write_text(science)
    c.write(record / 'native-outcome.json', completed_node(step=step, role=role, stop_reason='native_failure_event',
        thread_id='original-ui-thread', native_failure=native_recovery.classify(error),
        native_activity_observed=any(backend.event(event).get('activity', False) for event in events),
        metering=dict(tokens=meter, coverage='unknown', live_reason=None)))


def test_actual_reconnect_endpoint_and_usage_have_consistent_saved_display(tmp_path):
    record = tmp_path / 'native-evidence/004'
    retained_reconnect(record)
    memo = {}
    node = ui_saved_status._node(tmp_path, 4, dict(role='W'), memo)
    assert summary(node)['headline'] == '原生已完成，待收集封存'
    usage = ui_saved_status._usage(tmp_path, [4], memo)
    assert usage['unknown_steps'] == [] and usage['observed_tokens'] == 3
    assert c.read(record / 'native-outcome.json')['stop_reason'] == 'native_failure_event'


def test_failure_projection_keeps_known_codes_without_private_native_fields(tmp_path):
    record = tmp_path / 'native-evidence/004'
    c.write(record / 'native-outcome.json', completed_node(returncode=1, terminal_observed=False,
        stop_reason='native_failure_event', native_failure=dict(category='permanent',
            codes=['invalid_api_key', 'private-provider-token'], statuses=[401, 'private-status'],
            message='private-provider-message', path='/private/native/path')))
    node = ui_saved_status._node(tmp_path, 4, dict(role='W'))
    facts = ui_view.native_facts(node, lambda value, limit=500: value[:limit])
    text = str(facts)
    assert 'invalid_api_key' in text and '401' in text and '原生退出码' in text
    assert '永久服务错误' in text and 'private' not in str(node['native_failure'])
    assert not ui_view.native_completed(node)


def test_manager_collects_exact_original_through_existing_resume_form(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    node = completed_node()
    status = summary(node)
    assert status['headline'] == '原生已完成，待收集封存'
    form = ui._primary(token, True, snapshot(node), status)
    assert 'action="/control/resume"' in form and 'name="step" value="4"' in form
    assert 'name="csrf"' in form and 'name="nonce"' in form
    assert '收集封存并继续研究' in form
    notices = ui._notices(snapshot(node), dict(kind='controller', status='applied',
        result=dict(status='native_outcome_unsealed', reason='private stale backend reason')))
    assert '当前原生已完成，待收集封存' in notices[0]
    assert 'private' not in str(notices) and '尚未核实' not in notices[0]
    details = str(ui._attention(token, True, snapshot(node), status))
    command = 'scid-research worker-resume --root ' + shlex.quote(str(root)) + ' --step 4'
    assert html.escape(command, quote=True) in details
    assert 'native-evidence/004/native-outcome.json' in details
    public = str(ui._attention(token, False, snapshot(node), status))
    assert 'worker-resume' not in public and 'native-outcome.json' not in public
    assert '/control/resume' not in ui._primary(token, False, snapshot(node), status)
    ui.translation.close()


@pytest.mark.parametrize('changes', [dict(returncode=1), dict(terminal_observed=False),
    dict(cleanup='unknown'), dict(stop_reason='native_failure_event'),
    dict(native_failure=dict(category='unknown')), dict(state='unknown'), dict(state='waiting'),
    dict(returncode=False)])
def test_unverified_or_failed_endpoint_does_not_offer_collection(tmp_path, changes):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    node = completed_node(**changes)
    status = summary(node)
    assert not ui_view.native_completed(node)
    assert '/control/resume' not in (ui._primary(ui._browser(cookie), True, snapshot(node), status) or '')
    assert status['headline'] != '原生已完成，待收集封存'
    ui.translation.close()


def test_operation_failure_has_exact_local_receipt_without_private_error(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, _ = session(root, key)
    page = ui._operation_rows([dict(kind='controller', status='applied', operation_id='a' * 32,
        result=dict(status='output_repair_unavailable', attempt_step=4, reason='private backend reason'))])
    assert 'output_repair_unavailable' in page and '原任务阶段：4' in page
    assert 'host-control/operations/' + 'a' * 32 + '.json' in page
    assert 'private backend reason' not in page
    ui.translation.close()


@pytest.mark.parametrize('state', sorted(ui_view.SEALED))
@pytest.mark.parametrize('status', ['native_stopped', 'native_outcome_unsealed', 'closeout_native_unsealed'])
def test_historical_stop_notice_yields_to_current_seal(tmp_path, state, status):
    root, key = prepare(tmp_path, enabled=False)
    ui, _ = session(root, key)
    node = completed_node(state=state, stop_reason='native_failure_event')
    notices = ui._notices(snapshot(node), dict(kind='controller', status='applied', result=dict(
        status=status, reason='Original execution lost its verified completion; preserved without replay')))
    assert len(notices) == 1 and '当前阶段结论已封存' in notices[0]
    assert '尚未封存' not in notices[0] and '尚未核实' not in notices[0] and '工作停止说明' not in notices[0]
    ui.translation.close()


@pytest.mark.process_e2e
def test_chrome_manager_renders_existing_collect_form_without_submitting(tmp_path):
    from test_workspace_ui import host, login, request
    chrome = Path('/usr/bin/google-chrome')
    if not chrome.is_file():
        pytest.skip('Headless Chrome is unavailable')
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = root / 'native-evidence/001'
    retained_reconnect(record, step=1, role='H')
    originals = {path: path.read_bytes() for path in (root / 'settings.json', root / 'jobs/001.json',
        record / 'native-intent.json', record / 'native-outcome.json', record / 'native.jsonl',
        record / 'usage.json', record / 'result.pending.json')}
    # Chrome's singleton Unix socket must fit the platform's short path limit;
    # the outer process guard may deliberately supply a much longer TMPDIR.
    with host(root, key, workspace=False) as (connection, url, endpoint), \
            tempfile.TemporaryDirectory(prefix='scid-ui-chrome-', dir='/tmp') as browser_directory:
        browser_root = Path(browser_directory)
        cookie = login(connection, url)
        status, page, _ = request(connection, 'GET', '/', cookie=cookie)
        assert status == 200 and b'action="/control/resume"' in page
        command = [str(chrome), '--headless', '--no-sandbox', '--disable-gpu',
            '--renderer-process-limit=1', '--disable-dev-shm-usage',
            '--disable-background-networking', '--disable-component-update', '--disable-sync',
            '--disable-extensions', '--no-first-run', '--no-default-browser-check',
            '--js-flags=--max-old-space-size=128', '--timeout=8000',
            '--user-data-dir=' + str(browser_root / 'profile'), '--dump-dom', endpoint['management_url']]
        browser_env = dict(os.environ, TMPDIR=browser_directory)
        for name in ('XDG_CONFIG_HOME', 'XDG_CACHE_HOME', 'XDG_DATA_HOME'):
            browser_env[name] = str(browser_root / name.lower())
        log_directory = Path(os.environ.get('SCID_TEST_LOG_DIR', str(tmp_path)))
        log_directory.mkdir(parents=True, exist_ok=True)
        diagnostic = log_directory / 'actionable-ui-chrome.stderr.log'

        def save_diagnostic(stderr, status):
            raw = stderr.encode('utf-8') if isinstance(stderr, str) else (stderr or b'')
            diagnostic.write_bytes(('exit_code=' + str(status) + '\n').encode() + raw[:128 * 1024]
                + (b'\n[stderr truncated at 128 KiB]\n' if len(raw) > 128 * 1024 else b''))

        try:
            # Initial server-rendered controls need no virtual clock. The page's
            # live EventSource intentionally retains a pending network request.
            result = subprocess.run(command, capture_output=True, text=True, errors='replace', timeout=15,
                env=browser_env)
        except subprocess.TimeoutExpired as error:
            save_diagnostic(error.stderr, 'timeout_after_15_seconds')
            raise AssertionError('Headless Chrome exceeded its smoke-test time budget; diagnostic: '
                + str(diagnostic)) from None
        save_diagnostic(result.stderr, result.returncode)
        assert result.returncode == 0, 'Headless Chrome failed; startup diagnostic: ' + str(diagnostic)
        dom = result.stdout
        assert '原生已完成，待收集封存' in dom and '收集封存并继续研究' in dom
        assert 'action="/control/resume"' in dom and 'name="step" value="1"' in dom
        cli = 'scid-research worker-resume --root ' + shlex.quote(str(root)) + ' --step 1'
        assert html.escape(cli, quote=True) in dom
        reader = login(connection, urlsplit(endpoint['read_url']))
        status, readonly, _ = request(connection, 'GET', '/', cookie=reader)
        assert status == 200 and b'action="/control/resume"' not in readonly
    assert originals == {path: path.read_bytes() for path in originals}
    operations = root / 'host-control/operations'
    assert not operations.exists() or not any(operations.iterdir())
    assert not (record / 'runner-process.json').exists() and not (record / 'result-identity.json').exists()
