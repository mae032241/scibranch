"""Direct UI/control boundaries only; no HTTP, model or execution qualification."""
from datetime import datetime, timedelta, timezone
import html
import re
import threading
from urllib.parse import quote, urlencode

import pytest

from scidiscovery.browser_access import access_cookie
from scidiscovery.research_loop import cli, controller, instance_control as control
from scidiscovery.research_loop.instance_ui import InstanceUI
from test_execution_authority import prepare
from test_history_handoff import finish


def test_current_context_is_independent_of_billing_and_loaded_once(tmp_path, monkeypatch):
    from scidiscovery.research_loop import ui_saved_status as saved
    record=tmp_path/'native-evidence/001'
    record.mkdir(parents=True)
    (record/'native-intent.json').write_text('{}')
    observation=dict(coverage='unknown',context=dict(status='last_reported',total_tokens=85,
        model_context_window=100,current_window=True,observed_at=123.,reported_at=122.))
    reads=[]
    def read(path,memo):
        reads.append(path.name)
        return observation if path.name=='usage-observation.json' else None
    monkeypatch.setattr(saved,'_saved_read',read)
    monkeypatch.setattr(saved,'_native_outcome',lambda *args: {})
    value=saved._usage(tmp_path,[1])
    assert value['unknown_steps']==[1]
    assert value['context']['status']=='last_reported' and value['context']['ratio']==.85
    assert value['context']['observed_at']==123. and value['context']['reported_at']==122.
    assert value['context']['step']==1 and value['context']['attempt']==0
    assert reads.count('usage-observation.json')==1


def test_latest_unstarted_attempt_does_not_reuse_prior_context(tmp_path, monkeypatch):
    from scidiscovery.research_loop import ui_saved_status as saved
    record=tmp_path/'native-evidence/001'
    (record/'continuations/001').mkdir(parents=True)
    (record/'native-intent.json').write_text('{}')
    observation=dict(coverage='live',context=dict(status='last_reported',total_tokens=85,
        model_context_window=100,current_window=True,observed_at=123.))
    monkeypatch.setattr(saved,'_saved_read',lambda path,memo:
        observation if path.name=='usage-observation.json' else None)
    monkeypatch.setattr(saved,'_native_outcome',lambda *args: {})
    value=saved._usage(tmp_path,[1])
    assert value['context']==dict(status='unavailable',step=1,attempt=1)


def test_execution_owner_view_is_bounded_readonly_and_not_scientific_counts(tmp_path,monkeypatch):
    from pathlib import Path
    from scidiscovery.research_loop import experiments,ui_saved_status as saved
    job=dict(step=2,role='W',worker_view_version=1)
    records=tmp_path/'host-runtime/experiment-control/002/records'
    records.mkdir(parents=True)
    config=dict(record_dir=str(records),scope=dict(step=2),native_attempt=dict(number=1))
    owner=experiments.Records(config)
    owner.live_status('exact-original','submitted',accepted=dict(_display_first_call=True,native_attempt=dict(number=1)))
    monkeypatch.setattr(Path,'iterdir',lambda *args:pytest.fail('Display must not enumerate receipts'))
    monkeypatch.setattr(experiments,'registration',lambda *args:pytest.fail('Display must not load a backend'))
    first=saved._live_executions(tmp_path,job)
    assert first['coverage']=='observed' and first['counts']['submitted']==1
    assert first['counts']['running']==0 and first['calls'][0]['call_id']=='exact-original'
    assert first['calls'][0]['attempt']==1
    owner.live_status('exact-original','running')
    changed=saved._live_executions(tmp_path,job)
    assert changed['counts']['running']==1 and changed['counts']['submitted']==0
    assert set(changed['calls'][0])=={'call_id','attempt','state','updated_at'}
    assert 'scientific' not in changed and 'candidate_id' not in changed['calls'][0]
    owner.live_status('exact-original','known_success')
    assert saved._live_executions(tmp_path,job)['counts']['known_success']==1


def test_missing_execution_view_is_explicit_unknown_without_legacy_scan(tmp_path,monkeypatch):
    from pathlib import Path
    from scidiscovery.research_loop import ui_saved_status as saved
    monkeypatch.setattr(Path,'iterdir',lambda *args:pytest.fail('No legacy scan'))
    value=saved._live_executions(tmp_path,dict(step=2,role='W',worker_view_version=1))
    assert value['coverage']=='unknown' and value['counts']=={} and value['calls']==[]


def test_only_bound_observed_process_identity_marks_running_and_terminal_wins(tmp_path,monkeypatch):
    from scidiscovery.research_loop import experiments,protocol,ui_saved_status as saved
    records=tmp_path/'host-runtime/experiment-control/002/records'
    directory=records/'calls/exact-call'
    directory.mkdir(parents=True)
    scope=dict(step=2)
    experiments.write(directory/'accepted.json',dict(call_id='exact-call',scope=scope,
        native_attempt=dict(number=0)))
    monkeypatch.setattr(protocol,'require_scope',lambda value:None)
    owner=experiments.Records(dict(record_dir=str(records),scope=scope))
    publish=experiments.Records.publish.__wrapped__
    invalid=records/'calls/invalid-process'
    invalid.mkdir()
    experiments.write(invalid/'accepted.json',dict(call_id='invalid-process',scope=scope,
        native_attempt=dict(number=0)))
    publish(owner,'invalid-process','process.json',dict(pid=123))
    assert not (records/'live-status.json').exists()
    identity=dict(pid=123,start_ticks=1,pgid=123,sid=123,boot_id='observed-boot',state='S',
        task_ref=dict(path=str(directory/'accepted.json')))
    publish(owner,'exact-call','process.json',identity)
    process_original=(directory/'process.json').read_bytes()
    job=dict(step=2,role='W',worker_view_version=1)
    assert saved._live_executions(tmp_path,job)['counts']['running']==1
    publish(owner,'exact-call','completed.json',dict(status='known_success'))
    with pytest.raises(FileExistsError):publish(owner,'exact-call','process.json',identity)
    assert (directory/'process.json').read_bytes()==process_original
    owner.live_status('exact-call','running')  # A stale observation cannot regress terminal.
    value=saved._live_executions(tmp_path,job)
    assert value['counts']['known_success']==1 and value['counts']['running']==0


def test_technical_pause_offers_only_exact_owner_reported_recovery(tmp_path):
    root,key=prepare(tmp_path,enabled=False)
    ui,cookie=session(root,key)
    token=ui._browser(cookie)
    snapshot=ui._snapshot()
    technical=dict(phase='native_prelaunch',stage='technical_pause',pending_step=2,pending_role='W',
        reason='native_metadata_unsupported',repair_scope='worker_attempt',expected_attempt=1,
        recovery_available=False,supported_actions=[],recheck_required=True)
    current=dict(step=2,role='W',state='native_not_submitted',continuations=1,technical_stop=technical)
    snapshot['view'].update(nodes=[current],paused=False,correction={})
    snapshot.update(conclusion=None,final=None,operation=None)
    status=ui._status(snapshot,{})
    assert status['state']=='technical_pause' and not status['display']['active']
    primary=str(ui._primary(token,True,snapshot,status))
    assert 'action="/control/' not in primary
    available=dict(technical,recovery_available=True,supported_actions=['worker-resume'])
    snapshot['operation']=dict(status='applied',kind='controller',result=dict(technical_stop=available))
    primary=str(ui._primary(token,True,snapshot,status))
    assert 'action="/control/resume"' in primary
    assert 'name="step" value="2"' in primary and 'name="expected_attempt" value="1"' in primary
    assert 'action="/control/' not in str(ui._primary(token,False,snapshot,status))
    available['expected_attempt']=0
    assert 'action="/control/' not in str(ui._primary(token,True,snapshot,status))


