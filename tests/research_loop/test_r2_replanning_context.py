"""Synthetic planning advice preserves scientific choices and FINAL limits."""
import copy

import pytest

from scidiscovery.research_loop import contracts as c, guidance, native, search
from scidiscovery.research_loop.scientific_intent import resolve_intent


def declared_questions():
    return dict(mechanisms={
        'h001.m3': dict(status='deferred', label='Deferred alternative',
            minimal_test='Which matched observation distinguishes the alternative?'),
        'h001.m4': dict(status='screened', label='Retained primary',
            minimal_test='Does the result transfer to the target condition?')},
        authorized={'h001.m3', 'h001.m4'}, focus=dict(mechanism_ref='h001.m4', action='screen'),
        order=dict(remaining=[dict(mechanism_ref='h001.m3', planned_step=1),
            dict(mechanism_ref='h001.m4', planned_step=1)], seen_w=set(), allocation=None,
            discriminating_work=[dict(work_ref='A001-D001', status='deferred',
                mechanism_refs=['h001.m3'], question='Which target observation separates the alternatives?',
                next_action='extract', executions=[], updates=[])]))


def test_processed_initial_queue_keeps_temporarily_deferred_scientific_questions_visible():
    state = declared_questions()
    assessed = dict(identity=(2, 'synthetic-original'), mechanism_ref='h001.m4', allocation=None,
        breadth=True, mechanism_refs=['h001.m3', 'h001.m4'])
    review = dict(dispositions=[dict(mechanism_ref=ref, status=item['status'])
        for ref, item in state['mechanisms'].items()],
        next_focus=dict(mechanism_ref='h001.m4', action='improve'))
    result = resolve_intent(state, dict(role='A', step=3),
        dict(next_action='continue_w', route_reason='The initial queue is processed; the target question remains.',
            search_review=review), assessed=assessed)
    assert not result['unfinished_plan']
    current = copy.deepcopy(state)
    current['order'].update(remaining=result['unfinished_plan'],
        discriminating_work=result['discriminating_work'])
    before = copy.deepcopy(current)
    view = search.planning_view(current)
    assert view['initial_queue'] == dict(remaining_count=0, processed=True)
    assert view['deferred_mechanisms'] == [dict(mechanism_ref='h001.m3',
        question='Which matched observation distinguishes the alternative?')]
    assert view['declared_work'] == [dict(work_ref='A001-D001', status='deferred',
        question='Which target observation separates the alternatives?')]
    assert 'temporarily set aside' in view['scope']
    job = dict(role='A', history=dict(view=dict(recent=[],
        mechanisms=dict(unfinished_plan=dict(remaining=[], **view)))))
    assert c._role_lineage(job)['mechanisms']['unfinished_plan']['declared_work'] == view['declared_work']
    assert current == before


def test_computed_question_view_is_bounded_without_hiding_omitted_work():
    import json
    state = declared_questions()
    row = state['order']['discriminating_work'][0]
    state['order']['discriminating_work'] = [dict(row, work_ref=f'A{step:03d}-D001', question='问' * 400)
        for step in range(1, 30)]
    view = search.planning_view(state)
    assert len(view['declared_work']) == 4
    assert view['declared_work_omitted_count'] == 25
    assert len(json.dumps(view, ensure_ascii=False).encode()) <= search.MAX_VIEW_BYTES


