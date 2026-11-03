# Installation

[简体中文](INSTALL.zh-CN.md) | English

## 1. Supported Environment

- Linux or WSL2 with systemd
- Python 3.10 or newer with `pip`, `setuptools>=68`, `packaging`,
  `pydantic>=2,<3`, and `jsonschema>=4,<5`
- Codex CLI
- `poppler-utils` for bounded PDF extraction
- `bubblewrap` for isolated worker analysis
- `curl` for service health checks
- OpenSSH client for remote TCAD execution

Sentaurus, its license, and any VM are external prerequisites.

On Debian, Ubuntu, or WSL:

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-pip poppler-utils bubblewrap curl openssh-client
```

The installer uses the selected base Python executable and installs only the
repository's application packages into `/opt/scidiscovery/site`. It does not
create a virtual environment, download dependencies, or modify the selected
Python environment. Provide third-party dependencies before deployment. For a
Conda base environment:

```bash
conda install -n base -c conda-forge \
  'pydantic>=2,<3' 'jsonschema>=4,<5' 'setuptools>=68' packaging pip
```

The install prints each phase and performs the local package build before it
stops an existing service deployment. Activation is transactional: exact
release/configuration targets and SQLite databases are snapshotted, and a
failed service or MCP health check restores the previous files and service
state. Successful transaction evidence is retained under
`/var/backups/scidiscovery/transactions/`.

The generic transactional reinstall entrypoint is:

```bash
SCID_WORKSPACE="$PWD/workspace/<project-name>" \
  deploy/reinstall.sh --dry-run
SCID_WORKSPACE="$PWD/workspace/<project-name>" \
  deploy/reinstall.sh reinstall
```

`reinstall.sh` installs no domain plugin by default. Select
`tcad_artifact,curve_score` or other plugins explicitly with `SCID_PLUGINS`, and an external adapter with
`SCID_TCAD_COMMAND_CONFIG`. `install` and `reinstall` use the same
transactional flow; no argument is equivalent to `install`.

Figure curve extraction uses the three generic domain plugins. Preinstall
Pillow within the plugin's declared package range and Poppler `pdfimages`.
The Agent supplies axis, tick, and scientific identity information after
viewing the source figure; the runtime does not use OCR or Tesseract. The
installer records the actual Pillow and `pdfimages` versions and the absolute
executable path.

```bash
export SCID_PYTHON=/absolute/path/to/service/python
"$SCID_PYTHON" -m pip install --no-index --find-links /srv/scid-offline/wheels 'Pillow>=10.0,<13.0'
export SCID_PLUGINS=tcad_artifact,curve_score,curve_figure_evidence
export SCID_PLATFORM=codex SCID_WORKER_BACKEND=local
export SCID_WORKSPACE=/srv/scid-project
export SCID_CODEX_LAUNCH_ROOT=/srv/scid-codex
export SCID_SERVICE_USER="$(id -un)" SCID_SERVICE_GROUP="$(id -gn)"
deploy/reinstall.sh --dry-run
# After the installation/review gates have passed:
deploy/reinstall.sh reinstall
```

The workspace and launch directories must already exist and be separate from
the checkout. Run the generic wrapper as the service user. The installer executes
real Python imports, version calls, and one real embedded-PDF-image recovery under that service
identity and systemd's effective default PATH before creating the installation
transaction. The operator's shell PATH is not the dependency check.

The verified binding is written once to
`site/curve_figure_evidence/figure_dependencies.json` in the staged installation,
then checked again with the selected service Python before switching and after
installation. Activation makes the installation prefix read-only. Runtime uses
the recorded absolute `pdfimages` path and rechecks its version. A tool-version
change requires a new installation record. An ordinary wheel without this
supplied record can compile but cannot pass figure installation.

The current trusted Local backend provides soft isolation, not an OS sandbox.
The production package, runtime resources, default configuration and explicit
Worker context must not include case geometry, historical answers or paths that
act as answer-discovery inputs. Historical audit documents may retain locators,
but the runtime must not consume them. Record readable host probes honestly and
claim only that those files were not supplied or observed in use; `SEC-002`
remains a known issue. Check for queued or running old-contract Runs before any
approved switch; cross-digest recovery is not promised. The figure curve
Operation does not bind the external `scientific-paper-evidence` skill.

Local Workers may discover installed Skills and read their references on demand;
Operation tool permissions remain unchanged. The separately managed TCAD Skill
has deployment-directory integrity checks and transaction rollback. Its bytes
are not bound to Operation or Run identity, so per-Run reference replay is not
promised. Validate discovery and helper use in fresh compiled Agent sessions
after installation; generated-prompt tests alone do not prove that behavior.

After the curve-analysis update, check the compiled catalog in a fresh session: `science.result.diagnose.v1` accepts experiment results directly, with optional curve bundle and metric report inputs. With TCAD enabled, `tcad.result.analyze.v1` provides `worker_tcad_curve_score` for bound raw outputs and reference material. These are alternative analysis entrypoints, not sequential steps. Reinstallation does not refresh Worker tools in an existing session; restart the session. Historical records remain, while current preflight determines qualification after contract changes.

## 2. Obtain the Source

```bash
git clone <repository-url> scidiscovery-agent
cd scidiscovery-agent

