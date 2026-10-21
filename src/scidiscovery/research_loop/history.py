"""Rebuildable scientific history and optional host observations; no attachments."""
from __future__ import annotations

from . import execution

import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import unquote, urlsplit

from . import contracts as c

PAGE_ROWS = 8
TEXT_CHARS = 8192
MAX_PAGE_BYTES = 64_000
MAX_HISTORY_BYTES = 32 * 1024 * 1024
MAX_BRIEF_BYTES = 4096


class UnresolvedImplementation(ValueError):
    """A scientific object choice has no unique frozen original; not corruption."""
MAX_OBSERVER_BYTES = 256_000
MAX_OBJECT_BYTES = 128 * 1024 * 1024
OBSERVER_SECONDS = 5
MAX_CANDIDATES = 128
ID = re.compile(r'[A-Za-z0-9._-]{1,80}\Z')
HANDLE = re.compile(r'history/nodes/([0-9]{3,})/artifacts/(.+)\Z')


class ObserverIntegrityError(ValueError):
    pass


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n').encode()


def observer_config(value):
    """Freeze a trusted synchronous, side-effect-free external adapter."""
    if (not isinstance(value, dict) or not {'adapter','config'} <= set(value)
            or set(value) - {'adapter','config','contract_version','source_contract'}
            or not isinstance(value['config'],dict) or 'contract_version' in value and (type(value['contract_version']) is not int or value['contract_version'] != 2)):
        raise ValueError('Observer requires an exact adapter and configuration')
    path = Path(value['adapter']).absolute()
    if path.resolve() != path:
        raise ValueError('Observer requires an original configuration')
    c.regular(path)
    result = dict(adapter=str(path), config=value['config'],
                  **(dict(contract_version=2) if value.get('contract_version') == 2 else {}))
    if 'source_contract' in value:
        from . import execution_inputs
        if value['source_contract'] != execution_inputs.SOURCE_CONTRACT:
            raise ValueError('Observer source contract must explicitly cover all declared inputs and contributors')
        result['source_contract'] = dict(contract=execution_inputs.source_contract())
    return result


def observer_input_sources(root, job, observation, *, source=None):
    """Only the retained trusted contract/view; display coverage grants nothing."""
    from . import execution_inputs, qualification as q
    from . import originals
    config = (originals.source_settings(source) if source is not None else c.settings(root, job)).get('history_observer')
    original = observation.get('input_sources')
    if config is None or config.get('source_contract') is None:
        if original is not None:
            raise q.IntegrityError('Observer input sources have no frozen trusted contract')
        return None
    contract = config['source_contract']
    if contract['contract'] != execution_inputs.source_contract():
        raise q.IntegrityError('Observer source contract changed')
    if original is None:
        return None  # Optional adapter failure remains unknown, never implicit empty.
    if source is not None:
        logical_root = Path(source.original_root)
        originals.source_raw(source)
        record = source.native_record
        raw = logical_root / record / 'science-output.json' if record else Path(c._execution_raw(logical_root, job['step']))
    else:
        logical_root = Path(root)
        raw = Path(c._execution_raw(root, job['step']))
    if (not isinstance(original, dict) or set(original) != {'contract','job','raw','view'}
            or original['contract'] != contract
            or original['job'] != q._ref(root, logical_root / 'jobs' / f'{job["step"]:03d}.json', source=source)
            or q._read(root, original['job'], source=source) != job or original['raw'] != q._ref(root, raw, source=source)
            or original['raw']['sha256'] != observation['binding']['source_sha256']):
        raise q.IntegrityError('Observer source view differs from its original task')
    return execution_inputs.validate_source_view(root, job, original['view'], source=source)


def handle(row):
    # Existing four-field paths retain the original node ownership.
    parts = Path(row['path']).parts
    return 'history/nodes/' + '/'.join(parts[2:])


def resolve(root, target, receipts, *, current=None):
    match = HANDLE.fullmatch(target)
    if not match or '..' in Path(target).parts or '\\' in target or unquote(target) != target:
        raise ValueError('History reuse requires an exact historical implementation handle')
    step = int(match[1])
    if match[1] != f'{step:03d}':
        raise ValueError('History node identity is not canonical')
    if current is not None and step == current['step']:
        rows = current['materials']
    else:
        receipt = next((row for row in receipts if row['step'] == step), None)
        if receipt is None:
            raise ValueError('Historical implementation is outside the frozen history')
        from . import originals
        path = originals.source_record(originals.node(root, step), 'result-identity.json')
        if c.digest(c.regular(path)) != receipt['seal_sha256']:
            raise ValueError('Historical node seal changed')
        rows = c.read(path)['materials']
    row = next((row for row in rows if handle(row) == target), None)
    if row is None:
        raise ValueError('Historical implementation is not an exact sealed deliverable')
    return row


def _proof(root, source, *, context=None):
    from .protocol import require_current
    require_current(root)
    if not isinstance(source, str):
        raise ValueError('Observer evidence path is not a string')
    path = root / source
    if Path(source).is_absolute() or '..' in Path(source).parts or path.resolve() != path:
        raise ObserverIntegrityError('Observer evidence must be an exact instance original')
    c.regular(path)
    size = path.stat().st_size
    from . import experiments
    sha = c.digest(path) if context is None else experiments.proof(path,context=context)['sha256']
    objects = root / 'host-history/objects'
    snapshot = objects / sha
    if objects.resolve() != objects or snapshot.is_symlink():
        raise ObserverIntegrityError('Observer archive must contain original files')
    if snapshot.exists():
        if not snapshot.is_file():raise ObserverIntegrityError('Archived observer original changed')
        actual=c.digest(c.regular(snapshot)) if context is None else experiments.proof(snapshot,context=context)['sha256']
        if actual != sha:
            raise ObserverIntegrityError('Archived observer original changed')
    else:
        objects.mkdir(parents=True, exist_ok=True)
        if objects.resolve() != objects:
            raise ObserverIntegrityError('Observer archive must be an original directory')
        # A timed-out bounded copy must not poison the next explicit operation.
        # Keep its partial; a later operation verifies the original anew.
        temporary = objects / (sha + ('.'+uuid.uuid4().hex if context is not None else '') + '.tmp')
        if temporary.exists() or temporary.is_symlink():
            raise ObserverIntegrityError('Observer archive has an uncertain temporary original')
        c._copy_bound(path,temporary,sha,context=context)
        with temporary.open('rb') as stream:os.fsync(stream.fileno())
        if context is not None:context.remaining_seconds()
        temporary.replace(snapshot)
    return dict(path=source, sha256=sha, size_bytes=size, retention='archived',
                snapshot=snapshot.relative_to(root).as_posix())


def read_adapter(config, request):
    """The trusted synchronous observation transport carries complete records."""
    adapter = c.regular(Path(config['adapter']))
    with tempfile.TemporaryFile(mode='w+') as request_file, tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        json.dump(request,request_file,ensure_ascii=False,allow_nan=False)
        request_file.seek(0)
        result = subprocess.run([sys.executable, '-I', str(adapter)], stdin=request_file,
            stdout=out, stderr=err, timeout=OBSERVER_SECONDS, env={'PATH': os.defpath})
        if result.returncode:raise ValueError('Observer adapter failed')
        out.seek(0)
        value = json.load(out)
    if config.get('contract_version') == 2 and isinstance(value, dict) and set(value) == {'integrity_error'}:
        raise ObserverIntegrityError(str(value['integrity_error']))
    return value



def _managed_config(job,root=None):
    if job.get('formal_review'):return None
    configured = ((job.get('search') or {}).get('breadth') or 'experiment_mode' in job and
        (job['experiment_mode']=='local' and job['role']=='W'
         or root is not None and (execution.record(root,job['step'])/'experiment-config.json').exists()))
    return dict(adapter_sha256='registered-originals-v1') if configured else None


def _managed_proof(root,path,original,*,context=None):
    proof=_proof(root,Path(path).relative_to(root).as_posix(),context=context)
    if any(proof[key]!=original[key] for key in ('sha256','size_bytes')):
        raise ObserverIntegrityError('Managed file differs from completed original')
    return proof


