"""Sustained scientific breadth advice and existing exact routing; no model calls.

Authored findings and inert execution prove delivery and identity contracts only,
not the scientific effectiveness of the advice or the fixture mechanisms.
"""
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_budget, backends, contracts as c, controller, guidance, native, search
from execution_fixtures import execution_registry
from test_breadth_science import findings, prepare as managed_prepare, run, worker
from test_history_handoff import finish, fixture_final_coverage, fixture_portfolio_review
from test_result_schema import rendered_contract


def prepare(tmp_path, advice):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve the original scientific goal under the same public evaluation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], model='fixture',
        reasoning_effort='high', max_tokens=100000, max_seconds=600, max_steps=8,
        role_guidance=advice, search_policy=search.DEFAULT_POLICY,research_strategy_version=None)
    return root


@pytest.mark.parametrize('role', ['H', 'W', 'A'])
@pytest.mark.parametrize('custom', [False, True])
@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_normal_prepared_scientific_request_delivers_sustained_breadth_and_frozen_literal(
        tmp_path, monkeypatch, role, custom, backend_name):
    advice = {key: 'Preserved custom scientific instruction for ' + key for key in 'HWAE'} if custom else guidance.DEFAULT
    root = prepare(tmp_path, advice)
    if role in {'W', 'A'}:
        finish(root, 1, 'H', [], action='experiment', search_choice=plan())
    if role == 'A':
        finish(root, 2, 'W', [1], mechanism_findings=findings(blocked=('h001.m1',)))
    step, parents = {'H': (1, []), 'W': (2, [1]), 'A': (3, [1, 2])}[role]
    job = c.build_job(root, step, role, parents)
    work = c.workspace(root, step)
    task = c.read(work / 'science-task.json')
    literal = job['native_role_instruction']
    expected = advice[role]
    job_path = root / 'jobs' / f'{step:03d}.json'
    original = job_path.read_bytes()
    monkeypatch.setitem(guidance.DEFAULT, role, 'A future default cannot rewrite this task.')
    assert c.build_job(root, step, role, parents) == job
    backend = backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name == 'codex' else ['independent']),
        schema_transport='prompt' if backend_name == 'codex' else None)
    prompt, instruction, schema = native.render_scientific_request(job, task, {},
        backend_name=backend.name, instruction_transport=backend.instruction_transport)
    schema_path = work / 'response.schema.json'
    c.write(schema_path, schema)
    profile = dict(model='fixture', reasoning_effort='high')
    packet = backend.request(prompt, profile=profile, work=work, schema=schema_path,
        response_schema=schema)
    if backend_name == 'codex':
        assert backend.instruction_transport == 'developer'
        args = backend.launch(profile, work, work / 'pending.json', schema_path, instruction)
        configs = [args[index + 1] for index, value in enumerate(args[:-1]) if value == '-c']
        developer = json.loads(next(value.split('=', 1)[1] for value in configs
            if value.startswith('developer_instructions=')))
        transmitted = developer + '\n' + packet.decode()
        assert literal not in packet.decode()
    else:
        assert backend.instruction_transport == 'prompt'
        transmitted = json.loads(packet)['prompt']
        assert transmitted.startswith(literal + '\n')
    assert transmitted.count(literal) == 1
    assert literal == expected
    assert job_path.read_bytes() == original
    if role != 'A':
        assert transmitted.count(guidance.STAGE_PLANNING[role]) == 1
    assert len(transmitted.encode()) <= c.MAX_FIXED_RESPONSE_BYTES == 3072
    assert schema == c.result_schema(role, job.get('handoff_protocol', c.HANDOFF_PROTOCOL),
        'selection' in job, **c.response_options(job))
    reading = task['reading_order']
    for concept in ('native file/shell tools', 'JSON keys/shapes', 'needed fields or line ranges',
            'combined batch returns', 'Reuse unchanged originals', 'fill gaps/conflicts/truncation'):
        assert concept in reading
        assert concept not in transmitted
    if role == 'H':
        assert 'Check prior directions against subsequent W work' in reading
        for concept in ('competing explanations', 'distinguishing experiments',
                'observations/counterevidence', 'contradictions or stalled progress',
                'constraints/counts/commitments fixed', 'may add/revise mechanisms'):
            assert concept in transmitted
        for declaration in ('search_plan:', 'commitments[{label,existing_ref?}]',
                'index:1-based|null', 'screen|improve'):
            assert declaration in task['breadth_response']
        assert 'minimal_test' not in task['breadth_response']
        for requirement in ('task acceptance', 'minimal_test', 'evaluation driver', 'fair controls', 'replication'):
            assert requirement not in transmitted
    elif role == 'A':
        assert 'portfolio_assessment' not in task
        assert 'portfolio_response' not in task
        assert c.PORTFOLIO_RESPONSE not in transmitted
        assert task['role_contract'].count(c.AUTHOR_EVIDENCE_GUIDANCE) == 1
        assert c.AUTHOR_EVIDENCE_GUIDANCE not in transmitted
        for concept in ('meaningful judgment', 'deliver/finish/continue_w', 'decision_request',
                'question,change_criterion', 'High-level decision-changing', 'mechanical checks/W',
                'not indefinite vetoes'):
            assert concept in task['role_contract']
        assert 'Soft targets do not end research' in transmitted
        assert reading.startswith('The concrete assessment question and latest W changes')
        for concept in ('no routine redo of old observations',
                'Check execution facts only at calls addressable by the current experiment client',
                'Parent claims are not qualification', 'originals remain available'):
            assert concept in reading
        assert task['lineage']['candidates'] == job['history']['view']['candidates']
        assert task['lineage']['indexes'] == job['history']['view']['indexes']
        assert '2-3 full procedures' not in transmitted
    else:
        for concept in ('W sustains direction/design/adaptation',
                'H:hypotheses', 'A:decision-changing judgment', 'observations/residuals',
                'Conflict/stall:revisit breadth', 'runnable candidates/helpers',
                'negatives', 'exact originals'):
            assert concept in transmitted
        assert set(schema['properties']['stage_intent']['enum'])=={'continue','handoff','revise_h','review','decision'}
        assert 'search_plan' in job['scientific_response_fields']
        assert 'next_action' not in schema['properties']
        assert 'before deepening any' not in transmitted
        assert 'fair coverage before focus' not in transmitted
    if role != 'A':
        assert ' A verifies independently. Parents are claims.' in reading
        assert reading.endswith(' Check execution facts only at calls addressable by the current experiment client. For history use assigned originals or select exact frozen-history I IDs for next-node inputs after return. Retained identity alone implies neither readable material nor queryable calls. Prose/requested_evidence/evidence_requests do not bind inputs; do not search private control records. Omissions: conclusion/index/next_page.')
    assert 'declared writes; no wrappers/hashes' in transmitted
    if role == 'H':
        for concept in ('revisable high-level scientific methods/directions',
                'W owns direction/design/implementation/adaptation',
                'Publish rationale', 'declared outputs', 'cite managed originals',
                'No downstream budget/call/audit caps'):
            assert concept in transmitted
    assert schema['additionalProperties'] is True  # Science extras remain allowed without a repeated banner.


