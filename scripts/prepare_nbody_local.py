"""N-body-only local preparation and no-model smoke; never a Goal/research runner."""
from __future__ import annotations

import argparse
from itertools import islice
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import time

REVISION = 'd78f5eb52ad754c5ee9154741af73130a85a65b8'
PUBLIC = ('task.toml', 'instruction.md', 'environment/Dockerfile',
    'environment/workspace/simulator.py', 'environment/harness/baseline.py',
    'environment/harness/bench_cli.py', 'environment/harness/generate_particles.py',
    'environment/harness/world.py')
MAX_SUBMISSION = 2_000_000


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def regular_bytes(path, maximum=MAX_SUBMISSION):
    """Only a bounded regular file, without following its final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum:
            raise ValueError('bounded_regular_file_required')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(maximum+1)
        if len(raw) != metadata.st_size:
            raise ValueError('file_changed_during_read')
        return raw
    finally:
        os.close(fd)


def regular_size(path, maximum=MAX_SUBMISSION):
    """Inspect a bounded regular original without reading its contents."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= maximum:
            raise ValueError('bounded_regular_file_required')
        return metadata.st_size
    finally:
        os.close(fd)


def prepare(output, freeze):
    frozen = json.loads(Path(freeze).read_text())
    task = frozen['task']
    if (task['commit'] != REVISION or task['id'] != 'nbody-accel-iterative'
            or not isinstance(task['public_files'],list) or len(task['public_files']) != len(PUBLIC)
            or set(task['public_files']) != set(PUBLIC)):
        raise ValueError('exact_frozen_nbody_public_inputs_required')
    public = Path(task['public_path']).resolve()
    blobs = {}
    for name in PUBLIC:
        path = public/name
        if path.resolve() != path.absolute():
            raise ValueError('public_input_symlink_rejected')
        raw = regular_bytes(path)
        blobs[name] = raw
    root = Path(output).absolute()
    root.mkdir(exist_ok=False)
    for name, raw in blobs.items():
        target = root/'public'/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for directory in ('workspace', 'output', 'submission', 'host-private'):
        (root/directory).mkdir()
    (root/'workspace/simulator.py').write_bytes(blobs['environment/workspace/simulator.py'])
    (root/'host-private/canary.txt').write_bytes(b'local-smoke-private-canary\n')
    manifest = dict(kind='nbody_local_engineering_preparation', task_revision=REVISION,
        public_files=list(PUBLIC), public_file_sizes={name:len(raw) for name,raw in blobs.items()}, source_candidate=frozen['candidate']['source_commit'],
        installed_candidate=frozen['candidate']['path'], launch_ready=False,
        no_model=True, scoring='not_run', profile_proposal='not_activated')
    write(root/'prepared.json', manifest)
    return manifest


def namespace_command(root, python, command):
    """Task commands only: no host home, control state, credentials or network."""
    python = Path(python).resolve()
    prefix = python.parent.parent
    if not (prefix/'lib').is_dir():
        raise ValueError('python_runtime_lib_missing')
    result = [shutil.which('bwrap') or 'bwrap', '--unshare-user', '--unshare-pid',
        '--unshare-net', '--unshare-ipc', '--unshare-uts', '--die-with-parent', '--new-session',
        '--clearenv', '--ro-bind', '/usr', '/usr', '--ro-bind', '/lib', '/lib',
        '--ro-bind', '/lib64', '/lib64', '--ro-bind', str(python), str(python),
        '--ro-bind', str(prefix/'lib'), str(prefix/'lib'), '--proc', '/proc', '--dev', '/dev',
        '--tmpfs', '/tmp', '--ro-bind', str(root/'public'), '/task',
        '--ro-bind', str(root/'public/environment/harness'), '/app/harness',
        '--bind', str(root/'workspace'), '/app/workspace', '--bind', str(root/'output'), '/app/output',
        '--ro-bind', str(Path(__file__).resolve()), '/runtime/local_preflight.py', '--chdir', '/app/workspace']
    for key, value in {'PATH':'/usr/bin', 'HOME':'/tmp', 'PYTHONDONTWRITEBYTECODE':'1',
            'OMP_NUM_THREADS':'1', 'OPENBLAS_NUM_THREADS':'1', 'MKL_NUM_THREADS':'1'}.items():
        result.extend(['--setenv', key, value])
    return result+command


