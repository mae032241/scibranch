"""Bridge argument syntax and original-request identity, without solver qualification."""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import contracts as c, experiment_client, experiments as e
from scidiscovery.research_loop import managed_wait, worker_execution_bridge as bridge


@pytest.mark.parametrize('command,source_option', [('run', '--script'), ('compile', '--implementation')])
def test_equals_options_match_separate_values_without_changing_original(tmp_path, command, source_option):
    work = tmp_path.resolve()
    source = work/'source=a.py'
    source.write_text('print(1)\n')
    separate = [command, source_option, source.name, '--mechanism', '12',
        '--change-intent', 'alpha=beta', '--output', 'result=a.json']
    equals = [command, f'{source_option}={source.name}', '--mechanism=12',
        '--change-intent=alpha=beta', '--output=result=a.json']
    original = list(equals)
    expected = [command, source_option, str(source), '--mechanism', '12',
        '--change-intent', 'alpha=beta', '--output', str(work/'result=a.json')]
    assert bridge._arguments(separate, work) == expected
    assert bridge._option_arguments(bridge._arguments(equals, work)) == expected
    assert equals == original


@pytest.mark.parametrize('option', ['--start', '--key', '--parent-plan', '--change-intent', '--call-id'])
def test_known_nonpath_values_preserve_embedded_equals(tmp_path, option):
    arguments = ['lookup', f'{option}=alpha=beta']
    assert bridge._arguments(arguments, tmp_path) == arguments


def test_separate_values_and_run_script_tail_are_opaque(tmp_path):
    tail = ['--', '--mechanism=0', '--script=../outside.py', '--new-plan=true', 'value=a=b']
    arguments = ['run', '--change-intent', '--mechanism=7', '--mechanism=12', *tail]
    assert bridge._arguments(arguments, tmp_path) == arguments
    assert bridge._option_arguments(arguments) == [
        'run', '--change-intent', '--mechanism=7', '--mechanism', '12', *tail]
    assert arguments == ['run', '--change-intent', '--mechanism=7', '--mechanism=12', *tail]


@pytest.mark.parametrize('options', [
    ['--unknown=1'], ['--new-plan=true'], ['--metadata=false'],
    ['--mechanism'], ['--script'], ['--key'], ['--mechanism='], ['--script='],
])
def test_unknown_flag_values_missing_and_empty_required_values_are_rejected(tmp_path, options):
    with pytest.raises(e.CallerError):
        bridge._arguments(['run', *options], tmp_path)


@pytest.mark.parametrize('command', ['run', 'compile'])
@pytest.mark.parametrize('value', ['0', '01', '-1', '1.0', 'h001.m1', ' 1', '1=2'])
@pytest.mark.parametrize('equals', [False, True])
def test_mechanism_remains_an_exact_positive_catalog_decimal(tmp_path, command, value, equals):
    option = [f'--mechanism={value}'] if equals else ['--mechanism', value]
    with pytest.raises(e.CallerError, match='exact positive decimal mechanism number'):
        bridge._arguments([command, *option], tmp_path)


@pytest.mark.parametrize('option', sorted(bridge.PATH_OPTIONS))
@pytest.mark.parametrize('equals', [False, True])
def test_all_path_options_keep_workspace_confinement(tmp_path, option, equals):
    work = tmp_path/'work'
    work.mkdir()
    value = '../outside=a.py'
    arguments = [f'{option}={value}'] if equals else [option, value]
    with pytest.raises(e.CallerError, match='inside the assigned task workspace'):
        bridge._arguments(['run', *arguments], work)


@pytest.mark.parametrize('option', ['--script', '--payload', '--implementation', '--scientific-files'])
def test_equals_inputs_still_require_existing_ordinary_files(tmp_path, option):
    with pytest.raises(e.CallerError, match='scientific input is missing'):
        bridge._arguments(['run', f'{option}=missing=a.py'], tmp_path)
    outside = tmp_path/'outside.py'
    outside.write_text('print(1)\n')
    work = tmp_path/'work'
    work.mkdir()
    (work/'linked.py').symlink_to(outside)
    with pytest.raises(e.CallerError, match='inside the assigned task workspace'):
        bridge._arguments(['run', f'{option}=linked.py'], work)


def test_equals_experiment_directory_keeps_current_worker_creation_rule(tmp_path):
    config = dict(scope=dict(role='W'), execution_interface=dict(executor='local_execution:command'))
    arguments = ['run', '--experiment-dir=experiments/case=a', '--mechanism=12']
    assert bridge._arguments(arguments, tmp_path, config) == [
        'run', f'--experiment-dir={tmp_path / "experiments/case=a"}', '--mechanism=12']
    with pytest.raises(e.CallerError, match='ordinary --experiment-dir directory'):
        bridge._arguments(arguments, tmp_path)


def test_request_consumption_uses_argparse_equals_semantics():
    separate = bridge._request_consumption(['collect', '--call-id', 'original-call'])
    equals = bridge._request_consumption(['collect', '--call-id=original-call'])
    assert vars(equals) == vars(separate)
    assert equals.command == 'collect' and equals.call_id == 'original-call'


