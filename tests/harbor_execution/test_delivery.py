"""Declared provider candidates retain exact delivery originals without execution."""
import importlib.metadata
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / 'plugins/harbor_execution'))

from harbor_execution.config import configure
from harbor_execution.experiments import HarborExecution, REGISTRATION
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.research_loop import (code_versions, contracts as c, delivery,
    experiments as e, generic_delivery, generic_results, originals, protocol)


@pytest.fixture
def registered(tmp_path, monkeypatch):
    if not list(importlib.metadata.entry_points().select(group=e.GROUP, name='harbor_execution')):
        metadata = tmp_path / 'discovery/harbor_execution_source.dist-info'
        metadata.mkdir(parents=True)
        (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: harbor-execution-source\nVersion: 0\n')
        (metadata / 'entry_points.txt').write_text(
            '[scidiscovery.execution_interfaces]\nharbor_execution = harbor_execution.experiments:REGISTRATION\n')
        monkeypatch.syspath_prepend(str(metadata.parent))
    assert e.registration('harbor_execution:experiment') == (REGISTRATION, 'experiment')


def archived_candidate(tmp_path, launch=None, *, capture_code=False):
    """Small sealed-call view, with synthetic public bytes and common code metadata."""
    root = tmp_path / 'instance'
    directory = root / 'host-runtime/experiment-control/001/records/calls/0001-call'
    directory.mkdir(parents=True)
    code = c.workspace(root, 1) / 'code/experiment.py'
    code.parent.mkdir(parents=True)
    code.write_bytes(b"print('fixture only; never executed')\n")
    data = directory / 'inputs/data.txt'
    data.parent.mkdir()
    data.write_bytes(b'public fixture input\n')
    artifact = directory / 'outputs/regressor.py'
    artifact.parent.mkdir()
    artifact.write_bytes(b'def predict(x): return x\n')
    data.chmod(0o400);artifact.chmod(0o400)
    scope = dict(step=1, role='W', job_ref=dict(path=str(root / 'jobs/001.json')),
        execution_key=dict(path=str(root / 'native-intent.json')), mechanism_ref=None,
        handoff_protocol=protocol.CURRENT)
    config = dict(configure(dict(container_id='a' * 64, container_root='/tmp/scid-fixture'), None,
        binding='experiment'), record_dir=str(directory.parents[1]), scope=scope)
    adapter = HarborExecution(config, backend=object())
    interface = dict(executor='harbor_execution:experiment', version=1,
        result_protocol=adapter.result_protocol, terminal_evidence=adapter.terminal_evidence_kind,
        config_record=config)
    version = (code_versions.capture(root, step=1, call_id=directory.name, code_root=code.parent,
        entry=code.name, selection='file', logical_root='code') if capture_code else
        dict(version=1, object_format='sha1', commit='b' * 40, tree_path='.',
            selection='file', entry=code.name, logical_root='code'))
    payload = LocalFileDescriptor(name=directory.name, local_path=str(code), size_bytes=code.stat().st_size,
        media_type='text/x-python')
    metadata = adapter.acceptance_metadata(payload, command_options=dict(arguments=['--label', 'two words', '']))
    if launch is not None:
        metadata.pop('arguments')
        metadata.update(launch)
    accepted = dict(metadata, call_id=directory.name, scope=scope, interface=interface,
        code_version=version, source_script=str(code),
        snapshot_files=[dict(e.proof(data), name='data.txt')])
    declaration = dict(label='synthetic candidate', artifact='artifacts/regressor.py', metric='loss')
    result = dict(status='available', metrics=[dict(name='loss', value=0.25, direction='minimize')],
        outputs=[dict(path=declaration['artifact'])], candidates=[declaration])
    completed = dict(call_id=directory.name, scope=scope, status='known_success', result=result, proofs=[],
        outputs=[dict(e.proof(artifact), name=artifact.name)])
    c.write(directory / 'accepted.json', accepted)
    c.write(directory / 'completed.json', completed)
    def archived(path, **fields):
        proof = e.proof(path)
        return dict(proof, path=path.relative_to(root).as_posix(),
            snapshot=path.relative_to(root).as_posix(), retention='archived', **fields)
    row = dict(call_id=directory.name, scope=scope, result_protocol=adapter.result_protocol,
        status='known_success', execution=completed,
        accepted=archived(directory / 'accepted.json'), completed=archived(directory / 'completed.json'),
        originals=[archived(data)], outputs=[archived(artifact, name=artifact.name)])
    fact = dict(managed_calls=[row], candidates=generic_results.candidates([row]))
    owner = originals.NodeSource(dict(goal_id='a' * 64,
        settings_ref=dict(path=str(root / 'settings.json'))), 1, str(root), root)
    return root, owner, fact, accepted, code, data, artifact


@pytest.mark.parametrize('launch,expected', [
    (None, dict(entry='experiment.py', arguments=['--label', 'two words', ''])),
    (dict(command=['python', '-u', 'original.py', '--local', 'two words', ''], script_index=2),
        dict(entry='experiment.py', arguments=['--local', 'two words', ''])),
    (dict(arguments=[]), dict(entry='experiment.py', arguments=[])),
    ({}, dict(entry='experiment.py')),
])
def test_declared_candidate_replay_uses_only_available_launch_facts(tmp_path, launch, expected):
    root, owner, fact, accepted, _, data, artifact = archived_candidate(tmp_path, launch)
    candidate = fact['candidates'][0]
    source = generic_results.sources(root, fact, candidate['id'], source=owner)
    assert source['context']['replay'] == expected
    assert source['context']['source_code_version'] == accepted['code_version']
    assert source['context']['candidate'] == fact['managed_calls'][0]['execution']['result']['candidates'][0]
    assert source['status'] == 'audit_pending'
    assert originals.resolve_ref(owner, source['objects']['artifact']) == artifact
    assert originals.resolve_ref(owner, source['objects']['inputs/data.txt']) == data
    assert artifact.read_bytes() == b'def predict(x): return x\n'
    assert data.read_bytes() == b'public fixture input\n'


@pytest.mark.process_e2e
def test_registered_provider_candidate_prepares_generic_delivery_without_local_command(tmp_path, monkeypatch, registered):
    root, owner, fact, accepted, code, data, artifact = archived_candidate(tmp_path, capture_code=True)
    assert 'command' not in accepted and 'script_index' not in accepted
    directory = root / fact['managed_calls'][0]['accepted']['snapshot']
    directory = directory.parent
    (root / 'jobs').mkdir()
    c.write(root / 'jobs/001.json', dict(step=1, role='W', worker_view_version=1))
    submission = dict(call_id=accepted['call_id'], scope=accepted['scope'], external_run_id=accepted['call_id'])
    c.write(directory / 'submission.json', submission)
    descriptor = LocalFileDescriptor(name=artifact.name, local_path=str(artifact), size_bytes=artifact.stat().st_size,
        media_type='text/x-python')
    output = dict(e.proof(artifact), name=artifact.name, source=e.proof(artifact),
        descriptor=descriptor.model_dump(mode='json'))
    c.write(directory / 'execution-observation.json', dict(version=e.OBSERVATION_VERSION,
        call_id=accepted['call_id'], scope=accepted['scope'], status='known_success',
        interface=accepted['interface'], external_run_id=accepted['call_id'],
        accepted=e.proof(directory / 'accepted.json'), completed=e.proof(directory / 'completed.json'),
        submission=e.proof(directory / 'submission.json'), proofs=[], outputs=[output],
        source_code_version=accepted['code_version']))
    source = generic_results.sources(root, fact, fact['candidates'][0]['id'], source=owner)
    source['objects'] = {label: dict(path=str(originals.resolve_ref(owner, proof)), size_bytes=proof['size_bytes'])
        for label, proof in source['objects'].items()}
    source['context']['accepted'] = e.proof(directory / 'accepted.json')
    source['artifact_origin'] = dict(path=str(artifact), size_bytes=artifact.stat().st_size)
    target = tmp_path / 'target';target.mkdir()
    spec = generic_delivery.descriptor(target)
    spec['inputs'] = [dict(label='runtime-python', path=str(Path(sys.executable).resolve()),
        invoked_path=str(Path(sys.executable).absolute()))]
    monkeypatch.setattr(delivery, '_settings', lambda *args: dict(handoff_protocol=protocol.CURRENT))
    request = delivery._staged(root, spec, source, 'c' * 64, root / 'host-delivery/stage')
    assert request['context']['replay'] == dict(entry='experiment.py', arguments=accepted['arguments'])
    assert request['candidate_id'] == fact['candidates'][0]['id']
    assert request['object_refs']['artifact'] == source['objects']['artifact']
    assert request['object_refs']['inputs/data.txt'] == source['objects']['inputs/data.txt']
    exported = Path(request['object_refs']['inputs/experiment.py']['path'])
    assert exported.read_bytes() == code.read_bytes()
    captured = []
    monkeypatch.setattr(generic_delivery.shutil, 'which', lambda name: '/qualified/bwrap')
    monkeypatch.setattr(generic_delivery.os, 'execv', lambda binary, argv: captured.extend(argv))
    generic_delivery.isolate(dict(request, phase='promote', target=spec['target']), spec['adapter'])
    for label, original in [('artifact', artifact), ('inputs/data.txt', data), ('inputs/experiment.py', exported)]:
        assert any(captured[index:index + 3] == ['--ro-bind', str(original), '/inputs/' + label]
            for index in range(len(captured) - 2))
    assert artifact.read_bytes() == b'def predict(x): return x\n'
    assert data.read_bytes() == b'public fixture input\n'
