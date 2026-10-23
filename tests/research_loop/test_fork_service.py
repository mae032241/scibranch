"""Fork transaction behavior; retained scientific qualification is tested separately."""
import shutil
from contextlib import nullcontext

import pytest

from scidiscovery.research_loop import contracts as c, fork_service as service


BUDGET = dict(max_tokens=200, max_seconds=30, max_steps=2)


@pytest.fixture
def transaction(tmp_path, monkeypatch):
    parent = tmp_path / 'parent'; parent.mkdir()
    child = tmp_path / 'child'
    plan = dict(source=dict(step=200, intent_kind='normal'), entries=[],
                prefix_entries=[], cut={}, selected_settings={}, blocked_reasons=[])
    monkeypatch.setattr(service.fork_sources, 'inspect_source', lambda *_: plan)
    monkeypatch.setattr(service.fork_sources, 'validate_manifest', lambda *_, **__: {})
    monkeypatch.setattr(service.fork_sources, 'source_guard', lambda *_: nullcontext())
    def prepare(plan, request, transaction, *, _on_created=None):
        root = transaction / 'child'; root.mkdir()
        if _on_created is not None:
            _on_created(root)
        c.write(root / 'settings.json', dict(goal_id='child-' + service.uuid.uuid4().hex))
        c.write(root / 'settings-identity.json', dict(path='settings.json'))
        c.write(root / 'pause.request', dict(reason='fork_created_paused'))
        c.write(root / 'host-runtime/fork/manifest.json', dict(kind='test-transaction-only'))
        return root, c.settings(root)
    monkeypatch.setattr(service, '_prepare', prepare)
    return parent, child, plan


def create(parent, child, operation='create-once', **options):
    preview = service.preview(parent, 200, child, budget=BUDGET, **options)
    return service.create(parent, 200, child, budget=BUDGET, expected_preview_id=preview['preview_id'],
                          operation_id=operation, **options)


def operation_record(child, operation):
    return c.read(child.parent / '.scid-fork-operations' / (service._digest(operation) + '.json'))


def test_preview_is_read_only_and_guidance_preserves_original(transaction):
    parent, child, _ = transaction
    text = '  Exact original guidance.\n\n'
    preview = service.preview(parent, 200, child, budget=BUDGET, guidance_text=text)
    assert preview['guidance']['text'] == text
    assert not child.exists()
    assert list(parent.iterdir()) == []
    assert not (child.parent / '.scid-fork-operations').exists()


def test_publish_is_paused_durable_and_idempotent_after_parent_disappears(transaction, monkeypatch):
    parent, child, _ = transaction
    expected = service.preview(parent, 200, child, budget=BUDGET)['preview_id']
    receipt = service.create(parent, 200, child, budget=BUDGET, expected_preview_id=expected, operation_id='one')
    assert receipt['state'] == 'paused' and (child / 'pause.request').is_file()
    assert not (child / 'native-evidence').exists()
    assert c.read(child / 'host-runtime/fork/receipt.json') == receipt
    shutil.rmtree(parent)
    monkeypatch.setattr(service.fork_sources, 'inspect_source', lambda *_: pytest.fail('Published retry reread parent'))
    assert service.create(parent, 200, child, budget=BUDGET, expected_preview_id=expected, operation_id='one') == receipt
    with pytest.raises(service.ForkError, match='another request'):
        service.create(parent, 200, child, budget=dict(BUDGET, max_tokens=201), expected_preview_id=expected, operation_id='one')


def test_operation_id_conflicts_across_sibling_targets(transaction):
    parent, child, _ = transaction
    create(parent, child, 'one')
    other = child.with_name('other')
    with pytest.raises(service.ForkError, match='another request'):
        create(parent, other, 'one')
    assert not other.exists()


def test_stale_preview_and_unknown_effect_never_publish(transaction):
    parent, child, plan = transaction
    expected = service.preview(parent, 200, child, budget=BUDGET)['preview_id']
    plan['blocked_reasons'] = [dict(code='fork_external_effect_unknown', path='original-call')]
    with pytest.raises(service.ForkError) as error:
        service.create(parent, 200, child, budget=BUDGET, expected_preview_id=expected, operation_id='one')
    assert error.value.code == 'fork_preview_stale'
    assert service.preview(parent, 200, child, budget=BUDGET)['status'] == 'fork_preview_blocked'
    assert not child.exists()


