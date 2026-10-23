from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
from scidiscovery.execution_security import request_digest
from scidiscovery.artifact_agent.schema.refs import ArtifactRef
from tcad_artifact.execution_control import (
    SolverCapability,
    _write_abandoned_terminal,
)
from tcad_artifact.project_packager import (
    DeckFile,
    DeckProjectDraft,
    DeckReviewReport,
    ProjectInputSlot,
    ProjectPreflightAttestation,
    ProjectResourceLimits,
    ResolvedProjectInput,
    ExecutionPackage,
    TCADRuntimeManifest,
    attest_runtime_contract,
    PackagerError,
    materialize_execution_inputs,
)


def _execution_package(resolved_input_count: int = 0) -> bytes:
    capability = SolverCapability(
        profile_id="r4b-runtime",
        solver_kind="sdevice",
        executable="/opt/fake/sdevice",
        environment={},
        release_evidence="R4-B synthetic lifecycle fixture",
        public_release_label="Synthetic SDevice",
    ).public_snapshot()
    source = "Solve {}\n"
    input_slots = tuple(
        ProjectInputSlot(
            semantic_name=f"input_{index:03d}",
            target_relative_path=f"inputs/input-{index:03d}.json",
            media_type="application/json",
        )
        for index in range(1, resolved_input_count + 1)
    )
    resolved_inputs = tuple(
        ResolvedProjectInput(
            semantic_name=slot.semantic_name,
            target_relative_path=slot.target_relative_path,
            artifact_ref=ArtifactRef(
                artifact_id=f"art_execution_input_{index:03d}",
                kind="execution_input",
                schema_id="fixture.execution-input.v1",
            ),
            media_type=slot.media_type,
            size_bytes=2,
        )
        for index, slot in enumerate(input_slots, start=1)
    )
    project = DeckProjectDraft(
        tool_profile=capability.profile_id,
        solver_kind="sdevice",
        capability_sha256=capability.capability_sha256,
        files=(DeckFile(relative_path="main.cmd", content=source),),
        input_slots=input_slots,
        entrypoint="main.cmd",
        expected_outputs=(),
        preflight_attestation=ProjectPreflightAttestation(
            terminal_state="succeeded",
            exit_code=0,
            diagnostic_layer="complete",
            qualified=True,
            summary="The synthetic direct deck passed bounded preflight.",
        ),
        resource_limits=ProjectResourceLimits(max_storage_bytes=2000000000,
            wall_time_seconds=60,
            cpu_time_seconds=60,
            max_memory_bytes=512 * 1024 * 1024,
            max_output_bytes=1024 * 1024,
        ),
    )
    review = DeckReviewReport(
        verdict="pass",
        capability_sha256=capability.capability_sha256,
        summary="The bounded lifecycle fixture is ready.",
        rationale="All declared implementation dimensions pass.",
        physical_fidelity="pass",
        implementation_fidelity="pass",
        syntax_fidelity="pass",
        numerical_protocol_fidelity="pass",
        execution_ready=True,
    )
    return canonical_json(
        ExecutionPackage(
            project=project,
            review=review,
            capability=capability,
            resolved_inputs=resolved_inputs,
        ).model_dump(mode="json")
    )


def test_abandoned_execution_manifest_remains_attestable(tmp_path: Path) -> None:
    run_directory = tmp_path / "abandoned-run"
    run_directory.mkdir()
    _write_abandoned_terminal(
        run_directory,
        terminal_state="failed",
        exit_code=98,
        error="worker_lost",
    )
    raw = (run_directory / "output_manifest.json").read_bytes()
    manifest = TCADRuntimeManifest.model_validate_json(raw, strict=True)
    assert manifest.started_at is None
    attestation = attest_runtime_contract(
        ExecutionPackage.model_validate_json(_execution_package(), strict=True),
        manifest,
    )
    assert attestation.verdict == "fail"
    assert attestation.terminal_state == "failed"


def _small_resolved_execution_package() -> ExecutionPackage:
    value = json.loads(_execution_package(1))
    return ExecutionPackage.model_validate_json(json.dumps(value), strict=True)


