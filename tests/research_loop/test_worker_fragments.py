"""Checkpoint boundary evidence only; no launcher, model or new stage contract."""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from scidiscovery.research_loop import checkpoint, contracts as c, controller, continuation, execution, experiment_client, native, runtime
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from execution_fixtures import execution_registry, remote_registry
from test_mutable_run_inputs import fixed_code, live_fragment, terminal_code


@pytest.fixture
def fragment(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    goal = public / 'goal.md'
    goal.write_text('Compare mechanisms using only public evidence.')
    root = tmp_path / 'instance'
    controller.prepare(root, goal, public, ['never-launched'], max_tokens=1000,
                       max_seconds=100, max_steps=8, model='no-model', reasoning_effort='high',
                       search_policy=None)
    job = c.build_job(root, 1, 'W', [])
    record = c.host_record(c.workspace(root, 1))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root, 1, record, dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        native_started=False, cleanup='observed_group_cleaned'))
    c.write(record / 'usage.json', dict(input_tokens=10, cached_input_tokens=3, output_tokens=4))
    return root, job, record


def science(record, **changes):
    value = dict(summary='One discriminating observation', scientific_result='The target condition remains unresolved.',
                 limitations=['Synthetic checkpoint evidence only'], implementation_selection=None)
    root = record.parents[1]
    job = c.read(root / 'jobs/001.json')
    if 'stage_intent' in c.result_schema('W', job.get('handoff_protocol', 1))['properties']:
        value['stage_intent'] = 'handoff'
    if 'mechanism_findings' in c.result_schema('W', job.get('handoff_protocol', 1))['properties']:
        value['mechanism_findings'] = None
    value.update(changes)
    c.write(record / 'science-output.json', value)
    outcome = c.read(record / 'native-outcome.json')
    c.write(record / 'native-outcome.json', dict(outcome, raw_ref=c.file_ref(record / 'science-output.json')))


def capture_fragment(root, job, record):
    """Accept stopped originals once, then pass the owner's inventory explicitly."""
    inventory=c.accept_current_inventory(root,job['step'])
    return checkpoint._capture(root,job,record,inventory=inventory)


@pytest.fixture
def sealed_fragment(live_fragment, terminal_code, monkeypatch):
    """Reuse actual Local receipt/collection producers with no process launch."""
    from scidiscovery.research_loop import experiments
    from test_direct_public_collect import stopped_attempt
    root,job,record=live_fragment
    terminal=terminal_code()
    experiments.record_observation(terminal['config'],terminal['adapter'],terminal['accepted']['call_id'])
    c.write(record/'usage.json',dict(input_tokens=10,cached_input_tokens=3,output_tokens=4))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,
        native_started=True,terminal_observed=True,cleanup='observed_group_cleaned'))
    stopped_attempt(root,job,record,monkeypatch)
    return root,job,record,terminal


def test_missing_scientific_summary_preserves_native_identity_and_direct_results(sealed_fragment):
    root, job, record, terminal = sealed_fragment
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    result = work / 'artifacts/result.csv'
    result.write_text('public_metric\n0.61\n')
    inventory=c.accept_current_inventory(root,job['step'])
    ledger=root/'control-ledger'
    def ledger_snapshot():
        # One explicit directory only: preserve the existing accepted head and
        # commit inventory without following any ledger subdirectories.
        return {path.name:path.read_bytes() if path.is_file() else None for path in ledger.iterdir()}
    before_ledger=ledger_snapshot()
    value = checkpoint._capture(root, job, record,inventory=inventory)
    assert value['science'] == dict(status='missing', diagnostic='missing_output')
    assert value['binding']['stage_owner'] == c.record_ref(root / 'jobs/001.json')
    # Mutable drafts are not terminal managed-call outputs or retained results.
    assert value['materials']
    assert all('artifacts/result.csv' != row['name'] for row in value['materials'])
    output=next(row for row in value['materials'] if row['name'].endswith('/artifacts/result.txt'))
    assert (root/output['original']['snapshot']).samefile(terminal['output'])
    assert terminal['output'].read_bytes()==b'retained scientific output'
    assert result.read_bytes() == b'public_metric\n0.61\n'
    assert not (record / 'result-identity.json').exists()
    assert ledger_snapshot()==before_ledger
    view = checkpoint.brief(checkpoint=value)
    assert view['current_understanding'] is None and view['science_status'] == 'missing'
    assert checkpoint.disposition(None) == 'repair'


