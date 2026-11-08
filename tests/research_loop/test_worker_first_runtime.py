"""W-first launch and final-review boundaries, using deterministic originals."""
import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, search, supervision
from test_worker_led_strategy import instance, DEFAULT_STRATEGY


def test_new_prepare_defaults_worker_first_but_existing_strategy_stays_frozen(tmp_path):
    root=instance(tmp_path/'new',strategy=DEFAULT_STRATEGY)
    selected=c.settings(root)
    assert selected['research_strategy_version']==3
    assert selected['role_guidance']==guidance.WORKER_FIRST
    # search_policy=None in this fixture preserves that explicit choice.
    for version in (None,1,2):
        original=instance(tmp_path/f'old-{version}',strategy=version,validate_only=True)
        recreated=instance(tmp_path/f'restored-{version}',existing=original,validate_only=True)
        assert recreated.get('research_strategy_version')==version
        assert recreated['role_guidance']==original['role_guidance']


def test_controller_starts_real_first_w_with_empty_parents(tmp_path,monkeypatch):
    root=instance(tmp_path,strategy=3)
    called=[]
    def stop(root,step,**kwargs):
        called.append(c.read(root/'jobs'/f'{step:03d}.json'))
        raise RuntimeError('Deterministic stop before native execution')
    monkeypatch.setattr(controller,'_native_call',stop)
    controller.run(root)
    assert len(called)==1 and called[0]['role']=='W' and called[0]['step']==1
    assert called[0]['direct_refs']==[] and not (root/'jobs/002.json').exists()


@pytest.mark.parametrize('version,bootstrap,expected',[(2,True,'science_plan_empty'),(3,False,'science_plan_empty'),(3,True,None)])
def test_only_explicit_v3_bootstrap_allows_empty_plan(monkeypatch,version,bootstrap,expected):
    monkeypatch.setattr(search.c,'settings',lambda *a:dict(search_policy={},research_strategy_version=version))
    monkeypatch.setattr(search,'current_state',lambda *a:dict(authorized=set()))
    bound=dict(focus=None,intent_diagnostics=[])
    if bootstrap:bound['worker_research']=dict(version=1,exploration=True)
    assert search.launch_reason(None,dict(role='W',research_strategy_version=version,search=bound))==expected


@pytest.mark.parametrize('extra',[dict(final_only=True),dict(formal_review={"version":1}),dict(purpose='closeout')])
def test_bootstrap_cannot_expand_closeout_or_formal_work(monkeypatch,extra):
    monkeypatch.setattr(search.c,'settings',lambda *a:dict(search_policy={},research_strategy_version=3))
    monkeypatch.setattr(search,'current_state',lambda *a:dict(authorized=set()))
    job=dict(role='W',search=dict(focus=None,intent_diagnostics=[],worker_research=dict(version=1,exploration=True)),**extra)
    assert search.launch_reason(None,job)=='science_plan_empty'


def test_v3_w_decision_routes_to_independent_delivery_review(tmp_path):
    job=dict(role='W',research_strategy_version=3,instruction='Preserve the retained question.')
    result=dict(stage_intent='decision',route_reason='Existing evidence can support a final assessment.')
    route=controller._route(tmp_path,1,'W',result,job,None,None)
    assert route[0]=='A' and route[1]==[1] and route[3]['kind']=='delivery_review'
    assert not controller._worker_led_requests_final(job,result)


def test_v3_ordinary_a_cannot_authorize_final_delivery_directly(tmp_path):
    job=dict(role='A',research_strategy_version=3)
    assert controller._worker_led_final_required(job)
    route=controller._route(tmp_path,2,'A',dict(next_action='deliver'),job,None,1)
    assert route[0]=='A' and route[3]['kind']=='delivery_review'


