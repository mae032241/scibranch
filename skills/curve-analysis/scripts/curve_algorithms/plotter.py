"""Deterministic reference/candidate overview plots for scored comparisons."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .schema import (
    CurveBundle,
    CurveComparison,
    CurveComparisonSpec,
    CurveConsistencyReport,
    CurvePoint,
    CurveSeries,
)


CURVE_COMPARISON_PLOT_PROFILE = "scidiscovery.curve-comparison-plot.v1"
_MAX_PANELS = 8
_MAX_DRAW_POINTS = 2048


@dataclass(frozen=True)
class CurveComparisonPlot:
    content: bytes
    plotted_comparison_keys: tuple[str, ...]
    omitted_comparison_keys: tuple[str, ...]


def render_curve_comparison_overview(
    bundle: CurveBundle,
    spec: CurveComparisonSpec,
    report: CurveConsistencyReport,
) -> CurveComparisonPlot:
    """Render exact declared pairs without resampling or physical interpretation."""

    comparisons = spec.comparisons[:_MAX_PANELS]
    omitted = spec.comparisons[_MAX_PANELS:]
    width = 1200
    header_height = 70
    panel_height = 270
    height = header_height + panel_height * len(comparisons) + 30
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.text(
        (30, 18),
        "Declared curve comparisons: reference (black) / candidate (red)",
        fill="black",
        font=font,
    )
    draw.text(
        (30, 38),
        f"renderer={CURVE_COMPARISON_PLOT_PROFILE}",
        fill="#555555",
        font=font,
    )
    by_series = {item.series_key: item for item in bundle.series}
    by_result = {item.comparison_key: item for item in report.comparisons}
    for index, comparison in enumerate(comparisons):
        top = header_height + index * panel_height
        box = (90, top + 40, 1160, top + 225)
        result = by_result[comparison.comparison_key]
        reference = by_series.get(comparison.reference_series)
        candidate = by_series.get(comparison.candidate_series)
        _draw_panel(
            draw,
            font,
            box,
            comparison,
            result.status,
            reference,
            candidate,
            tuple(
                support
                for metric in result.metrics
                for support in metric.crossing_support
            ),
        )
    if omitted:
        draw.text(
            (30, height - 20),
            f"omitted_comparisons={len(omitted)}",
            fill="#555555",
            font=font,
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", compress_level=9)
    return CurveComparisonPlot(
        content=buffer.getvalue(),
        plotted_comparison_keys=tuple(item.comparison_key for item in comparisons),
        omitted_comparison_keys=tuple(item.comparison_key for item in omitted),
    )


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    box: tuple[int, int, int, int],
    comparison: CurveComparison,
    status: str,
    reference: CurveSeries | None,
    candidate: CurveSeries | None,
    crossing_support: tuple[object, ...],
) -> None:
    left, top, right, bottom = box
    draw.rectangle(box, outline="#333333", width=1)
    draw.text(
        (left, top - 32),
        _ascii(
            f"{comparison.comparison_key} | {comparison.purpose} | status={status}"
        ),
        fill="black",
        font=font,
    )
    draw.text(
        (left, top - 17),
        _ascii(
            f"ref={comparison.reference_series}  cand={comparison.candidate_series}  "
            f"domain=[{comparison.domain.start:.8g},{comparison.domain.stop:.8g}] "
            f"{comparison.domain.unit}"
        ),
        fill="#555555",
        font=font,
    )
    available = tuple(item for item in (reference, candidate) if item is not None)
    points = tuple(
        point
        for series in available
        for point in series.points
        if comparison.domain.start <= point.x <= comparison.domain.stop
    )
    if not points:
        draw.text((left + 10, top + 10), "series unavailable", fill="#777777", font=font)
        return
    y_log = any(item.y_axis.scale == "log10" for item in available)
    y_values = tuple(
        point.y for point in points if not y_log or point.y > 0
    )
    if not y_values:
        y_values = (1.0, 10.0) if y_log else (0.0, 1.0)
    transformed = tuple(math.log10(value) if y_log else value for value in y_values)
    y_min, y_max = min(transformed), max(transformed)
    if y_min == y_max:
        y_min -= 0.5
        y_max += 0.5

    x_log = any(item.x_axis.scale == "log10" for item in available)
    if x_log and comparison.domain.start <= 0:
        draw.text(
            (left + 10, top + 30),
            "invalid log-x comparison domain",
            fill="#777777",
            font=font,
        )
        return
    x_start = (
        math.log10(comparison.domain.start) if x_log else comparison.domain.start
    )
    x_stop = math.log10(comparison.domain.stop) if x_log else comparison.domain.stop

    def x_pixel(value: float) -> int:
        transformed_value = math.log10(value) if x_log else value
        return round(
            left
            + (transformed_value - x_start) * (right - left) / (x_stop - x_start)
        )

    def y_pixel(value: float) -> int | None:
        if y_log and value <= 0:
            return None
        transformed_value = math.log10(value) if y_log else value
        return round(
            bottom
            - (transformed_value - y_min) * (bottom - top) / (y_max - y_min)
        )

    if reference is not None:
        _draw_series(
            draw, reference, comparison, x_pixel, y_pixel, "#1f1f1f", top, bottom
        )
    if candidate is not None:
        _draw_series(
            draw, candidate, comparison, x_pixel, y_pixel, "#d62728", top, bottom
        )
    for support in crossing_support:
        locations = getattr(support, "locations", ())
        side = getattr(support, "series_side", "reference")
        color = "#1f77b4" if side == "reference" else "#ff7f0e"
        for location in locations:
            x = x_pixel(location)
            draw.line((x, top, x, bottom), fill=color, width=1)
            draw.ellipse((x - 3, top + 3, x + 3, top + 9), fill=color)
    draw.text((left + 8, top + 8), "reference", fill="#1f1f1f", font=font)
    draw.text((left + 90, top + 8), "candidate", fill="#d62728", font=font)
    draw.text(
        (left, bottom + 5),
        _ascii(f"{available[0].x_axis.name} [{available[0].x_axis.unit}]"),
        fill="black",
        font=font,
    )
    draw.text(
        (5, top),
        _ascii(f"{available[0].y_axis.name} [{available[0].y_axis.unit}]"),
        fill="black",
        font=font,
    )


def _draw_series(
    draw: ImageDraw.ImageDraw,
    series: CurveSeries,
    comparison: CurveComparison,
    x_pixel: Callable[[float], int],
    y_pixel: Callable[[float], int | None],
    color: str,
    top: int,
    zero_baseline: int,
) -> None:
    raw_points = tuple(
        item
        for item in series.points
        if comparison.domain.start <= item.x <= comparison.domain.stop
    )
    points = _bounded_points(raw_points)
    previous: tuple[int, int] | None = None
    previous_point: CurvePoint | None = None
    previous_zero_x: int | None = None
    for point in points:
        x = x_pixel(point.x)
        y = y_pixel(point.y)
        if y is None:
            if previous_zero_x is not None and previous_point is not None:
                if _segment_supported(series, previous_point.x, point.x):
                    draw.line(
                        (previous_zero_x, zero_baseline, x, zero_baseline),
                        fill=color,
                        width=2,
                    )
            draw.ellipse((x - 2, zero_baseline - 4, x + 2, zero_baseline), fill=color)
            previous = None
            previous_zero_x = x
            previous_point = point
            continue
        if (
            previous is not None
            and previous_point is not None
            and _segment_supported(series, previous_point.x, point.x)
        ):
            draw.line((*previous, x, y), fill=color, width=2)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=color)
        previous = (x, y)
        previous_zero_x = None
        previous_point = point
    x_counts: dict[float, int] = {}
    for point in raw_points:
        x_counts[point.x] = x_counts.get(point.x, 0) + 1
    for x_value, count in x_counts.items():
        if count > 1:
            x = x_pixel(x_value)
            draw.line((x, top, x, zero_baseline), fill="#9467bd", width=2)


def _bounded_points(points: tuple[CurvePoint, ...]) -> tuple[CurvePoint, ...]:
    if len(points) <= _MAX_DRAW_POINTS:
        return points
    counts: dict[float, int] = {}
    for point in points:
        counts[point.x] = counts.get(point.x, 0) + 1
    priority = {
        index
        for index, point in enumerate(points)
        if point.y == 0 or counts[point.x] > 1
    }
    priority.update((0, len(points) - 1))
    remaining = max(0, _MAX_DRAW_POINTS - len(priority))
    if remaining:
        step = (len(points) - 1) / max(1, remaining - 1)
        priority.update(round(index * step) for index in range(remaining))
    indexes = set(sorted(priority)[:_MAX_DRAW_POINTS])
    return tuple(points[index] for index in sorted(indexes))


def _segment_supported(series: CurveSeries, left: float, right: float) -> bool:
    lower, upper = sorted((left, right))
    if series.valid_intervals and not any(
        interval.start <= lower and interval.stop >= upper
        for interval in series.valid_intervals
    ):
        return False
    return not any(
        interval.start < upper and interval.stop > lower
        for interval in series.exclusions
    )


def _ascii(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii")


__all__ = [
    "CURVE_COMPARISON_PLOT_PROFILE",
    "CurveComparisonPlot",
    "render_curve_comparison_overview",
]
