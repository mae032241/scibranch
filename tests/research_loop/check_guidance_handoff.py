"""Guidance binding uses its own status and the exact task being accepted."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, user_guidance
from test_history_handoff import scene, finish


def test_binding_never_enumerates_historical_jobs(scene, monkeypatch):
    original = Path.glob

    def glob(path, pattern):
        if path == scene / 'jobs':
            pytest.fail('Guidance binding enumerated historical jobs')
        return original(path, pattern)

    monkeypatch.setattr(Path, 'glob', glob)
    with user_guidance.binding(scene, 1, 'E', None) as values:
        assert values == []
    advice = user_guidance.submit(scene, 'Compare the retained control.', target='E')
    with user_guidance.binding(scene, 1, 'H', None) as values:
        assert values == []
    with user_guidance.binding(scene, 1, 'E', None) as values:
        assert [value['guidance_id'] for value in values] == [advice['guidance_id']]


def test_interrupted_task_acceptance_applies_guidance_once_without_history_scan(scene, monkeypatch):
    advice = user_guidance.submit(scene, 'Retain the missing evidence.', target='E')
    apply = user_guidance.apply_job

    def interrupted(root, job):
        assert (root / 'jobs/001.json').is_file()
        raise OSError('fixture interruption after task publication')

    monkeypatch.setattr(user_guidance, 'apply_job', interrupted)
    with pytest.raises(OSError, match='fixture interruption'):
        c.build_job(scene, 1, 'E', [])
    task_bytes = (scene / 'jobs/001.json').read_bytes()
    assert accepted_budget.read(scene).current_job is None
    path = scene / 'host-control/guidance' / (advice['guidance_id'] + '.json')
    assert c.read(path)['status'] == 'pending'
    monkeypatch.setattr(user_guidance, 'apply_job', apply)
    job = c.build_job(scene, 1, 'E', [])
    assert (scene / 'jobs/001.json').read_bytes() == task_bytes
    assert c.read(path) == job['user_guidance'][0]
    assert c.read(path)['status'] == 'applied'
    finish(scene, 1, 'E', [])
    following = c.build_job(scene, 2, 'E', [1])
    assert not following.get('user_guidance')
    assert c.read(path)['applied_step'] == 1
