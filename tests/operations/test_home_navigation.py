"""Browser navigation and binding confirmation through real HTTP and SQLite."""
from datetime import datetime, timedelta, timezone
import html
import http.client
import re
from urllib.parse import urlencode, urlparse

import pytest

from scidiscovery.artifact_agent.approval_ui.app import ApprovalUI
from scidiscovery.artifact_agent.service.instance_management import issue_instance_management_capability
from scidiscovery.artifact_agent.service.scheduler_bindings import SchedulerInstanceConflict
from tests.operations.test_instance_read_model import system, artifact, approval, run


SECRET = b'n' * 32
A, B, C = ('sch_' + letter*32 for letter in 'abc')


def request(base, method, path, *, form=None, cookie=None, accept=None):
    url = urlparse(base)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
    headers = {'Cookie':cookie} if cookie else {}
    if accept: headers['Accept'] = accept
    if form is not None:
        headers.update({'Origin':base,'Content-Type':'application/x-www-form-urlencoded'})
    connection.request(method,path,body=urlencode(form) if form is not None else None,headers=headers)
    response=connection.getresponse(); result=response.status,dict(response.getheaders()),response.read().decode()
    connection.close();return result


def field(page,name):
    return html.unescape(re.search("name='"+name+"' value='([^']*)'",page)[1])


@pytest.fixture
def home(system):
    system.runs.recent_ids=lambda *,instance_id,limit: tuple(r.run_id for r in system.runs.records.values() if r.instance_id==instance_id)[-limit:]
    ui=ApprovalUI(system.approvals,bindings=system.bindings,read_model=system.model,instance_management_secret=SECRET)
    base=ui.start()
    yield system,ui,base
    ui.stop()


def enter(ui,base,session=A):
    token=issue_instance_management_capability(session_key=session,secret=SECRET)
    status,headers,page=request(base,'GET','/sessions/'+session+'?'+urlencode({'capability':token}))
    assert status==200
    return token,headers['Set-Cookie'].split(';',1)[0],page


def test_home_cookie_and_independent_directory_do_not_bind(home):
    s,ui,base=home
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    s.bindings.bind_session(session_key=B,instance_id=s.b)
    run(s,'working',state='running')
    before=s.bindings.session_binding_snapshot(session_key=A,instance_id=s.b)
    token,cookie,page=enter(ui,base)
    assert '科研会话 · aaaaaaaa' in page and "action='/instances/select'" in page
    assert "action='/instances/create'" not in page
    assert '进入当前实例工作台' in page
    status,_,page=request(base,'GET','/',cookie=cookie)
    assert status==200 and "action='/instances/create-unbound'" in page
    assert "action='/instances/select'" not in page and "action='/instances/create'" not in page
    status,_,page=request(base,'GET','/instances/manage',cookie=cookie)
    assert status==200 and '当前会话已绑定' in page and '已被其他会话绑定' in page
    assert '已记录的排队 / 运行任务：1' in page and '最近任务：运行中' in page
    assert "action='/instances/create'" not in page and "action='/instances/select'" not in page
    status,headers,_=request(base,'POST','/instances/access',form={'capability':token,'csrf':ui.session_id,'scope':'read','instance_id':s.b})
    assert status==303
    view_cookie=headers['Set-Cookie'].split(';',1)[0]
    assert request(base,'GET',headers['Location'],cookie=view_cookie)[0]==200
    assert s.bindings.session_binding_snapshot(session_key=A,instance_id=s.b)==before
    assert request(base,'GET',f'/api/instances/{s.a}/overview',cookie=view_cookie)[0]==403
    assert request(base,'GET','/',cookie=cookie+'; '+view_cookie)[0]==200


def test_takeover_requires_confirmation_and_rejects_stale_owner(home):
    s,ui,base=home
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    s.bindings.bind_session(session_key=B,instance_id=s.b)
    task=run(s,'ongoing',state='running')
    token,_,_=enter(ui,base,B)
    form={'capability':token,'csrf':ui.session_id,'name':'one'}
    status,_,page=request(base,'POST','/instances/select',form=form)
    assert status==200 and '确认科研会话接续' in page and '排队 / 运行任务 1' in page
    assert s.bindings.session_instance(session_key=A)==s.a
    assert s.bindings.session_instance(session_key=B)==s.b
    stale={**form,'confirm_binding':'yes','expected_binding':field(page,'expected_binding')}
    s.bindings.bind_session(session_key=C,instance_id=s.a)
    assert request(base,'POST','/instances/select',form=stale)[0]==409
    assert s.bindings.session_instance(session_key=C)==s.a
    _,_,page=request(base,'POST','/instances/select',form=form)
    confirm={**form,'confirm_binding':'yes','expected_binding':field(page,'expected_binding')}
    assert request(base,'POST','/instances/select',form=confirm)[0]==303
    assert s.bindings.session_instance(session_key=B)==s.a
    assert s.bindings.session_instance(session_key=C) is None
    assert task.state=='running'


