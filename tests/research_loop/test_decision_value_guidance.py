"""Scientific advice stays frozen and reaches the native role instruction."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, controller, decision, guidance, native, search
from test_history_handoff import finish

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
        expected = guidance.ADVISORY_WORKER_FIRST[role] if advice is None else advice[role]
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
    assert guidance.DEFAULT is guidance.ADVISORY_WORKER_FIRST
    assert 'Ordinary A gives fallible advice to W' in guidance.DEFAULT['A']
    assert 'W judges the concern' in guidance.DEFAULT['A']
    assert 'Own research/methods' in guidance.DEFAULT['W']
    assert 'No unknown-call replay' in guidance.DEFAULT['W']
    assert 'Answer W\'s concrete question' in guidance.DEFAULT['H']
    assert 'Preserve original constraints' in guidance.DEFAULT['H']


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


def test_prepared_literal_and_current_fallback_are_not_rewritten(tmp_path, monkeypatch):
    custom = {**guidance.DEFAULT, 'A': 'Frozen global assessment: compare existing evidence and retain limitations.'}
    expected = custom['A']
    root = prepare(tmp_path, custom)
    finish(root, 1, 'H', [], action='analyze')
    job, work, record = finish(root, 2, 'A', [1])
    paths = [root / 'settings.json', root / 'jobs/002.json', work / 'science-task.json',
             record / 'science-output.json', record / 'science-result.schema.json', record / 'result-identity.json']
    originals = {path: path.read_bytes() for path in paths}
    assert job['native_role_instruction'] == expected
    monkeypatch.setitem(guidance.DEFAULT, 'A', 'Changed future guidance must not replace a prepared literal')
    assert c.build_job(root, 2, 'A', [1]) == job
    assert c.validate(work)['next_action'] == 'continue_w'
    assert job['handoff_authority_version'] == 1
    assert job['native_role_instruction'] == expected
    view = c.science_view(job, root)
    prompt, instruction, _ = native.render_scientific_request(job, view, {}, backend_name='codex',
        instruction_transport='prompt')
    assert instruction.startswith(expected+'\n') and prompt.startswith(expected+'\n')
    assert 'role_guidance' not in view and 'role_instruction' not in view
    assert all(path.read_bytes() == raw for path, raw in originals.items())


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
    assert 'Own research and adapt in this W session' in view['question']
    assert 'Declare/revise stable mechanisms and experiment_question' in view['question']
    assert 'assigned H scope' not in view['question']
    assert job['scientific_work_unit'] == guidance.WORK_UNITS['improve']


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
    assert 'Own research/methods' in job['native_role_instruction']
    assert 'W chooses E' in task['role_contract']
    for route in ('continue=W', 'handoff=new W', 'revise_h=H advice', 'review=A advice', 'decision=pre-delivery Final A'):
        assert route in task['stage_response']
    assert 'new W' in task['stage_response']
