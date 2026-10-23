"""Real rootless fake-native closeout and audits; no model requests or training."""
import json
from pathlib import Path
import shlex
import sys
import sysconfig

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller, execution, native
from cov_execution import lineage as cov_lineage
from scidiscovery.research_loop.experiment_client import record_directory,resolve
from test_cov_delivery_lineage import current_managed_scene


FAKE=r'''import json,os,pathlib,subprocess,sys
args=sys.argv[1:];sys.stdin.read();work=pathlib.Path(args[args.index('-C')+1])
task=json.loads((work/'science-task.json').read_text());home=pathlib.Path(os.environ['CODEX_HOME'])
thread='99999999-2222-4333-8444-555555555555'
session=home/'sessions/current.jsonl';session.parent.mkdir(parents=True,exist_ok=True)
session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
denied=subprocess.run(['exp','run','--script','never.py'],capture_output=True,text=True)
assert denied.returncode==2 and json.loads(denied.stdout)['accepted'] is False,(denied.stdout,denied.stderr)
payload=dict(summary='Synthetic author interpretation',scientific_result='Transport fixture only, no scientific efficacy claim.',
 limitations=['Fake native; historical metering remains unknown.'],implementation_selection=None)
if task['role']=='W':
 projection=work.parents[2]/'native-evidence'/work.name/'experiment-config.json'
 assert not projection.exists(), 'Private execution projection exposed to science view'
 for mutate in (lambda:projection.write_text('{}'),lambda:projection.chmod(0o600),lambda:projection.unlink()):
  try:mutate()
  except OSError:pass
  else:raise AssertionError('Retained execution projection writable')
 facts=json.loads((work/'work/author-closeout-science.json').read_text());assert len(facts['calls'])==2
 assert not (work/'work/author-closeout-facts.json').exists()
 assert 'source_ref' not in json.dumps(facts) and 'model_ref' not in json.dumps(facts)
 payload.update(stage_intent='review',mechanism_findings=None)
 (work/'artifacts/author.md').write_text('Independent synthetic author final contribution; no new experiment.')
 if MODE=='unknown':raise SystemExit(7)
 if MODE=='wall':
  import time
  print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
  time.sleep(30)
else:
 assert task['role']=='A'
 step=int(work.name);choice='run-1'
 payload.update(next_action='deliver',candidate_recommendation=choice,
  route_reason='Synthetic audit or delivery authorization',search_review=None,progress_assessment=None,
  decision_request=None,portfolio_review=None)
if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=dict(criteria='Compare the declared public fixture evidence and retain its stated limitations.',criteria_change=dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None))
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=100 if MODE=='over' and task['role']=='W' else 7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


def assert_original_delivery(root):
    actions=budget.closeout_state(root)['actions']
    assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
    assert all(row['outcome']['result']['status']=='confirmed' for row in actions)
    assert actions[0]['intent']['binding']['source'].get('audit_required',True)
    assert len({row['intent']['operation_id'] for row in actions})==1
    for row,phase in ((actions[0],'audit'),(actions[0],'promote'),(actions[1],'confirm')):
        intent=row['intent']
        process=c.read(root/'host-delivery/action-processes'/(intent['action_id']+'-'+phase+'.json'))
        assert (process['action_id'],process['operation_id'],process['phase'])==(intent['action_id'],intent['operation_id'],phase)
        assert process['identity']['task_ref']==intent['operation_id']
    assert list(candidate_audit.facts(root))==[]  # FINAL uses the original host audit, without another scientific A.
    assert c.read(root/'jobs/003.json')['final_only'] and not (root/'jobs/004.json').exists()
    assert c.read(root/'runs/submitted.json')['run_id']==1


def source_python(tmp_path,repo):
    """Explicit source imports under real -I; no product install or test path."""
    bridge=tmp_path/'source-runtime';bridge.mkdir()
    metadata=bridge/'scidiscovery_namespace_fixture-0.dist-info';metadata.mkdir()
    (metadata/'METADATA').write_text('Metadata-Version: 2.1\nName: scidiscovery-namespace-fixture\nVersion: 0\n')
    source=repo/'src';bootstrap=bridge/'bootstrap.py'
    bootstrap.write_text('import importlib.metadata, runpy, sys\nfrom pathlib import Path\n'
        'assert sys.flags.isolated and sys.dont_write_bytecode\n'
        f'sys.path[:0] = {[str(source),str(repo/'plugins/cov_execution'),str(bridge)]!r}\n'
        f'discovery_paths = [path for path in sys.path if path != {str(bridge)!r}]\n'
        'declared = {entry.name for distribution in importlib.metadata.distributions(path=discovery_paths)\n'
        '    for entry in distribution.entry_points if entry.group == "scidiscovery.execution_interfaces"}\n'
        'entries = {"local_execution": "scidiscovery.research_loop.local_experiments:REGISTRATION",\n'
        '    "cov_execution": "cov_execution.experiments:REGISTRATION"}\n'
        'content = "[scidiscovery.execution_interfaces]\\n" + "".join(\n'
        '    name + " = " + target + "\\n" for name, target in entries.items() if name not in declared)\n'
        f'entry_file = Path({str(metadata/'entry_points.txt')!r})\n'
        'if not entry_file.exists() or entry_file.read_text() != content: entry_file.write_text(content)\n'
        'from scidiscovery.research_loop import contracts as c\n'
        f'assert Path(c.__file__).resolve() == Path({str(source/"scidiscovery/research_loop/contracts.py")!r})\n'
        'args = sys.argv[1:]\n'
        'while args and args[0] in ("-B", "-I"): args.pop(0)\n'
        'if len(args) < 2 or args[0] not in ("-c", "-m"): raise SystemExit("source fixture requires -c or -m")\n'
        'if args[0] == "-m":\n'
        '    sys.argv = args[1:]\n'
        '    runpy.run_module(args[1], run_name="__main__", alter_sys=True)\n'
        'else:\n'
        '    sys.argv = ["-c", *args[2:]]\n'
        '    exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__"})\n')
    python=str(Path(sys.executable).absolute());shim=bridge/'python'
    shim.write_text('#!/bin/sh\nexec '+shlex.quote(python)+' -B -I '+shlex.quote(str(bootstrap))+' "$@"\n')
    shim.chmod(0o555)
    paths=sysconfig.get_paths()
    runtime_paths=list(dict.fromkeys([sys.base_prefix,str(Path(python).parent.parent),str(source),str(bridge),
        *(str(Path(paths[key]).resolve()) for key in ('purelib','platlib'))]))
    return python,str(shim),runtime_paths


def scene(tmp_path,monkeypatch,mode):
    from test_default_native_local_execution import actual_native
    repo=Path(__file__).resolve().parents[2];config=tmp_path/'launcher.json'
    python,scid_python,runtime_paths=source_python(tmp_path,repo)
    launcher=[scid_python,'-B','-I','-m','cov_execution.launcher','--config',str(config),'--']
    actual=actual_native()
    fake=tmp_path/'fake-codex';fake.write_text('#!'+sys.executable+'\nimport os,sys\n'
        f'if os.path.basename(sys.argv[0])=="codex-linux-sandbox": os.execv({str(actual)!r},[sys.argv[0],*sys.argv[1:]])\n'
        f'if len(sys.argv)>1 and sys.argv[1]=="sandbox": os.execv({str(actual)!r},[{str(actual)!r},*sys.argv[1:]])\n'
        'assert len(sys.argv)>1 and sys.argv[1]=="exec", repr(sys.argv)\n'
        +'MODE='+repr(mode)+'\n'+FAKE);fake.chmod(0o555)
    original_prepare=controller.prepare
    def prepare(*args,**kwargs):
        kwargs.update(max_steps=5,node_budget_mode='soft')
        return original_prepare(*args,**kwargs)
    monkeypatch.setattr(controller,'prepare',prepare)
    import test_cov_delivery_lineage as managed
    old_finish=managed.finish
    def unfinished(root,step,role,parents,*args,**kwargs):
        if step!=2:return old_finish(root,step,role,parents,*args,**kwargs)
        # Retain an unsealed W slot; no scientific W output is made.
        c.write(execution.origin(root,2)/'result-identity.json',dict(fixture='unsealed W slot'))
    monkeypatch.setattr(managed,'finish',unfinished)
    root,*_=current_managed_scene(tmp_path,monkeypatch,native_launcher=launcher,native_program=fake,audit=True)
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Bounded delivery preparation')
    origin=execution.origin(root,2);(origin/'result-identity.json').unlink()
    meter=dict(input_tokens=17,cached_input_tokens=2,output_tokens=3)
    observation=dict(coverage='unknown',reason='terminal_usage_unreported',thread_id='old-fixture-thread',tokens=meter)
    c.write(origin/'usage.json',meter);c.write(origin/'usage-observation.json',observation)
    (origin/'native.jsonl').write_text('{"type":"thread.started"}\n')
    policy=c.settings(root)['node_budget_policy']['W']
    report=dict(role='W',advance_seconds=1.,**meter,observed_tokens=20,dispatches=1,limits=policy,usage_observed=True)
    c.write(origin/'native-outcome.json',dict(returncode=-15,stop_reason='token_limit',cleanup='observed_group_cleaned',
        metering=observation,thread_id='old-fixture-thread',budget_slice=report,usage=dict(unknown_steps=[2])))
    receipt=dict(pid=999999999,start_ticks=1,pgid=999999999,sid=999999999,ppid=1,
        boot_id=budget.clock_stamp()['boot_id'],task_ref=c.record_ref(root/'jobs/002.json'))
    for name in ('runner-process.json','native-process.json'):c.write(origin/name,receipt)
    session=root/'native-homes/002/sessions/old.jsonl';session.parent.mkdir(parents=True)
    session.write_text(json.dumps(dict(type='session_meta',payload=dict(id='old-fixture-thread',cwd=str(c.workspace(root,2)))))+'\n')
    public=tmp_path/'public-eight';harness=public/'environment/harness';harness.mkdir(parents=True)
    for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json'):
        (harness/name).write_bytes((tmp_path/'public/harness'/name).read_bytes())
    (public/'instruction.md').write_text('Synthetic retained-call author closeout.')
    (public/'task.toml').write_text('fixture=true\n');(public/'environment/Dockerfile').write_text('FROM fixture\n')
    manifest=tmp_path/'public-manifest.json'
    c.write(manifest,dict(task_revision='d78f5eb52ad754c5ee9154741af73130a85a65b8',
        public_files=[p.relative_to(public).as_posix() for p in public.rglob('*') if p.is_file()]))
    codehost=actual.parent/'codex-code-mode-host'
    c.write(config,dict(scene_root=str(root),public_root=str(public),public_manifest=str(manifest),
        runs_dir=str(root/'runs'),submission_dir=str(root/'submission'),native=str(fake),
        code_mode_host=dict(path=str(codehost)),task_python=python,scid_python=scid_python,
        runtime_paths=[*runtime_paths,str(actual)],model='fixture',reasoning_effort='high',profile='closeout-no-model',cov_receipt_channel='public_runner_main'))
    projection=origin/'experiment-config.json';original=projection.read_bytes();frozen=c.read(projection)
    configured=c.workspace(root,2)/'work/lineage-config.json'
    cov_lineage.configure(root,2,frozen['run_argv'],Path(frozen['runs_root']),configured,
        receipt_channel=frozen['receipt_channel'],
        engineering_check_reserve_seconds=frozen.get('engineering_check_reserve_seconds',0))
    assert resolve(configured,c.read(configured))==frozen
    assert projection.read_bytes()==original
    budget.amend(root,max_tokens=200000,max_steps=24,authorization_note='Finite synthetic recovery.',authorization_statement='Finish the retained task.')
    return root,origin


def closeout_diagnostics(root,origin):
    diagnostics={}
    for record in (origin/'continuations/001',root/'native-evidence/003'):
        for name in ('native.stderr','runner.stderr','native.jsonl','native-outcome.json','result.pending.json','science-output.json'):
            path=record/name
            if path.is_file():
                with path.open('rb') as stream:
                    stream.seek(max(0,path.stat().st_size-8192))
                    diagnostics[str(path.relative_to(root))]=stream.read(8192).decode(errors='replace')
    return diagnostics


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode',['success','unknown','over','wall'])
def test_fresh_author_closeout_seals_and_audits_or_preserves_failed_attempt(tmp_path,monkeypatch,execution_registry,mode):
    root,origin=scene(tmp_path,monkeypatch,mode)
    old=(origin/'native-outcome.json').read_bytes();settings=(root/'settings.json').read_bytes()
    calls=record_directory(root,2)/'calls'
    originals={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
    if mode in {'success','over'}:
        target_tokens=5 if mode=='over' else 1000
        try:
            result=controller.worker_closeout(root,2,max_tokens=target_tokens,max_seconds=20,authorization_note='One fresh author-only interpretation, no experiment retry.')
        except (ValueError,RuntimeError) as error:
            pytest.fail(json.dumps(dict(error=str(error),diagnostics=closeout_diagnostics(root,origin)),ensure_ascii=False,indent=2))
        if result['status']!='delivery_confirmed':
            pytest.fail(json.dumps(dict(result=result,diagnostics=closeout_diagnostics(root,origin)),ensure_ascii=False,indent=2))
        assert c.validate(c.workspace(root,2))['stage_intent']=='review'
        outcome=c.read(origin/'continuations/001/native-outcome.json')
        assert outcome['returncode']==0 and outcome['stop_reason'] is None
        if mode=='over':
            assert c.read(origin/'continuations/001/usage.json')['input_tokens']==100
            assert c.read(root/'host-recovery/author-closeout/002.json')['max_tokens']==5
        assert_original_delivery(root)
        assert len(execution.records(root,2))==2 and not (origin/'continuations/002').exists()
        intents={p:p.read_bytes() for p in origin.rglob('native-intent.json')}
        from scidiscovery.research_loop import runtime,delivery
        with monkeypatch.context() as repeat:
            repeat.setattr(runtime,'start',lambda *a,**kw:pytest.fail('Confirmed closeout cannot dispatch another native attempt'))
            repeat.setattr(delivery,'invoke',lambda *a,**kw:pytest.fail('Confirmed closeout cannot repeat host submission'))
            assert controller.worker_closeout(root,2,max_tokens=target_tokens,max_seconds=20,authorization_note='One fresh author-only interpretation, no experiment retry.')['status']=='delivery_confirmed'
        assert all(p.read_bytes()==raw for p,raw in intents.items())
        assert budget.used_steps(root)>5 and budget.effective_max_steps(root)==24
        assert c.settings(root).get('resume_policy') is None
        assert native.usage(root)['unknown_steps']==[2]
        assert native.usage(root)['blocking_unknown_steps']==[]
        projection=origin/'continuations/001/experiment-config.json'
        assert projection.read_bytes()==(origin/'experiment-config.json').read_bytes()
        seal=c.read(origin/'result-identity.json')
        observed=c.read(root/seal['observations']['path'])
        assert len(observed['managed_calls'])==2
        assert {row['id'] for row in observed['candidates']}=={'run-1','run-2'}
        assert len(observed['contexts'])==2 and all(row['status']=='known_success' for row in observed['contexts'])
        projection.chmod(0o600);projection.write_bytes(b'{}\n')
        from scidiscovery.research_loop import author_closeout
        with pytest.raises(ValueError,match='retained execution projection changed'):
            author_closeout.require_intent_eligibility(root,c.read(root/'jobs/002.json'),c.read(origin/'continuations/001/native-intent.json'))
        projection.write_bytes((origin/'experiment-config.json').read_bytes());projection.chmod(0o400)
    else:
        with pytest.raises((ValueError,RuntimeError)):
            controller.worker_closeout(root,2,max_tokens=1000,max_seconds=3 if mode=='wall' else 20,authorization_note='One finite negative-control author attempt.')
        assert not (origin/'result-identity.json').exists()
        assert not (root/'jobs/003.json').exists()
        if mode=='unknown':
            outcome=c.read(origin/'continuations/001/native-outcome.json')
            assert (outcome['returncode'],outcome['stop_reason'])==(7,None),closeout_diagnostics(root,origin)
            assert outcome['thread_id']=='99999999-2222-4333-8444-555555555555'
            assert outcome['metering']['coverage']=='unknown'
            assert (c.workspace(root,2)/'artifacts/author.md').read_text()=='Independent synthetic author final contribution; no new experiment.'
            assert native.usage(root)['unknown_steps']==[2]
            assert native.usage(root)['blocking_unknown_steps']==[]
        if mode=='wall':
            outcome=c.read(origin/'continuations/001/native-outcome.json')
            assert outcome['stop_reason']=='author_closeout_wall_limit',closeout_diagnostics(root,origin)
            assert outcome['cleanup']=='observed_group_cleaned'
    assert len(execution.records(root,2))==2
    assert (origin/'native-outcome.json').read_bytes()==old and (root/'settings.json').read_bytes()==settings
    assert originals=={p:p.read_bytes() for p in calls.rglob('*') if p.is_file()}
