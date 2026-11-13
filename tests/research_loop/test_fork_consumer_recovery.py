"""Fork consumers bind exact originals; synthetic native identities grant no permission."""
import copy
from pathlib import Path
import shutil

import pytest

from execution_fixtures import execution_registry

from scidiscovery.research_loop import (
    accepted_budget, contracts as c, controller, execution, fork_runtime, fork_service, native,
)
from test_breadth_science import a, findings, h, prepare
from test_history_handoff import finish
from test_composed_execution_results import composed_registry
from execution_fixtures import execution_registry
from test_mutable_run_inputs import fixed_code,terminal_code


@pytest.fixture
def legacy_recovery_sources():
    """In-memory inherited handles; no original or native-home files exist."""
    from scidiscovery.research_loop import originals
    origins={}
    for name in ('parent','child'):
        root=Path('/recovery-unit')/name
        identity=dict(goal_id=name,settings_ref=dict(path=str(root/'settings.json')))
        origins[name]=dict(identity=identity,original_root=str(root))
    snapshot=originals._snapshot(dict(entries=[],origins=origins,native_records={}))
    return tuple(originals._snapshot_source(Path('/recovery-unit/nested/host-runtime/fork'),
        snapshot,origins[name]['identity'],4) for name in ('parent','child'))


@pytest.mark.parametrize('inherited',[False,True])
@pytest.mark.parametrize('refs',[None,[]],ids=['absent','empty'])
def test_empty_legacy_recoveries_do_not_walk_modern_history(
        monkeypatch,legacy_recovery_sources,inherited,refs):
    from scidiscovery.research_loop import originals
    parent,source=legacy_recovery_sources
    if not inherited:
        source=originals.NodeSource(source.origin_identity,4,source.original_root,Path(source.original_root))
    history=dict(prefix=dict(count=3,owner='child'),breadth_snapshot=dict(path='accepted-search.json'))
    if refs is not None:history['execution_recoveries']=refs
    job=dict(step=4,history=history,fork_source=dict(job=dict(
        origin_identity=parent.origin_identity,path='jobs/004.json')))
    before=copy.deepcopy(job)
    def unexpected(*args,**kwargs):
        pytest.fail('Empty legacy recoveries must not reopen historical ownership')
    monkeypatch.setattr(originals,'history_source',unexpected)
    monkeypatch.setattr(originals,'resolve_ref',unexpected)
    monkeypatch.setattr(originals,'_json',unexpected)
    assert originals.recovery_refs(source,job)==[]
    assert job==before


@pytest.mark.parametrize('changed_history',[False,True],ids=['exact','foreign-history'])
def test_nonempty_legacy_recoveries_keep_strict_inherited_origin(
        monkeypatch,legacy_recovery_sources,changed_history):
    from scidiscovery.research_loop import originals
    parent,source=legacy_recovery_sources
    refs=[dict(path='host-control/execution-recoveries/legacy.json'),
        dict(path='host-control/execution-recoveries/explicit.json',origin_identity=source.origin_identity)]
    history=dict(prefix=dict(count=3,owner='parent'),execution_recoveries=refs)
    original=dict(step=4,history=copy.deepcopy(history))
    job=dict(step=4,history=copy.deepcopy(history),fork_source=dict(job=dict(
        origin_identity=parent.origin_identity,path='jobs/004.json')))
    if changed_history:job['history']['prefix']['owner']='child'
    before=copy.deepcopy(job);reads=[];retained=Path('/recovery-unit/retained-parent-job.json')
    def resolve(owner,ref):
        assert owner.inherited and owner.origin_identity==parent.origin_identity
        assert owner.original_root==parent.original_root
        assert ref==job['fork_source']['job']
        reads.append((owner,ref))
        return retained
    def load(path):
        assert path==retained
        return copy.deepcopy(original)
    monkeypatch.setattr(originals,'resolve_ref',resolve)
    monkeypatch.setattr(originals,'_json',load)
    if changed_history:
        with pytest.raises(ValueError,match='Frozen history differs from its exact original task'):
            originals.recovery_refs(source,job)
    else:
        result=originals.recovery_refs(source,job)
        assert result==[dict(refs[0],origin_identity=parent.origin_identity),refs[1]]
        assert result[1]['origin_identity']==source.origin_identity
    assert len(reads)==1
    assert job==before


