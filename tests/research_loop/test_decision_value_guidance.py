"""Scientific advice stays frozen and reaches the native role instruction."""
import json
import sys

import pytest

from scidiscovery.research_loop import accepted_budget, contracts as c, controller, decision, guidance, native, search
from test_history_handoff import finish, fixture_portfolio_review
from test_node_budget import policy as node_policy

COV_USAGE = (' Cov public tools: exp run [--script FILE] --new-plan or --parent-plan NAME; '
             'exp lineage-status. Other exp commands keep their public meanings.')


def prepare(tmp_path, advice, *, marker=1, search_policy=None, launcher=None, **options):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep data separation and the required five comparisons; resolve the target-condition gap.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, launcher or ['never-launched'], max_tokens=100_000,
                       max_seconds=3600, max_steps=8, model='fixture', reasoning_effort='high',
                       search_policy=search_policy, role_guidance=advice, handoff_authority_version=marker,
                       **options)
    return root


@pytest.mark.parametrize('advice', [None, guidance.DEFAULT])
def test_current_role_advice_reaches_frozen_science_view_with_original_constraints(tmp_path, advice):
    root = prepare(tmp_path, advice)
    original_objective = (tmp_path/'public'/'objective.md').read_bytes()
    for step, role in enumerate(('H', 'W', 'A', 'E'), 1):
        job, work, _ = finish(root, step, role, [step - 1] if step > 1 else [])
        view = c.read(work / 'science-task.json')
        expected = guidance.OPPORTUNITY[role] if advice is None else advice[role]
        assert job['native_role_instruction'] == expected
        prompt, instruction, _ = native.render_scientific_request(job, view, {}, backend_name='codex',
            instruction_transport='prompt')
        assert instruction.startswith(expected+'\n') and prompt.startswith(expected+'\n')
        assert 'role_guidance' not in view and 'role_instruction' not in view
        if role == 'A':
            assert view['reading_order'].startswith('The concrete assessment question and latest W changes')
            assert 'Parent claims are not qualification' in view['reading_order']
        else:
            assert ' A verifies independently. Parents are claims.' in view['reading_order']
            assert 'Check execution facts only at calls addressable by the current experiment client' in view['reading_order']
            assert 'select exact frozen-history I IDs for next-node inputs after return' in view['reading_order']
            assert 'Prose/requested_evidence/evidence_requests do not bind inputs' in view['reading_order']
            assert view['reading_order'].endswith('Omissions: conclusion/index/next_page.')
        # Source construction retains a logical namespace, mounted at launch.
        # Read its declared source rather than requiring a copied input tree.
        objective = next(item for item in job['global_materials'] if item['path']==view['global']['objective'])
        bound_objective = (root/objective['source']).read_bytes()
        assert bound_objective == original_objective
        assert b'required five comparisons' in bound_objective
        assert b'Keep data separation' in bound_objective
        assert 'decision_value' not in c.result_schema(role, job['handoff_protocol'])['properties']
        assert c.validate(work)['summary'] == 'A contribution'
    decisions = decision.schema()['properties']['decision']['enum']
    assert set(decisions) == {'candidate', 'mechanism', 'compliance'}
    assert 'Independently judge the requested high-level' in guidance.DEFAULT['A']
    assert 'High-level decision-changing' in c.AUTHOR_EVIDENCE_GUIDANCE
    assert 'mechanical checks/W' in c.AUTHOR_EVIDENCE_GUIDANCE
    assert 'not indefinite vetoes' in c.AUTHOR_EVIDENCE_GUIDANCE
    assert 'Honor checks' in guidance.DEFAULT['W']
    assert 'Own the research direction' in guidance.DEFAULT['W']
    assert 'W owns direction/design/implementation/adaptation' in guidance.STAGE_PLANNING['H']
    assert 'Propose revisable competing explanations' in guidance.DEFAULT['H']
    assert 'Preserve original constraints and counts' in guidance.DEFAULT['H']
    assert 'W may add or revise mechanisms' in guidance.DEFAULT['H']
    assert 'minimal_test' not in guidance.DEFAULT['H']
    assert 'validate' not in guidance.DEFAULT['H'] and 'repair' not in guidance.DEFAULT['H']


