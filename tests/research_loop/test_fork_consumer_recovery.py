"""Fork consumers bind exact originals; synthetic native identities grant no permission."""
import copy
import os
from pathlib import Path
import shutil

import pytest

from scidiscovery.research_loop import (
    contracts as c, controller, execution, fork_runtime, fork_service, native,
)
from test_breadth_science import a, findings, h, prepare
from test_history_handoff import finish
from test_composed_execution_results import composed_registry
from execution_fixtures import execution_registry
from test_mutable_run_inputs import fixed_code,terminal_code
from test_worker_fragments import fragment


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


@pytest.fixture
def inherited_completed_output(tmp_path):
    """Exact receipt/control read fixture; it grants no new author execution."""
    from scidiscovery import archive_io
    from scidiscovery.research_loop import author_closeout as author,experiments as e,originals
    root=tmp_path/'producer';root.mkdir();controls=[]
    identity=dict(goal_id='inherited-output-fixture',settings_ref=dict(path=str(root/'settings.json')))
    def write(path,value):
        c.write(root/path,value);controls.append(path)
        return author._ref(root,root/path)
    write('settings.json',dict(goal_id=identity['goal_id'],handoff_protocol=c.HANDOFF_PROTOCOL))
    job=write('jobs/002.json',dict(step=2,role='W',handoff_protocol=c.HANDOFF_PROTOCOL))
    original={name:write('native-evidence/002/'+name,{}) for name in
        ('native-intent.json','native-outcome.json','usage.json','experiment-config.json')}
    permit=write('host-recovery/author-closeout/002.json',dict(version=1,context='new_context',
        scope='author_closeout_only',step=2,source_attempt=0,job=job,
        settings=author._ref(root,root/'settings.json'),original_intent=original['native-intent.json'],
        original_outcome=original['native-outcome.json'],original_usage=original['usage.json'],
        original_experiment_config=original['experiment-config.json'],calls=[]))
    successor=write('host-recovery/author-closeout/002-successor.json',dict(original_permit=permit))
    upgrade=write('host-recovery/author-closeout/002-output-upgrade.json',dict(version=1,
        scope='completed_author_output_code_upgrade',step=2,attempt=2,successor=successor,
        authorization_note='Preserved original control',authorization_statement='Preserved explicit acceptance'))
    record=root/'native-evidence/002/continuations/002'
    intent=dict(attempt=2,continuation_kind='author_closeout',task_ref=dict(path=str(root/'jobs/002.json')))
    outcome=dict(returncode=0,stop_reason='author_closeout_token_limit',raw_ref=None)
    names=('native-intent.json','native-outcome.json','usage.json','usage-observation.json','native.jsonl',
        'runner-process.json','native-process.json','experiment-config.json','author-closeout-facts.json')
    refs={name:write('native-evidence/002/continuations/002/'+name,
        intent if name=='native-intent.json' else outcome if name=='native-outcome.json' else {}) for name in names}
    pending=record/'result.pending.json';pending.write_bytes(b'{"summary":"original returned bytes"}\n');pending.chmod(0o400)
    raw=record/'science-output.json';os.link(pending,raw)
    refs['result.pending.json']=author._ref(root,pending)
    ledger=write('control-ledger/000001.json',dict(seq=1))
    facts=dict(scope='Preserved original completion; no new execution permission')
    receipt=write('host-recovery/author-closeout/002-output-acceptance.json',dict(version=1,
        scope='explicit_completed_author_output',step=2,attempt=2,upgrade=upgrade,job=job,originals=refs,
        session=refs['native.jsonl'],usage_originals=[],facts=facts,admission=dict(accounted_tokens=0,
            max_tokens=1,required_tokens=0,available_steps=0,required_steps=0,
            seconds_remaining=0,required_seconds=0,ledger=ledger)))
    seal=dict(completed_output_acceptance=receipt,completed_output_limitation=facts,raw_ref=e.proof(raw))
    store=tmp_path/'child'/originals.FORK_DIRECTORY;entries=[]
    for path in controls:
        entry=dict(origin_identity=identity,path=path,size_bytes=(root/path).stat().st_size,mode=0o400,
            storage_path=f'inherited/{originals.identity_key(identity)}/{path}',visibility='host_proof')
        archive_io.copy_original(root/path,store/entry['storage_path'],dict(entry,kind='file'));entries.append(entry)
    for path in (pending,raw):
        relative=path.relative_to(root).as_posix()
        entries.append(dict(origin_identity=identity,path=relative,size_bytes=path.stat().st_size,mode=0o400,
            storage_path=f'inherited/{originals.identity_key(identity)}/{relative}',visibility='historical_science'))
    snapshot=originals._snapshot(dict(entries=entries,origins={originals.identity_key(identity):dict(
        identity=identity,original_root=str(root))}))
    source=originals._snapshot_source(store,snapshot,identity,2)
    return root,source,seal,record,intent,outcome,pending


