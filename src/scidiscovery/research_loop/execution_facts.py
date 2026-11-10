"""Bounded execution navigation from explicit accepted values, without IO.

This presentation contract grants neither scientific validity nor execution
permission. Original call records remain authoritative. No argument is a loader.
"""
from __future__ import annotations

import json
import re

from scidiscovery.operations.execution import PENDING_EXECUTION_STATES

VERSION = 1
MAX_BYTES = 4096
_STATUSES = frozenset({'known_success', 'known_failed', 'not_submitted', 'unknown',
                       'cancelled'}) | PENDING_EXECUTION_STATES
_UNRESOLVED = PENDING_EXECUTION_STATES | {'unknown'}
_NATIVE_STATES = PENDING_EXECUTION_STATES | {'cancelled', 'succeeded', 'failed', 'unknown'}
_BASES = frozenset({'accepted_terminal', 'accepted_call', 'current_observation'})
_ID = re.compile(r'[A-Za-z0-9._-]{1,200}\Z')


def serialized_bytes(value):
    """Match the pretty JSON UTF-8 envelope used by the existing publisher."""
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def head_tail_indices(size):
    return [index for left in range((size+1)//2)
            for index in dict.fromkeys((left,size-1-left))]


def fit_rows(candidates, *, priority, build, max_bytes):
    """Greedily fit caller-prioritized original positions against a whole envelope."""
    selected=set();value=build([])
    if len(serialized_bytes(value))>max_bytes:
        raise ValueError('Presentation envelope exceeds its bound without rows')
    for index in priority:
        proposed=selected|{index}
        candidate=build([row for position,row in enumerate(candidates) if position in proposed])
        if len(serialized_bytes(candidate))<=max_bytes:selected=proposed;value=candidate
    return value


def priority_indices(rows):
    unresolved=[index for index,row in enumerate(rows) if row['status'] in _UNRESOLVED]
    resolved=[index for index,row in enumerate(rows) if row['status'] not in _UNRESOLVED]
    return unresolved+[resolved[index] for index in head_tail_indices(len(resolved))]


def with_rows(value, rows):
    omitted=value.get('omitted_rows')
    result=dict(value,calls=rows,omitted_rows=None if omitted is None else omitted+len(value['calls'])-len(rows))
    shown=sum(row['status'] in _UNRESOLVED for row in rows)
    total=value.get('unresolved_calls')
    result.update(unresolved_shown=shown,unresolved_omitted=None if total is None else total-shown,
        unresolved_id_access=('complete' if total==shown and value.get('counts_are')=='exact_within_accepted_scope'
            else 'limited' if shown else 'unavailable'))
    return result


def compact_rows(rows):
    """Keep exact unresolved identity, status, origin and reconciliation only."""
    return [dict({key:item for key,item in row.items() if key in {'call_id','status','node',
        'observation_basis','scientific_result','native_state'}},
        navigation={key:item for key,item in row.get('navigation',{}).items() if key=='reconcile'})
        if row['status'] in _UNRESOLVED else row for row in rows]


def fit_view(value, *, max_bytes=MAX_BYTES):
    rows=value['calls']
    if len(serialized_bytes(with_rows(value,rows)))>max_bytes:rows=compact_rows(rows)
    base=dict(value,calls=rows)
    while len(serialized_bytes(with_rows(base,[])))>max_bytes and base.get('navigation'):
        base=dict(base,navigation=base['navigation'][:-1])
    return fit_rows(rows,priority=priority_indices(rows),
        build=lambda selected:with_rows(base,selected),max_bytes=max_bytes)


def _locator(value):
    # These are existing science namespace aliases, never host path rewrites.
    if (not isinstance(value, str) or not value or len(value.encode()) > 512
            or '\\' in value or '%' in value or '?' in value or '#' in value
            or any(ord(char) < 32 for char in value)):
        return None
    parts = value.split('/')
    if any(part in {'', '.', '..'} for part in parts):
        return None
    if value != 'science-task.json' and not value.startswith(('inputs/', 'history/', 'work/')):
        return None
    if any(part in {'native-evidence', 'host-runtime', 'sessions'} for part in parts):
        return None
    return value



def _public_scope(value):
    if (not isinstance(value, dict) or set(value) - {'kind', 'node', 'attempt'}
            or value.get('kind') not in {'single_call', 'stopped_worker', 'accepted_sources'}
            or not isinstance(value.get('node'), str) or len(value['node'])>80
            or re.fullmatch(r'[HWAE][0-9]{3,}|current', value['node']) is None
            or ('attempt' in value and (type(value['attempt']) is not int or value['attempt'] < 0))):
        raise ValueError('Execution facts require an explicit public scope')
    return dict(value)



def _selected_entry(value):
    """Retain an already relative selected entry without rewriting its origin."""
    if not isinstance(value,str) or not value:return None
    try:
        if len(value.encode('utf-8'))>512:return None
    except UnicodeError:
        return None
    if (any(char in value for char in ('\\',':','?','#','%'))
            or any(ord(char)<32 or ord(char)==127 for char in value)):
        return None
    parts=value.split('/')
    if any(part in {'','.','..'} for part in parts):return None
    if any(part in {'private','private-bindings','private_bindings','privatebindings','bindings','sessions',
                    'native-evidence','host-runtime','host-history','.git'} for part in parts):
        return None
    return value


def _code(value):
    if not isinstance(value, dict):
        return None
    commit = value.get('commit')
    if not isinstance(commit, str) or re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', commit) is None:
        return None
    # Preserve a captured logical entry only when it already is a safe relative
    # path. Never strip a private producer prefix into a fabricated public path.
    result=dict(commit=commit, scope='accepted selected code only')
    entry=_selected_entry(value.get('entry'))
    if entry is not None:result['entry']=entry
    return result


def accepted_fact(*, call_id, status, accepted, observed, outputs,
                  observation_basis, native_state=None):
    """Reduce owner-loaded accepted metadata, without opening its references."""
    if not isinstance(call_id, str) or _ID.fullmatch(call_id) is None:
        raise ValueError('Execution fact requires an exact public call identity')
    if status not in _STATUSES or observation_basis not in _BASES:
        raise ValueError('Execution fact requires an explicit status and observation basis')
    if not isinstance(accepted, dict) or accepted.get('call_id') != call_id:
        raise ValueError('Execution fact differs from its accepted call identity')
    if observed is not None and not isinstance(observed, dict):
        raise TypeError('Execution observation must be an accepted value or explicit None')
    if outputs is not None and not isinstance(outputs, (list, tuple)):
        raise TypeError('Scientific output availability requires explicit accepted outputs')
    source = (observed or {}).get('source_code_version', accepted.get('code_version'))
    attempt = (accepted.get('native_attempt') or {}).get('number')
    public=(observed or {}).get('public_result')
    reported=(public.get('result') or {}) if isinstance(public,dict) else {}
    reported_status=reported.get('status') if isinstance(reported,dict) else None
    missing=reported_status if isinstance(reported_status,str) and reported_status in {'missing','invalid','unavailable'} else None
    available = bool(outputs) or public is not None
    availability=(missing if missing is not None else 'available' if available else
        'unavailable' if observation_basis == 'accepted_terminal' else 'not_accepted')
    result = dict(call_id=call_id, status=status, observation_basis=observation_basis,
                  scientific_result=availability,
                  selected_code=_code(source))
    if type(attempt) is int and attempt >= 0:
        result['native_attempt'] = attempt
    if isinstance(native_state,str) and native_state in _NATIVE_STATES:
        result['native_state'] = native_state
    return result



def output_refs(*, observed_outputs, published_slots):
    """Match accepted outputs to explicit producer-owned readable public slots.

    A logical reuse handle or a matching filename cannot establish a mounted
    file. The owner supplies exact source/path pairs, already accepted once.
    """
    if not isinstance(observed_outputs,(list,tuple)) or not isinstance(published_slots,(list,tuple)):
        return []
    result=[]
    for slot in published_slots:
        if not isinstance(slot,dict):continue
        alias=_locator(slot.get('path'))
        if alias is None:continue
        parts=alias.split('/')
        if (len(parts)<5 or parts[:2]!=['work','retained-experiments']
                or _ID.fullmatch(parts[2]) is None or parts[3]!='outputs'
                or '/'.join(parts[4:])!=slot.get('name')):continue
        matches=[item for item in observed_outputs if isinstance(item,dict)
            and item.get('name')==slot.get('name') and item.get('path')==slot.get('source')]
        if len(matches)==1 and alias not in result:result.append(alias)
    return result


def render(*, facts, scope, public_refs, coverage, omitted_calls=0, max_bytes=MAX_BYTES):
    """Render only supplied lightweight facts and existing public aliases.

    Counts describe the supplied scope, never all workspace work. None omitted
    calls means an unknown additional population; no zero is invented for it.
    """
    if not isinstance(facts, (list, tuple)) or not isinstance(public_refs, dict):
        raise TypeError('Execution facts and explicit public references are required')
    scope=_public_scope(scope)
    if coverage not in {'accepted_scope', 'partial', 'unknown'}:
        raise ValueError('Execution fact coverage must be explicit')
    if omitted_calls is not None and (type(omitted_calls) is not int or omitted_calls < 0):
        raise ValueError('Execution fact omissions require a count or explicit unknown')
    if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_BYTES:
        raise ValueError('Execution fact byte bound must be between 1024 and 4096')
    counts = {key: 0 for key in sorted(_STATUSES)}
    rows, seen = [], set()
    for fact in facts:
        call_id = fact['call_id']
        status = fact['status']
        if (not isinstance(call_id, str) or _ID.fullmatch(call_id) is None
                or call_id in seen or status not in _STATUSES
                or fact['observation_basis'] not in _BASES
                or fact['scientific_result'] not in {'available', 'missing', 'invalid', 'unavailable', 'not_accepted'}):
            raise ValueError('Execution fact identity or status is invalid')
        seen.add(call_id)
        counts[status] += 1
        row = {key: fact[key] for key in ('call_id', 'status', 'observation_basis', 'scientific_result')}
        if type(fact.get('native_attempt')) is int and fact['native_attempt'] >= 0:
            row['native_attempt'] = fact['native_attempt']
        if isinstance(fact.get('native_state'),str) and fact['native_state'] in _NATIVE_STATES:
            row['native_state'] = fact['native_state']
        row['selected_code'] = _code(fact.get('selected_code'))
        refs = public_refs.get(call_id, {})
        navigation = {}
        for kind in ('result', 'code', 'reconcile'):
            aliases = refs.get(kind, []) if isinstance(refs, dict) else []
            if isinstance(aliases, (list, tuple)):
                values = list(dict.fromkeys(alias for value in aliases if (alias := _locator(value)) is not None))
                if values:
                    navigation[kind] = values
        row['navigation'] = navigation
        row['navigation_availability'] = 'public_locator' if navigation else 'unavailable'
        row['result_access'] = 'directly_readable' if navigation.get('result') else 'not_directly_readable'
        rows.append(row)
    value = dict(version=VERSION, scope=dict(scope), coverage=coverage,
                 counts_are='exact_within_accepted_scope' if coverage=='accepted_scope' and omitted_calls==0 else 'lower_bound',
                 observed_calls=len(rows), status_counts=counts,
                 unresolved_calls=sum(counts[key] for key in _UNRESOLVED),
                 additional_calls=omitted_calls, omitted_rows=0, calls=rows,
                 interpretation='Execution status is not scientific validity. Unknown requires same-call reconciliation; it grants no resubmission or execution permission.',
                 result_interpretation='Result availability and public navigation are separate; neither establishes scientific qualification. Unmounted originals are unavailable to direct reading.')
    return fit_view(value,max_bytes=max_bytes)


def unavailable(*, scope):
    """Optional presentation failure retains a public task navigation only."""
    return dict(version=VERSION, scope=_public_scope(scope), coverage='unknown',
                observed_calls=None, status_counts=None, unresolved_calls=None,
                additional_calls=None, omitted_rows=None, calls=[],
                diagnostic='execution_fact_view_unavailable',
                original_access='science-task.json',
                interpretation='Original permissions and same-call reconciliation remain binding; scientific validity is not assessed.')


def combine(previous, current, *, scope, source_cutoff, node_count, navigation,
            max_bytes=MAX_BYTES):
    """Append one accepted node's projection to its already accepted prefix.

    The publisher owns exact-once append and immutable prefix identity. Missing
    historical presentation leaves known lower bounds, never an empty history.
    """
    if (type(source_cutoff) is not int or source_cutoff < 0
            or type(node_count) is not int or node_count < 0
            or not isinstance(navigation, (list, tuple))):
        raise ValueError('Execution fact combination requires an explicit accepted cutoff')
    # Validate the public scope and bound without reading or reconstructing it.
    render(facts=[], scope=scope, public_refs={}, coverage='unknown',
           omitted_calls=None, max_bytes=max_bytes)
    documents=[item for item in (previous,current) if isinstance(item,dict) and item.get('version')==VERSION]
    missing=(node_count>1 and (not isinstance(previous,dict) or previous.get('version')!=VERSION)) or not isinstance(current,dict) or current.get('version')!=VERSION
    counts={key:0 for key in sorted(_STATUSES)}
    observed=0;omitted=0;rows=[];additional=0
    for document in documents:
        distribution=document.get('status_counts')
        if not isinstance(distribution,dict):
            missing=True
        else:
            for key in counts:
                number=distribution.get(key)
                if type(number) is not int or number<0:
                    missing=True
                else:counts[key]+=number
        number=document.get('observed_calls')
        if type(number) is int and number>=0:observed+=number
        else:missing=True
        number=document.get('omitted_rows')
        if type(number) is int and number>=0:omitted+=number
        else:missing=True
        number=document.get('additional_calls')
        if type(number) is int and number>=0:
            if additional is not None:additional+=number
        else:additional=None;missing=True
        if document.get('coverage')!='accepted_scope':missing=True
        for row in document.get('calls',[]):
            # Reuse the pure row allowlist; arbitrary stored presentation fields
            # cannot become private context in a subsequent task.
            # A prior W's work tree and logical reuse handles are not mounted
            # result files in another context. Only this node's accepted public
            # index below survives as navigation across this boundary.
            projected=render(facts=[row],scope=scope,
                public_refs={},coverage='accepted_scope',
                max_bytes=MAX_BYTES)['calls']
            if not projected:
                omitted+=1
                continue
            value=projected[0]
            node=row.get('node',(document.get('scope') or {}).get('node'))
            if isinstance(node,str) and re.fullmatch(r'[HWAE][0-9]{3,}|current',node):value['node']=node
            rows.append(value)
    value=dict(version=VERSION,scope=dict(scope),source_cutoff=source_cutoff,node_count=node_count,
        coverage='unknown' if missing else 'accepted_scope',
        counts_are='lower_bound' if missing else 'exact_within_accepted_scope',
        observed_calls=observed,status_counts=counts,
        unresolved_calls=sum(counts[key] for key in _UNRESOLVED),
        additional_calls=None if missing else additional,omitted_rows=omitted,calls=rows,
        navigation=list(dict.fromkeys(alias for item in navigation if (alias:=_locator(item)) is not None)),
        interpretation='Accepted managed calls only. Execution status is not scientific validity; preserve original permissions and same-call reconciliation.')
    return fit_view(value,max_bytes=max_bytes)
