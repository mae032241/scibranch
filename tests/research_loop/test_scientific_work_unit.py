"""Frozen W task units reach actual native turns without changing stage control."""
import json
import os
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import (budget, contracts as c, controller,
    execution, experiment_client, guidance, native, search)
from execution_fixtures import execution_registry
from test_breadth_science import a
from test_default_native_local_execution import actual_native
from test_history_handoff import finish
from test_worker_fragments import FRAGMENT_FAKE


def transport():
    # Reuse the existing real subprocess/session transport. Capture what the
    # native worker actually receives; keep its registered experiment lifecycle.
    source = FRAGMENT_FAKE.replace(
        "counter.write_text(str(number+1))",
        "counter.write_text(str(number+1))\n"
        "task=json.loads((work/'science-task.json').read_text())\n"
        "developer=''\n"
        "for index,value in enumerate(args[:-1]):\n"
        "    if value=='-c' and args[index+1].startswith('developer_instructions='):\n"
        "        developer=json.loads(args[index+1].split('=',1)[1])\n"
        "with (work/'work/received-prompts.jsonl').open('a') as out:\n"
        "    out.write(json.dumps({'stdin':prompt,'developer':developer,'question':task.get('question',''),'number':number})+'\\n')\n"
        "if role=='W' and number:\n"
        "    from scidiscovery.research_loop import execution\n"
        "    assert not (execution.origin(root,job['step'])/'result-identity.json').exists()")
    source = source.replace(
        "assert response.returncode==0,(response.stdout,response.stderr)\n"
        "        return json.loads(response.stdout)",
        "if job['step']==2 and number==0 and os.environ.get('SCID_TASK_UNIT_PREFIX_FAILED')=='1' and arguments[0]=='run':\n"
        "            result=json.loads(response.stdout)\n"
        "            assert result['status']=='known_failed' and result['returncode']==3,result\n"
        "            assert response.returncode==1,(response.stdout,response.stderr)\n"
        "            return result\n"
        "        assert response.returncode==0,(response.stdout,response.stderr)\n"
        "        return json.loads(response.stdout)")
    run = "result=exp(['run','--experiment-dir',str(work/'experiments/original-fragment'),'--script',str(script)])"
    assert source.count(run) == 1
    source = source.replace(run,
        "if job['step']==2 and os.environ.get('SCID_TASK_UNIT_PREFIX_FAILED')=='1':\n"
        "            script.write_text(script.read_text()+'\\nraise SystemExit(3)\\n')\n"
        "        result=exp(['run','--experiment-dir',str(work/'experiments/original-fragment'),"
        "'--script',str(script),'--mechanism','1'])")
    source = source.replace("assert result['status']=='known_success'",
        "assert result['status']==('known_failed' if job['step']==2 and os.environ.get('SCID_TASK_UNIT_PREFIX_FAILED')=='1' else 'known_success')")
    return source.replace(
        'payload.update(stage_intent=intent,mechanism_findings=None)',
        "payload.update(stage_intent=intent,mechanism_findings=None)\n"
        "    if task.get('candidate_nomination_mechanisms'):\n"
        "        payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],\n"
        "            reason='This no-model observation fixture declares no scientifically suitable candidate.')\n"
        "            for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])\n"
        "    if job['search']['focus']['action']=='screen':\n"
        "        payload['mechanism_findings']=[{'mechanism':'h001.m1','basis':'observed',"
        "'finding':'The registered observation is available; this transport fixture establishes no target efficacy.'}]")