deploy/init_workspace.sh ingaas-paper
export SCID_WORKSPACE="$PWD/workspace/ingaas-paper"
```

The Git repository contains framework source only. Every research project must
live under the Git-ignored `workspace/<project-name>/` tree; papers, parameters,
research ledgers, simulator projects, logs, and results belong in that project's
subdirectories.

Do not run the services from `/mnt/c` under WSL. Use the WSL filesystem, for
example `~/src/scidiscovery-agent`.

## 3. Select the AI Platform

`SCID_PLATFORM` accepts only `codex`. It generates `.codex/config.toml`, role
TOMLs, and the managed generic scheduler block at the framework source root;
nested workspaces inherit that configuration.

Generated files contain absolute local paths. Regenerate them on every machine
and do not commit them. A workspace `AGENTS.md` contains project-specific
constraints only; installation migrates the old layout by removing its
duplicate managed scheduler block.

## 4. Local Deployment Smoke

This mode validates the services and MCP surfaces without running a scientific
solver. The internal TCAD controller allow-lists `/bin/true` only.

```bash
PYTHON="$(command -v python3)"

# Optional source preview. It requires the dependencies to be importable by
# the selected base Python.
SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_PYTHON="$PYTHON" deploy/install.sh --dry-run

sudo SCID_PYTHON="$PYTHON" \
  SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_SERVICE_USER="$USER" \
  SCID_SERVICE_GROUP="$(id -gn)" \
  SCID_PLATFORM=codex \
  SCID_WORKER_BACKEND=local \
  deploy/install.sh install
```

The installation creates:

- `/opt/scidiscovery/site`: immutable installed Python packages;
- `/var/lib/scidiscovery`: control-plane state;
- `/var/lib/scidiscovery/tcad`: execution state only for local-adapter mode;
- `/etc/scidiscovery`: generated secrets and execution policy;
- `/run/scidiscovery/control.sock`: Root MCP socket;
- `scidiscovery-control.service`;
- `scidiscovery-approval-ui.service`;
- per-Operation local stdio MCP profiles for supported Agent Operations; no central worker service;
- `tcad-control.service` only for local-adapter mode.

The approval UI listens only on <http://127.0.0.1:8765>.

Restart Codex after installation. Start it directly from the framework root:

```bash
codex -C "$PWD"
```

You may also start it in the default nested workspace, which inherits the root
MCP and Subagent configuration:

```bash
codex -C "$SCID_WORKSPACE"
```

When Codex is actually launched outside both the source root and workspace, set
that existing absolute directory as `SCID_CODEX_LAUNCH_ROOT`. The installer
generates, backs up, and verifies its `.codex` and `AGENTS.md` in the same
transaction so the launch directory cannot retain stale MCP definitions. When
`SCID_WORKSPACE` points outside the source tree, the installer emits an
equivalent project-local runtime profile there because it cannot inherit the
framework root. This does not duplicate research data or control-plane state.

```bash
export SCID_CODEX_LAUNCH_ROOT=/absolute/directory/where/codex/is/started
```

Do not rerun the installer for every Codex session. Reinstall only for the
initial deployment, an installation-configuration change, or an installed-code
update.

## 5. Remote TCAD VM

### 5.1 Configure the VM runner

Create a private local copy of the example; do not edit the tracked example with
real license or host data:

```bash
mkdir -p config/local
cp plugins/tcad_artifact/config/remote-runner.example.json \
  config/local/remote-runner.json
