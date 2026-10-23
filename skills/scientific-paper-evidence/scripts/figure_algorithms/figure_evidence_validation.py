"""Deterministic integrity validation for curve-figure evidence bundles."""

from __future__ import annotations
from .common import Sha256, canonical_json

import csv
import hashlib
import io
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from PIL import Image



VALIDATOR_VERSION = "7"
REPORT_SCHEMA_VERSION = "scidiscovery.figure-evidence-validation-report.v1"
MANIFEST_SCHEMA_VERSION = "scidiscovery.figure-evidence-manifest.v1"

STANDARD_CURVE_COLUMNS = (
    "panel_key",
    "series_key",
    "point_index",
    "pixel_x_raw",
    "pixel_y_raw",
    "pixel_x_subpixel",
    "pixel_y_subpixel",
    "x_value",
    "y_value",
    "uncertainty_px",
    "x_uncertainty",
    "y_uncertainty",
    "observed",
)
EXTENDED_CURVE_COLUMNS = (
    "panel_key",
    "series_key",
    "point_index",
    "pixel_x",
    "pixel_y",
    "observed",
)


class FigureEvidenceBundleError(ValueError):
    """Raised when evidence bytes do not satisfy their mechanical contract."""


def build_figure_evidence_validation_report(
    *,
    manifest_data_item: str,
    manifest_content: bytes,
    sibling_files: Mapping[str, tuple[bytes, str]],
    validator_version: str = VALIDATOR_VERSION,
) -> dict[str, Any]:
    """Validate exact bundle bytes and return their canonical derived metrics."""

    if validator_version not in {"6", "7"}:
        raise FigureEvidenceBundleError("unsupported saved validator version")
    manifest = _json_object(manifest_content, manifest_data_item)
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise FigureEvidenceBundleError("unsupported figure evidence manifest version")
    figure_key = _required_string(manifest, "figure_key", manifest_data_item)
    source_status = manifest.get("status")
    if source_status not in {"qualified", "unresolved"}:
        raise FigureEvidenceBundleError("manifest status must be qualified or unresolved")
    source = _required_mapping(manifest, "source", manifest_data_item)
    width = _positive_int(source.get("width"), "manifest source width")
    height = _positive_int(source.get("height"), "manifest source height")

    panels = manifest.get("panels")
    if not isinstance(panels, list) or not panels:
        raise FigureEvidenceBundleError("manifest panels must be a non-empty array")
    series_by_item: dict[str, tuple[str, str, Mapping[str, Any], Mapping[str, Any]]] = {}
    ambiguous_series = {
        (item.get("panel_key"), item.get("series_key"))
        for item in manifest.get("ambiguities", [])
        if isinstance(item, dict)
    }
    declared_shared: dict[tuple[str, str, int], tuple[str, str, str]] = {}
    declared_coincident: dict[tuple[str, str, int], tuple[str, ...]] = {}
    overdraw_intervals: list[
        tuple[str, str, int, int, str, tuple[str, ...], float]
    ] = []
    coincident_intervals: list[
        tuple[str, str, int, int, tuple[str, ...], float]
    ] = []
    for panel in panels:
        if not isinstance(panel, dict):
            raise FigureEvidenceBundleError("manifest panel must be an object")
        panel_key = _required_string(panel, "panel_key", "manifest panel")
        axes = _required_mapping(panel, "axis_calibration", panel_key)
        series_values = panel.get("series")
        if not isinstance(series_values, list) or not series_values:
            raise FigureEvidenceBundleError(f"panel {panel_key} has no series")
        for series in series_values:
            if not isinstance(series, dict):
                raise FigureEvidenceBundleError("manifest series must be an object")
            series_key = _required_string(series, "series_key", panel_key)
            data_item = _required_string(series, "data_item", series_key)
            if data_item in series_by_item:
                raise FigureEvidenceBundleError("manifest series data items must be unique")
            series_by_item[data_item] = (panel_key, series_key, series, axes)
        known_series = {
            _required_string(item, "series_key", panel_key)
            for item in series_values
            if isinstance(item, dict)
        }
        shared_values = panel.get("shared_support", [])
        if not isinstance(shared_values, list):
            raise FigureEvidenceBundleError("manifest shared_support must be an array")
        for shared in shared_values:
            if not isinstance(shared, dict):
                raise FigureEvidenceBundleError("manifest shared support must be an object")
            group = _required_string(shared, "group_key", panel_key)
            ranges = shared.get("pixel_ranges")
            mode = shared.get("mode")
            if not isinstance(ranges, list) or not ranges:
                raise FigureEvidenceBundleError("manifest shared support requires ranges")
            if mode == "overdraw":
                donor = _required_string(shared, "visible_series", group)
                covered = shared.get("covered_series")
                max_distance = shared.get("max_endpoint_distance_px")
                if (
                    donor not in known_series
                    or not isinstance(covered, list)
                    or not covered
                    or len(covered) != len(set(covered))
                    or not all(
                        isinstance(item, str) and item in known_series
                        for item in covered
                    )
                    or donor in covered
                    or not isinstance(max_distance, (int, float))
                    or isinstance(max_distance, bool)
                    or not math.isfinite(max_distance)
                    or max_distance < 0
                ):
                    raise FigureEvidenceBundleError(
                        "manifest shared support references invalid series"
                    )
                members = (donor, *covered)
            elif mode == "coincident_overlap":
                member_values = shared.get("member_series")
                max_distance = shared.get("max_member_distance_px")
                if (
                    not isinstance(member_values, list)
                    or len(member_values) < 2
                    or len(member_values) != len(set(member_values))
                    or not all(
                        isinstance(item, str) and item in known_series
                        for item in member_values
                    )
                    or not isinstance(max_distance, (int, float))
                    or isinstance(max_distance, bool)
                    or not math.isfinite(max_distance)
                    or max_distance < 0
                ):
                    raise FigureEvidenceBundleError(
                        "manifest coincident overlap references invalid series"
                    )
                members = tuple(member_values)
            else:
                raise FigureEvidenceBundleError("manifest shared support mode is invalid")
            for interval in ranges:
                if (
                    not isinstance(interval, list)
                    or len(interval) != 2
                    or not all(isinstance(item, int) and not isinstance(item, bool) for item in interval)
                    or not 0 <= interval[0] < interval[1] <= width
                ):
                    raise FigureEvidenceBundleError("manifest shared-support range is invalid")
                if mode == "coincident_overlap":
                    coincident_intervals.append(
                        (
                            panel_key,
                            group,
                            interval[0],
                            interval[1],
                            members,
                            float(max_distance),
                        )
                    )
                else:
                    overdraw_intervals.append(
                        (
                            panel_key,
                            group,
                            interval[0],
                            interval[1],
                            donor,
                            tuple(covered),
                            float(max_distance),
                        )
                    )
                for pixel_x in range(interval[0], interval[1]):
                    if mode == "overdraw":
                        for member, kind in (
                            (donor, "direct_pixel"),
                            *((item, "shared_occlusion") for item in covered),
                        ):
                            key = (panel_key, member, pixel_x)
                            if key in declared_shared:
                                raise FigureEvidenceBundleError(
                                    "manifest shared-support ranges overlap"
                                )
                            declared_shared[key] = (group, donor, kind)
                    else:
                        group_key = (panel_key, group, pixel_x)
                        if group_key in declared_coincident or any(
                            (panel_key, member, pixel_x) in declared_shared
                            for member in members
                        ):
                            raise FigureEvidenceBundleError(
                                "manifest shared-support ranges overlap"
                            )
                        declared_coincident[group_key] = members

    declared = _declared_artifacts(manifest)
    if set(declared) != set(sibling_files):
        raise FigureEvidenceBundleError(
            "figure manifest provenance must exactly enumerate evidence sibling files"
        )
    for data_item, (content, media_type) in sibling_files.items():
        record = declared[data_item]
        if record.get("bytes") != len(content):
            raise FigureEvidenceBundleError(
                f"manifest byte count does not match sibling file: {data_item}"
            )
        if record.get("sha256") != _sha256(content):
            raise FigureEvidenceBundleError(
                f"manifest SHA-256 does not match sibling file: {data_item}"
            )
        if _base_media_type(record.get("media_type")) != _base_media_type(media_type):
            raise FigureEvidenceBundleError(
                f"manifest media type does not match sibling file: {data_item}"
            )
    source_sha256 = _required_string(source, "image_sha256", "manifest source")
    source_images = tuple(
        content
        for name, (content, _) in sorted(sibling_files.items())
        if name.startswith("source_panels/") and _sha256(content) == source_sha256
    )
    if not source_images:
        raise FigureEvidenceBundleError(
            "manifest source image SHA-256 has no matching source-panel file"
        )
    try:
        source_image = Image.open(io.BytesIO(source_images[0])).convert("RGB")
    except Exception as error:
        raise FigureEvidenceBundleError("manifest source panel is not a valid image") from error
    if source_image.size != (width, height):
        raise FigureEvidenceBundleError(
            "manifest source dimensions differ from the source-panel image"
        )

    missing_series = set(series_by_item) - set(sibling_files)
    if missing_series:
        raise FigureEvidenceBundleError(
            f"manifest series CSV is missing: {sorted(missing_series)[0]}"
        )

    report_series: list[dict[str, Any]] = []
    supporting_tables: list[dict[str, Any]] = []
    all_pixels: Counter[tuple[float, float]] = Counter()
    pixel_series: dict[tuple[float, float], set[tuple[str, str]]] = defaultdict(set)
    pixel_ownership: dict[tuple[float, float], list[tuple[str, str]]] = defaultdict(list)
    scientific_roles: Counter[str] = Counter()
    observed_rows = 0
    eligible_rows = 0
    below_limit_rows = 0
    eligibility_tables = 0
    detection_limit_tables = 0
    direct_rows = 0
    shared_rows = 0
    actual_shared: dict[
        tuple[str, str, int],
        tuple[tuple[float, float], tuple[int, int], str, str, str, int],
    ] = {}
    direct_points: dict[tuple[str, str], dict[int, tuple[float, float]]] = {}
    series_by_identity = {
        (panel_key, series_key): series
        for panel_key, series_key, series, _axes in series_by_item.values()
    }

    for data_item, (panel_key, series_key, series, axes) in sorted(
        series_by_item.items()
    ):
        content, media_type = sibling_files[data_item]
        if _base_media_type(media_type) != "text/csv":
            raise FigureEvidenceBundleError(f"series data item is not CSV: {data_item}")
        table = _validate_curve_csv(
            data_item=data_item,
            content=content,
            panel_key=panel_key,
            series_key=series_key,
            primitive_kind=series.get("primitive_kind"),
            axes=axes,
            width=width,
            height=height,
            scientific_order=False,
            use_limits=series.get("use_limits", []),
            exclusion_regions=series.get("exclusion_regions", []),
        )
        if validator_version == "7" and table["identity_resolved_row_count"] is not None:
            for raw_pixel in table["direct_raw_pixels"]:
                if not _source_pixel_matches_series(source_image, raw_pixel, series):
                    raise FigureEvidenceBundleError(f"direct observation lacks its descriptor-colored source pixel: {data_item} at {raw_pixel}")
        binding = series.get("binding")
        binding_unresolved = (
            not isinstance(binding, dict) or binding.get("status") != "matched"
        )
        if (
            (binding_unresolved or (panel_key, series_key) in ambiguous_series)
            and (table["eligible_row_count"] or 0) > 0
        ):
            raise FigureEvidenceBundleError(
                "an unresolved figure series cannot contain eligible rows: "
                f"{data_item}"
            )
        declared_count = _nonnegative_int(
            series.get("point_count"), f"manifest point_count for {data_item}"
        )
        if table["row_count"] != declared_count:
            raise FigureEvidenceBundleError(
                f"manifest point_count does not match CSV rows: {data_item}"
            )
        for manifest_field, table_field in (
            ("identity_resolved_point_count", "identity_resolved_row_count"),
            ("interpolation_eligible_point_count", "interpolation_eligible_row_count"),
        ):
            if manifest_field in series and series[manifest_field] != table[table_field]:
                raise FigureEvidenceBundleError(f"manifest {manifest_field} does not match CSV flags: {data_item}")
        report_series.append(
            {
                "panel_key": panel_key,
                "series_key": series_key,
                "data_item": data_item,
                "csv_sha256": _sha256(content),
                "row_count": table["row_count"],
                "observed_row_count": table["observed_row_count"],
                "eligible_row_count": table["eligible_row_count"],
                "below_detection_limit_row_count": table[
                    "below_detection_limit_row_count"
                ],
                "point_index_min": table["point_index_min"],
                "point_index_max": table["point_index_max"],
                "point_index_gap_count": table["point_index_gap_count"],
                "unique_pixel_count": len(table["pixels"]),
                "duplicate_pixel_row_count": (
                    table["row_count"] - len(table["pixels"])
                ),
                "direct_row_count": table["direct_row_count"],
                "shared_row_count": table["shared_row_count"],
                **({field: table[field] for field in ("shared_pixel_row_count", "shared_observation_row_count",
                    "identity_resolved_row_count", "interpolation_eligible_row_count")} if validator_version == "7" else {}),
            }
        )
        observed_rows += table["observed_row_count"]
        eligible_rows += table["eligible_row_count"] or 0
        below_limit_rows += table["below_detection_limit_row_count"] or 0
        eligibility_tables += table["eligible_row_count"] is not None
        detection_limit_tables += table["below_detection_limit_row_count"] is not None
        direct_rows += table["direct_row_count"]
        shared_rows += table["shared_row_count"]
        all_pixels.update(table["pixel_rows"])
        for pixel in table["pixels"]:
            pixel_series[pixel].add((panel_key, series_key))
        for pixel, ownership, shared_group in table["owned_pixels"]:
            pixel_ownership[pixel].append((ownership, shared_group))
        scientific_roles.update(table["scientific_roles"])
        direct_points[(panel_key, series_key)] = table["direct_points"]
        for raw_x, raw_pixel, pixel, kind, group, source, eligible in table[
            "support_rows"
        ]:
            actual_shared[(panel_key, series_key, raw_x)] = (
                pixel,
                raw_pixel,
                kind,
                group,
                source,
                eligible,
            )

    _validate_shared_ownership(pixel_series, pixel_ownership, all_pixels)
    _validate_declared_shared_support(
        declared_shared,
        declared_coincident,
        overdraw_intervals,
        coincident_intervals,
        actual_shared,
        direct_points,
        series_by_identity,
        source_image,
    )

    for data_item, (content, media_type) in sorted(sibling_files.items()):
        if data_item in series_by_item or _base_media_type(media_type) != "text/csv":
            continue
        supporting_tables.append(
            {
                "data_item": data_item,
                "csv_sha256": _sha256(content),
                "row_count": _validate_supporting_csv(data_item, content),
            }
        )

    total_rows = sum(item["row_count"] for item in report_series)
    metrics = manifest.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("total_point_count") != total_rows:
        raise FigureEvidenceBundleError(
            "manifest metrics.total_point_count does not match CSV rows"
        )
    unique_pixels = len(all_pixels)
    input_records = [
        {
            "data_item": manifest_data_item,
            "bytes": len(manifest_content),
            "sha256": _sha256(manifest_content),
            "media_type": "application/json",
        },
        *[
            {
                "data_item": name,
                "bytes": len(content),
                "sha256": _sha256(content),
                "media_type": _base_media_type(media_type),
            }
            for name, (content, media_type) in sorted(sibling_files.items())
        ],
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "validator_version": validator_version,
        "integrity_status": "valid",
        "figure_key": figure_key,
        "source_status": source_status,
        "manifest_sha256": _sha256(manifest_content),
        "bundle_fingerprint_sha256": _sha256(canonical_json(input_records)),
        "validated_artifact_count": len(input_records),
        "series": report_series,
        "supporting_tables": supporting_tables,
        "scientific_role_counts": [
            {"scientific_role": name, "row_count": count}
            for name, count in sorted(scientific_roles.items())
        ],
        "metrics": {
            "curve_table_count": len(report_series),
            "total_curve_rows": total_rows,
            "observed_curve_rows": observed_rows,
            "eligibility_flagged_table_count": eligibility_tables,
            "eligible_curve_rows": eligible_rows,
            "detection_limit_flagged_table_count": detection_limit_tables,
            "below_detection_limit_curve_rows": below_limit_rows,
            "direct_pixel_curve_rows": direct_rows,
            "shared_occlusion_curve_rows": shared_rows,
            **({metric: sum(item[field] or 0 for item in report_series) for metric, field in (
                ("shared_pixel_curve_rows", "shared_pixel_row_count"),
                ("shared_observation_curve_rows", "shared_observation_row_count"),
                ("identity_resolved_curve_rows", "identity_resolved_row_count"),
                ("interpolation_eligible_curve_rows", "interpolation_eligible_row_count"))}
                if validator_version == "7" else {}),
            "global_unique_pixel_count": unique_pixels,
            "global_duplicate_pixel_rows": total_rows - unique_pixels,
            "cross_series_shared_pixel_count": sum(
                len(owners) > 1 for owners in pixel_series.values()
            ),
        },
    }


