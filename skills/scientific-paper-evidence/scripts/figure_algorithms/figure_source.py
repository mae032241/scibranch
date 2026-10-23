"""Bounded recovery of one exact raster or embedded PDF figure image."""

from __future__ import annotations
from .errors import ToolInputError, declared_violation

import hashlib
import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, __version__ as PILLOW_VERSION
from .figure_dependencies import command_path


MAX_IMAGE_PIXELS = 25_000_000
MAX_PDF_IMAGES_PER_PAGE = 32

@dataclass(frozen=True, slots=True)
class RecoveredFigureImage:
    content: bytes
    width: int
    height: int
    image_sha256: str
    recovery_tool: str = "Pillow"
    recovery_tool_version: str = PILLOW_VERSION
    page: int | None = None
    document_image_index: int | None = None
    page_image_index: int | None = None
    pdf_object_id: int | None = None
    pdf_object_generation: int | None = None

    def public_metadata(self, *, local_path: str) -> dict[str, object]:
        value: dict[str, object] = {
            "local_path": local_path,
            "media_type": "image/png",
            "width": self.width,
            "height": self.height,
            "recovered_image_sha256": self.image_sha256,
            "recovery_tool": self.recovery_tool,
            "recovery_tool_version": self.recovery_tool_version,
            "access": "read_only",
            "cache": "run_local",
        }
        if self.page is not None:
            value.update(
                {
                    "page": self.page,
                    "document_image_index": self.document_image_index,
                    "page_image_index": self.page_image_index,
                    "pdf_object_id": self.pdf_object_id,
                    "pdf_object_generation": self.pdf_object_generation,
                }
            )
        return value


@dataclass(frozen=True, slots=True)
class _PdfImageRow:
    page: int
    document_image_index: int
    page_image_index: int
    width: int
    height: int
    object_id: int
    object_generation: int


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False, compress_level=9)
    return stream.getvalue()


def _canonical_raster(path: Path) -> tuple[bytes, int, int]:
    try:
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            if source.width * source.height > MAX_IMAGE_PIXELS:
                raise ValueError("source image exceeds the bounded pixel budget")
            width, height = source.width, source.height
            content = _png_bytes(source.convert("RGB"))
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("source is not a valid bounded raster image") from error
    return content, width, height


def _run(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess[bytes]:
    command = [command_path(command[0]), *command[1:]]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=max(1, min(60, timeout_seconds)),
        )
    except FileNotFoundError as error:
        raise RuntimeError("pdfimages is not installed") from error
    except subprocess.TimeoutExpired as error:
        raise TimeoutError("PDF image recovery timed out") from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace")[-2048:]
        raise RuntimeError(f"PDF image recovery failed: {detail}")
    return completed


def _pdf_rows(path: Path, *, page: int, timeout_seconds: int) -> tuple[_PdfImageRow, ...]:
    completed = _run(["pdfimages", "-list", str(path)], timeout_seconds=timeout_seconds)
    rows: list[_PdfImageRow] = []
    page_index = 0
    for line in completed.stdout.decode("utf-8", errors="replace").splitlines():
        fields = line.split()
        if len(fields) < 12 or not fields[0].isdigit() or fields[2] != "image":
            continue
        row_page = int(fields[0])
        if row_page != page:
            continue
        rows.append(
            _PdfImageRow(
                page=row_page,
                document_image_index=int(fields[1]),
                page_image_index=page_index,
                width=int(fields[3]),
                height=int(fields[4]),
                object_id=int(fields[10]),
                object_generation=int(fields[11]),
            )
        )
        page_index += 1
    if not rows:
        raise ToolInputError("figure_page_has_no_embedded_image", "The selected PDF page has no recoverable embedded image; locate the figure page from the original PDF text and correct the page selection.", path="$.source_page")
    if len(rows) > MAX_PDF_IMAGES_PER_PAGE:
        raise ValueError("PDF page exceeds the embedded-image count limit")
    if any(row.width * row.height > MAX_IMAGE_PIXELS for row in rows):
        raise ValueError("PDF page contains an image above the pixel budget")
    return tuple(rows)


