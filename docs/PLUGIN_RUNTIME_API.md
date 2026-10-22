# Plugin runtime boundary

This reference owns the supported shared plugin-library boundary. The old Root/MCP/UI
applications are retired; these declarations do not expose a replacement scheduler
or require the thin CLI to use the legacy Run protocol. `scidiscovery.operations`
continues to own stable declarations. There is one compiler/catalog and no runtime
API registry. Catalog ABI/digests, configuration identity, public/support/internal
visibility and versioned schema IDs remain necessary platform contracts.
The old Run/SQL service stack, runtime loader and standalone collection helpers are
deleted. Retained callbacks consume injected capabilities; their declarations do
not install an Operation-tool host in the current thin loop.

Thin-loop candidate observations are public facts in first-observation order, including
ties and missing metrics. Plugins supply facts and exact qualification receipts; the
scientific author supplies an explicit candidate identity and explains its choice
in `scientific_result`. An optional comparison table carries scientific explanation;
its row, `keep`, rationale and evidence-step formatting do not gate submission.
Selection policy version 2 is exactly `{"version":2}` and performs no automatic
smoke or audit. Actual audit/delivery requires an exact available registered object;
declared qualification, execution permissions and budgets still apply.

## Declaration and injected callback contracts

Execution factories may declare `RuntimePluginFactory.configure(config, source, *, binding)`
and `requires_config_file=True`. The callback receives the bounded JSON/TOML
object and its original `Path` (or `None` for supported inline configuration),
and the selected binding, and returns effective JSON settings. It owns binding/schema validation and relative
references; it must perform only local reads, without constructing adapters,
creating state directories, starting subprocesses or contacting services.
The framework discovers the existing execution registration, loads the selected
file, calls this hook and freezes the resulting `executor`/`configuration` pair.
There is no additional plugin registry. Missing required files fail before
instance creation. `PluginConfigurationError` supplies safe `code`,
`public_message`, `field_path`, `hint` and `plugin_id` diagnostics; authored
messages must exclude configuration values and credentials. Existing frozen
settings do not reopen the original top-level file or rerun this callback.
Runtime adapter initialization continues through the factory's existing `build`.

These are retained library contracts for callers that supply a trusted host and
its declared capabilities. They do not make the named Operation Worker tools,
Run lifecycle or loader available in the thin CLI. Thin execution discovery uses
`scidiscovery.execution_interfaces`; its frozen instances, provenance, review,
authorization and budgets follow the [current architecture](ARCHITECTURE.md).

Support is attached to the values and callbacks below, at their existing import
paths. It does not cover every symbol in their containing packages. Source type
annotations and compiled schemas are authoritative; this reference does not define
parallel types. Current plugin protocol is `1`, Operation ABI is `26`, and component
protocol versions are declared independently. Compiled identity covers declared
component/resource metadata; arbitrary handler
source edits are not automatically included unless their declaration binds that
identity. Thin CLI consumers freeze executor selection, configuration and declared
execution contracts. Installed framework/plugin/tool source hashes and versions do
not prevent upgrades; scientific task, input, output and authorization originals
retain their exact content identities.

`OperationSpec.scientific_progress` optionally declares `ScientificProgressSpec`,
including the objective/subject/source ports and a projector component reference.
The schema value `artifact_agent.schema.scientific_progress.ScientificProgressProjection`
and the generic analysis projector retain a pure projection contract: copy the
Worker's answered question, conclusion, remaining question, evidence aliases and
assessed scientific changes from the supplied report and sources. A declared
`progress_assessment` may preserve `no_progress` or `unknown`. Projection neither
proves novelty nor grants scientific qualification.

A consuming host must resolve exact sources, bind objective and subject identities,
verify attachment membership, enforce source-byte limits and decide whether a
projected change may count as progress. The retained declaration/projector does not
provide that lifecycle, family deduplication, advance admission or recovery service.

`OperationSpec.independent_review_ports` declares exact review subjects;
`authorship_source_ports` declares transform input edges that contribute authorship
and participates in compiled identity. A consuming host must verify contributors
and reject a reviewer who participated in the exact subject. Copied labels,
arbitrary parent links and raw user text do not establish independent authorship.
The declaration does not attach reviewers or implement a review host.

