#!/usr/bin/env python3
"""Dependency-free Python 3.6 remote runner used through short SSH calls."""

from __future__ import print_function

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import resource
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from datetime import datetime


HEADER_LIMIT = 1024 * 1024
RESPONSE_LIMIT = 16 * 1024 * 1024
PUBLIC_PROFILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,255}$")
PUBLIC_RELEASE_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,255}$")
CREDENTIAL_LABEL_RE = re.compile(
    r"password|passwd|secret|token|credential|license[ _-]?file|snpslmd",
    re.IGNORECASE,
)


class _Cancelled(Exception):
    pass


class _CollectionStopped(RuntimeError):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scidiscovery-tcad-ssh-runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--worker")
    args = parser.parse_args(argv)
    config = _load_config(args.config)
    if args.worker:
        return _run_worker(config, os.path.abspath(args.worker))
    try:
        header = sys.stdin.buffer.readline()
        if not header:
            raise ValueError("remote runner request header is missing")
        request = json.loads(header.decode("utf-8"))
        _handle(config, request, sys.stdin.buffer, sys.stdout.buffer)
        return 0
    except Exception as error:
        sys.stderr.write("remote runner rejected request: %s\n" % error)
        return 1


def _load_config(path):
    path = os.path.abspath(path)
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("runner configuration must be a regular file")
    with open(path, "rb") as source:
        value = json.loads(source.read().decode("utf-8"))
    required = {"exchange_root", "state_root", "result_root", "tools", "agent_execution_policy", "runner", "debug"}
    if not isinstance(value, dict) or not required.issubset(value):
        raise ValueError("runner configuration is incomplete")
    for name in ("exchange_root", "state_root", "result_root"):
        value[name] = os.path.abspath(value[name])
    if value["result_root"] != os.path.join(value["state_root"], "runs"):
        raise ValueError("runner result root must be state_root/runs")
    if not isinstance(value["tools"], list) or not value["tools"]:
        raise ValueError("runner must allow at least one tool")
    profiles = set()
    for tool in value["tools"]:
        required_tool = {"profile_id", "solver_kind", "executable", "arguments", "environment", "release_evidence"}
        allowed_tool = required_tool | {"public_arguments", "public_release_label"}
        missing_tool = required_tool - set(tool)
        unexpected_tool = set(tool) - allowed_tool
        if missing_tool:
            raise ValueError(
                "runner tool profile is missing required fields: %s"
                % ",".join(sorted(missing_tool))
            )
        if unexpected_tool:
            raise ValueError(
                "runner tool profile has unexpected fields: %s"
                % ",".join(sorted(unexpected_tool))
            )
        if tool["solver_kind"] not in {"sprocess", "sdevice", "shell_runner", "deterministic_tool"}:
            raise ValueError("runner tool solver_kind is invalid")
        if tool["profile_id"] in profiles:
            raise ValueError("runner tool profiles must be unique")
        if (
            not isinstance(tool["profile_id"], str)
            or not PUBLIC_PROFILE_RE.fullmatch(tool["profile_id"])
            or CREDENTIAL_LABEL_RE.search(tool["profile_id"])
        ):
            raise ValueError("runner tool profile id is not public-safe")
        profiles.add(tool["profile_id"])
        # Preserve the configured launch name. Synopsys GENERIC-style drivers
        # select the actual tool from argv[0], so resolving a symlink changes
        # program semantics even when the target inode is executable.
        executable = os.path.abspath(os.path.expanduser(tool["executable"]))
        metadata = os.stat(executable)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o111 == 0:
            raise ValueError("runner tool is not executable")
        tool["executable"] = executable
        if not isinstance(tool["arguments"], list) or not isinstance(tool["environment"], dict):
            raise ValueError("runner tool arguments or environment are invalid")
        if not isinstance(tool["release_evidence"], str) or not tool["release_evidence"]:
            raise ValueError("runner tool release evidence is invalid")
        tool.setdefault("public_arguments", [])
        tool.setdefault("public_release_label", None)
        if not isinstance(tool["public_arguments"], list):
            raise ValueError("runner public arguments are invalid")
        remaining = iter(tool["arguments"])
        for public in tool["public_arguments"]:
            if not isinstance(public, str) or not any(
                private == public for private in remaining
            ):
                raise ValueError(
                    "runner public arguments must be an ordered exact subset"
                )
        _public_release_label(tool)
    _validate_policy(value)
    value["max_transfer_bytes"] = value["runner"]["max_transfer_bytes"]
    value["max_concurrent_runs"] = int(value.get("max_concurrent_runs", 1))
    if value["max_transfer_bytes"] < 1 or value["max_concurrent_runs"] < 1:
        raise ValueError("runner bounds must be positive")
    value["_config_path"] = path
    return value


def _policy_snapshot(config):
    return {key: config[key] for key in ("agent_execution_policy", "runner", "debug")}


def _validate_policy(config):
    policy = config["agent_execution_policy"]
    if set(policy) != {"enabled", "max_storage_bytes", "max_wall_time_seconds", "max_total_wall_time_seconds", "outside_limits"}:
        raise ValueError("administrator execution policy fields are invalid")
    if type(policy["enabled"]) is not bool or policy["outside_limits"] not in {"deny", "require_human_approval"}:
        raise ValueError("administrator execution policy mode is invalid")
    for key in ("max_storage_bytes", "max_wall_time_seconds", "max_total_wall_time_seconds"):
        if type(policy[key]) is not int or policy[key] < 1:
            raise ValueError("administrator execution policy bounds are invalid")
    runner = config["runner"]
    required = {"sample_interval_seconds", "terminate_grace_seconds", "transfer_chunk_bytes", "max_transfer_bytes", "max_preview_bytes", "max_manifest_bytes", "max_log_bytes", "max_diagnostic_files", "collection_total_seconds", "collection_file_seconds", "collection_idle_seconds"}
    if set(runner) != required:
        raise ValueError("runner policy fields are invalid")
    for key, value in runner.items():
        if type(value) not in (int, float) or value <= 0 or not __import__("math").isfinite(value):
            raise ValueError("runner policy bounds are invalid")
        if key not in {"sample_interval_seconds", "terminate_grace_seconds", "collection_total_seconds", "collection_file_seconds", "collection_idle_seconds"} and type(value) is not int:
            raise ValueError("runner byte/count bounds must be integers")
    debug = config["debug"]
    required_debug = {"budget_scope", "preflight", "smoke", "initialization", "max_runs", "total_wall_seconds", "max_memory_bytes", "max_storage_bytes", "max_output_file_bytes", "max_output_files", "max_log_bytes", "max_manifest_bytes", "max_response_log_chars", "max_response_bytes"}
    if set(debug) != required_debug:
        raise ValueError("debug policy fields are invalid")
    for key, value in debug.items():
        if key == "budget_scope":
            if value not in {"task", "scientific_subject"}:
                raise ValueError("debug budget scope is invalid")
        elif key in {"preflight", "smoke", "initialization"}:
            if set(value) != {"wall_time_seconds", "max_output_bytes"} or any(type(item) is not int or item < 1 for item in value.values()):
                raise ValueError("debug mode bounds are invalid")
        elif type(value) is not int or value < 1:
            raise ValueError("debug bounds are invalid")


