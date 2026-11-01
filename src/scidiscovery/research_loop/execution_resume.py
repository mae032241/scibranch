"""Host recovery of an original call; execution authority and ledger keep ownership.

The seal records positive namespace/cleanup evidence before any late dispatch.
No current missing file or PID is interpreted as proof of non-submission.
"""
from pathlib import Path
import uuid
import math
import re
import time

from . import budget, contracts as c, execution, qualification as q, experiment_client
from scidiscovery import archive_io as files


def capture_pending(root, job):
    """Optional evidence failure never discards a valid scientific conclusion."""
    from . import execution_authority as authority
    root=Path(root).resolve()
    if job['role']!='W':return None
    path=execution.origin(root,job['step'])/'pending-execution-set.json'
    if path.exists():return q._ref(root,path)
    try:
        return _capture_pending(root,job)
    except BlockingIOError:
        # A live writer is a retryable sealing boundary, never missing evidence.
        raise
    except (OSError,ValueError,KeyError,TypeError,files.ArchiveError) as error:
        return q._save(root,path,dict(version=1,job=q._ref(root,root/'jobs'/f'{job["step"]:03d}.json'),
                contract=authority.contract(root,job) if job.get('execution_authority') else None,
                attempts=[],allocations=[],calls_namespace=[],host_preparations=[],complete=False,
                unavailable_reason='Pending namespace or owner evidence could not be captured: '+type(error).__name__))


def _capture_pending(root, job):
    from . import accepted_budget, execution_authority as authority
    root = Path(root).resolve()
    if job['role'] != 'W':
        return None
    path = execution.origin(root, job['step']) / 'pending-execution-set.json'
    if path.exists():
        return q._ref(root, path)
    records=experiment_client.record_directory(root,job['step'])/'calls'
    from . import preparation_recovery
    if not job.get('execution_authority'):
        bindings=records.parent/'host-preparations'
        if not bindings.exists() and not bindings.is_symlink():return None
        with c.record_writer(records.parent, blocking=False, exclusive=True):
            pending=[q._ref(root,Path(row['binding']['path'])) for row in preparation_recovery.retained_preparations(root,records.parent)
                if not row['resolved']]
            if not pending:return None
            namespace=sorted(item.name for item in records.iterdir()) if records.exists() else []
            return q._save(root,path,dict(version=1,job=q._ref(root,root/'jobs'/f'{job["step"]:03d}.json'),
                contract=None,attempts=[],allocations=[],calls_namespace=namespace,host_preparations=pending,
                complete=False,unavailable_reason='Original host preparation is unknown; no execution authority or external preparation reconciliation is available'))
    with authority.writer(root,blocking=False), c.record_writer(records.parent,blocking=False,exclusive=True):
        allocations = []; complete = True
        preparations=preparation_recovery.retained_preparations(root,records.parent)
        pending=[q._ref(root,Path(row['binding']['path'])) for row in preparations if not row['resolved']]
        unknown_calls={row['call_id'] for row in preparations if not row['resolved']}
        if pending:complete=False
        job_ref=q._ref(root,root/'jobs'/f'{job["step"]:03d}.json')
        records=experiment_client.record_directory(root,job['step'])/'calls'
        namespace=sorted(item.name for item in records.iterdir()) if records.exists() else []
        if records.resolve()!=records:complete=False
        head=accepted_budget.read(root)
        current=accepted_budget.authority_calls(head,job['step'])
        calls=[];requests={}
        for call,refs in sorted(current.items()):
            allocation_ref=refs['allocation']
            if allocation_ref is not None:
                allocation=accepted_budget.authority_original(head,allocation_ref)
                if not (root/allocation_ref['path']).exists():complete=False
                if (allocation['call_id']!=call
                        or allocation['binding']['scope']['job_ref']!=dict(path=str(root/job_ref['path']))):
                    raise q.IntegrityError('Accepted allocation changed its exact current job')
                calls.append((root/Path(allocation_ref['path']).parent,allocation,allocation_ref))
            for ref in refs['requests']:
                value=accepted_budget.authority_original(head,ref)
                if not (root/ref['path']).exists():complete=False
                if value['job']!=job_ref or value['call_id']!=call:
                    raise q.IntegrityError('Accepted request changed its exact current job or call')
                requests.setdefault(call,[]).append(ref)
        # No pending authority allocation means no new attempt/request history IO.
        if not calls and not requests and not namespace and not pending:return None
        attempts = []
        for record in execution.records(root, job['step']):
            from . import worker_execution_bridge
            worker_execution_bridge.stopped(record,execution.identity(root,job['step'],len(attempts)))
            proofs = {}
            for name in ('native-intent.json', 'native-outcome.json', 'runner-process.json', 'native-process.json'):
                if (record / name).exists():
                    proofs[name] = q._ref(root, record / name)
                else:
                    complete = False
            outcome = c.read(record / 'native-outcome.json') if 'native-outcome.json' in proofs else {}
            if (outcome.get('cleanup') != 'observed_group_cleaned'
                    or type(outcome.get('returncode')) is not int):
                complete = False
            binding=execution.identity(root,job['step'],len(attempts))
            for name in ('runner-process.json','native-process.json'):
                if name in proofs:
                    process=q._read(root,proofs[name])
                    if (process.get('task_ref')!=binding or not process.get('boot_id')
                            or any(type(process.get(key)) is not int or process[key]<=0 for key in ('pid','start_ticks','pgid','sid'))):
                        complete=False
            attempts.append(proofs)
        for directory,allocation,allocation_ref in calls:
            original = directory / 'allocation.json'
            call = allocation['call_id']
            if directory.name != call or directory.resolve() != directory:
                complete = False
            prepare = directory / 'prepare-intent.json'
            accepted = records / call / 'accepted.json'
            refs = dict(prepare_intent=q._ref(root, prepare) if prepare.exists() else None,
                        accepted=q._ref(root, accepted) if accepted.exists() else None)
            # Snapshot the original call's inventory without interpreting any
            # adapter-owned JSON or depending on provider availability.
            originals=[]
            call_directory=records/call
            if call_directory.exists():
                import os
                def unreadable(error):raise error
                for parent,directories,files in os.walk(call_directory,followlinks=False,onerror=unreadable):
                    directory_path=Path(parent)
                    if directory_path.resolve()!=directory_path:raise ValueError('Pending call directory changed')
                    for name in directories:
                        if (directory_path/name).resolve()!=directory_path/name:
                            raise ValueError('Pending call directory is not original')
                    for name in sorted(files):
                        originals.append(q._ref(root,c.regular(directory_path/name)))
            refs['records']=originals
            allocations.append(dict(call_id=call, allocation=allocation_ref,
                requests=requests.pop(call, []), **refs,
                disposition='unprepared' if not prepare.exists() and call not in namespace and call not in unknown_calls else 'possibly_prepared'))
        if requests or any(name not in {row['call_id'] for row in allocations} for name in namespace):
            complete = False
        # Partial preparation directories also make absence unprovable.
        lineage = records.parent
        if lineage.exists() and any(item.name.startswith('.prepared-') for item in lineage.iterdir()):
            complete = False
        return q._save(root, path, dict(version=1, job=q._ref(root, root / 'jobs' / f'{job["step"]:03d}.json'),
            contract=authority.contract(root,job), attempts=attempts, allocations=allocations,
            calls_namespace=namespace, host_preparations=pending, complete=complete))


