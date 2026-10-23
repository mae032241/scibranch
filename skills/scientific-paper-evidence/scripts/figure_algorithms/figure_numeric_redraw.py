"""Deterministically redraw digitized CSV values for visual self-checking.

The rendering math is the in-package form of the 8765
``render_curve_support.py`` implementation.  It reads only materialized CSV
values; source-image pixels are never used as a background.
"""

from __future__ import annotations

import csv
import io
import json
import math

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .figure_digitization_contract import FigureDigitizationRequest, axis_value
from .figure_line_tracker import _support_ranges_connected


def _space(value: float, scale: str) -> float:
    if scale == "log10":
        if value <= 0:
            raise ValueError("log10 redraw values must be positive")
        return math.log10(value)
    return value


def _runs(raw: bytes) -> tuple[tuple[dict[str, str], ...], ...]:
    rows = tuple(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    result: list[tuple[dict[str, str], ...]] = []
    current: list[dict[str, str]] = []
    previous: int | None = None
    for row in rows:
        index = int(row["point_index"])
        if current and previous is not None and (index != previous + 1 or row.get("interpolation_eligible", "1") != "1" or
                not _support_ranges_connected(json.loads(current[-1].get("pixel_y_support_ranges", "[]")),
                    json.loads(row.get("pixel_y_support_ranges", "[]")))):
            result.append(tuple(current))
            current = []
        if row.get("interpolation_eligible", "1") != "1":
            result.append((row,))
            previous = index
            continue
        current.append(row)
        previous = index
    if current:
        result.append(tuple(current))
    return tuple(result)


def _draw_line(draw, points, *, color, style):
    if style == "solid":
        draw.line(points, fill=color, width=3)
        return
    on, off = (14, 8) if style == "dashed" else (2, 6)
    distance = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, math.ceil(length))
        for step in range(steps):
            if (distance + length * step / steps) % (on + off) < on:
                a, b = step / steps, (step + 1) / steps
                draw.line((x0 + a * (x1 - x0), y0 + a * (y1 - y0),
                    x0 + b * (x1 - x0), y0 + b * (y1 - y0)), fill=color, width=3)
        distance += length


def _label(value: float, scale: str) -> str:
    return f"{value:.1e}" if scale == "log10" else f"{value:.3g}"


def render_numeric_redraw(
    request: FigureDigitizationRequest,
    tables: dict[str, bytes],
) -> bytes:
    """Render the calibrated values from per-series CSVs to one PNG."""

    plot, axes, series_values = request.require_ready()
    width, height = 1200, 760
    left, top, right, bottom = 120, 70, width - 45, height - 95
    x_left = axis_value(axes.x, plot[0])
    x_right = axis_value(axes.x, plot[2] - 1)
    y_top = axis_value(axes.y, plot[1])
    y_bottom = axis_value(axes.y, plot[3] - 1)
    x0, x1 = _space(x_left, axes.x.scale), _space(x_right, axes.x.scale)
    y0, y1 = _space(y_top, axes.y.scale), _space(y_bottom, axes.y.scale)
    if x0 == x1 or y0 == y1:
        raise ValueError("numeric redraw axes have no span")

    def x_pixel(value: float) -> float:
        return left + (_space(value, axes.x.scale) - x0) / (x1 - x0) * (right - left)

    def y_pixel(value: float) -> float:
        return top + (_space(value, axes.y.scale) - y0) / (y1 - y0) * (bottom - top)

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    draw.rectangle((left, top, right, bottom), outline="#202020", width=2)
    draw.text((left, 22), "Digitized values redrawn from CSV", fill="#202020", font=font)

    for index in range(6):
        fraction = index / 5
        px = left + fraction * (right - left)
        py = top + fraction * (bottom - top)
        x_value = 10 ** (x0 + fraction * (x1 - x0)) if axes.x.scale == "log10" else x0 + fraction * (x1 - x0)
        y_value = 10 ** (y0 + fraction * (y1 - y0)) if axes.y.scale == "log10" else y0 + fraction * (y1 - y0)
        draw.line((px, top, px, bottom), fill="#ededed")
        draw.line((left, py, right, py), fill="#ededed")
        draw.text((px - 18, bottom + 10), _label(x_value, axes.x.scale), fill="#303030", font=font)
        draw.text((8, py - 6), _label(y_value, axes.y.scale), fill="#303030", font=font)

    draw.text(((left + right) // 2 - 30, height - 34), axes.x.unit, fill="#202020", font=font)
    draw.text((8, 48), f"{axes.y.unit} ({axes.y.scale})", fill="#202020", font=font)

    legend_x = left
    for series in series_values:
        try:
            raw = tables[series.series_key]
        except KeyError as error:
            raise ValueError(f"numeric redraw lacks table for {series.series_key}") from error
        color = ImageColor.getrgb(series.color)
        for run in _runs(raw):
            points = [
                (x_pixel(float(row["x_value"])), y_pixel(float(row["y_value"])))
                for row in run
            ]
            if len(points) > 1:
                _draw_line(draw, points, color=color, style=series.line_style)
            for (px, py), row in zip(points, run, strict=True):
                if row.get("shared_group"):
                    draw.ellipse((px - 3, py - 3, px + 3, py + 3), outline="#d69e00")
                else:
                    draw.point((px, py), fill="#ff4000" if row.get("interpolation_eligible", "1") != "1" else color)
        _draw_line(draw, [(legend_x, 48), (legend_x + 26, 48)], color=color, style=series.line_style)
        draw.text((legend_x + 31, 42), series.label[:48], fill="#202020", font=font)
        legend_x += min(330, max(170, len(series.label) * 6 + 45))

    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()
