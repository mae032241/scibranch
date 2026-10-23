"""Raster support is a measurement limit, not a physical multivalued verdict."""
from __future__ import annotations

import csv
import math
import base64
import hashlib
import io
import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from figure_algorithms.figure_digitization import build_digitized_figure_bundle
from figure_algorithms.figure_digitization_contract import materialize_figure_request
from figure_algorithms.figure_line_tracker import trace_continuous_line
from figure_algorithms.figure_evidence_normalizer import normalize_figure_evidence
from curve_algorithms.common import canonical_json


def _source_request(image, series, **extra):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    source = stream.getvalue()
    request = {"schema_version": "scidiscovery.curve-figure-digitization-request.v2",
        "figure_key": "synthetic", "panel_key": "main", "figure": "Synthetic raster",
        "citation": "Local algorithm regression", "source": {},
        "plot_bbox": [2, 3, image.width - 2, image.height - 2],
        "axis_calibration": {
            "x": {"scale": "linear", "unit": "x", "ticks": [[2, 0], [image.width - 3, image.width - 5]]},
            "y": {"scale": "linear", "unit": "y", "ticks": [[3, image.height - 5], [image.height - 3, 0]]}},
        "series": series}
    request.update(extra)
    return source, canonical_json(materialize_figure_request(request, source))


def _series(key="black", *, color="#000000", seeds=((2, 12), (27, 12)), **extra):
    return {"series_key": key, "label": key, "color": color, "line_style": "solid",
        "binding_source": "legend", "visible_label": key, "binding_bbox": [2, 0, 8, 2],
        "seeds": seeds, **extra}


def _rows(files, key="black"):
    return list(csv.DictReader(io.StringIO(files[f"curve_tables/main--{key}.csv"][0].decode())))


def _trace(image, seeds):
    return trace_continuous_line(image, rgb=(0, 0, 0), tolerance=24,
        seeds=seeds, bounds=(2, 3, image.width - 2, image.height - 2), exclusions=())


def _assert_horizontal_mirror(image, seeds):
    original = _trace(image, seeds)
    mirrored = _trace(image.transpose(Image.Transpose.FLIP_LEFT_RIGHT),
        tuple((image.width - 1 - x, y) for x, y in reversed(seeds)))
    original_points = {image.width - 1 - p.pixel_x_raw: p for p in original.points}
    mirrored_points = {p.pixel_x_raw: p for p in mirrored.points}
    assert original_points.keys() == mirrored_points.keys()
    for x, point in original_points.items():
        other = mirrored_points[x]
        assert point.pixel_y_raw == other.pixel_y_raw
        assert point.pixel_y_subpixel == pytest.approx(other.pixel_y_subpixel)
        assert point.identity_ambiguous == other.identity_ambiguous
        assert point.interpolation_eligible == other.interpolation_eligible
        assert point.interpolation_reason == other.interpolation_reason
    return original


def test_thick_line_retains_supported_centers():
    image = Image.new("RGB", (30, 30), "white")
    ImageDraw.Draw(image).line((2, 12, 27, 12), fill="black", width=5)
    points = _trace(image, ((2, 11), (27, 13))).points
    assert len(points) == 26
    assert all(point.pixel_y_subpixel == 12 and not point.identity_ambiguous for point in points)
    assert all(point.interpolation_eligible for point in points)


def test_neighbor_continuity_rejects_isolated_nearest_guide_distractor():
    image = Image.new("RGB", (30, 30), "white")
    ImageDraw.Draw(image).line((2, 18, 27, 18), fill="black", width=3)
    image.putpixel((14, 10), (0, 0, 0))
    points = _trace(image, ((2, 18), (14, 10), (27, 18))).points
    assert next(point for point in points if point.pixel_x_raw == 14).pixel_y_raw == 18


def test_guide_selected_short_curve_is_not_replaced_by_long_unrelated_curve():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 14, 12), fill="black")
    draw.line((2, 24, 27, 24), fill="black")
    points = _trace(image, ((2, 12), (14, 12))).points
    assert [(p.pixel_x_raw, p.pixel_y_raw) for p in points] == [(x, 12) for x in range(2, 15)]
    assert all(p.interpolation_eligible and not p.identity_ambiguous for p in points)


def test_target_reappears_after_internal_gap_without_seed_endpoint_clipping():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 10, 12), fill="black")
    draw.line((15, 12, 27, 12), fill="black")
    draw.line((2, 24, 27, 24), fill="black")
    trace = _trace(image, ((5, 12), (24, 12)))
    expected = [*range(2, 11), *range(15, 28)]
    assert [p.pixel_x_raw for p in trace.points] == expected
    assert all(p.pixel_y_raw == 12 and p.interpolation_eligible for p in trace.points)
    assert trace.max_gap_px == 4