`InputPortSpec.derivation` also owns the public `source_requirements` projection.
Requirements are attached to the visible anchor input; derived port names and
producer traversal paths remain internal. A wildcard anchor does not waive these
requirements. The same declaration produces origin-rejection guidance, so plugin
authors do not maintain a separate caller rule. Missing origins are repairable
input admission errors, not engineering failures. Originals can be passed directly
to a source-based scientific task; copying them does not manufacture a required
scientific result or its provenance.

`InputDerivationSpec(anchor_port=..., select="parents")` declares selection from only
the anchor's immutable direct parents matching the hidden port's concrete schema.
Missing or multiple matching parents fail admission. It does not search by name,
recency, bytes or unrelated artifacts. TCAD analysis uses this edge to obtain its
control-owned package from the selected runtime manifest; Workers receive a
read-only scientific projection, without execution credentials or transport fields.

`InputPortSpec.exposure="material"` declares a paired wildcard evidence inventory
(`schema="*"`, `media_types=("*/*",)`). A file reference may instead declare concrete
MIME/schema constraints. The consuming host must enforce its frozen
`AgentSettings.input_materials` limits, provide only declared validation bytes and
resolve bounded `OperationToolContext.input_path(source_name)` requests. A material
reference does not authorize solver execution or qualify a scientific claim.

ABI 24 permits `max_item_bytes=None` only for wildcard material/file-reference
inputs and control-owned attachments, whose collection also declares
`max_total_bytes=None`. This selects the declared `input_materials` or `attachments`
settings as the byte-budget owner; it does not mean unlimited bytes. Ordinary
scientific ports still require positive declared limits. The consumer must project
and enforce the effective budget; older compiled identities remain distinct.

`with_reference_access` can add declared reference/publication tools and evidence
ports to an Operation. For example, `worker_publish_files` accepts scientific file
paths and source aliases rather than Agent-authored references or manifest rows.
A host must supply the corresponding `OperationToolContext` callbacks, snapshot
and stream files, assign identities and seal collections. Declaring tools does not
implement this host or make them thin CLI tools. Domain tool evidence, such as
calibrated figure families, remains separate.

Source declarations describe associations; they do not prove that the stated
inputs produced a file. Publication and matching source identities do not establish
scientific correctness, qualification or independent review. A consuming host must
implement any automatic capture, restriction propagation, receipt verification and
recovery policy it offers. `OperationToolContext.capture_science` is an optional
injected callback; its fallback returns the supplied value and provides no such
pipeline. Explicit publication likewise requires a host implementation of the
injected publication capability and its source policy.

`AgentSettings.attachments` declares `max_item_bytes`, `max_total_bytes`, `max_files`
and `chunk_bytes` (defaults: 2,000,000,000 bytes per file and total, 64 files, 1 MiB
chunks). These are configuration values, not an installed task host. A consumer
owns freezing settings, enforcing cumulative bounds, retaining publication receipts
and resolving authorized references. The reference tools forward requests to the
injected `read_reference` callback; they supply no artifact resolver, UI preview,
running-stage read service or recovery implementation on their own.

