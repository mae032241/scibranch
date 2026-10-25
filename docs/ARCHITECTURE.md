# Current architecture: thin H/W/A/E control

[简体中文](ARCHITECTURE.zh-CN.md) | English

Control does not hash scientific data or outputs by default. Hashing is restricted
to approval bindings, small control requests that require durable idempotency and
host-owned Git code capture. Declared project bases use ordinary Git worktree
checks, without control reading or hashing every base file. Approval/idempotency helpers must
not traverse scientific files, code, logs, observations, inputs, results, Skills or
archives. Explicit scientific tools may use their own algorithms
on selected evidence; that is separate from control identity. Protocol 26 uses
`record_ref = {"path": "/absolute/original"}` and
`file_ref = {"path": "/absolute/original", "size_bytes": N}`. Control bindings use
`*_ref` fields; paths, producers and lifecycle identities replace content digests.
Legacy retained object paths use UUIDs, without content deduplication or hash caches.
Fork manifest v2 retains this reference contract. Independent archive and whole-instance
cold checkpoint packages are retired; historical packages remain read-only without
automatic migration, restoration or owner reactivation. Earlier protocol originals
are preserved without migration. Read-only Linux mounts protect selected
inputs in the scientific namespace; trusted same-UID code remains a soft boundary,
without protection against deliberate same-UID rewrites or content-tamper detection.

`scid-research` owns explicit research instances; `scid-experiment` records managed experiments.
There is no LLM Root, MCP scheduling service or background Root daemon.
The core exposes the thin CLI and instance UI; the old CLI/MCP/UI, Root host,
platform-profile generator and legacy systemd deployment are retired.

`research_config.py` owns the shared TOML, CLI and host preparation, defaulting and validation path. New instances freeze effective settings; editing a declaration does not amend an existing instance. `backends.py` separates Codex and explicit protocol transport from scientific judgment, budgets and execution authority. Protocol permissions are launcher hints, not isolation; native resume and complete authorship attestation are unsupported, so formal independent review is unavailable. Enabling subagents also establishes neither complete authorship nor complete usage. See the [configuration and backend reference](RESEARCH_CONFIGURATION.md).

Frozen permission summaries distinguish requested and effective policy, enforcement and limitations. Standard Codex danger-full-access cannot enforce offline intent; read-only remains offline despite a network request. Protocol/custom-launcher access remains unknown with hint-only declarations.

The CLI is the application core; the optional webpage uses the same public controls and read views. `control_operations.py` records durable detached advancement and scientific budget amendments. The independent controller retains the original writer lock and survives webpage service exit. Pause prevents the next node while accepted work finishes under original limits. Amendments apply at safe boundaries without resetting consumption or unknown usage; unknown original calls require reconciliation, never automatic resubmission.

`user_guidance.py` binds global or role-specific advice to the next matching new task or subsequent research. Pending revisions can be edited/revoked; applied task inputs are immutable, with future research revocation recorded separately. Advice may be declined. Explicit goal changes preserve the original objective record and name an effective node; they grant no execution permission or scientific qualification.

`configuration_updates.py` stores operational revisions separately from the original settings identity. A new node reserves an exact revision before constructing its inputs; existing nodes and their original calls retain their own settings for validation, execution and recovery. Saving queues a revision without launching work. Cumulative budget and resource ledgers, pending execution facts and FINAL barriers survive configuration changes. The configuration reference describes editable fields and the distinction between active and pending settings.

## Ownership and flow

```text
Operator objective + inputs + configuration + finite budgets
  -> CLI prepare: freeze instance settings
  -> controller: instance writer lock, budget, pause and role routing
  -> native: fresh H / W / A / E threads
  -> sealed scientific JSON and exact materials
  -> A scientific direction -> controller

W -> scid-experiment -> frozen interface -> local / domain adapter
                                    -> original-call query / cancel / collect
CLI serve-ui -> same instance read views and control commands
CLI original-call recovery -> same-call reconciliation and bounded maintenance
```

H owns hypotheses and mechanisms; W owns bounded experimental design, implementation
and observations; A owns contribution, evidence gaps, direction and delivery selection;
E owns requested evidence work. Control owns immutable bindings, budgets, qualification,
authorization and mechanical publication. It does not manufacture science or ask models
to perform extra hash calculation, comparison, verification or prepare hash inventories.
Workers write ordinary scientific files; control records their exact paths, producers
and declared sizes privately on the host under the reference contract above.

Local result metrics retain distinct nonempty names and explicit finite numeric
values. `unit`, `evaluation_key`, `direction` and labels are optional scientific
descriptions: control imposes no optimization enum or scientific text-length limit.
The optional version 1 result `candidates` array declares all retained models
available to A, each bound to a distinct exact declared retained artifact. Candidate
identity includes its original scope, call and artifact path; reordering the list
preserves identity. The complete catalogue lets A choose any member for exact
delivery, with no control ranking, default or inference from other outputs. An
empty or absent list creates no candidate; the old single `candidate` field is not
interpreted. An absent or unresolved metric stays unknown, without selecting another
metric or inferring a direction. Invalid individual metric/output declarations and metadata
that cannot enter strict JSON receive local diagnostics while other valid results
and artifact-bound candidates survive. Original `result.json` bytes remain unchanged;
compact previews and omitted-field diagnostics refer back to that retained file.

With H search commitments, `prepare --candidate-extra-slots N` freezes the W
nomination allowance (default 2). W's scientific `candidate_nominations` response
declares one representative or an explained gap for each unwaived mechanism in
the frozen task scope, plus at most N distinct extra alternatives. Local W tasks
cover only their assigned mechanisms; screen retains the global scope.
Each representative names the actual H mechanism reference, exact registered
candidate and scientific reason; sharing one model across mechanisms needs an
explicit reason for each. Current-call originals use `{call_id, artifact}`;
historical candidates use their existing scientific references. Control checks
identity, declaration coverage, duplicate extras and the allowance, without
inferring mechanism membership from a call, ranking or judging scientific truth.
No usable model is an explicit gap and does not prohibit partial or final results.
Only A's explicit waived disposition with a scientific negative or infeasibility
reason exempts a mechanism; relative underperformance alone calls for deferral.

The latest sealed W declaration for each mechanism supplies the comparison
preview and paginated nomination index with its own original source. Local
returns retain other mechanisms' prior representatives and gaps; new mechanisms
remain pending without a synthesized global judgment. Extra alternatives come
from the latest W declaration. The
retained candidate pool, experiments and exact materials remain in their existing
paginated indexes and delivery catalogue, without a nomination count limit.
A may inspect and select any registered model using the existing recommendation
and selection explanation; this neither changes the sealed W declaration nor
requires another experiment or registration. Tasks without H search commitments
do not invent mechanism coverage.

Scientific commitment and disposition lists have no fixed four-item or 128-mechanism
capacity. The configured initial-call opportunities, exact extra-call grants,
accepted-call consumption, finite execution budgets and unknown-call reconciliation
still govern execution. Paginated indexes retain all mechanisms and their original
scientific text; the compact navigation view remains bounded.

