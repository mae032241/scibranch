"""Pre-live gate: latest frozen settings, real launcher/bwrap/controller/delivery, zero models."""
import hashlib
import json
import os
import runpy
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from scidiscovery.research_loop import audit_recovery,budget,contracts as c,controller,cov_delivery,cov_lineage,delivery,search
from scidiscovery.research_loop.protocol import CURRENT
from execution_fixtures import execution_registry
from test_cov_delivery_lineage import managed_scene
from test_delivery import FAKE_HARNESS,accepted,closeout_decision,delivery_policy,descriptor

REPO=Path(__file__).resolve().parents[2];GATE=REPO/'scripts/acceptance/prelive_gate.py'
LATEST=Path(os.environ.get('SCID_PRELIVE_SETTINGS',REPO.parents[1]/'.trial/rootless-task-unit-cov-20261014/instance/settings.json'))


def latest():
    if shutil.which('bwrap') is None:pytest.skip('Actual namespace unavailable')
    if not LATEST.is_file():pytest.skip('Latest frozen real-run settings unavailable')
    source_protocol=c.read(LATEST).get('handoff_protocol')
    if source_protocol!=CURRENT:pytest.skip(f'Exact frozen prelive source protocol {source_protocol} is not current {CURRENT}')
    launcher=LATEST.parent.parent/'launcher.json'
    if not Path(c.read(launcher)['task_python']).is_file():pytest.skip('Frozen task interpreter unavailable')
    return LATEST,launcher


def gate(tmp_path,settings,launcher,*extra):
    output=tmp_path/'verdict.json'
    result=subprocess.run([sys.executable,'-I',str(GATE),'--settings',str(settings),'--launcher-config',str(launcher),
        '--work',str(tmp_path/'scene'),'--output',str(output),*extra],capture_output=True,text=True,timeout=900)
    assert output.is_file(),(result.stdout[-2000:],result.stderr[-4000:])
    assert output.stat().st_size<=64*1024
    return result,c.read(output)


def originals(*paths):
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


@pytest.mark.process_e2e
@pytest.mark.parametrize('comparison',[False,True])
@pytest.mark.parametrize('audit_status',[None,'audited','unknown'])
def test_fake_closeout_author_uses_current_delivery_contract(tmp_path,comparison,audit_status):
    """Exercise the actual gate native program even without an installed lane."""
    work=tmp_path/'work';(work/'inputs/parents').mkdir(parents=True)
    sources=dict(items=[dict(candidate_id=cid,availability='available',audit=dict(qualification='not_observed'))
        for cid in ('run-1','run-2')],omitted_count=0)
    closeout=dict(sources=sources)
    if comparison:
        closeout['comparison']=dict(rows=[dict(candidate_id=cid) for cid in ('run-1','run-2')])
    candidate_page='inputs/history/candidates/0001.json'
    c.write(work/candidate_page,dict(candidates=[dict(id=cid,label=cid,metric=.5,direction='max',
        evaluation_key='fixture-public',status='ok') for cid in ('run-1','run-2')],next_page=None,omitted_count=0))
    task=dict(role='A',closeout=closeout,lineage=dict(indexes=dict(candidates=candidate_page)))
    if audit_status is not None:
        task['candidate_audit']=dict(candidate_id='run-1',status=audit_status,
            qualification='passed' if audit_status=='audited' else 'unknown')
    c.write(work/'science-task.json',task)
    schema=tmp_path/'science-result.schema.json';c.write(schema,c.result_schema('A',CURRENT,comparison,final_only=True,final_delivery=True))
    program=tmp_path/'fake-native.py';program.write_text(runpy.run_path(str(GATE))['FAKE_NATIVE'])
    output=work/'result.pending.json'
    result=subprocess.run([sys.executable,str(program),'-C',str(work),'--output-schema',str(schema),
        '--output-last-message',str(output)],input='',text=True,capture_output=True,timeout=10,
        env={**os.environ,'CODEX_HOME':str(tmp_path/'native-home')})
    if audit_status=='unknown':
        assert result.returncode!=0 and not output.exists()
        return
    assert result.returncode==0,result.stderr
    payload=c.science_payload(output,'A',CURRENT,comparison,final_only=True,final_delivery=True)
    assert payload['next_action']=='deliver'
    assert payload['candidate_recommendation']==('run-1' if audit_status else 'run-2')
    explanation=payload['selection_explanation']
    assert explanation['criteria'].strip() and 'exact public fixture candidates' in explanation['criteria']
    assert 'preserving original checks; no scientific qualification is asserted' in explanation['criteria']
    assert explanation['criteria_change']==dict(status='unchanged',prior_criteria=None,changes=None,evidence=None,accepted_tradeoffs=None)
    if comparison:
        assert [row['candidate_id'] for row in payload['candidate_comparison']['rows'] if row['decision']=='keep']==[payload['candidate_recommendation']]
        assert payload['candidate_comparison']['rationale'].strip()
        assert payload['candidate_comparison']['evidence_steps']==[2]


