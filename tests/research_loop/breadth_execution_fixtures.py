"""Real Local transport for breadth control fixtures; no model or solver runs."""
from functools import lru_cache
from pathlib import Path

from scidiscovery.research_loop import breadth, contracts as c, execution, experiment_client
from scidiscovery.research_loop import experiments, native_execution, worker_execution_bridge as bridge
from test_default_native_local_execution import actual_native


def native_launcher():
    """Use the installed sandbox implementation without copying its binary."""
    return [str(actual_native())]


@lru_cache(maxsize=None)
def _launch(root, step, attempt):
    """Retain the one immutable permission capture for this exact attempt."""
    work = c.workspace(root, step)
    record = execution.record(root, step, attempt)
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    settings = c.settings(root, job)
    home = record / 'breadth-fixture-native-home'
    home.mkdir(mode=0o700)
    args = [*settings['native_launcher_argv'], 'exec', '--sandbox', 'workspace-write', '-C', str(work), '-']
    _, launch = native_execution.prepare(root, step, record, work, home, args,
        standard=True, native_options=settings['native_options'])
    return launch


def run(root, config, source, mechanism, runtime):
    """Submit through the real host owner and return its retained test originals."""
    root = Path(root).absolute()
    step = config['scope']['step']
    attempt = config['native_attempt']['number']
    work = c.workspace(root, step)
    path = work / 'work/experiment-config.json'
    assert experiment_client.resolve(path, c.read(path)) == config
    launch = _launch(root, step, attempt)
    # Fixture callers retain canonical identities; the public CLI accepts the
    # ordinal frozen in this exact job's breadth, never a later live catalog.
    choice = next((str(row['ordinal']) for row in (config.get('breadth') or {}).get('mechanisms', [])
        if row['mechanism_ref'] == mechanism), mechanism)
    arguments = ['run', '--script', str(source), '--new-plan', '--mechanism', choice,
        '--experiment-dir', str(runtime)]
    with bridge.serve(work / bridge.SUBMISSION_DIRECTORY, path, work, launch=launch) as endpoint:
        reply = bridge.client(endpoint, arguments)
    result = reply['result']
    # This is the existing CLI's explicit pre-admission opportunity refusal.
    # Preserve the older test helper's exception API after exercising that path.
    if reply['code'] == 2 and result.get('status') == 'unresolved' and result.get('accepted') is False:
        raise breadth.OpportunityRequired(result['note'])
    assert reply['code'] in {0, 1}, reply
    accepted = experiments.Records(config).accepted(result['call_id'])
    # Host-only assertion data never enters the scientific submission/reply.
    return dict(accepted, **result)