def test_bounded_correction_combination_uses_source_w_not_global_catalog():
    from scidiscovery.research_loop import delivery_review
    source=dict(step=3,role='W',research_strategy_version=3,search=dict(
        breadth=None,worker_research=dict(seed=[dict(mechanism_ref='w001.held',status='active'),
            dict(mechanism_ref='w001.retired',status='retired'),dict(mechanism_ref='w001.recently_retired',status='active')],
            version=1,exploration=True)))
    assessment=dict(step=4,role='A',research_strategy_version=3,
        evidence_return=delivery_review.make('delivery_review',3,0))
    job=dict(step=5,role='W',research_strategy_version=3,
        evidence_return=delivery_review.make('delivery_correction',4,1))
    state=dict(mechanisms={'w001.held':dict(source_step=1,status='pending'),
        'w003.new':dict(source_step=3,status='pending',research_binding=dict(status='active')),
        'w001.retired':dict(source_step=1,status='pending',research_binding=dict(status='retired')),
        'w001.recently_retired':dict(source_step=3,status='pending',research_binding=dict(status='retired')),
        'w002.outside':dict(source_step=2,status='pending')})
    frozen=search._correction_combination(job,{3:source,4:assessment},state)
    assert frozen==dict(version=1,source_step=3,source_steps=[3],mechanism_refs=['w001.held','w003.new'])
    assert state['combination_refs']==frozen['mechanism_refs']
    with pytest.raises(ValueError,match='parent jobs'):search._correction_combination(job,None,state)


def test_bounded_correction_from_ordinary_a_uses_its_exact_loaded_w_subjects(tmp_path):
    from scidiscovery.research_loop import delivery_review
    worker=dict(step=1,role='W',research_strategy_version=3,search=dict(
        worker_research=dict(seed=[dict(mechanism_ref='w001.held',status='active')],version=1,exploration=True)))
    ordinary=dict(step=2,role='A',research_strategy_version=3,
        direct_refs=[dict(path='inputs/parents/001/conclusion.json')])
    final=dict(step=3,role='A',research_strategy_version=3,
        evidence_return=delivery_review.make('delivery_review',2,0),
        direct_refs=[dict(path='inputs/parents/001/conclusion.json'),dict(path='inputs/parents/002/conclusion.json')])
    request=dict(next_action='continue_w',decision_request=dict(question='A concrete retained gap',change_criterion='A changed retained observation'))
    route=controller._route(tmp_path,3,'A',request,final,None,1)
    assert set(route[1])=={1,2,3}
    correction=dict(step=4,role='W',research_strategy_version=3,evidence_return=route[3])
    state=dict(mechanisms={'w001.held':dict(source_step=1,status='pending',research_binding=dict(status='active')),
        'w099.outside':dict(source_step=99,status='pending',research_binding=dict(status='active'))})
    frozen=search._correction_combination(correction,{1:worker,2:ordinary,3:final},state)
    assert frozen==dict(version=1,source_step=2,source_steps=[1],mechanism_refs=['w001.held'])
    with pytest.raises(ValueError,match='unloaded direct source'):
        search._correction_combination(correction,{2:ordinary,3:final},state)
    with pytest.raises(ValueError,match='no exact loaded W'):
        search._correction_combination(correction,{1:dict(worker,role='H'),2:ordinary,3:final},state)


def test_ordinary_a_to_final_to_correction_preserves_multiple_explicit_w_subjects(tmp_path):
    worker=lambda number:dict(step=number,role='W',research_strategy_version=3,search=dict(
        worker_research=dict(version=1,exploration=True,seed=[dict(mechanism_ref=f'w{number:03d}.held',status='active')])))
    first,second=worker(1),worker(2)
    ordinary=dict(step=3,role='A',research_strategy_version=3,
        direct_refs=[dict(path=f'inputs/parents/{number:03d}/conclusion.json') for number in (1,2)])
    readiness=controller._route(tmp_path,3,'A',dict(next_action='finish'),ordinary,None,2)
    assert set(readiness[1])=={1,2,3}
    final=dict(step=4,role='A',research_strategy_version=3,evidence_return=readiness[3],
        direct_refs=[dict(path=f'inputs/parents/{number:03d}/conclusion.json') for number in readiness[1]])
    gap=controller._route(tmp_path,4,'A',dict(next_action='continue_w',
        decision_request=dict(question='Exact combined evidence gap',change_criterion='Retained evidence changes the judgment')),final,None,2)
    assert set(gap[1])=={1,2,3,4}
    correction=dict(step=5,role='W',research_strategy_version=3,evidence_return=gap[3])
    state=dict(mechanisms={f'w{number:03d}.held':dict(source_step=number,status='pending',research_binding=dict(status='active'))
        for number in (1,2,99)})
    bound=search._correction_combination(correction,{1:first,2:second,3:ordinary,4:final},state)
    assert bound['source_steps']==[1,2] and bound['mechanism_refs']==['w001.held','w002.held']
