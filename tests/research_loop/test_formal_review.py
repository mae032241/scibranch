"""Exact review through existing native nodes; scripted transport is not Codex qualification."""
import sys
import json
from pathlib import Path

import jsonschema
import pytest

from scidiscovery.operations.review_rules import independence
from scidiscovery.research_loop import accepted_budget, contracts as c, controller, formal_review as review, native, qualification as q
from test_history_handoff import finish


# This independent local process supplies protocol-shaped native/session events.
# It never calls a model, backend or solver. Its identities are synthetic.
LAUNCHER = r'''import hashlib,json,os,pathlib,shlex,subprocess,sys,tomllib,uuid
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);step=int(work.name)
args=sys.argv[1:]
assert args[-2:]==['app-server','--stdio'],args
assert args[args.index('-C')+1]==str(work)
config={}
def merge(target,update):
 for key,value in update.items():
  if isinstance(value,dict) and isinstance(target.get(key),dict):merge(target[key],value)
  else:target[key]=value
for index,arg in enumerate(args[:-1]):
 if arg=='-c':merge(config,tomllib.loads(args[index+1]))
def send(value):print(json.dumps(value,separators=(',',':')),flush=True)
def receive():
 line=sys.stdin.readline()
 assert line,'Missing app-server request'
 return json.loads(line)
request=receive();assert request['method']=='initialize'
send(dict(id=request['id'],result={}))
assert receive()['method']=='initialized'
request=receive()
if request['method']=='hooks/list':
 assert request['params']==dict(cwds=[str(work)])
 hooks=[]
 for name,key,event in (('PostToolUse','post_tool_use','postToolUse'),
                       ('PostModelRequest','post_model_request','postModelRequest'),
                       ('PostCompact','post_compact','postCompact')):
  declared=config.get('hooks',{}).get(name,[])
  if not declared:continue
  assert len(declared)==1 and len(declared[0]['hooks'])==1
  handler=declared[0]['hooks'][0]
  hooks.append(dict(key='/<session-flags>/config.toml:'+key+':0:0',
   currentHash='sha256:'+hashlib.sha256(json.dumps(handler,sort_keys=True).encode()).hexdigest(),
   displayOrder=0,enabled=True,source='sessionFlags',sourcePath='/<session-flags>/config.toml',
   isManaged=False,trustStatus='untrusted',eventName=event,matcher=None,handlerType=handler['type'],
   command=handler['command'],**{'async':handler['async']},timeoutSec=handler['timeout'],
   additionalContextLimit=handler.get('additionalContextLimit')))
 send(dict(id=request['id'],result=dict(data=[dict(cwd=str(work),hooks=hooks,warnings=[],errors=[])])))
 raise SystemExit(0)
assert request['method'] in ('thread/start','thread/resume')
start=request['params']
assert start['cwd']==str(work) and start['approvalPolicy']=='never'
assert '--dangerously-bypass-hook-trust' in args and start['config']==dict(bypass_hook_trust=True)
assert set(config['hooks'])=={'PostToolUse','PostModelRequest','PostCompact'}
assert config['features']['multi_agent'] is False
anchor='REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE'
thread=str(uuid.uuid5(uuid.NAMESPACE_URL,'formal-native-'+str(2 if step==4 and os.environ.get('REVIEW_REUSE_AUTHOR') else step)))
if request['method']=='thread/resume':
 assert start['threadId']==thread and start['excludeTurns'] is True
else:assert start['allowProviderModelFallback'] is False and start['experimentalRawEvents'] is False
mode=start['sandbox'];sandbox=config.get('sandbox_workspace_write',{})
assert mode==config['sandbox_mode']
policy=dict(type={'read-only':'readOnly','workspace-write':'workspaceWrite','danger-full-access':'dangerFullAccess'}[mode])
if mode!='danger-full-access':policy['networkAccess']=sandbox.get('network_access',False) if mode=='workspace-write' else False
if mode=='workspace-write':
 policy.update(writableRoots=sandbox.get('writable_roots',[]),
  excludeSlashTmp=sandbox.get('exclude_slash_tmp',False),excludeTmpdirEnvVar=sandbox.get('exclude_tmpdir_env_var',False))
send(dict(id=request['id'],result=dict(thread=dict(id=thread,turns=[]),cwd=str(work),model=start['model'],
 approvalPolicy='never',sandbox=policy,reasoningEffort=config.get('model_reasoning_effort'),
 serviceTier=start.get('serviceTier'))))
request=receive();assert request['method']=='turn/start'
turn=request['params'];assert turn['threadId']==thread and turn['model']==start['model']
assert turn['approvalPolicy']=='never' and turn.get('effort')==config.get('model_reasoning_effort')
assert turn.get('serviceTier')==start.get('serviceTier')
assert len(turn['input'])==1 and turn['input'][0]['type']=='text'
prompt=turn['input'][0]['text']
task=json.loads((work/'science-task.json').read_text())
if 'review' in task:
 # The neutral developer instruction crosses the real thread/start control,
 # once, separately from both the user prompt and public science-task packet.
 instruction=start['developerInstructions']
 assert instruction==config['developer_instructions']
 assert isinstance(instruction,str) and instruction
 assert 'instruction' not in task['review']
 assert instruction not in prompt and instruction not in json.dumps(task)
 actual_input=prompt+chr(10)+instruction
 assert actual_input.count(instruction)==1
 for concept in ('exact requested claims','original objective','authorized scope','locatable sources','experiment conditions',
                 'measurements','inferences','assumptions','target metrics','proxies','sensitivity',
                 'conflicting evidence','uncertainty','missing inputs','favorable verdict'):
  assert concept in instruction,concept
 assert 'scientific_judgment' not in task and 'candidate_recommendation' not in task['final_response']
 assert 'history' not in task and 'parents' not in task
 assert anchor not in json.dumps(task) and anchor not in actual_input
 assert not (work/'inputs/history').exists()
 assert not (work/'inputs/parents/002/conclusion.json').exists()
 assert not (work/'inputs/parents/003/conclusion.json').exists()
 declared_inputs=[task['review']['subject'],*task['global_inputs']]
 assert all(anchor.encode() not in (work/path).read_bytes() for path in declared_inputs)
 assert (work/task['review']['subject']).read_text()=='a scientific implementation'
 assert all((work/path).is_file() for path in task['global_inputs'])
 value=dict(verdict='pass',summary='Exact fixture review',scientific_result='No fixture defect observed.',limitations=['Synthetic native transport only.'])
else:
 value=dict(summary='Fixture science',scientific_result='A bounded finding.',limitations=['No real science.'],implementation_selection=None)
 role=task['role']
 if role=='H':value.update(next_action='experiment',route_reason='Implement the question',search_plan=None)
 if role=='W':
  (work/'artifacts/model.txt').write_text('a scientific implementation')
  value.update(stage_intent='decision',mechanism_findings=None,consultation_question=None,candidate_nominations=None)
 if role=='A':
  value.update(next_action='continue_w',route_reason=anchor,candidate_recommendation=None,search_review=None,progress_assessment=None,decision_request=None,portfolio_review=None)
  value.update(summary=anchor,scientific_result=anchor,implementation_selection=['W002-I001'])
home=pathlib.Path(os.environ['CODEX_HOME']);sessions=home/'sessions';sessions.mkdir(parents=True,exist_ok=True)
transcript=sessions/(thread+'.jsonl')
# Appending preserves an overlapping thread's original entity. Headers alone
# confer no proof: the real declared helper must deliver the bound metadata.
missing=step==2 and bool(os.environ.get('REVIEW_MISSING_AUTHOR'))
if not missing:
 with transcript.open('a') as stream:
  stream.write(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
event=dict(cwd=str(work),session_id=thread,hook_event_name='PostModelRequest',status='completed',
 token_usage=dict(input_tokens=10,cached_input_tokens=0,output_tokens=5))
if not missing:event['transcript_path']=str(transcript)
def hook(name,payload):
 handler=config['hooks'][name][0]['hooks'][0]
 result=subprocess.run(shlex.split(handler['command']),input=json.dumps(payload).encode(),
  stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=handler['timeout'],check=True)
 return result.stdout
send(dict(id=request['id'],result=dict(turn=dict(id='turn-one',items=[],status='inProgress'))))
assert hook('PostModelRequest',event)==b''
notice=hook('PostToolUse',dict(cwd=str(work),session_id=thread,hook_event_name='PostToolUse'))
if notice:
 output=json.loads(notice)
 assert set(output)=={'hookSpecificOutput'}
 assert output['hookSpecificOutput']['hookEventName']=='PostToolUse'
 assert set(output['hookSpecificOutput'])=={'hookEventName','additionalContext'}
# Capacity is not reported by this fixture. Context fullness remains unknown;
# billing comes only from the actual PostModelRequest helper delivery above.
send(dict(method='thread/tokenUsage/updated',params=dict(threadId=thread,turnId='turn-one',
 tokenUsage=dict(last=dict(totalTokens=15)))))
send(dict(method='item/completed',params=dict(threadId=thread,turnId='turn-one',
 item=dict(type='agentMessage',text=json.dumps(value),phase='final_answer'))))
send(dict(method='turn/completed',params=dict(threadId=thread,
 turn=dict(id='turn-one',items=[],status='completed'))))
'''


