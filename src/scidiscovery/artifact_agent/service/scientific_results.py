"""Exact selected scientific syntheses; discovery never replaces the selection."""
from __future__ import annotations

import base64
import json

from ...general_science_results import RESULT_KIND, SCHEMA_ID, ScientificResult, comparison_projection
from ..schema.common import canonical_json

MAX_RESULT_BYTES = 64 * 1024


class ScientificResultError(ValueError):
    pass


def read_sealed_result(*, artifacts, runs, instance_id, reference):
    envelope = artifacts.verify(reference)
    if envelope.kind != RESULT_KIND or envelope.schema_id != SCHEMA_ID or envelope.size_bytes > MAX_RESULT_BYTES:
        raise ScientificResultError("Select a bounded scientific-result.v1 synthesis.")
    run = runs.completed_for_output(reference)
    if (run is None or run.instance_id != instance_id or run.state != "completed"
            or run.output_ref != reference or run.operation_id != "science.results.compare.v1"):
        raise ScientificResultError("Scientific results require their exact completed synthesis Run in this instance.")
    payload = ScientificResult.model_validate_json(artifacts.read(reference), strict=True).model_dump(mode="json")
    # Resolve aliases through immutable Run/receipt bindings. JSON itself grants
    # neither scope nor identity, and the current compiled catalog is irrelevant.
    aliases = {item.source_name for item in run.inputs}
    records = runs.tool_evidence(run.run_id)
    aliases.update(item["alias"] for item in records)
    descriptors = {alias: runs.source_descriptor(run, alias) for alias in aliases}
    needed = {item.source_name for item in descriptors.values() if item.port_name == "evaluation_contract"}
    needed.update(metric["source_key"] for item in payload["candidates"] for metric in item["metrics"])
    needed.update(payload["analyses"])
    needed.update(item.source_name for item in descriptors.values() if item.port_name in {"candidate_origins", "candidate_materials"})
    needed.update(item.source_name for item in descriptors.values()
        if item.port_name == "candidates" and item.artifact_ref.schema_id in {SCHEMA_ID, "scidiscovery.experiment-report.v1"})
    contents = {}
    for alias in needed:
        descriptor = descriptors.get(alias)
        if descriptor is None or descriptor.size_bytes > 32 * 1024 * 1024:
            raise ScientificResultError("Scientific metric or contract evidence is unavailable or exceeds its bound.")
        contents[alias] = artifacts.read(descriptor.artifact_ref)
    projected = comparison_projection(payload, contents, descriptors, lambda alias: runs.sealed_material_record(run, alias))
    if canonical_json(projected) != canonical_json(payload):
        raise ScientificResultError("Sealed result disagrees with its exact evidence/contract projection.")
    return run, payload, descriptors


def _cursor(reference, position):
    return base64.urlsafe_b64encode(canonical_json({"result": reference.artifact_id, "after": position})).decode().rstrip("=")


