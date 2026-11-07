# SciBranch execution adapter API

The supported extension seam is the installed `scidiscovery.execution_interfaces`
entry-point group. Core supplies `local_execution:command`; optional packages
supply `tcad_execution:tcad`, `cov_execution:experiment`, and the independent
[`harbor_execution:experiment`](../plugins/harbor_execution/README.md). The old
`scidiscovery.plugins` registry, Operation catalog/compiler, Artifact host,
component finalizers and Worker-tool hosting are retired.

Scientific guidance and deterministic analysis belong in ordinary Skill bundles.
A Skill does not register an executor, qualify evidence, grant execution permission
or bypass exact original-call recovery. See [configuration](RESEARCH_CONFIGURATION.md)
and [architecture](ARCHITECTURE.md).

## Registration and configuration

An entry point returns `ExecutionInterfaceRegistration` from
[`operations.execution`](../src/scidiscovery/operations/execution.py).
Its identity is the exact `plugin_id`, `version` and selected `plugin:binding`.
`factory` is a `RuntimePluginFactory` from
[`operations.runtime_plugins`](../src/scidiscovery/operations/runtime_plugins.py).
These legacy module paths contain narrow execution contracts only.

`configure(configuration, source, binding=...)` optionally normalizes operator
configuration without contacting a transport or constructing an adapter.
`requires_config_file` rejects inline configuration when separate configuration
is mandatory. Relative paths are resolved against that explicit configuration
file. Host identities, gate services and admission facts cannot be supplied in
operator JSON. The configuration reference owns public error behavior.

