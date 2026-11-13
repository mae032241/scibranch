"""Host cooperation at exact fork source identity; no scientific process runs."""
import hashlib
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (budget, configuration_updates as updates,
    contracts as c, controller, execution_authority as authority, instance_archive as archive,
    instance_owner as owner)


@pytest.fixture
def gates(tmp_path, monkeypatch):
    directory = tmp_path / 'host-private-gates'
    monkeypatch.setattr(owner, '_gate_directory', lambda: directory)
    return directory


@pytest.fixture
def instance(tmp_path, gates):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact original scientific inputs.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=2000,
        max_seconds=60, max_steps=4, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external')
    return root


def _gate_path(root, directory):
    identity = owner._identity(root)
    return directory / f'{identity["dev"]}-{identity["ino"]}.lock'


def test_read_only_owner_and_missing_gate_probe_do_not_create(tmp_path, gates):
    root = tmp_path / 'source'
    root.mkdir()
    assert owner.require_owner(root) is None
    with owner.source_lease(root, create=False) as value:
        assert value is None
    assert not gates.exists()
    assert list(root.iterdir()) == []
    gates.mkdir(mode=0o700)
    with owner.source_lease(root, create=False) as value:
        assert value is None
    assert list(gates.iterdir()) == []
    assert list(root.iterdir()) == []


def test_fixed_namespace_ignores_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('TMPDIR', str(tmp_path / 'alternate'))
    assert owner._gate_directory() == Path('/tmp') / f'scidiscovery-owner-gates-{os.geteuid()}'


def test_nested_effect_shares_same_descriptor_and_source_ex_rejects_mutation(instance, gates):
    root = instance
    @owner.effect
    def inner(root):
        return owner._gates.get()[str(root)]['fd']
    with owner.lease(root):
        descriptor = owner._gates.get()[str(root)]['fd']
        assert inner(root) == descriptor
        with pytest.raises(owner.files.ArchiveError):
            with owner.source_lease(root):
                pass
    original = _gate_path(root, gates).stat().st_ino
    with owner.source_lease(root):
        with pytest.raises(owner.files.ArchiveError, match='Source read guard'):
            inner(root)
        assert owner.require_owner(root) is None
    with owner.lease(root):
        assert inner(root) == owner._gates.get()[str(root)]['fd']
    assert _gate_path(root, gates).stat().st_ino == original


@pytest.mark.parametrize('operation,local', [
    ('configuration', 'configuration.lock'), ('ledger', 'control-ledger.lock'),
    ('authority', 'execution-authority/writer.lock')])
def test_legacy_first_local_writer_is_fenced(instance, operation, local):
    root = instance
    document = updates.status(root)['configuration']
    document['agent']['model'] = 'changed'
    (root / 'execution-authority').mkdir(exist_ok=True)
    assert not (root / owner.BINDING).exists()
    assert not (root / local).exists()
    if operation == 'ledger':
        c.write(root / 'host-started.json', budget.clock_stamp())
    def mutate():
        if operation == 'configuration':
            return updates.submit(root, document, expected_revision=0)
        if operation == 'ledger':
            return budget.transition(root, 'paused', 'Fixture explicit inactive boundary')
        else:
            with authority.writer(root):
                (root / 'fixture-authority-effect').write_text('effect')
    with owner.source_lease(root):
        with pytest.raises(owner.files.ArchiveError, match='Source read guard'):
            mutate()
        assert not (root / local).exists()
    mutate()
    assert (root / local).is_file()
    if operation == 'configuration':
        assert updates.current(root)['model'] == 'changed'


@pytest.mark.parametrize('unsafe', ['directory_mode', 'directory_owner', 'directory_symlink',
                                   'lock_mode', 'lock_owner', 'lock_symlink', 'lock_hardlink'])
