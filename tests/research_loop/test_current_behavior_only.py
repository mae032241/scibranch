"""Current scientific behavior admission without native execution or migration."""
import copy
from types import SimpleNamespace

import pytest

from scidiscovery.research_loop import checkpoint, contracts, controller, protocol, supervision


def current_settings():
    return dict(handoff_protocol=protocol.CURRENT, research_strategy_version=3,
        advisory_scope_version=1, node_budget_mode='soft', final_a_completion_policy=dict(version=1))


def current_job():
    return dict(handoff_protocol=protocol.CURRENT, research_strategy_version=3,
        advisory_scope_version=1, worker_view_version=1, lineage_projection_version=1,
        step=1, role='W')


@pytest.mark.parametrize('kind,role,corrections_used', [
    ('delivery_review','A',0), ('delivery_correction','W',1),
])
def test_delivery_binding_accepts_current_exact_integer_strategy(kind,role,corrections_used):
    from scidiscovery.research_loop import delivery_review
    value=dict(kind=kind,step=1,corrections_used=corrections_used)
    job=dict(current_job(),step=2,role=role,evidence_return=value)
    before=copy.deepcopy(job)
    result=delivery_review.binding(job)
    assert result==value and result is not value
    assert job==before


@pytest.mark.parametrize('version', [2,None,True,3.0])
@pytest.mark.parametrize('kind,role,corrections_used', [
    ('delivery_review','A',0), ('delivery_correction','W',1),
])
def test_delivery_binding_rejects_obsolete_or_noninteger_strategy(version,kind,role,corrections_used):
    from scidiscovery.research_loop import delivery_review
    job=dict(current_job(),step=2,role=role,research_strategy_version=version,
        evidence_return=dict(kind=kind,step=1,corrections_used=corrections_used))
    before=copy.deepcopy(job)
    with pytest.raises(ValueError,match='Invalid immutable delivery review scope'):
        delivery_review.binding(job)
    assert job==before


@pytest.mark.parametrize('field,value', [
    ('research_strategy_version',None), ('research_strategy_version',1),
    ('research_strategy_version',2), ('research_strategy_version',True),
    ('advisory_scope_version',None), ('advisory_scope_version',True),
    ('supervision_policy',dict(version=3)),
    ('worker_fragment_policy',dict(context='same_session')),
    ('worker_fragment_policy',dict(context='new_context',max_context_windows=2,context_handoff_version=1)),
])
def test_same_protocol_rejects_obsolete_behavior_without_mutation(field,value):
    settings=current_settings();settings[field]=value
    before=copy.deepcopy(settings)
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_settings_format(settings)
    assert settings==before


@pytest.mark.parametrize('field', ['research_strategy_version','advisory_scope_version'])
def test_retained_job_requires_explicit_current_behavior(field):
    job=current_job();job.pop(field)
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_job_format(job)


def test_current_optional_policies_preserve_declared_controls():
    settings=current_settings()
    assert protocol.require_settings_format(settings) is settings
    settings['supervision_policy']=dict(supervision.DEFAULT_POLICY,mode='off',require_fresh_input=False)
    settings['worker_fragment_policy']=dict(context='new_context',max_context_windows=2,context_handoff_version=2)
    before=copy.deepcopy(settings)
    assert protocol.require_settings_format(settings) is settings
    assert settings==before
    assert protocol.require_job_format(current_job())['worker_view_version']==1
    assert settings['final_a_completion_policy']==dict(version=1)


def test_frozen_v4_requires_explicit_fresh_input_choice():
    settings=current_settings()
    settings['supervision_policy']=copy.deepcopy(supervision.ADAPTIVE_REFLECTION_POLICY)
    before=copy.deepcopy(settings)
    with pytest.raises(protocol.UnsupportedProtocol):
        protocol.require_settings_format(settings)
    assert settings==before
    declared=supervision.policy(settings['supervision_policy'])
    assert declared['require_fresh_input'] is True
    settings['supervision_policy']=declared
    assert protocol.require_settings_format(settings) is settings
    settings['supervision_policy']['require_fresh_input']=False
    assert protocol.require_settings_format(settings) is settings


def test_current_supervision_defaults_do_not_rewrite_declaration_or_reset_state():
    declared=copy.deepcopy(supervision.ADAPTIVE_REFLECTION_POLICY)
    before=copy.deepcopy(declared)
    assert supervision.policy(declared)['require_fresh_input'] is True
    assert declared==before
    state=supervision.empty(dict(declared,require_fresh_input=False))
    state['investment'].update(total_calls=7,covered_calls=3,consecutive_calls=4)
    state['adaptive']['automatic_cost']['tokens']=29
    before=copy.deepcopy(state)
    observed=supervision.observe(state)
    assert observed['investment']['total_calls']==7
    assert observed['investment']['covered_calls']==3
    assert observed['adaptive']['automatic_cost']['tokens']==29
    assert state==before


