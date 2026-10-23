"""Operational snapshots retain exact historical runtime and delivery originals."""
import copy
import importlib.util
from pathlib import Path
import sys

from scidiscovery.research_loop import (
    budget, candidate_audit, configuration_updates as updates, contracts as c,
    controller, delivery, execution_authority as authority, execution_recovery,
    formal_review as review, profiles, qualification as q, research_config,
)
from execution_fixtures import execution_registry
from test_delivery import delivery_policy
from test_history_handoff import finish


def prepare(tmp_path, **options):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Keep exact original runtime settings.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture'], model='fixture', reasoning_effort='high',
        max_tokens=20_000, max_seconds=300, max_steps=12, search_policy=None, **options)
    return root


def snapshot(root, settings):
    """Queue a validated update through the public editor's submission seam."""
    result = updates.submit(root, research_config.to_document(root, settings),
                            authorization_note='Bounded runtime regression update')
    assert result['status'] == 'configuration_saved'
    number = result['revision']
    path = root / f'host-control/configuration/revisions/{number:06d}.json'
    return dict(revision=number, path=path.relative_to(root).as_posix(), sha256=c.digest(path))


def test_review_and_authority_enable_after_legacy_job_keep_original_contracts(tmp_path):
    root = prepare(tmp_path)
    old, _, _ = finish(root, 1, 'H', [], action='wait')
    settings = copy.deepcopy(c.settings(root))
    settings['formal_review_policy'] = settings['execution_authority_policy'] = dict(version=1)
    reference = snapshot(root, settings)
    settings = updates.current(root)
    job = c.build_job(root, 2, 'H', [])
    assert job['configuration_revision'] == reference
    assert 'execution_authority' not in old
    assert review.contract(root, old) is None
    assert authority.contract(root, job) == job['execution_authority']
    assert review.contract(root, job) == settings['formal_review_policy']['contract']
    assert not (root / 'qualification/review-contract.json').exists()
    assert not (root / 'execution-authority/contract.json').exists()
    disabled = copy.deepcopy(settings)
    disabled['formal_review_policy'] = disabled['execution_authority_policy'] = None
    snapshot(root, disabled)
    assert review.contract(root) is None
    assert review.contract(root, job) == settings['formal_review_policy']['contract']
    assert authority.contract(root, job) == settings['execution_authority_policy']['contract']
    authority.validate_job(root, old)
    authority.validate_job(root, job)
    c.validate(c.workspace(root, 1))


