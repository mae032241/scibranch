"""Authority-only negatives; no backend/resource permission is asserted here."""
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlencode

import pytest

from scidiscovery import execution_security as security
from scidiscovery.research_loop import contracts as c, controller, execution_authority as authority
from scidiscovery.research_loop import qualification as q
from scidiscovery.research_loop.instance_ui import InstanceUI
from test_formal_review import LAUNCHER
from test_history_handoff import finish


def prepare(tmp_path, *, enabled=True, launcher=None):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Assess one exact declared implementation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, launcher or ['fixture'],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external', formal_review_policy={'version': 1} if enabled else None,
        execution_authority_policy={'version': 1} if enabled else None,
        source_restrictions=[dict(source=str(objective), restriction='clear')])
    secret = tmp_path / 'operator.key'; secret.write_bytes(b'operator-key-for-fixture-only----32'); secret.chmod(0o600)
    return root, secret


def test_execution_vocabulary_and_legacy_hmac_wire_bytes():
    options = [security.execution_approval_option_id('accept')]
    assert not security.decision_authorizes('scientific_review', options[0], options)
    assert not security.decision_authorizes('execution_authorization', 'pass', ['pass'])
    assert not security.decision_authorizes('execution_authorization', 'approve', ['approve'])
    assert security.decision_authorizes('execution_authorization', 'approve', ['approve'], legacy=True)
    # Existing receipts sign the caller's exact canonical bytes, without a new prefix.
    payload = b'{"request_ref":{"artifact_id":"original"},"selected_option":"authorize_execution"}'
    key = b'fixture-receipt-secret-32-bytes----'
    assert security.receipt_hmac(key, payload) == hmac.new(key, payload, hashlib.sha256).hexdigest()


def test_secret_file_refuses_symlink_and_public_permissions(tmp_path):
    key = tmp_path / 'key'; key.write_bytes(b'x' * 32); key.chmod(0o600)
    link = tmp_path / 'link'; link.symlink_to(key)
    with pytest.raises(ValueError, match='non-symlink'):
        security.read_secret_file(link, label='fixture')
    key.chmod(0o644)
    with pytest.raises(ValueError, match='0600'):
        security.read_secret_file(key, label='fixture')


def test_ordinary_science_has_no_authority_or_approval_requirement(tmp_path):
    root, secret = prepare(tmp_path, enabled=False)
    finish(root, 1, 'H', [], action='analyze')
    _, _, record = finish(root, 2, 'A', [1], text='pass, approved, authorized')
    assert c.validate(c.workspace(root, 2))['scientific_result'] == 'pass, approved, authorized'
    assert 'execution_authority' not in c.read(root / 'jobs/002.json')
    assert 'execution_qualifications' not in c.read(record / 'result-identity.json')
    ui = InstanceUI(root, secret_file=secret); ui.host = '127.0.0.1:18750'
    _, _, headers = ui.get('/manage?' + urlencode({'token': ui.management_token}), host=ui.host)
    assert ui.get('/', host=ui.host, cookie=headers['Set-Cookie'])[0] == 200
    with pytest.raises(ValueError, match='此实例未启用实验审批入口'):
        ui.get('/requests', host=ui.host, cookie=headers['Set-Cookie'])


def test_loopback_host_rejects_origin_and_old_boot_without_http(tmp_path):
    root, secret = prepare(tmp_path)
    first = InstanceUI(root, secret_file=secret); first.host = '127.0.0.1:18751'
    status, _, headers = first.get('/manage?' + urlencode({'token': first.management_token}), host=first.host)
    assert status == 200 and 'Location' not in headers
    cookie = headers['Set-Cookie']
    assert first.get('/', host=first.host, cookie=cookie)[0] == 200
    with pytest.raises(PermissionError, match='Host'):
        first.get('/', host='attacker.invalid', cookie=cookie)
    with pytest.raises(PermissionError, match='Origin'):
        first.post('/requests/' + '0' * 64 + '/decision', host=first.host,
            origin='http://attacker.invalid', cookie=cookie,
            content_type='application/x-www-form-urlencoded', body=b'')
    restarted = InstanceUI(root, secret_file=secret); restarted.host = first.host
    with pytest.raises(PermissionError):
        restarted.get('/', host=restarted.host, cookie=cookie)
    other_key = tmp_path / 'other.key'; other_key.write_bytes(b'y' * 32); other_key.chmod(0o600)
    with pytest.raises(ValueError, match='Frozen source receipt changed'):
        InstanceUI(root, secret_file=other_key)


