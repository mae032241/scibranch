"""A-selected scratch audits through the real isolated provider, never promotion."""
import json
from pathlib import Path
import shutil
import sys

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, correction, delivery, history, native, search
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy
from test_history_handoff import finish


def audit_fixture(tmp_path, mode='ok', *, enabled=True, candidate_count=1, correction_enabled=False):
    if shutil.which('bwrap') is None:
        pytest.skip('Actual isolated adapter required')
    public = tmp_path / 'public'
    (public / 'harness').mkdir(parents=True)
    root = tmp_path / 'instance'
    for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
        path = public / 'harness' / name
        if name == 'exp_cli.py':
            path.write_text(FAKE_HARNESS)
        elif name == 'meta.json':
            c.write(path, dict(mode=mode, control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'hidden.json')))
        else:
            path.write_text('Public fixture data only\n')
    c.write(tmp_path / 'hidden.json', dict(secret='Must not enter audit'))
    objective = public / 'objective.md'
    objective.write_text('Decide scientifically after one bounded public audit; no automatic submission.')
    limits = {role: dict(max_observed_tokens=2000, max_seconds=60) for role in ('H', 'W', 'A', 'E')}
    controller.prepare(root, objective, public, ['fake-no-model'], max_tokens=100_000,
        max_seconds=400, max_steps=16, model='fixture', reasoning_effort='high', search_policy=search.DEFAULT_POLICY if correction_enabled else None,
        correction_policy=dict(review_after=1,pause_after=1) if correction_enabled else None,
        node_budget_policy=limits, delivery_policy=delivery_policy(descriptor(root, public)),
        candidate_audit_policy={'version':1} if enabled else None,
        history_observer=dict(adapter=str(Path(__file__).resolve().parents[2] / 'plugins/cov_execution/cov_execution/observer.py'),
                              config=dict(runs='runs', evaluation_key='fixture-public')))
    (root / 'runs').mkdir()
    (root / 'submission').mkdir()
    h_plan = dict(scope='Scientific discrimination', commitments=[dict(label='Mechanism',minimal_test='One controlled comparison',existing_ref=None)], focus=dict(label='Mechanism',action='screen')) if correction_enabled else None
    finish(root, 1, 'H', [], action='experiment',search_choice=h_plan)
    c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    model = root / 'runs/exact-model.bin'
    model.write_bytes(b'Exact audit model; never host-unpickled\n' * 20_000)
    script = c.workspace(root, 2) / 'artifacts/train.py'
    row = dict(ok=True, val_acc=.6, val_score=.6, model_path='/opt/exp/runs/exact-model.bin', script=str(script))
    (root / 'runs/results.jsonl').write_text(''.join(json.dumps(dict(row, run_id=i))+'\n' for i in range(1,candidate_count+1)))
    finish(root, 2, 'W', [1], files={'train.py':'def train(train_df,val_df): return None\n'},
        mechanism_findings=[dict(mechanism='Mechanism',basis='blocked',finding='Fixture has no managed scientific observation')] if correction_enabled else None)
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'fixture')
    return root


def request(root, candidate='run-1'):
    job, _, _ = finish(root, 3, 'A', [1, 2], action='audit', recommendation=candidate, progress={'verdict':'no_progress'})
    return job, c.validate(c.workspace(root, 3))


def _seal_existing(root,step,payload):
    if c.read(root/'jobs'/f'{step:03d}.json')['role']=='A':
        payload={'decision_request':None,'portfolio_review':None,**payload}
    record=c.host_record(c.workspace(root,step))
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    from test_history_handoff import selection_explanation
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json')))
    return c.seal_result(root,step)
