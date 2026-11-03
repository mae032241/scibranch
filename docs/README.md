# SciBranch 文档 / Documentation

从明确目标出发，探索假设、运行实验、独立评估证据并继续迭代。
SciBranch supports an iterative research loop with managed execution, inspectable
results and explicit budgets. Start with the README, then follow the task below.

| 任务 / Task | 当前参考 / Current reference |
| --- | --- |
| 了解项目 / Overview | [中文 README](../README.zh-CN.md) · [English README](../README.md) |
| 安装、更新与卸载 / Install, update and remove | [中文](INSTALL.zh-CN.md) · [English](INSTALL.md) |
| 配置目标、输入、后端与预算 / Configure research | [中英参考 / Reference](RESEARCH_CONFIGURATION.md) · [TOML example](../research.example.toml) |
| 运行、查看结果、暂停、分支与恢复 / Run and inspect research | [CLI 指南 / CLI guide](ROOTLESS_CLI.zh-CN.md) |
| 理解控制、权限与结果合同 / Understand the contracts | [中文架构](ARCHITECTURE.zh-CN.md) · [English architecture](ARCHITECTURE.md) · [Security](../SECURITY.md) |
| 选择版本与了解验证边界 / Releases and qualification limits | [中文](RELEASE.zh-CN.md) · [English](RELEASE.md) |
| 软件许可与第三方资料 / Licensing and third-party materials | [MIT License](../LICENSE) · [NOTICE](../NOTICE.md) |
| 接入执行适配器 / Integrate execution adapters | [Plugin API](PLUGIN_RUNTIME_API.md) |
| 使用 TCAD / Use TCAD | [传输合同 / Transport](tcad_transport_contract.md) · [中文插件说明](../plugins/tcad_artifact/README.zh-CN.md) · [English plugin reference](../plugins/tcad_artifact/README.md) |
| 了解 TCAD 资格边界 / TCAD qualification limits | [中文](../plugins/tcad_artifact/README.zh-CN.md#资格边界) · [English](../plugins/tcad_artifact/README.md#qualification-boundary) |
| 提取图证据 / Extract figure evidence | [Runtime Skill](../skills/scientific-paper-evidence/SKILL.md) |
| 分析曲线 / Analyze curves | [Runtime Skill](../skills/curve-analysis/SKILL.md) |

运行时 Skill 由根目录 `skills/` 提供，按角色或命名 Worker 显式选择；发布 Skill 不代表启用、
执行授权或科学资格。Runtime Skills are selected explicitly per role or named Worker;
their presence grants no execution permission or scientific qualification.

源码维护从 [Contributing](../CONTRIBUTING.md) 与[串行测试分组 / serial test lanes](../tests/README.md)
开始。当前功能由源码和这些维护中的参考文档定义。计划、审计、交接、旧规范快照和试验输出
属于本地工作资料，不随源码发布，也不定义现行行为。

Qualification applies only to its exact implementation, original inputs and
environment. Historical passing records do not qualify a current implementation.
