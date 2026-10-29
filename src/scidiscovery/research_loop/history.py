"""Rebuildable scientific history and optional host observations; no attachments."""
from __future__ import annotations

from . import execution

import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from functools import wraps
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


def observer_input_sources(root,job,observation,*,source=None,controls_only=False):
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
        if not controls_only:originals.source_raw(source)
        record = source.native_record
        raw=(Path(observation['binding']['source_ref']['path']) if controls_only else
            logical_root/record/'science-output.json' if record else Path(c._execution_raw(logical_root,job['step'])))
    else:
        logical_root = Path(root)
        raw=(Path(observation['binding']['source_ref']['path']) if controls_only else Path(c._execution_raw(root,job['step'])))
    if not raw.is_relative_to(logical_root):raise q.IntegrityError('Observer raw source escaped its exact original instance')
    raw_ref=(dict(path=raw.relative_to(logical_root).as_posix(),
        size_bytes=observation['binding']['source_ref']['size_bytes']) if controls_only
        else q._ref(root,raw,source=source))
    if (not isinstance(original, dict) or set(original) != {'contract','job','raw','view'}
            or original['contract'] != contract
            or original['job'] != q._ref(root, logical_root / 'jobs' / f'{job["step"]:03d}.json', source=source)
            or q._read(root, original['job'], source=source) != job or original['raw'] != raw_ref
            or dict(path=str(raw),size_bytes=original['raw']['size_bytes']) != observation['binding']['source_ref']):
        raise q.IntegrityError('Observer source view differs from its original task')
    return execution_inputs.validate_source_view(root,job,original['view'],source=source,controls_only=controls_only)


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
        if source_seal_ref(originals.node(root, step)) != receipt['seal_ref']:
            raise ValueError('Historical node seal changed')
        rows = c.read(path)['materials']
    row = next((row for row in rows if handle(row) == target), None)
    if row is None:
        raise ValueError('Historical implementation is not an exact sealed deliverable')
    return c._public_materials(root,[row])[0]


def _observer_source_stat(info):
    # Same byte-generation fields as experiments.proof; the retained object's
    # file_stat belongs to a different inode and cannot identify its live source.
    return [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns]


def _proof(root, source, *, context=None, source_kind='mutable', previous=None):
    from .protocol import require_current
    require_current(root)
    saved=source if isinstance(source,dict) else None
    relative=saved['path'] if saved is not None else source
    if not isinstance(relative,str):raise ValueError('Observer evidence path is not a string')
    snapshot=saved.get('snapshot',relative) if saved is not None else relative
    path = root / snapshot
    if (not isinstance(snapshot,str) or '..' in Path(relative).parts
            or '..' in Path(snapshot).parts or path.resolve() != path):
        raise ObserverIntegrityError('Observer evidence must be an exact instance original')
    if Path(snapshot).is_absolute() and (saved is None or 'file_stat' not in saved):
        raise ObserverIntegrityError('External observer evidence requires its saved identity')
    c.regular(path)
    source_stat = _observer_source_stat(path.stat())
    size = source_stat[2]
    from . import experiments
    if saved is not None:
        actual=experiments.proof(path,context=context)
        if any(actual[key]!=saved[key] for key in ('size_bytes','file_stat') if key in saved):
            raise ObserverIntegrityError('Saved observer original changed')
        return dict(path=relative,**{key:saved[key] for key in ('size_bytes','file_stat') if key in saved},
            retention='archived',snapshot=snapshot)
    if source_kind=='managed_frozen':
        if path.stat().st_mode & 0o777 != 0o400:
            raise ObserverIntegrityError('Managed original has not been sealed by its stopped writer')
        original=experiments.proof(path,context=context)
        return dict(path=relative,**{key:original[key] for key in ('size_bytes','file_stat')},
            retention='archived',snapshot=relative)
    if previous is not None:
        owner,prior = previous
        if prior.get('source_file_stat')==source_stat and prior.get('size_bytes')==size:
            from . import originals
            try:
                retained=originals.resolve_ref(owner,dict(prior,path=_snapshot(prior)),context=context)
            except (OSError,ValueError) as error:
                raise ObserverIntegrityError('Archived observer original changed') from error
            original=experiments.proof(retained,context=context) if owner.inherited else prior
            if _observer_source_stat(path.stat())!=source_stat:
                raise ObserverIntegrityError('Observer evidence changed during retention')
            return dict(path=relative,**{key:original[key] for key in ('size_bytes','file_stat')},
                retention='archived',source_file_stat=source_stat,
                snapshot=retained.relative_to(root).as_posix() if retained.is_relative_to(root) else str(retained))
    from . import shared_originals
    retained=shared_originals.retain(root,path,size_bytes=size,source_kind='mutable',context=context)
    original=experiments.proof(retained,context=context)
    if _observer_source_stat(path.stat())!=source_stat:
        raise ObserverIntegrityError('Observer evidence changed during retention')
    return dict(path=relative,**{key:original[key] for key in ('size_bytes','file_stat')},
        retention='archived',snapshot=retained.relative_to(root).as_posix(),source_file_stat=source_stat)


def source_control_ref(source, relative):
    from . import originals
    originals.original_ref(source,relative)
    return dict(path=str(Path(source.original_root)/relative))


def source_seal_ref(source):
    return source_control_ref(source,f'native-evidence/{source.step:03d}/result-identity.json')


def _same_original(root, original, retained):
    return (Path(root) / original['path'] == Path(root) / retained['path']
        and original.get('size_bytes') == retained.get('size_bytes')
        and ('file_stat' not in original or 'file_stat' not in retained
            or original['file_stat']==retained['file_stat']))


def _retained_original(root, row, proof):
    candidates=[row[key] for key in ('accepted','completed','terminal','observation','retirement') if row.get(key) is not None]
    candidates.extend(row.get('originals', [])); candidates.extend(row.get('outputs', []))
    matches={item['snapshot']: item for item in candidates if _same_original(root, proof, item)}
    if len(matches)!=1:
        raise ObserverIntegrityError('Original has no exact retained path binding')
    return next(iter(matches.values()))


def _snapshot(proof):
    path=Path(proof.get('snapshot', ''))
    if (proof.get('retention')!='archived' or (path.is_absolute() and 'file_stat' not in proof) or '..' in path.parts
            or path.as_posix()!=proof['snapshot'] or not path.parts):
        raise ObserverIntegrityError('Archived original has no exact snapshot path')
    return path.as_posix()


def _job_storage(function):
    @wraps(function)
    def bound(root,job,*args,**kwargs):
        from . import storage_policy as storage, shared_originals
        with storage.bind(root,c.settings(root,job).get('storage_policy')):
            return function(root,job,*args,**kwargs)
    return bound


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