def test_current_binding_is_not_rewritten_and_creation_switch_is_confirmed(home):
    s,ui,base=home
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    token,_,_=enter(ui,base)
    before=s.bindings.session_binding_snapshot(session_key=A,instance_id=s.a)
    assert request(base,'POST','/instances/select',form={'capability':token,'csrf':ui.session_id,'name':'one'})[0]==303
    assert s.bindings.session_binding_snapshot(session_key=A,instance_id=s.a)==before
    create={'capability':token,'csrf':ui.session_id,'name':'new','title':'新实例','objective':'用户的原始目标'}
    status,_,page=request(base,'POST','/instances/create',form=create)
    assert status==200 and len(s.bindings.list_instances())==2
    expected=field(page,'expected_binding')
    s.bindings.bind_session(session_key=A,instance_id=s.b)
    assert request(base,'POST','/instances/create',form={**create,'confirm_binding':'yes','expected_binding':expected})[0]==409
    assert len(s.bindings.list_instances())==2
    _,_,page=request(base,'POST','/instances/create',form=create)
    status,_,_=request(base,'POST','/instances/create',form={**create,'confirm_binding':'yes','expected_binding':field(page,'expected_binding')})
    assert status==303
    assert s.bindings.select_instance(name='new').objective=='用户的原始目标'
    assert len(s.bindings.list_instances())==3


def test_service_transaction_rechecks_binding_before_any_create_or_transfer(home):
    s,_,_=home
    expected=s.bindings.session_binding_snapshot(session_key=A,instance_id=s.a)['fingerprint']
    s.bindings.bind_session(session_key=B,instance_id=s.a)
    with pytest.raises(SchedulerInstanceConflict):
        s.bindings.bind_session_by_name(session_key=A,name='one',expected_binding=expected)
    expected=s.bindings.session_binding_snapshot(session_key=A)['fingerprint']
    s.bindings.bind_session(session_key=A,instance_id=s.b)
    with pytest.raises(SchedulerInstanceConflict):
        s.bindings.create_instance_and_bind_session(session_key=A,name='uncreated',title='x',objective='x',expected_binding=expected)
    assert len(s.bindings.list_instances())==2


def test_expired_or_forged_home_context_cannot_create_or_extend_access(home):
    s,ui,base=home
    expired=issue_instance_management_capability(session_key=A,secret=SECRET,now=datetime.now(timezone.utc)-timedelta(hours=2))
    status,headers,page=request(base,'GET','/',cookie='scid_management='+expired)
    assert status==200 and "action='/instances/create'" not in page
    assert '创建实例' in page and '实例绑定' not in page
    assert 'Set-Cookie' not in headers
    for token in (expired,'forged'):
        status,_,page=request(base,'GET','/instances?'+urlencode({'capability':token}),accept='text/html')
        assert status==403 and '返回首页' in page
        assert request(base,'POST','/instances/create',form={'capability':token,'csrf':ui.session_id,'name':'x','title':'x','objective':'x'})[0]==403
    assert len(s.bindings.list_instances())==2


def test_navigation_covers_node_evidence_approval_and_metadata(home):
    s,ui,base=home
    record=artifact(s,'source',{'message':'Immutable source'},instance=s.a)
    run(s,'navigation.sample',output=record.ref)
    launch=approval(s,'approval',(record.ref,))
    token,cookie,_=enter(ui,base)
    _,headers,_=request(base,'POST','/instances/access',form={'capability':token,'csrf':ui.session_id,'scope':'read','instance_id':s.a})
    cookie += '; '+headers['Set-Cookie'].split(';',1)[0]
    paths=('/', '/instances/manage',f'/instance/{s.a}',f'/instance/{s.a}/nodes/artifact%3Asource',
        f'/instance/{s.a}/nodes/run%3Anavigation.sample',
        f'/instance/{s.a}/evidence/{record.artifact_id}',launch.review_path,
        f'/review/{launch.approval_id}/evidence/{record.artifact_id}?'+urlencode({'token':launch.access_token}))
    for path in paths:
        status,_,page=request(base,'GET',path,cookie=cookie)
        assert status==200,(path,status)
        assert "class='global-navigation'" in page and "href='/'" in page,path
    assert '返回原审批' in page
    status,_,page=request(base,'GET',f'/instance/{s.b}')
    assert status==200 and '返回首页' in page
    assert s.bindings.session_instance(session_key=A) is None


