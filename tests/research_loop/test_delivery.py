"""Exact pre-A snapshots and isolated delivery, deterministic zero-model processes."""
from test_history_handoff import selection_explanation
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, delivery, native
from cov_execution import delivery as cov_delivery
from scidiscovery.research_loop import runtime
from test_history_handoff import finish


def failure_diagnostics(root):
    """Bounded failed adapter diagnostics for pytest capture; no scientific bytes."""
    directory=root/'host-delivery/leaf-output'
    rows=[]
    for path in sorted(directory.glob('*.json'))[-8:]:
        value=c.read(path)
        row={key:value.get(key) for key in ('operation_id','phase','outcome','returncode')}
        streams=value.get('streams',{})
        for key,proof in streams.items():
            if 'stderr' not in key or not proof.get('path'):continue
            # _keep owns the local diagnostic filenames; never follow arbitrary
            # scientific paths or include settings, credentials or model data.
            saved=directory/Path(proof['path']).name
            if saved.is_file() and not saved.is_symlink():
                row[key]=saved.read_bytes()[-8192:].decode(errors='replace')
        rows.append(row)
    return rows


FAKE_HARNESS = '''import json,os,pathlib,shutil
runs=pathlib.Path(os.environ['EXP_RUN_DIR'])
row=json.loads((runs/'results.jsonl').read_text())
meta=json.loads(pathlib.Path(os.environ['EXP_META']).read_text())
# This subprocess is inside the audit namespace. Real targets/private control
# must be absent even though the launcher knows their literal host paths.
assert not pathlib.Path('/target').exists()
assert not pathlib.Path(meta['control_path']).exists()
assert not pathlib.Path(meta['hidden_path']).exists()
shutil.copyfile(row['model_path'],runs/'submitted_model.pkl')
mode=meta['mode']
fact={'run_id':row['run_id'],'audit_score':.5 if mode!='skip' else None,
      'audit_gap':.1 if mode!='skip' else None,'leakage_flag':mode=='leak',
      'audit_note':'audit took 0.01s on fixture held-out rows' if mode!='skip' else 'audit skipped'}
(runs/'submitted.json').write_text(json.dumps(fact))
'''

ZERO_PREDICTOR = '''import json,pathlib
class ZeroPredictor:
 def __init__(self,dimension=None):self.dimension=dimension
 def predict(self,frame):
  import numpy as np
  return np.zeros((len(frame),self.dimension)) if self.dimension else np.zeros(len(frame))
def train(train_df,val_df):
 assert not pathlib.Path('/target').exists()
 meta=json.loads(pathlib.Path('/inputs/harness/meta.json').read_text())
 return ZeroPredictor(len(meta['target_cols']) if meta.get('target_cols') else None)
'''


