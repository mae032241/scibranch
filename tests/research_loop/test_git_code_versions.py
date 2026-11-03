from __future__ import annotations

import os
import shutil
import stat

import pytest


@pytest.fixture
def helper(tmp_path):
    from scidiscovery.research_loop import code_versions
    assert shutil.which('git', path=os.defpath), 'code version checks require host Git'
    yield code_versions
    root = tmp_path / 'instance'
    if root.is_dir():
        for step, call in ((7, 'call-1'), (7, 'call-2'), (1000, 'one-file')):
            export = root / f'host-runtime/code-versions/exports/{step:03d}/{call}/tree'
            try:
                code_versions._directory(export.parent)
            except FileNotFoundError:
                continue
            if export.is_symlink():
                export.unlink()
            code_versions.cleanup_export(root, step=step, call_id=call)


def _source(tmp_path):
    root = tmp_path / 'instance'
    root.mkdir()
    code = root / 'workspace/code'
    code.mkdir(parents=True)
    (code / 'main.py').write_bytes(b"print('original')\r\n")
    return root, code


def _capture(helper, root, code, call='call-1', **kwargs):
    return helper.capture(root, step=7, call_id=call, code_root=code,
                          entry='main.py', selection='directory', logical_root='code', **kwargs)


def _project(tmp_path):
    root = tmp_path / 'instance'
    root.mkdir()
    project = root / 'workspace/project'
    for name in ('src', 'tools', 'empty'):
        (project / name).mkdir(parents=True)
    (project / 'src/main.py').write_bytes(b'run\n')
    (project / 'tools/helper.py').write_bytes(b'help\n')
    return root, project, dict(step=7, call_id='call-1', code_root=project,
        entry='src/main.py', selection='directory', logical_root='project',
        roots=['src', 'tools', 'empty'])


@pytest.mark.process_e2e
def test_project_capture_aggregates_only_declared_roots_and_preserves_empty_root_protocol(tmp_path, monkeypatch, helper):
    root, project, arguments = _project(tmp_path)
    for name in ('src/empty', 'tools/only-empty/child', 'empty/only/descendants'):
        (project / name).mkdir(parents=True)
    (project / 'undeclared').symlink_to(tmp_path / 'unreadable-missing')
    original_scandir = helper.os.scandir
    original_open = helper.os.open
    scanned = []

    def scandir(path):
        if isinstance(path, int):
            assert helper.os.fstat(path).st_ino != project.stat().st_ino
        scanned.append(path)
        return original_scandir(path)

    def open_file(path, flags, *args, **kwargs):
        assert str(path) != str(project / 'undeclared')
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(helper.os, 'scandir', scandir)
    monkeypatch.setattr(helper.os, 'open', open_file)
    version = helper.capture(root, **arguments)
    assert version['logical_root'] == 'project'
    assert version['entry'] == 'src/main.py'
    assert version['selection'] == 'directory'
    assert set(helper.verify(root, step=7, call_id='call-1', code_version=version)) == {
        'src/main.py', 'tools/helper.py'}
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert (export / 'src/main.py').read_bytes() == b'run\n'
    assert (export / 'tools/helper.py').read_bytes() == b'help\n'
    for name in ('src/empty', 'tools/only-empty', 'empty'):
        assert not (export / name).exists()
    assert not (export / 'undeclared').exists()
    assert helper.materialize(root, step=7, call_id='call-1', code_version=version) == export
    assert scanned


@pytest.mark.process_e2e
@pytest.mark.parametrize('limits', [{'max_files': 1}, {'max_total_bytes': 8}, {'max_scan_entries': 4}])
def test_project_capture_shares_limits_across_declared_roots_before_git(tmp_path, helper, limits):
    root, _, arguments = _project(tmp_path)
    with pytest.raises(ValueError):
        helper.capture(root, **arguments, limits=limits)
    assert not (root / 'host-runtime').exists()


@pytest.mark.process_e2e
def test_project_capture_rejects_cross_root_change_and_same_call_edit(tmp_path, monkeypatch, helper):
    root, project, arguments = _project(tmp_path)
    first = helper.capture(root, **arguments)
    (project / 'tools/helper.py').write_bytes(b'other\n')
    with pytest.raises(ValueError, match='already pins different code'):
        helper.capture(root, **arguments)
    helper.verify(root, step=7, call_id='call-1', code_version=first)
    original_read = helper._read_file

    def read(path, expected, inventory):
        result = original_read(path, expected, inventory)
        if path == project / 'tools/helper.py':
            (project / 'src/main.py').write_bytes(b'changed after its read\n')
        return result

    monkeypatch.setattr(helper, '_read_file', read)
    arguments['call_id'] = 'call-2'
    with pytest.raises(ValueError, match='changed during capture'):
        helper.capture(root, **arguments)
    ref = root / 'host-runtime/code-versions/repository.git/refs/scidiscovery/steps/007/calls/call-2'
    assert not ref.exists()


