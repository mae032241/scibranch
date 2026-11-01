"""Authority consumers use produced facts, with every read entrance closed."""
import copy
from contextlib import contextmanager
from pathlib import Path

import pytest

from scidiscovery.research_loop import configuration_updates, contracts as c
from scidiscovery.research_loop import execution, execution_authority as authority
from scidiscovery.research_loop import formal_review as review, handoff_state, originals
from scidiscovery.research_loop import qualification as q


def records():
    root = Path('/authority-fact-instance')
    identity = dict(goal_id='a' * 32, settings_ref=dict(path=str(root / 'settings.json')))
    material = dict(source=str(root / 'project/nodes/002/artifacts/model.txt'),
        path='inputs/parents/002/artifacts/model.txt', size_bytes=7)
    receipt = dict(path='native-evidence/004/formal-review.json', size_bytes=81)
    subject = dict(producer_step=2,
        producer_seal=dict(path='native-evidence/002/result-identity.json', size_bytes=57),
        material=material)
    review_contract = dict(path='qualification/review-contract.json', size_bytes=19)
    proof = dict(review_step=4, subject=subject, contract=review_contract,
        status='passed', verdict='pass', independence='independent')
    request_ref = dict(path='native-evidence/003/formal-review-request.json', size_bytes=43)
    request = dict(contract=review_contract)
    review_facts = dict(proof=proof, request=request, bound_receipt=receipt,
        subject=copy.deepcopy(subject))
    job = dict(step=6, role='W', direct_refs=[dict(path='inputs/parents/005/conclusion.json')],
        direct_materials=[material], evidence_return=dict(kind='formal_review_return', review=receipt))
    policy = dict(contract=dict(path='execution-authority/contract.json', size_bytes=17))
    document = dict(version=1, options=list(authority.OPTIONS), scope=authority.SCOPE,
        stage='decision_originals_resource_gate_separate')
    facts = dict(inputs=copy.deepcopy(authority._job_inputs(job)), contract=policy['contract'],
        qualifications=[receipt])
    return dict(root=root, identity=identity, material=material, receipt=receipt,
        subject=subject, proof=proof, request_ref=request_ref, request=request,
        review_facts=review_facts, job=job, policy=policy, document=document, facts=facts)


def forbidden(*args, **kwargs):
    raise AssertionError('A fact consumer reopened an original')


@contextmanager
def closed_reads(monkeypatch):
    with monkeypatch.context() as closed:
        for module, names in (
            (c, ('read', 'settings', 'settings_for_job', 'regular')),
            (q, ('_read',)),
            (configuration_updates, ('for_job', 'current')),
            (handoff_state, ('sealed_facts', 'source_facts', 'read_sealed')),
            (execution, ('records',)),
            (originals, ('node', 'node_from_base', 'for_origin', 'source_job',
                'source_settings', 'source_record', 'resolve_ref', 'resolve_root_ref',
                'dependency', 'source_for_ref', '_manifest', '_json')),
            (Path, ('open', 'read_bytes', 'read_text', 'stat', 'lstat', 'exists',
                'is_file', 'is_dir', 'is_symlink', 'resolve', 'iterdir', 'glob')),
        ):
            for name in names:
                closed.setattr(module, name, forbidden)
        yield


def test_authority_and_receipt_consumers_do_not_read(monkeypatch):
    data = records()
    job = data['job']
    owner = originals.NodeSource(data['identity'], 5, str(data['root']), data['root'])
    sources = {5: dict(owner=owner, job=dict(execution_qualifications=[data['receipt']],
        evidence_return=dict(kind='formal_review_return', review=data['receipt'])))}
    candidates = [dict(receipt=data['receipt'], proof=data['proof'], review=data['review_facts'])]
    with closed_reads(monkeypatch):
        assert authority.contract_from_facts(data['policy'], document=data['document']) == data['policy']['contract']
        assert authority._qualification_candidates(job, sources=sources) == [data['receipt']]
        assert authority._qualifications(job, candidates=candidates) == [data['receipt']]
        subject = review.subject_from_receipt_facts(data['receipt'], proof=data['proof'], producer_origin=None)
        assert review.require_review_from_facts(subject, data['receipt'], facts=data['review_facts']) == data['receipt']
        authority.bind_job(data['root'], job, facts=data['facts'])
        authority.validate_job(data['root'], job, facts=data['facts'])