def test_exact_input_callbacks_preserve_first_bound_staging_slot(tmp_path: Path) -> None:
    reviewed=_small_resolved_execution_package();item=reviewed.resolved_inputs[0]
    seen=[]
    def metadata(bound):
        seen.append(bound)
        assert bound==item
        return 2,"application/json"
    def original(bound):
        assert bound==item
        return io.BytesIO(b"{}")
    paths=materialize_execution_inputs(reviewed,None,tmp_path,1,
        input_metadata=metadata,open_input=original)
    target=paths['input_001']
    assert target.read_bytes()==b"{}" and target.stat().st_mode&0o222==0
    with pytest.raises(PackagerError,match='staging slot already exists'):
        materialize_execution_inputs(reviewed,None,tmp_path,1,input_metadata=metadata,
            open_input=lambda _:pytest.fail('Existing original must not be reopened or replaced'))
    assert seen==[item,item] and target.read_bytes()==b"{}"
    target.unlink()
    original_path=tmp_path/'original.json';original_path.write_bytes(b"{}")
    target.symlink_to(original_path)
    with pytest.raises(PackagerError,match='staging slot already exists'):
        materialize_execution_inputs(reviewed,None,tmp_path,1,
            input_metadata=metadata,open_input=original)
    assert original_path.read_bytes()==b"{}"


def test_exact_input_callbacks_reject_wrong_original_and_metadata(tmp_path: Path) -> None:
    reviewed = _small_resolved_execution_package()
    target = tmp_path / "scientific-inputs" / reviewed.resolved_inputs[0].semantic_name
    for metadata in ((1, "application/json"), (2, "text/plain"), (True, "application/json")):
        with pytest.raises(PackagerError, match="metadata"):
            materialize_execution_inputs(reviewed, None, tmp_path, 1,
                input_metadata=lambda _, value=metadata: value,
                open_input=lambda _: pytest.fail("bad metadata must not open original"))
        assert not target.exists()
    for content in (b"{", b"{}x"):
        with pytest.raises(PackagerError, match="input size|input bytes"):
            materialize_execution_inputs(reviewed, None, tmp_path, 1,
                input_metadata=lambda _: (2, "application/json"),
                open_input=lambda _, value=content: io.BytesIO(value))
        assert not target.exists()  # Only this call's new partial is removed.


def test_exact_input_callbacks_preserve_quota_and_source_boundaries(tmp_path: Path) -> None:
    reviewed = _small_resolved_execution_package()
    options = dict(input_metadata=lambda _: (2, "application/json"),
        open_input=lambda _: io.BytesIO(b"{}"))
    for chunk in (0, True):
        with pytest.raises(PackagerError, match="positive integer"):
            materialize_execution_inputs(reviewed, None, tmp_path, chunk, **options)
    with pytest.raises(PackagerError, match="supplied together"):
        materialize_execution_inputs(reviewed, None, tmp_path, 1,
            input_metadata=options["input_metadata"])
    with pytest.raises(PackagerError, match="cannot be combined"):
        materialize_execution_inputs(reviewed, object(), tmp_path, 1, **options)
    with pytest.raises(PackagerError, match="require exact original"):
        materialize_execution_inputs(reviewed, None, tmp_path, 1)
    limited = reviewed.model_copy(update={"project": reviewed.project.model_copy(update={
        "resource_limits": reviewed.project.resource_limits.model_copy(update={"max_storage_bytes": 1})})})
    with pytest.raises(PackagerError, match="storage budget"):
        materialize_execution_inputs(limited, None, tmp_path, 1, **options)
    assert not (tmp_path / "scientific-inputs").exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    exchange = tmp_path / "exchange"
    exchange.mkdir()
    (exchange / "scientific-inputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PackagerError, match="regular directory"):
        materialize_execution_inputs(reviewed, None, exchange, 1, **options)
    linked_exchange = tmp_path / "linked-exchange"
    linked_exchange.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PackagerError, match="regular directory"):
        materialize_execution_inputs(reviewed, None, linked_exchange, 1, **options)
    assert list(outside.iterdir()) == []


