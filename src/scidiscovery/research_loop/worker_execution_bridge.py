"""A lifecycle-bound host execution bridge for an exact scientific task.

Ordinary submission files request existing experiment CLI operations. Host originals and
dispatch configuration never enter the scientific namespace. The launcher's
trusted process wrapper preserves the declared scientific execution namespace.
"""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from contextvars import ContextVar
import fcntl
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
    'query','lookup','collect','cancel','reconcile','retire'})
PATH_OPTIONS = frozenset({'--script','--payload','--experiment-dir','--implementation',
    '--scientific-files','--output'})
VALUE_OPTIONS = PATH_OPTIONS | {'--start','--key','--parent-plan','--change-intent','--mechanism','--call-id'}
FLAG_OPTIONS = frozenset({'--metadata','--new-plan'})
_LOCAL_LAUNCH = ContextVar('scientific_local_launch', default=None)


class _FileReplaced(ValueError):
    """An ordinary regular file was atomically replaced during this read."""


def local_process_launch(config, accepted, command, cwd):
    launch = _LOCAL_LAUNCH.get()
    return launch(config, accepted, command, cwd) if launch is not None else (command, cwd, None)


def _arguments(arguments, work):
    from .experiments import CallerError
    if (not isinstance(arguments,list) or not 1<=len(arguments)<=128
            or any(not isinstance(arg,str) or '\x00' in arg or len(arg.encode())>4096 for arg in arguments)
            or arguments[0] not in COMMANDS):
        raise CallerError('Choose an existing scientific command with exp help.')
    result=[arguments[0]];index=1
    while index<len(arguments):
        option=arguments[index]
        if option=='--' and arguments[0]=='run':
            result.extend(arguments[index:]);break
        if option in FLAG_OPTIONS:
            result.append(option);index+=1;continue
        if option not in VALUE_OPTIONS or index+1>=len(arguments):
            raise CallerError('Check required options with exp help <command>; separate scientific script arguments with --.')
        value=arguments[index+1]
        if option in PATH_OPTIONS:
            path=Path(value)
            if not path.is_absolute():path=work/path
            if (path.resolve()!=path or not path.is_relative_to(work)
                    or path==work):
                raise CallerError('Use an ordinary scientific file inside the assigned task workspace; do not use links or parent directories.')
            if option!='--output' and not path.exists():
                raise CallerError('The selected scientific input is missing. Write or select its task file, then check exp help <command>.',error='scientific_input_not_found')
            value=str(path)
        result.extend([option,value]);index+=2
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
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)


def _encoded(value):
    return (json.dumps(value,ensure_ascii=False,allow_nan=False)+'\n').encode()


def _submission(raw):
    if len(raw)>MAX_REQUEST_BYTES:raise ValueError('Scientific submission exceeds its declared bound')
    try:value=json.loads(raw)
    except RecursionError:raise ValueError('Ordinary scientific submission required') from None
    if (not isinstance(value,dict) or set(value)!={'submission_ref','arguments'}
            or not isinstance(value['submission_ref'],str)
            or not SUBMISSION_REF.fullmatch(value['submission_ref'])):
        raise ValueError('Ordinary submission reference and existing command required')
    return value


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
    arguments=_arguments(arguments,server.work)
    from . import experiments as e
    config=_configuration(server)
    if server.stop.is_set():return _unknown()
    out=io.StringIO();err=io.StringIO()
    def trusted_launch(body,accepted,command,cwd):
        expected={key:value for key,value in config.items() if key!='execution_interface'}
        if (body!=expected or accepted.get('scope')!=config['scope']
                or accepted.get('native_attempt')!=config.get('native_attempt')
                or accepted.get('interface')!=config['execution_interface']
                or accepted.get('config_sha256')!=config['execution_interface']['config_sha256']
                or e.Records(config).accepted(accepted['call_id'])!=accepted):
            raise e.IntegrityError('Scientific process differs from its accepted frozen configuration')
        _configuration(server)
        if server.stop.is_set():raise e.IntegrityError('Original task submission lifetime ended')
        return server.launch(config,accepted,command,cwd)
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


