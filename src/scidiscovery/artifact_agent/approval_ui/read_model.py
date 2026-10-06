"""Instance-scoped, read-only views over existing immutable control records.

Only semantic bindings, Run inputs/outputs, execution control references and
ArtifactEnvelope provenance grant reachability. Scientific JSON never does.
No current scientific validator or operation admission path runs here.
"""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import asdict
import json
import re
from typing import Any

from ..schema.approval import parse_json_pointer
from ..service.approvals import ApprovalError, ApprovalReview
from ..service.executions import ExecutionServiceError
from ..service.run_records import RunError
from ..service.scheduler_bindings import SchedulerNameNotFound
from ..storage import ArtifactNotFoundError, ArtifactRegistryError, CASIntegrityError, CASObjectMissingError
from .view_models import MAX_PAYLOAD_BYTES, MAX_RESPONSE_BYTES, MAX_SOURCE_BYTES, NodePage, bounded_record, gap, json_size, select_json


class ReadModelNotFound(ValueError):
    pass


class ReadModelScopeError(PermissionError):
    pass


_READ_ERRORS = (ArtifactRegistryError, CASIntegrityError, RunError, ApprovalError, ExecutionServiceError)
_KINDS = {"run", "approval", "execution", "artifact"}
MAX_SCOPE_RECORDS = 1000


