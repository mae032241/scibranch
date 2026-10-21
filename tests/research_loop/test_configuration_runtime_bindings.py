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
        config={}, target=dict(directory=str(target)), inputs=[], source_roots=[str(tmp_path)])
    root = prepare(tmp_path, delivery_policy=delivery_policy(spec),
        node_budget_policy={role:dict(max_observed_tokens=1000,max_seconds=30) for role in 'HWAE'})
    job, _, _ = finish(root, 1, 'A', [], action='wait')
    old_adapter = delivery._adapter(root, job)
    changed = copy.deepcopy(c.settings(root))
    changed['delivery_policy']['adapter']['target'] = dict(directory=str(tmp_path / 'new-target'))
    snapshot(root, changed)
    assert delivery._adapter(root)['target'] != old_adapter['target']
    artifact = root / 'host-delivery/objects/exact'; artifact.parent.mkdir(parents=True); artifact.write_bytes(b'original')
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
    spec = importlib.util.spec_from_file_location('revision_cov_launcher', path)
    launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
    monkeypatch.setenv('THIN_HWA_ROLE', 'H')
    monkeypatch.setenv('THIN_HWA_JOB_MANIFEST', str(root / 'jobs/002.json'))
    declared = dict(c.settings(root), scene_root=str(root))
    assert launcher.native_profile(declared) == job['native_profile']
    assert launcher.native_profile(declared)['model'] == 'revised-model'
    import pytest
    with pytest.raises(ValueError, match='launcher_role_profiles_mismatch'):
        launcher.native_profile(dict(declared, model='invented-launcher-model'))
