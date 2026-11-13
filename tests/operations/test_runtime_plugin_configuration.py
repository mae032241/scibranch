from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scidiscovery.artifact_agent.schema.common import canonical_json
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
        with pytest.raises(PackagerError, match="original exceeds its declared input size"):
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
