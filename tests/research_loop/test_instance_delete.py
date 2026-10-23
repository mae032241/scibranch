"""Explicit local disposal on temporary fixtures; no model, solver or cancellation."""
from contextlib import contextmanager
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
        settings_ref=c.record_ref(root / 'settings.json'), operation_id=operation)


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


def _prepare_nested(root, tmp_path):
    public = tmp_path / 'public'
    inner = root / 'retained' / 'independent-instance'
    controller.prepare(inner, public / 'objective.md', public, [sys.executable],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture-never-called',
        reasoning_effort='high', search_policy=None, experiment_mode='external')
    return inner


@pytest.mark.parametrize('damage', ['intact', 'missing_settings', 'missing_identity',
    'corrupt_settings', 'corrupt_identity', 'missing_project', 'owner_marker_only',
    'retirement_marker_only'])
def test_delete_preserves_independently_leased_nested_instance(instance, tmp_path, damage):
    request = _request(instance)
    inner = _prepare_nested(instance, tmp_path)
    with owner.lease(inner):
        if damage in {'missing_settings', 'corrupt_settings'}:
            (inner / 'settings.json').unlink()
            if damage == 'corrupt_settings':
                (inner / 'settings.json').write_bytes(b'Damaged frozen settings')
        elif damage in {'missing_identity', 'corrupt_identity'}:
            (inner / 'settings-identity.json').unlink()
            if damage == 'corrupt_identity':
                (inner / 'settings-identity.json').write_bytes(b'Damaged frozen identity')
        elif damage == 'missing_project':
            (inner / 'project').rename(tmp_path / 'detached-inner-project')
        elif damage in {'owner_marker_only', 'retirement_marker_only'}:
            (inner / 'settings.json').unlink()
            (inner / 'settings-identity.json').unlink()
            marker = owner.BINDING if damage == 'owner_marker_only' else owner.RETIREMENT
            (inner / marker).write_bytes(b'Damaged retained owner identity')
        before = _retained(instance)
        with pytest.raises(deletion.DeleteError) as error:
            deletion.delete_instance(instance, **request)
        assert error.value.code == 'delete_nested_instance'
        _assert_retained(instance, before)
        assert inner.is_dir()
        assert not (instance / owner.RETIREMENT).exists()
        assert not _record_path(instance, request['operation_id']).exists()


@pytest.mark.parametrize('boundary', ['prepared_before_marker', 'prepared_after_marker',
    'prepared_tombstone', 'retired_tombstone'])
def test_delete_retry_preserves_new_independently_leased_nested_instance(instance, tmp_path, monkeypatch, boundary):
    request = _request(instance)
    path = _record_path(instance, request['operation_id'])
    with monkeypatch.context() as patch:
        if boundary == 'prepared_before_marker':
            write = deletion._write_record
            def crash(target, record):
                write(target, record)
                raise RuntimeError('fixture nested-instance retry interruption')
            patch.setattr(deletion, '_write_record', crash)
        elif boundary in {'prepared_after_marker', 'prepared_tombstone'}:
            publish = deletion._publish_retirement
            def crash(root, tombstone):
                if boundary == 'prepared_tombstone':
                    publish(root, tombstone)
                raise RuntimeError('fixture nested-instance retry interruption')
            patch.setattr(deletion, '_publish_retirement', crash)
        else:
            def crash(*args):
                raise RuntimeError('fixture nested-instance retry interruption')
            patch.setattr(deletion, '_remove_tree', crash)
        with pytest.raises(RuntimeError, match='fixture nested-instance retry interruption'):
            deletion.delete_instance(instance, **request)
    record = c.read(path)
    selected = Path(record['tombstone']) if 'tombstone' in boundary else instance
    if (selected / owner.RETIREMENT).exists():
        # Retained trees from earlier writers or restoration must also survive
        # retry admission; current creators reject this retirement marker.
        staged = _prepare_nested(tmp_path / 'staged', tmp_path)
        inner = selected / 'retained' / 'independent-instance'
        inner.parent.mkdir()
        staged.rename(inner)
    else:
        inner = _prepare_nested(selected, tmp_path)
    before = _retained(selected)
    with owner.lease(inner):
        with pytest.raises(deletion.DeleteError) as error:
            deletion.delete_instance(instance, **request)
        assert error.value.code == 'delete_nested_instance'
        _assert_retained(selected, before)
        assert inner.is_dir()
    retained_record = c.read(path)
    assert retained_record['request'] == record['request']
    assert retained_record['root_identity'] == record['root_identity']
    assert retained_record['phase'] != 'deleted'