def generate_isolated_model(python, script, meta, destination):
    """Fixture producer serializes only in the declared task namespace."""
    command = [shutil.which('bwrap'), '--unshare-user', '--unshare-pid', '--unshare-net',
               '--die-with-parent', '--new-session', '--clearenv', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
    base = Path(python).resolve().parent.parent
    for path in dict.fromkeys(['/usr', '/bin', '/lib', '/lib64', str(Path(python).parent.parent),
                               str(base / 'bin'), str(base / 'lib')]):
        if Path(path).exists():command += ['--ro-bind', path, path]
    command += ['--ro-bind', str(script), '/source.py', '--ro-bind', str(meta), '/meta.json',
                '--bind', str(destination.parent), '/output', '--setenv', 'OPENBLAS_NUM_THREADS', '1',
                '--', python, '-I', '-c',
                'import cloudpickle,importlib.util,json,sys;'
                's=importlib.util.spec_from_file_location("bounded_zero","/source.py");'
                'm=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m);'
                'meta=json.load(open("/meta.json"));'
                'model=m.ZeroPredictor(len(meta["target_cols"]) if meta.get("target_cols") else None);'
                'cloudpickle.dump(model,open("/output/'+destination.name+'","wb"))']
    subprocess.run(command, check=True, timeout=20, capture_output=True, env={'PATH': '/usr/bin:/bin'})


def descriptor(root, public, *, python=sys.executable):
    """Reusable real bwrap fixture for installed CLI gates; no host leaf fallback."""
    module = Path(cov_delivery.__file__).absolute()
    return dict(adapter=str(module), launcher_argv=[python, str(module), '--isolate'],
                config=dict(runs='runs', namespace_runs='/opt/exp/runs', python=python),
                target=dict(runs=str(root / 'runs'), submission=str(root / 'submission')), readonly_inputs_version=1,
                inputs=[dict(label='harness/' + name, path=str(public / 'harness' / name))
                        for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json')]
                       + [dict(label='runtime-python', path=python)],
                source_roots=[str(root / 'runs'), str(root / 'project/nodes')])


def delivery_policy(adapter):
    return dict(closeout_a=dict(max_observed_tokens=1000, max_seconds=30),
                inflight_tokens=100, cleanup_seconds=5, submit_seconds=20,
                confirm_seconds=10, adapter=adapter)


def prepare_fixture(tmp_path, mode='ok', *, real_harness=None, python=sys.executable, candidate_count=1):
    if shutil.which('bwrap') is None:pytest.skip('Actual bwrap qualification lane required')
    public = tmp_path / 'public';(public / 'harness').mkdir(parents=True)
    root = tmp_path / 'instance'
    for name in ('exp_cli.py', 'runner.py', 'train.csv', 'val.csv', 'meta.json'):
        destination = public / 'harness' / name
        if real_harness is not None:
            shutil.copyfile(real_harness / name, destination)
        elif name == 'exp_cli.py':destination.write_text(FAKE_HARNESS)
        elif name == 'meta.json':
            c.write(destination, dict(mode=mode, control_path=str(root / 'settings.json'), hidden_path=str(tmp_path / 'private-score.json')))
        else:destination.write_text('public fixture only\n')
    objective = public / 'objective.md';objective.write_text('Deliver an exact candidate without asserting goal completion.')
    c.write(tmp_path / 'private-score.json', dict(secret='Never mounted'))
    limits = {role: dict(max_observed_tokens=2000, max_seconds=60) for role in ('H', 'W', 'A', 'E')}
    controller.prepare(root, objective, public, ['fake-no-model'], max_tokens=100_000,
        max_seconds=300, max_steps=12, model='fixture', reasoning_effort='high', search_policy=None,
        node_budget_policy=limits, history_observer=dict(adapter=str(Path(__file__).resolve().parents[2] / 'plugins/cov_execution/cov_execution/observer.py'),
                                                      config=dict(runs='runs', evaluation_key='fixture-public')),
        delivery_policy=delivery_policy(descriptor(root, public, python=python)))
    (root / 'runs').mkdir();(root / 'submission').mkdir()
    finish(root, 1, 'H', [], action='experiment')
    # Ordinary candidate, not a previous audited/actual submission.
    model = root / 'runs/exact-model.bin';model.write_bytes(b'not a host-loaded pickle\n' * 20_000)
    script = c.workspace(root, 2) / 'artifacts/train.py'
    row = dict(run_id=1, ok=True, val_acc=0 if mode == 'real_ok' else .6, val_score=0 if mode == 'real_ok' else .6,
               model_path='/opt/exp/runs/exact-model.bin', script=str(script))
    (root / 'runs/results.jsonl').write_text(''.join(json.dumps(dict(row, run_id=run)) + '\n'
                                                  for run in range(1, candidate_count + 1)))
    if real_harness is None:
        code = 'def train(train_df, val_df): return None\n'
    elif mode == 'real_ok':
        code = ZERO_PREDICTOR
        script.parent.mkdir(parents=True, exist_ok=True);script.write_text(code)
        generate_isolated_model(python, script, public / 'harness/meta.json', model)
    else:
        code = 'def train(train_df, val_df):\n    return object()\n'  # Genuine public audit must skip/fail.
    finish(root, 2, 'W', [1], files={'train.py': code})
    c.write(root / 'host-started.json', budget.clock_stamp())
    budget.transition(root, 'advance', 'bounded input preparation')
    nodes = [dict(step=step, seal_ref=c.record_ref(c.host_record(c.workspace(root, step)) / 'result-identity.json'))
             for step in (1, 2)]
    return root, public, nodes


def closeout_decision(root, refs, recommendation='run-1'):
    from scidiscovery.research_loop import accepted_budget
    budget.begin_closeout(root, 2, 3, 'fixture closeout', delivery_sources=refs)
    job = c.build_job(root, 3, 'A', [1, 2], purpose='closeout', source_cutoff=2, delivery_sources=refs)
    record = c.host_record(c.workspace(root, 3))
    payload = dict(summary='A selects the original candidate', scientific_result='A decision with remaining uncertainty',
                   limitations=['Scientific goal remains unasserted'], implementation_selection=None,
                   candidate_recommendation=recommendation, progress_assessment=None,
                   search_review=None, decision_request=None, portfolio_review=None,
                   next_action='deliver', route_reason='An explicit bounded delivery decision')
    intent = dict(task_ref=c.record_ref(root / 'jobs/003.json'))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, 3, record, intent)
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
                                              raw_ref=c.file_ref(record / 'science-output.json')))
    result = c.seal_result(root, 3)
    return job, result, delivery.binding(root, job, result)


