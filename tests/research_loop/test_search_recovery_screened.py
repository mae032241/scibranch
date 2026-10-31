"""Recovered screen findings retain their exact attempt across A and forks."""
import copy
import json
import os
from pathlib import Path
import traceback

import pytest

from scidiscovery.research_loop import (
    contracts as c, controller, execution, experiment_client, experiments, fork_service,
    history, originals, search, semantic, worker_delivery,
)
from execution_fixtures import execution_registry
from test_default_cli_output_repair import cli, nomination_diagnostic, prepare_cli


pytestmark = pytest.mark.process_e2e


def fork_a_diagnostic(root, result):
    """Retain bounded A003 transport and its first three failing declared inputs."""
    observed = {}
    record = root / 'native-evidence/003'
    for name in ('native-outcome.json', 'native.stderr', 'native.jsonl', 'runner.stderr'):
        path = record / name
        if not path.is_file():
            observed[name] = 'missing'
            continue
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 8192))
            observed[name] = stream.read(8192).decode('utf-8', errors='replace')
    inputs = dict(failures=[])
    try:
        job = c.read(root / 'jobs/003.json')
        rows = list(experiment_client._input_rows(job))
        inputs['declared_count'] = len(rows)
        manifest = originals._manifest(root)
        work = c.workspace(root, 3)
        for item in rows:
            logical = originals.relative(item['path'])
            bound = dict(item, source=item.get('source', str(work / logical)))
            try:
                originals.material_path(root, bound)
            except (OSError, ValueError, KeyError) as error:
                if len(inputs['failures']) >= 3:
                    continue
                failure = dict(item={key: bound[key] for key in ('path', 'source', 'origin_identity')
                    if key in bound}, exception=dict(type=type(error).__name__, message=str(error)),
                    origins=[])
                inputs['failures'].append(failure)
                actual = Path(bound['source'])
                parts = Path(bound['path']).parts
                owner_step = (int(parts[2]) if len(parts) >= 3 and parts[:2] == ('inputs', 'parents')
                    and parts[2].isdecimal() else 1)
                for origin in (manifest or {}).get('origins', {}).values():
                    if not actual.is_relative_to(origin['original_root']):
                        continue
                    relative = actual.relative_to(origin['original_root']).as_posix()
                    owner = originals.for_origin(root, origin['identity'], step=owner_step)
                    matches = owner._index.get((originals.identity_key(owner.origin_identity), relative), ())
                    failure['origins'].append(dict(original_root=owner.original_root,
                        origin_identity=owner.origin_identity, step=owner.step, key_path=relative,
                        matches=[{key: entry[key] for key in ('path', 'storage_path', 'visibility')
                            if key in entry} for entry in matches[:2]]))
    except (OSError, ValueError, KeyError) as error:
        inputs['diagnostic_error'] = dict(type=type(error).__name__, message=str(error))
    stages = {}
    job_path = root / 'jobs/003.json'
    raw_path = record / 'science-output.json'
    if job_path.is_file() and raw_path.is_file():
        job, raw = c.read(job_path), c.read(raw_path)
        failed_paths = []
        original_regular = c.regular
        def traced_regular(path, **options):
            try:
                return original_regular(path, **options)
            except (OSError, ValueError):
                if len(failed_paths) < 8:
                    failed_paths.append(str(path))
                raise
        c.regular = traced_regular
        try:
            for name, operation in (
                    ('validate_inputs', lambda: c._validate_inputs(root, c.workspace(root, 3), job)),
                    ('semantic_outcome', lambda: semantic.outcome(root, job, raw))):
                failed_paths.clear()
                try:
                    operation()
                    stages[name] = dict(status='passed')
                except (OSError, ValueError, KeyError, TypeError) as error:
                    stages[name] = dict(status='failed', type=type(error).__name__,
                        message=str(error), regular_failures=list(failed_paths),
                        traceback=traceback.format_exc(limit=12))
        finally:
            c.regular = original_regular
    rendered = json.dumps(dict(result=result, node=3, transport=observed, inputs=inputs,
        synchronous_stages=stages),
        ensure_ascii=False, indent=2)
    rendered = rendered.encode('utf-8')[:64 * 1024].decode('utf-8', errors='ignore')
    if os.environ.get('SCID_TEST_LOG_DIR'):
        destination = Path(os.environ['SCID_TEST_LOG_DIR'])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'fork-screened-node003.txt').write_text(rendered)
    return rendered


