"""Freeze all public candidates for an explicit, evidence-bound A decision.

Catalogue preparation performs no source preparation, smoke or audit call.
Qualification is obtained only for an explicitly requested audit or delivery.
"""
from __future__ import annotations

from . import configuration_updates
from . import budget, contracts as c, delivery, originals, selection
from .candidate_audit import _save


def policy(value):
    if not isinstance(value, dict) or set(value) != {'version'} or type(value['version']) is not int or value['version'] != 2:
        raise ValueError('Selection policy requires version 2 explicit candidate comparison')
    return dict(value)


def configured(root):
    value = configuration_updates.current(root).get('selection_policy')
    return policy(value) if value is not None else None


def _directory(root, before_step=None, *, repair_step=None, normal_step=None):
    from . import engineering
    if normal_step is not None:
        path=root/'host-delivery/selection/nodes'/f'{normal_step:03d}'
        if path.resolve()!=path:raise ValueError('Control selection original location changed')
        return path
    if repair_step is None:
        repaired=engineering.steps(root,before_step)
        if repaired:
            begin=budget.closeout_state(root)['begin']
            repaired=[step for step in repaired if begin and step>begin['source_cutoff']]
        repair_step=max(repaired,default=0)
    path = root / 'host-delivery/selection'
    if repair_step:path=path/'repairs'/f'{repair_step:03d}'
    if path.resolve() != path:
        raise ValueError('Control selection original location changed')
    return path


def _boundary(root, begin):
    return dict(seq=begin['seq'],record=c.record_ref(root/'control-ledger'/f'{begin["seq"]:06d}.json'))


def _members(root, begin, before_step=None):
    from . import history, engineering
    repaired=[step for step in engineering.steps(root,before_step) if step>begin['source_cutoff']]
    end=max(repaired,default=begin['source_cutoff'])+1
    records = selection.public_inputs(history.rebuild(root,end,source_cutoff=begin['source_cutoff'],extra_steps=repaired,write_cache=False))
    return selection.shortlist(records)


def screen(root, begin, before_step=None):
    """Freeze every candidate without starting any external action."""
    from .protocol import require_current
    require_current(root)
    configured(root)
    directory = _directory(root,before_step)
    final = directory / 'screen.json'
    if final.exists():
        return None
    path = directory / 'shortlist.json'
    computed = dict(_members(root, begin,before_step), closeout_begin=_boundary(root, begin))
    if path.exists():
        if c.read(c.regular(path)) != computed:
            raise ValueError('Control catalogue differs from its sealed public prefix')
    else:
        _save(path, computed)
    # Do not prepare catalogue sources or qualify a candidate before A names it.
    # Existing bound receipts remain available in the frozen history audit index.
    rows = [dict(member, smoke=dict(status='not_run', note='explicit_candidate_required'),
                 audit=dict(status='not_run', note='read_exact_candidate_audit_receipts'))
            for member in computed['members']]
    _save(final, dict(version=2, public_view_version=3, shortlist_ref=c.record_ref(path),
        closeout_begin=computed['closeout_begin'], rows=rows))
    return None


def bind_normal(root, job):
    """Freeze the exact comparison input before an ordinary A sees its task."""
    from . import history
    directory=_directory(root,normal_step=job['step'])
    path=directory/'shortlist.json'
    computed=dict(selection.shortlist(selection.public_inputs(history.rebuild(root,job['step'],write_cache=False))),
        source_nodes=job['history']['nodes'])
    if path.exists():
        if c.read(c.regular(path))!=computed:raise ValueError('Ordinary A comparison input changed')
    else:_save(path,computed)
    final=directory/'screen.json'
    frozen=dict(version=2,public_view_version=3,shortlist_ref=c.record_ref(path),source_nodes=computed['source_nodes'],
        rows=[dict(member,smoke=dict(status='not_run',note='explicit_candidate_required'),
            audit=dict(status='not_run',note='read_exact_candidate_audit_receipts')) for member in computed['members']])
    if final.exists():
        if c.read(c.regular(final))!=frozen:raise ValueError('Ordinary A comparison catalogue changed')
    else:_save(final,frozen)
    return dict(version=2,record=c.record_ref(final),normal_step=job['step'])


def marker(root,before_step=None):
    directory=_directory(root,before_step)
    path = directory/'screen.json'
    if not path.exists():return None
    value=c.read(c.regular(path))
    if value.get('version') != 2 or value.get('public_view_version') != 3:
        raise ValueError('Unsupported frozen candidate catalogue')
    if not value['rows']:return None
    return dict(version=2,record=c.record_ref(path),**(dict(repair_step=int(directory.name)) if directory.parent.name=='repairs' else {}))