def _execution_interface(root, accepted):
    from . import originals
    if originals.inherited(root, accepted['scope']['step']):
        return _original_interface(originals.node(root, accepted['scope']['step']), accepted)
    """The host-frozen interface chooses authority, never worker-written markers."""
    attempt=accepted.get('native_attempt') or {}
    number=attempt.get('number',0)
    # Interface identity participates in validating a terminal repair itself.
    # Reading result eligibility here would re-enter that same repair chain.
    intents=list(execution.accepted_intents(root,accepted['scope']['step']))
    if type(number) is not int or not 0<=number<len(intents):
        raise ObserverIntegrityError('Managed host execution attempt is not accepted')
    path=intents[number][0]/'experiment-config.json'
    if not path.exists():raise ObserverIntegrityError('Managed host execution configuration missing')
    config=c.read(c.regular(path))
    if config.get('scope')!=accepted['scope'] or config.get('execution_interface')!=accepted.get('interface'):
        raise ObserverIntegrityError('Managed interface differs from frozen host configuration')
    return config['execution_interface']


def managed_status(root, row, *, context=None):
    """One immutable terminal authority shared with Worker delivery and history."""
    from . import experiments
    from . import originals
    step = (row.get('scope') or {}).get('step')
    if type(step) is int and step > 0 and originals.inherited(root, step):
        source = originals.node(root, step)
        validate_managed_original(source, originals.source_job(source), [row])
        return row['status']
    if row.get('retirement') is not None:
        if any(row.get(key) is not None for key in ('completed','terminal','observation')) or 'execution' in row or row.get('outputs'):
            raise ObserverIntegrityError('Non-submission cannot contain executed terminal evidence')
        accepted=c.read(root/row['accepted']['snapshot'])
        value=c.read(root/row['retirement']['snapshot'])
        if value.get('accepted',{}).get('sha256')!=row['accepted']['sha256']:
            raise ObserverIntegrityError('Retirement differs from the archived accepted call')
        try:
            experiments.verify_retirement(value,lambda item:root/'host-history/objects'/item['sha256'],
                interface=_execution_interface(root,accepted),context=context)
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise ObserverIntegrityError('Archived permanent retirement proof changed') from error
        return 'not_submitted'
    if row.get('completed') is None:return 'unknown'
    accepted=c.read(root/row['accepted']['snapshot']);completed=c.read(root/row['completed']['snapshot'])
    interface=_execution_interface(root,accepted)
    observation=row.get('observation')
    if observation is not None:
        value=c.read(root/observation['snapshot'])
        if (value['accepted']['sha256']!=row['accepted']['sha256']
                or value['completed']['sha256']!=row['completed']['sha256']):
            raise ObserverIntegrityError('Managed observation differs from archived call originals')
        try:
            return experiments.verify_observation(value,lambda proof:
                root/'host-history/objects'/proof['sha256'],interface=interface,context=context)
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise ObserverIntegrityError('Managed execution observation original changed') from error
    if interface.get('observation_version') is not None:return 'unknown'
    # Compatibility is restricted to the existing local receipt contract.
    if not ('local_observation_version' in accepted or 'receipt_transport' in accepted):return 'unknown'
    terminal=c.read(root/row['terminal']['snapshot']) if row.get('terminal') is not None else None
    return experiments.local_terminal_status(accepted,completed,terminal)


def _managed_outputs(interface, completed, observed):
    if observed is not None and interface.get('terminal_evidence')!='local_process':
        return observed['outputs']
    return completed.get('outputs',[]) if 'result' in completed else observed['outputs'] if observed is not None else []


def scientific_outputs(root, job, call):
    """Publish selected scientific assets from the full verified call archive."""
    from . import experiments, execution
    root=Path(root)
    from . import originals
    if originals.inherited(root, job['step']):
        source = originals.node(root, job['step'])
        validate_managed_original(source, originals.source_job(source), [call])
        if call.get('observation') is None:
            return [dict(row, origin_identity=source.origin_identity) for row in call.get('outputs', [])]
        def archived_ref(proof):
            return originals.resolve_ref(source, dict(proof, path=proof['snapshot']))
        accepted = c.read(archived_ref(call['accepted']))
        attempt = (accepted.get('native_attempt') or {}).get('number', 0)
        record = f'native-evidence/{job["step"]:03d}' + (f'/continuations/{attempt:03d}' if attempt else '')
        config = c.read(originals.resolve_ref(source, dict(path=record + '/experiment-config.json')))
        observed = c.read(archived_ref(call['observation']))
        archived = dict(observed, completed=dict(observed['completed'], path=str(archived_ref(call['completed']))),
            outputs=[dict(item, path=str(originals.resolve_ref(source, dict(item, path='host-history/objects/' + item['sha256']))))
                for item in observed['outputs']])
        result = []
        for descriptor in experiments.scientific_outputs(config, archived):
            matches = [row for row in call['outputs'] if row['name'] == descriptor.name
                and row['sha256'] == descriptor.sha256 and row['size_bytes'] == descriptor.size_bytes
                and archived_ref(row) == Path(descriptor.local_path)]
            if len(matches) != 1:
                raise ObserverIntegrityError('Inherited scientific output lost its exact archived descriptor')
            result.append(dict(matches[0], path=matches[0]['snapshot'], name=descriptor.name,
                origin_identity=source.origin_identity))
        return result
    managed_status(root,call)  # All control originals remain mandatory and private.
    if call.get('observation') is None:
        return call.get('outputs',[])
    accepted=c.read(c.regular(root/call['accepted']['snapshot']))
    attempt=(accepted.get('native_attempt') or {}).get('number',0)
    config=c.read(c.regular(execution.record(root,job['step'],attempt)/'experiment-config.json'))
    if config['execution_interface']!=_execution_interface(root,accepted):
        raise ObserverIntegrityError('Scientific output interface changed')
    observed=c.read(c.regular(root/call['observation']['snapshot']))
    archived=dict(observed,completed=dict(observed['completed'],path=str(root/call['completed']['snapshot'])),
        outputs=[dict(item,path=str(root/'host-history/objects'/item['sha256']))
        for item in observed['outputs']])
    result=[]
    for descriptor in experiments.scientific_outputs(config,archived):
        proof=_proof(root,Path(descriptor.local_path).relative_to(root).as_posix())
        if proof['sha256']!=descriptor.sha256 or proof['size_bytes']!=descriptor.size_bytes:
            raise ObserverIntegrityError('Selected scientific output original changed')
        result.append(dict(proof,path=proof['snapshot'],name=descriptor.name))
    return result


def managed_calls(root, job, *, context=None):
    """Capture small common receipts, independent of domain candidates or observers."""
    from . import experiments, breadth, generic_results
    from .experiment_client import record_directory
    records=experiments.Records(dict(record_dir=str(record_directory(root,job['step']))))
    from .protocol import stage_scope
    scope=stage_scope(root,job);result=[]
    for directory in records.calls():
        accepted_path=directory/'accepted.json'
        accepted=c.read(c.regular(accepted_path))
        if accepted.get('scope')!=scope or accepted.get('call_id')!=directory.name:
            raise ObserverIntegrityError('Managed original escaped its stage owner')
        binding=breadth.check_binding((job.get('search') or {}).get('breadth'),accepted.get('mechanism_binding'),scope)
        completion=directory/'completed.json';state='unknown';terminal=None;value={}
        if completion.exists():
            value=c.read(c.regular(completion))
            if value.get('call_id')!=directory.name or value.get('scope')!=scope or value.get('mechanism_binding')!=binding:
                raise ObserverIntegrityError('Managed completion changed accepted mechanism')
            terminal_path=directory/'terminal.json'
            if terminal_path.exists():
                terminal=_proof(root,terminal_path.relative_to(root).as_posix(),context=context)
        observed_path=directory/'execution-observation.json';observed=None;extra=[]
        if observed_path.exists():
            observed=c.read(c.regular(observed_path))
            extra=[observed[key] for key in ('accepted','completed','submission')]+observed['proofs']
            if 'native_terminal' in observed:extra.append(observed['native_terminal'])
        interface=_execution_interface(root,accepted)
        retired_path=directory/'submission-retirement.json'
        if retired_path.exists() or retired_path.is_symlink():
            value=experiments.retirement(dict(record_dir=str(records.root),scope=scope,execution_interface=interface,breadth=(job.get('search') or {}).get('breadth')),directory.name)
            originals=experiments.retirement_originals(value,accepted)
            row=dict(call_id=directory.name,scope=scope,mechanism_binding=binding,status='not_submitted',
                result_protocol=generic_results.protocol(interface),accepted=_proof(root,accepted_path.relative_to(root).as_posix(),context=context),
                completed=None,terminal=None,retirement=_proof(root,retired_path.relative_to(root).as_posix(),context=context),
                originals=[_managed_proof(root,item['path'],item,context=context) for item in originals])
            row['status']=managed_status(root,row,context=context);result.append(row)
            continue
        outputs=_managed_outputs(interface,value,observed)
        row=dict(call_id=directory.name,scope=scope,mechanism_binding=binding,status=state,
            result_protocol=generic_results.protocol(interface),
            accepted=_proof(root,accepted_path.relative_to(root).as_posix(),context=context),
            completed=_proof(root,completion.relative_to(root).as_posix(),context=context) if completion.exists() else None,terminal=terminal,
            **(dict(observation=_proof(root,observed_path.relative_to(root).as_posix(),context=context)) if observed is not None else {}),
            **(dict(originals=[_managed_proof(root,directory/'inputs'/item['name'],item,context=context) for item in accepted.get('snapshot_files',[])]
                    +[_managed_proof(root,item['path'],item,context=context) for item in (extra if observed is not None else value.get('proofs',[]))],
                outputs=[dict(name=item['name'],**_managed_proof(root,item['path'],item,context=context)) for item in outputs],
                execution=dict(returncode=value.get('returncode'),wall_seconds=value.get('wall_seconds'),result=value.get('result'),
                    **(dict(native_state=value['native_state']) if 'native_state' in value else {})))
               if 'result' in value or observed is not None else {}))
        if observed is not None:
            # Retain every collected descriptor even when local result outputs use scientific names.
            for item in observed['outputs']:_managed_proof(root,item['path'],item,context=context)
        row['status']=managed_status(root,row,context=context)
        result.append(row)
    return result