def current_result(*, artifacts, runs, bindings, instance_id, limit=30, before=None):
    """Bounded scientific view, including explicit coverage for change detection."""
    if type(limit) is not int or not 1 <= limit <= 30:
        raise ScientificResultError("Scientific-result change limit must be between 1 and 30.")
    selections = bindings.scientific_selections(instance=instance_id, limit=101)
    selection = next((item for item in selections if item.kind == RESULT_KIND), None)
    if selection is None or selection.artifact_ref is None:
        return {"state": "unorganized", "reason": "no_exact_scientific_result_selection",
            "scientific_acceptance": "not_assessed", "selection_lookup_complete": len(selections) < 101}
    name = bindings.find_name(instance=instance_id, namespace="artifact", object_id=selection.artifact_ref.artifact_id)
    if name is None:
        return {"state": "unavailable", "reason": "selected_scientific_result_binding_missing"}
    run, payload, descriptors = read_sealed_result(artifacts=artifacts, runs=runs,
        instance_id=instance_id, reference=selection.artifact_ref)
    report = {**payload, "artifact_name": name, "key": "artifact:" + name, "selected_at": selection.selected_at,
        "state": "selected", "scope": "included_candidates_only", "scheduler_signal_semantics": "synthesis_delivery_only",
        "run_name": bindings.find_name(instance=instance_id, namespace="run", object_id=run.run_id)}
    # Semantic names and source pointers are public. Ref/hash/path assembly stays
    # in control; UI receives image identities only for its scoped evidence route.
    for candidate in report["candidates"]:
        descriptor = descriptors[candidate["candidate"]]
        candidate["artifact_name"] = bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptor.artifact_ref.artifact_id)
        candidate["analysis_artifact_name"] = bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptors[candidate["analysis_reference"]].artifact_ref.artifact_id)
        candidate["method_names"] = [bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptors[alias].artifact_ref.artifact_id) for alias in candidate["method_evidence"]]
        candidate["observation_names"] = [bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptors[alias].artifact_ref.artifact_id) for alias in candidate["observation_evidence"]]
        if descriptor.artifact_ref.schema_id == "scidiscovery.experiment-report.v1" and descriptor.producer_run_id:
            original_run = runs.completed_for_output(descriptor.artifact_ref)
            original_report = json.loads(artifacts.read(descriptor.artifact_ref))
            if original_run and original_run.instance_id == instance_id:
                adopted = set(original_report.get("adopted_stages", ()))
                origins = [record for record in runs.tool_evidence(original_run.run_id) if record["alias"] in adopted]
                for field, kind in (("method_names", "experiment_implementation"), ("observation_names", "experiment_execution")):
                    names = [bindings.find_name(instance=instance_id, namespace="artifact", object_id=record["artifact_ref"]["artifact_id"])
                        for record in origins if record.get("metadata", {}).get("kind") == kind]
                    if names:
                        candidate[field] = names
        for metric in candidate["metrics"]:
            metric["artifact_name"] = bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptors[metric["source_key"]].artifact_ref.artifact_id)
    for figure in report["figures"]:
        descriptor = descriptors[figure["source_key"]]
        figure["artifact_id"] = descriptor.artifact_ref.artifact_id
        figure["artifact_name"] = bindings.find_name(instance=instance_id, namespace="artifact", object_id=descriptor.artifact_ref.artifact_id)
    contracts = [item for item in run.inputs if item.port_name == "evaluation_contract"]
    report["evaluation_contract_name"] = contracts[0].artifact_name
    report["reference_evidence_names"] = [item.artifact_name for item in run.inputs if item.port_name == "reference_evidence"]
    report["objective_names"] = [item.artifact_name for item in run.inputs if item.port_name == "research_objective"]
    frozen = {item.artifact_ref for item in run.inputs}
    original_names = {}
    for item in run.inputs:
        try:
            binding = bindings.get_binding(instance=instance_id, namespace="artifact", name=item.artifact_name)
            original_names[binding.logical_name] = (item.port_name, item.artifact_ref)
        except ValueError:
            pass
    after = None
    if before is not None:
        try:
            decoded = json.loads(base64.urlsafe_b64decode(before + "=" * (-len(before) % 4)))
            if decoded["result"] != selection.artifact_ref.artifact_id:
                raise ValueError("selection changed")
            after = tuple(decoded["after"])
        except (ValueError, KeyError, TypeError) as error:
            raise ScientificResultError("Result change cursor does not match the current exact selection.") from error
    records = bindings.binding_page(instance=instance_id, after=after, limit=limit + 1)
    objectives = {item.artifact_ref for item in run.inputs if item.port_name == "research_objective"}
    changes = []
    for record in records[:limit]:
        if record.namespace != "artifact" or record.object_id == selection.artifact_ref.artifact_id:
            continue
        envelope = artifacts.get_by_id(record.object_id)
        prior = original_names.get(record.logical_name)
        if prior and envelope.ref != prior[1] and envelope.ref not in frozen:
            original_binding = bindings.get_binding(instance=instance_id, namespace="artifact",
                name=next(item.artifact_name for item in run.inputs if item.artifact_ref == prior[1]))
            if record.revision > original_binding.revision:
                changes.append({"artifact_name": record.name, "reason": "evaluation_contract_changed" if prior[0] == "evaluation_contract" else "frozen_input_revised"})
        elif (envelope.ref not in frozen and objectives.intersection(envelope.parent_refs)
                and envelope.schema_id in {SCHEMA_ID, "scidiscovery.experiment-report.v1", "scidiscovery.layered-diagnosis.v1"}
                and record.created_at >= run.created_at):
            changes.append({"artifact_name": record.name, "reason": "related_result_not_included"})
    has_more = len(records) > limit
    next_before = _cursor(selection.artifact_ref, [records[limit - 1].created_at, records[limit - 1].namespace, records[limit - 1].name]) if has_more else None
    report["freshness"] = {"state": "needs_update" if changes else "unknown" if before or has_more else "current_within_recorded_scope",
        "changes": changes, "examined": min(len(records), limit), "coverage": "one_bounded_binding_page",
        "scan_complete": not has_more and before is None, "next_before": next_before,
        "meaning": "New evidence never overwrites this selection. Continue every page before concluding that no recorded related change exists."}
    return report


def public_result(value):
    """Do not expose route-only image identities through scheduler responses."""
    value = json.loads(canonical_json(value))
    for figure in value.get("figures", ()):
        figure.pop("artifact_id", None)
    for candidate in value.get("candidates", ()):
        candidate.pop("method_evidence", None)
        candidate.pop("observation_evidence", None)
    return value
