"""Explicit local disposal on temporary fixtures; no model, solver or cancellation."""
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (budget, contracts as c, controller,
    fork_service, fork_sources, instance_archive as archive, instance_delete as deletion,
    instance_owner as owner, protocol, runtime)
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
    return root.parent / '.scid-delete-operations' / (archive.files.safe_id(operation) + '.json')


def _enroll_legacy_owner(root, tmp_path):
    """Retained enrolled owners still fence effects after cold APIs retire."""
    anchors = tmp_path / 'legacy-owner-anchors'
    anchors.mkdir()
    with owner.source_lease(root), ExitStack() as locks:
        archive._owner_locks(locks, root)
        return owner.enroll(root, anchors)


def _stopped_managed_call(root, *, retired=False):
    """Finite retained native controls, checked by the actual lifecycle verifiers."""
    from scidiscovery.research_loop import experiments as e
    c.build_job(root, 1, 'H', [])
    call = '0001-' + 'a' * 32
    directory = root / 'project/nodes/001/work/lineage/calls' / call
    directory.mkdir(parents=True)
    scope = dict(step=1, role='H', job_ref=c.record_ref(root / 'jobs/001.json'))
    common = dict(call_id=call, scope=scope, mechanism_binding=None)
    script = directory / 'source.py'
    script.write_bytes(b'print("retained historical code")\n')
    prepared = dict(name=call, local_path=str(script), size_bytes=script.stat().st_size, media_type='text/x-python')
    interface = dict(version=e.VERSION, executor='local', terminal_evidence='local_process')
    accepted = dict(common, interface=interface, prepared=prepared, script=e.proof(script), native_attempt=dict(number=0))
    if retired:
        contract = dict(e.RETIREMENT_CONTRACT, backend_protocol=dict(version=1, fixture='retained-not-submitted'))
        capability = dict(key=e.RETIREMENT_KEY, schema_id=e.RETIREMENT_SCHEMA,
            payload_schema_version=1, kind='execution_recovery', media_type='application/json', size_bytes=len(e.encoded(contract)))
        interface['public_capabilities'] = [capability]
        authority = directory / 'authority.json'
        c.write(authority, dict(call_id=call, scope=scope, outcome='not_submitted'))
        accepted['execution_authority'] = e.proof(authority)
        c.write(directory / 'accepted.json', accepted)
        native = directory / 'native-not-submitted.json'
        c.write(native, dict(call_id=call, scope=scope, submitted=False))
        fields = dict(common, accepted=e.proof(directory / 'accepted.json'), prepared=prepared,
            authority=e.proof(authority), native_originals=[e.proof(native)])
        assertion = directory / 'not-submitted-assertion.json'
        c.write(assertion, dict(version=1, kind='permanent_not_submitted',
            **{key:fields[key] for key in ('call_id', 'scope', 'accepted', 'prepared', 'authority', 'native_originals')}))
        value = dict(fields, version=2, kind='permanent_not_submitted', interface=interface,
            native_attempt=accepted['native_attempt'], capability=capability, contract=contract, assertion=e.proof(assertion))
        c.write(directory / 'submission-retirement.json', value)
        controls = dict(native=native, assertion=assertion)
    else:
        c.write(directory / 'accepted.json', accepted)
        c.write(directory / 'completed.json', dict(common, status='known_success', proofs=[], outputs=[]))
        c.write(directory / 'submission.json', dict(common, external_run_id='retained-original-run'))
        terminal = directory / 'terminal.json'
        c.write(terminal, dict(common, cleanup_confirmed=True, transport_complete=True, returncode=0))
        science = root.parent / 'missing-native-science.bin'
        science.write_bytes(b'Former scientific output is not a writer-stop receipt.\n')
        output = dict(e.proof(science), name='science.bin', source=e.proof(science), descriptor=dict(
            name='science.bin', local_path=str(science), size_bytes=science.stat().st_size, media_type='application/octet-stream'))
        value = dict(common, version=e.OBSERVATION_VERSION, interface=interface,
            accepted=e.proof(directory / 'accepted.json'), completed=e.proof(directory / 'completed.json'),
            submission=e.proof(directory / 'submission.json'), native_terminal=e.proof(terminal),
            status='known_success', external_run_id='retained-original-run', proofs=[], outputs=[],
            scientific_outputs=[output], scientific_source=dict(e.proof(script), source=e.proof(script)))
        c.write(directory / 'execution-observation.json', value)
        science.unlink()
        controls = dict(terminal=terminal, observation=directory / 'execution-observation.json')
    script.unlink()
    return controls


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


