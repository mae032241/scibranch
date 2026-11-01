"""One producer owns direct source reads; no model or solver execution."""
from collections import Counter
from contextvars import Context
from copy import deepcopy
from pathlib import Path

import pytest

from scidiscovery.research_loop import (
    accepted_budget, accepted_prefix, candidate_nominations, candidate_refs, contracts as c,
    handoff_state, originals, qualification as q,
)
from test_history_handoff import scene, finish


def _physical_reads(monkeypatch, paths):
    observed = Counter()
    read_text, read_bytes = Path.read_text, Path.read_bytes

    def text(path, *args, **kwargs):
        if path in paths:
            observed[path] += 1
        return read_text(path, *args, **kwargs)

    def bytes_(path, *args, **kwargs):
        if path in paths:
            observed[path] += 1
        return read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', text)
    monkeypatch.setattr(Path, 'read_bytes', bytes_)
    return observed


def _inputs(root, job):
    base=c.settings(root)
    return q.input_facts(root,job,base=base,head=accepted_budget.load(root,base=base),sources={})


@pytest.mark.parametrize('first', [False, True])
def test_original_and_control_source_share_one_physical_parse(scene, monkeypatch, first):
    finish(scene, 1, 'H', [], action='wait')
    paths = {scene / 'jobs/001.json', scene / 'native-evidence/001/result-identity.json',
        scene / 'native-evidence/001/science-output.json'}
    observed = _physical_reads(monkeypatch, paths)

    def consume():
        with handoff_state.scope(scene):
            one = handoff_state.source(scene, 1, resolve_choices=first)
            two = handoff_state.source(scene, 1, resolve_choices=not first)
            facts = handoff_state.source_facts(scene, 1)
            assert len(one) == len(two) == 4
            assert handoff_state.source_from_facts(facts, resolve_choices=False)[1] is facts['payload']
            assert handoff_state.source_from_facts(facts)[2] is facts['seal']

    Context().run(consume)
    assert observed == Counter({path: 1 for path in paths})