def _source_path(source, proof):
    from . import originals
    owner = originals.source_for_ref(source, proof)
    path = Path(proof['path'])
    if path.is_absolute():
        path = path.relative_to(owner.original_root)
    try:
        return originals.resolve_ref(owner, dict(proof, path=path.as_posix()))
    except (OSError, ValueError) as error:
        raise ValueError('Frozen execution original unavailable: ' + path.as_posix()) from error


def verify_pending_set(root, seal, allocation_ref=None, *, unprepared=False, require_complete=True, context=None, source=None):
    """Check frozen positive facts; mutable absence is only an extra contradiction check."""
    from . import execution_authority as authority
    root = Path(root).resolve()
    if source is not None and unprepared:
        raise ValueError('Inherited pending evidence never authorizes dispatch')
    read = (lambda proof: q._read(root, proof)) if source is None else (lambda proof: c.read(_source_path(source, proof)))
    def verify_bytes(proof):
        if source is not None:
            _source_path(source,proof)
        else:
            from . import originals
            originals.resolve_root_ref(root,proof)
    def proof_at(path, expected):
        if source is None:return q._ref(root, path)
        relative = Path(path).relative_to(source.original_root).as_posix()
        actual = _source_path(source, dict(expected, path=relative))
        return dict(path=relative, size_bytes=actual.stat().st_size)
    ref = seal.get('pending_executions')
    if ref is None:
        raise ValueError('Original seal has no positive pending execution evidence')
    value = read(ref)
    job = read(value['job'])
    logical_root = Path(source.original_root) if source is not None else root
    def preparation_refs():
        from . import preparation_recovery
        def read_preparation(proof):
            if source is not None:return c.read(_source_path(source,proof))
            return c.read(preparation_recovery._verify_ref(root,proof,context))
        refs=value.get('host_preparations',[])
        if not isinstance(refs,list):raise q.IntegrityError('Pending host preparation references changed')
        for proof in refs:
            if context is not None:context.remaining_seconds()
            verify_bytes(proof); binding=read_preparation(proof)
            token=binding.get('snapshot_token');call=binding.get('call_id')
            config=read_preparation(binding['configuration'])
            records=Path(config['record_dir'])
            expected=records/'host-preparations'/str(call)/(str(token)+'.json')
            original_path=Path(proof['path'])
            original_path=original_path if original_path.is_absolute() else logical_root/original_path
            if (binding.get('version')!=1 or binding.get('kind')!='host_preparation_snapshot'
                    or not isinstance(token,str) or not preparation_recovery.TOKEN.fullmatch(token)
                    or not isinstance(call,str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}',call) or call in {'.','..'}
                    or binding.get('instance',{}).get('root')!=str(logical_root)
                    or not records.is_relative_to(logical_root)
                    or original_path!=expected or binding.get('records')!=str(records)
                    or binding.get('scope')!=config.get('scope')
                    or binding['scope'].get('job_ref')!=seal['job_ref']
                    or q.control_reference(logical_root,binding['job'])!=q.control_reference(logical_root,value['job'])):
                raise q.IntegrityError('Pending host preparation escaped its original job')
        return refs
    if not job.get('execution_authority'):
        if (value.get('version')!=1 or value.get('contract') is not None or value.get('complete') is not False
                or value.get('attempts')!=[] or value.get('allocations')!=[]
                or dict(path=str(logical_root/value['job']['path']))!=seal['job_ref']
                or ref['path']!=(execution.origin(logical_root,job['step'])/'pending-execution-set.json').relative_to(logical_root).as_posix()
                or not isinstance(value.get('unavailable_reason'),str)):
            raise q.IntegrityError('Binding-only pending evidence is not conservative original evidence')
        preparation_refs()
        if unprepared or allocation_ref is not None:
            raise ValueError('Binding-only incomplete evidence never authorizes dispatch or reservation rebind')
        return value
    if source is None:
        expected_contract = authority.contract(root,job)
        logical_root = root
    else:
        from . import originals
        policy = originals.source_settings(source).get('execution_authority_policy')
        if policy is None:raise q.IntegrityError('Original pending evidence has no authority contract')
        expected_contract = policy['contract']
        contract_value = read(expected_contract)
        if {key:item for key,item in contract_value.items() if key != 'implementation'} != dict(
                version=1, options=list(authority.OPTIONS), scope=authority.SCOPE, stage='decision_originals_resource_gate_separate'):
            raise q.IntegrityError('Original pending authority contract changed')
        logical_root = Path(source.original_root)
    if (value.get('version') != 1 or dict(path=str(logical_root/value['job']['path'])) != seal['job_ref']
            or type(value.get('complete')) is not bool
            or ref['path'] != (execution.origin(logical_root, job['step']) / 'pending-execution-set.json').relative_to(logical_root).as_posix()
            or value['contract'] != expected_contract):
        raise q.IntegrityError('Pending execution evidence differs from its original seal')
    preparation_refs()
    for proofs in value['attempts']:
        for proof in proofs.values():
            read(proof)
    for row in value['allocations']:
        if context is not None:context.remaining_seconds()
        allocation = read(row['allocation'])
        if allocation['call_id'] != row['call_id'] or allocation['binding']['scope']['job_ref'] != seal['job_ref']:
            raise q.IntegrityError('Pending allocation escaped its original job')
        for proof in row['requests']:
            request = read(proof)
            if request['call_id'] != row['call_id'] or request['job'] != value['job']:
                raise q.IntegrityError('Pending request escaped its original allocation')
        # New inventory refs and legacy top-level proof-shaped values share
        # the same hash/size/origin checks; legacy missing data is never empty.
        for key,item in row.items():
            if isinstance(item,dict) and {'path','size_bytes'} <= set(item):verify_bytes(item)
        if 'records' in row:
            if not isinstance(row['records'],list):raise q.IntegrityError('Pending call inventory changed')
            for proof in row['records']:verify_bytes(proof)
    if allocation_ref is None:
        return value
    matches = [row for row in value['allocations'] if row['allocation'] == allocation_ref]
    if len(matches) != 1 or require_complete and not value['complete']:
        raise ValueError('Original pending execution set is incomplete or has no exact allocation')
    if not require_complete:
        if unprepared:raise ValueError('Fresh dispatch always requires complete positive evidence')
        return matches[0]
    if source is None:
        records = execution.records(root, job['step'])
    else:
        count = len((seal.get('execution_ref') or {}).get('chain', [None]))
        records = [execution.origin(logical_root, job['step']) / (f'continuations/{number:03d}' if number else '')
            for number in range(count)]
    if len(records) != len(value['attempts']):
        raise q.IntegrityError('Original pending attempt set changed')
    for number,(record, proofs) in enumerate(zip(records, value['attempts'])):
        if (set(proofs) != {'native-intent.json', 'native-outcome.json', 'runner-process.json', 'native-process.json'}
                or any(proof != proof_at(record / name, proof) for name, proof in proofs.items())
                or read(proofs['native-outcome.json']).get('cleanup') != 'observed_group_cleaned'):
            raise q.IntegrityError('Original positive cleanup evidence is incomplete')
        for name in ('runner-process.json','native-process.json'):
            process=read(proofs[name])
            binding = (execution.identity(root,job['step'],number) if source is None else
                (dict(path=str(logical_root/value['job']['path'])) if number == 0 else dict(path=str(record / 'native-intent.json'))))
            if (process.get('task_ref')!=binding or not process.get('boot_id')
                    or any(type(process.get(key)) is not int or process[key]<=0 for key in ('pid','start_ticks','pgid','sid'))):
                raise q.IntegrityError('Original cleanup has no exact process owner')
    row = matches[0]
    if unprepared:
        if row['disposition'] != 'unprepared' or row['prepare_intent'] is not None or row['accepted'] is not None:
            raise ValueError('Original preparation may have started; only same-call reconciliation is allowed')
        allocation = q._read(root, allocation_ref)
        prepare = root / allocation_ref['path']; call = allocation['call_id']
        if ((prepare.parent / 'prepare-intent.json').exists()
                or (experiment_client.record_directory(root, job['step']) / 'calls' / call).exists()):
            raise q.IntegrityError('New call artifacts contradict the sealed unprepared evidence')
        from . import preparation_recovery
        if any(row['call_id']==call for row in preparation_recovery.retained_preparations(
                root,experiment_client.record_directory(root,job['step']),context=context)):
            raise q.IntegrityError('Retained host preparation contradicts sealed unprepared evidence')
    return row


