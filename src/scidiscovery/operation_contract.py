"""Pure declared diagnostics for execution payload validation."""

from __future__ import annotations

import json
import re
from pydantic import ValidationError
from .artifact_agent.schema.common import ContractDiagnostic
from collections.abc import Iterable
from typing import Any, Mapping




class SemanticRuleViolation(ValueError):
    """An intentional Worker-correctable rejection by a bound semantic checker."""

    def __init__(self, message: str, *, details: tuple[dict[str, Any], ...] = ()):
        super().__init__(message)
        self.details = details


class DiagnosticError(RuntimeError):
    """Transport a typed, already-safe diagnostic without stringifying it."""
    def __init__(self, message: str, *, details: tuple[dict[str, Any], ...] = ()):
        super().__init__(message)
        self.details = details


class DeclaredDiagnostic(dict):
    """In-process marker for explicitly declared safe details; never a wire field."""


class ToolInputError(DiagnosticError, ValueError):
    """A tool owner declares a safe, actionable caller correction."""

    def __init__(self, code: str, message: str, *, path: str = "$"):
        super().__init__(message, details=(contract_diagnostic(code,
            phase="tool_arguments", affected_action="tool_call", path=path,
            repairable=True, message=message),))


def contract_diagnostic(code: str, *, phase: str, affected_action: str,
                        path: str = "$", repairable: bool = False,
                        message: str = "The declared requirement was not satisfied.",
                        rule_id: str | None = None, error_type: str | None = None) -> dict[str, Any]:
    return DeclaredDiagnostic(ContractDiagnostic(code=code, phase=phase, path=path, message=message,
        repairable=repairable, affected_action=affected_action, rule_id=rule_id,
        type=error_type).model_dump(mode="json", exclude_none=True))


def declared_violation(message: str, *, path: str = "$") -> SemanticRuleViolation:
    """Declare a static relationship error at its owner, without reflecting input."""
    return SemanticRuleViolation(message, details=(contract_diagnostic("output_invalid",
        phase="output_payload", affected_action="submit", repairable=True,
        path=path, message=message, error_type="value_error"),))


def schema_field_names(schema: Any) -> frozenset[str]:
    names = set()
    def visit(value):
        if isinstance(value, Mapping):
            names.update(value.get("properties", ()))
            for child in value.values(): visit(child)
        elif isinstance(value, (tuple, list)):
            for child in value: visit(child)
    visit(schema)
    return frozenset(names)


def diagnostic_path(parts: Iterable[Any], names: Iterable[str], *, root: str = "$") -> str:
    allowed = set(names)
    path = root
    for part in tuple(parts)[:32]:
        token = (f"[{part}]" if isinstance(part, int) and 0 <= part <= 1000000
                 else f".{part}" if isinstance(part, str) and part in allowed
                 and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,95}", part) else "[key]")
        if len(path) + len(token) > 512: break
        path += token
    return path


def bounded_diagnostics(items: Iterable[dict[str, Any]], *, phase: str, action: str,
                        total: int | None = None, deferred: str | None = None) -> tuple[dict[str, Any], ...]:
    """Reserve a summary inside the public 16-detail budget; never miscount a prefix."""
    details = tuple(items)
    count = len(details) if total is None else total
    if count <= 16 and deferred is None:
        return details
    shown = min(15, len(details))
    summary = f"Reported {shown} of {count} diagnosed defects; omitted {count - shown}."
    if deferred:
        summary += " " + deferred
    return (*details[:shown], contract_diagnostic(
        "invalid_arguments" if phase == "tool_arguments" else "output_invalid",
        phase=phase, affected_action=action, repairable=True, message=summary,
        error_type="dependent_checks_deferred" if deferred else "diagnostics_omitted"))