class InstanceReadModel:
    def __init__(self, *, artifacts, bindings, runs, approvals, executions, operation_catalog,
                 engineering_diagnostics=None, execution_collection=None, presentation_summaries=None) -> None:
        self.artifacts = artifacts
        self.bindings = bindings
        self.runs = runs
        self.approvals = approvals
        self.executions = executions
        self.operation_catalog = operation_catalog
        self.engineering_diagnostics = engineering_diagnostics
        self.execution_collection = execution_collection
        self.presentation_summaries = presentation_summaries

    def overview(self, instance_id: str) -> dict[str, Any]:
        instance = self.bindings.get_instance(instance_id=instance_id)
        recent = self.nodes(instance_id, limit=30)
        active, pages, gaps = [], {}, []
        for namespace, service in (("run", self.runs), ("approval", self.approvals), ("execution", self.executions)):
            if service is None:
                gaps.append(gap("control_service_unavailable", kind=namespace))
                continue
            arguments = {"instance_id": instance_id, "limit": 31}
            if namespace != "run":
                arguments["scheduler_database_path"] = self.bindings.database_path
            ids = service.active_ids(**arguments)
            pages[namespace] = {"limit": 30, "has_more": len(ids) > 30}
            for object_id in ids[:30]:
                name = self.bindings.find_name(instance=instance_id, namespace=namespace, object_id=object_id)
                if name is None:
                    gaps.append(gap("active_control_binding_missing", kind=namespace))
                    continue
                active.append(self._metadata(instance_id, self._binding(instance_id, namespace + ":" + name)))
        objectives = []
        for item in active:
            if item["kind"] != "run":
                continue
            binding = self._binding(instance_id, item["key"])
            run = self._run(instance_id, binding.object_id)
            objectives.extend(
                self._ref(instance_id, source.artifact_ref, f"/inputs/{index}/artifact_ref")
                for index, source in enumerate(run.inputs) if source.port_name in {"objective", "research_objective"}
            )
        result = {
            "instance": asdict(instance),
            "management_description": instance.objective,
            "objective_refs": objectives[:100],
            "active_tasks": active,
            "active_task_pages": pages,
            "nodes": recent,
            "gaps": gaps + ([] if objectives else [gap("no_objective_in_observed_active_run_bindings")]),
        }
        selected = []
        selections = self.bindings.scientific_selections(instance=instance_id, limit=101)
        for selection in selections[:100]:
            if selection.artifact_ref is None:
                continue
            name = self.bindings.find_name(instance=instance_id, namespace="artifact", object_id=selection.artifact_ref.artifact_id)
            if name:
                # Resolve the complete exact identity, not a same-schema head.
                try:
                    self.artifacts.catalog(selection.artifact_ref)
                    selected.append({"key": "artifact:" + name, "kind": selection.kind,
                        "logical_name": selection.logical_name, "selected_at": selection.selected_at})
                except _READ_ERRORS as error:
                    result["gaps"].append(self._read_gap(error, selection_kind=selection.kind))
        result["selected_nodes"] = selected[:100]
        result["selected_node"] = selected[0] if len(selected) == 1 and len(selections) == 1 else None
        from ..service.scientific_results import current_result
        try:
            result["scientific_result"] = current_result(artifacts=self.artifacts, runs=self.runs,
                bindings=self.bindings, instance_id=instance_id)
        except (ValueError, *_READ_ERRORS) as error:
            result["scientific_result"] = {"state": "unavailable", "reason": type(error).__name__}
            result["gaps"].append(gap("selected_scientific_result_unavailable"))
        if len(selections) > 100:
            result["gaps"].append(gap("current_selection_read_limit"))
        if len(selections) > 1:
            result["gaps"].append(gap("current_selection_display_ambiguous"))
        reader = getattr(self.runs, "recent_ids", None)
        recent_ids = reader(instance_id=instance_id, limit=1) if reader else ()
        recent_name = (self.bindings.find_name(instance=instance_id, namespace="run", object_id=recent_ids[0]) if recent_ids else None)
        result["recent_node"] = {"key": "run:" + recent_name, "basis": "most_recent_run"} if recent_name else None
        if not objectives:
            anchor = None
            active_complete = not any(page["has_more"] for page in pages.values()) and not any(
                item["code"] in {"active_control_binding_missing", "control_service_unavailable"} for item in gaps)
            if len(active) == 1 and active_complete:
                anchor = {"key": active[0]["key"], "basis": "only_active_task"}
            elif not active and active_complete:
                if result["selected_node"]:
                    anchor = {"key": result["selected_node"]["key"], "basis": "only_selected_node"}
                elif not selections:
                    anchor = result["recent_node"]
            if anchor:
                result["objective_basis"] = anchor
                try:
                    binding = self._binding(instance_id, anchor["key"])
                    refs, lineage_gaps = self._objective_navigation(instance_id, binding, self._roots(instance_id, binding))
                    if len(refs) == 1 and not lineage_gaps:
                        result["objective_refs"] = refs
                        result["gaps"] = [item for item in result["gaps"]
                                          if item["code"] != "no_objective_in_observed_active_run_bindings"]
                    result["gaps"].extend(lineage_gaps)
                except _READ_ERRORS as error:
                    result["gaps"].append(self._read_gap(error))
        while json_size(result) > MAX_RESPONSE_BYTES and result["active_tasks"]:
            removed = result["active_tasks"].pop()
            result["active_task_pages"][removed["kind"]]["has_more"] = True
        while json_size(result) > MAX_RESPONSE_BYTES and len(recent["items"]) > 1:
            recent["items"].pop()
            last = self._binding(instance_id, recent["items"][-1]["key"])
            recent["next_cursor"] = self._cursor(instance_id, last)
        return result

    def trajectory(self, instance_id: str, page: int = 1) -> dict[str, Any]:
        limit = 10
        self.bindings.get_instance(instance_id=instance_id)
        if type(page) is not int or page < 1:
            raise ValueError("invalid trajectory page")
        records, total, page = self.bindings.trajectory_page(instance=instance_id, page=page, limit=limit)
        # Cards need metadata only; scientific payloads remain behind the node links.
        fields = ("key", "kind", "name", "title", "state", "created_at", "source_time",
                  "last_activity_at", "completed_at", "collection_status", "operation_id")
        from .experiment_trajectory import parent_binding, read_children
        items = []
        grouped = set()
        for record in records:
            # Keep numbered source-record pagination. A child whose parent lies
            # on another page still appears under that exact experiment here.
            try:
                owner = parent_binding(self, instance_id, record)
            except _READ_ERRORS:
                owner = None  # Unresolved ownership stays visible as a root.
            record = owner or record
            if (record.namespace, record.object_id) in grouped:
                continue
            grouped.add((record.namespace, record.object_id))
            metadata = self._metadata(instance_id, record)
            node = {key: metadata[key] for key in fields if key in metadata}
            if metadata.get("operation_id") == "science.experiment.v1":
                node["experiment_children"] = read_children(self, self._run(instance_id, record.object_id))
            items.append(node)
        return {"items": items, "page": page, "total": total,
                "total_pages": max(1, (total + limit - 1) // limit), "page_size": limit}

    def nodes(self, instance_id: str, cursor: str | None = None, limit: int = 30) -> NodePage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("node limit must be between 1 and 100")
        after = self._decode_cursor(instance_id, cursor)
        records = self.bindings.binding_page(instance=instance_id, after=after, limit=limit + 1)
        items, visible, size = [], [], 0
        for record in records[:limit]:
            item = self._metadata(instance_id, record)
            size += json_size(item)
            if items and size > MAX_RESPONSE_BYTES - 8192:
                break
            visible.append(record)
            items.append(item)
        return {"items": items,
                "next_cursor": self._cursor(instance_id, visible[-1]) if len(records) > len(visible) else None}

    def node(self, instance_id: str, key: str, diagnostic_after: int = 0, diagnostic_limit: int = 50,
             stage_offset: int = 0, stage_limit: int = 4, stage_reference: str | None = None,
             stage_text_offset: int = 0, child_offset: int = 0) -> dict[str, Any]:
        if (type(diagnostic_after) is not int or diagnostic_after < 0
                or type(diagnostic_limit) is not int or not 1 <= diagnostic_limit <= 100):
            raise ValueError("invalid diagnostic page bounds")
        binding = self._binding(instance_id, key)
        result = self._metadata(instance_id, binding)
        result["source"] = {"namespace": binding.namespace, "name": binding.name, "object_id": binding.object_id}
        if stage_reference:
            self._stage_display_copy(instance_id, key, stage_reference, result)
        result["gaps"] = []
        result["qualification"] = {"state": "not_evaluated", "reason": "historical_read_does_not_establish_current_qualification"}
        try:
            if binding.namespace == "artifact":
                result.update(self._artifact_view(instance_id, self.artifacts.get_by_id(binding.object_id)))
            elif binding.namespace == "run":
                value = self._run(instance_id, binding.object_id)
                result["record"] = bounded_record(self._run_record(value))
                result["inputs"] = [self._ref(instance_id, item.artifact_ref, f"/inputs/{i}/artifact_ref", port_name=item.port_name, artifact_name=item.artifact_name) for i, item in enumerate(value.inputs)]
                result["outputs"] = [] if value.output_ref is None else [self._ref(instance_id, value.output_ref, "/output_ref")]
                result["diagnostics"] = self.runs.diagnostic_events(value, after=diagnostic_after, limit=diagnostic_limit)
                self._run_detail(result, value)
                if value.operation_id == "science.experiment.v1":
                    from .experiment_trajectory import read_children
                    result["experiment_children"] = read_children(self, value, offset=child_offset)
                    from ..service.stage_deliveries import read_stage_deliveries
                    result["sealed_stages"] = read_stage_deliveries(self.runs, value, instance_id=instance_id,
                        stage_offset=stage_offset, stage_limit=stage_limit, stage_reference=stage_reference,
                        stage_text_offset=stage_text_offset)
                result["signal"] = value.signal.model_dump(mode="json") if value.state == "completed" and value.signal else None
                if value.state == "completed" and value.output_ref:
                    result["sealed_output"] = self._artifact_view(instance_id, self.artifacts.catalog(value.output_ref))
                else:
                    result["gaps"].append(gap("sealed_output_not_available", state=value.state))
            elif binding.namespace == "approval":
                value = self.approvals.status(binding.object_id)
                result["request"] = self._presentation_view(instance_id, self.artifacts.catalog(value.approval_request_ref))
                result["decision"] = None if value.decision_ref is None else self._artifact_view(instance_id, self.artifacts.catalog(value.decision_ref))
            else:
                value = self.executions.status(binding.object_id)
                result["record"] = bounded_record(self._execution_record(value))
                refs = self.executions.record_references(binding.object_id)
                result["inputs"] = [self._ref(instance_id, ref, "/" + name) for name, ref in refs.items() if ref and name != "result_ref"]
                result["outputs"] = [] if value.result_ref is None else [self._ref(instance_id, value.result_ref, "/result_ref")]
                result["gaps"].append(gap("execution_transition_history_not_recorded"))
                result["collection"] = self._collection(binding.object_id)
                result["observation"] = bounded_record(self.executions.observation(binding.object_id))
                if value.result_ref:
                    result["sealed_output"] = self._artifact_view(instance_id, self.artifacts.catalog(value.result_ref))
        except _READ_ERRORS as error:
            result["gaps"].append(self._read_gap(error))
        try:
            roots = self._roots(instance_id, binding)
            result["relationships"] = self._relationships(instance_id, binding, roots)
            result["objective_refs"], objective_gaps = self._objective_navigation(instance_id, binding, roots)
            result["gaps"].extend(objective_gaps)
        except _READ_ERRORS as error:
            result["gaps"].append(self._read_gap(error))
        return self._bounded_node(result)

    def stage_deliveries(self, instance_id: str, key: str, **arguments):
        binding = self._binding(instance_id, key)
        if binding.namespace != "run":
            return None
        from ..service.stage_deliveries import read_stage_deliveries
        value = self._run(instance_id, binding.object_id)
        if value.operation_id != "science.experiment.v1":
            return None
        return read_stage_deliveries(self.runs, value, instance_id=instance_id, **arguments)

    def display_source(self, instance_id, key, stage_reference=None):
        """Resolve the same sealed source for display, refresh and explicit generation."""
        from ..schema.refs import ArtifactRef
        binding = self._binding(instance_id, key)
        if binding.namespace != "run":
            raise ReadModelNotFound("展示整理需要任务的封存材料。")
        run = self._run(instance_id, binding.object_id)
        if stage_reference:
            record = self.runs.sealed_material_record(run, stage_reference)
            if record is None or record.get("metadata", {}).get("kind") not in {
                "experiment_stage", "experiment_implementation", "experiment_debug", "experiment_execution"}:
                raise ReadModelNotFound("没有找到该任务的确切阶段材料。")
            return run, ArtifactRef.model_validate(record["artifact_ref"]), record["metadata"]
        if run.output_ref is None:
            raise ReadModelNotFound("任务尚无封存结果。")
        return run, run.output_ref, {}

    def _stage_display_copy(self, instance_id, key, stage_reference, result):
        _, ref, metadata = self.display_source(instance_id, key, stage_reference)
        from .experiment_trajectory import PHASES, KINDS
        phase = metadata.get("stage") or KINDS.get(metadata.get("kind"))
        result["stage_reference"] = stage_reference
        result["stage_title"] = PHASES.get(phase, "阶段材料")
        result["display_copy"] = ((self.presentation_summaries.read(instance_id, ref)
            if self.presentation_summaries is not None else None) or {"state": "not_generated", "items": []})

    def node_metadata(self, instance_id: str, key: str, *, stage_reference=None) -> dict[str, Any]:
        """Refresh one known node without touching scientific payloads or drafts."""
        binding = self._binding(instance_id, key)
        result = self._metadata(instance_id, binding)
        if stage_reference:
            self._stage_display_copy(instance_id, key, stage_reference, result)
        if result.get("operation_id") == "science.experiment.v1":
            from .experiment_progress import read_progress
            from .experiment_trajectory import read_children
            result["experiment_progress"] = read_progress(self, self._run(instance_id, binding.object_id))
            result["experiment_children"] = read_children(self, self._run(instance_id, binding.object_id))
        return result

    def node_context(self, instance_id: str, key: str) -> dict[str, Any]:
        binding = self._binding(instance_id, key)
        try:
            roots = self._roots(instance_id, binding)
            focus = ()
            task_refs = ()
            priority_refs = ()
            presentation_controls = {}
            selected_run_id = None
            if binding.namespace == "run":
                run = self._run(instance_id, binding.object_id)
                task_refs = tuple(item.artifact_ref for item in run.inputs if item.port_name == "experiment_plan")
                if run.state == "completed" and run.output_ref is not None:
                    focus = (run.output_ref,)
                    selected_run_id = run.run_id
                    priority_refs, presentation_controls = self._run_presentation_refs(run)
            elif binding.namespace == "artifact":
                focus = roots[:1]
                if focus:
                    priority_refs, presentation_controls = self._focused_presentation_refs(focus)
            elif binding.namespace == "approval":
                # The request and decision describe this approval; ancestor
                # reviews remain evidence, never its current decision.
                focus = roots
            elif binding.namespace == "execution":
                result_ref = self.executions.record_references(binding.object_id).get("result_ref")
                focus = (result_ref,) if result_ref else ()
            ordered = tuple(dict.fromkeys((*focus, *task_refs, *roots)))
            views, gaps = self._lineage_views(
                instance_id, ordered, presentation=True, priority_refs=priority_refs
            )
            for view in views:
                if view["artifact_id"] in presentation_controls:
                    view["family"].update(presentation_controls[view["artifact_id"]])
            if selected_run_id is not None:
                for view in views:
                    if view["artifact_id"] == focus[0].artifact_id:
                        view["family"]["selected_run_id"] = selected_run_id
                        break
            # Ordinary instance browsing may associate same-invocation siblings.
            # Frozen approval_context deliberately does not take this path.
            from .presentation import _provider_entries, entry_points
            family_schemas = set()
            for entry in _provider_entries(entry_points):
                try:
                    family_schemas.update(getattr(entry.load(), "family_schemas", ()))
                except Exception:
                    gaps.append(gap("family_provider_unavailable"))
            families = set()
            for view in tuple(views):
                fingerprint = view.get("family", {}).get("operation_invocation_fingerprint")
                if view.get("schema_id") not in family_schemas or not fingerprint or fingerprint in families:
                    continue
                families.add(fingerprint)
                extra, family_gaps = self._family_views(instance_id, view["artifact_id"])
                gaps.extend(family_gaps)
                if family_gaps:
                    for member in (*views, *extra):
                        if member.get("family", {}).get("operation_invocation_fingerprint") == fingerprint:
                            member["family"]["lookup_incomplete"] = True
                known = {item["artifact_id"] for item in views}
                for sibling in extra:
                    if sibling["artifact_id"] in known:
                        continue
                    if len(views) >= 100 or json_size(views) + json_size(sibling) > MAX_RESPONSE_BYTES - 65536:
                        gaps.append(gap("family_display_limit"))
                        for member in views:
                            if member.get("family", {}).get("operation_invocation_fingerprint") == fingerprint:
                                member["family"]["lookup_incomplete"] = True
                        break
                    views.append(sibling)
                    known.add(sibling["artifact_id"])
            return {"artifacts": views, "focus_artifact_ids": [ref.artifact_id for ref in focus],
                    "task_artifact_ids": [ref.artifact_id for ref in task_refs],
                    "gaps": gaps, "scope": "exact_node_roots_and_envelope_provenance"}
        except _READ_ERRORS as error:
            return {"artifacts": [], "gaps": [self._read_gap(error)], "scope": "exact_node_roots_and_envelope_provenance"}

    def _run_presentation_refs(self, run):
        """Prioritize only the selected Run's exact published evidence family."""
        output = self.artifacts.catalog(run.output_ref)
        operation = (run.operation_id, run.operation_version, run.operation_digest)
        manifests, scan_complete = [], True
        for manifest_ref in output.parent_refs:
            if manifest_ref.schema_id != "scidiscovery.tool-evidence-manifest.v1":
                continue
            try:
                manifest = self.artifacts.catalog(manifest_ref)
            except _READ_ERRORS:
                scan_complete = False
                continue
            labels = manifest.labels
            if (labels.get("tool_producer_run") != run.run_id
                    or labels.get("operation_output_port") != "recovery_manifest_output"
                    or tuple(labels.get(key) for key in (
                        "operation_id", "operation_version", "operation_digest")) != operation):
                continue
            manifests.append((manifest_ref, manifest))
        controls = {output.artifact_id: {
            "presentation_manifest_match_count": len(manifests),
            "presentation_manifest_scan_complete": scan_complete,
        }}
        if not scan_complete or len(manifests) != 1:
            return (run.output_ref,), controls
        manifest_ref, manifest = manifests[0]
        result = [run.output_ref, manifest_ref]
        result.extend(self._presentation_file_refs(output, manifest))
        return tuple(dict.fromkeys(result)), controls

    def _presentation_file_refs(self, report, manifest):
        """Resolve published attachments and cited images through one exact manifest."""
        if (report.content_encoding != "identity" or manifest.content_encoding != "identity"
                or report.size_bytes > MAX_SOURCE_BYTES or manifest.size_bytes > MAX_SOURCE_BYTES):
            return ()
        try:
            report_payload = json.loads(self.artifacts.read(report.ref), parse_constant=_reject_constant)
            manifest_payload = json.loads(self.artifacts.read(manifest.ref), parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError, *_READ_ERRORS):
            return ()
        if not isinstance(report_payload, dict) or not isinstance(manifest_payload, dict):
            return ()
        citations = {}
        evidence = report_payload.get("evidence")
        if isinstance(evidence, list):
            for item in evidence:
                if (not isinstance(item, dict) or item.get("source_type") != "runtime_output"
                        or not isinstance(item.get("source_key"), str)
                        or re.fullmatch(r"tool_evidence_[0-9]+", item["source_key"]) is None):
                    continue
                citations.setdefault(item["source_key"], []).append(item)
        records, bindings = manifest_payload.get("records"), manifest_payload.get("bindings")
        if not isinstance(records, list) or not isinstance(bindings, dict):
            return ()
        by_alias = {}
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("alias"), str):
                by_alias.setdefault(record["alias"], []).append(record)
        # Attachments are already an explicit Worker publication. They do not
        # require an additional schema-specific evidence citation to be visible.
        for alias, matches in by_alias.items():
            if len(matches) == 1 and matches[0].get("output_port") == "attachments":
                citations[alias] = [None]
        result, downloads = [], []
        if report.schema_id == "scidiscovery.layered-diagnosis.v1":
            # Inputs are immutable evidence too. Resolve only exact references
            # present in both the report and its control manifest, never names.
            for binding in bindings.values():
                if not isinstance(binding, dict) or binding.get("port_name") in {
                        "tool_evidence", "attachments", "reference_access"}:
                    continue
                matches = [parent for parent in report.parent_refs
                    if parent.model_dump(mode="json") == binding.get("artifact_ref")
                    and parent in manifest.parent_refs]
                if len(matches) != 1:
                    continue
                try:
                    image = self.artifacts.catalog(matches[0])
                except _READ_ERRORS:
                    continue
                if image.media_type.split(";", 1)[0].strip().lower() in {"image/png", "image/jpeg"}:
                    result.append(matches[0])
        for alias, references in citations.items():
            matches = by_alias.get(alias, ())
            if len(references) != 1 or len(matches) != 1:
                continue
            record = matches[0]
            media = (record.get("media_type", "").split(";", 1)[0].lower()
                     if isinstance(record.get("media_type"), str) else "")
            reference = record.get("artifact_ref")
            binding = bindings.get(alias)
            parent_matches = [parent for parent in manifest.parent_refs
                              if parent.model_dump(mode="json") == reference]
            port = "attachments" if record.get("output_port") == "attachments" else "tool_evidence"
            if ((port != "attachments" and media not in {"image/png", "image/jpeg"}) or not isinstance(reference, dict)
                    or len(parent_matches) != 1 or not isinstance(binding, dict)
                    or binding.get("port_name") != port
                    or binding.get("artifact_ref") != reference):
                continue
            try:
                image = self.artifacts.catalog(parent_matches[0])
            except _READ_ERRORS:
                continue
            if (image.media_type.split(";", 1)[0].lower() != media
                    or image.size_bytes != record.get("size_bytes")):
                continue
            if port == "attachments" and image.labels.get("operation_output_port") != "attachments":
                continue
            (result if media in {"image/png", "image/jpeg"} else downloads).append(parent_matches[0])
        return tuple((*result, *downloads))

    def _focused_presentation_refs(self, subject_refs):
        """Prioritize exact focused reports with uniquely bound evidence."""
        # Frozen/direct subjects are always read before any lineage expansion.
        result, controls = list(dict.fromkeys(subject_refs)), {}
        for subject_ref in subject_refs:
            try:
                report = self.artifacts.catalog(subject_ref)
            except _READ_ERRORS:
                continue
            operation = tuple(report.labels.get(key) for key in (
                "operation_id", "operation_version", "operation_digest"))
            if not all(operation):
                controls[report.artifact_id] = {
                    "presentation_manifest_match_count": 0,
                    "presentation_manifest_scan_complete": False,
                }
                continue
            manifests, scan_complete = [], True
            for manifest_ref in report.parent_refs:
                if manifest_ref.schema_id != "scidiscovery.tool-evidence-manifest.v1":
                    continue
                try:
                    manifest = self.artifacts.catalog(manifest_ref)
                except _READ_ERRORS:
                    scan_complete = False
                    continue
                labels = manifest.labels
                if (labels.get("operation_output_port") == "recovery_manifest_output"
                        and isinstance(labels.get("tool_producer_run"), str)
                        and tuple(labels.get(key) for key in (
                            "operation_id", "operation_version", "operation_digest")) == operation):
                    manifests.append((manifest_ref, manifest))
            controls[report.artifact_id] = {
                "presentation_manifest_match_count": len(manifests),
                "presentation_manifest_scan_complete": scan_complete,
            }
            if not scan_complete or len(manifests) != 1:
                continue
            manifest_ref, manifest = manifests[0]
            result.append(manifest_ref)
            result.extend(self._presentation_file_refs(report, manifest))
        return tuple(dict.fromkeys(result)), controls

    def _family_views(self, instance_id, artifact_id):
        envelope = self.artifacts.get_by_id(artifact_id)
        labels = envelope.labels
        fingerprint = labels.get("operation_invocation_fingerprint")
        if (not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or not labels.get("transform_profile")):
            return [], []
        records = self.bindings.invocation_artifacts(instance=instance_id, request_fingerprint=fingerprint)
        if len(records) > 100:
            return [], [gap("family_lookup_limit", artifact_id=artifact_id)]
        if not any(item.object_id == artifact_id for item in records):
            return [], [gap("family_binding_missing", artifact_id=artifact_id)]
        members, gaps = {}, []
        for record in records:
            try:
                candidate = self.artifacts.get_by_id(record.object_id)
                if candidate.parent_refs != envelope.parent_refs or any(
                        candidate.labels.get(key) != labels.get(key) for key in (
                            "transform_profile", "operation_invocation_fingerprint", "operation_id",
                            "operation_version", "operation_digest")):
                    gaps.append(gap("family_member_identity_mismatch", artifact_id=candidate.artifact_id))
                    continue
                label = candidate.labels.get("output_label")
                if not label or (label in members and members[label].ref != candidate.ref):
                    return [], [gap("family_output_ambiguous", artifact_id=artifact_id)]
                members[label] = candidate
            except _READ_ERRORS as error:
                gaps.append(self._read_gap(error, artifact_id=record.object_id))
        return [self._presentation_view(instance_id, item) for item in members.values()], gaps

    def parameter_context(self, instance_id, artifact_id, *, review=None):
        """Read one full bounded original, plus only parameter-source dependencies."""
        from .presentation import _provider_entries, entry_points
        ref = (self.approval_artifact_reference(instance_id, review, artifact_id) if review
               else self.artifact_reference(instance_id, artifact_id))
        envelope = self.artifacts.catalog(ref)
        if envelope.size_bytes > MAX_SOURCE_BYTES or envelope.content_encoding != "identity":
            return {"artifact": None, "dependencies": [], "gaps": [gap("parameter_source_read_limit")]}
        target = self._artifact_view(instance_id, envelope)
        try:
            target["payload"] = json.loads(self.artifacts.read(ref), parse_constant=_reject_constant)
            target["payload_state"], target["gaps"] = "available", []
        except (ValueError, UnicodeError, RecursionError, *_READ_ERRORS) as error:
            return {"artifact": None, "dependencies": [], "gaps": [self._read_gap(error)]}
        schemas = set()
        try:
            for entry in _provider_entries(entry_points):
                provider = entry.load()
                if envelope.schema_id in getattr(provider, "parameter_schemas", ()):
                    schemas.update(getattr(provider, "parameter_dependencies", ()))
        except Exception:
            return {"artifact": target, "dependencies": [], "gaps": [gap("parameter_provider_unavailable")]}
        if not schemas:
            return {"artifact": target, "dependencies": [], "gaps": []}
        queue, seen, dependencies, gaps = deque(envelope.parent_refs), {artifact_id}, [], []
        while queue and len(seen) < 100:
            parent = queue.popleft()
            if parent.artifact_id in seen:
                continue
            seen.add(parent.artifact_id)
            try:
                original = self.artifacts.catalog(parent)
                if original.schema_id in schemas:
                    view = self._presentation_view(instance_id, original)
                else:
                    view = {"artifact_id": original.artifact_id, "schema_id": original.schema_id,
                            "source": {"artifact_id": original.artifact_id, "json_pointer": ""},
                            "provenance": [{"artifact_id": p.artifact_id} for p in original.parent_refs[:100]],
                            "parent_count": len(original.parent_refs)}
                if json_size(dependencies) + json_size(view) > MAX_RESPONSE_BYTES:
                    gaps.append(gap("parameter_dependency_limit"))
                    break
                dependencies.append(view)
                queue.extend(original.parent_refs[:100])
                if len(original.parent_refs) > 100:
                    gaps.append(gap("parameter_dependency_limit"))
            except _READ_ERRORS as error:
                gaps.append(self._read_gap(error))
        if queue:
            gaps.append(gap("parameter_dependency_limit"))
        if gaps:
            # An incomplete ancestry cannot establish unique external provenance.
            for dependency in dependencies:
                if dependency.get("schema_id") in schemas:
                    dependency.setdefault("gaps", []).extend(gaps[:1])
        return {"artifact": target, "dependencies": dependencies, "gaps": gaps}

    def diagnostics(self, instance_id: str, reference: str, *, node_key: str,
                    offset: int = 0, max_bytes: int = 16384, section: str = "summary") -> dict[str, Any]:
        if (type(offset) is not int or offset < 0 or type(max_bytes) is not int
                or not 1 <= max_bytes <= 65536 or section not in {"summary", "traceback", "stdout", "stderr"}):
            raise ValueError("invalid engineering diagnostic page")
        if not isinstance(reference, str) or not re.fullmatch(r"diag_[0-9a-f]{32}", reference):
            raise ValueError("invalid engineering diagnostic reference")
        binding = self._binding(instance_id, node_key)
        if self.engineering_diagnostics is None:
            return {"gaps": [gap("engineering_diagnostics_service_unavailable")]}
        if binding.namespace == "run":
            self._run(instance_id, binding.object_id)
            reader = getattr(self.runs, "diagnostic_reference_belongs", None)
            if reader is None:
                return {"gaps": [gap("run_diagnostic_association_unavailable")]}
            if not reader(binding.object_id, reference):
                raise ReadModelScopeError("diagnostic does not belong to the selected Run")
        elif binding.namespace == "execution":
            records = (self.executions.observation(binding.object_id), self._collection(binding.object_id))
            if not any(_engineering_reference_in(record, reference) for record in records):
                raise ReadModelScopeError("diagnostic does not belong to the selected execution")
        else:
            raise ReadModelScopeError("engineering diagnostics require an exact Run or execution node")
        try:
            scopes = ("instance:" + instance_id,) + (("execution:" + binding.object_id,) if binding.namespace == "execution" else ())
            return self.engineering_diagnostics.read(reference, scopes=scopes,
                offset=offset, max_bytes=max_bytes, section=section)
        except FileNotFoundError:
            return {"gaps": [gap("engineering_diagnostic_missing")]}
        except ValueError as error:
            raise ReadModelScopeError("diagnostic scope, reference, or requested section is invalid") from error
        except OSError as error:
            return {"gaps": [gap("engineering_diagnostic_read_error", error_type=type(error).__name__)]}

    def artifact(self, instance_id: str, artifact_id: str, *, pointer: str | None = None,
                 child_after: int = 0, child_limit: int = 30) -> dict[str, Any]:
        if (pointer is not None and (not isinstance(pointer, str) or len(pointer) > 4096)
                or type(child_after) is not int or child_after < 0
                or type(child_limit) is not int or not 1 <= child_limit <= 100):
            raise ValueError("invalid artifact selection bounds")
        if pointer is not None:
            parse_json_pointer(pointer)
        envelope = self._authorized_artifact(instance_id, artifact_id)
        return self._artifact_view(instance_id, envelope, pointer=pointer,
                                   child_after=child_after, child_limit=child_limit)

    def artifact_reference(self, instance_id: str, artifact_id: str):
        """Authorize an exact original for the HTTP download handler."""
        return self._authorized_artifact(instance_id, artifact_id).ref

    def approval_context(self, instance_id: str, review: ApprovalReview, *, presentation=False) -> dict[str, Any]:
        """Read only the authenticated review's frozen cohort, never latest heads."""
        name = self._approval_scope(instance_id, review)
        priority_refs, controls = (self._focused_presentation_refs(
            review.request.subject_refs) if presentation else ((), {}))
        subjects, gaps = self._lineage_views(instance_id, review.request.subject_refs,
            presentation=presentation, priority_refs=priority_refs)
        for subject in subjects:
            if subject["artifact_id"] in controls:
                subject["family"].update(controls[subject["artifact_id"]])
        result = {"approval_key": "approval:" + name,
                "request_ref": review.request_ref.model_dump(mode="json"),
                "subject_refs": [self._ref(instance_id, ref, f"/subject_refs/{i}") for i, ref in enumerate(review.request.subject_refs)],
                "artifacts": subjects, "gaps": gaps,
                "scope": "frozen_subjects_and_envelope_provenance"}
        while json_size(result) > MAX_RESPONSE_BYTES and result["artifacts"]:
            removed = result["artifacts"].pop()
            result["gaps"].append(gap("context_byte_limit", artifact_id=removed["artifact_id"]))
        return result

    def _approval_scope(self, instance_id, review):
        self.bindings.get_instance(instance_id=instance_id)
        name = self.bindings.find_name(instance=instance_id, namespace="approval", object_id=review.request.approval_id)
        if name is None:
            raise ReadModelScopeError("approval is not bound to this instance")
        status = self.approvals.status(review.request.approval_id)
        if status.approval_request_ref != review.request_ref:
            raise ReadModelScopeError("approval request identity differs")
        request_envelope = self.artifacts.catalog(review.request_ref)
        if request_envelope.parent_refs != (*review.request.subject_refs, review.request.review_manifest_ref):
            raise ReadModelScopeError("approval subjects differ from the frozen request provenance")
        return name

    def approval_artifact_reference(self, instance_id, review, artifact_id):
        """An approval token reaches only its frozen subjects and their provenance."""
        self._approval_scope(instance_id, review)
        queue, seen = deque(review.request.subject_refs), set()
        while queue and len(seen) < MAX_SCOPE_RECORDS:
            ref = queue.popleft()
            if ref in seen:
                continue
            seen.add(ref)
            envelope = self.artifacts.catalog(ref)
            if ref.artifact_id == artifact_id:
                return ref
            queue.extend(envelope.parent_refs[:max(0, MAX_SCOPE_RECORDS - len(seen) - len(queue))])
            if envelope.supersedes_ref:
                queue.append(envelope.supersedes_ref)
        raise ReadModelScopeError("artifact is outside this frozen approval context or its read bound")

    def approval_artifact(self, instance_id, review, artifact_id, *, pointer=None,
                          child_after=0, child_limit=30):
        if pointer is not None:
            if len(pointer) > 4096:
                raise ValueError("artifact pointer is too long")
            parse_json_pointer(pointer)
        if not 0 <= child_after or not 1 <= child_limit <= 100:
            raise ValueError("invalid artifact page bounds")
        ref = self.approval_artifact_reference(instance_id, review, artifact_id)
        return self._artifact_view(instance_id, self.artifacts.catalog(ref), pointer=pointer,
                                   child_after=child_after, child_limit=child_limit)

    def _binding(self, instance_id, key):
        self.bindings.get_instance(instance_id=instance_id)
        if not isinstance(key, str) or ":" not in key:
            raise ReadModelNotFound("invalid node key")
        namespace, name = key.split(":", 1)
        if namespace not in _KINDS:
            raise ReadModelNotFound("unknown node namespace")
        try:
            return self.bindings.get_binding(instance=instance_id, namespace=namespace, name=name)
        except SchedulerNameNotFound as error:
            raise ReadModelNotFound("node is not bound to this instance") from error

    def _run(self, instance_id, run_id):
        value = self.runs.status(run_id)
        if value.instance_id != instance_id:
            raise ReadModelScopeError("Run ownership differs from its semantic binding")
        return value

    def _metadata(self, instance_id, binding):
        result = {"key": f"{binding.namespace}:{binding.name}", "kind": binding.namespace,
                  "name": binding.name, "created_at": binding.created_at, "state": "unknown",
                  "title": binding.name, "inputs": [], "outputs": [],
                  "source_time": binding.created_at, "last_activity_at": None,
                  "completed_at": None, "collection_status": None}
        try:
            roots = []
            if binding.namespace == "artifact":
                value = self.artifacts.get_by_id(binding.object_id)
                result.update(state="registered", schema_id=value.schema_id, size_bytes=value.size_bytes)
                result.update(source_time=value.created_at, producer=self._producer(value),
                    recorded_scientific_claim_admissible=value.labels.get("scientific_claim_admissible"),
                    current_selection=list(self.bindings.selection_records(instance=instance_id, artifact_ref=value.ref)))
                roots = [(ref, f"/parent_refs/{i}") for i, ref in enumerate(value.parent_refs[:1])]
                result["input_count"] = len(value.parent_refs)
            elif binding.namespace == "run":
                value = self._run(instance_id, binding.object_id)
                result.update(state=value.state, operation_id=value.operation_id,
                              execution_profile=getattr(value, "execution_profile", None))
                result.update(last_activity_at=value.last_activity_at, completed_at=value.completed_at,
                              source_time=value.completed_at or value.last_activity_at or value.started_at or value.created_at)
                roots = [(item.artifact_ref, f"/inputs/{i}/artifact_ref") for i, item in enumerate(value.inputs[:1])]
                result["input_count"] = len(value.inputs)
                if value.output_ref:
                    if self.presentation_summaries is not None:
                        result["display_copy"] = self.presentation_summaries.read(instance_id, value.output_ref) or {
                            "state": "not_generated", "items": []}
                    result["outputs"] = [self._ref(instance_id, value.output_ref, "/output_ref")]
                    output = self.artifacts.catalog(value.output_ref)
                    result["recorded_scientific_claim_admissible"] = output.labels.get("scientific_claim_admissible")
                    result["current_selection"] = list(self.bindings.selection_records(instance=instance_id, artifact_ref=value.output_ref))
            elif binding.namespace == "approval":
                value = self.approvals.status(binding.object_id)
                result["state"] = value.status
                result["approval_summary"] = self.approvals.navigation_summary(binding.object_id)
                if value.decision_ref:
                    result["source_time"] = self.artifacts.catalog(value.decision_ref).created_at
                roots = [(value.approval_request_ref, "/approval_request_ref")]
                if value.decision_ref:
                    result["outputs"] = [self._ref(instance_id, value.decision_ref, "/decision_ref")]
            else:
                value = self.executions.status(binding.object_id)
                result.update(state=value.state, executor=value.executor)
                authorization = getattr(value, "authorization", {})
                result["execution_authorization"] = {key: authorization[key] for key in
                    ("budget", "allowance", "per_execution_allowance", "budget_usage", "accounting", "reason") if key in authorization}
                collection = self._collection(binding.object_id)
                observation = self.executions.observation(binding.object_id)
                from ...plugin_runtime.experiment import progress_summary
                result["execution_progress"] = {
                    "observed_at": observation.get("status_observed_at"),
                    "progress_observed_at": observation.get("progress_observed_at"),
                    "refresh_failed": bool(observation.get("observation_error")),
                    "progress": progress_summary(observation.get("progress", {}), include_log_text=True),
                }
                result["collection_progress"] = {key: collection[key] for key in
                    ("completed_bytes", "total_bytes", "completed_files", "total_files", "phase", "archive_bytes") if key in collection}
                result["collection_status"] = collection.get("state", "unknown")
                result["collection_updated_at"] = collection.get("updated_at")
                result["source_time"] = collection.get("updated_at") or observation.get("status_observed_at")
                if value.result_ref:
                    result["outputs"] = [self._ref(instance_id, value.result_ref, "/result_ref")]
            result["inputs"] = [self._ref(instance_id, ref, pointer) for ref, pointer in roots]
        except _READ_ERRORS as error:
            result["gaps"] = [self._read_gap(error)]
        return result

    @staticmethod
    def _producer(envelope):
        labels = envelope.labels
        if not labels.get("operation_id"):
            return None
        return {"kind": "transform" if labels.get("transform_profile") == labels.get("operation_id") else "operation",
                **{key: labels[key] for key in ("operation_id", "operation_version", "operation_digest", "operation_output_port", "output_label", "operation_invocation_fingerprint") if key in labels}}

    def _collection(self, execution_id):
        if self.execution_collection is None:
            return {"state": "unknown", "gaps": [gap("collection_service_unavailable")]}
        try:
            return self.execution_collection.summary(execution_id)
        except Exception as error:
            return {"state": "unknown", "gaps": [gap("collection_observation_unavailable", error_type=type(error).__name__)]}

    def _run_detail(self, result, value):
        if value.operation_id == "science.experiment.v1":
            result["scientific_result_semantics"] = {"scheduler_verdict": "experiment_delivery_only",
                "scientific_acceptance": "not_assessed", "reason": "legacy_report_has_no_structured_scientific_result"}
            from .experiment_progress import read_progress
            result["experiment_progress"] = read_progress(self, value, detail=True)
        from ...plugin_runtime.observation import read_summary
        try:
            result["native_execution"] = read_summary(self.runs.backend.open(value.run_id).root)
        except Exception as error:
            result["native_execution"] = {"coverage": "unobserved", "scientific_evidence": False,
                "reason": "workspace_observation_unavailable", "error_type": type(error).__name__}
        try:
            result["recovery"] = self.runs.recovery_status(value)
        except Exception as error:
            result["recovery"] = {"draft_available": None, "recovery_pending": None,
                "gaps": [gap("recovery_observation_unavailable", error_type=type(error).__name__)]}
        try:
            result["tool_timing"] = self.runs.tool_timing(value.run_id)
        except Exception as error:
            result["tool_timing"] = []
            result["gaps"].append(gap("tool_timing_unavailable", error_type=type(error).__name__))

    def _relationships(self, instance_id, binding, roots):
        result = {"predecessors": [], "successors": [], "matching_reviews": [], "has_more": False, "gaps": []}
        targets = []
        if binding.namespace == "run":
            value = self._run(instance_id, binding.object_id)
            prior = [(item.artifact_ref, "input", f"/inputs/{i}/artifact_ref") for i, item in enumerate(value.inputs)]
            targets = [] if value.output_ref is None else [value.output_ref]
            recovery_reader = getattr(self.runs, "recovery_links", None)
            if recovery_reader is not None:
                for field, parent_id in recovery_reader(value.run_id).items():
                    if parent_id:
                        self._run(instance_id, parent_id)
                        link = self._run_link(instance_id, parent_id, field.removesuffix("_run_id"), "/" + field)
                        if link:
                            result["predecessors"].append(link)
            else:
                result["gaps"].append(gap("recovery_parent_lookup_unavailable"))
        elif binding.namespace == "artifact":
            envelope = self.artifacts.catalog(roots[0])
            prior = [(ref, "parent", f"/parent_refs/{i}") for i, ref in enumerate(envelope.parent_refs)]
            if envelope.supersedes_ref:
                prior.append((envelope.supersedes_ref, "supersedes", "/supersedes_ref"))
            targets = [envelope.ref]
        else:
            prior = [(ref, "control_reference", "") for ref in roots]
            targets = list(roots)
        result["has_more"] = len(prior) > 50
        for ref, relation, path in prior[:50]:
            result["predecessors"].append(self._artifact_link(instance_id, ref, relation, path))
        related_reader = getattr(self.runs, "related_runs", None)
        child_reader = getattr(self.artifacts.registry, "linked_children", None)
        for ref in targets[:8]:
            envelope = self.artifacts.catalog(ref)
            review_edge, review_state = self._recorded_review_edge(envelope)
            if review_state not in {"available", "not_declared"}:
                result["gaps"].append(gap("matching_review_" + review_state, artifact_id=ref.artifact_id))
            if related_reader is not None:
                producers = related_reader(instance_id=instance_id, artifact_ref=ref, relation="output", limit=2)
                for producer in producers:
                    if producer.run_id != binding.object_id:
                        link = self._run_link(instance_id, producer.run_id, "produced", "/output_ref")
                        if link:
                            result["predecessors"].append(link)
                followers = related_reader(instance_id=instance_id, artifact_ref=ref, relation="input", limit=51)
                result["has_more"] |= len(followers) > 50
                for follower in followers[:50]:
                    link = self._run_link(instance_id, follower.run_id, "consumes", "/inputs")
                    if not link:
                        continue
                    result["successors"].append(link)
                    if (review_edge is not None and follower.state == "completed" and follower.output_ref is not None
                            and follower.operation_id == review_edge.reviewer_operation
                            and any(item.port_name == review_edge.reviewer_input_port and item.artifact_ref == ref for item in follower.inputs)):
                        result["matching_reviews"].append({"key": link["key"],
                            "subject_refs": [self._ref(instance_id, ref, "/inputs")],
                            "output_refs": [self._ref(instance_id, follower.output_ref, "/output_ref")],
                            "recorded_signal": follower.signal.model_dump(mode="json") if follower.signal else None})
            if child_reader is not None:
                children = child_reader(ref, limit=51)
                result["has_more"] |= len(children) > 50
                for child in children[:50]:
                    # Reverse provenance cannot enlarge instance authorization.
                    name = self.bindings.find_name(instance=instance_id, namespace="artifact", object_id=child.artifact_id)
                    if name is None:
                        continue
                    relation = "superseded_by" if child.supersedes_ref == ref else "parent_of"
                    result["successors"].append(self._artifact_link(instance_id, child.ref, relation, "/parent_refs"))
        for key in ("predecessors", "successors", "matching_reviews"):
            unique = {json.dumps(item, sort_keys=True): item for item in result[key]}
            result[key] = list(unique.values())[:50]
            result["has_more"] |= len(unique) > 50
        if related_reader is None:
            result["gaps"].append(gap("run_relationship_lookup_unavailable"))
        return result

    def _recorded_review_edge(self, envelope):
        labels = envelope.labels
        if not labels.get("operation_id") or not labels.get("operation_digest"):
            return None, "producer_metadata_not_recorded"
        if not self.operation_catalog:
            return None, "contract_unavailable"
        try:
            compiled = self.operation_catalog.recorded_operation(labels["operation_id"],
                version=labels.get("operation_version"), digest=labels["operation_digest"])
            edge = compiled.spec.review
            return edge, "available" if edge and edge.reviewer_operation else "not_declared"
        except (KeyError, AttributeError):
            return None, "contract_unavailable"

    def _artifact_link(self, instance_id, ref, relation, path):
        name = self.bindings.find_name(instance=instance_id, namespace="artifact", object_id=ref.artifact_id)
        return {"key": "artifact:" + name if name else None, "name": name, "kind": "artifact",
            "relation": relation, "artifact_ref": ref.model_dump(mode="json"), "source_pointer": path}

    def _run_link(self, instance_id, run_id, relation, path):
        name = self.bindings.find_name(instance=instance_id, namespace="run", object_id=run_id)
        if name is None:
            return None
        return {"key": "run:" + name, "name": name, "kind": "run", "relation": relation, "source_pointer": path}

    def _objective_navigation(self, instance_id, binding, roots):
        if binding.namespace == "run":
            inputs = self._run(instance_id, binding.object_id).inputs
            explicit = [item.artifact_ref for item in inputs if item.port_name in {"objective", "research_objective"}]
            if explicit:
                return [self._ref(instance_id, ref, "/inputs") for ref in explicit], []
            plans = [item.artifact_ref for item in inputs if item.port_name == "experiment_plan"]
            if plans:
                roots = tuple(plans)
        found, queue, seen, gaps = {}, deque(roots), set(), []
        while queue and len(seen) < 128:
            ref = queue.popleft()
            if ref in seen:
                continue
            seen.add(ref)
            try:
                envelope = self.artifacts.catalog(ref)
                if envelope.schema_id == "scidiscovery.research-objective.v1":
                    found[ref.artifact_id] = self._ref(instance_id, ref, "")
                    continue
                # Exact producer ports distinguish a prior plan from same-schema
                # background. This reads old bindings, not the current catalog.
                producer_reader = getattr(self.runs, "related_runs", None)
                producers = (producer_reader(instance_id=instance_id, artifact_ref=ref,
                    relation="output", limit=2) if producer_reader else ())
                if len(producers) == 1:
                    inputs = producers[0].inputs
                    explicit = tuple(item.artifact_ref for item in inputs if item.port_name in {"objective", "research_objective"})
                    prior = tuple(item.artifact_ref for item in inputs if item.port_name == "experiment_plan")
                    if explicit or prior:
                        queue.extend((explicit or prior)[:max(0, 128 - len(seen) - len(queue))])
                        continue
                parents = envelope.parent_refs
                if envelope.schema_id == "scidiscovery.experiment-portfolio.v1":
                    metadata = [(parent, self.artifacts.catalog(parent)) for parent in parents[:100]]
                    intents = [parent for parent, info in metadata if info.schema_id == "scidiscovery.experiment-design-intent.v1"]
                    original_plans = [parent for parent, info in metadata if info.schema_id == envelope.schema_id]
                    objectives = [parent for parent, info in metadata if info.schema_id == "scidiscovery.research-objective.v1"]
                    # Revisions retain the original plan chain even when a newer
                    # objective-shaped feedback artifact is another direct input.
                    parents = tuple(original_plans or objectives or intents or parents)
                    if len(original_plans) > 1:
                        gaps.append(gap("original_plan_parent_ambiguous", artifact_id=ref.artifact_id))
                queue.extend(parents[:max(0, 128 - len(seen) - len(queue))])
                if envelope.supersedes_ref and not parents:
                    queue.append(envelope.supersedes_ref)
            except _READ_ERRORS as error:
                gaps.append(self._read_gap(error, artifact_id=ref.artifact_id))
        if queue:
            gaps.append(gap("objective_lineage_read_limit"))
        if not found:
            gaps.append(gap("original_objective_not_recorded"))
        elif len(found) > 1:
            gaps.append(gap("original_objective_ambiguous"))
        return list(found.values()), gaps

    def _ref(self, instance_id, ref, pointer, **extra):
        name = self.bindings.find_name(instance=instance_id, namespace="artifact", object_id=ref.artifact_id)
        return {"artifact_id": ref.artifact_id, "ref": ref.model_dump(mode="json"),
                "key": None if name is None else "artifact:" + name,
                "source_pointer": pointer, **extra}

    def _roots(self, instance_id, binding):
        if binding.namespace == "artifact":
            return (self.artifacts.get_by_id(binding.object_id).ref,)
        if binding.namespace == "run":
            value = self._run(instance_id, binding.object_id)
            return tuple(item.artifact_ref for item in value.inputs) + (() if value.output_ref is None else (value.output_ref,))
        if binding.namespace == "approval":
            value = self.approvals.status(binding.object_id)
            return (value.approval_request_ref,) + (() if value.decision_ref is None else (value.decision_ref,))
        return tuple(ref for ref in self.executions.record_references(binding.object_id).values() if ref)

    def _authorized_artifact(self, instance_id, artifact_id):
        self.bindings.get_instance(instance_id=instance_id)
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ReadModelNotFound("invalid artifact identity")
        direct = self.bindings.find_name(instance=instance_id, namespace="artifact", object_id=artifact_id)
        if direct is not None:
            try:
                return self.artifacts.get_by_id(artifact_id)
            except ArtifactNotFoundError as error:
                raise ReadModelNotFound("bound artifact metadata is missing") from error
        seen, after, examined = set(), None, 0
        for _ in range(10):
            page = self.bindings.binding_page(instance=instance_id, after=after, limit=100)
            for binding in page:
                try:
                    queue = deque(self._roots(instance_id, binding))
                except _READ_ERRORS:
                    continue
                while queue and examined < MAX_SCOPE_RECORDS:
                    ref = queue.popleft()
                    identity = (ref.artifact_id, ref.sha256, ref.kind, ref.schema_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    examined += 1
                    try:
                        envelope = self.artifacts.catalog(ref)
                    except _READ_ERRORS:
                        continue
                    if ref.artifact_id == artifact_id:
                        return envelope
                    queue.extend(envelope.parent_refs[:max(0, MAX_SCOPE_RECORDS - examined - len(queue))])
                    if envelope.supersedes_ref:
                        queue.append(envelope.supersedes_ref)
                if examined >= MAX_SCOPE_RECORDS:
                    raise ReadModelScopeError("artifact scope could not be established within the lineage read bound")
            if len(page) < 100:
                raise ReadModelScopeError("artifact is outside this instance's recorded scope")
            after = self._position(page[-1])
        raise ReadModelScopeError("artifact scope could not be established within the binding read bound")

    def _artifact_view(self, instance_id, envelope, *, pointer=None, child_after=0, child_limit=30):
        ref = envelope.ref
        result = {"artifact_id": ref.artifact_id, "ref": ref.model_dump(mode="json"),
                  "schema_id": envelope.schema_id, "size_bytes": envelope.size_bytes,
                  "family": {key: envelope.labels[key] for key in (
                      "operation_invocation_fingerprint", "transform_profile", "operation_id",
                      "operation_version", "operation_digest", "output_label", "operation_output_port",
                      "tool_producer_run")
                      if key in envelope.labels},
                  "media_type": envelope.media_type,
                  "source": {"artifact_id": ref.artifact_id, "json_pointer": pointer or ""},
                  "original": {"artifact_id": ref.artifact_id, "ref": ref.model_dump(mode="json")},
                  "provenance": [self._ref(instance_id, parent, f"/parent_refs/{i}") for i, parent in enumerate(envelope.parent_refs[:100])],
                  "parent_count": len(envelope.parent_refs),
                  "supersedes": None if envelope.supersedes_ref is None else self._ref(instance_id, envelope.supersedes_ref, "/supersedes_ref"),
                  "payload_state": "unknown", "gaps": []}
        if len(envelope.parent_refs) > 100:
            result["gaps"].append(gap("provenance_page_limit", parent_count=len(envelope.parent_refs)))
        source_limit = MAX_PAYLOAD_BYTES if pointer is None else MAX_SOURCE_BYTES
        if envelope.size_bytes > source_limit:
            result.update(payload_state="too_large")
            result["gaps"].append(gap("payload_too_large" if pointer is None else "source_read_limit", size_bytes=envelope.size_bytes))
            return result
        media = envelope.media_type.split(";", 1)[0].lower()
        if envelope.content_encoding != "identity" or not (media == "application/json" or media.endswith("+json") or media.startswith("text/")):
            result.update(payload_state="metadata_only")
            result["gaps"].append(gap("payload_preview_unavailable"))
            return result
        try:
            raw = self.artifacts.read(ref)
            if media == "application/json" or media.endswith("+json"):
                payload = json.loads(raw, parse_constant=_reject_constant)
            else:
                payload = raw.decode("utf-8")
            bounded = bounded_record(payload) if pointer is None else select_json(payload, pointer, after=child_after, limit=child_limit)
            result["gaps"].extend(bounded.pop("gaps"))
            result.update(bounded)
        except (ValueError, UnicodeError, RecursionError):
            result.update(payload_state="invalid")
            result["gaps"].append(gap("payload_not_decodable"))
        except _READ_ERRORS as error:
            result.update(payload_state="missing" if isinstance(error, CASObjectMissingError) else "error")
            result["gaps"].append(self._read_gap(error))
        return result

    def _presentation_view(self, instance_id, envelope):
        """Read declared display fields without copying full project source into the page."""
        from .presentation import presentation_pointers
        paths = presentation_pointers(envelope.schema_id)
        view = self._artifact_view(instance_id, envelope)
        if paths and len(envelope.parent_refs) > 100:
            # Parent identity verification is independent of graph traversal.
            # Keep the complete immutable edges when they fit the existing byte
            # budget; unrelated ancestors still use the bounded lineage queue.
            parents, used = [], 2  # JSON list delimiters.
            for i, parent in enumerate(envelope.parent_refs):
                item = {"artifact_id": parent.artifact_id, "ref": parent.model_dump(mode="json"),
                    "source_pointer": f"/parent_refs/{i}"}
                size = json_size(item) + (2 if parents else 0)
                if used + size > MAX_PAYLOAD_BYTES:
                    break
                parents.append(item)
                used += size
            view["provenance"] = parents
            if len(parents) == len(envelope.parent_refs):
                view["gaps"] = [item for item in view["gaps"] if item["code"] != "provenance_page_limit"]
        if not paths or envelope.size_bytes > MAX_SOURCE_BYTES or envelope.content_encoding != "identity":
            return view
        try:
            original = (view["payload"] if view["payload_state"] == "available"
                        else json.loads(self.artifacts.read(envelope.ref), parse_constant=_reject_constant))
        except (ValueError, UnicodeError, RecursionError, *_READ_ERRORS):
            return view
        if envelope.schema_id == "scidiscovery.tool-evidence-manifest.v1":
            from .attachment_presentation import presentation_file_manifest
            compact = presentation_file_manifest(original)
            if compact is not None and json_size(compact) <= MAX_PAYLOAD_BYTES:
                view.update(payload_state="available", payload=compact, display_projection=True)
                view["gaps"] = [item for item in view["gaps"] if item["code"] != "payload_too_large"]
                return view
        selected = {}
        used = 0
        for pattern in paths:
            for tokens, value in _display_selections(original, parse_json_pointer(pattern)):
                used += 1
                pointer = "/" + "/".join(str(token).replace("~", "~0").replace("/", "~1") for token in tokens)
                if used > 512:
                    view["gaps"].append(gap("display_selection_limit", source_pointer=pointer))
                    break
                # A prefix keeps original indexes/names and is explicitly partial.
                if json_size(value) > 24 * 1024:
                    if isinstance(value, (list, dict)):
                        prefix = [] if isinstance(value, list) else {}
                        entries = enumerate(value) if isinstance(value, list) else value.items()
                        for key, item in entries:
                            candidate = prefix + [item] if isinstance(prefix, list) else {**prefix, key: item}
                            if json_size(candidate) > 24 * 1024:
                                break
                            prefix = candidate
                        value = prefix
                    else:
                        view["gaps"].append(gap("display_field_too_large", source_pointer=pointer))
                        continue
                    view["gaps"].append(gap("display_field_partial", source_pointer=pointer,
                                            original={"artifact_id": envelope.artifact_id, "json_pointer": pointer}))
                candidate = json.loads(json.dumps(selected))
                _put_selection(candidate, tokens, value)
                if json_size(candidate) > MAX_PAYLOAD_BYTES:
                    view["gaps"].append(gap("display_artifact_byte_limit", source_pointer=pointer))
                    continue
                selected = candidate
            if used > 512:
                break
        view.update(payload_state="available", payload=selected, display_projection=True)
        view["gaps"] = [item for item in view["gaps"] if item["code"] != "payload_too_large"]
        return view

    def _lineage_views(self, instance_id, refs, *, presentation=False, priority_refs=()):
        priority = deque((ref, None) for ref in priority_refs)
        queue = deque((ref, None) for ref in refs)
        seen, views, gaps, budget = set(), [], [], 0
        while (priority or queue) and len(seen) < 100:
            current = priority if priority else queue
            ref, origin = current.popleft()
            identity = (ref.artifact_id, ref.sha256, ref.kind, ref.schema_id)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                envelope = self.artifacts.catalog(ref)
                view = (self._presentation_view(instance_id, envelope) if presentation
                        else self._artifact_view(instance_id, envelope))
                view["reached_from"] = origin
                budget += json_size(view)
                if budget > MAX_RESPONSE_BYTES - 64 * 1024:
                    gaps.append(gap("context_byte_limit", artifact_id=ref.artifact_id))
                    break
                views.append(view)
                current.extend((parent, {"artifact_id": ref.artifact_id, "json_pointer": f"/parent_refs/{i}"}) for i, parent in enumerate(envelope.parent_refs[:100]))
                if len(envelope.parent_refs) > 100:
                    gaps.append(gap("lineage_branch_limit", artifact_id=ref.artifact_id))
                if envelope.supersedes_ref:
                    current.append((envelope.supersedes_ref, {"artifact_id": ref.artifact_id, "json_pointer": "/supersedes_ref"}))
            except _READ_ERRORS as error:
                gaps.append(self._read_gap(error, artifact_id=ref.artifact_id, reached_from=origin))
        if priority or queue:
            gaps.append(gap("lineage_read_limit"))
        return views, gaps

    @staticmethod
    def _run_record(value):
        # Workspace drafts and tokens are not a scientific result or UI payload.
        result = {key: getattr(value, key) for key in (
            "run_id", "instance_id", "operation_id", "operation_version", "operation_digest",
            "state", "reason", "created_at", "started_at", "deadline_at", "completed_at", "last_activity_at", "draft_from_run_id")}
        result["recovery"] = {"recorded": value.recovery_draft is not None,
                              "recovery_pending": (value.recovery_draft or {}).get("recovery_pending"),
                              "draft_available": None,
                              "availability_state": "not_revalidated"}
        return result

    @staticmethod
    def _execution_record(value):
        result = asdict(value)
        result["result_ref"] = None if value.result_ref is None else value.result_ref.model_dump(mode="json")
        return result

    @staticmethod
    def _read_gap(error, **details):
        missing = isinstance(error, (ArtifactNotFoundError, CASObjectMissingError))
        return gap("source_missing" if missing else "source_read_error", error_type=type(error).__name__, **details)

    @staticmethod
    def _bounded_node(result):
        if json_size(result) <= MAX_RESPONSE_BYTES:
            return result
        reduced = ({key: value for key, value in result.items() if key in {
            "key", "kind", "name", "created_at", "state", "title", "source", "operation_id", "original", "outputs", "sealed_stages",
            "experiment_progress", "experiment_children"}}
            | {"payload_state": "too_large", "gaps": [gap("node_byte_limit")]})
        diagnostics = result.get("diagnostics")
        if isinstance(diagnostics, dict):
            # Preserve exact errors and their continuation cursor before reducing
            # optional node summaries. A large error history must remain readable.
            page = {**diagnostics, "events": []}
            reduced["diagnostics"] = page
            used = json_size(reduced)
            events = diagnostics.get("events", [])
            for event in events:
                size = json_size(event) + 2
                if used + size + 128 > MAX_RESPONSE_BYTES:
                    break
                page["events"].append(event)
                used += size
            if events and not page["events"]:
                # Legacy records may predate current per-detail bounds. Keep the
                # exact event and its scoped log reference visible and advance,
                # rather than making all subsequent errors unreachable.
                event = events[0]
                original = event.get("diagnostic") or {}
                diagnostic = {"payload_state": "too_large", "code": "diagnostic_event_byte_limit",
                              "original_event_id": event["event_id"]}
                engineering = original.get("engineering")
                if isinstance(engineering, dict) and json_size(engineering) <= 4096:
                    diagnostic["engineering"] = engineering
                else:
                    diagnostic["log_reference_state"] = "not_available_in_bounded_event"
                page["events"] = [{key: event[key] for key in ("event_id", "recorded_at", "activity") if key in event}
                                  | {"diagnostic": diagnostic}]
            if len(page["events"]) < len(events) and page["events"]:
                page["next_after"] = page["events"][-1]["event_id"]
        return reduced

    @staticmethod
    def _position(binding):
        return binding.created_at, binding.namespace, binding.name

    @classmethod
    def _cursor(cls, instance_id, binding):
        raw = json.dumps([instance_id, *cls._position(binding)], separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    @staticmethod
    def _decode_cursor(instance_id, cursor):
        if cursor is None:
            return None
        if not isinstance(cursor, str) or not cursor or len(cursor) > 2048:
            raise ValueError("invalid node cursor")
        try:
            value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        except (ValueError, UnicodeError) as error:
            raise ValueError("invalid node cursor") from error
        if (not isinstance(value, list) or len(value) != 4 or value[0] != instance_id
                or any(not isinstance(item, str) for item in value)):
            raise ValueError("node cursor belongs to another instance or is invalid")
        return tuple(value[1:])


def _reject_constant(value):
    raise ValueError("non-finite JSON number: " + value)


def _engineering_reference_in(record, reference):
    """Inspect control-owned engineering facts, never scientific JSON or logs."""
    if not isinstance(record, dict):
        return False
    for key in ("observation_error", "error", "progress_error", "record_error", "stop_record_error"):
        value = record.get(key)
        if isinstance(value, dict) and (value.get("reference") == reference or
                isinstance(value.get("engineering"), dict) and value["engineering"].get("reference") == reference):
            return True
    return False


def _display_selections(value, tokens, prefix=()):
    """Expand UI provider array selectors into exact original positions."""
    if not tokens:
        yield prefix, value
        return
    token, *rest = tokens
    if token == "*" and isinstance(value, list):
        for index, child in enumerate(value[:128]):
            yield from _display_selections(child, rest, (*prefix, index))
    else:
        try:
            key = int(token) if isinstance(value, list) else token
            child = value[key]
        except (ValueError, KeyError, IndexError, TypeError):
            return
        yield from _display_selections(child, rest, (*prefix, key))


def _put_selection(target, tokens, value):
    for position, key in enumerate(tokens):
        final = position == len(tokens) - 1
        if isinstance(target, list):
            while len(target) <= key:
                target.append({})
            if final:
                target[key] = value
                return
            if isinstance(tokens[position + 1], int) and not isinstance(target[key], list):
                target[key] = []
            target = target[key]
        else:
            if final:
                target[key] = value
                return
            target = target.setdefault(key, [] if isinstance(tokens[position + 1], int) else {})
