"""Display saved figure families without re-running scientific qualification."""
from collections.abc import Mapping

from scidiscovery.plugin_runtime.presentation import (
    add_fields, add_gap, empty, items, pointer, source,
)

MANIFEST = "scidiscovery.figure-evidence-manifest.v1"


def _intake_files(result, intake, artifacts):
    """Display already sealed extraction attachments, without new author fields."""
    identity = ("operation_id", "operation_version", "operation_digest")
    family = intake.get("family", {})
    if family.get("operation_id") != "science.evidence.extract.figure.v3":
        return
    # The bounded cohort cannot overrule an authoritative ownership lookup.
    # In particular, a truncated historical singleton is not a current manifest.
    if (family.get("presentation_manifest_scan_complete") is False
            or ("presentation_manifest_match_count" in family
                and family["presentation_manifest_match_count"] != 1)):
        add_gap(result, "figure_intake_manifest_missing_or_ambiguous", intake)
        return
    parents = [item.get("ref") for item in intake.get("provenance", ())]
    if not family.get("selected_run_id") and (
            intake.get("parent_count", len(parents)) > len(parents)
            or any(isinstance(ref, Mapping)
                and ref.get("schema_id") == "scidiscovery.tool-evidence-manifest.v1"
                and not any(a.get("ref") == ref for a in artifacts) for ref in parents)):
        # Ancestor reports may have no explicit owner projection. Their missing
        # manifest parents still forbid uniqueness inferred from a partial view.
        add_gap(result, "figure_intake_manifest_incomplete", intake)
        return
    manifests = [a for a in artifacts
        if a.get("schema_id") == "scidiscovery.tool-evidence-manifest.v1"
        and a.get("ref") in parents
        and a.get("family", {}).get("operation_output_port") == "recovery_manifest_output"
        and all(family.get(key) and a.get("family", {}).get(key) == family[key] for key in identity)
        and a.get("family", {}).get("tool_producer_run")
        and (not family.get("selected_run_id")
             or a["family"]["tool_producer_run"] == family["selected_run_id"])]
    if len(manifests) != 1:
        add_gap(result, "figure_intake_manifest_missing_or_ambiguous", intake)
        return
    manifest = manifests[0]
    payload = manifest.get("payload")
    if (not isinstance(payload, Mapping) or manifest.get("payload_state") != "available"
            or manifest.get("gaps") or manifest.get("family", {}).get("lookup_incomplete")):
        add_gap(result, "figure_intake_manifest_incomplete", manifest)
        return
    bindings = payload.get("bindings", {})
    if not isinstance(bindings, Mapping):
        add_gap(result, "figure_intake_manifest_incomplete", manifest)
        return
    records = items(payload.get("records"))
    parents = [item.get("ref") for item in manifest.get("provenance", ())]
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or record.get("media_type") not in {"image/png", "image/jpeg", "text/csv"}:
            continue
        metadata = record.get("metadata", {})
        if (record.get("tool_name") != "worker_curve_figure_save"
                or not isinstance(metadata, Mapping) or metadata.get("kind") != "figure_file"):
            continue
        alias, ref = record.get("alias"), record.get("artifact_ref")
        binding = bindings.get(alias) if isinstance(alias, str) else None
        matches = [a for a in artifacts if isinstance(ref, Mapping) and a.get("ref") == ref
                   and a.get("size_bytes") == record.get("size_bytes")
                   and a.get("media_type") == record.get("media_type")]
        if (not isinstance(binding, Mapping) or binding.get("artifact_ref") != ref
                or binding.get("port_name") != "tool_evidence" or parents.count(ref) != 1
                or sum(isinstance(r, Mapping) and r.get("alias") == alias for r in records) != 1
                or len(matches) != 1):
            add_gap(result, "figure_intake_member_missing_or_ambiguous", manifest, pointer("records", index))
            continue
        if record["media_type"] == "text/csv":
            result["sections"].append({"title": "提取曲线", "kind": "curve_evidence", "items": [{
                "label": "CSV 数据表", "value": metadata.get("data_item", alias), "source": source(matches[0])}]})
            continue
        result["figures"].append({"artifact_id": matches[0]["artifact_id"],
            "label": _figure_label(metadata.get("data_item", "")),
            "source": source(manifest, pointer("records", index))})


def _figure_label(data_item):
    if isinstance(data_item, str):
        if data_item.startswith("source_panels/"):
            return "论文原图"
        if data_item.endswith("--numeric-redraw.png"):
            return "CSV 数值重绘"
        if data_item.endswith("--identity-fidelity.png"):
            return "原图叠点检查"
    return "已保存审计图（用途未明确）"