chmod 600 config/local/remote-runner.json
```

Edit the copied file:

- set the real `sprocess`/`sdevice` executable paths;
- set `STROOT`, `PATH`, and license environment variables;
- add one unique `profile_id` for each allowed solver profile;
- set VM-user-owned state and exchange roots.

The example explicitly contains one `sprocess` and one `sdevice` profile. Keep
`solver_kind` and non-empty `release_evidence` on both profiles, and point each
at an administrator-confirmed executable. Do not infer these values from an
executable name or from the other profile's environment.
Copying the file and changing only its permissions is not configuration; the
installer rejects a byte-for-byte copy of the tracked example.

Install the dependency-free runner through an existing passwordless SSH path:

```bash
SCID_SSH_DESTINATION='tcad@vm-host-or-ip' \
SCID_SSH_IDENTITY="$HOME/.ssh/id_ed25519" \
SCID_REMOTE_RUNNER_ROOT='/home/tcad/scidiscovery-tcad' \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install
```

The script does not create an SSH key and does not install a VM system service.
It uploads a Python 3.6-compatible runner under the VM user account.

For VMware DHCP discovery, set all of the following instead of a fixed
`SCID_SSH_DESTINATION`:

```bash
export SCID_SSH_DESTINATION_FALLBACK='tcad@192.0.2.10'
export SCID_VMRUN_EXE='/path/to/vmrun'
export SCID_VMX_PATH='/path/to/guest.vmx'
```

### 5.2 Configure the WSL/Linux transport

```bash
sudo install -d -o root -g "$(id -gn)" -m 0750 /etc/scidiscovery

sudo install -o root -g "$(id -gn)" -m 0640 \
  plugins/tcad_artifact/config/ssh-transport.example.json \
  /etc/scidiscovery/tcad-transport.json

sudo install -o root -g "$(id -gn)" -m 0640 \
  plugins/tcad_artifact/config/command-adapter.example.json \
  /etc/scidiscovery/command-adapter.json
```

Edit `/etc/scidiscovery/tcad-transport.json` and set:

- SSH executable and optional identity;
- destination or VMware resolver;
- known-hosts file and stable host-key alias;
- remote runner, config, and exchange paths.

After an active transport exists, use it for a standard full install whenever
the runner code or private profiles change. This is also the migration path
from an older single-profile config to the current dual-profile config:

```bash
SCID_PYTHON="$(command -v python3)" \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install-from-transport --dry-run

SCID_PYTHON="$(command -v python3)" \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install-from-transport
```

`install-from-transport` reads `/etc/scidiscovery/tcad-transport.json` by
default; set `SCID_TCAD_TRANSPORT_CONFIG` to select another absolute path. It
reuses the configured SSH executable, identity, destination resolver,
known-hosts, and exact remote paths. It runs `tcad_capabilities` against the
candidate runner and config in a temporary directory before replacing both;
a failed post-commit probe restores the previous files.
Every install mode that writes the VM requires an explicit
`SCID_REMOTE_RUNNER_CONFIG` and refuses to deploy the tracked example as the
private configuration.

Use `upgrade-code` only when the remote `runner.json` already conforms to the
current schema and only code must change. It neither uploads nor migrates the
configuration and fails closed against an old schema:

```bash
SCID_PYTHON="$(command -v python3)" \
deploy/install_ssh_tcad_runner.sh upgrade-code --dry-run

SCID_PYTHON="$(command -v python3)" \
deploy/install_ssh_tcad_runner.sh upgrade-code
```

For Windows OpenSSH from WSL, a typical executable is
`/mnt/c/Windows/System32/OpenSSH/ssh.exe`; identity and known-hosts values use
the syntax expected by that executable.

Install the services with the external adapter selected:

```bash
PYTHON="$(command -v python3)"
sudo SCID_PYTHON="$PYTHON" \
  SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_SERVICE_USER="$USER" \
  SCID_SERVICE_GROUP="$(id -gn)" \
  SCID_PLATFORM=codex \
  SCID_TCAD_COMMAND_CONFIG=/etc/scidiscovery/command-adapter.json \
  deploy/install.sh install