def load_evidence_package(package_dir: Any) -> tuple[str, bytes, dict[str, tuple[bytes, str]]]:
    """Read the bounded four-collection package used by the Skill CLI."""

    from pathlib import Path

    root = Path(package_dir).expanduser().resolve()
    manifest_dir = root / "figure_manifest"
    manifests = sorted(manifest_dir.glob("*.json")) if manifest_dir.is_dir() else []
    if len(manifests) != 1:
        raise FigureEvidenceBundleError(
            "evidence package must contain exactly one figure_manifest JSON file"
        )
    manifest_path = manifests[0]
    files: dict[str, tuple[bytes, str]] = {}
    allowed = {"source_panels", "audit_overlays", "curve_tables"}
    for collection in sorted(allowed):
        directory = root / collection
        if not directory.is_dir():
            raise FigureEvidenceBundleError(
                f"evidence package collection is missing: {collection}"
            )
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise FigureEvidenceBundleError(
                    f"evidence package items must be regular files: {path.name}"
                )
            data_item = f"{collection}/{path.name}"
            files[data_item] = (path.read_bytes(), _media_type(path.suffix))
    return (
        f"figure_manifest/{manifest_path.name}",
        manifest_path.read_bytes(),
        files,
    )


def _validate_curve_csv(
    *,
    data_item: str,
    content: bytes,
    panel_key: str,
    series_key: str,
    primitive_kind: object,
    axes: Mapping[str, Any],
    width: int,
    height: int,
    scientific_order: bool = False,
    use_limits: list | tuple = (),
    exclusion_regions: list | tuple = (),
) -> dict[str, Any]:
    fieldnames, rows = _csv_rows(data_item, content)
    standard = all(name in fieldnames for name in STANDARD_CURVE_COLUMNS)
    extended = all(name in fieldnames for name in EXTENDED_CURVE_COLUMNS)
    if not standard and not extended:
        raise FigureEvidenceBundleError(
            f"curve CSV lacks the canonical pixel/identity columns: {data_item}"
        )
    x_name = "pixel_x_subpixel" if standard else "pixel_x"
    y_name = "pixel_y_subpixel" if standard else "pixel_y"
    pixels: set[tuple[float, float]] = set()
    pixel_rows: list[tuple[float, float]] = []
    owned_pixels: list[tuple[tuple[float, float], str, str]] = []
    roles: Counter[str] = Counter()
    observed = 0
    eligible = 0
    below_limit = 0
    has_eligible = "quantitative_measurement_claim_eligible" in fieldnames
    has_below_limit = "below_sims_detection_limit" in fieldnames
    has_eligibility_reason = "eligibility_reason" in fieldnames
    has_ownership = "coordinate_ownership" in fieldnames
    support_provenance_fields = {"support_kind", "support_source_series"}
    has_support = support_provenance_fields.issubset(fieldnames) and has_ownership
    if support_provenance_fields.intersection(fieldnames) and not has_support:
        raise FigureEvidenceBundleError(
            f"curve CSV support provenance fields must be supplied together: {data_item}"
        )
    point_indices: list[int] = []
    previous_x: float | None = None
    previous_raw_x: int | None = None
    previous_point_index: int | None = None
    if has_ownership and "shared_group" not in fieldnames:
        raise FigureEvidenceBundleError(
            f"coordinate_ownership requires shared_group: {data_item}"
        )
    support_rows: list[
        tuple[
            int,
            tuple[int, int],
            tuple[float, float],
            str,
            str,
            str,
            int,
        ]
    ] = []
    direct_points: dict[int, tuple[float, float]] = {}
    direct_raw_pixels = []
    direct_rows = 0
    shared_rows = 0
    shared_pixel_rows = shared_observation_rows = 0
    identity_resolved_rows = interpolation_rows = 0
    has_point_permissions = {"identity_resolved", "interpolation_eligible"}.issubset(fieldnames)
    if not isinstance(exclusion_regions, (list, tuple)) or len(exclusion_regions) > 256:
        raise FigureEvidenceBundleError(f"effective exclusion regions must be a bounded array: {data_item}")
    for box in exclusion_regions:
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(type(value) is int for value in box)
                or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height):
            raise FigureEvidenceBundleError(f"effective exclusion region is invalid: {data_item}")
    if not isinstance(use_limits, (list, tuple)):
        raise FigureEvidenceBundleError(f"scientific use limits must be an array: {data_item}")
    for limit in use_limits:
        box = limit.get("pixel_bbox") if isinstance(limit, dict) else None
        if (not isinstance(box, (list, tuple)) or len(box) != 4
                or not all(type(value) is int for value in box)
                or not 0 <= box[0] < box[2] <= width or not 0 <= box[1] < box[3] <= height
                or limit.get("applies_to") not in {"quantitative_measurement", "interpolation", "all"}
                or not isinstance(limit.get("reason"), str) or not limit["reason"].strip()):
            raise FigureEvidenceBundleError(f"scientific use limit is invalid: {data_item}")
    if use_limits and "use_limit_indices" not in fieldnames:
        raise FigureEvidenceBundleError(f"scientific use limits require their CSV associations: {data_item}")
    if {"identity_resolved", "interpolation_eligible"}.intersection(fieldnames) and not has_point_permissions:
        raise FigureEvidenceBundleError(f"curve row identity/interpolation flags must be supplied together: {data_item}")
    if not has_eligible:
        raise FigureEvidenceBundleError(
            "curve CSV requires explicit quantitative eligibility: "
            f"{data_item}"
        )
    for index, row in enumerate(rows):
        if row["panel_key"] != panel_key or row["series_key"] != series_key:
            raise FigureEvidenceBundleError(
                f"CSV identity does not match manifest series: {data_item} row {index + 2}"
            )
        point_index = _parse_int(row["point_index"], "point_index", data_item)
        if point_index < 0:
            raise FigureEvidenceBundleError(
                f"CSV point_index must be non-negative: {data_item}"
            )
        if point_indices and point_index <= point_indices[-1]:
            raise FigureEvidenceBundleError(
                f"CSV point_index must be strictly increasing: {data_item}"
            )
        point_indices.append(point_index)
        x = _parse_finite(row[x_name], x_name, data_item)
        y = _parse_finite(row[y_name], y_name, data_item)
        if (
            not standard
            and primitive_kind in {"line", "fit_segment"}
            and previous_x is not None
        ):
            x_delta = x - previous_x
            index_delta = point_index - int(previous_point_index)
            if x_delta <= 0:
                raise FigureEvidenceBundleError(
                    f"line CSV source pixel columns must be strictly increasing: {data_item}"
                )
            # Historical extended tables may use a deliberately sparse index
            # even for adjacent pixels.  That is conservative because it only
            # splits a rendered run.  The unsafe inverse is forbidden: a
            # multi-column pixel jump cannot be hidden behind adjacent indices.
            if x_delta > 1.0 + 1e-6 and index_delta == 1:
                raise FigureEvidenceBundleError(
                    "line CSV point_index gaps must preserve exact source pixel-column gaps: "
                    f"{data_item}"
                )
        if not 0.0 <= x < width or not 0.0 <= y < height:
            raise FigureEvidenceBundleError(
                f"CSV pixel coordinate exceeds source bounds: {data_item}"
            )
        pixel = (x, y)
        pixels.add(pixel)
        pixel_rows.append(pixel)
        observed_flag = _parse_flag(row["observed"], "observed", data_item)
        actual_pixels = [(x, y)]
        if standard:
            actual_pixels.append((_parse_int(row["pixel_x_raw"], "pixel_x_raw", data_item),
                _parse_int(row["pixel_y_raw"], "pixel_y_raw", data_item)))
        if observed_flag and any(left <= px < right and top <= py < bottom
                for left, top, right, bottom in exclusion_regions for px, py in actual_pixels):
            raise FigureEvidenceBundleError(f"observed curve row lies in an exclusion region: {data_item} row {index + 2}")
        eligible_flag = _parse_flag(
            row["quantitative_measurement_claim_eligible"],
            "quantitative_measurement_claim_eligible",
            data_item,
        )
        below_limit_flag = (
            _parse_flag(
                row["below_sims_detection_limit"],
                "below_sims_detection_limit",
                data_item,
            )
            if has_below_limit
            else 0
        )
        if eligible_flag and not observed_flag:
            raise FigureEvidenceBundleError(
                f"eligible curve row must be observed: {data_item}"
            )
        if eligible_flag and below_limit_flag:
            raise FigureEvidenceBundleError(
                f"below-limit curve row cannot be eligible: {data_item}"
            )
        if has_point_permissions:
            identity = _parse_flag(row["identity_resolved"], "identity_resolved", data_item)
            interpolation = _parse_flag(row["interpolation_eligible"], "interpolation_eligible", data_item)
            if (eligible_flag and not identity) or (interpolation and not eligible_flag):
                raise FigureEvidenceBundleError(f"row eligibility requires resolved identity; interpolation requires eligibility: {data_item} row {index + 2}")
            identity_resolved_rows += identity
            interpolation_rows += interpolation
            if row.get("interpolation_reason", "") and interpolation:
                raise FigureEvidenceBundleError(f"interpolation-eligible row cannot declare a restriction reason: {data_item} row {index + 2}")
            if "interpolation_reason" in fieldnames and not interpolation and not row["interpolation_reason"]:
                raise FigureEvidenceBundleError(f"restricted interpolation requires a reason: {data_item} row {index + 2}")
        if "use_limit_indices" in fieldnames:
            indices = [index for index, limit in enumerate(use_limits)
                if limit["pixel_bbox"][0] <= x < limit["pixel_bbox"][2]
                and limit["pixel_bbox"][1] <= y < limit["pixel_bbox"][3]]
            if row["use_limit_indices"] != "|".join(str(index) for index in indices):
                raise FigureEvidenceBundleError(f"CSV use limits differ from manifest geometry: {data_item} row {index + 2}")
            restrictions = {use_limits[index]["applies_to"] for index in indices}
            if (eligible_flag and restrictions & {"all", "quantitative_measurement"}) or (
                    has_point_permissions and interpolation and restrictions & {"all", "interpolation", "quantitative_measurement"}):
                raise FigureEvidenceBundleError(f"CSV permissions exceed scientific use limits: {data_item} row {index + 2}")
        if "pixel_y_support_min" in fieldnames or "pixel_y_support_max" in fieldnames:
            low = _parse_finite(row.get("pixel_y_support_min", ""), "pixel_y_support_min", data_item)
            high = _parse_finite(row.get("pixel_y_support_max", ""), "pixel_y_support_max", data_item)
            if not 0 <= low <= y <= high < height:
                raise FigureEvidenceBundleError(f"pixel support bounds must contain the observation: {data_item} row {index + 2}")
            if "pixel_y_support_ranges" in fieldnames:
                try:
                    ranges = json.loads(row["pixel_y_support_ranges"])
                    valid_ranges = (isinstance(ranges, list) and ranges and all(
                        isinstance(pair, list) and len(pair) == 2 and all(type(value) in {int, float} for value in pair)
                        and 0 <= pair[0] <= pair[1] < height for pair in ranges))
                    contains = valid_ranges and any(start <= y <= stop for start, stop in ranges)
                    envelope_matches = valid_ranges and min(pair[0] for pair in ranges) == low and max(pair[1] for pair in ranges) == high
                except (ValueError, TypeError) as error:
                    raise FigureEvidenceBundleError(f"pixel support ranges are invalid: {data_item} row {index + 2}") from error
                if not contains or not envelope_matches:
                    raise FigureEvidenceBundleError(f"pixel support ranges must preserve candidate bands and match their bounds: {data_item} row {index + 2}")
        if has_eligibility_reason:
            eligibility_reason = row["eligibility_reason"].strip()
            if eligibility_reason and (
                len(eligibility_reason) > 64
                or not eligibility_reason[0].isalpha()
                or not all(
                    character.islower()
                    or character.isdigit()
                    or character == "_"
                    for character in eligibility_reason
                )
            ):
                raise FigureEvidenceBundleError(
                    f"eligibility_reason must be a lowercase key: {data_item}"
                )
            if eligible_flag and eligibility_reason:
                raise FigureEvidenceBundleError(
                    f"eligible curve row cannot declare an eligibility_reason: {data_item}"
                )
            if not eligible_flag and not eligibility_reason:
                raise FigureEvidenceBundleError(
                    f"ineligible curve row requires an eligibility_reason: {data_item}"
                )
            if below_limit_flag and eligibility_reason != "below_detection_limit":
                raise FigureEvidenceBundleError(
                    f"below-limit curve row requires below_detection_limit reason: {data_item}"
                )
        observed += observed_flag
        eligible += eligible_flag
        below_limit += below_limit_flag
        if "scientific_role" in fieldnames:
            role = row["scientific_role"].strip()
            if not role:
                raise FigureEvidenceBundleError(
                    f"scientific_role must not be empty: {data_item}"
                )
            roles[role] += 1
        if has_ownership:
            ownership = row["coordinate_ownership"].strip()
            shared_group = row["shared_group"].strip()
            if ownership not in {"exclusive", "shared"}:
                raise FigureEvidenceBundleError(
                    f"coordinate_ownership must be exclusive or shared: {data_item}"
                )
            if (ownership == "shared") != bool(shared_group):
                raise FigureEvidenceBundleError(
                    f"shared coordinate ownership requires exactly one shared_group: {data_item}"
                )
            owned_pixels.append((pixel, ownership, shared_group))
            shared_observation_rows += ownership == "shared"
        raw_x = (
            _parse_int(row["pixel_x_raw"], "pixel_x_raw", data_item)
            if standard
            else int(round(x))
        )
        raw_y = (
            _parse_int(row["pixel_y_raw"], "pixel_y_raw", data_item)
            if standard
            else int(round(y))
        )
        if has_support:
            support_kind = row["support_kind"].strip()
            support_source = row["support_source_series"].strip()
            if support_kind not in {"direct_pixel", "shared_occlusion", "shared_pixel"}:
                raise FigureEvidenceBundleError(
                    f"support_kind must be direct_pixel or shared_occlusion: {data_item}"
                )
            if support_kind in {"shared_occlusion", "shared_pixel"}:
                if ownership != "shared" or not shared_group:
                    raise FigureEvidenceBundleError(
                        f"shared occlusion requires shared ownership: {data_item}"
                    )
                if not support_source or support_source == series_key:
                    raise FigureEvidenceBundleError(
                        f"shared occlusion requires a different support source series: {data_item}"
                    )
                shared_rows += support_kind == "shared_occlusion"
                shared_pixel_rows += support_kind == "shared_pixel"
            else:
                if support_source and support_source != series_key:
                    raise FigureEvidenceBundleError(
                        f"direct support source must be its own series: {data_item}"
                    )
                direct_rows += 1
            if support_kind == "direct_pixel":
                direct_points[raw_x] = pixel
                if observed_flag:
                    direct_raw_pixels.append((raw_x, raw_y))
            if ownership == "shared":
                support_rows.append(
                    (
                        raw_x,
                        (raw_x, raw_y),
                        pixel,
                        support_kind,
                        shared_group,
                        support_source,
                        eligible_flag,
                    )
                )
        else:
            direct_rows += 1
            direct_points[raw_x] = pixel
        if standard:
            if primitive_kind in {"line", "fit_segment"} and previous_raw_x is not None:
                direction = -1 if scientific_order and _axis_value(axes["x"], 1) < _axis_value(axes["x"], 0) else 1
                raw_delta = (raw_x - previous_raw_x) * direction
                index_delta = point_index - int(previous_point_index)
                if raw_delta <= 0:
                    raise FigureEvidenceBundleError(
                        f"line CSV source pixel columns must be strictly increasing: {data_item}"
                    )
                if index_delta != raw_delta:
                    raise FigureEvidenceBundleError(
                        "line CSV point_index gaps must preserve exact source pixel-column gaps: "
                        f"{data_item}"
                    )
            previous_raw_x = raw_x
            previous_point_index = point_index
            _validate_standard_row(row, data_item, axes, width, height)
        else:
            _validate_extended_numeric_fields(row, fieldnames, data_item)
            previous_x = x
            previous_point_index = point_index
    return {
        "row_count": len(rows),
        "observed_row_count": observed,
        "eligible_row_count": eligible if has_eligible else None,
        "below_detection_limit_row_count": below_limit if has_below_limit else None,
        "point_index_min": point_indices[0] if point_indices else None,
        "point_index_max": point_indices[-1] if point_indices else None,
        "point_index_gap_count": (
            point_indices[-1] - point_indices[0] + 1 - len(point_indices)
            if point_indices
            else 0
        ),
        "pixels": pixels,
        "pixel_rows": pixel_rows,
        "owned_pixels": owned_pixels,
        "scientific_roles": roles,
        "direct_row_count": direct_rows,
        "shared_row_count": shared_rows,
        "shared_pixel_row_count": shared_pixel_rows,
        "shared_observation_row_count": shared_observation_rows,
        "identity_resolved_row_count": identity_resolved_rows if has_point_permissions else None,
        "interpolation_eligible_row_count": interpolation_rows if has_point_permissions else None,
        "support_rows": support_rows,
        "direct_points": direct_points,
        "direct_raw_pixels": direct_raw_pixels,
    }


