from __future__ import annotations

import csv

import hashlib

import io

import json

import re

import time

from typing import Annotated, Any, Literal, Mapping, Callable

from .common import canonical_json, Identifier

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator

from .schema import CurveAxis, CurveBundle, CurveComparison, CurveComparisonSpec, CurveOperatorSpec, CurveSeries, evaluate_curve_consistency

MAX_POINTS = 65536

MAX_SOURCE_BYTES = 16 * 1024 * 1024

class ScoreLimitError(ValueError):
    """A declared calculation or source budget was exhausted."""

class UnsupportedSourceError(NotImplementedError):
    """A source encoding is outside the parser's declared support."""

def check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("calculation_time_budget")

def admit_raw_lines(raw: bytes, *, max_lines: int = MAX_POINTS + 256) -> None:
    # Count before decode/split or domain parsers allocate per-row Python objects.
    if any(separator in raw for separator in
           (b"\x0b", b"\x0c", b"\x1c", b"\x1d", b"\x1e", b"\xc2\x85", b"\xe2\x80\xa8", b"\xe2\x80\xa9")):
        raise UnsupportedSourceError("source_line_separator_unsupported")
    if raw.count(b"\n") + raw.count(b"\r") - raw.count(b"\r\n") > max_lines:
        raise ScoreLimitError("source_line_limit")

class AnalysisSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_alias: Annotated[str, Field(min_length=1)]

class CSVSource(AnalysisSource):
    format: Literal["csv"]
    series_key: Identifier
    case_key: Identifier
    role: Identifier
    x_axis: CurveAxis
    y_axis: CurveAxis
    x_column: str
    y_column: str

def csv_series(mapping: CSVSource, raw: bytes, *, deadline: float | None = None) -> CurveSeries:
    """Read only explicit columns. Original bytes remain the evidence source."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise ScoreLimitError("source_byte_limit")
    check_deadline(deadline)
    admit_raw_lines(raw)
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    x, y = mapping.x_column, mapping.y_column
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("csv_header_ambiguous")
    if x not in reader.fieldnames or y not in reader.fieldnames:
        raise ValueError("csv_column_missing")
    points = []
    for row in reader:
        check_deadline(deadline)
        if len(points) >= MAX_POINTS:
            raise ScoreLimitError("source_point_limit")
        try:
            points.append({"x": float(row[x]), "y": float(row[y])})
        except (ValueError, TypeError) as error:
            raise ValueError("csv_non_numeric_column") from error
    return CurveSeries.model_validate_json(canonical_json({
        **{key: getattr(mapping, key) for key in ("series_key", "case_key", "role", "x_axis", "y_axis")},
        "points": points, "availability": {"status": "available", "rationale": "Explicit CSV columns."},
        "source_locator": mapping.input_alias,
    }), strict=True)
