"""Registered artifact candidates through W seals and frozen A navigation."""
import copy
import json

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from test_context_selection import bound_input_path
from test_history_handoff import finish
from test_nonlocal_execution_observation import case, observe_calls, research_path
from test_worker_input_repair import _receiver
from scidiscovery.research_loop import accepted_prefix, contracts as c, execution, experiment_client, experiments as e
from scidiscovery.research_loop import local_experiments, worker_execution_bridge as bridge


pytestmark = pytest.mark.process_e2e


def _local_case(tmp_path, monkeypatch):
    root, job, config, _ = case(tmp_path, monkeypatch, executor='local_execution:command')
    launched = []

    def launch(body, accepted, command, cwd):
        # Trusted host fixture: run the exact export and write the exact call's
        # output holder. This does not exercise native namespace isolation.
        assert body == config
        assert e.Records(config).accepted(accepted['call_id']) == accepted
        launched.append(accepted['call_id'])
        command = list(command)
        command[accepted['script_index']] = accepted['script']['path']
        command.append(str(local_experiments.artifact_directory(config, accepted['call_id'])))
        return command, cwd, None

    return root, job, config, _receiver(root, config, monkeypatch, launch=launch), launched


def _run_candidates(root, server, *, count, unknown_metric=False, mechanism='1'):
    source = c.workspace(root, 2) / 'code/candidates/run.py'
    source.parent.mkdir(parents=True)
    source.write_text('import json,pathlib,sys\n'
        'output=pathlib.Path(sys.argv[1])\n'
        'metrics=[];outputs=[];candidates=[]\n'
        f'for number in range({count}):\n'
        ' name=f"candidate-{number:02d}.txt"\n'
        ' (output/name).write_text(f"original-{number:02d}\\n")\n'
        ' outputs.append(dict(path="artifacts/"+name))\n'
        ' metrics.append(dict(name=f"metric-{number}",value=number,evaluation_key="fixture-v1",direction="max"))\n'
        f' metric="unmeasured" if {unknown_metric!r} and number=={count - 1} else f"metric-{{number}}"\n'
        ' candidates.append(dict(label=f"retained-{number:02d}",artifact="artifacts/"+name,metric=metric))\n'
        '(output/"result.json").write_text(json.dumps(dict(version=1,metrics=metrics,outputs=outputs,candidates=candidates)))\n')
    reply = bridge.client(server.directory, ['run', '--experiment-dir', 'experiments/candidates',
        '--script', 'code/candidates/run.py', '--new-plan', '--research', research_path(root,mechanism)])
    assert reply['code'] == 0 and reply['result']['status'] == 'known_success', _reply_debug(server, reply)
    return reply['result']['call_id']


def _reply_debug(server, reply):
    """Include the exact receiver diagnostic, bounded to one small prefix."""
    diagnostic = None
    if server.diagnostic.is_file():
        with server.diagnostic.open('rb') as stream:
            diagnostic = stream.read(4096).decode('utf-8', errors='replace')
    return dict(reply=reply, host_diagnostic=diagnostic)


def _seal_worker(root):
    # The fixture reports transport facts only, with no inferred scientific finding.
    finish(root, 2, 'W', [1], preserve_native_intent=True)
    record = execution.record(root, 2)
    seal = c.read(record / 'result-identity.json')
    return c.read(root / seal['observations']['path'])


def _snapshot(root, job, facts, config):
    # The explicit frozen input inventory replaces any recursive file walk.
    paths = {root / 'jobs/002.json', root / 'jobs/003.json',
        execution.record(root, 2) / 'native-intent.json',
        execution.record(root, 2) / 'result-identity.json',
        execution.record(root, 2) / 'experiment-config.json',
        root / c.read(execution.record(root, 2) / 'result-identity.json')['observations']['path']}
    paths.update(bound_input_path(root, job, row['path']) for row in experiment_client._input_rows(job))
    for call in facts['managed_calls']:
        directory = e.Records(config).directory(call['call_id'])
        accepted = e.Records(config).accepted(call['call_id'])
        assert call['scope'] == accepted['scope'] == config['scope']
        assert call['mechanism_binding'] == accepted['mechanism_binding']
        assert accepted['scope']['job_ref'] == c.record_ref(root / 'jobs/002.json')
        paths.update(directory / name for name in ('accepted.json', 'completed.json', 'submission.json'))
    return {path: path.read_bytes() for path in paths}


def _candidates(root, job):
    """Follow the current frozen node navigation to each exact retained entity."""
    prefix = job['history']['prefix']
    navigation = accepted_prefix.navigation(root, prefix, 2)
    entries = [row for row in navigation['entities'] if row['kind'] == 'candidate']
    rows = []
    for entry in entries:
        path = accepted_prefix.visibility(root, prefix) / entry['page'].removeprefix('inputs/history/')
        assert path.is_file()
        rows.append(accepted_prefix.candidate(root, prefix, entry['reference']))
    return entries, rows


