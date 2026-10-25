"""Append terminal evidence for a sealed unknown call; never rewrite its science."""
import copy
from pathlib import Path

from . import budget, contracts as c, execution, experiments as e, history, experiment_client

DIRECTORY = 'host-history/execution-recoveries'


def prefix(root,step):
    from . import originals
    path=Path(root)/'jobs'/f'{step:03d}.json'
    if path.exists():return originals.recovery_refs(originals.node(root,step),c.read(c.regular(path)))
    directory=Path(root)/DIRECTORY
    paths=sorted([*directory.glob('*/*.json'), *directory.glob('*/*/[0-9]*.json')])
    local = [dict(path=path.relative_to(root).as_posix()) for path in paths]
    inherited = originals.frozen_recovery_refs(root, step) if originals._manifest(root) is not None else []
    return inherited + [ref for ref in local if ref not in inherited]


def validate(root,refs,*,originals=True):
    if not isinstance(refs,list):raise ValueError('Execution recovery prefix must be a list')
    values=[];seen=set();chains={};sources={};pending={}
    for ref in refs:
        if set(ref) not in ({'path'}, {'path','origin_identity'}, {'path','size_bytes','origin_identity'}):
            raise ValueError('Execution recovery prefix is malformed')
        if 'origin_identity' in ref:
            from . import originals as source_originals
            source = source_originals.for_origin(root, ref['origin_identity'])
            value = c.read(source_originals.resolve_ref(source, ref))
            _validate_inherited(root, source, ref, value, chains, originals=originals)
            values.append(value)
            continue
        path=Path(root)/ref['path']
        if (path.resolve()!=path or not path.is_relative_to(Path(root)/DIRECTORY)
                or c.regular(path,limit=None).resolve()!=path):
            raise history.ObserverIntegrityError('Frozen execution recovery original changed')
        value=c.read(path)
        if value.get('version')==2:
            _validate_event(root,ref,value,chains,originals=originals,sources=sources,pending=pending)
            values.append(value);continue
        step=value['source_step'];call=value['row']['call_id'];key=(step,call)
        if key in seen or path!=Path(root)/DIRECTORY/f'{step:03d}'/(call+'.json'):
            raise history.ObserverIntegrityError('Execution recovery identity is ambiguous')
        seen.add(key)
        if not originals:
            if value['row']['status'] not in {'known_success','known_failed','not_submitted'}:
                raise history.ObserverIntegrityError('Legacy recovery has no terminal metadata')
            values.append(value);continue
        job,_,seal,sha=history._source(root,step)
        if sha!=value['seal_ref'] or not seal.get('observations'):
            raise history.ObserverIntegrityError('Execution recovery source seal changed')
        old=c.read(Path(root)/seal['observations']['path'])
        matches=[row for row in old.get('managed_calls',[]) if row['call_id']==call]
        if len(matches)!=1 or matches[0]['status']!='unknown' or matches[0]!=value['old_row']:
            raise history.ObserverIntegrityError('Execution recovery has no exact sealed unknown origin')
        row=value['row']
        if (row['accepted']!=matches[0]['accepted'] or row['scope']!=matches[0]['scope']
                or row.get('mechanism_binding')!=matches[0].get('mechanism_binding')
                or row['status'] not in {'known_success','known_failed','not_submitted'}):
            raise history.ObserverIntegrityError('Execution recovery changed original reservation')
        history.validate_managed(root,job,[row],verify_files=False);values.append(value)
    return values