def test_instance_marker_symlinks_do_not_inspect_external_instance(instance, tmp_path):
    inner = _prepare_nested(tmp_path / 'outside', tmp_path)
    links = instance / 'payload-links'
    links.mkdir()
    for name in ('settings.json', 'settings-identity.json', owner.BINDING, owner.RETIREMENT):
        (links / name).symlink_to(inner / 'settings.json')
    (links / 'project').symlink_to(inner / 'project', target_is_directory=True)
    before = _retained(inner)
    with owner.lease(inner):
        assert deletion.delete_instance(instance, **_request(instance))['status'] == 'instance_deleted'
        _assert_retained(inner, before)
    assert not instance.exists()


def test_nested_preparation_fences_outer_delete_before_settings_publication(instance, tmp_path, monkeypatch):
    from scidiscovery.research_loop import qualification
    request = _request(instance)
    freeze = qualification.freeze_sources
    observed = []

    def check_delete(root, *args, **options):
        before = _retained(instance)
        with pytest.raises(archive.files.ArchiveError):
            deletion.delete_instance(instance, **request)
        _assert_retained(instance, before)
        assert not (instance / owner.RETIREMENT).exists()
        observed.append(root)
        return freeze(root, *args, **options)

    monkeypatch.setattr(qualification, 'freeze_sources', check_delete)
    inner = _prepare_nested(instance, tmp_path)
    assert observed == [inner]
    assert c.settings(inner)['goal_id']
    with owner.lease(inner):
        with pytest.raises(deletion.DeleteError) as error:
            deletion.delete_instance(instance, **request)
        assert error.value.code == 'delete_nested_instance'


@pytest.mark.parametrize('boundary', ['original', 'tombstone', 'partial_cleanup_retry'])
def test_outer_disposal_fences_nested_preparation_before_mkdir(instance, tmp_path, monkeypatch, boundary):
    request = _request(instance)
    if boundary == 'partial_cleanup_retry':
        with monkeypatch.context() as patch:
            def crash(*args):
                raise RuntimeError('fixture retained tombstone')
            patch.setattr(deletion, '_remove_tree', crash)
            with pytest.raises(RuntimeError, match='fixture retained tombstone'):
                deletion.delete_instance(instance, **request)
        record = c.read(_record_path(instance, request['operation_id']))
        (Path(record['tombstone']) / owner.RETIREMENT).unlink()
    preflight = deletion._preflight_tree
    observed = []

    def check_prepare(root, identity):
        selected = root == instance if boundary == 'original' else root != instance
        if selected and not observed:
            before = _retained(root)
            with pytest.raises(archive.files.ArchiveError):
                _prepare_nested(root, tmp_path)
            assert not (root / 'retained').exists()
            _assert_retained(root, before)
            observed.append(root)
        return preflight(root, identity)

    monkeypatch.setattr(deletion, '_preflight_tree', check_prepare)
    assert deletion.delete_instance(instance, **request)['status'] == 'instance_deleted'
    assert len(observed) == 1
    assert not observed[0].exists()


def test_creator_rejects_retired_ancestor_before_mkdir(instance, tmp_path, monkeypatch):
    request = _request(instance)
    with monkeypatch.context() as patch:
        def crash(*args):
            raise RuntimeError('fixture prepared retirement')
        patch.setattr(deletion, '_publish_retirement', crash)
        with pytest.raises(RuntimeError, match='fixture prepared retirement'):
            deletion.delete_instance(instance, **request)
    before = _retained(instance)
    with pytest.raises(archive.files.ArchiveError, match='deletion is pending'):
        _prepare_nested(instance, tmp_path)
    assert not (instance / 'retained').exists()
    _assert_retained(instance, before)


@pytest.mark.parametrize('replacement', [False, True])
def test_creator_does_not_recreate_ancestor_retired_before_gate_acquisition(instance, tmp_path, monkeypatch, replacement):
    request = _request(instance)
    host_gate = owner._host_gate
    observed = []

    @contextmanager
    def retire_before_gate(root, *, exclusive, **options):
        if root == instance and not exclusive and not observed:
            observed.append(deletion.delete_instance(instance, **request))
            if replacement:
                instance.mkdir()
                (instance / 'sentinel').write_bytes(b'Replacement directory must survive failed creation.')
        with host_gate(root, exclusive=exclusive, **options) as value:
            yield value

    monkeypatch.setattr(owner, '_host_gate', retire_before_gate)
    with pytest.raises(archive.files.ArchiveError):
        _prepare_nested(instance, tmp_path)
    assert len(observed) == 1
    assert observed[0]['status'] == 'instance_deleted'
    assert not (instance / 'retained').exists()
    if replacement:
        assert (instance / 'sentinel').read_bytes() == b'Replacement directory must survive failed creation.'
    else:
        assert not instance.exists()


