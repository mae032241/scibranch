"""Finite global portfolio reconsideration, without scientific value classification."""
import copy
import json
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, decision, execution, experiments, history, native, search
from execution_fixtures import execution_registry
from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
from test_breadth_science import run as run_command, findings
from test_history_handoff import finish

P, Q, R = 'h001.m1', 'h001.m2', 'h001.m3'
TRADEOFF = 'Retain the current branch for its decision value; remaining commitments remain unresolved under the original constraints.'
REQUEST = dict(decision='mechanism', question='Which declared mechanism should receive the next scientific comparison?',
               change_criterion='A controlled contradiction would change the mechanism choice.')


def prepare(tmp_path, *, threshold=2, enabled=True, version=2):
    public = tmp_path / 'public'; public.mkdir(parents=True)
    objective = public / 'objective.md'; objective.write_text('Judge the whole committed mechanism portfolio without abandoning original constraints.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['never-launched'], max_tokens=100_000, max_seconds=3600,
        max_steps=64, model='fixture', reasoning_effort='high', decision_contract_version=1,
        portfolio_policy=dict(review_after_cycles=threshold, version=version) if enabled else None)
    plan = dict(scope='Competing solutions', commitments=[dict(label=label, minimal_test='A discriminating comparison', existing_ref=None)
                for label in ('P', 'Q', 'R', 'S')], focus=dict(label='P', action='screen'))
    finish(root, 1, 'H', [], action='experiment', search_choice=plan)
    worker(root, 2, [1])
    return root


def route_instruction(root, parents):
    """Use the actual sealed predecessor, including W partial-delivery routing."""
    if not parents:
        return ''
    step = max(parents)
    job = c.read(root / 'jobs' / f'{step:03d}.json')
    payload = c.validate(c.workspace(root, step))
    if job['role'] == 'W':
        return controller._route(root, step, 'W', payload, job, None, step)[2]
    return payload.get('route_reason', '')


def worker(root, step, parents, *, observed=P):
    """One genuine accepted terminal call; untested assignments stay blocked."""
    path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(path) if path.exists() else c.build_job(root, step, 'W', parents,
        instruction=route_instruction(root, parents))
    assert job['role'] == 'W' and [int(Path(row['path']).parts[2]) for row in job['direct_refs']] == parents
    record = execution.record(root, step)
    anchor = budget.accept_node(root, job)
    intent = dict(step=step, task_sha256=c.digest(path), **(
        dict(budget_slice=anchor) if anchor is not None else dict(search_slice=search.accept(root, job))))
    c.write(record / 'native-intent.json', intent)
    original_intent = (record / 'native-intent.json').read_bytes()
    output = root.parent / f'portfolio-execution-{step}.json'
    experiments.configure(root, step, 'local_execution:command', dict(run_argv=[sys.executable]), output)
    config = c.read(output)
    result = run_command(root, config, observed)
    assert result['status'] == 'known_success' and result['mechanism_binding']['mechanism_ref'] == observed
    assigned = [row['mechanism_ref'] for row in job['search']['coverage_obligation']]
    assert observed in [row['mechanism_ref'] for row in job['search']['breadth']['mechanisms']]
    payload = dict(summary='A retained mechanical comparison',
        scientific_result=f'Mechanical comparison at W{step}; scientific adequacy remains unknown.',
        limitations=['A tiny command is not scientific effectiveness evidence'], implementation_selection=None,
        stage_intent='review', mechanism_findings=findings(observed, blocked=[ref for ref in assigned if ref != observed]),
        candidate_nominations=dict(representatives=[], extras=[], gaps=[dict(
            mechanism_ref=row['mechanism_ref'], reason='The mechanical command supplies no scientifically suitable model candidate.')
            for row in job.get('candidate_nomination_mechanisms', []) if row['status'] != 'waived']))
    c.write(record / 'science-output.json', payload)
    meter = dict(input_tokens=7, cached_input_tokens=2, output_tokens=3)
    c.write(record / 'usage.json', meter)
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, usage=native.usage(root),
        raw_sha256=c.digest(record / 'science-output.json'), search_slice=search.report(root, job, intent, meter)))
    c.seal_result(root, step)
    assert (record / 'native-intent.json').read_bytes() == original_intent