def verify_rebind(root, proof, before, after):
    """Ledger's only authority-rebind proof: same positively never-prepared call."""
    seal=q._read(root,proof['seal'])
    row=verify_pending_set(root,seal,proof['allocation'],unprepared=True)
    if row['disposition']!='unprepared' or proof['pending_set']!=seal['pending_executions']:
        raise q.IntegrityError('Reservation rebind lacks positive original non-preparation evidence')
    for key in ('call_id','scope','native_attempt','interface'):
        if before[key]!=after[key]:raise q.IntegrityError('Reservation rebind changed original execution identity')
    old=q._read(root,before['request']);new=q._read(root,after['request'])
    for key in ('job','native_intent','call_id','subject','review','payload','payload_alias','target','resources'):
        if old.get(key)!=new.get(key):raise q.IntegrityError('Reservation rebind changed its exact scientific request')
    cursor=after['request'];seen=set()
    while cursor!=before['request']:
        if cursor['path'] in seen:raise q.IntegrityError('Request successor chain is cyclic')
        seen.add(cursor['path']);value=q._read(root,cursor)
        cursor=value.get('supersedes')
        if cursor is None:raise q.IntegrityError('Reservation authority has no exact original request chain')
    if q._read(root,after['decision'])['request']!=after['request']:
        raise q.IntegrityError('Reservation rebind decision changed request')
    from . import execution_authority as authority
    allocation=q._read(root,proof['allocation'])
    config=c.read(c.regular(execution.record(root,before['scope']['step'],before['native_attempt']['number'])/'experiment-config.json'))
    if allocation['binding']['scope']!=before['scope'] or allocation['binding']['interface']!=before['interface']:
        raise q.IntegrityError('Reservation rebind changed the frozen host owner')
    for binding in (before,after):
        permit=authority._permit(root,binding['authority'],config,new['target'],binding['call_id'],None,authority.host_secret(root))
        if permit['request']!=binding['request'] or permit['decision']!=binding['decision']:
            raise q.IntegrityError('Reservation rebind lost original authority receipt')


