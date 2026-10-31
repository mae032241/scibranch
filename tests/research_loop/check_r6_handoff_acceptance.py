"""Same-corpus handoff timing; no model requests or wall-clock pass threshold."""
import json
import os
from pathlib import Path
import time

import pytest
from scidiscovery.research_loop import contracts as c, lineage
from execution_fixtures import execution_registry
from r6_handoff_fixture import r6_cost_scene


@pytest.mark.process_e2e
def test_same_r6_handoff_elapsed(r6_cost_scene):
    root, evidence, _ = r6_cost_scene
    started = time.perf_counter()
    job = c.build_job(root, 9, 'A', [7, 8])
    build_next = time.perf_counter()-started
    assert job['step'] == 9 and job['role'] == 'A'
    started = time.perf_counter()
    projection = lineage.project(root, 9, include_details=False)
    project_seconds = time.perf_counter()-started
    assert len(projection['sources']) == 8
    report = dict(case='r6-eight-node-six-call', node_count=8,
        managed_call_count=sum(x.get('managed_call_count', 0) for x in evidence['nodes']),
        phases=evidence['_timings'], next_a_build_seconds=build_next,
        prefix_projection_seconds=project_seconds,
        total_seal_seconds=sum(x['seconds'] for x in evidence['_timings'] if x['operation']=='seal'),
        operation_scope=evidence['_timing_scope'],
        scope='Measured framework seal/task preparation only; no model inference. Same checked-in corpus and actions. Post-fix fixture keeps one controller operation scope active through all seals and the next A build; the recorded pre-fix baseline had no such scope. Fresh-scope restore is checked separately. Timing is evidence, not a machine-dependent assertion.')
    directory = Path(os.environ.get('SCID_TEST_LOG_DIR', str(root.parent)))
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'r6-handoff-timings.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report))
