"""Optional Harbor 0.25 host entry; importing the experiment provider needs no SDK.

The private Compose hook is confined to ``bind_main_container``. Control and
native roles remain on the host; only admitted experiments enter the original
scientific container.
"""
from __future__ import annotations

import asyncio
from importlib.metadata import version
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sys
import tarfile
import threading
import time
import tomllib
from types import SimpleNamespace
from uuid import uuid4

from harbor.agents.base import BaseAgent
from harbor.environments.docker.docker import DockerEnvironment

from scidiscovery.research_loop.storage_policy import Deadline


HARBOR_VERSION = "0.25.0"
CONTAINER_PYTHON = "/usr/local/bin/python"
MAX_STATUS_BYTES = 4 * 1024 * 1024
MAX_BUNDLE_FILES = 128


def task_shape(profile):
    """The three explicit local cohort shapes; no core task dispatch exists."""
    if profile == "symbolic-regression":
        return dict(runtime="python", argv=[CONTAINER_PYTHON, "-B", "-u"],
            public_files=[("/app/regressor.py", "regressor.py")],
            artifact="/app/regressor.py", bundle=False)
    if profile == "highdim-mediation-debiasing":
        return dict(runtime="shell", argv=["/usr/bin/Rscript", "--vanilla"],
            public_files=[("/root/project/" + name, name) for name in
                ("starter_code.R", "helper_functions.R", "evaluation_contract.md", "run_public_check.R")],
            artifact="/root/project", bundle=True)
    if profile == "small-area-equivalence":
        return dict(runtime="python", argv=[CONTAINER_PYTHON, "-B", "-u"],
            public_files=[("/root/" + name, name) for name in
                ("draw_contract.py", "task_contract.json", "validate_submission.py")],
            artifact="/app/small_area_submission", bundle=True)
    raise ValueError("unsupported explicit task_profile")


def task_navigation(profile):
    common = ("Absolute task paths refer to the original scientific container. "
        "The host public files are exact originals named in this navigation. "
        "Use the existing experiment interface with --script code/<entry> and optional arguments "
        "to execute authored code in the original runtime. Its cwd is separate scratch work/; "
        "read original public materials by their absolute container paths, and keep intermediate "
        "code and outputs in scratch. Frozen authored sibling files are in the entry's code directory. "
        "The existing optional artifacts/result.json protocol declares retained observations and files. "
        "Do not modify final task artifact paths during experiments; final confirmed delivery publishes them. ")
    if profile == "symbolic-regression":
        return common + ("The public regressor.py is /app/regressor.py; training data remains at "
            "/app/training_data.npz. Execute Python entries. Nominate and deliver the retained "
            "regressor.py itself as the artifact. Its exact confirmed bytes go to /app/regressor.py.\n")
    if profile == "highdim-mediation-debiasing":
        return common + ("The original runtime is Rscript, not Python; use R entries. The task workspace "
            "and public data are under /root/project. Public starter_code.R, helper_functions.R, "
            "evaluation_contract.md and run_public_check.R are exact originals from there. Preserve "
            "the task's Rscript command interface. Nominate and deliver one TAR artifact containing "
            "exactly the regular files starter_code.R and helper_functions.R at the archive root; "
            "both confirmed files are published to their original /root/project paths. R can create "
            "this with utils::tar(..., tar='internal'), without external archive tools or new packages. The native "
            "transport bounds the original process group with timeout; detached same-user processes "
            "remain outside this soft boundary.\n")
    if profile == "small-area-equivalence":
        return common + ("Execute Python entries. Public task_contract.json, draw_contract.py and "
            "validate_submission.py are exact originals under /root. Development populations remain "
            "under /root/development. Nominate and deliver one TAR artifact whose only top-level "
            "directory is small_area_submission/, containing solve.py and any authored bundled "
            "resources. Confirmed regular files are published under /app/small_area_submission. "
            "Python's standard-library tarfile can create it. Do not put task_contract.json or draw_contract.py in the TAR: their original container "
            "bytes remain untouched and Harbor collects them under the original task contract.\n")
    raise ValueError("unsupported explicit task_profile")


class _ArchiveReader:
    """Check the same extraction deadline during archive reads and skips."""
    def __init__(self, source, context):
        self.source, self.context = source, context

    def read(self, size=-1):
        # Tar metadata and compressed-member skips may request a larger read.
        # Keep deadline/cancellation checkpoints between native IO chunks.
        remaining = 65536 if size < 0 else size
        chunks = []
        while remaining:
            self.context.remaining_seconds()
            block = self.source.read(min(65536, remaining))
            self.context.remaining_seconds()
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
        return b"".join(chunks)

    def seek(self, offset, whence=0):
        self.context.remaining_seconds()
        value = self.source.seek(offset, whence)
        self.context.remaining_seconds()
        return value

    def tell(self):
        self.context.remaining_seconds()
        return self.source.tell()

    def __getattr__(self, name):
        return getattr(self.source, name)