def _validate_authorization(config, job, authorization):
    limits = job["limits"]
    if sum(item["size_bytes"] for item in job["archive_entries"]) > limits["max_storage_bytes"]:
        raise ValueError("declared inputs exceed task storage limit")
    if job["execution_purpose"] == "development_debug":
        if limits["wall_time_seconds"] > config["debug"]["total_wall_seconds"] or limits["max_storage_bytes"] > config["debug"]["max_storage_bytes"]:
            raise ValueError("development job exceeds configured debug allowance")
        return
    snapshot = _policy_snapshot(config)
    policy = snapshot["agent_execution_policy"]
    budget = {"max_storage_bytes": limits["max_storage_bytes"], "wall_time_seconds": limits["wall_time_seconds"]}
    allowed = policy["enabled"] and budget["max_storage_bytes"] <= policy["max_storage_bytes"] and budget["wall_time_seconds"] <= policy["max_wall_time_seconds"]
    outcome = "policy" if allowed else policy["outside_limits"]
    if (isinstance(authorization, dict) and authorization.get("reason") == "cumulative_budget_exceeded"
            and authorization.get("outcome") == "require_human_approval"
            and policy["outside_limits"] == "require_human_approval"):
        outcome = "require_human_approval"
    if (not isinstance(authorization, dict) or outcome == "deny"
            or authorization.get("outcome") != outcome
            or authorization.get("policy_digest") != _control_digest(_canonical(snapshot))
            or authorization.get("policy") != snapshot or authorization.get("budget") != budget
            or authorization.get("allowance") != {"max_storage_bytes": policy["max_storage_bytes"], "wall_time_seconds": policy["max_total_wall_time_seconds"]}
            or authorization.get("per_execution_allowance") != {"max_storage_bytes": policy["max_storage_bytes"], "wall_time_seconds": policy["max_wall_time_seconds"]}
            or authorization.get("outside_allowance") != policy["outside_limits"]
            or not _valid_control_digest(authorization.get("budget_key"))):
        raise ValueError("execution policy changed or control authorization is missing; reauthorize exact request")
    if config["runner"]["max_transfer_bytes"] < limits["max_storage_bytes"]:
        raise ValueError("runner transfer capability is smaller than requested storage budget")


def _handle(config, request, input_stream, output_stream):
    if not isinstance(request, dict) or request.get("schema_version") != 2:
        raise ValueError("invalid remote runner envelope")
    operation = request.get("operation")
    payload = request.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("remote runner payload must be an object")
    if operation == "put":
        _put(config, payload, input_stream)
        _write_response(output_stream, operation, {"stored": True})
    elif operation == "get":
        payload.setdefault("max_bytes",config["max_transfer_bytes"])
        _stream_get(config, payload, output_stream)
    elif operation == "get_archive":
        _stream_get_archive(config, payload, output_stream)
    elif operation == "rpc":
        response = _rpc(config, payload)
        _write_response(output_stream, operation, {"response": response})
    else:
        raise ValueError("unsupported remote runner operation")


def _put(config, payload, stream):
    relative = _safe_relative(payload.get("relative_path"))
    size = int(payload.get("size_bytes", -1))
    if size < 0 or size > config["max_transfer_bytes"]:
        raise ValueError("invalid upload descriptor")
    destination = os.path.join(config["exchange_root"], *relative.split("/"))
    _require_within(destination, config["exchange_root"])
    _mkdir(os.path.dirname(destination))
    descriptor, temporary = tempfile.mkstemp(prefix=".upload.", dir=os.path.dirname(destination))
    try:
        with os.fdopen(descriptor, "wb") as target:
            count = 0
            while count < size:
                block = stream.read(min(config["runner"]["transfer_chunk_bytes"], size - count))
                if not block:
                    raise ValueError("incomplete upload")
                target.write(block)
                count += len(block)
            if stream.read(1):
                raise ValueError("uploaded bytes differ from descriptor")
            target.flush()
            os.fsync(target.fileno())
        if os.path.exists(destination):
            if _file_size(destination, config["runner"]["transfer_chunk_bytes"]) != size:
                raise ValueError("immutable upload already exists with different bytes")
        else:
            os.chmod(temporary, 0o440)
            os.link(temporary, destination)
    finally:
        _unlink(temporary)


def _stream_get(config, payload, output_stream):
    path = os.path.abspath(payload.get("local_path", ""))
    _require_within(path, config["result_root"])
    limit = min(int(payload["max_bytes"]), config["max_transfer_bytes"])
    metadata = os.lstat(path)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > limit:
        raise ValueError("inspection download exceeds file bounds")
    size=metadata.st_size
    with open(path,"rb") as stream:
        before=os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino,before.st_size)!=(metadata.st_dev,metadata.st_ino,size):
            raise ValueError("inspection source changed")
        _write_response(output_stream,"get",{"size_bytes":size})
        sent = 0
        for chunk in iter(lambda: stream.read(config["runner"]["transfer_chunk_bytes"]), b""):
            sent += len(chunk)
            if sent > limit:
                raise ValueError("inspection download changed")
            output_stream.write(chunk)
        after=os.fstat(stream.fileno())
        if sent!=size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise ValueError("inspection source changed during transfer")
    output_stream.flush()


def _stream_get_archive(config, payload, output_stream):
    """Standard tar stream with a frozen list; no remote archive or full-file buffer."""
    files = payload.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("archive download requires file descriptors")
    _write_response(output_stream, "get_archive", {"files": len(files)})
    with tarfile.open(fileobj=output_stream, mode="w|", format=tarfile.GNU_FORMAT) as archive:
        manifest = _canonical(files)
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        for index, item in enumerate(files):
            path = os.path.abspath(item["local_path"])
            _require_within(path, config["result_root"])
            _require_within(os.path.realpath(path), os.path.realpath(config["result_root"]))
            size = item["size_bytes"]
            if type(size) is not int or not 0 <= size <= config["max_transfer_bytes"]:
                raise ValueError("invalid archive download descriptor")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
                    raise ValueError("archive download differs from descriptor")
                info = tarfile.TarInfo("files/%d" % index)
                info.size = size
                archive.addfile(info, stream)
                if stream.read(1):
                    raise ValueError("archive source changed since inspection")
    output_stream.flush()


def _rpc(config, payload):
    request = payload.get("request")
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        raise ValueError("invalid runner RPC request")
    params = request.get("params")
    if request.get("method") != "tools/call" or not isinstance(params, dict):
        raise ValueError("unsupported runner RPC method")
    name = params.get("name")
    arguments = params.get("arguments") or {}
    try:
        if name == "tcad_submission_protocol":
            if arguments:
                raise ValueError("protocol discovery accepts no arguments")
            result = {"version": 2, "submit": "tcad_submit_retirable", "retire": "tcad_retire_submission"}
        elif name == "tcad_execution_policy":
            if arguments:
                raise ValueError("policy discovery accepts no arguments")
            result = _policy_snapshot(config)
        elif name == "tcad_capabilities":
            if arguments:
                raise ValueError("capability discovery accepts no arguments")
            result = _capabilities(config)
        elif name == "tcad_lookup_submission":
            result = _lookup_submission(
                config, arguments.get("submission_id")
            )
        elif name == "tcad_retire_submission":
            result = _retire_submission(config, arguments.get("submission_id"))
        elif name in {"tcad_submit", "tcad_submit_retirable"}:
            result = _submit(config, arguments.get("submission"), arguments.get("authorization"))
        elif name == "tcad_status":
            result = _status(config, arguments.get("run_id"))
        elif name == "tcad_collect":
            result = _collect(config, arguments.get("run_id"))
        elif name == "tcad_inspect_outputs":
            result = _inspect_directory(_run_dir(config, arguments.get("run_id")), arguments.get("relative_path"), min(arguments.get("max_bytes", config["runner"]["max_preview_bytes"]), config["runner"]["max_preview_bytes"]))
        elif name == "tcad_cancel":
            result = _cancel(config, arguments.get("run_id"))
        else:
            raise ValueError("unknown runner tool")
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": {"structuredContent": result},
        }
    except Exception as error:
        return {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "error": {"code": -32000, "message": str(error)},
        }


def _capabilities(config):
    values = []
    for tool in sorted(config["tools"], key=lambda item: item["profile_id"]):
        release_raw = tool["release_evidence"].encode("utf-8")
        values.append(
            {
                "capability_sha256": _capability_sha256(tool),
                "launch_name": os.path.basename(tool["executable"]),
                "private_fixed_argument_count": len(tool["arguments"]),
                "private_fixed_arguments_sha256": _control_digest(
                    _canonical(tool["arguments"])
                ),
                "private_release_evidence_bytes": len(release_raw),
                "private_release_evidence_sha256": _control_digest(release_raw),
                "profile_id": tool["profile_id"],
                "public_arguments": tool.get("public_arguments", []),
                "public_release_label": _public_release_label(tool),
                "schema_version": 2,
                "solver_kind": tool["solver_kind"],
            }
        )
    return {"capabilities": values}


