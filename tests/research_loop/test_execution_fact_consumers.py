"""Frozen public execution facts cross real task and fragment boundaries."""
from copy import deepcopy

import pytest

from execution_fixtures import execution_registry, remote_registry
from scidiscovery.research_loop import (accepted_prefix, budget, checkpoint, contracts as c,
    controller, delivery, delivery_review, execution, execution_facts, handoff_state, history, native)
from test_delivery_review_guidance import correction_return, reviewed
from test_history_handoff import finish
from test_shl051_guidance import instance
from test_soft_external_closeout import external_scene
from test_worker_fragments import internal_fragment_instance, fragment_run


def _projected_instance(tmp_path, monkeypatch, *, current_work_views=None):
    """Synthetic fact producer; task admission and publication remain real."""
    root = instance(tmp_path)
    publish = accepted_prefix.prepare_node

    def publish_fixture(root, job, payload, seal, *, inventory):
        if job['role'] == 'W':
            refs = {}
            outputs = []
            if current_work_views is not None:
                alias = 'work/retained-experiments/same-local-id/outputs/result.txt'
                result = c.workspace(root, job['step']) / alias
                result.parent.mkdir(parents=True, exist_ok=True)
                result.write_text(f'Synthetic original for W{job["step"]:03d}')
                refs['same-local-id'] = dict(result=[alias])
                outputs = [dict(name='result.txt')]
            fact = execution_facts.accepted_fact(call_id='same-local-id',
                status='known_failed' if job['step'] == 1 else 'known_success',
                accepted=dict(call_id='same-local-id'), observed=None, outputs=outputs,
                observation_basis='accepted_terminal')
            inventory.execution_fact_view = execution_facts.render(facts=[fact],
                scope=dict(kind='stopped_worker', node=f'W{job["step"]:03d}', attempt=0),
                public_refs=refs, coverage='accepted_scope')
            if current_work_views is not None:
                current_work_views[job['step']] = deepcopy(inventory.execution_fact_view)
        return publish(root, job, payload, seal, inventory=inventory)

    monkeypatch.setattr(accepted_prefix, 'prepare_node', publish_fixture)
    return root


def test_new_worker_task_retains_prior_failures_at_its_frozen_cutoff(tmp_path, monkeypatch):
    root = _projected_instance(tmp_path, monkeypatch)
    finish(root, 1, 'W', [], stage_intent='handoff')

    def forbidden(*args, **kwargs):
        raise AssertionError('Task facts must use the accepted public prefix, without a history scan')

    monkeypatch.setattr(history, 'load_records_from_source_facts', forbidden)
    second = c.build_job(root, 2, 'W', [1])
    task_path = c.workspace(root, 2) / 'science-task.json'
    task = c.read(task_path)
    facts = task['execution_facts']
    assert facts == second['history']['execution_facts']
    assert facts['source_cutoff'] == 1 and facts['node_count'] == 1
    assert facts['status_counts']['known_failed'] == 1
    frozen = task_path.read_bytes()
    finish(root, 2, 'W', [1], stage_intent='decision')
    assert task_path.read_bytes() == frozen
    assert c.build_job(root, 2, 'W', [1]) == second


def test_successor_and_final_use_public_indexes_without_stale_worker_output_aliases(tmp_path, monkeypatch):
    owner_views = {}
    root = _projected_instance(tmp_path, monkeypatch, current_work_views=owner_views)
    finish(root, 1, 'W', [], stage_intent='handoff')
    alias = 'work/retained-experiments/same-local-id/outputs/result.txt'
    assert owner_views[1]['calls'][0]['navigation']['result'] == [alias]
    assert (c.workspace(root, 1) / alias).read_text() == 'Synthetic original for W001'
    successor = c.build_job(root, 2, 'W', [1])

    def assert_context_navigation(job):
        task = c.read(c.workspace(root, job['step']) / 'science-task.json')
        facts = task['execution_facts']
        assert facts['calls']
        for row in facts['calls']:
            assert row['scientific_result'] == 'available'
            assert row['navigation'] == {}
            assert row['navigation_availability'] == 'unavailable'
            assert row['result_access'] == 'not_directly_readable'
        assert not (c.workspace(root, job['step']) / alias).exists()
        assert facts['navigation']
        for locator in facts['navigation']:
            assert locator.startswith('inputs/history/') and locator.endswith('/index.json')
            published = accepted_prefix.visibility(root, accepted_prefix.for_job(job)) / locator.removeprefix('inputs/history/')
            assert c.read(published)['execution_facts'] == facts

    assert_context_navigation(successor)
    finish(root, 2, 'W', [1], stage_intent='decision')
    final = reviewed(root, 3, 2, 0)
    assert_context_navigation(final)
    assert owner_views[2]['calls'][0]['navigation']['result'] == [alias]


