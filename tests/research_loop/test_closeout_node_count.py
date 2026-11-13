"""Closeout counting regression; the retired A decision-reuse assessor is gone."""
from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, native


def test_closeout_phase_does_not_count_earlier_final_nodes_twice(tmp_path, monkeypatch):
    root = tmp_path/'instance'
    for step in (1, 2, 4):
        c.write(root/'jobs'/f'{step:03d}.json', dict(step=step, role='A', purpose='closeout', final_only=step<4))
        c.write(root/'native-evidence'/f'{step:03d}'/'result-identity.json', {})
        c.write(root/'native-evidence'/f'{step:03d}'/'native-outcome.json', {})
    state = dict(begin=dict(mode='model', first_step=4, source_cutoff=3, seq=1),
        actions=[], unresolved_operations=[])
    monkeypatch.setattr(budget, 'closeout_state', lambda root: state)
    monkeypatch.setattr(budget, 'status_snapshot', lambda root: {})
    monkeypatch.setattr(budget, 'used_steps', lambda root: 4)
    monkeypatch.setattr(native, 'usage', lambda root: {})
    monkeypatch.setattr(accepted_budget, 'read', lambda root: dict(max_local_step=4))
    monkeypatch.setattr(controller.configuration_updates, 'current', lambda root: {})
    monkeypatch.setattr(controller, '_record', lambda root, step: root/'native-evidence'/f'{step:03d}')
    monkeypatch.setattr(controller.execution, 'record', lambda root, step: root/'native-evidence'/f'{step:03d}')
    monkeypatch.setattr(controller.execution, 'raw', lambda root, step: root/f'science-{step}.json')
    slots, science = [], []
    def accepted(root, step):
        slots.append(step)
        return dict(actual_submission=None, a_recommended=None, latest_a_step=step)
    def sealed(root, step):
        science.append(step)
        return dict(role='A', next_action='finish', limitations=[])
    monkeypatch.setattr(controller, '_closeout_accepted_slots', accepted)
    monkeypatch.setattr(controller.handoff_state, 'read_sealed', sealed)
    result = controller._closeout_result(root, 'delivery_unknown', read_only=True)
    assert result['completed_scientific_nodes'] == 4
    assert result['source_cutoff'] == 3 and result['last_step'] == 4
    assert slots == [3, 4] and science == [4]
