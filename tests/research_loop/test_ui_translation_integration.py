"""Browser auth and application-only translation integration; no providers."""
import html
import json
import re
import threading
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import contracts as c, instance_render as render, ui_components as ui
from scidiscovery.research_loop import ui_translation
from scidiscovery.research_loop.instance_ui import InstanceUI
from test_execution_authority import prepare
from test_history_handoff import finish
from test_instance_ui import session


def state(host, cookie):
    return json.loads(host.get('/translation/state', host=host.host, cookie=cookie)[1])


def request(host, cookie, action, texts, *, credentials=None):
    credentials = credentials or state(host, cookie)
    return host.post('/translation/' + action, host=host.host, origin='http://' + host.host,
        cookie=cookie, content_type='application/json', body=json.dumps(dict(texts=texts,
            nonce=credentials['nonce'], csrf=credentials['csrf'][action])).encode())


def save(host, cookie, **values):
    credentials = state(host, cookie)
    values.update(nonce=credentials['nonce'], csrf=credentials['csrf']['configure'])
    return host.post('/translation/configure', host=host.host, origin='http://' + host.host,
        cookie=cookie, content_type='application/x-www-form-urlencoded', body=urlencode(values).encode())


def test_translation_routes_reject_new_calls_before_launch_and_keep_cached_lookup(tmp_path, monkeypatch):
    import subprocess
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze', summary='An exact original finding')
    frozen = (root / 'settings.json').read_bytes()
    host, cookie = session(root, key)
    def scientific_records():
        records = {}
        for name in ('jobs', 'native-evidence', 'control-ledger', 'host-control', 'host-runtime', 'host-delivery', 'execution-authority'):
            directory = root / name
            for path in [directory, *directory.rglob('*')]:
                if path.exists():
                    records[path.relative_to(root)] = path.read_bytes() if path.is_file() else None
        return records
    before = scientific_records()
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('translation launched'))
    try:
        assert save(host, cookie, enabled='true', model='reading-model', backend='codex')[0] == 303
        profile = ui_translation._profile(host.translation._config)
        host.translation._db.execute('INSERT INTO translations(profile, source, translation) VALUES (?, ?, ?)',
            (profile, 'An exact original finding', '已有译文'))
        host.translation._db.commit()
        assert state(host, cookie)['supported'] is False
        value = json.loads(request(host, cookie, 'start', ['An exact original finding', 'run shell'])[1])
        assert value['status'] == 'unsupported'
        assert value['entries'][0]['status'] == 'cached' and value['entries'][1]['status'] == 'missing'
        assert json.loads(request(host, cookie, 'lookup', ['An exact original finding'])[1])['entries'][0]['translation'] == '已有译文'
        assert frozen == (root / 'settings.json').read_bytes()
        assert scientific_records() == before
    finally:
        host.translation.close()


def test_workspace_translation_settings_are_shared_with_mounted_instances(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    host = InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root]); host.host = '127.0.0.1:18753'
    cookie = host.get('/manage?' + urlencode(dict(token=host.management_token)), host=host.host)[2]['Set-Cookie']
    try:
        assert host.get('/reading', host=host.host, cookie=cookie)[0] == 200
        save(host, cookie, enabled='true', backend='codex', model='app-model', target_language='en')
        child = host._child(1)
        assert child.translation is host.translation
        assert child.translation.settings()['target_language'] == 'en'
        child_cookie = child.get('/manage?' + urlencode(dict(token=child.management_token)), host=child.host)[2]['Set-Cookie']
        _, page, _ = host.get('/instances/1/reading', host=host.host, cookie=child_cookie)
        assert b'data-translation-url="/instances/1/translation"' in page
        assert b'action="/instances/1/translation/configure"' in page
        assert not (root / '.scid-ui').exists()
    finally:
        host.translation.close()


def test_narrative_annotation_preserves_exact_text_and_static_page_script():
    source = 'Original <script>alert("x")</script> & units 3 nm\r\nnext\rline\x00'
    marked = ui.translatable(source)
    assert json.loads(html.unescape(re.search('data-translation-source="([^"]*)"', marked)[1])) == source
    assert ui.translatable('same\r\nline') != ui.translatable('same\nline')
    assert '<script>' not in marked
    assert ui.esc(source) == html.escape(source, quote=True)
    page = render.page('Static result', ui.translatable(source), live=False).decode()
    assert render.translation_client.SCRIPT in page and render.PROGRESS_SCRIPT not in page
    assert render.translation_client.CSP in render.CSP
    assert 'node.textContent = text' in render.translation_client.SCRIPT
    assert 'innerHTML' not in render.translation_client.SCRIPT


def test_candidate_identity_fallbacks_never_become_translation_sources():
    from scidiscovery.research_loop import ui_view
    projection = dict(index=[dict(id='local-identity-only', metric=3, evaluation_key='exact-evaluation', observation_step=1)],
        nominated_ids=['local-identity-only'], references={}, graph=dict(candidates={
            'local-identity-only': dict(mechanism_ref='h001.m1')}))
    rows = ui_view.candidate_rows(projection)
    markup = ui.candidate_table(rows)
    assert 'only' in markup and 'h001.m1' in markup and 'exact-evaluation' in markup
    assert 'data-translation-source' not in markup
    projection['index'][0]['label'] = 'Authored candidate'
    rows = ui_view.candidate_rows(projection, mechanisms={'h001.m1': 'Authored mechanism'})
    markup = ui.candidate_table(rows)
    sources = [json.loads(html.unescape(value)) for value in re.findall('data-translation-source="([^"]*)"', markup)]
    assert sources == ['Authored candidate', 'Authored mechanism']


def test_cli_translation_config_is_application_only_and_explicit(tmp_path, monkeypatch):
    from scidiscovery.research_loop import cli, instance_ui
    root, key = prepare(tmp_path, enabled=False)
    frozen = c.settings(root)
    config = tmp_path / 'reading.toml'
    config.write_text('[translation]\nenabled = true\nbackend = "codex"\n'
        'model = "application-reading-model"\ntarget_language = "ja"\nreasoning_effort = "low"\n', encoding='utf-8')
    served = []
    def serve(selected, **options):
        served.append((selected, options))
        return dict(status='ui_stopped')
    monkeypatch.setattr(instance_ui, 'serve', serve)
    arguments = ['serve-ui', '--root', str(root), '--approval-secret-file', str(key)]
    assert cli.main(arguments + ['--translation-config', str(config)]) == 0
    assert served[-1][0] == root
    assert served[-1][1]['translation_config'] == dict(enabled=True, backend='codex',
        model='application-reading-model', target_language='ja', reasoning_effort='low')
    assert served[-1][1]['translation_config']['model'] != frozen['model']
    assert cli.main(arguments) == 0
    assert 'translation_config' not in served[-1][1]
    assert c.settings(root) == frozen
