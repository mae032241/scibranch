"""Current catalog admission keeps identity and scope without trial quotas.

Accepted tables are explicit mechanical originals; no process or model runs.
"""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, accepted_search, breadth, contracts as c, search
from execution_fixtures import execution_registry


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / 'instance'
    summary = accepted_search.empty()
    for number, role in enumerate(('W', 'H'), 1):
        ref = f'{role.lower()}001.m{number}'
        summary['table']['mechanisms'][ref] = dict(
            mechanism_ref=ref, label='Same label', minimal_test='A discriminating observation',
            status='pending', source_step=1, source_role=role,
            source_ref=dict(path='/fixture/native-evidence/001/result-identity.json'),
            source_origin_identity=dict(goal_id='fixture'), ordinal=number, accepted_calls=number,
            origin_ref=dict(goal_id='fixture', source_step=1, source_role=role, mechanism_ref=ref))
        summary['table']['ordinals'].append(ref)
    summary.update(mechanism_count=2, owed_count=1, current_h=1,
        last_science=dict(step=1, role='W'), focus=dict(mechanism_ref='w001.m1', action='screen'))
    head = dict(prefix=dict(version=1, cutoff=1), search=summary)
    state = accepted_search.state(root, 2, head=head)
    search._current_combination(root, state)
    value = search._breadth(root, state, search.DEFAULT_POLICY, state['focus'])
    job = dict(step=2, search=dict(breadth=value, focus=state['focus']), history=dict(prefix=head['prefix']))
    c.write(root / 'jobs/002.json', job)
    scope = dict(step=2, job_ref=c.record_ref(root / 'jobs/002.json'))
    monkeypatch.setattr(accepted_budget, 'read', lambda selected: head)
    return root, head, state, value, scope


def test_repeated_calls_keep_exact_binding_without_initial_or_a_grant_quota(catalog):
    _, head, _, value, scope = catalog
    ref = head['search']['table']['ordinals'][0]
    value['grant'] = dict(mechanism_ref=ref, source_a=1, source_a_ref=dict(path='/fixture/a.json'),
        extra_calls=1, used_calls=1)
    # The frozen task retains historical advice; it cannot grant execution.
    root = catalog[0]
    job = c.read(root / 'jobs/002.json'); job['search']['breadth'] = value
    c.write(root / 'jobs/002.json', job)
    accepted = []
    for _ in range(3):
        binding = breadth.admit(value, '1', scope, accepted)
        assert binding['mechanism_ref'] == ref and binding['source_role'] == 'W'
        assert binding['grant'] is None
        assert breadth.check_binding(value, binding, scope) == binding
        accepted.append(dict(mechanism_binding=binding))
    assert head['search']['table']['mechanisms'][ref]['status'] == 'pending'


@pytest.mark.parametrize('choice', [None, 'Same label', '01', '3'])
def test_invalid_choice_reports_every_available_exact_number(catalog, choice):
    _, _, _, value, scope = catalog
    with pytest.raises(breadth.OpportunityRequired) as caught:
        breadth.admit(value, choice, scope, [])
    assert caught.value.reason == 'mechanism_choice_required'
    assert caught.value.available_mechanisms == [dict(number='1', mechanism_ref='w001.m1'),
        dict(number='2', mechanism_ref='h001.m2')]


def test_unknown_original_blocks_every_new_mechanism_without_mutating_accepted_state(catalog):
    _, head, _, value, scope = catalog
    head['search']['opportunity_unknown_count'] = 1
    before = copy.deepcopy(head)
    for choice in ('1', '2'):
        with pytest.raises(breadth.OpportunityRequired) as caught:
            breadth.admit(value, choice, scope, [])
        assert caught.value.reason == 'original_execution_unknown'
        assert caught.value.available_mechanisms == []
    assert head == before


@pytest.mark.parametrize('changed', ['source_ref', 'source_role', 'job_ref', 'catalog_binding'])
def test_accepted_binding_cannot_change_its_original_identity(catalog, changed):
    _, _, _, value, scope = catalog
    binding = breadth.admit(value, '1', scope, [])
    forged = copy.deepcopy(binding)
    forged[changed] = (dict(path='/another/original.json') if changed in {'source_ref', 'job_ref'}
        else 'H' if changed == 'source_role' else dict(binding['catalog_binding'], ordinal=2))
    with pytest.raises(ValueError):
        breadth.admit(value, '1', scope, [dict(mechanism_binding=forged)])


