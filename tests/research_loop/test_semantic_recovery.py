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


@pytest.mark.parametrize('focus_label', ['Renamed mechanism', 'h001.m1'])
def test_known_identity_survives_rename_and_later_omission(scene, focus_label):
    finish(scene,1,'H',[],action='experiment',search_choice=plan(
        [commitment(f'Mechanism {number}') for number in range(1,5)],'Mechanism 1'))
    original=(execution.record(scene,1)/'science-output.json').read_bytes()
    finish(scene,2,'H',[1],action='experiment',
        search_choice=plan([commitment('Renamed mechanism','h001.m1')],'h001.m1','improve'))
    finish(scene,3,'H',[2],action='experiment',search_choice=plan([],focus_label,'improve'))
    state=search.project(scene,4)
    assert set(state['mechanisms'])=={f'h001.m{i}' for i in range(1,5)}
    assert state['mechanisms']['h001.m1']['label']=='Renamed mechanism'
    assert state['mechanisms']['h001.m1']['original_h']==1
    assert state['mechanisms']['h001.m1']['source_h']==2
    assert state['focus']['mechanism_ref']=='h001.m1'
    assert (execution.record(scene,1)/'science-output.json').read_bytes()==original


def test_unknown_h_focus_keeps_science_but_does_not_apply_plan_or_invent_mechanism(scene):
    finish(scene,1,'H',[],action='experiment',search_choice=plan([commitment('Mechanism 1')],'Mechanism 1'))
    text='A scientific alternative was considered; the claimed focus has no declared identity.'
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    with pytest.raises(OutputRepairRequired) as caught:
        finish(scene,2,'H',[1],text=text,action='experiment',
            search_choice=plan([commitment('Mechanism 2')],'Unbound textual focus'))
    assert caught.value.code=='science_focus_unauthorized'
    record=execution.record(scene,2)
    assert c.read(record/'science-output.json')['scientific_result']==text
    assert not (record/'result-identity.json').exists()
    assert set(search.project(scene,2)['mechanisms'])=={'h001.m1'}


def test_invalid_selection_never_falls_back_to_predecessor_implementation(scene):
    finish(scene, 1, 'H', [], action='experiment', search_choice=plan([commitment('Mechanism')], 'Mechanism'))
    finish(scene, 2, 'W', [1], files={'candidate.py': 'ORIGINAL_IMPLEMENTATION'})
    finish(scene, 3, 'A', [1, 2], action='continue_w', selection=['W002-I001'])
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    with pytest.raises(OutputRepairRequired) as caught:
        finish(scene, 4, 'H', [1, 2, 3], action='experiment',
            search_choice=plan([], 'h001.m1'), selection=['W002-I999'])
    assert caught.value.code=='implementation_unresolved'
    job=c.read(scene/'jobs/004.json');record=execution.record(scene,4)
    old_path = 'inputs/parents/002/artifacts/candidate.py'
    assert [row['material']['path'] for row in job['selected_inputs']] == [old_path]
    assert not (record/'result-identity.json').exists()
    assert not (scene/'jobs/005.json').exists()


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


def test_scientific_reconsideration_cap_survives_new_prose_and_restart(scene, monkeypatch):
    finish(scene,1,'H',[],action='experiment',
        search_choice=plan([commitment('Declared mechanism 1')],'Declared mechanism 1','repair'))
    calls = [1]
    def execute(root, step):
        calls.append(step)
        if step > 1:
            assert_public_author_request(root, c.read(root / 'jobs' / f'{step:03d}.json'), 4 - step)
        label = f'Declared mechanism {step}'
        finish_bound(root, step, action='experiment', text=f'Different scientific prose {step}',
                     search_choice=plan([commitment(label)],label,'repair'))
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert semantic.MAX_AUTHOR_CORRECTIONS == 2
    assert result['status'] == 'semantic_correction_required'
    assert result['reason'] == 'semantic_correction_limit'
    assert result['completed_steps'] == 3 and result['usage']['observed_tokens'] == 30
    assert calls == [1, 2, 3]
    requests = [c.read(scene / 'jobs' / f'{step:03d}.json')['author_correction'] for step in (2, 3)]
    assert requests[0]['root'] == requests[1]['root']
    assert requests[0]['root']['step'] == 1
    assert requests[0]['reason'] == requests[1]['reason']
    assert [item['source']['step'] for item in requests] == [1, 2]
    assert semantic.accepted(scene, requests[-1], 4) == 2
    assert not (scene / 'jobs/004.json').exists()
    originals = original_bytes(scene, range(1, 4))
    consumed = budget.snapshot(scene)['advance_seconds']
    assert consumed > 0
    assert controller.run(scene)['reason'] == 'semantic_correction_limit'
    assert calls == [1, 2, 3]
    assert budget.snapshot(scene)['advance_seconds'] == consumed
    assert_original_bytes(originals)


