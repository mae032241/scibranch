"""A lifecycle-bound host execution bridge for an exact scientific task.

Ordinary submission files request existing experiment CLI operations. Host originals and
dispatch configuration never enter the scientific namespace. The launcher's
trusted process wrapper preserves the declared scientific execution namespace.
"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from contextvars import ContextVar
import fcntl
import ctypes
import signal
import io
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from types import SimpleNamespace
import sys
import threading

MAX_BYTES = 256_000
MAX_REQUEST_BYTES = 64_000
SUBMISSION_DIRECTORY = 'work/experiment'
SUBMISSION_REF = re.compile(r'S[0-9]{3,6}\Z')
WAIT_SECONDS = 3600
COMMANDS = frozenset({'capabilities','describe','compile','run','lineage-status',
    'query','lookup','collect','cancel','reconcile','retire','research-declare'})
PATH_OPTIONS = frozenset({'--script','--payload','--experiment-dir','--implementation',
    '--scientific-files','--output','--research'})
VALUE_OPTIONS = PATH_OPTIONS | {'--start','--key','--parent-plan','--change-intent','--mechanism','--call-id'}
FLAG_OPTIONS = frozenset({'--metadata','--new-plan'})
_LOCAL_LAUNCH = ContextVar('scientific_local_launch', default=None)
_LOCAL_LAUNCH_CONTEXT = ContextVar('scientific_local_launch_context', default=None)


class _FileReplaced(ValueError):
    """An ordinary regular file was atomically replaced during this read."""


def operation_context():
    """The original local call deadline, available only during host wrapping."""
    return _LOCAL_LAUNCH_CONTEXT.get()


def local_process_launch(config, accepted, command, cwd, *, operation_context=None):
    launch = _LOCAL_LAUNCH.get()
    if launch is None:
        from .experiments import UnsupportedExecution
        raise UnsupportedExecution('Shared input readonly view requires the original host launch owner; no bare local subprocess fallback')
    token=_LOCAL_LAUNCH_CONTEXT.set(operation_context)
    try:
        return launch(config, accepted, command, cwd)
    finally:
        _LOCAL_LAUNCH_CONTEXT.reset(token)


def local_process_available():
    """Reject a host's already known unsupported launcher before admission."""
    launch=_LOCAL_LAUNCH.get()
    check=getattr(launch,'check_available',None)
    if check is not None:check()


def _option_arguments(arguments):
    """Split known value options in a derived view, preserving values and script tails."""
    result=[];index=0
    while index<len(arguments):
        argument=arguments[index]
        if argument=='--':
            result.extend(arguments[index:]);break
        if isinstance(argument,str):
            option,separator,value=argument.partition('=')
            if separator and option in VALUE_OPTIONS:
                result.extend([option,value]);index+=1;continue
        result.append(argument);index+=1
        if isinstance(argument,str) and argument in VALUE_OPTIONS and index<len(arguments):
            result.append(arguments[index]);index+=1
    return result


def _arguments(arguments, work, config=None):
    from .experiments import CallerError
    if (not isinstance(arguments,list) or not arguments
            or any(not isinstance(arg,str) or '\x00' in arg for arg in arguments)
            or arguments[0] not in COMMANDS):
        raise CallerError('Choose an existing scientific command with exp help.')
    result=[arguments[0]];index=1
    while index<len(arguments):
        tokens=_option_arguments([arguments[index]])
        option=tokens[0];inline=len(tokens)==2
        if option=='--' and arguments[0]=='run':
            result.extend(arguments[index:]);break
        if option in FLAG_OPTIONS:
            result.append(option);index+=1;continue
        if option not in VALUE_OPTIONS or not inline and index+1>=len(arguments):
            raise CallerError('Check required options with exp help <command>; separate scientific script arguments with --.')
        value=tokens[1] if inline else arguments[index+1]
        if option=='--mechanism' and arguments[0] in {'run','compile'}:
            if re.fullmatch(r'[1-9][0-9]*',value) is None:
                raise CallerError('Use the exact positive decimal mechanism number from the frozen public catalog.')
        if option in PATH_OPTIONS:
            path=Path(value)
            if not path.is_absolute():path=work/path
            if (path.resolve()!=path or not path.is_relative_to(work)
                    or path==work):
                raise CallerError('Use an ordinary scientific file inside the assigned task workspace; do not use links or parent directories.')
            if option!='--output':
                try:
                    if option=='--experiment-dir':
                        relative=path.relative_to(work)
                        local_runtime=(config is not None and config.get('scope',{}).get('role')=='W'
                            and config.get('execution_interface',{}).get('executor')=='local_execution:command'
                            and len(relative.parts)==2 and relative.parts[0]=='experiments')
                        if not path.is_dir() and not (local_runtime and not path.exists()):
                            raise CallerError('Select an ordinary --experiment-dir directory. Current-W Local execution can create an explicit experiments/<name> runtime directory.',error='scientific_input_unusable')
                    elif config is not None:
                        from . import experiment_client
                        experiment_client.input_reference(config,path)
                    elif not path.is_file():raise FileNotFoundError(path)
                except FileNotFoundError:
                    raise CallerError(f'The selected {option} scientific input is missing. Write or select its task file; script paths are relative to the task workspace, independently of --experiment-dir.',error='scientific_input_not_found') from None
            value=str(path)
        # Inline values may themselves start with an option prefix. Preserve
        # their argparse meaning after owner validation and path resolution.
        result.extend([f'{option}={value}'] if inline else [option,value])
        index+=1 if inline else 2
    return result


