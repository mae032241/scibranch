# Application deployment

`install.sh` publishes `scid-research` and `scid-experiment` from local source and
selected plugins. It requires a fresh prefix and creates no instance, budget or
service. `SCID_DEPLOY_MODE` accepts only `thin`.
See [installation](../docs/INSTALL.md) for dependencies, settings, prefix protection,
transaction rollback and removal. `reinstall.sh` is an operator sudo wrapper with
the same fresh-prefix requirement.

`install_ssh_tcad_runner.sh` deploys the independent domain execution backend.
It requires separate administrator configuration and deployment authorization;
see the [transport contract](../docs/tcad_transport_contract.md).

The persistent Skill source is `$SCID_INSTALL_ROOT/skills`; wrappers select it for new instances.
