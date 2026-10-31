"""Portable eight-node handoff cost scene; inert Local commands, no models.

The checked-in r6 evidence supplies topology and mechanism identities. Scientific
text is deliberately authored fixture data. Existing Local process fixtures need
the native launch capability and Linux namespace support, and create a temporary
synthetic native HOME. Successful transport supplies no scientific qualification.
"""
import json
from pathlib import Path

import pytest

from test_breadth_science import prepare, run, worker
from test_history_handoff import finish


CORPUS = Path(__file__).parent / 'fixtures/r6_handoff_examples.json'


@pytest.fixture
def r6_cost_scene(tmp_path, execution_registry):
    """Create exact, newly sealed originals for the recorded 3/2/1 call prefix."""
    evidence = json.loads(CORPUS.read_text())
    root = prepare(tmp_path)
    configurations = {}
    for node in evidence['nodes']:
        step, role, parents = node['step'], node['role'], node['parents']
        if role == 'H':
            declared = [row for row in evidence['mechanisms'] if row['introduced_h'] <= step]
            choice = dict(scope='Synthetic competing causal explanations', commitments=[dict(
                label=row['label'], minimal_test='A bounded synthetic discriminator; target evidence is missing.',
                existing_ref=row['mechanism_ref'] if row['introduced_h'] < step else None)
                for row in declared], focus=dict(index=None, action='screen'))
            finish(root, step, role, parents, action='experiment', search_choice=choice)
        elif role == 'A':
            finish(root, step, role, parents, action='revise_h',
                search_choice=dict(dispositions=[], next_focus=None))
        else:
            job, config = worker(root, tmp_path, step, parents)
            configurations[step] = config
            required = job['search']['coverage_obligation']
            assert [dict(mechanism_ref=row['mechanism_ref'], source_h=row['source_h'])
                for row in required] == node['coverage_obligation']
            assert job['search']['focus'] == node['focus']
            requested = [row['mechanism_ref'] for row in evidence['mechanisms']
                if row['introduced_h'] == parents[0]]
            assert len(requested) == node['managed_call_count']
            for ref in requested:
                result = run(root, config, ref)
                assert result['status'] == 'known_success', result
            # Revised H obligations retain older identities. A canonical
            # explicit blocker seals a partial mechanical replay for those
            # absent comparisons, without another execution or allowance.
            # This authors fixture science, not the r6 scientific verdict.
            finish(root, step, role, parents, preserve_native_intent=True,
                mechanism_findings=[dict(mechanism=ref, basis='observed',
                    finding='Mechanical fixture observation only; scientific adequacy is unknown.')
                    for ref in requested] + [dict(mechanism=row['mechanism_ref'], basis='blocked',
                    finding='Explicit synthetic scientific blocker: the revised comparison is unavailable; A must retain this gap.')
                    for row in required if row['mechanism_ref'] not in requested])
    return root, evidence, configurations
