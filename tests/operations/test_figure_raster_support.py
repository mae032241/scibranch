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

from curve_figure_evidence.figure_digitization import build_digitized_figure_bundle
from curve_figure_evidence.figure_digitization_contract import materialize_figure_request
from curve_figure_evidence.figure_line_tracker import trace_continuous_line
from curve_figure_evidence.figure_evidence_normalizer import normalize_figure_evidence
from scidiscovery.artifact_agent.schema.common import canonical_json


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


def test_tail_connected_frame_cannot_erase_disconnected_anchored_front():
    image = Image.new("RGB", (80, 80), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 22, 12), fill="black")
    draw.line((26, 17, 43, 34), fill="black")
    draw.line((47, 50, 58, 61), fill="black")
    draw.line((2, 76, 77, 76), fill="black")  # Same-color plot frame.
    draw.line((62, 66, 77, 66), fill="black")
    draw.line((70, 66, 70, 76), fill="black")  # Tail/frame connection.
    seeds = ((12, 12), (32, 23), (52, 55), (63, 66), (68, 66), (75, 66))
    trace = _assert_horizontal_mirror(image, seeds)
    points = {p.pixel_x_raw: p for p in trace.points}
    for x, y in seeds:
        assert points[x].pixel_y_subpixel == pytest.approx(y, abs=0.5)
        assert not points[x].identity_ambiguous
    for x in (23, 24, 25, 44, 45, 46):
        # A remote frame is not evidence for bridging a real missing segment.
        assert x not in points


@pytest.mark.parametrize("frame", [False, True])
@pytest.mark.parametrize("mirror", [False, True])
def test_each_actual_anchor_recovers_its_full_segment_through_remote_frame(frame, mirror):
    image = Image.new("RGB", (42, 42), "white")
    draw = ImageDraw.Draw(image)
    draw.line((2, 12, 20, 12), fill="black")
    draw.line([(25, 4), (36, 12), (38, 12)], fill="black")
    expected = {x: y for x in range(2, 40) for y in range(3, 40)
        if image.getpixel((x, y)) == (0, 0, 0)}
    if frame:
        draw.line([(2, 12), (2, 35), (38, 35), (38, 12)], fill="black")
    seeds = ((18, 12), (31, 8), (36, 12))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        seeds = tuple((41 - x, y) for x, y in reversed(seeds))
    trace = _trace(image, seeds)
    points = {41 - p.pixel_x_raw if mirror else p.pixel_x_raw: p for p in trace.points}
    assert points.keys() == expected.keys()
    for x, y in expected.items():
        # A vertical frame joins the stroke at its end; all other columns
        # retain the exact target pixel, including x=25..29 before the guide.
        if frame and x in (2, 38):
            continue
        assert points[x].pixel_y_raw == y
        assert not points[x].identity_ambiguous
        assert points[x].interpolation_eligible
    assert all(p.pixel_y_raw < 35 for p in points.values())
    assert trace.max_gap_px == 4
    ImageDraw.Draw(image).line((2, 1, 7, 1), fill="black")  # Legend binding.
    source, request = _source_request(image, [_series(seeds=seeds)])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = _rows(files)
    assert {(41 - int(row["pixel_x_raw"]) if mirror else int(row["pixel_x_raw"]),
        int(row["pixel_y_raw"])) for row in rows} == {
        (x, p.pixel_y_raw) for x, p in points.items()}
    bundle, _ = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    from curve_score.schema import _raw_value_at
    gap_x = (41 - 23 if mirror else 23) - 2  # Calibration maps pixel x to x - 2.
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(bundle.series[0], gap_x, "linear_y")