@pytest.mark.parametrize('version',[1,2,3])
def test_obsolete_supervision_algorithm_is_unavailable(version):
    with pytest.raises(ValueError,match='version 4'):
        supervision.policy(dict(version=version))


def test_declaration_may_default_new_fragment_but_frozen_fragment_must_be_explicit():
    declared=dict(context='same_session')
    assert checkpoint.policy(declared)==declared
    with pytest.raises(ValueError,match='version 2'):
        checkpoint.policy(declared,frozen=True)
    with pytest.raises(ValueError,match='version 2'):
        checkpoint.policy(dict(declared,context_handoff_version=1))


def test_build_job_refuses_old_base_before_reconciliation_or_workspace(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_prefix
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Test current behavior admission.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture'],max_tokens=1000,max_seconds=60,
        max_steps=4,model='fixture',reasoning_effort='high',search_policy=None,
        experiment_mode='external')
    settings=contracts.settings(root)
    settings['research_strategy_version']=2
    monkeypatch.setattr(contracts,'settings',lambda root:settings)
    def forbidden(*args,**kwargs):
        pytest.fail('obsolete behavior reached reconciliation')
    monkeypatch.setattr(accepted_prefix,'reconcile',forbidden)
    settings_bytes=(root/'settings.json').read_bytes()
    with pytest.raises(protocol.UnsupportedProtocol):
        contracts.build_job(root,1,'W',[])
    assert not (root/'jobs/001.json').exists()
    assert not contracts.workspace(root,1).exists()
    assert (root/'settings.json').read_bytes()==settings_bytes


def test_old_original_is_refused_before_scientific_projection(tmp_path,monkeypatch):
    from scidiscovery.research_loop import originals, handoff_state
    original=tmp_path/'historical-science.json'
    original.write_bytes(b'{"scientific_result":"exact historical bytes"}\n')
    before=original.read_bytes()
    job=current_job();job['research_strategy_version']=2
    source=SimpleNamespace(origin_identity={},step=1)
    monkeypatch.setattr(originals,'identity_key',lambda identity:'fixture-origin')
    monkeypatch.setattr(originals,'source_job',lambda source:job)
    monkeypatch.setattr(originals,'source_settings',lambda source:current_settings())
    def forbidden(*args,**kwargs):
        pytest.fail('obsolete original reached scientific projection')
    monkeypatch.setattr(handoff_state,'material_inputs',forbidden)
    with pytest.raises(protocol.UnsupportedProtocol):
        contracts.validate_original(source)
    assert original.read_bytes()==before


def test_old_frozen_prepare_is_refused_before_project_preparation(tmp_path,monkeypatch):
    from scidiscovery.research_loop import project_layout
    settings=current_settings();settings.pop('advisory_scope_version')
    def forbidden(*args,**kwargs):
        pytest.fail('obsolete settings reached project preparation')
    monkeypatch.setattr(project_layout,'prepare',forbidden)
    root=tmp_path/'absent-instance'
    with pytest.raises(protocol.UnsupportedProtocol):
        controller.prepare(root,tmp_path/'objective.md',tmp_path/'public',['fixture'],
            max_tokens=1000,max_seconds=60,max_steps=4,model='fixture',reasoning_effort='high',
            _existing_settings=settings,validate_only=True)
    assert not root.exists()


def test_current_response_menu_keeps_diagnostic_advice_and_w_research_fields():
    job=current_job()
    assert 'exploration_rounds' not in contracts.response_fields(job,{})
    assert 'research_memory' in contracts.response_fields(job,{})
    advisory=dict(job,role='A')
    options=contracts.response_options(advisory)
    schema=contracts.result_schema('A',**options)
    assert schema['properties']['next_action']['enum']==['continue_w']
    instructions=contracts.response_instructions(job)
    assert 'exploration_rounds' not in instructions['stage_response']
    assert 'research_memory' in instructions['stage_response']


