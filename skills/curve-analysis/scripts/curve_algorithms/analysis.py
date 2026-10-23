"""Deterministic residual localization and bounded curve-error plots."""

from __future__ import annotations


import hashlib
import io
import math
from dataclasses import dataclass
from typing import Annotated, Callable, Literal

from pydantic import Field, model_validator
from .common import Identifier, SchemaModel, Sha256, canonical_json, canonical_sha256

from .schema import (
    metric_bundle_matches,
    CurveAvailability,
    CurveBundle,
    CurveComparison,
    CurveComparisonSpec,
    CurveConsistencyReport,
    CurveOperatorSpec,
    CurveSeries,
    _raw_value_at,
    evaluate_curve_consistency,
)


CURVE_ERROR_ANALYSIS_PROFILE = "scidiscovery.curve-error-analysis.v1"
CURVE_ERROR_PLOT_PROFILE = "scidiscovery.curve-error-plot.v1"
_RESIDUAL_KINDS = frozenset(
    {"residual_rms", "residual_max_abs", "mean_signed_difference"}
)
_MAX_ANALYSES = 8
_MAX_SEGMENTS = 16
_MAX_TRACE_POINTS = 512

CurveSupport = Literal[
    "both_positive",
    "both_floor",
    "reference_floor_only",
    "candidate_floor_only",
    "reference_zero_only",
    "candidate_zero_only",
    "both_zero",
    "reference_duplicate_x",
    "candidate_duplicate_x",
    "both_duplicate_x",
    "invalid",
]


class CurveResidualPoint(SchemaModel):
    x: Annotated[float, Field(allow_inf_nan=False)]
    residual: Annotated[float, Field(allow_inf_nan=False)] | None = None
    support: CurveSupport


class CurveErrorSegment(SchemaModel):
    x_start: Annotated[float, Field(allow_inf_nan=False)]
    x_stop: Annotated[float, Field(allow_inf_nan=False)]
    support: CurveSupport
    eligible_points: Annotated[int, Field(ge=0, le=1_000_000)]
    rms_error: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None
    mean_signed_error: Annotated[float, Field(allow_inf_nan=False)] | None = None
    error_fraction: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None

    @model_validator(mode="after")
    def _range_and_metrics_are_coherent(self) -> CurveErrorSegment:
        if self.x_stop < self.x_start:
            raise ValueError("curve error segment stop precedes start")
        metrics = (self.rms_error, self.mean_signed_error, self.error_fraction)
        if self.eligible_points:
            if any(value is None for value in metrics):
                raise ValueError("eligible curve error segment requires metrics")
        elif any(value is not None for value in metrics):
            raise ValueError("support-only curve error segment cannot declare metrics")
        return self


class CurveErrorGlobalScore(SchemaModel):
    operator_key: Identifier
    kind: Literal[
        "residual_rms", "residual_max_abs", "mean_signed_difference"
    ]
    status: Literal["available", "unavailable"]
    reported: Annotated[float, Field(allow_inf_nan=False)] | None = None
    recomputed: Annotated[float, Field(allow_inf_nan=False)] | None = None
    unit: Annotated[str, Field(min_length=1, max_length=128)]
    reason_code: Identifier | None = None

    @model_validator(mode="after")
    def _availability_is_coherent(self) -> CurveErrorGlobalScore:
        if self.status == "available":
            if (
                self.reported is None
                or self.recomputed is None
                or self.reason_code is not None
            ):
                raise ValueError("available global score requires only numeric values")
        elif (
            self.reported is not None
            or self.recomputed is not None
            or self.reason_code is None
        ):
            raise ValueError("unavailable global score requires only a reason code")
        return self


class CurveErrorComparisonNumerics(SchemaModel):
    comparison_key: Identifier
    operator_key: Identifier
    value_space: Literal["linear", "log10"]
    global_score: CurveErrorGlobalScore
    segments: Annotated[
        tuple[CurveErrorSegment, ...], Field(min_length=1, max_length=_MAX_SEGMENTS)
    ]
    residual_trace: Annotated[
        tuple[CurveResidualPoint, ...], Field(min_length=1, max_length=_MAX_TRACE_POINTS)
    ]
    truncated: bool = False