def bundle_delivery_files(artifact, profile, destination, *, context=None):
    """Validate names and stream declared files within one finite IO deadline."""
    context = context or Deadline(120)
    context.remaining_seconds()
    shape = task_shape(profile)
    if not shape["bundle"]:
        raise ValueError("this task delivers its single original file directly")
    destination = Path(destination)
    with Path(artifact).open("rb") as raw, tarfile.open(
            fileobj=_ArchiveReader(raw, context), mode="r:*") as archive:
        selected, names = [], set()
        for index, item in enumerate(archive):
            context.remaining_seconds()
            if index >= MAX_BUNDLE_FILES * 2:
                raise ValueError("delivery TAR exceeds its entry count bound")
            name = item.name.rstrip("/") if item.isdir() else item.name
            path = PurePosixPath(name)
            if (not name or "\0" in name or "\\" in name or path.is_absolute()
                    or path.as_posix() != name or any(part in {"", ".", ".."} for part in name.split("/"))
                    or len(name.encode("utf-8")) > 4096):
                raise ValueError("delivery TAR contains an invalid relative path")
            if profile == "highdim-mediation-debiasing":
                allowed = not item.isdir() and name in {"starter_code.R", "helper_functions.R"}
            else:
                allowed = path.parts[0] == "small_area_submission" and (item.isdir() or len(path.parts) >= 2)
            if not allowed:
                raise ValueError("delivery TAR contains a path outside the editable task artifact")
            if item.isdir():
                continue
            if not item.isfile():
                raise ValueError("delivery TAR entries must be regular files")
            if name in names or item.size < 0:
                raise ValueError("delivery TAR has duplicate files or an invalid declared size")
            names.add(name)
            selected.append((item, path))
        if len(selected) > MAX_BUNDLE_FILES:
            raise ValueError("delivery TAR exceeds its file count bound")
        expected = {"starter_code.R", "helper_functions.R"}
        if profile == "highdim-mediation-debiasing" and names != expected:
            raise ValueError("delivery TAR must contain both declared R files")
        if profile == "small-area-equivalence" and "small_area_submission/solve.py" not in names:
            raise ValueError("delivery TAR must include small_area_submission/solve.py")
        if any(PurePosixPath(name).parent.as_posix() in names or
               any(parent.as_posix() in names for parent in PurePosixPath(name).parents)
               for name in names):
            raise ValueError("delivery TAR has conflicting file paths")
        context.remaining_seconds()
        destination.mkdir(mode=0o700)
        delivered = []
        for item, path in sorted(selected, key=lambda row: row[1].as_posix()):
            context.remaining_seconds()
            target = destination / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with archive.extractfile(item) as source, target.open("xb") as stream:
                size = 0
                while True:
                    context.remaining_seconds()
                    block = source.read(65536)
                    context.remaining_seconds()
                    if not block:
                        break
                    size += len(block)
                    if size > item.size:
                        raise ValueError("delivery TAR decompressed file exceeds its declared bound")
                    stream.write(block)
            context.remaining_seconds()
            if size != item.size:
                raise ValueError("delivery TAR file is incomplete")
            target.chmod(0o600 | (item.mode & 0o111))
            original = (PurePosixPath("/root/project") / path if profile == "highdim-mediation-debiasing"
                        else PurePosixPath("/app") / path)
            delivered.append((target, original.as_posix()))
    context.remaining_seconds()
    return delivered


async def _bundle_delivery_files(artifact, profile, destination, *, timeout):
    """Join the local extractor after cancellation before any final upload."""
    deadline = Deadline(min(120., timeout))
    cancelled = threading.Event()
    def remaining_seconds():
        if cancelled.is_set():
            raise TimeoutError("delivery extraction cancelled")
        return deadline.remaining_seconds()
    context = SimpleNamespace(remaining_seconds=remaining_seconds)
    extraction = asyncio.create_task(asyncio.to_thread(bundle_delivery_files,
        artifact, profile, destination, context=context))
    try:
        return await asyncio.shield(extraction)
    except BaseException:
        cancelled.set()
        # A cancelled await does not stop a thread. Join its next cooperative
        # checkpoint before cleanup can finish or anything can be promoted.
        while not extraction.done():
            try:
                await asyncio.shield(extraction)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if extraction.done() and not extraction.cancelled():
            extraction.exception()
        raise


def _toml(value):
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return "true" if value else "false"
    if type(value) in (int, float) and math.isfinite(value):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(part) for part in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(_toml(key) + " = " + _toml(part) for key, part in value.items()) + " }"
    raise ValueError("unsupported template value")