def test_local_workbench_without_conversation_preserves_science_and_bindings(home):
    s,ui,base=home
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    s.bindings.bind_session(session_key=B,instance_id=s.b)
    task=run(s,'local_navigation',state='running')
    source=artifact(s,'local_source',instance=s.a)
    decision=approval(s,'local_decision',(source.ref,))
    before=s.bindings.session_binding_snapshot(session_key=A,instance_id=s.b)
    status,headers,page=request(base,'GET','/')
    assert status==200 and '创建实例' in page
    assert "action='/instances/create-unbound'" in page
    assert "action='/instances/select'" not in page
    assert 'Set-Cookie' not in headers  # Reading a page is not a grant.
    status,_,directory=request(base,'GET','/instances/manage')
    assert status==200 and "action='/instances/access'" in directory
    status,_,reentry=request(base,'GET',f'/instance/{s.a}/settings',accept='text/html')
    assert status==403 and '无需连接科研对话' in reentry
    assert "name='destination' value='settings'" in reentry
    assert request(base,'GET','/instance/missing/settings',accept='text/html')[0]==404
    status,headers,_=request(base,'POST','/instances/access',form={
        'csrf':field(directory,'csrf'),'instance_id':s.a,'scope':'read'})
    assert status==303
    cookie=headers['Set-Cookie'].split(';',1)[0]
    assert request(base,'GET',headers['Location'],cookie=cookie)[0]==200
    assert request(base,'GET',f'/api/instances/{s.b}/overview',cookie=cookie)[0]==403
    # A browser grant is not a conversation capability, even with a valid CSRF.
    for action,values in (('select',{'name':'two'}),('create',{'name':'forbidden','title':'x','objective':'x'})):
        assert request(base,'POST','/instances/'+action,cookie=cookie,form={'csrf':ui.session_id,**values})[0]==403
    assert s.bindings.session_binding_snapshot(session_key=A,instance_id=s.b)==before
    assert task.state=='running' and s.approvals.status(decision.approval_id).status=='pending'
    status,headers,_=request(base,'POST','/instances/create-unbound',form={
        'csrf':field(page,'csrf'),'name':'standalone','title':'独立实例','objective':'原始研究目标'})
    assert status==303
    new=s.bindings.select_instance(name='standalone')
    assert new.objective=='原始研究目标'
    assert not s.bindings.session_binding_snapshot(session_key=A,instance_id=new.instance_id)['target_bound']
    assert s.bindings.session_binding_snapshot(session_key=A,instance_id=s.b)==before


@pytest.mark.parametrize('fault',['origin_missing','origin_null','origin_external','csrf','host'])
def test_local_entry_retains_origin_host_and_csrf_gates(home,fault):
    s,ui,base=home
    endpoint=urlparse(base)
    for action,values in (('access',{'instance_id':s.a,'scope':'maintenance'}),
                          ('create-unbound',{'name':'bad','title':'x','objective':'x'})):
        headers={'Content-Type':'application/x-www-form-urlencoded','Origin':base}
        if fault=='origin_missing': headers.pop('Origin')
        if fault=='origin_null': headers['Origin']='null'
        if fault=='origin_external': headers['Origin']='https://example.invalid'
        if fault=='host': headers['Host']='example.invalid'
        form={'csrf':'bad' if fault=='csrf' else ui.session_id,**values}
        conn=http.client.HTTPConnection(endpoint.hostname,endpoint.port,timeout=5)
        conn.request('POST','/instances/'+action,body=urlencode(form),headers=headers)
        response=conn.getresponse()
        assert response.status==403 and response.getheader('Set-Cookie') is None
        response.read();conn.close()
    assert len(s.bindings.list_instances())==2