class CurveErrorComparisonAnalysis(CurveErrorComparisonNumerics):
    plot_item: Annotated[
        str,
        Field(
            min_length=5,
            max_length=256,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.png$",
        ),
    ]
    plot_sha256: Sha256


class CurveErrorAnalysisReport(SchemaModel):
    profile: Literal["scidiscovery.curve-error-analysis.v1"] = (
        CURVE_ERROR_ANALYSIS_PROFILE
    )
    selection_comparison_key: Identifier | None = None
    curve_bundle_sha256: Sha256
    comparison_spec_sha256: Sha256
    analyses: Annotated[
        tuple[CurveErrorComparisonAnalysis, ...],
        Field(min_length=1, max_length=_MAX_ANALYSES),
    ]
    interpretation_boundary: Literal[
        "deterministic_localization_only_no_physical_interpretation"
    ] = "deterministic_localization_only_no_physical_interpretation"

    @model_validator(mode="after")
    def _analyses_are_unique(self) -> CurveErrorAnalysisReport:
        keys = tuple(
            (item.comparison_key, item.operator_key) for item in self.analyses
        )
        if len(keys) != len(set(keys)):
            raise ValueError("curve error analyses must be unique")
        items = tuple(item.plot_item for item in self.analyses)
        if len(items) != len(set(items)):
            raise ValueError("curve error plot items must be unique")
        return self


@dataclass(frozen=True)
class CurveErrorAnalysisArtifacts:
    report: CurveErrorAnalysisReport
    plots: tuple[tuple[str, bytes], ...]


class _ResidualAtom(SchemaModel):
    """Immutable JSON renderer sample retained inside a numerical checkpoint."""
    x: float
    reference: float | None
    candidate: float | None
    residual: float | None
    support: CurveSupport




class CurveAnalysisUnavailable(ValueError):
    """This input has no residual computation supported by this helper."""




class CurveErrorNumericalItem(SchemaModel):
    """Saved renderer inputs; no plot success or scientific verdict is implied."""
    analysis: CurveErrorComparisonNumerics
    atoms: tuple[_ResidualAtom, ...]
    comparison: CurveComparison
    reference: CurveSeries
    candidate: CurveSeries


class CurveErrorNumerics(SchemaModel):
    profile: Literal["scidiscovery.curve-error-numerics.v1"] = "scidiscovery.curve-error-numerics.v1"
    selection_comparison_key: Identifier | None = None
    curve_bundle_sha256: Sha256
    comparison_spec_sha256: Sha256
    items: Annotated[tuple[CurveErrorNumericalItem, ...], Field(min_length=1, max_length=_MAX_ANALYSES)]


def compute_curve_error(
    bundle: CurveBundle,
    spec: CurveComparisonSpec,
    metric_report: CurveConsistencyReport,
    *,
    comparison_key: str | None = None,
    check_budget: Callable[[], None] = lambda: None,
) -> CurveErrorNumerics:
    """Localize a freshly computed comparison without experiment-time contracts.

    The legacy transform above still verifies its complete-plan inputs. Analysis
    tools save these numerical renderer inputs before attempting optional plots.
    """
    if (not metric_bundle_matches(bundle, metric_report)
            or canonical_sha256(spec) != metric_report.comparison_spec_sha256):
        raise ValueError("localization inputs differ from the metric report")
    check_budget()
    selected = _selected_residual_operators(
        spec, metric_report, comparison_key=comparison_key
    )
    if len(selected) > _MAX_ANALYSES:
        raise ValueError("too many failed residual comparisons; select one comparison_key")

    by_series = {item.series_key: item for item in bundle.series}
    items: list[CurveErrorNumericalItem] = []
    reported_by_comparison = {
        item.comparison_key: item for item in metric_report.comparisons
    }
    for comparison, operator in selected:
        check_budget()
        reported_metric = next(
            item
            for item in reported_by_comparison[comparison.comparison_key].metrics
            if item.operator_key == operator.operator_key
        )
        reference = _series_for_analysis(
            spec, by_series, comparison.reference_series
        )
        candidate = _series_for_analysis(
            spec, by_series, comparison.candidate_series
        )
        atoms = (
            _residual_atoms(reference, candidate, comparison, operator, check_budget=check_budget)
            if reported_metric.status == "available"
            else _unavailable_atoms(reference, candidate, comparison)
        )
        segments, truncated = _adaptive_segments(atoms, check_budget=check_budget)
        trace = tuple(
            CurveResidualPoint(x=item.x, residual=item.residual, support=item.support)
            for item in _display_atoms(atoms)
        )
        analysis = CurveErrorComparisonNumerics(
            comparison_key=comparison.comparison_key,
            operator_key=operator.operator_key,
            value_space=operator.value_space,
            global_score=CurveErrorGlobalScore(
                operator_key=operator.operator_key,
                kind=operator.kind,
                status=reported_metric.status,
                reported=reported_metric.value,
                recomputed=reported_metric.value,
                unit=reported_metric.unit,
                reason_code=reported_metric.reason_code,
            ),
            segments=segments,
            residual_trace=trace,
            truncated=truncated,
        )
        # Freeze the declared checkpoint types, including CurveOperatorSpec children.
        # Request-only subclasses keep their admission rules outside this wire model.
        saved_comparison = CurveComparison.model_validate_json(
            canonical_json(comparison), strict=True)
        items.append(CurveErrorNumericalItem(analysis=analysis, atoms=atoms,
            comparison=saved_comparison, reference=reference, candidate=candidate))

    return CurveErrorNumerics(selection_comparison_key=comparison_key,
        curve_bundle_sha256=metric_report.curve_bundle_sha256,
        comparison_spec_sha256=canonical_sha256(spec), items=tuple(items))