@pytest.mark.process_e2e
def test_fake_final_choice_reads_full_index_when_source_preview_is_empty(tmp_path):
    work=tmp_path/'work';(work/'inputs/parents').mkdir(parents=True)
    first='inputs/public/first-candidates.json';last='inputs/public/final-candidates.json'
    c.write(work/first,dict(candidates=[dict(id='retained-first-fixture')],next_page=last,omitted_count=1))
    c.write(work/last,dict(candidates=[dict(id='retained-explicit-fixture')],next_page=None,omitted_count=0))
    c.write(work/'science-task.json',dict(role='A',closeout=dict(sources=dict(items=[],omitted_count=2)),
        lineage=dict(indexes=dict(candidates=first))))
    schema=tmp_path/'science-result.schema.json';c.write(schema,c.result_schema('A',CURRENT,final_only=True,final_delivery=True))
    program=tmp_path/'fake-native.py';program.write_text(runpy.run_path(str(GATE))['FAKE_NATIVE'])
    output=work/'result.pending.json'
    def invoke():
        return subprocess.run([sys.executable,str(program),'-C',str(work),'--output-schema',str(schema),
            '--output-last-message',str(output)],input='',text=True,capture_output=True,timeout=10,
            env={**os.environ,'CODEX_HOME':str(tmp_path/'native-home')})
    result=invoke()
    assert result.returncode==0,result.stderr
    payload=c.science_payload(output,'A',CURRENT,final_only=True,final_delivery=True)
    assert payload['next_action']=='deliver' and payload['candidate_recommendation']=='retained-explicit-fixture'
    assert 'control must validate its exact originals and perform the host audit' in payload['route_reason']
    assert 'no scientific qualification is asserted' in payload['selection_explanation']['criteria']
    # Missing exact next-page originals cannot fall back to a guessed candidate.
    output.unlink();(work/last).unlink()
    failed=invoke()
    assert failed.returncode!=0 and not output.exists()
    assert 'FileNotFoundError' in failed.stderr and last in failed.stderr


@pytest.mark.process_e2e
def test_latest_frozen_settings_drive_full_chain_to_confirmed_delivery(tmp_path):
    settings,launcher=latest();before=originals(settings,launcher)
    result,verdict=gate(tmp_path,settings,launcher)
    assert result.returncode==0 and verdict['passed'],(verdict.get('error'),{k:v for k,v in verdict['checks'].items() if not v},verdict.get('reason'))
    assert verdict['status']=='delivery_confirmed' and verdict['models_called']==0
    assert verdict['adopted_defaults']==[]
    if c.read(settings).get('selection_policy') is None:
        assert verdict['checks']['selection_disabled_preserved']
    assert verdict['source_protocol']==verdict['current_protocol']==CURRENT and verdict['settings_differences']==[]
    assert [(row['role'],row['purpose'],row['route']) for row in verdict['chain']]==[('H',None,'experiment'),('W',None,'review'),
        ('A',None,'audit'),('A',None,'continue_w'),('W',None,'review'),('A',None,'finish'),('A','closeout','deliver')]
    assert verdict['chain'][4]['fragments']==2 and verdict['usage']['unknown_steps']==[]
    assert originals(settings,launcher)==before


