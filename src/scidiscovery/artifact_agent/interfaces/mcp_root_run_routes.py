"""Minimal Run lifecycle routes for the Root scheduler facade."""

from __future__ import annotations

import json
from itertools import islice
from typing import Any

from ..schema.approval import parse_json_pointer
from ..schema.common import canonical_json
from .mcp_response_views import run_is_terminal, run_intent_projection
from .mcp_root_shared import RootToolError


def _pointer_value(value: Any, pointer: str) -> Any:
    for token in parse_json_pointer(pointer):
        if isinstance(value, dict):
            value = value[token]
        elif isinstance(value, list) and (token == "0" or (
            token.isascii() and token.isdigit() and not token.startswith("0")
        )):
            value = value[int(token)]
        else:
            raise KeyError(token)
    return value


def _output_index(output: dict[str, Any], pointer: str, offset: int, limit: int) -> dict[str, Any]:
    """Bound the whole directory, including origin and pointer, without scientific values."""
    budget = 8 * 1024
    result = {key: output[key] for key in ("artifact_name", "kind", "schema")}
    result.update(pointer=pointer, status="available", children=[], total_children=0, next_offset=None)
    if len(canonical_json(result)) > budget:
        raise RootToolError("output index metadata exceeds 8192 bytes; select a shorter ancestor path")
    try:
        value = _pointer_value(output["payload"], pointer)
    except (KeyError, IndexError, ValueError):
        result["status"] = "missing"
        return result
    def metadata(value):
        kind = ("null" if value is None else "boolean" if isinstance(value, bool)
            else "object" if isinstance(value, dict) else "array" if isinstance(value, list)
            else "string" if isinstance(value, str) else "number")
        return {"type": kind, "size_bytes": len(canonical_json(value))}
    result.update(metadata(value))
    total = len(value) if isinstance(value, (dict, list)) else 0
    result["total_children"] = total
    if len(canonical_json(result)) > budget:
        raise RootToolError("output index metadata exceeds 8192 bytes; select a shorter ancestor path")
    if offset > total:
        raise RootToolError(f"index_offset {offset} exceeds total_children {total}")
    candidates = value.items() if isinstance(value, dict) else enumerate(value) if isinstance(value, list) else ()
    for position, (key, child) in enumerate(islice(candidates, offset, offset + limit), offset):
        child_pointer = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
        entry = {"pointer": child_pointer, **metadata(child)}
        result["children"].append(entry)
        result["next_offset"] = position + 1 if position + 1 < total else None
        if len(canonical_json(result)) > budget:
            result["children"].pop()
            if not result["children"]:
                raise RootToolError(f"output index entry at offset {position} exceeds 8192 bytes; read this parent in values mode")
            result["next_offset"] = position
            break
    return result


def _output_selection(output: dict[str, Any], pointers: list[str]) -> dict[str, Any]:
    """Select exact scientific values; omission and navigation are not evidence."""
    remaining = 32 * 1024
    navigation_remaining = 8 * 1024
    items = []
    for pointer in pointers:
        value = output["payload"]
        try:
            value = _pointer_value(value, pointer)
        except (KeyError, IndexError, ValueError):
            items.append({"pointer": pointer, "status": "missing"})
            continue
        size = len(canonical_json(value))
        item = {"pointer": pointer, "size_bytes": size}
        if size <= remaining:
            item.update(status="selected", value=value)
            remaining -= size
        else:
            item["status"] = "omitted"
            if isinstance(value, (dict, list)):
                children = []
                candidates = value.items() if isinstance(value, dict) else enumerate(value)
                for key, child in candidates:
                    if len(children) == 32:
                        break
                    child_pointer = pointer + "/" + str(key).replace("~", "~0").replace("/", "~1")
                    child_type = ("null" if child is None else "boolean" if isinstance(child, bool)
                        else "object" if isinstance(child, dict) else "array" if isinstance(child, list)
                        else "string" if isinstance(child, str) else "number")
                    entry = {"pointer": child_pointer, "type": child_type,
                        "size_bytes": len(canonical_json(child))}
                    # Count the complete navigation array; never shorten a key into a false pointer.
                    cost = len(canonical_json([*children, entry]))
                    if cost <= navigation_remaining:
                        children.append(entry)
                navigation_remaining -= len(canonical_json(children)) if children else 0
                item.update(children=children, omitted_children=len(value) - len(children))
        items.append(item)
    return {key: output[key] for key in ("artifact_name", "kind", "schema")} | {"items": items}