```

External adapter mode validates the adapter configuration; the external executor
supplies execution policy. A leftover local `tcad-policy.json` is neither validated
nor rewritten or chmod/chowned in this mode. Local socket mode still requires an
administrator-supplied complete policy matching the current schema. Successful
installation does not establish live remote policy or solver acceptance.

TCAD `agent_execution_policy` now has separate configurable thresholds:
`max_wall_time_seconds` is the per-request limit (example: 3600 seconds), and
`max_total_wall_time_seconds` is the cumulative limit for the same scientific
subject (example: 21600 seconds). Execution requests reserve their declared limit;
terminal runner observations settle actual elapsed time. The installation transaction
adds missing cumulative and `debug.budget_scope` fields from the bundled example
configuration and preserves explicit settings. Diagnostic scope defaults to `task`,
including its recovery lineage; `scientific_subject` explicitly shares historical usage.
Administrators can adjust `debug.total_wall_seconds` / `debug.max_runs` when exhausted
without erasing history; rebuild the diagnostic runtime or restart its owning service
after editing policy so its cached adapter policy is reloaded. For bundled SSH deployment it upgrades runner
code and remote configuration together; both must use the new policy contract.
Existing approval decisions and scientific Run retry limits remain unchanged.

The external adapter replaces the local `tcad-control.service`. SSH operations
are bounded and return immediately; the VM runner owns detached execution and
durable `running`, `status`, and `done` records.
In this mode, `TCAD_STATE_ROOT` is ignored: installation does not create, change
ownership of, or back up the local TCAD state directory.

## 6. Verify

```bash
deploy/install.sh status

systemctl is-active \
  scidiscovery-control.service \
  scidiscovery-approval-ui.service

