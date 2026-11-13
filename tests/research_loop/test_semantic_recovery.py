"""Incremental scientific choices and bounded author correction; no models."""
from test_history_handoff import DEFAULT_ACTION, selection_explanation, fixture_portfolio_review, fixture_final_coverage
import json

import pytest

from scidiscovery.research_loop import backends, budget, contracts as c, controller, correction, execution, experiments, native, originals, search, semantic
from execution_fixtures import execution_registry
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
from test_breadth_science import run as run_command, findings

pytestmark = pytest.mark.process_e2e


def native_intent(root, job):
    """Use real stage anchors once; an accepted intent is never overwritten."""
    record = execution.record(root, job['step'])
    path = record / 'native-intent.json'
    task = c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json')
    if not path.exists():
        node_anchor = budget.accept_node(root, job)
        anchor = search.accept(root, job) if node_anchor is None else None
        c.write(path, dict(step=job['step'], task_ref=task,
            **(dict(search_slice=anchor) if anchor is not None else {}),
            **(dict(budget_slice=node_anchor) if node_anchor is not None else {})))
        from scidiscovery.research_loop import accepted_budget
        accepted_budget.accept_native(root, job['step'], record, c.read(path))
    original = c.regular(path).read_bytes()
    intent = c.read(path)
    assert intent['task_ref'] == task
    if job['role'] == 'W' and job.get('search') and budget.node_limits(root, job) is None:
        assert search.stop_reason(root, job, intent, budget.snapshot(root),
            dict(input_tokens=0, output_tokens=0)) is None
    return original


