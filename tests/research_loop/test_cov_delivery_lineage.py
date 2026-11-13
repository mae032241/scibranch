"""Managed delivery uses sealed public call originals, never mutable current files."""
import copy

import pytest

from scidiscovery.research_loop import delivery
from cov_execution import delivery as cov_delivery
from test_delivery import prepare_fixture


@pytest.mark.parametrize('kind', ['missing', 'ambiguous', 'unknown', 'unretained'])
def test_managed_context_problem_never_falls_back_to_current_source(kind):
    proof = dict(name='source.py', size_bytes=1, retention='digest_only', input_label=None)
    context = dict(candidate_id='run-1', run_id=1, status='known_success',
        proofs=[proof, dict(proof, name='model.pkl'), dict(proof, name='row.json')], model=dict(proof, name='model.pkl'))
    contexts = [context]
    if kind == 'missing':contexts = []
    if kind == 'ambiguous':contexts = [context, copy.deepcopy(context)]
    if kind == 'unknown':context['status'] = 'unknown'
    with pytest.raises(cov_delivery.SourceUnavailable):
        cov_delivery.managed_objects(dict(candidate_id='run-1', execution_contexts=contexts), dict(run_id=1))


@pytest.mark.process_e2e
def test_legacy_v1_snapshot_is_explicitly_limited_and_still_works(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    assert refs['items'][0]['status'] == 'audit_pending'
    assert refs['items'][0]['context']['identity_note'].startswith('Legacy current-source snapshot')
