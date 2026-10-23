# Solver-Neutral TCAD Source Declarations

## Responsibility

The author writes the complete solver-language project. The control plane owns
safe file handling, capability identity, arguments, resource policy, reviewed
case values and units, exact source references, and raw process-log capture. It does not
generate, parse, or interpret TCAD source.

## Authoritative Task Contract

When the assignment exposes `deck_materialization_spec_path`, read
`deck/contract/materialization-spec.json`. It projects only immutable
experiment cases, control values, and units. It must not contain SProcess or
SDevice commands, materials, fields, equations, geometry, boundaries, or
simulator state names. The declarations below apply only to that materialized
workspace. Otherwise follow the assignment's editable `deck/project.json`
Schema and exact experiment-plan input; do not create a parallel declarations
contract or assume that the materialization-spec path exists.

Write the complete project below `deck/files/`. Then update
`deck/declarations.json` with:

- `entrypoint`: the production direct-solver `.cmd` file;
- `development_initialization_entrypoint`: optional, a separate minimal `.cmd`
  authored specifically for bounded initialization diagnosis; and
- one `case_anchors` item per planned case, containing its experiment key,
  case key, source-relative path, and one unique exact source locator; and
- zero or more `raw_outputs` items containing only a stable name, safe relative
  output path, and media type for solver-native TDR/PLX/PLT output. Process-log
  capture is automatic and must not be redeclared.

A locator proves only that the named bytes exist. It does not prove their
physical meaning. The independent deck reviewer compares the full solver code
with the experiment plan. The control layer never marks a physical requirement
implemented from a locator.

The control layer converts immutable plan values and units plus declared source
locations into provenance-bearing case bindings. Do not recreate binding or
manifest tables. The declaration Schema does not accept global parameter
anchors or unused-parameter dispositions; do not invent those fields.
If a locator is missing or duplicated, fix the declaration or make the authored
entry unique. Never change TCAD physics to satisfy a control parser: no TCAD
parser exists in control.

Raw-output declarations are mechanical collection contracts, not proof that a
solver command produces the file. The independent reviewer checks source/output
consistency. Control validates only names, safe paths, uniqueness, size policy,
and later collection path/size bindings.

## Initialization Boundary

Control never deletes cases, changes durations, or rewrites the production
deck to synthesize an initialization probe. If initialization diagnosis is
needed, author a separate minimal entrypoint that exercises exactly the intended
initialization step and declare it. Development runs remain provisional and
cannot qualify scientific results.

## Failure Boundary

Materialization may reject only unsafe/missing files, incomplete or duplicate
case declarations, non-unique locators, plan/declaration case mismatch, missing
source references, invalid capability identity, or resource/output contract violations.
Control does not hash scientific source or outputs; approval-binding hashes follow
its architecture allowlist. Read-only views do not establish hostile same-UID
isolation or content-tamper detection.
Material names, equations, units inside TCAD commands, and boundary semantics
belong to authoring and independent review.

When creating an additional text file, omit `expected_bytes` from
`worker_file_write_begin`; the service counts UTF-8 bytes. Pass chunks as
ordinary quoted strings or arrays of lines. JavaScript template literals must
not carry Tcl `${...}`, and `TextEncoder` is unavailable and unnecessary in the
orchestration isolate.