def test_normal_final_review_and_one_correction_keep_earlier_failed_calls(tmp_path, monkeypatch):
    root = _projected_instance(tmp_path, monkeypatch)
    finish(root, 1, 'W', [], stage_intent='handoff')
    finish(root, 2, 'W', [1], stage_intent='decision')
    first = reviewed(root, 3, 2, 0)
    task = c.read(c.workspace(root, 3) / 'science-task.json')
    facts = task['execution_facts']
    assert facts['source_cutoff'] == 2 and facts['observed_calls'] == 2
    assert facts['status_counts']['known_failed'] == facts['status_counts']['known_success'] == 1
    assert {(row['node'], row['call_id']) for row in facts['calls']} == {
        ('W001', 'same-local-id'), ('W002', 'same-local-id')}
    assert first['evidence_return']['kind'] == 'delivery_review'
    finish(root, 3, 'A', [2], action='continue_w')
    correction = c.build_job(root, 4, 'W', [3],
        evidence_return=delivery_review.make('delivery_correction', 3, 1))
    correction_task = c.read(c.workspace(root, 4) / 'science-task.json')
    assert correction_task['execution_facts']['status_counts']['known_failed'] == 1
    correction_return(root, correction)
    final = reviewed(root, 5, 4, 1)
    final_task = c.read(c.workspace(root, 5) / 'science-task.json')
    assert final_task['execution_facts']['status_counts']['known_failed'] == 1
    assert final_task['execution_facts']['status_counts']['known_success'] == 2
    assert c.result_schema('A', **c.response_options(final))['properties']['next_action']['enum'] == ['deliver', 'finish']


def test_consultation_and_returning_worker_share_the_accepted_fact_prefix(tmp_path, monkeypatch):
    root = _projected_instance(tmp_path, monkeypatch)
    worker, _, _ = finish(root, 1, 'W', [], stage_intent='review')
    route = controller._route(root, 1, 'W', handoff_state.read_sealed(root, 1), worker, None, 1)
    advice = c.build_job(root, 2, route.role, list(route.parents),
        instruction=route.instruction, evidence_return=route.evidence_return)
    assert advice['purpose'] == 'worker_consultation'
    assert c.read(c.workspace(root, 2) / 'science-task.json')['execution_facts']['status_counts']['known_failed'] == 1
    finish(root, 2, 'A', list(route.parents), action='continue_w')
    route = controller._route(root, 2, 'A', handoff_state.read_sealed(root, 2), advice, None, 1)
    returned = c.build_job(root, 3, route.role, list(route.parents),
        instruction=route.instruction, evidence_return=route.evidence_return)
    assert returned['role'] == 'W'
    task = c.read(c.workspace(root, 3) / 'science-task.json')
    assert task['execution_facts']['source_cutoff'] == 2
    assert task['execution_facts']['status_counts']['known_failed'] == 1


def test_optional_fact_presentation_failure_preserves_task_admission(tmp_path, monkeypatch):
    root = instance(tmp_path)

    def unavailable(*args, **kwargs):
        raise ValueError('Synthetic optional presentation capacity failure')

    monkeypatch.setattr(execution_facts, 'combine', unavailable)
    finish(root, 1, 'W', [], stage_intent='handoff')
    job = c.build_job(root, 2, 'W', [1])
    facts = c.read(c.workspace(root, 2) / 'science-task.json')['execution_facts']
    assert job['role'] == 'W' and facts['diagnostic'] == 'execution_fact_view_unavailable'
    assert facts['coverage'] == 'unknown'


