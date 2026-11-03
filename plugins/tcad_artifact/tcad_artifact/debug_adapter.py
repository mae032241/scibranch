"""Development-only direct-solver bridge for one Operation Run."""

from __future__ import annotations

from scidiscovery.plugin_runtime.submission import SubmissionNotCreated

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path

from scidiscovery.artifact_agent.execution_bridge import ExecutionAdapter
from scidiscovery.artifact_agent.schema.execution import LocalFileDescriptor
from .debug_contract import (
    DEVELOPMENT_ARTIFACT_LIMIT_BYTES,
    CollectedTCADDebugFile,
    CollectedTCADDebugRun,
    PreparedTCADDebugRun,
    TCADDebugSource,
    TCADSourceDiagnostic,
)

from .execution_control import ResourceLimits, SolverCapabilitySnapshot, TCADJobSpec
from .remote_runner_py36 import _execution_timing, _redact_log
from .project_packager import (
    DeckProjectDraft,
    PackagerError,
    ResolvedProjectInput,
    package_deck_project,
)


# Author-side runs are manual-backed preflight/smoke checks plus one
# control-derived initialization probe. Full study execution belongs to the
# reviewed, approved production bridge.
_MANUAL_BACKED_RELEASE = "R-2020.09"


def _development_limits(mode: str, policy) -> tuple[int, int]:
    if mode not in {"preflight", "smoke", "initialization"}:
        raise ValueError("development debug mode is unsupported")
    value = getattr(policy, mode)
    return value.wall_time_seconds, value.max_output_bytes


def _development_arguments(
    *, release: str, solver_kind: str, entrypoint: str, mode: str
) -> tuple[str, ...]:
    if not entrypoint.endswith(".cmd"):
        raise ValueError("development debug requires a direct command file")
    if (
        release == _MANUAL_BACKED_RELEASE
        and solver_kind == "sprocess"
        and mode == "initialization"
    ):
        return (entrypoint,)
    if (
        release == _MANUAL_BACKED_RELEASE
        and solver_kind == "sdevice"
        and mode == "initialization"
    ):
        return "-i", entrypoint
    option = {
        (_MANUAL_BACKED_RELEASE, "sprocess", "preflight"): "-s",
        (_MANUAL_BACKED_RELEASE, "sprocess", "smoke"): "-f",
        (_MANUAL_BACKED_RELEASE, "sdevice", "preflight"): "-P",
    }.get((release, solver_kind, mode))
    if option is None:
        raise ValueError("development debug mode is unsupported for the solver")
    return option, entrypoint


def _manual_backed_release(snapshot: SolverCapabilitySnapshot) -> str:
    release = snapshot.public_release_label
    if re.search(r"(?<![A-Za-z0-9.])R-2020\.09(?![A-Za-z0-9.])", release):
        return _MANUAL_BACKED_RELEASE
    raise ValueError("development debug release has no bundled mode contract")


def _development_mode(job: TCADJobSpec) -> str:
    archive_paths = {item.relative_path for item in job.archive_entries}
    if (
        job.solver_kind == "sprocess"
        and len(job.arguments) == 1
        and job.arguments[0] in archive_paths
    ):
        return "initialization"
    if len(job.arguments) != 2 or job.arguments[1] not in archive_paths:
        raise ValueError("development debug arguments are outside the controlled modes")
    option = job.arguments[0]
    if job.solver_kind == "sprocess":
        modes = {"-s": "preflight", "-f": "smoke"}
    elif job.solver_kind == "sdevice":
        modes = {"-P": "preflight", "-i": "initialization"}
    else:
        modes = {}
    try:
        return modes[option]
    except KeyError as error:
        raise ValueError(
            "development debug arguments are outside the controlled modes"
        ) from error


