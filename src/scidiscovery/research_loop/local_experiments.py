"""Ordinary local scientific commands under the caller's native permissions."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from scidiscovery.operations.execution import (AdapterCapability, ExecutionInterfaceRegistration,
    LifecycleCapabilities, LocalFileDescriptor)
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory
from . import configuration_updates
from . import experiments as e
from . import storage_policy as storage, shared_originals

# Published by describe; this is the existing optional result format, not a
# domain evaluation or a grant to execute anything.
RESULT_SCHEMA = dict(type='object',
    required=['version','metrics','outputs'], properties=dict(
        version=dict(type='integer',enum=[1]),
        metrics=dict(type='array',items=dict(type='object',
            required=['name','value'],properties=dict(
                name=dict(type='string',minLength=1,description='Unique declared metric name.'),
                value=dict(type='number',description='Explicit finite observed value.'),
                unit=dict(description='Optional author-owned scientific description.'),
                evaluation_key=dict(description='Optional author-owned evaluation description.'),
                direction=dict(description='Optional author-owned scientific description; no direction is inferred.')))),
        outputs=dict(type='array',items=dict(type='object',
            required=['path'],properties=dict(
                path=dict(type='string',description='artifacts/<retained output>, relative to the experiment root.'),
                label=dict(description='Optional author-owned output label.')))),
        candidates=dict(type='array',items=dict(type='object',required=['artifact'],properties=dict(
                label=dict(description='Optional author-owned candidate label.'),
                metric=dict(description='Optional exact declared metric name; absent or unresolved means unknown measurement.'),
                artifact=dict(type='string',description='Exactly one declared outputs.path; unique within candidates.'))))))


class LocalExecution:
    terminal_evidence_kind = 'local_process'
    result_protocol = e.LOCAL_RESULT_PROTOCOL

    def __init__(self, config, *, frozen_interface=None):
        argv=config.get('run_argv')
        if not isinstance(argv,list) or not argv or not all(isinstance(item,str) and item for item in argv):
            raise ValueError('Explicit local command argv required')
        self.config=config
        self.records=e.Records(dict(config,execution_interface=frozen_interface) if frozen_interface is not None else config)
        self.frozen_interface=frozen_interface
        self._lease=None

    @staticmethod
    def lifecycle_capabilities():
        return LifecycleCapabilities(lookup_submission=True,query=True,collect=True,atomic_admission=True)

    @staticmethod
    def capabilities():
        from .experiment_client import frozen_code_directory_capability
        content=e.encoded(dict(kind='local_command',domain_metrics=False,
            optional_result=dict(protocol=e.LOCAL_RESULT_PROTOCOL,path='artifacts/result.json',schema=RESULT_SCHEMA,
                constraints='Metric names, output paths and candidate artifact paths must be unique. Outputs must be retained originals. Scientific descriptions are optional and preserved without scientific interpretation. The optional candidates array declares every retained model available to A; each member requires its exact retained artifact. Missing measurements stay unknown. Invalid individual declarations are diagnosed without discarding other observations. Execution success alone is not scientific success.')))
        return (AdapterCapability('local_command','execution','scidiscovery.local-command.v1',1,
            'application/json',content,dict(kind='local_command')),
            AdapterCapability(e.INPUT_SOURCES_KEY,'source_provenance',e.INPUT_SOURCES_SCHEMA,1,
                'application/json',e.encoded(e.INPUT_SOURCES_CONTRACT),
                dict(scope='Explicit current-job experiments/<name> authoring snapshots and exact bound input copies only; not arbitrary shell reads.')),
            frozen_code_directory_capability())

    def input_sources(self, payload, metadata):
        from . import execution_inputs
        return execution_inputs.local_input_sources(self.config,self.frozen_interface,payload,metadata)

    def prepare_runtime_directory(self, directory, source):
        """Create only a validated current-W Local runtime, after preflight."""
        from . import experiment_client
        config=dict(self.config,execution_interface=self.frozen_interface)
        selection=experiment_client.code_selection(config,source,experiment_directory=directory)
        if selection is None:
            raise e.CallerError('Choose current-W code with an explicit experiments/<name> Local runtime directory.')
        directory=Path(directory).absolute()
        try:directory.mkdir(parents=True,exist_ok=True)
        except OSError:
            raise e.CallerError('The selected experiments/<name> runtime directory cannot be created. Select a writable ordinary runtime directory.') from None
        if directory.resolve()!=directory or not directory.is_dir():
            raise e.CallerError('The selected experiments/<name> runtime directory must remain an ordinary directory.')

    @e.record_effect
    def prepare(self,payload,*,preparation_profile,exchange_directory):
        if preparation_profile!='research_loop.v1' or Path(exchange_directory)!=self.records.root:
            raise ValueError('Exact managed preparation context required')
        if e.proof(payload.local_path)['size_bytes']!=payload.size_bytes:
            raise e.IntegrityError('Prepared command source changed')
        argv=self.config['run_argv']
        if not isinstance(argv,list) or not argv or not all(isinstance(item,str) and item for item in argv):
            raise ValueError('Explicit local command argv required')
        seconds=self.config['max_seconds']
        if type(seconds) not in (int,float) or not 0<seconds<float('inf'):
            raise ValueError('Finite original execution time limit required')
        return payload

    def acceptance_metadata(self,payload,*,parent=None,new=False,change=None,command_options=None,_code_version=None,client_facts=None):
        options=command_options or {}; arguments=options.get('arguments',[])
        if (set(options)-{'arguments','experiment_directory','source_script'} or not isinstance(arguments,list)
                or any(not isinstance(item,str) or '\0' in item for item in arguments)):
            raise ValueError('Local command arguments must be a list of strings without NUL bytes; use -- before script arguments.')
        metadata=dict(parent=dict(status='unknown',candidate_id=None,source='none'),change_intent=None,
            local_observation_version=1,
            command=[*self.config['run_argv'],payload.local_path,*arguments],script_index=len(self.config['run_argv']),cwd=self.config.get('cwd'),
            config_record=self.config)
        if parent is not None or change is not None:
            metadata['note']='Local execution ignores --parent-plan and --change-intent; no candidate parent or change semantics are declared.'
        if _code_version is not None:
            from . import experiment_client, contracts as c
            source=Path(options.get('source_script',payload.local_path)).absolute()
            metadata.update(source_script=str(source),script=e.proof(payload.local_path))
            metadata['command'][metadata['script_index']]=str(source)
            if e.proof(payload.local_path)['size_bytes']!=payload.size_bytes:
                raise e.IntegrityError('Local code export differs from its prepared descriptor')
            if client_facts is None:
                raise e.IntegrityError('Local code metadata requires its owner-loaded scientific input facts')
            instance,work,job=client_facts['root'],client_facts['work'],client_facts['job']
            if (instance is None or job.get('step')!=self.config['scope']['step']
                    or work!=c.workspace(instance,job['step'])
                    or Path(self.config['record_dir'])!=experiment_client._record_directory_from_facts(instance,job['step'],job)):
                raise e.IntegrityError('Local code metadata belongs to another scientific task')
            runtime=Path(options.get('experiment_directory') or self.config.get('cwd') or work).absolute()
            # The final readonly overlays follow the backend's artifacts
            # exception, so an overlapping runtime target cannot be writable.
            from . import shared_input_views
            output=runtime/'artifacts'
            readonly=[work/'work'/name for name in shared_input_views.DYNAMIC_ROOTS]
            if (work/'inputs').exists():readonly.append(work/'inputs')
            readonly.extend(work/material['path'] for material in experiment_client._input_rows(job))
            if any(output.is_relative_to(path) or path.is_relative_to(output) for path in readonly):
                raise ValueError('Local artifacts overlap an existing readonly scientific input')
            if options.get('experiment_directory') is not None:
                directory=Path(options['experiment_directory']).absolute()
                if (directory.resolve()!=directory or not directory.is_dir()
                        or not directory.is_relative_to(work) or source.is_relative_to(directory)):
                    raise ValueError('Local code requires an independent ordinary runtime directory')
                files={}
                for material in experiment_client._input_rows(job):
                    logical=work/material['path']
                    if logical.is_relative_to(directory) and not logical.is_relative_to(directory/'artifacts'):
                        name=logical.relative_to(directory).as_posix()
                        files[name]=dict(experiment_client._observe_input_reference(self.config,logical,facts=client_facts),name=name)
                metadata.update(experiment_directory=str(directory),cwd=str(directory),
                    snapshot_files=[files[name] for name in sorted(files)])
            return metadata
        if options.get('experiment_directory') is not None:
            root=Path(options['experiment_directory']).absolute()
            if root.resolve()!=root or not root.is_dir() or self.records.root.is_relative_to(root):
                raise ValueError('Explicit original experiment directory must exclude execution records')
            source=Path(payload.local_path)
            if not source.is_relative_to(root) or source.relative_to(root).parts[0]=='artifacts':
                raise ValueError('Script must belong to the experiment input directory')
            metadata.update(experiment_directory=str(root),entry=source.relative_to(root).as_posix(),
                snapshot_files=self.files(root,exclude_artifacts=True,policy=self.config.get('storage_policy'),
                    context=storage.Deadline(self.config['max_seconds'])))
            entry=next(item for item in metadata['snapshot_files'] if item['name']==metadata['entry'])
            if entry['size_bytes']!=payload.size_bytes:
                raise e.IntegrityError('Experiment script changed during preparation')
            metadata['cwd']=str(self.records.root/'calls'/payload.name/'inputs')
        snapshot=self.records.root/'calls'/payload.name
        metadata['command'][metadata['script_index']]=str(snapshot/'inputs'/metadata['entry'] if 'entry' in metadata else snapshot/'source.py')
        return metadata

    @staticmethod
    def files(root,*,exclude_artifacts=False,policy=None,context=None):
        context = context or storage.Deadline(65)
        inventory = storage.Inventory(policy, context=context)
        if root.resolve()!=root or not root.is_dir():
            raise ValueError('Managed files require an original directory')
        files=[];pending=[root]
        while pending:
            directory=pending.pop()
            if directory.resolve()!=directory or not directory.is_dir():
                raise ValueError('Managed directories must remain original')
            with os.scandir(directory) as entries:
                for entry in entries:
                    inventory.entry()
                    if exclude_artifacts and directory==root and entry.name=='artifacts':
                        if not entry.is_dir(follow_symlinks=False):
                            raise ValueError('Managed artifacts output must be an original directory')
                        continue
                    path=Path(entry.path);name=path.relative_to(root).as_posix()
                    if entry.is_symlink():raise ValueError('Managed directories cannot contain links')
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path);continue
                    size=entry.stat(follow_symlinks=False).st_size
                    inventory.file(size)
                    item=e.proof(path,context=context)
                    if item['size_bytes'] != size:
                        raise e.IntegrityError('Managed file changed during its inventory')
                    files.append(dict(item,name=name))
        return sorted(files,key=lambda item:item['name'])

    def dispatch_lease(self,accepted):
        if accepted['scope']!=self.config['scope']:
            raise e.IntegrityError('Dispatch lease differs from exact frozen scope')
        self._lease=(accepted['call_id'],accepted['prepared'])

    @e.record_effect
    def lookup_submission(self,submission):
        path=self.records.root/'calls'/submission.name/'accepted.json'
        if not path.exists():return None
        accepted=self.records.accepted(submission.name)
        if accepted['prepared']!=submission.model_dump(mode='json'):
            raise e.IntegrityError('Original submission differs from prepared input')
        return submission.name,self._state(submission.name)

    @e.record_effect
    def submit(self,submission):
        found=self.lookup_submission(submission)
        if self._lease!=(submission.name,submission.model_dump(mode='json')):
            if found is not None:return found
            raise e.UnsupportedExecution('No exact accepted dispatch lease; submission is not permitted')
        self._lease=None
        if found is None:raise e.IntegrityError('Dispatch has no durable acceptance original')
        directory=self.records.directory(submission.name)
        with self._execution_writer_lease(submission.name):
            if (directory/'process.json').exists() or (directory/'terminal.json').exists():return found
            accepted=json.loads(e.read(directory/'accepted.json'))
            self._execute(submission.name,accepted)
        return submission.name,self._state(submission.name)

    def _execution_writer_lease(self,call_id):
        # A stopped child does not imply its host output callback has finished.
        # Recovery uses this same call lease before publishing a completion.
        return self.records.lease('local-writer-'+call_id+'.lock')

    def command(self,accepted):
        if 'code_version' in accepted:return list(accepted['command'])
        # Execute the exact control snapshot. cwd is separately configured;
        # imports/environment beyond this one payload are not frozen evidence.
        command=list(accepted['command']);directory=self.records.directory(accepted['call_id'])
        command[accepted.get('script_index',len(command)-1)]=str(directory/'inputs'/accepted['entry'] if 'entry' in accepted else directory/'source.py')
        return command

    def _state(self,call_id):
        from . import runtime
        directory=self.records.directory(call_id);accepted=self.records.accepted(call_id)
        path=directory/'process.json'
        identity=None
        if path.exists():
            identity=json.loads(e.read(path))
            key=dict(path=str(directory/'accepted.json'))
            if (not isinstance(identity,dict) or identity.get('task_ref')!=key
                    or any(type(identity.get(field)) is not int or identity[field]<=0
                           for field in ('pid','start_ticks','pgid','sid'))):
                raise e.IntegrityError('Original local process identity is malformed or changed')
        self._recover_completion(call_id)
        if (directory/'completed.json').exists():
            observation=directory/'execution-observation.json'
            if observation.exists():
                e.verify_observation(json.loads(e.read(observation)),lambda item:Path(item['path']),
                    interface=accepted.get('interface'),verify_files=False)
            state=self.records.state(call_id)
            if state=='unknown':return state
            terminal=json.loads(e.read(directory/'terminal.json'))
            completed=json.loads(e.read(directory/'completed.json'))
            controls={str(directory/name) for name in ('accepted.json','process.json','terminal.json')}
            for item in completed.get('proofs',[]):
                if item['path'] in controls:
                    actual=e.proof(item['path'])
                    if any(actual.get(key)!=saved for key,saved in item.items()):
                        raise e.IntegrityError('Completed execution control original changed')
            if e.local_terminal_status(accepted,completed,terminal)=='unknown':
                return 'unknown'
            if state!=('succeeded' if terminal['returncode']==0 else 'failed'):
                raise e.IntegrityError('Completed local execution differs from its transport')
            return state
        return 'running' if identity and runtime.process_matches(identity,identity['task_ref']) else 'unknown'

    def _recover_completion(self,call_id):
        """Project a stopped original Local call; never dispatch another command."""
        if type(self) is not LocalExecution:return
        from . import runtime
        from contextlib import ExitStack
        directory=self.records.directory(call_id)
        if (directory/'completed.json').exists() or not (directory/'terminal.json').exists():return
        with ExitStack() as writer:
            try:writer.enter_context(self._execution_writer_lease(call_id))
            except BlockingIOError:return  # The original host may still publish outputs.
            if (directory/'completed.json').exists():return
            accepted=self.records.accepted(call_id)
            identity=json.loads(e.read(directory/'process.json'))
            key=dict(path=str(directory/'accepted.json'))
            if (identity.get('task_ref')!=key
                    or any(type(identity.get(field)) is not int or identity[field]<=0
                        for field in ('pid','start_ticks','pgid','sid'))):
                raise e.IntegrityError('Original local process identity is malformed or changed')
            if runtime.process_matches(identity,key):return
            terminal=json.loads(e.read(directory/'terminal.json'))
            if terminal.get('call_id')!=call_id or terminal.get('scope')!=accepted['scope']:
                raise e.IntegrityError('Original Local terminal owner changed')
            if (terminal.get('cleanup_confirmed') is not True
                    or terminal.get('transport_complete') is not True
                    or type(terminal.get('returncode')) is not int):return
            completed=self.completion(directory,accepted,terminal['returncode'],True,True,
                context=storage.Deadline(self.config['max_seconds']), _recovering=True)
            self.records.publish(call_id,'completed.json',completed)

    def _execution_deadline(self,accepted):
        """Optional local partition; ordinary and previously accepted calls keep their limit."""
        return None

    def _execute(self,call_id,accepted):
        from . import runtime
        config=(dict(self.config,execution_interface=self.frozen_interface)
            if self.frozen_interface is not None else self.config)
        e.verify_scientific_inputs(accepted,config=config)
        directory=self.records.directory(call_id)
        runtime._subreaper()
        started=time.monotonic()
        transport_complete=True;cleanup=False;returncode=None
        cwd=accepted.get('cwd')
        if 'entry' in accepted:
            cwd=str(directory/'inputs');(Path(cwd)/'artifacts').mkdir()
        elif 'code_version' in accepted and (accepted.get('experiment_directory') is not None
                or accepted.get('base_identity') is not None):
            # verify_scientific_inputs above bound project metadata to the
            # original job; even an empty declared output view needs its holder.
            artifact_directory(self.config,call_id).mkdir(parents=True)
        deadline=self._execution_deadline(accepted)
        if deadline is None:deadline=started+self.config['max_seconds']
        from . import experiment_client
        storage_root = experiment_client.root_for_configuration(self.config, allow_standalone=True) or self.records.root
        limits = storage.configuration(self.config.get('storage_policy'))
        log_inventory = storage.Inventory(limits)
        log_limited = False
        with (directory/'stdout.raw').open('xb') as out,(directory/'stderr.raw').open('xb') as err:
            command=self.command(accepted)
            from . import worker_execution_bridge
            command,cwd,finish=worker_execution_bridge.local_process_launch(self.config,accepted,command,cwd,
                operation_context=storage.Deadline(max(.001,deadline-time.monotonic())))
            if deadline is not None and time.monotonic()>=deadline:
                raise TimeoutError('Original reserved experiment deadline reached before launch')
            from . import shared_input_views
            with shared_input_views.popen_arguments(command) as (launch_command,pass_fds):
                process=subprocess.Popen(launch_command,cwd=cwd,stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,start_new_session=True,pass_fds=pass_fds)
            identity=None
            try:
                identity=runtime.process_identity(process.pid,dict(path=str(directory/'accepted.json')))
                self.records.publish(call_id,'process.json',identity)
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout,selectors.EVENT_READ);selector.register(process.stderr,selectors.EVENT_READ)
                    count=0
                    while selector.get_map() or process.poll() is None:
                        for key,_ in selector.select(.1):
                            chunk=os.read(key.fileobj.fileno(),65536)
                            if not chunk:selector.unregister(key.fileobj);continue
                            sink=out if key.fileobj is process.stdout else err
                            try:
                                log_inventory.limit('max_file_bytes',sink.tell()+len(chunk))
                                log_inventory.limit('max_total_bytes',count+len(chunk))
                                with storage.mutation(storage_root,len(chunk),policy=limits,
                                        context=storage.Deadline(max(.001,deadline-time.monotonic()))):
                                    sink.write(chunk);sink.flush()
                                count+=len(chunk)
                            except storage.StorageQuotaExceeded:
                                log_limited=True
                                break
                            except TimeoutError:
                                break
                        if log_limited or time.monotonic()>=deadline:
                            transport_complete=False
                            break
            except BaseException:
                transport_complete=False
                raise
            finally:
                # An identity/receipt/IO exception after Popen must not leave an
                # unregistered running child. Preserve that original exception.
                if process.poll() is None:
                    if identity is not None and runtime.process_matches(identity,identity['task_ref']):
                        try:os.killpg(identity['pgid'],signal.SIGTERM)
                        except ProcessLookupError:pass
                    else:process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if identity is not None and runtime.process_matches(identity,identity['task_ref']):
                        try:os.killpg(identity['pgid'],signal.SIGKILL)
                        except ProcessLookupError:pass
                    else:process.kill()
                    process.wait(timeout=5)
                if identity is not None:
                    cleanup=runtime.cleanup_owned_group(identity)=='observed_group_cleaned'
                returncode=process.returncode
                process.stdout.close();process.stderr.close()
            # The child owns pipe writers, never these host sink descriptors.
            # Freeze only after draining/cleanup, before closing our final writers.
            from . import atomic_files
            atomic_files._finish(out,0o400)
            atomic_files._finish(err,0o400)
        self.records.publish(call_id,'terminal.json',dict(returncode=returncode,cleanup_confirmed=cleanup,
            transport_complete=transport_complete,wall_seconds=time.monotonic()-started,
            **(dict(storage_limit_reached=True) if log_limited else {})))
        # Output publication can fail after the child and its writers stopped.
        # Retain those execution facts before invoking the output adapter.
        if finish is not None:finish(returncode,cleanup,transport_complete)
        completion_context=storage.Deadline(max(.001,deadline-time.monotonic()))
        if 'code_version' not in accepted:
            try:
                for stream in ('stdout.raw','stderr.raw'):
                    source=directory/stream
                    identity=e.proof(source,context=completion_context)
                    shared_originals.retain(storage_root,source,size_bytes=identity['size_bytes'],
                        source_kind='managed_frozen',context=completion_context,policy=limits)
            except (TimeoutError,storage.StorageQuotaExceeded):
                # Old calls keep their saved retention contract. Neither path
                # repeats a command to recover unavailable output processing.
                pass
        self.records.publish(call_id,'completed.json',self.completion(directory,accepted,returncode,cleanup,transport_complete,
            context=completion_context))

    def completion(self,directory,accepted,returncode,cleanup,transport_complete,*,context=None,_recovering=False):
        if 'code_version' in accepted and ((directory/'completed.json').exists() or (directory/'completed.json').is_symlink()):
            if (directory!=self.records.directory(accepted['call_id'])
                    or self.records.accepted(accepted['call_id'])!=accepted):
                raise e.IntegrityError('Saved Local completion lost its exact accepted owner')
            # Public log projections may have been published after completion.
            # The original completion, rather than a new output inventory, owns
            # the backend result and its saved ordinary file identities.
            return self.result(accepted['call_id'])
        known=cleanup and transport_complete and type(returncode) is int
        script=[accepted['script']]
        project_run=False
        if 'code_version' in accepted:
            root=e._code_version_binding(dict(self.config,execution_interface=self.frozen_interface)
                if getattr(self,'frozen_interface',None) is not None else self.config,accepted)
            # The binding checks original job.project, exact base_identity and
            # directory code selection without consuming current base bytes.
            project_run=accepted.get('base_identity') is not None
            if e._code_export_entry(root,accepted) is not None:script=[]
        original=dict(call_id=accepted['call_id'],status=('known_success' if returncode==0 else 'known_failed') if known else 'unknown',
            candidate_id=None,run_id=None,model=None,parent=accepted['parent'],change_intent=None,scope=accepted['scope'],
            proofs=[e.proof(directory/name) for name in ('accepted.json','process.json','stdout.raw','stderr.raw','terminal.json')]+script,
            note='Original local command outcome; no domain metric or candidate qualification')
        if accepted.get('local_observation_version')!=1:return original
        outputs=[];result=dict(status='not_requested',metrics=[],outputs=[])
        if ('entry' in accepted or 'code_version' in accepted
                and (accepted.get('experiment_directory') is not None or project_run)):
            if not cleanup:
                result=dict(status='unavailable',metrics=[],outputs=[],diagnostic='execution_cleanup_unknown')
            else:
                try:
                    from . import experiment_client
                    root = experiment_client.root_for_configuration(self.config, allow_standalone=True) or self.records.root
                    context=context or storage.Deadline(self.config['max_seconds'])
                    artifacts=(artifact_directory(self.config,accepted['call_id']) if 'code_version' in accepted
                        else directory/'inputs/artifacts')
                    files=self.files(artifacts,policy=self.config.get('storage_policy'),context=context)
                    if 'code_version' in accepted:
                        # Validate the complete mutable inventory before any
                        # inode permission changes affect another path.
                        for item in files:
                            info=Path(item['path']).stat()
                            if info.st_mode & 0o777 != 0o400 and info.st_nlink != 1:
                                raise ValueError('Mutable Local output must not have hard-link aliases')
                    for item in files:
                        source=Path(item['path'])
                        if 'code_version' in accepted:
                            # The original backend wrote this already-bound
                            # public entity. Cleanup precedes its only seal.
                            if source.stat().st_mode & 0o777 != 0o400:source.chmod(0o400)
                            target=source
                        else:
                            target=directory/'outputs'/item['name']
                            if _recovering and (target.exists() or target.is_symlink()):
                                # A prior completion may have frozen this output
                                # before its receipt was interrupted. Reuse only
                                # the exact call slot whose retained bytes still
                                # match the stopped command's original output.
                                shared_originals.link(target,target,size_bytes=item['size_bytes'],context=context,
                                    root=root,policy=self.config.get('storage_policy'))
                                saved=e.proof(target,context=context)
                                with source.open('rb') as source_stream, target.open('rb') as target_stream:
                                    remaining=item['size_bytes']
                                    while remaining:
                                        context.remaining_seconds()
                                        block=source_stream.read(min(65536,remaining))
                                        if not block or block!=target_stream.read(len(block)):
                                            raise e.IntegrityError('Interrupted Local output differs from its retained original')
                                        remaining-=len(block)
                                    if source_stream.read(1) or target_stream.read(1):
                                        raise e.IntegrityError('Interrupted Local output grew during recovery')
                                if (e.proof(source,context=context)!= {key:value for key,value in item.items() if key!='name'}
                                        or e.proof(target,context=context)!=saved):
                                    raise e.IntegrityError('Interrupted Local output changed during recovery')
                            else:
                                frozen_original=shared_originals.retain(root,source,size_bytes=item['size_bytes'],source_kind='mutable',
                                    context=context,policy=self.config.get('storage_policy'))
                                shared_originals.link(frozen_original,target,size_bytes=item['size_bytes'],context=context,
                                    root=root,policy=self.config.get('storage_policy'))
                        frozen=e.proof(target,context=context)
                        if any(frozen[key]!=item[key] for key in ('size_bytes','file_stat')) and target==source:
                            raise e.IntegrityError('Local output changed during its terminal seal')
                        outputs.append(dict(frozen,name=item['name']))
                    result=self.read_result(artifacts if 'code_version' in accepted else directory/'outputs',outputs)
                except storage.StorageQuotaExceeded:
                    result=dict(status='unavailable',metrics=[],outputs=[],diagnostic='managed_output_storage_limit')
                except (OSError,ValueError) as error:
                    result=dict(status='unavailable',metrics=[],outputs=[],diagnostic='managed_output_io' if isinstance(error,OSError) else 'managed_output_invalid')
        terminal=json.loads(e.read(directory/'terminal.json'))
        completed=dict(original,
            outputs=outputs,result=result,wall_seconds=terminal.get('wall_seconds'),returncode=returncode,
            note='Observed local command and declared results; external imports/environment and scientific validity are not qualified')
        return completed

    @staticmethod
    def read_result(directory,outputs):
        path=directory/'result.json'
        if not path.exists():return dict(status='missing',metrics=[],outputs=[])
        try:
            if path.stat().st_size>e.MAX_BYTES:
                return dict(status='unavailable',metrics=[],outputs=[],diagnostic='result_declaration_limit',
                    details_ref='result.json')
            def nonfinite(_):raise ValueError('Nonfinite JSON value')
            value=json.loads(e.read(path,e.MAX_BYTES),parse_constant=nonfinite)
        except (OSError,ValueError,OverflowError,RecursionError) as error:
            return dict(status='invalid',metrics=[],outputs=[],diagnostic='result_io' if isinstance(error,OSError) else 'result_json_invalid',
                detail='result.json original could not be read' if isinstance(error,OSError) else 'result.json must contain strict JSON')
        return LocalExecution.parse_result(value,outputs)

    @staticmethod
    def parse_result(value,outputs):
        """Project an owner-decoded result over its already retained output rows."""
        detail='result requires version=1 and metrics and outputs arrays'
        try:
            if (not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1
                    or not isinstance(value.get('metrics'),list) or not isinstance(value.get('outputs'),list)):
                raise ValueError('Expected version 1 metrics and outputs')
            diagnostics=[];metrics=[];name_counts={}
            def serializable_fields(item,field):
                # Decoding can overflow JSON exponents. Keep exact source bytes,
                # but omit only fields the strict completion record cannot store.
                projected={}
                for key,part in item.items():
                    try:e.encoded({key:part})
                    except (ValueError,OverflowError,RecursionError):
                        diagnostics.append(dict(field=field,code='scientific_description_projection_unavailable',details_ref='result.json'))
                    else:projected[key]=part
                return projected
            for metric in value['metrics']:
                if isinstance(metric,dict) and isinstance(metric.get('name'),str):
                    name_counts[metric['name']]=name_counts.get(metric['name'],0)+1
            for index,metric in enumerate(value['metrics']):
                valid=(isinstance(metric,dict) and isinstance(metric.get('name'),str) and bool(metric['name'])
                    and name_counts[metric['name']]==1 and type(metric.get('value')) in (int,float))
                try:valid=valid and math.isfinite(metric['value'])
                except OverflowError:valid=False
                if valid:
                    try:e.encoded(dict(name=metric['name'],value=metric['value']))
                    except (ValueError,OverflowError,RecursionError):valid=False
                if not valid:
                    diagnostics.append(dict(field=f'metrics[{index}]',code='metric_name_or_finite_value_invalid'))
                    continue
                metrics.append(serializable_fields(metric,f'metrics[{index}]'))
            names={'artifacts/'+item['name'] for item in outputs}
            retained=[];seen=set()
            output_paths=[item.get('path') for item in value['outputs'] if isinstance(item,dict) and isinstance(item.get('path'),str)]
            for index,item in enumerate(value['outputs']):
                if (not isinstance(item,dict) or not isinstance(item.get('path'),str)
                        or item['path'] not in names or output_paths.count(item['path'])!=1):
                    diagnostics.append(dict(field=f'outputs[{index}]',code='output_original_mapping_invalid'))
                    continue
                seen.add(item['path']);retained.append(serializable_fields(item,f'outputs[{index}]'))
            result=dict(status='available',metrics=metrics,outputs=retained)
            if 'candidates' in value:
                result['candidates']=[]
                declared=value['candidates']
                if not isinstance(declared,list):
                    diagnostics.append(dict(field='candidates',code='candidate_list_invalid'))
                    declared=[]
                artifacts=[item.get('artifact') for item in declared if isinstance(item,dict) and isinstance(item.get('artifact'),str)]
            else:declared=[];artifacts=[]
            for index,candidate in enumerate(declared):
                if (not isinstance(candidate,dict) or not isinstance(candidate.get('artifact'),str)
                        or candidate['artifact'] not in seen or artifacts.count(candidate['artifact'])!=1):
                    diagnostics.append(dict(field=f'candidates[{index}].artifact',code='candidate_original_mapping_invalid'))
                else:
                    result['candidates'].append(serializable_fields(candidate,f'candidates[{index}]'))
                    if ('metric' in candidate and not any(isinstance(candidate['metric'],str)
                            and item['name']==candidate['metric'] for item in metrics)):
                        diagnostics.append(dict(field=f'candidates[{index}].metric',code='candidate_metric_mapping_unknown'))
            if diagnostics:result['diagnostics']=diagnostics
            return result
        except (OSError,ValueError,OverflowError,RecursionError) as error:
            return dict(status='invalid',metrics=[],outputs=[],diagnostic='result_io' if isinstance(error,OSError) else 'result_json_invalid',
                detail='result.json original could not be read' if isinstance(error,OSError) else detail)

    @e.record_effect
    def result(self,call_id):
        directory=self.records.directory(call_id)
        self._recover_completion(call_id)
        path=directory/'completed.json'
        if not path.exists():return dict(status='unknown',call_id=call_id,note='Original execution has no confirmed completion')
        result=json.loads(e.read(path))
        if result['call_id']!=call_id or result['scope']!=self.config['scope']:
            raise e.IntegrityError('Completed execution binding changed')
        accepted=self.records.accepted(call_id)
        if result.get('mechanism_binding')!=accepted.get('mechanism_binding'):
            raise e.IntegrityError('Completed scientific mechanism binding changed')
        for item in result['proofs']:
            if any(e.proof(item['path']).get(key)!=saved for key,saved in item.items()):raise e.IntegrityError('Completed execution original changed')
        for item in result.get('outputs',[]):
            if any(e.proof(item['path'])[key]!=item[key] for key in ('path','size_bytes','file_stat') if key in item):
                raise e.IntegrityError('Completed output original changed')
        if 'code_version' not in accepted:
            for item in accepted.get('snapshot_files',[]):
                original=e.proof(directory/'inputs'/item['name'])
                if any(original[key]!=item[key] for key in ('size_bytes',)):
                    raise e.IntegrityError('Accepted input snapshot changed')
        return result

    @e.record_effect
    def status(self,external_run_id):
        return self._state(external_run_id)

    @e.record_effect
    def status_details(self,external_run_id):
        state=self._state(external_run_id)
        result={}
        if state in {'succeeded','failed'}:
            result=json.loads(e.read(self.records.directory(external_run_id)/'completed.json'))
            if result['status']=='unknown':state='unknown'
        return dict(state=state,call_id=external_run_id,candidate_id=None,evaluation_key=None,metric=None,
            direction=None,wall_seconds=result.get('wall_seconds'),returncode=result.get('returncode'),
            result=self.public_result(result).get('result'),parent_candidate=None)

    def cancel(self,external_run_id):
        raise e.UnsupportedExecution('Original command cancellation is not declared')

    @e.record_effect
    def cancel_original(self,call_id):
        external=self.records.external(call_id)
        if external is None:raise e.UnsupportedExecution('Original external execution is unknown')
        return self.cancel(external)

    @e.record_effect
    def collect_original(self,call_id):
        external=self.records.external(call_id)
        if external is None:raise e.UnsupportedExecution('Original external execution is unknown')
        return self.collect(external)

    @e.record_effect
    def collect(self,external_run_id):
        if self.status(external_run_id) not in {'succeeded','failed'}:
            raise e.UnsupportedExecution('Original command is not confirmed terminal')
        result=self.result(external_run_id)
        directory=self.records.directory(external_run_id)
        paths=[directory/name for name in ('stdout.raw','stderr.raw')]+[Path(item['path']) for item in result.get('outputs',[])]
        return tuple(LocalFileDescriptor(name=f'output-{index}',local_path=str(path),
            size_bytes=path.stat().st_size,
            media_type='application/octet-stream') for index,path in enumerate(paths))

    @staticmethod
    def scientific_source(accepted, *, implementation=None):
        return accepted['script']

    @staticmethod
    def public_result(result):
        view={key:result[key] for key in ('call_id','status','note','returncode','wall_seconds') if key in result}
        if 'result' in result:
            original=result['result'];compact={key:value for key,value in original.items() if key not in {'metrics','outputs','candidates','diagnostics'}}
            # These are display projections. Exact declarations remain in the
            # retained result.json, independent of the response-size allowance.
            def preview(value):return e.encoded(value)[:1024].decode('utf-8',errors='ignore')
            diagnostics=original.get('diagnostics',[])
            if diagnostics:
                compact['diagnostics']=[]
                for row in diagnostics:
                    if len(e.encoded(dict(compact,diagnostics=[*compact['diagnostics'],row])))>8_000:break
                    compact['diagnostics'].append(row)
                if len(compact['diagnostics'])<len(diagnostics):
                    compact['diagnostics_omitted_count']=len(diagnostics)-len(compact['diagnostics'])
                    compact['details_ref']='result.json'
            for name in ('candidates','metrics','outputs'):
                compact[name]=[];rows=original.get(name,[])
                for row in rows:
                    projected=row
                    if len(e.encoded(row))>8_000:
                        projected=dict(preview=preview(row),details_ref='result.json')
                    trial=dict(compact,**{name:[*compact[name],projected]})
                    if len(e.encoded(trial))>15_000:break
                    compact[name].append(projected)
                if len(compact[name])<len(rows):
                    compact[name+'_omitted_count']=len(rows)-len(compact[name])
                    compact['details_ref']='result.json'
            view['result']=compact
        return view


def build(context):
    return RuntimePluginContribution(execution_adapters={'command':LocalExecution(json.loads(context.config_bytes),
        frozen_interface=context.frozen_execution_interface)})


def configure(configuration, source, *, binding):
    from .python_environment import check_python_requirements
    return check_python_requirements(configuration, plugin_id='local_execution')


REGISTRATION=ExecutionInterfaceRegistration('local_execution','1',RuntimePluginFactory(build, configure=configure),
    scientific_source=LocalExecution.scientific_source,projection_contract='scidiscovery.local-projections.v1')


def cleanup(root,job,*,watchdog=False):
    """Clean only this native owner's accepted local groups; never replay calls."""
    from . import contracts as c, execution, protocol, runtime
    if job.get('experiment_mode')!='local' or job['role']!='W':return 'not_applicable'
    path=execution.record(root,job['step'])/'experiment-config.json'
    if not path.exists():return 'not_configured'
    config=c.read(c.regular(path))
    selected=configuration_updates.for_job(root, job).get('execution_config')
    expected=selected['executor'] if selected is not None else 'local_execution:command'
    if config['scope']!=protocol.stage_scope(root,job) or config['execution_interface']['executor']!=expected:
        raise e.IntegrityError('Local cleanup escaped its frozen native owner')
    if expected!='local_execution:command':return 'not_applicable'
    e.load(config,path=path)
    result='observed_group_cleaned'
    records=e.Records(config)
    for directory in records.calls():
        accepted=records.accepted(directory.name)
        if accepted.get('local_observation_version')!=1:raise e.IntegrityError('Local cleanup original differs')
        terminal=directory/'terminal.json'
        if terminal.exists():
            value=json.loads(e.read(terminal))
            if value.get('call_id')!=directory.name or value.get('scope')!=config['scope']:
                raise e.IntegrityError('Local terminal owner differs')
            if value.get('cleanup_confirmed') is True:continue
        process=directory/'process.json'
        if not process.exists():result='cleanup_unknown';continue
        identity=json.loads(e.read(process));key=dict(path=str(directory/'accepted.json'))
        if identity.get('task_ref')!=key or identity.get('pgid')!=identity.get('pid') or identity.get('sid')!=identity.get('pid'):
            raise e.IntegrityError('Local cleanup process binding differs')
        status=runtime.signal_observed_group(identity) if watchdog else runtime.cleanup_owned_group(identity)
        if status!='observed_group_cleaned':result='cleanup_unknown'
    return result