@pytest.mark.parametrize("style", ["dashed", "dotted"])
@pytest.mark.parametrize("unrelated_y", [14, 24])
def test_separate_stroke_components_keep_identity_without_absorbing_long_line(style, unrelated_y):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, unrelated_y, 27, unrelated_y), fill="black")
    expected = set()
    runs = ((2, 4), (8, 10), (14, 16), (20, 22), (26, 27)) if style == "dashed" else tuple((x, x) for x in range(2, 28, 2))
    for left, right in runs:
        draw.line((left, 12, right, 12), fill="black")
        expected.update(range(left, right + 1))
    source, request = _source_request(image, [_series(line_style=style, seeds=((8, 12), (20, 12)))])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = _rows(files)
    assert {int(row["pixel_x_raw"]) for row in rows} == expected
    assert all(row["pixel_y_raw"] == "12" for row in rows)
    trace = _assert_horizontal_mirror(image, ((8, 12), (20, 12)))
    assert {p.pixel_x_raw for p in trace.points} == expected


@pytest.mark.parametrize("unrelated_y", [14, 24])
@pytest.mark.parametrize("connected", [False, True])
def test_mirrored_short_target_does_not_absorb_long_prefix(unrelated_y, connected):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 14, 12), fill="black")
    draw.line((2, unrelated_y, 27, unrelated_y), fill="black")
    if connected:
        draw.line((8, 12, 8, unrelated_y), fill="black")
    points = _assert_horizontal_mirror(image, ((2, 12), (14, 12))).points
    assert [p.pixel_x_raw for p in points] == list(range(2, 15))
    assert all(p.pixel_y_raw == 12 for p in points if p.pixel_x_raw != 8)
    if connected:
        merge = next(p for p in points if p.pixel_x_raw == 8)
        assert merge.identity_ambiguous and not merge.interpolation_eligible
    else:
        assert all(not p.identity_ambiguous and p.interpolation_eligible for p in points)


@pytest.mark.parametrize("unrelated_y", [14, 24])
@pytest.mark.parametrize("gap", [False, True])
def test_mirrored_identity_growth_preserves_seed_external_support_and_gaps(unrelated_y, gap):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, unrelated_y, 27, unrelated_y), fill="black")
    runs = ((4, 10), (15, 25)) if gap else ((4, 25),)
    expected = set()
    for left, right in runs:
        draw.line((left, 12, right, 12), fill="black")
        expected.update(range(left, right + 1))
    draw.line((17, 12, 17, unrelated_y), fill="black")
    trace = _assert_horizontal_mirror(image, ((8, 12), (20, 12)))
    assert {p.pixel_x_raw for p in trace.points} == expected
    assert all(p.pixel_y_raw == 12 for p in trace.points if p.pixel_x_raw != 17)
    assert trace.max_gap_px == (4 if gap else 2)
    merge = next(p for p in trace.points if p.pixel_x_raw == 17)
    assert merge.identity_ambiguous and not merge.interpolation_eligible


@pytest.mark.parametrize("unclear", [False, True])
def test_mirrored_competing_paths_preserve_identity_and_interpolation_eligibility(unclear):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 27, 12), fill="black")
    draw.line([(10, 12), (10, 20), (20, 20), (20, 12)], fill="black")
    seeds = ((2, 12), (15, 12), (27, 12))
    if unclear:
        seeds = ((2, 12), (10, 12), (11, 15.8), (19, 15.8), (20, 12), (27, 12))
    trace = _assert_horizontal_mirror(image, seeds)
    point = next(p for p in trace.points if p.pixel_x_raw == 15)
    assert point.identity_ambiguous is unclear
    assert point.interpolation_eligible is not unclear


def _normalized_sloped_curve(use_limits=()):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, 5, 27, 25), fill="black")
    source, request = _source_request(image, [_series(seeds=((2, 5), (27, 25)), use_limits=use_limits)])
    files, _ = build_digitized_figure_bundle(source, request)
    bundle, _ = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    return bundle.series[0]


def test_all_interpolation_forbidden_cannot_generate_supported_crossing():
    from curve_algorithms.schema import CurveDomain, curve_crossings, _raw_value_at
    curve = _normalized_sloped_curve([{"pixel_bbox": [2, 3, 28, 28],
        "reason": "Observations only; no supported interpolation.", "applies_to": "interpolation"}])
    assert curve.availability.status == "unavailable" and not curve.valid_intervals
    assert len(curve.points) == 26
    assert _raw_value_at(curve, curve.points[0].x, "linear_y") == curve.points[0].y
    with pytest.raises(ValueError, match="interpolation_curve_unavailable"):
        _raw_value_at(curve, 0.5, "linear_y")
    with pytest.raises(ValueError, match="interpolation_curve_unavailable"):
        curve_crossings(curve, 13.5, "either", CurveDomain(start=0, stop=25, unit="x"), "linear_y")