def test_build_and_next_input_owner_each_read_their_direct_original_once(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    paths = {scene / 'jobs/001.json', scene / 'native-evidence/001/result-identity.json',
        scene / 'native-evidence/001/science-output.json'}
    original = (scene / 'native-evidence/001/science-output.json').read_bytes()
    observed = _physical_reads(monkeypatch, paths)

    def build():
        with handoff_state.scope(scene):
            return c.build_job(scene, 2, 'E', [1])

    job = Context().run(build)
    assert observed == Counter({path: 1 for path in paths})
    observed.clear()

    def validate():
        with handoff_state.scope(scene):
            return c._validate_inputs(scene, c.workspace(scene, 2), job)

    document = Context().run(validate)
    assert document['step'] == 2
    assert observed == Counter({path: 1 for path in paths})
    assert (scene / 'native-evidence/001/science-output.json').read_bytes() == original


def test_assessment_completes_an_existing_source_once_without_a_handoff_scope(scene, monkeypatch):
    finish(scene,1,'H',[],action='wait')
    worker,_,_=finish(scene,2,'W',[1],action='wait')
    assert worker.get('decision_context') is None
    paths={scene/'jobs/001.json',scene/'native-evidence/001/result-identity.json',
        scene/'native-evidence/001/science-output.json'}
    observed=_physical_reads(monkeypatch,paths)

    def build():
        assert handoff_state.current(scene) is None
        return c.build_job(scene,3,'A',[2])

    job=Context().run(build)
    assert job['assessment_handoff']['current_w']=='inputs/parents/002/conclusion.json'
    assert observed==Counter({path:1 for path in paths})


@pytest.mark.parametrize('terminal',[False,True],ids=['never-submitted','unsealed-terminal'])
def test_real_fork_selected_inputs_do_not_require_a_selected_result(tmp_path,monkeypatch,terminal):
    from scidiscovery.research_loop import controller,fork_runtime,fork_service
    from test_execution_authority import prepare
    root,_=prepare(tmp_path,enabled=False)
    write=c.write

    def stopped_write(path,value):
        if path.name=='native-outcome.json':
            value=dict(value,native_started=False)
        return write(path,value)

    with monkeypatch.context() as patch:
        patch.setattr(c,'write',stopped_write)
        finish(root,1,'H',[],action='wait')
        if terminal:
            patch.setattr(c,'seal_result',lambda *args,**kwargs:None)
            selected,_,_=finish(root,2,'A',[1],action='wait')
        else:
            selected=c.build_job(root,2,'A',[1])
    assert not (root/'native-evidence/002/result-identity.json').exists()
    assert (root/'native-evidence/002/native-intent.json').exists() is terminal
    with controller._writer(root):
        pass
    child=tmp_path/'selected-child'
    limits=dict(max_tokens=1000,max_seconds=30,max_steps=1)
    preview=fork_service.preview(root,2,child,budget=limits)
    assert preview['status']=='fork_preview_ready',preview
    receipt=fork_service.create(root,2,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='selected-task-only')
    assert receipt['state']=='paused'
    job=fork_runtime.build_selected_job(child,2)
    assert job['step']==selected['step'] and job['fork_source']
    assert q.validate_inputs(child,job)['step']==selected['step']
    assert q.status(child,job)==job['source_restrictions']
    for name in ('native-intent.json','native-outcome.json','result-identity.json'):
        assert not (child/'native-evidence/002'/name).exists()


def test_compared_provenance_document_feeds_status_without_a_second_read(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'E', [1])
    facts = _inputs(scene, job)
    path = scene / job['source_provenance']['path']
    observed = _physical_reads(monkeypatch, {path})
    document = q.validate_inputs_from_facts(scene, job, facts=facts)
    assert observed[path] == 1

    def forbidden(*args, **kwargs):
        pytest.fail('A restriction consumer reopened or reconstructed its accepted inputs')

    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(c, 'read_scientific_output', forbidden)
    monkeypatch.setattr(c, 'settings', forbidden)
    monkeypatch.setattr(q, '_read', forbidden)
    monkeypatch.setattr(q, '_inputs', forbidden)
    monkeypatch.setattr(q, 'validate_inputs', forbidden)
    monkeypatch.setattr(q, 'original_status', forbidden)
    for _ in range(3):
        assert q.status_from_facts(job, document, facts=facts) == job['source_restrictions']
    assert observed[path] == 1


def test_source_projection_consumers_do_not_load_or_accept(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'E', [1])
    facts = handoff_state.source_facts(scene, 1)
    ref = job['direct_refs'][0]
    member = accepted_prefix.member(scene, accepted_prefix.for_job(job), 1)
    _, public = c._parent_public(job, ref, source=facts)
    source = dict(facts=facts, public=public, member=member)
    material = dict(path='inputs/parents/001/artifacts/data.txt', source='/retained/data.txt',
        size_bytes=3, file_stat=[1, 2, 3, 4])
    prior = dict(previous_a=dict(step=1, original='inputs/context/001/scientific-original.json'))
    a_source=dict(facts,job=dict(facts['job'],role='A'),
        payload=dict(facts['payload'],next_action='continue_w',route_reason='exact route'))
    worker=dict(step=2,role='W',instruction='exact route',direct_refs=[ref],
        decision_context=dict(source_original=ref['path'],source_action='continue_w'))
    w_ref=dict(path='inputs/parents/002/conclusion.json',size_bytes=1)
    w_seal_ref=dict(path=str(scene/'native-evidence/002/result-identity.json'))
    w_source=dict(job=worker,payload={},seal_ref=w_seal_ref)
    assessment=dict(step=3,role='A',direct_refs=[w_ref],direct_materials=[])
    members={1:member,2:dict(seal_ref=w_seal_ref)}

    def forbidden(*args, **kwargs):
        pytest.fail('A source projection consumer attempted to load or accept again')

    for name in ('read', 'read_scientific_output', 'settings', '_execution_raw'):
        monkeypatch.setattr(c, name, forbidden)
    for name in ('source', 'source_facts', 'source_facts_from_sealed', 'sealed_facts'):
        monkeypatch.setattr(handoff_state, name, forbidden)
    for name in ('source_job', 'source_record', 'resolve_ref', 'material_path'):
        monkeypatch.setattr(originals, name, forbidden)
    for _ in range(3):
        assert c._parent_view(job, ref, source=source)['role'] == 'H'
        assert c._decision_context(job, sources={1: facts}) is None
        assert c._assessment_handoff(job, sources={1: facts}, members={1: member}, policy={}) is None
        assert c._assessment_original(prior, sources={1: facts})['source'] == str(facts['raw_path'])
        assert c._public_materials([material]) == [{key: value for key, value in material.items() if key != 'file_stat'}]
        assert c._decision_context(worker,sources={1:a_source})['source_action']=='continue_w'
        handoff=c._assessment_handoff(assessment,sources={1:a_source,2:w_source},members=members,policy={})
        assert handoff['previous_a']['step']==1 and handoff['current_w']==w_ref['path']


def test_extra_loaded_context_does_not_upgrade_unknown_material_provenance(scene):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'E', [1])
    facts = _inputs(scene, job)
    material = dict(path='inputs/parents/009/artifacts/unbound.txt', source='/retained/unbound.txt', size_bytes=3)
    changed = dict(job, direct_materials=[material])
    document = q._inputs(scene, changed, facts=facts)
    assert [edge['step'] for edge in document['edges'] if edge['kind'] == 'producer'] == [1]
    assert dict(kind='unknown', reason='missing_or_ambiguous_material_origin', material=material) in document['edges']
    assert q.status_from_facts(changed, document, facts=facts)['restriction'] == 'unknown'


