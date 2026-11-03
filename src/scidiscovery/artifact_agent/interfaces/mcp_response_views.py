"""Read projections for MCP clients; services and sealed records stay complete."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json

from .mcp_root_shared import RootToolError


def run_intent_projection(value, *, intent):
    """Pure compact projection; the route avoids assembling excluded details."""
    result = pick(
        value,
        "name",
        "operation_id",
        "operation_contract_status",
        "state",
        "agent_type",
        "execution_profile",
        "deadline_at",
        "last_activity_at",
        "output_artifact_name",
        "recovery_available",
        "sealed_output_status",
        "diagnostics_available",
        "attention_required",
        "content_unavailable",
        "sealed_stages",
        "dispatch",
        "preparation",
        "reason",
        "scientific_result_semantics",
    )
    if isinstance(result.get("execution_profile"), dict):
        result["execution_profile"] = pick(result["execution_profile"], "profile")
    if intent == "full" and value.get("state") == "completed":
        result.update(pick(value, "output_delivery", "sealed_output", "scheduler_signal", "scheduler_signal_status"))
    elif intent == "navigation" and value.get("state") == "completed":
        result.update(pick(value, "output_delivery", "output_index", "output_metadata"))
    elif intent == "decision" and value.get("state") == "completed":
        result.update(
            pick(
                value,
                "output_delivery",
                "output_metadata",
                "selected_output",
                "default_output_unavailable",
                "output_index",
                "scheduler_signal",
                "scheduler_signal_status",
                "scheduler_signal_omissions",
            )
        )
    return result


def run_is_terminal(state):
    # Unknown lifecycle values fail closed; they cannot opt into long details.
    return state in {"completed", "failed", "timed_out", "cancelled", "canceled"}


def pick(value, *keys):
    return {key: value[key] for key in keys if key in value}


def page(items, *, key, limit=20, before=None):
    """Keep the owner's order and use its exact semantic key as the cursor."""
    found = before is None
    selected = []
    for item in items:
        if not found:
            found = item[key] == before
            continue
        if len(selected) == limit:
            return selected, selected[-1][key]
        selected.append(item)
    if not found:
        raise RootToolError("page cursor is not present in this query; restart the listing")
    return selected, None


_NAV_DIMENSIONS = ("consequence", "executor_kind", "input_schema")
_NAV_BUDGET = {"index": 8192, "facets": 4096, "matches": 8192}


def _nav_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _nav_values(item, dimension):
    if dimension == "input_schema":
        return sorted({port["schema"] for port in item.get("inputs", ())})
    value = item.get(dimension)
    return [] if value is None else [value]