def test_partial_interpolation_limit_preserves_raw_observations_and_legal_crossings():
    from curve_algorithms.schema import CurveDomain, curve_crossings, _raw_value_at
    curve = _normalized_sloped_curve([{"pixel_bbox": [10, 3, 15, 28],
        "reason": "Do not interpolate this local region.", "applies_to": "interpolation"}])
    assert curve.availability.status == "available" and len(curve.valid_intervals) == 2
    restricted_observation = next(point for point in curve.points if point.x == 10)
    assert _raw_value_at(curve, 10, "linear_y") == restricted_observation.y
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(curve, 10.5, "linear_y")
    domain = CurveDomain(start=0, stop=25, unit="x")
    assert curve_crossings(curve, 13.5, "either", domain, "linear_y") == ()
    assert len(curve_crossings(curve, 20, "either", domain, "linear_y")) == 1
    available = _normalized_sloped_curve()
    assert curve_crossings(available, 13.5, "either", domain, "linear_y") == pytest.approx((11.04,))


def test_narrow_spike_cap_is_not_smoothed_or_rejected_for_column_height():
    image = Image.new("RGB", (30, 40), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 32, 27, 32), fill="black", width=3)
    draw.line((14, 4, 14, 32), fill="black", width=1)
    point = next(point for point in _trace(image, ((2, 32), (14, 4), (27, 32))).points
        if point.pixel_x_raw == 14)
    assert point.pixel_y_raw == 4
    assert point.pixel_y_support_min == 4 and point.pixel_y_support_max == 33
    assert point.interpolation_eligible
    assert not point.identity_ambiguous  # Width alone does not establish a different curve.
    assert abs(point.local_slope) > 20
    assert point.normal_line_width_px < 2
    assert point.x_localization_uncertainty_px == 0.5
    assert point.y_localization_uncertainty_px != (point.pixel_y_support_max - point.pixel_y_support_min + 1) / 2


def test_coarse_steep_single_stroke_with_sparse_guides_retains_center_and_interpolation():
    image = Image.new("RGB", (40, 145), "white")
    draw = ImageDraw.Draw(image)
    # Width is perpendicular to the stroke. Its tall column projection is not
    # a second physical curve. Only endpoint anchors are supplied.
    draw.line((5, 125, 33, 13), fill="black", width=7)
    trace = _trace(image, ((5, 126), (33, 12)))
    interior = [point for point in trace.points if 8 <= point.pixel_x_raw <= 30]
    assert len(interior) == 23
    assert all(point.interpolation_eligible and not point.identity_ambiguous for point in interior)
    assert max(abs(point.pixel_y_subpixel - (145 - 4 * point.pixel_x_raw)) for point in interior) < 1
    assert all(abs(point.local_slope + 4) < 0.5 for point in interior)
    assert all(4 < point.normal_line_width_px < 9 for point in interior)
    assert all(point.pixel_y_support_max - point.pixel_y_support_min > 20 for point in interior)
    for point in interior:
        expected = max(0, (point.normal_line_width_px - 1) / 2) * math.hypot(1, point.local_slope) + 0.5 * (1 + abs(point.local_slope))
        assert point.y_localization_uncertainty_px == pytest.approx(expected)
        assert point.x_localization_uncertainty_px == 0.5


def test_sharp_turn_uses_image_tangents_with_endpoint_guides_only():
    image = Image.new("RGB", (40, 60), "white")
    ImageDraw.Draw(image).line([(2, 48), (20, 8), (37, 48)], fill="black", width=3)
    points = _trace(image, ((2, 48), (37, 48))).points
    tip = next(point for point in points if point.pixel_x_raw == 20)
    assert abs(tip.pixel_y_subpixel - 8) < 2, [(p.pixel_x_raw, p.pixel_y_subpixel,
        p.pixel_y_support_min, p.pixel_y_support_max, p.local_slope, p.normal_line_width_px)
        for p in points if 18 <= p.pixel_x_raw <= 22]
    assert abs(tip.local_slope) > 1
    assert tip.interpolation_eligible and not tip.identity_ambiguous


def test_separated_competing_paths_are_ambiguous_without_exact_distance_tie():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 10, 27, 10), fill="black")
    draw.line((2, 14, 27, 14), fill="black")
    trace = _trace(image, ((2, 11.8), (27, 11.8)))
    assert all(point.identity_ambiguous and not point.interpolation_eligible for point in trace.points)


