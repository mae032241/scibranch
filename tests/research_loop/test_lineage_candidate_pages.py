"""Registered artifact candidates through W seals and frozen A navigation."""
import copy
import json

import pytest

from execution_fixtures import DifferentExternalIdentity, execution_registry, remote_registry
from test_context_selection import bound_input_path
from test_history_handoff import finish
from test_nonlocal_execution_observation import case
from test_worker_input_repair import _receiver
from scidiscovery.research_loop import contracts as c, execution, experiment_client, experiments as e
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


def _run_candidates(root, server, *, count, unknown_metric=False, mechanism='P'):
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
        '--script', 'code/candidates/run.py', '--new-plan', '--mechanism', mechanism])
    assert reply['code'] == 0 and reply['result']['status'] == 'known_success', _reply_debug(server, reply)
    return reply['result']['call_id']


def _reply_debug(server, reply):
    """Include the exact receiver diagnostic, bounded to one small prefix."""
    diagnostic = None
    if server.diagnostic.is_file():
        with server.diagnostic.open('rb') as stream:
            diagnostic = stream.read(4096).decode('utf-8', errors='replace')
    return dict(reply=reply, host_diagnostic=diagnostic)


def _seal_worker(root, *, observed=('P',)):
    finish(root, 2, 'W', [1], preserve_native_intent=True,
        mechanism_findings=[dict(mechanism=label, basis='observed' if label in observed else 'blocked',
            finding='Inert contract fixture; missing scientific qualification remains explicit.') for label in ('P', 'Q')])
    record = execution.record(root, 2)
    seal = c.read(record / 'result-identity.json')
    return c.read(root / seal['observations']['path'])


def _snapshot(root, job, facts, config):
    # The explicit frozen input inventory replaces any recursive file walk.
    paths = {root / 'jobs/002.json', root / 'jobs/003.json',
        execution.record(root, 2) / 'native-intent.json',
        execution.record(root, 2) / 'result-identity.json',
        execution.record(root, 2) / 'experiment-config.json',
        root / 'host-history/observations/002.json'}
    paths.update(bound_input_path(root, job, row['path']) for row in experiment_client._input_rows(job))
    for call in facts['managed_calls']:
        directory = e.Records(config).directory(call['call_id'])
        accepted = e.Records(config).accepted(call['call_id'])
        assert call['scope'] == accepted['scope'] == config['scope']
        assert call['mechanism_binding'] == accepted['mechanism_binding']
        assert accepted['scope']['job_ref'] == c.record_ref(root / 'jobs/002.json')
        paths.update(directory / name for name in ('accepted.json', 'completed.json', 'submission.json'))
    return {path: path.read_bytes() for path in paths}


def _pages(root, job, name):
    path = job['history']['view']['indexes'].get(name)
    pages = []
    seen = set()
    while path:
        assert path not in seen, 'Frozen continuation must not cycle'
        seen.add(path)
        original = next(row for row in job['history']['files'] if row['path'] == path)
        material = bound_input_path(root, job, path)
        assert material.stat().st_size == original['size_bytes']
        value = c.read(material)
        pages.append((path, value))
        path = value['next_page']
    rows = [row for _, value in pages for row in value[name]]
    remaining = len(rows)
    for _, value in pages:
        remaining -= len(value[name])
        assert value['omitted_count'] == remaining
    return pages, rows


def _candidate_count(view):
    candidates = view['lineage']['candidates']
    return candidates.get('candidate_count', candidates.get('count'))


