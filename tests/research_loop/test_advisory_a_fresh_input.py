"""New advice opportunities need fresh originals without changing frozen v4."""
import copy
import tomllib

import pytest

from scidiscovery.research_loop import (
    accepted_budget, accepted_search, budget, configuration_form, contracts as c,
    controller, research_config, supervision,
)
from test_reflection_configuration import prepare
from test_worker_first_investment import scene, publish


POLICY=supervision.DEFAULT_WORKER_FIRST_REFLECTION_POLICY


def facts(tokens=2000,seconds=200,*,complete=True):
    return dict(tokens=tokens,seconds=seconds,limits=dict(tokens=10000,seconds=1000),
        complete=dict(tokens=complete,seconds=complete))


def test_new_default_and_export_keep_explicit_fresh_input_requirement(tmp_path):
    root,_=prepare(tmp_path)
    selected=c.settings(root)
    assert selected['supervision_policy']==POLICY
    assert POLICY['require_fresh_input'] is True
    document=research_config.to_document(root,selected)
    text=configuration_form.export_toml(configuration_form.imported(document,base=root))
    options=research_config.from_document(tomllib.loads(text),base=root)
    assert supervision.policy(options['supervision_policy'])==POLICY
    assert research_config.validate_update(root,document)['supervision_policy']==POLICY


@pytest.mark.parametrize('value',[None,0,1,'true',[],{}])
def test_fresh_input_requirement_is_an_explicit_boolean(value):
    with pytest.raises(ValueError,match='must be boolean'):
        supervision.policy(dict(supervision.ADAPTIVE_REFLECTION_POLICY,require_fresh_input=value))


@pytest.mark.parametrize('flag',[None,False,True])
def test_old_v4_operational_cadence_and_new_notice_are_distinct(flag):
    chosen=dict(supervision.ADAPTIVE_REFLECTION_POLICY)
    if flag is not None:chosen['require_fresh_input']=flag
    assert supervision.policy(chosen)==chosen
    state=supervision.empty(chosen);before=copy.deepcopy(state)
    status=supervision.due(state,budget_facts=facts())
    assert status['due'] and status['operational_due'] and not status['fresh_input']
    assert status['automatic_allowed'] is (flag is not True)
    if flag is True:assert status['suppression_reason']=='no_new_review_input'
    assert state==before


@pytest.mark.parametrize('new_input',['question','receipt'])
def test_fresh_original_can_open_diagnosis_but_unknown_consumption_cannot(new_input):
    state=supervision.empty(POLICY)
    if new_input=='question':state=supervision.authored_question_revision(state,True)
    else:
        state=supervision.add_investment(state,dict(original_call_id='exact-original',
            mechanism_ref='w001.m1',completed_receipt=True))
    assert supervision.due(state,budget_facts=facts())['automatic_allowed']
    unknown=supervision.due(state,budget_facts=facts(complete=False))
    assert not unknown['automatic_allowed']
    assert unknown['suppression_reason']=='shared_consumption_unknown_reconcile_original_usage'


def test_operational_notice_does_not_insert_a_or_publish_live_pause(tmp_path):
    root,job,intent,limits,host,live=scene(tmp_path,'review',policy=POLICY)
    host['research_investment']=live
    host['shared']=dict(host['shared'],observed_tokens=30000,accounted_tokens=30000)
    limits=dict(limits,consumed_seconds=20)
    _,hint=publish(root,job,intent,limits,host)
    assert 'direction_review' not in hint
    assert hint['research_investment']['suppression_reason']=='no_new_review_input'
    review=c.read(budget._review_directory(root,job)/'review.json')
    assert not review['pending'] and review['reason']!='worker_direction_review_due'
    summary=accepted_search.empty();summary['last_w']=job['step']
    summary['supervision']=supervision.empty(POLICY)
    route=('W',[job['step']],'Continue the original question',None,None,job['step'],None,None)
    selected,frozen=controller._supervision_route(job['step'],dict(role='W',stage_intent='handoff'),route,
        facts=dict(summary=summary,policy=POLICY,budget_fraction=.2,budget_facts=facts()),source_job=job)
    assert selected==route and frozen is None


def test_default_does_not_upgrade_existing_v3_or_old_explicit_v4(tmp_path):
    for name,chosen in [('v3',supervision.AUTOMATIC_REFLECTION_POLICY),
            ('v4',supervision.ADAPTIVE_REFLECTION_POLICY)]:
        branch=tmp_path/name;branch.mkdir()
        root,options=prepare(branch,supervision_policy=chosen)
        before=(root/'settings.json').read_bytes();original=c.settings(root)
        options.pop('supervision_policy')
        restored=controller.prepare(**dict(options,validate_only=True,_existing_settings=original))
        assert restored['supervision_policy']==chosen
        assert (root/'settings.json').read_bytes()==before