def validate_managed(root, job, rows, *, context=None):
    from . import originals
    if originals.inherited(root, job['step']):
        return validate_managed_original(originals.node(root, job['step']), job, rows)
    from . import breadth, generic_results
    from .protocol import stage_scope
    if not isinstance(rows,list):raise ObserverIntegrityError('Managed receipt list required')
    scope=stage_scope(root,job);seen=set()
    for row in rows:
        shapes=({'call_id','scope','mechanism_binding','status','accepted','completed','terminal'},
                {'call_id','scope','mechanism_binding','status','accepted','completed','terminal','originals','outputs','execution'},
                {'call_id','scope','mechanism_binding','status','accepted','completed','terminal','retirement','originals'})
        if set(row)-{'observation','result_protocol'} not in shapes or row['call_id'] in seen or row['scope']!=scope:
            raise ObserverIntegrityError('Managed receipt owner differs')
        seen.add(row['call_id'])
        for key in ('accepted','completed','terminal','observation','retirement'):
            proof=row.get(key)
            if proof is not None:
                if proof.get('retention')!='archived':raise ObserverIntegrityError('Managed receipt original unavailable')
                _validate_proof(root,proof,context=context)
        accepted=c.read(root/row['accepted']['snapshot'])
        if accepted.get('call_id')!=row['call_id'] or accepted.get('scope')!=scope or accepted.get('mechanism_binding')!=row['mechanism_binding']:
            raise ObserverIntegrityError('Managed receipt differs from accepted original')
        if 'result_protocol' in row and row['result_protocol']!=generic_results.protocol(_execution_interface(root,accepted)):
            raise ObserverIntegrityError('Managed result protocol differs from frozen host interface')
        breadth.check_binding((job.get('search') or {}).get('breadth'),row['mechanism_binding'],scope)
        for proof in [*row.get('originals',[]),*row.get('outputs',[])]:_validate_proof(root,proof,context=context)
        if 'execution' in row:
            completed=c.read(root/row['completed']['snapshot'])
            if row['execution']!=dict(returncode=completed.get('returncode'),wall_seconds=completed.get('wall_seconds'),result=completed.get('result'),
                    **(dict(native_state=completed['native_state']) if 'native_state' in completed else {})):
                raise ObserverIntegrityError('Managed results differ from completed original')
            originals=[*accepted.get('snapshot_files',[])]
            observed=c.read(root/row['observation']['snapshot']) if row.get('observation') else None
            if observed is not None:
                originals.extend([observed[key] for key in ('accepted','completed','submission')]+observed['proofs'])
                if 'native_terminal' in observed:originals.append(observed['native_terminal'])
            else:originals.extend(completed.get('proofs',[]))
            if (len(row['originals'])!=len(originals) or any(any(proof[key]!=original[key] for key in ('sha256','size_bytes'))
                    for proof,original in zip(row['originals'],originals))):
                raise ObserverIntegrityError('Managed input or log original changed')
            outputs=_managed_outputs(_execution_interface(root,accepted),completed,observed)
            if len(row['outputs'])!=len(outputs):
                raise ObserverIntegrityError('Managed outputs differ from completed original')
            for proof,original in zip(row['outputs'],outputs):
                if any(proof[key]!=original[key] for key in ('name','sha256','size_bytes')):
                    raise ObserverIntegrityError('Managed output changed')
        if row.get('retirement') is not None:
            value=c.read(root/row['retirement']['snapshot'])
            from . import experiments
            originals=experiments.retirement_originals(value,accepted)
            if (len(row['originals'])!=len(originals) or any(any(item[key]!=original[key]
                    for key in ('sha256','size_bytes')) for item,original in zip(row['originals'],originals))):
                raise ObserverIntegrityError('Retirement native originals differ from the frozen receipt')
        if row['status'] not in {'known_success','known_failed','not_submitted','unknown'}:raise ObserverIntegrityError('Managed state differs')
        if row['status']!=managed_status(root,row,context=context):
            raise ObserverIntegrityError('Managed state differs from verified execution observation')


def _original_interface(source, accepted):
    from . import originals
    number = (accepted.get('native_attempt') or {}).get('number', 0)
    if type(number) is not int or not 0 <= number < 10000:
        raise ObserverIntegrityError('Inherited native attempt has no exact finite identity')
    record = f'native-evidence/{source.step:03d}' + (f'/continuations/{number:03d}' if number else '')
    config = c.read(originals.resolve_ref(source, dict(path=record + '/experiment-config.json')))
    if config.get('scope') != accepted['scope'] or config.get('execution_interface') != accepted['interface']:
        raise ObserverIntegrityError('Inherited managed interface lost its frozen native owner')
    return config['execution_interface']


def validate_managed_original(source, job, rows):
    """Verify retained call bindings/terminals without querying a provider."""
    from . import originals, experiments, breadth, generic_results
    job_ref = originals.original_ref(source, f'jobs/{job["step"]:03d}.json')
    scope = dict(step=job['step'], role=job['role'], handoff_protocol=job['handoff_protocol'],
        job_sha256=job_ref['sha256'], execution_key=job_ref['sha256'],
        mechanism_ref=None if (job.get('search') or {}).get('breadth') is not None else
            ((job.get('search') or {}).get('focus') or {}).get('mechanism_ref'))
    def proof_path(proof):
        if proof.get('retention') != 'archived' or proof.get('snapshot') != 'host-history/objects/' + proof['sha256']:
            raise ObserverIntegrityError('Inherited managed original has no exact archived bytes')
        return originals.resolve_ref(source, dict(proof, path=proof['snapshot']))
    def native_proof(proof):
        return originals.resolve_ref(source, dict(path='host-history/objects/' + proof['sha256'],
            sha256=proof['sha256'], size_bytes=proof['size_bytes']))
    seen = set()
    for row in rows:
        if row['call_id'] in seen or row['scope'] != scope:
            raise ObserverIntegrityError('Inherited managed call escaped its exact stage owner')
        seen.add(row['call_id'])
        for key in ('accepted', 'completed', 'terminal', 'observation', 'retirement'):
            if row.get(key) is not None:
                proof_path(row[key])
        accepted = c.read(proof_path(row['accepted']))
        if (accepted.get('scope') != scope or accepted.get('call_id') != row['call_id']
                or accepted.get('mechanism_binding') != row['mechanism_binding']):
            raise ObserverIntegrityError('Inherited managed call changed its accepted identity')
        breadth.check_binding((job.get('search') or {}).get('breadth'), row['mechanism_binding'], scope)
        interface = _original_interface(source, accepted)
        if 'result_protocol' in row and row['result_protocol'] != generic_results.protocol(interface):
            raise ObserverIntegrityError('Inherited managed result protocol changed')
        for proof in [*row.get('originals', []), *row.get('outputs', [])]:
            proof_path(proof)
        status = 'unknown'
        if row.get('retirement') is not None:
            value = c.read(proof_path(row['retirement']))
            experiments.verify_retirement(value, native_proof, interface=interface)
            status = 'not_submitted'
        elif row.get('completed') is not None:
            completed = c.read(proof_path(row['completed']))
            if row.get('observation') is not None:
                value = c.read(proof_path(row['observation']))
                if value['accepted']['sha256'] != row['accepted']['sha256'] or value['completed']['sha256'] != row['completed']['sha256']:
                    raise ObserverIntegrityError('Inherited terminal differs from its exact accepted call')
                status = experiments.verify_observation(value, native_proof, interface=interface)
            elif interface.get('observation_version') is None:
                terminal = c.read(proof_path(row['terminal'])) if row.get('terminal') else None
                status = experiments.local_terminal_status(accepted, completed, terminal)
            if row.get('execution') != dict(returncode=completed.get('returncode'), wall_seconds=completed.get('wall_seconds'),
                    result=completed.get('result'), **(dict(native_state=completed['native_state']) if 'native_state' in completed else {})):
                raise ObserverIntegrityError('Inherited execution facts changed their completed original')
        if status != row['status']:
            raise ObserverIntegrityError('Inherited managed state differs from verified terminal originals')


