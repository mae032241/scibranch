"""Controlled Pi authorship parity, not scientific or exhaustive-call evidence.

Unmarked tests validate supplied synthetic facts only, with no processes. Marked
cases use installed Pi, fake credentials and a finite loopback provider; cleanup
belongs to a separate durable collector, never to pytest's process group.
"""
import copy
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from scidiscovery.operations.review_rules import independence
from scidiscovery.research_loop import (backends, budget, contracts as c, execution,
    configuration_updates, formal_review as review, native, originals, pi_backend as pi, pi_observation,
    pi_transport, pi_sessions, qualification as q, research_config)
from test_pi_backend_v2 import (OPTIONS, PROFILE, STAMP, adapter, settings, options,
    configure_installed_pi_fixture, installed_pi_fixture)


def test_formal_publication_keeps_exact_review_context_and_local_admission_separate(monkeypatch):
    from scidiscovery.research_loop import accepted_prefix
    local = dict(version=accepted_prefix.VERSION, branch='child')
    history = dict(prefix=dict(local, branch='original'), breadth_snapshot={'path': 'original-cut'},
        inputs=[], view={})
    job = dict(formal_review={'path': 'exact-request'}, purpose='formal_review', history=history)
    admitted = dict(prefix=local, purpose='formal_review')
    deny_io(monkeypatch)
    accepted_prefix._validate_publication_prefix(job, admitted, local,
        review_binding=dict(history=copy.deepcopy(history)))
    with pytest.raises(ValueError, match='admitted accepted prefix'):
        accepted_prefix._validate_publication_prefix(dict(history=history), admitted, local,
            review_binding=None)


@pytest.mark.parametrize('damage', ['missing_binding', 'wrong_history', 'wrong_purpose',
    'wrong_admitted_purpose', 'wrong_admitted_prefix'])
def test_formal_publication_rejects_unvalidated_or_changed_cut(monkeypatch, damage):
    from scidiscovery.research_loop import accepted_prefix
    prefix = dict(version=accepted_prefix.VERSION, branch='child')
    history = dict(prefix=dict(prefix, branch='original'), inputs=[], view={})
    job = dict(formal_review={'path': 'exact-request'}, purpose='formal_review', history=history)
    admitted = dict(prefix=prefix, purpose='formal_review')
    binding = dict(history=copy.deepcopy(history))
    if damage == 'missing_binding': binding = None
    elif damage == 'wrong_history': binding['history']['prefix']['branch'] = 'borrowed'
    elif damage == 'wrong_purpose': job['purpose'] = None
    elif damage == 'wrong_admitted_purpose': admitted['purpose'] = None
    else: admitted['prefix'] = dict(prefix, branch='borrowed')
    deny_io(monkeypatch)
    with pytest.raises(ValueError):
        accepted_prefix._validate_publication_prefix(job, admitted, prefix, review_binding=binding)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def compact_pi_facts():
    """Self-consistent validator fixture, NOT acquired/qualified native evidence."""
    work = '/instance/project/nodes/001'
    home = '/instance/native-homes/001'
    path = home + '/sessions/pi-session.jsonl'
    nonce = 'a' * 64
    observer = dict(nonce=nonce, path=path, workspace=work, required=False,
        metadata=home + '/sessions/.pi-observation-' + nonce + '.json',
        extension=home + '/sessions/.scid-pi-observation-' + nonce + '.ts')
    header = dict(type='session', version=3, id='sdk-session', timestamp=STAMP, cwd=work)
    session = dict(backend='pi', home=home, path=path, workspace=work,
        thread_id='sdk-session', profile=dict(PROFILE), header=json.dumps(header)+'\n',
        identity=[1, 2], size_bytes=1000, sha256='b'*64)
    event = dict(type='pi.session_bound', thread_id='sdk-session', path=path,
        workspace=work, profile=dict(PROFILE))
    intent = dict(task_ref=dict(path='/instance/jobs/001.json'), native_profile=dict(PROFILE))
    process = dict(task_ref=intent['task_ref'], boot_id='fixture-boot', pid=10,
        start_ticks=20, pgid=10, sid=10, ppid=9)
    final = dict(sha256=hashlib.sha256(b'{malformed').hexdigest(), size_bytes=10)
    prompt_hash = hashlib.sha256(b'original accepted prompt').hexdigest()
    delivery = dict(source=pi_observation.SOURCE, nonce=nonce, attempt=0,
        sequence=3, acknowledged=3, pending=False, fault=None, terminal=True)
    argv = ['/installed/pi', '--mode', 'rpc', *pi.RESOURCE_FLAGS,
        '--tools', 'read,bash,edit,write,grep,find,ls', '--session', path,
        '--session-dir', home+'/sessions', '--provider', 'fixture', '--model',
        'exact-model', '--thinking', 'high', '--extension', observer['extension']]
    launch = dict(kind='pi_cli_closed_resources_v1', launcher=argv[0],
        resource_policy=pi_transport.resource_policy(), resources=[], observer=observer,
        observer_source=dict(version=1, path=observer['extension'], sha256='c'*64,
            size_bytes=1234), source_readonly=True)
    transport = dict(type=pi_transport.CONTROLLED_EXIT, version=1, argv=argv,
        session_id='sdk-session', path=path, workspace=work, profile=dict(PROFILE),
        resource_policy=pi_transport.resource_policy(), resources=[],
        observer_start=dict(type=pi_observation.BOUND, nonce=nonce, path=path,
            workspace=work, session_id='sdk-session'),
        observer_command=pi_observation.command_proof(observer['extension']),
        prompt_sha256=prompt_hash, disposition='started',
        settled=dict(type='agent_settled', aborted=False), idle=True,
        stdin_closed=True, stdout_eof=True, drained=True, pending_commands=0, returncode=0)
    outcome = dict(native_backend='pi', native_started=True, returncode=0,
        stop_reason=None, cleanup='observed_group_cleaned', thread_id='sdk-session',
        terminal_observed=True, native_failure=None, native_activity_observed=True,
        native_thread_event=event, shared_inputs=dict(enforcement='kernel_readonly_mounts'),
        terminal_evidence=dict(final=final, thread='sdk-session', ambiguous=False),
        metering=dict(session_binding=session, turn_id='native-user', coverage='terminal',
            metadata_delivery={key: delivery[key] for key in ('acknowledged', 'pending', 'fault')}))
    proof = dict(version=1, kind=pi.PARTICIPANT_PROOF, capability=pi.PARTICIPANT_SCOPE,
        session=copy.deepcopy(session), launch=launch, transport=transport,
        persisted=dict(user=dict(id='native-user', prompt_sha256=prompt_hash),
            leaf='native-final', baseline_leaf=None, baseline_count=0, baseline_offset=0,
            entry_count=2, branch_sha256='d'*64, responses=[dict(id='native-final',
                status='completed', profile=dict(PROFILE), final=final)], delivery=delivery),
        prefix_verified=True, intent_sha256=digest(intent),
        outcome_sha256=digest(pi._outcome_facts(outcome)), process=copy.deepcopy(process))
    outcome['native_identity'] = dict(backend='pi', participant_scope=pi.PARTICIPANT_SCOPE,
        session=session, home=home, path=path, session_id='sdk-session', controlled=proof)
    return outcome, intent, process, work