`screened` means A assessed the exact W evidence, without excluding that mechanism
from later improvement. `deferred` preserves an unresolved route for later work;
`waived` is A's explicit scientific exclusion. A must explain each exclusion with
evidence supporting a negative or infeasibility judgment against the original goal
and constraints. Temporary relative underperformance or missing evidence supports
deferral rather than scientific rejection. H compares plausible mechanisms before
concentrating and explains any single-mechanism scope; parameter trial counts alone
do not establish evidence across mechanisms. Control does not grade these reasons.

Mechanism indexes retain the adopted A disposition reason as a bounded preview and
link the complete original scientific JSON, outside navigation page size limits.
Same-status restatements and omissions preserve the adopted reason and its source;
an H revision clears the current disposition while retaining its historical original.
Relevant unexcluded routes remain available through the complete index, including
screened routes. Every search instance shows the complete portfolio to H/A by default,
including actual accepted-call counts, initial and extra opportunities remaining,
completed independent W→A investments and unserved completed cycles. These are
observed activity, not scientific coverage, value rankings or per-mechanism resource
costs. Stable identity order is navigation; local improve/repair/validate work does
not erase the global view. Explanations and H revisions do not reset investment.
All unexcluded routes, including screened, accrue unserved cycles; only waived routes
are excluded. `search_policy.initial_calls` configures initial registered-call
opportunities. The optional portfolio interval requests a reminder only: no
portfolio_review form, forced route switch, result-sealing or FINAL completion gate.
A explains comparisons, concentration, postponement and exclusion in its existing
scientific result and route reasons; control does not judge their sufficiency.

Budget choices use current ordinary-work availability and the runtime
`work/budget-status.json`. With protected FINAL, provider-reported consumption
determines the token balance; missing billing remains unknown without an estimated
charge or an old closeout reserve. Unresolved execution still requires exact-call
reconciliation. Older bounded-closeout instances retain their frozen reserves. Call
opportunities do not reserve resources or guarantee coverage. Bound expensive calls
with the existing execution configuration and W stage targets; no automatic breadth
share, minimum mechanism count or new execution permission is introduced.

H revises only changed commitments; omitted commitments and their adopted A
reasons remain. Repair binds the stable mechanism to one exact direct W observation,
or to an exact original accepted input selected through `implementation_selection`.
Multiple possible observations require an explicit exact selection; equal output
bytes, labels and recency cannot select the source. Shared archive paths
cannot prove logical input identity; such outputs cannot disambiguate a repair.
The old W, call, accepted and
terminal originals keep their own H seal; the current H/A authorization is frozen
separately. Known process success can still require scientific repair. Unknown
execution, missing or changed originals, ambiguity and exhausted repair allowance
remain barriers, reported in the author's control outcome before a dependent W.
No old screening qualification, spent opportunity or budget is transferred or
refunded. Within unchanged H scope, A may use `continue_w` and an explicit local
focus to request the next finite W stage after review. Prior stage usage remains
consumed; H revision changes scientific commitments and cannot reset budgets.

Each task freezes the auxiliary fields its configured consumers actually use.
Unconsumed auxiliary fields are absent from the required native response contract;
extra scientific content remains in the sealed original. An enabled optional field
with an unreadable shape supplies a local diagnostic and no inferred progress or
scope. Explicitly malformed W findings go to A as partial evidence without a
scientific screening proof; missing or valid-but-incomplete findings retain the
existing mechanism-coverage checks. Actual action, final-only routing, input
selection, independent qualification and execution permission remain explicit.
For input selection, null retains, `[]` clears and a valid list replaces the exact
bound objects; an unresolved list is never partially applied or silently inherited.
A registered candidate and its exact sealed implementation may share a readable
choice reference when their call/output/material bindings match. Equal bytes from
another source do not merge identities, and an arbitrary implementation does not
become a candidate.

New jobs freeze a public `configured_delivery` description from the configured
adapter's optional `effect_scope` (nonempty public text, without a fixed length cap).
Undeclared custom scope is `unknown`; control does not infer effects from launcher
names. The built-in directory adapter only copies and confirms already retained
declared files; it does not train or perform task-native submission or scientific
audit. This description grants neither permission nor qualification. W preserves
the original task's necessary deliverables and native receipts in existing declared
outputs before temporary work disappears. A compares those results and the actual
adapter scope with the task requirements, reports gaps, and may deliver the best
existing partial result, including in FINAL. Coverage adds no completion gate.

Managed call records use `host-runtime/experiment-control/<step>/records`, keyed
by the exact task step rather than a job-content hash. `exp` is a thin submission
and wait helper using one ordinary `submit.json`/response slot. For local-mode W
scientific views, the existing native
host services that slot for the native attempt's lifetime and writes responses
automatically. External mode leaves service ownership with its declared launcher.
Neither path adds an agent, daemon, UDS or FIFO.

Without `[project]`, new W execution code belongs in the current scientific workspace's `code/`, with
data, logs and outputs outside that directory. Local
`--experiment-dir experiments/<name>` selects an independent runtime working
directory and captures the whole `code/` tree; `--script code/<name>/run.py` selects its entry. Without
`--experiment-dir`, only that script is captured, including for Cov. Stop editing
the selected code during capture. Capture accepts ordinary files and real
directories, rejects symlinks and special files, and defaults to 1 MiB per file,
50 MiB per tree and 4,096 files. It does not capture the experiment working
directory, scientific data or execution outputs.

Before `adapter.prepare`, the host pins the exact original call in an
instance-private Git repository and retains the seven-field `code_version`:
`version`, `object_format`, `commit`, `tree_path`, `selection`, `entry` and
`logical_root`. This is a narrow code-identity exception to the hash rule, not
scientific qualification, execution permission or a change to data/output identity.
Adapters cannot supply or alter this host binding. An export is a temporary
read-only execution view, not a permanent scientific original. The supported
launch path overlays the entire selected logical code directory with the fixed
export; even a single-file selection hides mutable draft siblings. The independent
runtime working directory and its `artifacts/` remain writable.

Same-call query, result, collection and cleanup do not require an export that has
already been removed. Actual code consumption requires the retained commit and
its original call pin, and materializes that version without falling back to a
mutable draft. New compiler records use `code_version` without an `author`
snapshot and retain their original UUID identity; pre-Git records retain their
saved interpretation. Code audit views retain the original compiler reference,
UUID and seven-field `code_version`; only selected consumption materializes an
export. After launch, export cleanup waits for confirmed native, bridge and
applicable backend shutdown; unknown cleanup retains the selected export. Ordinary
private `file_stat` fingerprints supplement path/size checks, including
`seal.materials` rows after writers stop. Selected material verification removes only that private
field when returning the unchanged public material or qualification reference.
Original authorization, budgets, deadlines and unknown-call reconciliation remain
unchanged.

