"""Real CLI transport freezes output repair without changing native configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import contracts as c, continuation, execution, experiment_client
from execution_fixtures import execution_registry
from scidiscovery.research_loop import search as search_contract, worker_delivery

pytestmark=pytest.mark.process_e2e


NATIVE = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
config=json.loads(pathlib.Path(__file__).resolve().with_name('launcher-config.json').read_text())
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE']
home=pathlib.Path(os.environ['CODEX_HOME'])
if config['default_home']:
 assert str(home)==config['home']
 assert (home/'config.toml').read_text()==config['configuration']
 assert (home/'auth.json').is_file()
thread='11111111-2222-4333-8444-'+('%012d'%int(work.name))
with (work/'work/launches').open('a') as stream:stream.write('resume\n' if resume else 'exec\n')
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
if not resume:
 session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
 session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
 (work/'work/completed-observation').write_text('one retained observation')
else:
 assert thread in args
 assert prompt.strip()
 assert (work/'work/completed-observation').read_text()=='one retained observation'
payload=dict(summary='Deterministic CLI transport',scientific_result='No scientific qualification is asserted.',
 limitations=['Only native communication is simulated.'],implementation_selection=None)
completed_work=config['fault'].startswith('completed-')
author_repair=config['fault'].startswith('author-')
nomination=config['fault'].startswith('nomination-') or completed_work or author_repair
if role=='H':
 payload.update(next_action='analyze',route_reason='Review retained evidence',search_plan=None)
 if config['fault']=='incomplete_w' or nomination:
  payload.update(next_action='experiment',search_plan=dict(scope='Declared bounded fixture mechanisms',
   commitments=[dict(label='Fixture mechanism',minimal_test='Read a bounded observation',existing_ref=None)],
   focus=dict(index=None,action='screen')))
  if completed_work:
   payload['search_plan']['commitments'].append(dict(label='Second fixture mechanism',minimal_test='Produce a different bounded observation',existing_ref=None))
   if 'caller-improve' in config['fault']:payload['search_plan']['focus']=dict(index=1,action='improve')
 if author_repair:
  if resume:
   assert 'Fix diagnosed JSON/layout only' in prompt
   (work/'work/author-repair-prompt.txt').write_text(prompt)
  if config['fault'].startswith('author-focus'):
   payload['search_plan']['focus']['index']=999 if not resume or config['fault']=='author-focus-repeat' else 1
  if config['fault']=='author-selection' and not resume:payload['implementation_selection']=['W999-I001']
 if config['fault'] in {'caller-no-search','caller-compile-no-search','caller-command-no-search'}:payload['next_action']='experiment'
elif role=='W':
 payload.update(stage_intent='decision',mechanism_findings=None)
 task=json.loads((work/'science-task.json').read_text())
 mechanisms=task.get('candidate_nomination_mechanisms',[])
 if config['fault'] in {'caller-no-search','caller-compile-no-search','caller-command-no-search'}:
  import subprocess
  from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
  directory=work/'experiments/no-search';directory.mkdir(parents=True,exist_ok=True)
  code=work/'code';code.mkdir(exist_ok=True);script=code/'run.py'
  script.write_text('from pathlib import Path\nPath("artifacts/result.json").write_text('+repr(json.dumps(dict(version=1,metrics=[],outputs=[])))+')\n')
  arguments=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge','--submission-directory',str(work/SUBMISSION_DIRECTORY),
   'run','--experiment-dir',str(directory),'--script',str(script)]
  if config['fault']=='caller-compile-no-search' and not resume:
   aliases=work/'code/scientific-files.json';aliases.write_text('{"broken":')
   arguments=arguments[:5]+['compile','--implementation',str(script),'--scientific-files',str(aliases),'--output',str(work/'code/compiled.json')]
  elif config['fault']=='caller-command-no-search' and not resume:arguments=arguments[:5]+['rnu']
  elif not resume:arguments+=['--misspelled-option','value']
  outcome=subprocess.run(arguments,capture_output=True,text=True)
  response=json.loads(outcome.stdout)
  if resume:
   if config['fault']=='caller-compile-no-search':
    assert 'unaccepted compile request' in prompt and 'JSON' in prompt
    (work/'work/caller-repair-prompt.txt').write_text(prompt)
   if config['fault']=='caller-command-no-search':
    assert 'unaccepted rnu request' in prompt
    (work/'work/caller-repair-prompt.txt').write_text(prompt)
   assert outcome.returncode==0 and response['status']=='known_success',(outcome.stdout,outcome.stderr)
   (work/'work/only-accepted-call.json').write_text(json.dumps(response))
  else:
   assert outcome.returncode==2 and response['status']=='caller_error' and response['accepted'] is False,(outcome.stdout,outcome.stderr)
   (work/'work/caller-refusal.json').write_text(json.dumps(response));payload['stage_intent']='decision'
 if config['fault']=='incomplete_w':
  payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
   reason='This fixture supplies no scientific model suitability claim.') for row in mechanisms if row['status']!='waived'])
 if nomination:
  locator_file=work/'work/retained-candidate-locator.json'
  if not resume:
   import subprocess
   from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
   directory=work/'experiments/retained';directory.mkdir(parents=True)
   artifacts=['artifacts/model-'+str(i)+'.bin' for i in range(4)]
   declared=dict(version=1,metrics=[],outputs=[dict(path=path) for path in artifacts],candidates=[dict(artifact=path) for path in artifacts])
   code=work/'code';code.mkdir(exist_ok=True);script=code/'run.py'
   script.write_text('from pathlib import Path\nfor i,path in enumerate('+repr(artifacts)+'): Path(path).write_text("original model "+str(i))\nPath("artifacts/result.json").write_text('+repr(json.dumps(declared))+')\n')
   outcome=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
    '--submission-directory',str(work/SUBMISSION_DIRECTORY),'run','--experiment-dir',str(directory),
    '--script',str(script),'--mechanism','1'],capture_output=True,text=True)
   assert outcome.returncode==0,(outcome.stdout,outcome.stderr)
   result=json.loads(outcome.stdout);assert result['status']=='known_success'
   locator_file.write_text(json.dumps(dict(call_id=result['call_id'],artifact=artifacts[0])))
  locator=json.loads(locator_file.read_text())
  if config['fault'].startswith('nomination-layout'):
   import shutil
   output_name='model-0.bin'  # The fixture's public output descriptor; candidate artifact retains its own identity.
   original=work/'work/retained-experiments'/locator['call_id']/'outputs'/output_name
   copied=work/'artifacts/experiments'/locator['call_id']/'artifacts'/output_name
   if not resume:
    copied.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,copied)
    retained_bytes=original.read_bytes()
    assert not copied.samefile(original)
    copied.write_bytes(retained_bytes+b'\nConflicting authored fixture publication.\n')
    assert copied.read_bytes()!=retained_bytes and original.read_bytes()==retained_bytes
   else:
    assert 'Fix diagnosed JSON/layout only' in prompt and 'deliverable_path_collision' in prompt
    assert 'Remove redundant copies' in prompt
    if config['fault']!='nomination-layout-repeat':copied.unlink()
  if resume and config.get('output_repair_probe'):
   import subprocess
   from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
   bridge=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
    '--submission-directory',str(work/SUBMISSION_DIRECTORY)]
   def probe(name,arguments):
    outcome=subprocess.run([*bridge,*arguments],capture_output=True,text=True)
    (work/('work/output-repair-'+name+'.json')).write_text(json.dumps(
     dict(code=outcome.returncode,result=json.loads(outcome.stdout))))
   probe('query',['query','--call-id',locator['call_id']])
   probe('collect',['collect','--call-id',locator['call_id']])
   directory=work/'experiments/repair-probe';directory.mkdir(parents=True)
   script=work/'code/repair-probe.py'
   declared=dict(version=1,metrics=[],outputs=[dict(path='artifacts/new-managed-effect.txt')])
   script.write_text('from pathlib import Path\nPath("artifacts/new-managed-effect.txt").write_text("unexpected new effect")\nPath('+repr(str(work/'artifacts/output-repair-new-effect.txt'))+').write_text("unexpected new effect")\nPath("artifacts/result.json").write_text('+repr(json.dumps(declared))+')\n')
   probe('run',['run','--experiment-dir',str(directory),'--script',str(script),
    '--mechanism','1'])
  payload['mechanism_findings']=[dict(mechanism='h001.m1',basis='observed',finding='One retained fixture observation; target suitability remains for A.')]
  value=dict(representatives=[dict(mechanism_ref=mechanisms[0]['mechanism_ref'],candidate=locator,reason='A retained model for this scientific mechanism.')],extras=[],gaps=[])
  if completed_work:
   value['gaps']=[dict(mechanism_ref=row['mechanism_ref'],reason='No candidate suitability is asserted for the second bounded observation.') for row in mechanisms[1:]]
  if resume:
   assert locator_file.is_file()
   payload['candidate_nominations']=value
  elif completed_work or config['fault'].startswith('nomination-layout'):payload['candidate_nominations']=value
  elif config['fault']=='nomination-extra':
   value['extras']=[dict(candidate=dict(call_id=locator['call_id'],artifact='artifacts/model-'+str(i)+'.bin'),reason='Distinct scientific alternative.') for i in (1,2,3)]
   payload['candidate_nominations']=value
  elif config['fault']=='nomination-locator':
   value['representatives'][0]['candidate']=dict(call_id='unregistered-call',artifact=locator['artifact'])
   payload['candidate_nominations']=value
  if completed_work:
   if not resume or config['fault']=='completed-repeat' or (config['fault']=='completed-missing-output' and (work/'work/launches').read_text().count('resume')==1):
    payload['mechanism_findings']=None
   if config['fault']=='completed-blocked':payload['mechanism_findings']=[dict(mechanism='h001.m1',basis='observed',finding='The first observation is retained.'),dict(mechanism='h001.m2',basis='blocked',finding='The second assigned comparison requires unavailable scientific input.')]
   if config['fault']=='completed-decision':payload['stage_intent']='decision'
   if config['fault']=='completed-unresolved':
    payload['mechanism_findings']='An explicit narrative finding; the second target input is unavailable.'
    payload['stage_intent']='decision'
   if resume and config['fault'] in {'completed-findings','completed-missing-output'} and not (config['fault']=='completed-missing-output' and (work/'work/launches').read_text().count('resume')==1):
    payload['stage_intent']='decision'
    payload['scientific_result']='The first original observation is retained. The second assigned target input is unavailable; A must decide the remaining bounded scope.'
   if config['fault']=='completed-experiment' and resume:
    import subprocess
    from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
    second=work/'work/second-original-call.json';assert not second.exists()
    directory=work/'experiments/second';directory.mkdir(parents=True)
    script=work/'code/second.py'
    script.write_text('from pathlib import Path\nPath("artifacts/analysis.txt").write_text("A different assigned bounded observation")\nPath("artifacts/result.json").write_text('+repr(json.dumps(dict(version=1,metrics=[],outputs=[dict(path='artifacts/analysis.txt')])))+')\n')
    outcome=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
     '--submission-directory',str(work/SUBMISSION_DIRECTORY),'run','--experiment-dir',str(directory),
     '--script',str(script),'--mechanism','2'],capture_output=True,text=True)
    assert outcome.returncode==0,(outcome.stdout,outcome.stderr)
    result=json.loads(outcome.stdout);assert result['status']=='known_success'
    second.write_text(json.dumps(dict(call_id=result['call_id'])))
    payload['mechanism_findings'].append(dict(mechanism='h001.m2',basis='observed',finding='A different retained assigned observation; scientific suitability remains for A.'))
   if config['fault'].startswith('completed-caller-'):
    payload['mechanism_findings']=[dict(mechanism='h001.m1',basis='observed',finding='The first original observation is retained.')]
    import subprocess
    from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
    bridge=[sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge','--submission-directory',str(work/SUBMISSION_DIRECTORY)]
    directory=work/'experiments/second';directory.mkdir(parents=True,exist_ok=True)
    script=work/'code/second.py'
    script.write_text('from pathlib import Path\nPath("artifacts/analysis.txt").write_text("A different assigned bounded observation")\nPath("artifacts/result.json").write_text('+repr(json.dumps(dict(version=1,metrics=[],outputs=[dict(path='artifacts/analysis.txt')])))+')\n')
    requested_mechanism='1' if 'caller-improve' in config['fault'] else '2'
    requested_ref='h001.m1' if 'caller-improve' in config['fault'] else 'h001.m2'
    arguments=['run','--experiment-dir',str(directory),'--script',str(script),'--mechanism',requested_mechanism]
    if not resume:
     rejected=subprocess.run([*bridge,*arguments,'--misspelled-option','value'],capture_output=True,text=True)
     response=json.loads(rejected.stdout)
     assert rejected.returncode==2 and response['status']=='caller_error' and response['accepted'] is False,(rejected.stdout,rejected.stderr)
     (work/'work/caller-refusal.json').write_text(json.dumps(response))
    if resume or config['fault']=='completed-caller-corrected':
     if resume:
      assert 'worker_delivery_incomplete' in prompt and requested_ref in prompt
      (work/'work/caller-repair-prompt.txt').write_text(prompt)
     second=work/'work/second-original-call.json';assert not second.exists()
     accepted=subprocess.run([*bridge,*arguments],capture_output=True,text=True)
     response=json.loads(accepted.stdout)
     assert accepted.returncode==0 and response['status']=='known_success',(accepted.stdout,accepted.stderr)
     second.write_text(json.dumps(dict(call_id=response['call_id'])))
     payload['mechanism_findings'].append(dict(mechanism=requested_ref,basis='observed',finding='The corrected request yielded the retained assigned observation.'))
    elif config['fault']=='completed-caller-blocked':
     payload['mechanism_findings'].append(dict(mechanism='h001.m2',basis='blocked',finding='The command options were refused before this required observation started.'))
    else:payload['stage_intent']='decision'
 if config.get('output_repair_probe') and not completed_work and not resume and not config['fault'].startswith('nomination-layout'):payload.pop('summary')
else:
 assert role=='A'
 payload.update(next_action='finish',route_reason='The measured scientific objective remains unresolved.',
  candidate_recommendation='declared-fixture-choice',search_review=None,progress_assessment=None,decision_request=None)
 task=json.loads((work/'science-task.json').read_text())
 references=task.get('final_coverage_mechanisms',[])
 if task.get('portfolio_assessment',{}).get('required_for_completed_w'):
  payload['portfolio_review']=dict(discriminating_work=None,main_line=references[0] if references else None,
   reason='The exact first W observation does not qualify target performance. Competing target comparisons require missing input; defer them until that input arrives and revisit before further investment.')
 if references:
  payload['final_coverage']=[dict(mechanism_ref=ref,status='unexplored',
   reason='This synthetic transport fixture supplies no scientific coverage qualification; target behavior and remaining discriminating questions are unanswered.') for ref in references]
 if config['fault'] in {'missing','repeat','unknown','failed'} and (not resume or config['fault']=='repeat'):
  payload.pop('summary')
 if config['fault']=='incomplete_w':
  task=json.loads((work/'science-task.json').read_text())
  mechanisms=json.loads((work/task['lineage']['indexes']['mechanisms']).read_text())
  payload['search_review']=dict(dispositions=[dict(mechanism_ref=mechanisms['mechanisms'][0]['mechanism_ref'],
   status='pending',reason='The W contribution lacks the requested scientific findings.')],next_focus=None)
 if config.get('a_screened'):
  task=json.loads((work/'science-task.json').read_text())
  mechanisms=json.loads((work/task['lineage']['indexes']['mechanisms']).read_text())
  payload['search_review']=dict(dispositions=[dict(mechanism_ref=mechanisms['mechanisms'][0]['mechanism_ref'],
   status='screened',reason='The exact retained W observation supports this bounded scientific assessment.')],next_focus=None)
if not (config['fault']=='completed-missing-output' and role=='W' and not resume):
 pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
if (config['fault']!='unknown' and config['fault']!='nomination-unknown') or role=='H':
 tokens=config['layout_input_tokens'] if role=='W' and config['fault'].startswith('nomination-layout') and not resume else 10
 print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=tokens,cached_input_tokens=3,output_tokens=4))),flush=True)
raise SystemExit(7 if config['fault']=='failed' and role=='A' else 0)
'''


def cli(root,*arguments,env,trace_timeout=False,timeout=40):
    entry=[sys.executable,'-m','scidiscovery.research_loop.cli']
    if trace_timeout:
        # The same CLI under a bounded diagnostic wrapper; no timeout increase.
        entry=[sys.executable,'-c','''import runpy,sys,traceback
from scidiscovery.research_loop import native
original_wait=native.wait_native
def traced_wait(*args,**kwargs):
 try:return original_wait(*args,**kwargs)
 except RuntimeError as error:
  if type(error) is RuntimeError:traceback.print_exc(limit=40)
  raise
native.wait_native=traced_wait
sys.argv=["scid-research",*sys.argv[1:]]
runpy.run_module("scidiscovery.research_loop.cli",run_name="__main__")''']
    try:
        completed=subprocess.run([*entry,*arguments,'--root',str(root)],
            env=env,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired as error:
        stderr=error.stderr or b''
        if isinstance(stderr,bytes):stderr=stderr.decode('utf-8',errors='replace')
        print(nomination_diagnostic(root,dict(status='cli_timeout',stderr=stderr[-16384:]),fault='cli-timeout'))
        raise
    # CLI permission/admission rejections remain structured JSON too.
    if trace_timeout and completed.stderr:
        print(nomination_diagnostic(root,dict(status='cli_host_trace',returncode=completed.returncode,
            stderr=completed.stderr[-24576:]),fault='cli-host-trace'))
    if not completed.stdout.strip():
        print(nomination_diagnostic(root,dict(status='cli_empty_output',returncode=completed.returncode,
            stderr=completed.stderr[-16384:]),fault='cli-empty-output'))
    assert completed.stdout.strip(),(completed.returncode,completed.stderr)
    return json.loads(completed.stdout)


def nomination_diagnostic(root, result, *, fault, fragment=False):
    """Only the failing fake W's exact transport files, at most 128 KiB."""
    record=execution.origin(root,2)
    observed={}
    for name,limit in (('native-outcome.json',8192),('native.stderr',8192),
                       ('native.jsonl',4096),('runner.stderr',8192),
                       ('checkpoint.json',4096),('stage-end.json',4096),
                       ('continuations/001/native-outcome.json',8192),
                       ('continuations/001/native.stderr',8192),
                       ('continuations/001/native.jsonl',4096),
                       ('continuations/001/runner.stderr',8192),
                       ('continuations/001/checkpoint.json',4096),
                       ('continuations/001/stage-end.json',4096),
                       ('continuations/002/native-outcome.json',8192),
                       ('continuations/002/native.stderr',8192),
                       ('continuations/002/native.jsonl',4096),
                       ('continuations/002/runner.stderr',8192),
                       ('continuations/002/checkpoint.json',4096),
                       ('continuations/002/stage-end.json',4096)):
        path=record/name
        if not path.is_file():
            observed[name]='missing'
            continue
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-limit))
            observed[name]=stream.read(limit).decode('utf-8',errors='replace')
    launches=c.workspace(root,2)/'work/launches'
    if launches.is_file():observed['work/launches']=launches.read_bytes()[:4096].decode('utf-8',errors='replace')
    # Explicitly authorized node-002 host diagnostics only: one directory level,
    # four entries at most. These stay in the test failure log, never science.
    directory=root/'host-runtime/experiment-control/002/execution-diagnostics'
    if directory.is_dir():
        with os.scandir(directory) as entries:
            for index,entry in enumerate(entries):
                if index>=4:break
                if not entry.name.endswith('.json') or not entry.is_file(follow_symlinks=False):continue
                with Path(entry.path).open('rb') as stream:
                    observed['execution-diagnostics/'+entry.name]=stream.read(8192).decode('utf-8',errors='replace')
    rendered=json.dumps(dict(result=result,node=2,transport=observed),ensure_ascii=False,indent=2)
    rendered=rendered.encode('utf-8')[:128*1024].decode('utf-8',errors='ignore')
    if os.environ.get('SCID_TEST_LOG_DIR'):
        destination=Path(os.environ['SCID_TEST_LOG_DIR']);destination.mkdir(parents=True,exist_ok=True)
        (destination/f'{fault}-fragment-{int(fragment)}-node002.txt').write_text(rendered)
    return rendered


