"""Selected A supervision scope survives the real fork preparation crash boundary."""
import copy

import pytest

from scidiscovery.research_loop import (accepted_budget, contracts as c, controller,
    fork_runtime, fork_service, fork_sources, supervision)
from test_history_handoff import finish


@pytest.fixture
def selected_supervision(tmp_path):
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md'
    objective.write_text('Retain the selected assessment question and its explicit scientific limitations.')
    parent=tmp_path/'parent'
    controller.prepare(parent,objective,public,['fixture-never-launch'],max_tokens=1000,
        max_seconds=60,max_steps=6,model='fixture',reasoning_effort='high',
        source_restrictions=[dict(source=str(objective),restriction='clear')])
    finish(parent,1,'H',[],action='analyze',search_choice=dict(scope='A bounded target-condition question',
        commitments=[dict(label='Retained fixture mechanism',minimal_test='A target-condition observation',existing_ref=None)],
        focus=dict(index=None,action='screen')))
    head=accepted_budget.read(parent)
    frozen=supervision.request(head['search'],budget_fraction=.01,automatic=False)
    selected=c.build_job(parent,2,'A',[1],instruction='Assess the retained target-condition question.',
        supervision_request=frozen)
    with controller._writer(parent):
        pass
    child=tmp_path/'child'
    allowance=dict(max_tokens=600,max_seconds=40,max_steps=3)
    preview=fork_service.preview(parent,2,child,budget=allowance)
    assert preview['status']=='fork_preview_ready',preview
    fork_service.create(parent,2,child,budget=allowance,
        expected_preview_id=preview['preview_id'],operation_id='selected-supervision')
    return parent,child,selected,frozen


def test_selected_scope_survives_copy_bootstrap_and_unaccepted_job_crash_window(selected_supervision,monkeypatch):
    parent,child,selected,frozen=selected_supervision
    original=(parent/'jobs/002.json').read_bytes()
    boot=fork_runtime.bootstrap(child)
    assert boot['route']['supervision_request']==frozen
    assert fork_sources.selected_task(child)['job']['supervision_request']==frozen
    assert accepted_budget.read(child)['current_job'] is None
    def crash_before_acceptance(*args,**kwargs):
        raise RuntimeError('fixture crash after selected job publication, before accepted current_job')
    with monkeypatch.context() as patch:
        patch.setattr(accepted_budget,'accept_job_from_facts',crash_before_acceptance)
        with pytest.raises(RuntimeError,match='fixture crash'):
            fork_runtime.build_selected_job(child,2)
    assert (child/'jobs/002.json').exists()
    assert accepted_budget.read(child)['current_job'] is None
    route=boot['route']
    checked=controller._check_job(child,c.settings(child),2,route['role'],route['parents'],
        route['instruction'],route['evidence_return'],route['correction_request'],
        route['author_correction'],route['supervision_request'])
    assert checked['supervision_request']==frozen and checked['fork_source']
    changed=copy.deepcopy(frozen);changed['automatic']=True
    with pytest.raises(ValueError,match='exact bootstrap route'):
        controller._check_job(child,c.settings(child),2,route['role'],route['parents'],
            route['instruction'],route['evidence_return'],route['correction_request'],
            route['author_correction'],changed)
    resumed=fork_runtime.build_selected_job(child,2)
    assert resumed['supervision_request']==frozen
    assert accepted_budget.read(child)['current_job']['step']==2
    assert (parent/'jobs/002.json').read_bytes()==original
    assert not (child/'native-evidence/002/native-intent.json').exists()


@pytest.mark.parametrize('field',['supervision_policy','supervision_budget_basis'])
def test_selected_policy_and_initial_budget_basis_cannot_change_before_admission(selected_supervision,field):
    _,child,_,_=selected_supervision
    settings=c.settings(child)
    assert settings['supervision_budget_basis']==dict(max_tokens=1000,max_seconds=60)
    changed=copy.deepcopy(settings)
    if field=='supervision_policy':
        changed[field]['consecutive_rounds']=5
    else:
        changed[field]['max_tokens']=600
    originals={path:path.read_bytes() for path in (child/'settings.json',child/accepted_budget.HEAD)}
    with pytest.raises(ValueError,match='Fork selected task configuration changed: '+field):
        fork_runtime.preflight_selected_settings(child,changed,step=2)
    assert all(path.read_bytes()==value for path,value in originals.items())
    assert not (child/'jobs/002.json').exists()