def verify_public(root, manifest):
    if (not isinstance(manifest['public_files'],list) or len(manifest['public_files']) != len(PUBLIC)
            or set(manifest['public_files']) != set(PUBLIC) or manifest['task_revision'] != REVISION):
        raise ValueError('exact_public_manifest_required')
    public = root/'public'
    directories = {}
    for name in PUBLIC:
        path = Path(name)
        for parent in (path, *path.parents[:-1]):
            directories.setdefault(parent.parent, set()).add(parent.name)
    for directory, names in directories.items():
        path = public/directory
        if path.is_symlink() or not path.is_dir():
            raise ValueError('prepared_public_directory_changed')
        with os.scandir(path) as entries:
            if {item.name for item in islice(entries,len(names)+1)} != names:
                raise ValueError('prepared_public_whitelist_changed')
    for name in PUBLIC:
        if regular_size(public/name) != manifest['public_file_sizes'][name]:
            raise ValueError('prepared_public_input_changed:'+name)


def compute_smoke(private, host_netns):
    import numpy
    sys.path[:0] = ['/app/workspace', '/app/harness']
    from simulator import Simulator
    from world import Particle, Vec2, StepParameters
    inputs = dict(particles=[dict(id=i, mass=1.+(i%3)/10., position=[2.*(i%6),2.*(i//6)],
        velocity=[.01*(i%4),-.01*(i%5)]) for i in range(24)], parameters=dict(deltaTime=.01,cullRadius=25.))
    particles = [Particle(p['id'],p['mass'],Vec2(*p['position']),Vec2(*p['velocity'])) for p in inputs['particles']]
    engine = Simulator(particles, StepParameters(**inputs['parameters']))
    for _ in range(2):
        output = engine.simulate_step()
    values = [dict(id=p.id,mass=p.mass,position=[p.position.x,p.position.y],velocity=[p.velocity.x,p.velocity.y]) for p in output]
    assert len(values)==24 and {p['id'] for p in values}==set(range(24))
    assert all(math.isfinite(v) for p in values for v in [p['mass'],*p['position'],*p['velocity']])
    write('/app/output/input.json', inputs)
    write('/app/output/output.json', values)
    assert not Path(private).exists() and 'NBODY_SYNTHETIC_SECRET' not in os.environ
    child = subprocess.run([sys.executable,'-I','-c',
        'from pathlib import Path; import os,sys; assert not Path(sys.argv[1]).exists(); '
        'assert "NBODY_SYNTHETIC_SECRET" not in os.environ; '
        'assert os.readlink("/proc/self/ns/net") != sys.argv[2]; print("native_child_boundary_passed")',
        private,host_netns],capture_output=True,text=True,check=True)
    assert os.readlink('/proc/self/ns/net') != host_netns
    fd_targets = []
    for fd in Path('/proc/self/fd').iterdir():
        try: fd_targets.append(os.readlink(fd))
        except FileNotFoundError: pass
    assert not any('scid-tests-' in value or private in value for value in fd_targets)
    result = dict(kind='custom_engineering_smoke_not_score', particles=24,steps=2,
        python=sys.version.split()[0],numpy=numpy.__version__,finite_values=True,stable_ids=True,
        cwd=str(Path.cwd()),native_child_boundary=child.stdout.strip(),task_network_namespace='separate',
        host_private_not_mounted=True,host_environment_cleared=True,supervisor_lock_not_exposed=True,
        source_ref=dict(path='/app/workspace/simulator.py',size_bytes=regular_size('/app/workspace/simulator.py')),
        input_ref=dict(path='/app/output/input.json',size_bytes=regular_size('/app/output/input.json')),
        output_ref=dict(path='/app/output/output.json',size_bytes=regular_size('/app/output/output.json')))
    write('/app/output/cpu-smoke.json', result)
    print(json.dumps(result))


def synthetic_stop_smoke():
    """Only this synthetic process group; no claim about detached native writers."""
    pulse = 'from pathlib import Path; import time\nwhile True:\n Path("/app/output/pulse").write_text(str(time.monotonic()))\n time.sleep(.01)'
    child = subprocess.Popen([sys.executable,'-I','-c',pulse],start_new_session=True)
    started = time.monotonic()
    try:
        while not Path('/app/output/pulse').exists():
            if time.monotonic()-started > 2: raise RuntimeError('synthetic_child_start_timeout')
            time.sleep(.01)
        # A short fixture deadline, independent of the unchanged 300s supervisor.
        time.sleep(.15)
        os.killpg(child.pid,signal.SIGSTOP)
        _, status = os.waitpid(child.pid,os.WUNTRACED)
        assert os.WIFSTOPPED(status)
        before = Path('/app/output/pulse').read_bytes()
        time.sleep(.05)
        assert Path('/app/output/pulse').read_bytes()==before
    finally:
        os.killpg(child.pid,signal.SIGKILL)
        child.wait(timeout=2)
    result = dict(kind='synthetic_stop_not_task_result', deadline_seconds=.15,
        frozen_before_cleanup=True,pulse_stable_while_frozen=True,returncode=child.returncode,
        direct_child_reaped=True,scope='owned_fixture_process_group; detached_native_coverage_not_proven')
    write('/app/output/stop-smoke.json',result)
    print(json.dumps(result))


def handoff(root):
    raw = regular_bytes(root/'workspace/simulator.py')
    target = root/'submission/simulator.py'
    with target.open('xb') as stream:
        stream.write(raw)
    result = dict(file='simulator.py',path=str(target.absolute()),size_bytes=len(raw),
        scoring='not_run',source='quiescent_engineering_smoke_workspace')
    write(root/'handoff.json',result)
    return result


def smoke(root, python):
    try:
        from scripts import run_tests as guard
    except ModuleNotFoundError:
        import run_tests as guard
    root = Path(root).resolve()
    manifest = json.loads((root/'prepared.json').read_text())
    verify_public(root,manifest)
    limits = json.loads(guard.DEFAULT_CONFIG.read_text())['limits']
    env = dict(os.environ,NBODY_SYNTHETIC_SECRET='not-a-credential')
    env.pop('PYTHONPATH',None)
    runtime = str(Path(python).resolve())
    results = {}
    with guard.serial_lock() as lock:
        for name, arguments in (('cpu', ['_cpu',str(root/'host-private/canary.txt'),os.readlink('/proc/self/ns/net')]),
                ('stop',['_stop'])):
            output = root/name
            output.mkdir()
            # run_one keeps the supervisor's lock; never expose its inherited copy
            # to the public Simulator or native descendants inside the namespace.
            payload = [runtime,'-I','/runtime/local_preflight.py',*arguments]
            entry = [runtime,'-I','-c',
                'import os,sys; os.close(int(sys.argv[1])); os.execv(sys.executable,sys.argv[2:])',
                str(lock.fileno()),*payload]
            command = namespace_command(root,runtime,entry)
            write(output/'command.json',dict(command=command,limits=limits,no_model=True))
            result = guard.run_one(command,env,limits,output,lock.fileno())
            write(output/'resource.json',result)
            print(name,json.dumps(result),flush=True)
            results[name]=result
            if result['returncode']!=0 or result['stop_reason']!='exited':
                raise RuntimeError('local_smoke_failed:'+name)
    results['handoff']=handoff(root)
    write(root/'result.json',dict(local_engineering_smoke='passed',launch_ready=False,
        scientific_score='not_run',native_goal='not_run',scidiscovery_live='not_run',results=results))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='action',required=True)
    prep=sub.add_parser('prepare');prep.add_argument('--output',type=Path,required=True);prep.add_argument('--freeze',type=Path,required=True)
    check=sub.add_parser('smoke');check.add_argument('--prepared',type=Path,required=True);check.add_argument('--python',required=True)
    cpu=sub.add_parser('_cpu');cpu.add_argument('private');cpu.add_argument('host_netns')
    sub.add_parser('_stop')
    args=parser.parse_args()
    if args.action=='prepare':print(json.dumps(prepare(args.output,args.freeze),ensure_ascii=False))
    elif args.action=='smoke':smoke(args.prepared,args.python)
    elif args.action=='_cpu':compute_smoke(args.private,args.host_netns)
    else:synthetic_stop_smoke()


if __name__=='__main__':main()
