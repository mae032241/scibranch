"""Current frozen budgets reject old shapes before reading or launching jobs."""

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, instance_control, native, research_config
from scidiscovery.research_loop.protocol import UnsupportedProtocol, require_current, require_settings_format


def prepare(tmp_path, *, nodes=False):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain original work and every consumed budget fact.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=200, max_steps=8,
        model='fixture-no-model', reasoning_effort='high', search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=10)
                            for role in 'HWAE'} if nodes else None)
    return root


@pytest.mark.parametrize('old_shape', ['missing_final_policy', 'hard_mode', 'missing_mode'])
def test_old_budget_shape_refused_before_job_or_adapter(tmp_path, monkeypatch, old_shape):
    root = prepare(tmp_path)
    settings = c.settings(root)
    if old_shape == 'missing_final_policy':
        settings.pop('final_a_completion_policy')
    elif old_shape == 'missing_mode':
        settings.pop('node_budget_mode')
    else:
        settings['node_budget_mode'] = 'hard'
    c.write(root / 'settings.json', settings)
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    originals = {path: path.read_bytes() for path in
                 (root / 'settings.json', root / 'settings-identity.json')}

    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported settings cannot launch native work or an adapter')

    monkeypatch.setattr(native, 'execute_native', forbidden)
    monkeypatch.setattr(delivery, 'invoke', forbidden)
    for enter in (lambda: controller.run(root), lambda: instance_control.status(root),
                  lambda: require_current(root, dict(step=1)),
                  lambda: research_config.inspect(root=root),
                  lambda: research_config.inspect(root=root, view='current'),
                  lambda: research_config.to_document(root)):
        with pytest.raises(UnsupportedProtocol, match='Instance budget policy'):
            enter()
    assert not (root / 'jobs/001.json').exists()
    assert not (root / 'host-started.json').exists()
    assert all(path.read_bytes() == original for path, original in originals.items())


def test_current_reserve_apis_and_optional_node_policy_remain_available(tmp_path):
    root = prepare(tmp_path)
    settings = c.settings(root)
    assert require_settings_format(settings) is settings
    assert budget.node_mode(root) == 'soft'
    assert budget.node_limits(root, dict(step=1, role='H'), settings=settings) is None
    assert budget.node_limits(root, dict(step=1, role='A', final_only=True), settings=settings) == dict(
        max_observed_tokens=1000, max_seconds=200)
    assert budget.delivery_reserve(settings) == dict(tokens=0, seconds=0., steps=0)
    assert budget.closeout_floor(settings, preparation_seconds=123) == dict(tokens=0, seconds=0., steps=0)
    assert budget.final_a_admission(root, dict(step=1, role='A')) is None


def test_current_soft_thinking_share_reduces_only_h_and_a_targets(tmp_path):
    root = prepare(tmp_path, nodes=True)
    settings = dict(c.settings(root), role_budget_policy=dict(thinking_node_target_tokens=40))
    for role in 'HA':
        assert budget.node_limits(root, dict(step=1, role=role), settings=settings) == dict(
            max_observed_tokens=40, max_seconds=10)
    for role in 'WE':
        assert budget.node_limits(root, dict(step=1, role=role), settings=settings) == dict(
            max_observed_tokens=100, max_seconds=10)
    settings['role_budget_policy']['thinking_node_target_tokens'] = 200
    assert budget.node_limits(root, dict(step=1, role='H'), settings=settings)['max_observed_tokens'] == 100
