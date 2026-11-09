"""Frozen role adapters with synthetic receipts; no native/provider execution."""
import pytest

from scidiscovery.research_loop import (
    accepted_budget, backends, contracts as c, controller, execution,
    formal_review as review,
)
from test_formal_review import compact_identity_fixture


def prepared(tmp_path, *, global_subagents, worker_subagents, reviewer_subagents=False):
    public = tmp_path / 'public'
    public.mkdir()
    objective = public / 'objective.md'
    objective.write_text('Keep exact frozen role permissions for formal identity.')
    launcher = tmp_path / 'codex'
    launcher.write_text('#!/bin/sh\nexit 99\n')
    launcher.chmod(0o700)
    root = tmp_path / 'instance'
    controller.prepare(root, objective, public, [str(launcher)],
        max_tokens=2000, max_seconds=60, max_steps=6,
        model='fixture', reasoning_effort='high', search_policy=None,
        experiment_mode='external', resume_policy=None,
        native_options=dict(workspace='workspace-write', network=False,
            web='disabled', subagents=global_subagents),
        role_profiles=dict(W=dict(subagents=worker_subagents),
            A=dict(subagents=reviewer_subagents)),
        formal_review_policy=dict(version=1), research_strategy_version=None)
    return root


def accepted_attempt(root, job):
    """Use the durable native owner; this synthetic launcher is never invoked."""
    _, _, outcome, _, _ = compact_identity_fixture()
    record = execution.origin(root, job['step'])
    record.mkdir(parents=True, exist_ok=True)
    intent = dict(task_ref=c.record_ref(root / 'jobs' / f'{job["step"]:03d}.json'),
        native_profile=job['native_profile'], native_subagents=job['native_subagents'])
    accepted_budget.accept_native(root, job['step'], record, intent)
    workspace = c.workspace(root, job['step'])
    session = dict(outcome['native_identity']['session'], workspace=str(workspace))
    outcome['native_identity'].update(session=session, home=session['home'], path=session['path'])
    c.write(record / 'native-outcome.json', outcome)
    c.write(record / 'native-process.json', dict(task_ref=intent['task_ref']))
    return record, intent, outcome, session


def supply_live_session(monkeypatch, *, intent, outcome, session, workspace):
    calls = []

    def supplied(adapter, saved, native_intent, work):
        assert saved == outcome and native_intent == intent and work == workspace
        calls.append(adapter.options['subagents'])
        assert adapter.options['subagents'] is False
        return dict(session, thread_event=outcome['native_thread_event'])

    # Only live acquisition is stubbed. Retained metadata/event validation and
    # accepted native/task/material identities remain on their actual owners.
    monkeypatch.setattr(backends.Codex, 'acquire_participant_session', supplied)
    return calls


@pytest.mark.parametrize('global_subagents,role_subagents', [(True, False), (False, True)])
def test_capture_uses_frozen_role_subagent_scope(tmp_path, monkeypatch, global_subagents, role_subagents):
    root = prepared(tmp_path, global_subagents=global_subagents, worker_subagents=role_subagents)
    job = c.build_job(root, 1, 'W', [])
    declared = backends.selected(c.settings(root))
    actual = declared.for_job(job)
    assert declared.options['subagents'] is global_subagents
    assert job['native_subagents'] is role_subagents
    assert actual.options['subagents'] is role_subagents
    assert declared.complete_authorship is not global_subagents
    assert actual.complete_authorship is not role_subagents
    record, intent, outcome, session = accepted_attempt(root, job)
    # No meter was supplied: close accounting conservatively, without claiming
    # complete usage or invoking a provider to fill the missing observation.
    accepted_budget.accept_terminal(root, 1, record, meter=None, unknown=True)
    calls = supply_live_session(monkeypatch, intent=intent, outcome=outcome,
        session=session, workspace=c.workspace(root, 1))
    reference = review.capture_participants(root, job)
    captured = c.read(root / reference['path'])
    assert captured['complete'] is not role_subagents, captured
    if role_subagents:
        assert calls == []
        assert captured['participants'] == []
        assert captured['attempts'][0]['status'] == 'unsupported'
        assert captured['attempts'][0]['reason'] == 'native_participant_identity_unsupported'
    else:
        assert calls == [False]
        assert captured['attempts'][0]['status'] == 'known'
        assert captured['participants'] == [['codex_exec', outcome['thread_id']]]
        assert review.participants(root, job, dict(native_authorship=reference)) == dict(
            complete=True, participants=captured['participants'], reason=None)


@pytest.mark.parametrize('global_subagents,role_subagents', [(True, False), (False, True)])
def test_formal_choices_use_frozen_reviewer_scope(tmp_path, global_subagents, role_subagents):
    root = prepared(tmp_path, global_subagents=global_subagents,
        worker_subagents=False, reviewer_subagents=role_subagents)
    job = c.build_job(root, 1, 'A', [])
    assert job['native_subagents'] is role_subagents
    assert backends.selected(c.settings(root)).options['subagents'] is global_subagents
    expected = dict(launcher_supported=not role_subagents, unavailable=[])
    assert job['formal_review_choices'] == expected
    assert review.choice_constraints(root, job, references=[]) == expected
    assert c.read(root / 'jobs/001.json')['formal_review_choices'] == expected


@pytest.mark.parametrize('global_subagents,role_subagents', [(True, False), (False, True)])
def test_formal_request_uses_frozen_reviewer_scope(tmp_path, monkeypatch, global_subagents, role_subagents):
    root = prepared(tmp_path, global_subagents=global_subagents,
        worker_subagents=False, reviewer_subagents=role_subagents)
    reviewer = c.build_job(root, 2, 'A', [])
    assert reviewer['native_subagents'] is role_subagents
    assert backends.selected(c.settings(root)).options['subagents'] is global_subagents
    # Supply the already-validated subject/author facts at their existing seams.
    # This unit case isolates request capability selection; it creates no native
    # identity receipt, stopped-process proof or scientific qualification.
    material = dict(path='inputs/parents/001/artifacts/subject.txt')
    selected = [dict(origin_step=1, material=material)]
    subject = dict(producer_step=1, producer_seal={'path': 'retained-subject-seal'}, material=material)
    identity = dict(complete=True, participants=[['fixture', 'retained-author']], reason=None)
    calls = []

    def supplied_subject(actual_root, selection):
        assert actual_root == root and selection == selected
        calls.append('subject')
        return subject

    def supplied_authors(actual_root, step):
        assert actual_root == root and step == subject['producer_step']
        calls.append('authors')
        return identity

    monkeypatch.setattr(review, '_subject', supplied_subject)
    monkeypatch.setattr(review, 'authors', supplied_authors)
    request = review._request_value(root, selected, reviewer)
    assert calls == ['subject', 'authors']
    assert request['authors'] == identity
    assert request['subject'] == subject
    assert request['contract'] == review.contract(root, reviewer)
    assert request['reason'] == ('reviewer_identity_unsupported' if role_subagents else None)