def _read_file(path):
    path=Path(path)
    if path.resolve()!=path:raise ValueError('Exact ordinary submission path required')
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK),'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size>MAX_BYTES:
            raise ValueError('Bounded ordinary submission file required')
        data=stream.read(MAX_BYTES+1)
        after=os.fstat(stream.fileno())
        current=path.lstat()
    if (not stat.S_ISREG(current.st_mode) or len(data)>MAX_BYTES
            or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns)):
        raise ValueError('Submission changed while reading')
    if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):
        raise _FileReplaced('Ordinary file generation changed while reading')
    return data


def _read_response(path):
    # Only a proven regular-file generation replacement is retryable. Invalid
    # JSON, special files, symlinks and wrong request identities remain errors.
    for attempt in range(5):
        try:return _read_file(path)
        except _FileReplaced:
            if attempt==4:raise
            time.sleep(.01)


def _publish(path, data):
    if len(data)>MAX_BYTES:raise ValueError('Execution message exceeds its declared bound')
    path=Path(path)
    if path.resolve()!=path:raise ValueError('Exact ordinary response path required')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.submission-',delete=False) as stream:
            temporary=Path(stream.name);stream.write(data);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(parent)
        finally:os.close(parent)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def _encoded(value):
    return (json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode()


def _submission(raw):
    if len(raw)>MAX_BYTES:raise ValueError('Scientific message exceeds its declared transport bound')
    try:value=json.loads(raw)
    except RecursionError:raise ValueError('Ordinary scientific submission required') from None
    if (not isinstance(value,dict) or set(value) not in ({'submission_ref','arguments'},{'submission_ref','arguments','client_error'})
            or 'client_error' in value and value['client_error']!='submission_frame_too_large'
            or not isinstance(value['submission_ref'],str)
            or not SUBMISSION_REF.fullmatch(value['submission_ref'])):
        raise ValueError('Ordinary submission reference and existing command required')
    return value


def _frame_refusal(size=None):
    from .experiments import CallerError
    note=(f'Scientific submission is {size} bytes; the request frame limit is {MAX_REQUEST_BYTES} bytes.'
        if size is not None else
        f'The original scientific request was rejected before publication because its request/response would exceed the {MAX_BYTES}-byte transport frame.')
    return CallerError(note+' Shorten command arguments or use a scientific input file.',error='submission_frame_too_large')


def _client_request(reference,arguments):
    """Use the existing claim channel for a proven prepublication rejection."""
    from . import experiments as e,managed_wait
    request=dict(submission_ref=reference,arguments=arguments)
    raw=_encoded(request)
    result=e.caller_feedback(_frame_refusal(len(raw)))
    result['handoff']=managed_wait.handoff(None,result,'run')
    response=dict(code=2,result=result,submission_ref=reference,request=request)
    if len(raw)<=MAX_BYTES and len(_encoded(response))<=MAX_BYTES:return request
    control=_option_arguments(arguments)
    if '--' in control:control=control[:control.index('--')]
    selected=control[:1]
    if '--mechanism' in control:
        index=control.index('--mechanism')
        if index+1<len(control):selected=[*selected,'--mechanism',control[index+1]]
    notification=dict(submission_ref=reference,arguments=selected,client_error='submission_frame_too_large')
    if len(_encoded(notification))>MAX_REQUEST_BYTES:
        notification['arguments']=selected[:1]
    if len(_encoded(notification))>MAX_REQUEST_BYTES:
        notification['arguments']=[]
    return notification


@contextmanager
def _environment(values):
    previous={key:os.environ.get(key) for key in values}
    os.environ.update(values)
    try:yield
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def _configuration(server):
    from . import contracts as c,experiments as e,experiment_client
    client=c.read(c.regular(server.config_path))
    config=experiment_client.resolve(server.config_path,client)
    if client!=server.client or config!=server.configuration:
        raise e.IntegrityError('Original task or attempt changed')
    return config


def _execute(server, arguments):
    from . import experiments as e
    config=_configuration(server)
    arguments=_arguments(arguments,server.work,config)
    from . import contracts as c,experiment_client,budget,budget_limits
    shared_deadline=_bridge_deadline(experiment_client.root_for_configuration(config),config)
    deadline=budget_limits.minimum(shared_deadline,
        time.clock_gettime(time.CLOCK_BOOTTIME)+config['max_seconds']+8)
    c.write(server.record/'bridge-operation.json',dict(boot_id=budget.clock_stamp()['boot_id'],deadline_boottime=deadline))
    if server.stop.is_set():return _unknown()
    out=io.StringIO();err=io.StringIO()
    def trusted_launch(body,accepted,command,cwd):
        expected={key:value for key,value in config.items() if key!='execution_interface'}
        if (body!=expected or accepted.get('scope')!=config['scope']
                or accepted.get('native_attempt')!=config.get('native_attempt')
                or accepted.get('interface')!=config['execution_interface']
                or accepted.get('config_record')!=config['execution_interface']['config_record']
                or e.Records(config).accepted(accepted['call_id'])!=accepted):
            raise e.IntegrityError('Scientific process differs from its accepted frozen configuration')
        _configuration(server)
        if server.stop.is_set():raise e.IntegrityError('Original task submission lifetime ended')
        if not accepted.get('execution_authority'):
            # Retain the local call's finite max_seconds and existing wait(5)
            # plus group-cleanup(3) grace within the frozen shared permission.
            c.write(server.record/'bridge-operation.json',dict(boot_id=budget.clock_stamp()['boot_id'],
                deadline_boottime=budget_limits.minimum(shared_deadline,
                    time.clock_gettime(time.CLOCK_BOOTTIME)+config['max_seconds']+8)))
        return server.launch(config,accepted,command,cwd)
    trusted_launch.check_available=getattr(server.launch,'check_available',lambda:None)
    token=_LOCAL_LAUNCH.set(trusted_launch)
    try:
        with _environment(server.environment),redirect_stdout(out),redirect_stderr(err):
            try:code=e.main(['--config',str(server.config_path),*arguments])
            except SystemExit:raise ValueError('Scientific execution command options are invalid') from None
    finally:_LOCAL_LAUNCH.reset(token)
    return dict(code=code,result=json.loads(out.getvalue()))


def _unknown():
    from . import managed_wait
    return dict(code=2,result=dict(status='unknown',
        note='Preserve the original submission and reconcile its original call; no automatic replay',
        handoff=managed_wait.handoff(None,{},'run')))


def _call_evidence(config, call_id):
    from . import experiments as e
    directory=e.Records(config).directory(call_id)
    value=dict(call_id=call_id,accepted=e.proof(directory/'accepted.json'))
    for key,name in (('completed','completed.json'),('observation','execution-observation.json'),
            ('retirement','submission-retirement.json')):
        path=directory/name;value[key]=e.proof(path) if path.exists() else None
    return value


def _verify_calls(config, calls, *, arguments=None, response=None):
    """Pure original-byte verification; no factory, backend query or replay."""
    from . import experiments as e
    purpose=_request_consumption(arguments) if calls and arguments is not None else None
    result=response.get('result',{}) if isinstance(response,dict) else {}
    consume=(purpose is not None and purpose.command=='collect' and result.get('status')=='collected')
    fields=('path','size_bytes','file_stat')
    def bound(item):
        reference={key:item[key] for key in fields if key in item}
        if any(e.proof(reference['path']).get(key)!=saved for key,saved in reference.items()):
            raise e.IntegrityError('Cached submission original changed')
    complete=True
    for call in calls:
        records=e.Records(config);directory=records.directory(call['call_id'])
        current=_call_evidence(config,call['call_id'])
        if set(call)!=set(current) or any(value is not None and (
                not isinstance(value,dict) and current[key]!=value or isinstance(value,dict)
                and (not isinstance(current[key],dict)
                    or any(current[key].get(field)!=saved for field,saved in value.items())))
                for key,value in call.items()):
            raise e.IntegrityError('Cached submission original call changed')
        accepted=records.accepted(call['call_id']);selected=[];source_controls=()
        if current['observation'] is None and accepted.get('execution_authority') is not None:
            body=accepted.get('interface',{}).get('config_record')
            if isinstance(body,dict) and body.get('record_dir') is not None:
                from . import experiment_client
                frozen=dict(body,execution_interface=accepted['interface'])
                if experiment_client.root_for_configuration(frozen,allow_standalone=True) is not None:
                    source_controls=e._source_control_metadata(frozen,accepted)['control_paths']
        if current['observation'] is not None:
            observed=json.loads(e.read(directory/'execution-observation.json'))
            e.verify_observation(observed,lambda item:Path(item['path']),
                interface=accepted['interface'],verify_files=False)
            if consume and call['call_id']==purpose.call_id:
                # Bind only outputs returned in this bounded cached response.
                # Reading a cache never reruns the frozen public projection.
                selected=observed.get('scientific_outputs',observed['outputs'])
        elif current['retirement'] is not None:
            value=json.loads(e.read(directory/'submission-retirement.json'))
            science,controls=e._observation_file_roles(None,accepted,{},
                directory=directory,source_controls=source_controls)
            script={key:accepted['script'][key] for key in fields if key in accepted['script']}
            e.verify_retirement(value,lambda item:Path(item['path']),
                interface=accepted['interface'],verify_files=script['path'] in controls or script not in science)
        else:
            completed=(json.loads(e.read(directory/'completed.json'))
                if current['completed'] is not None else {})
            if completed and any(completed.get(key)!=accepted.get(key)
                    for key in ('call_id','scope','mechanism_binding')):
                raise e.IntegrityError('Cached submission terminal binding changed')
            science,controls=e._observation_file_roles(None,accepted,completed,
                directory=directory,source_controls=source_controls)
            for item in accepted.get('snapshot_files',[]):
                relative=Path(item['name'])
                if relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=item['name']:
                    raise e.IntegrityError('Cached submission snapshot namespace changed')
            for item in [accepted['script'],*completed.get('proofs',[])]:
                reference={key:item[key] for key in fields if key in item}
                if item['path'] in controls or reference not in science:bound(item)
            if accepted.get('execution_authority') is not None:bound(accepted['execution_authority'])
            if isinstance(accepted.get('tcad'),dict):
                for key in ('prepared_ref','authority'):
                    item=accepted['tcad'].get(key)
                    if isinstance(item,dict) and 'path' in item:bound(item)
            if (directory/'submission.json').exists():records.external(call['call_id'])
            process=directory/'process.json'
            if accepted.get('interface',{}).get('terminal_evidence')=='local_process' and process.exists():
                identity=json.loads(e.read(process))
                if (not isinstance(identity,dict) or identity.get('task_ref')!=dict(path=str(directory/'accepted.json'))
                        or any(type(identity.get(key)) is not int or identity[key]<=0
                            for key in ('pid','start_ticks','pgid','sid'))):
                    raise e.IntegrityError('Cached submission process binding changed')
            if consume and call['call_id']==purpose.call_id:
                selected=[*completed.get('outputs',[])]
                if isinstance(completed.get('model'),dict):selected.append(completed['model'])
                selected.extend(item for item in completed.get('proofs',[])
                    if Path(item['path']).parent==directory and Path(item['path']).name in {'stdout.raw','stderr.raw'})
        if consume and call['call_id']==purpose.call_id:
            outputs=result.get('outputs')
            if not isinstance(outputs,list):raise e.IntegrityError('Cached collection outputs changed')
            for output in outputs:
                if not isinstance(output,dict):raise e.IntegrityError('Cached collection output is malformed')
                path=output.get('path',output.get('local_path'))
                from . import experiment_client, contracts as c
                root=experiment_client.root_for_configuration(config,allow_standalone=True)
                work=c.workspace(root,config['scope']['step']) if root is not None else None
                matches=[]
                for item in selected:
                    target=(str(work/'work/retained-experiments'/call['call_id']/'outputs'/item['name'])
                        if work is not None and 'name' in item else item.get('path'))
                    if (path not in {item.get('path'),target} or item.get('size_bytes')!=output.get('size_bytes')
                            or 'name' in item and item['name']!=output.get('name')):continue
                    matches.append(dict({key:item[key] for key in fields if key in item},path=path))
                if not matches or any(item!=matches[0] for item in matches):
                    raise e.IntegrityError('Cached collection lost its declared original output')
                bound(matches[0])
        if call['observation'] is None and call['retirement'] is None:complete=False
    return complete


def _pending_cache(response,calls):
    result=_unknown();original=response.get('result',{})
    reference=original.get('call_id')
    if reference is None and len(calls)==1:reference=calls[0]['call_id']
    if reference is not None:result['result']['call_id']=reference
    if 'state' in original:result['result']['state']='unknown'
    if isinstance(original.get('calls'),list):
        result['result']['calls']=[dict(
            {key:row[key] for key in ('call_id','mechanism_ref') if key in row},status='unknown')
            for row in original['calls']]
    return result


def _response_calls(server, request, response, before):
    from . import experiments as e
    records=e.Records(server.configuration)
    current={path.name for path in records.calls()}
    references=current-before
    result=response.get('result',{})
    if result.get('accepted') is False and not references:return []
    if isinstance(result.get('call_id'),str):references.add(result['call_id'])
    for row in result.get('calls',[]):
        if isinstance(row,dict) and isinstance(row.get('call_id'),str):references.add(row['call_id'])
    arguments=request['arguments']
    if not isinstance(arguments,list) or any(not isinstance(item,str) for item in arguments):
        arguments=[]
    arguments=_option_arguments(arguments)
    if '--' in arguments:arguments=arguments[:arguments.index('--')]
    if '--call-id' in arguments:
        index=arguments.index('--call-id')
        if index+1<len(arguments) and arguments[index+1] in current:
            references.add(arguments[index+1])
    calls=[];preparations=None
    for reference in sorted(references):
        directory=records.root/'calls'/reference
        if (arguments[:1]==['run'] and result.get('status')=='unknown'
                and result.get('call_id')==reference
                and e.ID.fullmatch(reference) and reference not in {'.','..'}
                and not directory.exists() and not directory.is_symlink()):
            from . import experiment_client,preparation_recovery,storage_policy as storage
            if preparations is None:
                root=experiment_client.root_for_configuration(server.configuration)
                preparations=preparation_recovery.retained_preparations(root,records.root,
                    context=storage.Deadline(65))
            if any(row['call_id']==reference and not row['resolved'] for row in preparations):
                # This existing original is preparation-unknown, with no accepted
                # call facts to cache. Retain the unknown response and exact ID.
                continue
        calls.append(_call_evidence(server.configuration,reference))
    return calls


def _handle(server, raw, *, retained=False):
    from . import contracts as c,experiments as e
    request=_submission(raw);reference=request['submission_ref']
    claim=server.claims/reference
    try:
        _configuration(server)
        # The private directory claims this reference before any effect. A crash
        # after this point can only produce unknown, never another execution.
        try:claim.mkdir(mode=0o700)
        except FileExistsError:
            if _read_file(claim/'submit.json')!=raw:
                raise ValueError('Submission reference already belongs to different original bytes')
            binding=c.read(c.regular(claim/'binding.json'))
            if (not isinstance(binding,dict)
                    or set(binding)!={'step','attempt','configuration','retained_at_start','submission'}
                    or binding['step']!=server.client['step']
                    or type(binding['attempt']) is not int or binding['attempt']<0
                    or binding['configuration'].get('scope')!=server.configuration['scope']
                    or any(e.proof(claim/'submit.json').get(key)!=item for key,item in binding['submission'].items())):
                raise ValueError('Submission original binding differs')
            response_path=claim/'response.json';outcome=claim/'outcome.json'
            if response_path.exists() and outcome.exists():
                expected=c.read(c.regular(outcome))
                if (not isinstance(expected,dict) or set(expected)!={'submission','response','calls'}
                        or any(e.proof(claim/'binding.json').get(key)!=item for key,item in expected['submission'].items())
                        or any(e.proof(response_path).get(key)!=item for key,item in expected['response'].items())
                        or not isinstance(expected['calls'],list)):
                    raise ValueError('Submission original result differs')
                response=json.loads(_read_file(response_path))
                if not _verify_calls(server.configuration,expected['calls'],
                        arguments=request['arguments'],response=response):response=_pending_cache(response,expected['calls'])
            else:response=_unknown()
        else:
            _publish(claim/'submit.json',raw)
            c.write(claim/'binding.json',dict(step=server.client['step'],attempt=server.client['attempt'],
                configuration=server.configuration,retained_at_start=retained,submission=e.proof(claim/'submit.json')))
            before={path.name for path in e.Records(server.configuration).calls()}
            try:
                if request.get('client_error')=='submission_frame_too_large':raise _frame_refusal()
                if len(raw)>MAX_REQUEST_BYTES:raise _frame_refusal(len(raw))
                if (not retained and isinstance(request['arguments'],list)
                        and request['arguments'] and isinstance(request['arguments'][0],str)
                        and request['arguments'][0] in {'query','lookup','collect','cancel','reconcile','retire'}):
                    purpose=_request_consumption(request['arguments'])
                    if purpose.call_id not in before:
                        # Only reject this operation's current addressing scope;
                        # absence here says nothing about another original's state.
                        raise e.CallerError(f'{purpose.command} requires an original call record addressable by the current W task. '
                            'This refusal concerns this operation request only. '
                            'Read earlier retained files from the assigned inputs/history node index and its material IDs. '
                            'Preserve any existing unknown submission and reconcile it with its original owner. '
                            'Do not resubmit the earlier experiment.',error='call_outside_current_task')
                response=_unknown() if retained else _execute(server,request['arguments'])
            except e.CallerError as error:
                response=dict(code=2,result=e.caller_feedback(error))
            calls=[] if retained else _response_calls(server,request,response,before)
            _verify_calls(server.configuration,calls,arguments=request['arguments'],response=response)
            # A fresh e.main return owns its verified pending state and handle.
            # Only reuse of an older pending response is qualified as unknown.
            response=dict(response,submission_ref=reference,request=request)
            _publish(claim/'response.json',_encoded(response))
            c.write(claim/'outcome.json',dict(submission=e.proof(claim/'binding.json'),response=e.proof(claim/'response.json'),calls=calls))
        response=dict(response,submission_ref=reference,request=request)
    except e.CallerError as error:
        response=dict(code=2,result=e.caller_feedback(error),submission_ref=reference,request=request)
    except Exception as error:
        # Private diagnostics retain control paths; scientific files do not.
        try:c.write(server.diagnostic,dict(error=type(error).__name__,note=str(error)[:2048]))
        except (OSError,ValueError):pass
        response=dict(code=3,result=dict(status='integrity_error',
            note='Scientific submission could not be verified; preserve original calls'),submission_ref=reference,request=request)
    arguments=request['arguments']
    if (isinstance(arguments,list) and arguments and isinstance(arguments[0],str)
            and (arguments[0] in {'run','query','lookup','collect','cancel','reconcile'}
                or arguments[0] in {'compile','retire'} and response['result'].get('status')=='caller_error'
                and response['result'].get('accepted') is False)):
        # Recompute only the public projection, never cached claim originals.
        # A retained unknown remains unknown even if its old hint said to wait.
        from . import managed_wait
        control=_option_arguments(arguments)
        if '--' in control:control=control[:control.index('--')]
        call_id=None
        if '--call-id' in control:
            index=control.index('--call-id')
            if index+1<len(control):call_id=control[index+1]
        response['result']=dict(response['result'],handoff=managed_wait.handoff(
            server.configuration,response['result'],arguments[0],call_id))
    if len(_encoded(response))>MAX_BYTES:
        from . import managed_wait
        result=dict(status='integrity_error',note='Execution response exceeds its declared bound')
        result['handoff']=managed_wait.handoff(None,result,'run')
        response=dict(code=3,result=result,submission_ref=reference,request=request)
    _publish(server.directory/'response.json',_encoded(response))


def terminal_caller_error(root,job,record):
    """Read a final rejected work command from its host claim, never public files."""
    from . import contracts as c,execution,experiments as e,public_output
    root=Path(root);record=Path(record)
    config_path=record/'experiment-config.json'
    if not config_path.exists():return None
    config=c.read(c.regular(config_path))
    attempt=config.get('native_attempt')
    if (not isinstance(attempt,dict) or config.get('scope',{}).get('step')!=job['step']
            or execution.record(root,job['step'],attempt['number'])!=record):
        raise e.IntegrityError('Caller-error evidence differs from its original native attempt')
    claims=Path(config['record_dir']).parent/'worker-submissions'
    if not claims.exists():return None
    latest=[];latest_stamp=None;accepted_claims={}
    readonly={'capabilities','describe','lineage-status','query','lookup','help','--help','-h'}
    for claim in claims.iterdir():
        if not SUBMISSION_REF.fullmatch(claim.name) or claim.resolve()!=claim or not claim.is_dir():
            raise e.IntegrityError('Original scientific submission namespace changed')
        binding_path=claim/'binding.json'
        if not binding_path.exists():
            submit=claim/'submit.json'
            if submit.exists():
                pending=_submission(_read_file(submit)).get('arguments')
                if isinstance(pending,list) and pending and isinstance(pending[0],str) and pending[0] in readonly:continue
            return None
        binding=c.read(c.regular(binding_path))
        if binding.get('attempt')!=attempt['number']:continue
        if (set(binding)!={'step','attempt','configuration','retained_at_start','submission'}
                or binding['step']!=job['step'] or binding['configuration']!=config
                or any(e.proof(claim/'submit.json').get(key)!=value for key,value in binding['submission'].items())):
            raise e.IntegrityError('Caller-error submission binding changed')
        request=_submission(_read_file(claim/'submit.json'))
        arguments=request.get('arguments')
        command=arguments[0] if isinstance(arguments,list) and arguments and isinstance(arguments[0],str) else None
        effect=command not in readonly
        outcome_path=claim/'outcome.json';response_path=claim/'response.json'
        if not outcome_path.exists() or not response_path.exists():
            if effect:return None
            continue
        outcome=c.read(c.regular(outcome_path))
        response=json.loads(_read_file(response_path))
        if (set(outcome)!={'submission','response','calls'}
                or any(e.proof(binding_path).get(key)!=value for key,value in outcome['submission'].items())
                or any(e.proof(response_path).get(key)!=value for key,value in outcome['response'].items())
                or set(response)!={'code','result','submission_ref','request'}
                or response['submission_ref']!=claim.name or request['submission_ref']!=claim.name
                or response['request']!=request or not isinstance(outcome['calls'],list)):
            raise e.IntegrityError('Caller-error original response changed')
        _verify_calls(config,outcome['calls'],arguments=request['arguments'],response=response)
        terminal=True
        for call in outcome['calls']:
            current=_call_evidence(config,call['call_id'])
            if current['observation'] is None and current['retirement'] is None:terminal=False
        if not terminal:return None
        if not effect:continue
        if response['result'].get('status') in {'unknown','integrity_error'} and not outcome['calls']:return None
        stamp=outcome_path.stat().st_mtime_ns
        if command=='run':
            # These host originals establish that the accepted call already
            # existed by this response, independent of its file's later mtime.
            for call in outcome['calls']:
                reference=call['call_id']
                accepted_claims[reference]=min(stamp,accepted_claims.get(reference,stamp))
        if latest_stamp is None or stamp>latest_stamp:
            latest=[(request,response,outcome)];latest_stamp=stamp
        elif stamp==latest_stamp:latest.append((request,response,outcome))
    # An older tie cannot hide a later request encountered after it. Only an
    # actual latest tie leaves the final work request ambiguous.
    if len(latest)!=1:return None
    request,response,outcome=latest[0];result=response['result']
    arguments=request['arguments']
    command=arguments[0] if isinstance(arguments,list) and arguments and isinstance(arguments[0],str) else None
    correctable=command in {'run','compile'} or (command not in COMMANDS and command not in readonly)
    if (not isinstance(arguments,list) or not correctable or response['code']!=2
            or result.get('status')!='caller_error' or result.get('accepted') is not False
            or outcome['calls'] or not isinstance(result.get('note'),str)):
        return None
    # A direct host operation after this response may have no bridge claim.
    # A later acceptance still prevents treating the rejected request as work.
    records=e.Records(config)
    for call in records.calls():
        accepted=records.accepted(call.name)
        if accepted.get('native_attempt')!=attempt:continue
        accepted_stamp=accepted_claims.get(call.name)
        if ((accepted_stamp is None and (call/'accepted.json').stat().st_mtime_ns>=latest_stamp)
                or accepted_stamp is not None and accepted_stamp>=latest_stamp):return None
        current=_call_evidence(config,call.name)
        _verify_calls(config,[current])
        if current['observation'] is None and current['retirement'] is None:return None
    clean=public_output.cleaner(root,c.settings(root,job))
    note=clean(result['note'],2048).encode('utf-8')[:240].decode('utf-8',errors='ignore')
    return dict(diagnostic=note,arguments=request['arguments'])


def _watch(server, stop, initial):
    previous=None
    if initial is not None:
        try:_handle(server,initial,retained=True)
        except (OSError,ValueError):pass
        previous=initial
    while not stop.wait(.1):
        try:
            raw=_read_file(server.directory/'submit.json')
            if raw==previous:continue
            _handle(server,raw)
            previous=raw
        except (OSError,ValueError):
            # An unfinished manual edit is not a submission and has no effect.
            continue


class _BridgeStopped(BaseException):
    pass


def _descendants(owner):
    """Snapshot actual parentage, including children with their own session."""
    from . import runtime
    children=[];parents={owner['pid']}
    rows=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        try:rows.append(runtime.process_identity(int(path.name),owner['task_ref']))
        except (OSError,ValueError,IndexError):continue
    while True:
        found=[row for row in rows if row['ppid'] in parents and row['pid'] not in parents]
        if not found:return children
        children.extend(found);parents.update(row['pid'] for row in found)


def _cleanup_bridge_children(identity):
    from . import runtime
    deadline=time.monotonic()+3  # Existing owned-process cleanup grace.
    while True:
        children=_descendants(identity)
        if not children:return 'observed_group_cleaned'
        for child in reversed(children):
            if runtime.process_matches(child,identity['task_ref']):
                try:os.kill(child['pid'],signal.SIGKILL)
                except ProcessLookupError:pass
            try:os.waitpid(child['pid'],os.WNOHANG)
            except ChildProcessError:pass
        if time.monotonic()>=deadline:return 'cleanup_unknown'
        time.sleep(.02)


def _bridge_process(server,initial,parent_pid,ready):
    from . import contracts as c,instance_owner,runtime
    def interrupted(*_):raise _BridgeStopped()
    try:
        signal.signal(signal.SIGUSR1,lambda *_:server.stop.set())
        signal.signal(signal.SIGTERM,interrupted)
        os.setsid();runtime._subreaper()
        libc=ctypes.CDLL(None,use_errno=True)
        if libc.prctl(1,signal.SIGTERM,0,0,0)!=0:
            raise OSError(ctypes.get_errno(),'Cannot bind execution bridge to its runner')
        if os.getppid()!=parent_pid:raise _BridgeStopped()
        # Fork transfers only the immutable launch closure. Never retain the
        # parent's owner/controller/record locks or their logical held tokens.
        c._record_writers.set({})
        instance_owner._held.set({});instance_owner._gates.set({})
        for path in Path('/proc/self/fd').iterdir():
            descriptor=int(path.name)
            if descriptor>=3 and descriptor!=ready:
                try:os.close(descriptor)
                except OSError:pass
        identity=runtime.process_identity(os.getpid(),server.identity)
        c.write(server.record/'bridge-process.json',identity)
        (server.record/'bridge-outcome.json').unlink(missing_ok=True)
        (server.record/'bridge-operation.json').unlink(missing_ok=True)
        os.write(ready,b'1');os.close(ready);ready=None
        _watch(server,server.stop,initial)
    except _BridgeStopped:
        pass
    except BaseException as error:
        try:c.write(server.diagnostic,dict(error=type(error).__name__,note=str(error)))
        except (OSError,ValueError):pass
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        signal.signal(signal.SIGUSR1,signal.SIG_IGN)
        if ready is not None:
            try:os.close(ready)
            except OSError:pass
        try:
            identity=runtime.process_identity(os.getpid(),server.identity)
            cleanup=_cleanup_bridge_children(identity)
            c.write(server.record/'bridge-outcome.json',dict(process=identity,cleanup=cleanup))
        finally:os._exit(0)


def _bridge_deadline(root,configuration):
    """Freeze the original task's remaining wall permission, never extend it."""
    from . import budget,execution,native,contracts as c
    job=c.read(c.regular(root/'jobs'/f'{configuration['scope']['step']:03d}.json'))
    record=execution.record(root,job['step'],configuration['native_attempt']['number'])
    c.read(c.regular(record/'native-intent.json'))
    stamp=budget.clock_stamp();limits=budget.snapshot(root,sample_stamp=stamp)
    seconds=budget.execution_available(root,job,limits,native.usage(root))['seconds']
    budget.node_mode(root)  # Historical hard settings remain read-only.
    return None if seconds is None else time.clock_gettime(time.CLOCK_BOOTTIME)+max(0.,seconds)


@contextmanager
def serve(directory, config_path, work, *, launch, environment=None):
    """One isolated host bridge, stopped and reaped before the task can seal."""
    from . import contracts as c,experiment_client,experiments as e,execution,runtime
    directory=Path(directory).absolute();work=Path(work).absolute();config_path=Path(config_path).absolute()
    if (directory!=work/SUBMISSION_DIRECTORY or directory.resolve()!=directory
            or work.resolve()!=work or not callable(launch)):
        raise ValueError('Exact ordinary scientific submission directory required')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    client=c.read(c.regular(config_path));configuration=experiment_client.resolve(config_path,client)
    root=experiment_client.root_for_configuration(configuration)
    if c.workspace(root,client['step'])!=work:
        raise ValueError('Submission workspace differs from its accepted task')
    e.load(configuration,path=config_path)
    private=Path(configuration['record_dir']).parent
    claims=private/'worker-submissions';claims.mkdir(mode=0o700,parents=True,exist_ok=True)
    diagnostic=private/'bridge-diagnostics';diagnostic.mkdir(mode=0o700,parents=True,exist_ok=True)
    record=execution.record(root,client['step'],client['attempt'])
    identity=execution.identity(root,client['step'],client['attempt'])
    server=SimpleNamespace(directory=directory,work=work,config_path=config_path,client=client,
        configuration=configuration,launch=launch,environment=dict(environment or {}),
        claims=claims,diagnostic=diagnostic/'latest-error.json',record=record,identity=identity,stop=threading.Event())
    try:initial=_read_file(directory/'submit.json')
    except FileNotFoundError:initial=None
    read_ready,write_ready=os.pipe()
    parent_pid=os.getpid();pid=os.fork()
    if pid==0:
        os.close(read_ready)
        _bridge_process(server,initial,parent_pid,write_ready)
    os.close(write_ready)
    receipt=None
    try:
        import select
        if not select.select([read_ready],[],[],5)[0] or os.read(read_ready,1)!=b'1':
            raise ValueError('Original execution bridge did not start')
        receipt=c.read(c.regular(record/'bridge-process.json'))
        if receipt.get('pid')!=pid or not runtime.process_matches(receipt,identity):
            raise ValueError('Original execution bridge identity changed')
        yield directory
    finally:
        os.close(read_ready)
        if receipt is None:
            try:receipt=runtime.process_identity(pid,identity)
            except (OSError,ValueError,IndexError):pass
        if receipt is not None and runtime.process_matches(receipt,identity):os.kill(pid,signal.SIGUSR1)
        idle_deadline=time.clock_gettime(time.CLOCK_BOOTTIME)+5
        while receipt is not None and runtime.process_matches(receipt,identity):
            # Read the operation's original permission, not today's balance.
            # New effects publish this marker before checking the stop signal.
            path=record/'bridge-operation.json'
            deadline=idle_deadline
            if path.exists():
                from . import budget
                operation=c.read(c.regular(path))
                if operation.get('boot_id')==budget.clock_stamp()['boot_id']:
                    deadline=operation['deadline_boottime']
            if deadline is not None and time.clock_gettime(time.CLOCK_BOOTTIME)>=deadline:break
            time.sleep(.05)
        if receipt is not None and runtime.process_matches(receipt,identity):
            descendants=_descendants(receipt)
            os.kill(pid,signal.SIGTERM)
            cleanup_deadline=time.monotonic()+5
            while runtime.process_matches(receipt,identity) and time.monotonic()<cleanup_deadline:time.sleep(.02)
            for child in reversed(descendants):
                if runtime.process_matches(child,identity):
                    try:os.kill(child['pid'],signal.SIGKILL)
                    except ProcessLookupError:pass
            if runtime.process_matches(receipt,identity):os.kill(pid,signal.SIGKILL)
        os.waitpid(pid,0)
        # A killed bridge has stopped writing, but has no positive cleanup
        # receipt. Sealing must defer; accepted external calls stay unknown.
        stopped(record,identity)


def stopped(record, identity):
    from . import contracts as c,runtime
    process=record/'bridge-process.json'
    if not process.exists():return  # Attempts without a bridge retain their contract.
    receipt=c.read(c.regular(process))
    if receipt.get('task_ref')!=identity or runtime.process_matches(receipt,identity):
        raise BlockingIOError('Original execution bridge still owns this attempt')
    path=record/'bridge-outcome.json'
    if not path.exists():raise ValueError('Original execution bridge cleanup is unconfirmed')
    outcome=c.read(c.regular(path))
    if (outcome.get('cleanup')!='observed_group_cleaned' or any(outcome.get('process',{}).get(key)!=receipt.get(key)
            for key in ('pid','boot_id','start_ticks','pgid','sid','task_ref'))):
        raise ValueError('Original execution bridge cleanup is unconfirmed')


def _client(directory, arguments):
    """Write an ordinary scientific command and wait for its same-reference result."""
    directory=Path(directory).absolute()
    if directory.resolve()!=directory or not directory.is_dir():
        raise ValueError('Ordinary scientific submission directory unavailable')
    path=directory/'submit.json';reply=directory/'response.json'
    try:previous=_submission(_read_file(path))
    except FileNotFoundError:previous=None
    prior_response=None
    if reply.exists():prior_response=json.loads(_read_response(reply))
    if previous is not None and (not isinstance(prior_response,dict)
            or prior_response.get('submission_ref')!=previous['submission_ref']
            or prior_response.get('request')!=previous):
        if previous!=_client_request(previous['submission_ref'],arguments):
            raise ValueError('An original submission still awaits its result')
        request=previous
    else:
        number=1 if previous is None else int(previous['submission_ref'][1:])+1
        if number>999999:raise ValueError('Submission reference bound exhausted')
        request=_client_request(f'S{number:03d}',arguments)
        _submission(_encoded(request))
        _publish(path,_encoded(request))
    original=_read_file(path)
    if _submission(original)!=request:raise ValueError('Scientific submission slot was replaced')
    deadline=time.monotonic()+WAIT_SECONDS
    while time.monotonic()<deadline:
        try:
            response=json.loads(_read_response(reply))
            if not isinstance(response,dict):raise ValueError('Ordinary scientific response required')
            if response.get('submission_ref')!=request['submission_ref']:
                time.sleep(.05);continue
            if (set(response)!={'submission_ref','request','code','result'}
                    or response['request']!=request or _read_file(path)!=original
                    or type(response['code']) is not int or response['code'] not in {0,1,2,3}
                    or not isinstance(response['result'],dict)):
                raise ValueError('Scientific submission response is unavailable')
            return {key:response[key] for key in ('code','result')}
        except FileNotFoundError:time.sleep(.05)
    raise ValueError('Original scientific submission result is unknown; do not replay')


def client(directory, arguments):
    """Serialize thin helpers only; hand-written submissions remain ordinary files."""
    directory=Path(directory).absolute()
    if directory.resolve()!=directory or not directory.is_dir():
        raise ValueError('Ordinary scientific submission directory unavailable')
    path=directory/'.client.lock'
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):raise ValueError('Ordinary client lock file required')
        deadline=time.monotonic()+WAIT_SECONDS
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:raise ValueError('Original scientific submission still owns its slot')
                time.sleep(.05)
        return _client(directory,arguments)
    finally:os.close(fd)