def test_actual_domain_usage_fits_existing_total_bound_and_only_w_receives_it(tmp_path):
    advice = dict(guidance.DEFAULT)
    advice['W'] += COV_USAGE
    assert sum(len(text.encode()) for text in guidance.DEFAULT.values()) + len(COV_USAGE.encode()) <= guidance.MAX_BYTES
    assert sum(len(text.encode()) for text in guidance.policy(advice).values()) <= guidance.MAX_BYTES
    root = prepare(tmp_path, advice)
    finish(root, 1, 'H', [], action='experiment')
    job, work, _ = finish(root, 2, 'W', [1])
    task = c.read(work/'science-task.json')
    prompt, instruction, _ = native.render_scientific_request(job, task, {}, backend_name='codex',
        instruction_transport='prompt')
    assert job['native_role_instruction'].endswith(COV_USAGE)
    assert instruction.startswith(job['native_role_instruction']+'\n') and COV_USAGE in prompt
    assert 'Cov public tools' not in c.read(root/'jobs/001.json')['native_role_instruction']
    assert 'exp run' not in guidance.DEFAULT['W']
    assert 'hidden' not in json.dumps(c.science_view(job, root))


def test_guidance_accepts_exact_byte_bound_and_rejects_one_extra_byte():
    boundary = dict(guidance.DEFAULT)
    padding = guidance.MAX_BYTES - sum(len(text.encode()) for text in boundary.values())
    assert padding >= 0
    boundary['E'] += 'x' * padding
    assert sum(len(text.encode()) for text in guidance.policy(boundary).values()) == guidance.MAX_BYTES
    too_large = {**boundary, 'E': boundary['E'] + 'x'}
    with pytest.raises(ValueError, match='frozen byte bound'):
        guidance.policy(too_large)


@pytest.mark.parametrize('marker', [None, 1])
def test_prepared_literal_and_current_fallback_are_not_rewritten(tmp_path, monkeypatch, marker):
    custom = {**guidance.DEFAULT, 'A': 'Frozen global assessment: compare existing evidence and retain limitations.'}
    expected = custom['A'] if marker == 1 else guidance.OPPORTUNITY['A']
    root = prepare(tmp_path, custom if marker == 1 else None, marker=marker)
    finish(root, 1, 'H', [], action='analyze')
    job, work, record = finish(root, 2, 'A', [1])
    paths = [root / 'settings.json', root / 'jobs/002.json', work / 'science-task.json',
             record / 'science-output.json', record / 'science-result.schema.json', record / 'result-identity.json']
    originals = {path: path.read_bytes() for path in paths}
    assert job['native_role_instruction'] == expected
    monkeypatch.setitem(guidance.DEFAULT, 'A', 'Changed future guidance must not replace a prepared literal')
    monkeypatch.setitem(guidance.OPPORTUNITY, 'A', 'Changed future opportunity advice must not replace a prepared literal')
    assert c.build_job(root, 2, 'A', [1]) == job
    assert c.validate(work)['next_action'] == 'wait'
    assert job['handoff_authority_version'] == 1
    assert job['native_role_instruction'] == expected
    view = c.science_view(job, root)
    prompt, instruction, _ = native.render_scientific_request(job, view, {}, backend_name='codex',
        instruction_transport='prompt')
    assert instruction.startswith(expected+'\n') and prompt.startswith(expected+'\n')
    assert 'role_guidance' not in view and 'role_instruction' not in view
    assert all(path.read_bytes() == raw for path, raw in originals.items())


def assessment_with_comparison_recipe(root, action, next_action='continue_w', *,
        instruction=None, worker_request=None):
    """Seal authored A science with its optional request, without a native call."""
    job = c.build_job(root, 3, 'A', [1, 2])
    record = c.host_record(c.workspace(root, 3))
    instruction = instruction or 'Resolve the target-condition gap: compare the proposed 2x2 grid, then return to A.'
    payload = dict(summary='The broad H goal remains unmet',
        scientific_result='The initial observation has not resolved the target-condition gap; no global completion or candidate selection is justified.',
        limitations=['Synthetic scientific claims only; no model or experiment executed.'],
        next_action=next_action, route_reason=instruction,
        search_review=dict(dispositions=[], next_focus=dict(mechanism_ref='h001.m1', action=action)),
        decision_request=dict(decision='mechanism', question='Which related change resolves the target-condition gap?',
            change_criterion='Retained fair controls show that the target-condition gap is resolved.'),
        worker_request=worker_request if worker_request is not None else dict(
            proposed_grid='2x2: two candidate methods by two target conditions',
            return_point='Return to A after the four comparisons.'))
    fixture_portfolio_review(root, job, payload)
    c.write(record/'native-intent.json', dict(task_ref=c.record_ref(root/'jobs/003.json')))
    accepted_budget.accept_native(root, 3, record, c.read(record/'native-intent.json'))
    c.write(record/'science-output.json', payload)
    c.write(record/'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record/'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(root), raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root, 3)
    return instruction, record