def test_checkpoint_is_idempotent_after_next_fragment_edits_working_artifact(sealed_fragment):
    root, job, record, terminal = sealed_fragment
    science(record)
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    artifact = work / 'artifacts/model.txt'
    artifact.write_text('first implementation')
    value = capture_fragment(root, job, record)
    frozen = (record / 'checkpoint.json').read_bytes()
    artifact.write_text('later implementation')
    assert capture_fragment(root, job, record) == value
    assert (record / 'checkpoint.json').read_bytes() == frozen
    assert artifact.read_text() == 'later implementation'
    output=next(row for row in value['materials'] if row['name'].endswith('/artifacts/result.txt'))
    assert (root/output['original']['snapshot']).samefile(terminal['output'])
    assert terminal['output'].read_bytes()==b'retained scientific output'


def test_changed_native_original_refuses_checkpoint_validation(fragment):
    root, job, record = fragment
    science(record)
    inventory=c.accept_current_inventory(root,job['step'])
    checkpoint._capture(root,job,record,inventory=inventory)
    c.write(record / 'usage.json', dict(input_tokens=11, cached_input_tokens=3, output_tokens=4))
    with pytest.raises(ValueError, match='native original changed'):
        checkpoint._validate(root, job, record,inventory=inventory)


def test_invalid_science_is_saved_without_manufacturing_conclusion(fragment):
    root, job, record = fragment
    (record / 'science-output.json').write_text('{torn')
    c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'),
        raw_ref=c.file_ref(record / 'science-output.json')))
    value = capture_fragment(root, job, record)
    assert value['science']['status'] == 'invalid'
    assert checkpoint.brief(checkpoint=value)['scientific_summary'] is None
    assert (record / 'science-output.json').read_text() == '{torn'
    assert not (record / 'result-identity.json').exists()


def test_output_without_original_completion_binding_is_retained_as_unverified(fragment):
    root, job, record = fragment
    science(record)
    outcome=c.read(record/'native-outcome.json');outcome.pop('raw_ref')
    c.write(record/'native-outcome.json',outcome)
    value = capture_fragment(root, job, record)
    assert value['science'] == dict(status='unverified', diagnostic='unbound_final_output')
    assert checkpoint.brief(checkpoint=value)['scientific_summary'] is None
    assert (record / 'science-output.json').exists()


def test_unknown_usage_and_cleanup_still_preserve_mechanical_checkpoint(fragment):
    root, job, record = fragment
    (record / 'usage.json').unlink()
    c.write(record / 'native-outcome.json', dict(returncode=None, stop_reason=None,
        native_started=False, cleanup='cleanup_unknown'))
    value = capture_fragment(root, job, record)
    assert 'usage.json' in value['missing_native_originals']
    assert value['science']['status'] == 'missing'
    assert checkpoint.brief(checkpoint=value)['current_understanding'] is None
    assert not (record / 'result-identity.json').exists()


def test_unsafe_mutable_file_is_not_promoted_into_execution_checkpoint(fragment):
    root, job, record = fragment
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    (work / 'artifacts/unsafe').symlink_to(record / 'usage.json')
    value = capture_fragment(root, job, record)
    assert value['material_status'] == 'observed' and value['materials'] == []
    assert any(row['path'].endswith('/native-intent.json') for row in value['originals'])
    assert checkpoint.brief(checkpoint=value)['material_status'] == 'observed'


def test_bounded_multibyte_brief_preserves_omissions_and_no_host_bindings(sealed_fragment):
    root, job, record, terminal = sealed_fragment
    science(record, summary='研究摘要。' * 1000,
        scientific_result='未完成的问题。' * 1000, limitations=['尚未知。' * 30] * 8)
    assert (record / 'science-output.json').stat().st_size < c.MAX_RESULT_BYTES
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    for index in range(60):
        (work / 'artifacts' / f'{index:03d}-结果.csv').write_text('public\n')
    saved=capture_fragment(root, job, record)
    originals=[record/name for name in checkpoint.NATIVE_ORIGINALS if (record/name).is_file()]
    originals += [record/'checkpoint.json',root/'jobs/001.json',root/'settings.json']
    originals += [work/'artifacts'/f'{index:03d}-结果.csv' for index in range(60)]
    originals += [root/row['original']['snapshot'] for row in saved['materials']]
    before = {path: path.read_bytes() for path in originals}
    view = checkpoint.brief(checkpoint=saved)
    raw = checkpoint._bytes(view)
    assert len(raw) <= checkpoint.MAX_BRIEF_BYTES and view['truncated']
    assert len(view['direct_files']) + view['omitted_files'] == len(saved['materials'])
    assert view['omitted_files'] > 0
    assert str(root) not in raw.decode() and 'sha256' not in raw.decode() and 'stage_owner' not in raw.decode()
    assert {path: path.read_bytes() for path in before} == before


