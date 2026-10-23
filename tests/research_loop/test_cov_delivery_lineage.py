"""Managed delivery uses sealed public call originals, never mutable current files."""
import copy
import json
from pathlib import Path
import shutil
import sys

import pytest
from execution_fixtures import execution_registry

from scidiscovery.research_loop import budget, contracts as c, controller, delivery
from cov_execution import delivery as cov_delivery, lineage as cov_lineage
from test_cov_lineage import BACKEND
from test_delivery import FAKE_HARNESS, descriptor, delivery_policy, prepare_fixture
from test_history_handoff import finish


def managed_scene(tmp_path, *, alias_correct=True, rewrite_script=True, launcher=None, harness=None, audit=False, max_tokens=100000, resume_policy=None, output_capacity=None, namespace_runs=None):
    if shutil.which('bwrap') is None:
        pytest.skip('Actual namespace lane required')
    root, public = tmp_path / 'instance', tmp_path / 'public'
    (public / 'harness').mkdir(parents=True)
    for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
        path = public / 'harness' / name
        if name == 'exp_cli.py':
            path.write_text(harness or FAKE_HARNESS)
        elif name == 'meta.json':
            c.write(path, dict(mode='ok', control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'hidden-never-mounted')))
        else:
            path.write_text('Public fixture only\n')
    objective = public / 'objective.md';objective.write_text('Deliver the exact public scientific candidate.')
    spec = descriptor(root, public)
    if not rewrite_script:
        adapter=tmp_path/'adapter_without_script_rebinding.py'
        from scidiscovery.research_loop import protocol
        shutil.copyfile(Path(protocol.__file__),tmp_path/'protocol.py')
        adapter.write_text(Path(cov_delivery.__file__).read_text().replace("script='/inputs/source.py'", "script=request['context']['row']['script']"))
        spec.update(adapter=str(adapter),launcher_argv=[sys.executable,str(adapter),'--isolate'])
    spec['config']['namespace_runs'] = (str(namespace_runs) if namespace_runs is not None
        else str(root / 'runs') if alias_correct else '/opt/exp/runs')
    controller.prepare(root, objective, public, launcher or ['fake-no-model'], max_tokens=max_tokens, max_seconds=300, max_steps=12,
        model='fixture', reasoning_effort='high', search_policy=None,**(dict(max_output_item_bytes=output_capacity,max_output_total_bytes=output_capacity) if output_capacity else {}),resume_policy=resume_policy,candidate_audit_policy={'version':1} if audit else None,
        node_budget_policy={role: dict(max_observed_tokens=2000, max_seconds=60) for role in ('H','W','A','E')},
        delivery_policy=delivery_policy(spec),
        history_observer=dict(contract_version=2, adapter=str(Path(cov_lineage.__file__).absolute()),
            config=dict(runs='runs', calls='project/nodes', evaluation_key='covshift-public-val-v1')))
    (root / 'runs').mkdir();(root / 'submission').mkdir()
    finish(root, 1, 'H', [], action='experiment')
    c.build_job(root, 2, 'W', [1],instruction='Exact scientific question')
    record = c.host_record(c.workspace(root, 2))
    c.write(record / 'native-intent.json', dict(task_sha256=c.digest(root / 'jobs/002.json'),
        budget_slice=budget.accept_node(root,c.read(root/'jobs/002.json'))))
    backend = tmp_path / 'backend.py';backend.write_text(BACKEND)
    config_path = tmp_path / 'config.json'
    cov_lineage.configure(root, 2, [sys.executable, str(backend), '--runs-root', str(root / 'runs')], root / 'runs', config_path,receipt_channel='backend_file')
    config, digest = cov_lineage._config(config_path)
    script = c.workspace(root, 2) / 'work/train.py'
    script.write_text(json.dumps(dict(mode='success', revision='P')))
    first = cov_lineage.run(config, digest, script, new=True)
    source_p = script.read_bytes();model_p = Path(first['model']['path']).read_bytes()
    script.write_text(json.dumps(dict(mode='success', revision='Q')))
    second = cov_lineage.run(config, digest, script, parent=first['candidate_id'])
    finish(root, 2, 'W', [1])
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'Bounded delivery preparation')
    nodes = [dict(step=step, seal_sha256=c.digest(c.host_record(c.workspace(root, step)) / 'result-identity.json')) for step in (1,2)]
    return root, nodes, first, second, script, source_p, model_p


