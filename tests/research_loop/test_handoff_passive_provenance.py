"""Passive saved restriction facts and exact current scientific input checks.

Synthetic findings establish no scientific qualification or execution permission.
"""
import pytest

from scidiscovery.research_loop import (
    contracts as c, execution_inputs, handoff_state, history, originals,
    qualification as q,
)
from test_history_handoff import finish
from test_source_qualification import instance


@pytest.mark.parametrize('declaration,expected', [('restricted', 'restricted'), (None, 'unknown')])
def test_cold_successor_preserves_saved_restrictions_without_old_gates(tmp_path, monkeypatch, declaration, expected):
    root = instance(tmp_path, restriction=declaration)
    with handoff_state.scope(root):
        first, _, _ = finish(root, 1, 'E', [], files={'candidate.txt': 'Unqualified fixture original.'})
        second, _, _ = finish(root, 2, 'E', [1])
    assert first['source_restrictions']['restriction'] == second['source_restrictions']['restriction'] == expected

    def no_old_job(function):
        def checked(root, job, *args, **kwargs):
            assert job['step'] > 2, 'Passive handoff reopened old provenance or managed-call gates'
            return function(root, job, *args, **kwargs)
        return checked

    native_outcome = c.native_outcome
    def current_native(record, *args, **kwargs):
        assert record not in (root / 'native-evidence/001', root / 'native-evidence/002')
        return native_outcome(record, *args, **kwargs)

    def forbidden(*args, **kwargs):
        raise AssertionError('Passive handoff revalidated a sealed original')

    monkeypatch.setattr(c, 'native_outcome', current_native)
    monkeypatch.setattr(c, 'validate_original', forbidden)
    monkeypatch.setattr(q, '_original_inputs', forbidden)
    monkeypatch.setattr(q, '_output', no_old_job(q._output))
    monkeypatch.setattr(q, 'validate_inputs', no_old_job(q.validate_inputs))
    monkeypatch.setattr(history, 'validate_input_coverage', no_old_job(history.validate_input_coverage))
    with handoff_state.scope(root):
        with monkeypatch.context() as facts:
            facts.setattr(c, 'read_scientific_output', forbidden)
            assert q.original_status(originals.node(root, 2), controls_only=True)['restriction'] == expected
            assert q.output_status(root, 2)['restriction'] == expected
        current, _, _ = finish(root, 3, 'E', [2])
        assert current['source_restrictions']['restriction'] == expected
        c._validate_inputs(root, c.workspace(root, 3), current)


def test_selected_material_producer_uses_saved_seal_but_checks_current_original(tmp_path, monkeypatch):
    root = instance(tmp_path, restriction='clear')
    finish(root, 1, 'E', [], files={'candidate.txt': 'Exact selected fixture material.'})
    seal = c.read(root / 'native-evidence/001/result-identity.json')
    private = seal['materials'][0]
    material = {key: value for key, value in private.items() if key != 'file_stat'}

    def forbidden(*args, **kwargs):
        raise AssertionError('A selected artifact reopened its producer native gate')

    monkeypatch.setattr(c, '_execution_raw', forbidden)
    assert execution_inputs._producer(root, material)['producer_step'] == 1
    original = originals.material_path(root, material)
    original.chmod(0o600)
    original.write_bytes(original.read_bytes() + b' changed')
    with pytest.raises((ValueError, OSError)):
        execution_inputs._producer(root, material)


@pytest.mark.parametrize('saved,additional,expected', [
    ('clear', 'unknown', 'unknown'),
    ('unknown', 'clear', 'unknown'),
    ('restricted', 'clear', 'restricted'),
    ('unknown', 'restricted', 'restricted'),
    (None, 'clear', 'unknown'),
])
def test_saved_output_restriction_never_upgrades_missing_or_restricted_facts(saved, additional, expected):
    job = {'source_restrictions': {'restriction': saved}} if saved is not None else {}
    assert q._saved_output_restriction(job, {'additional_input_restriction': additional}) == expected