| Owner | Supported extension contract |
| --- | --- |
| `operations.spec` | `PluginDefinition`, `PluginDependency`, `ComponentSpec`, `ComponentRef`, `CallableComponent`, `OperationSpec`, descriptions, port/derivation/collection, semantic, executor, workspace, permission, review, approval and limit declarations consumed by the compiler. Plugin entry points return definitions; declarations do not grant runtime access. |
| `operations.tooling` | `WorkerToolDefinition`: a typed `input_model`, either `handler(request)` or `contextual_handler(request, context)`, and optional `local_contextual_handler`. Declared required/optional services, evidence ports, network/reference permissions, attempt recording and owner restriction determine the supplied capability. Compiler caches and router helpers are internal. |
| `operations.input_validation` | `OperationInvocationError`, `BoundSourceError`, `parse_bound_json`, `InputBindingDescriptor`, supplied `ValidationSources` and `prior_analysis_sources`. Validators receive bound bytes plus read-only source descriptors and verified calculation values; they do not assemble control proofs. |
| `operations.invoke` | Effect executors return `EffectExecutorPlan(executor, preparation_profile, payload_port, budget_subject_schemas)`. Approval projectors receive `ApprovalProjectorContext` with immutable `ApprovalSubjectSnapshot` and supplied producer-family/source/input projections, and return `schema.approval.ReviewDocument`. These are trusted callback data, not Agent output. Preflight, bound-call construction, invocation and execution orchestration remain control-owned. |
| `operations.runtime_plugins` | `RuntimePluginFactory.build(RuntimePluginContext) -> RuntimePluginContribution`. The frozen startup context supplies `plugin_id`, `mode` (`control` or `local_worker`), exact `config_path`/`config_bytes`, and `state_root`. Contributions bind named `execution_adapters`, named `tool_services`, and control-managed `reconcilers`. A trusted consumer constructs this context; the declaration supplies no runtime loader or Worker capability. |
| `operations.workspace` | `WorkspaceMaterializer(request) -> WorkspaceMaterializationResult`, `WorkspaceFilePolicy(WorkspaceFileRequest) -> WorkspaceFileRule | None`, finalizer callables `(WorkspaceFinalizationRequest) -> bytes`, `WorkspaceSnapshotter(Path) -> tuple[WorkspaceSnapshotFile, ...]`, and `WorkspaceProtocolError`. A finalizer that removes control-owned fields from the authoring schema uses `WorkspaceFinalizer(implementation, draft_schema, projection_version)` to pair that projection with its deterministic completion; the version participates in compiled identity. An optional `draft_input_ports` tuple requests a fourth `draft_schema` argument containing only those frozen input bytes, keyed by task alias; the consuming host must supply the same frozen inputs whenever it invokes the projection, including explicitly requested hidden inputs. These bytes and aliases are control-only callback data: the hook may emit scientific choices and safe projection paths, but must not publish private aliases or bindings in the authoring form. Use this for scientific choices that depend on the bound materials, and bump `projection_version` when changing the projection. Plain callables retain the complete output schema. Requests carry only supplied paths, limits, binding descriptors and frozen input bytes; private `input_contents`, trusted tool records and Run identity must not be materialized for Agents. The consuming host resolves hooks and seals results. |
| `operations.experiment` | `ExperimentCapability(tools, instructions, execution_operation, execution_package_schema_version, output_inventory_name=None)` implements `scidiscovery.complete-experiment.v1`. The package version is required and participates in identity; configuration selects the contribution. An optional collected JSON inventory contains `outputs[{name, relative_path, sha256, size_bytes, media_type}]`. A consuming host must verify it against collected originals before exposing solver-relative filenames, without revealing transport paths or asking Workers to build a mapping table. |

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
| Scientific calculation | `complete_calculation(record, *, diagnostics=(), public_result=None)`, `publish_analysis_file(raw, *, media_type, kind, sources, suffix, metadata=None)`, `publish_calculation_checkpoint(raw, *, algorithm_version, record_key, sources, numerical_identity)`, `read_calculation_checkpoint(alias, *, algorithm_version, tool_names, sources, max_bytes)` |

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
`artifact_ref`/`content`. The supplying host must bind commands to the task's frozen execution and budget
gates and deny cross-task access to its coordinator.
A caller supplying this retained callback protocol owns its coordinator, frozen
inputs, execution authority and cancellation. The old RunService assembly and
operation-service loader are deleted. The current thin loop uses its registered
managed execution interfaces; it does not instantiate this historical host.

A caller implementing requested review must bind its exact subject and verified
contributors. Waiting for review never grants execution permission.

`progress_summary(progress)` projects executor timing and up to two log metadata
records, without log text. Only UI callers opt into redacted, bounded last-line
previews with `include_log_text=True`. Missing timing is not estimated. This projection carries
operational observations, never a completion percentage or scientific verdict.
A supplied `read_implementation` must accept only exact implementations created
in or explicitly bound to its task. `source_alias` must resolve a current alias
from an immutable reference, not treat a previous-task alias as an identity.
`ValidationSources.sealed_material_record(alias)` delegates to an optional host
resolver; without one it supplies no sealed-material receipt. The host owns
receipt authenticity, source namespace, authorship and interrupted-producer policy.
The callback returns validator data, not additional Worker fields or file bytes.

