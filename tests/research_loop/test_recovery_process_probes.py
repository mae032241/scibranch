"""Read-only positive interruption proofs from original receipts and proc facts."""
import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import accepted_budget, execution, runtime
from scidiscovery.research_loop import ui_saved_status, ui_view


BOOT = 'current-host-boot'
RECEIPTS = ('runner-process.json', 'native-process.json',
    'bridge-process.json', 'watchdog-process.json')


def _identity(pid, binding, **changes):
    return dict(dict(pid=pid, boot_id=BOOT, start_ticks=100 + pid,
        pgid=pid, sid=pid, ppid=1, state='S', task_ref=binding), **changes)


def _stat(identity):
    # Linux stat fields 3..22; comm deliberately contains spaces and parentheses.
    fields = [identity['state'], str(identity['ppid']), str(identity['pgid']),
        str(identity['sid']), *(['0'] * 15), str(identity['start_ticks'])]
    return f"{identity['pid']} (native worker (fixture)) {' '.join(fields)}\n"


@pytest.fixture
def proc_scene(tmp_path, monkeypatch):
    record = tmp_path / 'record'
    record.mkdir()
    binding = dict(step=1, attempt=0,
        native_intent_ref=dict(path='native-intent.json', sha256='a' * 64))
    scene = SimpleNamespace(record=record, binding=binding, identities={},
        stats={}, entries=[], stat_calls=[], inventory_calls=[], boot_calls=[])

    def receipt(name, **changes):
        pid = 41001 + RECEIPTS.index(name)
        identity = _identity(pid, scene.binding, **changes)
        (scene.record / name).write_text(json.dumps(identity))
        scene.identities[name] = identity
        return identity

    scene.receipt = receipt
    receipt('runner-process.json')
    receipt('native-process.json')
    read_text = Path.read_text
    iterdir = Path.iterdir

    def proc_read(path, *args, **kwargs):
        if path == Path('/proc/sys/kernel/random/boot_id'):
            scene.boot_calls.append(path)
            return BOOT + '\n'
        if path.name == 'stat' and path.parent.parent == Path('/proc'):
            pid = int(path.parent.name)
            scene.stat_calls.append(pid)
            value = scene.stats.get(pid, FileNotFoundError(errno.ENOENT, 'fixture PID absent'))
            if isinstance(value, BaseException):
                raise value
            return value
        return read_text(path, *args, **kwargs)

    def proc_entries(path):
        if path == Path('/proc'):
            scene.inventory_calls.append(path)
            if isinstance(scene.entries, BaseException):
                raise scene.entries
            return iter(Path('/proc') / str(pid) for pid in scene.entries)
        return iterdir(path)

    def no_signal(*args, **kwargs):
        pytest.fail('An interruption probe must never signal any PID or group')

    monkeypatch.setattr(Path, 'read_text', proc_read)
    monkeypatch.setattr(Path, 'iterdir', proc_entries)
    monkeypatch.setattr(runtime.os, 'kill', no_signal)
    monkeypatch.setattr(runtime.os, 'killpg', no_signal)
    return scene


def _probe(scene, *, current_boot=BOOT):
    # Snapshot the exact named originals, including malformed receipt bytes.
    originals = {name: (scene.record / name).read_bytes()
        for name in RECEIPTS if (scene.record / name).is_file()}
    result = runtime.probe_interruption(scene.record, scene.binding, current_boot=current_boot)
    assert {name: (scene.record / name).read_bytes() for name in originals} == originals
    return result


def _stopped(scene, result, kind, observed):
    assert result == dict(status='stopped', kind=kind, boot_id=BOOT,
        processes=scene.identities, observed=observed, groups_absent=True)


def test_previous_boot_proves_stopped_without_inspecting_reused_numeric_pids(proc_scene):
    scene = proc_scene
    for name in RECEIPTS:
        identity = scene.receipt(name, boot_id='previous-host-boot')
        # A current-boot process could now occupy every old numeric PID.
        scene.stats[identity['pid']] = _stat(dict(identity, boot_id=BOOT, start_ticks=999999))
    scene.entries = list(scene.stats)
    result = _probe(scene)
    _stopped(scene, result, 'boot_changed', dict.fromkeys(RECEIPTS, 'previous_boot'))
    assert scene.stat_calls == scene.inventory_calls == scene.boot_calls == []


@pytest.mark.parametrize(('state', 'observation'), [
    ('absent', 'absent'), ('reused', 'reused'), ('Z', 'zombie'), ('X', 'dead'), ('x', 'dead')])
