"""New frozen guidance is delivered without changing execution or legacy tasks."""
import copy
import io
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, controller, execution, guidance, native, runtime, selection, shared_input_views
from test_history_handoff import finish
from execution_fixtures import execution_registry
from test_breadth_science import prepare as managed_prepare, h, worker, run, a, findings

DOMAIN_SUFFIX=' Use exp run --mechanism LABEL [--script FILE]; exp lineage-status.'


def prepare(tmp_path,advice,*,portfolio=False):
    public=tmp_path/'public';public.mkdir()
    objective=public/'goal.md';objective.write_text('Compare candidate gains, losses and unknowns under the same original goal.')
    root=tmp_path/'instance'
    from scidiscovery.research_loop import search
    controller.prepare(root,objective,public,['never-call-a-model'],model='no-model',reasoning_effort='high',
        max_tokens=100000,max_seconds=600,max_steps=12,role_guidance=advice,search_policy=search.DEFAULT_POLICY,
        portfolio_policy=dict(review_after_cycles=3) if portfolio else None)
    finish(root,1,'H',[],action='experiment',search_choice=dict(scope='One named mechanism',
        commitments=[dict(label='P',minimal_test='A discriminating scientific question remains unresolved',existing_ref=None)],
        focus=dict(index=1,action='screen')))
    return root


def assigned_views(root):
    job=c.build_job(root,2,'W',[1]);worker_view=c.read(c.workspace(root,2)/'science-task.json')
    finish(root,2,'W',[1],stage_intent='decision',mechanism_findings=[dict(mechanism='h001.m1',basis='blocked',
        finding='This guidance fixture has no domain observation or experiment and cannot complete scientific screening.')])
    judge=c.build_job(root,3,'A',[1,2]);judge_view=c.read(c.workspace(root,3)/'science-task.json')
    return job,worker_view,judge,judge_view


@pytest.mark.parametrize('kind',['default','opportunity','default_domain','opportunity_domain'])
def test_actual_prepared_worker_and_a_views_deliver_symmetric_choice_and_adaptive_task_roles(tmp_path,kind):
    opportunity=kind.startswith('opportunity')
    supplied=guidance.DEFAULT
    if kind.endswith('_domain'):
        supplied=dict(guidance.OPPORTUNITY if opportunity else guidance.DEFAULT)
        supplied['W']+=DOMAIN_SUFFIX
    root=prepare(tmp_path,supplied,portfolio=opportunity)
    worker_job,worker_view,judge_job,judge_view=assigned_views(root)
    expected=guidance.OPPORTUNITY if opportunity and supplied is guidance.DEFAULT else supplied
    assert c.settings(root)['role_guidance']==expected
    assert sum(len(text.encode()) for text in expected.values())<=guidance.MAX_BYTES==2560
    assert 'Compare plausible explanations' in guidance.STAGE_PLANNING['H']
    assert 'justify a single line' in guidance.STAGE_PLANNING['H']
    text=worker_job['native_role_instruction']
    assert text==expected['W']
    assert 'role_guidance' not in worker_view and 'role_instruction' not in worker_view
    worker_prompt,worker_instruction,_=native.render_scientific_request(worker_job,worker_view,{},
        backend_name='codex',instruction_transport='prompt')
    assert worker_prompt.startswith(text+'\n') and worker_instruction.startswith(text+'\n')
    assert 'Initial/grant opportunities are not trial caps' in text
    assert '--mechanism=task catalog number' in text
    assert 'reconcile unknown calls, never replay' in text
    assert 'Honor checks' in text and 'No auxiliary agents' in text
    judge_text=judge_job['native_role_instruction']
    assert judge_text==expected['A']
    assert 'role_guidance' not in judge_view and 'role_instruction' not in judge_view
    judge_prompt,judge_instruction,_=native.render_scientific_request(judge_job,judge_view,{},
        backend_name='codex',instruction_transport='prompt')
    assert judge_prompt.startswith(judge_text+'\n') and judge_instruction.startswith(judge_text+'\n')
    assert 'no incumbent or unknown-optimality veto' in judge_instruction
    assert c.AUTHOR_EVIDENCE_GUIDANCE in judge_instruction
    assert guidance.STAGE_PLANNING['A'] in judge_prompt
    if kind.endswith('_domain'):
        assert text.endswith(DOMAIN_SUFFIX) and DOMAIN_SUFFIX not in judge_text
    assert worker_job['search']['focus']['action']=='screen'
    assert worker_job['scientific_work_unit']==guidance.WORK_UNITS['screen']
    assert worker_job['scientific_work_unit'] in worker_view['question']


