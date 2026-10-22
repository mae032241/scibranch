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
    def prepare(plan, request, transaction):
        root = transaction / 'child'; root.mkdir()
        c.write(root / 'settings.json', dict(goal_id='child-new-identity'))
        c.write(root / 'pause.request', dict(reason='fork_created_paused'))
        c.write(root / 'host-runtime/fork/manifest.json', dict(kind='test-transaction-only'))
        return root, dict(goal_id='child-new-identity')
    monkeypatch.setattr(service, '_prepare', prepare)
    return parent, child, plan


def create(parent, child, operation='create-once', **options):
    preview = service.preview(parent, 200, child, budget=BUDGET, **options)
    return service.create(parent, 200, child, budget=BUDGET, expected_preview_id=preview['preview_id'],
                          operation_id=operation, **options)


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
    with pytest.raises(service.ForkError, match='different request'):
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


def test_receipt_exists_before_atomic_publication_and_failure_leaves_no_target(transaction, monkeypatch):
    from scidiscovery.research_loop import instance_archive
    parent, child, _ = transaction
    def fail(stage, output):
        assert c.read(stage / 'host-runtime/fork/receipt.json')['state'] == 'paused'
        assert (stage / 'pause.request').exists()
        assert not output.exists()
        raise OSError('injected publication failure')
    monkeypatch.setattr(instance_archive, '_publish', fail)
    with pytest.raises(OSError, match='injected'):
        create(parent, child)
    assert not child.exists()
    assert not [path for path in child.parent.iterdir() if path.is_dir() and path.name.startswith('.scid-fork-') and path.name != '.scid-fork-operations']


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
        source_restrictions=[dict(source=str(objective), sha256=c.digest(objective), restriction='clear')])
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
    assert sources['globals'][0]['origin']['sha256'] == c.digest(child / 'project/global/objective.md')
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
        source_restrictions=[dict(source=str(path), sha256=c.digest(path), restriction=restriction)
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
        assert c.digest(child / material['source']) == material['sha256']


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
        c.write(parent / 'settings-identity.json', dict(sha256=c.digest(parent / 'settings.json')))
    with pytest.raises(service.ForkError) as error:
        service.preview(parent, 2, tmp_path / 'branch', budget=BUDGET)
    assert error.value.code == code


def test_typed_source_file_absence_retains_missing_input_code(tmp_path):
    with pytest.raises(service.ForkError) as error:
        service._source_call(service.files.hash_file, tmp_path / 'missing-original')
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
        experiment_mode='external', search_policy=None, role_guidance=role_guidance)
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
