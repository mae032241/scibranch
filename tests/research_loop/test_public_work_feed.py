"""Public work records contain prose while meters and originals retain their facts."""
import base64
import copy
from html.parser import HTMLParser
import json
import shutil
import subprocess

import pytest

from scidiscovery.research_loop import backends, instance_render, progress, public_output
from scidiscovery.research_loop.instance_ui import InstanceUI
from scidiscovery.research_loop.pi_backend import Pi
from test_instance_ui import session
from test_public_progress import line, message, transport


def test_codex_completed_public_reasoning_summary_only():
    item = dict(type='reasoning', text='Compare the two reported measurements.',
                metadata=dict(prompt='private prompt'), text_truncated=True)
    before = copy.deepcopy(item)
    for kind in ('item.started', 'item.updated'):
        assert backends.Codex.public_events(None, dict(type=kind, item=item)) == []
    assert backends.Codex.public_events(None, dict(type='item.completed', item=item)) == [
        dict(type='thinking', text=item['text'], text_truncated=True)]
    assert backends.Codex.public_events(None,
        dict(type='item.completed', item=dict(type='reasoning', metadata=item))) == []
    assert item == before


def test_pi_explicit_blocks_preserve_order_and_ignore_deltas_and_tool_inputs():
    value = dict(type='message_end', message=dict(role='assistant', content=[
        dict(type='text', text='First '), dict(type='text', text='message.'),
        dict(type='thinking', thinking='Compare '), dict(type='thinking', thinking='observations.'),
        dict(type='toolCall', name='bash', arguments=dict(thinking='private tool input')),
        dict(type='text', text='Second message.'),
        dict(type='thinking', text='wrong SDK field'),
    ]))
    before = copy.deepcopy(value)
    assert Pi.public_events(None, value) == [
        dict(type='message', text='First message.'),
        dict(type='thinking', text='Compare observations.'),
        dict(type='message', text='Second message.')]
    assert Pi.public_events(None, dict(type='message_update',
        assistantMessageEvent=dict(type='thinking_delta', delta='Compare observations.'))) == []
    assert value == before


def test_protocol_explicit_thinking_uses_same_public_shape():
    value = dict(type='thinking', text='Public plan', text_truncated=True,
                 metadata=dict(prompt='private prompt'))
    assert backends.Protocol.public_events(None, value) == [
        dict(type='thinking', text='Public plan', text_truncated=True)]
    assert backends.Protocol.public_events(None, dict(type='thinking', thinking='wrong field')) == []


@pytest.mark.parametrize('kind', ['message', 'thinking'])
@pytest.mark.parametrize('text', ['', ' \n\t ', '\x00'])
def test_blank_prose_does_not_take_a_work_record_row(kind, text):
    assert public_output.event(dict(type=kind, text=text), lambda value: value.replace('\x00', '')) is None
    assert InstanceUI._event_html(dict(type=kind, text=' \n\t ')) == ''


@pytest.mark.parametrize('kind', ['message', 'thinking'])
def test_short_valid_prose_and_truncation_metadata_survive_cleaning(tmp_path, kind):
    clean = public_output.cleaner(tmp_path, dict(native_backend='codex'))
    assert public_output.event(dict(type=kind, text='好'), clean)['text'] == '好'
    text = f'{tmp_path} api_key="private-token" ' + '字' * 17000
    original = dict(type=kind, text=text, metadata=dict(prompt='private prompt'))
    before = copy.deepcopy(original)
    value = public_output.event(original, clean)
    assert value['type'] == kind and value['text_truncated'] is True
    assert value['omitted_characters'] == len(text) - 16384
    assert '[private]' in value['text'] and '[credential]' in value['text']
    assert str(tmp_path) not in value['text'] and 'private-token' not in value['text']
    assert 'metadata' not in value and original == before
    assert '本条公开文本已截短' in InstanceUI._event_html(dict(type=kind, text='好', text_truncated=True))


class Details(HTMLParser):
    def __init__(self):
        super().__init__()
        self.disclosures = []
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'details': self.disclosures.append(dict(attrs))
        if tag == 'script': self.scripts.append(dict(attrs))


def test_thinking_server_markup_is_collapsed_and_escaped():
    markup = InstanceUI._event_html(dict(type='thinking', text='<script>alert(1)</script>'))
    parsed = Details(); parsed.feed(markup)
    assert len(parsed.disclosures) == 1 and 'open' not in parsed.disclosures[0]
    assert '思考摘要' in markup and '&lt;script&gt;' in markup and not parsed.scripts
    for event in (dict(type='tool', tool='bash', status='completed'), dict(type='usage'),
                  dict(type='session.started'), dict(type='turn.completed')):
        assert InstanceUI._event_html(event) == ''


