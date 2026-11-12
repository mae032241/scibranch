"""Real native repair requests keep billing separate from prospective science."""
import copy
import hashlib
import json
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, native, originals, search
from test_soft_node_budget import start_clock


PAID_H_NATIVE = r'''import json,os,pathlib,shlex,subprocess,sys,tomllib
args=sys.argv[1:];work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);config={}
assert os.environ['THIN_HWA_ROLE']=='H'
def merge(target,update):
 for key,value in update.items():
  if isinstance(value,dict) and isinstance(target.get(key),dict):merge(target[key],value)
  else:target[key]=value
for index,arg in enumerate(args[:-1]):
 if arg=='-c':merge(config,tomllib.loads(args[index+1]))
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():return json.loads(sys.stdin.readline())
request=receive();assert request['method']=='initialize';send(dict(id=request['id'],result={}))
assert receive()['method']=='initialized';request=receive()
if request['method']=='hooks/list':
 assert request['params']==dict(cwds=[str(work)])
 hooks=[]
 for name,key,event in [('PostToolUse','post_tool_use','postToolUse'),('PostModelRequest','post_model_request','postModelRequest'),('PostCompact','post_compact','postCompact')]:
  if name not in config.get('hooks',{}):continue
  handler=config['hooks'][name][0]['hooks'][0]
  hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',eventName=event,
   enabled=True,isManaged=False,trustStatus='untrusted',currentHash='sha256:'+'0'*64,
   source='sessionFlags',sourcePath='/<session-flags>/config.toml',handlerType='command',
   command=handler['command'],timeoutSec=handler['timeout'],additionalContextLimit=handler.get('additionalContextLimit')))
 send(dict(id=request['id'],result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
 raise SystemExit(0)
assert request['method'] in {'thread/start','thread/resume'}
start=request['params'];resume=request['method']=='thread/resume'
thread='11111111-2222-4333-8444-000000000001'
assert start['cwd']==str(work) and start['approvalPolicy']=='never'
assert '--dangerously-bypass-hook-trust' in args and start['config']==dict(bypass_hook_trust=True)
assert start['sandbox']=='workspace-write' and config['features']['multi_agent'] is False
assert not resume or start['threadId']==thread and start['excludeTurns'] is True
send(dict(id=request['id'],result=dict(thread=dict(id=thread,turns=[]),model=start['model'],cwd=str(work),
 approvalPolicy='never',sandbox=dict(type='workspaceWrite',writableRoots=[],networkAccess=False),
 reasoningEffort=config['model_reasoning_effort'],serviceTier=start.get('serviceTier'))))
request=receive();assert request['method']=='turn/start';turn=request['params']
assert turn['threadId']==thread and turn['model']==start['model'] and turn['approvalPolicy']=='never'
assert turn.get('effort')==config['model_reasoning_effort'] and turn.get('serviceTier')==start.get('serviceTier')
assert len(turn['input'])==1 and turn['input'][0]['type']=='text'
prompt=turn['input'][0]['text'];turn_id='repair-turn' if resume else 'original-turn'
send(dict(id=request['id'],result=dict(turn=dict(id=turn_id,items=[],status='inProgress'))))
home=pathlib.Path(os.environ['CODEX_HOME']);transcript=home/'sessions/opaque.jsonl'
with (work/'work/launches').open('a') as stream:stream.write('resume\n' if resume else 'exec\n')
if not resume:
 transcript.parent.mkdir(parents=True,exist_ok=True);transcript.write_bytes(b'opaque native original\n')
 payload=dict(summary='Prospective experiment plan',scientific_result='No experiment has been performed.',
  limitations=['The planned comparisons remain untested.'],implementation_selection=None,
  next_action='experiment',route_reason='A new bounded comparison can distinguish the hypotheses.',
  search_plan=dict(scope='Compare declared mechanisms',commitments=[dict(label='Mechanism',
   minimal_test='Compare two fresh controlled observations',existing_ref=None)],focus=dict(index=999,action='screen')),
  execution_request=dict(experiments=[dict(name='fresh-control',repeats=2)],observed_experiments=0))
 (work/'work/original-plan.json').write_text(json.dumps(payload))
else:
 assert transcript.read_bytes()==b'opaque native original\n'
 assert 'Fix diagnosed JSON/layout only' in prompt and 'No work/rerun/resubmit' in prompt
 assert 'No prior experiment is implied.' in prompt
 payload=json.loads((work/'work/original-plan.json').read_text())
 payload['search_plan']['focus']['index']=1
 (work/'work/repair-request.txt').write_text(prompt)
 (work/'work/repair-budget.json').write_bytes((work/'work/budget-status.json').read_bytes())
handler=config['hooks']['PostModelRequest'][0]['hooks'][0]
event=dict(session_id=thread,cwd=str(work),transcript_path=str(transcript),hook_event_name='PostModelRequest',
 status='completed',token_usage=dict(input_tokens=10 if resume else 131729,cached_input_tokens=3,output_tokens=4))
delivery=subprocess.run(shlex.split(handler['command']),input=json.dumps(event),text=True,capture_output=True,timeout=handler['timeout'])
assert delivery.returncode==0,delivery.stderr
raw=json.dumps(payload)
(work/('work/repaired-final.json' if resume else 'work/original-final.json')).write_text(raw)
send(dict(method='item/completed',params=dict(threadId=thread,turnId=turn_id,
 item=dict(type='agentMessage',text=raw,phase='final_answer'))))
send(dict(method='turn/completed',params=dict(threadId=thread,turn=dict(id=turn_id,status='completed'))))
'''