class _RecoveryAdmission:
    """One synchronous host operation, never serialized into Worker configuration."""
    def __init__(self,root,job,seal,seal_sha,allocation,config,*,max_seconds,note,valid_seconds):
        from . import budget, execution_authority as authority
        self.root,self.job,self.seal,self.config=root,job,seal,config
        self.allocation_ref=allocation;self.allocation=q._read(root,allocation)
        self.call=self.allocation['call_id'];self.valid_seconds=valid_seconds
        self.secret=authority.host_secret(root);self.started=False;self.base=None;self._collection=None
        self.proof=dict(seal=q._ref(root,c.host_record(c.workspace(root,job['step']))/'result-identity.json'),
                        allocation=allocation,pending_set=seal['pending_executions'])
        stamp=budget.clock_stamp();limits=budget.snapshot(root,sample_stamp=stamp)
        from . import native
        available=budget.execution_available(root,job,limits,native.usage(root))
        if (type(max_seconds) not in (int,float) or not math.isfinite(max_seconds) or max_seconds<=0
                or not isinstance(note,str) or not note.strip()):
            raise ValueError('Finite host operation seconds and explicit operator audit note required')
        if available['tokens']<=0 or available['seconds']<=0:
            raise ValueError('Current original shared scientific authorization is exhausted')
        seconds=min(max_seconds,available['seconds'])
        directory=root/'execution-authority/calls'/self.call/'recovery-operations'
        number=len(list(directory.glob('*.json')))+1
        self.operation=q._save(root,directory/f'{number:06d}.json',dict(version=1,allocation=allocation,
            pending_set=seal['pending_executions'],source_seal=self.proof['seal'],boot_id=stamp['boot_id'],
            started_boottime=stamp['at_boottime'],deadline_boottime=stamp['at_boottime']+seconds,
            scientific_authorization=budget.authorization_revision(root),authorization_note=note))
        self.receipt=q._read(root,self.operation)
        self.seal_sha=seal_sha
        from scidiscovery.plugin_runtime.collection import CollectionContext
        monotonic=time.monotonic();current=budget.clock_stamp()
        remaining=self.receipt['deadline_boottime']-current['at_boottime']
        if current['boot_id']!=self.receipt['boot_id'] or remaining<=0:
            raise ValueError('Finite recovery admission expired while its original was published')
        end=monotonic+remaining
        self.context=CollectionContext(end,end,file_timeout_seconds=remaining,idle_timeout_seconds=remaining,
                                      stopped=lambda:self.expired())

    def expired(self):
        from . import budget
        stamp=budget.clock_stamp()
        return stamp['boot_id']!=self.receipt['boot_id'] or stamp['at_boottime']>=self.receipt['deadline_boottime']

    def check(self):
        from . import budget, native
        stamp=budget.clock_stamp();q._read(self.root,self.operation)
        if self.expired() or self.receipt['scientific_authorization']!=budget.authorization_revision(self.root):
            raise ValueError('Current finite host recovery admission expired or ceiling changed')
        limits=budget.snapshot(self.root,sample_stamp=stamp)
        available=budget.execution_available(self.root,self.job,limits,native.usage(self.root))
        if available['tokens']<=0 or available['seconds']<=0:
            raise ValueError('Current original shared scientific authorization is exhausted')
        return stamp,min(available['seconds'],self.receipt['deadline_boottime']-stamp['at_boottime'])

    def job_remaining(self):
        from . import budget,native
        self.check()
        return budget.execution_available(self.root,self.job,budget.snapshot(self.root),native.usage(self.root))['seconds']

    def opportunity(self):
        from . import breadth,search,execution_recovery
        binding=self.allocation['binding'].get('mechanism_binding')
        if binding is None:return
        if self.started:return # The accepted opportunity was checked before resume_intent.
        from . import history
        sources=[]
        for step in range(1,budget.historical_step_limit(self.root)+1):
            if not (c.host_record(c.workspace(self.root,step))/'result-identity.json').exists():break
            sources.append(history._source(self.root,step))
        state=search.fold(self.root,sources,recovery_refs=execution_recovery.prefix(self.root,budget.historical_step_limit(self.root)+1))
        from . import configuration_updates
        current=search._breadth(self.root,state,configuration_updates.for_job(self.root,self.job)['search_policy'],None)
        actual=breadth.admit(current,binding['mechanism_ref'],self.config['scope'],[])
        if actual!=binding:raise ValueError('Original scientific opportunity is no longer eligible; no new opportunity was granted')

    def request(self,admission,target):
        from . import budget,execution_authority as authority
        self.check();self.opportunity()
        row=verify_pending_set(self.root,self.seal,self.allocation_ref,unprepared=True)
        matches=[]
        for ref in row['requests']:
            value=q._read(self.root,ref)
            if value['target']==target:matches.append(ref)
        if len(matches)!=1:raise ValueError('Late execution requires one exact original authorization request')
        ref=matches[0]
        while (self.root/ref['path']).parent.joinpath('successor.json').exists():
            successor=q._read(self.root,q._ref(self.root,(self.root/ref['path']).parent/'successor.json'))
            if successor['previous']!=ref:raise q.IntegrityError('Request successor lost its original request')
            next_ref=successor['successor'];value=q._read(self.root,next_ref)
            if value.get('supersedes')!=ref:raise q.IntegrityError('Request successor changed its explicit parent')
            ref=next_ref
        value=q._read(self.root,ref)
        current=authority.status(self.root,Path(ref['path']).parent.name,secret=self.secret)
        grant=budget.execution_grant(self.root,operation_context=self.context)
        if (current['state']!='expired' and value['policy']==admission.model_dump(mode='json')
                and value['grant_revision']==grant['ref']):
            return ref
        if self.valid_seconds is None:
            raise authority.AuthorityRequired(dict(status='authorization_renewal_required',call_id=self.call,
                authorization_request=ref,review_url=authority.review_url(self.root,Path(ref['path']).parent.name,self.secret),
                note='Supply explicit finite --request-valid-seconds to renew the immutable expired or changed-policy request; approving the old request cannot dispatch.'))
        if type(self.valid_seconds) not in (int,float) or not math.isfinite(self.valid_seconds) or self.valid_seconds<=0:
            raise ValueError('Explicit positive finite request validity required')
        return authority.create_request(self.root,step=self.job['step'],native_intent=self.allocation['native_intent'],
            call_id=self.call,subject=value['subject'],review_ref=value['review'],payload_alias=value.get('payload_alias'),
            target=target,policy=admission.model_dump(mode='json'),resources=dict(admission.budget),grant_revision=grant['ref'],
            expires_at=time.time()+self.valid_seconds,secret=self.secret,supersedes=ref,operation_context=self.context)

    def begin(self,request,decision):
        from . import execution_recovery
        self.check()
        if self.started:
            if self.base['request']!=request or self.base['decision']!=decision:
                raise q.IntegrityError('Recovery changed the original exact decision')
            return
        self.opportunity()
        verify_pending_set(self.root,self.seal,self.allocation_ref,unprepared=True)
        self.base=dict(source_step=self.job['step'],call_id=self.call,scope=self.config['scope'],seal_ref=self.seal_sha,
            pending_set=self.seal['pending_executions'],allocation=self.allocation_ref,request=request,decision=decision,admission=self.operation,
            origin='sealed_unprepared')
        execution_recovery.append(self.root,self.base,'resume_intent',context=self.context)
        self.started=True

    def collection(self,original):
        self.check()
        if self._collection is not None:
            self._collection.remaining_seconds();return self._collection
        runner=original['admission']['policy']['runner'];remaining=min(self.context.remaining_seconds(),runner['collection_total_seconds'])
        from scidiscovery.plugin_runtime.collection import CollectionContext
        end=time.monotonic()+remaining
        from . import budget
        stamp=budget.clock_stamp()
        q._save(self.root,self.root/'execution-authority/calls'/self.call/'collection-recoveries'/Path(self.operation['path']).name,
            dict(operation=self.operation,request=original['request'],target=original['target'],boot_id=stamp['boot_id'],
                 deadline_boottime=stamp['at_boottime']+remaining))
        self._collection=CollectionContext(end-min(2.,remaining*.1),end,
            file_timeout_seconds=min(remaining,runner['collection_file_seconds']),
            idle_timeout_seconds=min(remaining,runner['collection_idle_seconds']),stopped=self.expired)
        return self._collection

    def services(self):
        from . import execution_authority as authority
        from scidiscovery.operations.runtime_plugins import ExecutionHostServices
        from .execution_inputs import NativeInputHost
        return ExecutionHostServices(gate=lambda provider:lambda phase,**kw:authority._gate(self.config,provider,phase,recovery=self,**kw),
            call_identity=lambda *_:self.call,implementation_inputs=NativeInputHost(self.config),operation_context=self.context)