def _public_release_label(tool):
    label = tool.get("public_release_label")
    if label is None:
        profile_id = tool["profile_id"]
        if (
            not isinstance(profile_id, str)
            or not PUBLIC_PROFILE_RE.fullmatch(profile_id)
            or CREDENTIAL_LABEL_RE.search(profile_id)
        ):
            raise ValueError("runner tool profile id is not public-safe")
        label = profile_id
    if (
        not isinstance(label, str)
        or not PUBLIC_RELEASE_LABEL_RE.fullmatch(label)
        or CREDENTIAL_LABEL_RE.search(label)
    ):
        raise ValueError("runner public release label is invalid")
    return label


def _submit(config, descriptor, authorization=None):
    job_raw = _read_bound_descriptor(descriptor, config["exchange_root"])
    job = json.loads(job_raw.decode("utf-8"))
    if _canonical(job) != job_raw:
        raise ValueError("job specification must use canonical JSON")
    _validate_job(job)
    _validate_authorization(config, job, authorization)
    tool = _tool(config, job["tool_profile"])
    if tool["solver_kind"] != job["solver_kind"]:
        raise ValueError("job solver kind differs from configured tool capability")
    if _capability_sha256(tool) != job["capability_sha256"]:
        raise ValueError("job capability differs from configured tool capability")
    archive = os.path.abspath(job["input_archive"]["local_path"])
    _require_within(archive, config["exchange_root"])
    if _file_size(archive, config["runner"]["transfer_chunk_bytes"]) != job["input_archive"]["size_bytes"]:
        raise ValueError("archive differs from its descriptor")
    request_id = job["request_id"]
    run_id = "run_" + request_id
    runs_root = config["result_root"]
    _mkdir(runs_root)
    lock_path = os.path.join(config["state_root"], "submission.lock")
    _mkdir(config["state_root"])
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        run_dir = os.path.join(runs_root, run_id)
        if os.path.isdir(run_dir):
            original=json.loads(_read_regular(os.path.join(run_dir,"submission.json")).decode("utf-8"))
            if original!=job:raise ValueError("request identity differs from original submission")
            return _status(config, run_id)
        if os.path.exists(os.path.join(config["state_root"], "retired_submissions", request_id)):
            raise RuntimeError("this exact submission was permanently retired")
        active = 0
        for name in os.listdir(runs_root):
            existing = os.path.join(runs_root, name)
            # Creation reserves the slot while startup is still asynchronous.
            # An uncertain/failed start is not proof that the slot is free.
            if os.path.isdir(existing) and not os.path.isfile(os.path.join(existing, "done")):
                active += 1
        if active >= config["max_concurrent_runs"]:
            raise RuntimeError("remote runner concurrent limit is reached")
        os.mkdir(run_dir, 0o770)
        _write_new(os.path.join(run_dir,"submission.json"),job_raw,0o440)
        work_dir = os.path.join(run_dir, "work")
        os.mkdir(work_dir, 0o750)
        _extract_archive(archive, job["archive_entries"], work_dir, config["runner"]["transfer_chunk_bytes"])
        runtime = {
            "arguments": list(tool["arguments"]) + list(job["arguments"]),
            "environment": tool["environment"],
            "executable": tool["executable"],
            "execution_purpose": job["execution_purpose"],
            "expected_outputs": job["expected_outputs"],
            "collect_generated_outputs": job["collect_generated_outputs"],
            "archive_entries": job["archive_entries"],
            "limits": job["limits"],
            "runner_policy": config["runner"],
            "authorization": authorization,
        }
        _write_new(os.path.join(run_dir, "runtime.json"), _canonical(runtime), 0o440)
        _write_new(os.path.join(run_dir, "submitted_at"), (_timestamp() + "\n").encode("ascii"), 0o440)
        with open(os.path.join(run_dir, "launcher.log"), "xb") as launcher_log:
            process = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "--config", config["_config_path"], "--worker", run_dir],
                cwd=run_dir,
                stdin=subprocess.DEVNULL,
                stdout=launcher_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        _write_new(os.path.join(run_dir, "launcher_pid"), (str(process.pid) + "\n").encode("ascii"), 0o440)
    return {"run_id": run_id, "state": "accepted", "accepted_at": _timestamp()}


def _lookup_submission(config, submission_id):
    if (
        not isinstance(submission_id, str)
        or len(submission_id) != 32
        or any(character not in "0123456789abcdef" for character in submission_id)
    ):
        raise ValueError("submission identity is invalid")
    run_id = "run_" + submission_id
    run_dir = os.path.join(config["result_root"], run_id)
    if not os.path.isdir(run_dir):
        return {"found": False}
    return dict({"found": True}, **_status(config, run_id))


def _retire_submission(config, submission_id):
    """Fence future submits under the same lock used to create the job."""
    _mkdir(config["state_root"])
    with open(os.path.join(config["state_root"], "submission.lock"), "a+") as lock:
        # Do not wait behind a slow archive extraction/launch. A busy submit
        # has not granted an absence proof; callers retain their reservation.
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Submission is still in progress; retry abandon/cancel later. No retirement was confirmed; budget remains reserved.")
        value = _lookup_submission(config, submission_id)
        if value["found"]:
            return value
        root = os.path.join(config["state_root"], "retired_submissions")
        _mkdir(root)
        marker = os.path.join(root, submission_id)
        if not os.path.exists(marker):
            _write_new(marker, (submission_id + "\n").encode("ascii"), 0o440)
        # The caller may release budget once this acknowledgment arrives.
        # Persist both the marker entry and its directory before acknowledging.
        for directory in (root, config["state_root"]):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return {"found": False, "retired": True, "submission_id": submission_id}


def _status(config, run_id):
    value = _status_value(config, run_id)
    value["progress"] = _job_progress(_run_dir(config, run_id))
    return value


def _status_value(config, run_id):
    run_dir = _run_dir(config, run_id)
    submitted = _read_regular(os.path.join(run_dir, "submitted_at")).decode("ascii").strip()
    orphan_running = False
    if not os.path.isfile(os.path.join(run_dir, "done")):
        orphan_running = _reconcile_worker(config, run_dir)
    done = os.path.isfile(os.path.join(run_dir, "done"))
    if done:
        manifest = json.loads(_read_regular(os.path.join(run_dir, "output_manifest.json"), config["runner"]["max_manifest_bytes"]).decode("utf-8"))
        return {
            "run_id": run_id,
            "state": manifest["terminal_state"],
            "consumed_budget": _consumed_budget(manifest),
            "accepted_at": submitted,
            "exit_code": manifest["exit_code"],
            "done": True,
        }
    if os.path.isfile(os.path.join(run_dir, "cancel_requested")):
        return {
            "run_id": run_id,
            "state": "cancelling",
            "accepted_at": submitted,
            "exit_code": None,
            "done": False,
        }
    pid = _read_pid(run_dir)
    if orphan_running or (pid is not None and _alive(pid)):
        return {
            "run_id": run_id,
            "state": "running" if os.path.isfile(os.path.join(run_dir, "running")) else "accepted",
            "accepted_at": submitted,
            "exit_code": None,
            "done": False,
        }
    raise RuntimeError("remote runner has no live worker and no terminal marker")


def _collect(config, run_id):
    status_value = _status(config, run_id)
    if not status_value["done"]:
        raise RuntimeError("remote job is not terminal")
    run_dir = _run_dir(config, run_id)
    manifest = json.loads(_read_regular(os.path.join(run_dir, "output_manifest.json"), config["runner"]["max_manifest_bytes"]).decode("utf-8"))
    outputs = []
    for item in manifest["outputs"]:
        descriptor = _descriptor(
            item["name"],
            os.path.join(run_dir, "work", *item["relative_path"].split("/")),
            item["media_type"],
        )
        if (
            descriptor["size_bytes"] != item["size_bytes"]
        ):
            raise RuntimeError("completed output differs from its manifest")
        outputs.append(descriptor)
    diagnostic = os.path.join(run_dir, "diagnostic.log")
    outputs.append(_descriptor("tcad_log", diagnostic if os.path.isfile(diagnostic) else os.path.join(run_dir, "worker.log"), "text/plain; charset=utf-8"))
    outputs.append(_descriptor("tcad_manifest", os.path.join(run_dir, "output_manifest.json"), "application/json"))
    return {"run_id": run_id, "outputs": outputs}


