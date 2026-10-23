---
name: scientific-paper-evidence
description: Digitize explicitly identified scientific paper figure lines from raster images or selected embedded PDF images into CSV, calibrated coordinates, uncertainty and validated overlays. Use with supplied axes and identity anchors; do not infer missing labels, units or scientific identity.
---

Use `scripts/figure_evidence.py` relative to this frozen Skill directory. It requires Python 3.10+, Pydantic 2 and Pillow 10+; PDF sources additionally require Poppler `pdfimages` on PATH. `requirements.txt` declares Python dependencies. The script never installs dependencies or invokes a model or solver.

Read [measurement contract](references/measurement-contract.md) for exact geometry, identity and failure rules. Export request/manifest/report schemas with `schema --output schemas.json` when constructing inputs.

1. Select exact original bytes. Run `preflight [--pdf] --output dependencies.json`; preserve its runtime versions. A global `--dependency-contract dependencies.json` on subsequent commands requires an exact match.
2. Inspect using `inspect --input original [--page N] --output source.json`. PDFs require an explicit page and then a document image index chosen from this inspection. The script does not locate plots, OCR labels or select a scientific curve.
3. Declare figure/panel identity, citation, plot bounds, linear/log tick pairs and uncertainty, series colors/styles, visible legend/annotation/caption bindings and guide anchors. Then run `digitize --input original --request request.json --output-dir evidence`. Use a new output directory. The script binds source metadata from the original and writes CSV, source image, overlay, numeric redraw, fail-closed manifest, mechanical validation report, exact request and dependency receipt.
4. Inspect the source overlay and numeric redraw against the original. Retain ambiguity and scientific-use restrictions. To export numeric curves, run `normalize --input-dir evidence --output curves.json`; it revalidates all declared files and rejects stale or altered reports before export. Audit/dependency receipts accompany the bundle.

The manifest's `qualified` status describes matched mechanical source/identity checks only. It is no scientific review verdict. The Worker owns source interpretation, uncertainty suitability and conclusions; unresolved or ambiguous bindings cannot be upgraded by this Skill. Skill selection gives no execution permission or independent qualification.