@pytest.mark.parametrize('expected_attempt',[0,2])
def test_technical_repair_form_posts_exact_attempt_and_binds_retries(tmp_path,monkeypatch,expected_attempt):
    root,key=prepare(tmp_path,enabled=False)
    ui,cookie=session(root,key)
    token=ui._browser(cookie)
    snapshot=ui._snapshot()
    technical=dict(phase='native_prelaunch',stage='technical_pause',pending_step=2,pending_role='W',
        reason='native_metadata_unsupported',repair_scope='worker_attempt',expected_attempt=expected_attempt,
        recovery_available=True,supported_actions=['worker-resume'],recheck_required=True)
    snapshot['view'].update(nodes=[dict(step=2,role='W',state='native_not_submitted',
        continuations=expected_attempt,technical_stop=technical)],paused=False,correction={})
    snapshot.update(conclusion=None,final=None,operation=dict(status='applied',kind='controller',
        result=dict(technical_stop=technical)))
    primary=str(ui._primary(token,True,snapshot,ui._status(snapshot,{})))
    fields={html.unescape(key):html.unescape(value) for key,value in
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">',primary)}
    submitted=[]
    def start(selected,**options):
        assert selected==root
        submitted.append(options)
        return dict(status='accepted',operation_id=options['operation_id'])
    monkeypatch.setattr(control,'start_background',start)
    assert post(ui,cookie,'resume',fields)[0]==303
    assert submitted[0]['resume_step']==2 and submitted[0]['expected_attempt']==expected_attempt
    assert post(ui,cookie,'resume',fields)[0]==303
    assert submitted[0]==submitted[1]
    assert post(ui,cookie,'resume',dict(fields,expected_attempt=str(expected_attempt+1)))[0]==422
    assert len(submitted)==2


def test_service_backoff_presentation_remains_active_and_pausable(tmp_path):
    from scidiscovery.research_loop import ui_view
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    node = dict(step=2, role='W', state='automatic_recovery_waiting',
        automatic_recovery=dict(status='waiting', reason='native_temporary', next_attempt_at='2026-10-26T12:00:00Z'))
    snapshot = ui._snapshot()
    snapshot.update(conclusion=None, final=None)
    snapshot['view'].update(nodes=[node], paused=False, correction={})
    shown = ui._presentation(snapshot, dict(status='running'))
    assert shown['active'] is True and shown['current'] == node
    status = ui._status(snapshot, dict(status='running'))
    assert status['state'] == 'service_wait'
    primary = str(ui._primary(token, True, snapshot, status))
    assert 'action="/control/pause"' in primary
    assert 'action="/control/start"' not in primary and 'action="/control/continue"' not in primary
    assert 'action="/control/' not in str(ui._primary(token, False, snapshot, status))
    snapshot['view']['paused'] = True
    assert ui._presentation(snapshot, {})['active'] is True
    node.update(state='automatic_recovery_paused', automatic_recovery=dict(status='paused'))
    paused = ui._presentation(snapshot, {})
    assert paused['active'] is False
    assert ui_view.status_summary(paused, snapshot['view'])['state'] == 'paused'


def test_permanent_failure_overview_offers_pause_and_exact_current_task_fork(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    snapshot = ui._snapshot()
    node = dict(step=2, role='W', state='automatic_recovery_stopped', returncode=1,
        terminal_observed=False, cleanup='observed_group_cleaned', continuations=0,
        automatic_recovery=dict(status='stopped', reason='automatic_recovery_permanent_failure'),
        native_failure=dict(category='permanent', codes=['insufficient_quota'], statuses=[429],
            message='private-token /private/provider https://private.invalid'))
    snapshot.update(conclusion=None, final=None, operation=None, operations=[])
    snapshot['view'].update(nodes=[node], paused=False, correction={})
    monkeypatch.setattr(ui, '_snapshot', lambda **options: snapshot)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    hero = re.search(r'<div id="live-hero">(.*?)<div id="live-usage">', page, re.S)[1]
    assert '服务错误待处理' in hero and '永久服务错误：服务配额、余额或计费上限不足' in hero
    assert 'insufficient_quota：服务配额、余额或计费上限不足' in hero
    assert '增加本研究的 tokens 预算不会恢复服务账户额度' in hero
    assert 'action="/control/pause"' in hero and 'action="/control/start"' not in hero
    assert 'href="/nodes/2/fork"' in hero and '放弃本次尝试并分支重做' in hero
    assert '不会重跑前一阶段' in hero and '新分支须声明新预算，创建后保持暂停' in hero
    assert '原尝试不删除，已用预算不退还' in hero
    assert 'private-token' not in page and '/private/provider' not in page and 'https://private.invalid' not in page
    assert not (root / 'pause.request').exists()
    _, _, read_headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    read_page = ui.get('/', host=ui.host, cookie=read_headers['Set-Cookie'])[1].decode()
    assert '服务错误待处理' in read_page and '处理建议' in read_page
    assert 'action="/control/' not in read_page and '/nodes/2/fork' not in read_page
    assert 'native-evidence/002/native-outcome.json' not in read_page
    snapshot['view']['paused'] = True
    paused_hero = str(ui._dashboard_parts(ui._browser(cookie), snapshot)['live-hero'])
    assert '研究已暂停' in paused_hero and 'action="/control/continue"' not in paused_hero
    assert 'href="/nodes/2/fork"' in paused_hero


@pytest.mark.parametrize('state, cleanup', [
    ('running', 'observed_group_cleaned'), ('unknown', 'observed_group_cleaned'),
    ('waiting', 'observed_group_cleaned'), ('automatic_recovery_waiting', 'observed_group_cleaned'),
    ('automatic_recovery_stopped', 'unknown'),
])
def test_failure_attention_keeps_active_or_uncertain_execution_out_of_branch_exit(tmp_path, state, cleanup):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    snapshot = ui._snapshot()
    node = dict(step=2, role='W', state=state, returncode=1, cleanup=cleanup,
        native_failure=dict(category='permanent', codes=['invalid_api_key'], statuses=[401]))
    snapshot.update(conclusion=None, final=None, operation=None)
    snapshot['view'].update(nodes=[node], paused=False, correction={})
    status = ui._status(snapshot, {})
    attention = repr(ui._attention(ui._browser(cookie), True, snapshot, status))
    assert '/nodes/2/fork' not in attention and '放弃本次尝试并分支重做' not in attention
    assert '认证失败或凭据无效' in attention


@pytest.mark.parametrize('category, reason, expected', [
    ('unknown', 'automatic_recovery_unknown_failure', '原生错误原因未识别'),
    ('temporary', 'automatic_recovery_retry_limit', '自动接续次数或等待额度已用尽'),
])
def test_confirmed_stopped_failure_keeps_pause_and_fork_preview_available(tmp_path, category, reason, expected):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    snapshot = ui._snapshot()
    node = dict(step=2, role='W', state='automatic_recovery_stopped', returncode=1,
        cleanup='observed_group_cleaned', native_failure=dict(category=category, codes=[], statuses=[]),
        automatic_recovery=dict(status='stopped', reason=reason))
    snapshot.update(conclusion=None, final=None, operation=None)
    snapshot['view'].update(nodes=[node], paused=False, correction={})
    hero = str(ui._dashboard_parts(ui._browser(cookie), snapshot)['live-hero'])
    assert expected in hero and '接续停止待处理' in hero and 'tone-bad' in hero
    assert '永久' not in hero
    assert 'action="/control/pause"' in hero and 'action="/control/start"' not in hero
    assert 'href="/nodes/2/fork"' in hero and '放弃本次尝试并分支重做' in hero
    assert '预览仍会核对在途或未知外部调用' in hero
    assert not (root / 'pause.request').exists()


@pytest.mark.parametrize('workspace_host', [False, True])
@pytest.mark.parametrize('category, code, expected', [
    ('permanent', 'insufficient_quota', '服务错误待处理'),
    ('unknown', None, '接续停止待处理'),
])
def test_serving_failed_task_exit_obeys_standalone_maintenance_handoff(tmp_path, workspace_host, category, code, expected):
    from scidiscovery.research_loop import contracts as c
    from scidiscovery.research_loop.instance_ui import _host_lock
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = root / 'native-evidence/001'
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(record / 'native-outcome.json', dict(returncode=1, native_started=False,
        terminal_observed=False, cleanup='observed_group_cleaned', stop_reason='native_failure_event',
        native_failure=dict(category=category, codes=[code] if code else [], statuses=[])))
    c.write(record / 'automatic-recovery.json', dict(status='stopped',
        reason='automatic_recovery_permanent_failure' if category == 'permanent' else 'automatic_recovery_unknown_failure'))
    if workspace_host:
        host = InstanceUI(tmp_path, secret_file=key, workspace=True, instance_roots=[root])
        host.host = '127.0.0.1:18753'
        ui = host._child(1)
        assert ui.workspace_host is host
    else:
        host = ui = InstanceUI(root, secret_file=key)
        ui.host = '127.0.0.1:18753'
    host._serving = ui._serving = True
    originals = {path: path.read_bytes() for path in (root / 'jobs/001.json',
        record / 'native-intent.json', record / 'native-outcome.json', record / 'automatic-recovery.json')}
    with _host_lock(host.root, workspace=host.workspace):
        _, _, headers = ui.get('/manage?' + urlencode({'token': ui.management_token}), host=ui.host)
        cookie = headers['Set-Cookie']
        overview = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
        hero = re.search(r'<div id="live-hero">(.*?)<div id="live-usage">', overview, re.S)[1]
        detail = ui.get('/nodes/1', host=ui.host, cookie=cookie)[1].decode()
        fork_page = ui.get('/nodes/1/fork', host=ui.host, cookie=cookie)[1].decode()
        assert expected in hero and 'action="/control/pause"' in hero
        if category == 'unknown':
            assert '永久' not in hero
        for page in (hero, detail):
            if workspace_host:
                assert '放弃本次尝试并分支重做' in page and 'href="/nodes/1/fork"' in page
                assert '在工作台打开本实例以预览分支' not in page
            else:
                assert '放弃本次尝试并分支重做' not in page
                assert '在工作台打开本实例以预览分支' in page
                assert '查看工作台分支操作说明' in page and 'href="/nodes/1/fork"' in page
        assert ('action="/control/fork-preview"' in fork_page) is workspace_host
        assert ('分支暂不可用' in fork_page) is not workspace_host
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (root / 'pause.request').exists() and not (record / 'result-identity.json').exists()


@pytest.mark.parametrize('category, code', [
    ('temporary', 'rate_limit_exceeded'), ('incomplete', 'turn_interrupted'),
])
def test_paused_temporary_or_incomplete_failure_keeps_paused_status_and_continue(tmp_path, category, code):
    from scidiscovery.research_loop import ui_view
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    snapshot = ui._snapshot()
    node = dict(step=2, role='W', state='automatic_recovery_paused', returncode=1,
        terminal_observed=False, cleanup='observed_group_cleaned',
        native_failure=dict(category=category, codes=[code], statuses=[429] if category == 'temporary' else []),
        automatic_recovery=dict(status='paused', reason='native_' + category, retry_count=1, continue_count=1))
    snapshot.update(conclusion=None, final=None, operation=None)
    snapshot['view'].update(nodes=[node], paused=True, correction={})
    status = ui._status(snapshot, {})
    assert status['state'] == 'paused' and status['badge'] == '已暂停'
    assert ui_view.native_failure_guidance(node) is None
    hero = str(ui._dashboard_parts(ui._browser(cookie), snapshot)['live-hero'])
    assert 'action="/control/continue"' in hero
    assert '服务错误待处理' not in hero and '接续停止待处理' not in hero
    assert '自动接续已停止' not in hero and '放弃本次尝试并分支重做' not in hero


def session(root, key):
    ui = InstanceUI(root, secret_file=key); ui.host = '127.0.0.1:18753'
    _, _, headers = ui.get('/manage?' + urlencode({'token': ui.management_token}), host=ui.host)
    return ui, headers['Set-Cookie']


def form(ui, cookie, action):
    _, page, _ = ui.get('/', host=ui.host, cookie=cookie)
    original = re.search(r'<form method="post" action="/control/' + action + r'">(.*?)</form>',
                         page.decode(), re.S).group(1)
    return {html.unescape(key): html.unescape(value)
            for key, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', original)}


def control_fields(ui, cookie, action):
    # Direct legacy-control boundary tests do not depend on the ordinary UI
    # exposing an exceptional recovery form.
    token = ui._browser(cookie)
    nonce = ui._form_nonce(token, action)
    return dict(nonce=nonce, csrf=ui._csrf(token, action, nonce))


def post(ui, cookie, action, value):
    return ui.post('/control/' + action, host=ui.host, origin='http://' + ui.host,
        cookie=cookie, content_type='application/x-www-form-urlencoded', body=urlencode(value).encode())


def test_ordinary_instance_status_pages_and_browser_bound_controls(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    finish(root, 2, 'A', [1])
    ui, cookie = session(root, key)
    full = cli.status(root)
    first = control.status(root, limit=1)
    second = control.status(root, after=first['next_after'], limit=1)
    fields = ('step', 'role', 'state')
    assert [tuple(node[key] for key in fields) for node in first['nodes']] == [(1, 'H', 'seal_present_unverified')]
    assert first['next_after'] == 1
    assert [tuple(node[key] for key in fields) for node in second['nodes']] == [(2, 'A', 'seal_present_unverified')]
    assert [{key: node[key] for key in fields} for node in first['nodes'] + second['nodes']] == [
        {key: node[key] for key in fields} for node in full['nodes']]
    assert second['next_after'] is None
    assert ui.get('/nodes/2', host=ui.host, cookie=cookie)[0] == 200
    with pytest.raises(ValueError) as invalid_route:
        ui.get('/nodes/../../operator.key', host=ui.host, cookie=cookie)
    assert ui._error_response(invalid_route.value, '/nodes/../../operator.key')[0] == 404
    with pytest.raises(ValueError, match='审批入口'):
        ui.get('/requests', host=ui.host, cookie=cookie)
    _, _, read_headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    read_cookie = read_headers['Set-Cookie']
    assert b'action="/control/' not in ui.get('/', host=ui.host, cookie=read_cookie)[1]
    value = form(ui, cookie, 'pause')
    with pytest.raises(PermissionError):
        post(ui, read_cookie, 'pause', value)
    with pytest.raises(PermissionError, match='Origin'):
        ui.post('/control/pause', host=ui.host, origin='http://other.invalid', cookie=cookie,
            content_type='application/x-www-form-urlencoded', body=urlencode(value).encode())
    with pytest.raises(PermissionError, match='CSRF'):
        post(ui, cookie, 'pause', dict(value, csrf='forged'))
    assert not (root / 'pause.request').exists()
    for token in (ui.browser.issue('other-instance', maintenance=True),
                  ui.browser.issue(ui.instance, maintenance=True,
                    now=datetime.now(timezone.utc) - timedelta(days=1), lifetime=timedelta(minutes=1))):
        with pytest.raises(PermissionError):
            ui.get('/', host=ui.host, cookie=access_cookie(ui.instance, token))
    restarted, _ = session(root, key)
    with pytest.raises(PermissionError):
        restarted.get('/', host=restarted.host, cookie=cookie)
    assert not (root / 'execution-authority/host-key.json').exists()


def test_final_results_follow_exact_sealed_terminal_route(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    ui, cookie = session(root, key)
    assert '最终综合结论尚未返回' in ui.get('/final-results', host=ui.host, cookie=cookie)[1].decode()
    finish(root, 2, 'A', [1], action='finish')
    assert ui._final_step() == 2
    page = ui.get('/final-results', host=ui.host, cookie=cookie)[1].decode()
    assert '研究发现' in page and '/nodes/2/science' in page
    overview = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert '已完成' in overview and 'href="/final-results" class="primary">查看结果</a>' in overview
    assert '第 1 轮 · A 决定结束探索' in overview


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('log_present', [False, True])
def test_progress_distinguishes_unconfirmed_runner_from_active_empty_log(tmp_path, active, log_present):
    import json
    import os
    from scidiscovery.research_loop import contracts as c, execution, runtime
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1); record.mkdir(parents=True, exist_ok=True)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json'), prepared_at=1.0))
    runner = runtime.process_identity(os.getpid(), c.record_ref(root / 'jobs/001.json'))
    if not active:
        runner['pid'] = 0  # No Linux /proc/0 identity can match this stopped runner.
    c.write(record / 'runner-process.json', runner)
    if log_present:
        (record / 'native.jsonl').write_bytes(b'')
    originals = {path: path.read_bytes() for path in (root / 'jobs/001.json',
        record / 'native-intent.json', record / 'runner-process.json')}
    ui, cookie = session(root, key)
    value = json.loads(ui.get('/nodes/1/events?attempt=0', host=ui.host, cookie=cookie)[1])
    page = ui.get('/nodes/1/progress?attempt=0', host=ui.host, cookie=cookie)[1].decode()
    assert value['state'] == ('running' if active else 'unknown')
    assert value['stream_state'] == ('ready' if log_present else 'missing')
    assert value['events'] == [] and value['progress_label'] in page
    if active:
        assert '正在运行' in value['progress_label']
        assert ('等待助手输出' in value['progress_label']) is (not log_present)
    else:
        assert '运行状态未确认' in value['progress_label'] and '核对原任务' in value['progress_label']
        assert '等待助手输出' not in page and '开始运行后会在这里更新' not in page
        hero = ui._dashboard_parts(ui._browser(cookie), ui._snapshot())['live-hero']
        assert 'action="/control/start"' not in hero
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (record / 'native-outcome.json').exists()
    assert not (record / 'result-identity.json').exists()


@pytest.mark.parametrize('malformed', ['native-intent.json', 'usage.json'])
def test_progress_unavailable_observation_never_claims_prepared_or_running(tmp_path, malformed):
    import json
    import os
    from scidiscovery.research_loop import contracts as c, execution, runtime
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json'), prepared_at=1.0))
    c.write(record / 'runner-process.json', runtime.process_identity(os.getpid(), c.record_ref(root / 'jobs/001.json')))
    ui, cookie = session(root, key)
    (record / malformed).write_bytes(b'{')
    value = json.loads(ui.get('/nodes/1/events?attempt=0', host=ui.host, cookie=cookie)[1])
    assert value['state'] == ('unstarted' if malformed == 'native-intent.json' else 'running')
    assert value['observation_state'] == 'unavailable' and value['stream_state'] == 'missing'
    assert '运行状态暂不可确认' in value['progress_label'] and '核对原任务' in value['progress_label']
    page = ui.get('/nodes/1/progress?attempt=0', host=ui.host, cookie=cookie)[1].decode()
    assert value['progress_label'] in page
    assert all(text not in value['progress_label'] for text in ('尚未开始', '正在运行', '等待助手输出'))
    assert (record / malformed).read_bytes() == b'{'


def test_dashboard_shows_controller_stop_receipt_without_private_reason(tmp_path):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    operation_id = 'd' * 32
    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
        operation_id=operation_id, kind='controller', sequence=1, status='applied',
        result=dict(status='native_stopped', reason='private-stderr-path-and-token')))
    ui, cookie = session(root, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert '本次控制请求已停止推进：原生工作尚未封存阶段结论' in page
    assert '请核对原任务，勿重复提交' in page
    assert '本次控制请求已处理。' not in page and 'private-stderr-path-and-token' not in page


@pytest.mark.parametrize('reason, explanation', [
    ('Original execution lost its verified completion; preserved without replay',
     '原任务缺少可验证的完成记录；现有工作已保留，未重新执行。'),
    ('private-stderr-path-and-token', None),
])
def test_unsealed_controller_receipt_preserves_unknown_task_in_overview_and_live(tmp_path, reason, explanation):
    from scidiscovery.research_loop import contracts as c, execution
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = execution.origin(root, 1)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json'), prepared_at=1.0))
    operation_id = 'd' * 32
    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
        operation_id=operation_id, kind='controller', sequence=1, status='applied',
        result=dict(status='native_outcome_unsealed', reason=reason)))
    originals = {path: path.read_bytes() for path in (root / 'jobs/001.json', record / 'native-intent.json',
        root / 'host-control/operations' / (operation_id + '.json'))}
    ui, cookie = session(root, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    overview_hero = re.search(r'<div id="live-hero">(.*?)<div id="live-usage">', page, re.S).group(1)
    hero = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']['live-hero']
    for rendered in (overview_hero, hero):
        assert '本次控制请求已停止推进' in rendered and 'native_outcome_unsealed' in rendered
        assert '上次任务结果不确定' in rendered and '请核对原任务，勿重复提交' in rendered
        assert 'href="/progress" class="primary">查看原任务进度</a>' in rendered
        assert '本次控制请求已处理。' not in rendered and reason not in rendered
        assert '工作已结束' not in rendered
        if explanation:
            assert explanation in rendered
        else:
            assert '停止说明：' not in rendered
    assert reason not in page
    assert 'action="/control/start"' not in hero
    assert {path: path.read_bytes() for path in originals} == originals
    assert not (record / 'native-outcome.json').exists()
    assert not (record / 'result-identity.json').exists()


@pytest.mark.parametrize('result_status, text', [
    ('seal_pending', '阶段结论封存待修复'),
    ('output_repair_required', '原任务输出待修复'),
    ('output_repair_unavailable', '原任务输出暂无法修复'),
    ('waiting_execution', '本次控制请求已结束，正在等待原实验执行'),
])
def test_dashboard_explains_returned_controller_block_without_private_reason(tmp_path, result_status, text):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    operation_id = 'd' * 32
    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
        operation_id=operation_id, kind='controller', sequence=1, status='applied',
        result=dict(status=result_status, reason='private-stderr-path-and-token')))
    ui, cookie = session(root, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert text in page and result_status in page
    assert '本次控制请求已处理。' not in page and 'private-stderr-path-and-token' not in page
    if result_status == 'waiting_execution':
        assert '本次控制请求已停止推进' not in page and '执行失败' not in page


@pytest.mark.parametrize('kind', ['controller', 'budget'])
def test_dashboard_preserves_ordinary_applied_notice(tmp_path, kind):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    operation_id = 'd' * 32
    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
        operation_id=operation_id, kind=kind, sequence=1, status='applied', result=dict(status='finish')))
    ui, cookie = session(root, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert '本次控制请求已处理。' in page
    assert '本次控制请求已停止推进' not in page


@pytest.mark.parametrize('reason, explanation', [
    ('Original closeout native failed; explicit worker-resume required for the same original task',
     '原收尾任务已失败，未自动重试'),
    ('Original closeout native execution is unknown; reconcile the same original without replay',
     '原收尾任务的完成状态尚未核实'),
    ('worker_fragment_checkpoint_unavailable', '原 W 片段的 checkpoint 缺失'),
    ('/private/native-home/token-value', None),
])
def test_closeout_stop_notice_keeps_same_original_guidance_and_hides_raw_reason(tmp_path, reason, explanation):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    operation_id = 'd' * 32
    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
        operation_id=operation_id, kind='controller', sequence=1, status='applied',
        result=dict(status='closeout_native_unsealed', reason=reason, attempt_step=3)))
    ui, cookie = session(root, key)
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    live = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']['live-hero']
    for shown in (page, live):
        assert 'closeout_native_unsealed' in shown and '请核对同一原任务，勿重复提交' in shown
        assert '原任务步骤：3' in shown
        assert reason not in shown and '原任务输出暂无法修复' not in shown
        if explanation:
            assert explanation in shown
        else:
            assert '停止说明：' not in shown
        if 'failed;' in reason:
            assert 'worker-resume' in shown and '原权限与预算核验' in shown