@pytest.mark.parametrize("unclear", [False, True])
def test_extending_unambiguous_tail_does_not_change_local_branch_ambiguity(unclear):
    def trace(width, prefix=0):
        image = Image.new("RGB", (width + prefix, 30), "white")
        draw = ImageDraw.Draw(image)
        draw.line((2, 12, image.width - 3, 12), fill="black")
        draw.line([(prefix + 10, 12), (prefix + 10, 20),
            (prefix + 20, 20), (prefix + 20, 12)], fill="black")
        seeds = ((2, 12), (prefix + 15, 12), (image.width - 3, 12))
        if unclear:
            seeds = ((2, 12), (prefix + 10, 12), (prefix + 11, 15.8),
                (prefix + 19, 15.8), (prefix + 20, 12), (image.width - 3, 12))
        return {point.pixel_x_raw - prefix: point for point in _trace(image, seeds).points}
    short, extended = trace(60), trace(260)
    prefixed = trace(260, prefix=100)
    assert short[15].identity_ambiguous is unclear
    for x in range(10, 21):
        assert short[x].identity_ambiguous == extended[x].identity_ambiguous == prefixed[x].identity_ambiguous
        assert short[x].pixel_y_subpixel == extended[x].pixel_y_subpixel == prefixed[x].pixel_y_subpixel


def _shared_exclusion_request(scope=None, use_limits=()):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((10, 1, 15, 1), fill="red")
    draw.line((2, 12, 27, 12), fill="black", width=5)
    draw.line((2, 10, 9, 10), fill="red")
    draw.line((15, 10, 27, 10), fill="red")
    boxes = [[10, 11, 15, 14]]
    red = _series("red", color="#ff0000", seeds=((2, 10), (27, 10)), binding_bbox=[10, 0, 16, 2],
        exclusion_regions=boxes if scope == "recipient" else [], use_limits=use_limits)
    return _source_request(image, [_series("black"), red],
        exclusion_regions=boxes if scope == "global" else [])


@pytest.mark.parametrize("scope", ["recipient", "global"])
def test_shared_donor_does_not_restore_excluded_recipient_coordinates(scope):
    source, request = _shared_exclusion_request(scope)
    files, _ = build_digitized_figure_bundle(source, request)
    forbidden = [row for row in _rows(files, "red") if 10 <= float(row["pixel_x_subpixel"]) < 15
        and 11 <= float(row["pixel_y_subpixel"]) < 14]
    assert not forbidden
    if scope == "recipient":
        assert not [row for row in _rows(files, "red") if 10 <= int(row["pixel_x_raw"]) < 15]
    manifest = json.loads(files["figure_manifest/evidence.json"][0])
    assert manifest["panels"][0]["series"][1]["exclusion_regions"] == [[10, 11, 15, 14]]


def test_legal_shared_donor_respects_receiving_scientific_use_limit():
    source, request = _shared_exclusion_request(use_limits=[{"pixel_bbox": [10, 11, 15, 14],
        "reason": "Only observe this receiving series here.", "applies_to": "interpolation"}])
    files, _ = build_digitized_figure_bundle(source, request)
    receiving = [row for row in _rows(files, "red") if 10 <= int(row["pixel_x_raw"]) < 15]
    assert len(receiving) == 5
    assert all(row["support_kind"] == "shared_pixel" and row["observed"] == "1"
        and row["quantitative_measurement_claim_eligible"] == "1" and row["interpolation_eligible"] == "0"
        and row["use_limit_indices"] == "0" for row in receiving)
    assert all(row["interpolation_eligible"] == "1" for row in _rows(files, "black")
        if 10 <= int(row["pixel_x_raw"]) < 15)
    bundle, _ = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={f"curve_table__main__{key}": files[f"curve_tables/main--{key}.csv"][0]
            for key in ("black", "red")})
    from curve_algorithms.schema import _raw_value_at
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(bundle.series[1], 10.5, "linear_y")


@pytest.mark.parametrize("box", [(10, 11, 15, 14), (10, 14, 15, 15)])
def test_shared_allocator_checks_both_raw_and_subpixel_receiving_coordinates(box):
    from figure_algorithms.figure_line_tracker import TracePoint
    from figure_algorithms.figure_digitization import _derive_shared_support
    from figure_algorithms.figure_digitization_contract import FigureDigitizationRequest
    _, request = _shared_exclusion_request()
    parsed = FigureDigitizationRequest.model_validate_json(request)
    red = parsed.series[1].model_copy(update={"exclusion_regions": (box,)})
    parsed = parsed.model_copy(update={"series": (parsed.series[0], red)})
    donor = TracePoint(point_index=10, pixel_x_raw=12, pixel_y_raw=14,
        pixel_x_subpixel=12, pixel_y_subpixel=13.75, uncertainty_px=0.5,
        cluster_y_min=10, cluster_y_max=14)
    owners, groups = _derive_shared_support(parsed, {"black": (donor,), "red": ()})
    assert not owners["red"] and not groups


