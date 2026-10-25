"""Optional cov CLI observer/wrapper. Never imports models or hidden scoring data."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
import uuid
import fcntl

if __package__ in {None, ''}:
    checkout=Path(__file__).resolve().parents[3]
    if (checkout/'pyproject.toml').is_file() and (checkout/'src/scidiscovery').is_dir():
        sys.path.insert(0,str(checkout/'src'))
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
MAX_BYTES = 256_000
MAX_ASSET = 32 * 1024 * 1024
MAX_SCAN = 4 * 1024 * 1024
MAX_CALLS = 128


from scidiscovery.research_loop.experiments import IntegrityError


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode()


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _read(path, limit=None):
    path = Path(path)
    if path.resolve() != path or not path.is_file() or limit is not None and path.stat().st_size > limit:
        raise IntegrityError('Exact bounded original required')
    return path.read_bytes()


def _write(path, value):
    with Path(path).open('x',encoding='utf-8') as stream:
        json.dump(value,stream,ensure_ascii=False,allow_nan=False,sort_keys=True);stream.write('\n');stream.flush();os.fsync(stream.fileno())


def _proof(path, limit=None, *, context=None):
    from scidiscovery.research_loop import experiments
    return experiments.proof(path,limit,context=context)


def _check(proof):
    actual = _proof(Path(proof['path']))
    if any(actual.get(key)!=value for key,value in proof.items()):raise IntegrityError('Original call proof changed')
    return Path(proof['path'])


def _rows(path):
    path=Path(path);rows=[]
    if path.exists():
        from scidiscovery.research_loop import contracts as c
        with c.regular(path).open(encoding='utf-8') as stream:
            rows=[json.loads(line) for line in stream if line.strip()]
    ids = [row['run_id'] for row in rows]
    if any(type(i) is not int or i < 1 for i in ids) or len(set(ids)) != len(ids):
        raise IntegrityError('Public ledger run identities are ambiguous')
    return rows


def _config(path):
    data = _read(path);value = json.loads(data)
    keys = {'contract_version','record_dir','runs_root','run_argv','scope','parents','evaluation_key','feedback_version','execution_interface','max_seconds','admission','storage_policy'}
    if 'native_attempt' in value:keys.add('native_attempt')
    if 'breadth' in value:keys.add('breadth')
    if 'engineering_repair' in value:keys.add('engineering_repair')
    if 'receipt_channel' in value:keys.add('receipt_channel')
    if 'engineering_check_reserve_seconds' in value:keys.add('engineering_check_reserve_seconds')
    if 'worker_view_version' in value:
        if type(value['worker_view_version']) is not int or value['worker_view_version']!=1:
            raise ValueError('Explicit scientific worker view version required')
        keys.add('worker_view_version')
    if 'host_launch_implementation' in value:
        implementations=value['host_launch_implementation']
        if (not isinstance(implementations,list) or any(not isinstance(item,dict)
                or set(item)!={'path','size_bytes'} or not isinstance(item['path'],str)
                or not Path(item['path']).is_absolute()
                or type(item['size_bytes']) is not int or item['size_bytes']<0 for item in implementations)):
            raise ValueError('Host launcher implementation metadata is malformed')
        keys.add('host_launch_implementation')
    if set(value) != keys or value['contract_version'] != 2 or value['feedback_version'] != 1:
        raise ValueError('Explicit wrapper contract2 configuration required')
    from scidiscovery.research_loop import replay_check, storage_policy as storage
    if storage.configuration(value['storage_policy']) != value['storage_policy']:
        raise ValueError('Exact frozen storage policy required')
    replay_check.reserve_seconds(value)
    scope = value['scope']
    if set(scope) != ({'step','role','job_ref','execution_key','mechanism_ref','handoff_protocol'} if 'handoff_protocol' in scope else {'step','role','job_ref','execution_key','mechanism_ref'}) or type(scope['step']) is not int or scope['step'] <= 0 or scope['role'] not in {'H','W','A','E'}:
        raise ValueError('Exact frozen scientific execution scope required')
    for key in ('job_ref','execution_key'):
        if not isinstance(scope[key],dict) or set(scope[key])!={'path'} or not isinstance(scope[key]['path'],str) or not Path(scope[key]['path']).is_absolute():raise ValueError('Exact execution reference required')
    if not isinstance(value['parents'],list):
        raise ValueError('Bounded frozen parents required')
    for key in ('record_dir','runs_root'):
        p = Path(value[key])
        if not p.is_absolute() or p.resolve() != p:raise ValueError('Original namespace directories required')
    if not isinstance(value['run_argv'],list) or not value['run_argv'] or not Path(value['run_argv'][0]).is_absolute():
        raise ValueError('Exact run executable required')
    return value


def _completed(directory, *, verify_assets=True, verify_source=True):
    path = directory/'completed.json'
    if not path.exists():return None
    result = json.loads(_read(path))
    accepted = json.loads(_read(directory/'accepted.json'))
    controls={directory/name for name in ('accepted.json','stdout.raw','stderr.raw','receipt.json','row.json','terminal.json')}
    source=Path(accepted['script']['path'])
    temporary=None
    if 'code_version' in accepted:
        from scidiscovery.research_loop import experiments
        root=experiments._code_version_binding(accepted.get('config_record'),accepted)
        temporary=experiments._code_export_entry(root,accepted)
    model=Path(result['model']['path']) if result['model'] is not None else None
    for proof in result['proofs']:
        if Path(proof['path']) not in controls|{source,model}:
            raise IntegrityError('Progress proof escaped this exact call')
        if proof==temporary:continue
        if Path(proof['path']) in controls or (verify_assets
                and (Path(proof['path']) != source or verify_source)):
            _check(proof)
    if result['model'] is not None and verify_assets:_check(result['model'])
    if result.get('mechanism_binding')!=accepted.get('mechanism_binding'):
        raise IntegrityError('Completion changed accepted scientific mechanism')
    if result['call_id'] != accepted['call_id'] or result['scope'] != accepted['scope'] or result['parent'] != accepted['parent'] or result['change_intent'] != accepted['change_intent']:
        raise IntegrityError('Completion differs from accepted scientific context')
    if result['status'] in {'known_success','known_failed'}:
        # Canonical completion comes only from the immutable same-call transport.
        row=json.loads(_read(directory/'row.json'))
        receipt=_receipt_row(directory,accepted)
        terminal=json.loads(_read(directory/'terminal.json'))
        ok=result['status']=='known_success'
        if (row!=receipt or row.get('script')!=accepted['source_script']
                or type(row.get('run_id')) is not int or row['run_id']<1
                or result['run_id']!=row['run_id'] or result['candidate_id']!=f'run-{row["run_id"]}'
                or row.get('ok') is not ok or terminal.get('returncode')!=(0 if ok else 1)
                or type(terminal.get('returncode')) is not int
                or any(terminal.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding'))
                or terminal.get('cleanup_confirmed') is not True or terminal.get('transport_complete') is not True
                or (result['model'] is not None)!=ok
                or (ok and row.get('model_path')!=str(Path(accepted['runs_root'])/f'model_run{row["run_id"]}.pkl'))):
            raise IntegrityError('Completion differs from immutable same-call transport')
        # Existing completions keep their saved call/model.pkl meaning. New
        # receipts bind the actual run output, without creating another model.
        if ok and (model not in {Path(row['model_path']),directory/'model.pkl'}
                   or result['model'] not in result['proofs']):
            raise IntegrityError('Completed model differs from this exact call original')
        required={str(path) for path in controls}
        if (not required.issubset({proof['path'] for proof in result['proofs']})
                or temporary is None and accepted['script'] not in result['proofs']):
            raise IntegrityError('Completion lacks original transport proofs')
    elif result['status']!='unknown' or any(result[key] is not None for key in ('candidate_id','run_id','model')):
        raise IntegrityError('Unknown completion acquired an experiment identity')
    return result


def _calls(config):
    root = Path(config['record_dir'])/'calls'
    if root.resolve() != root:
        raise IntegrityError('Call namespace directory changed')
    paths = sorted(root.iterdir()) if root.exists() else []
    for path in paths:
        if path.resolve() != path or not path.is_dir():raise IntegrityError('Call directory changed')
    return paths


def _parent(config, name, new):
    if new:return dict(status='declared_root',candidate_id=None,source='none'),None
    if name is None:return dict(status='unknown',candidate_id=None,source='none'),None
    options = [(row['name'],row['candidate_id'],row['model'],'sealed',row['binding']) for row in config['parents']]
    for path in _calls(config):
        result = _completed(path, verify_assets=False)
        if result and result['status'] == 'known_success':
            options.append((result['candidate_id'],result['candidate_id'],result['model'],'same_node_completed_call',_binding(result,_proof(path/'completed.json'))))
    matches = [row for row in options if row[0] == name]
    if len(matches) != 1:raise ValueError('parent_plan_unresolved')
    _, candidate, model, source, binding = matches[0]
    _check(model)
    return dict(status='declared',candidate_id=candidate,source=source),binding


def _binding(context, completion):
    root = Path(context['scope']['job_ref']['path']).parent.parent
    def reference(value):
        path = Path(value['path'])
        return dict(path=str(path if path.is_absolute() else root/path),size_bytes=value['size_bytes'])
    return dict(call_id=context['call_id'],step=context['scope']['step'],
                completion_ref=reference(completion),model_ref=reference(context['model']))


def _receipt_row(directory,accepted):
    """Read one exact independent receipt; logs never qualify an execution."""
    try:
        value=json.loads(_read(directory/'receipt.json'))
    except (OSError,ValueError):return None
    from cov_execution.receipt import envelope
    if not isinstance(value,dict) or value!=envelope(accepted,value.get('row')):return None
    return value['row']


def _receipt(directory, accepted, rows, returncode, cleanup, transport_complete=True, *, storage_root, policy, context):
    if not transport_complete:return None
    row=_receipt_row(directory,accepted)
    if not isinstance(row,dict) or type(row.get('run_id')) is not int or row.get('script') != accepted['source_script']:
        return None
    matches = [item for item in rows if item.get('run_id') == row['run_id']]
    if len(matches) != 1 or matches[0] != row:return None
    if cleanup is not True or type(returncode) is not int or returncode not in (0,1) or row.get('ok') is not (returncode == 0):return None
    terminal=json.loads(_read(directory/'terminal.json'))
    if (terminal.get('returncode')!=returncode or type(terminal.get('returncode')) is not int
            or terminal.get('cleanup_confirmed') is not True or terminal.get('transport_complete') is not True
            or any(terminal.get(key)!=accepted.get(key) for key in ('call_id','scope','mechanism_binding'))):
        return None
    if (directory/'completed.json').exists():
        return _completed(directory,verify_source='code_version' not in accepted)
    if 'code_version' not in accepted and any(
            _proof(Path(accepted['script']['path']),context=context).get(key)!=value
            for key,value in accepted['script'].items()):
        raise IntegrityError('Executed source snapshot changed')
    if 'code_version' in accepted:
        from scidiscovery.research_loop import experiments
        root=experiments._code_version_binding(accepted.get('config_record'),accepted)
        if experiments._code_export_entry(root,accepted) is None:
            raise IntegrityError('Executed code lost its accepted export binding')
    elif any(_proof(Path(accepted['source_script']),context=context).get(key)!=value for key,value in
            dict(path=accepted['prepared']['local_path'],size_bytes=accepted['prepared']['size_bytes']).items()):
        raise IntegrityError('Executed source changed')
    model = None
    if row['ok']:
        path = Path(row.get('model_path',''))
        if path != Path(accepted['runs_root'])/f'model_run{row["run_id"]}.pkl':
            raise IntegrityError('Returned artifact is outside the public run scope')
        from scidiscovery.research_loop import storage_policy as storage
        storage.Inventory(policy, context=context).file(path.stat().st_size)
        model=_proof(path, context=context)
        # Receipt, row and terminal above establish this call's ownership and
        # stopped writer. External or unconsumed assets never enter this seal.
        if path.stat().st_mode&0o777!=0o400:path.chmod(0o400)
        if _proof(path, context=context)!=model:
            raise IntegrityError('Stopped model original changed during sealing')
    from scidiscovery.research_loop import shared_originals
    payload = (json.dumps(row,ensure_ascii=False,allow_nan=False,sort_keys=True)+'\n').encode()
    frozen = shared_originals.publish_bytes(storage_root, payload, context=context, policy=policy)
    shared_originals.link(frozen, directory/'row.json',
        size_bytes=len(payload), root=storage_root, context=context, policy=policy)
    proofs = [_proof(directory/name, context=context) for name in ('accepted.json','stdout.raw','stderr.raw','receipt.json','row.json','terminal.json')]
    if 'code_version' not in accepted:proofs.append(accepted['script'])
    if model:proofs.append(model)
    return dict(call_id=accepted['call_id'],status='known_success' if row['ok'] else 'known_failed',
        candidate_id=f'run-{row["run_id"]}',run_id=row['run_id'],model=model,parent=accepted['parent'],
        change_intent=accepted['change_intent'],note='Exact same-call public receipt; not audit qualification',
        scope=accepted['scope'],proofs=proofs)


def status(config, *, verify_assets=True):
    results = []
    for directory in _calls(config):
        result = _completed(directory, verify_assets=verify_assets)
        accepted = json.loads(_read(directory/'accepted.json'))
        if result is None:
            accepted = json.loads(_read(directory/'accepted.json'))
            if result is None:
                result = dict(call_id=accepted['call_id'],status='unknown',candidate_id=None,run_id=None,model=None,
                    parent=accepted['parent'],change_intent=accepted['change_intent'],scope=accepted['scope'],proofs=[],note='Accepted original has no verified completion; never replay')
        if result['status']=='unknown' and 'mechanism_binding' in accepted:
            result['mechanism_binding']=accepted['mechanism_binding']
        results.append(result)
    return results


def run(config, script, parent=None, new=False, change=None, mechanism=None):
    from scidiscovery.research_loop.experiments import dispatch
    return dispatch(config,script,parent=parent,new=new,change=change,mechanism=mechanism)


def progress(request):
    """Only current accepted call metadata; no models, source bodies or raw logs."""
    root, config, scope = Path(request['root']), request['config'], request['scope']
    calls = Path(config['calls'])
    if calls != Path('project/nodes') or calls.is_absolute() or '..' in calls.parts or (root / calls).resolve() != root / calls:
        raise IntegrityError('Live observation escaped its configured original')
    from scidiscovery.research_loop import experiment_client
    return _progress(dict(record_dir=str(experiment_client.record_directory(root, request['step'])),
                          evaluation_key=config['evaluation_key']), scope)


def _progress(config, scope):
    from scidiscovery.research_loop import runtime
    try:
        directories = _calls(config)
    except OSError as error:
        raise IntegrityError('Live call namespace cannot be read') from error
    rows = []
    counts = dict.fromkeys(('accepted', 'running', 'known_success', 'known_failed', 'unknown'), 0)
    for directory in directories:
        state, accepted, completed = 'unknown', None, None
        metric = wall = parent = None
        try:
            completed = _completed(directory, verify_assets=False)
            if not (directory / 'accepted.json').exists():
                raise FileNotFoundError('Acceptance has not been published')
            accepted = json.loads(_read(directory / 'accepted.json'))
            if not isinstance(accepted, dict) or not isinstance(accepted.get('scope'), dict):
                raise IntegrityError('Published live acceptance is malformed')
            bound = accepted['scope']
            if (type(bound['step']) is not int or bound['step'] != scope['step'] or bound['role'] != scope['role'] or bound['job_ref'] != scope['job_ref']
                    or bound['execution_key'] not in scope['execution_keys'] or bound['mechanism_ref'] != scope['mechanism_ref']
                    or accepted['call_id'] != directory.name):
                raise IntegrityError('Live call differs from the exact accepted scientific task')
            if not isinstance(accepted.get('parent'), dict):
                raise IntegrityError('Published live parent is malformed')
            parent = accepted['parent']['candidate_id']
            from scidiscovery.research_loop import candidate_refs
            if parent is not None and not candidate_refs.valid_parent_id(parent):
                raise IntegrityError('Published live parent is malformed')
            if completed is not None:
                state = completed['status']
            elif (directory / 'process.json').exists():
                identity = json.loads(_read(directory / 'process.json'))
                if not isinstance(identity, dict):
                    raise IntegrityError('Published live process identity is malformed')
                from scidiscovery.research_loop import experiments
                key = dict(path=str(directory/'accepted.json'))
                if identity.get('task_ref') != key:
                    raise IntegrityError('Live process binding differs from the accepted call')
                if runtime.process_matches(identity, key):
                    state = 'running'
            if state in {'known_success', 'known_failed'}:
                original = json.loads(_read(directory / 'row.json'))
                metric = original.get('val_score')
                wall = original.get('wall_seconds')
                metric = metric if _finite(metric) else None
                wall = wall if _finite(wall) else None
        except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError):
            state, completed = 'unknown', None  # Half-written metadata never proves execution or completion.
            metric = wall = None
        except OSError as error:
            raise IntegrityError('Published live original cannot be read') from error
        except IntegrityError:
            raise
        except (ValueError, KeyError, TypeError, AttributeError, UnicodeEncodeError, RecursionError, OverflowError) as error:
            raise IntegrityError('Published live metadata is malformed') from error
        counts[state] += 1
        rows.append(dict(call_id=directory.name, status=state,
            candidate_id=completed['candidate_id'] if completed is not None else None,
            evaluation_key=config['evaluation_key'], metric=metric, direction='max', wall_seconds=wall,
            parent_candidate=parent))
    return dict(version=1, scope=scope, calls=rows[-16:], counts=counts, omitted_count=max(0, len(rows)-16),
                coverage='partial' if counts['unknown'] else 'complete', observed_at=time.time())


def observe(request):
    if request.get('progress_version') == 1:
        return progress(request)
    config = request['config'];root = Path(request['root'])
    if set(config) != {'runs','calls','evaluation_key'}:raise ValueError('Explicit public run/call roots required')
    for key in ('runs','calls'):
        path = Path(config[key])
        if path.is_absolute() or '..' in path.parts or (root/path).resolve() != root/path:raise IntegrityError('Observer escaped original instance')
    if Path(config['calls']) != Path('project/nodes'):
        raise IntegrityError('Observer requires the original scientific task namespace')
    rows = _rows(root/config['runs']/'results.jsonl');known=set(request.get('known_candidates',[]))
    contexts=[];models={}
    from scidiscovery.research_loop import experiment_client
    directories = [directory for node in sorted((root/config['calls']).glob('*'))
        if node.name.isdecimal()
        for directory in sorted((experiment_client.record_directory(root, int(node.name)) / 'calls').glob('*'))]
    for directory in directories:
        result = _completed(directory,verify_assets=False)
        accepted=json.loads(_read(directory/'accepted.json'))
        if (result is not None and result['scope']['step']==request['step']
                and result['call_id'] not in set(request.get('known_contexts',[]))):
            result=_completed(directory)
        if result is not None:
            result['proofs'].append(_proof(directory/'completed.json'))
        if result is None:
            accepted=json.loads(_read(directory/'accepted.json'))
            result=dict(call_id=accepted['call_id'],scope=accepted['scope'],status='unknown',candidate_id=None,run_id=None,model=None,
                parent=accepted['parent'],change_intent=accepted['change_intent'],proofs=[_proof(directory/'accepted.json')],note='Accepted original without exact completion')
        if result['status']=='unknown' and 'mechanism_binding' in accepted:result['mechanism_binding']=accepted['mechanism_binding']
        if 'code_version' in accepted and result['status'] in {'known_success','known_failed'}:
            from scidiscovery.research_loop import experiments
            _,version=experiments._source_projection(accepted['config_record'],accepted)
            result['artifact']=None
            result['source_code_version']=version
        if result['status']=='known_success':models[result['candidate_id']]=dict(result['model'])
        if result['scope']['step'] != request['step']:continue
        if result and result['call_id'] not in set(request.get('known_contexts',[])):
            # _completed checks immutable proofs; relative names let core archive once.
            for proof in result['proofs']:proof['path']=str(Path(proof['path']).relative_to(root))
            if result['model']:result['model']['path']=str(Path(result['model']['path']).relative_to(root))
            contexts.append(result)
    candidates=[];partial=False
    for row in rows:
        cid=f'run-{row["run_id"]}'
        if cid in known:continue
        candidates.append(dict(id=cid,label=f'Public experiment {row["run_id"]}',implementation=None,evaluation_key=config['evaluation_key'],
            metric=row.get('val_score'),direction='max',status='ok' if row.get('ok') is True else 'failed',evidence=[str(Path(config['runs'])/'results.jsonl')]))
    bound={item['candidate_id'] for item in contexts if item['candidate_id']}
    partial=partial or any(row['id'] not in bound for row in candidates) or any(item['status']=='unknown' for item in contexts)
    actual=None;submission_note=''
    submitted=root/config['runs']/'submitted.json';model=root/config['runs']/'submitted_model.pkl'
    if submitted.exists():
        fact=json.loads(_read(submitted))
        if not model.exists():
            partial=True;submission_note=' Submitted record lacks an exact artifact; actual identity unknown.'
        else:
            artifact=_proof(model);run_id=fact.get('run_id')
            candidate=f'run-{run_id}' if type(run_id) is int and run_id>0 else None
            original=models.get(candidate)
            matched=False
            marker_path=root/config['runs']/'rootless-delivery-operation.json'
            if original is not None and marker_path.exists():
                marker=json.loads(_read(marker_path))
                reference=marker.get('artifact_origin')
                info=model.stat()
                generation=[info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns]
                if (marker.get('candidate_id')==candidate and marker.get('audit')==fact
                        and isinstance(marker.get('operation_id'),str) and marker['operation_id']
                        and isinstance(reference,dict) and reference==original
                        and marker.get('published',{}).get('run_model')==generation):
                    matched=True
            if (fact.get('model_path')!=str(model) or candidate not in known|{row['id'] for row in candidates}
                    or not matched):
                candidate=None;partial=True;submission_note=' Submitted artifact is observed but candidate binding is unknown.'
            actual=dict(candidate_id=candidate,artifact=str(model.relative_to(root)),evidence=[str(submitted.relative_to(root))])
    return dict(candidates=candidates,current_candidate=None,actual_submission=actual,contexts=contexts,
        coverage=dict(status='partial' if partial else 'complete',note='Managed declared relations only; ordinary shell/parallel or unknown calls remain uncovered.'+submission_note))



def configure(root, step, run_argv, runs_root, output, *, receipt_channel='public_runner_main', engineering_check_reserve_seconds=0):
    """Trusted launcher's host-side preparation, before entering its namespace."""
    from scidiscovery.research_loop.protocol import require_current
    require_current(root)
    from scidiscovery.research_loop import contracts as _contracts
    require_current(root,_contracts.read(_contracts.regular(Path(root)/'jobs'/f'{step:03d}.json')))
    from scidiscovery.research_loop import contracts as c, execution, lineage, experiment_client
    root=Path(root).resolve();job=c.read(c.regular(root/'jobs'/f'{step:03d}.json'))
    c._validate_inputs(root,c.workspace(root,step),job)
    record=execution.record(root,step)
    if c.read(c.regular(record/'native-intent.json')).get('task_ref')!=c.record_ref(root/'jobs'/f'{step:03d}.json'):
        raise IntegrityError('Launcher preparation has no accepted scientific task')
    parents=[]
    for candidate,item in lineage.build(root,step)['candidates'].items():
        context=item.get('context');model=context.get('model') if context else None
        if context and context['status']=='known_success' and model and model.get('retention')=='archived':
            # The qualified namespace retains the original public runs alias.
            path=Path(runs_root)/f'model_run{context["run_id"]}.pkl'
            from scidiscovery.research_loop import originals
            producer=originals.node(root,context['scope']['step'])
            if producer.inherited:
                source=originals.source_for_ref(producer,model)
                path=originals.resolve_ref(source,dict(model,path=model['snapshot']))
            completion=next((p for p in context['proofs'] if Path(p['path']).name=='completed.json'),None)
            if completion is None:continue
            parents.append(dict(name=candidate,candidate_id=candidate,model=dict(path=str(path),size_bytes=model['size_bytes']),
                                binding=_binding(context,completion)))
    from scidiscovery.research_loop.protocol import stage_scope
    value=dict(contract_version=2,feedback_version=1,record_dir=str(experiment_client.record_directory(root,step)),runs_root=str(runs_root),run_argv=run_argv,
        scope=stage_scope(root,job),receipt_channel=receipt_channel,
        parents=parents,evaluation_key='covshift-public-val-v1')
    from scidiscovery.research_loop import replay_check
    replay_check.reserve_seconds(dict(engineering_check_reserve_seconds=engineering_check_reserve_seconds))
    if engineering_check_reserve_seconds!=0:
        value['engineering_check_reserve_seconds']=engineering_check_reserve_seconds
    from scidiscovery.research_loop import experiments
    value=experiments.configure(root,step,'cov_execution:experiment',value,output)
    return dict(status='configured',config=str(output))

