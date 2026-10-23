"""Exact host snapshot recovery with real killed writers; no model or external service."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import traceback

import pytest

from execution_fixtures import execution_registry, remote_registry, DifferentExternalIdentity
from test_nonlocal_execution_observation import case, run
from test_history_handoff import finish
from test_native_execution_inputs import chain, late_call as original_late_call, _approve, _resume, _reviewed_execution
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (budget, cli, contracts as c, execution,
    execution_resume, experiments as e, fork_sources, instance_archive as archive,
    preparation_recovery as recovery, qualification as q)


@pytest.fixture
def late_call(chain, capsys, monkeypatch):
    # Retain the real original exception when the reused setup fails before our
    # recovery subject exists. Public CLI sanitization and return codes are kept.
    diagnostic = e._diagnostic
    def traced(error, *args, **kwargs):
        traceback.print_exception(type(error), error, error.__traceback__)
        return diagnostic(error, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(e, '_diagnostic', traced)
        return original_late_call.__wrapped__(chain, capsys, monkeypatch)


# These wrappers observe actual production writes/admission. They never supply a
# successful adapter, authorization, accepted record or recovery result.
CRASH_WRITER = r'''
import json, os, pathlib, signal, sys
from execution_fixtures import DifferentExternalIdentity
from scidiscovery.research_loop import breadth, contracts as c, experiments as e, preparation_recovery as recovery
config=c.read(pathlib.Path(sys.argv[1])); payload=pathlib.Path(sys.argv[2]); boundary=sys.argv[3]
counter=pathlib.Path(sys.argv[4])
def counted(name, original):
    def invoke(*args, **kwargs):
        with counter.open('a') as stream:
            stream.write(name+'\n');stream.flush();os.fsync(stream.fileno())
        return original(*args, **kwargs)
    return invoke
for name in ('prepare','dispatch_lease','submit'):
    setattr(DifferentExternalIdentity,name,counted(name,getattr(DifferentExternalIdentity,name)))
breadth.admit=counted('admit',breadth.admit)
def stop(path):
    print(json.dumps(dict(ready=str(path))),flush=True)
    while True:signal.pause()
if boundary=='binding':
    original=recovery.bind_snapshot
    def bind(*args,**kwargs):
        ref=original(*args,**kwargs);stop(ref['path']);return ref
    recovery.bind_snapshot=bind
elif boundary=='partial':
    original=c._bind_original
    def copy(*args,**kwargs):
        result=original(*args,**kwargs);stop(args[1]);return result
    c._bind_original=copy
elif boundary=='accepted':
    original=e.write
    def write(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if pathlib.Path(path).name=='accepted.json':stop(path)
        return result
    e.write=write
e.dispatch(config,payload,new=True,mechanism='P')
raise AssertionError('writer passed its durable stop')
'''


@contextmanager
def _child(source, *arguments):
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(Path(__file__).parent) + os.pathsep + environment.get('PYTHONPATH', '')
    process = subprocess.Popen([sys.executable, '-c', source, *map(str, arguments)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        readable, _, _ = select.select([process.stdout], [], [], 15)
        if not readable:
            pytest.fail('Host fixture did not reach its exact durable boundary')
        line = process.stdout.readline()
        if not line:
            pytest.fail('Host fixture exited before ready: ' + process.stderr.read())
        yield process, json.loads(line)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def _payload(root):
    path = c.workspace(root, 2) / 'work/opaque-package'
    path.write_bytes(b'opaque contract fixture; never executed as source\n')
    return path


def _killed(root, config, boundary):
    payload = _payload(root)
    counter = root.parent / 'host-effect-counters'
    original = execution.origin(root, 2) / 'experiment-config.json'
    with _child(CRASH_WRITER, original, payload, boundary, counter) as (process, ready):
        durable = Path(ready['ready'])
        assert durable.is_file()
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
    rows = recovery.retained_preparations(root, Path(config['record_dir']))
    assert len(rows) == 1 and rows[0]['resolved'] is False
    row = rows[0]
    assert row['status'] == 'unknown'
    assert Path(row['binding']['path']).is_file()
    assert not (Path(config['record_dir']) / 'calls' / row['call_id']).exists()
    assert counter.read_text().splitlines() == (['prepare', 'admit'] if boundary == 'accepted' else ['prepare'])
    return row, counter


def _maintain(root, row, action, **changes):
    options = dict(action=action, max_seconds=10, authorization_note='Exact local fixture maintenance')
    options.update(changes)
    return recovery.maintain(root, 2, row['call_id'], row['snapshot_token'], **options)


def _pending_binding(root, row):
    return dict(row['binding'], path=Path(row['binding']['path']).relative_to(root).as_posix())


def _cli(root, row, action):
    return cli.main(['execution-preparation-maintain', '--root', str(root), '--step', '2',
        '--call-id', row['call_id'], '--snapshot-token', row['snapshot_token'], '--action', action,
        '--max-seconds', '10', '--authorization-note', 'Exact original snapshot only'])


def _no_replay(monkeypatch):
    from scidiscovery.research_loop import breadth
    def forbidden(*args, **kwargs):
        pytest.fail('Recovery entered a new prepare/admission/dispatch/submit')
    for name in ('prepare', 'dispatch_lease', 'submit'):
        monkeypatch.setattr(DifferentExternalIdentity, name, forbidden)
    monkeypatch.setattr(breadth, 'admit', forbidden)


@pytest.mark.process_e2e
def test_durable_accepted_cli_only_publishes_original_inode(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, counter = _killed(root, config, 'accepted')
    snapshot = Path(row['snapshot'])
    identity = snapshot.stat().st_dev, snapshot.stat().st_ino
    accepted = (snapshot / 'accepted.json').read_bytes()
    frozen_source = (snapshot / 'source.py').read_bytes()
    originals = {path: path.read_bytes() for path in [root / 'settings.json', root / 'jobs/002.json',
        execution.origin(root, 2) / 'native-intent.json', execution.origin(root, 2) / 'experiment-config.json',
        Path(row['binding']['path']), *sorted((root / 'control-ledger').glob('*.json'))]}
    _no_replay(monkeypatch)
    _payload(root).write_bytes(b'Authoring bytes changed after the accepted snapshot was frozen\n')
    assert _cli(root, row, 'inspect') == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'accepted_unpublished'
    with pytest.raises(recovery.PreparationError):
        _maintain(root, row, 'discard-partial')
    assert _cli(root, row, 'publish-accepted') == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'accepted_published' and result['external_preparation_state'] == 'unknown'
    canonical = e.Records(config).directory(row['call_id'])
    assert (canonical.stat().st_dev, canonical.stat().st_ino) == identity
    assert (canonical / 'accepted.json').read_bytes() == accepted and not snapshot.exists()
    assert (canonical / 'source.py').read_bytes() == frozen_source
    assert recovery.retained_preparations(root, Path(config['record_dir']))[0]['resolved'] is True
    assert _maintain(root, row, 'publish-accepted')['status'] == 'already_published'
    assert e.Records(config).accepted(row['call_id'])['host_preparation'] == row['binding']
    assert counter.read_text().splitlines() == ['prepare', 'admit']
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert not (canonical / 'submission.json').exists()


@pytest.mark.process_e2e
def test_partial_discard_retains_unknown_and_blocks_prepare_and_pending(tmp_path, monkeypatch, remote_registry, capsys):
    write = c.write
    with monkeypatch.context() as patch:
        def no_native(path, value):
            return write(path, dict(value, native_started=False) if Path(path).name == 'native-outcome.json' else value)
        patch.setattr(c, 'write', no_native)
        root, job, config, _ = case(tmp_path, monkeypatch)
    row, counter = _killed(root, config, 'partial')
    assert (Path(row['snapshot']) / 'source.py').is_file()
    binding_bytes = Path(row['binding']['path']).read_bytes()
    _no_replay(monkeypatch)
    assert _cli(root, row, 'discard-partial') == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'partial_discarded' and result['external_preparation_state'] == 'unknown'
    assert not Path(row['snapshot']).exists()
    assert Path(row['binding']['path']).read_bytes() == binding_bytes
    assert _maintain(root, row, 'discard-partial')['status'] == 'partial_discarded'
    rows = recovery.retained_preparations(root, Path(config['record_dir']))
    assert len(rows) == 1 and rows[0]['resolved'] is False
    result = e.dispatch(config, _payload(root), new=True, mechanism='P')
    assert result['status'] == 'unknown' and result['call_id'] == row['call_id']
    descriptor = LocalFileDescriptor(**c.read(Path(row['binding']['path']))['prepared'])
    with pytest.raises((e.ConcurrentExecution, e.IntegrityError, recovery.PreparationError)):
        e.Records(config).accept(descriptor, dict(parent=dict(candidate_id=None)), config['execution_interface'], mechanism='P')
    assert counter.read_text().splitlines() == ['prepare']
    # There is deliberately no allocation, authority contract, accepted call or
    # original native cleanup proof; none may turn the retained binding into None.
    assert not job.get('execution_authority')
    ref = execution_resume.capture_pending(root, job)
    assert ref is not None
    pending = q._read(root, ref)
    assert pending['contract'] is None and pending['complete'] is False
    assert pending['attempts'] == pending['allocations'] == pending['calls_namespace'] == []
    assert pending['host_preparations'] == [_pending_binding(root, row)]
    seal = dict(job_sha256=c.digest(root / 'jobs/002.json'), pending_executions=ref)
    assert execution_resume.verify_pending_set(root, seal)['complete'] is False
    with pytest.raises(ValueError):
        execution_resume.verify_pending_set(root, seal, unprepared=True)
    with pytest.raises(ValueError):
        execution_resume.verify_pending_set(root, seal, _pending_binding(root, row))
    before = (root / ref['path']).read_bytes()
    assert execution_resume.capture_pending(root, job) == ref
    assert (root / ref['path']).read_bytes() == before
    blockers = fork_sources._parent_state(root)
    assert any('preparation' in row['code'] for row in blockers)
    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', no_native)
        finish(root, 2, 'W', [1], preserve_native_intent=True,
            mechanism_findings=[dict(mechanism=label, basis='blocked',
                finding='Original host preparation remains unknown; no scientific observation was claimed.') for label in ('P', 'Q')])
    assert (root / ref['path']).read_bytes() == before
    from scidiscovery.research_loop import execution_recovery
    sealed = c.read(execution.origin(root, 2) / 'result-identity.json')
    sources = [(job, None, sealed, None)]
    retained = execution_recovery.sealed_pending_rows(root, sources)[(2, row['call_id'])]
    assert retained == dict(row=None, phase='original_prepare_unknown', native_state=None)
    assert retained.get('grant_dispatch') is not True
    assert execution_recovery.pending(root) and execution_recovery.pending(root, unknown_only=True)
    projected = execution_recovery.project_records(root, [dict(step=2, observation=None)], [], sources=sources)
    assert projected[0]['execution_resumes'] == [dict(call_id=row['call_id'], phase='original_prepare_unknown', native_state=None)]
    assert row['binding']['path'] not in json.dumps(projected) and str(root) not in json.dumps(projected)
    with pytest.raises(archive.files.ArchiveError, match='Retained original host preparation'):
        archive._quiescent(root)


@pytest.mark.process_e2e
@pytest.mark.parametrize('lock', ['node', 'owner'])
def test_real_owner_contention_is_busy_without_operation_receipts(tmp_path, monkeypatch, remote_registry, lock):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, _ = _killed(root, config, 'binding')
    source = r'''
import json,pathlib,signal,sys
from scidiscovery.research_loop import contracts as c,controller,experiments as e
root=pathlib.Path(sys.argv[1]);config=c.read(pathlib.Path(sys.argv[2]))
with (controller._writer(root) if sys.argv[3]=='owner' else e.Records(config).lease()):
    print(json.dumps(dict(ready=True)),flush=True)
    while True:signal.pause()
'''
    directory = Path(row['binding']['path']).parent
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    with _child(source, root, execution.origin(root, 2) / 'experiment-config.json', lock):
        result = _maintain(root, row, 'discard-partial')
        assert result['status'] == 'busy'
        assert Path(row['snapshot']).is_dir()
        assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


@pytest.mark.process_e2e
@pytest.mark.parametrize('damage', ['snapshot_symlink', 'source_symlink', 'replaced_inode', 'unknown_file', 'special_file', 'corrupt_accepted', 'canonical_collision'])
def test_exact_identity_and_tree_conflicts_preserve_target_and_neighbor(tmp_path, monkeypatch, remote_registry, damage):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, counter = _killed(root, config, 'accepted')
    snapshot = Path(row['snapshot'])
    neighbor = snapshot.parent / 'unrelated-original'
    neighbor.mkdir(); (neighbor / 'keep').write_bytes(b'untouched neighbor')
    accepted = (snapshot / 'accepted.json').read_bytes()
    if damage == 'snapshot_symlink':
        snapshot.rename(snapshot.with_name(snapshot.name + '-original'))
        snapshot.symlink_to(neighbor, target_is_directory=True)
    elif damage == 'source_symlink':
        (snapshot / 'source.py').unlink(); (snapshot / 'source.py').symlink_to(neighbor / 'keep')
    elif damage == 'replaced_inode':
        snapshot.rename(snapshot.with_name(snapshot.name + '-original')); snapshot.mkdir()
        (snapshot / 'accepted.json').write_bytes(accepted)
    elif damage == 'unknown_file':
        (snapshot / 'extra').write_bytes(b'unknown ownership')
    elif damage == 'special_file':
        os.mkfifo(snapshot / 'fifo')
    elif damage == 'corrupt_accepted':
        (snapshot / 'accepted.json').chmod(0o600); (snapshot / 'accepted.json').write_bytes(b'{broken')
    else:
        canonical = snapshot.parent / 'calls' / row['call_id']
        canonical.mkdir(parents=True); (canonical / 'accepted.json').write_bytes(accepted)
    before = {path.name: path.read_bytes() for path in Path(row['binding']['path']).parent.iterdir()}
    for action in ('inspect', 'discard-partial', 'publish-accepted'):
        with pytest.raises((ValueError, archive.files.ArchiveError)):
            _maintain(root, row, action)
    assert (neighbor / 'keep').read_bytes() == b'untouched neighbor'
    assert {path.name: path.read_bytes() for path in Path(row['binding']['path']).parent.iterdir()} == before
    assert counter.read_text().splitlines() == ['prepare', 'admit']


@pytest.mark.process_e2e
def test_cli_rejects_symlink_root_and_exact_identity_arguments(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, _ = _killed(root, config, 'binding')
    alias = tmp_path / 'alias'; alias.symlink_to(root, target_is_directory=True)
    assert _cli(alias, row, 'discard-partial') == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'error'
    for bad in ('../escape', '.', '..', 'wrong-original'):
        with pytest.raises((ValueError, OSError)):
            recovery.maintain(root, 2, bad, row['snapshot_token'], action='discard-partial', max_seconds=10, authorization_note='Exact identity negative control')
        with pytest.raises((ValueError, OSError)):
            recovery.maintain(root, 2, row['call_id'], bad, action='discard-partial', max_seconds=10, authorization_note='Exact identity negative control')
    assert Path(row['snapshot']).is_dir()


def test_no_authority_without_binding_keeps_legacy_none(tmp_path, monkeypatch, remote_registry):
    root, job, _, _ = case(tmp_path, monkeypatch)
    assert not job.get('execution_authority')
    assert execution_resume.capture_pending(root, job) is None


@pytest.mark.process_e2e
@pytest.mark.parametrize('boundary', ['publish_after_rename', 'discard_after_remove'])
def test_exact_operation_retry_after_mutation_before_outcome(tmp_path, monkeypatch, remote_registry, boundary):
    from scidiscovery.research_loop import instance_delete
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, counter = _killed(root, config, 'accepted' if boundary.startswith('publish') else 'binding')
    snapshot = Path(row['snapshot']); identity = snapshot.stat().st_dev, snapshot.stat().st_ino
    binding_bytes = Path(row['binding']['path']).read_bytes()
    _no_replay(monkeypatch)
    action = 'publish-accepted' if boundary.startswith('publish') else 'discard-partial'
    with monkeypatch.context() as patch:
        module, name = (archive, '_publish') if boundary.startswith('publish') else (instance_delete, '_remove_tree')
        original = getattr(module, name)
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Exact mutation completed; outcome not written')
        patch.setattr(module, name, interrupted)
        with pytest.raises(RuntimeError, match='outcome not written'):
            _maintain(root, row, action)
    assert not snapshot.exists()
    result = _maintain(root, row, action)
    assert result['status'] == ('already_published' if boundary.startswith('publish') else 'partial_discarded')
    assert Path(row['binding']['path']).read_bytes() == binding_bytes
    if boundary.startswith('publish'):
        canonical = e.Records(config).directory(row['call_id'])
        assert (canonical.stat().st_dev, canonical.stat().st_ino) == identity
    else:
        assert recovery.retained_preparations(root, Path(config['record_dir']))[0]['resolved'] is False
    assert counter.read_text().splitlines() == (['prepare', 'admit'] if boundary.startswith('publish') else ['prepare'])


@pytest.mark.process_e2e
def test_discard_retry_refuses_replacement_directory_after_original_was_removed(tmp_path, monkeypatch, remote_registry):
    from scidiscovery.research_loop import instance_delete
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, _ = _killed(root, config, 'binding')
    snapshot = Path(row['snapshot'])
    with monkeypatch.context() as patch:
        original = instance_delete._remove_tree
        def interrupted(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Original directory removed')
        patch.setattr(instance_delete, '_remove_tree', interrupted)
        with pytest.raises(RuntimeError, match='Original directory removed'):
            _maintain(root, row, 'discard-partial')
    snapshot.mkdir(); protected = snapshot / 'new-owner'; protected.write_bytes(b'new inode must survive')
    with pytest.raises(recovery.PreparationError):
        _maintain(root, row, 'discard-partial')
    assert protected.read_bytes() == b'new inode must survive'


def test_ordinary_partial_cleanup_still_blocks_new_preparation(tmp_path, monkeypatch, remote_registry):
    root, job, config, _ = case(tmp_path, monkeypatch)
    original = c._bind_original
    with monkeypatch.context() as patch:
        def fail_after_copy(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('Ordinary writer failed after its first real copy')
        patch.setattr(c, '_bind_original', fail_after_copy)
        with pytest.raises(RuntimeError, match='first real copy'):
            e.dispatch(config, _payload(root), new=True, mechanism='P')
    rows = recovery.retained_preparations(root, Path(config['record_dir']))
    assert len(rows) == 1 and rows[0]['resolved'] is False
    assert not Path(rows[0]['snapshot']).exists()
    _no_replay(monkeypatch)
    result = e.dispatch(config, _payload(root), new=True, mechanism='P')
    assert result['status'] == 'unknown' and result['call_id'] == rows[0]['call_id']
    ref = execution_resume.capture_pending(root, job)
    assert q._read(root, ref)['host_preparations'] == [_pending_binding(root, rows[0])]


@pytest.mark.process_e2e
def test_resolved_history_survives_enrollment_and_cold_restore_without_package(tmp_path, monkeypatch, remote_registry):
    # The reused case declares this exact launcher name. Make its dependency
    # real before prepare freezes it; scientific native work is never launched.
    launcher = tmp_path / 'never-launched'
    launcher.write_text('#!' + sys.executable + '\nraise SystemExit("Native fixture must never launch")\n')
    launcher.chmod(0o700)
    monkeypatch.setenv('PATH', str(tmp_path) + os.pathsep + os.environ.get('PATH', ''))
    # Record the explicit synthetic no-native fact before each thin fixture seal,
    # rather than changing a frozen outcome to make archive admission pass.
    write = c.write
    def no_native(path, value):
        if Path(path).name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)
    monkeypatch.setattr(c, 'write', no_native)
    root, _, config, exp_cli = case(tmp_path, monkeypatch)
    calls = [run(root, exp_cli, label)['call_id'] for label in ('P', 'Q')]
    finish(root, 2, 'W', [1], preserve_native_intent=True,
        mechanism_findings=[dict(mechanism=label, basis='observed', finding='Synthetic local terminal contract only.') for label in ('P', 'Q')])
    rows = recovery.retained_preparations(root, Path(config['record_dir']))
    assert {row['call_id'] for row in rows} == set(calls) and all(row['resolved'] for row in rows)
    bindings = {Path(row['binding']['path']): Path(row['binding']['path']).read_bytes() for row in rows}
    archive._quiescent(root)
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Explicit inactive fixture transfer boundary')
    anchor = tmp_path / 'anchor'; anchor.mkdir()
    archive.checkpoint_enroll(root, anchor)
    archive._quiescent(root)
    assert all(row['resolved'] for row in recovery.retained_preparations(root, Path(config['record_dir'])))
    # Historical enrollment proves provenance only; it cannot authorize maintenance
    # against the old owner=None generation, even for an already published inode.
    with pytest.raises(recovery.PreparationError):
        _maintain(root, rows[0], 'inspect')
    package = tmp_path / 'checkpoint'
    archive.checkpoint_export(root, package)
    root.rename(tmp_path / 'source-generation')
    archive.checkpoint_restore(root, package)
    package.rename(tmp_path / 'external-package-unavailable')
    assert all(path.read_bytes() == raw for path, raw in bindings.items())
    assert all(row['resolved'] for row in recovery.retained_preparations(root, Path(config['record_dir'])))
    archive._quiescent(root)
    assert archive.archive_export(root, tmp_path / 'restored-archive')['status'] == 'archive_exported'
    with pytest.raises(recovery.PreparationError):
        _maintain(root, rows[0], 'inspect')


@pytest.mark.process_e2e
def test_published_same_generation_replacement_inode_is_not_original_publication(tmp_path, monkeypatch, remote_registry):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, _ = _killed(root, config, 'accepted')
    _maintain(root, row, 'publish-accepted')
    canonical = e.Records(config).directory(row['call_id'])
    # A dangling name at the original temp path is still a conflicting live
    # namespace entry, even though Path.exists() would report False.
    snapshot = Path(row['snapshot']); snapshot.symlink_to(tmp_path / 'missing-snapshot')
    with pytest.raises((ValueError, archive.files.ArchiveError)):
        recovery.retained_preparations(root, Path(config['record_dir']))
    with pytest.raises((ValueError, archive.files.ArchiveError)):
        _maintain(root, row, 'publish-accepted')
    snapshot.unlink()
    canonical.rename(canonical.with_name(canonical.name + '-retained-original'))
    canonical.mkdir()
    original = canonical.with_name(canonical.name + '-retained-original')
    for name in ('source.py', 'accepted.json'):
        (canonical / name).write_bytes((original / name).read_bytes())
    with pytest.raises(recovery.PreparationError):
        recovery.retained_preparations(root, Path(config['record_dir']))
    with pytest.raises(recovery.PreparationError):
        _maintain(root, row, 'publish-accepted')


def _killed_authorized_resume(root, backend, call, config, boundary, operation=None):
    """Fork retains the reused fixture's declared local transport/native DI only."""
    from scidiscovery.research_loop import breadth
    from tcad_artifact import managed_execution
    read_fd, write_fd = os.pipe()
    counter = root.parent / 'authority-effect-counters'
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        try:
            def counted(name, original):
                def invoke(*args, **kwargs):
                    with counter.open('a') as stream:
                        stream.write(name + '\n'); stream.flush(); os.fsync(stream.fileno())
                    return original(*args, **kwargs)
                return invoke
            backend.prepare = counted('prepare', backend.prepare)
            backend.submit = counted('submit', backend.submit)
            managed_execution.TCADManagedExecution.dispatch_lease = counted('dispatch_lease', managed_execution.TCADManagedExecution.dispatch_lease)
            breadth.admit = counted('admit', breadth.admit)
            def stop(path):
                os.write(write_fd, (json.dumps(dict(ready=str(path))) + '\n').encode())
                while True:
                    signal.pause()
            if boundary == 'binding':
                original = recovery.bind_snapshot
                def bind(*args, **kwargs):
                    ref = original(*args, **kwargs); stop(ref['path']); return ref
                recovery.bind_snapshot = bind
            else:
                original = e.write
                def write(path, *args, **kwargs):
                    result = original(path, *args, **kwargs)
                    if Path(path).name == 'accepted.json':
                        stop(path)
                    return result
                e.write = write
            result = operation() if operation is not None else _resume(root, call)
            os.write(write_fd, (json.dumps(dict(error=result)) + '\n').encode())
        except BaseException as error:
            os.write(write_fd, (json.dumps(dict(error=repr(error))) + '\n').encode())
        finally:
            os._exit(1)
    os.close(write_fd)
    try:
        readable, _, _ = select.select([read_fd], [], [], 20)
        assert readable, 'Authorized original did not reach the exact durable boundary'
        stream = os.fdopen(read_fd)
        read_fd = None
        with stream:
            ready = json.loads(stream.readline())
        assert 'ready' in ready, ready
        assert Path(ready['ready']).is_file()
    finally:
        if read_fd is not None:
            os.close(read_fd)
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        waited, status = os.waitpid(pid, 0)
    assert waited == pid and os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    rows = recovery.retained_preparations(root, Path(config['record_dir']))
    assert len(rows) == 1 and rows[0]['call_id'] == call and rows[0]['resolved'] is False
    expected = ['prepare', 'admit'] if boundary == 'accepted' else ['prepare']
    # Ordinary run checks breadth while resolving its call identity; sealed
    # resume uses the already frozen allocation and starts at provider prepare.
    if operation is not None:
        expected = ['admit', *expected]
    assert counter.read_text().splitlines() == expected
    return rows[0], counter