def test_line_style_survives_request_manifest_and_redraw():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    for left in (2, 8, 14, 20, 26):
        draw.line((left, 12, min(left + 2, 27), 12), fill="black")
    source, request = _source_request(image, [_series(line_style="dashed")])
    files, _ = build_digitized_figure_bundle(source, request)
    manifest = json.loads(files["figure_manifest/evidence.json"][0])
    assert manifest["panels"][0]["series"][0]["descriptor"]["line_style"] == "dashed"
    rows = _rows(files)
    assert len(rows) == 14
    assert {int(row["pixel_x_raw"]) for row in rows}.isdisjoint({5, 6, 7})


def test_shared_neighbor_observations_keep_each_owner_coordinate():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, 12, 27, 12), fill="black", width=3)
    source, request = _source_request(image, [
        _series("a", seeds=((2, 11), (27, 11))),
        _series("b", seeds=((2, 13), (27, 13)))])
    files, report = build_digitized_figure_bundle(source, request)
    first, second = _rows(files, "a"), _rows(files, "b")
    assert all(row["coordinate_ownership"] == "shared" for row in first + second)
    assert all(row["quantitative_measurement_claim_eligible"] == "1" for row in first + second)
    # Both guides select the same visible center trajectory; guides are not
    # substituted as independently observed coordinates.
    assert all(float(row["pixel_y_subpixel"]) == 12 for row in first + second)
    assert report["metrics"]["shared_observation_curve_rows"] == 52
    bundle, _ = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={f"curve_table__main__{key}": files[f"curve_tables/main--{key}.csv"][0] for key in ("a", "b")})
    assert bundle.series[0].shared_support == bundle.series[1].shared_support
    assert bundle.series[0].shared_support[0].member_series == ("a", "b")


def test_shared_association_preserves_distinct_direct_positions():
    from dataclasses import replace
    from figure_algorithms.figure_digitization import _derive_shared_support
    from figure_algorithms.figure_digitization_contract import FigureDigitizationRequest
    image = Image.new("RGB", (30, 30), "white")
    ImageDraw.Draw(image).line((2, 12, 27, 12), fill="black", width=3)
    _, request = _source_request(image, [_series("a"), _series("b")])
    first = _trace(image, ((2, 12), (27, 12))).points
    second = tuple(replace(p, pixel_y_raw=13, pixel_y_subpixel=12.75) for p in first)
    owners, groups = _derive_shared_support(FigureDigitizationRequest.model_validate_json(request),
        {"a": first, "b": second})
    assert groups
    assert [p.pixel_y_subpixel for p in owners["a"]] == [p.pixel_y_subpixel for p in first]
    assert [p.pixel_y_subpixel for p in owners["b"]] == [p.pixel_y_subpixel for p in second]
    assert all(p.support_kind == "direct_pixel" for p in owners["a"] + owners["b"])


def test_scientific_use_limit_is_retained_and_splits_downstream_intervals():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, 12, 27, 12), fill="black")
    source, request = _source_request(image, [_series(use_limits=[{
        "pixel_bbox": [10, 3, 15, 28], "reason": "Caption limits this region to qualitative comparison.",
        "applies_to": "quantitative_measurement"}])])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = _rows(files)
    limited = [row for row in rows if 10 <= int(row["pixel_x_raw"]) < 15]
    assert len(limited) == 5 and all(row["observed"] == "1" for row in limited)
    assert all(row["quantitative_measurement_claim_eligible"] == "0" for row in limited)
    assert all(row["interpolation_eligible"] == "0" for row in limited)
    bundle, audit = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    assert len(bundle.series[0].points) == 26
    assert [part.start for part in bundle.series[0].valid_intervals] == pytest.approx([0, 13])
    assert [part.stop for part in bundle.series[0].valid_intervals] == pytest.approx([7, 25])
    assert audit[0].eligible_row_count == 21