def prepare(tmp_path, launcher, *, enabled=True, steps=6, delivery_policy=None, decision_contract_version=None, role_profiles=None):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Review one exact authored scientific object when useful.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,launcher,max_tokens=2000,max_seconds=60,max_steps=steps,
        model='no-model',reasoning_effort='high',search_policy=None,experiment_mode='external',
        formal_review_policy=dict(version=1) if enabled else None,delivery_policy=delivery_policy,
        node_budget_policy={role:dict(max_observed_tokens=100,max_seconds=5) for role in 'HWAE'} if delivery_policy is not None else None,
        decision_contract_version=decision_contract_version,role_profiles=role_profiles)
    return root


def scripted(tmp_path, monkeypatch, *, reuse=False, missing=False):
    launcher=tmp_path/'codex'
    launcher.write_text('#!'+sys.executable+'\n'+LAUNCHER)
    launcher.chmod(0o700)
    home=tmp_path/'actual-native-home';home.mkdir()
    monkeypatch.setenv('CODEX_HOME',str(home))
    for name,enabled in (('REVIEW_REUSE_AUTHOR',reuse),('REVIEW_MISSING_AUTHOR',missing)):
        if enabled:monkeypatch.setenv(name,'1')
        else:monkeypatch.delenv(name,raising=False)
    # This transport has one observed participant and no native subagents.
    root=prepare(tmp_path,[str(launcher)],role_profiles={'W':dict(subagents=False)})
    # Native source/contributor proofs remain real protocol transport. The
    # host-bound exact request below does not restore ordinary A dispatch.
    for step,role,parents in ((1,'H',[]),(2,'W',[1]),(3,'A',[1,2])):
        c.build_job(root,step,role,parents)
        native.execute_native(root,step)
    return root,home