def _validate_standard_row(
    row: Mapping[str, str],
    data_item: str,
    axes: Mapping[str, Any],
    width: int,
    height: int,
) -> None:
    raw_x = _parse_int(row["pixel_x_raw"], "pixel_x_raw", data_item)
    raw_y = _parse_int(row["pixel_y_raw"], "pixel_y_raw", data_item)
    if not 0 <= raw_x < width or not 0 <= raw_y < height:
        raise FigureEvidenceBundleError(
            f"CSV raw pixel coordinate exceeds source bounds: {data_item}"
        )
    x = _parse_finite(row["pixel_x_subpixel"], "pixel_x_subpixel", data_item)
    y = _parse_finite(row["pixel_y_subpixel"], "pixel_y_subpixel", data_item)
    if not math.isclose(x, raw_x, rel_tol=0.0, abs_tol=1e-9) or abs(y - raw_y) > (
        0.5 + 1e-9
    ):
        raise FigureEvidenceBundleError(
            f"CSV raw and subpixel coordinates do not identify one source trace: {data_item}"
        )
    uncertainty = _parse_nonnegative(row["uncertainty_px"], "uncertainty_px", data_item)
    geometry_fields = ("local_slope", "normal_line_width_px", "pixel_resolution_px",
        "x_localization_uncertainty_px", "y_localization_uncertainty_px")
    geometry_present = [field in row for field in geometry_fields]
    x_uncertainty = y_uncertainty = uncertainty
    if any(geometry_present):
        if not all(geometry_present):
            raise FigureEvidenceBundleError(f"CSV localization geometry fields must occur together: {data_item}")
        slope = _parse_finite(row["local_slope"], "local_slope", data_item)
        normal_width = _parse_nonnegative(row["normal_line_width_px"], "normal_line_width_px", data_item)
        resolution = _parse_nonnegative(row["pixel_resolution_px"], "pixel_resolution_px", data_item)
        if normal_width <= 0 or resolution != 1.0:
            raise FigureEvidenceBundleError(f"CSV localization geometry is invalid: {data_item}")
        x_uncertainty = resolution / 2
        y_uncertainty = max(0.0, (normal_width - resolution) / 2) * math.hypot(1.0, slope) + resolution / 2 * (1 + abs(slope))
        for field, value in (("x_localization_uncertainty_px", x_uncertainty),
                ("y_localization_uncertainty_px", y_uncertainty),
                ("uncertainty_px", max(x_uncertainty, y_uncertainty))):
            actual = _parse_nonnegative(row[field], field, data_item)
            if not math.isclose(actual, value, rel_tol=1e-9, abs_tol=1e-12):
                raise FigureEvidenceBundleError(f"CSV {field} does not match localization geometry: {data_item}")
    x_axis = _axis(axes, "x", data_item)
    y_axis = _axis(axes, "y", data_item)
    expected = {
        "x_value": _axis_value(x_axis, x),
        "y_value": _axis_value(y_axis, y),
        "x_uncertainty": _axis_uncertainty(x_axis, x, x_uncertainty),
        "y_uncertainty": _axis_uncertainty(y_axis, y, y_uncertainty),
    }
    for field, value in expected.items():
        actual = _parse_nonnegative(row[field], field, data_item) if field.endswith(
            "uncertainty"
        ) else _parse_finite(row[field], field, data_item)
        if not math.isclose(actual, value, rel_tol=1e-9, abs_tol=1e-12):
            raise FigureEvidenceBundleError(
                f"CSV {field} does not match axis calibration: {data_item}"
            )


