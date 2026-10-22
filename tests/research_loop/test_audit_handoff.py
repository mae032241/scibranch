"""Frozen adapter receipts survive scientific handoffs without scientific promotion."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, delivery, history
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy
from test_history_handoff import finish
from test_lineage import scene as lineage_scene
from scidiscovery.research_loop import cov_lineage as cov


def audited_scene(tmp_path, monkeypatch):
    """A real v2 completed call and isolated scratch audit, with no model requests."""
    if shutil.which('bwrap') is None:
        pytest.skip('Actual isolated adapter required')
    prepare = controller.prepare

    def configured(root, objective, public, launcher, **kwargs):
        (public / 'harness').mkdir()
        for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
            path = public / 'harness' / name
            if name == 'exp_cli.py':
                path.write_text(FAKE_HARNESS)
            elif name == 'meta.json':
                c.write(path, dict(mode='ok', control_path=str(root / 'settings.json'),
                                   hidden_path=str(tmp_path / 'hidden.json')))
            else:
                path.write_text('Public fixture data\n')
        c.write(tmp_path / 'hidden.json', dict(secret='Not an audit input'))
        adapter = descriptor(root, public)
        # This backend's exact stdout names host runs, not the real task's /opt alias.
        adapter['config']['namespace_runs'] = str(root / 'runs')
        kwargs.update(max_tokens=100_000, max_seconds=400, max_steps=16,
                      candidate_audit_policy={'version': 1},
                      delivery_policy=delivery_policy(adapter),
                      node_budget_policy={role: dict(max_observed_tokens=2000, max_seconds=60)
                                          for role in ('H', 'W', 'A', 'E')})
        return prepare(root, objective, public, launcher, **kwargs)

    monkeypatch.setattr(controller, 'prepare', configured)
    root, config, digest = lineage_scene(tmp_path)
    (root / 'submission').mkdir()
    script = c.workspace(root, 2) / 'work/train.py'
    script.write_text('success')
    result = cov.run(config, digest, script, new=True)
    assert result['status'] == 'known_success'
    finish(root, 2, 'W', [1])
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'fixture')
    c.build_job(root, 3, 'A', [1, 2], instruction='Exact scientific question')
    job, _, _ = finish(root, 3, 'A', [1, 2], action='audit', recommendation=result['candidate_id'])
    assert candidate_audit.execute(root, job, c.validate(c.workspace(root, 3))) is None
    receipt = candidate_audit.public_receipt(root, candidate_audit.receipt_ref(root, 3))
    assert receipt['status'] == 'audited', receipt
    return root, result['candidate_id']


def next_review(root):
    c.build_job(root, 4, 'A', [1, 2, 3], instruction='Exact scientific question')
    job, _, _ = finish(root, 4, 'A', [1, 2, 3], action='revise_h')
    return job


def across_mechanism(root):
    c.build_job(root, 5, 'H', [4], instruction='Exact scientific question')
    finish(root, 5, 'H', [4], action='experiment')
    c.build_job(root, 6, 'W', [5], instruction='Exact scientific question')
    finish(root, 6, 'W', [5])
    return c.build_job(root, 7, 'A', [5, 6], instruction='Exact scientific question')


@pytest.mark.process_e2e
def test_original_audit_survives_a_h_w_and_is_bounded_readonly(tmp_path, monkeypatch):
    root, candidate = audited_scene(tmp_path, monkeypatch)
    first = next_review(root)
    later = across_mechanism(root)
    assert 'candidate_audit' in first and 'candidate_audit' not in later
    for job in (first, later):
        work = c.workspace(root, job['step'])
        view = c.read(work / 'science-task.json')['lineage']
        rows = view['candidates']['adapter_audits']
        assert len(rows) == 1 and rows[0]['candidate_id'] == candidate
        assert rows[0]['adapter_result'] == 'audited' and rows[0]['artifact_binding'] == 'matched'
        assert rows[0]['qualification'] == 'passed' and rows[0]['scope'] == candidate_audit.SCOPE
        assert 'public_audits' not in view['candidates']  # No contradictory unknown duplicate slot.
        assert len((json.dumps(view, ensure_ascii=False, indent=2) + '\n').encode()) <= history.MAX_BRIEF_BYTES
        page = work / view['indexes']['audits']
        assert page.is_file() and page.stat().st_mode & 0o222 == 0
        assert c.read(page)['adapter_audits'] == rows
        public = json.dumps(c.read(page))
        assert str(root) not in public and 'sha256' not in public
        assert job['history']['audit_receipts'] == [candidate_audit.receipt_ref(root, 3)]
        c._validate_inputs(root, work, job)


@pytest.mark.process_e2e
@pytest.mark.parametrize('identity', ['different', 'absent'])
def test_audit_cannot_qualify_different_or_missing_candidate_artifact(tmp_path, monkeypatch, identity):
    root, candidate = audited_scene(tmp_path, monkeypatch)
    sources = history.source_sequence(root, 4)
    records = copy.deepcopy(history.records_from_sources(root, sources, write_cache=False))
    context = next(row for record in records for row in (record.get('observation') or {}).get('contexts', [])
                   if row['candidate_id'] == candidate)
    if identity == 'different':
        context['model']['sha256'] = '0' * 64
    else:
        context['model'] = None
    rows = candidate_audit.project_receipts(root, records, [candidate_audit.receipt_ref(root, 3)])
    assert rows[0]['adapter_result'] == 'audited'
    assert rows[0]['artifact_binding'] == ('different' if identity == 'different' else 'unknown')
    assert rows[0]['qualification']=='artifact_scope_unknown'


@pytest.mark.process_e2e
def test_adapter_nonleaking_note_is_not_a_leakage_fact(tmp_path, monkeypatch):
    root, _ = audited_scene(tmp_path, monkeypatch)
    ref = candidate_audit.receipt_ref(root, 3)
    assert 'nonleaking' in candidate_audit.public_receipt(root, ref)['note']
    job = next_review(root)
    row = job['history']['view']['candidates']['adapter_audits'][0]
    assert row['qualification']=='passed' and 'leakage_flag' not in row and 'note' not in row
    assert 'target_best' not in job['history']['view']['candidates']
    assert not (root / 'runs/submitted.json').exists()
    assert not list((root / 'submission').iterdir())


@pytest.mark.process_e2e
def test_current_projection_marker_preserves_exact_bytes(tmp_path, monkeypatch):
    root, _ = audited_scene(tmp_path, monkeypatch)
    job = next_review(root)
    from scidiscovery.research_loop.protocol import CURRENT
    assert job['handoff_protocol'] == CURRENT and job['audit_projection_version'] == 1
    originals = {path: path.read_bytes() for path in (
        root / 'jobs/004.json', c.workspace(root, 4) / 'science-task.json',
        c.host_record(c.workspace(root, 4)) / 'result-identity.json')}
    assert c.build_job(root, 4, 'A', [1, 2, 3], instruction='Exact scientific question') == job
    assert c.validate(c.workspace(root, 4))['next_action'] == 'revise_h'
    assert all(path.read_bytes() == raw for path, raw in originals.items())
    later = across_mechanism(root)
    assert later['audit_projection_version'] == 1
    assert later['history']['view']['candidates']['adapter_audits'][0]['artifact_binding'] == 'matched'


@pytest.mark.process_e2e
@pytest.mark.parametrize('original', ['outcome', 'page'])
def test_frozen_receipt_or_projection_page_corruption_hard_rejects(tmp_path, monkeypatch, original):
    root, _ = audited_scene(tmp_path, monkeypatch)
    next_review(root)
    job = across_mechanism(root)
    work = c.workspace(root, 7)
    if original == 'outcome':
        path = candidate_audit._directory(root, 3) / 'outcome.json'
        value = c.read(path)
        value['result']['note'] = 'Changed after the frozen source anchor'
        c.write(path, value)
    else:
        path = work / job['history']['view']['indexes']['audits']
        path.chmod(0o600)
        path.write_text('{}\n')
    with pytest.raises(ValueError):
        c._validate_inputs(root, work, job)
    assert not (c.host_record(work) / 'result-identity.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('corrupt', [False, True])
def test_closeout_author_prefix_keeps_exact_audit_without_inheriting_original_node(tmp_path, corrupt):
    from test_candidate_audit import audit_fixture, _seal_existing
    root = audit_fixture(tmp_path)
    refs = delivery.freeze_sources(root, [dict(step=i, seal_sha256=history._source(root, i)[3]) for i in (1, 2)])
    budget.begin_closeout(root, 2, 3, 'Explicit closeout', delivery_sources=refs)
    job = c.build_job(root, 3, 'A', [1, 2], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    payload = dict(summary='Need adapter audit', scientific_result='Preserve original model for independent review',
                   limitations=['Fixture only'], implementation_selection=None, candidate_recommendation='run-1',
                   search_review=None, progress_assessment=None, next_action='audit', route_reason='Bounded audit')
    _seal_existing(root, 3, payload)
    assert candidate_audit.execute(root, job, c.validate(c.workspace(root, 3))) is None
    assert candidate_audit.public_receipt(root, candidate_audit.receipt_ref(root, 3))['status'] == 'audited'
    c.build_job(root, 4, 'A', [1, 2, 3], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    payload.update(summary='Correction needs another A', next_action='finish', candidate_recommendation=None, route_reason='No final decision')
    _seal_existing(root, 4, payload)
    if corrupt:
        path = candidate_audit._directory(root, 3) / 'outcome.json'
        changed = c.read(path); changed['result']['note'] = 'Changed after A4 froze the receipt'
        c.write(path, changed)
        with pytest.raises(ValueError):
            c.build_job(root, 5, 'A', [4], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    else:
        following = c.build_job(root, 5, 'A', [4], purpose='closeout', source_cutoff=2, delivery_sources=refs)
        assert [row['step'] for row in following['history']['nodes']] == [1, 2, 4]
        assert following['history']['audit_receipts'] == [candidate_audit.receipt_ref(root, 3)]
        assert following['history']['view']['candidates']['adapter_audits'][0]['adapter_result'] == 'audited'
        c._validate_inputs(root, c.workspace(root, 5), following)
