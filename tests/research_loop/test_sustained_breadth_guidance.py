"""Sustained scientific breadth advice and existing exact routing; no model calls.

Authored findings and inert execution prove delivery and identity contracts only,
not the scientific effectiveness of the advice or the fixture mechanisms.
"""
import json
import os
from pathlib import Path

import pytest

from scidiscovery.research_loop import backends, contracts as c, controller, guidance, native, search
from execution_fixtures import execution_registry
from test_breadth_science import worker
from test_history_handoff import finish
from test_result_schema import rendered_contract


def prepare(tmp_path, advice):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Resolve the original scientific goal under the same public evaluation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-call-a-model'], model='fixture',
        reasoning_effort='high', max_tokens=100000, max_seconds=600, max_steps=8,
        role_guidance=advice, search_policy=search.DEFAULT_POLICY,research_strategy_version=3)
    return root


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
    assert 'Goals remain.' not in prompt
    assert 'Follow-up targets advise' not in prompt
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
    job = dict(role='W', handoff_protocol=c.HANDOFF_PROTOCOL, research_strategy_version=3, advisory_scope_version=1, search={},
        native_role_instruction=literal, scientific_work_unit=unit,
        worker_fragment_policy=dict(version=2,context='same_session'))
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
    job = dict(role=role, handoff_protocol=c.HANDOFF_PROTOCOL, research_strategy_version=3, advisory_scope_version=1, search={},
        scientific_response_fields=['portfolio_review', 'final_coverage'], formal_review_version=1,
        native_role_instruction=guidance.ADVISORY_WORKER_FIRST[role])
    if role == 'A':
        job.update(portfolio_context={}, final_coverage_mechanisms=[dict(mechanism_ref='h001.m1')])
    else:
        job['worker_fragment_policy']=dict(version=2,context='same_session')
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