def render_configuration(template, bindings, *, max_seconds, model_name):
    """Render explicit host-only bindings into an ordinary user-owned TOML."""
    text = template
    for key, value in bindings.items():
        token = "__SCID_" + key + "__"
        if token not in text:
            raise ValueError("configuration template is missing " + token)
        text = text.replace(token, _toml(value))
    if re.search(r"__SCID_[A-Z_]+__", text):
        raise ValueError("configuration template contains an unbound host field")
    parsed = tomllib.loads(text)
    executor = parsed.get("executor", {})
    if (executor.get("mode") not in (None, "local")
            or executor.get("config", {}).get("executor") != "harbor_execution:experiment"):
        raise ValueError("the host entry requires the registered harbor_execution:experiment provider")
    expected_seconds = "unlimited" if max_seconds is None else max_seconds
    if parsed.get("budget", {}).get("max_seconds") != expected_seconds:
        raise ValueError("the research and Harbor deadlines must use the same max_seconds")
    profile = parsed.get("agent", {})
    if model_name is not None and profile.get("model") != model_name:
        raise ValueError("Harbor model_name must match the declared research model")
    return text, parsed


def publish_usage(status, context):
    """Project existing cumulative usage; input_tokens already includes cache."""
    usage = status.get("usage", {})
    if not isinstance(usage, dict):
        return
    for source, target in (("input_tokens", "n_input_tokens"),
                           ("cached_input_tokens", "n_cache_tokens"),
                           ("output_tokens", "n_output_tokens")):
        value = usage.get(source)
        if type(value) is int and value >= 0:
            setattr(context, target, value)


def confirmed_artifact(status, delivery_root):
    """Select solely the exact confirmed directory-delivery operation."""
    closeout = status.get("closeout", {})
    if not isinstance(closeout, dict) or closeout.get("status") != "delivery_confirmed":
        raise ValueError("research has no confirmed directory delivery")
    confirmed = closeout.get("last_confirmed")
    if (not isinstance(confirmed, dict) or confirmed.get("status") != "confirmed"
            or confirmed.get("cleanup_confirmed") is not True
            or not re.fullmatch(r"[0-9a-f]{64}", str(confirmed.get("operation_id", "")))):
        raise ValueError("confirmed delivery operation identity is missing")
    directory = Path(delivery_root) / confirmed["operation_id"]
    manifest_path = directory / "manifest.json"
    artifact = directory / "artifact"
    for path in (manifest_path, artifact):
        if path.resolve() != path or not path.is_file():
            raise ValueError("confirmed delivery requires exact regular files")
    # This is compact delivery handoff metadata, separate from artifact bytes.
    if manifest_path.stat().st_size > MAX_STATUS_BYTES:
        raise ValueError("delivery manifest exceeds the read bound")
    manifest = json.loads(manifest_path.read_text())
    if any(manifest.get(key) != confirmed.get(key) for key in ("operation_id", "candidate_id", "artifact_ref")):
        raise ValueError("directory delivery differs from the confirmed operation")
    if manifest.get("objects", {}).get("artifact") != confirmed.get("artifact_ref"):
        raise ValueError("directory delivery artifact binding differs")
    reference = confirmed.get("artifact_ref", {})
    if type(reference.get("size_bytes")) is not int or artifact.stat().st_size != reference["size_bytes"]:
        raise ValueError("confirmed artifact size differs")
    return artifact


def finished_without_delivery(status, report, *, instance):
    """Recognize the controller's ordinary sealed A finish, without a submission."""
    if (not isinstance(report, dict) or status.get("closeout", {}) is not None
            or report.get("status") != "finish" or report.get("scientific_route") != "finish"
            or report.get("role") != "A" or status.get("instance") != instance
            or not isinstance(report.get("goal_id"), str) or not report["goal_id"]
            or report["goal_id"] != status.get("goal_id")):
        return False
    step = report.get("last_step")
    nodes = status.get("nodes")
    if type(step) is not int or step <= 0 or not isinstance(nodes, list) or not nodes:
        return False
    latest = nodes[-1]
    return (isinstance(latest, dict) and latest.get("step") == step
        and latest.get("role") == "A" and latest.get("state") == "seal_present_unverified")