def _validate_extended_numeric_fields(
    row: Mapping[str, str], fieldnames: list[str], data_item: str
) -> None:
    for field in (
        "localization_uncertainty_px",
        "depth_uncertainty_um",
        "zn_concentration_uncertainty_cm-3",
    ):
        if field in fieldnames:
            _parse_nonnegative(row[field], field, data_item)
    for field in ("depth_um", "zn_concentration_cm-3"):
        if field in fieldnames:
            _parse_finite(row[field], field, data_item)


def _validate_shared_ownership(
    pixel_series: Mapping[tuple[float, float], set[tuple[str, str]]],
    ownership: Mapping[tuple[float, float], list[tuple[str, str]]],
    occurrences: Mapping[tuple[float, float], int],
) -> None:
    for pixel, series in pixel_series.items():
        if len(series) < 2 or pixel not in ownership:
            continue
        records = ownership[pixel]
        groups = {group for mode, group in records if mode == "shared"}
        if (
            len(records) != occurrences[pixel]
            or any(mode != "shared" for mode, _ in records)
            or len(groups) != 1
        ):
            raise FigureEvidenceBundleError(
                "cross-series duplicate pixels require one consistent shared_group"
            )


def _validate_declared_shared_support(
    declared: Mapping[tuple[str, str, int], tuple[str, str, str]],
    coincident: Mapping[tuple[str, str, int], tuple[str, ...]],
    overdraw_intervals: list[
        tuple[str, str, int, int, str, tuple[str, ...], float]
    ],
    coincident_intervals: list[
        tuple[str, str, int, int, tuple[str, ...], float]
    ],
    actual: Mapping[
        tuple[str, str, int],
        tuple[tuple[float, float], tuple[int, int], str, str, str, int],
    ],
    direct_points: Mapping[
        tuple[str, str], Mapping[int, tuple[float, float]]
    ],
    series_by_identity: Mapping[tuple[str, str], Mapping[str, Any]],
    source_image: Image.Image,
) -> None:
    if not actual:
        return
    coincident_keys = {
        (panel, member, pixel_x)
        for (panel, _group, pixel_x), members in coincident.items()
        for member in members
    }
    if not set(actual).issubset(set(declared) | coincident_keys):
        raise FigureEvidenceBundleError(
            "CSV shared-support rows must be within manifest declarations"
        )
    coordinates: dict[
        tuple[str, str, int],
        set[tuple[tuple[int, int], tuple[float, float]]],
    ] = defaultdict(set)
    for key, (pixel, raw_pixel, kind, group, source, eligible) in actual.items():
        source_series = series_by_identity.get((key[0], source))
        if source_series is None or not _source_pixel_matches_series(
            source_image, raw_pixel, source_series
        ):
            raise FigureEvidenceBundleError(
                "shared-support source pixel does not match its source series"
            )
        if key not in declared:
            continue
        expected_group, expected_source, expected_kind = declared[key]
        if (group, source, kind) != (
            expected_group,
            expected_source,
            expected_kind,
        ):
            raise FigureEvidenceBundleError(
                "CSV shared-support provenance does not match manifest declaration"
            )
        if kind == "shared_occlusion" and eligible:
            raise FigureEvidenceBundleError(
                "copied shared-support rows cannot be quantitatively eligible"
            )
        if kind == "shared_occlusion" and (
            (source_record := actual.get((key[0], source, key[2]))) is None
            or source_record[2] != "direct_pixel"
            or source_record[:2] != (pixel, raw_pixel)
        ):
            raise FigureEvidenceBundleError("shared support lacks its direct source row")
        coordinates[(key[0], group, key[2])].add((raw_pixel, pixel))

    for (panel, group, pixel_x), members in coincident.items():
        if not any((panel, member, pixel_x) in actual for member in members):
            continue
        if not all((panel, member, pixel_x) in actual for member in members):
            raise FigureEvidenceBundleError("shared coordinate omits a declared member")
        records = tuple(
            (member, actual[(panel, member, pixel_x)]) for member in members
        )
        group_values = {record[3] for _, record in records}
        if group_values != {group} or any(record[4] not in members for _, record in records):
            raise FigureEvidenceBundleError("coincident-overlap rows require their declared group and member support source")
        direct = [(member, record) for member, record in records if record[2] == "direct_pixel"]
        if not direct:
            raise FigureEvidenceBundleError("coincident overlap requires directly observed raster support")
        for member, record in records:
            if record[2] == "direct_pixel":
                if record[4] != member:
                    raise FigureEvidenceBundleError("direct shared observation must retain its own source series")
            elif record[2] in {"shared_pixel", "shared_occlusion"}:
                source_record = actual.get((panel, record[4], pixel_x))
                if source_record is None or source_record[2] != "direct_pixel" or source_record[:2] != record[:2]:
                    raise FigureEvidenceBundleError("shared observation lacks its exact direct source row")
            else:
                raise FigureEvidenceBundleError("coincident overlap has an invalid support kind")
        # Historical coincident families used one donor and identical copies.
        # New families retain all direct owner coordinates and link shared
        # observations without pretending they are independent source evidence.
        if any(record[2] == "shared_occlusion" for _, record in records):
            if (len(direct) != 1 or len({record[0] for _, record in records}) != 1
                    or len({record[1] for _, record in records}) != 1
                    or len({record[4] for _, record in records}) != 1):
                raise FigureEvidenceBundleError("legacy coincident copies require one exact direct source coordinate")

    for panel, _group, left, right, members, max_distance in coincident_intervals:
        member_points = {
            member: direct_points.get((panel, member), {}) for member in members
        }
        for pixel_x in range(left, right):
            if not any((panel, member, pixel_x) in actual for member in members):
                continue
            visible = tuple(
                member_points[member][pixel_x][1]
                for member in members
                if pixel_x in member_points[member]
            )
            if not visible:
                raise FigureEvidenceBundleError(
                    f"coincident overlap lacks direct pixel {pixel_x}"
                )
            if max(visible) - min(visible) > max_distance:
                raise FigureEvidenceBundleError(
                    "coincident-overlap member distance exceeds its contract"
                )
    if any(len(items) != 1 for items in coordinates.values()):
        raise FigureEvidenceBundleError(
            "shared-support members must use one identical source coordinate"
        )


