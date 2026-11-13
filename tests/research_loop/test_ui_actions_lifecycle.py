"""Bounded UI intent/identity fixtures; no native process, model or solver."""
import json
import os
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import configuration_form, contracts as c, control_operations
from scidiscovery.research_loop import instance_ui, fork_service, instance_delete
from test_execution_authority import prepare
from test_instance_ui import session, form, control_fields, post
from test_workspace_ui import controls


def test_start_response_loss_replays_original_operation_and_new_form_is_new_intent(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    launches = []
    def launch(*args, **kwargs):
        launches.append(args)
        return SimpleNamespace(pid=os.getpid())
    monkeypatch.setattr(control_operations.subprocess, 'Popen', launch)
    fields = form(ui, cookie, 'start')
    assert post(ui, cookie, 'start', fields)[0] == 303
    original = ui.control_result
    assert post(ui, cookie, 'start', fields)[0] == 303
    assert ui.control_result == original and len(launches) == 1
    # Distinct issued intents get distinct nonces without replaying the accepted launch.
    fresh = control_fields(ui, cookie, 'start')
    assert fresh['nonce'] != fields['nonce']
    assert len(list((root / 'host-control/operations').glob('*.json'))) == 1
    ui.translation.close()


@pytest.mark.parametrize('status', ['accepted', 'running', 'applied', 'failed'])
def test_saved_controller_intent_never_restarts_and_rejects_changed_request(tmp_path, monkeypatch, status):
    root, _ = prepare(tmp_path, enabled=False)
    operation_id = 'a' * 32
    request = dict(continue_run=False, ack_correction=None, authorization_note=None, resume_step=None)
    saved = dict(operation_id=operation_id, kind='controller', status=status, request=request,
                 action=dict(kind='run'), sequence=1)
    c.write(root / 'host-control/operations' / (operation_id + '.json'), saved)
    monkeypatch.setattr(control_operations.subprocess, 'Popen', lambda *a, **k: pytest.fail('Replay launched a process'))
    assert control_operations.start_background(root, operation_id=operation_id) == saved
    with pytest.raises(ValueError, match='another exact control request'):
        control_operations.start_background(root, operation_id=operation_id, continue_run=True)
    assert c.read(root / 'host-control/operations' / (operation_id + '.json')) == saved


def test_budget_response_loss_keeps_one_amendment_and_freezes_original_text(tmp_path):
    from scidiscovery.research_loop import controller
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    fields = dict(control_fields(ui, cookie, 'budget'), max_tokens='5000', max_seconds='', max_steps='',
                  authorization_note='  Original bounded adjustment.\n')
    with controller._writer(root):
        assert post(ui, cookie, 'budget', fields)[0] == 303
        original = ui.control_result
        assert post(ui, cookie, 'budget', fields)[0] == 303
        assert ui.control_result == original
        assert post(ui, cookie, 'budget', dict(fields, authorization_note='changed'))[0] == 422
    assert len(list((root / 'host-control/operations').glob('*.json'))) == 1
    ui.translation.close()


def test_standalone_locked_maintenance_is_disabled_before_nonce_or_effect(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    delete = controls(ui._delete_page(token)[1], 'delete')
    delete['confirmation'] = 'DELETE'
    fork = dict(control_fields(ui, cookie, 'fork-preview'), step='1', child_root=str(tmp_path / 'child'),
                max_tokens='10', max_seconds='10', max_steps='1', guidance_text='',
                expected_preview_id='', operation_id='locked')
    ui._serving = True
    monkeypatch.setattr(instance_delete, 'delete_instance', lambda *a, **k: pytest.fail('Locked delete dispatched'))
    monkeypatch.setattr(fork_service, 'preview', lambda *a, **k: pytest.fail('Locked fork dispatched'))
    for action, fields, route in [('delete', delete, '/delete'), ('fork-preview', fork, '/nodes/1/fork')]:
        page = ui.get(route, host=ui.host, cookie=cookie)[1]
        assert b'disabled' in page and b'action="/control/' not in page
        before = dict(ui.form_nonces)
        status, page, _ = post(ui, cookie, action, fields)
        assert status == 422 and '先关闭'.encode() in page and '工作台'.encode() in page
        assert ui.form_nonces == before
    ui.translation.close()


def test_loaded_same_path_replacement_preserves_registered_identity_and_rejects_control(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    host = instance_ui.InstanceUI(workspace, secret_file=key, workspace=True, instance_roots=[root])
    host.host = '127.0.0.1:18753'
    original = host._child(1)
    old_cookie = original.get('/manage?token=' + original.management_token, host=host.host)[2]['Set-Cookie']
    start = form(original, old_cookie, 'start')
    (root / 'pause.request').write_text('{}')
    assert host._child(1) is original  # Ordinary state changes preserve identity.
    root.rename(tmp_path / 'retained-original')
    replacement_base = tmp_path / 'replacement'; replacement_base.mkdir()
    replacement, _ = prepare(replacement_base, enabled=False)
    replacement.rename(root)
    with pytest.raises(ValueError, match='identity changed'):
        host._child(1)
    assert host.children[1] is original
    restarted = instance_ui.InstanceUI(workspace, secret_file=key, workspace=True)
    with pytest.raises(ValueError, match='identity changed'):
        restarted._child(1)
    monkeypatch.setattr(instance_ui.control, 'start_background', lambda *a, **k: pytest.fail('Old identity dispatched new work'))
    with pytest.raises(ValueError, match='identity changed'):
        host.post('/instances/1/control/start', host=host.host, origin='http://' + host.host,
            cookie=old_cookie, content_type='application/x-www-form-urlencoded',
            body=urlencode(start).encode())
    assert post(original, old_cookie, 'start', start)[0] == 422
    original.translation.close()
    restarted.translation.close()


def test_current_project_and_sets_only_declaration_round_trip_without_legacy_roots(tmp_path):
    document = dict(project=dict(base_root='/absolute/project', editable=['src'], scratch=['build'], outputs=['out']),
        skills=dict(sets=dict(tcad=dict(items=['sentaurus-tcad-code']))),
        agent=dict(roles=dict(W=dict(skill_set='tcad'), A=dict(worker='analysis')),
                   workers=dict(analysis=dict(model='fixture', skill_set='tcad'))))
    draft = configuration_form.imported(document, base=tmp_path)
    markup = configuration_form.render(draft).encode()
    fields = controls(b'<form action="/control/configure">' + markup + b'</form>')
    fields.pop('mode')
    parsed = configuration_form.parse(fields, base=tmp_path)
    assert parsed['document'] == document
    assert b'base_root = &quot;/absolute/project&quot;' in markup and b'cfg.agent.roles.W.skill_set' in markup
    assert b'[skills.sets.tcad]' in markup and b'skill_roots' not in markup
    exported = configuration_form.export_toml(parsed)
    assert json.loads(json.dumps(parsed['document'])) == document
    import tomllib
    assert tomllib.loads(exported)['project'] == document['project']
    # Existing-instance settings retain frozen project and set originals.
    edit = controls(b'<form action="/control/configuration-update">' + configuration_form.render(draft, edit=True).encode() + b'</form>', 'configuration-update')
    assert not any(key.startswith(('cfg.project.', 'enabled.project.', 'enabled.skills.')) for key in edit)
    assert configuration_form.parse_edit(edit, base=tmp_path, configuration=document)['document'] == document


def test_progress_omissions_and_sealed_lifecycle_are_explicit(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, _ = session(root, key)
    monkeypatch.setattr(instance_ui.progress, 'read', lambda *a, **k: dict(state='seal_present_unverified',
        stream_state='ready', events=[], role='H', has_more=True, malformed_lines=2,
        omitted_oversize_lines=1, tail_omitted=True, sealed_conclusion=dict(available=True)))
    value = ui._progress_value(1, 0, None)
    assert '已有阶段结论' in value['progress_label']
    assert '1 条超长日志' in value['progress_label'] and '2 条不可解析日志' in value['progress_label']
    assert '正在分批读取' in value['progress_label'] and '更早消息' in value['progress_label']
    ui.translation.close()


def test_live_budget_navigation_follows_closeout_without_replacing_forms(tmp_path, monkeypatch):
    import copy
    import re
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    token = ui._browser(cookie)
    snapshot = ui._snapshot()
    original_actions = str(ui._dashboard_parts(token, snapshot)['live-actions'])
    original_forms = re.findall(r'<form\b.*?</form>', original_actions, re.S)
    assert original_forms and 'action="/control/start"' in original_actions
    original_tools = ui._more_actions(token, snapshot)
    assert '<form' not in original_tools and 'id="live-budget-navigation"' in original_tools
    assert 'href="/configuration#budget-settings"' in ui._budget_navigation(snapshot)
    closing = copy.deepcopy(snapshot)
    closing['view']['closeout'] = dict(status='final_a_completed')
    monkeypatch.setattr(ui, '_snapshot_or_error', lambda **options: closing)
    fragments = ui._live_snapshot(token, '/instances/7', force=True)['fragments']
    navigation = fragments['live-budget-navigation']
    assert 'href="/instances/7/budget-recovery#budget"' in navigation
    assert '<form' not in navigation and 'nonce' not in navigation and 'more-actions' not in navigation
    assert '<form' not in ui._more_actions(token, closing)
    assert 'action="/instances/7/control/start"' not in str(fragments['live-actions'])
    # Updating the navigation does not consume or change the original action
    # credentials; lifecycle controls stay in their own live action row.
    unchanged_actions = str(ui._dashboard_parts(token, snapshot)['live-actions'])
    assert re.findall(r'<form\b.*?</form>', unchanged_actions, re.S) == original_forms
    # Returning to an ordinary state restores the canonical configuration link.
    monkeypatch.setattr(ui, '_snapshot_or_error', lambda **options: snapshot)
    restored = ui._live_snapshot(token, '/instances/7', force=True)['fragments']['live-budget-navigation']
    assert 'href="/instances/7/configuration#budget-settings"' in restored
    assert 'budget-recovery' not in restored
    ui.translation.close()
