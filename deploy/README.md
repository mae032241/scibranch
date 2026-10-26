# Application deployment

`install.sh` publishes `scid-research` and `scid-experiment` from local source and
selected plugins. It requires a fresh prefix and creates no instance, budget or
service. `SCID_DEPLOY_MODE` accepts only `thin`.
The defaults are `$HOME/.local/share/scidiscovery` for `site/` and `skills/`,
`$HOME/.local/bin` for commands, and `$HOME/.local/state/scidiscovery/install`
for installation transactions. Installation needs no sudo or root and creates
the command directory if needed; `--dry-run` writes nothing. Add
`export PATH="$HOME/.local/bin:$PATH"` to your shell configuration as needed.
See [installation](../docs/INSTALL.md) for dependencies, settings, prefix protection,
transaction rollback and removal. `reinstall.sh` calls the same fresh-install
entry point without sudo and retains the fresh-prefix requirement.

`install_ssh_tcad_runner.sh` deploys the independent domain execution backend.
It requires separate administrator configuration and deployment authorization;
see the [transport contract](../docs/tcad_transport_contract.md).

The persistent Skill source is `$SCID_INSTALL_ROOT/skills`, located automatically
from the actual CLI installation prefix. `[skills].root` and `SCID_SKILLS_ROOT`
are unsupported. Published Skills require explicit role or named Worker selection.
