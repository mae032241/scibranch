"""Stopped checkpoints reference terminal originals and leave drafts editable."""
from pathlib import Path

import pytest

from test_direct_public_collect import (execution_registry, fixed_code, fragment,
    observed_local, replace_equal_bytes, stopped_attempt, terminal_code)
from test_worker_fragments import science
from scidiscovery.research_loop import checkpoint, contracts as c, experiments as e


def test_checkpoint_keeps_mutable_drafts_and_reuses_terminal_originals(
        fragment, observed_local, monkeypatch):
    root, job, record = fragment
    row = observed_local()
    science(record)
    stopped_attempt(root, job, record, monkeypatch)
    output = row['output']
    saved_output = e.proof(output)
    draft = c.workspace(root, job['step']) / 'artifacts/current.txt'
    draft.write_bytes(b'mutable current implementation')
    saved_draft = e.proof(draft)
    draft_mode = draft.stat().st_mode
    # Saved control/history reads do not consume a current authoring input or
    # the already-cleaned temporary code export.
    Path(row['accepted']['source_script']).unlink()
    assert not Path(row['accepted']['script']['path']).exists()
    value = checkpoint.capture(root, job, record)
    original = next(item['original'] for item in value['materials']
        if root / item['original']['snapshot'] == output)
    assert original['file_stat'] == saved_output['file_stat']
    assert original['size_bytes'] == saved_output['size_bytes']
    assert all(root / item['original']['snapshot'] != draft for item in value['materials'])
    assert e.proof(draft) == saved_draft and draft.stat().st_mode == draft_mode
    saved_checkpoint = (record / 'checkpoint.json').read_bytes()
    draft.write_bytes(b'later author revision')
    draft.unlink()
    for _ in range(2):
        assert checkpoint.capture(root, job, record) == value
        assert checkpoint.validate(root, job, record) == value
    assert e.proof(output) == saved_output
    assert (record / 'checkpoint.json').read_bytes() == saved_checkpoint
    assert not (record / 'result-identity.json').exists()


def test_checkpoint_rejects_changed_terminal_control(fragment, observed_local, monkeypatch):
    root, job, record = fragment
    row = observed_local()
    science(record)
    stopped_attempt(root, job, record, monkeypatch)
    checkpoint.capture(root, job, record)
    saved = (record / 'checkpoint.json').read_bytes()
    replace_equal_bytes(row['directory'] / 'terminal.json')
    for read in (checkpoint.validate, checkpoint.capture):
        with pytest.raises(ValueError, match='changed|original|observation'):
            read(root, job, record)
    assert (record / 'checkpoint.json').read_bytes() == saved


def test_checkpoint_refuses_a_live_original_writer(fragment, observed_local, monkeypatch):
    root, job, record = fragment
    row = observed_local()
    science(record)
    live, receipts = stopped_attempt(root, job, record, monkeypatch)
    draft = c.workspace(root, job['step']) / 'artifacts/current.txt'
    draft.write_bytes(b'editable while the original writer lives')
    original, mode = e.proof(draft), draft.stat().st_mode
    live.add(receipts['native-process.json']['pid'])
    with pytest.raises(ValueError, match='writer|owns|stopp'):
        checkpoint.capture(root, job, record)
    assert not (record / 'checkpoint.json').exists()
    assert e.proof(draft) == original and draft.stat().st_mode == mode
    live.clear()
    value = checkpoint.capture(root, job, record)
    assert any(root / item['original']['snapshot'] == row['output'] for item in value['materials'])
    live.add(receipts['bridge-process.json']['pid'])
    for read in (checkpoint.validate, checkpoint.capture):
        with pytest.raises((ValueError, BlockingIOError), match='bridge|owns|stopp'):
            read(root, job, record)
