# 安装薄科研 CLI

简体中文 | [English](INSTALL.md)

安装器支持 `scid-research` 与 `scid-experiment`，离线构建并发布应用与选定插件。
依赖须由操作员预先提供；安装不连接模型、创建科研实例或启动服务。
`SCID_DEPLOY_MODE` 仅接受 `thin`，其他值在修改文件前失败。

提供 Python 3.10+ 及 [pyproject.toml](../pyproject.toml) 声明的依赖，再离线安装应用：

```sh
SCID_PYTHON=/absolute/python deploy/install.sh --dry-run
sudo SCID_PYTHON=/absolute/python deploy/install.sh install
scid-research --help
```

默认代码前缀 `/opt/scidiscovery-thin`、命令目录 `/usr/local/bin`。
`SCID_INSTALL_ROOT`、`SCID_BIN_ROOT`、`SCID_BACKUP_ROOT` 可显式覆盖；路径须为规范绝对路径，命令目录须已存在。
核心提供 `local_execution:command`，不安装 Cov。需要 Cov 时选择
`SCID_PLUGINS=cov_execution`，安装独立发行包 `scidiscovery-cov-execution`；
其 `cov_execution:experiment` 入口、observer、回执与交付提供方随同一插件发布。
`SCID_PLUGINS=tcad_artifact,curve_score` 可选择本地插件；依赖插件须显式列入，不安装外部依赖。
可选图插件还需现有 Pillow/pdfimages 环境；预检失败不进入发布事务。
插件安装不证明 SSH、许可证或真实 solver 资格；远端 runner 的独立部署见[传输合同](tcad_transport_contract.md)。

安装只发布 `scid-research` 和 `scid-experiment`，不创建实例、预算、凭据、平台配置或服务。
既有 site 或任一同名 wrapper 会拒绝覆盖；这不是原地升级工具。
`deploy/reinstall.sh` 是操作员的 sudo 包装，同样只支持新前缀安装，不绕过覆盖检查。
代码变化后旧冻结实例不会静默采用新实现或重新签名。

`deploy/install.sh status` 检查 wrapper 归属；`sudo deploy/install.sh uninstall` 仅删除匹配当前配置的两个 wrapper，
保留包前缀和实例原件。发布失败沿既有事务回滚新 wrapper；意外出现的其他命令 owner 保留并报告。
这些命令不停止或修改用户主机上残存的旧服务，不删除数据库或历史成果。

运行实例、UI、批准与恢复见[CLI 指南](ROOTLESS_CLI.zh-CN.md)。
模型、执行环境与权限的准备见[原生试验指南](NATIVE_TRIAL_LAUNCH.zh-CN.md)；
安装成功不证明真实模型、浏览器或求解器资格。
