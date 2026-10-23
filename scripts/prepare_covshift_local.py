"""Covshift public environment and one no-model engineering run, never a trial."""
from __future__ import annotations

import argparse
from itertools import islice
import json
import os
from pathlib import Path
import subprocess
import sys

REVISION = 'd78f5eb52ad754c5ee9154741af73130a85a65b8'
PUBLIC = ('task.toml', 'instruction.md', 'environment/Dockerfile',
    'environment/harness/exp_cli.py', 'environment/harness/runner.py',
    'environment/harness/train.csv', 'environment/harness/val.csv',
    'environment/harness/meta.json')
PRIVATE_META_KEYS = {'a_perturbation', 'estimated_A_score', 'noise_sigma'}
INPUT_BOUND = 4 * 1024 * 1024
# An engineering read/handoff bound only, not the original task's submission limit.
SMOKE_ARTIFACT_BOUND = 32 * 1024 * 1024
TRAIN = '''import numpy as np
from sklearn.dummy import DummyRegressor
def train(train_df, val_df):
    targets = [c for c in train_df.columns if c.startswith("x")]
    assert len(targets) == 200
    return DummyRegressor(strategy="constant", constant=np.zeros(200)).fit(
        train_df.drop(columns=targets), train_df[targets])
'''


def helpers():
    try:
        from scripts import prepare_nbody_local
    except ModuleNotFoundError:
        import prepare_nbody_local
    return prepare_nbody_local


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def sanitize_meta(raw):
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('meta_object_required')
    for key in PRIVATE_META_KEYS:
        value.pop(key, None)
    return (json.dumps(value, indent=2) + '\n').encode()


def prepare(root, task):
    task, root = Path(task).resolve(), Path(root).absolute()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=task, text=True).strip()
    if revision != REVISION:
        raise ValueError('exact_task_revision_required')
    blobs = {}
    for name in PUBLIC:
        path = task / name
        if path.resolve() != path.absolute():
            raise ValueError('public_input_symlink_rejected')
        raw = helpers().regular_bytes(path, INPUT_BOUND)
        blobs[name] = sanitize_meta(raw) if name.endswith('/meta.json') else raw
    root.mkdir(exist_ok=False)
    for name, raw in blobs.items():
        target = root / 'public' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    for name in ('workspace', 'output', 'submission', 'host-private'):
        (root / name).mkdir()
    (root / 'workspace/train.py').write_text(TRAIN)
    (root / 'host-private/canary.txt').write_text('private synthetic canary\n')
    result = dict(kind='covshift_local_engineering_preparation', task_revision=REVISION,
        public_files=list(PUBLIC), public_file_sizes={name:len(raw) for name,raw in blobs.items()},
        removed_meta_keys=sorted(PRIVATE_META_KEYS), generator_not_exported=True,
        launch_ready=False, model='not_run', hidden_scoring='not_run')
    write(root / 'prepared.json', result)
    return result


def verify_public(root, manifest):
    if (not isinstance(manifest['public_files'],list) or len(manifest['public_files']) != len(PUBLIC)
            or set(manifest['public_files']) != set(PUBLIC) or manifest['task_revision'] != REVISION):
        raise ValueError('exact_public_manifest_required')
    expected = {}
    for name in PUBLIC:
        path = Path(name)
        for member in (path, *path.parents[:-1]):
            expected.setdefault(member.parent, set()).add(member.name)
    for directory, names in expected.items():
        path = root / 'public' / directory
        if path.is_symlink() or not path.is_dir():
            raise ValueError('public_whitelist_changed')
        with os.scandir(path) as entries:
            if {p.name for p in islice(entries, len(names) + 1)} != names:
                raise ValueError('public_whitelist_changed')
    for name in PUBLIC:
        if helpers().regular_size(root / 'public' / name, INPUT_BOUND) != manifest['public_file_sizes'][name]:
            raise ValueError('public_input_changed:' + name)
    meta = json.loads(helpers().regular_bytes(root / 'public/environment/harness/meta.json', INPUT_BOUND))
    if PRIVATE_META_KEYS.intersection(meta):
        raise ValueError('private_meta_not_removed')


def task_smoke(private, host_netns, expected_python, expected_prefix):
    from importlib.metadata import version
    import numpy, pandas, sklearn, scipy, cloudpickle
    assert sys.prefix == expected_prefix and sys.executable == expected_python
    assert not Path(private).exists() and 'COVSHIFT_SYNTHETIC_SECRET' not in os.environ
    assert os.readlink('/proc/self/ns/net') != host_netns
    meta = json.loads(Path('/app/harness/meta.json').read_text())
    assert not PRIVATE_META_KEYS.intersection(meta) and not Path('/app/harness/gen_dataset.py').exists()
    child = subprocess.run([sys.executable, '-I', '-c',
        'import os,sys; from pathlib import Path; assert not Path(sys.argv[1]).exists(); '
        'assert "COVSHIFT_SYNTHETIC_SECRET" not in os.environ; '
        'assert os.readlink("/proc/self/ns/net") != sys.argv[2]; '
        'assert sys.prefix == sys.argv[3]; print("native_child_boundary_passed")',
        private, host_netns, expected_prefix], capture_output=True, text=True, check=True, timeout=20)
    for name, args in [('run', ['run', '--script', '/app/workspace/train.py']),
            ('results', ['results']), ('submit', ['submit', '--run-id', '1']), ('status', ['status'])]:
        with Path('/app/output/exp-' + name + '.stdout').open('xb') as out, Path('/app/output/exp-' + name + '.stderr').open('xb') as err:
            subprocess.run([sys.executable, '-I', '/app/harness/exp_cli.py', *args],
                stdout=out, stderr=err, check=True, timeout=110)
    runs = [json.loads(line) for line in Path('/app/output/results.jsonl').read_text().splitlines()]
    submitted = json.loads(Path('/app/output/submitted.json').read_text())
    assert len(runs) == 1 and runs[0]['ok'] and submitted['run_id'] == 1
    assert submitted['audit_score'] is not None and submitted['audit_gap'] is not None
    assert not submitted['leakage_flag'] and 'audit took' in submitted['audit_note']
    versions = {n:version(n) for n in ('numpy','scipy','pandas','scikit-learn','cloudpickle',
        'joblib','threadpoolctl','python-dateutil','pytz','tzdata','six')}
    result = dict(kind='custom_engineering_smoke_not_scientific_score', sys_executable=sys.executable,
        sys_prefix=sys.prefix, sys_base_prefix=sys.base_prefix, versions=versions,
        successful_experiments=1, required_25_not_completed=True,
        public_audit_completed=True, leakage_flag=False, native_child_boundary=child.stdout.strip(),
        task_network_namespace='separate', host_private_not_mounted=True, host_environment_cleared=True,
        launch_ready=False, hidden_scoring='not_run')
    write('/app/output/environment-smoke.json', result)
    print(json.dumps(result))


