# SciDiscovery Agent

[简体中文](README.zh-CN.md) | English

Current platform support is Linux: the framework uses POSIX file/lock semantics and Linux namespaces. Native Windows is unsupported; WSL2 has not been qualified. Skill source binding and role selection are general contracts, while read-only namespace mounts are a Linux backend implementation.

SciDiscovery is a CLI-first scientific research framework with Codex and an explicit
agent-protocol backend. It runs H/W/A/E threads against an explicit objective and
frozen inputs, with finite shared budgets,
sealed scientific results and recovery of known calls. Scientific Workers own model
choices and conclusions; deterministic control owns identities, input bindings,
qualification checks, budgets and execution permissions.

Candidate metrics, audit receipts and availability are evidence for the scientific
author. Control preserves observed order and every candidate; A explicitly names
its delivery choice and explains it with bound evidence. Control supplies no
scientific ranking or default choice.

Local result `candidates` lists every retained model available to A; each member
requires a distinct exact retained artifact. Identity follows the original call and
artifact path, independently of list order. Measurements and scientific metadata may
be absent. Optional descriptions and comparison tables do not grant
or gate delivery. Invalid individual declarations retain their originals and
receive local diagnostics; previews link full retained content.

This is an engineering prototype. A successful command or solver run supports only
the claims established by its exact inputs, experiment and validation contract.

## What you can do

| Entrance | Purpose |
| --- | --- |
| `scid-research` | Prepare an instance; run, inspect, pause and continue research; close out results. |
| `scid-research start` | Start or reconnect to a detached controller for one explicit instance. |
| `scid-research fork-preview` / `fork-create` | Preview and create a paused branch from one exact historical logical task. |
| `scid-research instance-delete` | Permanently remove an exact quiescent instance; reconcile retries through its retained deletion receipt. |
| `scid-research serve-ui` | Optional webpage for the same instance controls and paginated sealed results. |
| `scid-experiment` | Use managed local experiments or a declared domain execution interface. |

The core installs the local execution interface. Cov execution, receipt handling,
public observation and delivery belong to the optional `cov_execution` plugin
(`SCID_PLUGINS=cov_execution`), with the stable executor key
`cov_execution:experiment`. The optional TCAD adapter provides detached solver execution through bounded
transport. Deterministic curve scoring and paper-figure extraction ship as
self-contained `curve-analysis` and `scientific-paper-evidence` Skills. The installer
publishes these and `sentaurus-tcad-code` into its persistent `skills/` directory.
Select Skill sets per role or named Worker in the [configuration reference](docs/RESEARCH_CONFIGURATION.md).
Instances freeze names and source bindings; native discovery reads the selected
installation directories in place through read-only mounts, without instance copies.
Plugins may offer optional sets; only explicit role/Worker selection enables them.
Skills grant no execution permission or scientific qualification.
The [execution adapter API](docs/PLUGIN_RUNTIME_API.md) owns the installed adapter contract.

## Install and start

Provide Python 3.10+ and the runtime/build dependencies declared in
[pyproject.toml](pyproject.toml), then install the application offline:

```sh
SCID_PYTHON=/absolute/python deploy/install.sh --dry-run
sudo SCID_PYTHON=/absolute/python deploy/install.sh install
scid-research --help
```

Installation publishes `scid-research` and `scid-experiment` and requires a fresh
installation prefix. Choose optional plugins explicitly with `SCID_PLUGINS`;
see [installation](docs/INSTALL.md) for dependencies, settings and removal.