@pytest.mark.parametrize('role', ['H', 'W', 'A'])
@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
@pytest.mark.parametrize('gate', ['final_return', 'closeout', 'final_report', 'final_only',
    'output_repair', 'work_repair'])
def test_existing_final_closeout_and_repair_gates_suppress_new_work_planning(tmp_path, role, backend_name, gate):
    root = prepare(tmp_path, guidance.DEFAULT)
    if role == 'W':
        finish(root, 1, 'H', [], action='experiment', search_choice=plan())
    step, parents = (2, [1]) if role == 'W' else (1, [])
    job = c.build_job(root, step, role, parents)
    task = c.read(c.workspace(root, step) / 'science-task.json')
    options = {}
    intent = {}
    if gate == 'final_return':
        options['final_return'] = True
    elif gate == 'closeout':
        job = dict(job, purpose='closeout')
    elif gate in {'output_repair', 'work_repair'}:
        intent[gate] = dict(code='fixture_diagnostic', diagnostic='Preserve the original scientific scope.')
    else:
        job = dict(job, **{gate: True})
    prompt, _, _ = native.render_scientific_request(job, task, intent, backend_name=backend_name,
        instruction_transport='prompt', **options)
    assert guidance.STAGE_PLANNING[role] not in prompt
    if gate == 'final_return':
        assert 'Protected FINAL: conclude now; no new role/audit/experiment.' in prompt
    elif gate == 'output_repair':
        for boundary in ('Fix diagnosed JSON/layout only', 'full JSON; keep science/plans/limits',
                'No work/rerun/resubmit', 'W grants unchanged', 'no prior experiment implied'):
            assert boundary in prompt


