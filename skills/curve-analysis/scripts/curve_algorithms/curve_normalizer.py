"""Strict streaming-style parser for source-declared SProcess curve records."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Annotated, Literal

from .common import Identifier, SchemaModel, Sha256
from pydantic import Field, model_validator

from .schema import (
    CurveAvailability,
    CurveAxis,
    CurveBundle,
    CurveInterval,
    CurvePoint,
    CurveSeries,
    CurveComparisonSpec,
)


RECORD_PREFIX = "SCID_CURVE_V1"
NORMALIZER_PROFILE = "scidiscovery.curve-normalize.sprocess-log.v1"
MAX_LOG_BYTES = 64 * 1024 * 1024


class SProcessPointLimitError(ValueError):
    """A raw series exceeded its declared maximum point count."""


class SProcessSeriesSpec(SchemaModel):
    series_key: Identifier
    case_key: Identifier
    role: Identifier
    x_axis: CurveAxis
    y_axis: CurveAxis
    min_points: Annotated[int, Field(ge=2, le=1_000_000)] = 2
    max_points: Annotated[int, Field(ge=2, le=1_000_000)] = 1_000_000

    @model_validator(mode="after")
    def _point_bounds_are_ordered(self) -> SProcessSeriesSpec:
        if self.max_points < self.min_points:
            raise ValueError("series max_points must not be below min_points")
        return self


class SProcessLogSourceSpec(SchemaModel):
    source_profile: Identifier = "sprocess.curve-records.v1"
    expected_series: Annotated[
        tuple[SProcessSeriesSpec, ...], Field(min_length=1, max_length=10000)
    ]
    max_input_bytes: Annotated[
        int, Field(ge=1, le=MAX_LOG_BYTES)
    ] = MAX_LOG_BYTES

    @model_validator(mode="after")
    def _expected_series_are_unique(self) -> SProcessLogSourceSpec:
        keys = tuple(item.series_key for item in self.expected_series)
        identities = tuple((item.case_key, item.series_key) for item in self.expected_series)
        if len(keys) != len(set(keys)) or len(identities) != len(set(identities)):
            raise ValueError("SProcess source series identities must be unique")
        return self


class SeriesNormalizationCount(SchemaModel):
    case_key: Identifier
    series_key: Identifier
    point_count: Annotated[int, Field(ge=0, le=1_000_000)]
    first_line: Annotated[int, Field(ge=1)]
    last_line: Annotated[int, Field(ge=1)]


class SProcessNormalizationAudit(SchemaModel):
    record_grammar: Literal["scid_curve_v1"]
    input_sha256: Sha256
    input_bytes: Annotated[int, Field(ge=0, le=MAX_LOG_BYTES)]
    total_lines: Annotated[int, Field(ge=0, le=10_000_000)]
    matched_record_lines: Annotated[int, Field(ge=0, le=10_000_000)]
    series: Annotated[
        tuple[SeriesNormalizationCount, ...], Field(min_length=1, max_length=10000)
    ]


def normalize_sprocess_log(
    raw: bytes, spec: SProcessLogSourceSpec
) -> tuple[CurveBundle, SProcessNormalizationAudit]:
    """Parse exact SCID_CURVE_V1 records and ignore unrelated solver log lines."""

    if len(raw) > spec.max_input_bytes:
        raise ValueError("SProcess curve log exceeds source-spec byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("SProcess curve log is not valid UTF-8") from error

    expected = {
        (item.case_key, item.series_key): item for item in spec.expected_series
    }
    points: dict[tuple[str, str], list[CurvePoint]] = {
        key: [] for key in expected
    }
    first_lines: dict[tuple[str, str], int] = {}
    last_lines: dict[tuple[str, str], int] = {}
    ended: set[tuple[str, str]] = set()
    matched = 0
    lines = text.splitlines()
    for line_number, source_line in enumerate(lines, start=1):
        line = source_line.strip()
        if not line.startswith(RECORD_PREFIX + "|"):
            continue
        matched += 1
        fields = line.split("|")
        record_kind = fields[1] if len(fields) > 1 else ""
        if record_kind == "POINT":
            if len(fields) != 7:
                raise ValueError("SProcess curve point record has invalid field count")
            _, _, case_key, series_key, raw_index, raw_x, raw_y = fields
            key = (case_key, series_key)
            series_spec = expected.get(key)
            if series_spec is None:
                raise ValueError("SProcess curve log contains an undeclared series")
            if key in ended:
                raise ValueError("SProcess curve point appears after series completion")
            try:
                index = int(raw_index)
                x = float(raw_x)
                y = float(raw_y)
            except ValueError as error:
                raise ValueError("SProcess curve point is not numeric") from error
            if index != len(points[key]):
                raise ValueError("SProcess curve point indexes must be contiguous from zero")
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError("SProcess curve point must be finite")
            if len(points[key]) >= series_spec.max_points:
                raise SProcessPointLimitError("SProcess curve series exceeds its point limit")
            points[key].append(CurvePoint(x=x, y=y))
            first_lines.setdefault(key, line_number)
            last_lines[key] = line_number
            continue
        if record_kind == "END":
            if len(fields) != 5:
                raise ValueError("SProcess curve end record has invalid field count")
            _, _, case_key, series_key, raw_count = fields
            key = (case_key, series_key)
            series_spec = expected.get(key)
            if series_spec is None:
                raise ValueError("SProcess curve log completes an undeclared series")
            if key in ended:
                raise ValueError("SProcess curve series completion is duplicated")
            try:
                declared_count = int(raw_count)
            except ValueError as error:
                raise ValueError("SProcess curve completion count is invalid") from error
            if declared_count != len(points[key]):
                raise ValueError("SProcess curve completion count differs from records")
            if not series_spec.min_points <= declared_count <= series_spec.max_points:
                raise ValueError("SProcess curve point count violates its source spec")
            ended.add(key)
            first_lines.setdefault(key, line_number)
            last_lines[key] = line_number
            continue
        raise ValueError("SProcess curve record kind is unsupported")

    if ended != set(expected):
        missing = sorted(f"{case}/{series}" for case, series in set(expected) - ended)
        diagnostic = _unsupported_record_diagnostic(lines, expected)
        raise ValueError(
            "SProcess curve log is missing completed series: "
            + ", ".join(missing)
            + diagnostic
        )

    digest = hashlib.sha256(raw).hexdigest()
    series: list[CurveSeries] = []
    counts: list[SeriesNormalizationCount] = []
    for series_spec in spec.expected_series:
        key = (series_spec.case_key, series_spec.series_key)
        series_points = tuple(points[key])
        series.append(
            CurveSeries(
                series_key=series_spec.series_key,
                case_key=series_spec.case_key,
                role=series_spec.role,
                x_axis=series_spec.x_axis,
                y_axis=series_spec.y_axis,
                points=series_points,
                valid_intervals=(
                    CurveInterval(start=series_points[0].x, stop=series_points[-1].x),
                ),
                availability=CurveAvailability(
                    status="available",
                    rationale="The exact source-declared series completed in the solver log.",
                ),
                source_locator=(
                    f"solver_output:lines:{first_lines[key]}-{last_lines[key]}"
                ),
            )
        )
        counts.append(
            SeriesNormalizationCount(
                case_key=series_spec.case_key,
                series_key=series_spec.series_key,
                point_count=len(series_points),
                first_line=first_lines[key],
                last_line=last_lines[key],
            )
        )
    return (
        CurveBundle(
            source_profile=NORMALIZER_PROFILE,
            source_digests=(digest,),
            series=tuple(series),
        ),
        SProcessNormalizationAudit(
            record_grammar="scid_curve_v1",
            input_sha256=digest,
            input_bytes=len(raw),
            total_lines=len(lines),
            matched_record_lines=matched,
            series=tuple(counts),
        ),
    )


def _unsupported_record_diagnostic(
    lines: list[str], expected: dict[tuple[str, str], SProcessSeriesSpec]
) -> str:
    """Return bounded value-free evidence of an unsupported record layout."""

    labels = {
        "CASE",
        "CASE_END",
        "C",
        "CONCENTRATION",
        "END",
        "NODE",
        "POINT",
        "PROFILE",
        "SOLVE_COMPLETED",
        "X",
    }
    layouts: Counter[str] = Counter()
    marker_hits: Counter[str] = Counter()
    case_hits: Counter[str] = Counter()
    delimiter_hits: Counter[str] = Counter()
    candidate_lines = 0
    expected_cases = tuple(sorted({case_key for case_key, _ in expected}))
    for line in lines:
        upper_line = line.upper()
        for marker in labels:
            if re.search(
                rf"(?<![A-Z0-9_]){re.escape(marker)}(?![A-Z0-9_])",
                upper_line,
            ):
                marker_hits[marker] += 1
        for case_key in expected_cases:
            if case_key in line:
                case_hits[case_key] += 1
        for name, delimiter in (
            ("pipe", "|"),
            ("tab", "\t"),
            ("comma", ","),
            ("equals", "="),
        ):
            if delimiter in line:
                delimiter_hits[name] += 1
        if not (
            any(
                re.search(
                    rf"(?<![A-Z0-9_]){re.escape(marker)}(?![A-Z0-9_])",
                    upper_line,
                )
                for marker in labels
            )
            or any(case_key in line for case_key in expected_cases)
        ):
            continue
        candidate_lines += 1
        fields = tuple(
            token
            for token in re.findall(
                r"[A-Za-z_][A-Za-z0-9_.:-]*|"
                r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?|"
                r"[|,=]",
                line.strip(),
            )
        )
        normalized = []
        for field in fields[:24]:
            token = field.strip()
            upper = token.upper()
            if upper in labels:
                normalized.append(upper)
            elif token in expected_cases:
                normalized.append("<case>")
            elif token in {"|", ",", "="}:
                normalized.append(token)
            elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}", token):
                normalized.append("<text>")
            elif token:
                normalized.append("<value>")
            else:
                normalized.append("<empty>")
        suffix = "|..." if len(fields) > 24 else ""
        layouts[f"tokens={len(fields)}:" + " ".join(normalized) + suffix] += 1
    if not candidate_lines:
        return (
            "; no case/series candidate records were detected"
            + _diagnostic_counts(marker_hits, case_hits, delimiter_hits)
        )
    summaries = ", ".join(
        f"{layout} x{count}"
        for layout, count in sorted(
            layouts.items(), key=lambda item: (-item[1], item[0])
        )[:8]
    )
    return (
        f"; unsupported legacy candidate lines={candidate_lines}; "
        f"value-free layouts=[{summaries}]"
        + _diagnostic_counts(marker_hits, case_hits, delimiter_hits)
    )


def _diagnostic_counts(
    marker_hits: Counter[str],
    case_hits: Counter[str],
    delimiter_hits: Counter[str],
) -> str:
    markers = ",".join(
        f"{key}:{marker_hits[key]}" for key in sorted(marker_hits)
    ) or "none"
    cases = ",".join(
        f"{key}:{case_hits[key]}" for key in sorted(case_hits)
    ) or "none"
    delimiters = ",".join(
        f"{key}:{delimiter_hits[key]}" for key in sorted(delimiter_hits)
    ) or "none"
    return (
        f"; marker_hits=[{markers}]; declared_case_hits=[{cases}]; "
        f"delimiter_line_hits=[{delimiters}]"
    )


def source_spec_from_comparison_spec(
    comparison_spec: CurveComparisonSpec,
) -> SProcessLogSourceSpec:
    if not comparison_spec.series_declarations:
        raise ValueError(
            "composite curve score requires source series declarations"
        )
    return SProcessLogSourceSpec(
        expected_series=tuple(
            SProcessSeriesSpec(
                series_key=item.series_key,
                case_key=item.case_key,
                role=item.role,
                x_axis=item.x_axis,
                y_axis=item.y_axis,
                min_points=item.min_points,
                max_points=item.max_points,
            )
            for item in comparison_spec.series_declarations
            if item.source == "solver_output"
        )
    )