class TCADDevelopmentDebugBridge:
    """Prepare bounded, control-selected development jobs for direct solvers."""

    def __init__(self, adapter: ExecutionAdapter) -> None:
        self.adapter = adapter
        self._policy = None
        self._submission_bindings: dict[
            tuple[str, str], tuple[str, str, str]
        ] = {}
        self._binding_lock = threading.Lock()

    @property
    def policy(self):
        if self._policy is None:
            self._policy = self.adapter.execution_policy().debug
        return self._policy

    def prepare(
        self,
        *,
        project: bytes,
        capability: bytes,
        sources: tuple[TCADDebugSource, ...],
        exchange_directory: Path,
        mode: str,
        output_names: tuple[str, ...] = (),
        output_budget_bytes: int | None = None,
    ) -> PreparedTCADDebugRun:
        draft = DeckProjectDraft.model_validate_json(project, strict=True)
        if output_names and mode != "initialization":
            raise ValueError("diagnostic outputs are only available in initialization mode")
        if len(output_names) > self.policy.max_output_files:
            raise ValueError("too many initialization diagnostic outputs")
        declared = {item.name: item for item in draft.expected_outputs}
        for name in output_names:
            _validate_output_name(name)
            if name not in declared or declared[name].capture != "workspace_file":
                raise ValueError(f"initialization output is not a declared workspace file: {name}")
        snapshot = SolverCapabilitySnapshot.model_validate_json(
            capability, strict=True
        )
        if draft.solver_kind not in {"sprocess", "sdevice"}:
            raise ValueError("development debug requires a direct TCAD solver")
        release = _manual_backed_release(snapshot)
        mode_wall, mode_output = _development_limits(mode, self.policy)
        if output_budget_bytes is not None:
            if output_budget_bytes < 1:
                raise ValueError("development output budget is exhausted")
            mode_output = min(mode_output, output_budget_bytes)
        mode_arguments = _development_arguments(
            release=release,
            solver_kind=draft.solver_kind,
            entrypoint=draft.entrypoint,
            mode=mode,
        )
        if (
            draft.tool_profile != snapshot.profile_id
            or draft.solver_kind != snapshot.solver_kind
            or draft.capability_sha256 != snapshot.capability_sha256
        ):
            raise ValueError("development debug capability differs from the project")
        if draft.arguments:
            raise ValueError(
                "development debug does not admit worker-selected solver arguments"
            )
        try:
            self._require_active_capability(snapshot)
        except ValueError as error:
            if str(error) == "development debug capability is not active":
                raise
            raise ValueError(
                "development debug capability discovery failed"
            ) from error
        except Exception as error:
            raise ValueError(
                "development debug capability discovery failed"
            ) from error
        source_map = {item.source_name: item for item in sources}
        if len(source_map) != len(sources):
            raise ValueError("development debug Run sources are not unique")
        if mode == "initialization":
            if draft.development_initialization_entrypoint is None:
                raise ValueError(
                    "initialization mode requires an author-declared development entrypoint"
                )
            draft = draft.model_copy(
                update={
                    "entrypoint": draft.development_initialization_entrypoint,
                    "development_initialization_entrypoint": None,
                    "parameter_bindings": (),
                    "case_parameter_bindings": (),
                    "runtime_assertions": (),
                    "realization_manifest": (),
                    "materialization_report": None,
                    "preflight_attestation": None,
                }
            )
            mode_arguments = _development_arguments(
                release=release,
                solver_kind=draft.solver_kind,
                entrypoint=draft.entrypoint,
                mode=mode,
            )
        resolved = []
        payloads = {}
        for slot in draft.input_slots:
            try:
                source = source_map[slot.semantic_name]
            except KeyError as error:
                raise ValueError(
                    "development debug project slot has no exact task input"
                ) from error
            if source.media_type != slot.media_type:
                raise ValueError(
                    "development debug project slot media type differs"
                )
            resolved.append(
                ResolvedProjectInput(
                    semantic_name=slot.semantic_name,
                    target_relative_path=slot.target_relative_path,
                    artifact_ref=source.artifact_ref,
                    media_type=source.media_type,
                    size_bytes=source.content.stat().st_size if isinstance(source.content, Path) else len(source.content),
                )
            )
            payloads[slot.semantic_name] = source.content
        exchange = exchange_directory.absolute()
        try:
            exchange.mkdir(parents=True, exist_ok=True, mode=0o750)
            if exchange.is_symlink() or not exchange.is_dir():
                raise ValueError(
                    "development debug exchange is not a real directory"
                )
        except ValueError:
            raise
        except Exception as error:
            raise ValueError("development debug exchange setup failed") from error
        try:
            packaged = package_deck_project(
                draft,
                capability=snapshot,
                resolved_inputs=tuple(resolved),
                input_payloads=payloads,
                output_root=exchange / "prepared",
            )
        except PackagerError as error:
            raise ValueError("development debug project packaging failed") from error
        except Exception as error:
            reason = (
                "development debug archive path is unsupported"
                if str(error) == "name is too long"
                else "development debug project packaging failed"
            )
            raise ValueError(reason) from error
        try:
            limits = ResourceLimits(
                max_storage_bytes=min(draft.resource_limits.max_storage_bytes, self.policy.max_storage_bytes),
                wall_time_seconds=min(
                    draft.resource_limits.wall_time_seconds, mode_wall
                ),
                cpu_time_seconds=min(
                    draft.resource_limits.cpu_time_seconds, mode_wall
                ),
                max_memory_bytes=min(
                    draft.resource_limits.max_memory_bytes, self.policy.max_memory_bytes
                ),
                max_output_bytes=min(
                    draft.resource_limits.max_output_bytes, mode_output
                ),
            )
            job = packaged.job_spec.model_copy(
                update={
                    "execution_purpose": "development_debug",
                    "arguments": mode_arguments,
                    "expected_outputs": tuple(
                        item.model_copy(update={"required": True,
                            "max_bytes": min(item.max_bytes, self.policy.max_output_file_bytes, limits.max_output_bytes)})
                        for item in packaged.job_spec.expected_outputs if item.name in output_names
                    ),
                    "collect_generated_outputs": mode == "initialization" and draft.collect_generated_outputs,
                    "limits": limits,
                }
            )
            job_path = exchange / "development-debug-job.json"
            raw = _canonical(job.model_dump(mode="python"))
            if job_path.exists():
                if job_path.is_symlink() or job_path.read_bytes() != raw:
                    raise ValueError("development debug job changed within one run")
            else:
                _write_new(job_path, raw)
            return PreparedTCADDebugRun(
                submission=_descriptor(
                    "development_debug_submission", job_path, "application/json"
                ),
                wall_time_seconds=limits.wall_time_seconds,
                arguments=mode_arguments,
            )
        except Exception as error:
            raise ValueError(
                "development debug job materialization failed"
            ) from error

    def clamp_wall_time(
        self, prepared: PreparedTCADDebugRun, *, wall_time_seconds: int
    ) -> PreparedTCADDebugRun:
        if wall_time_seconds < 1:
            raise ValueError("development debug wall-time bound is exhausted")
        raw = _read_descriptor(prepared.submission, max_bytes=1024 * 1024)
        job = TCADJobSpec.model_validate_json(raw, strict=True)
        if job.execution_purpose != "development_debug":
            raise ValueError("development debug submission purpose changed")
        bounded = min(
            prepared.wall_time_seconds,
            job.limits.wall_time_seconds,
            wall_time_seconds,
        )
        limits = job.limits.model_copy(
            update={
                "wall_time_seconds": bounded,
                "cpu_time_seconds": min(job.limits.cpu_time_seconds, bounded),
            }
        )
        clamped = job.model_copy(update={"limits": limits})
        source = Path(prepared.submission.local_path).absolute()
        path = source.parent / f"development-debug-job-{bounded}.json"
        encoded = _canonical(clamped.model_dump(mode="python"))
        if path.exists():
            if path.is_symlink() or path.read_bytes() != encoded:
                raise ValueError("development debug clamped job changed")
        else:
            _write_new(path, encoded)
        return PreparedTCADDebugRun(
            submission=_descriptor(
                "development_debug_submission", path, "application/json"
            ),
            wall_time_seconds=bounded,
            arguments=prepared.arguments,
        )

    def submit(self, submission: LocalFileDescriptor) -> tuple[str, str]:
        key = (submission.local_path, submission.sha256)
        with self._binding_lock:
            binding = self._submission_bindings.get(key)
        if binding is None:
            raise SubmissionNotCreated(submission.sha256, "local_validation_failed")
        profile_id, solver_kind, capability_sha256 = binding
        snapshot = next(
            (
                value
                for value in self._capabilities()
                if value.profile_id == profile_id
                and value.solver_kind == solver_kind
                and value.capability_sha256 == capability_sha256
            ),
            None,
        )
        if snapshot is None:
            raise SubmissionNotCreated(submission.sha256, "local_validation_failed")
        result = self.adapter.submit(submission)
        with self._binding_lock:
            self._submission_bindings.pop(key, None)
        return result

    def prepare_submission(
        self, prepared: PreparedTCADDebugRun
    ) -> LocalFileDescriptor:
        raw = _read_descriptor(prepared.submission, max_bytes=1024 * 1024)
        job = TCADJobSpec.model_validate_json(raw, strict=True)
        if job.execution_purpose != "development_debug":
            raise ValueError("development debug submission purpose changed")
        if (
            job.solver_kind not in {"sprocess", "sdevice"}
            or (job.expected_outputs and _development_mode(job) != "initialization")
            or (job.collect_generated_outputs and _development_mode(job) != "initialization")
            or len(job.expected_outputs) > self.policy.max_output_files
            or any(item.capture != "workspace_file" or item.max_bytes > min(self.policy.max_output_file_bytes, job.limits.max_output_bytes)
                   for item in job.expected_outputs)
            or _development_mode(job)
            not in {"preflight", "smoke", "initialization"}
        ):
            raise ValueError("development debug submission boundary is invalid")
        active_capability = self._require_job_capability(job)
        _manual_backed_release(active_capability)
        archive = LocalFileDescriptor.model_validate(
            job.input_archive.model_dump(mode="python"), strict=True
        )
        preparer = getattr(self.adapter, "prepare_development_debug", None)
        if preparer is None:
            raise ValueError(
                "configured TCAD adapter does not support development debug"
            )
        submission = preparer(
            job_spec_file=prepared.submission,
            archive=archive,
            exchange_directory=Path(prepared.submission.local_path).parent,
        )
        key = (submission.local_path, submission.sha256)
        with self._binding_lock:
            self._submission_bindings[key] = (
                job.tool_profile,
                job.solver_kind,
                job.capability_sha256,
            )
        return submission

    def lookup_submission(self, submission):
        return self.adapter.lookup_submission(submission)

    def validate_submission_protocol(self):
        validate = getattr(self.adapter, "validate_submission_protocol", None)
        if callable(validate):
            validate()

    def retire_submission(self, submission):
        method = getattr(self.adapter, "retire_submission", None)
        if not callable(method):
            raise RuntimeError("Diagnostic transport cannot confirm retirement; retain its reservation")
        return method(submission)

    def status(self, external_run_id: str) -> str:
        return str(self.status_details(external_run_id)["state"])

    def status_details(self, external_run_id: str) -> dict:
        reader = getattr(self.adapter, "status_details", None)
        return reader(external_run_id) if callable(reader) else {"state": self.adapter.status(external_run_id)}

    def cancel(self, external_run_id: str) -> str:
        return self.adapter.cancel(external_run_id)

    def collect(self, external_run_id: str, *, limits) -> CollectedTCADDebugRun:
        descriptors = self.adapter.collect(external_run_id)
        return self._collected_run(descriptors, limits=limits)

    def collect_with_budget(self, external_run_id: str, *, context, limits) -> CollectedTCADDebugRun:
        context.remaining_seconds()
        method = getattr(self.adapter, "collect_with_budget", None)
        if not callable(method):
            raise RuntimeError("legacy debug collection requires its configured runtime factory")
        descriptors = method(external_run_id, context=context)
        return self._collected_run(descriptors, context=context, limits=limits)

    def _collected_run(self, descriptors, *, limits, context=None):
        if context: context.remaining_seconds()
        if len(descriptors) > limits["max_output_files"] + 2:
            raise ValueError("development debug output count exceeds its bound")
        for descriptor in descriptors:
            _validate_output_name(descriptor.name)
        by_name = {item.name: item for item in descriptors}
        if len(by_name) != len(descriptors):
            raise ValueError("development debug outputs contain duplicate names")
        try:
            log_descriptor = by_name.pop("tcad_log")
            manifest_descriptor = by_name.pop("tcad_manifest")
        except KeyError as error:
            raise ValueError("development debug control outputs are incomplete") from error
        log_raw = _read_descriptor(log_descriptor, max_bytes=limits["max_log_bytes"])
        manifest_raw = _read_descriptor(
            manifest_descriptor, max_bytes=limits["max_manifest_bytes"]
        )
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("development debug manifest is invalid") from error
        if not isinstance(manifest, dict):
            raise ValueError("development debug manifest is invalid")
        files = []
        total = 0
        paths = {item["name"]: item["relative_path"] for item in manifest.get("outputs", ())}
        for name, descriptor in sorted(by_name.items()):
            if context: context.remaining_seconds()
            content = _read_descriptor(
                descriptor, max_bytes=limits["max_output_file_bytes"]
            )
            total += len(content)
            if total > limits["max_output_bytes"]:
                raise ValueError("development debug outputs exceed their total bound")
            files.append(
                CollectedTCADDebugFile(
                    name=name,
                    media_type=descriptor.media_type,
                    content=content,
                    relative_path=paths.get(name),
                )
            )
        error = str(manifest.get("error", ""))[:4096]
        diagnostic_raw = ("Runner diagnostic: " + error + "\n").encode("utf-8") + log_raw if error else log_raw
        diagnostic_log = _sanitize_log(diagnostic_raw, max_chars=limits["max_response_log_chars"], truncate=False)
        log_excerpt = _sanitize_log(diagnostic_raw, max_chars=limits["max_response_log_chars"])
        files.append(
            CollectedTCADDebugFile(
                name="debug.log.txt",
                media_type="text/plain; charset=utf-8",
                content=diagnostic_log.encode("utf-8"),
            )
        )
        terminal = str(manifest.get("terminal_state", "failed"))
        if terminal not in {"succeeded", "failed", "cancelled"}:
            terminal = "failed"
        try:
            exit_code = int(manifest.get("exit_code", 99))
        except (TypeError, ValueError):
            exit_code = 99
        error = str(manifest.get("error", ""))[:4096]
        if "diagnostic collection failed:" in error:
            # Missing diagnostic coverage must not produce a qualified debug proof.
            terminal = "failed"
        layer, summary = _earliest_diagnostic(
            terminal=terminal,
            exit_code=exit_code,
            error=error,
            log=diagnostic_log,
            output_count=len(by_name),
        )
        source_diagnostic = _source_diagnostic(
            error=_sanitize_log(error.encode("utf-8"), max_chars=limits["max_response_log_chars"]),
            log=diagnostic_log,
        )
        return CollectedTCADDebugRun(
            terminal_state=terminal,
            exit_code=exit_code,
            diagnostic_layer=layer,
            summary=summary,
            log_excerpt=log_excerpt,
            files=tuple(files),
            source_diagnostic=source_diagnostic,
            timing=_execution_timing(manifest.get("started_at"), manifest.get("completed_at"), terminal=True),
        )

    def _capabilities(self) -> tuple[SolverCapabilitySnapshot, ...]:
        return tuple(
            SolverCapabilitySnapshot.model_validate_json(item.content, strict=True)
            for item in self.adapter.capabilities()
            if item.schema_id == "tcad.solver-capability.v2"
        )

    def _require_active_capability(
        self, expected: SolverCapabilitySnapshot
    ) -> None:
        if expected not in self._capabilities():
            raise ValueError("development debug capability is not active")

    def _require_job_capability(
        self, job: TCADJobSpec
    ) -> SolverCapabilitySnapshot:
        active = next(
            (
                value
                for value in self._capabilities()
                if value.profile_id == job.tool_profile
                and value.solver_kind == job.solver_kind
                and value.capability_sha256 == job.capability_sha256
            ),
            None,
        )
        if active is None:
            raise ValueError("development debug capability is not active")
        return active