@pytest.mark.parametrize('changed', ['receipt', 'subject', 'contract', 'status'])
def test_pure_receipt_acceptance_keeps_each_original_gate(monkeypatch, changed):
    data = records()
    facts = copy.deepcopy(data['review_facts'])
    if changed == 'receipt':
        facts['bound_receipt']['path'] = 'native-evidence/005/formal-review.json'
    elif changed == 'subject':
        facts['subject']['material']['source'] += '.other-original'
    elif changed == 'contract':
        facts['proof']['contract'] = dict(path='qualification/another-review-contract.json')
    else:
        facts['proof']['status'] = 'not_qualified'
    with closed_reads(monkeypatch):
        with pytest.raises(ValueError, match='Exact subject has no compatible independent sealed pass'):
            review.require_review_from_facts(data['subject'], data['receipt'], facts=facts)


def test_unsupported_review_and_unbound_science_never_become_grants(monkeypatch):
    data = records()
    unsupported = dict(data['proof'], status='not_qualified',
        verdict='pass', independence='overlap', scientific_result='approved for execution')
    unrelated = copy.deepcopy(data['proof'])
    unrelated['subject']['material']['source'] += '.different-producer'
    with closed_reads(monkeypatch):
        assert authority._qualifications(data['job'], candidates=[
            dict(receipt=data['receipt'], proof=unsupported, review=None),
            dict(receipt=data['receipt'], proof=unrelated, review=None),
        ]) == []
        disabled = dict(data['facts'], contract=None, qualifications=[])
        invented = dict(data['job'], execution_authority=data['policy']['contract'],
            execution_qualifications=[data['receipt']])
        with pytest.raises(q.IntegrityError, match='invented execution authority'):
            authority.validate_job(data['root'], invented, facts=disabled)
        with pytest.raises(q.IntegrityError, match='contract changed'):
            authority.contract_from_facts(data['policy'], document=dict(data['document'], scope='scientific pass'))


def test_authority_facts_cannot_move_to_another_subject_or_be_changed_through_job(monkeypatch):
    data = records()
    with closed_reads(monkeypatch):
        authority.bind_job(data['root'], data['job'], facts=data['facts'])
        data['job']['execution_qualifications'][0]['path'] = 'native-evidence/005/formal-review.json'
        with pytest.raises(q.IntegrityError, match='qualifications differ'):
            authority.validate_job(data['root'], data['job'], facts=data['facts'])
        changed = copy.deepcopy(data['job'])
        changed['direct_materials'][0]['source'] += '.other-subject'
        with pytest.raises(q.IntegrityError, match='facts belong to another frozen task'):
            authority.bind_job(data['root'], changed, facts=data['facts'])


def test_inherited_subject_interpretation_uses_only_produced_origin(monkeypatch):
    data = records()
    origin = dict(goal_id='b' * 32, settings_ref=dict(path='/original/settings.json'))
    receipt = dict(data['receipt'], origin_identity=origin)
    with closed_reads(monkeypatch):
        subject = review.subject_from_receipt_facts(receipt, proof=data['proof'], producer_origin=origin)
        assert subject['producer_seal']['origin_identity'] == origin
        assert 'origin_identity' not in data['proof']['subject']['producer_seal']