def test_same_boot_requires_original_leader_and_group_absence(proc_scene, state, observation):
    scene = proc_scene
    for identity in scene.identities.values():
        pid = identity['pid']
        if state == 'absent':
            scene.stats[pid] = ProcessLookupError(errno.ESRCH, 'fixture original exited')
        else:
            current = dict(identity, **({'state': state} if state != 'reused'
                else {'start_ticks': identity['start_ticks'] + 1000,
                    'pgid': 51000, 'sid': 51000}))
            scene.stats[pid] = _stat(current)
            scene.entries.append(pid)
    result = _probe(scene)
    _stopped(scene, result, 'same_boot_stopped',
        dict.fromkeys(('runner-process.json', 'native-process.json'), observation))
    assert scene.inventory_calls == [Path('/proc')]
    assert set(scene.stat_calls) == {value['pid'] for value in scene.identities.values()}


def test_live_original_after_absent_runner_remains_running(proc_scene):
    scene = proc_scene
    identity = scene.identities['native-process.json']
    scene.stats[identity['pid']] = _stat(identity)
    result = _probe(scene)
    assert result == dict(status='running', reason='original_owner_running')
    assert scene.inventory_calls == []


@pytest.mark.parametrize('failure', ['missing_native', 'malformed_native',
    'missing_identity_field', 'wrong_binding', 'missing_boot', 'mixed_boots'])
def test_original_receipts_and_exact_binding_are_required(proc_scene, failure):
    scene = proc_scene
    path = scene.record / 'native-process.json'
    identity = scene.identities['native-process.json']
    if failure == 'missing_native':
        path.unlink()
    elif failure == 'malformed_native':
        path.write_text('{not valid json')
    else:
        identity = dict(identity)
        if failure == 'missing_identity_field':
            identity.pop('start_ticks')
        elif failure == 'wrong_binding':
            identity['task_ref'] = dict(scene.binding, attempt=1)
        elif failure == 'missing_boot':
            identity.pop('boot_id')
        else:
            identity['boot_id'] = 'previous-host-boot'
        path.write_text(json.dumps(identity))
    result = _probe(scene)
    assert result['status'] == 'unknown'
    assert 'reason' in result and 'groups_absent' not in result


def test_missing_current_boot_cannot_prove_stopped(proc_scene):
    assert _probe(proc_scene, current_boot='') == dict(
        status='unknown', reason='current_boot_unavailable')
    assert proc_scene.stat_calls == proc_scene.inventory_calls == []


@pytest.mark.parametrize('failure', ['permission', 'bad_stat'])
def test_unreadable_original_pid_is_unknown_despite_legacy_false_and_empty_group(proc_scene, failure):
    scene = proc_scene
    identity = scene.identities['runner-process.json']
    scene.stats[identity['pid']] = (PermissionError(errno.EACCES, 'fixture denied')
        if failure == 'permission' else 'malformed proc stat')
    # These old boolean helpers erase exactly the uncertainty at issue.
    assert runtime.process_matches(identity, scene.binding) is False
    assert runtime._observed_group_members(identity) == []
    result = _probe(scene)
    assert result == dict(status='unknown', reason=('original_process_probe_unavailable'
        if failure == 'permission' else 'original_process_probe_invalid'))
    assert 'groups_absent' not in result


@pytest.mark.parametrize('failure', ['inventory_denied', 'member_denied', 'member_bad_stat', 'live_member'])
def test_absent_leaders_do_not_hide_unknown_or_surviving_original_group(proc_scene, failure):
    scene = proc_scene
    if failure == 'inventory_denied':
        scene.entries = PermissionError(errno.EACCES, 'fixture proc inventory denied')
        expected = 'original_group_probe_unavailable'
    else:
        pid = 42001
        scene.entries = [pid]
        if failure == 'member_denied':
            scene.stats[pid] = PermissionError(errno.EACCES, 'fixture member denied')
            expected = 'original_group_probe_unavailable'
        elif failure == 'member_bad_stat':
            scene.stats[pid] = '42001 (broken) S 1'
            expected = 'original_group_probe_invalid'
        else:
            owner = scene.identities['native-process.json']
            scene.stats[pid] = _stat(dict(owner, pid=pid, ppid=owner['pid'],
                start_ticks=owner['start_ticks'] + 1))
            expected = 'original_group_members_remain'
    assert _probe(scene) == dict(status='unknown', reason=expected)


@pytest.mark.parametrize('name', ['bridge-process.json', 'watchdog-process.json'])
def test_present_optional_original_owner_must_also_be_stopped(proc_scene, name):
    scene = proc_scene
    identity = scene.receipt(name)
    scene.stats[identity['pid']] = _stat(identity)
    assert _probe(scene) == dict(status='running', reason='original_owner_running')


def test_present_optional_receipt_is_validated_even_after_reboot(proc_scene):
    scene = proc_scene
    for name in ('runner-process.json', 'native-process.json'):
        scene.receipt(name, boot_id='previous-host-boot')
    (scene.record / 'bridge-process.json').write_text('[]')
    assert _probe(scene) == dict(status='unknown', reason='original_process_identity_invalid')
    assert scene.stat_calls == scene.inventory_calls == []


