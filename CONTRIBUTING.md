# Contributing to SciBranch

The project is maintained at [mae032241/scibranch](https://github.com/mae032241/scibranch).
Start with the [README](README.md), [architecture](docs/ARCHITECTURE.md) and
[CLI guide](docs/ROOTLESS_CLI.zh-CN.md) for current behavior.

## Branch responsibilities / 分支职责

- `main`: the current published source line. / 当前公开源码主线。
- `dev`: active development, promoted to `main` after review and scoped checks.
  / 当前开发线，经审查和范围内检查后同步到 main。
- `maintenance/v0.1`: bounded fixes for the original `v0.1` baseline; carry
  applicable fixes forward to the current line. / 原 v0.1 基线的维护修复；适用修复同步到当前主线。
- Published tags retain their original commits. Use a new tag for a patch
  release, and keep unrelated development out of maintenance updates.
  / 已发布标签保持原提交；补丁发布使用新标签，维护更新不夹带无关开发。

1. Keep research control changes separate from domain algorithms and adapters.
   Preserve exact input/implementation identities, consumed budgets and original-call recovery.
2. Do not place control identifiers or lifecycle fields in role prompts or role
   payloads.
3. Add deterministic code for mechanical transformations; use Agents only for
   scientific judgment or simulator authoring/review.
4. Add focused tests for behavior changes. Run them through
   `python scripts/run_tests.py --lane source --output .trial/focused-check tests/path.py`;
   choose installed/process checks as described in [tests/README.md](tests/README.md).
5. Keep tracked examples free of credentials, licenses, live hosts, and
   machine-specific absolute paths.
6. Update both English and Chinese README or installation documents when their
   shared behavior changes.
7. Keep local maintenance tools in ignored `.agents/` or `.agent/` directories.
   Runtime bundles under `skills/` are product assets and ship with their helpers,
   references and fixtures; preserve their original bytes.

Real TCAD output is evidence only for the exact reviewed project and execution
contract. Unit tests and mock adapters do not qualify scientific claims. See the
[TCAD qualification boundary](plugins/tcad_artifact/README.md#qualification-boundary).

Keep current usage and boundary rules in maintained reference documents. Development
plans, audits, handoffs and run evidence remain local ignored records. Functional
fixtures belong under tests or acceptance tools, with their consumers and release
manifest updated together. Source maintenance does not require a research instance.

Project-owned source code uses the [MIT License](LICENSE). Review
[NOTICE.md](NOTICE.md) for the separate rights and unresolved redistribution
permissions of bundled third-party references.
