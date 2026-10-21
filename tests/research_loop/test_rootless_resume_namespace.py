"""Actual launcher/main/bwrap resume transport; fake native, no model requests."""
import os
import subprocess

import pytest

from execution_fixtures import execution_registry
from scidiscovery.research_loop import backends,configuration_updates,contracts as c,execution,native,continuation,profiles
from scidiscovery.research_loop.experiment_client import record_directory
from test_rootless_fragment_namespace import FAKE_NATIVE,prepare_namespace,execute_namespace,assert_public_submission


THREAD='11111111-1111-4111-8111-111111111111'


def resume_native(repair,*,shallow=False):
    source=FAKE_NATIVE.replace("assert 'resume' not in args", "assert ('resume' in args)==later\nif later:\n assert args[-2:] == ['11111111-1111-4111-8111-111111111111','-']\n assert '--last' not in args")
    source=source.replace("thread='22222222-2222-4222-8222-222222222222' if later else '11111111-1111-4111-8111-111111111111'", "thread='11111111-1111-4111-8111-111111111111'")
    source=source.replace("session.write_text(", "if not later:session.write_text(")
    source=source.replace("  assert home.name=='context-001'\n  assert (work/'work/science-resume.json').is_file()", "  assert home.name==work.name\n  prior=json.loads((work/'work/first-transport.json').read_text())\n  assert prior['session_header']==session.read_text()\n  assert prior['home_config']==(home/'config.toml').read_text()\n  assert prior['home']==str(home)\n  (work/'work/resume-argv.json').write_text(json.dumps(args))")
    source=source.replace("output.write_text(json.dumps(payload))", """if not later and REPAIR:payload.pop('scientific_result')
output.write_text(json.dumps(payload))
if not later:
  (work/'work/first-transport.json').write_text(json.dumps(dict(raw_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
  session_header=session.read_text(),home_config=(home/'config.toml').read_text(),home=str(home))))
""")
    if shallow:
        start=source.index('  if search_enabled:\n   rejected=')
        end=source.index("  (work/'work/original-call.json')",start)
        source=source[:start]+source[end:]
        source=source.replace("  (work/'work/second-context-query.json').write_text(result.stdout)","""  (work/'work/second-context-query.json').write_text(result.stdout)
  added=subprocess.run([str(work/'bin/exp'),'run','--script',str(work/'payload.py'),'--new-plan','--mechanism','Q'],capture_output=True,text=True)
  assert added.returncode==0,(added.stdout,added.stderr)
  (work/'work/added-q.json').write_text(added.stdout)""")
        source=source.replace("payload['stage_intent']='review' if later else 'continue'", "payload['stage_intent']='review'")
        source=source.replace("for label in ('P','Q')] if search_enabled else None)","for label in (('P','Q') if later else ('P',))] if search_enabled else None)")
    return 'import hashlib\nREPAIR='+repr(repair)+'\n'+source


@pytest.mark.parametrize('mode',['fragment','output_repair'])
def test_actual_same_session_preserves_call_and_frozen_originals(tmp_path,mode):
    repair=mode=='output_repair'
    root=prepare_namespace(tmp_path,fake_source=resume_native(repair),context='same_session',repair=repair)
    c.build_job(root,1,'W',[]);execute_namespace(root,1)
    chain=execution.records(root,1);assert len(chain)==2
    first,second=chain;intent=c.read(second/'native-intent.json')
    assert intent['continuation_kind']==('repair' if repair else 'fragment')
    assert intent['session']==continuation.session(root,1,first)
    assert intent['previous']==execution.proof(first)
    assert intent['root_intent_sha256']==c.digest(first/'native-intent.json')
    assert execution.home(root,1,0)==execution.home(root,1,1)
    assert c.read(first/'native-outcome.json')['thread_id']==THREAD
    assert c.read(second/'native-outcome.json')['thread_id']==THREAD
    assert native.usage(root)['observed_tokens']==2
    work=c.workspace(root,1);snapshot=c.read(work/'work/first-transport.json')
    assert snapshot['raw_sha256']==c.digest(first/'science-output.json')
    assert snapshot['session_header']==(execution.home(root,1)/'sessions/current.jsonl').read_text()
    assert snapshot['home_config']==(execution.home(root,1)/'config.toml').read_text()
    assert c.read(work/'work/second-context-query.json')['state']=='succeeded'
    assert len(list((record_directory(root,1)/'calls').glob('*/accepted.json')))==1
    assert_public_submission(root,1)
    assert len(list((root/'jobs').glob('*.json')))==1
    assert c.validate(work)['stage_intent']=='review'
    args=c.read(work/'work/resume-argv.json')
    assert args.count('resume')==1 and args[-2:]==[THREAD,'-']
    assert args[args.index('--output-schema')+1]=='/task-result/science-result.schema.json'
    assert args[args.index('--output-last-message')+1]=='/task-result/result.pending.json'
    public=root/'host-runtime/science-native-results/001/001'
    assert (public/'science-result.schema.json').read_bytes()==(first/'science-result.schema.json').read_bytes()
    assert c.digest(public/'result.pending.json')==c.read(second/'native-outcome.json')['raw_sha256']