def current_managed_scene(tmp_path,monkeypatch):
    """Real current public-runner calls through the shipped native launch callback."""
    import importlib.util
    from cov_execution import receipt as cov_receipt
    from scidiscovery.research_loop import execution,experiment_client,experiments,worker_execution_bridge as bridge
    from test_default_native_local_execution import actual_native
    from test_worker_execution_hash_free import PUBLIC_CLI,PUBLIC_RUNNER
    actual=actual_native()
    public=tmp_path/'public';harness=public/'environment/harness';harness.mkdir(parents=True)
    audit_harness=public/'harness';audit_harness.mkdir()
    (public/'instruction.md').write_text('Inert public receipt and delivery transport; no scientific efficacy claim.')
    for name,text in dict(exp_cli=PUBLIC_CLI,runner=PUBLIC_RUNNER).items():
        (harness/(name+'.py')).write_text(text)
    for name in ('train.csv','val.csv'):(harness/name).write_text('fixture\n')
    root=tmp_path/'instance'
    meta=dict(mode='ok',control_path=str(root/'settings.json'),hidden_path=str(tmp_path/'private-never-mounted'))
    c.write(harness/'meta.json',meta)
    for name in ('runner.py','train.csv','val.csv','meta.json'):shutil.copyfile(harness/name,audit_harness/name)
    (audit_harness/'exp_cli.py').write_text(FAKE_HARNESS)
    adapter=descriptor(root,public)
    # The production scientific callback keeps this declared host-path alias
    # inside its namespace; the resolver must use that exact frozen prefix.
    adapter['config']['namespace_runs']=str(root/'runs')
    controller.prepare(root,public/'instruction.md',public,['fake-no-model'],max_tokens=100000,max_seconds=300,max_steps=12,
        model='fixture',reasoning_effort='high',search_policy=None,
        node_budget_policy={role:dict(max_observed_tokens=2000,max_seconds=60) for role in 'HWAE'},
        delivery_policy=delivery_policy(adapter),
        history_observer=dict(contract_version=2,adapter=str(Path(cov_lineage.__file__).absolute()),
            config=dict(runs='runs',calls='project/nodes',evaluation_key='covshift-public-val-v1')))
    (root/'runs').mkdir();(root/'submission').mkdir()
    finish(root,1,'H',[],action='experiment')
    job=c.build_job(root,2,'W',[1],instruction='Exact scientific question');work=c.workspace(root,2)
    record=execution.origin(root,2)
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/002.json'),budget_slice=budget.accept_node(root,job)))
    # The production callback freezes the native home and exact source/runner,
    # then runs the actual installed sandbox inside its bwrap mounts. No model.
    repo=Path(__file__).resolve().parents[2]
    from cov_execution import launcher
    receipt_path=str(Path(cov_receipt.__file__).resolve())
    settings=dict(scene_root=str(root),public_root=str(public),runs_dir=str(root/'runs'),submission_dir=str(root/'submission'),
        native=str(actual),code_mode_host=dict(path=str(actual.parent/'codex-code-mode-host')),
        task_python=sys.executable,scid_python=sys.executable,runtime_paths=list(dict.fromkeys([sys.prefix,sys.base_prefix,receipt_path])),
        profile='delivery-transport-no-model',model='fixture',reasoning_effort='high',_worker_science_view=True,
        cov_engineering_check_reserve_seconds=0)
    monkeypatch.setenv('THIN_HWA_JOB_MANIFEST',str(root/'jobs/002.json'));monkeypatch.setenv('THIN_HWA_ROLE','W')
    home=root/'host-runtime/delivery-scientific-home'
    launcher.prepare_home(settings,work,home)
    path=work/'work/experiment-config.json'
    environment=dict(EXP_RUN_DIR=str(root/'runs'),EXP_RUNNER=str(harness/'runner.py'),EXP_TRAIN=str(harness/'train.csv'),
        EXP_VAL=str(harness/'val.csv'),EXP_META=str(harness/'meta.json'))
    with bridge._environment(environment):
        cov_lineage.configure(root,2,[sys.executable,'-I',str(harness/'exp_cli.py')],root/'runs',path,receipt_channel='public_runner_main')
    configuration=experiment_client.resolve(path,c.read(path))
    launch=launcher.backend_launcher(settings,work,home,[(receipt_path,receipt_path)])
    source=work/'train.py'
    def diagnostics(reply):
        tails=[]
        for call in experiments.Records(configuration).calls()[-2:]:
            error=call/'stderr.raw'
            if error.is_file():tails.append(error.read_bytes()[-8192:].decode(errors='replace'))
        return reply,tails
    with bridge.serve(work/bridge.SUBMISSION_DIRECTORY,path,work,launch=launch,environment=environment) as slot:
        source.write_text("print('inert P; no training')\n")
        first_reply=bridge.client(slot,['run','--script',str(source),'--new-plan'])
        assert first_reply['code']==0 and first_reply['result']['status']=='known_success',diagnostics(first_reply)
        calls=experiments.Records(configuration).calls()
        first=cov_lineage._completed(calls[-1])
        source_p=source.read_bytes();model_p=Path(first['model']['path']).read_bytes()
        source.write_text("print('inert Q; no training')\n")
        second_reply=bridge.client(slot,['run','--script',str(source),'--parent-plan',first_reply['result']['candidate_id']])
        assert second_reply['code']==0 and second_reply['result']['status']=='known_success',diagnostics(second_reply)
        calls=experiments.Records(configuration).calls()
        second=cov_lineage._completed(calls[-1])
    finish(root,2,'W',[1],preserve_native_intent=True)
    nodes=[dict(step=step,seal_sha256=c.digest(c.host_record(c.workspace(root,step))/'result-identity.json')) for step in (1,2)]
    return root,nodes,first,second,source,source_p,model_p