def test_tcad_prepare_adapters_use_exact_input_callbacks(tmp_path: Path, monkeypatch) -> None:
    from tcad_artifact.command_adapter import CommandAdapterConfig, CommandTCADExecutorAdapter
    from tcad_artifact.execution_adapter import TCADExecutorAdapter
    from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor

    reviewed = _small_resolved_execution_package()
    executable = tmp_path / "unused-transport"
    executable.write_text("stand-in; never executed")
    executable.chmod(0o700)
    adapters = (
        TCADExecutorAdapter(tmp_path / "unused.socket"),
        CommandTCADExecutorAdapter(CommandAdapterConfig(executable=str(executable)),
            local_result_root=tmp_path / "results"),
    )
    for index, adapter in enumerate(adapters):
        exchange = tmp_path / f"exchange-{index}"
        exchange.mkdir()
        raw = canonical_json(reviewed.model_dump(mode="json"))
        payload_path = exchange / "package.json"
        payload_path.write_bytes(raw)
        payload = LocalFileDescriptor(name="execution-package", local_path=str(payload_path),
            size_bytes=len(raw), media_type="application/json")
        monkeypatch.setattr(adapter, "capabilities", lambda: (SimpleNamespace(
            content=canonical_json(reviewed.capability.model_dump(mode="json"))),))
        monkeypatch.setattr(adapter, "execution_policy", lambda: SimpleNamespace(
            runner=SimpleNamespace(transfer_chunk_bytes=1)))
        commands = []

        def transport(command, values):
            commands.append(command)
            assert command == "prepare"  # No submit/upload/solver in this test.
            return {"submission": {**values["job_spec"], "schema_version": 1}}

        monkeypatch.setattr(adapter, "_call", transport)
        with pytest.raises(PackagerError, match="input bytes"):
            adapter.prepare(payload, preparation_profile="tcad.execution-package.v2",
                exchange_directory=exchange, input_metadata=lambda _: (2, "application/json"),
                open_input=lambda _: io.BytesIO(b"[]x"))
        assert commands == []
        submission = adapter.prepare(payload, preparation_profile="tcad.execution-package.v2",
            exchange_directory=exchange, input_metadata=lambda _: (2, "application/json"),
            open_input=lambda _: io.BytesIO(b"{}"))
        assert Path(submission.local_path).is_file()
        assert commands == ([] if index == 0 else ["prepare"])


class _ManagedTCADBackend:
    """Trusted dependency stand-in only; no transport, process or solver."""
    def __init__(self, root):
        self.root, self.events = root, []
        self.state, self.found, self.lost = 'accepted', None, False
        self.fenced, self.retire_lost = False, False

    def prepare(self, payload, *, preparation_profile, exchange_directory, **callbacks):
        from tcad_artifact.project_packager import package_execution_package_json, validate_execution_package_json
        self.events.append('prepare')
        assert preparation_profile == 'tcad.execution-package.v2'
        raw = Path(payload.local_path).read_bytes()
        inputs = materialize_execution_inputs(validate_execution_package_json(raw), None,
            exchange_directory, 1, **callbacks)
        package = package_execution_package_json(raw, input_payloads=inputs,
            output_root=exchange_directory / 'prepared')
        from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
        return LocalFileDescriptor.model_validate(package.job_spec_file.model_dump(mode='json'))

    def lookup_submission(self, descriptor):
        self.events.append('lookup')
        if self.found is not None:
            assert descriptor.model_dump(mode='json') == self.prepared_ref
            return self.found, self.state
        return None

    def submit(self, descriptor, *, authorization):
        self.events.append('submit')
        if self.fenced:
            raise RuntimeError('exact synthetic submission was fenced')
        self.prepared_ref = descriptor.model_dump(mode='json')
        if self.lost:
            raise RuntimeError('synthetic lost response')
        self.found = 'run_0123456789abcdef0123456789abcdef'
        return self.found, self.state

    def status_details(self, external):
        self.events.append('query')
        assert external == self.found
        return dict(state=self.state, run_id=external)

    def collect_with_budget(self, external, *, context):
        from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
        self.events.append('collect')
        assert external == self.found
        context.remaining_seconds()
        rows = [('tcad_log', b'synthetic native log\n', 'text/plain')]
        manifest = dict(started_at=None, completed_at='2026-10-17T00:00:00Z',
            terminal_state=self.state, exit_code=130 if self.state == 'cancelled' else 0,
            error='synthetic terminal', outputs=[])
        rows.append(('tcad_manifest', canonical_json(manifest), 'application/json'))
        outputs = []
        for name, raw, media in rows:
            path = self.root / name
            path.write_bytes(raw)
            outputs.append(LocalFileDescriptor(name=name, local_path=str(path),
                size_bytes=len(raw), media_type=media))
        return tuple(outputs)

    def cancel(self, external):
        self.events.append('cancel')
        assert external == self.found
        self.state = 'cancelling'
        return self.state

    def validate_submission_protocol(self):
        self.events.append('protocol')
        return dict(version=2, submit='tcad_submit_retirable', retire='tcad_retire_submission')

    def retire_submission_details(self, descriptor):
        self.events.append('retire')
        if self.found is not None:
            assert descriptor.model_dump(mode='json') == self.prepared_ref
            return dict(found=True, run_id=self.found, state=self.state)
        self.fenced = True
        if self.retire_lost:
            raise TimeoutError('synthetic fence acknowledgment lost')
        return dict(found=False, retired=True, submission_id=json.loads(Path(descriptor.local_path).read_bytes())['request_id'])


