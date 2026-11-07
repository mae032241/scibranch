"""Consultation advice cannot replace the requesting W's retained input choices."""
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts as c, controller, handoff_state
from scidiscovery.research_loop import shared_input_views
from test_direct_input_bindings import direct_instance, stopped_seals
from test_history_handoff import finish


@pytest.mark.parametrize('role,intent', [('H', 'revise_h'), ('A', 'review')])
@pytest.mark.parametrize('choice', ['omitted', [], ['H001-I001'], ['W999-I001']])
def test_consultation_preserves_requesting_worker_materials_and_selection(
        tmp_path, monkeypatch, role, intent, choice):
    root, _ = direct_instance(tmp_path)
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment', files={
        'other.py': 'An unselected alternative.', 'seed.py': 'The W-selected original.'})
    finish(root, 2, 'W', [1], stage_intent=intent, selection=['H001-I002'],
        files={'candidate.py': 'Partial candidate.', 'residual.txt': 'An unresolved residual.'})
    requester = c.read_job(root / 'jobs/002.json')
    science = handoff_state.read_sealed(root, 2)
    route = controller._route(root, 2, 'W', science, requester, 1, None)
    consultation = c.build_job(root, 3, route[0], route[1], instruction=route[2],
        evidence_return=route[3])
    retained = {row['path']: row for row in consultation['direct_materials']}
    assert set(retained) == {
        'inputs/parents/001/artifacts/seed.py',
        'inputs/parents/002/artifacts/candidate.py',
        'inputs/parents/002/artifacts/residual.txt'}
    selected = consultation['selected_inputs']
    assert [row['label'] for row in selected] == ['H001-I002']
    reuse_instruction = c.response_instructions(consultation)['implementation_reuse']
    assert 'carry forward unchanged' in reuse_instruction
    assert c.IMPLEMENTATION_REUSE not in c.response_instructions(consultation).values()

    # The ordinary fixture writes an explicit null. Omit that optional key in
    # this case while retaining the real terminal transport and seal consumers.
    original_write = c.write
    output = root / 'native-evidence/003/science-output.json'
    def write(path, value):
        if choice == 'omitted' and Path(path) == output:
            value = dict(value)
            value.pop('implementation_selection')
        return original_write(path, value)
    with monkeypatch.context() as authored:
        authored.setattr(c, 'write', write)
        finish(root, 3, role, route[1], action='wait',
            selection=None if choice == 'omitted' else choice,
            files={'advice.txt': 'Consultant-authored evidence and limitations.'})
    original_bytes = output.read_bytes()
    original = c.read(output)
    if choice == 'omitted':
        assert 'implementation_selection' not in original
    else:
        assert original['implementation_selection'] == choice
    sealed = c.read(root / 'native-evidence/003/result-identity.json')
    assert not [row for row in sealed['control_outcome']['diagnostics']
        if row['code'] == 'implementation_unresolved']
    response = handoff_state.read_sealed(root, 3)
    returned = controller._route(root, 3, role, response, consultation, route[4], route[5])
    assert returned[0] == 'W' and returned[3] is None
    successor = c.build_job(root, 4, returned[0], returned[1], instruction=returned[2],
        evidence_return=returned[3])
    assert successor['selected_inputs'] == selected
    actual = {row['path']: row for row in successor['direct_materials']}
    assert all(actual[path] == row for path, row in retained.items())
    assert 'inputs/parents/001/artifacts/other.py' not in actual
    assert 'inputs/parents/003/artifacts/advice.txt' in actual
    # Follow the actual native readonly mount plan to the same exact originals.
    work = c.workspace(root, 4)
    mounts = shared_input_views.mount_arguments(root, successor, work, include_skills=False)
    for path, row in retained.items():
        assert any(mounts[index:index + 3] == ['--ro-bind', row['source'], str(work / path)]
            for index in range(len(mounts) - 2))
    assert output.read_bytes() == original_bytes