@pytest.mark.process_e2e
def test_real_resolver_uses_p_archives_after_same_script_model_and_calls_change(tmp_path):
    root, nodes, first, _, script, source_p, model_p = managed_scene(tmp_path)
    script.write_text('Current script is no longer the original P implementation\n')
    (root / 'runs/model_run1.pkl').write_bytes(b'current model is not P')
    call_source = Path(first['model']['path']).parent / 'source.py'
    call_source.chmod(0o600);call_source.write_text('Changed current call bundle')
    refs = delivery.freeze_sources(root, nodes)  # Actual task-Python bwrap leaf, no package import.
    chosen = next(item for item in refs['items'] if item['candidate_id'] == first['candidate_id'])
    assert chosen['status'] == 'audit_pending'
    assert (root / chosen['objects']['source.py']['path']).read_bytes() == source_p
    assert (root / chosen['objects']['artifact']['path']).read_bytes() == model_p
    seal = c.read(c.host_record(c.workspace(root, 2)) / 'result-identity.json')
    observation = c.read(root / seal['observations']['path'])
    candidate = next(row for row in observation['candidates'] if row['id'] == first['candidate_id'])
    assert chosen['ledger']['sha256'] == candidate['evidence'][0]['sha256']
    assert chosen['context']['identity_note'].startswith('Exact sealed managed')


@pytest.mark.process_e2e
def test_absolute_namespace_alias_mismatch_is_unavailable_not_wrong_candidate(tmp_path):
    root, nodes, *_ = managed_scene(tmp_path, alias_correct=False)
    refs = delivery.freeze_sources(root, nodes)
    assert refs['items'] and all(item['status'] == 'unavailable' for item in refs['items'])


@pytest.mark.process_e2e
def test_archived_original_hash_tamper_is_hard_before_resolver(tmp_path):
    root, nodes, first, *_ = managed_scene(tmp_path)
    seal = c.read(c.host_record(c.workspace(root, 2)) / 'result-identity.json')
    observation = c.read(root / seal['observations']['path'])
    context = next(item for item in observation['contexts'] if item['candidate_id'] == first['candidate_id'])
    proof = next(item for item in context['proofs'] if Path(item['path']).name == 'source.py')
    archived = root / proof['snapshot'];archived.chmod(0o600);archived.write_bytes(b'tampered archive')
    with pytest.raises(ValueError, match='Archived observer original changed'):
        delivery.freeze_sources(root, nodes)


@pytest.mark.parametrize('kind', ['missing', 'ambiguous', 'unknown', 'unretained'])
def test_managed_context_problem_never_falls_back_to_current_source(kind):
    proof = dict(name='source.py', sha256='a'*64, size_bytes=1, retention='digest_only', input_label=None)
    context = dict(candidate_id='run-1', run_id=1, status='known_success',
        proofs=[proof, dict(proof, name='model.pkl'), dict(proof, name='row.json')], model=dict(proof, name='model.pkl'))
    contexts = [context]
    if kind == 'missing':contexts = []
    if kind == 'ambiguous':contexts = [context, copy.deepcopy(context)]
    if kind == 'unknown':context['status'] = 'unknown'
    with pytest.raises(cov_delivery.SourceUnavailable):
        cov_delivery.managed_objects(dict(candidate_id='run-1', execution_contexts=contexts), dict(run_id=1))


