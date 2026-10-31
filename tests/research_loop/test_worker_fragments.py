"""Checkpoint boundary evidence only; no launcher, model or new stage contract."""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from scidiscovery.research_loop import checkpoint, contracts as c, controller, continuation, execution, experiment_client, native, runtime
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from execution_fixtures import execution_registry


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
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, cleanup='observed_group_cleaned'))
    c.write(record / 'usage.json', dict(input_tokens=10, cached_input_tokens=3, output_tokens=4))
    return root, job, record


def science(record, **changes):
    value = dict(summary='One discriminating observation', scientific_result='The target condition remains unresolved.',
                 limitations=['Synthetic checkpoint evidence only'], implementation_selection=None)
    root = record.parents[1]
    job = c.read(root / 'jobs/001.json')
    if 'stage_intent' in c.result_schema('W', job.get('handoff_protocol', 1))['properties']:
        value['stage_intent'] = 'review'
    if 'mechanism_findings' in c.result_schema('W', job.get('handoff_protocol', 1))['properties']:
        value['mechanism_findings'] = None
    value.update(changes)
    c.write(record / 'science-output.json', value)
    outcome = c.read(record / 'native-outcome.json')
    c.write(record / 'native-outcome.json', dict(outcome, raw_ref=c.file_ref(record / 'science-output.json')))


def test_missing_scientific_summary_preserves_native_identity_and_direct_results(fragment):
    root, job, record = fragment
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    result = work / 'artifacts/result.csv'
    result.write_text('public_metric\n0.61\n')
    value = checkpoint.capture(root, job, record)
    assert value['science'] == dict(status='missing', diagnostic='missing_output')
    assert value['binding']['stage_owner'] == c.record_ref(root / 'jobs/001.json')
    snapshot = value['materials'][0]['original']
    assert (root / snapshot['snapshot']).read_bytes() == result.read_bytes()
    assert not (record / 'result-identity.json').exists()
    assert not (root / 'control-ledger').exists()
    view = checkpoint.brief(root, job, record)
    assert view['current_understanding'] is None and view['science_status'] == 'missing'
    assert checkpoint.disposition(None) == 'repair'


def test_checkpoint_is_idempotent_after_next_fragment_edits_working_artifact(fragment):
    root, job, record = fragment
    science(record)
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    artifact = work / 'artifacts/model.txt'
    artifact.write_text('first implementation')
    value = checkpoint.capture(root, job, record)
    frozen = (record / 'checkpoint.json').read_bytes()
    artifact.write_text('later implementation')
    assert checkpoint.capture(root, job, record) == value
    assert (record / 'checkpoint.json').read_bytes() == frozen
    assert (root / value['materials'][0]['original']['snapshot']).read_text() == 'first implementation'


def test_changed_native_original_refuses_resume_preview(fragment):
    root, job, record = fragment
    science(record)
    checkpoint.capture(root, job, record)
    c.write(record / 'usage.json', dict(input_tokens=11, cached_input_tokens=3, output_tokens=4))
    with pytest.raises(ValueError, match='native original changed'):
        checkpoint.brief(root, job, record)


def test_invalid_science_is_saved_without_manufacturing_conclusion(fragment):
    root, job, record = fragment
    (record / 'science-output.json').write_text('{torn')
    c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'),
        raw_ref=c.file_ref(record / 'science-output.json')))
    value = checkpoint.capture(root, job, record)
    assert value['science']['status'] == 'invalid'
    assert checkpoint.brief(root, job, record)['scientific_summary'] is None
    assert (record / 'science-output.json').read_text() == '{torn'
    assert not (record / 'result-identity.json').exists()


def test_output_without_original_completion_binding_is_retained_as_unverified(fragment):
    root, job, record = fragment
    science(record)
    (record / 'native-outcome.json').write_text('{torn')
    value = checkpoint.capture(root, job, record)
    assert value['science'] == dict(status='unverified', diagnostic='unbound_final_output')
    assert checkpoint.brief(root, job, record)['scientific_summary'] is None
    assert (record / 'science-output.json').exists()


def test_unknown_usage_and_cleanup_still_preserve_mechanical_checkpoint(fragment):
    root, job, record = fragment
    (record / 'usage.json').unlink()
    c.write(record / 'native-outcome.json', dict(returncode=None, stop_reason=None, cleanup='cleanup_unknown'))
    value = checkpoint.capture(root, job, record)
    assert 'usage.json' in value['missing_native_originals']
    assert value['science']['status'] == 'missing'
    assert checkpoint.brief(root, job, record)['current_understanding'] is None
    assert not (record / 'result-identity.json').exists()