def retained_rows(outcome):
    binding = outcome['native_identity']
    session = copy.deepcopy(dict(binding['session'], controlled=binding['controlled']))
    row = dict(attempt=0, proof_kind=pi.PARTICIPANT_PROOF, intent={'path': 'intent'},
        outcome={'path': 'outcome'}, session=session)
    metadata = dict(version=1, kind=pi.PARTICIPANT_PROOF, intent=row['intent'],
        outcome=row['outcome'], session=copy.deepcopy(session))
    return row, metadata


def deny_io(monkeypatch):
    def forbidden(*a, **kw): pytest.fail('Pure retained validator acquired filesystem/process facts')
    for name in ('open', 'stat', 'lstat', 'resolve', 'read_bytes', 'read_text', 'exists', 'is_file'):
        monkeypatch.setattr(Path, name, forbidden)
    for name in ('open', 'stat', 'lstat', 'fstat', 'read', 'readlink', 'scandir', 'listdir', 'access', 'getcwd'):
        monkeypatch.setattr(os, name, forbidden)
    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)


def test_fresh_capability_and_retained_false_survive_revision_and_restart(monkeypatch):
    fresh = backends.configuration('pi', ['pi'], dict(OPTIONS))
    assert fresh['native_capabilities']['complete_native_authorship'] is True
    old = copy.deepcopy(fresh)
    old['native_capabilities']['complete_native_authorship'] = False
    old['goal_id'] = 'old-instance'
    outcome, intent, _, work = compact_pi_facts()
    deny_io(monkeypatch)
    for workspace in ('read-only', 'workspace-write'):
        revised = backends.configuration('pi', ['other-pi'], dict(OPTIONS, workspace=workspace), existing=old)
        restarted = backends.selected(json.loads(json.dumps(revised)))
        assert revised['native_capabilities']['complete_native_authorship'] is False
        assert not restarted.complete_authorship
        forked = backends.configuration('pi', ['fork-pi'], revised['native_options'], existing=revised)
        assert not backends.selected(forked).complete_authorship
        with pytest.raises(ValueError, match='frozen native authorship unsupported'):
            restarted.acquire_participant_session(outcome, intent, work)
    assert adapter(goal_id='old-with-no-capabilities').complete_authorship is False
    assert backends.selected(fresh).complete_authorship is True
    assert pi.Pi.readonly_capability(settings())['status'] == 'unsupported'


def test_fresh_formal_request_gate_is_distinct_from_retained_unsupported_gate(monkeypatch):
    # Test choice admission only: these supplied author facts are not a native
    # receipt, and cannot seal a formal verdict or qualify any science.
    subject = dict(producer_step=1, material={'path': 'exact-subject'})
    monkeypatch.setattr(review, 'contract', lambda *a: {'path': 'contract'})
    monkeypatch.setattr(review, '_subject', lambda *a: subject)
    monkeypatch.setattr(review, 'authors', lambda *a: dict(complete=True,
        participants=[['fixture', 'author']]))
    frozen = settings()
    monkeypatch.setattr(review.configuration_updates, 'for_job', lambda *a: frozen)
    deny_io(monkeypatch)
    assert review._request_value('/instance', [subject], {'role': 'A'})['reason'] is None
    frozen['native_capabilities'] = dict(complete_native_authorship=False)
    assert review._request_value('/instance', [subject], {'role': 'A'})['reason'] == 'reviewer_identity_unsupported'


def test_capability_or_session_bound_event_alone_never_qualifies_old_receipt(monkeypatch):
    outcome, intent, process, work = compact_pi_facts()
    outcome['native_identity'].pop('controlled')
    outcome['native_identity']['participant_scope'] = 'unsupported'
    row, metadata = dict(session={}), dict(session={})
    deny_io(monkeypatch)
    assert adapter().complete_authorship
    with pytest.raises(ValueError): adapter().acquire_participant_session(outcome, intent, work)
    with pytest.raises(ValueError): pi.Pi.validate_participant_proof(row, metadata, outcome, intent, work)
    with pytest.raises(ValueError):
        pi.Pi.validate_participant_event(outcome['native_thread_event'], outcome, intent, work)


def test_pure_provider_and_shared_compact_validation_same_actor_not_attempt(monkeypatch):
    outcome, intent, process, work = compact_pi_facts()
    row, metadata = retained_rows(outcome)
    deny_io(monkeypatch)
    actor = ('pi_native_session', 'sdk-session')
    assert adapter().acquire_participant_session(outcome, intent, work) == row['session']
    assert pi.Pi.validate_participant_proof(row, metadata, outcome, intent, work) == actor
    pi.Pi.validate_participant_process(outcome, process)
    assert pi.Pi.validate_participant_event(outcome['native_thread_event'], outcome, intent, work) == actor
    assert review._metadata_participant(row, metadata, outcome, intent, process, work, row['intent']) == actor
    # A malformed final still has an author; later output parsing is not native
    # identity. Attempt, nonce and PID must never manufacture a fresh actor.
    outcome.update(output_status='invalid_json', raw_ref={'path': 'malformed-original'})
    assert pi.Pi.validate_participant_proof(row, metadata, outcome, intent, work) == actor
    assert independence({actor}, {actor}) == 'overlap'
    assert independence({actor}, {('pi_native_session', 'fresh-sdk-session')}) == 'independent'


@pytest.mark.parametrize('field,replacement', [
    ('launch.observer_source', None),
    ('launch.observer_source.path', '/borrowed/observer.ts'),
    ('launch.observer_source.sha256', 'not-a-digest'),
    ('launch.source_readonly', False),
    ('launch.observer.nonce', '0'*64),
    ('transport.observer_start.nonce', '0'*64),
    ('transport.observer_start.session_id', 'borrowed-session'),
    ('transport.observer_command.sourceInfo.source', 'local'),
    ('transport.observer_command.sourceInfo.path', '/borrowed/observer.ts'),
    ('transport.session_id', 'borrowed-session'),
    ('transport.profile.model', 'fixture/borrowed'),
    ('transport.path', '/borrowed/session.jsonl'),
    ('transport.prompt_sha256', '0'*64),
    ('transport.disposition', 'queued'),
    ('transport.settled', None),
    ('transport.idle', False),
    ('transport.drained', False),
    ('transport.stdout_eof', False),
    ('transport.stdin_closed', False),
    ('transport.pending_commands', 1),
    ('transport.returncode', 1),
    ('prefix_verified', False),
    ('intent_sha256', '0'*64),
    ('outcome_sha256', '0'*64),
    ('persisted.user.prompt_sha256', '0'*64),
    ('persisted.delivery.terminal', False),
    ('persisted.delivery.pending', True),
    ('persisted.delivery.acknowledged', 0),
    ('persisted.delivery.source', 'uncontrolled'),
    ('persisted.responses', []),
    ('session', None),
])
def test_missing_or_borrowed_control_facts_refuse_purely(monkeypatch, field, replacement):
    outcome, intent, process, work = compact_pi_facts()
    target = outcome['native_identity']['controlled']
    parts = field.split('.')
    for name in parts[:-1]: target = target[name]
    target[parts[-1]] = replacement
    row, metadata = retained_rows(outcome)
    deny_io(monkeypatch)
    with pytest.raises(ValueError): pi.Pi.validate_participant_proof(row, metadata, outcome, intent, work)