def _managed_tcad_case(tmp_path, *, retirement=False, call_id='0001-original', storage_policy=None):
    from scidiscovery.artifact_agent.schema.execution import ExecutionAdmission, LocalFileDescriptor
    from scidiscovery.plugin_runtime.collection import CollectionContext
    from scidiscovery.research_loop import experiments as e
    from scidiscovery.research_loop.protocol import CURRENT
    from tcad_artifact.managed_execution import TCADManagedExecution

    job=tmp_path/'job.json'
    if not job.exists():e.write(job,dict(step=1,role='W'))
    scope = dict(step=1, role='W', job_ref=dict(path=str(job)), execution_key=dict(path=str(job)),
        mechanism_ref=None, handoff_protocol=CURRENT)
    config = dict(transport='socket', socket_path=str(tmp_path / 'unused.socket'),
        scope=scope, record_dir=str(tmp_path / 'records'), native_attempt=dict(number=0),
        admission=dict(version=1, mode='hard'), max_seconds=60, storage_policy=storage_policy)
    # Trusted DI boundary only; this does not claim a real core freeze.
    config['execution_interface'] = dict(version=e.VERSION, executor='tcad_execution:tcad',
        observation_version=e.OBSERVATION_VERSION, terminal_evidence='adapter_result', result_protocol=None,
        config_record=dict(config),
        capabilities=vars(TCADManagedExecution.lifecycle_capabilities()))
    raw = canonical_json(_small_resolved_execution_package().model_dump(mode='json'))
    source = tmp_path / (call_id+'-execution-package.json')
    source.write_bytes(raw)
    payload = LocalFileDescriptor(name=call_id, local_path=str(source),
        size_bytes=len(raw), media_type='application/json')
    owner = tmp_path / (call_id+'-trusted-owner-original.json')
    e.write(owner, dict(call_id=payload.name, scope=scope, payload=payload.model_dump(mode='json')))
    receipt = e.proof(owner)
    admission = ExecutionAdmission(outcome='policy', policy_digest=request_digest({}), policy={},
        budget=dict(max_storage_bytes=2_000_000_000, wall_time_seconds=60),
        allowance=dict(max_storage_bytes=2_000_000_000, wall_time_seconds=60),
        outside_allowance='deny', budget_key=request_digest(dict(owner=str(owner))), reason='synthetic trusted dependency only')
    control = SimpleNamespace(phases=[], collect=True, context=CollectionContext.for_seconds(60))

    def gate(phase, **bound):
        control.phases.append(phase)
        assert bound['scope'] == scope and bound['call_id'] == payload.name
        assert bound['payload'] == payload
        assert bound['target']['transport']['socket_path'] == config['socket_path']
        if phase == 'collect' and not control.collect:
            raise e.UnsupportedExecution('synthetic original collection unavailable')
        return dict(receipt=receipt, authorization=admission, context=control.context)

    backend = _ManagedTCADBackend(tmp_path)
    wrapper = TCADManagedExecution(config, backend, gate=gate,
        input_metadata=lambda item: (item.size_bytes, item.media_type),
        open_input=lambda _: io.BytesIO(b'{}'))
    if retirement:
        config['execution_interface']['public_capabilities'] = e._public_capabilities(wrapper)
    prepared = wrapper.prepare(payload, preparation_profile='research_loop.v1',
        exchange_directory=wrapper.records.root)
    assert prepared.name == payload.name  # The native package's own name is not a second call.
    accepted = wrapper.records.accept(prepared, wrapper.acceptance_metadata(prepared), config['execution_interface'])
    wrapper.dispatch_lease(accepted)
    return config, wrapper, backend, control, payload, prepared


