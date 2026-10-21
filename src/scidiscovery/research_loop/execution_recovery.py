"""Append terminal evidence for a sealed unknown call; never rewrite its science."""
import copy
import hashlib
from pathlib import Path

from . import budget, contracts as c, execution, experiments as e, history, experiment_client

DIRECTORY = 'host-history/execution-recoveries'


def prefix(root,step):
    path=Path(root)/'jobs'/f'{step:03d}.json'
    if path.exists():return c.read(c.regular(path)).get('history',{}).get('execution_recoveries',[])
    directory=Path(root)/DIRECTORY
    paths=sorted([*directory.glob('*/*.json'), *directory.glob('*/*/[0-9]*.json')])
    return [dict(path=path.relative_to(root).as_posix(),sha256=c.digest(c.regular(path))) for path in paths]


def validate(root,refs,*,originals=True):
    if not isinstance(refs,list):raise ValueError('Execution recovery prefix must be a list')
    values=[];seen=set();chains={};sources={};pending={}
    for ref in refs:
        if set(ref)!={'path','sha256'}:raise ValueError('Execution recovery prefix is malformed')
        path=Path(root)/ref['path']
        if (path.resolve()!=path or not path.is_relative_to(Path(root)/DIRECTORY)
                or c.digest(c.regular(path,limit=None))!=ref['sha256']):
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
        if sha!=value['seal_sha256'] or not seal.get('observations'):
            raise history.ObserverIntegrityError('Execution recovery source seal changed')
        old=c.read(Path(root)/seal['observations']['path'])
        matches=[row for row in old.get('managed_calls',[]) if row['call_id']==call]
        if len(matches)!=1 or matches[0]['status']!='unknown' or hashlib.sha256(e.encoded(matches[0])).hexdigest()!=value['old_row_sha256']:
            raise history.ObserverIntegrityError('Execution recovery has no exact sealed unknown origin')
        row=value['row']
        if (row['accepted']!=matches[0]['accepted'] or row['scope']!=matches[0]['scope']
                or row.get('mechanism_binding')!=matches[0].get('mechanism_binding')
                or row['status'] not in {'known_success','known_failed','not_submitted'}):
            raise history.ObserverIntegrityError('Execution recovery changed original reservation')
        history.validate_managed(root,job,[row]);values.append(value)
    return values


def resolved(root,refs):
    return {key:value['row'] for key,value in call_view(root,[],refs).items()
            if value.get('row') is not None and value['row']['status'] in {'known_success','known_failed','not_submitted'}}


