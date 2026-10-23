# SciDiscovery 文档 / Documentation

当前应用以 `scid-research`、`scid-experiment` 为核心，实例 UI 是复用同一逻辑的可选入口。
科学 Worker 负责结论，控制层负责精确身份、冻结输入、预算和授权。
Current references describe the supported commands, reusable contracts and their limits.

| 用途 / Purpose | 当前文档 / Current reference |
| --- | --- |
| 开始使用 / Getting started | [中文 README](../README.zh-CN.md) · [English README](../README.md) |
| 运行、结果、批准与恢复 / CLI, results, approval and recovery | [CLI 指南](ROOTLESS_CLI.zh-CN.md) |
| 统一配置与后端 / Configuration and backends | [Reference / 中英参考](RESEARCH_CONFIGURATION.md) · [TOML example](../research.example.toml) |
| 控制层架构 / Architecture | [中文](ARCHITECTURE.zh-CN.md) · [English](ARCHITECTURE.md) |
| 安装与卸载 / Installation | [中文](INSTALL.zh-CN.md) · [English](INSTALL.md) |
| 发布与验证范围 / Publication and validation scope | [中文](RELEASE.zh-CN.md) · [English](RELEASE.md) |
| 安全与信任 / Security and trust | [Security](../SECURITY.md) |
| 插件与共享合同 / Plugins and shared contracts | [Plugin API](PLUGIN_RUNTIME_API.md) · [角色输出 / Role output](role-result-json-protocol-v1.md) |
| TCAD 执行 / TCAD execution | [传输合同 / Transport](tcad_transport_contract.md) · [中文插件说明](../plugins/tcad_artifact/README.zh-CN.md) · [English plugin reference](../plugins/tcad_artifact/README.md) |
| TCAD 资格边界 / TCAD qualification limits | [中文](TCAD_QUALIFICATION_STATUS.zh-CN.md) · [English](TCAD_QUALIFICATION_STATUS.md) |
| 图证据 / Figure evidence | [Skill](../skills/scientific-paper-evidence/SKILL.md) |
| 曲线评分 / Curve analysis | [Skill](../skills/curve-analysis/SKILL.md) |
| 原生试验准备 / Native trial preparation | [启动与资源指南](NATIVE_TRIAL_LAUNCH.zh-CN.md) |
| 源码检查 / Source checks | [测试分组](../tests/README.md) · [Contributing](../CONTRIBUTING.md) |

计划、审计、阶段验收、交接和旧规范快照是本地维护资料，不定义现行功能，也不随源码发布。
资格结论必须绑定精确实现、原件和环境；历史通过记录不授予当前实现资格。
Plans, audits, handoffs and superseded snapshots remain local working records.
Qualification applies only to its exact implementation, original inputs and environment.