curl -fsS http://127.0.0.1:8765/ >/dev/null
pytest -q
```

For an external TCAD setup, first verify SSH and the configured runner with a
deployment-smoke tool profile. A service health check does not qualify a
scientific model or a Sentaurus license.

### Instance workbench and archives

Parameter panels offer compact previews and complete pagination for each original,
separating design choices, cited evidence, and implementation bindings without inferring
missing sources or uncertainty. Pages contain at most 8 rows; originals above 4 MiB
retain their download entry. With `curve_figure_evidence` installed, evidence nodes show
saved paper panels, numerical CSV redraws, and point overlays, with original CSV downloads.
Association uses the exact saved invocation and manifest. Missing images, output ports,
or incomplete manifests produce local display gaps without re-extraction or scientific
requalification. Historical prose translation is deferred; no translation service is required.

The archive feature uses the existing approval UI service and port and requires no
VM protocol changes of its own. TCAD policy upgrades still synchronize the VM runner.
Reinstall with the existing deployment configuration and restart the services.
Use the management URL returned by `instance_current` to obtain instance read or
maintenance access; request a new URL after expiry. An approval link still
authorizes only its original request, never instance maintenance.

The installer safely creates `<SCID_WORKSPACE>/.scidiscovery-archive/instances`,
rejects symlinks, and grants the approval UI write access only to that fixed
archive root rather than the whole workspace. Management supports confirmed
previews, archive, read-only browsing, restore, and instance UI-cache cleanup.
There is no permanent-delete action. Restore requires selecting the instance
again; it does not restart work or extend approvals. Shared originals and
historical files with unknown ownership are retained and listed. Remote VM
originals remain remote. This version bounds the target control-record snapshot
to 32 MiB, individual control-payload reads to 16 MiB, and its file inventory to
50,000 entries; an oversized target receives an unsupported preview with its
originals preserved. Incomplete external-reference reads conservatively retain
shared data and report a gap instead of assuming that no reference exists.

An unknown native writer produces a busy/unknown preview and preserves the files.
In particular, a terminal LocalTrusted Run alone cannot prove process quiescence;
this restriction does not affect workbench reading or ordinary scientific work.
Resume or roll back interrupted maintenance through its page; do not remove the
journal to force access. Before reverting to old code, restore with the new
version or retain the archive offline without expecting the old code to resume
that instance. Installation and uninstall do not delete archives.

See [validation scope and outstanding checks](RELEASE.md#current-status).

## 7. Configuration Reference

| Variable | Default | Purpose |
| --- | --- | --- |
| `SCID_WORKSPACE` | `<source-root>/workspace/default` | Existing project workspace; must differ from the source root |
| `SCID_CODEX_LAUNCH_ROOT` | unset | Optional actual Codex launch directory; receives the same-generation project profile when distinct from source and workspace |
| `SCID_PYTHON` | first `python3` on `PATH` | Base interpreter |
| `SCID_SERVICE_USER` | `SUDO_USER` or current user | Service account |
| `SCID_SERVICE_GROUP` | primary group of service user | Socket/file group |
| `SCID_PLATFORM` | `codex` | Codex platform selector; other values are rejected |
| `SCID_WORKER_BACKEND` | `local` | `local` is the trusted native-tool path; `hardened` is the MCP-only file backend, and the value drives daemon, systemd, Codex profiles, and install verification together |
| `SCID_PLUGINS` | empty | Comma-separated local plugin directory names, for example `tcad_artifact,curve_score,curve_figure_evidence` |
| `SCID_INSTALL_ROOT` | `/opt/scidiscovery` | Installed package root |
| `SCID_STATE_ROOT` | `/var/lib/scidiscovery` | Control-plane state |
| `TCAD_STATE_ROOT` | `${SCID_STATE_ROOT}/tcad` | Local-adapter execution state; ignored for external command transport |
| `SCID_CONFIG_ROOT` | `/etc/scidiscovery` | Secrets and policy |
| `SCID_APPROVAL_PORT` | `8765` | Loopback approval UI port |
| `SCID_TCAD_COMMAND_CONFIG` | unset | External command adapter configuration |

When upgrading a local adapter that already uses a separate state directory,
set `TCAD_STATE_ROOT` to that existing path. Installation does not migrate it.

`hardened` currently rejects Operations requiring shell, code, or `view_image`,
so TCAD Deck authoring v1 uses `local`. Effect approval and adapter boundaries
remain independent of that choice.

The remote-runner script documents additional `SCID_SSH_*`, `SCID_VMRUN_*`,
and `SCID_REMOTE_*` variables through `--dry-run` output and its source header.

## 8. Upgrade and Uninstall

Re-run `install`; packages and generated platform configuration are replaced
atomically and the previous configuration is backed up under
`/var/backups/scidiscovery`.

```bash
sudo deploy/install.sh uninstall
```

Uninstall removes services but preserves state, configuration, and backups.
Delete those directories only after an explicit archival decision.

After migrating from the retired `artifact-agent-vnext` or early
`tcad-control` deployments, audit their exact service roots and duplicate
user-local packages with:

```bash
deploy/cleanup_legacy_services.sh --dry-run
```

If the audit shows the current services are active and no current configuration
references those roots, remove them and retain the newest three SciDiscovery
installation backups with:

```bash
sudo deploy/cleanup_legacy_services.sh clean
```

The cleanup script never removes `/opt/scidiscovery`, `/etc/scidiscovery`,
`/var/lib/scidiscovery`, or `/var/lib/scidiscovery-tcad`.

## 9. Troubleshooting

- **A base Python dependency is missing**: install the reported dependency in
  the selected Conda or system Python environment, then rerun the installer.
  Deployment itself is offline and does not resolve third-party packages.
- **MCP socket missing**: inspect
  `journalctl -u scidiscovery-control.service -n 100`.
- **Agent Run timeouts**: inspect `run_status`; create a new Run after failure
  rather than overwriting the old result or current binding.
- **Approval returns 403**: open the exact URL on the same host and verify the
  process is bound to the intended ResearchInstance.
- **VM address changed**: use a resolver or update only the private transport
  configuration; never hard-code a discovered address in source.
- **TCAD job appears stuck**: use short status calls. Never hold SSH open while
  the solver runs.

Controlled analysis evidence recovery requires matching control/Worker packages and the VM helper. With the bundled SSH adapter, the main reinstall transaction updates runner code and configuration together using the selected command configuration. Do not use a code-only upgrade for policy schema changes. Keep original result directories and explicit administrator settings; reload Codex before scheduling a new analysis Run. Independently managed external adapters require their own matching deployment. Never resubmit an old execution merely to refresh collection.

## Agent execution settings

The global `agent-settings.json` section `mcp_read` bounds complete JSON pages for
`run_list` and `scientific_inventory`: `summary_max_bytes` defaults to 4096 and
`detail_max_bytes` to 32768. `limit` is an item ceiling; byte limits may shorten a
page, so continue with the returned `next_before`. Summaries omit per-task dispatch
instructions and model profiles while retaining scientific qualification flags.
If one entry exceeds the budget, its bounded `entry_omitted` marker retains the
exact name and a `read_request` for `run_status` or `artifact_catalog`. Pagination
advances past that marker, so following objects remain reachable. The omitted
body is not a scientific summary, and omitted objects are not absent. These are global control
settings, not instance overrides. The page budget also covers appended attention notices; notices omitted for lack of space remain pending. Before rolling back to an older release that does not recognize `mcp_read`, remove that section from the configuration.

The instance directory has separate Agent Settings and archive-management entries. Agent Settings obtains editing access and opens the dedicated settings page; saving stays on that page.

The installer creates `${SCID_CONFIG_ROOT}/agent-settings.json` (default `/etc/scidiscovery/agent-settings.json`) only when absent, with `zh-CN`, `gpt-6-sol`, and `medium` defaults. An existing file that exactly matches the previous generated default is upgraded transactionally to the new default; customized files are preserved. Control and UI services use the same `SCID_AGENT_SETTINGS_FILE`; global-file edits require restarting both services. Missing files retain compatibility defaults; malformed existing files report their path and fields.
The initial model value comes from the packaged `scidiscovery/default_agent_settings.json`; Agent Operation declarations do not pin a model.

The current M7 configuration matches the previous generated default, so reinstalling upgrades its `defaults.model` to `gpt-6-sol`. If a customized installation keeps an older model, edit that file separately and restart the control and approval UI services. Instance-level model overrides still take precedence; already created Runs retain their frozen execution profiles.

```json
{"schema_version":1,"defaults":{"narrative_language":"zh-CN","model":"gpt-6-sol","reasoning_effort":"medium"},"operations":{}}
```

Instance management exposes sparse overrides for narrative language, model, effort and recovery-chain total Run count. The latter three also support installed Operation IDs. Blank fields inherit; resetting removes instance overrides only. Read-only and archived instances cannot save. Saving affects future preflights only: it does not dispatch, execute, approve or rebind a session. Historical reports are not translated and missing historical Run profiles are not backfilled.

The initial upgrade requires installation, role regeneration and reloading Codex to remove fixed role models. Later instance edits need neither new roles nor a restart. Reuse preflight's `normalized_request` for invoke and dispatch its frozen `execution_profile.profile.model` and `reasoning_effort`. The standalone CLI launcher accepts `--model` and `--reasoning-effort`; dynamic roles require both from the queued Run. Requested configuration is not actual model telemetry. VM runner is unchanged and needs no synchronization. Roll back with matching code, roles, database and configuration transaction backups.

## Experiment material and reading budgets

### Automatic UI reading aids after sealing

Sealed stage conclusions and final Run outputs enqueue optional display-copy jobs
in the existing UI cache. The approval UI service processes them serially, using
`helpers.profiles.lightweight` (packaged default: `gpt-6-luna`, `low`). These calls
do not consume the Worker's native helper allowance or require Worker/Root review.
Original conclusions, values, figures and execution states remain authoritative.
Identical source versions and configuration are cached; interrupted model calls
are not retried automatically after restart.

Configure the top-level `presentation` section of `agent-settings.json`:

```json
{"presentation":{"enabled":true,"command":["/absolute/path/to/codex"],
  "timeout_seconds":60,"interval_seconds":5,"max_source_bytes":1048576,
  "max_input_bytes":16384,"max_output_bytes":32768,"memory_limit_bytes":2147483648}}
