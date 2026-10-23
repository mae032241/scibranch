"""Strict manifest for calibrated evidence digitized from one paper figure."""

from __future__ import annotations
from .common import Sha256, canonical_json

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator



FigureKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
ShortText = Annotated[str, Field(min_length=1, max_length=4096)]
Fraction = Annotated[float, Field(ge=0.0, le=1.0)]
MAX_FORMAL_RGB_DISTANCE = 48.0
DataItem = Annotated[
    str,
    Field(
        min_length=1,
        max_length=512,
        pattern=(
            r"^curve_tables/[A-Za-z0-9][A-Za-z0-9._-]*"
            r"--[A-Za-z0-9][A-Za-z0-9._-]*\.csv$"
        ),
    ),
]


class _FigureEvidenceModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class FigureEvidenceSource(_FigureEvidenceModel):
    source_name: FigureKey
    page: Annotated[int, Field(ge=1, le=100000)] | ShortText
    figure: ShortText
    image_sha256: Sha256
    width: Annotated[int, Field(ge=1, le=1_000_000)]
    height: Annotated[int, Field(ge=1, le=1_000_000)]
    pdf_sha256: Sha256 | None = None
    pdf_page: Annotated[int, Field(ge=1, le=100000)] | None = None
    pdf_object: Annotated[str, Field(min_length=1, max_length=256)] | None = None

    @model_validator(mode="after")
    def _pdf_provenance_is_complete(self) -> FigureEvidenceSource:
        fields = (self.pdf_sha256, self.pdf_page, self.pdf_object)
        if any(item is not None for item in fields) and any(
            item is None for item in fields
        ):
            raise ValueError("PDF provenance fields must be supplied together")
        if isinstance(self.page, int) and self.pdf_page is not None:
            if self.page != self.pdf_page:
                raise ValueError("source page and PDF page must agree")
        return self


class FigureAxisCalibration(_FigureEvidenceModel):
    scale: Literal["linear", "log10"]
    unit: Annotated[str, Field(max_length=256)]
    pixel_min: Annotated[float, Field(ge=0.0, le=1_000_000.0)]
    pixel_max: Annotated[float, Field(ge=0.0, le=1_000_000.0)]
    value_min: float
    value_max: float
    reprojection_error_px: Annotated[float, Field(ge=0.0, le=1_000_000.0)]

    @model_validator(mode="after")
    def _endpoints_define_a_transform(self) -> FigureAxisCalibration:
        if self.pixel_min == self.pixel_max:
            raise ValueError("axis pixel endpoints must differ")
        if self.value_min == self.value_max:
            raise ValueError("axis value endpoints must differ")
        if self.scale == "log10" and (
            self.value_min <= 0.0 or self.value_max <= 0.0
        ):
            raise ValueError("log10 axis values must be positive")
        return self


class FigurePanelAxisCalibration(_FigureEvidenceModel):
    x: FigureAxisCalibration
    y: FigureAxisCalibration


class FigureSeriesBinding(_FigureEvidenceModel):
    source: Literal["legend", "annotation", "caption"]
    visible_label: ShortText
    status: Literal["matched", "unresolved"]
    confidence: Fraction
    alternatives: Annotated[tuple[ShortText, ...], Field(max_length=64)] = ()


class FigureSeriesDescriptor(_FigureEvidenceModel):
    color: Annotated[str, Field(pattern=r"^#[0-9a-f]{6}$")]
    color_tolerance: Annotated[
        float, Field(ge=0.0, le=MAX_FORMAL_RGB_DISTANCE)
    ]
    line_style: Literal["solid", "dashed", "dotted", "none"]


class FigureEvidenceUseLimit(_FigureEvidenceModel):
    pixel_bbox: tuple[int, int, int, int]
    reason: ShortText
    applies_to: Literal["quantitative_measurement", "interpolation", "all"]


