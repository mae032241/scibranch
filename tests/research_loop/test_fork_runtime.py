"""Fork accounting and frozen recovery validation; no model or solver runs."""
import copy
import shutil
from pathlib import Path

import pytest

from execution_fixtures import execution_registry

from scidiscovery.research_loop import (accepted_budget, budget, contracts as c, controller,
    fork_runtime, fork_service, native, role_share)
from test_history_handoff import finish
from test_native_execution_inputs import chain, late_call


def prepared(tmp_path, *, storage=None, final=None, public_identity=None):
    public = tmp_path / 'public'
    public.mkdir(exist_ok=True)
    root = storage or tmp_path / 'child'
    controller.prepare(root, public / 'unused-objective', public, ['fixture-never-launch'],
        max_tokens=100, max_seconds=20, max_steps=2, model='fixture', reasoning_effort='high',
        objective_text='Keep the exact original research objective.\n', _identity_root=final,
        _public_identity_root=public_identity)
    return root


def test_staged_prepare_freezes_final_generated_objective_identity(tmp_path):
    final = tmp_path / 'published'
    root = prepared(tmp_path, storage=tmp_path / 'staged', final=final,
        public_identity=final / 'host-runtime/fork/public')
    settings = c.settings(root)
    source = c.read(root / settings['source_provenance']['path'])
    assert source['globals'][0]['origin']['path'] == str(final / 'project/global/objective.md')
    assert settings['public_root'] == str(final / 'host-runtime/fork/public')
    assert not final.exists()
    assert not (root / 'host-started.json').exists()
    assert not (root / 'native-evidence').exists()
    root.rename(final)
    from scidiscovery.research_loop import qualification
    assert qualification._sources(final) == source


