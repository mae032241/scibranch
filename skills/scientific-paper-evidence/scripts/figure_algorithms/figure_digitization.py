"""Deterministic, source-bound digitization for visibly continuous plot lines."""

from __future__ import annotations
from .common import Sha256, canonical_json

import csv
import hashlib
import io
import json

from PIL import Image, ImageDraw


from .figure_evidence import validate_figure_evidence_manifest
from .figure_evidence_validation import build_figure_evidence_validation_report
from .figure_line_tracker import (
    LineFinding,
    TracePoint,
    guide_y,
    mark_shared,
    trace_continuous_line,
)
from .figure_numeric_redraw import render_numeric_redraw
from .figure_digitization_contract import (
    FigureDigitizationRequest,
    FigureDigitizationSeries,
    PdfFigureSource,
    RasterFigureSource,
    axis_uncertainty as _axis_uncertainty,
    axis_value as _axis_value,
    recover_requested_image,
)


COLOR_MATCH_TOLERANCE = 24.0


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(
        int(color[index : index + 2], 16) for index in (1, 3, 5)
    )  # type: ignore[return-value]


def _binding(
    image: Image.Image, series: FigureDigitizationSeries
) -> tuple[dict[str, object], LineFinding | None]:
    left, top, right, bottom = series.binding_bbox
    target = _rgb(series.color)
    threshold = COLOR_MATCH_TOLERANCE**2
    pixels = image.load()
    count = sum(
        sum((pixels[x, y][index] - target[index]) ** 2 for index in range(3))
        <= threshold
        for x in range(left, right)
        for y in range(top, bottom)
    )
    matched = count > 0
    value: dict[str, object] = {
        "source": series.binding_source,
        "visible_label": series.visible_label,
        "status": "matched" if matched else "unresolved",
        "confidence": 1.0 if matched else 0.0,
        "alternatives": [],
    }
    return value, (
        None
        if matched
        else LineFinding(
            "binding_pixels_missing",
            "binding box lacks the declared series color",
        )
    )


def _derive_shared_support(
    request: FigureDigitizationRequest,
    direct: dict[str, tuple[TracePoint, ...]],
) -> tuple[dict[str, tuple[TracePoint, ...]], list[dict[str, object]]]:
    """Associate intersecting raster support while retaining every owner's location."""

    definitions = {item.series_key: item for item in request.series}
    direct_by_x = {key: {point.pixel_x_raw: point for point in points}
        for key, points in direct.items()}
    by_x = {key: dict(points) for key, points in direct_by_x.items()}
    memberships: dict[tuple[str, ...], list[int]] = {}
    donors: dict[tuple[int, str], str] = {}
    for x in sorted({x for points in direct_by_x.values() for x in points}):
        available = {key: points[x] for key, points in direct_by_x.items() if x in points}
        edges = {key: {key} for key in definitions}
        for source_key, point in available.items():
            low, high = point.cluster_y_min, point.cluster_y_max
            for key, definition in definitions.items():
                if key == source_key:
                    continue
                other = available.get(key)
                guide = guide_y(definition.seeds, x, (low + high) / 2)
                actual = other if other is not None else point
                excluded = any(left <= px < right and top <= py < bottom
                    for left, top, right, bottom in (*request.exclusion_regions, *definition.exclusion_regions)
                    for px, py in ((actual.pixel_x_raw, actual.pixel_y_raw),
                        (actual.pixel_x_subpixel, actual.pixel_y_subpixel)))
                intersects = (other is not None and
                    max(low, other.cluster_y_min) <= min(high, other.cluster_y_max))
                if not excluded and (intersects or (other is None and low <= guide <= high)):
                    edges[source_key].add(key)
                    edges[key].add(source_key)
                    if other is None:
                        donors.setdefault((x, key), source_key)
        used = set()
        for key in sorted(definitions):
            if key in used:
                continue
            members, pending = set(), [key]
            while pending:
                member = pending.pop()
                if member in members:
                    continue
                members.add(member)
                pending.extend(edges[member] - members)
            used.update(members)
            if len(members) > 1:
                memberships.setdefault(tuple(sorted(members)), []).append(x)

    records = []
    for members, columns in sorted(memberships.items()):
        runs = []
        start = previous = columns[0]
        for x in columns[1:]:
            if x != previous + 1:
                runs.append((start, previous + 1))
                start = x
            previous = x
        runs.append((start, previous + 1))
        for left, right in runs:
            group = f"shared_{_sha256(canonical_json((members, left, right)))[:20]}"
            max_spread = 0.0
            for x in range(left, right):
                originals = [direct_by_x[key][x] for key in members if x in direct_by_x[key]]
                max_spread = max(max_spread, max(p.pixel_y_subpixel for p in originals)
                    - min(p.pixel_y_subpixel for p in originals))
                for key in members:
                    own = direct_by_x[key].get(x)
                    source = key if own is not None else donors[(x, key)]
                    point = own if own is not None else direct_by_x[source][x]
                    by_x[key][x] = mark_shared(point, group=group, source_series=source,
                        support_kind="direct_pixel" if own is not None else "shared_pixel",
                        shared_eligible=not point.identity_ambiguous)
            records.append({"group_key": group, "member_series": list(members),
                "pixel_ranges": [[left, right]], "mode": "coincident_overlap",
                "max_member_distance_px": max_spread})
    return ({key: tuple(points[x] for x in sorted(points)) for key, points in by_x.items()}, records)