@pytest.mark.process_e2e
def test_h_field_repair_preserves_paid_usage_and_complete_prospective_w_plan(tmp_path, monkeypatch):
    public = tmp_path/'public'; public.mkdir()
    objective = public/'objective.md'; objective.write_text('Retain scientific uncertainty.')
    home = tmp_path/'inherited-home'; home.mkdir()
    (home/'config.toml').write_text('[sandbox_workspace_write]\nnetwork_access=true\nexclude_tmpdir_env_var=true\n')
    (home/'auth.json').write_text('{"fixture":"private authentication marker"}')
    monkeypatch.setenv('CODEX_HOME', str(home))
    launcher = tmp_path/'codex'; launcher.write_text('#!'+sys.executable+'\n'+PAID_H_NATIVE); launcher.chmod(0o700)
    root = tmp_path/'instance'
    controller.prepare(root, objective, public, [str(launcher)], max_tokens=300000, max_seconds=60,
        max_steps=6, model='no-model', reasoning_effort='high', experiment_mode='external', node_budget_mode='soft',
        search_policy=dict(search.DEFAULT_POLICY, initial_calls=1),
        native_options=dict(workspace='workspace-write', network=False, web='disabled', subagents=False),
        node_budget_policy={role:dict(max_observed_tokens=300000, max_seconds=60) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn',
            output_repair=True, output_only=True, native_home=str(home)))
    settings = (root/'settings.json').read_bytes()
    native_configuration = {path: path.read_bytes() for path in (home/'auth.json', home/'config.toml')}
    c.build_job(root, 1, 'H', [])
    start_clock(root, 'native field repair fixture')
    native.execute_native(root, 1)

    original, repaired = execution.records(root, 1)
    before = {path: path.read_bytes() for path in (
        original/'science-output.json', original/'native-intent.json', original/'native-outcome.json',
        original/'usage.json', repaired/'science-output.json', repaired/'native-intent.json',
        repaired/'native-outcome.json', repaired/'usage.json')}
    intent = c.read(repaired/'native-intent.json')
    assert 'focus' in intent['output_repair']['diagnostic']
    assert intent.get('work_repair') is None
    assert intent['budget_slice']['previous_tokens'] == 131733
    prompt = (c.workspace(root, 1)/'work/repair-request.txt').read_text()
    objects = [json.loads(line) for line in prompt.splitlines() if line.startswith('{')]
    remaining = next(value for value in objects if 'remaining_observed_tokens' in value)
    assert remaining['remaining_observed_tokens'] == 168267
    assert remaining['previous_consumed_observed_tokens'] == 131733
    assert 0 < remaining['remaining_active_seconds'] <= 60
    assert not {'observed_tokens', 'active_seconds', 'previous_observed_tokens'} & remaining.keys()
    shared = next(value for value in objects if 'exploration' in value)
    assert shared['exploration']['token_usage_unknown'] is True
    assert shared['shared_observed_tokens_remaining'] == 168267
    hint = c.read(c.workspace(root, 1)/'work/repair-budget.json')
    assert hint['observed_tokens_lower_bound'] == 131733
    assert hint['inflight_usage_unknown']
    assert native.usage(root)['observed_tokens'] == 131747
    assert (c.workspace(root, 1)/'work/launches').read_text() == 'exec\nresume\n'
    assert intent['continuation_kind'] == 'repair'
    assert intent['output_repair']['raw_ref'] == c.file_ref(original/'science-output.json')
    assert intent['session']['home'] == str(home)
    assert intent['session']['workspace'] == str(c.workspace(root, 1))
    assert intent['session']['thread_id'] == '11111111-2222-4333-8444-000000000001'
    assert intent['native_profile'] == c.read(original/'native-intent.json')['native_profile']
    for record, name in ((original, 'original-final.json'), (repaired, 'repaired-final.json')):
        outcome = c.read(record/'native-outcome.json')
        assert outcome['budget_notification']['status'] == 'available'
        assert outcome['native_identity']['session']['source'] == 'native_metadata_hooks'
        assert outcome['metering']['coverage'] == 'terminal' and outcome['metering']['reconciliation'] == 'matched'
        raw = (record/'science-output.json').read_bytes()
        assert raw == (c.workspace(root, 1)/'work'/name).read_bytes()
        assert outcome['terminal_evidence']['final'] == dict(sha256=hashlib.sha256(raw).hexdigest(), size_bytes=len(raw))

    assert c.read(original/'science-output.json') == c.read(c.workspace(root, 1)/'work/original-plan.json')
    expected = copy.deepcopy(c.read(original/'science-output.json'))
    assert expected['search_plan']['focus']['index'] == 999
    expected['search_plan']['focus']['index'] = 1
    science = c.validate(c.workspace(root, 1))
    assert c.read(repaired/'science-output.json') == expected
    assert {key: science[key] for key in expected} == expected
    assert science['execution_request']['observed_experiments'] == 0
    worker = c.build_job(root, 2, 'W', [1])
    assert worker['direct_originals'] == [dict(path='inputs/parents/001/conclusion.json',
        size_bytes=(repaired/'science-output.json').stat().st_size)]
    parent = originals.resolve_root_ref(root, dict(worker['direct_refs'][0],
        path=str(c.workspace(root, 2)/worker['direct_refs'][0]['path'])))
    assert c.read(parent) == expected
    assert worker['search']['focus']['action'] == 'screen'
    assert search.launch_reason(root, worker) is None
    assert budget.node_admission(root, worker) is None
    assert not (execution.origin(root, 2)/'native-intent.json').exists()
    assert native.usage(root)['observed_tokens'] == 131747
    assert (root/'settings.json').read_bytes() == settings
    assert all(path.read_bytes() == raw for path, raw in {**before, **native_configuration}.items())