def exact_request(root, requester_step, producer_step, filename, rationale):
    """Freeze an exact host request without granting authorship or a verdict."""
    requester=c.host_record(c.workspace(root,requester_step))
    producer=c.host_record(c.workspace(root,producer_step))
    seal=c.read(producer/'result-identity.json')
    material=next(item for item in c._public_materials(seal['materials'])
        if item['path'].endswith('/'+filename))
    head=accepted_budget.read(root)
    return q._save(root,requester/'formal-review-request.json',dict(
        contract=review.contract(root),question=review.QUESTION,requester_rationale=rationale,
        requester=dict(step=requester_step,seal=q._ref(root,requester/'result-identity.json')),
        prefix=head['prefix'],breadth_snapshot=head['search_ref'],
        subject=dict(producer_step=producer_step,producer_seal=q._ref(root,producer/'result-identity.json'),material=material),
        authors=review.authors(root,producer_step),unresolved_selection=None,reason=None))


def formal_job(root, reference, producer_step):
    return c.build_job(root,4,'A',[producer_step],instruction=review.QUESTION,
        evidence_return=dict(kind='formal_review',request=reference))


def test_independence_needs_every_verified_participant():
    assert independence({('engine','author')},{('engine','reviewer')})=='independent'
    assert independence({('engine','author')},{('engine','reviewer'),('engine','author')})=='overlap'
    assert independence(set(),{('engine','reviewer')},authors_complete=False)=='unknown'