class FigureEvidenceSeries(_FigureEvidenceModel):
    series_key: FigureKey
    label: ShortText
    primitive_kind: Literal["line", "marker", "fit_segment"]
    descriptor: FigureSeriesDescriptor
    data_item: DataItem
    binding: FigureSeriesBinding
    point_count: Annotated[int, Field(ge=0, le=100_000_000)]
    visible_fraction: Fraction
    max_gap_px: Annotated[int, Field(ge=0, le=1_000_000)]
    uncertainty_px: Annotated[float, Field(ge=0.0, le=1_000_000.0)]
    tracking_diagnostics: Annotated[tuple[ShortText, ...], Field(max_length=256)] = ()
    use_limits: Annotated[tuple[FigureEvidenceUseLimit, ...], Field(max_length=128)] = ()
    exclusion_regions: Annotated[tuple[tuple[int, int, int, int], ...], Field(max_length=256)] = ()
    identity_resolved_point_count: Annotated[int, Field(ge=0)] | None = None
    interpolation_eligible_point_count: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _primitive_has_a_compatible_style(self) -> FigureEvidenceSeries:
        if self.primitive_kind != "marker" and self.descriptor.line_style == "none":
            raise ValueError("line and fit_segment series require a line style")
        return self


class FigureOverdrawSupport(_FigureEvidenceModel):
    group_key: FigureKey
    visible_series: FigureKey
    covered_series: Annotated[
        tuple[FigureKey, ...], Field(min_length=1, max_length=10000)
    ]
    pixel_ranges: Annotated[
        tuple[tuple[int, int], ...], Field(min_length=1, max_length=100000)
    ]
    mode: Literal["overdraw"]
    covered_eligible: bool = Field(
        default=False,
        description=(
            "Deprecated v2 compatibility field; copied support is always ineligible."
        ),
    )
    max_endpoint_distance_px: Annotated[float, Field(ge=0.0, le=1_000_000.0)]

    @model_validator(mode="after")
    def _ranges_and_members_are_valid(self) -> FigureOverdrawSupport:
        if len(self.covered_series) != len(set(self.covered_series)):
            raise ValueError("shared-support covered series must be unique")
        if self.visible_series in self.covered_series:
            raise ValueError("shared-support visible series cannot be covered")
        for left, right in self.pixel_ranges:
            if left < 0 or right <= left:
                raise ValueError("shared-support pixel ranges must be positive intervals")
        return self


class FigureCoincidentOverlapSupport(_FigureEvidenceModel):
    group_key: FigureKey
    member_series: Annotated[
        tuple[FigureKey, ...], Field(min_length=2, max_length=10000)
    ]
    pixel_ranges: Annotated[
        tuple[tuple[int, int], ...], Field(min_length=1, max_length=100000)
    ]
    mode: Literal["coincident_overlap"]
    max_member_distance_px: Annotated[float, Field(ge=0.0, le=1_000_000.0)]

    @model_validator(mode="after")
    def _ranges_and_members_are_valid(self) -> FigureCoincidentOverlapSupport:
        if len(self.member_series) != len(set(self.member_series)):
            raise ValueError("coincident-overlap member series must be unique")
        for left, right in self.pixel_ranges:
            if left < 0 or right <= left:
                raise ValueError(
                    "coincident-overlap pixel ranges must be positive intervals"
                )
        return self


FigureSharedSupport = Annotated[
    FigureOverdrawSupport | FigureCoincidentOverlapSupport,
    Field(discriminator="mode"),
]


