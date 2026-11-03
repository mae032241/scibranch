"""Durable control issues, separate from the latest tool/form failure.

Control issues retain their transition history; notification snapshots live in
a separate projection table. Observing a notification does not resolve an issue.
"""
import json

from ..schema.common import canonical_json

NOTICE_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_attention_notices (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    version INTEGER NOT NULL,
    diagnostic_json BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS run_attention_notices_open ON run_attention_notices(run_id)
    WHERE json_extract(CAST(diagnostic_json AS TEXT),'$.state')='attention_required';
"""


def migrate_notices(connection):
    """Copy legacy projections without rewriting the immutable activity history/cursors."""
    connection.execute("""INSERT OR IGNORE INTO run_attention_notices(run_id,version,diagnostic_json)
        SELECT run_id,rowid,diagnostic_json FROM run_activity WHERE activity='attention_notice'""")


SUBMISSION_CODES = frozenset({"submission_intervention_required", "submission_timeout_unclassified",
    "submission_state_unavailable"})
BINDING_CODES = frozenset({"experiment_implementation_ambiguous", "experiment_implementation_unavailable"})
ACTION = "Inspect this task with intent=status, diagnostic_after=0; preserve retained results and choose recovery. Do not repeat execution."


def record(runs, connection, run_id, *, scope, code, now, resolution="resolved"):
    """An observation repeats a stable issue; code changes/recurrences notify again."""
    row = connection.execute("SELECT rowid, diagnostic_json FROM run_activity WHERE run_id=? "
        "AND activity='attention_state' AND json_extract(CAST(diagnostic_json AS TEXT),'$.scope')=? "
        "ORDER BY rowid DESC LIMIT 1", (run_id, scope)).fetchone()
    prior = json.loads(row['diagnostic_json']) if row else None
    if code is None and (prior is None or prior['state'] != 'open'):
        return
    same = prior and prior['state'] == 'open' and prior['code'] == code
    data = {'scope':scope, 'code':code or prior['code'], 'state':'open' if code else resolution,
        'first_seen_at':prior['first_seen_at'] if same or code is None else now,
        'last_seen_at':now, 'generation':prior['generation'] if same or code is None else None}
    # Repeated observations update only the projection row. Original errors remain
    # in their own append-only diagnostic records; notification generation is stable.
    if same:
        connection.execute("UPDATE run_activity SET diagnostic_json=? WHERE rowid=?",
            (canonical_json(data), row['rowid']))
        return
    event = runs._append_activity(connection, run_id, 'attention_state', now, canonical_json(data))
    if data['generation'] is None:
        data['generation'] = event
        connection.execute("UPDATE run_activity SET diagnostic_json=? WHERE rowid=?", (canonical_json(data), event))
    refresh_notice(runs, connection, run_id, now=now, closed_state=resolution)


def refresh_notice(runs, connection, run_id, *, now, closed_state="resolved"):
    """One compact pending-delivery projection per Run, refreshed only on transitions."""
    attention = projection([item for _, item in issues(connection, run_id=run_id)])
    data = {'state':'attention_required' if attention else closed_state}
    if attention:
        codes = list(dict.fromkeys(item['code'] for item in attention['issues']))
        data['attention_required'] = {'code':attention['code'], 'codes':codes,
            'total':attention['total'], 'issues_truncated':attention['has_more']}
    content = canonical_json(data)
    version = runs._append_activity(connection, run_id, 'attention_changed', now, content)
    connection.execute("""INSERT INTO run_attention_notices(run_id,version,diagnostic_json) VALUES (?,?,?)
        ON CONFLICT(run_id) DO UPDATE SET version=excluded.version,diagnostic_json=excluded.diagnostic_json""",
        (run_id, version, content))


def pending_notices(runs, connection, *, instance, observer, limit):
    # A bounded join to named Runs and the observer cursor. It never reads
    # historical attention_state rows or enumerates all scheduler bindings.
    runs._attach_scheduler(connection)
    changed = "(o.state IS NULL OR instr(o.state,':')=0 OR CAST(substr(o.state,instr(o.state,':')+1) AS INTEGER)<a.version)"
    rows = connection.execute("""SELECT a.version AS version, b.name, a.diagnostic_json
        FROM run_attention_notices a JOIN runs r ON r.run_id=a.run_id
        JOIN scheduler_control.scheduler_bindings b ON b.instance=r.instance_id
            AND b.namespace='run' AND b.object_id=r.run_id
        LEFT JOIN scheduler_control.scheduler_observations o ON o.instance=b.instance
            AND o.observer_key=? AND o.object_type='attention' AND o.name=b.name
        WHERE r.instance_id=?
            AND json_extract(CAST(a.diagnostic_json AS TEXT),'$.state')='attention_required'
            AND """ + changed + """
        UNION ALL
        SELECT a.version AS version, b.name, a.diagnostic_json
        FROM scheduler_control.scheduler_observations o
        JOIN scheduler_control.scheduler_bindings b ON b.instance=o.instance
            AND b.namespace='run' AND b.name=o.name
        JOIN run_attention_notices a ON a.run_id=b.object_id
        WHERE o.instance=? AND o.observer_key=? AND o.object_type='attention'
            AND substr(o.state,1,5)!='done:'
            AND json_extract(CAST(a.diagnostic_json AS TEXT),'$.state')!='attention_required'
            AND """ + changed + """ ORDER BY 1, 2 LIMIT ?""",
        (observer, instance, instance, observer, limit + 1)).fetchall()
    events, receipts = [], []
    for row in rows[:limit]:
        event = {'name':row['name'], **json.loads(row['diagnostic_json'])}
        if event['state'] == 'attention_required':
            event['read_request'] = {'name':'run_status', 'arguments':{'name':row['name'],
                'intent':'status', 'diagnostic_after':0, 'diagnostic_limit':8}}
        events.append(event)
        receipts.append((row['name'], row['version'], event['state'] == 'attention_required'))
    return {'events':events, 'more_available':len(rows)>limit}, receipts


def supersede_submission_ancestors(runs, connection, row, *, now):
    # Successful resume proves continuity of the same frozen scientific inputs.
    # draft_from can change the question and cannot resolve its ancestor's issues.
    parent, seen = row['resume_from_run_id'], set()
    while parent and parent not in seen:
        seen.add(parent)
        previous = runs._row(connection, parent)
        if (previous['instance_id'], previous['operation_id']) != (row['instance_id'], row['operation_id']):
            break
        for _, issue in issues(connection, run_id=parent):
            if issue['scope'].startswith('submission:') and issue['code'] in SUBMISSION_CODES:
                record(runs, connection, parent, scope=issue['scope'], code=None, now=now, resolution='superseded')
        parent = previous['resume_from_run_id']


def issues(connection, *, run_id=None, instance_id=None):
    predicate, values = ('a.run_id=?', (run_id,)) if run_id is not None else ('r.instance_id=?', (instance_id,))
    rows = connection.execute("SELECT a.run_id, a.diagnostic_json FROM run_activity a JOIN runs r ON r.run_id=a.run_id "
        "WHERE a.activity='attention_state' AND " + predicate + " AND NOT EXISTS (SELECT 1 FROM run_activity newer "
        "WHERE newer.run_id=a.run_id AND newer.activity='attention_state' AND newer.rowid>a.rowid "
        "AND json_extract(CAST(newer.diagnostic_json AS TEXT),'$.scope')="
        "json_extract(CAST(a.diagnostic_json AS TEXT),'$.scope')) ORDER BY a.rowid", values).fetchall()
    return [(row['run_id'], json.loads(row['diagnostic_json'])) for row in rows]


def projection(items):
    active = [item for item in items if item['state'] == 'open']
    if not active:
        return None
    # Never expose execution IDs, scope/bindings or engineering bodies to Root.
    return {'code':active[0]['code'], 'action':ACTION, 'total':len(active),
        'issues':[{'code':item['code'], 'first_seen_at':item['first_seen_at'],
            'last_seen_at':item['last_seen_at'],
            **({'diagnostic_name':item['scope'].removeprefix('diagnostic:')}
               if item['scope'].startswith('diagnostic:') else {})} for item in active[:8]],
        'has_more':len(active)>8}


def migrate_running(runs, connection):
    """Retain pre-upgrade alerts, without interpreting bad aliases as missing bindings.

    Legacy events lack exact execution scopes. Keep their prior task-level alert
    until successful delivery; new execution observations use precise scopes.
    Resolved/new issues always take precedence over historical error records.
    """
    codes = sorted(SUBMISSION_CODES | {'experiment_implementation_ambiguous'})
    rows = connection.execute("SELECT a.run_id, json_extract(d.value,'$.code') AS code, "
        "MIN(a.recorded_at) AS first_seen, MAX(a.recorded_at) AS last_seen "
        "FROM run_activity a JOIN runs r ON r.run_id=a.run_id, "
        "json_each(CAST(a.diagnostic_json AS TEXT),'$.details') d "
        "WHERE r.state='running' AND a.activity IN ('tool_failed','output_rejected') "
        "AND json_extract(d.value,'$.code') IN (" + ','.join('?' for _ in codes) + ") "
        "AND NOT EXISTS (SELECT 1 FROM run_activity current WHERE current.run_id=a.run_id "
        "AND current.activity='attention_state' AND json_extract(CAST(current.diagnostic_json AS TEXT),'$.code')="
        "json_extract(d.value,'$.code')) GROUP BY a.run_id, code", codes).fetchall()
    for row in rows:
        scope = 'submission:' + row['code']
        record(runs, connection, row['run_id'], scope=scope, code=row['code'], now=row['first_seen'])
        if row['last_seen'] != row['first_seen']:
            record(runs, connection, row['run_id'], scope=scope, code=row['code'], now=row['last_seen'])
