# Contributing

## Branch responsibilities / 分支职责

- `main`: fixes for existing functionality, with the necessary regression tests
  and documentation updates. / 现有功能的 Bug 修复，以及必要的回归测试和文档更新。
- `dev`: new features and their supporting architecture changes. / 新功能开发及其配套架构调整。
- Land fixes affecting `main` on `main`, then carry them into `dev`. Bugs confined
  to unreleased `dev` features stay on `dev`. / 影响 main 的缺陷先在 main 修复，再同步到 dev；仅涉及 dev 未发布功能的问题在 dev 修复。
- Keep unrelated feature changes out of routine bug-fix synchronization. / 日常修复同步不得将无关新功能带入 main。

1. Keep generic control-plane changes separate from domain plugins.
2. Do not place control identifiers or lifecycle fields in role prompts or role
   payloads.
3. Add deterministic code for mechanical transformations; use Agents only for
   scientific judgment or simulator authoring/review.
4. Add focused tests for behavior changes. Run them through
   `python scripts/run_tests.py --lane source --output /tmp/scid-focused tests/path.py`;
   choose installed/process checks as described in [tests/README.md](tests/README.md).
5. Keep tracked examples free of credentials, licenses, live hosts, and
   machine-specific absolute paths.
6. Update both English and Chinese README or installation documents when their
   shared behavior changes.

Real TCAD output is evidence only for the exact reviewed project and execution
contract. Unit tests and mock adapters do not qualify scientific claims.