def render_curve_error(numerics: CurveErrorNumerics, *,
                       check_budget: Callable[[], None] = lambda: None) -> CurveErrorAnalysisArtifacts:
    """Render saved numbers without parsing sources, scoring or segment fitting."""
    report_items, plots = [], []
    for item in numerics.items:
        check_budget()
        name = _plot_item(item.analysis.comparison_key, item.analysis.operator_key)
        image = _render_curve_error_plot(item.analysis, atoms=item.atoms,
            comparison=item.comparison, reference=item.reference, candidate=item.candidate)
        check_budget()
        report_items.append(CurveErrorComparisonAnalysis(
            **item.analysis.model_dump(), plot_item=name,
            plot_sha256=hashlib.sha256(image).hexdigest()))
        plots.append((name, image))
    return CurveErrorAnalysisArtifacts(report=CurveErrorAnalysisReport(
        selection_comparison_key=numerics.selection_comparison_key,
        curve_bundle_sha256=numerics.curve_bundle_sha256,
        comparison_spec_sha256=numerics.comparison_spec_sha256,
        analyses=tuple(report_items)), plots=tuple(plots))


def localize_curve_error(bundle: CurveBundle, spec: CurveComparisonSpec,
                         metric_report: CurveConsistencyReport, *,
                         comparison_key: str | None = None,
                         check_budget: Callable[[], None] = lambda: None) -> CurveErrorAnalysisArtifacts:
    """Complete deterministic transform; tools can checkpoint between its phases."""
    return render_curve_error(compute_curve_error(bundle, spec, metric_report,
        comparison_key=comparison_key, check_budget=check_budget), check_budget=check_budget)








def validate_curve_error_plot_collection(
    primary_payload: dict[str, object], items: dict[str, bytes]
) -> None:
    """Bind every finalized plot byte-for-byte to the portable analysis report."""

    from PIL import Image

    raw_analysis = primary_payload.get("curve_analysis")
    if not isinstance(raw_analysis, dict):
        raise ValueError("curve analysis plot collection requires curve_analysis")
    report = CurveErrorAnalysisReport.model_validate_json(
        canonical_json(raw_analysis), strict=True
    )
    expected = {item.plot_item: item.plot_sha256 for item in report.analyses}
    if set(items) != set(expected):
        raise ValueError("curve analysis plot collection differs from report items")
    for name, content in items.items():
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"curve analysis plot is not PNG: {name}")
        if hashlib.sha256(content).hexdigest() != expected[name]:
            raise ValueError(f"curve analysis plot digest differs from report: {name}")
        try:
            with Image.open(io.BytesIO(content)) as image:
                if image.format != "PNG" or image.size != (1200, 800):
                    raise ValueError(
                        f"curve analysis plot dimensions differ from renderer: {name}"
                    )
                image.verify()
        except (OSError, ValueError) as error:
            raise ValueError(f"curve analysis plot is invalid: {name}") from error




