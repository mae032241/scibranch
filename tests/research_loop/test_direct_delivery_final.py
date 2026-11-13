"""Deterministic native fact publisher for frozen configuration checks."""

from scidiscovery.research_loop import accepted_budget, budget, contracts as c, native
from test_history_handoff import selection_explanation




def publish(root, job, *, action='finish', tokens=10, recommendation=None):
    """Seal deterministic native facts, including the original cleanup evidence."""
    step = job['step']
    record = c.host_record(c.workspace(root, step))
    intent = dict(step=step, task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json'),
        budget_slice=budget.accept_node(root, job))
    c.write(record/'native-intent.json', intent)
    accepted_budget.accept_native(root, step, record, intent)
    payload = dict(summary='Retained bounded fixture assessment',
        scientific_result='No experiment or model was run; retain the missing evidence.',
        limitations=['Deterministic control fixture; no scientific qualification'],
        implementation_selection=None)
    if job['role'] == 'H':
        payload['search_plan'] = None
    if job['role'] == 'A':
        payload.update(candidate_recommendation=recommendation,
            selection_explanation=selection_explanation() if recommendation is not None else None,
            search_review=None, progress_assessment=None, decision_request=None,
            portfolio_review=None)
    payload.update(next_action=action, route_reason='Retain the original bounded conclusion')
    meter = dict(input_tokens=tokens-3, cached_input_tokens=2, output_tokens=3)
    observation = dict(coverage='terminal', source='turn.completed', tokens=meter)
    c.write(record/'science-output.json', payload)
    c.write(record/'usage.json', meter)
    c.write(record/'usage-observation.json', observation)
    outcome = dict(returncode=0, stop_reason=None, native_started=False, cleanup='observed_group_cleaned',
        metering=observation, usage=native.usage(root),
        raw_ref=c.file_ref(record/'science-output.json'))
    accepted = budget.final_a_return(root, job, intent)
    if accepted is not None:
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    c.write(record/'native-outcome.json', outcome)
    c.seal_result(root, step)
    return record
