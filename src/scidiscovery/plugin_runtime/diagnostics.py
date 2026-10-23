"""Bounded redacted error facts and declared failure categories; no diagnostic store."""
from __future__ import annotations
import re
import subprocess
from pydantic import ValidationError

__all__ = ["redact", "exception_facts", "recovery_facts"]

# Stages are set at the local boundary that knows whether an action was sent.
# Exception type or a remote error message alone cannot establish that fact.
_RECOVERY = {
    "configuration": (False, "administrator", "Repair service configuration before retrying the same action."),
    "connection": (True, "operator", "No request was sent; restore connectivity before retrying within the existing budget."),
    "pre_execution_rejected": (False, "caller", "Resolve the declared rejection before retrying the same action."),
    "submission_unknown": (False, "operator", "Read status and recover the exact prepared submission through execution_start; do not rename or resubmit."),
}


def recovery_facts(stage: str) -> dict:
    retryable, owner, condition = _RECOVERY[stage]
    return {"stage": stage, "retryable": retryable, "responsibility": owner,
            "recovery_condition": condition}

def redact(value: str) -> str:
    _SECRET = re.compile(rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}\b|(?:api[_-]?key|password|secret)[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_./+=-]{12,})", re.IGNORECASE)
    value = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", "[redacted private key]", value)
    value = re.sub(r"Command ['\"].*(?:timed out|returned non-zero exit status|died with)", "Command [redacted argv] failed:", value)
    value = _SECRET.sub(b"[redacted]", value.encode("utf-8", errors="replace")).decode("utf-8")
    value = re.sub(r"(?i)\b(password|secret|token|credential|api[_-]?key|authorization)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", value)
    value = re.sub(r'''(["'])(?:/|[A-Za-z]:\\)[^\r\n]*?\1''', '"[host-path]"', value)
    value = re.sub(r"(?<![\w])(?:/[\w.~-]+){2,}[^\s\"']*|[A-Za-z]:\\[^\s\"']+", "[host-path]", value)
    return value


def exception_facts(error: Exception, *, layer: str, action: str) -> dict:
    chain, seen, upstreams = [], set(), []
    current = error
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        upstream = getattr(current, "engineering", None)
        if isinstance(upstream, dict):
            upstreams.append(upstream)
        # Subprocess exception strings contain argv; expose the action, not argv.
        message = (f"Subprocess exceeded {current.timeout} seconds."
                   if isinstance(current, subprocess.TimeoutExpired) else
                   f"Subprocess exited with code {current.returncode}."
                   if isinstance(current, subprocess.CalledProcessError) else
                   "Declared field validation failed; see the contract diagnostic locations."
                   if isinstance(current, ValidationError) else str(current))
        item = {"type": type(current).__name__, "message": redact(message)[:2048]}
        for field in ("timeout", "returncode", "errno", "elapsed_seconds"):
            value = getattr(current, field, None)
            if isinstance(value, (int, float)):
                item[field] = value
        if getattr(current, "timeout_kind", None):
            item["timeout_kind"] = str(current.timeout_kind)[:80]
        chain.append(item)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    for upstream in upstreams:
        if not isinstance(upstream.get("causes"), list):
            continue
        for cause in upstream["causes"][:8]:
            if not isinstance(cause, dict):
                continue
            item = {key: redact(str(cause.get(key, "")))[:2048] for key in ("type", "message")}
            for key in ("timeout", "returncode", "elapsed_seconds"):
                if isinstance(cause.get(key), (int, float)):
                    item[key] = cause[key]
            if cause.get("timeout_kind"):
                item["timeout_kind"] = redact(str(cause["timeout_kind"]))[:80]
            if item not in chain:
                chain.append(item)
    stage = getattr(error, "failure_stage", None)
    if stage not in _RECOVERY:
        stage = None
    return {"layer": layer, "action": action,
            **({"recovery": recovery_facts(stage)} if stage else {}),
            **({"origin_layer": redact(str(upstreams[-1].get("origin_layer", upstreams[-1].get("layer", "unknown"))))[:80]} if upstreams else {}),
            "causes": chain[:12],
            "category": "timeout" if any("timeout" in x or x["type"] == "TimeoutError" for x in chain) else "runtime_failure"}