def _check_source_subject(declared, binding):
    if binding is not None and not any(row['origin_step'] == binding['subject']['producer_step']
            and row['material'] == binding['subject']['material'] for row in declared['contributions']):
        raise ObserverIntegrityError('Managed declared inputs omitted their exact authorized package')


def managed_input_coverage(root, job, rows):
    """Capture coverage by existing input edges after validating original calls."""
    from . import execution_authority as authority, execution_inputs, qualification as q
    root = Path(root).resolve()
    validate_managed(root, job, rows)
    result = []; memo = {}
    for row in rows:
        accepted = c.read(root / row['accepted']['snapshot'])
        if row['status'] in {'known_success','known_failed'} and accepted.get('input_sources') is not None:
            inputs = execution_inputs.managed_source_contributions(root, job, accepted)
            if inputs is not None and not inputs['complete']:
                result.append(dict(call_id=row['call_id'],accepted_ref=q._ref(root, root / row['accepted']['snapshot']),
                    complete=False,missing_reason='managed_inputs_not_covered_by_frozen_edges'))
                continue  # An explicit incomplete view cannot fall back to broader compiler coverage.
            if inputs is not None and inputs['complete']:
                bound = authority.accepted_input_subject(root, job, accepted) if accepted.get('execution_authority') is not None else None
                if accepted.get('execution_authority') is not None and bound is None:
                    raise q.IntegrityError('Declared managed input has no verified original authority')
                _check_source_subject(inputs, bound)
                result.append(dict(call_id=row['call_id'],accepted_ref=q._ref(root, root / row['accepted']['snapshot']),
                    complete=True,missing_reason=None,input_sources=accepted['input_sources']['contract']['sha256'],
                    **(dict(binding=bound) if bound is not None else {})))
                continue
        bound = (authority.accepted_input_subject(root, job, accepted)
            if row['status'] in {'known_success','known_failed'} else None)
        compiler = None
        if bound is not None:
            key = json.dumps(bound['subject'], sort_keys=True)
            if key not in memo:
                memo[key] = execution_inputs.execution_input_coverage(root, job, bound['subject'])
            compiler = memo[key]
        complete = compiler is not None
        client_binding = None
        if complete:
            subject = bound['subject']; seal = q._read(root, subject['producer_seal'])
            compiled = [item for item in seal.get('compiled_implementations', []) if item['material'] == subject['material']]
            if len(compiled) != 1 or compiled[0]['compiler'] != compiler:
                raise q.IntegrityError('Managed compiler differs from its exact sealed payload')
            client_binding = compiled[0].get('client_binding')
        result.append(dict(call_id=row['call_id'], accepted_ref=q._ref(root, root / row['accepted']['snapshot']),
            complete=complete, missing_reason=None if complete else 'managed_inputs_not_covered_by_frozen_edges',
            **(dict(binding=bound, compiler_ref=compiler,
                **(dict(client_binding=client_binding) if client_binding is not None else {})) if complete else {})))
    return result


def validate_input_coverage(root, job, rows, published, *, source=None):
    """Validate sealed refs/relations only; never recapture or upgrade coverage."""
    from . import execution_authority as authority, execution_inputs, qualification as q
    root = Path(source.original_root) if source is not None else Path(root).resolve()
    def read(ref):
        return q._read(root, ref, source=source)
    if not isinstance(published, list) or len(published) != len(rows):
        raise q.IntegrityError('Sealed managed input coverage omitted a call')
    verified_compilers = set()
    for row, value in zip(rows, published):
        if source is None:_validate_proof(root, row['accepted'])
        else:
            if row['accepted'].get('retention') != 'archived' or row['accepted'].get('snapshot') != 'host-history/objects/' + row['accepted']['sha256']:
                raise q.IntegrityError('Sealed managed accepted original is not retained')
            q._check(dict(row['accepted'], path=row['accepted']['snapshot']), source=source)
        if (value.get('call_id') != row['call_id']
                or value.get('accepted_ref') != q._ref(root, root / row['accepted']['snapshot'], source=source)
                or type(value.get('complete')) is not bool):
            raise q.IntegrityError('Sealed managed input coverage differs from accepted original')
        if not value['complete']:
            if set(value) != {'call_id','accepted_ref','complete','missing_reason'} or value['missing_reason'] != 'managed_inputs_not_covered_by_frozen_edges':
                raise q.IntegrityError('Unsupported managed input coverage changed')
            continue
        if row['status'] not in {'known_success','known_failed'}:
            raise q.IntegrityError('Nonterminal or not-submitted inputs have no complete coverage')
        accepted = read(dict(path=row['accepted']['snapshot'], sha256=row['accepted']['sha256'], size_bytes=row['accepted']['size_bytes']))
        if 'input_sources' in value:
            keys = {'call_id','accepted_ref','complete','missing_reason','input_sources'}
            declared = execution_inputs.managed_source_contributions(root, job, accepted, source=source)
            if accepted.get('execution_authority') is not None:
                keys.add('binding')
                authority.validate_accepted_input_subject(root, job, accepted, value.get('binding'), source=source)
                if declared is not None:
                    _check_source_subject(declared, value['binding'])
            if (set(value) != keys
                    or value['missing_reason'] is not None or accepted.get('input_sources') is None
                    or value['input_sources'] != accepted['input_sources']['contract']['sha256']
                    or not (declared or {}).get('complete')):
                raise q.IntegrityError('Sealed managed input contract or view changed')
            continue
        keys = {'call_id','accepted_ref','complete','missing_reason','binding','compiler_ref'}
        if 'client_binding' in value:
            keys.add('client_binding')
        if set(value) != keys or value['missing_reason'] is not None:
            raise q.IntegrityError('Sealed managed input coverage shape changed')
        authority.validate_accepted_input_subject(root, job, accepted, value['binding'], source=source)
        subject = value['binding']['subject']; seal = read(subject['producer_seal'])
        compiled = dict(material=subject['material'], compiler=value['compiler_ref'],
            **(dict(client_binding=value['client_binding']) if 'client_binding' in value else {}))
        if [item for item in seal.get('compiled_implementations', []) if item['material'] == subject['material']] != [compiled]:
            raise q.IntegrityError('Sealed managed compiler no longer names its exact payload')
        # The prior coverage fact is immutable. Recheck its compiler originals,
        # not qualification/coverage generation or the current operator key.
        key = json.dumps(compiled, sort_keys=True)
        if key not in verified_compilers:
            execution_inputs.verify_compilation(root, value['compiler_ref'], material=subject['material'],
                                               client_binding=value.get('client_binding'), source=source)
            verified_compilers.add(key)


