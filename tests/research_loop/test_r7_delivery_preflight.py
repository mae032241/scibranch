"""Local directory start failures are bounded facts, never replay permission."""
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import contracts as c, delivery, generic_delivery, research_config


def policy(adapter):
    return dict(closeout_a=dict(max_observed_tokens=100, max_seconds=10),
        inflight_tokens=0, cleanup_seconds=5, submit_seconds=10, confirm_seconds=5,
        adapter=adapter)


def declaration(tmp_path, target):
    """Handwritten TOML takes the same path as config-check and prepare."""
    public = tmp_path / 'public'
    public.mkdir()
    adapter = generic_delivery.descriptor(target)
    quoted = json.dumps
    config = tmp_path / 'research.toml'
    config.write_text(f'''version = 1
[research]
root = {quoted(str(tmp_path / 'instance'))}
public_root = {quoted(str(public))}
objective_text = "Retain exact bounded originals; no scientific completion claim."
[agent]
launcher = ["fixture-never-launch"]
model = "fixture-no-model"
[budget]
max_tokens = 10000
max_seconds = 100
max_steps = 4
node_targets = {{H = {{max_observed_tokens = 100, max_seconds = 10}}, W = {{max_observed_tokens = 100, max_seconds = 10}}, A = {{max_observed_tokens = 100, max_seconds = 10}}, E = {{max_observed_tokens = 100, max_seconds = 10}}}}
[delivery]
closeout_a = {{max_observed_tokens = 100, max_seconds = 10}}
inflight_tokens = 0
cleanup_seconds = 5
submit_seconds = 10
confirm_seconds = 5
[delivery.adapter]
adapter = {quoted(adapter['adapter'])}
launcher_argv = {quoted(adapter['launcher_argv'])}
readonly_inputs_version = 1
capabilities = []
config = {{python = {quoted(adapter['config']['python'])}, runtime_roots = {quoted(adapter['config']['runtime_roots'])}}}
target = {{directory = {quoted(str(target))}}}
inputs = [{{label = "runtime-python", path = {quoted(adapter['config']['python'])}}}]
source_roots = {quoted(adapter['source_roots'])}
''')
    return config


@pytest.mark.parametrize('damage', ['missing', 'file', 'symlink', 'relative', 'root', 'shape'])
def test_handbuilt_builtin_policy_uses_descriptor_directory_validation(tmp_path, damage):
    target = tmp_path / 'target'
    target.mkdir()
    adapter = generic_delivery.descriptor(target)
    if damage == 'missing':
        target.rmdir()
    elif damage == 'file':
        target.rmdir();target.write_bytes(b'not a directory')
    elif damage == 'symlink':
        target.rmdir();target.symlink_to(tmp_path, target_is_directory=True)
    elif damage == 'relative':
        adapter['target']['directory'] = 'relative-target'
    elif damage == 'root':
        adapter['target']['directory'] = '/'
    else:
        adapter['target']['extra'] = 'unsupported'
    with pytest.raises(ValueError, match='target directory|local target directory'):
        delivery.policy(policy(adapter))
    if damage in {'missing', 'file', 'symlink', 'root'}:
        with pytest.raises(ValueError, match='target directory'):
            generic_delivery.descriptor(adapter['target']['directory'])


@pytest.mark.parametrize('validate_only', [True, False])
def test_config_check_and_prepare_reject_missing_directory_without_creating_it(tmp_path, validate_only):
    target = tmp_path / 'delivery'
    target.mkdir()
    config = declaration(tmp_path, target)
    target.rmdir()
    with pytest.raises(ValueError, match='existing explicit original target directory'):
        if validate_only:
            research_config.inspect(config=config)
        else:
            research_config.prepare(config)
    assert not target.exists()
    assert not (tmp_path / 'instance').exists()


def test_directory_policy_rejects_control_root_exposure_but_does_not_probe_remote_targets(tmp_path):
    local = generic_delivery.descriptor(tmp_path)
    with pytest.raises(ValueError, match='must not expose the control root'):
        delivery.policy(policy(local), root=tmp_path / 'instance')
    provider = tmp_path / 'provider.py'
    provider.write_text('# Opaque remote provider; no effect in this test.\n')
    remote = dict(local, adapter=str(provider), launcher_argv=[sys.executable, str(provider)],
        config=dict(remote='opaque'), target=dict(endpoint='opaque://missing-target'))
    assert delivery.policy(policy(remote), root=tmp_path / 'instance')['adapter']['target'] == remote['target']


@pytest.mark.parametrize('replacement', ['missing', 'control-ancestor'])
def test_config_edit_rejects_invalid_local_target_without_changing_frozen_settings(tmp_path, replacement):
    target = tmp_path / 'delivery'
    target.mkdir()
    research_config.prepare(declaration(tmp_path, target))
    root = tmp_path / 'instance'
    before = (root / 'settings.json').read_bytes()
    document = research_config.to_document(root)
    selected = tmp_path / 'missing' if replacement == 'missing' else tmp_path
    document['delivery']['adapter']['target'] = dict(directory=str(selected))
    with pytest.raises(ValueError, match='target directory|must not expose the control root'):
        research_config.validate_update(root, document)
    assert (root / 'settings.json').read_bytes() == before
    assert not (tmp_path / 'missing').exists()
    assert list(target.iterdir()) == []