def public_feedback(config, result, *, include_model=False):
    value={key:result[key] for key in ('call_id','status','candidate_id','run_id','parent','change_intent','note')}
    if include_model:value['model']={'retained':True} if result['model'] else None
    from scidiscovery.research_loop import replay_check
    value['engineering_check'] = replay_check.public(config, result['call_id'])
    if result['status'] in {'known_success','known_failed'}:
        path=Path(config['record_dir'])/'calls'/result['call_id']/'row.json'
        data=_read(path)
        proof=next((item for item in result['proofs'] if item['path']==str(path)),None)
        if proof is None or len(data)!=proof['size_bytes']:
            raise IntegrityError('Public feedback original changed')
        row=json.loads(data)
        value['evaluation_key']=config['evaluation_key']
        if isinstance(row.get('metric'),str):value['metric']=row['metric'].encode()[:80].decode('utf-8',errors='ignore')
        for key in ('val_score','val_acc','wall_seconds'):
            number=row.get(key)
            if _finite(number):value[key]=number
        if isinstance(row.get('error'),str):value['error']=row['error'].encode()[:160].decode('utf-8',errors='ignore')
    return value


def main(argv=None):
    if argv is None and len(sys.argv)==1:
        try:print(json.dumps(observe(json.load(sys.stdin)),allow_nan=False))
        except IntegrityError as error:print(json.dumps(dict(integrity_error=str(error))))
        return 0
    if argv is None and len(sys.argv)>1 and sys.argv[1]=='configure':
        p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('--root',type=Path,required=True);p.add_argument('--step',type=int,required=True)
        p.add_argument('--run-argv-json',required=True);p.add_argument('--runs-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
        print(json.dumps(configure(a.root,a.step,json.loads(a.run_argv_json),a.runs_root,a.output)));return 0
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',type=Path,required=True)
    sub=parser.add_subparsers(dest='command',required=True);r=sub.add_parser('run');r.add_argument('--script',type=Path)
    group=r.add_mutually_exclusive_group();group.add_argument('--parent-plan');group.add_argument('--new-plan',action='store_true')
    r.add_argument('--change-intent');r.add_argument('--mechanism');sub.add_parser('lineage-status');args=parser.parse_args(argv)
    try:
        config=_config(args.config)
        if args.command=='lineage-status':
            from scidiscovery.research_loop import experiments
            result = experiments.progress(config)
            code=0
        else:
            script=args.script
            if script is None:
                default=os.environ.get('EXP_DEFAULT_SCRIPT')
                if not default or not Path(default).is_absolute():
                    raise ValueError('Absolute EXP_DEFAULT_SCRIPT is unavailable; pass --script explicitly')
                script=Path(default)
            result=run(config,script,args.parent_plan,args.new_plan,args.change_intent,args.mechanism)
            code={'known_success':0,'known_failed':1,'unknown':2}.get(result['status'],2)
    except IntegrityError as error:result=dict(status='integrity_error',note=str(error));code=3
    except ValueError as error:result=dict(status='unresolved',note=str(error));code=2
    from scidiscovery.research_loop import feedback
    try:
        if args.command=='run' and 'scope' in result:
            result=public_feedback(config,result,include_model=True)
        if 'config' in locals():
            from scidiscovery.research_loop import contracts as c, experiment_client
            root = experiment_client.root_for_configuration(config, allow_standalone=True)
            work = Path(config['record_dir']).parent.parent if root is None else c.workspace(root, config['scope']['step'])
            result['feedback'] = dict(version=1, budget=feedback.budget_hint(work),
            qualification='Progress metadata does not establish source/model integrity or audit qualification.',
            notification='Available only at a tool return or explicit query; no mid-turn message channel.')
    except (IntegrityError, feedback.history.ObserverIntegrityError) as error:result=dict(status='integrity_error',note=str(error));code=3
    print(json.dumps(result,allow_nan=False));return code


if __name__=='__main__':raise SystemExit(main())
