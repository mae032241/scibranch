"""A advice leaves search unchanged; exact scientific originals stay validated."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, originals, search
from scidiscovery.research_loop.protocol import CURRENT
from test_portfolio_contract import prepared, raw_author


@pytest.mark.parametrize('purpose', [None, 'closeout'], ids=['ordinary', 'closeout'])
def test_a_interpretation_and_reducer_preserve_prior_search_without_filesystem(purpose):
    baseline = dict(focus=dict(mechanism_ref='h001.m1', action='screen'),
        mechanisms={'h001.m1': dict(status='pending')},
        order=dict(allocation=dict(mechanism_ref='h001.m1', planned_step=1),
            remaining=[dict(mechanism_ref='h001.m1', planned_step=1)], diagnostics=[]))
    job = dict(role='A', step=3, purpose=purpose)
    payload = dict(next_action='finish' if purpose else 'continue_w', search_review=dict(
        dispositions=[dict(mechanism_ref='h001.m1', status='waived')],
        next_focus=dict(mechanism_ref='h001.m2', action='improve')))
    seal = dict(control_outcome=dict(binding_protocol=CURRENT-1,
        search_status='unresolved', selection_status='unresolved', decision_status='unresolved',
        resolved_focus=dict(mechanism_ref='h001.m2', action='repair'),
        resolved_allocation=dict(mechanism_ref='h001.m2', planned_step=3),
        diagnostics=[dict(code='science_task_action_required', subject='focus')]))
    frozen = copy.deepcopy((baseline, job, payload, seal))
    # Both consumers receive facts directly; no instance or filesystem exists.
    interpreted = search._interpret_from_facts(None, job, payload,
        policy=search.DEFAULT_POLICY, state=baseline, selected_inputs=[])
    assert interpreted == dict(state=baseline, focus=baseline['focus'],
        allocation=baseline['order']['allocation'], diagnostics=[])
    assert interpreted['state'] is not baseline
    state = copy.deepcopy(baseline)
    search.apply_source(None, job, payload, seal, state)
    assert state == baseline and (baseline, job, payload, seal) == frozen


@pytest.mark.parametrize('damage', ['raw', 'seal'])
def test_exact_a_original_tamper_rejects_at_validation_without_changing_originals(tmp_path, damage):
    root = prepared(tmp_path)
    _, _, record = raw_author(root)
    result = c.seal_result(root, 3)
    work = c.workspace(root, 3)
    source = originals.node(root, 3)
    paths = (root / 'jobs/003.json', work / 'science-task.json',
        record / 'native-intent.json', record / 'native-outcome.json',
        record / 'usage.json', record / 'science-output.json',
        record / 'result.json', record / 'result-identity.json')
    frozen = {path: path.read_bytes() for path in paths}
    assert c.validate(work) == result
    assert c.validate_original(source) == result
    assert {path: path.read_bytes() for path in paths} == frozen
    if damage == 'raw':
        raw = record / 'science-output.json'
        raw.chmod(0o600)
        raw.write_bytes(frozen[raw] + b' ')
    else:
        seal = c.read(record / 'result-identity.json')
        seal['job_ref'] = dict(path=str(root / 'jobs/999.json'))
        c.write(record / 'result-identity.json', seal)
    changed = {path: path.read_bytes() for path in paths}
    with pytest.raises(ValueError, match='identity changed'):
        c.validate(work)
    with pytest.raises(ValueError, match='changed|exact original'):
        c.validate_original(source)
    assert {path: path.read_bytes() for path in paths} == changed