class _MaintenanceWindow:
    """Current finite control IO, without authority to start scientific execution."""
    def __init__(self, root, config, call, action, *, max_seconds, note):
        from . import budget, experiments as e, execution_authority as authority
        from scidiscovery.plugin_runtime.collection import CollectionContext
        if (type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or max_seconds <= 0
                or not isinstance(note, str) or not note.strip()):
            raise ValueError('Finite maintenance seconds and explicit operator audit note required')
        self.root, self.config, self.call, self.action = root, config, call, action
        self._collection = None
        records = e.Records(config); directory = records.directory(call); accepted = records.accepted(call)
        self.authority = accepted['execution_authority']
        self.permit = authority._permit(root, self.authority, config, accepted['execution_target'], call,
                                       None, authority.host_secret(root))
        stamp = budget.clock_stamp()
        operations = directory / 'maintenance-operations'
        number = len(list(operations.glob('*/intent.json'))) + 1
        self.directory = operations / f'{number:06d}'
        self.operation = q._save(root, self.directory / 'intent.json', dict(version=1, kind='original_call_maintenance',
            call_id=call, action=action, accepted=q._ref(root, directory / 'accepted.json'), authority=self.authority,
            scope=config['scope'], native_attempt=config['native_attempt'], boot_id=stamp['boot_id'],
            started_boottime=stamp['at_boottime'], deadline_boottime=stamp['at_boottime'] + max_seconds,
            authorization_note=note))
        self.receipt = q._read(root, self.operation)
        remaining = self.check()[1]; end = time.monotonic() + remaining
        self.context = CollectionContext(end, end, file_timeout_seconds=remaining,
            idle_timeout_seconds=remaining, stopped=self.expired)

    def expired(self):
        from . import budget
        stamp = budget.clock_stamp()
        return stamp['boot_id'] != self.receipt['boot_id'] or stamp['at_boottime'] >= self.receipt['deadline_boottime']

    def check(self):
        from . import budget
        stamp = budget.clock_stamp()
        if q._read(self.root, self.operation) != self.receipt or self.expired():
            raise ValueError('Original maintenance window expired or changed')
        return stamp, self.receipt['deadline_boottime'] - stamp['at_boottime']

    def collection(self, original):
        if self.action != 'collect' or original != self.permit:
            raise q.IntegrityError('Maintenance cannot change its original action or permit')
        self.check(); self.context.remaining_seconds()
        if self._collection is None:
            from scidiscovery.plugin_runtime.collection import CollectionContext
            runner = original['admission']['policy']['runner']
            remaining = min(self.context.remaining_seconds(), runner['collection_total_seconds'])
            end = time.monotonic() + remaining
            self._collection = CollectionContext(end - min(2., remaining * .1), end,
                file_timeout_seconds=min(remaining, runner['collection_file_seconds']),
                idle_timeout_seconds=min(remaining, runner['collection_idle_seconds']), stopped=self.expired)
        self._collection.remaining_seconds()
        return self._collection

    def services(self):
        from . import experiments as e, execution_authority as authority
        from scidiscovery.operations.runtime_plugins import ExecutionHostServices
        def gate(provider):
            def bound(phase, **kwargs):
                self.check()
                if (phase != self.action or phase not in {'collect', 'cancel'}
                        or kwargs.get('call_id') != self.call or kwargs.get('prior_receipt') != self.authority):
                    raise e.UnsupportedExecution('Maintenance cannot prepare, submit or change the original call')
                return authority._gate(self.config, provider, phase, recovery=self, **kwargs)
            return bound
        def no_dispatch(*args, **kwargs):
            raise e.UnsupportedExecution('Maintenance has no new execution identity or dispatch authority')
        return ExecutionHostServices(gate=gate, call_identity=no_dispatch, operation_context=self.context)


