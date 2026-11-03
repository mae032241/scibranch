# Plugin runtime boundary

This reference owns the supported plugin runtime boundary. `scidiscovery.operations`
continues to own stable declarations. There is one compiler/catalog and no runtime
API registry. Catalog ABI/digests, configuration identity, public/support/internal
visibility and versioned schema IDs remain necessary platform contracts.

## Declaration and injected callback contracts

Support is attached to the values and callbacks below, at their existing import
paths. It does not cover every symbol in their containing packages. Source type
annotations and compiled schemas are authoritative; this reference does not define
parallel types. Current plugin protocol is `1`, Operation ABI is `23`, and component
protocol versions are declared independently. Changing an implementation or resource
still changes compiled identity.

`InputDerivationSpec(anchor_port=..., select="parents")` resolves only the selected
anchor's immutable direct parents matching the hidden port's concrete schema.
Missing or multiple matching parents fail admission. It does not search by name,
recency, bytes or unrelated artifacts. TCAD analysis uses this edge to obtain its
control-owned package from the selected runtime manifest; Workers receive a
read-only scientific projection, without execution credentials or transport fields.

`InputPortSpec.exposure="material"` declares a paired wildcard evidence inventory
(`schema="*"`, `media_types=("*/*",)`). Control applies `AgentSettings.input_materials`
to stored sizes, delivers small text on demand, and freezes larger or binary inputs
as `file_reference`. A file reference may declare concrete MIME/schema constraints
or the same paired wildcard inventory; it is not restricted to octet-stream.
Its bytes are absent from in-memory validation mappings. Tools use
`OperationToolContext.input_path(source_name)` for bounded streaming. Scientific Agent tasks with native shell access expose `worker_materialize_input` so Workers can request a native-readable
path without handling private identities. Material limits are shown in Root's
contract and frozen in the Run; they do not authorize solver execution or qualify
scientific claims. Other input ports retain their declared semantic/content limits.

Scientific Agent tasks with native shell access declared through `with_reference_access` also expose
`worker_publish_files(files=[{path, purpose}], source_aliases=[...])`. Files live
under `scratch/`; `output/` remains the primary `result.json` plus control receipts.
Control snapshots and streams files, infers MIME types, assigns references, and seals
an independent `attachments` collection. No hashes, ports or manifest rows are
Agent-authored. Publication does not imply execution, qualification or review.
Domain tool evidence (for example, calibrated figure families) remains separate.

`AgentSettings.attachments` configures `max_item_bytes`, `max_total_bytes`, `max_files`
and `chunk_bytes` (defaults: 2,000,000,000 bytes per file and total, 64 files, 1 MiB
chunks). Settings are frozen per Run, inherited on recovery and shown in startup
budgets. Identical publication retries reuse receipts. Downstream Workers can list
attachments through `worker_reference_read` on the exact bound main output, then
read selected fragments or request streamed file delivery under `input_materials`.
The UI renders verified attachments as downloads, with PNG/JPEG previews. Reference
file delivery and finalization do not bulk-load attachment bytes.
An explicitly bound sealed stage or tool original is also readable while its
producer is running or failed, including after its contract becomes historical.
Control freezes a private receipt snapshot for the read; only declared source
edges are traversable, never the producer's workspace or unrelated later files.
Diagnosis views display exact frozen input PNG/JPEG images without requiring a
Worker to copy them into a new output or fill a separate visualization form.