def _managed_config(job,root=None,*,controls_only=False):
    if job.get('formal_review'):return None
    record=None
    if root is not None:
        if controls_only:
            seal_path=Path(root)/'native-evidence'/f'{job["step"]:03d}'/'result-identity.json'
            record=(Path(c.read(c.regular(seal_path))['raw_ref']['path']).parent
                if seal_path.exists() else Path(root)/'native-evidence'/f'{job["step"]:03d}')
            origin=Path(root)/'native-evidence'/f'{job["step"]:03d}'
            if not record.is_relative_to(origin):raise ObserverIntegrityError('Managed source has no exact sealed native attempt')
        else:record=execution.record(root,job['step'])
    configured = ((job.get('search') or {}).get('breadth') or 'experiment_mode' in job and
        (job['experiment_mode']=='local' and job['role']=='W'
         or record is not None and (record/'experiment-config.json').exists()))
    return dict(adapter_ref='registered-originals-v1') if configured else None


def _managed_proof(root,path,original,*,context=None,verify=True):
    path=Path(path)
    if Path(original['path'])==path:
        relative=path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        saved=dict(path=relative,**{key:original[key] for key in ('size_bytes','file_stat') if key in original})
        if not verify:return dict(saved,retention='archived',snapshot=relative)
        return _proof(root,saved,context=context,source_kind='managed_frozen')
    proof=_proof(root,Path(path).relative_to(root).as_posix(),context=context,source_kind='managed_frozen')
    if any(proof[key]!=original[key] for key in ('size_bytes',)):
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


def managed_status(root, row, *, context=None, verify_files=True):
    """One immutable terminal authority shared with Worker delivery and history."""
    from . import experiments
    from . import originals
    step = (row.get('scope') or {}).get('step')
    if type(step) is int and step > 0 and originals.inherited(root, step):
        source = originals.node(root, step)
        validate_managed_original(source,originals.source_job(source),[row],controls_only=not verify_files)
        return row['status']
    for name in ('accepted','completed','terminal','observation','retirement'):
        if row.get(name) is not None:_validate_proof(root,row[name],context=context)
    if row.get('retirement') is not None:
        if any(row.get(key) is not None for key in ('completed','terminal','observation')) or 'execution' in row or row.get('outputs'):
            raise ObserverIntegrityError('Non-submission cannot contain executed terminal evidence')
        accepted=c.read(root/row['accepted']['snapshot'])
        value=c.read(root/row['retirement']['snapshot'])
        if not _same_original(root, value.get('accepted', {}), row['accepted']):
            raise ObserverIntegrityError('Retirement differs from the archived accepted call')
        try:
            experiments.verify_retirement(value,lambda item:root/_retained_original(root,row,item)['snapshot'],
                interface=_execution_interface(root,accepted),context=context,verify_files=verify_files)
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise ObserverIntegrityError('Archived permanent retirement proof changed') from error
        return 'not_submitted'
    if row.get('completed') is None:return 'unknown'
    accepted=c.read(root/row['accepted']['snapshot']);completed=c.read(root/row['completed']['snapshot'])
    interface=_execution_interface(root,accepted)
    observation=row.get('observation')
    if observation is not None:
        value=c.read(root/observation['snapshot'])
        if (not _same_original(root, value['accepted'], row['accepted'])
                or not _same_original(root, value['completed'], row['completed'])):
            raise ObserverIntegrityError('Managed observation differs from archived call originals')
        try:
            return experiments.verify_observation(value,lambda proof:
                root/_retained_original(root,row,proof)['snapshot'],interface=interface,context=context,
                verify_files=verify_files and not ('code_version' in accepted and experiments._local_original_observation(
                    dict(execution_interface=interface),value)))
        except (ValueError,OSError,KeyError,TypeError) as error:
            raise ObserverIntegrityError('Managed execution observation original changed') from error
    if interface.get('observation_version') is not None:return 'unknown'
    # Compatibility is restricted to the existing local receipt contract.
    if not ('local_observation_version' in accepted or 'receipt_transport' in accepted):return 'unknown'
    terminal=c.read(root/row['terminal']['snapshot']) if row.get('terminal') is not None else None
    return experiments.local_terminal_status(accepted,completed,terminal)


def _managed_outputs(interface, completed, observed):
    if observed is not None and 'scientific_outputs' in observed:return observed['scientific_outputs']
    if observed is not None and interface.get('terminal_evidence')!='local_process':
        return observed['outputs']
    return completed.get('outputs',[]) if 'result' in completed else observed['outputs'] if observed is not None else []


@_job_storage
def scientific_outputs(root, job, call, *, verify_files=True):
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
            outputs=[dict(item, path=str(originals.resolve_ref(source, dict(item, path=_retained_original(source.original_root,call,item)['snapshot']))))
                for item in observed['outputs']],
            **(dict(scientific_outputs=[dict(item,path=str(originals.resolve_ref(source,dict(item,path=_retained_original(source.original_root,call,item)['snapshot']))))
                for item in observed['scientific_outputs']]) if 'scientific_outputs' in observed else {}))
        result = []
        for descriptor in experiments.scientific_outputs(config, archived):
            matches = [row for row in call['outputs'] if row['name'] == descriptor.name
                and row['size_bytes'] == descriptor.size_bytes
                and archived_ref(row) == Path(descriptor.local_path)]
            if len(matches) != 1:
                raise ObserverIntegrityError('Inherited scientific output lost its exact archived descriptor')
            result.append(dict(matches[0], path=matches[0]['snapshot'], name=descriptor.name,
                origin_identity=source.origin_identity))
        return result
    managed_status(root,call,verify_files=verify_files)  # Control originals remain mandatory.
    if not verify_files:
        return [dict(row,path=row['snapshot']) for row in call.get('outputs',[])]
    if call.get('observation') is None:
        return call.get('outputs',[])
    accepted=c.read(c.regular(root/call['accepted']['snapshot']))
    attempt=(accepted.get('native_attempt') or {}).get('number',0)
    config=c.read(c.regular(execution.record(root,job['step'],attempt)/'experiment-config.json'))
    if config['execution_interface']!=_execution_interface(root,accepted):
        raise ObserverIntegrityError('Scientific output interface changed')
    observed=c.read(c.regular(root/call['observation']['snapshot']))
    archived=dict(observed,completed=dict(observed['completed'],path=str(root/call['completed']['snapshot'])),
        outputs=[dict(item,path=str(root/_retained_original(root,call,item)['snapshot']))
        for item in observed['outputs']],
        **(dict(scientific_outputs=[dict(item,path=str(root/_retained_original(root,call,item)['snapshot']))
            for item in observed['scientific_outputs']]) if 'scientific_outputs' in observed else {}))
    result=[]
    for descriptor in experiments.scientific_outputs(config,archived):
        matches=[item for item in call.get('outputs',[]) if item['name']==descriptor.name
            and root/item['snapshot']==Path(descriptor.local_path)]
        if len(matches)!=1:raise ObserverIntegrityError('Selected scientific output lost its saved original')
        _validate_proof(root,matches[0])
        proof=matches[0]
        if proof['size_bytes']!=descriptor.size_bytes:
            raise ObserverIntegrityError('Selected scientific output original changed')
        result.append(dict(proof,path=proof['snapshot'],name=descriptor.name))
    return result