def finish(root, step, role, parents, text='A bounded scientific finding', action=DEFAULT_ACTION, files=None,
           summary='A contribution', limitations=None, recommendation=None, search_choice=None,
           progress=None, selection=None, mechanism_findings=None):
    """Current fake native output, preserving the exact accepted intent and budget."""
    parents=list(parents)
    path = root / 'jobs' / f'{step:03d}.json'
    previous = c.read(path) if path.exists() else {}
    job = c.build_job(root, step, role, parents, previous.get('instruction', ''),
        previous.get('evidence_return'), previous.get('correction_request'), previous.get('author_correction'),
        **(dict(final_report=previous['final_report']) if 'final_report' in previous else {}))
    if action is DEFAULT_ACTION:action='continue_w' if role=='A' else 'wait'
    work = c.workspace(root, step);record = execution.record(root, step)
    original_intent = native_intent(root, job)
    payload = dict(summary=summary, scientific_result=text,
        limitations=limitations if limitations is not None else ['Fixture only'], implementation_selection=selection)
    if role == 'W':
        if mechanism_findings is None:
            labels = {row['mechanism_ref']: row['label'] for row in job['search']['breadth']['mechanisms']}
            mechanism_findings = [dict(mechanism=labels[row['mechanism_ref']], basis='blocked',
                finding='This mechanical fixture has no experiments or observations for the mechanism.')
                for row in job['search']['coverage_obligation']]
        payload.update(stage_intent='review',mechanism_findings=mechanism_findings,
            consultation_question='Which retained observation changes the unfinished fixture question?')
        task=c.read(work/'science-task.json')
        if task.get('candidate_nomination_mechanisms'):
            payload['candidate_nominations']=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
                reason='This no-model recovery fixture declares no scientifically suitable candidate.')
                for row in task['candidate_nomination_mechanisms'] if row['status']!='waived'])
    if role == 'H':
        payload['search_plan'] = search_choice
    if role == 'A':
        payload.update(candidate_recommendation=recommendation, search_review=search_choice,
            progress_assessment=progress, decision_request=None, portfolio_review=None)
    if role in {'H', 'A'}:
        payload.update(next_action=action, route_reason='Exact scientific question')
    for name, contents in (files or {}).items():
        target = work / 'artifacts' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    fixture_portfolio_review(root,job,payload)
    fixture_final_coverage(root,job,payload)
    c.write(record / 'science-output.json', payload)
    meter=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3)
    c.write(record / 'usage.json',meter)
    terminal=dict(returncode=0,stop_reason=None,native_started=False,usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json'))
    intent=c.read(record/'native-intent.json')
    if (intent.get('budget_slice') or {}).get('final_return') is not None:
        backend=backends.selected(c.settings(root,job))
        terminal.update(cleanup='observed_group_cleaned',native_backend=backend.name,
            metering=dict(coverage='terminal',reason=None,source=backend.terminal_source,tokens=meter))
        accepted=budget.final_a_return(root,job,intent)
        assert accepted is not None
        terminal['final_a_completion']=budget.final_a_receipt(accepted,intent,meter,terminal['usage'])
        c.write(record/'usage-observation.json',terminal['metering'])
    c.write(record/'native-outcome.json',terminal)
    c.seal_result(root, step)
    assert (record / 'native-intent.json').read_bytes() == original_intent
    return job, work, record


def prepare_scene(tmp_path, *, max_tokens=100_000, max_steps=32, correction_policy=None):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Compare mechanisms, preserve original findings and correct unsupported choices.')
    root = tmp_path / 'instance'
    from breadth_execution_fixtures import native_launcher
    controller.prepare(root, objective, public, native_launcher(), max_tokens=max_tokens,
                       max_seconds=3600, max_steps=max_steps, model='fake', reasoning_effort='high',
                       native_options=dict(workspace='workspace-write',network=False),
                       correction_policy=correction_policy)
    return root


@pytest.fixture
def scene(tmp_path, execution_registry):
    return prepare_scene(tmp_path)


def commitment(label, existing_ref=None):
    return dict(label=label, minimal_test='Discriminating scientific comparison', existing_ref=existing_ref)


def plan(rows, label, action='screen'):
    return dict(scope='Global scientific comparison', commitments=rows,
                focus=dict(label=label, action=action) if label is not None else None)


def focus(ref='h001.m1', action='screen'):
    return dict(mechanism_ref=ref, action=action)


def review(items=(), next_focus=None):
    return dict(dispositions=list(items), next_focus=next_focus)


def outcome(record):
    value = c.read(record / 'result-identity.json')['control_outcome']
    assert value['binding_protocol'] == CURRENT
    assert len(value['diagnostics']) <= 8
    assert all(set(row) == {'code', 'subject'} for row in value['diagnostics'])
    return value


def finish_bound(root, step, **choices):
    """Stand-in native transport; production owns the actual job and sealing."""
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    parents = [int(row['path'].split('/')[2]) for row in job['direct_refs']]
    return finish(root, step, job['role'], parents, **choices)


def assert_public_author_request(root, job, remaining):
    request = job['author_correction']
    view = c.science_view(job, root)
    public = view['control_request']
    assert set(public) == {'reason', 'source_step', 'original_science', 'initial_attempt_step',
                           'unresolved', 'remaining_author_corrections'}
    assert public['reason'] == request['reason']
    assert public['source_step'] == request['source']['step']
    assert public['initial_attempt_step'] == request['root']['step']
    assert public['original_science'] == f'inputs/parents/{request["source"]["step"]:03d}/conclusion.json'
    assert public['remaining_author_corrections'] == remaining
    text = json.dumps(view, ensure_ascii=False)
    for key in ('job_ref', 'raw_ref', 'seal_ref'):
        assert key not in text
        assert all(identity[key]['path'] not in text for identity in (request['root'], request['source']))


def original_bytes(root, steps):
    paths = []
    for step in steps:
        paths.append(root / 'jobs' / f'{step:03d}.json')
        work=c.workspace(root,step);record=c.host_record(work)
        paths.extend(work/name for name in ('science-task.json','conclusion.json','artifacts/candidate.py'))
        paths.extend(record/name for name in ('native-intent.json','native-outcome.json','science-output.json',
            'usage.json','result-identity.json','result.json','work-evidence.json','checkpoint.json','stage-end.json'))
    return {path: path.read_bytes() for path in paths if path.is_file()}


def assert_original_bytes(originals):
    assert all(path.is_file() and path.read_bytes() == data for path, data in originals.items())


def terminal_payload(root, step, payload):
    record = c.host_record(c.workspace(root, step))
    native_intent(root, c.read(root / 'jobs' / f'{step:03d}.json'))
    if isinstance(payload.get('candidate_recommendation'),str):payload['selection_explanation']=selection_explanation()
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,native_started=False,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))


def h_payload(choice):
    return dict(summary='Scientific author finding', scientific_result='Retain the original scientific reasoning.',
                limitations=['Bounded control interpretation'], implementation_selection=None,
                next_action='experiment', route_reason='Exact scientific question', search_plan=choice)


