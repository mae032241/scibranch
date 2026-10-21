"""Fork accounting and frozen recovery validation; no model or solver runs."""
import copy
import shutil
from pathlib import Path

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, fork_runtime, native, role_share
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


def test_high_logical_labels_do_not_consume_child_action_allowance(tmp_path, monkeypatch):
    root = prepared(tmp_path)
    c.write(root / 'host-runtime/fork/manifest.json', {'version': 1})
    # Source validation belongs to test_fork_source. This unit receives that
    # admitted bootstrap and checks the independent resource boundary.
    monkeypatch.setattr(fork_runtime, 'bootstrap', lambda _: {'next_step': 200})
    assert budget.used_steps(root) == 0
    assert budget.task_step_available(root, 200)
    c.write(root / 'jobs/200.json', {'step': 200, 'role': 'H'})
    record = root / 'native-evidence/200'
    c.write(record / 'native-intent.json', {'step': 200,
        'task_sha256': c.digest(root / 'jobs/200.json')})
    c.write(record / 'usage.json', {'input_tokens': 7, 'cached_input_tokens': 2, 'output_tokens': 3})
    assert budget.used_steps(root) == 1
    assert budget.task_step_available(root, 201)
    assert native.usage(root)['observed_tokens'] == 10
    assert role_share.usage(root, 200)['H'] == 10
    c.write(root / 'jobs/201.json', {'step': 201, 'role': 'A'})
    assert budget.used_steps(root) == 2
    assert not budget.task_step_available(root, 202)
    assert native.usage(root)['observed_tokens'] == 10
    assert budget._selection_accounts_job(root, {'step': 201}, 2)
    assert not budget._selection_accounts_job(root, {'step': 201}, 1)


def test_usage_keeps_high_original_after_ceiling_reduction_and_unknown_meter(tmp_path):
    root = prepared(tmp_path)
    c.write(root / 'jobs/200.json', {'step': 200, 'role': 'H'})
    c.write(root / 'native-evidence/200/native-intent.json', {'step': 200,
        'task_sha256': c.digest(root / 'jobs/200.json')})
    meter = native.usage(root)
    assert meter['unknown_steps'] == [200]
    assert meter['observed_tokens'] == 0
    assert meter['accounting_basis'] == 'observed_lower_bound'
    assert meter['blocking_unknown_steps'] == []
    # Billing uncertainty and unresolved execution are independent facts.
    assert budget.execution_blocked(root)


def test_bootstrap_return_binds_plain_proofs_and_retains_nested_origin():
    origin = dict(goal_id='parent', settings_sha256='a' * 64)
    earlier = dict(goal_id='ancestor', settings_sha256='b' * 64)
    original = dict(kind='formal_review', request=dict(path='request.json', sha256='c' * 64),
        review=dict(path='review.json', sha256='d' * 64, origin_identity=earlier))
    bound = fork_runtime._bound_return(original, origin)
    assert bound['request']['origin_identity'] == origin
    assert bound['review']['origin_identity'] == earlier
    assert 'origin_identity' not in original['request']
    assert fork_runtime._bound_return(bound, origin) == bound


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
    # Exercise the exact typed closure and immutable reader, without claiming
    # that a recovery snapshot itself admits a new research instance.
    closure = fork_sources._Closure()
    closure.sealed(source, values[-1]['seal_sha256'])
    for ref in refs:
        closure.add(source, ref['path'], proof=ref, parse=True)
    store = tmp_path / 'frozen-recovery'
    for key, entry in closure.entries.items():
        archive_io.copy_verified(Path(closure.paths[key]), store / entry['storage_path'], dict(entry, kind='file'))
    snapshot = originals._snapshot(dict(entries=list(closure.entries.values()),
        origins=closure.origins, native_records=closure.records))
    frozen = originals._snapshot_source(store, snapshot, source.origin_identity, 6)
    shutil.rmtree(root)
    def validate(sequence):
        chains = {}
        for ref, value in zip(refs, sequence):
            recovery._validate_inherited(store, frozen, dict(ref, origin_identity=source.origin_identity), value, chains)
    validate(values)
    seal = c.read(originals.source_record(frozen, 'result-identity.json'))
    with pytest.raises(ValueError, match='never authorizes dispatch'):
        verify_pending_set(store, seal, values[-1]['allocation'], source=frozen, unprepared=True)
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