def _observed_original_refs(accepted, observed):
    originals=[observed[key] for key in ('accepted','completed','submission')]+observed['proofs']
    if accepted.get('execution_authority') is not None:originals.append(accepted['execution_authority'])
    if isinstance(accepted.get('tcad'),dict):originals.append(accepted['script'])
    if 'native_terminal' in observed:originals.append(observed['native_terminal'])
    originals.extend(observed.get('scientific_outputs',[]))
    if observed.get('scientific_source') is not None:originals.append(observed['scientific_source'])
    # Retain every collected descriptor even when result outputs use scientific names.
    originals.extend(observed['outputs'])
    return originals


@_job_storage
def managed_calls(root, job, *, context=None, call_ids=None):
    """Capture small common receipts, independent of domain candidates or observers."""
    from . import experiments, breadth, generic_results
    from .experiment_client import record_directory
    records=experiments.Records(dict(record_dir=str(record_directory(root,job['step']))))
    from .protocol import stage_scope
    scope=stage_scope(root,job);result=[]
    if call_ids is not None and (not isinstance(call_ids,(list,tuple))
            or any(not isinstance(item,str) for item in call_ids) or len(set(call_ids))!=len(call_ids)):
        raise ObserverIntegrityError('Managed selection requires distinct exact original call IDs')
    directories=records.calls() if call_ids is None else [records.directory(item) for item in call_ids]
    for directory in directories:
        accepted_path=directory/'accepted.json'
        accepted=c.read(c.regular(accepted_path))
        if accepted.get('scope')!=scope or accepted.get('call_id')!=directory.name:
            raise ObserverIntegrityError('Managed original escaped its stage owner')
        binding=breadth.check_binding((job.get('search') or {}).get('breadth'),accepted.get('mechanism_binding'),scope)
        completion=directory/'completed.json';terminal_path=directory/'terminal.json';state='unknown';terminal=None;value={}
        if completion.exists():
            value=c.read(c.regular(completion))
            if value.get('call_id')!=directory.name or value.get('scope')!=scope or value.get('mechanism_binding')!=binding:
                raise ObserverIntegrityError('Managed completion changed accepted mechanism')
            if terminal_path.exists():
                terminal=_proof(root,terminal_path.relative_to(root).as_posix(),context=context,source_kind='managed_frozen')
        observed_path=directory/'execution-observation.json';observed=None;extra=[]
        if observed_path.exists():
            observed=c.read(c.regular(observed_path))
            extra=_observed_original_refs(accepted,observed)
        interface=_execution_interface(root,accepted)
        direct=('code_version' in accepted and observed is not None
            and experiments._local_original_observation(dict(execution_interface=interface),observed))
        if direct:
            # Preserve the producer's saved identities. Historical output reads
            # need the call's controls, not an expired code export or old data.
            experiments.verify_observation(observed,lambda item:Path(item['path']),
                interface=interface,context=context,verify_files=False)
            terminal=_managed_proof(root,terminal_path,observed['native_terminal'],context=context)
        retired_path=directory/'submission-retirement.json'
        if retired_path.exists() or retired_path.is_symlink():
            value=experiments.retirement(dict(record_dir=str(records.root),scope=scope,execution_interface=interface,breadth=(job.get('search') or {}).get('breadth')),directory.name)
            originals=experiments.retirement_originals(value,accepted)
            row=dict(call_id=directory.name,scope=scope,mechanism_binding=binding,status='not_submitted',
                result_protocol=generic_results.protocol(interface),accepted=_proof(root,accepted_path.relative_to(root).as_posix(),context=context,source_kind='managed_frozen'),
                completed=None,terminal=None,retirement=_proof(root,retired_path.relative_to(root).as_posix(),context=context,source_kind='managed_frozen'),
                originals=[_managed_proof(root,item['path'],item,context=context) for item in originals])
            row['status']=managed_status(root,row,context=context);result.append(row)
            continue
        outputs=_managed_outputs(interface,value,observed)
        row=dict(call_id=directory.name,scope=scope,mechanism_binding=binding,status=state,
            result_protocol=generic_results.protocol(interface),
            accepted=(_managed_proof(root,accepted_path,observed['accepted'],context=context) if direct else
                _proof(root,accepted_path.relative_to(root).as_posix(),context=context,source_kind='managed_frozen')),
            completed=(_managed_proof(root,completion,observed['completed'],context=context) if direct else
                _proof(root,completion.relative_to(root).as_posix(),context=context,source_kind='managed_frozen')) if completion.exists() else None,terminal=terminal,
            **(dict(observation=_proof(root,observed_path.relative_to(root).as_posix(),context=context,source_kind='managed_frozen')) if observed is not None else {}),
            **(dict(originals=[_managed_proof(root,Path(item['path']) if 'code_version' in accepted else directory/'inputs'/item['name'],
                    item,context=context,verify=not direct) for item in accepted.get('snapshot_files',[])]
                    +[_managed_proof(root,item['path'],item,context=context) for item in (extra if observed is not None else value.get('proofs',[]))],
                outputs=[dict(name=item['name'],**_managed_proof(root,item['path'],item,context=context)) for item in outputs],
                execution=dict(returncode=value.get('returncode'),wall_seconds=value.get('wall_seconds'),result=value.get('result'),
                    **(dict(public_result=observed['public_result']) if observed is not None and 'public_result' in observed else {}),
                    **(dict(native_state=value['native_state']) if 'native_state' in value else {})))
               if 'result' in value or observed is not None else {}))
        row['status']=managed_status(root,row,context=context)
        result.append(row)
    return result


