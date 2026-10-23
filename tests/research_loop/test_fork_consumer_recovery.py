"""Fork consumers bind exact originals; synthetic native identities grant no permission."""
import copy
import shutil

import pytest

from scidiscovery.research_loop import (
    contracts as c, controller, execution, fork_runtime, fork_service, native,
)
from test_breadth_science import a, findings, h, prepare
from test_history_handoff import finish
from test_composed_execution_results import composed_registry


def complete(root,job,payload):
    """Represent a stopped native transport; no native/model process is invoked."""
    step=job['step'];record=execution.origin(root,step)
    c.write(record/'native-intent.json',dict(task_ref=c.record_ref(root/'jobs'/f'{step:03d}.json')))
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    c.seal_result(root,step)
    return record


def test_new_fork_screen_can_seal_using_its_retained_prefix(tmp_path):
    root = prepare(tmp_path)
    h(root, labels=('P',))
    finish(root, 2, 'W', [1], mechanism_findings=findings(blocked=('P',)))
    a(root, 3, [1, 2], focus='h001.m1')
    finish(root, 4, 'W', [1, 2, 3], mechanism_findings=findings(blocked=('P',)))
    a(root, 5, [1, 4], action='finish')
    for step in range(1, 6):
        path = execution.origin(root, step) / 'native-outcome.json'
        c.write(path, dict(c.read(path), native_started=False))
    with controller._writer(root):
        pass
    child = tmp_path / 'child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    preview = fork_service.preview(root, 4, child, budget=limits)
    fork_service.create(root, 4, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='review-new-screen')

    # Run the actual selected-task constructor and original seal path. Only
    # native transport completion is represented by the existing thin fixture.
    job = fork_runtime.build_selected_job(child, 4)
    assert job['search']['focus']['action'] == 'screen'
    assert job['history']['nodes']
    assert not (child / 'native-evidence/001/result-identity.json').exists()
    record = execution.origin(child, 4)
    payload = dict(summary='Explicit scientific obstacle',
        scientific_result='The original missing observation remains missing.',
        limitations=['No model or experiment was executed by this fixture.'],
        implementation_selection=None, stage_intent='review',
        mechanism_findings=findings(blocked=('P',)),
        candidate_nominations=dict(representatives=[], extras=[], gaps=[
            dict(mechanism_ref='h001.m1', reason='No usable model exists.')]))
    c.write(record / 'native-intent.json',
        dict(task_ref=c.record_ref(child / 'jobs/004.json')))
    c.write(record / 'science-output.json', payload)
    c.write(record / 'usage.json', dict(input_tokens=7, cached_input_tokens=2, output_tokens=3))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(child), raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(child, 4)
    assert (record / 'result-identity.json').is_file()
    c.validate(c.workspace(child,4))


@pytest.fixture
def quiescent_compiler_chain(tmp_path, monkeypatch, request):
    """Keep synthetic no-process declarations inside the original seal binding."""
    from test_native_execution_inputs import chain
    write = c.write

    def synthetic_write(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)

    monkeypatch.setattr(c, 'write', synthetic_write)
    return chain.__wrapped__(tmp_path, monkeypatch, request)


