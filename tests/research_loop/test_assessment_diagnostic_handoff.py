"""Unpublished advisory A drafts never become reusable scientific materials."""
import json

from scidiscovery.research_loop import contracts as c, controller
from test_direct_input_bindings import stopped_seals
from test_history_handoff import finish


MISSING = 'No public dataset or model was supplied; no scientific comparison was run.'
GAP = 'The A diagnostic exists only as a work draft and was not published for reuse.'


def test_unpublished_work_diagnostic_allows_bounded_a_seal_and_retains_the_gap(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain exact authored diagnostic originals and their limitations.\n')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'],
        max_tokens=10000, max_seconds=300, max_steps=4, model='fixture',
        reasoning_effort='high', search_policy=None, experiment_mode='external')
    stopped_seals(monkeypatch)
    finish(root, 1, 'H', [], action='experiment', limitations=[MISSING])
    finish(root, 2, 'W', [1], limitations=[MISSING])
    c.build_job(root, 3, 'A', [1, 2])
    work = c.workspace(root, 3)
    draft = work / 'work' / 'diagnostic.py'
    draft.write_text('# Unpublished arithmetic draft; no scientific inputs.\n')
    (work / 'work' / 'report.json').write_text(json.dumps(dict(results=None, limitations=[GAP, MISSING])))
    _, _, record = finish(root, 3, 'A', [1, 2], action='continue_w',
        text='Only work/diagnostic.py and work/report.json drafts exist. ' + GAP,
        limitations=[GAP, MISSING])
    seal = c.read(record / 'result-identity.json')
    assert seal['materials'] == seal['entry_points'] == []
    assert seal['control_outcome']['diagnostics'] == []
    assert c.read(record / 'science-output.json')['limitations'] == [GAP, MISSING]
    successor = c.build_job(root, 4, 'W', [3])
    assert successor['direct_materials'] == successor['implementation_options'] == []
    assert not successor.get('selected_inputs')
    assert draft.read_text() == '# Unpublished arithmetic draft; no scientific inputs.\n'