The Local and Cov factories accept optional `python_requirements` in their
existing configuration object. Their configure callbacks check declared installed
distributions and imports in the Python selected by `run_argv[0]`; they never run
the task or construct an execution adapter. See the
[declaration and bounds](RESEARCH_CONFIGURATION.md#scientific-python-dependencies--科学-python-依赖).
Other providers own any equivalent environment preflight. Retained result, query
and recovery paths do not recall configure or requalify an environment.

`research_defaults(configuration, *, root)` may return only `history_observer`,
`delivery_adapter` and `optional_skill_sets`. It is called for the explicitly
selected `executor.config.executor`, not every installed provider. Optional Skill
sets use the ordinary configuration shape, for example
`{"optional_skill_sets": {"analysis": {"items": ["curve-analysis"]}}}`. Names must be
simple directory names; each set has only an `items` array without duplicates.
A same-named explicit set must have identical members. Offers grant no activation:
a role or explicitly named Worker must select the set through `skill_set`.

Skill bytes remain in the actual CLI installation's single `skills/` directory;
this callback supplies no source root, asset bytes, native permissions or runtime
registry. Prepare retains expanded names/source bindings, and ordinary retained
execution/resume does not recall defaults. Fork preparation preserves that frozen
selection and ignores later optional offers, while still checking the provider's
defaults scope and observer/delivery gates. Bare wheels require separately published
installation assets. See the [configuration contract](RESEARCH_CONFIGURATION.md#selected-framework-skills--按角色选择框架-skill).

`factory.build(RuntimePluginContext(...))` returns immutable
`RuntimePluginContribution.execution_adapters`. The context provides exact config
bytes, an adapter-owned state root, the frozen execution interface and optional
nonserialized `ExecutionHostServices`. No tool-service registry, Run host or
archive-provider callback is supplied.

The nonserialized host gate binds the original task, exact implementation, budget
and permission. Its JSON description is not a capability token. An adapter cannot
turn a missing gate into permission; startup/configuration success is not admission.

## Execution and recovery

`ManagedExecutionInterface` defines preparation, submission, original submission
lookup, status, cancellation, collection and retained result projections.
`LifecycleCapabilities` explicitly declares query, cancel, collect, same-original
resume, lookup and atomic-admission support. Missing declarations grant no operation.
Unsupported lifecycle operations must fail before transport work.

`LocalFileDescriptor` binds name, exact local original path, byte count and media
type. It has no content digest. Adapters retain those references and complete native
terminal evidence under the [architecture's hash allowlist](ARCHITECTURE.md).
Known process success is an engineering fact. A cancelled native state remains a
known engineering failure; neither state establishes scientific qualification.
Pending native states remain pending; uncertain submission is unknown.

Host preacceptance budget refusals are application feedback, not native execution
states. Only the trusted host gate and a proven side-effect-free refusal produce
`accepted=false` with `budget_exhausted` (`shared_time_exhausted` or
`shared_budget_exhausted`) or `budget_unavailable` (`shared_usage_unknown`). The
latter preserves unknown usage instead of inventing an exhausted or zero balance.
`handoff.action=finalize_response` requests normal scientific JSON reporting the
unperformed work, retained evidence and unmet goals. It creates no waiting call,
wake permission, retry or new allowance; the host applies its original control
rules. Formal review remains `review_required`. Uncertain provider outcomes,
uncertain external preparation and accepted original calls preserve their existing
outcome and identity; exception text alone never establishes nonacceptance. Public reasons are
fixed host codes; arbitrary exception text remains in private diagnostics.

For new W code, the host captures the selected `code/` bytes in instance-private
Git before `prepare` and binds the original call to a seven-field `code_version`
(`version`, `object_format`, `commit`, `tree_path`, `selection`, `entry`,
`logical_root`). This binding is host-owned: adapter configuration, preparation
results and acceptance metadata cannot supply or alter it. An adapter consumes the
fixed export descriptor; the export is temporary and must not be promoted to a
permanent scientific original. Supported launchers overlay the whole selected
logical code directory, including for a single-file selection, so draft siblings
cannot supply uncaptured code. Local's independent experiment cwd and outputs do
not enter Git. Same-call query/result/collect/cleanup do not require a deleted
export; actual code consumption still requires the retained commit and original
call pin. Code-bound compiler records use `code_version`; current opaque scientific
packets retain their exact author-input binding and UUID identity. The old public
author-client bridge and old fork compilation-order metadata are not supported. Ordinary host execution fingerprints do not change
`LocalFileDescriptor`, global scientific references or qualification contracts.
Authorization, consumed budgets, deadlines and recovery authority remain unchanged.

An implementation that consumes the complete selected logical code directory
declares the existing `AdapterCapability` key `frozen_code_directory`, using
`experiment_client.frozen_code_directory_capability()`. Core freezes and checks
that declaration before selecting or admitting directory inputs. Local and
external implementations use the same capability rule; a provider name does not
imply support. This capability does not grant Local's `--experiment-dir` runtime
semantics or change the original permission, budget, or code-version owner.

Control retains the original call identity, consumed budgets, prepared submission,
external binding, terminal facts and exact collection. Lookup or reconciliation
must reuse that identity. Unknown submissions must never be automatically renamed
or resubmitted. Explicit retirement only resolves a missing original; finding the
original job binds it and returns its state. Cancellation is a separate explicit
operation. Resource accounting uses observed facts and preserves unknown dimensions.

The optional `scientific_outputs`, `scientific_source`, `context_validator`,
`research_defaults`, `engineering_feedback`, `legacy_retirement` and
`projection_contract` fields are pure, declared projections over exact retained
facts. They grant no independent control authority. Scientific readers consume the
frozen projection; they do not re-query a backend to reconstruct historical facts.
The optional local result/candidate protocol is explicitly frozen; arbitrary result
fields do not opt an implementation into that protocol.

Protocol 33 keeps W as the research main line. `continue` resumes the same W;
`handoff` seals and starts a new W, `revise_h` and `review` request serial H/A
consultation returning to a new W, and `decision` requests an ordinary A assessment.
These are jobs under the existing single writer, not nested same-thread calls.
H/A consultations retain the requesting W's selected implementation inputs and
materials; consultant `implementation_selection` does not alter control selection.
The [architecture](ARCHITECTURE.md#ownership-and-flow) owns the exact handoff rules.
Window use is one initial window plus completed compactions, against the frozen
one- or two-window allowance. Same-session resume preserves counts and deduplicates
accepted compactions. A completed compaction beyond the allowance seals a partial
W at the next supported observable boundary, without automatically invoking A;
occupancy percentage and peaks do not control handoff. Required `PostCompact` and
`PostModelRequest` hooks feed small control facts to the existing native helper;
native stdout carries these and bounded assistant text, without tool/input/output
body observation or private-rollout scans. Unavailable required hooks are explicitly
unsupported before launch, rather than repeated fresh W attempts with unknown
coverage. This contract concerns the supported launch path, not arbitrary backends.
Existing owners retain time and original-call state. New W contexts preserve global
active time, tokens, steps, consumed execution, permissions and unknown-call identities.
Original calls must be reconciled through their existing paths.

Scientific originals, arrays, artifacts, scripts/code and logs have no framework
per-file or aggregate byte quota. Legacy output/file/total/instance/log/result byte
configuration remains accepted and frozen as inert file-quota syntax, without UI
file-size controls. Compact handoffs, declared-result documents, delivery manifests,
control metadata and public views retain their document/view bounds. Exact
`size_bytes`, changed-original and actual-truncation checks, file/entry counts,
finite IO/execution windows, path/symlink safety and explicit permission remain.
Transfers use chunked IO; this does not promise unlimited RAM or host hard isolation.

H and W may register mechanisms through existing declarations. Their exact
`source_step` and `source_role` are retained; a W declaration becomes executable
only after a following W freezes the new catalog and passes admission; the current
W cannot execute that declaration against its old catalog. H/A consultation is
optional, without a registration prerequisite. Ordinary A consultation neither
consumes an assessment cycle nor grants candidate recommendation or qualification.
Ordinary A answers the W-authored high-level assessment question; an explicit W
assessment request alone does not classify retained work as incomplete. Further
research requires a bounded decision_request. A planning/coverage text does not
change W focus, dispositions or grants, and no full inventory is mandatory.
Mechanical identity/interface checks stay with control/W. Direction-round grouping,
exact-call counting, A coverage and due supervision belong to the common accepted
ledger/controller; launchers do not maintain separate counters. W can declare
`exploration_rounds`; a scoped macro A returns `supervision_result` and may explicitly
continue existing work without inventing a new decision request. The public research
view exposes the shared counts and due reasons. Only A decides scientific finalization
or delivery; adapters execute its exact authorized choice and report actual outcomes.
Formal independent review retains its neutral exact-subject contract. Protocols
29–32 require their matching old runtime; current readers reject them without
migration. No adapter capability, permission or lifecycle operation is inferred
from consultation or context renewal.

Shared `lineage.indexes.research` navigation is produced once with the accepted node and
links existing authored science and candidate/work indexes. Full originals remain
readable and excerpts disclose omissions; no adapter must populate a new ledger,
hash scientific data or supply additional mandatory scientific fields. Scientific
ranking, confidence and qualification remain authored judgments. Private control
paths, budgets, cursors and tokens do not enter scientific research pages.

## Native views supplied by a provider

A trusted installed registration can declare `native_launcher_module`, the exact
Python module implementing selective native Skill loading and the final scientific
read-only namespace. The generic loader
resolves a unique declaration through the existing execution entry points and
freezes provider/interface/module identity in `native_capabilities.native_launcher_contract`,
including when no Skills are selected. Protocol 33 keeps this registered launcher
on the host for preparation and its original submission service; its final scientific
launch must apply the shared-input read-only mounts after all writable mappings.
Other launcher argv retain the generic final-process protection. This declares native loading capability;
it does not select a scientific execution backend or enable external execution.
Conflicting or changed frozen bindings fail before model launch. Unmatched argv
retains generic protection; selecting Skills still requires a supported Skill
loader contract.

The supported module invocation is `python [-B] [-I] -m <declared-module>
--config <exact-operator-file> [--]`. A copied script or arbitrary wrapper cannot claim
support through a prompt or a JSON flag. A declared provider implementing this
contract obtains verified `skill_supply.native_mounts(root, job)` on the host,
mounts only the current role's discovery directory and selected installation
directories read-only in place, and grants the corresponding native file reads.
No whole Skill library, private manifest or unselected objects are supplied.
Only Skill schema v3 installation bindings are accepted; older manifest/object
formats are rejected before reading assets. Current forks retain the exact
installation binding without copying or hardlinking Skill bytes.
Scientific backend subprocesses receive no Skill mounts. Native permission settings
retain their declared semantics; this selection does not suppress other native
user/system/plugin sources or establish a same-user security sandbox. Standard
Codex retains its frozen native home and authentication. The Cov adapter no longer
declares or ships a dedicated native launcher; its executor, observer, delivery
and same-call recovery remain separate contracts.

Git-bound Local artifacts publish
directly at existing `work/retained-experiments/<call>/outputs`, with in-place sealing
only after actual writers stop and read access through the existing read-only dynamic
root. Repeated collection, observation, checkpoint and history views preserve first
destinations and saved proofs; actual content consumption strictly verifies selected
originals without copying or refreshing them. Checkpoints do not seal current drafts.
Ordinary catalogue/status/provenance reads check retained controls without reopening
unselected scientific bodies or Git pins. Output errors preserve known
terminal status. Current standalone Local calls without `code_version` retain
their declared input contract. External global/declaration and cross-node inputs use
their exact actual originals through existing logical read-only mappings, without
node body copies or external-source chmod; a host file alias need not exist. History
bodies reuse their first existing destinations. Private `file_stat` in
`seal.materials` rows is verified for selected use before returning the unchanged
public material fields. Code audit views retain the original compiler reference, UUID and seven-field
`code_version`; only selected use materializes an export. After launch, cleanup
requires confirmed native, bridge and applicable backend shutdown; unknown retains it.

Cov keeps the existing `runs_root/model_run<run_id>.pkl` original after exact
call/run/receipt checks and retains its single-script entry contract. Cov publishes
complete bounded result rows and host receipts exclusively. Same-call recovery may
publish a complete pending host receipt only when its frozen acceptance, stopped
terminal and original ledger agree; it never reruns the experiment. Native terminal
facts survive output-publication failure: unavailable scientific output carries no
candidate, run or model qualification. A raw backend row alone is not a host receipt.
TCAD command
collection first writes native scientific outputs, the existing scientific `tcad_log`
and facts at the existing node/call output directory. Manifest, transport-derived
files, bindings and transfer spool stay private. New socket-backed preparation is
unsupported before admission or submission; exact old-call queries remain supported.

Original-call recovery validates retained control relationships without reopening
unselected scientific bodies or requiring a removed export. A retained terminal
observation permits idempotent cleanup of that exact call's export after the existing
shutdown checks; cleanup does not dispatch work or reset consumed budgets. Unknown
native, bridge, backend or replay effects retain their original reconciliation boundary.
A Git capture/export interrupted before any preparation binding or accepted record
remains preserved; missing records do not prove that cleanup is safe.

Fork transfers exact selected code objects into child-owned private Git and copies
necessary typed controls and current code drafts. Unsealed handoff drafts remain inert
private entries, without child native facts, seals or grants. Scientific bodies keep
their declared original references and are checked at actual consumption. Deletion
checks stopping evidence from necessary controls, then removes only the exact instance's
own Git, exports and directory through the original deletion operation.

Sealed nodes remain the result/reference source. Independent archive and whole-instance
cold checkpoint package APIs are retired; historical packages stay read-only without
automatic migration, restoration or owner reactivation. Ordinary checkpoint capture,
validation and original-call recovery remain; checkpoints do not retain or restore
lost drafts.

Focused source and synthetic checks do not establish complete live validation of
default native, registered Cov, Codex/namespace, replay/model, solver or SSH paths.
Those paths need their own exact independent CLI evidence. Source support alone
establishes neither scientific qualification nor a real sandbox guarantee.

## Deterministic scientific assets

The persistent source bundles are
[`curve-analysis`](../skills/curve-analysis/SKILL.md),
[`scientific-paper-evidence`](../skills/scientific-paper-evidence/SKILL.md) and
[`sentaurus-tcad-code`](../skills/sentaurus-tcad-code/SKILL.md). Ordinary scripts
operate on explicit input files without Operation, Artifact or Run hosting.
Dependencies and CLI preflight belong to each bundle; core does not require Pillow.
Curve unit/operator/support/numeric checks and figure identity/calibration/ambiguity
checks remain executable algorithms. Explicit tool-internal evidence processing is
separate from control's hash allowlist. Their computed output is evidence for Workers;
control does not manufacture a scientific verdict from it.

## Delivery input ownership

Delivery adapters declare `readonly_inputs_version = 1` in their frozen adapter
descriptor. Candidate staging keeps the existing wire version 2 and `object_refs`;
zero-byte mountpoints receive read-only binds to exact selected originals. Launchers
must preserve read-only input access and provide separate writable scratch.
Resolve, smoke, audit, replay, promote and confirm must not chmod
or modify an input inode. Unsupported mapping fails before dispatch without a copy
fallback. Built-in launchers retain their read-only input mounts. A final requested
writable delivery artifact may be independently materialized; this is an explicit
output, not a repeated copy of the entire input stage. Native permissions and
same-original unknown-call reconciliation remain unchanged.

Cov confirmation may also read the exact final operation marker's
`.rootless-pending` sibling. It confirms only the original operation, candidate,
artifact, audit and both current target generations together; confirmation does not
write, remove pending files or repeat promotion. Partial or mismatched publication
remains unknown. Promotion refuses any of its four existing pending targets before
writing a target. Earlier partial continuation/cancellation is unsupported when
those files lack exact operation ownership; filename or age does not establish it.

## Retained transport leaves

`plugin_runtime.collection` provides finite-deadline process/transfer IO;
`plugin_runtime.transport` provides bounded Unix socket and JSON-RPC transport;
`plugin_runtime.workspace` provides atomic engineering JSON records;
`plugin_runtime.diagnostics` provides redacted error facts. These helpers own no
registry, scientific qualification or retry authority.

TCAD retains the independent daemon/socket and SSH command transport. The unused
`tcad-control-mcp` stdio console is retired; socket JSON-RPC remains available for
exact old-call queries under the preparation limitation above. See the
[TCAD transport contract](tcad_transport_contract.md) and
[qualification boundary](../plugins/tcad_artifact/README.md#qualification-boundary). Installed transport discovery,
source tests or successful installation do not qualify a real solver or remote host.