@pytest.mark.parametrize('custom',[False,True])
def test_no_guidance_fallback_and_custom_frozen_text_preserve_their_actual_assignment(tmp_path,custom):
    advice={role:'Custom scientific instruction for '+role for role in 'HWAE'} if custom else None
    root=prepare(tmp_path,advice)
    w,w_view,judge,a_view=assigned_views(root)
    for role,job,view in [('W',w,w_view),('A',judge,a_view)]:
        expected=advice[role] if custom else guidance.OPPORTUNITY[role]
        assert job['native_role_instruction']==expected
        assert 'role_guidance' not in view and 'role_instruction' not in view
        prompt,instruction,_=native.render_scientific_request(job,view,{},
            backend_name='codex',instruction_transport='prompt')
        assert prompt.startswith(expected+'\n') and instruction.startswith(expected+'\n')


@pytest.mark.parametrize('custom', [False, True])
def test_a_breadth_judgment_reaches_actual_native_prompt_with_custom_guidance(tmp_path, monkeypatch, custom):
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'goal.md'; objective.write_text('Compare plausible mechanisms under the original goal.')
    root = tmp_path / 'instance'
    advice = {role: 'Custom scientific instruction for ' + role for role in 'HWAE'} if custom else guidance.DEFAULT
    nodes = {role: dict(max_observed_tokens=10000, max_seconds=120) for role in 'HWAE'}
    controller.prepare(root, objective, public, ['captured-native'], max_tokens=100000,
        max_seconds=600, max_steps=4, model='fixture-no-model', reasoning_effort='high',
        search_policy=None, role_guidance=advice, node_budget_policy=nodes, node_budget_mode='soft')
    job = c.build_job(root, 1, 'A', [])
    work = c.workspace(root, 1)
    task = c.read(work / 'science-task.json')
    judgment = guidance.STAGE_PLANNING['A']
    for concept in ('Prior A gaps/W delta first', 'reuse reports/arrays/code/full-pool index',
            'no routine rereads/refits/repackaging', 'finite choice with limits if sufficient',
            'Batch unmet-goal work: continue_w(improve)',
            'Only evidenced mechanism gaps: revise_h(minimal executable test)',
            'even if methods improve'):
        assert concept.lower() in judgment.lower()
    record = execution.origin(root, 1)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/001.json'),
        budget_slice=dict(task_ref=c.record_ref(root / 'jobs/001.json'), role='A',
            boot_id=budget.clock_stamp()['boot_id'], advance_seconds=0., limits=nodes['A'],
            shared_limits=dict(max_tokens=100000, max_seconds=600), previous_tokens=0, previous_seconds=0.)))
    accepted_budget.accept_native(root, 1, record, c.read(record / 'native-intent.json'))
    c.write(record / 'runner-process.json', dict(fixture='prompt capture only'))
    prompts = []
    commands = []

    class CaptureInput(io.FileIO):
        def __init__(self):
            self.read_fd, write_fd = os.pipe()
            super().__init__(write_fd, 'wb', closefd=True)

        def close(self):
            if self.closed:
                return
            super().close()
            with os.fdopen(self.read_fd, 'rb') as reader:
                prompts.append(reader.read().decode())

    class CaptureProcess:
        pid = 999999999
        returncode = 0

        def __init__(self, args, **kwargs):
            commands.append(list(args))
            self.stdin = CaptureInput()
            read_fd, write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, 'rb')
            try:
                os.write(write_fd, (json.dumps(dict(type='turn.completed', usage=dict(
                    input_tokens=7, cached_input_tokens=2, output_tokens=3))) + '\n').encode())
            finally:
                os.close(write_fd)
            c.write(Path(args[args.index('--output-last-message') + 1]), dict(fixture='transport only'))

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(native.subprocess, 'Popen', CaptureProcess)
    # Capture the request at the native launch boundary; namespace capability
    # probes and mount validation belong to the readonly-view tests.
    namespace_facts = object()
    monkeypatch.setattr(shared_input_views, 'load_wrap_facts', lambda root, job, work, **kwargs: namespace_facts)
    def unwrapped(facts, argv):
        assert facts is namespace_facts
        return argv
    monkeypatch.setattr(shared_input_views, 'wrap_from_facts', unwrapped)
    monkeypatch.setattr(runtime, 'process_identity', lambda pid, binding: dict(task_ref=binding))
    monkeypatch.setattr(runtime, 'process_matches', lambda *args: False)
    monkeypatch.setattr(runtime, 'cleanup_owned_group', lambda *args: 'observed_group_cleaned')
    outcome = native.run_native(root, 1)
    assert outcome['returncode'] == 0 and outcome['stop_reason'] is None
    assert len(prompts) == 1 and prompts[0].count(judgment) == 1
    assert (prompts[0].startswith(job['native_role_instruction'])
        or any(job['native_role_instruction'] in argument for argument in commands[0]))
    assert job['native_role_instruction'] == advice['A']
    assert 'role_guidance' not in task and 'role_instruction' not in task
    assert not (record / 'result-identity.json').exists()  # No scientific verdict was sealed.


