# Explicit numerical contracts

Export exact JSON schemas with `python scripts/curve_analysis.py schema --output schemas.json`. JSON arrays map to immutable tuple fields. Unknown keys and nonfinite numeric values are rejected. Every numeric curve requires explicit availability and source locator; available curves need at least two points. Duplicate x observations remain in the source and cannot be silently averaged or treated as an interpolation seam.

For CSV normalization, the source specification is:

```json
{"format":"csv","input_alias":"original_table","series_key":"reference","case_key":"case","role":"reference","x_axis":{"name":"depth","unit":"um","scale":"linear"},"y_axis":{"name":"density","unit":"cm^-3","scale":"log10"},"x_column":"depth_um","y_column":"concentration"}
```

Column names must match unique headers. Missing/non-numeric cells, oversized sources and unsupported line separators fail. No unit conversion, deduplication or sorting repair occurs during CSV parsing.

SProcess record logs use exact `SCID_CURVE_V1|POINT|case|series|index|x|y` and completion records. The `SProcessLogSourceSpec` schema declares every expected case/series, axes and point limits. Unrelated solver output is ignored; malformed declared records, missing completion or unexpected series are rejected or retained as unavailable according to the parser's explicit rules.

Native ASCII PLX requires quoted dataset headers followed by exactly two finite numeric columns. `SProcessPLXSourceSpec` binds one series, exact dataset name when needed and required support intervals. Multiple datasets are never selected by recency or guessed label. The audit records selected/ignored datasets, duplicates and x decreases.

Comparison specifications declare `spec_key` and `comparisons`. Each comparison names distinct `reference_series`/`candidate_series`, a finite increasing `domain` with its unit, `linear_y` or `log10_y` interpolation, a sample count, and explicit operators. Operators are point difference, RMS/max/mean residual, crossing shift or width shift. Crossing/width operators require unique supported crossings; ambiguity returns unavailable. `floor_mask` must state thresholds, unit and rationale. Log metrics never manufacture positive values from zeros; masked intervals, gaps, exclusions and repeated x seams remain explicit limitations.

Sources are bounded to 16 MiB per CLI input and 65,536 total comparison points. Scoring accepts at most 16 comparisons, 16 operators per comparison and 4,096 samples. Total sample/operator work is at most 65,536 and weighted interpolation work at most 2,000,000. These deterministic resource limits confer no experiment execution authority.
