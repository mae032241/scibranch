"""Small raster regressions for actual anchors; no old service/installer fixtures."""
import pytest
from PIL import Image, ImageDraw

from figure_algorithms.figure_line_tracker import trace_continuous_line


def _trace(image, seeds):
    return trace_continuous_line(image, rgb=(0, 0, 0), tolerance=24, seeds=seeds,
        bounds=(2, 3, image.width - 2, image.height - 2), exclusions=())


def _orientations(image, seeds):
    for mirrored in (False, True):
        picture = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT) if mirrored else image
        anchors = tuple((image.width - 1 - x, y) for x, y in reversed(seeds)) if mirrored else seeds
        trace = _trace(picture, anchors)
        points = {image.width - 1 - point.pixel_x_raw if mirrored else point.pixel_x_raw: point
            for point in trace.points}
        yield trace, points


def test_disconnected_anchors_survive_tail_frame_without_bridging_gaps():
    image = Image.new('RGB', (80, 80), 'white'); draw = ImageDraw.Draw(image)
    for line in ((2, 12, 22, 12), (26, 17, 43, 34), (47, 50, 58, 61),
            (2, 76, 77, 76), (62, 66, 77, 66), (70, 66, 70, 76)):
        draw.line(line, fill='black')
    seeds = ((12, 12), (32, 23), (52, 55), (63, 66), (68, 66), (75, 66))
    for _, points in _orientations(image, seeds):
        for x, y in seeds:
            assert points[x].pixel_y_subpixel == pytest.approx(y, abs=0.5)
            assert not points[x].identity_ambiguous
        assert not set(points).intersection((23, 24, 25, 44, 45, 46))


def test_every_actual_anchor_recovers_full_stroke_inside_one_frame_component():
    image = Image.new('RGB', (42, 42), 'white'); draw = ImageDraw.Draw(image)
    draw.line((2, 12, 20, 12), fill='black')
    draw.line([(25, 4), (36, 12), (38, 12)], fill='black')
    # Independent oracle: the target's original raster before adding the frame.
    expected = {x:y for x in range(2, 40) for y in range(3, 40) if image.getpixel((x, y)) == (0, 0, 0)}
    draw.line([(2, 12), (2, 35), (38, 35), (38, 12)], fill='black')
    for trace, points in _orientations(image, ((18, 12), (31, 8), (36, 12))):
        assert points.keys() == expected.keys()
        for x, y in expected.items():
            if x in (2, 38):
                continue  # Frame joins the exact target at these end columns.
            assert points[x].pixel_y_raw == y
            assert not points[x].identity_ambiguous and points[x].interpolation_eligible
        assert all(point.pixel_y_raw < 35 for point in points.values())
        assert trace.max_gap_px == 4


def test_actual_anchor_path_beats_nearer_straight_guide_distractor():
    image = Image.new('RGB', (30, 30), 'white'); draw = ImageDraw.Draw(image)
    draw.line([(2, 18), (14, 10), (27, 18)], fill='black')
    draw.line((14, 18, 16, 18), fill='black')
    for _, points in _orientations(image, ((2, 18), (27, 18))):
        assert set(points) == set(range(2, 28))
        assert points[14].pixel_y_raw == 10
        assert all(points[x].pixel_y_raw < 18 for x in (14, 15, 16))
        assert all(not point.identity_ambiguous for point in points.values())


def test_half_pixel_anchor_keeps_support_on_both_sides_and_mirror():
    image = Image.new('RGB', (42, 32), 'white'); draw = ImageDraw.Draw(image)
    draw.line([(2, 18), (16, 18), (20, 11), (21, 10), (21, 8), (24, 8)], fill='black')
    draw.line((32, 8, 38, 8), fill='black'); draw.line((10, 8, 14, 8), fill='black')
    for _, points in _orientations(image, ((20.5, 8), (36, 8))):
        for x, y in ((12, 18), (23, 8), (35, 8)):
            assert points[x].pixel_y_raw == y
            assert not points[x].identity_ambiguous and points[x].interpolation_eligible
            assert points[x].pixel_y_support_ranges == ((float(y), float(y)),)
        assert not set(points).intersection(range(25, 32))


def test_branch_must_visit_actual_anchor_and_equal_supported_branches_stay_ambiguous():
    image = Image.new('RGB', (44, 32), 'white'); draw = ImageDraw.Draw(image)
    draw.line([(2, 10), (4, 8), (18, 8), (20, 10), (22, 8), (36, 8), (38, 10), (40, 10)], fill='black')
    draw.line([(2, 10), (4, 12), (36, 12), (38, 10)], fill='black')
    draw.line([(2, 10), (2, 25), (40, 25), (40, 10)], fill='black')
    for _, points in _orientations(image, ((20, 10), (38, 10))):
        point = points[20]
        assert point.pixel_y_raw == 10 and not point.identity_ambiguous and point.interpolation_eligible
        assert point.pixel_y_support_ranges == ((10.0, 10.0),)
    for _, points in _orientations(image, ((2, 10), (38, 10))):
        point = points[20]
        assert point.identity_ambiguous and not point.interpolation_eligible
        assert point.pixel_y_support_ranges == ((10.0, 10.0), (12.0, 12.0))


def test_weighted_singleton_center_stays_in_its_observed_pixel_support():
    image = Image.new('RGB', (30, 130), 'white')
    ImageDraw.Draw(image).line((2, 114, 27, 114), fill=(0, 3, 5))
    trace = _trace(image, ((2, 114), (27, 114)))
    assert len(trace.points) == 26
    assert all(point.pixel_y_support_min == point.pixel_y_subpixel == point.pixel_y_support_max == 114
        for point in trace.points)