def _capture(root, job, payload, materials):
    """Snapshot mechanical adapter facts once, before the scientific seal."""
    config = c.settings(root, job).get('history_observer')
    if config is None:
        config=_managed_config(job,root)
        if config is None:return None
        raw_sha=c.digest(c.regular(execution.raw(root,job['step'])))
        path=root/'host-history/observations'/f'{job["step"]:03d}.json'
        binding=_observer_binding(raw_sha,config)
        if path.exists():
            if c.read(c.regular(path)).get('binding')!=binding:
                raise ObserverIntegrityError('Retained managed snapshot binding changed')
        else:
            rows=managed_calls(root,job)
            from . import generic_results
            diagnostics=[]
            snapshot=dict(binding=binding,candidates=generic_results.candidates(rows,diagnostics=diagnostics),current_candidate=None,actual_submission=None,contexts=[],managed_calls=rows,
                coverage=dict(status='partial',note='Registered call originals and explicitly declared candidates only; scientific qualification is unobserved.'))
            if diagnostics:snapshot['candidate_diagnostics']=diagnostics
            c.write(path,snapshot)
        return dict(path=path.relative_to(root).as_posix(),sha256=c.digest(path))
    adapter = c.regular(Path(config['adapter']))
    raw = execution.raw(root, job['step'])
    source_sha = c.digest(c.regular(raw))
    path = root / 'host-history/observations' / f'{job["step"]:03d}.json'
    binding = _observer_binding(source_sha, config)
    if path.exists():
        stored = c.read(c.regular(path))
        if stored.get('binding') != binding:
            raise ObserverIntegrityError('Retained observer snapshot binding changed')
        return dict(path=path.relative_to(root).as_posix(), sha256=c.digest(path))
    known = set();known_contexts = set();prior_contexts = []
    from . import originals
    for receipt in job.get('history', {}).get('nodes', []):
        prior_source = originals.node(root, receipt['step'])
        old_seal = c.read(originals.source_record(prior_source, 'result-identity.json'))
        if old_seal.get('observations'):
            prior = read_observation(root, originals.source_job(prior_source), old_seal)
            known.update(row['id'] for row in prior['candidates'])
            known_contexts.update(row['call_id'] for row in prior.get('contexts', []))
            prior_contexts.extend(prior.get('contexts', []))
    request = dict(root=str(root), step=job['step'], config=config['config'], materials=materials,
                   known_candidates=sorted(known),
                   **(dict(known_contexts=sorted(known_contexts)) if config.get('contract_version') == 2 else {}))
    if config.get('source_contract') is not None:
        from . import execution_inputs
        request['input_sources'] = dict(contract=execution_inputs.SOURCE_CONTRACT,
            available=execution_inputs.declared_sources(root, job))
    value = read_adapter(config, request)
    expected = {'candidates','current_candidate','actual_submission','coverage'}
    if config.get('contract_version') == 2:expected.add('contexts')
    if config.get('source_contract') is not None:expected.add('input_sources')
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Observer must return candidates and actual_submission separately')
    if not isinstance(value['candidates'], list):
        raise ValueError('Observer candidate list required')
    coverage = value['coverage']
    if not isinstance(coverage, dict) or set(coverage) != {'status', 'note'} or coverage['status'] not in ('complete', 'partial', 'unavailable') or not isinstance(coverage['note'], str):
        raise ValueError('Observer coverage requires an explicit status')
    proof_cache = {}
    def proof(name):
        if not isinstance(name, str):
            raise ValueError('Observer original path is not a string')
        if name not in proof_cache:
            item = _proof(root, name)
            proof_cache[name] = item
        return proof_cache[name]
    candidates, seen, diagnostics = [], set(), []
    receipts = job.get('history', {}).get('nodes', [])
    repeated={item['id'] for item in value['candidates'] if isinstance(item,dict) and isinstance(item.get('id'),str)
        and sum(isinstance(other,dict) and other.get('id')==item['id'] for other in value['candidates'])>1}
    for index,item in enumerate(value['candidates']):
        if (not isinstance(item,dict) or not isinstance(item.get('id'),str) or not ID.fullmatch(item['id'])
                or item['id'] in repeated or item['id'] in known):
            diagnostics.append(dict(index=index,code='candidate_identity_unresolved'));continue
        if (not isinstance(item.get('status'),str) or item['status'] not in ('ok','failed','unknown')
                or not isinstance(item.get('evidence'),list) or not item['evidence']
                or any(not isinstance(name,str) for name in item['evidence'])):
            diagnostics.append(dict(index=index,code='candidate_original_unavailable'));continue
        seen.add(item['id'])
        implementation = item.get('implementation')
        if implementation is not None:
            try:resolve(root, implementation, receipts, current=dict(step=job['step'], materials=materials))
            except (ValueError,TypeError) as error:
                if str(error)=='Historical node seal changed':
                    raise ObserverIntegrityError(str(error)) from error
                seen.remove(item['id']);diagnostics.append(dict(index=index,code='candidate_implementation_unresolved'));continue
        try:evidence=[proof(name) for name in item['evidence']]
        except ObserverIntegrityError:raise
        except (OSError,ValueError):
            seen.remove(item['id']);diagnostics.append(dict(index=index,code='candidate_original_unavailable'));continue
        candidates.append({key:item.get(key) for key in ('id','label','implementation','evaluation_key','metric','direction','status')}
            | dict(evidence=evidence))
    current = value['current_candidate']
    if current is not None:
        if (not isinstance(current, dict) or set(current) != {'candidate_id', 'evidence'}
                or not isinstance(current['candidate_id'],str)
                or current['candidate_id'] not in seen | known
                or not isinstance(current['evidence'], list) or not current['evidence']):
            diagnostics.append(dict(code='current_candidate_unresolved'));current=None
        else:current = dict(candidate_id=current['candidate_id'], evidence=[proof(name) for name in current['evidence']])
    actual = value['actual_submission']
    if actual is not None:
        if (not isinstance(actual, dict) or set(actual) != {'candidate_id', 'artifact', 'evidence'}
                or actual['candidate_id'] is not None and not isinstance(actual['candidate_id'],str)
                or (actual['candidate_id'] is not None and actual['candidate_id'] not in seen | known)
                or not isinstance(actual['evidence'], list) or not actual['evidence']):
            diagnostics.append(dict(code='actual_submission_unresolved'));actual=None
        else:actual = dict(candidate_id=actual['candidate_id'], artifact=proof(actual['artifact']),
                          evidence=[proof(name) for name in actual['evidence']])
    contexts = None
    if config.get('contract_version') == 2:
        from . import lineage
        contexts = lineage.contexts(root, job, value['contexts'], proof, prior_contexts)
        if any(row['status']=='unknown' for row in contexts):
            coverage = dict(status='partial',note='Original execution contexts include unknown outcomes.')
    snapshot = dict(binding=binding, candidates=candidates, current_candidate=current, actual_submission=actual, coverage=coverage,
                    **(dict(contexts=contexts) if contexts is not None else {}))
    if diagnostics:snapshot['candidate_diagnostics']=diagnostics
    if config.get('source_contract') is not None:
        from . import qualification as q
        snapshot['input_sources'] = dict(contract=config['source_contract'],
            job=q._ref(root, root / 'jobs' / f'{job["step"]:03d}.json'), raw=q._ref(root, raw), view=value['input_sources'])
        observer_input_sources(root, job, snapshot)
    if _managed_config(job,root):
        from . import generic_results
        snapshot['managed_calls']=managed_calls(root,job)
        diagnostics=[]
        for candidate in generic_results.candidates(snapshot['managed_calls'],diagnostics=diagnostics):
            if candidate['id'] in known | seen:raise ObserverIntegrityError('Managed candidate identity collides with observer facts')
            snapshot['candidates'].append(candidate)
        if diagnostics:snapshot.setdefault('candidate_diagnostics',[]).extend(diagnostics)
    if snapshot.get('candidate_diagnostics'):
        snapshot['coverage']=dict(status='partial',note='Some observer items have no verified binding; retained valid facts remain available.')
    c.write(path, snapshot)
    return dict(path=path.relative_to(root).as_posix(), sha256=c.digest(path))


def capture(root, job, payload, materials):
    """Optional adapter failures never discard original science."""
    if job.get('formal_review'):return None
    from .protocol import require_current
    require_current(root,job)
    config = c.settings(root, job).get('history_observer')
    if config is None:
        return _capture(root,job,payload,materials) if _managed_config(job,root) else None
    adapter = c.regular(Path(config['adapter']))
    path = root / 'host-history/observations' / f'{job["step"]:03d}.json'
    if path.exists():
        return _capture(root, job, payload, materials)  # Never repair an uncertain snapshot.
    try:
        return _capture(root, job, payload, materials)
    except ObserverIntegrityError:
        raise
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        code = ('timeout' if isinstance(error, subprocess.TimeoutExpired) else
                'malformed' if isinstance(error, json.JSONDecodeError) else
                'capacity' if 'capacity' in str(error) or 'bound' in str(error) else
                'adapter_io' if isinstance(error, OSError) else 'invalid_output')
        raw = execution.raw(root, job['step'])
        value = dict(binding=_observer_binding(c.digest(c.regular(raw)),config),
                     candidates=[], current_candidate=None, actual_submission=None,
                     **(dict(contexts=[]) if config.get('contract_version') == 2 else {}),
                     coverage=dict(status='unavailable', note='Observer returned no verified facts; original science is retained.'),
                     diagnostic=dict(failure_type=type(error).__name__, error_code=code))
        if _managed_config(job,root):
            from . import generic_results
            value['managed_calls']=managed_calls(root,job)
            diagnostics=[]
            value['candidates']=generic_results.candidates(value['managed_calls'],diagnostics=diagnostics)
            if diagnostics:value['candidate_diagnostics']=diagnostics
        c.write(path, value)
        return dict(path=path.relative_to(root).as_posix(), sha256=c.digest(path))


