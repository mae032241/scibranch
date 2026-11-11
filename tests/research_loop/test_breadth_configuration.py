"""Search settings change future tasks without rewriting accepted science."""
import copy

from scidiscovery.research_loop import configuration_updates as updates, contracts as c, controller, research_config, search
from test_history_handoff import finish


def test_search_off_then_on_preserves_accepted_declarations_and_originals(tmp_path):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Compare exact retained mechanisms')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], model='fixture',
        reasoning_effort='high', max_tokens=1000, max_seconds=60, max_steps=6,
        search_policy=search.DEFAULT_POLICY)
    finish(root, 1, 'H', [], action='wait', search_choice=dict(scope='Fixture comparison',
        commitments=[dict(label='Mechanism P', minimal_test='Distinct prediction', existing_ref=None)], focus=None))
    original_paths = [root / 'jobs/001.json', root / 'native-evidence/001/science-output.json',
        root / 'native-evidence/001/result-identity.json']
    originals = {path: path.read_bytes() for path in original_paths}
    before = search.project(root, 2)
    document = research_config.to_document(root)
    off = copy.deepcopy(document); off['budget'].pop('breadth')
    updates.submit(root, off, expected_revision=0)
    disabled = c.build_job(root, 2, 'W', [1])
    assert c.settings(root, disabled).get('search_policy') is None
    assert 'search' not in disabled
    finish(root, 2, 'W', [1], stage_intent='decision')
    on = research_config.to_document(root, updates.current(root))
    on['budget']['breadth'] = copy.deepcopy(document['budget']['breadth'])
    updates.submit(root, on, expected_revision=1)
    enabled = c.build_job(root, 3, 'W', [1])
    assert enabled['search']['breadth']['mechanisms'][0]['mechanism_ref'] == 'h001.m1'
    finish(root, 3, 'W', [1], stage_intent='decision')
    after = search.project(root, 4)
    assert after['mechanisms'] == before['mechanisms']
    assert after['opportunity_counts'] == before['opportunity_counts']
    assert {path: path.read_bytes() for path in original_paths} == originals