def _cancel(config, run_id):
    run_dir = _run_dir(config, run_id)
    if os.path.isfile(os.path.join(run_dir, "done")):
        return _status(config, run_id)
    marker = os.path.join(run_dir, "cancel_requested")
    if not os.path.exists(marker):
        _write_new(marker, (_timestamp() + "\n").encode("ascii"), 0o440)
    return _status(config, run_id)


def _run_worker(config, run_dir):
    # The lease covers startup, solver launch and final publication. A reader
    # must hold this same lease before repairing a stopped worker's run.
    with open(os.path.join(run_dir, "worker.lock"), "a+") as lease:
        # A status reader may briefly own the lease before startup. Wait for
        # that reader instead of losing the accepted worker before its identity
        # is published. The immutable worker.json still fences duplicate starts.
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        worker = {"worker": _process_identity(os.getpid()), "solver": None}
        _write_new(os.path.join(run_dir, "worker.json"), _canonical(worker), 0o440)
        return _run_worker_owned(config, run_dir, worker)


def _run_worker_owned(config, run_dir, worker):
    runtime = json.loads(_read_regular(os.path.join(run_dir, "runtime.json")).decode("utf-8"))
    _write_new(os.path.join(run_dir, "pid"), (str(os.getpid()) + "\n").encode("ascii"), 0o440)
    _write_new(os.path.join(run_dir, "running"), b"", 0o440)
    exit_code = 99
    solver_exit_code = None
    collection_errors = []
    terminal = "failed"
    error = ""
    outputs = []
    usage = {}
    process = None
    collection_deadline = [None]
    collection_stopped = False
    input_generations = {}

    def check_collection():
        if os.path.isfile(os.path.join(run_dir, "cancel_requested")):
            raise _Cancelled("cancelled_during_collection")
        now = time.monotonic()
        if collection_deadline[0] is None:
            collection_deadline[0] = now + runtime["runner_policy"]["collection_total_seconds"]
        if now >= collection_deadline[0]:
            raise _CollectionStopped("collection_time_exceeded")

    started_at = _timestamp()
    _write_new(os.path.join(run_dir, "started_at"), started_at.encode("ascii"), 0o440)
    try:
        environment = _runtime_environment(run_dir)
        environment.update(runtime["environment"])
        input_generations = _input_generations(os.path.join(run_dir,"work"),runtime["archive_entries"])
        with open(os.path.join(run_dir, "worker.log"), "wb") as log:
            if os.path.isfile(os.path.join(run_dir, "cancel_requested")):
                raise _Cancelled("cancelled_before_launch")
            # A kill during Popen has no safe same-boot absence proof. Preserve
            # that uncertainty until a solver identity or a later boot resolves it.
            worker["solver"] = "launching"
            worker["solver_deadline_monotonic"] = time.monotonic() + runtime["limits"]["wall_time_seconds"]
            _atomic_write(os.path.join(run_dir, "worker.json"), _canonical(worker), 0o440)
            process = subprocess.Popen(
                [runtime["executable"]] + runtime["arguments"],
                cwd=os.path.join(run_dir, "work"),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=environment,
                preexec_fn=lambda: _limits(runtime["limits"]),
                start_new_session=True,
            )
            worker["solver"] = _process_identity(process.pid)
            _atomic_write(os.path.join(run_dir, "worker.json"), _canonical(worker), 0o440)
            _write_new(os.path.join(run_dir, "solver_pid"), (str(process.pid) + "\n").encode("ascii"), 0o440)
            exit_code = _wait(process, runtime, run_dir, usage)
        solver_exit_code = exit_code
        _collect_expected(
            os.path.join(run_dir, "work"),
            runtime["expected_outputs"],
            dict(runtime["limits"], transfer_chunk_bytes=runtime["runner_policy"]["transfer_chunk_bytes"]),
            outputs, collection_errors,
            archive_entries=runtime.get("archive_entries", ()),
            input_generations=input_generations,
            collect_generated_outputs=runtime.get("collect_generated_outputs", False),
            check=check_collection,
        )
        if collection_errors:
            error = "; ".join(collection_errors)[:8192]
            if exit_code == 0:
                exit_code = 97
        terminal = "succeeded" if exit_code == 0 else "failed"
    except _Cancelled as failure:
        collection_stopped = True
        exit_code = 130
        terminal = "cancelled"
        error = str(failure)
        if process is not None and process.poll() is None:
            _terminate(process, runtime["runner_policy"]["terminate_grace_seconds"])
    except RuntimeError as failure:
        collection_stopped = isinstance(failure, _CollectionStopped)
        reason = str(failure)
        error = reason
        if reason == "wall_time_exceeded":
            exit_code = 124
        elif exit_code == 0:
            exit_code = 97
        if process is not None and process.poll() is None:
            _terminate(process, runtime["runner_policy"]["terminate_grace_seconds"])
    except Exception as failure:
        error = "%s: %s" % (type(failure).__name__, failure)
        if exit_code == 0:
            exit_code = 97
        if process is not None and process.poll() is None:
            _terminate(process, runtime["runner_policy"]["terminate_grace_seconds"])
    if error and not outputs and not collection_stopped:
        try:
            _collect_expected(os.path.join(run_dir, "work"), runtime["expected_outputs"],
                dict(runtime["limits"], transfer_chunk_bytes=runtime["runner_policy"]["transfer_chunk_bytes"]),
                outputs, collection_errors, archive_entries=runtime["archive_entries"],
                input_generations=input_generations,
                collect_generated_outputs=runtime.get("collect_generated_outputs", False),
                check=check_collection)
        except _Cancelled as failure:
            terminal, exit_code, error = "cancelled", 130, str(failure)
            collection_stopped = True
        except _CollectionStopped as failure:
            collection_errors.append(str(failure))
            collection_stopped = True
        except Exception as failure:
            collection_errors.append(str(failure)[:1024])
    try:
        if not collection_stopped:
            check_collection()
            _augment_development_debug_log(run_dir, runtime, check=check_collection)
            check_collection()
    except _Cancelled as failure:
        terminal, exit_code, error = "cancelled", 130, str(failure)
        collection_stopped = True
    except _CollectionStopped as failure:
        error = (error + "; " + str(failure)).lstrip("; ")
        if terminal == "succeeded":
            terminal, exit_code = "failed", 97
        collection_stopped = True
    except Exception as failure:
        error = (error + "; diagnostic collection failed: " + str(failure)).lstrip("; ")
    try:
        if "_started_monotonic" in usage:
            usage["elapsed_seconds"] = time.monotonic() - usage.pop("_started_monotonic")
            if not collection_stopped:
                usage["observed_storage_high_water_bytes"] = max(usage["observed_storage_high_water_bytes"],
                    _logical_storage_bytes(run_dir, runtime["archive_entries"], runtime["expected_outputs"], check=check_collection))
            if usage["observed_storage_high_water_bytes"] > runtime["limits"]["max_storage_bytes"]:
                terminal, exit_code, error = "failed", 124, "total_storage_exceeded"
    except _Cancelled as failure:
        terminal, exit_code, error = "cancelled", 130, str(failure)
    except _CollectionStopped as failure:
        error = (error + "; " + str(failure)).lstrip("; ")
        if terminal == "succeeded":
            terminal, exit_code = "failed", 97
    except Exception as failure:
        usage = {}
        terminal, exit_code, error = "failed", 124, "terminal storage accounting failed: " + str(failure)
    manifest = {
        "completed_at": _timestamp(),
        "error": error,
        "exit_code": exit_code,
        "outputs": outputs,
        "solver_exit_code": solver_exit_code,
        "collection_errors": collection_errors,
        "started_at": started_at,
        "terminal_state": terminal,
        "resource_usage": usage,
    }
    _atomic_write(os.path.join(run_dir, "output_manifest.json"), _canonical(manifest), 0o440)
    _atomic_write(os.path.join(run_dir, "status"), (str(exit_code) + "\n").encode("ascii"), 0o440)
    _unlink(os.path.join(run_dir, "running"))
    _write_new(os.path.join(run_dir, "done"), b"", 0o440)
    return exit_code