def _curve_csv(
    request: FigureDigitizationRequest,
    series: FigureDigitizationSeries,
    points: tuple[TracePoint, ...],
    *,
    series_unresolved: bool,
) -> bytes:
    _, axes, _ = request.require_ready()
    fields = (
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
        "quantitative_measurement_claim_eligible",
        "below_sims_detection_limit",
        "eligibility_reason",
        "support_kind",
        "coordinate_ownership",
        "shared_group",
        "support_source_series",
        "identity_resolved",
        "interpolation_eligible",
        "interpolation_reason",
        "pixel_y_support_min",
        "pixel_y_support_max",
        "pixel_y_support_ranges",
        "use_limit_indices",
        "local_slope",
        "normal_line_width_px",
        "pixel_resolution_px",
        "x_localization_uncertainty_px",
        "y_localization_uncertainty_px",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for point in points:
        eligible, interpolation, reason, use_reason = _point_permissions(series, point, series_unresolved)
        writer.writerow(
            {
                "panel_key": request.panel_key,
                "series_key": series.series_key,
                "point_index": point.point_index,
                "pixel_x_raw": point.pixel_x_raw,
                "pixel_y_raw": point.pixel_y_raw,
                "pixel_x_subpixel": format(point.pixel_x_subpixel, ".17g"),
                "pixel_y_subpixel": format(point.pixel_y_subpixel, ".17g"),
                "x_value": format(
                    _axis_value(axes.x, point.pixel_x_subpixel), ".17g"
                ),
                "y_value": format(
                    _axis_value(axes.y, point.pixel_y_subpixel), ".17g"
                ),
                "uncertainty_px": format(point.uncertainty_px, ".17g"),
                "x_uncertainty": format(
                    _axis_uncertainty(
                        axes.x, point.pixel_x_subpixel, point.x_localization_uncertainty_px
                    ),
                    ".17g",
                ),
                "y_uncertainty": format(
                    _axis_uncertainty(
                        axes.y, point.pixel_y_subpixel, point.y_localization_uncertainty_px
                    ),
                    ".17g",
                ),
                "observed": 1,
                "quantitative_measurement_claim_eligible": int(eligible),
                "below_sims_detection_limit": 0,
                "eligibility_reason": reason,
                "support_kind": point.support_kind,
                "coordinate_ownership": point.coordinate_ownership,
                "shared_group": point.shared_group,
                "support_source_series": point.support_source_series,
                "identity_resolved": int(not series_unresolved and not point.identity_ambiguous),
                "interpolation_eligible": int(interpolation),
                "interpolation_reason": "" if interpolation else (reason or
                    point.interpolation_reason or "scientific_use_limit"),
                "pixel_y_support_min": point.pixel_y_support_min,
                "pixel_y_support_max": point.pixel_y_support_max,
                "pixel_y_support_ranges": json.dumps(point.pixel_y_support_ranges, separators=(",", ":")),
                "use_limit_indices": use_reason,
                "local_slope": format(point.local_slope, ".17g"),
                "normal_line_width_px": format(point.normal_line_width_px, ".17g"),
                "pixel_resolution_px": format(point.pixel_resolution_px, ".17g"),
                "x_localization_uncertainty_px": format(point.x_localization_uncertainty_px, ".17g"),
                "y_localization_uncertainty_px": format(point.y_localization_uncertainty_px, ".17g"),
            }
        )
    return stream.getvalue().encode("utf-8")


def _point_permissions(series: FigureDigitizationSeries, point: TracePoint, unresolved: bool):
    indices = [index for index, limit in enumerate(series.use_limits)
        if limit.pixel_bbox[0] <= point.pixel_x_subpixel < limit.pixel_bbox[2]
        and limit.pixel_bbox[1] <= point.pixel_y_subpixel < limit.pixel_bbox[3]]
    limits = [series.use_limits[index] for index in indices]
    eligible = not unresolved and not point.identity_ambiguous and point.measurement_location_resolved and not any(
        limit.applies_to in {"all", "quantitative_measurement"} for limit in limits)
    interpolation = eligible and point.interpolation_eligible and not any(
        limit.applies_to in {"all", "interpolation"} for limit in limits)
    reason = ("series_unresolved" if unresolved else "ambiguous_path" if point.identity_ambiguous
        else "raster_support_unresolved" if not point.measurement_location_resolved
        else "scientific_use_limit" if not eligible else "")
    return eligible, interpolation, reason, "|".join(str(index) for index in indices)


def _audit_overlay(
    image: Image.Image,
    request: FigureDigitizationRequest,
    traces: dict[str, tuple[TracePoint, ...]],
    unresolved: set[str],
) -> bytes:
    plot, axes, series_values = request.require_ready()
    # Each source-sized panel has its own trace, so coincident members do not
    # paint over one another. The strip below it reports observed columns only.
    panel_height = image.height + 70
    overlay = Image.new(
        "RGB", (max(image.width, 420), panel_height * len(series_values)), "white"
    )
    draw = ImageDraw.Draw(overlay)
    colors = ("#00bfff", "#ff00ff", "#ffb000", "#00b060")
    for index, series in enumerate(series_values):
        color = colors[index % len(colors)]
        offset = index * panel_height + 25
        overlay.paste(image, (0, offset))
        draw.text((4, offset - 20), f"{series.series_key} ({series.line_style})", fill=color)
        left, top, right, bottom = series.binding_bbox
        draw.rectangle((left, top + offset, right, bottom + offset), outline=color, width=1)
        for x, y in series.seeds:
            draw.line((x - 3, y + offset, x + 3, y + offset), fill="#888888", width=1)
            draw.line((x, y + offset - 3, x, y + offset + 3), fill="#888888", width=1)
        domain = (plot[0], plot[2])
        strip_y = offset + image.height + 3
        draw.rectangle((domain[0], strip_y, domain[1] - 1, strip_y + 4), fill="#aaaaaa")
        for point in traces[series.series_key]:
            if point.shared_group:
                x, y = point.pixel_x_subpixel, point.pixel_y_subpixel + offset
                draw.ellipse((x - 2, y - 2, x + 2, y + 2), outline="#d6a000")
        for point in traces[series.series_key]:
            x = int(round(point.pixel_x_subpixel))
            y = int(round(point.pixel_y_subpixel))
            eligible, interpolation, _, _ = _point_permissions(series, point, series.series_key in unresolved)
            local_limit = not eligible or not interpolation
            point_color = "#ff4000" if local_limit else color
            if local_limit:
                for low, high in point.pixel_y_support_ranges:
                    draw.line((x, low + offset, x, high + offset), fill="#ffb080")
            draw.point((x, y + offset), fill=point_color)
            draw.line((x, strip_y, x, strip_y + 4), fill=(
                "#ff4000" if local_limit else "#d6a000" if point.shared_group else color
            ))
        draw.text((4, strip_y + 9), "Trace: series color | shared: gold ring", fill="#333333")
        draw.text((4, strip_y + 21), "Coverage: gray missing | red local limit", fill="#333333")
    stream = io.BytesIO()
    overlay.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_digitized_figure_bundle(
    source_content: bytes,
    request_content: bytes,
) -> tuple[dict[str, tuple[bytes, str]], dict[str, object]]:
    """Build and mechanically validate one bounded continuous-line evidence family."""

    request = FigureDigitizationRequest.model_validate_json(request_content, strict=True)
    plot, axes, series_values = request.require_ready()
    recovered = recover_requested_image(source_content, request)
    image = Image.open(io.BytesIO(recovered.content)).convert("RGB")

    direct: dict[str, tuple[TracePoint, ...]] = {}
    findings: dict[str, list[LineFinding]] = {}
    bindings: dict[str, dict[str, object]] = {}
    for series in series_values:
        binding, binding_finding = _binding(image, series)
        bindings[series.series_key] = binding
        local = [] if binding_finding is None else [binding_finding]
        domain = (plot[0], plot[2])
        trace = trace_continuous_line(
            image,
            rgb=_rgb(series.color),
            tolerance=COLOR_MATCH_TOLERANCE,
            seeds=series.seeds,
            bounds=(domain[0], plot[1], domain[1], plot[3]),
            exclusions=(*request.exclusion_regions, *series.exclusion_regions),
        )
        local.extend(trace.findings)
        direct[series.series_key] = trace.points
        findings[series.series_key] = local
    traces, shared_support = _derive_shared_support(request, direct)

    siblings: dict[str, tuple[bytes, str]] = {
        f"source_panels/{request.panel_key}.png": (recovered.content, "image/png"),
        f"audit_overlays/{request.panel_key}--identity-fidelity.png": (
            _audit_overlay(
                image,
                request,
                traces,
                {key for key, binding in bindings.items() if binding["status"] != "matched"},
            ),
            "image/png",
        ),
    }
    manifest_series = []
    ambiguities = []
    qualified_count = 0
    for series in series_values:
        key = series.series_key
        points = traces[key]
        local_findings = findings[key]
        # Tracking diagnostics describe extraction, not scientific identity.
        # A missed seed or missing column cannot revoke other measured pixels.
        series_unresolved = bindings[key]["status"] != "matched"
        item = f"curve_tables/{request.panel_key}--{key}.csv"
        siblings[item] = (
            _curve_csv(
                request,
                series,
                points,
                series_unresolved=series_unresolved,
            ),
            "text/csv",
        )
        domain = (plot[0], plot[2])
        observed_x = sorted({point.pixel_x_raw for point in points})
        visible_fraction = len(observed_x) / max(1, domain[1] - domain[0])
        max_gap = max(
            [
                observed_x[0] - domain[0],
                domain[1] - 1 - observed_x[-1],
                *(
                    right - left - 1
                    for left, right in zip(observed_x, observed_x[1:])
                ),
            ]
            if observed_x
            else [domain[1] - domain[0]]
        )
        if not series_unresolved:
            qualified_count += 1
        ambiguities.extend(
            {
                "panel_key": request.panel_key,
                "series_key": key,
                "message": finding.message,
                "candidates": [],
            }
            for finding in local_findings
            if finding.code == "binding_pixels_missing"
        )
        manifest_series.append(
            {
                "series_key": key,
                "label": series.label,
                "primitive_kind": "line",
                "descriptor": {
                    "color": series.color,
                    "color_tolerance": COLOR_MATCH_TOLERANCE,
                    "line_style": series.line_style,
                },
                "data_item": item,
                "binding": bindings[key],
                "point_count": len(points),
                "visible_fraction": round(visible_fraction, 9),
                "max_gap_px": max_gap,
                "tracking_diagnostics": [
                    finding.message for finding in local_findings
                    if finding.code != "binding_pixels_missing"
                ],
                "use_limits": [limit.model_dump(mode="json") for limit in series.use_limits],
                "exclusion_regions": [list(box) for box in (*request.exclusion_regions, *series.exclusion_regions)],
                "identity_resolved_point_count": sum(not series_unresolved and not p.identity_ambiguous for p in points),
                "interpolation_eligible_point_count": sum(_point_permissions(series, p, series_unresolved)[1] for p in points),
                "uncertainty_px": round(
                    max(
                        (
                            point.uncertainty_px
                            + max(
                                axes.x.reprojection_error_px,
                                axes.y.reprojection_error_px,
                            )
                            for point in points
                        ),
                        default=max(
                            axes.x.reprojection_error_px,
                            axes.y.reprojection_error_px,
                        ),
                    ),
                    6,
                ),
            }
        )
    siblings[f"audit_overlays/{request.panel_key}--numeric-redraw.png"] = (
        render_numeric_redraw(
            request,
            {
                series.series_key: siblings[
                    f"curve_tables/{request.panel_key}--{series.series_key}.csv"
                ][0]
                for series in series_values
            },
        ),
        "image/png",
    )
    manifest = {
        "schema_version": "scidiscovery.figure-evidence-manifest.v1",
        "figure_key": request.figure_key,
        "status": "unresolved" if ambiguities else "qualified",
        "source": {
            "source_name": "paper_source",
            "page": (
                request.source.page
                if isinstance(request.source, PdfFigureSource)
                else "supplied raster"
            ),
            "figure": request.figure,
            "image_sha256": recovered.image_sha256,
            "width": image.width,
            "height": image.height,
            **(
                {
                    "pdf_sha256": request.source.source_sha256,
                    "pdf_page": request.source.page,
                    "pdf_object": (
                        f"{request.source.pdf_object_id} "
                        f"{request.source.pdf_object_generation}"
                    ),
                }
                if isinstance(request.source, PdfFigureSource)
                else {}
            ),
        },
        "panels": [
            {
                "panel_key": request.panel_key,
                "citation": request.citation,
                "axis_calibration": axes.model_dump(mode="json"),
                "series": manifest_series,
                "shared_support": shared_support,
            }
        ],
        "metrics": {
            "panel_count": 1,
            "series_count": len(manifest_series),
            "qualified_series_count": qualified_count,
            "total_point_count": sum(len(points) for points in traces.values()),
            "minimum_visible_fraction": min(
                item["visible_fraction"] for item in manifest_series
            ),
        },
        "ambiguities": ambiguities,
        "provenance": {
            "spec_sha256": _sha256(canonical_json(request.model_dump(mode="json"))),
            "recovery_tool": recovered.recovery_tool,
            "recovery_tool_version": recovered.recovery_tool_version,
            "output_artifacts": [
                {
                    "collection": name.split("/", 1)[0],
                    "data_item": name,
                    "sha256": _sha256(content),
                    "bytes": len(content),
                    "media_type": media_type,
                }
                for name, (content, media_type) in sorted(siblings.items())
            ],
        },
    }
    manifest_raw = canonical_json(validate_figure_evidence_manifest(manifest))
    report = build_figure_evidence_validation_report(
        manifest_data_item="figure_manifest/evidence.json",
        manifest_content=manifest_raw,
        sibling_files=siblings,
    )
    files = {
        "figure_manifest/evidence.json": (manifest_raw, "application/json"),
        **siblings,
        "validation_reports/validation_report.json": (
            canonical_json(report),
            "application/json",
        ),
    }
    return files, report
