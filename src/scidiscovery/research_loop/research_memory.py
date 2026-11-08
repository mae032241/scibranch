"""Pure projections of W-selected scientific memory and experiment purposes.

The existing publisher supplies accepted identities and writes the navigation.
This module never loads evidence, chooses candidates, or grants permissions.
"""
from __future__ import annotations

import copy
import json


def experiment_question(value):
    """A discovery, diagnostic or comparison question needs no hypothesis pair."""
    if (not isinstance(value, dict) or set(value) != {'question', 'change_criterion'}
            or any(not isinstance(value[key], str) or not value[key].strip() for key in value)):
        raise ValueError('experiment_question requires nonblank question and change_criterion')
    return copy.deepcopy(value)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _identity(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def rows(value, field):
    """Select only an explicitly authored collection; malformed input is inert."""
    selected=value.get(field) if isinstance(value,dict) else None
    return selected if isinstance(selected,list) else []


def _evidence(value, references, *, required=False):
    if not isinstance(value,list) or required and not value:
        return [],'invalid_evidence_memory_shape'
    result=[]
    for item in value:
        if (not isinstance(item,dict) or set(item)!={'ref','relation','note'}
                or not isinstance(item['ref'],str)
                or item['relation'] not in ('supports','opposes','unknown') or not _text(item['note'])):
            return [],'invalid_evidence_memory_shape'
        if item['ref'] not in references:
            return [],'evidence_original_unavailable'
        result.append(dict(copy.deepcopy(item),ref=copy.deepcopy(references[item['ref']])))
    return result,None


def project(value, *, candidates, references, source):
    """Validate selected entries against already-produced exact public facts.

    ``candidates`` maps authored exact choices to canonical public choices;
    ``references`` maps readable exact paths to their immutable public locators.
    Scientific notes and limits remain authored claims, including negatives.
    Invalid optional entries stay in the original and get a navigation diagnostic.
    """
    if (not isinstance(value,dict) or set(value)-{'question','candidates','clues'}
            or 'question' not in value or not isinstance(value['question'],str)
            or not {'candidates','clues'} & set(value)
            or any(not isinstance(value[key],list) for key in ('candidates','clues') if key in value)):
        return dict(source=copy.deepcopy(source), candidates=[], diagnostics=[dict(pointer='/research_memory', reason='invalid_memory_shape')])
    result=dict(source=copy.deepcopy(source), question=value['question'], candidates=[], diagnostics=[])
    seen=set()
    for index, row in enumerate(rows(value,'candidates')):
        pointer=f'/research_memory/candidates/{index}'
        reason=None
        keys={'candidate','data','evaluation','evidence','failure_condition','open_question','limitations'}
        if (not isinstance(row, dict) or set(row) != keys
                or any(not _text(row.get(key)) for key in ('data','evaluation','failure_condition'))
                or not isinstance(row.get('open_question'), str)
                or not isinstance(row.get('limitations'), list)
                or any(not isinstance(text, str) for text in row['limitations'])
                or not isinstance(row.get('evidence'), list)):
            reason='invalid_candidate_memory_shape'
        if reason is None:
            try:choice=candidates.get(_identity(row['candidate']))
            except (TypeError,ValueError,UnicodeError):choice=None
            if choice is None:reason='candidate_original_unavailable'
        if reason is None:
            evidence,reason=_evidence(row['evidence'],references)
        if reason is not None:
            result['diagnostics'].append(dict(pointer=pointer,reason=reason));continue
        identity=_identity(choice)
        if identity in seen:
            result['diagnostics'].append(dict(pointer=pointer,reason='duplicate_exact_candidate'));continue
        seen.add(identity)
        result['candidates'].append(dict(copy.deepcopy(row),candidate=copy.deepcopy(choice),evidence=evidence,
            source=dict(copy.deepcopy(source),pointer=pointer)))
    if 'clues' in value:
        result['clues']=[]
        statements={'observed_fact','applicability','limited_negative','open_question'}
        for index,row in enumerate(value['clues']):
            pointer=f'/research_memory/clues/{index}'
            reason=None
            if (not isinstance(row,dict) or set(row)-statements-{'evidence'}
                    or not any(_text(row.get(key)) for key in statements-{'applicability'})
                    or any(not _text(row[key]) for key in statements if key in row)
                    or 'limited_negative' in row and not _text(row.get('applicability'))):
                reason='invalid_clue_memory_shape'
            if reason is None:
                evidence,reason=_evidence(row.get('evidence'),references,required=True)
            if reason is not None:
                result['diagnostics'].append(dict(pointer=pointer,reason=reason));continue
            result['clues'].append(dict(copy.deepcopy(row),evidence=evidence,
                source=dict(copy.deepcopy(source),pointer=pointer)))
    return result


def retain(prior, job, payload, *, source, facts):
    """Carry W's selected memory through consultations and new contexts."""
    previous=copy.deepcopy(prior.get('candidate_memory'))
    if job.get('research_strategy_version') != 3 or job['role'] != 'W' or payload.get('research_memory') is None:
        return previous
    if facts is None:
        result=previous or dict(source=copy.deepcopy(source),candidates=[])
        result['update_diagnostics']=[dict(pointer='/research_memory',reason='accepted_memory_facts_unavailable')]
        return result
    result=project(payload['research_memory'],candidates=facts['candidates'],references=facts['references'],source=source)
    # Preserve legacy partial candidate updates. Once clues participate, an
    # invalid optional update must not silently discard usable clue context.
    clues_selected='clues' in payload['research_memory'] if isinstance(payload['research_memory'],dict) else False
    if result['diagnostics'] and (not (result['candidates'] or result.get('clues'))
            or clues_selected or previous is not None and previous.get('clues')):
        if previous is not None:
            previous['update_diagnostics']=result['diagnostics']
            previous['update_source']=copy.deepcopy(source)
            return previous
    result['scope']='W-selected scientific claims and exact originals; applicability and limited negatives remain authored claims, never global rejection, ranking, execution permission or qualification.'
    return result
