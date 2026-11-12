"""Exact implementation reuse through real thin seals; no model calls."""
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import contracts, controller, guidance, native


@pytest.fixture
def scene(tmp_path):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Test a revised mechanism against a retained implementation.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fake'], max_tokens=1000,
        max_seconds=60, max_steps=6, model='fake', reasoning_effort='high',
        search_policy=None,
        max_output_item_bytes=64, max_output_total_bytes=96)
    return root


def selection_explanation(criteria='Compare the declared fixture observations within their stated limitations.'):
    return dict(criteria=criteria,criteria_change=dict(status='unchanged',prior_criteria=None,
        changes=None,evidence=None,accepted_tradeoffs=None))


DEFAULT_ACTION = object()
DEFAULT_CONSULTATION = object()
AUTO_PORTFOLIO = object()
OMIT_PORTFOLIO = object()
AUTO_COVERAGE = object()
OMIT_COVERAGE = object()


def fixture_portfolio_review(root, job, payload, value=AUTO_PORTFOLIO):
    """Author a synthetic control fixture; explicit values retain negative cases.

    The context establishes only applicability and exact visible identities.
    These fixed scientific assumptions are test data, never inferred from calls
    or their success. Tests with declared discriminating work must supply their
    own authored update rather than receive an automatic scientific disposition.
    """
    if job['role'] != 'A':
        return
    if value is OMIT_PORTFOLIO:
        payload.pop('portfolio_review', None)
        return
    if value is AUTO_PORTFOLIO:
        from scidiscovery.research_loop import search
        context = search.assessment_context(root, job)
        value = None
        if context['applicable'] and payload['next_action'] != 'wait':
            assert not [row for row in context['discriminating_work'] if row['status'] != 'withdrawn'], (
                'Supply explicit fixture portfolio_review for declared discriminating work')
            resolved = search.interpret(root, job, payload)['focus']
            value = dict(main_line=resolved['mechanism_ref'] if resolved else None,
                discriminating_work=None,
                reason='Synthetic fixture assumes no feasible competing discriminator; missing scientific evidence prevents qualification. Revisit when that evidence is supplied.')
    payload['portfolio_review'] = value



def fixture_final_coverage(root, job, payload, value=AUTO_COVERAGE):
    """Explicit synthetic A declaration, not a depth inferred from executions.

    AUTO is positive fixture data only. None and OMIT preserve negative controls;
    full science fixtures should supply their own authored coverage claims.
    """
    if job['role'] != 'A':
        return
    if value is OMIT_COVERAGE:
        payload.pop('final_coverage', None)
        return
    if value is AUTO_COVERAGE:
        value = None
        if job.get('final_coverage_mechanisms') and (payload.get('next_action') in {'finish', 'deliver'}
                or job.get('final_report') or job.get('purpose') == 'closeout' or job.get('final_only')):
            value = [dict(mechanism_ref=row['mechanism_ref'], status='unexplored',
                reason='Synthetic fixture declares no scientific exploration depth; scientific effectiveness remains unanswered.')
                for row in job['final_coverage_mechanisms']]
    payload['final_coverage'] = value


