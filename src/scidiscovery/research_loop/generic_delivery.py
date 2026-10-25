"""Isolated delivery of exact managed results to an explicitly configured directory.

This adapter confirms local directory delivery only. It makes no external service,
scientific audit, smoke, or goal completion claim.
"""
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import tempfile


def descriptor(directory, *, python=sys.executable):
    directory = Path(directory).absolute()
    if directory.resolve() != directory or not directory.is_dir():
        raise ValueError('Delivery requires an explicit original target directory')
    python = str(Path(python).absolute())
    if python == str(Path(sys.executable).absolute()):
        roots = [sys.base_prefix, sys.prefix]
    else:
        # A copied venv executable does not identify its base standard library.
        # Ask this exact interpreter during preparation, before freezing config.
        result = subprocess.run([python, '-I', '-c',
            'import json,sys;print(json.dumps([sys.base_prefix,sys.prefix]))'],
            check=True, capture_output=True, text=True, timeout=5)
        roots = json.loads(result.stdout)
    if (not isinstance(roots, list) or not roots or any(not isinstance(path, str)
            or not Path(path).is_absolute() or not Path(path).is_dir()
            or Path(path).resolve() in {Path('/'), Path.home().resolve()}
            or directory.is_relative_to(Path(path).resolve()) for path in roots)):
        raise ValueError('Delivery requires explicit Python runtime roots')
    module = str(Path(__file__).resolve())
    return dict(adapter=module, launcher_argv=[python, module, '--isolate'],
        config=dict(python=python, runtime_roots=list(dict.fromkeys(roots))),
        target=dict(directory=str(directory)), capabilities=[], readonly_inputs_version=1,
        effect_scope='Copy and confirm already retained declared files in a local directory only. '
                     'Does not train, run task-native submission or perform a scientific audit.',
        inputs=[dict(label='runtime-python', path=python)], source_roots=[str(directory)])


def _file_size(path):
    path = Path(path)
    if path.resolve() != path or path.is_symlink() or not path.is_file():
        raise ValueError('Exact bounded delivery file required')
    return path.stat().st_size


def _guard(request):
    source = Path('/inputs/scid-current-protocol')
    proof = next(item for item in request['inputs'] if item['label'] == 'scid-current-protocol')
    if _file_size(source) != proof['size_bytes']:
        raise ValueError('Frozen delivery protocol changed')
    runpy.run_path(str(source))['require_scope'](request['scope'])


def _manifest(request):
    if (not re.fullmatch('[0-9a-f]{64}', request['operation_id'])
            or request['context'].get('kind') != 'managed_local_result'):
        raise ValueError('Exact managed local delivery required')
    objects = {name: digest for name, digest in request['object_refs'].items()
        if name == 'artifact' or name.startswith(('inputs/', 'outputs/'))}
    if objects.get('artifact') != request['artifact_ref']:raise ValueError('Declared artifact identity changed')
    for name, digest in objects.items():
        if (Path(name).is_absolute() or '..' in Path(name).parts or '\\' in name
                or Path(name).as_posix() != name or _file_size(Path('/inputs') / name) != digest['size_bytes']):
            raise ValueError('Managed delivery object changed')
    return dict(version=1, operation_id=request['operation_id'], candidate_id=request['candidate_id'],
        artifact_ref=request['artifact_ref'], objects=objects, result=request['context'],
        qualification='Exact local directory delivery only; no scientific or external submission qualification.')


def _write(path, data):
    with path.open('xb') as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())


def leaf(request):
    _guard(request)
    manifest = _manifest(request)
    destination = Path('/target') / request['operation_id']
    reply = dict(operation_id=request['operation_id'], candidate_id=request['candidate_id'],
        artifact_ref=request['artifact_ref'])
    if request['phase'] == 'promote':
        if destination.exists() or destination.is_symlink():
            raise ValueError('Existing delivery requires confirmation, never another promotion')
        pending = Path('/target') / ('.pending-' + request['operation_id'])
        pending.mkdir()  # An uncertain previous attempt must not be overwritten.
        for name in manifest['objects']:
            path = pending / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with (Path('/inputs')/name).open('rb') as source,path.open('xb') as sink:
                shutil.copyfileobj(source,sink,1024*1024);sink.flush();os.fsync(sink.fileno())
            if _file_size(path) != manifest['objects'][name]['size_bytes']:raise ValueError('Delivered object changed')
        with (pending/'manifest.json').open('x',encoding='utf-8') as stream:
            json.dump(manifest,stream,sort_keys=True,allow_nan=False);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.rename(pending, destination)
        descriptor = os.open('/target', os.O_RDONLY | os.O_DIRECTORY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)
    elif request['phase'] != 'confirm':
        raise ValueError('Directory adapter provides promotion and confirmation only')
    try:
        original = destination / 'manifest.json'
        if original.resolve()!=original:raise ValueError('Exact bounded delivery manifest required')
        actual = json.loads(original.read_text())
        if actual == manifest and all(_file_size(destination / name) == digest['size_bytes'] for name, digest in manifest['objects'].items()):
            return dict(reply, status='confirmed', note='exact_local_directory_delivery_confirmed')
    except (OSError, ValueError):
        pass
    return dict(reply, status='unknown', note='exact_local_directory_delivery_not_confirmed')