@pytest.mark.parametrize("distractor_width", [1, 2, 3, 8])
def test_anchor_connected_curvature_is_not_replaced_by_guide_nearest_short_line(distractor_width):
    image = Image.new("RGB", (30, 30), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(2, 18), (14, 10), (27, 18)], fill="black")
    draw.line((14, 18, 14 + distractor_width - 1, 18), fill="black")
    trace = _assert_horizontal_mirror(image, ((2, 18), (27, 18)))
    points = {p.pixel_x_raw: p for p in trace.points}
    assert set(points) == set(range(2, 28))
    assert points[14].pixel_y_raw == 10
    assert all(points[x].pixel_y_raw < 18 for x in range(14, 14 + distractor_width))
    assert all(not p.identity_ambiguous for p in points.values())


@pytest.mark.parametrize("seed_x", [20.5, 20.500001, 21.0])
@pytest.mark.parametrize("mirror", [False, True])
def test_half_pixel_anchor_preserves_curved_support_in_both_directions(seed_x, mirror):
    image = Image.new("RGB", (42, 32), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(2, 18), (16, 18), (20, 11), (21, 10), (21, 8), (24, 8)], fill="black")
    draw.line((32, 8, 38, 8), fill="black")
    draw.line((10, 8, 14, 8), fill="black")  # Short guide-nearest distractor.
    seeds = ((seed_x, 8), (36, 8))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        seeds = tuple((41 - x, y) for x, y in reversed(seeds))
    points = {41 - p.pixel_x_raw if mirror else p.pixel_x_raw: p
        for p in _trace(image, seeds).points}
    for x, y in ((12, 18), (23, 8), (35, 8)):
        assert points[x].pixel_y_raw == y
        assert not points[x].identity_ambiguous
        assert points[x].interpolation_eligible
        assert points[x].pixel_y_support_ranges == ((float(y), float(y)),)
    assert all(x not in points for x in range(25, 32))
    ImageDraw.Draw(image).line((2, 1, 7, 1), fill="black")
    source, request = _source_request(image, [_series(seeds=seeds)])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = {41 - int(row["pixel_x_raw"]) if mirror else int(row["pixel_x_raw"]): row
        for row in _rows(files)}
    assert rows[12]["pixel_y_raw"] == "18"
    assert rows[12]["quantitative_measurement_claim_eligible"] == "1"
    assert rows[12]["interpolation_eligible"] == "1"
    bundle, _ = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    from curve_score.schema import _raw_value_at
    plateau_x = (41 - 12.5 if mirror else 12.5) - 2
    # The existing y calibration maps pixel 29 to 0 and pixel 3 to 27.
    assert _raw_value_at(bundle.series[0], plateau_x, "linear_y") == pytest.approx(11 * 27 / 26)
    gap_x = (41 - 28 if mirror else 28) - 2
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(bundle.series[0], gap_x, "linear_y")


def test_singleton_weighted_center_stays_in_exact_pixel_support():
    image = Image.new("RGB", (30, 130), "white")
    ImageDraw.Draw(image).line((2, 114, 27, 114), fill=(0, 3, 5))
    trace = _trace(image, ((2, 114), (27, 114)))
    assert len(trace.points) == 26
    assert all(p.pixel_y_support_min <= p.pixel_y_subpixel <= p.pixel_y_support_max for p in trace.points)


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


def _closed_anchor_branches():
    image = Image.new("RGB", (44, 32), "white")
    draw = ImageDraw.Draw(image)
    draw.line([(2, 10), (4, 8), (18, 8), (20, 10), (22, 8), (36, 8),
        (38, 10), (40, 10)], fill="black")
    draw.line([(2, 10), (4, 12), (36, 12), (38, 10)], fill="black")
    # A remote frame puts both branches into one component without making the
    # lower branch pass through the interior seed on the upper branch.
    draw.line([(2, 10), (2, 25), (40, 25), (40, 10)], fill="black")
    return image