def test_enrolled_legacy_writer_fences_delete_without_a_host_gate(instance, tmp_path):
    binding = _enroll_legacy_owner(instance, tmp_path)
    directory, anchor = owner._anchor(binding)
    before = _retained(instance)
    # Older registered writers hold this existing anchor lock even when they
    # do not participate in the newer host inode gate.
    with owner._owner_lease(instance):
        with pytest.raises(archive.files.ArchiveError, match='enrolled effect'):
            deletion.delete_instance(instance, **_request(instance))
    _assert_retained(instance, before)
    assert c.read(directory / 'owner.json') == anchor
    assert not (instance / owner.RETIREMENT).exists()
    assert deletion.delete_instance(instance, **_request(instance))['status'] == 'instance_deleted'


@pytest.mark.parametrize('state', ['frozen', 'superseded'])
def test_frozen_or_superseded_legacy_owner_is_not_reactivated(instance, tmp_path, state):
    binding = _enroll_legacy_owner(instance, tmp_path)
    directory, anchor = owner._anchor(binding)
    changed = dict(anchor, state='frozen') if state == 'frozen' else dict(anchor, generation=anchor['generation'] + 1)
    archive.files.durable_json(directory / 'owner.json', changed)
    before = _retained(instance)
    with pytest.raises(archive.files.ArchiveError, match='frozen or superseded'):
        with owner.lease(instance):
            pytest.fail('A retained inactive owner admitted an ordinary writer')
    with pytest.raises(deletion.DeleteError, match='Owner is frozen|generation'):
        deletion.delete_instance(instance, **_request(instance))
    _assert_retained(instance, before)
    assert c.read(directory / 'owner.json') == changed
    assert not (instance / owner.RETIREMENT).exists()


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


@pytest.mark.parametrize('damage', ['science_missing', 'terminal_missing', 'cleanup_unknown'])
def test_delete_uses_terminal_controls_without_requiring_missing_science(instance, damage):
    from scidiscovery.research_loop import experiments as e
    controls = _stopped_managed_call(instance)
    if damage == 'terminal_missing':
        controls['terminal'].unlink()
    elif damage == 'cleanup_unknown':
        terminal = c.read(controls['terminal'])
        c.write(controls['terminal'], dict(terminal, cleanup_confirmed=False))
        observation = c.read(controls['observation'])
        c.write(controls['observation'], dict(observation, native_terminal=e.proof(controls['terminal'])))
    request = _request(instance)
    if damage == 'science_missing':
        assert deletion.delete_instance(instance, **request)['status'] == 'instance_deleted'
    else:
        before = _retained(instance)
        with pytest.raises((e.IntegrityError, OSError, ValueError)):
            deletion.delete_instance(instance, **request)
        _assert_retained(instance, before)
        assert not (instance / owner.RETIREMENT).exists()


@pytest.mark.parametrize('damage', ['science_missing', 'native_missing', 'assertion_missing'])
def test_delete_keeps_exact_retirement_controls_when_scientific_source_is_missing(instance, damage):
    from scidiscovery.research_loop import experiments as e
    controls = _stopped_managed_call(instance, retired=True)
    if damage != 'science_missing':
        controls[damage.removesuffix('_missing')].unlink()
    request = _request(instance)
    if damage == 'science_missing':
        assert deletion.delete_instance(instance, **request)['status'] == 'instance_deleted'
    else:
        before = _retained(instance)
        with pytest.raises((e.IntegrityError, OSError, ValueError)):
            deletion.delete_instance(instance, **request)
        _assert_retained(instance, before)
        assert not (instance / owner.RETIREMENT).exists()


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


