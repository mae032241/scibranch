"""Private calculation receipts. Never a scientific schema or workspace artifact."""
from __future__ import annotations

import hashlib
from typing import Literal
from pydantic import Field
from ..artifact_agent.schema.common import SchemaModel, Identifier, ContractDiagnostic, canonical_json
from ..plugin_runtime.calculations import CalculationResult
from ..artifact_agent.schema.tool_evidence import ToolEvidenceManifest

class CalculationAttemptReference(SchemaModel):
    manifest_alias: Identifier
    attempt_key: Identifier
    proof_kind: Literal["current", "recovery"] = "current"

class ControlledCalculationRecord(CalculationResult):
    attempt: CalculationAttemptReference | None = None
    diagnostics: tuple[ContractDiagnostic, ...] = Field(default=(), max_length=8)


def _is_projection(public, original):
    """A registered tool may omit fields, never alter scientific values."""
    if isinstance(original, dict):
        return isinstance(public, dict) and all(
            key in original and _is_projection(value, original[key]) for key, value in public.items())
    if isinstance(original, list):
        return isinstance(public, list) and len(public) == len(original) and all(
            _is_projection(value, source) for value, source in zip(public, original))
    return canonical_json(public) == canonical_json(original)


def scientific_calculation(record, *, public_result=None):
    """Project this declared record; user science dictionaries are not filtered."""
    value = record.model_dump(mode="json", exclude={"input_digests", "attempt", "diagnostics"})
    if public_result is not None:
        if not _is_projection(public_result, value.get("result")):
            raise ValueError("calculation public result must only omit fields from the recorded result")
        value["result"] = public_result
    return value


def controlled_calculation(raw, receipt):
    """Restore only the exact service-sealed receipt of these scientific bytes."""
    proof = receipt.get("metadata", {}).get("calculation_proof")
    if proof is None:
        raise ValueError("calculation has no sealed control proof")
    record = ControlledCalculationRecord.model_validate_json(canonical_json(proof))
    import json
    actual = json.loads(raw)
    expected = scientific_calculation(record)
    if (not isinstance(actual, dict) or "result" not in actual
            or not _is_projection(actual["result"], expected.get("result"))
            or canonical_json({**actual, "result": expected.get("result")}) != canonical_json(expected)):
        raise ValueError("calculation scientific material differs from its sealed proof")
    public_digest = receipt.get("metadata", {}).get("calculation_public_sha256")
    if public_digest is None and canonical_json(actual) != canonical_json(expected):
        # Historical receipts sealed the public file identity with the complete
        # record, before explicit public projections had their own digest.
        public_digest = receipt.get("artifact_ref", {}).get("sha256")
        if public_digest is None:
            raise ValueError("calculation public projection has no sealed original identity")
    if public_digest is not None and hashlib.sha256(raw).hexdigest() != public_digest:
        raise ValueError("calculation scientific material differs from its sealed public identity")
    return record


def _recovery_origins(proof):
    return (proof, *proof.ancestors) if proof is not None else ()


def calculation_record_content(record):
    """Receipt metadata does not change the historical numerical result format."""
    return record.model_dump(mode="json", exclude={"attempt", "diagnostics"})