def _earliest_diagnostic(
    *, terminal: str, exit_code: int, error: str, log: str, output_count: int
) -> tuple[str, str]:
    combined = (error + "\n" + log).lower()
    if "diagnostic collection failed:" in error:
        return "output_contract", "Diagnostic capture is incomplete; original logs retained at the runner."
    if terminal == "succeeded" and exit_code == 0:
        return (
            "complete",
            "Direct-solver development check completed within its bounded contract.",
        )
    if exit_code == 124 or "wall_time_exceeded" in combined:
        return "resource_limit", "Direct-solver debug reached a fixed resource limit."
    diagnostic = _source_diagnostic(error=error, log=log)
    message = diagnostic.message.lower() if diagnostic is not None else ""
    layers = (
        ("parser", ("syntax error", "parse error", "unexpected token", "space required after", "failure during syntax check", "can't read", "can't use", "invalid command", "wrong # args")),
        (
            "initialization",
            ("initialization failed", "initial solution failed", "unknown contact", "unknown region", "no regions specified"),
        ),
        (
            "numerical",
            ("convergence", "converge", "diverg", "newton", "residual", "time step"),
        ),
    )
    for layer, markers in layers:
        if any(marker in message for marker in markers):
            return layer, f"Direct-solver debug stopped at the {layer} layer."
    if exit_code == 0 and any(
        marker in combined
        for marker in ("required output missing", "output exceeds", "regular file")
    ):
        return (
            "output_contract",
            "Direct-solver debug completed but failed the declared output contract.",
        )
    if exit_code < 0:
        return (
            "runtime",
            f"Direct-solver debug terminated by signal {-exit_code} before output validation.",
        )
    return "runtime", "Direct-solver debug failed at an unclassified runtime layer."


