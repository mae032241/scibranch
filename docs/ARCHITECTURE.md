# SciBranch architecture: W-led research with a thin control layer

[简体中文](ARCHITECTURE.zh-CN.md) | English

SciBranch runs a scientific research loop from an explicit objective, selected
original inputs, declared native permissions and finite budgets. W owns sustained
research and conclusions; the host preserves identity, enforces admission and
records what actually happened. Evidence, scientific qualification and execution
permission are separate facts.

`scid-research` is the application core. `scid-experiment` is the managed execution
entry point. The optional instance UI uses the same public controls and read views;
it adds no scheduler or state authority. There is no LLM Root or MCP scheduler.

<a id="ownership-and-flow"></a>

## Research flow

```text
Operator: objective + exact inputs + configuration + finite budgets
  -> prepare: freeze instance identity and effective settings
  -> one controller writer: admit work, bind inputs, budgets and permissions
  -> W: observe -> choose a question -> implement -> run -> compare -> adapt
       |-> H: alternative explanations ---------> W
       |-> E: bounded evidence -----------------> W
       |-> ordinary A: diagnostic advice --------> W
       |-> context handoff ----------------------> new W
       `-> decision -> mandatory Final A
                         |-> one bounded W correction -> one reassessment
                         |-> finish: no delivery
                         `-> deliver exact candidate -> submit -> confirm

W -> scid-experiment -> frozen execution interface -> Local / domain adapter
                                               -> same-call query/collect/cancel
UI / CLI -> same controller operations and sealed result views
```

W starts autonomously and can explore before a candidate or mature hypothesis
exists. Before a managed batch it declares an exact mechanism and a question with
an observation that could change the next step. Small work stays with W or its
permitted native subagents; W chooses H/E consultation when useful. A weak score,
a completed call or a context change does not require an A cycle.

Ordinary A diagnoses evidence-backed lock-in, ignored counterevidence, goal drift
or severe reasoning flaws, and returns advice to W. W decides how to respond.
Final A independently assesses delivery claims, reproducibility, unmet objectives
and limitations. It may reject every candidate. When allowance remains it may
request one concrete correction within the original scope and budget, followed
by at most one reassessment. Exhaustion permits final reporting from originals,
with no new exploration. Final A is required before delivery; it does not replace
the separate explicit formal qualification and execution gates.

## Owners and boundaries

| Owner | Responsibility |
| --- | --- |
| Operator | Select objective, exact originals, native permissions, budgets and explicit control requests. |
| W | Research direction, mechanisms, experimental design, implementation, adaptation and candidate choice. |
| H / E | Answer W's concrete explanation / bounded evidence question and return to W. |
| Ordinary A / Final A | Diagnostic advice / final scientific delivery judgment; their authorities differ. |
| Controller | One advancing writer, immutable task and call identities, accepted decisions, admission, budget and pause. |
| Backend | Complete native lifecycle, permission projection, observations, terminal evidence, recovery and authorship facts. |
| Execution adapter | The selected execution contract and each original external call's lifecycle. |
| Result publisher / UI | Preserve authored originals and render derived navigation; neither authors scientific conclusions. |

Formal independent review binds the exact subject, complete required contributor
proof, an independent reviewer and an explicit sealed verdict. Ordinary A, a
passing test, transport completion and user chat supply none of those grants.

## Backends and execution

Codex is the default backend; explicit protocol transport and experimental Pi use
the same complete `Backend` class. Capabilities and limitations are projected to
control and UI through that boundary. There are no separate external launch,
observation, recovery or authorship subprotocols. Backend-specific profiles,
permissions, resources, Skills and defaults belong to
[Research configuration](RESEARCH_CONFIGURATION.md).

Managed execution freezes the selected adapter interface and exact accepted call.
Host-owned Git capture binds selected code; scientific inputs and outputs keep
their original references. Linux read-only mounts protect selected scientific
inputs at launch. Workers and same-user processes remain trusted: native
permissions and these mounts provide a soft boundary, not hostile-process isolation.
A backend or adapter capability is not scientific qualification or an execution grant.