| Owner | Supported extension contract |
| --- | --- |
| `operations.spec` | `PluginDefinition`, `PluginDependency`, `ComponentSpec`, `ComponentRef`, `CallableComponent`, `OperationSpec`, descriptions, port/derivation/collection, semantic, executor, workspace, permission, review, approval and limit declarations consumed by the compiler. Plugin entry points return definitions; declarations do not grant runtime access. |
| `operations.tooling` | `WorkerToolDefinition`: a typed `input_model`, either `handler(request)` or `contextual_handler(request, context)`, and optional `local_contextual_handler`. Declared required/optional services, evidence ports, network/reference permissions, attempt recording and owner restriction determine the supplied capability. Compiler caches and router helpers are internal. |
| `operations.input_validation` | `OperationInvocationError`, `BoundSourceError`, `parse_bound_json`, `InputBindingDescriptor`, supplied `ValidationSources` and `prior_analysis_sources`. Validators receive bound bytes plus read-only source descriptors and verified calculation values; they do not assemble control proofs. |
| `operations.invoke` | Effect executors return `EffectExecutorPlan(executor, preparation_profile, payload_port, budget_subject_schemas)`. Approval projectors receive `ApprovalProjectorContext` with immutable `ApprovalSubjectSnapshot` and supplied producer-family/source/input projections, and return `schema.approval.ReviewDocument`. These are trusted callback data, not Agent output. Preflight, bound-call construction, invocation and execution orchestration remain control-owned. |
| `operations.runtime_plugins` | `RuntimePluginFactory.build(RuntimePluginContext) -> RuntimePluginContribution`. The frozen startup context supplies `plugin_id`, `mode` (`control` or `local_worker`), exact `config_path`/`config_bytes`, and `state_root`. Contributions bind named `execution_adapters`, named `tool_services`, and control-managed `reconcilers`. This is installed trusted startup code, not a Worker capability or a second registry. |
| `operations.experiment` | `ExperimentCapability(tools, instructions, execution_operation, execution_package_schema_version, output_inventory_name=None)` implements `scidiscovery.complete-experiment.v1`. The package version is required and participates in identity; configuration selects the contribution. An optional collected JSON inventory contains `outputs[{name, relative_path, sha256, size_bytes, media_type}]`. Control verifies it against collected originals and exposes solver-relative filenames, without revealing transport paths or asking Workers to build a mapping table. |
| `operations.workspace` | `WorkspaceMaterializer(request) -> WorkspaceMaterializationResult`, `WorkspaceFilePolicy(WorkspaceFileRequest) -> WorkspaceFileRule | None`, finalizer callables `(WorkspaceFinalizationRequest) -> bytes`, `WorkspaceSnapshotter(Path) -> tuple[WorkspaceSnapshotFile, ...]`, and `WorkspaceProtocolError`. A finalizer that removes control-owned fields from the authoring schema uses `WorkspaceFinalizer(implementation, draft_schema, projection_version)` to pair that projection with its deterministic completion; the version participates in compiled identity. Plain callables retain the complete output schema. Requests carry only supplied paths, limits, binding descriptors and frozen input bytes; private `input_contents`, trusted tool records and Run identity must not be materialized for Agents. Control resolves hooks and seals results. |

The actual tool callback object is
[`OperationToolContext`](../src/scidiscovery/artifact_agent/operation_tool_context.py),
injected by the tool host. Plugins use its methods instead of constructing it or
setting its underscore-prefixed callback fields:

| Capability | Supplied fields or methods |
| --- | --- |
| Task-local work | `workspace`, `output_directory`, `output_collections`, `remaining_seconds`, mutable task `state`; `validate_outputs()`, `record_activity(activity)`, `candidate_snapshot()` |
| Declared inputs | `input_names_for_port(port)`, `read_input(name)`, `input_path(name)`, `input_media_type(name)`, `input_ref(name)`, `source_descriptor(name)`, read-only `prior_source_bindings` |
| Declared services and access | `require_service(name)` over the supplied read-only `services`; `read_reference(request)`, `reserve_network_request(url)`. Missing permissions reject access; importing the context grants none. |
| Evidence collection | `io_budget(**values)`, `evidence()`, `read_evidence(name)`, `accept_evidence(**values)`, `adopt_bound_evidence(manifest_alias)`, `publish_files(request)`. Budget/registration arguments follow the declared tool's collection contract; adoption verifies the exact bound family. |
| Scientific calculation | `complete_calculation(record, *, diagnostics=(), summary=False)`, `publish_analysis_file(raw, *, media_type, kind, sources, suffix, metadata=None)`, `publish_calculation_checkpoint(raw, *, algorithm_version, record_key, sources, numerical_identity)`, `read_calculation_checkpoint(alias, *, algorithm_version, tool_names, sources, max_bytes)` |

`run_id`, `operation_id`, `tool_attempts`, `finish_attempt`, `execution_scope` and
`recovery_authorized` are control/host plumbing on that object, not general plugin
extension APIs or Agent fields. Private callback slots, receipt shapes and service
implementations are not supported imports. Evidence descriptors and immutable
`ArtifactRef` values are trusted callback data; they must not be copied into a
Worker's scientific response. A plugin receives only its declared named services;
`RunService`, databases and the experiment coordinator are not public services.