def test_staged_prepare_preserves_supplied_external_original_identity(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    original = public / 'objective.md'
    original.write_text('An exact externally supplied original.')
    root, final = tmp_path / 'stage', tmp_path / 'final'
    controller.prepare(root, original, public, ['fixture-never-launch'], max_tokens=100,
        max_seconds=20, max_steps=2, model='fixture', reasoning_effort='high', _identity_root=final)
    source = c.read(root / c.settings(root)['source_provenance']['path'])
    assert source['globals'][0]['origin']['path'] == str(original)


def high_source(tmp_path):
    """A supported, accepted sparse task; no native process or Git fixture."""
    public = tmp_path / 'public'
    public.mkdir()
    root = tmp_path / 'parent'
    controller.prepare(root, public / 'unused-objective', public, ['fixture-never-launch'],
        max_tokens=1000, max_seconds=60, max_steps=202, model='fixture', reasoning_effort='high',
        objective_text='Keep the exact original research objective.\n',
        search_policy=None, experiment_mode='external')
    c.build_job(root, 200, 'H', [])
    # The real fork source guard requires the retained controller lock, even
    # when the selected original was prepared but never submitted.
    with controller._writer(root):
        pass
    return root


def test_high_logical_labels_do_not_consume_child_action_allowance(tmp_path, monkeypatch):
    parent = high_source(tmp_path)
    root = tmp_path / 'child'
    allowance = dict(max_tokens=100, max_seconds=20, max_steps=2)
    preview = fork_service.preview(parent, 200, root, budget=allowance)
    assert preview['status'] == 'fork_preview_ready'
    fork_service.create(parent, 200, root, budget=allowance,
        expected_preview_id=preview['preview_id'], operation_id='first-high-label')
    assert fork_runtime.bootstrap(root)['next_step'] == 200
    assert budget.used_steps(root) == 0
    assert budget.task_step_available(root, 200)
    assert budget.ceilings(root) == allowance
    assert native.usage(root)['observed_tokens'] == 0
    assert not (root / 'host-started.json').exists()
    assert not (root / 'jobs').exists()

    class FirstLaunchReached(BaseException):
        pass

    reached = []
    def before_native(actual_root, step, *, launch=False):
        assert actual_root == root and step == 200 and launch is True
        job = c.read_job(root / 'jobs/200.json')
        head = accepted_budget.read(root)
        assert job['role'] == 'H' and job['fork_source']['job']['path'] == 'jobs/200.json'
        assert head.current_job['job_ref'] == c.record_ref(root / 'jobs/200.json')
        assert head.current_job['status'] == 'prepared' and head['prepared_count'] == 1
        assert budget.used_steps(root) == 1
        assert budget.ceilings(root) == allowance
        assert not (root / 'native-evidence/200/native-intent.json').exists()
        reached.append(step)
        raise FirstLaunchReached()

    root.joinpath('pause.request').unlink()
    with monkeypatch.context() as patch:
        patch.setattr(controller, '_native_call', before_native)
        with pytest.raises(FirstLaunchReached):
            controller.run(root)
    assert reached == [200]
    assert budget.closeout_state(root)['begin'] is None
    consumed = budget.status_snapshot(root)['consumed_seconds']
    assert 0 <= consumed <= allowance['max_seconds']

    # Finish the accepted original with the ordinary stopped, filesystem-only
    # fixture. Re-preparing that selected job cannot reset any child spending.
    finish(root, 200, 'H', [], action='analyze')
    assert native.usage(root)['observed_tokens'] == 10
    assert role_share.usage(root, 200)['H'] == 10
    fork_runtime.build_selected_job(root, 200)
    assert budget.used_steps(root) == 1
    assert budget.task_step_available(root, 201)
    assert budget.status_snapshot(root)['consumed_seconds'] >= consumed
    c.build_job(root, 201, 'A', [200])
    assert budget.used_steps(root) == 2
    assert not budget.task_step_available(root, 202)
    assert native.usage(root)['observed_tokens'] == 10
    assert budget.ceilings(root) == allowance
    assert budget._selection_accounts_job(root, {'step': 201}, 2)
    assert not budget._selection_accounts_job(root, {'step': 201}, 1)


def test_nonfork_high_logical_label_still_uses_ordinary_step_ceiling(tmp_path):
    root = high_source(tmp_path)
    budget.amend(root, max_steps=2, authorization_note='Reduce future ordinary work.',
        authorization_statement='Keep prepared originals; authorize at most two logical steps.')
    assert not budget.task_step_available(root, 200)
    assert budget.used_steps(root) == 200
    # Idempotent job acceptance retains the original nonfork accounting fact.
    c.build_job(root, 200, 'H', [])
    assert not budget.task_step_available(root, 200)
    assert not budget.task_step_available(root, 201)
    assert budget.historical_step_limit(root) >= 200
    assert not (root / 'native-evidence/200/native-intent.json').exists()


@pytest.mark.parametrize('reported', [False, True], ids=['unknown-meter', 'known-lower-bound'])
def test_usage_keeps_high_original_after_ceiling_reduction_and_unknown_meter(tmp_path, monkeypatch, reported):
    root = high_source(tmp_path)
    budget.amend(root, max_steps=2, max_tokens=8, max_seconds=10,
        authorization_note='Reduce future ceilings without refunding originals.',
        authorization_statement='Retain all original work and lower bounds.')
    record = root / 'native-evidence/200'
    intent = dict(step=200, task_ref=c.record_ref(root / 'jobs/200.json'))
    accepted_budget.accept_native(root, 200, record, intent)
    if reported:
        c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    # The exact accepted current attempt is the sole live observation. Neither
    # orphan discovery nor historical replay may manufacture its accounting.
    assert budget.execution_blocked(root)
    originals = [root / accepted_budget.HEAD, root / 'jobs/200.json', record / 'native-intent.json']
    if reported:
        originals.append(record / 'usage.json')
    retained = {path: path.read_bytes() for path in originals}
    def forbidden(*args, **kwargs):
        pytest.fail('Read-only accounting attempted a write, scan or ledger replay')
    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', forbidden)
        patch.setattr(budget, 'audit_events', forbidden)
        patch.setattr(Path, 'glob', forbidden)
        patch.setattr(Path, 'iterdir', forbidden)
        meter = native.usage(root)
        assert meter['unknown_steps'] == ([] if reported else [200])
        assert meter['unknown_step_count'] == (0 if reported else 1)
        assert meter['observed_tokens'] == (10 if reported else 0)
        assert meter['cached_input_tokens'] == (2 if reported else 0)
        assert meter['accounting_basis'] == 'observed_lower_bound'
        assert meter['blocking_unknown_steps'] == []
        assert budget.ceilings(root) == dict(max_steps=2, max_tokens=8, max_seconds=10)
        assert not budget.task_step_available(root, 201)
        assert budget.historical_step_limit(root) >= 200
        assert role_share.usage(root, 200)['H'] == (10 if reported else 0)
    assert {path: path.read_bytes() for path in originals} == retained
    # Billing uncertainty and unresolved execution are independent facts; even
    # a reported lower bound does not permit replay of this accepted attempt.
    assert budget.execution_blocked(root)


def test_bootstrap_return_binds_plain_proofs_and_retains_nested_origin():
    origin = dict(goal_id='parent', settings_ref=dict(path='/parent/settings.json'))
    earlier = dict(goal_id='ancestor', settings_ref=dict(path='/ancestor/settings.json'))
    original = dict(kind='formal_review', request=dict(path='request.json', size_bytes=0),
        review=dict(path='review.json', size_bytes=0, origin_identity=earlier))
    bound = fork_runtime._bound_return(original, origin)
    assert bound['request']['origin_identity'] == origin
    assert bound['review']['origin_identity'] == earlier
    assert 'origin_identity' not in original['request']
    assert fork_runtime._bound_return(bound, origin) == bound


@pytest.mark.process_e2e
def test_frozen_terminal_recovery_uses_original_pending_authority_and_query(late_call, tmp_path):
    from scidiscovery import archive_io
    from scidiscovery.research_loop import execution_recovery as recovery, fork_sources, originals
    from scidiscovery.research_loop.execution_resume import _source_path, verify_pending_set
    from test_native_execution_inputs import _approve, _resume
    root, backend, _, call, request, _ = late_call
    _approve(root, request)
    backend.state = 'succeeded'
    assert _resume(root, call)['status'] == 'execution_resume_recorded'
    refs = recovery.prefix(root, 9)
    values = recovery.validate(root, refs)
    assert values[-1]['phase'] == 'terminal' and values[-1]['query'] is not None
    source = originals.node(root, 6)
    # The canonical successor supplies the accepted parent-control closure.
    # Retain this separately validated recovery sequence for archival reading;
    # do not graft it onto an already frozen scientific input cut.
    c.build_job(root, 7, 'A', [6])
    plan = fork_sources.inspect_source(root, 7)
    assert [row['step'] for row in plan['prefix_entries']] == list(range(1, 7))
    # Controls-only validation needs every sealed result envelope, not its
    # scientific original. Promote only these declared opaque projections;
    # all other scientific entries remain uncopied direct-original locators.
    proofs = fork_sources._Closure()
    for ref in refs:
        proofs.control(source, ref)
    for binding in plan['prefix_entries']:
        owner = originals.for_origin(root, binding['origin_identity'], step=binding['step'])
        seal = c.read(originals.resolve_ref(owner, binding['seal_ref']))
        proofs.control_proof(owner, seal['result_ref'])
    entries = {fork_sources._key(row['origin_identity'], row['path'], row): row
        for row in plan['entries']}
    entries.update(proofs.entries)
    paths = dict(plan['source_paths'])
    paths.update(proofs.paths)
    frozen_root = tmp_path / 'frozen-recovery'
    store = frozen_root / originals.FORK_DIRECTORY
    # This is NONADMISSION archival proof, not child publication. Keep real
    # fork blockers intact and prove the admission copier still rejects them.
    if plan['blocked_reasons']:
        with pytest.raises(ValueError, match='unresolved original effects'):
            fork_sources.copy_source(plan, store)
    for key, path in paths.items():
        entry = entries[key]
        archive_io.copy_original(Path(path), store / entry['storage_path'], dict(entry, kind='file'))
    manifest = {key: value for key, value in plan.items() if key not in fork_sources.SOURCE_LOCATORS}
    manifest['entries'] = list(entries.values())
    snapshot = originals._snapshot(manifest)
    frozen = originals._snapshot_source(store, snapshot, source.origin_identity, 6)
    shutil.rmtree(root)
    def validate(sequence):
        chains = {}
        for ref, value in zip(refs, sequence):
            recovery._validate_inherited(frozen_root, frozen, dict(ref, origin_identity=source.origin_identity), value, chains)
    validate(values)
    seal = c.read(originals.source_record(frozen, 'result-identity.json'))
    with pytest.raises(ValueError, match='never authorizes dispatch'):
        verify_pending_set(frozen_root, seal, values[-1]['allocation'], source=frozen, unprepared=True)
    allocation = _source_path(frozen, values[-1]['allocation'])
    retained = allocation.read_bytes()
    allocation.unlink()
    with pytest.raises((OSError, ValueError)):
        validate(values)
    allocation.write_bytes(retained)
    altered = copy.deepcopy(values)
    altered[-1]['native_state'] = 'different-original-state'
    with pytest.raises(ValueError, match='query lost its exact'):
        validate(altered)


@pytest.mark.parametrize('name', ['0200.json', '0.json', 'name.json'])
def test_local_job_enumeration_refuses_ambiguous_namespace(tmp_path, name):
    root = prepared(tmp_path)
    c.write(root / 'jobs' / name, {'step': 200, 'role': 'H'})
    with pytest.raises(ValueError, match='namespace'):
        fork_runtime.local_steps(root)
