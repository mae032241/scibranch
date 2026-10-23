"""Typed request and exact-source contract for continuous-line digitization."""

from __future__ import annotations
from .errors import ToolInputError, declared_violation

import hashlib
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .figure_evidence import (
    FigureAxisCalibration,
    FigurePanelAxisCalibration,
)
from .figure_source import RecoveredFigureImage, inspect_figure_source_bytes


FigureKey = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"),
]
ShortText = Annotated[str, Field(min_length=1, max_length=4096)]
PixelBox = tuple[int, int, int, int]
FiniteNumber = int | float
PixelSeed = tuple[FiniteNumber, FiniteNumber]


class _DigitizationModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )


def calibration_from_tick_pairs(
    *, scale: str, unit: str,
    ticks: tuple[tuple[FiniteNumber, FiniteNumber], tuple[FiniteNumber, FiniteNumber]],
    uncertainty_px: FiniteNumber,
) -> FigureAxisCalibration:
    """Normalize measured (pixel, value) pairs without breaking their pairing."""
    low, high = sorted(ticks, key=lambda tick: tick[1])
    return FigureAxisCalibration(
        scale=scale, unit=unit, pixel_min=float(low[0]), value_min=float(low[1]),
        pixel_max=float(high[0]), value_max=float(high[1]),
        reprojection_error_px=float(uncertainty_px),
    )


class FigureAxisRequest(_DigitizationModel):
    scale: Literal["linear", "log10"]
    unit: Annotated[str, Field(max_length=256)]
    ticks: tuple[tuple[FiniteNumber, FiniteNumber], tuple[FiniteNumber, FiniteNumber]]
    uncertainty_px: Annotated[FiniteNumber, Field(ge=0.0, le=1_000_000.0)] = 1.0

    @model_validator(mode="after")
    def _ticks_define_an_axis(self) -> FigureAxisRequest:
        if self.ticks[0][0] == self.ticks[1][0]:
            raise ValueError("axis tick pixels must differ")
        if self.ticks[0][1] == self.ticks[1][1]:
            raise ValueError("axis tick values must differ")
        if self.scale == "log10" and any(value <= 0 for _, value in self.ticks):
            raise ValueError("log10 axis tick values must be positive")
        return self

    def normalized(self) -> FigureAxisCalibration:
        return calibration_from_tick_pairs(
            scale=self.scale,
            unit=self.unit,
            ticks=self.ticks,
            uncertainty_px=self.uncertainty_px,
        )


class FigurePanelAxisRequest(_DigitizationModel):
    x: FigureAxisRequest
    y: FigureAxisRequest

    def normalized(self) -> FigurePanelAxisCalibration:
        return FigurePanelAxisCalibration(
            x=self.x.normalized(),
            y=self.y.normalized(),
        )


class FigureScientificUseLimit(_DigitizationModel):
    pixel_bbox: PixelBox
    reason: ShortText
    applies_to: Literal["quantitative_measurement", "interpolation", "all"]


class FigureDigitizationSeries(_DigitizationModel):
    series_key: FigureKey
    label: ShortText
    primitive_kind: Literal["line"] = "line"
    color: Annotated[str, Field(pattern=r"^#[0-9a-f]{6}$")]
    line_style: Literal["solid", "dashed", "dotted"] = "solid"
    binding_source: Literal["legend", "annotation", "caption"]
    visible_label: ShortText
    binding_bbox: PixelBox
    seeds: Annotated[tuple[PixelSeed, ...], Field(min_length=1, max_length=128)]
    exclusion_regions: Annotated[
        tuple[PixelBox, ...], Field(max_length=128)
    ] = ()
    use_limits: Annotated[tuple[FigureScientificUseLimit, ...], Field(max_length=128)] = ()
    @model_validator(mode="after")
    def _anchors_are_ordered(self) -> FigureDigitizationSeries:
        if any(
            right[0] <= left[0] for left, right in zip(self.seeds, self.seeds[1:])
        ):
            raise ValueError("series seed x coordinates must be strictly increasing")
        return self


class PdfFigureSource(_DigitizationModel):
    source_kind: Literal["pdf_embedded_image"]
    media_type: Literal["application/pdf"]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    page: Annotated[int, Field(ge=1, le=100_000)] | None = None
    document_image_index: Annotated[int, Field(ge=0, le=1_000_000)] | None = None
    page_image_index: Annotated[int, Field(ge=0, le=10_000)] | None = None
    pdf_object_id: Annotated[int, Field(ge=1, le=2**31 - 1)] | None = None
    pdf_object_generation: Annotated[int, Field(ge=0, le=65_535)] | None = 0
    recovered_image_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    width: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    height: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    recovery_tool: Literal["pdfimages+Pillow"] | None = None
    recovery_tool_version: Annotated[str, Field(min_length=1, max_length=256)] | None = None