The `plugin_runtime.experiment.ExperimentTools` structural protocol is the supplied
per-Run service: `capabilities(context)`, `seal_implementation(context, *, payload,
scientific_material, sources, private_outputs=())`, `read_implementation(context,
alias)`, `source_alias(context, ref)`, `diagnostic_context(context, *, service_name)`,
`cancel_diagnostics(context, *, service_name, name)`, and `command(context, request)`.
`publish_diagnostic_files(context, *, implementation, name, workspace, outputs)`
streams identity-checked diagnostic originals through common attachment publication.
`workspace` and output descriptors come from the trusted diagnostic service, never
Agent-authored paths. Returned references retain diagnostic scope; publication is
not formal execution or scientific qualification. The tool declares `attachments`
in its evidence ports to use this delivery capability.
`ExperimentMethod` supplies `key`/`content`; `SealedImplementation` supplies exact
`artifact_ref`/`content`. Commands still pass the task's frozen execution and budget
gates; these callbacks cannot bind another Run or expose the coordinator.
`progress_summary(progress)` projects executor timing and up to two log metadata
records, without log text. Only UI callers opt into redacted, bounded last-line
previews with `include_log_text=True`. Missing timing is not estimated. This projection carries
operational observations, never a completion percentage or scientific verdict.
`read_implementation` accepts exact implementations created in or explicitly bound
to the task, retaining original receipts. `source_alias` resolves an immutable file
reference to a currently available alias; previous-task aliases are never identities.
Output validators may use `ValidationSources.sealed_material_record(alias)` for a
control-verified receipt of a current or explicitly bound material. Its original
source-reference namespace and author are retained; an interrupted author's sealed
material is not invalidated by the author's later Run state. This callback exposes
control provenance to validators, not additional Worker form fields or file bytes.

## Execution adapter boundary

[`ExecutionAdapter` and `AdapterCapability`](../src/scidiscovery/artifact_agent/execution_bridge.py)
remain at their existing import path. Only these protocol/value types are supported
plugin contracts in that module; `ExecutionBridge` is a control implementation.

An adapter implements `capabilities() -> tuple[AdapterCapability, ...]`,
`prepare(payload: LocalFileDescriptor, *, preparation_profile: str,
exchange_directory: Path) -> LocalFileDescriptor`, `submit(submission) ->
tuple[str, str]`, `lookup_submission(submission) -> tuple[str, str] | None`,
`status(external_run_id) -> str`, `cancel(external_run_id) -> str`, and
`collect(external_run_id) -> tuple[LocalFileDescriptor, ...]`.
Submission returns external identity/state and must be idempotent for the exact
frozen descriptor. Lookup must authoritatively distinguish absence from an accepted
submission; an unknown response cannot justify another submission.

Optional `retire_submission(submission) -> tuple[str, str] | None` is a stronger
contract: return the existing job, or return `None` only after durably fencing all
future acceptance of that exact descriptor. It must serialize with submit; repeated
negative lookups, elapsed time or an unavailable endpoint are not retirement proof.
Control records support when a new request is started. Older uncertain requests
cannot gain this guarantee retroactively from a newly installed adapter method.
TCAD adapters submit through `submit_retirable` / `tcad_submit_retirable`; old
transports/runners reject these entry points before accepting work. They must not
fall back to the old submit endpoint, whose in-flight processes may ignore fencing.
TCAD also implements `validate_submission_protocol()`, a side-effect-free check the
bridge calls before admission/reservation. Local method presence is not proof of
remote protocol support. The transport's `submission_protocol` maps to
`tcad_submission_protocol`; version 1 advertises the exact submit and retire names.

`AdapterCapability` contains `key`, `kind`, `schema_id`,
`payload_schema_version`, `media_type`, exact `content: bytes`, and
`public_summary: Mapping[str, object]`. Keys must be unique in a nonempty capability
set; the bridge bounds each content value to 1 MiB. The port/schema declarations
own payload validation and version identity.

The bridge also consumes optional adapter hooks already used by production TCAD:
`supports_preparation_profile(profile)`,
`validate_preparation_payload(raw, *, preparation_profile)`,
`execution_admission(raw, *, preparation_profile) -> ExecutionAdmission`, and
`status_details(external_run_id) -> dict` (`state`, optional `progress` and
`consumed_budget`). An adapter supplying admission must accept
`submit(submission, *, authorization=admission)`. Admission supplies domain policy
facts; control still decides approval, reserves budget and records lifecycle.
`prepare_with_artifacts(payload, *, artifacts, preparation_profile,
exchange_directory)` is an existing trusted control-side integration hook used by
TCAD; it receives `ArtifactService` and is **not** a portable public plugin API.
This document does not make that service or its methods stable.

