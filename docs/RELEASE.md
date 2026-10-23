# Release and qualification scope

[简体中文](RELEASE.zh-CN.md) | English

<a id="current-status"></a>
## Supported scope

The application provides `scid-research`, `scid-experiment` and an on-demand
instance UI. The research controller uses native scientific threads, explicit
instance roots, frozen inputs and finite budgets. Execution adapters remain installed
interfaces; scientific algorithms ship as selected Skill bundles. The old
non-execution registry and Operation host are retired. The [adapter API](PLUGIN_RUNTIME_API.md)
identifies the surviving contracts.
[Architecture](ARCHITECTURE.md) and the [CLI guide](ROOTLESS_CLI.zh-CN.md)
define behavior, permission and recovery boundaries.

Source, HTTP and synthetic-process checks establish only the behavior they exercise.
They do not qualify real native continuation, browsers, TCAD solvers, actual power
loss or cross-host recovery. Workers and same-user host processes are trusted.
[Security](../SECURITY.md) describes that soft boundary; [TCAD qualification](TCAD_QUALIFICATION_STATUS.md)
describes domain limits. A qualification decision applies to exact implementation,
inputs and environment. New code cannot inherit a historical verdict.

<a id="publication-scope"></a>
## Source publication

`scripts/build_git_release.py` is the explicit source-publication allowlist. It
copies current source, deployment tools, supported plugin trees, functional tests
and fixtures, current reference documents and selected developer tools. The plugin
trees include the optional `cov_execution` distribution; the core wheel carries no
Cov execution entry point. It normalizes
text and modes, scans the result, then writes `MANIFEST.sha256` and a deterministic
`.tar.gz` archive. An existing output requires an explicit `--force`; use a fresh
output directory when preserving a candidate.

Plans, audits, stage results, handoffs, superseded specifications and trial outputs
are local records outside long-term tracking and source publication. Functional
fixtures live under `tests/fixtures/` or `scripts/acceptance/fixtures/` and are
published with their consumers. Research databases, credentials, private datasets,
generated platform profiles and local agent instructions are excluded. Bundled
reference manuals have separate distribution restrictions; see [NOTICE](../NOTICE.md).
The release scan and checksum manifest do not establish redistribution rights.

## Checks and preservation

Select checks for the exact diff under the serial supervisor in
[tests/README.md](../tests/README.md). The explicit source-release check is
`tests/installation/check_source_release.py`; it checks files, references and the
manifest without installing the application or running the packaged functional
suite. Wheel packaging and installed entry points require their own selected
checks when affected. A source archive does not prove wheel completeness.

Run model, browser and solver checks only with their own scope, environment and
budget. Sampled RSS/time stops do not provide kernel-enforced aggregate isolation.
Keep verification evidence bound to the exact candidate and retain unverified
boundaries. Do not combine passing counts across revisions into whole-suite qualification.

See [installation](INSTALL.md) for publication, rollback and removal boundaries.
Frozen implementations retain their identities; uncertain submissions require
same-call reconciliation. Restore never resets consumed budgets or replaces
external effects with an earlier ledger snapshot.
