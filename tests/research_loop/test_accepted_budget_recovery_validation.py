"""One durable pending event is validated before any redo is installed."""
import copy

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller


@pytest.fixture
def instance(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact originals and consumed allowances during recovery.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=1000,
        max_seconds=200, max_steps=5, model='fixture', reasoning_effort='high',
        node_budget_policy={role: dict(max_observed_tokens=100, max_seconds=20) for role in 'HWAE'})
    return root


def pending_amendment(root, monkeypatch, *, operation='configuration-1', after=None, steps=2):
    """Stop the existing publisher after its event, before indexes or head.

    This is a control-arithmetic fixture, not a configuration/execution grant.
    """
    state = accepted_budget.read(root)
    fields = dict(kind='budget_amend',
        before={key: state['ceilings'][key] for key in ('max_tokens', 'max_seconds')},
        after=dict(max_tokens=1300, max_seconds=180) if after is None else after,
        authorization_note='Explicit bounded recovery adjustment',
        authorization_statement='Keep all original work and consumption.')
    if steps is not None:
        fields.update(before_max_steps=state['ceilings']['max_steps'], after_max_steps=steps,
            max_steps_delta=steps-state['ceilings']['max_steps'])
    if operation is not None:
        fields['operation_id'] = operation

    def interrupted(*args, **kwargs):
        raise InterruptedError('Event durable, before redo installation')

    with budget._ledger_writer(root), monkeypatch.context() as patch:
        patch.setattr(accepted_budget, '_install_indexes', interrupted)
        with pytest.raises(InterruptedError, match='Event durable'):
            accepted_budget.append_budget_event(root, accepted_budget.AcceptedEvents(state), **fields)
    path = root / 'control-ledger' / f'{state["seq"]+1:06d}.json'
    return state, c.read(path), path


def retained_paths(root, event, path):
    paths = [root / name for name in ('settings.json', 'settings-identity.json', accepted_budget.HEAD)]
    paths.append(path)
    paths.extend(accepted_budget._row_path(root, key) for key in event['index_updates'])
    paths.extend(root / relative for relative in event.get('control_files', {}))
    return {item: item.read_bytes() if item.exists() else None for item in paths}


def assert_retained(values):
    for path, raw in values.items():
        assert (path.read_bytes() if path.exists() else None) == raw, path


@pytest.mark.parametrize('operation', [None, 'operator-adjustment', 'configuration-1'])
@pytest.mark.parametrize('after,steps', [
    (dict(max_tokens=1300, max_seconds=180), 2),
    (dict(max_tokens=None, max_seconds=None), None),
    (dict(max_tokens=1000, max_seconds=150.5), None),
    (dict(max_tokens=1000, max_seconds=200), 24),
])
def test_valid_pending_amendment_is_readonly_then_installed_once(instance, monkeypatch, operation, after, steps):
    root = instance
    state, event, path = pending_amendment(root, monkeypatch, operation=operation, after=after, steps=steps)
    retained = retained_paths(root, event, path)
    cold = accepted_budget.load(root, base=state.base)
    expected = dict(after, max_steps=5 if steps is None else steps)
    assert cold['ceilings'] == expected
    assert cold['historical_max_steps'] == max(5, expected['max_steps'])
    assert cold['explicit_amendment'] == (state['explicit_amendment']
        if operation and operation.startswith('configuration-') else cold.parent)
    assert budget.ceilings(root) == expected
    assert_retained(retained)
    installed = accepted_budget.recover(root, cold)
    assert installed.pending is None and installed['seq'] == state['seq']+1
    if operation is not None:
        assert installed.row('operation/'+operation) == dict(ref=installed.parent)
    installed_bytes = (root / accepted_budget.HEAD).read_bytes()
    again = accepted_budget.recover(root, accepted_budget.load(root, base=state.base))
    assert again['seq'] == installed['seq'] and again.pending is None
    assert (root / accepted_budget.HEAD).read_bytes() == installed_bytes
    assert path.read_bytes() == retained[path]
    assert not (root / 'control-ledger' / f'{again["seq"]+1:06d}.json').exists()