def accept_fixture_native(root,job,record):
    """Bind the frozen profile before accepting this exact fixture attempt."""
    from scidiscovery.research_loop import profiles
    intent=dict(task_ref=c.record_ref(root/'jobs'/f'{job["step"]:03d}.json'),
        **profiles.binding(c.settings(root),job))
    c.write(record/'native-intent.json',intent)
    # The compiler fixture enriches original intents at their write boundary.
    intent=c.read(record/'native-intent.json')
    head=accepted_budget.accept_native(root,job['step'],record,intent)
    assert head['current_native']['record']==record.relative_to(root).as_posix()
    assert head['current_native']['intent']==intent


def complete(root,job,payload,*,with_inventory=False):
    """Represent a stopped native transport; no native/model process is invoked."""
    step=job['step'];record=execution.origin(root,step)
    accept_fixture_native(root,job,record)
    c.write(record/'science-output.json',payload)
    c.write(record/'usage.json',dict(input_tokens=1,cached_input_tokens=0,output_tokens=1))
    c.write(record/'native-outcome.json',dict(returncode=0,stop_reason=None,native_started=False,
        usage=native.usage(root),raw_ref=c.file_ref(record/'science-output.json')))
    if with_inventory:
        inventory=c.accept_current_inventory(root,step)
        c.seal_result_from_inventory(root,step,inventory=inventory)
        return record,inventory
    c.seal_result(root,step)
    return record


@pytest.mark.parametrize('payload_field',['result','public_result'])
@pytest.mark.parametrize('record_name',['completed.json','query.json'])
def test_opaque_scientific_fields_do_not_remove_typed_control_copies(tmp_path,payload_field,record_name):
    from scidiscovery.research_loop import fork_sources,originals
    from test_fork_source import completed_source
    root=completed_source(tmp_path);source=originals.node(root,2)
    paths=['settings.json','jobs/002.json','native-evidence/002/native-intent.json',
        'native-evidence/002/native-outcome.json']
    refs=[dict(path=path,size_bytes=(root/path).stat().st_size) for path in paths]
    record=root/'host-control/opaque'/record_name
    c.write(record,{payload_field:dict(files=refs,source_ref=refs[0])})
    closure=fork_sources._Closure()
    for path in paths:closure.add(source,path)
    closure.control(source,dict(path=record.relative_to(root).as_posix()))
    for path in [*paths,record.relative_to(root).as_posix()]:
        entry=closure.entry(source.origin_identity,path)
        assert entry['visibility']=='host_proof'
        assert closure.paths[f'{originals.identity_key(source.origin_identity)}:{path}']==str(root/path)


