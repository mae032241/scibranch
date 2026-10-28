"""Scientific advice stays frozen and reaches the native role instruction."""
import json

import pytest

from scidiscovery.research_loop import contracts as c, controller, decision, guidance, native
from test_history_handoff import finish

COV_USAGE = (' Cov public tools: exp run [--script FILE] --new-plan or --parent-plan NAME; '
             'exp lineage-status. Other exp commands keep their public meanings.')


def prepare(tmp_path, advice, *, marker=1):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep data separation and the required five comparisons; resolve the target-condition gap.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
                       max_seconds=3600, max_steps=8, model='fixture', reasoning_effort='high',
                       search_policy=None, role_guidance=advice, handoff_authority_version=marker)
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
    assert 'goal/evaluation-version judgment and counterevidence' in guidance.DEFAULT['A']
    assert 'No incumbent/unknown-optimality veto' in guidance.DEFAULT['A']
    assert "calls/audits aren't goal proof" in guidance.DEFAULT['A']
    assert 'Honor checks' in guidance.DEFAULT['W']
    assert 'Initial/grant calls protect screening opportunities, not trial totals' in guidance.DEFAULT['W']
    assert 'W owns methods/grids' in guidance.DEFAULT['H']
    assert 'differing predictions, the discriminating signal, priority and reopening condition' in guidance.DEFAULT['H']
    assert 'compare 2-3 mechanisms' in guidance.DEFAULT['H'] and 'this is no quota' in guidance.DEFAULT['H']
    assert 'Preserve constraints, limits and omitted commitments' in guidance.DEFAULT['H']


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