def _validate_event(root,ref,value,chains,*,originals=True,sources=None,pending=None,context=None):
    from . import qualification as q
    from .execution_resume import verify_pending_set
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
        for key_name in ('source_step','call_id','scope','seal_sha256','pending_set','allocation','request','decision','admission','origin'):
            if value[key_name]!=prior['value'][key_name]:
                raise history.ObserverIntegrityError('Execution recovery changed its original dispatch bindings')
    request=q._read(root,value['request']);decision=q._read(root,value['decision'])
    admission=q._read(root,value['admission'])
    if (request['call_id']!=call or request['job']['sha256']!=value['scope']['job_sha256']
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
            if context is None:sources[step]=history._source(root,step)
            else:
                # Current publication verifies its exact source, not every old call's logs.
                from . import execution
                job_path=Path(root)/'jobs'/f'{step:03d}.json'
                seal_path=c.host_record(c.workspace(root,step))/'result-identity.json'
                job=c.read(c.regular(job_path));seal=c.read(c.regular(seal_path))
                if (e.proof(job_path,context=context)['sha256']!=seal['job_sha256']
                        or e.proof(execution.raw(root,step),context=context)['sha256']!=seal['raw_sha256']
                        or e.proof(seal_path.parent/'result.json',context=context)['sha256']!=seal['sha256']):
                    raise history.ObserverIntegrityError('Current recovery source originals changed')
                sources[step]=(job,None,seal,e.proof(seal_path,context=context)['sha256'])
        job,_,seal,sha=sources[step]
        if sha!=value['seal_sha256'] or value['pending_set']!=seal.get('pending_executions'):
            raise history.ObserverIntegrityError('Execution recovery source seal changed')
        identity=(step,value['allocation']['path'],value['allocation']['sha256'])
        fresh=value['origin']=='sealed_unprepared'
        if identity not in pending:pending[identity]=verify_pending_set(root,seal,value['allocation'],require_complete=fresh,context=context)
        if pending[identity]['disposition']!=('unprepared' if fresh else 'possibly_prepared'):
            raise history.ObserverIntegrityError('Late dispatch has no positive original unprepared evidence')
        allocation=q._read(root,value['allocation'])
        if allocation['call_id']!=call or allocation['binding']['scope']!=value['scope']:
            raise history.ObserverIntegrityError('Execution recovery allocation changed')
        if row is not None:
            history.validate_managed(root,job,[row],context=context)
            if row.get('mechanism_binding')!=allocation['binding'].get('mechanism_binding'):
                raise history.ObserverIntegrityError('Recovered execution changed original scientific opportunity')
            accepted=c.read(Path(root)/row['accepted']['snapshot'])
            selected=allocation['binding'];prepared=accepted['prepared']
            original=accepted['execution_authority'];q._check(original)
            authority=c.read(Path(original['path']))
            if (accepted.get('native_attempt')!=selected['native_attempt'] or accepted.get('interface')!=selected['interface']
                    or authority['allocation']!=value['allocation'] or authority['request']!=value['request']
                    or authority['decision']!=value['decision'] or authority['scope']!=value['scope']
                    or authority['native_attempt']!=selected['native_attempt'] or authority['interface']!=selected['interface']
                    or authority['payload_alias']!=selected['payload_alias'] or accepted['execution_target']!=request['target']
                    or authority['target']!=request['target'] or prepared['name']!=call
                    or accepted['script']['path']!=str(experiment_client.record_directory(root,step)/'calls'/call/'source.py')
                    or (accepted['script']['sha256'],accepted['script']['size_bytes'])!=(prepared['sha256'],prepared['size_bytes'])
                    or e.proof(accepted['script']['path'],context=context)!=accepted['script']
                    or e.proof(prepared['local_path'],context=context)!=dict(path=prepared['local_path'],sha256=prepared['sha256'],size_bytes=prepared['size_bytes'])):
                raise history.ObserverIntegrityError('Recovered accepted/prepared authority lost its exact original allocation')
        if value.get('query') is not None:
            query=q._read(root,value['query'])
            submission=q._read(root,value['submission']) if value.get('submission') is not None else None
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
    chain=[value for value in values if value.get('version')==2 and
           (value['source_step'],value['call_id'])==(base['source_step'],base['call_id'])]
    if chain and all(chain[-1].get(key)==item for key,item in dict(phase=phase,row=row,native_state=native_state,submission=submission).items()):
        return next(ref for ref in refs if c.read(root/ref['path'])==chain[-1])
    directory=root/DIRECTORY/f'{base["source_step"]:03d}'/base['call_id']
    previous=None if not chain else dict(path=(directory/f'{len(chain):06d}.json').relative_to(root).as_posix(),
                                         sha256=c.digest(directory/f'{len(chain):06d}.json'))
    value=dict(base,version=2,previous=previous,phase=phase,row=row,query=query,native_state=native_state,submission=submission)
    path=directory/f'{len(chain)+1:06d}.json'
    # Validate the prospective value with the same validator before publication.
    ref=dict(path=path.relative_to(root).as_posix(),sha256=hashlib.sha256(e.encoded(value)).hexdigest())
    chains={}
    for old_ref,old in zip(refs,values):
        if old.get('version')==2:_validate_event(root,old_ref,old,chains,originals=False)
    _validate_event(root,ref,value,chains,context=context)
    if context is not None:context.remaining_seconds()
    c.write(path,value);path.chmod(0o400)
    ref['sha256']=c.digest(path)
    return ref


def call_view(root,sources,refs,*,originals=True,validated=None,original_rows=None):
    """Single derived call projection; frozen source conclusions remain untouched."""
    sources=list(sources)
    result={**sealed_pending_rows(root,sources),**(original_rows or {})}
    for job,_,seal,_ in sources:
        observed=c.read(Path(root)/seal['observations']['path']) if seal.get('observations') else {}
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
        value=q._read(root,ref)
        if (value['job']['sha256']!=seal['job_sha256'] or value['job']['path']!=f'jobs/{job["step"]:03d}.json'):
            raise history.ObserverIntegrityError('Pending execution metadata escaped its sealed source')
        for row in value['allocations']:
            if row['disposition']!='possibly_prepared' or row['accepted'] is not None:continue
            allocation=q._read(root,row['allocation'])
            if allocation['call_id']!=row['call_id'] or allocation['binding']['scope']['job_sha256']!=seal['job_sha256']:
                raise history.ObserverIntegrityError('Unknown preparation lost its original allocation')
            result[(job['step'],row['call_id'])]=dict(row=None,phase='original_prepare_unknown',native_state=None)
    return result


def _current_pending_sources(root):
    # Clock/new-call admission explicitly use current seals; frozen science never does.
    result=[]
    for path in sorted((Path(root)/'native-evidence').glob('*/result-identity.json')):
        seal=c.read(c.regular(path,limit=None))
        if seal.get('pending_executions') is not None:
            result.append((dict(step=int(path.parent.name)),None,
                {key:seal[key] for key in ('job_sha256','pending_executions')},None))
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
    job,_,seal,sha=history._source(root,step)
    if job['role']!='W' or not seal.get('observations'):raise ValueError('Recovery requires an exact sealed W observation')
    old=c.read(root/seal['observations']['path']);created=[];unresolved=[]
    for row in old.get('managed_calls',[]):
        if row['status']!='unknown':continue
        path=root/DIRECTORY/f'{step:03d}'/(row['call_id']+'.json')
        if path.exists():
            ref=dict(path=path.relative_to(root).as_posix(),sha256=c.digest(path));validate(root,[ref]);created.append(ref);continue
        accepted=c.read(root/row['accepted']['snapshot'])
        from . import managed_wait
        _,config=managed_wait._config(root,job,accepted)
        directory=e.Records(config).directory(row['call_id'])
        retired=e.retirement(config,row['call_id'])
        if retired is None and not (directory/'execution-observation.json').exists():
            if not config['execution_interface']['capabilities']['query']:
                unresolved.append(row['call_id']);continue
            if e.record_observation(config,e.load(config),row['call_id']) is None:
                unresolved.append(row['call_id']);continue
        current=[item for item in history.managed_calls(root,job) if item['call_id']==row['call_id']]
        if len(current)!=1 or current[0]['status']=='unknown':raise ValueError('Original recovery terminal is unverified')
        value=dict(source_step=step,seal_sha256=sha,old_row_sha256=hashlib.sha256(e.encoded(row)).hexdigest(),row=current[0])
        c.write(path,value);path.chmod(0o400)
        ref=dict(path=path.relative_to(root).as_posix(),sha256=c.digest(path));validate(root,[ref]);created.append(ref)
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