@pytest.mark.parametrize('limit,expected', [('tokens', 'closeout_reserve_tokens'), ('steps', 'bounded_steps_exhausted')])
def test_author_corrections_consume_original_token_and_node_budget(tmp_path, monkeypatch, limit, expected):
    root = prepare_scene(tmp_path, max_tokens=20 if limit == 'tokens' else 100_000,
                         max_steps=2 if limit == 'steps' else 32)
    finish(root,1,'H',[],action='experiment',search_choice=plan([commitment('Declared')],'Declared','repair'))
    calls = [1];spent={};originals={}
    def execute(root, step):
        calls.append(step)
        job=c.read(root/'jobs'/f'{step:03d}.json')
        if job.get('final_only'):
            assert step==3 and job['role']=='A' and spent==dict(tokens=20,nodes=2)
            assert native.usage(root)['observed_tokens']==20
            originals.update(original_bytes(root,[1,2]))
            finish_bound(root,step,action='finish',text='The original author corrections consumed the exploration budget; no accepted W evidence or external submission is available.')
        else:
            finish_bound(root, step, action='experiment', search_choice=plan([commitment('Declared')], 'Declared','repair'))
            if step==2:spent.update(tokens=native.usage(root)['observed_tokens'],nodes=budget.used_steps(root))
    monkeypatch.setattr(native, 'execute_native', execute)
    settings = (root / 'settings.json').read_bytes()
    result = controller.run(root)
    assert result['status']=='final_a_completed'
    assert budget.closeout_state(root)['begin']['stop_reason']==expected
    assert result['completed_scientific_nodes']==3 and result['usage']['observed_tokens']==30
    assert result['submission_result']['status']=='unsubmitted' and budget.closeout_state(root)['actions']==[]
    assert calls==[1,2,3] and [c.read(root/'jobs'/f'{step:03d}.json')['role'] for step in calls]==['H','H','A']
    final=budget.final_a_barrier(root)
    assert final['step']==3 and final['selection']['accounted_tokens']==20
    record=execution.origin(root,3)
    assert c.read(record/'native-outcome.json')['final_a_completion']['final_return']==c.read(record/'native-intent.json')['budget_slice']['final_return']
    assert c.validate(c.workspace(root,3))['next_action']=='finish'
    assert not (root / 'jobs/004.json').exists()
    assert (root / 'settings.json').read_bytes() == settings
    assert_original_bytes(originals)
    originals.update(original_bytes(root,[3]))
    assert controller.run(root)['status']=='final_a_completed'
    assert calls==[1,2,3]
    assert_original_bytes(originals)


def terminal_payload(root, step, payload):
    record = c.host_record(c.workspace(root, step))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json')))
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
    c.build_job(scene,1,'W',[])
    record = c.host_record(c.workspace(scene, 1))
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(scene / 'jobs/001.json')))
    def forbidden(*args, **kwargs):
        pytest.fail('Unknown native submission must not dispatch a correction or replay')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    intent = (record / 'native-intent.json').read_bytes()
    for _ in range(2):
        assert controller.run(scene)['status'] == 'native_outcome_unsealed'
    assert (record / 'native-intent.json').read_bytes() == intent
    assert not (record / 'result-identity.json').exists()
    assert not (scene / 'jobs/002.json').exists()