def test_previously_frozen_native_literal_is_not_rewritten_by_new_defaults(tmp_path,monkeypatch):
    root=prepare(tmp_path,None)
    monkeypatch.setitem(guidance.OPPORTUNITY,'W','Previously frozen Worker scientific instruction.')
    job=c.build_job(root,2,'W',[1])
    finish(root,2,'W',[1],stage_intent='decision',mechanism_findings=[dict(
        mechanism='h001.m1',basis='blocked',finding='A fixture observation remains missing; no experiment was run.')])
    files=[root/'settings.json',root/'jobs/002.json',c.workspace(root,2)/'science-task.json',c.host_record(c.workspace(root,2))/'result-identity.json']
    before={path:path.read_bytes() for path in files}
    monkeypatch.setitem(guidance.OPPORTUNITY,'W','New defaults cannot retroactively change an accepted assignment.')
    assert c.build_job(root,2,'W',[1])==job
    c.validate(c.workspace(root,2))
    assert before=={path:path.read_bytes() for path in files}


@pytest.mark.process_e2e
def test_local_improve_multiple_calls_already_share_original_scope_without_a_single_trial_gate(tmp_path,execution_registry):
    root=managed_prepare(tmp_path);h(root,labels=('P','Q'))
    _,first=worker(root,tmp_path,2,[1]);run(root,first,'h001.m1')
    finish(root,2,'W',[1],preserve_native_intent=True,
        mechanism_findings=findings('h001.m1',blocked=('h001.m2',)))
    a(root,3,[1,2],focus='h001.m1',mode='improve')
    job,local=worker(root,tmp_path,4,[1,2,3])
    job_bytes=(root/'jobs/004.json').read_bytes()
    assert job['search']['focus']['action']=='improve'
    assert local['scope']['step']==job['step']==4
    assert [row['mechanism_ref'] for row in local['breadth']['mechanisms']]==['h001.m1']
    results=[run(root,local,'h001.m1') for _ in range(3)]
    assert all(result['status']=='known_success' and result['scope']==local['scope'] for result in results)
    assert all(result['mechanism_binding']['mechanism_ref']=='h001.m1' for result in results)
    assert all(result['mechanism_binding']['grant'] is None for result in results)
    assert len({result['call_id'] for result in results})==3
    assert (root/'jobs/004.json').read_bytes()==job_bytes
    view=c.read(c.workspace(root,4)/'science-task.json')
    assert guidance.WORK_UNITS['improve'] in view['question']
    assert 'Initial/grant opportunities are not trial caps' in job['native_role_instruction']


@pytest.mark.parametrize('recommended',['old','new'])
def test_recommendation_does_not_require_proxy_lead_or_proven_target_optimality(recommended):
    candidates=[dict(id=cid,label=cid,evaluation_key='same-public-evaluation',metric=metric,direction='max',status='ok')
        for cid,metric in [('old',.9),('new',.8)]]
    records=[dict(step=1,role='W',recommendation=None,coverage='complete',candidates=candidates,
        current_candidate=dict(candidate_id='old'),actual_submission=None),
        dict(step=2,role='A',recommendation=recommended,coverage=None,candidates=[],current_candidate=None,actual_submission=None)]
    original=copy.deepcopy(records)
    view=selection.project(records)
    assert view['a_recommended']['candidate_id']==recommended
    assert 'target_best' not in view and records==original
    assert [row['id'] for row in view['candidate_facts']]==['old','new']
