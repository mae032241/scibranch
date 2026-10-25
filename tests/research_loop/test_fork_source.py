"""Frozen logical-task cuts; these fixtures launch no participant or solver."""
import shutil
from unittest.mock import patch

import pytest

from scidiscovery.research_loop import contracts as c, controller, fork_sources, originals
from test_execution_authority import prepare
from test_history_handoff import finish
from test_composed_execution_results import composed_registry


def completed_source(tmp_path):
    root, _ = prepare(tmp_path, enabled=False)
    write=c.write
    def stopped_write(path,value):
        # Declare the fixture's non-submission before its original seal.
        if path.name=='native-outcome.json':value=dict(value,native_started=False)
        return write(path,value)
    with patch.object(c,'write',stopped_write):
        for step, role, parents, action in [(1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')]:
            finish(root, step, role, parents, action=action)
    with controller._writer(root):
        pass
    return root


@pytest.mark.parametrize('latest_finished', [True, False])
def test_parent_quiescence_does_not_require_latest_finished_step(tmp_path, monkeypatch, latest_finished):
    root = completed_source(tmp_path)
    endpoint_results = {}
    for step, finished in ((999, not latest_finished), (1000, latest_finished)):
        c.write(root / 'jobs' / f'{step:03d}.json', dict(step=step, role='A' if finished else 'H'))
        # Sparse markers isolate latest-job selection from full seal validation.
        c.write(root / 'native-evidence' / f'{step:03d}' / 'result-identity.json', {})
        endpoint_results[c.workspace(root, step)] = dict(next_action='finish' if finished else 'analyze')
    validate = c.validate
    validated = []

    def validate_endpoint(work):
        if work in endpoint_results:
            validated.append(work)
            return endpoint_results[work]
        return validate(work)

    monkeypatch.setattr(c, 'validate', validate_endpoint)
    blocked = fork_sources._parent_state(root)

    assert validated == []
    assert blocked == []


def test_selected_cut_excludes_selected_output_and_keeps_exact_inputs(tmp_path):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    assert plan['blocked_reasons'] == []
    paths = {row['path'] for row in plan['entries'] if row['visibility'] != 'private_snapshot'}
    assert 'native-evidence/001/science-output.json' in paths
    assert 'project/nodes/002/inputs/parents/001/conclusion.json' in paths
    assert 'native-evidence/002/science-output.json' not in paths
    assert 'project/nodes/002/conclusion.json' not in paths
    assert plan['bootstrap']['next_step'] == 2
    assert [row['step'] for row in plan['prefix_entries']] == [1]
    assert dict(path=str(root/plan['source']['job_ref']['path'])) == c.record_ref(root / 'jobs/002.json')


def test_missing_science_does_not_block_metadata_but_changed_selector_fails_closed(tmp_path):
    root = completed_source(tmp_path)
    ref = originals.original_ref(originals.node(root, 2), 'jobs/002.json')
    with pytest.raises(ValueError, match='identity changed'):
        fork_sources.inspect_source(root, dict(ref, size_bytes=ref['size_bytes']+1))
    first=originals.node(root,1)
    originals.source_raw(first).unlink()
    plan=fork_sources.inspect_source(root,ref)
    assert plan['blocked_reasons']==[]
    child=tmp_path/'child'
    fork_sources.copy_source(plan,child/originals.FORK_DIRECTORY)
    assert fork_sources.validate_manifest(child,full=True)['source']['step']==2
    with pytest.raises((ValueError,OSError)):
        originals.resolve_ref(originals.selected_source(child),dict(
            path='project/nodes/002/inputs/parents/001/conclusion.json'))


def test_changed_sealed_native_terminal_is_not_a_completed_source(tmp_path):
    root = completed_source(tmp_path)
    record = root / 'native-evidence/002'
    c.write(record / 'native-outcome.json', dict(returncode=None, cleanup='unknown'))
    plan=fork_sources.inspect_source(root,2)
    assert any(row['code']=='unknown_native_effect' for row in plan['blocked_reasons'])
    with pytest.raises(ValueError,match='unresolved original effects'):
        fork_sources.copy_source(plan,tmp_path/'copy')


@pytest.mark.process_e2e
def test_completed_parent_with_original_unknown_managed_call_cannot_fork(tmp_path, composed_registry, monkeypatch):
    from scidiscovery.research_loop import execution, managed_wait
    from test_composed_execution_results import case
    write=c.write
    def stopped_write(path,value):
        if path.name=='native-outcome.json':value=dict(value,native_started=False)
        return write(path,value)
    monkeypatch.setattr(c,'write',stopped_write)
    root, _, _, _, _ = case(tmp_path, dict(collect=False))
    # A retained historical pre-wait seal is a genuine unknown call; it is not
    # manufactured by changing a sealed terminal or reconciling future effects.
    with monkeypatch.context() as patch:
        patch.setattr(managed_wait, 'before_seal', lambda *args: None)
        finish(root, 2, 'W', [1], preserve_native_intent=True, mechanism_findings=[
            dict(mechanism=label, basis='blocked', finding='Original backend call remains pending.')
            for label in ('P', 'Q')])
    finish(root, 3, 'A', [1, 2], action='finish')
    seal = c.read(execution.origin(root, 2) / 'result-identity.json')
    observed = c.read(root / seal['observations']['path'])
    assert observed['managed_calls'][0]['status'] == 'unknown'
    with controller._writer(root):
        pass
    plan = fork_sources.inspect_source(root, 3)
    assert not any(row['code'] == 'source_not_completed' for row in plan['blocked_reasons'])
    assert any(row['code'] == 'unknown_managed_effect' for row in plan['blocked_reasons'])
    with pytest.raises(ValueError, match='unresolved original effects'):
        fork_sources.copy_source(plan, tmp_path / 'copy')


def test_copied_controls_survive_parent_removal_without_retaining_science(tmp_path,monkeypatch):
    root = completed_source(tmp_path)
    plan = fork_sources.inspect_source(root, 2)
    before = (root / 'jobs/002.json').read_bytes()
    child = tmp_path / 'child'
    def forbidden(*args,**kwargs):pytest.fail('Fork attempted to hardlink an original')
    monkeypatch.setattr(fork_sources.files,'link_original',forbidden)
    fork_sources.copy_source(plan, child / originals.FORK_DIRECTORY)
    entry=next(row for row in plan['entries'] if row['path']=='jobs/002.json')
    retained=child/originals.FORK_DIRECTORY/entry['storage_path']
    assert retained.stat().st_ino!=(root/'jobs/002.json').stat().st_ino
    shutil.rmtree(root)
    selected = fork_sources.selected_task(child)
    assert originals.resolve_ref(selected['source'], selected['source_job_ref']).read_bytes() == before
    assert selected['job']['step'] == 2
    assert fork_sources.validate_manifest(child, full=True)['source']['step'] == 2
    assert not (child / originals.FORK_DIRECTORY / 'public').exists()
    for entry in plan['entries']:
        if entry['visibility'] in {'scientific_input','historical_science'}:
            assert not (child/originals.FORK_DIRECTORY/entry['storage_path']).exists()
    with pytest.raises((ValueError,OSError)):
        originals.source_raw(originals.node(child,1))
    assert not (child / 'jobs').exists()


def test_fork_restores_current_code_and_output_drafts_without_publishing_result(tmp_path):
    from scidiscovery.research_loop import execution,fork_runtime,fork_service
    root=completed_source(tmp_path)
    code=c.workspace(root,2)/'code';code.mkdir()
    (code/'main.py').write_bytes(b'current dirty code\n')
    (code/'untracked.sh').write_bytes(b'#!/bin/sh\nprintf draft\n')
    (code/'untracked.sh').chmod(0o755)
    pending=root/'native-evidence/002/result.pending.json'
    pending.write_bytes(b'{"summary":"unfinished handoff draft"}\n');pending.chmod(0o640)
    child=tmp_path/'draft-child';limits=dict(max_tokens=1000,max_seconds=30,max_steps=1)
    preview=fork_service.preview(root,2,child,budget=limits)
    fork_service.create(root,2,child,budget=limits,expected_preview_id=preview['preview_id'],operation_id='current-drafts')
    job=fork_runtime.build_selected_job(child,2)
    restored=c.workspace(child,2)/'code'
    assert (restored/'main.py').read_bytes()==b'current dirty code\n'
    assert (restored/'untracked.sh').read_bytes()==(code/'untracked.sh').read_bytes()
    assert (restored/'untracked.sh').stat().st_mode&0o777==0o755
    manifest=originals._manifest(child)
    draft=next(entry for entry in manifest['entries'] if entry['path']=='native-evidence/002/result.pending.json')
    saved=originals.resolve_ref(originals.selected_source(child),draft)
    assert saved.read_bytes()==pending.read_bytes()
    assert saved.stat().st_mode&0o777==0o640
    assert not (child/'native-evidence/002/result.pending.json').exists()
    assert list(execution.accepted_intents(child,2))==[]
    assert execution.record(child,2)==execution.origin(child,2)
    assert not (child/'host-runtime/code-versions').exists()
    assert not job.get('execution_qualifications') and 'execution_authority' not in job
    for name in ('native-intent.json','native-outcome.json','result-identity.json'):
        assert not (child/'native-evidence/002'/name).exists()
    assert (child/'pause.request').is_file()
    (restored/'main.py').write_bytes(b'child edits remain mutable\n')
    fork_runtime.build_selected_job(child,2)
    assert (restored/'main.py').read_bytes()==b'child edits remain mutable\n'
    assert (code/'main.py').read_bytes()==b'current dirty code\n'


@pytest.mark.parametrize('name',['result.pending.json','science-output.json'])
def test_inert_continuation_draft_does_not_create_child_execution_facts(tmp_path,name):
    """A private-entry fixture exercises the real new-child execution readers."""
    from scidiscovery.research_loop import execution,fork_runtime,fork_service
    root=completed_source(tmp_path);child=tmp_path/'continuation-child'
    limits=dict(max_tokens=1000,max_seconds=30,max_steps=1)
    preview=fork_service.preview(root,2,child,budget=limits)
    fork_service.create(root,2,child,budget=limits,expected_preview_id=preview['preview_id'],operation_id='inert-continuation')
    manifest=originals._manifest(child);identity=manifest['source']['origin_identity']
    path='native-evidence/002/continuations/001/'+name
    entry=dict(origin_identity=identity,path=path,size_bytes=3,mode=0o640,
        storage_path=f'inherited/{originals.identity_key(identity)}/{path}',visibility='private_snapshot')
    saved=child/originals.FORK_DIRECTORY/entry['storage_path']
    saved.parent.mkdir(parents=True);saved.write_bytes(b'B\n\n');saved.chmod(0o640)
    manifest['entries'].append(entry);c.write(child/originals.MANIFEST,manifest)
    fork_runtime.build_selected_job(child,2)
    assert list(execution.accepted_intents(child,2))==[]
    assert execution.record(child,2)==execution.origin(child,2)
    assert not (execution.origin(child,2)/'continuations').exists()
    assert originals.resolve_ref(originals.selected_source(child),entry).read_bytes()==b'B\n\n'
    intent=execution.origin(child,2)/'native-intent.json'
    c.write(intent,dict(task_ref=c.record_ref(child/'jobs/002.json')))
    assert [record for record,_ in execution.accepted_intents(child,2)]==[execution.origin(child,2)]
    assert execution.record(child,2)==execution.origin(child,2)
