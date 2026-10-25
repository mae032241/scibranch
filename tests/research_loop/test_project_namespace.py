"""Small explicit project mount plans; process cases never invoke a model."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import native_execution, project_layout, shared_input_views as views
from scidiscovery.research_loop.experiments import UnsupportedExecution


@pytest.fixture
def project_view(tmp_path, monkeypatch):
    base = tmp_path / 'base'
    base.mkdir()
    for name, content in [('config.txt', 'base'), ('hidden.txt', 'untracked')]:
        (base / name).write_text(content)
    (base / '.git').mkdir()
    (base / 'src').mkdir()
    (base / 'src/main.py').write_text('old base code')
    work = tmp_path / 'instance/project/nodes/001'
    for relative in ('project/src', 'project/empty', 'artifacts'):
        (work / relative).mkdir(parents=True, exist_ok=True)
    (work / 'project/src/main.py').write_text('draft code')
    project = dict(base_root=str(base), editable=['src', 'empty'], scratch=['build'], outputs=['out'],
        base_identity=dict(object_format='sha1', commit='0'*40))
    calls = []
    def files(declaration, *, context=None, exclude=()):
        calls.append(list(exclude))
        return [dict(source=base / 'config.txt', relative='config.txt')]
    monkeypatch.setattr(project_layout, 'files', files)
    return project, base, work, calls


def test_worker_plan_masks_untracked_and_grants_only_declared_directories(project_view):
    project, base, work, calls = project_view
    args = views.project_arguments(project, work, artifacts=work/'artifacts', writable=True)
    assert args[:2] == ['--tmpfs', str(base)]
    assert ['src', 'empty', 'build', 'out'] == calls[0]
    assert str(base/'hidden.txt') not in args
    assert str(base/'.git') not in args
    assert ['--ro-bind', str(base/'config.txt'), str(base/'config.txt')] == args[args.index('--ro-bind'):args.index('--ro-bind')+3]
    assert ['--bind', str(work/'project/src'), str(base/'src')] in [args[i:i+3] for i in range(len(args)-2)]
    assert base not in views.project_targets(project, writable=True)


def test_run_plan_uses_exact_export_and_represents_empty_code_root(project_view, tmp_path):
    project, base, work, _ = project_view
    export = tmp_path/'export'
    (export/'src').mkdir(parents=True)
    (export/'src/main.py').write_text('pinned code')
    artifacts = tmp_path/'original-call-artifacts'
    artifacts.mkdir()
    args = views.project_arguments(project, work, code_tree=export, artifacts=artifacts, writable=False)
    assert ['--ro-bind', str(export/'src'), str(base/'src')] in [args[i:i+3] for i in range(len(args)-2)]
    assert ['--tmpfs', str(base/'empty'), '--remount-ro', str(base/'empty')] in [args[i:i+4] for i in range(len(args)-3)]
    assert ['--bind', str(artifacts/'project/out'), str(base/'out')] in [args[i:i+3] for i in range(len(args)-2)]
    assert not (export/'empty').exists()


def test_project_overlap_is_rejected_before_launch(project_view):
    project, base, _, _ = project_view
    with pytest.raises(UnsupportedExecution, match='overlaps'):
        views.project_overlap(project, [(base/'src/data.json', Path('/scientific/input'))])


def test_output_directories_keep_the_complete_relative_path(project_view):
    project, base, work, _ = project_view
    project = dict(project, outputs=['first/results', 'second/results'])
    args = views.project_arguments(project, work, artifacts=work/'artifacts', writable=True)
    for name in project['outputs']:
        expected = ['--bind', str(work/'artifacts/project'/name), str(base/name)]
        assert expected in [args[i:i+3] for i in range(len(args)-2)]


def test_final_readonly_aliases_cover_editable_output_and_parent_views(project_view):
    project, base, work, _ = project_view
    original = work/'inputs/data.txt'
    mappings = {
        str(work/'project/src/shared.txt'): original,
        str(work/'artifacts/project/out/shared.txt'): original,
    }
    assert views._project_readonly_aliases(project, work, work, mappings) == [
        (original, base/'src/shared.txt'), (original, base/'out/shared.txt')]
    parent = views._project_readonly_aliases(project, work, work,
        {str(work/'project'): work/'project'})
    assert (work/'project/src', base/'src') in parent
    assert (work/'project/empty', base/'empty') in parent


@pytest.mark.process_e2e
def test_worker_wrap_keeps_scientific_hardlinks_readonly_at_all_project_aliases(project_view, tmp_path, monkeypatch):
    from scidiscovery.research_loop import contracts, experiment_client, originals, skill_supply
    project, base, work, _ = project_view
    try:
        program = views.ensure_capability()
    except UnsupportedExecution as error:
        pytest.skip(str(error))
    root = tmp_path/'instance'
    original = tmp_path/'scientific-original.txt'
    original.write_text('scientific original')
    draft = work/'project/src/shared.txt'
    output = work/'artifacts/project/out/shared.txt'
    output.parent.mkdir(parents=True)
    os.link(original, draft)
    os.link(original, output)
    settings = dict(project=project, native_backend='codex', native_launcher_argv=['codex'],
        native_options=dict(workspace='workspace-write', network=False))
    monkeypatch.setattr(contracts, 'settings', lambda *args: settings)
    monkeypatch.setattr(experiment_client, '_input_rows', lambda job: [dict(path='inputs/data.txt')])
    monkeypatch.setattr(originals, 'material_path', lambda root, row: original)
    monkeypatch.setattr(views, '_wake_mappings', lambda *args: [])
    monkeypatch.setattr(views, '_author_context', lambda *args: None)
    monkeypatch.setattr(views, 'selected_code_mounts', lambda *args, **kwargs: [])
    monkeypatch.setattr(skill_supply, 'native_mounts', lambda *args: [])
    # Keep the actual wrap/mount_arguments/inode inventory path. This policy
    # seam stub prevents passing a model-specific -c flag to the fake Python.
    declaration = 'sandbox_workspace_write='+native_execution._toml(dict(
        writable_roots=[str(path) for path in views.project_targets(project, writable=True)],
        network_access=False, exclude_tmpdir_env_var=False, exclude_slash_tmp=False))
    payload = '''import pathlib,sys
paths=[pathlib.Path(name) for name in sys.argv[1:6]]
for path in paths:
 assert path.read_text()=='scientific original'
 for action in (lambda: path.write_text('changed'), lambda: path.chmod(0o600)):
  try:action()
  except OSError:pass
  else:raise AssertionError(str(path))
(pathlib.Path(sys.argv[6])/'ordinary.txt').write_text('allowed')
'''
    paths = [work/'inputs/data.txt', draft, base/'src/shared.txt', output, base/'out/shared.txt']
    # wrap accepts opaque argv; its declaration check sees an explicit inert
    # -c pair after Python's payload arguments, without executing a model.
    argv = [sys.executable, '-I', '-c', payload, *map(str, paths), str(base/'src'), '-c', declaration]
    wrapped = views.wrap(root, dict(step=1, role='W', project=project), work, argv)
    assert wrapped[0] == program
    assert isinstance(wrapped, views._BwrapCommand)
    with views.popen_arguments(wrapped) as (launch_args, pass_fds):
        completed = subprocess.run(launch_args, pass_fds=pass_fds, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
    assert original.read_text() == 'scientific original'
    assert (work/'project/src/ordinary.txt').read_text() == 'allowed'


def test_native_projection_preserves_workspace_boundary_and_network(project_view):
    project, base, work, _ = project_view
    policy = dict(writable_roots=[], network_access=True, exclude_tmpdir_env_var=True, exclude_slash_tmp=True)
    writes = views.project_targets(project, writable=False)
    result = native_execution._profile(policy, work, '/task-tmp', virtual=True, project_writes=writes)
    assert result['extends'] == ':workspace'
    assert result['network'] == dict(enabled=True)
    assert result['filesystem'][str(base/'build')] == 'write'
    assert result['filesystem'][str(base/'out')] == 'write'
    assert str(base) not in result['filesystem']
    assert str(base/'src') not in result['filesystem']
    assert native_execution._profile(policy, work, '/task-tmp', virtual=True)['filesystem'] == {
        ':tmpdir': 'read', ':slash_tmp': 'read'}


@pytest.mark.parametrize('mode', ['read-only', 'danger-full-access'])
def test_native_readonly_or_unrestricted_policy_cannot_gain_project_writes(mode):
    settings = dict(native_backend='codex', native_launcher_argv=['codex'],
        native_options=dict(workspace=mode, network=False))
    with pytest.raises(UnsupportedExecution, match='frozen standard Codex workspace-write'):
        native_execution.project_policy(settings)


@pytest.mark.process_e2e
@pytest.mark.parametrize('worker', [True, False])
def test_project_kernel_mount_writes_and_original_call_outputs(project_view, tmp_path, worker):
    project, base, work, _ = project_view
    try:
        program = views.ensure_capability()
    except UnsupportedExecution as error:
        pytest.skip(str(error))
    export = tmp_path/'exact-export'
    (export/'src').mkdir(parents=True)
    (export/'src/main.py').write_text('pinned code')
    artifacts = tmp_path/'accepted-call-artifacts' if not worker else work/'artifacts'
    artifacts.mkdir(exist_ok=True)
    args = views.project_arguments(project, work, artifacts=artifacts,
        code_tree=None if worker else export, writable=worker)
    payload = '''import pathlib,sys
p=pathlib.Path(sys.argv[1]);worker=sys.argv[2]=='yes'
assert not (p/'hidden.txt').exists() and not (p/'.git').exists()
assert (p/'src/main.py').read_text()==('draft code' if worker else 'pinned code')
for name,allowed in [('config.txt',False),('undeclared',False),('src/main.py',worker),('empty/new',worker),('build/temporary',True),('out/result',True)]:
 try:(p/name).write_text('written')
 except OSError:
  assert not allowed,name
 else:assert allowed,name
'''
    completed = subprocess.run([program, '--unshare-user', '--die-with-parent', '--bind', '/', '/',
        *args, '--', sys.executable, '-I', '-c', payload, str(base), 'yes' if worker else 'no'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
    assert (artifacts/'project/out/result').read_text() == 'written'
    assert not (base/'build').exists()
    assert (base/'config.txt').read_text() == 'base'
    assert (base/'src/main.py').read_text() == 'old base code'


@pytest.mark.process_e2e
def test_project_native_inner_profile_keeps_base_readonly_with_base_cwd(project_view, tmp_path):
    project, base, work, _ = project_view
    try:
        program = views.ensure_capability()
    except UnsupportedExecution as error:
        pytest.skip(str(error))
    # Explicit installed ELF only. Other machines opt in with an exact path;
    # this never invokes a package wrapper or searches installation trees.
    native = Path(os.environ.get('SCID_TEST_NATIVE_SANDBOX',
        '/home/da/.nvm/versions/node/v25.2.1/lib/node_modules/@openai/codex/node_modules/'
        '@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'))
    if not native.is_absolute() or not native.is_file() or not os.access(native, os.X_OK):
        pytest.skip('Exact native sandbox ELF is unavailable; set SCID_TEST_NATIVE_SANDBOX')
    with native.open('rb') as stream:
        if stream.read(4) != b'\x7fELF':pytest.skip('Exact native sandbox program is not ELF')
    export = tmp_path/'inner-exact-export'
    (export/'src').mkdir(parents=True)
    (export/'src/main.py').write_text('pinned code')
    artifacts = tmp_path/'inner-original-call-artifacts'
    artifacts.mkdir()
    isolated_home = tmp_path/'isolated-home'
    isolated_home.mkdir()
    codex_home = isolated_home/'.codex'
    codex_home.mkdir()
    task_tmp = tmp_path/'task-tmp'
    task_tmp.mkdir()
    for name in ('.git', '.agents', '.codex', '.aws'):
        (work/name).mkdir()
    policy = dict(writable_roots=[], network_access=False,
        exclude_tmpdir_env_var=True, exclude_slash_tmp=True)
    profile = native_execution._profile(policy, work, str(task_tmp), virtual=True,
        project_writes=views.project_targets(project, writable=False))
    configuration = dict(default_permissions=native_execution.PERMISSION_PROFILE,
        permissions={native_execution.PERMISSION_PROFILE:profile})
    (codex_home/'config.toml').write_text('\n'.join(
        key+'='+native_execution._toml(value) for key,value in configuration.items())+'\n')
    args = views.project_arguments(project, work, code_tree=export, artifacts=artifacts, writable=False)
    payload = '''import pathlib,sys
p=pathlib.Path(sys.argv[1]);assert pathlib.Path.cwd()==p
for name,allowed in [('config.txt',False),('undeclared',False),('src/main.py',False),('empty/new',False),('build/temporary',True),('out/result',True)]:
 try:(p/name).write_text('written')
 except OSError:
  assert not allowed,name
 else:assert allowed,name
'''
    command = ['/bin/sh', '-c', 'cd "$1" && shift && exec "$@"', 'scid-project-cwd', str(base),
        sys.executable, '-I', '-c', payload, str(base)]
    environment = dict(PATH='/usr/bin:/bin', HOME=str(isolated_home), CODEX_HOME=str(codex_home),
        TMPDIR=str(task_tmp), XDG_RUNTIME_DIR=str(task_tmp), LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
    bootstrap = ['custom-project-native-bootstrap', str(task_tmp), str(native), 'sandbox',
        '-P', native_execution.PERMISSION_PROFILE, '-C', str(work), '--', *command]
    completed = subprocess.run([program, '--unshare-user', '--unshare-net', '--die-with-parent', '--bind', '/', '/',
        '--tmpfs', '/tmp', '--bind', str(work), str(work),
        '--bind', str(isolated_home), str(isolated_home),
        '--proc', '/proc', '--dev', '/dev', '--tmpfs', str(task_tmp), *args,
        '--', '/bin/sh', '-c', 'umask 077; chmod 700 "$1" && shift && exec "$@"', *bootstrap],
        env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
    assert (artifacts/'project/out/result').read_text() == 'written'
    assert (export/'src/main.py').read_text() == 'pinned code'
    assert not (base/'build').exists()