def _runtime_environment(run_dir):
    # POSIX C is available on the supported CentOS and Linux targets; C.UTF-8
    # is not guaranteed on CentOS 7 and must not be forced by the runner.
    return {
        "HOME": run_dir,
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _validate_job(job):
    expected = {"schema_version", "request_id", "execution_purpose", "tool_profile", "solver_kind", "capability_sha256", "input_archive", "archive_entries", "arguments", "expected_outputs", "limits", "collect_generated_outputs"}
    if not isinstance(job, dict) or set(job) != expected or job["schema_version"] != 5:
        raise ValueError("job specification has unexpected fields")
    if (not isinstance(job["request_id"],str) or len(job["request_id"])!=32
            or any(ch not in "0123456789abcdef" for ch in job["request_id"])):
        raise ValueError("job request identity is invalid")
    if not isinstance(job["collect_generated_outputs"], bool):
        raise ValueError("job generated-output collection flag is invalid")
    if job["execution_purpose"] not in {"production", "development_debug"}:
        raise ValueError("job execution_purpose is invalid")
    if not isinstance(job["archive_entries"], list) or not job["archive_entries"]:
        raise ValueError("job archive manifest is empty")
    if not isinstance(job["arguments"], list) or not isinstance(job["expected_outputs"], list):
        raise ValueError("job arguments or outputs are invalid")
    for entry in job["archive_entries"]:
        if entry["relative_path"].startswith(".scid-capture/"):
            raise ValueError("archive uses reserved stdout directory")
    process_log_count = 0
    for output in job["expected_outputs"]:
        if not isinstance(output, dict):
            raise ValueError("job output contract is invalid")
        capture = output.get("capture", "workspace_file")
        if capture not in {"workspace_file", "process_log"}:
            raise ValueError("job output capture mode is invalid")
        if capture == "process_log" and output["relative_path"] != ".scid-capture/solver_stdout.log":
            raise ValueError("process log must use the reserved stdout path")
        if capture != "process_log" and output["relative_path"].startswith(".scid-capture/"):
            raise ValueError("solver output uses reserved stdout directory")
        process_log_count += capture == "process_log"
    if process_log_count > 1:
        raise ValueError("job can declare at most one process-log output")
    if job["solver_kind"] not in {"sprocess", "sdevice", "shell_runner", "deterministic_tool"}:
        raise ValueError("job solver_kind is invalid")
    if not _valid_control_digest(job["capability_sha256"]):
        raise ValueError("job capability_sha256 is invalid")
    limits = job["limits"]
    required_limits = {"wall_time_seconds", "cpu_time_seconds", "max_memory_bytes", "max_output_bytes", "max_storage_bytes"}
    if not isinstance(limits, dict) or set(limits) != required_limits:
        raise ValueError("job limits are invalid")
    for key in required_limits:
        if not isinstance(limits[key], int) or limits[key] < 1:
            raise ValueError("job limits must be positive integers")


def _publish_log_mirror(source_path, target_path, size, chunk_bytes, check=None):
    import tempfile
    descriptor, temporary = tempfile.mkstemp(prefix=".stdout.", dir=os.path.dirname(str(target_path)))
    try:
        with os.fdopen(descriptor, "wb") as target:
            source_descriptor = os.open(str(source_path), os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(source_descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ValueError("scientific file is not regular")
                while True:
                    if check is not None:
                        check()
                    chunk = source.read(chunk_bytes)
                    if not chunk:
                        break
                    target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        if _file_size(temporary, chunk_bytes, check=check) != size:
            raise RuntimeError("stdout changed during mirror publication")
        os.chmod(temporary, 0o440)
        try:
            os.link(temporary, str(target_path))
        except FileExistsError:
            if _file_size(target_path, chunk_bytes, check=check) != size:
                raise RuntimeError("reserved stdout mirror conflicts with an existing file; originals retained")
    finally:
        os.unlink(temporary)


def _file_size(path, chunk_bytes, check=None):
    if check is not None:check()
    descriptor=os.open(str(path),os.O_RDONLY|os.O_NONBLOCK|getattr(os,"O_NOFOLLOW",0))
    with os.fdopen(descriptor,"rb") as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):raise ValueError("scientific file is not regular")
        return info.st_size


def _extract_archive(archive_path, entries, destination, chunk_bytes):
    expected = {item["relative_path"]: item for item in entries}
    if not os.path.isdir(str(destination)):
        os.makedirs(str(destination), mode=0o750)
    seen = set()
    with tarfile.open(str(archive_path), "r:*") as archive:
        for member in archive:
            name = _safe_relative(member.name)
            if name in seen or name not in expected or not member.isfile():
                raise ValueError("archive contains an undeclared member")
            item = expected[name]
            if member.size != item["size_bytes"]:
                raise ValueError("archive member size differs from manifest")
            source = archive.extractfile(member)
            target = os.path.join(str(destination), *name.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            size = 0
            with source, open(target, "xb") as output:
                for chunk in iter(lambda: source.read(chunk_bytes), b""):
                    size += len(chunk)
                    if size > item["size_bytes"]:
                        raise ValueError("archive member exceeds manifest")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if size != item["size_bytes"]:
                raise ValueError("archive member differs from manifest")
            os.chmod(target, 0o440)
            seen.add(name)
    if seen != set(expected):
        raise ValueError("archive is missing declared members")


def _collect_expected(root, expected, limits, records=None, errors=None, *, archive_entries=(), input_generations=None, collect_generated_outputs=False, check=None):
    records = [] if records is None else records
    total = sum(int(item["size_bytes"]) for item in records)
    failures = [] if errors is None else errors
    if check is not None:
        check()
    for index, item in enumerate(expected):
        try:
            if check is not None:
                check()
            relative = _safe_relative(item["relative_path"])
            capture = item.get("capture", "workspace_file")
            if capture == "workspace_file":
                path = os.path.join(root, *relative.split("/"))
            elif capture == "process_log":
                path = os.path.join(os.path.dirname(root), "worker.log")
            else:
                raise RuntimeError("unsupported output capture mode")
            if not os.path.isfile(path):
                if item["required"]:
                    raise RuntimeError("required output missing: %s" % relative)
                continue
            if os.lstat(path).st_size > item["max_bytes"]:
                raise RuntimeError("output exceeds declared bound")
            size = _file_size(path, limits["transfer_chunk_bytes"], check=check)
            if size > item["max_bytes"]:
                raise RuntimeError("output exceeds declared bound")
            total += size
            if total > limits["max_output_bytes"]:
                raise RuntimeError("total output exceeds job limit")
            if capture == "process_log":
                target = os.path.join(root, *relative.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                _publish_log_mirror(path, target, size, limits["transfer_chunk_bytes"], check=check)
            os.chmod(path, 0o440)
            records.append({"name": item["name"], "relative_path": relative,
                "media_type": item["media_type"], "size_bytes": size})
        except _CollectionStopped:
            raise
        except (OSError, RuntimeError, ValueError) as failure:
            failures.append(str(failure)[:1024])
            if "total output" in str(failure):
                failures[-1] += "; %d later outputs not inspected" % (len(expected)-index-1)
                break
    if collect_generated_outputs:
        try:
            _collect_generated(root, archive_entries, expected, limits, records, input_generations=input_generations, check=check)
        except _CollectionStopped:
            raise
        except (OSError, RuntimeError, ValueError) as failure:
            failures.append(str(failure)[:1024])
    if errors is None and failures:
        raise RuntimeError("; ".join(failures)[:8192])
    if check is not None:
        check()
    return records


def _input_generation(value):
    return (value.st_dev,value.st_ino,value.st_mode,value.st_size,
            value.st_mtime_ns,value.st_ctime_ns)


def _input_generations(root, archive_entries):
    """Capture only this run's pre-launch input metadata, without reading bytes."""
    result={}
    for item in archive_entries:
        relative=_safe_relative(item["relative_path"])
        path=os.path.join(root,*relative.split("/"))
        current=root
        for part in relative.split("/"):
            current=os.path.join(current,part)
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise ValueError("original input path contains a symlink")
        descriptor=os.open(path,os.O_RDONLY|os.O_NONBLOCK|getattr(os,"O_NOFOLLOW",0))
        with os.fdopen(descriptor,"rb") as source:
            value=os.fstat(source.fileno())
            if not stat.S_ISREG(value.st_mode) or value.st_size!=item["size_bytes"]:
                raise ValueError("original input differs from its staged descriptor")
            result[relative]=_input_generation(value)
    return result


def _collect_generated(root, archive_entries, expected, limits, records, input_generations=None, check=None):
    originals = dict((item["relative_path"], item) for item in archive_entries)
    reserved = set(item["relative_path"] for item in expected)
    total = sum(int(item["size_bytes"]) for item in records)
    generated = 0
    def fail_walk(error):
        raise error

    for directory, dirs, files in os.walk(root, followlinks=False, onerror=fail_walk):
        if check is not None:
            check()
        dirs.sort()
        files.sort()
        for name in dirs:
            if check is not None:
                check()
            if not stat.S_ISDIR(os.lstat(os.path.join(directory, name)).st_mode):
                raise RuntimeError("generated output directory is not a real directory")
        for name in files:
            if check is not None:
                check()
            path = os.path.join(directory, name)
            relative = _safe_relative(os.path.relpath(path, root).replace(os.sep, "/"))
            if len(relative) > 1024:
                raise RuntimeError("generated output path exceeds contract")
            if relative in reserved:
                continue
            if not stat.S_ISREG(os.lstat(path).st_mode):
                raise RuntimeError("generated output is not a regular file: " + relative)
            flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeError("generated output is not a regular file: " + relative)
                if relative in originals:
                    before=(input_generations or {}).get(relative)
                    if before is None:
                        raise RuntimeError("generated collection lacks original input generation")
                    if _input_generation(metadata)==tuple(before):
                        continue
                if total + metadata.st_size > limits["max_output_bytes"]:
                    raise RuntimeError("total output exceeds job limit")
                size=metadata.st_size
            finally:
                os.close(descriptor)
            # Changed original paths remain outputs; unchanged inputs cost no output quota.
            generated += 1
            if len(records) >= 4092:
                raise RuntimeError("collected solver output count exceeds 4092")
            total += size
            suffix = os.path.splitext(path)[1].lower()
            media_type = {".plx": "application/x-synopsys-plx", ".json": "application/json",
                          ".csv": "text/csv", ".txt": "text/plain", ".log": "text/plain",
                          ".png": "image/png"}.get(suffix, "application/octet-stream")
            records.append({"name": "generated_" + str(len(records)),
                            "relative_path": relative, "media_type": media_type,
                            "size_bytes": size})
    if not generated:
        raise RuntimeError("solver produced no generated output files")
    if check is not None:
        check()


def _augment_development_debug_log(run_dir, runtime, check=None):
    arguments = runtime.get("arguments")
    if not isinstance(arguments, list) or not arguments:
        return
    entrypoint = arguments[-1]
    if not isinstance(entrypoint, str) or not entrypoint.endswith(".cmd"):
        return
    work_dir = os.path.join(run_dir, "work")
    preferred = os.path.splitext(os.path.basename(entrypoint))[0] + ".log"
    names = [preferred]
    try:
        names.extend(
            name
            for name in sorted(os.listdir(work_dir))
            if name.endswith((".log", ".err")) and name != preferred
        )
    except OSError:
        return
    if len(names) > runtime["runner_policy"]["max_diagnostic_files"]:
        raise ValueError("solver diagnostic file count exceeds configured bound; original files retained")
    parts = []
    total = 0
    for name in names:
        if check is not None:
            check()
        candidate = os.path.join(work_dir, name)
        if not os.path.exists(candidate):
            continue
        raw = _read_diagnostic_log(candidate, runtime["runner_policy"]["max_log_bytes"] // 2 - total, check=check)
        total += len(raw)
        parts.append(b"\n--- scidiscovery solver diagnostic: " + name.encode("utf-8") + b" ---\n" + raw)
    if not parts:
        return
    stdout = _read_diagnostic_log(os.path.join(run_dir, "worker.log"), runtime["runner_policy"]["max_log_bytes"] // 2, check=check)
    _atomic_write(os.path.join(run_dir, "diagnostic.log"), stdout + b"".join(parts), 0o440)


def _read_diagnostic_log(path, limit, check=None):
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("debug log is not a regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise ValueError("debug log is not a regular file")
        if check is not None:
            check()
        if metadata.st_size <= limit:
            return source.read(limit + 1)
        raise ValueError("diagnostic log exceeds its capture limit; original file retained")


def _limits(limits):
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_time_seconds"], limits["cpu_time_seconds"] + 1))
    resource.setrlimit(resource.RLIMIT_AS, (limits["max_memory_bytes"], limits["max_memory_bytes"]))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["max_output_bytes"], limits["max_output_bytes"]))


def _logical_storage_bytes(run_dir, entries, expected, check=None):
    originals = {item["relative_path"]: item["size_bytes"] for item in entries}
    total = sum(originals.values())
    work = os.path.join(str(run_dir), "work")
    control = {"job.json", "runtime.json", "submitted_at", "started_at", "pid", "solver_pid", "launcher_pid", "running", "done", "status", "output_manifest.json", "cancel_requested", "worker.json", "worker.lock"}
    for directory, dirs, files in os.walk(str(run_dir), followlinks=False):
        if check is not None:
            check()
        for name in dirs:
            if check is not None:
                check()
            if os.path.islink(os.path.join(directory, name)):
                raise RuntimeError("storage accounting encountered a symlink directory")
        for name in files:
            if check is not None:
                check()
            path = os.path.join(directory, name)
            try:
                metadata = os.lstat(path)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RuntimeError("storage accounting encountered a nonregular file")
            relative = os.path.relpath(path, str(run_dir))
            if relative in control:
                continue
            work_relative = os.path.relpath(path, work)
            if not work_relative.startswith("../"):
                total += max(0, metadata.st_size - originals.get(work_relative, 0))
            else:
                total += metadata.st_size
    if check is not None:
        check()
    return total


def _wait(process, job, run_dir, usage):
    limits, policy = job["limits"], job["runner_policy"]
    started = time.monotonic()
    usage["_started_monotonic"] = started
    usage.update(observed_storage_high_water_bytes=0, elapsed_seconds=0.0,
        sample_interval_seconds=policy["sample_interval_seconds"],
        storage_overshoot_bytes=0, accounting="sampled_logical_files", hard_quota=False)
    while True:
        elapsed = time.monotonic() - started
        observed = _logical_storage_bytes(run_dir, job["archive_entries"], job["expected_outputs"])
        usage["elapsed_seconds"] = elapsed
        usage["observed_storage_high_water_bytes"] = max(usage["observed_storage_high_water_bytes"], observed)
        usage["storage_overshoot_bytes"] = max(0, usage["observed_storage_high_water_bytes"] - limits["max_storage_bytes"])
        reason = ("total_storage_exceeded" if observed > limits["max_storage_bytes"] else
                  "wall_time_exceeded" if elapsed >= limits["wall_time_seconds"] else None)
        if reason:
            _terminate(process, policy["terminate_grace_seconds"])
            raise RuntimeError(reason)
        if os.path.isfile(os.path.join(str(run_dir), "cancel_requested")):
            raise _Cancelled("cancelled_by_request")
        result = process.poll()
        if result is not None:
            _terminate(process, policy["terminate_grace_seconds"])
            return result
        time.sleep(policy["sample_interval_seconds"])


def _terminate(process, grace_seconds):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _read_bound_descriptor(value, root):
    required = {"name", "local_path", "size_bytes", "media_type"}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("file descriptor is invalid")
    path = os.path.abspath(value["local_path"])
    _require_within(path, root)
    raw = _read_regular(path)
    if len(raw) != value["size_bytes"]:
        raise ValueError("file differs from descriptor")
    return raw


def _descriptor(name, path, media_type):
    size = _file_size(path, 1024 * 1024)
    return {"name": name, "local_path": path, "size_bytes": size, "media_type": media_type}


def _tool(config, profile_id):
    for tool in config["tools"]:
        if tool["profile_id"] == profile_id:
            return tool
    raise ValueError("tool profile is not allowed")


def _capability_sha256(tool):
    capability = {
        "arguments": tool["arguments"],
        "environment": tool["environment"],
        "executable": tool["executable"],
        "profile_id": tool["profile_id"],
        "release_evidence": tool["release_evidence"],
        "schema_version": 1,
        "solver_kind": tool["solver_kind"],
    }
    return _control_digest(_canonical(capability))


def _run_dir(config, run_id):
    if (
        not isinstance(run_id, str)
        or len(run_id) != 36
        or not run_id.startswith("run_")
        or any(character not in "0123456789abcdef" for character in run_id[4:])
    ):
        raise ValueError("invalid run identity")
    path = os.path.join(config["result_root"], run_id)
    if not os.path.isdir(path):
        raise ValueError("remote run does not exist")
    return path


def _read_pid(run_dir):
    for name in ("pid", "launcher_pid"):
        try:
            return int(_read_regular(os.path.join(run_dir, name)).decode("ascii").strip())
        except (IOError, OSError, ValueError):
            pass
    return None


def _boot_id():
    return _read_regular("/proc/sys/kernel/random/boot_id", 128).decode("ascii").strip()


def _process_record(pid):
    raw = _read_regular("/proc/%s/stat" % pid, 4096).decode("ascii")
    fields = raw[raw.rindex(")") + 2:].split()
    return fields[0], fields[19]


def _process_identity(pid):
    _, started = _process_record(pid)
    return {"pid": pid, "boot_id": _boot_id(), "start_time": started}


def _identity_state(identity):
    if (not isinstance(identity, dict) or set(identity) != {"pid", "boot_id", "start_time"}
            or type(identity["pid"]) is not int or identity["pid"] <= 0
            or not isinstance(identity["boot_id"], str) or not identity["boot_id"]
            or not isinstance(identity["start_time"], str) or not identity["start_time"].isdigit()):
        raise RuntimeError("remote worker process identity is invalid")
    if identity["boot_id"] != _boot_id():
        return "stopped"
    try:
        state, started = _process_record(identity["pid"])
    except FileNotFoundError:
        return "stopped"
    if started != identity["start_time"] or state in ("Z", "X"):
        return "stopped"
    return "alive"


def _solver_state(identity):
    state = _identity_state(identity)
    if identity["boot_id"] != _boot_id():
        return "stopped"
    try:
        os.killpg(identity["pid"], 0)
    except ProcessLookupError:
        return state
    try:
        _, started = _process_record(identity["pid"])
    except FileNotFoundError:
        # A group can outlive its leader. Its original PGID cannot be reused
        # while the group still exists and there is no process with that PID.
        return "alive"
    if started != identity["start_time"]:
        raise RuntimeError("remote solver process group identity is uncertain")
    return "alive"


def _reconcile_worker(config, run_dir):
    """Finish only this accepted local worker, with positive stop evidence.

    Missing startup facts remain uncertain. Neither a timeout nor a missing PID
    by itself proves that an external submission never happened.
    """
    with open(os.path.join(run_dir, "worker.lock"), "a+") as lease:
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        if os.path.isfile(os.path.join(run_dir, "done")):
            return False
        try:
            worker = json.loads(_read_regular(os.path.join(run_dir, "worker.json"), 4096).decode("utf-8"))
        except FileNotFoundError:
            return False
        identity = worker["worker"]
        if _identity_state(identity) != "stopped":
            return False
        rebooted = identity["boot_id"] != _boot_id()
        solver = worker["solver"]
        cancelled = os.path.isfile(os.path.join(run_dir, "cancel_requested"))
        wall_expired = False
        if not rebooted and solver == "launching":
            raise RuntimeError("remote worker stopped during uncertain solver launch")
        if not rebooted and solver is not None and _solver_state(solver) == "alive":
            deadline = worker.get("solver_deadline_monotonic")
            wall_expired = (type(deadline) in (int, float) and deadline >= 0
                            and time.monotonic() >= deadline)
            if not cancelled and not wall_expired:
                return True
            # Only a positively identified orphan session may be signalled.
            # This bounded cancellation never launches another worker or solver.
            for sig in (signal.SIGTERM, signal.SIGKILL):
                if _solver_state(solver) != "alive":
                    break
                try:
                    os.killpg(solver["pid"], sig)
                except ProcessLookupError:
                    break
                deadline = time.monotonic() + config["runner"]["terminate_grace_seconds"]
                while _solver_state(solver) == "alive" and time.monotonic() < deadline:
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            if _solver_state(solver) == "alive":
                return True
        manifest_path = os.path.join(run_dir, "output_manifest.json")
        if os.path.exists(manifest_path):
            # Worker finalization may have stopped between its atomic manifest
            # publication and done. Preserve the exact original terminal facts.
            manifest = json.loads(_read_regular(manifest_path, config["runner"]["max_manifest_bytes"]).decode("utf-8"))
            if (manifest.get("terminal_state") not in ("succeeded", "failed", "cancelled")
                    or type(manifest.get("exit_code")) is not int
                    or not isinstance(manifest.get("outputs"), list)):
                raise RuntimeError("remote worker terminal manifest is invalid")
        else:
            started = os.path.join(run_dir, "started_at")
            started_at = (_read_regular(started, 128).decode("ascii").strip() if os.path.exists(started)
                          else _read_regular(os.path.join(run_dir, "submitted_at"), 128).decode("ascii").strip())
            error = "wall_time_exceeded" if wall_expired else "remote_worker_lost"
            manifest = {"completed_at": _timestamp(), "error": error,
                "exit_code": 124 if wall_expired else 130 if cancelled else 99,
                "outputs": [], "solver_exit_code": None,
                "collection_errors": ["remote_worker_lost"], "started_at": started_at,
                "terminal_state": "cancelled" if cancelled and not wall_expired else "failed", "resource_usage": {}}
            _atomic_write(manifest_path, _canonical(manifest), 0o440)
        _atomic_write(os.path.join(run_dir, "status"), (str(manifest["exit_code"]) + "\n").encode("ascii"), 0o440)
        if not os.path.exists(os.path.join(run_dir, "worker.log")):
            _write_new(os.path.join(run_dir, "worker.log"), b"remote_worker_lost\n", 0o440)
        _unlink(os.path.join(run_dir, "running"))
        _write_new(os.path.join(run_dir, "done"), b"", 0o440)
        return False


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _safe_relative(value):
    if not isinstance(value, str) or value.startswith("/") or "\\" in value:
        raise ValueError("unsafe relative path")
    parts = value.split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ValueError("unsafe relative path")
    return value


def _require_within(path, root):
    path = os.path.abspath(path)
    root = os.path.abspath(root)
    if os.path.commonpath([path, root]) != root:
        raise ValueError("path is outside configured root")


def _read_regular(path, max_bytes=None):
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("path is not a regular file")
    if max_bytes is not None and metadata.st_size > max_bytes:
        raise ValueError("metadata exceeds configured byte bound")
    with open(path, "rb") as source:
        raw = source.read() if max_bytes is None else source.read(max_bytes + 1)
    if max_bytes is not None and len(raw) > max_bytes:
        raise ValueError("metadata exceeds configured byte bound")
    return raw


def _write_immutable(path, raw):
    if os.path.exists(path):
        if _read_regular(path) != raw:
            raise ValueError("existing immutable file differs")
        return
    _write_new(path, raw, 0o440)


def _write_new(path, raw, mode):
    parent = os.path.dirname(path)
    _mkdir(parent)
    descriptor, temporary = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.chmod(temporary, mode)
        if os.path.exists(path):
            raise ValueError("file already exists")
        os.rename(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write(path, raw, mode):
    temporary = path + ".tmp"
    with open(temporary, "wb") as target:
        target.write(raw)
        target.flush()
        os.fsync(target.fileno())
    os.chmod(temporary, mode)
    os.rename(temporary, path)


def _mkdir(path):
    if not os.path.isdir(path):
        os.makedirs(path, 0o770)


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _valid_control_digest(value):
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _control_digest(raw):
    """Installed capability and approval records only; never scientific files."""
    return hashlib.sha256(raw).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _timestamp():
    return datetime.utcnow().isoformat(timespec="microseconds") + "Z"


def _execution_timing(started_at, completed_at=None, terminal=False):
    """Observed job wall time, not solver CPU time or proof of initialization."""
    observed_at = _timestamp()
    result = {"observed_at": observed_at}
    parsed = {}
    for name, value in (("started_at", started_at), ("completed_at", completed_at)):
        try:
            parsed[name] = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ")
            result[name] = value
        except (TypeError, ValueError):
            pass
    end = parsed.get("completed_at")
    if end is None and completed_at is None and not terminal:
        end = datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%S.%fZ")
    if end is not None and "started_at" in parsed:
        result["elapsed_seconds"] = round(max(0, (end - parsed["started_at"]).total_seconds()), 3)
    return result


def _redact_log(text):
    # Shared by live status and the complete terminal debug log.
    text = re.sub(r"(?<![A-Za-z0-9_.-])(?:/[A-Za-z0-9_.-]+){2,}", "<path>", text)
    text = re.sub(r"(?i)\b[A-Z]:\\(?:[^\s\\]+\\)*[^\s\\]*", "<path>", text)
    text = re.sub(r"\b[0-9]{2,6}@[A-Za-z0-9_.-]+\b", "<license-endpoint>", text)
    text = re.sub(r"(?im)\b(?:SNPSLMD_LICENSE_FILE|LM_LICENSE_FILE|PATH|HOME)\s*=\s*\S+",
                  "<redacted-environment>", text)
    return re.sub(r"(?im)^.*\b(?:password|credential|private[_ -]?key|access[_ -]?token)\b.*$",
                  "<redacted-private-line>", text)


def _progress_read(directory, name, limit, tail=False):
    """Nonblocking, bounded reads of regular files in this job only."""
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            metadata = os.fstat(handle)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("progress source is not regular")
            if tail:
                os.lseek(handle, max(0, metadata.st_size - limit), os.SEEK_SET)
            elif metadata.st_size > limit:
                raise ValueError("progress metadata exceeds its bound")
            return os.read(handle, limit), metadata
        finally:
            os.close(handle)
    finally:
        os.close(parent)


def _job_progress(run_dir):
    # Optional observations must never change the authoritative job state.
    started_at = completed_at = None
    terminal = os.path.isfile(os.path.join(run_dir, "done"))
    try:
        if terminal:
            raw, _ = _progress_read(run_dir, "output_manifest.json", 1024 * 1024)
            manifest = json.loads(raw.decode("utf-8"))
            started_at, completed_at = manifest.get("started_at"), manifest.get("completed_at")
        else:
            raw, _ = _progress_read(run_dir, "started_at", 128)
            started_at = raw.decode("ascii").strip()
    except (OSError, ValueError, AttributeError):
        pass
    result = _execution_timing(started_at, completed_at, terminal=terminal)
    candidates = [(run_dir, "worker.log")]
    work = os.path.join(run_dir, "work")
    # One latest solver log plus stdout; bounded scan, no recursion or symlinks.
    latest = None
    try:
        if not os.path.islink(work):
            with os.scandir(work) as entries:
                for index, entry in enumerate(entries):
                    if index >= 64:
                        break
                    if entry.name.endswith((".log", ".err")) and entry.is_file(follow_symlinks=False):
                        candidate = (entry.stat(follow_symlinks=False).st_mtime, entry.name)
                        if latest is None or candidate > latest:
                            latest = candidate
    except OSError:
        pass
    if latest is not None:
        candidates.append((work, latest[1]))
    result["log_tails"] = []
    for directory, name in candidates:
        try:
            raw, metadata = _progress_read(directory, name, 2048, tail=True)
            # Drop a partial first line: its redaction context may be omitted.
            truncated = metadata.st_size > len(raw)
            start_byte = max(0, metadata.st_size - 2048)
            if truncated:
                skipped, separator, raw = raw.partition(b"\n")
                start_byte += len(skipped) + len(separator)
            text = raw.decode("utf-8", errors="replace")
            stream_identity = "%s:%s:%s:%s" % (directory, name, metadata.st_dev, metadata.st_ino)
            result["log_tails"].append({
                "stream_id": "%s:%s:%s" % (name,metadata.st_dev,metadata.st_ino),
                "start_byte": start_byte,
                "source": "stdout" if directory == run_dir else "solver_log",
                "tail": _redact_log(text), "size_bytes": metadata.st_size,
                "updated_at": datetime.utcfromtimestamp(metadata.st_mtime).isoformat(timespec="microseconds") + "Z",
                "truncated": truncated,
            })
        except (OSError, ValueError):
            pass
    return result


def _write_response(stream, operation, payload):
    stream.write(_canonical({"schema_version": 2, "operation": operation, "ok": True, "payload": payload}) + b"\n")
    stream.flush()



def _inspect_directory(run_dir, relative_path=None, max_bytes=32 * 1024 * 1024, deadline_monotonic=None):
    """Read bounded terminal execution products; Python 3.6/local shared logic."""
    # Only the local socket caller supplies its host's absolute deadline. The
    # remote RPC and standalone runner retain their existing default behavior.
    def check_deadline():
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            error = TimeoutError("inspection IO budget exhausted")
            error.timeout_kind = "inspection_io"
            raise error
    check_deadline()
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("inspection byte budget invalid")
    if not os.path.isfile(os.path.join(run_dir, "done")):
        return {"status": "unavailable", "reason": "execution_not_terminal"}
    root = os.path.join(run_dir, "work")
    if os.path.islink(run_dir) or os.path.islink(root):
        return {"status": "unavailable", "reason": "execution_directory_link"}
    if relative_path is None:
        entries = []
        pending = [root]
        visited = 0
        deadline = time.monotonic() + 10
        while pending:
            check_deadline()
            directory = pending.pop()
            with os.scandir(directory) as scan:
                for item in scan:
                    check_deadline()
                    visited += 1
                    if visited > 256 or time.monotonic() > deadline:
                        return {"status": "limit_exceeded", "files": entries, "reason": "inventory_limit"}
                    if item.is_symlink():
                        continue
                    if item.is_dir(follow_symlinks=False):
                        pending.append(item.path)
                    elif item.is_file(follow_symlinks=False):
                        entry = {"relative_path": os.path.relpath(item.path, root).replace(os.sep, "/"), "size_bytes": item.stat(follow_symlinks=False).st_size}
                        if len(_canonical(entries + [entry])) > 60 * 1024:
                            return {"status": "limit_exceeded", "files": entries, "reason": "inventory_bytes"}
                        entries.append(entry)
        if len(_canonical(entries)) > 60 * 1024:
            return {"status": "limit_exceeded", "reason": "inventory_bytes"}
        return {"status": "available", "files": entries}
    relative = _safe_relative(relative_path)
    path = root
    try:
        for part in relative.split("/"):
            check_deadline()
            path = os.path.join(path, part)
            if stat.S_ISLNK(os.lstat(path).st_mode):
                return {"status": "unavailable", "reason": "symlink_forbidden"}
        metadata = os.lstat(path)
    except FileNotFoundError:
        return {"status": "not_found", "reason": "file_missing"}
    if not stat.S_ISREG(metadata.st_mode):
        return {"status": "unavailable", "reason": "not_regular_file"}
    if metadata.st_size > max_bytes:
        return {"status": "limit_exceeded", "reason": "file_bytes"}
    check_deadline()
    return {"status":"available","relative_path":relative,"file":{"name":"candidate",
        "local_path":path,"media_type":"application/octet-stream","size_bytes":metadata.st_size}}



def _consumed_budget(manifest):
    import math
    usage = manifest.get("resource_usage", {})
    elapsed = usage.get("elapsed_seconds")
    if elapsed is None:
        elapsed = _execution_timing(manifest.get("started_at"), manifest.get("completed_at"), terminal=True).get("elapsed_seconds")
    storage = usage.get("observed_storage_high_water_bytes")
    consumed = {}
    if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
        consumed["wall_time_seconds"] = int(math.ceil(elapsed))
    if type(storage) is int and storage >= 0:
        consumed["max_storage_bytes"] = storage
    return consumed or None


if __name__ == "__main__":
    raise SystemExit(main())