@pytest.mark.parametrize('changed', ['raw', 'job', 'history_page'])
def test_integrity_corruption_is_not_semantic_author_recovery(scene, monkeypatch, changed):
    step=3 if changed=='history_page' else 1
    if step==3:
        finish(scene,1,'H',[],action='experiment',search_choice=plan([commitment('Declared')],'Declared'))
        route=c.read(c.host_record(c.workspace(scene,1))/'science-output.json')['route_reason']
        c.build_job(scene,2,'W',[1],instruction=route)
        finish(scene,2,'W',[1])
        route=controller._route(scene,2,'W',c.validate(c.workspace(scene,2)),c.read(scene/'jobs/002.json'),1,None)
        assert route.role=='A' and route.instruction
        job=c.build_job(scene,3,route.role,list(route.parents),route.instruction,route.evidence_return)
        payload=dict(summary='Scientific attempt',scientific_result='Retain science, not unauthorized focus',limitations=['Fixture only'],
            implementation_selection=None,candidate_recommendation=None,progress_assessment=None,
            decision_request=None,portfolio_review=None,search_review=review(next_focus=focus('unknown')),
            next_action='continue_w',route_reason='Needs bounded author repair')
    else:
        job=c.build_job(scene,1,'H',[]);payload=h_payload(plan([commitment('Declared')],'Undeclared'))
    terminal_payload(scene,step,payload)
    record=c.host_record(c.workspace(scene,step))
    if changed == 'raw':
        (record / 'science-output.json').write_text('TAMPERED SCIENCE')
    elif changed == 'job':
        c.write(record/'native-intent.json',dict(task_ref=dict(path=str(scene/'jobs/002.json'))))
        with pytest.raises(ValueError,match='Native launch task changed'):
            c.seal_result(scene,step)
    else:
        page = c.workspace(scene, step) / job['history']['view']['indexes']['nodes']
        page.chmod(0o600)
        page.write_text('TAMPERED HISTORY')
    def forbidden(*args, **kwargs):
        pytest.fail('Integrity corruption cannot create a new scientific attempt')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    stopped=controller.run(scene)
    if changed=='history_page':
        assert stopped['status']=='seal_pending' and stopped['completed_steps']==2
        assert 'Original size changed' in stopped['reason']
        assert str(page) in stopped['reason']
    else:
        assert stopped['status'] in {'native_outcome_unsealed','seal_pending'}
    assert not (record / 'result-identity.json').exists()
    assert execution.records(scene,step)==[record]
    assert not (scene / 'jobs' / f'{step+1:03d}.json').exists()


def test_terminal_unsealed_attempt_is_collected_before_new_author_work(scene, monkeypatch):
    c.build_job(scene, 1, 'H', [])
    terminal_payload(scene, 1, h_payload(plan([commitment('Declared')], 'Undeclared')))
    originals = original_bytes(scene, [1])
    from scidiscovery.research_loop import continuation
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    with pytest.raises(OutputRepairRequired):c.seal_result(scene,1)
    request=continuation.output_request(scene,1,execution.record(scene,1),verify_task=False)
    assert request['code']=='science_focus_unauthorized'
    assert not (scene/'jobs/002.json').exists()
    assert_original_bytes(originals)


def test_current_terminal_tenth_attempt_keeps_originals_and_repairs_only_its_output(scene):
    for step in range(1,10):
        finish(scene,step,'H',[step-1] if step>1 else [],action='experiment',
            search_choice=plan([commitment('Declared')] if step==1 else [],'Declared'))
    c.build_job(scene,10,'H',[9])
    terminal_payload(scene,10,h_payload(plan([commitment('Mechanism 2')],'Undeclared mechanism')))
    originals=original_bytes(scene,range(1,11))
    from scidiscovery.research_loop import continuation
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    with pytest.raises(OutputRepairRequired):c.seal_result(scene,10)
    request=continuation.output_request(scene,10,execution.record(scene,10),verify_task=False)
    assert request['code']=='science_focus_unauthorized'
    assert not (scene/'jobs/011.json').exists()
    assert c.read(scene/'jobs/010.json')['handoff_protocol']==CURRENT
    assert all(outcome(c.host_record(c.workspace(scene,step)))['binding_protocol']==CURRENT
        for step in range(1,10))
    assert_original_bytes(originals)


@pytest.mark.parametrize('protocol', [7, 8])
def test_retired_write_schema_is_rejected_without_changing_originals(scene, protocol):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan([commitment('Declared')], 'Declared'))
    originals = original_bytes(scene, [1])
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('H', protocol)
    assert_original_bytes(originals)


def test_more_than_sixteen_multibyte_mechanisms_have_bounded_readonly_navigation(scene):
    for step in range(1, 6):
        rows = [dict(label=f'{step}-{index}' + '机' * 70, minimal_test='证据' * 500, existing_ref=None)
                for index in range(4)]
        finish(scene, step, 'H', [step - 1] if step > 1 else [], action='analyze',
               search_choice=plan(rows, rows[0]['label']))
    job = c.build_job(scene, 6, 'A', [5])
    view = c.science_view(job,scene)['lineage']
    assert 'search' not in c.science_view(job,scene)
    assert view['mechanisms']['count'] == 20
    assert len(json.dumps(view, ensure_ascii=False, indent=2).encode()) <= search.MAX_VIEW_BYTES
    path, rows, pages = view['indexes']['mechanisms'], [], []
    while path:
        page_path = c.workspace(scene, 6) / path
        page = c.read(page_path)
        assert len(page['mechanisms']) <= 16
        assert page_path.stat().st_size <= 64_000
        assert not page_path.stat().st_mode & 0o222
        for row in page['mechanisms']:
            assert len(row['minimal_test'].encode()) <= 160 and row['minimal_test_truncated'] is True
            original=originals.resolve_root_ref(scene,dict(path=str(c.workspace(scene,6)/row['original'])))
            assert original.is_file() and not original.stat().st_mode & 0o222
            assert c.read(original)['step']==int(row['original'].split('/')[3])
        rows.extend(page['mechanisms'])
        pages.append(page_path)
        path = page['next_page']
    assert len(pages) == 2 and len(rows) == 20
    assert {row['mechanism_ref'] for row in rows} == {f'h{step:03d}.m{index}'
                                                     for step in range(1, 6) for index in range(1, 5)}