@pytest.mark.parametrize("first_seed", [(20, 10), (20, 10.25), (19, 9)])
@pytest.mark.parametrize("mirror", [False, True])
def test_competing_branch_cannot_borrow_an_anchor_it_does_not_visit(first_seed, mirror):
    image = _closed_anchor_branches()
    seeds = (first_seed, (38, 10))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        seeds = tuple((43 - x, y) for x, y in reversed(seeds))
    seed_x = 43 - first_seed[0] if mirror else first_seed[0]
    point = next(p for p in _trace(image, seeds).points if p.pixel_x_raw == seed_x)
    assert point.pixel_y_raw == int(first_seed[1])
    assert not point.identity_ambiguous
    assert point.interpolation_eligible
    assert point.pixel_y_support_ranges == ((float(int(first_seed[1])),) * 2,)
    ImageDraw.Draw(image).line((2, 1, 7, 1), fill="black")
    source, request = _source_request(image, [_series(seeds=seeds)])
    files, _ = build_digitized_figure_bundle(source, request)
    rows = {int(row["pixel_x_raw"]): row for row in _rows(files)}
    assert rows[seed_x]["quantitative_measurement_claim_eligible"] == "1"
    assert rows[seed_x]["interpolation_eligible"] == "1"
    bundle, _ = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    from curve_score.schema import _raw_value_at
    next_x = seed_x + (-1 if mirror else 1)
    # Read an interior value through the production CSV and normalizer path.
    assert _raw_value_at(bundle.series[0], (seed_x + next_x) / 2 - 2, "linear_y") == pytest.approx(
        (float(rows[seed_x]["y_value"]) + float(rows[next_x]["y_value"])) / 2)


