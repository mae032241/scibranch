"""Current W decisions and ordinary-A authority boundaries; no scientific runs."""
import pytest

from scidiscovery.research_loop import (
    accepted_budget, budget, contracts as c, controller, guidance, native, supervision,
)
from scidiscovery.research_loop.output_repair import OutputRepairRequired
from test_history_handoff import finish, fixture_final_coverage, fixture_portfolio_review, selection_explanation

DEFAULT_STRATEGY = object()


def instance(tmp_path, *, strategy=3, existing=None, guidance_value=None, validate_only=False):
    public = tmp_path / 'public'
    public.mkdir(parents=True, exist_ok=True)
    objective = public / 'goal.md'
    objective.write_text('Compare the exact retained observations and preserve unmet objectives.')
    root = tmp_path / 'instance'
    options = {} if guidance_value is None else dict(role_guidance=guidance_value)
    if strategy is not DEFAULT_STRATEGY:
        options['research_strategy_version'] = strategy
    settings = controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000,
        max_seconds=3600, max_steps=16, model='fixture', reasoning_effort='high',
        search_policy=None, _existing_settings=existing,
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=60) for role in 'HWAE'},
        validate_only=validate_only, **options)
    return settings if validate_only else root


def publish(root, job, *, action='continue_w', request=None, science='The retained depth remains productive.',
            supervision_result=None, omit_request=False, candidate=None):
    record = c.host_record(c.workspace(root, job['step']))
    payload = dict(summary='Bounded assessment of existing originals', scientific_result=science,
        limitations=['Deterministic fixture; scientific qualification remains unasserted.'],
        implementation_selection=None, search_review=None, progress_assessment=None,
        candidate_recommendation=candidate,
        selection_explanation=selection_explanation() if candidate else None,
        decision_request=request, portfolio_review=None,
        next_action=action, route_reason='Retain the original unfinished W question.')
    if omit_request:
        payload.pop('decision_request')
    if job.get('supervision_request') is not None:
        payload['supervision_result'] = supervision_result
    fixture_portfolio_review(root, job, payload)
    fixture_final_coverage(root, job, payload)
    intent = dict(step=job['step'], task_ref=c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'),
                  budget_slice=budget.accept_node(root, job))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(root, job['step'], record, intent)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    observation = dict(coverage='terminal', source='turn.completed', tokens=meter)
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', meter)
    c.write(record / 'usage-observation.json', observation)
    outcome = dict(returncode=0, stop_reason=None, native_started=False,
        cleanup='observed_group_cleaned', metering=observation, usage=native.usage(root),
        raw_ref=c.file_ref(record / 'science-output.json'))
    accepted = budget.final_a_return(root, job, intent)
    if accepted is not None:
        outcome['final_a_completion'] = budget.final_a_receipt(accepted, intent, meter, outcome['usage'])
    c.write(record / 'native-outcome.json', outcome)
    c.seal_result(root, job['step'])
    return payload


def chain(root):
    finish(root, 1, 'H', [], action='experiment')
    finish(root, 2, 'W', [1], text='A retained controlled observation with unfinished depth.')


def test_actual_action_admission_rejects_marked_ordinary_a(tmp_path, monkeypatch):
    from scidiscovery.research_loop import configuration_updates, protocol
    policy = dict(research_strategy_version=3, delivery_policy=dict(adapter={'fixture': True}))
    monkeypatch.setattr(protocol, 'require_current', lambda *args: None)
    monkeypatch.setattr(configuration_updates, 'for_job', lambda *args: policy)
    monkeypatch.setattr(budget, 'closeout_state', lambda *args: dict(begin=dict(mode='model')))
    monkeypatch.setattr(budget, 'execution_blocked', lambda *args, **kwargs: False)
    monkeypatch.setattr(native, 'usage', lambda *args: {})
    monkeypatch.setattr(budget, 'snapshot', lambda *args: {})
    monkeypatch.setattr(budget, 'require_final_a_delivery_eligibility', lambda *args: None)
    c.write(tmp_path / 'jobs/003.json', dict(step=3, role='A', research_strategy_version=3, advisory_scope_version=1))
    with pytest.raises(budget.NodeBudgetUnavailable, match='final_author_required'):
        budget.accept_action(tmp_path, 'submit', 'fixture', dict(step=3), {}, 1)



def test_defaults_freeze_current_worker_and_advisory_identity(tmp_path):
    root=instance(tmp_path,strategy=DEFAULT_STRATEGY)
    selected=c.settings(root)
    assert selected['research_strategy_version']==3 and selected['advisory_scope_version']==1
    assert selected.get('supervision_policy') is None
    job=c.build_job(root,1,'W',[])
    before=(root/'jobs/001.json').read_bytes()
    assert c.build_job(root,1,'W',[])==job
    assert (root/'jobs/001.json').read_bytes()==before


@pytest.mark.parametrize('version',[None,0,1,2,4,True,'3'])
def test_unsupported_strategy_has_no_preparation_effect(tmp_path,version):
    from scidiscovery.research_loop.protocol import UnsupportedProtocol
    with pytest.raises(UnsupportedProtocol):instance(tmp_path,strategy=version)
    assert not (tmp_path/'instance').exists()


@pytest.mark.parametrize('action',['wait','finish','deliver','revise_h','extract','formal_review'])
def test_explicit_ordinary_a_action_is_rejected_without_rewriting_original(tmp_path,action):
    root=instance(tmp_path)
    job=c.build_job(root,1,'A',[])
    original=(root/'jobs/001.json').read_bytes()
    with pytest.raises(OutputRepairRequired):publish(root,job,action=action)
    record=c.host_record(c.workspace(root,1))
    assert c.read(record/'science-output.json')['next_action']==action
    assert not (record/'result-identity.json').exists()
    assert not (root/'jobs/002.json').exists()
    assert (root/'jobs/001.json').read_bytes()==original


def test_worker_decision_requests_exact_pre_delivery_assessment(tmp_path):
    selected=controller._route(tmp_path,2,'W',dict(stage_intent='decision',
        route_reason='Are the original requirements satisfied?'),
        dict(role='W',research_strategy_version=3,advisory_scope_version=1),1,None)
    assert selected.role=='A' and selected.parents==(1,2)
    assert selected.evidence_return==dict(kind='delivery_review',step=2,corrections_used=0)
    assert selected.current_w==2 and 'original requirements' in selected.instruction


def test_ordinary_a_route_cannot_dispatch_work_even_with_authored_terminal_text(tmp_path):
    with pytest.raises(ValueError,match='Ordinary advisory A'):
        controller._route(tmp_path,2,'A',dict(next_action='deliver'),
            dict(role='A',research_strategy_version=3,advisory_scope_version=1),None,1)
