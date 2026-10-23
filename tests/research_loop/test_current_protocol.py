"""Current write authority and bounded archive reads; no legacy execution replay."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import (
    budget, candidate_audit, checkpoint, cli, continuation, contracts as c,
    controller, correction, delivery, experiments, history, native, protocol, runtime,
)
from scidiscovery.operations.execution import LocalFileDescriptor
from execution_fixtures import execution_registry


def tree(root):
    """Include new locks/directories and mode changes, not only selected JSON files."""
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        name = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if path.is_symlink():
            result[name] = ('link', str(path.readlink()), mode)
        elif path.is_file():
            result[name] = ('file', path.read_bytes(), mode)
        else:
            result[name] = ('directory', None, mode)
    return result


def archived_job(root, step, *, sealed=False):
    """Write preserved bytes, never call an old execution or scientific schema."""
    job = dict(step=step, role='W', handoff_protocol=13)
    c.write(root / 'jobs' / f'{step:03d}.json', job)
    record = c.host_record(c.workspace(root, step))
    record.mkdir(parents=True, exist_ok=True)
    if sealed:
        c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json')))
        c.write(record / 'science-output.json', dict(legacy_scientific_text='Preserved original; qualification is not reinterpreted.'))
        c.write(record / 'result.json', dict(legacy_result='Preserved original scientific envelope.'))
        c.write(record / 'usage.json', dict(input_tokens=10, cached_input_tokens=3, output_tokens=4))
        c.write(record / 'result-identity.json', dict(job_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json'),
             result_ref=c.file_ref(record / 'result.json'), raw_ref=c.file_ref(record / 'science-output.json')))
    return job, record


@pytest.fixture
def archive(tmp_path):
    root = tmp_path / 'archive'
    root.mkdir()
    settings = dict(handoff_protocol=13, goal_id='archived-original', max_steps=600,
                    objective='Keep original scientific limitations.', max_tokens=1000, max_seconds=1000)
    c.write(root / 'settings.json', settings)
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    job, record = archived_job(root, 1, sealed=True)
    c.write(root / 'pause.request', dict(reason='preserved_operator_pause'))
    c.write(root / 'host-history/pages/0001.json', dict(legacy_page='Retained unchanged.'))
    work = c.workspace(root, 1)
    work.mkdir(parents=True, exist_ok=True)
    (work / 'public.py').write_text('raise AssertionError("Archived work must never execute")\n')
    return root, job, record


@pytest.fixture
def forbid_effect(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('The retired-protocol guard must reject before any subprocess')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


@pytest.mark.parametrize('value', [None, 1, 8, 13, 14, 15, str(protocol.CURRENT), float(protocol.CURRENT), True])
def test_execution_scope_requires_exact_current_integer_without_mutation(value):
    scope = dict(handoff_protocol=value, preserved='original field')
    before = copy.deepcopy(scope)
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_scope(scope)
    assert scope == before


def test_current_instance_cannot_accept_old_retained_job_from_current_caller_copy(archive):
    root, job, _ = archive
    settings = c.settings(root)
    settings['handoff_protocol'] = protocol.CURRENT
    c.write(root / 'settings.json', settings)
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    caller = dict(job, handoff_protocol=protocol.CURRENT)
    before = tree(root)
    with pytest.raises(protocol.UnsupportedProtocol, match='Retained scientific job'):
        protocol.require_current(root, caller)
    assert tree(root) == before


OWNER_WRITES = {
    'checkpoint_end_stage': lambda root, job, record: checkpoint.end_stage(root,job,record,'archived_original'),
    'controller_run': lambda root, job, record: controller.run(root),
    'controller_closeout': lambda root, job, record: controller.closeout(root),
    'controller_resume': lambda root, job, record: controller.worker_resume(root, 1),
    'prepare_scientific_job': lambda root, job, record: c.build_job(root, 2, 'W', [1]),
    'seal_already_sealed_original': lambda root, job, record: c.seal_result(root, 1),
    'budget_transition': lambda root, job, record: budget.transition(root, 'advance', 'archive_must_not_advance'),
    'budget_amend': lambda root, job, record: budget.amend(root, max_tokens=2000, authorization_note='Fixture operator request'),
    'native_accept': lambda root, job, record: native.execute_native(root, 1),
    'native_runner': lambda root, job, record: native.run_native(root, 1),
    'runtime_start': lambda root, job, record: runtime.start(root, 1),
    'continuation': lambda root, job, record: continuation.start(root, 1),
    'checkpoint_write': lambda root, job, record: checkpoint.capture(root, job, record),
    'correction_publish': lambda root, job, record: correction.publish(root, 1),
    'audit_execution': lambda root, job, record: candidate_audit.execute(root, job, dict(next_action='audit', candidate_recommendation='original')),
    'delivery_sources': lambda root, job, record: delivery.freeze_sources(root, []),
    'delivery_submit': lambda root, job, record: delivery.invoke(root, dict(action_id='never-submitted'), 1),
    'history_refresh': lambda root, job, record: history.refresh(root, 2),
}


@pytest.mark.parametrize('entry', OWNER_WRITES, ids=list(OWNER_WRITES))
def test_archived_owner_write_rejects_before_any_tree_change(archive, forbid_effect, entry):
    root, job, record = archive
    before = tree(root)
    with pytest.raises(protocol.UnsupportedProtocol) as failure:
        OWNER_WRITES[entry](root, job, record)
    assert failure.value.code == 'unsupported_protocol'
    assert f'current research-loop protocol {protocol.CURRENT}' in str(failure.value)
    assert tree(root) == before


@pytest.mark.parametrize('command, extra', [
    ('run', []), ('continue', []), ('pause', []), ('worker-resume', ['--step', '1']),
    ('budget-amend', ['--max-tokens', '2000', '--authorization-note', 'Fixture operator request']),
    ('closeout', []),
])
def test_archived_cli_write_rejects_without_clearing_pause_or_creating_locks(archive, forbid_effect, capsys, command, extra):
    root, _, _ = archive
    before = tree(root)
    assert cli.main([command, '--root', str(root), *extra]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'error'
    assert f'current research-loop protocol {protocol.CURRENT}' in result['error']
    assert tree(root) == before


def test_archive_status_reads_exact_originals_without_schema_observer_or_cache_rebuild(archive, monkeypatch, capsys):
    root, _, _ = archive
    def forbidden(*args, **kwargs):
        raise AssertionError('Archive reads must not replay current scientific schemas or build derived history')
    monkeypatch.setattr(c, 'science_payload', forbidden)
    monkeypatch.setattr(c, 'result_schema', forbidden)
    monkeypatch.setattr(history, 'refresh', forbidden)
    monkeypatch.setattr(history, 'rebuild', forbidden)
    monkeypatch.setattr(history, 'read_adapter', forbidden)
    before = tree(root)
    direct = protocol.archive_status(root)
    assert direct['status'] == 'preserved_read_only' and direct['current_protocol'] == protocol.CURRENT
    assert direct['nodes'] == [dict(step=1, role='W', state='seal_present_originals_verified', scientific_qualification='not_reinterpreted')]
    assert direct['usage']['observed_tokens'] == 14 and direct['usage']['coverage'] == 'complete'
    assert cli.main(['status', '--root', str(root)]) == 0
    assert json.loads(capsys.readouterr().out) == direct
    assert tree(root) == before
    assert not (root / 'host-history/cache.json').exists()


def test_archive_node_truncation_is_explicit_partial_without_regeneration(archive):
    root, _, _ = archive
    for step in range(2, 258):
        archived_job(root, step)
    before = tree(root)
    result = protocol.archive_status(root)
    assert len(result['nodes']) == 256 and result['omitted_nodes'] == 1
    assert result['partial_usage'] and result['usage']['coverage'] == 'partial'
    assert result['goal_completion'] == 'not_asserted'
    assert tree(root) == before


def test_archive_native_record_truncation_is_explicit_partial(archive):
    root, _, record = archive
    # This is a bounded archive-reader fixture, not a claim that 257 old native
    # continuations were admissible under an old execution budget.
    for number in range(1, 258):
        child = record / 'continuations' / f'{number:03d}'
        c.write(child / 'native-intent.json', dict(preserved_attempt=number))
        c.write(child / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=0))
    before = tree(root)
    result = protocol.archive_status(root)
    assert result['omitted_native_records'] == 1 and result['omitted_nodes'] == 0
    assert result['partial_usage'] and result['usage']['coverage'] == 'partial'
    assert tree(root) == before


def test_archive_missing_usage_is_unknown_not_zero_or_complete(archive):
    root, _, record = archive
    (record / 'usage.json').unlink()
    before = tree(root)
    result = protocol.archive_status(root)
    assert result['usage']['unknown_steps'] == [1]
    assert result['usage']['coverage'] == 'partial' and result['partial_usage']
    assert tree(root) == before


def test_archive_original_tamper_is_reported_without_schema_repair_or_cache_write(archive):
    root, _, record = archive
    (record / 'science-output.json').write_text('{"changed_original":true}')
    before = tree(root)
    with pytest.raises(ValueError, match='Preserved scientific originals changed'):
        protocol.archive_status(root)
    assert tree(root) == before


def test_registered_scope_guard_precedes_factory_load_and_original_acceptance(archive, execution_registry, monkeypatch):
    root, job, _ = archive
    work = c.workspace(root, 1)
    digest = c.record_ref(root / 'jobs/001.json')
    scope = dict(step=1, role='W', job_ref=digest, execution_key=digest, mechanism_ref=None,
                 handoff_protocol=protocol.CURRENT)
    current = experiments.freeze(dict(record_dir=str(work / 'work/lineage'), scope=scope,
        native_attempt=dict(number=0, execution_key=digest),
        admission=dict(version=experiments.ADMISSION_VERSION, mode='hard'),
        run_argv=[sys.executable], cwd=str(work), max_seconds=1), 'local_execution:command')
    old = copy.deepcopy(current)
    old['scope']['handoff_protocol'] = 13
    script = work / 'public.py'
    payload = LocalFileDescriptor(name='not-accepted', local_path=str(script),
        size_bytes=script.stat().st_size, media_type='application/octet-stream')
    def forbidden(*args, **kwargs):
        raise AssertionError('Scope protocol must be checked before loading a factory')
    monkeypatch.setattr(experiments, '_adapter', forbidden)
    before = tree(root)
    for action in (lambda: experiments.load(old), lambda: experiments.dispatch_descriptor(old, payload)):
        with pytest.raises(protocol.UnsupportedProtocol):
            action()
        assert tree(root) == before