def test_publication_failure_retains_exact_prepared_child_for_retry(transaction, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    publish = instance_archive._publish
    def fail(stage, output):
        assert c.read(stage / 'host-runtime/fork/receipt.json')['state'] == 'paused'
        assert (stage / 'pause.request').exists()
        assert not output.exists()
        raise OSError('injected publication failure')
    monkeypatch.setattr(instance_archive, '_publish', fail)
    with pytest.raises(OSError, match='injected'):
        create(parent, child)
    assert not child.exists()
    record = operation_record(child, 'create-once')
    assert record['phase'] == 'prepared'
    stage = service.Path(record['transaction_path']) / 'child'
    assert c.read(stage / 'host-runtime/fork/receipt.json') == record['receipt']
    monkeypatch.setattr(instance_archive, '_publish', publish)
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Prepared retry created another identity'))
    assert create(parent, child) == record['receipt']
    assert not stage.parent.exists()


def test_lost_response_after_publish_returns_original_identity_on_retry(transaction, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    publish = instance_archive._publish
    def lose_response(stage, target):
        publish(stage, target)
        raise OSError('lost published response')
    monkeypatch.setattr(instance_archive, '_publish', lose_response)
    with pytest.raises(OSError, match='lost published'):
        create(parent, child)
    receipt = c.read(child / 'host-runtime/fork/receipt.json')
    assert create(parent, child) == receipt


@pytest.mark.process_e2e
def test_second_process_cannot_publish_while_target_creator_owns_lock(transaction):
    import multiprocessing
    parent, child, _ = transaction
    context = multiprocessing.get_context('fork')
    ready, release = context.Event(), context.Event()
    def owner():
        with service._target_lock(child):
            ready.set()
            release.wait(10)
    process = context.Process(target=owner)
    process.start()
    try:
        assert ready.wait(5)
        with pytest.raises(service.ForkError) as error:
            create(parent, child)
        assert error.value.code == 'fork_busy'
        assert not child.exists()
    finally:
        release.set()
        process.join(5)
        if process.is_alive():
            process.terminate(); process.join()


@pytest.mark.process_e2e
def test_killed_creator_before_publication_can_retry_without_half_instance(transaction):
    import multiprocessing
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    context = multiprocessing.get_context('fork')
    ready = context.Event()
    def creator():
        def before_publish(stage, target):
            assert (stage / 'host-runtime/fork/receipt.json').is_file()
            ready.set()
            context.Event().wait(30)
        instance_archive._publish = before_publish
        create(parent, child, 'killed-once')
    process = context.Process(target=creator)
    process.start()
    try:
        assert ready.wait(5)
        assert not child.exists()
        process.terminate(); process.join(5)
        assert not child.exists()
        receipt = create(parent, child, 'killed-once')
        assert receipt['state'] == 'paused'
        assert c.read(child / 'host-runtime/fork/receipt.json') == receipt
    finally:
        if process.is_alive():
            process.terminate(); process.join()


def test_target_created_during_transaction_is_never_overwritten(transaction, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    publish = instance_archive._publish
    def competing_target(stage, target):
        target.mkdir()
        (target / 'foreign-original').write_bytes(b'preserve this exact target')
        publish(stage, target)
    monkeypatch.setattr(instance_archive, '_publish', competing_target)
    with pytest.raises(instance_archive.files.ArchiveError, match='already exists'):
        create(parent, child)
    assert (child / 'foreign-original').read_bytes() == b'preserve this exact target'
    assert not (child / 'settings.json').exists()


@pytest.mark.parametrize('budget', [dict(BUDGET, max_steps=0), dict(BUDGET, max_tokens=True),
                                  dict(BUDGET, max_seconds=float('inf')), {}])
def test_invalid_new_allowance_rejected_before_source_read(transaction, monkeypatch, budget):
    parent, child, _ = transaction
    monkeypatch.setattr(service.fork_sources, 'inspect_source', lambda *_: pytest.fail('Invalid allowance read source'))
    with pytest.raises(service.ForkError) as error:
        service.preview(parent, 200, child, budget=budget)
    assert error.value.code == 'fork_budget_incomplete'


def test_real_source_create_preserves_permissions_and_new_ledgers(tmp_path):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import fork_sources, budget
    parent = completed_source(tmp_path)
    child = tmp_path / 'branch'
    original = c.settings(parent)
    preview = service.preview(parent, 2, child, budget=BUDGET, guidance_text='  Original advice\n')
    receipt = service.create(parent, 2, child, budget=BUDGET, guidance_text='  Original advice\n',
        expected_preview_id=preview['preview_id'], operation_id='real-create')
    settings = c.settings(child)
    assert receipt['child_identity'] != original['goal_id']
    assert settings.get('native_options') == original.get('native_options')
    assert settings['public_root'] == str(child / 'host-runtime/fork/public')
    assert budget.used_steps(child) == 0 and not (child / 'jobs').exists()
    assert not (child / 'host-started.json').exists()
    assert not (child / 'native-evidence').exists()
    assert fork_sources.selected_task(child)['job']['step'] == 2
    assert [c.read(path)['text'] for path in (child / 'host-control/guidance').glob('*.json')] == ['  Original advice\n']
    shutil.rmtree(parent)
    fork_sources.validate_manifest(child, full=True)


def test_file_objective_over_inline_limit_survives_fork(tmp_path, monkeypatch):
    from test_history_handoff import finish
    from scidiscovery.research_loop import controller, fork_sources, qualification
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'
    text = 'A' * (300 * 1024 - 1) + '\n'
    objective.write_text(text, encoding='utf-8')
    original = objective.read_bytes()
    options = dict(max_tokens=1000, max_seconds=60, max_steps=3,
        model='fixture', reasoning_effort='high', experiment_mode='external', search_policy=None)
    for previous in (None, dict(objective='A different frozen objective.')):
        with pytest.raises(ValueError, match='256000-byte limit'):
            controller.prepare(tmp_path / 'inline', objective, public, ['fixture-no-execution'],
                objective_text=text, validate_only=True, _existing_settings=previous, **options)
    assert not (tmp_path / 'inline').exists()
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, ['fixture-no-execution'], **options,
        source_restrictions=[dict(source=str(objective),  restriction='clear')])
    write = c.write

    def write_synthetic_result(path, value):
        if path.name == 'native-outcome.json':
            value = dict(value, native_started=False)
        return write(path, value)

    # Declare the helper's lack of a native process before sealing its results.
    with monkeypatch.context() as patch:
        patch.setattr(c, 'write', write_synthetic_result)
        finish(parent, 1, 'H', [], action='analyze')
        finish(parent, 2, 'A', [1], action='finish')
    with controller._writer(parent):
        pass
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    assert preview['status'] == 'fork_preview_ready'
    receipt = service.create(parent, 2, child, budget=BUDGET,
        expected_preview_id=preview['preview_id'], operation_id='large-file-objective')
    settings = c.settings(child)
    assert receipt['state'] == 'paused' and (child / 'pause.request').exists()
    assert settings['objective'] == text
    assert (child / 'project/global/objective.md').read_bytes() == original
    sources = qualification._sources(child)
    assert sources['globals'][0]['origin']['path'] == str(child / 'project/global/objective.md')
    assert Path(sources['globals'][0]['origin']['path']).read_bytes() == (child / 'project/global/objective.md').read_bytes()
    assert any(row['origin']['path'] == str(child / 'project/global/objective.md')
               and row['restriction'] == 'clear' for row in sources['declarations'])
    assert b'.scid-fork-' not in (child / settings['source_provenance']['path']).read_bytes()
    shutil.rmtree(parent)
    shutil.rmtree(public)
    fork_sources.validate_manifest(child, full=True)
    assert (child / 'project/global/objective.md').read_bytes() == original


def test_created_child_archives_private_origins_without_public_science_leak(tmp_path):
    import json
    from test_fork_source import completed_source
    from scidiscovery.research_loop import instance_archive as archive, instance_display, instance_control
    parent = completed_source(tmp_path)
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    service.create(parent, 2, child, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='archive-one')
    shutil.rmtree(parent)
    status = instance_control.status(child)
    assert [(row['step'], row.get('inherited')) for row in status['nodes']] == [(1, True)]
    assert status['used_steps'] == 0
    with instance_display.science(child, 1) as original:
        scientific_bytes = original.stream.read()
    assert b'host-runtime/fork' not in scientific_bytes
    assert str(parent).encode() not in scientific_bytes
    package = tmp_path / 'archive'
    archive.archive_export(child, package)
    rows = [json.loads(line) for line in (package / 'manifest.jsonl').read_bytes().splitlines()]
    by_path = {row['path']: row for row in rows}
    assert by_path['host-runtime/fork/manifest.json']['kind'] == 'file'
    assert by_path['host-runtime/fork/origins.json']['kind'] == 'file'
    assert any(path.startswith('host-runtime/fork/inherited/') and row['kind'] == 'file' for path, row in by_path.items())
    assert archive.archive_inspect(package)['status'] == 'archive_inspected'


def test_selected_global_updates_and_restrictions_survive_child_birth(tmp_path):
    from test_history_handoff import finish
    from scidiscovery.research_loop import controller, qualification
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Exact original objective.\n')
    update = public / 'update.md'; update.write_text('  Restricted original update.\n\n')
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, ['fixture-no-execution'],
        max_tokens=1000, max_seconds=60, max_steps=3, model='fixture', reasoning_effort='high',
        experiment_mode='external', search_policy=None, user_updates=[update],
        source_restrictions=[dict(source=str(path),  restriction=restriction)
            for path, restriction in ((objective, 'clear'), (update, 'restricted'))])
    for step, role, parents, action in ((1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')):
        _, _, record = finish(parent, step, role, parents, action=action)
        c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'), native_started=False))
    with controller._writer(parent):
        pass
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    service.create(parent, 2, child, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='globals-one')
    settings = c.settings(child)
    assert [bytes((child / row['source']).read_bytes()) for row in settings['global_materials']] == [objective.read_bytes(), update.read_bytes()]
    declarations = qualification._sources(child)
    by_path = {row['origin']['path']: row['restriction'] for row in declarations['declarations']}
    assert by_path[str(child / 'project/global/objective.md')] == 'clear'
    assert by_path[str(child / 'project/global/updates/001.md')] == 'restricted'
    assert by_path[str(child / 'host-runtime/fork/public/update.md')] == 'restricted'
    assert b'.scid-fork-' not in (child / settings['source_provenance']['path']).read_bytes()
    shutil.rmtree(parent)
    for material in settings['global_materials']:
        assert (child / material['source']).stat().st_size == material['size_bytes']


@pytest.mark.parametrize('mutation,code', [('invalid_sealed_input', 'fork_origin_invalid'), ('unsupported', 'fork_source_unsupported')])
def test_shared_source_validation_rejections_have_stable_codes(tmp_path, mutation, code):
    from test_fork_source import completed_source
    parent = completed_source(tmp_path)
    if mutation == 'invalid_sealed_input':
        # A removed sealed task input invalidates the shared original contract.
        # Its regular-file validator intentionally uses the same ValueError for
        # absence, links and wrong file types; it does not report typed ENOENT.
        (parent / 'project/nodes/002/inputs/parents/001/conclusion.json').unlink()
    else:
        settings = c.settings(parent)
        settings['handoff_protocol'] = 22
        c.write(parent / 'settings.json', settings)
        c.write(parent / 'settings-identity.json', dict(path='settings.json'))
    with pytest.raises(service.ForkError) as error:
        service.preview(parent, 2, tmp_path / 'branch', budget=BUDGET)
    assert error.value.code == code


def test_typed_source_file_absence_retains_missing_input_code(tmp_path):
    with pytest.raises(service.ForkError) as error:
        service._source_call(service.files.stat_file, tmp_path / 'missing-original')
    assert error.value.code == 'fork_input_missing'
    assert isinstance(error.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize('role_guidance', [None, {role: 'Exact custom ' + role + ' guidance.\n' for role in 'HWAE'}])
def test_fork_preserves_disabled_search_and_literal_role_guidance_for_successors(tmp_path, role_guidance):
    import sys
    from test_history_handoff import finish
    from scidiscovery.research_loop import controller
    public = tmp_path / 'public'; public.mkdir()
    objective = public / 'objective.md'; objective.write_text('Preserve exact scientific configuration.')
    parent = tmp_path / 'parent'
    controller.prepare(parent, objective, public, [sys.executable, '-c', 'raise SystemExit(99)'],
        max_tokens=1000, max_seconds=60, max_steps=4, model='fixture', reasoning_effort='high',
        experiment_mode='external', search_policy=None, role_guidance=role_guidance,
        storage_policy=dict(version=1, enabled=True, max_file_bytes=10 * 1024 * 1024))
    for step, role, parents, action in ((1, 'H', [], 'analyze'), (2, 'A', [1], 'finish')):
        _, _, record = finish(parent, step, role, parents, action=action)
        c.write(record / 'native-outcome.json', dict(c.read(record / 'native-outcome.json'), native_started=False))
    with controller._writer(parent):
        pass
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    service.create(parent, 2, child, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='scientific-config')
    settings = c.settings(child)
    assert settings.get('search_policy') is None
    assert settings.get('role_guidance') == role_guidance
    assert settings['native_launcher_argv'] == c.settings(parent)['native_launcher_argv']
    assert settings['storage_policy'] == c.settings(parent)['storage_policy']
    # Compile a later ordinary task without launching a participant. Its
    # scientific contract must retain the source's disabled/custom semantics.
    job = c.build_job(child, 3, 'H', [])
    assert 'search' not in job
    assert job.get('role_guidance') == (role_guidance['H'] if role_guidance else None)


def test_source_writer_guard_is_held_through_final_sync_and_publication(tmp_path, monkeypatch):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import controller, instance_archive
    parent = completed_source(tmp_path)
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    synchronize = service._sync_tree
    publish = instance_archive._publish
    checked = []
    def probe(phase):
        with pytest.raises(RuntimeError, match='Source read guard cannot be entered as an effect lease'):
            with controller._writer(parent):
                pytest.fail('Source writer entered between final inspection and publication')
        checked.append(phase)
    def while_guarded(stage):
        probe('sync')
        synchronize(stage)
    def publish_guarded(stage, target):
        probe('publish')
        publish(stage, target)
    monkeypatch.setattr(service, '_sync_tree', while_guarded)
    monkeypatch.setattr(instance_archive, '_publish', publish_guarded)
    service.create(parent, 2, child, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='guarded-publish')
    assert checked == ['sync', 'publish'] and child.exists()
    with controller._writer(parent):
        pass


def test_missing_existing_source_guard_blocks_without_creating_parent_lock(tmp_path):
    from test_fork_source import completed_source
    parent = completed_source(tmp_path)
    (parent / 'host-run.lock').unlink()
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    assert preview['status'] == 'fork_preview_blocked'
    assert any(row['code'] == 'source_guard_unavailable' for row in preview['blockers'])
    with pytest.raises(service.ForkError) as error:
        service.create(parent, 2, child, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='missing-guard')
    assert error.value.code == 'fork_source_guard_unavailable'
    assert not child.exists() and not (parent / 'host-run.lock').exists()


@pytest.mark.parametrize('disposition', ['delete', 'move'])
def test_published_operation_never_recreates_missing_child(transaction, monkeypatch, disposition):
    parent, child, _ = transaction
    expected = service.preview(parent, 200, child, budget=BUDGET)['preview_id']
    receipt = create(parent, child)
    assert operation_record(child, 'create-once')['phase'] == 'published'
    if disposition == 'delete':
        shutil.rmtree(child)
    else:
        child.rename(child.with_name('moved-child'))
    shutil.rmtree(parent)
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Completed operation created another child'))
    monkeypatch.setattr(service.fork_sources, 'inspect_source', lambda *_: pytest.fail('Receipt retry reread source'))
    assert service.create(parent, 200, child, budget=BUDGET, expected_preview_id=expected,
                          operation_id='create-once') == receipt
    assert not child.exists()


@pytest.mark.parametrize('disposition', ['delete', 'move'])
def test_lost_publish_and_missing_child_remain_unknown_with_original_identity(transaction, monkeypatch, disposition):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    publish = instance_archive._publish
    def publish_without_ack(stage, target):
        publish(stage, target)
        raise OSError('lost publish acknowledgement')
    monkeypatch.setattr(instance_archive, '_publish', publish_without_ack)
    with pytest.raises(OSError, match='lost publish'):
        create(parent, child)
    record = operation_record(child, 'create-once')
    assert record['phase'] == 'prepared'
    if disposition == 'delete':
        shutil.rmtree(child)
    else:
        child.rename(child.with_name('moved-child'))
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Unknown publication recreated child'))
    monkeypatch.setattr(service.fork_sources, 'inspect_source', lambda *_: pytest.fail('Unknown publication read source'))
    with pytest.raises(service.ForkError) as error:
        service.create(parent, 200, child, budget=BUDGET,
                       expected_preview_id=record['receipt']['preview_id'], operation_id='create-once')
    assert error.value.code == 'fork_publication_unknown'
    assert error.value.receipt == record['receipt']
    assert record['receipt']['child_identity'] in str(error.value)
    assert operation_record(child, 'create-once') == record
    assert not child.exists()


def test_prepared_receipt_reconciliation_confirms_publish_before_later_delete(transaction, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    publish = instance_archive._publish
    def lose_response(stage, target):
        publish(stage, target)
        raise OSError('lost response')
    monkeypatch.setattr(instance_archive, '_publish', lose_response)
    with pytest.raises(OSError, match='lost response'):
        create(parent, child)
    receipt = operation_record(child, 'create-once')['receipt']
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Published child was rebuilt'))
    assert create(parent, child) == receipt
    assert operation_record(child, 'create-once')['phase'] == 'published'
    shutil.rmtree(child)
    assert create(parent, child) == receipt
    assert not child.exists()


def test_published_record_preserves_existing_child_integrity_checks(transaction):
    parent, child, _ = transaction
    create(parent, child)
    c.write(child / 'settings.json', dict(goal_id='a-different-child'))
    c.write(child / 'settings-identity.json', dict(path='settings.json'))
    with pytest.raises(service.ForkError, match='receipt identity'):
        create(parent, child)
    assert c.read(child / 'settings.json')['goal_id'] == 'a-different-child'


@pytest.mark.process_e2e
def test_killed_staging_reclaims_only_registered_links_and_preserves_active_other_operation(transaction):
    import multiprocessing
    import os
    parent, child, _ = transaction
    original = parent / 'immutable.bin'; original.write_bytes(b'one shared original'); original.chmod(0o400)
    context = multiprocessing.get_context('fork')
    first_ready, second_ready = context.Event(), context.Event()
    prepare = service._prepare
    def creator(target, operation, ready):
        def retain(plan, request, directory, **options):
            retained = directory / 'origins'; retained.mkdir()
            os.link(original, retained / 'immutable.bin')
            return prepare(plan, request, directory, **options)
        service._prepare = retain
        def stop_before_prepared(stage):
            ready.set()
            context.Event().wait(30)
        service._sync_tree = stop_before_prepared
        create(parent, target, operation)
    other = child.with_name('other-child')
    first = context.Process(target=creator, args=(child, 'killed-staging', first_ready))
    second = context.Process(target=creator, args=(other, 'active-staging', second_ready))
    first.start(); second.start()
    try:
        assert first_ready.wait(5) and second_ready.wait(5)
        first_record = operation_record(child, 'killed-staging')
        second_record = operation_record(other, 'active-staging')
        assert first_record['phase'] == second_record['phase'] == 'staging'
        assert original.stat().st_nlink == 3
        first.terminate(); first.join(5)
        receipt = create(parent, child, 'killed-staging')
        assert receipt['state'] == 'paused'
        assert not service.Path(first_record['transaction_path']).exists()
        assert service.Path(second_record['transaction_path']).is_dir()
        assert original.stat().st_nlink == 2
        with pytest.raises(service.ForkError) as error:
            create(parent, other, 'active-staging')
        assert error.value.code == 'fork_busy'
        assert original.read_bytes() == b'one shared original' and original.stat().st_mode & 0o777 == 0o400
    finally:
        for process in (first, second):
            if process.is_alive():
                process.terminate()
            process.join(5)


def test_unregistered_transaction_and_replaced_registered_inode_are_preserved(transaction, monkeypatch):
    parent, child, _ = transaction
    directory = child.parent / '.scid-fork-operations'
    directory.mkdir(mode=0o700)
    orphan = directory / (service._digest('create-once') + '.stage')
    orphan.mkdir(); (orphan / 'original.bin').write_bytes(b'unregistered; preserve')
    with pytest.raises(service.ForkError, match='Unregistered'):
        create(parent, child)
    assert (orphan / 'original.bin').read_bytes() == b'unregistered; preserve'
    orphan.rename(child.parent / 'orphan-preserved')
    prepare = service._prepare
    clean = service._clean_transaction
    def fail_after_prepare(plan, request, transaction, **options):
        prepare(plan, request, transaction, **options)
        raise OSError('interrupted preparation')
    with monkeypatch.context() as patch:
        patch.setattr(service, '_prepare', fail_after_prepare)
        patch.setattr(service, '_clean_transaction', lambda *_: None)
        with pytest.raises(OSError, match='interrupted'):
            create(parent, child)
    record = operation_record(child, 'create-once')
    transaction_path = service.Path(record['transaction_path'])
    transaction_path.rename(child.parent / 'registered-original-preserved')
    transaction_path.mkdir(); (transaction_path / 'foreign.bin').write_bytes(b'replacement; preserve')
    with pytest.raises(service.ForkError, match='registered directory identity'):
        create(parent, child)
    assert (transaction_path / 'foreign.bin').read_bytes() == b'replacement; preserve'
    assert service._clean_transaction is clean and not child.exists()


def test_real_published_delete_never_recreates_child(tmp_path, monkeypatch):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import instance_delete
    parent = completed_source(tmp_path); child = tmp_path / 'branch'
    expected = service.preview(parent, 2, child, budget=BUDGET)['preview_id']
    receipt = service.create(parent, 2, child, budget=BUDGET,
        expected_preview_id=expected, operation_id='published-deleted')
    instance_delete.delete_instance(child, instance_id=receipt['child_identity'],
        settings_ref=c.record_ref(child / 'settings.json'), operation_id='delete-child')
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Deleted child was recreated'))
    assert service.create(parent, 2, child, budget=BUDGET,
        expected_preview_id=expected, operation_id='published-deleted') == receipt
    assert not child.exists()


def test_cleanup_failure_never_revokes_published_receipt(transaction, monkeypatch):
    parent, child, _ = transaction
    def unavailable(*_):
        raise OSError('disposable cleanup unavailable')
    monkeypatch.setattr(service, '_clean_transaction', unavailable)
    receipt = create(parent, child)
    assert operation_record(child, 'create-once')['phase'] == 'published'
    shutil.rmtree(child)
    monkeypatch.setattr(service, '_prepare', lambda *_: pytest.fail('Cleanup failure recreated published child'))
    assert create(parent, child) == receipt
    assert not child.exists()


def test_initialization_failure_after_settings_reclaims_registered_stage_and_original_links(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from test_fork_source import completed_source
    from scidiscovery.research_loop import storage_policy as storage
    parent = completed_source(tmp_path)
    sibling = tmp_path / 'sibling'
    preview = service.preview(parent, 2, sibling, budget=BUDGET)
    service.create(parent, 2, sibling, budget=BUDGET, expected_preview_id=preview['preview_id'], operation_id='sibling')
    manifest = service.fork_sources.validate_manifest(sibling, full=True)
    retained = sibling / 'host-runtime/fork' / manifest['entries'][0]['storage_path']
    proof = (retained.read_bytes(), retained.stat().st_mode, retained.stat().st_nlink)
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    mutation = storage.mutation
    registered = []
    @contextmanager
    def reject_initialized(root, growth, **options):
        if root is not None and root.name == 'child' and root.parent.name.endswith('.stage'):
            assert (root / 'settings.json').is_file() and (root / 'settings-identity.json').is_file()
            record = operation_record(child, 'failed-initialization')
            assert record['phase'] == 'staging'
            assert record['stage_identity'] == service._directory_identity(root)
            registered.append(record['stage_identity'])
            raise storage.StorageQuotaExceeded('injected initialization quota')
        with mutation(root, growth, **options):
            yield
    with monkeypatch.context() as patch:
        patch.setattr(storage, 'mutation', reject_initialized)
        with pytest.raises(storage.StorageQuotaExceeded, match='injected initialization quota'):
            service.create(parent, 2, child, budget=BUDGET,
                expected_preview_id=preview['preview_id'], operation_id='failed-initialization')
    record = operation_record(child, 'failed-initialization')
    assert registered and record['phase'] == 'reserved'
    assert not service.Path(record['transaction_path']).exists() and not child.exists()
    assert (retained.read_bytes(), retained.stat().st_mode, retained.stat().st_nlink) == proof
    service.fork_sources.validate_manifest(sibling, full=True)
    receipt = service.create(parent, 2, child, budget=BUDGET,
        expected_preview_id=preview['preview_id'], operation_id='failed-initialization')
    assert receipt['state'] == 'paused'


def test_prepared_record_follows_completed_tree_and_stage_parent_sync(transaction, monkeypatch):
    parent, child, _ = transaction
    events = []
    synchronize, fsync, durable = service._sync_tree, service.files.fsync_directory, service.files.durable_json
    def sync_tree(stage):
        synchronize(stage)
        events.append(('tree', stage))
    def sync_directory(directory):
        fsync(directory)
        events.append(('directory', directory))
    def write_record(path, value):
        if value.get('phase') == 'prepared':
            directory = service.Path(value['transaction_path'])
            assert events[-2:] == [('tree', directory / 'child'), ('directory', directory)]
            events.append(('prepared', path))
        durable(path, value)
    monkeypatch.setattr(service, '_sync_tree', sync_tree)
    monkeypatch.setattr(service.files, 'fsync_directory', sync_directory)
    monkeypatch.setattr(service.files, 'durable_json', write_record)
    create(parent, child)
    assert any(event[0] == 'prepared' for event in events)


def test_source_directory_sync_failure_recovers_original_published_child(transaction, monkeypatch):
    parent, child, _ = transaction
    synchronize = service.files.fsync_directory
    failures, recovered = [], []
    def sync_directory(directory):
        if directory.name.endswith('.stage') and child.exists():
            record = operation_record(child, 'create-once')
            assert record['phase'] == 'prepared'
            if not failures:
                failures.append(service._directory_identity(child))
                raise OSError('injected source directory sync failure')
            recovered.append(directory)
        synchronize(directory)
    monkeypatch.setattr(service.files, 'fsync_directory', sync_directory)
    with pytest.raises(OSError, match='source directory sync failure'):
        create(parent, child)
    record = operation_record(child, 'create-once')
    assert record['phase'] == 'prepared' and failures == [record['stage_identity']]
    monkeypatch.setattr(service, '_prepare', lambda *_, **__: pytest.fail('Rename recovery rebuilt a child'))
    assert create(parent, child) == record['receipt']
    assert recovered == [service.Path(record['transaction_path'])]
    assert service._directory_identity(child) == record['stage_identity']
    assert operation_record(child, 'create-once')['phase'] == 'published'


@pytest.mark.parametrize('phase', ['published', 'prepared'])
def test_existing_child_retry_rejects_corrupt_retained_original(tmp_path, monkeypatch, phase):
    from test_fork_source import completed_source
    from scidiscovery.research_loop import instance_archive
    parent = completed_source(tmp_path)
    child = tmp_path / 'branch'
    preview = service.preview(parent, 2, child, budget=BUDGET)
    def retry():
        return service.create(parent, 2, child, budget=BUDGET,
            expected_preview_id=preview['preview_id'], operation_id='corrupt-retained')
    if phase == 'prepared':
        publish = instance_archive._publish
        def lose_response(stage, target):
            publish(stage, target)
            raise OSError('injected lost publication response')
        with monkeypatch.context() as patch:
            patch.setattr(instance_archive, '_publish', lose_response)
            with pytest.raises(OSError, match='lost publication response'):
                retry()
    else:
        retry()
    record = operation_record(child, 'corrupt-retained')
    assert record['phase'] == phase
    manifest = service.fork_sources.validate_manifest(child, full=True)
    retained = child / 'host-runtime/fork' / manifest['entries'][0]['storage_path']
    retained.chmod(0o600)
    retained.write_bytes(b'corrupt retained original')
    retained.chmod(0o400)
    monkeypatch.setattr(service, '_prepare', lambda *_, **__: pytest.fail('Corrupt child was rebuilt'))
    with pytest.raises(service.ForkError):
        retry()
    assert operation_record(child, 'corrupt-retained') == record
    assert service._directory_identity(child) == record['stage_identity']
