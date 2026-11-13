"""Overview, result and workspace pages: read-only display, unchanged controls."""
import copy
import re
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import instance_render as render
from test_execution_authority import prepare
from test_history_handoff import finish
from test_instance_ui import session


def text(page):
    return page.decode() if isinstance(page, bytes) else page


def files(root):
    return {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_overview_answers_state_action_usage_and_rounds_for_manager_and_reader(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    page = text(ui.get('/', host=ui.host, cookie=cookie)[1])
    assert '<h1>Assess one exact declared implementation.</h1>' in page
    assert '未开始' in page and '已准备，等待你开始' in page
    # The status head carries no controls; the one start form is the primary action.
    assert page.count('action="/control/start"') == 1
    assert page.count('<section class="stat') == 4 and '还没有开始研究' in page
    assert '更多操作' in page and '如何理解这些结果' in page
    assert 'href="/configuration#budget-settings"' in page and 'action="/control/budget"' not in page
    assert 'nav class="top-nav"' in page and '研究过程' in page and 'href="/settings"' in page
    finish(root, 1, 'W', [], summary='Plan <script>x</script>')
    finish(root, 2, 'A', [1], summary='Continue')
    ui._snapshot(force=True)  # The shared cache otherwise serves the same 2 s window.
    page = text(ui.get('/', host=ui.host, cookie=cookie)[1])
    assert '&lt;script&gt;x&lt;/script&gt;' in page and '<script>x' not in page
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    reader = headers['Set-Cookie']
    before = files(root)
    readonly = text(ui.get('/', host=ui.host, cookie=reader)[1])
    assert 'action="/control/' not in readonly and '只读查看' in readonly
    assert 'class="budget-adjustment"' not in readonly
    for path in ('/settings', '/results', '/results/1', '/results/2', '/final-results', '/progress', '/guidance', '/?after=0', '/nodes/1'):
        assert ui.get(path, host=ui.host, cookie=reader)[0] == 200, path
    assert files(root) == before


def test_overview_and_live_usage_share_weighted_cache_and_latest_exact_context(tmp_path):
    from scidiscovery.research_loop import contracts as c, ui_saved_status
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'W', [])
    finish(root, 2, 'A', [1])
    for step, input_tokens, cached, output in [(1, 100, 80, 2), (2, 300, 30, 5)]:
        record = root / 'native-evidence' / f'{step:03d}'
        c.write(record / 'usage.json', dict(input_tokens=input_tokens, cached_input_tokens=cached, output_tokens=output))
        c.write(record / 'usage-observation.json', dict(coverage='terminal', context=dict(
            status='last_reported', total_tokens=25, model_context_window=100)))
    saved = ui_saved_status.status(root)
    assert saved['usage']['observed_tokens'] == 407
    assert saved['usage']['noncached_input_tokens'] == 290
    assert saved['usage']['cache_hit_ratio'] == .275
    assert saved['usage']['context']['step'] == 2 and saved['usage']['context']['attempt'] == 0
    ui, cookie = session(root, key)
    page = text(ui.get('/', host=ui.host, cookie=cookie)[1])
    fragments = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']
    for markup in (page, fragments['live-token-monitor']):
        assert '27.5%' in markup and '25 / 100' in markup
        assert '<h2>Token 监控</h2>' in markup
    for markup in (page, fragments['live-usage']):
        assert markup.count('<section class="stat') == 4
        assert all('token-metrics' not in stat for stat in re.findall(r'<section class="stat[^>]*>.*?</section>', markup, re.S))
    assert 'token-monitor' not in fragments['live-usage']
    assert page.index('id="live-hero"') < page.index('id="live-usage"') < page.index('id="live-activity"') < page.index('id="live-trail"') < page.index('id="live-token-monitor"') < page.index('class="more-actions"')
    continuation = root / 'native-evidence/002/continuations/001'
    c.write(continuation / 'native-intent.json', dict(task_ref='fixture'))
    latest = ui_saved_status.status(root)['usage']
    assert latest['context'] == dict(status='unavailable', step=2, attempt=1)
    assert latest['observed_tokens'] == 407


@pytest.mark.parametrize('state', ['ready', 'budget_exhausted', 'final'])
def test_budget_recovery_editor_is_contextual_and_starts_collapsed(tmp_path, monkeypatch, state):
    root, key = prepare(tmp_path, enabled=False)
    if state != 'ready':
        finish(root, 1, 'H', [], action='analyze')
    ui, cookie = session(root, key)
    snapshot = copy.deepcopy(ui._snapshot())
    if state == 'budget_exhausted':
        snapshot['view']['budget']['max_tokens'] = 1
        snapshot['view']['usage']['accounted_tokens'] = 2
    elif state == 'final':
        snapshot['view']['closeout'] = dict(status='final_only')
    monkeypatch.setattr(ui, '_snapshot_or_error', lambda **options: snapshot)
    page = text(ui.get('/', host=ui.host, cookie=cookie)[1])
    outer = re.search(r'<details class="more-actions"[^>]*>', page).group(0)
    assert (' open' in outer) == (state in {'budget_exhausted', 'final'})
    assert 'action="/control/budget"' not in page and 'class="budget-adjustment"' not in page
    recovery = text(ui.get('/budget-recovery', host=ui.host, cookie=cookie)[1])
    if state == 'ready':
        assert 'href="/configuration#budget-settings"' in page and 'href="/configuration#budget-settings"' in recovery
        assert 'action="/control/budget"' not in recovery
        configuration = text(ui.get('/configuration', host=ui.host, cookie=cookie)[1])
        assert 'action="/control/configuration-update"' in configuration
        assert configuration.count('name="cfg.budget.max_tokens"') == 1
        assert 'action="/control/budget"' not in configuration
    else:
        assert 'href="/budget-recovery#budget"' in page
        budget = re.search(r'<details class="budget-adjustment"(.*?)</details>', recovery, re.S).group(0)
        assert budget.startswith('<details class="budget-adjustment"><summary>恢复前追加预算</summary><div id="budget">')
        assert re.findall(r'<form\b[^>]*action="(/control/[^"]+)"', recovery) == ['/control/budget']
        assert all('name="' + field + '"' in budget for field in (
            'max_tokens', 'max_seconds', 'max_steps', 'authorization_note', 'csrf', 'nonce'))
        assert budget.count('<textarea') == 1 and 'name="authorization_statement"' not in budget
        assert '调整说明' in budget and '确认追加预算' in budget
        assert '已用额度保留' in budget and '生效后返回总览继续研究；提交本表不会启动研究。' in budget
        assert '恢复仍须满足原有条件' in budget
        assert not any('action="/control/' + action + '"' in budget for action in ('start', 'continue', 'resume'))
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    reader = headers['Set-Cookie']
    readonly = text(ui.get('/', host=ui.host, cookie=reader)[1])
    assert 'action="/control/' not in readonly and 'href="/budget-recovery#budget"' not in readonly
    for path in ('/budget-recovery', '/configuration'):
        with pytest.raises(PermissionError):
            ui.get(path, host=ui.host, cookie=reader)
    ui.translation.close()


def test_status_matrix_primary_actions_follow_state_without_new_controls(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    snapshot = ui._snapshot()
    def primary(state, manager=True):
        return ui._primary(token, manager, snapshot, dict(state=state, display=dict(current=dict(step=4))))
    assert 'action="/control/start"' in primary('ready') and 'action="/control/pause"' in primary('active')
    assert 'action="/control/continue"' in primary('paused')
    assert not primary('pause_requested') and not primary('error')  # No cancellation exists.
    assert 'href="/results/4"' in primary('waiting') and 'href="/progress"' in primary('unknown')
    assert 'href="/final-results"' in primary('completed')
    assert all('href="/budget-recovery#budget"' in primary(state) for state in ('budget_exhausted', 'final'))
    assert all('<form' not in (primary(state, False) or '') for state in ('ready', 'active', 'paused', 'final'))
    assert '只读查看' in primary('active', False)


def test_snapshot_failure_is_specific_and_the_page_still_renders(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    ui, cookie = session(root, key)
    def broken(**options):
        raise ValueError('Control ledger at ' + str(root) + '/control-ledger is malformed')
    monkeypatch.setattr(ui, '_snapshot', broken)
    status, page, _ = ui.get('/', host=ui.host, cookie=cookie)
    page = text(page)
    # Read-only rendering records no diagnostic; it names the failure and the original-record check.
    assert status == 200 and '状态不可读' in page and '工作台内部错误' in page and '核对保存的原记录' in page
    assert 'is malformed' not in page
    assert str(root) not in page and '更多操作' in page and 'nav class="top-nav"' in page
    live = ui._live_snapshot(ui._browser(cookie), '')
    assert '工作台内部错误' in live['fragments']['live-hero'] and '核对保存的原记录' in live['fragments']['live-hero'] and live['current'] is None


def test_workspace_list_names_the_failure_instead_of_a_generic_note(tmp_path, monkeypatch):
    from scidiscovery.research_loop.instance_ui import InstanceUI
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workbench'; workspace.mkdir()
    host = InstanceUI(workspace, secret_file=key, instance_roots=[root], workspace=True)
    host.host = '127.0.0.1:18753'
    good = host._loaded_instances()
    assert 'instance-card' in good and '未开始' in good and '0 / 4,000 tokens' in good
    child = host._child(1)
    monkeypatch.setattr(child, '_summary_snapshot', lambda **options: (_ for _ in ()).throw(OSError('busy record')))
    failed = host._loaded_instances()
    assert '无法读取此实例：工作台内部错误' in failed and '核对保存的原记录' in failed and '重试' in failed
    assert 'busy record' not in failed
    assert '状态暂不可用' not in failed


def test_result_pages_lead_with_the_conclusion_and_keep_the_original(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    from scidiscovery.research_loop import contracts as c, delivery_review
    finish(root, 1, 'W', [], files={'plan.md': 'plan'})
    c.build_job(root, 2, 'A', [1], evidence_return=delivery_review.make('delivery_review', 1, 0))
    finish(root, 2, 'A', [1], action='finish', summary='Final <b>judgment</b>', limitations=['L1', 'L2', 'L3', 'L4'])
    ui, cookie = session(root, key)
    before = files(root)
    status, page, headers = ui.get('/final-results', host=ui.host, cookie=cookie)
    page = text(page)
    assert status == 200 and page.index('class="result-card') < page.index('class="candidates') < page.index('id="materials"')
    assert page.index('id="materials"') < page.index('id="full-text"')
    assert '未推荐' in page and 'L1' in page and 'L3' in page and '另有 1 条局限' in page
    assert '本阶段 0 份；截至本阶段，各阶段共 1 份研究文件' in page and 'href="/nodes/1/materials"' in page
    assert 'Final &lt;b&gt;judgment&lt;/b&gt;' in page and '研究发现' in page and '/nodes/2/science' in page
    assert re.findall(r'<script src="([^"]+)"></script>', page) == ['/assets/translation.js', '/assets/progress.js']
    h = text(ui.get('/results/1', host=ui.host, cookie=cookie)[1])
    assert 'class="role-line"' in h and 'id="full-text"' not in h
    assert re.findall(r'<script src="([^"]+)"></script>', h) == ['/assets/translation.js']
    listing = text(ui.get('/results', host=ui.host, cookie=cookie)[1])
    records = listing[listing.index('<h2>阶段原文记录</h2>'):]
    assert listing.count('href="/results/2"') == 1 and records.index('/results/2') < records.index('/results/1')
    assert files(root) == before
    with pytest.raises(ValueError):
        ui.get('/results/3', host=ui.host, cookie=cookie)


def test_csp_uses_same_origin_static_assets_without_inline_sources(tmp_path):
    assert "'unsafe-inline'" not in render.CSP and "'unsafe-eval'" not in render.CSP
    directives = dict((parts[0], set(parts[1:])) for directive in render.CSP.split(';') if (parts := directive.split()))
    assert directives == {
        'default-src': {"'none'"}, 'connect-src': {"'self'"}, 'form-action': {"'self'"},
        'frame-ancestors': {"'none'"}, 'base-uri': {"'none'"},
        'script-src': {"'self'"}, 'style-src': {"'self'"}}
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    for path in ('/', '/configuration', '/guidance'):
        status, body, _ = ui.get(path, host=ui.host, cookie=cookie)
        page = text(body)
        assert status == 200
        assert '<link rel="stylesheet" href="/assets/style.css">' in page
        assert '<style' not in page
        scripts = re.findall(r'<script([^>]*)>(.*?)</script>', page, re.S)
        # Inline JSON data blocks are never executed; every executable script is a same-origin asset.
        executable = [(attributes, inline) for attributes, inline in scripts if 'type="application/json"' not in attributes]
        assert executable and all('src="/assets/' in attributes and not inline for attributes, inline in executable)
    for path in ('/assets/style.css', '/assets/translation.js', '/assets/progress.js'):
        status, body, _ = ui.get(path, host=ui.host, cookie=cookie)
        assert status == 200 and body
    ui.translation.close()