def test_saved_document_tampering_is_rejected_before_reduce(scene):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'E', [1])
    facts = _inputs(scene, job)
    path = scene / job['source_provenance']['path']
    changed = deepcopy(facts['document'])
    # Keep the byte size unchanged to reach the document comparison itself.
    changed['step'] = job['step'] + 1
    path.chmod(0o600)
    c.write(path, changed)
    with pytest.raises(q.IntegrityError, match='Frozen direct source binding changed'):
        q.validate_inputs_from_facts(scene, job, facts=facts)


def test_input_owner_rejects_a_same_step_old_or_unaccepted_attempt(scene):
    finish(scene,1,'H',[],action='wait')
    job=c.build_job(scene,2,'E',[1])
    seal_path=scene/'native-evidence/001/result-identity.json'
    seal=c.read(seal_path)
    decoy=scene/'native-evidence/001/continuations/001/science-output.json'
    decoy.parent.mkdir(parents=True)
    decoy.write_bytes((scene/'native-evidence/001/science-output.json').read_bytes())
    decoy.chmod(0o400)
    seal_path.chmod(0o600)
    c.write(seal_path,dict(seal,raw_ref=c.file_ref(decoy)))

    def validate():
        with handoff_state.scope(scene):
            c._validate_inputs(scene,c.workspace(scene,2),job)

    with pytest.raises(q.IntegrityError,match='Direct source does not name its exact producer original'):
        Context().run(validate)


def test_source_cache_is_bound_to_origin_and_retained_attempt(tmp_path):
    root = tmp_path / 'consumer'
    root.mkdir()
    owners = []
    for name in ('one', 'two'):
        original = tmp_path / name
        identity = dict(goal_id='same-label', settings_ref=dict(path=str(original / 'settings.json')))
        owner = originals.NodeSource(identity, 1, str(original), original)
        owners.append(owner)
        c.write(original / 'jobs/001.json', dict(step=1, role='H'))
        raw = original / 'native-evidence/001/science-output.json'
        c.write(raw, dict(summary=name))
        c.write(original / 'native-evidence/001/result-identity.json',
            dict(job_ref=dict(path=str(original / 'jobs/001.json')), raw_ref=c.file_ref(raw)))
    with handoff_state.scope(root):
        first = handoff_state.source_facts(root, 1, owner=owners[0], resolve_choices=False)
        second = handoff_state.source_facts(root, 1, owner=owners[1], resolve_choices=False)
        assert first is not second
        assert first['payload']['summary'] == 'one' and second['payload']['summary'] == 'two'
        wrong = originals.NodeSource(owners[0].origin_identity, 1, owners[0].original_root,
            owners[0].storage_root, native_record='native-evidence/001/continuations/001')
        with pytest.raises(ValueError, match='retained native attempt'):
            handoff_state.sealed_facts(root, 1, owner=wrong)