def accepted(root, bound, kind='submit'):
    return budget.accept_action(root, kind, bound['operation_id'], bound['decision'], bound,
                                20 if kind == 'submit' else 10)


def test_generic_descriptor_does_not_require_cov_config_fields(tmp_path):
    script = tmp_path / 'adapter.py';script.write_text('print("bounded")')
    supplied = dict(adapter=str(script), launcher_argv=[sys.executable, str(script)],
                    config=dict(domain_specific='opaque'), target=dict(domain_target='opaque'),
                    inputs=[], source_roots=[str(tmp_path)], readonly_inputs_version=1)
    result = delivery.policy(delivery_policy(supplied))
    assert result['adapter']['config'] == supplied['config']
    assert result['adapter']['target'] == supplied['target']


def test_delivery_tool_upgrade_preserves_scientific_input_checks(tmp_path,monkeypatch):
    adapter=tmp_path/'adapter.py';adapter.write_text('print("original tool")')
    python=tmp_path/'python';python.write_text('original interpreter')
    scientific=tmp_path/'data.csv';scientific.write_text('x,y\n1,2\n')
    spec=delivery.policy(delivery_policy(dict(adapter=str(adapter),launcher_argv=[str(python),str(adapter)],
        config=dict(python=str(python)),target={},source_roots=[str(tmp_path)],readonly_inputs_version=1,
        inputs=[dict(label='runtime-python',path=str(python)),dict(label='harness/data.csv',path=str(scientific))])))['adapter']
    runtime=next(item for item in spec['inputs'] if item['label']=='runtime-python')
    assert 'sha256' not in runtime
    monkeypatch.setattr(delivery,'_settings',lambda *args:dict(delivery_policy=dict(adapter=spec)))
    python.write_text('upgraded interpreter')
    adapter.write_text('print("upgraded tool")')
    assert delivery._adapter(tmp_path)['config']==spec['config']
    scientific.write_text('x,y\n1,999\n')
    with pytest.raises(ValueError,match='public harness changed'):
        delivery._adapter(tmp_path)


def test_delivery_namespace_uses_exact_interpreter_reference(tmp_path,monkeypatch):
    from scidiscovery.research_loop import generic_delivery
    from scidiscovery.research_loop.protocol import CURRENT
    stage=tmp_path/'stage';(stage/'inputs').mkdir(parents=True);(stage/'scratch').mkdir()
    target=tmp_path/'target';target.mkdir()
    python=Path(sys.executable)
    proof=dict(label='runtime-python',invoked_path=str(python.absolute()),path=str(python.resolve()))
    request=dict(scope=dict(handoff_protocol=CURRENT),stage=str(stage),phase='confirm',inputs=[proof],
        source_roots=[],object_refs={},config=dict(python=str(python),runtime_roots=list(dict.fromkeys([sys.base_prefix,sys.prefix]))),
        target=dict(directory=str(target)))
    commands=[]
    class NamespaceReady(Exception):pass
    def capture(program,arguments):
        commands.append((program,arguments))
        raise NamespaceReady
    monkeypatch.setattr(generic_delivery.shutil,'which',lambda name:'/qualified/bwrap')
    monkeypatch.setattr(generic_delivery.os,'execv',capture)
    with pytest.raises(NamespaceReady):generic_delivery.isolate(request,str(Path(generic_delivery.__file__).resolve()))
    program,arguments=commands[0]
    assert program=='/qualified/bwrap' and '--unshare-net' in arguments
    assert arguments[arguments.index('--')+1]==str(python)
    assert '--bind' not in arguments
    proof['invoked_path']='/another/python'
    with pytest.raises(ValueError,match='runtime changed'):
        generic_delivery.isolate(request,str(Path(generic_delivery.__file__).resolve()))
    assert len(commands)==1


