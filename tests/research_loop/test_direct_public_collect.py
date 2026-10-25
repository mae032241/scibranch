"""Local originals use the public root already bound before backend output."""
from copy import deepcopy
from pathlib import Path

import pytest

from execution_fixtures import execution_registry
from test_mutable_run_inputs import fixed_code, terminal_code
from test_worker_fragments import fragment
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (contracts as c, experiments as e,
    execution, local_experiments, native, runtime, shared_input_views)
from scidiscovery.research_loop.local_experiments import LocalExecution


def inode(path):
    info = Path(path).stat()
    return info.st_dev, info.st_ino


def replace_equal_bytes(path):
    """Preserve size and mtime, changing only the original entity."""
    import os
    path = Path(path)
    saved = path.stat()
    replacement = path.with_name(path.name + '.replacement')
    replacement.write_bytes(path.read_bytes())
    os.utime(replacement, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    replacement.chmod(saved.st_mode & 0o777)
    replacement.replace(path)


def stopped_attempt(root, job, record, monkeypatch):
    """Inject physical stop facts while retaining the real stop validator."""
    identity = execution.identity(root, job['step'], 0)
    receipts = {}
    for number, name in enumerate(('runner-process.json', 'native-process.json', 'bridge-process.json'), 1):
        pid = 900000000 + number
        receipt = dict(pid=pid, start_ticks=1, pgid=pid, sid=pid,
            boot_id='batch-a-physical-state-fixture', task_ref=identity)
        c.write(record / name, receipt)
        receipts[name] = receipt
    c.write(record / 'bridge-outcome.json',
        dict(process=receipts['bridge-process.json'], cleanup='observed_group_cleaned'))
    previous = c.read(record / 'native-outcome.json')
    c.write(record / 'native-outcome.json', dict(previous, native_started=True,
        cleanup='observed_group_cleaned', usage=native.usage(root),
        execution_processes={name: c.record_ref(record / name)
            for name in ('runner-process.json', 'native-process.json')}))
    live = set()
    def process_matches(receipt, expected):
        assert receipt['task_ref'] == expected
        return receipt['pid'] in live
    monkeypatch.setattr(runtime, 'process_matches', process_matches)
    return live, receipts


@pytest.fixture
def observed_local(terminal_code):
    """Use the real preparation, completion and observation producers."""
    def create(**options):
        row = terminal_code(**options)
        row['observation'] = e.record_observation(
            row['config'], row['adapter'], row['accepted']['call_id'])
        return row
    return create


def test_collect_returns_the_declared_actual_public_original(fragment, terminal_code):
    root, job, _ = fragment
    work = c.workspace(root, job['step'])
    public = shared_input_views.dynamic_root(root, work, 'retained-experiments')
    bound_inode = inode(public)
    mounts = shared_input_views.mount_arguments(root, job, work, include_skills=False)
    assert any(mounts[index:index + 3] == ['--ro-bind', str(public), str(public)]
        for index in range(0, len(mounts), 3))

    row = terminal_code()
    config, adapter, accepted = row['config'], row['adapter'], row['accepted']
    call_id = accepted['call_id']
    assert row['output'].parent == public / call_id / 'outputs'
    assert local_experiments.artifact_directory(config, call_id) == row['output'].parent
    assert inode(public) == bound_inode

    # Exercise the exact Local output-target guard while the prepared export
    # still exists. The public root is already a readonly Worker mount.
    prepared = LocalFileDescriptor.model_validate(accepted['prepared'])
    protected = LocalExecution(dict(adapter.config, cwd=str(public)),
        frozen_interface=config['execution_interface'])
    with pytest.raises(ValueError, match='readonly|read-only|output'):
        protected.acceptance_metadata(prepared,
            command_options=dict(source_script=accepted['source_script']),
            _code_version=accepted['code_version'])
    with pytest.raises(ValueError, match='readonly|read-only|output'):
        adapter.acceptance_metadata(prepared,
            command_options=dict(source_script=accepted['source_script'],
                experiment_directory=str(public)),
            _code_version=accepted['code_version'])

    observed = e.record_observation(config, adapter, call_id)
    saved = {item['name']: item for item in observed['scientific_outputs']}
    first = e._collected_view(config, call_id, observed, adapter)
    second = e._collected_view(config, call_id, observed, adapter)
    assert first == second and first['status'] == 'collected'
    assert {item['name'] for item in first['outputs']} == set(saved)
    for item in first['outputs']:
        original = saved[item['name']]
        assert item['path'] == original['path'] == original['source']['path']
        assert Path(item['path']).is_relative_to(public / call_id / 'outputs')
        assert e.proof(item['path']) == {key: original[key]
            for key in ('path', 'size_bytes', 'file_stat')}
    assert not (row['directory'] / 'outputs').exists()
    assert not (row['directory'] / 'observed-originals').exists()
    assert inode(public) == bound_inode

    # Corrupt only the selected mapping of a producer-created observation.
    # Equal-looking private bytes do not declare a public publication location.
    private = row['directory'] / 'private-result.txt'
    private.write_bytes(row['output'].read_bytes())
    tampered = deepcopy(observed)
    selected = next(item for item in tampered['scientific_outputs'] if item['name'] == 'result.txt')
    selected.update(e.proof(private), source=e.proof(private))
    selected['descriptor']['local_path'] = str(private)
    with pytest.raises((e.IntegrityError, e.UnsupportedExecution), match='public|original|projection'):
        e._collected_view(config, call_id, tampered, adapter)