def _source_diagnostic(*, error: str, log: str) -> TCADSourceDiagnostic | None:
    """Extract one bounded, public-safe locator hint from a solver diagnostic."""

    combined = "\n".join(value for value in (error, log) if value)
    combined = re.sub(r"\x1b\[[0-9;]*m", "", combined)
    if not combined:
        return None
    error_markers = (
        "can't read", "can't use", "expected ", "invalid command", "wrong # args",
        "domain error", "syntax error", "parse error", "unexpected token",
        "space required after", "failure during syntax check", "no regions specified",
        "initialization failed", "initial solution failed", "unknown region", "unknown contact",
        "failed to converge", "convergence failed", "newton failed", "divergence detected",
        "time step too small", "required output missing", "output exceeds",
    )
    lines = combined.splitlines(keepends=True)
    message_index = next((
        index for index, line in enumerate(lines)
        if any(marker in line.lower() for marker in error_markers)
    ), None)
    if message_index is None:
        if not error.strip():
            return None
        # An unclassified adapter error provides no source locator in the log.
        lines = error.splitlines(keepends=True)
        message_index = 0
    message = lines[message_index].strip()
    fragment_boundary = r"(?im)^--- (?:scidiscovery solver diagnostic|bounded (?:diagnostic|log) omission)"
    prefix = re.split(fragment_boundary, "".join(lines[:message_index]))[-1]
    context = "".join(lines[message_index:])
    context = re.split(fragment_boundary + r"|^Checking syntax of ", context)[0]
    entrypoint_matches = list(re.finditer(
        r"(?im)^Checking syntax of "
        r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.cmd)\s*:",
        prefix,
    ))
    source_relative_path = (
        entrypoint_matches[-1].group(1) if entrypoint_matches else None
    )
    file_line = re.search(r'(?i)\bfile\s+"([^"]+)"\s+line\s+(\d+)', context)
    reported_line = int(file_line.group(2)) if file_line is not None else None
    if file_line is not None:
        path = file_line.group(1)
        source_relative_path = path if (
            re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.cmd", path)
            and ".." not in Path(path).parts
        ) else None
    if source_relative_path is not None and ".." in Path(source_relative_path).parts:
        source_relative_path = None
    procedure_match = re.search(
        r'(?i)procedure\s+"?([A-Za-z0-9_.:-]+)"?\s+line\s+(\d+)',
        context,
    )
    procedure = procedure_match.group(1) if procedure_match is not None else None
    procedure_line = (
        int(procedure_match.group(2)) if procedure_match is not None else None
    )
    command_match = re.search(
        r'(?is)\bwhile executing\s*\n?\s*"([^"\x00]{1,1024})"',
        context,
    )
    command_excerpt = (
        command_match.group(1).strip() if command_match is not None else None
    )
    message = message[:2048]
    if command_excerpt is not None:
        command_excerpt = command_excerpt[:1024]
    return TCADSourceDiagnostic(
        source_relative_path=source_relative_path,
        reported_line=reported_line,
        line_basis=(
            "entrypoint_solver_reported"
            if source_relative_path is not None and reported_line is not None
            else "solver_reported"
            if reported_line is not None
            else "log_only"
        ),
        procedure=procedure,
        procedure_line=procedure_line,
        message=message,
        command_excerpt=command_excerpt,
    )