def comparison_recipe_stage(tmp_path, action, *, next_action='continue_w',
        instruction=None, worker_request=None, h_choice=None):
    root = prepare(tmp_path, guidance.DEFAULT, search_policy=search.DEFAULT_POLICY, launcher=[sys.executable],
        node_budget_mode='soft', node_budget_policy=node_policy(),
        worker_fragment_policy=dict(context='same_session'),
        resume_policy=dict(max_attempts=2, protocol='codex_exec_resume', usage_scope='turn', output_repair=True))
    finish(root, 1, 'H', [], action='experiment',
        text='Investigate related scientific improvements until the target-condition gap is resolved; preserve all required controls.',
        search_choice=h_choice if h_choice is not None else dict(scope='Resolve the target-condition gap within this mechanism',
            commitments=[dict(label='Related candidate changes', minimal_test='Fair target-condition comparison', existing_ref=None)],
            focus=dict(index=1, action='improve')))
    finish(root, 2, 'W', [1], text='The initial comparison leaves the target-condition gap unresolved.')
    instruction, record = assessment_with_comparison_recipe(root, action, next_action,
        instruction=instruction, worker_request=worker_request)
    return root, instruction, record


def test_improve_a_comparison_recipe_preserves_local_scientific_iterations_and_originals(tmp_path, monkeypatch):
    root, instruction, record = comparison_recipe_stage(tmp_path, 'improve')
    paths = [root/'settings.json', root/'jobs/003.json', record/'science-output.json',
        record/'result-identity.json', c.host_record(c.workspace(root, 1))/'science-output.json']
    originals = {path: path.read_bytes() for path in paths}
    job = c.build_job(root, 4, 'W', [1, 2, 3], instruction=instruction)
    work = c.workspace(root, 4)
    task = c.read(work/'science-task.json')
    context = task['decision_context']
    assert context['source_original'] == 'inputs/parents/003/conclusion.json'
    assert context['source_role'] == 'A' and context['source_action'] == 'continue_w'
    assert context['original_instruction'] == instruction == job['instruction']
    authority = context['authority']
    for bound in ('Original user/task constraints', 'frozen dispatch/catalog',
            'focused evidence gaps', 'execution permissions remain binding'):
        assert bound in authority
    assert 'H offers revisable hypotheses/directions' in authority
    assert 'A independently assesses evidence/claims' in authority
    assert 'Concrete execution methods are proposals' in authority
    assert 'W owns research direction/design/implementation/adaptation' in authority
    assert 'may revise mechanisms' in authority
    assert 'seal before a new W freezes revisions' in authority
    for obsolete in ('assigned H scope', 'H owns high-level methods/directions'):
        assert obsolete not in authority
    for scope in ('original user/task constraints', 'frozen dispatch/catalog',
            'Propose revisions', 'seal before a new W freezes'):
        assert scope in task['question']
    assert 'assigned H directions' not in task['question']
    assert 'explore toward unmet goals' in task['question']
    assert 'necessary validation' not in task['question']
    assert job['search']['focus'] == dict(mechanism_ref='h001.m1', action='improve')
    assert search.launch_reason(root, job) is None
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['improve']
    assert job['scientific_work_unit'] in task['question']
    assert 'focused scientific mechanism' in task['question']
    assert job['worker_fragment_policy'] == dict(context='same_session')
    assert 'Continuation:same_session;' in task['stage_response']
    for route in ('continue=same W', 'handoff=', 'revise_h=H', 'review=A evidence', 'decision=request A assessment'):
        assert route in task['stage_response']
    assert 'new W' in task['stage_response']
    assert task['stage_response'].endswith('needs terminal/session+known usage+authorization.')
    assert 'Handoff freezes new/revised mechanisms without A' in job['native_role_instruction']
    assert 'Batches/progress/windows alone do not require A' in task['role_contract']
    # A's complete optional proposal is the same bound original, not a rewritten
    # four-comparison quota or a substituted scientific question.
    assert c.read(record/'science-output.json')['worker_request']['return_point'] == 'Return to A after the four comparisons.'
    assert any(row['path'] == context['source_original'] for row in job['direct_originals'])
    assert task['global']['objective'] == 'inputs/global/objective.md'
    objective = next(row for row in job['global_materials'] if row['path'] == task['global']['objective'])
    assert (root/objective['source']).read_bytes() == (tmp_path/'public/objective.md').read_bytes()
    assert b'required five comparisons' in (root/objective['source']).read_bytes()
    for intent in ({}, dict(continuation_kind='fragment')):
        prompt, _, schema = native.render_scientific_request(job, task, intent, backend_name='codex',
            instruction_transport='prompt')
        assert prompt.count(guidance.STAGE_PLANNING['W']) == 1
        assert 'W owns sustained research direction/design/adaptation' in prompt
        assert 'H for hypotheses' in prompt and 'A for a decision-changing judgment' in prompt
        assert 'from observations and residuals' in prompt
        assert 'Revisit breadth for conflict/stall' in prompt
        assert 'stage_intent:continue|handoff|revise_h|review|decision' in prompt
        assert schema['properties']['stage_intent']['enum'] == ['continue', 'handoff', 'revise_h', 'review', 'decision']
        assert 'next_action' not in schema['properties']
    frozen = {path: path.read_bytes() for path in (root/'jobs/004.json', work/'science-task.json')}
    monkeypatch.setitem(guidance.WORK_UNITS, 'improve', 'Future guidance must not replace this accepted work unit.')
    assert c.build_job(root, 4, 'W', [1, 2, 3], instruction=instruction) == job
    assert all(path.read_bytes() == raw for path, raw in {**originals, **frozen}.items())