@pytest.mark.parametrize('damage', ['process', 'task', 'event', 'workspace', 'unknown-kind', 'borrowed-metadata'])
def test_shared_bindings_and_provider_dispatch_do_not_accept_borrowed_identity(monkeypatch, damage):
    outcome, intent, process, work = compact_pi_facts()
    row, metadata = retained_rows(outcome)
    if damage == 'process': process['pid'] += 1
    elif damage == 'task': process['task_ref'] = {'path': '/other/job'}
    elif damage == 'event': outcome['native_thread_event']['thread_id'] = 'other'
    elif damage == 'workspace': work = '/other/workspace'
    elif damage == 'unknown-kind': row['proof_kind'] = metadata['kind'] = 'unrecognized_native_v9'
    else: metadata['session']['controlled']['transport']['session_id'] = 'other'
    deny_io(monkeypatch)
    with pytest.raises(ValueError):
        review._metadata_participant(row, metadata, outcome, intent, process, work, row['intent'])


@pytest.mark.parametrize('process', [None, {}, dict(boot_id='borrowed', pid=10, start_ticks=20, pgid=10, sid=10, ppid=9)])
def test_missing_or_unbound_process_receipt_refuses_without_io(monkeypatch, process):
    outcome, _, _, _ = compact_pi_facts()
    deny_io(monkeypatch)
    with pytest.raises(ValueError, match='writer changed'):
        pi.Pi.validate_participant_process(outcome, process)


def test_native_child_cannot_emit_bridge_reserved_exit_receipt():
    binding = pi_transport.Binding('fixture', 'exact-model', 'high', '/private/session', 'prompt')
    binding.prompt_sent = binding.accepted = binding.idle = True
    binding.settled = dict(type='agent_settled', aborted=False)
    with pytest.raises(pi_transport.TransportFault, match='invalid_rpc_record'):
        binding.event(dict(type=pi_transport.CONTROLLED_EXIT))


def test_legacy_header_semantics_are_delegated_to_non_codex_provider(tmp_path, monkeypatch):
    """A provider seam regression; this synthetic provider grants no real identity."""
    calls = []
    class HeaderProvider(backends.Protocol):
        name = 'header-fixture'
        @staticmethod
        def authorship_scope(binding): return binding.get('backend') == 'header-fixture'
        @staticmethod
        def validate_participant_header(raw, outcome, workspace):
            calls.append((raw, str(workspace)))
            if raw != b'provider-original\n': raise ValueError('provider header rejected')
            return ('fixture-header-actor', 'provider-session')
    monkeypatch.setitem(backends._PROVIDERS, HeaderProvider.name, HeaderProvider)
    root = tmp_path
    record = root/'native-evidence/001'
    record.mkdir(parents=True)
    header = root/'retained-header'
    header.write_bytes(b'provider-original\n')
    intent_ref = {'path': 'native-evidence/001/native-intent.json'}
    outcome_ref = {'path': 'native-evidence/001/native-outcome.json'}
    proof = dict(complete=True, participants=[['fixture-header-actor', 'provider-session']], attempts=[
        dict(attempt=0, intent=intent_ref, outcome=outcome_ref, status='known',
            participant=['fixture-header-actor', 'provider-session'], session={'thread_id': 'provider-session'},
            header=dict(path='retained-header', size_bytes=len(header.read_bytes())))])
    outcome = dict(native_identity=dict(backend=HeaderProvider.name))
    monkeypatch.setattr(execution, 'records', lambda *a: [record])
    monkeypatch.setattr(q, '_read', lambda root, ref: proof if ref['path'] == 'proof' else outcome)
    monkeypatch.setattr(q, '_ref', lambda root, path: dict(path=path.relative_to(root).as_posix()))
    result = review.participants(root, dict(step=1), dict(native_authorship={'path': 'proof'}),
        source=SimpleNamespace(inherited=False))
    assert result == dict(complete=True, participants=proof['participants'], reason=None)
    assert calls == [(b'provider-original\n', str(c.workspace(root, 1)))]
    header.write_bytes(b'provider-spoof!!!\n')
    proof['attempts'][0]['header']['size_bytes'] = len(header.read_bytes())
    with pytest.raises(ValueError, match='provider header rejected'):
        review.participants(root, dict(step=1), dict(native_authorship={'path': 'proof'}),
            source=SimpleNamespace(inherited=False))


def test_inherited_attempt_inventory_rejects_omitted_tail_even_if_manifest_also_omits_it(monkeypatch):
    origin = dict(goal_id='original', settings_ref={'path': '/original/settings.json'})
    base = 'native-evidence/001'
    refs = lambda number: {'path': base + (f'/continuations/{number:03d}' if number else '') + '/native-intent.json'}
    source = SimpleNamespace(step=1, original_root='/original', origin_identity=origin,
        entries=[dict(path=refs(n)['path'], origin_identity=origin) for n in range(2)])
    proof = dict(attempts=[dict(attempt=n, intent=refs(n)) for n in range(2)])
    seal = dict(raw_ref={'path': base+'/continuations/001/science-output.json'}, execution_ref=dict(
        attempt=1, chain=[{'native-intent.json': q.control_reference('/original', refs(n))} for n in range(2)]))
    deny_io(monkeypatch)
    review._inherited_attempt_inventory(source, proof, seal)
    proof['attempts'].pop()
    with pytest.raises(ValueError, match='omits a native attempt'):
        review._inherited_attempt_inventory(source, proof, seal)
    source.entries.pop()
    with pytest.raises(ValueError, match='sealed attempt inventory'):
        review._inherited_attempt_inventory(source, proof, seal)
    seal['execution_ref'] = dict(attempt=0, chain=seal['execution_ref']['chain'][:1])
    with pytest.raises(ValueError, match='terminal attempt'):
        review._inherited_attempt_inventory(source, proof, seal)


def test_retained_interrupted_original_cannot_be_promoted_to_complete(monkeypatch):
    origin = dict(goal_id='original', settings_ref={'path': '/original/settings.json'})
    base = 'native-evidence/001/'
    source = SimpleNamespace(step=1, original_root='/original', origin_identity=origin,
        entries=[dict(path=base+'native-intent.json', origin_identity=origin)])
    job_ref = {'path': '/original/jobs/001.json'}
    row = dict(attempt=0, intent={'path': base+'native-intent.json'}, outcome=None,
        interruption={'path': base+'interruption.json'}, status='unsupported')
    proof = dict(complete=False, participants=[], attempts=[row])
    seal = dict(native_authorship={'path': base+'native-authorship.json'},
        job_ref=job_ref, raw_ref={'path': base+'science-output.json'})
    loaded = {base+'native-authorship.json': proof,
        base+'native-intent.json': dict(task_ref=job_ref),
        base+'interruption.json': dict(task_ref=job_ref,
            intent_ref=q.control_reference('/original', row['intent']))}
    deny_io(monkeypatch)
    # Explicit already-loaded originals stand in for the storage producer;
    # nothing here invokes live process reconciliation or manufactures proof.
    monkeypatch.setattr(originals, 'resolve_ref', lambda source, ref: ref['path'])
    monkeypatch.setattr(c, 'read', lambda path: loaded[path])
    assert review._inherited_participants(source, dict(step=1), seal) == dict(
        complete=False, participants=[], reason='native_identity_unavailable')
    proof['complete'] = True
    with pytest.raises(ValueError, match='completeness changed'):
        review._inherited_participants(source, dict(step=1), seal)