def prefix(tmp_path, action, monkeypatch, *, custom=False):
    actual = actual_native()
    # The real child runs from its W workspace, outside pytest's source path.
    # Retain the fixture's execution entry-point metadata after current src.
    monkeypatch.setenv('PYTHONPATH', str(Path(c.__file__).resolve().parents[2])
        + os.pathsep + os.environ.get('PYTHONPATH', ''))
    public = tmp_path/'public'; public.mkdir()
    objective = public/'goal.md'
    objective.write_text('Preserve required scientific checks; explore only the authorized H mechanism.')
    # Replace only model communication; Local work uses the selected real
    # installation's sandbox. No native home, authentication or PATH changes.
    package = tmp_path/'node_modules/@openai/codex'
    entry = package/'bin/codex.js'; entry.parent.mkdir(parents=True)
    entry.write_text('#!'+sys.executable+'\n'+transport()); entry.chmod(0o555)
    launcher = tmp_path/'bin/codex'; launcher.parent.mkdir()
    launcher.symlink_to(entry)
    architecture, target = {'x86_64':('x64','x86_64-unknown-linux-musl'),
        'aarch64':('arm64','aarch64-unknown-linux-musl')}[os.uname().machine]
    installed = package/'node_modules/@openai'/('codex-linux-'+architecture)/'vendor'/target/'bin/codex'
    installed.parent.mkdir(parents=True); installed.symlink_to(actual)
    root = tmp_path/'instance'
    controller.prepare(root, objective, public, [str(launcher)],
        max_tokens=100000, max_seconds=120, max_steps=16,
        model='no-model', reasoning_effort='high', search_policy=search.DEFAULT_POLICY,
        node_budget_mode='soft', experiment_mode='local',
        native_options=dict(workspace='workspace-write',network=False,web='disabled',subagents=False,
            sandbox_workspace_write=dict(writable_roots=[],network_access=False,
                exclude_tmpdir_env_var=False,exclude_slash_tmp=False)),
        node_budget_policy={role:dict(max_observed_tokens=200,max_seconds=30) for role in 'HWAE'},
        resume_policy=dict(max_attempts=2,protocol='codex_exec_resume',usage_scope='turn',output_repair=True),
        worker_fragment_policy=dict(context='same_session'),
        **(dict(role_guidance={role:'Original custom '+role+' scientific criteria.' for role in 'HWAE'}) if custom else {}))
    c.write(root/'host-started.json',budget.clock_stamp())
    budget.transition(root,'advance','Scientific work-unit transport fixture')
    finish(root,1,'H',[],action='experiment',search_choice=dict(
        scope='One explicitly authorized scientific mechanism',
        commitments=[dict(label='P',minimal_test='A discriminating observation with required checks',existing_ref=None)],
        focus=dict(index=1,action='improve')))
    monkeypatch.setenv('SCID_U2_FAKE_TURNS','1')
    monkeypatch.setenv('SCID_TASK_UNIT_PREFIX_FAILED','1' if action=='repair' else '0')
    c.build_job(root,2,'W',[1])
    # Use the real runner to create the original budget anchor, terminal,
    # cleanup, usage and managed-call proofs before A assigns the next unit.
    try:
        native.execute_native(root,2)
    except (RuntimeError, ValueError) as error:
        # Preserve the original failure; fixture scratch is removed by the
        # supervisor, so retain only bounded engineering diagnostics in pytest.
        record = execution.record(root, 2)
        outcome = c.read(record / 'native-outcome.json')
        usage = outcome.get('usage', {})
        calls = experiment_client.record_directory(root, 2) / 'calls'
        facts = {key: outcome.get(key) for key in ('returncode', 'stop_reason', 'output_status')}
        facts.update(unknown_steps=usage.get('unknown_steps'),
            blocking_unknown_steps=usage.get('blocking_unknown_steps'),
            managed_record_counts={name: len(list(calls.glob('*/' + name)))
                for name in ('accepted.json', 'process.json', 'completed.json')})
        stderr = record / 'native.stderr'
        tail = b''
        if stderr.exists():
            with stderr.open('rb') as stream:
                stream.seek(max(0, stderr.stat().st_size - 4096))
                tail = stream.read(4096)
        error.add_note('Prefix native facts: ' + json.dumps(facts, sort_keys=True))
        error.add_note('Prefix native stderr (last <=4096 bytes): ' + tail.decode('utf-8', errors='replace'))
        raise
    a(root,3,[1,2],focus='h001.m1',mode=action)
    return root


def execute(root, job):
    before = (root/'jobs/004.json').read_bytes()
    native.execute_native(root,4)
    assert (root/'jobs/004.json').read_bytes() == before
    work = c.workspace(root,4)
    received = [json.loads(line) for line in (work/'work/received-prompts.jsonl').read_text().splitlines()]
    question = c.read(work/'science-task.json').get('question','')
    assert all(row['question'] == question for row in received)
    assert c.validate(work)['role'] == 'W'
    return received, [row['developer']+'\n'+row['stdin'] for row in received]