@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_frozen_long_worker_literal_and_work_unit_keep_the_actual_fragment_contract_bounded(tmp_path, backend_name):
    # Retained literals are intentionally independent of future shorter defaults.
    literal = ('Own methods/candidates within H; A suggests methods. Honor checks; use --mechanism. '
        'Initial/grant calls protect screening opportunities, not trial totals. '
        'Route scope changes via A to H; reconcile unknown calls, never replay. '
        'Return to independent A, not global completion. No auxiliary agents.')
    unit = ('In H, follow findings/global unmet goals beyond a recipe/contrast. '
        'Return to A for required independent judgment, strategic choices/tradeoffs, '
        'direction-changing counterevidence, stall/scope change/blocker or actual limits. Retain spent budgets.')
    job = dict(role='W', handoff_protocol=c.HANDOFF_PROTOCOL, search={},
        native_role_instruction=literal, scientific_work_unit=unit,
        worker_fragment_policy=dict(context='same_session'))
    task = c.response_instructions(job)
    backend = backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name == 'codex' else ['independent'],
        native_options={} if backend_name == 'codex' else None),
        schema_transport='prompt' if backend_name == 'codex' else None)
    size, _, instruction, _ = rendered_contract(tmp_path, job, task,
        dict(continuation_kind='fragment'), backend, remaining={},
        execution_interface=dict(capabilities={}, result_protocol='local-result-v1', executor='local_execution:command'),
        managed_command=['experiment'],
        budget_status=dict(shared_seconds_remaining=0, shared_observed_tokens_remaining=0))
    assert instruction.startswith(literal + '\n') and job['scientific_work_unit'] == unit
    if os.environ.get('SCID_TEST_LOG_DIR'):
        report = Path(os.environ['SCID_TEST_LOG_DIR']) / 'retained-worker-contract-sizes.jsonl'
        report.parent.mkdir(parents=True, exist_ok=True)
        with report.open('a') as stream:
            stream.write(json.dumps(dict(backend=backend_name, branch='fragment',
                rendered_bytes=size, diagnostic_bytes=0)) + '\n')
    assert size <= c.MAX_FIXED_RESPONSE_BYTES == 3072