@pytest.mark.parametrize('has_worker', [False, True])
def test_protected_final_report_task_receives_the_accepted_prefix(tmp_path, monkeypatch, has_worker):
    root = _projected_instance(tmp_path, monkeypatch)
    finish(root, 1, 'W' if has_worker else 'H', [], stage_intent='handoff')
    begin = budget.begin_closeout(root, 1, 2, 'Frozen execution fact task fixture', final_only=True)

    def stop_before_native(root, step, **kwargs):
        raise RuntimeError('Deterministic stop before native execution')

    monkeypatch.setattr(controller, '_native_call', stop_before_native)
    assert controller._run_final(root, begin)['status'] == 'closeout_native_unsealed'
    job = c.read(root / 'jobs/002.json')
    task = c.read(c.workspace(root, 2) / 'science-task.json')
    assert job['final_only'] and job['role'] == 'A'
    facts = task['execution_facts']
    assert facts['source_cutoff'] == 1 and facts['node_count'] == 1
    assert facts['status_counts']['known_failed'] == (1 if has_worker else 0)
    assert task['execution_facts'] == job['history']['execution_facts']


@pytest.mark.process_e2e
def test_protected_delivery_sources_task_receives_the_same_frozen_prefix(tmp_path, monkeypatch):
    root = external_scene(tmp_path, monkeypatch)
    nodes = [dict(step=step, seal_ref=c.record_ref(execution.origin(root, step) / 'result-identity.json'))
        for step in (1, 2)]
    sources = delivery.catalogue_sources(root, nodes)
    budget.begin_closeout(root, 2, 3, 'Protected delivery source projection fixture',
        final_only=True, delivery_sources=sources)
    job = c.build_job(root, 3, 'A', [1, 2], purpose='closeout',
        source_cutoff=2, delivery_sources=sources)
    task = c.read(c.workspace(root, 3) / 'science-task.json')
    assert job['final_only'] and job['delivery_sources'] == sources
    assert task['execution_facts'] == job['history']['execution_facts']
    assert task['execution_facts']['source_cutoff'] == 2
    assert task['execution_facts']['node_count'] == 2
    assert not (root / 'jobs/004.json').exists()


@pytest.mark.process_e2e
def test_fragment_admission_freezes_the_exact_native_resume_view(internal_fragment_instance):
    root = internal_fragment_instance(context='new_context')
    result = fragment_run(root, 'execution-fact-consumer')
    assert result['status'] == 'finish', result
    chain = execution.records(root, 1)
    assert len(chain) == 2
    saved = c.read(chain[0] / 'checkpoint.json')
    intent = c.read(chain[1] / 'native-intent.json')
    packet = c.workspace(root, 1) / 'work/science-resume.json'
    assert intent['science_resume'] == saved['resume_brief'] == c.read(packet)
    assert len(packet.read_bytes()) <= checkpoint.MAX_BRIEF_BYTES
    facts = intent['science_resume']['execution_facts']
    assert facts['version'] == 1 and facts['scope']['attempt'] == 0
    assert facts['observed_calls'] == 1
    assert facts['status_counts']['known_success'] == 1


def test_native_resume_writer_consumes_the_accepted_value_without_reading(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    (work / 'work').mkdir(parents=True)
    frozen = dict(version=1, task='science-task.json', scientific_summary='Frozen cutoff',
        execution_facts=dict(version=1, scope=dict(kind='stopped_worker', node='W001', attempt=0)))
    intent = dict(continuation_kind='fragment', science_resume=deepcopy(frozen))

    def forbidden(*args, **kwargs):
        raise AssertionError('Native resume must consume the accepted public value')

    with monkeypatch.context() as patch:
        patch.setattr(native, 'read', forbidden)
        patch.setattr(c, 'read', forbidden)
        patch.setattr(c, 'science_payload', forbidden)
        patch.setattr(checkpoint, 'brief', forbidden)
        native.write_science_resume(work, intent=intent)
    assert c.read(work / 'work/science-resume.json') == frozen
    assert intent['science_resume'] == frozen


@pytest.mark.parametrize('value', [None, {}, {'science_resume': {}}, {'science_resume': 'not a public object'},
    {'science_resume': {'summary': '研究' * 4096}}])
def test_native_resume_writer_degrades_an_unavailable_optional_view(tmp_path, value):
    intent = {} if value is None else value
    (tmp_path / 'work').mkdir()
    native.write_science_resume(tmp_path, intent=intent)
    packet = c.read(tmp_path / 'work/science-resume.json')
    assert packet['diagnostic'] == 'science_resume_view_unavailable'
    assert packet['task'] == 'science-task.json'
