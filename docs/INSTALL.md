# Install the thin research CLI

[简体中文](INSTALL.zh-CN.md) | English

The installer builds and publishes `scid-research`, `scid-experiment` and selected
plugins offline. Operators must provide dependencies beforehand. Installation does
not contact a model, create a research instance or start services.
`SCID_DEPLOY_MODE` accepts only `thin`; other values fail before filesystem changes.

Provide Python 3.10+ and the dependencies in [pyproject.toml](../pyproject.toml),
then install the application offline:

```sh
SCID_PYTHON=/absolute/python deploy/install.sh --dry-run
sudo SCID_PYTHON=/absolute/python deploy/install.sh install
scid-research --help
```

Use the common installed framework and dependency environment across research
projects. `SCID_PYTHON` can select an existing base Python or virtual environment;
fresh scientific cases do not require creating a virtual environment, copying
dependencies or reinstalling the framework. Select the existing runtime explicitly
and keep it unchanged while frozen instances use it. Namespace launchers mount the
declared runtime paths read-only. A fresh instance still has its own explicit root,
exact input originals and new budgets; environment reuse does not inherit another
instance's scientific state or usage. Clean-package installation checks use an
isolated installation to validate distribution contents, independently of this
scientific runtime policy.

The defaults are `/opt/scidiscovery-thin` and `/usr/local/bin`.
Override `SCID_INSTALL_ROOT`, `SCID_BIN_ROOT`, `SCID_BACKUP_ROOT` explicitly as
canonical absolute paths; the command directory must already exist.
The core supplies `local_execution:command` without installing Cov. Select
`SCID_PLUGINS=cov_execution` for the independently packaged
`scidiscovery-cov-execution` adapter and its `cov_execution:experiment` entry point.
Its observer, receipt and delivery providers ship in the same package.
`SCID_PLUGINS=tcad_artifact,curve_score` selects local plugins. Explicitly include
plugin dependencies; the installer does not fetch external dependencies.
The optional figure plugin also requires existing Pillow/pdfimages; dependency
preflight fails before publication begins. Installation does not qualify SSH,
licenses or real solvers. See the [transport contract](tcad_transport_contract.md)
for the independently deployed remote runner.

Installation publishes only `scid-research` and `scid-experiment`. It creates no
instance, budget, credential, platform profile or service. Existing sites or either
command wrapper cause refusal; this is not an in-place upgrade tool.
`deploy/reinstall.sh` is an operator sudo wrapper with the same fresh-prefix rule.
Changed code never silently upgrades or re-signs frozen instances.

`deploy/install.sh status` checks wrapper ownership. `sudo deploy/install.sh uninstall`
removes only the two matching wrappers, retaining code and instance originals.
Publication failure rolls back new wrappers using the existing transaction; foreign
command owners are preserved and reported. These commands do not stop or modify
leftover host services or delete databases and historical results.

See the [CLI guide](ROOTLESS_CLI.zh-CN.md) for research, UI, approval and recovery.
See [native trial preparation](NATIVE_TRIAL_LAUNCH.zh-CN.md) for model, execution
environment and permission requirements. Successful installation does not qualify
a real model, browser or solver.