def validate_managed(root, job, rows, *, context=None, verify_files=True):
    from . import originals
    if originals.inherited(root, job['step']):
        return validate_managed_original(originals.node(root,job['step']),job,rows,controls_only=not verify_files)
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
        observed=c.read(root/row['observation']['snapshot']) if row.get('observation') is not None else None
        from . import experiments
        direct=('code_version' in accepted and observed is not None
            and experiments._local_original_observation(dict(execution_interface=accepted['interface']),observed))
        unused=set()
        if not verify_files and observed is not None or direct:
            _,controls=experiments._observation_file_roles(observed,accepted,c.read(root/row['completed']['snapshot']))
            science,_=experiments._observation_file_roles(observed,accepted,c.read(root/row['completed']['snapshot']))
            unused=({item['path'] for item in science}|{item['path'] for key in ('outputs','scientific_outputs')
                for item in observed.get(key,[])}|({observed['scientific_source']['path']}
                if observed.get('scientific_source') is not None else set()))-controls
        for proof in row.get('originals',[]):
            if str(root/proof['path']) not in unused:_validate_proof(root,proof,context=context)
        if verify_files:
            for proof in row.get('outputs',[]):_validate_proof(root,proof,context=context)
        if 'execution' in row:
            completed=c.read(root/row['completed']['snapshot'])
            observed=c.read(root/row['observation']['snapshot']) if row.get('observation') is not None else {}
            if row['execution']!=dict(returncode=completed.get('returncode'),wall_seconds=completed.get('wall_seconds'),result=completed.get('result'),
                    **(dict(public_result=observed['public_result']) if 'public_result' in observed else {}),
                    **(dict(native_state=completed['native_state']) if 'native_state' in completed else {})):
                raise ObserverIntegrityError('Managed results differ from completed original')
            originals=[*accepted.get('snapshot_files',[])]
            observed=c.read(root/row['observation']['snapshot']) if row.get('observation') else None
            if observed is not None:
                originals.extend(_observed_original_refs(accepted,observed))
            else:originals.extend(completed.get('proofs',[]))
            if (len(row['originals'])!=len(originals) or any(any(proof[key]!=original[key] for key in ('size_bytes',))
                    for proof,original in zip(row['originals'],originals))):
                raise ObserverIntegrityError('Managed input or log original changed')
            outputs=_managed_outputs(_execution_interface(root,accepted),completed,observed)
            if len(row['outputs'])!=len(outputs):
                raise ObserverIntegrityError('Managed outputs differ from completed original')
            for proof,original in zip(row['outputs'],outputs):
                if any(proof[key]!=original[key] for key in ('name','size_bytes')):
                    raise ObserverIntegrityError('Managed output changed')
        if row.get('retirement') is not None:
            value=c.read(root/row['retirement']['snapshot'])
            from . import experiments
            originals=experiments.retirement_originals(value,accepted)
            if (len(row['originals'])!=len(originals) or any(any(item[key]!=original[key]
                    for key in ('size_bytes',)) for item,original in zip(row['originals'],originals))):
                raise ObserverIntegrityError('Retirement native originals differ from the frozen receipt')
        if row['status'] not in {'known_success','known_failed','not_submitted','unknown'}:raise ObserverIntegrityError('Managed state differs')
        if row['status']!=managed_status(root,row,context=context,verify_files=verify_files):
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


def validate_managed_original(source,job,rows,*,controls_only=False):
    """Verify retained call bindings/terminals without querying a provider."""
    from . import originals, experiments, breadth, generic_results
    job_ref = originals.original_ref(source, f'jobs/{job["step"]:03d}.json')
    scope = dict(step=job['step'], role=job['role'], handoff_protocol=job['handoff_protocol'],
        job_ref=dict(path=str(Path(source.original_root)/job_ref['path'])), execution_key=dict(path=str(Path(source.original_root)/job_ref['path'])),
        mechanism_ref=None if (job.get('search') or {}).get('breadth') is not None else
            ((job.get('search') or {}).get('focus') or {}).get('mechanism_ref'))
    def proof_path(proof):
        if _snapshot(proof) != proof['snapshot']:
            raise ObserverIntegrityError('Inherited managed original has no exact archived bytes')
        return originals.resolve_ref(source, dict(proof, path=proof['snapshot']))
    def native_proof(proof):
        retained=_retained_original(source.original_root,row,proof)
        actual=proof_path(retained)
        path=Path(retained['snapshot'])
        if path.is_absolute():
            if not path.is_relative_to(source.original_root):return actual
            path=path.relative_to(source.original_root)
        entries=source._index.get((originals.identity_key(source.origin_identity),path.as_posix()),())
        if len(entries)==1 and entries[0].get('visibility') in {'host_proof','effect_only'}:
            if entries[0]['size_bytes']>MAX_HISTORY_BYTES:
                raise ObserverIntegrityError('Inherited control exceeds its bounded byte reader')
            # Transported control bytes retain their original typed bindings,
            # while the destination inode is necessarily a new host object.
            return actual.read_bytes()
        return actual
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
        if not controls_only:
            for proof in [*row.get('originals',[]),*row.get('outputs',[])]:proof_path(proof)
        status = 'unknown'
        if row.get('retirement') is not None:
            value = c.read(proof_path(row['retirement']))
            experiments.verify_retirement(value,native_proof,interface=interface,verify_files=not controls_only)
            status = 'not_submitted'
        elif row.get('completed') is not None:
            completed = c.read(proof_path(row['completed']))
            if row.get('observation') is not None:
                value = c.read(proof_path(row['observation']))
                if not _same_original(source.original_root, value['accepted'], row['accepted']) or not _same_original(source.original_root, value['completed'], row['completed']):
                    raise ObserverIntegrityError('Inherited terminal differs from its exact accepted call')
                status = experiments.verify_observation(value,native_proof,interface=interface,verify_files=not controls_only,source=source)
            elif interface.get('observation_version') is None:
                terminal = c.read(proof_path(row['terminal'])) if row.get('terminal') else None
                status = experiments.local_terminal_status(accepted, completed, terminal)
            frozen=c.read(proof_path(row['observation'])) if row.get('observation') is not None else {}
            expected_outputs=_managed_outputs(interface,completed,frozen or None)
            if (len(row.get('outputs',[]))!=len(expected_outputs) or any(
                    any(actual[key]!=expected[key] for key in ('name','size_bytes'))
                    for actual,expected in zip(row.get('outputs',[]),expected_outputs))):
                raise ObserverIntegrityError('Inherited scientific output projection changed')
            expected_execution = (dict(returncode=completed.get('returncode'), wall_seconds=completed.get('wall_seconds'),
                    **(dict(public_result=frozen['public_result']) if 'public_result' in frozen else {}),
                    result=completed.get('result'), **(dict(native_state=completed['native_state']) if 'native_state' in completed else {}))
                if 'result' in completed or row.get('observation') is not None else None)
            if row.get('execution') != expected_execution:
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
                    complete=True,missing_reason=None,input_sources=accepted['input_sources']['contract'],
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