class RootRunRoutes:
    def run_list(self, *, state: str | None, limit: int, before: str | None = None,
                 view: str = "summary") -> dict[str, Any]:
        if view not in {"summary", "detail"}:
            raise RootToolError('run_list view must be "summary" or "detail"')
        if self.runs is None:
            return {"runs": [], "next_before": None}
        if before is not None:
            self._resolve("run", before)
        items = []
        past_cursor = before is None
        for binding in self.bindings.list(instance=self._instance_id(), namespace="run"):
            if not past_cursor:
                past_cursor = binding.name == before
                continue
            value = self.runs.status(binding.object_id)
            if state is not None and value.state != state:
                continue
            if len(items) == limit:
                return {"runs": items, "next_before": items[-1]["name"]}
            items.append(self._scientific_run_view(value, self._run_status_value(binding.name, value))
                if view == "detail" and run_is_terminal(value.state)
                else self._compact_run_status(name=binding.name, value=value,
                    intent="status", output_paths=[], index_offset=0, index_limit=16))
        return {"runs": items, "next_before": None}

    def run_status(self, *, name: str, **arguments) -> dict[str, Any]:
        from .mcp_root import parse_run_status_arguments
        request = parse_run_status_arguments({"name": name, **arguments})
        if self.runs is None:
            raise RuntimeError("minimal Run service is unavailable")
        value = self.runs.status(self._resolve("run", name))
        result = self._compact_run_status(name=name, value=value, **{
            key: request[key] for key in ("intent", "output_paths", "index_offset", "index_limit")})
        if request["intent"] == "status" and request["diagnostic_after"] is not None:
            page = self.runs.diagnostic_events(value, after=request["diagnostic_after"],
                limit=request["diagnostic_limit"])
            result["diagnostic_events"] = {"events": [
                {"event_id": event["event_id"], "recorded_at": event["recorded_at"],
                 "diagnostic": self._scientific_run_diagnostic(value, event["diagnostic"])}
                for event in page["events"]], "next_after": page["next_after"]}
        if request["intent"] == "navigation" and run_is_terminal(value.state):
            aliases = {b.name: b.object_id for b in self.bindings.list(
                instance=self._instance_id(), namespace="artifact")}
            inputs = {}
            for item in value.inputs:
                alias = item.artifact_name if aliases.get(item.artifact_name) == item.artifact_ref.artifact_id else None
                inputs.setdefault(item.port_name, []).append(alias)
            # Preserve exact frozen aliases and order; hidden/derived ports stay private.
            result["bound_inputs"] = self._scientific_run_view(value, {"bound_inputs": [
                {"port": port, "artifact_names": names} for port, names in inputs.items()]})["bound_inputs"]
        if request["intent"] == "navigation":
            evidence = (self.runs.evidence_output_refs(value) if value.state == "completed"
                else self.runs.sealed_evidence_output_refs(value))
            if evidence:
                page = []
                offset = request["index_offset"]
                for alias, ref in evidence[offset:offset + request["index_limit"]]:
                    item = {"artifact_name": value.output_binding_name + "." + alias, "schema": ref.schema_id}
                    if len(canonical_json([*page, item])) > 8192:
                        break
                    page.append(item)
                end = offset + len(page)
                result.update(evidence_outputs=page, evidence_outputs_total=len(evidence),
                    evidence_outputs_next_offset=end if end < len(evidence) else None,
                    evidence_outputs_status="sealed_materials_not_final_report")
        if request["stage_offset"] is not None or request["stage_reference"] is not None:
            from ..service.stage_deliveries import read_stage_deliveries
            result["sealed_stages"] = read_stage_deliveries(self.runs, value,
                instance_id=self._instance_id(), **{key: request[key] for key in (
                    "stage_offset", "stage_limit", "stage_reference", "stage_text_offset")})
        return result

    def _compact_run_status(
        self,
        *,
        name: str,
        value: Any,
        intent: str,
        output_paths: list[str] | None,
        index_offset: int,
        index_limit: int,
    ) -> dict[str, Any]:
        include_payload = intent in {"navigation", "decision", "full"}
        output_status, output = self._sealed_output(value, include_payload=include_payload)
        contract_status = self._operation_catalog.recorded_contract_status(value.operation_id,
            version=value.operation_version, digest=value.operation_digest)
        result = {
            "name": name,
            "operation_id": value.operation_id,
            "operation_version": value.operation_version,
            "operation_digest": value.operation_digest,
            "operation_contract_status": contract_status,
            "state": value.state,
            "agent_type": value.agent_type,
            "execution_profile": value.execution_profile,
            "deadline_at": value.deadline_at,
            "last_activity_at": value.last_activity_at,
            "output_artifact_name": value.output_binding_name if value.output_ref is not None else None,
            "recovery_available": self.runs.recovery_available(value),
            "diagnostics_available": True,
            "sealed_output_status": output_status,
            "scheduler_signal_status": (
                "available"
                if output is not None and value.signal is not None
                else "unavailable"
            ),
        }
        if value.state == "failed":
            result["diagnostic_summary"] = self.runs.diagnostic_summary(value)
        attention = self.runs.submission_attention(value)
        if attention is not None:
            result["attention_required"] = attention
        if intent != "status" and value.state != "completed":
            result["content_unavailable"] = "run_not_completed"
        if intent == "full" and value.state == "completed":
            result["output_delivery"] = "full"
            result["sealed_output"] = output
            result["scheduler_signal"] = (value.signal.model_dump(mode="json")
                if output_status in {"available", "historical"} and value.signal is not None else None)
        elif intent == "navigation" and value.state == "completed":
            result["output_delivery"] = "index"
            result["output_index"] = (
                _output_index(
                    output,
                    (output_paths or [""])[0],
                    index_offset,
                    index_limit,
                )
                if output is not None
                else None
            )
        elif intent == "decision" and value.state == "completed":
            if not output_paths:
                # The frozen read projection remains useful after its authoring contract
                # retires; it never establishes current scientific qualification.
                fields = value.decision_fields
                if fields is None and contract_status == "current":
                    fields = compiled.spec.decision_fields
                if fields is None:
                    result["default_output_unavailable"] = "decision_fields_not_recorded"
                    result["output_index"] = (_output_index(output, "", index_offset, index_limit)
                        if output is not None else None)
                else:
                    output_paths = [field if field.startswith("/") else "/" + field.replace("~", "~0").replace("/", "~1") for field in fields]
            result["output_delivery"] = "index" if "default_output_unavailable" in result else "selected"
            result["output_metadata"] = (
                {key: output[key] for key in ("artifact_name", "kind", "schema")}
                if output is not None
                else None
            )
            result["selected_output"] = (
                _output_selection(output, output_paths or [])
                if output is not None and output_paths
                else None
            )
            result["scheduler_signal"] = (
                value.signal.model_dump(mode="json")
                if output_status in {"available", "historical"}
                and value.signal is not None
                else None
            )
        if intent == "decision" and isinstance(result.get("scheduler_signal"), dict):
            signal = dict(result["scheduler_signal"])
            omissions = {}
            for field in ("assumptions", "missing_inputs", "next_actions"):
                entries = signal.get(field, [])
                shown = [entry for entry in entries[:4] if len(entry.encode("utf-8")) <= 1024]
                signal[field] = shown
                if len(shown) != len(entries):
                    omissions[field] = len(entries) - len(shown)
            result["scheduler_signal"] = signal
            if omissions:
                result["scheduler_signal_omissions"] = {"omitted_items": omissions,
                    "read": {"tool": "run_status", "name": name, "intent": "full"}}
        return run_intent_projection(self._scientific_run_view(value, result), intent=intent)

    def _scientific_run_diagnostic(self, value, diagnostic):
        if diagnostic is None:
            return None
        safe = self.runs.sanitize_diagnostic(value, diagnostic,
            repairable=diagnostic.get("repairable_by_output") is True)
        # Revalidate saved details, including old records. Only an opaque handle
        # crosses this boundary; diagnostic_read separately enforces instance scope.
        from ..service.engineering_diagnostics import diagnostic_locator
        locator = diagnostic_locator(safe.get("engineering"))
        return {**({"engineering": locator} if locator else {}),**{key: safe[key] for key in ("category", "code", "repairable_by_output", "tool_name") if key in safe},
            "details": [{key: item[key] for key in
                ("code", "path", "message", "phase", "affected_action", "repairable", "rule_id") if key in item}
                for item in safe.get("details", ())]}

    def _scientific_run_view(self, value, result):
        if "reason" in result:
            result["reason"] = None
        if value.state == "failed":
            failure = result.get("diagnostic_summary", {}).get("failure") or {}
            result["reason"] = {
                "run_timeout": "The Run exceeded its time limit. Inspect the saved diagnostics before choosing recovery.",
                "output_rejected": "The output did not satisfy its declared contract. Correct the reported scientific fields before retrying.",
                "integrity_failure": "The sealed materials failed integrity checks. Restore the exact originals before retrying.",
                "admission_defect": "The selected materials did not satisfy the declared input requirements. Inspect the saved diagnostics.",
            }.get(failure.get("category"), "The Run failed. Read the saved diagnostic page to identify a repair or choose another scientific action.")
        if value.operation_id in {"science.experiment.v1", "science.results.compare.v1"}:
            result["scientific_result_semantics"] = {"scheduler_verdict": "delivery_only",
                "scientific_acceptance": "not_assessed" if value.operation_id == "science.experiment.v1" else "read_structured_scientific_result",
                "reason": "completed_or_pass_does_not_mean_model_accepted"}
        allowed = {"name", "operation_id", "operation_contract_status", "state", "agent_type", "execution_profile", "scientific_result_semantics",
            "created_at", "started_at", "completed_at", "deadline_at", "last_activity_at", "output_artifact_name",
            "recovery_available", "draft_from", "diagnostics_available", "attention_required", "reason", "diagnostic_events", "sealed_output_status", "sealed_output",
            "scheduler_signal_status", "scheduler_signal", "scheduler_signal_omissions", "output_delivery", "output_metadata", "selected_output",
            "output_index", "default_output_unavailable", "evidence_outputs", "bound_inputs", "content_unavailable", "dispatch"}
        result = {key:item for key,item in result.items() if key in allowed}
        try:
            ports = self._operation_catalog.recorded_operation(value.operation_id, version=value.operation_version, digest=value.operation_digest).spec.inputs
            visible = {port.name for port in ports if port.agent_visible and port.derivation is None}
        except KeyError:
            visible = set()
        if "bound_inputs" in result:
            result["bound_inputs"] = [item for item in result["bound_inputs"] if item["port"] in visible]
        if value.state == "queued":
            result["dispatch"] = {
                "agent_type": value.agent_type,
                "instruction": "Spawn the returned agent_type with execution_profile.profile model and reasoning_effort, without parent history. Ask it to wait for attachment; use worker_attach with this Run name and the actual platform thread ID, then ask it to open its assignment. A formal reviewer must be a different Agent from its subject author. Do not relay scientific results through chat.",
                "attachment": {"tool": "worker_attach", "name": result.get("name")},
                "after_completion": {"tool": "run_status", "name": result.get("name")}}
        if isinstance(result.get("execution_profile"), dict):
            result["execution_profile"] = {"profile":result["execution_profile"]["profile"]}
        return result

    def run_record_failure(
        self,
        *,
        name: str,
        reason: str,
        expected_state: str,
        expected_last_activity_at: str | None,
        timed_out: bool,
    ) -> dict[str, Any]:
        if self.runs is None:
            raise RuntimeError("minimal Run service is unavailable")
        self.runs.record_failure(
            self._resolve("run", name),
            reason=reason,
            expected_state=expected_state,
            expected_last_activity_at=expected_last_activity_at,
            timed_out=timed_out,
        )
        return self.run_status(name=name)

    def _run_status_value(self, name: str, value: Any) -> dict[str, Any]:
        binding = self._binding("run", name)
        return {
            **self._binding_value(binding),
            "operation_id": value.operation_id,
            "agent_type": value.agent_type,
            "execution_profile": value.execution_profile,
            "backend": value.backend_id,
            "state": value.state,
            "output_artifact_name": (
                value.output_binding_name if value.output_ref is not None else None
            ),
            "reason": value.reason,
            "created_at": value.created_at,
            "started_at": value.started_at,
            "deadline_at": value.deadline_at,
            "completed_at": value.completed_at,
            "last_activity_at": value.last_activity_at,
            "candidate_accepted": value.accepted_candidate_digest is not None,
            "head_advance": (
                None
                if value.completion_receipt is None
                else value.completion_receipt.head_advance
            ),
            "recovery_available": self.runs.recovery_available(value),
            "recovery": self.runs.recovery_status(value),
            "diagnostic_summary": self.runs.diagnostic_summary(value),
            "tool_timing": self.runs.tool_timing(value.run_id),
            "draft_from": (
                None if value.draft_from_run_id is None else self.bindings.find_name(
                    instance=self._instance_id(), namespace="run",
                    object_id=value.draft_from_run_id,
                )
            ),
        }

    def _sealed_output(
        self, value: Any, *, include_payload: bool = True
    ) -> tuple[str, dict[str, Any] | None]:
        """Expose scientific content only after the Run completed and sealed it."""

        if value.state != "completed" or value.output_ref is None:
            return "unavailable", None
        contract_status = self._operation_catalog.recorded_contract_status(value.operation_id,
            version=value.operation_version, digest=value.operation_digest)
        output_status = "historical" if contract_status == "unavailable" else "available"
        metadata = {"artifact_name": value.output_binding_name, "kind": value.output_ref.kind,
            "schema": value.output_ref.schema_id}
        if not include_payload:
            return output_status, metadata
        payload = json.loads(self.artifacts.read(value.output_ref))
        if not isinstance(payload, dict):
            raise RuntimeError("completed scientific output is not a JSON object")
        return (
            output_status,
            {
                **metadata,
                "payload": payload,
            },
        )


__all__ = ["RootRunRoutes"]
