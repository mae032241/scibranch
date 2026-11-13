"""Editor declarations reach frozen exploration consumers and real local work."""

import pytest

from scidiscovery.research_loop import budget, configuration_form as form, configuration_updates as updates, contracts as c, execution, native, role_share
from test_configuration_form import Controls, set_setting
from test_history_handoff import finish
from test_node_budget import instance as native_instance, policy as node_policy
from test_soft_node_budget import start_clock


def save(root, **changes):
    """Submit the same successful typed HTML controls as the instance editor."""
    state = updates.status(root)
    fields = Controls(form.render(form.imported(state['configuration'], base=root), edit=True)).fields
    for path, value in changes.items():
        set_setting(fields, 'budget.' + path, value)
        if path.startswith('role_shares.'):
            fields['enabled.budget.role_shares'] = 'yes'
    draft = form.parse_edit(fields, base=root, configuration=state['configuration'])
    return updates.submit(root, draft['document'], expected_revision=state['revision'])


@pytest.mark.process_e2e
def test_saved_thinking_target_is_real_native_advice_with_original_meter(tmp_path):
    root = native_instance(tmp_path, limits=node_policy(tokens=1000), observed=(70, 69, 5))
    old, _, _ = finish(root, 1, 'H', [], action='wait')
    saved = save(root, **{'role_shares.' + key: value for key, value in
        dict(role_share.DEFAULT, thinking_node_target_tokens=17).items()})
    assert saved['pending'] and budget.node_limits(root, old)['max_observed_tokens'] == 1000
    job = c.build_job(root, 2, 'H', [])
    assert budget.node_limits(root, job)['max_observed_tokens'] == 17
    start_clock(root, 'real updated thinking target')
    native.execute_native(root, 2)
    record = execution.record(root, 2)
    outcome = c.read(record / 'native-outcome.json')
    assert outcome['stop_reason'] is None
    assert outcome['budget_slice']['observed_tokens'] == 75
    assert outcome['budget_slice']['limits']['max_observed_tokens'] == 17
    assert c.validate(c.workspace(root, 2))['summary'] == 'A bounded finding'
    hint = c.read(c.workspace(root, 2) / 'work/budget-status.json')
    assert hint['targets']['max_observed_tokens'] == 17
    assert hint['scope'] == 'advice_only_not_authorization_or_scientific_evidence'