## Selected shared schema values

Production plugins currently use the following values at
`scidiscovery.artifact_agent.schema.<module>`. Support covers these values and
nested scientific fields required by their models, not the whole `schema` package.
Wire schema IDs come from each compiled port/codec, not a Python module name.
`SchemaModel` descendants below carry `schema_version: 1`; this number is distinct
from Operation versions, plugin protocol/ABI, and a port's versioned schema ID.

| Module | Supported values/functions used by production plugins |
| --- | --- |
| `common` | `SchemaModel`, `Identifier`, `Sha256`, `canonical_json`, `canonical_sha256` |
| `refs` | `ArtifactRef`: complete immutable identity, available only within trusted callback data |
| `artifact`, `scientific_foundation`, `units` | `UtcRfc3339`, `SourceType`; `unit_definition`, `supported_unit_spellings`, `units_equivalent` (aliases/helpers, not separately versioned payloads) |
| `execution` | `LocalFileDescriptor`, `ExecutionAdmission`, `ExecutionRequest`; request identity remains control-owned and its canonical method preserves omission of absent `compiled_identity` |
| `execution_context` | `ExecutionContext` |
| `experiment` | `ExperimentPortfolio`, `ExperimentProposal`, `ValidationPlan`, `deterministic_validation_check_keys`; nested case, comparison and validation values remain part of these scientific contracts, including active `experiment.ComparisonContract` |
| `research_cycle` | `ScientificIntake`, `ScientificReview`, `validate_scientific_intake`, `validate_scientific_review` |
| `research_objective` | `ObjectiveClosureRequirement`, `ObjectiveIntent` (literal alias), `ObjectiveTarget`, `ResearchObjectiveContract` |
| `layered_diagnosis` | `CaseMappingBasis`, `LayeredDiagnosisReport`, `validate_layered_diagnosis`; scientific calculation values in reports do not expose private calculation receipts |
| `approval` | `ReviewDocument`, `ReviewDocumentSection`, `ReviewDocumentItem` for deterministic presentation; not human decisions, approval signing or authority |
| `role_result` | `RoleHandoff`, `RoleResultEnvelope`, `parse_role_result`; envelope version `1`, handoff has no separate version |
| `cognitive` | `EvidenceAudit`, payload version `1` |

Historical importability is not a stability promise. The retired
`artifact_agent.schema.comparison` realization/equivalence evaluator and
`tcad_artifact.project_materializer` author workflow have been removed after checking
production imports, registered component resources and entry points. Their dedicated
legacy tests were removed; current experiment comparison and domain-neutral artifact
kind contracts remain supported. No old author/materialize Operation is restored.

## Canonical identities

`schema.common.canonical_json` normalizes strict supported values, requires string
mapping keys and finite numbers, and emits sorted compact UTF-8 bytes without ASCII
escaping. It supports `SchemaModel` and rejects arbitrary model/dataclass values;
`canonical_sha256` hashes those exact bytes. The figure validator's bundle fingerprint
now uses this helper on its locally built list of string-keyed records containing
strings and integer byte counts. Frozen pre-change byte/hash vectors cover that
migration. Non-string Python mapping keys are outside this JSON input contract.

Other similarly named encoders have different owners and remain separate:

- Operation declaration projection handles model/dataclass/enum values and produces
  a string for compiled identity.
- Instance archive encoding uses ASCII escaping; those persisted archive digests
  must not be recanonicalized.
- Copied workspace schema/contract readers are standalone stdlib programs with
  their own string output, and the remote runner remains Python 3.6 compatible.
- TCAD packager, transport and execution encoders retain their current input domains
  and exact persisted bytes; matching JSON formatting options alone does not prove
  that strict normalization accepts the same inputs.

## Implemented public modules

