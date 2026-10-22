"""Tool-owned analysis files and reference-only consumption of their receipts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..operation_contract import declared_violation
from ..plugin_runtime.calculations import CalculationResult, analysis_source_claims, analysis_evidence_aliases
from .input_validation import prior_analysis_sources
from ..artifact_agent.schema.common import canonical_json
from .calculation_proof import ControlledCalculationRecord as CalculationRecord, controlled_calculation, scientific_calculation
from ..plugin_runtime.workspace import write_control_workspace_file


def publish_analysis_file(context, raw, *, media_type, kind, sources, suffix, metadata=None):
    """Register immutable bytes first; expose only a task-local preview path."""
    accepted = context.accept_evidence(raw=raw, media_type=media_type, derived_from=tuple(sources),
        metadata={**(metadata or {}), "kind": kind})
    write_control_workspace_file(context.workspace,
        Path(".operation-tools/analysis") / (accepted["alias"] + suffix), raw,
        replace=False, mode=0o400, create_parents=True)
    return {"evidence_alias": accepted["alias"]}


def retain_calculation(context, response, *, public_result=None):
    """Keep the existing tool reply readable, but remove the need to copy it.

    The complete record is a tool output. The added alias is a transport hint,
    excluded by CalculationRecord from receipt identity and historical replay.
    """
    record = CalculationRecord.model_validate_json(canonical_json(response))
    response = scientific_calculation(record, public_result=public_result)
    raw = canonical_json(response)
    saved = publish_analysis_file(context, raw, media_type="application/json",
        kind="calculation_record", sources=tuple(record.input_digests) or context.input_names_for_port("experiment_results") or context.input_names_for_port("execution_result"),
        suffix=".json", metadata={"record_key": record.record_key,
            "calculation_proof": record.model_dump(mode="json"),
            "calculation_public_sha256": hashlib.sha256(raw).hexdigest()})
    return {**response, "calculation_ref": saved["evidence_alias"]}


def analysis_calculations(report, sources):
    """Resolve cited tool records without changing scientific output or rerunning tools.

    Reports cite current registered scientific files or exact originals from a
    paired prior analysis. The service verifies their private sealed receipts.
    """
    records = []
    descriptors = getattr(sources, "binding_descriptors", {})
    current_raw = getattr(sources, "tool_snapshot", None)
    current_proof = json.loads(current_raw) if current_raw else {}
    current = current_proof.get("records", ())
    # Accept direct citations as well as optional local citation mappings. A
    # copied evidence row is not required to consume a tool-owned receipt.
    aliases = list(dict.fromkeys(
        [item.input_alias for item in report.source_references]
        + [item.source_key for item in report.evidence]
        + [item.locator.split(":", 1)[0] for item in report.evidence]))
    pending = [report.model_dump(mode="json")]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for alias in value.get("evidence_keys", ()):
                if alias not in aliases:
                    aliases.append(alias)
            pending.extend(child for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            pending.extend(child for child in value if isinstance(child, (dict, list)))
    for alias in aliases:
        descriptor = descriptors.get(alias)
        if descriptor is None or dict(descriptor.labels).get("analysis_artifact_kind") != "calculation_record":
            continue
        is_current = any(item["alias"] == alias and item["artifact_ref"] == descriptor.artifact_ref.model_dump(mode="json")
                         for item in current)
        proof = current_proof
        if not is_current and any(item["alias"] == alias
                and item["artifact_ref"] == descriptor.artifact_ref.model_dump(mode="json")
                for item in current_proof.get("accesses", ())):
            # An accessed calculation keeps its producer's proof namespace. The
            # control resolver verifies that exact receipt, without binding its
            # internal sources as new Worker inputs or current products.
            original_sources = sources.reference_calculation_sources(alias)
            record = (original_sources.controlled_reference_calculation(alias, descriptor.artifact_ref)
                      if original_sources is not None else None)
            if record is None:
                raise declared_violation("calculation reference differs from its exact controlled access", path="$.evidence")
            records.append(record.model_copy(update={"calculation_ref": alias}))
            continue
        if not is_current:
            prior = prior_analysis_sources(sources)
            if prior is None:
                raise declared_violation("historical calculation reference needs its paired prior analysis manifest", path="$.evidence")
            proof = json.loads(sources[prior["manifest_alias"]])
            if not any(item["artifact_ref"] == descriptor.artifact_ref.model_dump(mode="json")
                       for item in proof.get("records", ())):
                raise declared_violation("calculation reference is not in the paired prior manifest", path="$.evidence")
        receipt = next(item for item in proof.get("records", ())
            if item["artifact_ref"] == descriptor.artifact_ref.model_dump(mode="json"))
        record = controlled_calculation(sources[alias], receipt)
        if not is_current and record.attempt is not None:
            record = record.model_copy(update={"attempt": record.attempt.model_copy(
                update={"manifest_alias": prior["manifest_alias"]})})
        recovery = proof.get("recovery") or {}
        if record.attempt is not None and any(binding["artifact_ref"] == descriptor.artifact_ref.model_dump(mode="json")
                for binding in recovery.get("bindings", {}).values()):
            record = record.model_copy(update={"attempt": record.attempt.model_copy(update={"proof_kind": "recovery"})})
        # Saved files have distinct artifact identities. Their human-chosen
        # record names need not form another namespace across calls or Runs.
        records.append(record)
    return tuple(records)


def complete_calculation(context, result, *, diagnostics=(), public_result=None):
    """Own attempt finalization and private receipt construction, never the plugin."""
    # Revalidate the public shape so a caller cannot inject an attempt/receipt.
    public = CalculationResult.model_validate_json(canonical_json(result))
    # Reject an invalid projection before sealing the attempt's terminal result.
    scientific_calculation(public, public_result=public_result)
    response = public.model_dump(mode="json", exclude={"diagnostics"})
    diagnostics = diagnostics or tuple(item.model_dump(mode="json") for item in public.diagnostics)
    finished = context.finish_attempt(result_status=public.status, reason_code=public.reason_code,
        response=response, diagnostics=diagnostics)
    if finished is not None:
        attempt, controlled_diagnostics = finished
        response.update(attempt=attempt, diagnostics=list(controlled_diagnostics))
    return retain_calculation(context, response, public_result=public_result)


def verified_calculations(report, sources):
    """Verify private receipts before exposing domain result values."""
    from .calculation_proof import calculation_sources
    result = []
    for record in analysis_calculations(report, sources):
        calculation_sources(record, sources)
        public = CalculationResult.model_validate_json(canonical_json(
            record.model_dump(mode="json", exclude={"attempt", "diagnostics"})))
        result.append(public.model_copy(update={"calculation_ref": record.calculation_ref}))
    return tuple(result)


def _checkpoint_sources(context, sources):
    refs = {name: context.source_descriptor(name).artifact_ref.model_dump(mode="json")
            for name in sources}
    return refs, {name: ref["sha256"] for name, ref in refs.items()}


def publish_calculation_checkpoint(context, raw, *, algorithm_version, record_key,
                                   sources, numerical_identity):
    """Derive immutable source identity; plugins supply only numerical values."""
    refs, digests = _checkpoint_sources(context, sources)
    return publish_analysis_file(context, raw, media_type="application/json",
        kind="calculation_checkpoint", sources=sources, suffix=".json",
        metadata={"record_key": record_key, "algorithm_version": algorithm_version,
            "checkpoint_proof": {"numerical_identity": numerical_identity,
                "input_digests": digests, "input_refs": refs}})


def read_calculation_checkpoint(context, alias, *, algorithm_version, tool_names,
                                sources, max_bytes):
    """Verify exact registered source identities before exposing checkpoint values."""
    receipts = context._list_evidence() if context._list_evidence else ()
    record = next((item for item in receipts if item["alias"] == alias), None)
    if (record is None or record.get("metadata", {}).get("kind") != "calculation_checkpoint"
            or record.get("metadata", {}).get("algorithm_version") != algorithm_version
            or record.get("tool_name") not in tool_names):
        raise ValueError("diagnostic_checkpoint_not_controlled")
    raw = context.read_evidence(alias)
    if len(raw) > max_bytes or hashlib.sha256(raw).hexdigest() != record["artifact_ref"]["sha256"]:
        raise ValueError("diagnostic_checkpoint_identity_mismatch")
    refs, digests = _checkpoint_sources(context, sources)
    proof = record.get("metadata", {}).get("checkpoint_proof", {})
    if proof.get("input_digests") != digests or proof.get("input_refs") != refs:
        raise ValueError("diagnostic_checkpoint_scope_mismatch")
    return raw, proof.get("numerical_identity", [])