@pytest.mark.parametrize('roots,entry', [(['src'], 'tools/helper.py'), (['src', 'src'], 'src/main.py'),
    (['src', 'src/nested'], 'src/main.py'), (['../src'], 'src/main.py'), ([], 'src/main.py')])
def test_project_capture_rejects_invalid_roots_before_git(tmp_path, helper, roots, entry):
    root, _, arguments = _project(tmp_path)
    arguments.update(roots=roots, entry=entry)
    with pytest.raises(ValueError):
        helper.capture(root, **arguments)
    assert not (root / 'host-runtime').exists()


@pytest.mark.process_e2e
def test_capture_exports_raw_bytes_modes_despite_poisoned_git_environment(tmp_path, monkeypatch, helper):
    root, code = _source(tmp_path)
    workspace_git = code.parent / '.git'
    workspace_git.mkdir()
    (workspace_git / 'config').write_text('[core]\nrepositoryformatversion = 999\n', encoding='ascii')
    original = {
        'main.py': b"print('original')\r\n",
        '.gitignore': b'ignored.txt\n',
        '.gitattributes': b'* text eol=lf filter=forbidden\nmain.py export-ignore\n',
        'ignored.txt': b'$Format:%H$\r\n',
        'nested/helper.sh': b'#!/bin/sh\nprintf raw\\n\n',
        'empty': b'',
        'binary.bin': b'\0\xff\x80binary\0',
        'no-final-newline': b'raw without a trailing newline',
        'nested/space tab\tline\n"quoted".bin': (
            b'blob\nmark :1\ndata 0\n\nget-mark :1\ndone\n'
            b'commit refs/heads/injected\nreset refs/heads/injected\n'
            b'M 100644 inline injected\n\0\xff'),
        'duplicate.bin': b'$Format:%H$\r\n',
        '--leading-dash': b'-option\n',
    }
    (code / 'nested').mkdir()
    for name, content in original.items():
        path = code / name
        path.write_bytes(content)
        path.chmod(0o755 if name.endswith('.sh') else 0o644)
    poison = tmp_path / 'poison'
    poison.mkdir()
    (poison / '.gitconfig').write_text(
        '[filter "forbidden"]\nrequired = true\nclean = false\nsmudge = false\n', encoding='ascii')
    for name, value in {
        'HOME': str(poison), 'PATH': str(poison), 'GIT_DIR': str(workspace_git),
        'GIT_WORK_TREE': str(poison), 'GIT_INDEX_FILE': str(poison / 'index'),
        'GIT_OBJECT_DIRECTORY': str(poison / 'objects'), 'GIT_NAMESPACE': 'poison',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(poison / 'alternates'),
        'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.repositoryformatversion',
        'GIT_CONFIG_VALUE_0': '999', 'GIT_AUTHOR_DATE': 'invalid date',
    }.items():
        monkeypatch.setenv(name, value)
    version = _capture(helper, root, code)
    assert set(version) == {'version', 'object_format', 'commit', 'tree_path',
                            'selection', 'entry', 'logical_root'}
    helper.verify(root, step=7, call_id='call-1', code_version=version)
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert export == root / 'host-runtime/code-versions/exports/007/call-1/tree'
    for name, content in original.items():
        assert (export / name).read_bytes() == content
        assert stat.S_IMODE((export / name).stat().st_mode) == (0o555 if name.endswith('.sh') else 0o444)
    assert stat.S_IMODE(export.stat().st_mode) == 0o555
    assert not (workspace_git / 'index').exists()
    assert not (poison / 'index').exists()
    assert not (root / 'host-runtime/code-versions/repository.git/index').exists()
    assert helper.materialize(root, step=7, call_id='call-1', code_version=version) == export