class FigureEvidencePanel(_FigureEvidenceModel):
    panel_key: FigureKey
    citation: ShortText
    axis_calibration: FigurePanelAxisCalibration | None = None
    series: Annotated[
        tuple[FigureEvidenceSeries, ...], Field(max_length=10000)
    ]
    shared_support: Annotated[
        tuple[FigureSharedSupport, ...], Field(max_length=10000)
    ] | None = None

    @model_validator(mode="after")
    def _series_keys_are_unique(self) -> FigureEvidencePanel:
        if self.series and self.axis_calibration is None:
            raise ValueError("measured series require calibrated axes")
        keys = tuple(item.series_key for item in self.series)
        if len(keys) != len(set(keys)):
            raise ValueError("series keys must be unique within a panel")
        shared_support = self.shared_support or ()
        groups = tuple(item.group_key for item in shared_support)
        if len(groups) != len(set(groups)):
            raise ValueError("shared-support group keys must be unique within a panel")
        known = set(keys)
        claims: dict[str, list[tuple[int, int]]] = {}
        for support in shared_support:
            members = (
                (support.visible_series, *support.covered_series)
                if support.mode == "overdraw"
                else support.member_series
            )
            if not set(members).issubset(known):
                raise ValueError("shared support references an unknown series")
            for member in members:
                for left, right in support.pixel_ranges:
                    claims.setdefault(member, []).append((left, right))
        for intervals in claims.values():
            ordered = sorted(intervals)
            if any(
                current[0] < previous[1]
                for previous, current in zip(ordered, ordered[1:])
            ):
                raise ValueError("shared-support pixel ranges overlap")
        return self


class FigureEvidenceAmbiguity(_FigureEvidenceModel):
    panel_key: FigureKey
    series_key: FigureKey
    message: ShortText
    candidates: Annotated[tuple[ShortText, ...], Field(max_length=128)] = ()


class FigureEvidenceMetrics(_FigureEvidenceModel):
    panel_count: Annotated[int, Field(ge=0, le=10000)]
    series_count: Annotated[int, Field(ge=0, le=100_000_000)]
    qualified_series_count: Annotated[int, Field(ge=0, le=100_000_000)]
    total_point_count: Annotated[int, Field(ge=0, le=100_000_000)]
    minimum_visible_fraction: Fraction


class FigureEvidenceArtifact(_FigureEvidenceModel):
    collection: Literal["source_panels", "audit_overlays", "curve_tables"]
    data_item: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            pattern=(
                r"^(source_panels|audit_overlays|curve_tables)/"
                r"[A-Za-z0-9][A-Za-z0-9._-]*$"
            ),
        ),
    ]
    sha256: Sha256
    bytes: Annotated[int, Field(ge=1, le=2**50)]
    media_type: Annotated[
        str,
        Field(
            min_length=3,
            max_length=256,
            pattern=r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$",
        ),
    ]

    @model_validator(mode="after")
    def _collection_matches_path(self) -> FigureEvidenceArtifact:
        if self.data_item.split("/", 1)[0] != self.collection:
            raise ValueError("artifact collection must match its data_item prefix")
        return self


class FigureEvidenceProvenance(_FigureEvidenceModel):
    spec_sha256: Sha256
    recovery_tool: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    recovery_tool_version: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    output_artifacts: Annotated[
        tuple[FigureEvidenceArtifact, ...], Field(max_length=100000)
    ]

    @model_validator(mode="after")
    def _recovery_identity_is_complete(self) -> FigureEvidenceProvenance:
        if (self.recovery_tool is None) != (self.recovery_tool_version is None):
            raise ValueError("recovery tool and version must be supplied together")
        return self

    @model_validator(mode="after")
    def _artifact_items_are_unique(self) -> FigureEvidenceProvenance:
        items = tuple(item.data_item for item in self.output_artifacts)
        if len(items) != len(set(items)):
            raise ValueError("provenance data_item values must be unique")
        return self


