# SciBranch

**An open framework for iterative scientific discovery.**

[简体中文](README.zh-CN.md) | English

Original software is open source under the [MIT License](LICENSE).

SciBranch turns a research objective into an iterative loop: explore multiple
hypotheses, implement experiments, assess the evidence independently, and choose
what to investigate next. It connects scientific agents to managed execution,
keeps the original inputs and results traceable, and lets you inspect, pause,
continue or branch a research instance.

Scientific Workers own model choices and conclusions. The control layer manages
exact identities, frozen inputs, finite budgets, execution permissions and recovery
of known calls. It retains candidates and evidence; scientific assessment supplies
the comparison and delivery choice.

This is an engineering prototype for Linux, using POSIX file/lock semantics and
Linux namespaces. Native Windows is unsupported; WSL2 has not been qualified.
Successful commands or solver runs establish only the claims supported by their
exact experiments and validation contracts.

## From hypotheses to evidence

- **Explore competing routes.** Run scientific threads against an explicit
  objective and inputs, with independent assessment guiding the next iteration.
- **Run real experiments.** Use managed local execution or an explicitly selected
  domain adapter, with its own permissions and finite execution limits.
- **Keep work inspectable.** Read sealed results, retained artifacts and limitations
  through the CLI or the optional instance webpage.
- **Continue or branch deliberately.** Pause and resume an instance, recover known
  calls, or create a paused branch from one exact historical logical task with new
  finite budgets.

The supported commands are `scid-research` for research instances and
`scid-experiment` for managed experiments. `scid-research serve-ui` exposes the same
instance controls and results through an optional webpage; detached research can
continue after the webpage service exits.

## Install

Clone the [SciBranch repository](https://github.com/mae032241/scibranch):

```sh
git clone https://github.com/mae032241/scibranch.git
cd scibranch
```

Python 3.12 is recommended; the package supports Python 3.10+. Prepare a Python
with the runtime and build dependencies in the [installation guide](docs/INSTALL.md),
then install offline as the current user:

```sh
SCID_PYTHON=/absolute/python bash deploy/install.sh --dry-run
SCID_PYTHON=/absolute/python bash deploy/install.sh install
export PATH="$HOME/.local/bin:$PATH"
scid-research --help
```

The installer publishes the two commands and bundled runtime Skills. Defaults
remain `$HOME/.local/share/scidiscovery` for code and Skills,
`$HOME/.local/bin` for commands, and `$HOME/.local/state/scidiscovery/install`
for installation transactions. The Python package is still `scidiscovery`;
these technical identifiers are unchanged by the SciBranch project name.
Use `reinstall` to update an existing installation after stopping its controllers,
Workers and UI. See the [installation guide](docs/INSTALL.md) for dependencies,
optional plugins, update and removal.

## Start a research instance

Copy [research.example.toml](research.example.toml) (Codex) or
[research.pi.example.toml](research.pi.example.toml) (Pi's explicit supported profile)
to `research.toml`. Declare an
objective, exact public inputs, backend permissions and finite budgets, and create
the files named by that declaration. Then validate and prepare it:

```sh
scid-research config-check --config research.toml
scid-research prepare --config research.toml
scid-research config-inspect --root /absolute/path/to/research-instance
scid-research start --root /absolute/path/to/research-instance
```

Use the exact instance root created by `prepare`. Configuration changes affect new
instances; existing instances retain their frozen settings. The
[configuration reference](docs/RESEARCH_CONFIGURATION.md) describes backend,
permission, budget and Skill selection. New supported Pi preparations can retain
controlled single-native-session authorship and use the existing exact-subject
formal independent-review workflow: complete author and reviewer evidence,
disjoint participants and a sealed `pass` are required. This is not automatic
scientific approval or execution permission; older frozen unsupported capabilities
and receipts remain unsupported. The [CLI guide](docs/ROOTLESS_CLI.zh-CN.md)
covers results, UI, pause/continue, historical task forks and recovery.

The core includes `local_execution:command`. Select optional Cov execution with
`SCID_PLUGINS=cov_execution`, or the TCAD adapter with
`SCID_PLUGINS=tcad_artifact`. Runtime Skills live in the repository's `skills/`
and are published to the installation's persistent `skills/` directory; select
them explicitly per role or named Worker. Skills grant no execution permission
or scientific qualification. See the [adapter API](docs/PLUGIN_RUNTIME_API.md)
and [configuration reference](docs/RESEARCH_CONFIGURATION.md).

## Documentation and support boundaries

[Documentation index](docs/README.md) · [CLI guide](docs/ROOTLESS_CLI.zh-CN.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Release scope](docs/RELEASE.md) ·
[Security](SECURITY.md) · [Contributing](CONTRIBUTING.md)

The current runtime accepts protocol **33** instances only; earlier formats have
no compatibility reader or migration. Node token targets are advisory, while shared
admission budgets and finite execution limits remain enforced. The
[architecture](docs/ARCHITECTURE.md) owns the detailed completion, permission,
candidate and recovery contracts.

Workers and same-user host processes are trusted; this is a soft boundary.
Effective access depends on the declared backend and permission projection.
Research budgets, review and user statements do not grant execution permission.
Unknown external submissions require reconciliation against their original call
and must not be automatically resubmitted.

Browser behavior, real native continuation, TCAD solvers and power-loss recovery
require evidence for the exact candidate and environment. Source tests do not
establish that qualification; full cross-host disaster recovery is outside current
support. Historical packages remain read-only. Independent archive packages and
whole-instance cold checkpoint transfer commands are retired.

For source maintenance, the controller is in `src/scidiscovery/research_loop/`,
adapters are in `plugins/`, and runtime scientific tools and guidance are in
`skills/`. Follow the scoped, serial [test lanes](tests/README.md). Development
plans, audits, handoffs and trial outputs remain local working records. Consult
[NOTICE](NOTICE.md) for bundled third-party materials' copyright and distribution terms
before redistributing a source release.