def test_managed_tcad_default_factory_blocks_before_any_backend_call(tmp_path, monkeypatch):
    from scidiscovery.operations.execution import LocalFileDescriptor
    from scidiscovery.operations.runtime_plugins import RuntimePluginContext
    from scidiscovery.research_loop import experiments as e
    import tcad_artifact.managed_execution as managed

    config, wrapper, _, _, payload, _ = _managed_tcad_case(tmp_path)
    backend = _ManagedTCADBackend(tmp_path)
    monkeypatch.setattr(managed, 'build_adapter', lambda *args, **kwargs: backend)
    contribution = managed.build_managed_runtime(RuntimePluginContext('tcad_execution', 'local_worker',
        tmp_path / 'config.json', e.encoded({key: value for key, value in config.items()
            if key != 'execution_interface'}), wrapper.records.root,
            frozen_execution_interface=config['execution_interface']))
    actual = contribution.execution_adapters['tcad']
    source = tmp_path / 'self-contained.json'
    source.write_bytes(_execution_package())
    identity = e.proof(source)
    self_contained = LocalFileDescriptor(name='0002-default-blocked', local_path=str(source),
        size_bytes=identity['size_bytes'], media_type='application/json')
    with pytest.raises(e.UnsupportedExecution, match='production blocked'):
        actual.prepare(self_contained, preparation_profile='research_loop.v1', exchange_directory=actual.records.root)
    assert backend.events == []
    actual.gate = lambda *args, **kwargs: pytest.fail('Missing resolver reached authority gate')
    with pytest.raises(e.UnsupportedExecution, match='Exact frozen TCAD input originals'):
        actual.prepare(payload, preparation_profile='research_loop.v1', exchange_directory=actual.records.root)
    assert backend.events == []
    assert actual.result_protocol is None
    actual.config['execution_interface'] = {'executor': 'tcad_execution:tcad'}
    with pytest.raises(e.IntegrityError, match='complete original host interface'):
        actual.prepare(self_contained, preparation_profile='research_loop.v1', exchange_directory=actual.records.root)
    assert backend.events == []
    with pytest.raises(ValueError, match='undeclared'):
        managed.build_managed_runtime(RuntimePluginContext('tcad_execution', 'local_worker',
            tmp_path / 'config.json', e.encoded(dict(config, gate='allow')), wrapper.records.root))


def test_managed_tcad_retains_cancelled_original_without_requery(tmp_path):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, control, payload, prepared = _managed_tcad_case(tmp_path)
    external, state = wrapper.submit(prepared)
    assert state == 'accepted'
    wrapper.records.bind_external(payload.name, external, state)
    backend.state = 'queued'
    assert wrapper.status_details(payload.name)['state'] == 'queued'
    backend.found = 'run_changed'
    with pytest.raises(e.IntegrityError, match='external identity'):
        wrapper.status_details(payload.name)
    backend.found = external
    assert wrapper.cancel_original(payload.name) == 'cancelling'
    assert wrapper.status_details(payload.name)['state'] == 'cancelling'
    backend.state = 'cancelled'
    observed = e.record_observation(config, wrapper, payload.name)
    assert observed['native_state'] == 'cancelled' and observed['status'] == 'known_failed'
    assert observed['external_run_id'] == external
    assert {item['name'] for item in observed['outputs']} == {'tcad_log', 'tcad_manifest'}
    calls = list(backend.events)
    assert e.record_observation(config, wrapper, payload.name) == observed
    assert wrapper.result(payload.name)['native_state'] == 'cancelled'
    assert backend.events == calls and backend.events.count('submit') == 1
    assert control.phases == ['prepare', 'submit', 'cancel', 'collect']
    binding = wrapper.records.directory(payload.name) / 'tcad-backend-binding.json'
    value = json.loads(binding.read_bytes()); value['prepared_ref'] = dict(path=str(tmp_path/'other-original'),size_bytes=0)
    binding.write_bytes(e.encoded(value))
    with pytest.raises(e.IntegrityError):
        wrapper.result(payload.name)