def finish(root, step, role, parents, text='A bounded scientific finding', action=DEFAULT_ACTION, files=None, summary='A contribution', limitations=None, recommendation=None, search_choice=None, progress=None, selection=None, mechanism_findings=None, candidate_nominations=None, preserve_native_intent=False, portfolio_review=AUTO_PORTFOLIO, final_coverage=AUTO_COVERAGE, stage_intent='decision', consultation_question=DEFAULT_CONSULTATION):
    # These route fixtures request ordinary A assessment; review is W consultation.
    parents=list(parents)
    path = root / 'jobs' / f'{step:03d}.json'
    previous = contracts.read(path) if path.exists() else {}
    job = contracts.build_job(root, step, role, parents, previous.get('instruction', ''),
                              previous.get('evidence_return'), previous.get('correction_request'), previous.get('author_correction'))
    if action is DEFAULT_ACTION:
        action = 'continue_w' if role == 'A' else 'wait'
    work = contracts.workspace(root, step)
    record = contracts.host_record(work)
    payload = dict(summary=summary, scientific_result=text, limitations=limitations if limitations is not None else ['Fixture only'])
    payload['implementation_selection'] = selection
    if role == 'A' or recommendation is not None:
        payload['candidate_recommendation'] = recommendation
        payload['selection_explanation'] = selection_explanation() if recommendation is not None else None
    if role in {'H', 'A'}:
        payload['search_plan' if role == 'H' else 'search_review'] = search_choice
    if role == 'A':
        payload['progress_assessment'] = progress
    if role=='W':
        if consultation_question is DEFAULT_CONSULTATION:
            consultation_question = ('Which retained counterevidence changes the unfinished fixture question?'
                if stage_intent in {'review','revise_h','extract'} else None)
        payload['consultation_question'] = consultation_question
        if candidate_nominations is None and job.get('candidate_nomination_mechanisms'):
            candidate_nominations=dict(representatives=[],extras=[],gaps=[dict(mechanism_ref=row['mechanism_ref'],
                reason='Fixture declares no scientific candidate suitability; the retained pool remains available to A.')
                for row in job['candidate_nomination_mechanisms'] if row['status']!='waived'])
        payload.update(stage_intent=stage_intent,mechanism_findings=mechanism_findings,candidate_nominations=candidate_nominations)
    if role=='A':
        payload.update(decision_request=(dict(decision='mechanism',
            question='Does the retained fixture evidence address the original scientific question?',
            change_criterion='A relevant retained observation resolving the stated evidence gap changes the assessment.')
            if action in {'continue_w','revise_h','extract'} and job.get('purpose') is None
            and not guidance.advisory_assessment(job) else None),portfolio_review=None)
    if role in {'H', 'A'}:
        payload.update(next_action=action, route_reason='Exact scientific question')
    fixture_portfolio_review(root, job, payload, portfolio_review)
    fixture_final_coverage(root, job, payload, final_coverage)
    for name, contents in (files or {}).items():
        path = work / 'artifacts' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    task_ref=contracts.record_ref(root / 'jobs' / f'{step:03d}.json')
    if preserve_native_intent:
        assert contracts.read(contracts.regular(record/'native-intent.json'))['task_ref']==task_ref
    else:
        contracts.write(record / 'native-intent.json', dict(task_ref=task_ref))
    from scidiscovery.research_loop import accepted_budget
    accepted_budget.accept_native(root,step,record,contracts.read(contracts.regular(record/'native-intent.json')))
    contracts.write(record / 'science-output.json', payload)
    contracts.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    contracts.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None, native_started=False,
        usage=native.usage(root), raw_ref=contracts.file_ref(record / 'science-output.json')))
    contracts.seal_result(root, step)
    return job, work, record


def source(scene, files=None):
    finish(scene, 1, 'H', [], action='experiment')
    return finish(scene, 2, 'W', [1], files=files or {'candidate.py': 'def predict(): return 42\n', 'unused.txt': 'Do not forward me'})


REUSED = 'inputs/parents/002/artifacts/candidate.py'
MARKED = f'[retained implementation]({REUSED}#reuse-implementation)'


def test_real_scientific_route_reuses_only_selected_file_without_ancestry(scene, monkeypatch):
    from test_direct_input_bindings import stopped_seals
    from scidiscovery.research_loop import originals
    stopped_seals(monkeypatch)
    retained='inputs/parents/001/artifacts/candidate.py'
    calls=[]
    def execute(root, step):
        job=contracts.read_job(root/'jobs'/f'{step:03d}.json')
        role=job['role'];calls.append(role)
        parents=[int(Path(ref['path']).parts[2]) for ref in job['direct_refs']]
        if step==4:
            work=contracts.workspace(root,step)
            declared=[row for row in job['direct_materials'] if row['path']==retained]
            assert len(declared)==1
            assert originals.material_path(root,declared[0]).read_text()=='def predict(): return 42\n'
            assert not (work/retained).exists()
            assert not (work/Path(retained).parent/'unused.txt').exists()
            assert [ref['path'] for ref in job['direct_refs']]==[
                'inputs/parents/003/conclusion.json','inputs/parents/002/conclusion.json']
            view=contracts.read(work/'science-task.json')
            assert view['reused_implementations']==[dict(label='W001-I001',path=retained)]
            assert 'sha256' not in json.dumps(view) and 'origin_result' not in json.dumps(view)
        finish(root,step,role,parents,text=f'[retained implementation]({retained}#reuse-implementation)' if step==3 else 'Bounded retained finding',
            action='wait' if role=='H' else 'finish' if role=='A' else DEFAULT_ACTION,
            stage_intent={1:'handoff',2:'revise_h',4:'decision'}.get(step,'decision'),
            files={'candidate.py':'def predict(): return 42\n','unused.txt':'Do not forward me'} if step==1 else None,
            selection=['W001-I001'] if step==2 else [] if step==4 else None)
    monkeypatch.setattr(native,'execute_native',execute)
    result=controller.run(scene)
    assert result['status']=='finish' and calls==['W','W','H','W','A'],result
    final=contracts.workspace(scene,5)
    assert not (final/retained).exists()
    assert contracts.read(contracts.host_record(final)/'result-identity.json').get('selected_inputs',[])==[]
    assert controller.run(scene)['status']=='finish'