@pytest.mark.process_e2e
def test_capture_git_process_count_is_constant_for_one_and_eight_files(tmp_path, monkeypatch, helper):
    original_popen = helper.subprocess.Popen
    commands = []

    def popen(command, *args, **kwargs):
        commands.append(tuple(command))
        return original_popen(command, *args, **kwargs)

    monkeypatch.setattr(helper.subprocess, 'Popen', popen)
    captures = []
    for count in (1, 8):
        case = tmp_path / f'{count}-files'
        case.mkdir()
        root, code = _source(case)
        for number in range(1, count):
            (code / f'file-{number}.py').write_bytes(f'raw {number}\n'.encode('ascii'))
        start = len(commands)
        _capture(helper, root, code)
        captures.append(commands[start:])

    assert len(captures[0]) == len(captures[1])
    for capture_commands in captures:
        assert sum('fast-import' in command for command in capture_commands) == 1
        assert not any('hash-object' in command for command in capture_commands)


@pytest.mark.process_e2e
@pytest.mark.parametrize('response', ['missing', 'extra', 'uppercase', 'command_error'])
def test_capture_rejects_blob_import_failure_before_index_and_pin(tmp_path, monkeypatch, helper, response):
    root, code = _source(tmp_path)
    original_git = helper._git
    commands, temporary_paths = [], []

    def git(private, arguments, **kwargs):
        commands.append(arguments[0])
        if arguments[0] != 'fast-import':
            return original_git(private, arguments, **kwargs)
        assert arguments == ('fast-import', '--quiet', '--done')
        assert isinstance(kwargs['data'], bytearray)
        assert kwargs['input_limit'] == len(kwargs['data'])
        assert kwargs['output_limit'] == 41
        index = kwargs['index']
        lock = index.with_name(index.name + '.lock')
        for path in (index, lock):
            path.write_bytes(b'partial index\n')
            temporary_paths.append(path)
        if response == 'command_error':
            kwargs['data'] = b'unsupported\n'
            kwargs['input_limit'] = len(kwargs['data'])
            return original_git(private, arguments, **kwargs)
        output = original_git(private, arguments, **kwargs)
        if response == 'missing':
            return output[:-1]
        if response == 'extra':
            return output + output
        return b'A' * 40 + b'\n'

    monkeypatch.setattr(helper, '_git', git)
    with pytest.raises(ValueError):
        _capture(helper, root, code)
    assert len(temporary_paths) == 2
    assert all(not path.exists() for path in temporary_paths)
    assert 'update-index' not in commands
    assert 'update-ref' not in commands
    ref = root / 'host-runtime/code-versions/repository.git/refs/scidiscovery/steps/007/calls/call-1'
    assert not ref.exists()


@pytest.mark.process_e2e
def test_pins_survive_draft_edits_cleanup_and_rematerialization(tmp_path, helper):
    root, code = _source(tmp_path)
    first = _capture(helper, root, code)
    first_export = helper.materialize(root, step=7, call_id='call-1', code_version=first)
    (code / 'main.py').write_bytes(b'new draft\n')
    second = _capture(helper, root, code, call='call-2')
    second_export = helper.materialize(root, step=7, call_id='call-2', code_version=second)
    assert second['commit'] != first['commit']
    (code / 'main.py').unlink()
    helper.cleanup_export(root, step=7, call_id='call-1')
    helper.cleanup_export(root, step=7, call_id='call-1')
    assert not first_export.exists()
    assert (second_export / 'main.py').read_bytes() == b'new draft\n'
    for call, version in (('call-1', first), ('call-2', second)):
        helper.verify(root, step=7, call_id=call, code_version=version)
    restored = helper.materialize(root, step=7, call_id='call-1', code_version=first)
    assert restored == first_export
    assert (restored / 'main.py').read_bytes() == b"print('original')\r\n"
    with pytest.raises(ValueError):
        helper.verify(root, step=7, call_id='call-2', code_version=first)


@pytest.mark.process_e2e
def test_file_selection_is_complete_and_changed_same_call_refuses(tmp_path, helper):
    root, code = _source(tmp_path)
    (code / 'nested').mkdir()
    (code / 'nested/main.py').write_bytes(b'one\n')
    (code / 'unselected').symlink_to(code / 'main.py')
    (code / 'unselected-empty').mkdir()
    arguments = dict(step=1000, call_id='one-file', code_root=code, entry='nested/main.py',
                     selection='file', logical_root='workspace/code', limits={'max_files': 1})
    first = helper.capture(root, **arguments)
    assert helper.capture(root, **arguments) == first
    export = helper.materialize(root, step=1000, call_id='one-file', code_version=first)
    assert (export / 'nested/main.py').read_bytes() == b'one\n'
    assert not (export / 'main.py').exists()
    assert not (export / 'unselected').exists()
    (code / 'nested/main.py').write_bytes(b'two\n')
    with pytest.raises(ValueError, match='already pins different code'):
        helper.capture(root, **arguments)
    helper.verify(root, step=1000, call_id='one-file', code_version=first)