def test_unsafe_direct_file_does_not_erase_original_execution_checkpoint(fragment):
    root, job, record = fragment
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    (work / 'artifacts/unsafe').symlink_to(record / 'usage.json')
    value = checkpoint.capture(root, job, record)
    assert value['material_status'] == 'unavailable' and value['materials'] == []
    assert any(row['path'].endswith('/native-intent.json') for row in value['originals'])
    assert checkpoint.brief(root, job, record)['material_status'] == 'unavailable'


def test_bounded_multibyte_brief_preserves_omissions_and_no_host_bindings(fragment):
    root, job, record = fragment
    science(record, scientific_result='未完成的问题。' * 1000, limitations=['尚未知。' * 200] * 8)
    assert (record / 'science-output.json').stat().st_size < c.MAX_RESULT_BYTES
    work = c.workspace(root, 1)
    (work / 'artifacts').mkdir(exist_ok=True)
    for index in range(60):
        (work / 'artifacts' / f'{index:03d}-结果.csv').write_text('public\n')
    checkpoint.capture(root, job, record)
    before = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
    view = checkpoint.brief(root, job, record)
    raw = checkpoint._bytes(view)
    assert len(raw) <= checkpoint.MAX_BRIEF_BYTES and view['truncated']
    assert len(view['direct_files']) + view['omitted_files'] == 60
    assert str(root) not in raw.decode() and 'sha256' not in raw.decode() and 'stage_owner' not in raw.decode()
    assert {path: path.read_bytes() for path in before} == before


def test_json_escaping_is_bounded_even_without_direct_files(fragment):
    root, job, record = fragment
    escaped = '"\\\n' * 1000
    science(record, summary=escaped, scientific_result=escaped, limitations=[escaped] * 4)
    checkpoint.capture(root, job, record)
    view = checkpoint.brief(root, job, record)
    assert len(checkpoint._bytes(view)) <= checkpoint.MAX_BRIEF_BYTES
    assert view['truncated'] and view['omitted_limitations'] > 0
    assert view['direct_files'] == [] and view['omitted_files'] == 0
    assert view['science_status'] == 'present'


def test_only_current_direct_outputs_are_archived_not_scratch_or_ancestor_inputs(fragment):
    root, job, record = fragment
    work = c.workspace(root, 1)
    for directory in ('artifacts', 'work', 'inputs/ancestor'):
        (work / directory).mkdir(parents=True, exist_ok=True)
    (work / 'artifacts/current.csv').write_text('current')
    (work / 'work/scratch.log').write_text('scratch')
    (work / 'inputs/ancestor/old.bin').write_text('ancestor')
    value = checkpoint.capture(root, job, record)
    assert [row['name'] for row in value['materials']] == ['artifacts/current.csv']
    assert len(list((root / 'host-history/objects').iterdir())) == 1


