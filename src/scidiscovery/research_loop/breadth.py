"""Frozen registered-call opportunities; these are never scientific verdicts."""
from __future__ import annotations


class OpportunityRequired(ValueError):
    code = 'scientific_opportunity_required'

    def __init__(self,message,*,reason=None,available_mechanisms=()):
        super().__init__(message)
        self.reason=reason
        self.available_mechanisms=list(available_mechanisms)


def validate(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'version','initial_calls','mechanisms','baseline','grant','prior_unknown','outside_initial_owed','catalog'}
            or value['version'] != 2 or type(value['initial_calls']) is not int or value['initial_calls'] < 1
            or type(value['prior_unknown']) is not bool):
        raise ValueError('Exact frozen scientific opportunity configuration required')
    if 'outside_initial_owed' in value and (type(value['outside_initial_owed']) is not int or value['outside_initial_owed']<0):
        raise ValueError('Frozen omitted opportunity count differs')
    catalog=value['catalog']
    if (not isinstance(catalog,dict) or set(catalog)!={'prefix','max_ordinal'}
            or not isinstance(catalog['prefix'],dict) or catalog['prefix'].get('version')!=1
            or type(catalog['prefix'].get('cutoff')) is not int or catalog['prefix']['cutoff']<1
            or type(catalog['max_ordinal']) is not int or catalog['max_ordinal']<1):
        raise ValueError('Exact frozen mechanism catalog required')
    rows = value['mechanisms']
    if not isinstance(rows,list) or not rows:
        raise ValueError('Scientific opportunity combination requires mechanisms')
    refs = set();ordinals=set()
    for row in rows:
        if (not isinstance(row,dict) or set(row) != {'mechanism_ref','label','source_h','source_h_ref','minimal_test','status','ordinal','origin_ref','source_goal_id'}
                or row['mechanism_ref'] in refs or any(not isinstance(row[key],str) or not row[key] for key in ('mechanism_ref','label','minimal_test'))
                or type(row['source_h']) is not int or row['source_h'] < 1
                or not isinstance(row['source_h_ref'], dict) or set(row['source_h_ref']) != {'path'} or not isinstance(row['source_h_ref']['path'],str)
                or row['status'] not in {'pending','screened','deferred','waived'}):
            raise ValueError('Scientific opportunity mechanism binding differs')
        _catalog_binding(dict(ordinal=row['ordinal'],origin_ref=row['origin_ref'],source_goal_id=row['source_goal_id']),row['mechanism_ref'],catalog)
        if row['ordinal'] in ordinals:raise ValueError('Mechanism catalog number is repeated')
        ordinals.add(row['ordinal'])
        refs.add(row['mechanism_ref'])
    baseline = value['baseline']
    if (not isinstance(baseline,list) or len(baseline) != len(refs)
            or {row.get('mechanism_ref') for row in baseline} != refs
            or any(set(row) != {'mechanism_ref','accepted_count'} or type(row['accepted_count']) is not int or row['accepted_count'] < 0 for row in baseline)):
        raise ValueError('Frozen accepted-call baseline differs')
    grant = value['grant']
    if grant is not None and (not isinstance(grant,dict) or set(grant) != {'mechanism_ref','source_a','source_a_ref','extra_calls','used_calls'}
            or grant['mechanism_ref'] not in refs or type(grant['source_a']) is not int or grant['source_a'] < 1
            or not isinstance(grant['source_a_ref'], dict) or set(grant['source_a_ref']) != {'path'}
            or grant['extra_calls'] != 1 or type(grant['used_calls']) is not int or grant['used_calls'] not in (0,1)):
        raise ValueError('Frozen A opportunity authorization differs')
    return value


