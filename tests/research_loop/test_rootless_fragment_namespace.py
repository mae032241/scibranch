"""Real bwrap/main/registered-cov path with two fake native turns, zero models."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import budget, contracts as c, controller, execution, lineage, native, search
from cov_execution import lineage as cov_lineage, receipt as cov_receipt
from scidiscovery.research_loop.experiment_client import record_directory
from test_author_closeout_e2e import source_python
from test_worker_execution_hash_free import PUBLIC_CLI,PUBLIC_RUNNER


FAKE_NATIVE=r'''import errno,json,os,pathlib,subprocess,sys
def readonly(path):
 before=path.stat();original=path.read_bytes() if path.is_file() else None
 for action in (lambda:path.chmod(0o700 if path.is_dir() else 0o600),
  lambda:(path/'.write-probe').write_text('changed') if path.is_dir() else path.write_text('changed'),
  lambda:path.rename(path.with_name(path.name+'-moved'))):
  try:action()
  except OSError as error:assert error.errno in (errno.EROFS,errno.EBUSY,errno.EACCES),error
  else:raise AssertionError('Shared input mutation succeeded: '+str(path))
 assert path.stat().st_mode==before.st_mode
 if original is not None:assert path.read_bytes()==original
def shared_inputs(work):
 readonly(work/'inputs/global/objective.md')
 for name in ('retained-experiments','execution-results','retained-implementations'):
  readonly(work/'work'/name)
 for parent in (work,work/'work'):
  try:parent.rename(parent.with_name(parent.name+'-moved'))
  except OSError as error:assert error.errno==errno.EBUSY,error
  else:raise AssertionError('Shared-input ancestor was renamed')
 (work/'work/readonly-view-scratch').write_text('Native scratch remains writable')
 (work/'artifacts/readonly-view-scratch').write_text('Native artifacts remain writable')
args=sys.argv[1:]
if args and args[0]=='sandbox':
 # Fake contract only: real outer bwrap and runner, no provider sandbox qualification.
 assert len(args)==12 and args[:4]==['sandbox','-P','namespace-no-model','-C'] and args[5]=='--'
 assert args[6:9]==[sys.executable,'-B','-I'] and args[9:]==[EXPECTED_RECEIPT,'--science-runner','/opt/exp/scid-invocation.json']
 invocation=json.loads(pathlib.Path(args[11]).read_text())
 assert set(invocation)=={'runner','source','output_line','output'} and invocation['runner']==EXPECTED_RUNNER
 assert pathlib.Path(invocation['source']).parent==pathlib.Path(args[4]) and pathlib.Path(invocation['source']).is_file()
 assert type(invocation['output_line']) is int and invocation['output_line']>0
 assert invocation['output']=='/task-tmp/scid-receipt/row.json'
 shared_inputs(pathlib.Path(args[4]));readonly(pathlib.Path(invocation['source']))
 os.chdir(args[4]);os.execv(args[6],args[6:])
sys.stdin.read()
work=pathlib.Path(args[args.index('-C')+1]);home=pathlib.Path(os.environ['CODEX_HOME'])
root=work.parents[2]
for private in (root/'jobs',root/'settings-identity.json',root/'settings.json',root/'control-ledger',
 root/'native-evidence',root.parent/'private-owner-state'):
 assert not private.exists(), 'Private owner or execution metadata exposed to science view'
task=json.loads((work/'science-task.json').read_text());role=task['role']
assert role in ('H','W','A') and task['global']['objective']=='inputs/global/objective.md'
assert (work/task['global']['objective']).read_text()=='Two synthetic native contexts; zero model requests.'
shared_inputs(work)
search_enabled=bool(task.get('lineage',{}).get('mechanisms',{}).get('authorized_count'))
output=pathlib.Path(args[args.index('--output-last-message')+1]);later='resume' in args or home.name=='context-001'
assert output==pathlib.Path('/task-result/result.pending.json')
assert 'resume' not in args
schema=pathlib.Path(args[args.index('--output-schema')+1])
assert schema==pathlib.Path('/task-result/science-result.schema.json') and json.loads(schema.read_text())['type']=='object'
readonly(schema)
thread='22222222-2222-4222-8222-222222222222' if later else '11111111-1111-4111-8111-111111111111'
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
session=home/'sessions/current.jsonl';session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
if role=='W':
 slot=work/'work/experiment'
 capabilities=subprocess.run([str(work/'bin/exp'),'capabilities'],capture_output=True,text=True)
 assert capabilities.returncode==0,(capabilities.stdout,capabilities.stderr)
 assert any(item['key']=='cov_single_source' for item in json.loads(capabilities.stdout)['contracts'])
 contract=work/'work'/('cov-contract-later.json' if later else 'cov-contract-first.json')
 described=subprocess.run([str(work/'bin/exp'),'describe','--key','cov_single_source','--output',str(contract)],capture_output=True,text=True)
 assert described.returncode==0,(described.stdout,described.stderr)
 assert json.loads(contract.read_text())['experiment_directory'] is False
 status=subprocess.run([str(work/'bin/exp'),'lineage-status'],capture_output=True,text=True)
 assert status.returncode==0,(status.stdout,status.stderr)
 if not later:
  p=work/'payload.py';p.write_text('print("Inert namespace transport fixture")\n')
  cmd=[str(work/'bin/exp'),'run','--script',str(p),'--new-plan']
  rejected_options=[]
  for options,expected_status,expected_error in ((['--experiment-dir',str(work)],'caller_error','invalid_arguments'),(['--','--seed','7'],'known_not_started','caller_error')):
   before=(slot/'submit.json').read_bytes()
   rejected=subprocess.run(cmd+options,capture_output=True,text=True)
   assert rejected.returncode==2,(rejected.stdout,rejected.stderr)
   rejection=json.loads(rejected.stdout)
   assert (rejection['status'],rejection['error'],rejection['accepted'])==(expected_status,expected_error,False),rejection
   assert 'call_id' not in rejection
   if expected_status=='caller_error':
    assert (slot/'submit.json').read_bytes()==before
    reference=None
   else:reference=json.loads((slot/'response.json').read_text())['submission_ref']
   rejected_options.append(dict(result=rejection,submission_ref=reference))
  (work/'work/rejected-caller-options.json').write_text(json.dumps(rejected_options))
  result=subprocess.run(cmd+(['--mechanism','P'] if search_enabled else []),capture_output=True,text=True)
  assert result.returncode==0,(result.stdout,result.stderr)
  reply=json.loads(result.stdout);assert reply['status']=='known_success'
  original=(slot/'response.json').read_bytes();response=json.loads(original)
  assert response['code']==0 and response['result']==reply
  (work/'work/original-response.json').write_bytes(original)
  collected=subprocess.run([str(work/'bin/exp'),'collect','--call-id',reply['call_id']],capture_output=True,text=True)
  assert collected.returncode==0,(collected.stdout,collected.stderr)
  collection=json.loads(collected.stdout)
  assert collection['status']=='collected' and collection['call_id']==reply['call_id'] and collection['outputs']
  for item in collection['outputs']:
   path=pathlib.Path(item['path'])
   assert path.is_relative_to(work/'work/retained-experiments'/reply['call_id']/'outputs') and path.is_file()
   readonly(path)
  shared_inputs(work)
  (work/'work/readonly-collect.json').write_text(collected.stdout)
  if search_enabled:
   rejected=subprocess.run(cmd+['--mechanism','P'],capture_output=True,text=True)
   rejection=json.loads(rejected.stdout)
   assert rejected.returncode!=0 and rejection['status']=='unresolved',(rejected.stdout,rejected.stderr)
   assert rejection['handoff']['action']=='reconcile_or_stop' and 'call_id' not in rejection
   ledger=pathlib.Path(os.environ['EXP_RUN_DIR'])/'results.jsonl'
   assert len(ledger.read_text().splitlines())==1
   rejected_response=json.loads((slot/'response.json').read_text())
   assert rejected_response['code']!=0 and rejected_response['result']==rejection
   (work/'work/breadth-rejection.json').write_text(json.dumps(rejected_response))
   other=subprocess.run(cmd+['--mechanism','Q'],capture_output=True,text=True)
   assert other.returncode==0,(other.stdout,other.stderr)
   assert json.loads(other.stdout)['status']=='known_success'
  (work/'work/original-call.json').write_text(json.dumps(dict(submission_ref=response['submission_ref'])))
 else:
  assert home.name=='context-001'
  assert (work/'work/science-resume.json').is_file()
  original=json.loads((work/'work/original-response.json').read_text())
  assert original['submission_ref']==json.loads((work/'work/original-call.json').read_text())['submission_ref']
  call=original['result']['call_id']
  result=subprocess.run([str(work/'bin/exp'),'query','--call-id',call],capture_output=True,text=True)
  assert result.returncode==0,(result.stdout,result.stderr)
  assert json.loads(result.stdout)['state']=='succeeded'
  (work/'work/second-context-query.json').write_text(result.stdout)
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=1,cached_input_tokens=0,output_tokens=0))),flush=True)
payload=dict(summary='Synthetic namespace contribution',scientific_result='No scientific efficacy was measured.',
 limitations=['Fake native transport, not a model.'],implementation_selection=None)
if role=='W':
 payload['stage_intent']='review' if later else 'continue'
 payload['mechanism_findings']=([dict(mechanism=label,basis='observed',finding='Independent bounded interpretation; fake transport, no measured efficacy.') for label in ('P','Q')] if search_enabled else None)
 if task.get('candidate_nomination_mechanisms'):
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
   reason='The public runner creates only an inert transport artifact; no scientifically suitable model was established.')
   for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])
if role=='H':payload.update(next_action='experiment',route_reason='Discriminate the declared mechanism',
 search_plan=dict(scope='Synthetic current search',commitments=[dict(label=label,minimal_test='One bounded experiment; prioritize competing mechanisms before fine tuning',existing_ref=None) for label in ('P','Q')],focus=dict(label=None,action='screen')))
if role=='A':payload.update(next_action='finish',route_reason='The synthetic combination was observed',candidate_recommendation=None,
 progress_assessment=None,decision_request=None,portfolio_review=None,
 search_review=dict(dispositions=[dict(mechanism_ref=ref,status='screened',reason='One final W was assessed') for ref in ('h001.m1','h001.m2')],next_focus=dict(mechanism_ref=None,action='screen')))
output.write_text(json.dumps(payload))
'''


def prepare_namespace(tmp_path,*,search_enabled=False,fake_source=FAKE_NATIVE,context='new_context',repair=False):
    if shutil.which('bwrap') is None:pytest.skip('Actual namespace unavailable')
    repo=Path(__file__).resolve().parents[2]
    python,scid_python,runtime_paths=source_python(tmp_path,repo)
    (tmp_path/'private-owner-state').write_text('Host-only owner-state canary; no credentials.')
    public=tmp_path/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    (public/'instruction.md').write_text('Two synthetic native contexts; zero model requests.')
    (public/'task.toml').write_text('fixture=true\n');(public/'environment/Dockerfile').write_text('FROM fixture\n')
    (harness/'exp_cli.py').write_text(PUBLIC_CLI);(harness/'runner.py').write_text(PUBLIC_RUNNER)
    for name in ('train.csv','val.csv','meta.json'):(harness/name).write_text('{}')
    manifest=tmp_path/'public-manifest.json'
    c.write(manifest,dict(task_revision='d78f5eb52ad754c5ee9154741af73130a85a65b8',
        public_files=[p.relative_to(public).as_posix() for p in public.rglob('*') if p.is_file()]))
    fake=tmp_path/'fake-codex';fake.write_text('#!'+sys.executable+'\nEXPECTED_RECEIPT='+repr(str(Path(cov_receipt.__file__).resolve()))+
        '\nEXPECTED_RUNNER='+repr(str(harness/'runner.py'))+'\n'+fake_source);fake.chmod(0o555)
    root=tmp_path/'instance';config=tmp_path/'launcher.json';codehost=Path('/usr/bin/true').resolve()
    assert str(repo) not in runtime_paths
    c.write(config,dict(scene_root=str(root),public_root=str(public),public_manifest=str(manifest),
        runs_dir=str(root/'runs'),submission_dir=str(root/'submission'),native=str(fake),
        code_mode_host=dict(path=str(codehost)),task_python=python,scid_python=scid_python,
        runtime_paths=[*runtime_paths,str(Path(cov_receipt.__file__).resolve())],
        model='no-model',reasoning_effort='high',profile='namespace-no-model',cov_receipt_channel='public_runner_main'))
    launch=[scid_python,'-B','-I','-m','cov_execution.launcher','--config',str(config),'--']
    controller.prepare(root,public/'instruction.md',public,launch,
        max_tokens=100000,max_seconds=120,max_steps=8,model='no-model',reasoning_effort='high',search_policy=search.DEFAULT_POLICY if search_enabled else None,
        history_observer=dict(adapter=str(Path(cov_lineage.__file__).resolve()),contract_version=2,
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')) if search_enabled else None,
        node_budget_policy={role:dict(max_observed_tokens=10000,max_seconds=60) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=repair),
        worker_fragment_policy=dict(context=context))
    c.write(root/'host-started.json',budget.clock_stamp());budget.transition(root,'advance','Namespace zero-model transport probe')
    return root


def execute_namespace(root,step):
    origin=execution.origin(root,step)
    originals={path:path.read_bytes() for path in (root/'jobs'/f'{step:03d}.json',root/'settings.json',
        root/'settings-identity.json',origin/'science-result.schema.json')}
    try:
        result=native.execute_native(root,step)
        assert {path:path.read_bytes() for path in originals}==originals
        return result
    except (ValueError,RuntimeError) as error:
        diagnostics={}
        for record in (origin,origin/'continuations/001'):
            for name in ('native.stderr','runner.stderr','native.jsonl','native-outcome.json','result.pending.json','science-output.json'):
                path=record/name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(max(0,path.stat().st_size-8192))
                        diagnostics[str(path.relative_to(root))]=stream.read(8192).decode(errors='replace')
        calls=record_directory(root,step)/'calls'
        if calls.is_dir():
            for call in sorted(calls.iterdir())[-3:]:
                if not call.is_dir() or call.is_symlink():continue
                for name in ('stdout.raw','stderr.raw','terminal.json','submission.json','completed.json',
                    'engineering-check.intent.json','engineering-check.process.json','engineering-check.outcome.json'):
                    path=call/name
                    if path.is_file():
                        with path.open('rb') as stream:
                            stream.seek(max(0,path.stat().st_size-16384))
                            diagnostics[str(path.relative_to(root))]=stream.read(16384).decode(errors='replace')
        pytest.fail(json.dumps(dict(error=str(error),diagnostics=diagnostics),ensure_ascii=False,indent=2))


def assert_public_submission(root,step):
    work=c.workspace(root,step);records=record_directory(root,step)
    response_path=work/'work/original-response.json';response=c.read(response_path)
    reference=c.read(work/'work/original-call.json')['submission_ref']
    assert response['submission_ref']==reference
    claim=records.parent/'worker-submissions'/reference
    assert response_path.read_bytes()==(claim/'response.json').read_bytes()
    assert c.read(claim/'binding.json')['attempt']==0
    call=response['result']['call_id'];original=records/'calls'/call
    assert c.read(original/'accepted.json')['native_attempt']['number']==0
    evidence=c.read(claim/'outcome.json')['calls']
    assert len(evidence)==1 and evidence[0]['call_id']==call
    assert evidence[0]['accepted']==c.file_ref(original/'accepted.json')
    assert evidence[0]['completed']==c.file_ref(original/'completed.json')
    for rejected in c.read(work/'work/rejected-caller-options.json'):
        assert rejected['result']['accepted'] is False and 'call_id' not in rejected['result']
        if rejected['submission_ref'] is not None:
            rejected_claim=records.parent/'worker-submissions'/rejected['submission_ref']
            assert c.read(rejected_claim/'outcome.json')['calls']==[]


@pytest.mark.process_e2e
@pytest.mark.parametrize('search_enabled',[False,True],ids=['no-search','sealed-h-search-w-a'])
def test_actual_launcher_two_new_contexts_keep_original_registered_call(tmp_path,search_enabled):
    root=prepare_namespace(tmp_path,search_enabled=search_enabled)
    if search_enabled:
        c.build_job(root,1,'H',[]);execute_namespace(root,1)
        assert 'exploration_order_version' not in c.settings(root)
        assert c.validate(c.workspace(root,1))['search_plan']['commitments']
    step=2 if search_enabled else 1
    job=c.build_job(root,step,'W',[1] if search_enabled else [])
    if search_enabled:
        assert job['search']['focus']['mechanism_ref']=='h001.m1'
        assert lineage.build(root,step)['mechanisms']==search.project(root,step)['mechanisms']
    execute_namespace(root,step)
    chain=execution.records(root,step)
    assert len(chain)==2 and chain[1].name=='001'
    assert c.read(chain[0]/'science-output.json')['stage_intent']=='continue'
    assert c.read(chain[1]/'science-output.json')['stage_intent']=='review'
    assert c.read(chain[0]/'native-outcome.json')['thread_id']!=c.read(chain[1]/'native-outcome.json')['thread_id']
    assert native.usage(root)['observed_tokens']==(3 if search_enabled else 2)
    first=c.read(chain[0]/'experiment-config.json');second=c.read(chain[1]/'experiment-config.json')
    assert first['scope']==second['scope'] and first['native_attempt']!=second['native_attempt']
    work=c.workspace(root,step)
    assert len(list((record_directory(root,step)/'calls').glob('*/accepted.json')))==(2 if search_enabled else 1)
    assert_public_submission(root,step)
    assert len(c.read(work/'work/rejected-caller-options.json'))==2
    collection=c.read(work/'work/readonly-collect.json')
    assert collection['status']=='collected' and collection['outputs']
    assert all(Path(item['path']).is_file() for item in collection['outputs'])
    assert c.read(work/'work/second-context-query.json')['state']=='succeeded'
    assert len(list((root/'jobs').glob('*.json')))==step
    assert c.validate(work)['stage_intent']=='review'
    if search_enabled:
        rejected=c.read(work/'work/breadth-rejection.json')
        claim=record_directory(root,step).parent/'worker-submissions'/rejected['submission_ref']
        assert c.read(claim/'outcome.json')['calls']==[]
        assert len((root/'runs/results.jsonl').read_text().splitlines())==2
        assert first['scope']['mechanism_ref'] is None
        c.build_job(root,3,'A',[1,2]);execute_namespace(root,3)
        assert c.validate(c.workspace(root,3))['next_action']=='finish'
        state=search.project(root,4);graph=lineage.build(root,4)
        assert graph['mechanisms']==state['mechanisms']
        assert all(row['status']=='screened' for row in state['mechanisms'].values())
        assert len(state['order']['seen_w'])==1 and state['order']['remaining']==[]
        assert len(graph['candidates'])==2
        assert {item['mechanism_ref'] for item in graph['candidates'].values()}=={'h001.m1','h001.m2'}
        assert native.usage(root)['observed_tokens']==4
