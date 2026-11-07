# Harbor task-container experiments

This optional host package registers `harbor_execution:experiment`. SciDiscovery
and its native scientific participants stay on the host; accepted experimental
code runs in the exact original Docker task container owned by Harbor. The
provider has no Harbor dependency and does not create, replace, stop or remove
the task container. Harbor owns its lifecycle and any verifier invocation.

The optional host entry `harbor_execution.harbor_agent:SciDiscoveryHarborAgent`
requires the installed host plugin and Harbor 0.25. Its required
`config_template` agent option names an absolute TOML template path. Optional
options are `scid_argv` (default `["scid-research"]`), `max_seconds` (default
1800), and `poll_seconds` (default 2). The Harbor model must equal the template's
`agent.model`; H/W/A/E keep the template's declared native execution profiles.
The entry renders explicit host bindings privately, supplies the original task
objective and public starter, and leaves training data in the original container.
Its final delivery publishes only the exact confirmed task artifact. An ordinary
sealed A `finish` can instead end without nomination or delivery. The host entry
accepts that outcome only from the successful CLI terminal report bound to the
same instance, goal and latest sealed node, with host cleanup confirmed. It
preserves native usage, publishes no artifact and leaves the original task
container available for Harbor's verifier to grade its retained files. This
completion makes no scientific success claim; missing delivery alone, paused or
waiting work, and incomplete or failed host work do not qualify.

The tracked host examples are
[symbolic-regression.research.toml](harbor_execution/examples/symbolic-regression.research.toml)
and [symbolic-regression.trial.json](harbor_execution/examples/symbolic-regression.trial.json).
Copy both and replace the absolute-path placeholders in the trial JSON. Keep
the trial's `override_timeout_sec`, the entry's `max_seconds`, and the research
template's `budget.max_seconds` aligned at 1800 seconds. The example task source
is pinned to Terminal Bench v0.1.0 commit
`f81afac4f11048e77a15dfc8fb1dbfb897fea0ce`.

Install this package alongside SciDiscovery in the host environment. A trusted
host integration must bind the canonical full container ID for the original
trial. Container names, abbreviated IDs and selection by recency are not
accepted. Configuration checks are local and make no Docker calls:

```json
{
  "executor": "harbor_execution:experiment",
  "configuration": {
    "container_id": "<exact 64-character lowercase hexadecimal ID>",
    "container_root": "/tmp/scid-trial-<original-trial-identity>",
    "docker_argv": ["docker"],
    "python_argv": ["/usr/local/bin/python", "-B", "-u"],
    "max_seconds": 60,
    "max_output_files": 128
  }
}
```

Select the original image's actual scientific interpreter. No interpreter,
scientific dependency, framework, credential or native agent environment is
installed in the container. `environment` supplies explicit string values;
`HOME`, `PYTHONHOME` and `PYTHONPATH` overrides are rejected. Optional `user`
selects an existing task-container user. Optional finite `deadline_epoch` retains
the whole-trial cutoff; each call also retains its original `max_seconds`
deadline. Recovery never grants a new execution window.

This initial backend supports only an original root effective task-container
user. It rejects configured or observed non-root users before upload, and never
forces a different user or changes native permissions. Supporting another user
requires a separately validated code-transfer ownership contract.

Use the existing managed CLI with `--script code/experiment.py` and optional
script arguments after `--`. The declared directory capability receives the
whole host-frozen logical `code/` directory, including authored sibling modules.
Project selection uses its existing complete frozen code contract. The provider
rejects `--experiment-dir`, which describes a Local host runtime. Standalone
single-file calls upload only the accepted source through an isolated launch
view, excluding private call records.

Each call has independent `code/`, `work/` and `control/` locations below the
trial scratch root. The experiment's current directory is `work/`, outside
`/app`. Read original public task data by its absolute path, for example
`/app/training_data.npz`. Experiments must not modify other `/app` files; final
delivery owns publication of the selected task artifact such as
`/app/regressor.py`. This preserves the original task user's native permissions
and remains a same-user soft boundary.

Declare scientific observations with the existing optional local-result
protocol at `artifacts/result.json`, using `version: 1`, `metrics`, `outputs` and
optional `candidates`. Output and candidate paths use canonical
`artifacts/<file>` names. The provider downloads only that result and explicitly
declared output files, with finite deadlines and file-count limits, directly into the
existing retained call output directory. It never scans the container or its
artifact tree. Metric meanings, candidate selection, scientific qualification
and final delivery remain with the existing scientific workflow.

Scientific originals, arrays, artifacts, scripts/code and logs have no framework
per-file or aggregate byte quota. Legacy `max_file_bytes`, `max_total_bytes` and
log/result byte configuration remains accepted and frozen without enforcing file
quotas; it is not exposed as an active UI file-size control. Transfer uses chunked
IO and preserves exact `size_bytes`, including declared-size mismatch and real
truncation checks, canonical paths, symlink safety and explicit permissions.
Compact result declarations, delivery manifests, handoffs, control metadata and
public views remain bounded. Removing file-byte quotas does not promise unlimited
RAM or hard isolation from same-user processes.

Ordinary W admission uses the common retained acceptance, budgets, permissions
and a matching dispatch lease. The optional formal reviewed-subject
`execution_resource_policy` host gate is unsupported and fails before transport.
Neither configuration nor a completed container command supplies scientific
qualification or execution authority.

Dispatch intent is retained before the first Docker effect. A lost response
keeps that exact call unknown until same-call lookup can reconcile it; lookup,
query and repeated submit never launch it again. Query retains native terminal
facts but does not download or parse results. Cancellation addresses only the
original call's process group and remains pending until shutdown is confirmed;
an already terminal success or failure is preserved.

Result collection has one exact-call writer lease. Native terminal facts are
saved before fallible output collection. Genuine raw stdout and stderr are
required even when empty. Complete downloads publish sealed final files
atomically; interrupted transfers retry the same original output, and already
published files are adopted without another download. Completion is published
only after the original outputs have been accounted for. Missing or invalid
declared artifacts, or an oversized compact result declaration, produce actionable author diagnostics alongside
the known native completion. Available metrics and artifacts remain retained;
unavailable artifacts produce no candidate. W can then run corrected code through
ordinary admission. Missing or invalid optional result declarations produce no
invented candidates. Container
IDs, native bindings and transport diagnostics remain private; public status and
results use the existing sanitized projections.

After a transport or storage failure leaves collection unresolved, `result`
returns the retained safe diagnostic without repeating the failed transfer.
Query still reports the known native state. An explicit `collect` retries only
the original outputs, reusing complete retained files; it cannot dispatch code
again. Every transfer receives the remaining collection time and original trial
cutoff. Original raw logs are retained in full without file-byte truncation;
compact declarations retain their document bounds.

The fake-backend tests cover admission, lost replies, no resubmission, retained
terminal facts, single collection, candidates, cancellation races, interrupted
collection and binding privacy. Docker/process tests and a pinned Harbor host
entry integration require separately scoped verification; source tests alone
make no benchmark or scientific outcome claim.