def check_binding(value, binding, scope, *, _validated=False, _rows=None):
    if not _validated:validate(value)
    if value is None:
        if binding is not None:raise ValueError('Unexpected scientific call binding')
        return None
    if not isinstance(binding,dict) or set(binding) != {'mechanism_ref','source_h','source_h_ref','job_ref','grant','catalog_binding'}:
        raise ValueError('Accepted scientific call binding missing')
    row = _rows.get(binding['mechanism_ref']) if _rows is not None else next((row for row in value['mechanisms'] if row['mechanism_ref'] == binding['mechanism_ref']),None)
    proof=_catalog_binding(binding['catalog_binding'],binding['mechanism_ref'],value['catalog'])
    if (binding['job_ref'] != scope['job_ref'] or type(binding['source_h']) is not int
            or not 0<binding['source_h']<=value['catalog']['prefix']['cutoff']
            or not isinstance(binding['source_h_ref'],dict) or set(binding['source_h_ref'])!={'path'}
            or not isinstance(binding['source_h_ref']['path'],str)):
        raise ValueError('Accepted scientific call escaped frozen combination')
    if proof['origin_ref']['source_h']>binding['source_h']:
        raise ValueError('Accepted mechanism predates its original declaration')
    if row is not None and (any(binding[key]!=row[key] for key in ('source_h','source_h_ref'))
            or proof!=dict(ordinal=row['ordinal'],origin_ref=row['origin_ref'],source_goal_id=row['source_goal_id'])):
        raise ValueError('Accepted scientific call changed its frozen model')
    grant = binding['grant']
    if grant is not None:
        allowed = value['grant']
        if allowed is None or grant != {key:allowed[key] for key in ('source_a','source_a_ref')} or allowed['mechanism_ref'] != binding['mechanism_ref']:
            raise ValueError('Accepted extra opportunity escaped exact A authorization')
    return binding


def _catalog_binding(proof,ref,catalog):
    if (not isinstance(proof,dict) or set(proof)!={'ordinal','origin_ref','source_goal_id'}
            or type(proof['ordinal']) is not int or not 1<=proof['ordinal']<=catalog['max_ordinal']
            or not isinstance(proof['source_goal_id'],str) or not proof['source_goal_id']):
        raise ValueError('Exact accepted mechanism catalog proof required')
    origin=proof['origin_ref']
    if (not isinstance(origin,dict) or set(origin)!={'goal_id','source_h','mechanism_ref'}
            or not isinstance(ref,str) or not ref
            or not isinstance(origin['goal_id'],str) or not origin['goal_id']
            or type(origin['source_h']) is not int or not 0<origin['source_h']<=catalog['prefix']['cutoff']
            or origin['mechanism_ref']!=ref):
        raise ValueError('Accepted mechanism original identity changed')
    return proof


def _catalog_root(value,scope):
    from pathlib import Path
    from . import contracts as c
    path=Path(scope['job_ref']['path'])
    if not path.is_absolute() or path.parent.name!='jobs' or path.name!=f'{scope["step"]:03d}.json':
        raise ValueError('Mechanism catalog requires its exact current task')
    job=c.read(c.regular(path))
    if c.record_ref(path)!=scope['job_ref'] or (job.get('search') or {}).get('breadth')!=value:
        raise ValueError('Mechanism catalog differs from its frozen task')
    if job.get('history',{}).get('prefix')!=value['catalog']['prefix']:
        raise ValueError('Mechanism catalog changed its frozen visibility')
    return path.parent.parent,(job.get('search') or {}).get('focus')


def resolve_choice(value,choice,scope,*,head=None,_root=None,_focus=None,_validated=False):
    """Resolve the submitted number/ref once, without expanding the catalog."""
    from . import accepted_search
    if not _validated:validate(value)
    if value is None:
        if choice is not None:raise OpportunityRequired('No H mechanism is authorized for this stage')
        return None
    try:
        root,focus=_catalog_root(value,scope) if _root is None else (_root,_focus)
        ref,item,head,member=accepted_search.catalog_item(root,value['catalog'],choice,head=head)
        if focus and focus['action']!='screen' and ref!=focus['mechanism_ref']:
            raise ValueError('This scientific task permits only its frozen focused mechanism')
    except (ValueError,FileNotFoundError) as error:
        raise OpportunityRequired(str(error)) from error
    return dict(mechanism_ref=ref,label=item['label'],minimal_test=item['minimal_test'],status=item['status'],
        source_h=item['source_h'],source_h_ref=member['seal_ref'],ordinal=item['ordinal'],origin_ref=item['origin_ref'],
        source_goal_id=item['source_origin_identity']['goal_id'],accepted_count=item['accepted_calls'],_head=head,_root=root,_focus=focus)