New Git-bound Local artifacts are written directly at the existing
`work/retained-experiments/<call>/outputs` path and read through the existing
read-only dynamic root. These Local and node-owned raw/artifact originals are sealed in place only after
actual writers are confirmed stopped. Repeated collection, observation, checkpoint
and history views preserve first destinations and saved proofs; actual content
consumption verifies the selected original without copying or refreshing it.
Checkpoints do not seal current drafts. Fragment checkpoints neither retain draft contents nor
restore deleted or lost drafts; continuation depends on drafts still present in the existing workspace.
Required selected-control checks and saved older records
remain, and output errors do not rewrite a known terminal outcome. Local calls without
`code_version`, including the old `entry` branch, retain their existing copy behavior.

External global/declaration inputs and cross-node inputs resolve their exact actual
originals and are mapped read-only at existing logical task paths, without node body
copies or chmod of external sources. A logical public path need not have a host file
alias. History bodies reuse their first existing destinations. Ordinary catalogue,
status and provenance reads validate retained control relationships without reopening
unselected scientific bodies or Git pins; actual selected consumption remains strict.
Delivery retains wire version 2 and `object_refs`, with zero-byte mountpoints overlaid
by read-only binds to selected originals.

Cov retains its existing `runs_root/model_run<run_id>.pkl` original after exact
call/run/receipt checks and keeps its single-script entry contract. New TCAD command
collection first writes native scientific outputs, the existing scientific `tcad_log`
and facts into the existing node/call output directory. Manifest, transport-derived
files, bindings and transfer spool remain private. New socket-backed preparation is
unsupported before admission or submission; exact old-call queries remain supported.

Original-call recovery checks retained control relationships without reopening
unselected scientific bodies, temporary exports or current code drafts. Actual code
consumption still verifies the original retained Git version. Existing or newly
recorded terminal recovery uses the same call's saved observation for idempotent
export cleanup after the original shutdown checks. This does not dispatch work,
allocate a new scientific allowance or reset consumed budgets. Unknown native,
bridge, backend or replay effects preserve the original reconciliation boundary.
A capture/export interrupted before any preparation binding or accepted record is
preserved: absent records or empty directories do not prove safe cleanup.

The fork and deletion rules below describe their source behavior. Real default native,
registered Cov, Codex/namespace, replay/model, solver and SSH validation is deferred
until G3 and an independent user CLI run. Source changes establish neither scientific
qualification nor a real sandbox guarantee.

The default local command backend resolves the selected Codex installation's native
binary and uses its sandbox with a restricted scientific subprocess file view. The
accepted Git code export is read-only at its logical scientific code directory;
pre-Git calls retain their saved directory/script snapshot behavior. The backend
receives runtime files, the scientific workspace and a task-native permission
configuration, without native authentication, provider configuration, control
originals or the submission slot. The native Worker
and backend honor the same supported legacy `workspace-write` declaration, including
declared network access and temporary-directory exclusions, interpreted equivalently
for their respective working directories, `TMPDIR` values and namespaces. The
native Worker retains `--sandbox workspace-write` and the captured legacy
configuration overrides; only the backend sandbox uses an internal permission
profile that expresses the same declaration in its namespace. This requires Linux,
`bwrap` and a resolvable native Codex binary.
Named/default permissions, selected configuration profiles, managed policies,
additional writable roots and custom launchers whose backend permissions cannot be
faithfully projected yield explicit unsupported managed launches; ordinary native
permissions are not replaced to make them run. The internal permission profile adds
no operator setting. These file views do not establish hostile same-user process
isolation or a blanket guarantee for generic native execution.