def _validate_inherited(root, context, ref, value, chains, *, originals=True):
    """Read an already accepted original recovery; never operate its provider."""
    from . import originals as o
    step = value['source_step']
    call = value.get('call_id', (value.get('row') or {}).get('call_id'))
    key = (o.identity_key(context.origin_identity), step, call)
    source = o.dependency(context, step, seal_ref=value['seal_ref'])
    job = o.source_job(source)
    seal = c.read(o.source_record(source, 'result-identity.json'))
    if history.source_seal_ref(source) != value['seal_ref']:
        raise history.ObserverIntegrityError('Inherited recovery source seal changed')
    def read_ref(proof):
        return c.read(o.resolve_ref(o.source_for_ref(context, proof), proof))
    if value.get('version') == 2:
        local_key = (step, call)
        isolated = {} if key not in chains else {local_key: chains[key]}
        plain_ref = {field: ref[field] for field in ('path',)}
        _validate_event(root, plain_ref, value, isolated, originals=originals, source=source)
        chains[key] = isolated[local_key]
        return
    else:
        if ref['path'] != f'{DIRECTORY}/{step:03d}/{call}.json' or key in chains:
            raise history.ObserverIntegrityError('Inherited recovery identity is ambiguous')
        old = read_ref(seal['observations'])
        matches = [row for row in old.get('managed_calls', []) if row['call_id'] == call]
        row = value['row']
        if (len(matches) != 1 or matches[0]['status'] != 'unknown'
                or matches[0] != value['old_row']
                or row['accepted'] != matches[0]['accepted'] or row['scope'] != matches[0]['scope']
                or row.get('mechanism_binding') != matches[0].get('mechanism_binding')
                or row['status'] not in {'known_success', 'known_failed', 'not_submitted'}):
            raise history.ObserverIntegrityError('Inherited recovery changed its original reservation')
        chains[key] = dict(ref=ref, number=1, phase='terminal', value=value)
    if originals and row is not None:
        history.validate_managed_original(source, job, [row], controls_only=True)


def resolved(root,refs):
    return {key:value['row'] for key,value in call_view(root,[],refs).items()
            if value.get('row') is not None and value['row']['status'] in {'known_success','known_failed','not_submitted'}}