def test_continue_delegates_to_detached_owner_and_pause_remains_immediate(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    owners = []

    def detached(instance, **options):
        owners.append((instance, options))
        return dict(operation_id='a' * 32, kind='controller', status='running')

    monkeypatch.setattr(control, 'start_background', detached)
    control.request_pause(root)
    continuing = form(ui, cookie, 'continue')
    assert post(ui, cookie, 'continue', continuing)[0] == 303
    assert owners == [(root, dict(continue_run=True, ack_correction=None, authorization_note=None,
        operation_id=ui._operation_id(ui._browser(cookie), 'continue', continuing)))]
    # The UI does not own a controller thread or clear its pause marker; the
    # detached shared control operation owns the actual transition.
    assert not hasattr(ui, 'control_thread')
    pause = form(ui, cookie, 'pause')
    assert post(ui, cookie, 'pause', pause)[0] == 303
    assert post(ui, cookie, 'pause', pause)[0] == 422
    assert (root / 'pause.request').exists()


@pytest.mark.parametrize('ceiling,value,legacy_statement', [
    ('max_steps', '9', None), ('max_tokens', '5000', None),
    ('max_steps', '9', ''), ('max_steps', '9', '   \n'),
    ('max_steps', '9', '  Exact statement from an already open form.\n'),
])
def test_budget_form_queues_exact_limits_and_original_adjustment_text(tmp_path, ceiling, value, legacy_statement):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    note = '  Raise this ceiling for the next bounded work.\n保留原文。\n'
    fields = dict(control_fields(ui, cookie, 'budget'), max_tokens='', max_seconds='', max_steps='', authorization_note=note)
    fields[ceiling] = value
    if legacy_statement is not None:
        fields['authorization_statement'] = legacy_statement
    settings = (root / 'settings.json').read_bytes()
    # A current owner keeps the actual browser request queued at its boundary.
    with controller._writer(root):
        status, _, headers = post(ui, cookie, 'budget', fields)
        assert status == 303 and headers['Location'] == '/operations'
        operations = control.operations(root)
        assert len(operations) == 1 and operations[0]['status'] == 'accepted'
        amendment = operations[0]['amendment']
        assert amendment == dict(max_tokens=5000 if ceiling == 'max_tokens' else None,
            max_seconds=None, max_steps=9 if ceiling == 'max_steps' else None,
            authorization_note=note, authorization_statement=legacy_statement if legacy_statement and legacy_statement.strip() else (
                note if ceiling == 'max_steps' else None))
        assert c.read(root / 'host-control/operations' / (operations[0]['operation_id'] + '.json'))['amendment'] == amendment
    assert (root / 'settings.json').read_bytes() == settings
    assert not (root / 'execution-authority').exists()
    assert post(ui, cookie, 'budget', fields)[0] == 303
    assert len(control.operations(root)) == 1
    ui.translation.close()


@pytest.mark.parametrize('note', ['', '   \n'])
def test_budget_form_rejects_empty_explanation_and_keeps_one_retry_field(tmp_path, note):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    fields = dict(control_fields(ui, cookie, 'budget'), max_tokens='', max_seconds='', max_steps='9', authorization_note=note)
    status, page, _ = post(ui, cookie, 'budget', fields)
    assert status == 422 and not control.operations(root)
    retry = re.search(r'<form method="post" action="/control/budget">(.*?)</form>', page.decode(), re.S).group(1)
    assert retry.count('name="authorization_note"') == 1 and '调整说明' in retry
    assert 'name="authorization_statement"' not in retry and '确认追加预算' in retry
    assert '9' in retry and 'name="csrf"' in retry and 'name="nonce"' in retry
    ui.translation.close()


@pytest.mark.parametrize('extra', ['unknown=value', 'authorization_note=duplicate'])
def test_budget_form_rejects_unknown_or_duplicate_fields_before_queueing(tmp_path, extra):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    fields = dict(control_fields(ui, cookie, 'budget'), max_tokens='', max_seconds='', max_steps='9', authorization_note='Exact bounded adjustment.')
    with pytest.raises(ValueError, match='missing or duplicate fields'):
        ui.post('/control/budget', host=ui.host, origin='http://' + ui.host, cookie=cookie,
            content_type='application/x-www-form-urlencoded', body=(urlencode(fields) + '&' + extra).encode())
    assert not control.operations(root)
    ui.translation.close()


def test_read_cookie_opens_only_sealed_science_and_attachment_members(tmp_path,monkeypatch):
    from test_instance_science import read
    from scidiscovery.research_loop import instance_render as render
    from ui_html import element_texts
    root,key=prepare(tmp_path,enabled=False)
    finish(root,1,'H',[],action='analyze',files={'report.html':'<script>untrusted authored file</script>'})
    ui,_=session(root,key)
    _,_,headers=ui.get('/view?'+urlencode({'token':ui.read_token}),host=ui.host)
    cookie=headers['Set-Cookie']
    page=ui.get('/nodes/1',host=ui.host,cookie=cookie)[1]
    assert b'/nodes/1/science' in page and b'/nodes/1/materials' in page
    assert b'untrusted authored file' not in page
    def no_live_status(*args,**kwargs):
        pytest.fail('Original reading must not depend on live status/clock')
    monkeypatch.setattr(control,'status',no_live_status)
    status,body,headers=ui.get('/nodes/1/science',host=ui.host,cookie=cookie)
    assert status==200 and headers['Content-Type'].startswith('application/json')
    assert read(body)==(root/'native-evidence/001/science-output.json').read_bytes()
    page=ui.get('/nodes/1/materials',host=ui.host,cookie=cookie)[1]
    assert b'/nodes/1/materials/1' in page and b'untrusted authored file' not in page
    assert not any(element_texts(page, 'script'))
    assert b'/assets/translation.js' in page
    _,body,headers=ui.get('/nodes/1/materials/1',host=ui.host,cookie=cookie)
    assert headers['Content-Disposition'].startswith('attachment;')
    assert read(body)==b'<script>untrusted authored file</script>'
    for target in ('/nodes/1/science?path=operator.key','/nodes/1/materials/../../operator.key',
                   '/nodes/1/materials/1?path=native-homes','/nodes/1/materials?after=0&after=1'):
        with pytest.raises(ValueError):ui.get(target,host=ui.host,cookie=cookie)
    with pytest.raises(PermissionError):ui.get('/nodes/1/science',host=ui.host)
    with pytest.raises(PermissionError):ui.get('/nodes/1/materials/1',host='other.invalid',cookie=cookie)


def _materials_session(tmp_path, names):
    from test_instance_science import directory_scene
    root = directory_scene(tmp_path, names)
    key = tmp_path / 'directory-browser.key'
    key.write_bytes(b'fixture-directory-browser-key-32bytes'); key.chmod(0o600)
    ui, _ = session(root, key)
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    return root, ui, headers['Set-Cookie']


def test_material_directories_use_breadcrumbs_encoded_names_and_original_download_indexes(tmp_path):
    from test_instance_science import read
    special = '实验 & %20+<记录>'
    names = ['root-note.txt', 'experiments/call-b/artifacts/curve.csv',
        'experiments/call-a/artifacts/curve.csv', 'experiments/call-a/log.txt',
        special + '/literal%20&+.txt', 'reports/finding.txt']
    root, ui, cookie = _materials_session(tmp_path, names)
    base = '/nodes/1/materials'
    page = ui.get(base, host=ui.host, cookie=cookie)[1].decode()
    assert page.count('class="material-directory"') == 3
    assert page.index('class="material-directory"') < page.index('class="material-file"')
    assert '当前目录共 4 项（子目录与文件），本阶段共 6 份研究文件' in page
    assert 'Saved scientific deliverables' not in page
    assert 'experiments/call-a/artifacts/curve.csv' not in page
    assert '含 3 份研究文件' in page
    encoded = base + '?dir=' + quote(special, safe='')
    assert 'href="' + encoded + '"' in page and html.escape(special) + '/' in page
    folder = ui.get(encoded, host=ui.host, cookie=cookie)[1].decode()
    assert 'aria-current="page">' + html.escape(special) in folder
    assert '>literal%20&amp;+.txt</a>' in folder and base + '/5' in folder
    assert '<记录>' not in folder and str(root) not in folder
    assert 'href="' + base + '">上一级</a>' in folder
    for call, ordinal in [('call-a', 3), ('call-b', 2)]:
        target = base + '?' + urlencode({'dir': 'experiments/' + call + '/artifacts'})
        current = ui.get(target, host=ui.host, cookie=cookie)[1].decode()
        assert 'href="' + base + '/' + str(ordinal) + '">curve.csv</a>' in current
        assert 'href="' + base + '?dir=experiments">experiments</a>' in current
        assert 'href="' + base + '?dir=experiments%2F' + call + '">上一级</a>' in current
        assert 'aria-current="page">artifacts</span>' in current
        original = ui.get(base + '/' + str(ordinal), host=ui.host, cookie=cookie)[1]
        assert read(original) == ('Exact directory fixture: ' + names[ordinal - 1]).encode()
    ui.translation.close()


def test_material_directory_pagination_keeps_folder_context_and_twenty_entry_bound(tmp_path):
    names = []
    for number in range(23):
        names.extend([f'collection/item-{number:02d}.txt', f'collection/folder-{number:02d}/data.csv'])
    _, ui, cookie = _materials_session(tmp_path, names)
    target = '/nodes/1/materials?dir=collection'
    entries = []; pages = 0
    while target:
        page = ui.get(target, host=ui.host, cookie=cookie)[1].decode()
        rows = re.findall(r'<li class="material-(directory|file)"><a href="([^"]+)">([^<]+)</a>', page)
        assert 0 < len(rows) <= 20
        assert '当前目录共 46 项（子目录与文件），本阶段共 46 份研究文件' in page
        assert 'href="/nodes/1/materials">研究文件根目录</a>' in page
        assert 'aria-current="page">collection</span>' in page
        entries.extend(rows); pages += 1
        more = re.search(r'<a href="([^"]+)">查看本目录后续项目</a>', page)
        target = html.unescape(more[1]) if more else None
        if target:
            assert target.startswith('/nodes/1/materials?dir=collection&after=')
        assert pages <= 3
    assert pages == 3 and len(entries) == 46
    assert [kind for kind, _, _ in entries] == ['directory'] * 23 + ['file'] * 23
    assert [name for _, _, name in entries[:23]] == [f'folder-{number:02d}/' for number in range(23)]
    assert [href for _, href, _ in entries[23:]] == ['/nodes/1/materials/' + str(number) for number in range(1, 46, 2)]
    ui.translation.close()


def test_material_directory_empty_invalid_and_unsealed_routes_preserve_read_gates(tmp_path):
    from scidiscovery.research_loop import contracts as c
    root, ui, cookie = _materials_session(tmp_path, [])
    base = '/nodes/1/materials'
    empty = ui.get(base + '?dir=', host=ui.host, cookie=cookie)[1].decode()
    assert '当前目录没有研究文件或子目录' in empty
    assert '当前目录共 0 项（子目录与文件），本阶段共 0 份研究文件' in empty
    for query in ('dir=..', 'dir=%2F', 'dir=.%2Fexperiments', 'dir=experiments%2F..',
        'dir=experiments%5Ccall-a', 'dir=unknown', 'dir=&dir=', 'dir=x&dir=y',
        'dir=&unknown=', 'after=', 'after=0&after=1'):
        with pytest.raises(ValueError):
            ui.get(base + '?' + query, host=ui.host, cookie=cookie)
    with pytest.raises(PermissionError):
        ui.get(base + '?dir=', host=ui.host)
    # Saved directory metadata does not requalify or reopen the science body.
    raw = root / 'native-evidence/001/science-output.json'
    raw.chmod(0o600); raw.write_bytes(b'{}')
    assert ui.get(base + '?dir=', host=ui.host, cookie=cookie)[1].decode() == empty
    ui.translation.close()

    # Use a genuine unsealed node, independent of the synthetic membership fixture.
    unsealed_base = tmp_path / 'unsealed'; unsealed_base.mkdir()
    unsealed_root, key = prepare(unsealed_base, enabled=False)
    c.build_job(unsealed_root, 1, 'H', [])
    unsealed_ui, _ = session(unsealed_root, key)
    _, _, headers = unsealed_ui.get('/view?' + urlencode({'token': unsealed_ui.read_token}), host=unsealed_ui.host)
    with pytest.raises(ValueError, match='missing|unavailable'):
        unsealed_ui.get(base + '?dir=', host=unsealed_ui.host, cookie=headers['Set-Cookie'])
    unsealed_ui.translation.close()


def test_material_directory_navigation_keeps_mounted_instance_scope(tmp_path):
    from test_instance_science import read
    root, standalone, _ = _materials_session(tmp_path, ['experiment & one/curve.csv'])
    standalone.translation.close()
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui = InstanceUI(workspace, secret_file=standalone.secret_file, workspace=True, instance_roots=[root])
    ui.host = '127.0.0.1:18754'
    child = ui._child(1)
    _, _, headers = ui.get('/instances/1/view?' + urlencode({'token':child.read_token}), host=ui.host)
    cookie = headers['Set-Cookie']
    base = '/instances/1/nodes/1/materials'
    page = ui.get(base, host=ui.host, cookie=cookie)[1].decode()
    folder = base + '?dir=experiment%20%26%20one'
    assert 'href="' + folder + '"' in page
    nested = ui.get(folder, host=ui.host, cookie=cookie)[1].decode()
    assert 'href="' + base + '">研究文件根目录</a>' in nested
    assert 'href="' + base + '">上一级</a>' in nested
    assert 'href="' + base + '/1">curve.csv</a>' in nested
    assert 'href="/nodes/' not in nested
    original = ui.get(base + '/1', host=ui.host, cookie=cookie)[1]
    assert read(original) == b'Exact directory fixture: experiment & one/curve.csv'
    with pytest.raises(PermissionError):
        ui.get(base + '?dir=experiment%20%26%20one', host=ui.host)
    ui.translation.close()


@pytest.mark.process_e2e
def test_loopback_science_download_uses_existing_browser_scope_and_safe_headers(tmp_path,monkeypatch):
    import http.client
    from scidiscovery.research_loop import instance_ui
    root,key=prepare(tmp_path,enabled=False)
    authored=b'<svg onload="never execute"/>'
    untrusted=authored.decode()+'<script>window.untrustedFixture=true</script>'
    folder = 'z experiments & %20+试验'
    finish(root,1,'H',[],action='analyze',text=untrusted,
        files={'figure.svg':authored.decode(), folder + '/curve.csv':'x,y\n1,2\n'})
    ui=InstanceUI(root,secret_file=key);ready=threading.Event();servers=[];errors=[]
    server_type=instance_ui.ThreadingHTTPServer
    class Server(server_type):
        def serve_forever(self,**options):
            servers.append(self);ready.set();super().serve_forever(**options)
    monkeypatch.setattr(instance_ui,'ThreadingHTTPServer',Server)
    monkeypatch.setattr(instance_ui,'InstanceUI',lambda *args,**options:ui)
    monkeypatch.setattr(instance_ui,'print',lambda *args,**options:None,raising=False)
    def serve():
        try:instance_ui.serve(root,secret_file=key)
        except BaseException as error:errors.append(error);ready.set()
    worker=threading.Thread(target=serve);worker.start();connection=None
    try:
        assert ready.wait(5) and not errors
        connection=http.client.HTTPConnection('127.0.0.1',servers[0].server_port,timeout=5)
        connection.request('GET','/nodes/1/science')
        response=connection.getresponse();assert response.status==403
        recovery=response.read()
        assert b'management_url' in recovery and b'?token=' not in recovery
        assert b'data-live-url' not in recovery and b'<script>' not in recovery
        connection.request('GET', '/nodes/1/materials?dir=' + quote(folder, safe=''))
        response=connection.getresponse(); assert response.status==403; response.read()
        connection.request('GET','/view?'+urlencode({'token':ui.read_token}))
        response=connection.getresponse();assert response.status==200
        cookie=response.getheader('Set-Cookie').split(';',1)[0];response.read()
        connection.request('GET','/results/1',headers={'Cookie':cookie})
        response=connection.getresponse()
        from scidiscovery.research_loop import instance_render
        assert response.status==200 and "style-src 'self'" in response.getheader('Content-Security-Policy')
        result_page=response.read()
        assert b'/nodes/1/science' in result_page and b'<details>' in result_page
        assert untrusted.encode() not in result_page and html.escape(untrusted).encode() in result_page
        assert b'<svg' not in result_page
        csp=response.getheader('Content-Security-Policy')
        assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
        assert "script-src 'self'" in csp
        scripts=re.findall(rb'<script\b[^>]*src="([^"]+)"[^>]*></script>',result_page)
        assert scripts
        for script in scripts:
            assert script.startswith(b'/assets/')
            connection.request('GET',script.decode(),headers={'Cookie':cookie})
            asset=connection.getresponse()
            assert asset.status==200 and 'javascript' in asset.getheader('Content-Type')
            assert asset.read()
        connection.request('GET', '/nodes/1/materials', headers={'Cookie':cookie})
        response=connection.getresponse()
        assert response.status==200 and response.getheader('X-Content-Type-Options')=='nosniff'
        directory_page=response.read().decode()
        folder_route='/nodes/1/materials?dir=' + quote(folder, safe='')
        assert 'href="' + folder_route + '"' in directory_page
        assert 'class="material-directory"' in directory_page
        connection.request('GET', folder_route, headers={'Cookie':cookie})
        response=connection.getresponse()
        assert response.status==200 and "script-src 'self'" in response.getheader('Content-Security-Policy')
        folder_page=response.read().decode()
        assert 'href="/nodes/1/materials">上一级</a>' in folder_page
        assert 'aria-current="page">' + html.escape(folder) in folder_page
        curve=re.search(r'href="(/nodes/1/materials/[0-9]+)">curve.csv</a>', folder_page)
        assert curve is not None and 'figure.svg' not in folder_page
        connection.request('GET', curve[1], headers={'Cookie':cookie})
        response=connection.getresponse()
        assert response.status==200 and response.getheader('Content-Type')=='application/octet-stream'
        assert response.getheader('Content-Disposition').endswith(quote(folder + '/curve.csv', safe=''))
        assert response.read()==b'x,y\n1,2\n'
        connection.request('GET','/nodes/1/materials/1',headers={'Cookie':cookie})
        response=connection.getresponse()
        assert response.status==200 and response.getheader('Content-Disposition').startswith('attachment;')
        assert response.getheader('Content-Type')=='application/octet-stream'
        assert response.getheader('X-Content-Type-Options')=='nosniff'
        assert "default-src 'none'" in response.getheader('Content-Security-Policy')
        assert response.read()==authored
    finally:
        if connection is not None:connection.close()
        if servers:servers[0].shutdown()
        worker.join(5)
    assert not worker.is_alive() and not errors


def test_settings_index_has_one_entry_for_each_setting(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    page = ui.get('/settings', host=ui.host, cookie=cookie)[1].decode()
    groups = page.split('<div class="settings-groups">', 1)[1]
    assert re.findall(r'href="([^"]+)"', groups) == ['/configuration', '/guidance', '/reading', '/operations']
    overview = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert 'href="/configuration#budget-settings"' in overview
    assert 'action="/control/budget"' not in overview
    configuration = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
    assert 'id="budget-settings"' in configuration
    assert 'href="/#budget"' not in configuration
    assert configuration.count('name="cfg.budget.max_tokens"') == 1
    assert 'action="/control/budget"' not in configuration
    ui.translation.close()


@pytest.mark.parametrize('exhaustion', ['tokens', 'time', 'steps'])
@pytest.mark.parametrize('state,available', [
    ('ordinary', False), ('exhausted', True), ('paused_exhausted', True),
    ('waiting_exhausted', True), ('active_exhausted', False), ('final', True), ('completed_final', True),
])
def test_budget_recovery_has_one_contextual_editor_and_preserves_read_only_boundary(tmp_path, monkeypatch, state, available, exhaustion):
    import copy
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    ui, cookie = session(root, key)
    if state == 'waiting_exhausted':
        finish(root, 2, 'A', [1], action='wait')
    snapshot = copy.deepcopy(ui._snapshot())
    if 'exhausted' in state:
        if exhaustion == 'tokens':
            snapshot['view']['budget']['max_tokens'] = 1
            snapshot['view']['usage']['accounted_tokens'] = 2
        elif exhaustion == 'time':
            snapshot['view']['budget']['seconds_remaining'] = 0
        else:
            snapshot['view']['budget']['max_steps'] = snapshot['ui']['used_steps']
    if state == 'paused_exhausted':
        snapshot['view']['paused'] = True
    if state == 'active_exhausted':
        snapshot['view']['nodes'][-1]['state'] = 'running'
    if state in {'final', 'completed_final'}:
        snapshot['view']['closeout'] = dict(status='final_a_completed' if state == 'completed_final' else 'final_only')
    monkeypatch.setattr(ui, '_snapshot_or_error', lambda **options: snapshot)
    page = ui.get('/budget-recovery', host=ui.host, cookie=cookie)[1].decode()
    assert ('action="/control/budget"' in page) is available
    assert ui._budget_recovery_available(snapshot) is available
    overview = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    assert 'action="/control/budget"' not in overview
    if available:
        assert 'href="/budget-recovery#budget"' in overview
        assert '确认追加预算' in page and '恢复仍须满足原有条件' in page
        assert '生效后返回总览继续研究；提交本表不会启动研究。' in page
    else:
        assert 'href="/configuration#budget-settings"' in page
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    with pytest.raises(PermissionError):
        ui.get('/budget-recovery', host=ui.host, cookie=headers['Set-Cookie'])
    ui.translation.close()


def test_management_configuration_guidance_and_explicit_instance_selection(tmp_path):
    from scidiscovery.research_loop import contracts as c
    from test_research_config import declaration
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    _, _, read_headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    reader = read_headers['Set-Cookie']
    for path in ('/instances', '/configuration', '/new', '/operations'):
        with pytest.raises(PermissionError):
            ui.get(path, host=ui.host, cookie=reader)
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1]
    assert '从下一个新节点生效'.encode() in page and b'operator.key' not in page
    assert b'id="research-config-edit-form"' in page and b'action="/control/configuration-update"' in page
    assert b'id="research-config-form"' not in page and b'action="/control/configure"' not in page
    assert b'id="research-config-form"' in ui.get('/new', host=ui.host, cookie=cookie)[1]
    submit = dict(control_fields(ui, cookie, 'guidance-submit'), text='Check uncertainty', target='W', kind='advice', scope='research', effective_step='')
    submit['csrf'] = ui._csrf(ui._browser(cookie), 'guidance-submit', submit['nonce'])
    assert post(ui, cookie, 'guidance-submit', submit)[2]['Location'] == '/guidance'
    assert b'Check uncertainty' in ui.get('/guidance', host=ui.host, cookie=reader)[1]
    config_dir = tmp_path / 'declarations'; config_dir.mkdir()
    config = declaration(config_dir)
    value = dict(control_fields(ui, cookie, 'configuration'), config_path=str(config), config_text='', mode='prepare')
    value['csrf'] = ui._csrf(ui._browser(cookie), 'configuration', value['nonce'])
    assert post(ui, cookie, 'configuration', value)[0] == 200
    created = config_dir / 'instance'
    assert c.settings(created)['objective'] == 'Compare alternatives and retain limitations.'
    listing = ui.get('/instances', host=ui.host, cookie=cookie)[1]
    assert str(created).encode() in listing and b'/instances/1/manage?token=' in listing
    child = ui._child(1)
    status, _, headers = ui.get('/instances/1/manage?' + urlencode({'token': child.management_token}), host=ui.host)
    assert status == 200 and 'Location' not in headers
    child_cookie = headers['Set-Cookie']
    page = ui.get('/instances/1/', host=ui.host, cookie=child_cookie)[1]
    assert b'action="/instances/1/control/start"' in page
    assert b'href="/instances"' in page
    with pytest.raises(PermissionError):
        ui.get('/instances/1/', host=ui.host, cookie=cookie)


@pytest.mark.process_e2e
def test_real_http_start_survives_disconnect_server_death_and_restart(tmp_path):
    import http.client
    import json
    import os
    from pathlib import Path
    import select
    import subprocess
    import sys
    import time
    from urllib.parse import urlsplit
    from scidiscovery.research_loop import control_operations

    root, key = prepare(tmp_path, enabled=False)
    # A bounded control-only stand-in: it neither invokes a model nor authors a
    # scientific result. The production HTTP/CLI/process ownership path is real.
    shim = tmp_path / 'shim'; shim.mkdir()
    (shim / 'sitecustomize.py').write_text('''
import time
from scidiscovery.research_loop import controller
def bounded_control(root):
    (root / 'entered').touch()
    deadline = time.monotonic() + 20
    while not (root / 'release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    return dict(status='wait', reason='HTTP lifetime fixture')
controller._run = bounded_control
''')
    source = Path(control_operations.__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(shim), str(source)]))
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli', 'serve-ui', '--root', str(root), '--approval-secret-file', str(key)]
    hosts = []

    def start_host():
        process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        hosts.append(process)
        assert select.select([process.stdout], [], [], 8)[0]
        endpoint = json.loads(process.stdout.readline())
        url = urlsplit(endpoint['management_url'])
        connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
        connection.request('GET', url.path + '?' + url.query)
        response = connection.getresponse(); response.read()
        assert response.status == 200
        cookie = response.getheader('Set-Cookie').split(';', 1)[0]
        return process, connection, url.netloc, cookie

    def get_form(connection, cookie):
        connection.request('GET', '/', headers={'Cookie': cookie})
        response = connection.getresponse(); page = response.read().decode()
        assert response.status == 200
        original = re.search(r'<form method="post" action="/control/start">(.*?)</form>', page, re.S).group(1)
        return {html.unescape(key): html.unescape(value) for key, value in
            re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)">', original)}

    connection = None
    try:
        first, connection, host, cookie = start_host()
        fields = get_form(connection, cookie)
        connection.request('POST', '/control/start', body=urlencode(fields), headers={
            'Cookie': cookie, 'Origin': 'http://' + host, 'Content-Type': 'application/x-www-form-urlencoded'})
        # Disconnect the browser without waiting for its control response.
        connection.close()
        deadline = time.monotonic() + 8
        while not (root / 'entered').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (root / 'entered').exists()
        receipt = control_operations.operations(root)[0]
        first.terminate(); first.wait(5)
        assert control_operations.operation(root, receipt['operation_id'])['status'] == 'running'
        second, connection, host, cookie = start_host()
        fields = get_form(connection, cookie)
        connection.request('POST', '/control/start', body=urlencode(fields), headers={
            'Cookie': cookie, 'Origin': 'http://' + host, 'Content-Type': 'application/x-www-form-urlencoded'})
        response = connection.getresponse(); response.read()
        assert response.status == 303
        assert [row['operation_id'] for row in control_operations.operations(root)] == [receipt['operation_id']]
        connection.request('GET', '/operations', headers={'Cookie': cookie})
        response = connection.getresponse()
        assert response.status == 200 and receipt['operation_id'].encode() in response.read()
        second.terminate(); second.wait(5)
        assert control_operations.operation(root, receipt['operation_id'])['status'] == 'running'
    finally:
        if connection is not None: connection.close()
        (root / 'release').touch()
        for process in hosts:
            if process.poll() is None: process.terminate(); process.wait(5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            rows = control_operations.operations(root)
            if rows and rows[0]['status'] not in {'accepted', 'running'}: break
            time.sleep(.02)
    assert rows[0]['status'] == 'applied'
    assert not list(root.glob('**/result-identity.json'))


@pytest.mark.parametrize('backend', ['codex', 'protocol'])
def test_configuration_displays_requested_and_effective_permission_limits(tmp_path, backend):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Explicit permission visibility fixture.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['codex'] if backend == 'codex' else ['operator-launcher'],
        native_backend=backend, native_options=dict(workspace='danger-full-access', network=False, web='disabled', subagents=False),
        max_tokens=100, max_seconds=10, max_steps=2, model='fixture', reasoning_effort=None, service_tier=None,
        experiment_mode='external', search_policy=None)
    key = tmp_path / 'operator.key'; key.write_bytes(b'fixture-key-for-browser-only-32bytes'); key.chmod(0o600)
    ui, cookie = session(root, key)
    page = html.unescape(ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode())
    if backend == 'codex':
        assert 'The requested offline setting is not enforced' in page
        assert '"network": true' in page and '"network": false' in page
        assert '"response_schema_transport": "native_flag"' in page
    else:
        assert '"hint_only"' in page and '"network": "unknown"' in page
        assert '"response_schema_transport": "protocol_request"' in page
        assert '"session_resume": false' in page and '"complete_native_authorship": false' in page
    assert '"native_capabilities"' in page and '"session_resume"' in page
    assert 'operator.key' not in page and 'native_launcher_argv' not in page