```

The adapter uses [Codex non-interactive structured output](https://developers.openai.com/codex/noninteractive/)
in an ephemeral session with bounded scientific excerpts, no parent history,
project instructions, user tool configuration or scientific execution tools.
`deploy/reinstall.sh` resolves Codex before sudo and passes its absolute path as
`SCID_CODEX_EXECUTABLE` to the services. Set that variable to override discovery.
The launcher also resolves an npm entrypoint's adjacent Node executable; systemd
does not need to inherit an interactive nvm PATH. Explicit `presentation.command`
values other than the default `codex` remain authoritative.

The display worker uses `${SCID_WORKSPACE}/.scidiscovery-ui/display-codex` as its persistent
Codex home (`presentation.codex_home` can select a separate absolute directory).
The installer creates the hidden parent with mode `0700` and grants the approval
UI service write access to that directory. The framework Git ignore list excludes it.
It seeds file-based authentication once from the service user's Codex home, with
private permissions, and retains subsequent credential refreshes there. It does
not copy research sessions or user configuration. Legacy home-level managed
policy files remain linked; system-managed policy still applies. If authentication
is available only through a keyring, authenticate the dedicated home separately.
This uses the supported [Codex auth-cache reuse](https://developers.openai.com/codex/auth/)
mechanism. The dedicated home contains credentials and must stay private.

The SQLite index is persistent in that dedicated home. Creating a new SQLite
directory on every call while retaining the research home caused historical
session indexing, excessive memory use and startup timeouts. `memory_limit_bytes`
is a per-process virtual-address-space ceiling, not an RSS measurement; it remains
configurable, for example `13000000000` for a 13 GB ceiling. Existing failed copies
can use the repaired default runtime when explicitly regenerated after deployment.
Missing authentication or failed generation falls back to originals without
blocking submissions or approvals. No VM runner update is needed.

`ui/workbench.sqlite3`, table `display_summaries`, stores instance-scoped exact
ArtifactRef identities and successful-call token usage, input bytes and elapsed
time. Failed-call usage can be unknown and must not be counted as zero. Failures
have instance-scoped engineering diagnostics. Viewing historical content never
starts a model call. Disable globally and restart the services to stop processing.
Pages with instance management access offer **Generate guide** for missing copies
and **Regenerate** for completed or failed copies. These buttons remain visible outside
the collapsible reading guide. Stage child nodes read, refresh and regenerate their
own exact sealed material and version, never the parent experiment report. Regeneration
uses the copy's recorded configuration without changing scientific results or requiring
Worker review. Queued and running copies disable the button; repeated old-form submissions
cannot start another attempt. Archived instances, maintenance and global disable block
these actions.

Experiment progress is also collapsible; background refresh preserves its open state.
The approval page offers **Invalidate this approval request** only for pending requests,
with the existing identity and submission checks. Cancellation records a human decision
and receipt without authorizing execution or overwriting an approval or rejection.

### Scientific material budgets

Complete experiments accept exact PDFs, images, CSV, JSON, archives and grids via
`scientific_materials`; `scientific_files` supports the same types with explicit lazy
file delivery. Both share the stored-material budget. Small text remains directly
readable; large or binary files retain exact references until the Worker requests
`worker_materialize_input(source_name=...)`. Native image viewing is enabled for
experiment Workers. Startup does not embed file contents or ask for control hashes.

Merge this top-level field into `${SCID_CONFIG_ROOT}/agent-settings.json`, retaining
other settings (all sizes are bytes):

```json
{
  "input_materials": {
    "max_item_bytes": 2000000000,
    "max_total_bytes": 2000000000,
    "inline_max_bytes": 1048576,
    "inline_total_bytes": 8388608,
    "transfer_chunk_bytes": 1048576
  }
}
```

The first two limits bound stored individual/aggregate material sizes, the next two
bound automatic in-memory delivery, and the last bounds streaming copy chunks.
Existing instance overrides inherit global values; the settings form currently has
no editing controls for these fields. `scid_describe` reports effective budgets and
new Runs freeze budgets and delivery modes. Admission errors identify the material;
do not drop necessary evidence to bypass a rejection. These budgets do not replace
TCAD execution authorization or solver resource policy. Regenerate roles and reload
Codex on upgrade; finish tasks bound to old contracts before switching services.

## Legacy uncertain submissions

Upgrade and validate the remote runner before enabling the new control plane. The
standard installation transaction follows this order and probes capabilities,
policy, and `tcad_submission_protocol`. Replacing a runner file does not stop old
RPC/submit processes that already loaded its code.

Before enabling retirement for a legacy uncertain request, the administrator must
stop old local submitters, establish that old remote RPC/submit processes and queued
submissions have stopped, and upgrade the runner. A negative lookup is not that proof.
As the service account, using the installed Python environment and exact execution ID:

```bash
PYTHONPATH="${SCID_INSTALL_ROOT:-/opt/scidiscovery}/site" "${SCID_PYTHON:-python3}" \
  -m scidiscovery.artifact_agent.interfaces.submission_recovery \
  --state-root "${SCID_STATE_ROOT:-/var/lib/scidiscovery}" \
  --execution-id EXACT_EXECUTION_ID \
  --confirm-old-submitters-stopped \
  --reason 'All old submitters stopped; runner upgraded; reference operator evidence'
