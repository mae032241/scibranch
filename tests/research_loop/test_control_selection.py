"""Explicit scientific candidate choice and side-effect-free catalogue; zero models."""
from test_history_handoff import selection_explanation
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop.protocol import CURRENT
from scidiscovery.research_loop import budget, contracts as c, controller, delivery, native, selection, shortlist
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy
from test_history_handoff import finish

REPO = Path(__file__).resolve().parents[2]
OBSERVER = REPO / 'plugins/cov_execution/cov_execution/observer.py'
LAUNCHER = REPO.parents[1] / '.trial/rootless-task-unit-cov-20261014/launcher.json'
RUNNER = '''def split_X_y(df, meta):
    targets = meta['target_cols'];features = [c for c in df.columns if c not in targets]
    return df[features], df[targets].values, features
'''


def cand(cid, metric, key='v1', status='ok'):
    return dict(id=cid, label=cid, evaluation_key=key, metric=metric, direction='max', status=status)


def rec(step, role, candidates=(), recommendation=None, audit=False):
    return dict(step=step, role=role, recommendation=recommendation, coverage='complete' if candidates else None,
                candidates=list(candidates), current_candidate=None, actual_submission=None, **(dict(audit_request=True) if audit else {}))


def science_python():
    if shutil.which('bwrap') is None:pytest.skip('Actual isolated adapter namespace required')
    for python in (sys.executable, c.read(LAUNCHER)['task_python'] if LAUNCHER.is_file() else None):
        if python and subprocess.run([python, '-I', '-c', 'import numpy,pandas'], capture_output=True).returncode == 0:
            return python
    pytest.skip('Smoke needs an interpreter with numpy/pandas (the frozen task Python)')


def scene(tmp_path, *, policy={'version':2}, metrics=(.9, .8, .7), launcher=None, author_action='finish', harness=None):
    python = science_python()
    public, root = tmp_path / 'public', (tmp_path / 'instance').absolute()
    (public / 'harness').mkdir(parents=True)
    files = {'exp_cli.py': harness or FAKE_HARNESS, 'runner.py': RUNNER, 'train.csv': 'public fixture only\n', 'val.csv': 'y0,y1,x0,x1\n1,2,0,0\n3,4,0,1\n'}
    for name, text in files.items():(public / 'harness' / name).write_text(text)
    c.write(public / 'harness/meta.json', dict(mode='ok', target_cols=['x0', 'x1'], control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'hidden.json')))
    c.write(tmp_path / 'hidden.json', dict(secret='Never an input'))
    objective = public / 'objective.md';objective.write_text('Deliver one exact public candidate; completion is not asserted.')
    kwargs = dict(selection_policy=policy)
    controller.prepare(root, objective, public, launcher or ['fake-no-model'], max_tokens=100_000, max_seconds=600, max_steps=24, model='fixture',
        reasoning_effort='high', search_policy=None, node_budget_policy={role: dict(max_observed_tokens=2000, max_seconds=60) for role in 'HWAE'},
        delivery_policy=delivery_policy(descriptor(root, public, python=python)), candidate_audit_policy={'version': 1},
        history_observer=dict(adapter=str(OBSERVER), config=dict(runs='runs', evaluation_key='fixture-public')), **kwargs)
    (root / 'runs').mkdir();(root / 'submission').mkdir()
    finish(root, 1, 'H', [], action='experiment')
    c.build_job(root,2,'W',[1],instruction='Exact scientific question')
    script = c.workspace(root, 2) / 'artifacts/train.py'
    # run-1 is the public best but its saved bytes are not a loadable model; the others predict zeros.
    (root / 'runs/model-1.pkl').write_bytes(b'not a pickle')
    for run in (2, 3):
        subprocess.run([python, '-I', '-c', 'import numpy,pickle,sys,types;pickle.dump(types.SimpleNamespace(predict=numpy.zeros_like),open(sys.argv[1],"wb"))',
                        str(root / f'runs/model-{run}.pkl')], check=True, timeout=30)
    (root / 'runs/results.jsonl').write_text(''.join(json.dumps(dict(run_id=run, ok=True, val_acc=metric, val_score=metric,
        model_path=f'/opt/exp/runs/model-{run}.pkl', script=str(script))) + '\n' for run, metric in enumerate(metrics, 1)))
    finish(root, 2, 'W', [1], files={'train.py': 'def train(train_df, val_df): return None\n'})
    finish(root, 3, 'A', [1, 2], action=author_action, recommendation='run-3', progress={'verdict': 'progress'})
    c.write(root / 'host-started.json', budget.clock_stamp())
    return root