@pytest.mark.process_e2e
def test_capture_ignores_empty_directory_branches_and_preserves_regular_files(tmp_path, helper):
    root, code = _source(tmp_path)
    for name in ('empty', 'nested/empty', 'only-empty/child/descendant'):
        (code / name).mkdir(parents=True)
    helper_bytes = b'#!/bin/sh\nprintf original\\n\n'
    (code / 'nested/helper.sh').write_bytes(helper_bytes)
    (code / 'nested/helper.sh').chmod(0o755)
    (code / 'zero-byte').write_bytes(b'')

    version = _capture(helper, root, code)
    assert version['entry'] == 'main.py'
    assert set(helper.verify(root, step=7, call_id='call-1', code_version=version)) == {
        'main.py', 'nested/helper.sh', 'zero-byte'}
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert (export / 'main.py').read_bytes() == b"print('original')\r\n"
    assert (export / 'nested/helper.sh').read_bytes() == helper_bytes
    assert stat.S_IMODE((export / 'nested/helper.sh').stat().st_mode) == 0o555
    assert (export / 'zero-byte').is_file()
    assert (export / 'zero-byte').read_bytes() == b''
    for name in ('empty', 'nested/empty', 'only-empty'):
        assert (code / name).is_dir()
        assert not (export / name).exists()
    assert helper.materialize(root, step=7, call_id='call-1', code_version=version) == export
    (code / 'empty').rmdir()
    (code / 'new-empty').mkdir()
    assert _capture(helper, root, code) == version


@pytest.mark.process_e2e
@pytest.mark.parametrize('case', ['file_bytes', 'total_bytes', 'files', 'scan_entries',
                                  'symlink_file', 'symlink_directory', 'fifo', 'git_component'])
def test_preflight_rejects_bounds_and_nonregular_code_before_repository_writes(tmp_path, helper, case):
    root, code = _source(tmp_path)
    limits = {}
    if case == 'file_bytes':
        limits = {'max_file_bytes': 1}
    elif case == 'total_bytes':
        limits = {'max_total_bytes': 1}
    elif case == 'files':
        (code / 'second.py').write_bytes(b'x')
        limits = {'max_files': 1}
    elif case == 'scan_entries':
        (code / 'empty').mkdir()
        limits = {'max_scan_entries': 1}
    elif case == 'symlink_file':
        (code / 'link').symlink_to(code / 'main.py')
    elif case == 'symlink_directory':
        (code / 'link').symlink_to(code.parent, target_is_directory=True)
    elif case == 'fifo':
        os.mkfifo(code / 'pipe')
    elif case == 'git_component':
        (code / '.git').mkdir()
    with pytest.raises(ValueError):
        _capture(helper, root, code, limits=limits)
    assert not (root / 'host-runtime').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('drift', ['bytes', 'executable', 'added_file', 'ancestor'])
def test_capture_barrier_rechecks_drift_before_pinning(tmp_path, monkeypatch, helper, drift):
    root, code = _source(tmp_path)
    original_git = helper._git
    changed = False

    def git(private, arguments, **kwargs):
        nonlocal changed
        output = original_git(private, arguments, **kwargs)
        if arguments[0] == 'commit-tree' and not changed:
            changed = True
            if drift == 'bytes':
                (code / 'main.py').write_bytes(b'drift\n')
            elif drift == 'executable':
                (code / 'main.py').chmod(0o755)
            elif drift == 'added_file':
                (code / 'new.py').write_bytes(b'new\n')
            else:
                code.rename(code.with_name('old-code'))
                code.mkdir()
                (code / 'main.py').write_bytes(b"print('original')\r\n")
        return output

    monkeypatch.setattr(helper, '_git', git)
    with pytest.raises(ValueError, match='changed'):
        _capture(helper, root, code)
    assert changed
    ref = root / 'host-runtime/code-versions/repository.git/refs/scidiscovery/steps/007/calls/call-1'
    assert not ref.exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('selection', ['file', 'directory'])
