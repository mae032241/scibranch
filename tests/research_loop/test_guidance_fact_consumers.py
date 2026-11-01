"""Guidance originals have one writer owner and explicit downstream consumers."""
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, configuration_updates, contracts as c, controller,
    user_guidance,
)
from test_history_handoff import scene, finish


def _no_read(*args, **kwargs):
    pytest.fail('A guidance/admission consumer reopened a produced fact')


def test_guidance_consumers_publish_through_the_real_binding_writer_without_reads(scene, monkeypatch):
    advice = user_guidance.submit(scene, 'Keep the missing observation explicit.', target='E')
    base = c.settings(scene)
    with user_guidance.binding_from_base(scene, 1, 'E', None, base=base) as (statements, current):
        job = dict(step=1, role='E', user_guidance=statements)
        c.write(scene / 'jobs/001.json', job)
        facts = accepted_budget.job_acceptance_facts(scene, 1, base=base)
        with monkeypatch.context() as blocked:
            blocked.setattr(c, 'read', _no_read)
            blocked.setattr(c, 'settings', _no_read)
            blocked.setattr(Path, 'read_text', _no_read)
            blocked.setattr(Path, 'read_bytes', _no_read)
            blocked.setattr(accepted_budget.AcceptedState, 'row', _no_read)
            assert user_guidance.select_binding(1, 'E', None,
                current=current, revocations={}) == statements
            state = accepted_budget.accept_job_from_facts(scene, 1, job,
                facts=facts, guidance_current=current)
        assert state.current_job['step'] == 1
    path = scene / 'host-control/guidance' / f'{advice["guidance_id"]}.json'
    assert c.read(path) == statements[0]
    assert accepted_budget.read(scene)['prepared_count'] == 1

    # The retained acceptance branch also consumes its already produced row.
    with budget._ledger_writer_from_base(scene, base=base):
        facts = accepted_budget.job_acceptance_facts(scene, 1, base=base)
        with monkeypatch.context() as blocked:
            blocked.setattr(c, 'read', _no_read)
            blocked.setattr(accepted_budget.AcceptedState, 'row', _no_read)
            retained = accepted_budget.accept_job_from_facts(scene, 1, job,
                facts=facts, guidance_current={})
        assert retained['prepared_count'] == 1


def test_guidance_revision_rejection_consumes_the_exact_current_original(scene, monkeypatch):
    advice = user_guidance.submit(scene, 'Keep the unresolved measurement.', target='E')
    base = c.settings(scene)
    with user_guidance.binding_from_base(scene, 1, 'E', None, base=base) as (statements, current):
        job = dict(step=1, role='E',
            user_guidance=[dict(statements[0], revision=statements[0]['revision'] + 1)])
        c.write(scene / 'jobs/001.json', job)
        facts = accepted_budget.job_acceptance_facts(scene, 1, base=base)
        with monkeypatch.context() as blocked:
            blocked.setattr(c, 'read', _no_read)
            blocked.setattr(accepted_budget.AcceptedState, 'row', _no_read)
            with pytest.raises(ValueError, match='Frozen guidance revision changed'):
                accepted_budget.accept_job_from_facts(scene, 1, job,
                    facts=facts, guidance_current=current)
    assert accepted_budget.read(scene).current_job is None
    path = scene / 'host-control/guidance' / f'{advice["guidance_id"]}.json'
    assert c.read(path)['status'] == 'pending'


def test_each_new_binding_refreshes_guidance_and_revocation_once(scene, monkeypatch):
    advice = user_guidance.submit(scene, 'Retain this distinction.', target='E', scope='research')
    guidance_path = scene / 'host-control/guidance' / f'{advice["guidance_id"]}.json'
    revocation_path = scene / 'host-control/guidance-revocations' / guidance_path.name
    reads = []
    original_read = c.read

    def observed(path, *args, **kwargs):
        if Path(path) in (guidance_path, revocation_path):
            reads.append(Path(path))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(c, 'read', observed)
    first = c.build_job(scene, 1, 'E', [])
    assert reads == [guidance_path]
    first_bytes = (scene / 'jobs/001.json').read_bytes()
    finish(scene, 1, 'E', [])
    user_guidance.revoke(scene, advice['guidance_id'], expected_revision=advice['revision'])
    reads.clear()
    following = c.build_job(scene, 2, 'E', [1])
    assert not following.get('user_guidance')
    assert reads == [guidance_path, revocation_path]
    assert c.build_job(scene, 1, 'E', []) == first
    assert (scene / 'jobs/001.json').read_bytes() == first_bytes


def test_configuration_binding_reuses_base_through_real_controller_and_ledger_writers(scene, monkeypatch):
    base = c.settings(scene)
    monkeypatch.setattr(c, 'settings', _no_read)
    revision, policy, head = configuration_updates.bind_new_job_facts(scene, 1, base=base)
    assert revision is None and policy is base
    assert head.base is base
    with budget._operator_writer_from_base(scene, base=base):
        controller.require_writer(scene)
        retained_revision, retained_policy, _ = configuration_updates.bind_new_job_facts(scene, 1, base=base)
        assert retained_revision == revision and retained_policy is base
        with pytest.raises(RuntimeError, match='already has a running controller'):
            with controller._local_writer_from_base(scene, base=base):
                pytest.fail('A second writer acquired the held original lock')


def test_strict_controller_writer_keeps_original_descriptor_identity_check(scene, monkeypatch):
    base = c.settings(scene)
    monkeypatch.setattr(c, 'settings', _no_read)
    descriptor = os.open(scene / 'unrelated.lock', os.O_CREAT | os.O_RDWR, 0o600)
    (scene / 'host-run.lock').touch(exist_ok=True)
    try:
        with pytest.raises(ValueError, match='lost the original writer lock'):
            with controller._local_writer_from_base(scene, base=base, descriptor=descriptor):
                pytest.fail('A different lock inode was accepted')
    finally:
        os.close(descriptor)
