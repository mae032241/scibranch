"""Pure science allocation from a copied fold of exact sealed originals.

This parser does not publish, rank mechanisms, infer evidence or grant execution.
The caller supplies one eligible final W assessment, established from the original
A binding. A preliminary interpretation is committed only after all diagnostics
are resolved in the original control outcome.
"""
from __future__ import annotations

import copy


def resolve_discriminating_work(state,job,payload):
    """Rebuild work from authored A declarations, separate from call allocation."""
    rows=copy.deepcopy(state['order'].get('discriminating_work',[]))
    review=payload.get('portfolio_review')
    if job['role']!='A' or payload.get('next_action')=='wait' or not isinstance(review,dict):return rows
    by_ref={row['work_ref']:row for row in rows}
    for update in review['work_updates']:
        row=by_ref.get(update['work_ref'])
        if row is None or row['status']=='withdrawn':continue  # Raw contract gate rejects this output.
        row['status']={'execute':'executed','defer':'deferred','withdraw':'withdrawn'}[update['action']]
        row['updates'].append(dict(source_a=job['step'],**copy.deepcopy(update)))
        if update['action']=='execute':row['executions'].append(dict(source_a=job['step'],response=None))
    work=review['discriminating_work']
    if work is not None and work['existing_ref'] is None:
        executed=work['next_action']==payload.get('next_action')
        rows.append(dict(work_ref=f'A{job["step"]:03d}-D001',source_a=job['step'],
            declaration_pointer='/portfolio_review/discriminating_work',
            **{key:copy.deepcopy(work[key]) for key in ('mechanism_refs','question','change_criterion','next_action')},
            status='executed' if executed else 'deferred',
            updates=[dict(source_a=job['step'],action='execute' if executed else 'defer',reason=review['reason'],
                deferral=copy.deepcopy(review['deferral']))],
            executions=[dict(source_a=job['step'],response=None)] if executed else []))
    return rows


def resolve_intent(state, job, payload, *, assessed=None):
    mechanisms=state['mechanisms'];authorized=state['authorized']
    remaining=copy.deepcopy(state['order']['remaining'])
    seen=set(state['order']['seen_w'])
    role=job['role'];action=payload.get('next_action')
    choice=(payload.get('search_plan') if role=='H' else payload.get('search_review')) or {}
    declared=choice.get('focus' if role=='H' else 'next_focus')
    selected=None;diagnostics=[]
    if declared is not None and declared['label' if role=='H' else 'mechanism_ref'] is not None:
        name=declared['label' if role=='H' else 'mechanism_ref']
        matches=[name] if name in authorized else [ref for ref in authorized if mechanisms[ref]['label']==name]
        selected=matches[0] if len(matches)==1 else None
        if selected is None:
            diagnostics.append(dict(code='science_focus_ambiguous' if matches else 'science_focus_unauthorized',subject=name))
        elif mechanisms[selected]['status']=='waived':
            diagnostics.append(dict(code='science_focus_waived',subject=selected))
        elif not (payload.get('route_reason') or '').strip():
            diagnostics.append(dict(code='science_focus_reason_required',subject=selected))
    if declared is not None and declared['label' if role=='H' else 'mechanism_ref'] is None and declared['action']!='screen':
        diagnostics.append(dict(code='science_focus_required',subject=declared['action']))
    repair=declared is not None and declared['action']=='repair'
    if role=='A' and action not in {'audit','formal_review','extract','wait'} and assessed is not None:
        identity=assessed['identity']
        if identity not in seen:
            allocation=assessed.get('allocation')
            same_repair=repair and selected in (assessed.get('mechanism_refs') or [assessed['mechanism_ref']])
            if not same_repair:
                if assessed.get('breadth'):
                    disposed={row['mechanism_ref'] for row in choice.get('dispositions',[]) if row['status'] in {'screened','deferred','waived'} and mechanisms.get(row['mechanism_ref'],{}).get('status')==row['status']}
                    remaining=[item for item in remaining if item['mechanism_ref'] not in disposed]
                elif allocation is not None:
                    remaining=[item for item in remaining if item!=allocation]
                seen.add(identity)
    remaining=[item for item in remaining if mechanisms[item['mechanism_ref']]['status']!='waived']
    focus=None;allocation=None
    if action in {'experiment','continue_w'}:
        if declared is None:
            diagnostics.append(dict(code='science_task_action_required',subject='W scientific task'))
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
    elif role not in {'H','A'} or action in {'audit','formal_review','extract','wait'}:
        focus=copy.deepcopy(state.get('focus'))
        allocation=copy.deepcopy(state['order'].get('allocation'))
    return dict(focus=focus,allocation=allocation,unfinished_plan=remaining,seen_w=seen,diagnostics=diagnostics,
        discriminating_work=resolve_discriminating_work(state,job,payload))