def prepare_cli(tmp_path,*,fault='missing',custom=False,explicit_false=False,final=False,fragment=False,automatic=None,max_tokens=None,a_screened=False,output_repair_probe=False,layout_input_tokens=2040301,max_seconds=60):
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Retain scientific uncertainty.')
    home=tmp_path/'inherited-home';home.mkdir()
    configuration='[sandbox_workspace_write]\nnetwork_access=true\nexclude_tmpdir_env_var=true\n'
    (home/'config.toml').write_text(configuration);(home/'auth.json').write_text('{"fixture":"private authentication marker"}')
    program=tmp_path/'native.py'
    if fault.startswith(('nomination-','completed-','author-','caller-')):
        # The model wire is fake; managed local execution still needs the real
        # sandbox auxiliary from the selected native installation, as in the
        # existing default-local transport fixture.
        from test_default_native_local_execution import actual_native
        actual=actual_native()
        package=tmp_path/'node_modules/@openai/codex'
        program=package/'bin/codex.js';program.parent.mkdir(parents=True)
        architecture,target={'x86_64':('x64','x86_64-unknown-linux-musl'),
            'aarch64':('arm64','aarch64-unknown-linux-musl')}[os.uname().machine]
        auxiliary=package/'node_modules/@openai'/('codex-linux-'+architecture)/'vendor'/target/'bin/codex'
        auxiliary.parent.mkdir(parents=True);auxiliary.symlink_to(actual)
    program.write_text('#!'+sys.executable+'\n'+NATIVE);program.chmod(0o755)
    binary=tmp_path/'bin';binary.mkdir();(binary/'codex').symlink_to(program)
    c.write(program.with_name('launcher-config.json'),dict(fault=fault,home=str(home),configuration=configuration,default_home=not explicit_false,a_screened=a_screened,output_repair_probe=output_repair_probe,layout_input_tokens=layout_input_tokens))
    env=dict(os.environ,CODEX_HOME=str(home),PATH=str(binary)+os.pathsep+os.environ.get('PATH',''))
    source=str(Path(__file__).resolve().parents[2]/'src')
    env['PYTHONPATH']=source+os.pathsep+env.get('PYTHONPATH','')
    root=tmp_path/'instance';search=tmp_path/'search.json'
    c.write(search,dict(search_contract.DEFAULT_POLICY,initial_calls=1) if fault=='incomplete_w' or fault.startswith(('nomination-','completed-','author-')) else None)
    launcher=[sys.executable,str(program)] if custom else [str(binary/'codex') if explicit_false or fragment or automatic is not None else 'codex']
    arguments=['prepare','--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json',json.dumps(launcher),
        '--max-tokens',str(max_tokens) if max_tokens is not None else '1' if final else '1000','--max-seconds',str(max_seconds),'--max-steps','1' if final else '6',
        '--search-policy',str(search)]
    if explicit_false:
        policy=tmp_path/'resume.json';c.write(policy,dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=False))
        nodes=tmp_path/'nodes.json';c.write(nodes,{role:dict(max_observed_tokens=1000,max_seconds=60) for role in 'HWAE'})
        arguments+=['--worker-resume-policy',str(policy),'--node-budget-policy',str(nodes)]
    if fragment or automatic is not None:
        policy=tmp_path/'resume.json';c.write(policy,dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',
            output_repair=True,output_only=True,native_home=str(home),
            **(dict(automatic_recovery=automatic) if automatic is not None else {})))
        nodes=tmp_path/'nodes.json';c.write(nodes,{role:dict(max_observed_tokens=1000,max_seconds=60) for role in 'HWAE'})
        arguments+=['--worker-resume-policy',str(policy),'--node-budget-policy',str(nodes)]
        if fragment:arguments+=['--worker-context','same_session']
    prepared=cli(root,*arguments,env=env)
    assert prepared['status']=='prepared',prepared
    return root,home,env