def test_unknown_submission_is_never_rerouted_or_replayed(scene, monkeypatch):
    job = c.build_job(scene,1,'W',[])
    record = c.host_record(c.workspace(scene, 1))
    native_intent(scene, job)
    def forbidden(*args, **kwargs):
        pytest.fail('Unknown native submission must not dispatch a correction or replay')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    intent = (record / 'native-intent.json').read_bytes()
    for _ in range(2):
        assert controller.run(scene)['status'] == 'native_outcome_unsealed'
    assert (record / 'native-intent.json').read_bytes() == intent
    assert not (record / 'result-identity.json').exists()
    assert not (scene / 'jobs/002.json').exists()


def w_payload():
    return dict(summary='Retain the original bounded output',
        scientific_result='Synthetic transport only; no scientific qualification.',
        limitations=['Exact original identity check'], implementation_selection=None,
        stage_intent='decision', consultation_question=None, mechanism_findings=None,
        candidate_nominations=None)


@pytest.mark.parametrize('changed', ['raw', 'job'])
def test_integrity_corruption_is_not_semantic_author_recovery(scene, monkeypatch, changed):
    c.build_job(scene, 1, 'W', [])
    terminal_payload(scene, 1, w_payload())
    record = execution.record(scene, 1)
    if changed == 'raw':
        (record/'science-output.json').write_text('TAMPERED SCIENCE')
    else:
        path = record/'native-intent.json'; path.chmod(0o600)
        c.write(path, dict(task_ref=dict(path=str(scene/'jobs/002.json'))))
        with pytest.raises(ValueError, match='native intent|Native launch task|control original'):
            c.seal_result(scene, 1)
    def forbidden(*args, **kwargs):
        pytest.fail('Integrity corruption cannot create a new scientific attempt')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    if changed == 'job':
        with pytest.raises(ValueError, match='native intent identity changed'):
            controller.run(scene)
    else:
        stopped = controller.run(scene)
        assert stopped['status'] in {'native_outcome_unsealed', 'seal_pending'}
    assert not (record/'result-identity.json').exists()
    if changed == 'raw':
        assert execution.records(scene, 1) == [record]
    assert not (scene/'jobs/002.json').exists()


def test_terminal_unsealed_attempt_is_collected_before_new_author_work(scene, monkeypatch):
    c.build_job(scene, 1, 'W', [])
    payload = dict(w_payload(), stage_intent='review', consultation_question=None)
    terminal_payload(scene, 1, payload)
    originals = original_bytes(scene, [1])
    from scidiscovery.research_loop import continuation
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    inventory = c.accept_current_inventory(scene, 1)
    with pytest.raises(OutputRepairRequired):
        c.seal_result(scene, 1, inventory=inventory)
    request = continuation.output_request(scene, 1, inventory.record, verify_task=False, inventory=inventory)
    assert request['code'] == 'consultation_question_missing'
    assert not (scene/'jobs/002.json').exists()
    assert_original_bytes(originals)


@pytest.mark.parametrize('protocol', [7, 8])
def test_retired_write_schema_is_rejected_without_changing_originals(scene, protocol):
    c.build_job(scene, 1, 'W', [])
    terminal_payload(scene, 1, w_payload())
    c.seal_result(scene, 1)
    originals = original_bytes(scene, [1])
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('H', protocol)
    assert_original_bytes(originals)


@pytest.mark.parametrize('changed', ['job_ref', 'raw_ref'])
def test_wrong_sealed_original_identity_is_not_author_correction(scene, monkeypatch, changed):
    c.build_job(scene, 1, 'W', [])
    terminal_payload(scene, 1, w_payload())
    c.seal_result(scene, 1)
    work = c.workspace(scene, 1); record = execution.record(scene, 1)
    path = record / 'result-identity.json'
    seal = c.read(path)
    if changed == 'job_ref':
        seal['job_ref']=dict(path=str(scene/'jobs/002.json'))
    else:
        foreign=scene/'foreign-science.json'
        foreign.write_bytes((record/'science-output.json').read_bytes())
        seal['raw_ref']=c.file_ref(foreign)
    path.chmod(0o600)
    c.write(path, seal)
    corrupted = path.read_bytes()
    with pytest.raises(ValueError):
        c.validate(work)
    def forbidden(*args, **kwargs):
        pytest.fail('Wrong original identity cannot authorize a new author or experiment')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    with pytest.raises(ValueError, match='Source seal'):
        controller.run(scene)
    assert path.read_bytes() == corrupted
    assert not (scene / 'jobs/002.json').exists()