def test_structured_selection_inherits_and_deduplicates_without_ancestry(scene,monkeypatch):
    from test_direct_input_bindings import stopped_seals
    stopped_seals(monkeypatch)
    source(scene)
    finish(scene, 3, 'W', [2], stage_intent='handoff', text=MARKED + ' ' + MARKED, selection=['W002-I001', 'W002-I001'])
    job, work, _ = finish(scene, 4, 'H', [3], text=MARKED)
    from scidiscovery.research_loop import originals
    assert len(job['selected_inputs']) == 1
    selected=job['selected_inputs'][0]['material']
    assert originals.material_path(scene,selected)==contracts.workspace(scene,2)/'artifacts/candidate.py'
    assert not (work/REUSED).exists()
    successor = contracts.build_job(scene, 5, 'W', [4])
    assert len(successor['selected_inputs']) == 1
    assert len([row for row in successor['direct_materials'] if row['path'] == REUSED]) == 1
    assert not any(row['path'].endswith('/unused.txt') for row in successor['direct_materials'])


@pytest.mark.parametrize('target', [
    'work/candidate.py', 'inputs/parents/002/artifacts/unused-missing.py',
    'inputs/parents/002/artifacts/../candidate.py',
    'inputs/parents/002/artifacts/%2e%2e/candidate.py',
    '/etc/passwd', 'https://example.org/code.py', REUSED + '?latest=1',
])
def test_old_marker_paths_do_not_select_or_grant_input_authority(scene, target):
    source(scene)
    _, work, record = finish(scene, 3, 'A', [2], text=f'[implementation]({target}#reuse-implementation)')
    seal = contracts.read(record / 'result-identity.json')
    assert not seal.get('selected_inputs') and not seal['entry_points']
    assert contracts.validate(work)['scientific_result'] == f'[implementation]({target}#reuse-implementation)'
    successor = contracts.build_job(scene, 4, 'H', [3])
    assert {row['path'] for row in successor['direct_materials']} == {
        REUSED, 'inputs/parents/002/artifacts/unused.txt'}
    assert not successor.get('selected_inputs')  # Null relays inputs; prose selects nothing.


def test_ordinary_citation_leaves_the_null_material_relay_unchanged(scene):
    source(scene)
    finish(scene, 3, 'A', [2], text=f'[citation]({REUSED})')
    job = contracts.build_job(scene, 4, 'H', [3])
    assert {row['path'] for row in job['direct_materials']} == {
        REUSED, 'inputs/parents/002/artifacts/unused.txt'}
    assert not job.get('selected_inputs')


@pytest.mark.parametrize('changed', ['original', 'receipt'])
def test_changed_bound_reuse_is_rejected(scene, changed):
    source(scene)
    _, work, record = finish(scene, 3, 'W', [2], stage_intent='handoff', text=MARKED, selection=['W002-I001'])
    if changed == 'receipt':
        seal = contracts.read(record / 'result-identity.json')
        seal['selected_inputs'][0]['material_ref']['list_ref']['path'] = str(scene.parent / 'foreign/result-identity.json')
        contracts.write(record / 'result-identity.json', seal)
    else:
        path = contracts.workspace(scene, 2) / 'artifacts/candidate.py'
        path.chmod(0o600)
        path.write_text('different implementation')
    with pytest.raises(ValueError):
        contracts.build_job(scene, 4, 'H', [3])


def test_worker_created_alias_does_not_replace_selected_original(scene):
    from scidiscovery.research_loop import originals
    source(scene)
    _, work, _ = finish(scene, 3, 'W', [2], stage_intent='handoff', text=MARKED, selection=['W002-I001'])
    alias = work / REUSED
    assert not alias.exists()
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.write_text('A worker-owned alias cannot replace the retained producer.')
    job = contracts.build_job(scene, 4, 'H', [3])
    assert len(job['direct_materials']) == 1
    selected = originals.material_path(scene, job['direct_materials'][0])
    assert selected == contracts.workspace(scene, 2) / 'artifacts/candidate.py'
    assert selected.read_text() == 'def predict(): return 42\n'


