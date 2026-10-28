"""Budget refusal is a preacceptance control fact, never an invented review."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import (breadth, budget, experiment_client, experiments as e,
    instance_owner, protocol, replay_check)


@pytest.fixture
def cli(tmp_path, monkeypatch, capsys):
    config = dict(record_dir=str(tmp_path / 'records'), scope={})
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(config))
    monkeypatch.setattr(instance_owner, 'lease_for_configuration', lambda *args: nullcontext())
    monkeypatch.setattr(protocol, 'require_scope', lambda *args: None)
    monkeypatch.setattr(e, 'load', lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(e, '_diagnostic', lambda error, note, **kwargs: note)

    def invoke(dispatch):
        monkeypatch.setattr(e, 'dispatch', dispatch)
        code = e.main(['--config', str(path), 'run', '--script', str(tmp_path / 'science.py')])
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
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args, **kwargs: None)
    monkeypatch.setattr(experiment_client, 'code_selection', lambda *args, **kwargs: None)
    monkeypatch.setattr(e, '_require_new_execution', lambda *args: None)
    adapter = SimpleNamespace(prepare=lambda *args, **kwargs: pytest.fail('Refused work must not prepare'))
    payload = SimpleNamespace(local_path=str(tmp_path / 'science.py'))

    def dispatch(*args, **kwargs):
        return e._prepare_descriptor(config, payload, adapter, records, [])
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
    records = e.Records(dict(record_dir=str(tmp_path / 'records'), scope={}))
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


@pytest.mark.parametrize('error', [
    e.ReviewRequired('review_deadline_reached private/provider/path'),
    e.ReviewRequired('reserve_reached private/provider/path'),
    ValueError('shared_usage_unknown private/provider/path'),
])
def test_provider_exception_text_cannot_claim_budget_refusal(cli, error):
    _, invoke = cli
    def dispatch(*args, **kwargs):raise error
    code, result = invoke(dispatch)
    assert code in {2, 3}
    assert result['status'] not in {'budget_exhausted', 'budget_unavailable', 'review_required'}
    assert 'accepted' not in result and 'private/provider/path' not in json.dumps(result)
    assert result['handoff']['action'] != 'finalize_response'


@pytest.mark.parametrize('error', [
    e.ReviewRequired('review_deadline_reached'),
    e._BudgetAdmissionUnavailable('review_deadline_reached'),
    e._AdmissionBudgetRefused('reserve_reached'),
])
def test_accepted_provider_refusal_keeps_original_unknown(tmp_path, monkeypatch, error):
    config = dict(record_dir=str(tmp_path / 'records'))
    monkeypatch.setattr(e, 'verify_scientific_inputs', lambda *args, **kwargs: None)
    def submit(*args):raise error
    adapter = SimpleNamespace(dispatch_lease=lambda *args: None, submit=submit)
    result = e._submit_descriptor(config, adapter, SimpleNamespace(query=True), object(),
        dict(call_id='original-call'))
    assert result['status'] == 'unknown' and result['call_id'] == 'original-call'
    assert 'accepted' not in result and 'budget_exhausted' not in result


def test_external_preparation_before_budget_refusal_stays_unknown(tmp_path, monkeypatch):
    script = tmp_path / 'science.py'
    script.write_text('pass\n')
    config = dict(record_dir=str(tmp_path / 'records'), scope={}, max_seconds=10,
        execution_interface=dict(executor='external:fixture'))
    payload = e.LocalFileDescriptor(name='input', local_path=str(script),
        size_bytes=script.stat().st_size, media_type='text/plain')
    records = e.Records(config)
    monkeypatch.setattr(records, 'calls', lambda: [])
    monkeypatch.setattr(records, 'preflight', lambda **kwargs: None)
    def refuse(*args, **kwargs):raise e._BudgetAdmissionUnavailable('reserve_reached')
    monkeypatch.setattr(records, 'accept', refuse)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda *args, **kwargs: None)
    monkeypatch.setattr(experiment_client, 'code_selection', lambda *args, **kwargs: None)
    monkeypatch.setattr(e, '_require_new_execution', lambda *args: None)
    monkeypatch.setattr(replay_check, 'acceptance', lambda *args: {})
    prepared = []
    def prepare(descriptor, **kwargs):
        prepared.append(descriptor.name)
        return descriptor
    adapter = SimpleNamespace(prepare=prepare, acceptance_metadata=lambda *args, **kwargs: {},
        capabilities=lambda: [])
    result = e._prepare_descriptor(config, payload, adapter, records, [])
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