class RasterFigureSource(_DigitizationModel):
    source_kind: Literal["raster_image"]
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    recovered_image_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    width: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    height: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    recovery_tool: Literal["Pillow"] | None = None
    recovery_tool_version: Annotated[str, Field(min_length=1, max_length=256)] | None = None


FigureSource = Annotated[
    PdfFigureSource | RasterFigureSource,
    Field(discriminator="source_kind"),
]


def _valid_box(value: PixelBox, *, width: int, height: int) -> bool:
    left, top, right, bottom = value
    return 0 <= left < right <= width and 0 <= top < bottom <= height


class FigureDigitizationRequest(_DigitizationModel):
    schema_version: Literal["scidiscovery.curve-figure-digitization-request.v2"]
    request_status: Literal["ready", "unresolved"] = "ready"
    unresolved_reasons: Annotated[tuple[ShortText, ...], Field(max_length=32)] = ()
    figure_key: FigureKey
    panel_key: FigureKey
    figure: ShortText
    citation: ShortText
    source: FigureSource
    plot_bbox: PixelBox | None = None
    axis_calibration: FigurePanelAxisRequest | None = None
    exclusion_regions: Annotated[
        tuple[PixelBox, ...], Field(max_length=128)
    ] = ()
    series: Annotated[
        tuple[FigureDigitizationSeries, ...], Field(max_length=32)
    ] = ()

    @model_validator(mode="after")
    def _request_is_bounded_and_self_consistent(self) -> FigureDigitizationRequest:
        if self.request_status == "unresolved":
            if not self.unresolved_reasons:
                raise ValueError("unresolved figure request requires explicit reasons")
        elif self.unresolved_reasons:
            raise ValueError("ready figure request cannot declare unresolved reasons")
        if self.request_status == "ready" and (
            self.plot_bbox is None or self.axis_calibration is None or not self.series
        ):
            raise ValueError(
                "ready figure request requires plot_bbox, axis_calibration, and series"
            )
        # Unresolved requests may retain known geometry without inventing image bounds.
        width = getattr(self.source, "width", None) or math.inf
        height = getattr(self.source, "height", None) or math.inf
        if self.plot_bbox is not None and not _valid_box(
            self.plot_bbox, width=width, height=height
        ):
            raise ValueError("plot_bbox exceeds the recovered image")
        plot_left, plot_top, plot_right, plot_bottom = self.plot_bbox or (
            0, 0, width, height
        )
        if self.axis_calibration is not None:
            axes = self.axis_calibration.normalized()
            if not (
                plot_left <= min(axes.x.pixel_min, axes.x.pixel_max)
                and max(axes.x.pixel_min, axes.x.pixel_max) < plot_right
                and plot_top <= min(axes.y.pixel_min, axes.y.pixel_max)
                and max(axes.y.pixel_min, axes.y.pixel_max) < plot_bottom
            ):
                raise ValueError("axis calibration exceeds plot_bbox or recovered image")
        if any(
            not _valid_box(box, width=width, height=height)
            for box in self.exclusion_regions
        ):
            raise ValueError("figure exclusion region exceeds the recovered image")
        keys = tuple(item.series_key for item in self.series)
        if len(keys) != len(set(keys)):
            raise ValueError("digitization series keys must be unique")
        for series_index, item in enumerate(self.series):
            if not _valid_box(
                item.binding_bbox,
                width=width,
                height=height,
            ):
                raise declared_violation(f"Series {item.series_key} binding_bbox exceeds the recovered image; correct the legend/annotation box.",
                    path=f"$.series[{series_index}].binding_bbox")
            boxes = (*self.exclusion_regions, *item.exclusion_regions,
                *(limit.pixel_bbox for limit in item.use_limits))
            if any(not _valid_box(box, width=width, height=height) for box in boxes):
                raise declared_violation(f"Series {item.series_key} exclusion/use limit exceeds the recovered image; correct exclusion_regions or use_limits.pixel_bbox.",
                    path=f"$.series[{series_index}]")
            for seed_index, seed in enumerate(item.seeds):
                if not (
                    plot_left <= seed[0] < plot_right
                    and plot_top <= seed[1] < plot_bottom
                ):
                    raise declared_violation(f"Series {item.series_key} seed {seed_index} lies outside plot_bbox; correct that guide point or the visible plot box.",
                        path=f"$.series[{series_index}].seeds[{seed_index}]")
        return self

    def require_ready(self) -> tuple[
        PixelBox, FigurePanelAxisCalibration, tuple[FigureDigitizationSeries, ...]
    ]:
        if (
            self.request_status != "ready"
            or self.plot_bbox is None
            or self.axis_calibration is None
            or not self.series
        ):
            raise ToolInputError("figure_request_unresolved", "An unresolved request cannot be previewed as measured data; retain its reasons and save the unresolved family, or establish plot_bbox, axis_calibration and series visibly.", path="$.request.request_status")
        if any(value is None for value in self.source.model_dump().values()):
            raise ValueError("ready figure request requires complete recovered source metadata")
        return self.plot_bbox, self.axis_calibration.normalized(), self.series


