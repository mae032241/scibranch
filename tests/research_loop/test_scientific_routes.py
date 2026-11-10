"""Real current jobs and seals, no model or domain submission."""

import pytest

from scidiscovery.research_loop import contracts as c,controller,correction,decision,execution,experiments,native,search,semantic
from scidiscovery.research_loop.routing import Route
from test_history_handoff import DEFAULT_ACTION, finish


def scene(tmp_path,*,portfolio=False):
    public=tmp_path/'public';public.mkdir();objective=public/'objective';objective.write_text('Discriminate competing mechanisms while retaining unfinished work.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['never-call-a-model'],max_tokens=1000,max_seconds=60,max_steps=24,
        model='no-model-fixture',reasoning_effort='high',search_policy=search.DEFAULT_POLICY,
        portfolio_policy=dict(version=2,review_after_cycles=100) if portfolio else None)
    return root


def plan(focus=None):
    return dict(scope='Competing target mechanisms',commitments=[dict(label=label,minimal_test='A bounded independent discrimination',existing_ref=None) for label in ('P','Q')],focus=focus or dict(index=None,action='screen'))


def seal(root,step,role,parents,*,action=DEFAULT_ACTION,choice=None,question='What target observation would change this choice?',evidence_return=None,stage_intent='review',mechanism_findings=None):
    job=c.build_job(root,step,role,list(parents),instruction='Bounded original scientific question',evidence_return=evidence_return)
    _,_,record=finish(root,step,role,list(parents),action=action,search_choice=choice,
        stage_intent=stage_intent,mechanism_findings=mechanism_findings,
        limitations=['No scientific efficacy was tested.'])
    return job,c.read(record/'science-output.json'),c.read(record/'result-identity.json')


def test_empty_plan_preserves_original_science_and_requires_same_output_repair(tmp_path):
    from scidiscovery.research_loop.output_repair import OutputRepairRequired
    root=scene(tmp_path)
    with pytest.raises(OutputRepairRequired) as error:
        seal(root,1,'H',[],action='experiment',choice=dict(scope='No unfinished mechanism is available',
            commitments=[],focus=dict(index=None,action='screen')))
    assert error.value.code=='science_plan_empty'
    record=execution.record(root,1)
    original=c.read(record/'science-output.json')
    assert original['search_plan']['commitments']==[]
    assert original['scientific_result']=='A bounded scientific finding'
    assert not (record/'result-identity.json').exists()
    assert not (root/'jobs/002.json').exists()


def test_internal_parser_failure_remains_host_error_without_author_retry(tmp_path,monkeypatch):
    root=scene(tmp_path);job=c.build_job(root,1,'H',[])
    before=list((root/'jobs').iterdir())
    def failed(*args,**kwargs):raise ValueError('Host interpretation failure')
    from types import SimpleNamespace
    inventory=SimpleNamespace(root=root,job=job,interpretation=dict(focus=None,allocation=None,diagnostics=[]),
        selection_diagnostics=[],policy=c.settings(root),head=None)
    monkeypatch.setattr(semantic,'author_choices',failed)
    with pytest.raises(ValueError,match='Host interpretation failure'):
        semantic.outcome(root,job,dict(search_plan=None,next_action='experiment'),inventory=inventory)
    assert list((root/'jobs').iterdir())==before
    assert not (execution.record(root,1)/'native-intent.json').exists()


def test_unconfigured_continue_requires_missing_work_without_authorizing_or_replaying(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    from scidiscovery.research_loop.output_repair import WorkRepairRequired
    root=scene(tmp_path)
    seal(root,1,'H',[],action='experiment',choice=plan())
    job=c.build_job(root,2,'W',[1],instruction='Bounded original scientific question')
    with pytest.raises(WorkRepairRequired) as error:
        seal(root,2,'W',[1],stage_intent='continue',mechanism_findings=[dict(mechanism=label,basis='blocked',
            finding='This fixture has no executed observations; scientific suitability remains unknown.')
            for label in ('P','Q')])
    assert 'h001.m1' in str(error.value) and 'h001.m2' in str(error.value)
    record=execution.record(root,2)
    raw=record/'science-output.json';before=raw.read_bytes()
    intent=(record/'native-intent.json').read_bytes()
    assert c.read(raw)['stage_intent']=='continue'
    accepted=accepted_budget.native_attempt(root,2,record)
    assert accepted['intent']['task_ref']==c.record_ref(root/'jobs/002.json')
    assert not (record/'result-identity.json').exists()
    assert not (root/'jobs/003.json').exists()
    monkeypatch.setattr(native,'execute_native',lambda *args,**kwargs:pytest.fail('Output repair cannot replay the original call'))
    with pytest.raises(WorkRepairRequired):c.seal_result(root,2)
    assert raw.read_bytes()==before and (record/'native-intent.json').read_bytes()==intent
    assert execution.records(root,2)==[record]
    assert not (root/'jobs/003.json').exists()