class FigureEvidenceManifest(_FigureEvidenceModel):
    schema_version: Literal["scidiscovery.figure-evidence-manifest.v1"]
    figure_key: FigureKey
    status: Literal["qualified", "unresolved"]
    source: FigureEvidenceSource
    panels: Annotated[
        tuple[FigureEvidencePanel, ...], Field(max_length=10000)
    ]
    metrics: FigureEvidenceMetrics
    ambiguities: Annotated[
        tuple[FigureEvidenceAmbiguity, ...], Field(max_length=100000)
    ] = ()
    provenance: FigureEvidenceProvenance

    @model_validator(mode="after")
    def _manifest_is_internally_consistent(self) -> FigureEvidenceManifest:
        if not self.panels or not any(panel.series for panel in self.panels):
            raise ValueError("figure evidence manifest requires panels and series")
        panel_keys = tuple(panel.panel_key for panel in self.panels)
        if len(panel_keys) != len(set(panel_keys)):
            raise ValueError("panel keys must be unique")

        by_series = {
            (panel.panel_key, series.series_key): series
            for panel in self.panels
            for series in panel.series
        }
        data_items = tuple(series.data_item for series in by_series.values())
        if len(data_items) != len(set(data_items)):
            raise ValueError("series data_item values must be unique")

        ambiguous_series: set[tuple[str, str]] = set()
        for ambiguity in self.ambiguities:
            key = (ambiguity.panel_key, ambiguity.series_key)
            if key not in by_series:
                raise ValueError("ambiguity references an unknown panel or series")
            ambiguous_series.add(key)

        if self.status == "qualified":
            if self.ambiguities:
                raise ValueError("qualified manifest cannot contain ambiguities")
            if any(
                series.binding.status != "matched" for series in by_series.values()
            ):
                raise ValueError("qualified manifest requires matched bindings")

        for panel in self.panels:
            if panel.axis_calibration is None:
                continue
            x_axis = panel.axis_calibration.x
            y_axis = panel.axis_calibration.y
            if max(x_axis.pixel_min, x_axis.pixel_max) >= self.source.width:
                raise ValueError("panel x calibration exceeds source width")
            if max(y_axis.pixel_min, y_axis.pixel_max) >= self.source.height:
                raise ValueError("panel y calibration exceeds source height")

        series_count = len(by_series)
        qualified_count = sum(
            series.binding.status == "matched" and key not in ambiguous_series
            for key, series in by_series.items()
        )
        visible_minimum = min((series.visible_fraction for series in by_series.values()), default=0.0)
        expected_metrics = {
            "panel_count": len(self.panels),
            "series_count": series_count,
            "qualified_series_count": qualified_count,
            "total_point_count": sum(
                series.point_count for series in by_series.values()
            ),
        }
        for field_name, expected in expected_metrics.items():
            if getattr(self.metrics, field_name) != expected:
                raise ValueError(f"metrics.{field_name} is inconsistent")
        if not math.isclose(
            self.metrics.minimum_visible_fraction,
            visible_minimum,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("metrics.minimum_visible_fraction is inconsistent")

        artifact_items = {
            item.data_item for item in self.provenance.output_artifacts
        }
        if not set(data_items).issubset(artifact_items):
            raise ValueError("every curve data_item requires provenance")
        if not any(
            item.collection == "source_panels"
            and item.sha256 == self.source.image_sha256
            for item in self.provenance.output_artifacts
        ):
            raise ValueError(
                "source image SHA-256 requires matching source-panel provenance"
            )
        return self


class FigureEvidenceValidationSeries(_FigureEvidenceModel):
    panel_key: FigureKey
    series_key: FigureKey
    data_item: DataItem
    csv_sha256: Sha256
    row_count: Annotated[int, Field(ge=0, le=100_000_000)]
    observed_row_count: Annotated[int, Field(ge=0, le=100_000_000)]
    eligible_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None
    below_detection_limit_row_count: Annotated[
        int, Field(ge=0, le=100_000_000)
    ] | None
    point_index_min: Annotated[int, Field(ge=0, le=100_000_000)] | None
    point_index_max: Annotated[int, Field(ge=0, le=100_000_000)] | None
    point_index_gap_count: Annotated[int, Field(ge=0, le=100_000_000)]
    unique_pixel_count: Annotated[int, Field(ge=0, le=100_000_000)]
    duplicate_pixel_row_count: Annotated[int, Field(ge=0, le=100_000_000)]
    direct_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    shared_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    shared_pixel_row_count: Annotated[int, Field(ge=0, le=100_000_000)] = 0
    shared_observation_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    identity_resolved_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    interpolation_eligible_row_count: Annotated[int, Field(ge=0, le=100_000_000)] | None = None

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> FigureEvidenceValidationSeries:
        if self.observed_row_count > self.row_count:
            raise ValueError("observed row count exceeds total rows")
        for value in (
            self.eligible_row_count,
            self.below_detection_limit_row_count,
        ):
            if value is not None and value > self.row_count:
                raise ValueError("flagged row count exceeds total rows")
        if self.unique_pixel_count + self.duplicate_pixel_row_count != self.row_count:
            raise ValueError("series pixel counts do not match total rows")
        if (self.direct_row_count is None) != (self.shared_row_count is None):
            raise ValueError("direct/shared row counts must be supplied together")
        if (
            self.direct_row_count is not None
            and self.direct_row_count + int(self.shared_row_count) + self.shared_pixel_row_count != self.row_count
        ):
            raise ValueError("direct/shared row counts do not match total rows")
        if self.row_count == 0:
            if self.point_index_min is not None or self.point_index_max is not None:
                raise ValueError("empty series cannot declare an index range")
            if self.point_index_gap_count != 0:
                raise ValueError("empty series cannot declare index gaps")
        else:
            if self.point_index_min is None or self.point_index_max is None:
                raise ValueError("non-empty series requires an index range")
            if (
                self.point_index_max
                - self.point_index_min
                + 1
                - self.row_count
                != self.point_index_gap_count
            ):
                raise ValueError("series point index gap count is inconsistent")
        return self


class FigureEvidenceSupportingTable(_FigureEvidenceModel):
    data_item: Annotated[
        str,
        Field(
            min_length=1,
            max_length=512,
            pattern=(
                r"^curve_tables/[A-Za-z0-9][A-Za-z0-9._-]*\.csv$"
            ),
        ),
    ]
    csv_sha256: Sha256
    row_count: Annotated[int, Field(ge=0, le=100_000_000)]


class FigureEvidenceScientificRoleCount(_FigureEvidenceModel):
    scientific_role: FigureKey
    row_count: Annotated[int, Field(ge=1, le=100_000_000)]


class FigureEvidenceValidationMetrics(_FigureEvidenceModel):
    curve_table_count: Annotated[int, Field(ge=0, le=100_000_000)]
    total_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)]
    observed_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)]
    eligibility_flagged_table_count: Annotated[int, Field(ge=0, le=100_000_000)]
    eligible_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)]
    detection_limit_flagged_table_count: Annotated[
        int, Field(ge=0, le=100_000_000)
    ]
    below_detection_limit_curve_rows: Annotated[
        int, Field(ge=0, le=100_000_000)
    ]
    global_unique_pixel_count: Annotated[int, Field(ge=0, le=100_000_000)]
    global_duplicate_pixel_rows: Annotated[int, Field(ge=0, le=100_000_000)]
    cross_series_shared_pixel_count: Annotated[int, Field(ge=0, le=100_000_000)]
    direct_pixel_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    shared_occlusion_curve_rows: Annotated[
        int, Field(ge=0, le=100_000_000)
    ] | None = None
    shared_pixel_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    shared_observation_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    identity_resolved_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)] | None = None
    interpolation_eligible_curve_rows: Annotated[int, Field(ge=0, le=100_000_000)] | None = None