@pytest.mark.parametrize('selected_step', [3, 4])
def test_fork_formal_reviewer_can_bind_inherited_compiler_originals(
        tmp_path, quiescent_compiler_chain, selected_step):
    from scidiscovery.research_loop import backends, experiments as e, formal_review as review

    root, backend, _, config, _, author, aliases = quiescent_compiler_chain
    assert backends.selected(c.settings(root)).complete_authorship
    public = c.workspace(root, 2) / 'artifacts/implementation.json'
    compiled = e.compile_implementation(config, e.load(config), author, c.read(aliases), public)
    assert compiled['status'] == 'compiled'
    assert not backend.prepared and backend.submits == 0
    finish(root, 2, 'W', [1])
    requester, _, request_record = finish(root, 3, 'A', [2],
        action='formal_review', selection=['W002-I001'])
    role, parents, instruction, back = review.request_route(root, requester,
        c.read(request_record / 'result.json'), [3])
    assert back['kind'] == 'formal_review'  # Real eligibility, no unavailable shortcut.
    reviewer = c.build_job(root, 4, role, parents, instruction, back)
    assert reviewer['purpose'] == 'formal_review'
    record = execution.origin(root, 4)
    c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(root / 'jobs/004.json')))
    c.write(record / 'science-output.json', dict(verdict='pass', summary='Fixture review',
        scientific_result='Exact compiler originals are retained.', limitations=['Synthetic identities only.']))
    c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
        usage=native.usage(root), raw_ref=c.file_ref(record / 'science-output.json')))
    c.seal_result(root, 4)
    _, parents, instruction, back = review.return_route(root, reviewer, [2, 3, 4])
    c.build_job(root, 5, 'A', parents, instruction, back)
    finish(root, 5, 'A', parents, action='finish')
    with controller._writer(root):
        pass
    child = tmp_path / 'review-child'
    limits = dict(max_tokens=1000, max_seconds=30, max_steps=3)
    preview = fork_service.preview(root, selected_step, child, budget=limits)
    fork_service.create(root, selected_step, child, budget=limits,
        expected_preview_id=preview['preview_id'], operation_id='review-inherited-compiler')

    # The inherited original compiler is valid; only the consumer lacks context.
    from scidiscovery.research_loop import execution_inputs, originals
    producer = originals.node(child, 2)
    seal = c.read(originals.source_record(producer, 'result-identity.json'))
    row = seal['compiled_implementations'][0]
    execution_inputs.verify_original_compilation(producer, row['compiler'],
        material=row['material'], client_binding=row['client_binding'])
    assert not (child / 'jobs/002.json').exists()
    shutil.rmtree(root)
    selected = fork_runtime.build_selected_job(child, selected_step)
    if selected_step == 3:
        # A new request must construct neutral inputs, rather than reusing the
        # historical reviewer's already frozen scientific-input copies.
        record = execution.origin(child, 3)
        c.write(record / 'native-intent.json', dict(task_ref=c.record_ref(child / 'jobs/003.json')))
        c.write(record / 'science-output.json', dict(summary='Request exact review',
            scientific_result='Assess the inherited exact compiled implementation.',
            limitations=['Synthetic native identities only.'], implementation_selection=['W002-I001'],
            candidate_recommendation=None, selection_explanation=None, next_action='formal_review',
            route_reason='Assess the exact retained scientific subject.'))
        c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
        c.write(record / 'native-outcome.json', dict(returncode=0, stop_reason=None,
            usage=native.usage(child), raw_ref=c.file_ref(record / 'science-output.json')))
        c.seal_result(child, 3)
        role, parents, instruction, back = review.request_route(child, selected,
            c.validate(c.workspace(child, 3)), [1, 2, 3])
        assert back['kind'] == 'formal_review'
        selected = c.build_job(child, 4, role, parents, instruction, back)
    assert selected['purpose'] == 'formal_review'
    task = c.read(c.workspace(child, 4) / 'science-task.json')
    assert task['review']['scientific_implementation']
    record=complete(child,selected,dict(verdict='pass',summary='Synthetic independent review',
        scientific_result='Assess exact retained compiler inputs.',limitations=['Synthetic identities; no solver qualification.']))
    c.validate(c.workspace(child,4))
    seal=c.read(record/'result-identity.json')
    proof=c.read(child/seal['formal_review']['path'])
    assert proof['status']=='passed' and proof['independence']=='independent'
    assert proof['subject']['producer_seal']['origin_identity']==producer.origin_identity
    binding=review.input_binding(child,selected['formal_review'])
    scientific=binding['compiler_scientific_materials'][0]
    assert (c.workspace(child,4)/scientific['path']).stat().st_size==scientific['size_bytes']
    assert not backend.prepared and backend.submits==0

    compiler=execution_inputs.verify_original_compilation(producer,row['compiler'],
        material=row['material'],client_binding=row['client_binding'])
    owner=originals.source_job(producer)
    wrong=copy.deepcopy(compiler['inputs'][0]['original']);wrong['instance_id']='unbound-instance'
    with pytest.raises(ValueError,match='exact producer/material'):
        execution_inputs.resolve_native_original(child,wrong,compiler_job=owner,source=producer)
    author_ref=compiler['author']
    retained=originals.resolve_ref(producer,author_ref)
    retained.chmod(0o600);retained.write_bytes(b'changed compiler author original')
    with pytest.raises(ValueError,match='Original size changed'):
        review.input_binding(child,selected['formal_review'])