@pytest.mark.parametrize('invalid', ['missing', 'nan', 'zero_submit', 'negative_margin'])
def test_delivery_policy_rejects_incomplete_or_unbounded_limits(tmp_path, invalid):
    value = delivery_policy(None)
    value['submit_seconds'] = value['confirm_seconds'] = 0
    if invalid == 'missing':value.pop('inflight_tokens')
    if invalid == 'nan':value['cleanup_seconds'] = float('nan')
    if invalid == 'zero_submit':value['submit_seconds'] = 1  # No adapter may not spend external allowance.
    if invalid == 'negative_margin':value['inflight_tokens'] = -1
    with pytest.raises(ValueError):delivery.policy(value)


@pytest.mark.process_e2e
def test_pre_a_sources_are_snapshot_pending_not_proxy_qualified(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    assert refs['items'][0]['status'] == 'audit_pending'
    assert refs['items'][0]['objects']['artifact']['size_bytes'] > 256_000
    assert not (root / 'runs/submitted.json').exists()
    saved = copy.deepcopy(refs)
    (root / 'runs/exact-model.bin').write_bytes(b'Overwritten mutable public model')
    (c.workspace(root, 2) / 'artifacts/train.py').chmod(0o600)
    # Changing sealed material is corruption, unlike overwriting unsealed scratch.
    with pytest.raises(ValueError, match='Original size changed|original'):
        delivery.validate_sources(root, refs)
    assert refs == saved
    view = json.dumps(delivery.public_sources(refs))
    assert len(view.encode()) <= 4096 and 'sha256' not in view and 'host-delivery' not in view


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['ok', 'skip', 'leak'])
def test_real_namespace_audit_promotes_only_complete_nonleaking_result(tmp_path, mode):
    root, _, nodes = prepare_fixture(tmp_path, mode)
    (root / 'runs/submitted_model.pkl').write_bytes(b'Old confirmed bytes')
    (root / 'submission/model.pkl').write_bytes(b'Old confirmed bytes')
    old_settings = (root / 'settings.json').read_bytes()
    refs = delivery.freeze_sources(root, nodes)
    _, _, bound = closeout_decision(root, refs)
    intent = accepted(root, bound)
    result = delivery.invoke(root, intent, 20)
    assert result['cleanup_confirmed'] is True
    assert (root / 'settings.json').read_bytes() == old_settings
    if mode == 'ok':
        assert result['status'] == 'confirmed', result
        expected = bound['source']['objects']['artifact']
        assert (root / 'submission/model.pkl').stat().st_size == expected['size_bytes']
        assert (root / 'runs/submitted_model.pkl').stat().st_size == expected['size_bytes']
        marker=c.read(root/'runs/rootless-delivery-operation.json')
        assert marker['artifact_ref']==expected
        assert marker['artifact_origin']==bound['source']['artifact_origin']
        budget.action_outcome(root, intent, result)
        before = (root / 'runs/submitted.json').read_bytes()
        assert delivery.invoke(root, intent, 20) == result  # Stored outcome is a read, not resubmission.
        query = accepted(root, bound, 'confirm')
        assert delivery.invoke(root, query, 10)['status'] == 'confirmed'
        assert (root / 'runs/submitted.json').read_bytes() == before
    else:
        assert result['status'] == ('unclassified' if mode=='skip' else 'rejected'), result
        assert (root / 'submission/model.pkl').read_bytes() == b'Old confirmed bytes'
        assert (root / 'runs/submitted_model.pkl').read_bytes() == b'Old confirmed bytes'
        assert not (root / 'runs/rootless-delivery-operation.json').exists()