def axis_value(axis: object, pixel: float) -> float:
    fraction = (pixel - axis.pixel_min) / (axis.pixel_max - axis.pixel_min)
    if axis.scale == "linear":
        return axis.value_min + fraction * (axis.value_max - axis.value_min)
    low = math.log10(axis.value_min)
    high = math.log10(axis.value_max)
    return 10 ** (low + fraction * (high - low))


def axis_uncertainty(axis: object, pixel: float, uncertainty_px: float) -> float:
    total = uncertainty_px + axis.reprojection_error_px
    center = axis_value(axis, pixel)
    return max(
        abs(center - axis_value(axis, pixel - total)),
        abs(axis_value(axis, pixel + total) - center),
    )


def materialize_figure_request(value: dict, source_content: bytes) -> dict:
    """Resolve only program metadata from the original bytes and Agent image choice."""
    value = dict(value)
    source = dict(value.get("source", {}))
    actual_hash = hashlib.sha256(source_content).hexdigest()
    if source.get("source_sha256", actual_hash) != actual_hash:
        raise ValueError("figure source hash differs from the typed request")
    source["source_sha256"] = actual_hash
    # The preserved original bytes own file type; page/image selection stays scientific.
    if source_content.startswith(b"%PDF-"):
        media_type, source_kind = "application/pdf", "pdf_embedded_image"
    else:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(source_content)) as image:
            media_type = Image.MIME[image.format]
        source_kind = "raster_image"
    if source.get("media_type", media_type) != media_type or source.get("source_kind", source_kind) != source_kind:
        raise ValueError("figure source type differs from the preserved original")
    source.update(media_type=media_type, source_kind=source_kind)
    value["source"] = source
    if value.get("request_status", "ready") == "unresolved":
        return value
    pdf = source.get("source_kind") == "pdf_embedded_image"
    if pdf and (source.get("page") is None or source.get("document_image_index") is None):
        raise ToolInputError("figure_image_selection_required", "Select page and document_image_index from source inspection before previewing a ready PDF request.", path="$.request.source")
    images = inspect_figure_source_bytes(source_content, media_type=source.get("media_type"),
                                        page=source.get("page") if pdf else None)
    selected = [image for image in images if not pdf or image.document_image_index == source["document_image_index"]]
    if len(selected) != 1:
        raise ToolInputError("figure_image_selection_invalid", "The selected page/document_image_index does not identify one inspected embedded image; inspect the target PDF page and correct the selection.", path="$.request.source.document_image_index")
    metadata = selected[0].public_metadata(local_path="")
    for key in ("recovered_image_sha256", "width", "height", "recovery_tool", "recovery_tool_version",
                "page", "document_image_index", "page_image_index", "pdf_object_id", "pdf_object_generation"):
        if key in metadata:
            source[key] = metadata[key]
    return value


def recover_requested_image(
    source_content: bytes, request: FigureDigitizationRequest
) -> RecoveredFigureImage:
    source = request.source
    if hashlib.sha256(source_content).hexdigest() != source.source_sha256:
        raise ValueError("figure source hash differs from the typed request")
    page = source.page if isinstance(source, PdfFigureSource) else None
    candidates = inspect_figure_source_bytes(
        source_content,
        media_type=source.media_type,
        page=page,
    )
    if isinstance(source, PdfFigureSource):
        selected = tuple(
            item
            for item in candidates
            if (
                item.document_image_index == source.document_image_index
                and item.page_image_index == source.page_image_index
                and item.pdf_object_id == source.pdf_object_id
                and item.pdf_object_generation == source.pdf_object_generation
            )
        )
        if len(selected) != 1:
            raise ValueError("typed request does not select one exact PDF image object")
        image = selected[0]
    else:
        if len(candidates) != 1:
            raise ValueError("raster source recovery is not singular")
        image = candidates[0]
    if (
        image.image_sha256 != source.recovered_image_sha256
        or image.width != source.width
        or image.height != source.height
        or image.recovery_tool != source.recovery_tool
        or image.recovery_tool_version != source.recovery_tool_version
    ):
        raise ValueError("recovered figure image differs from the typed request")
    return image