def test_workbench_manages_registered_clients_without_a_conversation_cookie(home):
    s,ui,base=home
    s.bindings.register_client(session_key=A)
    s.bindings.register_client(session_key=B)
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    status,_,page=request(base,'GET','/sessions')
    assert status==200 and '申请绑定' in page and '已绑定 · 允许调度' in page
    form={'csrf':field(page,'csrf'),'session_key':A,'enabled':'0','expected_enabled':'1'}
    assert request(base,'POST','/instances/client-state',form={**form,'csrf':'bad'})[0]==403
    assert request(base,'POST','/instances/client-state',form=form)[0]==303
    assert not s.bindings.client_enabled(session_key=A)
    assert s.bindings.session_instance(session_key=A)==s.a
    s.bindings.register_client(session_key=A)  # Restart/eviction must not undo a pause.
    assert not s.bindings.client_enabled(session_key=A)
    assert request(base,'POST','/instances/client-state',form=form)[0]==409
    assert request(base,'POST','/instances/client-state',form={**form,'session_key':C})[0]==409
    assert request(base,'POST','/instances/client-state',form={**form,'enabled':'1','expected_enabled':'0'})[0]==303
    assert s.bindings.client_enabled(session_key=A)
    assert s.bindings.session_instance(session_key=B) is None


def test_scoped_session_page_rejects_other_session_and_supports_legacy_entry(home):
    s,ui,base=home
    token,cookie,page=enter(ui,base,A)
    assert "action='/instances/select'" in page
    assert request(base,'GET','/sessions/'+B+'?'+urlencode({'capability':token}))[0]==403
    assert request(base,'GET','/sessions/'+B,cookie=cookie)[0]==403
    status,_,legacy=request(base,'GET','/instances?'+urlencode({'capability':token}))
    assert status==200 and '科研会话 · aaaaaaaa' in legacy
    assert '创建与绑定' not in legacy


def test_clear_offline_clients_removes_only_transient_rows_and_bindings(home):
    import sqlite3
    s,ui,base=home
    s.bindings.bind_session(session_key=A,instance_id=s.a)
    s.bindings.bind_session(session_key=B,instance_id=s.b)
    s.bindings.register_client(session_key=A)
    s.bindings.register_client(session_key=B)
    task=run(s,'survives_cleanup',state='running')
    source=artifact(s,'survives_cleanup',instance=s.a)
    pending=approval(s,'survives_cleanup',(source.ref,))
    s.bindings.disconnect_client(session_key=A)
    writable = ui._cache_writable
    ui._cache_writable = lambda instance_id: False
    assert request(base,'POST','/instances/clients-clear',form={'csrf':ui.session_id})[0]==303
    assert s.bindings.session_instance(session_key=A)==s.a
    ui._cache_writable = writable
    status,_,_=request(base,'POST','/instances/clients-clear',form={'csrf':'bad'})
    assert status==403 and s.bindings.session_instance(session_key=A)==s.a
    status,headers,_=request(base,'POST','/instances/clients-clear',form={'csrf':ui.session_id})
    assert status==303 and headers['Location']=='/sessions?cleared=1'
    assert '已清理 1 个离线会话' in request(base,'GET',headers['Location'])[2]
    with sqlite3.connect(s.bindings.client_database_path) as db:
        assert [row[0] for row in db.execute('SELECT session_key FROM scheduler_clients')]==[B]
    assert s.bindings.session_instance(session_key=A) is None
    assert s.bindings.session_instance(session_key=B)==s.b
    assert len(s.bindings.list_instances())==2
    assert task.state=='running' and s.approvals.status(pending.approval_id).status=='pending'
    s.bindings.client_heartbeat(session_key=A)
    assert [row['session_key'] for row in s.bindings.clients()]==[B]


def test_instance_results_route_reuses_read_grant_without_changing_selection(home):
    s, ui, base = home
    token, cookie, _ = enter(ui, base)
    status, headers, _ = request(base, 'POST', '/instances/access', form={
        'capability': token, 'csrf': ui.session_id, 'scope': 'read', 'instance_id': s.a})
    assert status == 303
    read_cookie = headers['Set-Cookie'].split(';', 1)[0]
    before = s.bindings.scientific_selections(instance=s.a, limit=101)
    status, _, page = request(base, 'GET', '/instance/' + s.a + '/results', cookie=read_cookie)
    assert status == 200 and '科学结果 / Best' in page and '科学结果尚未整理' in page
    assert 'workbench.js' not in page
    assert s.bindings.scientific_selections(instance=s.a, limit=101) == before
    assert request(base, 'GET', '/instance/' + s.b + '/results', cookie=read_cookie)[0] == 403
    assert request(base, 'GET', '/instance/' + s.a + '/results')[0] == 403