def test_task_advice_and_admission_share_available_numbers_without_reading_originals(catalog, monkeypatch):
    root, head, state, value, scope = catalog
    head['search']['table']['mechanisms']['h001.m2']['status'] = 'waived'
    state['mechanisms']['h001.m2']['status'] = 'waived'
    monkeypatch.setattr(search.configuration_updates, 'for_new_job', lambda *args: dict(search_policy=search.DEFAULT_POLICY))
    def forbidden(*args, **kwargs):
        pytest.fail('Produced task advice must not reload originals')
    with monkeypatch.context() as no_io:
        no_io.setattr(c, 'read', forbidden)
        no_io.setattr(accepted_budget, 'read', forbidden)
        bound = search.bind(root, 2, 'W', state=state)
    assert bound['admission_advice'] == dict(available_mechanisms=[dict(number='1', mechanism_ref='w001.m1')],
        omitted_count=0, focus_reason=None)
    with pytest.raises(breadth.OpportunityRequired) as caught:
        breadth.admit(value, '2', scope, [])
    assert caught.value.reason == 'mechanism_waived'
    assert caught.value.available_mechanisms == bound['admission_advice']['available_mechanisms']


@pytest.fixture
def recorded_call(catalog, execution_registry):
    import sys
    from scidiscovery.research_loop import experiments, protocol

    root, _, _, value, scope = catalog
    scope = dict(scope, handoff_protocol=protocol.CURRENT, role='W',
        execution_key=scope['job_ref'], mechanism_ref=None)
    config = experiments.freeze(dict(admission=dict(version=1, mode='hard'),
        record_dir=str(root / 'records'), scope=scope, breadth=value,
        run_argv=[sys.executable], max_seconds=10), 'local_execution:command', path=root / 'config.json')
    records = experiments.Records(config)
    accepted = dict(call_id='exact-original', scope=config['scope'], parent=dict(candidate_id=None),
        mechanism_binding=breadth.admit(value, '1', config['scope'], []))
    path = records.root / 'calls/exact-original/accepted.json'
    c.write(path, accepted)
    assert records.accepted(accepted['call_id']) == accepted
    return records, accepted, path


def test_records_reject_changed_accepted_catalog_original(recorded_call):
    from scidiscovery.research_loop import experiments

    records, accepted, path = recorded_call
    changed = copy.deepcopy(accepted)
    changed['mechanism_binding']['source_ref'] = dict(path='/different/producer/result-identity.json')
    c.write(path, changed)
    original = path.read_bytes()
    with pytest.raises(experiments.IntegrityError, match='scientific mechanism binding changed'):
        records.accepted(accepted['call_id'])
    assert path.read_bytes() == original


def test_completed_publication_cannot_replace_accepted_mechanism(recorded_call):
    from scidiscovery.research_loop import experiments

    records, accepted, path = recorded_call
    original = path.read_bytes()
    changed = copy.deepcopy(accepted['mechanism_binding'])
    changed['catalog_binding']['ordinal'] = 2
    with pytest.raises(experiments.IntegrityError, match='receipt changed scientific mechanism'):
        records.publish(accepted['call_id'], 'completed.json', dict(status='known_success', mechanism_binding=changed))
    assert not (path.parent / 'completed.json').exists()
    assert path.read_bytes() == original
    records.publish(accepted['call_id'], 'completed.json', dict(status='known_success'))
    completed_path = path.parent / 'completed.json'
    completed = c.read(completed_path)
    assert completed['mechanism_binding'] == accepted['mechanism_binding']
    assert records.state(accepted['call_id']) == 'succeeded'
    completed['scope'] = dict(completed['scope'], job_ref=dict(path='/different/jobs/002.json'))
    c.write(completed_path, completed)
    corrupted = completed_path.read_bytes()
    with pytest.raises(experiments.IntegrityError, match='Completed execution binding changed'):
        records.state(accepted['call_id'])
    assert completed_path.read_bytes() == corrupted and path.read_bytes() == original


def test_forged_native_attempt_cannot_acquire_new_execution_permission(tmp_path, execution_registry):
    import sys
    from scidiscovery.research_loop import controller, execution, experiments

    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Preserve the original W execution owner')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], model='fixture',
        reasoning_effort='high', max_tokens=1000, max_seconds=60, max_steps=3, search_policy=None)
    c.build_job(root, 1, 'W', [])
    record = execution.record(root, 1)
    intent_path = record / 'native-intent.json'
    c.write(intent_path, dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    accepted_budget.accept_native(root, 1, record, c.read(intent_path))
    config = experiments.configure(root, 1, 'local_execution:command',
        dict(run_argv=[sys.executable], max_seconds=10), c.workspace(root, 1) / 'work/experiment-config.json')
    experiments._require_new_execution(config)  # The active original passes this attempt-identity gate.
    originals = [intent_path, root / 'jobs/001.json', root / accepted_budget.HEAD]
    before = {path: path.read_bytes() for path in originals}
    forged = copy.deepcopy(config)
    forged['native_attempt'] = dict(number=1, execution_key=dict(path=str(record / 'continuations/001/native-intent.json')))
    with pytest.raises(experiments.IntegrityError, match='exact accepted native attempt'):
        experiments._require_new_execution(forged)
    assert {path: path.read_bytes() for path in originals} == before
    assert experiments.Records(config).calls() == []
