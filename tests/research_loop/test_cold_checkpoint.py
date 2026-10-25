"""Cold package capabilities are retired; ordinary owners and paused controls remain."""
import sys

import pytest

from scidiscovery.research_loop import (budget, cli, contracts as c, controller,
    instance_archive as archive, instance_owner as owner)
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


@pytest.fixture
def cold(tmp_path, monkeypatch, request):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Preserve a complete sealed scientific instance.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,[sys.executable],max_tokens=2000,max_seconds=60,max_steps=4,
        model='fixture',reasoning_effort='high',search_policy=None,experiment_mode='external',node_budget_mode='soft',
        storage_policy=getattr(request, 'param', None))
    stopped_seals(monkeypatch)
    finish(root,1,'H',[],action='experiment',files={'result.txt':'Exact sealed scientific bytes.\n'})
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'paused','Cold fixture explicit inactive boundary')
    anchor=tmp_path/'anchor';anchor.mkdir()
    return root,anchor


@pytest.mark.parametrize('name', ['checkpoint_enroll', 'checkpoint_export', 'checkpoint_restore'])
def test_cold_checkpoint_public_apis_are_unavailable(name):
    assert not hasattr(archive, name)


@pytest.mark.parametrize('command', ['checkpoint-enroll', 'checkpoint-export', 'checkpoint-restore'])
def test_cold_checkpoint_commands_reject_before_creating_state(tmp_path, capsys, command):
    root = tmp_path / 'absent-instance'
    anchor = tmp_path / 'new-anchor'
    package = tmp_path / 'historical-package'
    package.mkdir()
    original = package / 'checkpoint.json'
    raw = b'Existing cold package bytes stay untouched.\n'
    original.write_bytes(raw)
    output = tmp_path / 'new-checkpoint'
    options = {
        'checkpoint-enroll': ['--root', str(root), '--anchor-directory', str(anchor)],
        'checkpoint-export': ['--root', str(root), '--output', str(output)],
        'checkpoint-restore': ['--root', str(root), '--package', str(package)],
    }
    with pytest.raises(SystemExit) as error:
        cli.main([command, *options[command]])
    assert error.value.code == 2
    assert 'invalid choice' in capsys.readouterr().err
    assert not root.exists() and not anchor.exists() and not output.exists()
    assert original.read_bytes() == raw
    assert [path.name for path in package.iterdir()] == ['checkpoint.json']


def test_ordinary_owner_and_paused_controls_preserve_existing_budget(cold):
    root, anchor = cold
    ledger = root / 'control-ledger'
    saved = {path.name: path.read_bytes() for path in ledger.iterdir()}
    consumed = budget.snapshot(root)['consumed_seconds']
    assert owner.require_owner(root) is None
    with owner.lease(root):
        with controller._writer(root):
            pass
    assert budget.snapshot(root)['consumed_seconds'] == consumed
    assert {path.name: path.read_bytes() for path in ledger.iterdir()} == saved
    assert not (root / owner.BINDING).exists()
    assert 'cold_owner' not in c.read(root / 'settings-identity.json')
    assert list(anchor.iterdir()) == []