@pytest.mark.process_e2e
def test_source_archive_tamper_and_changed_argv_symlink_are_hard_errors(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    proof = refs['items'][0]['objects']['artifact']
    snapshot = root / proof['path'];snapshot.chmod(0o600);snapshot.write_bytes(b'Changed immutable bytes')
    with pytest.raises(ValueError, match='changed'):delivery.validate_sources(root, refs)
    # Frozen argv lookup must validate the invocation path, not only its old target.
    first = tmp_path / 'first.py';first.write_text('original')
    second = tmp_path / 'second.py';second.write_text('replacement')
    link = tmp_path / 'launcher-link';link.symlink_to(first)
    value = c.settings(root)
    descriptor_ = dict(value['delivery_policy']['adapter'])
    descriptor_['launcher_files'].append(dict(path=str(first), invoked_path=str(link)))
    value['delivery_policy']['adapter'] = descriptor_
    c.write(root / 'settings.json', value)
    c.write(root / 'settings-identity.json', dict(path='settings.json'))
    link.unlink();link.symlink_to(second)
    with pytest.raises(ValueError, match='names another original'):delivery._adapter(root)


@pytest.mark.process_e2e
def test_seventeenth_source_remains_selectable_and_shared_objects_are_deduplicated(tmp_path, monkeypatch):
    root, _, nodes = prepare_fixture(tmp_path, candidate_count=17)
    refs = delivery.freeze_sources(root, nodes)
    assert len(refs['items']) == 17 and refs['omitted_count'] == 0
    assert len({item['objects']['artifact']['path'] for item in refs['items']}) == 1
    assert len({item['objects']['source.py']['path'] for item in refs['items']}) == 1
    assert delivery.public_sources(refs)['omitted_count'] == 1
    read_original = delivery._proof
    reads = []
    def observe_read(root_, proof):
        reads.append((proof['path'],proof['size_bytes']))
        return read_original(root_, proof)
    monkeypatch.setattr(delivery, '_proof', observe_read)
    delivery.validate_sources(root, refs)
    assert len(reads) == len(set(reads))  # Each exact retained original is checked once per validation.
    conflict = copy.deepcopy(refs)
    conflict['items'][1]['objects']['artifact'] = dict(conflict['items'][1]['objects']['artifact'], size_bytes=0)
    with pytest.raises(ValueError, match='Conflicting delivery aliases'):
        delivery.validate_sources(root, conflict)
    _, _, bound = closeout_decision(root, refs, recommendation='run-17')
    assert bound['candidate_id'] == 'run-17'
    job = c.read(root / 'jobs/003.json')
    assert any(row['candidate_id']=='run-17' for row in refs['items'])
    from scidiscovery.research_loop import accepted_prefix
    candidate = accepted_prefix.candidate(root, job['history']['prefix'], 'run-17')
    assert candidate['observation']['id'] == bound['candidate_id'] == 'run-17'


@pytest.mark.process_e2e
def test_partial_target_or_missing_marker_cannot_confirm_or_resubmit(tmp_path):
    root, _, nodes = prepare_fixture(tmp_path)
    refs = delivery.freeze_sources(root, nodes)
    _, _, bound = closeout_decision(root, refs)
    original = accepted(root, bound)
    # Accepted submit may have crashed after one target write. The controller
    # recovers by a new confirm identity, never calling the submit again.
    shutil.copyfile(root / bound['source']['objects']['artifact']['path'], root / 'submission/model.pkl')
    # The local call is known ended; external state is explicitly unknown.
    # With no such outcome/process fact the production guard must reject query.
    budget.action_outcome(root, original, delivery._result(bound, 'unknown', 'fixture_partial_target', cleanup_confirmed=True))
    query = accepted(root, bound, 'confirm')
    assert query['operation_id'] == original['operation_id']
    result = delivery.invoke(root, query, 10)
    assert result['status'] == 'unknown', result
    assert not (root / 'runs/submitted.json').exists()
    assert not (root / 'runs/submitted_model.pkl').exists()


@pytest.mark.process_e2e
def test_exited_launcher_with_live_stdout_child_is_cleaned_not_success(tmp_path):
    launcher = tmp_path / 'misbehaving.py'
    launcher.write_text('''import json,pathlib,subprocess,sys
request=json.load(sys.stdin)
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'])
pathlib.Path(request['pid_file']).write_text(str(child.pid))
print('{}',flush=True)
''')
    ids = []
    with pytest.raises(delivery.DeliveryUnresolved,match='delivery_audit_timeout'):
        delivery._call(dict(launcher_argv=[sys.executable, str(launcher)], adapter=str(launcher)),
            dict(phase='audit', operation_id='exact-fixture-operation', pid_file=str(tmp_path / 'child.pid')), .5,
            on_process=lambda phase, identity: ids.append((phase, identity)))
    # EOF did not arrive before the deadline; the typed engineering timeout is
    # returned only after the actually owned child group is cleaned.
    assert len(ids) == 1 and ids[0][0] == 'audit'
    pid = int((tmp_path / 'child.pid').read_text())
    try:
        child = runtime.process_identity(pid, ids[0][1]['task_ref'])
    except (OSError, ValueError):
        child = None
    if child is not None:assert not runtime.process_matches(child, child['task_ref'])
    # Reap only the fixture child that the real subreaper now owns.
    import os
    try:os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:pass


@pytest.mark.process_e2e
@pytest.mark.parametrize('mode', ['real_ok', 'real_skip'])
def test_actual_public_harness_audit_completion_controls_promotion(tmp_path, mode):
    """Opt-in deterministic original public audit, no N5 instance or hidden data."""
    import os
    harness = os.environ.get('SCID_DELIVERY_PUBLIC_HARNESS')
    python = os.environ.get('SCID_DELIVERY_TASK_PYTHON')
    if not harness or not python:
        pytest.skip('Parent supplies original public harness and isolated task runtime for this lane')
    root, _, nodes = prepare_fixture(tmp_path, mode=mode, real_harness=Path(harness), python=python)
    refs = delivery.freeze_sources(root, nodes)
    _, _, bound = closeout_decision(root, refs)
    intent = accepted(root, bound)
    result = delivery.invoke(root, intent, 20)
    if mode == 'real_ok':
        assert result['status'] == 'confirmed', result
        assert (root / 'submission/model.pkl').stat().st_size == bound['source']['objects']['artifact']['size_bytes']
        receipt = c.read(root / 'runs/submitted.json')
        assert receipt['leakage_flag'] is False
        assert receipt['audit_score'] is not None and receipt['audit_note'].startswith('audit took ')
    else:
        assert result['status'] == 'engineering_error' and result['note']=='audit_interface_missing', result
        assert not (root / 'submission/model.pkl').exists()
        assert not (root / 'runs/submitted.json').exists()


@pytest.mark.process_e2e
def test_complete_delivery_sources_exceed_old_byte_and_candidate_caps(tmp_path):
    root,_,nodes=prepare_fixture(tmp_path,candidate_count=140)
    catalogue=delivery.catalogue_sources(root,nodes)
    assert len(catalogue['items'])==140 and catalogue['omitted_count']==0
    refs=delivery.freeze_sources(root,nodes)
    assert len(refs['items'])==140 and refs['omitted_count']==0
    assert len(delivery._bytes(refs))>256_000
    path=root/'full-delivery-sources.json';c.write(path,refs)
    assert delivery.validate_sources(root,c.read(c.regular(path)))==refs
    view=delivery.public_sources(refs)
    assert len(delivery._bytes(view))<=4096 and view['omitted_count']>0
    selected=delivery.selected_sources(root,catalogue,'run-140',prepare=True)
    assert selected['items'][0]['candidate_id']=='run-140'


@pytest.mark.process_e2e
def test_complete_internal_adapter_reply_is_spooled_without_byte_truncation(tmp_path):
    script=tmp_path/'adapter.py'
    script.write_text('import json,sys\nrequest=json.load(sys.stdin)\njson.dump({"items":request["items"]},sys.stdout)\n')
    rows=[dict(label='item-'+str(i),data='x'*2048) for i in range(160)]
    result=delivery._call(dict(launcher_argv=[sys.executable,str(script)],adapter=str(script)),
        dict(phase='resolve',operation_id='f'*64,items=rows),10)
    assert result==dict(items=rows) and len(delivery._bytes(result))>256_000


def test_staged_audit_and_recovery_inputs_omit_delivery_destination(tmp_path,monkeypatch):
    original=tmp_path/'artifact';original.write_bytes(b'Exact retained scientific object')
    original.chmod(0o400)
    proof=dict(path='artifact',size_bytes=original.stat().st_size)
    spec=dict(inputs=[],config={'scientific_contract':'bounded audit'},target={'credential':'private-destination'})
    source=dict(candidate_id='exact-candidate',objects={'artifact':proof},artifact_origin=c.file_ref(original),context={'kind':'scientific-result'})
    monkeypatch.setattr(delivery,'_proof',lambda root,value,**kwargs:original)
    monkeypatch.setattr(delivery,'_settings',lambda *args:dict(handoff_protocol=c.HANDOFF_PROTOCOL))
    stage=tmp_path/'stage'
    request=delivery._staged(tmp_path,spec,source,'same-operation',stage)
    recovery=delivery._staged(tmp_path,spec,source,'same-operation',stage/'recovery-1')
    assert 'target' not in request and 'target' not in recovery
    assert 'private-destination' not in json.dumps([request,recovery])
    assert request['candidate_id']==recovery['candidate_id']=='exact-candidate'
    assert request['object_refs']==recovery['object_refs']=={'artifact':c.file_ref(original)}
    assert request['artifact_origin']==recovery['artifact_origin']==c.file_ref(original)
    assert (stage/'inputs/artifact').stat().st_size == (stage/'recovery-1/inputs/artifact').stat().st_size == 0
    assert (stage/'inputs/artifact').stat().st_ino != original.stat().st_ino
    (stage/'scratch/owned').write_text('writable independent audit work')
    assert (stage/'scratch/owned').stat().st_ino != original.stat().st_ino


def test_custom_delivery_requires_explicit_readonly_input_contract(tmp_path):
    script = tmp_path / 'adapter.py'; script.write_text('pass\n')
    supplied = dict(adapter=str(script), launcher_argv=[sys.executable, str(script)], config={}, target={},
        inputs=[], source_roots=[str(tmp_path)])
    with pytest.raises(ValueError, match='readonly_inputs_version'):
        delivery.policy(delivery_policy(supplied))


def test_staging_deadline_expires_before_any_leaf_dispatch(tmp_path, monkeypatch):
    original = tmp_path / 'artifact'; original.write_bytes(b'frozen'); original.chmod(0o400)
    proof = dict(path='artifact',size_bytes=original.stat().st_size)
    monkeypatch.setattr(delivery, '_settings', lambda *args: dict(handoff_protocol=c.HANDOFF_PROTOCOL))
    class Expired:
        def remaining_seconds(self):
            raise TimeoutError('original attempt exhausted')
    with pytest.raises(TimeoutError, match='original attempt exhausted'):
        delivery._staged(tmp_path, dict(inputs=[], config={}),
            dict(candidate_id='exact', objects={'artifact': proof},artifact_origin=c.file_ref(original),context={}), 'same-call', tmp_path / 'stage',
            context=Expired())


def test_selected_sources_ignore_missing_unconsumed_science(tmp_path,monkeypatch):
    from test_direct_input_bindings import direct_instance, stopped_seals
    from test_direct_public_collect import replace_equal_bytes
    from scidiscovery.research_loop import generic_delivery, shared_originals
    target=tmp_path/'target';target.mkdir()
    adapter=generic_delivery.descriptor(target)
    root,_=direct_instance(tmp_path,delivery_policy=delivery_policy(adapter))
    stopped_seals(monkeypatch)
    _,work,record=finish(root,1,'H',[],action='experiment',
        files={'chosen.txt':'one selected original','unconsumed.txt':'an unused original'})
    nodes=[dict(step=1,seal_ref=c.record_ref(record/'result-identity.json'))]
    (work/'artifacts/unconsumed.txt').unlink()
    assert delivery.catalogue_sources(root,nodes)['items']==[]
    selected=work/'artifacts/chosen.txt'
    spec=delivery._adapter(root)
    objects={proof['label']:dict(path=proof['path'],size_bytes=proof['size_bytes'])
        for proof in spec['inputs'] if proof['label']!='runtime-python'}
    objects['artifact']=c.file_ref(selected)
    source=dict(candidate_id='selected',status='audit_pending',ledger=c.file_ref(record/'result.json'),
        objects=objects,artifact_origin=c.file_ref(selected),context=dict(kind='managed_local_result'))
    refs=dict(items=[source],omitted_count=0,source_nodes=nodes)
    assert delivery.validate_sources(root,refs)==refs
