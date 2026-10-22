"""Plugin v2 exact original relations; no models and no default-view rewrite."""
import hashlib
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, cov_lineage as cov, execution, history, lineage, experiments
from test_history_handoff import finish
from execution_fixtures import execution_registry

BACKEND = '''import json,os,pathlib,sys
runs=pathlib.Path(sys.argv[1]);runs.mkdir(exist_ok=True)
script=pathlib.Path(sys.argv[sys.argv.index('--script')+1]).resolve()
ledger=runs/'results.jsonl';rows=ledger.read_text().splitlines() if ledger.exists() else []
n=len(rows)+1;ok=script.read_text()!='failed'
row=dict(run_id=n,script=str(script),started_at=123,metric='fixture',ok=ok)
if ok:
 model=runs/f'model_run{n}.pkl';model.write_bytes(b'not a pickle; mechanical fixture')
 row.update(val_score=.5,val_acc=.5,model_path=str(model),wall_seconds=.01)
else:row.update(error='Declared fixture failure',wall_seconds=.01)
with ledger.open('a') as f:f.write(json.dumps(row)+'\\n')
if os.environ.get('SCID_COV_RECEIPT_PATH'):
 envelope=dict(version=1,call_id=os.environ['SCID_COV_CALL_ID'],accepted_sha256=os.environ['SCID_COV_ACCEPTED_SHA256'],
  source_sha256=os.environ['SCID_COV_SOURCE_SHA256'],row=row)
 with open(os.environ['SCID_COV_RECEIPT_PATH'],'x') as f:json.dump(envelope,f)
print(json.dumps(row,indent=2));sys.exit(0 if ok else 1)
'''