To start research, copy [research.example.toml](research.example.toml), declare the
objective, public inputs, backend permissions and finite budgets, then use
`scid-research config-check --config research.toml` and
`scid-research prepare --config research.toml`. Inspect and start that exact instance
with `config-inspect --root /absolute/instance` and `start --root /absolute/instance`.
Add `--view current` to inspect the active configuration and any pending revision for the next new node.
The declaration affects new instances; existing instances retain their frozen settings.
See [configuration and backend reference](docs/RESEARCH_CONFIGURATION.md).
The webpage is optional and uses the same state and control logic; detached research
continues when its webpage service exits. The [UI contract](docs/ARCHITECTURE.md#instance-ui-control-and-display)
defines form retries, display caches and translation boundaries.
`result`, `materials` and `artifact` read or
download exact sealed results through the CLI. The [CLI guide](docs/ROOTLESS_CLI.zh-CN.md)
contains commands, policy examples, result retrieval and recovery rules.
A single `codex` launcher defaults to local experiments: the native host handles
ordinary-file submissions during W and returns responses automatically. Managed
local commands require a supported native permission projection; see the
[CLI guide](docs/ROOTLESS_CLI.zh-CN.md) for prerequisites and explicit unsupported
cases. External execution remains owned by its declared launcher.
Execution requires its own frozen configuration and authorization. Research
budgets, independent review and user statements do not grant execution permission.

A quiescent current-protocol instance can be forked at an exact logical step N;
its latest A need not have finished the research. The child reruns that task from
its frozen first-invocation inputs in a fresh session with explicit finite new
budgets. Exact selected code versions transfer into the child's own private Git;
current dirty/untracked code is copied as a writable draft. Unsealed handoff drafts
remain readable private entries, without becoming child execution facts or scientific
inputs. Scientific data and outputs remain direct references to their actual originals.
Removing the source Git does not remove transferred code; removing scientific originals
can make dependent uses unavailable. Creation leaves the child paused; explicitly use
`start --continue` to launch it. Unknown effects, frozen task boundaries, historical
investment and source restrictions remain binding; prior review or execution permission
does not authorize new subjects. See [fork semantics](docs/ARCHITECTURE.md#historical-task-forks)
and the [CLI workflow](docs/ROOTLESS_CLI.zh-CN.md#从历史任务创建分支).

Sealed nodes and their outputs stay at their existing locations for result viewing and
references. Independent archive packages and whole-instance cold checkpoint transfer
commands are retired. Historical packages remain read-only; they are not automatically
migrated, restored or reactivated. Ordinary execution checkpoints and original-call
recovery remain supported; checkpoints do not retain or restore lost drafts.

All H/W/A/E node token budgets and W stage targets are advisory: reaching a target requests a bounded conclusion without rejecting new managed experiments or forcing A review. Actual input plus output is fully accounted; no whole-node token allocation is prepaid. Current protocol 26 accepts only soft node settings and the frozen final-A completion policy. Earlier instance formats are unsupported; no compatibility reader or migration is provided. Shared budget checks for new nodes, finite execution time, permissions and cleanup limits remain enforced.

New instances freeze a final-A completion policy with one A per FINAL cycle: insufficient, zero or
exceeded shared token, time or step budgets do not deny the final A's admission,
completion or its own authorized delivery. Missing or unreconciled billing alone
also cannot block its verified terminal return or that delivery: unknown usage stays
unknown and is never replaced with a zero meter. A completed native event, zero exit
and confirmed cleanup remain required. Observed usage remains accounted without
raising the shared ceilings; original finite per-call time, permissions and other
execution limits still apply. After an explicit threshold increase, the next run may resume the saved pending normal route when its full admission is affordable and original effects are known and quiescent. New physical IDs preserve prior final outputs, submissions and all consumption; a later budget denial begins another FINAL cycle. See the [architecture](docs/ARCHITECTURE.md).

A candidate choice requires `selection_explanation`: its comparison criteria and
whether they changed. Revised criteria explicitly acknowledge the prior rule,
changes, locatable evidence explaining why, and accepted tradeoffs. Missing details
use the frozen same-session output repair, including FINAL with its original
completion protection and cumulative usage. Control checks completeness; A owns
scientific sufficiency. Planning without a candidate choice needs no explanation.
Standard `codex` CLI preparation defaults to at most two output-only repairs in
the original native home, preserving configuration, permissions and authentication.
Explicit policies take precedence; custom launchers and direct controller API
calls require an explicit repair policy. This default grants no failed-task or
scientific-work continuation.

Workers write ordinary scientific files and use the thin experiment submission and
wait interface. Their instructions add no hash calculation, comparison, verification
or hash inventories. Control retains exact path, producer and size references
privately under the [architecture's hash allowlist](docs/ARCHITECTURE.md).
By default, new W execution code belongs in `code/`, separate from the experiment
working directory; host-owned Git fixes the selected code before adapter preparation.
For an existing Git project, declare its read-only base and explicit editable,
scratch and output directories through the [project configuration contract](docs/RESEARCH_CONFIGURATION.md#declared-projects--声明工程).
See the [CLI examples](docs/ROOTLESS_CLI.zh-CN.md#受管本地实验) and [code contract](docs/ARCHITECTURE.md).

The operator evidence exporter keeps default omission metadata within 8 KiB and
links to complete scientific details in pages of at most 16 KiB. This limit applies
to omissions, not the entire evidence index. Details describe missing materials;
`not_available_in_this_view` does not provide the omitted originals. See the
[architecture](docs/ARCHITECTURE.md) for the reading entry points.

## Documentation and development

[Documentation index](docs/README.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Release scope](docs/RELEASE.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

The active controller lives in `src/scidiscovery/research_loop/`; execution adapters
live in `plugins/`, and deterministic scientific tools and guidance live in `skills/`. Use the [test lanes](tests/README.md)
with supervised, serial, exact targets. Functional tests do not build or install
packages. `scripts/build_git_release.py` creates a source distribution with a
per-file path and size manifest from explicitly selected source, current references,
tools and functional fixtures.

Development plans, audits, handoffs and trial outputs are local working records.
They are excluded from long-term source tracking and source publication. Current
usage and behavior are defined by the code and current reference documents.

## Boundaries

Workers and same-user host processes are trusted: control provides a soft boundary.
The default managed local subprocess and an explicit science launcher provide
their declared file views; generic native execution has no blanket physical-isolation
guarantee. Permission summaries separate requested settings from effective policy and
enforcement; unrestricted Codex cannot enforce offline intent, and protocol launcher
access remains unknown. See the [configuration reference](docs/RESEARCH_CONFIGURATION.md).
Browser behavior, real native continuation, TCAD solvers and power-loss recovery
need evidence for the exact candidate and environment. Source tests do not establish
that qualification. Full cross-host disaster recovery is outside current support.
Frozen instances retain their implementation identity. Unknown external submissions
must be reconciled by their original identity before further action.

The supported application entrances are the two commands above. The old `scid`,
Root/MCP application, generated platform roles and legacy systemd application
installation are unavailable. See the [distribution notice](NOTICE.md) for software
licensing and bundled-reference restrictions before sharing a source release.
