"""Explicit local disposal on temporary fixtures; no model, solver or cancellation."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (budget, contracts as c, controller,
    fork_service, fork_sources, instance_archive as archive, instance_delete as deletion,
    instance_owner as owner, originals, protocol, runtime)
from test_history_handoff import finish


@pytest.fixture(autouse=True)
def private_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(owner, '_gate_directory', lambda: tmp_path / 'host-private-gates')


@pytest.fixture
def instance(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the exact declared original until explicitly deleted.\n')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [sys.executable], max_tokens=4000,
        max_seconds=60, max_steps=7, model='fixture-never-called', reasoning_effort='high',
        search_policy=None, experiment_mode='external')
    return root


def _request(root, operation='explicit-delete'):
    return dict(instance_id=c.settings(root)['goal_id'],
        settings_sha256=c.digest(root / 'settings.json'), operation_id=operation)


def _retained(root):
    return {path.relative_to(root): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
        for path in root.rglob('*') if path.is_file() and not path.is_symlink()}


def _assert_retained(root, before):
    # Existing archive admission may create owner lock files, never alter originals.
    assert {name: ((root / name).read_bytes(), stat.S_IMODE((root / name).stat().st_mode))
        for name in before} == before


def _finish(monkeypatch, root, step, role, parents, **options):
    write = c.write

    def synthetic_transport(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)

    # The declaration precedes sealing; no frozen native outcome is rewritten.
    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', synthetic_transport)
        return finish(root, step, role, parents, **options)


def _record_path(root, operation):
    key = hashlib.sha256(operation.encode()).hexdigest()
    return root.parent / '.scid-delete-operations' / (key + '.json')


@pytest.mark.parametrize('state', ['prepared', 'paused', 'completed'])
def test_explicit_delete_accepts_quiescent_lifecycle_states(instance, monkeypatch, state):
    root = instance
    if state == 'paused':
        c.write(root / 'pause.request', dict(reason='Fixture explicit pause'))
        assert controller.run(root)['status'] == 'paused'
    elif state == 'completed':
        _finish(monkeypatch, root, 1, 'H', [], action='analyze')
        hypothesis = c.validate(c.workspace(root, 1))
        c.build_job(root, 2, 'A', [1], hypothesis['route_reason'])
        _finish(monkeypatch, root, 2, 'A', [1], action='finish')
        assert c.validate(c.workspace(root, 2))['next_action'] == 'finish'
        with controller._writer(root):
            pass
        assert fork_sources._parent_state(root) == []
    private = root / 'native-homes/001/private-cache'
    private.parent.mkdir(parents=True)
    private.write_bytes(b'Exact private native cache also belongs to this instance.')
    request = _request(root)
    result = deletion.delete_instance(root, **request)
    assert result['status'] == 'instance_deleted'
    assert result['operation_id'] == request['operation_id']
    assert result['instance_id'] == request['instance_id']
    assert not root.exists()
    assert Path(result['receipt']).is_file()
    assert deletion.delete_instance(root, **request) == result


@pytest.mark.parametrize('wrong', ['instance_id', 'settings_sha256'])
def test_wrong_confirmation_preserves_exact_originals(instance, wrong):
    request = _request(instance)
    request[wrong] = 'wrong-instance' if wrong == 'instance_id' else '0' * 64
    before = _retained(instance)
    with pytest.raises((ValueError, archive.files.ArchiveError)):
        deletion.delete_instance(instance, **request)
    _assert_retained(instance, before)
    assert not (instance / owner.RETIREMENT).exists()


def test_legacy_protocol_is_rejected_without_migration(instance):
    settings = c.settings(instance)
    c.write(instance / 'settings.json', dict(settings, handoff_protocol=protocol.CURRENT - 1))
    c.write(instance / 'settings-identity.json', dict(sha256=c.digest(instance / 'settings.json')))
    request = _request(instance)
    before = _retained(instance)
    with pytest.raises(protocol.UnsupportedProtocol):
        deletion.delete_instance(instance, **request)
    _assert_retained(instance, before)


@pytest.mark.parametrize('ancestor', [False, True])
def test_root_and_ancestor_symlinks_are_rejected(instance, tmp_path, ancestor):
    request = _request(instance)
    link = tmp_path / 'alias'
    link.symlink_to(instance.parent if ancestor else instance, target_is_directory=True)
    selected = link / instance.name if ancestor else link
    before = _retained(instance)
    with pytest.raises((ValueError, archive.files.ArchiveError)):
        deletion.delete_instance(selected, **request)
    _assert_retained(instance, before)
    assert link.is_symlink()


@pytest.mark.parametrize('guard', ['controller', 'legacy_controller', 'source', 'ui', 'managed'])
def test_live_owner_guards_preserve_originals_and_budget(instance, guard):
    from scidiscovery.research_loop.experiments import Records
    from scidiscovery.research_loop.instance_ui import _host_lock
    request = _request(instance)
    contexts = dict(controller=lambda: controller._writer(instance),
        legacy_controller=lambda: controller._local_writer(instance),
        source=lambda: owner.source_lease(instance), ui=lambda: _host_lock(instance),
        managed=lambda: Records(dict(record_dir=str(instance / 'project/nodes/001/work/lineage'))).lease())
    c.write(instance / 'host-started.json', budget.clock_stamp())
    budget.transition(instance, 'paused', 'Fixture budget remains consumed on rejection')
    with contexts[guard]():
        before = _retained(instance)
        with pytest.raises((RuntimeError, ValueError, archive.files.ArchiveError)):
            deletion.delete_instance(instance, **request)
        _assert_retained(instance, before)
        assert not (instance / owner.RETIREMENT).exists()


@pytest.mark.parametrize('fault', ['pending_native', 'cleanup_unknown', 'live_process',
    'watchdog_unknown', 'managed_unknown', 'preaccept', 'delivery_cleanup_unknown'])
def test_unknown_or_live_originals_never_cancel_or_reset_budget(instance, monkeypatch, fault):
    from scidiscovery.research_loop import experiments as e, native
    root = instance
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Retain original consumption and unknown work')
    c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    binding = c.digest(root / 'jobs/001.json')
    c.write(record / 'native-intent.json', dict(task_sha256=binding))
    if fault != 'pending_native':
        c.write(record / 'native-outcome.json', dict(returncode=0,
            cleanup='cleanup_unknown' if fault == 'cleanup_unknown' else 'observed_group_cleaned',
            native_started=True))
    if fault == 'live_process':
        c.write(record / 'runner-process.json', runtime.process_identity(os.getpid(), binding))
    elif fault == 'watchdog_unknown':
        c.write(record / 'runner-cleanup.json', dict(status='cleanup_unknown'))
    elif fault in {'managed_unknown', 'preaccept'}:
        records = root / 'project/nodes/001/work/lineage'
        if fault == 'preaccept':
            (records / '.prepared-original').mkdir(parents=True)
        else:
            # Retained synthetic acceptance without any terminal/submission truth.
            # No backend is constructed and no cancellation is permitted.
            call = records / 'calls' / ('0001-' + 'a' * 32)
            c.write(call / 'accepted.json', dict(call_id=call.name, scope=dict(step=1),
                interface=dict(terminal_evidence='external_submission'), fixture_only=True))
    elif fault == 'delivery_cleanup_unknown':
        c.write(root / 'host-delivery/preparation/original.json', dict(cleanup_confirmed=False))

    def forbidden(*args, **kwargs):
        pytest.fail('Deletion must use retained facts without provider IO or new execution')

    monkeypatch.setattr(native, 'execute_native', forbidden)
    monkeypatch.setattr(e, 'load', forbidden)
    request = _request(root)
    before = _retained(root)
    with pytest.raises(archive.files.ArchiveError):
        deletion.delete_instance(root, **request)
    _assert_retained(root, before)
    assert not (root / owner.RETIREMENT).exists()


def test_interior_symlink_is_unlinked_without_touching_external_original(instance, tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'sentinel'
    sentinel.write_bytes(b'External retained bytes must survive deletion.')
    (instance / 'external-link').symlink_to(outside, target_is_directory=True)
    deletion.delete_instance(instance, **_request(instance))
    assert sentinel.read_bytes() == b'External retained bytes must survive deletion.'
    assert not instance.exists()


@pytest.mark.process_e2e
def test_shipped_cli_requires_confirmation_and_retries_absent_original_root(instance):
    request = _request(instance, 'real-cli-delete')
    environment = dict(os.environ, PYTHONPATH=str(Path(deletion.__file__).resolve().parents[2]))
    command = [sys.executable, '-m', 'scidiscovery.research_loop.cli', 'instance-delete']
    options = ['--root', str(instance), '--instance-id', request['instance_id'],
        '--settings-sha256', request['settings_sha256'], '--operation-id', request['operation_id']]
    before = _retained(instance)
    for omitted in range(0, len(options), 2):
        missing = subprocess.run(command + options[:omitted] + options[omitted + 2:],
            env=environment, capture_output=True, text=True, timeout=20)
        assert missing.returncode != 0
        _assert_retained(instance, before)
    wrong = list(options)
    wrong[5] = '0' * 64
    rejected = subprocess.run(command + wrong, env=environment, capture_output=True, text=True, timeout=20)
    assert rejected.returncode != 0
    _assert_retained(instance, before)
    alias = instance.with_name('cli-symlink')
    alias.symlink_to(instance, target_is_directory=True)
    linked = list(options)
    linked[1] = str(alias)
    rejected = subprocess.run(command + linked, env=environment, capture_output=True, text=True, timeout=20)
    assert rejected.returncode != 0
    _assert_retained(instance, before)
    first = subprocess.run(command + options, env=environment, capture_output=True, text=True, timeout=20)
    assert first.returncode == 0, first.stdout + first.stderr
    receipt = json.loads(first.stdout)
    assert receipt['status'] == 'instance_deleted' and not instance.exists()
    repeated = subprocess.run(command + options, env=environment, capture_output=True, text=True, timeout=20)
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert json.loads(repeated.stdout) == receipt


@pytest.mark.parametrize('nested', [False, True])
def test_mounted_target_or_descendant_rejects_before_retirement(instance, monkeypatch, nested):
    request = _request(instance)
    point = instance / 'mounted' if nested else instance
    if nested:
        point.mkdir()
    monkeypatch.setattr(deletion, '_mount_points', lambda: [Path('/'), point])
    def forbidden(*args, **kwargs):
        pytest.fail('Mounted tree reached metadata, owner inventory or quiescence traversal')
    monkeypatch.setattr(deletion, '_verify_instance', forbidden)
    monkeypatch.setattr(archive, '_owner_locks', forbidden)
    monkeypatch.setattr(archive, '_quiescent', forbidden)
    before = _retained(instance)
    with pytest.raises(deletion.DeleteError) as error:
        deletion.delete_instance(instance, **request)
    assert error.value.code == 'delete_mount_unsafe'
    _assert_retained(instance, before)
    assert not (instance / owner.RETIREMENT).exists()


@pytest.mark.parametrize('directory_mode', [0o500, 0o555])
def test_readonly_directory_cleanup_never_changes_shared_regular_inode(instance, tmp_path, monkeypatch, directory_mode):
    directory = instance / 'private-readonly/nested'
    directory.mkdir(parents=True)
    original = directory / 'shared-original'
    original.write_bytes(b'Exact readonly shared inode survives instance disposal.\n')
    original.chmod(0o400)
    survivor = tmp_path / 'surviving-original'
    os.link(original, survivor)
    directory.chmod(directory_mode)
    directory.parent.chmod(directory_mode)
    before = survivor.stat()
    chmod = os.chmod
    fchmod = os.fchmod

    def directory_chmod(path, mode, **options):
        info = os.stat(path, dir_fd=options.get('dir_fd'), follow_symlinks=False)
        assert stat.S_ISDIR(info.st_mode), 'Disposal attempted chmod on a shared regular inode'
        return chmod(path, mode, **options)

    def directory_fchmod(fd, mode):
        assert stat.S_ISDIR(os.fstat(fd).st_mode), 'Disposal attempted fchmod on a shared regular inode'
        return fchmod(fd, mode)

    monkeypatch.setattr(os, 'chmod', directory_chmod)
    monkeypatch.setattr(os, 'fchmod', directory_fchmod)
    request = _request(instance)
    with pytest.raises(deletion.DeleteError):
        deletion.delete_instance(instance, **dict(request, settings_sha256='0' * 64))
    assert stat.S_IMODE(directory.stat().st_mode) == directory_mode
    assert stat.S_IMODE(directory.parent.stat().st_mode) == directory_mode
    assert survivor.stat().st_nlink == before.st_nlink
    deletion.delete_instance(instance, **request)
    after = survivor.stat()
    assert not instance.exists()
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert stat.S_IMODE(after.st_mode) == 0o400
    assert after.st_nlink == before.st_nlink - 1
    assert survivor.read_bytes() == b'Exact readonly shared inode survives instance disposal.\n'


def test_device_boundary_rejects_without_real_mount(instance, monkeypatch):
    child = instance / 'different-device'
    child.mkdir()
    original = Path.lstat

    def other_device(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path == child:
            fields = list(value)
            fields[2] += 1  # os.stat_result st_dev; no actual mount is created.
            return os.stat_result(fields)
        return value

    monkeypatch.setattr(deletion, '_mount_points', lambda: [])
    monkeypatch.setattr(Path, 'lstat', other_device)
    before = _retained(instance)
    with pytest.raises(deletion.DeleteError) as error:
        deletion.delete_instance(instance, **_request(instance))
    assert error.value.code == 'delete_mount_unsafe'
    _assert_retained(instance, before)
    assert not (instance / owner.RETIREMENT).exists()


@pytest.mark.parametrize('after_marker', [False, True])
def test_prepared_record_order_and_ordinary_lease_fence(instance, monkeypatch, after_marker):
    root = instance
    request = _request(root)
    before = _retained(root)
    with monkeypatch.context() as patch:
        if after_marker:
            def crash(root, tombstone):
                raise RuntimeError('fixture interruption after durable retirement marker')
            patch.setattr(deletion, '_publish_retirement', crash)
        else:
            write = deletion._write_record
            def crash(path, record):
                write(path, record)
                raise RuntimeError('fixture interruption before retirement marker')
            patch.setattr(deletion, '_write_record', crash)
        with pytest.raises(RuntimeError, match='fixture interruption'):
            deletion.delete_instance(root, **request)
    record = c.read(_record_path(root, request['operation_id']))
    assert record['phase'] == 'prepared'
    assert not Path(record['tombstone']).exists()
    assert (root / owner.RETIREMENT).exists() is after_marker
    _assert_retained(root, before)

    @owner.effect
    def ordinary_effect(root):
        pytest.fail('Ordinary effect entered a retired instance')

    if after_marker:
        with pytest.raises(archive.files.ArchiveError, match='delet'):
            with owner.lease(root):
                pytest.fail('Unenrolled lease passed the durable retirement marker')
        with pytest.raises(archive.files.ArchiveError, match='delet'):
            ordinary_effect(root)
        with pytest.raises(archive.files.ArchiveError, match='delet'):
            owner.require_owner(root)
    else:
        with owner.lease(root):
            pass
    assert deletion.delete_instance(root, **request)['status'] == 'instance_deleted'


def test_prepared_retry_rechecks_unknown_work_and_preserves_consumption(instance, monkeypatch):
    root = instance
    c.build_job(root, 1, 'H', [])
    request = _request(root)
    with monkeypatch.context() as patch:
        def crash(root, tombstone):
            raise RuntimeError('fixture marker crash')
        patch.setattr(deletion, '_publish_retirement', crash)
        with pytest.raises(RuntimeError):
            deletion.delete_instance(root, **request)
    c.write(root / 'native-evidence/001/native-intent.json',
        dict(task_sha256=c.digest(root / 'jobs/001.json')))
    before = _retained(root)
    with pytest.raises(archive.files.ArchiveError, match='outcome is absent'):
        deletion.delete_instance(root, **request)
    _assert_retained(root, before)


@pytest.mark.parametrize('entry', ['source_lease', 'fork_preview', 'fork_create'])
def test_prepared_retirement_fences_fork_sources(instance, tmp_path, monkeypatch, entry):
    root = instance
    _finish(monkeypatch, root, 1, 'H', [], action='analyze',
        files={'fork-source.bin': 'Retained source bytes cannot admit a new fork after retirement.\n'})
    c.build_job(root, 2, 'A', [1], c.validate(c.workspace(root, 1))['route_reason'])
    _finish(monkeypatch, root, 2, 'A', [1], action='finish')
    with controller._writer(root):
        pass
    assert fork_sources._parent_state(root) == []
    limits = dict(max_tokens=200, max_seconds=30, max_steps=2)
    published = tmp_path / 'published-child'
    published_preview = fork_service.preview(root, 2, published, budget=limits)
    assert published_preview['status'] == 'fork_preview_ready'
    published_options = dict(budget=limits, expected_preview_id=published_preview['preview_id'],
        operation_id='fork-before-retirement')
    receipt = fork_service.create(root, 2, published, **published_options)
    child = tmp_path / 'unpublished-child'
    preview = fork_service.preview(root, 2, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready'
    request = _request(root)

    with monkeypatch.context() as patch:
        def crash(*args):
            raise RuntimeError('fixture crash after marker before rename')
        patch.setattr(deletion, '_publish_retirement', crash)
        with pytest.raises(RuntimeError, match='fixture crash'):
            deletion.delete_instance(root, **request)
    assert (root / owner.RETIREMENT).is_file()
    assert not (root / owner.BINDING).exists(), 'Regression must exercise the unenrolled source path'
    before = _retained(root)
    cache_before = _retained(root / 'host-history/objects')
    # A published child acknowledges its original receipt without reentering source admission.
    assert fork_service.create(root, 2, published, **published_options) == receipt
    if entry == 'source_lease':
        with pytest.raises(archive.files.ArchiveError, match='delet'):
            with owner.source_lease(root, create=False):
                pytest.fail('Read-only source lease admitted a prepared deletion marker')
    elif entry == 'fork_preview':
        assert fork_service.preview(root, 2, child, budget=limits)['status'] == 'fork_preview_blocked'
    else:
        with pytest.raises(fork_service.ForkError):
            fork_service.create(root, 2, child, budget=limits,
                expected_preview_id=preview['preview_id'], operation_id='fork-after-retirement')
    assert not child.exists()
    assert _retained(root) == before
    assert _retained(root / 'host-history/objects') == cache_before
    assert deletion.delete_instance(root, **request)['status'] == 'instance_deleted'
    assert fork_service.create(root, 2, published, **published_options) == receipt
    fork_sources.validate_manifest(published, full=True)


@pytest.mark.parametrize('interruption', ['after_rename', 'partial_cleanup', 'tree_removed', 'lost_response'])
def test_original_operation_reconciles_each_irreversible_boundary(instance, monkeypatch, interruption):
    root = instance
    request = _request(root)
    path = _record_path(root, request['operation_id'])
    original_inode = owner._identity(root)
    with monkeypatch.context() as patch:
        if interruption == 'after_rename':
            publish = deletion._publish_retirement
            def crash(source, target):
                publish(source, target)
                raise RuntimeError('fixture interruption immediately after rename')
            patch.setattr(deletion, '_publish_retirement', crash)
        elif interruption in {'partial_cleanup', 'tree_removed'}:
            remove = deletion._remove_tree
            def crash(target, identity):
                if interruption == 'partial_cleanup':
                    (target / 'settings.json').unlink()
                    (target / owner.RETIREMENT).unlink()
                else:
                    remove(target, identity)
                raise RuntimeError('fixture interruption during cleanup')
            patch.setattr(deletion, '_remove_tree', crash)
        else:
            write = deletion._write_record
            def crash(target, record):
                write(target, record)
                if record['phase'] == 'deleted':
                    raise RuntimeError('fixture response lost after durable completion')
            patch.setattr(deletion, '_write_record', crash)
        with pytest.raises((RuntimeError, archive.files.ArchiveError)):
            deletion.delete_instance(root, **request)
    record = c.read(path)
    assert record['root_identity'] == original_inode
    assert not root.exists()
    tombstone = Path(record['tombstone'])
    if interruption in {'after_rename', 'partial_cleanup'}:
        assert owner._identity(tombstone) == original_inode
    else:
        assert not tombstone.exists()

    # A new object at the old pathname never becomes a pending retry's target.
    root.mkdir()
    replacement = root / 'replacement-original'
    replacement.write_bytes(b'This replacement was never confirmed for deletion.')
    result = deletion.delete_instance(root, **request)
    assert result['status'] == 'instance_deleted'
    assert replacement.read_bytes() == b'This replacement was never confirmed for deletion.'
    assert not tombstone.exists()
    assert c.read(path)['phase'] == 'deleted'
    assert deletion.delete_instance(root, **request) == result
    assert replacement.is_file()


@pytest.mark.parametrize('replacement', ['root', 'tombstone', 'parent'])
def test_pending_transaction_rejects_replaced_storage_identity(instance, tmp_path, monkeypatch, replacement):
    root = instance
    if replacement == 'parent':
        parent = tmp_path / 'target-parent'
        parent.mkdir()
        root = root.rename(parent / root.name)
    request = _request(root)
    path = _record_path(root, request['operation_id'])
    hook = '_publish_retirement' if replacement == 'root' else '_remove_tree'
    with monkeypatch.context() as patch:
        def crash(*args):
            raise RuntimeError('fixture storage identity interruption')
        patch.setattr(deletion, hook, crash)
        with pytest.raises(RuntimeError):
            deletion.delete_instance(root, **request)
    record = c.read(path)
    selected = root if replacement == 'root' else Path(record['tombstone'])
    if replacement == 'parent':
        # Move only the temporary fixture parent; preserve the recorded transaction
        # bytes in a replacement directory so its parent inode check must reject.
        moved = tmp_path / 'original-parent'
        root.parent.rename(moved)
        root.parent.mkdir()
        path.parent.mkdir(mode=0o700)
        path.write_bytes((moved / '.scid-delete-operations' / path.name).read_bytes())
        selected = root.parent / 'replacement-tree'
        selected.mkdir()
    else:
        selected.rename(tmp_path / ('original-' + replacement))
        selected.mkdir()
    sentinel = selected / 'sentinel'
    sentinel.write_bytes(b'Replacement identity must survive rejected retry.')
    with pytest.raises((ValueError, archive.files.ArchiveError)):
        deletion.delete_instance(root, **request)
    assert sentinel.read_bytes() == b'Replacement identity must survive rejected retry.'


def test_missing_root_without_original_transaction_is_not_success(instance):
    root = instance
    request = _request(root)
    root.rename(root.with_name('unconfirmed-original'))
    with pytest.raises((OSError, ValueError, archive.files.ArchiveError)):
        deletion.delete_instance(root, **request)


def test_prepared_transaction_with_both_original_locations_missing_fails_closed(instance, monkeypatch):
    root = instance
    request = _request(root)
    with monkeypatch.context() as patch:
        def crash(*args):
            raise RuntimeError('fixture before rename')
        patch.setattr(deletion, '_publish_retirement', crash)
        with pytest.raises(RuntimeError):
            deletion.delete_instance(root, **request)
    moved = root.rename(root.with_name('unconfirmed-relocated-original'))
    before = _retained(moved)
    with pytest.raises(deletion.DeleteError) as error:
        deletion.delete_instance(root, **request)
    assert error.value.code == 'delete_state_unknown'
    _assert_retained(moved, before)


def test_operation_id_conflict_never_retargets_replacement(instance):
    root = instance
    request = _request(root)
    result = deletion.delete_instance(root, **request)
    root.mkdir()
    sentinel = root / 'sentinel'
    sentinel.write_text('New object at a previously deleted pathname.')
    changed = dict(request, instance_id='different-instance')
    with pytest.raises(deletion.DeleteError) as error:
        deletion.delete_instance(root, **changed)
    assert error.value.code == 'delete_operation_conflict'
    assert sentinel.read_text() == 'New object at a previously deleted pathname.'
    assert deletion.delete_instance(root, **request) == result


def test_deleting_source_sibling_and_child_preserves_exact_shared_forks(instance, tmp_path, monkeypatch):
    payload = 'Opaque original shared through independent exact fork manifests.\n' * 128
    _finish(monkeypatch, instance, 1, 'H', [], action='analyze', files={'shared.bin': payload})
    c.build_job(instance, 2, 'A', [1], c.validate(c.workspace(instance, 1))['route_reason'])
    _finish(monkeypatch, instance, 2, 'A', [1], action='finish')
    with controller._writer(instance):
        pass
    # The mutable producer must normalize into a shared snapshot cache inode.
    (instance / 'project/nodes/001/artifacts/shared.bin').chmod(0o600)
    limits = dict(max_tokens=200, max_seconds=30, max_steps=2)

    def create(parent, child, operation):
        preview = fork_service.preview(parent, 2, child, budget=limits)
        assert preview['status'] == 'fork_preview_ready'
        fork_service.create(parent, 2, child, budget=limits,
            expected_preview_id=preview['preview_id'], operation_id=operation)

    first, second, grandchild = (tmp_path / name for name in ('first', 'second', 'grandchild'))
    create(instance, first, 'delete-link-first')
    create(instance, second, 'delete-link-second')
    _finish(monkeypatch, first, 2, 'A', [1], action='finish')
    with controller._writer(first):
        pass
    create(first, grandchild, 'delete-link-grandchild')

    def payload_paths(root):
        manifest = c.read(root / originals.FORK_DIRECTORY / 'manifest.json')
        return {(originals.identity_key(row['origin_identity']), row['path'],
            row['sha256'], row['size_bytes']): root / originals.FORK_DIRECTORY / row['storage_path']
            for row in manifest['entries'] if row['path'].endswith('/shared.bin')}

    original = instance / 'project/nodes/001/artifacts/shared.bin'
    key = (originals.identity_key(originals.local_identity(instance)),
        original.relative_to(instance).as_posix(), c.digest(original), original.stat().st_size)
    first_paths, second_paths, grandchild_paths = (payload_paths(root)
        for root in (first, second, grandchild))
    assert key in first_paths and key in second_paths and key in grandchild_paths
    # Different logical input snapshots may hold the same bytes in distinct
    # inodes. Only the exact original identity is required to share across forks.
    paths = [second_paths[key], grandchild_paths[key]]
    pinned = paths[0].stat()
    inode = pinned.st_dev, pinned.st_ino
    assert all((path.stat().st_dev, path.stat().st_ino) == inode for path in paths)
    assert (first_paths[key].stat().st_dev, first_paths[key].stat().st_ino) == inode
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o400 for path in paths)

    for number, removed in enumerate((instance, first, second), 1):
        surviving = [path for path in paths if not path.is_relative_to(removed)]
        before_links = surviving[0].stat().st_nlink
        removed_links = sum((path.stat().st_dev, path.stat().st_ino) == inode
            for path in removed.rglob('*') if path.is_file() and not path.is_symlink())
        assert removed_links > 0
        deletion.delete_instance(removed, **_request(removed, f'delete-shared-{number}'))
        for path in surviving:
            info = path.stat()
            assert (info.st_dev, info.st_ino) == inode
            assert stat.S_IMODE(info.st_mode) == 0o400
            assert info.st_nlink == before_links - removed_links
            assert path.read_text() == payload
        for branch in (second, grandchild):
            if branch.exists():
                fork_sources.validate_manifest(branch, full=True)
        paths = surviving
    deletion.delete_instance(grandchild, **_request(grandchild, 'delete-shared-final'))
    assert not grandchild.exists()


@pytest.mark.parametrize('interruption', ['before_rename', 'after_tree_removed', 'after_anchor_deleted'])
def test_enrolled_anchor_retirement_survives_crashes_and_fences_old_checkpoint(instance, tmp_path, monkeypatch, interruption):
    root = instance
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Quiescent checkpoint fixture, no scientific dispatch')
    anchors = tmp_path / 'anchors'
    anchors.mkdir()
    archive.checkpoint_enroll(root, anchors)
    package = tmp_path / 'checkpoint'
    archive.checkpoint_export(root, package)
    root.rename(tmp_path / 'previous-generation')
    assert archive.checkpoint_restore(root, package)['status'] == 'checkpoint_restored'
    binding = c.read(root / owner.BINDING)
    anchor_path = Path(binding['anchor']) / 'owner.json'
    assert c.read(anchor_path)['state'] == 'active'
    request = _request(root)

    with monkeypatch.context() as patch:
        if interruption == 'before_rename':
            def crash(*args):
                raise RuntimeError('fixture anchor pending before rename')
            patch.setattr(deletion, '_publish_retirement', crash)
        elif interruption == 'after_tree_removed':
            remove = deletion._remove_tree
            def crash(target, identity):
                remove(target, identity)
                raise RuntimeError('fixture anchor pending after cleanup')
            patch.setattr(deletion, '_remove_tree', crash)
        else:
            write = deletion._write_record
            def crash(path, record):
                if record['phase'] == 'deleted':
                    raise RuntimeError('fixture anchor retired before completed receipt')
                write(path, record)
            patch.setattr(deletion, '_write_record', crash)
        with pytest.raises(RuntimeError, match='fixture anchor'):
            deletion.delete_instance(root, **request)
    expected = 'deleted' if interruption == 'after_anchor_deleted' else 'delete_pending'
    assert c.read(anchor_path)['state'] == expected
    if root.exists():
        with pytest.raises(archive.files.ArchiveError):
            with owner.lease(root):
                pytest.fail('Enrolled deletion marker admitted an ordinary lease')
    with pytest.raises(archive.files.ArchiveError):
        archive.checkpoint_restore(root, package)
    result = deletion.delete_instance(root, **request)
    assert result['status'] == 'instance_deleted'
    assert not root.exists()
    anchor = c.read(anchor_path)
    assert anchor['state'] == 'deleted' and anchor['generation'] == binding['generation']
    assert c.read(_record_path(root, request['operation_id']))['phase'] == 'deleted'
    assert deletion.delete_instance(root, **request) == result
    with pytest.raises(archive.files.ArchiveError):
        archive.checkpoint_restore(root, package)
    assert not root.exists()