def test_registered_candidates_cross_real_frozen_pages_without_losing_late_original(tmp_path, monkeypatch, execution_registry):
    root, _, config, server, launched = _local_case(tmp_path, monkeypatch)
    # Production candidate pages hold 16 rows; one additional tiny artifact
    # crosses that real boundary without changing packing or byte limits.
    call_id = _run_candidates(root, server, count=17)
    facts = _seal_worker(root)
    assert launched == [call_id]
    assert [row['call_id'] for row in facts['managed_calls']] == [call_id]
    assert len(facts['candidates']) == 17
    job = c.build_job(root, 3, 'A', [1, 2])
    original_job = copy.deepcopy(job)
    before = _snapshot(root, job, facts, config)
    view = c.science_view(job, root)
    pages, rows = _pages(root, job, 'candidates')
    assert len(pages) == 2
    assert [len(page['candidates']) for _, page in pages] == [16, 1]
    assert pages[0][1]['next_page'] == pages[1][0]
    assert pages[1][1]['next_page'] is None
    expected = [job['history']['candidate_references'][row['id']] for row in facts['candidates']]
    assert [row['id'] for row in rows] == expected
    assert len(set(expected)) == 17
    assert [row['label'] for row in rows] == [f'retained-{number:02d}' for number in range(17)]
    assert [row['metric'] for row in rows] == list(range(17))
    assert all(row['status'] == 'ok' and row['observation_step'] == 2 for row in rows)
    assert _candidate_count(view) == 17
    preview = view['lineage']['candidates']
    assert len(preview['candidate_facts']) + preview['candidate_facts_omitted_count'] == 17
    assert expected[-1] not in {row['id'] for row in preview['candidate_facts']}
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
        '--script', 'code/failure/run.py', '--new-plan', '--mechanism', 'Q'])
    assert reply['code'] == 1 and reply['result']['status'] == 'known_failed', _reply_debug(server, reply)
    failed = reply['result']['call_id']
    facts = _seal_worker(root, observed=('P', 'Q'))
    assert launched == [success, failed]
    assert [row['status'] for row in facts['managed_calls']] == ['known_success', 'known_failed']
    assert len(facts['candidates']) == 2
    assert facts['candidate_diagnostics'] == [dict(call_id=success, code='declared_candidate_metric_unknown')]
    job = c.build_job(root, 3, 'A', [1, 2])
    original_job = copy.deepcopy(job)
    before = _snapshot(root, job, facts, config)
    view = c.science_view(job, root)
    _, candidates = _pages(root, job, 'candidates')
    _, calls = _pages(root, job, 'experiments')
    assert [row['id'] for row in candidates] == [job['history']['candidate_references'][row['id']] for row in facts['candidates']]
    assert [row['call_id'] for row in calls] == [success, failed]
    assert [row['execution_status'] for row in calls] == ['known_success', 'known_failed']
    assert calls[1]['returncode'] == 7
    # Candidate status describes retained registration. An unresolved declared
    # measurement stays null; a failed execution creates no candidate.
    assert [row['status'] for row in candidates] == ['ok', 'ok']
    assert candidates[0]['metric'] == 0
    assert all(candidates[1][key] is None for key in ('metric', 'evaluation_key', 'direction'))
    assert _candidate_count(view) == 2
    preview = view['lineage']['candidates']
    assert len(preview['candidate_facts']) + preview['candidate_facts_omitted_count'] == 2
    for field in ('failed_candidate_count', 'unknown_candidate_count'):
        if field in preview:
            assert preview[field] == 0
    original = f'inputs/parents/002/artifacts/experiments/{success}/artifacts/candidate-01.txt'
    assert bound_input_path(root, job, original).read_bytes() == b'original-01\n'
    assert job == original_job and all(path.read_bytes() == data for path, data in before.items())

    # Unknown is the final actual submission in a separate remote binding.
    # Its real provider seam supplies the unresolved original; no receipt is edited.
    remote = tmp_path / 'remote'; remote.mkdir()
    root, _, config, _ = case(remote, monkeypatch, outcome='unknown')
    source = c.workspace(root, 2) / 'code/source.json'; source.parent.mkdir()
    source.write_bytes(b'{"message":"remote-original"}')
    submissions = []
    submit = DifferentExternalIdentity.submit
    def submitted(adapter, descriptor):
        submissions.append(descriptor.name)
        return submit(adapter, descriptor)
    monkeypatch.setattr(DifferentExternalIdentity, 'submit', submitted)
    assert e.main(['--config', str(c.workspace(root, 2) / 'work/experiment-config.json'),
        'run', '--payload', str(source), '--new-plan', '--mechanism', 'P']) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'unknown', result
    unknown = result['call_id']
    accepted = e.Records(config).accepted(unknown)
    assert 'result' not in c.read(e.Records(config).directory(unknown) / 'completed.json')
    facts = _seal_worker(root, observed=())
    assert [row['call_id'] for row in facts['managed_calls']] == [unknown]
    assert facts['managed_calls'][0]['status'] == 'unknown' and facts['candidates'] == []
    assert 'execution' not in facts['managed_calls'][0]
    job = c.build_job(root, 3, 'A', [1, 2])
    original_job = copy.deepcopy(job)
    before = _snapshot(root, job, facts, config)
    view = c.science_view(job, root)
    _, calls = _pages(root, job, 'experiments')
    assert _pages(root, job, 'candidates') == ([], [])
    assert _candidate_count(view) == 0
    assert len(calls) == 1 and calls[0]['call_id'] == unknown
    assert calls[0]['step'] == 2 and calls[0]['execution_status'] == 'unknown'
    assert 'outputs' not in calls[0] and 'result_status' not in calls[0]
    assert len(e.Records(config).calls()) == 1
    assert submissions == [unknown]
    assert e.Records(config).accepted(unknown) == accepted
    assert job == original_job and all(path.read_bytes() == data for path, data in before.items())