def _validate_event(root,ref,value,chains,*,originals=True,sources=None,pending=None,context=None,source=None):
    from . import qualification as q
    from .execution_resume import verify_pending_set, _source_path
    logical_root=Path(source.original_root) if source is not None else Path(root)
    read = (lambda proof: q._read(root, proof)) if source is None else (lambda proof: c.read(_source_path(source, proof)))
    step=value['source_step'];call=value['call_id'];key=(step,call)
    prior=chains.get(key)
    number=1 if prior is None else prior['number']+1
    expected=Path(root)/DIRECTORY/f'{step:03d}'/call/f'{number:06d}.json'
    if (Path(root)/ref['path']!=expected or value['previous']!=(prior['ref'] if prior else None)
            or value['phase'] not in {'resume_intent','accepted','status','terminal','not_submitted'}
            or value.get('origin') not in {'sealed_unprepared','sealed_preparation_unknown'}
            or prior is None and value['phase']!='resume_intent' and value.get('origin')!='sealed_preparation_unknown'
            or value.get('origin')=='sealed_preparation_unknown' and value['phase']=='resume_intent'
            or prior is not None and (value['phase']=='resume_intent' or prior['phase'] in {'terminal','not_submitted'})):
        raise history.ObserverIntegrityError('Execution recovery chain identity or phase changed')
    if prior is not None:
        for key_name in ('source_step','call_id','scope','seal_ref','pending_set','allocation','request','decision','admission','origin'):
            if value[key_name]!=prior['value'][key_name]:
                raise history.ObserverIntegrityError('Execution recovery changed its original dispatch bindings')
    request=read(value['request']);decision=read(value['decision'])
    admission=read(value['admission'])
    if (request['call_id']!=call or dict(path=str(logical_root/request['job']['path']))!=value['scope']['job_ref']
            or decision['request']!=value['request'] or admission['allocation']!=value['allocation']
            or admission['pending_set']!=value['pending_set']):
        raise history.ObserverIntegrityError('Execution recovery lost exact admission/decision originals')
    row=value.get('row')
    if value['phase']=='resume_intent':
        if row is not None:raise history.ObserverIntegrityError('Dispatch intent invented accepted execution')
    else:
        if not isinstance(row,dict) or row['call_id']!=call or row['scope']!=value['scope']:
            raise history.ObserverIntegrityError('Recovered execution escaped original call')
        if (value['phase']=='terminal' and row['status'] not in {'known_success','known_failed'}
                or value['phase']=='not_submitted' and row['status']!='not_submitted'
                or value['phase'] in {'accepted','status'} and row['status']!='unknown'):
            raise history.ObserverIntegrityError('Recovery phase has no verified execution fact')
        if prior is not None and prior['value'].get('row') is not None and row['accepted']!=prior['value']['row']['accepted']:
            raise history.ObserverIntegrityError('Recovered accepted identity changed')
    if originals:
        sources={} if sources is None else sources;pending={} if pending is None else pending
        if step not in sources:
            from . import originals as source_originals
            selected=source if source is not None else source_originals.node(root,step)
            c.validate_original(selected,controls_only=True)
            seal_path=source_originals.source_record(selected,'result-identity.json')
            sources[step]=(source_originals.source_job(selected),None,c.read(seal_path),history.source_seal_ref(selected))
        job,_,seal,sha=sources[step]
        if sha!=value['seal_ref'] or value['pending_set']!=seal.get('pending_executions'):
            raise history.ObserverIntegrityError('Execution recovery source seal changed')
        identity=(step,value['allocation']['path'])
        fresh=value['origin']=='sealed_unprepared'
        if identity not in pending:pending[identity]=verify_pending_set(root,seal,value['allocation'],require_complete=fresh,context=context,source=source)
        if pending[identity]['disposition']!=('unprepared' if fresh else 'possibly_prepared'):
            raise history.ObserverIntegrityError('Late dispatch has no positive original unprepared evidence')
        allocation=read(value['allocation'])
        if allocation['call_id']!=call or allocation['binding']['scope']!=value['scope']:
            raise history.ObserverIntegrityError('Execution recovery allocation changed')
        if row is not None:
            if source is None:history.validate_managed(root,job,[row],context=context,verify_files=False)
            else:history.validate_managed_original(source,job,[row],controls_only=True)
            if row.get('mechanism_binding')!=allocation['binding'].get('mechanism_binding'):
                raise history.ObserverIntegrityError('Recovered execution changed original scientific opportunity')
            accepted=(c.read(Path(root)/row['accepted']['snapshot']) if source is None else
                read(dict(path=row['accepted']['snapshot'])))
            selected=allocation['binding'];prepared=accepted['prepared']
            original=accepted['execution_authority']
            if source is None:q._check(original)
            authority=c.read(Path(original['path'])) if source is None else read(original)
            if source is None:records=experiment_client.record_directory(root,step)
            else:
                logical_root=Path(source.original_root)
                records=(logical_root/'host-runtime/experiment-control'/f'{value["scope"]["step"]:03d}'/'records'
                    if job.get('worker_view_version')==experiment_client.VERSION else source.logical_workspace/'work/lineage')
            script=accepted['script']
            script_bound=(isinstance(script,dict) and set(script) in (
                    {'path','size_bytes'},{'path','size_bytes','file_stat'})
                and isinstance(script.get('path'),str) and Path(script['path']).is_absolute()
                and type(script.get('size_bytes')) is int and script['size_bytes']>=0
                and isinstance(prepared.get('local_path'),str) and Path(prepared['local_path']).is_absolute()
                and type(prepared.get('size_bytes')) is int
                and script['size_bytes']==prepared['size_bytes'])
            if script_bound and 'file_stat' in script:
                script_bound=(isinstance(script['file_stat'],list) and len(script['file_stat'])==4
                    and all(type(part) is int for part in script['file_stat'])
                    and script['file_stat'][2]==script['size_bytes'])
            if 'code_version' in accepted:
                from . import code_versions
                try:
                    version=code_versions._version(accepted['code_version'])
                    logical=Path(version['logical_root']);entry=Path(version['entry'])
                    selected_code=(logical==Path('code') and accepted['interface'].get('executor')=='local_execution:command'
                        if version['selection']=='directory' else logical.parts[0]=='code' and len(entry.parts)==1)
                    source_bound=(selected_code and accepted.get('source_script')==str(c.workspace(logical_root,step)/logical/entry)
                        and not any(key in accepted for key in ('entry','source_reference','source_input')))
                    # These are accepted metadata, including transformed packets.
                    # Lookup/submission owns any later actual prepared-file read.
                    fixed_entry=e._code_export_entry(logical_root,accepted)
                    code_bound=(script_bound and 'file_stat' in script and source_bound
                        and (fixed_entry is not None or script['path']==prepared['local_path']))
                except (OSError,ValueError,KeyError,TypeError,AttributeError):
                    code_bound=False
            else:
                code_bound=(script_bound and (script['path']==str(records/'calls'/call/'source.py')
                    or 'file_stat' in script and script['path']==prepared['local_path']))
            if (accepted.get('native_attempt')!=selected['native_attempt'] or accepted.get('interface')!=selected['interface']
                    or authority['allocation']!=value['allocation'] or authority['request']!=value['request']
                    or authority['decision']!=value['decision'] or authority['scope']!=value['scope']
                    or authority['native_attempt']!=selected['native_attempt'] or authority['interface']!=selected['interface']
                    or authority['payload_alias']!=selected['payload_alias'] or accepted['execution_target']!=request['target']
                    or authority['target']!=request['target'] or prepared['name']!=call or not code_bound):
                raise history.ObserverIntegrityError('Recovered accepted/prepared authority lost its exact original allocation')
        if value.get('query') is not None:
            query=read(value['query'])
            submission=read(value['submission']) if value.get('submission') is not None else None
            if (query.get('call_id')!=call or query.get('state')!=value['native_state']
                    or submission is not None and (submission['call_id']!=call or submission['scope']!=value['scope']
                        or query.get('external_run_id')!=submission['external_run_id'])
                    or submission is None and query.get('external_run_id') is not None):
                raise history.ObserverIntegrityError('Recovery query lost its exact call/external/state original')
    chains[(step,call)]=dict(ref=ref,number=number,phase=value['phase'],value=value)