## Execution adapter boundary

[`ExecutionAdapter` and `AdapterCapability`](../src/scidiscovery/operations/execution.py)
are shared protocol/value types owned by `operations.execution`. Use that module directly; an adapter consumer owns admission, collection and
recovery rather than relying on a shared bridge service.

Worker-facing instructions request ordinary scientific files and add no hash
calculation, comparison, verification or hash inventories; the consuming control
performs that bookkeeping automatically and keeps its exact bindings host-private.
`exp` acts as a thin submit/wait helper over a single ordinary
`submit.json`/response slot. Local-mode W scientific views are serviced automatically
by the existing native host for that attempt's lifetime; external mode retains the
declared launcher's service ownership. No agent, daemon, UDS or FIFO is added.
The default local command backend maps accepted input snapshots read-only at their
scientific paths and gives `artifacts/` separate write access. Its task-native
permission configuration excludes native authentication and provider settings;
control originals and the submission slot are absent from the subprocess view.
Network access follows the same supported native permission projection as the
Worker. A launcher or policy that cannot be faithfully projected returns an
explicit unsupported managed launch. Supported prerequisites and permission cases
are defined in the [CLI guide](ROOTLESS_CLI.zh-CN.md); these file views do not provide
a general native physical-isolation or hostile same-user process guarantee.

The built-in local result protocol requires distinct nonempty metric names and
finite numeric values for usable measurements. `unit`, `evaluation_key`, `direction`
and labels are optional author-owned JSON descriptions, without an optimization enum
or scientific text-length limit. The optional `candidates` array in version 1
`artifacts/result.json` explicitly lists all retained models available to A. Each
member needs a distinct artifact path uniquely bound to one declared retained output.
Identity includes the original scope, call and artifact path, independent of list
order; public references name that call and member, such as
`W002-C001/artifacts/model.bin`. A sees the complete catalogue and can choose any
member for exact delivery. Control supplies no sorting, default, or inference from
other outputs. An empty or absent list supplies no candidate; the old single
`candidate` field is not interpreted. Absent or unresolved measurements remain null
without an inferred metric or direction. Invalid individual declarations receive local diagnostics.
Unserializable metadata is omitted only from the mechanical projection, with a
reference to unchanged retained `result.json` bytes; other valid results and the
artifact-bound candidate remain available. Compact previews do not cap originals.
These local declarations do not replace a domain plugin's inputs for actual
calculations or its frozen execution contract.

Configured consumers determine the task's frozen auxiliary response fields.
Unconsumed scientific annotations remain in the original. Malformed configured
optional fields receive diagnostics instead of invented progress or scope. Actual action selection,
exact input bindings, scientific qualification and execution authorization retain
their own checks. Scientific mechanism/disposition lists have no fixed four-item or
128-mechanism capacity; configured call opportunities and finite execution resources
continue to govern admission, including reconciliation of unknown original calls.

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
consumer calls before admission/reservation. Local method presence is not proof of
remote protocol support. The transport's `submission_protocol` maps to
`tcad_submission_protocol`; version 1 advertises the exact submit and retire names.

`AdapterCapability` contains `key`, `kind`, `schema_id`,
`payload_schema_version`, `media_type`, exact `content: bytes`, and
`public_summary: Mapping[str, object]`. Keys must be unique in a nonempty capability
set. The consuming contract owns content limits; port/schema declarations own
payload validation and version identity.

