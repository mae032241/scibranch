"""Explicit UI navigation persistence and identity binding; no research effects."""
import json
import stat

import pytest

from scidiscovery.research_loop import workspace_catalog as catalog


def test_catalog_retains_all_explicit_entries_and_refuses_identity_rebinding(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    for number in range(103):
        root = tmp_path / str(number)
        catalog.remember(workspace, 'instances', dict(root=str(root), goal_id=str(number)))
    rows = catalog.load(workspace, 'instances')
    assert len(rows) == 103
    assert stat.S_IMODE((workspace / '.scid-ui/instances.json').stat().st_mode) == 0o600
    first = rows[0]
    with pytest.raises(ValueError, match='identity changed'):
        catalog.remember(workspace, 'instances', dict(first, goal_id='replacement'))
    assert catalog.load(workspace, 'instances') == rows
    catalog.remember(workspace, 'instances', first, remove=True)
    assert catalog.load(workspace, 'instances') == rows[1:]


def test_catalog_rejects_unsupported_format_and_symlink_without_touching_target(tmp_path):
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    directory = workspace / '.scid-ui'; directory.mkdir()
    path = directory / 'instances.json'
    path.write_text(json.dumps(dict(version=2, instances=[]))); path.chmod(0o600)
    with pytest.raises(ValueError, match='Unsupported'):
        catalog.load(workspace, 'instances')
    frozen = path.read_bytes()
    with pytest.raises(ValueError, match='Unsupported'):
        catalog.remember(workspace, 'instances', dict(root=str(tmp_path / 'known'), goal_id='one'))
    assert path.read_bytes() == frozen
    target = tmp_path / 'target.json'; path.rename(target); path.symlink_to(target)
    with pytest.raises(OSError):
        catalog.load(workspace, 'instances')
    assert target.read_bytes() == frozen


def test_catalog_updates_preserve_unrelated_bad_rows_for_ui_diagnostics(tmp_path):
    directory = tmp_path / '.scid-ui'; directory.mkdir()
    path = directory / 'instances.json'
    path.write_text(json.dumps(dict(version=1, instances=[dict(root='invalid')]))); path.chmod(0o600)
    good = dict(root=str(tmp_path / 'known'), goal_id='one')
    catalog.remember(tmp_path, 'instances', good)
    assert catalog.load(tmp_path, 'instances') == [dict(root='invalid'), good]