@pytest.mark.process_e2e
def test_new_fork_screen_seals_recovered_original_and_nested_fork_keeps_origin(tmp_path,composed_registry,monkeypatch):
    """Only the composed fixture's local execution CLI uses subprocesses."""
    from scidiscovery.research_loop import execution_recovery,experiments,history,managed_wait,originals,search,worker_delivery
    from test_composed_execution_results import case
    write=c.write
    def synthetic_write(path,value):
        if path.name=='native-outcome.json':value=dict(value,native_started=False)
        return write(path,value)
    # Every thin native completion, including the child's final A, declares
    # non-submission before its original seal. Managed CLI effects stay real.
    monkeypatch.setattr(c,'write',synthetic_write)
    root,_,config,cli,call=case(tmp_path,dict(collect=False))
    blocked=findings(blocked=('P','Q'))
    with monkeypatch.context() as patch:
        # Retain a historical pre-wait unknown original, as in recovery fixtures.
        patch.setattr(managed_wait,'before_seal',lambda *args:None)
        finish(root,2,'W',[1],mechanism_findings=blocked,preserve_native_intent=True)
    original_seal=c.read(execution.origin(root,2)/'result-identity.json')
    old_observation=(root/original_seal['observations']['path']).read_bytes()
    finish(root,3,'A',[1,2],action='continue_w',search_choice=dict(dispositions=[],
        next_focus=dict(mechanism_ref='h001.m1',action='screen')))
    experiments.load(config).complete(call)
    assert cli('reconcile','--call-id',call)['status']=='known_success'
    assert controller.execution_recover(root,2)['status']=='execution_recovered'
    finish(root,4,'W',[1,2,3],mechanism_findings=findings('P',blocked=('Q',)))
    a(root,5,[1,4],action='finish')
    with controller._writer(root):pass
    child=tmp_path/'child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=3)
    preview=fork_service.preview(root,4,child,budget=limits)
    fork_service.create(root,4,child,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='screen-recovered-prefix')
    shutil.rmtree(root)
    job=fork_runtime.build_selected_job(child,4)
    assert all('origin_identity' not in ref for ref in job['history']['execution_recoveries'])
    bound_recoveries=originals.recovery_refs(originals.node(child,4),job)
    assert all(ref['origin_identity']==job['fork_source']['job']['origin_identity'] for ref in bound_recoveries)
    assert execution_recovery.prefix(child,4)==bound_recoveries
    assert originals.frozen_recovery_refs(child,5)==bound_recoveries
    payload=dict(summary='Assess one retained observation',scientific_result='No experiment was resubmitted.',
        limitations=['Synthetic execution facts only.'],implementation_selection=None,stage_intent='review',
        mechanism_findings=findings('P',blocked=('Q',)),candidate_nominations=dict(representatives=[],extras=[],gaps=[
            dict(mechanism_ref=row['mechanism_ref'],reason='No scientific candidate claimed.')
            for row in job['candidate_nomination_mechanisms'] if row['status']!='waived']))
    record=complete(child,job,payload)
    c.validate(c.workspace(child,4))
    facts=worker_delivery.validate(child,job,record)
    producer=originals.node(child,2)
    assert facts['evidence'][0]['accepted']['origin_identity']==producer.origin_identity
    assert originals.resolve_ref(producer,original_seal['observations']).read_bytes()==old_observation
    assert search._screen_refs(child,job,c.read(record/'result-identity.json'),search.project(child,5))=={'h001.m1'}
    missing=copy.deepcopy(job);missing['history']['execution_recoveries']=[]
    with pytest.raises(ValueError,match='Frozen history differs from its exact original task'):
        worker_delivery.validate(child,missing,record)
    changed=copy.deepcopy(facts)
    changed['evidence'][0]['accepted']['origin_identity']=originals.local_identity(child)
    c.write(record/worker_delivery.FACT_NAME,changed)
    with pytest.raises((ValueError,OSError)):worker_delivery.validate(child,job,record)
    c.write(record/worker_delivery.FACT_NAME,facts)
    a(child,5,[1,4],action='finish')
    with controller._writer(child):pass
    nested=tmp_path/'nested';preview=fork_service.preview(child,5,nested,budget=limits)
    fork_service.create(child,5,nested,budget=limits,expected_preview_id=preview['preview_id'],
        operation_id='nested-screen-original')
    shutil.rmtree(child)
    retained,_,seal,_=history._source(nested,4)
    assert originals.recovery_refs(originals.node(nested,4),retained)==bound_recoveries
    assert originals._manifest(nested)['cut']['execution_recoveries']==bound_recoveries
    assert search._screen_refs(nested,retained,seal,search.project(nested,5))=={'h001.m1'}
