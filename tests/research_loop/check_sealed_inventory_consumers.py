"""Normal consumers read saved sealed facts; no native or provider execution."""
from copy import deepcopy

import pytest

from scidiscovery.research_loop import budget, candidate_audit, contracts as c, controller
from scidiscovery.research_loop import delivery, formal_review
from test_history_handoff import finish


def test_sealed_subject_and_requesters_do_not_reaccept_inventory(tmp_path, monkeypatch):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Retain the exact synthetic subject and its unanswered scientific question.')
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, ['fixture-never-launch'], max_tokens=10000,
        max_seconds=120, max_steps=6, model='fixture', reasoning_effort='high',
        search_policy=None, experiment_mode='external')
    finish(root, 1, 'E', [], files={'subject.txt': 'An authored synthetic subject, not scientific evidence.'})
    job, _, _ = finish(root, 2, 'A', [1], action='wait')
    producer = c.read(root / 'native-evidence/001/result-identity.json')
    selected = dict(origin_step=1, origin_result_ref=producer['result_ref'],
        material={key: value for key, value in producer['materials'][0].items() if key != 'file_stat'})
    decision = candidate_audit._decision(root, 2)
    # Naming this requester in a closeout boundary does not supply the
    # configured delivery permission that this fixture deliberately lacks.
    budget.begin_closeout(root, 2, 3, 'fixture_boundary', authorization_step=2)
    def forbidden(*args, **kwargs):
        raise AssertionError('A normal sealed consumer reaccepted scientific inventory')
    monkeypatch.setattr(c, 'validate', forbidden)
    monkeypatch.setattr(c, 'validate_original', forbidden)
    monkeypatch.setattr(c, 'accept_current_inventory', forbidden)
    subject = formal_review._subject(root, [selected])
    assert subject['producer_step'] == 1 and subject['material'] == selected['material']
    wrong = deepcopy(selected)
    wrong['origin_result_ref'] = dict(path=str(root / 'another-result.json'))
    with pytest.raises(ValueError, match='exact producer'):
        formal_review._subject(root, [wrong])
    with pytest.raises(ValueError, match='original scientific request'):
        candidate_audit.require_eligibility(root, dict(decision=decision, candidate_id='undeclared-candidate'))
    with pytest.raises(delivery.DeliveryUnresolved, match='delivery_authorization_required'):
        delivery.binding(root, job, c.read(root / 'native-evidence/002/result.json'))
