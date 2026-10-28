"""Current same-session attempts persist their proven delta once."""
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, continuation, native, execution


BASELINE = dict(input_tokens=2017365, cached_input_tokens=1926656, output_tokens=22940)
TERMINAL = dict(input_tokens=2385841, cached_input_tokens=2180096, output_tokens=24982)
DELTA = dict(input_tokens=368476, cached_input_tokens=253440, output_tokens=2042)


@pytest.mark.parametrize('terminal_scope', ['session', 'turn'])
@pytest.mark.process_e2e
def test_real_resume_transport_counts_attempt_delta_and_retains_raw_terminal(tmp_path, terminal_scope):
    from test_worker_resume import FAKE, instance, fail, wait_runner
    counter = lambda value: json.dumps(dict(type='event_msg', payload=dict(type='token_count',
        info=dict(total_token_usage=value)))) + '\n'
    fake = FAKE.replace('rollout.jsonl', 'rollout-' + '11111111-2222-4333-8444-555555555555' + '.jsonl')
    fake = fake.replace("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'cached_input_tokens':3,'output_tokens':4}}),flush=True)",
        "with p.open('a') as stream:stream.write(" + repr(counter(BASELINE)) + ")\n "
        + "print(json.dumps({'type':'turn.completed','usage':" + repr(BASELINE) + "}),flush=True)")
    terminal = TERMINAL if terminal_scope == 'session' else DELTA
    fake = fake.replace("print(json.dumps({'type':'turn.completed','usage':{'input_tokens':20,'cached_input_tokens':5,'output_tokens':2}}),flush=True)",
        "with (home/'sessions/rollout-11111111-2222-4333-8444-555555555555.jsonl').open('a') as stream:stream.write(" + repr(counter(TERMINAL)) + ")\n"
        + "print(json.dumps({'type':'turn.completed','usage':" + repr(terminal) + "}),flush=True)")
    root = instance(tmp_path, fake=fake, cap=5000000, max_tokens=5000000)
    first = fail(root)
    assert c.read(first / 'usage.json') == BASELINE
    originals = {path: path.read_bytes() for path in first.iterdir() if path.is_file()}
    # The controller entry also schedules a later FINAL A. Exercise the exact
    # native continuation entry here, including admission, process and seal.
    result = continuation.start(root, 1)
    assert result['role'] == 'H'
    current = wait_runner(root)
    assert c.read(current / 'usage.json') == DELTA
    observation = c.read(current / 'native-outcome.json')['metering']
    assert observation['coverage'] == 'terminal' and observation['reconciliation'] == 'matched'
    assert observation['live_tokens'] == DELTA
    raw = [json.loads(line) for line in (current / 'native.jsonl').read_text().splitlines()]
    assert next(event['usage'] for event in raw if event['type'] == 'turn.completed') == terminal
    assert native.usage(root)['observed_tokens'] == 2410823
    assert budget._attempt_consumption(root, c.read(root / 'jobs/001.json'), execution.records(root, 1))[0] == 2410823
    assert (c.workspace(root, 1) / 'work/launches').read_text() == 'exec\nresume\n'
    assert not (root / 'jobs/002.json').exists()
    assert budget.used_steps(root) == 2
    assert all(path.read_bytes() == data for path, data in originals.items())