@pytest.mark.parametrize("box", [[12, 11, 13, 14], [12, 14, 13, 15]])
def test_primary_csv_validator_rejects_excluded_raw_or_subpixel_observation(box):
    from figure_algorithms.figure_evidence_validation import build_figure_evidence_validation_report, FigureEvidenceBundleError
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, 12, 27, 12), fill="black", width=5)
    source, request = _source_request(image, [_series()])
    files, _ = build_digitized_figure_bundle(source, request)
    manifest = json.loads(files["figure_manifest/evidence.json"][0])
    manifest["panels"][0]["series"][0]["exclusion_regions"] = [box]
    rows = _rows(files)
    row = next(row for row in rows if row["pixel_x_raw"] == "12")
    row.update(pixel_y_raw="14", pixel_y_subpixel="13.75", y_value=format(25 - (13.75 - 3) * 25 / 24, ".17g"))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    raw = stream.getvalue().encode()
    files["curve_tables/main--black.csv"] = (raw, "text/csv")
    for entry in manifest["provenance"]["output_artifacts"]:
        if entry["data_item"] == "curve_tables/main--black.csv":
            entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(FigureEvidenceBundleError, match="observed curve row lies in an exclusion region"):
        build_figure_evidence_validation_report(manifest_data_item="figure_manifest/evidence.json",
            manifest_content=canonical_json(manifest), sibling_files={name: item for name, item in files.items()
                if name not in {"figure_manifest/evidence.json", "validation_reports/validation_report.json"}})


@pytest.mark.parametrize("tamper", ["identity", "interpolation", "use_limits", "source_pixel", "localization"])
def test_integrity_validator_rejects_inconsistent_primary_csv_permissions(tamper):
    from figure_algorithms.figure_evidence_validation import build_figure_evidence_validation_report, FigureEvidenceBundleError
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((2, 12, 27, 12), fill="black")
    source, request = _source_request(image, [_series(use_limits=[{"pixel_bbox": [10, 3, 15, 28],
        "reason": "Quantitative comparison is excluded here.", "applies_to": "quantitative_measurement"}])])
    files, _ = build_digitized_figure_bundle(source, request)
    manifest = json.loads(files["figure_manifest/evidence.json"][0])
    rows = _rows(files)
    if tamper == "identity":
        rows[0]["identity_resolved"] = "0"
    elif tamper == "interpolation":
        rows[8]["interpolation_eligible"] = "1"
    elif tamper == "use_limits":
        rows[8]["use_limit_indices"] = ""
    elif tamper == "localization":
        rows[0]["y_localization_uncertainty_px"] = "50"
    else:
        rows[0]["pixel_y_raw"] = "13"
        rows[0]["pixel_y_subpixel"] = "13"
        rows[0]["y_value"] = format(25 - 10 * 25 / 24, ".17g")
        rows[0]["pixel_y_support_min"] = rows[0]["pixel_y_support_max"] = "13"
        rows[0]["pixel_y_support_ranges"] = "[[13,13]]"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    table = "curve_tables/main--black.csv"
    raw = stream.getvalue().encode()
    files[table] = (raw, "text/csv")
    for entry in manifest["provenance"]["output_artifacts"]:
        if entry["data_item"] == table:
            entry.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    siblings = {name: value for name, value in files.items()
        if name not in {"figure_manifest/evidence.json", "validation_reports/validation_report.json"}}
    reason = {"identity": "eligibility requires resolved identity", "interpolation": "interpolation requires eligibility",
        "use_limits": "use limits differ from manifest geometry", "source_pixel": "direct observation lacks",
        "localization": "does not match localization geometry"}[tamper]
    with pytest.raises(FigureEvidenceBundleError, match=reason):
        build_figure_evidence_validation_report(manifest_data_item="figure_manifest/evidence.json",
            manifest_content=canonical_json(manifest), sibling_files=siblings)


def test_disconnected_strokes_in_adjacent_columns_do_not_allow_interpolation():
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 14, 12), fill="black")
    draw.line((15, 20, 27, 20), fill="black")
    seeds = ((2, 12), (14, 12), (15, 20), (27, 20))
    points = _assert_horizontal_mirror(image, seeds).points
    assert all(p.interpolation_eligible and p.measurement_location_resolved for p in points)
    draw.line((2, 1, 7, 1), fill="black")
    source, request = _source_request(image, [_series(seeds=seeds)])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = _rows(files)
    assert all(row["observed"] == row["quantitative_measurement_claim_eligible"] == row["interpolation_eligible"] == "1"
        for row in rows)
    from figure_algorithms.figure_numeric_redraw import _runs
    assert [[int(row["pixel_x_raw"]) for row in run]
        for run in _runs(files["curve_tables/main--black.csv"][0])] == [list(range(2, 15)), list(range(15, 28))]
    bundle, _ = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    curve = bundle.series[0]
    assert [(interval.start, interval.stop) for interval in curve.valid_intervals] == [(0, 12), (13, 25)]
    from curve_algorithms.schema import _raw_value_at, curve_crossings, CurveDomain
    assert _raw_value_at(curve, 12, "linear_y") == pytest.approx(375 / 24)
    assert _raw_value_at(curve, 13, "linear_y") == pytest.approx(175 / 24)
    assert _raw_value_at(curve, 11.5, "linear_y") == pytest.approx(375 / 24)
    assert _raw_value_at(curve, 13.5, "linear_y") == pytest.approx(175 / 24)
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(curve, 12.5, "linear_y")
    assert curve_crossings(curve, 11, "either", CurveDomain(start=0, stop=25, unit="x"), "linear_y") == ()