def append(root,base,phase,*,row=None,query=None,native_state=None,submission=None,context=None):
    """Only actual fact changes append; caller owns the controller writer."""
    root=Path(root);refs=prefix(root,budget.historical_step_limit(root)+1)
    values=validate(root,refs,originals=False)
    local_values=[value for ref,value in zip(refs,values) if 'origin_identity' not in ref]
    chain=[value for value in local_values if value.get('version')==2 and
           (value['source_step'],value['call_id'])==(base['source_step'],base['call_id'])]
    if chain and all(chain[-1].get(key)==item for key,item in dict(phase=phase,row=row,native_state=native_state,submission=submission).items()):
        return next(ref for ref in refs if 'origin_identity' not in ref and c.read(root/ref['path'])==chain[-1])
    directory=root/DIRECTORY/f'{base["source_step"]:03d}'/base['call_id']
    previous=None if not chain else dict(path=(directory/f'{len(chain):06d}.json').relative_to(root).as_posix())
    value=dict(base,version=2,previous=previous,phase=phase,row=row,query=query,native_state=native_state,submission=submission)
    path=directory/f'{len(chain)+1:06d}.json'
    # Validate the prospective value with the same validator before publication.
    ref=dict(path=path.relative_to(root).as_posix())
    chains={}
    for old_ref,old in zip(refs,values):
        if 'origin_identity' not in old_ref and old.get('version')==2:
            _validate_event(root,old_ref,old,chains,originals=False)
    _validate_event(root,ref,value,chains,context=context)
    if context is not None:context.remaining_seconds()
    c.write(path,value);path.chmod(0o400)
    return ref