```

The command rejects a live local submit lock and records operator UID, reason, time,
and exact submission digest. It changes no execution terminal state, evidence or
budget. After service recovery, cancel/abandon the same execution normally. An
existing remote job is recovered; only atomic retirement releases an absent job's
reservation. An unavailable remote remains unresolved. This command is not a Worker
or MCP tool; the documented SEC-002 same-UID isolation limitation still applies.

Transient terminal-query failures retain reservations until
`lifecycle.terminal_observation_failure_limit` consecutive failures (default 3,
positive integer) in `agent-settings.json`; a successful query resets the counter.

Task capacity is configured globally in `agent-settings.json` as
`scheduling.max_active_runs` (default 32; includes queued and running tasks).
It is independent of Operation identity and request idempotence. Waiting parent
tasks also occupy a slot: leave room for their independent review tasks. Native
Worker dispatch continues to follow the scheduler's serial execution policy.
Capacity rejection creates no Run
and returns a repairable `run_capacity_busy`; retry the same request after a slot
is released. Existing active tasks are retained when lowering the limit.

TCAD pre-creation capacity rejections are typed and bound to the exact submission.
Upgrade the bundled remote runner together with control to receive this protocol;
older untyped errors remain uncertain. Diagnostic reservations survive a limited
blocked/inconclusive report. Use `run_diagnostic` on the execution surface with
the owning Run name and exact `diagnostic_name` for status/cancel/collect/read after its Worker
exits. Cancel releases allowance only after exact remote retirement, or cancels
the recovered existing job. Legacy reservations without frozen retirement support
retain their allowance and require administrator recovery; repeated absent lookup
never proves an old request cannot arrive. Collection seals a separate diagnostic delivery without changing the final scientific report.

For legacy uncertain diagnostics, the existing administrator recovery command
also accepts `--diagnostic-ledger`, `--diagnostic-name` and `--run-id` instead of
`--execution-id`. Invoke `python -m
scidiscovery.artifact_agent.interfaces.submission_recovery --state-root <state>
--diagnostic-ledger <ledger> --diagnostic-name <name> --run-id <owner>
--confirm-old-submitters-stopped --reason <reason>` only after all old local and
remote submitters have actually stopped. It records UID/reason/exact digest but
releases no allowance. The ordinary `run_diagnostic(action="cancel")` must still
obtain remote retirement proof. The command is not an Agent/MCP tool.

Rolling back to a version that does not support `scheduling` requires removing
that configuration section. An old version also recreates the Operation-wide
unique index: do not roll back while multiple queued/running tasks share an
Operation digest. Finish those tasks first. Upgrading does not alter their frozen
contracts or remove existing Runs.

Task workspace readiness is durable: queued work is dispatched only after preparation and all domain hooks finish. An interrupted preparation can be recovered by repeating the identical operation_invoke request; Worker open uses the same recovery gate. Upgrade marks legacy queued work unready and rebuilds it before dispatch; running work is preserved.

`run_diagnostic(action="collect")` can collect existing late terminal results after the owning task has ended, without starting a solver or changing its sealed report. `action="read"` pages the separate immutable delivery receipt or an exact `file_name`; status contains no file bodies. Collection and publication retries reuse the same original identity.