@pytest.mark.parametrize('first', ['fork', 'delete'])
def test_fork_creation_and_outer_disposal_are_mutually_fenced(instance, tmp_path, monkeypatch, first):
    source = _prepare_nested(tmp_path / 'source', tmp_path)
    _finish(monkeypatch, source, 1, 'H', [], action='analyze')
    c.build_job(source, 2, 'A', [1], c.validate(c.workspace(source, 1))['route_reason'])
    _finish(monkeypatch, source, 2, 'A', [1], action='finish')
    with controller._writer(source):
        pass
    child = instance / 'fork-child'
    limits = dict(max_tokens=200, max_seconds=30, max_steps=2)
    preview = fork_service.preview(source, 2, child, budget=limits)
    assert preview['status'] == 'fork_preview_ready'
    options = dict(budget=limits, expected_preview_id=preview['preview_id'],
        operation_id='nested-fork-creation')
    request = _request(instance)
    observed = []
    if first == 'fork':
        prepare = fork_service._prepare

        def check_delete(*args):
            result = prepare(*args)
            before = _retained(instance)
            with pytest.raises(archive.files.ArchiveError):
                deletion.delete_instance(instance, **request)
            _assert_retained(instance, before)
            observed.append(True)
            return result

        monkeypatch.setattr(fork_service, '_prepare', check_delete)
        receipt = fork_service.create(source, 2, child, **options)
        assert observed == [True]
        with owner.lease(child):
            with pytest.raises(deletion.DeleteError) as error:
                deletion.delete_instance(instance, **request)
            assert error.value.code == 'delete_nested_instance'
        def forbidden(*args):
            pytest.fail('An exact published receipt must not repeat creation admission')
        monkeypatch.setattr(owner, 'creation', forbidden)
        assert fork_service.create(source, 2, child, **options) == receipt
    else:
        preflight = deletion._preflight_tree

        def check_fork(root, identity):
            if root == instance and not observed:
                with pytest.raises(archive.files.ArchiveError):
                    fork_service.create(source, 2, child, **options)
                assert not child.exists()
                assert not (instance / '.scid-fork-operations').exists()
                observed.append(True)
            return preflight(root, identity)

        monkeypatch.setattr(deletion, '_preflight_tree', check_fork)
        assert deletion.delete_instance(instance, **request)['status'] == 'instance_deleted'
        assert observed == [True]


def _cold_checkpoint_at(root, tmp_path):
    public = tmp_path / 'public'
    controller.prepare(root, public / 'objective.md', public, [sys.executable],
        max_tokens=4000, max_seconds=60, max_steps=7, model='fixture-never-called',
        reasoning_effort='high', search_policy=None, experiment_mode='external')
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Fixture inactive checkpoint, no scientific dispatch')
    anchors = tmp_path / 'nested-anchors'
    anchors.mkdir()
    archive.checkpoint_enroll(root, anchors)
    anchor = Path(c.read(root / owner.BINDING)['anchor']) / 'owner.json'
    package = tmp_path / 'nested-checkpoint'
    archive.checkpoint_export(root, package)
    previous = root.rename(tmp_path / 'previous-nested-instance')
    return package, previous, anchor


