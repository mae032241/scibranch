"""Exact producer-family and input-origin resolution shared by admission and freeze."""
from __future__ import annotations

import json
from typing import Any
from ..schema.refs import ArtifactRef
from ..schema.common import canonical_json, canonical_sha256
from ...operations.invoke import (
    InvocationArtifact, OperationInvocationError, ProducerOutputFamily,
    ProducerFamilyMember, ProducerInputProjection, ProducerEvidenceSource,
)


class OperationOrigins:
    def __init__(self, *, artifacts, bindings, runs, catalog, instance_id):
        self.artifacts = artifacts
        self.bindings = bindings
        self.runs = runs
        self._operation_catalog = catalog
        self.instance_id = instance_id

    def _instance_id(self):
        return self.instance_id

    def validate_inputs(self, compiled, inputs):
        """Re-derive every declared edge, comparing refs rather than navigation names."""
        ports = {port.name: port for port in compiled.spec.inputs}
        resolved = {port.name: [] for port in compiled.spec.inputs if port.derivation is None}
        actual = {port.name: [] for port in compiled.spec.inputs}
        for item in inputs:
            envelope = self.artifacts.catalog(item.artifact_ref)
            actual[item.port_name].append(item.artifact_ref)
            if ports[item.port_name].derivation is None:
                resolved[item.port_name].append(InvocationArtifact(
                    artifact_name=item.artifact_name, ref=envelope.ref,
                    schema_id=envelope.schema_id, media_type=envelope.media_type,
                    size_bytes=envelope.size_bytes, parent_refs=envelope.parent_refs))
        resolved = {port: tuple(items) for port, items in resolved.items()}
        self._derive_operation_inputs(compiled, resolved)
        derived = {}
        for port in compiled.spec.inputs:
            if port.derivation is None:
                continue
            expected = resolved.get(port.name, ())
            if tuple(actual[port.name]) != tuple(item.ref for item in expected):
                raise OperationInvocationError("input_origin_mismatch", port=port.name)
            derived.update(((port.name, item.ref), item) for item in expected)
        return derived

    def _producer_output_family_from_envelope(
        self,
        artifact_name: str,
        envelope: Any,
    ) -> ProducerOutputFamily | None:
        run_family = self._run_output_family(envelope)
        if run_family is not None:
            return run_family
        return self._transform_output_family(artifact_name, envelope)

    def _run_output_family(self, envelope: Any) -> ProducerOutputFamily | None:
        if self.runs is None:
            return None
        status = self.runs.completed_for_output(envelope.ref)
        if status is None or status.output_ref is None:
            return None
        is_primary_output = status.output_ref == envelope.ref
        if not is_primary_output and not any(
            ref == envelope.ref for _, ref in self.runs.evidence_output_refs(status)
        ):
            return None
        if any(envelope.labels.get(key) != expected for key, expected in (
            ("operation_id", status.operation_id),
            ("operation_version", status.operation_version),
            ("operation_digest", status.operation_digest),
        )):
            raise OperationInvocationError("producer_family_inconsistent")
        port_name = envelope.labels.get("operation_output_port")
        counts: dict[str, int] = {}
        producer_inputs = []
        for item in status.inputs:
            counts[item.port_name] = counts.get(item.port_name, 0) + 1
            producer_inputs.append(
                ProducerInputProjection(
                    port_name=item.port_name,
                    item_index=counts[item.port_name],
                    ref=item.artifact_ref,
                    artifact_name=item.artifact_name,
                    source_name=item.source_name,
                )
            )
        sources = tuple(
            ProducerEvidenceSource(
                source_kind="run_input",
                source_name=value.source_name,
                ref=value.artifact_ref,
            )
            for value in status.inputs
            if value.usage
            in {"claim_evidence", "evidence_inventory", "cached_excerpt"}
        )
        family_identity = canonical_sha256(
            {
                "producer": "run",
                "operation": status.operation_id,
                "operation_version": status.operation_version,
                "operation_digest": status.operation_digest,
                "run_id": status.run_id,
                "primary_ref": status.output_ref,
            }
        )
        try:
            producer = self._operation_catalog.recorded_operation(status.operation_id, version=status.operation_version, digest=status.operation_digest)
        except KeyError:
            producer = None
        if producer is None:
            return ProducerOutputFamily(
                primary_ref=status.output_ref,
                operation_id=status.operation_id,
                operation_version=status.operation_version,
                operation_digest=status.operation_digest,
                producer_kind="run",
                producer_instance_id=status.instance_id,
                producer_run_id=status.run_id,
                contract_availability="historical",
                unavailable_reason="producer_contract_unavailable",
                members=(
                    ProducerFamilyMember(port_name=port_name, item_name=None, ref=envelope.ref),
                ) if isinstance(port_name, str) else (),
                evidence_sources=sources,
                producer_inputs=tuple(producer_inputs),
                family_identity=family_identity,
            )
        if producer.spec.executor.kind != "agent":
            return None
        selected_port = next(
            (
                port
                for port in producer.spec.outputs
                if port.name == port_name
                and ((port.collection is None) == is_primary_output)
            ),
            None,
        )
        if selected_port is None or not self._artifact_matches_output_port(
            envelope, selected_port
        ):
            return None
        return ProducerOutputFamily(
            primary_ref=status.output_ref,
            operation_id=producer.spec.operation_id,
            operation_version=status.operation_version,
            operation_digest=status.operation_digest,
            producer_kind="run",
            producer_instance_id=status.instance_id,
            producer_run_id=status.run_id,
            contract_availability="current",
            unavailable_reason=None,
            members=(
                ProducerFamilyMember(
                    port_name=selected_port.name,
                    item_name=None,
                    ref=envelope.ref,
                ),
            ),
            evidence_sources=sources,
            producer_inputs=tuple(producer_inputs),
            reviewer_operation=(
                producer.spec.review.reviewer_operation
                if producer.spec.review is not None
                else None
            ),
            review_subject_outputs=(
                producer.spec.review.subject_outputs
                if producer.spec.review is not None
                else ()
            ),
            family_identity=family_identity,
        )

    def _transform_output_family(
        self,
        artifact_name: str,
        envelope: Any,
    ) -> ProducerOutputFamily | None:
        labels = envelope.labels
        operation_id = labels.get("operation_id")
        operation_version = labels.get("operation_version")
        operation_digest = labels.get("operation_digest")
        invocation_fingerprint = labels.get("operation_invocation_fingerprint")
        if (
            not isinstance(operation_id, str)
            or not isinstance(operation_version, str)
            or not isinstance(operation_digest, str)
            or not isinstance(invocation_fingerprint, str)
            or labels.get("transform_profile") != operation_id
        ):
            return None
        if labels.get("operation_invocation_instance", self._instance_id()) != self._instance_id():
            return None
        output_label = labels.get("output_label")
        if not isinstance(output_label, str):
            return None
        # Older outputs already have an exact durable creation binding. Recover
        # its family boundary from that binding, never from equal request bytes.
        suffix = "." + output_label
        legacy_name = (artifact_name if output_label == "primary" else
                       artifact_name[:-len(suffix)] if artifact_name.endswith(suffix) else None)
        invocation_name = labels.get("operation_invocation_name", legacy_name)
        if not isinstance(invocation_name, str) or not invocation_name:
            return None
        family_identity = canonical_sha256(
            {
                "producer": "transform",
                "instance": self._instance_id(),
                "invocation_name": invocation_name,
                "operation": operation_id,
                "operation_version": operation_version,
                "operation_digest": operation_digest,
                "invocation_fingerprint": invocation_fingerprint,
                "ordered_parents": envelope.parent_refs,
            }
        )

        instance_bindings = self.bindings.list(
            instance=self._instance_id(), namespace="artifact", name_prefix=invocation_name
        )
        subject_bindings = tuple(
            binding
            for binding in instance_bindings
            if binding.object_id == envelope.artifact_id
        )
        if not any(
            binding.name == artifact_name
            and binding.request_fingerprint == invocation_fingerprint
            for binding in subject_bindings
        ):
            return None
        siblings: dict[str, Any] = {}
        for binding in instance_bindings:
            if binding.request_fingerprint != invocation_fingerprint:
                continue
            candidate = self.artifacts.get_by_id(binding.object_id)
            candidate_labels = candidate.labels
            output_label = candidate_labels.get("output_label")
            if not isinstance(output_label, str):
                continue
            expected_name = invocation_name if output_label == "primary" else invocation_name + "." + output_label
            if binding.name != expected_name:
                continue
            if (candidate_labels.get("operation_invocation_name", invocation_name) != invocation_name
                    or candidate_labels.get("operation_invocation_instance", self._instance_id()) != self._instance_id()):
                raise OperationInvocationError("producer_family_inconsistent")
            if candidate.parent_refs != envelope.parent_refs or any(
                candidate_labels.get(key) != expected
                for key, expected in (
                    ("operation_id", operation_id),
                    ("operation_version", operation_version),
                    ("operation_digest", operation_digest),
                    ("transform_profile", operation_id),
                    ("operation_invocation_fingerprint", invocation_fingerprint),
                )
            ):
                raise OperationInvocationError("producer_family_inconsistent")
            output_label = candidate_labels.get("output_label")
            if not isinstance(output_label, str):
                raise OperationInvocationError("producer_family_inconsistent")
            existing = siblings.get(output_label)
            if existing is not None and existing.ref != candidate.ref:
                raise OperationInvocationError("producer_family_inconsistent")
            siblings[output_label] = candidate
        if "primary" not in siblings or envelope.ref not in {
            value.ref for value in siblings.values()
        }:
            return None

        def unavailable(reason: str) -> ProducerOutputFamily:
            return ProducerOutputFamily(
                primary_ref=siblings["primary"].ref,
                operation_id=operation_id,
                operation_version=operation_version,
                operation_digest=operation_digest,
                producer_kind="transform",
                producer_instance_id=self._instance_id(),
                producer_run_id=None,
                contract_availability="historical",
                unavailable_reason=reason,
                members=(),
                evidence_sources=(),
                producer_inputs=(),
                family_identity=family_identity,
            )
        try:
            compiled = self._operation_catalog.recorded_operation(operation_id, version=operation_version, digest=operation_digest)
        except KeyError:
            return unavailable("producer_contract_unavailable")
        if compiled.spec.executor.kind != "transform":
            return unavailable("producer_contract_unavailable")
        input_groups = self._transform_input_groups(compiled, envelope.parent_refs)
        if input_groups is None:
            return unavailable("producer_input_mapping_ambiguous")
        ordered_members = self._validated_transform_members(compiled, siblings)
        if ordered_members is None:
            return None
        return ProducerOutputFamily(
            primary_ref=siblings["primary"].ref,
            operation_id=operation_id,
            operation_version=operation_version,
            operation_digest=operation_digest,
            producer_kind="transform",
            producer_instance_id=self._instance_id(),
            producer_run_id=None,
            contract_availability="current",
            unavailable_reason=None,
            members=ordered_members,
            evidence_sources=(),
            producer_inputs=tuple(
                ProducerInputProjection(
                    port_name=port.name,
                    item_index=index,
                    ref=item.ref,
                )
                for port in compiled.spec.inputs
                for index, item in enumerate(input_groups[port.name], start=1)
            ),
            reviewer_operation=(
                compiled.spec.review.reviewer_operation
                if compiled.spec.review is not None
                else None
            ),
            review_subject_outputs=(
                compiled.spec.review.subject_outputs
                if compiled.spec.review is not None
                else ()
            ),
            family_identity=family_identity,
        )

    def _transform_input_groups(
        self, compiled: Any, parent_refs: tuple[Any, ...]
    ) -> dict[str, tuple[Any, ...]] | None:
        if len(parent_refs) != len(set(parent_refs)):
            return None
        envelopes = tuple(self.artifacts.verify(ref) for ref in parent_refs)
        ports = compiled.spec.inputs
        solutions: list[dict[str, tuple[Any, ...]]] = []

        def visit(port_index: int, offset: int, values: dict[str, tuple[Any, ...]]) -> None:
            if len(solutions) > 1:
                return
            if port_index == len(ports):
                if offset == len(envelopes):
                    solutions.append(dict(values))
                return
            port = ports[port_index]
            remaining_min = sum(item.min_items for item in ports[port_index + 1 :])
            remaining_max = sum(item.max_items for item in ports[port_index + 1 :])
            lower = max(port.min_items, len(envelopes) - offset - remaining_max)
            upper = min(port.max_items, len(envelopes) - offset - remaining_min)
            for count in range(lower, upper + 1):
                selected = envelopes[offset : offset + count]
                if not all(self._artifact_matches_input_port(item, port) for item in selected):
                    continue
                values[port.name] = selected
                visit(port_index + 1, offset + count, values)
            values.pop(port.name, None)

        visit(0, 0, {})
        return solutions[0] if len(solutions) == 1 else None

    def _validated_transform_members(
        self, compiled: Any, siblings: dict[str, Any]
    ) -> tuple[ProducerFamilyMember, ...] | None:
        by_port: dict[str, list[Any]] = {}
        valid_ports = {port.name for port in compiled.spec.outputs}
        for candidate in siblings.values():
            port_name = candidate.labels.get("operation_output_port")
            if port_name not in valid_ports:
                return None
            by_port.setdefault(port_name, []).append(candidate)
        expected_labels: list[tuple[str, str, int]] = []
        output_count = 0
        for port in compiled.spec.outputs:
            values = by_port.get(port.name, [])
            if not port.min_items <= len(values) <= port.max_items:
                return None
            if not all(self._artifact_matches_output_port(item, port) for item in values):
                return None
            for index in range(1, len(values) + 1):
                label = (
                    "primary"
                    if output_count == 0
                    else port.name
                    if port.collection is None and len(values) == 1
                    else f"{port.name}_{index:03d}"
                )
                expected_labels.append((label, port.name, index))
                output_count += 1
        if {label for label, _, _ in expected_labels} != set(siblings):
            return None
        if any(
            siblings[label].labels.get("operation_output_port") != port_name
            for label, port_name, _ in expected_labels
        ):
            return None
        return tuple(
            ProducerFamilyMember(
                port_name=port_name,
                item_name=(None if label == "primary" else label),
                ref=siblings[label].ref,
            )
            for label, port_name, _ in expected_labels
        )

    def _derived_parent_refs(self, ref, port):
        """Prefer exact control receipt edges over inherited task input parents."""
        envelope = self.artifacts.verify(ref)
        parents = envelope.parent_refs
        producer_id = envelope.labels.get('tool_producer_run')
        producer = None
        if self.runs is not None and producer_id and envelope.labels.get('tool_name'):
            producer = self.runs.status(producer_id)
            if producer.instance_id != self._instance_id():
                raise OperationInvocationError('input_origin_unavailable', port=port.name)
            receipts = [item for item in self.runs.tool_evidence(producer_id)
                if ArtifactRef.model_validate(item['artifact_ref']) == ref]
            if len(receipts) != 1 or receipts[0]['tool_name'] != envelope.labels['tool_name']:
                raise OperationInvocationError('input_origin_unavailable', port=port.name)
            if receipts[0].get('control_parents'):
                direct = tuple(ArtifactRef.model_validate(item) for item in receipts[0]['control_parents'])
                if any(parent not in parents for parent in direct):
                    raise OperationInvocationError('input_origin_unavailable', port=port.name)
                parents = direct
                producer = None  # Explicit edges require no historical inference.
            elif receipts[0].get('metadata', {}).get('derived_from'):
                direct = tuple(self.runs.source_descriptor(producer, alias).artifact_ref
                    for alias in receipts[0]['metadata']['derived_from'])
                if any(parent not in parents for parent in direct):
                    raise OperationInvocationError('input_origin_unavailable', port=port.name)
                parents, producer = direct, None
        matches = [parent for parent in parents
            if self._artifact_matches_input_port(self.artifacts.catalog(parent), port)]
        if producer is not None and len(matches) > 1:
            inherited = {item.artifact_ref for item in producer.inputs}
            matches = [parent for parent in matches if parent not in inherited]
        return matches

    @staticmethod
    def _artifact_matches_input_port(envelope: Any, port: Any) -> bool:
        return bool(
            (port.schema_id == "*" or envelope.schema_id == port.schema_id)
            and (port.media_types == ("*/*",) or envelope.media_type in port.media_types)
            and envelope.size_bytes <= port.max_item_bytes
        )

    @staticmethod
    def _artifact_matches_output_port(envelope: Any, port: Any) -> bool:
        return bool(
            envelope.kind == port.kind
            and envelope.schema_id == port.schema_id
            and envelope.payload_schema_version == port.payload_schema_version
            and envelope.media_type in port.media_types
            and envelope.size_bytes <= port.max_item_bytes
        )

    def _derive_operation_inputs(self, compiled, resolved):
        """Resolve only declared producer edges of exact selected immutable anchors."""
        ports = {port.name: port for port in compiled.spec.inputs}
        def bind(port):
            if port.name in resolved or port.derivation is None:
                return
            rule = port.derivation
            bind(ports[rule.anchor_port])
            anchors = resolved.get(rule.anchor_port, ())
            if not anchors and port.min_items == 0:
                resolved[port.name] = ()
                return
            def reject(reason):
                raise OperationInvocationError(reason, port=rule.anchor_port,
                    message="The selected scientific subject has missing, ambiguous or inaccessible original materials. Select a subject with its sealed producer history; no newer material is substituted.")
            if len(anchors) != 1 and not (anchors and rule.select in {"parents", "receipt_materials"} and not rule.producer_input_path):
                reject("input_origin_ambiguous" if anchors else "input_origin_missing")
            def family_for(ref):
                name = self.bindings.find_name(instance=self._instance_id(),
                    namespace="artifact", object_id=ref.artifact_id)
                if name is None:
                    reject("input_origin_unavailable")
                self.artifacts.verify(ref)
                return self._producer_output_family_from_envelope(name, self.artifacts.catalog(ref))
            refs = [anchor.ref for anchor in anchors]
            for edge in rule.producer_input_path:
                following = []
                for ref in refs:
                    family = family_for(ref)
                    if family is None or family.producer_instance_id != self._instance_id():
                        reject("input_origin_unavailable")
                    following.extend(item.ref for item in family.producer_inputs if item.port_name == edge)
                refs = list(dict.fromkeys(following))
                if not refs:
                    reject("input_origin_missing")
            private_names = {}
            family = None
            if rule.select == "receipt_materials":
                # Receipt manifests are control-owned, exact original records.
                # Follow only recorded material refs, never all inherited inputs.
                selected = []
                for ref in refs:
                    envelope = self.artifacts.verify(ref)
                    owner_id = envelope.labels.get("tool_producer_run")
                    if (envelope.schema_id != "scidiscovery.tool-evidence-manifest.v1"
                            or envelope.labels.get("operation_output_port") != "recovery_manifest_output"
                            or envelope.size_bytes > 1024 * 1024 or not owner_id or self.runs is None):
                        reject("input_origin_unavailable")
                    owner = self.runs.status(owner_id)
                    if (owner.instance_id != self._instance_id() or owner.state != "completed"
                            or owner.output_ref is None or ref not in self.artifacts.catalog(owner.output_ref).parent_refs
                            or envelope.labels.get("operation_digest") != owner.operation_digest):
                        reject("input_origin_unavailable")
                    original = {item["alias"]: item for item in self.runs.tool_evidence(owner_id)}
                    for record in json.loads(self.artifacts.read(ref)).get("records", ()):
                        retained = original.get(record.get("alias"))
                        if retained is None or canonical_json(retained) != canonical_json(record):
                            reject("input_origin_unavailable")
                        material = ArtifactRef.model_validate(record["artifact_ref"])
                        if material not in envelope.parent_refs:
                            reject("input_origin_unavailable")
                        if self._artifact_matches_input_port(self.artifacts.catalog(material), port):
                            selected.append(material)
                            private_names[material] = next(anchor.artifact_name for anchor in anchors if anchor.ref == ref)
                refs = list(dict.fromkeys(selected))
            elif rule.select == "parents":
                # Only the exact anchor's immutable direct parents are candidates.
                # No public parent alias, completed author, head or global lookup
                # is needed to bind a control-owned typed execution package.
                refs = list(dict.fromkeys(parent for ref in refs
                    for parent in self._derived_parent_refs(ref, port)))
                private_names = ({ref: anchors[0].artifact_name for ref in refs} if len(anchors) == 1 else
                    {ref: next(anchor.artifact_name for anchor in anchors
                        if ref in self._derived_parent_refs(anchor.ref, port)) for ref in refs})
            elif rule.select != "subject":
                if len(refs) != 1:
                    reject("input_origin_ambiguous")
                family = family_for(refs[0])
                if family is None or family.producer_instance_id != self._instance_id() or family.contract_availability != "current":
                    reject("input_origin_unavailable")
                refs = ([item.ref for item in family.members if item.ref != family.primary_ref]
                        if rule.select == "siblings" else [item.ref for item in family.evidence_sources])
                refs = list(dict.fromkeys(refs))
            if rule.producer_output_port is not None:
                if len(refs) != 1:
                    reject("input_origin_ambiguous")
                family = family_for(refs[0])
                if family is None or family.contract_availability != "current" or family.producer_instance_id != self._instance_id():
                    reject("input_origin_unavailable")
                producer = self._operation_catalog.recorded_operation(family.operation_id, version=family.operation_version, digest=family.operation_digest)
                declared = next((item for item in producer.spec.outputs if item.name == rule.producer_output_port), None)
                if declared is None:
                    reject("input_origin_output_undeclared")
                if family.producer_run_id is None:
                    candidates = [item.ref for item in family.members if item.port_name == declared.name]
                else:
                    original = self.runs.status(family.producer_run_id)
                    if original.state != "completed" or original.output_ref != family.primary_ref:
                        reject("input_origin_unavailable")
                    exact_evidence = {ArtifactRef.model_validate(item["artifact_ref"])
                        for item in self.runs.tool_evidence(original.run_id)}
                    candidates = [original.output_ref, *self.artifacts.catalog(original.output_ref).parent_refs]
                    candidates.extend(ref for _, ref in self.runs.evidence_output_refs(original))
                    candidates = [ref for ref in dict.fromkeys(candidates)
                        if self.artifacts.catalog(ref).labels.get("operation_output_port") == declared.name
                        and self.artifacts.catalog(ref).labels.get("operation_digest") == original.operation_digest
                        and (ref == original.output_ref
                            or self.artifacts.catalog(ref).labels.get("tool_producer_run") == original.run_id
                            or (declared.name in {"tool_evidence", "attachments"} and ref in exact_evidence))]
                refs = list(dict.fromkeys(candidates))
                # Private artifacts deliberately have no public name binding.
                private_names = {ref: anchors[0].artifact_name for ref in refs} if not port.agent_visible else {}
            if len(refs) < port.min_items or len(refs) > port.max_items:
                reject("input_origin_ambiguous" if len(refs) > port.max_items else "input_origin_missing")
            values = []
            for ref in refs:
                name = private_names.get(ref) or self.bindings.find_name(instance=self._instance_id(), namespace="artifact", object_id=ref.artifact_id)
                if name is None:
                    reject("input_origin_unavailable")
                envelope = self.artifacts.catalog(ref)
                self.artifacts.verify(ref)
                family = self._producer_output_family_from_envelope(name, envelope) if ref not in private_names else family
                signal = self.runs.signal_for_output(ref, require_current=False) if self.runs else None
                values.append(InvocationArtifact(artifact_name=name, ref=ref, schema_id=envelope.schema_id,
                    media_type=envelope.media_type, size_bytes=envelope.size_bytes,
                    current=self.runs.input_is_current(instance_id=self._instance_id(), artifact_name=name, artifact_ref=ref) if self.runs else False,
                    parent_refs=envelope.parent_refs, labels=tuple(sorted(envelope.labels.items())),
                    historical=self._is_historical(envelope), handoff_verdict=signal.verdict if signal else None,
                    producer_run_id=family.producer_run_id if family else None,
                    producer_inputs=tuple((item.port_name,item.ref) for item in family.producer_inputs) if family else None))
            resolved[port.name] = tuple(values)
        for port in compiled.spec.inputs:
            bind(port)

    def _is_historical(self, envelope: Any) -> bool:
        operation_id = envelope.labels.get("operation_id")
        if operation_id is None:
            return False
        try:
            producer = self._operation_catalog.recorded_operation(operation_id, version=envelope.labels.get("operation_version"), digest=envelope.labels.get("operation_digest"))
        except KeyError:
            return True
        return False