def validate_input_coverage(root,job,rows,published,*,source=None,controls_only=False):
    """Validate sealed refs/relations only; never recapture or upgrade coverage."""
    from . import execution_authority as authority, execution_inputs, qualification as q
    root = Path(source.original_root) if source is not None else Path(root).resolve()
    def read(ref):
        return q._read(root, ref, source=source)
    if not isinstance(published, list) or len(published) != len(rows):
        raise q.IntegrityError('Sealed managed input coverage omitted a call')
    def saved_binding(accepted,published_binding):
        # Match the five already-published control fields. This neither checks
        # present admission/signatures nor opens aliases, scripts or packages.
        if (not isinstance(published_binding,dict)
                or set(published_binding)!={'subject','authority','request','decision','allocation'}):
            raise q.IntegrityError('Sealed managed source control binding changed')
        authority_ref=accepted.get('execution_authority')
        if authority_ref is None:raise q.IntegrityError('Sealed managed source lost its original authority control')
        q._check(authority_ref,source=source)
        authority_path=Path(authority_ref['path'])
        authorization=(c.read(q._source_path(source,authority_ref)) if source is not None
            else c.read(c.regular(authority_path)))
        request_ref=authorization['request'];request=read(request_ref)
        allocation_ref=authorization['allocation'];allocation=read(allocation_ref)
        decision_ref=authorization['decision'];decision=read(decision_ref)
        ref_at=lambda path:q._ref(root,path,source=source)
        expected=dict(subject=request['subject'],authority=ref_at(authority_path),
            request=request_ref,decision=decision_ref,allocation=allocation_ref)
        call=accepted['call_id'];attempt=accepted.get('native_attempt') or {};number=attempt.get('number')
        if type(number) is not int or not 0<=number<10000:
            raise q.IntegrityError('Sealed managed source native attempt changed')
        native_record=root/'native-evidence'/f'{job["step"]:03d}'
        if number:native_record=native_record/'continuations'/f'{number:03d}'
        directory=root/'execution-authority/requests'/Path(request_ref['path']).parent.name
        bound=allocation['binding'];subject=request['subject']
        if (published_binding!=expected or authority_path!=directory/'authorization.json'
                or request_ref!=ref_at(directory/'request.json')
                or decision_ref!=ref_at(directory/'decision.json') or decision['request']!=request_ref
                or allocation_ref!=ref_at(root/'execution-authority/calls'/call/'allocation.json')
                or allocation['call_id']!=call or request['call_id']!=call
                or request['job']!=ref_at(root/'jobs'/f'{job["step"]:03d}.json')
                or accepted['scope']['job_ref']!=dict(path=str(root/'jobs'/f'{job["step"]:03d}.json'))
                or any(authorization.get(key)!=accepted.get(key) for key in ('scope','native_attempt','interface','call_id'))
                or any(bound.get(key)!=accepted.get(key) for key in ('scope','native_attempt','interface'))
                or bound['subject']!=subject or bound['review']!=request['review']
                or request['native_intent']!=ref_at(native_record/'native-intent.json')
                or allocation['native_intent']!=request['native_intent']
                or read(request['native_intent'])['task_ref']!=q.control_reference(root,request['job'])):
            raise q.IntegrityError('Sealed managed source differs from its original call controls')
        return subject
    verified_compilers = set()
    for row, value in zip(rows, published):
        if source is None:_validate_proof(root, row['accepted'])
        else:
            if _snapshot(row['accepted']) != row['accepted']['snapshot']:
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
        accepted = read(dict(path=row['accepted']['snapshot'], size_bytes=row['accepted']['size_bytes']))
        if controls_only:
            keys={'call_id','accepted_ref','complete','missing_reason'}
            subject=None
            if 'input_sources' in value:
                keys.add('input_sources')
                if (accepted.get('input_sources') is None
                        or value['input_sources']!=accepted['input_sources']['contract']):
                    raise q.IntegrityError('Sealed declared-input control contract changed')
                if accepted.get('execution_authority') is not None:
                    keys.add('binding');subject=saved_binding(accepted,value.get('binding'))
            else:
                keys.update(('binding','compiler_ref'))
                if 'client_binding' in value:keys.add('client_binding')
                subject=saved_binding(accepted,value.get('binding'))
            if set(value)!=keys or value['missing_reason'] is not None:
                raise q.IntegrityError('Sealed managed input control shape changed')
            if subject is not None:
                from . import originals
                producer=(originals.dependency(source,subject['producer_step'],seal_ref=subject['producer_seal'])
                    if source is not None else originals.node(root,subject['producer_step']))
                seal=read(subject['producer_seal'])
                expected_ref=q._ref(producer.original_root,Path(producer.original_root)/'native-evidence'/
                    f'{producer.step:03d}'/'result-identity.json',source=producer if producer.inherited else None)
                if 'origin_identity' in subject['producer_seal']:expected_ref['origin_identity']=producer.origin_identity
                matches=[row for row in seal.get('materials',[]) if
                    {key:item for key,item in row.items() if key!='file_stat'}==subject['material']]
                if (not 0<subject['producer_step']<job['step'] or subject['producer_seal']!=expected_ref or len(matches)!=1):
                    raise q.IntegrityError('Sealed managed source producer metadata changed')
                if 'compiler_ref' in value:
                    compiled=dict(material=subject['material'],compiler=value['compiler_ref'],
                        **(dict(client_binding=value['client_binding']) if 'client_binding' in value else {}))
                    if [item for item in seal.get('compiled_implementations',[]) if item['material']==subject['material']]!=[compiled]:
                        raise q.IntegrityError('Sealed managed compiler control binding changed')
                    execution_inputs.verify_compilation(root,value['compiler_ref'],material=subject['material'],
                        client_binding=value.get('client_binding'),source=source,controls_only=True)
            continue
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
                    or value['input_sources'] != accepted['input_sources']['contract']
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