@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
@pytest.mark.parametrize('role', ['A', 'W'])
def test_unlimited_shared_balances_keep_actual_ordinary_and_fragment_contracts_bounded(tmp_path, backend_name, role):
    job = dict(role=role, handoff_protocol=c.HANDOFF_PROTOCOL, search={},
        scientific_response_fields=['portfolio_review', 'final_coverage'], formal_review_version=1,
        native_role_instruction=max((guidance.DEFAULT[role], c.ROLE_INSTRUCTIONS[role]),
            key=lambda value: len(value.encode())))
    if role == 'A':
        job.update(portfolio_context={}, final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')])
    else:
        job['worker_fragment_policy']=dict(context='same_session')
        job['scientific_work_unit'] = max(guidance.WORK_UNITS.values(), key=lambda value: len(value.encode()))
    task = c.response_instructions(job)
    backend = backends.selected(dict(native_backend=backend_name,
        native_launcher_argv=['codex'] if backend_name == 'codex' else ['independent'],
        native_options={} if backend_name == 'codex' else None),
        schema_transport='prompt' if backend_name == 'codex' else None)
    worker = role == 'W'
    size, _, _, _ = rendered_contract(tmp_path, job, task,
        dict(continuation_kind='fragment' if worker else 'automatic_recovery'), backend,
        remaining={},
        execution_interface=dict(capabilities={}, result_protocol='local-result-v1',
            executor='local_execution:command') if worker else None,
        managed_command=['experiment'] if worker else None,
        budget_status=dict(shared_seconds_remaining=None, shared_observed_tokens_remaining=None))
    assert size <= c.MAX_FIXED_RESPONSE_BYTES == 3072


def plan(*, revised=False):
    commitments = [dict(label='Transport-limited explanation' if not revised else 'Retained transport explanation',
        existing_ref='h001.m1' if revised else None)]
    if revised:
        commitments.append(dict(label='Interface-state explanation', existing_ref=None))
    return dict(scope='Distinguish transport from interface-state causal explanations.',
        commitments=commitments, focus=dict(index=2 if revised else 1, action='screen'))


def assess(root, action, *, main_line='h001.m1', revision_reason=None):
    """Seal explicit synthetic scientific judgment using the production consumers."""
    job = c.build_job(root, 3, 'A', [1, 2])
    record = c.host_record(c.workspace(root, 3))
    reason = (revision_reason or 'The retained comparison leaves an interface-history alternative outside the current H scope.'
        if action == 'revise_h' else
        'Synthetic evidence supports the retained main line; no feasible competing discriminator changes this bounded decision. '
        'Retain the missing target evidence and reopen when the stated limitation changes.')
    payload = dict(summary='Synthetic scientific assessment', scientific_result=reason,
        limitations=['Authored fixture science; inert execution provides no scientific qualification.'],
        next_action=action, route_reason=reason, implementation_selection=None,
        candidate_recommendation=None, progress_assessment=None,
        search_review=dict(dispositions=[], next_focus=dict(mechanism_ref='h001.m1', action='improve')
            if action == 'continue_w' else None),
        decision_request=dict(decision='mechanism', question='Which causal explanation resolves the original goal?',
            change_criterion='A matched public discriminator changes the mechanism choice.')
            if action in {'continue_w', 'revise_h'} else None)
    fixture_portfolio_review(root, job, payload, dict(main_line=main_line,
        discriminating_work=None, reason=reason))
    fixture_final_coverage(root, job, payload)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/003.json')))
    accepted_budget.accept_native(root, 3, record, c.read(record / 'native-intent.json'))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, 3)
    outcome = c.read(record / 'result-identity.json')['control_outcome']
    assert outcome['search_status'] == 'applied'
    if action in {'continue_w', 'revise_h'}:
        assert outcome['decision_status'] == 'applied'
    return record


def initial_screen(tmp_path, *, competing=False):
    root = managed_prepare(tmp_path, qualified_local=True)
    choice = plan()
    if competing:
        choice['commitments'].append(dict(label='Interface-state explanation', existing_ref=None,
            minimal_test='A history effect would distinguish interface states; target-history input is missing.'))
    finish(root, 1, 'H', [], action='experiment', search_choice=choice)
    _, config = worker(root, tmp_path, 2, [1], qualified_local=True)
    run(root, config, 'h001.m1', qualified_local=True)
    finish(root, 2, 'W', [1], mechanism_findings=findings('h001.m1',
        blocked=('h001.m2',) if competing else ()), preserve_native_intent=True)
    return root


