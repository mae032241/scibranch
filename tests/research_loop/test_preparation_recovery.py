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
from test_nonlocal_execution_observation import case, run, research, research_path
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
from test_nonlocal_execution_observation import research
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
    original=e.verify_scientific_inputs
    def verify(accepted,*args,**kwargs):
        result=original(accepted,*args,**kwargs)
        if accepted.get('host_preparation'):stop(accepted['host_preparation']['path'])
        return result
    e.verify_scientific_inputs=verify
elif boundary=='accepted':
    original=e.write
    def write(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if pathlib.Path(path).name=='accepted.json':stop(path)
        return result
    e.write=write
e.dispatch(config,payload,new=True,mechanism=research(payload.parents[4], '1'))
raise AssertionError('writer passed its durable stop')
'''


LOCAL_CRASH_WRITER = r'''
from test_nonlocal_execution_observation import research
import json, os, pathlib, signal, sys
from scidiscovery.research_loop import contracts as c, experiments as e
from scidiscovery.research_loop.local_experiments import LocalExecution
config=c.read(pathlib.Path(sys.argv[1]));payload=pathlib.Path(sys.argv[2]);boundary=sys.argv[3]
counter=pathlib.Path(sys.argv[4])
original_prepare=LocalExecution.prepare
def prepare(*args,**kwargs):
    with counter.open('a') as stream:
        stream.write('prepare\n');stream.flush();os.fsync(stream.fileno())
    return original_prepare(*args,**kwargs)
LocalExecution.prepare=prepare
def submit(*args,**kwargs):
    with counter.open('a') as stream:
        stream.write('submit\n');stream.flush();os.fsync(stream.fileno())
    raise AssertionError('Local crash fixture must stop before submission')
LocalExecution.submit=submit
def stop(path):
    print(json.dumps(dict(ready=str(path))),flush=True)
    while True:signal.pause()
if boundary=='partial':
    original=c._bind_original
    def copy(*args,**kwargs):
        result=original(*args,**kwargs)
        target=pathlib.Path(args[1])
        if target.name=='source.py' and target.parent.name.startswith('.prepared-'):stop(target)
        return result
    c._bind_original=copy
else:
    original=e.write
    def write(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if pathlib.Path(path).name=='accepted.json':stop(path)
        return result
    e.write=write
e.dispatch(config,payload,new=True,mechanism=research(payload.parents[4], '1'))
raise AssertionError('Local writer passed its durable stop')
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
    path = c.workspace(root, 2) / 'code/opaque-package'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b'opaque contract fixture; never executed as source\n')
    return path


def _local_payload(root):
    path = c.workspace(root, 2) / 'code/local.py'
    path.parent.mkdir(exist_ok=True)
    path.write_text('raise AssertionError("Local preparation fixture must never launch")\n')
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
    assert counter.read_text().splitlines() == ['prepare']
    return row, counter


def _maintain(root, row, action, **changes):
    options = dict(action=action, max_seconds=10, authorization_note='Exact local fixture maintenance')
    options.update(changes)
    return recovery.maintain(root, 2, row['call_id'], row['snapshot_token'], **options)


def _pending_binding(root, row):
    binding = row['binding']
    return dict(path=Path(binding['path']).relative_to(root).as_posix(),
        size_bytes=binding['size_bytes'])


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


def _unbound(root, snapshot, action='discard-partial', **changes):
    options = dict(action=action, max_seconds=10, authorization_note='Exact built-in local residual only', unbound_local=True)
    options.update(changes)
    return recovery.maintain(root, 2, None, snapshot.name.removeprefix('.prepared-'), **options)


def _unbound_cli(root, snapshot, action):
    return cli.main(['execution-preparation-maintain', '--root', str(root), '--step', '2',
        '--unbound-local', '--snapshot-token', snapshot.name.removeprefix('.prepared-'), '--action', action,
        '--max-seconds', '10', '--authorization-note', 'Exact built-in local residual only'])


@contextmanager
def _local_writer(root, config, boundary='partial', *, payload=None):
    original = execution.record(root, 2, config['native_attempt']['number']) / 'experiment-config.json'
    counter = root.parent / 'local-preparation-counters'
    with _child(LOCAL_CRASH_WRITER, original, payload or _local_payload(root), boundary, counter) as (process, ready):
        materialized = Path(ready['ready'])
        assert materialized.is_file() and materialized.parent.name.startswith('.prepared-')
        yield process, materialized.parent, counter


def _killed_local(root, config, boundary='partial'):
    with _local_writer(root, config, boundary) as (process, snapshot, counter):
        process.send_signal(signal.SIGKILL)
        assert process.wait(timeout=5) == -signal.SIGKILL
    assert counter.read_text().splitlines() == ['prepare']
    return snapshot, counter


@pytest.mark.parametrize('damage', ['clean', 'unknown_file', 'source_symlink'])
def test_legacy_copied_snapshot_consumer_uses_retained_originals(tmp_path, monkeypatch, damage):
    records = tmp_path / 'records'
    snapshot = records / '.prepared-legacy'; snapshot.mkdir(parents=True)
    draft = tmp_path / 'retired-draft.py'; draft.write_bytes(b'legacy copied source\n')
    source = snapshot / 'source.py'; source.write_bytes(draft.read_bytes())
    prepared = dict(name='legacy-call', local_path=str(draft), size_bytes=draft.stat().st_size,
        media_type='application/octet-stream')
    draft.unlink()
    inputs = snapshot / 'inputs'; inputs.mkdir()
    retained_input = inputs / 'context.txt'; retained_input.write_bytes(b'exact copied input\n')
    inventory = [dict(e.proof(retained_input), name='context.txt')]
    scope = dict(step=2, role='W')
    attempt = dict(number=0)
    interface = dict(executor='remote_execution:fixture')
    base = dict(call_id=prepared['name'], scope=scope, native_attempt=attempt, interface=interface,
        prepared=prepared, source_script=str(draft),
        script=dict(path=str(records / 'calls' / prepared['name'] / 'source.py'),
            size_bytes=prepared['size_bytes']), snapshot_files=inventory, parent=dict(candidate_id=None))
    value = dict(records=str(records), call_id=prepared['name'], prepared=prepared, scope=scope,
        native_attempt=attempt, interface=interface, acceptance_base=base,
        input_inventory=inventory, directory_identity=recovery._identity(snapshot))
    binding = tmp_path / 'legacy-binding.json'; binding.write_text(json.dumps(value))
    ref = e.proof(binding)
    accepted = dict(base, host_preparation=ref)
    accepted_path = snapshot / 'accepted.json'; accepted_path.write_text(json.dumps(accepted))
    originals = {path: path.read_bytes() for path in (source, retained_input, binding, accepted_path)}

    def forbidden(*args, **kwargs):
        pytest.fail('Legacy copied snapshot consumer reopened live payload resolution')
    monkeypatch.setattr(recovery.experiment_client, 'payload_path', forbidden)
    config = dict(worker_view_version=1)
    assert not draft.exists() and 'code_version' not in accepted
    assert recovery._accepted(snapshot, value, config, ref, historical=True) == accepted
    recovery._tree(snapshot, value, config, complete=True)
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    if damage == 'clean':
        return
    neighbor = tmp_path / 'neighbor'; neighbor.write_bytes(b'untouched neighbor\n')
    if damage == 'unknown_file':
        (snapshot / 'extra').write_bytes(b'unknown ownership')
    else:
        source.unlink(); source.symlink_to(neighbor)
    with pytest.raises(recovery.PreparationError, match='unknown file|link or filesystem boundary'):
        recovery._tree(snapshot, value, config, complete=True)
    assert neighbor.read_bytes() == b'untouched neighbor\n'
    assert binding.read_bytes() == originals[binding] and accepted_path.read_bytes() == originals[accepted_path]






@pytest.mark.process_e2e
def test_unbound_cleanup_preserves_foreign_executor_originals_without_adapter_effects(tmp_path, monkeypatch, remote_registry):
    root, _, config, _ = case(tmp_path, monkeypatch, executor='remote_execution:fixture')
    # This explicit foreign residual must never gain the built-in Local cleanup
    # exception or invoke an adapter to establish a different execution identity.
    records = Path(config['record_dir'])
    records.mkdir(parents=True, exist_ok=True)
    snapshot = records / '.prepared-foreign-residual'
    snapshot.mkdir()
    (snapshot / 'source.py').write_bytes(b'foreign partial original; never executed\n')
    counter = root.parent / 'foreign-maintenance-effects'
    counter.write_bytes(b'')
    native = execution.record(root, 2)
    foreign_originals = {path: path.read_bytes() for path in (
        root / 'settings.json', root / 'jobs/002.json',
        native / 'native-intent.json', native / 'experiment-config.json',
        records.parent / 'interfaces/000.json')}
    def no_adapter_effect(*args, **kwargs):
        with counter.open('ab') as stream:stream.write(b'forbidden adapter invocation\n')
        pytest.fail('Foreign snapshot maintenance invoked preparation or submission')
    monkeypatch.setattr(DifferentExternalIdentity, 'prepare', no_adapter_effect)
    monkeypatch.setattr(DifferentExternalIdentity, 'submit', no_adapter_effect)
    source = (snapshot / 'source.py').read_bytes()
    blocker, = [row for row in fork_sources._parent_state(root)
        if row['code'] == 'unknown_managed_preparation' and row['path'] == snapshot.relative_to(root).as_posix()]
    assert f'--step 2 --snapshot-token {snapshot.name.removeprefix(".prepared-")}' in blocker['reason']
    assert 'execution-preparation-maintain --help' in blocker['reason']
    assert 'Only for a proven built-in local unbound partial' in blocker['reason']
    assert 'bound or unknown preparations require the exact original call' in blocker['reason']
    for action in ('inspect', 'discard-partial', 'publish-accepted'):
        with pytest.raises((ValueError, OSError)):
            _unbound(root, snapshot, action)
    assert snapshot.is_dir() and (snapshot / 'source.py').read_bytes() == source
    assert counter.read_bytes() == b''
    assert all(path.read_bytes() == raw for path, raw in foreign_originals.items())
    assert e.Records(config).calls() == []


@pytest.mark.process_e2e
def test_durable_accepted_cli_only_publishes_original_inode(tmp_path, monkeypatch, remote_registry, capsys):
    root, _, config, _ = case(tmp_path, monkeypatch)
    row, counter = _killed(root, config, 'accepted')
    snapshot = Path(row['snapshot'])
    identity = snapshot.stat().st_dev, snapshot.stat().st_ino
    accepted = (snapshot / 'accepted.json').read_bytes()
    source = Path(c.read(snapshot / 'accepted.json')['script']['path'])
    frozen_source = source.read_bytes()
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
    assert source.read_bytes() == frozen_source
    assert recovery.retained_preparations(root, Path(config['record_dir']))[0]['resolved'] is True
    assert _maintain(root, row, 'publish-accepted')['status'] == 'already_published'
    assert e.Records(config).accepted(row['call_id'])['host_preparation'] == row['binding']
    assert counter.read_text().splitlines() == ['prepare']
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
    assert Path(row['snapshot']).is_dir() and not (Path(row['snapshot']) / 'accepted.json').exists()
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
    result = e.dispatch(config, _payload(root), new=True, mechanism=research(root, '1'))
    assert result['status'] == 'unknown' and result['call_id'] == row['call_id']
    descriptor = LocalFileDescriptor(**c.read(Path(row['binding']['path']))['prepared'])
    with pytest.raises((e.ConcurrentExecution, e.IntegrityError, recovery.PreparationError)):
        e.Records(config).accept(descriptor, dict(parent=dict(candidate_id=None)), config['execution_interface'], mechanism=research(root, '1'))
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
    seal = dict(job_ref=c.record_ref(root / 'jobs/002.json'), pending_executions=ref)
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
    assert execution_recovery.pending(root) and execution_recovery.pending(root, unknown_only=True)
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
        (snapshot / 'accepted.json').unlink(); (snapshot / 'accepted.json').symlink_to(neighbor / 'keep')
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
    assert counter.read_text().splitlines() == ['prepare']


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
    assert counter.read_text().splitlines() == ['prepare']


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
    for name in ('accepted.json',):
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
    # Current W research admission is distinct from retired breadth.admit.
    expected = ['prepare']
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
    assert counter.read_text().splitlines() == ['prepare']
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
    from scidiscovery.research_loop import breadth, execution_authority as authority, formal_review
    root, backend, output, active, config, _ = _reviewed_execution(chain, capsys)
    job = c.read(root / 'jobs/006.json')
    qualifications = job['execution_qualifications']
    assert len(qualifications) == 1
    subject = formal_review.subject_for_receipt(root, qualifications[0])
    assert subject['producer_step'] == 2
    from scidiscovery.research_loop import handoff_state, originals
    inputs=handoff_state.material_inputs(root,job,sources={},source=originals.node(root,job['step']))
    bound = [*inputs['direct_materials'], *[row['material'] for row in inputs.get('selected_inputs', [])]]
    material = subject['material']
    assert material in bound
    output = originals.material_path(root,material)
    assert e.proof(output)['size_bytes'] == material['size_bytes']
    admission = backend.execution_admission
    monkeypatch.setattr(backend, 'execution_admission', lambda *args, **kwargs:
        admission(*args, **kwargs).model_copy(update={'outcome': 'require_human_approval'}))
    diagnostic = e._diagnostic
    def traced(error, *args, **kwargs):
        traceback.print_exception(type(error), error, error.__traceback__)
        return diagnostic(error, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(e, '_diagnostic', traced)
        assert e.main(['--config', str(active), 'run', '--payload', str(output), '--research', str(Path(active).with_name('research.json'))]) == 2
    pending = json.loads(capsys.readouterr().out)
    assert pending['status'] == 'authorization_required'
    call = pending['call_id']
    requests = sorted((root / 'execution-authority/requests').glob('*/request.json'))
    assert len(requests) == 1
    _approve(root, q._ref(root, requests[0]))
    row, counter = _killed_authorized_resume(root, backend, call, config, 'binding',
        operation=lambda: e.main(['--config', str(active), 'run', '--payload', str(output), '--research', str(Path(active).with_name('research.json'))]))
    assert counter.read_text().splitlines() == ['prepare']
    assert not (execution.origin(root, 6) / 'result-identity.json').exists()
    def forbidden(*args, **kwargs):
        pytest.fail('Retained unknown reached a second prepare/admission/submit')
    for target, name in ((backend, 'prepare'), (backend, 'submit'), (breadth, 'admit'),
            (managed_execution.TCADManagedExecution, 'dispatch_lease'), (authority, '_remaining')):
        monkeypatch.setattr(target, name, forbidden)
    result = recovery.maintain(root, 6, call, row['snapshot_token'], action='discard-partial',
        max_seconds=20, authorization_note='Original unsealed snapshot only')
    assert result['status'] == 'partial_discarded'
    assert counter.read_text().splitlines() == ['prepare']
    assert e.main(['--config', str(active), 'run', '--payload', str(output), '--research', str(Path(active).with_name('research.json'))]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unknown' and result['call_id'] == call
    descriptor = LocalFileDescriptor(**c.read(Path(row['binding']['path']))['prepared'])
    with pytest.raises(e.ConcurrentExecution):
        e.Records(config).accept(descriptor, dict(parent=dict(candidate_id=None)), config['execution_interface'])
    assert counter.read_text().splitlines() == ['prepare']
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
    # Sealing retains the unavailable namespace as a fact; execution admission
    # still refuses it rather than inventing an empty namespace or another call.
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
@pytest.mark.parametrize('interruption', ['none', 'binding', 'rename'])
def test_unbound_local_accepted_publication_preserves_original_and_never_submits(tmp_path, monkeypatch, capsys, interruption):
    from scidiscovery.research_loop import breadth
    from scidiscovery.research_loop.local_experiments import LocalExecution
    root, _, config, _ = case(tmp_path, monkeypatch, executor='local_execution:command')
    payload = c.workspace(root, 2)/'code/local.py'
    payload.parent.mkdir(exist_ok=True)
    payload.write_text('raise AssertionError("Publication fixture must never execute")\n')
    with _local_writer(root, config, 'accepted', payload=payload) as (process, snapshot, counter):
        assert _unbound(root, snapshot, 'publish-accepted')['status'] == 'busy'
        process.kill();process.wait(timeout=5)
    accepted = (snapshot/'accepted.json').read_bytes()
    inode = snapshot.stat().st_dev, snapshot.stat().st_ino
    def forbidden(*args, **kwargs):
        pytest.fail('Local publication replayed preparation, admission or execution')
    for target, name in ((LocalExecution, 'prepare'), (LocalExecution, 'submit'), (breadth, 'admit')):
        monkeypatch.setattr(target, name, forbidden)
    assert _unbound(root, snapshot, 'inspect')['status'] == 'accepted_unpublished'
    with pytest.raises(recovery.PreparationError, match='cannot use unbound partial cleanup'):
        _unbound(root, snapshot, 'discard-partial')
    if interruption != 'none':
        with monkeypatch.context() as patch:
            module, name = (recovery, 'bind_snapshot') if interruption == 'binding' else (archive, '_publish')
            original = getattr(module, name)
            def interrupted(*args, **kwargs):
                value = original(*args, **kwargs)
                if interruption != 'binding' or kwargs.get('publish', True):
                    raise RuntimeError('Exact local mutation completed')
                return value
            patch.setattr(module, name, interrupted)
            with pytest.raises(RuntimeError, match='local mutation completed'):
                _unbound(root, snapshot, 'publish-accepted')
    assert _unbound_cli(root, snapshot, 'publish-accepted') == 0
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == ('already_published' if interruption == 'rename' else 'accepted_published')
    destination = e.Records(config).directory(result['call_id'])
    assert (destination.stat().st_dev, destination.stat().st_ino) == inode
    assert (destination/'accepted.json').read_bytes() == accepted
    row, = recovery.retained_preparations(root, Path(config['record_dir']))
    assert row['resolved'] is True
    assert not snapshot.exists() and counter.read_text().splitlines() == ['prepare']
    assert _unbound(root, snapshot, 'publish-accepted')['status'] == 'already_published'