def maintain(root, step, call_id, *, action, max_seconds, authorization_note):
    """Maintain only an existing accepted call, independently of scientific clocks."""
    from . import experiments as e, execution_authority as authority, instance_owner
    root = Path(root).resolve()
    if action not in {'query', 'collect', 'cancel'} or not e.ID.fullmatch(call_id):
        raise ValueError('Exact original call and supported maintenance action required')
    with instance_owner.lease(root):
        directory = experiment_client.record_directory(root, step) / 'calls' / call_id
        if directory.resolve() != directory:
            raise e.IntegrityError('Original maintenance call namespace changed')
        if not (directory / 'accepted.json').exists():
            return dict(status='unknown', call_id=call_id, note='No original accepted call; maintenance cannot prepare or submit.')
        accepted = c.read(c.regular(directory / 'accepted.json'))
        attempt = accepted['native_attempt']['number']
        config = c.read(c.regular(execution.record(root, step, attempt) / 'experiment-config.json'))
        actual_root, job, _ = authority._host_context(config)
        if actual_root != root or job['step'] != step:
            raise q.IntegrityError('Maintenance configuration escaped its original task')
        config = e.original_configuration(config, call_id)
        window = _MaintenanceWindow(root, config, call_id, action, max_seconds=max_seconds, note=authorization_note)
        try:
            adapter = e.load(config, host_services=window.services())
            if not getattr(e.lifecycle_capabilities(adapter), 'query' if action == 'collect' else action):
                raise e.UnsupportedExecution('Requested original maintenance operation is unavailable')
            window.check(); window.context.remaining_seconds()
            records = e.Records(config)
            if records.external(call_id) is None and e.lifecycle_capabilities(adapter).lookup_submission:
                descriptor = e.LocalFileDescriptor.model_validate(records.accepted(call_id)['prepared'])
                found = adapter.lookup_submission(descriptor)
                if found is not None:
                    window.context.remaining_seconds(); records.bind_external(call_id, *found)
            if action == 'query':
                result = dict(adapter.status_details(call_id))
                if result.get('call_id', call_id) != call_id:
                    raise e.IntegrityError('Maintenance reply changed its original call')
                result['call_id'] = call_id
            elif action == 'cancel':
                result = dict(call_id=call_id, status=adapter.cancel_original(call_id))
            else:
                if not e.lifecycle_capabilities(adapter).collect:
                    raise e.UnsupportedExecution('Original collection is unavailable')
                observed = e.record_observation(config, adapter, call_id, operation_context=window.context)
                result = dict(call_id=call_id, status=observed['status'] if observed is not None else 'unknown',
                    **(dict(native_state=observed['native_state']) if observed is not None and 'native_state' in observed else {}),
                    note='Original terminal facts retained; no new submission.' if observed is not None else 'No verified original terminal observation.')
        except Exception as error:
            q._save(root, window.directory / 'outcome.json', dict(operation=window.operation,
                status='unknown', error_type=type(error).__name__, diagnostic=str(error)))
            raise
        q._save(root, window.directory / 'outcome.json', dict(operation=window.operation, result=result))
        return result


