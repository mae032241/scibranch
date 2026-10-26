"""Current scientific scope and real local W resource guards; no model calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scidiscovery.research_loop import budget, contracts as c, controller, execution, experiments, native, protocol, search
from execution_fixtures import execution_registry
from test_history_handoff import AUTO_COVERAGE, AUTO_PORTFOLIO, OMIT_PORTFOLIO, finish, fixture_final_coverage, fixture_portfolio_review
from test_breadth_science import worker as configure_worker, run as run_command, findings


def plan(label='Mechanism', existing_ref=None, action='screen', other=False):
    rows = [dict(label=label, minimal_test='A bounded discriminating measurement', existing_ref=existing_ref)]
    if other:
        rows.append(dict(label='Unexplored mechanism', minimal_test='Independent negative control', existing_ref=None))
    return dict(scope='Original scientific H scope', commitments=rows, focus=dict(label=label, action=action))


def focus(ref='h001.m1', action='improve'):
    return dict(mechanism_ref=ref, action=action)


def review(ref='h001.m1', status='screened', next_focus=None):
    return dict(dispositions=[dict(mechanism_ref=ref, status=status,
        reason='Scientific judgment includes negative and limited results')], next_focus=next_focus)


@pytest.fixture
def scene(tmp_path, execution_registry):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Resolve mechanisms without assuming proxy optimality.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=10_000_000,
        max_seconds=3600, max_steps=24, model='fake', reasoning_effort='high', node_budget_mode='soft')
    return root


def worker(root, step, parents, *, observed=None, cov=False, qualified_local=False):
    """Registered mechanical backend calls precede observed fixture findings."""
    path = root / 'jobs' / f'{step:03d}.json'
    if path.exists() and not cov and not qualified_local:
        job = c.read(path)
        assert job['role'] == 'W' and [int(row['path'].split('/')[2]) for row in job['direct_refs']] == parents
        c.write(execution.record(root, step) / 'native-intent.json',
            dict(task_ref=c.record_ref(path), search_slice=search.accept(root, job)))
        output = root.parent / f'config-{step}.json'
        experiments.configure(root, step, 'local_execution:command', dict(run_argv=[sys.executable]), output)
        config = c.read(output)
    else:
        job, config = configure_worker(root, root.parent, step, parents, cov=cov, qualified_local=qualified_local)
    mechanisms = config['breadth']['mechanisms']
    if observed is None:
        observed = [job['search']['focus']['mechanism_ref']]
    selected = [row for row in mechanisms if row['mechanism_ref'] in observed or row['label'] in observed]
    assert len(selected) == len(observed)
    for row in selected:
        result = run_command(root, config, row['mechanism_ref'], qualified_local=qualified_local)
        assert result['status'] == 'known_success'
    selected_refs = {row['mechanism_ref'] for row in selected}
    assigned = {row['mechanism_ref'] for row in job['search']['coverage_obligation']}
    screen = job['search']['focus']['action'] == 'screen'
    if screen:
        assert selected_refs <= assigned
    return finish(root, step, 'W', parents, files={'candidate.py': 'def predict(): return 1\n'},
        mechanism_findings=findings(*selected_refs, blocked=sorted(assigned - selected_refs)) if screen else None,
        preserve_native_intent=True)


def assess(root, step, parents, *, action='continue_w', search_choice=None, portfolio_review=AUTO_PORTFOLIO, final_coverage=AUTO_COVERAGE):
    """Seal an authored synthetic A; explicit portfolio values retain negatives."""
    path = root / 'jobs' / f'{step:03d}.json'
    job = c.read(path) if path.exists() else c.build_job(root, step, 'A', parents)
    assert job['role'] == 'A' and [int(row['path'].split('/')[2]) for row in job['direct_refs']] == parents
    record = execution.record(root, step)
    payload = dict(summary='Fixture assessment', scientific_result='Assess retained observations with their scientific limits.',
        limitations=['Mechanical fixture; no scientific effectiveness qualification'], implementation_selection=None,
        candidate_recommendation=None, progress_assessment=None, portfolio_review=None, search_review=search_choice,
        next_action=action, route_reason='An explicit scientific question determines the next fixture contribution',
        decision_request=dict(decision='mechanism', question=f'Which declared mechanism should receive contribution {step}?',
            change_criterion='A new trustworthy discriminating observation would change the mechanism choice')
            if action in {'continue_w', 'revise_h'} else None)
    fixture_portfolio_review(root, job, payload, portfolio_review)
    fixture_final_coverage(root, job, payload, final_coverage)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs' / f'{step:03d}.json')))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False, usage=native.usage(root),
        raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, step)
    return job, c.workspace(root, step), record


def start(root, other=False):
    finish(root, 1, 'H', [], action='experiment', search_choice=plan(other=other))
    return worker(root, 2, [1])


def control(root, step):
    return c.read(execution.record(root, step) / 'result-identity.json')['control_outcome']


def test_normal_scientific_screen_then_improve_has_small_views(scene):
    start(scene)
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus()))
    job = c.build_job(scene, 4, 'W', [1, 2, 3])
    assert search.launch_reason(scene, job) is None
    assert job['search']['focus']['action'] == 'improve'
    state = search.project(scene, 4)
    assert len(state['accepted_slices']) == 1
    assert state['accepted_slices'][0]['candidate_coverage'] == 'partial'
    assert state['accepted_slices'][0]['observed_new_candidates'] == 0
    packet = c.science_view(job, scene)
    assert 'search' not in packet and 'lineage' in packet
    assert len((json.dumps(packet['lineage'], ensure_ascii=False, indent=2) + '\n').encode()) <= 4096
    worker(scene, 4, [1, 2, 3])
    assess(scene, 5, [1, 4], action='finish')
    assert len(search.project(scene, 6)['accepted_slices']) == 2


def test_pending_old_ref_survives_h_omission_and_rename(scene):
    start(scene, other=True)
    assess(scene, 3, [1, 2], action='revise_h', search_choice=review())
    finish(scene, 4, 'H', [1, 2, 3], action='experiment', search_choice=plan('Renamed', 'h001.m1', 'improve'))
    job = c.build_job(scene, 5, 'W', [4])
    state = search.project(scene, 5)
    assert state['mechanisms']['h001.m2']['status'] == 'pending'
    assert state['mechanisms']['h001.m1']['label'] == 'Renamed'
    assert state['mechanisms']['h001.m1']['original_h'] == 1
    assert len(state['mechanisms']) == 2
    # An explicit authorized deepen is legal; initial-call fairness is separate.
    assert search.launch_reason(scene, job) is None
    assert job['search']['focus'] == focus(action='improve')


def test_h_reauthorizes_exact_pending_ref_for_screen(scene):
    start(scene, other=True)
    assess(scene, 3, [1, 2], action='revise_h', search_choice=review())
    finish(scene, 4, 'H', [1, 2, 3], action='experiment', search_choice=plan('Renamed pending', 'h001.m2'))
    job = c.build_job(scene, 5, 'W', [4])
    assert job['search']['focus']['mechanism_ref'] == 'h001.m2'
    assert search.project(scene, 5)['mechanisms']['h001.m2']['source_h'] == 4
    assert search.launch_reason(scene, job) is None


def test_negative_or_infeasible_judgment_can_waive_without_new_w(scene):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan(other=True))
    assess(scene, 2, [1], search_choice=review('h001.m2', status='waived', next_focus=focus(action='screen')))
    job = c.build_job(scene, 3, 'W', [1, 2])
    assert search.project(scene, 3)['mechanisms']['h001.m2']['status'] == 'waived'
    assert search.launch_reason(scene, job) is None
    assert not list(scene.glob('project/nodes/*/work/lineage/calls/*/accepted.json'))


def test_disposition_sources_follow_only_adopted_status_changes(scene):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan(other=True))
    choices = dict(dispositions=[
        dict(mechanism_ref='h001.m1', status='deferred', reason='The discriminating input is unavailable.'),
        # Control retains this weak scientific judgment; it does not grade reasons.
        dict(mechanism_ref='h001.m2', status='waived', reason='Its current proxy score is lower.')], next_focus=None)
    assess(scene, 2, [1], action='finish', search_choice=choices)
    original = (execution.record(scene, 2) / 'science-output.json').read_bytes()
    adopted = copy.deepcopy(search.project(scene, 3)['mechanisms'])
    for item in choices['dispositions']:
        assert adopted[item['mechanism_ref']]['disposition'] == dict(reason=item['reason'], source_a=2)
    restated = copy.deepcopy(choices)
    for item in restated['dispositions']:
        item['reason'] = 'A later restatement must not replace the adopted reason.'
    assess(scene, 3, [1, 2], action='finish', search_choice=restated)
    assert search.project(scene, 4)['mechanisms'] == adopted
    # One valid change cannot leak out of an interpretation with an unresolved
    # screened claim for the unexecuted second mechanism.
    rejected = dict(dispositions=[dict(mechanism_ref='h001.m1', status='pending', reason='Reopen'),
        dict(mechanism_ref='h001.m2', status='screened', reason='No supporting W exists')], next_focus=None)
    assess(scene, 4, [1, 3], action='finish', search_choice=rejected)
    assert control(scene, 4)['search_status'] == 'unresolved'
    assert search.project(scene, 5)['mechanisms'] == adopted
    assess(scene, 5, [1, 4], action='finish', search_choice=dict(dispositions=[], next_focus=None))
    assert search.project(scene, 6)['mechanisms'] == adopted
    assert (execution.record(scene, 2) / 'science-output.json').read_bytes() == original


def test_h_revision_clears_current_disposition_without_refunding_calls(scene):
    start(scene, other=True)
    assess(scene, 3, [1, 2], action='revise_h', search_choice=review(status='deferred'))
    before = search.project(scene, 4)
    assert before['mechanisms']['h001.m1']['disposition']['source_a'] == 3
    original = (execution.record(scene, 3) / 'science-output.json').read_bytes()
    finish(scene, 4, 'H', [1, 2, 3], action='experiment',
        search_choice=plan('Revised mechanism', 'h001.m1', 'improve'))
    after = search.project(scene, 5)
    assert after['mechanisms']['h001.m1']['status'] == 'pending'
    assert 'disposition' not in after['mechanisms']['h001.m1']
    assert after['opportunity_counts'] == before['opportunity_counts'] == {'h001.m1': 1}
    job = c.build_job(scene, 5, 'W', [4])
    assert search.launch_reason(scene, job) is None
    assert job['search']['focus'] == focus(action='improve')
    assert (execution.record(scene, 3) / 'science-output.json').read_bytes() == original


def test_screened_requires_same_mechanism_terminal_observation(scene):
    start(scene, other=True)
    assess(scene, 3, [1, 2], search_choice=review('h001.m2', next_focus=focus()))
    assert control(scene, 3)['search_status'] == 'unresolved'
    assert any(row['code'] == 'screen_source_unavailable' for row in control(scene, 3)['diagnostics'])
    state = search.project(scene, 4)
    assert state['mechanisms']['h001.m2']['status'] == 'pending'
    assert c.validate(c.workspace(scene, 3))['search_review']['dispositions'][0]['mechanism_ref'] == 'h001.m2'


def test_screened_state_carries_while_screening_another_mechanism(scene):
    start(scene, other=True)
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus('h001.m2', 'screen')))
    worker(scene, 4, [1, 2, 3], observed=['h001.m2'])
    choice = review();choice['dispositions'] += review('h001.m2')['dispositions']
    assess(scene, 5, [1, 4], action='finish', search_choice=choice)
    assert {ref: row['status'] for ref, row in search.project(scene, 6)['mechanisms'].items()} == {
        'h001.m1': 'screened', 'h001.m2': 'screened'}
    assert c.validate(c.workspace(scene, 5))['next_action'] == 'finish'
    assert not c.read(scene / 'jobs/005.json').get('selected_inputs')
    assert (c.workspace(scene, 5) / 'inputs/parents/004/artifacts/candidate.py').is_file()
    assert not (c.workspace(scene, 5) / 'inputs/parents/002/artifacts/candidate.py').exists()


@pytest.mark.parametrize('restate', [True, False])
def test_a_reports_only_changes_and_restatements_do_not_replace_originals(scene, restate):
    choice = plan(other=True)
    choice['commitments'] += [dict(label=label, minimal_test='Bounded screen', existing_ref=None)
        for label in ('Heavy tail', 'Supervised regression')]
    finish(scene, 1, 'H', [], action='experiment', search_choice=choice)
    worker(scene, 2, [1])
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus('h001.m2', 'screen')))
    worker(scene, 4, [1, 2, 3], observed=['h001.m2'])
    assess(scene, 5, [1, 4], search_choice=review('h001.m2', next_focus=focus('h001.m4', 'screen')))
    worker(scene, 6, [1, 4, 5], observed=['h001.m4'])
    original = (execution.record(scene, 3) / 'science-output.json').read_bytes()
    choice = review('h001.m4', next_focus=focus('h001.m3', 'screen'))
    if restate:
        choice['dispositions'] += review()['dispositions'] + review('h001.m2')['dispositions']
        choice['dispositions'] += review('h001.m3', status='pending')['dispositions']
    assess(scene, 7, [1, 6], search_choice=choice)
    state = search.project(scene, 8)
    assert [state['mechanisms'][f'h001.m{i}']['status'] for i in range(1, 5)] == ['screened', 'screened', 'pending', 'screened']
    assert state['focus'] == focus('h001.m3', 'screen')
    assert (execution.record(scene, 3) / 'science-output.json').read_bytes() == original
    assert c.read(execution.record(scene, 7) / 'science-output.json')['search_review'] == choice


@pytest.mark.parametrize('forged_view', [False, True])
def test_first_screened_cannot_use_a_only_claim_or_forged_view(scene, forged_view):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan())
    assess(scene, 2, [1], search_choice=dict(dispositions=[], next_focus=focus(action='screen')))
    worker(scene, 3, [1, 2], observed=[])
    job = c.build_job(scene, 4, 'A', [1, 3])
    if forged_view:
        job = copy.deepcopy(job)
        job['history']['view']['mechanisms']['pending_count'] = 0
    diagnostics = search.assess_payload(scene, job, dict(next_action='finish', route_reason='Fixture assessment', search_review=review()))
    assert any(row['code'] == 'screen_source_unavailable' for row in diagnostics)
    assert search.project(scene, 4)['mechanisms']['h001.m1']['status'] == 'pending'
    assert not (execution.record(scene, 4) / 'result-identity.json').exists()


def test_improve_action_with_real_same_mechanism_finding_can_be_screened(scene):
    finish(scene, 1, 'H', [], action='analyze', search_choice=plan())
    assess(scene, 2, [1], search_choice=review(status='deferred', next_focus=focus()))
    worker(scene, 3, [1, 2])
    assert c.read(scene / 'jobs/003.json')['search']['focus']['action'] == 'improve'
    assess(scene, 4, [1, 3], action='finish', search_choice=review())
    assert search.project(scene, 5)['mechanisms']['h001.m1']['status'] == 'screened'


@pytest.mark.parametrize('tampered_history', [False, True])
def test_restatement_is_noop_but_frozen_history_still_validates(scene, tampered_history):
    start(scene)
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus()))
    worker(scene, 4, [1, 2, 3], observed=[])
    job = c.build_job(scene, 5, 'A', [1, 4])
    if tampered_history:
        page = c.workspace(scene, 5) / job['history']['files'][0]['path']
        page.chmod(0o600);page.write_text('{}')
        with pytest.raises(ValueError, match='history page changed'):
            c._validate_inputs(scene, c.workspace(scene, 5), job)
    else:
        # Repeating an already adopted status does not create another W proof.
        assert search.assess_payload(scene, job, dict(next_action='finish', route_reason='Restate', search_review=review())) == []
    assert not (execution.record(scene, 5) / 'result-identity.json').exists()


@pytest.mark.parametrize('old_protocol', [6, 7, 19])
def test_retired_write_schema_is_rejected_without_changing_current_packets(scene, old_protocol):
    start(scene)
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus()))
    paths = [scene / 'jobs/003.json', c.workspace(scene, 3) / 'science-task.json', execution.record(scene, 3) / 'science-result.schema.json']
    originals = [path.read_bytes() for path in paths]
    with pytest.raises(protocol.UnsupportedProtocol):
        c.result_schema('A', old_protocol)
    assert c.build_job(scene, 3, 'A', [1, 2])['handoff_protocol'] == protocol.CURRENT
    assert [path.read_bytes() for path in paths] == originals
    c.validate(c.workspace(scene, 3))


def test_reopened_pending_cannot_be_screened_by_a_claim_without_new_w_observation(scene):
    start(scene)
    assess(scene, 3, [1, 2], search_choice=review(next_focus=focus()))
    worker(scene, 4, [1, 2, 3])
    assess(scene, 5, [1, 4], search_choice=review(status='pending', next_focus=focus(action='screen')))
    worker(scene, 6, [1, 4, 5], observed=[])
    assert search.project(scene, 7)['mechanisms']['h001.m1']['status'] == 'pending'
    assess(scene, 7, [1, 6], action='finish', search_choice=review())
    assert control(scene, 7)['search_status'] == 'unresolved'
    assert search.project(scene, 8)['mechanisms']['h001.m1']['status'] == 'pending'


def test_null_review_stops_w_intent_without_forcing_recommendation(scene):
    start(scene)
    assess(scene, 3, [1, 2])
    job = c.build_job(scene, 4, 'W', [1, 2, 3])
    assert search.launch_reason(scene, job) == 'science_task_action_required'
    assert c.read(execution.record(scene, 3) / 'science-output.json')['candidate_recommendation'] is None


def test_exact_requested_repair_chain_is_bounded_and_restart_deduplicated(scene):
    start(scene)
    for a_step, w_step, source_step in [(3, 4, 2), (5, 6, 4)]:
        assess(scene, a_step, [1, source_step], search_choice=dict(dispositions=[], next_focus=focus(action='repair')))
        job, _, _ = worker(scene, w_step, [1, source_step, a_step])
        assert job['search']['repair_root'] == 2
        assert search.launch_reason(scene, job) is None
    assess(scene, 7, [1, 6], search_choice=dict(dispositions=[], next_focus=focus(action='repair')))
    job = c.build_job(scene, 8, 'W', [1, 6, 7])
    assert search.launch_reason(scene, job) == 'search_repair_limit'
    assert search.project(scene, 8)['repairs'] == {2: 2}
    assert search.project(scene, 8)['repairs'] == {2: 2}
    assert search.project(scene, 8)['mechanisms']['h001.m1']['status'] == 'pending'


def test_current_dispositions_and_repair_requests_do_not_accept_authored_paths():
    schema = c.result_schema('A', protocol.CURRENT)
    review_schema = schema['properties']['search_review']['properties']
    assert set(review_schema['dispositions']['items']['properties']) == {'mechanism_ref', 'status', 'reason'}
    assert set(review_schema['next_focus']['properties']) == {'mechanism_ref', 'action'}


def test_same_boot_slice_anchor_paused_time_and_cached_tokens(scene, monkeypatch):
    start(scene)
    job = c.read(scene / 'jobs/002.json')
    now = [100.]
    monkeypatch.setattr(budget.time, 'clock_gettime', lambda _: now[0])
    monkeypatch.setattr(budget.time, 'time', lambda: now[0])
    c.write(scene / 'host-started.json', budget.clock_stamp())
    budget.transition(scene, 'advance', 'test')
    anchor = search.accept(scene, job)
    intent = dict(search_slice=anchor)
    meter = dict(input_tokens=1_000_000, cached_input_tokens=999_999, output_tokens=0)
    now[0] += 3
    budget.inactive(scene, 'controller paused after accepted test work')
    now[0] += 5000
    budget.transition(scene, 'advance', 'same original test work')
    assert search.stop_reason(scene, job, intent, budget.snapshot(scene), meter) is None
    assert search.report(scene, job, intent, meter)['advance_seconds'] == 3
    meter['output_tokens'] = 1_000_000
    assert search.stop_reason(scene, job, intent, budget.snapshot(scene), meter) is None
    now[0] += 900
    assert search.stop_reason(scene, job, intent, budget.snapshot(scene), meter) is None
    assert intent['search_slice'] == anchor


FAKE_NATIVE = '''import json,os,pathlib,sys,time
args=sys.argv[1:];sys.stdin.read()
work=pathlib.Path(os.environ['THIN_HWA_WORKSPACE']);role=os.environ['THIN_HWA_ROLE']
job=json.loads((work/'science-task.json').read_text());step=int(work.name)
mode=os.environ.get('N3_FIXTURE_MODE','normal')
payload=dict(summary='Scientific fixture result',scientific_result='Blocked mechanical fixture; no scientific effectiveness claim.',limitations=['No model or scientific qualification'],implementation_selection=None)
if role=='H':
 payload.update(next_action='experiment',route_reason='Test the declared route',search_plan=dict(scope='Bounded H scope',commitments=[dict(label='Mechanism',minimal_test='Discriminating measurement',existing_ref=None)],focus=dict(label='Mechanism',action='screen')))
elif role=='W':
 manifest=json.loads(pathlib.Path(os.environ['THIN_HWA_JOB_MANIFEST']).read_text())
 payload.update(stage_intent='review',mechanism_findings=[dict(mechanism='Mechanism',basis='blocked',finding='This transport fixture runs no managed scientific observation.')],
  candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
   reason='This transport fixture produces no scientific candidate to compare.')
   for row in manifest['candidate_nomination_mechanisms'] if row['status']!='waived'])
   if manifest.get('candidate_nomination_mechanisms') else None)
elif role=='A':
 active=bool(job.get('lineage',{}).get('mechanisms',{}).get('count'))
 again=active and step==3
 payload.update(next_action='continue_w' if again else 'finish',route_reason='Ask a distinct followup without claiming completed screening',candidate_recommendation=None,progress_assessment=None,portfolio_review=None,
  search_review=dict(dispositions=[dict(mechanism_ref='h001.m1',status='deferred',reason='Blocked fixture is not screened')],next_focus=dict(mechanism_ref='h001.m1',action='improve')) if again else None,
  decision_request=dict(decision='mechanism',question='Can a followup resolve the declared obstacle?',change_criterion='A trustworthy discriminating observation would change the mechanism choice') if again else None)
(work/'artifacts/observation.txt').write_text('Retained mechanical fixture limitation')
if role=='W':
 (work/'work/launches.txt').write_text('one accepted native process')
 if mode=='time':time.sleep(10)
 if mode=='tokens':
  print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=101,cached_input_tokens=100,output_tokens=1))),flush=True);time.sleep(10)
pathlib.Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(payload))
print(json.dumps(dict(type='turn.completed',usage=dict(input_tokens=7,cached_input_tokens=2,output_tokens=3))),flush=True)
'''


def process_instance(tmp_path, mode='normal'):
    public = tmp_path / 'public';public.mkdir()
    objective = public / 'objective.md';objective.write_text('Bounded original question')
    launcher = tmp_path / 'native.py';launcher.write_text(FAKE_NATIVE)
    root = tmp_path / 'instance'
    limits = dict(search.DEFAULT_POLICY)
    if mode == 'time':limits['max_w_seconds'] = 1.5
    if mode == 'tokens':limits['max_w_observed_tokens'] = 100
    controller.prepare(root, objective, public, [sys.executable, str(launcher)], max_tokens=100_000,
        max_seconds=60, max_steps=6, model='fixture', reasoning_effort='high', node_budget_mode='soft',
        search_policy=None if mode == 'disabled' else limits)
    return root


def test_real_native_five_roles_complete_short_steps_without_root(tmp_path):
    root = process_instance(tmp_path)
    result = controller.run(root)
    assert result['status'] == 'finish', result
    assert result['completed_steps'] == 5
    assert search.project(root, 6)['accepted_slices'][0]['meter']['observed_tokens'] == 10
    assert search.project(root, 6)['accepted_slices'][1]['meter']['dispatches'] == 1
    assert controller.run(root) == result




def test_utf8_long_scientific_commitments_keep_default_view_bounded(scene):
    value = plan()
    value['scope'] = '原始科学问题与适用条件' * 200
    value['commitments'] = [dict(label='机制' + str(i) + '文' * 90, minimal_test='科学' * 900, existing_ref=None) for i in range(6)]
    value['focus']['label'] = value['commitments'][0]['label']
    finish(scene, 1, 'H', [], action='experiment', search_choice=value)
    assert c.validate(c.workspace(scene, 1))['search_plan'] == value
    job = c.build_job(scene, 2, 'W', [1])
    packet = c.science_view(job, scene)
    assert len((json.dumps(packet['lineage'], ensure_ascii=False, indent=2) + '\n').encode()) <= 4096
    assert len((json.dumps(job['history']['view'], ensure_ascii=False, indent=2) + '\n').encode()) <= 4096
    assert packet['lineage']['mechanisms']['count'] == 6
    assert (c.workspace(scene, 2) / packet['lineage']['indexes']['mechanisms']).is_file()


def test_a_can_seal_more_than_four_scientific_dispositions_with_full_reasons(scene):
    value = plan()
    value['commitments'] = [dict(label=f'Mechanism {index}', minimal_test='Independent comparison', existing_ref=None)
        for index in range(6)]
    value['focus']['label'] = value['commitments'][0]['label']
    finish(scene, 1, 'H', [], action='analyze', search_choice=value)
    choice = dict(dispositions=[dict(mechanism_ref=f'h001.m{index}', status='deferred',
        reason='保留尚未测量的机制及其科学限制' * 200) for index in range(1, 7)], next_focus=None)
    assess(scene, 2, [1], action='finish', search_choice=choice)
    assert c.validate(c.workspace(scene, 2))['search_review'] == choice
    state = search.project(scene, 3)
    assert len(state['mechanisms']) == 6
    assert {row['status'] for row in state['mechanisms'].values()} == {'deferred'}


def test_missing_h_plan_preserves_science_and_stops_w_admission(scene):
    finish(scene, 1, 'H', [], action='experiment')
    job = c.build_job(scene, 2, 'W', [1])
    assert control(scene, 1)['search_status'] == 'unresolved'
    assert search.launch_reason(scene, job) == 'science_task_action_required'
    with pytest.raises(ValueError, match='frozen search authorization'):
        search.accept(scene, job)
    assert c.validate(c.workspace(scene, 1))['search_plan'] is None
    assert not (execution.record(scene, 2) / 'native-intent.json').exists()


def test_e_route_keeps_requester_and_mechanism_identity(scene, monkeypatch):
    def execute(root, step):
        job = c.read(root / 'jobs' / f'{step:03d}.json')
        parents = [int(Path(row['path']).parts[2]) for row in job['direct_refs']]
        if step == 1:finish(root, step, 'H', parents, action='extract', search_choice=plan())
        elif step == 2:
            assert job['evidence_return'] == dict(role='H', step=1)
            finish(root, step, 'E', parents)
        elif step == 3:finish(root, step, 'H', parents, action='experiment', search_choice=plan(existing_ref='h001.m1'))
        elif step == 4:worker(root, step, parents)
        else:assess(root, step, parents, action='finish', search_choice=review())
    monkeypatch.setattr(native, 'execute_native', execute)
    result = controller.run(scene)
    assert result['status'] == 'finish', result
    assert len(search.project(scene, 6)['mechanisms']) == 1


def test_current_settings_without_policy_complete_without_search_gates(tmp_path):
    root = process_instance(tmp_path, 'disabled')
    assert 'search_policy' not in c.settings(root)
    result = controller.run(root)
    assert result['status'] == 'finish', result
    assert result['completed_steps'] == 3
    for step in range(1, 4):
        assert 'search' not in c.read(root / 'jobs' / f'{step:03d}.json')
        assert 'search_slice' not in c.read(c.host_record(c.workspace(root, step)) / 'native-intent.json')
