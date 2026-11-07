"""Local validation for a privately bound, original task container."""
from __future__ import annotations

import math
from pathlib import PurePosixPath
import re

from scidiscovery.research_loop.experiments import HOST_CONFIGURATION_FIELDS


_FIELDS = frozenset({"container_id", "docker_argv", "python_argv", "container_root",
    "max_seconds", "deadline_epoch", "environment", "user", "max_log_bytes",
    "max_file_bytes", "max_total_bytes", "max_output_files", "max_result_bytes",
    "collection_seconds", "runner_mode"})
_DEFAULTS = dict(docker_argv=["docker"], python_argv=["/usr/local/bin/python", "-B", "-u"],
    max_seconds=60, max_log_bytes=None, max_file_bytes=None,
    max_total_bytes=None, max_output_files=128, max_result_bytes=None,
    collection_seconds=120)


def validate(configuration, *, host=False):
    """Validate JSON settings only: no executable probes or transport side effects."""
    allowed = _FIELDS | (HOST_CONFIGURATION_FIELDS if host else frozenset())
    if not isinstance(configuration, dict) or set(configuration) - allowed:
        raise ValueError("Harbor execution configuration contains unsupported fields")
    value = dict(_DEFAULTS, **configuration)
    if value.get("runner_mode", "python") not in {"python", "shell"}:
        raise ValueError("runner_mode must be python or shell")
    if not isinstance(value.get("container_id"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["container_id"]):
        raise ValueError("Bind the exact canonical full Docker container ID on the host")
    root = value.get("container_root")
    path = PurePosixPath(root) if isinstance(root, str) else None
    if (path is None or "\0" in root or "\\" in root or not path.is_absolute()
            or path.as_posix() != root or ".." in path.parts or len(path.parts) < 3
            or path.parts[1] in {"app", "proc", "sys", "dev"}):
        raise ValueError("container_root must be an exact trial scratch directory outside /app")
    for name in ("docker_argv", "python_argv"):
        argv = value[name]
        if (not isinstance(argv, list) or not argv
                or any(not isinstance(item, str) or not item or "\0" in item for item in argv)):
            raise ValueError(name + " requires an explicit nonempty argument list")
        value[name] = list(argv)
    if not PurePosixPath(value["python_argv"][0]).is_absolute():
        raise ValueError("python_argv must name the original container's absolute interpreter")
    if any(item.startswith(("-c", "-m")) for item in value["python_argv"][1:]):
        raise ValueError("python_argv cannot select -c or -m instead of the captured entry")
    for name in ("max_seconds", "collection_seconds", "deadline_epoch"):
        part = value.get(name)
        if name == "deadline_epoch" and part is None:
            continue
        try:
            valid = type(part) in (int, float) and math.isfinite(part) and part > 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(name + " requires a finite positive limit")
    # Legacy byte settings remain part of frozen configuration identities, but
    # no longer limit originals. Only count and time budgets are enforced.
    for name in ("max_log_bytes", "max_file_bytes", "max_total_bytes", "max_result_bytes"):
        if value[name] is not None and (type(value[name]) is not int or value[name] <= 0):
            raise ValueError(name + " requires a positive integer or null")
    if type(value["max_output_files"]) is not int or value["max_output_files"] <= 0:
        raise ValueError("max_output_files requires a positive integer")
    environment = value.get("environment", {})
    if (not isinstance(environment, dict) or any(not isinstance(key, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            or key in {"HOME", "PYTHONHOME", "PYTHONPATH"} or not isinstance(part, str)
            or "\0" in part for key, part in environment.items())):
        raise ValueError("environment requires explicit strings without HOME or Python runtime overrides")
    value["environment"] = dict(environment)
    if value.get("user") is not None and (not isinstance(value["user"], str)
            or not value["user"] or "\0" in value["user"]):
        raise ValueError("user must be an explicit container user string")
    if value.get("user") is not None and value["user"].split(":", 1)[0] not in {"root", "0"}:
        raise ValueError("Harbor Docker execution currently supports only the original root effective user")
    return value


def configure(configuration, source, *, binding):
    if binding != "experiment":
        raise ValueError("Harbor execution supports the experiment binding")
    return validate(configuration)