def _source_pixel_matches_series(
    image: Image.Image,
    raw_pixel: tuple[int, int],
    series: Mapping[str, Any],
) -> bool:
    descriptor = _required_mapping(series, "descriptor", "manifest series")
    color = _required_string(descriptor, "color", "manifest series descriptor")
    if len(color) != 7 or color[0] != "#":
        raise FigureEvidenceBundleError("manifest series color is invalid")
    try:
        target = tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
    except ValueError as error:
        raise FigureEvidenceBundleError("manifest series color is invalid") from error
    tolerance = descriptor.get("color_tolerance")
    if (
        not isinstance(tolerance, (int, float))
        or isinstance(tolerance, bool)
        or not math.isfinite(tolerance)
        or not 0 <= tolerance <= 48.0
    ):
        raise FigureEvidenceBundleError("manifest series color tolerance is invalid")
    if not (0 <= raw_pixel[0] < image.width and 0 <= raw_pixel[1] < image.height):
        return False
    pixel = image.getpixel(raw_pixel)
    return sum((int(pixel[index]) - target[index]) ** 2 for index in range(3)) <= (
        float(tolerance) ** 2
    )


def _validate_supporting_csv(data_item: str, content: bytes) -> int:
    _, rows = _csv_rows(data_item, content)
    return len(rows)