@pytest.mark.parametrize('instruction,proposal', [
    pytest.param(
        'Pressure-test the retained OMP and robust diagnostic estimators under changed perturbation '
        'conditions. State the comparison limits, then develop and test useful improvements within '
        'the existing mechanisms toward the unmet original goal before independent A assessment.',
        dict(proposed_grid='2x2: retained estimators by changed perturbation conditions',
            followups='Use observed failures to choose useful implementation and experiment followups.',
            return_point='Return for independent A judgment after coherent in-scope work or an actual limit.'),
        id='r12-retained-estimator-pressure-test'),
    pytest.param(
        'Decompose the retained OMP residual error into support recovery and coefficient estimation '
        'effects. Use that diagnostic to choose and test useful solver improvements within the '
        'existing mechanism toward the unmet original goal before independent A assessment.',
        dict(proposed_diagnostic='Separate support recovery and coefficient estimation error.',
            followups='Develop and compare useful solver changes informed by the residual diagnostic.',
            return_point='Return for independent A judgment after coherent in-scope work or an actual limit.'),
        id='r12-residual-error-diagnostic'),
])
def test_existing_mechanism_pressure_tests_and_diagnostics_route_directly_to_batched_w(
        tmp_path, instruction, proposal):
    # Minimized authored r12 counterexamples exercise delivery and routing, not
    # whether an LLM will choose this advice or whether fixture science succeeds.
    h_choice = dict(scope='Improve reconstruction within the retained sparse and robust estimation mechanisms.',
        commitments=[
            dict(label='Sparse reconstruction with OMP', existing_ref=None,
                minimal_test='Compare support recovery and coefficient estimation under public perturbations.'),
            dict(label='Robust diagnostic estimation', existing_ref=None,
                minimal_test='Compare robustness under the same public perturbations.')],
        focus=dict(index=1, action='improve'))
    root, instruction, record = comparison_recipe_stage(tmp_path, 'improve',
        instruction=instruction, worker_request=proposal, h_choice=h_choice)
    original_paths = [root / 'jobs/001.json', c.host_record(c.workspace(root, 1)) / 'science-output.json',
        root / 'jobs/003.json', record / 'science-output.json', record / 'result-identity.json']
    originals = {path: path.read_bytes() for path in original_paths}
    a_job = c.read(root / 'jobs/003.json')
    payload = c.validate(c.workspace(root, 3))
    assert payload['next_action'] == 'continue_w'
    assert c.read(record / 'result-identity.json')['control_outcome']['decision_status'] == 'applied'
    role, parents, routed_instruction, evidence_return, current_h, current_w = controller._route(
        root, 3, 'A', payload, a_job, 1, 2)
    assert (role, parents, routed_instruction, evidence_return, current_h, current_w) == (
        'W', [1, 2, 3], instruction, None, 1, 2)
    job = c.build_job(root, 4, role, parents, instruction=routed_instruction,
        evidence_return=evidence_return)
    task = c.read(c.workspace(root, 4) / 'science-task.json')
    assert job['role'] == 'W' and job['search']['focus'] == dict(mechanism_ref='h001.m1', action='improve')
    assert search.launch_reason(root, job) is None
    state = search.project(root, 4)
    assert set(state['mechanisms']) == {'h001.m1', 'h001.m2'}
    assert all(row['original_step'] == row['source_step'] == 1 for row in state['mechanisms'].values())
    assert c.read(c.host_record(c.workspace(root, 1)) / 'science-output.json')['search_plan'] == h_choice
    context = task['decision_context']
    assert context['source_action'] == 'continue_w'
    assert context['source_original'] == 'inputs/parents/003/conclusion.json'
    assert context['original_instruction'] == instruction == job['instruction']
    assert 'Concrete execution methods are proposals' in context['authority']
    assert payload['worker_request'] == proposal
    assert all(value not in task['question'] for value in proposal.values())
    assert '2x2' not in task['question']  # No proposal-derived execution quota.
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['improve']
    prompt, _, _ = native.render_scientific_request(job, task, {}, backend_name='codex',
        instruction_transport='prompt')
    assert prompt.count(guidance.STAGE_PLANNING['W']) == 1
    for retained in ('runnable candidates/helpers', 'negatives', 'exact originals'):
        assert retained in prompt
    assert all(path.read_bytes() == raw for path, raw in originals.items())