@pytest.mark.process_e2e
def test_exact_review_to_ui_receipt_rejects_forgery_and_never_dispatches(tmp_path, monkeypatch):
    # Reuses the real thin native/seal/review route with synthetic platform events.
    # The grant below is only an opaque authority-binding fixture: no budget owner
    # or TCAD admission is implemented/claimed in this P3c test.
    launcher = tmp_path / 'codex'; launcher.write_text('#!' + sys.executable + '\n' + LAUNCHER); launcher.chmod(0o700)
    home = tmp_path / 'native'; home.mkdir(); monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.delenv('REVIEW_REUSE_AUTHOR', raising=False); monkeypatch.delenv('REVIEW_MISSING_AUTHOR', raising=False)
    root, secret_file = prepare(tmp_path, launcher=[str(launcher)])
    assert controller.run(root)['status'] == 'finish'
    job, _, record = finish(root, 6, 'W', [2, 5])
    assert len(job['execution_qualifications']) == 1
    ref = job['execution_qualifications'][0]; subject = q._read(root, ref)['subject']
    ui = InstanceUI(root, secret_file=secret_file); ui.host = '127.0.0.1:18752'
    _, _, headers = ui.get('/manage?' + urlencode({'token': ui.management_token}), host=ui.host)
    cookie = headers['Set-Cookie']; browser_token = ui._browser(cookie)
    grant = q._save(root, root / 'host-runtime/fixture-grant.json', dict(fixture_only=True))
    request_ref = authority.create_request(root, step=6, native_intent=q._ref(root, record / 'native-intent.json'),
        call_id='0001-' + 'a' * 32, subject=subject, review_ref=ref, target=dict(configuration_files=[]),
        policy=dict(outcome='require_human_approval'), resources=dict(wall_time_seconds=10, max_storage_bytes=1000),
        grant_revision=grant, expires_at=time.time() + 30, secret=ui.secret)
    request_id = Path(request_ref['path']).parent.name
    tokens = authority.form_tokens(root, request_id, secret=ui.secret, boot=ui.boot, browser_token=browser_token)
    form = dict(tokens, selected_option='authorize_execution', rationale='Fixture operator decision')
    with pytest.raises(PermissionError, match='CSRF'):
        ui.post('/requests/' + request_id + '/decision', host=ui.host, origin='http://' + ui.host,
            cookie=cookie, content_type='application/x-www-form-urlencoded', body=urlencode(dict(form, csrf='forged')).encode())
    arguments = dict(host=ui.host, origin='http://' + ui.host, cookie=cookie,
        content_type='application/x-www-form-urlencoded', body=urlencode(form).encode())
    assert ui.post('/requests/' + request_id + '/decision', **arguments)[0] == 303
    original = (root / request_ref['path']).parent / 'decision.json'
    before = original.read_bytes()
    assert ui.post('/requests/' + request_id + '/decision', **arguments)[0] == 303
    assert original.read_bytes() == before
    assert authority.status(root, request_id, secret=ui.secret)['production'] == 'blocked_pending_resource_gate'
    assert not list(root.glob('**/accepted.json')) and not list(root.glob('**/submission.json'))
    with pytest.raises(ValueError, match='different content'):
        ui.post('/requests/' + request_id + '/decision', **dict(arguments,
            body=urlencode(dict(form, rationale='Changed decision content')).encode()))
    modified = json.loads(before); modified['selected_option'] = 'authorize_execution_with_exception'
    original.chmod(0o600); c.write(original, modified)
    with pytest.raises(ValueError, match='host receipt'):
        authority.decision(root, request_id, secret=ui.secret)


def declared_request(tmp_path):
    """Permission documents only; these declarations claim no scientific qualification."""
    root,secret_file=prepare(tmp_path)
    secret=secret_file.read_bytes();authority.bind_secret(root,secret)
    material=dict(path='artifacts/subject.py',source=str(tmp_path/'missing-scientific-original.py'),
        size_bytes=17)
    review_ref=dict(path='qualification/reviews/declared.json',size_bytes=31)
    job=dict(step=2,role='W',direct_materials=[material],selected_inputs=[],execution_qualifications=[review_ref])
    job_ref=q._save(root,root/'jobs/002.json',job)
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_job(root,2,job)
    native_ref=q._save(root,root/'native-evidence/002/native-intent.json',dict(step=2,task_ref=q.control_reference(root,job_ref)))
    subject=dict(producer_step=1,producer_seal=dict(path='native-evidence/001/result-identity.json',size_bytes=19),material=material)
    grant=q._save(root,root/'host-runtime/permission-fixture-grant.json',dict(scope='Declared permission owner only'))
    arguments=dict(step=2,native_intent=native_ref,call_id='0001-'+'d'*32,subject=subject,review_ref=review_ref,
        target=dict(configuration_files=[]),policy=dict(outcome='require_human_approval'),
        resources=dict(wall_time_seconds=10,max_storage_bytes=1000),grant_revision=grant,
        expires_at=time.time()+60,secret=secret)
    return root,secret,arguments