@pytest.mark.parametrize('changed', [
    'token_predecessor', 'step_predecessor', 'delta', 'bool_delta', 'missing_step',
    'orphan_delta', 'bool_token', 'zero_token', 'bool_seconds', 'negative_seconds',
    'bad_step', 'empty_note', 'empty_statement', 'head_ceiling', 'head_peak',
    'head_bool_peak', 'head_usage', 'head_resource', 'head_explicit', 'wrong_kind', 'index_ref',
    'index_predecessor', 'index_generation', 'control_original',
])
def test_invalid_pending_amendment_publishes_nothing(instance, monkeypatch, changed):
    root = instance
    state, event, path = pending_amendment(root, monkeypatch)
    if changed == 'token_predecessor':
        event['before']['max_tokens'] += 1
    elif changed == 'step_predecessor':
        event['before_max_steps'] = 4
        event['max_steps_delta'] = event['after_max_steps']-4
    elif changed == 'delta':
        event['max_steps_delta'] += 1
    elif changed == 'bool_delta':
        event.update(after_max_steps=6, max_steps_delta=True)
        event['head_updates']['ceilings']['max_steps'] = 6
        event['head_updates']['historical_max_steps'] = 6
    elif changed == 'missing_step':
        event.pop('after_max_steps')
    elif changed == 'orphan_delta':
        event.pop('before_max_steps')
        event.pop('after_max_steps')
    elif changed in ('bool_token', 'zero_token'):
        event['after']['max_tokens'] = True if changed == 'bool_token' else 0
        event['head_updates']['ceilings']['max_tokens'] = event['after']['max_tokens']
    elif changed in ('bool_seconds', 'negative_seconds'):
        event['after']['max_seconds'] = True if changed == 'bool_seconds' else -1
        event['head_updates']['ceilings']['max_seconds'] = event['after']['max_seconds']
    elif changed == 'bad_step':
        event['after_max_steps'] = True
    elif changed == 'empty_note':
        event['authorization_note'] = ' '
    elif changed == 'empty_statement':
        event['authorization_statement'] = ''
    elif changed == 'head_ceiling':
        event['head_updates']['ceilings']['max_tokens'] += 1
    elif changed == 'head_peak':
        event['head_updates']['historical_max_steps'] = 2
    elif changed == 'head_bool_peak':
        event['head_updates']['historical_max_steps'] = True
    elif changed == 'head_usage':
        event['head_updates']['usage'] = dict(state['usage'], input_tokens=0)
    elif changed == 'head_resource':
        event['head_updates']['resource'] = None
    elif changed == 'head_explicit':
        event['head_updates']['explicit_amendment'] = dict(path='other.json')
    elif changed == 'wrong_kind':
        event.update(kind='clock', phase='paused')
    elif changed == 'index_ref':
        event['index_updates']['operation/configuration-1']['current']['ref'] = dict(path='other.json')
    elif changed == 'index_predecessor':
        event['index_updates']['operation/configuration-1']['previous'] = dict(ref=state.parent)
    elif changed == 'index_generation':
        event['index_updates']['operation/configuration-1']['previous_seq'] += 1
    else:
        event['control_files']['host-control/forged-original.json'] = dict(forged=True)
    c.write(path, event)
    retained = retained_paths(root, event, path)
    for acquire in (lambda: accepted_budget.load(root, base=state.base), lambda: accepted_budget.read(root)):
        with pytest.raises(ValueError):
            accepted_budget.recover(root, acquire())
        assert_retained(retained)


@pytest.mark.parametrize('control_files', [[], None, '', 0, False, [{}], 'invalid', 1, True])
def test_malformed_control_files_tail_is_rejected_before_any_redo(instance, monkeypatch, control_files):
    root = instance
    state, event, path = pending_amendment(root, monkeypatch)
    # Keep the valid operation redo: recovery must not install even its first row.
    original = copy.deepcopy(event)
    event['control_files'] = control_files
    c.write(path, event)
    retained = retained_paths(root, original, path)

    def forbidden(*args, **kwargs):
        pytest.fail('Malformed control_files must be rejected before atomic publication')

    monkeypatch.setattr(accepted_budget.atomic_files, 'replace_bytes', forbidden)
    monkeypatch.setattr(accepted_budget.atomic_files, 'publish_bytes', forbidden)
    for acquire in (lambda: accepted_budget.load(root, base=state.base),
            lambda: accepted_budget.recover(root, accepted_budget.load(root, base=state.base))):
        with pytest.raises(ValueError, match='accepted predecessor'):
            acquire()
        assert_retained(retained)


@pytest.mark.parametrize('kind', ['budget_amend', 'clock'])
@pytest.mark.parametrize('present', [True, False])
def test_empty_or_legacy_absent_control_files_recover(instance, monkeypatch, kind, present):
    root = instance
    state, event, path = pending_amendment(root, monkeypatch)
    if kind == 'clock':
        event.update(kind='clock', phase='paused', head_updates={})
    assert event['control_files'] == {}
    if not present:
        event.pop('control_files')
    c.write(path, event)
    retained = retained_paths(root, event, path)
    cold = accepted_budget.load(root, base=state.base)
    assert cold['ceilings'] == (event['head_updates']['ceilings']
        if kind == 'budget_amend' else state['ceilings'])
    assert_retained(retained)
    installed = accepted_budget.recover(root, cold)
    assert installed.pending is None and installed['seq'] == state['seq']+1
    assert installed.row('operation/configuration-1') == dict(ref=installed.parent)
    assert path.read_bytes() == retained[path]