@_job_storage
def _capture(root, job, payload, materials):
    """Snapshot mechanical adapter facts once, before the scientific seal."""
    config = c.settings(root, job).get('history_observer')
    if config is None:
        config=_managed_config(job,root)
        if config is None:return None
        raw_sha=c.file_ref(c.regular(execution.raw(root,job['step'])))
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
        return dict(path=path.relative_to(root).as_posix())
    adapter = c.regular(Path(config['adapter']))
    raw = execution.raw(root, job['step'])
    source_sha = c.file_ref(c.regular(raw))
    path = root / 'host-history/observations' / f'{job["step"]:03d}.json'
    binding = _observer_binding(source_sha, config)
    if path.exists():
        stored = c.read(c.regular(path))
        if stored.get('binding') != binding:
            raise ObserverIntegrityError('Retained observer snapshot binding changed')
        return dict(path=path.relative_to(root).as_posix())
    known = set();known_contexts = set();prior_contexts = [];managed_outputs = {};previous_proofs = {}
    def remember_proofs(observation, owner):
        # Read only already-loaded control metadata here. Validate the chosen
        # retained object later, without statting every historical evidence file.
        proofs=[proof for row in observation['candidates'] for proof in row['evidence']]
        for field in ('current_candidate','actual_submission'):
            if observation.get(field) is not None:proofs.extend(observation[field]['evidence'])
        for row in observation.get('contexts',[]):proofs.extend(row['proofs'])
        for proof in proofs:
            if proof.get('retention')=='archived' and 'source_file_stat' in proof:
                source=originals.source_for_ref(owner,proof)
                key=str(Path(source.original_root)/proof['path'])
                previous=previous_proofs.get(key)
                if previous is None or previous[0].step<source.step:previous_proofs[key]=(source,proof)
    def remember_outputs(rows, owner):
        # Index only existing host-managed output bindings. Resolve and verify
        # the exact original only when the observer actually selects it.
        for row in rows:
            for proof in row.get('outputs',[]):
                if proof.get('retention')=='archived':
                    managed_outputs[str(Path(owner.original_root)/proof['path'])]=(owner,proof)
                    managed_outputs[str(Path(owner.original_root)/proof['snapshot'])]=(owner,proof)
    from . import originals
    for receipt in job.get('history', {}).get('nodes', []):
        prior_source = originals.node(root, receipt['step'])
        old_seal = c.read(originals.source_record(prior_source, 'result-identity.json'))
        if old_seal.get('observations'):
            prior = read_observation(root, originals.source_job(prior_source), old_seal)
            known.update(row['id'] for row in prior['candidates'])
            known_contexts.update(row['call_id'] for row in prior.get('contexts', []))
            prior_contexts.extend(prior.get('contexts', []))
            remember_outputs(prior.get('managed_calls',[]),prior_source)
            remember_proofs(prior,prior_source)
    current_calls=managed_calls(root,job) if _managed_config(job,root) else None
    if current_calls is not None:
        remember_outputs(current_calls,originals.node(root,job['step']))
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
    proof_cache = {};artifact_cache = {}
    def artifact(name):
        if not isinstance(name,str):raise ValueError('Observer artifact path is not a string')
        if name not in artifact_cache:
            bound=managed_outputs.get(str(root/name))
            if bound is None:
                path=root/name
                if Path(name).is_absolute() or '..' in Path(name).parts or path.resolve()!=path:
                    raise ObserverIntegrityError('Observer artifact must be an exact instance path')
                artifact_cache[name]=dict(path=name,size_bytes=c.regular(path).stat().st_size,retention='unavailable')
            else:
                owner,saved=bound
                path=originals.resolve_ref(originals.source_for_ref(owner,saved),dict(saved,path=saved['snapshot']))
                from . import experiments
                actual=experiments.proof(path)
                artifact_cache[name]=dict(path=name,snapshot=str(path),retention='archived',
                    **{key:actual[key] for key in ('size_bytes','file_stat')})
        return artifact_cache[name]
    # Contract2 top-level file descriptors are artifacts; listing one again in
    # proofs cannot turn its scientific bytes into copyable control metadata.
    artifact_names={item['artifact'] for item in [value.get('actual_submission')] if isinstance(item,dict) and isinstance(item.get('artifact'),str)}
    if config.get('contract_version')==2:
        from . import lineage
        artifact_names.update(proof['path'] for item in value['contexts'] for proof in lineage.context_artifacts(item))
    def proof(name):
        if not isinstance(name, str):
            raise ValueError('Observer original path is not a string')
        if name in artifact_names:return artifact(name)
        if name not in proof_cache:
            proof_cache[name] = _proof(root,name,previous=previous_proofs.get(str(root/name)))
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
        else:actual = dict(candidate_id=actual['candidate_id'], artifact=artifact(actual['artifact']),
                          evidence=[proof(name) for name in actual['evidence']])
    contexts = None
    if config.get('contract_version') == 2:
        from . import lineage
        contexts = lineage.contexts(root, job, value['contexts'], proof, prior_contexts)
        if any(row['status']=='unknown' for row in contexts):
            coverage = dict(status='partial',note='Original execution contexts include unknown outcomes.')
    snapshot = dict(binding=binding, candidates=candidates, current_candidate=current, actual_submission=actual, coverage=coverage,
                    **(dict(contexts=contexts) if contexts is not None else {}))
    if any(proof.get('retention')!='archived' for proof in artifact_cache.values()):
        snapshot['coverage']=dict(status='partial',note='Live observer artifacts have no retained managed original; their historical bytes are unavailable.')
    if diagnostics:snapshot['candidate_diagnostics']=diagnostics
    if config.get('source_contract') is not None:
        from . import qualification as q
        snapshot['input_sources'] = dict(contract=config['source_contract'],
            job=q._ref(root, root / 'jobs' / f'{job["step"]:03d}.json'), raw=q._ref(root, raw), view=value['input_sources'])
        observer_input_sources(root, job, snapshot)
    if _managed_config(job,root):
        from . import generic_results
        snapshot['managed_calls']=current_calls
        diagnostics=[]
        for candidate in generic_results.candidates(snapshot['managed_calls'],diagnostics=diagnostics):
            if candidate['id'] in known | seen:raise ObserverIntegrityError('Managed candidate identity collides with observer facts')
            snapshot['candidates'].append(candidate)
        if diagnostics:snapshot.setdefault('candidate_diagnostics',[]).extend(diagnostics)
    if snapshot.get('candidate_diagnostics'):
        snapshot['coverage']=dict(status='partial',note='Some observer items have no verified binding; retained valid facts remain available.')
    c.write(path, snapshot)
    return dict(path=path.relative_to(root).as_posix())


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
        value = dict(binding=_observer_binding(c.file_ref(c.regular(raw)),config),
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
        return dict(path=path.relative_to(root).as_posix())


def _source(root, step, *, resolve_choices=True):
    from . import originals
    if originals.inherited(root, step):
        source = originals.node(root, step)
        c.validate_original(source,controls_only=True)
        job = originals.source_job(source)
        seal_path = originals.source_record(source, 'result-identity.json')
        seal = c.read(seal_path)
        from . import candidate_refs
        raw=originals.resolve_ref(source,seal['raw_ref'])
        _sealed_raw_ref(raw,seal)
        payload=c.read_scientific_output(raw)
        if resolve_choices:
            payload = candidate_refs.control_payload(job, payload, root=root, source=source)
            mapping = candidate_refs.source_input_mapping(source, job)
            if job['role'] == 'W' and seal.get('observations'):
                observed = c.read(originals.resolve_ref(source, seal['observations']))
                mapping.update({row['id']: candidate_refs.origin_candidate(source.origin_identity, row['id'])
                    for row in observed['candidates']})
            payload = candidate_refs.public_science_fields(payload, mapping)
        return candidate_refs.source_job_view(source, job), payload, seal, source_seal_ref(source)
    source=originals.node(root,step)
    record=c.host_record(c.workspace(root,step))
    seal_path=c.regular(record/'result-identity.json')
    seal=c.read(seal_path)
    c.validate_original(source,controls_only=True)
    raw=originals.resolve_ref(source,seal['raw_ref'])
    _sealed_raw_ref(raw,seal)
    job=originals.source_job(source)
    from . import candidate_refs
    payload=c.read_scientific_output(raw)
    return job, candidate_refs.control_payload(job,payload,root=root) if resolve_choices else payload, seal, c.record_ref(seal_path)


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
            if chooser is not None else originals.resolve_root_ref(root,dict(ref,
                path=str(c.workspace(root,job['step'])/ref['path']))))
        native_record = (Path(original.original_root) / (original.native_record or f'native-evidence/{step:03d}')
            if original.inherited else execution.record(root, step))
        intent = c.read(originals.source_record(original, 'native-intent.json')
            if original.inherited else native_record / 'native-intent.json')
        outcome = c.read(originals.source_record(original, 'native-outcome.json')
            if original.inherited else native_record / 'native-outcome.json')
        raw_ref = c._native_output_identity(root, step, intent, outcome,
            **(dict(source=original) if original.inherited else {}))
        if (c.regular(input_path).stat().st_size != ref['size_bytes']
                or next((row for row in job.get('direct_originals',job['direct_refs']) if row['path']==ref['path']),None) is None
                or intent['task_ref'] != seal['job_ref']
                or type(outcome.get('returncode')) is not int or outcome['returncode'] != 0
                or outcome.get('stop_reason')
                or outcome.get('usage', {}).get('unknown_steps') != [] and not budget.native_completion_known(outcome, originals.source_settings(original))
                or raw_ref != {key:seal['raw_ref'][key] for key in ('path','size_bytes')}
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
    if receipt is not None and seal_sha != receipt['seal_ref']:
        raise ValueError('Historical node seal changed')
    if (receipt is None or job['role'] != match[1]
            or object_id != f'{job["role"]}{step:03d}-I{index:03d}' or index < 1
            or index > len(seal['materials'])):
        raise UnresolvedImplementation('Implementation selection is outside its exact frozen source')
    return c._public_materials(root,[sorted(seal['materials'], key=lambda row: row['path'])[index - 1]])[0]


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
        if seal_sha!=receipt['seal_ref']:raise ObserverIntegrityError('Candidate source seal changed')
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
                    and all(row[key]==output[key] for key in ('size_bytes',))]
                if len(matches)!=1:continue
                material=c._public_materials(root,matches)[0]
            owner_step=int(Path(material['path']).parts[2])
            owner,_,owner_seal,_=_source(root,owner_step,resolve_choices=False)
            index=next((number for number,row in enumerate(sorted(owner_seal['materials'],key=lambda row:row['path']),1)
                if {key:value for key,value in row.items() if key!='file_stat'}==material),None)
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
            if step > source_cutoff:
                extra = c.read(root / 'jobs' / f'{step:03d}.json')
                report = extra.get('final_report')
                final_report = (extra.get('final_only') and report is not None and report.get('source_cutoff') == source_cutoff)
                if not final_report and (extra.get('purpose') != 'closeout' or extra.get('source_cutoff') != source_cutoff):
                    raise ValueError('Closeout history cannot include an unrelated stopped node')
        sources.append(_source(root, step))
    return sources