def test_inherited_completed_output_reads_original_acceptance_without_new_budget(inherited_completed_output,monkeypatch):
    from scidiscovery.research_loop import budget
    root,source,seal,record,intent,outcome,pending=inherited_completed_output
    def forbidden(*args,**options):pytest.fail('Inherited acceptance requested a new local budget')
    monkeypatch.setattr(budget,'snapshot',forbidden)
    shutil.rmtree(root/'jobs');shutil.rmtree(root/'host-recovery');shutil.rmtree(root/'control-ledger')
    raw,accepted=c._native_output_identity(root,2,record,intent,outcome,source=source,seal=seal)
    assert raw==seal['raw_ref'] and accepted['receipt']==seal['completed_output_acceptance']
    assert accepted['facts']==seal['completed_output_limitation']
    assert pending.samefile(record/'science-output.json')
    wrong=copy.deepcopy(seal);wrong['completed_output_limitation']={'scope':'different facts'}
    with pytest.raises(ValueError,match='changed'):
        c._native_output_identity(root,2,record,intent,outcome,source=source,seal=wrong)


def test_inherited_completed_output_checks_original_alias_only_on_consumption(inherited_completed_output):
    from test_direct_public_collect import replace_equal_bytes
    root,source,seal,record,intent,outcome,pending=inherited_completed_output
    replace_equal_bytes(pending)
    raw,accepted=c._native_output_identity(root,2,record,intent,outcome,source=source,seal=seal,controls_only=True)
    assert raw==seal['raw_ref'] and accepted is not None
    with pytest.raises(ValueError,match='alias changed'):
        c._native_output_identity(root,2,record,intent,outcome,source=source,seal=seal)


