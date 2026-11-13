"""Mount consumers use explicit facts; no model, solver or namespace runs."""
from collections import Counter
from pathlib import Path
import sys

import pytest

from scidiscovery.research_loop import (
    accepted_budget, author_closeout, contracts as c, execution, lineage,
    shared_input_views as views,
)
from scidiscovery.research_loop.experiments import UnsupportedExecution
from test_history_handoff import finish, scene


def _reads(monkeypatch, paths, *, readers=None, json_reads=None, json_root=None):
    counts = Counter()
    read_text, read_bytes = Path.read_text, Path.read_bytes

    def record(path):
        counts[path] += 1
        if readers is not None:
            caller, stack = sys._getframe(1), []
            while caller is not None:
                module = caller.f_globals.get('__name__', '')
                if module.startswith('scidiscovery.research_loop.'):
                    stack.append(f'{module.rsplit(".", 1)[-1]}.{caller.f_code.co_name}:{caller.f_lineno}')
                caller = caller.f_back
            readers.setdefault(path, []).append(' <- '.join(stack))

    def text(path, *args, **kwargs):
        if path in paths:record(path)
        if json_reads is not None and path.is_relative_to(json_root) and path.suffix == '.json':
            json_reads[path] += 1
        return read_text(path, *args, **kwargs)

    def bytes_(path, *args, **kwargs):
        if path in paths:record(path)
        if json_reads is not None and path.is_relative_to(json_root) and path.suffix == '.json':
            json_reads[path] += 1
        return read_bytes(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', text)
    monkeypatch.setattr(Path, 'read_bytes', bytes_)
    return counts


def _no_reads(patch):
    def forbidden(*args, **kwargs):
        raise AssertionError('A fact consumer attempted a file or metadata read')

    patch.setattr(c, 'read', forbidden)
    patch.setattr(c, 'regular', forbidden)
    for name in ('read_text', 'read_bytes', 'open', 'stat', 'lstat', 'resolve',
            'exists', 'is_file', 'is_dir', 'is_symlink', 'samefile'):
        patch.setattr(Path, name, forbidden)
    patch.setattr(views.os, 'scandir', forbidden)
    for name in ('record', 'records', 'accepted_intents', 'accepted_intents_from_facts'):
        patch.setattr(execution, name, forbidden)


def test_prepared_node_has_empty_native_views_without_loading_a_chain(scene, monkeypatch):
    job = c.build_job(scene, 1, 'H', [])
    work = c.workspace(scene, 1)

    def forbidden(*args, **kwargs):
        pytest.fail('An unsubmitted node traversed accepted native history')

    monkeypatch.setattr(accepted_budget, 'load', forbidden)
    monkeypatch.setattr(execution, 'accepted_intents_from_facts', forbidden)
    facts = views.load_source_facts(scene, job, work)
    assert facts['native']['intent'] == {} and facts['author'] is None
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert views.selected_code_from_facts(facts) == []
        assert views.wake_mappings_from_facts(facts) == []
        assert views.logical_original_from_facts(facts, work / 'work/unselected') is None


def test_missing_initial_intent_keeps_the_accepted_continuation_chain_refusal(scene, monkeypatch):
    job = c.build_job(scene, 1, 'H', [])
    work, origin = c.workspace(scene, 1), execution.origin(scene, 1)
    task_ref = c.record_ref(scene / 'jobs/001.json')
    initial = dict(task_ref=task_ref)
    c.write(origin / 'native-intent.json', initial)
    accepted_budget.accept_native(scene, 1, origin, initial)
    accepted_budget.accept_terminal(scene, 1, origin,
        meter=dict(input_tokens=0, cached_input_tokens=0, output_tokens=0), unknown=False,
        metering=dict(coverage='terminal', reporting_delay_unmeasured=False,
            inflight_usage_unknown=False))
    record = origin / 'continuations/001'
    continuation = dict(attempt=1, task_ref=task_ref,
        root_intent_ref=c.record_ref(origin / 'native-intent.json'),
        previous={'native-intent.json': c.record_ref(origin / 'native-intent.json')})
    c.write(record / 'native-intent.json', continuation)
    accepted_budget.accept_native(scene, 1, record, continuation)
    assert [path for path, _ in execution.accepted_intents(scene, 1)] == [origin, record]
    (origin / 'native-intent.json').unlink()
    counts = _reads(monkeypatch, {scene / 'host-history/accepted-budget/head.json',
        record / 'native-intent.json'})
    with pytest.raises(ValueError, match='Accepted native continuation identity changed'):
        views.logical_original(scene, job, work / 'work/execution-results/001/result.json')
    assert counts == Counter({scene / 'host-history/accepted-budget/head.json': 1,
        record / 'native-intent.json': 1})


def test_one_source_producer_feeds_the_real_mount_and_wrapper_consumers(scene, monkeypatch):
    job, work, record = finish(scene, 1, 'H', [], action='wait')
    tracked = {scene / 'host-history/accepted-budget/head.json', record / 'native-intent.json',
        scene / 'jobs/001.json'}
    counts = _reads(monkeypatch, tracked)
    sources = views.load_source_facts(scene, job, work)
    assert counts == Counter({record / 'native-intent.json': 1,
        scene / 'host-history/accepted-budget/head.json': 1})
    monkeypatch.setattr(views, 'ensure_capability', lambda **kwargs: '/inert/bwrap')
    wrapped = views.load_wrap_facts(scene, job, work, source_facts=sources, selected_code=[])
    assert counts == Counter({record / 'native-intent.json': 1,
        scene / 'host-history/accepted-budget/head.json': 1})
    argv = ['inert-native', '--sandbox', 'danger-full-access', '--network=original']
    expected = views.mount_arguments_from_facts(wrapped['mounts'])
    assert str(work / 'inputs/global/objective.md') in expected
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert views.mount_arguments_from_facts(wrapped['mounts']) == expected
        command = views.wrap_from_facts(wrapped, argv)
        assert command[-len(argv):] == argv
        assert views.selected_code_from_facts(sources) == []
        assert views.wake_mappings_from_facts(sources) == []
        assert views.logical_original_from_facts(sources, work / 'unselected') is None


def test_source_facts_cannot_enter_another_namespace_or_attempt(scene):
    job = c.build_job(scene, 1, 'H', [])
    work = c.workspace(scene, 1)
    sources = views.load_source_facts(scene, job, work)
    with pytest.raises(UnsupportedExecution, match='another task or namespace'):
        views.load_mount_facts(scene, job, work, target_work='/another-task', source_facts=sources)
    with pytest.raises(UnsupportedExecution, match='another task or attempt'):
        views.load_source_facts(scene, job, work, native=dict(
            record=execution.origin(scene, 1),
            intent=dict(attempt=1, task_ref=c.record_ref(scene / 'jobs/001.json'))))


def test_mount_binding_is_read_once_before_the_final_metadata_observation(scene, monkeypatch):
    job = c.build_job(scene, 1, 'H', [])
    work = c.workspace(scene, 1)
    views.dynamic_root(scene, work, views.DYNAMIC_ROOTS[0])
    binding = views._binding(scene, work)
    counts = _reads(monkeypatch, {binding})
    facts = views.load_mount_facts(scene, job, work, include_skills=False)
    assert counts == Counter({binding: 1})
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert views.mount_arguments_from_facts(facts)


def test_mount_final_observation_rejects_a_replaced_root_without_reopening_binding(scene, monkeypatch):
    job = c.build_job(scene, 1, 'H', [])
    work = c.workspace(scene, 1)
    retained = views.dynamic_root(scene, work, 'execution-results')
    binding = views._binding(scene, work)
    counts = _reads(monkeypatch, {binding})
    walk = views._Inventory.walk
    replaced = False

    def replace_after_inventory(inventory, path):
        nonlocal replaced
        yield from walk(inventory, path)
        if path == work and not replaced:
            retained.rename(retained.with_name('previous-execution-results'))
            retained.mkdir()
            replaced = True

    monkeypatch.setattr(views._Inventory, 'walk', replace_after_inventory)
    with pytest.raises(UnsupportedExecution, match='replaced'):
        views.load_mount_facts(scene, job, work, include_skills=False)
    assert replaced and counts == Counter({binding: 1})


def test_private_mount_observes_the_latest_holder_writer_declaration(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'W', [1])
    work = c.workspace(scene, 2)
    views.dynamic_root(scene, work, views.DYNAMIC_ROOTS[0])
    binding = views._binding(scene, work)
    counts = _reads(monkeypatch, {binding})
    pin = views._pin_holders

    def replace_new_holder(root, workspace, holders):
        value = pin(root, workspace, holders)
        holder = holders[0]
        holder.rename(holder.with_name('previous-' + holder.name))
        holder.mkdir()
        return value

    monkeypatch.setattr(views, '_pin_holders', replace_new_holder)
    with pytest.raises(UnsupportedExecution, match='private original holder was replaced'):
        views.load_mount_facts(scene, job, work, include_skills=False, private=True)
    # Initial declaration plus the actual holder writer's fresh admission read.
    assert counts == Counter({binding: 2})


def test_wake_producer_reads_selected_originals_once_and_refreshes_the_next_operation(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'W', [1])
    work, record = c.workspace(scene, 2), execution.origin(scene, 2)
    scope = dict(step=2, job_ref=c.record_ref(scene / 'jobs/002.json'))
    interface = dict(executor='local_execution:command')
    accepted = scene / 'host-runtime/wake-fixture/accepted.json'
    observed = accepted.with_name('observation.json')
    config = record / 'experiment-config.json'
    c.write(config, dict(scope=scope, execution_interface=interface))
    c.write(accepted, dict(call_id='original-wake', scope=scope, interface=interface, native_attempt=dict(number=0)))
    c.write(observed, dict(accepted=c.file_ref(accepted), outputs=[], proofs=[], scientific_outputs=[]))
    intent = dict(task_ref=c.record_ref(scene / 'jobs/002.json'),
        execution_wake=dict(observations=[c.file_ref(observed), c.file_ref(observed)]))
    c.write(record / 'native-intent.json', intent)
    accepted_budget.accept_native(scene, 2, record, intent)
    counts = _reads(monkeypatch, {accepted, observed, config})
    facts = views.load_source_facts(scene, job, work, native=dict(record=record, intent=intent))
    assert counts == Counter({accepted: 1, observed: 1, config: 1})
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert views.wake_mappings_from_facts(facts) == []
    c.write(config, dict(scope=dict(scope, step=99), execution_interface=interface))
    with pytest.raises(UnsupportedExecution, match='accepted owner'):
        views.load_source_facts(scene, job, work, native=dict(record=record, intent=intent))


def test_author_manifest_is_loaded_once_and_pure_validation_keeps_its_original_facts(scene, monkeypatch):
    finish(scene, 1, 'H', [], action='wait')
    job = c.build_job(scene, 2, 'W', [1])
    record = scene / 'host-recovery/author-context/002/001'
    record.mkdir(parents=True)
    proof = author_closeout.science_context(scene, job, record)
    manifest = scene / proof['path']
    counts = _reads(monkeypatch, {manifest})
    facts = author_closeout.load_science_context_facts(scene, job, proof)
    assert counts == Counter({manifest: 1})
    native_record = execution.origin(scene, 2)
    intent = dict(task_ref=c.record_ref(scene / 'jobs/002.json'), author_context_version=1,
        author_closeout_science_context=proof)
    sources = views.load_source_facts(scene, job, c.workspace(scene, 2),
        native=dict(record=native_record, intent=intent), input_policy=facts['input_policy'],
        author_facts=facts)
    assert sources['author'] is facts
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert views.logical_original_from_facts(sources,
            c.workspace(scene, 2) / 'science-task.json') == facts['directory'] / 'science-task.json'
    with pytest.raises(UnsupportedExecution, match='another exact context'):
        views.load_source_facts(scene, job, c.workspace(scene, 2),
            native=dict(record=native_record, intent=intent), input_policy=facts['input_policy'],
            author_facts=None)
    assert counts == Counter({manifest: 1})
    task = next(row for row in facts['entries'] if row['item']['kind'] == 'task')
    original = task['display'].read_bytes()
    task['display'].chmod(0o600)
    task['display'].write_bytes(b'changed author display')
    with monkeypatch.context() as patch:
        _no_reads(patch)
        assert author_closeout.validate_science_context_from_facts(facts) == facts['directory']
    with pytest.raises(ValueError, match='original changed|differs'):
        author_closeout.load_science_context_facts(scene, job, proof)
    task['display'].write_bytes(original)
    task['display'].chmod(0o400)


@pytest.mark.parametrize('operation', ['create', 'load'])
def test_author_producer_reads_its_direct_parent_originals_once(scene, monkeypatch, operation):
    _, _, parent = finish(scene, 1, 'E', [], action='wait',
        files={'config.json': '{"value":1}\n', 'impl.py': 'def value(): return 1\n'})
    job = c.build_job(scene, 2, 'E', [1])
    opaque={Path(row['source']) for row in job['direct_materials']}
    assert len(opaque)==2
    record = scene / 'host-recovery/author-context/002/001'
    record.mkdir(parents=True)
    manifest = record / 'author-science-context.json'
    if operation == 'load':proof = author_closeout.science_context(scene, job, record)
    tracked = {scene / 'jobs/001.json', parent / 'result-identity.json',
        parent / 'science-output.json', manifest, scene / 'settings.json',
        scene / 'settings-identity.json', scene / 'host-history/accepted-budget/head.json',
        c.workspace(scene, 2) / 'science-task.json', *opaque}
    readers, json_reads = {}, Counter()
    counts = _reads(monkeypatch, tracked, readers=readers, json_reads=json_reads, json_root=scene)
    def unrequested(*args, **kwargs):
        pytest.fail('An author source-record load entered an unused graph or history audit')
    with monkeypatch.context() as patch:
        patch.setattr(lineage.search, 'fold', unrequested)
        patch.setattr(lineage, 'build_from_source_facts', unrequested)
        if operation == 'create':
            proof = author_closeout.science_context(scene, job, record)
        else:
            facts = author_closeout.load_science_context_facts(scene, job, proof)
    details = '\n'.join(f'{path.relative_to(scene)}: {counts[path]}\n  '
        + '\n  '.join(readers.get(path, [])) for path in sorted(tracked))
    details += '\nAll instance JSON reads:\n' + '\n'.join(
        f'{path.relative_to(scene)}: {count}' for path, count in sorted(json_reads.items()))
    expected = Counter({path: 0 if path in opaque or operation == 'create' and path == manifest else 1
        for path in tracked})
    assert counts == expected, details
    assert all(count == 1 for count in json_reads.values()), details
    assert scene / proof['path'] == manifest
    if operation == 'create':return
    source_rows = [source['control'] for source in facts['sources'].values()]
    with monkeypatch.context() as patch:
        _no_reads(patch)
        patch.setattr(lineage.search, 'fold', unrequested)
        graph = lineage.build_from_source_facts(scene, job['step'], sources=source_rows,
            source_facts=facts['sources'], search_state=dict(mechanisms={}))
        assert [row['step'] for row in graph['nodes']] == [1]