def closeout_author(monkeypatch, *attempts, authorize_final=True,cleanup='observed_group_cleaned'):
    calls = []
    def complete(root, step):
        task = c.read(c.workspace(root, step) / 'science-task.json')
        payload = dict(summary='Closeout comparison', scientific_result='Public validation only; target suitability is not asserted.',
                       limitations=['Fixture transport, not a model'], implementation_selection=None, search_review=None,
                       progress_assessment=None, decision_request=None, portfolio_review=None, next_action='deliver',
                       route_reason='Bounded delivery decision')
        payload.update(attempts[len(calls)](task))
        if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
        calls.append((step, task))
        record = c.host_record(c.workspace(root, step))
        job=c.read(root/'jobs'/f'{step:03d}.json')
        intent=dict(step=step,task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'))
        if job.get('final_only') and authorize_final:intent['budget_slice']=budget.accept_node(root,job)
        c.write(record/'native-intent.json',intent)
        c.write(record / 'science-output.json', payload)
        meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
        c.write(record/'usage.json',meter)
        observation=dict(coverage='terminal',source='turn.completed',tokens=meter)
        c.write(record/'usage-observation.json',observation)
        usage=native.usage(root)
        outcome=dict(returncode=0,stop_reason=None,cleanup=cleanup,usage=usage,raw_ref=c.file_ref(record/'science-output.json'))
        accepted=budget.final_a_return(root,job,intent)
        if accepted is not None:
            outcome.update(metering=observation,final_a_completion=budget.final_a_receipt(accepted,intent,meter,usage))
        c.write(record/'native-outcome.json',outcome)
        c.seal_result(root, step)
    monkeypatch.setattr(native, 'execute_native', complete)
    return calls


def table(keep, *, ids=('run-1', 'run-2', 'run-3'), rationale='W002 retained implementation fits the declared scope.', evidence_steps=(2,)):
    return dict(rows=[dict(candidate_id=cid, key_risk='Public evidence only', decision='keep' if cid == keep else 'replace') for cid in ids],
                rationale=rationale, evidence_steps=list(evidence_steps))


def choose(candidate, compared=None):
    def author(task):
        ids=(candidate,) if compared is None else compared
        return dict(candidate_recommendation=candidate, candidate_comparison=table(candidate, ids=ids))
    return author


def test_candidate_catalogue_preserves_every_identity_and_metric_changes_cannot_reorder():
    rows=[cand('tie-a', .9), cand('tie-b', .9), cand('unscored', None), cand('other-version', 900, key='v2'),
          cand('failed', None, status='failed'), cand('unknown', None, status='unknown')]
    records=[rec(2,'W',rows),rec(3,'A',recommendation='tie-b')]
    original=selection.shortlist(records)
    assert [row['candidate_id'] for row in original['members']]==[row['id'] for row in rows]
    changed=[dict(row,metric=-1000 if row['metric'] is not None else None) for row in rows]
    again=selection.shortlist([rec(2,'W',changed),rec(3,'A',recommendation='tie-b')])
    assert [row['candidate_id'] for row in again['members']]==[row['id'] for row in rows]
    assert all(not ({'rank','reasons','default'} & set(row)) for row in original['members'])


@pytest.mark.parametrize('extra',[dict(shortlist_k=1),dict(evaluation_key='v1'),dict(smoke_seconds=20),dict(version=1)])
def test_retired_selection_policy_fields_fail_closed(extra):
    with pytest.raises(ValueError):shortlist.policy(dict(version=2,**{k:v for k,v in extra.items() if k!='version'}) if 'version' not in extra else extra)
    assert shortlist.policy(dict(version=2))==dict(version=2)


def test_non_public_or_changed_candidate_facts_are_rejected():
    with pytest.raises(ValueError):selection.shortlist([rec(2,'W',[dict(cand('P',1),hidden_score=1)])])
    with pytest.raises(ValueError):selection.shortlist([rec(2,'W',[cand('P',1)]),rec(3,'W',[cand('P',2)])])