def author(root, step, parents, *, branch=None, portfolio=None, action='continue_w', request=REQUEST, dispositions=(), mode='screen'):
    path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(path) if path.exists() else c.build_job(root, step, 'A', parents,
        instruction=route_instruction(root, parents))
    assert job['role'] == 'A' and [int(Path(row['path']).parts[2]) for row in job['direct_refs']] == parents
    record = execution.record(root, step)
    payload = dict(summary='Global portfolio judgment', scientific_result='Retain scientific limitations while choosing the next direction.',
        limitations=['Unobserved target performance is unknown'], implementation_selection=None,
        search_review=dict(dispositions=list(dispositions), next_focus=dict(mechanism_ref=branch, action=mode)),
        progress_assessment=dict(verdict='no_progress'), candidate_recommendation=None,
        next_action=action, route_reason='Exact scientific question', decision_request=request,
        portfolio_review=portfolio)
    c.write(record / 'native-intent.json', dict(task_sha256=c.digest(path)))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, usage=native.usage(root),
                                             raw_sha256=c.digest(record / 'science-output.json')))
    c.seal_result(root, step)
    return job, payload


def two_cycles(root, *, action='continue_w', branch=None, portfolio=None):
    author(root, 3, [1, 2])
    worker(root, 4, [1, 2, 3], observed=Q)
    return author(root, 5, [1, 4], action=action, branch=branch, portfolio=portfolio)


def pending_three(root):
    author(root, 3, [1, 2])
    worker(root, 4, [1, 2, 3], observed=Q)
    author(root, 5, [1, 4])
    worker(root, 6, [1, 4, 5], observed=R)


def mechanism_rows(root, step, view):
    rows=[];page=view['index']
    while page:
        path=c.workspace(root,step)/page
        assert path.stat().st_mode & 0o222 == 0
        value=c.read(path);rows.extend(value['mechanisms']);page=value['next_page']
    return rows


def test_real_controller_reminder_does_not_require_report_or_author_correction(tmp_path, monkeypatch):
    root=prepare(tmp_path,threshold=3)
    pending_three(root)
    job,payload=author(root,7,[1,6])
    record=execution.record(root,7);raw=(record/'science-output.json').read_bytes()
    view=c.science_view(job,root)['lineage']['mechanisms']
    assert view['portfolio']['review_reminder'] is True
    rows=mechanism_rows(root,7,view)
    assert [row['mechanism_ref'] for row in rows]==[P,Q,R,'h001.m4']
    by_ref={row['mechanism_ref']:row for row in rows}
    assert by_ref['h001.m4']['unserved_cycles_if_assessed']==3
    assert by_ref[P]['accepted_calls']==1 and by_ref[P]['initial_calls_remaining']==0
    assert by_ref['h001.m4']['accepted_calls']==0 and by_ref['h001.m4']['initial_calls_remaining']==1
    assert decision.reason(root,job,payload) is None
    assert 'portfolio_status' not in c.read(record/'result-identity.json')['control_outcome']
    state=search.project(root,8)['portfolio']
    assert state['invested']=={P:1,Q:1,R:1} and len(state['seen_w'])==3
    accepted=[]
    def execute(root,step):
        current=c.read(root/'jobs'/f'{step:03d}.json')
        assert step==8 and current['role']=='W' and 'author_correction' not in current
        accepted.append((step,current['role']))
        parents=[int(Path(ref['path']).parts[2]) for ref in current['direct_refs']]
        c.write(root/'pause.request',dict(reason='Stop after accepted W'))
        # Null A focus grants no extra call to P: S still retains its original
        # opportunity. Exercise legal W work without weakening that admission.
        worker(root,step,parents,observed='h001.m4')
    monkeypatch.setattr(native,'execute_native',execute)
    assert controller.run(root)['status']=='paused' and accepted==[(8,'W')]
    assert (record/'science-output.json').read_bytes()==raw
    assert controller.run(root)['status']=='paused' and accepted==[(8,'W')]


@pytest.mark.parametrize('action,branch', [('continue_w', Q), ('revise_h', P), ('audit', P), ('extract', P)])
def test_switch_revision_audit_or_extraction_is_not_forced_to_review(tmp_path, action, branch):
    root = prepare(tmp_path)
    job, payload = two_cycles(root, action=action, branch=branch)
    assert decision.reason(root,job,payload) is None
    state = search.project(root, 6)['portfolio']
    if action in {'audit', 'extract'}:
        assert state['last_decision_step']==3 and state['invested']=={P:1}
        assert len(state['seen_w']) == 1 and state['pending_w']['step'] == 4
    else:
        assert state['invested']=={P:1,Q:1} and state['last_decision_step']==5
        assert len(state['seen_w']) == 2 and state['pending_w'] is None


