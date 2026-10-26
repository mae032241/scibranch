"""Operational snapshots retain exact historical runtime and delivery originals."""
import copy
import sys

import pytest

from scidiscovery.research_loop import (
    budget, candidate_audit, configuration_updates as updates, contracts as c,
    controller, delivery, execution_authority as authority, execution_recovery,
    formal_review as review, profiles, qualification as q, research_config,
)
from execution_fixtures import execution_registry
from test_delivery import delivery_policy
from test_direct_delivery_final import publish


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
    return dict(revision=number, path=path.relative_to(root).as_posix(), )


def test_review_and_authority_enable_after_prior_job_keep_original_contracts(tmp_path):
    root = prepare(tmp_path)
    old = c.build_job(root, 1, 'H', [])
    publish(root, old, action='wait')
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
    job = c.build_job(root, 1, 'A', [])
    publish(root, job, action='wait')
    old_adapter = delivery._adapter(root, job)
    changed = copy.deepcopy(c.settings(root))
    changed['delivery_policy']['adapter']['target'] = dict(directory=str(tmp_path / 'new-target'))
    snapshot(root, changed)
    assert delivery._adapter(root)['target'] != old_adapter['target']
    from scidiscovery.research_loop import shared_originals
    artifact = shared_originals.publish_bytes(root, b'original')
    decision = candidate_audit._decision(root, 1)
    source = dict(candidate_id='original', context={}, objects=dict(artifact=q._ref(root, artifact)),
                  artifact_origin=c.file_ref(artifact))
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
                    artifact_ref=source['objects']['artifact'], note='Original call remains unknown')
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
    pending = dict(path='native-evidence/001/pending.json', size_bytes=0)
    seal = root / 'native-evidence/001/result-identity.json'
    c.write(seal, dict(job_ref=dict(path='/fixture/jobs/002.json'), pending_executions=pending))
    disabled = copy.deepcopy(settings); disabled['execution_authority_policy'] = None
    snapshot(root, disabled)
    assert updates.current(root).get('execution_authority_policy') is None
    sources = execution_recovery._current_pending_sources(root)
    assert len(sources) == 1
    assert sources[0][2]['pending_executions'] == pending



def test_runtime_profile_uses_verified_job_revision(tmp_path):
    root = prepare(tmp_path)
    publish(root, c.build_job(root, 1, 'H', []), action='wait')
    base = c.settings(root)
    settings = copy.deepcopy(base)
    settings['role_profiles'] = profiles.normalize({'H':dict(model='revised-model')}, settings)
    reference = snapshot(root, settings)
    job = c.build_job(root, 2, 'H', [])
    frozen = updates.for_job(root, job)
    expected = profiles.for_job(frozen, job)
    assert job['configuration_revision'] == reference
    assert expected == job['native_profile']
    assert expected['model'] == 'revised-model' and base['model'] == 'fixture'
    binding = profiles.binding(frozen, job)
    assert profiles.validate_intent(frozen, job, binding) == expected
    changed = copy.deepcopy(binding)
    changed['native_profile']['model'] = 'invented-runtime-model'
    with pytest.raises(ValueError, match='profile changed'):
        profiles.validate_intent(frozen, job, changed)


def test_native_backend_uses_private_skill_and_named_worker_profiles(tmp_path):
    from test_skill_supply import prepare as prepare_skills
    from scidiscovery.research_loop import backends, execution

    for named_worker in (False, True):
        case = tmp_path / ('named' if named_worker else 'role'); case.mkdir()
        role = dict(worker='analyst') if named_worker else dict(skill_set='first')
        workers = {'analyst': dict(model='worker-specific-model', skill_set='first')} if named_worker else None
        root, _ = prepare_skills(case, roles={'H': role}, workers=workers)
        job = c.build_job(root, 1, 'H', [])
        frozen = updates.for_job(root, job)
        expected = profiles.for_job(frozen, job)
        assert expected['model'] == ('worker-specific-model' if named_worker else 'fixture')
        binding = profiles.binding(frozen, job)
        assert profiles.validate_intent(frozen, job, binding) == expected
        record = execution.record(root, 1)
        output = record / 'result.pending.json'
        schema = record / 'science-result.schema.json'
        backend = backends.selected(frozen)
        assert backend.standard and backend.supports_native_schema_flag
        argv = backend.launch(expected, c.workspace(root, 1), output, schema,
            job.get('native_role_instruction', c.ROLE_INSTRUCTIONS['H']))
        assert argv[argv.index('--model') + 1] == expected['model']
        assert argv[argv.index('--output-schema') + 1] == str(schema)
        changed = copy.deepcopy(binding)
        changed['native_profile']['model'] = 'changed-runtime-model'
        with pytest.raises(ValueError, match='profile changed'):
            profiles.validate_intent(frozen, job, changed)