def test_runtime_directory_change_is_a_typed_preparation_failure(tmp_path, monkeypatch):
    target = tmp_path / 'delivery'
    target.mkdir()
    frozen = delivery.policy(policy(generic_delivery.descriptor(target)))
    target.rmdir()
    monkeypatch.setattr(delivery, '_settings', lambda *args: dict(delivery_policy=frozen))
    with pytest.raises(delivery.PreparationUnavailable, match=generic_delivery.PREFLIGHT_FAILED):
        delivery._adapter(tmp_path / 'instance')
    assert not target.exists()


def test_valid_local_setup_keeps_frozen_target_and_selected_exact_references(tmp_path, monkeypatch):
    target = tmp_path / 'delivery'
    target.mkdir()
    config = declaration(tmp_path, target)
    checked = research_config.inspect(config=config)
    assert checked['status'] == 'config_checked'
    assert not (tmp_path / 'instance').exists()
    prepared = research_config.prepare(config)
    assert prepared['delivery_policy']['adapter']['target'] == dict(directory=str(target))
    original = tmp_path / 'retained-result'
    original.write_bytes(b'Exact retained output, never copied into a staging file.\n')
    reference = dict(path=str(original), size_bytes=original.stat().st_size)
    stage = tmp_path / 'stage'
    (stage / 'inputs').mkdir(parents=True)
    (stage / 'inputs/artifact').touch()
    adapter = prepared['delivery_policy']['adapter']
    request = dict(stage=str(stage), phase='promote', target=adapter['target'],
        config=adapter['config'], inputs=adapter['inputs'], object_refs=dict(artifact=reference))
    commands = []
    monkeypatch.setattr(generic_delivery.shutil, 'which', lambda name: '/qualified/bwrap')
    monkeypatch.setattr(generic_delivery.os, 'execv', lambda binary, args: commands.append(args))
    generic_delivery.isolate(request, adapter['adapter'])
    arguments = commands[0]
    assert any(arguments[index:index + 3] == ['--ro-bind', str(original), '/inputs/artifact']
        for index in range(len(arguments) - 2))
    assert request['object_refs'] == dict(artifact=reference)
    assert (stage / 'inputs/artifact').stat().st_size == 0
    assert list(target.iterdir()) == []


@pytest.mark.parametrize('phase,status', [('promote', 'failed'), ('confirm', 'unknown')])
def test_builtin_pre_exec_failure_has_fixed_public_result_and_private_host_diagnostic(
        tmp_path, monkeypatch, capsys, phase, status):
    request = dict(operation_id='a' * 64, candidate_id='original-candidate',
        artifact_ref=dict(path='original', size_bytes=1), phase=phase)
    private = 'private host binding /credentials/never-public'
    def unstarted(*args):
        raise ValueError(private)
    monkeypatch.setattr(generic_delivery, 'isolate', unstarted)
    monkeypatch.setattr(generic_delivery.os, 'dup2', lambda *args: None)
    generic_delivery.isolated_entry(request, str(Path(generic_delivery.__file__).resolve()))
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == dict(operation_id=request['operation_id'], candidate_id=request['candidate_id'],
        artifact_ref=request['artifact_ref'], status=status, note=generic_delivery.PREFLIGHT_FAILED)
    assert private not in captured.out and private in captured.err


