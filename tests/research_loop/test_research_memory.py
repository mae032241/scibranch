"""W-selected memory retains conditional evidence and exact provenance."""
import copy
import json
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_prefix, accepted_search, contracts as c, lineage, research_memory


def selected_memory():
    return dict(question='Is the apparent plateau an optimizer failure?',candidates=[dict(
        candidate='W001-C001/artifacts/model',data='adaptive public split',evaluation='loss on retained public rows',
        evidence=[dict(ref='inputs/history/observation.json',relation='opposes',note='Only failed when initialization was zero')],
        failure_condition='Zero initialization under this optimizer',open_question='Does another initialization retain the effect?',
        limitations=['No independent final sample'])])


def memory_facts():
    identity='W001-C001/artifacts/model'
    return dict(candidates={json.dumps(identity,ensure_ascii=False,sort_keys=True):identity},
        references={'inputs/history/observation.json':'inputs/history/observation.json'})


@pytest.mark.parametrize('question,criterion',[
    ('What structures appear in a first run?','Use the observed structure to choose the next representation'),
    ('Does the optimizer fail before evaluating the candidate?','A pre-evaluation error changes implementation work'),
])
def test_discovery_and_diagnostic_questions_require_no_hypothesis_pair(question,criterion):
    value=dict(question=question,change_criterion=criterion)
    assert research_memory.experiment_question(value)==value
    empty=research_memory.project(dict(question=question,candidates=[]),**memory_facts(),source=dict(original='science.json'))
    assert empty['candidates']==[] and empty['diagnostics']==[]


def test_competing_candidate_memory_keeps_negative_conditions_limits_and_source():
    value=selected_memory();before=copy.deepcopy(value);facts=memory_facts()
    source=dict(source_step=1,source_role='W',original='inputs/history/001/scientific-original.json')
    projected=research_memory.project(value,**facts,source=source)
    row=projected['candidates'][0]
    assert value==before
    assert row['failure_condition']==value['candidates'][0]['failure_condition']
    assert row['evidence'][0]['relation']=='opposes'
    assert row['evidence'][0]['note']=='Only failed when initialization was zero'
    assert row['limitations']==['No independent final sample']
    assert row['source']==dict(source,pointer='/research_memory/candidates/0')
    assert 'permission' not in projected and 'qualified' not in projected


def test_unknown_candidate_or_reference_stays_in_original_without_becoming_memory():
    value=selected_memory();before=copy.deepcopy(value)
    result=research_memory.project(value,candidates={},references={},source=dict(original='science.json'))
    assert value==before and result['candidates']==[]
    assert result['diagnostics'][0]['reason']=='candidate_original_unavailable'
    result=research_memory.project(value,candidates=memory_facts()['candidates'],references={},source={})
    assert result['diagnostics'][0]['reason']=='evidence_original_unavailable'


def test_consultation_and_new_worker_keep_question_candidate_and_original():
    w=dict(role='W',step=1,research_strategy_version=3)
    prior=lineage.research_navigation({},w,dict(summary='Observed a conditional failure',limitations=[],research_memory=selected_memory()),
        node_path='nodes/001',memory_facts=memory_facts())
    memory=copy.deepcopy(prior['candidate_memory'])
    h=lineage.research_navigation(prior,dict(role='H',step=2,purpose='worker_consultation',research_strategy_version=3),
        dict(summary='Try an alternative representation',limitations=[]),node_path='nodes/002')
    next_w=lineage.research_navigation(h,dict(role='W',step=3,research_strategy_version=3),
        dict(summary='Use returned advice',limitations=[]),node_path='nodes/003')
    assert h['candidate_memory']==next_w['candidate_memory']==memory
    assert next_w['candidate_memory']['source']['source_step']==1


def test_optional_malformed_update_preserves_last_usable_memory():
    prior=dict(candidate_memory=research_memory.project(selected_memory(),**memory_facts(),source={'original':'first.json'}))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),dict(research_memory='bad'),
        source={'original':'second.json'},facts=memory_facts())
    assert result['candidates']==prior['candidate_memory']['candidates']
    assert result['source']=={'original':'first.json'}
    assert result['update_diagnostics'][0]['reason']=='invalid_memory_shape'


@pytest.mark.parametrize('update',[
    dict(question=123,candidates=[]),
    dict(question='Current question',candidates=[],unexpected=True),
])
def test_invalid_empty_memory_update_cannot_clear_previous_candidates(update):
    prior=dict(candidate_memory=research_memory.project(selected_memory(),**memory_facts(),source={'original':'first.json'}))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),
        dict(research_memory=update),source={'original':'second.json'},facts=memory_facts())
    assert result['candidates']==prior['candidate_memory']['candidates']
    assert result['source']=={'original':'first.json'}
    assert result['update_diagnostics'][0]['reason']=='invalid_memory_shape'


def test_valid_explicit_empty_update_can_clear_previous_candidates():
    prior=dict(candidate_memory=research_memory.project(selected_memory(),**memory_facts(),source={'original':'first.json'}))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),
        dict(research_memory=dict(question='Reconsider prior candidates',candidates=[])),
        source={'original':'second.json'},facts=memory_facts())
    assert result['candidates']==[] and result['diagnostics']==[]
    assert result['source']=={'original':'second.json'}


def test_legacy_partial_candidate_update_still_keeps_its_valid_new_entries():
    prior=dict(candidate_memory=research_memory.project(selected_memory(),**memory_facts(),source={'original':'first.json'}))
    value=selected_memory();value['candidates'][0]['open_question']='A new authored question'
    value['candidates'].append(dict(value['candidates'][0],candidate='unavailable-candidate'))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),
        dict(research_memory=value),source={'original':'second.json'},facts=memory_facts())
    assert result['candidates'][0]['open_question']=='A new authored question'
    assert result['source']=={'original':'second.json'}
    assert result['diagnostics'][0]['reason']=='candidate_original_unavailable'