def calculation_sources(record, sources):
    """Verify recorded results and their exact bound sources without recalculating.

    This consumes frozen evidence only. It does not admit inputs or call tools.
    """
    from dataclasses import replace
    from ..operation_contract import SemanticRuleViolation
    from .input_validation import ValidationSources, prior_analysis_sources
    if record.calculation_ref is not None:
        alias = record.calculation_ref
        if alias not in sources:
            raise SemanticRuleViolation("reference calculation alias is not an available controlled source")
        resolver = getattr(sources, "reference_calculation_sources", None)
        original_sources = resolver(alias) if resolver is not None else None
        descriptor = sources.binding_descriptors.get(alias)
        exact = (original_sources.controlled_reference_calculation(alias, descriptor.artifact_ref)
                 if original_sources is not None and descriptor is not None else None)
        if exact is None:
            raise SemanticRuleViolation("reference calculation has no authorized original manifest")
        # The service already verified scientific bytes against this receipt.
        # Keep exact record comparison; file-access bytes are a lazy placeholder.
        if (canonical_json(record.model_dump(mode="json", exclude={"calculation_ref"}))
                != canonical_json(exact.model_dump(mode="json", exclude={"calculation_ref"}))):
            raise SemanticRuleViolation("reference calculation differs from its exact accessed original")
        return calculation_sources(exact, original_sources)
    if not isinstance(record, ControlledCalculationRecord):
        raise SemanticRuleViolation("Numerical evidence must cite a registered calculation; inline science is not a tool receipt")
    prior = prior_analysis_sources(sources)
    current_raw = getattr(sources, "tool_snapshot", None)
    historical = False
    proof_raw = current_raw
    if record.attempt is not None:
        alias = record.attempt.manifest_alias
        if alias == "tool_recovery_manifest":
            if current_raw is None:
                raise SemanticRuleViolation("calculation attempt has no current control manifest")
        elif prior is not None and alias == prior["manifest_alias"]:
            proof_raw = sources[alias]
            historical = True
        else:
            raise SemanticRuleViolation("calculation attempt references an unpaired proof manifest")
    if record.attempt is None and current_raw is not None and not historical:
        raise SemanticRuleViolation("current calculation requires its controlled attempt receipt")
    view = sources
    if historical:
        aliases = prior["source_bindings"]
        descriptors = getattr(sources, "binding_descriptors", {})
        view = ValidationSources({old:sources[new] for old,new in aliases.items()},
            {old:replace(descriptors[new], source_name=old) for old,new in aliases.items()},
            validation_deadline=getattr(sources, "validation_deadline", None))
    if record.attempt is None:
        # Receipt-free legacy results must be unchanged members of the exact
        # sealed prior analysis. Current computed results require tool evidence.
        if record.status == "computed":
            if not historical:
                raise SemanticRuleViolation("computed calculation requires a controlled receipt or exact sealed historical record")
            descriptors = getattr(view, "binding_descriptors", {})
            if any(alias not in descriptors or descriptors[alias].sha256 != digest
                   for alias, digest in record.input_digests.items()):
                raise SemanticRuleViolation("historical calculation source is not exactly bound")
        return view
    proof = ToolEvidenceManifest.model_validate_json(proof_raw)
    if record.attempt.proof_kind == "recovery":
        if proof.recovery is None:
            raise SemanticRuleViolation("calculation recovery proof is missing or changed")
        last_error = None
        for origin in _recovery_origins(proof.recovery):
            try:
                # Alias scopes are never merged across failed Runs. A record
                # must match one complete controlled origin and current bytes.
                descriptors = getattr(sources, "binding_descriptors", {})
                aliases = {}
                for old, binding in origin.bindings.items():
                    matches = [name for name, descriptor in descriptors.items()
                               if descriptor.artifact_ref == binding.artifact_ref]
                    if len(matches) > 1:
                        matches = [name for name in matches
                                   if descriptors[name].port_name == binding.port_name]
                    if len(matches) > 1:
                        raise SemanticRuleViolation("recovery source identity is ambiguous")
                    if matches:
                        aliases[old] = matches[0]
                recovery_view = ValidationSources({old:sources[new] for old,new in aliases.items()},
                    {old:replace(descriptors[new], source_name=old) for old,new in aliases.items()},
                    validation_deadline=getattr(sources, "validation_deadline", None))
                return _calculation_attempt_sources(record, origin, recovery_view)
            except SemanticRuleViolation as error:
                last_error = error
        raise last_error
    return _calculation_attempt_sources(record, proof, view)


def _calculation_attempt_sources(record, proof, view):
    from ..operation_contract import SemanticRuleViolation
    attempt = next((a for a in proof.attempts if a.attempt_key == record.attempt.attempt_key), None)
    if attempt is None or attempt.state == "started":
        raise SemanticRuleViolation("calculation attempt has no terminal control evidence")
    if hashlib.sha256(canonical_json(record.request)).hexdigest() != attempt.request_digest:
        raise SemanticRuleViolation("calculation request differs from its recorded attempt")
    bindings = {}
    descriptors = getattr(view, "binding_descriptors", {})
    for alias in attempt.read_sources:
        binding = proof.bindings.get(alias)
        descriptor = descriptors.get(alias)
        if binding is None or descriptor is None or binding.artifact_ref != descriptor.artifact_ref:
            raise SemanticRuleViolation("calculation attempt source is not exactly bound for replay")
        bindings[alias] = binding.model_dump(mode="json")
    if (attempt.sources_digest is not None or attempt.state != "interrupted") and hashlib.sha256(canonical_json(bindings)).hexdigest() != attempt.sources_digest:
        raise SemanticRuleViolation("calculation attempt read-source proof differs")
    if any(alias not in bindings or digest != bindings[alias]["artifact_ref"]["sha256"]
           for alias,digest in record.input_digests.items()):
        raise SemanticRuleViolation("calculation digest does not identify a source actually read")
    if attempt.state == "completed":
        if record.status != attempt.result_status or record.reason_code != attempt.reason_code:
            raise SemanticRuleViolation("calculation outcome differs from the actual tool response")
        if hashlib.sha256(canonical_json(calculation_record_content(record))).hexdigest() != attempt.result_digest:
            raise SemanticRuleViolation("calculation record differs from the actual tool response")
    elif attempt.state == "rejected":
        if (record.status not in {"unavailable", "error"} or not attempt.diagnostics
                or record.reason_code != attempt.diagnostics[0].code):
            raise SemanticRuleViolation("rejected arguments cannot establish an unsupported format or successful result")
    else:
        if record.status != "error" or record.reason_code != "interrupted":
            raise SemanticRuleViolation("an interrupted call has no confirmed tool outcome")
    allowed = {canonical_json(d) for d in attempt.diagnostics}
    if any(canonical_json(d) not in allowed for d in record.diagnostics):
        raise SemanticRuleViolation("calculation diagnostics differ from the controlled attempt")
    if record.status != "computed" and attempt.diagnostics and not record.diagnostics:
        raise SemanticRuleViolation("failed calculation must preserve its controlled failure phase")
    return view
