# Measurement contract

Requests use `scidiscovery.curve-figure-digitization-request.v2`. The executable schemas in `scripts/figure_algorithms` are authoritative; export them with the CLI `schema` command. No mutable repository or another Skill is needed.

A ready request binds `figure_key`, `panel_key`, `figure`, `citation`, source selection, `plot_bbox`, `axis_calibration`, and `series`. `source` may initially be empty for a raster; for a PDF supply exact `page` and `document_image_index` selected from inspection. Materialization adds the original SHA-256, exact recovered image identity, dimensions, source type and recovery versions; conflicting supplied metadata is rejected.

Each axis declares `scale` (`linear` or `log10`), `unit`, two `[pixel,value]` tick pairs and `uncertainty_px`. Pixel/value pairing survives ascending or descending image orientation. Log calibration requires positive values. Pixel uncertainty propagates through the declared axis transform; do not substitute a generic fractional error.

Each line declares its exact `series_key`, label, RGB hex color, solid/dashed/dotted style, `binding_source` (legend/annotation/caption), `visible_label`, `binding_bbox`, and ordered `[x,y]` guide seeds. Identity anchors guide tracking; neither visual proximity alone nor an apparent crossing establishes physical identity. Ambiguous candidates retain separate supported pixels and interpolation restrictions. Coincident observations preserve shared source provenance rather than silently creating independent data.

Global/per-series exclusions and `use_limits` remain attached to CSV evidence and downstream support intervals. Disconnected strokes and forbidden interpolation never become synthetic crossings. Raw and subpixel positions are both checked against exclusions. Explicit unresolved requests are rejected for numerical digitization and publish no measured evidence directory.

The output retains source SHA-256, original image identity, calibration, CSV point coordinates, pixel uncertainty, per-point permissions, shared observations, overlays and numeric redraw. Missing legend color or ambiguous binding yields an unresolved manifest/series and unavailable numeric support. The validation report must bind exact manifest and sibling bytes. Numeric export recomputes validation and checks that the saved report reproduces.

Raster images are bounded to 25 million pixels. PDF recovery accepts only a selected page with at most 32 embedded images and rejects masks/forms that cannot be bound exactly. Sources are limited to 64 MiB by the CLI. Poppler is used only for PDF image recovery; standalone raster work does not need it. Preserve `dependencies.json`; version contracts compare Pydantic, Pillow and, for PDF work, Poppler plus the resolved `pdfimages` path.
