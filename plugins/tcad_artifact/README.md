# SciBranch TCAD execution plugin

[简体中文](README.zh-CN.md) | English

The thin research CLI loads the `tcad_execution` execution interface. It freezes
the selected adapter, transport configuration and implementation inputs, then uses
the host's trusted execution gate for side effects. Installation registers the
plugin; it does not contact or qualify a remote solver. See the
[CLI guide](../../docs/ROOTLESS_CLI.zh-CN.md) and
[transport contract](../../docs/tcad_transport_contract.md) for configuration and calls.

Select `tcad_execution:tcad` with an explicit `config_file`; inline TCAD
configuration and implicit deployment defaults are rejected. The plugin file is
a JSON/TOML object with `transport = "command"` and `command_config_path`, or
`transport = "socket"` and `socket_path`. Local paths resolve relative to their
own configuration file. Preparation checks command/SSH schemas, referenced local
files and optional `implementation_compilation.capability_snapshot_path` without
contacting a backend or creating adapter state. Settings freeze with the instance;
editing the top-level plugin file does not amend an existing instance.
New socket-backed preparation is rejected before admission or submission; exact
old-call queries remain supported. Use command transport for new managed execution.

This adapter retains execution schemas, project materializers, transport and policy
checks. Curve/PLX/log analysis belongs to the [curve-analysis Skill](../../skills/curve-analysis/SKILL.md).
See the [execution API](../../docs/PLUGIN_RUNTIME_API.md).
Scientific Workers author solver projects and assess results. Deterministic code
resolves exact source, case anchors, capability, file bindings and resource policy.
It does not invent physical models or generate a solver deck from an underspecified request.

## Execution backend

`tcad_control` manages operational run directories and detached process state. It
provides capability/policy discovery, exact submission lookup, submission, status,
cancellation, inspection and collection. Submission is idempotent for the exact
frozen descriptor. If a response is uncertain, recover the original identity;
unavailable lookup does not authorize another submission.

The accepted `TCADJobSpec` binds an archive descriptor and member manifest, a
deployment-owned tool profile, trusted execution purpose, resource limits and
expected outputs. Job protocol schema version 5 includes `collect_generated_outputs`
and wall, CPU, memory, output and total storage limits. Control and runner must use
compatible protocols. Solver capability snapshots use `tcad.solver-capability.v2`;
private executable paths, environments and fixed arguments remain outside the
public snapshot. Counts and digests bind private settings; public arguments require
administrator opt-in. Changing scientific inputs creates a new implementation and
requires applicable qualification to be established again.

The bounded SSH command transport invokes `remote_runner_py36.py` through an
existing authorized OpenSSH connection. `transfer_chunk_bytes` defaults to 1 MiB.
The application installer and `deploy/reinstall.sh` publish local application code.
Remote runner deployment is a separate administrator action using explicit
configuration and authorization; see `deploy/install_ssh_tcad_runner.sh` at the
repository root. Updating local code does not update a remote protocol.
Examples and installation success grant no execution permission.

## Policy, accounting and recovery

Administrator configuration must supply valid `agent_execution_policy`, `runner`
and `debug` sections. See [policy example](config/execution-policy.example.json) and
[runner example](config/remote-runner.example.json). Missing or invalid policy is
rejected. Example thresholds are configurable values, separate from RAM or transfer
quotas. Policy authorization is bound to the exact request, package, compiled
identity, budget and policy digest; independent review is not an execution decision.
The runner rechecks authorization at submission and freezes resource settings.
The standalone remote runner reserves a concurrency slot as soon as it creates
the accepted run directory, including startup before the worker is running.
Incomplete or uncertain starts keep that slot until terminal completion is proven;
retrying the same descriptor never launches another worker.

Worker output collection rejects nonregular files, including FIFOs, without
blocking on their open. Collection, recovery collection and diagnostic finalization
share the existing `collection_total_seconds` window and check cancellation.
A stopped collection retains already captured records and publishes its failure
or cancellation; it does not grant another execution.

