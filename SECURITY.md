# SciBranch security

Report public, non-sensitive defects through
[SciBranch issues](https://github.com/mae032241/scibranch/issues). A private
security-reporting channel is not specified in this repository; keep sensitive
details out of public reports.

Do not report simulator license data, private keys, live service secrets, or
confidential scientific inputs in a public issue.

Before publishing a release, verify that it contains none of the following:

- `/etc/scidiscovery` secrets or policies copied from a live machine;
- `/var/lib/scidiscovery` databases or CAS payloads;
- generated `.codex` configuration;
- SSH identities, known-host databases, real host/IP data, or VM paths;
- Sentaurus licenses, proprietary binaries, or customer decks;
- research inputs or results that are not approved for disclosure.

Release-matched Sentaurus manuals bundled with the TCAD skill are identified in
[NOTICE.md](NOTICE.md). Their third-party distribution rights are separate from
the project's license status.

The loopback instance UI is designed for a trusted local user and must not be
exposed to an untrusted network without a separate authentication and transport
security layer.

The thin research CLI assumes cooperative Workers and a trusted host user. The
installer publishes command wrappers and creates no control/UI/TCAD service, SQL
state, Root profile or credential. Legacy Root/MCP/UI entrances are retired.
Instance UI and execution gates bind exact requests, but a Worker with the same
OS permissions may read or modify instance control files and approval secrets.
Independent processes, hidden paths and mode `0600` do not isolate same-UID peers.

Binding, qualification, approval and budget gates constrain normal interface use;
they do not contain a hostile same-UID shell. Stronger isolation requires an independently trusted launcher to bind caller identity and
an OS/sandbox boundary that denies Workers access to control sockets, state and
secrets. Existing native sandbox restrictions may narrow access but are not a
framework guarantee of that boundary.

Execution policies are allow-lists. Review executable paths, arguments,
environment variables, input roots, limits, and output bounds before enabling a
real solver profile. The TCAD adapter enforces domain runner and host restrictions;
core control binds the exact request to authorization, budgets and lifecycle.
Both layers are required for supported execution.
