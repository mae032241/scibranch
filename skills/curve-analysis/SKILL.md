---
name: curve-analysis
description: Normalize explicit CSV, SProcess log or PLX curves, compute declared curve metrics, and localize residuals with deterministic scripts. Use for numerical curve comparison and diagnostics; scientific interpretation and solver execution remain separate.
---

Use `scripts/curve_analysis.py` relative to this frozen Skill directory. The scripts need Python 3.10+ and Pydantic 2; plots additionally need Pillow 10+. Read [input contracts](references/input-contracts.md) for normalization formats and limits. `requirements.txt` declares Python dependencies; the script does not install them.

Select exact input files and declare series, columns, units, interpolation, support, operators and thresholds. Do not infer absent units, fill missing measurements, repair duplicate x coordinates, extrapolate through gaps or replace unavailable results with zero. Distinct unit scales require explicit conversion before comparison.

Run `preflight --output dependencies.json` before numerical work, adding `--plots` for plots. Preserve that receipt with results. To require an exact environment, supply it as the global `--dependency-contract dependencies.json` argument on later commands; a mismatch fails. All commands work from arbitrary directories; outputs go to explicitly supplied paths.

- `schema --output schemas.json` exports the executable input contracts.
- `normalize --format csv|sprocess-log|plx --input source --spec source.json --output bundle.json` writes a numeric bundle, `.audit.json` and `.dependencies.json` siblings. Combine separate bundles only by explicitly retaining source digests and unique series identities.
- `score --bundle bundle.json --spec comparison.json --output metrics.json [--plot overview.png]` computes only the requested metrics and thresholds.
- `diagnose --bundle bundle.json --spec comparison.json --output-dir diagnosis [--comparison-key key]` localizes failed/unavailable residual comparisons, saving numerical renderer inputs before plots. It can decline a report without a supported residual failure.

Keep input bytes, dependency receipts, declared support and missing reasons with the output. Threshold pass/fail is a mechanical result; the Worker owns scientific conclusions and suitability. This Skill supplies no execution permission, independent review or scientific qualification.