def records_from_sources(root, sources, *, write_cache=True, include_details=True):
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
        raw = c.regular(c._execution_raw(root,step))
        raw_ref = _sealed_raw_ref(raw,seal)
        detail = []
        if include_details:
            text = raw.read_text(encoding='utf-8')
            detail = [dict(step=step, role=job['role'], source='node:%03d/scientific-output' % step,
                           part=index + 1, parts=(len(text) + TEXT_CHARS - 1) // TEXT_CHARS,
                           text=text[start:start + TEXT_CHARS])
                      for index, start in enumerate(range(0, len(text), TEXT_CHARS))]
            view['conclusion_pages'] = [f'inputs/history/nodes/{step:03d}/{index+1:04d}.json'
                for index in range(len(detail))]
        observation = None
        if seal.get('observations'):
            observation = read_observation(root, job, seal)
            if observation['binding']['source_ref'] != raw_ref:
                raise ValueError('Observer snapshot differs from scientific source')
        row = dict(step=step, role=job['role'], seal_ref=seal_sha, view=view, detail=detail,
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


def page_writer(work, *, root, policy=None, context=None):
    from .protocol import require_current
    from . import storage_policy as storage
    require_current(Path(work).parents[2])
    inventory=storage.Inventory(policy,context=context)
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
        inventory.entry();inventory.file(len(data))
        if destination.exists() or destination.is_symlink():
            if c.regular(destination,limit=MAX_PAGE_BYTES).read_bytes()!=data:
                raise ObserverIntegrityError('Frozen scientific history page changed')
        else:
            from . import atomic_files
            with storage.mutation(root,len(data),policy=policy,context=context):
                atomic_files.publish_bytes(destination,data)
        files.append(dict(path=path, source=str(destination), size_bytes=len(data)))
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
                or c.regular(path).stat().st_size!=item['size_bytes'] or path.stat().st_size!=item['size_bytes']):
            raise ObserverIntegrityError('Bound execution recovery material changed')
    if binding.get('audit_projection_version') == 1:
        from . import candidate_audit
        sources = [_source(root, row['step']) for row in binding['nodes']]
        expected = candidate_audit.collect_refs(root, sources, step=int(work.name), adjacent=binding.get('adjacent_audit'))
        if binding['audit_receipts'] != expected:
            raise ValueError('Frozen audit receipts differ from their sealed prefix anchors')
    for row in binding['nodes']:
        _, _, _, seal_sha = _source(root, row['step'])
        if seal_sha != row['seal_ref']:
            raise ValueError('Frozen historical node identity changed')
    for row in binding['files']:
        from . import originals
        path = originals.resolve_root_ref(root,dict(row,path=str(work/row['path'])))
        limit=MAX_PAGE_BYTES
        if row.get('kind')=='scientific_original':
            match=re.fullmatch(r'inputs/(?:history/nodes|parents)/([0-9]{3,})/scientific-original\.json',row['path'])
            if (match is None or row['path'] not in {f'inputs/history/nodes/{int(match[1]):03d}/scientific-original.json',
                        f'inputs/parents/{int(match[1]):03d}/scientific-original.json'}
                    or not any(ref['step']==int(match[1]) for ref in binding['nodes'])):
                raise ValueError('Bound scientific original has no exact history source')
            _,_,seal,_=_source(root,int(match[1]),resolve_choices=False)
            if set(row)-{'source'}!={'path','size_bytes','kind'} or row['size_bytes']!=seal['raw_ref']['size_bytes']:
                raise ValueError('Bound scientific original differs from its sealed source')
            limit=None
        if c.regular(path, limit=limit).stat().st_size != row['size_bytes'] or path.stat().st_size != row['size_bytes']:
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
        if config is None or value['binding'] != _observer_binding(_sealed_raw_ref(originals.source_raw(source),seal), config):
            raise ObserverIntegrityError('Inherited observation lost its exact source binding')
        from . import candidate_refs
        return candidate_refs.source_observation(source, originals.source_job(source), value)
    if job.get('formal_review'):
        if seal.get('observations') is not None:raise ValueError('Formal review has an ordinary observer snapshot')
        return
    receipt = seal.get('observations')
    config = c.settings(root, job).get('history_observer') or _managed_config(job,root,controls_only=True)
    if config is None:
        if receipt is not None:
            raise ValueError('Unconfigured observer snapshot in scientific seal')
        return
    if receipt is None:
        raise ValueError('Configured observer snapshot missing from scientific seal')
    path = root / receipt['path']
    expected = root / 'host-history/observations' / f'{job["step"]:03d}.json'
    if path != expected or c.regular(path).stat().st_size != receipt.get('size_bytes',path.stat().st_size):
        raise ObserverIntegrityError('Sealed observer snapshot changed')
    value = c.read(path)
    raw_ref=_sealed_raw_ref(originals.resolve_root_ref(root,seal['raw_ref']),seal)
    if value['binding'] != _observer_binding(raw_ref, config):
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
            from . import lineage
            proofs.extend(lineage.context_artifacts(context))
    for proof in proofs:
        _validate_proof(root, proof)