def _sanitize_log(raw: bytes, *, max_chars: int, truncate: bool = True) -> str:
    text = raw.decode("utf-8", errors="replace")
    if truncate and len(text) > max_chars:
        half = max_chars // 2
        text = (
            text[:half]
            + "\n--- bounded diagnostic omission ---\n"
            + text[-half:]
        )
    return _redact_log(text)


def _read_descriptor(
    descriptor: LocalFileDescriptor, *, max_bytes: int
) -> bytes:
    path = Path(descriptor.local_path).expanduser().absolute()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(path, flags)
    except OSError as error:
        raise ValueError("development debug output is unavailable") from error
    try:
        metadata = os.fstat(handle)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise ValueError("development debug output exceeds its bound")
        with os.fdopen(handle, "rb", closefd=False) as stream:
            raw = stream.read(max_bytes + 1)
    finally:
        os.close(handle)
    if (
        len(raw) != descriptor.size_bytes
        or len(raw) > max_bytes
        or hashlib.sha256(raw).hexdigest() != descriptor.sha256
    ):
        raise ValueError("development debug output differs from its descriptor")
    return raw


def _validate_output_name(value: str) -> None:
    parts = Path(value).parts
    if (
        len(value) > 512
        or Path(value).is_absolute()
        or not parts
        or len(parts) > 8
        or "\\" in value
        or any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", part) is None
            for part in parts
        )
    ):
        raise ValueError("development debug output name is unsafe")


def _descriptor(name: str, path: Path, media_type: str) -> LocalFileDescriptor:
    raw = path.read_bytes()
    return LocalFileDescriptor(
        name=name,
        local_path=str(path.absolute()),
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        media_type=media_type,
    )


def _write_new(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o440,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


__all__ = ["TCADDevelopmentDebugBridge"]