def test_registered_candidates_keep_late_original_in_frozen_navigation(tmp_path, monkeypatch, execution_registry):
    root, _, config, server, launched = _local_case(tmp_path, monkeypatch)
    call_id = _run_candidates(root, server, count=17)
    facts = _seal_worker(root)
    assert launched == [call_id]
    assert [row['call_id'] for row in facts['managed_calls']] == [call_id]
    assert len(facts['candidates']) == 17
    job = c.build_job(root, 3, 'A', [1, 2])
    original_job = copy.deepcopy(job)
    before = _snapshot(root, job, facts, config)
    entries, rows = _candidates(root, job)
    assert len(entries) == len(rows) == 17
    assert len({row['reference'] for row in entries}) == 17
    assert [row['observation']['id'] for row in rows] == [row['id'] for row in facts['candidates']]
    assert [row['observation']['label'] for row in rows] == [f'retained-{number:02d}' for number in range(17)]
    assert [row['observation']['metric'] for row in rows] == list(range(17))
    for number in range(17):
        logical = f'inputs/parents/002/artifacts/experiments/{call_id}/artifacts/candidate-{number:02d}.txt'
        output = next(row for row in facts['managed_calls'][0]['outputs'] if row['name'] == f'candidate-{number:02d}.txt')
        original = bound_input_path(root, job, logical)
        assert original.stat().st_size == output['size_bytes']
        assert original.read_bytes() == f'original-{number:02d}\n'.encode()
    assert job == original_job
    assert all(path.read_bytes() == data for path, data in before.items())


def test_registered_unknown_measurement_and_failed_unknown_execution_keep_distinct_facts(tmp_path, monkeypatch, remote_registry, capsys):
    local = tmp_path / 'local'; local.mkdir()
    root, _, config, server, launched = _local_case(local, monkeypatch)
    success = _run_candidates(root, server, count=2, unknown_metric=True)
    failed_source = c.workspace(root, 2) / 'code/failure/run.py'
    failed_source.parent.mkdir(parents=True)
    failed_source.write_text('raise SystemExit(7)\n')
    reply = bridge.client(server.directory, ['run', '--experiment-dir', 'experiments/failure',
        '--script', 'code/failure/run.py', '--new-plan', '--research', research_path(root, '2')])
    assert reply['code'] == 1 and reply['result']['status'] == 'known_failed', _reply_debug(server, reply)
    failed = reply['result']['call_id']
    facts = _seal_worker(root)
    assert launched == [success, failed]
    assert [row['status'] for row in facts['managed_calls']] == ['known_success', 'known_failed']
    assert facts['managed_calls'][1]['execution']['returncode'] == 7
    assert len(facts['candidates']) == 2
    assert facts['candidate_diagnostics'] == [dict(call_id=success, code='declared_candidate_metric_unknown')]
    job = c.build_job(root, 3, 'A', [1, 2])
    before = _snapshot(root, job, facts, config)
    _, rows = _candidates(root, job)
    candidates = [row['observation'] for row in rows]
    assert [row['status'] for row in candidates] == ['ok', 'ok']
    assert candidates[0]['metric'] == 0
    assert all(candidates[1][key] is None for key in ('metric', 'evaluation_key', 'direction'))
    original = f'inputs/parents/002/artifacts/experiments/{success}/artifacts/candidate-01.txt'
    assert bound_input_path(root, job, original).read_bytes() == b'original-01\n'
    assert all(path.read_bytes() == data for path, data in before.items())

    remote = tmp_path / 'remote'; remote.mkdir()
    root, job, config, _ = case(remote, monkeypatch, outcome='unknown')
    source = c.workspace(root, 2) / 'code/source.json'; source.parent.mkdir(exist_ok=True)
    source.write_bytes(b'{"message":"remote-original"}')
    submissions = []
    submit = DifferentExternalIdentity.submit
    def submitted(adapter, descriptor):
        submissions.append(descriptor.name)
        return submit(adapter, descriptor)
    monkeypatch.setattr(DifferentExternalIdentity, 'submit', submitted)
    assert e.main(['--config', str(c.workspace(root, 2) / 'work/experiment-config.json'),
        'run', '--payload', str(source), '--new-plan', '--research', research_path(root, '1')]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unknown', result
    unknown = result['call_id']
    accepted = e.Records(config).accepted(unknown)
    assert 'result' not in c.read(e.Records(config).directory(unknown) / 'completed.json')
    facts = observe_calls(root, job, config)
    assert [row['call_id'] for row in facts] == [unknown]
    assert facts[0]['status'] == 'unknown' and 'execution' not in facts[0]
    directory = e.Records(config).directory(unknown)
    before = {directory/name: (directory/name).read_bytes()
        for name in ('accepted.json', 'completed.json', 'submission.json')}
    assert e.main(['--config', str(c.workspace(root, 2) / 'work/experiment-config.json'),
        'run', '--payload', str(source), '--new-plan', '--research', research_path(root, '2')]) == 2
    repeated = json.loads(capsys.readouterr().out)
    assert repeated['status'] == 'unknown' and repeated['call_id'] == unknown
    assert len(e.Records(config).calls()) == 1 and submissions == [unknown]
    assert e.Records(config).accepted(unknown) == accepted
    assert all(path.read_bytes() == data for path, data in before.items())