def _selected_residual_operators(
    spec: CurveComparisonSpec,
    report: CurveConsistencyReport,
    *,
    comparison_key: str | None,
) -> tuple[tuple[CurveComparison, CurveOperatorSpec], ...]:
    comparisons = {item.comparison_key: item for item in spec.comparisons}
    results = {item.comparison_key: item for item in report.comparisons}
    if comparison_key is not None:
        try:
            comparison = comparisons[comparison_key]
            result = results[comparison_key]
        except KeyError as error:
            raise ValueError("comparison_key is not present in the exact plan") from error
        failed_keys = {
            item.operator_key for item in result.checks if item.status == "fail"
        }
        operators = tuple(
            item
            for item in comparison.operators
            if item.kind in _RESIDUAL_KINDS
            and (not failed_keys or item.operator_key in failed_keys)
        )
        if not operators:
            raise CurveAnalysisUnavailable("selected comparison has no analyzable residual operator")
        return tuple((comparison, item) for item in operators)

    selected: list[tuple[CurveComparison, CurveOperatorSpec]] = []
    for result in report.comparisons:
        if result.status not in {"fail", "unavailable"}:
            continue
        selected_keys = (
            {item.operator_key for item in result.checks if item.status == "fail"}
            if result.status == "fail"
            else {
                item.operator_key
                for item in result.metrics
                if item.status == "unavailable"
            }
        )
        comparison = comparisons[result.comparison_key]
        selected.extend(
            (comparison, item)
            for item in comparison.operators
            if item.kind in _RESIDUAL_KINDS and item.operator_key in selected_keys
        )
    if not selected:
        raise CurveAnalysisUnavailable("metric report has no failed or unavailable residual comparison")
    return tuple(selected)


def _series_for_analysis(
    spec: CurveComparisonSpec,
    available: dict[str, CurveSeries],
    series_key: str,
) -> CurveSeries:
    series = available.get(series_key)
    if series is not None:
        return series
    declaration = next(
        (item for item in spec.series_declarations if item.series_key == series_key),
        None,
    )
    if declaration is None:
        raise ValueError(f"curve series is missing without a declaration: {series_key}")
    return CurveSeries(
        series_key=declaration.series_key,
        case_key=declaration.case_key,
        role=declaration.role,
        x_axis=declaration.x_axis,
        y_axis=declaration.y_axis,
        points=(),
        availability=CurveAvailability(
            status="unavailable",
            reason_code="series_missing",
            rationale="The exact metric input did not contain this declared series.",
        ),
        source_locator=f"curve-declaration:{series_key}",
    )


def _grid(comparison: CurveComparison) -> tuple[float, ...]:
    step = (comparison.domain.stop - comparison.domain.start) / (
        comparison.evaluation_points - 1
    )
    return tuple(
        comparison.domain.stop
        if index == comparison.evaluation_points - 1
        else comparison.domain.start + step * index
        for index in range(comparison.evaluation_points)
    )


def _residual_atoms(
    reference: CurveSeries,
    candidate: CurveSeries,
    comparison: CurveComparison,
    operator: CurveOperatorSpec,
    *,
    check_budget: Callable[[], None] = lambda: None,
) -> tuple[_ResidualAtom, ...]:
    atoms: list[_ResidualAtom] = []
    reference_duplicates = _duplicate_xs(reference, comparison)
    candidate_duplicates = _duplicate_xs(candidate, comparison)
    diagnostic_grid = tuple(
        sorted(set(_grid(comparison)) | reference_duplicates | candidate_duplicates)
    )
    for x in diagnostic_grid:
        check_budget()
        reference_duplicate = x in reference_duplicates
        candidate_duplicate = x in candidate_duplicates
        if reference_duplicate or candidate_duplicate:
            support: CurveSupport = (
                "both_duplicate_x"
                if reference_duplicate and candidate_duplicate
                else "reference_duplicate_x"
                if reference_duplicate
                else "candidate_duplicate_x"
            )
            atoms.append(_ResidualAtom(x=x, reference=None, candidate=None, residual=None, support=support))
            continue
        try:
            reference_value = _raw_value_at(reference, x, comparison.interpolation)
            candidate_value = _raw_value_at(candidate, x, comparison.interpolation)
        except ValueError:
            atoms.append(_ResidualAtom(x=x, reference=None, candidate=None, residual=None, support="invalid"))
            continue
        support = _support(reference_value, candidate_value, comparison)
        residual: float | None
        if support == "invalid" or "floor" in support:
            residual = None
        elif operator.value_space == "log10":
            residual = (
                math.log10(candidate_value) - math.log10(reference_value)
                if support == "both_positive"
                else None
            )
        else:
            residual = candidate_value - reference_value
        atoms.append(
            _ResidualAtom(
                x=x,
                reference=reference_value,
                candidate=candidate_value,
                residual=residual,
                support=support,
            )
        )
    return tuple(atoms)


