"""Strict parser for one native ASCII SProcess PLX profile."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Annotated, Literal

from .common import Identifier, SchemaModel, Sha256
from pydantic import Field

from .schema import (
    CurveAvailability,
    CurveBundle,
    CurveInterval,
    CurvePoint,
    CurveSeries,
)

from .curve_normalizer import MAX_LOG_BYTES, SProcessPointLimitError, SProcessSeriesSpec


PLX_NORMALIZER_PROFILE = "scidiscovery.curve-normalize.sprocess-plx.v1"
_HEADER = re.compile(r'^\s*"([^"\r\n]+)"\s*$')


class SProcessPLXSourceSpec(SchemaModel):
    source_profile: Literal["sprocess.ascii-plx.v1"] = "sprocess.ascii-plx.v1"
    series: SProcessSeriesSpec
    dataset_name: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    required_intervals: Annotated[
        tuple[CurveInterval, ...], Field(max_length=4096)
    ] = ()
    max_input_bytes: Annotated[int, Field(ge=1, le=MAX_LOG_BYTES)] = MAX_LOG_BYTES


class SProcessPLXNormalizationAudit(SchemaModel):
    record_grammar: Literal["sprocess_ascii_plx_v1"] = "sprocess_ascii_plx_v1"
    input_sha256: Sha256
    input_bytes: Annotated[int, Field(ge=0, le=MAX_LOG_BYTES)]
    total_lines: Annotated[int, Field(ge=0, le=10_000_000)]
    field_name: Annotated[str, Field(min_length=1, max_length=256)]
    dataset_count: Annotated[int, Field(ge=1, le=1_000_000)]
    selected_dataset_index: Annotated[int, Field(ge=0, le=999_999)]
    ignored_dataset_count: Annotated[int, Field(ge=0, le=999_999)]
    input_point_count: Annotated[int, Field(ge=2, le=1_000_000)]
    point_count: Annotated[int, Field(ge=2, le=1_000_000)]
    duplicate_x_count: Annotated[int, Field(ge=0, le=1_000_000)]
    x_decrease_count: Annotated[int, Field(ge=0, le=1_000_000)]
    ignored_duplicate_x_count: Annotated[int, Field(ge=0, le=1_000_000)]
    first_data_line: Annotated[int, Field(ge=2)]
    last_data_line: Annotated[int, Field(ge=2)]


def normalize_sprocess_plx(
    raw: bytes,
    spec: SProcessPLXSourceSpec,
    *,
    source_name: str = "solver_output",
) -> tuple[CurveBundle, SProcessPLXNormalizationAudit]:
    """Parse a one-field native SProcess ASCII PLX without inference or repair."""

    if len(raw) > spec.max_input_bytes:
        raise ValueError("SProcess PLX exceeds source-spec byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("SProcess PLX is not valid UTF-8") from error

    lines = text.splitlines()
    nonblank = tuple(
        (line_number, line)
        for line_number, line in enumerate(lines, start=1)
        if line.strip()
    )
    if not nonblank:
        raise ValueError("SProcess PLX is empty")
    first_line, _ = nonblank[0]
    if first_line != 1:
        raise ValueError("SProcess PLX header must be the first line")

    datasets: list[tuple[str, list[tuple[int, float, float]]]] = []
    current_name: str | None = None
    current_records: list[tuple[int, float, float]] = []
    for line_number, line in nonblank:
        header = _HEADER.fullmatch(line)
        if header is not None:
            if current_name is not None:
                datasets.append((current_name, current_records))
            current_name = header.group(1).strip()
            if not current_name or len(current_name) > 256:
                raise ValueError("SProcess PLX field header is invalid")
            current_records = []
            continue
        if current_name is None:
            raise ValueError("SProcess PLX requires a quoted field header")
        fields = line.split()
        if len(fields) != 2:
            raise ValueError(
                f"SProcess PLX data row at line {line_number} must contain "
                "exactly x and y"
            )
        try:
            x, y = (float(value) for value in fields)
        except ValueError as error:
            raise ValueError(
                f"SProcess PLX data row at line {line_number} is not numeric"
            ) from error
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("SProcess PLX data values must be finite")
        current_records.append((line_number, x, y))
    if current_name is not None:
        datasets.append((current_name, current_records))
    if not datasets:
        raise ValueError("SProcess PLX requires a quoted field header")
    dataset_names = tuple(name for name, _ in datasets)
    if len(dataset_names) != len(set(dataset_names)):
        raise ValueError("SProcess PLX dataset names must be unique")
    if spec.dataset_name is None and len(dataset_names) > 1:
        raise ValueError(
            "multi-dataset SProcess PLX requires an explicit dataset_name"
        )
    if spec.dataset_name is None:
        selected_dataset_index = 0
    else:
        try:
            selected_dataset_index = dataset_names.index(spec.dataset_name)
        except ValueError as error:
            raise ValueError("SProcess PLX required dataset is missing") from error
    field_name, records = datasets[selected_dataset_index]

    duplicate_x_count = 0
    x_decrease_count = 0
    seen_x: set[float] = set()
    previous_x: float | None = None
    for _, x, _ in records:
        if x in seen_x:
            duplicate_x_count += 1
        if previous_x is not None and x < previous_x:
            x_decrease_count += 1
        seen_x.add(x)
        previous_x = x

    if len(records) > spec.series.max_points:
        raise SProcessPointLimitError("SProcess PLX exceeds its point limit")
    if len(records) < spec.series.min_points:
        raise ValueError("SProcess PLX point count violates its source spec")
    _require_domain_coverage(records, spec.required_intervals)
    retained = tuple(records)
    retained_duplicate_count = len(retained) - len({item[1] for item in retained})
    points = tuple(CurvePoint(x=x, y=y) for _, x, y in retained)
    first_data_line = retained[0][0]
    last_data_line = retained[-1][0]

    digest = hashlib.sha256(raw).hexdigest()
    series = CurveSeries(
        series_key=spec.series.series_key,
        case_key=spec.series.case_key,
        role=spec.series.role,
        x_axis=spec.series.x_axis,
        y_axis=spec.series.y_axis,
        points=points,
        valid_intervals=(
            spec.required_intervals
            if spec.required_intervals
            else (CurveInterval(start=points[0].x, stop=points[-1].x),)
        ),
        availability=CurveAvailability(
            status="available",
            rationale="The exact registered native PLX contains one complete series.",
        ),
        source_locator=f"{source_name}:lines:{first_data_line}-{last_data_line}",
    )
    return (
        CurveBundle(
            source_profile=PLX_NORMALIZER_PROFILE,
            source_digests=(digest,),
            series=(series,),
        ),
        SProcessPLXNormalizationAudit(
            input_sha256=digest,
            input_bytes=len(raw),
            total_lines=len(lines),
            field_name=field_name,
            dataset_count=len(datasets),
            selected_dataset_index=selected_dataset_index,
            ignored_dataset_count=len(datasets) - 1,
            input_point_count=len(records),
            point_count=len(points),
            duplicate_x_count=duplicate_x_count,
            x_decrease_count=x_decrease_count,
            ignored_duplicate_x_count=duplicate_x_count - retained_duplicate_count,
            first_data_line=first_data_line,
            last_data_line=last_data_line,
        ),
    )


def _require_domain_coverage(
    records: list[tuple[int, float, float]],
    required_intervals: tuple[CurveInterval, ...],
) -> None:
    if not required_intervals:
        return
    lower = min(item.start for item in required_intervals)
    upper = max(item.stop for item in required_intervals)
    xs = tuple(item[1] for item in records)
    if min(xs) > lower or max(xs) < upper:
        raise ValueError("SProcess PLX does not cover the required domain")
