# SciBranch 部署

简体中文 | [English](README.md)

`install.sh` 从本地源码与选定插件发布 `scid-research`、`scid-experiment`。
`install` 要求目标不存在；`reinstall` 更新原安装路径。两者都不创建实例、预算或服务。
`SCID_DEPLOY_MODE` 仅接受 `thin`。
默认将 `site/` 和 `skills/` 发布到 `$HOME/.local/share/scidiscovery`，命令发布到
`$HOME/.local/bin`，安装事务保存在 `$HOME/.local/state/scidiscovery/install`。
安装无需 sudo 或 root，按需创建命令目录；`--dry-run` 不写文件。
按需在 shell 配置添加 `export PATH="$HOME/.local/bin:$PATH"`。
依赖、参数、前缀保护、事务回退与卸载见[安装指南](../docs/INSTALL.zh-CN.md)。
`reinstall.sh` 默认无 sudo 执行原目录 `reinstall`。更新前先停止既有控制进程、Worker 和 UI；
新包先暂存并验证，再替换受管目录，发布失败恢复旧目录及匹配的命令。

`install_ssh_tcad_runner.sh` 独立部署领域执行后端，另需明确管理员配置与部署授权；
见[传输合同](../docs/tcad_transport_contract.md)。

运行时 Skill 来自仓库根目录的 `skills/`，安装后的持久来源为 `$SCID_INSTALL_ROOT/skills`，
按实际 CLI 安装前缀自动定位；
不支持 `[skills].root` 或 `SCID_SKILLS_ROOT`。已发布 Skill 须由角色或命名 Worker 显式选择才启用。