def test_configuration_reads_audited_budget_without_rewriting_frozen_settings(tmp_path):
    from scidiscovery.research_loop import budget, contracts as c
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    frozen = (root / 'settings.json').read_bytes()
    budget.amend(root, max_tokens=54321, max_seconds=7654, authorization_note='Review revised ceilings')
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
    current, original = page.split('<summary>查看创建时的原始总上限</summary>')
    assert '54321 tokens' in current and '7654 秒' in current
    assert str(c.settings(root)['max_tokens']) + ' tokens' in original
    assert (root / 'settings.json').read_bytes() == frozen


@pytest.mark.parametrize('change_budget', [False, True])
def test_workspace_configuration_updates_are_queued_after_host_restart(tmp_path, monkeypatch, change_budget):
    from scidiscovery.research_loop import budget, configuration_updates, contracts as c
    from test_configuration_form import Controls
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    stamp = dict(at_unix=1000., at_boottime=10., boot_id='historical-boot')
    monkeypatch.setattr(budget, 'clock_stamp', lambda: dict(stamp))
    c.write(root / 'host-started.json', stamp)
    budget.amend(root, max_tokens=54321, max_seconds=7654, max_steps=9,
                 authorization_note='Retain the audited ceilings across host restart',
                 authorization_statement='Allow nine research nodes')
    budget.transition(root, 'advance', 'An open historical interval')
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui = InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root])
    ui.host = '127.0.0.1:18754'
    child = ui._child(1)
    _, _, headers = ui.get('/instances/1/manage?' + urlencode({'token': child.management_token}), host=ui.host)
    cookie = headers['Set-Cookie']
    originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'settings-identity.json', root / 'host-started.json',
        root / 'jobs/001.json', root / 'host-control/configuration/bindings/000001.json')}
    originals.update({path: path.read_bytes() for path in (root / 'control-ledger').iterdir()
                      if path.is_file()})
    stamp.update(at_unix=2000., at_boottime=1., boot_id='current-boot')

    settings = ui.get('/instances/1/settings', host=ui.host, cookie=cookie)
    assert settings[0] == 200 and b'href="/instances/1/configuration"' in settings[1]
    status, body, _ = ui.get('/instances/1/configuration', host=ui.host, cookie=cookie)
    assert status == 200
    page = body.decode()
    editor = re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', page, re.S).group(1)
    fields = Controls(editor).fields
    assert fields['cfg.budget.max_tokens'] == '54321'
    assert fields['cfg.budget.max_seconds'] == '7654'
    assert fields['cfg.budget.max_steps'] == '9'
    current, original = page.split('<summary>查看创建时的原始总上限</summary>')
    assert '54321 tokens' in current and '7654 秒' in current and '9 阶段' in current
    assert '4000 tokens' in original and '60 秒' in original
    fields['cfg.agent.model'] = 'next-boot-model'
    if change_budget:
        fields['cfg.budget.max_tokens'] = '60000'
        fields['authorization_note'] = 'Queue more comparisons after host restart'
    saved, _, headers = ui.post('/instances/1/control/configuration-update', host=ui.host,
        origin='http://' + ui.host, cookie=cookie, content_type='application/x-www-form-urlencoded',
        body=urlencode(fields).encode())
    assert saved == 303 and headers['Location'] == '/instances/1/configuration'
    state = configuration_updates.status(root)
    assert state['pending'] and state['settings']['model'] == 'next-boot-model'
    assert state['active_settings']['model'] == 'fixture'
    assert state['settings']['max_tokens'] == (60000 if change_budget else 54321)
    assert all(path.read_bytes() == data for path, data in originals.items())
    assert not (root / 'host-control/configuration/bindings/000002.json').exists()
    assert not (root / 'jobs/002.json').exists()


def test_configuration_update_retains_draft_and_applies_only_to_new_nodes(tmp_path):
    from scidiscovery.research_loop import configuration_updates, contracts as c
    from test_configuration_form import Controls
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    c.build_job(root, 1, 'H', [])
    original_job = (root / 'jobs/001.json').read_bytes()
    frozen = (root / 'settings.json').read_bytes()

    def edit_fields():
        markup = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
        editor = re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', markup, re.S).group(1)
        return Controls(editor).fields

    fields = edit_fields()
    fields['cfg.agent.model'] = 'next-node-model'
    fields['cfg.permissions.web'] = 'live'
    fields['cfg.budget.max_tokens'] = 'retain this invalid number'
    fields['authorization_note'] = '  More comparisons <with exact originals>.  '
    status, page, _ = post(ui, cookie, 'configuration-update', fields)
    assert status == 422
    retry = Controls(re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', page.decode(), re.S).group(1)).fields
    assert retry['cfg.agent.model'] == 'next-node-model'
    assert retry['cfg.permissions.web'] == 'live'
    assert retry['cfg.budget.max_tokens'] == 'retain this invalid number'
    assert retry['authorization_note'] == fields['authorization_note']
    assert configuration_updates.status(root)['revision'] == 0
    retry['cfg.budget.max_tokens'] = '4500'
    assert post(ui, cookie, 'configuration-update', retry)[2]['Location'] == '/configuration'
    state = configuration_updates.status(root)
    assert state['pending'] and state['settings']['model'] == 'next-node-model'
    assert state['active_settings']['model'] == 'fixture'
    assert '配置待生效'.encode() in ui.get('/configuration', host=ui.host, cookie=cookie)[1]
    assert (root / 'settings.json').read_bytes() == frozen
    assert (root / 'jobs/001.json').read_bytes() == original_job
    from test_history_handoff import finish
    finish(root, 1, 'H', [], action='wait')
    c.build_job(root, 2, 'H', [])
    assert not configuration_updates.status(root)['pending']
    assert c.read(root / 'jobs/002.json')['configuration_revision']['revision'] == 1


def test_configuration_update_keeps_maintenance_csrf_and_revision_guards(tmp_path):
    from scidiscovery.research_loop import configuration_updates
    from test_configuration_form import Controls
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
    fields = Controls(re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', page, re.S).group(1)).fields
    _, _, headers = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)
    with pytest.raises(PermissionError):
        post(ui, headers['Set-Cookie'], 'configuration-update', fields)
    with pytest.raises(PermissionError, match='CSRF'):
        post(ui, cookie, 'configuration-update', dict(fields, csrf='forged'))
    assert configuration_updates.status(root)['revision'] == 0
    state = configuration_updates.status(root)
    newer = state['configuration']
    newer['agent']['model'] = 'other-tab-model'
    configuration_updates.submit(root, newer, expected_revision=state['revision'])
    fields['cfg.agent.model'] = 'stale-draft-model'
    status, body, _ = post(ui, cookie, 'configuration-update', fields)
    assert status >= 400 and b'stale-draft-model' in body
    assert configuration_updates.status(root)['settings']['model'] == 'other-tab-model'


def test_configuration_enables_explicit_host_and_signed_endpoint_without_starting_call(tmp_path):
    from scidiscovery.research_loop import configuration_updates, execution_authority as authority, instance_ui
    from test_configuration_form import Controls
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    assert not ui.approvals and not (root / 'execution-authority/host-key-source.json').exists()
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
    fields = Controls(re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', page, re.S).group(1)).fields
    fields['cfg.policies.formal_review'] = 'true'
    fields['cfg.policies.execution_authority'] = 'true'
    with instance_ui._host_lock(root):
        ui._serving = True
        assert post(ui, cookie, 'configuration-update', fields)[0] == 303
        assert configuration_updates.status(root)['pending']
        assert ui.approvals and authority.host_secret(root) == key.read_bytes()
        assert authority.review_url(root, 'a' * 64, ui.secret) == 'http://' + ui.host + '/requests/' + 'a' * 64
        assert ui.get('/requests', host=ui.host, cookie=cookie)[0] == 200
    assert not list((root / 'jobs').glob('*.json'))
    assert not list(root.glob('**/accepted.json')) and not list(root.glob('**/submission.json'))
    restarted, restarted_cookie = session(root, key)
    assert restarted.approvals and restarted.get('/requests', host=restarted.host, cookie=restarted_cookie)[0] == 200
    (root / 'host-control/configuration/revisions/000002.json').write_text('{unfinished')
    retained, retained_cookie = session(root, key)
    assert retained.approvals and retained.get('/requests', host=retained.host, cookie=retained_cookie)[0] == 200


def test_configuration_disabling_host_policy_preserves_old_request_decisions_and_restart(tmp_path):
    from scidiscovery.research_loop import configuration_updates, contracts as c, execution_authority as authority
    from test_configuration_form import Controls
    from test_execution_authority import declared_request
    root, secret, arguments = declared_request(tmp_path)
    ref = authority.create_request(root, **arguments)
    request_id = ref['path'].split('/')[-2]
    request_bytes = (root / ref['path']).read_bytes()
    key = tmp_path / 'operator.key'
    ui, cookie = session(root, key)
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1].decode()
    fields = Controls(re.search(r'<form id="research-config-edit-form".*?>(.*?)</form>', page, re.S).group(1)).fields
    fields['cfg.policies.execution_authority'] = 'false'
    assert post(ui, cookie, 'configuration-update', fields)[0] == 303
    assert configuration_updates.current(root).get('execution_authority_policy') is None
    assert ui.get('/requests/' + request_id, host=ui.host, cookie=cookie)[0] == 200
    restarted, cookie = session(root, key)
    assert restarted.approvals
    tokens = authority.form_tokens(root, request_id, secret=secret, boot=restarted.boot,
        browser_token=restarted._browser(cookie))
    def decision(action, values):
        return restarted.post('/requests/' + request_id + '/' + action, host=restarted.host,
            origin='http://' + restarted.host, cookie=cookie, content_type='application/x-www-form-urlencoded',
            body=urlencode(values).encode())
    assert decision('decision', dict(tokens, selected_option=authority.OPTIONS[0], rationale='Decision on the retained original'))[0] == 303
    assert decision('revoke', dict(tokens, rationale='Revoke the retained original'))[0] == 303
    assert authority.status(root, request_id, secret=secret)['state'] == 'revoked'
    assert (root / ref['path']).read_bytes() == request_bytes
    assert c.settings(root)['execution_authority_policy'] is not None
    # Unrelated future damage cannot lock the operator out of old decisions.
    future = root / 'host-control/configuration/revisions/000002.json'
    future.write_text('{unfinished')
    again, cookie = session(root, key)
    assert again.get('/requests/' + request_id, host=again.host, cookie=cookie)[0] == 200


def test_workspace_mounted_child_stays_non_host_after_authority_enable(tmp_path):
    from scidiscovery.research_loop import configuration_updates
    root, key = prepare(tmp_path, enabled=False)
    state = configuration_updates.status(root)
    document = state['configuration']
    document['policies'].update(formal_review=True, execution_authority=True)
    configuration_updates.submit(root, document, expected_revision=state['revision'])
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    ui = InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root])
    ui.host = '127.0.0.1:18754'
    child = ui._child(1)
    assert child.approvals_configured and not child.approvals and not child.approval_host
    child._serving = True
    child._refresh_approval_host()
    assert not (root / 'execution-authority/host-key.json').exists()
    assert not (root / 'execution-authority/host-key-source.json').exists()
    assert not (root / 'execution-authority/ui-endpoint.json').exists()


@pytest.mark.parametrize('overrides', [dict(target='W'), dict(scope='next'), dict(effective_step='')])
def test_goal_change_rejects_incomplete_binding_and_keeps_escaped_draft(tmp_path, overrides):
    from scidiscovery.research_loop import user_guidance
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    draft = dict(text='Compare <script>alert(1)</script> alternatives', target='global', kind='goal_change',
                 scope='research', effective_step='3')
    draft.update(overrides)
    value = dict(draft, **control_fields(ui, cookie, 'guidance-submit'))
    status, page, _ = post(ui, cookie, 'guidance-submit', value)
    assert status == 422 and user_guidance.list_guidance(root) == []
    page = page.decode()
    assert '<script>alert(1)</script>' not in page and html.escape(draft['text']) in page
    assert 'name="effective_step" type="number" min="1" step="1" value="' + draft['effective_step'] + '"' in page
    for field in ('target', 'kind', 'scope'):
        selected = re.search(r'<select name="' + field + r'">(.*?)</select>', page, re.S).group(1)
        assert 'value="' + draft[field] + '" selected' in selected
    # The retry uses the rotated nonce and a matching action/browser CSRF token.
    retry = re.search(r'<form method="post" action="/control/guidance-submit">(.*?)</form>', page, re.S).group(1)
    assert 'name="nonce"' in retry and 'name="csrf"' in retry
    value = dict(text=draft['text'], target='global', kind='goal_change', scope='research', effective_step='3',
        **control_fields(ui, cookie, 'guidance-submit'))
    assert post(ui, cookie, 'guidance-submit', value)[0] == 303
    row = user_guidance.list_guidance(root)[0]
    assert (row['kind'], row['target'], row['scope'], row['effective_step']) == ('goal_change', 'global', 'research', 3)


def test_guidance_read_only_csrf_bound_edits_and_future_revocation(tmp_path):
    from scidiscovery.research_loop import contracts as c, user_guidance
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    reader = ui.get('/view?' + urlencode({'token': ui.read_token}), host=ui.host)[2]['Set-Cookie']
    token = ui._browser(cookie)
    row = user_guidance.submit(root, 'Retain uncertain alternatives', target='E', scope='research')
    value = dict(text='Forged draft', target='global', kind='advice', scope='next', effective_step='',
        **control_fields(ui, cookie, 'guidance-submit'))
    with pytest.raises(PermissionError):
        post(ui, reader, 'guidance-submit', value)
    value['csrf'] = 'invalid-csrf'
    with pytest.raises(PermissionError):
        post(ui, cookie, 'guidance-submit', value)
    assert len(user_guidance.list_guidance(root)) == 1
    # A task can already contain the advice before the application receipt is saved.
    c.write(root / 'jobs/001.json', dict(user_guidance=[row]))
    edit = dict(text='Preserve this <b>rejected edit</b>', guidance_id=row['guidance_id'], expected_revision='1',
        **control_fields(ui, cookie, 'guidance-edit'))
    status, page, _ = post(ui, cookie, 'guidance-edit', edit)
    assert status >= 400 and html.escape(edit['text']).encode() in page
    assert user_guidance.list_guidance(root)[0]['text'] == row['text']
    c.write(root / 'host-control/guidance' / (row['guidance_id'] + '.json'), dict(row, status='applied', applied_step=1, applied_role='E'))
    user_guidance.revoke(root, row['guidance_id'], expected_revision=1)
    page = ui.get('/guidance', host=ui.host, cookie=reader)[1].decode()
    assert 'E · 证据' in page and '第 1 阶段' in page and '从第 2 阶段起' in page
    assert '不表示科学结论已采纳' in page and '<form ' not in page