def main(argv=None):
    arguments=list(sys.argv[1:] if argv is None else argv)
    from . import experiments as e
    try:
        if len(arguments)<2 or arguments[0]!='--submission-directory':
            raise e.CallerError('Use exp help from the assigned task; the tool selects its own submission directory.')
        scientific=arguments[2:]
        control=scientific[:scientific.index('--')] if '--' in scientific else scientific
        if not scientific or scientific[0] in {'help','--help','-h'} or '--help' in control or '-h' in control:
            command=(scientific[1] if scientific and scientific[0]=='help' and len(scientific)==2 else
                scientific[0] if scientific and scientific[0] in COMMANDS else None)
            response=dict(code=0,result=e.cli_help(command))
        else:
            response=client(arguments[1],scientific)
    except e.CallerError as error:response=dict(code=2,result=e.caller_feedback(error))
    except (OSError,ValueError):
        response=dict(code=3,result=dict(status='unknown',note='Scientific submission result is unavailable; preserve original calls'))
    if (response['result'].get('status') in {'unknown','integrity_error','caller_error'}
            and not isinstance(response['result'].get('handoff'),dict)):
        from . import managed_wait
        response['result']['handoff']=managed_wait.handoff(None,response['result'],'run')
    print(json.dumps(response['result'],ensure_ascii=False,allow_nan=False))
    return response['code']


def _request_consumption(arguments):
    """Interpret the current request without resolving any scientific paths."""
    from . import experiments as e
    if (not isinstance(arguments,list) or not arguments
            or any(not isinstance(arg,str) or '\x00' in arg for arg in arguments)
            or arguments[0] not in COMMANDS):
        raise e.CallerError('Choose an existing scientific command with exp help.')
    parser,_=e._cli_parser(configuration=False)
    try:return parser.parse_args(arguments)
    except SystemExit:raise e.CallerError('Check required options with exp help <command>.') from None


if __name__=='__main__':raise SystemExit(main())