def test_json_escaping_is_bounded_even_without_direct_files(fragment):
    root, job, record = fragment
    escaped = '"\\\n' * 1000
    science(record, summary=escaped, scientific_result=escaped, limitations=[escaped] * 4)
    saved = capture_fragment(root, job, record)
    view = checkpoint.brief(checkpoint=saved)
    assert len(checkpoint._bytes(view)) <= checkpoint.MAX_BRIEF_BYTES
    assert view['truncated'] and view['omitted_limitations'] > 0
    assert view['direct_files'] == [] and view['omitted_files'] == 0
    assert view['science_status'] == 'present'


def test_only_sealed_terminal_outputs_are_archived_not_drafts_scratch_or_ancestor_inputs(sealed_fragment):
    root, job, record, terminal = sealed_fragment
    work = c.workspace(root, 1)
    for directory in ('artifacts', 'work', 'inputs/ancestor'):
        (work / directory).mkdir(parents=True, exist_ok=True)
    (work / 'artifacts/current.csv').write_text('current')
    (work / 'work/scratch.log').write_text('scratch')
    (work / 'inputs/ancestor/old.bin').write_text('ancestor')
    value = capture_fragment(root, job, record)
    assert value['materials']
    assert all(row['name'].startswith('experiments/'+terminal['accepted']['call_id']+'/artifacts/')
        for row in value['materials'])
    assert not any(row['name'].endswith('/current.csv') for row in value['materials'])
    assert any((root/row['original']['snapshot']).samefile(terminal['output']) for row in value['materials'])


