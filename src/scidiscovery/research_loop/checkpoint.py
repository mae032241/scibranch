"""Stopped W fragment originals and a bounded resume brief; no execution grants.

The scientific stage remains the original job. This module neither seals that
stage nor starts a native turn, queries adapters, or updates scientific counters.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import configuration_updates
from . import contracts as c, execution, history
from .output_repair import OutputRepairRequired

VERSION = 1
MAX_BYTES = 64_000
MAX_BRIEF_BYTES = 4096
MAX_MATERIALS = 128
NATIVE_ORIGINALS = ('native-intent.json', 'native-outcome.json', 'usage.json',
                    'science-output.json', 'experiment-config.json', 'work-evidence.json')


def policy(value):
    if (not isinstance(value, dict) or set(value) - {'context', 'max_context_windows', 'context_handoff_version'} or 'context' not in value
            or value['context'] not in {'same_session', 'new_context'}):
        raise ValueError('W fragments require one explicit same_session or new_context strategy')
    if 'max_context_windows' in value and (type(value['max_context_windows']) is not int
            or value['max_context_windows'] not in (1, 2)):
        raise ValueError('W context windows must be 1 or 2')
    if 'context_handoff_version' in value and (type(value['context_handoff_version']) is not int
            or value['context_handoff_version'] not in (1,2) or 'max_context_windows' not in value):
        raise ValueError('Context handoff version 1/2 requires explicit window count')
    return dict(value)


def enabled(root, job):
    return enabled_for_policy(job,settings=configuration_updates.for_job(root,job))


def ordinary_worker(job):
    return (job['role'] == 'W' and not job.get('formal_review')
            and not job.get('final_only') and not job.get('final_report')
            and job.get('purpose') != 'closeout')


def enabled_for_policy(job,*,settings):
    value = settings.get('worker_fragment_policy')
    if value is None or not ordinary_worker(job):
        return False
    policy(value)
    return True


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def _binding(root, job, record):
    root, record = Path(root), Path(record)
    if root.resolve() != root or record.resolve() != record:
        raise ValueError('Fragment originals require exact instance paths')
    path = c.regular(root / 'jobs' / f'{job["step"]:03d}.json')
    if job.get('role') != 'W' or c.read(path) != c.job_record(job):
        raise ValueError('Fragment must bind the exact original W scientific job')
    chain = execution.records(root, job['step'])
    if record not in chain:
        raise ValueError('Fragment must name an accepted original native attempt')
    intent = c.read(c.regular(record / 'native-intent.json'))
    digest = c.record_ref(path)
    if intent.get('task_ref') != digest:
        raise ValueError('Fragment native intent changed its scientific job')
    return dict(step=job['step'], attempt=chain.index(record), job_ref=digest,
                stage_owner=digest, record=record.relative_to(root).as_posix())


def _science(root, job, originals):
    row = next((item for item in originals if Path(item['path']).name == 'science-output.json'), None)
    if row is None:
        return dict(status='missing', diagnostic='missing_output'), None
    outcome = next((item for item in originals if Path(item['path']).name == 'native-outcome.json'), None)
    try:
        receipt = c.native_outcome((root / outcome['path']).parent) if outcome else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        receipt = None
    if not isinstance(receipt, dict) or receipt.get('raw_ref') != dict(path=str(Path(root)/row['path']),size_bytes=row['size_bytes']):
        return dict(status='unverified', diagnostic='unbound_final_output'), None
    try:
        payload = c.science_payload(root / row['path'], 'W', job['handoff_protocol'],**c.response_options(job))
    except OutputRepairRequired as error:
        return dict(status='invalid', diagnostic=error.code), None
    return dict(status='present', diagnostic=None), payload


def _binding_for_inventory(root,job,record,inventory):
    c._inventory_identity(root,job['step'],inventory)
    if job!=inventory.job or record!=inventory.record or job.get('role')!='W':
        raise ValueError('Fragment must bind the exact original W scientific job')
    return dict(step=job['step'],attempt=inventory.attempt,job_ref=inventory.task_ref,
        stage_owner=inventory.task_ref,record=record.relative_to(root).as_posix())


def _science_for_inventory(root,originals,inventory):
    row=next((item for item in originals if Path(item['path']).name=='science-output.json'),None)
    if row is None:return dict(status='missing',diagnostic='missing_output'),None
    receipt=inventory.native_outcome
    if not isinstance(receipt,dict) or receipt.get('raw_ref')!=dict(path=str(Path(root)/row['path']),size_bytes=row['size_bytes']):
        return dict(status='unverified',diagnostic='unbound_final_output'),None
    if inventory.science_error is not None:
        return dict(status='invalid',diagnostic=inventory.science_error.code),None
    return dict(status='present',diagnostic=None),inventory.payload


def _capture(root, job, record, *, inventory):
    """Reference this stopped attempt and already-sealed terminal outputs.

    The caller must first establish that this attempt's native originals have
    stopped being written. Unknown usage or cleanup can be preserved after that
    boundary; an unknown outcome alone never establishes it. This reference API
    must not capture a live runner. Live observations remain in feedback instead.

    Repeated capture returns the prior verified checkpoint. Mutable drafts remain
    owned by later fragments and are never turned into a checkpoint snapshot.
    Managed call identity and candidate facts stay in their existing receipts.
    """
    root, record = Path(root), Path(record)
    binding = _binding_for_inventory(root,job,record,inventory)
    from . import worker_delivery, experiments
    destination = record / 'checkpoint.json'
    if destination.exists() or destination.is_symlink():
        return _validate(root, job, record,inventory=inventory)
    originals, missing = [], []
    for name in NATIVE_ORIGINALS:
        path = record / name
        if not path.exists() and not path.is_symlink():
            missing.append(name)
            continue
        original = c.regular(path)
        if original.stat().st_mode & 0o777 != 0o400:original.chmod(0o400)
        originals.append(dict(experiments.proof(original),path=original.relative_to(root).as_posix()))
    science, _ = _science_for_inventory(root,originals,inventory)
    work = c.workspace(root, job['step'])
    materials = []
    try:
        for call in history.managed_calls(inventory):
            accepted=c.read(root/call['accepted']['snapshot'])
            attempt=accepted.get('native_attempt')
            if not isinstance(attempt,dict) or type(attempt.get('number')) is not int:
                raise history.ObserverIntegrityError('Checkpoint output has no exact accepted native attempt')
            if attempt['number']>binding['attempt'] or call['status'] not in {'known_success','known_failed'}:continue
            outputs=history.scientific_outputs(inventory,call)
            for output in outputs:
                history._validate_proof(root,output)
                row=dict(source=str(root/output['snapshot']),
                    path=f'inputs/parents/{job["step"]:03d}/artifacts/experiments/{call["call_id"]}/artifacts/{output["name"]}',
                    size_bytes=output['size_bytes'])
                materials.append(dict(name=c.material_name(work,row),
                    original={key:value for key,value in output.items() if key!='name'}))
        material_status, material_diagnostic = 'observed', None
    except (history.ObserverIntegrityError,experiments.IntegrityError):
        raise
    except (OSError,ValueError) as error:
        # Output capacity or unsafe deliverables cannot erase native originals.
        # No missing/unsafe file is presented as a successfully retained result.
        materials = []
        material_status, material_diagnostic = 'unavailable', str(error)[:256]
    value = dict(version=VERSION, binding=binding, originals=originals,
                 missing_native_originals=missing, science=science, materials=materials,
                 material_status=material_status, material_diagnostic=material_diagnostic,
                 omitted_materials=0,
                 scope='Stopped native originals and sealed terminal outputs only; mutable drafts are not retained. Not a stage seal, scientific qualification or restart permission.')
    c.write(destination, value)
    destination.chmod(0o400)
    return _validate_value(root,job,record,value,inventory=inventory)


def _validate(root, job, record, *, inventory):
    """Verify this stopped attempt and saved originals without reading drafts."""
    root, record = Path(root), Path(record)
    value = c.read(c.regular(record / 'checkpoint.json'))
    return _validate_value(root,job,record,value,inventory=inventory)


def _validate_value(root,job,record,value,*,inventory):
    from . import experiments
    if value.get('version') != VERSION or value.get('binding') != _binding_for_inventory(root,job,record,inventory):
        raise ValueError('Fragment checkpoint binding changed')
    for row in value['originals']:
        path = root / row['path']
        if path.parent != record or path.name not in NATIVE_ORIGINALS:
            raise ValueError('Fragment native original changed')
        actual=experiments.proof(c.regular(path))
        if any(actual[key]!=row.get(key) for key in ('size_bytes','file_stat')):
            raise ValueError('Fragment native original changed')
    expected, _ = _science_for_inventory(root,value['originals'],inventory)
    if value['science'] != expected:
        raise ValueError('Fragment scientific output diagnosis changed')
    for row in value['materials']:
        original = row['original']
        if original['retention'] == 'archived':
            history._validate_proof(root,original)
    # The existing scientific name carries the exact original call selection.
    # Validate only those calls; a later or unrelated call cannot rewrite this
    # checkpoint's necessary terminal controls or become a new execution.
    selected={}
    for row in value['materials']:
        name=Path(row['name']);parts=name.parts
        if (len(parts)>=4 and parts[0]=='experiments' and parts[2]=='artifacts'
                and '..' not in parts and name.as_posix()==row['name']):
            selected.setdefault(parts[1],[]).append(row)
    calls=history.managed_calls(inventory,call_ids=list(selected)) if selected else []
    for call in calls:
        accepted=c.read(root/call['accepted']['snapshot'])
        attempt=accepted.get('native_attempt')
        if (not isinstance(attempt,dict) or type(attempt.get('number')) is not int
                or attempt['number']>value['binding']['attempt'] or call['status'] not in {'known_success','known_failed'}):
            raise ValueError('Fragment terminal output lost its original native attempt')
        outputs=history.scientific_outputs(inventory,call)
        for row in selected[call['call_id']]:
            name=row['name'].split('/',3)[3]
            matches=[output for output in outputs if output['name']==name]
            if len(matches)!=1:
                raise ValueError('Fragment terminal output differs from its saved original')
            current={key:item for key,item in matches[0].items() if key!='name'}
            saved=row['original']
            changed=(current!=saved if 'file_stat' in saved else current['size_bytes']!=saved['size_bytes'])
            if changed:
                raise ValueError('Fragment terminal output differs from its saved original')
    return value


def validate(root,job,record,*,inventory):
    if record!=inventory.record or job!=inventory.job:
        raise ValueError('Checkpoint belongs to another accepted inventory')
    return inventory.checkpoint


def _clip(value, limit):
    data = value.encode('utf-8')
    return data[:limit].decode('utf-8', errors='ignore'), len(data) > limit


def end_stage(root, job, record, reason, *, inventory, work_exit=None):
    """Record why control ended this stage without rewriting scientific bytes."""
    from .protocol import require_current
    require_current(root,job)
    root, record = Path(root), Path(record)
    saved = validate(root, job, record,inventory=inventory)
    value = dict(version=VERSION, binding=saved['binding'], reason=reason,
                 checkpoint_ref=c.record_ref(record / 'checkpoint.json'),
                 raw_ref=c.file_ref(c.regular(record / 'science-output.json')),
                 **(dict(work_exit=work_exit) if work_exit is not None else {}))
    path = record / 'stage-end.json'
    if path.exists() or path.is_symlink():
        if stage_end(root, job, record) != value:
            raise ValueError('Scientific stage ending changed its original control reason')
    else:
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 128:
            raise ValueError('Stage ending requires a bounded control reason')
        c.write(path, value)
        path.chmod(0o400)
    return value


def stage_end(root, job, record):
    root, record = Path(root), Path(record)
    value = c.read(c.regular(record / 'stage-end.json'))
    if (value.get('version') != VERSION or value.get('binding') != _binding(root, job, record)
            or value.get('checkpoint_ref') != c.record_ref(c.regular(record / 'checkpoint.json'))
            or value.get('raw_ref') != c.file_ref(c.regular(record / 'science-output.json'))
            or not isinstance(value.get('reason'), str) or not value['reason'].strip() or len(value['reason']) > 128):
        raise ValueError('Scientific stage ending differs from its exact fragment originals')
    return value


def brief(root, job, record):
    """Return at most 4KB of ordinary science and direct-file availability.

    Host paths, hashes, raw logs and private execution bindings are omitted. Full
    originals stay in the checkpoint; omissions never become inferred evidence.
    """
    root, record = Path(root), Path(record)
    value = c.read(c.regular(record/'checkpoint.json'))
    _, science = _science(root, job, value['originals'])
    result = dict(task='science-task.json', science_status=value['science']['status'], scientific_summary=None,
                  current_understanding=None, limitations=[], omitted_limitations=0, direct_files=[],
                  omitted_files=value['omitted_materials'], truncated=False,
                  material_status=value['material_status'],
                  missing_native_originals=value['missing_native_originals'],
                  original_access='Read sealed parents and lineage originals through science-task.json. The previous fragment full text is not mounted; this preview is incomplete when omissions are reported.',
                  interpretation='Current understanding and limitations are authored claims, not independently verified observations. Unstated questions remain unknown.',
                  scope='Fragment preview; original task and permissions remain binding. No restart or scientific qualification is granted.')
    if science is not None:
        result['scientific_summary'], a = _clip(science['summary'], 384)
        result['current_understanding'], b = _clip(science['scientific_result'], 1400)
        result['limitations'],result['omitted_limitations']=c._whole_limitations(science['limitations'],byte_limit=1536)
        result['truncated'] = a or b or result['omitted_limitations']>0
        result['stage_intent']=science['stage_intent']
    plan=job.get('history',{}).get('view',{}).get('mechanisms',{}).get('unfinished_plan')
    if plan is not None:
        result['unfinished_mechanisms']=dict(count=plan['remaining_count'],index=plan['index'],
            interpretation='Original unconsumed opportunities, not proof of scientifically untested mechanisms. Read the index for all entries.')
    for row in value['materials']:
        name, clipped = _clip(row['name'], 256)
        result['direct_files'].append(dict(name=name, name_truncated=clipped,
            retention=row['original']['retention']))
    while len(_bytes(result)) > MAX_BRIEF_BYTES and result['direct_files']:
        result['direct_files'].pop()
        result['omitted_files'] += 1
    # UTF-8 lengths alone do not bound JSON: quotes, backslashes and controls
    # expand during encoding. Trim only scientific excerpts using the actual
    # bytes written by c.write, preserving diagnosis and omission metadata.
    for field in ('current_understanding', 'scientific_summary'):
        while len(_bytes(result)) > MAX_BRIEF_BYTES and result[field]:
            result[field] = _clip(result[field], len(result[field].encode()) // 2)[0]
            result['truncated'] = True
    while len(_bytes(result)) > MAX_BRIEF_BYTES and result['limitations']:
        result['limitations'].pop();result['omitted_limitations']+=1;result['truncated']=True
    return result


def disposition(science, *, review_requested=False):
    """Interpret explicit W intent; host cadence can seal a partial W handoff.

    This internal seam is enabled by the final U3 schema, not a protocol upgrade
    or alternate execution contract by itself.
    """
    if science is None:
        return 'repair'
    intent = science.get('stage_intent')
    if intent not in {'continue','handoff','revise_h','review','extract','decision'}:
        raise OutputRepairRequired('stage_intent_invalid', 'Use continue, handoff, revise_h, review, extract (when allowed) or decision for the exact intended route.')
    return ('handoff' if review_requested and intent=='continue' else intent)