def test_exact_subject_and_not_qualified_pass_refuse_without_io(monkeypatch):
    subject = dict(producer_step=1, producer_seal={'path': 'seal'}, material=dict(
        path='artifacts/subject.txt', source='/original/subject.txt', size_bytes=5))
    receipt = {'path': 'formal-review.json'}
    facts = dict(proof=dict(contract={'path': 'contract'}, status='passed'),
        request=dict(contract={'path': 'contract'}), subject=subject, bound_receipt=receipt)
    deny_io(monkeypatch)
    assert review.require_review_from_facts(subject, receipt, facts=facts) == receipt
    wrong = copy.deepcopy(subject); wrong['material']['source'] = '/same-bytes/other-subject.txt'
    with pytest.raises(ValueError, match='Exact subject'):
        review.require_review_from_facts(wrong, receipt, facts=facts)
    facts['proof'].update(status='not_qualified', verdict='pass', independence='overlap')
    with pytest.raises(ValueError, match='independent sealed pass'):
        review.require_review_from_facts(subject, receipt, facts=facts)


@pytest.mark.parametrize('storage', ['local', 'inherited', 'nested'])
def test_formal_request_uses_supplied_original_owner(tmp_path, monkeypatch, storage):
    """Synthetic request storage only; this does not qualify a review verdict."""
    root = tmp_path/'instance'
    root.mkdir()
    origin = dict(goal_id='original', settings_ref={'path': str(root/'settings.json')})
    owner_origin = (dict(goal_id='child', settings_ref={'path': '/child/settings.json'})
        if storage == 'nested' else origin)
    relative = 'native-evidence/002/formal-review-request.json'
    request = dict(subject=dict(producer_step=1, producer_seal=dict(
        path='native-evidence/001/result-identity.json', size_bytes=123),
        material=dict(path='inputs/parents/001/artifacts/subject.txt', size_bytes=5)),
        requester=dict(step=2), question=review.QUESTION)
    raw = (json.dumps(request)+'\n').encode()
    if storage == 'local':
        owner = originals.NodeSource(origin, 3, str(root), root)
        path = root/relative
    else:
        entry = dict(origin_identity=origin, path=relative, size_bytes=len(raw),
            storage_path='inherited/original/'+relative, visibility='host_proof')
        origins = {identity['goal_id']: dict(identity=identity, original_root=(
            '/child' if identity == owner_origin and storage == 'nested' else str(root)))
            for identity in (origin, owner_origin)}
        snapshot = originals._snapshot(dict(entries=[entry], origins=origins))
        store = root/'host-runtime/fork'
        owner = originals.NodeSource(owner_origin, 3, origins[owner_origin['goal_id']]['original_root'],
            store, snapshot=snapshot)
        path = store/entry['storage_path']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    reference = dict(origin_identity=origin, path=relative, size_bytes=len(raw))
    def rediscovery(*a, **kw): pytest.fail('Supplied request owner was rediscovered')
    monkeypatch.setattr(originals, '_manifest', rediscovery)
    monkeypatch.setattr(originals, 'node', rediscovery)
    monkeypatch.setattr(originals, 'local_identity', rediscovery)
    context, value = review._request_original(root, reference, owner=owner)
    assert value == request and context.step == 2
    assert context.origin_identity == origin and context.snapshot is owner.snapshot
    assert context.storage_root == owner.storage_root
    with pytest.raises(ValueError, match='differs from its frozen binding|Original size changed'):
        review._request_original(root, dict(reference, size_bytes=len(raw)+1), owner=owner)
    wrong = dict(origin, settings_ref={'path': '/unrelated/settings.json'})
    # An unbound local origin may use the existing acquisition wrapper, but it
    # must not silently adopt the supplied local owner's identity or bytes.
    with monkeypatch.context() as mismatch:
        if storage == 'local': mismatch.setattr(originals, '_manifest', lambda *a: None)
        with pytest.raises(ValueError, match='exact origin binding|not bound in this fork'):
            review._request_original(root, dict(reference, origin_identity=wrong), owner=owner)


@pytest.mark.parametrize('explicit_origin', [False, True])
@pytest.mark.parametrize('inherited', [False, True])
def test_formal_material_inputs_pass_known_owner_to_request_acquisition(monkeypatch, explicit_origin, inherited):
    from scidiscovery.research_loop import handoff_state
    origin = dict(goal_id='original', settings_ref={'path': '/original/settings.json'})
    snapshot = (originals._snapshot(dict(entries=[], origins={'original': dict(
        identity=origin, original_root='/original')})) if inherited else None)
    owner = originals.NodeSource(origin, 3, '/original', Path('/original'), snapshot=snapshot)
    reference = dict(path='native-evidence/002/formal-review-request.json', size_bytes=123)
    if explicit_origin: reference['origin_identity'] = origin
    job = dict(material_refs=[], formal_review=reference)
    sources = {}
    class AcquiredRequest(Exception): pass
    def acquire(root, reference, *, owner=None):
        assert root == Path('/original') and owner is expected_owner
        assert reference == dict(job['formal_review'], origin_identity=origin)
        raise AcquiredRequest
    expected_owner = owner
    monkeypatch.setattr(review, '_request_original', acquire)
    deny_io(monkeypatch)
    with pytest.raises(AcquiredRequest):
        handoff_state.material_inputs(Path('/original'), job, sources=sources, source=owner)
    assert job['formal_review'] == reference and sources == {}