def _unavailable_atoms(
    reference: CurveSeries,
    candidate: CurveSeries,
    comparison: CurveComparison,
) -> tuple[_ResidualAtom, ...]:
    """Return bounded support-only atoms without interpolating unavailable curves."""

    reference_duplicates = _duplicate_xs(reference, comparison)
    candidate_duplicates = _duplicate_xs(candidate, comparison)
    diagnostic_grid = tuple(
        sorted(set(_grid(comparison)) | reference_duplicates | candidate_duplicates)
    )
    atoms: list[_ResidualAtom] = []
    for x in diagnostic_grid:
        reference_duplicate = x in reference_duplicates
        candidate_duplicate = x in candidate_duplicates
        support: CurveSupport = (
            "both_duplicate_x"
            if reference_duplicate and candidate_duplicate
            else "reference_duplicate_x"
            if reference_duplicate
            else "candidate_duplicate_x"
            if candidate_duplicate
            else "invalid"
        )
        atoms.append(_ResidualAtom(x=x, reference=None, candidate=None, residual=None, support=support))
    return tuple(atoms)


def _duplicate_xs(
    series: CurveSeries, comparison: CurveComparison
) -> set[float]:
    counts: dict[float, int] = {}
    for point in series.points:
        counts[point.x] = counts.get(point.x, 0) + 1
    return {
        x
        for x, count in counts.items()
        if count > 1 and comparison.domain.start <= x <= comparison.domain.stop
    }


def _support(
    reference: float,
    candidate: float,
    comparison: CurveComparison,
) -> CurveSupport:
    if reference < 0 or candidate < 0:
        return "invalid"
    if reference == 0 and candidate == 0:
        return "both_zero"
    if reference == 0:
        return "reference_zero_only"
    if candidate == 0:
        return "candidate_zero_only"
    floor = comparison.floor_mask
    if floor is not None:
        reference_floor = reference <= floor.reference_at_or_below
        candidate_floor = candidate <= floor.candidate_at_or_below
        if reference_floor and candidate_floor:
            return "both_floor"
        if reference_floor:
            return "reference_floor_only"
        if candidate_floor:
            return "candidate_floor_only"
    return "both_positive"


def _adaptive_segments(
    atoms: tuple[_ResidualAtom, ...],
    *,
    check_budget: Callable[[], None] = lambda: None,
) -> tuple[tuple[CurveErrorSegment, ...], bool]:
    ranges: list[tuple[int, int]] = []
    start = 0
    for index in range(1, len(atoms)):
        if (
            atoms[index].support != atoms[index - 1].support
            or (atoms[index].residual is None) != (atoms[index - 1].residual is None)
        ):
            ranges.append((start, index))
            start = index
    ranges.append((start, len(atoms)))
    if len(ranges) > _MAX_SEGMENTS:
        raise ValueError("curve support fragmentation exceeds diagnostic limit")

    truncated = False
    while len(ranges) < _MAX_SEGMENTS:
        candidates: list[tuple[float, int, tuple[int, ...]]] = []
        for range_index, (left, right) in enumerate(ranges):
            partition = _best_partition(atoms, left, right, check_budget=check_budget)
            if partition is not None:
                gain, positions = partition
                if len(ranges) + len(positions) <= _MAX_SEGMENTS:
                    candidates.append((gain, range_index, positions))
        if not candidates:
            break
        _, range_index, positions = max(
            candidates,
            key=lambda item: (
                item[0],
                tuple(-atoms[position].x for position in item[2]),
            ),
        )
        left, right = ranges[range_index]
        boundaries = (left, *positions, right)
        ranges[range_index : range_index + 1] = list(
            zip(boundaries, boundaries[1:])
        )
    if len(ranges) == _MAX_SEGMENTS and any(
        _best_partition(atoms, left, right, check_budget=check_budget) is not None for left, right in ranges
    ):
        truncated = True

    total_error = sum(
        item.residual * item.residual
        for item in atoms
        if item.residual is not None
    )
    segments: list[CurveErrorSegment] = []
    for left, right in ranges:
        values = tuple(
            item.residual for item in atoms[left:right] if item.residual is not None
        )
        error = sum(item * item for item in values)
        segments.append(
            CurveErrorSegment(
                x_start=atoms[left].x,
                x_stop=atoms[right - 1].x,
                support=atoms[left].support,
                eligible_points=len(values),
                rms_error=(
                    math.sqrt(error / len(values)) if values else None
                ),
                mean_signed_error=(
                    sum(values) / len(values) if values else None
                ),
                error_fraction=(
                    error / total_error if values and total_error > 0 else 0.0
                    if values
                    else None
                ),
            )
        )
    return tuple(segments), truncated