def test_owner_validation_preserves_dash_leading_inline_value_for_argparse(tmp_path):
    arguments = bridge._arguments(['collect', '--call-id=--literal=call'], tmp_path)
    assert arguments == ['collect', '--call-id=--literal=call']
    assert bridge._request_consumption(arguments).call_id == '--literal=call'


def test_client_frame_refusal_retains_equals_mechanism_hint_without_rewriting_requests(monkeypatch):
    monkeypatch.setattr(managed_wait, 'handoff', lambda *args: {})
    ordinary = ['run', '--mechanism=12', '--change-intent=alpha=beta']
    request = bridge._client_request('S001', ordinary)
    assert request == dict(submission_ref='S001', arguments=ordinary)
    assert request['arguments'] is ordinary
    huge = ['run', '--mechanism=12', '--', 'x'*(bridge.MAX_BYTES+1), '--mechanism=99']
    notification = bridge._client_request('S002', huge)
    assert notification == dict(submission_ref='S002', arguments=['run', '--mechanism', '12'],
        client_error='submission_frame_too_large')
    assert huge[1] == '--mechanism=12' and huge[-1] == '--mechanism=99'


def test_execute_forwards_validated_equals_options_with_opaque_script_tail(tmp_path, monkeypatch):
    work = tmp_path/'work'
    work.mkdir()
    source = work/'source=a.py'
    source.write_text('print(1)\n')
    record = tmp_path/'record'
    record.mkdir()
    config = dict(scope=dict(step=2), max_seconds=10)
    config_path = work/'client.json'
    server = SimpleNamespace(work=work, record=record, config_path=config_path, stop=threading.Event(),
        configuration=config, environment={}, launch=lambda *args: pytest.fail('Unexpected process launch'))
    monkeypatch.setattr(bridge, '_configuration', lambda given: config)
    monkeypatch.setattr(bridge, '_bridge_deadline', lambda *args: None)
    monkeypatch.setattr(experiment_client, 'root_for_configuration', lambda given: tmp_path)
    validated = []
    monkeypatch.setattr(experiment_client, 'input_reference', lambda given, path: validated.append(path))
    forwarded = []

    def main(arguments):
        forwarded.append(arguments)
        print(json.dumps(dict(status='fixture_result')))
        return 0

    monkeypatch.setattr(e, 'main', main)
    tail = ['--', '--mechanism=0', '--input=alpha=beta']
    arguments = ['run', '--script=source=a.py', '--mechanism=12', *tail]
    assert bridge._execute(server, arguments) == dict(code=0, result=dict(status='fixture_result'))
    assert forwarded == [['--config', str(config_path), 'run', f'--script={source}', '--mechanism=12', *tail]]
    assert validated == [source]
    assert arguments == ['run', '--script=source=a.py', '--mechanism=12', *tail]


def test_equals_call_id_preserves_response_evidence_handoff_and_original_replay(tmp_path, monkeypatch):
    directory = tmp_path/'submission'
    directory.mkdir()
    claims = tmp_path/'claims'
    claims.mkdir()
    configuration = dict(scope=dict(step=2))
    server = SimpleNamespace(directory=directory, claims=claims, configuration=configuration,
        client=dict(step=2, attempt=0), diagnostic=tmp_path/'diagnostic.json')
    monkeypatch.setattr(bridge, '_configuration', lambda given: configuration)
    monkeypatch.setattr(e, 'Records', lambda given: SimpleNamespace(
        root=tmp_path/'records', calls=lambda: [Path('original-call')]))
    executions = []

    def execute(given, arguments, *, submission_ref=None):
        assert submission_ref=='S001'
        executions.append(list(arguments))
        return dict(code=2, result=dict(status='unknown'))

    monkeypatch.setattr(bridge, '_execute', execute)
    monkeypatch.setattr(bridge, '_call_evidence', lambda given, reference: dict(
        call_id=reference, observation={}, retirement=None))
    monkeypatch.setattr(bridge, '_verify_calls', lambda *args, **kwargs: True)
    monkeypatch.setattr(managed_wait, 'handoff', lambda config, result, command, call_id=None:
        dict(command=command, call_id=call_id))
    raw = b' { "submission_ref" : "S001", "arguments" : ["collect", "--call-id=original-call"] }\n'
    bridge._handle(server, raw)
    response = c.read(directory/'response.json')
    assert response['result']['handoff'] == dict(command='collect', call_id='original-call')
    assert response['request']['arguments'] == ['collect', '--call-id=original-call']
    claim = claims/'S001'
    assert (claim/'submit.json').read_bytes() == raw
    assert c.read(claim/'outcome.json')['calls'] == [dict(
        call_id='original-call', observation={}, retirement=None)]
    originals = {name: (claim/name).read_bytes() for name in (
        'submit.json', 'binding.json', 'response.json', 'outcome.json')}
    bridge._handle(server, raw)
    assert c.read(directory/'response.json') == response
    assert executions == [['collect', '--call-id=original-call']]
    changed = bridge._encoded(dict(submission_ref='S001', arguments=['collect', '--call-id', 'original-call']))
    bridge._handle(server, changed)
    assert c.read(directory/'response.json')['code'] == 3
    assert len(executions) == 1
    assert all((claim/name).read_bytes() == original for name, original in originals.items())
