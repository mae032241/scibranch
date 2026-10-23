# Current architecture: thin H/W/A/E control

[简体中文](ARCHITECTURE.zh-CN.md) | English

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
CLI archive / checkpoint -> original verification and bounded restoration
```

H owns hypotheses and mechanisms; W owns bounded experimental design, implementation
and observations; A owns contribution, evidence gaps, direction and delivery selection;
E owns requested evidence work. Control owns immutable bindings, budgets, qualification,
authorization and mechanical publication. It does not manufacture science or ask models
to perform extra hash calculation, comparison, verification or prepare hash inventories.
Workers write ordinary scientific files; control computes and verifies their exact
bindings automatically and retains the mechanical records privately on the host.

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
bytes, labels and recency cannot select the source. Deduplicated archive paths
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

`exp` is a thin submission and wait helper using one ordinary
`submit.json`/response slot. For local-mode W scientific views, the existing native
host services that slot for the native attempt's lifetime and writes responses
automatically. External mode leaves service ownership with its declared launcher.
Neither path adds an agent, daemon, UDS or FIFO.

The default local command backend resolves the selected Codex installation's native
binary and uses its sandbox with a restricted scientific subprocess file view. The
accepted directory snapshot is read-only at its original scientific path, with a
separate writable `artifacts/`; a source-only call maps its sealed script back to
its scientific source path. The backend receives runtime files, the scientific
workspace and a task-native permission configuration, without native authentication,
provider configuration, control originals or the submission slot. The native Worker
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

Managed execution outputs, observation originals and history retention reuse the
instance's existing `host-history/objects/<sha256>` archive. Host-only immutable
aliases may share an inode across calls; equal bytes do not merge original calls,
origins or scientific paths. Accepted writable cwd snapshots and Worker input,
collection and wake views use independent inodes: bounded reflinks where supported,
otherwise bounded copies. Mode 0400 alone is not same-user write isolation.

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

Control retains exact binding indexes, full receipts and per-file omission inventories;
default scientific views expose human references, observations and limitations. New
views are labelled projections, while source, user originals and scientific prose
retain their exact content. The standalone operator exporter
`scripts/acceptance/worker_evidence_view.py` verifies an explicitly pinned original
index and all indexed bytes, then creates separate `control/` and `science/` trees.
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
| Local W submission lifetime and scientific subprocess view | `native.py`, `worker_execution_bridge.py`, `native_execution.py` |
| Provenance, independent review, approval | `qualification.py`, `formal_review.py`, `execution_inputs.py`, `execution_authority.py` |
| Budget and execution resources | Original control ledger in `budget.py` |
| Instance UI | `instance_ui.py`, `instance_render.py`; no second state authority |
| Archive and same-host cold restore | `instance_archive.py`, `instance_owner.py`, shared `archive_io.py` |

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
  does not authorize delivery. Protocol 23 rejects earlier frozen protocols without
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
Protocol 23 accepts only soft node settings. Older protocols remain read-only;
frozen hard settings are never silently changed to soft. A denied start
advances the existing control journal to durable `FINAL` (`closeout_begin` mode
`final`); it never reclassifies an ordinary A as final from the remaining balance.
An ordinary A's explicit `finish` ends the run without delivery. Its explicit
`deliver` consumes only that sealed author's exact candidate authorization, with
no additional FINAL author. When selection policy is enabled, the ordinary A
receives its frozen complete comparison catalogue before authoring its result;
delivery verifies that same catalogue and all original qualification gates.
Delivery failure or missing qualification retains the unresolved result without
a new scientific author or automatic FINAL. Budget denial preserves the exact
pending route; optional thinking shares use the ledger's effective total token
authorization and are checked again before explicit budget increases can resume it.
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
use the supported current frozen protocol, be completed and quiescent, and retain the exact selected
logical task N and its declared dependency originals. Unsupported protocols,
missing historical cuts, ambiguous identities and missing originals are refused.
Unknown native, managed-execution or delivery effects must first be reconciled
against their original calls; a fork never automatically resubmits them.

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

`fork_sources.py` reuses the generic directory inventory to preserve the complete
quiescent source storage as a private snapshot using verified hardlinks. This is a
current-directory superset, not a reconstruction of a complete historical directory
at task N. The selected job and its exact original input cut separately determine
scientific visibility. `visibility=private_snapshot` entries are omitted from ordinary
node views; internal exact-hash references can still resolve them without making
them scientific inputs or qualifications. Fork/cache implementation storage is
excluded; existing original object storage is retained. No domain filenames or
plugin JSON traversal define this inventory. `originals.py` resolves originals by
original identity and digest. Existing 0400 originals are shared directly.
Writable or differently permissioned originals are first frozen once into the
source's private `host-history/fork-cache` store, then shared. Source bytes
and permissions are not changed. Public views link only their selected retained
originals, never private control metadata. Nested branches share these same
immutable inodes while keeping distinct original identities and local paths.
Declared in-instance host keys are excluded using their verified private source
locator. An unknown credential-looking filename does not grant an exclusion;
unrecognized ownership remains an inventory gap and blocks a fork.

Each retained directory entry is an owning reference; the filesystem's link count
is authoritative. Deleting a parent or sibling removes only its references, so
children remain usable. Source-owned cached snapshots remain until that source's
storage is removed; data is reclaimed when its last link disappears. There is no
separate mutable reference-count database. All shared sources and the target must
be on the same filesystem; unsupported links fail explicitly without copying as
a fallback. A source requiring new snapshots must permit private-store writes.
Existing copied manifests remain readable. Later task execution still prepares
normal node-local input copies; sharing at fork creation is not a zero-copy
execution-workspace guarantee.
Pure readers tolerate link-induced metadata changes with at most three attempts
per reader: only ctime may differ on a 0400 regular file, while its path, inode,
mode, size and mtime remain fixed. Each attempt must finish with a stable generation
and exact original binding; aborted hashes are not cached. Persistent changes fail
closed. This does not retry creation, control actions or external execution.

Native homes, credentials, model sessions, tools, solvers, licenses and the
execution environment are not copied. Frozen model, permission and launcher declarations remain declarations;
arbitrary unfrozen native reads are not portable. Declared external runtime/adapter
read scopes, namespace mappings and delivery targets remain frozen; forking does
not copy, recreate or expand them, and they must remain valid and available when
the child continues. The current closure bounds are
65,536 entries, 4 GiB, 16 ancestry levels and 32 MiB per parsed control JSON.

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

Adapter record writers hold the generic shared `record-writer.lock`; pending
inventory takes its exclusive nonblocking lease. Lifecycle quiescence checks take
all existing direct record `*.lock` leases exclusively without domain prefixes or
loading installed providers, preserving conservative handling of old records.
Writers acquire owner gate, shared record writer, node/admission, then
adapter-private locks. Lifecycle checks acquire the owner gate exclusively and
nonblocking before node/admission and direct record locks; that gate serializes
the two orders. Ordinary archive export also holds this exclusive source gate.
A held writer blocks fork/archive/delete; an unknown original call still
requires reconciliation.

All cooperating host processes must see the same stable
`/tmp/scidiscovery-owner-gates-<euid>` directory and lock inodes, as well as the
same instance mounts. Do not delete or rotate these gates while processes live.
Private `/tmp` namespaces, including PrivateTmp or separate containers, require
an explicit common deployment mount; independent private gates do not coordinate.
Changing TMPDIR or an XDG runtime directory does not establish that shared mount.

Read-only originals and the host's cooperative source lease protect the declared
sharing boundary among cooperating current-version writers. They remain a same-user
soft boundary: a same-user process that deliberately changes permissions and writes
a shared inode can damage all its references; digest validation detects corruption. Do not combine forking with active pre-upgrade writers: the lease
does not retroactively fence older processes. These controls do not establish
real-model, solver or scientific qualification.

## Explicit instance deletion

`instance_delete.py` removes only an explicitly named current-protocol instance,
bound to its goal ID, frozen settings digest and operation ID. Unused prepared,
paused and completed roots must pass the existing archive quiescence checks.
Active owner/source-reader locks, unknown execution or delivery effects, and
unknown cleanup block deletion without canceling or replaying work. Canonical
paths and mounted-subtree checks constrain cleanup to the selected tree. Symlinks
in control namespaces traversed by quiescence checks are rejected before traversal;
other interior symlinks are unlinked without traversing their targets. Cleanup may
restore owner write access to pinned directories, never to shared regular files.
Nested independent instances, including recognizable damaged identities, block
deletion and cleanup retries. Preparation, fork publication and cold restoration hold containing
directory gates; deletion fences both the original and tombstone paths so a new
instance cannot be created inside a tree during its disposal.

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
a deleted owner generation. Shared payloads use ordinary inode references:
unlinking one instance does not remove another branch's originals, and no manual
reference-count database or garbage-collection authority is introduced.

## Extension rules

Scientific algorithms and usage guidance belong to selected Skill bundles; execution adapters belong to independently installed adapter packages.
Core routes frozen declared capabilities without branching on a domain or plugin
name to decide scientific behavior. Schema shape, declared scientific semantics
and validation must agree; engineering failures must not be disguised as scientific
rejection or an invitation to retry. Bounds and failure evidence survive recovery.
Read views and UI render the same sealed scientific bytes without rewriting them.
CLI result/materials/artifact and the webpage use the same exact-original API; downloads refuse existing destinations. Webpage configuration, explicit instance choices, queued budget changes and guidance use public interfaces and never infer an instance by discovery or recency.

## Support and qualification

Managed local experiments, candidate delivery, UI, declared provenance, execution gates,
original-call maintenance and same-host original-path cold checkpoints are implemented.
`/results?after=N` lists sealed nodes; `/results/<step>` shows an exact result.
Observed candidate facts and authored choices are frozen at that node's input boundary.
Ordinary read-only archives grant no advancing restore permission. Cross-host recovery,
lost anchors and arbitrary native external reads remain outside support.
Source/HTTP/synthetic-process checks do not qualify real browsers, native Codex continuation,
TCAD solvers or power-loss recovery. Retirement grants none of these qualifications and does
not operate existing host services, databases or external jobs.

TCAD-specific promotion evidence is defined in the [qualification status](TCAD_QUALIFICATION_STATUS.md).

See the [CLI guide](ROOTLESS_CLI.zh-CN.md), [installation](INSTALL.md) and [release scope](RELEASE.md).

Adapter audit, smoke and scratch recovery requests contain staged candidate originals and the frozen scientific configuration, without the real delivery target. Only promote and confirm requests receive that target. This reduces unnecessary host information in the phase interface; same-user execution remains a soft boundary.

## Selected framework Skill assets

`skill_supply.py` freezes explicitly declared bundle sets once per instance and
binds role/Worker selections to jobs and native intents. Ordinary bundle files
remain instance-owned; `.agents/skills` is a validated, rebuildable discovery
projection. Source installation updates do not refresh frozen bytes. Revisions
select existing frozen sets only; new IDs/versions require a new instance.
Archive/fork/cold restore retain ordinary objects and rebuild projections under
the receiving root. They cannot depend on a surviving installation or parent.
Native user/system/plugin sources remain inherited, and existing native permission
profiles remain unchanged. See [configuration](RESEARCH_CONFIGURATION.md#selected-framework-skills--按角色选择框架-skill).