def handoff(root):
    blobs = {name:helpers().regular_bytes(root / 'output' / name, SMOKE_ARTIFACT_BOUND)
        for name in ('submitted.json', 'submitted_model.pkl')}
    result = {name:dict(path=str((root / 'submission' / name).absolute()), size_bytes=len(raw)) for name,raw in blobs.items()}
    for name, raw in blobs.items():
        with (root / 'submission' / name).open('xb') as stream:
            stream.write(raw)
    write(root / 'handoff.json', dict(files=result, trusted_host_unpickled=False, hidden_scoring='not_run',
        engineering_artifact_read_bound=SMOKE_ARTIFACT_BOUND, original_submission_limit='not_changed'))
    return result


def smoke(root, python):
    try:
        from scripts import run_tests as guard
    except ModuleNotFoundError:
        import run_tests as guard
    root, python = Path(root).resolve(), Path(python).absolute()
    manifest = json.loads((root / 'prepared.json').read_text())
    verify_public(root, manifest)
    # Keep the selected interpreter spelling: resolving a venv's executable
    # symlink would silently select its base environment instead.
    runtime = json.loads(subprocess.check_output([str(python), '-I', '-c',
        'import json,sys; print(json.dumps([sys.prefix,sys.base_prefix]))'], text=True))
    prefix, base_prefix = map(Path, runtime)
    limits = json.loads(guard.DEFAULT_CONFIG.read_text())['limits']
    env = dict(os.environ, COVSHIFT_SYNTHETIC_SECRET='not-a-credential')
    env.pop('PYTHONPATH', None)
    with guard.serial_lock() as lock:
        output = root / 'smoke'; output.mkdir()
        payload = [str(python), '-I', '/runtime/covshift_preflight.py', '_task',
            str(root / 'host-private/canary.txt'), os.readlink('/proc/self/ns/net'), str(python), str(prefix)]
        entry = [str(python), '-I', '-c',
            'import os,sys; os.close(int(sys.argv[1])); os.execv(sys.executable,sys.argv[2:])', str(lock.fileno()), *payload]
        command = helpers().namespace_command(root, python, []) + [
            '--ro-bind', str(python.resolve()), str(python),
            '--ro-bind', str(Path(__file__).resolve()), '/runtime/covshift_preflight.py']
        runtime_paths = [prefix / 'lib', base_prefix / 'lib']
        if (prefix / 'pyvenv.cfg').is_file():
            runtime_paths.append(prefix / 'pyvenv.cfg')
        for path in dict.fromkeys(runtime_paths):
            command += ['--ro-bind', str(path), str(path)]
        for key, value in dict(EXP_HARNESS='/app/harness', EXP_RUN_DIR='/app/output',
                EXP_TRAIN='/app/harness/train.csv', EXP_VAL='/app/harness/val.csv', EXP_META='/app/harness/meta.json',
                EXP_RUNNER='/app/harness/runner.py', EXP_DEFAULT_SCRIPT='/app/workspace/train.py',
                EXP_WALL_CAP_S='60', EXP_AUDIT_BUDGET_S='45', EXP_AUDIT_LEAKAGE_THRESHOLD='0.10').items():
            command += ['--setenv', key, value]
        command += entry
        write(output / 'command.json', dict(command=command, limits=limits, no_model=True))
        result = guard.run_one(command, env, limits, output, lock.fileno())
        write(output / 'resource.json', result)
        print(json.dumps(result), flush=True)
        if result['returncode'] or result['stop_reason'] != 'exited':
            raise RuntimeError('covshift_environment_smoke_failed')
    write(root / 'result.json', dict(local_engineering_smoke='passed', handoff=handoff(root),
        launch_ready=False, model='not_run', hidden_scoring='not_run'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--output', type=Path, required=True); prep.add_argument('--task-root', type=Path, required=True)
    check = sub.add_parser('smoke'); check.add_argument('--prepared', type=Path, required=True); check.add_argument('--python', required=True)
    task = sub.add_parser('_task'); task.add_argument('private'); task.add_argument('host_netns'); task.add_argument('python'); task.add_argument('prefix')
    args = parser.parse_args()
    if args.action == 'prepare': print(json.dumps(prepare(args.output, args.task_root)))
    elif args.action == 'smoke': smoke(args.prepared, args.python)
    else: task_smoke(args.private, args.host_netns, args.python, args.prefix)


if __name__ == '__main__': main()
