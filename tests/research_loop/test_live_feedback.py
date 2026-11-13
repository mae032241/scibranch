"""Read current managed receipts before W seals, without charging or granting work."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import accepted_budget, cli, contracts as c, controller, feedback, history
from cov_execution import lineage as cov_lineage
from test_cov_lineage import wrapper  # Real CLI fixture, never mocked transport.
from test_history_handoff import finish
from execution_fixtures import execution_registry


def hint(work, *, target='within', reserve='within'):
    path = Path(work) / 'work/budget-status.json'
    c.write(path, dict(scope='advice_only_not_authorization_or_scientific_evidence', updated_at_unix=123.,
        shared_observed_tokens_remaining_upper_bound=900, shared_active_seconds_remaining=100.,
        current_work_observed_tokens_remaining_upper_bound=700, current_work_active_seconds_available=80.,
        target_state=target, reserve_state=reserve, secret='not public feedback', raw_log='never forward'))
    return path


def live_scope(config):
    result = dict(config['scope'])
    result['execution_keys'] = [result.pop('execution_key')]
    return result


@pytest.mark.process_e2e
def test_running_call_is_visible_before_w_or_call_finishes_and_queries_are_readonly(wrapper):
    code = wrapper.backend.read_text().replace("if mode=='failed':row", "if mode=='slow':\n import time;time.sleep(2)\nif mode=='failed':row", 1)
    wrapper.backend.write_text(code)
    script = wrapper.script('inflight', 'slow')
    env = dict(os.environ, PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''), EXP_WALL_CAP_S='4')
    launch = ('import runpy,sys; from scidiscovery.research_loop import worker_execution_bridge as bridge; '
              'bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None)); '
              'sys.argv=["cov_execution.lineage",*sys.argv[1:]]; '
              'runpy.run_module("cov_execution.lineage",run_name="__main__")')
    process = subprocess.Popen([sys.executable, '-c', launch,
        '--config', str(wrapper.config), 'run', '--script', str(script), '--new-plan'],
        cwd=wrapper.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        record = Path(json.loads(wrapper.config.read_text())['record_dir'])
        until = time.monotonic() + 10
        while process.poll() is None and not list(record.glob('calls/*/process.json')) and time.monotonic() < until:
            time.sleep(.01)
        if process.poll() is not None:
            out, err = process.communicate(timeout=5)
            pytest.fail(json.dumps(dict(returncode=process.returncode, stdout=out, stderr=err)))
        assert list(record.glob('calls/*/process.json')) and not list(record.glob('calls/*/completed.json'))
        # The live receipt owner can update its observation concurrently.
        before = {p: p.read_bytes() for p in record.glob('calls/*/accepted.json')}
        first = wrapper.cli('lineage-status')
        second = wrapper.cli('lineage-status')
        assert first['calls'][0]['status'] == second['calls'][0]['status'] == 'running'
        assert first['counts']['running'] == 1 and second['counts'] == first['counts']
        assert {p: p.read_bytes() for p in before} == before
        assert first['feedback']['budget']['inflight_usage_unknown'] is True
        out, err = process.communicate(timeout=10)
        assert process.returncode == 0, err
        assert json.loads(out)['status'] == 'known_success'
        done = wrapper.cli('lineage-status')
        assert done['counts']['known_success'] == 1 and done['counts']['running'] == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['accepted.json', 'completed.json'])
def test_torn_metadata_never_proves_success_or_reconciles_originals(wrapper, name):
    result = wrapper.run('initial', 'success', '--new-plan')
    directory = wrapper.directory(result)
    if name in {'accepted.json', 'process.json'}:
        (directory / 'completed.json').unlink()
    path = directory / name
    path.chmod(0o600)
    path.write_text('{')
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    if name == 'accepted.json':
        # Without a readable accepted binding, CURRENT cannot resolve even the
        # call's original configuration; it supplies no per-call observation.
        value = wrapper.cli('lineage-status', expected=2)
        assert value['status'] == 'unresolved'
        assert not {'calls', 'counts', 'coverage', 'candidate_id'} & value.keys()
        assert value['feedback']['budget']['inflight_usage_unknown'] is True
    else:
        value = wrapper.cli('lineage-status')
        assert value['calls'][0]['status'] == 'unknown' and value['counts']['known_success'] == 0
        assert value['coverage'] == 'partial' and value['calls'][0]['candidate_id'] is None
    assert before == {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert [p.name for p in directory.parent.iterdir()] == [directory.name]


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['accepted.json', 'row.json', 'terminal.json'])
def test_published_metadata_mismatch_is_hard_error_not_optional_unknown(wrapper, name):
    result = wrapper.run('initial', 'success', '--new-plan')
    path = wrapper.directory(result) / name
    value = json.loads(path.read_text())
    value['tamper'] = True
    path.chmod(0o600)
    path.write_text(json.dumps(value))
    assert wrapper.cli('lineage-status', expected=3)['status'] == 'integrity_error'


@pytest.mark.parametrize('target,reserve,reason', [('reached', 'within', 'target_reached'), ('within', 'reached', 'reserve_reached')])
def test_fixed_budget_advice_has_observation_time_and_unknown_usage_without_authority(tmp_path, target, reserve, reason):
    work = tmp_path / 'node'
    path = hint(work, target=target, reserve=reserve)
    original = path.read_bytes()
    value = feedback.budget_hint(work)
    assert value['observed_at'] == 123. and value['inflight_usage_unknown'] is True
    assert value['review_request'] == dict(requested=True, reason=reason, enforced=False)
    assert value['current_work_observed_tokens_remaining_upper_bound'] == 700
    assert 'not public feedback' not in json.dumps(value) and 'never forward' not in json.dumps(value)
    assert path.read_bytes() == original
    path.unlink()
    assert feedback.budget_hint(work)['status'] == 'unavailable'
    assert feedback.budget_hint(work)['observed_at'] is None


def test_budget_hint_symlink_is_not_an_arbitrary_host_read(tmp_path):
    work = tmp_path / 'node'
    path = hint(work)
    external = tmp_path / 'private'
    external.write_text('must not be read or changed')
    path.unlink()
    path.symlink_to(external)
    with pytest.raises(history.ObserverIntegrityError):
        feedback.budget_hint(work)
    assert external.read_text() == 'must not be read or changed'


@pytest.mark.parametrize('key,value', [('target_state', []), ('reserve_state', {}),
    ('shared_observed_tokens_remaining_upper_bound', 2**1024), ('updated_at_unix', 2**1024)])
def test_malformed_budget_advice_is_unavailable_without_raising(tmp_path, key, value):
    work = tmp_path / 'node'
    path = hint(work)
    content = c.read(path)
    content[key] = value
    c.write(path, content)
    result = feedback.budget_hint(work)
    assert result['status'] == 'unavailable' and result['inflight_usage_unknown'] is True
    assert result['observed_at'] is None and result['review_request'] is None


@pytest.mark.process_e2e
def test_tool_budget_symlink_reports_bounded_integrity_error(wrapper):
    wrapper.run('public-candidate', 'success', '--new-plan')
    path = hint(wrapper.root)
    private = wrapper.root / 'private'
    private.write_text('no disclosure')
    path.unlink()
    path.symlink_to(private)
    result = wrapper.cli('lineage-status', expected=3)
    assert result['status'] == 'integrity_error' and 'no disclosure' not in json.dumps(result)
    assert private.read_text() == 'no disclosure'


@pytest.mark.process_e2e
@pytest.mark.parametrize('data', [b'{', b'\xff', b' ' * 4097, b'[' * 1500 + b']' * 1500])
def test_tool_malformed_budget_bytes_are_unavailable_and_preserved(wrapper, data):
    wrapper.run('public-candidate', 'success', '--new-plan')
    path = hint(wrapper.root)
    path.write_bytes(data)
    value = wrapper.cli('lineage-status')['feedback']['budget']
    assert value['status'] == 'unavailable' and value['inflight_usage_unknown'] is True
    assert value['observed_at'] is None and value['review_request'] is None
    assert path.read_bytes() == data


@pytest.mark.process_e2e
def test_tool_budget_directory_is_a_bounded_integrity_error(wrapper):
    wrapper.run('public-candidate', 'success', '--new-plan')
    path = hint(wrapper.root)
    path.unlink()
    path.mkdir()
    result = wrapper.cli('lineage-status', expected=3)
    assert result['status'] == 'integrity_error' and str(path) not in json.dumps(result)
    assert path.is_dir() and not list(path.iterdir())


def test_budget_hint_read_error_is_typed_without_private_oserror(tmp_path, monkeypatch):
    work = tmp_path / 'node'
    path = hint(work)
    original = Path.open

    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError('private filesystem detail')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(history.ObserverIntegrityError) as error:
        feedback.budget_hint(work)
    assert 'private filesystem detail' not in str(error.value)


def test_budget_notice_channel_is_projected_honestly_with_its_tool_boundary_limit(tmp_path):
    work=tmp_path/'node';path=hint(work)
    value=c.read(path)
    value['notification_channel']=dict(status='available',mechanism='codex_post_tool_use')
    c.write(path,value)
    channel=feedback.budget_hint(work)['notification_channel']
    assert channel['status']=='available' and channel['mechanism']=='codex_post_tool_use'
    assert 'running model requests and hosted web cannot be interrupted' in channel['limitation']
    value['notification_channel']=dict(status='unavailable',reason='hook_trust_unqualified')
    c.write(path,value)
    assert feedback.budget_hint(work)['notification_channel']==value['notification_channel']
    value['notification_channel']=dict(status='available',mechanism='custom_unverified')
    c.write(path,value)
    assert feedback.budget_hint(work)['notification_channel']['status']=='unavailable'


def test_budget_hint_ancestor_symlink_is_an_integrity_error(tmp_path):
    original = tmp_path / 'original'
    path = hint(original)
    contents = path.read_bytes()
    work = tmp_path / 'node'
    work.symlink_to(original, target_is_directory=True)
    with pytest.raises(history.ObserverIntegrityError):
        feedback.budget_hint(work)
    assert path.read_bytes() == contents


@pytest.mark.process_e2e
def test_large_public_metrics_remain_known_but_are_omitted_from_feedback(wrapper):
    code = wrapper.backend.read_text().replace('val_score=.5,val_acc=.5',
        'val_score=2**1024,val_acc=2**1024,wall_seconds=2**1024')
    wrapper.backend.write_text(code)
    result = wrapper.run('large-metrics', 'success', '--new-plan')
    assert result['status'] == 'known_success'
    assert not {'val_score', 'val_acc', 'wall_seconds'} & result.keys()
    directory = wrapper.directory(result)
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    status = wrapper.cli('lineage-status')
    assert status['counts']['known_success'] == 1
    assert status['calls'][0]['metric'] is status['calls'][0]['wall_seconds'] is None
    assert json.loads((directory / 'row.json').read_text())['val_score'] == 2**1024
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.process_e2e
@pytest.mark.parametrize('name,value', [('accepted.json', {'parent': []})])
def test_tool_malformed_live_metadata_is_bounded_integrity_error(wrapper, name, value):
    result = wrapper.run('public-candidate', 'success', '--new-plan')
    directory = wrapper.directory(result)
    (directory / 'completed.json').unlink()
    path = directory / name
    if isinstance(value, dict):
        value = dict(json.loads(path.read_text()), **value)
    path.chmod(0o600)
    path.write_text(json.dumps(value))
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    response = wrapper.cli('lineage-status', expected=3)
    assert response['status'] == 'integrity_error' and str(directory) not in json.dumps(response)
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['accepted.json', 'completed.json'])
def test_tool_torn_utf8_live_metadata_is_unresolved_or_unknown(wrapper, name):
    result = wrapper.run('public-candidate', 'success', '--new-plan')
    directory = wrapper.directory(result)
    if name != 'completed.json':
        (directory / 'completed.json').unlink()
    path = directory / name
    path.chmod(0o600)
    path.write_bytes(b'\xff')
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    if name == 'accepted.json':
        value = wrapper.cli('lineage-status', expected=2)
        assert value['status'] == 'unresolved'
        assert not {'calls', 'counts', 'coverage', 'candidate_id'} & value.keys()
        assert value['feedback']['budget']['inflight_usage_unknown'] is True
    else:
        value = wrapper.cli('lineage-status')
        assert value['counts']['unknown'] == 1 and value['counts']['known_success'] == 0
        assert value['coverage'] == 'partial' and value['calls'][0]['candidate_id'] is None
    assert before == {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    assert [p.name for p in directory.parent.iterdir()] == [directory.name]


@pytest.mark.process_e2e
@pytest.mark.parametrize('intent', [None])
@pytest.mark.parametrize('name', ['native-intent.json', 'native-outcome.json'])
def test_host_status_malformed_live_original_reports_bounded_integrity_error(tmp_path, intent, name):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'goal'
    objective.write_text('Original public goal')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
        max_steps=4, model='fixture', reasoning_effort='high', search_policy=None,
        history_observer=dict(adapter=cov_lineage.__file__, contract_version=2,
            config=dict(runs='data/runs', calls='project/nodes', evaluation_key='public-fixture-v1')))
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json')))
    accepted_budget.accept_native(root, 1, record, c.read(record / 'native-intent.json'))
    if (record / name).exists():(record / name).chmod(0o600)
    c.write(record / name, intent)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    if name == 'native-intent.json':
        with pytest.raises((history.ObserverIntegrityError, ValueError)):
            feedback.live(root, job)
    env = dict(os.environ, PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''))
    reply = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli', 'status', '--root', str(root)],
        env=env, capture_output=True, text=True, timeout=10)
    assert reply.returncode == 1 and not reply.stderr
    result = json.loads(reply.stdout)
    assert result['status'] == 'error' and 'original' in result['error'].lower()
    assert str(root) not in reply.stdout
    assert before == {p: p.read_bytes() for p in before}


def test_live_intent_excessive_json_depth_is_typed_integrity_error(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'goal'
    objective.write_text('Original public goal')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
        max_steps=4, model='fixture', reasoning_effort='high', search_policy=None,
        history_observer=dict(adapter=cov_lineage.__file__, contract_version=2,
            config=dict(runs='data/runs', calls='project/nodes', evaluation_key='public-fixture-v1')))
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    record.mkdir(parents=True, exist_ok=True)
    path = record / 'native-intent.json'
    depth = sys.getrecursionlimit() + 100
    data = b'[' * depth + b']' * depth
    path.write_bytes(data)
    # This unit targets parsing after the execution owner has supplied its record.
    monkeypatch.setattr(feedback.execution, 'records', lambda root, step: [record])
    with pytest.raises(history.ObserverIntegrityError):
        feedback.live(root, job)
    original_read = c.read

    def recursive_read(original):
        if original == path:
            raise RecursionError('parser recursion limit')
        return original_read(original)

    monkeypatch.setattr(c, 'read', recursive_read)
    with pytest.raises(history.ObserverIntegrityError, match='^Live feedback original intent cannot be read$'):
        feedback.live(root, job)
    assert path.read_bytes() == data


def test_no_observer_is_explicitly_unavailable_not_zero_progress(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'goal'
    objective.write_text('Original goal')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
                       max_steps=4, model='fixture', reasoning_effort='high')
    job = c.build_job(root, 1, 'H', [])
    assert c.settings(root)['live_feedback_version'] == job['live_feedback_version'] == 1
    value = feedback.live(root, job)
    assert value['status'] == value['coverage'] == 'unavailable' and value['counts'] is None
    assert value['budget']['inflight_usage_unknown'] is True


@pytest.mark.process_e2e
def test_registered_local_host_feedback_requires_no_domain_observer(tmp_path):
    from scidiscovery.research_loop import experiments
    public=tmp_path/'public';public.mkdir();objective=public/'goal';objective.write_text('Original public goal')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-launched'],max_tokens=1000,max_seconds=60,
        max_steps=4,model='fixture',reasoning_effort='high',search_policy=None)
    job=c.build_job(root,1,'W',[]);record=c.host_record(c.workspace(root,1))
    intent = dict(task_ref=c.record_ref(root/'jobs/001.json'))
    c.write(record/'native-intent.json',intent)
    accepted_budget.accept_native(root,1,record,intent)
    path=c.workspace(root,1)/'work/local-config.json'
    experiments.configure(root,1,'local_execution:command',dict(run_argv=[sys.executable],max_seconds=10),path)
    from test_native_execution_inputs import research_file
    request = research_file(path, 1)
    source=c.workspace(root,1)/'code/ordinary.py'
    source.parent.mkdir(exist_ok=True)
    source.write_text('print("public output without domain metric")\n')
    env=dict(os.environ,PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''))
    # Explicit component host receiver; this fixture makes no native namespace claim.
    launch=('import runpy,sys; from scidiscovery.research_loop import worker_execution_bridge as bridge; '
        'bridge._LOCAL_LAUNCH.set(lambda config,accepted,command,cwd:(command,cwd,None)); '
        'sys.argv=["scidiscovery.research_loop.experiments",*sys.argv[1:]]; '
        'runpy.run_module("scidiscovery.research_loop.experiments",run_name="__main__")')
    reply=subprocess.run([sys.executable,'-c',launch,'--config',str(path),
        'run','--script',str(source),'--research',str(request)],env=env,capture_output=True,text=True,timeout=20)
    assert reply.returncode==0,(reply.stdout,reply.stderr)
    presentation=Path(c.read(record/'experiment-config.json')['record_dir'])/'live-status.json'
    assert c.read(presentation)['source']=='execution_receipt_owner'
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file() and p!=presentation}
    value=cli.status(root)['nodes'][0]['live_feedback']
    assert value['status']=='observed' and value['counts']['known_success']==1
    assert value['calls'][0]['candidate_id'] is value['calls'][0]['metric'] is None
    assert 'history_observer' not in c.settings(root)
    assert before=={p:p.read_bytes() for p in before}
