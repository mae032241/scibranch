"""Optional, pure presentation providers over an already authorized cohort.

This group is deliberately independent of Operation compilation. Providers get
read-only JSON, never services, models, tools, or a way to acquire more records.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import entry_points
import json
import re
from types import MappingProxyType
from urllib.parse import parse_qsl, urlsplit
from ...plugin_runtime.presentation import (PRESENTATION_COLLECTIONS as _KEYS, plain,
    json_size as _size, safe_value, pointer, source, items, empty, add_gap, add_fields)


ENTRY_POINT_GROUP = "scidiscovery.instance_views"
MAX_PRESENTATION_BYTES = 192 * 1024
from ...plugin_runtime.presentation import (MISSING, NO_UNCERTAINTY, NO_SOURCE,
    value_at, parameter, dependency_cohort, dependency_complete, ParameterPageRows, safe_url, citation)


@lru_cache(maxsize=1)
def _provider_entries(discover):
    """Cache installed UI metadata only; never cache scientific content."""
    return tuple(discover(group=ENTRY_POINT_GROUP))[:8]


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def build_parameter_page(artifact, dependencies=(), *, after=0, limit=8):
    if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 8:
        raise ValueError("invalid parameter page bounds")
    result = empty()
    total, owners = 0, 0
    try:
        cohort = tuple(_freeze(plain(item)) for item in (artifact, *dependencies))
        if not isinstance(artifact.get("payload"), Mapping):
            raise ValueError("parameter original is not an object")
        for entry in _provider_entries(entry_points):
            provider = entry.load()
            if artifact["schema_id"] not in getattr(provider, "parameter_schemas", ()):
                continue
            owners += 1
            page = provider(cohort, parameter_target=artifact["artifact_id"], parameter_after=after, parameter_limit=limit)
            total = page["parameters"].total
            page["parameters"] = list(page["parameters"])
            result = _validate_output(page, cohort)
        if owners != 1:
            result = empty()
            add_gap(result, "parameter_provider_unavailable" if not owners else "parameter_provider_ambiguous", artifact)
            total = None
    except Exception as error:
        result = empty()
        add_gap(result, "parameter_page_unavailable", artifact, error_type=type(error).__name__)
        total = None
    result.update(parameter_page={"after": after, "limit": limit, "total": total,
        "next_after": after + limit if total is not None and after + limit < total else None},
        parameter_scope_ids=[artifact["artifact_id"]], focused_view=True)
    return result


def _valid_source(reference, cohort):
    if not isinstance(reference, Mapping) or set(reference) != {"artifact_id", "json_pointer"}:
        return False
    for artifact in cohort:
        if artifact["artifact_id"] != reference["artifact_id"]:
            continue
        base = artifact.get("source", {}).get("json_pointer", "")
        path = reference["json_pointer"]
        if not isinstance(path, str) or not (path == base or path.startswith(base + "/")):
            continue
        if path == base:
            return True
        try:
            value_at(artifact["payload"], path[len(base):])
            return True
        except (KeyError, IndexError, ValueError):
            continue
    return False


def _validate_output(result, cohort):
    if not isinstance(result, Mapping) or set(result) != set(_KEYS):
        raise ValueError("invalid provider output")
    result = plain(result)
    allowed = {artifact["artifact_id"] for artifact in cohort}
    for section in result["sections"]:
        if set(section) - {"title", "items", "kind"} or not {"title", "items"}.issubset(section) or section.get("kind") not in {None, "execution_scope", "curve_evidence", "attachments"}:
            raise ValueError("invalid provider section")
        for item in section["items"]:
            if set(item) != {"label", "value", "source"} or not _valid_source(item["source"], cohort):
                raise ValueError("provider fact has no exact authorized source")
    for row in result["parameters"]:
        if set(row) - {"name", "description", "selected_value", "reported_values", "unit", "conditions", "case_scope", "epistemic_status", "acquisition", "uncertainty", "rationale", "sources", "source", "field_sources", "record_type", "source_status"}:
            raise ValueError("unsupported provider parameter fields")
        if not _valid_source(row.get("source"), cohort):
            raise ValueError("provider parameter has no authorized source")
        for ref in row.get("field_sources", {}).values():
            if not _valid_source(ref, cohort):
                raise ValueError("provider parameter field has no authorized source")
        for entry in row.get("sources", []):
            if set(entry) - {"title", "locator", "url", "original_url", "accessed_at", "acquisition", "doi", "authors", "publication_year", "source", "locator_source"}:
                raise ValueError("unsupported provider citation fields")
            if not _valid_source(entry.get("source"), cohort):
                raise ValueError("provider citation has no authorized source")
            if "locator_source" in entry and not _valid_source(entry["locator_source"], cohort):
                raise ValueError("provider locator has no authorized source")
            entry["url"] = safe_url(entry.get("url"))
            entry["original_url"] = safe_url(entry.get("original_url"))
        for reported in items(row.get("reported_values")):
            if isinstance(reported, Mapping) and "source" in reported and not _valid_source(reported["source"], cohort):
                raise ValueError("reported value has no authorized source")
    for figure in result["figures"]:
        if set(figure) != {"artifact_id", "label", "source"} or figure["artifact_id"] not in allowed or not _valid_source(figure["source"], cohort):
            raise ValueError("provider figure has no authorized source")
    for item in result["gaps"]:
        if "source" in item and not _valid_source(item["source"], cohort):
            raise ValueError("provider gap has no authorized source")
    return result


def presentation_pointers(schema_id):
    """Static display selections; these never grant access to another artifact."""
    control = {
        "scidiscovery.tool-evidence-manifest.v1": ("/records", "/bindings"),
        "scidiscovery.approval-request": ("/kind", "/question", "/options", "/subject_refs", "/expires_at"),
        "scidiscovery.human-decision": ("/selected_option", "/rationale", "/decided_at"),
    }
    result = list(control.get(schema_id, ()))
    try:
        providers = _provider_entries(entry_points)
    except Exception:
        return tuple(result)
    for entry in providers:
        try:
            declared = getattr(entry.load(), "display_pointers", {}).get(schema_id, ())
            for path in declared[:64]:
                if isinstance(path, str) and len(path) <= 4096 and path.startswith("/") and path not in result:
                    result.append(path)
        except Exception:
            continue
    return tuple(result[:64])


def build_presentation(artifacts, *, focus_artifact_ids=None, task_artifact_ids=()):
    """Load optional providers and keep a bounded fallback if any cannot render."""
    result = empty()
    focus = frozenset(focus_artifact_ids or ())
    tasks = frozenset(task_artifact_ids)
    allowed_keys = {"artifact_id", "schema_id", "payload", "parents", "provenance", "source", "media_type", "payload_state", "gaps", "family", "ref", "size_bytes", "parent_count"}
    incoming = tuple(artifacts)
    incomplete = len(incoming) > 100
    cohort, size = [], 0
    for artifact in incoming[:100]:
        copied = plain({key: value for key, value in artifact.items() if key in allowed_keys})
        if not isinstance(copied.get("artifact_id"), str) or not isinstance(copied.get("schema_id"), str):
            add_gap(result, "artifact_view_metadata_missing")
            continue
        size += _size(copied)
        if size > 8 * 1024 * 1024:
            add_gap(result, "presentation_input_limit")
            incomplete = True
            break
        cohort.append(_freeze(copied))
    if incomplete:
        cohort = [_freeze({**plain(artifact), "family": {**plain(artifact.get("family", {})), "lookup_incomplete": True}})
                  for artifact in cohort]
        add_gap(result, "presentation_cohort_incomplete")
    cohort = tuple(cohort)
    from .attachment_presentation import append_attachments
    append_attachments(result, cohort)
    try:
        providers = _provider_entries(entry_points)
    except Exception as error:
        providers = ()
        add_gap(result, "provider_discovery_failed", error_type=type(error).__name__)
    for entry in providers:
        try:
            output = _validate_output(entry.load()(cohort), cohort)
            for key in _KEYS:
                result[key].extend(output[key])
        except Exception as error:
            add_gap(result, "provider_unavailable", provider=entry.name, error_type=type(error).__name__)
    covered = {item["source"]["artifact_id"] for section in result["sections"] for item in section["items"]}
    covered.update(item["source"]["artifact_id"] for item in result["parameters"])
    pictured = {item["artifact_id"] for item in result["figures"]}
    for artifact in cohort:
        # A raw image is self-describing only when it is the selected subject.
        # Ancestor images require a provider's exact manifest/report mapping.
        if (artifact["artifact_id"] in focus and artifact["artifact_id"] not in pictured
                and artifact.get("media_type", "").split(";", 1)[0] in {"image/png", "image/jpeg"}):
            result["figures"].append({"artifact_id": artifact["artifact_id"], "label": "已封存图件", "source": source(artifact)})
        if artifact["artifact_id"] in covered:
            continue
        payload = artifact.get("payload")
        if isinstance(payload, Mapping):
            add_fields(result, artifact, "原始记录", [(key, key) for key in tuple(payload)[:8]])
        elif "payload" in artifact:
            result["sections"].append({"title": "原始记录", "items": [{"label": "原文", "value": safe_value(payload), "source": source(artifact)}]})
        add_gap(result, "schema_presentation_unavailable", artifact, schema_id=artifact["schema_id"])
    if focus_artifact_ids is not None:
        # Provider discovery order must never outrank the selected node's own
        # output. Keep provenance on every item; background stays readable.
        result["sections"].sort(key=lambda section: not any(
            item.get("source", {}).get("artifact_id") in focus for item in section.get("items", ())))
        result["focus_artifact_ids"] = [artifact["artifact_id"] for artifact in cohort if artifact["artifact_id"] in focus]
        result["focused_view"] = True
    parameter_schemas = set()
    for entry in providers:
        try:
            parameter_schemas.update(getattr(entry.load(), "parameter_schemas", ()))
        except Exception:
            continue
    result["parameter_documents"] = [source(artifact) for artifact in cohort
        if artifact["schema_id"] in parameter_schemas]
    result["task_sources"] = [{"artifact_id": artifact["artifact_id"], "json_pointer": ""}
        for artifact in cohort if artifact["artifact_id"] in tasks]
    result["parameter_scope_ids"] = [artifact["artifact_id"] for artifact in cohort
        if artifact["artifact_id"] in focus | tasks]
    result["parameters"].sort(key=lambda item: item.get("source", {}).get("artifact_id") not in focus | tasks)
    # Providers may reach the same immutable picture through several revisions.
    # Count/display it once, retaining each exact publication link. Prefer the
    # selected report's manifest over historical publication captions/sources.
    primary = set(focus)
    for artifact in cohort:
        if artifact["artifact_id"] in focus:
            owner = artifact.get("family", {}).get("selected_run_id")
            primary.update(a["artifact_id"] for a in cohort if owner
                and a.get("family", {}).get("tool_producer_run") == owner
                and a.get("family", {}).get("operation_output_port") == "recovery_manifest_output")
    result["figures"].sort(key=lambda f: f["source"]["artifact_id"] not in primary)
    figures = {}
    for figure in result["figures"]:
        existing = figures.setdefault(figure["artifact_id"], figure)
        if existing is not figure and figure["source"] != existing["source"]:
            sources = existing.setdefault("additional_sources", [])
            if figure["source"] not in sources:
                sources.append(figure["source"])
    result["figures"] = list(figures.values())
    for key, maximum in (("sections", 64), ("parameters", 128), ("figures", 32), ("gaps", 64)):
        if len(result[key]) > maximum:
            result[key] = result[key][:maximum]
            add_gap(result, "presentation_item_limit", collection=key)
    reduced = False
    while _size(result) > MAX_PRESENTATION_BYTES - 512:
        removed = False
        if focus:
            for key in ("sections", "parameters", "figures"):
                for index in range(len(result[key]) - 1, -1, -1):
                    item = result[key][index]
                    sources = item.get("items", ()) if key == "sections" else (item,)
                    if not any(value.get("source", {}).get("artifact_id") in focus for value in sources):
                        result[key].pop(index)
                        removed = reduced = True
                        break
                if removed:
                    break
        if removed:
            continue
        # When all remaining data belongs to the selected node, preserve its
        # formal conclusion ahead of optional detailed parameter/image rows.
        for key in (("parameters", "figures", "gaps", "sections") if focus
                    else ("sections", "parameters", "figures", "gaps")):
            if result[key]:
                result[key].pop()
                reduced = True
                break
    if reduced:
        add_gap(result, "presentation_byte_limit")
    return result