def _best_partition(
    atoms: tuple[_ResidualAtom, ...], left: int, right: int,
    *, check_budget: Callable[[], None] = lambda: None,
) -> tuple[float, tuple[int, ...]] | None:
    if right - left < 6 or any(item.residual is None for item in atoms[left:right]):
        return None
    residuals = tuple(float(item.residual) for item in atoms[left:right])
    magnitudes = tuple(abs(item) for item in residuals)
    signed_scale = _sse(residuals) or 1.0
    magnitude_scale = _sse(magnitudes) or 1.0
    base_cost = _sse(residuals) / signed_scale + _sse(magnitudes) / magnitude_scale
    count = len(residuals)
    base_bic = count * math.log(max(base_cost / count, 1e-15)) + 2 * math.log(count)
    best: tuple[float, tuple[int, ...]] | None = None
    offsets = tuple(range(3, count - 2))
    for offset in offsets:
        check_budget()
        split_cost = _partition_cost(
            residuals, magnitudes, (offset,), signed_scale, magnitude_scale
        )
        split_bic = count * math.log(max(split_cost / count, 1e-15)) + 4 * math.log(count)
        gain = base_bic - split_bic
        if gain > 0 and (best is None or gain > best[0]):
            best = (gain, (left + offset,))
    if count >= 9:
        candidate_offsets = _island_boundary_candidates(residuals, magnitudes)
        for first_index, first in enumerate(candidate_offsets):
            check_budget()
            for second in candidate_offsets[first_index + 1 :]:
                if first < 3 or second - first < 3 or count - second < 3:
                    continue
                split_cost = _partition_cost(
                    residuals,
                    magnitudes,
                    (first, second),
                    signed_scale,
                    magnitude_scale,
                )
                split_bic = (
                    count * math.log(max(split_cost / count, 1e-15))
                    + 6 * math.log(count)
                )
                gain = base_bic - split_bic
                if gain > 0 and (best is None or gain > best[0]):
                    best = (gain, (left + first, left + second))
    return best


def _partition_cost(
    residuals: tuple[float, ...],
    magnitudes: tuple[float, ...],
    offsets: tuple[int, ...],
    signed_scale: float,
    magnitude_scale: float,
) -> float:
    boundaries = (0, *offsets, len(residuals))
    return sum(
        _sse(residuals[start:stop]) / signed_scale
        + _sse(magnitudes[start:stop]) / magnitude_scale
        for start, stop in zip(boundaries, boundaries[1:])
    )


def _island_boundary_candidates(
    residuals: tuple[float, ...], magnitudes: tuple[float, ...]
) -> tuple[int, ...]:
    count = len(residuals)
    jumps = sorted(
        range(3, count - 2),
        key=lambda index: (
            abs(residuals[index] - residuals[index - 1])
            + abs(magnitudes[index] - magnitudes[index - 1]),
            -index,
        ),
        reverse=True,
    )[:64]
    if count > 96:
        jumps.extend(
            max(3, min(count - 3, round(index * count / 32)))
            for index in range(1, 32)
        )
    return tuple(sorted(set(jumps)))