def test_formal_review_scientific_prose_has_no_minimum_length_and_keeps_verdict_contract():
    schema = review.schema()
    value = dict(verdict='inconclusive',summary='',scientific_result='',limitations=[])
    jsonschema.validate(value,schema)
    for field in ('summary','scientific_result'):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({**value,field:None},schema)
        missing = {key:item for key,item in value.items() if key != field}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(missing,schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**value,'verdict':'looks good'},schema)


@pytest.mark.process_e2e
@pytest.mark.parametrize('reuse',[False,True])
def test_native_exact_review_binds_actual_authors_and_home(tmp_path,monkeypatch,reuse):
    root,home=scripted(tmp_path,monkeypatch,reuse=reuse)
    reference=exact_request(root,3,2,'model.txt','REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE')
    job=formal_job(root,reference,2)
    native.execute_native(root,4)
    record=c.host_record(c.workspace(root,4))
    assert job['role']=='A' and job['purpose']=='formal_review'
    request=c.read(root/job['formal_review']['path'])
    assert request['requester_rationale']=='REQUESTER_INSISTS_PASS_WITHOUT_COUNTEREVIDENCE'
    assert request['question']==job['instruction']==review.QUESTION
    assert job['direct_refs']==[] and len(job['direct_materials'])==1
    assert job['selected_inputs'][0]['origin_step']==2
    assert set(c.read(record/'science-output.json'))=={'verdict','summary','scientific_result','limitations'}
    seal=c.read(record/'result-identity.json');proof=c.read(root/seal['formal_review']['path'])
    assert proof['verdict']=='pass' and proof['subject']['producer_step']==2
    own=c.read(root/seal['native_authorship']['path'])
    assert own['complete'] and own['attempts'][0]['session']['home']==str(home)
    assert own['attempts'][0]['proof_kind']=='native_app_server_hooks_v1'
    outcome=c.read(record/'native-outcome.json')
    assert outcome['native_identity']['session']['source']=='native_metadata_hooks'
    assert outcome['budget_notification']==dict(status='available',mechanism='codex_post_tool_use')
    assert outcome['native_thread_event']['rpc_method']=='thread/start'
    assert outcome['native_thread_event']['requested_profile']==outcome['native_thread_event']['effective_profile']
    assert home!=root/'native-homes/004'
    subject_seal=c.read(c.host_record(c.workspace(root,2))/'result-identity.json')
    source=c.read(root/subject_seal['source_provenance']['path'])
    diagnostic=dict(independence=proof['independence'],status=proof['status'],
        authors={key:proof['authors'].get(key) for key in ('complete','reason')},
        reviewer={key:proof['reviewer'].get(key) for key in ('complete','reason')},
        source_missing_reason=source.get('missing_reason'),
        managed_input_coverage=[dict(complete=row.get('complete'),reason=row.get('reason'))
            for row in (source.get('managed_input_coverage') or [])])
    if reuse:
        assert proof['independence']=='overlap' and proof['status']=='not_qualified',diagnostic
        with pytest.raises(ValueError,match='independent sealed pass'):
            review.require_review(root,proof['subject'],seal['formal_review'])
    else:
        assert proof['independence']=='independent' and proof['status']=='passed',diagnostic
        assert review.require_review(root,proof['subject'],seal['formal_review'])==seal['formal_review']
        wrong=dict(proof['subject'],material={**proof['subject']['material'],'source':str(root/'same-bytes-other-origin')})
        with pytest.raises(ValueError,match='Exact subject'):
            review.require_review(root,wrong,seal['formal_review'])
    originals={path:path.read_bytes() for path in (root/'jobs/004.json',record/'science-output.json',record/'result-identity.json')}
    c._validate_inputs(root,c.workspace(root,4),job)
    assert all(path.read_bytes()==data for path,data in originals.items())