**Declared project views.** `project_layout.py` owns strict project declarations,
frozen base Git identity and bounded tracked-file/worktree validation. The
[project configuration contract](RESEARCH_CONFIGURATION.md#declared-projects--声明工程)
owns its supported fields and permission limits. The original base path exposes
only verified HEAD ordinary files read-only, without `.git`, untracked/ignored
files, a base-data copy or an overlay filesystem. Its SHA-1/SHA-256 HEAD identity
is immutable across configuration revisions and forks. HEAD/index consistency is
checked globally; ordinary Git dirty checks and source/parent stat race checks
apply to visible base sources. Git filters and index optimization flags retain
their normal semantics. W and runs exclude editable/scratch/output roots from
those source checks; read-only roles exclude scratch/output roots. Missing or
dirty visible sources affect only later consumption that needs them, not known
terminal records or metadata-only queries.

Project draft exposure is limited to declared editable code. Initial tracked-code seed and a
uniquely selected retained parent commit use existing shared code limits;
ambiguous or absent parent selection reports a frozen-base fallback. Existing
jobs keep their current drafts. A local project run captures all editable roots
into one `code_version` rooted at `project`, then mounts that exact export
read-only; empty roots use read-only tmpfs. Scratch roots are temporary tmpfs and
output roots map to the original call's artifacts with full relative paths.
W may write only its exact editable/scratch/node-output targets in the base view;
other roles receive only the read-only base, without W drafts. Scientific input
protection is applied last.

Run cwd may be the exact base root; the backend's internal permission policy
retains the task-work cwd and adds only declared scratch/output writable targets.
The native Worker retains its frozen Codex `workspace-write` policy with exact
project targets projected into it. Unsupported native permission or launcher
configurations are rejected before launch rather than replaced. Network, web,
subagent, Skill and authentication contracts remain unchanged. TCAD retains the
self-contained implementation-JSON compiler/review/package path; DeckFiles and
typed inputs remain the author's explicit dependency closure. Host project paths
and the whole base tree are not automatically sent to remote execution.

Existing legacy retention records keep their saved `host-history/objects/<uuid>`
paths and owning-hardlink interpretation. Each independent freeze has its own
identity; equal bytes do not merge original calls, origins or scientific paths.
Current task, wake, code audit and delivery views resolve their selected originals
through existing bindings and expose them through read-only mounts. Editing requires
an explicit, file-scoped working copy under `work/`, never a pre-copied input tree.
Source ownership is supplied by its lifecycle, not inferred from mode or link
count. Mode 0400 alone is not hostile same-user write isolation.

Protocol 26 protects shared inputs at the final native launch boundary with Linux
read-only mounts, including custom protocol argv. An exactly registered module
launcher retains host preparation and submission service ownership, then applies
the same protection at its final scientific namespace. Its provider/module identity
is frozen even without selected Skills; ordinary wrappers cannot claim this path.
Workspace and work mountpoints
are established before input, Skill and retained-result overlays; ordinary scratch
and output writes remain available. Cov and managed execution apply these overlays
after their path mappings, retaining only the exact accepted output exception.
Host publication verifies the pinned retained-directory identities; replacing a
directory does not redirect publication. Unsupported mount capability fails before
launch, without a writable or copied-tree fallback. Existing native network, web,
subagent and sandbox declarations remain unchanged. Metadata alias checks cover
the exact workspace and known framework aliases, not arbitrary host paths. External
same-user processes, pre-existing descriptors and external aliases remain a soft
boundary; these mounts do not make the host itself read-only.
A new standalone local subprocess without the original host launch owner is
unsupported; existing query/collect/reconcile operations retain their original
record path. Legacy public compilation cannot publish new shared aliases into
writable artifacts after a view is bound; the current private Worker compilation
path remains supported.

`storage_policy` is frozen with the instance and exact job/call configuration.
It defaults to `enabled=false`, with every limit null. Enabling it requires at least
one explicit positive `max_scan_entries`, `max_files`, `max_file_bytes`,
`max_total_bytes` or `max_instance_bytes`; no old constant silently becomes a cap.
The instance limit serializes participating retention/materialization writers and
counts regular-file sizes by unique inode, including partials. Reflinks count their
full size conservatively; hardlink aliases count once. This is managed retention
accounting, not an OS quota on arbitrary native Worker writes. Disabled quotas do
not trigger a whole-instance usage scan. Finite execution/IO deadlines and explicit
handoff or execution-authorization resource policies remain independently effective.
Necessary dispatched-identity, terminal and retirement receipts bypass content
quota refusal so exhaustion cannot erase recovery facts; their bytes remain in
subsequent usage scans. Pre-dispatch acceptance, payloads, logs and materialization
retain their quota checks.

Verified hardlink aliases of an immutable inode already owned by the same instance
add no regular-file bytes and do not rescan instance usage, even if existing usage
already exceeds the configured limit. This does not permit new content: independent
freezes, new projections and log writes still require admission. Usage scans tolerate
entries or descendant directories disappearing during the scan; a missing root or
other access errors remain failures. With an instance byte limit enabled, growing
process logs still incur usage scans; no usage cache survives an unlocked interval.

Local scientific log projections stream complete content through the original ordered
path replacements, including matches across block boundaries. One finite operation
deadline covers both logs, input-reference checks, transformation and publication.
Each projection streams into a new atomic output inode with an opaque object
identity; it does not scan existing content for deduplication. Memory depends on
bounded blocks and replacement metadata,
not the full log length; raw originals remain unchanged and are never truncated.

Control retains exact binding indexes, full receipts and per-file omission inventories;
default scientific views expose human references, observations and limitations. New
views are labelled projections, while source, user originals and scientific prose
retain their exact content. The standalone operator exporter
`scripts/acceptance/worker_evidence_view.py` reads an explicitly selected original
index and its materials, then creates separate `control/` and `science/` trees.
Only `science/index.json` is the reader entry point; omitted materials appear as a
small category summary with `details_ref` and `when_to_open`. When a candidate or
experiment's material coverage matters, the reader can follow those references to
bounded pages of human material names, categories, scope and reasons. These pages
are separate from ordinary evidence entries; the full mechanical inventory remains
in control. The reader decides how the missing materials affect its assessment.
The complete default omission metadata block is at most 8 KiB of encoded JSON;
multiple inventories continue through `omissions_next`. Detail pages have at most
12 items and 16 KiB of final JSON bytes, including navigation and escaped text.
Long scientific strings retain a string preview with sibling `*_truncated` and
`*_details_ref` fields. Their linked `{text, next}` pages reconstruct the full text
by concatenating decoded `text` values; the last page has no `next`. Short strings
keep their original shape. These limits apply to omissions, not the whole index.
Missing-material detail is availability metadata, not the omitted original;
`not_available_in_this_view` records when no original or safe read path was supplied.
The operator supplies only `science/` to the Worker, retaining the output parent
and `control/` outside that default scientific workspace.

## Implementation owners

All listed files are in `src/scidiscovery/research_loop/` unless stated otherwise.

| Responsibility | Owner |
| --- | --- |
| Preparation, routing, pause | `controller.py`, `runtime.py`, `instance_control.py` |
| Unified configuration and backends | `research_config.py`, `backends.py` |
| Detached control and user direction | `control_operations.py`, `user_guidance.py` |
| Native work, continuation, usage | `native.py`, `execution.py`, `continuation.py` |
| Scientific contracts and originals | `contracts.py`, `semantic.py`, `instance_science.py` |
| Candidates and delivery | `selection.py`, `delivery.py`, `worker_delivery.py` |
| Execution and original-call recovery | `experiments.py`, `managed_wait.py`, `local_experiments.py`; domain adapters |
| Host-owned selected code and temporary exports | `code_versions.py`, `experiment_client.py`, `execution_inputs.py` |
| Local W submission lifetime and scientific subprocess view | `native.py`, `worker_execution_bridge.py`, `native_execution.py` |
| Provenance, independent review, approval | `qualification.py`, `formal_review.py`, `execution_inputs.py`, `execution_authority.py` |
| Budget and execution resources | Original control ledger in `budget.py` |
| Instance UI | `instance_ui.py`, `instance_render.py`; no second state authority |
| Fork, quiescence and exact instance deletion | `fork_service.py`, `fork_sources.py`, `fork_runtime.py`, `originals.py`, `instance_delete.py`, `instance_owner.py`; retained helpers in `instance_archive.py` and shared `archive_io.py` |

`scidiscovery.execution_interfaces` discovers execution implementations; instances freeze
the selected executor, operator configuration and execution contract. Implementation upgrades use a fresh prefix and an explicit wrapper switch after
stopping or reconciling old writers; retain the old prefix for rollback. Scientific
originals remain preserved, but old protocol instances have no execution compatibility
guarantee. Historical source fingerprints are metadata, not admission gates.
Task/input/artifact originals, declared permissions, finite budgets and exact external
call identities remain binding; an upgrade never authorizes replay of an unknown call.
Stable role and evidence rules travel once: Codex standard/options launchers use the
developer instruction; protocol and inherited custom launchers use the prompt. Task
packets retain scientific questions, exact inputs and response fields. Formal review
uses its independent review rules. Admission reuses facts only within one read-only
calculation; writer boundaries and later admissions read fresh settings, ledger and usage.
Reported input plus output is a lower bound; unknown coverage is not zero consumption.
Ordinary accepted nodes finish across soft targets, with shared ceilings checked at the
next boundary. Original external-call, FINAL and author-closeout deadlines remain finite.
The non-execution plugin registry, Operation/compiler host, Artifact storage host,
helpers and finalizers are retired. Narrow execution schemas, provenance rules,
process IO and wire transports remain under their existing module paths to avoid
unnecessary file movement. They contain no catalog or scientific scheduler.
Deterministic curve and figure algorithms are self-contained Skill scripts;
[execution adapter API](PLUGIN_RUNTIME_API.md) describes the surviving extension seam.

## Invariants

- Scientific authors own candidate comparison and selection. Control preserves all
  observed candidates in first-observation order, including ties and missing metrics.
  Compact previews report mechanical omissions and link complete originals; they do
  not prune the delivery catalogue. Policy version 2 is exactly `{"version":2}`;
  the catalogue starts no domain calls. A delivery names an exact member and
  explains its choice in `scientific_result`. `candidate_comparison` is optional
  scientific explanation; its rows, `keep`, rationale and evidence-step formatting
  do not grant or gate delivery. Actual audit and delivery actions still require
  an exact available candidate, qualification and execution permission. Null
  does not authorize delivery. Protocol 26 rejects earlier frozen protocols without
  migrating their original bytes.
- One advancing writer per instance; read views neither call models nor own scientific/budget state.
- Exact immutable originals, configuration and calls; revisions inherit no prior qualification or approval.
- Unknown provenance stays unknown. Formal review excludes all known subject authors/participants;
  ordinary A analysis is not automatically formal review.
- Scientific judgments, qualification and execution permission are distinct. Chat and handwritten
  decision files cannot become human authorization.
- Reconcile uncertain external submissions by original identity; no replay, refund, budget reset or new owner.
- Escape author text and read exact sealed attachment members; HTML/SVG remain downloads.
- Workers and same-user host processes are trusted. This is a soft boundary, not hostile-process isolation.

Authorization reads check immutable declarations, identity, exact subjects, scope,
and declared expiry or revocation state; they do not traverse execution outcomes or
replay scientific results. At effect entry points, control explicitly checks
scientific qualification and original integrity separately from current budget,
pause, and live or unknown execution admission. Damaged or missing results do not
erase accepted intents or consumption, or prevent viewing or revoking permission;
they block new effects or leave affected terminal and metering facts unverified.
Collecting or cancelling an already accepted call retains its original authority
and identity; it grants no new submission and never resubmits the original. These
operations remain bounded by the original call or an explicit finite maintenance window.

Ordinary A assignments require an assessment of each original objective and authorized
scope requirement. Key claims cite locatable observations and experiment conditions,
distinguishing measurement, inference and assumption. A separates target metrics from
proxies and sensitivity checks, compares relevant alternatives by the same goal
criteria, and explains conflict, uncertainty and missing inputs. Workflow completion,
audit qualification, exhausted budgets, `finish` and delivery do not establish an
unmeasured goal; A reports unmet objectives when stopping.

An explicit A `candidate_recommendation`, including FINAL, requires
`selection_explanation`: the adopted `criteria` and `criteria_change.status`
(`unchanged` or `revised`). A revision names `prior_criteria`, `changes`, locatable
`evidence` explaining why, and `accepted_tradeoffs`; unchanged details are null.
A compares against prior H/W design and report conditions, thresholds and fallback
rules; relaxing any is a revision even if the report already adopted it.
A first selection may state that no prior criteria were declared. Planning with
no candidate recommendation may use null. Control checks declaration completeness,
not scientific truth, sufficiency or candidate ranking. Missing details return to
the same A through the frozen output-repair policy before sealing. FINAL repairs
retain one task, session and final-return authorization, cumulative consumption,
finite attempt limits and original call duration; depleted shared balances do not
revoke that completion or its original authorized delivery. They authorize no new
experiment, scientific node or retry of unknown execution.

Standard single-command `codex` CLI preparation freezes up to two output-only
repairs or original managed-execution wakes by default, binding the original native home privately so configuration,
authentication and permissions remain unchanged. It freezes the absolute invoked
launcher and its target without replacing invocation symlinks. If no node policy
was supplied, per-role soft targets reference the original global ceilings; they
allocate nothing. Explicit policies override this default, including disabling
repair; custom launchers and direct controller API calls remain explicit. This
default freezes `execution_wake: true` as a narrow exception to `output_only`.
When W normally returns scientific JSON while an original managed call is still
pending, control retains an unsealed waiting checkpoint and monitors that call
without a native turn. Only verified terminal facts for those exact calls may
resume the same W session to interpret results and finalize its response. The
host rejects new managed calls during this narrow wake, including through an old
client; ordinary same-user shell access remains a soft boundary. The exception
shares the original finite attempt limit, cumulative budget, permissions and
home/session. It grants no failed-task retry, work-repair or fragment continuation.
Old frozen output-only policies without the flag gain no wake permission; explicit
full-resume policies retain their existing wake behavior unless explicitly disabled.
The W task reports frozen wake permission and normal pending-call handoff instructions;
this does not force a live W to yield or prove that its session is usable. Missing
permission is diagnosed before host monitoring waits for terminal completion, while
preserving the original checkpoint and call.
Executor receipts carry a compact host-generated `handoff.action`: `return_control`,
`poll_original`, `consume_terminal`, or `reconcile_or_stop`. The same pure decision
rule drives host waiting. Consumption requires a verified original terminal,
not a returned status string. `poll_original` requires waiting and querying the
same original call in the same native turn at a one-second interval while it is
pending and queryable, until the handoff changes or an actual original budget,
time or host-stop limit is reached. It has no query-count limit. Terminal status
strings require same-call reconciliation before consumption; unknown or unavailable
facts require reconciliation or an explicit obstacle instead of blind polling.
This is a Worker handoff contract, not a forced scheduler or an added execution grant. New tasks freeze the
handoff explanation literally; existing task and call originals remain unchanged.

New `prepare` freezes `final_a_completion_policy` version 1. Each ordinary state
checks whether its remaining budget can start the next action. This mode reserves
no shared tokens, time or steps for protected FINAL and does not prepay the next
node token target: positive reported token balance permits ordinary token admission,
subject to the original finite time, step, integrity and execution gates. All H/W/A/E
node token targets and W stage targets are advisory: reaching a target requests a
bounded conclusion without rejecting new managed experiments or forcing A review.
Missing or stale budget advice does not grant allowance or deny execution;
exact identity checks and independent execution authorization still apply.
Shared budget checks for new nodes remain unchanged. Author
closeout token permits are also soft targets; their finite time limits remain
enforced. Actual usage remains accounted without creating a new shared allowance.
Protocol 26 accepts only soft node settings. Older protocols remain read-only;
frozen hard settings are never silently changed to soft. A denied start
advances the existing control journal to durable `FINAL` (`closeout_begin` mode
`final`); it never reclassifies an ordinary A as final from the remaining balance.
An H `wait` with no pending or unknown external execution enters the existing
durable FINAL phase; pending originals retain their waiting mechanism. This
requires the frozen final-A completion capability; its absence is reported as
`final_unavailable`, without inventing a completion grant.
An ordinary A's explicit `finish` ends the run without delivery. Without a frozen
delivery policy, `deliver` has the same stop behavior as `finish`, while its sealed
scientific choice remains unchanged. With that policy, `deliver` consumes only
that sealed author's exact candidate authorization, with no additional FINAL author. When selection policy is enabled, the ordinary A
receives its frozen complete comparison catalogue before authoring its result;
delivery verifies that same catalogue and all original qualification gates.
Delivery failure or missing qualification retains the unresolved result without
a new scientific author or automatic FINAL. Shared budget denial preserves the exact pending route. Optional H/A admission
refusal instead returns a bounded control clarification to H, binding the exact
sealed choice and actual admission reason through the existing author-correction
request. Its first root and reason remain fixed across retries and budget changes;
the existing two-correction allowance is not renewed. The next scientific choice
still faces current role shares, repetition, shared budget and execution gates.
Choosing W rejoins ordinary routing; control does not invent an experiment or new
evidence. Live or unknown originals prevent this new H clarification.

The ordinary control boundaries remain distinct:

| Retained choice or control fact | Next boundary |
| --- | --- |
| H `wait`, no pending/unknown execution | Durable FINAL; `final_unavailable` if its frozen capability is absent. |
| H `wait`, pending/unknown execution | Preserve waiting and the original call. |
| Ordinary A `wait` | Stop at that sealed wait; only exact explicit continuation may reopen it. |
| Ordinary A `finish` | Stop without submission or a goal-success assertion. |
| Ordinary A `deliver`, no delivery policy | Stop as `finish`, preserving scientific route `deliver`. |
| Ordinary A `deliver`, delivery policy present | Consume only that author's exact authorization; failure retains science and supplies no replacement author. |
| Known completed output needs repair | Use only its frozen finite repair permission; exhaustion reports `output_repair_unavailable`. |
| Scientific clarification allowance exhausted | Report `semantic_correction_required`; preserve the first root and consumed attempts. |
| Optional H/A admission refused | Bounded H reconsideration under the same correction allowance and current admission gates. |
| Shared new-action budget denied | Existing FINAL entry, preserving the pending route where applicable. |
| External terminal verified, wake wall budget exhausted | Preserve terminal evidence and report `managed_execution_terminal_wake_deadline`; do not wake without admission. Still-pending deadline remains `managed_execution_wait_deadline`. |
| Actual execution or cleanup unknown | Preserve the original unresolved boundary; no replay, replacement or invented cleanup. |

Already accepted ordinary successors retain their exact scientific route when
replayed after a budget amendment. Current role shares apply to new admission,
including a prepared successor that has no native acceptance.

FINAL authorizes one exact original A through `final_a_return`, using accepted W
evidence and preserving missing inputs. Insufficient, zero or exceeded shared
token, time or step budgets cannot veto this A or its original authorized host
submission and same-operation confirmation. The A gives its conclusion, explicit
candidate selection and unmet objectives; the host supplies the actual submission
receipt. No new scientific work, dedicated audit or correction node follows within that FINAL cycle.
Missing W, candidate originals or delivery permission produce an explicit
unsubmitted result, with the final A still reporting its evidence limitations.
Without an optional node policy, the original frozen global token/time settings
supply the final call's advisory token target and finite duration. Shared ceilings
stay unchanged. An explicit later threshold increase may reopen the saved pending normal route when its admission is affordable and all original effects are known and quiescent. `budget-amend` records the increase; the next run resumes with a new monotone physical step ID. Prior final A outputs, submissions and all consumption remain immutable; a later budget denial starts a new FINAL cycle with its own single A. A decrease or insufficient increase cannot reopen or repeat the final A. Operator closeout without a pending normal route remains terminal. Older frozen instances without this policy retain prior behavior.

Actual provider-reported usage, cached input, consumed time and steps
remain accounted; unknown billing remains explicitly unknown without an estimated charge; original ceilings stay unchanged, and `final_overrun` records
token excess separately. A uses the existing `summary`, `scientific_result` and
`limitations` to explain completed work, unfinished work and evidence-supported
gaps. Ordinary earlier A, formal review and W author continuation receive no such
exception. During a FINAL cycle, no new scientific, review or correction node is
dispatched. The frozen output-repair policy may only resume the same final A task
to complete its response, preserving its original authorization, cumulative usage,
finite attempt limit and call duration; it grants no retry of unknown execution.

For that exact final A, native completion and billing coverage are separate facts.
A verified `turn.completed` event, zero process exit, confirmed original-group cleanup
and intact scientific output can support sealing even when usage is missing or
cannot be reconciled with live counters. Known observed lower bounds and unknown
billing remain recorded; no zero meter or complete billing coverage is invented.
This billing exception also covers its own explicitly authorized submit and
same-operation confirm. A missing terminal event, nonzero exit, unknown cleanup or
unreconciled external effect retains the original unresolved boundary and grants
no replacement A or replay.

Only that original decision's finite `submit` and same-operation `confirm` may
follow. Each native A call, cleanup and delivery action retains its configured
finite duration even after the shared time ceiling is exceeded. Qualification,
authorization, exact-original checks, RSS and cancellation still apply. Unknown
external submissions require original-call reconciliation and are never resubmitted.
Native execution has no per-request admission hook; this policy does not guarantee
a report or successful delivery after process, cleanup or transport failure.

## Declared sources and review

Qualification consumes exact frozen originals, not names, equal bytes or the most
recent object. Managed execution may declare input sources through its frozen
capability; self-contained TCAD V2 packages must match the prepared archive and the
original reviewed producer, while compiled V3 inputs retain their compiler chain.
An explicitly incomplete declaration cannot fall back to a broader source claim.
Optional observers need a separately enabled frozen source contract and a source
view bound to the exact observation. Display coverage alone grants no qualification.
Known producer contributions enter authorship even when not selected as an
implementation. Ordinary parent links do not manufacture authorship.

Ordinary exploration has no mandatory formal-review stage. When requested, review
binds the exact subject and verified contributors; ordinary A interpretation does
not qualify as independent review. Source restrictions propagate through frozen
inputs, and unknown origins remain unknown. These checks cover declared framework
surfaces, not arbitrary native reads or scientific correctness.

## Historical-task forks

`fork_service.py` provides the shared CLI/UI preview and create path. A parent must
use the supported current frozen protocol, be quiescent, and retain the exact selected
logical task N, its historical input cut and necessary control records. The latest A
need not have returned `finish` or completed the research. Unsupported protocols,
missing cuts and ambiguous or damaged control identities are refused. Unknown native,
managed-execution, preparation or delivery effects must first be reconciled against
their original calls; a fork never automatically resubmits them. Scientific originals
are checked by the consumers that actually use them.

The child has a new instance identity. Its first task keeps logical label N and
reruns N's frozen first-invocation scientific inputs in a fresh native session. N's
outputs, later tasks, tool feedback and continuation/repair attempt context are
not inherited as that task's input. The entry selects a logical task, with no
arbitrary attempt breakpoint or remaining-at-cut budget mode. Ordinary tasks
retain their selected route. FINAL and closeout tasks retain their phase and
source cutoff; new budget alone does not convert them into ordinary exploration.
Formal-review tasks retain their neutral exact-subject contract and independence
gates. Engineering-repair tasks retain their bounded repair contract. A task with
continuations or output repairs restarts from its first input, rather than
resuming those attempts.

`fork_sources.py` collects the selected task's necessary typed controls, exact
referenced code versions and current drafts. It does not inventory or preserve the
whole parent directory. Each selected run's original seven-field `code_version`
binds the bounded commit/tree/blob objects transferred into child-owned private Git.
The child uses no parent alternates, object hardlinks or persistent transfer bundle;
removing the source private Git does not remove those transferred versions.

Current dirty/untracked code B is copied separately from historical code A and restored
to the child's writable `code/` directory when its selected job is built, or only
the declared editable drafts under `project/` for a declared project. The original
base remains a shared external dependency. The existing
`visibility=private_snapshot` entries retain unsealed handoff drafts as readable,
inert private state. They do not populate the child's native execution namespace,
create a native intent, commit or seal, or grant scientific qualification or execution
permission. Completed-output acceptance already bound by a seal remains a scientific
reference, not a current handoff draft. The original selected input cut still defines
scientific visibility.

Necessary job, seal, accepted, compiler, authority, history and recovery controls are
copied into the child's existing private store. Original logical identities remain
unchanged; inherited readers use those declared copies rather than requiring the
parent's copied jobs/control files to survive. `originals.py` resolves scientific
entries through their declared origin, path, size and saved fingerprints at actual
consumption. Scientific data, global/declaration inputs and node-produced outputs
remain direct references to their actual originals; fork does not copy or hardlink
their bodies, freeze a new source cache or package inputs. Removing an original can
make its dependent use unavailable. Manifest and selected-task metadata checks do
not reopen unselected scientific bodies. Publication and recovery retain their
original transaction and inode checks without retrying external execution.

Native homes, credentials, model sessions, tools, solvers, licenses and the
execution environment are not copied. Frozen model, permission and launcher declarations remain declarations;
arbitrary unfrozen native reads are not portable. Declared external runtime/adapter
read scopes, namespace mappings and delivery targets remain frozen; forking does
not copy, recreate or expand them, and they must remain valid and available when
the child continues. The current closure bounds are 65,536 entries, 4 GiB of copied
controls/drafts, 16 ancestry levels and 32 MiB per parsed control JSON. Direct scientific
locators do not add their body sizes to copied-byte accounting; selected code keeps
the separate bounded Git rules above.

The operator declares positive finite new token, active-time and control-action
limits. Child accounting measures new work; logical step N is not N newly
consumed actions. Inherited scientific investment and comparison opportunities
remain in scientific history. Inherited costs are reported separately: incomplete
meters stay unknown with any known lower bound; historical active time and total
control actions are not reconstructed as exact costs. A new allowance does not
reset historical science or turn missing usage into zero. Source restrictions
travel with exact originals. Historical review verdicts and execution grants
remain bound to their original subjects and calls; they grant no permission or
qualification to a new child subject or execution.

Creation atomically publishes a paused child and a durable request/receipt.
`fork-create` binds `expected_preview_id` and `operation_id` to the full request,
including target, budgets and exact guidance text. Repeating the same ID and
request reconciles the same receipt, including after loss of the response; a
conflicting request or existing unrelated target is refused. Receipt reconciliation
does not require a surviving parent. The destination parent's private operation
record retains the published child identity and original receipt even after the
child is deleted or moved; the same operation never recreates that child.
Interrupted preparation is cleaned only through its registered transaction inode
while holding that operation's lock. Once a complete staged child and receipt
are durable, recovery publishes that same child. If publication was not confirmed
and both the original stage and target are gone, `fork_publication_unknown` retains
the original identity/receipt for reconciliation and creates nothing. Existing
targets still undergo receipt and original-integrity checks. Explicit
`start --continue` clears the pause
and starts child work under its own gates. Preview and creation launch no model
or solver calls.

Optional guidance preserves the user's exact text as advice for the selected
ordinary task and legal successors. General fork guidance is deferred past a
selected formal reviewer so its neutral inputs remain intact. It grants no
scientific verdict, budget or execution permission. The UI enters through a
historical node; open the paused child and choose “继续研究” to advance. It retains
failed form drafts and offers same-request receipt
reconciliation for the attempted operation in the same UI host/browser session.
General Web operation recovery across host restarts is not established; the
CLI/service receipt is durable.

Adapter record writers hold the generic shared `record-writer.lock`; pending-call
inspection takes its exclusive nonblocking lease. Lifecycle quiescence checks take
all existing direct record `*.lock` leases exclusively without domain prefixes or
loading installed providers, preserving conservative handling of old records.
Writers acquire owner gate, shared record writer, node/admission, then
adapter-private locks. Lifecycle checks acquire the owner gate exclusively and
nonblocking before node/admission and direct record locks; that gate serializes
the two orders. A held writer blocks fork/delete; an unknown original call still
requires reconciliation.

All cooperating host processes must see the same stable
`/tmp/scidiscovery-owner-gates-<euid>` directory and lock inodes, as well as the
same instance mounts. Do not delete or rotate these gates while processes live.
Private `/tmp` namespaces, including PrivateTmp or separate containers, require
an explicit common deployment mount; independent private gates do not coordinate.
Changing TMPDIR or an XDG runtime directory does not establish that shared mount.

Read-only original views and the host's cooperative source lease protect the declared
reference boundary among cooperating current-version writers. They remain a same-user
soft boundary: a same-user process that deliberately changes permissions and writes
an original inode can damage its consumers. Reference metadata is not a content-tamper
detector; saved fingerprints remain required for selected scientific consumption.
Do not combine forking with active pre-upgrade writers: the lease
does not retroactively fence older processes. These controls do not establish
real-model, solver or scientific qualification.

## Explicit instance deletion

`instance_delete.py` removes only an explicitly named current-protocol instance,
bound to its goal ID, frozen settings reference and operation ID. Unused prepared,
paused and completed roots must pass the existing lifecycle quiescence checks.
Necessary accepted, submission, terminal, authority and retirement controls still
prove stopping and identity; missing unselected science, old code drafts or removed
exports are not deletion prerequisites. Active owner/source-reader locks, unknown
execution or delivery effects, and unknown cleanup block deletion without canceling
or replaying work. Canonical paths and mounted-subtree checks constrain cleanup to
the selected tree. Symlinks
in control namespaces traversed by quiescence checks are rejected before traversal;
other interior symlinks are unlinked without traversing their targets. Cleanup may
restore owner write access to pinned directories, never to shared regular files.
Nested independent instances, including recognizable damaged identities, block
deletion and cleanup retries. Preparation and fork publication hold containing
directory gates. One physical host gate keyed by the original directory inode fences
both its original and tombstone paths across rename, so a new instance cannot be
created inside the tree during disposal.

A private durable operation record outside the root pins its directory inode and
exact tombstone. A retirement marker fences ordinary effects, including
unenrolled instances. With the original host gate and local owners locked, the
service retires an enrolled anchor, atomically moves the exact root, and removes
only the recorded tombstone. Owner disposal retains identity checks across this
intentional move; ordinary leases cannot opt out of them. The external anchor
reaches its deleted state before the completed receipt is published.

Exact-request retries recover recorded retirement/cleanup and return the original
receipt. They never adopt a replacement root at the former path. Deleted evidence
cannot be restored by the delete service; old cold checkpoints do not reactivate
a deleted owner generation. Cleanup removes only this instance's own Git, temporary
exports and directory; it does not chmod or remove external originals, inspect
neighboring instances for references, or add garbage-collection authority. A child
that directly references science owned by this deleted instance can lose that
science; already transferred child Git and copied controls remain child-owned.
An interrupted cleanup continues under the same operation, identities and receipt;
its IO deadline does not reset scientific budgets.

## Extension rules

Scientific algorithms and usage guidance belong to selected Skill bundles; execution adapters belong to independently installed adapter packages.
Core routes frozen declared capabilities without branching on a domain or plugin
name to decide scientific behavior. Schema shape, declared scientific semantics
and validation must agree; engineering failures must not be disguised as scientific
rejection or an invitation to retry. Bounds and failure evidence survive recovery.
Read views and UI render the same sealed scientific bytes without rewriting them.
CLI result/materials/artifact and the webpage use the same exact-original API; downloads refuse existing destinations. Webpage configuration, explicit instance choices, queued budget changes and guidance use public interfaces and never infer an instance by discovery or recency.

### Instance UI control and display

Each submitted start, resume, continue or budget form retains one operation ID and
its exact request. A retry reconciles the saved pending or terminal receipt without
dispatching again; a newly opened form represents a new intent. Loaded instances
are bound to immutable `goal_id`, so a replacement at the same path gets a fresh
UI session. A standalone instance UI holding `ui.lock` disables fork and delete;
close that host and load the exact directory from a workspace to use those controls.
Direct requests retain the same refusal and never bypass the lock.

Display caches serialize read, invalidation and eviction. Compact all-history facts
survive the 256-large-node cache limit, with saved-source metadata checks including
referenced recommendation sources; history aggregation still grows with recorded
history. Candidate pages retain a fixed sealed prefix and report an expired view
after its cache is cleared or evicted. These are display projections, never scientific
admission evidence. Displayed time uses the control budget's interval rules, including
supported cross-boot intervals; unreadable intervals remain unknown. Public progress
uses bounded catchup, explicit omissions and links to newly sealed conclusions.

Field translation is a separate application service, with explicit model start
and its own cache. Translation sets the native shell and image tool switches to
false before launch and inherits only named runtime, authentication, proxy and
certificate environment keys. Authentication-home references remain available
without copying credential files.
Tool-event rejection is an additional check, not proof that every native tool is
absent or that same-user access is isolated. Unknown UI errors use fixed public
messages and bounded private diagnostics; operation request IDs are distinct from
saved diagnostic references. See the [CLI guide](ROOTLESS_CLI.zh-CN.md) for the
supported controls and translation limits.

## Support and qualification

Managed local experiments, candidate delivery, UI, declared provenance, execution gates
and original-call maintenance are implemented. `/results?after=N` lists sealed nodes;
`/results/<step>` shows an exact result. Observed candidate facts and authored choices
are frozen at that node's input boundary. Sealed nodes and their outputs remain in
place for result viewing, handoff and references; retaining a result does not collect
its input bodies or create another package or seal.

`archive-export`, `archive-inspect`, `archive-restore-material`, `checkpoint-enroll`,
`checkpoint-export` and `checkpoint-restore` are retired, along with their package APIs.
Historical packages and audit records remain read-only, without automatic import,
migration, deletion or reactivation. Ordinary `checkpoint.capture/validate`, node-owned
training checkpoints and original-call query/reconciliation/recovery retain their
distinct roles. Ordinary checkpoints do not preserve or restore lost drafts; deleting
node outputs or their source originals cannot reconstruct their scientific bodies
from references or code commits.

New dispatches check known review/breadth admission before prepare and execution
resource allocation, then recheck the accepted-call set and admission under the
existing short locks. In the exact built-in local adapter's current pre-submit
flow, refusal or cancellation cleans only its own temporary preparation without
registering external pending state. Cleanup failure remains a local cleanup
problem. This exception neither infers no launch from missing files after a crash
nor promises cancellation of a durably accepted call.

For adapters whose preparation may have external effects, unpublished snapshots
retain one exact host binding before input materialization. Explicit maintenance
can publish an already durable, admitted accepted record, or discard only a
partial snapshot with the original
directory identity. Neither action reruns prepare, admission or submission. A
discarded unresolved binding remains unknown in pending recovery and later Worker
views, and blocks new preparation and fork paths, including configurations
without execution authority. Original permissions, reservations and consumption remain unchanged.
Historical published bindings retain their original bytes and provenance. Reading
that history grants no live maintenance authority over a different directory generation.
Recovery of unbound temporary directories remains explicit and targeted: the existing maintenance command's
`--unbound-local` branch selects only root, step and the exact snapshot token.
Under the source, controller and exact record-writer locks, it requires all
retained attempt configuration originals and interface projections for that step
to agree on the built-in local executor without execution authority. Missing or
mixed originals, third-party preparation, a binding for the token, or any durable
accepted record prevent cleanup. It removes only the existing selected partial
with finite symlink-safe tree removal, writes no new recovery receipt, and changes
no execution state, budget or retry permission. An absent target cannot establish
a prior successful cleanup. Other unbound directories remain preserved.

Historical read-only packages grant no advancement, restoration or owner reactivation.
Cross-host recovery, lost anchors and arbitrary native external reads remain outside support.
Source/HTTP/synthetic-process checks do not qualify real browsers, native Codex continuation,
TCAD solvers or power-loss recovery. Retirement grants none of these qualifications and does
not operate existing host services, databases or external jobs.

TCAD-specific promotion evidence is defined in the [qualification status](TCAD_QUALIFICATION_STATUS.md).

See the [CLI guide](ROOTLESS_CLI.zh-CN.md), [installation](INSTALL.md) and [release scope](RELEASE.md).

Adapter audit, smoke and scratch recovery requests contain staged candidate originals and the frozen scientific configuration, without the real delivery target. Only promote and confirm requests receive that target. This reduces unnecessary host information in the phase interface; same-user execution remains a soft boundary.

## Selected framework Skill assets

`skill_supply.py` binds each new instance to the actual CLI installation's single
`skills/` tree. Source releases use the tree beside `src/`; thin installations use
the tree beside `site/`; the supported standard wheel layout uses
`sys.prefix/skills`. Bare wheels do not include these external assets. Source roots
cannot be supplied by configuration or `SCID_SKILLS_ROOT`.

Prepare freezes expanded named sets and source bindings, not bundle bytes. Existing
role/Worker `skill_set` selects actual use. Only the explicitly selected execution
provider may offer `research_defaults.optional_skill_sets`; installation or an offer
alone enables nothing. Jobs and native intents bind exact set/items and revision;
retained execution and fork selection do not import newer offers. Revisions select
already frozen sets only; changing their inventory requires a new instance.

`.agents/skills` is a validated, rebuildable discovery projection. Selected
installation directories are bound read-only in place at the final native mount
seam. New instances create no Skill objects, byte manifests, copies or hardlinks;
source and instance may cross filesystems. Only selected directories and manual
metadata are checked during preparation. Resources remain live installation assets;
updates at the frozen source path are visible to later consumers. Missing or linked
selected directories/manuals identify the Skill and exact expected repair path.

Retained Skill schema v2 manifests and objects remain read-only consumers. A fork
with no local legacy store resolves the exact registered parent Source, validates
its identity and original manifest binding, then reads that parent's objects without
copying. Parent removal makes those consumers unavailable with a precise source
path; no automatic migration or retired archive/cold restoration is provided.

This selection does not suppress native user/system/plugin Skill sources or change
declared native permissions. Scientific backend subprocesses receive no Skill
mounts. Cov still copies configured authentication into its separate native home;
shared credentials, refresh and logout are not established by this change.
Read-only mounts remain a soft boundary for trusted same-user processes. See
[configuration](RESEARCH_CONFIGURATION.md#selected-framework-skills--按角色选择框架-skill).