@pytest.mark.parametrize("scale", ["linear", "log10"])
@pytest.mark.parametrize("descending", [False, True])
def test_shared_disconnected_support_survives_axis_orientation(scale, descending):
    from figure_algorithms.figure_numeric_redraw import _runs
    from figure_algorithms.figure_evidence_normalizer import FIGURE_EVIDENCE_BUNDLE_PROFILE
    from curve_algorithms.schema import CurveBundle, CurveDomain, curve_crossings, _raw_value_at
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 1, 7, 1), fill="black")
    draw.line((10, 1, 15, 1), fill="red")
    draw.line((2, 12, 14, 12), fill="black")
    draw.line((15, 20, 27, 20), fill="black")
    seeds = ((2, 12), (14, 12), (15, 20), (27, 20))
    values = [1, 100] if scale == "log10" else [0, 25]
    if descending:
        values.reverse()
    source, request = _source_request(image, [
        _series(seeds=seeds),
        _series("red", color="#ff0000", seeds=seeds, binding_bbox=[10, 0, 16, 2]),
    ], axis_calibration={
        "x": {"scale": scale, "unit": "x", "ticks": [[2, values[0]], [27, values[1]]]},
        "y": {"scale": "linear", "unit": "y", "ticks": [[3, 25], [27, 0]]},
    })
    files, _ = build_digitized_figure_bundle(source, request)
    tables = {f"curve_table__main__{key}": files[f"curve_tables/main--{key}.csv"][0]
        for key in ("black", "red")}
    for raw in tables.values():
        assert [len(run) for run in _runs(raw)] == [13, 13]
    bundle, audits = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables=tables)
    audit = {"series": [item.as_dict() for item in audits]}
    for curve in bundle.series:
        assert curve.availability.status == "available"
        assert len(curve.points) == 26
        assert all(a.x < b.x for a, b in zip(curve.points, curve.points[1:]))
        assert len(curve.valid_intervals) == 2
        left, right = curve.valid_intervals
        assert left.stop < right.start
        assert _raw_value_at(curve, (left.start + left.stop) / 2, "linear_y") == curve.points[0].y
        assert _raw_value_at(curve, (right.start + right.stop) / 2, "linear_y") == curve.points[-1].y
        # Both observed endpoints remain readable; the edge between them is not.
        assert _raw_value_at(curve, left.stop, "linear_y") == curve.points[0].y
        assert _raw_value_at(curve, right.start, "linear_y") == curve.points[-1].y
        with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
            _raw_value_at(curve, (left.stop + right.start) / 2, "linear_y")
        domain = CurveDomain(start=curve.points[0].x, stop=curve.points[-1].x, unit="x")
        assert curve_crossings(curve, 11, "either", domain, "linear_y") == ()
        assert curve.shared_support[0].member_series == ("black", "red")
        assert curve.shared_support[0].source_pixel_ranges == ((2, 28),)
    for series_audit in audit["series"]:
        assert [row["pixel_x"] for row in series_audit["shared_pixel_provenance"]] == list(range(2, 28))
    legacy, _ = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables=tables, profile=FIGURE_EVIDENCE_BUNDLE_PROFILE)
    for curve in legacy.series:
        assert curve.availability.status == "available" and len(curve.points) == 26
        assert all(a.x < b.x for a, b in zip(curve.points, curve.points[1:]))
        assert not curve.valid_intervals and not curve.exclusions  # Existing V1 policy.
        assert curve.shared_support[0].source_pixel_ranges == ((2, 28),)