def test_checkpoint_restore_cannot_publish_after_final_tombstone_preflight(instance, tmp_path, monkeypatch):
    request = _request(instance)
    key = hashlib.sha256(request['operation_id'].encode()).hexdigest()
    tombstone = instance.parent / ('.scid-delete-' + key[:24])
    inner = tombstone / 'independent-instance'
    # This is an exact supported historical original, not a changed restore
    # destination: the ordinary outer will later retire into this pathname.
    package, previous, anchor = _cold_checkpoint_at(inner, tmp_path)
    tombstone.rmdir()
    originals = _retained(previous)
    anchor_before = anchor.read_bytes()
    preflight = deletion._preflight_tree
    observed = []

    def restore_after_preflight(root, identity):
        result = preflight(root, identity)
        if root == tombstone:
            before = _retained(root)
            entries = set(root.iterdir())
            with pytest.raises(archive.files.ArchiveError):
                archive.checkpoint_restore(inner, package)
            assert not inner.exists()
            assert set(root.iterdir()) == entries
            _assert_retained(root, before)
            assert anchor.read_bytes() == anchor_before
            observed.append(root)
        return result

    monkeypatch.setattr(deletion, '_preflight_tree', restore_after_preflight)
    assert deletion.delete_instance(instance, **request)['status'] == 'instance_deleted'
    assert observed == [tombstone]
    assert not tombstone.exists()
    _assert_retained(previous, originals)
    assert c.read(anchor)['state'] == 'frozen'


@pytest.mark.parametrize('pending_retry', [False, True])
def test_checkpoint_restore_fences_outer_delete_through_publication(instance, tmp_path, monkeypatch, pending_retry):
    request = _request(instance)
    inner = instance / 'independent-instance'
    package, previous, anchor = _cold_checkpoint_at(inner, tmp_path)
    originals = _retained(previous)
    publish = archive._publish
    observed = []

    def delete_before_publication(stage, target):
        if target == inner:
            before = _retained(instance)
            with pytest.raises(archive.files.ArchiveError):
                deletion.delete_instance(instance, **request)
            _assert_retained(instance, before)
            assert not (instance / owner.RETIREMENT).exists()
            observed.append(stage)
        return publish(stage, target)

    monkeypatch.setattr(archive, '_publish', delete_before_publication)
    if pending_retry:
        with monkeypatch.context() as patch:
            def missing(settings):
                raise archive.files.ArchiveError('Fixture pending restore dependency')
            patch.setattr(archive, '_restore_dependencies', missing)
            with pytest.raises(archive.files.ArchiveError, match='Fixture pending restore dependency'):
                archive.checkpoint_restore(inner, package)
        assert c.read(anchor)['state'] == 'restore_pending'
        assert c.read(anchor)['generation'] == 2
    result = archive.checkpoint_restore(inner, package)
    assert result['status'] == 'checkpoint_restored'
    assert result['generation'] == 2 and result['paused'] is True
    assert len(observed) == 1
    _assert_retained(previous, originals)
    with owner.lease(inner):
        with pytest.raises(deletion.DeleteError) as error:
            deletion.delete_instance(instance, **request)
        assert error.value.code == 'delete_nested_instance'

    # A completed restore is still a pure acknowledgment after control advances;
    # it does not repeat creator admission or restore an old pause/generation.
    with controller._writer(inner):
        (inner / 'pause.request').unlink()
        budget.transition(inner, 'advance', 'Fixture control advancement, no native launch')
    before = _retained(inner)
    def forbidden(*args):
        pytest.fail('An exact completed restore must not repeat creation admission')
    monkeypatch.setattr(owner, 'creation', forbidden)
    again = archive.checkpoint_restore(inner, package)
    assert again['status'] == 'checkpoint_restore_already_completed'
    assert again['generation'] == 2 and again['paused'] is False
    _assert_retained(inner, before)


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


@pytest.mark.parametrize('wrong', ['instance_id', 'settings_ref'])
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
    c.write(instance / 'settings-identity.json', dict(path='settings.json'))
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
    binding = c.record_ref(root / 'jobs/001.json')
    c.write(record / 'native-intent.json', dict(task_ref=binding))
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
        '--settings-ref', json.dumps(request['settings_ref']), '--operation-id', request['operation_id']]
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
        deletion.delete_instance(instance, **dict(request, settings_ref=dict(path='/fixture/settings.json')))
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
        dict(task_ref=c.record_ref(root / 'jobs/001.json')))
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
    # Its hash bucket also holds the original settings.json beside project/nodes;
    # immutable inherited payload must not be mistaken for a nested instance.
    _finish(monkeypatch, instance, 1, 'H', [], action='analyze',
        files={'shared.bin': payload, 'settings-copy.json': (instance / 'settings.json').read_text()})
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
            row['size_bytes']): root / originals.FORK_DIRECTORY / row['storage_path']
            for row in manifest['entries'] if row['path'].endswith('/shared.bin')}

    original = instance / 'project/nodes/001/artifacts/shared.bin'
    key = (originals.identity_key(originals.local_identity(instance)),
        original.relative_to(instance).as_posix(), original.stat().st_size)
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
