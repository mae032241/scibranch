# 安装薄科研 CLI

简体中文 | [English](INSTALL.md)

支持宿主为 Linux，依赖 POSIX 文件系统和锁语义。原生 Windows 不支持，WSL2 未验证；安装器不提供 Windows 适配。

安装器支持 `scid-research` 与 `scid-experiment`，离线构建并发布应用与选定插件。
依赖须由操作员预先提供；安装不连接模型、创建科研实例或启动服务。
`SCID_DEPLOY_MODE` 仅接受 `thin`，其他值在修改文件前失败。

以当前用户安装，无需 `sudo` 或 root。推荐 Python 3.12，包支持 Python 3.10+。
预先提供带 pip 的 Python、[pyproject.toml](../pyproject.toml) 声明的运行依赖
（`cryptography>=42,<47`、`jsonschema>=4,<5`、`pydantic>=2,<3`，Python 3.10 另需 `tomli>=2`），
以及安装/构建工具 `setuptools>=68`、`packaging` 和 `wheel`。

例如，一次性准备可复用的用户环境。此准备步骤可能下载依赖；离线环境须改用预先准备的本地包：

```sh
python3.12 -m venv "$HOME/.local/share/scidiscovery-python"
"$HOME/.local/share/scidiscovery-python/bin/python" -m pip install \
  'cryptography>=42,<47' 'jsonschema>=4,<5' 'pydantic>=2,<3' \
  'setuptools>=68' packaging wheel
```

再从源码 checkout 离线安装应用。下例显式选择可选 Cov adapter：

```sh
SCID_PYTHON="$HOME/.local/share/scidiscovery-python/bin/python" \
  SCID_PLUGINS=cov_execution bash deploy/install.sh --dry-run
SCID_PYTHON="$HOME/.local/share/scidiscovery-python/bin/python" \
  SCID_PLUGINS=cov_execution bash deploy/install.sh install
export PATH="$HOME/.local/bin:$PATH"
scid-research --help
```

仅需核心时省略 `SCID_PLUGINS`；按需将 PATH export 保存到 shell 配置。
安装器使用离线 `--no-deps` 发布，本身不创建 venv。

不同科研工程复用同一套已安装框架和依赖环境；`SCID_PYTHON` 可选择已有基础 Python 或 venv，
新科学案例无需新建 venv、复制依赖或重装框架。显式选择既有 runtime，并在冻结实例使用期间保持不变；
namespace launcher 只读挂载声明的 runtime 路径。新实例仍有明确根目录、准确原件和新预算，
复用环境不继承其他实例的科学状态或消费。干净包安装检查使用隔离安装验证分发内容，与科学 runtime 复用策略独立。

默认 `SCID_INSTALL_ROOT=$HOME/.local/share/scidiscovery`（包含 `site/` 和 `skills/`），
`SCID_BIN_ROOT=$HOME/.local/bin`，`SCID_BACKUP_ROOT=$HOME/.local/state/scidiscovery/install`。
安装时按需创建命令目录；`--dry-run` 只预检，不写文件。
这些 `SCID_*` 路径可显式覆盖，须为规范绝对路径，并选择当前用户可写的目录。
核心提供 `local_execution:command`，不安装 Cov。需要 Cov 时选择
`SCID_PLUGINS=cov_execution`，安装独立发行包 `scidiscovery-cov-execution`；
其 `cov_execution:experiment` 入口、observer、回执与交付提供方随同一插件发布。
`SCID_PLUGINS=tcad_artifact` 选择 TCAD，不再依赖曲线插件；不安装外部依赖。
安装器逐项核对 core 依赖的版本和 marker（含 cryptography 与 Python 3.10 的 tomli），
发布前实际导入 browser/UI。全部 Skill 作为普通文件持久发布到 `$SCID_INSTALL_ROOT/skills`，
Skill 来源由实际 CLI 安装前缀自动定位，不支持 `[skills].root` 或 `SCID_SKILLS_ROOT` 覆盖；
pip 临时源码不是安装资源。发布 Skill 不会启用它们，须按角色或命名 Worker 显式选择集合。
图像能力按 Skill 的 `requirements.txt` 和 CLI preflight 提供 Pillow；只有 PDF 图像提取需要 Poppler pdfimages。
核心不要求图像依赖。
插件安装不证明 SSH、许可证或真实 solver 资格；远端 runner 的独立部署见[传输合同](tcad_transport_contract.md)。

安装只发布 `scid-research` 和 `scid-experiment`，不创建实例、预算、凭据、平台配置或服务。
既有 site 或任一同名 wrapper 会拒绝覆盖；这不是原地升级工具。
`deploy/reinstall.sh` 无 sudo 调用同一 fresh install 入口，同样只支持新前缀，不绕过覆盖检查。
科学配置与原件身份保持冻结；实现升级需按下述新前缀合同显式切换。

`bash deploy/install.sh status` 检查 wrapper 归属；`bash deploy/install.sh uninstall` 仅删除匹配当前配置的两个 wrapper，
保留包前缀和实例原件。发布失败沿既有事务回滚新 wrapper；意外出现的其他命令 owner 保留并报告。
这些命令不停止或修改用户主机上残存的旧服务，不删除数据库或历史成果。

运行实例、UI、批准与恢复见[CLI 指南](ROOTLESS_CLI.zh-CN.md)。
模型、执行环境与权限的准备见[原生试验指南](NATIVE_TRIAL_LAUNCH.zh-CN.md)；
安装成功不证明真实模型、浏览器或求解器资格。模型/bootstrap 访问、Cov 训练数据和科学执行环境须另行准备；
安装不启动研究。

实现升级需新 `SCID_INSTALL_ROOT` 和独立的 `SCID_BIN_ROOT`，验证后停止或核对旧写者，
再显式切换命令入口。保留旧前缀供回退；reinstall 只是 fresh install 包装，不覆盖原 site/wrapper。
科学配置、原件身份及冻结 Skill 选择与来源绑定属于实例；Skill 资产仍由安装前缀提供，
代码实现升级不承诺旧协议实例兼容。
