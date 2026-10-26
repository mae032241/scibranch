"""Frozen role guidance policy and byte limits."""
import pytest

from scidiscovery.research_loop import guidance


def test_default_guidance_satisfies_frozen_policy_and_rejects_oversize():
    assert set(guidance.DEFAULT)==set('HWAE')
    assert all(isinstance(text,str) and text.strip() for text in guidance.DEFAULT.values())
    assert sum(len(text.encode()) for text in guidance.DEFAULT.values())<=guidance.MAX_BYTES
    assert guidance.policy(guidance.DEFAULT)==guidance.DEFAULT
    oversized=dict(guidance.DEFAULT)
    oversized['A']+='x'*guidance.MAX_BYTES
    with pytest.raises(ValueError,match='frozen byte bound'):guidance.policy(oversized)