@pytest.mark.process_e2e
@pytest.mark.parametrize('boundary', ['binding', 'accepted'])
def test_authorized_original_recovery_preserves_permission_reservation_budget_and_seal(late_call, monkeypatch, boundary):
    from scidiscovery.research_loop import execution_authority as authority
    from tcad_artifact import managed_execution
    root, backend, config, call, request, seal_bytes = late_call
    _approve(root, request)
    row, counter = _killed_authorized_resume(root, backend, call, config, boundary)
    binding = c.read(Path(row['binding']['path']))
    refs = binding['authority_originals']
    assert refs['authority'] and refs['reservation'] and refs['prepare_intent']
    originals = {root / item['path'] if not Path(item['path']).is_absolute() else Path(item['path']):
        (root / item['path'] if not Path(item['path']).is_absolute() else Path(item['path'])).read_bytes()
        for item in refs.values()}
    originals.update({path: path.read_bytes() for path in (root / 'control-ledger').glob('*.json')})
    pending_path = execution.origin(root, 6) / 'pending-execution-set.json'
    originals[pending_path] = pending_path.read_bytes()
    accounting = budget.execution_accounting(root)
    reservation = budget.execution_reservation(root, refs['reservation'])
    assert reservation['released'] is False and reservation['bound'] is None
    def forbidden(*args, **kwargs):
        pytest.fail('Snapshot maintenance replayed preparation, admission or resource authority')
    from scidiscovery.research_loop import breadth
    for target, name in ((backend, 'prepare'), (backend, 'submit'), (breadth, 'admit'),
            (managed_execution.TCADManagedExecution, 'dispatch_lease'), (budget, 'reserve_execution'),
            (budget, 'bind_execution'), (budget, 'release_execution'), (authority, '_remaining')):
        monkeypatch.setattr(target, name, forbidden)
    if boundary == 'accepted':
        inspected = recovery.maintain(root, 6, call, row['snapshot_token'], action='inspect',
            max_seconds=20, authorization_note='Inspect the exact accepted original before publication')
        assert inspected['status'] == 'accepted_unpublished' and inspected['external_preparation_state'] == 'unknown'
        assert all(path.read_bytes() == raw for path, raw in originals.items())
    action = 'publish-accepted' if boundary == 'accepted' else 'discard-partial'
    result = recovery.maintain(root, 6, call, row['snapshot_token'], action=action,
        max_seconds=20, authorization_note='One original authorized host snapshot only')
    assert result['status'] == ('accepted_published' if boundary == 'accepted' else 'partial_discarded')
    assert result['external_preparation_state'] == 'unknown'
    assert budget.execution_accounting(root) == accounting
    assert budget.execution_reservation(root, refs['reservation']) == reservation
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    assert (execution.origin(root, 6) / 'result-identity.json').read_bytes() == seal_bytes
    assert counter.read_text().splitlines() == (['prepare', 'admit'] if boundary == 'accepted' else ['prepare'])
    if boundary == 'accepted':
        accepted = e.Records(config).accepted(call)
        assert accepted['execution_reservation'] == refs['reservation']
        assert accepted['execution_authority'] == refs['authority']
        assert not (e.Records(config).directory(call) / 'submission.json').exists()
    else:
        # The old seal predates this positive binding; deleting the local partial
        # cannot make its original unprepared disposition safe to dispatch again.
        seal = c.read(execution.origin(root, 6) / 'result-identity.json')
        with pytest.raises(ValueError):
            execution_resume.verify_pending_set(root, seal, refs['allocation'], unprepared=True)
        resumed = _resume(root, call)
        assert resumed['status'] in {'unknown', 'execution_resume_unavailable'}
        assert counter.read_text().splitlines() == ['prepare']
        assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.process_e2e