def _validate_artifact_owner(config,call_id,*,root,work,job):
    """Pure binding check over the launch owner's already loaded originals."""
    from . import contracts as c, experiment_client
    scope=config.get('scope')
    if (not isinstance(scope,dict) or not isinstance(job,dict)
            or type(job.get('step')) is not int or job['step']<=0
            or job['step']!=scope.get('step')
            or not root.is_absolute() or '..' in root.parts
            or work!=c.workspace(root,job['step'])
            or config.get('record_dir')!=str(experiment_client._record_directory_from_facts(root,job['step'],job))
            or scope.get('job_ref')!=dict(path=str(root/'jobs'/f'{job["step"]:03d}.json'))):
        raise e.IntegrityError('Local artifacts belong to another owner-loaded task')
    if not isinstance(call_id,str) or not e.ID.fullmatch(call_id) or call_id in ('.','..'):
        raise e.IntegrityError('Local artifacts require the exact original call identity')


def artifact_directory_from_facts(config,call_id,*,root,work,job):
    """Launch-boundary producer, NOT a pure argv projector.

    Immutable configuration/task facts arrive from their owner. Only the current
    call directory and mutable retained-root binding are observed here; never
    reload a task, accepted call, native intent or execution configuration.
    The publication owner, not a provider's namespace, selects the output root.
    """
    from . import shared_input_views
    root,work=Path(root),Path(work)
    _validate_artifact_owner(config,call_id,root=root,work=work,job=job)
    e.Records(config).directory(call_id)
    retained=shared_input_views.dynamic_root(root,work,'retained-experiments')
    return retained/call_id/'outputs'


def artifact_directory(config,call_id):
    """One backend-owned output entity in the already-bound public root."""
    from . import contracts as c, experiment_client, shared_input_views
    records=e.Records(config)
    records.directory(call_id)  # Validate the existing exact call identity.
    root=experiment_client.root_for_configuration(config,allow_standalone=True)
    if root is None:
        return records.directory(call_id)/'outputs'
    return (shared_input_views.dynamic_root(root,c.workspace(root,config['scope']['step']),
        'retained-experiments')/call_id/'outputs')
