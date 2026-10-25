"""Saved control reconciliation cannot authorize terminal evidence publication."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import execution_authority, experiments as e, protocol, replay_check


class TerminalAdapter:
    """A bounded terminal provider; no process, submission or qualification."""
    def __init__(self, completed, descriptor):
        self.completed = completed
        self.descriptor = descriptor
        self.calls = []

    def lifecycle_capabilities(self):
        return e.LifecycleCapabilities(query=True, lookup_submission=True, collect=True)

    def status_details(self, call_id):
        self.calls.append(('query', call_id))
        return dict(state='succeeded', call_id=call_id)

    def result(self, call_id):
        self.calls.append(('result', call_id))
        return self.completed

    def collect_original(self, call_id):
        self.calls.append(('collect', call_id))
        return [self.descriptor]

    def lookup_submission(self, descriptor):
        self.calls.append(('lookup', descriptor.name))
        return 'native-original', 'succeeded'

    def public_result(self, result):
        return dict(status=result['status'], call_id=result['call_id'])

    def submit(self, *args, **kwargs):
        pytest.fail('Observation reconciliation submitted an execution')


def put(path, value):
    path.write_bytes(e.encoded(value))
    return e.proof(path)


@pytest.fixture
def original(tmp_path):
    records = tmp_path/'records'
    call_id = '0001-original'
    directory = records/'calls'/call_id
    directory.mkdir(parents=True)
    scope = dict(step=1, role='W', handoff_protocol=protocol.CURRENT)
    interface = dict(executor='fixture:terminal', terminal_evidence='adapter_result')
    config = dict(record_dir=str(records), scope=scope, execution_interface=interface, max_seconds=30)
    source = directory/'source.bin'
    source.write_bytes(b'exact selected input')
    output = directory/'output.bin'
    output.write_bytes(b'exact selected output')
    descriptor = e.LocalFileDescriptor(name='selected.bin', local_path=str(output),
        size_bytes=output.stat().st_size, media_type='application/octet-stream')
    prepared = e.LocalFileDescriptor(name=call_id, local_path=str(source),
        size_bytes=source.stat().st_size, media_type='application/octet-stream')
    accepted = dict(call_id=call_id, scope=scope, interface=interface,
        parent=dict(candidate_id=None), script=e.proof(source), prepared=prepared.model_dump(mode='json'))
    accepted_ref = put(directory/'accepted.json', accepted)
    native_ref = put(directory/'native-terminal.json', dict(state='succeeded', call_id=call_id))
    output_ref = e.proof(output)
    completed = dict(call_id=call_id, scope=scope, status='known_success',
        proofs=[native_ref, output_ref], outputs=[output_ref])
    completed_ref = put(directory/'completed.json', completed)
    submission_ref = put(directory/'submission.json',
        dict(call_id=call_id, scope=scope, external_run_id='native-original'))
    retained = lambda item: dict(item, source=item)
    observed = dict(version=e.OBSERVATION_VERSION, call_id=call_id, scope=scope,
        mechanism_binding=None, interface=interface, external_run_id='native-original',
        status='known_success', accepted=accepted_ref, completed=completed_ref,
        submission=submission_ref, proofs=[retained(native_ref), retained(output_ref)],
        outputs=[dict(retained(output_ref), name=descriptor.name, descriptor=descriptor.model_dump(mode='json'))],
        scientific_source=retained(accepted['script']),
        scientific_outputs=[dict(retained(output_ref), name=descriptor.name,
            descriptor=descriptor.model_dump(mode='json'))],
        public_result=dict(status='known_success', call_id=call_id))
    return dict(config=config, call_id=call_id, directory=directory, source=source,
        output=output, accepted=accepted, observed=observed,
        adapter=TerminalAdapter(completed, descriptor))


@pytest.fixture
def effects(monkeypatch):
    calls = []

    def accounting(config, adapter, call_id, *, observation, operation_context):
        operation_context.remaining_seconds()
        calls.append(('accounting', call_id, observation))

    def cleanup(config, call_id, observation):
        calls.append(('cleanup', call_id, observation))

    monkeypatch.setattr(execution_authority, 'reconcile_resources', accounting)
    monkeypatch.setattr(e, '_cleanup_terminal_code_export', cleanup)
    return calls


def save(original):
    path = original['directory']/'execution-observation.json'
    put(path, original['observed'])
    return path


def damage(path, fault):
    if fault == 'missing':
        path.unlink()
    else:
        replacement = path.with_name(path.name+'.replacement')
        replacement.write_bytes(b'x'*path.stat().st_size)
        replacement.replace(path)


def assert_effects(effects, original, observed):
    assert effects == [('accounting', original['call_id'], observed),
        ('cleanup', original['call_id'], observed)]


def test_missing_saved_observation_never_queries_collects_or_publishes(original, effects, monkeypatch):
    monkeypatch.setattr(e, 'write', lambda *args, **kwargs: pytest.fail('Missing saved observation was published'))
    monkeypatch.setattr(e.Records, 'publish', lambda *args, **kwargs: pytest.fail('Missing saved receipt was published'))
    assert e.reconcile_saved_observation(original['config'], original['adapter'], original['call_id']) is None
    assert original['adapter'].calls == []
    assert effects == []
    assert not (original['directory']/'execution-observation.json').exists()


def test_saved_controls_survive_unconsumed_science_and_keep_original_bytes(original, effects):
    path = save(original)
    before = path.read_bytes()
    original['source'].unlink()
    original['output'].unlink()
    observed = e.reconcile_saved_observation(original['config'], original['adapter'], original['call_id'])
    assert observed == original['observed']
    assert path.read_bytes() == before
    assert original['adapter'].calls == []
    assert_effects(effects, original, observed)
    # Actual consumption still has the full original-file requirement.
    with pytest.raises((OSError, ValueError)):
        e.record_observation(original['config'], original['adapter'], original['call_id'])
    assert_effects(effects, original, observed)


@pytest.mark.parametrize('name', ['accepted.json', 'completed.json', 'submission.json', 'native-terminal.json'])
def test_saved_reconciliation_rejects_changed_controls_before_effects(original, effects, name):
    path = save(original)
    before = path.read_bytes()
    damage(original['directory']/name, 'replacement')
    with pytest.raises(e.IntegrityError, match='original changed'):
        e.reconcile_saved_observation(original['config'], original['adapter'], original['call_id'])
    assert effects == []
    assert original['adapter'].calls == []
    assert path.read_bytes() == before


def test_science_declaration_cannot_hide_a_control_from_saved_verification(original, effects):
    # Even a native proof also listed as science keeps its same-call control role.
    accepted_ref = original['observed']['accepted']
    completed_path = original['directory']/'completed.json'
    completed = copy.deepcopy(original['adapter'].completed)
    completed['proofs'].append(accepted_ref)
    completed['outputs'].append(accepted_ref)
    original['observed']['completed'] = put(completed_path, completed)
    original['observed']['proofs'].append(dict(accepted_ref, source=accepted_ref))
    save(original)
    damage(Path(accepted_ref['path']), 'replacement')
    with pytest.raises(e.IntegrityError):
        e.reconcile_saved_observation(original['config'], original['adapter'], original['call_id'])
    assert effects == []


@pytest.fixture
def publication(monkeypatch):
    # The fixture supplies selected scientific originals; the production owner
    # still retains, fingerprints and verifies them before publication.
    monkeypatch.setattr(e, '_source_projection', lambda config, accepted: (accepted['script'], None))
    monkeypatch.setattr(e, 'scientific_outputs', lambda config, value, *, adapter, **kwargs: [adapter.descriptor])
    monkeypatch.setattr(replay_check, 'complete', lambda *args, **kwargs: None)


@pytest.mark.parametrize('selected', ['source', 'output'])
@pytest.mark.parametrize('fault', ['missing', 'replacement'])
def test_first_publication_rejects_missing_or_replaced_selected_originals(original, effects, publication, selected, fault):
    damage(original[selected], fault)
    with pytest.raises((OSError, ValueError)):
        e.record_observation(original['config'], original['adapter'], original['call_id'])
    assert not (original['directory']/'execution-observation.json').exists()
    assert effects == []


def test_first_publication_and_full_saved_read_reconcile_once_each(original, effects, publication):
    observed = e.record_observation(original['config'], original['adapter'], original['call_id'])
    path = original['directory']/'execution-observation.json'
    assert json.loads(path.read_bytes()) == observed
    assert original['adapter'].calls == [('query', original['call_id']),
        ('result', original['call_id']), ('collect', original['call_id'])]
    assert_effects(effects, original, observed)
    before = path.read_bytes()
    effects.clear()
    original['adapter'].calls.clear()
    assert e.record_observation(original['config'], original['adapter'], original['call_id']) == observed
    assert_effects(effects, original, observed)
    assert original['adapter'].calls == []
    assert path.read_bytes() == before


@pytest.mark.parametrize('command', ['query', 'lookup', 'collect'])
def test_cli_cached_operations_route_to_named_reconciliation(original, effects, monkeypatch, command):
    save(original)
    config_path = original['directory'].parents[2]/'client.json'
    current = dict(original['config'], native_attempt=dict(number=1, execution_key='current'))
    put(config_path, current)
    adapter = original['adapter']
    selected = []
    monkeypatch.setattr(e, 'load', lambda config, **kwargs: adapter)

    def original_adapter(config, call_id, capability):
        selected.append((config, call_id, capability))
        return original['config'], adapter

    monkeypatch.setattr(e, 'original_adapter', original_adapter)
    monkeypatch.setattr(e, 'record_observation', lambda *args, **kwargs: pytest.fail('Cached CLI selected publication'))
    reconcile = e.reconcile_saved_observation
    routed = []

    class Routed(BaseException):
        """Stop after exercising the real CLI branch and observation owner."""

    def reconciled(config, actual_adapter, call_id, **kwargs):
        observed = reconcile(config, actual_adapter, call_id, **kwargs)
        routed.append((config, actual_adapter, call_id, kwargs, observed))
        raise Routed

    monkeypatch.setattr(e, 'reconcile_saved_observation', reconciled)
    with pytest.raises(Routed):
        e.main(['--config', str(config_path), command, '--call-id', original['call_id']])
    capability = 'lookup_submission' if command == 'lookup' else command
    assert selected == [(current, original['call_id'], capability)]
    assert len(routed) == 1
    config, actual_adapter, call_id, kwargs, observed = routed[0]
    assert config is original['config'] and actual_adapter is adapter and call_id == original['call_id']
    assert_effects(effects, original, observed)
    expected = {'query': [('query', call_id)],
        'lookup': [('lookup', call_id), ('query', call_id)], 'collect': [('collect', call_id)]}
    assert adapter.calls == expected[command]
    if command == 'collect':
        kwargs['operation_context'].remaining_seconds()
    else:
        assert kwargs == {}
