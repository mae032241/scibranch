"""Real native repair requests keep billing separate from prospective science."""
import copy
import json

import pytest

from scidiscovery.research_loop import budget, contracts as c, execution, native, originals, search
import test_default_cli_output_repair as transport
from test_soft_node_budget import start_clock


H_ONLY = r'''import json,os,pathlib,sys
args=sys.argv[1:];prompt=sys.stdin.read();resume='resume' in args
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE'])
home=pathlib.Path(os.environ['CODEX_HOME'])
assert os.environ['THIN_HWA_ROLE']=='H'
thread='11111111-2222-4333-8444-000000000001'
print(json.dumps(dict(type='thread.started',thread_id=thread)),flush=True)
if not resume:
 session=home/'sessions'/('rollout-'+thread+'.jsonl');session.parent.mkdir(parents=True,exist_ok=True)
 session.write_text(json.dumps(dict(type='session_meta',payload=dict(id=thread,cwd=str(work))))+'\n')
 payload=dict(summary='Prospective experiment plan',scientific_result='No experiment has been performed.',
  limitations=['The planned comparisons remain untested.'],implementation_selection=None,
  next_action='experiment',route_reason='A new bounded comparison can distinguish the hypotheses.',
  search_plan=dict(scope='Compare declared mechanisms',commitments=[dict(label='Mechanism',
   minimal_test='Compare two fresh controlled observations',existing_ref=None)],focus=dict(index=999,action='screen')),
  execution_request=dict(experiments=[dict(name='fresh-control',repeats=2)],observed_experiments=0))
 (work/'work/original-plan.json').write_text(json.dumps(payload))
else:
 assert thread in args
 payload=json.loads((work/'work/original-plan.json').read_text())
 payload['search_plan']['focus']['index']=1
 (work/'work/repair-request.txt').write_text(prompt)
 (work/'work/repair-budget.json').write_bytes((work/'work/budget-status.json').read_bytes())
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=10 if resume else 131729,
 cached_input_tokens=3,output_tokens=4))),flush=True)
'''


@pytest.mark.process_e2e
def test_h_field_repair_preserves_paid_usage_and_complete_prospective_w_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, 'NATIVE', H_ONLY)
    root, home, env = transport.prepare_cli(tmp_path, fault='incomplete_w', max_tokens=300000)
    for name in ('CODEX_HOME', 'PATH', 'PYTHONPATH'):
        monkeypatch.setenv(name, env[name])
    settings = (root/'settings.json').read_bytes()
    native_configuration = {path: path.read_bytes() for path in (home/'auth.json', home/'config.toml')}
    c.build_job(root, 1, 'H', [])
    start_clock(root, 'native field repair fixture')
    native.execute_native(root, 1)

    original, repaired = execution.records(root, 1)
    before = {path: path.read_bytes() for path in (
        original/'science-output.json', original/'native-intent.json', original/'native-outcome.json',
        original/'usage.json', repaired/'science-output.json', repaired/'native-intent.json',
        repaired/'native-outcome.json', repaired/'usage.json')}
    intent = c.read(repaired/'native-intent.json')
    assert 'focus' in intent['output_repair']['diagnostic']
    assert intent.get('work_repair') is None
    assert intent['budget_slice']['previous_tokens'] == 131733
    prompt = (c.workspace(root, 1)/'work/repair-request.txt').read_text()
    objects = [json.loads(line) for line in prompt.splitlines() if line.startswith('{')]
    remaining = next(value for value in objects if 'remaining_observed_tokens' in value)
    assert remaining['remaining_observed_tokens'] == 168267
    assert remaining['previous_consumed_observed_tokens'] == 131733
    assert 0 < remaining['remaining_active_seconds'] <= 60
    assert not {'observed_tokens', 'active_seconds', 'previous_observed_tokens'} & remaining.keys()
    shared = next(value for value in objects if 'exploration' in value)
    assert shared['exploration']['token_usage_unknown'] is True
    assert shared['shared_observed_tokens_remaining'] == 168267
    hint = c.read(c.workspace(root, 1)/'work/repair-budget.json')
    assert hint['observed_tokens_lower_bound'] == 131733
    assert hint['inflight_usage_unknown']
    assert native.usage(root)['observed_tokens'] == 131747

    expected = copy.deepcopy(c.read(original/'science-output.json'))
    expected['search_plan']['focus']['index'] = 1
    science = c.validate(c.workspace(root, 1))
    assert c.read(repaired/'science-output.json') == expected
    assert {key: science[key] for key in expected} == expected
    assert science['execution_request']['observed_experiments'] == 0
    worker = c.build_job(root, 2, 'W', [1])
    assert worker['direct_originals'] == [dict(path='inputs/parents/001/conclusion.json',
        size_bytes=(repaired/'science-output.json').stat().st_size)]
    parent = originals.resolve_root_ref(root, dict(worker['direct_refs'][0],
        path=str(c.workspace(root, 2)/worker['direct_refs'][0]['path'])))
    assert c.read(parent) == expected
    assert worker['search']['focus']['action'] == 'screen'
    assert search.launch_reason(root, worker) is None
    assert budget.node_admission(root, worker) is None
    assert native.usage(root)['observed_tokens'] == 131747
    assert (root/'settings.json').read_bytes() == settings
    assert all(path.read_bytes() == raw for path, raw in {**before, **native_configuration}.items())