@pytest.mark.process_e2e
@pytest.mark.parametrize('action', ['screen','improve','validate','repair'])
def test_actual_native_prompt_uses_explicit_frozen_work_unit(tmp_path,monkeypatch,execution_registry,action):
    root = prefix(tmp_path,action,monkeypatch)
    instruction=c.read(execution.raw(root,3))['route_reason']
    job = c.build_job(root,4,'W',[1,2,3],instruction=instruction)
    literal = guidance.WORK_UNITS[action]
    assert job['search']['focus']['action'] == action
    assert job['scientific_work_unit'] == literal
    view = c.read(c.workspace(root,4)/'science-task.json')
    assert literal in view['question']
    assert job['instruction'] == instruction
    assert view['decision_context']['original_instruction'] == job['instruction']
    assert 'scientific_work_unit' not in c.result_schema('W',job['handoff_protocol'])['properties']
    monkeypatch.setenv('SCID_U2_FAKE_TURNS','3' if action=='improve' else '1')
    received, prompts = execute(root,job)
    assert all(row['question'].count(literal) == 1 for row in received)
    assert all(prompt.count(guidance.STAGE_PLANNING['W']) == 1 for prompt in prompts)
    assert all("Complete your role's scientific work within the budget" not in prompt for prompt in prompts)
    assert [c.read(path)['role'] for path in sorted((root/'jobs').glob('*.json'))] == ['H','W','A','W']
    if action=='improve':
        chain = execution.records(root,4)
        assert len(chain) == len(prompts) == 3
        assert [c.read(path/'science-output.json')['stage_intent'] for path in chain] == ['continue','continue','review']
        assert [c.read(path/'native-intent.json')['budget_slice']['previous_tokens'] for path in chain[1:]] == [14,28]
        assert all(c.read(path/'native-intent.json')['continuation_kind']=='fragment' for path in chain[1:])
        assert len(list((experiment_client.record_directory(root,4)/'calls').iterdir())) == 1
        configs = [c.read(path/'experiment-config.json') for path in chain]
        assert all(config['scope']==configs[0]['scope'] for config in configs)


@pytest.mark.process_e2e
def test_cached_work_unit_and_custom_criteria_survive_future_default_change(tmp_path,monkeypatch,execution_registry):
    root = prefix(tmp_path,'improve',monkeypatch,custom=True)
    frozen = 'Previously frozen bounded scientific search; preserve the original declared checks.'
    monkeypatch.setitem(guidance.WORK_UNITS,'improve',frozen)
    job = c.build_job(root,4,'W',[1,2,3],instruction='Original user-required check remains binding.')
    work = c.workspace(root,4)
    originals = [(root/'jobs/004.json').read_bytes(),(work/'science-task.json').read_bytes()]
    monkeypatch.setitem(guidance.WORK_UNITS,'improve','A future default must not replace the accepted literal.')
    rebuilt = c.build_job(root,4,'W',[1,2,3],instruction=job['instruction'])
    assert rebuilt['scientific_work_unit'] == frozen
    assert originals == [(root/'jobs/004.json').read_bytes(),(work/'science-task.json').read_bytes()]
    assert rebuilt['role_guidance'] == rebuilt['native_role_instruction'] == 'Original custom W scientific criteria.'
    monkeypatch.setenv('SCID_U2_FAKE_TURNS','1')
    received, prompts = execute(root,rebuilt)
    prompt = prompts[0]
    assert received[0]['question'].count(frozen) == 1
    assert prompt.count('Original custom W scientific criteria.') == 1
    assert 'future default must not replace' not in prompt


@pytest.mark.process_e2e
def test_retained_job_without_work_unit_is_not_backfilled(tmp_path,monkeypatch,execution_registry):
    root = prefix(tmp_path,'improve',monkeypatch)
    units = guidance.WORK_UNITS
    monkeypatch.setattr(guidance,'WORK_UNITS',{})
    job = c.build_job(root,4,'W',[1,2,3])
    assert 'scientific_work_unit' not in job
    work = c.workspace(root,4)
    originals = [(root/'jobs/004.json').read_bytes(),(work/'science-task.json').read_bytes()]
    monkeypatch.setattr(guidance,'WORK_UNITS',units)
    rebuilt = c.build_job(root,4,'W',[1,2,3])
    assert 'scientific_work_unit' not in rebuilt
    assert originals == [(root/'jobs/004.json').read_bytes(),(work/'science-task.json').read_bytes()]
    monkeypatch.setenv('SCID_U2_FAKE_TURNS','1')
    received, _ = execute(root,rebuilt)
    assert all('Work unit:' not in row['question'] for row in received)


def test_missing_author_action_does_not_invent_a_work_unit(tmp_path):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    from test_worker_task_kinds import instance, hypothesis
    root = instance(tmp_path)
    with pytest.raises(OutputRepairRequired) as failure:
        hypothesis(root,None)
    assert failure.value.code == 'science_task_action_required'
    job = c.read(root/'jobs/001.json')
    assert job['role'] == 'H'
    assert 'scientific_work_unit' not in job
    assert 'Work unit:' not in c.read(c.workspace(root,1)/'science-task.json').get('question','')
    assert not (root/'jobs/002.json').exists()