@pytest.mark.parametrize('inherited', [False, True])
def test_authority_producer_reuses_selected_sources_and_reads_each_original_once(monkeypatch, inherited):
    data = records()
    root = data['root']
    identity = data['identity']
    if inherited:
        identity = dict(goal_id='b' * 32, settings_ref=dict(path='/original/settings.json'))
        entry = dict(path='native-evidence/002/result-identity.json',
            origin_identity=identity, size_bytes=data['subject']['producer_seal']['size_bytes'])
        snapshot = originals.SourceSnapshot((entry,), {}, {
            identity['goal_id']: dict(identity=identity, original_root='/original')}, {})
        review_owner = originals.NodeSource(identity, 4, '/original', root / originals.FORK_DIRECTORY,
            snapshot=snapshot)
        data['receipt']['origin_identity'] = identity
    else:
        review_owner = originals.NodeSource(identity, 4, str(root), root)
    parent_owner = originals.NodeSource(data['identity'], 5, str(root), root)
    parent = dict(owner=parent_owner, job=dict(execution_qualifications=[data['receipt']],
        evidence_return=dict(kind='formal_review_return', review=data['receipt'])))
    reviewer = dict(owner=review_owner, job=dict(step=4, formal_review=data['request_ref']),
        seal=dict(formal_review={key:value for key,value in data['receipt'].items() if key != 'origin_identity'}))
    reads, accepted = [], []

    def read_original(given_root, reference, *, source=None):
        assert given_root == root
        reads.append(copy.deepcopy(reference))
        if reference == data['policy']['contract']:
            return data['document']
        if reference == data['receipt']:
            assert source == review_owner if inherited else source is None
            return data['proof']
        assert reference == data['request_ref']
        assert source.origin_identity == identity
        return data['request']

    def accepted_result(path):
        accepted.append(path)
        return dict(verdict='pass')

    monkeypatch.setattr(q, '_read', read_original)
    monkeypatch.setattr(c, 'read', accepted_result)
    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(configuration_updates, 'for_job', forbidden)
    monkeypatch.setattr(originals, 'for_origin', forbidden)
    monkeypatch.setattr(originals, 'node', forbidden)
    monkeypatch.setattr(originals, 'node_from_base', forbidden)
    monkeypatch.setattr(originals, 'source_job', forbidden)
    monkeypatch.setattr(originals, 'source_settings', forbidden)
    monkeypatch.setattr(originals, '_manifest', forbidden)
    monkeypatch.setattr(originals, 'source_record', lambda owner, name: root / f'native-evidence/{owner.step:03d}' / name)
    monkeypatch.setattr(handoff_state, 'sealed_facts', forbidden)
    monkeypatch.setattr(handoff_state, 'read_sealed', forbidden)
    facts = authority.job_facts(root, data['job'], base=dict(goal_id=data['identity']['goal_id']),
        policy=dict(execution_authority_policy=data['policy']), sources={4: reviewer, 5: parent})
    assert facts['qualifications'] == [data['receipt']]
    assert reads == [data['policy']['contract'], data['receipt'], data['request_ref']]
    assert accepted == [root / 'native-evidence/004/result.json']


def test_fork_provenance_reduction_is_pure_and_keeps_original_document(monkeypatch):
    data = records()
    source = object()
    original = dict(step=6, source_restrictions=dict(restriction='clear'))
    document = dict(version=1, step=6, edges=[dict(kind='producer', step=2,
        seal=copy.deepcopy(data['subject']['producer_seal']))], scope=q.SCOPE)
    with closed_reads(monkeypatch):
        facts = q.fork_input_facts(source,original,document=document,
            producer_origins={2:data['identity']})
        assert facts['fork_source'] is source
        assert facts['fork_restriction'] == 'clear'
        assert facts['document']['edges'][0]['seal']['origin_identity'] == data['identity']
        assert 'origin_identity' not in document['edges'][0]['seal']