def _pdfimages_version(*, timeout_seconds: int) -> str:
    completed = _run(["pdfimages", "-v"], timeout_seconds=timeout_seconds)
    value = (completed.stdout + completed.stderr).decode(
        "utf-8", errors="replace"
    ).strip().splitlines()
    if not value:
        raise ValueError("pdfimages did not report its version")
    return f"{value[0][:192]}; Pillow {PILLOW_VERSION}"


def inspect_pdf_images(
    path: Path, *, page: int, timeout_seconds: int = 60
) -> tuple[RecoveredFigureImage, ...]:
    """Recover all ordinary embedded images on one exact PDF page as canonical PNG."""

    rows = _pdf_rows(path, page=page, timeout_seconds=timeout_seconds)
    recovery_version = _pdfimages_version(timeout_seconds=timeout_seconds)
    with tempfile.TemporaryDirectory(prefix="scid-pdf-images-") as raw_directory:
        prefix = Path(raw_directory) / "image"
        _run(
            [
                "pdfimages",
                "-f",
                str(page),
                "-l",
                str(page),
                "-png",
                str(path),
                str(prefix),
            ],
            timeout_seconds=timeout_seconds,
        )
        paths = tuple(sorted(Path(raw_directory).glob("image-*.png")))
        if len(paths) != len(rows):
            raise ValueError(
                "PDF page uses masks or image forms unsupported by this bounded extractor"
            )
        images: list[RecoveredFigureImage] = []
        for row, image_path in zip(rows, paths, strict=True):
            content, width, height = _canonical_raster(image_path)
            if (width, height) != (row.width, row.height):
                raise ValueError("recovered PDF image dimensions differ from its object table")
            images.append(
                RecoveredFigureImage(
                    content=content,
                    width=width,
                    height=height,
                    image_sha256=_sha256(content),
                    recovery_tool="pdfimages+Pillow",
                    recovery_tool_version=recovery_version,
                    page=row.page,
                    document_image_index=row.document_image_index,
                    page_image_index=row.page_image_index,
                    pdf_object_id=row.object_id,
                    pdf_object_generation=row.object_generation,
                )
            )
    return tuple(images)


def inspect_figure_source(
    path: Path,
    *,
    media_type: str,
    page: int | None = None,
    timeout_seconds: int = 60,
) -> tuple[RecoveredFigureImage, ...]:
    normalized = media_type.split(";", 1)[0].strip().lower()
    if normalized == "application/pdf":
        if page is None:
            raise ValueError("PDF figure inspection requires an exact page")
        return inspect_pdf_images(path, page=page, timeout_seconds=timeout_seconds)
    if normalized not in {"image/png", "image/jpeg", "image/webp"}:
        raise ValueError("figure source media type is unsupported")
    if page is not None:
        raise ValueError("raster figure inspection does not accept a PDF page")
    content, width, height = _canonical_raster(path)
    return (
        RecoveredFigureImage(
            content=content,
            width=width,
            height=height,
            image_sha256=_sha256(content),
        ),
    )


def inspect_figure_source_bytes(
    content: bytes,
    *,
    media_type: str,
    page: int | None = None,
    timeout_seconds: int = 60,
) -> tuple[RecoveredFigureImage, ...]:
    suffix = ".pdf" if media_type.split(";", 1)[0].strip().lower() == "application/pdf" else ".img"
    with tempfile.TemporaryDirectory(prefix="scid-figure-source-") as directory:
        path = Path(directory) / f"source{suffix}"
        path.write_bytes(content)
        return inspect_figure_source(
            path,
            media_type=media_type,
            page=page,
            timeout_seconds=timeout_seconds,
        )