def test_configuration_binding_dictionary_redo_remains_supported(instance, monkeypatch):
    root = instance
    state = accepted_budget.read(root)
    binding = dict(step=1, configuration_revision=None)

    def interrupted(*args, **kwargs):
        raise InterruptedError('Event durable, before redo installation')

    with monkeypatch.context() as patch:
        patch.setattr(accepted_budget, '_install_indexes', interrupted)
        with pytest.raises(InterruptedError, match='Event durable'):
            accepted_budget.accept_configuration_binding_from_base(root, 1, binding, base=state.base)
    path = root / 'control-ledger' / f'{state["seq"]+1:06d}.json'
    event = c.read(path)
    relative = 'host-control/configuration/bindings/000001.json'
    assert event['control_files'] == {relative: binding}
    retained = retained_paths(root, event, path)
    cold = accepted_budget.load(root, base=state.base)
    assert cold.control_file(relative) == binding
    assert_retained(retained)
    installed = accepted_budget.recover(root, cold)
    assert installed.row('configuration-binding/000001') == binding
    assert c.read(root / relative) == binding
    assert path.read_bytes() == retained[path]


def test_amendment_transition_is_pure_and_keeps_consumed_fork_facts(instance, monkeypatch):
    state, event, _ = pending_amendment(instance, monkeypatch)
    # Supplied retained accounting facts, not resource admission/qualification:
    # high logical labels are not a child's action allowance, and unknown usage
    # remains a reported lower bound, not a refund.
    state.update(is_fork=True, max_local_step=900, prepared_count=2, continuation_count=1,
        usage=dict(input_tokens=37, cached_input_tokens=5, output_tokens=11,
            unknown_count=1, first_unknown_step=900),
        resource=dict(settings_ref=dict(path='settings.json'), held=dict(wall_time_seconds=6)))
    original = copy.deepcopy(dict(state))

    def forbidden(*args, **kwargs):
        pytest.fail('Pending budget validation must consume supplied state/event only')

    monkeypatch.setattr(c, 'read', forbidden)
    for method in ('row', 'event', 'control_file'):
        monkeypatch.setattr(accepted_budget.AcceptedState, method, forbidden)
    result = accepted_budget._apply(state, event)
    assert dict(state) == original
    for key in ('usage', 'role_usage', 'resource', 'prefix', 'is_fork', 'max_local_step',
            'prepared_count', 'continuation_count', 'applied_configuration', 'configuration'):
        assert result[key] == original[key]
    bad = copy.deepcopy(event)
    bad['before']['max_tokens'] += 1
    with pytest.raises(ValueError, match='Budget amendment predecessor changed'):
        accepted_budget._apply(state, bad)


@pytest.mark.parametrize('before,after', [
    (dict(max_tokens=None, max_seconds=None), dict(max_tokens=500, max_seconds=50)),
    (dict(max_tokens=None, max_seconds=200), dict(max_tokens=None, max_seconds=150.5)),
])
def test_initial_unbounded_predecessor_is_not_rebased(instance, monkeypatch, before, after):
    state, event, _ = pending_amendment(instance, monkeypatch)
    state['ceilings'].update(before)
    event.update(before=before, after=after)
    event['head_updates']['ceilings'].update(after)
    result = accepted_budget._apply(state, event)
    assert result['ceilings'] == dict(after, max_steps=2)
    assert result['historical_max_steps'] == 5
    assert state.base['max_tokens'] == 1000


@pytest.mark.parametrize('counter', [False, True])
def test_invalid_marker_tail_does_not_publish_redo(instance, monkeypatch, counter):
    root = instance
    state, event, path = pending_amendment(root, monkeypatch)
    event['uptime_accounting'] = 1
    if not counter:
        event.pop('observed_advance_seconds', None)
    c.write(path, event)
    retained = retained_paths(root, event, path)
    reason = 'advancement observation' if counter else 'uptime accounting marker'
    with pytest.raises(ValueError, match=reason):
        accepted_budget.recover(root, accepted_budget.load(root, base=state.base))
    assert_retained(retained)


@pytest.mark.parametrize('seconds', [float('inf'), float('-inf'), float('nan')])
def test_nonfinite_amendment_seconds_are_rejected_from_supplied_facts(instance, monkeypatch, seconds):
    state, event, _ = pending_amendment(instance, monkeypatch)
    event['after']['max_seconds'] = seconds
    event['head_updates']['ceilings']['max_seconds'] = seconds
    with pytest.raises(ValueError, match='Invalid audited budget ceilings'):
        accepted_budget._apply(state, event)


@pytest.mark.parametrize('marker', ['absent', True, 1, False, None])
@pytest.mark.parametrize('basis', ['active', 'elapsed'])
def test_optional_legacy_uptime_marker_has_exact_type_and_basis(instance, monkeypatch, marker, basis):
    state, event, _ = pending_amendment(instance, monkeypatch)
    event.update(kind='clock', phase='paused', head_updates={}, index_updates={})
    event.pop('observed_advance_seconds', None)
    event.pop('uptime_accounting', None)
    state['clock']['basis'] = basis
    if marker != 'absent':
        event['uptime_accounting'] = marker
    if marker == 'absent' or marker is True and basis == 'active':
        assert accepted_budget._apply(state, event)['seq'] == state['seq']+1
    else:
        with pytest.raises(ValueError, match='uptime accounting marker'):
            accepted_budget._apply(state, event)