@pytest.mark.parametrize('name', ['result.pending.json', 'science-output.json'])
def test_unsealed_continuation_snapshot_keeps_private_draft_and_clean_child(tmp_path, name):
    """Real repair-policy/record/snapshot readers; no native process is launched."""
    from scidiscovery.research_loop import fork_sources, originals
    public = tmp_path / 'public'; public.mkdir()
    goal = public / 'goal.md'; goal.write_text('Retain an unfinished scientific handoff.')
    root = tmp_path / 'instance'
    launcher = tmp_path / 'never-launch'
    launcher.write_bytes(b'#!/bin/sh\nexit 99\n'); launcher.chmod(0o700)
    controller.prepare(root, goal, public, [str(launcher)], max_tokens=10000,
        max_seconds=100, max_steps=8, model='no-model', reasoning_effort='high',
        search_policy=None,
        node_budget_policy={role: dict(max_observed_tokens=1000, max_seconds=30) for role in 'HWAE'},
        resume_policy=dict(max_attempts=1, protocol='codex_exec_resume', usage_scope='turn', output_repair=True))
    c.build_job(root, 1, 'W', [])
    first = execution.origin(root, 1)
    task = c.record_ref(root / 'jobs/001.json')
    accepted_budget.accept_native(root, 1, first, dict(task_ref=task))
    c.write(first / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    stopped = dict(returncode=1, stop_reason='fixture_stopped', native_started=False,
        cleanup='observed_group_cleaned')
    c.write(first / 'native-outcome.json', stopped)
    accepted_budget.accept_terminal(root, 1, first,
        meter=c.read(first / 'usage.json'), unknown=False)
    record = first / 'continuations/001'
    accepted_budget.accept_native(root, 1, record, dict(task_ref=task, attempt=1, continuation_kind='repair',
        root_intent_ref=c.record_ref(first / 'native-intent.json'), previous=execution.proof(first)))
    c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    c.write(record / 'native-outcome.json', stopped)
    accepted_budget.accept_terminal(root, 1, record,
        meter=c.read(record / 'usage.json'), unknown=False)
    pending = record / name; pending.write_bytes(b'{"summary":"unfinished B"}\n'); pending.chmod(0o640)
    with controller._writer(root):
        pass
    assert execution.record(root, 1) == record
    plan = fork_sources.inspect_source(root, 1)
    assert plan['blocked_reasons'] == []
    entry = next(row for row in plan['entries'] if row['path'] == pending.relative_to(root).as_posix())
    assert entry['visibility'] == 'private_snapshot' and entry['mode'] == 0o640
    child = tmp_path / 'child'; limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    preview = fork_service.preview(root, 1, child, budget=limits)
    fork_service.create(root, 1, child, budget=limits, expected_preview_id=preview['preview_id'],
        operation_id='unsealed-continuation-snapshot')
    fork_runtime.build_selected_job(child, 1)
    retained = originals.resolve_ref(originals.selected_source(child), entry)
    assert retained.read_bytes() == pending.read_bytes() and retained.stat().st_mode & 0o777 == 0o640
    assert not (execution.origin(child, 1) / 'continuations').exists()
    assert list(execution.accepted_intents(child, 1)) == []
    assert execution.record(child, 1) == execution.origin(child, 1)
    accepted_budget.accept_native(child, 1, execution.origin(child, 1),
        dict(task_ref=c.record_ref(child / 'jobs/001.json')))
    assert [path for path, _ in execution.accepted_intents(child, 1)] == [execution.origin(child, 1)]
    assert execution.record(child, 1) == execution.origin(child, 1)


def test_inherited_git_observer_uses_copied_controls_and_keeps_science_stat_checks(
        tmp_path,monkeypatch,execution_registry,fixed_code):
    """Real observation/control readers; this source fixture mocks Git and all launches."""
    from scidiscovery import archive_io
    from scidiscovery.research_loop import experiments as e,fork_sources,history,originals
    from test_direct_public_collect import replace_equal_bytes
    public=tmp_path/'public';public.mkdir();goal=public/'goal.md';goal.write_text('Exact retained Git controls.')
    root=tmp_path/'instance'
    # This fixed-capture reader fixture tests neither W declarations nor scheduling defaults.
    controller.prepare(root,goal,public,['never-launched'],max_tokens=10000,
        max_seconds=120,max_steps=8,model='fixture',reasoning_effort='high',
        search_policy=None)
    job=c.build_job(root,1,'W',[]);record=execution.origin(root,1)
    saved_job=c.read(root/'jobs/001.json')
    assert c.job_record(job)==saved_job
    assert job['direct_materials']==[]  # Expanded producer facts, not job metadata.
    accepted_budget.accept_native(root,1,record,dict(task_ref=c.record_ref(root/'jobs/001.json')))
    create=terminal_code.__wrapped__((root,job,record),monkeypatch,execution_registry,fixed_code)
    row=create();call=row['accepted']['call_id']
    observation=e.record_observation(row['config'],row['adapter'],call)
    assert observation['source_code_version']==row['accepted']['code_version']
    managed=history._observe_managed_calls(root,job,call_ids=[call])[0]
    source=originals.node(root,job['step']);closure=fork_sources._Closure()
    closure.task(source,selected=True);closure.call(source,managed)
    entries=list(closure.entries.values());store=tmp_path/'reader-child'/originals.FORK_DIRECTORY
    for entry in entries:
        if entry['visibility'] in {'host_proof','effect_only'}:
            key=f'{originals.identity_key(entry["origin_identity"])}:{entry["path"]}'
            archive_io.copy_original(closure.paths[key],store/entry['storage_path'],dict(entry,kind='file'))
    snapshot=originals._snapshot(dict(entries=entries,origins=closure.origins,native_records=closure.records))
    inherited=originals._snapshot_source(store,snapshot,source.origin_identity,job['step'])
    science=[row['output'],row['directory']/'stdout.raw',row['directory']/'stderr.raw']
    saved={path:e.proof(path) for path in science}
    for entry in entries:
        if entry['visibility'] in {'host_proof','effect_only'} and entry['origin_identity']==source.origin_identity:
            (root/entry['path']).unlink()
    assert not (root/'jobs'/f'{job["step"]:03d}.json').exists()
    assert originals.source_job(inherited)==saved_job
    history.validate_managed_original(inherited,job,[managed],controls_only=True)
    history.validate_managed_original(inherited,job,[managed])
    assert all(e.proof(path)==proof for path,proof in saved.items())
    replace_equal_bytes(row['output'])
    history.validate_managed_original(inherited,job,[managed],controls_only=True)
    with pytest.raises(ValueError,match='identity|changed'):
        history.validate_managed_original(inherited,job,[managed])


def test_new_fork_screen_can_seal_using_its_retained_prefix(tmp_path,monkeypatch):
    from scidiscovery.research_loop import originals
    write=c.write
    def stopped_write(path,value):
        if path.name=='native-outcome.json':value=dict(value,native_started=False)
        return write(path,value)
    monkeypatch.setattr(c,'write',stopped_write)
    root = prepare(tmp_path)
    h(root, labels=('P',))
    finish(root, 2, 'W', [1], mechanism_findings=findings(blocked=('P',)))
    finish(root, 3, 'A', [1, 2], search_choice=dict(dispositions=[],
        next_focus=dict(mechanism_ref='h001.m1', action='screen')))
    finish(root, 4, 'W', [1, 2, 3], mechanism_findings=findings(blocked=('P',)))
    finish(root, 5, 'A', [1, 4])
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
    assert job['history']['prefix']['count'] > 0
    changed_prefix=copy.deepcopy(job)
    changed_prefix['history']['prefix']['count']+=1
    with pytest.raises(ValueError,match='Fork task differs from its exact selected input contract'):
        fork_runtime.validate_selected_job(child,c.workspace(child,4),changed_prefix)
    assert not (child / 'native-evidence/001/result-identity.json').exists()
    source = originals.selected_source(child)
    producer = originals.node(child, 1)
    seal_ref = originals.original_ref(producer, 'native-evidence/001/result-identity.json')
    assert originals.dependency(source, 1, seal_ref=seal_ref).origin_identity == producer.origin_identity
    with pytest.raises(ValueError, match='no unique exact original'):
        originals.dependency(source, 1, seal_ref=dict(seal_ref,
            origin_identity=originals.local_identity(child)))
    with pytest.raises(ValueError, match='no unique exact original'):
        originals.dependency(source, 1, seal_ref=dict(seal_ref, size_bytes=seal_ref['size_bytes'] + 1))
    record = execution.origin(child, 4)
    payload = dict(summary='Explicit scientific obstacle',
        scientific_result='The original missing observation remains missing.',
        limitations=['No model or experiment was executed by this fixture.'],
        implementation_selection=None, stage_intent='review',
        consultation_question=('For retained h001.m1 (P), do the existing blocked findings and '
            'missing observation justify ruling it out, or should A retain it as screened_pending? '
            'Use the exact retained originals and unresolved gap; no new experiments.'),
        mechanism_findings=findings(blocked=('P',)),
        candidate_nominations=dict(representatives=[], extras=[], gaps=[
            dict(mechanism_ref='h001.m1', reason='No usable model exists.')]))
    accepted_budget.accept_native(child,4,record,
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
    """Reuse the chain's stopped-process facts and exact native session identities."""
    from test_native_execution_inputs import chain
    write = c.write

    def write_with_runner_lock(path, value):
        result = write(path, value)
        if Path(path).name == 'native-intent.json':
            Path(path).with_name('runner.lock').touch(exist_ok=True)
        return result

    monkeypatch.setattr(c, 'write', write_with_runner_lock)
    return chain.__wrapped__(tmp_path, monkeypatch, request)




@pytest.mark.process_e2e
def test_fork_keeps_compiler_commit_and_dirty_drafts_in_child_git(tmp_path,quiescent_compiler_chain):
    """Real Git only; compiler fixture invokes no native participant or solver."""
    from scidiscovery.research_loop import code_versions,execution_inputs,experiments as e,originals
    root,backend,_,config,_,author,aliases=quiescent_compiler_chain
    code=c.workspace(root,2)/'code';code.mkdir(exist_ok=True)
    source=code/'scientific-author.json';original=author.read_bytes();source.write_bytes(original)
    expected={original,original+b'\n '}
    for name,raw in (('implementation.json',original),('implementation-next.json',original+b'\n ')):
        source.write_bytes(raw)
        public=c.workspace(root,2)/'artifacts'/name
        assert e.compile_implementation(config,e.load(config),source,c.read(aliases),public)['status']=='compiled'
    finish(root,2,'W',[1])
    seal=c.read(execution.origin(root,2)/'result-identity.json');rows=seal['compiled_implementations']
    assert len(rows)==2
    calls=[]
    for row in rows:
        compiler=c.read(root/row['compiler']['path']);version=compiler['code_version']
        call=execution_inputs._compiler_call_id(root,2,row['compiler'])
        calls.append((row,version,call))
    assert len({version['commit'] for _,version,_ in calls})==2
    source.write_bytes(b'current dirty author draft\n')
    (code/'untracked.sh').write_bytes(b'#!/bin/sh\nprintf untracked\n');(code/'untracked.sh').chmod(0o755)
    pending=execution.origin(root,2)/'result.pending.json';pending.write_bytes(b'{"summary":"unsealed draft"}\n')
    with controller._writer(root):pass
    child=tmp_path/'git-child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=2)
    preview=fork_service.preview(root,2,child,budget=limits)
    fork_service.create(root,2,child,budget=limits,expected_preview_id=preview['preview_id'],operation_id='git-and-drafts')
    selected=fork_runtime.build_selected_job(child,2)
    assert (c.workspace(child,2)/'code/scientific-author.json').read_bytes()==b'current dirty author draft\n'
    assert (c.workspace(child,2)/'code/untracked.sh').stat().st_mode&0o777==0o755
    manifest=originals._manifest(child)
    draft=next(entry for entry in manifest['entries'] if entry['path']=='native-evidence/002/result.pending.json')
    assert originals.resolve_ref(originals.selected_source(child),draft).read_bytes()==pending.read_bytes()
    assert not (execution.origin(child,2)/'result.pending.json').exists()
    assert list(execution.accepted_intents(child,2))==[]
    assert execution.record(child,2)==execution.origin(child,2)
    assert not selected.get('execution_qualifications')
    assert not (execution.origin(child,2)/'native-intent.json').exists()
    assert not (execution.origin(child,2)/'result-identity.json').exists()
    shutil.rmtree(root/'host-runtime/code-versions/repository.git')
    inherited=originals.selected_source(child)
    restored=[]
    for row,version,call in calls:
        assert execution_inputs.verify_original_compilation(inherited,row['compiler'],material=row['material'],
            client_binding=row['client_binding'])['code_version']==version
        tree=code_versions.materialize(child,step=2,call_id=call,code_version=version)
        try:restored.append((tree/version['entry']).read_bytes())
        finally:code_versions.cleanup_export(child,step=2,call_id=call)
    assert set(restored)==expected
    assert not backend.prepared and backend.submits==0
