"""Operator continuation keeps billing gaps separate from original-call safety.

These are source-owner/admission tests with an in-process external-interface
fixture. Native launch, real providers, solvers and scientific qualification
are outside their evidence layer.
"""
import json

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from test_immediate_interrupt import accepted_scene, resume_options, stopped_original
from scidiscovery.research_loop import (accepted_budget, budget, continuation,
    contracts as c, execution, experiments as e, instance_control, native, runtime)


def _continue(root, record, target, monkeypatch):
    launched = []
    monkeypatch.setattr(runtime, 'start', lambda *args: launched.append(args))
    monkeypatch.setattr(native, 'wait_native', lambda *args, **kwargs: dict(status='fixture_continued'))
    assert continuation.start(root, 1, **resume_options(target)) == dict(status='fixture_continued')
    assert launched == [(root, 1, 1)]
    successor = record/'continuations/001'
    assert c.read(successor/'native-intent.json')['continuation_kind'] == 'operator_resume'
    return successor


def _configure(root):
    path = c.workspace(root, 1)/'work/execution.json'
    config = e.configure(root, 1, 'remote_execution:fixture',
        dict(max_seconds=2, outcome='unknown'), path)
    return path, config


def _payload(root):
    path = c.workspace(root, 1)/'work/mechanical-input.json'
    path.write_bytes(b'{"message":"remote-original"}\n')
    return e.LocalFileDescriptor(name='input', local_path=str(path),
        size_bytes=path.stat().st_size, media_type='application/json')


def test_missing_native_bill_after_operator_resume_allows_original_scope_admission(
        tmp_path, monkeypatch, remote_registry):
    root, record, target = stopped_original(tmp_path, monkeypatch)
    intent = c.read(record/'native-intent.json')
    stopped = c.read(record/'native-outcome.json')
    before = native.usage(root)
    originals = {path: path.read_bytes() for path in (
        record/'native-intent.json', record/'native-outcome.json', record/'interrupt.request')}
    assert not (record/'usage.json').exists()
    assert before['unknown_steps'] == [1]

    successor = _continue(root, record, target, monkeypatch)
    resumed = c.read(successor/'native-intent.json')
    after = accepted_budget.usage(root)
    assert resumed['task_ref'] == intent['task_ref']
    assert resumed['native_profile'] == intent['native_profile']
    assert resumed['budget_slice']['previous_tokens'] == stopped['budget_slice']['observed_tokens']
    assert resumed['budget_slice']['previous_seconds'] == stopped['budget_slice']['advance_seconds']
    assert after['observed_tokens'] == before['observed_tokens']
    assert after['accounting_basis'] == 'observed_lower_bound'
    assert after['unknown_steps'] == [1] and after['unknown_step_count'] > 0
    assert after['blocking_unknown_count'] == 0 and after['blocking_unknown_steps'] == []

    # configure publishes the real shared-budget review. The public dispatch
    # then passes the exact native-attempt, review and durable acceptance gates;
    # only the external implementation is the mechanical in-process fixture.
    _, config = _configure(root)
    review = c.read(budget._review_directory(root, c.read(root/'jobs/001.json'))/'review.json')
    assert review['pending'] is False and review['reason'] is None
    assert review['inflight_usage_unknown'] is True
    result = e.dispatch_descriptor(config, _payload(root))
    assert result['status'] == 'unknown'
    call = result['call_id']
    accepted = e.Records(config).accepted(call)
    assert accepted['native_attempt'] == config['native_attempt']
    assert accepted['native_attempt']['number'] == 1
    assert accepted['scope'] == config['scope']
    assert e.Records(config).external(call) == 'remote-'+call
    assert len(e.Records(config).calls()) == 1
    assert not (record/'usage.json').exists()
    assert native.usage(root)['unknown_steps'] == [1]
    assert {path: path.read_bytes() for path in originals} == originals


def test_unknown_managed_call_after_operator_resume_queries_original_without_resubmission(
        tmp_path, monkeypatch, remote_registry, capsys):
    root, record, target = accepted_scene(tmp_path, monkeypatch)
    _, original_config = _configure(root)
    payload = _payload(root)
    first = e.dispatch_descriptor(original_config, payload)
    assert first['status'] == 'unknown'
    call = first['call_id']
    records = e.Records(original_config)
    accepted = records.accepted(call)
    external = records.external(call)
    assert accepted['native_attempt']['number'] == 0 and external == 'remote-'+call
    originals = {path: path.read_bytes() for path in (
        records.directory(call)/'accepted.json', records.directory(call)/'submission.json',
        record/'experiment-config.json',
        root/'host-runtime/experiment-control/001/interfaces/000.json')}

    assert instance_control.request_interrupt(root, **target)['status'] == 'interrupt_requested'
    monkeypatch.setattr(native.subprocess, 'Popen',
        lambda *args, **kwargs: pytest.fail('unexpected native launch'))
    with pytest.raises(RuntimeError, match='before native submission'):
        native.run_native(root, 1)
    _continue(root, record, target, monkeypatch)
    path, config = _configure(root)
    assert config['native_attempt']['number'] == 1
    assert config['scope'] == original_config['scope']
    assert config['record_dir'] == original_config['record_dir']
    assert e.original_configuration(config, call) == original_config

    queried = []
    def unknown_original(adapter, reference):
        assert reference == call
        assert adapter.config['native_attempt'] == accepted['native_attempt']
        assert adapter.records.external(reference) == external
        queried.append((reference, external))
        return dict(state='unknown', call_id=reference)
    monkeypatch.setattr(DifferentExternalIdentity, 'status_details', unknown_original)
    monkeypatch.setattr(DifferentExternalIdentity, 'prepare',
        lambda *args, **kwargs: pytest.fail('unknown original was prepared again'))
    monkeypatch.setattr(DifferentExternalIdentity, 'submit',
        lambda *args, **kwargs: pytest.fail('unknown original was submitted again'))

    assert e.main(['--config', str(path), 'query', '--call-id', call]) == 0
    assert json.loads(capsys.readouterr().out)['call_id'] == call
    assert e.main(['--config', str(path), 'reconcile', '--call-id', call]) == 2
    reconciled = json.loads(capsys.readouterr().out)
    assert reconciled['status'] == 'unknown' and reconciled['call_id'] == call
    # The public dispatch reaches _original_dispatch_state, which selects the
    # old interface and returns the accepted identity before any new prepare.
    refused = e.dispatch_descriptor(config, payload)
    assert refused['status'] == 'unknown' and refused['call_id'] == call
    assert queried and all(row == (call, external) for row in queried)
    assert [item.name for item in e.Records(config).calls()] == [call]
    assert e.Records(config).accepted(call) == accepted
    assert e.Records(config).external(call) == external
    assert not (records.directory(call)/'execution-observation.json').exists()
    assert {path: path.read_bytes() for path in originals} == originals
    assert len(execution.records(root, 1)) == 2