def test_permission_issue_read_decide_and_revoke_do_not_read_results_or_admission(tmp_path,monkeypatch):
    from scidiscovery.research_loop import budget,execution,native
    root,secret,arguments=declared_request(tmp_path)
    def forbidden(*args,**kwargs):pytest.fail('Permission path entered result, qualification or admission validation')
    for module,name in ((c,'_validate_inputs'),(execution,'records'),(authority.review,'require_review'),
            (q,'execution_payload'),(q,'output_status'),(q,'status'),(budget,'snapshot'),(native,'usage')):
        monkeypatch.setattr(module,name,forbidden)
    ref=authority.create_request(root,**arguments);request_id=Path(ref['path']).parent.name
    value,_=authority.request(root,request_id)
    payload={key:arguments['subject']['material']['source' if key=='path' else key] for key in ('path','size_bytes')}
    expected=dict(version=1,kind='execution_authorization',instance=c.settings(root)['goal_id'],contract=authority.contract(root),
        job=q._ref(root,root/'jobs/002.json'),native_intent=arguments['native_intent'],call_id=arguments['call_id'],
        subject=arguments['subject'],review=arguments['review_ref'],payload=payload,target=arguments['target'],
        policy=arguments['policy'],resources=arguments['resources'],grant_revision=arguments['grant_revision'],
        expires_at=arguments['expires_at'],options=list(authority.OPTIONS),scope=authority.SCOPE)
    assert {key:item for key,item in value.items() if key!='nonce_hash'}==expected
    assert request_id==hashlib.sha256(security.canonical(expected)).hexdigest()
    tokens=authority.form_tokens(root,request_id,secret=secret,boot='fixture-boot',browser_token='fixture-browser')
    authority.record_decision(root,request_id,secret=secret,boot='fixture-boot',browser_token='fixture-browser',
        **tokens,selected_option=authority.OPTIONS[0],rationale='Permission only; scientific eligibility remains separate')
    assert authority.status(root,request_id,secret=secret)['state']=='decided'
    authority.revoke(root,request_id,secret=secret,reason='Revoke without consulting unavailable scientific originals')
    assert authority.status(root,request_id,secret=secret)['state']=='revoked'
    assert not list(root.glob('**/accepted.json')) and not list(root.glob('**/submission.json'))


def test_permission_renewal_reuses_declared_payload_without_result_resolution(tmp_path,monkeypatch):
    root,secret,arguments=declared_request(tmp_path)
    payload=dict(path=str(tmp_path/'private-compiled-package.py'),size_bytes=53)
    first=authority.create_request(root,**arguments,payload_binding=payload)
    monkeypatch.setattr(q,'execution_payload',lambda *args:pytest.fail('Renewal resolved scientific outputs'))
    renewed=authority.create_request(root,**dict(arguments,expires_at=arguments['expires_at']+10),supersedes=first)
    new,_=authority.request(root,Path(renewed['path']).parent.name)
    assert new['payload']==payload and new['supersedes']==first
    assert authority.request_successor(root,first)==renewed


@pytest.mark.parametrize('changed',['native_requester','review_reference','payload_alias'])
def test_permission_rejects_changed_declared_identity_without_science(tmp_path,changed):
    root,secret,arguments=declared_request(tmp_path)
    ref=authority.create_request(root,**arguments)
    value,_=authority.request(root,Path(ref['path']).parent.name)
    if changed=='native_requester':value['native_intent']=dict(value['native_intent'],path='native-evidence/003/native-intent.json')
    elif changed=='review_reference':value['review']=dict(value['review'],path='qualification/reviews/another.json')
    else:value['payload_alias']=dict(value['payload'],path=str(tmp_path/'unbound-copy.py'))
    with pytest.raises(ValueError):authority._validate_binding(root,value)