@pytest.mark.parametrize('action', ['screen', 'validate', 'repair'])
def test_a_bounded_work_proposals_retain_w_research_focus(tmp_path, action):
    root, instruction, record = comparison_recipe_stage(tmp_path, action)
    job = c.build_job(root, 4, 'W', [1, 2, 3], instruction=instruction)
    task = c.read(c.workspace(root, 4)/'science-task.json')
    # A's proposal remains authored context. It cannot turn W's retained
    # improve scope into screening, validation or repair authority.
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert job['scientific_work_unit']==guidance.WORK_UNITS['improve']
    assert guidance.WORK_UNITS['improve'] in task['question']
    assert 'original user/task constraints' in task['question']
    assert 'frozen dispatch/catalog' in task['question']
    assert search.launch_reason(root,job) is None
    original=c.read(record/'science-output.json')
    assert original['search_review']['next_focus']['action']==action
    assert original['decision_request']['question']=='Which related change resolves the target-condition gap?'
    assert original['decision_request']['change_criterion']=='Retained fair controls show that the target-condition gap is resolved.'
    assert task['decision_context']['source_original']=='inputs/parents/003/conclusion.json'
    assert task['decision_context']['original_instruction']==instruction
    assert 'return-after-comparison' not in task['decision_context']['authority']
    assert 'related observation-driven follow-ups' not in task['decision_context']['authority']
    assert guidance.WORK_UNITS[action] not in task['question']


@pytest.mark.parametrize('action,question_start', [
    ('improve', 'Own sustained experiment design'),
    ('validate', 'Own sustained experiment design'),
])
def test_a_work_intent_survives_real_science_projection_and_native_rendering(tmp_path, action, question_start):
    root, instruction, record = comparison_recipe_stage(tmp_path, action)
    job = c.build_job(root, 4, 'W', [1, 2, 3], instruction=instruction)
    task = c.science_view(job, root)
    assert task['question'].startswith(question_start)
    assert task['decision_context']['original_instruction'] == instruction == job['instruction']
    assert task['decision_context']['source_original'] == 'inputs/parents/003/conclusion.json'
    assert c.read(record/'science-output.json')['search_review']['next_focus']['action'] == action
    assert search.launch_reason(root, job) is None
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action='improve')
    assert 'observations, residuals and local relations' in task['question']
    assert guidance.WORK_UNITS['improve'] in task['question']
    assert guidance.WORK_UNITS['validate'] not in task['question']
    for intent in ({}, dict(continuation_kind='fragment')):
        prompt, _, schema = native.render_scientific_request(job, task, intent,
            backend_name='codex', instruction_transport='prompt')
        assert prompt.count(guidance.STAGE_PLANNING['W']) == 1
        assert job['scientific_work_unit'] in task['question']
        assert schema['properties']['stage_intent']['enum'] == ['continue', 'handoff', 'revise_h', 'review', 'decision']
        assert 'next_action' not in schema['properties']


@pytest.mark.parametrize('special', [dict(purpose='closeout'), dict(final_report=True),
    dict(final_only=True), dict(correction_request=dict(reason='Retain exact correction scope.'))])