def test_unsafe_host_gate_is_rejected(tmp_path, gates, monkeypatch, unsafe):
    root = tmp_path / 'source'; root.mkdir()
    gates.mkdir(mode=0o700)
    lock = _gate_path(root, gates)
    if unsafe == 'directory_mode':
        gates.chmod(0o755)
    elif unsafe == 'directory_owner':
        monkeypatch.setattr(owner.os, 'geteuid', lambda: gates.stat().st_uid + 1)
    elif unsafe == 'directory_symlink':
        gates.rmdir()
        target = tmp_path / 'target'; target.mkdir(mode=0o700)
        gates.symlink_to(target, target_is_directory=True)
    else:
        lock.touch(mode=0o600)
        if unsafe == 'lock_mode':
            lock.chmod(0o644)
        elif unsafe == 'lock_owner':
            real_fstat = owner.os.fstat
            def wrong_owner(fd):
                value = real_fstat(fd)
                if value.st_ino == lock.stat().st_ino:
                    values = list(value); values[4] = value.st_uid + 1
                    return os.stat_result(values)
                return value
            monkeypatch.setattr(owner.os, 'fstat', wrong_owner)
        elif unsafe == 'lock_symlink':
            lock.unlink()
            target = tmp_path / 'target'; target.touch(mode=0o600)
            lock.symlink_to(target)
        elif unsafe == 'lock_hardlink':
            os.link(lock, tmp_path / 'second-link')
    with pytest.raises((owner.files.ArchiveError, OSError)):
        with owner.source_lease(root):
            pytest.fail('Unsafe host coordination admitted')
    assert list(root.iterdir()) == []


def test_source_directory_replacement_is_rejected_on_exit(tmp_path, gates):
    root = tmp_path / 'source'; root.mkdir()
    with pytest.raises(owner.files.ArchiveError, match='original instance directory'):
        with owner.source_lease(root):
            root.rename(tmp_path / 'original')
            root.mkdir()


_CHILD = '''
import sys
from pathlib import Path
from scidiscovery.research_loop import instance_owner as owner, configuration_updates as updates
root, directory = map(Path, sys.argv[1:3])
owner._gate_directory = lambda: directory
try:
    if sys.argv[3] == 'hold':
        with owner.lease(root):
            print('held', flush=True)
            sys.stdin.readline()
    elif sys.argv[3] == 'configuration':
        document = updates.status(root)['configuration']
        document['agent']['model'] = 'child-model'
        updates.submit(root, document, expected_revision=0)
    else:
        with owner.lease(root):
            pass
except owner.files.ArchiveError as error:
    print(str(error))
    sys.exit(23)
'''


def _child(root, gates, operation='lease'):
    return subprocess.run([sys.executable, '-c', _CHILD, str(root), str(gates), operation],
        env=dict(os.environ, PYTHONPATH=str(Path(owner.__file__).resolve().parents[2])),
        capture_output=True, text=True, timeout=10)


@pytest.mark.process_e2e
def test_child_first_configuration_mutation_cannot_cross_source_ex(instance, gates):
    root = instance
    assert not (root / 'configuration.lock').exists()
    with owner.source_lease(root):
        result = _child(root, gates, 'configuration')
        assert result.returncode == 23, result.stderr
        assert 'source read guard is active' in result.stdout
        assert not (root / 'configuration.lock').exists()
        assert updates.revisions(root) == []
    assert _child(root, gates, 'configuration').returncode == 0
    assert updates.current(root)['model'] == 'child-model'


@pytest.mark.process_e2e
def test_read_only_source_probe_rejects_child_effect_without_new_files(instance, gates):
    process = subprocess.Popen([sys.executable, '-c', _CHILD, str(instance), str(gates), 'hold'],
        env=dict(os.environ, PYTHONPATH=str(Path(owner.__file__).resolve().parents[2])),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([process.stdout], [], [], 5)[0], 'Effect child did not become ready'
        assert process.stdout.readline() == 'held\n'
        before = {path.name: path.stat().st_ino for path in gates.iterdir()}
        with pytest.raises(owner.files.ArchiveError, match='effect is still active'):
            with owner.source_lease(instance, create=False):
                pass
        assert {path.name: path.stat().st_ino for path in gates.iterdir()} == before
    finally:
        try:
            stdout, stderr = process.communicate('release\n', timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    with owner.source_lease(instance, create=False):
        pass
