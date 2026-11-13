"""CLI control receipts and exact next-task guidance, without model execution."""
import json
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import budget, contracts, controller, control_operations as controls, user_guidance as guidance
from test_history_handoff import finish


@pytest.fixture
def root(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Original bounded scientific objective.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=100,
        max_seconds=20, max_steps=3, model='fixture', reasoning_effort='high', search_policy=None)
    return root


def test_pending_guidance_edit_cancel_and_exact_new_role_boundary(root):
    pending = guidance.submit(root, 'First advice', target='W')
    edited = guidance.edit(root, pending['guidance_id'], 'Revised advice', expected_revision=1)
    with pytest.raises(ValueError, match='revision changed'):
        guidance.edit(root, pending['guidance_id'], 'Stale edit', expected_revision=1)
    h = contracts.build_job(root, 1, 'H', [])
    assert 'user_guidance' not in h
    finish(root, 1, 'H', [], action='experiment')
    w = contracts.build_job(root, 2, 'W', [])
    assert w['user_guidance'][0]['revision'] == edited['revision']
    assert w['user_guidance'][0]['text'] == 'Revised advice'
    original = (root / 'jobs/002.json').read_bytes()
    with pytest.raises(ValueError, match='immutable'):
        guidance.revoke(root, pending['guidance_id'], expected_revision=2)
    guidance.submit(root, 'Later advice', target='W')
    assert contracts.build_job(root, 2, 'W', []) == w
    assert (root / 'jobs/002.json').read_bytes() == original
    finish(root, 2, 'W', [])
    cancelled = guidance.submit(root, 'Do not apply', target='A')
    guidance.revoke(root, cancelled['guidance_id'], expected_revision=1)
    assert 'user_guidance' not in contracts.build_job(root, 3, 'A', [])
    public = contracts.read(contracts.workspace(root, 2) / 'science-task.json')['user_guidance']
    assert 'guidance_id' not in json.dumps(public)
    assert 'sha256' not in json.dumps(public)


def test_research_guidance_is_directly_visible_and_revoked_only_for_future_jobs(root):
    row = guidance.submit(root, 'Compare sensitivity throughout', scope='research')
    first = contracts.build_job(root, 1, 'H', [])
    finish(root, 1, 'H', [], action='experiment')
    second = contracts.build_job(root, 2, 'W', [])
    assert second['user_guidance'] == first['user_guidance']
    immutable = (root / 'host-control/guidance' / f'{row["guidance_id"]}.json').read_bytes()
    receipt = guidance.revoke(root, row['guidance_id'], expected_revision=1)
    assert receipt['effective_step'] == 3
    assert (root / 'host-control/guidance' / f'{row["guidance_id"]}.json').read_bytes() == immutable
    finish(root, 2, 'W', [])
    assert 'user_guidance' not in contracts.build_job(root, 3, 'A', [])
    assert contracts.build_job(root, 1, 'H', []) == first


def test_crash_after_job_publication_cannot_reedit_or_reapply(root):
    row = guidance.submit(root, 'Immutable advice', target='H')
    with pytest.raises(RuntimeError, match='crash'):
        with guidance.binding(root, 1, 'H', None) as values:
            contracts.write(root / 'jobs/001.json', dict(step=1, role='H', user_guidance=values))
            raise RuntimeError('crash after publication')
    with pytest.raises(ValueError, match='already bound'):
        guidance.edit(root, row['guidance_id'], 'Overwrite', expected_revision=1)
    with guidance.binding(root, 2, 'W', None) as values:
        assert values == []


def test_pending_edit_and_application_are_serialized(root):
    row = guidance.submit(root, 'Exact boundary text')
    entered = threading.Event()
    finished = threading.Event()
    errors = []

    def edit_pending():
        entered.set()
        try:
            guidance.edit(root, row['guidance_id'], 'Racing edit', expected_revision=1)
        except ValueError as error:
            errors.append(str(error))
        finally:
            finished.set()

    with guidance.binding(root, 1, 'H', None) as values:
        thread = threading.Thread(target=edit_pending)
        thread.start()
        assert entered.wait(2)
        assert not finished.wait(.05)
        contracts.write(root / 'jobs/001.json', dict(step=1, role='H', user_guidance=values))
    thread.join(3)
    assert finished.is_set()
    assert errors and 'immutable' in errors[0]
    assert contracts.read(root / 'jobs/001.json')['user_guidance'][0]['text'] == 'Exact boundary text'


def test_goal_change_requires_explicit_effective_stage_and_retains_original(root):
    with pytest.raises(ValueError, match='effective step'):
        guidance.submit(root, 'Changed goal', kind='goal_change')
    guidance.submit(root, 'Changed goal', kind='goal_change', effective_step=2)
    assert 'user_guidance' not in contracts.build_job(root, 1, 'H', [])
    finish(root, 1, 'H', [], action='analyze')
    second = contracts.build_job(root, 2, 'A', [])
    assert second['user_guidance'][0]['kind'] == 'goal_change'
    assert contracts.settings(root)['objective'] == 'Original bounded scientific objective.'