@pytest.mark.process_e2e
def test_unknown_native_identity_keeps_usage_and_science_without_qualification(tmp_path,monkeypatch):
    root,_=scripted(tmp_path,monkeypatch,missing=True)
    author=c.read(c.host_record(c.workspace(root,2))/'native-outcome.json')
    assert author['native_identity']['session'] is None
    assert author['budget_notification']['status']=='available'
    assert c.validate(c.workspace(root,2))['scientific_result']=='A bounded finding.'
    assert review.authors(root,2)['complete'] is False
    assert not (root/'jobs/004.json').exists()


@pytest.mark.parametrize('launcher',['codex','unsupported-launcher'])
def test_ordinary_a_cannot_dispatch_formal_review(tmp_path,launcher):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=prepare(tmp_path,[launcher])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'scientific implementation'})
    job=c.build_job(root,3,'A',[1,2])
    assert job['formal_review_choices']['launcher_supported'] is (launcher=='codex')
    assert c.result_schema('A',**c.response_options(job))['properties']['next_action']['enum']==['continue_w']
    with pytest.raises(OutputRepairRequired,match='Correct next_action'):
        finish(root,3,'A',[1,2],action='formal_review',selection=['W002-I001'])
    record=c.host_record(c.workspace(root,3))
    assert not (record/'result-identity.json').exists()
    assert not (record/'formal-review-request.json').exists()
    assert review.unavailable(root,3) is None


def test_ordinary_a_and_pass_text_do_not_gain_formal_qualification(tmp_path):
    root=prepare(tmp_path,['fixture'],enabled=False)
    finish(root,1,'H',[],action='analyze')
    _,_,record=finish(root,2,'A',[1],text='pass: independent and ready to execute')
    assert 'formal_review' not in c.read(record/'result-identity.json')
    assert 'formal_review' not in c.result_schema('A')['properties']['next_action']['enum']
    assert set(review.schema()['properties'])=={'verdict','summary','scientific_result','limitations'}
    ordinary=c.build_job(root,3,'A',[2],instruction='Ordinary scientific continuation')
    assert ordinary['direct_refs'][0]['path']=='inputs/parents/002/conclusion.json'
    c._validate_inputs(root,c.workspace(root,3),ordinary)
    original=c.read(record/'result-identity.json')['raw_ref']
    assert ordinary['direct_refs'][0]['size_bytes']==original['size_bytes']
    assert Path(original['path'])==record/'science-output.json'
    assert c.read(Path(original['path']))['scientific_result']=='pass: independent and ready to execute'
    assert not (c.workspace(root,3)/ordinary['direct_refs'][0]['path']).exists()


