"""Host lifecycle contracts; fake transport creates no processes or containers."""
import importlib.metadata
import json
from pathlib import Path
import sys
import time

import pytest


# The optional source package remains optional for the core test configuration.
sys.path.insert(0, str(Path(__file__).parents[2] / "plugins/harbor_execution"))

from harbor_execution.config import configure
from harbor_execution.docker_backend import BackendError, FileMissing, FileChanged, FileInvalid
from harbor_execution.experiments import HarborExecution, REGISTRATION, build
from scidiscovery.operations.execution import LocalFileDescriptor
from scidiscovery.operations.runtime_plugins import RuntimePluginContext
from scidiscovery.research_loop import experiments as e, protocol


CONTAINER = "a" * 64


class FakeBackend:
    def __init__(self):
        self.launches = []
        self.reads = []
        self.cancels = []
        self.native = {}
        self.files = {}
        self.lose_reply = False
        self.on_read = None
        self.after_read = None
        self.before_launch = None
        self.on_cancel = None
        self.timeouts = []

    def state(self, call_id):
        return self.native.get(call_id, dict(call_id=call_id, state="unknown", returncode=None,
            cleanup_confirmed=False, native_identity=None))

    def launch(self, call_id, code_root, entry, arguments, *, deadline_epoch):
        if self.before_launch is not None:
            self.before_launch(call_id)
        self.launches.append((call_id, code_root, entry, arguments, deadline_epoch))
        self.native[call_id] = dict(call_id=call_id, state="running", returncode=None,
            cleanup_confirmed=False, native_identity=dict(container_id=CONTAINER))
        if self.lose_reply:
            raise BackendError("private transport target " + CONTAINER)
        return self.native[call_id]

    def terminal(self, call_id, state="succeeded"):
        self.native[call_id] = dict(call_id=call_id, state=state, returncode=0 if state == "succeeded" else -15,
            started_at=10, finished_at=11, cleanup_confirmed=True,
            native_identity=dict(container_id=CONTAINER, supervisor_pid=123))

    def cancel(self, call_id):
        self.cancels.append(call_id)
        if self.on_cancel is not None:
            return self.on_cancel(call_id)
        self.terminal(call_id, "cancelled")
        return self.native[call_id]

    def read_file(self, call_id, relative, destination, *, max_bytes, timeout_seconds):
        self.reads.append((call_id, relative))
        self.timeouts.append(timeout_seconds)
        assert 0 < timeout_seconds < float("inf")
        if self.on_read is not None:
            self.on_read(relative)
        if relative not in self.files:
            raise FileMissing("private missing file")
        data = self.files[relative]
        if isinstance(data, Exception):
            raise data
        assert max_bytes is None  # Legacy byte configuration never caps originals.
        with destination.open("xb") as stream:
            stream.write(data)
        destination.chmod(0o400)
        if self.after_read is not None:
            self.after_read(relative)


@pytest.fixture
def registered(tmp_path, monkeypatch):
    existing = list(importlib.metadata.entry_points().select(group=e.GROUP, name="harbor_execution"))
    if not existing:
        metadata = tmp_path / "discovery/harbor_execution_source.dist-info"
        metadata.mkdir(parents=True)
        (metadata / "METADATA").write_text("Metadata-Version: 2.1\nName: harbor-execution-source\nVersion: 0\n")
        (metadata / "entry_points.txt").write_text(
            "[scidiscovery.execution_interfaces]\nharbor_execution = harbor_execution.experiments:REGISTRATION\n")
        monkeypatch.syspath_prepend(str(metadata.parent))


def case(tmp_path, registered, extra=None):
    scope = dict(step=1, role="W", job_ref=dict(path=str(tmp_path / "job.json")),
        execution_key=dict(path=str(tmp_path / "intent.json")), mechanism_ref=None,
        handoff_protocol=protocol.CURRENT)
    config = e.freeze(dict(configure(dict(container_id=CONTAINER, container_root="/tmp/scid-trial", **(extra or {})), None,
        binding="experiment"), record_dir=str(tmp_path / "records"), scope=scope,
        admission=dict(version=1, mode="hard")), "harbor_execution:experiment")
    backend = FakeBackend()
    adapter = HarborExecution({key: value for key, value in config.items() if key != "execution_interface"},
        frozen_interface=config["execution_interface"], backend=backend)
    source = tmp_path / "experiment.py"
    source.write_text("print('scientific stdout')\n")
    descriptor = LocalFileDescriptor(name="0001-call", local_path=str(source), size_bytes=source.stat().st_size,
        media_type="text/x-python")
    prepared = adapter.prepare(descriptor, preparation_profile="research_loop.v1", exchange_directory=adapter.records.root)
    accepted = adapter.records.accept(prepared, adapter.acceptance_metadata(prepared), config["execution_interface"])
    return config, adapter, backend, prepared, accepted