class FigureEvidenceValidationReport(_FigureEvidenceModel):
    schema_version: Literal["scidiscovery.figure-evidence-validation-report.v1"]
    validator_version: Literal["2", "3", "4", "5", "6", "7"]
    integrity_status: Literal["valid"]
    figure_key: FigureKey
    source_status: Literal["qualified", "unresolved"]
    manifest_sha256: Sha256
    bundle_fingerprint_sha256: Sha256
    validated_artifact_count: Annotated[int, Field(ge=1, le=100_001)]
    series: Annotated[
        tuple[FigureEvidenceValidationSeries, ...],
        Field(max_length=10000),
    ]
    supporting_tables: Annotated[
        tuple[FigureEvidenceSupportingTable, ...], Field(max_length=10000)
    ] = ()
    scientific_role_counts: Annotated[
        tuple[FigureEvidenceScientificRoleCount, ...], Field(max_length=10000)
    ] = ()
    metrics: FigureEvidenceValidationMetrics

    @model_validator(mode="after")
    def _derived_counts_are_consistent(self) -> FigureEvidenceValidationReport:
        if not self.series:
            raise ValueError("figure evidence validation report requires measured series")
        series_keys = tuple(
            (item.panel_key, item.series_key) for item in self.series
        )
        if len(series_keys) != len(set(series_keys)):
            raise ValueError("validation report series identities must be unique")
        data_items = tuple(item.data_item for item in self.series) + tuple(
            item.data_item for item in self.supporting_tables
        )
        if len(data_items) != len(set(data_items)):
            raise ValueError("validation report data items must be unique")
        role_names = tuple(item.scientific_role for item in self.scientific_role_counts)
        if len(role_names) != len(set(role_names)):
            raise ValueError("validation report scientific roles must be unique")
        expected = {
            "curve_table_count": len(self.series),
            "total_curve_rows": sum(item.row_count for item in self.series),
            "observed_curve_rows": sum(
                item.observed_row_count for item in self.series
            ),
            "eligibility_flagged_table_count": sum(
                item.eligible_row_count is not None for item in self.series
            ),
            "eligible_curve_rows": sum(
                item.eligible_row_count or 0 for item in self.series
            ),
            "detection_limit_flagged_table_count": sum(
                item.below_detection_limit_row_count is not None
                for item in self.series
            ),
            "below_detection_limit_curve_rows": sum(
                item.below_detection_limit_row_count or 0
                for item in self.series
            ),
            "global_duplicate_pixel_rows": (
                self.metrics.total_curve_rows
                - self.metrics.global_unique_pixel_count
            ),
        }
        if self.validator_version in {"3", "4", "5", "6", "7"}:
            if any(
                item.direct_row_count is None or item.shared_row_count is None
                for item in self.series
            ):
                raise ValueError(
                    "validator v3/v4/v5 requires direct/shared series counts"
                )
            expected.update(
                {
                    "direct_pixel_curve_rows": sum(
                        int(item.direct_row_count) for item in self.series
                    ),
                    "shared_occlusion_curve_rows": sum(
                        int(item.shared_row_count) for item in self.series
                    ),
                }
            )
        if self.validator_version == "7":
            for metric, row_field in (
                ("shared_pixel_curve_rows", "shared_pixel_row_count"),
                ("shared_observation_curve_rows", "shared_observation_row_count"),
                ("identity_resolved_curve_rows", "identity_resolved_row_count"),
                ("interpolation_eligible_curve_rows", "interpolation_eligible_row_count"),
            ):
                expected[metric] = sum(getattr(item, row_field) or 0 for item in self.series)
        for field_name, value in expected.items():
            if getattr(self.metrics, field_name) != value:
                raise ValueError(f"metrics.{field_name} is inconsistent")
        if self.metrics.global_unique_pixel_count > self.metrics.total_curve_rows:
            raise ValueError("global unique pixel count exceeds total rows")
        if (
            self.metrics.cross_series_shared_pixel_count
            > self.metrics.global_unique_pixel_count
        ):
            raise ValueError("shared pixel count exceeds unique pixel count")
        return self


def validate_figure_evidence_manifest(
    value: dict[str, object],
) -> dict[str, object]:
    return FigureEvidenceManifest.model_validate_json(
        canonical_json(value), strict=True
    ).model_dump(mode="json", exclude_none=True)


def validate_figure_evidence_validation_report(
    value: dict[str, object],
) -> dict[str, object]:
    return FigureEvidenceValidationReport.model_validate_json(
        canonical_json(value), strict=True
    ).model_dump(mode="json", exclude_none=False)