@pytest.mark.parametrize('nested', [False, True])
def test_formal_fork_retains_requester_inclusive_canonical_prefix(monkeypatch, nested):
    """Synthetic retained controls only: no native process or scientific claim."""
    from scidiscovery.research_loop import accepted_prefix, fork_sources, protocol
    branch = dict(goal_id='request-origin', settings_ref={'path': '/request/settings.json'})
    worker_origin = dict(goal_id='worker-origin', settings_ref={'path': '/worker/settings.json'})
    child_origin = dict(goal_id='child', settings_ref={'path': '/child/settings.json'})
    request_owner = originals.NodeSource(branch, 3, '/request', Path('/retained'))
    worker = originals.NodeSource(worker_origin, 1, '/worker', Path('/retained'))
    requester = originals.NodeSource(branch, 2, '/request', Path('/retained'))
    selected = (originals.NodeSource(child_origin, 3, '/child', Path('/retained'))
        if nested else request_owner)
    owners = {row.origin_identity['goal_id']: row for row in (request_owner, worker, selected)}
    seal = lambda owner: dict(path=f'{owner.original_root}/native-evidence/{owner.step:03d}/result-identity.json',
        size_bytes=100+owner.step)
    members = [dict(branch=branch, origin_identity=owner.origin_identity, step=owner.step,
        ordinal=ordinal, seal_ref=seal(owner), navigation=dict(
            path=f'nodes/{owner.origin_identity["goal_id"]}/{owner.step:03d}/index.json', size_bytes=200+ordinal),
        scientific_event=dict(origin_identity=owner.origin_identity,
            path=f'{owner.original_root}/control-ledger/{ordinal:06d}.json', size_bytes=300+ordinal))
        for ordinal, owner in enumerate((worker, requester), 1)]
    tip = {field: members[-1][field] for field in ('origin_identity', 'step', 'seal_ref', 'navigation')}
    frozen = dict(version=1, branch=branch, tip=tip, cutoff=2, count=2,
        visibility_root=dict(path=accepted_prefix.PUBLIC+'/saved-request'))
    request_ref = dict(origin_identity=branch, path='native-evidence/002/formal-review-request.json', size_bytes=400)
    request = dict(prefix=copy.deepcopy(frozen), requester=dict(step=2, seal=seal(requester)))
    job = dict(step=3, role='A', purpose='formal_review', formal_review=request_ref,
        history=dict(prefix=copy.deepcopy(frozen), inputs=[], view={}), global_materials=[],
        direct_refs=[], direct_materials=[], selected_inputs=[dict(subject='neutral-exact-subject')])
    original_job = copy.deepcopy(job)
    # The old requester.history.nodes override loses W001 and all modern refs.
    jobs = {1: dict(role='W'), 2: dict(role='A', history=dict(nodes=[])), 3: job}
    records = {(branch['goal_id'], request_ref['path']): request}
    expected = []
    namespace = 'saved-request'
    for member in members:
        base = f'{accepted_prefix.MEMBERS}/{namespace}'
        records[(branch['goal_id'], f'{base}/ordinals/{member["ordinal"]:09d}.json')] = dict(
            origin_identity=member['origin_identity'], step=member['step'])
        member_path = f'{base}/{member["origin_identity"]["goal_id"]}/{member["step"]:03d}.json'
        records[(branch['goal_id'], member_path)] = member
        navigation_path = frozen['visibility_root']['path']+'/'+member['navigation']['path']
        records[(branch['goal_id'], navigation_path)] = dict(research_note_files=[])
        expected.append(dict(member_ref=dict(origin_identity=branch, path=member_path, size_bytes=123),
            navigation_ref=dict(origin_identity=branch,
                path=frozen['visibility_root']['path']+'/'+member['navigation']['path'], size_bytes=123),
            scientific_event=dict(origin_identity=member['origin_identity'],
                path=f'control-ledger/{member["ordinal"]:06d}.json', size_bytes=123)))
    class RetainedClosure(fork_sources._Closure):
        def task(self, owner, *, selected=False):
            assert owner is selected_owner and selected
            return job, dict(origin_identity=owner.origin_identity, path='jobs/003.json', size_bytes=123), dict(path='science-task.json')
        def add(self, owner, path, **kwargs):
            return dict(origin_identity=owner.origin_identity, path=path, size_bytes=123)
        def sealed(self, owner, reference):
            assert reference == dict(seal(owner), origin_identity=owner.origin_identity)
            return dict(step=owner.step, origin_identity=owner.origin_identity,
                job_ref=self.add(owner, f'jobs/{owner.step:03d}.json'), seal_ref=reference)
        def attempts(self, *a, **kw): return None
        def snapshot(self, *a): pass
        def phase(self, *a): return None
    selected_owner = selected
    with monkeypatch.context() as guarded:
        deny_io(guarded)
        guarded.setattr(fork_sources.files, 'check_path', lambda path: path)
        guarded.setattr(c, 'settings', lambda root: dict(handoff_protocol=protocol.CURRENT))
        guarded.setattr(protocol, 'require_job_format', lambda job: None)
        guarded.setattr(fork_sources, '_Closure', RetainedClosure)
        guarded.setattr(fork_sources, '_parent_state', lambda root: [])
        guarded.setattr(fork_sources, '_json', lambda key: records[key])
        guarded.setattr(originals, 'node', lambda root, step: selected)
        guarded.setattr(originals, 'source_job', lambda owner: jobs[owner.step])
        guarded.setattr(originals, 'source_settings', lambda owner: {})
        guarded.setattr(originals, 'source_for_ref', lambda owner, ref: owners[ref.get('origin_identity', owner.origin_identity)['goal_id']])
        guarded.setattr(originals, 'resolve_ref', lambda owner, ref: (owner.origin_identity['goal_id'], ref['path']))
        guarded.setattr(originals, 'dependency', lambda owner, step, **kw: {1: worker, 2: requester}[step])
        guarded.setattr(originals, '_manifest', lambda root: None)
        guarded.setattr(originals, 'local_identity', lambda root: selected.origin_identity)
        guarded.setattr(originals, 'history_source', lambda owner, job: request_owner)
        guarded.setattr(originals, 'recovery_refs', lambda owner, job: [])
        guarded.setattr(accepted_prefix, 'current', lambda *a: pytest.fail('Fork reopened latest accepted head'))
        plan = fork_sources.inspect_source('/retained', 3)
        assert job == original_job  # Retention keeps neutral scientific inputs exact.
        job['history']['prefix'] = dict(frozen, count=1)
        with pytest.raises(ValueError, match='immutable requested prefix'):
            fork_sources.inspect_source('/retained', 3)
        job['history']['prefix'] = copy.deepcopy(frozen)
    entries = plan['prefix_entries']
    assert [row['step'] for row in entries] == [1, 2]
    assert [row['origin_identity'] for row in entries] == [worker_origin, branch]
    for entry, refs in zip(entries, expected):
        assert {field: entry[field] for field in refs} == refs
    cut = plan['cut']['history']['prefix']
    assert cut == request['prefix'] == frozen
    assert cut['count'] == len(entries) and cut['cutoff'] == max(row['step'] for row in entries)
    assert cut['tip'] == tip and cut['tip']['step'] == entries[-1]['step']
    assert plan['bootstrap']['completed_source_refs'] == [row['job_ref'] for row in entries]
    assert job == original_job  # Host retention must not expand neutral scientist inputs.