def validate_observation_original(source,job,seal,*,controls_only=False):
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
            managed = dict(adapter_ref='registered-originals-v1')
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
    if receipt is None or receipt.get('path',) != f'host-history/observations/{source.step:03d}.json':
        raise ObserverIntegrityError('Original observer snapshot lost its exact producer')
    value = c.read(o.resolve_ref(source, receipt))
    raw_ref=({key:seal['raw_ref'][key] for key in ('path','size_bytes')} if controls_only
        else _sealed_raw_ref(o.source_raw(source),seal))
    if value.get('binding') != _observer_binding(raw_ref, config):
        raise ObserverIntegrityError('Original observer snapshot binding changed')
    observer_input_sources(source.storage_root,job,value,source=source,controls_only=controls_only)
    if managed is not None:
        validate_managed_original(source,job,value.get('managed_calls',[]),controls_only=controls_only)
    if controls_only:
        from . import experiments
        for context_value in value.get('contexts',[]):
            accepted_refs=[proof for proof in context_value.get('proofs',[]) if Path(proof['path']).name=='accepted.json']
            if len(accepted_refs)!=1:raise ObserverIntegrityError('Original context has no exact accepted control')
            accepted_ref=accepted_refs[0]
            accepted=c.read(o.resolve_ref(o.source_for_ref(source,accepted_ref),dict(accepted_ref,path=accepted_ref['snapshot'])))
            if any(accepted.get(key)!=context_value.get(key) for key in ('call_id','scope','parent','change_intent')):
                raise ObserverIntegrityError('Original context differs from its saved accepted call')
            completed_refs=[proof for proof in context_value.get('proofs',[]) if Path(proof['path']).name=='completed.json']
            if context_value['status'] in {'known_success','known_failed'}:
                if len(completed_refs)!=1:raise ObserverIntegrityError('Original context has no exact terminal control')
                completed_ref=completed_refs[0]
                completed=c.read(o.resolve_ref(o.source_for_ref(source,completed_ref),dict(completed_ref,path=completed_ref['snapshot'])))
                if any(completed.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding')):
                    raise ObserverIntegrityError('Original context terminal changed its accepted call')
                _,controls=experiments._observation_file_roles(None,accepted,completed,directory=Path(accepted_ref['path']).parent)
                for proof in context_value['proofs']:
                    if proof['path'] in controls:
                        o.resolve_ref(o.source_for_ref(source,proof),dict(proof,path=proof['snapshot']))
        return value
    proofs = [proof for row in value['candidates'] for proof in row['evidence']]
    for field in ('current_candidate', 'actual_submission'):
        if value.get(field) is not None:
            proofs.extend(value[field]['evidence'])
    if value.get('actual_submission') is not None:
        proofs.append(value['actual_submission']['artifact'])
    if config.get('contract_version') == 2:
        for row in value.get('contexts', []):
            proofs.extend(row['proofs'])
            from . import lineage
            proofs.extend(lineage.context_artifacts(row))
    for proof in proofs:
        if proof.get('retention') == 'archived':
            if _snapshot(proof) != proof['snapshot']:
                raise ObserverIntegrityError('Archived original observer proof changed')
            o.resolve_ref(o.source_for_ref(source, proof), dict(proof, path=proof['snapshot']))
    return value


def _validate_proof(root, proof, *, context=None):
    if proof.get('retention') != 'archived':
        return
    expected = _snapshot(proof)
    if proof.get('snapshot')!=expected:raise ObserverIntegrityError('Archived observer original changed')
    from . import experiments
    from . import originals
    if 'origin_identity' in proof:
        source=originals.for_origin(root,proof['origin_identity'])
        if source.inherited:
            # The typed resolver checks copied-control custody and preserves
            # original scientific-file identity; copied control inodes differ.
            originals.resolve_ref(source,dict(proof,path=expected),context=context)
            return
    path = originals.resolve_root_ref(root, dict(proof, path=expected)) if (Path(root) / originals.MANIFEST).exists() else root / expected
    actual=experiments.proof(path,context=context)
    if any(actual[key]!=proof[key] for key in ('size_bytes','file_stat') if key in proof):
        raise ObserverIntegrityError('Archived observer original changed')


def refresh(root, before_step):
    """Update disposable projections after every seal, including a terminal A."""
    from .protocol import require_current
    require_current(root)
    latest = c.read(root / 'jobs' / f'{before_step-1:03d}.json') if before_step > 1 else {}
    if latest.get('lineage_projection_version') == 1:
        from . import lineage
        cutoff=(latest.get('final_report') or {}).get('source_cutoff',latest.get('source_cutoff'))
        if cutoff is not None:
            from . import search,originals
            frozen=search._frozen_sources(root,latest)
            sources=[frozen[key] for key in sorted(frozen)]+[_source(root,latest['step'])]
            records=records_from_sources(root,sources,write_cache=False,include_details=False)
            state=(search.fold(root,sources,recovery_refs=originals.recovery_refs(originals.node(root,latest['step']),latest))
                if c.settings(root,latest).get('search_policy') is not None or any(
                    originals.source_settings(originals.node(root,source['step'])).get('search_policy') is not None for source,_,_,_ in sources) else None)
            projection=dict(candidates=lineage.candidate_view(records),search_state=state)
        else:
            projection=lineage.project(root,before_step)
        overview=projection['candidates']
        if projection['search_state'] is not None:
            from . import native,search
            threshold=(c.settings(root,latest).get('portfolio_policy') or {}).get('review_after_cycles')
            try:advice=native.cycle_token_advice(root,projection['search_state'])
            except (OSError,ValueError,KeyError,TypeError):
                advice=dict(status='unavailable',reference_cycles=None,minimum_samples=2,complete_sample_count=0,excluded_sample_count=0,
                    reasons=['current_usage_snapshot_unavailable'],samples=[],scope='Reported token reference unavailable; no time or admission guarantee.')
            overview['portfolio_metrics']=search.metrics_view(projection['search_state'],threshold,source_step=latest['step'],token_advice=advice)
    else:
        overview = candidate_overview(rebuild(root, before_step))
    overview.pop('candidate_index')
    if len(_bytes(overview)) > MAX_PAGE_BYTES:
        overview=dict(coverage=dict(status='partial',note='Read complete observation originals for coverage.'),view_omitted=True,
            omission_reason='Overview exceeds compact capacity; complete observations and candidate indexes remain retained.')
    c.write(root / 'host-history/overview.json', overview)


def _sealed_raw_ref(raw, seal):
    """Verify the saved private proof before using its existing owner locator."""
    from . import experiments
    saved=seal['raw_ref'];actual=experiments.proof(raw)
    if any(actual[key]!=saved[key] for key in ('size_bytes','file_stat') if key in saved):
        raise ObserverIntegrityError('Sealed historical scientific original changed')
    return {key:saved[key] for key in ('path','size_bytes')}


def _observer_binding(source_sha, config):
    return dict(source_ref={key:source_sha[key] for key in ('path','size_bytes')},adapter_ref=config.get('adapter_ref'),
                **(dict(contract_version=2) if config.get('contract_version') == 2 else {}),
                **(dict(source_contract=config['source_contract'])
                   if config.get('source_contract') is not None else {}))