@pytest.mark.parametrize("first_seed_x", [2, 2.5])
@pytest.mark.parametrize("mirror", [False, True])
def test_closed_branches_with_the_same_actual_anchors_remain_ambiguous(mirror, first_seed_x):
    image = _closed_anchor_branches()
    seeds = ((first_seed_x, 10), (38, 10))
    if mirror:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        seeds = tuple((43 - x, y) for x, y in reversed(seeds))
    x = 23 if mirror else 20
    point = next(p for p in _trace(image, seeds).points if p.pixel_x_raw == x)
    assert point.identity_ambiguous
    assert not point.interpolation_eligible
    assert point.pixel_y_support_ranges == ((10.0, 10.0), (12.0, 12.0))
    ImageDraw.Draw(image).line((2, 1, 7, 1), fill="black")
    source, request = _source_request(image, [_series(seeds=seeds)])
    files, _ = build_digitized_figure_bundle(source, request)
    row = next(row for row in _rows(files) if int(row["pixel_x_raw"]) == x)
    assert row["quantitative_measurement_claim_eligible"] == row["interpolation_eligible"] == "0"
    bundle, _ = normalize_figure_evidence(
        manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    from curve_score.schema import _raw_value_at
    with pytest.raises(ValueError, match="interpolation_curve_unavailable"):
        _raw_value_at(bundle.series[0], x - 2 + (-0.5 if mirror else 0.5), "linear_y")


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
    from curve_score.schema import CurveDomain, curve_crossings, _raw_value_at
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
    from curve_score.schema import CurveDomain, curve_crossings, _raw_value_at
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
    from curve_score.schema import _raw_value_at
    with pytest.raises(ValueError, match="interpolation_outside_supported_interval"):
        _raw_value_at(bundle.series[1], 10.5, "linear_y")


@pytest.mark.parametrize("box", [(10, 11, 15, 14), (10, 14, 15, 15)])
def test_shared_allocator_checks_both_raw_and_subpixel_receiving_coordinates(box):
    from curve_figure_evidence.figure_line_tracker import TracePoint
    from curve_figure_evidence.figure_digitization import _derive_shared_support
    from curve_figure_evidence.figure_digitization_contract import FigureDigitizationRequest
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
    from curve_figure_evidence.figure_digitization import _derive_shared_support
    from curve_figure_evidence.figure_digitization_contract import FigureDigitizationRequest
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


def _figure_worker(tmp_path):
    from curve_figure_evidence.plugin import PLUGIN as FIGURE
    from curve_score.plugin import PLUGIN as CURVE
    from scidiscovery.builtin_plugin import CORE_PLUGIN
    from scidiscovery.general_science_plugin import PLUGIN as GENERAL
    from scidiscovery.operations.catalog import compile_catalog
    from scidiscovery.artifact_agent.runtime import open_runtime
    from scidiscovery.artifact_agent.interfaces.mcp_root import RootMCPRouter, RootToolFacade
    from scidiscovery.artifact_agent.schema.artifact import ArtifactRegistration
    from tests.operations.worker_fixtures import attached_worker
    from tests.operations.test_minimal_figure_extraction import _png, _request
    catalog = compile_catalog((CORE_PLUGIN, GENERAL, CURVE, FIGURE))
    project = tmp_path / "project"
    project.mkdir()
    runtime = open_runtime(project_root=project, state_root=tmp_path / "state", worker_backend="local")
    runtime.runs.operation_catalog = catalog
    instance = runtime.scheduler_bindings.create_instance(name="raster_support", title="Raster support regression",
        objective="Preserve source support, limits, and shared dependency.")
    root = RootMCPRouter(RootToolFacade(runtime.artifacts, runtime.intake, runs=runtime.runs,
        approvals=runtime.approvals, executions=runtime.executions, bindings=runtime.scheduler_bindings,
        instance=instance.instance_id, operation_catalog=catalog))
    source = _png(shared=True)
    artifact = runtime.artifacts.register(source, ArtifactRegistration(kind="paper", schema_id="opaque",
        payload_schema_version=1, media_type="image/png", creator=runtime.actor), idempotency_key="source")
    runtime.scheduler_bindings.bind(instance=instance.instance_id, namespace="artifact", name="original", object_id=artifact.artifact_id)
    root.call_tool("operation_invoke", {"name": "author", "operation_id": "science.evidence.extract.figure.v3",
        "instruction": "Extract the synthetic raster with explicit shared support and local limits.",
        "inputs": [{"port": "paper_source", "artifact_names": ["original"]}]})
    worker = attached_worker(runtime, root, "author")
    opened = worker.call_tool("worker_open_assignment", {})
    request = json.loads(_request(source, shared=True))
    request["source"] = {}
    request.pop("schema_version")
    request["series"][0]["use_limits"] = [{"pixel_bbox": [14, 3, 16, 18],
        "applies_to": "interpolation", "reason": "Synthetic test of a stated interpolation limit."}]
    return runtime, root, worker, opened, request


def test_preview_diagnostics_keep_input_repair_separate_from_internal_defect(tmp_path, monkeypatch):
    from scidiscovery.artifact_agent.interfaces.mcp_worker_protocol import WorkerToolError
    from scidiscovery.plugin_runtime.transport import rpc_error
    from curve_figure_evidence import figure_worker_tool
    from curve_figure_evidence.figure_evidence_validation import FigureEvidenceBundleError
    runtime, root, worker, _, request = _figure_worker(tmp_path)
    wrong = json.loads(json.dumps(request))
    wrong["series"][0]["binding_bbox"] = [2, 0, 35, 2]
    with pytest.raises(WorkerToolError) as caught:
        worker.call_tool("worker_curve_figure_preview", {"request": wrong})
    detail = caught.value.details[0]
    assert detail["repairable"] is True
    assert detail["path"] == "$.request.series[0].binding_bbox"
    assert "black" in detail["message"] and "binding_bbox" in detail["message"]
    assert not runtime.runs.tool_evidence(worker._run_id)
    assert runtime.runs.tool_attempts(worker._run_id)[-1]["state"] == "rejected"

    for error, code in ((FigureEvidenceBundleError("cross-series duplicate pixels require one consistent shared_group"),
            "figure_generation_inconsistent"), (ValueError("UNDECLARED_INTERNAL_DETAIL"), "runtime_failure")):
        def broken(*args, **kwargs):
            raise error
        monkeypatch.setattr(figure_worker_tool, "build_digitized_figure_bundle", broken)
        with pytest.raises(WorkerToolError) as caught:
            worker.call_tool("worker_curve_figure_preview", {"request": request})
        assert caught.value.details[0]["code"] == code
        assert caught.value.details[0]["repairable"] is False
        wire = rpc_error(1, caught.value)
        assert "UNDECLARED_INTERNAL_DETAIL" not in json.dumps(wire)
        if code == "figure_generation_inconsistent":
            assert "program defect" in caught.value.details[0]["message"]
            assert "shared_group" not in json.dumps(wire)
        status = root.call_tool("run_status", {"name": "author", "intent": "status", "diagnostic_after": 0})
        assert status["diagnostic_events"]["events"][-1]["diagnostic"]["details"][0]["code"] == code


def test_real_worker_preview_save_finalization_audit_and_bundle_preserve_limits(tmp_path):
    from tests.operations.test_general_transform_operations import _intake
    from tests.operations.test_l2_run_invariants import _audit_envelope
    from tests.operations.worker_fixtures import attached_worker
    from curve_score.schema import CurveBundle, _raw_value_at
    runtime, root, worker, opened, request = _figure_worker(tmp_path)
    preview = worker.call_tool("worker_curve_figure_preview", {"request": request})
    assert preview["integrity_status"] == "valid"
    saved = worker.call_tool("worker_curve_figure_save", {"request": request})
    frozen = {record["artifact_ref"]["artifact_id"]: runtime.artifacts.read(
        runtime.artifacts.get_by_id(record["artifact_ref"]["artifact_id"]).ref)
        for record in runtime.runs.tool_evidence(worker._run_id)}
    assert saved["integrity_status"] == "valid"
    payload = _intake().model_dump(mode="json")
    payload["scientific_foundation"]["evidence"][0]["source_key"] = "paper_source"
    payload["scientific_foundation"]["items"][0].update(evidence_keys=["paper_source"],
        statement="Synthetic digitized curves retain shared raster support and a local interpolation restriction.")
    Path(opened["output_directory"], "result.json").write_bytes(canonical_json({"payload": payload,
        "handoff": {"verdict": "pass", "summary": "The synthetic source and its limitations are preserved."}}))
    assert worker.call_tool("worker_submit_result", {})["state"] == "completed"
    audit_request = {"name": "audit", "operation_id": "science.figure.evidence.audit.v2",
        "instruction": "Independently audit the exact synthetic family and its limits.",
        "inputs": [{"port": "scientific_intake", "artifact_names": ["author.output"]}]}
    root.call_tool("operation_invoke", audit_request)
    auditor = attached_worker(runtime, root, "audit")
    audit_opened = auditor.call_tool("worker_open_assignment", {})
    Path(audit_opened["output_directory"], "result.json").write_bytes(_audit_envelope("scientific_intake"))
    assert auditor.call_tool("worker_submit_result", {})["state"] == "completed"
    invocation = root.call_tool("operation_invoke", {"name": "bundle",
        "operation_id": "scidiscovery.curve-bundle.figure-evidence.v3",
        "inputs": [{"port": "scientific_intake", "artifact_names": ["author.output"]},
            {"port": "evidence_audit", "artifact_names": ["audit.output"]}]})
    output_name = invocation["result"]["outputs"][0]["artifact_name"]
    binding = runtime.scheduler_bindings.resolve(instance=root.facade.instance, namespace="artifact", name=output_name)
    bundle = CurveBundle.model_validate_json(runtime.artifacts.read(runtime.artifacts.get_by_id(binding).ref), strict=True)
    black, red = bundle.series
    assert black.shared_support == red.shared_support
    assert black.shared_support[0].member_series == ("black", "red")
    assert len(black.points) == 16  # Includes shared observations and use-limited pixels.
    with pytest.raises(ValueError, match="outside_supported_interval"):
        _raw_value_at(black, 12.5, "linear_y")
    assert all(runtime.artifacts.read(runtime.artifacts.get_by_id(key).ref) == raw for key, raw in frozen.items())


def _controlled_test_family(source, request, files, algorithm):
    from curve_figure_evidence.figure_family import (FigureFamilyMember, SelectedFigureFamily,
        SELECTION_ITEM, REQUEST_ITEM)
    from curve_figure_evidence.scientific_files import project_figure_file
    from scidiscovery.artifact_agent.schema.refs import ArtifactRef
    files = dict(files)
    files[REQUEST_ITEM] = (request, "application/json")
    source_ref = ArtifactRef(artifact_id="synthetic_original", sha256=hashlib.sha256(source).hexdigest(), kind="paper", schema_id="opaque")
    request_digest = hashlib.sha256(request).hexdigest()
    records, contents, members = [], {}, []
    def record(name, raw, media):
        public, control = project_figure_file(name, raw)
        ref = ArtifactRef(artifact_id=f"synthetic_{len(records)}", sha256=hashlib.sha256(public).hexdigest(), kind="figure_evidence_file", schema_id="opaque")
        records.append({"artifact_ref": ref.model_dump(mode="json"), "source_ref": source_ref.model_dump(mode="json"),
            "media_type": media, "size_bytes": len(public), "tool_name": "worker_curve_figure_save",
            "metadata": {"data_item": name, "request_sha256": request_digest, "algorithm": algorithm,
                "figure_control": control}})
        contents[ref] = public
        return FigureFamilyMember(data_item=name, artifact_ref=ref, media_type=media, size_bytes=len(public))
    for name, (raw, media) in files.items():
        members.append(record(name, raw, media))
    selection = SelectedFigureFamily(source_ref=source_ref, algorithm=algorithm,
        request_sha256=request_digest, members=tuple(members))
    record(SELECTION_ITEM, canonical_json(selection.model_dump(mode="json")), "application/json")
    return records, contents, source_ref, files


def test_original_v1_v6_family_replays_without_rewriting_its_bytes():
    from curve_figure_evidence.figure_family import selected_family_files
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/figure_digitizer_v1_family.json").read_bytes())
    assert fixture["origin_commit"] == "0c49a22"
    source = base64.b64decode(fixture["source_base64"])
    request = base64.b64decode(fixture["request_base64"])
    original_files = {name: (base64.b64decode(item["base64"]), item["media_type"])
        for name, item in fixture["files"].items()}
    records, contents, source_ref, original_files = _controlled_test_family(
        source, request, original_files, "curve-figure-digitizer.v1")
    frozen = dict(contents)
    assert selected_family_files(records, contents, source_ref) == original_files
    assert contents == frozen
    assert json.loads(original_files["validation_reports/validation_report.json"][0])["validator_version"] == "6"


@pytest.mark.parametrize("field", ["exclusion_regions", "use_limits"])
def test_selected_family_cannot_omit_or_change_frozen_scientific_restrictions(field):
    from curve_figure_evidence.figure_family import selected_family_files
    from curve_figure_evidence.figure_digitization_contract import FigureDigitizationRequest
    from curve_figure_evidence.figure_evidence_validation import build_figure_evidence_validation_report
    source, request = _shared_exclusion_request("recipient", use_limits=[{
        "pixel_bbox": [20, 3, 22, 28], "reason": "The author forbids local interpolation.", "applies_to": "interpolation"}])
    # Real Worker saves serialize typed defaults before hashing the request.
    request = canonical_json(FigureDigitizationRequest.model_validate_json(request).model_dump(mode="json"))
    files, _ = build_digitized_figure_bundle(source, request)
    records, contents, source_ref, _ = _controlled_test_family(source, request, files, "curve-figure-digitizer.v2")
    assert selected_family_files(records, contents, source_ref)
    manifest = json.loads(files["figure_manifest/evidence.json"][0])
    red = manifest["panels"][0]["series"][1]
    if field == "exclusion_regions":
        red[field] = []
    else:
        red[field][0]["reason"] = "An altered scientific rationale."
    manifest_raw = canonical_json(manifest)
    report = build_figure_evidence_validation_report(manifest_data_item="figure_manifest/evidence.json",
        manifest_content=manifest_raw, sibling_files={name: item for name, item in files.items()
            if name not in {"figure_manifest/evidence.json", "validation_reports/validation_report.json"}})
    files["figure_manifest/evidence.json"] = (manifest_raw, "application/json")
    files["validation_reports/validation_report.json"] = (canonical_json(report), "application/json")
    records, contents, source_ref, _ = _controlled_test_family(source, request, files, "curve-figure-digitizer.v2")
    with pytest.raises(ValueError, match="scientific restrictions differ from the frozen request"):
        selected_family_files(records, contents, source_ref)


@pytest.mark.parametrize("box", [[12, 11, 13, 14], [12, 14, 13, 15]])
def test_primary_csv_validator_rejects_excluded_raw_or_subpixel_observation(box):
    from curve_figure_evidence.figure_evidence_validation import build_figure_evidence_validation_report, FigureEvidenceBundleError
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
    from curve_figure_evidence.figure_evidence_validation import build_figure_evidence_validation_report, FigureEvidenceBundleError
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
    from curve_figure_evidence.figure_numeric_redraw import _runs
    assert [[int(row["pixel_x_raw"]) for row in run]
        for run in _runs(files["curve_tables/main--black.csv"][0])] == [list(range(2, 15)), list(range(15, 28))]
    bundle, _ = normalize_figure_evidence(manifest_content=files["figure_manifest/evidence.json"][0],
        validation_report_content=files["validation_reports/validation_report.json"][0],
        curve_tables={"curve_table__main__black": files["curve_tables/main--black.csv"][0]})
    curve = bundle.series[0]
    assert [(interval.start, interval.stop) for interval in curve.valid_intervals] == [(0, 12), (13, 25)]
    from curve_score.schema import _raw_value_at, curve_crossings, CurveDomain
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
    from curve_figure_evidence.operation_transforms import bundle_figure_evidence_outputs
    from curve_figure_evidence.figure_numeric_redraw import _runs
    from curve_figure_evidence.figure_evidence_normalizer import FIGURE_EVIDENCE_BUNDLE_PROFILE
    from curve_score.schema import CurveBundle, CurveDomain, curve_crossings, _raw_value_at
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
    outputs = bundle_figure_evidence_outputs({
        "figure_manifest": files["figure_manifest/evidence.json"][0],
        "validation_report": files["validation_reports/validation_report.json"][0],
        **tables})
    bundle = CurveBundle.model_validate_json(outputs["curve_bundle"][0])
    audit = json.loads(outputs["normalization_audit"][0])
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
    from curve_score.schema import (CurveSeries, CurvePoint, CurveAxis, CurveInterval,
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
    from curve_figure_evidence.figure_line_tracker import _column_candidates
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


@pytest.mark.parametrize("notch",[False,True])
@pytest.mark.parametrize("mirror",[False,True])
def test_reacquired_frame_does_not_inherit_distant_target_anchor_span(notch,mirror):
    image=Image.new("RGB",(30,30),"white"); d=ImageDraw.Draw(image)
    for a,b in [(2,6),(13,16),(22,27)]: d.line((a,10,b,10),fill="black")
    d.line([(2,25),(7,25),(9,11),(12,25),(17,25),(20,11),(22,25),(27,25)] if notch else [(2,25),(27,25)],fill="black")
    for x in [4,24]: d.line((x,10,x,25),fill="black")
    seeds=((4,10),(15,10),(24,10))
    if mirror:
        image=image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        seeds=tuple((29-x,y) for x,y in reversed(seeds))
    trace=_trace(image, seeds)
    byx={29-p.pixel_x_raw if mirror else p.pixel_x_raw:p for p in trace.points}
    assert byx[15].pixel_y_raw==10,[(x,p.pixel_y_raw,p.identity_ambiguous,p.interpolation_eligible) for x,p in sorted(byx.items())]
    assert not byx[15].identity_ambiguous
