"""Prospective routes retain named facts across admission and serialization."""
from dataclasses import FrozenInstanceError, replace

import pytest

from scidiscovery.research_loop import controller, correction, role_share, semantic
from scidiscovery.research_loop.routing import Route


def test_route_is_frozen_and_has_no_positional_protocol():
    parents=[2,4]
    route=Route(role='W',parents=parents,instruction='Retain the exact question.',current_h=2,current_w=4)
    parents.append(99)
    assert route.parents==(2,4)
    with pytest.raises(FrozenInstanceError):route.role='A'
    with pytest.raises(TypeError):route[0]
    changed=replace(route,role='A')
    assert changed.parents==route.parents and changed.current_w==4
    assert route.role=='W'


def test_existing_durable_schema_serializes_explicitly_without_new_fields():
    route=Route(role='W',parents=[1,3],instruction='Continue retained work.',
        evidence_return=dict(role='W',step=1),current_h=2,current_w=1,
        correction_request=dict(trigger_id='exact'),author_correction=dict(reason='existing'))
    payload=controller._route_payload(route)
    assert payload==dict(role='W',parents=[1,3],instruction='Continue retained work.',
        evidence_return=dict(role='W',step=1),correction_request=dict(trigger_id='exact'),
        author_correction=dict(reason='existing'))
    assert controller._route_from_payload(payload,current_h=2,current_w=1)==route


def test_correction_updates_only_its_owned_facts(monkeypatch):
    route=Route(role='W',parents=[1,2],instruction='Original question.',
        current_h=1,current_w=2,evidence_return=dict(role='A',step=2))
    monkeypatch.setattr(correction,'project',lambda *args:dict(
        review_request=dict(trigger_id='original-trigger',reason='assessment_gap',step=2)))
    corrected=correction.effective_route(None,2,dict(next_action='continue_w'),dict(role='A'),route)
    assert corrected.role=='H' and corrected.correction_request==dict(trigger_id='original-trigger')
    assert corrected.parents==route.parents and corrected.evidence_return==route.evidence_return
    assert (corrected.current_h,corrected.current_w)==(1,2)
    assert route.role=='W' and route.correction_request is None


def test_role_share_refusal_and_reconsideration_keep_route_shape(tmp_path,monkeypatch):
    route=Route(role='A',parents=[1],instruction='Optional reflection.',current_w=1)
    monkeypatch.setattr(role_share,'reason',lambda *args,**kwargs:'role_share_thinking_reached')
    admitted,refusal=role_share.effective_route(tmp_path,1,{},dict(role='W'),route)
    assert admitted is route and refusal=='role_share_thinking_reached'
    source=dict(path='retained-original')
    monkeypatch.setattr(semantic,'_source',lambda *args:source)
    reconsidered=role_share.reconsider(tmp_path,1,{},admitted,refusal)
    assert reconsidered.role=='H' and reconsidered.parents==(1,)
    assert reconsidered.current_w==1 and reconsidered.evidence_return is None
    assert reconsidered.author_correction==dict(root=source,source=source,reason=refusal)


def test_cursor_recovery_consumes_saved_route_without_renewing_admission(tmp_path,monkeypatch):
    from scidiscovery.research_loop import accepted_budget
    route=Route(role='W',parents=[1,2],instruction='Previously accepted instruction.',current_h=2,current_w=1)
    saved=dict(completed=2,current_h=2,current_w=1,route=controller._route_payload(route),
        supervision_request=None)
    job=dict(step=2,role='H')
    last=dict(role='H',next_action='wait')
    monkeypatch.setattr(accepted_budget,'unavailable_consultation',lambda *args,**kwargs:None)
    monkeypatch.setattr(controller,'_saved_route',lambda *args,**kwargs:saved)
    monkeypatch.setattr(controller.handoff_state,'read_sealed',lambda *args:last)
    monkeypatch.setattr(controller.contracts,'read',lambda path:job)
    def forbidden(*args,**kwargs):raise AssertionError('Recovery renewed a prospective scientific gate')
    monkeypatch.setattr(controller,'_route',forbidden)
    monkeypatch.setattr(role_share,'effective_route',forbidden)
    cursor=controller._recover_cursor(tmp_path,fork=None,resumed=None,head={},events=[])
    assert cursor.route==route and cursor.first_step==3 and cursor.completed==2
    assert cursor.last is last and cursor.source_job is job
