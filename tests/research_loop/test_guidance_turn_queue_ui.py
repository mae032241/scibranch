"""Guidance UI follows exact current-turn claims without resubmission."""
import json
from urllib.parse import urlencode

import pytest

from scidiscovery.research_loop import contracts, turn_input, ui_guidance, user_guidance
from test_instance_ui import control_fields, post, session
from test_execution_authority import prepare


def test_guidance_post_current_turn_claim_and_readonly_status_poll(tmp_path, monkeypatch):
    root, key = prepare(tmp_path, enabled=False)
    ui, cookie = session(root, key)
    reader = ui.get('/view?' + urlencode(dict(token=ui.read_token)), host=ui.host)[2]['Set-Cookie']
    context = turn_input.begin(root, dict(step=1, role='W'), tmp_path/'native-work', 1,
        task_ref=dict(path='jobs/001.json'), base=contracts.settings(root))
    try:
        turn_input.poll(context, thread='private-thread', turn='private-turn')
        fields = dict(control_fields(ui, cookie, 'guidance-submit'), text='Exact current advice',
            target='W', kind='advice', scope='next', effective_step='')
        with pytest.raises(PermissionError):
            post(ui, reader, 'guidance-submit', fields)
        forged = dict(fields, csrf='wrong')
        with pytest.raises(PermissionError):
            post(ui, cookie, 'guidance-submit', forged)
        assert post(ui, cookie, 'guidance-submit', fields)[2]['Location'] == '/guidance'
        row = user_guidance.list_guidance(root)[0]
        assert row['status'] == 'pending' and row['turn_input']['status'] == 'queued'
        page = ui.get('/guidance', host=ui.host, cookie=cookie)[1].decode()
        assert '已加入当前 turn 待发队列' in page
        assert 'data-guidance-edit hidden' in page and 'data-guidance-revoke hidden' in page
        assert 'data-status-url="guidance/status"' in page
        edit = dict(control_fields(ui, cookie, 'guidance-edit'), guidance_id=row['guidance_id'],
            expected_revision='1', text='Cannot change a bound message')
        assert post(ui, cookie, 'guidance-edit', edit)[0] >= 400
        original_list = user_guidance.list_guidance
        reads = []
        def listing(root):
            reads.append(True)
            return original_list(root)
        monkeypatch.setattr(user_guidance, 'list_guidance', listing)
        status, raw, headers = ui.get('/guidance/status', host=ui.host, cookie=reader)
        assert status == 200 and len(reads) == 1
        assert headers['Cache-Control'] == 'no-store'
        state = json.loads(raw)['rows'][0]
        assert state['guidance_id'] == row['guidance_id']
        assert not state['editable'] and not state['revocable']
        assert not any(secret in raw.decode() for secret in ('Exact current advice', 'private-thread',
            'private-turn', 'channel_id', 'input_id', str(root)))
        with pytest.raises(PermissionError):
            ui.get('/guidance/status', host=ui.host)
        with pytest.raises(ValueError):
            ui.get('/guidance/status?extra=1', host=ui.host, cookie=reader)
        requests = contracts.read(context['inbox']/'inbox.json')
        turn_input.poll(context, event=turn_input.Inbox.receipt(requests[0], 'accepted'))
        applied = original_list(root)[0]
        assert applied['status'] == 'applied'
        state = json.loads(ui.get('/guidance/status', host=ui.host, cookie=reader)[1])['rows'][0]
        assert state['label'] == '已加入当前 turn 待发队列'
        assert not state['editable'] and not state['revocable']
        assert '不表示模型已读取' in state['note']
    finally:
        turn_input.end(context)


@pytest.mark.parametrize('claim_status', ['queued', 'submitting', 'unknown'])
def test_guidance_claim_actions_freeze_until_proven_unsubmitted_release(claim_status):
    row = dict(guidance_id='a'*32, status='pending', scope='research',
        turn_input=dict(status=claim_status, step=3, role='H'))
    state = ui_guidance.state(row, manager=True)
    assert not state['editable'] and not state['revocable']
    released = dict(row, last_turn_input=dict(status='unavailable'))
    released.pop('turn_input')
    state = ui_guidance.state(released, manager=True)
    assert state['editable'] and state['revocable']
    assert '未提交证明' in state['note']
    assert 'replaceChildren' not in ui_guidance.SCRIPT and 'location.reload' not in ui_guidance.SCRIPT
