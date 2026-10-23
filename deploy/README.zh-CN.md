# 应用部署

`install.sh` 从本地源码与选定插件发布 `scid-research`、`scid-experiment`，要求新前缀，
不创建实例、预算或服务。`SCID_DEPLOY_MODE` 仅接受 `thin`。
依赖、参数、前缀保护、事务回退与卸载见[安装指南](../docs/INSTALL.zh-CN.md)。
`reinstall.sh` 是操作员 sudo 包装，同样要求新前缀。

`install_ssh_tcad_runner.sh` 独立部署领域执行后端，另需明确管理员配置与部署授权；
见[传输合同](../docs/tcad_transport_contract.md)。

持久 Skill 来源为 `$SCID_INSTALL_ROOT/skills`，wrapper 为新实例选择该路径。
