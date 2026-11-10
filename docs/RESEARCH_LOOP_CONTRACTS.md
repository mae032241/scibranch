# Research-loop contracts

[简体中文](RESEARCH_LOOP_CONTRACTS.zh-CN.md) | [Architecture overview](ARCHITECTURE.md)

This reference owns the detailed current scientific, identity, lifecycle and recovery
contracts. Configuration fields and backend-specific profiles are maintained in
[Research configuration](RESEARCH_CONFIGURATION.md); operator commands are maintained
in the [CLI guide](ROOTLESS_CLI.zh-CN.md).

## Supported contract

The supported scientific behavior is `research_strategy_version=3` with
`advisory_scope_version=1`. Automatic supervision is either absent (`None`) or
version 4 with an explicit boolean `require_fresh_input` in frozen settings/jobs;
new preparation defaults to `true`. Explicit `false` remains configurable.
A fragment policy is either absent (`None`) or has `context_handoff_version=2`.
Older strategy 1/2, unmarked scientific jobs, supervision 1/2/3 or missing v4
freshness fields, and fragment version 1 or a missing handoff marker are rejected
before scientific interpretation or new effects. They are not reinterpreted,
upgraded by configuration/fork/recovery, or supported by a compatibility reader.
Protocol 33, worker/lineage view 1 and
`final_a_completion_policy.version=1` remain valid format markers; their numeral 1
does not identify an older scientific policy. Custom scientific text remains an
immutable original and cannot grant authority.

## Originals and private bindings

Control does not hash scientific data or outputs by default. Hashing is restricted
to approval bindings, small control requests that require durable idempotency and
host-owned Git code capture. Declared project bases use ordinary Git worktree
checks, without control reading or hashing every base file. Approval/idempotency helpers must
not traverse scientific files, code, logs, observations, inputs, results, Skills or
archives. Explicit scientific tools may use their own algorithms
on selected evidence; that is separate from control identity. Protocol 33 uses
`record_ref = {"path": "/absolute/original"}` and
`file_ref = {"path": "/absolute/original", "size_bytes": N}`. Control bindings use
`*_ref` fields; paths, producers and lifecycle identities replace content digests.
Legacy retained object paths use UUIDs, without content deduplication or hash caches.
Fork manifest v2 retains this reference contract. Independent archive and whole-instance
cold checkpoint packages are retired. Only current protocol 33 instances with
worker/lineage view 1, soft budgets and final-A policy v1 are supported. Earlier
formats are rejected without compatibility reading, migration or owner reactivation. One-off engineering rescue commands, their historical execution exceptions and derived correction of old usage receipts are retired. Instances relying on those rescue records are unsupported; bounded `worker-closeout`, current same-session attempt metering, ordinary restart recovery and budget-amendment continuation remain supported under their own gates. Read-only Linux mounts protect selected
inputs in the scientific namespace; trusted same-UID code remains a soft boundary,
without protection against deliberate same-UID rewrites or content-tamper detection.

`scid-research` owns explicit research instances; `scid-experiment` records managed experiments.
There is no LLM Root, MCP scheduling service or background Root daemon.
The core exposes the thin CLI and instance UI; the old CLI/MCP/UI, Root host,
platform-profile generator and legacy systemd deployment are retired.

`research_config.py` owns the shared TOML, CLI and host preparation, defaulting and validation path. New instances freeze effective settings; editing a declaration does not amend an existing instance. `backends.py` separates Codex, explicit protocol transport and experimental Pi from scientific judgment, budgets and execution authority. Protocol permissions are launcher hints, not isolation; native resume and complete authorship attestation are unsupported, so formal independent review is unavailable. Enabling subagents also establishes neither complete authorship nor complete usage. See the [configuration and backend reference](RESEARCH_CONFIGURATION.md).

Each native backend implements the complete `Backend` abstract class. Preparation,
profile and permission projection, launch/resume, observations, terminal evidence,
recovery capabilities, authorship and public configuration belong to this boundary.
Control and UI consume common facts; unavailable optional telemetry stays unknown.
The standard Codex adapter owns one app-server transport, using exact thread/turn
notifications without reading private rollout files. Its event projection skips
tool/input bodies; complete assistant final bytes are streamed separately from the
bounded public preview. Host clocks, scientific decisions and managed experiment
adapters retain their existing owners.

Backend-specific model, permissions, native resources, Skills, telemetry and resume
profiles belong to the [configuration and backend reference](RESEARCH_CONFIGURATION.md).
Codex, explicit protocol and experimental Pi implement the same complete `Backend`
class. No external launch, observation, recovery or authorship subprotocol registry
is introduced. Optional telemetry can be unavailable; required identity and
required metadata/admission checks remain strict. Chunked RPC/session reads remove
raw per-line and whole-session byte quotas, but parsing a complete JSON value still
uses memory proportional to that value. This does not promise bounded total RAM.

Ordinary scientific responses require only summary, scientific_result and limitations,
plus H/A next_action and route_reason or W stage_intent. Under v3, W's `revise_h`
and `review` also require a concrete nonblank `consultation_question`. Standard Codex requests JSON
in the prompt; local validation checks required fields and the explicit control choices
actually consumed. Output repair names the exact role, JSON path and expected
shape, preserving unrelated scientific content. Other scientific content stays open, without nested optional-field
Schemas. Formal independent review retains its separate strict contract. Frozen schema
inputs remain unchanged. Ordinary tasks use current minimal outbound instructions
even with an older frozen strict schema; protocol carries the current minimal schema
projection. Recorded pre-work format failures retain bounded original-call recovery.
Actual invocation transport is recorded separately from capability. Custom launcher
permissions retain their own contracts.

Regression checks limit new default fixed ordinary final-response instructions to 3 KiB across
Codex/protocol and the actual ordinary, resume, repair and W-fragment branches,
including a 512-byte repair-diagnostic allowance. Changed rules reach retained
resume/repair prompts through bounded addenda under the same fixed-default limit.
Scientific originals, exact dynamic
references, budgets and user instructions remain complete and are measured separately.
Retained frozen scientific guidance and historical task inputs are also measured
separately; their actual total may exceed this new default limit.
Formal review and the Local execution result-file interface have separate contracts.

Frozen permission summaries distinguish requested and effective policy, enforcement and limitations. Standard Codex danger-full-access cannot enforce offline intent; read-only remains offline despite a network request. Protocol/custom-launcher access remains unknown with hint-only declarations.

The CLI is the application core; the optional webpage uses the same public controls and read views. `control_operations.py` records durable detached advancement and scientific budget amendments. The independent controller retains the original writer lock and survives webpage service exit. Pause prevents the next node while accepted work finishes under original limits. Amendments apply at safe boundaries without resetting consumption or unknown usage; unknown original calls require reconciliation, never automatic resubmission.

`user_guidance.py` binds global or role-specific advice to the next matching new task or subsequent research. Pending revisions can be edited/revoked; applied task inputs are immutable, with future research revocation recorded separately. Advice may be declined. Explicit goal changes preserve the original objective record and name an effective node; they grant no execution permission or scientific qualification.

`configuration_updates.py` stores operational revisions separately from the original settings identity. A new node reserves an exact revision before constructing its inputs; existing nodes and their original calls retain their own settings for validation, execution and recovery. Saving queues a revision without launching work. Cumulative budget and resource ledgers, pending execution facts and FINAL barriers survive configuration changes. The configuration reference describes editable fields and the distinction between active and pending settings.

## Scientific ownership and routing