Reserve work before submission. Renaming or revising a scientific project does not
create a fresh allowance. Terminal observations settle elapsed wall time and storage
high water; missing observations and unknown submissions retain reservations.
Policy tightening retains prior consumption. A reservation may be released only
upon authoritative permanent non-submission proof for the exact original call and
descriptor, consumed by the original budget owner. An absent lookup result,
cancellation, timeout or unknown state is not such proof. Diagnostic attempts have
their own configured bounds and durable identities.

Total storage includes logical inputs, intermediates, outputs and logs; deleting
inputs does not remove their charge. Retained stdout mirrors are conservatively
included. Runners sample directory size and stop the process group on excess.
Their manifest reports observed high water, sampling interval and overshoot;
this is not an instantaneous peak or filesystem hard quota. Scientific input
staging, archives, transfers and collection stream file bytes. Small JSON and
previews have separate limits. Transfer allowances do not authorize larger jobs.

SDevice grid inputs bind exact scientific file originals and implementation slots;
materialization retains the source bytes and provenance. A cached response alone
is not trusted execution evidence. Recovery must verify original submission and file
identities, preserve accounting, and reconcile the same call before any next action.

## Trust and qualification

The backend owns execution facts; the research controller owns scientific bindings,
budgets and permission decisions. A local domain daemon separates processes and
faults while retaining the same-user trust boundary. Same-user shells may reach
control files, sockets and secrets. See [Security](../../SECURITY.md).

Solver projects declare physical code, inputs, parameters and raw TDR/PLX/PLT/log
outputs. Parsers and curve scoring derive observations and metrics; Workers own
scientific conclusions. Native solver, SSH and scientific-chain qualification must
bind the exact implementation and environment.
The plugin does not distribute Synopsys executables or licenses.

## Qualification boundary

This section owns the current TCAD qualification boundary. The
[architecture](../../docs/ARCHITECTURE.md) owns control responsibilities; the
[transport contract](../../docs/tcad_transport_contract.md) owns domain execution
and collection behavior.

The thin research loop can freeze a selected TCAD execution interface, retain exact
prepared inputs and collected originals, request independent review of an exact
subject, bind execution permission and account for finite resources. Optional native
implementation compilation supplies nonempty input chains. Self-contained V2
packages and declared observer sources can contribute verified input bindings and
authorship within their frozen declared scope. Missing or incomplete source
declarations retain uncertainty; they do not establish that arbitrary native or
solver file reads are closed.

Focused source, synthetic-process and loopback HTTP checks cover selected binding,
review, budget, original-call recovery and collection paths. They do not qualify
all combinations, installed solver behavior, physical models or scientific claims.
Removing the old Run/SQL host and checking wheel/source packaging adds no solver
qualification.

The maintained evidence does not establish a real Sentaurus SProcess or SDevice
execution/collection/parser loop, a qualified TDR metadata provider, or a
figure-specific scientific claim. This is an evidence boundary, not a current probe
of this host's executables, licenses or remote runners. Mock outputs, synthetic
PLX/PLT fixtures, historical solver results and passing engineering test counts
cannot establish those qualifications.

Promotion requires evidence bound to the exact candidate and controlled runner:

- Provide a usable solver license and freeze solver capability, release evidence,
  approved executable/arguments, environment and exact input package; reject
  capability drift before execution.
- Complete an authorized real SProcess smoke through execution, collection and
  parsing. Retain exact input/output originals and their bindings, original logs,
  release and parser evidence, failures, limitations and the independent review
  required by the selected contract. Control uses the architecture's reference
  contract; this step does not require scientific-file hashes.
- Qualify SDevice separately with a usable license, real TDR evidence and a qualified
  metadata provider. SProcess success does not supply this qualification.
- Assess physical-model or figure claims from the exact scientific evidence and
  applicability conditions; execution success and parser acceptance alone do not
  establish scientific correctness.

Execution permission follows the frozen policy or exact human decision. Scientific
review, solver qualification and execution authorization remain separate. The
[release scope](../../docs/RELEASE.md) describes other unqualified deployment and
recovery paths.