An adapter may supply `worker_capabilities() -> tuple[AdapterCapability, ...]` for
producer-owned scientific discovery and schema reads. Control still freezes and
validates the complete `capabilities()` set; the Worker view does not change that
identity. TCAD exposes implementation authoring and scientific solver facts through
this view. Its V2/V3 execution-package schemas and solver-snapshot binding digests
remain host validation contracts. The consumer omits control-only capabilities and
mechanical proof metadata from Worker discovery and description responses.
An optional `ExecutionInterfaceRegistration.scientific_outputs` callback projects
verified retained output descriptors without building a runtime or querying a
backend. Its signature is `(descriptors, *, projection_directory: Path)`; control
derives this private cache directory from the exact host configuration outside
the immutable managed-call tree. TCAD keeps the complete native manifest private and supplies typed
execution facts alongside unchanged scientific output bytes. Original manifest and
output verification still precede publication.

The optional `cov_execution` package owns `experiments`, `lineage`, `delivery`,
`receipt` and `observer`; its execution entry point remains `cov_execution:experiment`.
Core-only installations expose `local_execution:command` without importing Cov.
Installed providers are discovered through the existing execution interface group.
The registration may declare `scientific_source`,
`context_validator`, `research_defaults`, `engineering_feedback`, `legacy_retirement`
and a `projection_contract`. These trusted optional hooks project retained facts,
validate native contexts or supply new-instance defaults. The projection contract is
frozen with the interface; it is not an installed-version authorization gate.
Core owns call/candidate identity, budgets, permissions, independent review and exact
audit scope. Native run IDs and model layouts belong to providers. `public_result`
is the adapter's strict JSON scientific projection, shared by immediate results and
managed wakes; raw backend results and private bindings are not public feedback.

Managed adapter record mutation uses the host's shared
`research_loop.contracts.record_writer(records, *, blocking=True, exclusive=False)`
lease on `records/record-writer.lock`. Pending inventory takes the exclusive
nonblocking lease inside the original owner writer context. Writers acquire owner
gate, shared record-writer, node/admission, then adapter-private locks, covering the
first preparation write as well. Never upgrade a shared lease to exclusive inside
the context. Lifecycle checks first take the owner gate exclusively and nonblocking,
then node/admission and record locks; the gate serializes these two orders.
Lifecycle checks acquire existing direct record `*.lock` files exclusively and
nonblocking as conservative compatibility for old records, without matching domain
names or loading a provider. Terminal original retention remains offline; a missing
provider cannot erase an unknown call, consumed budget or immutable binding.

TCAD adapter consumers may use the following optional hooks:
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

Importability alone is not a stability promise. Current experiment comparison
and domain-neutral artifact-kind contracts remain supported; absent authoring or
materialization workflows are not supplied by these schema values.

## Canonical identities

`schema.common.canonical_json` normalizes strict supported values, requires string
mapping keys and finite numbers, and emits sorted compact UTF-8 bytes without ASCII
escaping. It supports `SchemaModel` and rejects arbitrary model/dataclass values;
`canonical_sha256` hashes those exact bytes. The figure validator's bundle fingerprint
uses this helper on its locally built list of string-keyed records containing
strings and integer byte counts. Non-string Python mapping keys are outside this JSON input contract.

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
| `scidiscovery.plugin_runtime.transport` | Generic `MCPRouter`, JSON-RPC parsing/error values, bounded Unix socket client/daemon | Domain transport callers, authentication, tool registration and compiled action authority |
| `scidiscovery.plugin_runtime.observation` | `RECORD_DIR`, `normalize_log`, `materialize_launcher` for the optional Worker-executed stdlib launcher | Launch/stop authorization, Run lifecycle and interpretation of observations; other module functions are implementation details |
| `scidiscovery.plugin_runtime.presentation` | Pure JSON field/section/gap/source/parameter/citation constructors, bounded row windows and ancestry within the supplied cohort | Authorized input cohort, provider discovery, output/source validation, aggregate bounds and rendering |

The runtime package performs no imports of service, interfaces or approval UI modules.
Figure, curve and TCAD consume these modules. All three production plugin packages
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
HTML. A consuming UI must validate every source and cap the combined response. Domain
providers retain interpretation and layout of their own scientific fields.

## Calculations and checkpoints