def test_preflight_directory_swap_never_reads_foreign_tree(tmp_path, monkeypatch, helper, selection):
    root, code = _source(tmp_path)
    nested = code / 'nested'
    nested.mkdir()
    (nested / 'helper.py').write_bytes(b'original code\n')
    outside = tmp_path / 'scientific-input'
    outside.mkdir()
    (outside / 'helper.py').write_bytes(b'foreign scientific bytes\n')
    original_open = helper._open_directory
    swapped = False

    def open_directory(path, *, dir_fd=None, expected=None):
        nonlocal swapped
        if path == 'nested' and dir_fd is not None and not swapped:
            swapped = True
            nested.rename(code / 'parked')
            nested.symlink_to(outside, target_is_directory=True)
        return original_open(path, dir_fd=dir_fd, expected=expected)

    monkeypatch.setattr(helper, '_open_directory', open_directory)
    with pytest.raises((OSError, ValueError)):
        helper.capture(root, step=7, call_id='call-1', code_root=code, entry='nested/helper.py',
                       selection=selection, logical_root='code')
    assert swapped
    assert not (root / 'host-runtime').exists()


@pytest.mark.process_e2e
def test_export_mutation_refuses_and_cleanup_never_follows_export_symlink(tmp_path, helper):
    root, code = _source(tmp_path)
    version = _capture(helper, root, code)
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    (export / 'main.py').chmod(0o644)
    (export / 'main.py').write_bytes(b'mutated\n')
    with pytest.raises(ValueError, match='differs'):
        helper.materialize(root, step=7, call_id='call-1', code_version=version)
    helper.cleanup_export(root, step=7, call_id='call-1')
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'keep'
    sentinel.write_bytes(b'keep')
    export.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        helper.cleanup_export(root, step=7, call_id='call-1')
    with pytest.raises(ValueError):
        helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert sentinel.read_bytes() == b'keep'
    export.unlink()
    restored = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert (restored / 'main.py').read_bytes() == b"print('original')\r\n"