@pytest.fixture
def accepted_proc_scene(proc_scene, tmp_path, monkeypatch):
    from test_reboot_native_acceptance import accepted_interrupted_native

    scene = proc_scene
    root, record, _ = accepted_interrupted_native(tmp_path, monkeypatch, meter=False)
    scene.root = root
    scene.record = record
    scene.binding = execution.identity(root, 1)
    scene.identities.clear()
    for name in ('runner-process.json', 'native-process.json'):
        identity = scene.receipt(name, state='R')
        scene.stats[identity['pid']] = _stat(identity)
    return scene


def _saved_originals(scene):
    paths = [scene.root / accepted_budget.HEAD, scene.root / 'jobs/001.json',
        *(scene.record / name for name in
            ('native-intent.json', 'runner-process.json', 'native-process.json'))]
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


def _assert_originals_unchanged(originals):
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in originals} == originals


def _current_is_open(scene):
    head = accepted_budget.read(scene.root)
    assert head['current_native']['record'] == scene.record.relative_to(scene.root).as_posix()
    assert accepted_budget.native_attempt(scene.root, 1, scene.record, head=head)['status'] == 'open'
    return head


@pytest.mark.parametrize('death', ['absent', 'reused', 'Z', 'X', 'x'])
def test_open_saved_running_owner_is_reprobed_after_death_without_file_changes(
        accepted_proc_scene, death):
    scene = accepted_proc_scene
    head = _current_is_open(scene)
    originals = _saved_originals(scene)
    memo = ui_view.DisplayMemo()
    assert runtime.node_state(scene.root, 1) == 'running'
    first = ui_saved_status.status(scene.root, memo=memo)
    assert first['nodes'][0]['state'] == 'running'
    # The previously generated JSON remains a running observation; neither it
    # nor immutable on-disk receipts may substitute for the next live probe.
    retained_json = json.dumps(first)
    for identity in scene.identities.values():
        scene.stats[identity['pid']] = (FileNotFoundError(errno.ENOENT, 'fixture exited')
            if death == 'absent' else _stat(dict(identity,
                **({'start_ticks': identity['start_ticks'] + 1} if death == 'reused'
                    else {'state': death}))))
        assert runtime.process_live(identity, scene.binding) is False
    assert runtime.node_state(scene.root, 1) != 'running'
    second = ui_saved_status.status(scene.root, memo=memo)
    assert second['nodes'][0]['state'] == 'unknown'
    assert json.loads(retained_json)['nodes'][0]['state'] == 'running'
    assert _current_is_open(scene) == head
    _assert_originals_unchanged(originals)


def test_live_native_without_output_remains_running_with_open_saved_owner(accepted_proc_scene):
    scene = accepted_proc_scene
    _current_is_open(scene)
    runner = scene.identities['runner-process.json']
    scene.stats.pop(runner['pid'])
    native = scene.identities['native-process.json']
    originals = _saved_originals(scene)
    assert not (scene.record / 'native-outcome.json').exists()
    assert not (scene.record / 'science-output.json').exists()
    assert not (scene.record / 'usage.json').exists()
    assert runtime.process_live(native, scene.binding) is True
    assert runtime.node_state(scene.root, 1) == 'running'
    assert ui_saved_status.status(scene.root, memo=ui_view.DisplayMemo())['nodes'][0]['state'] == 'running'
    _assert_originals_unchanged(originals)


def test_foreign_receipt_cannot_supply_its_own_task_binding_as_live_authority(accepted_proc_scene):
    scene = accepted_proc_scene
    foreign_binding = dict(path='different-original-job.json')
    for name in ('runner-process.json', 'native-process.json'):
        identity = scene.receipt(name, state='R', task_ref=foreign_binding)
        scene.stats[identity['pid']] = _stat(identity)
        # Reconstructing an expected binding from the receipt itself would
        # incorrectly pass the old helper even though this is another task.
        assert runtime.process_matches(identity, foreign_binding) is True
        assert runtime.process_live(identity, scene.binding) is False
    _current_is_open(scene)
    originals = _saved_originals(scene)
    assert runtime.node_state(scene.root, 1) != 'running'
    assert ui_saved_status.status(scene.root, memo=ui_view.DisplayMemo())['nodes'][0]['state'] == 'unknown'
    _assert_originals_unchanged(originals)


@pytest.mark.parametrize('invalid', ['previous_boot', 'missing_start_ticks'])
def test_process_live_requires_complete_original_identity_and_current_boot(proc_scene, invalid):
    scene = proc_scene
    identity = dict(scene.identities['runner-process.json'])
    scene.stats[identity['pid']] = _stat(identity)
    if invalid == 'previous_boot':
        identity['boot_id'] = 'previous-host-boot'
    else:
        identity.pop('start_ticks')
    assert runtime.process_live(identity, scene.binding) is False