def accepted_scene(tmp_path, monkeypatch, spec):
    """An already accepted immutable decision; leaf behavior remains production code."""
    from scidiscovery.research_loop import budget, execution, handoff_state, protocol
    root = tmp_path / 'instance'
    public = tmp_path / 'scene-public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain a bounded synthetic control decision; no model or solver.\n')
    research_config.prepare(root=root, objective=objective, public_root=public,
        launcher=['fixture-never-launch'], model='fixture-no-model', reasoning_effort='high',
        max_tokens=10000, max_seconds=100, max_steps=4, search_policy=None)
    record = root / 'record'
    record.mkdir()
    job = dict(step=1, role='A')
    c.write(root / 'jobs/001.json', job)
    raw = record / 'science-output.json'
    raw.write_text('{}\n')
    seal = record / 'result-identity.json'
    seal.write_text('{}\n')
    reference = dict(path='retained-original', size_bytes=9)
    decision = dict(step=1, job_ref=c.record_ref(root / 'jobs/001.json'),
        raw_ref=c.file_ref(raw), seal_ref=c.record_ref(seal))
    bound = dict(operation_id='b' * 64, candidate_id='original-candidate', decision=decision,
        source=dict(objects=dict(artifact=reference), audit_required=False), target=spec['target'])
    intent = dict(action_id='original-action', operation_id=bound['operation_id'],
        decision=decision, binding=bound, action_kind='submit', advance_seconds=0, max_seconds=10)
    actions = [dict(intent=intent, outcome=None)]
    monkeypatch.setattr(protocol, 'require_current', lambda root: None)
    monkeypatch.setattr(budget, 'closeout_state', lambda root: dict(actions=actions))
    monkeypatch.setattr(budget, 'snapshot', lambda root: dict(advance_seconds=0, seconds_remaining=10))
    monkeypatch.setattr(c, 'workspace', lambda root, step: record)
    monkeypatch.setattr(c, 'host_record', lambda work: record)
    monkeypatch.setattr(execution, 'raw', lambda root, step: raw)
    monkeypatch.setattr(handoff_state, 'read_sealed', lambda root, step: {})
    monkeypatch.setattr(delivery, 'binding', lambda *args: bound)
    monkeypatch.setattr(delivery, '_adapter', lambda *args, **kwargs: spec)
    monkeypatch.setattr(delivery, '_staged', lambda *args, **kwargs:
        dict(operation_id=bound['operation_id'], candidate_id=bound['candidate_id'], artifact_ref=reference))
    return root, intent, actions


@pytest.mark.parametrize('kind,status', [('submit', 'failed'), ('confirm', 'unknown')])
def test_admitted_action_preflight_failure_never_starts_or_recreates_target(tmp_path, monkeypatch, kind, status):
    target = tmp_path / 'delivery'
    spec = dict(target=dict(directory=str(target)))
    root, intent, actions = accepted_scene(tmp_path, monkeypatch, spec)
    intent['action_kind'] = kind
    def unavailable(*args, **kwargs):
        try:
            raise ValueError('private frozen target /host/private-directory')
        except ValueError as error:
            raise delivery.PreparationUnavailable(generic_delivery.PREFLIGHT_FAILED) from error
    monkeypatch.setattr(delivery, '_adapter', unavailable)
    monkeypatch.setattr(delivery.subprocess, 'Popen', lambda *args, **kwargs:
        pytest.fail('Known local preflight failure must not dispatch a process'))
    result = delivery.invoke(root, intent, 10)
    assert result['status'] == status and result['note'] == generic_delivery.PREFLIGHT_FAILED
    assert result['cleanup_confirmed'] and not target.exists()
    assert 'private-directory' not in json.dumps(result)
    diagnostic = c.read(root / delivery.leaf_refs(root, intent['operation_id'])[0]['path'])
    saved = delivery.leaf_output(root) / diagnostic['streams']['stderr']['path']
    assert 'private-directory' in saved.read_text()
    actions[0]['outcome'] = dict(result=result)
    assert delivery.invoke(root, intent, 10) == result
    assert not target.exists()


@pytest.mark.process_e2e
def test_arbitrary_child_failure_after_possible_effect_remains_unknown_and_is_not_replayed(tmp_path, monkeypatch):
    provider = tmp_path / 'provider.py'
    marker = tmp_path / 'effect'
    provider.write_text('''import json,pathlib,sys
request=json.load(sys.stdin)
pathlib.Path(request['target']['marker']).write_text('one possible effect')
raise ValueError('private provider error /credentials/host-only')
''')
    spec = dict(adapter=str(provider), launcher_argv=[sys.executable, str(provider)],
        target=dict(marker=str(marker)))
    root, intent, actions = accepted_scene(tmp_path, monkeypatch, spec)
    calls = []
    result = delivery.invoke(root, intent, 10, on_process=lambda phase, identity: calls.append(phase))
    assert result['status'] == 'unknown' and result['note'] == 'bounded_promote_unverified'
    assert result['cleanup_confirmed'] and marker.read_text() == 'one possible effect'
    assert 'credentials' not in json.dumps(result)
    actions[0]['outcome'] = dict(result=result)
    before = marker.stat().st_mtime_ns
    assert delivery.invoke(root, intent, 10) == result
    assert calls == ['promote'] and marker.stat().st_mtime_ns == before


@pytest.mark.parametrize('phase,status', [('promote', 'failed'), ('confirm', 'unknown')])
def test_spawn_os_failure_is_known_unstarted_without_public_exception_text(tmp_path, monkeypatch, phase, status):
    def unavailable(*args, **kwargs):
        raise OSError('private launcher /credentials/unavailable')
    monkeypatch.setattr(delivery.subprocess, 'Popen', unavailable)
    request = dict(operation_id='c' * 64, candidate_id='original-candidate',
        artifact_ref=dict(path='original', size_bytes=1), phase=phase)
    result = delivery._call(dict(adapter='never-started', launcher_argv=['/never-started']), request, 1,
        evidence=tmp_path / 'host-diagnostics')
    assert result['status'] == status and result['note'] == 'delivery_process_unavailable'
    assert 'credentials' not in json.dumps(result)