def technical_pause_hold(status, terminal, *, instance):
    """Only the owner's actionable, clean prelaunch stop can retain this trial."""
    if (not isinstance(terminal,dict) or terminal.get('cleanup_confirmed') is not True
            or terminal.get('reason') != 'exited' or type(terminal.get('returncode')) is not int or terminal['returncode'] != 2
            or not isinstance(status,dict) or status.get('instance') != instance
            or not isinstance(status.get('goal_id'),str) or not status['goal_id']):
        return False
    stopped=status.get('technical_stop')
    if (not isinstance(stopped,dict) or stopped.get('phase') != 'native_prelaunch'
            or stopped.get('stage') != 'technical_pause'
            or stopped.get('reason') not in {'native_metadata_unsupported','shared_input_view_unsupported'}
            or stopped.get('repair_scope') != 'worker_attempt'
            or stopped.get('recovery_available') is not True
            or stopped.get('recheck_required') is not True
            or stopped.get('supported_actions') != ['worker-resume']
            or type(stopped.get('pending_step')) is not int or stopped['pending_step'] <= 0
            or type(stopped.get('expected_attempt')) is not int or stopped['expected_attempt'] < 0):
        return False
    nodes=status.get('nodes')
    if not isinstance(nodes,list) or not nodes:return False
    current=nodes[-1]
    return (isinstance(current,dict) and current.get('step') == stopped['pending_step']
        and current.get('role') == stopped.get('pending_role')
        and current.get('state') == 'native_not_submitted'
        and current.get('technical_stop') == stopped)


def _recovery_action(operation, stopped, *, instance):
    """An explicit accepted operation names this exact prelaunch source."""
    action=operation.get('action') or {}
    expected=action.get('expected_attempt',0)
    return (operation.get('kind') == 'controller'
        and action.get('kind') == 'worker_resume'
        and type(action.get('step')) is int and type(expected) is int
        and action.get('step') == stopped['pending_step']
        and expected == stopped['expected_attempt']
        and action.get('job_ref') == dict(path=str(Path(instance)/'jobs'/f'{stopped["pending_step"]:03d}.json')))


def _controller_process(operation):
    operation_id=operation.get('operation_id')
    process=operation.get('process')
    if (not isinstance(operation_id,str) or not re.fullmatch('[0-9a-f]{32}',operation_id)
            or not isinstance(process,dict) or process.get('task_ref') != operation_id
            or not isinstance(process.get('boot_id'),str) or not process['boot_id']
            or any(type(process.get(key)) is not int or process[key] <= 0
                for key in ('pid','start_ticks','pgid','sid','ppid'))):
        raise ValueError('recovery lost its exact controller process identity')
    return process


async def _command(argv, *, timeout=30, output=None, env=None):
    """Bound transport processes. Scientific host work has its own supervisor."""
    if output is not None:
        with Path(output).open("ab") as stream:
            process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
                stdout=stream, stderr=stream, start_new_session=True, env=env)
            try:
                await asyncio.wait_for(process.wait(), timeout)
            except BaseException:
                if process.returncode is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
                raise
        if process.returncode:
            raise RuntimeError("host command failed; see private command log")
        return ""
    process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True, env=env)
    async def read_bounded(stream):
        chunks, size = [], 0
        while True:
            part = await stream.read(min(65536, MAX_STATUS_BYTES + 1 - size))
            if not part:
                return b"".join(chunks)
            chunks.append(part)
            size += len(part)
            if size > MAX_STATUS_BYTES:
                raise ValueError("host response exceeds its read bound")
    try:
        stdout, stderr, _ = await asyncio.wait_for(asyncio.gather(
            read_bounded(process.stdout), read_bounded(process.stderr), process.wait()), timeout)
    except BaseException:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
        raise
    if process.returncode or len(stdout) > MAX_STATUS_BYTES or len(stderr) > MAX_STATUS_BYTES:
        raise RuntimeError("bounded host transport failed")
    return stdout.decode()