def _sse(values: tuple[float, ...]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    return sum((item - mean) ** 2 for item in values)


def _display_atoms(atoms: tuple[_ResidualAtom, ...]) -> tuple[_ResidualAtom, ...]:
    if len(atoms) <= _MAX_TRACE_POINTS:
        return atoms
    priority = {0, len(atoms) - 1}
    numeric = [(index, item.residual) for index, item in enumerate(atoms) if item.residual is not None]
    if numeric:
        priority.add(min(numeric, key=lambda item: item[1])[0])
        priority.add(max(numeric, key=lambda item: item[1])[0])
        priority.add(max(numeric, key=lambda item: abs(item[1]))[0])
    for index in range(1, len(atoms)):
        if atoms[index].support != atoms[index - 1].support:
            priority.update((index - 1, index))
    if len(priority) > _MAX_TRACE_POINTS:
        ordered = sorted(priority)
        step = len(ordered) / _MAX_TRACE_POINTS
        return tuple(atoms[ordered[int(index * step)]] for index in range(_MAX_TRACE_POINTS))
    remaining = _MAX_TRACE_POINTS - len(priority)
    candidates = [index for index in range(len(atoms)) if index not in priority]
    if remaining and candidates:
        step = len(candidates) / remaining
        priority.update(candidates[int(index * step)] for index in range(remaining))
    return tuple(atoms[index] for index in sorted(priority))


def _plot_item(comparison_key: str, operator_key: str) -> str:
    stem = f"{comparison_key}--{operator_key}"
    if len(stem.encode("utf-8")) > 240:
        digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16]
        stem = f"{stem[:220]}--{digest}"
    return f"{stem}.png"


