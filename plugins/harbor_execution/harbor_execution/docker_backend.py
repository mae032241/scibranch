"""Bounded transport to one canonical, already-existing Harbor container."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import tempfile
import time

from scidiscovery.plugin_runtime.collection import CollectionContext, run_bounded

from .runner import SHELL_RUNNER


class BackendError(RuntimeError):
    """Private transport facts; the public provider must redact this error."""

    def __init__(self, message, **facts):
        super().__init__(message)
        self.facts = facts


class FileMissing(BackendError):
    pass


class FileChanged(BackendError):
    """The named original changed during its exact transfer."""


# Compatibility import for callers of the previous provider. No byte quota is applied.
FileTooLarge = FileChanged


class FileInvalid(BackendError):
    pass


class UnsupportedUser(BackendError):
    """Fixed diagnostic for the first provider's root-effective-user scope."""


MAX_RESPONSE_BYTES = 128 * 1024
TRANSPORT_SECONDS = 30.0
CALL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")

# Claim transfer before any copy. If this process disappears while copying,
# the original claim remains unknown and cannot launch again through recovery.
_PREPARE = r'''
import json,os,sys
root,request=sys.argv[1],json.loads(sys.argv[2])
os.makedirs(os.path.dirname(root),mode=0o700,exist_ok=True)
try:
 os.mkdir(root,0o700)
except FileExistsError:
 print('{"created":false}')
else:
 for name in ('code','work','control'):os.mkdir(os.path.join(root,name),0o700)
 target=os.path.join(root,'control','request.json')
 descriptor=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(descriptor,'w') as stream:
  json.dump(request,stream,allow_nan=False,sort_keys=True)
  stream.flush();os.fsync(stream.fileno())
 descriptor=os.open(os.path.join(root,'control'),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 os.fsync(descriptor);os.close(descriptor)
 for directory in (root,os.path.dirname(root)):
  descriptor=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
  os.fsync(descriptor);os.close(descriptor)
 print('{"created":true}')
'''

_PROBE = r'''
import json,os,stat,sys
root=sys.argv[1]
path=os.path.join(root,'control','runner.py')
try:mode=os.lstat(path).st_mode
except FileNotFoundError:mode=0
print(json.dumps({'runner_present':stat.S_ISREG(mode)}))
'''

_PREPARE_SHELL = r'''
set -eu
root=$1
mkdir -p "${root%/*}"
if mkdir "$root" 2>/dev/null; then
 chmod 700 "$root"
 mkdir "$root/code" "$root/work" "$root/control"
 printf '%s' "$2" > "$root/control/request.json"
 printf '%s' "$3" > "$root/control/request.sh"
 chmod 600 "$root/control/request.json" "$root/control/request.sh"
 printf '{"created":true}\n'
else
 printf '{"created":false}\n'
fi
'''


# Collection is independent of whichever supervisor was frozen for this call.
# This read-only command neither changes that supervisor nor claims execution.
_READ_FILE = r'''
import errno,os,stat,sys
root,relative=sys.argv[1:]
fd=directory=None
try:
 if (not relative or relative.startswith('/') or '\0' in relative or '\\' in relative
     or any(part in ('','.','..') for part in relative.split('/'))):
  sys.exit(5)
 directory=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
 parts=relative.split('/')
 for part in parts[:-1]:
  next_directory=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
  os.close(directory);directory=next_directory
 fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
 before=os.fstat(fd)
 if not stat.S_ISREG(before.st_mode):sys.exit(5)
 with os.fdopen(fd,'rb') as source:
  fd=None
  sys.stdout.buffer.write(str(before.st_size).encode('ascii')+b'\n')
  remaining=before.st_size
  while remaining:
   block=source.read(min(65536,remaining))
   if not block:sys.exit(4)
   sys.stdout.buffer.write(block);remaining-=len(block)
  if source.read(1):sys.exit(4)
  after=os.fstat(source.fileno())
  fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
  if any(getattr(before,key)!=getattr(after,key) for key in fields):sys.exit(4)
except FileNotFoundError:sys.exit(3)
except OSError as error:
 if error.errno in (errno.ELOOP,errno.ENOTDIR):sys.exit(5)
 raise
finally:
 if fd is not None:os.close(fd)
 if directory is not None:os.close(directory)
'''

_READ_FILE_SHELL = r'''
set -eu
root=$1
relative=$2
[[ "$relative" != /* && "$relative" != *\\* ]] || exit 5
IFS=/ read -ra parts <<< "$relative"
path=$root
for part in "${parts[@]}"; do
 [[ -n "$part" && "$part" != . && "$part" != .. ]] || exit 5
 path=$path/$part
 [[ ! -L "$path" ]] || exit 5
done
[[ -e "$path" ]] || exit 3
[[ -f "$path" ]] || exit 5
before=$(stat -c '%d:%i:%s:%y:%z' "$path") || exit 2
size=$(stat -c %s "$path") || exit 2
printf '%s\n' "$size"
cat "$path" || exit 2
after=$(stat -c '%d:%i:%s:%y:%z' "$path") || exit 2
[[ "$before" == "$after" ]] || exit 4
'''


