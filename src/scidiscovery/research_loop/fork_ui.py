"""Escaped maintenance form for previewing and creating paused forks."""
import html
import json
import uuid


FIELDS = {'step', 'child_root', 'max_tokens', 'max_seconds', 'max_steps', 'guidance_text',
          'expected_preview_id', 'operation_id'}


def fields(step, draft=None):
    value = dict(step=str(step), child_root='', max_tokens='', max_seconds='', max_steps='',
                 guidance_text='', expected_preview_id='', operation_id=uuid.uuid4().hex)
    value.update({key: item for key, item in (draft or {}).items() if key in FIELDS})
    hidden = ''.join('<input type="hidden" name="' + key + '" value="'
        + html.escape(str(value[key]), quote=True) + '">'
        for key in ('step', 'expected_preview_id', 'operation_id'))
    for key, label in [('child_root', '新分支的完整目录'), ('max_tokens', '新分支 tokens 上限'),
                       ('max_seconds', '新分支活动时间上限（秒）'), ('max_steps', '新分支新增控制动作上限')]:
        hidden += '<label>' + label + '<input name="' + key + '" required value="' + html.escape(str(value[key]), quote=True) + '"></label>'
    hidden += '<label>方向指引原文（可留空）<textarea name="guidance_text">' + html.escape(value['guidance_text']) + '</textarea></label>'
    return hidden


def options(form):
    return dict(budget=dict(max_tokens=int(form['max_tokens']), max_seconds=float(form['max_seconds']),
                            max_steps=int(form['max_steps'])), guidance_text=form['guidance_text'] or None)


def reconciliation_fields(draft):
    """Freeze the attempted request; editing cannot mutate receipt recovery."""
    return ''.join('<input type="hidden" name="' + key + '" value="'
        + html.escape(str(draft[key]), quote=True) + '">' for key in sorted(FIELDS))


def summary(preview):
    # Only this service-authored public summary is rendered, never origins or host paths.
    value = {key: preview[key] for key in ('source', 'cut', 'budget', 'native', 'guidance', 'inherited_costs',
             'limitations', 'blockers', 'entry_count', 'size_bytes', 'status') if key in preview}
    return '<section><h2>分支预览</h2><pre>' + html.escape(json.dumps(value, ensure_ascii=False, indent=2)) + '</pre></section>'