@contextmanager
def finite_peer(turns):
    """Exact finite sequence of tool/final deltas; loopback and fake auth only."""
    requests, errors = [], []
    stop = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def setup(self): self.request.settimeout(2); super().setup()
        def log_message(self, *args): pass
        def do_POST(self):
            try:
                size = int(self.headers.get('Content-Length', '0'))
                assert 0 < size <= 256*1024 and self.path == '/v1/chat/completions'
                assert self.headers.get('Authorization') == 'Bearer synthetic-not-a-credential'
                body = json.loads(self.rfile.read(size)); requests.append(body)
                assert len(requests) <= len(turns)
                assert body['model'] == 'exact-model' and body['scid_fixture_thinking'] == 'high'
                turn = turns[len(requests)-1]
                if isinstance(turn, str):
                    delta, finish = dict(role='assistant', content=turn), 'stop'
                else:
                    name, arguments = turn
                    delta = dict(role='assistant', tool_calls=[dict(index=0,
                        id='authorship-tool-'+str(len(requests)), type='function',
                        function=dict(name=name, arguments=json.dumps(arguments)))])
                    finish = 'tool_calls'
                chunks = [dict(choices=[dict(index=0, delta=delta, finish_reason=None)]),
                    dict(choices=[dict(index=0, delta={}, finish_reason=finish)]),
                    dict(choices=[], usage=dict(prompt_tokens=17, completion_tokens=5,
                        total_tokens=22, prompt_tokens_details=dict(cached_tokens=7)))]
                wire = b''.join(b'data: '+json.dumps(dict(id='authorship-'+str(len(requests)),
                    object='chat.completion.chunk', created=1, model='exact-model', **chunk)).encode()+b'\n\n'
                    for chunk in chunks)+b'data: [DONE]\n\n'
                self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(wire))); self.send_header('Connection', 'close')
                self.end_headers(); self.wfile.write(wire); self.wfile.flush(); self.close_connection = True
            except Exception as error:
                errors.append(type(error).__name__); self.close_connection = True
    server = HTTPServer(('127.0.0.1', 0), Handler); server.timeout = .05
    def serve():
        while not stop.is_set(): server.handle_request()
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    try: yield 'http://127.0.0.1:'+str(server.server_port)+'/v1', requests, errors
    finally:
        stop.set(); thread.join(timeout=3); server.server_close()
        assert not thread.is_alive()