def test_unsealed_authority_partial_discard_blocks_ordinary_and_direct_prepare(chain, capsys, monkeypatch):
    from tcad_artifact import managed_execution
    from scidiscovery.research_loop import breadth, execution_authority as authority
    root, backend, output, active, config, _ = _reviewed_execution(chain, capsys)
    admission = backend.execution_admission
    monkeypatch.setattr(backend, 'execution_admission', lambda *args, **kwargs:
        admission(*args, **kwargs).model_copy(update={'outcome': 'require_human_approval'}))
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    pending = json.loads(capsys.readouterr().out)
    assert pending['status'] == 'authorization_required'
    call = pending['call_id']
    requests = sorted((root / 'execution-authority/requests').glob('*/request.json'))
    assert len(requests) == 1
    _approve(root, q._ref(root, requests[0]))
    row, counter = _killed_authorized_resume(root, backend, call, config, 'binding',
        operation=lambda: e.main(['--config', str(active), 'run', '--payload', str(output)]))
    assert counter.read_text().splitlines() == ['admit', 'prepare']
    assert not (execution.origin(root, 6) / 'result-identity.json').exists()
    def forbidden(*args, **kwargs):
        pytest.fail('Retained unknown reached a second prepare/admission/submit')
    for target, name in ((backend, 'prepare'), (backend, 'submit'), (breadth, 'admit'),
            (managed_execution.TCADManagedExecution, 'dispatch_lease'), (authority, '_remaining')):
        monkeypatch.setattr(target, name, forbidden)
    result = recovery.maintain(root, 6, call, row['snapshot_token'], action='discard-partial',
        max_seconds=20, authorization_note='Original unsealed snapshot only')
    assert result['status'] == 'partial_discarded'
    assert counter.read_text().splitlines() == ['admit', 'prepare']
    assert e.main(['--config', str(active), 'run', '--payload', str(output)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unknown' and result['call_id'] == call
    descriptor = LocalFileDescriptor(**c.read(Path(row['binding']['path']))['prepared'])
    with pytest.raises(e.ConcurrentExecution):
        e.Records(config).accept(descriptor, dict(parent=dict(candidate_id=None)), config['execution_interface'])
    assert counter.read_text().splitlines() == ['admit', 'prepare']
    ref = execution_resume.capture_pending(root, c.read(root / 'jobs/006.json'))
    captured = q._read(root, ref)
    assert captured['complete'] is False
    assert captured['host_preparations'] == [_pending_binding(root, row)]
    assert next(item for item in captured['allocations'] if item['call_id'] == call)['disposition'] == 'possibly_prepared'


def test_no_authority_pending_capture_failure_does_not_seek_nonexistent_contract(tmp_path, monkeypatch, remote_registry):
    from scidiscovery.research_loop import execution_authority as authority, execution_recovery, history
    root, job, config, _ = case(tmp_path, monkeypatch)
    records = Path(config['record_dir']); records.mkdir(parents=True, exist_ok=True)
    (records / 'host-preparations').symlink_to(tmp_path / 'missing-preparation-namespace')
    with pytest.raises(archive.files.ArchiveError):
        recovery.retained_preparations(root, records)
    def forbidden(*args):
        pytest.fail('No-authority fallback sought an authority contract')
    monkeypatch.setattr(authority, 'contract', forbidden)
    ref = execution_resume.capture_pending(root, job)
    captured = q._read(root, ref)
    assert captured['contract'] is None and captured['complete'] is False
    pending_bytes = (root / ref['path']).read_bytes()
    # It is a retained inability to inspect the original namespace, not an empty
    # namespace or an invented call. The thin seal remains durable when its
    # subsequent history refresh refuses to project a clean execution state.
    with pytest.raises(history.ObserverIntegrityError, match='pending namespace was unavailable'):
        finish(root, 2, 'W', [1], preserve_native_intent=True,
            mechanism_findings=[dict(mechanism=label, basis='blocked',
                finding='Original namespace evidence is unavailable; no execution result was claimed.') for label in ('P', 'Q')])
    seal_path = execution.origin(root, 2) / 'result-identity.json'
    assert seal_path.is_file() and (root / ref['path']).read_bytes() == pending_bytes
    sealed = c.read(seal_path)
    assert sealed['pending_executions'] == ref
    with pytest.raises(history.ObserverIntegrityError, match='pending namespace was unavailable'):
        execution_recovery.sealed_pending_rows(root, [(job, None, sealed, None)])
    with pytest.raises(history.ObserverIntegrityError, match='pending namespace was unavailable'):
        execution_recovery.pending(root)
    with pytest.raises(history.ObserverIntegrityError, match='pending namespace was unavailable'):
        budget.execution_blocked(root)


@pytest.mark.process_e2e
def test_cli_keeps_missing_accepted_call_as_integrity_error(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, exp_cli = case(tmp_path, monkeypatch)
    result = run(root, exp_cli, 'P')
    assert result['status'] == 'known_success'
    canonical = e.Records(config).directory(result['call_id'])
    retained = tmp_path / 'retained-accepted-original'; canonical.rename(retained)
    accepted = (retained / 'accepted.json').read_bytes()
    # Exercise only optional CLI result projection with an actual prior result;
    # the canonical original must still be required for an accepted outcome.
    monkeypatch.setattr(e, 'dispatch', lambda *args, **kwargs: result)
    assert e.main(['--config', str(c.workspace(root, 2) / 'work/experiment-config.json'),
        'run', '--payload', str(_payload(root))]) == 3
    assert json.loads(capsys.readouterr().out)['status'] == 'integrity_error'
    assert (retained / 'accepted.json').read_bytes() == accepted and not canonical.exists()