def test_noise_only_pages_advance_cursor_then_deliver_prose_and_keep_meter(tmp_path):
    root, key, log = transport(tmp_path)
    noise = b''.join([
        line(dict(type='thread.started', thread_id='private-session')),
        line(dict(type='item.completed', item=dict(type='command_execution', command='private input'))),
        line(dict(type='turn.completed', usage=dict(input_tokens=10, cached_input_tokens=2, output_tokens=1))),
        line(message(' \n\t ')),
    ])
    raw = noise * 12
    log.write_bytes(raw)
    meter = dict(input_tokens=10, cached_input_tokens=2, output_tokens=1)
    meter_bytes = json.dumps(meter).encode()
    (log.parent / 'usage.json').write_bytes(meter_bytes)
    cursor = None; consumed = 0
    for _ in range(len(raw) // 512 + 1):
        value = progress.read(root, 1, cursor=cursor, limit_bytes=1024)
        assert value['events'] == [] and value['bytes_read'] <= 1024
        decoded = json.loads(base64.urlsafe_b64decode(value['cursor'] + '=' * (-len(value['cursor']) % 4)))
        assert decoded['offset'] > consumed
        consumed = decoded['offset']; cursor = value['cursor']
        if not value['has_more']: break
    assert consumed == len(raw)
    assert {key: value['usage'][key] for key in meter} == meter
    assert value['usage']['noncached_input_tokens'] == 8
    with log.open('ab') as stream:
        stream.write(line(dict(type='item.completed', item=dict(type='reasoning', text='Public summary'))))
        stream.write(line(message('好')))
    resumed = progress.read(root, 1, cursor=cursor)
    assert [(item['type'], item['text']) for item in resumed['events']] == [('thinking', 'Public summary'), ('message', '好')]
    assert progress.read(root, 1, cursor=resumed['cursor'])['events'] == []
    ui, cookie = session(root, key)
    try:
        status, body, _ = ui.get('/nodes/1/events', host=ui.host, cookie=cookie)
        assert status == 200
        assert [(item['type'], item['text']) for item in json.loads(body)['events']] == [('thinking', 'Public summary'), ('message', '好')]
        status, body, _ = ui.get('/nodes/1/progress', host=ui.host, cookie=cookie)
        markup = body.decode() if isinstance(body, bytes) else body
        assert status == 200 and '<details><summary>思考摘要</summary>' in markup
    finally:
        ui.translation.close()
    assert log.read_bytes().startswith(raw)
    assert (log.parent / 'usage.json').read_bytes() == meter_bytes


@pytest.mark.process_e2e
def test_javascript_append_keeps_thinking_closed_and_preserves_reader_toggle():
    node = shutil.which('node')
    if node is None: pytest.skip('Node is required for the public feed DOM process check')
    append = instance_render.PROGRESS_SCRIPT.split('function append(item) {', 1)[1].split('\nfunction tokenDetails(', 1)[0]
    script = r'''
const assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tagName = tag; this.className = ''; this.children = []; this.textContent = ''; this.open = false; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  get firstChild() { return this.children[0]; }
}
const events = new Element('section');
const panel = {querySelector: selector => { assert.equal(selector, '[data-events]'); return events; }};
const document = {createElement: tag => new Element(tag)};
''' + 'function append(item) {' + append + r'''
for (const item of [{type:'tool'}, {type:'usage'}, {type:'session.started'}, {type:'turn.completed'}, {type:'message',text:' \n '}]) append(item);
assert.equal(events.children.length, 0);
append({type:'thinking', text:'<script>public summary</script>', text_truncated:true});
const details = events.firstChild.firstChild;
assert.equal(details.tagName, 'details'); assert.equal(details.open, false);
assert.equal(details.children[0].textContent, '思考摘要');
assert.equal(details.children[1].textContent, '<script>public summary</script>');
assert.equal(details.children[2].tagName, 'small');
details.open = true;
append({type:'message',text:'好'}); append({type:'thinking',text:'Next summary'});
assert.equal(events.firstChild.firstChild, details); assert.equal(details.open, true);
assert.equal(events.children[2].firstChild.open, false);
assert.equal(events.children.length, 3);
'''
    invoked = subprocess.run([node, '-e', script], capture_output=True, text=True, timeout=10)
    assert invoked.returncode == 0, invoked.stderr