@pytest.mark.parametrize('state',['terminal_replay','unknown_dispatch'])
def test_actual_main_rejects_relaunch_before_second_namespace_native(tmp_path,state):
    root=prepare_namespace(tmp_path,fake_source=resume_native(False),context='same_session')
    c.build_job(root,1,'W',[]);execute_namespace(root,1)
    first,second=execution.records(root,1);work=c.workspace(root,1)
    if state=='unknown_dispatch':
        # Model no longer known terminal, but original native/runner PID receipts
        # cannot authorize this new launcher process. No replacement submission.
        (second/'native-outcome.json').rename(second/'held-outcome.json')
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    env=dict(os.environ,THIN_HWA_JOB_MANIFEST=str(root/'jobs/001.json'),THIN_HWA_WORKSPACE=str(work),
        THIN_HWA_NATIVE_HOME=str(execution.home(root,1,1)),THIN_HWA_ROLE='W')
    # Rebuild the original request; fake-native argv has already had the
    # framework sandbox options translated to the generated namespace profile.
    job=c.read(root/'jobs/001.json');settings=configuration_updates.for_job(root,job)
    backend=backends.selected(settings);profile=profiles.for_job(settings,job)
    pending=second/'result.pending.json';schema=first/'science-result.schema.json'
    args=backend.launch(profile,work,pending,schema,job.get('native_role_instruction',c.ROLE_INSTRUCTIONS['W']))
    args,_=backend.resume(args,profile,work,pending,schema,c.read(second/'native-intent.json')['session'])
    if not backend.supports_native_schema_flag:args[-2:-2]=['--output-schema',str(schema)]
    args=args[len(backend.launcher):]
    result=subprocess.run([*backend.launcher,*args],env=env,capture_output=True,text=True)
    assert result.returncode!=0
    expected='original_dispatch_already_terminal_or_returned' if state=='terminal_replay' else 'original_runner_dispatch_required'
    assert expected in result.stderr,(result.stdout,result.stderr)
    assert {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}==before



def test_actual_incomplete_screen_returns_same_w_and_only_adds_missing_q(tmp_path):
    root=prepare_namespace(tmp_path,search_enabled=True,fake_source=resume_native(False,shallow=True),context='new_context')
    c.build_job(root,1,'H',[]);execute_namespace(root,1)
    job=c.build_job(root,2,'W',[1]);execute_namespace(root,2)
    chain=execution.records(root,2);assert len(chain)==2
    first,second=chain;intent=c.read(second/'native-intent.json')
    assert intent['continuation_kind']=='repair' and intent.get('work_repair')
    assert intent.get('output_repair') is None
    assert intent['session']==continuation.session(root,2,first)
    assert intent['previous']==execution.proof(first)
    assert intent['root_intent_sha256']==c.digest(first/'native-intent.json')
    assert c.read(first/'science-output.json')['mechanism_findings'][0]['mechanism']=='P'
    final=c.validate(c.workspace(root,2))
    assert {finding['mechanism'] for finding in final['mechanism_findings']}=={'P','Q'}
    work=c.workspace(root,2);snapshot=c.read(work/'work/first-transport.json')
    assert snapshot['raw_sha256']==c.digest(first/'science-output.json')
    assert snapshot['session_header']==(execution.home(root,2)/'sessions/current.jsonl').read_text()
    assert snapshot['home_config']==(execution.home(root,2)/'config.toml').read_text()
    assert execution.home(root,2,0)==execution.home(root,2,1)
    assert [c.read(record/'native-outcome.json')['thread_id'] for record in chain]==[THREAD,THREAD]
    calls=list((record_directory(root,2)/'calls').glob('*/accepted.json'))
    assert len(calls)==2
    assert_public_submission(root,2)
    assert sorted(c.read(path)['mechanism_binding']['mechanism_ref'] for path in calls)==['h001.m1','h001.m2']
    assert c.read(work/'work/second-context-query.json')['state']=='succeeded'
    assert c.read(work/'work/added-q.json')['call_id']!=c.read(work/'work/original-response.json')['result']['call_id']
    assert len(list((root/'jobs').glob('*.json')))==2
    assert native.usage(root)['observed_tokens']==3
    assert not c.settings(root)['resume_policy']['output_repair']
    assert c.settings(root)['worker_fragment_policy']['context']=='new_context'