def test_new_job_binds_new_configuration_after_parent_facts_were_loaded(scene):
    from test_configuration_updates import edit
    parent, _, _ = finish(scene, 1, 'H', [], action='wait')
    with handoff_state.scope(scene):
        facts = handoff_state.source_facts(scene, 1)
        frozen_parent = deepcopy(facts['job'])
        edit(scene, model='fixture-next-node')
        job = c.build_job(scene, 2, 'E', [1])
        assert job.get('configuration_revision') != parent.get('configuration_revision')
        assert c.settings(scene, job)['model'] == 'fixture-next-node'
        assert facts['job'] == frozen_parent
        assert facts['job'].get('configuration_revision') == parent.get('configuration_revision')


def test_nomination_dict_consumes_supplied_sealed_source_without_loading(monkeypatch):
    job = dict(step=1, role='W', worker_view_version=1, history={})
    declaration = dict(representatives=[dict(mechanism_ref='h001.m1',reason='',
        candidate=dict(call_id='missing-call', artifact='result'))], extras=[],gaps=[])
    facts = dict(job=job, owner=None, observation=None)

    def forbidden(*args, **kwargs):
        pytest.fail('A sealed nomination consumer reopened its source')

    monkeypatch.setattr(c, 'read', forbidden)
    monkeypatch.setattr(originals, 'source_job', forbidden)
    monkeypatch.setattr(originals, 'source_record', forbidden)
    monkeypatch.setattr(originals, 'resolve_ref', forbidden)
    assert candidate_nominations.resolve(Path('/consumer'), job, declaration, source_facts=facts) == declaration


def test_inherited_projection_reuses_owner_mapping_without_renaming_science(monkeypatch):
    owner = originals.NodeSource(dict(goal_id='origin', settings_ref=dict(path='/origin/settings.json')),
        2, '/origin', Path('/retained'),entries=())
    job = dict(step=2,role='H',history=dict(candidate_ids=['raw'], candidate_references={'raw': 'W001-C001'}))
    mapping = {'raw': 'source:origin:raw'}

    def forbidden(*args, **kwargs):
        pytest.fail('An inherited view rebuilt its input mapping')

    monkeypatch.setattr(candidate_refs, 'source_input_mapping', forbidden)
    projected = candidate_refs.source_job_view(owner, job, input_mapping=mapping)
    assert projected['history']['candidate_ids'] == ['source:origin:raw']
    assert job['history']['candidate_ids'] == ['raw']
    observation=dict(candidates=[dict(id='own')],current_candidate=dict(candidate_id='raw'))
    projected_observation=candidate_refs.source_observation(owner,job,observation,input_mapping=mapping)
    assert projected_observation['current_candidate']['candidate_id']=='source:origin:raw'
    assert observation['current_candidate']['candidate_id']=='raw'
    authored = dict(scientific_result='raw remains authored prose', metrics={'raw': 1}, candidate_recommendation='raw')
    assert candidate_refs.public_science_fields(authored, mapping) == dict(
        scientific_result=authored['scientific_result'], metrics={'raw': 1}, candidate_recommendation='source:origin:raw')
    seal=dict(observations=dict(path='retained-observation.json'))
    facts=dict(owner=owner,job=job,seal=seal,seal_ref={},payload=authored,observation=observation)
    calls=[]

    def produce_mapping(source,current):
        assert source is owner and current is job
        calls.append(source)
        return mapping

    monkeypatch.setattr(handoff_state,'sealed_facts',lambda *args,**kwargs:facts)
    monkeypatch.setattr(candidate_refs,'source_input_mapping',produce_mapping)
    monkeypatch.setattr(c,'read_scientific_output',forbidden)
    with handoff_state.scope(Path('/consumer')):
        handoff_state.observation(Path('/consumer'),job,seal)
        monkeypatch.setattr(candidate_refs,'source_input_mapping',forbidden)
        assert handoff_state.source_facts_from_sealed(Path('/consumer'),facts) is facts
    assert calls==[owner] and facts['input_mapping'] is mapping
    assert facts['control'][1]['candidate_recommendation']=='source:origin:raw'
    assert facts['payload']['candidate_recommendation']=='raw'