def call_view(root,sources,refs,*,originals=True,validated=None,original_rows=None):
    """Single derived call projection; frozen source conclusions remain untouched."""
    sources=list(sources)
    result={**sealed_pending_rows(root,sources),**(original_rows or {})}
    for job,_,seal,_ in sources:
        from . import originals as source_originals
        source = source_originals.node(root, job['step'])
        observed=c.read(source_originals.resolve_ref(source, seal['observations'])) if seal.get('observations') else {}
        for row in observed.get('managed_calls',[]):
            result[(job['step'],row['call_id'])]=dict(row=row,phase='original',native_state=None)
    for value in (validate(root,list(refs),originals=originals) if validated is None else validated):
        if value.get('version')==2:
            result[(value['source_step'],value['call_id'])]=value
        else:
            result[(value['source_step'],value['row']['call_id'])]=dict(row=value['row'],phase='terminal',native_state=None)
    return result


def sealed_pending_rows(root,sources):
    """Small sealed control metadata only; callers explicitly select source seals."""
    from . import qualification as q
    result={}
    for job,_,seal,_ in sources:
        ref=seal.get('pending_executions')
        if ref is None:continue
        from . import originals
        source = originals.node(root, job['step'])
        value=c.read(originals.resolve_ref(source,ref))
        if (dict(path=str(Path(source.original_root)/value['job']['path']))!=seal['job_ref'] or value['job']['path']!=f'jobs/{job["step"]:03d}.json'):
            raise history.ObserverIntegrityError('Pending execution metadata escaped its sealed source')
        if (value.get('complete') is False and value.get('unavailable_reason')
                and not value.get('allocations') and not value.get('host_preparations')):
            raise history.ObserverIntegrityError('Original pending namespace was unavailable; no clean execution state can be proved')
        for row in value['allocations']:
            if row['disposition']!='possibly_prepared':continue
            allocation=c.read(originals.resolve_ref(originals.source_for_ref(source,row['allocation']),row['allocation']))
            if allocation['call_id']!=row['call_id'] or allocation['binding']['scope']['job_ref']!=seal['job_ref']:
                raise history.ObserverIntegrityError('Unknown preparation lost its original allocation')
            result[(job['step'],row['call_id'])]=dict(row=None,phase='original_prepare_unknown',native_state=None)
        # A host snapshot may precede any accepted call, including configurations
        # without execution authority. Its durable identity survives local discard
        # and remains an unknown original across later W nodes.
        for ref in value.get('host_preparations', []):
            from . import preparation_recovery
            binding=c.read(q._source_path(source,ref))
            config=c.read(q._source_path(source,binding['configuration']))
            call=binding.get('call_id');token=binding.get('snapshot_token')
            path=Path(ref['path'])
            path=path if path.is_absolute() else Path(source.original_root)/path
            records=Path(config['record_dir'])
            if (binding.get('version')!=1 or binding.get('kind')!='host_preparation_snapshot'
                    or not isinstance(call,str) or not e.ID.fullmatch(call) or call in {'.','..'}
                    or not isinstance(token,str) or not preparation_recovery.TOKEN.fullmatch(token)
                    or not records.is_relative_to(source.original_root)
                    or path!=records/'host-preparations'/call/(token+'.json')
                    or binding.get('records')!=str(records) or binding.get('scope')!=config.get('scope')
                    or binding['scope'].get('job_ref')!=seal['job_ref']
                    or binding.get('job')!=dict(path=str(Path(source.original_root)/value['job']['path']),
                        size_bytes=value['job']['size_bytes'])):
                raise history.ObserverIntegrityError('Unknown host preparation lost its original binding')
            result[(job['step'],call)]=dict(row=None,phase='original_prepare_unknown',native_state=None)
    return result


def _current_pending_sources(root):
    # Clock/new-call admission explicitly use current seals; frozen science never does.
    result=[]
    for path in sorted((Path(root)/'native-evidence').glob('*/result-identity.json')):
        seal=c.read(c.regular(path,limit=None))
        if seal.get('pending_executions') is not None:
            result.append((dict(step=int(path.parent.name)),None,
                {key:seal[key] for key in ('job_ref','pending_executions','observations') if key in seal},None))
    return result