Algorithms return `CalculationResult`: scientific request/result/status, exact numerical
input hashes and bounded safe diagnostics. Existing hashes support numerical replay;
they are not Agent fields or private receipts. `complete_calculation` on the supplied
tool context creates the actual attempt result and seals the private record. Control
checks those hashes against sources actually read. A plugin may pass `public_result`
as an explicit projection of `record.result`: dictionaries may omit fields, while
retained values and list lengths/order must remain exact. The plugin owns scientific
summaries and domain-private field removal. Control verifies this omission-only
projection, retains the complete private record and binds the exact public bytes.
The former `summary` flag is removed. Historical projected records require their
original artifact SHA when they lack an explicit public-byte digest; a receipt
without that identity is rejected rather than reinterpreted by domain prefixes.
`WorkspaceFinalizer.predecessor` retains the exact prior finalizer path and
projection identity for read-only historical contract interpretation. It does not
provide historical draft projection, execution or automatic recovery into the
current contract; those actions reject the historical finalizer explicitly.
`verified_calculations(report)` on
the validation sources verifies receipts/origins before returning result values; it
never returns attempts or recovery state. Both contexts implement the public structural
protocols without a second service registry.

`CalculationResult.validation` is an optional `CalculationValidation` value generated
by a registered evaluator tool, using an already bound `ExperimentPortfolio`, frozen
applicability conditions and exact result source aliases. It carries existing
`ValidationCheckResult` values; it is not an Agent authoring field or a qualification.
The consuming host must supply authentic read receipts and the paired manifest,
verify that criteria and conditions preceded measurements, and bind any claimed
progress to the exact scientific check. The retained value does not implement
progress deduplication or admission. The curve evaluator checks an exactly matching
prebound curve contract, finite values, units and thresholds; other tools may omit
this optional validation. Relative improvement is not supported. Absent validation
is omitted from serialized results. Numerical replay remains separate from it.

`ExperimentReview.scientific_test` may optionally hold a `ReviewedTestConclusion`
using scientific plan/check names and `ValidationCheckResult`. This is a schema
value, not proof that an independent review occurred or that a host admitted a
scientific advance. The host must bind the exact independent subject and prior
check; a general quality pass cannot replace that evidence.

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
part of the deleted Local Worker host, rather than a current thin assembly path.
A caller supplying these callback contracts must supply exact implementation
values and a derived diagnostic workspace.

## Diagnostics and host obligations

Compiled authoring schemas, declared semantic rules and validators must describe
the same scientific contract. A validator must not introduce a scientific rule
that was absent from the Worker's authoring contract. Bound semantic checkers use
`scidiscovery.operation_contract.SemanticRuleViolation` for an intentional,
Worker-correctable rejection. Undeclared rules, missing required context and other
checker exceptions are engineering faults; they must not become automatic
scientific retries. Optional missing inputs cannot silently become mandatory.

Tool handlers declare safe caller corrections with
`scidiscovery.operation_contract.ToolInputError(code, message, path=...)` or explicit
`DiagnosticError`/`contract_diagnostic` details. Messages must be authored explanations,
not raw exception strings or echoed input. Unexpected component/transport failures
remain engineering failures. Control stores sanitized diagnostic details with an
internal format version; caller-supplied flags never confer trust.

Experiment services may implement `experiment_pending_diagnostics(run_ids, inputs)`
to return bounded, owner-declared diagnostics identifying outstanding named jobs and
their status/cancel calls. The existing `experiment_activity` callback remains the
fallback and cancellation hook. Neither callback grants execution authority.

## Validation status

See the [release validation scope](RELEASE.md) for candidate-bound
results and open acceptance limits. Plugin API declarations and focused source
checks do not establish live model, solver or deployed-service qualification.

### Workspace and authoring visibility

`WorkspaceMaterializationResult` lets a plugin return domain read/edit rules and
report guidance on a supplied workspace request. A consuming host must materialize
the exact assignment, preserve its frozen contract and provide any startup or
navigation records. These hooks do not generate `worker-start.json` or implement
assignment delivery on their own.

A paired `WorkspaceFinalizer` declares which fields its deterministic callback
completes. The consumer must deliver the paired authoring schema and only the
explicitly declared frozen input bytes. Scientific rules and source choices must
remain visible to the Worker; internal bindings and receipts must remain under
host control. A plain finalizer supplies no projection that hides control fields.