def test_crossing_consumer_cannot_invent_a_crossing_across_an_unsupported_gap():
    from curve_algorithms.schema import (CurveSeries, CurvePoint, CurveAxis, CurveInterval,
        CurveAvailability, CurveDomain, curve_crossings, _raw_value_at)
    series = CurveSeries(series_key="gap", case_key="synthetic", role="gap",
        x_axis=CurveAxis(name="x", unit="x", scale="linear"),
        y_axis=CurveAxis(name="y", unit="y", scale="linear"),
        points=tuple(CurvePoint(x=x, y=y) for x, y in ((0, 0), (1, 0), (3, 10), (4, 10))),
        valid_intervals=(CurveInterval(start=0, stop=1), CurveInterval(start=3, stop=4)),
        exclusions=(CurveInterval(start=1, stop=3),),
        availability=CurveAvailability(status="available", rationale="Synthetic supported segments."),
        source_locator="synthetic raster")
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(series, 2, "linear_y")
    assert curve_crossings(series, 5, "either", CurveDomain(start=0, stop=4, unit="x"), "linear_y") == ()


def test_synthetic_raster_local_candidates_do_not_imply_physical_multivalued_behavior():
    # Public, deterministic geometry reproduces disconnected column candidates
    # and two empty columns without depending on the private publication image.
    from figure_algorithms.figure_line_tracker import _column_candidates
    image = Image.new("RGB", (930, 660), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((890, 40, 909, 192), fill="black")
    draw.rectangle((890, 240, 909, 248), fill="black")
    candidates = _column_candidates(image, x=902, top=40, bottom=280, rgb=(0, 0, 0), tolerance=24, exclusions=())
    assert [(item["support_min"], item["support_max"]) for item in candidates] == [(40, 192), (240, 248)]
    assert _column_candidates(image, x=814, top=40, bottom=650, rgb=(0, 0, 0), tolerance=24, exclusions=()) == []
    assert _column_candidates(image, x=815, top=40, bottom=650, rgb=(0, 0, 0), tolerance=24, exclusions=()) == []
    # This verifies geometry, not scientific identity or paper calibration.
    trace = trace_continuous_line(image, rgb=(0, 0, 0), tolerance=24,
        seeds=((890, 245), (909, 245)), bounds=(890, 40, 910, 280), exclusions=())
    assert trace.points
    for point in trace.points:
        pixel = image.getpixel((point.pixel_x_raw, point.pixel_y_raw))
        assert sum(channel ** 2 for channel in pixel) <= 24 ** 2
        assert any(low <= point.pixel_y_subpixel <= high for low, high in point.pixel_y_support_ranges)
        assert math.isfinite(point.local_slope) and math.isfinite(point.y_localization_uncertainty_px)


def test_original_figure_fixture_exports_calibrated_points_and_shared_provenance():
    # Consume the unchanged family as plain scientific files; no Artifact/Run
    # reconstruction or historical renderer invocation is involved.
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures/figure_digitizer_v1_family.json').read_bytes())
    files = {name: base64.b64decode(item['base64']) for name, item in fixture['files'].items()}
    original = dict(files)
    manifest = json.loads(files['figure_manifest/evidence.json'])
    report = json.loads(files['validation_reports/validation_report.json'])
    assert hashlib.sha256(files['figure_manifest/evidence.json']).hexdigest() == report['manifest_sha256']
    assert hashlib.sha256(files['source_panels/main.png']).hexdigest() == manifest['source']['image_sha256']
    tables = {f'curve_table__main__{key}': files[f'curve_tables/main--{key}.csv'] for key in ('black','red')}
    bundle, audits = normalize_figure_evidence(manifest_content=files['figure_manifest/evidence.json'],
        validation_report_content=files['validation_reports/validation_report.json'], curve_tables=tables)
    by_key = {curve.series_key:curve for curve in bundle.series}
    for key, curve in by_key.items():
        rows = list(csv.DictReader(io.StringIO(tables[f'curve_table__main__{key}'].decode())))
        assert curve.availability.status == 'available' and len(curve.points) == 16
        # The frozen measured ticks independently imply x=pixel_x-2 and
        # y=17-pixel_y. Do not compare new manifests or re-render old figures.
        assert [p.x for p in curve.points] == pytest.approx([float(r['pixel_x_subpixel'])-2 for r in rows])
        assert [p.y for p in curve.points] == pytest.approx([17-float(r['pixel_y_subpixel']) for r in rows])
        assert curve.x_axis.unit == 'V' and curve.y_axis.unit == 'A'
        assert curve.shared_support[0].member_series == ('black','red')
        assert curve.shared_support[0].source_pixel_ranges == ((8,11),)
    assert all(a.input_row_count == 16 and a.observed_row_count == 16 for a in audits)
    assert files == original
    damaged = dict(tables); damaged['curve_table__main__black'] += b'\n'
    with pytest.raises(ValueError, match='bind the exact reference table'):
        normalize_figure_evidence(manifest_content=files['figure_manifest/evidence.json'],
            validation_report_content=files['validation_reports/validation_report.json'], curve_tables=damaged)
