"""Managed host receipts for experiments in one original task container."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
import time

from scidiscovery.operations.execution import (AdapterCapability, ExecutionInterfaceRegistration,
    LifecycleCapabilities, LocalFileDescriptor, execution_status)
from scidiscovery.operations.runtime_plugins import RuntimePluginContribution, RuntimePluginFactory
from scidiscovery.plugin_runtime.workspace import atomic_json
from scidiscovery.research_loop import experiments as e, storage_policy as storage
from scidiscovery.research_loop.experiment_client import frozen_code_directory_capability
from scidiscovery.research_loop.local_experiments import LocalExecution, RESULT_SCHEMA, artifact_directory

from .config import configure, validate
from .docker_backend import BackendError, DockerBackend, FileMissing, FileInvalid


# result.json is the compact result declaration/handoff. Its original is
# always transferred in full before this separate document decoding bound.
_RESULT_DECLARATION_BYTES = e.MAX_BYTES
_TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
_STATES = _TERMINAL | {"submitted", "running", "cancelling", "unknown"}
_BACKEND_FIELDS = ("container_id", "docker_argv", "python_argv", "container_root", "max_seconds",
    "deadline_epoch", "environment", "user", "max_log_bytes", "runner_mode")


@dataclass(frozen=True)
class _CallFacts:
    accepted: dict
    submission: dict | None
    intent: dict | None
    terminal: dict | None


class HarborExecution:
    terminal_evidence_kind = "adapter_result"
    result_protocol = e.LOCAL_RESULT_PROTOCOL

    def __init__(self, config, *, frozen_interface=None, backend=None):
        self.config = validate(config, host=True)
        self.frozen_interface = frozen_interface
        self.records = e.Records(dict(self.config, execution_interface=frozen_interface)
            if frozen_interface is not None else self.config)
        # Preserve the existing exact symbolic-call target bytes: the optional
        # new mode is bound only when it was explicitly configured.
        self._target = {key: self.config.get(key) for key in _BACKEND_FIELDS
                        if key != "runner_mode" or key in self.config}
        self.backend = backend if backend is not None else DockerBackend(
            {key: self.config[key] for key in _BACKEND_FIELDS if key in self.config})
        self._lease = None

    @staticmethod
    def lifecycle_capabilities():
        return LifecycleCapabilities(lookup_submission=True, query=True, cancel=True,
            collect=True, atomic_admission=True)

    @staticmethod
    def capabilities():
        contract = dict(kind="original_task_container", cwd="per_call_scratch_outside_app",
            code="complete_host_frozen_code_directory", native_permissions="original_task_container",
            optional_result=dict(protocol=e.LOCAL_RESULT_PROTOCOL, path="artifacts/result.json",
                schema=RESULT_SCHEMA),
            limitations="Transport completion does not qualify scientific results. Read task data by its original absolute public path. Do not modify /app during experiments; final delivery owns the selected task artifact.")
        return (AdapterCapability("task_container", "execution", "scidiscovery.task-container.v1", 1,
            "application/json", e.encoded(contract), contract), frozen_code_directory_capability())

    @e.record_effect
    def prepare(self, payload, *, preparation_profile, exchange_directory):
        if preparation_profile != "research_loop.v1" or Path(exchange_directory) != self.records.root:
            raise ValueError("Exact managed preparation context required")
        if e.proof(payload.local_path)["size_bytes"] != payload.size_bytes:
            raise e.IntegrityError("Prepared code export changed")
        return payload

    def acceptance_metadata(self, payload, *, parent=None, new=False, change=None, command_options=None):
        options = command_options or {}
        if not isinstance(options, dict) or set(options) - {"arguments"}:
            raise e.CallerError("Harbor experiments use the frozen code directory and a container scratch working directory. Use --script code/<entry> and optional arguments after --; omit --experiment-dir.")
        arguments = options.get("arguments", [])
        if not isinstance(arguments, list) or any(not isinstance(item, str) or "\0" in item for item in arguments):
            raise e.CallerError("Script arguments must be strings without NUL bytes; pass them after --.")
        metadata = dict(parent=dict(status="unknown", candidate_id=None, source="none"),
            change_intent=None, arguments=list(arguments), config_record=self.config,
            local_observation_version=1)
        if parent is not None or change is not None:
            metadata["note"] = "This executor records ordinary experiments; parent and change-intent flags do not declare candidate lineage."
        return metadata

    def _check_accepted(self, accepted, submission):
        if (accepted.get("scope") != self.config["scope"]
                or accepted.get("config_record") != self.config
                or self.frozen_interface is not None and accepted.get("interface") != self.frozen_interface):
            raise e.IntegrityError("Original container execution binding changed")
        if submission is not None:
            if (not isinstance(submission, dict) or submission.get("call_id") != accepted["call_id"]
                    or submission.get("scope") != accepted["scope"]
                    or submission.get("mechanism_binding") != accepted.get("mechanism_binding")
                    or submission.get("external_run_id") != accepted["call_id"]):
                raise e.IntegrityError("Original external execution identity changed")
        return accepted

    def _load_call(self, call_id):
        accepted = self.records.accepted(call_id)
        directory = self.records.directory(call_id)
        submission = self._load_receipt(directory / "submission.json")
        intent = self._load_receipt(directory / "harbor-dispatch.json")
        terminal = self._load_receipt(directory / "terminal.json")
        self._check_accepted(accepted, submission)
        self._check_intent(accepted, intent)
        self._check_terminal(accepted, intent, terminal)
        return _CallFacts(accepted, submission, intent, terminal)

    @staticmethod
    def _load_receipt(path):
        return json.loads(e.read(path, e.MAX_BYTES)) if path.exists() else None

    def dispatch_lease(self, accepted):
        facts = self._load_call(accepted["call_id"])
        if accepted != facts.accepted:
            raise e.IntegrityError("Dispatch lease differs from durable accepted control")
        self._lease = (accepted["call_id"], accepted["prepared"], facts)

    def _check_intent(self, accepted, value):
        if value is None:
            return
        if (not isinstance(value, dict) or value.get("call_id") != accepted["call_id"] or value.get("scope") != accepted["scope"]
                or value.get("prepared") != accepted["prepared"]
                or value.get("target") != self._target
                or type(value.get("deadline_epoch")) not in (int, float)
                or not 0 < value["deadline_epoch"] < float("inf")):
            raise e.IntegrityError("Original dispatch intent changed")

    def _check_terminal(self, accepted, intent, value):
        if value is None:
            return
        if intent is None:
            raise e.IntegrityError("Native terminal has no original dispatch intent")
        self._validate_native(value, accepted["call_id"])
        if (value.get("scope") != accepted["scope"]
                or value.get("mechanism_binding") != accepted.get("mechanism_binding")
                or value["state"] not in _TERMINAL or value.get("cleanup_confirmed") is not True):
            raise e.IntegrityError("Original native terminal binding changed")

    def _refresh_native(self, facts):
        """Refresh only at a state/submission writer boundary, never acceptance."""
        directory = self.records.directory(facts.accepted["call_id"])
        intent = self._load_receipt(directory / "harbor-dispatch.json")
        terminal = self._load_receipt(directory / "terminal.json")
        self._check_intent(facts.accepted, intent)
        self._check_terminal(facts.accepted, intent, terminal)
        return replace(facts, intent=intent, terminal=terminal)

    def _validate_native(self, value, call_id):
        if (not isinstance(value, dict) or value.get("call_id") != call_id
                or value.get("state") not in _STATES
                or value.get("returncode") is not None and type(value["returncode"]) is not int
                or value.get("state") == "succeeded" and value.get("returncode") != 0):
            raise e.IntegrityError("Original native state is malformed")
        identity = value.get("native_identity")
        if identity is not None and (not isinstance(identity, dict)
                or identity.get("container_id") != self.config["container_id"]):
            raise e.IntegrityError("Original native container identity changed")
        if value["state"] in _TERMINAL and (value.get("cleanup_confirmed") is not True or not identity):
            raise e.IntegrityError("Native terminal lacks confirmed original writer shutdown")

    def _retain_native(self, facts, value):
        accepted = facts.accepted
        self._validate_native(value, accepted["call_id"])
        if value["state"] not in _TERMINAL:
            return value
        with self.records.lease("harbor-state-" + accepted["call_id"] + ".lock"):
            saved = self._load_receipt(self.records.directory(accepted["call_id"]) / "terminal.json")
            self._check_terminal(accepted, facts.intent, saved)
            if saved is not None:
                # The first terminal fact is immutable, including a success that
                # raced cancellation. Later transport observations cannot replace it.
                return saved
            published = dict(value, call_id=accepted["call_id"], scope=accepted["scope"])
            if "mechanism_binding" in accepted:
                published["mechanism_binding"] = accepted["mechanism_binding"]
            self._check_terminal(accepted, facts.intent, published)
            self.records.publish(accepted["call_id"], "terminal.json", published)
            return published

    def _native(self, facts):
        accepted = facts.accepted
        if facts.terminal is not None:
            return facts.terminal
        if facts.intent is None:
            return dict(call_id=accepted["call_id"], state="accepted", returncode=None)
        try:
            value = self.backend.state(accepted["call_id"])
        except BackendError:
            return dict(call_id=accepted["call_id"], state="unknown", returncode=None)
        return self._retain_native(facts, value)

    @e.record_effect
    def lookup_submission(self, submission):
        facts = self._load_call(submission.name)
        accepted = facts.accepted
        if accepted["prepared"] != submission.model_dump(mode="json"):
            raise e.IntegrityError("Lookup differs from the original prepared descriptor")
        if facts.intent is None:
            return None
        state = self._native(facts)["state"]
        return self.records.bind_external(submission.name, submission.name, state)

    def _code(self, accepted):
        source = Path(accepted["script"]["path"])
        if "code_version" in accepted:
            entry = PurePosixPath(accepted["code_version"]["entry"])
            root = source
            for _ in entry.parts:
                root = root.parent
            if root / entry != source:
                raise e.IntegrityError("Accepted export entry changed")
            return root, entry.as_posix()
        # Standalone single-file calls get an isolated launch view. Never upload
        # the call directory containing private acceptance and native receipts.
        root = self.records.directory(accepted["call_id"]) / "harbor-code"
        root.mkdir(mode=0o700, exist_ok=True)
        destination = root / ("source" + source.suffix)
        if not destination.exists():
            os.link(source, destination, follow_symlinks=False)
        if e.proof(destination) != dict(e.proof(source), path=str(destination)):
            raise e.IntegrityError("Standalone code launch view changed")
        return root, destination.name

    @e.record_effect
    def submit(self, submission):
        lease = self._lease
        self._lease = None
        facts = (lease[2] if lease is not None and lease[:2] ==
            (submission.name, submission.model_dump(mode="json")) else self._load_call(submission.name))
        accepted = facts.accepted
        if accepted["prepared"] != submission.model_dump(mode="json"):
            raise e.IntegrityError("Submission differs from its accepted original")
        with self.records.lease("harbor-submit-" + submission.name + ".lock"):
            facts = self._refresh_native(facts)
            if facts.intent is not None:
                state = self._native(facts)["state"]
                return self.records.bind_external(submission.name, submission.name, state)
            if lease is None or lease[:2] != (submission.name, accepted["prepared"]):
                raise e.UnsupportedExecution("No exact accepted dispatch lease; submission is not permitted")
            code_root, entry = self._code(accepted)
            deadline = min(time.time() + self.config["max_seconds"], self.config.get("deadline_epoch") or float("inf"))
            # This durable original precedes the first possibly uncertain Docker
            # effect. Neither lookup nor a repeated submit can issue launch again.
            intent = dict(call_id=submission.name, scope=accepted["scope"], prepared=accepted["prepared"],
                target=self._target, deadline_epoch=deadline)
            e.write(self.records.directory(submission.name) / "harbor-dispatch.json", intent)
            facts = replace(facts, intent=intent)
            try:
                value = self.backend.launch(submission.name, code_root, entry, accepted["arguments"], deadline_epoch=deadline)
                state = self._retain_native(facts, value)["state"]
            except BackendError:
                state = "unknown"
            return self.records.bind_external(submission.name, submission.name, state)

    @e.record_effect
    def status_details(self, call_id):
        facts = self._load_call(call_id)
        accepted = facts.accepted
        native = self._native(facts)
        result = dict(call_id=call_id, state=native["state"], candidate_id=None,
            evaluation_key=None, metric=None, direction=None, parent_candidate=None,
            returncode=native.get("returncode"))
        if native.get("state") in _TERMINAL:
            result["cleanup_confirmed"] = True
            diagnostic = self._collection_feedback(accepted)
            if diagnostic is not None and not (self.records.directory(call_id) / "completed.json").exists():
                result["note"] = diagnostic["note"]
        return result

    @e.record_effect
    def status(self, external_run_id):
        return self._native(self._load_call(external_run_id))["state"]

    @e.record_effect
    def cancel(self, external_run_id):
        return self._cancel(self._load_call(external_run_id))

    def _cancel(self, facts):
        external_run_id = facts.accepted["call_id"]
        with self.records.lease("harbor-submit-" + external_run_id + ".lock"):
            facts = self._refresh_native(facts)
            native = self._native(facts)
            if native["state"] in _TERMINAL:
                return native["state"]
            if facts.intent is None:
                raise e.UnsupportedExecution("This accepted call has no dispatched original to cancel")
            try:
                return self._retain_native(facts, self.backend.cancel(external_run_id))["state"]
            except BackendError:
                return "unknown"

    @e.record_effect
    def cancel_original(self, call_id):
        facts = self._load_call(call_id)
        if facts.submission is None:
            raise e.UnsupportedExecution("Original external execution is unknown; use same-call lookup")
        return self._cancel(facts)

    def _transfer(self, accepted, relative, destination, retained, context, inventory):
        context.remaining_seconds()
        inventory.entry()
        inventory.limit("max_files", inventory.files + 1)
        call_id = accepted["call_id"]
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if destination.resolve() != destination:
            raise e.IntegrityError("Retained transfer destination changed")
        # Only atomic, sealed final names are completed transfer facts. A retry
        # reuses that original; incomplete parts never become scientific files.
        if not destination.exists():
            with storage.mutation(self.records.storage_root(), 0, policy=self.config.get("storage_policy"),
                    context=context, target=destination):
                timeout = context.remaining_seconds()
                if self.config.get("deadline_epoch") is not None:
                    timeout = min(timeout, self.config["deadline_epoch"] - time.time())
                if timeout <= 0:
                    raise TimeoutError("Original trial collection deadline reached")
                self.backend.read_file(call_id, relative, destination, max_bytes=None,
                    timeout_seconds=timeout)
        context.remaining_seconds()
        item = e.proof(destination, context=context)
        if destination.stat().st_mode & 0o777 != 0o400:
            raise e.IntegrityError("Backend transfer did not seal its original")
        retained[relative] = item
        inventory.file(item["size_bytes"])
        return item

    def _completion(self, accepted, native):
        call_id = accepted["call_id"]
        directory = self.records.directory(call_id)
        context = storage.Deadline(self.config["collection_seconds"])
        inventory = storage.Inventory(self.config.get("storage_policy"), context=context)
        retained = {}
        logs = [self._transfer(accepted, "control/" + name + ".raw", directory / (name + ".raw"),
            retained, context, inventory) for name in ("stdout", "stderr")]
        output_root = artifact_directory(self.records.config, call_id)
        result_path = output_root / "result.json"
        outputs = []
        author_diagnostics = []
        try:
            original = self._transfer(accepted, "work/artifacts/result.json", result_path,
                retained, context, inventory)
        except FileMissing:
            result = dict(status="missing", metrics=[], outputs=[],
                detail="Write artifacts/result.json with version=1, metrics and outputs to declare scientific results.")
        except FileInvalid:
            result = dict(status="invalid", metrics=[], outputs=[],
                diagnostic="result_file_invalid",
                detail="Write artifacts/result.json as an ordinary regular JSON file.")
        else:
            outputs.append(dict(original, name="result.json"))
            try:
                def nonfinite(_):
                    raise ValueError("Nonfinite JSON value")
                value = json.loads(e.read(result_path, _RESULT_DECLARATION_BYTES), parse_constant=nonfinite)
            except (ValueError, OverflowError, RecursionError):
                bounded = original["size_bytes"] > _RESULT_DECLARATION_BYTES
                result = dict(status="unavailable" if bounded else "invalid", metrics=[], outputs=[],
                    diagnostic="result_declaration_document_bound" if bounded else "result_json_invalid",
                    detail=("The complete result.json original is retained; its compact declaration exceeds the document decoding bound."
                            if bounded else "result.json must contain strict JSON"), details_ref="result.json")
            else:
                rows = value.get("outputs", []) if isinstance(value, dict) else []
                names = set()
                attempted = set()
                for index, row in enumerate(rows if isinstance(rows, list) else []):
                    field = "outputs[" + str(index) + "]"
                    if index >= self.config["max_output_files"]:
                        author_diagnostics.append(dict(field=field, code="declared_output_count_limit"))
                        continue
                    name = row.get("path") if isinstance(row, dict) else None
                    if not isinstance(name, str):
                        continue
                    path = PurePosixPath(name)
                    try:
                        safe = ("\0" not in name and "\\" not in name and not path.is_absolute()
                            and ".." not in path.parts and path.as_posix() == name and len(path.parts) >= 2
                            and path.parts[0] == "artifacts" and len(name.encode("utf-8")) <= 4096
                            and all(len(part.encode("utf-8")) <= 255 for part in path.parts))
                    except UnicodeError:
                        safe = False
                    if not safe:
                        author_diagnostics.append(dict(field=field, code="declared_output_path_invalid"))
                        continue
                    relative = PurePosixPath(*path.parts[1:]).as_posix()
                    if relative in attempted or relative == "result.json":
                        continue
                    attempted.add(relative)
                    if any(PurePosixPath(relative).is_relative_to(PurePosixPath(other))
                            or PurePosixPath(other).is_relative_to(PurePosixPath(relative))
                            for other in names | {"result.json"}):
                        author_diagnostics.append(dict(field=field, code="declared_output_path_conflict"))
                        continue
                    names.add(relative)
                    try:
                        item = self._transfer(accepted, "work/" + name, output_root / relative,
                            retained, context, inventory)
                    except FileMissing:
                        author_diagnostics.append(dict(field=field, code="declared_output_missing"))
                        continue
                    except FileInvalid:
                        author_diagnostics.append(dict(field=field, code="declared_output_file_invalid"))
                        continue
                    outputs.append(dict(item, name=relative))
                result = LocalExecution.parse_result(value, outputs)
                if author_diagnostics:
                    result.setdefault("diagnostics", []).extend(author_diagnostics)
        proofs = [e.proof(directory / "accepted.json"), e.proof(directory / "harbor-dispatch.json"),
            e.proof(directory / "terminal.json"), *logs]
        completed = dict(call_id=call_id, scope=accepted["scope"], mechanism_binding=accepted.get("mechanism_binding"),
            status=execution_status(native["state"]), native_state=native["state"],
            candidate_id=None, run_id=None, model=None, parent=accepted["parent"], change_intent=None,
            returncode=native.get("returncode"), outputs=outputs, result=result, proofs=proofs,
            note="Observed original task-container execution and declared results; scientific validity remains unqualified.")
        if result.get("status") in {"missing", "invalid", "unavailable"} or result.get("diagnostics"):
            completed["note"] += " Correct the result declaration or unavailable artifacts in a new experiment; retained result diagnostics identify the affected fields. Available observations remain retained."
        truncated = native.get("log_truncated")
        if truncated is True or isinstance(truncated, dict) and any(truncated.values()):
            completed["note"] += " Raw log capture reached its configured byte limit; the retained logs are incomplete."
        collected = tuple(LocalFileDescriptor(name="output-" + str(index), local_path=item["path"],
            size_bytes=item["size_bytes"], media_type="application/octet-stream")
            for index, item in enumerate([*logs, *outputs]))
        collection = dict(call_id=call_id, scope=accepted["scope"],
            outputs=[item.model_dump(mode="json") for item in collected])
        if "mechanism_binding" in accepted:
            collection["mechanism_binding"] = accepted["mechanism_binding"]
        collection_path = directory / "collected.json"
        if collection_path.exists():
            if json.loads(e.read(collection_path)) != collection:
                raise e.IntegrityError("Interrupted collection descriptor binding changed")
        else:
            self.records.publish(call_id, "collected.json", collection)
        self.records.publish(call_id, "completed.json", completed)
        return completed

    def _saved_completion(self, facts):
        accepted = facts.accepted
        directory = self.records.directory(accepted["call_id"])
        path = directory / "completed.json"
        if not path.exists():
            return None
        value = json.loads(e.read(path))
        native = facts.terminal
        if (native is None or any(value.get(key) != accepted.get(key) for key in ("call_id", "scope", "mechanism_binding"))
                or value.get("native_state") != native["state"] or value.get("status") != execution_status(native["state"])):
            raise e.IntegrityError("Completed execution binding changed")
        for item in [*value["proofs"], *value.get("outputs", [])]:
            actual = e.proof(item["path"])
            if any(actual.get(key) != item[key] for key in ("path", "size_bytes", "file_stat") if key in item):
                raise e.IntegrityError("Completed execution original changed")
        return value

    def _collection_feedback(self, accepted):
        path = self.records.directory(accepted["call_id"]) / "harbor-collection-diagnostic.json"
        if not path.exists():
            return None
        value = json.loads(e.read(path, e.MAX_BYTES))
        if (value.get("call_id") != accepted["call_id"] or value.get("scope") != accepted["scope"]
                or value.get("code") not in {"collection_transport_unavailable", "collection_storage_limit"}
                or not isinstance(value.get("note"), str)):
            raise e.IntegrityError("Collection diagnostic binding changed")
        return value

    def _result(self, facts, *, retry_collection=False):
        accepted = facts.accepted
        call_id = accepted["call_id"]
        saved = self._saved_completion(facts)
        if saved is not None:
            return saved
        native = self._native(facts)
        if native["state"] not in _TERMINAL:
            return dict(call_id=call_id, status="unknown", note="Original execution has no confirmed terminal result; preserve its identity.")
        facts = replace(facts, terminal=native)
        diagnostic = self._collection_feedback(accepted)
        if diagnostic is not None and not retry_collection:
            return dict(call_id=call_id, status="unknown", note=diagnostic["note"])
        # A native terminal marker alone cannot finish collection while another
        # host writer owns file transfer and publication.
        try:
            with self.records.lease("harbor-writer-" + call_id + ".lock"):
                try:
                    return self._saved_completion(facts) or self._completion(accepted, native)
                except (BackendError, TimeoutError, storage.StorageQuotaExceeded) as error:
                    if isinstance(error, storage.StorageQuotaExceeded):
                        code, note = "collection_storage_limit", "Output collection reached the frozen storage limit; preserve the known native outcome and request an explicit retention limit change before retrying this call's collection."
                    else:
                        code, note = "collection_transport_unavailable", "Output transfer is unavailable; preserve the known native outcome and retry collection for this exact call when transport is available."
                        e._diagnostic(error, "Task-container output transfer unavailable", config=self.config)
                    note += " Use query for the known native state; collect retries only this original call's outputs."
                    atomic_json(self.records.directory(call_id) / "harbor-collection-diagnostic.json",
                        dict(call_id=call_id, scope=accepted["scope"], code=code, note=note))
                    return dict(call_id=call_id, status="unknown", note=note)
        except BlockingIOError:
            return dict(call_id=call_id, status="unknown", note="Original output collection still owns its writer lease; retry this exact call.")

    @e.record_effect
    def result(self, call_id):
        return self._result(self._load_call(call_id))

    @e.record_effect
    def collect(self, external_run_id):
        return self._collect(self._load_call(external_run_id))

    def _collect(self, facts):
        external_run_id = facts.accepted["call_id"]
        result = self._result(facts, retry_collection=True)
        if result.get("status") not in {"known_success", "known_failed"}:
            raise e.UnsupportedExecution("Original output collection is unresolved; preserve this call")
        value = json.loads(e.read(self.records.directory(external_run_id) / "collected.json"))
        directory = self.records.directory(external_run_id)
        logs = [item for name in ("stdout", "stderr") for item in result["proofs"]
            if item["path"] == str(directory / (name + ".raw"))]
        if len(logs) != 2:
            raise e.IntegrityError("Collected execution lost its genuine raw logs")
        expected = [LocalFileDescriptor(name="output-" + str(index), local_path=item["path"],
            size_bytes=item["size_bytes"], media_type="application/octet-stream").model_dump(mode="json")
            for index, item in enumerate([*logs, *result.get("outputs", [])])]
        if (value.get("call_id") != external_run_id or value.get("scope") != self.config["scope"]
                or value.get("mechanism_binding") != result.get("mechanism_binding") or value.get("outputs") != expected):
            raise e.IntegrityError("Collected execution descriptor binding changed")
        return tuple(LocalFileDescriptor.model_validate(item) for item in expected)

    @e.record_effect
    def collect_original(self, call_id):
        facts = self._load_call(call_id)
        if facts.submission is None:
            raise e.UnsupportedExecution("Original external execution is unknown; use same-call lookup")
        return self._collect(facts)

    scientific_source = staticmethod(LocalExecution.scientific_source)
    public_result = staticmethod(LocalExecution.public_result)


def build(context):
    if context.execution_host_services is not None:
        raise e.UnsupportedExecution("Harbor experiments support ordinary managed W admission; formal reviewed-subject execution-resource policy is unsupported")
    return RuntimePluginContribution(execution_adapters={"experiment": HarborExecution(
        json.loads(context.config_bytes), frozen_interface=context.frozen_execution_interface)})


REGISTRATION = ExecutionInterfaceRegistration("harbor_execution", "1", RuntimePluginFactory(build, configure=configure),
    scientific_source=HarborExecution.scientific_source, projection_contract="scidiscovery.local-projections.v1")