@pytest.fixture
def sealed_completed_output_source(tmp_path, monkeypatch):
    """Synthetic acceptance declaration around a real task/control cut.

    This tests metadata consumers, not the complete author producer, original
    managed-call eligibility, budget admission, or native sealing lifecycle.
    """
    from scidiscovery.research_loop import author_closeout as author, budget
    write = c.write
    def stopped_write(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)
    monkeypatch.setattr(c, 'write', stopped_write)
    root = prepare(tmp_path)
    h(root, labels=('P',))
    job, _, first = finish(root, 2, 'W', [1], mechanism_findings=findings(blocked=('P',)))
    def control(path, value):
        c.write(root / path, value)
        return author._ref(root, root / path)
    if not (first / 'experiment-config.json').exists():
        control('native-evidence/002/experiment-config.json', {})
    original = {name: author._ref(root, first / name) for name in
        ('native-intent.json', 'native-outcome.json', 'usage.json', 'experiment-config.json')}
    job_ref = author._ref(root, root / 'jobs/002.json')
    permit = control('host-recovery/author-closeout/002.json', dict(version=1,
        context='new_context', scope='author_closeout_only', step=2, source_attempt=0,
        job=job_ref, settings=author._ref(root, root / 'settings.json'), calls=[],
        original_intent=original['native-intent.json'], original_outcome=original['native-outcome.json'],
        original_usage=original['usage.json'], original_experiment_config=original['experiment-config.json']))
    task = c.record_ref(root / 'jobs/002.json')
    previous = first
    records = []
    for attempt in (1, 2):
        record = first / f'continuations/{attempt:03d}'
        c.write(record / 'native-intent.json', dict(task_ref=task, attempt=attempt,
            continuation_kind='author_closeout', context_mode='new_context',
            root_intent_ref=c.record_ref(first / 'native-intent.json'), previous=execution.proof(previous)))
        c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
        c.write(record / 'native-outcome.json', dict(returncode=0,
            stop_reason='author_closeout_token_limit' if attempt == 2 else 'author_closeout_wall_limit',
            cleanup='observed_group_cleaned', raw_ref=None))
        for name in ('usage-observation.json', 'native.jsonl', 'runner-process.json',
                'native-process.json', 'experiment-config.json', 'author-closeout-facts.json'):
            c.write(record / name, {})
        records.append(record); previous = record
    successor = control('host-recovery/author-closeout/002-successor.json', dict(original_permit=permit))
    upgrade = control('host-recovery/author-closeout/002-output-upgrade.json', dict(version=1,
        scope='completed_author_output_code_upgrade', step=2, attempt=2, successor=successor,
        authorization_note='Synthetic retained control', authorization_statement='Synthetic retained acceptance'))
    record = records[-1]
    pending = record / 'result.pending.json'
    pending.write_bytes((first / 'science-output.json').read_bytes()); pending.chmod(0o400)
    raw = record / 'science-output.json'; os.link(pending, raw)
    names = ('native-intent.json', 'native-outcome.json', 'usage.json', 'usage-observation.json', 'native.jsonl',
        'runner-process.json', 'native-process.json', 'experiment-config.json', 'author-closeout-facts.json',
        'result.pending.json')
    refs = {name: author._ref(root, record / name) for name in names}
    session = root / 'native-homes/002/context-002/sessions/fixture.jsonl'
    c.write(session, dict(type='session_meta', payload=dict(id='fixture-only')))
    with controller._writer(root), budget._ledger_writer(root):
        events, parent = budget._events(root, c.settings(root))
        event = budget._append(root, c.settings(root), events, parent,
            kind='clock', phase='paused', reason='Synthetic metadata-consumer fixture')
    ledger = author._ref(root, root / f'control-ledger/{event["seq"]:06d}.json')
    facts = dict(scope='Synthetic retained declaration; no execution or scientific qualification')
    receipt = control('host-recovery/author-closeout/002-output-acceptance.json', dict(version=1,
        scope='explicit_completed_author_output', step=2, attempt=2, upgrade=upgrade, job=job_ref,
        originals=refs, session=author._ref(root, session), usage_originals=[], facts=facts,
        admission=dict(accounted_tokens=0, max_tokens=100000, required_tokens=0,
            available_steps=0, required_steps=0, seconds_remaining=0, required_seconds=0, ledger=ledger)))
    seal_path = first / 'result-identity.json'; seal = c.read(seal_path)
    seal.update(raw_ref=c.file_ref(raw), completed_output_acceptance=receipt,
        completed_output_limitation=facts, execution_ref=execution._reference([first, *records]))
    c.write(seal_path, seal)
    code = c.workspace(root, 2) / 'code'; code.mkdir(exist_ok=True)
    (code / 'draft.py').write_bytes(b'current unsealed code B\n')
    return dict(root=root, job=job, record=record, pending=pending, raw=raw, session=session,
        seal=seal, receipt=root / receipt['path'])