Optional telemetry stays unknown when unavailable. Known usage lower bounds are
retained; missing billing alone does not stop ordinary work. Required identity and
admission evidence stays strict. Chunked reads remove raw RPC/selector/session
byte quotas; complete JSON parsing still uses memory proportional to the value.

## State and recovery

Settings, jobs, authored outputs, accepted calls, receipts and the control ledger
are the originals. Read views, lineage, candidate navigation and UI caches are
projections of them. One operation owner loads and accepts each immutable fact
once, then passes it to consumers; mutable state refreshes at its writer or
admission boundary. The controller's recovery cursor and single-step advancement
reuse accepted decisions. The internal immutable `Route` value adds no durable
schema or second state authority. `guidance.py` owns new-task instruction text;
recovery never regenerates retained task instructions.

The bounded `execution_facts` view describes managed calls from accepted values.
Existing call feedback, W continuation and Final A share this presentation; the
accepted-prefix publisher carries its frozen history without scanning old logs.
Scope, missing results and omissions remain explicit. Execution success does not
establish scientific validity. See [execution facts](RESEARCH_LOOP_CONTRACTS.md#execution-facts)
for the view's coverage and reading limits.

W may preserve optional [authored notes](RESEARCH_LOOP_CONTRACTS.md#research-notes)
after reading a result. They persist during the task, retain revisions and enter
existing recovery/navigation paths, separately from execution facts.

New nodes bind an exact operational configuration revision. Saving edits queues
future settings; it does not change existing tasks, reset budgets or launch work.
Pause prevents new work while accepted originals retain their own limits. Typed
pause diagnostics preserve already admitted resume actions. Native continuation,
output repair, execution wake and service recovery each require their own frozen
permission and retain the original task, home/session, calls and consumption.
Unknown external submissions require reconciliation of that same call and are
never automatically resubmitted. Fork and deletion use explicit identities and
quiescence checks; neither creates new qualification or restores missing science.

The supported scientific contract is strategy 3 with advisory scope 1;
supervision is v4 or `None`, and enabled fragments use handoff v2 or may be `None`.
Frozen v4 requires a boolean `require_fresh_input` (new default `true`, configurable
`false`). Earlier scientific policy variants and missing required markers are
rejected without migration. Protocol 33 and worker/lineage/final-completion format
version 1 remain current. Automatic recovery defaults share eight attempts and
5–300 second backoff; explicit native profiles and consumed original limits remain
binding. Precise fields and limits belong to the configuration reference.

## Implementation and reference map

Files below are in `src/scidiscovery/research_loop/` unless stated otherwise.

| Boundary | Main owners |
| --- | --- |
| Preparation, routing and advancement | `research_config.py`, `controller.py`, `runtime.py`, `instance_control.py` |
| Native lifecycle and recovery | `backends.py`, `native.py`, `execution.py`, `continuation.py`, `native_recovery.py` |
| Scientific contracts and guidance | `contracts.py`, `semantic.py`, `guidance.py`, `instance_science.py` |
| Calls, code and execution authority | `experiments.py`, `local_experiments.py`, `code_versions.py`, `execution_inputs.py`, `execution_authority.py` |
| Qualification and delivery | `qualification.py`, `formal_review.py`, `selection.py`, `delivery.py` |
| Controls, lifecycle and UI | `control_operations.py`, `configuration_updates.py`, `user_guidance.py`, `fork_service.py`, `instance_delete.py`, `instance_ui.py` |

- [Research-loop contracts](RESEARCH_LOOP_CONTRACTS.md): exact routing, materials,
  identity, budgets, final reporting, fork/delete and recovery rules.
- [Research configuration](RESEARCH_CONFIGURATION.md): declarations, backend
  profiles, permission limits, projects, Skills and recovery fields.
- [CLI guide](ROOTLESS_CLI.zh-CN.md): preparation, operation and original-call maintenance.
- [Execution adapter API](PLUGIN_RUNTIME_API.md): the surviving execution extension seam.
- [Release scope](RELEASE.md) and [TCAD qualification boundary](../plugins/tcad_artifact/README.md#qualification-boundary):
  implemented support and the evidence required for stronger claims. Source,
  synthetic and transport checks do not qualify real scientific outcomes.
