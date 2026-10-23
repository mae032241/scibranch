"""Public projection and byte reconnection through production CLI/HTTP readers."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, execution, progress
from test_instance_ui import session
from test_execution_authority import prepare


def transport(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    record.mkdir(parents=True, exist_ok=True)
    return root, key, record / 'native.jsonl'


def line(value):
    return (json.dumps(value, ensure_ascii=False) + '\n').encode()


def message(text):
    return dict(type='item.completed', item=dict(type='agent_message', text=text))


def test_public_reader_is_typed_private_cleaned_and_exact_byte_cursor(tmp_path):
    root, _, log = transport(tmp_path)
    raw = b''.join([
        line(dict(type='thread.started', thread_id='private-native-session')),
        line(dict(type='item.completed', item=dict(type='reasoning', text='hidden-reasoning'))),
        line(message(f'测量 {root}/native-homes/001 api_key="credential-bytes" Bearer opaque-value')),
        line(dict(type='item.completed', item=dict(type='mcp_tool_call', tool='experiment',
            arguments={'token': 'hidden-argument'}, result='hidden-output', status='completed'))),
    ])
    log.write_bytes(raw)
    result = progress.read(root, 1)
    assert result['events'][0] == dict(type='session.started')
    assert result['events'][-1] == dict(type='tool', tool='experiment', status='completed')
    public = json.dumps(result, ensure_ascii=False)
    for private in ('private-native-session', 'hidden-reasoning', str(root), 'credential-bytes', 'opaque-value', 'hidden-argument', 'hidden-output'):
        assert private not in public
    decoded = base64.urlsafe_b64decode(result['cursor'] + '=' * (-len(result['cursor']) % 4))
    assert '测量'.encode() not in decoded and b'credential-bytes' not in decoded
    assert progress.read(root, 1, cursor=result['cursor'])['events'] == []
    with log.open('ab') as stream:
        stream.write(line(message('new observed message')))
    next_page = progress.read(root, 1, cursor=result['cursor'])
    assert [row['text'] for row in next_page['events']] == ['new observed message']
    assert next_page['bytes_read'] == len(line(message('new observed message')))
    assert log.read_bytes().startswith(raw)


@pytest.mark.parametrize('oversize_bytes, page_count', [(2560, 3), (3000, 4)])
def test_partial_line_oversize_skip_truncation_and_replacement_are_explicit(tmp_path, oversize_bytes, page_count):
    root, _, log = transport(tmp_path)
    content = line(message('Unicode 数据'))
    log.write_bytes(content[:-2])
    first = progress.read(root, 1)
    assert first['stream_state'] == 'partial_line' and first['events'] == []
    with log.open('ab') as stream:
        stream.write(content[-2:])
    full = progress.read(root, 1, cursor=first['cursor'])
    assert full['events'][0]['text'] == 'Unicode 数据'
    log.write_bytes(b'')
    assert progress.read(root, 1, cursor=full['cursor'])['stream_state'] == 'truncated'
    log.write_bytes(b'x' * oversize_bytes + b'\n' + line(message('after oversized transport')))
    page = progress.read(root, 1, limit_bytes=1024)
    assert page['omitted_oversize_lines'] == 1
    for _ in range(page_count - 1):
        assert page['events'] == [] and page['stream_state'] == 'partial_line'
        assert page['bytes_read'] <= 1024
        page = progress.read(root, 1, cursor=page['cursor'], limit_bytes=1024)
    assert [event['text'] for event in page['events']] == ['after oversized transport']
    assert page['bytes_read'] <= 1024 and page['stream_state'] == 'ready'
    assert progress.read(root, 1, cursor=page['cursor'], limit_bytes=1024)['events'] == []
    old = log.with_suffix('.old')
    log.rename(old); log.write_bytes(content)
    replaced = progress.read(root, 1, cursor=page['cursor'])
    assert replaced['stream_state'] == 'replaced' and replaced['restart_required']
    assert old.read_bytes().startswith(b'x' * oversize_bytes)


def test_progress_reader_failure_and_cursor_namespace_do_not_mutate_execution(tmp_path):
    root, _, log = transport(tmp_path)
    log.write_bytes(line(message('public')))
    cursor = progress.read(root, 1)['cursor']
    with pytest.raises(ValueError, match='cursor'):
        progress.read(root, 1, attempt=1, cursor=cursor)
    before = (root / 'settings.json').read_bytes()
    log.unlink(); log.symlink_to(root / 'settings.json')
    assert progress.read(root, 1)['status'] == 'progress_unavailable'
    assert (root / 'settings.json').read_bytes() == before
    assert not (root / 'pause.request').exists()


@pytest.mark.process_e2e
def test_real_cli_progress_and_http_reader_share_projection_and_resume_cursor(tmp_path):
    root, key, log = transport(tmp_path)
    log.write_bytes(line(message('visible public draft')))
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli', 'progress', '--root', str(root), '--step', '1']
    invoked = subprocess.run(command, env=env, capture_output=True, check=True)
    value = json.loads(invoked.stdout)
    ui, cookie = session(root, key)
    status, body, headers = ui.get('/nodes/1/events', host=ui.host, cookie=cookie)
    assert status == 200 and headers['Content-Type'].startswith('application/json')
    displayed = json.loads(body)
    assert set(displayed) - set(value) == {'progress_label', 'token_details'}
    assert {name: item for name, item in displayed.items()
            if name not in {'progress_label', 'token_details'}} == value
    assert displayed['progress_label'].endswith('公开日志已连接')
    from scidiscovery.research_loop import ui_view
    assert displayed['token_details'] == ui_view.token_details(value['usage'], dict(value['context'], step=1, attempt=0))
    with log.open('ab') as stream:
        stream.write(line(message('appended after disconnect')))
    invoked = subprocess.run(command + ['--cursor', value['cursor']], env=env, capture_output=True, check=True)
    assert json.loads(invoked.stdout)['events'][0]['text'] == 'appended after disconnect'
    restarted, cookie = session(root, key)
    status, body, _ = restarted.get('/nodes/1/events?cursor=' + value['cursor'], host=restarted.host, cookie=cookie)
    assert status == 200 and json.loads(body)['events'][0]['text'] == 'appended after disconnect'


@pytest.mark.process_e2e
def test_exact_cli_science_and_artifact_download_refuse_overwrite(tmp_path):
    from test_instance_science import scene
    root = scene(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli']
    flags = ['--root', str(root), '--step', '1']
    science = subprocess.run(command + ['result', *flags], env=env, capture_output=True, check=True)
    assert science.stdout == (root / 'native-evidence/001/science-output.json').read_bytes()
    listing = subprocess.run(command + ['materials', *flags, '--limit', '1'], env=env, capture_output=True, check=True)
    page = json.loads(listing.stdout)
    assert len(page['items']) == 1 and page['next_after'] == 1
    member = page['items'][0]
    output = tmp_path / 'download'
    download = command + ['artifact', *flags, '--index', str(member['index']), '--output', str(output)]
    subprocess.run(download, env=env, capture_output=True, check=True)
    assert output.read_bytes() == (c.workspace(root, 1) / 'artifacts' / member['name']).read_bytes()
    output.write_bytes(b'operator existing bytes')
    denied = subprocess.run(download, env=env, capture_output=True)
    assert denied.returncode == 1 and output.read_bytes() == b'operator existing bytes'
    c.build_job(root, 2, 'A', [1])
    unavailable = subprocess.run(command + ['result', '--root', str(root), '--step', '2'], env=env, capture_output=True)
    assert unavailable.returncode == 1 and json.loads(unavailable.stdout)['status'] == 'error'


def test_progress_poll_never_walks_prior_attempts_or_history(tmp_path, monkeypatch):
    root, _, log = transport(tmp_path)
    log.write_bytes(line(message('bounded current attempt')))
    monkeypatch.setattr(execution, 'records', lambda *args, **options: pytest.fail('Progress must not rescan attempt history'))
    page = progress.read(root, 1)
    assert page['events'][0]['text'] == 'bounded current attempt'
    assert progress.read(root, 1, cursor=page['cursor'])['events'] == []


def test_exact_attempt_usage_breakdown_and_safe_optional_context(tmp_path):
    root, key, log = transport(tmp_path)
    log.write_bytes(line(message('public')))
    meter = dict(input_tokens=100, cached_input_tokens=80, output_tokens=5)
    c.write(log.parent / 'usage.json', meter)
    c.write(log.parent / 'usage-observation.json', dict(coverage='live', thread_id='private-thread',
        context=dict(status='last_reported', total_tokens=25, model_context_window=100,
                     ratio=900, private_path='/private/session', text='private-context')))
    value = progress.read(root, 1)
    assert value['usage']['noncached_input_tokens'] == 20
    assert value['usage']['cache_hit_ratio'] == .8
    assert value['context'] == dict(status='last_reported', total_tokens=25, model_context_window=100, ratio=.25)
    assert c.read(log.parent / 'usage.json') == meter
    assert 'private-' not in json.dumps(value)
    assert progress.read(root, 1, attempt=1)['context'] == dict(status='unavailable')
    assert 'input_tokens' not in progress.read(root, 1, attempt=1)['usage']
    ui, cookie = session(root, key)
    status, body, _ = ui.get('/nodes/1/progress', host=ui.host, cookie=cookie)
    assert status == 200
    page = body.decode() if isinstance(body, bytes) else body
    assert 'data-token-details' in page and '80.0%' in page and '25 / 100' in page
    c.write(log.parent / 'usage-observation.json', dict(coverage='unknown', reason='session_discovery_capacity',
        context=dict(status='last_reported', total_tokens=25, model_context_window=100)))
    unknown = progress.read(root, 1)
    assert unknown['context'] == dict(status='unavailable')
    assert unknown['usage']['measurement_status'] == '正在定位会话计量'


def test_zero_input_has_no_invented_cache_hit(tmp_path):
    root, _, log = transport(tmp_path)
    c.write(log.parent / 'usage.json', dict(input_tokens=0, cached_input_tokens=0, output_tokens=5))
    value = progress.read(root, 1)
    assert value['usage']['cache_hit_ratio'] is None
    assert value['usage']['noncached_input_tokens'] == 0


def test_real_live_runner_identity_preserves_cursor_namespace_across_polls(tmp_path):
    from scidiscovery.research_loop import runtime
    root, _, log = transport(tmp_path)
    log.write_bytes(line(message('first live runner update')))
    record = log.parent
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json'), prepared_at=1.0))
    c.write(record / 'runner-process.json', runtime.process_identity(os.getpid(), c.record_ref(root / 'jobs/001.json')))
    first = progress.read(root, 1)
    assert first['state'] == 'running'
    with log.open('ab') as stream:
        stream.write(line(message('second live runner update')))
    second = progress.read(root, 1, cursor=first['cursor'])
    assert second['state'] == 'running'
    assert [row['text'] for row in second['events']] == ['second live runner update']


@pytest.mark.parametrize('text, secrets', [
    ('Authorization: Bearer opaque-sensitive-value', ['opaque-sensitive-value']),
    ('aUtHoRiZaTiOn: bEaReR mixed-sensitive-value', ['mixed-sensitive-value']),
    ('Authorization: Basic YmFzaWMtc2Vuc2l0aXZl', ['YmFzaWMtc2Vuc2l0aXZl']),
    (json.dumps({'authorization': 'Bearer json-sensitive-value', 'api_key': 'escaped"quoted-sensitive-value'}),
        ['json-sensitive-value', 'quoted-sensitive-value']),
    ('Cookie: session=first-sensitive-cookie; refresh=second-sensitive-cookie',
        ['first-sensitive-cookie', 'second-sensitive-cookie']),
])
def test_typed_public_messages_redact_complete_credentials_before_field_matching(tmp_path, text, secrets):
    root, key, log = transport(tmp_path)
    log.write_bytes(line(message(text)))
    value = progress.read(root, 1)
    assert value['events'] and '[credential]' in value['events'][0]['text']
    public = json.dumps(value, ensure_ascii=False)
    ui, cookie = session(root, key)
    _, body, _ = ui.get('/nodes/1/events', host=ui.host, cookie=cookie)
    for secret in secrets:
        assert secret not in public and secret.encode() not in body


def test_fifo_transport_is_opened_nonblocking_and_rejected_without_reading(tmp_path, monkeypatch):
    root, _, log = transport(tmp_path)
    os.mkfifo(log)
    original_open = os.open
    opened = []

    def guarded_open(path, flags, *args, **options):
        if Path(path) == log:
            # Check before the real open, so a missing flag fails without
            # hanging this negative control on a FIFO with no writer.
            assert flags & os.O_NONBLOCK
            opened.append(path)
        return original_open(path, flags, *args, **options)

    monkeypatch.setattr(progress.os, 'open', guarded_open)
    value = progress.read(root, 1)
    assert opened == [log]
    assert value['status'] == 'progress_unavailable' and value['events'] == []
    assert not (root / 'pause.request').exists()


def test_malformed_backend_type_is_omitted_and_later_public_events_survive(tmp_path):
    root, key, log = transport(tmp_path)
    raw = line(dict(type='item.completed', item=dict(type=['invalid-type'], text='private-malformed-content')))
    log.write_bytes(raw + line(message('safe event after malformed transport')))
    value = progress.read(root, 1)
    assert value['malformed_lines'] == 1
    assert [row['text'] for row in value['events']] == ['safe event after malformed transport']
    assert 'private-malformed-content' not in json.dumps(value)
    ui, cookie = session(root, key)
    _, body, _ = ui.get('/nodes/1/events', host=ui.host, cookie=cookie)
    assert b'private-malformed-content' not in body
    assert log.read_bytes().startswith(raw)


def test_overview_cold_tail_is_bounded_and_full_forward_reader_stays_complete(tmp_path):
    root, key, log = transport(tmp_path)
    early = line(message('old message ' + 'x' * 1000))
    newest = line(message('recent public scientific message'))
    log.write_bytes(early * 300 + b'not-utf8\xff\n' + newest)
    tail = progress.tail(root, 1)
    assert tail['bytes_read'] <= 65536 and tail['tail_omitted']
    assert tail['events'][-1]['text'] == 'recent public scientific message'
    assert 'not-utf8' not in json.dumps(tail)
    ui, cookie = session(root, key)
    activity = ui._activity(dict(step=1, role='H'))
    assert activity['messages'][-1] == 'recent public scientific message'
    count, cursor = 0, None
    while True:
        page = progress.read(root, 1, cursor=cursor)
        count += len(page['events'])
        cursor = page['cursor']
        if not page.get('has_more'):
            break
    assert count == 301
    log.write_bytes(b'x' * 300000 + b'\n' + newest)
    assert progress.tail(root, 1)['events'][-1]['text'] == 'recent public scientific message'
    log.write_bytes(b'x' * 300000)
    oversized = progress.tail(root, 1)
    assert oversized['events'] == [] and oversized['bytes_read'] <= 65536
    ui.translation.close()