def test_queued_amendment_preserves_accepted_identity_and_is_idempotent(root):
    job = contracts.build_job(root, 1, 'H', [])
    job_bytes = (root / 'jobs/001.json').read_bytes()
    settings_bytes = (root / 'settings.json').read_bytes()
    with controller._writer(root):
        receipt = controls.request_budget_amendment(root, max_tokens=250, authorization_note='Explicit ceiling revision')
        assert receipt['status'] == 'accepted'
        assert budget.snapshot(root)['max_tokens'] == 100
        controls.apply_pending_budget(root)
        controls.apply_pending_budget(root)
    receipt = controls.operation(root, receipt['operation_id'])
    assert receipt['status'] == 'applied'
    assert budget.snapshot(root)['max_tokens'] == 250
    assert (root / 'settings.json').read_bytes() == settings_bytes
    assert (root / 'jobs/001.json').read_bytes() == job_bytes
    events, _ = budget._events(root)
    assert len([event for event in events if event['kind'] == 'budget_amend']) == 1
    # Crash after ledger publication but before the receipt update reconciles once.
    path = controls._path(root, receipt['operation_id'])
    contracts.write(path, dict(receipt, status='running'))
    with controller._writer(root):
        controls.apply_pending_budget(root)
    events, _ = budget._events(root)
    assert len([event for event in events if event['kind'] == 'budget_amend']) == 1


def test_amendment_stays_pending_while_original_effect_is_unknown(root, monkeypatch):
    monkeypatch.setattr(budget, 'execution_blocked', lambda root: True)
    receipt = controls.request_budget_amendment(root, max_tokens=250, authorization_note='Future ceiling only')
    assert receipt['status'] == 'accepted'
    with controller._writer(root):
        controls.apply_pending_budget(root)
    assert controls.operation(root, receipt['operation_id'])['status'] == 'accepted'
    assert budget.snapshot(root)['max_tokens'] == 100


def test_detached_resume_binds_original_and_keeps_the_existing_writer(root, monkeypatch):
    contracts.build_job(root, 1, 'H', [])
    original = (root / 'jobs/001.json').read_bytes()
    monkeypatch.setattr(controls.subprocess, 'Popen', lambda *args, **kwargs: SimpleNamespace(pid=os.getpid()))
    receipt = controls.start_background(root, resume_step=1)
    assert receipt['action'] == dict(kind='worker_resume', step=1, job_ref=contracts.record_ref(root / 'jobs/001.json'))
    seen = []

    def resume_locked(instance, step, *, expected_attempt=None):
        with pytest.raises(RuntimeError, match='running controller'):
            with controller._local_writer(instance):
                pass
        seen.append(step)
        return dict(status='worker_resume_unavailable', reason='Fixture original retained')

    monkeypatch.setattr(controller, '_worker_resume_locked', resume_locked)
    monkeypatch.setattr(controller, '_run', lambda root: pytest.fail('Resume must use the original continuation gate'))
    descriptor = os.open(root / 'host-run.lock', os.O_RDWR)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    controls._child(root, receipt['operation_id'], descriptor)
    final = controls.operation(root, receipt['operation_id'])
    assert seen == [1]
    assert final['status'] == 'applied'
    assert (root / 'jobs/001.json').read_bytes() == original


def test_worker_resume_keeps_unknown_original_ineligible(root, monkeypatch):
    from scidiscovery.research_loop import continuation
    contracts.build_job(root, 1, 'H', [])
    original = (root / 'jobs/001.json').read_bytes()

    def reject_unknown(root, step, *, expected_attempt=None):
        raise continuation.ResumeUnavailable('worker_resume_usage_unknown')

    monkeypatch.setattr(continuation, 'start', reject_unknown)
    monkeypatch.setattr(controller, '_run', lambda root: pytest.fail('Unknown original must not advance'))
    result = controller.worker_resume(root, 1)
    assert result['status'] == 'worker_resume_unavailable'
    assert result['reason'] == 'worker_resume_usage_unknown'
    assert (root / 'jobs/001.json').read_bytes() == original


def test_busy_writer_does_not_attribute_missing_process_receipt_to_current_owner(root, monkeypatch):
    stale = controls._new('controller', action=dict(kind='run'))
    path = controls._path(root, stale['operation_id'])
    contracts.write(path, stale)
    original = path.read_bytes()
    monkeypatch.setattr(controls.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('Busy writer must not dispatch'))
    with controller._writer(root):
        assert controls.operation(root, stale['operation_id']) == stale
        duplicate = controls.start_background(root)
        assert duplicate == dict(operation_id=None, kind='controller', status='running',
                                 already_running=True, owner='existing_controller')
        assert path.read_bytes() == original
    reconciled = controls.operation(root, stale['operation_id'])
    assert reconciled['operation_id'] == stale['operation_id'] and reconciled['status'] == 'failed'
    assert 'requires reconciliation' in reconciled['error']