def pending(root,*,unknown_only=False,ignore=None):
    from scidiscovery.operations.execution import PENDING_EXECUTION_STATES
    refs=prefix(root,budget.historical_step_limit(root)+1)
    for key,value in call_view(root,_current_pending_sources(root),refs,originals=False).items():
        if key==ignore:continue
        row=value.get('row')
        if row is None or row['status']=='unknown':
            from scidiscovery.operations.execution import execution_status
            if not unknown_only or execution_status(value.get('native_state')) not in PENDING_EXECUTION_STATES:
                return True
    return False


def create(root,step):
    from . import originals
    if originals.inherited(root, step):
        raise ValueError('Inherited calls require reconciliation at their exact original instance')
    job,_,seal,sha=history._source(root,step)
    if job['role']!='W' or not seal.get('observations'):raise ValueError('Recovery requires an exact sealed W observation')
    old=c.read(root/seal['observations']['path']);created=[];unresolved=[]
    for row in old.get('managed_calls',[]):
        if row['status']!='unknown':continue
        path=root/DIRECTORY/f'{step:03d}'/(row['call_id']+'.json')
        accepted=c.read(root/row['accepted']['snapshot'])
        from . import managed_wait
        _,config=managed_wait._config(root,job,accepted)
        directory=e.Records(config).directory(row['call_id'])
        if path.exists():
            ref=dict(path=path.relative_to(root).as_posix());validate(root,[ref]);created.append(ref)
            observed=directory/'execution-observation.json'
            if observed.exists():e._cleanup_terminal_code_export(config,row['call_id'],c.read(observed))
            continue
        retired=e.retirement(config,row['call_id'])
        if retired is None and not (directory/'execution-observation.json').exists():
            if not config['execution_interface']['capabilities']['query']:
                unresolved.append(row['call_id']);continue
            if e.record_observation(config,e.load(config),row['call_id']) is None:
                unresolved.append(row['call_id']);continue
        current=[item for item in history.managed_calls(root,job) if item['call_id']==row['call_id']]
        if len(current)!=1 or current[0]['status']=='unknown':raise ValueError('Original recovery terminal is unverified')
        value=dict(source_step=step,seal_ref=sha,old_row=row,row=current[0])
        c.write(path,value);path.chmod(0o400)
        ref=dict(path=path.relative_to(root).as_posix());validate(root,[ref]);created.append(ref)
        observed=directory/'execution-observation.json'
        if observed.exists():e._cleanup_terminal_code_export(config,row['call_id'],c.read(observed))
    return dict(status='execution_recovered' if created and not unresolved else 'execution_recovery_pending',
        recovered=len(created),unresolved=len(unresolved),qualification='Original execution or permanent non-submission facts only; old scientific conclusions and reservation counts are unchanged.')


def project_records(root,records,refs,*,sources=()):
    known={**sealed_pending_rows(root,sources),**call_view(root,[],refs)}
    result=copy.deepcopy(records)
    for record in result:
        observed=record.get('observation') or {};recovered=[]
        rows=observed.setdefault('managed_calls',[])
        for index,row in enumerate(rows):
            current=known.get((record['step'],row['call_id']))
            if current is not None and current.get('row') is not None:
                rows[index]=current['row'];recovered.append(row['call_id'])
        ids={row['call_id'] for row in rows}
        record['execution_resumes']=[]
        for (step,call),current in known.items():
            if step!=record['step']:continue
            if current.get('row') is not None and call not in ids:
                rows.append(current['row']);recovered.append(call)
            if current.get('version')==2 or current['phase']=='original_prepare_unknown':
                record['execution_resumes'].append(dict(call_id=call,phase=current['phase'],native_state=current.get('native_state')))
        if rows:record['observation']=observed
        record['recovered_calls']=recovered
    return result
