"""Pure H/W scientific intent; A assessments retain the authored direction.

This parser does not publish, rank mechanisms, infer evidence or grant execution.
Optional assessment planning stays in its original and never reallocates work.
"""
from __future__ import annotations

import copy


def focus_requirement(job):
    """Describe the consumed author choice, never supply a scientific default."""
    producer=job['role'] in {'H','W'}
    path='search_plan.focus' if producer else 'search_review.next_focus'
    shape=('{index:1-based commitment index|null,action:screen|improve|repair|validate}; index:null requires screen'
        if producer else '{mechanism_ref:exact listed ref|null,action:screen|improve|repair|validate}; null requires screen')
    return path,shape


def focus_diagnostic(job, *, code='science_task_action_required'):
    path,shape=focus_requirement(job)
    expected=('top-level search_plan with commitments:[{label,existing_ref?}] and focus:'+shape
        if job['role'] in {'H','W'} else shape)
    return dict(code=code,subject=path,expected=expected,
        message=f'Correct {job["role"]} output {path}: expected {expected}')


def resolve_discriminating_work(state,job,payload):
    """Retain prior work facts; optional A planning is advisory only."""
    return copy.deepcopy(state['order'].get('discriminating_work',[]))


def resolve_intent(state, job, payload, *, assessed=None):
    """Consume H/W choices while an A assessment preserves their authority."""
    if job['role']=='A':
        return dict(focus=copy.deepcopy(state.get('focus')),
            allocation=copy.deepcopy(state['order'].get('allocation')),
            unfinished_plan=copy.deepcopy(state['order']['remaining']),
            seen_w=set(state['order']['seen_w']),diagnostics=[],
            discriminating_work=resolve_discriminating_work(state,job,payload))
    return _resolve_producer_intent(state,job,payload,assessed=assessed)


def _resolve_producer_intent(state, job, payload, *, assessed=None):
    mechanisms=state['mechanisms'];authorized=state['authorized']
    remaining=copy.deepcopy(state['order']['remaining'])
    seen=set(state['order']['seen_w'])
    role=job['role'];producer=role in {'H','W'};action=payload.get('next_action')
    choice=payload.get('search_plan') if producer else payload.get('search_review')
    choice=choice if isinstance(choice,dict) else {}
    declared=choice.get('focus' if producer else 'next_focus')
    key='index' if producer else 'mechanism_ref'
    invalid=declared is not None and (not isinstance(declared,dict) or not isinstance(declared.get('action'),str) or declared['action'] not in {'screen','improve','repair','validate'}
        or key not in declared or 'label' in declared)
    if declared is not None and not invalid:
        name=declared[key]
        if producer:
            commitments=choice.get('commitments',[])
            invalid=name is not None and (type(name) is not int or not isinstance(commitments,list) or not 1<=name<=len(commitments))
        else:invalid=name is not None and not isinstance(name,str)
    if invalid:declared=None
    selected=None;diagnostics=[focus_diagnostic(job,code='science_choice_unresolved')] if invalid else []
    if declared is not None and declared[key] is not None:
        name=declared[key]
        if producer:
            item=choice['commitments'][name-1]
            name=item.get('existing_ref') or f'{role.lower()}{job["step"]:03d}.m{name}'
        selected=name if name in authorized else None
        if selected is None:
            path,_=focus_requirement(job)
            diagnostics.append(dict(code='science_focus_unauthorized',subject=name,expected='an authorized mechanism',
                message=f'Correct {role} output {path}: {name} is not an authorized mechanism'))
        elif mechanisms[selected]['status']=='waived':
            path,_=focus_requirement(job)
            diagnostics.append(dict(code='science_focus_waived',subject=selected,expected='a non-waived mechanism',
                message=f'Correct {role} output {path}: {selected} is waived; choose a non-waived mechanism'))
        elif role!='W' and job.get('purpose')!='worker_consultation' and not (payload.get('route_reason') or '').strip():
            diagnostics.append(dict(code='science_focus_reason_required',subject='route_reason',expected='a nonblank string',
                message=f'Correct {role} output route_reason: expected a nonblank scientific reason for the chosen focus'))
    if declared is not None and declared[key] is None and declared['action']!='screen':
        diagnostics.append(focus_diagnostic(job,code='science_focus_required'))
    retained=state.get('focus')
    keep=(declared is None and not diagnostics and retained is not None and retained['mechanism_ref'] in authorized
        and mechanisms[retained['mechanism_ref']]['status']!='waived')
    if keep:selected=retained['mechanism_ref']
    remaining=[item for item in remaining if mechanisms[item['mechanism_ref']]['status']!='waived']
    focus=None;allocation=None
    if keep:
        focus=copy.deepcopy(retained)
        allocation=next((item for item in remaining if item['mechanism_ref']==retained['mechanism_ref']),None)
    if action in {'experiment','continue_w'} or producer and declared is not None and (role=='W' or job.get('purpose')=='worker_consultation'):
        if declared is None:
            if focus is None and not diagnostics:diagnostics.append(focus_diagnostic(job))
        elif selected is None and not diagnostics and declared['action']=='screen':
            available=[item for item in remaining if item['mechanism_ref'] in authorized]
            if not available:
                diagnostics.append(dict(code='science_plan_empty',subject='unfinished scientific plan'))
            else:
                selected=available[0]['mechanism_ref']
                focus=dict(mechanism_ref=selected,action='screen')
        elif selected is not None and not diagnostics:
            focus=dict(mechanism_ref=selected,action=declared['action'])
        if focus is not None:
            allocation=next((item for item in remaining if item['mechanism_ref']==selected),None)
    elif job.get('purpose')=='worker_consultation' or role not in {'H','A'} or action in {'audit','formal_review','extract','wait'}:
        focus=copy.deepcopy(state.get('focus'))
        allocation=copy.deepcopy(state['order'].get('allocation'))
    return dict(focus=focus,allocation=allocation,unfinished_plan=remaining,seen_w=seen,diagnostics=diagnostics,
        discriminating_work=resolve_discriminating_work(state,job,payload))