def _verify_calls(config, calls):
    """Pure original-byte verification; no factory, backend query or replay."""
    from . import experiments as e
    complete=True
    for call in calls:
        directory=e.Records(config).directory(call['call_id'])
        current=_call_evidence(config,call['call_id'])
        if set(call)!=set(current) or any(value is not None and current[key]!=value for key,value in call.items()):
            raise e.IntegrityError('Cached submission original call changed')
        accepted=e.Records(config).accepted(call['call_id'])
        if (accepted['script']['path']!=str(directory/'source.py')
                or e.proof(directory/'source.py')!=accepted['script']):
            raise e.IntegrityError('Cached submission source snapshot changed')
        for item in accepted.get('snapshot_files',[]):
            relative=Path(item['name'])
            if relative.is_absolute() or '..' in relative.parts or relative.as_posix()!=item['name']:
                raise e.IntegrityError('Cached submission snapshot namespace changed')
            actual=e.proof(directory/'inputs'/relative)
            if any(actual[key]!=item[key] for key in ('sha256','size_bytes')):
                raise e.IntegrityError('Cached submission input snapshot changed')
        if current['completed'] is not None:
            completed=json.loads(e.read(directory/'completed.json'))
            for item in completed.get('proofs',[]):
                if e.proof(item['path'])!=item:
                    raise e.IntegrityError('Cached submission terminal original changed')
            for item in completed.get('outputs',[]):
                if e.proof(item['path'])!={key:item[key] for key in ('path','sha256','size_bytes')}:
                    raise e.IntegrityError('Cached submission output original changed')
        if current['observation'] is not None:
            observed=json.loads(e.read(directory/'execution-observation.json'))
            e.verify_observation(observed,lambda item:Path(item['path']),interface=accepted['interface'])
        elif current['retirement'] is not None:
            value=json.loads(e.read(directory/'submission-retirement.json'))
            e.verify_retirement(value,lambda item:Path(item['path']),interface=accepted['interface'])
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
    references={path.name for path in e.Records(server.configuration).calls()}-before
    result=response.get('result',{})
    if isinstance(result.get('call_id'),str):references.add(result['call_id'])
    for row in result.get('calls',[]):
        if isinstance(row,dict) and isinstance(row.get('call_id'),str):references.add(row['call_id'])
    arguments=request['arguments']
    if '--' in arguments:arguments=arguments[:arguments.index('--')]
    if '--call-id' in arguments:
        index=arguments.index('--call-id')
        if index+1<len(arguments):references.add(arguments[index+1])
    return [_call_evidence(server.configuration,reference) for reference in sorted(references)]


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
                    or binding['submission']!=e.proof(claim/'submit.json')):
                raise ValueError('Submission original binding differs')
            response_path=claim/'response.json';outcome=claim/'outcome.json'
            if response_path.exists() and outcome.exists():
                expected=c.read(c.regular(outcome))
                if (not isinstance(expected,dict) or set(expected)!={'submission','response','calls'}
                        or expected['submission']!=e.proof(claim/'binding.json')
                        or expected['response']!=e.proof(response_path)
                        or not isinstance(expected['calls'],list)):
                    raise ValueError('Submission original result differs')
                response=json.loads(_read_file(response_path))
                if not _verify_calls(server.configuration,expected['calls']):response=_pending_cache(response,expected['calls'])
            else:response=_unknown()
        else:
            _publish(claim/'submit.json',raw)
            c.write(claim/'binding.json',dict(step=server.client['step'],attempt=server.client['attempt'],
                configuration=server.configuration,retained_at_start=retained,submission=e.proof(claim/'submit.json')))
            before={path.name for path in e.Records(server.configuration).calls()}
            response=_unknown() if retained else _execute(server,request['arguments'])
            calls=[] if retained else _response_calls(server,request,response,before)
            _verify_calls(server.configuration,calls)
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
    if isinstance(arguments,list) and arguments and isinstance(arguments[0],str) and arguments[0] in {'run','query','lookup','collect','cancel','reconcile'}:
        # Recompute only the public projection, never cached claim originals.
        # A retained unknown remains unknown even if its old hint said to wait.
        from . import managed_wait
        control=arguments[:arguments.index('--')] if '--' in arguments else arguments
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


@contextmanager
def serve(directory, config_path, work, *, launch, environment=None):
    """Observe one ordinary file slot only for the exact launched task lifetime."""
    from . import contracts as c,experiment_client,experiments as e
    directory=Path(directory).absolute();work=Path(work).absolute();config_path=Path(config_path).absolute()
    if (directory!=work/SUBMISSION_DIRECTORY or directory.resolve()!=directory
            or work.resolve()!=work or not callable(launch)):
        raise ValueError('Exact ordinary scientific submission directory required')
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    client=c.read(c.regular(config_path));configuration=experiment_client.resolve(config_path,client)
    if c.workspace(experiment_client.root_for_configuration(configuration),client['step'])!=work:
        raise ValueError('Submission workspace differs from its accepted task')
    e.load(configuration,path=config_path)
    private=Path(configuration['record_dir']).parent
    claims=private/'worker-submissions';claims.mkdir(mode=0o700,parents=True,exist_ok=True)
    diagnostic=private/'bridge-diagnostics';diagnostic.mkdir(mode=0o700,parents=True,exist_ok=True)
    server=SimpleNamespace(directory=directory,work=work,config_path=config_path,client=client,
        configuration=configuration,launch=launch,environment=dict(environment or {}),
        claims=claims,diagnostic=diagnostic/'latest-error.json')
    try:initial=_read_file(directory/'submit.json')
    except FileNotFoundError:initial=None
    stop=threading.Event();server.stop=stop
    thread=threading.Thread(target=_watch,args=(server,stop,initial),daemon=True)
    thread.start()
    try:yield directory
    finally:
        # Special-file or idle readers cannot hold shutdown open. An active
        # effect retains its original deadline/cleanup and is never reclassified
        # as not-started or replayed because the submission observer stopped.
        stop.set();thread.join(timeout=2)


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
        if previous['arguments']!=arguments:
            raise ValueError('An original submission still awaits its result')
        request=previous
    else:
        number=1 if previous is None else int(previous['submission_ref'][1:])+1
        if number>999999:raise ValueError('Submission reference bound exhausted')
        request=dict(submission_ref=f'S{number:03d}',arguments=arguments)
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
            _arguments(scientific,Path(arguments[1]).absolute().parents[1])
            response=client(arguments[1],scientific)
    except e.CallerError as error:response=dict(code=2,result=e.caller_feedback(error))
    except (OSError,ValueError):
        response=dict(code=3,result=dict(status='unknown',note='Scientific submission result is unavailable; preserve original calls'))
    if response['result'].get('status') in {'unknown','integrity_error','caller_error'}:
        from . import managed_wait
        response['result']['handoff']=managed_wait.handoff(None,response['result'],'run')
    print(json.dumps(response['result'],ensure_ascii=False,allow_nan=False))
    return response['code']


if __name__=='__main__':raise SystemExit(main())