async def bind_main_container(environment, private_root, task_profile="symbolic-regression"):
    """Pinned private shim: exact main query, canonical inspection, cross-check."""
    if version("harbor") != HARBOR_VERSION or sys.platform != "linux" or type(environment) is not DockerEnvironment:
        raise ValueError("host entry requires Harbor 0.25.0 and its local Linux Docker environment")
    engine = list(type(environment).runtime().engine)
    if engine != ["docker"]:
        raise ValueError("only the original local Docker runtime is supported")
    result = await environment._run_docker_compose_command(
        ["ps", "--all", "--quiet", "main"], timeout_sec=15)
    identifiers = (result.stdout or "").split()
    if len(identifiers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", identifiers[0]):
        raise ValueError("exact main-service container lookup must return one Docker identity")
    inspected = json.loads(await _command([*engine, "inspect", identifiers[0]]))
    if not isinstance(inspected, list) or len(inspected) != 1:
        raise ValueError("exact main container inspection failed")
    item = inspected[0]
    container_id = item.get("Id", "")
    labels = item.get("Config", {}).get("Labels", {})
    project = re.sub(r"[^a-z0-9_-]", "-", environment.session_id.lower())
    if not re.match(r"^[a-z0-9]", project):
        project = "0" + project
    if (not re.fullmatch(r"[0-9a-f]{64}", container_id)
            or not container_id.startswith(identifiers[0])
            or item.get("State", {}).get("Running") is not True
            or labels.get("com.docker.compose.service") != "main"
            or labels.get("com.docker.compose.project") != project):
        raise ValueError("canonical original container identity does not match this Harbor environment")
    resolved_user = environment._resolve_user(None)
    user = (str(resolved_user) if resolved_user is not None
        else item.get("Config", {}).get("User") or "0")
    shape = task_shape(task_profile)
    if shape["runtime"] == "shell":
        probe_argv = ["/bin/bash", "-c", 'set -e; test "$(id -u)" = 0; '
            'for tool in setsid timeout cat stat mkfifo mkdir sleep date rm mv; do '
            'command -v "$tool" >/dev/null; done; '
            '/usr/bin/Rscript --vanilla -e \'cat("scid-original-environment-ok\\n")\'']
    else:
        probe = ("import os,sys; assert sys.platform=='linux' and os.geteuid()==0; ")
        if task_profile == "symbolic-regression":
            probe += ("import numpy,scipy,sklearn; "
                "assert (numpy.__version__,scipy.__version__,sklearn.__version__)==('2.0.0','1.14.0','1.5.0'); ")
        probe += "print('scid-original-environment-ok')"
        probe_argv = [CONTAINER_PYTHON, "-I", "-c", probe]
    try:
        checked = await _command([*engine, "exec", "--user", user, container_id,
            *probe_argv], timeout=15)
    except RuntimeError as error:
        raise ValueError("original task commands, interpreter, or effective root user are unsupported; no components were installed") from error
    if checked.strip() != "scid-original-environment-ok":
        raise ValueError("original task interpreter, packages, or effective root user are unsupported")
    # Docker exec already inherits the original container creation environment.
    # Harbor's phase overlay may contain inference credentials for the host agent.
    binding = dict(container_id=container_id, docker_argv=engine, environment={},
        user=user)
    (private_root / "binding.json").write_text(json.dumps({**binding,
        "session_id": environment.session_id, "context_id": str(environment.context_id),
        "image": item.get("Image"), "host_config": item.get("HostConfig", {})}, sort_keys=True))
    return binding


class SciDiscoveryHarborAgent(BaseAgent):
    """Runs the host CLI, publishing only an exact confirmed task artifact."""

    def __init__(self, logs_dir, model_name=None, *, config_template,
                 scid_argv=None, max_seconds=1800, poll_seconds=2,
                 task_profile="symbolic-regression", **kwargs):
        super().__init__(logs_dir=logs_dir, model_name=model_name, **kwargs)
        overlay = self.extra_env
        if any(key in overlay and overlay[key] != os.environ.get(key)
                for key in ("HOME", "CODEX_HOME")):
            raise ValueError("agent environment must preserve the original host HOME and CODEX_HOME")
        self._host_env = {**os.environ, **overlay}
        if max_seconds is not None and (type(max_seconds) not in (int, float)
                or not math.isfinite(max_seconds) or max_seconds <= 0):
            raise ValueError("max_seconds must be None or finite and positive")
        if type(poll_seconds) not in (int, float) or not .05 <= poll_seconds <= 30:
            raise ValueError("poll_seconds must be between .05 and 30")
        self.config_template = Path(config_template)
        if not self.config_template.is_absolute() or not self.config_template.is_file():
            raise ValueError("config_template must name an explicit existing absolute TOML file")
        self.scid_argv = ["scid-research"] if scid_argv is None else list(scid_argv)
        if not self.scid_argv or any(not isinstance(part, str) or not part for part in self.scid_argv):
            raise ValueError("scid_argv must be an explicit command argument list")
        self.max_seconds, self.poll_seconds = max_seconds, poll_seconds
        self.task_profile = task_profile
        self.shape = task_shape(task_profile)
        self.private_root = Path(logs_dir).absolute() / "scidiscovery-host"
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.binding = None
        self._runner = None

    @staticmethod
    def name():
        return "scidiscovery-host"

    def version(self):
        return "1"

    async def setup(self, environment):
        self.binding = await bind_main_container(environment, self.private_root, self.task_profile)

    async def _status(self, root, context, timeout=15):
        original = await _command([*self.scid_argv, "status", "--root", str(root),'--no-live-feedback'],
            timeout=timeout, env=self._host_env)
        status = json.loads(original)
        temporary = self.private_root / "final-status.json.part"
        temporary.write_text(original)
        temporary.replace(self.private_root / "final-status.json")
        publish_usage(status, context)
        return status

    async def _operations(self, root, *, timeout):
        response=await _command([*self.scid_argv,'operations','--root',str(root)],
            timeout=timeout,env=self._host_env)
        value=json.loads(response)
        operations=value.get('operations') if isinstance(value,dict) else None
        if not isinstance(operations,list) or any(not isinstance(row,dict) for row in operations):
            raise ValueError('host operation status is malformed')
        return operations

    async def _wait_technical_recovery(self, root, context, *, deadline, monotonic_deadline, initial_status):
        """Observe explicit recovery under this trial's unchanged hard deadline.

        The accepted worker-resume operation already runs the original
        controller after its replacement. This observer never starts a model
        or continues a scientific route on the operator's behalf.
        """
        if deadline is None or monotonic_deadline is None:
            raise RuntimeError('technical pause requires the original finite Harbor deadline')
        instance=str(root.resolve())
        source=dict(initial_status['technical_stop'])
        goal=initial_status.get('goal_id')
        # Cancellation can arrive in the first status/operation await, before
        # an accepted repair has been observed. Retain its exact source first.
        self._technical_hold_binding=dict(instance=instance,goal_id=goal,technical_stop=source)
        claimed=None
        while True:
            left=min(deadline-time.time(),monotonic_deadline-time.monotonic())
            if left <= 0:raise TimeoutError('original Harbor trial deadline reached during technical pause')
            status=await self._status(root,context,timeout=min(15.,left))
            if status.get('instance') != instance or status.get('goal_id') != goal:
                raise ValueError('technical recovery changed the original research identity')
            left=min(deadline-time.time(),monotonic_deadline-time.monotonic())
            if left <= 0:raise TimeoutError('original Harbor trial deadline reached during technical pause')
            operations=await self._operations(root,timeout=min(15.,left))
            relevant=[]
            for operation in sorted(operations,key=lambda row:row.get('sequence',0)):
                sequence=operation.get('sequence')
                if type(sequence) is not int or sequence <= 0:continue
                if claimed is None and _recovery_action(operation,source,instance=instance):
                    claimed=sequence
                    self._held_recovery_operation_id=operation.get('operation_id')
                    self._held_recovery_sequence=sequence
                if claimed is not None and sequence >= claimed and operation.get('kind') == 'controller':
                    relevant.append(operation)
            self._recovery_operations=relevant
            if claimed is None and not technical_pause_hold(status,
                    dict(reason='exited',returncode=2,cleanup_confirmed=True),instance=instance):
                raise RuntimeError('original technical pause no longer permits explicit recovery')
            for operation in relevant[-1:]:
                operation_id=operation.get('operation_id')
                if operation.get('status') != 'applied':continue
                process=_controller_process(operation)
                from scidiscovery.research_loop import runtime
                if runtime.process_matches(process,operation_id):continue
                report=operation.get('result')
                if not isinstance(report,dict):raise ValueError('applied recovery has no owner result')
                closeout=status.get('closeout')
                if (isinstance(closeout,dict) and closeout.get('status') == 'delivery_confirmed'
                        or finished_without_delivery(status,report,instance=instance)):
                    return status,report
                if (report.get('status') == 'delivery_confirmed'
                        or report.get('status') == 'finish' and report.get('scientific_route') == 'finish'
                            and report.get('role') == 'A'):
                    continue  # The status read may precede this applied receipt.
                nodes=status.get('nodes') or []
                if nodes and nodes[-1].get('state') in {'running','prepared','waiting'}:
                    continue  # A later explicit accepted owner can race these reads.
                stopped=status.get('technical_stop')
                if isinstance(stopped,dict) and stopped.get('phase') == 'native_prelaunch':
                    if not technical_pause_hold(status,dict(reason='exited',returncode=2,
                            cleanup_confirmed=True),instance=instance):
                        raise RuntimeError('technical recovery is unavailable under current original gates')
                elif report.get('scientific_route') != 'wait' and report.get('status') != 'paused':
                    raise RuntimeError('explicit recovery did not finish or reach an actionable original pause')
            left=min(deadline-time.time(),monotonic_deadline-time.monotonic())
            if left <= 0:raise TimeoutError('original Harbor trial deadline reached during technical pause')
            await asyncio.sleep(min(self.poll_seconds,left))

    async def _stop_container(self):
        if self.binding:
            await _command([*self.binding["docker_argv"], "stop", "--time", "2", self.binding["container_id"]], timeout=10)
            state = json.loads(await _command([*self.binding["docker_argv"], "inspect", self.binding["container_id"]], timeout=5))
            if len(state) != 1 or state[0].get("State", {}).get("Running") is not False:
                raise RuntimeError("original task container stopping is unconfirmed")

    async def _stop_host(self):
        if self._runner is not None and self._runner.returncode is None:
            try:
                self._runner.terminate()
            except ProcessLookupError:
                pass
            await asyncio.wait_for(self._runner.wait(), 12)
        if self._runner is not None:
            receipt = json.loads((self.private_root / "host-terminal.json").read_text())
            if receipt.get("cleanup_confirmed") is not True:
                raise RuntimeError("host descendant cleanup is unconfirmed")

    async def _stop_recovery_owner(self, root):
        binding=getattr(self,'_technical_hold_binding',None)
        held=getattr(self,'_held_recovery_operation_id',None)
        if binding is None and held is None:return
        result=dict(status='cleanup_unknown',cleanup_confirmed=False,held_operation_id=held)
        try:
            if not isinstance(binding,dict) or binding.get('instance') != str(root.resolve()):
                raise ValueError('held recovery source is unavailable')
            operations=await asyncio.wait_for(self._operations(root,timeout=3),3)
            matching=[row for row in operations if type(row.get('sequence')) is int
                and row['sequence'] > 0
                and _recovery_action(row,binding['technical_stop'],instance=binding['instance'])]
            if held is not None:
                matching=[row for row in matching if row.get('operation_id') == held]
            from scidiscovery.research_loop import control_operations
            if not matching:
                if held is not None:raise ValueError('exact held repair operation is unavailable')
                source=binding['technical_stop']
                result=await asyncio.to_thread(control_operations.confirm_no_held_recovery,root,
                    goal_id=binding['goal_id'],step=source['pending_step'],
                    expected_attempt=source['expected_attempt'])
            else:
                original=min(matching,key=lambda row:row['sequence'])
                held=original['operation_id']
                result['held_operation_id']=held
                sequence=original['sequence']
                self._held_recovery_operation_id=held
                self._held_recovery_sequence=sequence
                current=[row for row in operations if row.get('kind') == 'controller'
                    and type(row.get('sequence')) is int
                    and row['sequence'] >= sequence]
                if not current:raise ValueError('held recovery owner is unavailable')
                active=max(current,key=lambda row:row['sequence'])
                result=await asyncio.to_thread(control_operations.stop_held_recovery,root,
                    held_operation_id=held,active_operation_id=active['operation_id'])
        except Exception:
            pass  # The receipt keeps uncertainty; container cleanup still runs.
        temporary=self.private_root/'held-recovery-cleanup.json.part'
        temporary.write_text(json.dumps(result,allow_nan=False))
        temporary.replace(self.private_root/'held-recovery-cleanup.json')
        if result.get('cleanup_confirmed') is not True:
            raise RuntimeError('held recovery cleanup_unknown')

    async def run(self, instruction, environment, context):
        if self.binding is None:
            raise RuntimeError("setup must bind the original Harbor container first")
        from scidiscovery.research_loop.generic_delivery import descriptor
        started = time.time()
        deadline = None if self.max_seconds is None else started + self.max_seconds
        monotonic_deadline=None if self.max_seconds is None else time.monotonic()+self.max_seconds

        def transport_timeout(limit, *, total_only=False):
            if deadline is None:
                return limit
            remaining = min(deadline-time.time(),monotonic_deadline-time.monotonic())
            return max(.01, remaining if total_only else min(limit, remaining))

        root = self.private_root / "instance"
        public = self.private_root / "public"
        public.mkdir()
        delivery = self.private_root / "delivery"
        delivery.mkdir()
        objective = self.private_root / "objective.md"
        objective.write_text(instruction)
        navigation = self.private_root / "navigation.md"
        navigation.write_text(task_navigation(self.task_profile))
        completed = False
        try:
            for original, name in self.shape["public_files"]:
                await asyncio.wait_for(environment.download_file(original, public / name),
                    transport_timeout(120, total_only=True))
            adapter = descriptor(delivery)
            bindings = dict(INSTANCE_ROOT=str(root), OBJECTIVE=str(objective),
                PUBLIC_ROOT=str(public), NAVIGATION=str(navigation),
                DELIVERY_ADAPTER=adapter, CONTAINER_ID=self.binding["container_id"],
                CONTAINER_ROOT="/tmp/scid-" + uuid4().hex,
                DOCKER_ARGV=self.binding["docker_argv"], CONTAINER_ENV=self.binding["environment"],
                CONTAINER_USER=self.binding["user"])
            if deadline is not None:
                bindings["DEADLINE_EPOCH"] = deadline
            template = self.config_template.read_text()
            for key, value in (("CONTAINER_PYTHON_ARGV", self.shape["argv"]),
                               ("RUNNER_MODE", self.shape["runtime"])):
                if "__SCID_" + key + "__" in template:
                    bindings[key] = value
            text, parsed = render_configuration(template, bindings,
                max_seconds=self.max_seconds, model_name=self.model_name)
            provider = parsed["executor"]["config"].get("configuration", {})
            if (provider.get("python_argv", [CONTAINER_PYTHON, "-B", "-u"]) != self.shape["argv"]
                    or provider.get("runner_mode", "python") != self.shape["runtime"]):
                raise ValueError("task_profile requires its original runtime argv and runner_mode in the template")
            config = self.private_root / "research.toml"
            config.write_text(text)
            for verb in ("config-check", "prepare"):
                await _command([*self.scid_argv, verb, "--config", str(config)],
                    timeout=max(.1, transport_timeout(60)), output=self.private_root / "prepare.log",
                    env=self._host_env)
            receipt = self.private_root / "host-terminal.json"
            run_log = self.private_root / "run.log"
            deadline_argv = [] if deadline is None else ["--deadline-epoch", str(deadline)]
            with run_log.open("ab") as stream:
                self._runner = await asyncio.create_subprocess_exec(sys.executable, "-B",
                    str(Path(__file__).with_name("host_runner.py")), *deadline_argv,
                    "--receipt", str(receipt), "--", *self.scid_argv, "run", "--root", str(root),
                    stdin=asyncio.subprocess.DEVNULL, stdout=stream, stderr=stream, env=self._host_env)
                waiter = asyncio.create_task(self._runner.wait())
                while self._runner.returncode is None:
                    if deadline is not None and time.time() >= deadline:
                        raise TimeoutError("original Harbor trial deadline reached")
                    await self._status(root, context, timeout=transport_timeout(15))
                    try:
                        await asyncio.wait_for(asyncio.shield(waiter),
                            transport_timeout(self.poll_seconds))
                    except asyncio.TimeoutError:
                        pass
            terminal = json.loads(receipt.read_text())
            if not isinstance(terminal, dict) or terminal.get("cleanup_confirmed") is not True:
                raise RuntimeError("foreground host work did not finish with confirmed cleanup")
            status = await self._status(root, context, timeout=transport_timeout(15))
            recovered_report=None
            if (deadline is not None and self._runner.returncode == 2
                    and technical_pause_hold(status,terminal,instance=str(root.resolve()))):
                status,recovered_report=await self._wait_technical_recovery(root,context,
                    deadline=deadline,monotonic_deadline=monotonic_deadline,initial_status=status)
            elif (self._runner.returncode != 0 or terminal.get("reason") != "exited"
                    or type(terminal.get("returncode")) is not int or terminal["returncode"] != 0):
                raise RuntimeError(
                    f"foreground host work failed (reason {terminal.get('reason')}, "
                    f"exit code {terminal.get('returncode')}); "
                    f"see private host log {run_log} and terminal receipt {receipt}")
            if status.get("closeout", {}) is None:
                # CLI run stdout is one terminal control report. Load it once;
                # no history or scientific originals are reopened for this gate.
                if recovered_report is not None:report=recovered_report
                else:
                    with run_log.open("rb") as stream:
                        original = stream.read(MAX_STATUS_BYTES + 1)
                    if len(original) > MAX_STATUS_BYTES:
                        raise ValueError("host terminal report exceeds its read bound")
                    report = json.loads(original)
                if not finished_without_delivery(status, report, instance=str(root.resolve())):
                    raise ValueError("research has neither confirmed delivery nor an ordinary sealed finish")
                artifact = None
            else:
                artifact = confirmed_artifact(status, delivery)
            if deadline is not None and time.time() >= deadline:
                raise TimeoutError("original Harbor trial deadline reached before final export")
            if artifact is None:
                # Retain the original task files for Harbor's own verifier.
                # A scientific finish supplies no candidate or success verdict.
                completed = True
                return
            if self.shape["bundle"]:
                files = await _bundle_delivery_files(artifact, self.task_profile,
                    self.private_root / "selected-files", timeout=transport_timeout(120, total_only=True))
                parents = sorted({str(PurePosixPath(original).parent) for _, original in files})
                await _command([*self.binding["docker_argv"], "exec", "--user", self.binding["user"],
                    self.binding["container_id"], "/bin/sh", "-c", 'mkdir -p -- "$@"',
                    "scid-delivery", *parents], timeout=transport_timeout(30))
                for selected, original in files:
                    await asyncio.wait_for(environment.upload_file(selected, original),
                        transport_timeout(120, total_only=True))
            else:
                await asyncio.wait_for(environment.upload_file(artifact, self.shape["artifact"]),
                    transport_timeout(120, total_only=True))
            completed = True
        finally:
            if not completed:
                # Shield cleanup from the outer Harbor cancellation. Container
                # removal and subsequent verifier lifecycle remain Harbor-owned.
                cleanup = asyncio.create_task(self._cleanup(root, context))
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    await cleanup

    async def _cleanup(self, root, context):
        host_error = None
        try:
            await self._stop_host()
        except BaseException as error:
            host_error = error
        try:
            await self._stop_recovery_owner(root)
        except BaseException as error:
            host_error=host_error or error
        try:
            await self._stop_container()
        finally:
            if root.exists():
                try:
                    await self._status(root, context)
                except Exception:
                    pass
        if host_error:
            raise RuntimeError("host descendant shutdown was not confirmed (cleanup_unknown)") from host_error
