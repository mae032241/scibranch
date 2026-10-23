"""Portable numeric curve export data; no scoring or qualification."""
from __future__ import annotations
from typing import Annotated, Literal
from pydantic import Field, model_validator
from .common import Identifier, SchemaModel, Sha256
CurveStatus = Literal["available", "unavailable"]
CurveScientificRole = Literal["experimental_target", "simulation_candidate", "numerical_reference", "numerical_variant", "analytic_control", "diagnostic_series"]

class CurveAxis(SchemaModel):
    name: Identifier
    unit: Annotated[str, Field(min_length=1, max_length=128)]
    scale: Literal["linear", "log10"] = "linear"

class CurvePoint(SchemaModel):
    x: Annotated[float, Field(allow_inf_nan=False)]
    y: Annotated[float, Field(allow_inf_nan=False)]

class CurveInterval(SchemaModel):
    start: Annotated[float, Field(allow_inf_nan=False)]
    stop: Annotated[float, Field(allow_inf_nan=False)]

    @model_validator(mode="after")
    def _ordered(self) -> CurveInterval:
        if self.stop <= self.start:
            raise ValueError("curve interval stop must exceed start")
        return self

class CurveAvailability(SchemaModel):
    status: CurveStatus
    reason_code: Identifier | None = None
    rationale: Annotated[str, Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def _reason_matches_status(self) -> CurveAvailability:
        if self.status == "available" and self.reason_code is not None:
            raise ValueError("available curve cannot declare an unavailable reason")
        if self.status == "unavailable" and self.reason_code is None:
            raise ValueError("unavailable curve requires a reason code")
        return self

class CurveSharedSupport(SchemaModel):
    """Common source observations; weights are decided by a scoring contract."""
    group_key: Identifier
    member_series: Annotated[tuple[Identifier, ...], Field(min_length=2, max_length=10000)]
    source_locator: Annotated[str, Field(min_length=1, max_length=4096)]
    source_pixel_ranges: Annotated[tuple[tuple[int, int], ...], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def _members_and_ranges_are_coherent(self):
        if len(self.member_series) != len(set(self.member_series)):
            raise ValueError("shared support members must be unique")
        if any(left < 0 or right <= left for left, right in self.source_pixel_ranges):
            raise ValueError("shared support source pixel ranges must be positive intervals")
        return self

class CurveSeries(SchemaModel):
    series_key: Identifier
    case_key: Identifier
    role: Identifier
    scientific_role: CurveScientificRole | None = None
    x_axis: CurveAxis
    y_axis: CurveAxis
    points: Annotated[tuple[CurvePoint, ...], Field(max_length=1_000_000)]
    valid_intervals: Annotated[
        tuple[CurveInterval, ...], Field(default=(), max_length=4096)
    ]
    exclusions: Annotated[
        tuple[CurveInterval, ...], Field(default=(), max_length=4096)
    ]
    availability: CurveAvailability
    source_locator: Annotated[str, Field(min_length=1, max_length=4096)]
    shared_support: Annotated[tuple[CurveSharedSupport, ...], Field(default=(), max_length=10000)]

    @model_validator(mode="after")
    def _points_and_intervals_are_coherent(self) -> CurveSeries:
        if self.availability.status == "available" and len(self.points) < 2:
            raise ValueError("available curve requires at least two points")
        xs = tuple(item.x for item in self.points)
        if self.x_axis.scale == "log10" and any(item.x <= 0 for item in self.points):
            raise ValueError("log10 x-axis requires positive point values")
        if self.y_axis.scale == "log10" and any(item.y < 0 for item in self.points):
            raise ValueError("log10 y-axis requires nonnegative point values")
        for label, intervals in (
            ("valid", self.valid_intervals),
            ("exclusion", self.exclusions),
        ):
            if any(
                right.start < left.stop
                for left, right in zip(intervals, intervals[1:])
            ):
                raise ValueError(f"curve {label} intervals must not overlap")
            if self.points and any(
                item.start < min(xs) or item.stop > max(xs) for item in intervals
            ):
                raise ValueError(f"curve {label} interval exceeds point domain")
        return self

class CurveBundle(SchemaModel):
    source_profile: Identifier
    source_digests: Annotated[
        tuple[Sha256, ...], Field(min_length=1, max_length=4096)
    ]
    series: Annotated[tuple[CurveSeries, ...], Field(min_length=1, max_length=10000)]

    @model_validator(mode="after")
    def _series_are_unique(self) -> CurveBundle:
        keys = tuple(item.series_key for item in self.series)
        identities = tuple((item.case_key, item.role) for item in self.series)
        if len(keys) != len(set(keys)):
            raise ValueError("curve bundle series keys must be unique")
        if len(identities) != len(set(identities)):
            raise ValueError("curve bundle case/role identities must be unique")
        if len(self.source_digests) != len(set(self.source_digests)):
            raise ValueError("curve bundle source digests must be unique")
        return self