@pytest.mark.process_e2e
@pytest.mark.parametrize('revision_reason', [
    pytest.param('The current method continues to improve, but a goal-relevant mechanism gap remains: '
        'the retained W thickness comparison cannot address interface history outside the current H scope. '
        'Revisit that missing causal explanation using the retained evidence and the decision question.',
        id='mechanism-gap-despite-method-improvement'),
    pytest.param('The current method continues to improve, but a scientific bottleneck remains: '
        'the retained W thickness comparison cannot distinguish transport from an interface-history effect. '
        'Revise the assumptions and discriminator using the retained evidence and the decision question.',
        id='scientific-bottleneck-despite-method-improvement'),
])
def test_a_gap_or_bottleneck_revision_adds_distinct_route_with_stable_refs_and_fresh_h_ordinal(
        tmp_path, execution_registry, revision_reason):
    root = initial_screen(tmp_path)
    record = assess(root, 'revise_h', revision_reason=revision_reason)
    paths = [root / 'jobs/003.json', record / 'science-output.json', record / 'result-identity.json']
    originals = {path: path.read_bytes() for path in paths}
    payload = c.validate(c.workspace(root, 3))
    assert payload['next_action'] == 'revise_h'
    assert payload['scientific_result'] == payload['route_reason'] == revision_reason
    assert payload['decision_request'] == dict(decision='mechanism',
        question='Which causal explanation resolves the original goal?',
        change_criterion='A matched public discriminator changes the mechanism choice.')
    assert payload['search_review']['next_focus'] is None
    assert payload['portfolio_review']['main_line'] == 'h001.m1'
    a_job = c.read(root / 'jobs/003.json')
    role, parents, instruction, evidence_return, current_h, current_w = controller._route(
        root, 3, 'A', payload, a_job, 1, 2)
    assert (role, parents, instruction, evidence_return, current_h, current_w) == (
        'H', [1, 2, 3], revision_reason, None, 1, 2)
    h_job = c.build_job(root, 4, role, parents, instruction=instruction,
        evidence_return=evidence_return)
    h_job_bytes = (root / 'jobs/004.json').read_bytes()
    h_task = c.read(c.workspace(root, 4) / 'science-task.json')
    assert h_task['decision_context']['source_action'] == 'revise_h'
    assert h_task['decision_context']['original_instruction'] == revision_reason
    assert h_task['decision_context']['source_original'] == 'inputs/parents/003/conclusion.json'
    assert any(row['path'] == 'inputs/parents/003/conclusion.json' for row in h_job['direct_originals'])
    finish(root, 4, 'H', [1, 2, 3], action='experiment', search_choice=plan(revised=True))
    state = search.project(root, 5)
    assert set(state['mechanisms']) == {'h001.m1', 'h004.m2'}
    assert state['mechanisms']['h001.m1']['original_step'] == 1
    assert state['mechanisms']['h001.m1']['source_step'] == 4
    assert state['mechanisms']['h004.m2']['label'] == 'Interface-state explanation'
    assert state['mechanisms']['h004.m2']['original_step'] == 4
    assert state['focus'] == dict(mechanism_ref='h004.m2', action='screen')
    job, config = worker(root, tmp_path, 5, [4], qualified_local=True)
    assert [row['path'] for row in job['direct_refs']] == ['inputs/parents/004/conclusion.json']
    assert job['search']['focus'] == dict(mechanism_ref='h004.m2', action='screen')
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['screen']
    assert search.launch_reason(root, job) is None
    accepted = run(root, config, 'h004.m2', qualified_local=True)
    assert accepted['mechanism_binding']['mechanism_ref'] == 'h004.m2'
    finish(root, 5, 'W', [4], mechanism_findings=findings('h004.m2', blocked=('h001.m1',)),
        preserve_native_intent=True)
    assert all(path.read_bytes() == original for path, original in originals.items())
    assert (root / 'jobs/004.json').read_bytes() == h_job_bytes


@pytest.mark.process_e2e
@pytest.mark.parametrize('action', ['continue_w', 'finish'])
def test_supported_concentration_and_closure_remain_scientific_choices(tmp_path, execution_registry, action):
    root = initial_screen(tmp_path, competing=True)
    main_line = 'h001.m1' if action == 'continue_w' else None
    record = assess(root, action, main_line=main_line)
    payload = c.validate(c.workspace(root, 3))
    assert payload['next_action'] == action
    assert c.read(record / 'science-output.json')['portfolio_review']['discriminating_work'] is None
    assert c.read(record / 'science-output.json')['portfolio_review']['main_line'] == main_line
    assert search.project(root, 4)['mechanisms']['h001.m2']['status'] == 'pending'
    if action == 'continue_w':
        job = c.build_job(root, 4, 'W', [1, 2, 3], instruction=payload['route_reason'])
        assert job['search']['focus'] == dict(mechanism_ref='h001.m1', action='improve')
        assert job['scientific_work_unit'] == guidance.WORK_UNITS['improve']
        assert search.launch_reason(root, job) is None