def source_prelive(tmp_path,comparison):
    """Fresh declared synthetic inputs; never upgrades the external frozen run."""
    from test_author_closeout_e2e import source_python
    from test_worker_execution_hash_free import PUBLIC_CLI,PUBLIC_RUNNER
    if shutil.which('bwrap') is None:pytest.skip('Actual namespace unavailable')
    python,bridge,runtime_paths=source_python(tmp_path,REPO)
    shim=tmp_path/'source-runtime/gate-python'
    allowed=[str(GATE),str(REPO/'scripts/acceptance/rootless_cov_launcher.py'),
        str(Path(cov_delivery.__file__).resolve()),str(Path(cov_lineage.__file__).resolve()),str(tmp_path/'scene/fake-codex')]
    # Adapt only these fixture script entry points to the existing source bridge.
    shim.write_text('#!'+python+'\nimport os,sys\n'
        'args=sys.argv[1:]\n'
        f'bridge={bridge!r}\nallowed={allowed!r}\n'
        'script_args=list(args)\n'
        'while script_args and script_args[0] in ("-B","-I"):script_args.pop(0)\n'
        'if script_args and script_args[0] in allowed:\n'
        f' code="import runpy,sys;sys.executable={str(shim)!r};runpy.run_path(sys.argv.pop(1),run_name=\'__main__\')"\n'
        ' os.execv(bridge,[bridge,"-B","-I","-c",code,*script_args])\n'
        'os.execv(bridge,[bridge,*args])\n')
    shim.chmod(0o555)
    source=tmp_path/'source';public=source/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    root=source/'instance'
    (public/'instruction.md').write_text('Synthetic registered transport and exact delivery; no scientific efficacy claim.')
    (public/'task.toml').write_text('fixture=true\n');(public/'environment/Dockerfile').write_text('FROM fixture\n')
    header,dispatch=PUBLIC_CLI.split('parser=',1)
    (harness/'exp_cli.py').write_text(header+'if len(sys.argv)>1 and sys.argv[1]=="run":\n'
        +textwrap.indent('parser='+dispatch,' ')+'\nelse:\n'+textwrap.indent(FAKE_HARNESS,' '))
    # The public fixture records an inert artifact; it neither trains nor loads a model.
    runner=PUBLIC_RUNNER.replace('child=subprocess.run([sys.executable,str(source)],capture_output=True,text=True)',
        'child=type("Transport",(),dict(returncode=0,stdout="",stderr=""))()')
    assert runner!=PUBLIC_RUNNER
    (harness/'runner.py').write_text(runner)
    for name in ('train.csv','val.csv'):(harness/name).write_text('public transport fixture only\n')
    c.write(harness/'meta.json',dict(mode='ok',control_path=str(root/'settings.json'),hidden_path=str(source/'private-never-mounted')))
    names=['task.toml','instruction.md','environment/Dockerfile',
        *('environment/harness/'+name for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json'))]
    manifest=source/'public-manifest.json'
    c.write(manifest,dict(task_revision='d78f5eb52ad754c5ee9154741af73130a85a65b8',
        public_files_sha256={name:c.digest(public/name) for name in names}))
    codehost=Path('/usr/bin/true').resolve();config=source/'launcher.json'
    c.write(config,dict(scene_root=str(root),public_root=str(public),public_manifest=str(manifest),
        runs_dir=str(root/'runs'),submission_dir=str(root/'submission'),native=str(codehost),
        code_mode_host=dict(path=str(codehost),sha256=c.digest(codehost)),task_python=python,scid_python=str(shim),
        runtime_paths=runtime_paths,model='no-model',reasoning_effort='high',profile='prelive-no-model',
        cov_receipt_channel='public_runner_main'))
    adapter=descriptor(root,public,python=python)
    adapter['config']['namespace_runs']=str(root/'runs')
    adapter['inputs']=[dict(label='harness/'+name,path=str(harness/name))
        for name in ('exp_cli.py','runner.py','train.csv','val.csv','meta.json')]+[dict(label='runtime-python',path=python)]
    controller.prepare(root,public/'instruction.md',public,
        [str(shim),str(REPO/'scripts/acceptance/rootless_cov_launcher.py'),'--config',str(config),'--'],
        max_tokens=100000,max_seconds=300,max_steps=20,model='no-model',reasoning_effort='high',
        search_policy=search.DEFAULT_POLICY,candidate_audit_policy={'version':1},
        node_budget_policy={role:dict(max_observed_tokens=10000,max_seconds=60) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn'),
        worker_fragment_policy=dict(context='new_context'),selection_policy=dict(version=2) if comparison else None,
        source_restrictions=[dict(source=str(harness/'train.csv'),sha256=c.digest(harness/'train.csv'),restriction='clear')],
        delivery_policy=delivery_policy(adapter),
        history_observer=dict(contract_version=2,adapter=str(Path(cov_lineage.__file__).resolve()),
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    return root/'settings.json',config,shim


def source_gate_failure(tmp_path,verdict,result):
    """Read exact retained failure originals before the runner removes its tmpdir."""
    def show(label,value):
        document=json.dumps(value,ensure_ascii=False,indent=2,default=str)
        assert len(document.encode())<=24*1024,'Prelive diagnostic exceeded its explicit 24 KiB bound: '+label
        print(label+'\n'+document)
    def tail(path,limit=2048):
        if not path.is_file():return None
        with path.open('rb') as stream:
            stream.seek(max(0,path.stat().st_size-limit))
            return stream.read(limit).decode(errors='replace')
    show('Complete prelive verdict',verdict)
    detail=dict(stdout=result.stdout[-2048:],stderr=result.stderr[-2048:])
    last=next((row for row in reversed(verdict.get('chain',[])) if row.get('sealed') is False),None)
    if last is not None:
        from scidiscovery.research_loop import execution
        root=tmp_path/'scene/instance';step=last['step'];work=c.workspace(root,step)
        job=c.read(root/'jobs'/f'{step:03d}.json');record=execution.record(root,step)
        detail.update(step=step,record=str(record.relative_to(root)),job={name:job.get(name) for name in (
            'step','role','purpose','final_only','handoff_protocol','scientific_response_fields')})
        task=c.read(work/'science-task.json')
        lineage=task.get('lineage') or {};indexes=lineage.get('indexes') or {}
        navigation=dict(indexes=indexes,candidates=lineage.get('candidates'))
        for name in ('candidates','experiments'):
            index=indexes.get(name)
            if isinstance(index,str) and not Path(index).is_absolute() and '..' not in Path(index).parts:
                navigation[name+'_page']=tail(work/index,2048)
        show('Exact frozen public candidate navigation',navigation)
        detail['job']['instruction']=str(job.get('instruction',''))[:2048]
        detail['originals']={name:tail(record/name) for name in (
            'science-output.json','result.pending.json','native.stderr','runner.stderr','native-outcome.json','native-intent.json')}
        try:
            c.validate(work)
        except Exception as error:
            detail['validate_error']=dict(type=type(error).__name__,message=str(error)[:2048])
        raw=record/'science-output.json'
        if raw.is_file():
            try:
                c.science_payload(raw,job['role'],job['handoff_protocol'],bool(job.get('selection')),**c.response_options(job))
            except Exception as error:
                detail['science_payload_error']=dict(type=type(error).__name__,message=str(error)[:2048])
        from scidiscovery.research_loop.experiment_client import record_directory
        from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
        response=work/SUBMISSION_DIRECTORY/'response.json'
        if response.is_file():
            reply=c.read(response);call=(reply.get('result') or {}).get('call_id')
            if isinstance(call,str) and call and Path(call).name==call and call not in {'.','..'}:
                directory=record_directory(root,step)/'calls'/call
                accepted=c.read(directory/'accepted.json') if (directory/'accepted.json').is_file() else {}
                show('Exact current public call transport',dict(call_id=call,
                    accepted={name:accepted.get(name) for name in ('source_script','cwd','command','receipt_transport')},
                    originals={name:tail(directory/name,1024) for name in (
                        'terminal.json','completed.json','stderr.raw','stdout.raw','receipt.json','backend-science/inputs/invocation.json')}))
    show('Exact last unsealed prelive originals',detail)


@pytest.mark.process_e2e
@pytest.mark.parametrize('comparison',[False,True])
def test_source_normal_gate_uses_one_final_author_and_original_host_delivery(tmp_path,execution_registry,comparison):
    settings,launcher,python=source_prelive(tmp_path,comparison)
    before=originals(settings,launcher)
    output=tmp_path/'verdict.json'
    result=subprocess.run([str(python),str(GATE),'--settings',str(settings),'--launcher-config',str(launcher),
        '--work',str(tmp_path/'scene'),'--output',str(output)],capture_output=True,text=True,timeout=900)
    assert output.is_file(),(result.stdout[-2000:],result.stderr[-4000:])
    assert output.stat().st_size<=64*1024
    verdict=c.read(output)
    if result.returncode!=0 or not verdict['passed']:
        source_gate_failure(tmp_path,verdict,result)
    assert result.returncode==0 and verdict['passed'],'Prelive failed; complete bounded diagnostics are in captured stdout'
    assert verdict['status']=='delivery_confirmed' and verdict['models_called']==verdict['model_requests']==0
    assert verdict['source_protocol']==verdict['current_protocol']==CURRENT
    assert verdict['adopted_defaults']==[] and verdict['settings_differences']==[]
    assert len(verdict['chain'])==7 and verdict['chain'][-1]['role']=='A'
    assert verdict['chain'][-1]['purpose']=='closeout' and verdict['chain'][-1]['route']=='deliver'
    for check in ('source_provenance_equal_after_rebind','one_final_author','original_delivery_actions','final_host_audit_original','final_host_leaf_output',
        'submission_bytes_match','source_unchanged','selection_comparison_sealed' if comparison else 'selection_disabled_preserved'):
        assert verdict['checks'][check],check
    assert originals(settings,launcher)==before


@pytest.mark.parametrize('mutation',['restriction','source_bytes'])
def test_source_provenance_rebinding_rejects_self_consistent_changed_facts(tmp_path,mutation):
    roots=[];publics=[]
    for index in range(2):
        public=tmp_path/f'public-{index}';public.mkdir();publics.append(public)
        objective=public/'objective.md';objective.write_text('Exact source facts remain distinct from qualification.')
        source=public/'declared.txt';source.write_text('changed original' if index==1 and mutation=='source_bytes' else 'exact original')
        root=tmp_path/f'instance-{index}';roots.append(root)
        controller.prepare(root,objective,public,['fake-no-launch'],max_tokens=1000,max_seconds=30,max_steps=4,
            model='no-model',reasoning_effort='high',search_policy=None,
            source_restrictions=[dict(source=str(source),sha256=c.digest(source),
                restriction='restricted' if index==1 and mutation=='restriction' else 'clear')])
        reference=c.settings(root)['source_provenance']
        assert c.digest(root/reference['path'])==reference['sha256']  # Both outer proofs are self-consistent.
    equal=runpy.run_path(str(GATE))['source_provenance_equal']
    assert not equal(roots[0],roots[1],[(str(roots[0]),str(roots[1])),(str(publics[0]),str(publics[1]))])


@pytest.mark.process_e2e
def test_failed_native_turn_is_a_retained_stop_without_seal_replay_or_delivery(tmp_path):
    # rootless-receipt-cov-20261014b: A005 turn.failed (service routing), reported as native_stopped.
    settings,launcher=latest()
    result,verdict=gate(tmp_path,settings,launcher,'--fault','a_turn_failed')
    assert result.returncode==0 and verdict['passed'],verdict['checks']
    assert verdict['status']=='native_stopped' and verdict['chain'][-1]=={'step':3,'role':'A','purpose':None,'sealed':False}


@pytest.mark.process_e2e
def test_polluted_public_harness_fails_before_any_scene_or_launch(tmp_path):
    # rootless-receipt-cov-20261014: a host smoke wrote harness/__pycache__ into the shared public copy.
    settings,launcher=latest();config=c.read(launcher)
    source=tmp_path/'source';(source/'instance').mkdir(parents=True)
    for name in ('settings.json','settings-identity.json'):shutil.copyfile(settings.parent/name,source/'instance'/name)
    public=tmp_path/'public'
    for name in c.read(Path(config['public_manifest']))['public_files_sha256']:
        (public/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(Path(config['public_root'])/name,public/name)
    (public/'environment/harness/__pycache__').mkdir();(public/'environment/harness/__pycache__/runner.cpython-312.pyc').write_bytes(b'host bytecode')
    c.write(source/'launcher.json',dict(config,public_root=str(public)))
    result,verdict=gate(tmp_path,source/'instance/settings.json',source/'launcher.json')
    assert result.returncode==1 and not verdict['passed']
    assert verdict['checks']['source_launcher_preflight'] is False
    assert verdict['preflight_error']=='public_directory_whitelist_changed'
    assert not (tmp_path/'scene/instance').exists() and 'status' not in verdict


def _leaf_files(root,phase,count=1):
    indexes=sorted((root/'host-delivery/leaf-output').glob(f'*-{phase}-*.json'))
    assert len(indexes)==count,indexes
    for path in indexes:
        index=c.read(path);stream=index['streams']['stderr']
        data=(path.parent/stream['path']).read_bytes()
        assert stream['sha256']==hashlib.sha256(data).hexdigest() and stream['kept_bytes']==len(data)
        yield index,data


@pytest.mark.parametrize('phase',['resolve','audit','promote','confirm'])
def test_isolated_leaf_failure_keeps_exact_traceback_as_host_only_output(tmp_path,monkeypatch,phase):
    # U4: cov_delivery leaf stderr was discarded, so the closeout traceback had to be inferred.
    root,nodes,first,*_=managed_scene(tmp_path)
    original=delivery._call
    def tamper(spec,request,seconds,**kwargs):
        if request['phase']==phase:
            protocol=Path(request['stage'])/'inputs/scid-current-protocol'
            protocol.chmod(0o600);protocol.write_bytes(protocol.read_bytes()+b'\n# altered frozen original\n')
        return original(spec,request,seconds,**kwargs)
    monkeypatch.setattr(delivery,'_call',tamper)
    if phase=='resolve':
        with pytest.raises(delivery.DeliveryUnresolved,match='delivery_resolver_unavailable'):
            delivery.freeze_sources(root,nodes)
    else:
        refs=delivery.freeze_sources(root,nodes)
        _,_,bound=closeout_decision(root,refs,recommendation=first['candidate_id'])
        submit=accepted(root,bound);result=delivery.invoke(root,submit,20)
        if phase=='confirm':
            budget.action_outcome(root,submit,result);result=delivery.invoke(root,accepted(root,bound,'confirm'),10)
        assert result['status']==('engineering_error' if phase=='audit' else 'unknown')
        keys={'status','operation_id','candidate_id','artifact_sha256','note','cleanup_confirmed'}
        if phase=='audit':
            assert result['failure_phase']=='audit'
            keys.add('failure_phase')
        else:
            assert 'failure_phase' not in result
        assert set(result)==keys
        assert 'Traceback' not in result['note']
    if phase=='audit':
        retries=list(audit_recovery.facts(root))
        assert len(retries)==1 and retries[0][1]['status']=='engineering_error'
        assert retries[0][1]['note']=='audit_process_failed'
    else:
        assert not list(audit_recovery.facts(root))
    for index,data in _leaf_files(root,phase,2 if phase=='audit' else 1):
        assert index['phase']==phase and index['returncode']!=0 and index['outcome']=='exited'
        assert b'Traceback' in data and b'Frozen current protocol original differs' in data
    for path in (root/'project').rglob('*'):
        if path.is_file():assert b'Frozen current protocol original differs' not in path.read_bytes()


def test_leaf_output_tail_is_bounded_and_does_not_change_the_reply(tmp_path):
    launcher=tmp_path/'noisy.py'
    launcher.write_text('import sys\nsys.stdin.read()\nsys.stderr.write("e"*100000+"END")\nprint("{}")\n')
    evidence=tmp_path/'host-delivery/leaf-output'
    value=delivery._call(dict(launcher_argv=[sys.executable,str(launcher)],adapter=str(launcher)),
        dict(phase='confirm',operation_id='a'*64),10,evidence=evidence)
    assert value=={}
    index=c.read(evidence/(('a'*64)+'-confirm-1.json'))
    stream=index['streams']['stderr']
    assert stream['total_bytes']==100003 and stream['kept_bytes']==delivery.MAX_LEAF_TAIL and stream['truncated']
    data=(evidence/stream['path']).read_bytes()
    assert len(data)==delivery.MAX_LEAF_TAIL and data.endswith(b'END')
    assert not (evidence/stream['path']).stat().st_mode&0o222
    assert delivery._call(dict(launcher_argv=[sys.executable,str(launcher)],adapter=str(launcher)),
        dict(phase='confirm',operation_id='a'*64),10,evidence=evidence)=={}
    assert (evidence/(('a'*64)+'-confirm-2.json')).is_file()