def test_unrelated_native_attempt_cannot_be_checkpointed(fragment, tmp_path):
    root, job, record = fragment
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    c.write(unrelated / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    with pytest.raises(ValueError, match='exact original W scientific job'):
        capture_fragment(root, job, unrelated)
    assert not (unrelated / 'checkpoint.json').exists()


def test_host_review_overrides_continuation_without_rewriting_science():
    value = dict(stage_intent='continue', scientific_result='A small proxy improvement is conditional.')
    original = json.dumps(value)
    assert checkpoint.disposition(value) == 'continue'
    assert checkpoint.disposition(value, review_requested=True) == 'handoff'
    assert json.dumps(value) == original
    assert checkpoint.disposition(dict(stage_intent='review')) == 'review'
    with pytest.raises(OutputRepairRequired, match='Use continue, handoff'):
        checkpoint.disposition(dict(stage_intent='finish'))


FRAGMENT_FAKE = r'''import json,os,pathlib,sys,subprocess
from scidiscovery.research_loop import contracts as c
from scidiscovery.research_loop.worker_execution_bridge import SUBMISSION_DIRECTORY
args=sys.argv[1:];prompt=sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);home=pathlib.Path(os.environ['THIN_HWA_NATIVE_HOME'])
jobpath=pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']);job=json.loads(jobpath.read_text());root=jobpath.parent.parent
role=job['role'];resumed='resume' in args
counter=work/'work/fake-turns';number=int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(number+1))
sessionfile=home/'sessions/rollout.jsonl'
if resumed:
    thread=json.loads(sessionfile.read_text().splitlines()[0])['payload']['id'];assert thread in args
else:
    thread='11111111-2222-4333-8444-'+str(number+1).zfill(12)
    sessionfile.parent.mkdir(parents=True,exist_ok=True)
    sessionfile.write_text(json.dumps({'type':'session_meta','payload':{'id':thread,'cwd':str(work)}})+'\n')
print(json.dumps({'type':'thread.started','thread_id':thread}),flush=True)
case=os.environ.get('SCID_U2_FAKE_CASE','normal')
if role=='W':
    def exp(arguments):
        response=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.worker_execution_bridge',
            '--submission-directory',str(work/SUBMISSION_DIRECTORY),*arguments],capture_output=True,text=True)
        assert response.returncode==0,(response.stdout,response.stderr)
        return json.loads(response.stdout)
    if number==0:
        directory=work/'code';directory.mkdir(parents=True,exist_ok=True)
        payload=directory/'request.json';payload.write_text(json.dumps(dict(message='remote-original')))
        research=work/'work/fragment-research.json'
        ref='w'+str(job['step']).zfill(3)+'.fragment'
        research.write_text(json.dumps(dict(declarations=[dict(mechanism_ref=ref,action='declare',
            question='Does this bounded fixture preserve one original observation across fragments?',exploration=True)],
            mechanism_ref=ref,experiment_question=dict(question='Is the original fixture output retained?',
                change_criterion='A missing or changed original invalidates this transport fixture; no scientific qualification is claimed.'))))
        result=exp(['run','--payload',str(payload),'--research',str(research)])
        assert result['status']=='known_success'
        (work/'work/original-call').write_text(result['call_id'])
        (work/'artifacts/current.txt').write_text('first fragment result')
    else:
        call=(work/'work/original-call').read_text()
        assert exp(['query','--call-id',call])['state']=='succeeded'
        assert exp(['collect','--call-id',call])['status']=='collected'
        (work/'artifacts/current.txt').write_text('later fragment result')
        assert 'work/science-resume.json' in prompt or case in {'missing','failure'}
        if 'work/science-resume.json' in prompt:
            packet=work/'work/science-resume.json';assert len(packet.read_bytes())<=4096
            assert json.loads(packet.read_text())['science_status']=='present'
    if number==0 and case=='failure':
        print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
        sys.exit(7)
    if number==0 and case=='unknown_usage':sys.exit(0)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)
if role=='W' and number==0 and case=='missing':sys.exit(0)
payload={'summary':'One decision-relevant contribution','scientific_result':'A public observation with untested target limitations.',
         'limitations':['Internal no-model contract fixture'],'implementation_selection':None}
schema=c.result_schema(role,job['handoff_protocol'],**c.response_options(job))
if role=='H':payload.update(search_plan=None,next_action='experiment',route_reason='A discriminating comparison under the original scope')
if role=='W':
    turns=int(os.environ.get('SCID_U2_FAKE_TURNS','2'))
    intent='continue' if case=='normal' and number+1<turns else 'decision'
    payload.update(stage_intent=intent,mechanism_findings=None,consultation_question=None)
    if job.get('candidate_nomination_mechanisms'):
        payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
            reason='Transport fixture declares no scientific candidate suitability.')
            for row in job['candidate_nomination_mechanisms'] if row['status']!='waived'])
if role=='A':
    action='finish' if 'finish' in schema['properties']['next_action']['enum'] else 'continue_w'
    payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,
        next_action=action,route_reason='Retain bounded uncertainty',decision_request=None,portfolio_review=None)
    payload['final_coverage']=([dict(mechanism_ref=row['mechanism_ref'],status='unexplored',
        reason='Transport fixture asserts no scientific exploration depth; target evidence remains missing.')
        for row in job['final_coverage_mechanisms']] if job.get('final_coverage_mechanisms') and action=='finish' else None)
assert set(schema['required'])<=set(payload)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


@pytest.fixture
def internal_fragment_instance(tmp_path, monkeypatch, execution_registry, remote_registry):
    # Real child processes use CURRENT directly, preserving execution discovery
    # metadata without an obsolete schema override in this process or theirs.
    monkeypatch.setenv('PYTHONPATH', str(Path(c.__file__).resolve().parents[2])
                       + os.pathsep + os.environ.get('PYTHONPATH', ''))
    public = tmp_path / 'public-fragments'
    public.mkdir()
    goal = public / 'goal.md'
    goal.write_text('Run bounded discriminating work and independently review its target limits.')
    launcher = tmp_path / 'fake-fragments.py'
    launcher.write_text(FRAGMENT_FAKE)

    def prepare(*, context='new_context', cap=200, max_tokens=2000, steps=12):
        root = tmp_path / ('instance-' + context)
        controller.prepare(root, goal, public, [sys.executable, str(launcher)],
            native_backend='codex',
            max_tokens=max_tokens, max_seconds=120, max_steps=steps, model='no-model', reasoning_effort='high',
            search_policy=None, node_budget_mode='soft',
            execution_config=dict(executor='remote_execution:fixture',
                configuration=dict(require_contract=True,include_result=True)),
            node_budget_policy={role:dict(max_observed_tokens=cap,max_seconds=30) for role in 'HWAE'},
            resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=True,
                automatic_recovery=dict(enabled=False)),
            worker_fragment_policy=dict(context=context))
        return root
    return prepare


@pytest.mark.process_e2e
@pytest.mark.parametrize('context', ['same_session', 'new_context'])
def test_internal_runner_two_fragments_keep_one_w_stage_and_original_call(internal_fragment_instance, context):
    root = internal_fragment_instance(context=context)
    result=fragment_run(root,context)
    if context=='same_session':
        assert result['status']=='output_repair_unavailable',result
        assert result['reason']=='worker_resume_metadata_unavailable',result
        record=execution.origin(root,1)
        assert len(execution.records(root,1))==1
        assert len(list((experiment_client.record_directory(root,1)/'calls').iterdir()))==1
        assert native.usage(root)['observed_tokens']==14
        assert c.read(record/'checkpoint.json')['science']['status']=='present'
        assert c.read(record/'native-outcome.json')['native_identity']['session'] is None
        assert not (record/'result-identity.json').exists()
        with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_metadata_unavailable'):
            continuation.session(root,1,record)
        return
    assert result['status'] == 'finish',result
    jobs = [c.read(path) for path in sorted((root / 'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs] == ['W', 'A']
    chain = execution.records(root, 1)
    assert len(chain) == 2
    assert c.read(chain[1] / 'native-intent.json')['budget_slice']['previous_tokens'] == 14
    assert native.usage(root)['observed_tokens'] == 42
    configs = [c.read(path / 'experiment-config.json') for path in chain]
    assert configs[0]['scope'] == configs[1]['scope']
    assert configs[0]['scope']['execution_key'] == c.record_ref(root / 'jobs/001.json')
    calls = list((experiment_client.record_directory(root,1) / 'calls').iterdir())
    assert len(calls) == 1
    first = c.read(c.regular(chain[0] / 'checkpoint.json'))
    assert first['binding'] == dict(step=1,attempt=0,job_ref=c.record_ref(root/'jobs/001.json'),
        stage_owner=c.record_ref(root/'jobs/001.json'),record='native-evidence/001')
    assert c.read(chain[1]/'native-intent.json')['fragment_checkpoint'] == dict(path='native-evidence/001/checkpoint.json')
    retained=[root/row['original']['snapshot'] for row in first['materials']]
    assert any(path.read_bytes()==b'{"message": "remote-original"}' for path in retained)
    assert c.validate(c.workspace(root, 1))['role'] == 'W'
    threads = [c.read(path / 'native-outcome.json')['thread_id'] for path in chain]
    assert (threads[0] == threads[1]) == (context == 'same_session')
    homes = [execution.home(root, 1, number) for number in range(2)]
    assert (homes[0] == homes[1]) == (context == 'same_session')


def fragment_run(root, label):
    result=controller.run(root)
    if result['status']!='finish':
        diagnostics={}
        for step in (1,2):
            for name in ('native-intent.json','native-outcome.json','science-output.json',
                    'work-evidence.json','checkpoint.json','native.stderr','runner.stderr'):
                path=root/'native-evidence'/f'{step:03d}'/name
                if path.is_file():
                    if name.endswith('.json'):
                        diagnostics[path.relative_to(root).as_posix()]=path.read_text()
                    else:
                        with path.open('rb') as stream:
                            stream.seek(0,2);stream.seek(max(0,stream.tell()-4000))
                            diagnostics[path.relative_to(root).as_posix()]=stream.read(4000).decode(errors='replace')
        destination=Path(os.environ.get('SCID_TEST_LOG_DIR',str(root.parent)))/f'fragment-{label}-diagnostic.json'
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(dict(result=result,native_diagnostics=diagnostics),ensure_ascii=False,indent=2)+'\n')
    return result


@pytest.mark.process_e2e
def test_normal_fragments_do_not_spend_finite_failure_repair_attempts(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_TURNS', '4')
    root = internal_fragment_instance()
    result=fragment_run(root,'normal')
    assert result['status'] == 'finish',result
    chain = execution.records(root, 1)
    assert len(chain) == 4
    assert all(c.read(path / 'native-intent.json')['continuation_kind'] == 'fragment' for path in chain[1:])
    assert len(list((root / 'jobs').glob('*.json'))) == 2


@pytest.mark.process_e2e
def test_soft_target_allows_original_stage_to_continue_fragments(internal_fragment_instance):
    root = internal_fragment_instance(cap=14)
    result=fragment_run(root,'soft-target')
    assert result['status'] == 'finish',result
    assert len(execution.records(root, 1)) == 2
    assert c.read(execution.raw(root, 1))['stage_intent'] == 'decision'
    assert c.validate(c.workspace(root, 1))['role'] == 'W'
    record = execution.record(root, 1)
    ended = checkpoint.stage_end(root, c.read(root / 'jobs/001.json'), record)
    assert ended['reason'] == 'worker_requested'
    seal = c.read(execution.origin(root, 1) / 'result-identity.json')
    assert seal['execution_ref']['chain'][1]['stage-end.json'] == c.record_ref(record / 'stage-end.json')


@pytest.mark.process_e2e
def test_review_rechecked_after_original_writer_stops(internal_fragment_instance, monkeypatch):
    root = internal_fragment_instance(steps=2)
    eligible = continuation._eligible_original

    def crosses_boundary(*args, **kwargs):
        eligible(*args, **kwargs)
        monkeypatch.setattr(continuation, 'fragment_review', lambda *unused: dict(requested=True, reason='reserve_reached', enforced=True))

    monkeypatch.setattr(continuation, '_eligible_original', crosses_boundary)
    result=fragment_run(root,'review-recheck')
    # Bound this mechanical review case with the current protected FINAL;
    # transport completion does not assert scientific success.
    assert result['status'] == 'final_a_completed',result
    assert len(execution.records(root, 1)) == 1
    assert checkpoint.stage_end(root, c.read(root / 'jobs/001.json'), execution.origin(root, 1))['reason'] == 'reserve_reached'
    assert c.read(execution.raw(root, 1))['stage_intent'] == 'continue'
    assert len(list((experiment_client.record_directory(root,1)/'calls').iterdir()))==1


@pytest.mark.process_e2e
def test_missing_science_without_resume_metadata_retains_original_without_replay(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'missing')
    root = internal_fragment_instance()
    result=fragment_run(root,'missing')
    assert result['status'] == 'output_repair_unavailable',result
    assert result['reason']=='worker_resume_metadata_unavailable',result
    chain = execution.records(root, 1)
    assert len(chain) == 1
    first = c.read(c.regular(chain[0] / 'checkpoint.json'))
    assert first['science']['status'] == 'missing'
    assert first['binding'] == dict(step=1,attempt=0,job_ref=c.record_ref(root/'jobs/001.json'),
        stage_owner=c.record_ref(root/'jobs/001.json'),record='native-evidence/001')
    assert any(row['path']=='native-evidence/001/native-intent.json' for row in first['originals'])
    assert len(list((experiment_client.record_directory(root,1) / 'calls').iterdir())) == 1
    assert native.usage(root)['observed_tokens']==14
    with pytest.raises(continuation.ResumeUnavailable,match='worker_resume_metadata_unavailable'):
        continuation.start(root,1)
    assert len(execution.records(root,1))==1
    assert (c.workspace(root,1)/'work/fake-turns').read_text()=='1'


@pytest.mark.process_e2e
def test_unknown_native_usage_keeps_checkpoint_and_does_not_rotate_or_replay(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'unknown_usage')
    root = internal_fragment_instance(context='new_context')
    result=fragment_run(root,'unknown-usage')
    assert result['status'] == 'technical_pause',result
    record = execution.origin(root, 1)
    assert (record / 'checkpoint.json').exists()
    with pytest.raises((ValueError, continuation.ResumeUnavailable)):
        continuation.start(root, 1)
    assert len(execution.records(root, 1)) == 1
    assert (c.workspace(root, 1) / 'work/fake-turns').read_text() == '1'
    assert not (record / 'result-identity.json').exists()


@pytest.mark.process_e2e
def test_known_failure_without_resume_metadata_cannot_create_another_attempt(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'failure')
    root = internal_fragment_instance()
    result=fragment_run(root,'known-failure')
    assert result['status'] == 'native_stopped',result
    record = execution.origin(root, 1)
    until = time.monotonic() + 5
    while runtime.process_matches(c.read(record / 'runner-process.json'), execution.identity(root, 1, 0)):
        assert time.monotonic() < until
        time.sleep(.02)
    with pytest.raises(continuation.ResumeUnavailable):
        continuation.start(root,1)
    assert len(execution.records(root,1))==1
    assert (record/'checkpoint.json').exists()
    assert (c.workspace(root,1)/'work/fake-turns').read_text()=='1'
    assert len(list((experiment_client.record_directory(root,1) / 'calls').iterdir())) == 1
    assert native.usage(root)['observed_tokens'] == 14