def test_delivery_confirmation_uses_accepted_decision_adapter_after_update(tmp_path, monkeypatch):
    adapter = tmp_path / 'adapter.py'; adapter.write_text('# Frozen deterministic adapter fixture.\n')
    target = tmp_path / 'original-target'
    spec = dict(adapter=str(adapter), launcher_argv=[sys.executable, str(adapter)],
        config={}, target=dict(directory=str(target)), inputs=[], source_roots=[str(tmp_path)],readonly_inputs_version=1)
    root = prepare(tmp_path, delivery_policy=delivery_policy(spec),
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'})
    job, _, _ = finish(root, 1, 'A', [], action='wait')
    old_adapter = delivery._adapter(root, job)
    changed = copy.deepcopy(c.settings(root))
    changed['delivery_policy']['adapter']['target'] = dict(directory=str(tmp_path / 'new-target'))
    snapshot(root, changed)
    assert delivery._adapter(root)['target'] != old_adapter['target']
    from scidiscovery.research_loop import shared_originals
    artifact = shared_originals.publish_bytes(root, b'original')
    decision = candidate_audit._decision(root, 1)
    source = dict(candidate_id='original', context={}, objects=dict(artifact=q._ref(root, artifact)))
    bound = dict(candidate_id='original', operation_id='a'*64, decision=decision, source=source,
                 target=old_adapter['target'])
    intent = dict(action_id='confirm-original', action_kind='confirm', operation_id=bound['operation_id'],
                  decision=decision, binding=bound, max_seconds=10, advance_seconds=0)
    monkeypatch.setattr(budget, 'closeout_state', lambda root:dict(actions=[dict(intent=intent,outcome=None)]))
    monkeypatch.setattr(budget, 'snapshot', lambda root:dict(advance_seconds=0, seconds_remaining=30))
    monkeypatch.setattr(delivery, 'binding', lambda *args:bound)
    calls = []
    def confirm(spec, request, seconds, **kwargs):
        calls.append((spec['target'], request['phase']))
        return dict(status='unknown', operation_id=request['operation_id'], candidate_id='original',
                    artifact_sha256=c.digest(artifact), note='Original call remains unknown')
    monkeypatch.setattr(delivery, '_call', confirm)
    result = delivery.invoke(root, intent, 10)
    assert result['status'] == 'unknown'
    assert calls == [(old_adapter['target'], 'confirm')]


def test_disabled_policy_keeps_unknown_execution_from_enabled_revision(tmp_path):
    root = prepare(tmp_path)
    settings = copy.deepcopy(c.settings(root))
    settings['formal_review_policy'] = settings['execution_authority_policy'] = dict(version=1)
    snapshot(root, settings)
    settings = updates.current(root)
    pending = dict(path='native-evidence/001/pending.json', sha256='b'*64)
    seal = root / 'native-evidence/001/result-identity.json'
    c.write(seal, dict(job_sha256='a'*64, pending_executions=pending))
    disabled = copy.deepcopy(settings); disabled['execution_authority_policy'] = None
    snapshot(root, disabled)
    assert updates.current(root).get('execution_authority_policy') is None
    sources = execution_recovery._current_pending_sources(root)
    assert len(sources) == 1
    assert sources[0][2]['pending_executions'] == pending


def test_cov_launcher_uses_verified_revision_profile_and_keeps_base_assertion(tmp_path, monkeypatch):
    root = prepare(tmp_path)
    finish(root, 1, 'H', [], action='wait')
    settings = copy.deepcopy(c.settings(root))
    settings['role_profiles'] = profiles.normalize({'H':dict(model='revised-model')}, settings)
    snapshot(root, settings)
    job = c.build_job(root, 2, 'H', [])
    path = Path(__file__).resolve().parents[2] / 'scripts/acceptance/rootless_cov_launcher.py'
    from cov_execution import launcher
    monkeypatch.setenv('THIN_HWA_ROLE', 'H')
    monkeypatch.setenv('THIN_HWA_JOB_MANIFEST', str(root / 'jobs/002.json'))
    declared = dict(c.settings(root), scene_root=str(root))
    assert launcher.native_profile(declared) == job['native_profile']
    assert launcher.native_profile(declared)['model'] == 'revised-model'
    import pytest
    with pytest.raises(ValueError, match='launcher_role_profiles_mismatch'):
        launcher.native_profile(dict(declared, model='invented-launcher-model'))


def test_cov_accepted_attempt_uses_private_skill_context_and_rejects_changed_role_model(tmp_path, monkeypatch, execution_registry):
    import os
    import pytest
    from cov_execution import launcher
    from test_skill_supply import prepare as prepare_skills
    from scidiscovery.research_loop import backends, execution, runtime

    for named_worker in (False, True):
        case = tmp_path / ('named' if named_worker else 'role'); case.mkdir()
        role = dict(worker='analyst') if named_worker else dict(skill_set='first')
        workers = {'analyst': dict(model='worker-specific-model', skill_set='first')} if named_worker else None
        operator_file = case / 'operator.json'
        operator_file.write_text('{}')
        module_launcher = [sys.executable, '-m', 'cov_execution.launcher', '--config', str(operator_file)]
        root, _ = prepare_skills(case, roles={'H': role}, workers=workers, launcher=module_launcher)
        job = c.build_job(root, 1, 'H', [])
        assert job.get('configuration_revision') is None
        record = execution.record(root, 1)
        frozen = updates.for_job(root, job)
        c.write(record / 'native-intent.json', dict(
            task_sha256=c.digest(root / 'jobs/001.json'),
            budget_slice=budget.accept_node(root, job), **profiles.binding(frozen, job)))
        identity = execution.identity(root, 1)
        c.write(record / 'runner-process.json', runtime.process_identity(os.getppid(), identity))
        c.write(record / 'native-process.json', runtime.process_identity(os.getpid(), identity))
        monkeypatch.setenv('THIN_HWA_ROLE', 'H')
        monkeypatch.setenv('THIN_HWA_JOB_MANIFEST', str(root / 'jobs/001.json'))
        monkeypatch.setenv('THIN_HWA_NATIVE_HOME', str(execution.home(root, 1)))
        declared = dict(scene_root=str(root), model='fixture', reasoning_effort='high',
                        role_profiles={'H': role})
        assert 'skills' not in declared and 'worker_profiles' not in declared
        output = record / 'result.pending.json'
        work = c.workspace(root, 1)
        schema = record / 'science-result.schema.json'
        expected = profiles.for_job(frozen, job)
        assert expected['model'] == ('worker-specific-model' if named_worker else 'fixture')
        backend = backends.selected(frozen)
        assert backend.launcher == module_launcher and not backend.supports_native_schema_flag
        assert frozen['skills']['launcher']['provider']['native_launcher_module'] == 'cov_execution.launcher'
        argv = backend.launch(expected, work, output, schema,
            job.get('native_role_instruction', c.ROLE_INSTRUCTIONS['H']))[len(backend.launcher):]
        assert '--output-schema' not in argv
        without_role_refs = {key: value for key, value in declared.items() if key != 'role_profiles'}
        for operator in (declared, without_role_refs):
            assert launcher.native_profile(operator) == expected
            assert launcher.accepted_attempt(operator, work, output) == schema
            effective = launcher.native_args(operator, work, argv)
            assert effective[effective.index('--model') + 1] == expected['model']
            assert effective[effective.index('--output-schema') + 1] == str(schema)
        changed = dict(declared, role_profiles={'H': dict(role, model='changed-launcher-model')})
        for consume in (lambda: launcher.native_profile(changed),
                        lambda: launcher.accepted_attempt(changed, work, output),
                        lambda: launcher.native_args(changed, work, argv)):
            with pytest.raises(ValueError, match='launcher_role_profiles_mismatch'):
                consume()