def test_selected_sealed_acceptance_missing_pending_keeps_metadata_fork_available(
        tmp_path, monkeypatch, sealed_completed_output_source):
    """The real inspect/snapshot path skips only unselected accepted science."""
    from scidiscovery.research_loop import author_closeout as author, fork_sources, originals
    row = sealed_completed_output_source; root = row['root']; record = row['record']
    source = originals.node(root, 2); seal = row['seal']
    intent = c.read(record / 'native-intent.json'); outcome = c.read(record / 'native-outcome.json')
    assert c._native_output_identity(root, 2, record, intent, outcome,
        source=source, seal=seal)[1]['receipt'] == seal['completed_output_acceptance']
    row['pending'].unlink()
    with pytest.raises((ValueError, OSError)):
        c._native_output_identity(root, 2, record, intent, outcome, source=source, seal=seal)
    controls = [root / 'jobs/002.json', execution.origin(root, 2) / 'result-identity.json',
        row['receipt'], record / 'native-outcome.json']
    before = {path: path.read_bytes() for path in controls}
    protected = {row['pending'], row['raw'], row['session'], record / 'native.jsonl'}
    open_original = Path.open; record_original = execution.record
    def guarded_open(path, *args, **options):
        if path in protected:
            pytest.fail('Metadata fork tried to read sealed pending/raw/log/session science')
        return open_original(path, *args, **options)
    def guarded_record(owner, step, *args, **options):
        if owner == root and step == 2:
            pytest.fail('Metadata snapshot called the local scientific execution reader')
        return record_original(owner, step, *args, **options)
    def forbidden(*args, **options):
        pytest.fail('Metadata fork tried to requalify the completed native return')
    child = tmp_path / 'accepted-child'; limits = dict(max_tokens=1000, max_seconds=30, max_steps=1)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', guarded_open)
        patch.setattr(execution, 'record', guarded_record)
        patch.setattr(author, 'require_output_eligibility', forbidden)
        patch.setattr(author, '_completed_output_facts', forbidden)
        plan = fork_sources.inspect_source(root, 2)
        assert plan['blocked_reasons'] == []
        pending_path = row['pending'].relative_to(root).as_posix()
        entry = next(item for item in plan['entries'] if item['path'] == pending_path)
        assert entry['visibility'] == 'historical_science'
        assert not any(item['visibility'] == 'private_snapshot' and item['path'] == pending_path
            for item in plan['entries'])
        preview = fork_service.preview(root, 2, child, budget=limits)
        fork_service.create(root, 2, child, budget=limits, expected_preview_id=preview['preview_id'],
            operation_id='missing-selected-accepted-pending')
        selected = fork_runtime.build_selected_job(child, 2)
        fork_sources.validate_manifest(child, full=True)
    assert all(path.read_bytes() == data for path, data in before.items())
    assert not row['pending'].exists() and row['raw'].is_file()
    assert (c.workspace(child, 2) / 'code/draft.py').read_bytes() == b'current unsealed code B\n'
    manifest = originals._manifest(child)
    assert not any((child / originals.FORK_DIRECTORY / item['storage_path']).exists()
        for item in manifest['entries'] if item['visibility'] in {'scientific_input', 'historical_science'})
    for name in ('native-intent.json', 'native-outcome.json', 'result-identity.json',
            'result.pending.json', 'science-output.json', 'continuations'):
        assert not (execution.origin(child, 2) / name).exists()
    assert list(execution.accepted_intents(child, 2)) == []
    assert execution.record(child, 2) == execution.origin(child, 2)
    assert not selected.get('execution_qualifications') and 'execution_authority' not in selected
    with pytest.raises((ValueError, OSError)):
        c._native_output_identity(root, 2, record, intent, outcome, source=source, seal=seal)