@pytest.mark.process_e2e
def test_legacy_v1_snapshot_is_explicitly_limited_and_still_works(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    assert refs['items'][0]['status'] == 'audit_pending'
    assert refs['items'][0]['context']['identity_note'].startswith('Legacy current-source snapshot')


@pytest.mark.process_e2e
@pytest.mark.parametrize('rewrite_script',[False,True])
def test_direct_delivery_audits_frozen_w_scratch_without_mounting_old_workspace(tmp_path,monkeypatch,rewrite_script):
    """Counterfactual original absolute path fails; adapter-bound original succeeds."""
    from scidiscovery.research_loop import backends, native
    from test_history_handoff import selection_explanation

    checked = FAKE_HARNESS.replace("# This subprocess is inside the audit namespace.",
        "assert not (pathlib.Path(meta['control_path']).parent/'project/nodes/002/work/train.py').exists()\n"
        "assert row['script']=='/inputs/source.py' and pathlib.Path(row['script']).is_file()\n"
        "# This subprocess is inside the audit namespace.")
    monkeypatch.setattr(sys.modules[__name__],'FAKE_HARNESS',checked)
    root,_,first,_,script,source_p,model_p=managed_scene(tmp_path,rewrite_script=rewrite_script)
    # The old host scratch still exists and now differs. The exact sealed call
    # supplies the script; neither E nor the adapter mounts that old workspace.
    script.write_text('Unrelated mutable W scratch after the sealed call\n')
    assert script.is_file() and script.read_bytes()!=source_p
    def complete_final(selected,step):
        assert selected==root and step==3
        job=c.read(root/'jobs/003.json')
        assert job['role']=='A' and job['final_only'] and job['purpose']=='closeout'
        record=c.host_record(c.workspace(root,step))
        intent=dict(step=step,task_sha256=c.digest(root/'jobs/003.json'),budget_slice=budget.accept_node(root,job))
        c.write(record/'native-intent.json',intent)
        c.write(record/'science-output.json',dict(summary='Select the exact original P candidate',
            scientific_result='Retain synthetic P evidence; target applicability remains unresolved.',
            limitations=['Synthetic native transport; no scientific efficacy claim'],implementation_selection=None,
            candidate_recommendation=first['candidate_id'],selection_explanation=selection_explanation(),
            next_action='deliver',route_reason='Authorize only the selected frozen original'))
        meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
        c.write(record/'usage.json',meter)
        backend=backends.selected(c.settings(root,job))
        observation=dict(coverage='terminal',reason=None,source=backend.terminal_source,tokens=meter)
        c.write(record/'usage-observation.json',observation)
        usage=native.usage(root)
        accepted=budget.final_a_return(root,job,intent)
        assert accepted is not None
        c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,cleanup='observed_group_cleaned',
            native_backend=backend.name,metering=observation,usage=usage,
            raw_sha256=c.digest(record/'science-output.json'),
            final_a_completion=budget.final_a_receipt(accepted,intent,meter,usage)))
        c.seal_result(root,step)
    # Complete the controller's exact FINAL job; an ordinary legacy A fixture
    # lacks its authorization/terminal receipt and stops before the audit.
    budget.begin_closeout(root,2,3,'Frozen W original delivery regression',final_only=True)
    monkeypatch.setattr(native,'execute_native',complete_final)
    if rewrite_script:
        result=controller.run(root)
        assert result['status']=='delivery_confirmed',result
        assert (root/'submission/model.pkl').read_bytes()==model_p
        actions=budget.closeout_state(root)['actions']
        assert [row['intent']['action_kind'] for row in actions]==['submit','confirm']
        source=actions[0]['intent']['binding']['source']
        assert (root/source['objects']['source.py']['path']).read_bytes()==source_p
        assert [c.read(path)['role'] for path in sorted((root/'jobs').glob('*.json'))]==['H','W','A']
    else:
        # A frozen adapter that still uses inaccessible scratch is an engineering
        # fault. Re-staging once cannot rewrite its code or justify A reselect.
        result=controller.run(root)
        assert result['status']=='delivery_engineering_error' and result['reason']=='audit_process_failed',result
        actions=budget.closeout_state(root)['actions']
        assert len(actions)==1 and actions[0]['outcome']['result']['status']=='engineering_error'
        assert actions[0]['outcome']['result']['failure_phase']=='audit'
        from scidiscovery.research_loop import audit_recovery
        assert len(list(audit_recovery.facts(root)))==1
        assert len(list((root/'jobs').glob('*.json')))==3
        assert not (root/'submission/model.pkl').exists()