| Module | Contract | Authority retained by control |
| --- | --- | --- |
| `scidiscovery.operations.transforms` | Pure `object_schema`, `single_input`, `group_inputs`, `transform_output`, `transform_operation`; explicit component/codec references and scientific descriptions | Compilation, registration, identity, admission and side effects |
| `scidiscovery.plugin_runtime.evidence` | `read_evidence_records(raw)` validates the manifest wire shape and returns detached records with `alias`, `artifact_ref`, `source_ref`, `media_type`, `size_bytes`, `metadata`, `tool_name` | Authenticity, exact input binding, attempt/recovery proof, immutable registration and qualification |
| `scidiscovery.plugin_runtime.workspace` | Symlink-resistant task-root file IO, media typing, publication content validation, atomic bounded JSON records on supplied paths | Workspace allocation, roots, immutable sealing, Artifact registration and qualification |
| `scidiscovery.plugin_runtime.calculations` | `CalculationResult`, scientific citation resolution; structural `CalculationTools`/`CalculationEvidence` protocols implemented by the actual tool/validation contexts | Actual attempt, receipt, exact read-source identity, registration and recovery verification |
| `scidiscovery.plugin_runtime.experiment` | Per-Run `ExperimentTools`: methods, sealed implementation bytes, task diagnostic context/cancel and declared execution commands | Coordinator, Run/Execution/Artifact services, lineage, budget origin and task admission |
| `scidiscovery.plugin_runtime.collection` | Supplied `CollectionContext` deadlines/cancellation/progress, bounded streaming `run_bounded`, owned-child parent watch | Execution supervisor, grants, cancellation decisions, Run lifecycle, DB and leases |
| `scidiscovery.plugin_runtime.diagnostics` | Redacted bounded exception facts and declared failure value types | Diagnostic storage, host traceback capture, qualification and retry decisions |
| `scidiscovery.plugin_runtime.results` | Mechanical draft copies and handoff projections on the supplied workspace request | Final output validation, sealing and lifecycle |
| `scidiscovery.plugin_runtime.submission` | `SubmissionNotCreated`: typed pre-creation rejection with exact descriptor SHA and a bounded reason; transport must verify and translate identities | Reservation release, retry policy, exact lookup and retirement fencing; untyped errors remain uncertain |
| `scidiscovery.plugin_runtime.transport` | Generic `MCPRouter`, JSON-RPC parsing/error values, bounded Unix socket client/daemon | Root/Worker routers, tools/registration, authentication and compiled action authority |
| `scidiscovery.plugin_runtime.observation` | `RECORD_DIR`, `normalize_log`, `materialize_launcher` for the optional Worker-executed stdlib launcher | Launch/stop authorization, Run lifecycle and interpretation of observations; other module functions are implementation details |
| `scidiscovery.plugin_runtime.presentation` | Pure JSON field/section/gap/source/parameter/citation constructors, bounded row windows and ancestry within the supplied cohort | Authorized input cohort, provider discovery, output/source validation, aggregate bounds and rendering |

The runtime package performs no imports of service, interfaces or approval UI modules.
Figure, curve and TCAD are migrated consumers. All three production plugin packages
and plugin_runtime have no imports of service, interfaces or approval UI. Domain
SSH/solver, packager and numerical behavior remain in their plugins.
The full tool manifest models live in the core schema layer for control use, **not**
in the public API. Moving them does not make attempts, recovery state or private
calculation records public plugin contracts. Reserved `calculation_proof` and
`checkpoint_proof` metadata is excluded from both manifest record views and the
tool context's `evidence()` view. Control reads the original records for verification;
scientific/domain metadata remains available.

Evidence records are callback data for trusted domain tools/validators. They are not
Agent-visible output: source identities and domain restoration metadata may be private.
Parsing does not prove authenticity or authorize access. Obtain the exact bytes from
compiled inputs or the existing tool context; retain domain source/hash/membership
checks. Mutating the detached result changes no stored record. Registration and
adoption still go through the task's existing controlled tool context.

Presentation helpers accept only the already authorized records supplied by the UI.
They cannot load artifacts, expand a cohort, grant qualification or return executable
HTML. The UI still validates every source and caps the combined response. Domain
providers retain interpretation and layout of their own scientific fields.

## Calculations and checkpoints

Algorithms return `CalculationResult`: scientific request/result/status, exact numerical
input hashes and bounded safe diagnostics. Existing hashes support numerical replay;
they are not Agent fields or private receipts. `complete_calculation` on the supplied
tool context creates the actual attempt result and seals the private record. Control
checks those hashes against sources actually read. `verified_calculations(report)` on
the validation sources verifies receipts/origins before returning result values; it
never returns attempts or recovery state. Both contexts implement the public structural
protocols without a second service registry.