def _render_curve_error_plot(
    analysis: CurveErrorComparisonNumerics,
    *,
    atoms: tuple[_ResidualAtom, ...],
    comparison: CurveComparison,
    reference: CurveSeries,
    candidate: CurveSeries,
) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1200, 800
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    left, right = 90, 1160
    top_box = (left, 70, right, 390)
    residual_box = (left, 455, right, 720)
    score_text = (
        f"{analysis.global_score.recomputed:.6g} {analysis.global_score.unit}"
        if analysis.global_score.recomputed is not None
        else f"unavailable:{analysis.global_score.reason_code}"
    )
    title = _ascii(
        f"{comparison.comparison_key} / {analysis.operator_key} | "
        f"purpose={comparison.purpose} | score={score_text}"
    )
    draw.text((left, 25), title, fill="black", font=font)
    draw.text((left, 42), f"renderer={CURVE_ERROR_PLOT_PROFILE}", fill="#555555", font=font)

    x_values = [item.x for item in atoms]
    x_min, x_max = min(x_values), max(x_values)
    x_scale = reference.x_axis.scale

    def x_pixel(value: float) -> int:
        if x_scale == "log10":
            low, high, current = math.log10(x_min), math.log10(x_max), math.log10(value)
        else:
            low, high, current = x_min, x_max, value
        return round(left + (current - low) * (right - left) / (high - low))

    y_values = [
        value
        for item in atoms
        for value in (item.reference, item.candidate)
        if value is not None and (value > 0 or reference.y_axis.scale != "log10")
    ]
    if not y_values:
        y_values = [0.0, 1.0]
    y_log = reference.y_axis.scale == "log10"
    transformed = [math.log10(value) if y_log else value for value in y_values]
    y_min, y_max = min(transformed), max(transformed)
    if y_min == y_max:
        y_min -= 0.5
        y_max += 0.5

    def top_y_pixel(value: float) -> int | None:
        if y_log and value <= 0:
            return None
        current = math.log10(value) if y_log else value
        return round(top_box[3] - (current - y_min) * (top_box[3] - top_box[1]) / (y_max - y_min))

    _draw_box(draw, top_box)
    _draw_raw_series(
        draw,
        reference,
        comparison,
        x_pixel,
        top_y_pixel,
        "#1f1f1f",
    )
    _draw_raw_series(
        draw,
        candidate,
        comparison,
        x_pixel,
        top_y_pixel,
        "#d62728",
    )
    reference_label = reference.scientific_role or "legacy_reference"
    candidate_label = candidate.scientific_role or "legacy_candidate"
    draw.text((left + 10, top_box[1] + 8), reference_label, fill="#1f1f1f", font=font)
    draw.text((left + 180, top_box[1] + 8), candidate_label, fill="#d62728", font=font)
    draw.text(
        (left, top_box[3] + 8),
        _ascii(f"{reference.x_axis.name} [{reference.x_axis.unit}]"),
        fill="black",
        font=font,
    )
    draw.text(
        (8, top_box[1]),
        _ascii(f"{reference.y_axis.name} [{reference.y_axis.unit}]"),
        fill="black",
        font=font,
    )

    for index, segment in enumerate(analysis.segments):
        x0, x1 = x_pixel(segment.x_start), x_pixel(segment.x_stop)
        if index % 2 == 0:
            draw.rectangle(
                (x0, residual_box[1], max(x0 + 1, x1), residual_box[3]),
                fill="#f2f6ff",
            )
        draw.line((x0, residual_box[1], x0, residual_box[3]), fill="#9aa7bd", width=1)
    residuals = [abs(item.residual) for item in atoms if item.residual is not None]
    residual_limit = max(residuals) if residuals else 1.0
    if residual_limit == 0:
        residual_limit = 1.0

    def residual_y_pixel(value: float) -> int:
        middle = (residual_box[1] + residual_box[3]) / 2
        return round(middle - value * (residual_box[3] - residual_box[1]) * 0.45 / residual_limit)

    _draw_box(draw, residual_box)
    zero_y = residual_y_pixel(0.0)
    draw.line((left, zero_y, right, zero_y), fill="#666666", width=1)
    _draw_residual(draw, atoms, x_pixel, residual_y_pixel)
    support_colors = {
        "both_positive": "#4daf4a",
        "both_floor": "#bdbdbd",
        "reference_floor_only": "#80b1d3",
        "candidate_floor_only": "#fb8072",
        "reference_zero_only": "#377eb8",
        "candidate_zero_only": "#e41a1c",
        "both_zero": "#999999",
        "reference_duplicate_x": "#377eb8",
        "candidate_duplicate_x": "#e41a1c",
        "both_duplicate_x": "#ff7f00",
        "invalid": "#000000",
    }
    band_top = residual_box[3] - 12
    for first, second in zip(atoms, atoms[1:]):
        first_x = x_pixel(first.x)
        second_x = x_pixel(second.x)
        if "duplicate_x" in first.support:
            second_x = first_x + 2
        draw.rectangle(
            (first_x, band_top, second_x, residual_box[3]),
            fill=support_colors[first.support],
        )
    for atom in atoms:
        if "duplicate_x" in atom.support:
            seam_x = x_pixel(atom.x)
            draw.line(
                (seam_x, top_box[1], seam_x, residual_box[3]),
                fill=support_colors[atom.support],
                width=3,
            )
    draw.text((left, residual_box[1] - 18), "signed residual", fill="black", font=font)
    draw.text((left, residual_box[3] + 10), "support band", fill="black", font=font)

    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()


def _draw_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    draw.rectangle(box, outline="#333333", width=1)


def _draw_raw_series(
    draw: ImageDraw.ImageDraw,
    series: CurveSeries,
    comparison: CurveComparison,
    x_pixel: object,
    y_pixel: object,
    color: str,
) -> None:
    points: list[tuple[int, int]] = []
    for point in series.points:
        if not comparison.domain.start <= point.x <= comparison.domain.stop:
            continue
        pixel = y_pixel(point.y)  # type: ignore[operator]
        if pixel is None:
            if len(points) > 1:
                draw.line(points, fill=color, width=2)
            points = []
            continue
        points.append((x_pixel(point.x), pixel))  # type: ignore[operator]
    if len(points) > 1:
        draw.line(points, fill=color, width=2)


def _draw_residual(
    draw: ImageDraw.ImageDraw,
    atoms: tuple[_ResidualAtom, ...],
    x_pixel: object,
    y_pixel: object,
) -> None:
    points: list[tuple[int, int]] = []
    for atom in atoms:
        if atom.residual is None:
            if len(points) > 1:
                draw.line(points, fill="#7b3294", width=2)
            points = []
            continue
        points.append((x_pixel(atom.x), y_pixel(atom.residual)))  # type: ignore[operator]
    if len(points) > 1:
        draw.line(points, fill="#7b3294", width=2)


def _ascii(value: str) -> str:
    return value.encode("ascii", errors="replace").decode("ascii")