def test_special_worker_questions_keep_the_original_instruction(tmp_path, special):
    root, instruction, _ = comparison_recipe_stage(tmp_path, 'improve')
    job = c.build_job(root, 4, 'W', [1, 2, 3], instruction=instruction)
    view = c._science_view(dict(job, **special), None, parent_sources={}, policy={}, qualified_materials=[])
    assert view['question'] == instruction + '\nWork unit: ' + job['scientific_work_unit']


def test_initial_h_improve_instruction_is_retained_with_local_worker_autonomy(tmp_path):
    root = prepare(tmp_path, guidance.DEFAULT, search_policy=search.DEFAULT_POLICY)
    instruction = 'Study residual-conditioned solver changes within the retained sparse estimation mechanism.'
    finish(root, 1, 'H', [], action='experiment', text=instruction, search_choice=dict(
        scope='Improve sparse estimation', commitments=[dict(label='Sparse estimation', existing_ref=None)],
        focus=dict(index=1, action='improve')))
    job = c.build_job(root, 2, 'W', [1], instruction=instruction)
    view = c.science_view(job, root)
    assert view['question'].startswith(instruction + '\n')
    assert job['instruction'] == instruction
    assert 'decision_context' not in view
    assert job['search']['focus'] == dict(mechanism_ref='h001.m1', action='improve')
    for scope in ('original user/task constraints', 'frozen dispatch/catalog',
            'Propose revisions', 'seal before a new W freezes'):
        assert scope in view['question']
    assert 'assigned H scope' not in view['question']
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['improve']


def test_a_revision_request_retains_exact_h_input_without_frozen_h_authority(tmp_path):
    root, instruction, _ = comparison_recipe_stage(tmp_path, 'improve', next_action='revise_h')
    job = c.build_job(root, 4, 'H', [1, 2, 3], instruction=instruction)
    task = c.read(c.workspace(root, 4)/'science-task.json')
    assert task['decision_context']['source_action'] == 'revise_h'
    assert task['decision_context']['original_instruction'] == instruction
    assert 'H offers revisable hypotheses/directions' in task['decision_context']['authority']
    assert 'Original user/task constraints' in task['decision_context']['authority']
    assert 'frozen dispatch/catalog' in task['decision_context']['authority']
    assert 'H owns high-level methods/directions' not in task['decision_context']['authority']
    assert 'assigned H scope' not in task['decision_context']['authority']
    assert 'related observation-driven follow-ups' not in task['decision_context']['authority']
    assert 'Revise high-level scientific methods/directions' in task['question']
    assert 'direct W observations/counterevidence' in task['question']
    assert 'provisional H judgments' in task['question']
    assert guidance.WORK_UNITS['improve'] not in task['question']


def test_initial_screen_keeps_comparison_scope_without_forcing_exhaustion(tmp_path):
    root = prepare(tmp_path, guidance.DEFAULT, search_policy=search.DEFAULT_POLICY)
    finish(root, 1, 'H', [], action='experiment', search_choice=dict(
        scope='Compare mechanisms before selecting a main line',
        commitments=[dict(label='Initial mechanism', minimal_test='Cheap discriminating comparison', existing_ref=None),
            dict(label='Rival mechanism', minimal_test='Different prediction under the same conditions', existing_ref=None)],
        focus=dict(index=1, action='screen')))
    job = c.build_job(root, 2, 'W', [1])
    task = c.read(c.workspace(root, 2)/'science-task.json')
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['screen']
    assert set(search.project(root, 2)['mechanisms']) == {'h001.m1', 'h001.m2'}
    assert job['search']['focus']==dict(mechanism_ref='h001.m1',action='screen')
    assert {row['mechanism_ref'] for row in job['search']['coverage_obligation']}=={'h001.m1','h001.m2'}
    for concept in ('fair controls', 'Reuse adequate evidence', 'observations and obstacles'):
        assert concept in task['question']
    assert 'Calls alone do not establish screening' in task['question']
    assert 'before deepening any' not in task['question']
    assert guidance.WORK_UNITS['improve'] not in task['question']
    assert 'decision_context' not in task
    assert 'Handoff freezes new/revised mechanisms without A' in job['native_role_instruction']
    assert 'Batches/progress/windows alone do not require A' in task['role_contract']
    for route in ('continue=same W', 'handoff=', 'revise_h=H', 'review=A evidence', 'decision=request A assessment'):
        assert route in task['stage_response']
    assert 'new W' in task['stage_response']
