"""Frozen registered-call opportunities; these are never scientific verdicts."""
from __future__ import annotations


class OpportunityRequired(ValueError):
    code = 'scientific_opportunity_required'


def validate(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'version','initial_calls','mechanisms','baseline','grant','prior_unknown'}
            or value['version'] != 1 or type(value['initial_calls']) is not int or value['initial_calls'] < 1
            or type(value['prior_unknown']) is not bool):
        raise ValueError('Exact frozen scientific opportunity configuration required')
    rows = value['mechanisms']
    if not isinstance(rows,list) or not rows:
        raise ValueError('Scientific opportunity combination requires mechanisms')
    refs = set()
    for row in rows:
        if (not isinstance(row,dict) or set(row) != {'mechanism_ref','label','source_h','source_h_ref','minimal_test','status'}
                or row['mechanism_ref'] in refs or any(not isinstance(row[key],str) or not row[key] for key in ('mechanism_ref','label','minimal_test'))
                or type(row['source_h']) is not int or row['source_h'] < 1
                or not isinstance(row['source_h_ref'], dict) or set(row['source_h_ref']) != {'path'}
                or row['status'] not in {'pending','screened','deferred','waived'}):
            raise ValueError('Scientific opportunity mechanism binding differs')
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


def check_binding(value, binding, scope):
    validate(value)
    if value is None:
        if binding is not None:raise ValueError('Unexpected scientific call binding')
        return None
    if not isinstance(binding,dict) or set(binding) != {'mechanism_ref','source_h','source_h_ref','job_ref','grant'}:
        raise ValueError('Accepted scientific call binding missing')
    row = next((row for row in value['mechanisms'] if row['mechanism_ref'] == binding['mechanism_ref']),None)
    if (row is None or any(binding[key] != row[key] for key in ('source_h','source_h_ref'))
            or binding['job_ref'] != scope['job_ref']):
        raise ValueError('Accepted scientific call escaped frozen combination')
    grant = binding['grant']
    if grant is not None:
        allowed = value['grant']
        if allowed is None or grant != {key:allowed[key] for key in ('source_a','source_a_ref')} or allowed['mechanism_ref'] != binding['mechanism_ref']:
            raise ValueError('Accepted extra opportunity escaped exact A authorization')
    return binding


def admit(value, choice, scope, accepted):
    """Called under the common accept lock; only bounded original metadata is read."""
    validate(value)
    if value is None:
        if choice is not None:raise OpportunityRequired('No H mechanism combination is authorized for this stage')
        return None
    if value['prior_unknown']:
        raise OpportunityRequired('Preserve the original unknown execution; resolve its identity before a new submission')
    matches = [row for row in value['mechanisms'] if choice in (row['mechanism_ref'],row['label'])]
    if len(matches) != 1:
        raise OpportunityRequired('Choose one exact authorized H mechanism or its unique scientific label')
    selected = matches[0];ref = selected['mechanism_ref']
    if selected['status'] == 'waived':raise OpportunityRequired('This mechanism was scientifically waived by A')
    counts = {row['mechanism_ref']:row['accepted_count'] for row in value['baseline']}
    grant = value['grant'];used = grant['used_calls'] if grant else 0
    for original in accepted:
        binding = check_binding(value,original.get('mechanism_binding'),scope)
        counts[binding['mechanism_ref']] += 1
        if binding['grant'] is not None:used += 1
    awaiting = any(row['status'] not in {'deferred','waived'} and counts[row['mechanism_ref']] < value['initial_calls'] for row in value['mechanisms'] if row['mechanism_ref'] != ref)
    extra = None
    if awaiting and counts[ref] >= value['initial_calls']:
        if grant is None or grant['mechanism_ref'] != ref or used >= grant['extra_calls']:
            raise OpportunityRequired('Other H mechanisms retain initial call opportunities; report the scientific question to A for justified extra allocation')
        extra = {key:grant[key] for key in ('source_a','source_a_ref')}
    return dict(mechanism_ref=ref,source_h=selected['source_h'],source_h_ref=selected['source_h_ref'],job_ref=scope['job_ref'],grant=extra)
