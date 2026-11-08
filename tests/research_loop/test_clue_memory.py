"""Candidate-independent W clues use existing exact-original navigation."""
import copy
from pathlib import Path

import pytest

from scidiscovery.research_loop import accepted_prefix, contracts as c, lineage, research_memory
from test_research_memory import constant_prefix_memory_job


def clue_memory(reference='inputs/history/observation.json'):
    return dict(question='Which representation is worth investigating?',clues=[dict(
        observed_fact='The optimizer stopped before a candidate was evaluated',
        applicability='Zero initialization with the current optimizer',
        limited_negative='This setup supplied no evidence for a candidate comparison',
        open_question='Does a different initialization permit evaluation?',
        evidence=[dict(ref=reference,relation='unknown',note='Observed pre-evaluation stop')])])


def project(value, **kwargs):
    return research_memory.project(value,candidates={},references={
        'inputs/history/observation.json':'inputs/history/observation.json'},
        source=dict(original='inputs/history/first-science.json'),**kwargs)


def test_clues_precede_candidates_without_inventing_a_candidate_or_verdict():
    value=clue_memory();before=copy.deepcopy(value)
    result=project(value)
    assert value==before and result['diagnostics']==[] and result['candidates']==[]
    row=result['clues'][0]
    assert {key:row[key] for key in value['clues'][0]}==value['clues'][0]
    assert row['source']['pointer']=='/research_memory/clues/0'
    assert set(row)==set(value['clues'][0])|{'source'}
    assert 'ranking' not in result and 'qualified' not in result and 'mechanism' not in row


@pytest.mark.parametrize('statement',[
    dict(observed_fact='A structural pattern appeared'),
    dict(open_question='Would another representation preserve the pattern?'),
    dict(limited_negative='No improvement in this adaptive split',applicability='Current split and initialization'),
])
def test_clues_need_only_the_selected_claim_and_exact_evidence(statement):
    value=clue_memory();value['clues'][0]={**statement,'evidence':value['clues'][0]['evidence']}
    assert project(value)['diagnostics']==[]


@pytest.mark.parametrize('replacement,reason',[
    (dict(limited_negative='No signal',evidence=[dict(ref='inputs/history/observation.json',relation='opposes',note='This setup')]),'invalid_clue_memory_shape'),
    (dict(observed_fact='A pattern',evidence=[]),'invalid_evidence_memory_shape'),
    (dict(observed_fact='A pattern',evidence=[dict(ref='missing.json',relation='supports',note='Not bound')]),'evidence_original_unavailable'),
    (dict(applicability='Current split',evidence=[]),'invalid_clue_memory_shape'),
])
def test_unbound_or_unscoped_clues_remain_only_in_authored_original(replacement,reason):
    value=clue_memory();value['clues']=[replacement];before=copy.deepcopy(value)
    result=project(value)
    assert value==before and result['clues']==[] and result['candidates']==[]
    assert result['diagnostics']==[dict(pointer='/research_memory/clues/0',reason=reason)]


def test_clues_survive_h_a_advice_and_a_new_w_context():
    job=dict(role='W',step=1,research_strategy_version=3)
    navigation=lineage.research_navigation({},job,
        dict(summary='Diagnostic observation',limitations=[],research_memory=clue_memory()),
        node_path='nodes/001',memory_facts=dict(candidates={},references={
            'inputs/history/observation.json':'inputs/history/observation.json'}))
    original=copy.deepcopy(navigation['candidate_memory'])
    for step,role in enumerate(['H','A','W'],2):
        navigation=lineage.research_navigation(navigation,
            dict(role=role,step=step,research_strategy_version=3,purpose='worker_consultation'),
            dict(summary='Returned advice',limitations=[]),node_path=f'nodes/{step:03d}')
        assert navigation['candidate_memory']==original
    assert 'candidate-independent clues' in navigation['unresolved_questions']


def test_partial_malformed_update_cannot_erase_prior_usable_clues():
    prior=dict(candidate_memory=project(clue_memory()))
    update=clue_memory();update['clues'][0]['observed_fact']='A later observation'
    update['clues'].append(dict(observed_fact='Missing original'))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),
        dict(research_memory=update),source=dict(original='later.json'),
        facts=dict(candidates={},references={'inputs/history/observation.json':'inputs/history/observation.json'}))
    assert result['clues']==prior['candidate_memory']['clues']
    assert result['source']==prior['candidate_memory']['source']
    assert result['update_diagnostics'][0]['pointer']=='/research_memory/clues/1'


def test_clue_original_binding_uses_prefix_metadata_without_candidate_or_science_reads(constant_prefix_memory_job,monkeypatch):
    root,job,original=constant_prefix_memory_job
    value=clue_memory(original)
    read=c.read
    def metadata_only(path):
        assert Path(path).name not in {'science.json','scientific-original.json'}
        return read(path)
    def no_candidate(*args,**kwargs):raise AssertionError('Clues need no candidate lookup')
    monkeypatch.setattr(c,'read',metadata_only)
    monkeypatch.setattr(accepted_prefix,'candidate',no_candidate)
    facts=accepted_prefix._research_memory_facts(root,job['history']['prefix'],job,
        dict(research_memory=value),dict(materials=[]),None)
    assert facts['candidates']=={} and facts['references'][original]==original
    memory=research_memory.project(value,**facts,source=dict(original='new-science.json'))
    def no_reload(*args,**kwargs):raise AssertionError('Reuse accepted clue reference facts')
    monkeypatch.setattr(accepted_prefix,'member',no_reload)
    reused=accepted_prefix._research_memory_facts(root,job['history']['prefix'],job,
        dict(research_memory=value),dict(materials=[]),None,prior=dict(candidate_memory=memory))
    assert reused==facts


def test_clues_and_existing_candidate_shapes_can_be_explicitly_cleared():
    prior=dict(candidate_memory=project(clue_memory()))
    result=research_memory.retain(prior,dict(role='W',research_strategy_version=3),
        dict(research_memory=dict(question='Reconsider',candidates=[])),source=dict(original='later.json'),
        facts=dict(candidates={},references={}))
    assert result['candidates']==[] and 'clues' not in result and result['diagnostics']==[]