See the [architecture flow](ARCHITECTURE.md#research-flow) for the overall loop.

W owns sustained research direction, mechanisms, experimental design, implementation,
adaptation and candidate choice. New preparation enables W's native subagents by
default only when the selected Backend supports them. The effective boolean is
frozen separately from the model profile in the job/intent as `native_subagents`,
and the actual Backend applies it for that invocation. An explicit role
`subagents` value takes priority over an explicit global value; otherwise the
capability determines the new W default. Other roles retain their declared global
permissions, and old jobs without the role field retain their original options.
Unsupported backends, including native Pi, do not acquire an emulated subagent
path. When enabled, W prefers native subagents for small evidence extraction or
scripts; when unavailable or disabled W does that small work itself without an
extra handoff. W independently considers E only for large or complex evidence work.
Main-thread resume remains separate from complete authorship: enabled subagents
leave participant completeness unsupported, and observed main-thread token totals
are lower bounds with child usage unknown. These limits grant no formal-review
qualification or original-call replay permission. H supplies evidence-grounded, revisable initial
explanations and distinguishing experiments; W may reject or revise them and consult
H when an explanatory gap, contradiction or stalled progress warrants it. Routine
batches, new mechanisms and context renewal do not require A. Breadth is reconsidered
when it can change the research decision, without a mandatory full comparison cycle.

New preparations freeze `research_strategy_version=3` and start with W. W can
begin discovery with no candidate or mature hypothesis. During ordinary research,
before a managed batch it declares a stable mechanism and `experiment_question={question,change_criterion}`
through `--research JSON_FILE`:
what it wants to learn and what observations would change the next step. Discovery,
representation search and implementation or optimizer diagnosis are valid purposes.
Declarations can revise, merge, retire or resume exact identities within the same W
session; a new hypothesis does not require a preliminary H or W handoff. Each call
retains the exact declaration revision and original batch purpose. Control checks
identity and structure, without deciding scientific novelty or truth.

Research preflight uses the existing writer lease: it first admits the current
attempt and checks pause, host review and remaining budgets, then publishes the
immutable declaration once. It performs no external execution, adds no call
investment and grants no execution permission. Actual call acceptance reuses the
exact frozen declaration binding idempotently.

W leads research, H opens
useful alternatives, ordinary A reflects on search, and Final A judges delivery.
W keeps a runnable incumbent, checks that experiments answer the actual objective,
and adapts from observations and residuals. Exploratory evidence may guide the next
experiment with its adaptive limitations stated; final claims need the checks
appropriate to the declared goal. The strategy does not prescribe a search tree,
method-family coverage or one validation recipe for every scientific question.

H offers distinct explanations, representations or decompositions with their
evidence and uncertainty. New strategy-3 preparations additionally freeze
`advisory_scope_version=1` in settings and jobs. Marked ordinary A diagnoses
evidence-backed local lock-in, ignored counterevidence, goal drift or severe
reasoning flaws. It states findings and possible H/E consultation suggestions in
`scientific_result`; no material issue means no new constraint. W owns routine
strategy, model, depth, folds, bagging, expansion and ordering, and decides whether
to consult H or E. Calls, low scores and budget points alone are not evidence of
lock-in. Ordinary A returns meaningful `scientific_result`, `continue_w` and
`decision_request:null`. Control recognizes this return without requiring the
redundant `supervision_result` code or filling it into the original answer;
an explicit conflicting code or route still requires correction. Returning advice
does not endorse or confirm the scientific direction.
The successor W task does not promote A's `route_reason` into its main question
or require preservation of the preceding local focus. The diagnostic scope is
independent of supervision trigger settings.

Final A, formal independent review and engineering repair retain separate authority.

With original budget remaining, W's voluntary `decision` first
requests one pre-delivery Final A before irreversible final lock. That same A may
accept exact `deliver`, reject every candidate with `finish` and no recommendation,
or request one concrete decision-changing W correction. Correction uses only the
original remaining allowance, permits no broader research or H/A/E consultation,
and returns for at most one reassessment; a second correction is unavailable.
A correction freezes only its exact W subjects' retained active
mechanisms and uses the frozen `--mechanism N` interface, without live declarations.
An empty combination still permits reading and correcting retained evidence;
managed new execution is refused before preparation.
Accepted delivery does not require another ordinary A followed by another Final A.
Exhausted budgets use protected final-only reporting: no new W/E, experiment or
correction. An already accepted pre-delivery A concludes from originals under its
own exact deliver/finish authority; exhaustion does not require another A.
Protected Final A may also reject all candidates even when the
inventory is nonempty. Availability is not scientific qualification. Best-effort
submission needs explicit original task policy and does not imply scientific
endorsement; only explicit `deliver` authorizes the exact candidate under the
original qualification and execution gates. Normal no-delivery is a scientific
outcome, not by itself a failure metric. Engineering route tests establish none of
the strategy's scientific benefit. Missing
hidden scores, proof of a unique generator or perfect certainty cannot become
indefinite vetoes. Cancellation or transport failure remains an operational stop,
not a scientific conclusion. Formal independent review remains a separate purpose.

The purpose-specific clause in `guidance.py` is frozen with each new task and used by
the task view and native renderer. W consultation returns advice to W; an ordinary
requested assessment must answer the request; formal review uses a neutral exact
subject and independent reviewer; closeout permits only its existing final actions.
These purposes do not inherit one another's authority. Custom scientific literals
remain frozen and cannot create execution permission. E answers bounded evidence
requests. Control owns immutable bindings, budgets, qualification, authorization and
mechanical publication, without manufacturing scientific judgments or making models
repeat hash inventories. Workers retain ordinary scientific files; exact identity
and declared sizes remain privately bound by the existing host contract.

### Consultation, fragments and context

New standard single-command Codex and supported Pi preparations default to same-session W fragments
with `worker_fragment_policy.max_context_windows=1`; TOML
`policies.worker_context_windows` accepts 1 or 2. `stage_intent=continue` keeps the
same logical W job and cumulative allowance. `handoff` seals the current result
and starts a new W; `revise_h` asks H for consultation and `review` asks A for
reflection. Marked W can use `extract` to ask E for bounded evidence help. These
consultations require the authored `consultation_question` and bind exact requester,
evidence and materials. H/E answer that question and return directly to W, without
ordinary A selecting the next research step. W uses `decision` for pre-delivery Final A. Consultation is a
serial job with `purpose=worker_consultation` and an exact W return binding. Its
return creates a successor W job with the exact preceding W and advice originals.
Current jobs freeze
`worker_continuity_version=1`: the selected
Backend may resume the original native W session after validating its original
workspace/home and the successor invocation. This is serial, not a nested call.
Unsupported capability, changed profile/backend, inherited sources, unavailable
shared home/session or required context rotation explicitly use a fresh context.
An uncertain or failed requested resume never triggers a fresh replay. Only the
verified native outcome reports same_session; planned resume is not success. Pi
currently does not support `consultation_resume`, so H/A/E consultation returns
explicitly use `new_context`. Same-logical-task fragment, repair and managed-execution
wake can still append to the exact original Pi session under their own gates. Ordinary A consultation does not consume an
assessment cycle, update main-line judgment, recommend a candidate or grant qualification.

New preparations with `max_context_windows` additionally freeze
`worker_fragment_policy.context_handoff_version=2` (public declaration:
`policies.worker_context_handoff_version=2`), independently of the advisory
scope. One initial window plus accepted completed compactions gives windows started.
Same-session resume preserves this count and deduplicates compaction events. In
the final permitted complete window, fresh occupancy at or above 90% only advises
a safe checkpoint. Compaction then allows useful work in the new window: handoff
is required only when windows started exceeds `max_context_windows` and valid
current-window occupancy is strictly greater than 70%. Thus max=1 allows one
complete window plus 70% of the post-compaction window; max=2 allows two complete
windows plus 70% of the next window. A compaction/count alone never requests handoff
under v2. Missing or stale occupancy remains unknown and adds no stop. At the
supported handoff boundary W seals a partial result for a new W, without automatic
A; new calls pause while original query/collect/reconciliation remain available.
Only handoff version 2 is supported. For standard Codex, the existing native
helper receives small control facts through required `PostCompact` and
`PostModelRequest` hooks and bounded assistant text on native stdout. Pi supplies
persisted-boundary metadata through its controlled observer described above. It does not
observe tool/input/output bodies or scan private rollout files. Required hooks
that are unavailable produce an explicit unsupported result before launch, rather
than repeated fresh W attempts with unknown coverage. These are capabilities of
the supported launch path, not a guarantee for arbitrary backends. Existing owners
retain time and original-call state. Context renewal does not reset shared tokens,
active time, steps,
execution consumption, permissions or unknown calls. W time and cumulative token
targets remain advisory; actual shared and per-experiment limits remain binding.
Explicit direct API `worker_fragment_policy=None` still disables ordinary fragments.
Protocols 29, 30, 31 and 32 require their matching old runtime. Protocol 33 rejects
older originals before scientific interpretation, without migration or settings
edits. Earlier A planning authority and consultation input selection must not be
reinterpreted under the current W-led contract. Historical trials remain unchanged.

### Scientific navigation and evidence memory

The existing accepted-prefix publisher appends H and W authored originals and
publishes bounded `indexes.research` navigation beside each accepted node. It
links the latest W understanding, H competing explanations, ordinary A assessment
and separate A consultation through their existing summary, scientific_result and
limitations originals. Unresolved questions remain the authors' own text. Existing
candidate, nomination and work indexes retain all saved candidates and declared
questions. Excerpts mark truncation and link complete originals. Control adds no
scientific summary, ranking, confidence inference, editable ledger database or
generic required scientific fields. Candidate availability grants no qualification. Private
budgets, bindings, cursors and tokens stay outside these scientific pages. Formal
independent review retains its neutral exact-subject inputs.

V3 W may maintain `research_memory` in the same derived research navigation:
selected competing candidate references, data and evaluation basis, exact evidence
with `supports`/`opposes`/`unknown` notes, failure conditions, open questions and
limitations. Current candidates and artifacts bind to already loaded observations
and retained materials; historical choices use exact accepted candidate lookups.
Candidate-independent `clues` may retain authored observations, applicability,
limited negative findings and open questions before a candidate exists. Each clue
requires at least one nonblank statement and nonempty exact evidence; a limited
negative also requires applicability. A clue grants no candidate qualification.
The framework neither ranks these selections nor reads evidence paths supplied as
scientific text. Invalid optional entries receive diagnostics and remain in the
complete original. A malformed update cannot erase the last usable memory.
Consultation returns, compaction and a fresh W retain the chosen question,
conditional negative results and original locators. Full scientific originals and
the complete candidate pool remain in their existing indexes; this view adds no
independent database or execution permission.

H consultations consume W's concrete question, relevant originals and candidate
gaps, returning useful alternatives and cheap distinctions, or no useful addition.
E consultations supply bounded evidence and limitations to that same W owner.
Marked ordinary A returns a diagnosis or no material issue, leaving the response
to W. Unmarked A retains its frozen contract. Final A independently assesses key
delivery claims, reproducibility and limitations under the existing exact authority.

Ordinary H/W/A/E tasks use three reading levels. The byte-bounded `lineage`
view keeps current focus, counts, recent progress and exact index pointers. Its
authored excerpts explicitly mark truncated text and omitted limitations; an
excerpt is not the complete scientific claim. `lineage.indexes.research` opens
the shared latest H/W/A understanding, limitations and supervision boundary on
demand. Its candidate link opens node entities and the previous-node chain for
all retained candidates, including those not nominated. Full originals remain
available behind these links. H prioritizes W counterevidence and revisable
explanations; W prioritizes its goal and useful observations; A prioritizes the
assessment question, progress and material alternatives; E prioritizes its
requester’s bounded evidence question. These reading priorities do not require
reloading the whole history on every turn. Formal independent review keeps its
separate neutral packet.

### Automatic diagnostic supervision

Direction supervision uses this same accepted ledger and controller, independently
of the agent backend. W may supply optional `exploration_rounds` entries containing
`mechanism`, `question`, `call_ids` and `finding`. One entry groups an authored
scientific experiment batch. The publisher checks the exact current-W calls and
their frozen mechanism bindings; known terminal observations can count, including
declared scientific negative findings. Unknown, ambiguous, unbound, duplicated or
previously counted originals do not create another round. Missing or invalid
optional declarations retain diagnostics and do not trigger output repair; calls,
shell commands, W renewals and completed W+A cycles are not inferred rounds.

The host appends immutable round facts and maintains the stable direction's
consecutive count and the exact boundary covered by A. Reusing `existing_ref`
preserves mechanism identity across W; control does not infer equivalent mechanisms
from similar labels. The public research index exposes bounded round excerpts,
evidence pointers, counts, covered boundaries and due reasons. Agent-authored text
cannot overwrite these host counters or mark supervision complete.

New W-first preparations default to frozen supervision policy 4 with explicit
`require_fresh_input=true`: review mode, four original calls as one trigger,
a 10% consumption floor and spacing, no fixed budget checkpoints and no default
review-count cap. Automatic A has a separate 25% soft admission ceiling in each
finite shared-budget dimension. These are diagnostic opportunities, not
stagnation verdicts. A receives the authored question, clues and exact evidence
under its frozen advisory scope; its consumption remains in the original shared budget.
Original-call notices and enforced reviews remain separate settings; each original
counts once across continuation and recovery. The budget basis is frozen at
preparation; unavailable usage remains unknown. Direct API `supervision_policy=None`
disables automatic supervision. These are experimental scheduling heuristics,
without measured scientific benefit, and do not classify stagnation. Detailed
fields and optional emergency caps belong to [Research configuration](RESEARCH_CONFIGURATION.md).

V4 compares the accepted authored question and exact original-receipt frontier;
a changed input provides a diagnostic opportunity, without a host verdict of
scientific progress. With `require_fresh_input=true`, consumption without fresh
input leaves an operational status notice and cannot automatically request A or
block W experiments. Explicit `false` permits operational opportunities without the fresh-input gate;
a missing flag in frozen v4 settings/jobs is rejected. Live call investment does not yet supply author/receipt freshness,
so the first long W is not interrupted by consumption alone. Counts, scores,
elapsed time and engineering failures alone prove no stagnation; ordinary A
distinguishes productive deepening from repetition lacking new distinguishing
evidence, and W decides how to respond. Its frozen ordinary-A request carries `policy_version=4`,
the exact source W, input cutoffs and original shared-budget scope. The UI
requires the complete marker and displays only allowlisted reasons and scalar
summaries; raw budget scope, private paths, native threads and authentication
never enter its reflection projection.

The V4 automatic-A ceiling uses actual separately metered tokens and active
seconds from every attempt of each automatic ordinary A, independently of its
scientific result. It compares cumulative cost per finite shared-budget dimension,
without subtracting normalized fraction maxima. An unlimited dimension does not
gate admission. Missing or incomplete usage stays unknown, retains known lower
bounds and suppresses only additional automatic A until original usage is
reconciled; it cannot lock W or original query/collect. Reaching the budget
ceiling or emergency count cap likewise suppresses extra automatic A. This is
a soft admission ceiling: in-flight A can overshoot, with no new cancellation.
Final A, formal independent review and W's proactive consultations retain their
separate purposes and budgets.

Unknown completion consumption cannot prove a positive cooldown interval.
During a continuous W invocation the existing advice producer also checks this
policy against one accepted head and the already owned live usage. A due review
pauses new-work admission and asks W to reconcile originals, checkpoint and return;
original query/collect remains available. After W returns, the existing serial
ordinary A receives the exact accepted cutoff. Call counts, elapsed time and low
scores do not manufacture scientific rounds or stagnation judgments. This does
not forcibly interrupt accepted work. For marked consultations, automatic A can
merge with W's A request while retaining `purpose=worker_consultation` and the
exact W return binding. Its automatic cost is counted once. W-requested H/E help
has priority; its returned W receives a useful live work interval under the
existing call/consumption thresholds before an older pending A. That interval
does not mark the pending supervision boundary as reviewed. Exact saved routes and frozen jobs retain pending requests on recovery;
only the accepted A covers its frozen ledger boundary. FINAL takes precedence.

The UI labels marked ordinary A as diagnostic advice returned to W, preserves
automatic trigger facts even for merged consultation, and never displays
`continue_existing` as scientific endorsement. E evidence consultation returns
to W; Final A remains explicitly distinct. Only allowlisted public scalars enter
the reflection view, without private bindings or raw budget scope.

The UI reads one replaceable compact execution view published by the receipt
owner, so a current W can show accepted/submitted/observed-running/terminal calls
before scientific sealing. Coverage and omissions remain explicit; these counts
do not alter accepted scientific statistics. GET/SSE retains the existing TTL and
never queries, collects or accepts experiments. Context displays use the latest
reported current-window tokens and native capacity, independently of cumulative
billing and completed-compaction counts.

For v3, the execution view takes the mechanism, declaration revision, batch
question and change criterion from each call's exact accepted research binding.
Later revisions cannot rewrite an earlier call's displayed purpose. The same
owner publishes compact declarations even before a call exists, and original-call
investment is shown separately from sealed scientific history. Missing or partial
observations remain unknown or lower bounds, rather than evidence of zero work.
Sealed mechanism trails use retained declarations and call snapshots, including
directions with no call and explicitly authored findings. Selected candidate and
evidence memory is exposed through the existing accepted research navigation,
with its data, evaluation basis, conditional failures, open questions and limits;
it is separate from nominations, A recommendations and qualification.

UI role and action labels follow the frozen purpose, strategy and delivery-review
scope. Consultation does not choose recommendations, decide an assessment round
or complete research. The pre-delivery assessment and bounded W correction are shown separately.
The UI consumes saved facts once and preserves source-aware memo invalidation;
it does not reconstruct evidence from call directories or native streams. Older
records retain their own meanings and explicitly missing optional views.

A positively proven prelaunch failure projects an actionable technical pause.
Explicit recovery binds the same job and expected attempt, rechecks native
capability and preserves all original budgets and receipts. Unknown submissions
cannot use this path. Harbor retains the original container only within its
original finite deadline while observing explicit recovery; cancellation cleans
exact bound owners under the writer fence, retaining `cleanup_unknown` if proof
is missing. A technical pause or cancellation is not a scientific conclusion.

Marked A receives `supervision_request` and returns advice through meaningful
`scientific_result`, `continue_w` and `decision_request:null`. The redundant
`supervision_result` is optional and control never fills it into the original;
explicit conflicting markers or routes are rejected. Advice does not endorse
the current direction. Triggers prove no scientific failure. W chooses whether
and how to adapt or consult H/E. Errors in other required scoped decision fields
still use the same original's bounded output repair.

### Materials and candidate identity

Reports and reusable diagnostic scripts belong in `artifacts/`; `work/` is scratch.
H's `minimal_test` accepts nonempty text, objects or lists. Derived mechanism
views render structured descriptions as JSON text while preserving original
scientific bytes. Invalid commitment diagnostics identify the precise field.
Ordinary A/H relay their current material references through existing input bindings,
including exact E request/return and selected forks. Null preserves that working
set, `[]` clears it, and explicit IDs replace it. An accepted empty set is not
refilled from older parents. New W outputs refresh the automatic set; W explicitly
selects prior implementations needed after its result. Only references travel,
not managed-original copies or entire ancestor workspaces. H/A worker consultations
are the exception to selection replacement: their authored `implementation_selection`
is ignored for control, while the requesting W's exact selected inputs and material
references are retained for the returning W. Authored raw responses remain unchanged.
Implementation options
show selection IDs and usable paths; compile arguments name paths, not IDs.
Existing current-input bounds still apply.

Private jobs and relay records store references to existing producer material
lists and selected IDs, rather than repeating each file descriptor. The operation
owner resolves only those selected lists once and passes the resulting facts to
consumers; ordinary control-record reads do not implicitly load material lists or
walk history. Each produced material records one original source path.
The publication namespace `experiments/<accepted-call-id>/` belongs to control.
W cites retained managed outputs and publishes new reports outside that namespace.
An authored package occupying it is returned for delivery correction, preserving
original calls and outputs without rerunning experiments.

Public progress uses accepted node identities independently of job-body size.
The mechanism ledger displays W-authored identities and exact source revisions;
historical H declarations remain distinct.
Candidate downloads keep the exact sealed member and saved call-output binding,
including originals retained in place. FINAL submission and confirmation remain
distinct actions; only confirmation of the active FINAL marks delivery complete.

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

With H search commitments, `prepare --candidate-extra-slots N` freezes optional
preview guidance for extra nominations (default 2). W may declare representatives,
extra alternatives or gaps in `candidate_nominations`, without repeating a complete
mechanism table. Representatives use mechanism_ref, candidate and reason; extras
use candidate and reason; gaps use mechanism_ref and reason. Current-call candidates
may use `{call_id, artifact}`; historical candidates use existing scientific references.
The preview resolves recognizable entries and candidate identities. Omitted or
unreadable nominations do not trigger output repair or restrict the full candidate
pool. Unreadable nonempty advice retains nonblocking diagnostics and its original
response pointer in previews and the UI; compact views retain a count and index.
No candidate identity is inferred from a label or a declared file path.
Cross-mechanism observed findings cite an exact retained `call_id`; a comparative
call may support multiple author-declared findings without another experiment.
Mechanism membership, suitability and gaps remain scientific judgments;
A assesses the material evidence and gaps relevant to the requested judgment; no final mechanism inventory is required.

The latest sealed W declaration for each mechanism supplies the comparison
preview and paginated nomination index with its own original source. Local
returns retain other mechanisms' prior representatives and gaps; new mechanisms
remain pending without a synthesized global judgment. Extra alternatives come
from the latest W declaration. The
retained candidate pool, experiments and exact materials remain in their existing
paginated indexes and delivery catalogue, without a nomination count limit.
Final A judges the proposed candidate against the requested claim and may recommend an
exact retained candidate for delivery when supported. The full pool remains
available for a decision-changing comparison, without mandatory reranking; the
sealed W declaration is unchanged and no new experiment is implied. Tasks without H search commitments
do not invent mechanism coverage.

Bounded delivery correction uses `--mechanism N`, taking the positive decimal
number from its frozen task catalog. Control binds that number to the exact
mechanism and H/W source; display labels never resolve identity. Refusals expose
the fixed public reason and available numbers from the same admission calculation.
Ordinary research uses live `--research JSON_FILE` declarations instead.

Scientific commitment and disposition lists have no fixed four-item or 128-mechanism
capacity. Exact original identities, frozen catalog/focus, accepted-call consumption,
finite execution budgets and unknown-call reconciliation still govern execution.
Initial-opportunity and extra-grant counts remain historical observations and planning
advice. Paginated indexes retain all mechanisms and their original scientific text;
the compact navigation view remains bounded.

Mechanism status labels and their historical explanations are not a new A control
surface. Scientific support, unresolved alternatives and possible exclusions remain
in the authors' exact evidence and claims. Temporary underperformance or missing
evidence does not establish a scientific negative. W chooses relevant competing
explanations and distinguishing work; control does not classify scientific merit.

Final A's requested assessment concerns the original scientific goal and a concrete
candidate or claim. Final A may request its one bounded decision-changing correction; W chooses how to resolve it. H consultation can reconsider explanations without proving the old route
impossible. Ordinary research declares
or revises mechanisms through `--research JSON_FILE` in the same session, without a W
handoff. Bounded delivery correction retains its frozen catalog and permits no new
live declarations. Local algorithm and representation adaptations remain W work.
A requested assessment preserves W's exact question and factual missing/blocked evidence; merely choosing
`stage_intent=decision` does not mark observed work incomplete or confer qualification.

Custom and prepared role literals stay frozen. Purpose-specific task rules are sent
once through the selected Codex developer or protocol prompt transport; current
planning advice cannot authorize new work during protected FINAL or output repair.

Mechanism indexes retain exact H/W declarations, observed calls, nominations and
scientific originals in stable identity order. Full history remains available on
request; it is not an instruction for A to reread every mechanism or every work item.
Completed assessment counts, pending W and opportunity consumption are historical
observations, not quality rankings, execution grants or mandatory rotation rules.
Recovery and output repair add no assessment cycle; a fork uses its exact cut.

A-authored `search_review`, `portfolio_review` and `final_coverage` remain readable
scientific text when supplied, but are not planning or admission authority. They do
not change W/H focus, allocation, mechanism dispositions, extra opportunities or
active research work, and missing fields do not trigger output repair. No complete
portfolio comparison or per-mechanism final inventory is required. W continues to
own mechanism revisions; formal qualification and exact delivery checks remain
separate. Candidate presence and successful calls do not establish scientific depth.


Narrow output repair changes only diagnosed JSON/layout fields and returns the
complete original JSON with the other scientific plan unchanged. Repair budget
hints describe the current repair allowance: `remaining_observed_tokens`,
`remaining_active_seconds` and `previous_consumed_observed_tokens` retain consumed
usage; `token_usage_unknown` preserves missing metering. That allowance neither
requests nor authorizes replay of an earlier experiment. New prompts use these
field names. Earlier protocol instances are rejected without compatibility reading or migration.

Budget choices use current ordinary-work availability and the runtime
`work/budget-status.json`. With protected FINAL, provider-reported consumption
determines the token balance; missing billing remains unknown without an estimated
charge or an old closeout reserve. Unresolved execution still requires exact-call
reconciliation. Earlier bounded-closeout formats are unsupported. Call
opportunities do not reserve resources or guarantee coverage. Bound expensive calls
with the existing execution configuration and W stage targets; no automatic breadth
share, minimum mechanism count or new execution permission is introduced.

W revises only changed declarations; omitted declarations remain, and A advice
stays in its scientific original. Repair binds the stable mechanism to one exact direct W observation,
or to an exact original accepted input selected through `implementation_selection`.
Multiple possible observations require an explicit exact selection; equal output
bytes, labels and recency cannot select the source. Shared archive paths
cannot prove logical input identity; such outputs cannot disambiguate a repair.
The old W, call, accepted and
terminal originals keep their own mechanism-source seal; the current authorization is frozen
separately. Known process success can still require scientific repair. Unknown
execution, missing or changed originals, ambiguity and exhausted repair allowance
remain barriers, reported in the author's control outcome before a dependent W.
No old screening qualification, spent opportunity or budget is transferred or
refunded. A may use `continue_w` with a concrete evidence question and change
criterion; W retains its focus and chooses any revision. Prior stage usage remains
consumed; H/W revisions cannot reset budgets.
Initial/grant counts remain historical observations and planning advice; they no longer gate scheduling or reserve first-screening opportunities. Exact call identity, real execution budgets, waived mechanisms and the frozen focus still govern admission. Runtime budgets and execution permissions come from operator configuration and the control ledger. H proposes revisable explanations; W owns research choices against the control allowance; A answers the requested scientific judgment. H/A resource estimates and suggested call/audit counts do not become runtime caps or H-renewal triggers. Scientific design counts, user/task requirements and actual per-call limits retain their meaning.

Ordinary W guidance favors incremental implementation and validation, sizing the next work from evidence and current budget. Pending original tool sessions normally use finite 10–30 second waits within actual deadlines; this is advice, not a new polling policy. The actual handoff still governs finalization, host waiting and reconciliation. Repair, protected FINAL and author-only returns retain their original boundaries.

H offers revisable scientific mechanisms and method suggestions, without making a provisional method an extra boundary against W revision. W owns experimental design, implementation, parameters and batches within the original user/task constraints, frozen dispatch/catalog, explicit focus/evidence gaps and execution permissions; A proposes necessary result validation. Ordinary A's `continue_w` returns a diagnosis; W decides whether and how to respond. Within that direction, W observes, runs experiments, compares and adjusts through a coherent exploration before independent A judgment. A single completed experiment, weak score or locally repairable issue does not require a role handoff. Existing reports and indexes carry local observations, counterevidence, unresolved combinations and questions with exact source locators, alongside reusable drivers/helpers and residuals. The next A or H checks how its previous decision actually changed the work and what the resulting evidence supports; no new report schema is required.

Each next batch addresses an unresolved judgment with a discriminator and outcomes that could change the next step. W may continue, seal a handoff to a new W, or request H/A consultation as needed; a mechanism or scope change does not require A first. When ready for finite closure, W requests `decision` with explicit limitations: this requests pre-delivery Final A. Final judgment remains with independent A. Remaining token/time and suggested downstream A needs guide planning without adding quotas or reserves. Token and time targets guide current-batch closeout; the context-window policy seals the current partial result and starts a new W at its observable boundary. Actual execution limits and consumed budgets remain binding.

Initial `screen` fairly compares plausible mechanisms through informative shallow checks. Unresolved alternatives remain explicit while W chooses discriminating follow-ups within the frozen focus and original task constraints, without requiring every alternative to be resolved before deepening. Ordinary `improve` concentrates on one scientific mechanism, rather than an algorithm label, and does not end merely because a suggested comparison is complete. `validate` stops when its assigned gap is resolved or blocked, and `repair` stays bound to the exact retained scientific observation; neither expands into a new search. These work-unit definitions add no experiment quota or required reporting fields; user/task counts, stopping conditions and purpose-specific response contracts remain binding.

Task navigation uses a direct parent conclusion as the primary reading route only when its frozen source seal matches the historical source. Its optional `history_alias` retains the alternate text-page route; the two representations describe the same scientific source, not necessarily identical bytes. Full historical pages and complete indexes remain available. Agents inspect JSON shapes and selected fields, analyze numerical arrays with their own local code, and reread missing or conflicting evidence as needed. The ordinary assessment or Final A requested by `decision` under the frozen strategy retains independent A; control does not author scientific summaries or impose a read-once limit.

Historical index entries identify retained originals; they do not mount every artifact into the current task. All ordinary roles can return `implementation_selection` with exact listed artifact IDs from their frozen history. Control accepts the selection after the turn and binds those originals to the next task. Select needed originals before requesting E; E can select newly identified originals for its returning requester. Prose evidence requests do not select inputs. Private execution records are outside this scientific artifact interface. Execution commands address the current W's call records; a scope refusal describes the operation request, without changing the original call's known or unknown state.

E returns its answer and limitations to the exact H/A requester for a fresh scientific decision. The original extraction question remains in the requester parent; control does not reissue that completed action as the returning task's instruction. Another useful evidence question remains allowed. An unchanged unavailable host capability remains a limitation rather than a reason to repeat the same extraction.

New ordinary A tasks may include an `assessment_handoff` naming the exact current W/H and the prior A reached through their frozen decision route, or the explicit extraction/audit requester. The prior assessment is context, including after an H scope revision; it grants no qualification. A independently checks the current evidence and decision-changing gaps, reopening historical evidence when coverage is missing, results conflict, or scope/evaluation criteria change. Local tools and selected-field/difference/statistical queries keep numerical grids and repeated source bodies out of unnecessary tool output; needed originals remain readable. Useful A review scripts and small reports must be declared under `artifacts/` to be retained. The exact prior A's declared artifacts can enter the new A's readonly input view through existing original bindings, without copying its work directory. If that optional set exceeds the existing combined input bound, it is omitted with an availability reason while the assessment originals remain available. Existing frozen tasks retain their original inputs; author-only, correction, formal review and FINAL keep their existing scope.

Each task freezes actual optional consumers. Extra scientific content stays in the
original; optional explanations do not create format-repair gates. W delivery uses
exact retained observation facts, with actual missing/blocked evidence and host exceptions preserving partial results.
An explicit decision request alone does not mark observed work incomplete. Missing
real work can still require bounded continuation; a repetitive findings table is not
required. Scientific judgments remain in exact authored evidence. Actual actions, final routing,
input selection, independent qualification and execution permission remain explicit.
Omitted optional progress advice neither invents progress nor accumulates an unknown
pause; an explicit unknown retains its configured meaning.

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
existing partial result, including in FINAL. Target attainment is not a completion
gate; the authored mechanism-coverage declaration remains required where applicable.

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
directories, rejects symlinks and special files, and retains limits of 4,096 files
and 8,192 scan entries plus finite IO deadlines and canonical-path checks. Code
bytes have no framework per-file or aggregate byte quota. It does not capture the experiment working
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
Sealing closes a node's verification. Ordinary handoff, loading and restart
read its saved results, observations and accepted control decisions without
revalidating the historical files or rerunning scientific gates. Controller
route and search cursors carry accepted decisions forward; a missing derived
cursor can be rebuilt from saved facts without granting new qualification.
New verification covers the current node and its actual declared inputs,
including recovered materials, independently of the historical node count.
Current inventory checks reuse the frozen policy and accepted receipts. Pending
execution capture follows the current job's exact authority references; allocation
and request originals are accepted with those references in the existing control
ledger, including allocations made before preparation starts. Sealed A decisions
and an existing FINAL source selection are reused. A new A still performs its
required comparisons, and the first FINAL selection may read its eligible sources
once.
Changes across the framework must preserve one producer for each immutable
control fact or accepted scientific view. The operation owner loads and performs
necessary acceptance once, then passes existing values or original references
explicitly to internal consumers. Consumers reuse those facts without reopening
the same records or repeating acceptance. Loading, acceptance and computation
have separate interfaces: loaders acquire originals and filesystem observations;
validators compare those values; consumers derive bindings and plans without
filesystem reads, metadata probes or lazy record lookups. Existing publishers
own necessary writes and retain their transaction boundaries.
Mutable control state is observed at
the actual writer or admission boundary; mutations return the new state for
subsequent consumers. Every new node freshly selects and binds its effective
configuration; retained nodes and continuations keep their exact bound revision.
Direct parent jobs, seals and authored responses are produced once for the
operation, with authored bytes kept separate from control and public projections.
Each migration retains an explicit implementation scope;
other discovered violations are recorded for separate changes.
Source restrictions propagate saved input and output facts; passive provenance
reads do not reopen producer execution or ancestry gates. Missing restriction
facts remain unknown, and restricted sources cannot become clear by inheritance.
History navigation and readonly mount bindings reuse the recorded originals.
Checkpoints do not seal current drafts. Fragment checkpoints neither retain draft contents nor
restore deleted or lost drafts; continuation depends on drafts still present in the existing workspace.
Required selected-control checks remain, and output errors do not rewrite a known
terminal outcome. Current standalone Local calls without `code_version` retain
their declared input contract; absence of a Git code binding alone does not make
a call an older protocol.

### Read ownership and selected originals

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

The fork and deletion rules below describe their source behavior. Focused source and
synthetic checks do not establish complete live validation of default native,
registered Cov, Codex/namespace, replay/model, solver or SSH paths. Those paths need
their own exact independent CLI evidence. Source changes establish neither scientific
qualification nor a real sandbox guarantee.

The default local command backend consumes the selected provider's permission
projection through the existing native execution bridge. Pi uses its Linux mount
projection described above, without Codex configuration, binary or authentication.
On the standard Codex path, it resolves that installation's native binary and uses
its sandbox with a restricted scientific subprocess file view. The
accepted Git code export is read-only at its logical scientific code directory;
pre-Git calls retain their saved directory/script snapshot behavior. The backend
receives runtime files, the scientific workspace and a task-native permission
configuration, without native authentication, provider configuration, control
originals or the submission slot. The native Worker
and backend honor the same supported legacy `workspace-write` declaration, including
declared network access and temporary-directory exclusions, interpreted equivalently
for their respective working directories, `TMPDIR` values and namespaces. The
Codex native Worker retains `--sandbox workspace-write` and the captured legacy
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
owns its supported fields and permission limits. The whole base directory is
mounted read-only at its original path, including untracked/ignored files, with
its root `.git` masked. There is no base-data copy or overlay filesystem. Its
SHA-1/SHA-256 HEAD identity is immutable across configuration revisions and forks;
use an independent ordinary clone dedicated to the research, without committing,
switching branches or moving HEAD during research. Linked worktrees are unsupported.
The frozen HEAD establishes code identity, not adversarial content-integrity
verification; untracked/ignored contents remain the host's current files. HEAD/index
consistency is checked globally, with ordinary Git dirty checks and stable Git control paths.
Namespace launch does not inspect each base-file source or construct per-file
mounts; Git metadata checks still scale with tracked entries. Git filters and index
optimization flags retain their normal semantics. W and runs exclude
editable/scratch/output roots from dirty checks; read-only roles exclude
scratch/output roots. Missing or dirty required sources affect only later
consumption that needs them, not known terminal records or metadata-only queries.

Project draft exposure is limited to declared editable code. Initial tracked-code seed and a
uniquely selected retained parent commit use the fixed shared code limits in the
[project configuration contract](RESEARCH_CONFIGURATION.md#declared-projects--声明工程),
which do not limit read-only base contents. For a new declared project, `config-check`
and `prepare` preflight the initial tracked editable seed with file stat metadata and
entry counts, including editable roots and implicit parent directories, and report
limit violations during preparation. This quota preflight reads no file contents and does not scan
untracked/ignored contents. Initial seed retains its limit checks; runtime capture
checks all actual editable contents, including ordinary untracked/ignored entries.
Later additions or larger files can still prevent a node or call from starting.
Ambiguous or absent parent selection reports a frozen-base fallback. Existing jobs
keep their current drafts. A local project run captures all editable roots into one `code_version`
rooted at `project`, then mounts that exact export
read-only; empty roots use read-only tmpfs. Scratch roots are temporary tmpfs and
output roots map to the original call's artifacts with full relative paths.
W may write only its exact editable/scratch/node-output targets in the base view;
other roles receive only the read-only base, without W drafts. Scientific input
protection is applied last. Missing declared mountpoints and necessary parents
are created as empty host directories before launch; a non-writable host base
requires those directories to exist already.

Run cwd may be the exact base root; the backend's internal permission policy
retains the task-work cwd and adds only declared scratch/output writable targets.
The native Worker retains its frozen provider policy with exact project targets
projected into it: Codex `workspace-write` or Pi's supported workspace-write
mount projection. Unsupported native permission or launcher
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

Protocol 33 protects shared inputs at the final native launch boundary with Linux
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
It defaults to `enabled=false`, with every limit null. Enabled `max_scan_entries`
and `max_files` retain their count limits. The positive-integer legacy byte fields
`max_file_bytes`, `max_total_bytes` and `max_instance_bytes` remain accepted and
frozen, but impose no file quota. The same applies to legacy
`max_output_item_bytes`, `max_output_total_bytes` and provider file/total/log/result
byte fields; they are not active file-size controls in the UI. Scientific originals,
arrays, artifacts, scripts/code and logs have no framework per-file or aggregate
byte quota. `size_bytes` remains an exact identity fact: declared-size mismatches,
changed originals and actual truncation still fail validation. Compact handoffs,
declared-result documents, delivery manifests, control metadata and public views
remain bounded. Finite execution/IO windows, file/entry counts, explicit permissions,
canonical paths and symlink safety remain effective.

Participating writers retain the existing storage lock without scanning instance
usage for byte admission. Immutable aliases must still bind the exact same-instance
inode, and independent originals retain their normal publication and identity
checks. Code capture/transfer and log publication use chunked IO within finite
operation windows; removing byte quotas does not promise unlimited RAM or hard
isolation from same-user host processes. Necessary original-call and recovery
receipts retain their existing owners and consumed budgets.

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

## Implementation ownership

All listed files are in `src/scidiscovery/research_loop/` unless stated otherwise.

| Responsibility | Owner |
| --- | --- |
| Preparation, routing, pause | `controller.py`, `runtime.py`, `instance_control.py` |
| Unified configuration and backends | `research_config.py`, `backends.py` |
| Detached control and user direction | `control_operations.py`, `user_guidance.py` |
| Native work, continuation, usage | `native.py`, `execution.py`, `continuation.py`, `native_recovery.py` |
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

## Managed execution, budgets and final reporting

- Scientific authors own candidate comparison and selection. Control preserves all
  observed candidates in first-observation order, including ties and missing metrics.
  Compact previews report mechanical omissions and link complete originals; they do
  not prune the delivery catalogue. Policy version 2 is exactly `{"version":2}`;
  the catalogue starts no domain calls. A delivery names an exact member and
  explains its choice in `scientific_result`. `candidate_comparison` is optional
  scientific explanation; its rows, `keep`, rationale and evidence-step formatting
  do not grant or gate delivery. Actual audit and delivery actions still require
  an exact available candidate, qualification and execution permission. Null
  does not authorize delivery. Protocol 33 rejects earlier frozen protocols without
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

Final A assesses every original objective and authorized scope requirement. Key
claims cite locatable observations and experiment conditions, distinguishing
measurement, inference and assumption, target metrics, proxies and sensitivity
checks. Relevant comparisons use the same goal criteria and retain conflicts,
uncertainty and missing inputs. Workflow completion, audit qualification,
exhaustion, `finish` and delivery do not establish an unmeasured goal. Final A
reports unmet objectives and may reject all candidates even when sources exist.
Only exact `deliver` selects a candidate under the qualification and execution gates.

W uses H/A evidence, limitations and exact references to decide mechanism changes
and the next useful discriminator, checking concrete conflicts as needed rather
than routinely repeating A's full-pool review. Final A first matches prior A unresolved
questions against the latest W changes, reusing reports, result arrays, executed
scripts/code and the full-pool index. Read conclusions, then keys/shapes and exact
source locators, then needed fields; complete originals remain available for gaps,
truncation, conflicts or facts that can change the decision. Independent judgment
does not require repeating old observations, recomputing retained comparisons or
repackaging old evidence. New or conflicting execution facts are checked at the
original calls; parent claims do not establish qualification. Final A handles minor
issues through reuse or light checks, without a default extra W round. Targeted
checks or refits may address concrete conflicts, numerical warnings, missing
evidence or questions likely to change a decision, with expansion as needed.
The shared A evidence guidance governs strongest-rival comparisons and finite
closure: sufficient evidence permits a finite choice with stated limits, including
rejecting all candidates, without routine extra sensitivity analyses.
Within `improve`, W completes a useful,
coherent batch of implementation, experiment and check followups. Direction changes,
stalls, scope changes or blockers may motivate H/A consultation or a new W handoff;
they do not force an A assessment. W uses `review` for ordinary A diagnosis and `decision` for Final A.
Each small finding need not cause a
new A/W cycle. Neither role must invent filler work, exhaust budgets or meet an
experiment quota. `validate` remains bounded to its assigned decision-changing
gap and `repair` to the exact retained scientific observation; iterative research does not
expand either unit's permissions. Reused artifacts and
helper dependencies use the current task's declared input paths; old author
workspace absolute aliases are not current locators. Authors publish reusable
diagnostics and their executed source and necessary
helpers through the existing declared outputs, following task code and project
write rules. Managed originals are cited directly. Unpublished diagnostics remain
a stated gap, without a new nonempty-material or repair gate.

Final A explains candidate criteria, any revisions, supporting evidence and tradeoffs in
ordinary scientific prose. selection_explanation is optional and has no nested
completeness gate. Exact candidate identity, frozen inputs, audit qualification and
delivery authorization remain required. Output repair addresses required fields,
unusable author-owned action references and diagnosed delivery-layout conflicts,
retaining the same task, cumulative
usage and FINAL completion protection. The original author may remove redundant
copies or separate its own conflicting files; managed originals remain unchanged.
Managed outputs persist and can be referenced directly, without copying them into
the author's artifacts. Identity, I/O, unknown-execution and authorization failures
remain control errors and do not authorize an author retry.

Scientific final-output parsing accepts one leading BOM and a whole, single
plain or `json` Markdown fence. Surrounding prose, multiple objects and malformed
JSON still require bounded author repair. The raw bytes remain unchanged; sealing,
historical reads and display use the same scientific parser. Only a normalized
form adds `scientific_output_normalization` to the native outcome; control JSON
continues to use its strict reader.
Parent `inputs/parents/<step>/conclusion.json` may alias those raw authored bytes,
including a BOM or fence. Programmatic consumers must use the same narrow scientific
parser rather than assume a directly readable JSON document.

Standard single-command `codex` CLI preparation freezes up to two same-task
output-only repairs, a separate bounded automatic-recovery policy and ordinary W
fragments by default, binding the original native home privately so configuration,
authentication and permissions remain unchanged. It freezes the absolute invoked
launcher and its target without replacing invocation symlinks. If no node policy
was supplied, per-role soft targets reference the original global ceilings; they
allocate nothing. Explicit policies override this default, including disabling
repair; custom launchers and direct controller API calls remain explicit.
Ordinary managed-execution wake uses the independent `execution_wake: true`
full-W-task permission, also supplied by the supported Pi defaults above.
When W normally returns scientific JSON while an original managed call is still
pending/running, control retains an unsealed waiting checkpoint and monitors that
call without a native turn. Only verified terminal facts for those exact calls
may resume the same W session. W first consumes the original results and may then
submit new useful calls within the original task scope, permissions, budgets and
normal admission gates. At its frozen context boundary, W instead seals the
partial result and hands off to a new W before starting another batch.
Ordinary execution wakes and fragments use their separate full-task permissions;
neither consumes output-repair attempts. Cumulative shared budgets, existing step
limits, permissions and original home/session/context bindings remain checked.
An output-repair turn still prohibits new experiments. Author closeout, FINAL and
other restricted tasks retain their own scope; ordinary wake grants no failed-task
retry or scientific work repair. Native same-user shell access remains a soft
boundary. Transport completion grants neither execution permission nor scientific
qualification.
Old frozen output-only policies without the flag gain no wake permission; explicit
full-resume policies retain their existing wake behavior unless explicitly disabled.
The W task reports frozen wake permission and normal pending-call handoff instructions;
this does not force a live W to yield or prove that its session is usable. Missing
permission is diagnosed before host monitoring waits for terminal completion, while
preserving the original checkpoint and call.
An explicit `caller_error` with `accepted=false` returns `correct_request`: fix the
reported inputs or options before a corrected request, preserving any separately
accepted call. Failure of an operation on that call does not erase a verified original
terminal state. Known non-integrity failures before an existing-call provider operation
are reported as unavailable, including local loading or resource checks; required review
keeps its own status. A lost submission reply remains unknown under the same call ID. Arbitrary backend
exception text stays in host diagnostics; public feedback reports a safe operation and
error category. The Worker client preserves the host handoff. Local ignores candidate
parent/change annotations without inventing
parent lineage; executors that actually consume a parent still validate it.
Duplicate argv item/count ceilings are removed; the real message transport bound
and string/NUL requirements remain. A missing call ID alone
does not establish nonacceptance; other unresolved facts retain reconciliation.
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

`native_recovery.py` classifies structured native failure fields and narrowly
matched native failure envelopes, then supplies one bounded recovery decision
through the existing continuation/controller path.
Intermediate top-level `error` diagnostics do not override a later successful native endpoint: a unique completion, zero exit and confirmed cleanup resolve those earlier diagnostics. True failed/incomplete/interrupted turns, errors or activity after completion, duplicate completion and missing terminal evidence retain their existing failure handling. The same terminal interpretation is used by collection, repair admission, history validation and usage coverage. When retained pending bytes match the original final message and terminal counters, the ordinary collection path can seal that same call without another model turn; it preserves the original outcome, pending output, usage and attempt identity.

Recovery preserves native diagnostic categories: temporary, incomplete, permanent
or unknown. When the original session is safely available, each category may use
bounded backoff under the same task; classification alone does not force an
immediate stop or turn permanent failures into temporary ones. Waiting cannot
repair authentication, quota or configuration. Structured native codes and bounded
failure envelopes remain evidence; permanent quota facts dominate generic
capacity signals. The retained Codex routing-discovery failure envelopes and Pi's
exact native-boundary `fetch failed` remain connection diagnostics, not permission.
Ordinary agent/tool text supplies no recovery authority. A startup retry without
a session requires explicit native temporary failure and non-acceptance evidence;
missing identity is no proof of nonacceptance. Existing-session retry needs no
`not_accepted` claim. Cleanup, identity, permissions, pause and all original
budgets remain mandatory. Unknown external effects require same-call
reconciliation and cannot be automatically resubmitted. Known completion is never
reopened as native failure. Native transport and synthetic-process evidence does
not qualify real-provider reasoning recovery, complete usage or scientific results.

A completed turn with incomplete
required output uses existing output repair, while legal fragments keep their stage semantics.
A completed W response with valid required JSON can separately need scientific
work: an exact `work-evidence.json` assessment of `incomplete` may use the
independent automatic permission to continue the same original full W task. This
uses the same session, task and retained observations, binds the original response,
outcome and evidence snapshot, and consumes `max_continues`. Missing required format or
conditional breadth declarations use narrow output repair; a repaired valid response
may then need this independently authorized work continuation. Valid partial findings,
explicit scientific blocks and partial findings retain the declared stage intent.
`decision` requests pre-delivery Final A.
A final typed caller rejection of a run or compile request can reopen unfinished
work in the same task/session under the existing automatic policy, including
improve and no-search work. An unknown command is returned verbatim for the author
to correct; control does not infer the intended command. A later accepted request suppresses that correction;
unknown accepted execution only permits same-call reconciliation. This exact mechanical
refusal takes precedence over that response's decision/blocked handoff under the same
finite allowance; without it, the declared stage intent governs scientific
handoff, consultation or `decision` assessment. A live
tool hint alone does not establish this terminal repair permission.
Complete or inapplicable delivery assessments add no recovery action.
FINAL and closeout use the same native collector for interrupted or accepted
unstarted attempts. An exact host budget refusal before submission can enter
FINAL with the full original attempt chain and consumption retained.
An enforced host review keeps its existing priority over work continuation.
Exhausting the automatic continue cap or the original budget records a verified partial exit for A; it
creates neither completed findings nor a scientific screening verdict.
`output_only` retains its narrow meaning; `automatic_recovery` is an independent
permission. Explicit older resume policies without it gain no permission; custom
or multi-argument launchers and the protocol backend are unsupported for this path.

New default automatic recovery shares a total of eight automatic attempts,
with per-action caps of eight and exponential backoff from 5 to 300 seconds,
0.1 jitter and 1,800 seconds cumulative service waiting. Explicit frozen policies
retain their declared limits; future configuration revisions cannot reset the
original job's counters, waiting or consumed budgets. The
[configuration reference](RESEARCH_CONFIGURATION.md#automatic-recovery--自动接续)
owns all recovery fields and defaulting rules.
The existing controller writer owns arbitration and actual
monotonic waiting; no recovery runs without it. The original attempt retains the
reason, counters, actual wait and UTC next-attempt time. Downtime adds no service
wait, and an unmeasured crash tail remains uncertain. UTC expiry permits renewed
arbitration and budget admission, not launch by itself. A service wait takes one
fresh budget snapshot and derives its shared-time deadline locally; active time uses
BOOTTIME and elapsed time uses Unix time. The waiting loop still checks pause each
second, measures service waiting with monotonic time and rechecks admission before
launch. It does not repeatedly rebuild the full budget snapshot. Pause stops service
waiting and preserves its original schedule and recorded consumption.
Server retry hints exceeding the single-backoff or remaining cumulative limit
stop recovery instead of being shortened to bypass that limit.

Live or unverified native processes and running/unknown managed calls block a
replacement native attempt; existing exact-call reconciliation remains responsible
for those calls. Recovery preserves the original model/profile, home, permissions,
accepted-call identities and consumption. It neither resubmits unknown external
work nor grants more budget. Formal completion, explicit waits and user pause keep
their existing boundaries. Automatic scientific-work recovery is disabled for
protected FINAL and closeout; their existing finite output repair remains separate.
The same bounded output-format negotiation may retain their exact original final
claim, without adding a role, claim or scientific allowance. Ordinary closeout
collects the original live attempt or an already successful original result; a failed
or unknown original stays unsealed with a specific recovery diagnostic, except for
an independently rechecked eligible output-format negotiation. Unavailable
fragment continuation and output repair retain their own reason instead of entering
automatic work recovery or acquiring a new task. The UI projects
waiting/stopped recovery reason, next attempt, separate counts, service wait and
limitations without treating service waiting as scientific failure or owning a
second state ledger. This adds no required scientific output or protocol change
and does not replace the general scientific budget clock.

New `prepare` freezes `final_a_completion_policy` version 1. Each ordinary state
checks whether its remaining budget can start the next action. This mode reserves
no shared tokens, time or steps for protected FINAL and does not prepay the next
node token target: positive reported token balance permits ordinary token admission,
subject to the original finite time, step, integrity and execution gates. All H/W/A/E
node token targets and W stage targets are advisory: reaching a target requests a
bounded conclusion without rejecting new managed experiments or forcing A review.
W's context-window review gate remains separate when a fragment policy is enabled.
Missing billing or unknown metering coverage alone does not block ordinary
advancement: reported usage remains a lower bound and gaps remain unknown, without
zero replacement, refund or replay authority. Unresolved original execution,
submission and cleanup retain their separate reconciliation gates.
For an eligible standard Codex launch, a local
[PostToolUse hook](https://learn.chatgpt.com/docs/hooks) reads the current
`work/budget-status.json` and injects a short developer reminder at the next local
tool boundary. It does not interrupt an ongoing reasoning request or turn a soft
target into a hard kill. Unsupported hooks or failed eligibility checks retain
file-only advice with an explicit limitation; control changes neither user
configuration nor native permissions. The reminder grants no execution authority
and never resubmits an unknown call.
Notice starts at 80% of the node's advisory token or time target (`near`). H uses
the existing W+A role token advice as its follow-up reference, and W uses A's;
reported shared token balance at or below that reference also prompts a notice.
Shared active time prompts a notice only when strictly less than 10% of the total
time limit remains. Token and time risks each receive one notice per logical node
across resumes; a token notice does not suppress a later time notice. Ordinary W
can also receive one independent review-window notice, separate from those two
risk notices. Marked ordinary A closes its diagnosis and returns advice to W without
expanding its action set. For Final A, actual exhaustion requires a conclusion from existing originals
without new H/W/E work. These thresholds remain advice,
not hard quotas or new reservations.
Missing or stale budget advice does not grant allowance or deny execution;
exact identity checks and independent execution authorization still apply.
Shared budget checks for new nodes remain unchanged. Author
closeout token permits are also soft targets; their finite time limits remain
enforced. Actual usage remains accounted without creating a new shared allowance.
Protocol 33 accepts only soft node settings and final-A policy v1. Older protocols,
hard settings and missing final-A policies are rejected without migration. A denied start
advances the existing control journal to durable `FINAL` (`closeout_begin` mode
`final`); it never reclassifies an ordinary A as final from the remaining balance.
An H `wait` with no pending or unknown external execution enters the existing
durable FINAL phase; pending originals retain their waiting mechanism. This
uses the required frozen final-A completion capability; it does not invent a
scientific conclusion or execution grant.
W `decision` enters pre-delivery Final A while original allowance remains;
exhaustion enters protected final-only reporting. Final A retains its own
completion policy without another exploration cycle. Missing evidence or authority
remains an explicit undelivered result. Shared denial preserves the exact pending
route. A refused new W or consultation return records `role_admission_refused` and
its actual reason, without H replanning or resetting correction allowances.
Unresolved author-owned choices retain their existing bounded author-correction
identity and consumed allowance; no new evidence or experiment is invented.
Live or unknown originals prevent a replacement clarification.

The ordinary control boundaries remain distinct:

| Retained choice or control fact | Next boundary |
| --- | --- |
| H `wait`, no pending/unknown execution | Durable FINAL; `final_unavailable` if its frozen capability is absent. |
| H `wait`, pending/unknown execution | Preserve waiting and the original call. |
| W `decision`, budget available | Pre-delivery Final A: exact delivery, reject-all finish, or one bounded correction followed by at most one reassessment. |
| Shared budget exhausted | Protected Final A from originals only; reject-all finish remains available despite candidate inventory. |
| Known completed output needs repair | Use only its frozen finite repair permission; exhaustion reports `output_repair_unavailable`. |
| Scientific clarification allowance exhausted | Report `semantic_correction_required`; preserve the first root and consumed attempts. |
| New W handoff or consultation-return admission refused | Preserve `role_admission_refused` and its actual reason; no H replanning or renewed correction allowance. |
| Ordinary H/A author choice unresolved before dependent W | Existing bounded clarification to that H/A author, preserving the exact choice, reason and consumed correction allowance. |
| Existing optional H/A role-admission path refused | Retain its existing bounded H author-correction path and current admission gates. |
| Shared new-action budget denied | Enter existing FINAL, preserving the pending route where applicable. |
| External terminal verified, wake wall budget exhausted | Preserve terminal evidence and report `managed_execution_terminal_wake_deadline`; do not wake without admission. Still-pending deadline remains `managed_execution_wait_deadline`. |
| Actual execution or cleanup unknown | Preserve the original unresolved boundary; no replay, replacement or invented cleanup. |

Already accepted ordinary successors retain their exact scientific route when
replayed after a budget amendment. Current role shares apply to new admission,
including a prepared successor that has no native acceptance.

### Terminal evidence and FINAL

Native request delivery and event reception share interruptible I/O, so pipe
backpressure cannot prevent the existing finite-stop checks. Host delivery
failures clean up through the original child handle; missing group-cleanup proof stays
unknown and never authorizes a replacement submission.

Local completion recovery consumes the original terminal under the same call's
writer lease, after its writer has stopped. TCAD collection resumes the original
run's publication progress, and a retained terminal receipt completes a missing
collection receipt without querying or downloading again. A stopped remote worker
can be reconciled only with positive worker and solver identity evidence; a live
orphan solver retains the original hard deadline. Uncertain starts retain their
identity, consumed budgets and occupied slot. A fully written, unbound built-in
Local accepted snapshot can finish its original binding and inode publication;
it is never discarded or prepared again.

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
stay unchanged. An explicit later threshold increase may reopen the saved pending normal route when its admission is affordable and all original effects are known and quiescent. `budget-amend` records the increase; the next run resumes with a new monotone physical step ID. Prior final A outputs, submissions and all consumption remain immutable; a later budget denial starts a new FINAL cycle with its own single A. A decrease or insufficient increase cannot reopen or repeat the final A. Operator closeout without a pending normal route remains terminal. Frozen settings missing this required policy are unsupported.

Actual provider-reported usage, cached input, consumed time and steps
remain accounted; unknown billing remains explicitly unknown without an estimated charge; original ceilings stay unchanged, and `final_overrun` records
token excess separately. A uses the existing `summary`, `scientific_result` and
`limitations` to explain completed work, unfinished work and evidence-supported
gaps. Ordinary A, formal review and W author continuation receive no such
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

## Sources and formal review

Qualification consumes exact frozen originals, not names, equal bytes or the most
recent object. Managed execution may declare input sources through its frozen
capability; self-contained TCAD V2 packages must match the prepared archive and the
original reviewed producer, while compiled V3 inputs retain their compiler chain.
An explicitly incomplete declaration cannot fall back to a broader source claim.
Optional observers need a separately enabled frozen source contract and a source
view bound to the exact observation. Display coverage alone grants no qualification.
Observer requests describe the current observation; returned candidate and parent
references select exact prior facts through the frozen prefix. They receive no
complete historical candidate/context lists. One operation reuses each selected
producer's facts and adjacent retained metadata instead of walking earlier nodes.
Independent history projections preserve recommendations from the supplied
accepted observations within each author's frozen cut.
Mutable observation metadata is retained at capture under the existing storage policy.
An unchanged source path, device, inode, size and modification time reuse an available prior
retained original after its identity is checked; a changed source is copied into a
new original, preserving the earlier bytes. Model artifacts reuse exact managed
output originals; a live path without retained custody remains unavailable rather
than being copied or labeled archived. Replacing a later submission marker
preserves earlier captured facts.
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
permission. The original selected input cut still defines scientific visibility.

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

## Extensions and UI

Scientific algorithms and usage guidance belong to selected Skill bundles; execution adapters belong to independently installed adapter packages.
Core routes frozen declared capabilities without branching on a domain or plugin
name to decide scientific behavior. Schema shape, declared scientific semantics
and validation must agree; engineering failures must not be disguised as scientific
rejection or an invitation to retry. Bounds and failure evidence survive recovery.
Read views and UI render the same sealed scientific bytes without rewriting them.
CLI result/materials/artifact and the webpage use the same exact-original API; downloads refuse existing destinations. Webpage configuration, explicit instance choices, queued budget changes and guidance use public interfaces and never infer an instance by discovery or recency.
The webpage groups each node's sealed logical material names into directories, including experiment subdirectories. Each directory page lists at most 20 immediate children, directories first with descendant file counts, and provides breadcrumbs and parent navigation. This display projection does not scan or move files; file links retain their original seal ordinals and the CLI's flat materials listing remains unchanged.

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
Its event feed contains nonblank assistant messages and backend-provided thinking
text, collapsed by default. Tool lifecycle and token usage events stay out of the
feed; usage remains in the meter panel. The display labels are “Context” and “Updated”. Within the exact stage/attempt,
context history retains its last valid sample and update time when telemetry is
temporarily unavailable. A new stage or attempt clears both. Retained samples are
labelled historical and do not represent real-time occupancy or control telemetry.
After confirmed operator or unexpected interruption, explicit continuation of ordinary work
preserves the original task, permissions, consumed budgets and unknown calls.
Missing billing or complete authorship is not an ordinary continuation gate;
unknown external calls still cannot be resubmitted. The [CLI guide](ROOTLESS_CLI.zh-CN.md)
owns the controls and restricted-scope recovery boundaries.
Saved running/open status is historical. Current local liveness uses the original
boot, PID, start identity and process state; stale or reused PIDs and zombies do
not establish running work. Confirmed stopped work can retain missing terminal
records, while a live original is collected and uncertain execution stays unknown.

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

TCAD-specific promotion evidence is defined in the plugin's
[qualification boundary](../plugins/tcad_artifact/README.md#qualification-boundary).

See the [CLI guide](ROOTLESS_CLI.zh-CN.md), [installation](INSTALL.md) and [release scope](RELEASE.md).

Adapter audit, smoke and scratch recovery requests contain staged candidate originals and the frozen scientific configuration, without the real delivery target. Only promote and confirm requests receive that target. This reduces unnecessary host information in the phase interface; same-user execution remains a soft boundary.

## Selected framework Skill assets

[Research configuration](RESEARCH_CONFIGURATION.md#selected-framework-skills--按角色选择框架-skill)
owns installation-source schema v3, exact selected manuals, role selection,
read-only live assets and backend loading limits. Selection does not copy Skill
bytes, create per-instance manifests, or grant permission or qualification.

## Controller decisions and optional observations

Routing computation returns one internal immutable named `Route` value. It does
not introduce a durable route schema or a second journal. The controller's
recovery cursor and step advancement consume accepted facts under the existing
writer lock; they do not become another state authority. `guidance.py` owns
instruction text for newly routed tasks. Retained task instructions remain
originals and are never regenerated during recovery.

Missing billing retains known lower bounds and unknown coverage; it is not by
itself an ordinary Worker fatal error. Optional metadata or observer failures
degrade that optional view. Required identity, original-call and explicitly
required metadata/admission gates still refuse when their proof is missing.
Typed pause diagnostics preserve already admitted resume actions instead of
replacing them with a display-only stop.