`publish_analysis_file` registers immutable bytes and a task-local preview.
`publish_calculation_checkpoint` accepts checkpoint bytes, algorithm/record identity,
source aliases and numerical identity values. Control derives exact source references
and hashes. `read_calculation_checkpoint` checks the registered producer, bytes and
source cohort, returning only saved bytes and numerical identity values. Curve retains
request equality, numerical reconstruction and mathematical consistency checks. No
plugin constructs `ControlledCalculationRecord` to complete these paths.

## Ownership and extension

TCAD socket transport remains a supported runtime configuration and CLI; its generic
transport implementation is shared with core. It does not expose Root/Worker service
routers. Collection utilities preserve streaming, total/per-file/idle deadlines and
owned-process cancellation; a utility import grants no execution permission. Workspace
and atomic-record helpers require control-supplied roots/paths.

`prior_analysis_sources` supplies paired historical source-port facts along with exact
source mappings, so TCAD need not parse private manifest bindings. Scientific case
correspondence remains a domain check. Process supervisors, task lifecycle, leases,
execution DB, host traceback storage and retry authority remain internal.

A new facade must not simply re-export `RunService`, private proof models or UI
implementation functions. Pure values/functions can move to their owning public
module; stateful powers use a narrow injected capability with one control-side owner.
No plugin acquires lifecycle or approval authority by importing this API. Experiment
services expose a per-Run adapter, never the coordinator. Shared startup bindings are
scoped by the real local/hardened tool host without changing cached Run identity;
TCAD receives exact implementation values and a derived diagnostic workspace.

Tool handlers declare safe caller corrections with
`scidiscovery.operation_contract.ToolInputError(code, message, path=...)` or explicit
`DiagnosticError`/`contract_diagnostic` details. Messages must be authored explanations,
not raw exception strings or echoed input. Unexpected component/transport failures
remain engineering failures. Control stores sanitized diagnostic details with an
internal format version; caller-supplied flags never confer trust. Root can explicitly
page saved diagnostics while a task is running without reading its draft output.

Experiment services may implement `experiment_pending_diagnostics(run_ids, inputs)`
to return bounded, owner-declared diagnostics identifying outstanding named jobs and
their status/cancel calls. The existing `experiment_activity` callback remains the
fallback and cancellation hook. Neither callback grants execution authority.

## Root consumer boundary

Normal Root calls use the research surface. Standalone Effect plugins and historical
execution recovery select `surface="execution"` on all three gateway methods
(`scid_catalog` also needs `kind="interfaces"`). Interface declarations own this
projection; it does not authorize internal Operations or task-managed execution.
Execution services, exact approval, budgets and idempotency remain control-owned.

`run_status` uses `intent="decision"` by default; `status` waits or pages safe saved
diagnostics, `navigation` discovers exact scientific fields/input names, and `full`
explicitly reads complete completed science. The old profile/view/mode/full-output
flags are removed. `output_fields` and exact `output_paths` remain mutually exclusive;
index/stage pagination and explicit omission markers retain their bounds. UI and
control retain private recovery and engineering records.

## Validation status

See the [release validation status](RELEASE.md#current-status) for candidate-bound
results and open acceptance limits. Plugin API declarations and focused source
checks do not establish live model, solver or deployed-service qualification.

### Worker startup and authoring visibility

Control derives `worker-start.json` from the frozen assignment and workspace manifest.
Plugins put domain read/edit rules and report guidance in their existing
`WorkspaceMaterializationResult`; optional navigation files should not repeat the
task, role, tool-contract map or output contract. Helpers receive their selected
subtask materials instead. No new plugin hook or registration is required.

The paired `WorkspaceFinalizer` still owns which fields control completes. The
Worker form preserves scientific rules, source aliases and byte limits while
omitting input admission metadata, internal port/checker dependencies and Operation
identity. Full form reading never switches to the sealed artifact schema. Plain
finalizers retain the fields they have not declared and implemented as derived.

Task-managed diagnostic providers may expose `experiment_diagnostic_collect` for exact Root takeover of existing jobs. It uses the frozen original collection limits and immutable retained receipt, calls the control publisher only after verified collection, and marks delivery only after publication succeeds. It must never create or authorize a submission.