@pytest.mark.parametrize('fault', ['nomination-missing', 'completed-findings'])
def test_recovered_w_can_be_scientifically_screened(tmp_path, execution_registry, fault):
    root, _, env = prepare_cli(tmp_path, fault=fault, a_screened=True)
    result = cli(root, 'run', env=env)
    assert result['status'] == 'finish', nomination_diagnostic(root, result, fault=fault)
    origin, current = execution.records(root, 2)
    assert current == origin / 'continuations/001'
    intent = c.read(current / 'native-intent.json')
    if fault == 'nomination-missing':
        assert intent['output_repair']['code'] == 'candidate_nominations_incomplete'
    else:
        assert intent['automatic_recovery']['reason'] == 'worker_delivery_incomplete'
    job = c.read(root / 'jobs/002.json')
    seal = c.read(origin / 'result-identity.json')
    facts = worker_delivery.validate(root, job, current)
    assert facts['binding']['source_attempt'] == 1
    assert seal['control_outcome']['worker_delivery']['facts_ref'] == c.record_ref(
        current / worker_delivery.FACT_NAME)
    assert c.validate(c.workspace(root, 3))['search_review']['dispositions'][0]['status'] == 'screened'
    state = search.project(root, 4)
    assert state['mechanisms']['h001.m1']['status'] == 'screened'
    assert search._screen_refs(root, job, seal, state) == {'h001.m1'}
    locator = c.read(c.workspace(root, 2) / 'work/retained-candidate-locator.json')
    accepted = c.read(experiment_client.record_directory(root, 2) / 'calls' /
        locator['call_id'] / 'accepted.json')
    assert accepted['native_attempt']['number'] == 0
    assert (c.workspace(root, 2) / 'work/launches').read_text() == 'exec\nresume\n'

    wrong = copy.deepcopy(seal)
    wrong['control_outcome']['worker_delivery']['facts_ref'] = c.record_ref(
        origin / worker_delivery.FACT_NAME)
    with pytest.raises(history.ObserverIntegrityError, match='differs from its seal'):
        search._screen_refs(root, job, wrong, state)


def test_fork_a_screens_exact_inherited_recovered_w(tmp_path, execution_registry):
    root, _, env = prepare_cli(tmp_path, fault='completed-findings', a_screened=True)
    result = cli(root, 'run', env=env)
    assert result['status'] == 'finish', nomination_diagnostic(root, result, fault='completed-findings')
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=3)
    preview = fork_service.preview(root, 3, child, budget=limits)
    created = fork_service.create(root, 3, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='screen-recovered-w')
    assert created['state'] == 'paused'
    result = cli(child, 'continue', env=env)
    assert result['status'] == 'finish', fork_a_diagnostic(child, result)
    assert not (child / 'jobs/002.json').exists()
    assert c.validate(c.workspace(child, 3))['search_review']['dispositions'][0]['status'] == 'screened'
    state = search.project(child, 4)
    assert state['mechanisms']['h001.m1']['status'] == 'screened'
    source, _, seal, _ = history._source(child, 2)
    owner = originals.node(child, 2)
    assert owner.inherited and owner.native_record == 'native-evidence/002/continuations/001'
    facts_path = originals.source_record(owner, worker_delivery.FACT_NAME)
    facts = c.read(facts_path)
    assert set(facts['binding']['raw_ref']) == {'path', 'size_bytes'}
    assert set(seal['raw_ref']) == {'path', 'size_bytes', 'file_stat'}
    assert search._screen_refs(child, source, seal, state) == {'h001.m1'}

    wrong = copy.deepcopy(seal)
    wrong['control_outcome']['worker_delivery']['facts_ref'] = dict(
        path=str(root / 'native-evidence/002/work-evidence.json'))
    with pytest.raises(history.ObserverIntegrityError, match='differs from its seal'):
        search._screen_refs(child, source, wrong, state)

    # A valid proof of the origin attempt cannot replace the selected final one.
    wrong = copy.deepcopy(seal)
    wrong['raw_ref'] = experiments.proof(execution.origin(root, 2) / 'science-output.json')
    with pytest.raises((ValueError, OSError)):
        search._screen_refs(child, source, wrong, state)

    # Reading saved eligibility trusts the seal. Actual consumption by the
    # current A must still reject a replaced direct-input original.
    raw = originals.source_raw(owner)
    original = raw.read_bytes()
    replacement = raw.with_name('same-bytes-replacement.json')
    replacement.write_bytes(original)
    replacement.chmod(0o400)
    replacement.replace(raw)
    assert search._screen_refs(child, source, seal, state) == {'h001.m1'}
    with pytest.raises(ValueError, match='identity changed'):
        c._validate_inputs(child, c.workspace(child, 3), c.read(child / 'jobs/003.json'))
