"""Public covshift source resolver and isolated audit/byte-promotion adapter.

The resolver never loads models. Use [python, __file__, '--isolate'] as the
launcher; its audit namespace cannot mount the real targets or private task data.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

BOUND = 32 * 1024 * 1024
FILES = ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json')
AUTHOR_REPAIR_CODES = frozenset({'implementation_source_missing','audit_dependency_missing','audit_training_error',
    'audit_prediction_error','audit_interface_missing','audit_timeout'})


def _exact(path, limit=None):
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_file() or limit is not None and path.stat().st_size > limit:
        raise ValueError('Exact bounded delivery original required')
    return path


def _generation(path):
    info = _exact(path).stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _copy_selected(source, destination):
    before = _generation(source)
    pending = destination.with_name(destination.name + '.rootless-pending')
    if pending.exists() or pending.is_symlink():
        raise ValueError('Uncertain delivery target temporary exists')
    with source.open('rb') as original, pending.open('xb') as output:
        opened = os.fstat(original.fileno())
        if [opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns] != before:
            raise ValueError('Selected source changed before copy')
        shutil.copyfileobj(original, output, 1024 * 1024)
        output.flush();os.fsync(output.fileno())
        after = os.fstat(original.fileno())
        if [after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns] != before or _generation(source) != before:
            raise ValueError('Selected source changed during copy')
    if pending.stat().st_size != before[2]:
        raise ValueError('Selected source copy is incomplete')
    os.replace(pending, destination)
    return _generation(destination)


def target(value):
    if not isinstance(value, dict) or set(value) != {'runs', 'submission'}:
        raise ValueError('Cov target requires exact public run output and submission directories')
    for name in value.values():
        path = Path(name)
        if not path.is_absolute() or path.resolve() != path or '..' in path.parts:
            raise ValueError('Cov target must be an explicitly frozen original directory')
    if Path(value['runs']) == Path(value['submission']):
        raise ValueError('Run output and final submission must be distinct')
    return dict(value)


class SourceUnavailable(ValueError):
    pass


def original(root, candidate, cfg, ledger_cache):
    match = re.fullmatch(r'run-([1-9][0-9]*)', candidate['candidate_id'])
    if match is None or candidate['status'] != 'ok':
        raise SourceUnavailable('delivery_candidate_not_available')
    matches = []
    for proof in candidate['evidence']:
        path = _exact(Path('/inputs') / proof['label'])
        if proof['label'] not in ledger_cache:
            data = path.read_bytes()
            indexed = {}
            for line in data.splitlines():
                if not line.strip():continue
                row = json.loads(line)
                if type(row.get('run_id')) is int:indexed.setdefault(row['run_id'], []).append(row)
            ledger_cache[proof['label']] = indexed
        matches.extend((row, proof['label']) for row in ledger_cache[proof['label']].get(int(match[1]), []))
    if len(matches) != 1:
        raise SourceUnavailable('delivery_run_source_ambiguous')
    row, proof = matches[0]
    if (row.get('ok') is not True or not isinstance(row.get('model_path'), str)
            or not isinstance(row.get('script'), str) or type(row.get('val_acc')) not in (int, float)
            or not math.isfinite(row['val_acc'])):
        raise SourceUnavailable('delivery_run_source_incomplete')
    # Metric is not reinterpreted as an audit or a guarantee about current bytes.
    return {key: row[key] for key in ('run_id', 'ok', 'model_path', 'script', 'val_acc')}, proof


def mapped_model(root, row, cfg):
    named = Path(row['model_path'])
    prefix = Path(cfg['namespace_runs'])
    if not named.is_absolute() or '..' in named.parts or not named.is_relative_to(prefix):
        raise SourceUnavailable('delivery_model_mapping_unavailable')
    return root / cfg['runs'] / named.relative_to(prefix)


def model_path(root, row, cfg):
    path = mapped_model(root, row, cfg)
    if path.resolve() != path or not path.is_file() or path.stat().st_size > BOUND:
        raise SourceUnavailable('delivery_model_unavailable')
    return path


def source_path(root, row):
    path = Path(row['script'])
    if (not path.is_absolute() or not path.is_relative_to(root / 'project/nodes')
            or '..' in path.parts or path.resolve() != path or not path.is_file()
            or path.stat().st_size > BOUND):
        raise SourceUnavailable('delivery_code_source_unavailable')
    return path


def managed_objects(candidate, row):
    contexts = candidate.get('execution_contexts')
    if contexts is None:
        return None  # Legacy v1: current source snapshot, not a historical claim.
    if not isinstance(contexts, list) or len(contexts) != 1:
        raise SourceUnavailable('delivery_managed_context_missing_or_ambiguous')
    context = contexts[0]
    if (context.get('candidate_id') != candidate['candidate_id'] or context.get('run_id') != row['run_id']
            or context.get('status') != 'known_success' or not isinstance(context.get('proofs'), list)):
        raise SourceUnavailable('delivery_managed_context_not_available')
    def unique(name):
        matches = [proof for proof in context['proofs'] if proof.get('name') == name]
        if len(matches) != 1:
            raise SourceUnavailable('delivery_managed_original_missing_or_ambiguous')
        return matches[0]
    def bound(proof):
        if proof.get('retention') != 'archived' or not isinstance(proof.get('input_label'), str):
            raise SourceUnavailable('delivery_managed_original_not_retained')
        label = proof['input_label']
        if not re.fullmatch(r'execution/[A-Za-z0-9_.-]+', label):
            raise ValueError('Frozen execution proof label changed')
        path = _exact(Path('/inputs') / label)
        if path.stat().st_size != proof['size_bytes']:
            raise ValueError('Frozen managed execution original changed')
        return path
    model = context.get('model')
    if not isinstance(model,dict) or context['proofs'].count(model)!=1:
        raise ValueError('Managed model differs from frozen exact context')
    receipt = unique('row.json')
    original_row = json.loads(bound(receipt).read_text())
    if any(original_row.get(key) != value for key, value in row.items()):
        raise ValueError('Candidate ledger differs from frozen exact call row')
    bound(model)
    objects = [dict(label='artifact', proof_label=model['input_label'])]
    version = context.get('source_code_version')
    if version is not None:
        # Catalogue queries keep only the permanent code binding. The existing
        # selected stage materializes this call's pinned source when consumed.
        if (not isinstance(version,dict)
                or set(version)!={'version','object_format','commit','tree_path','selection','entry','logical_root'}
                or type(version['version']) is not int or version['version']!=1
                or version['object_format']!='sha1' or version['tree_path']!='.'
                or version['selection'] not in {'file','directory'}
                or not isinstance(version['commit'],str) or not re.fullmatch(r'[0-9a-f]{40}',version['commit'])):
            raise ValueError('Managed source code version changed')
        for key in ('entry','logical_root'):
            name=version[key]
            if (not isinstance(name,str) or not name or Path(name).is_absolute()
                    or Path(name).as_posix()!=name or '\\' in name or '\x00' in name
                    or any(part in {'','.','..'} for part in name.split('/'))):
                raise ValueError('Managed source code version path changed')
    else:
        source = unique('source.py')
        bound(source)
        objects.append(dict(label='source.py', proof_label=source['input_label']))
    return objects


def _audit(value, run_id):
    return (isinstance(value, dict) and type(value.get('run_id')) is int and value['run_id'] == run_id
            and value.get('leakage_flag') is False
            and all(type(value.get(key)) in (int, float) and math.isfinite(value[key]) for key in ('audit_score', 'audit_gap'))
            and isinstance(value.get('audit_note'), str) and value['audit_note'].startswith('audit took '))


def audit_status(value, run_id):
    """Known frozen harness fields; exit zero and a false leakage bit are insufficient."""
    if not isinstance(value, dict) or type(value.get('run_id')) is not int or value['run_id'] != run_id:
        return 'unclassified', 'audit_receipt_invalid'
    complete = (type(value.get('leakage_flag')) is bool
        and all(type(value.get(k)) in (int, float) and math.isfinite(value[k]) for k in ('audit_score', 'audit_gap'))
        and isinstance(value.get('audit_note'), str) and value['audit_note'].startswith('audit took '))
    if complete:
        return ('rejected', 'public_audit_rejected') if value['leakage_flag'] else ('audited', 'public_audit_completed_nonleaking')
    note = value.get('audit_note', '')
    if not isinstance(note, str):
        return 'unclassified', 'audit_receipt_invalid'
    if note.startswith('script ') and note.endswith(' not found; audit skipped'):
        return 'engineering_error', 'audit_source_missing'
    if note.startswith('audit timed out after '):
        return 'engineering_error', 'audit_timeout'
    if note.startswith('audit failed during training: ModuleNotFoundError:'):
        return 'engineering_error', 'audit_dependency_missing'
    if note.startswith('audit failed during training:'):
        return 'engineering_error', 'audit_training_error'
    if note.startswith('audit prediction failed:'):
        return 'engineering_error', 'audit_prediction_error'
    if note in ('train.py has no train() function; audit skipped', 'audit model has no .predict; audit skipped', 'could not load script for audit'):
        return 'engineering_error', 'audit_interface_missing'
    if note.startswith('could not import runner.score:') or note == 'train.csv missing; audit skipped':
        return 'engineering_error', 'audit_harness_unavailable'
    return 'unclassified', 'audit_not_completed_unclassified'


# Smoke: the exact saved artifact loads and predicts public validation inputs in
# the leaf, with the frozen task interpreter. No score, audit or target claim.
SMOKE = r'''import importlib.util,json
def stage(name,**extra):json.dump(dict(stage=name,**extra),open('/scratch/smoke.json','w'))
stage('harness')
import numpy as np,pandas as pd
try:import cloudpickle as loader
except ImportError:import pickle as loader
spec=importlib.util.spec_from_file_location('scid_public_runner','/inputs/harness/runner.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
meta=json.load(open('/inputs/harness/meta.json'));X,y,_=runner.split_X_y(pd.read_csv('/inputs/harness/val.csv'),meta)
stage('model')
with open('/inputs/artifact','rb') as f:model=loader.load(f)
stage('predict')
try:p=model.predict(X)
except Exception:p=model.predict(X.values)
p=np.asarray(p)
stage('done',shape_ok=p.shape==np.shape(y),finite=bool(np.issubdtype(p.dtype,np.number) and np.isfinite(p).all()))
'''


# This is a bounded engineering sample, not the public audit or a training verdict.
REPLAY = r'''import importlib.util,json,sys
from pathlib import Path
record=dict(status='inconclusive',note='sample_replay_not_completed',diagnostic='',samples={},stage='harness')
def save():Path('/scratch/replay.json').write_text(json.dumps(record,allow_nan=False))
save()
try:
    import numpy as np,pandas as pd
    spec=importlib.util.spec_from_file_location('scid_replay_runner','/inputs/harness/runner.py')
    runner=importlib.util.module_from_spec(spec);sys.modules[spec.name]=runner;spec.loader.exec_module(runner)
    meta=json.load(open('/inputs/harness/meta.json'))
    sampled_train=pd.read_csv('/inputs/harness/train.csv',nrows=32)
    sampled_val=pd.read_csv('/inputs/harness/val.csv',nrows=8)
    record['samples']=dict(selection='first rows in frozen CSV order',train_rows=len(sampled_train),
        validation_rows=len(sampled_val),available_train_rows=None,available_validation_rows=None,
        remaining_rows='not_scanned')
    record['stage']='source_import';save()
    spec=importlib.util.spec_from_file_location('scid_replay_candidate','/inputs/source.py')
    source=importlib.util.module_from_spec(spec);sys.modules[spec.name]=source;spec.loader.exec_module(source)
    record['stage']='sample_train';save()
    model=source.train(sampled_train,sampled_val)
    record['stage']='sample_predict';save()
    X,y,_=runner.split_X_y(sampled_val,meta)
    try:p=model.predict(X)
    except ModuleNotFoundError:raise
    except Exception:p=model.predict(X.values)
    p=np.asarray(p)
    if p.shape!=np.shape(y) or not np.issubdtype(p.dtype,np.number) or not np.isfinite(p).all():
        raise ValueError('Sample prediction shape, type or finiteness is incompatible')
    record.update(status='sample_replay_ok',note='only_declared_sample_import_train_predict_completed')
except (ModuleNotFoundError,FileNotFoundError) as error:
    record.update(status='inconclusive' if record['stage']=='harness' else 'dependency_error',
        note='public_harness_unavailable' if record['stage']=='harness' else 'dependency_unavailable_in_declared_sample_scope',
        diagnostic=type(error).__name__+': '+str(error))
except Exception as error:
    record.update(status='inconclusive',note='sample_or_harness_path_failed_without_full_training_verdict',
        diagnostic=type(error).__name__+': '+str(error))
save()
'''


def _replay(request):
    Path('/scratch/runs').mkdir()
    env = dict(os.environ, EXP_HARNESS='/inputs/harness', EXP_RUN_DIR='/scratch/runs',
        EXP_TRAIN='/inputs/harness/train.csv', EXP_VAL='/inputs/harness/val.csv',
        EXP_META='/inputs/harness/meta.json', EXP_RUNNER='/inputs/harness/runner.py')
    with Path('/scratch/replay.stdout').open('wb') as out, Path('/scratch/replay.stderr').open('wb') as err:
        process = subprocess.run([request['config']['python'], '-B', '-I', '-c', REPLAY],
            env=env, cwd='/scratch', stdout=out, stderr=err)
    try:
        value = json.loads(_exact('/scratch/replay.json').read_text())
    except (OSError, ValueError):
        value = {}
    if process.returncode or value.get('status') not in {'sample_replay_ok', 'dependency_error', 'inconclusive'}:
        value = dict(status='inconclusive', note='check_did_not_complete', diagnostic='', samples={})
    raw = value['diagnostic'].encode()
    return dict(_reply(request, value['status'], value['note']), diagnostic=raw[:1024].decode(errors='ignore'),
        diagnostic_omitted_bytes=max(0, len(raw)-1024), samples=value['samples'], stage=value.get('stage'))


def _smoke(request):
    with Path('/scratch/smoke.stdout').open('wb') as out, Path('/scratch/smoke.stderr').open('wb') as err:
        subprocess.run([request['config']['python'], '-B', '-I', '-c', SMOKE], env=dict(os.environ), cwd='/scratch', stdout=out, stderr=err)
    try:
        value = json.loads(_exact('/scratch/smoke.json').read_text())
    except (OSError, ValueError):
        value = {}
    return _reply(request, *smoke_status(value))


def smoke_status(value):
    """Interpret the leaf-written stage record; a harness failure is unknown, not a model failure."""
    stage = value.get('stage') if isinstance(value, dict) else None
    if stage in (None, 'harness'):
        return 'unknown', 'smoke_public_harness_unavailable'
    if stage != 'done':
        return 'failed', 'smoke_artifact_' + ('load' if stage == 'model' else 'predict') + '_failed'
    if value.get('shape_ok') is not True or value.get('finite') is not True:
        return 'failed', 'smoke_prediction_invalid'
    return 'ok', 'smoke_artifact_loaded_and_predicted_public_inputs'


def _reply(request, status, note):
    value=dict(status=status,operation_id=request['operation_id'],candidate_id=request['candidate_id'],
        artifact_ref=request['artifact_ref'],note=note)
    if status=='engineering_error' and request.get('phase')=='audit':
        value['recovery']='author_repair' if note in AUTHOR_REPAIR_CODES else 'control_retry' if note in {
            'audit_source_missing','audit_harness_unavailable','audit_process_failed'} else 'none'
    return value


def _write(path, data):
    with path.open('wb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def _replace(path, data):
    pending = path.with_name(path.name + '.rootless-pending')
    if pending.exists() or pending.is_symlink():
        raise ValueError('Uncertain delivery target temporary exists')
    _write(pending, data)
    os.replace(pending, path)


def _promote(request, runs, submission, artifact, audit):
    # Fixed temporary names have no independent operation ownership. Refuse
    # every known pending target before replacing even the first published file.
    for path in (runs/'submitted_model.pkl', submission/'model.pkl',
                 runs/'submitted.json', runs/'rootless-delivery-operation.json'):
        pending = path.with_name(path.name + '.rootless-pending')
        if pending.exists() or pending.is_symlink():
            raise ValueError('Uncertain delivery target temporary exists')
    # A multi-file commit can be interrupted. The marker is written last;
    # anything partial stays unknown and is queried without resubmission.
    published = {}
    for name, path in [('run_model', runs/'submitted_model.pkl'), ('submission_model', submission/'model.pkl')]:
        published[name] = _copy_selected(artifact, path)
    audit = dict(audit, model_path=str(Path(request['target']['runs']) / 'submitted_model.pkl'))
    _replace(runs / 'submitted.json', (json.dumps(audit, allow_nan=False) + '\n').encode())
    marker = dict(operation_id=request['operation_id'], candidate_id=request['candidate_id'],
                  artifact_ref=request['artifact_ref'], artifact_origin=request['artifact_origin'], audit=audit, published=published)
    _replace(runs / 'rootless-delivery-operation.json', (json.dumps(marker) + '\n').encode())


def _submission_matches(request, runs, submission, marker_path):
    """Read one exact completion marker and the current published originals."""
    try:
        marker = json.loads(_exact(marker_path).read_text())
        actual = json.loads(_exact(runs/'submitted.json').read_text())
        return (isinstance(marker, dict) and isinstance(marker.get('published'), dict)
                and all(marker.get(key) == request[key] for key in ('operation_id', 'candidate_id', 'artifact_ref', 'artifact_origin'))
                and _audit(actual, request['context']['row']['run_id']) and actual == marker['audit']
                and _generation(runs / 'submitted_model.pkl') == marker['published']['run_model']
                and _generation(submission / 'model.pkl') == marker['published']['submission_model'])
    except (OSError, ValueError, KeyError):
        return False


def _confirm(request, runs, submission):
    # Only a complete final marker can prove the original operation finished.
    # Confirmation never repairs, promotes or removes any pending file.
    marker = runs/'rootless-delivery-operation.json'
    for path in (marker, marker.with_name(marker.name + '.rootless-pending')):
        if _submission_matches(request, runs, submission, path):
            return _reply(request, 'confirmed', 'exact_public_submission_confirmed')
    return _reply(request, 'unknown', 'exact_submission_not_confirmed')


def _current_guard(request):
    # Standalone task Python has no framework import; load the host-frozen pure contract.
    import runpy
    host=None
    try:
        from scidiscovery.research_loop import protocol
        host=Path(protocol.__file__).resolve()
    except ImportError:
        parents=Path(__file__).resolve().parents
        if len(parents)>3:
            checkout=parents[3]
            candidate=checkout/'src/scidiscovery/research_loop/protocol.py'
            if (checkout/'pyproject.toml').is_file() and candidate.is_file():host=candidate
    source=host if host is not None else Path('/inputs/scid-current-protocol')
    source=_exact(source)
    if source!=host:
        proof=next((item for item in request.get('inputs',[]) if item.get('label')=='scid-current-protocol'),None)
        if proof is None or source.stat().st_size!=proof['size_bytes']:
            raise ValueError('Frozen current protocol original differs')
    runpy.run_path(str(source))['require_scope'](request.get('scope'))


def leaf(request):
    _current_guard(request)
    phase = request['phase']
    if phase == 'resolve':
        cfg = request['config']
        if set(cfg) != {'runs', 'namespace_runs', 'python'}:
            raise ValueError('Cov provider requires exact runs mapping and runtime')
        if (Path(cfg['runs']).is_absolute() or '..' in Path(cfg['runs']).parts
                or not Path(cfg['namespace_runs']).is_absolute() or '..' in Path(cfg['namespace_runs']).parts):
            raise ValueError('Cov provider requires canonical original run paths')
        items, ledger_cache, object_cache = [], {}, {}
        for candidate in request['candidates']:
            item = dict(candidate_id=candidate['candidate_id'], status='unavailable')
            try:
                row, evidence = original(Path(request['root']), candidate, cfg, ledger_cache)
                mapped_model(Path(request['root']), row, cfg)
                objects = managed_objects(candidate, row)
                managed = objects is not None
                if objects is None:
                    objects = []
                    for label, path in [('artifact', model_path(Path(request['root']), row, cfg)),
                                        ('source.py', source_path(Path(request['root']), row))]:
                        if path not in object_cache:object_cache[path] = dict(path=str(path), size_bytes=path.stat().st_size)
                        objects.append(dict(label=label, **object_cache[path]))
                item.update(status='audit_pending', evidence_label=evidence, objects=objects,
                    context=dict(row=row, identity_note=('Exact sealed managed call originals; public audit is still pending.' if managed else
                        'Legacy current-source snapshot; original historical source version is not established.')))
            except SourceUnavailable as error:
                item['reason']=str(error)
            except FileNotFoundError:
                item['reason']='delivery_original_missing'
            items.append(item)
        return dict(items=items, capabilities=['smoke'])
    if phase == 'replay':
        for label, reference in request['object_refs'].items():
            if _exact(Path('/inputs') / label).stat().st_size != reference['size_bytes']:
                raise ValueError('Frozen replay input changed')
        return _replay(request)
    run_id = request['context']['row']['run_id']
    if request['candidate_id'] != f'run-{run_id}' or not isinstance(request['operation_id'],str) or not request['operation_id']:
        raise ValueError('Exact operation and candidate required')
    if _exact('/inputs/artifact').stat().st_size != request['artifact_ref']['size_bytes']:
        raise ValueError('Selected model bytes changed')
    for label, reference in request['object_refs'].items():
        if _exact(Path('/inputs') / label).stat().st_size != reference['size_bytes']:raise ValueError('Frozen audit input changed')
    runs, submission = Path('/target/runs'), Path('/target/submission')
    if phase == 'smoke':
        return _smoke(request)
    if phase == 'audit':
        scratch = Path('/scratch/runs');scratch.mkdir()
        row = dict(request['context']['row'], model_path='/inputs/artifact', script='/inputs/source.py')
        _write(scratch / 'results.jsonl', (json.dumps(row, allow_nan=False) + '\n').encode())
        env = dict(os.environ, EXP_HARNESS='/inputs/harness', EXP_RUN_DIR=str(scratch),
                   EXP_TRAIN='/inputs/harness/train.csv', EXP_VAL='/inputs/harness/val.csv',
                   EXP_META='/inputs/harness/meta.json', EXP_RUNNER='/inputs/harness/runner.py')
        with Path('/scratch/audit.stdout').open('wb') as out, Path('/scratch/audit.stderr').open('wb') as err:
            result = subprocess.run([request['config']['python'], '-B', '-I', '/inputs/harness/exp_cli.py',
                                     'submit', '--run-id', str(run_id)], env=env, stdout=out, stderr=err)
        proof = scratch / 'submitted.json'
        if result.returncode:
            return _reply(request, 'engineering_error', 'audit_process_failed')
        if not proof.exists():
            return _reply(request, 'unclassified', 'audit_receipt_missing')
        value=None
        try:
            value=json.loads(_exact(proof).read_text())
            status, note = audit_status(value, run_id)
        except (OSError, ValueError):
            status, note = 'unclassified', 'audit_receipt_invalid'
        if status in {'audited','rejected'}:
            try:matched=_exact(scratch/'submitted_model.pkl').stat().st_size==request['artifact_ref']['size_bytes']
            except (OSError,ValueError):matched=False
            if not matched:status,note='unclassified','audit_model_identity_changed'
        if status != 'audited':
            reply=_reply(request, status, note)
            diagnostic=value.get('audit_note') if isinstance(value,dict) else None
            if isinstance(diagnostic,str):reply['diagnostic']=diagnostic[:1024]
            return reply
        return _reply(request, 'audited', 'public_audit_completed_nonleaking')
    if phase == 'promote':
        audit = json.loads(_exact('/scratch/runs/submitted.json').read_text())
        if not _audit(audit, run_id) or _exact('/scratch/runs/submitted_model.pkl').stat().st_size != request['artifact_ref']['size_bytes']:
            return _reply(request, 'failed', 'audit_not_completed')
        artifact=_exact('/inputs/artifact')
        _promote(request, runs, submission, artifact, audit)
    elif phase != 'confirm':
        raise ValueError('Unknown delivery phase')
    return _confirm(request, runs, submission)


def isolate(request, adapter):
    """Exec the declared namespace; only promotion can write true target dirs."""
    _current_guard(request)
    stage = Path(request['stage'])
    if stage.resolve() != stage or not stage.is_dir() or not Path(adapter).is_file():
        raise ValueError('Exact delivery stage required')
    python = Path(request['config']['python'])
    runtime = next((proof for proof in request['inputs'] if proof['label'] == 'runtime-python'), None)
    deadline = request.get('deadline_boottime') if request['phase']=='replay' else None
    if request['phase']=='replay' and (type(deadline) not in (int,float) or not 0 < deadline < float('inf')):
        raise ValueError('Replay requires its original absolute deadline')
    if runtime is None or python.absolute() != Path(runtime['invoked_path']) or python.resolve() != Path(runtime['path']):
        raise ValueError('Frozen task interpreter changed')
    bwrap = shutil.which('bwrap')
    if bwrap is None:
        raise ValueError('Delivery requires the qualified namespace launcher')
    args = [bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
            '--die-with-parent', '--new-session', '--clearenv', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    base = Path(runtime['path']).parent.parent
    # These paths derive only from the frozen invocation/resolved interpreter;
    # never search an operator home for a compatible Python or package cache.
    for path in dict.fromkeys(['/usr', '/bin', '/lib', '/lib64', str(python.parent.parent),
                               str(base / 'bin'), str(base / 'lib')]):
        if request['phase'] == 'replay':
            if Path(path) == Path.home():raise ValueError('Replay runtime must not expose the user home')
        if Path(path).exists():args += ['--ro-bind', path, path]
    args += ['--ro-bind', str(stage / 'inputs'), '/inputs', '--ro-bind', adapter, '/delivery.py']
    objects = request.get('object_refs',{})
    if not isinstance(objects,dict):raise ValueError('Frozen delivery object references changed')
    if request['phase'] in {'audit','smoke','replay'} and 'source.py' not in objects:
        raise ValueError('Frozen selected delivery source is missing')
    for label, reference in objects.items():
        if (not isinstance(label,str) or not label or Path(label).is_absolute()
                or Path(label).as_posix()!=label or '\\' in label or '\x00' in label
                or any(part in {'','.','..'} for part in label.split('/'))
                or not isinstance(reference,dict) or set(reference)!={'path','size_bytes'}
                or not isinstance(reference['path'],str) or not Path(reference['path']).is_absolute()
                or type(reference['size_bytes']) is not int or reference['size_bytes']<0):
            raise ValueError('Frozen delivery object reference changed')
        original = _exact(reference['path'])
        point = stage/'inputs'/label
        if (point.resolve()!=point or not point.is_file()
                or original.stat().st_size!=reference['size_bytes']):
            raise ValueError('Frozen delivery object original differs')
        args += ['--ro-bind', str(original), '/inputs/'+label]
    phase = request['phase']
    if phase not in {'audit','smoke','replay'}:target(request['target'])
    args += ['--bind' if phase in {'audit', 'resolve', 'smoke', 'replay'} else '--ro-bind', str(stage / 'scratch'), '/scratch']
    if phase == 'resolve':
        for scope in request['source_roots']:
            path = Path(scope)
            if path.resolve() != path or not path.is_dir():raise ValueError('Original read scope missing or changed')
            args += ['--ro-bind', str(path), str(path)]
    elif phase not in {'audit', 'smoke', 'replay'}:
        args += ['--dir', '/target']
        for key in ('runs', 'submission'):
            path = Path(request['target'][key])
            if path.resolve() != path or not path.is_dir():raise ValueError('Declared target missing or changed')
            args += ['--bind' if phase == 'promote' else '--ro-bind', str(path), '/target/' + key]
    args += ['--setenv', 'PATH', os.defpath, '--setenv', 'LANG', 'C.UTF-8',
             '--setenv', 'OPENBLAS_NUM_THREADS', '1', '--setenv', 'OMP_NUM_THREADS', '1',
             '--chdir', '/scratch', '--', str(python), '-B', '-I', '/delivery.py', '--leaf']
    # Preserve stdin for the leaf; no host secrets, control root or network mounts.
    os.execv(bwrap, args)


if __name__ == '__main__':
    request=json.load(sys.stdin)
    if sys.argv[1] == '--isolate':
        # Recreate a finite stdin after parsing the launcher request.
        import tempfile
        with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as stream:
            json.dump(request,stream,allow_nan=False);stream.flush();stream.seek(0);os.dup2(stream.fileno(),0)
            isolate(request, str(Path(sys.argv[2]).resolve()))
    elif sys.argv[1:] == ['--leaf']:
        print(json.dumps(leaf(request), allow_nan=False))
    else:
        raise ValueError('Delivery must use its isolated execution entry')