def test_structured_selection_counts_all_unique_files_beyond_one_page(scene):
    source(scene, {f'file{i}.py': 'x' for i in range(17)})
    finish(scene, 3, 'W', [2], stage_intent='handoff', selection=[f'W002-I{index:03d}' for index in range(1, 18)])
    job = contracts.build_job(scene, 4, 'H', [3])
    assert len(job['selected_inputs']) == len(job['direct_materials']) == 17
    assert sum(row['size_bytes'] for row in job['direct_materials']) == 17


def test_input_view_publication_rejects_destination_directory_link(scene, tmp_path):
    source(scene)
    finish(scene, 3, 'W', [2], stage_intent='handoff', text=MARKED, selection=['W002-I001'])
    work = contracts.workspace(scene, 4)
    work.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (work / 'inputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='original owned workspace'):
        contracts.build_job(scene, 4, 'H', [3])
    assert not list(outside.iterdir())


def test_final_input_bound_includes_new_outputs_and_selected_old_files(scene):
    from scidiscovery.research_loop import originals
    source(scene, {'candidate.py': 'x' * 48})
    finish(scene,3,'W',[2],stage_intent='handoff',text=MARKED,
        selection=['W002-I001'],files={'revision.txt':'y'*64})
    paths={REUSED:contracts.workspace(scene,2)/'artifacts/candidate.py',
        'inputs/parents/003/artifacts/revision.txt':contracts.workspace(scene,3)/'artifacts/revision.txt'}
    before={path:(contracts.file_ref(path),path.read_bytes()) for path in paths.values()}
    job=contracts.build_job(scene,4,'H',[3])
    assert {row['path']:row['size_bytes'] for row in job['direct_materials']}=={
        REUSED:48,'inputs/parents/003/artifacts/revision.txt':64}
    assert sum(row['size_bytes'] for row in job['direct_materials'])==112
    assert [row['material']['path'] for row in job['selected_inputs']]==[REUSED]
    for row in job['direct_materials']:
        path=originals.material_path(scene,row)
        assert path==paths[row['path']]
        reference,data=before[path]
        assert contracts.file_ref(path)==reference and path.read_bytes()==data
    assert not (contracts.workspace(scene,4)/REUSED).exists()


def test_final_input_bound_deduplicates_direct_and_reused_original(scene):
    source(scene, {'candidate.py': 'x' * 64})
    finish(scene, 3, 'W', [2], stage_intent='handoff', text=MARKED, selection=['W002-I001'])
    job = contracts.build_job(scene, 4, 'H', [2, 3])
    assert len(job['direct_materials']) == 1
    assert job['direct_materials'][0]['size_bytes'] == 64


def test_current_prepared_packets_are_idempotent_and_old_write_schema_is_rejected(scene):
    from scidiscovery.research_loop.protocol import CURRENT, UnsupportedProtocol
    job = contracts.build_job(scene, 1, 'H', [])
    finish(scene, 1, 'H', [], action='experiment')
    original = {path: path.read_bytes() for path in (
        scene / 'jobs/001.json', contracts.workspace(scene, 1) / 'science-task.json',
        contracts.host_record(contracts.workspace(scene, 1)) / 'result-identity.json')}
    assert job['handoff_protocol'] == CURRENT
    with pytest.raises(UnsupportedProtocol):
        contracts.result_schema('H', 1)
    assert contracts.build_job(scene, 1, 'H', []) == job
    assert all(path.read_bytes() == contents for path, contents in original.items())
    contracts.validate(contracts.workspace(scene, 1))


@pytest.mark.parametrize('origin', ['foreign', 'future'])
def test_selected_original_requires_same_instance_and_preceding_node(scene, origin):
    from scidiscovery.research_loop import originals
    source(scene)
    finish(scene, 3, 'W', [2], stage_intent='handoff', selection=['W002-I001'])
    job = contracts.build_job(scene, 4, 'H', [3])
    row = dict(job['selected_inputs'][0]['material'])
    row['source'] = (str(scene.parent / 'foreign/project/nodes/002/artifacts/candidate.py')
                     if origin == 'foreign' else str(contracts.workspace(scene, 5) / 'artifacts/candidate.py'))
    # The selection consumer receives an accepted value. Material admission
    # still rejects replacing its exact producer with foreign or future bytes.
    with pytest.raises(ValueError, match='original|exact producer|identity|changed'):
        originals.material_path(scene, row)