@pytest.mark.process_e2e
def test_cleanup_parent_swap_cannot_redirect_deletion(tmp_path, monkeypatch, helper):
    root, code = _source(tmp_path)
    version = _capture(helper, root, code)
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    holder = root / 'host-runtime/code-versions'
    step_directory = export.parent.parent
    parked = step_directory.with_name('parked')
    outside_step = tmp_path / 'outside-step'
    outside_tree = outside_step / 'call-1/tree'
    outside_tree.mkdir(parents=True)
    sentinel = outside_tree / 'keep'
    sentinel.write_bytes(b'keep')
    original_open = helper.os.open
    swapped = False

    def open_file(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if (dir_fd is None and os.fspath(path) in (str(holder), str(export.parent))
                and not swapped):
            swapped = True
            step_directory.rename(parked)
            step_directory.symlink_to(outside_step, target_is_directory=True)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(helper.os, 'open', open_file)
    try:
        with pytest.raises((OSError, ValueError)):
            helper.cleanup_export(root, step=7, call_id='call-1')
        assert swapped
        assert sentinel.read_bytes() == b'keep'
    finally:
        monkeypatch.setattr(helper.os, 'open', original_open)
        if step_directory.is_symlink():
            step_directory.unlink()
        if parked.exists():
            parked.rename(step_directory)
    assert (export / 'main.py').read_bytes() == b"print('original')\r\n"
    helper.verify(root, step=7, call_id='call-1', code_version=version)


@pytest.mark.process_e2e
@pytest.mark.parametrize('injection', ['extra_file', 'earlier_file'])
def test_new_export_rechecks_complete_bytes_before_returning(tmp_path, monkeypatch, helper, injection):
    root, code = _source(tmp_path)
    (code / 'z.py').write_bytes(b'later\n')
    version = _capture(helper, root, code)
    export = root / 'host-runtime/code-versions/exports/007/call-1/tree'
    original_blob = helper._blob
    injected = False

    def blob(private, record, context):
        nonlocal injected
        content = original_blob(private, record, context)
        if content == b'later\n' and not injected:
            injected = True
            if injection == 'extra_file':
                (export / 'extra.py').write_bytes(b'extra\n')
            else:
                (export / 'main.py').chmod(0o644)
                (export / 'main.py').write_bytes(b'corrupted\n')
                (export / 'main.py').chmod(0o444)
        return content

    monkeypatch.setattr(helper, '_blob', blob)
    with pytest.raises(ValueError, match='differs'):
        helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert injected
    assert not export.exists()
    helper.verify(root, step=7, call_id='call-1', code_version=version)
    monkeypatch.setattr(helper, '_blob', original_blob)
    restored = helper.materialize(root, step=7, call_id='call-1', code_version=version)
    assert (restored / 'main.py').read_bytes() == b"print('original')\r\n"


@pytest.mark.process_e2e
def test_original_effective_limits_can_be_supplied_to_verify_and_materialize(tmp_path, helper):
    root, code = _source(tmp_path)
    content = b'x' * (1048576 + 1)
    (code / 'main.py').write_bytes(content)
    limits = {'max_file_bytes': len(content)}
    version = _capture(helper, root, code, limits=limits)
    with pytest.raises(ValueError, match='max_file_bytes'):
        helper.verify(root, step=7, call_id='call-1', code_version=version)
    with pytest.raises(ValueError, match='max_file_bytes'):
        helper.materialize(root, step=7, call_id='call-1', code_version=version)
    helper.verify(root, step=7, call_id='call-1', code_version=version, limits=limits)
    export = helper.materialize(root, step=7, call_id='call-1', code_version=version, limits=limits)
    assert (export / 'main.py').read_bytes() == content


@pytest.mark.process_e2e
def test_transfer_preserves_exact_calls_after_source_git_is_deleted(tmp_path, helper):
    root, code = _source(tmp_path)
    (code / 'helper.sh').write_bytes(b'#!/bin/sh\nprintf original\\n\n')
    (code / 'helper.sh').chmod(0o755)
    first = _capture(helper, root, code)
    (code / 'main.py').write_bytes(b'next accepted version\n')
    second = _capture(helper, root, code, call='call-2')
    (code / 'main.py').write_bytes(b'current uncommitted draft\n')
    child = tmp_path / 'child'
    child.mkdir()
    for call, version in (('call-1', first), ('call-2', second)):
        helper.transfer(root, child, step=7, call_id=call, code_version=version)
        helper.transfer(root, child, step=7, call_id=call, code_version=version)
    shutil.rmtree(root / 'host-runtime/code-versions/repository.git')
    repository = child / 'host-runtime/code-versions/repository.git'
    assert not (repository / 'objects/info/alternates').exists()
    try:
        for call, version, raw in (('call-1', first, b"print('original')\r\n"),
                ('call-2', second, b'next accepted version\n')):
            exported = helper.materialize(child, step=7, call_id=call, code_version=version)
            assert (exported / 'main.py').read_bytes() == raw
            assert stat.S_IMODE((exported / 'helper.sh').stat().st_mode) == 0o555
    finally:
        for call in ('call-1', 'call-2'):
            helper.cleanup_export(child, step=7, call_id=call)


@pytest.mark.process_e2e
def test_transfer_rejects_conflicting_original_call_ref(tmp_path, helper):
    root, code = _source(tmp_path)
    first = _capture(helper, root, code)
    child = tmp_path / 'child'
    child.mkdir()
    helper.transfer(root, child, step=7, call_id='call-1', code_version=first)
    other_case = tmp_path / 'conflict'
    other_case.mkdir()
    other_root, other_code = _source(other_case)
    (other_code / 'main.py').write_bytes(b'conflicting original\n')
    conflict = _capture(helper, other_root, other_code)
    with pytest.raises(ValueError, match='already pins different code'):
        helper.transfer(other_root, child, step=7, call_id='call-1', code_version=conflict)
    helper.verify(child, step=7, call_id='call-1', code_version=first)


@pytest.mark.process_e2e
def test_invalid_identifiers_paths_and_version_shape_refuse(tmp_path, helper):
    root, code = _source(tmp_path)
    for call in ('.', '..', 'a..b', '.hidden', 'trailing.', 'x.lock', 'bad/ref'):
        with pytest.raises(ValueError):
            _capture(helper, root, code, call=call)
    for step in (True, -1):
        with pytest.raises(ValueError):
            helper.capture(root, step=step, call_id='call', code_root=code, entry='main.py',
                           selection='file', logical_root='code')
    for entry, logical_root in (('../main.py', 'code'), ('./main.py', 'code'), ('main.py', '.')):
        with pytest.raises(ValueError):
            helper.capture(root, step=7, call_id='call', code_root=code, entry=entry,
                           selection='file', logical_root=logical_root)
    assert not (root / 'host-runtime').exists()
    version = _capture(helper, root, code)
    for changed in ({**version, 'version': True}, {**version, 'private_path': '/private'},
                    {**version, 'tree_path': 'subtree'}, {**version, 'entry': '..'}):
        with pytest.raises(ValueError):
            helper.verify(root, step=7, call_id='call-1', code_version=changed)
