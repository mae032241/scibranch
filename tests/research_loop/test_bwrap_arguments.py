"""Bounded bwrap FD transport; no model, solver or large project fixture."""
import errno
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import shared_input_views as views


def _closed(descriptor):
    with pytest.raises(OSError) as error:
        os.fstat(descriptor)
    assert error.value.errno == errno.EBADF


def test_owned_arguments_preserve_order_bytes_and_close_parent_descriptor():
    arguments = ['--setenv', 'NAME', 'first', '--setenv', 'NAME', 'last',
        '--', '/bin/echo', '', 'space newline\n', 'nonascii-\u00e9', b'raw-\xff']
    command = views._BwrapCommand(['/explicit/bwrap', *arguments], command_index=7)
    with views.popen_arguments(command) as (argv, descriptors):
        descriptor, = descriptors
        assert argv == ['/explicit/bwrap', '--args', str(descriptor), *arguments[6:]]
        assert not os.get_inheritable(descriptor)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            assert stream.read().split(b'\0') == [*[os.fsencode(item) for item in arguments[:6]], b'']
    _closed(descriptor)
    assert command == ['/explicit/bwrap', *arguments]


@pytest.mark.parametrize('position', [0, 1])
def test_owned_arguments_reject_nul_before_launch(position):
    command = views._BwrapCommand(['/explicit/bwrap', '--die-with-parent'], command_index=2)
    command[position] += '\0injected'
    with pytest.raises(ValueError, match='NUL'):
        with views.popen_arguments(command):
            pytest.fail('An invalid bwrap argument reached launch')


def test_unowned_custom_or_registered_launcher_is_unchanged():
    # Even this executable spelling grants no control-plane ownership marker.
    command = ['/custom/bwrap', '--opaque-protocol', 'retained unchanged']
    with views.popen_arguments(command) as (argv, descriptors):
        assert argv is command
        assert descriptors == ()


@pytest.mark.process_e2e
def test_parent_descriptor_closes_when_popen_fails():
    command = views._BwrapCommand(['/nonexistent/scid-bwrap-fixture', '--die-with-parent'], command_index=2)
    with pytest.raises(FileNotFoundError):
        with views.popen_arguments(command) as (argv, descriptors):
            descriptor, = descriptors
            subprocess.Popen(argv, pass_fds=descriptors)
    _closed(descriptor)


def test_actual_local_execute_passes_owned_descriptor_at_popen(tmp_path, monkeypatch):
    from scidiscovery.research_loop import (experiment_client, experiments as e,
        local_experiments, runtime, worker_execution_bridge)
    subject = object.__new__(local_experiments.LocalExecution)
    subject.config = dict(max_seconds=10)
    subject.frozen_interface = None
    subject.records = SimpleNamespace(root=tmp_path, directory=lambda _: tmp_path)
    subject.command = lambda _: ['/opaque/scientific-command']
    monkeypatch.setattr(e, 'verify_scientific_inputs', lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime, '_subreaper', lambda: None)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args, **kwargs: None)
    command = views._BwrapCommand(['/explicit/bwrap', '--bind', '/', '/', '--', '/bin/true'], command_index=4)
    monkeypatch.setattr(worker_execution_bridge, 'local_process_launch',
        lambda config, accepted, argv, cwd, **kwargs: (command, cwd, None))
    captured = []

    def fail_spawn(argv, **kwargs):
        descriptor, = kwargs['pass_fds']
        assert argv == ['/explicit/bwrap', '--args', str(descriptor), '--', '/bin/true']
        assert os.read(descriptor, 1024).split(b'\0') == [
            b'--bind', b'/', b'/', b'']
        captured.append(descriptor)
        raise OSError('bounded spawn failure fixture')

    monkeypatch.setattr(local_experiments.subprocess, 'Popen', fail_spawn)
    with pytest.raises(OSError, match='bounded spawn failure fixture'):
        subject._execute('fixture', dict(cwd=str(tmp_path)))
    assert len(captured) == 1
    _closed(captured[0])


@pytest.mark.process_e2e
def test_actual_worker_native_popen_passes_owned_descriptor(tmp_path, monkeypatch):
    from scidiscovery.research_loop import native
    from scidiscovery.research_loop.experiments import UnsupportedExecution
    from test_native_cli import test_native_stdin_uses_the_execution_entrypoint_for_its_scientific_view
    try:
        views.ensure_capability()
    except UnsupportedExecution as error:
        pytest.skip(str(error))
    original_popen = native.subprocess.Popen
    captured = []

    def observe_spawn(argv, *args, **kwargs):
        if (kwargs.get('env') or {}).get('THIN_HWA_ROLE') == 'W':
            descriptor, = kwargs['pass_fds']
            assert argv[1:4] == ['--args', str(descriptor), '--']
            assert argv[4] == sys.executable
            assert os.pread(descriptor, 64, 0).startswith(b'--unshare-user\0')
            captured.append(descriptor)
        return original_popen(argv, *args, **kwargs)

    monkeypatch.setattr(native.subprocess, 'Popen', observe_spawn)
    # Reuse the existing finite synthetic native stdin fixture. It runs Python,
    # captures the Worker prompt and never submits a model or experiment.
    test_native_stdin_uses_the_execution_entrypoint_for_its_scientific_view(tmp_path, 1, 'W', 'local')
    assert len(captured) == 1
    _closed(captured[0])


@pytest.mark.process_e2e
@pytest.mark.parametrize('large', [False, True])
def test_actual_bwrap_consumes_fd_and_child_receives_only_final_environment(large):
    program = shutil.which('bwrap')
    if program is None:
        pytest.skip('bwrap is unavailable')
    arguments = ['--unshare-user', '--die-with-parent', '--bind', '/', '/', '--clearenv']
    if large:
        argument_limit = os.sysconf('SC_ARG_MAX')
        if not 0 < argument_limit <= 4 * 1024 * 1024:
            pytest.skip('This bounded fixture supports ARG_MAX up to 4 MiB')
        repeated = ['--setenv', 'SCID_ARGS_PADDING', 'x' * 1024]
        count = argument_limit // sum(len(item) + 1 for item in repeated) + 1
        arguments.extend(repeated * count)
        assert sum(len(os.fsencode(item)) + 1 for item in arguments) > argument_limit
    payload = '''import os
assert os.environ['SCID_ARGS_PADDING'] == 'small'
# bwrap supplies PWD; Python may normalize the C locale via LC_CTYPE.
unexpected = set(os.environ) - {'SCID_ARGS_PADDING', 'PWD', 'LC_CTYPE'}
assert not unexpected, sorted(unexpected)
for name in os.listdir('/proc/self/fd'):
 if int(name) > 2:
  try: os.fstat(int(name))
  except OSError: pass
  else: raise AssertionError('bwrap argument FD leaked')
'''
    arguments.extend(['--setenv', 'SCID_ARGS_PADDING', 'small'])
    command = views._BwrapCommand([program, *arguments, '--', sys.executable, '-I', '-c', payload],
        command_index=len(arguments) + 1)
    with views.popen_arguments(command) as (argv, descriptors):
        descriptor, = descriptors
        completed = subprocess.run(argv, pass_fds=descriptors, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    _closed(descriptor)
    assert completed.returncode == 0, completed.stderr.decode(errors='replace')