class DockerBackend:
    def __init__(self, config):
        self.config = dict(config)
        configured_user = config.get("user")
        if configured_user is not None and not self._root_user(configured_user):
            raise ValueError("Harbor Docker execution currently supports only the original root effective user")
        self.container_id = config["container_id"]
        if not isinstance(self.container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", self.container_id):
            raise ValueError("a canonical full container ID is required")
        self.docker_argv = list(config["docker_argv"])
        self.python_argv = list(config["python_argv"])
        self.runner_mode = config.get("runner_mode", "python")
        if self.runner_mode not in {"python", "shell"}:
            raise ValueError("runner_mode must be python or shell")
        if any(part in (".", "..") for part in str(config["container_root"]).split("/")):
            raise ValueError("execution scratch must have canonical path components")
        self.container_root = str(PurePosixPath(config["container_root"]))
        root = PurePosixPath(self.container_root)
        if not root.is_absolute() or str(root) == "/" or root == PurePosixPath("/app") or PurePosixPath("/app") in root.parents:
            raise ValueError("execution scratch must be absolute and outside /app")
        if not self.docker_argv or not self.python_argv or not os.path.isabs(self.python_argv[0]):
            raise ValueError("explicit engine and original interpreter commands are required")
        for value in self.docker_argv + self.python_argv:
            if not isinstance(value, str) or not value or "\x00" in value:
                raise ValueError("invalid transport command")
        cap = config["max_seconds"]
        if type(cap) not in (int, float) or not math.isfinite(cap) or cap <= 0:
            raise ValueError("finite execution bound is required")
        self.max_seconds = float(cap)
        outer_deadline = config.get("deadline_epoch")
        if outer_deadline is not None and (type(outer_deadline) not in (float, int) or not math.isfinite(outer_deadline)):
            raise ValueError("whole-trial deadline must be finite")

    def _root(self, call_id):
        if not isinstance(call_id, str) or not CALL_ID.fullmatch(call_id):
            raise ValueError("invalid original call ID")
        return str(PurePosixPath(self.container_root) / call_id)

    @staticmethod
    def _root_user(value):
        return isinstance(value, str) and (not value or value.split(":", 1)[0] in ("0", "root"))

    def _exec(self, command):
        argv = self.docker_argv + ["exec", "-i"]
        if self.config.get("user") is not None:
            argv += ["--user", self.config["user"]]
        for key, value in sorted(self.config.get("environment", {}).items()):
            argv += ["--env", key + "=" + value]
        return argv + [self.container_id] + list(command)

    def _run(self, command, *, sink=None, timeout=TRANSPORT_SECONDS, max_bytes=MAX_RESPONSE_BYTES, context=None):
        try:
            if context is not None:
                timeout = min(timeout, context.remaining_seconds())
            result = run_bounded(command, input=b"", timeout=timeout, sink=sink,
                                 max_output_bytes=max_bytes, idle_seconds=min(30.0, timeout), context=context)
            if context is not None:
                context.remaining_seconds()
        except BackendError:
            raise
        except Exception as error:
            raise BackendError("Docker transport did not return a complete response",
                               error_type=type(error).__name__, stdout=getattr(error, "stdout", b"")[:MAX_RESPONSE_BYTES],
                               stderr=getattr(error, "stderr", b"")[:MAX_RESPONSE_BYTES]) from error
        return result

    def _json(self, command, *, context=None):
        result = self._run(command, context=context)
        if result.returncode != 0:
            raise BackendError("Docker operation failed", returncode=result.returncode,
                               stdout=result.stdout, stderr=result.stderr)
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeError) as error:
            raise BackendError("Docker operation returned an invalid native response",
                               stdout=result.stdout, stderr=result.stderr) from error
        if not isinstance(value, dict):
            raise BackendError("Docker native response must be an object")
        return value

    def _inspect(self, *, context=None):
        template = '{"Id":{{json .Id}},"State":{"Running":{{json .State.Running}}},"Config":{"User":{{json .Config.User}}}}'
        value = self._json(self.docker_argv + ["inspect", "--format", template, self.container_id], context=context)
        if value.get("Id") != self.container_id:
            raise BackendError("Docker inspection did not confirm the bound original ID")
        if not self._root_user(value.get("Config", {}).get("User", "")):
            raise UnsupportedUser("Harbor Docker execution currently supports only the original root effective user")
        return bool(value.get("State", {}).get("Running"))

    def _runner(self, call_id, operation, *arguments):
        root = self._root(call_id)
        if self.runner_mode == "shell":
            # Preserve the existing private runner interface at this boundary.
            if operation == "read-file":
                arguments = (arguments[1],)
            return self._exec(["/bin/bash", root + "/control/runner.sh", operation,
                               root, *arguments])
        return self._exec(self.python_argv + [root + "/control/runner.py", operation,
                                              "--call-root", root] + list(arguments))

    def _unknown(self, call_id, reason, *, cleanup_confirmed=False):
        return dict(call_id=call_id, state="unknown", reason=reason, returncode=None,
                    started_at=None, finished_at=None, deadline_epoch=None,
                    cleanup_confirmed=cleanup_confirmed,
                    native_identity={"container_id": self.container_id})

    def launch(self, call_id, code_root, entry, arguments, *, deadline_epoch):
        root = self._root(call_id)
        self._relative(entry)
        code_root = Path(code_root)
        if code_root.is_symlink() or not code_root.is_dir():
            raise ValueError("admitted code export must be a directory")
        if not isinstance(arguments, list) or not all(isinstance(value, str) and "\x00" not in value for value in arguments):
            raise ValueError("scientific arguments must be a list of strings")
        if type(deadline_epoch) not in (int, float) or not math.isfinite(deadline_epoch):
            raise ValueError("finite original deadline is required")
        deadline = min(float(deadline_epoch), time.time() + self.max_seconds)
        if self.config.get("deadline_epoch") is not None:
            deadline = min(deadline, float(self.config["deadline_epoch"]))
        if not self._inspect():
            raise BackendError("bound original container is not running")
        request = dict(call_id=call_id, container_id=self.container_id, entry=entry,
                       arguments=list(arguments), deadline_epoch=deadline,
                       python_argv=self.python_argv)
        encoded = json.dumps(request, allow_nan=False, separators=(",", ":"))
        if self.runner_mode == "shell":
            values = dict(call_id=call_id, container_id=self.container_id,
                          deadline_epoch=str(deadline), deadline_seconds=str(math.floor(deadline)))
            settings = "\n".join(key + "=" + shlex.quote(value) for key, value in values.items())
            science = self.python_argv + [root + "/code/" + entry] + list(arguments)
            settings += "\nscience_argv=(" + shlex.join(science) + ")\n"
            prepared = self._json(self._exec(["/bin/bash", "-c", _PREPARE_SHELL,
                                             "scid-prepare", root, encoded, settings]))
        else:
            prepared = self._json(self._exec(self.python_argv + ["-c", _PREPARE, root, encoded]))
        if not prepared.get("created"):
            return self.state(call_id)
        # The only directory transfer is the exact host-admitted code export.
        # No container or workspace inventory, hashing, or task-data copy occurs.
        transfers = [(str(code_root) + "/.", root + "/code")]
        if self.runner_mode == "python":
            transfers.append((str(Path(__file__).with_name("runner.py")), root + "/control/runner.py"))
        for source, destination in transfers:
            result = self._run(self.docker_argv + ["cp", source, self.container_id + ":" + destination])
            if result.returncode:
                raise BackendError("Docker upload failed after the original transfer claim",
                                   returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        if self.runner_mode == "shell":
            with tempfile.NamedTemporaryFile(mode="w", prefix="scid-harbor-runner-", suffix=".sh") as script:
                script.write(SHELL_RUNNER)
                script.flush()
                result = self._run(self.docker_argv + ["cp", script.name,
                    self.container_id + ":" + root + "/control/runner.sh"])
                if result.returncode:
                    raise BackendError("Docker shell-runner upload failed after the original transfer claim",
                                       returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
        return self._validate_state(call_id, self._json(self._runner(call_id, "launch")))

    def _validate_state(self, call_id, value):
        if not isinstance(value, dict):
            raise BackendError("native state must be an object")
        if value.get("call_id") != call_id:
            raise BackendError("native response original call identity differs")
        identity = value.get("native_identity", {})
        if not isinstance(identity, dict) or identity.get("container_id") != self.container_id:
            raise BackendError("native response original container identity differs")
        if value.get("state") not in {"submitted", "running", "cancelling", "succeeded", "failed", "cancelled", "unknown"}:
            raise BackendError("native response has an invalid state")
        if value["state"] in ("succeeded", "failed", "cancelled") and value.get("cleanup_confirmed") is not True:
            raise BackendError("native terminal response lacks confirmed descendant cleanup")
        if value["state"] == "succeeded" and value.get("returncode") != 0:
            raise BackendError("native success lacks the actual zero return code")
        return value

    def state(self, call_id):
        root = self._root(call_id)
        if not self._inspect():
            # Stopping the exact whole container proves writers stopped, but
            # supplies neither a missing return code nor scientific success.
            return self._unknown(call_id, "original_container_stopped", cleanup_confirmed=True)
        if self.runner_mode == "shell":
            probe = self._json(self._exec(["/bin/bash", "-c",
                'if [[ -f "$1/control/runner.sh" && ! -L "$1/control/runner.sh" ]]; then printf \'{"runner_present":true}\'; else printf \'{"runner_present":false}\'; fi',
                "scid-probe", root]))
        else:
            probe = self._json(self._exec(self.python_argv + ["-c", _PROBE, root]))
        if not probe.get("runner_present"):
            return self._unknown(call_id, "original_native_runner_missing")
        result = self._run(self._runner(call_id, "status"))
        if result.returncode == 3:
            return self._unknown(call_id, "original_native_claim_missing")
        if result.returncode:
            raise BackendError("native status failed", returncode=result.returncode,
                               stdout=result.stdout, stderr=result.stderr)
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeError) as error:
            raise BackendError("native status is incomplete", stdout=result.stdout, stderr=result.stderr) from error
        return self._validate_state(call_id, value)

    def cancel(self, call_id):
        observed = self.state(call_id)
        if observed["state"] not in ("running", "submitted", "cancelling"):
            return observed
        return self._validate_state(call_id, self._json(self._runner(call_id, "cancel")))

    @staticmethod
    def _relative(value):
        if not isinstance(value, str) or not value or "\x00" in value or PurePosixPath(value).is_absolute() or any(
            part in ("", ".", "..") for part in value.split("/")
        ):
            raise ValueError("output must name one explicit relative file")
        return value

    def read_file(self, call_id, relative_path, destination, *, max_bytes=None, timeout_seconds):
        self._root(call_id)
        self._relative(relative_path)
        # max_bytes is a legacy compatibility keyword; originals have no byte quota.
        if type(timeout_seconds) not in (float, int) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("file timeout must be finite and positive")
        deadline = time.monotonic() + timeout_seconds
        context = CollectionContext(deadline_monotonic=deadline, stop_deadline_monotonic=deadline,
                                    file_timeout_seconds=timeout_seconds,
                                    idle_timeout_seconds=min(30., timeout_seconds))
        destination = Path(destination)
        # The caller owns the existing exact-call collection lease. A final
        # name is a completed transfer proof; a crashed transfer leaves only
        # this explicitly named, replaceable part (never a partial final).
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(str(destination))
        if not self._inspect(context=context):
            raise BackendError("bound original container is not running")
        temporary = destination.with_name(destination.name + ".harbor-part")
        try:
            partial_mode = temporary.lstat().st_mode
        except FileNotFoundError:
            partial_mode = None
        if partial_mode is not None:
            if not stat.S_ISREG(partial_mode):
                raise ValueError("transfer part must be an owned regular file")
            temporary.unlink()
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        count, declared, header = 0, None, bytearray()
        try:
            with os.fdopen(fd, "wb") as target:
                def receive(block):
                    nonlocal count, declared
                    context.remaining_seconds()
                    if declared is None:
                        prefix, separator, rest = block.partition(b"\n")
                        header.extend(prefix)
                        if len(header) > 32 or not header.isdigit():
                            raise FileChanged("native file transfer size identity is invalid")
                        if not separator:
                            return
                        declared = int(header)
                        block = rest
                    count += len(block)
                    if count > declared:
                        raise FileChanged("native file grew beyond its original declared size")
                    target.write(block)
                if self.runner_mode == "shell":
                    command = self._exec(["/bin/bash", "-c", _READ_FILE_SHELL,
                        "scid-read-original", self._root(call_id), relative_path])
                else:
                    command = self._exec(self.python_argv + ["-c", _READ_FILE,
                        self._root(call_id), relative_path])
                result = self._run(command, sink=receive,
                                   timeout=timeout_seconds, max_bytes=MAX_RESPONSE_BYTES, context=context)
                if result.returncode == 3:
                    raise FileMissing("named native output is absent", relative_path=relative_path)
                if result.returncode == 4:
                    raise FileChanged("native original changed during transfer", relative_path=relative_path)
                if result.returncode == 5:
                    raise FileInvalid("named native output is not a permitted regular file", relative_path=relative_path)
                if result.returncode:
                    raise BackendError("native file read failed", returncode=result.returncode,
                                       stderr=result.stderr, relative_path=relative_path)
                if declared is None or count != declared:
                    raise FileChanged("native file transfer is incomplete")
                target.flush()
                os.fchmod(target.fileno(), 0o400)
                os.fsync(target.fileno())
            context.remaining_seconds()
            # link supplies atomic publication without overwriting an already
            # complete destination, including a concurrent final publisher.
            os.link(temporary, destination, follow_symlinks=False)
            directory = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
