"""Editor declarations reach frozen exploration consumers and real local work."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    breadth, budget, configuration_form as form, configuration_updates as updates,
    contracts as c, execution, native, role_share, search,
)
from execution_fixtures import execution_registry
from test_breadth_science import prepare as breadth_instance, h, worker, run, findings
from test_configuration_form import Controls
from test_history_handoff import finish
from test_node_budget import instance as native_instance, policy as node_policy
from test_role_budget_share import scene as role_instance, seal, PLAN, BIG
from test_search import worker as search_worker, assess, focus
from test_soft_node_budget import start_clock


def save(root, **changes):
    """Submit the same successful typed HTML controls as the instance editor."""
    state = updates.status(root)
    fields = Controls(form.render(form.imported(state['configuration'], base=root), edit=True)).fields
    for path, value in changes.items():
        fields['cfg.budget.' + path] = str(value)
        if path.startswith('role_shares.'):
            fields['enabled.budget.role_shares'] = 'yes'
    draft = form.parse_edit(fields, base=root, configuration=state['configuration'])
    return updates.submit(root, draft['document'], expected_revision=state['revision'])


@pytest.mark.parametrize('dimension,custom,expected', [
    ('thinking', dict(min_w_share=.5, max_thinking_share=.2), 'role_share_thinking_reached'),
    ('w_floor', dict(min_w_share=.8, max_thinking_share=.15), 'role_share_w_floor_reached'),
])
def test_saved_shares_change_optional_route_only_for_new_job(tmp_path, dimension, custom, expected):
    root = role_instance(tmp_path)
    if dimension == 'thinking':
        seal(root, 1, 'H', [], action='analyze', plan=PLAN, tokens=1_000_000)
        old, result = seal(root, 2, 'A', [1], tokens=1_500_000)
    else:
        seal(root, 1, 'H', [], action='extract', plan=PLAN, tokens=500_000)
        old, result = seal(root, 2, 'E', [1], tokens=2_000_000)
    originals = {p: p.read_bytes() for p in (root / 'settings.json', root / 'jobs/002.json',
        execution.record(root, 2) / 'usage.json', execution.record(root, 2) / 'result-identity.json')}
    assert role_share.reason(root, 2, old, result, 'H') is None
    saved = save(root, **{'role_shares.' + key: value for key, value in custom.items()})
    assert saved['pending']
    assert role_share.reason(root, 2, old, result, 'H') is None
    new, result = seal(root, 3, 'A', [1, 2], tokens=10)
    assert role_share.reason(root, 3, new, result, 'H') == expected
    assert role_share.reason(root, 3, new, result, 'H', correction_request={'trigger_id': 'required'}) is None
    assert role_share.reason(root, 3, new, result, 'W') is None
    route = ('H', [1, 2, 3], 'Compare alternatives', None, 1, None, None, None)
    assert role_share.effective_route(root, 3, result, new, route)[-1] == expected
    assert role_share.usage(root, 3)['thinking'] < role_share.DEFAULT['max_thinking_share'] * BIG
    assert all(path.read_bytes() == raw for path, raw in originals.items())


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


@pytest.mark.parametrize('field,value', [('max_w_seconds', .001), ('max_w_observed_tokens', 1)])
@pytest.mark.process_e2e
def test_saved_w_target_reaches_hint_without_stopping_native_work(tmp_path, field, value):
    root = native_instance(tmp_path, search_policy=search.DEFAULT_POLICY, observed=(70, 69, 5))
    h(root, labels=('P',))
    old, _, _ = finish(root, 2, 'W', [1], mechanism_findings=findings(blocked=('h001.m1',)))
    original = (root / 'jobs/002.json').read_bytes()
    save(root, **{'breadth.' + field: value})
    assert old['search']['limits'][field] == search.DEFAULT_POLICY[field]
    job = c.build_job(root, 3, 'W', [1])
    assert job['search']['limits'][field] == value
    start_clock(root, 'real updated W target')
    native.execute_native(root, 3)
    outcome = c.read(execution.record(root, 3) / 'native-outcome.json')
    assert outcome['stop_reason'] is None and outcome['search_slice']['observed_tokens'] == 75
    hint = c.read(c.workspace(root, 3) / 'work/budget-status.json')
    key = 'max_seconds' if field == 'max_w_seconds' else 'max_observed_tokens'
    assert hint['targets'][key] == value
    assert hint['target_state'] == 'reached'
    assert c.validate(c.workspace(root, 3))['stage_intent'] == 'review'
    assert (root / 'jobs/002.json').read_bytes() == original
    assert not (execution.record(root, 2) / 'native-process.json').exists()


@pytest.mark.process_e2e
def test_saved_initial_calls_changes_registered_admission_without_refunding_originals(tmp_path, execution_registry):
    root = breadth_instance(tmp_path)
    h(root, labels=('P', 'Q'))
    old, original_config = worker(root, tmp_path, 2, [1])
    first = run(root, original_config, 'P')
    assert first['status'] == 'known_success'
    accepted_path = Path(original_config['record_dir']) / 'calls' / first['call_id'] / 'accepted.json'
    original = accepted_path.read_bytes()
    save(root, **{'breadth.initial_calls': 2})
    with pytest.raises(breadth.OpportunityRequired):
        run(root, original_config, 'P')
    finish(root, 2, 'W', [1], mechanism_findings=findings('P', blocked=('Q',)), preserve_native_intent=True)
    new, config = worker(root, tmp_path, 3, [1, 2])
    assert old['search']['breadth']['initial_calls'] == 1
    assert new['search']['breadth']['initial_calls'] == config['breadth']['initial_calls'] == 2
    assert run(root, config, 'P')['status'] == 'known_success'
    with pytest.raises(breadth.OpportunityRequired):
        run(root, config, 'P')
    assert run(root, config, 'Q')['status'] == 'known_success'
    assert run(root, config, 'Q')['status'] == 'known_success'
    assert run(root, config, 'P')['status'] == 'known_success'
    assert accepted_path.read_bytes() == original


@pytest.mark.parametrize('cap', [1, 3])
@pytest.mark.process_e2e
def test_saved_requested_repair_cap_controls_exact_original_chain(tmp_path, execution_registry, cap):
    root = breadth_instance(tmp_path)
    h(root, labels=('P', 'Q'))
    old, _, _ = search_worker(root, 2, [1])
    save(root, **{'breadth.max_requested_repairs': cap})
    source = 2
    for index in range(cap):
        a_step, w_step = 3 + 2 * index, 4 + 2 * index
        assess(root, a_step, [1, source], search_choice=dict(dispositions=[], next_focus=focus('h001.m1', 'repair')))
        job, _, _ = search_worker(root, w_step, [1, source, a_step])
        assert job['search']['limits']['max_requested_repairs'] == cap
        assert job['search']['repair_root'] == 2
        source = w_step
    a_step = 3 + 2 * cap
    assess(root, a_step, [1, source], search_choice=dict(dispositions=[], next_focus=focus('h001.m1', 'repair')))
    denied = c.build_job(root, a_step + 1, 'W', [1, source, a_step])
    assert search.launch_reason(root, denied) == 'search_repair_limit'
    assert search.project(root, a_step + 1)['repairs'] == {2: cap}
    assert old['search']['limits']['max_requested_repairs'] == search.DEFAULT_POLICY['max_requested_repairs']


def test_unsupported_share_version_is_rejected_before_revision_or_binding(tmp_path):
    root = role_instance(tmp_path)
    old, _ = seal(root, 1, 'H', [], plan=PLAN)
    before = (root / 'jobs/001.json').read_bytes()
    with pytest.raises(ValueError, match='version 1'):
        save(root, **{'role_shares.version': 2})
    assert updates.revisions(root) == []
    assert updates.for_job(root, old)['role_budget_policy']['version'] == 1
    assert (root / 'jobs/001.json').read_bytes() == before
