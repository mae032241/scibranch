"""Small deterministic tracker for visibly continuous, guide-anchored plot lines."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from typing import Iterable

from PIL import Image


@dataclass(frozen=True, slots=True)
class TracePoint:
    point_index: int
    pixel_x_raw: int
    pixel_y_raw: int
    pixel_x_subpixel: float
    pixel_y_subpixel: float
    uncertainty_px: float
    support_kind: str = "direct_pixel"
    coordinate_ownership: str = "exclusive"
    shared_group: str = ""
    support_source_series: str = ""
    shared_eligible: bool = False
    identity_ambiguous: bool = False
    interpolation_eligible: bool = True
    measurement_location_resolved: bool = True
    pixel_y_support_min: float = 0.0
    pixel_y_support_max: float = 0.0
    interpolation_reason: str = ""
    pixel_y_support_ranges: tuple[tuple[float, float], ...] = ()
    cluster_y_min: float = 0.0
    cluster_y_max: float = 0.0
    local_slope: float = 0.0
    normal_line_width_px: float = 1.0
    pixel_resolution_px: float = 1.0
    x_localization_uncertainty_px: float = 0.5
    y_localization_uncertainty_px: float = 0.5


# Pixel geometry tolerances belong to the extractor, never to scientific input.
DIRECTION_RADIUS_PX = 2
AMBIGUITY_SCORE_MARGIN_PX = 1.0
NORMAL_SAMPLE_STEP_PX = 0.25
CENTER_REFINEMENT_PASSES = 2
GUIDE_REACQUISITION_DISTANCE_PX = 3.0


@dataclass(frozen=True, slots=True)
class LineFinding:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class LineTrace:
    points: tuple[TracePoint, ...]
    visible_fraction: float
    max_gap_px: int
    ambiguous_fraction: float
    findings: tuple[LineFinding, ...]


def _matches(pixel: tuple[int, ...], rgb: tuple[int, int, int], tolerance: float) -> bool:
    return sum(
        (int(pixel[index]) - rgb[index]) ** 2 for index in range(3)
    ) <= tolerance**2


def _excluded(
    x: int, y: int, regions: Iterable[tuple[int, int, int, int]]
) -> bool:
    return any(
        left <= x < right and top <= y < bottom
        for left, top, right, bottom in regions
    )


def guide_y(
    seeds: tuple[tuple[float, float], ...], x: float, fallback: float
) -> float:
    if x <= seeds[0][0]:
        return seeds[0][1]
    if x >= seeds[-1][0]:
        return seeds[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(seeds, seeds[1:]):
        if left_x <= x <= right_x:
            if right_x == left_x:
                return (left_y + right_y) / 2.0
            fraction = (x - left_x) / (right_x - left_x)
            return left_y + fraction * (right_y - left_y)
    return fallback


def _column_candidates(
    image: Image.Image,
    *,
    x: int,
    top: int,
    bottom: int,
    rgb: tuple[int, int, int],
    tolerance: float,
    exclusions: tuple[tuple[int, int, int, int], ...],
) -> list[dict[str, float]]:
    pixels = image.load()
    matching = [
        y
        for y in range(top, bottom)
        if not _excluded(x, y, exclusions)
        and _matches(pixels[x, y], rgb, tolerance)
    ]
    if not matching:
        return []
    groups: list[list[int]] = [[matching[0]]]
    for y in matching[1:]:
        if y == groups[-1][-1] + 1:
            groups[-1].append(y)
        else:
            groups.append([y])
    result: list[dict[str, float]] = []
    for group in groups:
        weights = []
        for y in group:
            distance = math.sqrt(
                sum(
                    (int(pixels[x, y][index]) - rgb[index]) ** 2
                    for index in range(3)
                )
            )
            weights.append(max(1.0, tolerance + 1.0 - distance))
        subpixel_y = sum(
            y * weight for y, weight in zip(group, weights)
        ) / sum(weights)
        # A positive weighted mean is inside its support mathematically; keep
        # floating-point rounding from placing a singleton just outside it.
        subpixel_y = min(float(group[-1]), max(float(group[0]), subpixel_y))
        raw_y = min(group, key=lambda y: (abs(y - subpixel_y), y))
        result.append(
            {
                "raw_y": float(raw_y),
                "subpixel_y": subpixel_y,
                "uncertainty_px": max(0.5, len(group) / 2.0),
                "support_min": float(group[0]),
                "support_max": float(group[-1]),
            }
        )
    return result


def _connected(a: dict[str, float], b: dict[str, float]) -> bool:
    # Eight-connected raster support, including a steep stroke's overlapping
    # projections. Center displacement alone is not a connectivity test.
    return _support_ranges_connected(((a["support_min"], a["support_max"]),),
        ((b["support_min"], b["support_max"]),))


def _support_ranges_connected(left, right):
    """Test an adjacent-column edge; legacy tables have no support ranges."""
    return not left or not right or any(max(a[0] - b[1], b[0] - a[1]) <= 1.0
        for a in left for b in right)


def _guide_distance(candidate, y):
    return max(0.0, candidate["support_min"] - y, y - candidate["support_max"])


def _identity_supported_columns(columns, seeds, fallback):
    """Admit disconnected strokes by guide identity, never by their length.

    A component anchored anywhere can extend beyond the seed endpoints. Other
    components can retain dashed/occluded pieces near the guide.
    Disconnected segments must not compete by a global anchor count: a frame
    connected to the tail can otherwise erase the plateau and falling edge.
    Identity precedence is resolved after continuous paths have been grown.
    """
    parents = {}

    def root(node):
        while parents[node] != node:
            parents[node] = parents[parents[node]]
            node = parents[node]
        return node

    for x, candidates in columns.items():
        for j, candidate in enumerate(candidates):
            node = (x, j)
            parents[node] = node
            for k, previous in enumerate(columns.get(x - 1, ())):
                if _connected(previous, candidate):
                    parents[root((x - 1, k))] = root(node)
    components = {}
    for node in parents:
        components.setdefault(root(node), []).append(node)
    seed_distances = [min((_guide_distance(candidate, y)
        for candidate in columns.get(int(math.floor(x + 0.5)), ())), default=math.inf)
        for x, y in seeds]
    supported_seed_errors = [distance for distance in seed_distances
        if distance <= GUIDE_REACQUISITION_DISTANCE_PX]
    guide_limit = min(GUIDE_REACQUISITION_DISTANCE_PX,
        max(supported_seed_errors, default=0.0) + AMBIGUITY_SCORE_MARGIN_PX)
    admitted = set()
    for key, nodes in components.items():
        component_columns = {}
        for x, j in nodes:
            component_columns.setdefault(x, []).append(columns[x][j])
        anchors = sum(any(_guide_distance(candidate, y) <= min(
            GUIDE_REACQUISITION_DISTANCE_PX, nearest + 0.5)
            for candidate in component_columns.get(int(math.floor(x + 0.5)), ()))
            for (x, y), nearest in zip(seeds, seed_distances))
        if anchors or any(_guide_distance(candidate, guide_y(seeds, x, fallback))
                <= guide_limit
                for x, j in nodes for candidate in (columns[x][j],)):
            admitted.add(key)
    result = {}
    for x, candidates in columns.items():
        result[x] = [candidate for j, candidate in enumerate(candidates)
            if root((x, j)) in admitted]
    retained = {id(candidate) for candidates in result.values() for candidate in candidates}
    retained_components = []
    for nodes in components.values():
        component = {}
        for x, j in nodes:
            candidate = columns[x][j]
            if id(candidate) in retained:
                component.setdefault(x, []).append(candidate)
        if component:
            retained_components.append(component)
    return result, guide_limit, retained_components


def _continuous_paths(columns, seeds, fallback):
    """Grow from supported identity anchors in both directions, without clipping.

    The same natural-end rule applies to each side of an interior anchor. Equal
    anchors are integrated as candidate evidence, so scan order cannot decide
    identity. Disconnected components retain their own observed gaps.
    """
    _, guide_limit, components = _identity_supported_columns(columns, seeds, fallback)
    seed_anchors = tuple((int(math.floor(x + 0.5)), y) for x, y in seeds)
    identity_support = {}
    candidates_by_x = {}
    for component in components:
        midpoint = (min(component) + max(component)) / 2
        sites = []
        for x, seed_y in seed_anchors:
            candidates = component.get(x, ())
            nearest = min((_guide_distance(candidate, seed_y) for candidate in candidates), default=math.inf)
            if nearest <= guide_limit:
                sites.append(((nearest, abs(x - midpoint)), x, seed_y))
        if not sites:
            for x, candidates in component.items():
                y = guide_y(seeds, x, fallback)
                nearest = min(_guide_distance(candidate, y) for candidate in candidates)
                if nearest <= guide_limit:
                    sites.append(((nearest, abs(x - midpoint)), x, y))
            if sites:
                best = min(score for score, _, _ in sites)
                sites = [site for site in sites if site[0] == best]
        if not sites:
            continue
        # Each real anchor must recover both sides of its own stroke. A remote
        # frame can join separately ending strokes into one raster component;
        # growth from one seed then only reacquires the others near the guide.
        # Guide-only components still use their best sites above, rather than
        # promoting every near-guide pixel to an independent identity anchor.
        for _, anchor_x, anchor_y in sites:
            nearest = min(_guide_distance(candidate, anchor_y) for candidate in component[anchor_x])
            anchor_candidates = [candidate for candidate in component[anchor_x]
                if _guide_distance(candidate, anchor_y) <= nearest + 0.5]
            for direction in (-1, 1):
                side = {direction * x: list(component[x]) for x in sorted(component, reverse=direction < 0)
                    if direction * (x - anchor_x) >= 0}
                side[direction * anchor_x] = anchor_candidates
                side_seeds = seeds if direction > 0 else tuple((-x, y) for x, y in reversed(seeds))
                # Round identity columns in the original image coordinates:
                # negating a half-pixel guide first would move its anchor by one.
                side_anchors = tuple((direction * x, y) for x, y in seed_anchors)
                path_support = {}
                _, competing, _ = _grow_columns(side, side_seeds, fallback, guide_limit, path_support,
                    seed_anchors=side_anchors)
                for x, alternatives in competing.items():
                    actual_x = direction * x
                    for alternative in alternatives:
                        key = (actual_x, id(alternative))
                        identity_support[key] = max(identity_support.get(key, 0),
                            path_support[x, id(alternative)])
                    candidates_by_x.setdefault(actual_x, []).extend(alternatives)
    selected, competing, segment_ids = {}, {}, {}
    previous = None
    for x in sorted(candidates_by_x):
        alternatives = sorted({id(candidate): candidate for candidate in candidates_by_x[x]}.values(),
            key=lambda candidate: candidate["subpixel_y"])
        # A trajectory connecting actual identity anchors outranks a disconnected
        # guide-nearest stroke inside that span. Outside it, directly anchored
        # trajectories still outrank guide-only reacquisition. Never use distance
        # to a straight interpolated guide to erase the curve's real curvature.
        # Equal identity support remains ambiguous, including competing branches.
        best_support = max(identity_support[x, id(candidate)] for candidate in alternatives)
        alternatives = [candidate for candidate in alternatives
            if identity_support[x, id(candidate)] == best_support]
        chosen = min(alternatives, key=lambda candidate: (
            abs(candidate["subpixel_y"] - guide_y(seeds, x, fallback)), candidate["subpixel_y"]))
        selected[x], competing[x] = chosen, alternatives
        if previous is None or x != previous + 1 or not _connected(selected[previous], chosen):
            segment_id = x
        segment_ids[x] = segment_id
        previous = x
    return selected, competing, segment_ids


def _grow_columns(columns, seeds, fallback, guide_limit, identity_support=None, *, seed_anchors=None):
    """Select connected identity-supported paths, including natural endings.

    Forward/backward costs constrain choices within each retained segment.
    An isolated guide-nearest pixel cannot win via an unrelated neighbor.
    A guide-selected prefix may end even if a worse branch continues. Empty
    columns and disconnected strokes start new segments, never bridges.
    """
    selected, competing, segment_ids = {}, {}, {}
    segment = []
    forward, parents = [], []
    reacquiring = False
    seed_columns = {}
    if seed_anchors is None:
        seed_anchors = tuple((int(math.floor(x + 0.5)), y) for x, y in seeds)
    for seed_x, seed_y in seed_anchors:
        seed_columns.setdefault(seed_x, []).append(seed_y)

    def finish():
        if not segment:
            return
        last = min(range(len(forward[-1])), key=lambda j: forward[-1][j])
        best_cost = forward[-1][last]
        chosen = [last]
        for i in range(len(segment) - 1, 0, -1):
            last = parents[i][last]
            chosen.append(last)
        chosen.reverse()
        # Count actual differing columns in minimum-cost alternatives. Common
        # prefixes/tails cancel in the cost difference and must cancel in its
        # tolerance too. On equal costs prefer the completion closest to the
        # selected path, so an unrelated tied branch cannot inflate tolerance.
        prefix_differences = [[math.inf] * len(columns[x]) for x in segment]
        prefix_anchors = [[math.inf] * len(columns[x]) for x in segment]
        actual_anchors = [[any(_guide_distance(candidate, y) <= guide_limit
            for y in seed_columns.get(x, ())) for candidate in columns[x]] for x in segment]
        for i, x in enumerate(segment):
            for j, candidate in enumerate(columns[x]):
                if not math.isfinite(forward[i][j]):
                    continue
                differences = 0
                anchor = x if actual_anchors[i][j] else math.inf
                if i:
                    unary = abs(candidate["subpixel_y"] - guide_y(seeds, x, fallback))
                    predecessors = [k
                        for k, other in enumerate(columns[segment[i - 1]])
                        if _connected(other, candidate) and math.isclose(
                            forward[i - 1][k] + unary, forward[i][j], rel_tol=1e-12, abs_tol=1e-9)]
                    differences = min((prefix_differences[i - 1][k] for k in predecessors),
                        default=math.inf)
                    anchor = min(anchor, min((prefix_anchors[i - 1][k] for k in predecessors),
                        default=math.inf))
                prefix_differences[i][j] = differences + int(j != chosen[i])
                prefix_anchors[i][j] = anchor
        backward = [[math.inf] * len(columns[x]) for x in segment]
        suffix_differences = [[math.inf] * len(columns[x]) for x in segment]
        suffix_anchors = [[-math.inf] * len(columns[x]) for x in segment]
        backward[-1] = [0.0] * len(columns[segment[-1]])
        suffix_differences[-1] = [0] * len(columns[segment[-1]])
        suffix_anchors[-1] = [segment[-1] if anchored else -math.inf for anchored in actual_anchors[-1]]
        for i in range(len(segment) - 2, -1, -1):
            x, next_x = segment[i], segment[i + 1]
            for j, candidate in enumerate(columns[x]):
                values = [(abs(other["subpixel_y"] - guide_y(seeds, next_x, fallback))
                    + backward[i + 1][k], suffix_differences[i + 1][k] + int(k != chosen[i + 1]), k)
                    for k, other in enumerate(columns[next_x]) if _connected(candidate, other)]
                backward[i][j], suffix_differences[i][j] = min(
                    ((cost, differences) for cost, differences, _ in values), default=(math.inf, math.inf))
                anchor = max((suffix_anchors[i + 1][k] for cost, _, k in values
                    if math.isfinite(cost) and math.isclose(cost, backward[i][j], rel_tol=1e-12, abs_tol=1e-9)),
                    default=-math.inf)
                suffix_anchors[i][j] = max(anchor, x if actual_anchors[i][j] else -math.inf)
        segment_id = segment[0]
        for i, x in enumerate(segment):
            selected[x] = columns[x][chosen[i]]
            competing[x] = [candidate for j, candidate in enumerate(columns[x])
                if math.isfinite(forward[i][j] + backward[i][j]) and
                forward[i][j] + backward[i][j] - best_cost <= AMBIGUITY_SCORE_MARGIN_PX * max(
                    1, prefix_differences[i][j] + suffix_differences[i][j])]
            if identity_support is not None:
                # Each candidate's optimal prefix and suffix meet at this pixel,
                # so their anchors belong to one realizable path through it.
                # Reacquired segments start fresh. Selected-path anchors and
                # anchors on a mutually exclusive branch cannot be borrowed.
                for j, candidate in enumerate(columns[x]):
                    left, right = prefix_anchors[i][j], suffix_anchors[i][j]
                    identity_support[x, id(candidate)] = (
                        3 if left <= x <= right and left < right
                        else 2 if actual_anchors[i][j]
                        else 1 if math.isfinite(left) or math.isfinite(right) else 0)
            segment_ids[x] = segment_id

    for x, candidates in columns.items():
        if segment and x != segment[-1] + 1:
            finish()
            segment, forward, parents = [], [], []
            reacquiring = True
        if not candidates:
            finish()
            segment, forward, parents = [], [], []
            continue
        if segment:
            best_prefix = min(forward[-1])
            identity_paths = [other for value, other in zip(forward[-1], columns[segment[-1]])
                if value <= best_prefix + AMBIGUITY_SCORE_MARGIN_PX]
            # Ambiguity over the whole segment does not authorize a worse
            # branch to rewrite the best prefix when that prefix naturally ends.
            if not any(_connected(previous, current)
                    for previous in identity_paths for current in candidates):
                finish()
                segment, forward, parents = [], [], []
                reacquiring = True
        if not segment and reacquiring:
            candidates = [candidate for candidate in candidates
                if _guide_distance(candidate, guide_y(seeds, x, fallback))
                <= guide_limit]
            columns[x] = candidates
            if not candidates:
                continue
            reacquiring = False
        unary = [abs(c["subpixel_y"] - guide_y(seeds, x, fallback)) for c in candidates]
        if segment:
            values, links = [], []
            for cost, candidate in zip(unary, candidates):
                reachable = [(value, j) for j, (value, other) in enumerate(
                    zip(forward[-1], columns[segment[-1]])) if _connected(other, candidate)]
                previous_cost, previous_index = min(reachable, default=(math.inf, -1))
                values.append(cost + previous_cost)
                links.append(previous_index)
            if not any(math.isfinite(value) for value in values):
                finish()
                segment, forward, parents = [], [], []
        if not segment:
            values, links = unary, [-1] * len(candidates)
        segment.append(x)
        forward.append(values)
        parents.append(links)
    finish()
    return selected, competing, segment_ids


def _image_slope(x, centers, segment_ids):
    """Estimate direction from the selected image trajectory, preserving turns."""
    left = [centers[x] - centers[x - d] for d in range(1, DIRECTION_RADIUS_PX + 1)
        if x - d in centers and segment_ids[x - d] == segment_ids[x]]
    right = [centers[x + d] - centers[x] for d in range(1, DIRECTION_RADIUS_PX + 1)
        if x + d in centers and segment_ids[x + d] == segment_ids[x]]
    # Immediate one-sided tangents keep a sharp extremum from acquiring a
    # fictitious zero slope through cancellation across its two sides.
    if left and right and left[0] * right[0] <= 0:
        return max((left[0], right[0]), key=abs)
    slopes = [(centers[x] - centers[x - d]) / d
        for d in range(1, DIRECTION_RADIUS_PX + 1)
        if x - d in centers and segment_ids[x - d] == segment_ids[x]]
    slopes += [(centers[x + d] - centers[x]) / d
        for d in range(1, DIRECTION_RADIUS_PX + 1)
        if x + d in centers and segment_ids[x + d] == segment_ids[x]]
    return statistics.median(slopes) if slopes else 0.0


def _normal_section(image, x, y, slope, rgb, tolerance, bounds, exclusions):
    """Measure source-colored width and center along the local image normal."""
    left, top, right, bottom = bounds
    scale = math.hypot(1.0, slope)
    nx, ny = -slope / scale, 1.0 / scale
    pixels = image.load()

    def weight(t):
        px, py = int(math.floor(x + nx * t + 0.5)), int(math.floor(y + ny * t + 0.5))
        if not (left <= px < right and top <= py < bottom) or _excluded(px, py, exclusions):
            return 0.0
        pixel = pixels[px, py]
        if not _matches(pixel, rgb, tolerance):
            return 0.0
        distance = math.sqrt(sum((int(pixel[i]) - rgb[i]) ** 2 for i in range(3)))
        return max(1.0, tolerance + 1.0 - distance)

    samples = [(0.0, weight(0.0))]
    ends = []
    for sign in (-1, 1):
        step = 1
        while step * NORMAL_SAMPLE_STEP_PX <= max(right - left, bottom - top):
            t = sign * step * NORMAL_SAMPLE_STEP_PX
            w = weight(t)
            if not w:
                break
            samples.append((t, w))
            step += 1
        ends.append(sign * (step - 0.5) * NORMAL_SAMPLE_STEP_PX)
    total = sum(w for _, w in samples)
    offset = sum(t * w for t, w in samples) / total if total else 0.0
    return offset, max(NORMAL_SAMPLE_STEP_PX, ends[1] - ends[0])


def _select_path(
    image: Image.Image,
    *,
    rgb: tuple[int, int, int],
    tolerance: float,
    seeds: tuple[tuple[float, float], ...],
    bounds: tuple[int, int, int, int],
    exclusions: tuple[tuple[int, int, int, int], ...],
) -> tuple[tuple[TracePoint, ...], int]:
    left, top, right, bottom = bounds
    columns = {x: _column_candidates(image, x=x, top=top, bottom=bottom,
        rgb=rgb, tolerance=tolerance, exclusions=exclusions) for x in range(left, right)}
    selected, competing, segment_ids = _continuous_paths(columns, seeds, (top + bottom - 1) / 2)
    centers = {x: candidate["subpixel_y"] for x, candidate in selected.items()}
    # A turning normal can intersect both arms of a sharp tip. Its whole
    # section is then not one line width. Estimate the cap from its own section
    # and the narrower flank sections, retaining the image extremum.
    tip_widths = {}
    initial_centers = dict(centers)
    for x, candidate in selected.items():
        if x - 1 not in selected or x + 1 not in selected:
            continue
        if not segment_ids[x - 1] == segment_ids[x] == segment_ids[x + 1]:
            continue
        before = initial_centers[x] - initial_centers[x - 1]
        after = initial_centers[x + 1] - initial_centers[x]
        if before * after >= 0 or abs(before) + abs(after) <= 1:
            continue
        widths = []
        for neighbor in (x - DIRECTION_RADIUS_PX, x, x + DIRECTION_RADIUS_PX):
            if neighbor in selected and segment_ids[neighbor] == segment_ids[x]:
                _, width = _normal_section(image, neighbor, initial_centers[neighbor],
                    _image_slope(neighbor, initial_centers, segment_ids), rgb,
                    tolerance, bounds, exclusions)
                widths.append(width)
        normal_width = min(widths)
        tip_widths[x] = normal_width
        cap_offset = max(0.0, (normal_width - 1.0) / 2.0)
        centers[x] = (min(candidate["support_max"], candidate["support_min"] + cap_offset)
            if before < 0 else max(candidate["support_min"], candidate["support_max"] - cap_offset))
    for _ in range(CENTER_REFINEMENT_PASSES):
        refined = {}
        for x, candidate in selected.items():
            if any(abs(x - tip) <= DIRECTION_RADIUS_PX and
                    segment_ids[x] == segment_ids[tip] for tip in tip_widths):
                # A normal near a turn may traverse both arms. Keep its image
                # column centroid and cap rather than average across the turn.
                refined[x] = centers[x]
                continue
            slope = _image_slope(x, centers, segment_ids)
            offset, _width = _normal_section(image, x, centers[x], slope,
                rgb, tolerance, bounds, exclusions)
            # Transfer the normal-section center back to this source column.
            refined[x] = min(candidate["support_max"], max(candidate["support_min"],
                centers[x] + math.hypot(1.0, slope) * offset))
        centers = refined
    points = []
    for x, candidate in selected.items():
        slope = _image_slope(x, centers, segment_ids)
        _, normal_width = _normal_section(image, x, centers[x], slope,
            rgb, tolerance, bounds, exclusions)
        normal_width = tip_widths.get(x, normal_width)
        # Conservative coordinate bounds, not a statistical error bar. Raster
        # pixel resolution is separate from normal line width; the extracted
        # slope projects transverse width and horizontal quantization into y.
        x_uncertainty = 0.5
        normal_half_width = max(0.0, (normal_width - 1.0) / 2.0)
        y_uncertainty = normal_half_width * math.hypot(1.0, slope) + 0.5 * (1.0 + abs(slope))
        alternatives = competing[x]
        # A merged band can contain two coherent raster branches even though
        # the column has only one cluster. Its center cannot establish a unique
        # owner location. This is a topology check, not a line-width threshold.
        merged_paths = any(sum(_connected(candidate, other) and
            any(_connected(other, further) for further in columns.get(x + 2 * direction, ()))
            for other in columns.get(x + direction, ())) > 1 for direction in (-1, 1))
        ambiguous = len(alternatives) > 1 or merged_paths
        # A disconnected edge restricts the edge, not either legal endpoint.
        # CSV consumers use the same source support ranges to split runs.
        reason = "ambiguous_path" if ambiguous else ""
        points.append(TracePoint(
            point_index=x - left, pixel_x_raw=x,
            pixel_y_raw=int(math.floor(centers[x] + 0.5)),
            pixel_x_subpixel=float(x), pixel_y_subpixel=centers[x],
            uncertainty_px=max(x_uncertainty, y_uncertainty),
            identity_ambiguous=ambiguous, interpolation_eligible=not reason,
            pixel_y_support_min=min(c["support_min"] for c in alternatives),
            pixel_y_support_max=max(c["support_max"] for c in alternatives),
            interpolation_reason=reason,
            pixel_y_support_ranges=tuple((c["support_min"], c["support_max"]) for c in alternatives),
            cluster_y_min=candidate["support_min"], cluster_y_max=candidate["support_max"],
            local_slope=slope, normal_line_width_px=normal_width,
            x_localization_uncertainty_px=x_uncertainty,
            y_localization_uncertainty_px=y_uncertainty,
        ))
    return tuple(points), sum(point.identity_ambiguous for point in points)


def trace_continuous_line(
    image: Image.Image,
    *,
    rgb: tuple[int, int, int],
    tolerance: float,
    seeds: tuple[tuple[float, float], ...],
    bounds: tuple[int, int, int, int],
    exclusions: tuple[tuple[int, int, int, int], ...],
) -> LineTrace:
    """Select observed pixels using guide identity and neighboring stroke support."""

    left, top, right, bottom = bounds
    findings: list[LineFinding] = []
    points, ambiguous_columns = _select_path(
        image,
        rgb=rgb,
        tolerance=tolerance,
        seeds=seeds,
        bounds=bounds,
        exclusions=exclusions,
    )
    width = max(1, right - left)
    observed_x = [point.pixel_x_raw for point in points]
    gaps: list[tuple[int, int]] = []
    if observed_x:
        if observed_x[0] > left:
            gaps.append((left, observed_x[0]))
        gaps.extend(
            (previous + 1, current)
            for previous, current in zip(observed_x, observed_x[1:])
            if current - previous > 1
        )
        if observed_x[-1] < right - 1:
            gaps.append((observed_x[-1] + 1, right))
    else:
        gaps.append((left, right))
    visible_fraction = len(set(observed_x)) / width
    ambiguous_fraction = ambiguous_columns / width
    if ambiguous_columns:
        findings.append(
            LineFinding(
                "ambiguous_path",
                f"{ambiguous_columns} columns retain competing guide/continuity-supported pixel clusters; refine series seeds or exclusions",
            )
        )
    disconnected_edges = sum(right.pixel_x_raw == left.pixel_x_raw + 1 and
        not _support_ranges_connected(left.pixel_y_support_ranges, right.pixel_y_support_ranges)
        for left, right in zip(points, points[1:]))
    if disconnected_edges:
        findings.append(LineFinding("disconnected_pixel_support",
            f"{disconnected_edges} transitions lack connected source-colored raster support; interpolation cannot bridge them"))
    max_gap = max((right - left,), default=width)
    if observed_x:
        max_gap = max(
            observed_x[0] - left,
            right - 1 - observed_x[-1],
            *(current - previous - 1 for previous, current in zip(observed_x, observed_x[1:])),
        )
    return LineTrace(
        points=points,
        visible_fraction=visible_fraction,
        max_gap_px=max_gap,
        ambiguous_fraction=ambiguous_fraction,
        findings=tuple(findings),
    )


def mark_shared(
    point: TracePoint,
    *,
    group: str,
    source_series: str,
    support_kind: str,
    uncertainty_px: float | None = None,
    shared_eligible: bool = False,
) -> TracePoint:
    return replace(
        point,
        support_kind=support_kind,
        coordinate_ownership="shared",
        shared_group=group,
        support_source_series=source_series,
        uncertainty_px=(
            point.uncertainty_px if uncertainty_px is None else uncertainty_px
        ),
        shared_eligible=shared_eligible,
    )


__all__ = [
    "LineFinding",
    "LineTrace",
    "TracePoint",
    "guide_y",
    "mark_shared",
    "trace_continuous_line",
]