def validation_diagnostics(error: ValidationError, *, schema: Any,
                           phase: str = "tool_arguments", action: str = "tool_call") -> tuple[dict[str, Any], ...]:
    messages = {
        "missing": "Required field is missing.",
        "dict_type": "Expected a JSON object.", "model_type": "Expected a JSON object.",
        "string_type": "Expected a string.", "int_type": "Expected an integer.",
        "float_type": "Expected a number.", "bool_type": "Expected a boolean.",
        "tuple_type": "Expected a JSON array.", "list_type": "Expected a JSON array.",
        "extra_forbidden": "An undeclared field was supplied.",
        "literal_error": "Use a value from the declared enumeration.",
        "union_tag_invalid": "Use a source kind from the declared enumeration.",
        "union_tag_not_found": "The declared discriminator field is missing.",
    }
    names = schema_field_names(schema)
    result = []
    errors = error.errors(include_input=True, include_context=True, include_url=False)
    for item in errors:
        kind = item["type"]
        # Invalid children can make Pydantic's validated array look empty even
        # when the supplied array meets min_length. Report the child failures.
        if (kind == "too_short" and isinstance(item.get("input"), (list, tuple))
                and len(item["input"]) >= item.get("ctx", {}).get("min_length", 1)
                and any(len(child["loc"]) > len(item["loc"])
                        and child["loc"][:len(item["loc"])] == item["loc"] for child in errors)):
            continue
        cause = item.get("ctx", {}).get("error")
        explicit = cause.details if isinstance(cause, SemanticRuleViolation) else ()
        if explicit and all(isinstance(detail, DeclaredDiagnostic) for detail in explicit):
            for detail in explicit:
                result.append(contract_diagnostic("invalid_arguments" if phase == "tool_arguments" else detail["code"],
                    phase=phase, affected_action=action, repairable=True,
                    path=diagnostic_path(item["loc"], names) + detail["path"].removeprefix("$"),
                    message=detail["message"], error_type=kind))
            continue
        message = messages.get(kind, "Value violates the declared type, bounds, or field relationship.")
        if kind == "value_error" and isinstance(cause, ValueError):
            # Pydantic has already classified this as a declared model rejection.
            # Keep its bounded reason, without serializing the supplied value,
            # exception context or traceback. Ordinary runtime exceptions are not
            # handled here and remain engineering failures.
            message = str(cause)[:512] or message
            supplied = item.get("input")
            if isinstance(supplied, str) and 0 < len(supplied) <= 512:
                # Retain the rule's explanation while removing an echoed value.
                # Merely omitting Pydantic's input field does not remove echoes
                # embedded by a custom validator in its exception message.
                message = re.sub(r"(?<!\w)" + re.escape(supplied) + r"(?!\w)",
                    "[redacted input]", message)
        # These two values are generated from the declared literal/tag schema;
        # the supplied input and arbitrary custom error context are never copied.
        if kind in {"literal_error", "union_tag_invalid"}:
            expected = item.get("ctx", {}).get("expected" if kind == "literal_error" else "expected_tags")
            if isinstance(expected, str):
                message = ("Supported values: " + expected)[:512]
        bounds = {
            "less_than_equal": ("le", "Value must be at most {}."),
            "less_than": ("lt", "Value must be less than {}."),
            "greater_than_equal": ("ge", "Value must be at least {}."),
            "greater_than": ("gt", "Value must be greater than {}."),
            "too_long": ("max_length", "Maximum length is {}."),
            "too_short": ("min_length", "Minimum length is {}."),
            "string_too_long": ("max_length", "Maximum length is {}."),
            "string_too_short": ("min_length", "Minimum length is {}."),
        }
        if kind in bounds:
            key, template = bounds[kind]
            value = item.get("ctx", {}).get(key)
            if type(value) in (int, float):
                message = template.format(value)[:512]
        result.append(contract_diagnostic(
            "invalid_arguments" if phase == "tool_arguments" else "output_invalid",
            phase=phase, affected_action=action, repairable=True,
            path=diagnostic_path(item["loc"], names), error_type=kind, message=message))
    return bounded_diagnostics(result, phase=phase, action=action,
        deferred=("Dependent checks requiring invalid nested values were not evaluated."
                  if phase == "output_payload" else None))



def sanitize_diagnostic_details(items: Iterable[Any], *, schema: Any,
                                rules: Iterable[str] = (), phase: str = "output_payload",
                                action: str = "submit", rule_phases: Mapping[str, str] | None = None) -> tuple[dict[str, Any], ...]:
    names = schema_field_names(schema) | {"payload", "handoff", "schema_version"}
    allowed_rules = set(rules)
    result = []
    for item in items:
        if not isinstance(item, Mapping): continue
        rule = item.get("rule_id")
        if rule is not None and rule not in allowed_rules: continue
        parts = re.findall(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+|key)\]", str(item.get("path", "$"))[:512])
        # Explicit details have already bounded their schema fields at the owner;
        # they may include a declared workspace prefix outside the payload schema.
        path_names = names | {field for field, _ in parts} if isinstance(item, DeclaredDiagnostic) else names
        path = diagnostic_path((int(index) if index.isdigit() else field or "[key]" for field,index in parts), path_names)
        code = item.get("code", "output_invalid" if action == "submit" else "runtime_failure")
        if not isinstance(code,str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}",code): code="runtime_failure"
        error_type = item.get("type")
        if not isinstance(error_type,str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}",error_type): error_type=None
        resolved_phase = (item.get("phase", phase) if isinstance(item, DeclaredDiagnostic)
                          else (rule_phases or {}).get(rule, phase))
        # Only the explicit in-process constructor marks a message as safe. An
        # arbitrary error dictionary passing the wire schema does not do so.
        description = rules.get(rule) if isinstance(rules, Mapping) else None
        if isinstance(item, DeclaredDiagnostic):
            message = item["message"]
            if description and message == "Value violates the declared type, bounds, or field relationship.":
                message = description
        else:
            message = description or ("Required field is missing." if error_type == "missing" else
                "Value violates the declared type, bounds, or field relationship." if error_type else
                "The declared requirement was not satisfied.")
        result.append(contract_diagnostic(code, phase=resolved_phase, affected_action=action,
            path=path, repairable=(item.get("repairable", resolved_phase in {"input_admission","tool_arguments","output_payload","output_context"}) if isinstance(item, DeclaredDiagnostic) else
                                  resolved_phase in {"input_admission","tool_arguments","output_payload","output_context"}),
            message=message[:512], rule_id=rule, error_type=error_type))
    return bounded_diagnostics(result, phase=phase, action=action)
