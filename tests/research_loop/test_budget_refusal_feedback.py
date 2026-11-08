"""Budget refusal is a preacceptance control fact, never an invented review."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry, remote_registry
from scidiscovery.research_loop import (breadth, budget, experiment_client, experiments as e,
    instance_owner, protocol, replay_check)


def scope():
    """An explicit standalone task identity, not a scope-validation bypass."""
    return dict(handoff_protocol=protocol.CURRENT, step=2, role='W',
        job_ref=dict(path='/fixture/jobs/002.json'),
        execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    config = dict(record_dir=str(tmp_path / 'records'), scope=scope())
    # Reach dispatch rather than the CLI's missing-input caller-error branch.
    (tmp_path / 'science.py').write_text('pass\n')
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(instance_owner, 'lease_for_configuration', lambda *args: nullcontext())
    monkeypatch.setattr(e, 'load', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(e, '_diagnostic', lambda error, note, **kwargs: note)

    def invoke(dispatch):
        entered = []
        def operation(*args, **kwargs):
            entered.append(True)
            return dispatch(*args, **kwargs)
        monkeypatch.setattr(e, '_dispatch_descriptor_operation', operation)
        code = e.main(['--config', str(path), 'run', '--script', str(tmp_path / 'science.py')])
        assert entered == [True], 'The test must reach dispatch, not a caller-input refusal'
        return code, json.loads(capsys.readouterr().out)
    return config, invoke


@pytest.mark.parametrize('host_reason,status,reason', [
    ('review_deadline_reached', 'budget_exhausted', 'shared_time_exhausted'),
    ('reserve_reached', 'budget_exhausted', 'shared_budget_exhausted'),
    ('shared_usage_unknown', 'budget_unavailable', 'shared_usage_unknown'),
])
def test_host_budget_refusal_reaches_cli_before_preparation(cli, tmp_path, monkeypatch,
        host_reason, status, reason):
    config, invoke = cli
    records = e.Records(config)
    observed = e._review_observation(dict(deadline_boottime=0 if host_reason=='review_deadline_reached' else 100,
        valid_until_boottime=100, pending=True, reason=host_reason), dict(at_boottime=5))
    monkeypatch.setattr(e, 'review_status', lambda *args: observed)
    monkeypatch.setattr(records, 'calls', lambda: [])
    monkeypatch.setattr(records, 'preflight', lambda **kwargs: records.admission_binding(None, expected_calls=[]))
    adapter = SimpleNamespace(prepare=lambda *args, **kwargs: pytest.fail('Refused work must not prepare'))
    payload = SimpleNamespace(local_path=str(tmp_path / 'science.py'))

    def dispatch(*args, **kwargs):
        return e._prepare_descriptor(config, payload, adapter, records, [],
            client_facts=experiment_client.load_client_facts(config))
    code, result = invoke(dispatch)
    assert code == 2
    assert result['status'] == status and result['reason'] == reason
    assert result['accepted'] is False and 'call_id' not in result
    assert result['handoff']['action'] == 'finalize_response'
    assert 'review_required' not in json.dumps(result)
    assert not (tmp_path / 'records').exists()


@pytest.mark.parametrize('reason', [
    'target_tokens_reached', 'target_time_reached',
    'host_review_observation_expired', 'host_review_observation_unavailable',
])
def test_advisory_observations_do_not_refuse_new_work(tmp_path, monkeypatch, reason):
    records = e.Records(dict(record_dir=str(tmp_path / 'records'), scope=scope()))
    value = None if reason=='host_review_observation_unavailable' else dict(deadline_boottime=100,
        valid_until_boottime=0 if reason=='host_review_observation_expired' else 100,
        pending=True, reason=reason)
    observed = e._review_observation(value, dict(at_boottime=5))
    monkeypatch.setattr(e, 'review_status', lambda *args: observed)
    monkeypatch.setattr(records, 'calls', lambda: [])
    admitted = []
    monkeypatch.setattr(breadth, 'admit', lambda *args: admitted.append(args) or 'binding')
    assert records.admission_binding(None, expected_calls=[]) == 'binding'
    assert len(admitted) == 1


def test_formal_review_refusal_remains_separate(cli, monkeypatch):
    config, invoke = cli
    records = e.Records(config)
    monkeypatch.setattr(records, 'calls', lambda: [])
    monkeypatch.setattr(e, 'review_status', lambda *args: dict(requested=True, enforced=True,
        reason='declared_independent_review_missing'))
    def dispatch(*args, **kwargs):
        try:records.admission_binding(None, expected_calls=[])
        except e.ReviewRequired as error:e._raise_admission_refusal(error)
    code, result = invoke(dispatch)
    assert code == 2 and result['status'] == 'review_required' and result['accepted'] is False
    assert result['handoff']['action'] == 'reconcile_or_stop'


@pytest.mark.parametrize('error,expected_code,expected_status', [
    (e.ReviewRequired('review_deadline_reached private/provider/path'), 3, 'unknown'),
    (e.ReviewRequired('reserve_reached private/provider/path'), 3, 'unknown'),
    (ValueError('shared_usage_unknown private/provider/path'), 2, 'unresolved'),
])
def test_provider_exception_text_cannot_claim_budget_refusal(cli, error, expected_code, expected_status):
    _, invoke = cli
    def dispatch(*args, **kwargs):raise error
    code, result = invoke(dispatch)
    assert code == expected_code and result['status'] == expected_status
    assert result['status'] not in {'budget_exhausted', 'budget_unavailable', 'review_required'}
    assert 'accepted' not in result and 'private/provider/path' not in json.dumps(result)
    assert result['handoff']['action'] != 'finalize_response'


@pytest.mark.parametrize('error', [
    e.ReviewRequired('review_deadline_reached'),
    e._BudgetAdmissionUnavailable('review_deadline_reached'),
    e._AdmissionBudgetRefused('reserve_reached'),
])
def test_accepted_provider_refusal_keeps_original_unknown(tmp_path, monkeypatch, error, remote_registry):
    scope = dict(handoff_protocol=protocol.CURRENT, step=2, role='W',
        job_ref=dict(path='/fixture/jobs/002.json'),
        execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)
    config = e.freeze(dict(record_dir=str(tmp_path / 'records'), scope=scope,
        admission=dict(version=1, mode='hard'), max_seconds=10), 'remote_execution:fixture')
    script = tmp_path / 'science.json';script.write_text('{"message":"remote-original"}\n')
    prepared = e.LocalFileDescriptor(name='original-call', local_path=str(script),
        size_bytes=script.stat().st_size, media_type='application/json')
    records = e.Records(config)
    adapter = e.load(config)
    accepted = records.accept(prepared, adapter.acceptance_metadata(prepared), config['execution_interface'],
        expected_calls=[])
    original = (records.directory(prepared.name) / 'accepted.json').read_bytes()
    submissions = []
    def submit(descriptor):
        submissions.append(descriptor.name)
        raise error
    monkeypatch.setattr(adapter, 'submit', submit)
    monkeypatch.setattr(adapter, 'status_details', lambda call: dict(state='unknown', call_id=call))
    monkeypatch.setattr(e, 'load', lambda *args, **kwargs: adapter)
    result = e._submit_descriptor(config, adapter, e.lifecycle_capabilities(adapter), prepared, accepted)
    assert result['status'] == 'unknown' and result['call_id'] == prepared.name
    assert result['accepted'] is True
    assert not {'reason', 'budget_exhausted', 'budget_unavailable'}.intersection(result)
    assert 'private/provider/path' not in json.dumps(result)
    assert 'Never resubmit' in result['note']
    # A second run must reconcile this exact accepted original, not call submit.
    blocked = e._original_dispatch_state(config, prepared)
    assert blocked['status'] == 'unknown' and blocked['call_id'] == prepared.name
    assert submissions == [prepared.name] and records.external(prepared.name) is None
    assert (records.directory(prepared.name) / 'accepted.json').read_bytes() == original
    assert records.calls() == [records.directory(prepared.name)]


def test_external_preparation_before_budget_refusal_stays_unknown(tmp_path, monkeypatch):
    script = tmp_path / 'science.py'
    script.write_text('pass\n')
    config = dict(record_dir=str(tmp_path / 'records'), scope=scope(), max_seconds=10,
        execution_interface=dict(executor='external:fixture'))
    payload = e.LocalFileDescriptor(name='input', local_path=str(script),
        size_bytes=script.stat().st_size, media_type='text/plain')
    records = e.Records(config)
    monkeypatch.setattr(records, 'calls', lambda: [])
    monkeypatch.setattr(records, 'preflight', lambda **kwargs: None)
    def refuse(*args, **kwargs):raise e._BudgetAdmissionUnavailable('reserve_reached')
    monkeypatch.setattr(records, '_accept', refuse)
    monkeypatch.setattr(replay_check, 'acceptance', lambda *args: {})
    prepared = []
    def prepare(descriptor, **kwargs):
        prepared.append(descriptor.name)
        return descriptor
    adapter = SimpleNamespace(prepare=prepare, acceptance_metadata=lambda *args, **kwargs: {},
        capabilities=lambda: [])
    result = e._prepare_descriptor(config, payload, adapter, records, [],
        client_facts=experiment_client.load_client_facts(config))
    assert len(prepared) == 1
    assert result['status'] == 'unknown' and 'accepted' not in result
    assert 'preparation' in result['note']


@pytest.mark.process_e2e
def test_cli_expired_shared_deadline_refuses_before_acceptance(tmp_path, monkeypatch, execution_registry):
    source = Path(e.__file__).parents[2]
    monkeypatch.setenv('PYTHONPATH', str(source) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    review_dir = tmp_path / 'review'
    review_dir.mkdir()
    lock = review_dir / 'accept.lock'
    lock.write_bytes(b'')
    lock.chmod(0o444)
    scope = dict(handoff_protocol=protocol.CURRENT, step=2, role='W',
        job_ref=dict(path='/fixture/jobs/002.json'),
        execution_key=dict(path='/fixture/native-evidence/002/native-intent.json'), mechanism_ref=None)
    attempt = dict(number=0, execution_key=scope['execution_key'])
    config_path = tmp_path / 'config.json'
    records = tmp_path / 'records'
    config = e.freeze(dict(record_dir=str(records), scope=scope, native_attempt=attempt,
        admission=dict(version=1, mode='soft', directory=str(review_dir), stage_anchor=scope['job_ref']),
        run_argv=[sys.executable], max_seconds=10), 'local_execution:command', path=config_path)
    config_path.write_bytes(e.encoded(config))
    stamp = budget.clock_stamp()
    review_path = review_dir / 'review.json'
    review_path.write_bytes(e.encoded(dict(scope=scope, stage_anchor=scope['job_ref'],
        native_attempt=attempt, boot_id=stamp['boot_id'],
        deadline_boottime=stamp['at_boottime'] - 1,
        valid_until_boottime=stamp['at_boottime'] + 60, pending=False, reason=None)))
    marker = tmp_path / 'must-not-execute'
    script = tmp_path / 'science.py'
    script.write_text('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("executed")\n')
    originals = {path: (e.proof(path), path.read_bytes()) for path in (config_path, review_path, script, lock)}

    reply = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.experiments',
        '--config', str(config_path), 'run', '--script', str(script)],
        capture_output=True, text=True, timeout=10)

    assert reply.returncode == 2, (reply.stdout, reply.stderr)
    result = json.loads(reply.stdout)
    assert result['status'] == 'budget_exhausted' and result['accepted'] is False
    assert result['reason'] == 'shared_time_exhausted'
    assert result['handoff']['action'] == 'finalize_response'
    assert 'normal scientific JSON' in result['handoff']['instruction']
    assert 'call_id' not in result and str(tmp_path) not in reply.stdout
    assert e.Records(config).calls() == [] and not (records / 'calls').exists()
    assert not marker.exists()
    assert all(e.proof(path) == proof and path.read_bytes() == raw
        for path, (proof, raw) in originals.items())