def isolate(request, adapter):
    stage = Path(request['stage'])
    if stage.resolve() != stage or not stage.is_dir() or request['phase'] not in {'promote', 'confirm'}:
        raise ValueError('Exact isolated directory delivery stage required')
    if set(request['target']) != {'directory'} or set(request['config']) != {'python', 'runtime_roots'}:
        raise ValueError('Explicit directory target and runtime required')
    target = Path(request['target']['directory'])
    if target.resolve() != target or not target.is_dir():raise ValueError('Delivery target changed')
    python = Path(request['config']['python'])
    proof = next(item for item in request['inputs'] if item['label'] == 'runtime-python')
    if str(python.absolute()) != proof['invoked_path'] or str(python.resolve()) != proof['path']:
        raise ValueError('Frozen delivery runtime changed')
    bwrap = shutil.which('bwrap')
    if bwrap is None:raise ValueError('Directory delivery requires the qualified namespace launcher')
    args = [bwrap, '--unshare-user', '--unshare-pid', '--unshare-net', '--unshare-ipc', '--unshare-uts',
        '--die-with-parent', '--new-session', '--clearenv', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    roots = request['config']['runtime_roots']
    if not isinstance(roots, list) or not roots:
        raise ValueError('Delivery requires explicit Python runtime roots')
    for path in roots:
        if (not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_dir()
                or Path(path).resolve() in {Path('/'), Path.home().resolve()}
                or stage.is_relative_to(Path(path).resolve()) or target.is_relative_to(Path(path).resolve())):
            raise ValueError('Python runtime overlaps private delivery paths')
    for path in dict.fromkeys(['/usr', '/bin', '/lib', '/lib64', *roots]):
        if Path(path).exists():args += ['--ro-bind', path, path]
    for path in dict.fromkeys([str(python), str(python.resolve())]):
        if not any(Path(path).is_relative_to(root) for root in ['/usr', '/bin', '/lib', '/lib64', *roots]):
            args += ['--ro-bind', path, path]
    args += ['--ro-bind',str(stage/'inputs'),'/inputs','--ro-bind',adapter,'/delivery.py',
        '--bind' if request['phase']=='promote' else '--ro-bind',str(target),'/target']
    objects=request.get('object_refs')
    if not isinstance(objects,dict):raise ValueError('Selected delivery originals are unavailable')
    labels=[]
    for label,reference in objects.items():
        name=Path(label) if isinstance(label,str) else Path('/')
        if (not name.parts or name.is_absolute() or '..' in name.parts or '\\' in label
                or name.as_posix()!=label or any(name.is_relative_to(old) or old.is_relative_to(name) for old in labels)
                or not isinstance(reference,dict) or set(reference)!={'path','size_bytes'}
                or type(reference['size_bytes']) is not int or reference['size_bytes']<0):
            raise ValueError('Selected delivery original binding changed')
        original=Path(reference['path'])
        mountpoint=stage/'inputs'/name
        if (not original.is_absolute() or _file_size(original)!=reference['size_bytes']
                or _file_size(mountpoint)!=0):
            raise ValueError('Selected delivery original or mountpoint changed')
        labels.append(name)
        args += ['--ro-bind',str(original),str(Path('/inputs')/name)]
    args += ['--setenv','PATH',os.defpath,'--chdir','/tmp','--',str(python),'-I','/delivery.py','--leaf']
    os.execv(bwrap, args)


if __name__ == '__main__':
    request=json.load(sys.stdin)
    if sys.argv[1] == '--isolate':
        with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as stream:
            json.dump(request,stream,allow_nan=False);stream.flush();stream.seek(0);os.dup2(stream.fileno(),0)
            isolate(request, str(Path(sys.argv[2]).resolve()))
    elif sys.argv[1:] == ['--leaf']:
        print(json.dumps(leaf(request), allow_nan=False))
    else:
        raise ValueError('Directory delivery requires its isolated entry')
