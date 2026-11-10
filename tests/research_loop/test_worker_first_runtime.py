"""W-first launch and final-review boundaries, using deterministic originals."""
import pytest

from scidiscovery.research_loop import contracts as c, controller, guidance, search, supervision
from test_worker_led_strategy import instance, DEFAULT_STRATEGY


def test_new_prepare_uses_current_guidance_and_rejects_retired_strategy_without_writes(tmp_path):
    root=instance(tmp_path/'new',strategy=DEFAULT_STRATEGY)
    selected=c.settings(root)
    assert selected['research_strategy_version']==3 and selected['advisory_scope_version']==1
    assert selected['role_guidance']==guidance.ADVISORY_WORKER_FIRST
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Keep the original objective.')
    frozen={path:path.read_bytes() for path in (root/'settings.json',root/'settings-identity.json')}
    for version in (None,1,2):
        for existing in (None,dict(selected,research_strategy_version=version)):
            target=tmp_path/f'rejected-{version}-{existing is not None}'
            with pytest.raises(ValueError):
                controller.prepare(target,objective,public,['fixture-never-launch'],
                    max_tokens=10000,max_seconds=120,max_steps=8,model='explicit-fixture-model',
                    reasoning_effort='high',research_strategy_version=version,
                    _existing_settings=existing,validate_only=True)
            assert not target.exists()
    assert all(path.read_bytes()==original for path,original in frozen.items())


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


@pytest.mark.parametrize('bootstrap,expected',[(False,'science_plan_empty'),(True,None)])
def test_only_explicit_current_bootstrap_allows_empty_plan(monkeypatch,bootstrap,expected):
    version=3
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
    assert route.role=='A' and route.parents==(1,) and route.evidence_return['kind']=='delivery_review'


def test_v3_ordinary_a_cannot_authorize_final_delivery_directly(tmp_path):
    job=dict(role='A',research_strategy_version=3)
    assert controller._worker_led_final_required(job)
    route=controller._route(tmp_path,2,'A',dict(next_action='deliver'),job,None,1)
    assert route.role=='A' and route.evidence_return['kind']=='delivery_review'


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
    assert set(route.parents)=={1,2,3}
    correction=dict(step=4,role='W',research_strategy_version=3,evidence_return=route.evidence_return)
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
    assert set(readiness.parents)=={1,2,3}
    final=dict(step=4,role='A',research_strategy_version=3,evidence_return=readiness.evidence_return,
        direct_refs=[dict(path=f'inputs/parents/{number:03d}/conclusion.json') for number in readiness.parents])
    gap=controller._route(tmp_path,4,'A',dict(next_action='continue_w',
        decision_request=dict(question='Exact combined evidence gap',change_criterion='Retained evidence changes the judgment')),final,None,2)
    assert set(gap.parents)=={1,2,3,4}
    correction=dict(step=5,role='W',research_strategy_version=3,evidence_return=gap.evidence_return)
    state=dict(mechanisms={f'w{number:03d}.held':dict(source_step=number,status='pending',research_binding=dict(status='active'))
        for number in (1,2,99)})
    bound=search._correction_combination(correction,{1:first,2:second,3:ordinary,4:final},state)
    assert bound['source_steps']==[1,2] and bound['mechanism_refs']==['w001.held','w002.held']
