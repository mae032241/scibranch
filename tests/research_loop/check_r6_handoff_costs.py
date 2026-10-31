"""Explicit r6 handoff regression checks; intentional red baseline.

Run this file by its exact path with pytest. Its ``check_`` name keeps the
unfixed cost regressions out of default collection. No wall-time threshold,
xfail or explicit skip hides a known defect. Process checks require the existing
Local launch capability, native executable and bwrap; a dependency skip means
NOT RUN, never pass or reproduced RED. No model or live research instance runs.
"""
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy

import pytest

from scidiscovery.research_loop import history, lineage, search
from execution_fixtures import execution_registry
from r6_handoff_fixture import r6_cost_scene


def _candidates(row):
    return [row[key] for key in ('accepted', 'completed', 'terminal', 'observation', 'retirement')
        if row.get(key) is not None] + row.get('originals', []) + row.get('outputs', [])


def test_exact_retained_binding_controls(tmp_path):
    """An index optimization must preserve path, size, stat and ambiguity gates."""
    proof = dict(path='native-evidence/002/calls/call-1/accepted.json', size_bytes=11,
        file_stat=[1, 2, 11, 123456789])
    retained = dict(proof, snapshot='retained/accepted.json', retention='archived')
    row = dict(accepted=retained, originals=[dict(retained)], outputs=[])
    assert history._retained_original(tmp_path, row, proof) == retained
    for wrong in (dict(proof, path='native-evidence/002/calls/call-2/accepted.json'),
            dict(proof, size_bytes=12), dict(proof, file_stat=[1, 3, 11, 123456789])):
        with pytest.raises(history.ObserverIntegrityError, match='exact retained path binding'):
            history._retained_original(tmp_path, row, wrong)
    ambiguous = dict(row, outputs=[dict(retained, snapshot='retained/other.json')])
    with pytest.raises(history.ObserverIntegrityError, match='exact retained path binding'):
        history._retained_original(tmp_path, ambiguous, proof)


@pytest.mark.process_e2e
@pytest.mark.parametrize('entry', ['fold', 'project'])
def test_verified_r6_prefix_is_read_once(r6_cost_scene, monkeypatch, entry):
    """N authoritative source reads suffice; a supplied verified fold needs zero."""
    root, evidence, _ = r6_cost_scene
    before_step = max(row['step'] for row in evidence['nodes']) + 1
    sources = history.source_sequence(root, before_step)
    assert len(sources) == len(evidence['nodes']) == 8
    original = history._source
    reads = Counter()

    def counted(root, step, *args, **kwargs):
        reads[step] += 1
        return original(root, step, *args, **kwargs)

    monkeypatch.setattr(history, '_source', counted)
    if entry == 'fold':
        state = search.fold(root, sources)
        bound = 0
    else:
        projected = lineage.project(root, before_step)
        state = projected['search_state']
        assert [row[0]['step'] for row in projected['sources']] == list(range(1, before_step))
        bound = len(sources)
    assert len(state['accepted_slices']) == 3
    assert sum(state['opportunity_counts'].values()) == 6
    assert sum(reads.values()) <= bound, (
        f'{entry} reloaded an already verified scientific prefix: reads={dict(reads)}, '
        f'linear source-read bound={bound}; thread exact source_rows through scientific interpretation')


@contextmanager
def _match_costs(monkeypatch):
    """Measure matcher work per independent managed-status verification.

    C counts the available retained descriptors once for each verification;
    Q counts its native-original lookups. Two passes over C+Q allow validation
    and indexed lookup while forbidding a C-by-Q search for every proof. Every
    repeated verification receives a fresh allowance, so this check does not
    require a global cache or weaken original identity verification.
    """
    counts = Counter()
    scopes = []
    depth = 0
    status, retained, same = history.managed_status, history._retained_original, history._same_original
    validate_original = history.validate_managed_original

    def counted_status(root, row, **kwargs):
        scopes.append(set())
        try:
            return status(root, row, **kwargs)
        finally:
            scopes.pop()

    def counted_validate_original(source, job, rows, **kwargs):
        # Frozen-source validation resolves native_proof directly rather than
        # entering managed_status. Charge each call row's candidate index once
        # within this independent verification, including all its proof lookups.
        scopes.append(set())
        try:
            return validate_original(source, job, rows, **kwargs)
        finally:
            scopes.pop()

    def counted_retained(root, row, proof):
        nonlocal depth
        # Some output publication calls use the same matcher outside status;
        # each such independent lookup receives its own index-build allowance.
        seen = scopes[-1] if scopes else set()
        key = id(row)
        if key not in seen:
            counts['candidate_entries'] += len(_candidates(row))
            seen.add(key)
        counts['lookups'] += 1
        depth += 1
        try:
            return retained(root, row, proof)
        finally:
            depth -= 1

    def counted_same(*args, **kwargs):
        if depth:
            counts['comparisons'] += 1
        return same(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(history, 'managed_status', counted_status)
        patch.setattr(history, 'validate_managed_original', counted_validate_original)
        patch.setattr(history, '_retained_original', counted_retained)
        patch.setattr(history, '_same_original', counted_same)
        yield counts


@pytest.mark.process_e2e
def test_r6_projection_matches_originals_in_linear_work(r6_cost_scene, monkeypatch):
    root, evidence, _ = r6_cost_scene
    before_step = max(row['step'] for row in evidence['nodes']) + 1
    with _match_costs(monkeypatch) as counts:
        projection = lineage.project(root, before_step)
    assert len(projection['sources']) == 8
    assert sum(projection['search_state']['opportunity_counts'].values()) == 6
    assert counts['lookups'] > 0, 'The real projection must exercise retained native-original binding'
    bound = 2 * (counts['candidate_entries'] + counts['lookups'])
    assert counts['comparisons'] <= bound, (
        f'Original matching repeats candidate scans: measured={dict(counts)}, '
        f'two-pass linear bound={bound}; index exact original identities per verified call row')


@pytest.mark.process_e2e
def test_unknown_original_is_read_only_and_never_replayed(r6_cost_scene, monkeypatch):
    """Unknown transport retains original identity and can never trigger dispatch."""
    from scidiscovery.research_loop import experiments
    root, _, _ = r6_cost_scene
    job, _, seal, _ = history._source(root, 8)
    observation = history.read_observation(root, job, seal)
    row = deepcopy(observation['managed_calls'][0])
    row.update(status='unknown', completed=None, terminal=None, observation=None)
    # Pending native evidence has no new terminal result to claim.
    row.pop('execution', None)
    row['outputs'] = []
    identity = deepcopy({key: row[key] for key in ('call_id', 'scope', 'accepted', 'mechanism_binding')})
    accepted_path = root / row['accepted']['snapshot']
    before = accepted_path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError('An unknown original must not submit, replay or claim a terminal observation')

    monkeypatch.setattr(experiments, 'dispatch', forbidden)
    monkeypatch.setattr(experiments, '_submit_descriptor', forbidden)
    monkeypatch.setattr(experiments, 'verify_observation', forbidden)
    assert history.managed_status(root, row) == 'unknown'
    assert {key: row[key] for key in identity} == identity
    assert accepted_path.read_bytes() == before