def test_same_worker_live_declaration_enters_existing_table_without_call_or_h_parent():
    origin=dict(goal_id='goal',settings_ref={'path':'/instance/settings.json'})
    source={'path':'/instance/experiments/001/research/000001.json'}
    row=dict(mechanism_ref='w001.initial',question='What structure can initial exploration reveal?',label='Initial exploration',
        status='active',exploration=True,revision=1,source_step=1,source_role='W',source_ref=source,original_ref=source,predecessors=[])
    delta=accepted_search._Delta(accepted_search.empty(),lambda key:None)
    accepted_search._live_mechanisms(delta,dict(role='W',step=1,research_strategy_version=3),
        dict(research_state=dict(mechanisms={'w001.initial':row})),origin=origin,initial_calls=1)
    fact=delta.summary['table']['mechanisms']['w001.initial']
    assert fact['research_binding']['question']==row['question']
    assert fact['source_ref']==source and fact['exploration'] is True
    assert fact['accepted_calls']==0 and delta.summary['mechanism_count']==1
    retired=dict(row,status='retired',revision=2)
    accepted_search._live_mechanisms(delta,dict(role='W',step=1,research_strategy_version=3),
        dict(research_state=dict(mechanisms={'w001.initial':retired})),origin=origin,initial_calls=1)
    assert fact['status']=='waived' and fact['research_binding']['revision']==2
    accepted_search._live_mechanisms(delta,dict(role='W',step=3,research_strategy_version=3),
        dict(research_state=dict(mechanisms={'w001.initial':retired})),origin=origin,initial_calls=1)
    assert fact['status']=='waived' and fact['source_step']==1
    assert delta.summary['mechanism_count']==1


@pytest.fixture
def constant_prefix_memory_job(tmp_path):
    """Use actual sealed nodes and the production prefix-only task binding."""
    from test_history_handoff import finish
    from test_scientific_guidance import prepare
    root=prepare(tmp_path,None,research_strategy_version=3)
    finish(root,1,'W',[],stage_intent='handoff')
    first=accepted_prefix.current(root)
    original='inputs/history/'+str(Path(first['tip']['navigation']['path']).parent/'scientific-original.json')
    finish(root,2,'W',[1],stage_intent='handoff')
    job=c.build_job(root,3,'W',[2])
    assert 'files' not in job['history']
    assert job['history']['prefix']==accepted_prefix.current(root)
    return root,job,original


def test_prefix_only_binding_resolves_exact_historical_originals_without_science_reads(constant_prefix_memory_job,monkeypatch):
    root,job,original=constant_prefix_memory_job
    value=selected_memory()
    value['candidates'][0]['evidence']=[dict(ref=original,relation='opposes',note='Failure holds only for this initialization')]
    foreign=original.replace('/nodes/','/nodes/foreign-',1)
    future=original.replace('/001/','/999/')
    value['candidates'][0]['evidence'].extend([
        dict(ref=foreign,relation='unknown',note='Wrong origin'),
        dict(ref=future,relation='unknown',note='Not in this prefix'),
        dict(ref=original.replace('scientific-original.json','private.json'),relation='unknown',note='Not a public original')])
    read=c.read
    def metadata_only(path):
        assert Path(path).name not in {'science.json','scientific-original.json'}
        return read(path)
    monkeypatch.setattr(c,'read',metadata_only)
    facts=accepted_prefix._research_memory_facts(root,job['history']['prefix'],job,
        dict(research_memory=value),dict(materials=[]),None)
    assert facts['references'].get(original)==original
    assert foreign not in facts['references'] and future not in facts['references']
    assert original.replace('scientific-original.json','private.json') not in facts['references']


def test_revising_prior_memory_reuses_exact_bound_evidence_without_reloading_nodes(constant_prefix_memory_job,monkeypatch):
    root,job,original=constant_prefix_memory_job
    value=selected_memory()
    value['candidates'][0]['evidence']=[dict(ref=original,relation='opposes',note='Only failed at zero initialization')]
    initial=accepted_prefix._research_memory_facts(root,job['history']['prefix'],job,
        dict(research_memory=value),dict(materials=[]),None)
    # The selected identity is fixture scientific data; evidence availability
    # comes from the real prefix binder and accepted membership above.
    identity=value['candidates'][0]['candidate']
    initial['candidates'][research_memory._identity(identity)]=identity
    prior=dict(candidate_memory=research_memory.project(value,**initial,source=dict(original=original)))
    changed=copy.deepcopy(value);changed['candidates'][0]['open_question']='Would a different initializer avoid the failure?'
    def no_reload(*args,**kwargs):raise AssertionError('Previously bound memory must reuse its produced facts')
    monkeypatch.setattr(accepted_prefix,'member',no_reload)
    monkeypatch.setattr(accepted_prefix,'candidate',no_reload)
    facts=accepted_prefix._research_memory_facts(root,job['history']['prefix'],job,
        dict(research_memory=changed),dict(materials=[]),None,prior=prior)
    updated=research_memory.project(changed,**facts,source=dict(original='new-science.json'))
    assert updated['diagnostics']==[]
    assert updated['candidates'][0]['open_question']==changed['candidates'][0]['open_question']
    assert updated['candidates'][0]['evidence']==prior['candidate_memory']['candidates'][0]['evidence']
