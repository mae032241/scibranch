"""Live workbench HTTP state is read-only; all advancing records are fixtures."""
import http.client
import json
import threading

import pytest
from urllib.parse import urlsplit

from scidiscovery.research_loop import instance_live as live
from test_execution_authority import prepare
from test_history_handoff import finish
from test_workspace_ui import host, login, request, form


def stream(url, path, cookie):
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=6)
    connection.request('GET', path, headers={'Cookie': cookie})
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader('Content-Type').startswith('text/event-stream')
    assert response.getheader('Cache-Control') == 'no-store'
    assert response.getheader('Content-Length') is None
    return connection, response


def snapshot(response):
    while True:
        line = response.readline(live.MAX_EVENT_BYTES + 1)
        assert line, 'SSE disconnected before its snapshot'
        assert len(line) <= live.MAX_EVENT_BYTES
        if line.startswith(b'data: '):
            return json.loads(line[6:])


@pytest.mark.process_e2e
def test_http_sse_initial_change_reconnect_and_exact_read_scope(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    with host(root, key, workspace=False) as (connection, url, endpoint):
        cookie = login(connection, url)
        assert request(connection, 'GET', '/live')[0] == 403
        denied = request(connection, 'GET', '/live?token=forbidden', cookie=cookie)
        assert denied[0] == 400 and b'event: snapshot' not in denied[1]
        assert not denied[2].get('Content-Type', '').startswith('text/event-stream')
        following = request(connection, 'GET', '/progress', cookie=cookie)
        assert following[0] == 200 and b'data-follow-latest="true"' in following[1]
        client, response = stream(url, '/live', cookie)
        try:
            initial = snapshot(response)
            assert initial['current'] is None
            assert '未开始' in initial['fragments']['live-hero'] and 'action="/control/start"' in initial['fragments']['live-hero']
            finish(root, 1, 'H', [], action='analyze', summary='First retained finding')
            changed = snapshot(response)
            assert changed['current']['step'] == 1
            assert changed['current']['url'] == '/nodes/1/events?attempt=0'
            assert 'First retained finding' in changed['fragments']['live-trail']
            assert '<strong>10</strong><span class="stat-limit"> / 4,000</span>' in changed['fragments']['live-usage']
            assert 'Token 监控' in changed['fragments']['live-token-monitor']
            assert 'token-monitor' not in changed['fragments']['live-usage']
        finally:
            response.close(); client.close()
        finish(root, 2, 'A', [1], summary='Needs a missing measurement')
        # A fresh connection always receives authoritative complete state;
        # Last-Event-ID/history replay is not required to recover a dashboard.
        client, response = stream(url, '/live', cookie)
        try:
            resumed = snapshot(response)
            assert resumed['current']['step'] == 2
            assert '等待条件' in resumed['fragments']['live-hero'] and '需要你处理' in resumed['fragments']['live-hero']
            assert 'action="/control/start"' not in resumed['fragments']['live-hero']
            assert 'action="/control/continue"' in resumed['fragments']['live-hero']
        finally:
            response.close(); client.close()
        read_url = urlsplit(endpoint['read_url'])
        read_cookie = login(connection, read_url)
        client, response = stream(url, '/live', read_cookie)
        try:
            readonly = snapshot(response)
            assert readonly['csrf'] == {} and readonly['nonce'] is None
            assert all('action="/control/' not in value for value in readonly['fragments'].values())
        finally:
            response.close(); client.close()


@pytest.mark.process_e2e
def test_workspace_loaded_state_and_mounted_stream(tmp_path):
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workbench'; workspace.mkdir()
    with host(workspace, key, workspace=True) as (connection, url, _):
        cookie = login(connection, url)
        _, page, _ = request(connection, 'GET', '/', cookie=cookie)
        fields = dict(form(page, 'open'), root=str(root))
        status, _, headers = request(connection, 'POST', '/control/open', cookie=cookie,
            origin='http://' + url.netloc, fields=fields)
        assert status == 303
        status, _, headers = request(connection, 'GET', headers['Location'], cookie=cookie)
        assert status == 200
        child_cookie = headers['Set-Cookie'].split(';', 1)[0]
        assert request(connection, 'GET', '/instances/1/live', cookie=cookie)[0] == 403
        assert request(connection, 'GET', '/live', cookie=child_cookie)[0] == 403
        client, response = stream(url, '/instances/1/live', child_cookie)
        try:
            initial = snapshot(response)
            assert 'action="/instances/1/control/start"' in initial['fragments']['live-hero']
            assert 'href="/instances/1/' in initial['fragments']['live-trail'] or initial['current'] is None
            finish(root, 1, 'H', [], action='analyze')
            changed = snapshot(response)
            assert changed['current']['url'] == '/instances/1/nodes/1/events?attempt=0'
            assert 'href="/instances/1/results/1"' in changed['fragments']['live-trail']
        finally:
            response.close(); client.close()
        client, response = stream(url, '/live', cookie)
        try:
            loaded = snapshot(response)['fragments']['live-instances']
            assert '第 1 阶段' in loaded and '10 / 4,000 tokens' in loaded
        finally:
            response.close(); client.close()
        page = request(connection, 'GET', '/instances/1/progress', cookie=child_cookie)[1]
        assert b'data-live-url="/instances/1/live"' in page
        fixed = request(connection, 'GET', '/instances/1/nodes/1/progress', cookie=child_cookie)[1]
        assert b'data-progress="/instances/1/nodes/1/events?attempt=0"' in fixed
        assert b'data-follow-latest="true"' not in fixed
        for markup in (page, fixed):
            assert b'<section class="token-monitor" data-token-monitor>' in markup
            assert markup.index(b'<section data-progress=') < markup.index(b'<section class="token-monitor"')
            public_log = markup.split(b'<section data-progress=', 1)[1].split(b'</section>', 1)[0]
            assert b'data-token-details' not in public_log


def test_shared_snapshot_cache_reuses_briefly_then_reads_saved_state_without_tree_scan(tmp_path, monkeypatch):
    now = [100.]
    monkeypatch.setattr(live.time, 'monotonic', lambda: now[0])
    calls = []
    cache = live.SnapshotCache()
    def load():
        calls.append(1)
        return {'public': len(calls)}
    assert cache.read(tmp_path, load) == {'public': 1}
    assert cache.read(tmp_path, load) == {'public': 1}
    (tmp_path / 'control.json').write_text('{}')
    now[0] += live.INTERVAL
    assert cache.read(tmp_path, load) == {'public': 2}
    assert len(calls) == 2


@pytest.mark.process_e2e
def test_current_worker_execution_receipt_changes_reach_get_and_readonly_sse(tmp_path):
    from scidiscovery.research_loop import contracts as c,experiments,experiment_client
    root,key=prepare(tmp_path,enabled=False)
    finish(root,1,'H',[],action='analyze')
    c.build_job(root,2,'W',[1])
    job=c.read(root/'jobs/002.json')
    records=experiment_client._record_directory_from_facts(root,2,job)
    records.mkdir(parents=True,exist_ok=True)
    from scidiscovery.research_loop.protocol import stage_scope_for_job
    owner=experiments.Records(dict(record_dir=str(records),scope=stage_scope_for_job(root,job,
        task_ref=dict(path=str(root/'jobs/002.json')))))
    accepted=dict(_display_first_call=True,native_attempt=dict(number=0,execution_key='private-native-path'))
    owner.live_status('original-call-only','submitted',accepted=accepted)
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',root/'native-evidence/001/result-identity.json')}
    assert not (root/'native-evidence/002/result-identity.json').exists()
    with host(root,key,workspace=False) as (connection,url,endpoint):
        read_url=urlsplit(endpoint['read_url'])
        cookie=login(connection,read_url)
        page=request(connection,'GET','/',cookie=cookie)[1]
        assert '已提交，运行状态待观察' in page.decode()
        client,response=stream(url,'/live',cookie)
        try:
            initial=snapshot(response)
            assert 'original-call-only' in initial['fragments']['live-usage']
            assert '已提交，运行状态待观察' in initial['fragments']['live-usage']
            owner.live_status('original-call-only','running',accepted=accepted)
            changed=snapshot(response)
            assert '已观察运行' in changed['fragments']['live-usage']
            assert 'action="/control/' not in changed['fragments']['live-usage']
            assert 'private-binding-marker' not in json.dumps(changed)
            assert 'private-native-path' not in json.dumps(changed)
            assert all(path.read_bytes()==value for path,value in originals.items())
            assert not (root/'native-evidence/002/result-identity.json').exists()
            page=request(connection,'GET','/',cookie=cookie)[1]
            assert '已观察运行' in page.decode()
        finally:
            response.close();client.close()


@pytest.mark.process_e2e
def test_v3_same_worker_declaration_and_original_binding_reach_get_and_sse(tmp_path):
    from scidiscovery.research_loop import contracts as c,experiments,experiment_client,protocol,worker_research
    from scidiscovery.research_loop import controller
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Retain exact observations and explicit limitations.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],native_backend='protocol',
        model='ui-fixture',reasoning_effort='high',max_tokens=100000,max_seconds=120,max_steps=6,
        research_strategy_version=3)
    job=c.build_job(root,1,'W',[])
    assert job['search']['breadth'] is None
    key=tmp_path/'operator.key';key.write_bytes(b'ui-worker-first-fixture-key-32-bytes');key.chmod(0o600)
    directory=experiment_client._record_directory_from_facts(root,1,job);directory.mkdir(parents=True,exist_ok=True)
    config=dict(record_dir=str(directory),scope=protocol.stage_scope_for_job(root,job,
        task_ref=dict(path=str(root/'jobs/001.json'))),research_strategy_version=3,
        breadth=job['search']['breadth'],worker_research_goal_id=c.settings(root)['goal_id'])
    owner=experiments.Records(config)
    previous=worker_research.initial(config)
    current=worker_research.evolve(previous,[dict(action='declare',mechanism_ref='w001.observation',
        question='Which controlled observation distinguishes the alternatives?')],scope=config['scope'],
        original_ref=dict(path=str(directory/'research/000001.json')))
    worker_research.publish(config,previous,current)
    owner.research_status(current)
    originals={path:path.read_bytes() for path in (root/'jobs/001.json',directory/'research/000001.json')}
    with host(root,key,workspace=False) as (connection,url,endpoint):
        cookie=login(connection,urlsplit(endpoint['read_url']))
        page=request(connection,'GET','/',cookie=cookie)[1].decode()
        assert 'w001.observation' in page and '缺失视图不表示零投入' in page
        client,response=stream(url,'/live',cookie)
        try:
            initial=snapshot(response)
            assert 'w001.observation' in initial['fragments']['live-usage']
            _,binding=worker_research.prepare(config,current,dict(mechanism_ref='w001.observation',
                experiment_question=dict(question='Does this input preserve the observable?',
                    change_criterion='Change the input if the observable disappears.')))
            admitted=dict(scope=config['scope'],call_id='exact-original',mechanism_binding=binding,
                native_attempt=dict(number=0),_display_first_call=True,code_version=dict(entry='observe.py'))
            owner._research_prior_calls=[]
            owner.live_status('exact-original','running',accepted=admitted)
            changed=snapshot(response)
            html=changed['fragments']['live-usage']
            assert 'Does this input preserve the observable?' in html and 'revision 1' in html
            assert '原调用总数：1' in html and '当前连续投入：1' in html
            assert 'action="/control/' not in html and str(root) not in html
            assert 'job_ref' not in json.dumps(changed) and 'execution_key' not in json.dumps(changed)
            assert all(path.read_bytes()==data for path,data in originals.items())
            assert not (root/'native-evidence/001/result-identity.json').exists()
            page=request(connection,'GET','/',cookie=cookie)[1].decode()
            assert 'Does this input preserve the observable?' in page and '原调用总数：1' in page
        finally:
            response.close();client.close()


def test_stream_disconnection_releases_slot_and_has_bounded_transport(tmp_path, monkeypatch):
    monkeypatch.setattr(live, 'INTERVAL', .001)
    stop = threading.Event(); gate = threading.BoundedSemaphore(1)
    class Disconnected:
        def write(self, value):
            raise BrokenPipeError
        def flush(self):
            pass
    live.Stream(lambda _, **options: {'public':'safe'}, stop=stop, gate=gate).write(Disconnected())
    assert gate.acquire(blocking=False)
    gate.release()
    class Sink:
        values = []
        def write(self, value):
            self.values.append(value)
        def flush(self):
            pass
    sink = Sink()
    live.Stream(lambda _, **options: {'public':'x' * live.MAX_EVENT_BYTES}, stop=stop, gate=gate).write(sink)
    assert b'event: unavailable' in b''.join(sink.values)
    assert sum(map(len, sink.values)) < 100
    assert gate.acquire(blocking=False)
    gate.release()