def scene(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Test exact scientific lineage mechanics')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=10000,max_seconds=120,max_steps=8,
        model='no-model',reasoning_effort='high',search_policy=None,
        history_observer=dict(adapter=str(Path(cov.__file__).resolve()),contract_version=2,
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    finish(root,1,'H',[],action='experiment')
    c.build_job(root,2,'W',[1]);record=c.host_record(c.workspace(root,2))
    intent=dict(task_sha256=c.digest(root/'jobs/002.json'))
    if c.settings(root).get('node_budget_policy') is not None:intent['budget_slice']=budget.accept_node(root,c.read(root/'jobs/002.json'))
    c.write(record/'native-intent.json',intent)
    backend=tmp_path/'exp.py';backend.write_text(BACKEND)
    output=tmp_path/'config.json'
    cov.configure(root,2,[sys.executable,str(backend),str(root/'runs')],root/'runs',output,receipt_channel='backend_file')
    config,digest=cov._config(output)
    return root,config,digest


@pytest.mark.process_e2e
def test_real_calls_same_node_branch_return_and_readonly_projection(tmp_path):
    root,config,digest=scene(tmp_path);script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    p=cov.run(config,digest,script,new=True)
    q=cov.run(config,digest,script,parent=p['candidate_id'])
    r=cov.run(config,digest,script,parent=p['candidate_id'])
    assert all(row['status']=='known_success' for row in (p,q,r))
    assert q['parent']['source']=='same_node_completed_call'
    before={path:path.read_bytes() for path in Path(config['record_dir']).rglob('*') if path.is_file()}
    assert len(cov.status(config))==3
    assert all(path.read_bytes()==data for path,data in before.items())
    finish(root,2,'W',[1])
    record=execution.origin(root,2);seal=c.read(record/'result-identity.json')
    observed=c.read(root/seal['observations']['path'])
    assert observed['binding']['contract_version']==2 and len(observed['contexts'])==3
    view=lineage.build(root,3)
    edges={(edge['subject'],edge['target']) for edge in view['relations'] if edge['kind']=='derivation'}
    assert edges=={(q['candidate_id'],p['candidate_id']),(r['candidate_id'],p['candidate_id'])}
    assert view['candidates'][p['candidate_id']]['derivation']=='declared_root'
    assert all(item['support']=='unknown' for item in view['candidates'].values())
    originals={path:path.read_bytes() for path in (root/'jobs/002.json',c.workspace(root,2)/'science-task.json',record/'science-output.json',record/'result-identity.json')}
    for path in (root/'host-history/nodes').glob('*.json'):path.unlink()
    assert lineage.build(root,3)==view
    assert all(path.read_bytes()==data for path,data in originals.items())


@pytest.mark.process_e2e
def test_context_scope_and_asset_tamper_are_hard_not_optional(tmp_path):
    root,config,digest=scene(tmp_path);script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    result=cov.run(config,digest,script,new=True)
    request=dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config'],known_candidates=[],known_contexts=[])
    observed=cov.observe(request)
    job=c.read(root/'jobs/002.json');wrong=json.loads(json.dumps(observed['contexts']))
    wrong[0]['scope']['execution_key']='0'*64
    with pytest.raises(history.ObserverIntegrityError):lineage.contexts(root,job,wrong,lambda path:history._proof(root,path))
    path=Path(result['model']['path']);path.chmod(0o600);path.write_bytes(b'tampered')
    with pytest.raises(cov.IntegrityError):cov.observe(request)
    with pytest.raises(history.ObserverIntegrityError):finish(root,2,'W',[1])
    assert not (execution.origin(root,2)/'result-identity.json').exists()


def test_v1_existing_contract_bytes_and_relations_remain_unknown(tmp_path):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Legacy science')
    root=tmp_path/'instance';controller.prepare(root,objective,public,['fake'],max_tokens=1000,max_seconds=60,max_steps=4,
        model='fake',reasoning_effort='high',search_policy=None)
    finish(root,1,'H',[],action='wait')
    original={p:p.read_bytes() for p in (root/'jobs/001.json',c.workspace(root,1)/'science-task.json',execution.origin(root,1)/'result-identity.json')}
    assert c.validate(c.workspace(root,1))['next_action']=='wait'
    assert lineage.build(root,2)['relations']==[]
    assert all(p.read_bytes()==data for p,data in original.items())
    assert 'contract_version' not in history.observer_config(dict(adapter=str(Path(cov.__file__).resolve()),config={}))


@pytest.mark.process_e2e
def test_sealed_parent_exact_completion_is_distinct_from_implementation_reuse(tmp_path):
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    parent=cov.run(config,digest,script,new=True)
    finish(root,2,'W',[1],files={'retained.py':'Scientific implementation S'})
    # A's implementation handoff remains separate from the declared experiment parent.
    finish(root,3,'A',[2],selection=['W002-I001'],action='continue_w')
    c.build_job(root,4,'W',[3])
    c.write(execution.origin(root,4)/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/004.json')))
    output=tmp_path/'next.json'
    cov.configure(root,4,config['run_argv'],root/'runs',output,receipt_channel='backend_file')
    next_config,next_sha=cov._config(output)
    script=c.workspace(root,4)/'work/train.py';script.write_text('success')
    child=cov.run(next_config,next_sha,script,parent=parent['candidate_id'])
    assert child['parent']['source']=='sealed'
    finish(root,4,'W',[3])
    view=lineage.build(root,5)
    assert any(e['kind']=='derivation' and e['subject']==child['candidate_id'] and e['target']==parent['candidate_id'] for e in view['relations'])
    assert any(e['kind']=='reuse' and e['subject']['node']==3 and e['target']['node']==2 for e in view['relations'])
    assert view['candidates'][child['candidate_id']]['support']=='unknown'


@pytest.mark.process_e2e
@pytest.mark.parametrize('wrong', ['model', 'future'])
def test_declared_parent_binding_cannot_borrow_another_or_future_original(tmp_path,wrong):
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    parent=cov.run(config,digest,script,new=True)
    child=cov.run(config,digest,script,parent=parent['candidate_id'])
    directory=Path(config['record_dir'])/'calls'/child['call_id']
    accepted=json.loads((directory/'accepted.json').read_text())
    if wrong=='model':
        accepted['parent_binding']['model_sha256']='0'*64
        (directory/'accepted.json').write_bytes(cov._bytes(accepted))
        # Construct a transport-consistent fixture with an intentionally wrong
        # scientific parent binding; independent receipt rejection is tested
        # separately in test_cov_lineage's receipt.json tamper control.
        receipt_path=directory/'receipt.json'
        receipt=json.loads(receipt_path.read_text())
        receipt['accepted_sha256']=hashlib.sha256(experiments.encoded(accepted)).hexdigest()
        receipt_path.write_bytes(cov._bytes(receipt))
        completed=json.loads((directory/'completed.json').read_text())
        completed['proofs']=[cov._proof(Path(proof['path'])) if Path(proof['path']).name in {'accepted.json','receipt.json'} else proof
            for proof in completed['proofs']]
        (directory/'completed.json').write_bytes(cov._bytes(completed))
    request=dict(root=str(root),step=2,config=c.settings(root)['history_observer']['config'],known_candidates=[],known_contexts=[])
    observed=cov.observe(request)
    items=observed['contexts']
    assert all(item['status']=='known_success' for item in items)
    if wrong=='future':items.reverse()
    expected='Declared parent completion/artifact/source differs' if wrong=='model' else 'Declared parent has no earlier completed original'
    with pytest.raises(history.ObserverIntegrityError,match=expected):
        lineage.contexts(root,c.read(root/'jobs/002.json'),items,lambda path:history._proof(root,path))


@pytest.mark.process_e2e
@pytest.mark.parametrize('field,value', [('candidate_id','run-999'),('run_id',999),('status','known_failed')])
def test_completed_cannot_change_identity_or_outcome_while_preserving_proofs(tmp_path,field,value):
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    result=cov.run(config,digest,script,new=True)
    path=Path(config['record_dir'])/'calls'/result['call_id']/'completed.json'
    completed=json.loads(path.read_text());completed[field]=value
    path.write_bytes(cov._bytes(completed))
    originals={item:item.read_bytes() for item in (Path(config['record_dir'])/'calls').rglob('*') if item.is_file()}
    with pytest.raises(cov.IntegrityError):cov.status(config)
    # The already-existing observation receipt rejects changed completed bytes
    # before the optional domain/history observer is reached.
    with pytest.raises(experiments.IntegrityError,match='^Execution observation original changed$'):
        finish(root,2,'W',[1])
    assert all(item.read_bytes()==data for item,data in originals.items())
    assert not (execution.origin(root,2)/'result-identity.json').exists()
    assert not (root/'jobs/003.json').exists()


@pytest.mark.process_e2e
def test_completed_model_cannot_be_rebound_to_another_successful_call(tmp_path):
    root,config,digest=scene(tmp_path)
    script=c.workspace(root,2)/'work/train.py';script.write_text('success')
    first=cov.run(config,digest,script,new=True)
    second=cov.run(config,digest,script,new=True)
    path=Path(config['record_dir'])/'calls'/first['call_id']/'completed.json'
    completed=json.loads(path.read_text());completed['model']=second['model']
    # Even adding the other valid proof cannot turn it into this call's artifact.
    completed['proofs'].append(second['model']);path.write_bytes(cov._bytes(completed))
    originals={item:item.read_bytes() for item in (Path(config['record_dir'])/'calls').rglob('*') if item.is_file()}
    with pytest.raises(cov.IntegrityError):cov.status(config)
    # The already-existing observation receipt rejects changed completed bytes
    # before the optional domain/history observer is reached.
    with pytest.raises(experiments.IntegrityError,match='^Execution observation original changed$'):
        finish(root,2,'W',[1])
    assert all(item.read_bytes()==data for item,data in originals.items())
    assert not (execution.origin(root,2)/'result-identity.json').exists()
    assert not (root/'jobs/003.json').exists()


@pytest.mark.process_e2e
@pytest.mark.parametrize('version', [1,2])
def test_integrity_sentinel_is_v2_only_and_preserves_v1_optional_failure(tmp_path,version):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Versioned observation failures')
    adapter=tmp_path/'observer.py';adapter.write_text('print(\'{"integrity_error":"fixture original changed"}\')\n')
    descriptor=dict(adapter=str(adapter),config={})
    if version==2:descriptor['contract_version']=2
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fake'],max_tokens=1000,max_seconds=60,max_steps=4,
        model='fake',reasoning_effort='high',search_policy=None,history_observer=descriptor)
    if version==2:
        with pytest.raises(history.ObserverIntegrityError):finish(root,1,'H',[],action='wait')
        assert not (execution.origin(root,1)/'result-identity.json').exists()
    else:
        finish(root,1,'H',[],action='wait')
        seal=c.read(execution.origin(root,1)/'result-identity.json')
        assert c.read(root/seal['observations']['path'])['coverage']['status']=='unavailable'
        assert c.validate(c.workspace(root,1))['next_action']=='wait'