def test_unrelated_native_attempt_cannot_be_checkpointed(fragment, tmp_path):
    root, job, record = fragment
    unrelated = tmp_path / 'unrelated'
    unrelated.mkdir()
    c.write(unrelated / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    with pytest.raises(ValueError, match='accepted original native attempt'):
        checkpoint.capture(root, job, unrelated)
    assert not (unrelated / 'checkpoint.json').exists()


def test_host_review_overrides_continuation_without_rewriting_science():
    value = dict(stage_intent='continue', scientific_result='A small proxy improvement is conditional.')
    original = json.dumps(value)
    assert checkpoint.disposition(value) == 'continue'
    assert checkpoint.disposition(value, review_requested=True) == 'review'
    assert json.dumps(value) == original
    assert checkpoint.disposition(dict(stage_intent='review')) == 'review'
    with pytest.raises(OutputRepairRequired, match='continue this mechanism'):
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
        code=work/'code';code.mkdir(exist_ok=True)
        script=code/'public.py'
        script.write_text('from pathlib import Path\nprint("public discriminating observation")\nPath("artifacts/current.txt").write_text("first fragment result")\nPath("artifacts/result.json").write_text('+repr(json.dumps(dict(version=1,metrics=[],outputs=[dict(path='artifacts/current.txt')])))+')\n')
        result=exp(['run','--experiment-dir',str(work/'experiments/original-fragment'),'--script',str(script)])
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
if role=='H':payload.update(search_plan=None,next_action='experiment',route_reason='A discriminating comparison under the original scope')
if role=='W':
    turns=int(os.environ.get('SCID_U2_FAKE_TURNS','2'))
    intent='continue' if case=='normal' and number+1<turns else 'review'
    payload.update(stage_intent=intent,mechanism_findings=None)
if role=='A':payload.update(search_review=None,candidate_recommendation=None,progress_assessment=None,next_action='finish',route_reason='Retain bounded uncertainty')
for field in ('decision_request','portfolio_review'):
    if field in c.result_schema(role,job.get('handoff_protocol',1))['properties']:payload[field]=None
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
'''


@pytest.fixture
def internal_fragment_instance(tmp_path, monkeypatch, execution_registry):
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

    def prepare(*, context='same_session', cap=200, max_tokens=2000, steps=12):
        root = tmp_path / ('instance-' + context)
        controller.prepare(root, goal, public, [sys.executable, str(launcher)],
            max_tokens=max_tokens, max_seconds=120, max_steps=steps, model='no-model', reasoning_effort='high',
            search_policy=None, node_budget_mode='soft',experiment_mode='local',
            node_budget_policy={role:dict(max_observed_tokens=cap,max_seconds=30) for role in 'HWAE'},
            resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=True),
            worker_fragment_policy=dict(context=context))
        return root
    return prepare


@pytest.mark.process_e2e
@pytest.mark.parametrize('context', ['same_session', 'new_context'])
def test_internal_runner_two_fragments_keep_one_w_stage_and_original_call(internal_fragment_instance, context):
    root = internal_fragment_instance(context=context)
    result=controller.run(root)
    diagnostic={}
    diagnostic_path=None
    if result['status']!='finish':
        for step in (1,2):
            for name in ('native-outcome.json','native.stderr','runner.stderr'):
                path=root/'native-evidence'/f'{step:03d}'/name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(0,2);stream.seek(max(0,stream.tell()-2000))
                        diagnostic[path.relative_to(root).as_posix()]=stream.read(2000).decode(errors='replace')
        diagnostic_path=Path(os.environ.get('SCID_TEST_LOG_DIR',str(root.parent)))/f'fragment-{context}-diagnostic.json'
        diagnostic_path.parent.mkdir(parents=True,exist_ok=True)
        diagnostic_path.write_text(json.dumps(dict(result=result,native_diagnostics=diagnostic),ensure_ascii=False,indent=2)+'\n')
    assert result['status'] == 'finish',str(diagnostic_path)
    jobs = [c.read(path) for path in sorted((root / 'jobs').glob('*.json'))]
    assert [job['role'] for job in jobs] == ['H', 'W', 'A']
    chain = execution.records(root, 2)
    assert len(chain) == 2
    assert c.read(chain[1] / 'native-intent.json')['budget_slice']['previous_tokens'] == 14
    assert native.usage(root)['observed_tokens'] == 56
    configs = [c.read(path / 'experiment-config.json') for path in chain]
    assert configs[0]['scope'] == configs[1]['scope']
    assert configs[0]['scope']['execution_key'] == c.record_ref(root / 'jobs/002.json')
    calls = list((experiment_client.record_directory(root,2) / 'calls').iterdir())
    assert len(calls) == 1
    first = c.read(c.regular(chain[0] / 'checkpoint.json'))
    assert first['binding'] == dict(step=2,attempt=0,job_ref=c.record_ref(root/'jobs/002.json'),
        stage_owner=c.record_ref(root/'jobs/002.json'),record='native-evidence/002')
    assert c.read(chain[1]/'native-intent.json')['fragment_checkpoint'] == dict(path='native-evidence/002/checkpoint.json')
    assert (root / first['materials'][0]['original']['snapshot']).read_text() == 'first fragment result'
    assert c.validate(c.workspace(root, 2))['role'] == 'W'
    threads = [c.read(path / 'native-outcome.json')['thread_id'] for path in chain]
    assert (threads[0] == threads[1]) == (context == 'same_session')
    homes = [execution.home(root, 2, number) for number in range(2)]
    assert (homes[0] == homes[1]) == (context == 'same_session')


def test_normal_fragments_do_not_spend_finite_failure_repair_attempts(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_TURNS', '4')
    root = internal_fragment_instance()
    assert controller.run(root)['status'] == 'finish'
    chain = execution.records(root, 2)
    assert len(chain) == 4
    assert all(c.read(path / 'native-intent.json')['continuation_kind'] == 'fragment' for path in chain[1:])
    assert len(list((root / 'jobs').glob('*.json'))) == 3


def test_soft_target_allows_original_stage_to_continue_fragments(internal_fragment_instance):
    root = internal_fragment_instance(cap=14)
    assert controller.run(root)['status'] == 'finish'
    assert len(execution.records(root, 2)) == 2
    assert c.read(execution.raw(root, 2))['stage_intent'] == 'review'
    assert c.validate(c.workspace(root, 2))['role'] == 'W'
    record = execution.record(root, 2)
    ended = checkpoint.stage_end(root, c.read(root / 'jobs/002.json'), record)
    assert ended['reason'] == 'worker_requested'
    seal = c.read(execution.origin(root, 2) / 'result-identity.json')
    assert seal['execution_ref']['chain'][1]['stage-end.json'] == c.record_ref(record / 'stage-end.json')


def test_review_rechecked_after_original_writer_stops(internal_fragment_instance, monkeypatch):
    root = internal_fragment_instance()
    eligible = continuation._eligible_original

    def crosses_boundary(*args, **kwargs):
        eligible(*args, **kwargs)
        monkeypatch.setattr(continuation, 'fragment_review', lambda *unused: dict(requested=True, reason='reserve_reached', enforced=True))

    monkeypatch.setattr(continuation, '_eligible_original', crosses_boundary)
    assert controller.run(root)['status'] == 'finish'
    assert len(execution.records(root, 2)) == 1
    assert checkpoint.stage_end(root, c.read(root / 'jobs/002.json'), execution.origin(root, 2))['reason'] == 'reserve_reached'
    assert c.read(execution.raw(root, 2))['stage_intent'] == 'continue'


@pytest.mark.process_e2e
def test_missing_science_repairs_output_without_replaying_original_call(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'missing')
    root = internal_fragment_instance()
    result=controller.run(root)
    diagnostic={}
    diagnostic_path=None
    if result['status']!='finish':
        for step in (1,2):
            for name in ('native-outcome.json','native.stderr','runner.stderr'):
                path=root/'native-evidence'/f'{step:03d}'/name
                if path.is_file():
                    with path.open('rb') as stream:
                        stream.seek(0,2);stream.seek(max(0,stream.tell()-2000))
                        diagnostic[path.relative_to(root).as_posix()]=stream.read(2000).decode(errors='replace')
        diagnostic_path=Path(os.environ.get('SCID_TEST_LOG_DIR',str(root.parent)))/'fragment-missing-diagnostic.json'
        diagnostic_path.parent.mkdir(parents=True,exist_ok=True)
        diagnostic_path.write_text(json.dumps(dict(result=result,native_diagnostics=diagnostic),ensure_ascii=False,indent=2)+'\n')
    assert result['status'] == 'finish',str(diagnostic_path)
    chain = execution.records(root, 2)
    assert len(chain) == 2
    first = c.read(c.regular(chain[0] / 'checkpoint.json'))
    assert first['science']['status'] == 'missing'
    assert first['binding'] == dict(step=2,attempt=0,job_ref=c.record_ref(root/'jobs/002.json'),
        stage_owner=c.record_ref(root/'jobs/002.json'),record='native-evidence/002')
    assert any(row['path']=='native-evidence/002/native-intent.json' for row in first['originals'])
    assert c.read(chain[1] / 'native-intent.json')['continuation_kind'] == 'repair'
    assert len(list((experiment_client.record_directory(root,2) / 'calls').iterdir())) == 1


def test_unknown_native_usage_keeps_checkpoint_and_does_not_rotate_or_replay(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'unknown_usage')
    root = internal_fragment_instance(context='new_context')
    assert controller.run(root)['status'] == 'native_stopped'
    record = execution.origin(root, 2)
    assert (record / 'checkpoint.json').exists()
    with pytest.raises((ValueError, continuation.ResumeUnavailable)):
        continuation.start(root, 2)
    assert len(execution.records(root, 2)) == 1
    assert (c.workspace(root, 2) / 'work/fake-turns').read_text() == '1'
    assert not (record / 'result-identity.json').exists()


def test_known_failure_resumes_exact_thread_without_new_managed_work(internal_fragment_instance, monkeypatch):
    monkeypatch.setenv('SCID_U2_FAKE_CASE', 'failure')
    root = internal_fragment_instance()
    assert controller.run(root)['status'] == 'native_stopped'
    record = execution.origin(root, 2)
    until = time.monotonic() + 5
    while runtime.process_matches(c.read(record / 'runner-process.json'), execution.identity(root, 2, 0)):
        assert time.monotonic() < until
        time.sleep(.02)
    assert continuation.start(root, 2)['role'] == 'W'
    assert controller.run(root)['status'] == 'finish'
    assert len(list((experiment_client.record_directory(root,2) / 'calls').iterdir())) == 1
    assert native.usage(root)['observed_tokens'] == 56