def build_presentation(artifacts):
    result = empty()
    for artifact in artifacts:
        payload = artifact.get("payload")
        if artifact.get("schema_id") == "scidiscovery.scientific-intake.v1":
            _intake_files(result, artifact, artifacts)
        if artifact.get("schema_id") != MANIFEST or not isinstance(payload, Mapping):
            continue
        fingerprint = artifact.get("family", {}).get("operation_invocation_fingerprint")
        members = [a for a in artifacts if fingerprint and a.get("family", {}).get("operation_invocation_fingerprint") == fingerprint
                   and a.get("family", {}).get("transform_profile") == artifact.get("family", {}).get("transform_profile")
                   and a.get("provenance") == artifact.get("provenance")
                   and all(a.get("family", {}).get(key) == artifact.get("family", {}).get(key)
                           for key in ("operation_id", "operation_version", "operation_digest"))]
        provenance = payload.get("provenance")
        manifest_items = items(provenance.get("output_artifacts")) if isinstance(provenance, Mapping) else ()
        manifest_partial = any(str(g.get("source_pointer", "")).startswith("/provenance") for g in artifact.get("gaps", ()))
        if manifest_partial:
            add_gap(result, "figure_manifest_members_partial", artifact)
        mapped, seen_items = {}, set()
        for index, item in enumerate(manifest_items):
            if not isinstance(item, Mapping):
                continue
            key, collection, media = item.get("data_item"), item.get("collection"), item.get("media_type")
            if isinstance(key, str):
                if key in seen_items:
                    mapped[key] = None
                    add_gap(result, "figure_data_item_ambiguous", artifact)
                    continue
                seen_items.add(key)
            if (not isinstance(collection, str) or not isinstance(media, str) or collection not in {"curve_tables", "source_panels", "audit_overlays"}
                    or not isinstance(key, str) or not key.startswith(collection + "/") or not key[len(collection)+1:]
                    or (collection == "curve_tables" and media != "text/csv")
                    or (collection != "curve_tables" and media not in {"image/png", "image/jpeg"})):
                add_gap(result, "figure_member_metadata_unreadable", artifact, pointer("provenance", "output_artifacts", index))
                continue
            matches = [a for a in members if a.get("ref", {}).get("sha256") == item.get("sha256")
                       and a.get("size_bytes") == item.get("bytes") and a.get("media_type") == item.get("media_type")
                       and a.get("family", {}).get("operation_output_port") == item.get("collection")]
            if len(matches) != 1 or manifest_partial or artifact.get("family", {}).get("lookup_incomplete"):
                add_gap(result, "figure_member_missing_or_ambiguous", artifact,
                        pointer("provenance", "output_artifacts", index), data_item=item.get("data_item"))
                continue
            if key in mapped:
                mapped[key] = None
                add_gap(result, "figure_data_item_ambiguous", artifact)
            else:
                mapped[key] = matches[0]
        add_fields(result, artifact, "论文曲线提取", (("source", "论文原图出处"),), kind="curve_evidence")
        for i, panel in enumerate(items(payload.get("panels"))):
            if not isinstance(panel, Mapping):
                continue
            add_fields(result, artifact, "论文曲线提取", (("panel_key", "面板"), ("citation", "文献引用"),
                ("axis_calibration", "原记录坐标标定")), payload=panel, path=pointer("panels", i), kind="curve_evidence")
            for j, series in enumerate(items(panel.get("series"))):
                if not isinstance(series, Mapping):
                    continue
                add_fields(result, artifact, "提取曲线", (("label", "曲线名称"), ("point_count", "登记采样点数"),
                    ("binding", "原图身份依据")), payload=series, path=pointer("panels", i, "series", j), kind="curve_evidence")
                data_item = series.get("data_item")
                table = mapped.get(data_item) if isinstance(data_item, str) else None
                if table is not None and table.get("media_type") == "text/csv":
                    result["sections"].append({"title": "提取曲线", "kind": "curve_evidence", "items": [{
                        "label": "CSV 数据表", "value": series.get("data_item"), "source": source(table)}]})
        redraw = False
        for data_item, member in mapped.items():
            if member is None or member.get("media_type") not in {"image/png", "image/jpeg"}:
                continue
            label = _figure_label(data_item)
            redraw = redraw or label == "CSV 数值重绘"
            result["figures"].append({"artifact_id": member["artifact_id"], "label": label, "source": source(member)})
        if not redraw:
            add_gap(result, "numeric_redraw_not_saved_or_not_readable", artifact)
    result["figures"].sort(key=lambda item: {"论文原图": 0, "CSV 数值重绘": 1, "原图叠点检查": 2}.get(item["label"], 3))
    return result


build_presentation.display_pointers = {
    MANIFEST: ("/source", "/panels", "/provenance/output_artifacts"),
    "scidiscovery.tool-evidence-manifest.v1": ("/records", "/bindings"),
}
build_presentation.family_schemas = (MANIFEST,)