def test_review_default_packet_keeps_exact_source_without_requester_arguments(tmp_path,monkeypatch):
    from scidiscovery.research_loop import qualification as q
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='experiment')
    finish(root,2,'W',[1],files={'model.txt':'exact scientific claim','other.txt':'unselected material'})
    anchor='REQUESTER_FAVORS_PASS_AND_DISMISSES_ALL_LIMITATIONS'
    _,_,record=finish(root,3,'A',[1,2],text=anchor,summary=anchor)
    job=formal_job(root,exact_request(root,3,2,'model.txt',anchor),2)
    # Exact review material production supplies a sparse known seal/job view.
    # Qualification completes only its missing control locators and cannot
    # reopen the already loaded scientific producer originals or its admission.
    from scidiscovery.research_loop import accepted_budget,accepted_prefix
    known={}
    binding=review.input_binding(root,job['formal_review'],sources=known)
    head=accepted_budget.read(root);base=c.settings(root)
    original_read=c.read
    original_ref=q._ref
    seal_probes=[]
    producer_originals={root/'jobs/002.json',root/'native-evidence/002/result-identity.json'}
    rereads=[]
    def counted_read(path):
        if Path(path) in producer_originals:rereads.append(path)
        return original_read(path)
    def counted_ref(instance,path,**options):
        if Path(path)==root/'native-evidence/002/result-identity.json':seal_probes.append(Path(path))
        return original_ref(instance,path,**options)
    with monkeypatch.context() as observed:
        observed.setattr(c,'read',counted_read)
        observed.setattr(q,'_ref',counted_ref)
        observed.setattr(accepted_prefix,'reconcile',lambda *args,**kwargs:pytest.fail('Qualification must not renew producer admission'))
        facts=q.input_facts(root,job,base=base,head=head,sources=known,review_binding=binding)
    assert rereads==[] and seal_probes==[root/'native-evidence/002/result-identity.json']
    assert 'seal_path' in facts['producers'][2] and 'raw_path' in facts['producers'][2]
    assert [edge['step'] for edge in facts['producer_edges']]==[2]
    with monkeypatch.context() as missing:
        missing.setattr(review,'input_binding',lambda *args,**kwargs:pytest.fail('Missing explicit binding must not lazily reload review inputs'))
        with pytest.raises(q.IntegrityError,match='explicit review binding'):
            q.input_facts(root,job,base=base,head=head,sources={})
    work=c.workspace(root,4);c._validate_inputs(root,work,job)
    assert job['direct_refs']==[] and len(job['direct_materials'])==1
    assert set(job['history'])=={'prefix','breadth_snapshot','inputs','view'}
    assert job['history']==binding['history']
    assert job['history']['inputs']==[] and job['history']['view']=={}
    view=c.read(work/'science-task.json')
    assert not {'history','parents','lineage','research'} & set(view)
    assert view['review']['subject']==job['direct_materials'][0]['path']
    assert view['global_inputs']==[row['path'] for row in job['global_materials']]
    assert not (work/'inputs/history').exists() and not (work/'inputs/parents/003').exists()
    assert all(anchor.encode() not in Path(row['source']).read_bytes()
        for row in [*job['global_materials'],*job['direct_materials']])
    assert anchor in c.read(record/'science-output.json')['scientific_result']
    source=q._read(root,job['source_provenance'])
    assert [edge['step'] for edge in source['edges'] if edge['kind']=='producer']==[2]
    assert not any(edge['kind']=='unknown' for edge in source['edges'])
    changed=dict(job,selected_inputs=[dict(job['selected_inputs'][0],origin_step=1)])
    retained={path:path.read_bytes() for path in (
        root/'jobs/004.json',work/'science-task.json',root/job['source_provenance']['path'])}
    with monkeypatch.context() as rejected:
        rejected.setattr(c,'load_validated_input_facts',lambda *args,**kwargs:
            pytest.fail('Canonical mismatch must reject before consuming polluted scientific inputs'))
        with pytest.raises(ValueError,match='Scientific inputs differ from their accepted canonical job'):
            c._validate_inputs(root,work,changed)
    assert all(path.read_bytes()==contents for path,contents in retained.items())