@pytest.mark.parametrize('missing_input', [False, True])
def test_delete_removes_own_git_and_exports_without_reading_external_inputs_or_neighbors(instance, tmp_path, monkeypatch, missing_input):
    owned = [instance / 'host-runtime/code-versions/repository.git/objects/ab/fixture-object',
        instance / 'host-runtime/code-versions/exports/001/fixture-call/tree/run.py']
    for path in owned:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'Finite owned code fixture; no Git process.\n')
    source = tmp_path / 'public/objective.md'
    source.chmod(0o400)
    before = source.stat()
    body = source.read_bytes()
    neighbor = tmp_path / 'neighbor-instance'
    neighbor.mkdir()
    sentinel = neighbor / 'retained-data.bin'
    sentinel.write_bytes(b'Another explicit instance is never inventoried for deletion.\n')
    if missing_input:
        source.unlink()
    request = _request(instance)
    open_file, scandir = Path.open, os.scandir
    chmod, fchmod = os.chmod, os.fchmod

    def guarded_open(path, *args, **options):
        assert path not in {source, sentinel}, 'Deletion read an external scientific body'
        return open_file(path, *args, **options)

    def guarded_scandir(path):
        if not isinstance(path, int):
            assert Path(path) not in {tmp_path, neighbor}, 'Deletion searched outside its selected instance'
        return scandir(path)

    def directory_chmod(path, mode, **options):
        assert stat.S_ISDIR(os.stat(path, dir_fd=options.get('dir_fd'), follow_symlinks=False).st_mode)
        return chmod(path, mode, **options)

    def directory_fchmod(descriptor, mode):
        assert stat.S_ISDIR(os.fstat(descriptor).st_mode)
        return fchmod(descriptor, mode)

    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', guarded_open)
        patch.setattr(os, 'scandir', guarded_scandir)
        patch.setattr(os, 'chmod', directory_chmod)
        patch.setattr(os, 'fchmod', directory_fchmod)
        result = deletion.delete_instance(instance, **request)
        assert deletion.delete_instance(instance, **request) == result
    assert result['status'] == 'instance_deleted'
    assert not instance.exists() and all(not path.exists() for path in owned)
    assert sentinel.read_bytes() == b'Another explicit instance is never inventoried for deletion.\n'
    if missing_input:
        assert not source.exists()
    else:
        after = source.stat()
        assert (after.st_dev, after.st_ino, after.st_mode) == (before.st_dev, before.st_ino, before.st_mode)
        assert source.read_bytes() == body


def test_expired_io_context_leaves_original_tombstone_for_same_operation_retry(instance, monkeypatch):
    c.write(instance / 'host-started.json', budget.clock_stamp())
    budget.transition(instance, 'paused', 'Fixture consumed budget is not maintenance authority')
    request = _request(instance, 'finite-io-retry')
    remove, unlink = deletion._remove_tree, os.unlink

    class Window:
        expired = False
        checks = 0

        def remaining_seconds(self):
            self.checks += 1
            if self.expired:
                raise TimeoutError('fixture current IO deadline expired')
            return 1

    window = Window()

    def interrupt_after_unlink(path, *args, **options):
        result = unlink(path, *args, **options)
        if options.get('dir_fd') is not None:
            window.expired = True
        return result

    def finite_remove(root, identity):
        return remove(root, identity, context=window)

    def forbidden(*args, **options):
        pytest.fail('Maintenance changed the scientific budget or started execution')

    with monkeypatch.context() as patch:
        patch.setattr(deletion, '_remove_tree', finite_remove)
        patch.setattr(os, 'unlink', interrupt_after_unlink)
        patch.setattr(budget, 'transition', forbidden)
        with pytest.raises(TimeoutError, match='current IO deadline'):
            deletion.delete_instance(instance, **request)
    record = c.read(_record_path(instance, request['operation_id']))
    assert window.expired and window.checks > 0
    assert record['phase'] == 'retired' and record['request']['operation_id'] == request['operation_id']
    assert not instance.exists()
    tombstone = Path(record['tombstone'])
    assert owner._identity(tombstone) == record['root_identity']
    with monkeypatch.context() as patch:
        patch.setattr(budget, 'transition', forbidden)
        receipt = deletion.delete_instance(instance, **request)
        assert deletion.delete_instance(instance, **request) == receipt
    assert not tombstone.exists()


@pytest.mark.parametrize('interruption', ['before_rename', 'after_tree_removed', 'after_anchor_deleted'])
def test_enrolled_anchor_retirement_survives_crashes_and_fences_legacy_owner(instance, tmp_path, monkeypatch, interruption):
    root = instance
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'paused', 'Quiescent legacy-owner fixture, no scientific dispatch')
    binding = _enroll_legacy_owner(root, tmp_path)
    anchor_path = Path(binding['anchor']) / 'owner.json'
    historical = tmp_path / 'historical-checkpoint-evidence.json'
    historical.write_bytes(b'{"fixture":"old cold evidence remains inert"}\n')
    retained_evidence = historical.read_bytes()
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
    with pytest.raises(archive.files.ArchiveError, match='frozen or superseded'):
        owner._active(root, binding)
    result = deletion.delete_instance(root, **request)
    assert result['status'] == 'instance_deleted'
    assert not root.exists()
    anchor = c.read(anchor_path)
    assert anchor['state'] == 'deleted' and anchor['generation'] == binding['generation']
    assert c.read(_record_path(root, request['operation_id']))['phase'] == 'deleted'
    assert deletion.delete_instance(root, **request) == result
    with pytest.raises(archive.files.ArchiveError, match='frozen or superseded'):
        owner._active(root, binding)
    assert not root.exists()
    assert historical.read_bytes() == retained_evidence