def test_normal_bind_and_worker_projection_trim_question_excerpts_but_keep_queue_meaning(tmp_path):
    import json
    from pathlib import Path
    from scidiscovery.research_loop import accepted_budget, accepted_prefix, accepted_search, controller, history, lineage
    from test_history_handoff import finish

    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Preserve deferred questions when presenting a processed initial queue.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], model='fixture',
        reasoning_effort='high', max_tokens=100_000, max_seconds=600, max_steps=8)
    questions = [f'Question {number}: ' + 'Which matched target condition changes the scientific choice? ' * 12
        for number in range(1, 5)]
    commitments = [dict(label=f'Alternative {number}', minimal_test=question)
        for number, question in enumerate(questions, 1)]
    commitments.append(dict(label='Primary ' + '主' * 170,
        minimal_test='Compare the primary prediction under the retained target condition.'))
    finish(root, 1, 'H', [], action='experiment',
        search_choice=dict(commitments=commitments, focus=dict(index=5, action='improve')))
    original_job = c.build_job(root, 2, 'W', [1])
    head = copy.deepcopy(accepted_budget.read(root))
    summary = head['search']
    # Supply presentation facts at the existing accepted-state producer. The
    # immutable H source retains every full question; no scientific verdict or
    # execution is claimed by these synthetic deferred work rows.
    for number, question in enumerate(questions, 1):
        summary['table']['mechanisms'][f'h001.m{number}']['status'] = 'deferred'
        ref = f'A{number:03d}-D001'
        summary['current_facts'][accepted_search.key('work', ref)] = dict(
            work_ref=ref, status='deferred', question=question)
    summary['table']['remaining'] = []
    summary.update(remaining_count=0, pending_count=1, work_count=4)
    base = c.settings(root)
    binding, _ = lineage.bind_from_facts(root, 2, 'W', c.HANDOFF_PROTOCOL,
        base=base, head=head, policy=base)
    bound = binding['view']['mechanisms']['unfinished_plan']
    omitted = lambda order: sum(order[field + '_omitted_count']
        for field in ('deferred_mechanisms', 'declared_work'))
    assert omitted(bound) > 0
    job = dict(original_job, history=binding)
    rendered = c._role_lineage(job)
    order = rendered['mechanisms']['unfinished_plan']
    assert omitted(order) > omitted(bound)  # W adds its actual assigned overview.
    assert order['initial_queue'] == dict(remaining_count=0, processed=True)
    assert 'not scientific resolution' in order['scope']
    assert 'temporarily set aside' in order['scope']
    assert order['index'] == binding['view']['indexes']['nodes']
    assert len((json.dumps(rendered, ensure_ascii=False, indent=2) + '\n').encode()) <= history.MAX_BRIEF_BYTES
    assert sum(len(order[field]) + order[field + '_omitted_count']
        for field in ('deferred_mechanisms', 'declared_work')) == 8
    index = accepted_prefix.visibility(root, binding['prefix']) / Path(order['index']).relative_to('inputs/history')
    node = c.read(index)
    original = c.read(index.parent / node['science'])
    assert [row['minimal_test'] for row in original['search_plan']['commitments'][:4]] == questions


@pytest.mark.parametrize('action', ['continue_w', 'revise_h', 'finish'])
def test_method_reconsideration_does_not_force_or_block_a_choice(action):
    state = declared_questions()
    before = copy.deepcopy(state)
    payload = dict(next_action=action, route_reason='Reconsider the method after low information return.',
        search_review=dict(next_focus=dict(mechanism_ref='h001.m4', action='improve')
            if action == 'continue_w' else None))
    result = resolve_intent(state, dict(role='A', step=3), payload)
    assert not result['diagnostics']
    assert result['focus'] == (dict(mechanism_ref='h001.m4', action='improve')
        if action == 'continue_w' else None)
    assert state == before
    assert c.actions('A') == (*c.ACTIONS['A'], 'audit')


def test_h_may_replan_a_method_while_retaining_the_same_mechanism():
    state = declared_questions()
    result = resolve_intent(state, dict(role='H', step=4),
        dict(next_action='experiment', route_reason='Change the method; the mechanism remains viable.',
            search_plan=dict(commitments=[dict(existing_ref='h001.m4', label='Retained primary',
                minimal_test='Use a revised matched control to answer the retained question.')],
                focus=dict(index=1, action='improve'))))
    assert not result['diagnostics']
    assert result['focus'] == dict(mechanism_ref='h001.m4', action='improve')


@pytest.mark.parametrize('backend_name', ['codex', 'protocol'])
def test_method_replanning_advice_preserves_protected_final_boundary(backend_name):
    job = dict(role='A', handoff_protocol=c.HANDOFF_PROTOCOL, search={})
    task = c.response_instructions(job)
    prompt, instruction, _ = native.render_scientific_request(job, task, {},
        backend_name=backend_name, instruction_transport='developer')
    assert 'revise_h: methods/bottlenecks without refuting H' in prompt
    assert 'Independent W phase/major-choice/stall/delivery review' in prompt
    assert 'continue_w/revise_h/extract allowed' in prompt
    assert instruction.count(c.AUTHOR_EVIDENCE_GUIDANCE) == 1
    final, _, _ = native.render_scientific_request(job, task, {},
        backend_name=backend_name, instruction_transport='developer', final_return=True)
    assert guidance.STAGE_PLANNING['A'] not in final
    assert 'Protected FINAL: conclude now; no new role/audit/experiment.' in final
    final_only = dict(job, final_only=True)
    _, _, schema = native.render_scientific_request(final_only, c.response_instructions(final_only), {},
        backend_name=backend_name, instruction_transport='developer')
    assert schema['properties']['next_action']['enum'] == ['finish']