@pytest.mark.parametrize('changed', ['receipt', 'execution_ref', 'intent'])
def test_selected_sealed_acceptance_rejects_broken_controls(sealed_completed_output_source, changed):
    from scidiscovery.research_loop import fork_sources
    row = sealed_completed_output_source; root = row['root']
    if changed == 'receipt':
        path = row['receipt']; value = c.read(path); value['scope'] = 'unaccepted'
    elif changed == 'execution_ref':
        path = execution.origin(root, 2) / 'result-identity.json'
        value = c.read(path); value['execution_ref']['attempt'] = 1
    else:
        path = row['record'] / 'native-intent.json'; value = c.read(path)
        value['root_intent_ref'] = dict(path=str(root / 'jobs/002.json'))
    c.write(path, value)
    with pytest.raises((ValueError, OSError)):
        fork_sources.inspect_source(root, 2)


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
    c.write(first / 'native-intent.json', dict(task_ref=task))
    c.write(first / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    stopped = dict(returncode=1, stop_reason='fixture_stopped', native_started=False,
        cleanup='observed_group_cleaned')
    c.write(first / 'native-outcome.json', stopped)
    record = first / 'continuations/001'
    c.write(record / 'native-intent.json', dict(task_ref=task, attempt=1, continuation_kind='repair',
        root_intent_ref=c.record_ref(first / 'native-intent.json'), previous=execution.proof(first)))
    c.write(record / 'usage.json', dict(input_tokens=1, cached_input_tokens=0, output_tokens=1))
    c.write(record / 'native-outcome.json', stopped)
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
    c.write(execution.origin(child, 1) / 'native-intent.json',
        dict(task_ref=c.record_ref(child / 'jobs/001.json')))
    assert [path for path, _ in execution.accepted_intents(child, 1)] == [execution.origin(child, 1)]
    assert execution.record(child, 1) == execution.origin(child, 1)


def test_inherited_git_observer_uses_copied_controls_and_keeps_science_stat_checks(tmp_path,terminal_code):
    """Real observation/control readers; this source fixture mocks Git and all launches."""
    from scidiscovery import archive_io
    from scidiscovery.research_loop import experiments as e,fork_sources,history,originals
    from test_direct_public_collect import replace_equal_bytes
    row=terminal_code();root=row['root'];job=row['job'];call=row['accepted']['call_id']
    observation=e.record_observation(row['config'],row['adapter'],call)
    assert observation['source_code_version']==row['accepted']['code_version']
    managed=history.managed_calls(root,job,call_ids=[call])[0]
    source=originals.node(root,job['step']);closure=fork_sources._Closure()
    closure.task(source,selected=True);closure.call(source,managed)
    entries=list(closure.entries.values());store=tmp_path/'reader-child'/originals.FORK_DIRECTORY
    for entry in entries:
        if entry['visibility'] in {'host_proof','effect_only'}:
            key=f'{originals.identity_key(entry["origin_identity"])}:{entry["path"]}'
            archive_io.copy_original(closure.paths[key],store/entry['storage_path'],dict(entry,kind='file'))
    snapshot=originals._snapshot(dict(entries=entries,origins=closure.origins,native_records=closure.records,
        legacy_compilations=closure.legacy_compilations))
    inherited=originals._snapshot_source(store,snapshot,source.origin_identity,job['step'])
    science=[row['output'],row['directory']/'stdout.raw',row['directory']/'stderr.raw']
    saved={path:e.proof(path) for path in science}
    for entry in entries:
        if entry['visibility'] in {'host_proof','effect_only'} and entry['origin_identity']==source.origin_identity:
            (root/entry['path']).unlink()
    assert not (root/'jobs'/f'{job["step"]:03d}.json').exists()
    assert originals.source_job(inherited)==job
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
    a(root, 3, [1, 2], focus='h001.m1')
    finish(root, 4, 'W', [1, 2, 3], mechanism_findings=findings(blocked=('P',)))
    a(root, 5, [1, 4], action='finish')
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
@pytest.mark.parametrize('selected_step', [3, 4])
def test_fork_formal_reviewer_can_bind_inherited_compiler_originals(
        tmp_path, quiescent_compiler_chain, selected_step):
    from scidiscovery.research_loop import backends, experiments as e, formal_review as review

    root, backend, _, config, _, author, aliases = quiescent_compiler_chain
    assert backends.selected(c.settings(root)).complete_authorship
    code_author = c.workspace(root, 2) / 'code/implementation.json'
    code_author.parent.mkdir(exist_ok=True)
    code_author.write_bytes(author.read_bytes())
    public = c.workspace(root, 2) / 'artifacts/implementation.json'
    compiled = e.compile_implementation(config, e.load(config), code_author, c.read(aliases), public)
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

    # Validate the exact inherited compiler through the child's own Git holder.
    from scidiscovery.research_loop import execution_inputs, originals
    producer = originals.node(child, 2)
    seal = c.read(originals.source_record(producer, 'result-identity.json'))
    row = seal['compiled_implementations'][0]
    execution_inputs.verify_original_compilation(producer, row['compiler'],
        material=row['material'], client_binding=row['client_binding'])
    assert not (child / 'jobs/002.json').exists()
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
    assert binding['compiler_scientific_materials']==[]
    compiler=execution_inputs.verify_original_compilation(producer,row['compiler'],
        material=row['material'],client_binding=row['client_binding'])
    owner=originals.source_job(producer)
    input_original=compiler['inputs'][0]['original']
    scientific=execution_inputs.resolve_native_original(child,input_original,compiler_job=owner,source=producer)
    assert scientific in binding['materials']
    assert scientific['path']==input_original['material_path']
    assert scientific['size_bytes']==input_original['size_bytes']
    actual=originals.resolve_root_ref(child,dict(scientific,path=scientific['source']))
    assert actual==Path(scientific['source']) and actual.stat().st_size==scientific['size_bytes']
    assert not (c.workspace(child,4)/scientific['path']).exists()
    assert not backend.prepared and backend.submits==0

    wrong=copy.deepcopy(input_original);wrong['instance_id']='unbound-instance'
    with pytest.raises(ValueError,match='exact producer/material'):
        execution_inputs.resolve_native_original(child,wrong,compiler_job=owner,source=producer)
    repository=child/'host-runtime/code-versions/repository.git'
    assert repository.is_dir()
    shutil.rmtree(repository)
    with pytest.raises(ValueError,match='code version lost its exact selected source or original pin'):
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
    retained,_,seal,_=history._source(nested,4)
    assert originals.recovery_refs(originals.node(nested,4),retained)==bound_recoveries
    assert originals._manifest(nested)['cut']['execution_recoveries']==bound_recoveries
    assert search._screen_refs(nested,retained,seal,search.project(nested,5))=={'h001.m1'}


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