def _csv_rows(data_item: str, content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FigureEvidenceBundleError(f"CSV is not valid UTF-8: {data_item}") from error
    if "\x00" in text:
        raise FigureEvidenceBundleError(f"CSV contains NUL bytes: {data_item}")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fieldnames = list(reader.fieldnames or ())
        if not fieldnames or any(not name for name in fieldnames):
            raise FigureEvidenceBundleError(f"CSV header is empty: {data_item}")
        if len(fieldnames) != len(set(fieldnames)):
            raise FigureEvidenceBundleError(f"CSV headers must be unique: {data_item}")
        rows = list(reader)
    except csv.Error as error:
        raise FigureEvidenceBundleError(f"CSV syntax is invalid: {data_item}") from error
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise FigureEvidenceBundleError(f"CSV row width does not match header: {data_item}")
    return fieldnames, rows


def _declared_artifacts(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    provenance = _required_mapping(manifest, "provenance", "manifest")
    values = provenance.get("output_artifacts")
    if not isinstance(values, list) or not values:
        raise FigureEvidenceBundleError("manifest provenance has no output artifacts")
    result: dict[str, Mapping[str, Any]] = {}
    for value in values:
        if not isinstance(value, dict):
            raise FigureEvidenceBundleError("manifest provenance entry must be an object")
        name = _required_string(value, "data_item", "manifest provenance")
        if name in result:
            raise FigureEvidenceBundleError("manifest provenance data items must be unique")
        result[name] = value
    return result


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FigureEvidenceBundleError(f"invalid UTF-8 JSON: {label}") from error
    if not isinstance(value, dict):
        raise FigureEvidenceBundleError(f"JSON value must be an object: {label}")
    return value


def _required_mapping(value: Mapping[str, Any], key: str, label: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise FigureEvidenceBundleError(f"{label}.{key} must be an object")
    return result


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise FigureEvidenceBundleError(f"{label}.{key} must be a non-empty string")
    return result


def _axis(axes: Mapping[str, Any], name: str, data_item: str) -> Mapping[str, Any]:
    axis = axes.get(name)
    if not isinstance(axis, dict):
        raise FigureEvidenceBundleError(f"manifest {name} axis is missing: {data_item}")
    return axis


def _axis_value(axis: Mapping[str, Any], pixel: float) -> float:
    pixel_min = float(axis["pixel_min"])
    pixel_max = float(axis["pixel_max"])
    value_min = float(axis["value_min"])
    value_max = float(axis["value_max"])
    fraction = (pixel - pixel_min) / (pixel_max - pixel_min)
    if axis["scale"] == "linear":
        return value_min + fraction * (value_max - value_min)
    low = math.log10(value_min)
    high = math.log10(value_max)
    return 10 ** (low + fraction * (high - low))


def _axis_uncertainty(
    axis: Mapping[str, Any], pixel: float, uncertainty_px: float
) -> float:
    total = uncertainty_px + float(axis["reprojection_error_px"])
    center = _axis_value(axis, pixel)
    low = _axis_value(axis, pixel - total)
    high = _axis_value(axis, pixel + total)
    return max(abs(center - low), abs(high - center))


def _parse_flag(value: str, field: str, data_item: str) -> int:
    if value not in {"0", "1"}:
        raise FigureEvidenceBundleError(f"CSV {field} must be 0 or 1: {data_item}")
    return int(value)


def _parse_int(value: str, field: str, data_item: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise FigureEvidenceBundleError(f"CSV {field} must be an integer: {data_item}") from error
    return parsed


def _parse_finite(value: str, field: str, data_item: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise FigureEvidenceBundleError(f"CSV {field} must be numeric: {data_item}") from error
    if not math.isfinite(parsed):
        raise FigureEvidenceBundleError(f"CSV {field} must be finite: {data_item}")
    return parsed


def _parse_nonnegative(value: str, field: str, data_item: str) -> float:
    parsed = _parse_finite(value, field, data_item)
    if parsed < 0.0:
        raise FigureEvidenceBundleError(f"CSV {field} must be non-negative: {data_item}")
    return parsed


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise FigureEvidenceBundleError(f"{label} must be a positive integer")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FigureEvidenceBundleError(f"{label} must be a non-negative integer")
    return value


def _base_media_type(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.split(";", 1)[0].strip().lower()


def _media_type(suffix: str) -> str:
    return {
        ".json": "application/json",
        ".csv": "text/csv",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(suffix.lower(), "application/octet-stream")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