def test_managed_tcad_unknown_and_unverified_terminal_block_new_call(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, control, payload, prepared = _managed_tcad_case(tmp_path)
    backend.lost = True
    with pytest.raises(e.UnsupportedExecution, match='response is unknown'):
        wrapper.submit(prepared)
    with pytest.raises(e.UnsupportedExecution, match='without resubmission'):
        wrapper.submit(prepared)
    assert backend.events.count('submit') == 1
    backend.found = 'run_0123456789abcdef0123456789abcdef'
    wrapper.records.bind_external(payload.name, *wrapper.lookup_submission(prepared))
    backend.state = 'succeeded'; control.collect = False
    assert e.record_observation(config, wrapper, payload.name) is None
    directory = wrapper.records.directory(payload.name)
    assert not (directory / 'completed.json').exists()
    monkeypatch.setattr(e, 'load', lambda _: wrapper)
    monkeypatch.setattr(e, 'original_adapter', lambda *args: (config, wrapper))
    before = list(backend.events)
    result = e.dispatch_descriptor(config, payload)
    assert result['status'] == 'unknown' and result['call_id'] == payload.name
    assert backend.events.count('submit') == 1 and backend.events.count('prepare') == 1
    assert 'collect' not in backend.events[len(before):]
    # An existing canonical completion cannot be hidden behind a neutral result.
    e.write(directory / 'completed.json', dict(status='known_success'))
    monkeypatch.setattr(wrapper, 'status_details', lambda _: dict(state='succeeded'))
    monkeypatch.setattr(wrapper, 'result', lambda _: dict(status='unknown'))
    with pytest.raises(e.IntegrityError):
        e.record_observation(config, wrapper, payload.name)


def test_managed_tcad_collection_expiry_preserves_unknown_and_partial(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments as e
    import tcad_artifact.managed_execution as managed
    config, wrapper, backend, control, payload, prepared = _managed_tcad_case(tmp_path)
    wrapper.records.bind_external(payload.name, *wrapper.submit(prepared))
    backend.state = 'succeeded'
    original_retain = managed._retain_original

    def expire_during_local_retention(descriptor, target, context, **options):
        assert context is control.context
        context.deadline_monotonic = 0
        return original_retain(descriptor, target, context, **options)

    monkeypatch.setattr(managed, '_retain_original', expire_during_local_retention)
    assert e.record_observation(config, wrapper, payload.name) is None
    directory = wrapper.records.directory(payload.name)
    assert not (directory / 'completed.json').exists()
    assert not list((directory / 'tcad-originals').iterdir())
    assert (tmp_path / 'tcad_log').read_bytes() == b'synthetic native log\n'
    assert e.record_observation(config, wrapper, payload.name) is None
    assert backend.events.count('collect') == 1  # The same expired authority is not reset.


def test_managed_tcad_collected_crash_window_revalidates_retained_facts(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, _, payload, prepared = _managed_tcad_case(tmp_path)
    wrapper.records.bind_external(payload.name, *wrapper.submit(prepared))
    backend.state = 'succeeded'
    publish = wrapper.records.publish

    def stop_before_completed(call, filename, value):
        if filename == 'completed.json':
            raise OSError('synthetic crash before canonical completion')
        return publish(call, filename, value)

    with monkeypatch.context() as patch:
        patch.setattr(wrapper.records, 'publish', stop_before_completed)
        with pytest.raises(OSError, match='synthetic crash'):
            wrapper.result(payload.name)
    directory = wrapper.records.directory(payload.name)
    retained = directory / 'collected.json'
    original = retained.read_bytes()
    value = json.loads(original); value['native_state'] = 'failed'
    retained.chmod(0o600)
    retained.write_bytes(e.encoded(value))
    before = list(backend.events)
    with pytest.raises(e.IntegrityError, match='terminal state'):
        wrapper.result(payload.name)
    assert not (directory / 'completed.json').exists()
    retained.write_bytes(original)
    assert wrapper.result(payload.name)['status'] == 'known_success'
    assert backend.events == before  # Reentry consumes retained facts, without remote re-collection.


def test_managed_tcad_permanent_retirement_seals_under_call_lease(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, _, payload, prepared = _managed_tcad_case(tmp_path, retirement=True)
    old = dict(config, execution_interface={key: value for key, value in config['execution_interface'].items()
        if key != 'public_capabilities'})
    with pytest.raises(e.UnsupportedExecution, match='capability is unavailable'):
        e.retire_original(old, wrapper, payload.name)
    publish = wrapper.records.publish

    def publish_inside_original_lease(call, filename, value):
        if filename == 'submission-retirement.json':
            with pytest.raises(BlockingIOError):
                wrapper.submit(prepared)
        return publish(call, filename, value)

    monkeypatch.setattr(wrapper.records, 'publish', publish_inside_original_lease)
    reply = e.retire_original(config, wrapper, payload.name)
    assert reply['status'] == 'not_submitted'
    directory = wrapper.records.directory(payload.name)
    assert not any((directory / name).exists() for name in (
        'submission.json', 'completed.json', 'terminal.json', 'execution-observation.json'))
    calls = list(backend.events)
    assert e.retire_original(config, wrapper, payload.name) == reply
    with pytest.raises(e.UnsupportedExecution, match='permanently retired'):
        wrapper.submit(prepared)
    assert backend.events == calls and 'submit' not in calls
    monkeypatch.setattr(e, 'load', lambda _: wrapper)
    assert e.progress(config)['counts']['not_submitted'] == 1
    seen = []

    def next_prepare(descriptor, **options):
        seen.append(descriptor.name)
        raise e.UnsupportedExecution('next call still requires its own gate')

    monkeypatch.setattr(wrapper, 'prepare', next_prepare)
    with pytest.raises(e.UnsupportedExecution, match='own gate'):
        e.dispatch_descriptor(config, payload)
    assert len(seen) == 1 and seen[0] != payload.name
    assert len(wrapper.records.calls()) == 1  # Retiring does not refund scientific opportunities.


def test_managed_tcad_lost_retirement_and_independent_identity_validation(tmp_path):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, _, payload, prepared = _managed_tcad_case(tmp_path, retirement=True)
    accepted = (wrapper.records.directory(payload.name) / 'accepted.json').read_bytes()
    backend.retire_lost = True
    assert e.retire_original(config, wrapper, payload.name)['status'] == 'unknown'
    with pytest.raises(e.UnsupportedExecution, match='without resubmission'):
        wrapper.submit(prepared)
    assert 'submit' not in backend.events
    assert (wrapper.records.directory(payload.name) / 'accepted.json').read_bytes() == accepted
    backend.retire_lost = False
    e.retire_original(config, wrapper, payload.name)
    value = e.retirement(config, payload.name)
    wrong = tmp_path / 'wrong-assertion.json'; e.write(wrong, dict(version=0))
    with pytest.raises(e.IntegrityError, match='Normalized retirement'):
        e.verify_retirement(dict(value, assertion=e.proof(wrong)), lambda item: Path(item['path']))
    original = Path(prepared.local_path).read_bytes()
    Path(prepared.local_path).chmod(0o600)
    Path(prepared.local_path).write_bytes(b'changed native prepared bytes')
    with pytest.raises(e.IntegrityError, match='original changed'):
        e.retirement(config, payload.name)
    Path(prepared.local_path).write_bytes(original)
    assert e.retirement(config, payload.name)['kind'] == 'permanent_not_submitted'


def test_managed_tcad_retirement_found_job_never_becomes_non_submission(tmp_path):
    from scidiscovery.research_loop import experiments as e
    config, wrapper, backend, _, payload, prepared = _managed_tcad_case(tmp_path, retirement=True)
    external, _ = wrapper.submit(prepared)  # Native reply exists before core external binding.
    backend.state = 'queued'
    reply = e.retire_original(config, wrapper, payload.name)
    assert reply['status'] == 'queued' and wrapper.records.external(payload.name) == external
    assert e.retirement(config, payload.name) is None
    assert e.retire_original(config, wrapper, payload.name)['status'] == 'queued'
    assert 'cancel' not in backend.events
    backend.found = None  # A lost backend database cannot erase the original known job.
    with pytest.raises(e.IntegrityError, match='contradicts'):
        e.retire_original(config, wrapper, payload.name)
    assert not (wrapper.records.directory(payload.name) / 'submission-retirement.json').exists()


def test_retirement_archive_shares_original_validator(tmp_path, monkeypatch):
    from scidiscovery.research_loop import experiments as e, history, protocol
    config, wrapper, _, _, payload, _ = _managed_tcad_case(tmp_path, retirement=True)
    e.retire_original(config, wrapper, payload.name)
    value = e.retirement(config, payload.name)
    accepted = wrapper.records.accepted(payload.name)
    def archive(original):
        path=Path(original['path'])
        assert path.is_relative_to(tmp_path) and path.stat().st_size==original['size_bytes']
        relative=path.relative_to(tmp_path).as_posix()
        return dict(path=relative,size_bytes=original['size_bytes'],retention='archived',snapshot=relative)

    directory = wrapper.records.directory(payload.name)
    row = dict(call_id=payload.name, scope=config['scope'], mechanism_binding=None, status='not_submitted',
        result_protocol=None, accepted=archive(value['accepted']), completed=None, terminal=None,
        retirement=archive(e.proof(directory / 'submission-retirement.json')),
        originals=[archive(item) for item in e.retirement_originals(value, accepted)])
    # Only host scope/config discovery is injected; actual archived consumer/validator run unmodified.
    monkeypatch.setattr(history, '_execution_interface', lambda root, bound: config['execution_interface'])
    monkeypatch.setattr(protocol, 'stage_scope', lambda root, job: config['scope'])
    assert history.managed_status(tmp_path, row) == 'not_submitted'
    response=next(item for item in value['native_originals'] if Path(item['path']).name.startswith('response-'))
    native=Path(response['path']);native.chmod(0o600);native.write_bytes(b'changed response original')
    with pytest.raises(history.ObserverIntegrityError):history.managed_status(tmp_path,row)


def test_tcad_native_outputs_freeze_once_per_exact_call(tmp_path,monkeypatch):
    from scidiscovery.research_loop import atomic_files,experiments as e
    copies=[];copy=atomic_files.copy_original
    def counted(source,target,**options):
        if Path(source)==tmp_path/'tcad_log':copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    paths=[];source_paths=[]
    for call_id in ('first-original','second-original'):
        config,wrapper,backend,_,payload,prepared=_managed_tcad_case(tmp_path,call_id=call_id)
        wrapper.records.bind_external(payload.name,*wrapper.submit(prepared))
        backend.state='succeeded'
        completed=wrapper.result(payload.name)
        assert completed['status']=='known_success'
        collected=json.loads((wrapper.records.directory(payload.name)/'collected.json').read_bytes())
        output=next(item for item in collected['outputs'] if item['name']=='tcad_log')
        paths.append(Path(output['local_path']))
        source_paths.append(output['local_path'])
    assert len(set(source_paths))==2
    assert len({(path.stat().st_dev,path.stat().st_ino) for path in paths})==2
    assert copies==[len(b'synthetic native log\n')]*2
    assert all(not path.samefile(tmp_path/'tcad_log') for path in paths)
    (tmp_path/'tcad_log').write_bytes(b'changed external native file')
    assert all(path.read_bytes()==b'synthetic native log\n' for path in paths)
    assert all(path.stat().st_mode & 0o777==0o400 for path in paths)


def test_tcad_first_output_freeze_obeys_frozen_instance_quota(tmp_path,monkeypatch):
    from scidiscovery.research_loop import atomic_files,experiments as e,storage_policy as storage
    limits=dict(enabled=True,max_instance_bytes=2*1024*1024)
    config,wrapper,backend,_,payload,prepared=_managed_tcad_case(tmp_path,storage_policy=limits)
    wrapper.records.bind_external(payload.name,*wrapper.submit(prepared))
    backend.state='succeeded'
    root=wrapper.records.storage_root()
    filler=root/'filled-before-terminal-retention'
    with filler.open('wb') as stream:stream.truncate(limits['max_instance_bytes']-storage.usage(root))
    copy=atomic_files.copy_original
    def forbidden(source,target,**options):
        assert Path(source)!=tmp_path/'tcad_log','TCAD wrote payload before instance quota admission'
        return copy(source,target,**options)
    monkeypatch.setattr(atomic_files,'copy_original',forbidden)
    with pytest.raises(storage.StorageQuotaExceeded,match='max_instance_bytes'):
        wrapper.result(payload.name)
    directory=wrapper.records.directory(payload.name)
    assert not (directory/'tcad-originals'/'tcad_log').exists()
    assert not (directory/'completed.json').exists()
    assert backend.events.count('submit')==1



def test_tcad_result_reports_unsupported_sharing_without_resubmission(tmp_path,monkeypatch):
    import errno
    from scidiscovery import archive_io
    from scidiscovery.research_loop import atomic_files,experiments as e,shared_originals as shared
    config,wrapper,backend,_,payload,prepared=_managed_tcad_case(tmp_path)
    external,state=wrapper.submit(prepared)
    wrapper.records.bind_external(payload.name,external,state)
    backend.state='succeeded'
    link=shared.os.link
    def unsupported(source,target,*args,**options):
        if Path(target).parent.name=='tcad-originals':raise OSError(errno.EXDEV,'sharing unsupported')
        return link(source,target,*args,**options)
    copies=[];copy=atomic_files.copy_original
    def counted(source,target,**options):
        if Path(source)==tmp_path/'tcad_log':copies.append(options['size_bytes'])
        return copy(source,target,**options)
    monkeypatch.setattr(shared.os,'link',unsupported)
    monkeypatch.setattr(atomic_files,'copy_original',counted)
    with pytest.raises(archive_io.SharingUnsupported,match='hardlink'):
        wrapper.result(payload.name)
    directory=wrapper.records.directory(payload.name)
    assert copies==[len(b'synthetic native log\n')]
    assert not (directory/'tcad-originals'/'tcad_log').exists()
    assert not (directory/'completed.json').exists() and not (directory/'collected.json').exists()
    assert wrapper.records.external(payload.name)==external
    assert [path.name for path in wrapper.records.calls()]==[payload.name]
    assert backend.events.count('submit')==1 and backend.events.count('collect')==1