def test_e_requested_at_author_cap_preserves_root_and_does_not_renew_author_count(scene, monkeypatch):
    finish(scene,1,'H',[],action='experiment',
        search_choice=plan([commitment('Declared 1')],'Declared 1','repair'))
    calls = [(1,'H')]
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        calls.append((step, job['role']))
        if job['role'] == 'H':
            finish_bound(root, step, action='extract' if step == 3 else 'experiment',
                         search_choice=plan([commitment(f'Declared {step}')], f'Declared {step}','repair'))
        else:
            assert step == 4 and job['role'] == 'E'
            assert job['evidence_return'] == dict(role='H', step=3)
            finish_bound(root, step, text='Exact original extraction for the second author correction')
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'semantic_correction_required' and result['reason'] == 'semantic_correction_limit'
    assert calls == [(1, 'H'), (2, 'H'), (3, 'H'), (4, 'E')]
    assert result['completed_steps'] == 4 and result['usage']['observed_tokens'] == 40
    jobs = [c.read(scene / 'jobs' / f'{step:03d}.json') for step in (2, 3, 4)]
    requests = [job['author_correction'] for job in jobs]
    assert all(request['root'] == requests[0]['root'] for request in requests)
    assert all(request['reason'] == requests[0]['reason'] for request in requests)
    assert [request['source']['step'] for request in requests] == [1, 2, 3]
    assert semantic.accepted(scene, requests[-1], 5) == 2
    assert not (scene / 'jobs/005.json').exists()
    originals = original_bytes(scene, range(1, 5))
    assert controller.run(scene)['reason'] == 'semantic_correction_limit'
    assert calls == [(1, 'H'), (2, 'H'), (3, 'H'), (4, 'E')]
    assert_original_bytes(originals)


@pytest.mark.parametrize('changed', ['job_ref', 'raw_ref'])
def test_wrong_sealed_original_identity_is_not_author_correction(scene, monkeypatch, changed):
    _, work, record = finish(scene, 1, 'H', [], action='experiment',
                             search_choice=plan([commitment('Declared')], 'Declared','repair'))
    path = record / 'result-identity.json'
    seal = c.read(path)
    if changed == 'job_ref':
        seal['job_ref']=dict(path=str(scene/'jobs/002.json'))
    else:
        foreign=scene/'foreign-science.json'
        foreign.write_bytes((record/'science-output.json').read_bytes())
        seal['raw_ref']=c.file_ref(foreign)
    c.write(path, seal)
    corrupted = path.read_bytes()
    with pytest.raises(ValueError):
        c.validate(work)
    def forbidden(*args, **kwargs):
        pytest.fail('Wrong original identity cannot authorize a new author or experiment')
    monkeypatch.setattr(native, 'execute_native', forbidden)
    assert controller.run(scene)['status'] == 'sealed_node_invalid'
    assert path.read_bytes() == corrupted
    assert not (scene / 'jobs/002.json').exists()


def test_prepared_wrong_w_is_preserved_when_effective_route_requires_author_h(scene, monkeypatch):
    finish(scene,1,'H',[],action='experiment',search_choice=plan([commitment('Declared')],'Declared','repair'))
    c.build_job(scene,2,'W',[1],instruction='Exact scientific question')
    calls,prepared=[1],original_bytes(scene,[2])
    def execute(*args,**kwargs):pytest.fail('A conflicting retained task cannot dispatch')
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'retained_job_conflict'
    assert calls == [1]
    assert c.read(scene / 'jobs/002.json')['role'] == 'W'
    assert not (c.host_record(c.workspace(scene, 2)) / 'native-intent.json').exists()
    assert not (scene / 'jobs/003.json').exists()
    assert_original_bytes(prepared)
    assert controller.run(scene)['status'] == 'retained_job_conflict'
    assert calls == [1]
    assert_original_bytes(prepared)