def _nav_cursor(snapshot_digest, query_digest, offset, last_key):
    data = json.dumps([1, snapshot_digest, query_digest, offset, _nav_digest(last_key)],
        separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _nav_start(items, before, snapshot_digest, query_digest, key):
    if before is None:
        return 0
    try:
        data = base64.urlsafe_b64decode(before + "=" * (-len(before) % 4))
        version, cursor_snapshot, cursor_query, offset, last_key_digest = json.loads(data)
    except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as error:
        raise RootToolError("invalid navigation cursor; restart_query") from error
    if version != 1 or type(offset) is not int or not isinstance(last_key_digest, str):
        raise RootToolError("invalid navigation cursor; restart_query")
    if cursor_snapshot != snapshot_digest:
        raise RootToolError("catalog_changed; restart_query")
    if cursor_query != query_digest:
        raise RootToolError("cursor_query_mismatch; restart_query")
    if not 1 <= offset < len(items) or _nav_digest(items[offset - 1][key]) != last_key_digest:
        raise RootToolError("cursor item is no longer present; restart_query")
    return offset


def _nav_page(items, *, key, limit, before, snapshot_digest, query_digest, budget, response):
    start = _nav_start(items, before, snapshot_digest, query_digest, key)
    remaining = items[start:]
    field = "facets" if key == "value" else "operations"
    for count in range(min(limit, len(remaining)), -1, -1):
        selected = remaining[:count]
        next_before = (_nav_cursor(snapshot_digest, query_digest, start + count, selected[-1][key])
            if start + count < len(items) and selected else None)
        candidate = {**response, field: selected, "returned": count,
            "next_before": next_before, "complete": next_before is None}
        if field == "facets":
            candidate["omitted_count"] = len(items) - start - count
        if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= budget:
            if count == 0 and remaining:
                raise RootToolError("navigation_entry_too_large; cannot return a progressing page")
            return candidate
    raise RootToolError("navigation_response_too_large; cannot return required metadata")


def operation_navigation(value, arguments):
    view = arguments["view"]
    if value["scope"] != "public":
        raise RootToolError("navigation views require scope=public")
    if arguments.get("operation_id") is not None:
        raise RootToolError("navigation views do not accept operation_id; use view=detail")
    dimension = arguments.get("dimension")
    where = arguments.get("where")
    if view != "facets" and dimension is not None:
        raise RootToolError("dimension is available only for view=facets")
    if view != "matches" and where is not None:
        raise RootToolError("where is available only for view=matches")
    if view == "facets" and where is not None:
        raise RootToolError("facets do not accept filters")
    items = sorted(value["operations"], key=lambda item: item["operation_id"])
    snapshot_digest = _nav_digest({"version": 1, "catalog_digest": value["catalog_digest"],
        "state": value["navigation_state"],
        "visible": [(item["operation_id"], item.get("operation_digest"), item.get("purpose"),
            item.get("consequence"), item.get("executor_kind"),
            _nav_values(item, "input_schema"), item.get("runtime_binding")) for item in items]})
    filters = {}
    if view == "matches":
        if not isinstance(where, dict) or not where:
            raise RootToolError("matches requires nonempty where; use view=index for the full public set")
        if len(where) > len(_NAV_DIMENSIONS):
            raise RootToolError("too many P1 filter fields; use consequence, executor_kind or input_schema")
        unknown_fields = sorted(set(where) - set(_NAV_DIMENSIONS))
        if unknown_fields:
            raise RootToolError(f"unsupported P1 filter field; available: {list(_NAV_DIMENSIONS)}")
        for field in sorted(where):
            raw = where[field]
            requested = [raw] if isinstance(raw, str) else raw
            if (not isinstance(requested, list) or not 1 <= len(requested) <= 8
                or any(not isinstance(entry, str) or not entry or len(entry) > 4096 for entry in requested)):
                raise RootToolError(f"invalid {field} filter; use one to eight nonempty strings of at most 4096 characters")
            filters[field] = sorted(set(requested))
    query_digest = _nav_digest({"version": 1, "scope": "public", "view": view,
        "dimension": dimension, "where": filters, "sort": "ascending", "projection": "root"})
    common = {"scope": "public", "view": view,
        "catalog_digest": value["catalog_digest"],
        "navigation_snapshot_digest": snapshot_digest, "query_digest": query_digest,
        "visible_total": len(items), "semantic_labels_available": False}
    if view == "facets":
        if dimension is None:
            if arguments.get("before") is not None:
                raise RootToolError("facets without dimension do not accept before")
            result = {**common, "dimensions": [{"name": name,
                "value_count": len({entry for item in items for entry in _nav_values(item, name)}),
                "unclassified_count": sum(not _nav_values(item, name) for item in items)}
                for name in _NAV_DIMENSIONS], "returned": len(_NAV_DIMENSIONS),
                "next_before": None, "complete": True}
            if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > _NAV_BUDGET[view]:
                raise RootToolError("navigation_response_too_large; cannot return dimension index")
            return result
        counts = {entry: sum(entry in _nav_values(item, dimension) for item in items)
            for entry in sorted({entry for item in items for entry in _nav_values(item, dimension)})}
        facets = [{"value": entry, "count": count} for entry, count in counts.items()]
        return _nav_page(facets, key="value", limit=min(arguments["limit"], 12),
            before=arguments.get("before"), snapshot_digest=snapshot_digest,
            query_digest=query_digest, budget=_NAV_BUDGET[view], response={**common,
                "dimension": dimension, "total": len(facets),
                "unclassified_count": sum(not _nav_values(item, dimension) for item in items)})
    if view == "matches":
        matched = [item for item in items if all(
            set(_nav_values(item, field)) & set(requested)
            for field, requested in filters.items())]
        projected = [_nav_entry(item, matches=True) for item in matched]
        omitted = _nav_projected_omissions(projected)
        return _nav_page(projected, key="operation_id", limit=min(arguments["limit"], 20),
            before=arguments.get("before"), snapshot_digest=snapshot_digest,
            query_digest=query_digest, budget=_NAV_BUDGET[view], response={**common, **omitted,
                "matched_total": len(matched), "filtered_out_count": len(items) - len(matched),
                "unclassified_count": None, "coverage": "filtered_subset", "filters_applied": filters,
                "fallback": {"kind": "operations", "view": "index"},
                "describe": {"tool": "scid_describe", "name": "<selected operation_id>", "view": "invoke"}})
    projected = [_nav_entry(item) for item in items]
    return _nav_page(projected, key="operation_id", limit=arguments["limit"],
        before=arguments.get("before"), snapshot_digest=snapshot_digest,
        query_digest=query_digest, budget=_NAV_BUDGET[view], response={**common,
            **_nav_omissions(items),
            "describe": {"tool": "scid_describe", "name": "<selected operation_id>", "view": "invoke"}})


def _nav_omissions(items):
    count = sum(len(item.get("purpose") or "") > 120 for item in items)
    return {"omitted_fields": ["purpose_tail"], "omitted_count": count} if count else {}


def _nav_projected_omissions(items):
    omitted = [item["omitted_fields"] for item in items if "omitted_fields" in item]
    return {"omitted_fields": sorted({field for fields in omitted for field in fields}),
        "omitted_count": len(omitted)} if omitted else {}


def _nav_entry(item, *, matches=False):
    purpose = item.get("purpose") or ""
    entry = {"operation_id": item["operation_id"], "purpose": purpose[:120]}
    if matches:
        omitted = ["purpose_tail"] if len(purpose) > 120 else []
        entry.update(consequence=item.get("consequence"), executor_kind=item.get("executor_kind"),
            input_schemas=_nav_values(item, "input_schema"),
            runtime_status=(item.get("runtime_binding") or {}).get("status", "available"))
        size = lambda: len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size() > 320:
            entry.pop("input_schemas")
            omitted.append("input_schemas")
        if omitted:
            entry["omitted_fields"] = omitted
        while size() > 320 and entry["purpose"]:
            entry["purpose"] = entry["purpose"][:-1]
            if "purpose_tail" not in omitted:
                omitted.append("purpose_tail")
                entry["omitted_fields"] = omitted
        if size() > 320:
            raise RootToolError(f"navigation_entry_too_large: {item['operation_id']}")
    return entry


def operation_detail(value):
    """One compiled declaration, without duplicate port lists."""
    result = dict(value)
    validation = result.get("input_validation")
    if isinstance(validation, dict):
        # Cardinality and optionality are already explicit on every input port.
        result["input_validation"] = {key: item for key, item in validation.items()
            if key not in {"required_inputs", "optional_inputs"}}
    return result


def operation_revision_policy(spec):
    """Expose revision call rules from the same immutable OperationSpec."""
    review = spec.review
    revision_base_ports = [port.name for port in spec.inputs if port.usage == "revision_base"]
    change_request_ports = [port.name for port in spec.inputs if port.usage == "change_request"]
    return {
        "max_revisions": 0 if review is None else review.max_revisions,
        "requires_progress_between_change_requests": bool(
            review is not None and review.max_revisions > 0
        ),
        "revision_base_ports": revision_base_ports,
        "change_request_ports": change_request_ports,
        "progress_fingerprint_ports": change_request_ports if (
            review is not None and review.progress_fingerprint is not None
        ) else [],
    }


def operation_invoke_contract(value, *, revision_policy):
    """Project one callable contract from the same compiled catalog item."""
    # Strip only reviewed execution-only fields. Unknown future constraints are
    # retained, including unknown fields inside every input and output port.
    execution_only = {"native_shell", "native_view_image", "network_mode",
        "max_network_requests", "max_output_bytes", "max_files",
        "optional_runtime_services", "executor_model_usage"}
    result = {key: item for key, item in value.items() if key not in execution_only}
    result["revision_policy"] = revision_policy
    result["contract_view_version"] = "invoke.scientific.v2"
    result["binding_policy"] = "Choose scientific materials by their instance names. Control resolves declared exact original materials; missing or ambiguous origins require repair of the selected subject. Internal bindings are not caller inputs."
    result["review_policy"] = "Independent review is optional unless this action explicitly requests qualification or authorization. A review applies only to its exact immutable subject."
    result["invocation"] = {"tool": "operation_invoke", "required": ["name", "operation_id", "inputs"],
        "instruction": "Required for Agent tasks: state the scientific question and intended deliverable.",
        "inputs": "One entry per chosen visible port: {port, artifact_names: [exact semantic names]}. Omit optional ports when irrelevant.",
        "idempotency": "Reuse a name only for an identical request. An intentional changed request uses on_conflict=create_revision.",
        "dispatch": "For a queued Agent, use its returned dispatch instructions and frozen execution_profile. Transform and Approval actions complete through their returned result or review_url."}
    result["full"] = {"tool": "scid_describe", "name": value["operation_id"], "view": "full"}
    return result


def execution_summary(value):
    result = pick(value, "name", "state", "solver_state", "result_artifact_name",
                  "status_observed_at", "progress_observed_at", "reason", "observation_error",
                  "authorization", "authorization_required", "recovery", "submission", "approval")
    progress = value.get("progress") or {}
    if "progress" in value:
        result["progress"] = (None if value["progress"] is None else pick(progress,
            "elapsed_seconds", "started_at", "completed_at", "observed_at", "state", "exit_code",
            "reason", "error", "diagnostic_ref", "diagnostics", "observation_error"))
    if isinstance(progress, dict) and isinstance(progress.get("log_tails"), list):
        from ...plugin_runtime.experiment import progress_summary
        result["progress"].update(progress_summary(progress))
    if isinstance(progress, dict) and progress.get("log_tails") and progress.get("log_cache_state") != "released":
        result["logs"] = {"tool": "scid_call", "arguments": {"name": "execution_logs",
            "surface": "execution", "arguments": {"name": value.get("name")}}}
    if "collection" in value:
        result["collection"] = pick(value["collection"], "state", "accepted", "busy",
            "completed_bytes", "total_bytes", "completed_files", "total_files",
            "total_seconds", "recovery_pending", "reason", "error", "diagnostic_ref", "diagnostics",
            "record_error", "progress_error", "stop_record_error", "observation_error", "stop_observation_error", "stop_reason")
    result["detail"] = {"tool": "scid_call", "arguments": {"name": "execution_status",
        "surface": "execution", "arguments": {"name": value.get("name"), "view": "detail"}}}
    return result


def _bounded_list(result, *, field, key, budget):
    """Deliver complete entries or explicit omission markers with a usable cursor."""
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    items, selected = result[field], []
    for index, item in enumerate(items):
        candidate = {**result, field:[*selected, item]}
        if index + 1 < len(items):
            candidate.update(next_before=item[key], page_limited_by="bytes")
        if size(candidate) > budget:
            if selected:
                return {**result, field:selected, "next_before":selected[-1][key], "page_limited_by":"bytes"}
            # Do not make one oversized object a permanent pagination barrier.
            # This is an omission marker, never a substitute scientific summary.
            request = ({"name":"run_status", "arguments":{"name":item[key], "intent":"navigation"}}
                if field == "runs" else {"name":"artifact_catalog", "arguments":{"name":item[key]}})
            item = {key:item[key], "entry_omitted":"page_byte_budget", "read_request":request}
            candidate[field] = [item]
            if size(candidate) > budget:
                # The exact per-object read_request supersedes the page's generic
                # detail template; avoid repeating navigation at the minimum budget.
                result = {name:value for name, value in result.items() if name != "detail"}
                candidate.pop("detail", None)
            if size(candidate) > budget:
                raise RootToolError("list_metadata_too_large: response exceeds configured page budget")
        selected.append(item)
    result = {**result, field:selected}
    if size(result) > budget:
        raise RootToolError("list_metadata_too_large: response exceeds configured page budget")
    return result


def append_attention(response, notices, *, budget=None, field=None, key=None):
    """Fit the complete reply; never acknowledge notices omitted by its byte limit."""
    def size(value):
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    result = dict(response)
    selected = []
    for event in notices['events']:
        candidate = {**result, 'attention_notifications':{
            'events':[*selected, event], 'more_available':False}}
        # Keep at least one list item: an empty page cannot advance next_before.
        # Trim only while trying to deliver the first notice, not an entire burst.
        while (budget is not None and size(candidate)>budget and not selected
                and field and len(result.get(field, ()))>1):
            result[field] = result[field][:-1]
            result.update(next_before=result[field][-1][key], page_limited_by='bytes')
            candidate = {**result, 'attention_notifications':{
                'events':[event], 'more_available':False}}
        if budget is not None and size(candidate)>budget:
            break
        selected.append(event)
    if not selected:
        return response, 0
    result['attention_notifications'] = {'events':selected,
        'more_available':notices['more_available'] or len(selected)<len(notices['events'])}
    return result, len(selected)


def scientific_result_response(value, arguments, *, budget):
    """Budgeted scheduler pages; the workbench's complete read model stays intact."""
    def size(item):
        return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if value.get("state") != "selected":
        return value
    section = arguments.get("section", "summary")
    name, run_name = value["artifact_name"], value.get("run_name")
    response = {"state": "selected", "result_name": name, "section": section,
        "scope": value["scope"], "scheduler_signal_semantics": value["scheduler_signal_semantics"]}
    def request(selected_section, **fields):
        return {"name": "scientific_result_current", "arguments": {"result_name": name,
            "section": selected_section, "view": "detail", "limit": arguments.get("limit", 30),
            "page_limit": arguments.get("page_limit", 4), **pick(arguments, "before"), **fields}}
    def omitted(pointer):
        return {"omitted": "page_byte_budget", "json_pointer": pointer,
            "read_request": {"name": "run_status", "arguments": {"name": run_name,
                "intent": "decision", "output_paths": [pointer]}}}
    if section == "summary":
        response.update(pick(value, "disposition", "best_available", "accepted_candidates", "summary", "remaining_question"))
        response.update(candidate_count=len(value["candidates"]),
            best_available_count=len(value["best_available"]), accepted_count=len(value["accepted_candidates"]),
            candidates_request=request("candidates"))
        freshness = value.get("freshness", {})
        response["freshness"] = pick(freshness, "state", "examined", "scan_complete", "next_before", "coverage")
        response["freshness"]["change_count"] = len(freshness.get("changes", ()))
        for key in ("summary", "remaining_question", "best_available", "accepted_candidates"):
            if size(response) <= budget:
                break
            response[key] = omitted("/" + key)
        if size(response) > budget:
            response.pop("freshness", None)
            response["freshness_request"] = request("changes")
        if size(response) > budget:
            # Even very long scientific names cannot make the minimum configured
            # budget fail. The exact selected result and advancing page remain.
            response = {"state": "selected", "result_name": name, "disposition": value["disposition"],
                "candidate_count": len(value["candidates"]), "fields_omitted": "page_byte_budget",
                "read_request": request("candidates")}
        return response
    candidates = value["candidates"]
    offset, limit = arguments.get("offset", 0), arguments.get("page_limit", 4)
    if section == "candidates":
        items = []
        for index, candidate in enumerate(candidates):
            item = pick(candidate, "candidate", "artifact_name", "analysis_outcome", "execution", "evidence", "scientific",
                "mechanism", "conclusion", "evidence_readiness", "analysis_artifact_name", "method_names", "observation_names", "limitations")
            item.update(is_best_available=candidate["candidate"] in value["best_available"],
                metric_count=len(candidate["metrics"]), metrics_request=request("metrics", candidate=candidate["candidate"]))
            items.append((item, "/candidates/" + str(index)))
    elif section == "metrics":
        alias = arguments.get("candidate")
        indexed = [(index, item) for index, item in enumerate(candidates) if item["candidate"] == alias]
        if len(indexed) != 1:
            raise RootToolError("Select one exact candidate alias from the candidate page.")
        index, candidate = indexed[0]
        response["candidate"] = alias
        items = [(metric, f"/candidates/{index}/metrics/{metric_index}") for metric_index, metric in enumerate(candidate["metrics"])]
    elif section == "contract":
        contract = value["comparison_contract"]
        items = [({"field": key, "value": contract[key]}, "/comparison_contract/" + key) for key in contract]
        response["evaluation_contract_name"] = value["evaluation_contract_name"]
    elif section == "figures":
        items = [(figure, "/figures/" + str(index)) for index, figure in enumerate(value["figures"])]
    elif section == "changes":
        freshness = value.get("freshness", {})
        response["freshness"] = pick(freshness, "state", "examined", "scan_complete", "next_before", "coverage")
        items = [(change, "") for change in freshness.get("changes", ())]
    else:
        raise RootToolError("Unknown scientific-result section.")
    if offset > len(items):
        raise RootToolError("Scientific-result offset exceeds this exact section.")
    response.update(total=len(items), offset=offset, items=[], next_offset=None)
    for index in range(offset, min(len(items), offset + limit)):
        item, pointer = items[index]
        row = json.loads(json.dumps(item))
        trial = {**response, "items": [*response["items"], row], "next_offset": index + 1 if index + 1 < len(items) else None}
        if size(trial) > budget:
            if response["items"]:
                response["next_offset"] = index
                response["page_limited_by"] = "bytes"
                break
            # Keep decisive metadata where possible; oversized explanations or
            # evidence names get explicit exact-original pointers.
            for key in ("conclusion", "limitations", "method_names", "observation_names", "value", "json_pointer", "artifact_name"):
                if key in row:
                    original_key = {"method_names": "method_evidence", "observation_names": "observation_evidence",
                        "artifact_name": "source_key" if section in {"metrics", "figures"} else "candidate"}.get(key, key)
                    row[key] = omitted(pointer + "/" + original_key)
                    trial["items"] = [row]
                    if size(trial) <= budget:
                        break
            if size(trial) > budget:
                row = {"entry_omitted": "page_byte_budget", **omitted(pointer),
                    **pick(item, "candidate", "key", "source_key", "field")}
                trial["items"] = [row]
            if size(trial) > budget:
                raise RootToolError("Scientific-result metadata exceeds the configured page budget.")
            trial["page_limited_by"] = "bytes"
        response = trial
    if response["next_offset"] is not None:
        response["next_request"] = request(section, offset=response["next_offset"],
            **({"candidate": response["candidate"]} if "candidate" in response else {}))
        # The continuation belongs to the budget too; reserve its bytes before
        # returning. Remove complete last entries rather than truncating science.
        while size(response) > budget and len(response["items"]) > 1:
            response["items"].pop()
            response["next_offset"] = offset + len(response["items"])
            response["next_request"]["arguments"]["offset"] = response["next_offset"]
    if size(response) > budget:
        response["items"] = [{"entry_omitted": "page_byte_budget", **omitted(items[offset][1])}]
        response["next_offset"] = offset + 1 if offset + 1 < len(items) else None
        if response["next_offset"] is not None:
            response["next_request"]["arguments"]["offset"] = response["next_offset"]
        else:
            response.pop("next_request", None)
    if size(response) > budget:
        response.pop("next_request", None)
    if size(response) > budget:
        raise RootToolError("Scientific-result metadata exceeds the configured page budget.")
    return response


def root_response(name, value, arguments, *, read_settings=None):
    """Never replace a scientific object, precise request, or error with a verdict."""
    detail = arguments.get("view") == "detail"
    if name == "scientific_result_current":
        from ...agent_execution_settings import MCPReadSettings
        settings = read_settings or MCPReadSettings()
        return scientific_result_response(value, arguments,
            budget=settings.detail_max_bytes if detail else settings.summary_max_bytes)
    if name in {"execution_status", "execution_sync", "execution_start", "execution_cancel", "execution_abandon"}:
        if detail and isinstance(value.get("progress"), dict):
            from ...plugin_runtime.experiment import progress_summary
            # Even status detail does not implicitly deliver log bodies.
            value = {**value, "progress": {**{key: item for key, item in value["progress"].items()
                if key != "log_tails"}, **progress_summary(value["progress"])}}
        return value if detail else execution_summary(value)
    if name == "operation_invoke":
        kind = value.get("executor_kind")
        result = value.get("result")
        if not isinstance(result, dict) or result.get("state") in {"rejected", "failed", "cancelled", "timed_out"}:
            return value
        if kind == "agent":
            result = pick(result, "name", "state", "operation_id", "agent_type", "execution_profile",
                "operation_version", "operation_digest",
                "deadline_at", "output_artifact_name", "draft_from", "recovery", "normalized_request",
                "diagnostics", "missing_inputs", "review_url", "approval_name", "dispatch", "preparation")
            result["detail"] = {"tool": "run_status", "name": result.get("name"), "intent": "navigation"}
        # Transform/effect/approval returns already expose output identities and exact review URLs.
        return {**value, "result": result}
    if name == "run_record_failure":
        return run_intent_projection(value, intent="status")
    if name == "operation_catalog":
        if arguments["view"] in _NAV_BUDGET:
            return operation_navigation(value, arguments)
        if arguments.get("dimension") is not None or arguments.get("where") is not None:
            raise RootToolError("dimension and where require an explicit navigation view")
        items = value["operations"]
        selected = arguments.get("operation_id")
        if selected is not None:
            items = [item for item in items if item["operation_id"] == selected]
            if not items:
                raise RootToolError("operation is not available in the selected catalog scope")
        total = len(items)
        if not detail:
            items = sorted(items, key=lambda item: item["operation_id"])
            snapshot = _nav_digest({"version": "summary.v2", "catalog": value.get("catalog_digest"),
                "state": value.get("navigation_state"), "items": items})
            query = _nav_digest({"version": "summary.v2", "scope": value["scope"], "operation_id": selected})
            return _nav_page([pick(item, "operation_id", "purpose") for item in items],
                key="operation_id", limit=arguments["limit"], before=arguments.get("before"),
                snapshot_digest=snapshot, query_digest=query, budget=8192,
                response={"scope": value["scope"], "view": "summary", "total": total})
        items, cursor = page(items, key="operation_id", limit=arguments["limit"], before=arguments.get("before"))
        items = [operation_detail(item) for item in items]
        return {"scope": value["scope"], "view": "detail" if detail else "summary",
                "operations": items, "total": total, "next_before": cursor,
                "detail": {"tool": "operation_catalog", "scope": value["scope"], "view": "detail", "operation_id": "<selected operation_id>"}}
    lists = {"scientific_inventory": ("objects", "artifact_name"),
             "scientific_current": ("selections", "kind"), "instance_list": ("instances", "name"),
             "execution_outputs": ("outputs", "output_label")}
    if name in lists:
        field, key = lists[name]
        values = value[field]
        items, cursor = page(values, key=key, limit=arguments["limit"], before=arguments.get("before"))
        result = {field: items, "total": len(values), "next_before": cursor}
        if name == "instance_list" and not detail:
            result[field] = [pick(item, "name", "title", "state") for item in items]
        if name == "scientific_inventory":
            if not detail:
                result[field] = [pick(item, "artifact_name", "kind", "schema", "historical",
                    "selected_current", "claim_admissible", "producer_handoff") for item in items]
            result["detail"] = {"tool": "artifact_catalog", "name": "<artifact_name>", "view": "detail"}
            from ...agent_execution_settings import MCPReadSettings
            settings = read_settings or MCPReadSettings()
            return _bounded_list(result, field=field, key=key,
                budget=settings.detail_max_bytes if detail else settings.summary_max_bytes)
        if name == "execution_outputs":
            result["execution_name"] = value["execution_name"]
            result["result_artifact_name"] = value.get("result_artifact_name")
        return result
    if name in {"run_list", "execution_list", "approval_list"}:
        field = {"run_list": "runs", "execution_list": "executions", "approval_list": "approvals"}[name]
        if name == "run_list":
            result = {**value, field: [
                (item if run_is_terminal(item.get("state")) else run_intent_projection(item, intent="status"))
                if detail else pick(item, "name", "operation_id", "state",
                    "operation_contract_status", "recovery_available") for item in value[field]]}
            result["detail"] = {"tool": "run_status", "name": "<name>"}
            from ...agent_execution_settings import MCPReadSettings
            settings = read_settings or MCPReadSettings()
            return _bounded_list(result, field=field, key="name",
                budget=settings.detail_max_bytes if detail else settings.summary_max_bytes)
        if name == "execution_list" and detail:
            return {**value, field: [root_response("execution_status", item, arguments) for item in value[field]]}
        if not detail:
            value = {**value, field: [pick(item, "name", "state", "status", "operation_id",
                        "output_artifact_name", "selected_option", "recovery") for item in value[field]]}
        return value
    if name == "artifact_catalog" and arguments.get("view") == "parents":
        return value
    if name == "artifact_catalog" and arguments.get("view") == "producer_inputs":
        return value
    if name == "artifact_catalog" and not detail:
        return {**pick(value, "name", "kind", "schema", "media_type", "size_bytes", "payload_schema_version"),
                "parent_count": value["parent_count"] if "parent_count" in value else len(value["parents"]),
                "detail": {"tool": name, "name": value["name"], "view": "parents"}}
    if name in {"instance_current", "instance_close"} and not detail:
        return pick(value, "name", "title", "state", "management_url")
    if name == "approval_status" and not detail:
        result = pick(value, "name", "status", "selected_option", "review_url", "rationale")
        rationale = result.get("rationale")
        if isinstance(rationale, str) and len(rationale) > 512:
            result.pop("rationale")
            result.update(rationale_excerpt=rationale[:512], omitted_characters=len(rationale)-512)
        result["detail"] = {"tool": name, "name": value.get("name"), "view": "detail"}
        return result
    if name == "execution_capabilities":
        items = value["capabilities"]
        items, cursor = page(items, key="profile", limit=arguments["limit"], before=arguments.get("before"))
        if not detail:
            items = [pick(item, "profile", "solver_kind", "launch_name", "public_release_label") for item in items]
        return {**value, "capabilities": items, "next_before": cursor,
                "detail": {"tool": "scid_call", "arguments": {"name": name, "surface": "execution",
                    "arguments": {"operation_id": arguments["operation_id"], "view": "detail"}}}}
    return value


__all__ = [
    "operation_detail",
    "operation_invoke_contract",
    "operation_revision_policy",
    "page",
    "pick",
    "root_response",
    "run_intent_projection",
]