def resume(root,step,call_id,*,max_seconds,authorization_note,request_valid_seconds=None):
    """Explicit host entry. Caller must hold the existing controller writer."""
    from . import history,experiments as e,execution_recovery as recovery,budget,execution_authority as authority
    from scidiscovery.operations.execution import execution_status
    root=Path(root).resolve();job,_,seal,sha=history._source(root,step)
    if job['role']!='W' or not e.ID.fullmatch(call_id):raise ValueError('Exact sealed W and original call required')
    allocation=q._ref(root,root/'execution-authority/calls'/call_id/'allocation.json')
    row=verify_pending_set(root,seal,allocation,require_complete=False)
    original=q._read(root,allocation)
    attempt=original['binding']['native_attempt']['number']
    config=c.read(c.regular(execution.record(root,step,attempt)/'experiment-config.json'))
    authority._host_context(config)
    if original['binding']['interface']!=config['execution_interface'] or original['binding']['scope']!=config['scope']:
        raise q.IntegrityError('Recovery implementation differs from original allocation')
    prior=recovery.latest(root,step,call_id)
    if prior is not None and prior['phase'] in {'terminal','not_submitted'}:
        if prior['phase']=='terminal':
            observed=e.Records(config).directory(call_id)/'execution-observation.json'
            if observed.exists():e._cleanup_terminal_code_export(config,call_id,c.read(observed))
        return dict(status='execution_resume_recorded',call_id=call_id,note='Verified original terminal fact is already retained; no dispatch occurred.')
    operation=_RecoveryAdmission(root,job,seal,sha,allocation,config,max_seconds=max_seconds,note=authorization_note,valid_seconds=request_valid_seconds)
    if budget.execution_blocked(root,include_managed=False,ignore_recovery=(step,call_id)):
        raise ValueError('Another live or unknown original owner blocks host recovery')
    records=e.Records(config)
    budget.transition(root,'advance','execution_resume',operation_context=operation.context)
    if prior is not None or row['disposition']!='unprepared':
        # Allocation precedes preparation; only accepted calls have a canonical directory.
        directory=records.root/'calls'/call_id
        if directory.resolve()!=directory:
            raise e.IntegrityError('Execution call namespace changed')
        if not (directory/'accepted.json').exists():
            return dict(status='unknown',call_id=call_id,note='Preparation or recovery intent may have started; no fresh dispatch is permitted.')
        records.directory(call_id)
        adapter=e.load(config,host_services=operation.services())
        if prior is None:
            accepted=records.accepted(call_id)
            permit=authority._permit(root,accepted['execution_authority'],config,accepted['execution_target'],call_id,None,operation.secret)
            if permit['allocation']!=allocation or permit['payload_alias']!=original['binding']['payload_alias']:
                raise q.IntegrityError('Original accepted authority escaped its sealed allocation')
            operation.base=dict(source_step=step,call_id=call_id,scope=config['scope'],seal_ref=sha,
                pending_set=seal['pending_executions'],allocation=allocation,request=permit['request'],decision=permit['decision'],
                admission=operation.operation,origin='sealed_preparation_unknown')
    else:
        with authority.writer(root,context=operation.context):verify_pending_set(root,seal,allocation,unprepared=True)
        from . import preparation_recovery
        if any(not row['resolved'] for row in preparation_recovery.retained_preparations(root,records.root,context=operation.context)):
            return dict(status='unknown',call_id=call_id,note='Retained original host preparation blocks fresh preparation without replay.')
        operation.check();operation.opportunity()
        adapter=e.load(config,host_services=operation.services())
        identity=original['binding']['payload_alias'];q._check(identity)
        descriptor=e.LocalFileDescriptor(name=call_id,local_path=identity['path'],size_bytes=identity['size_bytes'],media_type='application/json')
        try:
            prepared=adapter.prepare(descriptor,preparation_profile='research_loop.v1',exchange_directory=records.root)
        except authority.AuthorityRequired as error:
            return error.feedback
        operation.check()
        metadata=adapter.acceptance_metadata(prepared,parent=None,new=False,change=None)
        from . import replay_check
        metadata['engineering_check']=replay_check.acceptance(config,adapter)
        binding=original['binding'].get('mechanism_binding')
        accepted=records.accept(prepared,metadata,config['execution_interface'],expected_calls=[p.name for p in records.calls()],
            mechanism=binding['mechanism_ref'] if binding else None,recovery_admission=operation.check,operation_context=operation.context)
        row=c.accept_recovery_call(root,job,call_id,context=operation.context)
        _publish(root,job,operation.base,adapter,row=row,query=False,context=operation.context)
        adapter.dispatch_lease(accepted)
        external,state=adapter.submit(prepared);records.bind_external(call_id,external,state)
        prior=operation.base
    # Reentry only queries original accepted/prepared identity. submit is never retried.
    if records.external(call_id) is None and e.lifecycle_capabilities(adapter).lookup_submission:
        operation.check()
        original_prepared=e.LocalFileDescriptor.model_validate(records.accepted(call_id)['prepared'])
        found=adapter.lookup_submission(original_prepared)
        if found is not None:records.bind_external(call_id,*found)
    if e.retirement(config,call_id) is None:
        details=adapter.status_details(call_id)
        if execution_status(details.get('state')) in {'known_success','known_failed'}:
            e.record_observation(config,adapter,call_id,operation_context=operation.context)
    if (prior is not None and prior.get('version')==2) or operation.base is not None:
        row=c.accept_recovery_call(root,job,call_id,context=operation.context)
        _publish(root,job,operation.base or {key:prior[key] for key in ('source_step','call_id','scope','seal_ref','pending_set','allocation','request','decision','admission','origin')},adapter,row=row,context=operation.context)
    else:
        return recovery.create(root,step)
    return dict(status='execution_resume_recorded',call_id=call_id,note='Original execution facts appended; the sealed scientific conclusion is unchanged.')


def _publish(root,job,base,adapter,*,row,query=True,context=None):
    from . import execution_recovery as recovery,experiments as e
    if row['call_id']!=base['call_id']:raise q.IntegrityError('Recovered original accepted record is unavailable')
    detail=adapter.status_details(base['call_id']) if query else None
    state=detail.get('state') if detail else None
    phase='terminal' if row['status'] in {'known_success','known_failed'} else 'not_submitted' if row['status']=='not_submitted' else 'status' if query else 'accepted'
    path=experiment_client.record_directory(root,job['step'])/'calls'/base['call_id']/'submission.json'
    submission=q._ref(root,path) if path.exists() else None
    current=recovery.latest(root,job['step'],base['call_id'])
    if current is not None and all(current.get(key)==value for key,value in
            dict(phase=phase,row=row,native_state=state,submission=submission).items()):return
    proof=None
    if detail is not None:
        data=e.encoded(detail);path=root/'host-history/objects'/uuid.uuid4().hex
        proof=q._save(root,path,detail)
    recovery.append(root,base,phase,row=row,query=proof,native_state=state,
                    submission=submission,context=context)