@pytest.mark.process_e2e
def test_headless_browser_executes_public_progress_poll(tmp_path, monkeypatch):
    import json
    import os
    from pathlib import Path
    import shutil
    import subprocess
    import tempfile
    import time
    from scidiscovery.research_loop import accepted_budget, profiles, instance_ui, contracts as c, execution, runtime, ui_mechanisms, ui_view, ui_translation
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    node = shutil.which('node')
    if not chrome or not node:
        pytest.skip('Optional local Chrome and Node smoke requires existing executables')
    from test_research_config import declaration
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    config = declaration(workspace, '''
[agent.roles.W]
model = "imported-worker-model"
[agent.native.sandbox_workspace_write]
writable_roots = ["runs", "more/runs"]
network_access = false
[executor.config]
plugin = "fixture"
nested = { enabled = true, values = [1, "two", { exact = "retained" }] }
[permissions]
web = "cached"
network = false
''')
    config.write_text(config.read_text().replace('max_tokens = 1000\n', ''))
    root = workspace / 'instance'
    key = tmp_path / 'operator.key'; key.write_bytes(b'operator-key-for-browser-only---32'); key.chmod(0o600)
    record = execution.origin(root, 1)
    owners = []

    def detached(instance, **options):
        owners.append((instance, options))
        c.build_job(instance, 1, 'H', [])
        record.mkdir(exist_ok=True)
        (record / 'native.jsonl').write_text(json.dumps(dict(type='item.completed', item=dict(type='agent_message', text='Earlier browser fixture'))) + '\n')
        return dict(operation_id='a' * 32, kind='controller', status='accepted')

    original_write = c.write
    def fixture_write(path, value, *args, **options):
        # finish() synthesizes these records without starting a native task.
        # State that fact so physical cleanup retains its production checks.
        selected = Path(path)
        if selected.name == 'native-outcome.json' and selected.is_relative_to(root):
            value = dict(value, native_started=False)
        return original_write(path, value, *args, **options)
    monkeypatch.setattr(c, 'write', fixture_write)

    # Browser-only fixture host updates: the signal files never reach a model
    # or solver, and scientific production instances are not used.
    original_snapshot = InstanceUI._snapshot
    advanced = set()
    advance = tmp_path / 'advance-live-ui'
    def fixture_snapshot(selected, **options):
        if selected.root == root and advance.exists():
            stage = advance.read_text()
            if stage not in advanced:
                advanced.add(stage)
                if stage == 'metered':
                    task_ref = c.record_ref(root / 'jobs/001.json')
                    job=c.read_job(root/'jobs/001.json')
                    accepted_budget.accept_native(root,1,record,dict(task_ref=task_ref,prepared_at=1.0,
                        **profiles.binding(c.settings(root),job)))
                    c.write(record / 'runner-process.json', runtime.process_identity(os.getpid(), task_ref))
                elif stage == '2':
                    finish(root, 1, 'H', [], action='analyze', summary='First browser phase finding',preserve_native_intent=True)
                    _, _, output = finish(root, 2, 'A', [1], summary='Second browser phase conclusion')
                    (output / 'native.jsonl').write_text(json.dumps(dict(type='item.completed', item=dict(type='agent_message', text='Second phase public output'))) + '\n')
                elif stage == '3':
                    _, _, output = finish(root, 3, 'H', [2], action='analyze', summary='Third browser phase conclusion')
                    (output / 'native.jsonl').write_text(json.dumps(dict(type='item.completed', item=dict(type='agent_message', text='Third phase public output'))) + '\n')
                elif stage == '4':
                    c.build_job(root, 4, 'W', [3])
                    output = execution.origin(root, 4)
                    job=c.read_job(root/'jobs/004.json')
                    accepted_budget.accept_native(root,4,output,dict(task_ref=c.record_ref(root/'jobs/004.json'),prepared_at=1.0,
                        **profiles.binding(c.settings(root),job)))
                    c.write(output / 'runner-process.json', runtime.process_identity(os.getpid(), c.record_ref(root / 'jobs/004.json')))
                elif stage == 'stopped':
                    output = execution.origin(root, 4)
                    runner = c.read(output / 'runner-process.json'); runner['pid'] = 0
                    c.write(output / 'runner-process.json', runner)
                    operation_id = 'd' * 32
                    c.write(root / 'host-control/operations' / (operation_id + '.json'), dict(
                        operation_id=operation_id, kind='controller', sequence=1, status='applied',
                        result=dict(status='native_stopped', last_step=3, reason='Private fixture diagnostic')))
        snapshot = original_snapshot(selected, **options)
        if selected.root == root and advance.exists() and advance.read_text() in {'3-refresh', '3-link', '3-outside'}:
            # A display-only update exercises focus restoration without another
            # scientific stage or changing any sealed fixture original.
            import copy
            snapshot = copy.deepcopy(snapshot)
            snapshot['ui']['trail']['rounds'][0]['summary'] += ' · Browser heading update ' + advance.read_text()
        if selected.root == root and advance.exists() and advance.read_text() == 'budget-recovery':
            import copy
            snapshot = copy.deepcopy(snapshot)
            snapshot['view']['closeout'] = dict(status='final_a_completed')
        return snapshot
    monkeypatch.setattr(InstanceUI, '_snapshot', fixture_snapshot)
    original_candidates = ui_view.candidate_projection
    def fixture_candidates(selected, step, **options):
        projection = original_candidates(selected, step, **options)
        if selected == root:
            # Synthetic public observations exercise presentation only.
            projection.update(index=[dict(id=f'local-{number:02d}', label=f'Browser candidate {number}',
                metric=None if number == 3 else number / 10, evaluation_key='test' if number in (2, 24) else 'val',
                observation_step=1, status='failed' if number == 4 else 'ok') for number in range(1, 38)],
                nominated_ids=[f'local-{number:02d}' for number in range(1, 7)], graph={}, references={})
        return projection
    monkeypatch.setattr(ui_view, 'candidate_projection', fixture_candidates)
    original_card = ui_view.result_card
    def fixture_card(reader, step, **options):
        card = original_card(reader, step, **options)
        if reader.root == root:
            card.update(chosen=dict(id='local-37', label='Browser candidate 37', metric='3.7',
                evaluation_key='val', direction=None, step=1, reference=None),
                delivery_state='recommended', delivery_label='已推荐未交付')
        return card
    monkeypatch.setattr(ui_view, 'result_card', fixture_card)
    original_mechanisms = ui_mechanisms.project
    def fixture_mechanisms(reader, steps, clean, **options):
        value = original_mechanisms(reader, steps, clean, **options)
        if reader.root == root:
            # Synthetic public presentation records exercise long originals,
            # local table scrolling and replacement of this exact live slot.
            question = 'Does the measured response distinguish this mechanism under the declared conditions? ' + 'Original question detail. ' * 25
            reason = 'Await an independent observation before deciding. ' + 'Original disposition caveat. ' * 25
            refreshed = advance.exists() and advance.read_text() == 'mechanisms-refresh'
            items = []
            for index, label in enumerate(('Transport-driven response', 'Interface-state response'), 1):
                finding = ('Updated public observation. ' if refreshed and index == 1 else 'The observed response remains uncertain. ') + 'Original finding detail. ' * 25
                items.append(dict(ref=f'fixture.m{index}', label=label, question=question,
                    status='deferred', reason=reason, declared_in_window=True, events=[
                        dict(step=1, role='H', label=label, text=question),
                        dict(step=2, role='W', text=None, findings=[dict(basis='observed', text=finding)],
                            calls=None, source_authors=[dict(source_step=1,source_role='H')], metrics=[dict(key='Exact fixture validation protocol', ambiguous_label=False,
                                omitted=0, rows=[dict(label='Fixture candidate with a long original label', metric='0.125',
                                    direction='lower', status='recorded')])]),
                        dict(step=3, role='A', status='deferred', text=reason)]))
            value.update(mechanisms=items, unbound=[], unresolved=[], errors=[], omitted=0, partial=False)
        return value
    monkeypatch.setattr(ui_mechanisms, 'project', fixture_mechanisms)
    monkeypatch.setattr(control, 'start_background', detached)
    ui = InstanceUI(workspace, secret_file=key, workspace=True); ready = threading.Event(); servers = []; errors = []
    ui.translation.configure(dict(enabled=True, model='fixture-reading-model', target_language='zh-CN', launcher_argv=['fixture-custom-wrapper']))
    server_type = instance_ui.ThreadingHTTPServer

    class Server(server_type):
        def serve_forever(self, **options):
            servers.append(self); ready.set(); super().serve_forever(**options)

    monkeypatch.setattr(instance_ui, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(instance_ui, 'InstanceUI', lambda selected, **options: ui if Path(selected) == workspace else InstanceUI(selected, **options))
    monkeypatch.setattr(instance_ui, 'print', lambda *args, **options: None, raising=False)

    def serve():
        try: instance_ui.serve(workspace, secret_file=key, workspace=True)
        except BaseException as error: errors.append(error); ready.set()

    # Chromium's wrapper and crash reporter use XDG directories even with an
    # explicit user-data-dir. Keep those writes inside a short test-owned /tmp
    # tree; the supervisor's long TMPDIR can also exceed Unix socket limits.
    browser_environment = tempfile.TemporaryDirectory(prefix='scid-browser-', dir='/tmp')
    browser_base = Path(browser_environment.name)
    browser_env = dict(os.environ)
    for key, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'),
                      ('XDG_STATE_HOME', 'state'), ('XDG_DATA_HOME', 'data'),
                      ('XDG_RUNTIME_DIR', 'runtime'), ('TMPDIR', 'tmp')):
        directory = browser_base / name; directory.mkdir(mode=0o700)
        browser_env[key] = str(directory)
    log_dir = Path(os.environ.get('SCID_TEST_LOG_DIR', str(tmp_path)))
    log_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = log_dir / 'headless-public-progress.stderr'
    browser_stderr = stderr_path.open('wb')
    thread = threading.Thread(target=serve); thread.start(); browser = None
    try:
        assert ready.wait(5) and not errors
        origin = 'http://' + ui.host
        profile = tmp_path / 'chrome-profile'
        browser = subprocess.Popen([chrome, '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--disable-background-networking', '--disable-component-update', '--disable-extensions',
            '--no-first-run', '--no-default-browser-check',
            '--renderer-process-limit=1', '--disable-features=BackForwardCache',
            '--remote-debugging-port=0', '--user-data-dir=' + str(profile), 'about:blank'],
            stdout=subprocess.DEVNULL, stderr=browser_stderr, env=browser_env, start_new_session=True)
        port_file = profile / 'DevToolsActivePort'
        deadline = time.monotonic() + 10
        while not port_file.exists() and time.monotonic() < deadline and browser.poll() is None:
            time.sleep(.05)
        if not port_file.exists():
            with stderr_path.open('rb') as diagnostic:
                diagnostic.seek(0, os.SEEK_END)
                diagnostic.seek(max(0, diagnostic.tell() - 4096))
                tail = diagnostic.read(4096).decode(errors='replace')
            pytest.fail('Chrome did not expose its local debugging endpoint; returncode='
                + str(browser.poll()) + '; stderr=' + str(stderr_path) + '\n' + tail)
        port = int(port_file.read_text().splitlines()[0])
        script = r'''
import {appendFile, writeFile} from 'node:fs/promises';
import {dirname, join} from 'node:path';
const [port, origin, transport] = process.argv.slice(1);
const managementToken = process.env.SCID_TEST_UI_MANAGEMENT_TOKEN;
const targets = await (await fetch('http://127.0.0.1:' + port + '/json/list')).json();
const ws = new WebSocket(targets.find(target => target.type === 'page').webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0;
const pending = new Map();
ws.onmessage = event => { const message = JSON.parse(event.data); const waiter = pending.get(message.id); if (waiter) { pending.delete(message.id); message.error ? waiter.reject(message.error) : waiter.resolve(message.result); } };
function call(method, params = {}) { return new Promise((resolve, reject) => { const next = ++id; pending.set(next, {resolve, reject}); ws.send(JSON.stringify({id: next, method, params})); }); }
await call('Network.enable');
await call('Emulation.setDeviceMetricsOverride', {width:1280,height:900,deviceScaleFactor:1,mobile:false});
async function evaluate(expression) {
  const answer = await call('Runtime.evaluate', {expression, returnByValue: true});
  if (answer.exceptionDetails) throw new Error('Browser expression failed: ' + (answer.exceptionDetails.exception?.description || answer.exceptionDetails.text));
  return answer.result.value;
}
async function waitFor(expression) {
  for (let index = 0; index < 60; index++) { await new Promise(resolve => setTimeout(resolve, 100)); if (await evaluate(expression)) return; }
  const diagnostic = await evaluate('JSON.stringify({path:location.pathname, typedRowGeometry:window.typedRowGeometry, mobileTypedGeometry:window.mobileTypedGeometry, text:document.body?.innerText?.slice(0,4000)})');
  throw new Error('Browser journey did not reach expected state: ' + expression + '\nPage: ' + diagnostic);
}
async function screenshot(name) {
  if (!process.env.SCID_TEST_LOG_DIR) return;
  const shot = await call('Page.captureScreenshot', {format:'png',captureBeyondViewport:false});
  await writeFile(join(process.env.SCID_TEST_LOG_DIR, name+'.png'), Buffer.from(shot.data,'base64'));
}
async function noOverflow() {
  if (await evaluate('document.documentElement.scrollWidth > innerWidth + 1')) throw new Error('Setup page overflows viewport');
}
await call('Page.navigate', {url: origin + '/manage?token=' + encodeURIComponent(managementToken)});
await waitFor('document.body.innerText.includes("研究工作台")');
await screenshot('setup-workspace');
await evaluate(`document.querySelector('a[href="/new"]').click(); true`);
await waitFor('document.querySelector("textarea[name=task_text]") !== null && document.querySelector("[aria-current=step]") !== null');
if (await evaluate(`document.querySelector('[name="cfg.budget.max_tokens"]').value`) !== '') throw new Error('Missing budget was silently supplied');
if (await evaluate(`document.querySelector('[name="task_text"]').value`) !== '') throw new Error('Workspace task was silently supplied');
if (await evaluate(`document.body.innerText.includes('budget.max_tokens') || document.body.innerText.includes('research.public_root')`)) throw new Error('Ordinary initial screen exposes declaration keys');
// A real missing-file response keeps the chosen path visible and its form usable.
const missingConfig = process.env.SCID_TEST_CONFIG_PATH + '.missing.toml';
await evaluate(`document.querySelector('.setup-tools').open=true;document.querySelector('[name="config_path"]').value=${JSON.stringify(missingConfig)};document.querySelector('form[action="/control/load-config"] button').click();true`);
await waitFor(`document.querySelector('h1')?.textContent==='配置文件不存在' && document.querySelector('.setup-tools').open`);
if (await evaluate(`document.querySelector('[name="config_path"]').value`) !== missingConfig) throw new Error('Missing-config failure lost chosen path');
await screenshot('setup-error-missing-config-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('setup-error-missing-config-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
// Import a nested declaration, then traverse both steps without changing any control.
await evaluate(`document.querySelector('.setup-tools').open=true;document.querySelector('[name="config_path"]').value=${JSON.stringify(process.env.SCID_TEST_CONFIG_PATH)};document.querySelector('form[action="/control/load-config"] button').click();true`);
await waitFor(`document.querySelector('.import-notice') !== null && document.querySelector('[aria-current=step]')?.dataset.setupMarker === '1'`);
const imported = await evaluate(`JSON.stringify(Array.from(new FormData(document.getElementById('research-config-form'))).filter(([name])=>!['nonce','csrf','selection'].includes(name)))`);
await evaluate(`document.querySelector('[data-setup-go="2"]').click();true`);
await waitFor(`document.querySelector('[aria-current=step]')?.dataset.setupMarker === '2'`);
await evaluate(`document.querySelector('[data-setup-go="1"]').click();true`);
const returned = await evaluate(`JSON.stringify(Array.from(new FormData(document.getElementById('research-config-form'))).filter(([name])=>!['nonce','csrf','selection'].includes(name)))`);
if (imported !== returned || !returned.includes('imported-worker-model') || !returned.includes('retained')) throw new Error('Imported nested controls changed across steps');
// Opening more configuration preserves closed categories; imported plugin rows remain compact when expanded.
await evaluate(`document.querySelector('[data-setup-go="2"]').click();document.querySelector('.advanced-settings').open=true;true`);
await waitFor(`(()=>{const categories=Array.from(document.querySelectorAll('.advanced-settings details.advanced-category[data-config-group]'));const groups=new Map(categories.map(group=>[group.dataset.configGroup,group]));const required=['research','project','skills','agent','budget','executor','delivery','policies','ui','version'];return required.every(name=>groups.has(name)) && groups.size===categories.length && categories.every(group=>!group.open && group.querySelector('summary').checkVisibility({contentVisibilityAuto:true})) && !document.querySelector('[name="cfg.agent.backend"]').checkVisibility({contentVisibilityAuto:true})})()`);
await evaluate(`document.querySelectorAll('.advanced-settings details.structured-setting,.advanced-settings details.expert-settings,.advanced-settings details.advanced-category').forEach(group=>group.open=true);true`);
await waitFor(`document.querySelector('[data-parent="@extra"] .config-node').checkVisibility({contentVisibilityAuto:true})`);
await screenshot('config-advanced-imported-before-geometry');
await waitFor(`(()=>{const nodes=Array.from(document.querySelectorAll('[data-parent="@extra"] .config-node'));const scalar=nodes.find(node=>!node.querySelector('.config-value').hidden);const box=element=>element.getBoundingClientRect();const key=box(scalar.querySelector('.config-node-key input')),kind=box(scalar.querySelector('.config-kind')),value=box(scalar.querySelector('.config-value input')),remove=box(scalar.querySelector('.config-remove'));const composite=nodes.find(node=>!node.querySelector('.config-children').hidden && node.querySelector('.config-children').children.length);const children=box(composite.querySelector('.config-children')),parent=box(composite.querySelector('.config-node-key'));window.typedRowGeometry={key,kind,value,remove,children,parent,visible:scalar.checkVisibility({contentVisibilityAuto:true})};return window.typedRowGeometry.visible && key.width>0 && Math.abs(key.top-kind.top)<2 && Math.abs(key.top-value.top)<2 && kind.left>key.left && value.left>kind.left && remove.left>value.left && children.top>=parent.bottom})()`);
await noOverflow(); await screenshot('config-advanced-imported-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await screenshot('config-advanced-imported-mobile-before-geometry');
await waitFor(`(()=>{const nodes=Array.from(document.querySelectorAll('.advanced-settings .config-node'));const details=node=>({id:node.dataset.node,rect:node.getBoundingClientRect(),visible:node.checkVisibility({contentVisibilityAuto:true}),controls:Array.from(node.children).filter(child=>child.matches('label,button')).map(child=>({className:child.className,rect:child.getBoundingClientRect(),control:child.querySelector('input,select')?.getBoundingClientRect()}))});window.mobileTypedGeometry={innerWidth,documentClientWidth:document.documentElement.clientWidth,documentScrollWidth:document.documentElement.scrollWidth,visualViewportWidth:visualViewport?.width,offending:nodes.filter(node=>!node.checkVisibility({contentVisibilityAuto:true}) || node.getBoundingClientRect().right>innerWidth).map(details),nodes:nodes.map(details),wideElements:Array.from(document.querySelectorAll('body *')).filter(node=>node.checkVisibility({contentVisibilityAuto:true}) && node.getBoundingClientRect().right>375).map(node=>({tag:node.tagName,className:node.className,name:node.getAttribute('name'),right:node.getBoundingClientRect().right,clientWidth:node.clientWidth,inMatrixScroll:Boolean(node.closest('.config-matrix-scroll'))})).slice(0,60)};return innerWidth<=375 && nodes.every(node=>node.checkVisibility({contentVisibilityAuto:true}) && node.getBoundingClientRect().right<=innerWidth)})()`);
await noOverflow(); await screenshot('config-advanced-imported-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
// Begin a new draft from this authenticated page so Strict cookies retain a same-site initiator.
await evaluate(`location.assign('/new');true`);
await waitFor(`document.querySelector('[name="task_text"]')?.value === '' && document.querySelector('[aria-current=step]')?.dataset.setupMarker === '1'`);
// An incomplete text attempt must not leave an invalid hidden control when switching to a file.
await evaluate(`document.querySelector('[data-setup-go="2"]').click();document.querySelector('[name="task_mode"]').value='file';document.querySelector('[name="task_mode"]').dispatchEvent(new Event('change',{bubbles:true}));
document.querySelector('[name="cfg.research.objective"]').value=${JSON.stringify(process.env.SCID_TEST_PUBLIC_ROOT+'/objective.md')};
document.querySelector('[name="cfg.research.root"]').value=${JSON.stringify(process.env.SCID_TEST_INSTANCE_ROOT)};
document.querySelector('[name="cfg.research.public_root"]').value=${JSON.stringify(process.env.SCID_TEST_PUBLIC_ROOT)};
document.querySelector('[data-setup-go="2"]').click();true`);
await waitFor(`document.querySelector('[aria-current=step]')?.dataset.setupMarker === '2'`);
if (!(await evaluate(`document.getElementById('research-config-form').checkValidity()`))) throw new Error('Inactive task input blocks submission');
await evaluate(`document.querySelector('[data-setup-go="1"]').click();document.querySelector('[name="task_mode"]').value='text';document.querySelector('[name="task_mode"]').dispatchEvent(new Event('change',{bubbles:true}));document.querySelector('[name="task_text"]').value='Browser direct-form research task.';document.querySelector('[name="task_text"]').dispatchEvent(new Event('input',{bubbles:true}));true`);
if (await evaluate(`document.getElementById('config-edited-note') !== null`)) throw new Error('Fresh draft input shows a stale-preview warning');
await screenshot('setup-task-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('setup-task-mobile');
await evaluate(`document.querySelector('[data-setup-go="2"]').click();true`);
await noOverflow();
await evaluate(`document.querySelector('[name="cfg.agent.model"]').value='browser-fixture-model';document.querySelector('[name="cfg.budget.max_tokens"]').value='not-a-number';document.querySelector('[name="cfg.budget.max_seconds"]').value='120';document.querySelector('[name="cfg.budget.max_steps"]').value='8';true`);
await screenshot('setup-budget-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await evaluate(`document.querySelector('[data-setup-go="1"]').click();true`);
if (await evaluate(`document.querySelector('[name="task_text"]').value`) !== 'Browser direct-form research task.') throw new Error('Back lost task');
await evaluate(`document.querySelector('[data-setup-go="2"]').click();document.querySelector('form[action="/control/configure"] button[value="preview"]').click();true`);
await waitFor(`document.querySelector('.setup-notice') !== null && document.querySelector('[aria-current=step]')?.dataset.setupMarker === '2'`);
if (await evaluate(`document.querySelector('[name="cfg.budget.max_tokens"]').value`) !== 'not-a-number') throw new Error('Validation lost invalid input');
if (await evaluate(`document.querySelector('[name="cfg.agent.model"]').value`) !== 'browser-fixture-model') throw new Error('Validation lost model');
await screenshot('setup-error-budget-desktop');
await evaluate(`document.querySelector('[name="cfg.budget.max_tokens"]').value='1234';true`);
await screenshot('setup-budget-desktop');
// User edits enable optional policy groups; explicit opt-out still wins.
await evaluate(`(()=>{const field=document.getElementById('config-budget-role_shares-min_w_share');field.value='0.65';field.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);
if (!(await evaluate(`document.querySelector('[name="enabled.budget.role_shares"]').checked`))) throw new Error('Editing a policy field silently left its group disabled');
await evaluate(`document.querySelector('[name="enabled.budget.role_shares"]').click();true`);
if (await evaluate(`document.querySelector('[name="enabled.budget.role_shares"]').checked`)) throw new Error('Explicit policy opt-out was overridden');
await evaluate(`document.querySelector('[data-config-path="budget.role_shares"] .expert-settings .config-add').click();true`);
if (!(await evaluate(`document.querySelector('[name="enabled.budget.role_shares"]').checked`))) throw new Error('Nested expert edit failed to enable its policy group');
await evaluate(`(()=>{const group=document.querySelector('[data-config-path="budget.role_shares"]');group.querySelector('.config-node .config-remove').click();group.querySelector('[name="enabled.budget.role_shares"]').click();document.querySelector('.advanced-settings').open=true;document.querySelector('[data-config-group="budget"]').open=true;document.querySelector('[data-config-group="agent.roles"]').open=true;return true})()`);
await noOverflow(); await screenshot('config-role-options-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('config-role-options-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await evaluate(`document.querySelector('.advanced-settings').open=false;true`);
await evaluate(`document.querySelector('form[action="/control/configure"] button[value="preview"]').click();true`);
await waitFor(`document.querySelector('form[action="/control/create"]') !== null && document.querySelector('[aria-current=step]')?.dataset.setupMarker === '3'`);
if (!(await evaluate('document.body.innerText')).includes('Browser direct-form research task.')) throw new Error('Edited task missing from preview');
if (!(await evaluate(`Array.from(document.querySelectorAll('.review-full-path code')).some(path=>path.textContent===${JSON.stringify(process.env.SCID_TEST_INSTANCE_ROOT)}) && Array.from(document.querySelectorAll('.review-full-path code')).some(path=>path.textContent===${JSON.stringify(process.env.SCID_TEST_PUBLIC_ROOT)})`))) throw new Error('Confirmation lost full path identities');
if (await evaluate(`document.querySelector('.review-summary').innerText.includes(${JSON.stringify(process.env.SCID_TEST_INSTANCE_ROOT)})`)) throw new Error('Long absolute path dominates collapsed confirmation');
await evaluate(`document.querySelector('.review-full-path').open=true;true`);
await noOverflow();
await evaluate(`document.querySelector('.review-full-path').open=false;true`);
await screenshot('setup-confirm-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('setup-confirm-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
// Editing after a preview must invalidate Create; a fresh server check restores it.
await evaluate(`document.querySelector('.confirmation [data-setup-go="2"]').click();document.querySelector('[name="cfg.agent.model"]').value='browser-corrected-model';document.querySelector('[name="cfg.agent.model"]').dispatchEvent(new Event('input',{bubbles:true}));true`);
if (!(await evaluate(`document.querySelector('form[action="/control/create"] button').disabled`))) throw new Error('Edited config can still create from stale preview');
await evaluate(`document.querySelector('form[action="/control/configure"] button[value="preview"]').click();true`);
await waitFor(`document.querySelector('[aria-current=step]')?.dataset.setupMarker === '3' && !document.querySelector('form[action="/control/create"] button').disabled && document.querySelector('.confirmation').innerText.includes('browser-corrected-model')`);
await evaluate(`document.querySelector('form[action="/control/create"] button').click(); true`);
await waitFor('location.pathname === "/instances/1/" && document.getElementById("live-hero")?.innerText.includes("未开始") && document.querySelector(\'form[action="/instances/1/control/start"]\') !== null');
if (!(await evaluate(`document.querySelector('h1').textContent.includes('Browser direct-form research task.') && document.getElementById('live-usage').innerText.includes('1,234')`))) throw new Error('Created overview lost the selected task or budget');
await call('Page.navigate', {url:origin+'/instances/1/progress'});
await waitFor(`document.querySelector('[data-stream-status]')?.textContent.includes('尚无研究阶段') && document.querySelector('[data-token-scope]')?.textContent==='当前阶段 · 等待助手用量'`);
if (!(await evaluate(`document.querySelectorAll('[data-token-details] dt').length===0`))) throw new Error('Empty research shows stale token readings');
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.querySelector('form[action="/instances/1/control/start"] button') !== null`);
await evaluate(`document.querySelector('form[action="/instances/1/control/start"] button').click(); true`);
await waitFor('document.body.innerText.includes("已接受请求")');
await call('Page.navigate', {url: origin + '/instances/1/nodes/1/progress?attempt=0'});
let observed = '';
for (let index = 0; index < 60; index++) {
  await new Promise(resolve => setTimeout(resolve, 100));
  const answer = await call('Runtime.evaluate', {expression: 'document.querySelector("[data-stream-status]")?.textContent || ""', returnByValue: true});
  observed = answer.result.value;
  if (observed.includes('已连接')) break;
}
if (!observed.includes('已连接')) throw new Error('Public progress JavaScript did not poll: ' + observed);
if (!observed.includes('阶段已准备，尚未开始')) throw new Error('Prepared phase lifecycle missing: ' + observed);
if (!(await evaluate(`document.querySelector('[data-token-details]').textContent.includes('缓存命中率') && document.querySelector('[data-token-details]').textContent.includes('不可用')`))) throw new Error('Missing token telemetry was not shown explicitly');
if (!(await evaluate(`(()=>{const card=document.querySelector('section.token-monitor'),log=document.querySelector('[data-progress]');return card && !card.closest('.stat') && !card.closest('[data-progress]') && card.parentElement===log.parentElement && Boolean(log.compareDocumentPosition(card)&Node.DOCUMENT_POSITION_FOLLOWING)})()`))) throw new Error('Token monitor must follow the primary public work log as an independent sibling card');
await appendFile(transport, JSON.stringify({type: 'item.completed', item: {type: 'agent_message', text: 'Public browser fixture'}}) + '\n');
let rendered = '';
for (let index = 0; index < 60; index++) {
  await new Promise(resolve => setTimeout(resolve, 100));
  const answer = await call('Runtime.evaluate', {expression: 'document.querySelector("[data-events]")?.textContent || ""', returnByValue: true});
  rendered = answer.result.value;
  if (rendered.includes('Public browser fixture')) break;
}
if (!rendered.includes('Public browser fixture')) throw new Error('Initial fixed output missing');
// The metered attempt has actually started, so aggregate accounting includes it.
await writeFile(process.env.SCID_TEST_ADVANCE, 'metered');
await writeFile(join(dirname(transport), 'usage.json'), JSON.stringify({input_tokens:30,cached_input_tokens:20,output_tokens:2}));
await writeFile(join(dirname(transport), 'usage-observation.json'), JSON.stringify({coverage:'live',context:{status:'last_reported',total_tokens:8,model_context_window:100}}));
await waitFor(`(()=>{const rows=Object.fromEntries(Array.from(document.querySelectorAll('[data-token-details] dt'),label=>[label.textContent,label.nextElementSibling.textContent]));return rows['输入']==='30' && rows['缓存输入']==='20' && rows['非缓存输入']==='10' && rows['输出']==='2' && rows['缓存命中率']==='66.7%' && rows['最近一次上报上下文（阶段 1 / attempt 0）']?.startsWith('8 / 100 · 8.0%')})()`);
await evaluate(`window.tokenPollSentinel='preserved';true`);
await writeFile(join(dirname(transport), 'usage.json'), JSON.stringify({input_tokens:100,cached_input_tokens:80,output_tokens:5}));
await writeFile(join(dirname(transport), 'usage-observation.json'), JSON.stringify({coverage:'live',context:{status:'last_reported',total_tokens:64,model_context_window:128}}));
await waitFor(`(()=>{const rows=Object.fromEntries(Array.from(document.querySelectorAll('[data-token-details] dt'),label=>[label.textContent,label.nextElementSibling.textContent]));return rows['输入']==='100' && rows['缓存输入']==='80' && rows['非缓存输入']==='20' && rows['输出']==='5' && rows['缓存命中率']==='80.0%' && rows['最近一次上报上下文（阶段 1 / attempt 0）']?.startsWith('64 / 128 · 50.0%')})()`);
if (!(await evaluate(`window.tokenPollSentinel==='preserved' && document.querySelector('[data-events]').textContent.includes('Public browser fixture')`))) throw new Error('Token updates reloaded the page or lost public output');
await noOverflow(); await screenshot('token-monitor-progress-desktop');
await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('token-monitor-progress-mobile');
await call('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.querySelector('.token-monitor')?.innerText.includes('80.0%')`);
if (!(await evaluate(`(()=>{const ids=['live-hero','live-usage','live-activity','live-trail','live-token-monitor'];return document.querySelectorAll('.stats > .stat').length===4 && ids.every((id,index)=>!index || document.getElementById(ids[index-1]).nextElementSibling===document.getElementById(id)) && document.querySelector('#live-token-monitor > .token-monitor') && !document.querySelector('#live-usage [data-token-details]')})()`))) throw new Error('Overview token monitor must follow the primary research content');
await noOverflow(); await screenshot('token-monitor-overview-desktop');
await call('Emulation.setDeviceMetricsOverride', {width:390,height:844,deviceScaleFactor:1,mobile:true});
await noOverflow(); await screenshot('token-monitor-overview-mobile');
await call('Emulation.setDeviceMetricsOverride', {width:1440,height:1000,deviceScaleFactor:1,mobile:false});
await call('Page.navigate', {url:origin+'/instances/1/nodes/1/progress?attempt=0'});
await waitFor(`document.querySelector('[data-events]')?.textContent.includes('Public browser fixture') && document.querySelector('[data-token-details]')?.textContent.includes('80.0%')`);
// A permanent progress authorization failure explains recovery and never polls again.
await evaluate(`(()=>{window.progressDeniedCalls=0;const originalFetch=window.fetch;window.fetch=(url,options)=>{if(String(url).includes('/nodes/1/events')){window.progressDeniedCalls++;return Promise.resolve(new Response(JSON.stringify({error:{code:'browser_session_invalid',title:'浏览会话已失效',cause:'进度会话已过期。',hint:'请使用本次 management_url 重新打开。',reference:'progress-fixture-ref'}}),{status:403,headers:{'Content-Type':'application/json'}}))}return originalFetch(url,options)};return true})()`);
await waitFor(`document.querySelector('[data-stream-status]')?.textContent.includes('management_url') && document.querySelector('[data-stream-status]').textContent.includes('progress-fixture-ref')`);
await new Promise(resolve=>setTimeout(resolve,2300));
if (!(await evaluate(`window.progressDeniedCalls===1 && document.querySelector('[data-events]').textContent.includes('Public browser fixture')`))) throw new Error('Expired progress kept polling or lost rendered output');
await screenshot('reading-error-progress-session-expired');
await call('Page.navigate', {url:origin+'/instances/1/progress'});
await waitFor(`document.querySelector('[data-progress-heading]')?.textContent.includes('第 1 阶段') && document.querySelector('[data-events]')?.textContent.includes('Public browser fixture')`);
await writeFile(process.env.SCID_TEST_ADVANCE, '2');
await waitFor(`document.querySelector('[data-progress-heading]')?.textContent.includes('第 2 阶段') && document.querySelector('[data-events]')?.textContent.includes('Second phase public output')`);
if (!(await evaluate(`document.querySelector('[data-token-monitor] [data-token-scope]').textContent.includes('第 2 阶段 · attempt 0') && !document.querySelector('[data-progress] [data-token-details]')`))) throw new Error('Separate token card did not follow the latest phase');
if ((await evaluate('location.pathname')) !== '/instances/1/progress') throw new Error('Following output navigated away');
await screenshot('live-follow-current-node');
await call('Page.navigate', {url:origin+'/instances/1/nodes/1/progress?attempt=0'});
await waitFor(`document.querySelector('[data-events]')?.textContent.includes('Public browser fixture')`);
await waitFor(`document.querySelector('[data-stream-status]')?.textContent.includes('已有阶段结论')`);
if ((await evaluate(`document.querySelector('[data-events]').textContent`)).includes('Second phase public output')) throw new Error('Historical output followed a newer node');
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.getElementById('live-hero')?.innerText.includes('第 2 阶段') && document.querySelector('[data-live-url]')?.dataset.state==='connected'`);
if (!(await evaluate(`!document.querySelector('form[action$="/control/budget"]') && document.querySelector('a[href$="/configuration#budget-settings"]')`))) throw new Error('Ordinary overview duplicates the canonical budget editor');
await writeFile(process.env.SCID_TEST_ADVANCE, 'budget-recovery');
await waitFor(`document.querySelector('#more-actions a[href$="/budget-recovery#budget"]') !== null`);
await evaluate(`document.querySelector('#more-actions a[href$="/budget-recovery#budget"]').click();true`);
await waitFor(`location.pathname.endsWith('/budget-recovery') && document.querySelector('details.budget-adjustment') !== null`);
if (!(await evaluate(`document.querySelector('details.budget-adjustment > summary').textContent==='恢复前追加预算' && document.querySelector('#budget').innerText.includes('恢复仍须满足原有条件')`))) throw new Error('Exceptional recovery is not distinct from next-node settings');
await waitFor(`document.querySelector('details.budget-adjustment').open && document.querySelector('#budget [name="max_tokens"]').checkVisibility({contentVisibilityAuto:true})`);
// Chromium updates the details content-visibility subtree during rendering.
await evaluate(`document.querySelector('details.budget-adjustment').open=false;true`);
await waitFor(`!document.querySelector('details.budget-adjustment').open && !document.querySelector('#budget [name="max_tokens"]').checkVisibility({contentVisibilityAuto:true})`);
await evaluate(`document.querySelector('details.budget-adjustment > summary').click();true`);
await waitFor(`document.querySelector('details.budget-adjustment').open && document.querySelector('#budget [name="max_tokens"]').checkVisibility({contentVisibilityAuto:true})`);
if (!(await evaluate(`document.querySelectorAll('#budget textarea').length===1 && document.querySelector('#budget [name="authorization_note"]').closest('label').innerText.includes('调整说明') && !document.querySelector('#budget [name="authorization_statement"]') && document.querySelector('#budget button').textContent==='确认追加预算'`))) throw new Error('Budget form must use one adjustment explanation and explicit submit confirmation');
await evaluate(`(()=>{document.querySelector('details.budget-adjustment').open=false;const jump=document.createElement('a');jump.href='#budget';jump.textContent='Budget fragment fixture';document.body.append(jump);jump.click();jump.remove();return true})()`);
await waitFor(`document.querySelector('details.budget-adjustment').open && document.querySelector('#budget [name="max_tokens"]').checkVisibility({contentVisibilityAuto:true})`);
await evaluate(`(()=>{window.liveDraftSentinel='preserved';document.querySelector('form[action="/instances/1/control/budget"] [name="max_tokens"]').value='4321';document.querySelector('form[action="/instances/1/control/budget"] [name="authorization_note"]').value='Keep this unsent budget explanation';document.querySelector('form[action="/instances/1/control/budget"] [name="authorization_note"]').focus();return true})()`);
await writeFile(process.env.SCID_TEST_ADVANCE, '3');
await waitFor(`document.getElementById('live-hero')?.innerText.includes('第 3 阶段') && document.getElementById('live-usage')?.innerText.includes('30 / 1,234')`);
if (!(await evaluate(`window.liveDraftSentinel==='preserved' && document.querySelector('form[action="/instances/1/control/budget"] [name="max_tokens"]').value==='4321' && document.querySelector('form[action="/instances/1/control/budget"] [name="authorization_note"]').value==='Keep this unsent budget explanation'`))) throw new Error('Live dashboard lost typed values or reloaded');
if (!(await evaluate(`document.activeElement===document.querySelector('form[action="/instances/1/control/budget"] [name="authorization_note"]')`))) throw new Error('Live recovery context stole keyboard focus from typed budget explanation');
await screenshot('live-budget-recovery-draft-preserved');
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.getElementById('live-trail')?.innerText.includes('Third browser phase conclusion')`);
await evaluate(`(()=>{const round=document.querySelector('#live-trail details[data-round]');window.retainedRound=round.dataset.detailsKey;round.open=false;round.querySelector('summary').focus();return true})()`);
await writeFile(process.env.SCID_TEST_ADVANCE, '3-refresh');
await waitFor(`document.getElementById('live-trail')?.innerText.includes('Browser heading update')`);
if (!(await evaluate(`Array.from(document.querySelectorAll('#live-trail details[data-details-key]')).some(round=>round.dataset.detailsKey===window.retainedRound && !round.open && document.activeElement===round.querySelector('summary'))`))) throw new Error('Live trail lost the reader\'s collapsed round or keyboard summary focus');
await screenshot('live-dashboard-draft-preserved');
await evaluate(`(()=>{const round=Array.from(document.querySelectorAll('#live-trail details[data-details-key]')).find(round=>round.dataset.detailsKey===window.retainedRound);round.open=true;const link=round.querySelector('a');window.retainedLink=link.dataset.liveFocus;link.focus();return true})()`);
await writeFile(process.env.SCID_TEST_ADVANCE, '3-link');
await waitFor(`document.getElementById('live-trail')?.innerText.includes('Browser heading update 3-link')`);
if (!(await evaluate(`document.activeElement?.dataset.liveFocus===window.retainedLink && document.activeElement.isConnected`))) throw new Error('Live trail lost keyboard focus on the same stage link');
await evaluate(`document.querySelector('#more-actions').open=true;document.querySelector('form[action="/instances/1/control/pause"] button').focus();true`);
await writeFile(process.env.SCID_TEST_ADVANCE, '3-outside');
await waitFor(`document.getElementById('live-trail')?.innerText.includes('Browser heading update 3-outside')`);
if (!(await evaluate(`document.activeElement===document.querySelector('form[action="/instances/1/control/pause"] button')`))) throw new Error('Live trail stole keyboard focus from another form');
await call('Page.navigate', {url:origin+'/'});
await waitFor(`document.getElementById('live-instances')?.innerText.includes('第 3 阶段') && document.getElementById('live-instances')?.innerText.includes('30 / 1,234 tokens')`);
await screenshot('live-workbench-loaded-status');
await evaluate(`(()=>{const details=document.querySelector('#live-instances details.review-full-path');details.open=true;window.retainedPath=details.dataset.detailsKey;details.querySelector('summary').focus();return true})()`);
await writeFile(process.env.SCID_TEST_ADVANCE, '4');
await waitFor(`document.getElementById('live-instances')?.innerText.includes('第 4 阶段')`);
if (!(await evaluate(`Array.from(document.querySelectorAll('#live-instances details[data-details-key]')).some(details=>details.dataset.detailsKey===window.retainedPath && details.open && document.activeElement===details.querySelector('summary'))`))) throw new Error('Live workbench lost the expanded exact instance path or summary focus');
await screenshot('live-workbench-path-preserved');
await call('Page.navigate', {url:origin+'/instances/1/results/2'});
await waitFor(`document.querySelector('.result-card') !== null && document.querySelector('.candidates-fold') !== null`);
await evaluate(`document.querySelector('.candidates-fold').open=true;true`);
const initialGroups = await evaluate(`Array.from(document.querySelectorAll('.candidate-group')).map(group=>Array.from(group.querySelectorAll('tbody tr')).map(row=>row.dataset.order))`);
if (initialGroups.length !== 2 || initialGroups.flat().length !== 7) throw new Error('Candidate table did not restrict 37 historical candidates to six nominees and the A choice');
if (!(await evaluate(`document.querySelector('.candidates h2').textContent.includes('（7）') && !document.querySelector('.candidates .toggle-all') && Array.from(document.querySelectorAll('.candidate-table .cand-label')).every(label=>['1','2','3','4','5','6','37'].some(number=>label.textContent==='Browser candidate '+number)) && document.querySelector('.candidate-table .mark-recommended').closest('tr').textContent.includes('Browser candidate 37')`))) throw new Error('Candidate comparison leaked historical alternatives or lost the explicit A choice');
for (const direction of ['desc','asc','order']) {
  await evaluate(`document.querySelector('[data-sort-group]').click();true`);
  const state = await evaluate(`(()=>{const groups=Array.from(document.querySelectorAll('.candidate-group'));const group=groups[0];return {order:groups.map(item=>Array.from(item.querySelectorAll('tbody tr')).map(row=>row.dataset.order)),metrics:Array.from(group.querySelectorAll('tbody tr')).map(row=>row.dataset.metric===''?null:Number(row.dataset.metric)),state:group.querySelector('[data-sort-group]').dataset.state,neutral:group.querySelector('[data-sort-note]').hidden}})()`);
  if (state.state !== direction || JSON.stringify(state.order[1]) !== JSON.stringify(initialGroups[1])) throw new Error('Candidate sort crossed evaluation groups');
  if (direction === 'order') {
    if (JSON.stringify(state.order) !== JSON.stringify(initialGroups) || !state.neutral) throw new Error('Candidate sort did not restore neutral observation order');
  } else {
    const numeric = state.metrics.filter(value=>value!==null);
    if (state.neutral || numeric.some((value,index)=>index>0 && (direction==='desc'?numeric[index-1]<value:numeric[index-1]>value)) || state.metrics.at(-1)!==null) throw new Error('Candidate numeric ordering or missing-metric retention failed');
  }
}
await screenshot('result-candidates-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow();
if (!(await evaluate(`parseFloat(getComputedStyle(document.querySelector('.candidate-table')).fontSize)>=13 && document.querySelector('.result-card').getBoundingClientRect().right<=innerWidth && document.querySelector('.table-scroll').scrollWidth>document.querySelector('.table-scroll').clientWidth`))) throw new Error('Mobile result or candidate table is unreadable');
await screenshot('result-candidates-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await call('Page.navigate', {url:origin+'/instances/1/progress'});
await waitFor(`document.querySelector('[data-progress-heading]')?.textContent.includes('第 4 阶段') && document.querySelector('[data-stream-status]')?.textContent.includes('正在运行；等待助手输出')`);
await writeFile(process.env.SCID_TEST_ADVANCE, 'stopped');
await waitFor(`document.querySelector('[data-stream-status]')?.textContent.includes('运行状态未确认')`);
if ((await evaluate(`document.querySelector('[data-stream-status]').textContent`)).includes('等待助手输出')) throw new Error('Stopped runner still claims to wait for output');
await screenshot('live-progress-stopped-unconfirmed');
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.getElementById('live-hero')?.innerText.includes('本次控制请求已停止推进') && document.getElementById('live-hero')?.innerText.includes('原任务状态待核对')`);
if ((await evaluate('document.body.innerText')).includes('Private fixture diagnostic')) throw new Error('Private stop diagnostic was rendered');
await call('Page.navigate', {url:origin+'/instances/1/results'});
await waitFor(`document.querySelector('#live-mechanisms .mechanism-trails') !== null && document.querySelector('[data-live-url]')?.dataset.state==='connected'`);
if ((await evaluate('location.pathname')) !== '/instances/1/results') throw new Error('Research process live connection changed the selected route');
if (!(await evaluate(`(()=>{const cards=Array.from(document.querySelectorAll('.mechanism-card'));const [first,second]=cards.map(card=>card.getBoundingClientRect());return cards.length===2 && Math.abs(first.top-second.top)<1 && second.left>first.right && cards.every(card=>!card.querySelector('details').open) && !document.querySelector('.mechanism-overview').innerText.includes('Original question detail')})()`))) throw new Error('Desktop mechanisms are not separate, concise, collapsed cards in two columns');
await noOverflow();
await screenshot('research-process-mechanisms-live');
await evaluate(`(()=>{const cards=document.querySelectorAll('.mechanism-card');window.neighborCardHeight=cards[1].getBoundingClientRect().height;window.previousMechanism=cards[0].querySelector('details');window.previousMechanism.open=true;window.previousMechanism.querySelector('summary').focus();return true})()`);
if (!(await evaluate(`Math.abs(document.querySelectorAll('.mechanism-card')[1].getBoundingClientRect().height-window.neighborCardHeight)<1 && document.querySelector('.mechanism-details').innerText.includes('Original disposition caveat')`))) throw new Error('Opening a mechanism stretches its neighbor or hides the full A reason');
// A custom wrapper cannot claim native read-only/offline protection; reads retain originals.
await waitFor(`document.querySelector('[data-translation-start]').disabled && document.querySelector('[data-translation-status]').textContent.includes('原生只读与禁网')`);
await writeFile(process.env.SCID_TEST_ADVANCE, 'mechanisms-refresh');
await waitFor(`!window.previousMechanism.isConnected && document.querySelector('.mechanism-details').open && document.activeElement===document.querySelector('.mechanism-details > summary') && document.querySelector('.mechanism-overview').innerText.includes('Updated public observation.')`);
await waitFor(`document.querySelector('.mechanism-card h3').textContent==='Transport-driven response'`);
if (!(await evaluate(`document.querySelector('.mechanism-overview').textContent.includes('Updated public observation.') && !Array.from(document.querySelectorAll('[data-translation-source]')).find(node=>JSON.parse(node.dataset.translationSource).startsWith('Updated public observation.'))?.textContent.startsWith('<b>译文</b>')`))) throw new Error('New live source used an old translation or launched automatically');
await screenshot('research-process-mechanisms-expanded-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow();
if (!(await evaluate(`(()=>{const cards=Array.from(document.querySelectorAll('.mechanism-card')).map(card=>card.getBoundingClientRect());const table=document.querySelector('.mechanism-details .table-scroll');return cards[1].top>=cards[0].bottom && Math.abs(cards[0].left-cards[1].left)<1 && cards.every(card=>card.right<=innerWidth) && table.scrollWidth>table.clientWidth})()`))) throw new Error('Mobile mechanism cards or expanded metric table overflow their own column');
await screenshot('research-process-mechanisms-expanded-mobile');
await evaluate(`document.querySelector('.mechanism-details').open=false;true`);
await noOverflow();
await screenshot('research-process-mechanisms-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await call('Page.reload');
await waitFor(`document.querySelector('.mechanism-card h3')?.textContent==='Transport-driven response'`);
// Settings edits subsequent research while independent creation remains available.
await call('Page.navigate', {url:origin+'/instances/1/settings'});
await waitFor(`document.querySelector('.settings-groups') !== null`);
if (!(await evaluate(`(()=>{const links=Array.from(document.querySelectorAll('.settings-list a'));return links.filter(link=>link.getAttribute('href').endsWith('/configuration')).length===1 && links.filter(link=>link.getAttribute('href').endsWith('/guidance')).length===1 && links.filter(link=>link.getAttribute('href').endsWith('/reading')).length===1 && !links.some(link=>{const href=link.getAttribute('href');return ['new','instances','progress'].some(route=>href.endsWith('/'+route)) || href.includes('#budget') || href.includes('after=')})})()`))) throw new Error('Settings still duplicates workspace, research records or budget editing entries');

await noOverflow();
await screenshot('settings-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow();
await screenshot('settings-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await call('Page.navigate', {url:origin+'/instances/1/configuration'});
await waitFor(`document.querySelector('.configuration-roles') !== null`);
if (await evaluate(`document.querySelector('#research-config-form') !== null || document.querySelector('form[action$="/control/configure"]') !== null`)) throw new Error('Current configuration still opens the creation wizard');
if (!(await evaluate(`document.querySelector('#budget-settings [name="cfg.budget.max_tokens"]') && document.querySelectorAll('[name="cfg.budget.max_tokens"]').length===1 && !document.querySelector('form[action$="/control/budget"]')`))) throw new Error('Research configuration does not provide one canonical budget editor');
if (!(await evaluate(`(()=>{const matrix=document.querySelector('[data-config-group="agent.roles"] .role-settings-matrix');return matrix && matrix.querySelectorAll('tbody tr').length===4 && !document.querySelector('.role-settings-card')})()`))) throw new Error('Role controls are not consolidated into one matrix');
await evaluate(`document.querySelector('.advanced-settings').open=true;true`);
await waitFor(`(()=>{const categories=Array.from(document.querySelectorAll('.advanced-settings details.advanced-category[data-config-group]'));const groups=new Map(categories.map(group=>[group.dataset.configGroup,group]));const required=['agent','budget','executor','delivery','policies'],frozen=['research','project','skills','ui','version'];return required.every(name=>groups.has(name)) && frozen.every(name=>!groups.has(name)) && groups.size===categories.length && categories.every(group=>!group.open && group.querySelector('summary').checkVisibility({contentVisibilityAuto:true})) && !document.querySelector('[name="cfg.budget.breadth.initial_calls"]').checkVisibility({contentVisibilityAuto:true})})()`);
await evaluate(`['agent','budget','executor','policies'].forEach(prefix=>document.querySelector('.advanced-category[data-config-group="'+prefix+'"]').open=true);true`);
await waitFor(`(()=>{const advanced=document.querySelector('.advanced-settings');const paths=['agent.backend','agent.native.features.fast_mode','budget.breadth.initial_calls','budget.correction.pause_after','budget.role_shares.min_w_share','executor.mode','executor.config.executor','policies.formal_review','policies.resume.max_attempts','policies.resume.output_only'];return paths.every(path=>advanced.querySelector('[name="cfg.'+path+'"]').checkVisibility({contentVisibilityAuto:true})) && !document.querySelector('[name="cfg.budget.max_output_item_bytes"]') && !document.querySelector('[name="cfg.budget.max_output_total_bytes"]')})()`);
await waitFor(`(()=>{const boxes=['max_w_seconds','max_w_observed_tokens','max_requested_repairs','initial_calls'].map(key=>document.querySelector('[name="cfg.budget.breadth.'+key+'"]').getBoundingClientRect());return boxes.every((box,index)=>box.width>100 && Math.abs(box.top-boxes[0].top)<2 && (!index || box.left>boxes[index-1].right))})()`);
await noOverflow();
await screenshot('current-configuration-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow();
await screenshot('current-configuration-mobile');
// A failed settings save keeps the full draft and corrected submission becomes pending.
await evaluate(`(()=>{const form=document.getElementById('research-config-edit-form');form.elements['cfg.agent.model'].value='browser-next-model';form.elements['cfg.budget.candidate_extra_slots'].value='invalid settings budget';document.querySelector('.advanced-category[data-config-group="budget"]').open=false;document.querySelector('.advanced-settings').open=false;form.querySelector('button[type="submit"]').click();return true})()`);
await waitFor(`document.querySelector('[role="alert"]') !== null && document.querySelector('[name="cfg.budget.candidate_extra_slots"]')?.value==='invalid settings budget'`);
if (!(await evaluate(`document.querySelector('[name="cfg.agent.model"]').value==='browser-next-model' && !document.querySelector('[name="cfg.research.root"]') && !document.querySelector('[name="cfg.ui.approval_secret_file"]')`))) throw new Error('Settings error lost draft or exposed instance identity controls');
if (!(await evaluate(`document.querySelector('.advanced-settings').open && document.querySelector('.advanced-category[data-config-group="budget"]').open && document.activeElement===document.querySelector('[name="cfg.budget.candidate_extra_slots"]')`))) throw new Error('Settings validation did not open and focus the failed category');
await evaluate(`document.querySelector('.advanced-category[data-config-group="policies"]').open=true;true`);
await waitFor(`document.querySelector('[name="cfg.budget.candidate_extra_slots"]').value==='invalid settings budget' && document.querySelector('[name="cfg.policies.resume.max_attempts"]').checkVisibility({contentVisibilityAuto:true})`);
await noOverflow(); await screenshot('current-configuration-error-mobile');
await evaluate(`(()=>{const form=document.getElementById('research-config-edit-form');form.elements['cfg.budget.max_tokens'].value='1234';form.elements['cfg.budget.candidate_extra_slots'].value='3';document.querySelector('.advanced-category[data-config-group="budget"]').open=false;document.querySelector('.advanced-settings').open=false;form.querySelector('button[type="submit"]').click();return true})()`);
await waitFor(`document.querySelector('.configuration-status')?.innerText.includes('配置待生效') && document.querySelector('[name="cfg.agent.model"]')?.value==='browser-next-model'`);
await evaluate(`document.querySelector('.advanced-settings').open=true;document.querySelector('.advanced-category[data-config-group="budget"]').open=true;true`);
await waitFor(`document.querySelector('[name="cfg.budget.candidate_extra_slots"]').value==='3' && document.querySelector('[name="cfg.budget.candidate_extra_slots"]').checkVisibility({contentVisibilityAuto:true})`);
await noOverflow(); await screenshot('current-configuration-pending-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
await call('Page.navigate', {url:origin+'/instances/1/guidance'});
await waitFor(`document.querySelector('[data-guidance-template]') !== null`);
await evaluate(`document.querySelector('[data-guidance-template]').click();true`);
if (!(await evaluate(`document.querySelector('[name="text"]').value.length > 0 && document.querySelectorAll('.guidance-record').length === 0`))) throw new Error('Guidance template did not fill an unsubmitted draft');
await evaluate(`document.querySelector('[name="text"]').value='Keep my unsent <b>draft</b>';document.querySelectorAll('[data-guidance-template]')[1].click();true`);
if (await evaluate(`document.querySelector('[name="text"]').value`) !== 'Keep my unsent <b>draft</b>') throw new Error('Guidance template overwrote typed draft');
await evaluate(`(()=>{const form=document.querySelector('form[action$="/control/guidance-submit"]');form.elements.kind.value='goal_change';form.elements.kind.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
if (!(await evaluate(`(()=>{const form=document.querySelector('form[action$="/control/guidance-submit"]'),data=new FormData(form);return data.get('kind')==='goal_change' && data.get('target')==='global' && data.get('scope')==='research' && form.elements.effective_step.required && !form.checkValidity()})()`))) throw new Error('Explicit goal change lost required global/research/step binding');
await screenshot('guidance-goal-change-desktop');
await evaluate(`(()=>{const form=document.querySelector('form[action$="/control/guidance-submit"]');form.elements.effective_step.value='0';form.noValidate=true;form.requestSubmit();return true})()`);
await waitFor(`document.querySelector('[role="alert"]') !== null && document.querySelector('[name="effective_step"]')?.value==='0'`);
if (await evaluate(`document.querySelector('[name="text"]').value`) !== 'Keep my unsent <b>draft</b>') throw new Error('Guidance validation lost original draft');
if (await evaluate(`document.querySelector('[name="kind"]').value`) !== 'goal_change') throw new Error('Guidance failure silently converted goal change to advice');
await noOverflow();
await screenshot('guidance-error-desktop');
await call('Emulation.setDeviceMetricsOverride',{width:375,height:812,deviceScaleFactor:1,mobile:true});
await noOverflow();
await screenshot('guidance-error-mobile');
await call('Emulation.setDeviceMetricsOverride',{width:1280,height:900,deviceScaleFactor:1,mobile:false});
// Natural-language settings work in the browser; switching languages never generates.
await call('Page.navigate', {url:origin+'/instances/1/reading'});
await waitFor(`document.querySelector('[name="target_language"]') !== null`);
await evaluate(`document.querySelector('[name="target_language"]').value='ja';document.querySelector('form[action$="/translation/configure"]').requestSubmit();true`);
await waitFor(`document.querySelector('[name="target_language"]')?.value==='ja' && document.querySelector('[data-translation-status]')?.textContent.includes('ja')`);
await call('Page.navigate', {url:origin+'/instances/1/results'});
await waitFor(`document.querySelector('.mechanism-card h3')?.textContent==='Transport-driven response' && document.querySelector('[data-translation-start]').disabled`);
await call('Page.navigate', {url:origin+'/instances/1/reading'});
await waitFor(`document.querySelector('[name="target_language"]')?.value==='ja'`);
await evaluate(`document.querySelector('[name="target_language"]').value='zh-CN';document.querySelector('form[action$="/translation/configure"]').requestSubmit();true`);
await waitFor(`document.querySelector('[name="target_language"]')?.value==='zh-CN' && document.querySelector('[data-translation-status]')?.textContent.includes('zh-CN')`);
await evaluate(`document.querySelector('[name="timeout_seconds"]').value='301';document.querySelector('form[action$="/translation/configure"]').requestSubmit();true`);
await waitFor(`document.querySelector('h1')?.textContent==='翻译超时设置无效' && document.querySelector('[name="timeout_seconds"]').closest('details').open`);
if (await evaluate(`document.querySelector('[name="target_language"]').value`) !== 'zh-CN') throw new Error('Translation failure lost reading language');
await screenshot('reading-error-timeout-desktop');
await evaluate(`document.querySelector('[name="timeout_seconds"]').value='120';document.querySelector('form[action$="/translation/configure"]').requestSubmit();true`);
await waitFor(`document.querySelector('h1')?.textContent==='阅读与翻译'`);
await call('Page.navigate', {url:origin+'/instances/1/'});
await waitFor(`document.getElementById('live-hero')?.innerText.includes('本次控制请求已停止推进') && document.querySelector('[data-live-url]')?.dataset.state==='connected'`);
await evaluate(`(()=>{const note=document.querySelector('[name="authorization_note"]');note.value='Keep this expired-session draft';window.liveAuthChecks=0;const originalFetch=window.fetch;window.fetch=(url,options)=>{if(String(url).endsWith('/session')){window.liveAuthChecks++;window.checkedSessionURL=String(url);return Promise.resolve(new Response(JSON.stringify({error:{code:'browser_session_invalid',title:'浏览会话已失效',cause:'本地会话已过期。',hint:'请使用本次 management_url 重新打开。',reference:'live-fixture-ref'}}),{status:403,headers:{'Content-Type':'application/json'}}))}return originalFetch(url,options)};source.dispatchEvent(new Event('error'));return true})()`);
await waitFor(`document.querySelector('[data-live-url]')?.dataset.state==='expired' && document.querySelector('[data-live-url]').textContent.includes('management_url') && document.querySelector('[data-live-url]').textContent.includes('live-fixture-ref')`);
await new Promise(resolve=>setTimeout(resolve,1700));
if (!(await evaluate(`source===null && window.liveAuthChecks===1 && window.checkedSessionURL==='/instances/1/session' && document.querySelector('[name="authorization_note"]').value==='Keep this expired-session draft'`))) throw new Error('Expired SSE retained reconnect or lost draft/mounted session route');
await screenshot('reading-error-live-session-expired');
ws.close();
if (rendered.includes('{\"type\"')) throw new Error('Raw JSON appeared in the normal progress panel');
if (!rendered.includes('Public browser fixture')) throw new Error('Public event did not render after append: ' + rendered);
console.log(observed);
console.log(rendered);
'''
        checked = subprocess.run([node, '--input-type=module', '-e', script, str(port), origin, str(record / 'native.jsonl')],
            env=dict(os.environ, SCID_TEST_UI_MANAGEMENT_TOKEN=ui.management_token,
                SCID_TEST_INSTANCE_ROOT=str(root), SCID_TEST_PUBLIC_ROOT=str(workspace / 'public'),
                SCID_TEST_CONFIG_PATH=str(config), SCID_TEST_LOG_DIR=str(log_dir), SCID_TEST_ADVANCE=str(advance)),
            capture_output=True, text=True, timeout=60)
        assert checked.returncode == 0, checked.stderr
        assert '已连接' in checked.stdout
        assert 'Public browser fixture' in checked.stdout
        assert len(owners) == 1 and owners[0][0] == root
        assert set(owners[0][1]) == {'operation_id'} and re.fullmatch('[0-9a-f]{32}', owners[0][1]['operation_id'])
        assert c.settings(root)['max_tokens'] == 1234
    finally:
        if browser is not None:
            import signal
            try: os.killpg(browser.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: browser.wait(5)
            except subprocess.TimeoutExpired:
                os.killpg(browser.pid, signal.SIGKILL); browser.wait(5)
        if servers: servers[0].shutdown()
        thread.join(5)
        browser_stderr.close()
        browser_environment.cleanup()
    assert not thread.is_alive() and not errors


def test_independent_form_nonces_expiry_replay_and_session_binding_keep_drafts(tmp_path, monkeypatch):
    from scidiscovery.research_loop import instance_ui
    from test_workspace_ui import controls
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    page = ui.get('/configuration', host=ui.host, cookie=cookie)[1]
    draft = controls(page, 'configuration-update')
    draft['authorization_note'] = 'Retain my editing explanation <draft>'
    captured = []
    monkeypatch.setattr(ui, '_control_post', lambda browser, action, form: (captured.append((action, dict(form))) or (303, b'', {'Location': '/'})))
    # Another tab consumes its own action; the configuration form stays valid.
    pause = control_fields(ui, cookie, 'pause')
    assert post(ui, cookie, 'pause', pause)[0] == 303
    assert post(ui, cookie, 'configuration-update', draft)[0] == 303
    assert len(captured) == 2
    replay = post(ui, cookie, 'configuration-update', draft)
    assert replay[0] == 422 and 'Retain my editing explanation &lt;draft&gt;'.encode() in replay[1]
    assert len(captured) == 2
    # Expired credentials retain parsed content and issue fresh credentials.
    fresh = controls(ui.get('/configuration', host=ui.host, cookie=cookie)[1], 'configuration-update')
    fresh['authorization_note'] = 'Expired draft survives'
    ui.form_nonce_ttl = -1
    expired = post(ui, cookie, 'configuration-update', fresh)
    assert expired[0] == 422 and b'Expired draft survives' in expired[1]
    assert len(captured) == 2
    ui.form_nonce_ttl = 3600
    other_cookie = ui.get('/manage?' + urlencode({'token': ui.management_token}), host=ui.host)[2]['Set-Cookie']
    with pytest.raises(PermissionError):
        post(ui, other_cookie, 'configuration-update', draft)
    with pytest.raises(PermissionError):
        post(ui, cookie, 'pause', {key: draft[key] for key in ('nonce', 'csrf')})
    ui.form_nonce_limit = 3
    for number in range(8):
        ui._form_nonce(token, 'pause', str(number))
    assert len(ui.form_nonces) <= 3
    ui.translation.close()


def test_save_toml_only_creates_and_never_replaces_regular_or_symlink(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    path = tmp_path / 'new.toml'
    assert ui._save_toml(path, 'title="new"\n') == path
    for selected in (path, tmp_path / 'link.toml', tmp_path / 'dangling.toml'):
        if selected.name == 'link.toml':
            selected.symlink_to(path)
        elif selected.name == 'dangling.toml':
            selected.symlink_to(tmp_path / 'not-created.toml')
        with pytest.raises(FileExistsError):
            ui._save_toml(selected, 'overwrite')
    assert path.read_text() == 'title="new"\n' and not (tmp_path / 'not-created.toml').exists()
    # O_EXCL arbitrates concurrent creation without truncation.
    winners, errors = [], []
    target = tmp_path / 'race.toml'
    def save(text):
        try:
            ui._save_toml(target, text); winners.append(text)
        except FileExistsError:
            errors.append(text)
    threads = [threading.Thread(target=save, args=(str(number),)) for number in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(2)
    assert len(winners) == len(errors) == 1 and target.read_text() == winners[0]
    # Legacy and current routes both reach the same exclusive helper.
    fields = dict(control_fields(ui, cookie, 'configuration'), config_path=str(path), config_text='legacy', mode='save')
    assert post(ui, cookie, 'configuration', fields)[0] == 409
    assert path.read_text() == 'title="new"\n'
    ui.translation.close()


def test_result_display_and_translation_sources_are_cleaned_without_changing_original(tmp_path):
    import json
    from scidiscovery.research_loop import instance_render, ui_view
    from test_instance_science import read
    root, key = prepare(tmp_path, enabled=False)
    private = f'Ordinary scientific result {root}/native-homes/001 api_key="credential-bytes" Bearer opaque-value'
    finish(root, 1, 'H', [], action='analyze', summary=private)
    ui, cookie = session(root, key)
    original = (root / 'native-evidence/001/science-output.json').read_bytes()
    page = ui.get('/results/1', host=ui.host, cookie=cookie)[1].decode()
    sources = [json.loads(html.unescape(value)) for value in re.findall(r'data-translation-source="([^"]*)"', page)]
    assert 'Ordinary scientific result' in page and '清洗后的展示' in page
    for forbidden in (str(root), 'credential-bytes', 'opaque-value'):
        assert forbidden not in page and all(forbidden not in source for source in sources)
    # Nested historical task prose passes through the same presentation copy.
    full = instance_render.result_body(1, dict(role='H', history=dict(view=dict(candidates=dict(candidate_facts=[dict(id=private)])))),
        dict(summary=private, scientific_result=dict(nested=[private])), dict(materials=[]), clean=ui_view.cleaner(root))
    assert str(root) not in full and 'credential-bytes' not in full
    assert read(ui.get('/nodes/1/science', host=ui.host, cookie=cookie)[1]) == original
    assert (root / 'native-evidence/001/science-output.json').read_bytes() == original
    ui.translation.close()


@pytest.mark.process_e2e
def test_all_candidate_history_paginates_frozen_prefix_without_latest_nominee_filter(tmp_path):
    from test_ui_view import nominated_scene
    root, key = nominated_scene(tmp_path)
    ui, cookie = session(root, key)
    first = ui.get('/candidates', host=ui.host, cookie=cookie)[1].decode()
    found = set(re.findall(r'Candidate (\d+)', first))
    link = html.unescape(re.search(r'href="([^"]+)"[^>]*>下一页候选', first)[1])
    second = ui.get(link, host=ui.host, cookie=cookie)[1].decode()
    remaining = set(re.findall(r'Candidate (\d+)', second))
    assert found.isdisjoint(remaining)
    assert found | remaining == {str(number) for number in range(1, 38)}
    assert 'Candidate 1' in first and '含失败' in first
    before = ui.get(link, host=ui.host, cookie=cookie)[1]
    from scidiscovery.research_loop import ui_view
    finish(root, 7, 'H', [6], action='analyze', search_choice=ui_view.Reader(root).node(1)['payload']['search_plan'])
    assert ui.get(link, host=ui.host, cookie=cookie)[1] == before
    overview = ui.get('/', host=ui.host, cookie=cookie)[1]
    assert b'href="/candidates"' in overview
    ui.translation.close()


def test_sse_component_budget_uses_final_utf8_json_and_keeps_small_status():
    import json
    from scidiscovery.research_loop import instance_live
    value = dict(fragments={'live-hero': '<p>原任务待核对</p>', 'live-trail': '汉字\\\"<&>' * 70000},
        current=dict(step=1, url='/nodes/1/events?attempt=0'))
    encoded = instance_live.encode_snapshot(value)
    assert len(encoded) <= instance_live.MAX_EVENT_BYTES
    decoded = json.loads(encoded)
    assert decoded['fragments']['live-hero'] == value['fragments']['live-hero']
    assert '/results' in decoded['fragments']['live-trail'] and decoded['current'] == value['current']
    assert len(value['fragments']['live-trail']) > 100000


@pytest.mark.process_e2e
def test_headless_browser_sse_preserves_draft_and_form_credentials(tmp_path):
    """Run the production JS/CSP against only a bounded loopback fixture."""
    import base64
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import os
    import shutil
    import signal
    import subprocess
    import tempfile
    from pathlib import Path
    from scidiscovery.research_loop import instance_render as render
    chrome = shutil.which('google-chrome') or shutil.which('chromium')
    if chrome is None:
        pytest.skip('No local Chrome executable for the explicit browser case')
    ready = threading.Event()
    form_markup = ('<form method="post" action="/control/continue"><input name="nonce" value="form-original">'
        '<input name="csrf" value="csrf-original"><input name="ack_correction" value="same-trigger">'
        '<textarea name="authorization_note"></textarea><button type="submit">Submit</button></form>')
    driver = '''document.addEventListener('DOMContentLoaded', () => {
      const input = document.querySelector('textarea'); input.value = 'preserved browser draft';
      input.dispatchEvent(new Event('input', {bubbles:true}));
      const observer = new MutationObserver(() => {
        if (!document.querySelector('[data-phase="2"]')) return;
        const current = document.querySelector('textarea');
        const ok = current.value === 'preserved browser draft' && document.querySelector('[name="nonce"]').value === 'form-original';
        const result = document.createElement('output'); result.id = 'browser-result';
        result.textContent = ok ? 'PASSED_STABLE_DRAFT' : 'FAILED_DRAFT';
        document.body.append(result); observer.disconnect();
      });
      observer.observe(document.getElementById('live-hero'), {subtree:true, childList:true});
      fetch('/fixture-ready');
    });'''
    driver_csp = "'sha256-" + base64.b64encode(hashlib.sha256(driver.encode()).digest()).decode() + "'"
    page = render.page('Offline browser fixture', '<p data-live-url="/live"></p><div id="live-hero">' + form_markup
        + '</div><script>' + driver + '</script>', live=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/live':
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                if ready.wait(3):
                    for phase in (1, 2):
                        value = dict(fragments={'live-hero': form_markup + f'<p data-phase="{phase}">Updated</p>'},
                            nonce='unrelated-tab-token', csrf={'continue': 'unrelated-csrf'}, current=None)
                        self.wfile.write(b'event: snapshot\ndata: ' + json.dumps(value).encode() + b'\n\n')
                    self.wfile.flush()
                return
            if self.path == '/fixture-ready':
                ready.set(); body = b'{}'
            elif self.path == '/': body = page
            elif self.path == '/assets/progress.js': body = render.PROGRESS_SCRIPT.encode()
            elif self.path == '/assets/translation.js': body = render.translation_client.SCRIPT.encode()
            elif self.path == '/assets/style.css': body = render.CSS.encode()
            else: body = b'{}'
            self.send_response(200); self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'text/html; charset=utf-8' if self.path == '/' else 'text/javascript' if self.path.endswith('.js') else 'text/css' if self.path.endswith('.css') else 'application/json')
            self.send_header('Content-Security-Policy', render.CSP.replace('script-src ', 'script-src ' + driver_csp + ' ', 1))
            self.end_headers(); self.wfile.write(body)
    with tempfile.TemporaryDirectory(prefix='scid-ui-browser-', dir='/tmp') as browser_directory, ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
        browser_base = Path(browser_directory)
        browser_env = dict(os.environ)
        for variable, name in (('XDG_CONFIG_HOME', 'config'), ('XDG_CACHE_HOME', 'cache'), ('XDG_STATE_HOME', 'state'), ('XDG_DATA_HOME', 'data'), ('XDG_RUNTIME_DIR', 'runtime'), ('TMPDIR', 'tmp')):
            directory = browser_base / name; directory.mkdir(mode=0o700); browser_env[variable] = str(directory)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        command = [chrome, '--headless', '--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage',
            '--disable-background-networking', '--disable-component-update', '--disable-default-apps',
            '--disable-extensions', '--no-first-run', '--no-default-browser-check',
            '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1', '--user-data-dir=' + str(browser_base / 'profile'),
            '--virtual-time-budget=3000', '--dump-dom', f'http://127.0.0.1:{server.server_port}/']
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=browser_env, start_new_session=True)
        try:
            output, error = process.communicate(timeout=20)
            assert process.returncode == 0, error[-2000:].decode(errors='replace')
            assert b'<output id="browser-result">PASSED_STABLE_DRAFT</output>' in output
        finally:
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            process.wait(timeout=5)
            server.shutdown(); thread.join(3)


@pytest.mark.parametrize('kind, expected', [('external', '等待原外部执行结果'), ('unsealed', '阶段结论尚未封存'),
    ('unsubmitted', '原任务尚未提交')])
def test_saved_native_wait_and_stops_never_dispatch_from_get_or_sse(tmp_path, monkeypatch, kind, expected):
    from scidiscovery.research_loop import contracts as c
    root, key = prepare(tmp_path, enabled=False)
    c.build_job(root, 1, 'H', [])
    record = root / 'native-evidence/001'; record.mkdir(exist_ok=True)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(record / 'native-outcome.json', dict(native_started=kind != 'unsubmitted', returncode=0))
    if kind == 'external':
        c.write(record / 'execution-wait.json', dict(status='waiting'))
    ui, cookie = session(root, key)
    monkeypatch.setattr(control, 'start_background', lambda *a, **k: pytest.fail('GET dispatched research'))
    page = ui.get('/', host=ui.host, cookie=cookie)[1].decode()
    hero = ui._live_snapshot(ui._browser(cookie), '', force=True)['fragments']['live-hero']
    assert expected in page and expected in hero
    assert 'class="primary"' in hero and 'href="/progress"' in hero
    assert 'action="/control/start"' not in hero and 'action="/control/pause"' not in hero
    ui.translation.close()


def test_completed_research_has_no_attention_for_optional_budget_extension(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    finish(root, 2, 'A', [1], action='finish')
    ui, cookie = session(root, key)
    snapshot = ui._snapshot()
    status = ui._status(snapshot, ui._receipt(snapshot))
    assert status['state'] == 'completed'
    assert ui._attention(ui._browser(cookie), True, snapshot, status) == []
    assert '追加预算' in ui._more_actions(ui._browser(cookie), dict(snapshot, view=dict(snapshot['view'], closeout=dict(status='final_a_completed'))))
    ui.translation.close()


def test_current_configure_save_route_keeps_existing_file_and_draft(tmp_path):
    from scidiscovery.research_loop.instance_ui import InstanceUI
    from test_workspace_ui import operator_key, controls
    from test_research_config import declaration
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    config = declaration(workspace)
    original = config.read_bytes()
    ui = InstanceUI(workspace, secret_file=operator_key(tmp_path), workspace=True); ui.host = '127.0.0.1:18753'
    cookie = ui.get('/manage?token=' + ui.management_token, host=ui.host)[2]['Set-Cookie']
    token = ui._browser(cookie)
    imported = ui._import_config(token, config)[1]
    fields = controls(imported)
    fields.update(mode='save', save_path=str(config))
    status, page, _ = post(ui, cookie, 'configure', fields)
    assert status == 409 and config.read_bytes() == original
    assert str(config).encode() in page and '配置文件已存在'.encode() in page
    fresh = controls(page)
    destination = workspace / 'saved-new.toml'
    fresh.update(mode='save', save_path=str(destination))
    assert post(ui, cookie, 'configure', fresh)[0] == 200
    assert destination.exists() and config.read_bytes() == original
    ui.translation.close()


@pytest.mark.process_e2e
def test_candidate_history_keeps_readable_candidates_when_earlier_recommended_source_is_missing(tmp_path):
    from scidiscovery.research_loop import contracts as c, ui_view
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Retain incomplete candidate evidence without inventing associations.')
    observer = tmp_path / 'observer.py'
    observer.write_text('''import json,sys
r=json.load(sys.stdin)
step=r['step']
rows=[dict(id=f'model-{step}',label=f'Observed candidate {step}',implementation=f'history/nodes/{step:03d}/artifacts/model.py',evaluation_key='val',metric=step/10,direction='max',status='ok',evidence=['project/global/objective.md'])] if step in (2,3) else []
print(json.dumps(dict(candidates=rows,current_candidate=None,actual_submission=None,coverage=dict(status='complete',note='Incomplete display fixture only'))))
''')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-invoked'], max_tokens=100000, max_seconds=600,
        max_steps=6, model='fixture', reasoning_effort='high', experiment_mode='external',
        search_policy=None, history_observer=dict(adapter=str(observer), config={}))
    finish(root, 1, 'H', [], action='analyze')
    finish(root, 2, 'W', [1], files={'model.py': '# original candidate 2\n'})
    finish(root, 3, 'W', [2], files={'model.py': '# original candidate 3\n'},
        candidate_nominations=dict(representatives=[], extras=[dict(candidate='model-2', reason='Retained earlier candidate')], gaps=[]))
    finish(root, 4, 'A', [3], recommendation='model-2')
    key = tmp_path / 'operator.key'; key.write_bytes(b'fixture-incomplete-history-key-32'); key.chmod(0o600)
    ui, cookie = session(root, key)
    original = root / 'native-evidence/002/science-output.json'
    original.chmod(0o600); original.write_text('{')
    status, page, _ = ui.get('/candidates', host=ui.host, cookie=cookie)
    text = page.decode()
    assert status == 200 and 'Observed candidate 3' in text and 'Observed candidate 2' not in text
    assert '缺口阶段：2' in text and '计数为下界' in text
    assert '1 条推荐的候选来源不可读' in text and '1 条提名的候选来源不可读' in text
    # The ordinary subject projection remains strict for the same exact gap.
    job = dict(history=dict(nodes=[dict(step=step) for step in (1,3,4)]))
    with pytest.raises(ValueError, match='unknown candidate'):
        ui_view.candidate_projection(root, 5, job=job)
    ui.translation.close()