def collect_step(root, step):
    """Same production entry as collect_separately, with an explicit node."""
    env = dict(os.environ)
    source = str(Path(c.__file__).resolve().parents[2])
    env['PYTHONPATH'] = source + os.pathsep + env.get('PYTHONPATH', '')
    code = ('import sys; from pathlib import Path; '
        'from scidiscovery.research_loop import native; '
        'native.execute_native(Path(sys.argv[1]), int(sys.argv[2]))')
    result = subprocess.run([sys.executable, '-c', code, str(root), str(step)],
        cwd=root.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
    assert result.returncode == 0, result.stderr[-8192:].decode(errors='replace')
    return result


def assert_real_participants(root, job):
    record = execution.origin(root, job['step'])
    seal = c.read(record/'result-identity.json')
    proof = q._read(root, seal['native_authorship'])
    accepted = review.participants(root, job, seal)
    assert proof['complete'] and accepted['complete'], dict(
        step=job['step'], proof_complete=proof['complete'], accepted_complete=accepted['complete'],
        proof_reason=proof.get('reason'), accepted_reason=accepted.get('reason'),
        attempts=[{key:row.get(key) for key in ('attempt','proof_kind','complete','reason')}
            for row in proof.get('attempts',[])])
    assert len(proof['participants']) == 1
    for attempt in proof['attempts']:
        outcome, intent = q._read(root, attempt['outcome']), q._read(root, attempt['intent'])
        metadata = q._read(root, attempt['metadata'])
        process = q._read(root, metadata['native_process'])
        assert attempt['proof_kind'] == metadata['kind'] == pi.PARTICIPANT_PROOF
        assert review._metadata_participant(attempt, metadata, outcome, intent, process,
            c.workspace(root, job['step']), q.control_reference(root, attempt['intent'])) == tuple(proof['participants'][0])
    return seal, proof, accepted


@contextmanager
def retain_fork_failure(root, job, *, directory):
    """Save only bounded, named fork facts using the pre-fixture log binding."""
    state = {}
    try:
        yield state
    except Exception as error:
        if directory:
            diagnostic = dict(error=dict(type=type(error).__name__, message=str(error)[:2048]),
                selected_step=job['step'], selected_prefix=job.get('history', {}).get('prefix'),
                preview={key: state.get('preview', {}).get(key) for key in (
                    'status', 'preview_id', 'source', 'cut', 'blockers', 'entry_count', 'size_bytes')})
            try:
                def bounded_json(path):
                    with Path(path).open('rb') as stream:
                        data = stream.read(32769)
                    if len(data) > 32768: raise ValueError('Named diagnostic JSON exceeds 32768 bytes')
                    return json.loads(data)
                owner = originals.node(root, job['step'])
                reference = job['formal_review']
                request_owner = originals.source_for_ref(owner, reference)
                request = bounded_json(originals.resolve_ref(request_owner, reference))
                frozen = request['prefix']
                diagnostic['request_prefix'] = frozen
                diagnostic['requester'] = request['requester']
                # One exact tip member, not a traversal of the retained tree.
                tip = frozen['tip']
                if tip is not None:
                    prefix_owner = originals.source_for_ref(request_owner, dict(
                        origin_identity=frozen['branch'], path='settings.json'))
                    from scidiscovery.research_loop import accepted_prefix
                    path = (f'{accepted_prefix.MEMBERS}/{accepted_prefix._view_key(frozen)}/'
                        f'{originals.identity_key(tip["origin_identity"])}/{tip["step"]:03d}.json')
                    member_ref = originals.original_ref(prefix_owner, path)
                    member = bounded_json(originals.resolve_ref(prefix_owner, member_ref))
                    diagnostic['tip_member_ref'] = member_ref
                    diagnostic['tip_member'] = {key: member.get(key) for key in (
                        'branch', 'origin_identity', 'step', 'ordinal', 'seal_ref', 'navigation', 'scientific_event')}
            except Exception as detail_error:
                diagnostic['detail_error'] = dict(type=type(detail_error).__name__, message=str(detail_error)[:1024])
            # Only this known, already-redacted artifact; never native/auth logs.
            artifact = root/f'native-evidence/{job["step"]:03d}/native-public.jsonl'
            try:
                with artifact.open('rb') as stream:
                    stream.seek(0, os.SEEK_END)
                    stream.seek(max(0, stream.tell()-4096))
                    diagnostic['native_public_tail'] = stream.read(4096).decode(errors='replace')
            except OSError:
                pass
            try:
                target = Path(directory)/'pi-authorship-fork-failure.json'
                text = json.dumps(diagnostic, ensure_ascii=True, indent=2)+'\n'
                if len(text) > 65536:
                    text = json.dumps(dict(error=diagnostic['error'], selected_step=job['step'],
                        diagnostic_truncated=True))+'\n'
                target.write_text(text)
            except (OSError, TypeError, ValueError):
                pass  # Diagnostics must not replace the original process failure.
        raise


@pytest.mark.process_e2e
def test_installed_pi_authors_reviews_and_retains_cold_fork_proofs(tmp_path, monkeypatch, record_property):
    """Actual native actors and shared review path; fixture science is not science."""
    from scidiscovery.research_loop import controller, fork_runtime, fork_service
    failure_log_dir = os.environ.get('SCID_TEST_LOG_DIR')  # Fixture clears this binding.
    launcher, agent = installed_pi_fixture(tmp_path, monkeypatch, record_property)
    value = options(tmp_path, launcher=[launcher], max_steps=6,
        native_options=dict(OPTIONS, workspace='workspace-write'), formal_review_policy={'version': 1},
        resume_policy=dict(pi.DEFAULT_RESUME_POLICY))
    value['public_root'].mkdir(); value['objective'].write_text('Review the exact inert authored UTF-8 artifact; no scientific claim.')
    authored = 'Inert authored original — 原件\n'
    worker = ' \n'+json.dumps(dict(summary='Mechanical authorship', scientific_result='No scientific qualification.',
        limitations=['Finite deterministic loopback only.'], implementation_selection=None,
        stage_intent='review', consultation_question='Does this exact inert original retain its stated bytes?',
        mechanism_findings=None), ensure_ascii=False)+'\n\t'
    # Complete formal-review evidence uses a fresh author. Same-session repair
    # retains incrementally metered originals but no whole-history author proof;
    # that negative contract belongs to the installed repair/interface cases.
    with finite_peer([('write', dict(path='artifacts/subject.txt', content=authored)), worker]) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url)
        prepared = research_config.prepare(**value)
        assert prepared['research_strategy_version'] == 3
        root = value['root']; author_job = c.build_job(root, 1, 'W', [])
        c.write(root/'host-started.json', budget.clock_stamp())
        budget.transition(root, 'advance', 'pi_authorship_mechanics_only')
        collect_step(root, 1)
        assert len(requests) == 2 and errors == []
    records = execution.records(root, 1)
    assert len(records) == 1
    first = records[0]
    assert (first/'science-output.json').read_bytes() == worker.encode()
    author_seal, author_proof, author = assert_real_participants(root, author_job)
    assert len(author_proof['attempts']) == 1
    outcomes = [c.read(first/'native-outcome.json')]
    assert len({outcome['thread_id'] for outcome in outcomes}) == 1
    assert all(row['participant'] == author['participants'][0] for row in author_proof['attempts'])
    assert (c.workspace(root, 1)/'artifacts/subject.txt').read_bytes() == authored.encode()
    prefix = outcomes[0]['native_identity']['session']
    session_path = Path(prefix['path']); data = session_path.read_bytes()
    assert len(data) == prefix['size_bytes']
    assert hashlib.sha256(data).hexdigest() == prefix['sha256']
    assert c.read(first/'usage.json') == dict(input_tokens=34, cached_input_tokens=14, output_tokens=10)
    assert native.usage(root)['observed_tokens'] == 44
    assert native.usage(root)['unknown_steps'] == []
    for path, outcome in zip(records, outcomes):
        assert outcome['budget_slice']['advance_seconds'] > 0
        assert outcome['metering']['context_windows']['windows_started'] == 1
        assert outcome['metering']['observation_capabilities']['native_calls']['status'] == 'unsupported'
        assert outcome['native_identity']['controlled']['process'] == c.read(path/'native-process.json')
        controlled = outcome['native_identity']['controlled']
        private = [controlled['launch']['observer']['nonce'], controlled['launch']['observer']['extension'],
            prefix['home'], outcome['thread_id']]
        public = (path/'native-public.jsonl').read_text()
        for secret in private:
            assert secret not in public
            assert all(secret not in json.dumps(body) for body in requests)
    # Shared producer acceptance must still reject an omitted author attempt.
    with monkeypatch.context() as omitted:
        read_original = q._read
        def missing_tail(instance, reference, **kwargs):
            proof = read_original(instance, reference, **kwargs)
            return dict(proof, attempts=[]) if reference == author_seal['native_authorship'] else proof
        omitted.setattr(q, '_read', missing_tail)
        with pytest.raises(ValueError, match='omits a native attempt'):
            review.participants(root, author_job, author_seal)
    # The real ordinary A returns scoped advice. The existing component
    # fixture binds an explicit exact formal request to these sealed originals;
    # ordinary A gains no formal dispatch action.
    requester = json.dumps(dict(summary='Request exact review', scientific_result='Assess only the inert subject.',
        limitations=['Mechanical fixture only.'], implementation_selection=['W001-I001'],
        next_action='continue_w', decision_request=None,
        route_reason='Assess exact authored bytes independently.'))
    requester_job = c.build_job(root, 2, 'A', [1])
    with finite_peer([requester]) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url); collect_step(root, 2)
        assert len(requests) == 1 and errors == []
    from test_formal_review import exact_request
    review_request = exact_request(root, 2, 1, 'subject.txt',
        'Assess exact authored bytes independently.')
    # Readonly subject overlays do not make a workspace-write declaration
    # readonly. Queue a real next-node policy through its configuration owner;
    # external mode needs no managed Local write grants or execution config.
    configuration = research_config.to_document(root, configuration_updates.editable(root))
    configuration['permissions']['workspace'] = 'read-only'
    configuration['executor']['mode'] = 'external'
    configuration['executor'].pop('config', None)
    saved = configuration_updates.submit(root, configuration, expected_revision=0)
    assert saved['status'] == 'configuration_saved'
    assert saved['configuration'] == configuration
    for prior_job in (author_job, requester_job):
        assert prior_job.get('configuration_revision') is None
        assert configuration_updates.for_job(root, prior_job) == prepared
    reviewer_job = c.build_job(root, 3, 'A', [1], instruction=review.QUESTION,
        evidence_return=dict(kind='formal_review', request=review_request))
    assert reviewer_job['configuration_revision']['revision'] == saved['revision']
    assert configuration_updates.for_job(root, reviewer_job) == saved['settings']
    reviewer_work = c.workspace(root, 3)
    task = c.read(reviewer_work/'science-task.json')
    assert 'history' not in task and 'parents' not in task
    assert 'instruction' not in task['review'] and reviewer_job['instruction'] == review.QUESTION
    subject_path = task['review']['subject']
    # Actual native Bash probes the readonly formal A namespace; no writable
    # budget advice is added merely to obtain another provider POST.
    probe = '''import pathlib
work=pathlib.Path.cwd()
subject=work/__SUBJECT__
assert subject.read_bytes()==__BYTES__
for path in (subject,work/'work/illegal',work/'science-task.json'):
 try:path.write_bytes(b'ILLEGAL')
 except OSError:pass
 else:raise AssertionError('formal A obtained write permission')
print('readonly-formal-a-ok')
'''.replace('__SUBJECT__', repr(subject_path)).replace('__BYTES__', repr(authored.encode()))
    verdict = ' \n'+json.dumps(dict(verdict='pass', summary='Mechanical independent review',
        scientific_result='The exact inert original was readable.', limitations=['Not scientific qualification.']))+'\n\t'
    with finite_peer([('bash', dict(command=shlex.join([sys.executable, '-c', probe]), timeout=5)), verdict]) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url); collect_step(root, 3)
        assert len(requests) == 2 and errors == []
        assert 'readonly-formal-a-ok' in json.dumps(requests[1])
        assert 'Token budget risk.' not in json.dumps(requests)
    reviewer_seal, reviewer_proof, reviewer = assert_real_participants(root, reviewer_job)
    reviewer_record = execution.origin(root, 3)
    reviewer_outcome = c.read(reviewer_record/'native-outcome.json')
    assert reviewer_outcome['budget_notification']['status'] == 'unavailable'
    assert c.read(reviewer_record/'usage.json') == dict(input_tokens=34, cached_input_tokens=14, output_tokens=10)
    assert native.usage(root)['observed_tokens'] == 110
    assert native.usage(root)['unknown_steps'] == []
    assert reviewer['participants'] != author['participants']
    receipt = reviewer_seal['formal_review']; proof = q._read(root, receipt)
    assert proof['verdict'] == 'pass' and proof['independence'] == 'independent' and proof['status'] == 'passed'
    assert review.require_review(root, proof['subject'], receipt) == receipt
    wrong = copy.deepcopy(proof['subject']); wrong['material']['source'] += '.same-bytes-other-origin'
    with pytest.raises(ValueError, match='Exact subject'): review.require_review(root, wrong, receipt)
    assert (execution.origin(root, 3)/'science-output.json').read_bytes() == verdict.encode()
    original_paths = [root/'settings.json', root/reviewer_job['configuration_revision']['path'],
        root/'jobs/001.json', root/'jobs/002.json', root/receipt['path'],
        first/'result-identity.json', reviewer_record/'result-identity.json']
    original_paths += [record/name for record in (first, reviewer_record)
        for name in ('native-intent.json', 'native-outcome.json', 'science-output.json', 'usage.json')]
    retained_originals = {path: path.read_bytes() for path in original_paths}
    # Native copied-ID seeding is intentionally not fabricated. Route the real
    # retained author through shared sealing to exercise actual actor overlap.
    with monkeypatch.context() as self_review:
        self_review.setattr(review, 'participants', lambda *a, **kw: author)
        self_review.setattr(q, '_save', lambda root, path, value: value)
        overlap = review.seal_review(root, reviewer_job, json.loads(verdict), reviewer_seal)
    assert overlap['verdict'] == 'pass' and overlap['independence'] == 'overlap' and overlap['status'] == 'not_qualified'
    with pytest.raises(ValueError, match='independent sealed pass'):
        review.require_review_from_facts(proof['subject'], receipt, facts=dict(proof=overlap,
            request=q._read(root, reviewer_job['formal_review']), bound_receipt=receipt, subject=proof['subject']))
    # Remove only explicitly named private artifacts, not a recursive home scan.
    # Historical acceptance must not use strict current-EOF resume validation.
    private_paths = {session_path}
    private_homes = set()
    retained_facts = []
    for row in author_proof['attempts']:
        outcome, intent = q._read(root, row['outcome']), q._read(root, row['intent'])
        metadata = q._read(root, row['metadata'])
        retained_facts.append((row, metadata, outcome, intent, q._read(root, metadata['native_process'])))
    for node in (1, 2, 3):
        for record in execution.records(root, node):
            outcome = c.read(record/'native-outcome.json')
            observer = outcome['native_identity']['controlled']['launch']['observer']
            private_homes.add(Path(outcome['native_identity']['session']['home']))
            private_paths.update(Path(observer[key]) for key in ('path', 'extension', 'metadata'))
            private_paths.add(Path(observer['metadata']).with_suffix('.lock'))
    for path in private_paths:
        path.unlink(missing_ok=True)
    for home in private_homes:
        (home/'sessions').rmdir()
        home.rmdir()
        assert not home.exists()
    assert not session_path.exists()
    with monkeypatch.context() as pure:
        deny_io(pure)
        for row, metadata, outcome, intent, process in retained_facts:
            assert review._metadata_participant(row, metadata, outcome, intent, process,
                c.workspace(root, 1), q.control_reference(root, row['intent'])) == tuple(author['participants'][0])
    with monkeypatch.context() as cold:
        cold.setattr(pi_sessions, 'load_resume_original', lambda *a: pytest.fail('Historical proof reopened live session'))
        cold.setattr(pi.Pi, 'validate_resume_session', lambda *a: pytest.fail('Historical proof performed live EOF validation'))
        assert review.participants(root, author_job, author_seal) == author
        assert review.require_review(root, proof['subject'], receipt) == receipt
    # Genuine framework forks carry original metadata/process receipts. Selecting
    # review step 3 keeps the authored W prefix without executing the child.
    with controller._writer(root): pass
    limits = dict(max_tokens=1000, max_seconds=25, max_steps=3)
    child = tmp_path/'child'
    with retain_fork_failure(root, reviewer_job, directory=failure_log_dir) as diagnostic:
        preview = fork_service.preview(root, 3, child, budget=limits)
        diagnostic['preview'] = preview
        fork_service.create(root, 3, child, budget=limits, expected_preview_id=preview['preview_id'],
            operation_id='pi-authorship-cold-prefix')
    selected = fork_runtime.build_selected_job(child, 3)
    assert selected['purpose'] == 'formal_review'
    assert configuration_updates.for_job(child, selected)['native_options'] == saved['settings']['native_options']
    inherited = originals.node(child, 1)
    inherited_job = originals.source_job(inherited)
    inherited_seal = c.read(originals.source_record(inherited, 'result-identity.json'))
    assert review.participants(child, inherited_job, inherited_seal, source=inherited) == author
    retained = c.read(originals.resolve_ref(inherited, inherited_seal['native_authorship']))
    assert retained == author_proof
    with monkeypatch.context() as omitted:
        deny_io(omitted)
        with pytest.raises(ValueError, match='omits a native attempt'):
            review._inherited_attempt_inventory(inherited,
                dict(retained, attempts=retained['attempts'][:-1]), inherited_seal)
    for row in retained['attempts']:
        metadata = c.read(originals.resolve_ref(inherited, row['metadata']))
        assert c.read(originals.resolve_ref(inherited, metadata['native_process']))
    # Complete a fresh child reviewer so nested preview uses a genuinely sealed
    # selected node, not synthetic lifecycle receipts or fabricated native IDs.
    with finite_peer([verdict]) as (url, requests, errors):
        configure_installed_pi_fixture(agent, url); collect_step(child, 3)
        assert len(requests) == 1 and errors == []
    child_seal = c.read(execution.origin(child, 3)/'result-identity.json')
    child_review = q._read(child, child_seal['formal_review'])
    assert child_review['status'] == 'passed'
    assert child_review['subject']['material'] == proof['subject']['material']
    assert child_review['subject']['producer_seal']['origin_identity'] == inherited.origin_identity
    assert review.require_review(child, child_review['subject'], child_seal['formal_review']) == child_seal['formal_review']
    with controller._writer(child): pass
    nested = tmp_path/'nested'
    with retain_fork_failure(child, selected, directory=failure_log_dir) as diagnostic:
        preview = fork_service.preview(child, 3, nested, budget=limits)
        diagnostic['preview'] = preview
        fork_service.create(child, 3, nested, budget=limits, expected_preview_id=preview['preview_id'],
            operation_id='pi-authorship-nested-prefix')
    inherited_again = originals.node(nested, 1)
    nested_seal = c.read(originals.source_record(inherited_again, 'result-identity.json'))
    assert inherited_again.origin_identity == inherited.origin_identity
    assert review.participants(nested, originals.source_job(inherited_again), nested_seal,
        source=inherited_again) == author
    assert c.read(originals.resolve_ref(inherited_again, nested_seal['native_authorship'])) == author_proof
    assert all(path.read_bytes() == data for path, data in retained_originals.items())
    assert native.usage(root)['observed_tokens'] == 110