def test_new_job_reuses_parent_policy_facts_for_authority_consumers(tmp_path, monkeypatch):
    from test_execution_authority import prepare
    from test_history_handoff import finish
    root, _ = prepare(tmp_path)
    finish(root,1,'H',[],action='analyze')
    produce, bind, validate = authority.job_facts, authority.bind_job, authority.validate_job
    produced = []

    def produced_authority(given_root, job, *, base, policy, sources):
        assert given_root == root and sources[1]['job']['step'] == 1
        with monkeypatch.context() as guarded:
            guarded.setattr(c,'settings',forbidden)
            guarded.setattr(configuration_updates,'for_job',forbidden)
            guarded.setattr(originals,'node',forbidden)
            guarded.setattr(originals,'source_job',forbidden)
            facts = produce(given_root,job,base=base,policy=policy,sources=sources)
        produced.append(facts)
        return facts

    def bind_facts(given_root, job, *, facts):
        with closed_reads(monkeypatch):
            return bind(given_root,job,facts=facts)

    def validate_facts(given_root, job, *, facts):
        with closed_reads(monkeypatch):
            return validate(given_root,job,facts=facts)

    monkeypatch.setattr(authority,'job_facts',produced_authority)
    monkeypatch.setattr(authority,'bind_job',bind_facts)
    monkeypatch.setattr(authority,'validate_job',validate_facts)
    job = c.build_job(root,2,'A',[1])
    c._validate_inputs(root,c.workspace(root,2),job)
    assert len(produced) == 2
    assert job['execution_authority'] == produced[0]['contract']


def test_fork_build_validation_and_reentry_share_produced_authority_and_guidance(tmp_path, monkeypatch):
    from scidiscovery.research_loop import controller, fork_runtime, fork_service
    from test_execution_authority import prepare
    from test_history_handoff import finish
    root, _ = prepare(tmp_path)
    write = c.write

    def stopped_write(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value,native_started=False)
        return write(path,value)

    with monkeypatch.context() as stopped:
        stopped.setattr(c,'write',stopped_write)
        finish(root,1,'H',[],action='analyze')
        finish(root,2,'A',[1],action='finish')
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=1000,max_seconds=60,max_steps=2)
    advice = 'Keep this exact inherited scientific question.'
    preview = fork_service.preview(root,2,child,budget=limits,guidance_text=advice)
    fork_service.create(root,2,child,budget=limits,guidance_text=advice,
        expected_preview_id=preview['preview_id'],operation_id='authority-facts-fork')
    selected_base = fork_runtime._load_selected_base_facts
    bind, validate = authority.bind_job, authority.validate_job
    produced = []

    def selected_facts(given_root, selected, *, base, policy, head, bind_inputs=False):
        with monkeypatch.context() as guarded:
            for name in ('input_facts','bind_inputs','validate_inputs','status'):
                guarded.setattr(q,name,forbidden)
            guarded.setattr(c,'settings',forbidden)
            guarded.setattr(configuration_updates,'for_job',forbidden)
            guarded.setattr(configuration_updates,'for_new_job',forbidden)
            result = selected_base(given_root,selected,base=base,policy=policy,head=head,
                bind_inputs=bind_inputs)
        produced.append(result[1])
        return result

    def bind_facts(given_root, job, *, facts):
        with closed_reads(monkeypatch):
            return bind(given_root,job,facts=facts)

    def validate_facts(given_root, job, *, facts):
        with closed_reads(monkeypatch):
            return validate(given_root,job,facts=facts)

    monkeypatch.setattr(fork_runtime,'_load_selected_base_facts',selected_facts)
    monkeypatch.setattr(authority,'bind_job',bind_facts)
    monkeypatch.setattr(authority,'validate_job',validate_facts)
    job = fork_runtime.build_selected_job(child,2)
    assert [row['text'] for row in job['user_guidance']] == [advice]
    assert not (child / 'native-evidence/002/result-identity.json').exists()
    assert fork_runtime.build_selected_job(child,2) == job
    assert fork_runtime.validate_selected_job(child,c.workspace(child,2),job) is None
    c._validate_inputs(child,c.workspace(child,2),job)
    assert len(produced) == 4
    assert all(facts['authority']['contract'] == job['execution_authority'] for facts in produced)