@pytest.mark.parametrize('action', ['audit', 'extract'])
def test_unsupported_audit_feedback_or_e_return_consumes_original_w_only_once(tmp_path, monkeypatch, action):
    root = prepare(tmp_path, threshold=3)
    author(root, 3, [1, 2], action=action, request=REQUEST if action == 'extract' else None, portfolio=TRADEOFF)
    accepted = []
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        accepted.append(job['role'])
        if job['role'] == 'E':
            finish(root, step, 'E', parents, text='Exact requested public evidence')
        else:
            assert job['role'] == 'A' and 2 in parents
            c.write(root / 'pause.request', dict(reason='Stop after one assessed W'))
            author(root, step, parents)
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(root)['status'] == 'paused'
    assert accepted == (['A'] if action == 'audit' else ['E', 'A'])
    state = search.project(root, max(int(p.stem) for p in (root / 'jobs').glob('*.json')) + 1)['portfolio']
    assert state['invested'][P] == 1 and len(state['seen_w']) == 1 and state['pending_w'] is None
    assert state['last_decision_step']==max(int(p.stem) for p in (root/'jobs').glob('*.json'))
    assert controller.run(root)['status'] == 'paused' and len(state['seen_w']) == 1


def test_qualified_author_finishes_original_cycle_and_review_prevents_h_analyze_recount(tmp_path, monkeypatch):
    root = prepare(tmp_path, threshold=1)
    author(root, 3, [1, 2], request=None)
    assert search.project(root, 4)['portfolio']['pending_w']['step'] == 2
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        assert step == 4 and job['role'] == 'A'
        parents = [int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        c.write(root / 'pause.request', dict(reason='Stop after bounded author resolution'))
        author(root, step, parents, action='revise_h', portfolio=TRADEOFF)
    monkeypatch.setattr(native, 'execute_native', execute)
    assert controller.run(root)['status'] == 'paused'
    state = search.project(root, 5)['portfolio']
    assert len(state['seen_w']) == 1 and state['pending_w'] is None and state['invested']=={P:1}
    c.build_job(root, 5, 'H', [1, 2, 4], instruction='Exact scientific question')
    finish(root, 5, 'H', [1, 2, 4], action='analyze', search_choice=dict(scope='Reconsider target solution', commitments=[], focus=dict(label=P,action='screen')))
    author(root, 6, [5, 2], action='revise_h')
    assert len(search.project(root, 7)['portfolio']['seen_w']) == 1
    assert search.project(root,7)['portfolio']['invested']=={P:1}


def test_same_status_omissions_and_existing_ref_rename_do_not_erase_window_deferral(tmp_path):
    root = prepare(tmp_path, threshold=3)
    author(root, 3, [1, 2], dispositions=[dict(mechanism_ref=Q,status='deferred',reason='A scientific tradeoff')])
    worker(root, 4, [1, 2, 3], observed=R)
    author(root, 5, [1, 4], action='revise_h', dispositions=[dict(mechanism_ref=Q,status='deferred',reason='Same-status prose is not a reset')])
    c.build_job(root, 6, 'H', [1, 4, 5], instruction='Exact scientific question')
    finish(root, 6, 'H', [1, 4, 5], action='experiment', search_choice=dict(scope='Original portfolio remains',
        commitments=[dict(label='Renamed Q',minimal_test='Same discriminating test',existing_ref=Q)], focus=dict(label=P,action='screen')))
    state = search.project(root, 7)
    assert state['mechanisms'][Q]['label'] == 'Renamed Q' and state['portfolio']['deferred'][Q] == 2
    worker(root, 7, [6], observed='h001.m4')
    job, payload = author(root, 8, [6, 7])
    assert decision.reason(root,job,payload) is None
    assert c.science_view(job,root)['lineage']['mechanisms']['portfolio']['review_reminder'] is True


def test_cumulative_actual_investments_and_waiver_use_existing_scientific_status(tmp_path):
    root = prepare(tmp_path, threshold=3)
    pending_three(root)
    job, payload = author(root, 7, [1, 6])
    state = search.project(root, 7)['portfolio']
    assert state['invested'] == {P: 1, Q: 1} and state['pending_w']['branches'] == [R]
    assert decision.reason(root,job,payload) is None
    # Waive is A's scientific decision, not a fabricated observation or host ranking.
    alternative = copy.deepcopy(payload)
    alternative['search_review']['dispositions'] = [dict(mechanism_ref='h001.m4', status='waived',
        reason='Scientifically outside the retained objective scope')]
    assert decision.reason(root,job,alternative) is None
    # Prospective alternatives do not rewrite the accepted original or recount W.
    assert c.read(execution.record(root, 7) / 'science-output.json') == payload
    assert len(search.project(root,8)['portfolio']['seen_w'])==3


def test_many_mechanisms_bounded_default_complete_index_and_page_tamper(tmp_path):
    root = prepare(tmp_path)
    author(root, 3, [1, 2], action='revise_h')
    # H delta declarations add 16 more mechanisms without restating earlier commitments.
    last = 3
    for step in range(4, 8):
        c.build_job(root, step, 'H', [last], instruction='Exact scientific question')
        finish(root, step, 'H', [last], action='analyze', search_choice=dict(scope='Global portfolio',
            commitments=[dict(label='机' * 70 + f'{step}-{i}',minimal_test='测' * 150,existing_ref=None) for i in range(4)],
            focus=dict(label=P,action='screen')))
        last = step
    job = c.build_job(root, 8, 'A', [7], instruction=route_instruction(root, [7]))
    view = c.science_view(job, root)['lineage']
    assert len((json.dumps(view,ensure_ascii=False,indent=2)+'\n').encode()) <= history.MAX_BRIEF_BYTES
    assert len(view['mechanisms']['overview']) <= 4
    assert view['mechanisms']['overview_omitted_count'] == 20 - len(view['mechanisms']['overview'])
    path = view['mechanisms']['index']; rows=[]
    while path:
        page_path=c.workspace(root,8)/path
        assert page_path.stat().st_mode & 0o222 == 0
        page=c.read(page_path);rows.extend(page['mechanisms']);path=page['next_page']
    assert len(rows)==20 and all('unserved_cycles' in row for row in rows)
    page_path.chmod(0o600);page_path.write_text('{}\n')
    with pytest.raises(ValueError):c._validate_inputs(root,c.workspace(root,8),job)


def test_default_portfolio_preserves_frozen_tasks_without_extra_report_field(tmp_path):
    root = prepare(tmp_path, enabled=False)
    job, _ = author(root, 3, [1, 2], action='wait')
    assert job['handoff_protocol'] == CURRENT and 'portfolio_review' not in c.result_schema('A')['properties']
    paths=[root/'settings.json',root/'jobs/003.json',c.workspace(root,3)/'science-task.json',
           c.host_record(c.workspace(root,3))/'science-result.schema.json',c.host_record(c.workspace(root,3))/'result-identity.json']
    original={path:path.read_bytes() for path in paths}
    assert c.build_job(root,3,'A',[1,2], instruction=job['instruction']) == job
    assert all(path.read_bytes()==raw for path,raw in original.items())
    assert c.build_job(root,4,'H',[3])['handoff_protocol'] == CURRENT
    with pytest.raises(UnsupportedProtocol):
        c.result_schema('A', 10)
    view=c.science_view(job,root)['lineage']['mechanisms']
    assert view['portfolio']['review_after_cycles'] is None and view['portfolio']['review_reminder'] is False
    assert 'portfolio_review' not in job['scientific_response_fields']
    assert 'portfolio_review' not in c.result_schema('A',final_only=True)['properties']
    assert len(mechanism_rows(root,3,view))==4


@pytest.mark.parametrize('bad', [None, 0])
def test_portfolio_requires_model_visible_unified_layout(tmp_path, bad):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Original goal')
    with pytest.raises(ValueError,match='unified lineage'):
        controller.prepare(tmp_path/'instance',objective,public,['never-launched'],max_tokens=1000,max_seconds=60,
            max_steps=8,model='fixture',reasoning_effort='high',lineage_projection_version=bad,
            portfolio_policy=dict(review_after_cycles=3))
    assert not (tmp_path/'instance').exists()


def test_screened_alternative_remains_visible_after_local_improvement(tmp_path):
    root=prepare(tmp_path,threshold=1)
    author(root,3,[1,2],branch=Q,mode='improve',dispositions=[
        dict(mechanism_ref=P,status='screened',reason='The exact observation has been assessed; the mechanism remains scientifically plausible.')])
    worker(root,4,[1,2,3],observed=Q)
    job,_=author(root,5,[1,4],branch=Q,mode='improve')
    view=c.science_view(job,root)['lineage']['mechanisms']
    rows={row['mechanism_ref']:row for row in mechanism_rows(root,5,view)}
    assert rows[P]['status']=='screened' and rows[P]['accepted_calls']==1
    assert rows[P]['unserved_cycles_if_assessed']==1
    assert rows[P]['disposition']['original'].endswith('/003/scientific-original.json')
    assert view['portfolio']['review_reminder'] is True
    state=search.project(root,6)['portfolio']
    assert state['invested']=={P:1,Q:1} and state['deferred'][P]==1
    assert search.project(root,6)['focus']==dict(mechanism_ref=Q,action='improve')
