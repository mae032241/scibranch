# Install the thin research CLI

[简体中文](INSTALL.zh-CN.md) | English

The supported host platform is Linux with POSIX filesystem/locking behavior. Native Windows is unsupported and WSL2 is unverified. No Windows adaptation is installed.

The installer builds and publishes `scid-research`, `scid-experiment` and selected
plugins offline. Operators must provide dependencies beforehand. Installation does
not contact a model, create a research instance or start services.
`SCID_DEPLOY_MODE` accepts only `thin`; other values fail before filesystem changes.

Install as the current user, without `sudo` or root. Python 3.12 is recommended;
the package supports Python 3.10+. Provide a Python with pip, the runtime dependencies
in [pyproject.toml](../pyproject.toml) (`cryptography>=42,<47`, `jsonschema>=4,<5`,
`pydantic>=2,<3`, and `tomli>=2` on Python 3.10), and the installer/build tools
`setuptools>=68`, `packaging` and `wheel` beforehand.

For example, prepare one reusable user environment once. This preparation may
download dependencies; use your prepared local packages if operating offline:

```sh
python3.12 -m venv "$HOME/.local/share/scidiscovery-python"
"$HOME/.local/share/scidiscovery-python/bin/python" -m pip install \
  'cryptography>=42,<47' 'jsonschema>=4,<5' 'pydantic>=2,<3' \
  'setuptools>=68' packaging wheel
```

Then, from the source checkout, install the application offline. The optional Cov
adapter is selected explicitly in this example:

```sh
SCID_PYTHON="$HOME/.local/share/scidiscovery-python/bin/python" \
  SCID_PLUGINS=cov_execution bash deploy/install.sh --dry-run
SCID_PYTHON="$HOME/.local/share/scidiscovery-python/bin/python" \
  SCID_PLUGINS=cov_execution bash deploy/install.sh install
export PATH="$HOME/.local/bin:$PATH"
scid-research --help
```

Omit `SCID_PLUGINS` for the core alone. Persist the PATH export in your shell
configuration if needed. The installer uses offline `--no-deps` publication and
does not create a virtual environment.

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

The defaults are `SCID_INSTALL_ROOT=$HOME/.local/share/scidiscovery` (containing
`site/` and `skills/`), `SCID_BIN_ROOT=$HOME/.local/bin`, and
`SCID_BACKUP_ROOT=$HOME/.local/state/scidiscovery/install`. Installation creates
the command directory if needed; `--dry-run` only checks and writes nothing.
Override these `SCID_*` settings explicitly with canonical absolute paths when
needed, choosing directories writable by the current user.
The core supplies `local_execution:command` without installing Cov. Select
`SCID_PLUGINS=cov_execution` for the independently packaged
`scidiscovery-cov-execution` adapter and its `cov_execution:experiment` entry point.
Its observer, receipt and delivery providers ship in the same package.
`SCID_PLUGINS=tcad_artifact` selects TCAD; it no longer depends on a curve plugin.
The installer checks every active core dependency version and marker, including
cryptography and Python 3.10 tomli, before publication; it imports the browser/UI
entry points during package probing. It fetches no external dependencies.
All bundled Skills are copied as ordinary files to `$SCID_INSTALL_ROOT/skills`,
independently of pip's temporary build tree. Skill discovery locates this directory
from the actual CLI installation prefix; neither `[skills].root` nor
`SCID_SKILLS_ROOT` is a supported override. Publishing Skills does not enable
them: select sets explicitly per role or named Worker. Skill dependencies are
optional for core: use each bundle's
`requirements.txt` and CLI preflight. Pillow is required for image tools;
Poppler `pdfimages` is required only for PDF image extraction. Installation does not qualify SSH,
licenses or real solvers. See the [transport contract](tcad_transport_contract.md)
for the independently deployed remote runner.

Installation publishes only `scid-research` and `scid-experiment`. It creates no
instance, budget, credential, platform profile or service. Existing sites or either
command wrapper cause refusal; this is not an in-place upgrade tool.
`deploy/reinstall.sh` calls the same fresh-install entry point without sudo and
does not bypass the fresh-prefix rule.
For an implementation upgrade, install to a new `SCID_INSTALL_ROOT` and a separate
`SCID_BIN_ROOT`, validate that deployment, stop or reconcile old writers,
then explicitly switch command paths. Retain the old prefix for rollback.
Scientific configuration, original identities and frozen Skill selections and
source bindings remain instance-owned; Skill assets remain in the installation.
Changing shared implementation code is a separate upgrade and provides no
compatibility guarantee for old protocols.

`bash deploy/install.sh status` checks wrapper ownership. `bash deploy/install.sh uninstall`
removes only the two matching wrappers, retaining code and instance originals.
Publication failure rolls back new wrappers using the existing transaction; foreign
command owners are preserved and reported. These commands do not stop or modify
leftover host services or delete databases and historical results.

See the [CLI guide](ROOTLESS_CLI.zh-CN.md) for research, UI, approval and recovery.
See [native trial preparation](NATIVE_TRIAL_LAUNCH.zh-CN.md) for model, execution
environment and permission requirements. Successful installation does not qualify
a real model, browser or solver. Prepare model/bootstrap access, Cov training data
and the scientific execution environment separately; installation starts no research.