def test_review_subject_from_requester_keeps_artifact_but_not_its_conclusion(tmp_path):
    from scidiscovery.research_loop import accepted_budget, qualification as q
    root=prepare(tmp_path,['codex'])
    finish(root,1,'H',[],action='analyze')
    anchor='REQUESTER_SELF_ASSESSMENT_MUST_NOT_BE_DEFAULT_CONTEXT'
    _,_,record=finish(root,2,'A',[1],text=anchor,files={'subject.txt':'the exact subject','unrelated.txt':anchor})
    seal=q._ref(root,record/'result-identity.json')
    materials=c._public_materials(c.read(record/'result-identity.json')['materials'])
    material=next(item for item in materials if item['path'].endswith('/subject.txt'))
    head=accepted_budget.read(root)
    # A host-bound exact request fixture, not an independence/production qualification.
    reference=q._save(root,record/'formal-review-request.json',dict(contract=review.contract(root),question=review.QUESTION,
        requester_rationale=anchor,requester=dict(step=2,seal=seal),
        prefix=head['prefix'],breadth_snapshot=head['search_ref'],
        subject=dict(producer_step=2,producer_seal=seal,material=material),
        authors=dict(complete=False,participants=[]),unresolved_selection=None,reason=None))
    back=dict(kind='formal_review',request=reference)
    job=c.build_job(root,3,'A',[2],instruction=review.QUESTION,evidence_return=back)
    work=c.workspace(root,3);c._validate_inputs(root,work,job)
    assert job['direct_materials']==[material] and job['direct_refs']==[]
    assert Path(material['source']).read_text()=='the exact subject'
    assert not (work/material['path']).exists()
    assert not (work/'inputs/parents/002/conclusion.json').exists()
    assert not (work/'inputs/parents/002/artifacts/unrelated.txt').exists()
    assert not (work/'inputs/history').exists()
    assert controller._check_job(root,c.settings(root),3,'A',[2],review.QUESTION,back)==job


def compact_identity_fixture():
    """Synthetic authentic shape, never a replacement session_meta header."""
    thread='11111111-2222-4333-8444-555555555555'
    session=dict(source='native_metadata_hooks', thread_id=thread, home='/native',
        workspace='/workspace', path='/native/sessions/original.jsonl', identity=[1,2], offset=100)
    event=dict(type='thread.started', thread_id=thread)
    outcome=dict(thread_id=thread, native_identity=dict(backend='codex',
        participant_scope='codex_exec_single_agent_v1',session=session), native_thread_event=event,
        budget_notification=dict(status='available'))
    row=dict(attempt=0, proof_kind='native_metadata_hooks_v1',intent={'path':'intent'},
        outcome={'path':'outcome'}, session=dict(session,thread_event=event))
    metadata=dict(version=1,kind='native_metadata_hooks_v1',intent=row['intent'],outcome=row['outcome'],session=row['session'])
    return row,metadata,outcome,dict(task_ref={'path':'job'}),dict(task_ref={'path':'job'})


def test_compact_native_authorship_proof_keeps_exact_session_and_attempt():
    row,metadata,outcome,intent,process=compact_identity_fixture()
    assert review._metadata_participant(row,metadata,outcome,intent,process,'/workspace',{'path':'intent'}) == (
        'codex_exec','11111111-2222-4333-8444-555555555555')
    assert 'header' not in row and 'header' not in metadata


@pytest.mark.parametrize('mutation',['borrowed_thread','borrowed_workspace','borrowed_process','unqualified_hook','replaced_receipt'])
def test_compact_native_authorship_rejects_borrowed_or_unqualified_facts(mutation):
    import copy
    row,metadata,outcome,intent,process=copy.deepcopy(compact_identity_fixture())
    if mutation=='borrowed_thread':outcome['native_thread_event']['thread_id']='another'
    elif mutation=='borrowed_workspace':outcome['native_identity']['session']['workspace']='/another'
    elif mutation=='borrowed_process':process['task_ref']={'path':'another-job'}
    elif mutation=='unqualified_hook':outcome['budget_notification']['status']='unavailable'
    else:metadata['outcome']={'path':'another-outcome'}
    with pytest.raises(ValueError,match='compact native identity'):
        review._metadata_participant(row,metadata,outcome,intent,process,'/workspace',{'path':'intent'})
