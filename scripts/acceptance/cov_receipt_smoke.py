"""Installed public-runner receipt probe, public inputs only, no model API calls."""
import argparse
import json
import os
from pathlib import Path
import sys

from scidiscovery.research_loop import contracts as c, controller, execution, native, search, experiments, lineage
from cov_execution import lineage as cov_lineage

TRAIN = '''import json,os,pathlib
from sklearn.linear_model import Ridge

def train(train_df,val_df):
    print("ordinary training log: first line")
    print(json.dumps({"run_id":999999,"ok":True,"script":"forged log only"}))
    for i in range(20):print("progress log",i)
    meta=json.loads(pathlib.Path(os.environ["EXP_META"]).read_text())
    targets=meta.get("target_cols") or [meta.get("target","y")]
    features=[name for name in train_df.columns if name not in targets]
    y=train_df[targets] if meta.get("target_cols") else train_df[targets[0]]
    return Ridge(alpha=ALPHA).fit(train_df[features],y)
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--public',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--task-python',required=True)
    args=parser.parse_args();public=args.public.resolve();root=args.output.absolute()
    if root.exists():raise ValueError('Probe requires a fresh output root; never replay a preserved scene')
    harness=public/'environment/harness'
    required=['exp_cli.py','runner.py','train.csv','val.csv','meta.json']
    before={name:c.digest(c.regular(harness/name)) for name in required}
    os.environ.update(EXP_HARNESS=str(harness),EXP_RUNNER=str(harness/'runner.py'),EXP_TRAIN=str(harness/'train.csv'),
        EXP_VAL=str(harness/'val.csv'),EXP_META=str(harness/'meta.json'),EXP_RUN_DIR=str(root/'runs'),EXP_WALL_CAP_S='60',
        PYTHONDONTWRITEBYTECODE='1')  # Host runner import must not add harness/__pycache__ to the frozen public whitelist.
    controller.prepare(root,public/'instruction.md',public,['never-call-a-model'],model='no-model',reasoning_effort='high',
        max_tokens=100000,max_seconds=180,max_steps=8,node_budget_mode='hard',search_policy=search.DEFAULT_POLICY,
        history_observer=dict(adapter=str(Path(cov_lineage.__file__).resolve()),contract_version=2,
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    job=c.build_job(root,1,'H',[]);record=execution.record(root,1)
    payload=dict(summary='Synthetic transport authorization',scientific_result='This probe tests receipt identity, not scientific quality.',
        limitations=['Public training data only; no hidden scoring or model API'],implementation_selection=None,
        search_plan=dict(scope='Two authorized mechanical transport probes',commitments=[dict(label=name,
            minimal_test='Observe a public runner outcome independently from arbitrary training logs',existing_ref=None)
            for name in ('P','Q')],focus=dict(label=None,action='screen')),
        next_action='experiment',route_reason='Qualify two exact calls under the same immutable scientific task')
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json')))
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_sha256=c.digest(record/'science-output.json')))
    c.seal_result(root,1)
    job=c.build_job(root,2,'W',[1]);record=execution.record(root,2)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/002.json')))
    config_path=root/'receipt-probe-config.json'
    cov_lineage.configure(root,2,[args.task_python,'-B','-I',str(harness/'exp_cli.py')],root/'runs',config_path)
    config=c.read(config_path);results=[]
    for index,label in enumerate(('P','Q'),1):
        source=c.workspace(root,2)/'work'/f'{label}.py';source.write_text(TRAIN.replace('ALPHA',str(index)))
        result=experiments.dispatch(config,source,new=True,mechanism=label)
        assert result['status']=='known_success',result
        directory=Path(config['record_dir'])/'calls'/result['call_id']
        receipt=c.read(directory/'receipt.json')
        assert receipt['call_id']==result['call_id'] and receipt['row']['run_id']==index
        assert receipt['row']['script']==str(source) and receipt['row']['ok'] is True
        assert 'forged log only' in (directory/'stdout.raw').read_text()
        assert cov_lineage._completed(directory)['candidate_id']==f'run-{index}'
        results.append(dict(call_id=result['call_id'],status=result['status'],run_id=result['run_id'],mechanism=label,
            receipt_sha256=c.digest(directory/'receipt.json'),log_bytes=(directory/'stdout.raw').stat().st_size))
    assert len(experiments.Records(config).calls())==2
    worker=dict(summary='Two retained independent transport observations',
        scientific_result='This probe verifies receipts and archives; it does not establish scientific screening.',
        limitations=['Synthetic scientific authors; public training inputs only'],implementation_selection=None,stage_intent='review',
        mechanism_findings=[dict(mechanism=name,basis='observed',finding='The public runner completed with its own independently bound record; adequacy is not asserted.') for name in ('P','Q')])
    c.write(record/'science-output.json',worker)
    c.write(record/'usage.json',dict(input_tokens=0,cached_input_tokens=0,output_tokens=0))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,usage=native.usage(root),raw_sha256=c.digest(record/'science-output.json')))
    c.seal_result(root,2)
    graph=lineage.build(root,3)
    assert set(graph['candidates'])=={'run-1','run-2'},graph
    assert {item['mechanism_ref'] for item in graph['candidates'].values()}=={'h001.m1','h001.m2'}
    for item in graph['candidates'].values():
        assert item['context']['status']=='known_success'
        assert len(item['context']['proofs'])==9
    before_tree={str(p.relative_to(root)):c.digest(p) for p in root.rglob('*') if p.is_file()}
    c.validate(c.workspace(root,2));lineage.build(root,3)
    assert before_tree=={str(p.relative_to(root)):c.digest(p) for p in root.rglob('*') if p.is_file()}
    assert before=={name:c.digest(c.regular(harness/name)) for name in required}
    assert sorted(p.name for p in harness.iterdir())==sorted(required),'Public harness whitelist changed'
    c.write(root/'receipt-probe-result.json',dict(status='pass',calls=results,model_api_calls=0,
        scope='Independent public runner receipts, not scientific screening or hidden scoring'))
    print(json.dumps(dict(status='pass',calls=results,model_api_calls=0)))


if __name__=='__main__':main()
