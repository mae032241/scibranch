# Install SciBranch

[简体中文](INSTALL.zh-CN.md) | English

The supported host platform is Linux with POSIX filesystem/locking behavior. Native Windows is unsupported and WSL2 is unverified. No Windows adaptation is installed.

The SciBranch installer builds and publishes `scid-research`, `scid-experiment` and selected
plugins offline. Operators must provide dependencies beforehand. Installation does
not contact a model, create a research instance or start services.
`SCID_DEPLOY_MODE` accepts only `thin`; other values fail before filesystem changes.

Install as the current user, without `sudo` or root. Python 3.12 is recommended;
the package supports Python 3.10+. Provide a Python with pip, the runtime dependencies
in [pyproject.toml](../pyproject.toml) (`cryptography>=42,<47`, `jsonschema>=4,<5`,
`pydantic>=2,<3`, `packaging>=23,<27`, and `tomli>=2` on Python 3.10), and the
installer/build tools `setuptools>=68` and `wheel` beforehand.

For example, prepare one reusable user environment once. This preparation may
download dependencies; use your prepared local packages if operating offline:

```sh
python3.12 -m venv "$HOME/.local/share/scidiscovery-python"
"$HOME/.local/share/scidiscovery-python/bin/python" -m pip install \
  'cryptography>=42,<47' 'jsonschema>=4,<5' 'pydantic>=2,<3' \
  'packaging>=23,<27' 'setuptools>=68' wheel
```

Clone the current source from the [SciBranch repository](https://github.com/mae032241/scibranch):

```sh
git clone https://github.com/mae032241/scibranch.git
cd scibranch
```

`main` carries the current product, `dev` carries ongoing development, and
`maintenance/v0.1` retains the older product line. See the
[release policy](RELEASE.md#branches-and-tags) before selecting a historical version.

From the source checkout, install the application offline. The optional Cov
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

The control Python selected by `SCID_PYTHON` and the scientific Python in an
executor's `run_argv[0]` may be different existing environments. Installing a
plugin checks that plugin's declared application dependencies; it does not supply
an arbitrary task's numerical or ML libraries. Declare those libraries with
[`python_requirements`](RESEARCH_CONFIGURATION.md#scientific-python-dependencies--科学-python-依赖)
and run `config-check` before preparing the research instance.

The defaults are `SCID_INSTALL_ROOT=$HOME/.local/share/scidiscovery` (containing
`site/` and `skills/`), `SCID_BIN_ROOT=$HOME/.local/bin`, and
`SCID_BACKUP_ROOT=$HOME/.local/state/scidiscovery/install`. Installation creates
the command directory if needed; `--dry-run` only checks and writes nothing.
The Python package remains `scidiscovery`; its name, the `scid-*` commands and
these installation paths remain stable under the SciBranch project name.
Override these `SCID_*` settings explicitly with canonical absolute paths when
needed, choosing directories writable by the current user.
The core supplies `local_execution:command` without installing Cov. Select
`SCID_PLUGINS=cov_execution` for the independently packaged
`scidiscovery-cov-execution` adapter and its `cov_execution:experiment` entry point.
Its observer, receipt and delivery providers ship in the same package.
`SCID_PLUGINS=tcad_artifact` selects TCAD; it no longer depends on a curve plugin.
The installer checks active runtime and build dependency versions and markers for
the core and selected plugins using `SCID_PYTHON` before publication. Requirements
between packages being installed use their source versions; external requirements
use that interpreter's installed metadata. Active extras and direct URL dependencies
are rejected because this offline check cannot validate their closure or origin.
Package probing also imports the browser/UI entry points. No external dependencies
are fetched.
Bundled runtime Skills from the repository's root `skills/` directory are copied
as ordinary files to `$SCID_INSTALL_ROOT/skills`,
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
instance, budget, credential, platform profile or service. Fresh `install` refuses
existing package roots or command wrappers. To update the existing installation,
stop its research controllers, Workers and UI processes, then use `reinstall`
with the same Python, installation paths and explicit plugin selection:

```sh
SCID_PYTHON="$HOME/.local/share/scidiscovery-python/bin/python" \
  SCID_PLUGINS=cov_execution bash deploy/install.sh reinstall
```

`bash deploy/reinstall.sh` defaults to this same update mode; its explicit
`install` argument still requests a fresh install. Reinstall builds and validates
temporary package and Skill stages before replacing the original `site/`,
`skills/` and matching command wrappers. Publication failures restore the old
directories and wrappers. Success removes temporary old package trees; the
existing installation transaction retains small wrapper evidence. No new permanent
installation prefix is required. Unknown files beside the managed trees, scientific
Python environments and research instances are preserved. An incomplete deployment,
symlink target or command owned by another configuration is refused.
The installer does not discover or stop live writers; stopping them is an operator
step, not a guarantee supplied by the deployment lock.
Scientific configuration, original identities and frozen Skill selections and
source bindings remain instance-owned; Skill assets remain in the installation.
Changing shared implementation code is a separate upgrade and provides no
compatibility guarantee for old protocols.

`bash deploy/install.sh status` checks wrapper ownership. `bash deploy/install.sh uninstall`
removes only the two matching wrappers, retaining code and instance originals.
Publication failure uses the existing transaction; foreign command owners are
preserved and reported. These commands do not stop or modify
leftover host services or delete databases and historical results.

See the [CLI guide](ROOTLESS_CLI.zh-CN.md) for research, UI, approval and recovery.
See the [configuration reference](RESEARCH_CONFIGURATION.md) for model, execution
environment and permission requirements. Successful installation does not qualify
a real model, browser or solver. Prepare model/bootstrap access, Cov training data
and the scientific execution environment separately; installation starts no research.