def _source(root, step, *, resolve_choices=True):
    from . import originals
    if originals.inherited(root, step):
        source = originals.node(root, step)
        c.validate_original(source)
        job = originals.source_job(source)
        seal_path = originals.source_record(source, 'result-identity.json')
        seal = c.read(seal_path)
        from . import candidate_refs
        payload = c.read(originals.source_raw(source))
        if resolve_choices:
            payload = candidate_refs.control_payload(job, payload, root=root, source=source)
            mapping = candidate_refs.source_input_mapping(source, job)
            if job['role'] == 'W' and seal.get('observations'):
                observed = c.read(originals.resolve_ref(source, seal['observations']))
                mapping.update({row['id']: candidate_refs.origin_candidate(source.origin_identity, row['id'])
                    for row in observed['candidates']})
            payload = candidate_refs.public_science_fields(payload, mapping)
        return candidate_refs.source_job_view(source, job), payload, seal, c.digest(seal_path)
    record = c.host_record(c.workspace(root, step))
    seal_path = c.regular(record / 'result-identity.json')
    seal = c.read(seal_path)
    raw = c.regular(execution.raw(root, step))
    job_path = c.regular(root / 'jobs' / f'{step:03d}.json')
    if (c.digest(raw) != seal['raw_sha256'] or c.digest(job_path) != seal['job_sha256']
            or c.digest(c.regular(record / 'result.json')) != seal['sha256']):
        raise ValueError('Historical scientific source changed')
    job = c.read(job_path)
    if job.get('handoff_protocol', 1) >= 3:
        validate_observation(root, job, seal)
    from . import candidate_refs
    payload=c.read(raw)
    return job, candidate_refs.control_payload(job,payload,root=root) if resolve_choices else payload, seal, c.digest(seal_path)


def direct_sources(root, job):
    """Verify frozen input ownership, without asserting scientific support."""
    from . import budget
    sources, seen = [], set()
    for ref in job['direct_refs']:
        match = re.fullmatch(r'inputs/parents/([0-9]{3,})/conclusion\.json', ref['path'])
        if match is None:
            raise ValueError('Direct scientific input has no exact node identity')
        step = int(match[1])
        if step in seen or match[1] != f'{step:03d}' or not 0 < step < job['step']:
            raise ValueError('Direct scientific input identity is ambiguous')
        seen.add(step)
        source, payload, seal, _ = _source(root, step)
        record = c.host_record(c.workspace(root, step))
        from . import originals
        original = originals.node(root, step)
        chooser = originals.node(root, job['step']) if originals.inherited(root, job['step']) else None
        input_path = (originals.resolve_ref(chooser, dict(ref, path=f'project/nodes/{job["step"]:03d}/' + ref['path']))
            if chooser is not None else c.workspace(root, job['step']) / ref['path'])
        outcome = c.read(originals.source_record(original, 'native-outcome.json'))
        if (c.digest(c.regular(input_path)) != ref['sha256']
                or next((row['sha256'] for row in job.get('direct_originals',job['direct_refs']) if row['path']==ref['path']),None) != seal['raw_sha256']
                or c.read(originals.source_record(original, 'native-intent.json'))['task_sha256'] != seal['job_sha256']
                or type(outcome.get('returncode')) is not int or outcome['returncode'] != 0 or outcome.get('stop_reason')
                or outcome.get('usage', {}).get('unknown_steps') != [] and not budget.native_completion_known(outcome, originals.source_settings(original))
                or outcome.get('raw_sha256') != seal['raw_sha256']
                or c.read(originals.source_record(original, 'result.json')) != c.result_projection(source, payload)):
            raise ValueError('Direct scientific original or terminal identity changed')
        sources.append((source, payload, seal))
    return sources


def implementation_options(root, step):
    job, _, seal, _ = _source(root, step)
    return [dict(id=f'{job["role"]}{step:03d}-I{index:03d}',
                 label=Path(row['path']).name.encode()[:160].decode('utf-8', errors='ignore'))
            for index, row in enumerate(sorted(seal['materials'], key=lambda row: row['path']), 1)]


def resolve_implementation(root, object_id, receipts):
    if not isinstance(object_id,str):
        raise UnresolvedImplementation('Implementation selection requires an exact scientific object reference')
    match = re.fullmatch(r'([HWAE])([0-9]{3,})-I([0-9]{3,})', object_id)
    if match is None:
        raise UnresolvedImplementation('Implementation selection requires a listed scientific object ID')
    step, index = int(match[2]), int(match[3])
    receipt = next((row for row in receipts if row['step'] == step), None)
    job, _, seal, seal_sha = _source(root, step) if receipt else (None, None, None, None)
    if receipt is not None and seal_sha != receipt['seal_sha256']:
        raise ValueError('Historical node seal changed')
    if (receipt is None or job['role'] != match[1]
            or object_id != f'{job["role"]}{step:03d}-I{index:03d}' or index < 1
            or index > len(seal['materials'])):
        raise UnresolvedImplementation('Implementation selection is outside its exact frozen source')
    return sorted(seal['materials'], key=lambda row: row['path'])[index - 1]


def candidate_materials(root, job):
    """Resolve existing candidate/artifact bindings without comparing equal bytes.

    A managed candidate names one output in one original call. The corresponding
    material retains that exact call in its logical path, even for equal bytes.
    Observer candidates may already name an exact historical implementation.
    """
    from . import generic_results
    receipts=job.get('history',{}).get('nodes',[]);result=[]
    for receipt in receipts:
        source,_,seal,seal_sha=_source(root,receipt['step'],resolve_choices=False)
        if seal_sha!=receipt['seal_sha256']:raise ObserverIntegrityError('Candidate source seal changed')
        observation=read_observation(root,source,seal)
        if observation is None:continue
        for candidate in observation['candidates']:
            material=None
            if candidate.get('implementation') is not None:
                material=resolve(root,candidate['implementation'],receipts)
            else:
                members=[(call,item) for call in observation.get('managed_calls',[])
                    for item in generic_results.declarations(call)
                    if isinstance(item,dict) and isinstance(item.get('artifact'),str)
                    and generic_results.identity(call,item)==candidate['id']]
                if len(members)!=1:continue
                call,declared=members[0]
                outputs=[item for item in call.get('outputs',[]) if 'artifacts/'+item['name']==declared['artifact']]
                if len(outputs)!=1:continue
                output=outputs[0]
                path=f'inputs/parents/{source["step"]:03d}/artifacts/experiments/{call["call_id"]}/artifacts/{output["name"]}'
                matches=[row for row in seal['materials'] if row['path']==path
                    and all(row[key]==output[key] for key in ('sha256','size_bytes'))]
                if len(matches)!=1:continue
                material=matches[0]
            owner_step=int(Path(material['path']).parts[2])
            owner,_,owner_seal,_=_source(root,owner_step,resolve_choices=False)
            index=next((number for number,row in enumerate(sorted(owner_seal['materials'],key=lambda row:row['path']),1)
                if row==material),None)
            if index is not None:
                result.append(dict(candidate_id=candidate['id'],material=material,
                    implementation_id=f'{owner["role"]}{owner_step:03d}-I{index:03d}'))
    return result