def test_detached_child_publication_window_retains_accepted_receipt(root, monkeypatch):
    seen = []

    def launch(command, **options):
        operation_id = command[4]
        value = controls.operation(root, operation_id)
        assert value['status'] == 'accepted' and 'process' not in value
        assert controls.start_background(root)['operation_id'] is None
        seen.append(operation_id)
        return SimpleNamespace(pid=os.getpid())

    monkeypatch.setattr(controls.subprocess, 'Popen', launch)
    receipt = controls.start_background(root)
    assert seen == [receipt['operation_id']]
    assert receipt['status'] == 'accepted' and receipt['process']['pid'] == os.getpid()


@pytest.mark.process_e2e
def test_killed_pre_popen_starter_receipt_reconciles_without_dispatch(root, tmp_path):
    # Kill the real starter after its receipt is durable, before Popen executes.
    script = r'''
import json, os, pathlib, signal, sys
from scidiscovery.research_loop import control_operations as controls, contracts
root, witness = map(pathlib.Path, sys.argv[1:])
def die_before_popen(command, **options):
    assert command[:3] == [sys.executable, '-m', controls.__name__]
    operation_id = command[4]
    path = controls._path(root, operation_id)
    value = contracts.read(path)
    assert value['status'] == 'accepted' and 'process' not in value
    with witness.open('x') as stream:
        json.dump(dict(operation_id=operation_id, record=contracts.record_ref(path)), stream)
        stream.flush(); os.fsync(stream.fileno())
    os.kill(os.getpid(), signal.SIGKILL)
controls.subprocess.Popen = die_before_popen
controls.start_background(root)
raise AssertionError('Killed starter returned')
'''
    source = Path(contracts.__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(source), *map(str, sys.path)]))
    witness = tmp_path / 'starter-boundary.json'
    starter = subprocess.run([sys.executable, '-c', script, str(root), str(witness)],
                             env=env, capture_output=True, text=True, timeout=10)
    assert starter.returncode == -signal.SIGKILL, (starter.stdout, starter.stderr)
    boundary = contracts.read(witness)
    path = controls._path(root, boundary['operation_id'])
    assert contracts.record_ref(path) == boundary['record']
    original = contracts.read(path)
    assert original['status'] == 'accepted' and 'process' not in original
    final = None
    for _ in range(2):
        query = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli', 'operations',
            '--root', str(root), '--operation-id', boundary['operation_id']],
            env=env, capture_output=True, text=True, timeout=10)
        assert query.returncode == 2, (query.stdout, query.stderr)
        observed = json.loads(query.stdout)
        assert observed['status'] == 'failed' and 'process' not in observed
        assert observed['operation_id'] == original['operation_id']
        assert observed['action'] == original['action']
        assert 'requires reconciliation' in observed['error']
        assert 'does not prove non-submission' in observed['error']
        if final is not None:
            assert path.read_bytes() == final
        final = path.read_bytes()
    assert not (root / 'jobs').exists()
    assert not (root / 'native-evidence/001/native-intent.json').exists()


@pytest.mark.process_e2e
def test_background_controller_survives_starter_and_serializes_duplicate_start(root, tmp_path):
    from scidiscovery.research_loop import configuration_updates
    state=configuration_updates.status(root)
    document=state['configuration'];document['agent']['model']='detached-next-model'
    configuration_updates.submit(root,document,expected_revision=state['revision'])
    # A process-only stand-in owns no scientific result, model or external call.
    shim = tmp_path / 'shim'
    shim.mkdir()
    (shim / 'sitecustomize.py').write_text('''
import time
from scidiscovery.research_loop import controller,configuration_updates,candidate_audit,contracts
def bounded_control(root):
    controller.require_writer(root)
    candidate_audit.settle_preflights(root)
    revision=configuration_updates.bind_new_job(root,1)
    job=contracts.build_job(root,1,'H',[])
    assert job.get('configuration_revision')==revision
    (root / 'entered').touch()
    deadline = time.monotonic() + 15
    while not (root / 'release').exists() and time.monotonic() < deadline:
        time.sleep(.02)
    return dict(status='wait', reason='Process lifetime fixture')
controller._run = bounded_control
''')
    source = Path(contracts.__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(shim), str(source)]))
    script = 'import json,sys; from pathlib import Path; from scidiscovery.research_loop.control_operations import start_background; print(json.dumps(start_background(Path(sys.argv[1]))))'
    try:
        starter = subprocess.run([sys.executable, '-c', script, str(root)], env=env,
                                 capture_output=True, text=True, timeout=10, check=True)
        receipt = json.loads(starter.stdout)
        deadline = time.monotonic() + 5
        while not (root / 'entered').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (root / 'entered').exists()
        assert contracts.read(root/'jobs/001.json')['configuration_revision']['revision']==1
        assert configuration_updates.for_step(root,1)['model']=='detached-next-model'
        duplicate = controls.start_background(root)
        assert duplicate['operation_id'] == receipt['operation_id']
        assert duplicate['already_running'] is True
        with pytest.raises(RuntimeError, match='running controller'):
            controller.run(root)
    finally:
        (root / 'release').touch()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        final = controls.operation(root, receipt['operation_id'])
        if final['status'] not in {'accepted', 'running'}:
            break
        time.sleep(.02)
    assert final['status'] == 'applied', final
