"""Normalize validated scientific-figure evidence into canonical curves."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from typing import Mapping

from .curves import (
    CurveAvailability,
    CurveAxis,
    CurveBundle,
    CurveInterval,
    CurvePoint,
    CurveSeries,
    CurveSharedSupport,
)
from .figure_evidence import (
    FigureAxisCalibration,
    FigureEvidenceManifest,
    FigureEvidencePanel,
    FigureEvidenceSeries,
    FigureEvidenceValidationReport,
    FigureEvidenceValidationSeries,
)
from .figure_line_tracker import _support_ranges_connected


FIGURE_EVIDENCE_BUNDLE_PROFILE = "scidiscovery.curve-bundle.figure-evidence.v1"
FIGURE_EVIDENCE_BUNDLE_PROFILE_V2 = "scidiscovery.curve-bundle.figure-evidence.v2"
_TABLE_PREFIX = "curve_table__"
_STANDARD_COLUMNS = frozenset(
    {
        "panel_key",
        "series_key",
        "point_index",
        "pixel_x_subpixel",
        "pixel_y_subpixel",
        "observed",
    }
)
_EXTENDED_COLUMNS = frozenset(
    {
        "panel_key",
        "series_key",
        "point_index",
        "pixel_x",
        "pixel_y",
        "observed",
    }
)


@dataclass(frozen=True)
class FigureEvidenceNormalizationAudit:
    series_key: str
    source_series_key: str
    panel_key: str
    data_item: str
    csv_sha256: str
    input_row_count: int
    observed_row_count: int
    eligible_row_count: int | None
    selected_point_count: int
    availability_status: str
    reason_code: str | None
    shared_pixel_provenance: tuple[dict[str, object], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "series_key": self.series_key,
            "source_series_key": self.source_series_key,
            "panel_key": self.panel_key,
            "data_item": self.data_item,
            "csv_sha256": self.csv_sha256,
            "input_row_count": self.input_row_count,
            "observed_row_count": self.observed_row_count,
            "eligible_row_count": self.eligible_row_count,
            "selected_point_count": self.selected_point_count,
            "availability_status": self.availability_status,
            "reason_code": self.reason_code,
            "shared_pixel_provenance": list(self.shared_pixel_provenance),
        }


def normalize_figure_evidence(
    *,
    manifest_content: bytes,
    validation_report_content: bytes,
    curve_tables: Mapping[str, bytes],
    profile: str = FIGURE_EVIDENCE_BUNDLE_PROFILE_V2,
) -> tuple[CurveBundle, tuple[FigureEvidenceNormalizationAudit, ...]]:
    """Build one complete fail-closed curve library from validated evidence."""

    if profile not in {
        FIGURE_EVIDENCE_BUNDLE_PROFILE,
        FIGURE_EVIDENCE_BUNDLE_PROFILE_V2,
    }:
        raise ValueError("unsupported figure evidence normalization profile")
    manifest = FigureEvidenceManifest.model_validate_json(
        manifest_content, strict=True
    )
    report = FigureEvidenceValidationReport.model_validate_json(
        validation_report_content, strict=True
    )
    if not report.series or not any(panel.series for panel in manifest.panels):
        raise ValueError("figure normalization requires measured curve tables; unresolved zero-table evidence is not quantitative")
    manifest_sha256 = _sha256(manifest_content)
    if report.manifest_sha256 != manifest_sha256:
        raise ValueError("validation report does not bind the exact figure manifest")
    if (
        report.figure_key != manifest.figure_key
        or report.source_status != manifest.status
    ):
        raise ValueError("validation report identity differs from figure manifest")

    manifest_series = _manifest_series(manifest)
    normalized_keys = {(panel.panel_key, evidence.series_key): key for panel, evidence, key in manifest_series}
    expected_inputs = {
        _table_alias(panel.panel_key, evidence.series_key)
        for panel, evidence, _ in manifest_series
    }
    if set(curve_tables) != expected_inputs:
        missing = sorted(expected_inputs - set(curve_tables))
        unexpected = sorted(set(curve_tables) - expected_inputs)
        raise ValueError(
            "figure evidence tables must cover the exact manifest series; "
            f"missing={missing}, unexpected={unexpected}"
        )
    report_series = {(item.panel_key, item.series_key): item for item in report.series}
    expected_identities = {
        (panel.panel_key, evidence.series_key)
        for panel, evidence, _ in manifest_series
    }
    if set(report_series) != expected_identities:
        raise ValueError("validation report series differ from figure manifest")

    normalized: list[CurveSeries] = []
    audits: list[FigureEvidenceNormalizationAudit] = []
    input_digests = {manifest_sha256, _sha256(validation_report_content)}
    ambiguous_series = {
        (item.panel_key, item.series_key) for item in manifest.ambiguities
    }
    for panel, evidence, series_key in manifest_series:
        validation = report_series[(panel.panel_key, evidence.series_key)]
        raw = curve_tables[_table_alias(panel.panel_key, evidence.series_key)]
        csv_sha256 = _sha256(raw)
        input_digests.add(csv_sha256)
        _validate_series_identity(
            panel=panel,
            evidence=evidence,
            validation=validation,
            csv_sha256=csv_sha256,
        )
        rows, has_eligibility = _curve_rows(
            raw,
            panel_key=panel.panel_key,
            series_key=evidence.series_key,
        )
        if len(rows) != validation.row_count:
            raise ValueError(
                f"curve table row count differs from validation report: {series_key}"
            )
        observed_count = sum(item.observed for item in rows)
        eligible_count = (
            sum(item.eligible for item in rows) if has_eligibility else None
        )
        if (
            observed_count != validation.observed_row_count
            or eligible_count != validation.eligible_row_count
        ):
            raise ValueError(
                f"curve table flags differ from validation report: {series_key}"
            )

        reason = _unavailability_reason(
            evidence=evidence,
            validation=validation,
            rows=rows,
            has_eligibility=has_eligibility,
            identity_ambiguous=(panel.panel_key, evidence.series_key)
            in ambiguous_series,
            global_unresolved=(
                profile == FIGURE_EVIDENCE_BUNDLE_PROFILE
                and manifest.status != "qualified"
            ),
        )
        quantitative_runs = _quantitative_runs(
            rows,
            panel.axis_calibration.x,
            has_eligibility=has_eligibility,
        )
        valid_intervals = _valid_intervals(quantitative_runs)
        if (
            profile == FIGURE_EVIDENCE_BUNDLE_PROFILE_V2
            and reason is None
            and not valid_intervals
        ):
            reason = "no_continuous_quantitative_interval"

        # Runs above retain source pixel adjacency and permissions. Only the
        # delivered observations are ordered in calibrated scientific space;
        # stable sorting preserves the existing duplicate-x policy and order.
        selected_points = tuple(sorted((
            CurvePoint(
                x=_axis_value(panel.axis_calibration.x, row.pixel_x),
                y=_axis_value(panel.axis_calibration.y, row.pixel_y),
            )
            for row in rows
            if row.observed and (not has_eligibility or row.eligible)
        ), key=lambda point: point.x))
        observed_points = tuple(sorted((
            CurvePoint(
                x=_axis_value(panel.axis_calibration.x, row.pixel_x),
                y=_axis_value(panel.axis_calibration.y, row.pixel_y),
            )
            for row in rows
            if row.observed
        ), key=lambda point: point.x))
        if profile == FIGURE_EVIDENCE_BUNDLE_PROFILE_V2:
            points = observed_points
            if points:
                _validate_points(points, series_key, allow_duplicate_x=True)
        else:
            points = selected_points if reason is None else ()
            if points:
                _validate_points(points, series_key, allow_duplicate_x=False)
        availability = (
            CurveAvailability(
                status="unavailable",
                reason_code=reason,
                rationale=(
                    "The validated figure evidence does not qualify this reference "
                    f"series for quantitative comparison ({reason})."
                ),
            )
            if reason is not None
            else CurveAvailability(
                status="available",
                rationale=(
                    "The exact validated figure evidence has a qualified identity "
                    "binding and sufficient observed quantitative rows."
                ),
            )
        )
        normalized.append(
            CurveSeries(
                series_key=series_key,
                case_key=panel.panel_key,
                role=series_key,
                x_axis=CurveAxis(
                    name="x",
                    unit=panel.axis_calibration.x.unit,
                    scale=panel.axis_calibration.x.scale,
                ),
                y_axis=CurveAxis(
                    name="y",
                    unit=panel.axis_calibration.y.unit,
                    scale=panel.axis_calibration.y.scale,
                ),
                points=points,
                valid_intervals=(
                    valid_intervals
                    if profile == FIGURE_EVIDENCE_BUNDLE_PROFILE_V2
                    and reason is None
                    else ()
                ),
                exclusions=(
                    _interval_gaps(valid_intervals)
                    if profile == FIGURE_EVIDENCE_BUNDLE_PROFILE_V2
                    and reason is None
                    else ()
                ),
                availability=availability,
                source_locator=(
                    f"figure:{manifest.figure_key}/panel:{panel.panel_key}/"
                    f"series:{series_key}/{evidence.data_item}"
                ),
                shared_support=tuple(CurveSharedSupport(group_key=support.group_key,
                    member_series=tuple(normalized_keys[(panel.panel_key, key)] for key in (
                        support.member_series if support.mode == "coincident_overlap"
                        else (support.visible_series, *support.covered_series))),
                    source_locator=f"figure:{manifest.figure_key}/panel:{panel.panel_key}/source:{manifest.source.image_sha256}",
                    source_pixel_ranges=support.pixel_ranges)
                    for support in panel.shared_support or ()
                    if evidence.series_key in (support.member_series if support.mode == "coincident_overlap"
                        else (support.visible_series, *support.covered_series))),
            )
        )
        audits.append(
            FigureEvidenceNormalizationAudit(
                series_key=series_key,
                source_series_key=evidence.series_key,
                panel_key=panel.panel_key,
                data_item=evidence.data_item,
                csv_sha256=csv_sha256,
                input_row_count=len(rows),
                observed_row_count=observed_count,
                eligible_row_count=eligible_count,
                selected_point_count=len(selected_points),
                availability_status=availability.status,
                reason_code=availability.reason_code,
                shared_pixel_provenance=tuple(
                    {"point_index": row.point_index, "pixel_x": row.pixel_x,
                     "pixel_y": row.pixel_y, "shared_group": row.shared_group,
                     "support_source_series": row.support_source_series}
                    for row in rows if row.shared_group
                ),
            )
        )
    return (
        CurveBundle(
            source_profile=profile,
            source_digests=tuple(sorted(input_digests)),
            series=tuple(normalized),
        ),
        tuple(audits),
    )


@dataclass(frozen=True)
class _EvidenceRow:
    point_index: int
    pixel_x: float
    pixel_y: float
    observed: int
    eligible: int
    shared_group: str = ""
    support_source_series: str = ""
    interpolation_eligible: int = 1
    pixel_y_support_ranges: tuple[tuple[float, float], ...] = ()


def _manifest_series(
    manifest: FigureEvidenceManifest,
) -> tuple[tuple[FigureEvidencePanel, FigureEvidenceSeries, str], ...]:
    counts: dict[str, int] = {}
    for panel in manifest.panels:
        for series in panel.series:
            counts[series.series_key] = counts.get(series.series_key, 0) + 1
    return tuple(
        (
            panel,
            series,
            (
                series.series_key
                if counts[series.series_key] == 1
                else f"{panel.panel_key}__{series.series_key}"
            ),
        )
        for panel in manifest.panels
        for series in panel.series
    )


def _table_alias(panel_key: str, series_key: str) -> str:
    return f"{_TABLE_PREFIX}{panel_key}__{series_key}"


def _validate_series_identity(
    *,
    panel: FigureEvidencePanel,
    evidence: FigureEvidenceSeries,
    validation: FigureEvidenceValidationSeries,
    csv_sha256: str,
) -> None:
    if (
        validation.panel_key != panel.panel_key
        or validation.series_key != evidence.series_key
        or validation.data_item != evidence.data_item
        or validation.csv_sha256 != csv_sha256
    ):
        raise ValueError(
            f"validation report does not bind the exact reference table: "
            f"{panel.panel_key}/{evidence.series_key}"
        )


def _curve_rows(
    raw: bytes, *, panel_key: str, series_key: str
) -> tuple[tuple[_EvidenceRow, ...], bool]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(f"figure curve table is not UTF-8: {series_key}") from error
    if "\x00" in text:
        raise ValueError(f"figure curve table contains NUL bytes: {series_key}")
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        fields = tuple(reader.fieldnames or ())
        if not fields or len(fields) != len(set(fields)):
            raise ValueError("CSV headers must be present and unique")
        field_set = set(fields)
        if _STANDARD_COLUMNS.issubset(field_set):
            x_name, y_name = "pixel_x_subpixel", "pixel_y_subpixel"
        elif _EXTENDED_COLUMNS.issubset(field_set):
            x_name, y_name = "pixel_x", "pixel_y"
        else:
            raise ValueError("CSV lacks canonical pixel and identity columns")
        has_eligibility = "quantitative_measurement_claim_eligible" in field_set
        rows: list[_EvidenceRow] = []
        previous_index: int | None = None
        for row_number, row in enumerate(reader, start=2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError("CSV row width differs from its header")
            if row["panel_key"] != panel_key or row["series_key"] != series_key:
                raise ValueError("CSV identity differs from figure manifest")
            point_index = _integer(row["point_index"], "point_index")
            if point_index < 0 or (
                previous_index is not None and point_index <= previous_index
            ):
                raise ValueError("CSV point_index must be strictly increasing")
            previous_index = point_index
            rows.append(
                _EvidenceRow(
                    point_index=point_index,
                    pixel_x=_finite(row[x_name], x_name),
                    pixel_y=_finite(row[y_name], y_name),
                    observed=_flag(row["observed"], "observed"),
                    eligible=(
                        _flag(
                            row["quantitative_measurement_claim_eligible"],
                            "quantitative_measurement_claim_eligible",
                        )
                        if has_eligibility
                        else 1
                    ),
                    shared_group=row.get("shared_group", ""),
                    support_source_series=row.get("support_source_series", ""),
                    interpolation_eligible=_flag(row.get("interpolation_eligible",
                        row.get("quantitative_measurement_claim_eligible", "1")), "interpolation_eligible"),
                    pixel_y_support_ranges=tuple((_finite(low, "pixel_y_support_ranges"),
                        _finite(high, "pixel_y_support_ranges"))
                        for low, high in json.loads(row.get("pixel_y_support_ranges", "[]"))),
                )
            )
    except (csv.Error, KeyError, ValueError) as error:
        raise ValueError(
            f"invalid figure curve table {series_key}: {error}"
        ) from error
    return tuple(rows), has_eligibility


def _unavailability_reason(
    *,
    evidence: FigureEvidenceSeries,
    validation: FigureEvidenceValidationSeries,
    rows: tuple[_EvidenceRow, ...],
    has_eligibility: bool,
    identity_ambiguous: bool,
    global_unresolved: bool,
) -> str | None:
    if global_unresolved:
        return "figure_identity_unresolved"
    if evidence.binding.status != "matched":
        return "figure_identity_unresolved"
    if identity_ambiguous:
        return "figure_series_ambiguous"
    if has_eligibility and validation.eligible_row_count == 0:
        return "no_quantitative_eligible_rows"
    selected = sum(
        item.observed and (not has_eligibility or item.eligible) for item in rows
    )
    if selected < 2:
        return "insufficient_quantitative_points"
    return None


def _quantitative_runs(
    rows: tuple[_EvidenceRow, ...],
    x_axis: FigureAxisCalibration,
    *,
    has_eligibility: bool,
) -> tuple[tuple[float, ...], ...]:
    runs: list[list[float]] = []
    current: list[float] = []
    previous: _EvidenceRow | None = None
    for row in rows:
        selected = row.observed and (not has_eligibility or row.eligible) and row.interpolation_eligible
        contiguous = previous is not None and row.point_index == previous.point_index + 1 and \
            _support_ranges_connected(previous.pixel_y_support_ranges, row.pixel_y_support_ranges)
        if selected:
            if current and not contiguous:
                runs.append(current)
                current = []
            current.append(_axis_value(x_axis, row.pixel_x))
        elif current:
            runs.append(current)
            current = []
        previous = row
    if current:
        runs.append(current)
    return tuple(tuple(run) for run in runs)


def _valid_intervals(
    runs: tuple[tuple[float, ...], ...],
) -> tuple[CurveInterval, ...]:
    intervals = [
        CurveInterval(start=min(run), stop=max(run))
        for run in runs
        if len(run) >= 2 and min(run) < max(run)
    ]
    return tuple(sorted(intervals, key=lambda item: (item.start, item.stop)))


def _interval_gaps(
    intervals: tuple[CurveInterval, ...],
) -> tuple[CurveInterval, ...]:
    return tuple(
        CurveInterval(start=left.stop, stop=right.start)
        for left, right in zip(intervals, intervals[1:])
        if left.stop < right.start
    )


def _validate_points(
    points: tuple[CurvePoint, ...],
    series_key: str,
    *,
    allow_duplicate_x: bool,
) -> None:
    if any(
        right.x < left.x if allow_duplicate_x else right.x <= left.x
        for left, right in zip(points, points[1:])
    ):
        raise ValueError(
            f"qualified figure series x values are not ordered: "
            f"{series_key}"
        )


def _axis_value(axis: FigureAxisCalibration, pixel: float) -> float:
    fraction = (pixel - axis.pixel_min) / (axis.pixel_max - axis.pixel_min)
    if axis.scale == "linear":
        value = axis.value_min + fraction * (axis.value_max - axis.value_min)
    else:
        low = math.log10(axis.value_min)
        high = math.log10(axis.value_max)
        value = 10 ** (low + fraction * (high - low))
    if not math.isfinite(value):
        raise ValueError("figure calibration produced a non-finite value")
    return value


def _flag(value: str, name: str) -> int:
    if value not in {"0", "1"}:
        raise ValueError(f"{name} must be 0 or 1")
    return int(value)


def _integer(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _finite(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
