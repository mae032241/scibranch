"""Pure projections of verified public selection facts; no execution authority."""
import json

MAX_BRIEF_BYTES = 4096
RECORD_FIELDS = {'step', 'role', 'recommendation', 'coverage', 'candidates', 'current_candidate', 'actual_submission'}
CANDIDATE_FIELDS = {'id', 'label', 'evaluation_key', 'metric', 'direction', 'status'}


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def _public_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or key.lower().startswith('hidden') or key.lower() in {'reward', 'test_score'}:
                raise ValueError('non_public_field')
            if key not in {'label', 'evaluation_key', 'metric', 'direction'}:
                _public_keys(item)
    elif isinstance(value, list):
        for item in value:
            _public_keys(item)


def _object(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Public fields differ from selection contract')


def _record(value):
    deferred = isinstance(value, dict) and 'audit_request' in value
    _object(value, RECORD_FIELDS | ({'audit_request'} if deferred else set()))
    if deferred and (value['audit_request'] is not True or value['role'] != 'A' or value['recommendation'] is not None):
        raise ValueError('An audit request defers candidate recommendation')


def _id(value):
    if not isinstance(value, str) or not value or len(value.encode()) > 160:
        raise ValueError('Invalid public candidate identity')


def public_inputs(records):
    """Discard private receipt/contexts; caller already verified sealed sources."""
    result = []
    for record in records:
        observation = record['observation']
        candidates = []
        if observation is not None:
            for item in observation['candidates']:
                # Explicit hidden columns must not silently disappear during projection.
                _public_keys({key: None for key in item})
                candidates.append({key: item.get(key) for key in CANDIDATE_FIELDS})
        actual = observation.get('actual_submission') if observation else None
        current = observation.get('current_candidate') if observation else None
        result.append(dict(step=record['step'], role=record['role'], recommendation=record['recommendation'],
            coverage=observation['coverage']['status'] if observation else None,
            candidates=candidates, current_candidate=dict(candidate_id=current['candidate_id']) if current else None,
            actual_submission=dict(candidate_id=actual['candidate_id'],
                artifact_retention=actual.get('artifact', {}).get('retention', 'unknown')) if actual else None,
            **(dict(audit_request=True) if record.get('audit_request') is True else {})))
    return result


def recommendations(records):
    """One latest authored judgment; a null choice clears the current slot."""
    authors = sorted((row for row in records if row['role'] == 'A' and not row.get('audit_request')),
                     key=lambda row: row['step'])
    latest = authors[-1] if authors else None
    old = next((row for row in reversed(authors[:-1]) if row['recommendation'] is not None), None)
    return dict(latest_a_step=latest['step'] if latest else None,
        a_recommended=dict(candidate_id=latest['recommendation'], author_step=latest['step']) if latest and latest['recommendation'] is not None else None,
        historical_a_recommendation=dict(candidate_id=old['recommendation'], author_step=old['step']) if old else None)


def project(records, *, audits=(), delivery=None):
    """Report observed facts in source order and authored judgments."""
    if not isinstance(records, list) or not isinstance(audits, (list, tuple)):
        raise ValueError('Public records and audits must be bounded sequences')
    for record in records:
        _record(record)
        if type(record['step']) is not int or record['step'] <= 0:
            raise ValueError('Invalid sealed record identity')
    _public_keys(records)
    _public_keys(list(audits))
    _public_keys(delivery)
    candidates, steps, observations, authors = {}, set(), [], []
    for record in sorted(records, key=lambda row: row['step']):
        _record(record)
        step = record['step']
        if type(step) is not int or step <= 0 or step in steps or not isinstance(record['role'], str) or record['role'] not in {'H', 'W', 'A', 'E'}:
            raise ValueError('Invalid sealed record identity')
        steps.add(step)
        if record['coverage'] is not None and (not isinstance(record['coverage'], str) or record['coverage'] not in {'complete', 'partial', 'unavailable'}):
            raise ValueError('Invalid public coverage')
        if not isinstance(record['candidates'], list):
            raise ValueError('Candidates must be a bounded list')
        for item in record['candidates']:
            if (not isinstance(item,dict) or not {'id','status'} <= set(item)
                    or not set(item) <= CANDIDATE_FIELDS):
                raise ValueError('Public fields differ from selection contract')
            _id(item['id'])
            if not isinstance(item['status'],str) or item['status'] not in {'ok','failed','unknown'}:
                raise ValueError('Invalid public execution status')
            item={key:item.get(key) for key in CANDIDATE_FIELDS}
            if item['id'] in candidates and candidates[item['id']] != item:
                raise ValueError('Observed candidate identity changed')
            candidates.setdefault(item['id'], dict(item))
        for field in ('current_candidate', 'actual_submission'):
            value = record[field]
            if value is not None:
                _object(value, {'candidate_id'} | ({'artifact_retention'} if field == 'actual_submission' else set()))
                if value['candidate_id'] is not None:
                    _id(value['candidate_id'])
                if value['candidate_id'] is not None and value['candidate_id'] not in candidates:
                    raise ValueError('Public observation references unknown candidate')
                if field == 'actual_submission' and (not isinstance(value['artifact_retention'], str) or value['artifact_retention'] not in {'archived', 'digest_only', 'unavailable', 'unknown'}):
                    raise ValueError('Invalid public artifact retention')
        if record['coverage'] is not None:
            observations.append(record)
        elif record['candidates'] or record['current_candidate'] is not None or record['actual_submission'] is not None:
            raise ValueError('Missing observer coverage for public observation')
        if record['role'] == 'A':
            if record['recommendation'] is not None:
                _id(record['recommendation'])
            if record['recommendation'] is not None and record['recommendation'] not in candidates:
                raise ValueError('A recommended unknown candidate')
            if not record.get('audit_request'):
                authors.append(record)
        elif record['recommendation'] is not None:
            raise ValueError('Only sealed A supplies recommendation')
    audit_by_id = {}
    for fact in audits:
        _object(fact, {'candidate_id', 'status', 'artifact_bound', 'leakage_flag'})
        cid = fact['candidate_id']
        _id(cid)
        if cid not in candidates or cid in audit_by_id or not isinstance(fact['status'], str) or fact['status'] not in {'completed', 'pending', 'skipped', 'failed', 'unknown'}:
            raise ValueError('Invalid verified public audit reference')
        if any(value is not None and type(value) is not bool for value in (fact['artifact_bound'], fact['leakage_flag'])):
            raise ValueError('Invalid public audit fact')
        audit_by_id[cid] = ('public_audit_passed' if fact['status'] == 'completed' and fact['artifact_bound'] is True and fact['leakage_flag'] is False
            else 'public_audit_failed' if fact['status'] == 'failed' or fact['leakage_flag'] is True else 'unknown')
    if delivery is None:
        delivery = dict(state='unknown', last_confirmed=None)
    _object(delivery, {'state', 'last_confirmed'})
    if not isinstance(delivery['state'], str) or delivery['state'] not in {'none', 'confirmed', 'failed', 'unknown'}:
        raise ValueError('Invalid delivery state')
    confirmed = delivery['last_confirmed']
    if confirmed is not None:
        _object(confirmed, {'candidate_id'})
        _id(confirmed['candidate_id'])
        if confirmed['candidate_id'] not in candidates:
            raise ValueError('Unknown confirmed delivery candidate')
    if delivery['state'] == 'confirmed' and confirmed is None:
        raise ValueError('Confirmed delivery lacks verified candidate')
    actual = next((row for row in reversed(observations) if row['actual_submission'] is not None), None)
    alerts=[dict(candidate_id=item['id'],status=item['status']) for item in candidates.values() if item['status'] in {'failed','unknown'}]
    audit_ids=list(audit_by_id)
    facts=list(candidates.values())
    value = dict(scope='observed_only',
        coverage='partial' if any(row['coverage'] != 'complete' for row in observations) else 'complete' if observations else 'unknown',
        candidate_count=len(candidates), current_candidate=observations[-1]['current_candidate'] if observations else None,
        **recommendations(authors),
        observed_actual_submission=dict(actual['actual_submission'], observed_at=actual['step']) if actual else None,
        delivery=dict(state=delivery['state'], current_confirmed=confirmed if delivery['state']=='confirmed' else None, last_confirmed=confirmed),
        candidate_facts=facts[:16], candidate_facts_omitted_count=max(0,len(facts)-16),
        public_audits=[dict(candidate_id=cid,status=audit_by_id[cid]) for cid in audit_ids[:16]],
        audits_omitted_count=max(0,len(audit_ids)-16),
        candidate_alerts=alerts[:4],candidate_alerts_omitted_count=max(0,len(alerts)-4),
        failed_candidate_count=sum(item['status']=='failed' for item in candidates.values()),
        unknown_candidate_count=sum(item['status']=='unknown' for item in candidates.values()),
        limitation='Candidate facts retain source order without scientific ranking. Listed audit facts do not authorize delivery; absent audit remains unknown. Read existing T2 candidate indexes for every original; A owns applicability and selection judgments.')
    for field, omitted in (('candidate_facts','candidate_facts_omitted_count'), ('public_audits','audits_omitted_count')):
        while len(_bytes(value)) > MAX_BRIEF_BYTES and value[field]:
            value[field].pop()
            value[omitted] += 1
    while len(_bytes(value))>MAX_BRIEF_BYTES and value['candidate_alerts']:
        value['candidate_alerts'].pop()
        value['candidate_alerts_omitted_count']+=1
    if len(_bytes(value)) > MAX_BRIEF_BYTES:
        return dict(scope='observed_only', coverage=value['coverage'], candidate_count=len(candidates),
            view_status='details_omitted', current_candidate=None,
            latest_a_step=None, a_recommended=None, historical_a_recommendation=None,
            observed_actual_submission=None, delivery=dict(state='unknown', current_confirmed=None, last_confirmed=None),
            candidate_facts=[], candidate_facts_omitted_count=len(facts),
            public_audits=[], audits_omitted_count=len(audit_ids),
            candidate_alerts=[], candidate_alerts_omitted_count=len(alerts),
            failed_candidate_count=value['failed_candidate_count'], unknown_candidate_count=value['unknown_candidate_count'],
            limitation='Selection details omitted, not absent. Read existing history indexes for complete original facts.')
    return value


SHORTLIST_RULE = ('All observed candidates in first-observation order. Metrics and evaluation versions are facts; '
    'control supplies no scientific ranking or default. A must explicitly choose an exact candidate and explain its evidence.')


def shortlist(records):
    """Retain every observed candidate, independent of scientific metrics."""
    project(records)  # Identical public validation: no hidden columns, stable identities.
    items, first = {}, {}
    for record in sorted(records, key=lambda row: row['step']):
        for item in record['candidates']:
            if item['id'] not in items:
                items[item['id']], first[item['id']] = {key:item.get(key) for key in CANDIDATE_FIELDS}, record['step']
    reviews = [row for row in records if row['role'] == 'A']
    authors = [row for row in reviews if not row.get('audit_request')]
    assessed = max((row['step'] for row in reviews), default=None)
    recommended = max(authors, key=lambda row: row['step'])['recommendation'] if authors else None
    members = [dict(candidate_id=cid, metric=items[cid]['metric'], evaluation_key=items[cid]['evaluation_key'],
                    direction=items[cid]['direction'], status=items[cid]['status'], first_step=first[cid]) for cid in items]
    return dict(version=2, candidate_count=len(items), latest_a_step=assessed, a_recommended=recommended,
                unassessed_count=sum(assessed is None or step >= assessed for step in first.values()),
                members=members, rule=SHORTLIST_RULE)


def accepted_project(summary, lookup, *, candidate_ids=(), delivery=None):
    """Project explicit candidate addresses from the canonical accepted head.

    The population count is complete; inline facts are the requested selection,
    with public navigation supplying every original independently of this view.
    No iteration over the retained candidate index is implied by this API.
    """
    from . import accepted_search
    facts = []
    seen = set()
    for identity in candidate_ids:
        _id(identity)
        if identity in seen:continue
        seen.add(identity)
        row = lookup(accepted_search.key('candidate', identity))
        if row is None:raise ValueError('Requested candidate is outside accepted facts')
        fact = row['observation']
        _public_keys(fact)
        facts.append(dict(fact))
    observed = summary['observation_count']
    coverage = 'partial' if summary['partial_observation_count'] else 'complete' if observed else 'unknown'
    actual = summary['actual_submission']
    if actual is not None:
        actual = dict(candidate_id=actual['candidate_id'],
            artifact_retention=actual.get('artifact', {}).get('retention', actual.get('artifact_retention', 'unknown')),
            observed_at=actual['observed_at'])
    return dict(scope='observed_only', coverage=coverage, candidate_count=summary['candidate_count'],
        current_candidate=summary['current_candidate'], latest_a_step=summary['latest_a_step'],
        a_recommended=summary['a_recommended'], historical_a_recommendation=summary['historical_a_recommendation'],
        observed_actual_submission=actual,
        delivery=delivery or dict(state='unknown', current_confirmed=None, last_confirmed=None),
        candidate_facts=facts, candidate_facts_omitted_count=summary['candidate_count']-len(facts),
        failed_candidate_count=summary['failed_candidate_count'], unknown_candidate_count=summary['unknown_candidate_count'],
        public_audits=[], audits_omitted_count=None, public_audits_status='not_projected',
        candidate_alerts=[dict(candidate_id=item['id'], status=item['status']) for item in facts if item['status'] in {'failed','unknown'}],
        candidate_alerts_omitted_count=summary['failed_candidate_count']+summary['unknown_candidate_count']-sum(item['status'] in {'failed','unknown'} for item in facts),
        limitation='Inline candidates are explicitly addressed original facts. The public candidate index retains every candidate without scientific ranking; A owns suitability and selection.')