def dispatch(adapter, prepared, accepted):
    adapter.dispatch_lease(accepted)
    return adapter.submit(prepared)


def scientific_files(backend):
    backend.files = {
        "control/stdout.raw": b"scientific stdout\n",
        "control/stderr.raw": b"",
        "work/artifacts/result.json": e.encoded(dict(version=1,
            metrics=[dict(name="loss", value=0.25, direction="minimize")],
            outputs=[dict(path="artifacts/regressor.py", label="candidate")],
            candidates=[dict(label="model one", artifact="artifacts/regressor.py", metric="loss")])),
        "work/artifacts/regressor.py": b"def predict(x): return x\n",
    }


def test_configuration_and_registration_are_local_only(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("configuration must not invoke Docker")
    monkeypatch.setattr("subprocess.Popen", forbidden)
    value = configure(dict(container_id=CONTAINER, container_root="/tmp/trial"), None, binding="experiment")
    assert value["python_argv"] == ["/usr/local/bin/python", "-B", "-u"]
    assert REGISTRATION.plugin_id == "harbor_execution"
    with pytest.raises(ValueError, match="outside /app"):
        configure(dict(container_id=CONTAINER, container_root="/app/scratch"), None, binding="experiment")
    context = RuntimePluginContext("harbor_execution", "local_worker", tmp_path / "config.json", b"{}", tmp_path,
        execution_host_services=object())
    with pytest.raises(e.UnsupportedExecution, match="formal reviewed-subject"):
        build(context)


def test_admission_and_lost_reply_reconcile_same_call_without_resubmission(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    assert adapter.status_details(prepared.name)["state"] == "accepted"
    assert adapter.lookup_submission(prepared) is None
    with pytest.raises(e.UnsupportedExecution, match="dispatch lease"):
        adapter.submit(prepared)
    backend.lose_reply = True
    backend.before_launch = lambda call: pytest.fail("dispatch intent must precede Docker effects") if not (
        adapter.records.directory(call) / "harbor-dispatch.json").exists() else None
    assert dispatch(adapter, prepared, accepted) == (prepared.name, "unknown")
    assert (adapter.records.directory(prepared.name) / "harbor-dispatch.json").exists()
    assert adapter.lookup_submission(prepared) == (prepared.name, "running")
    assert adapter.submit(prepared) == (prepared.name, "running")
    assert len(backend.launches) == 1
    assert backend.reads == []
    # A missing native marker after an uncertain dispatch remains unknown.
    backend.native.clear()
    assert adapter.lookup_submission(prepared) == (prepared.name, "unknown")
    adapter.dispatch_lease(accepted)
    assert adapter.submit(prepared) == (prepared.name, "unknown")
    assert len(backend.launches) == 1


def test_call_owner_loads_acceptance_once_and_validation_is_pure(tmp_path, registered, monkeypatch):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    original = adapter.records.accepted
    calls = []
    def loaded(call_id):
        calls.append(call_id)
        return original(call_id)
    def forbidden(*args, **kwargs):
        pytest.fail("a validated call consumer must not reload acceptance")
    monkeypatch.setattr(adapter.records, "accepted", loaded)
    monkeypatch.setattr(adapter.records, "external", forbidden)
    adapter.dispatch_lease(accepted)
    assert calls == [prepared.name]
    calls.clear()
    assert adapter.status_details(prepared.name)["state"] == "accepted"
    assert calls == [prepared.name]
    # Once facts have been acquired, the validator cannot reopen any records.
    with monkeypatch.context() as pure:
        pure.setattr(e, "read", forbidden)
        pure.setattr(adapter.records, "accepted", forbidden)
        assert adapter._check_accepted(accepted, None) == accepted
    adapter.submit(prepared)
    calls.clear()
    assert adapter.status_details(prepared.name)["state"] == "running"
    assert calls == [prepared.name]
    calls.clear()
    assert adapter.cancel_original(prepared.name) == "cancelled"
    assert calls == [prepared.name]
    receipts = {}
    read = e.read
    def receipt_read(path, *args, **kwargs):
        name = Path(path).name
        if name in {"accepted.json", "submission.json", "harbor-dispatch.json", "terminal.json"}:
            receipts[name] = receipts.get(name, 0) + 1
        return read(path, *args, **kwargs)
    monkeypatch.setattr(e, "read", receipt_read)
    assert adapter.status_details(prepared.name)["state"] == "cancelled"
    assert receipts == {name: 1 for name in ("accepted.json", "submission.json", "harbor-dispatch.json", "terminal.json")}


def test_terminal_collection_preserves_candidates_and_downloads_once(tmp_path, registered, monkeypatch):
    config, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.terminal(prepared.name)
    assert adapter.status_details(prepared.name)["state"] == "succeeded"
    assert backend.reads == []  # Query observes native facts only.
    parsed = []
    original = __import__("scidiscovery.research_loop.local_experiments", fromlist=["LocalExecution"]).LocalExecution.parse_result
    def parse(value, outputs):
        parsed.append(value)
        return original(value, outputs)
    monkeypatch.setattr("harbor_execution.experiments.LocalExecution.parse_result", parse)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success" and completed["native_state"] == "succeeded"
    assert completed["result"]["candidates"] == [dict(label="model one", artifact="artifacts/regressor.py", metric="loss")]
    reads = list(backend.reads)
    assert len(reads) == 4 and len(parsed) == 1
    proofs = {}
    original_proof = e.proof
    def proof_once(path, *args, **kwargs):
        proofs[str(path)] = proofs.get(str(path), 0) + 1
        return original_proof(path, *args, **kwargs)
    with monkeypatch.context() as replay:
        replay.setattr(e, "proof", proof_once)
        assert adapter.result(prepared.name) == completed
    assert proofs == {item["path"]: 1 for item in [*completed["proofs"], *completed["outputs"]]}
    collected = adapter.collect_original(prepared.name)
    assert len(collected) == 4 and backend.reads == reads and len(parsed) == 1
    observation = e.record_observation(config, adapter, prepared.name)
    assert observation["status"] == "known_success"
    names = {item["name"] for item in observation["scientific_outputs"]}
    assert {"regressor.py", "result.json", "execution-logs/stdout.txt", "execution-logs/stderr.txt"} <= names
    public = json.dumps(adapter.public_result(completed))
    details = json.dumps(adapter.status_details(prepared.name))
    assert CONTAINER not in public + details
    assert "native_identity" not in public + details
    assert "/tmp/scid-trial" not in public + details


def test_missing_output_keeps_observations_and_allows_corrected_core_dispatch(tmp_path, registered, monkeypatch):
    config, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.files.pop("work/artifacts/regressor.py")
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["metrics"][0]["name"] == "loss"
    assert completed["result"]["candidates"] == []
    assert any(item["code"] == "declared_output_missing" for item in completed["result"]["diagnostics"])
    directory = adapter.records.directory(prepared.name)
    assert (directory / "terminal.json").exists() and (directory / "completed.json").exists()
    assert adapter.status_details(prepared.name)["state"] == "succeeded"
    assert backend.reads.count((prepared.name, "control/stdout.raw")) == 1
    reads = list(backend.reads)
    assert adapter.result(prepared.name) == completed and backend.reads == reads
    adapter.collect_original(prepared.name)
    assert e.record_observation(config, adapter, prepared.name)["status"] == "known_success"
    # Exercise real common next-call admission, rather than only adapter status.
    monkeypatch.setattr("harbor_execution.experiments.DockerBackend", lambda config: backend)
    corrected = tmp_path / "corrected.py"
    corrected.write_text("print('corrected experiment')\n")
    next_call = e.dispatch(config, corrected)
    assert next_call["status"] == "running" and next_call["call_id"] != prepared.name
    assert len(backend.launches) == 2


@pytest.mark.parametrize("failure,code", [
    (FileInvalid("private file facts"), "declared_output_file_invalid"),
])
def test_permanent_invalid_artifact_preserves_other_observations(tmp_path, registered, failure, code):
    config, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    value = json.loads(backend.files["work/artifacts/result.json"])
    value["outputs"].append(dict(path="artifacts/good.py"))
    value["candidates"].append(dict(artifact="artifacts/good.py", metric="loss"))
    backend.files["work/artifacts/result.json"] = e.encoded(value)
    backend.files["work/artifacts/regressor.py"] = failure
    backend.files["work/artifacts/good.py"] = b"good candidate\n"
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["metrics"] == value["metrics"]
    assert completed["result"]["candidates"] == [dict(artifact="artifacts/good.py", metric="loss")]
    assert any(row["code"] == code for row in completed["result"]["diagnostics"])
    assert e.record_observation(config, adapter, prepared.name)["status"] == "known_success"
    assert "private" not in json.dumps(adapter.public_result(completed))


@pytest.mark.parametrize("result_file", [b'{"version":1', FileInvalid("private")])
def test_invalid_optional_result_keeps_known_native_completion(tmp_path, registered, result_file):
    config, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.files["work/artifacts/result.json"] = result_file
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success" and completed["result"]["status"] == "invalid"
    assert completed["result"]["metrics"] == []
    assert completed["result"].get("candidates", []) == []
    assert e.record_observation(config, adapter, prepared.name)["status"] == "known_success"


@pytest.mark.parametrize("path", ["../../control/identity.json", "artifacts/good.py/child"])
def test_unsafe_or_conflicting_output_paths_complete_with_diagnostics(tmp_path, registered, path):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    value = dict(version=1, metrics=[dict(name="loss", value=1)],
        outputs=[dict(path="artifacts/good.py"), dict(path=path)],
        candidates=[dict(artifact="artifacts/good.py"), dict(artifact=path)])
    backend.files["work/artifacts/result.json"] = e.encoded(value)
    backend.files["work/artifacts/good.py"] = b"valid\n"
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["candidates"] == [dict(artifact="artifacts/good.py")]
    assert any(row["code"].startswith("declared_output_path_") for row in completed["result"]["diagnostics"])
    assert (prepared.name, "work/" + path) not in backend.reads


def test_missing_nested_output_then_parent_file_is_a_completed_author_conflict(tmp_path, registered):
    config, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.files["work/artifacts/result.json"] = e.encoded(dict(version=1,
        metrics=[dict(name="loss", value=1)],
        outputs=[dict(path="artifacts/a/b"), dict(path="artifacts/a")],
        candidates=[dict(artifact="artifacts/a")]))
    backend.files["work/artifacts/a"] = b"ordinary native parent file\n"
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["metrics"] == [dict(name="loss", value=1)]
    assert completed["result"]["candidates"] == []
    codes = {row["code"] for row in completed["result"]["diagnostics"]}
    assert {"declared_output_missing", "declared_output_path_conflict"} <= codes
    assert (prepared.name, "work/artifacts/a") not in backend.reads
    assert e.record_observation(config, adapter, prepared.name)["status"] == "known_success"


@pytest.mark.parametrize("collection_seconds,trial_seconds,bound", [(120, 30, 30), (1, 60, 1)])
def test_transfer_receives_remaining_collection_and_original_trial_budget(tmp_path, registered,
        collection_seconds, trial_seconds, bound):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered,
        dict(collection_seconds=collection_seconds, deadline_epoch=time.time() + trial_seconds))
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.terminal(prepared.name)
    assert adapter.result(prepared.name)["status"] == "known_success"
    assert len(backend.timeouts) == 4
    assert all(0 < seconds <= bound for seconds in backend.timeouts)


def test_recovery_adopts_atomic_final_after_interrupted_collection(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.terminal(prepared.name)
    def interrupted(relative):
        backend.after_read = None
        raise BackendError("host response lost after atomic transfer")
    backend.after_read = interrupted
    result = adapter.result(prepared.name)
    assert result["status"] == "unknown" and "Output transfer is unavailable" in result["note"]
    assert adapter.status(prepared.name) == "succeeded"
    adapter.collect_original(prepared.name)
    assert adapter.result(prepared.name)["status"] == "known_success"
    assert backend.reads.count((prepared.name, "control/stdout.raw")) == 1
    assert len(backend.launches) == 1


def test_recovery_after_collection_receipt_before_completion(tmp_path, registered, monkeypatch):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.terminal(prepared.name)
    publish = adapter.records.publish
    def interrupted(call_id, filename, value):
        if filename == "completed.json":
            monkeypatch.setattr(adapter.records, "publish", publish)
            raise InterruptedError("host publication interrupted")
        return publish(call_id, filename, value)
    monkeypatch.setattr(adapter.records, "publish", interrupted)
    with pytest.raises(InterruptedError):
        adapter.result(prepared.name)
    reads = list(backend.reads)
    assert (adapter.records.directory(prepared.name) / "collected.json").exists()
    assert adapter.result(prepared.name)["status"] == "known_success"
    assert backend.reads == reads and len(backend.launches) == 1


def test_collection_writer_and_cancel_terminal_races(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.terminal(prepared.name)
    observed = []
    def during_transfer(relative):
        if not observed:
            observed.append(adapter.result(prepared.name))
            assert not (adapter.records.directory(prepared.name) / "completed.json").exists()
    backend.on_read = during_transfer
    assert adapter.result(prepared.name)["status"] == "known_success"
    assert observed[0]["status"] == "unknown"
    assert adapter.cancel_original(prepared.name) == "succeeded"
    assert backend.cancels == []


def test_cancelled_native_state_and_private_target_mismatch(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    assert adapter.cancel_original(prepared.name) == "cancelled"
    result = adapter.result(prepared.name)
    assert result["status"] == "known_failed" and result["native_state"] == "cancelled"
    assert adapter.status(prepared.name) == "cancelled"
    directory = adapter.records.directory(prepared.name)
    intent = json.loads(e.read(directory / "harbor-dispatch.json"))
    intent["target"]["container_id"] = "b" * 64
    (directory / "harbor-dispatch.json").unlink()  # Deliberate test-only control corruption.
    e.write(directory / "harbor-dispatch.json", intent)
    with pytest.raises(e.IntegrityError, match="dispatch intent"):
        adapter.lookup_submission(prepared)


def test_success_that_races_cancel_keeps_original_success(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    def finished_before_signal(call_id):
        backend.terminal(call_id)
        return backend.state(call_id)
    backend.on_cancel = finished_before_signal
    assert adapter.cancel_original(prepared.name) == "succeeded"
    assert adapter.status_details(prepared.name)["state"] == "succeeded"
    assert backend.cancels == [prepared.name]


def test_real_logs_required_and_missing_result_is_explicit(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    backend.terminal(prepared.name)
    backend.files["control/stdout.raw"] = b""
    assert adapter.result(prepared.name)["status"] == "unknown"
    assert not (adapter.records.directory(prepared.name) / "completed.json").exists()
    backend.files["control/stderr.raw"] = b""
    adapter.collect_original(prepared.name)
    result = adapter.result(prepared.name)
    assert result["status"] == "known_success" and result["result"]["status"] == "missing"
    assert len(result["proofs"]) == 5
    assert adapter.collect_original(prepared.name)


def test_legacy_byte_limits_do_not_reject_logs_result_or_declared_artifact(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered, dict(
        max_log_bytes=1, max_file_bytes=1, max_total_bytes=1, max_result_bytes=1))
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.files["control/stderr.raw"] = b"complete stderr\n"
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["status"] == "available"
    assert completed["result"]["candidates"]
    for item in [*completed["proofs"], *completed["outputs"]]:
        relative = {"stdout.raw": "control/stdout.raw", "stderr.raw": "control/stderr.raw"}.get(
            Path(item["path"]).name, "work/artifacts/" + Path(item["path"]).name)
        if relative in backend.files:
            assert Path(item["path"]).read_bytes() == backend.files[relative]
    reads = list(backend.reads)
    assert adapter.result(prepared.name) == completed and backend.reads == reads
    assert len(backend.launches) == 1


def test_changed_original_keeps_same_call_unresolved_without_relaunch(tmp_path, registered):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    backend.files["work/artifacts/regressor.py"] = FileChanged("private original mutation")
    backend.terminal(prepared.name)
    assert adapter.result(prepared.name)["status"] == "unknown"
    assert adapter.status(prepared.name) == "succeeded"
    assert adapter.submit(prepared) == (prepared.name, "succeeded")
    assert len(backend.launches) == 1
    backend.files["work/artifacts/regressor.py"] = b"stable original\n"
    assert adapter.collect_original(prepared.name)
    assert adapter.result(prepared.name)["status"] == "known_success"
    assert len(backend.launches) == 1


def test_compact_result_declaration_bound_keeps_complete_original_and_known_execution(
        tmp_path, registered, monkeypatch):
    _, adapter, backend, prepared, accepted = case(tmp_path, registered)
    dispatch(adapter, prepared, accepted)
    scientific_files(backend)
    original = backend.files["work/artifacts/result.json"]
    monkeypatch.setattr("harbor_execution.experiments._RESULT_DECLARATION_BYTES", 16)
    backend.terminal(prepared.name)
    completed = adapter.result(prepared.name)
    assert completed["status"] == "known_success"
    assert completed["result"]["status"] == "unavailable"
    assert completed["result"]["diagnostic"] == "result_declaration_document_bound"
    retained = next(item for item in completed["outputs"] if item["name"] == "result.json")
    assert Path(retained["path"]).read_bytes() == original
    assert len(adapter.collect_original(prepared.name)) == 3
    assert len(backend.launches) == 1