def _screen(root, mark, *, job=None):
    if mark.get('version') != 2:raise ValueError('Unsupported frozen candidate catalogue marker')
    directory = _directory(root,repair_step=mark.get('repair_step',0),normal_step=mark.get('normal_step'))
    path = directory / 'screen.json'
    source=None
    if job is not None and job.get('fork_source'):
        current=originals.node(root,job['step'])
        ref=job['fork_source']['job']
        source=originals.source_for_ref(current,ref)
        seen=set()
        while True:
            identity=(originals.identity_key(source.origin_identity),source.step)
            if identity in seen:raise ValueError('Fork candidate catalogue ancestry cycle')
            seen.add(identity)
            original=originals.source_job(source)
            if original.get('selection')!=mark:
                raise ValueError('Fork candidate catalogue differs from its exact original task')
            if not original.get('fork_source'):break
            ref=original['fork_source']['job']
            source=originals.source_for_ref(source,ref)
            originals.resolve_ref(source,ref)
        path=originals.resolve_ref(source,dict(path=path.relative_to(root).as_posix()))
    if (history.source_control_ref(source,(directory/'screen.json').relative_to(root).as_posix()) if source is not None else c.record_ref(c.regular(path))) != mark['record']:
        raise ValueError('Control candidate catalogue changed')
    value = c.read(path)
    if value.get('version') != 2 or value.get('public_view_version') != 3:
        raise ValueError('Unsupported frozen candidate catalogue')
    if mark.get('normal_step') is not None and job is not None:
        owner_job=originals.source_job(source) if source is not None else job
        if mark['normal_step']!=owner_job['step'] or value.get('source_nodes')!=owner_job['history']['nodes']:
            raise ValueError('Ordinary A catalogue differs from its exact scientific input')
    original=directory/'shortlist.json'
    if source is not None:
        original=originals.resolve_ref(source,dict(path=original.relative_to(root).as_posix()))
    if value['shortlist_ref'] != (history.source_control_ref(source,(directory/'shortlist.json').relative_to(root).as_posix()) if source is not None else c.record_ref(c.regular(original))):
        raise ValueError('Control candidate catalogue source changed')
    if source is not None:
        from . import candidate_refs
        value=candidate_refs.public_fields(value,candidate_refs.source_input_mapping(source,originals.source_job(source)))
    return value


def public_table(root, mark):
    value = _screen(root, mark)
    return dict(rows=[dict(candidate_id=row['candidate_id'], public_metric=row['metric'],
            evaluation_version=row['evaluation_key'], direction=row['direction'], status=row['status'],
            first_step=row['first_step'], smoke=row['smoke']['status'], audit=row['audit']['status'])
            for row in value['rows']],
        rule=selection.SHORTLIST_RULE,
        fields=('candidate_comparison optionally records scientifically relevant alternatives in your chosen order. '
            'Apply the same original-goal criteria and explain relevant omissions or unresolved alternatives in your scientific reasoning. '
            'For delivery explicitly name candidate_recommendation and explain your scientific reasoning in scientific_result. '
            'The comparison is explanation, not delivery permission. Null recommendation supplies no choice. No checks are run while preparing this catalogue; '
            'read existing exact audit receipts, request audit for an explicit candidate, and honor required delivery qualification.'))


def summary(root):
    path = _directory(root)/'screen.json'
    if not path.exists():return None
    value = c.read(c.regular(path))
    return dict(members=[dict(candidate_id=row['candidate_id'], smoke=row['smoke']['status'], audit=row['audit']['status'])
                         for row in value['rows']])


def schema():
    row = dict(type='object', properties=dict(candidate_id=dict(type='string'),
        key_risk=dict(type='string'), decision=dict(type='string')),
        required=['candidate_id', 'key_risk', 'decision'], additionalProperties=False)
    return dict(type=['object', 'null'], properties=dict(rows=dict(type='array', items=row),
        rationale=dict(type=['string', 'null']),
        evidence_steps=dict(type='array', items=dict(type='integer'))),
        required=['rows', 'rationale', 'evidence_steps'], additionalProperties=False,
        description='Optional scientific explanation. It grants no qualification or delivery permission; candidate_recommendation supplies the explicit choice.')


def choice(root, job, payload):
    """Only the author's explicit, evidence-bound candidate can be delivered."""
    chosen = payload.get('candidate_recommendation')
    if chosen is None:raise delivery.DeliveryUnresolved('delivery_no_decision')
    value = _screen(root, job['selection'], job=job)
    members = [row['candidate_id'] for row in value['rows']]
    if chosen not in members:raise delivery.DeliveryUnresolved('selection_candidate_unavailable')
    return chosen