def test_default_cli_policy_cannot_authorize_scientific_work_repair(tmp_path):
    root,home,_=prepare_cli(tmp_path)
    c.build_job(root,1,'W',[])
    work=c.workspace(root,1);record=execution.origin(root,1)
    originals={path:path.read_bytes() for path in (root/'settings.json',root/'settings-identity.json',
        root/'jobs/001.json',work/'science-task.json',record/'science-result.schema.json',
        home/'config.toml',home/'auth.json')}
    with pytest.raises(ValueError,match='does not authorize scientific work repair'):
        continuation.validate_repair(root,1,dict(work_repair=dict(code='worker_delivery_incomplete')))
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert not (work/'work/launches').exists()
    assert all(not (record/name).exists() for name in ('native-intent.json','runner-process.json',
        'native-process.json','native-outcome.json','result-identity.json','continuations'))


@pytest.mark.parametrize('native_home',['relative-home','~/.codex'])
def test_default_cli_rejects_literal_relative_native_home(tmp_path,native_home):
    binary=tmp_path/'bin';binary.mkdir();program=binary/'codex'
    program.write_text('#!'+sys.executable+'\nraise SystemExit(99)\n');program.chmod(0o755)
    public=tmp_path/'public';public.mkdir();objective=public/'objective.md';objective.write_text('Do not submit native work.')
    root=tmp_path/'instance';env=dict(os.environ,CODEX_HOME=native_home,PATH=str(binary)+os.pathsep+os.environ.get('PATH',''))
    env['PYTHONPATH']=str(Path(__file__).resolve().parents[2]/'src')+os.pathsep+env.get('PYTHONPATH','')
    result=cli(root,'prepare','--objective',str(objective),'--public-root',str(public),
        '--native-launcher-json','["codex"]','--max-tokens','1000','--max-seconds','60','--max-steps','6',env=env)
    assert result['status']=='error' and 'absolute inherited CODEX_HOME' in result['error']
    assert not root.exists()