def source_sequence(root, before_step, *, source_cutoff=None, extra_steps=()):
    """Recreate small node projections from originals; caches are not authority."""
    from . import originals
    if (Path(root) / originals.MANIFEST).exists():
        sources = originals.source_sequence(root, before_step,
            source_cutoff=source_cutoff, extra_steps=extra_steps)
        from . import budget
        resume = budget.normal_resume(root, before_step)
        if resume is not None:
            steps = budget.scientific_steps(root, before_step, resume=resume)
            if source_cutoff is not None:
                steps = [*[step for step in steps if step <= source_cutoff], *extra_steps]
            if set(steps) != {source.step for source in sources if source.step in steps}:
                raise ValueError('Resumed scientific original is missing from its frozen fork prefix')
            sources = [source for source in sources if source.step in steps]
        return [_source(root, source.step) for source in sources]
    sources = []
    from . import budget
    resume = budget.normal_resume(root,before_step)
    normal_steps = budget.scientific_steps(root,before_step,resume=resume) if resume is not None else list(range(1,before_step))
    if source_cutoff is None:
        steps = normal_steps
        if extra_steps:
            raise ValueError('Ordinary history cannot skip scientific originals')
    else:
        if type(source_cutoff) is not int or not 0 <= source_cutoff < before_step:
            raise ValueError('Invalid frozen scientific source cutoff')
        if (list(extra_steps) != sorted(set(extra_steps))
                or any(type(step) is not int or not source_cutoff < step < before_step for step in extra_steps)):
            raise ValueError('Closeout history requires exact preceding corrective nodes')
        steps = [*[number for number in normal_steps if number <= source_cutoff], *extra_steps]
    for step in steps:
        if source_cutoff is not None:
            c.validate(c.workspace(root, step))
            if step > source_cutoff:
                extra = c.read(root / 'jobs' / f'{step:03d}.json')
                report = extra.get('final_report')
                final_report = (extra.get('final_only') and report is not None and report.get('source_cutoff') == source_cutoff)
                if not final_report and (extra.get('purpose') != 'closeout' or extra.get('source_cutoff') != source_cutoff):
                    raise ValueError('Closeout history cannot include an unrelated stopped node')
        sources.append(_source(root, step))
    return sources


def records_from_sources(root, sources, *, write_cache=True):
    if write_cache:
        from .protocol import require_current
        require_current(root)
    records, current_h = [], None
    for job, payload, seal, seal_sha in sources:
        step = job["step"]
        if job['role'] == 'H':
            current_h = step
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        h_parents = list(dict.fromkeys(record['view']['h_scope'] for record in records if record['step'] in parents and record['view']['h_scope'] is not None))
        ordered = {row['path']: row for row in seal['materials']}
        preferred = [row['path'] for row in seal.get('entry_points', []) if row['path'] in ordered]
        paths = list(dict.fromkeys([*preferred, *ordered]))
        entries = [dict(path=handle(ordered[name])) for name in paths if len(handle(ordered[name])) <= 512][:16]
        limitations = payload['limitations']
        view = dict(step=step, role=job['role'], summary=payload['summary'][:512],
            summary_omitted_chars=max(0, len(payload['summary']) - 512),
            limitations=[value[:256] for value in limitations[:4]],
            limitations_omitted_count=max(0, len(limitations) - 4),
            limitations_omitted_chars=sum(max(0, len(value) - 256) for value in limitations[:4]),
            h_scope=current_h, h_parents=h_parents, implementations=entries,
            implementations_omitted_count=len(paths) - len(entries),
            conclusion_pages=[])
        # Source authors retain the complete original scientific fields.
        # Only the index excerpt is shortened; current roles can read text pages.
        text = c.regular(c._execution_raw(root,step)).read_text(encoding='utf-8')
        detail = [dict(step=step, role=job['role'], source='node:%03d/scientific-output' % step,
                       part=index + 1, parts=(len(text) + TEXT_CHARS - 1) // TEXT_CHARS,
                       text=text[start:start + TEXT_CHARS])
                  for index, start in enumerate(range(0, len(text), TEXT_CHARS))]
        for index in range(len(detail)):
            view['conclusion_pages'].append(f'inputs/history/nodes/{step:03d}/{index + 1:04d}.json')
        observation = None
        if seal.get('observations'):
            observation = read_observation(root, job, seal)
            if observation['binding']['source_sha256'] != seal['raw_sha256']:
                raise ValueError('Observer snapshot differs from scientific source')
        row = dict(step=step, role=job['role'], seal_sha256=seal_sha, view=view, detail=detail,
                   recommendation=payload.get('candidate_recommendation') if job['role'] == 'A' and payload.get('next_action') != 'audit'
                       and payload.get('candidate_recommendation') in job.get('history',{}).get('candidate_ids',[]) else None,
                   observation=observation)
        if job['role']=='W' and job.get('candidate_nomination_mechanisms'):
            declared=c.scientific_field(job,payload,'candidate_nominations')
            if declared is not None:row['candidate_nominations']=declared
        if job.get('handoff_protocol', 1) >= 9 and payload.get('next_action') == 'audit':
            row['audit_request'] = True
        # Replacing a disposable projection repairs deletion/corruption; originals
        # and sealed observer snapshots above remain independently verified.
        if write_cache:
            c.write(root / 'host-history/nodes' / f'{step:03d}.json', row)
        records.append(row)
    return records


def rebuild(root, before_step, *, source_cutoff=None, extra_steps=(), write_cache=True):
    """Legacy layout, using the same verified source boundary as lineage."""
    return records_from_sources(root, source_sequence(root, before_step, source_cutoff=source_cutoff, extra_steps=extra_steps),
                                write_cache=write_cache)

def candidate_overview(records):
    from . import lineage
    return lineage.candidate_view(records)


def page_writer(work):
    from .protocol import require_current
    require_current(Path(work).parents[2])
    files = []
    def omitted(row):
        identity={key:row[key] for key in ('id','step','role','call_id','source','part','parts') if key in row and isinstance(row[key],(int,str)) and len(str(row[key]).encode())<512}
        return dict(**identity,view_omitted=True,omission_reason='Oversized navigation record; consult retained original sources.')
    def add(path, value):
        data = _bytes(value)
        if len(data) > MAX_PAGE_BYTES:
            value=omitted(value)
            data=_bytes(value)
        destination = work / path
        # _copy_bound checks existing data and all destination directory symlinks.
        with tempfile.TemporaryDirectory() as temporary:
            original = Path(temporary) / 'page.json';original.write_bytes(data)
            c._copy_bound(original, destination, hashlib.sha256(data).hexdigest())
        files.append(dict(path=path, sha256=c.digest(destination), size_bytes=len(data)))
    def paged(rows, row_limit, key, prefix, source=None):
        groups, group = [], []
        # Use the longest possible continuation path and count in packing checks.
        def value(items, next_page, omitted):
            return {key: items, 'next_page': next_page, 'omitted_count': omitted,
                    **(dict(source=source) if source else {})}
        maximum_next = f'{prefix}/{max(1, len(rows)):04d}.json'
        for row in rows:
            tentative = [*group, row]
            if group and (len(tentative) > row_limit or len(_bytes(value(tentative, maximum_next, len(rows)))) > MAX_PAGE_BYTES):
                groups.append(group);group = []
            group.append(row)
            if len(_bytes(value(group, maximum_next, len(rows)))) > MAX_PAGE_BYTES:
                group[-1]=omitted(row)
        if group:
            groups.append(group)
        paths, remaining = [], len(rows)
        for index, items in enumerate(groups):
            path = f'{prefix}/{index + 1:04d}.json';paths.append(path)
            remaining -= len(items)
            next_page = f'{prefix}/{index + 2:04d}.json' if index + 1 < len(groups) else None
            add(path, value(items, next_page, remaining))
        return paths
    return add, paged, files


def bind(root,step,protocol=None,*,source_cutoff=None,extra_steps=()):
    from .protocol import require_current
    require_current(root)
    raise ValueError('Historical page renderer is retired; current tasks use unified lineage')


