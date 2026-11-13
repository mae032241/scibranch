"""Configured effects are public facts, separate from scientific delivery coverage."""
import json

import pytest

from scidiscovery.research_loop import delivery, generic_delivery


def configured_policy(tmp_path, *, declared=True):
    target = tmp_path / 'target'
    target.mkdir()
    adapter = generic_delivery.descriptor(target)
    if not declared:
        # Even the same executable is not a declaration: never infer from argv.
        adapter.pop('effect_scope')
    return dict(closeout_a=dict(max_observed_tokens=100, max_seconds=10),
        inflight_tokens=0, cleanup_seconds=5, submit_seconds=10,
        confirm_seconds=5, adapter=adapter)



def test_builtin_scope_is_explicit_frozen_and_public_without_private_bindings(tmp_path):
    policy = configured_policy(tmp_path)
    frozen = delivery.policy(policy)
    assert frozen['adapter']['effect_scope'] == policy['adapter']['effect_scope']
    view = delivery.public_configuration(frozen)
    assert view == dict(configured=True, effect_scope=policy['adapter']['effect_scope'])
    assert 'local directory only' in view['effect_scope']
    assert 'Does not train' in view['effect_scope']
    assert 'task-native submission' in view['effect_scope'] and 'scientific audit' in view['effect_scope']
    assert set(view) == {'configured', 'effect_scope'}
    assert str(tmp_path) not in json.dumps(view)
    assert list((tmp_path / 'target').iterdir()) == []


@pytest.mark.parametrize('scope', [None, False, {}, '', ' \n'])
def test_scope_requires_nonempty_public_text(tmp_path, scope):
    policy = configured_policy(tmp_path)
    policy['adapter']['effect_scope'] = scope
    with pytest.raises(ValueError, match='effect_scope'):
        delivery.policy(policy)


@pytest.mark.parametrize('scope', ['x' * 1025, '界' * 342, 'Actual retained delivery effects and missing task steps. ' * 100])
def test_long_scope_is_frozen_and_public_without_authorizing_effects(tmp_path, scope):
    policy = configured_policy(tmp_path)
    policy['adapter']['effect_scope'] = scope
    frozen = delivery.policy(policy)
    assert frozen['adapter']['effect_scope'] == scope
    assert delivery.public_configuration(frozen) == dict(configured=True, effect_scope=scope)
    assert frozen['adapter']['capabilities'] == []
    assert list((tmp_path / 'target').iterdir()) == []


def test_undeclared_scope_stays_absent_from_wire_and_unknown_in_public_view(tmp_path):
    frozen = delivery.policy(configured_policy(tmp_path, declared=False))
    assert 'effect_scope' not in frozen['adapter']
    assert delivery.public_configuration(frozen) == dict(configured=True, effect_scope='unknown')
    assert delivery.public_configuration(None) == dict(configured=False,
        effect_scope='No delivery adapter is configured.')
    assert delivery.public_configuration(dict(adapter=None))['configured'] is False