_REFUSALS={
    'original_execution_unknown': 'Preserve the original unknown execution; resolve its identity before a new submission',
    'mechanism_waived': 'This mechanism was scientifically waived by A',
    'focused_mechanism_required': 'This scientific task permits only its frozen focused mechanism',
    'initial_opportunities_owed': 'Other H mechanisms retain initial call opportunities; report the scientific question to A for justified extra allocation',
    'mechanism_choice_required': 'Choose one mechanism number from this task frozen catalog',
}


def _availability(value,scope,accepted,*,head,focus):
    """Use one accepted table for both admission and public numeric choices."""
    from . import accepted_search
    items=head['search']['table']['mechanisms']
    counts={ref:item['accepted_calls'] for ref,item in items.items()}
    owed=head['search']['owed_count']
    grant=value['grant'];used=grant['used_calls'] if grant else 0
    rows={row['mechanism_ref']:row for row in value['mechanisms']}
    for original in accepted:
        binding=check_binding(value,original.get('mechanism_binding'),scope,_validated=True,_rows=rows)
        ref=binding['mechanism_ref'];item=items.get(ref)
        if (item is None or binding['source_h']!=item['source_h']
                or binding['source_h_ref']!=item['source_h_ref']
                or binding['catalog_binding']!=dict(ordinal=item['ordinal'],origin_ref=item['origin_ref'],
                    source_goal_id=item['source_origin_identity']['goal_id'])):
            raise ValueError('Accepted scientific call changed its frozen catalog member')
        before=accepted_search._owed(dict(item,accepted_calls=counts[ref]),value['initial_calls'])
        counts[ref]+=1
        owed+=int(accepted_search._owed(dict(item,accepted_calls=counts[ref]),value['initial_calls']))-int(before)
        if binding['grant'] is not None:used+=1
    focused=focus and focus['action']!='screen'
    decisions={};available=[]
    for ref,item in items.items():
        extra=None
        if head['search']['opportunity_unknown_count']:reason='original_execution_unknown'
        elif focused and ref!=focus['mechanism_ref']:reason='focused_mechanism_required'
        elif item['status']=='waived':reason='mechanism_waived'
        else:
            selected_owed=accepted_search._owed(dict(item,accepted_calls=counts[ref]),value['initial_calls'])
            awaiting=not focused and owed-int(selected_owed)>0
            if awaiting and counts[ref]>=value['initial_calls']:
                if grant is None or grant['mechanism_ref']!=ref or used>=grant['extra_calls']:
                    reason='initial_opportunities_owed'
                else:
                    reason=None;extra={key:grant[key] for key in ('source_a','source_a_ref')}
            else:reason=None
        decisions[ref]=dict(reason=reason,grant=extra)
        if reason is None:
            available.append(dict(number=str(item['ordinal']),mechanism_ref=ref))
    return decisions,sorted(available,key=lambda row:int(row['number']))


def admit(value, choice, scope, accepted):
    """Called under the common accept lock; only bounded original metadata is read."""
    from . import accepted_budget,accepted_search
    validate(value)
    if value is None:
        if choice is not None:raise OpportunityRequired('No H mechanism combination is authorized for this stage')
        return None
    root,focus=_catalog_root(value,scope)
    head=accepted_budget.read(root)
    # Validate the exact prefix once before using its already loaded table.
    accepted_search.catalog_item(root,value['catalog'],1,head=head)
    decisions,available=_availability(value,scope,accepted,head=head,focus=focus)
    try:
        selected=resolve_choice(value,choice,scope,head=head,_root=root,_focus=None,_validated=True)
    except OpportunityRequired as error:
        reason='original_execution_unknown' if head['search']['opportunity_unknown_count'] else 'mechanism_choice_required'
        raise OpportunityRequired(_REFUSALS[reason],reason=reason,available_mechanisms=available) from error
    ref=selected['mechanism_ref'];decision=decisions[ref]
    if decision['reason'] is not None:
        reason=decision['reason']
        raise OpportunityRequired(_REFUSALS[reason],reason=reason,available_mechanisms=available)
    return dict(mechanism_ref=ref,source_h=selected['source_h'],source_h_ref=selected['source_h_ref'],job_ref=scope['job_ref'],grant=decision['grant'],
        catalog_binding=dict(ordinal=selected['ordinal'],origin_ref=selected['origin_ref'],source_goal_id=selected['source_goal_id']))
