"""Read current managed receipts before W seals, without charging or granting work."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scidiscovery.research_loop import cli, contracts as c, controller, feedback, history
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
    process = subprocess.Popen([sys.executable, '-m', 'cov_execution.lineage',
        '--config', str(wrapper.config), 'run', '--script', str(script), '--new-plan'],
        cwd=wrapper.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        record = Path(json.loads(wrapper.config.read_text())['record_dir'])
        until = time.monotonic() + 10
        while not list(record.glob('calls/*/process.json')) and time.monotonic() < until:
            time.sleep(.01)
        assert list(record.glob('calls/*/process.json')) and not list(record.glob('calls/*/completed.json'))
        before = {p: p.read_bytes() for p in record.rglob('*.json')}
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
@pytest.mark.parametrize('name', ['accepted.json', 'completed.json', 'process.json'])
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
def test_pid_mismatch_without_completion_is_unknown_not_running(wrapper):
    result = wrapper.run('initial', 'success', '--new-plan')
    directory = wrapper.directory(result)
    (directory / 'completed.json').unlink()
    process = json.loads((directory / 'process.json').read_text())
    process['start_ticks'] += 1
    (directory / 'process.json').write_text(json.dumps(process))
    value = wrapper.cli('lineage-status')
    assert value['calls'][0]['status'] == 'unknown' and value['counts']['running'] == 0


@pytest.mark.process_e2e
def test_more_than_sixteen_calls_remain_counted_once_without_large_asset_reads(wrapper, monkeypatch):
    for index in range(18):
        wrapper.run(f'candidate-{index}', 'success', '--new-plan')
    config = json.loads(wrapper.config.read_text())
    original = cov_lineage._read

    def metadata_only(path, limit=cov_lineage.MAX_BYTES):
        assert Path(path).name not in {'source.py', 'model.pkl'}
        return original(path, limit)

    monkeypatch.setattr(cov_lineage, '_read', metadata_only)
    before = {p: p.read_bytes() for p in Path(config['record_dir']).rglob('*.json')}
    first = cov_lineage._progress(config, live_scope(config))
    again = cov_lineage._progress(config, live_scope(config))
    assert len(first['calls']) == 16 and first['omitted_count'] == 2
    assert first['counts']['known_success'] == 18 and first['counts'] == again['counts']
    assert len({row['call_id'] for row in first['calls']}) == 16
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['accepted.json', 'row.json', 'terminal.json'])
def test_published_metadata_mismatch_is_hard_error_not_optional_unknown(wrapper, name):
    result = wrapper.run('initial', 'success', '--new-plan')
    path = wrapper.directory(result) / name
    value = json.loads(path.read_text())
    value['tamper'] = True
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
@pytest.mark.parametrize('name,value', [('accepted.json', []), ('process.json', []),
    ('accepted.json', {'parent': []}), ('accepted.json', {'parent': {}}),
    ('accepted.json', {'parent': {'candidate_id': []}})])
def test_tool_malformed_live_metadata_is_bounded_integrity_error(wrapper, name, value):
    result = wrapper.run('public-candidate', 'success', '--new-plan')
    directory = wrapper.directory(result)
    (directory / 'completed.json').unlink()
    path = directory / name
    if isinstance(value, dict):
        value = dict(json.loads(path.read_text()), **value)
    path.write_text(json.dumps(value))
    before = {p: p.read_bytes() for p in directory.iterdir() if p.is_file()}
    response = wrapper.cli('lineage-status', expected=3)
    assert response['status'] == 'integrity_error' and str(directory) not in json.dumps(response)
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.process_e2e
@pytest.mark.parametrize('name', ['accepted.json', 'completed.json', 'process.json'])
def test_tool_torn_utf8_live_metadata_is_unresolved_or_unknown(wrapper, name):
    result = wrapper.run('public-candidate', 'success', '--new-plan')
    directory = wrapper.directory(result)
    if name != 'completed.json':
        (directory / 'completed.json').unlink()
    path = directory / name
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
@pytest.mark.parametrize('intent', [[], None, 1, 'invalid'])
@pytest.mark.parametrize('name', ['native-intent.json', 'native-outcome.json'])
@pytest.mark.parametrize('node_budget', [False, True])
def test_host_status_malformed_live_original_reports_bounded_integrity_error(tmp_path, intent, name, node_budget):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'goal'
    objective.write_text('Original public goal')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=1000, max_seconds=60,
        max_steps=4, model='fixture', reasoning_effort='high', search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=500, max_seconds=30) for role in ('H', 'W', 'A', 'E')}
            if node_budget else None,
        history_observer=dict(adapter=cov_lineage.__file__, contract_version=2,
            config=dict(runs='data/runs', calls='project/nodes', evaluation_key='public-fixture-v1')))
    job = c.build_job(root, 1, 'H', [])
    record = c.host_record(c.workspace(root, 1))
    c.write(record / 'native-intent.json', dict(task_sha256=c.digest(root / 'jobs/001.json')))
    c.write(record / name, intent)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    if name == 'native-intent.json':
        with pytest.raises(history.ObserverIntegrityError):
            feedback.live(root, job)
    env = dict(os.environ, PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''))
    reply = subprocess.run([sys.executable, '-m', 'scidiscovery.research_loop.cli', 'status', '--root', str(root)],
        env=env, capture_output=True, text=True, timeout=10)
    assert reply.returncode == 1 and not reply.stderr
    result = json.loads(reply.stdout)
    assert result['status'] == 'error' and 'original' in result['error']
    assert str(root) not in reply.stdout
    assert before == {p: p.read_bytes() for p in before}


def test_host_status_observes_unsealed_w_through_frozen_adapter_without_writer_lock(tmp_path):
    from test_cov_lineage import BACKEND
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'goal.md'
    objective.write_text('Use only public observed experiment results')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=10_000, max_seconds=3600,
        max_steps=8, model='fixture', reasoning_effort='high', search_policy=None,
        history_observer=dict(adapter=cov_lineage.__file__, contract_version=2,
            config=dict(runs='data/runs', calls='project/nodes', evaluation_key='public-fixture-v1')))
    finish(root, 1, 'H', [], action='experiment')
    job = c.build_job(root, 2, 'W', [1], instruction='Exact scientific question')
    record = c.host_record(c.workspace(root, 2))
    c.write(record / 'native-intent.json', dict(task_sha256=c.digest(root / 'jobs/002.json')))
    backend = tmp_path / 'backend.py'
    backend.write_text(BACKEND)
    config_path = tmp_path / 'configured.json'
    cov_lineage.configure(root, 2, [sys.executable, str(backend), '--runs-root', str(root / 'data/runs')], root / 'data/runs', config_path,receipt_channel='backend_file')
    config, config_sha = cov_lineage._config(config_path)
    script = tmp_path / 'candidate.json'
    script.write_text(json.dumps(dict(mode='success')))
    cov_lineage.run(config, config_sha, script, new=True)
    assert not (record / 'result-identity.json').exists()
    hint(c.workspace(root, 2))
    import fcntl
    with (root / 'host-run.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        first = cli.status(root)
        second = cli.status(root)
    live = first['nodes'][1]['live_feedback']
    assert 'live_feedback' not in first['nodes'][0]  # Sealed nodes keep their existing snapshot; no historical live rescan.
    assert live['status'] == 'observed' and live['counts']['known_success'] == 1
    assert live['calls'][0]['candidate_id'] == 'run-1' and live['calls'][0]['metric'] == .5
    assert second['nodes'][1]['live_feedback']['counts'] == live['counts']
    assert live['budget']['inflight_usage_unknown'] is True
    encoded = json.dumps(live)
    assert str(root) not in encoded and 'job_sha256' not in encoded and 'proofs' not in encoded
    assert not (record / 'result-identity.json').exists() and not (root / 'host-history/observations/002.json').exists()


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
    job=c.build_job(root,1,'H',[]);record=c.host_record(c.workspace(root,1))
    c.write(record/'native-intent.json',dict(task_sha256=c.digest(root/'jobs/001.json')))
    path=tmp_path/'local-config.json'
    experiments.configure(root,1,'local_execution:command',dict(run_argv=[sys.executable],max_seconds=10),path)
    source=tmp_path/'ordinary.py';source.write_text('print("public output without domain metric")\n')
    env=dict(os.environ,PYTHONPATH=str(Path(cov_lineage.__file__).parents[1])+os.pathsep+os.environ.get('PYTHONPATH',''))
    reply=subprocess.run([sys.executable,'-m','scidiscovery.research_loop.experiments','--config',str(path),
        'run','--script',str(source)],env=env,capture_output=True,text=True,timeout=20)
    assert reply.returncode==0,reply.stderr
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    value=cli.status(root)['nodes'][0]['live_feedback']
    assert value['status']=='observed' and value['counts']['known_success']==1
    assert value['calls'][0]['candidate_id'] is value['calls'][0]['metric'] is None
    assert 'history_observer' not in c.settings(root)
    assert before=={p:p.read_bytes() for p in before}