@pytest.mark.parametrize('obsolete_policy',['fragment','supervision'])
@pytest.mark.parametrize('selector',['current','new_job','retained_job','bind','run','reserved_bind'])
def test_stored_obsolete_revision_is_refused_before_policy_effects(tmp_path,obsolete_policy,selector):
    from scidiscovery.research_loop import accepted_budget, budget, configuration_updates, research_config
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Test selected configuration admission.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture'],max_tokens=1000,max_seconds=60,
        max_steps=4,model='fixture',reasoning_effort='high',search_policy=None,
        experiment_mode='external')
    base=contracts.settings(root)
    declaration=research_config.to_document(root,base)
    declaration['budget']['max_steps']=6
    configuration_updates.submit(root,declaration,authorization_note='Fixture permits six total steps.')
    revision=dict(revision=1,path='host-control/configuration/revisions/000001.json')
    if selector=='reserved_bind':
        assert configuration_updates.bind_new_job(root,1)==revision
    path=root/revision['path']
    row=contracts.read(path)
    # Represent a snapshot accepted by the previous implementation. Its exact
    # reference and owner stay valid; current admission must reject its policy.
    if obsolete_policy=='fragment':
        row['settings']['worker_fragment_policy']=dict(context='same_session',
            max_context_windows=1,context_handoff_version=1)
    else:
        row['settings']['supervision_policy']=dict(version=3,mode='review',consecutive_calls=4,
            min_budget_fraction=0.2,budget_checkpoints=[0.3,0.65],max_automatic_reviews=2)
    contracts.write(path,row)
    before_bytes=path.read_bytes()
    binding=root/'host-control/configuration/bindings/000001.json'
    before_binding=binding.read_bytes() if binding.exists() else None
    before_ceiling=budget.ceilings(root)
    before_applied=accepted_budget.read(root)['applied_configuration']
    with pytest.raises(protocol.UnsupportedProtocol):
        if selector=='current':
            configuration_updates.current(root)
        elif selector=='new_job':
            configuration_updates.for_new_job(root,1)
        elif selector=='retained_job':
            configuration_updates.for_job(root,dict(configuration_revision=revision))
        elif selector=='run':
            controller.run(root)
        else:
            configuration_updates.bind_new_job_facts(root,1,base=base)
    assert path.read_bytes()==before_bytes
    assert (binding.read_bytes() if binding.exists() else None)==before_binding
    assert budget.ceilings(root)==before_ceiling
    assert accepted_budget.read(root)['applied_configuration']==before_applied
    assert not (root/'jobs/001.json').exists()
    assert not (root/'host-started.json').exists()


@pytest.mark.parametrize('obsolete_policy',['fragment','supervision'])
def test_inactive_obsolete_revision_remains_metadata_while_current_policy_is_usable(tmp_path,obsolete_policy):
    from scidiscovery.research_loop import budget, configuration_updates, research_config
    public=tmp_path/'public';public.mkdir()
    objective=public/'objective.md';objective.write_text('Test inactive configuration history.')
    root=tmp_path/'instance'
    controller.prepare(root,objective,public,['fixture'],max_tokens=1000,max_seconds=60,
        max_steps=4,model='fixture',reasoning_effort='high',search_policy=None,
        experiment_mode='external')
    declaration=research_config.to_document(root)
    declaration['budget']['max_steps']=6
    configuration_updates.submit(root,declaration,authorization_note='Fixture allows six total steps.')
    old_ref=configuration_updates.bind_new_job(root,1)
    declaration=research_config.to_document(root,configuration_updates.editable(root))
    declaration['agent']['model']='current-fixture'
    declaration['budget']['max_steps']=8
    configuration_updates.submit(root,declaration,authorization_note='Fixture allows eight total steps.')
    current_ref=configuration_updates.bind_new_job(root,2)
    assert current_ref['revision']==2
    old_path=root/old_ref['path']
    old_row=contracts.read(old_path)
    if obsolete_policy=='fragment':
        old_row['settings']['worker_fragment_policy']=dict(context='same_session',
            max_context_windows=1,context_handoff_version=1)
    else:
        old_row['settings']['supervision_policy']=dict(version=3,mode='review',consecutive_calls=4,
            min_budget_fraction=0.2,budget_checkpoints=[0.3,0.65],max_automatic_reviews=2)
    contracts.write(old_path,old_row)
    before_old=old_path.read_bytes()
    before_current=(root/current_ref['path']).read_bytes()
    binding_paths=[root/'host-control/configuration/bindings'/f'{step:06d}.json' for step in (1,2)]
    before_bindings=[path.read_bytes() for path in binding_paths]
    before_ceiling=budget.ceilings(root)
    assert configuration_updates.current(root)['model']=='current-fixture'
    assert configuration_updates.editable(root)['model']=='current-fixture'
    status=configuration_updates.status(root)
    assert status['revision']==status['active_revision']==2
    assert status['settings']['model']==status['active_settings']['model']=='current-fixture'
    with pytest.raises(protocol.UnsupportedProtocol):
        configuration_updates.for_job(root,dict(configuration_revision=old_ref))
    with pytest.raises(protocol.UnsupportedProtocol):
        configuration_updates.bind_new_job(root,1)
    assert old_path.read_bytes()==before_old
    assert (root/current_ref['path']).read_bytes()==before_current
    assert [path.read_bytes() for path in binding_paths]==before_bindings
    assert budget.ceilings(root)==before_ceiling
    assert not (root/'host-control/configuration/bindings/000003.json').exists()