def validate(root, work, binding):
    from . import execution_recovery
    recoveries=execution_recovery.validate(root,binding.get('execution_recoveries',[]))
    if any(value['source_step'] not in {row['step'] for row in binding['nodes']} for value in recoveries):
        raise ObserverIntegrityError('Execution recovery is outside the frozen scientific prefix')
    for item in binding.get('execution_recovery_materials',[]):
        path=work/item['path']
        if (not item['path'].startswith('inputs/recovered/') or '..' in Path(item['path']).parts
                or c.digest(c.regular(path))!=item['sha256'] or path.stat().st_size!=item['size_bytes']):
            raise ObserverIntegrityError('Bound execution recovery material changed')
    if binding.get('audit_projection_version') == 1:
        from . import candidate_audit
        sources = [_source(root, row['step']) for row in binding['nodes']]
        expected = candidate_audit.collect_refs(root, sources, step=int(work.name), adjacent=binding.get('adjacent_audit'))
        if binding['audit_receipts'] != expected:
            raise ValueError('Frozen audit receipts differ from their sealed prefix anchors')
    for row in binding['nodes']:
        _, _, _, seal_sha = _source(root, row['step'])
        if seal_sha != row['seal_sha256']:
            raise ValueError('Frozen historical node identity changed')
    for row in binding['files']:
        path = work / row['path']
        limit=MAX_PAGE_BYTES
        if row.get('kind')=='scientific_original':
            match=re.fullmatch(r'inputs/history/nodes/([0-9]{3,})/scientific-original\.json',row['path'])
            if (match is None or row['path']!=f'inputs/history/nodes/{int(match[1]):03d}/scientific-original.json'
                    or not any(ref['step']==int(match[1]) for ref in binding['nodes'])):
                raise ValueError('Bound scientific original has no exact history source')
            _,_,seal,_=_source(root,int(match[1]),resolve_choices=False)
            if row['sha256']!=seal['raw_sha256']:
                raise ValueError('Bound scientific original differs from its sealed source')
            limit=None
        if c.digest(c.regular(path, limit=limit)) != row['sha256'] or path.stat().st_size != row['size_bytes']:
            raise ValueError('Bound scientific history page changed')


def validate_recommendation(job, payload, *, root=None):
    recommendation = payload.get('candidate_recommendation')
    if job.get('handoff_protocol', 1) >= 9 and payload.get('next_action') == 'audit':
        return []  # An unavailable audit request preserves science and returns a host diagnostic.
    from . import candidate_refs
    if recommendation is not None and (job['role'] != 'A' or recommendation not in candidate_refs.available_ids(root, job)):
        subject=recommendation if isinstance(recommendation,str) and c.json_field_representable('reference',recommendation) else 'candidate_recommendation'
        return [dict(code='candidate_recommendation_unresolved',subject=subject)]
    return []


def read_observation(root, job, seal):
    """Read the owner's path/hash receipt and verify its exact source binding."""
    root = Path(root).resolve()
    from . import originals
    if originals.inherited(root, job['step']):
        source = originals.node(root, job['step'])
        receipt = seal.get('observations')
        if receipt is None:
            return None
        if receipt['path'] != f'host-history/observations/{job["step"]:03d}.json':
            raise ObserverIntegrityError('Inherited observation escaped its exact producer')
        value = c.read(originals.resolve_ref(source, receipt))
        config = originals.source_settings(source).get('history_observer') or _managed_config(job, root)
        if config is None or value['binding'] != _observer_binding(seal['raw_sha256'], config):
            raise ObserverIntegrityError('Inherited observation lost its exact source binding')
        from . import candidate_refs
        return candidate_refs.source_observation(source, originals.source_job(source), value)
    if job.get('formal_review'):
        if seal.get('observations') is not None:raise ValueError('Formal review has an ordinary observer snapshot')
        return
    receipt = seal.get('observations')
    config = c.settings(root, job).get('history_observer') or _managed_config(job,root)
    if config is None:
        if receipt is not None:
            raise ValueError('Unconfigured observer snapshot in scientific seal')
        return
    if receipt is None:
        raise ValueError('Configured observer snapshot missing from scientific seal')
    path = root / receipt['path']
    expected = root / 'host-history/observations' / f'{job["step"]:03d}.json'
    if path != expected or c.digest(c.regular(path)) != receipt['sha256']:
        raise ObserverIntegrityError('Sealed observer snapshot changed')
    value = c.read(path)
    if value['binding'] != _observer_binding(seal['raw_sha256'], config):
        raise ValueError('Observer snapshot source binding changed')
    return value


def validate_observation(root, job, seal):
    from . import originals
    if originals.inherited(root, job['step']):
        return validate_observation_original(originals.node(root, job['step']), originals.source_job(originals.node(root, job['step'])), seal)
    value = read_observation(root, job, seal)
    if value is None:
        return
    observer_input_sources(root, job, value)
    config = c.settings(root, job).get('history_observer') or _managed_config(job,root)
    if _managed_config(job,root):
        validate_managed(root,job,value['managed_calls'])
    proofs = [proof for row in value['candidates'] for proof in row['evidence']]
    for field in ('current_candidate', 'actual_submission'):
        if value.get(field):
            proofs.extend(value[field]['evidence'])
    if value.get('actual_submission'):
        proofs.append(value['actual_submission']['artifact'])
    if config.get('contract_version') == 2:
        for context in value.get('contexts', []):
            proofs.extend(context['proofs'])
            if context.get('model'):proofs.append(context['model'])
    for proof in proofs:
        _validate_proof(root, proof)


def validate_observation_original(source, job, seal):
    """Validate retained observer contracts without invoking an adapter or writer."""
    from . import originals as o
    policy = o.source_settings(source)
    managed = _managed_config(job)
    if managed is None and 'experiment_mode' in job and not job.get('formal_review'):
        try:
            o.source_record(source, 'experiment-config.json')
        except (FileNotFoundError, ValueError):
            pass
        else:
            managed = dict(adapter_sha256='registered-originals-v1')
    config = policy.get('history_observer') or managed
    receipt = seal.get('observations')
    if job.get('formal_review'):
        if receipt is not None:
            raise ObserverIntegrityError('Formal review has an ordinary observer snapshot')
        return None
    if config is None:
        if receipt is not None:
            raise ObserverIntegrityError('Unconfigured original observer snapshot')
        return None
    if receipt is None or receipt.get('path') != f'host-history/observations/{source.step:03d}.json':
        raise ObserverIntegrityError('Original observer snapshot lost its exact producer')
    value = c.read(o.resolve_ref(source, receipt))
    if value.get('binding') != _observer_binding(seal['raw_sha256'], config):
        raise ObserverIntegrityError('Original observer snapshot binding changed')
    observer_input_sources(source.storage_root, job, value, source=source)
    if managed is not None:
        validate_managed_original(source, job, value.get('managed_calls', []))
    proofs = [proof for row in value['candidates'] for proof in row['evidence']]
    for field in ('current_candidate', 'actual_submission'):
        if value.get(field) is not None:
            proofs.extend(value[field]['evidence'])
    if value.get('actual_submission') is not None:
        proofs.append(value['actual_submission']['artifact'])
    if config.get('contract_version') == 2:
        for row in value.get('contexts', []):
            proofs.extend(row['proofs'])
            if row.get('model') is not None:
                proofs.append(row['model'])
    for proof in proofs:
        if proof.get('retention') == 'archived':
            if proof.get('snapshot') != 'host-history/objects/' + proof['sha256']:
                raise ObserverIntegrityError('Archived original observer proof changed')
            o.resolve_ref(o.source_for_ref(source, proof), dict(proof, path=proof['snapshot']))
    return value


def _validate_proof(root, proof, *, context=None):
    if proof.get('retention') != 'archived':
        return
    expected = 'host-history/objects/' + proof['sha256']
    if proof.get('snapshot')!=expected:raise ObserverIntegrityError('Archived observer original changed')
    from . import experiments
    from . import originals
    path = originals.resolve_root_ref(root, dict(proof, path=expected)) if (Path(root) / originals.MANIFEST).exists() else root / expected
    actual=c.digest(c.regular(path)) if context is None else experiments.proof(path,context=context)['sha256']
    if actual != proof['sha256']:
        raise ObserverIntegrityError('Archived observer original changed')


def refresh(root, before_step):
    """Update disposable projections after every seal, including a terminal A."""
    from .protocol import require_current
    require_current(root)
    latest = c.read(root / 'jobs' / f'{before_step-1:03d}.json') if before_step > 1 else {}
    if latest.get('lineage_projection_version') == 1:
        from . import lineage
        overview = lineage.project(root, before_step)['candidates']
    else:
        overview = candidate_overview(rebuild(root, before_step))
    overview.pop('candidate_index')
    if len(_bytes(overview)) > MAX_PAGE_BYTES:
        overview=dict(coverage=dict(status='partial',note='Read complete observation originals for coverage.'),view_omitted=True,
            omission_reason='Overview exceeds compact capacity; complete observations and candidate indexes remain retained.')
    c.write(root / 'host-history/overview.json', overview)


def _observer_binding(source_sha, config):
    return dict(source_sha256=source_sha,adapter_sha256=config.get('adapter_sha256'),
                **(dict(contract_version=2) if config.get('contract_version') == 2 else {}),
                **(dict(source_contract_sha256=hashlib.sha256(_bytes(config['source_contract'])).hexdigest())
                   if config.get('source_contract') is not None else {}))
