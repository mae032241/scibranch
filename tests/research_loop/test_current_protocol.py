"""Current format admission; old instances and job views are unsupported."""
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
from test_history_handoff import scene


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


@pytest.mark.parametrize('value', [None, 1, 8, 13, 14, 15, protocol.CURRENT - 1, str(protocol.CURRENT), float(protocol.CURRENT), True])
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
    settings['node_budget_mode'] = 'soft'
    settings['final_a_completion_policy'] = dict(version=1)
    c.write(root / 'settings.json', settings)
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    caller = dict(job, handoff_protocol=protocol.CURRENT)
    before = tree(root)
    with pytest.raises(protocol.UnsupportedProtocol, match='requires current research-loop protocol'):
        protocol.require_current(root, caller)
    assert tree(root) == before


# Checkpoint capture is private to an accepted stopped-attempt inventory. Its
# public admission is covered by the native/continuation entries below; the
# independent stage-end writer still rejects before consuming its inventory.
OWNER_WRITES = {
    'checkpoint_end_stage': lambda root, job, record: checkpoint.end_stage(root,job,record,'archived_original',inventory=None),
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
    'correction_publish': lambda root, job, record: correction.publish(root, 1),
    'audit_execution': lambda root, job, record: candidate_audit.execute(root, job, dict(next_action='audit', candidate_recommendation='original')),
    'delivery_sources': lambda root, job, record: delivery.freeze_sources(root, []),
    'delivery_submit': lambda root, job, record: delivery.invoke(root, dict(action_id='never-submitted'), 1),
    'history_audit_projection': lambda root, job, record: history.render_audit_overview(root, 2),
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


@pytest.mark.parametrize('reader', ['direct', 'cli'])
def test_old_instance_status_rejects_before_reading_jobs_or_rebuilding_views(
        archive, monkeypatch, capsys, reader):
    root, _, _ = archive
    from scidiscovery.research_loop import instance_control
    original_read = c.read
    def read(path):
        if Path(path).is_relative_to(root / 'jobs'):
            pytest.fail('Unsupported instance status opened an old scientific job')
        return original_read(path)
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported instance status interpreted old scientific originals')
    monkeypatch.setattr(c, 'read', read)
    for name in ('science_payload', 'result_schema'):
        monkeypatch.setattr(c, name, forbidden)
    for name in ('refresh', 'rebuild', 'read_adapter'):
        monkeypatch.setattr(history, name, forbidden)
    before = tree(root)
    if reader == 'direct':
        with pytest.raises(protocol.UnsupportedProtocol, match='Instance'):
            instance_control.status(root)
    else:
        assert cli.main(['status', '--root', str(root)]) == 1
        result = json.loads(capsys.readouterr().out)
        assert result['status'] == 'error' and result['code'] == 'unsupported_protocol'
    assert tree(root) == before


@pytest.mark.parametrize('entry', ['status', 'prepare', 'invoke'])
@pytest.mark.parametrize('retired_protocol', [30, 31, 32])
def test_retired_hwa_protocol_rejects_before_scientific_interpretation_or_writes(
        scene, forbid_effect, monkeypatch, entry, retired_protocol):
    """Synthetic compatibility originals; not qualification of an old scientific seal."""
    from scidiscovery.research_loop import instance_control
    root = scene
    job = c.build_job(root, 1, 'H', [])
    settings = c.settings(root)
    settings['handoff_protocol'] = retired_protocol
    job['handoff_protocol'] = retired_protocol
    c.write(root / 'settings.json', settings)
    c.write(root / 'jobs/001.json', job)
    before = tree(root)
    def forbidden(*args, **kwargs):
        pytest.fail('Retired HWA originals must not be scientifically reinterpreted')
    monkeypatch.setattr(c, 'science_payload', forbidden)
    monkeypatch.setattr(c, 'result_schema', forbidden)
    operation = {
        'status': lambda: instance_control.status(root),
        'prepare': lambda: c.build_job(root, 2, 'W', [1]),
        'invoke': lambda: native.execute_native(root, 1),
    }[entry]
    with pytest.raises(protocol.UnsupportedProtocol, match='Instance') as failure:
        operation()
    assert failure.value.code == 'unsupported_protocol'
    assert tree(root) == before


@pytest.mark.parametrize('field,value', [
    ('worker_view_version', None), ('worker_view_version', 0), ('worker_view_version', True),
    ('lineage_projection_version', None), ('lineage_projection_version', 0),
])
def test_retained_old_job_view_rejects_without_upgrade(scene, monkeypatch, field, value):
    root = scene
    job = c.build_job(root, 1, 'H', [])
    if value is None:
        job.pop(field)
    else:
        job[field] = value
    c.write(root / 'jobs/001.json', job)
    before = tree(root)
    from scidiscovery.research_loop import accepted_prefix, handoff_state
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported retained view must reject before reconciliation or material expansion')
    monkeypatch.setattr(accepted_prefix, 'reconcile', forbidden)
    monkeypatch.setattr(handoff_state, 'material_inputs', forbidden)
    for operation in (lambda: protocol.require_current(root, job),
            lambda: protocol.stage_scope(root, job), lambda: c.build_job(root, 1, 'H', [])):
        with pytest.raises(protocol.UnsupportedProtocol, match='Scientific job view'):
            operation()
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
    from scidiscovery.research_loop import experiment_client
    monkeypatch.setattr(experiment_client, 'load_client_facts', forbidden)
    before = tree(root)
    for action in (lambda: experiments.load(old), lambda: experiments.dispatch_descriptor(old, payload)):
        with pytest.raises(protocol.UnsupportedProtocol):
            action()
        assert tree(root) == before
