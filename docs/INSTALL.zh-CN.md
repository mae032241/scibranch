# 安装教程

简体中文 | [English](INSTALL.md)

## 1. 支持环境

- 带 systemd 的 Linux 或 WSL2
- Python 3.10 及以上，并带有 `pip`、`setuptools>=68`、`packaging`、
  `pydantic>=2,<3` 和 `jsonschema>=4,<5`
- Codex CLI
- `poppler-utils`：受限 PDF 文本提取
- `bubblewrap`：Worker 隔离分析
- `curl`：服务健康检查
- OpenSSH Client：远端 TCAD 执行

Sentaurus、许可证和虚拟机均为外部前置条件，本仓库不提供。

Debian、Ubuntu 或 WSL：

```bash
sudo apt-get update
sudo apt-get install -y \
  python3 python3-pip poppler-utils bubblewrap curl openssh-client
```

安装器使用选定的 base Python 解释器，只把当前仓库的应用包安装到
`/opt/scidiscovery/site`。它不会创建 venv、联网下载依赖，也不会修改选定的
Python 环境。第三方依赖必须在部署前准备好。使用 Conda base 时执行：

```bash
conda install -n base -c conda-forge \
  'pydantic>=2,<3' 'jsonschema>=4,<5' 'setuptools>=68' packaging pip
```

正式安装会显示每个执行阶段，并且先完成本地包构建，再停止已有服务。激活过程是
事务化的：精确备份 release/configuration 目标和 SQLite 数据库；服务或 MCP 健康
检查失败时恢复安装前文件与服务状态。成功事务证据保留在
`/var/backups/scidiscovery/transactions/`。

通用事务化重装入口是：

```bash
SCID_WORKSPACE="$PWD/workspace/<project-name>" \
  deploy/reinstall.sh --dry-run
SCID_WORKSPACE="$PWD/workspace/<project-name>" \
  deploy/reinstall.sh reinstall
```

`reinstall.sh` 默认不安装领域插件；通过 `SCID_PLUGINS` 显式选择
`tcad_artifact,curve_score` 等插件，通过 `SCID_TCAD_COMMAND_CONFIG` 选择外部适配器。
`install` 和 `reinstall` 使用同一个事务化流程；无参数等价于 `install`。

图像曲线提取使用三个通用领域插件。部署前离线预装符合插件声明范围的
Pillow 和 Poppler `pdfimages`。坐标轴、刻度和科学身份由智能体查看原图后提供，
运行链不使用 OCR 或 Tesseract。安装器只记录实际的 Pillow 和 `pdfimages`
版本及可执行文件绝对路径。

```bash
export SCID_PYTHON=/absolute/path/to/service/python
"$SCID_PYTHON" -m pip install --no-index --find-links /srv/scid-offline/wheels 'Pillow>=10.0,<13.0'
export SCID_PLUGINS=tcad_artifact,curve_score,curve_figure_evidence
export SCID_PLATFORM=codex SCID_WORKER_BACKEND=local
export SCID_WORKSPACE=/srv/scid-project
export SCID_CODEX_LAUNCH_ROOT=/srv/scid-codex
export SCID_SERVICE_USER="$(id -un)" SCID_SERVICE_GROUP="$(id -gn)"
deploy/reinstall.sh --dry-run
# 安装与审查门通过后才执行：
deploy/reinstall.sh reinstall
```

workspace 与启动目录须已有且独立于 checkout。通用 wrapper 由服务用户运行。
安装器在创建安装事务前，以真实服务身份和 systemd 有效默认 PATH 执行 Python
import、版本调用及一次真实 PDF 嵌入图恢复；
操作者 shell 的 PATH 不充当服务依赖检查。

核验结果只写入暂存安装包的
`site/curve_figure_evidence/figure_dependencies.json`，切换前和安装后再用服务
Python 复验。激活时将安装前缀设为只读；运行时使用记录中的
`pdfimages` 绝对路径并复核版本。工具版本变化须重新生成安装记录。
未供应该记录的普通 wheel 可以编译，但不能通过 figure 安装预检。

当前可信 Local 后端采用软隔离，不是操作系统沙箱。生产包、运行资源、默认配置和
Worker 显式上下文不得携带案例几何、历史答案或可作为答案发现入口的路径。历史审计文档
可保留定位符，但运行端不得消费它们。宿主读取探针若显示可读，必须如实记录，只能声明这些文件
未向 Worker 提供或未观察到被使用；`SEC-002` 继续保持已知问题。获准切换前检查旧合同
queued/running Run，不承诺跨 digest 恢复。图像曲线 Operation 不绑定外部
`scientific-paper-evidence` 技能。

Local Worker 可以发现已安装 Skill 并按需读取参考资源，Operation 工具权限保持不变。
独立受管的 TCAD Skill 具备部署目录完整性校验和事务回滚；其字节不绑定 Operation
或 Run 身份，因此不承诺逐 Run 参考知识重放。安装后须在全新 compiled Agent 会话中
验证发现与 helper 使用，生成提示测试本身不能证明此行为。

曲线分析更新后，在全新会话核对编译目录：`science.result.diagnose.v1` 直接接收实验结果，曲线 bundle 与评分报告为可选输入；启用 TCAD 时另有 `tcad.result.analyze.v1`，其 Worker 提供 `worker_tcad_curve_score`，接收原始输出与参考材料。这两个入口是分析职责的替代入口，不是串行步骤。重新安装不会刷新已启动会话的 Worker 工具目录；须重启会话。旧记录保留，但变更合同后的资格应以当前预检为准。

## 2. 获取源码

```bash
git clone <仓库地址> scidiscovery-agent
cd scidiscovery-agent

deploy/init_workspace.sh ingaas-paper
export SCID_WORKSPACE="$PWD/workspace/ingaas-paper"
```

Git 仓库只保存框架源码。每个研究项目必须位于被 Git 忽略的
`workspace/<project-name>/` 下；论文、参数、研究账本、仿真工程、日志和结果均放入
该项目目录的对应子目录。

WSL 下不要从 `/mnt/c` 运行 systemd 服务。应把工程放在 WSL 文件系统，例如
`~/src/scidiscovery-agent`。

## 3. 选择 AI 平台

`SCID_PLATFORM` 目前只支持 `codex`。它在框架源码根目录生成
`.codex/config.toml`、角色 TOML，并在根目录 `AGENTS.md` 中写入受管理的通用
调度规范；其下的 workspace 自动继承。

生成文件包含当前设备的绝对路径，因此每台机器都必须重新生成，不能提交到 Git。
workspace 的 `AGENTS.md` 只保留该论文或用户任务的专用约束，安装器会迁移并移除
旧版本重复写入其中的通用 scheduler 块。

## 4. 本地部署冒烟测试

此模式只验证服务和 MCP 接口，不运行科学 Solver。内部 TCAD controller 只允许
`/bin/true`。

```bash
PYTHON="$(command -v python3)"

# 可选源码预检。选定 Python 必须已能导入工程依赖。
SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_PYTHON="$PYTHON" deploy/install.sh --dry-run

sudo SCID_PYTHON="$PYTHON" \
  SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_SERVICE_USER="$USER" \
  SCID_SERVICE_GROUP="$(id -gn)" \
  SCID_PLATFORM=codex \
  SCID_WORKER_BACKEND=local \
  deploy/install.sh install
```

安装后包含：

- `/opt/scidiscovery/site`：只读 Python 应用包；
- `/var/lib/scidiscovery`：控制面状态；
- `/var/lib/scidiscovery/tcad`：仅本地适配器模式使用的执行状态；
- `/etc/scidiscovery`：服务密钥和执行策略；
- `/run/scidiscovery/control.sock`：Root MCP；
- `scidiscovery-control.service`；
- `scidiscovery-approval-ui.service`；
- Codex 为受支持的 Agent Operation 生成按 Operation 隔离的本地 stdio MCP；不安装中央 Worker 服务；
- 仅本地适配器模式启用的 `tcad-control.service`。

审批网页只监听 <http://127.0.0.1:8765>。

安装后需要完全重启 Codex。可以从框架根目录直接启动：

```bash
codex -C "$PWD"
```

也可以进入默认的嵌套 workspace；它会继承框架根目录中的 MCP 和 Subagent：

```bash
codex -C "$SCID_WORKSPACE"
```

若实际从源码根和 workspace 之外的目录启动 Codex，请将该已有绝对目录设置为
`SCID_CODEX_LAUNCH_ROOT`。安装器会在同一事务中为它生成、备份和校验 `.codex`
与 `AGENTS.md`，避免启动目录继续加载旧 MCP 定义。若通过 `SCID_WORKSPACE` 使用源码树之外的 workspace，安装器会在该 workspace
生成等价的项目级运行时配置，因为它无法继承框架根目录；这不会复制研究数据或
控制面状态。

```bash
export SCID_CODEX_LAUNCH_ROOT=/实际启动/codex/的绝对目录
```

不要在每次启动 Codex 时重复执行安装脚本。只有首次部署、切换安装配置或更新已
安装代码时才需要重新安装。

## 5. 远端 TCAD VM

### 5.1 配置 VM Runner

复制示例到不会提交 Git 的本地目录，不要把真实许可证或主机信息写回示例：

```bash
mkdir -p config/local
cp plugins/tcad_artifact/config/remote-runner.example.json \
  config/local/remote-runner.json
chmod 600 config/local/remote-runner.json
```

修改副本：

- 写入真实 `sprocess`/`sdevice` 路径；
- 写入 `STROOT`、`PATH` 和许可证环境变量；
- 为每个允许的 Solver 配置唯一 `profile_id`；
- 设置由 VM 普通用户拥有的 state/exchange 目录。

示例已显式包含一条 `sprocess` 与一条 `sdevice` profile。两条 profile 都必须保留
`solver_kind` 和非空 `release_evidence`，并分别指向管理员确认的真实可执行文件；不要
从可执行文件名或另一个 profile 的环境变量隐式推断这些信息。
仅复制并修改文件权限不算完成配置；安装器会拒绝与 tracked example 逐字节相同的
副本。

通过现有免密 SSH 安装无第三方依赖的 Runner：

```bash
SCID_SSH_DESTINATION='tcad@vm主机或IP' \
SCID_SSH_IDENTITY="$HOME/.ssh/id_ed25519" \
SCID_REMOTE_RUNNER_ROOT='/home/tcad/scidiscovery-tcad' \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install
```

此脚本不会创建 SSH key，也不会在 VM 安装 systemd 服务，只会在 VM 普通用户目录
部署兼容 Python 3.6 的 Runner。

如需通过 VMware Tools 处理 DHCP 地址变化，可不设置固定
`SCID_SSH_DESTINATION`，改为设置：

```bash
export SCID_SSH_DESTINATION_FALLBACK='tcad@192.0.2.10'
export SCID_VMRUN_EXE='/path/to/vmrun'
export SCID_VMX_PATH='/path/to/guest.vmx'
```

### 5.2 配置 WSL/Linux 传输

```bash
sudo install -d -o root -g "$(id -gn)" -m 0750 /etc/scidiscovery

sudo install -o root -g "$(id -gn)" -m 0640 \
  plugins/tcad_artifact/config/ssh-transport.example.json \
  /etc/scidiscovery/tcad-transport.json

sudo install -o root -g "$(id -gn)" -m 0640 \
  plugins/tcad_artifact/config/command-adapter.example.json \
  /etc/scidiscovery/command-adapter.json
```

修改 `/etc/scidiscovery/tcad-transport.json`：

- SSH executable 和可选 identity；
- 固定 destination 或 VMware resolver；
- known-hosts 与稳定的 host-key alias；
- 远端 runner、config 和 exchange 路径。

运行态 transport 已存在后，Runner 代码或私有 profile 配置发生变化时，应复用这份
transport 执行标准完整安装；这也是从旧版单 profile 配置迁移到当前双 profile 配置的
路径：

```bash
SCID_PYTHON="$(command -v python3)" \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install-from-transport --dry-run

SCID_PYTHON="$(command -v python3)" \
SCID_REMOTE_RUNNER_CONFIG="$PWD/config/local/remote-runner.json" \
deploy/install_ssh_tcad_runner.sh install-from-transport
```

`install-from-transport` 默认读取 `/etc/scidiscovery/tcad-transport.json`；可用
`SCID_TCAD_TRANSPORT_CONFIG` 指定另一个绝对路径。它复用 SSH executable、identity、
destination resolver、known-hosts 及精确远端路径，先在临时目录对候选 Runner 与配置
执行 `tcad_capabilities`，成功后才一起替换；提交后自检失败会恢复原文件。
所有会写入 VM 的安装模式都强制要求显式设置 `SCID_REMOTE_RUNNER_CONFIG`，并拒绝把
仓库中的 tracked example 当作私有配置部署。

仅当远端 `runner.json` 已经符合当前 schema、且明确只需更新代码时，才使用
`upgrade-code`。该模式不会上传或迁移配置，并会在旧 schema 上失败关闭：

```bash
SCID_PYTHON="$(command -v python3)" \
deploy/install_ssh_tcad_runner.sh upgrade-code --dry-run

SCID_PYTHON="$(command -v python3)" \
deploy/install_ssh_tcad_runner.sh upgrade-code
```

WSL 调用 Windows OpenSSH 时，常见 executable 是
`/mnt/c/Windows/System32/OpenSSH/ssh.exe`；identity 和 known-hosts 应使用该
可执行文件能够识别的路径格式。

使用外部适配器安装服务：

```bash
PYTHON="$(command -v python3)"
sudo SCID_PYTHON="$PYTHON" \
  SCID_WORKSPACE="$SCID_WORKSPACE" \
  SCID_SERVICE_USER="$USER" \
  SCID_SERVICE_GROUP="$(id -gn)" \
  SCID_PLATFORM=codex \
  SCID_TCAD_COMMAND_CONFIG=/etc/scidiscovery/command-adapter.json \
  deploy/install.sh install
```

外部适配器模式只校验适配器配置；执行策略由外部执行器返回。本机遗留的
`tcad-policy.json` 不参与此模式的校验，也不会被改写或调整权限。本地 socket
模式仍要求管理员提供符合当前 schema 的完整策略。安装成功不代表远端策略或
求解器已经通过真实执行验收。

TCAD 的 `agent_execution_policy` 现在区分两个可配置阈值：
`max_wall_time_seconds` 为单次阈值（示例 3600 秒），
`max_total_wall_time_seconds` 为同一科学主体的累计阈值（示例 21600 秒）。
请求先预留声明上限，结束后按 runner 观测的真实耗时结算。安装事务只从随包示例配置
补充缺失的累计字段以及 `debug.budget_scope`，保留已有显式配置。诊断默认范围 `task`
只累计当前任务及恢复链；`scientific_subject` 可显式启用共享历史额度。达到诊断额度时可由
管理员调整 `debug.total_wall_seconds` / `debug.max_runs`，不会清空历史用量；修改策略后
需要重建诊断 runtime 或重启其所属服务，以重新读取适配器缓存的策略。
使用随包 SSH 部署时，会同时升级 VM runner
代码和远端配置；两端须使用新策略合同。既有审批决定和科学 Run 重试次数不变。

外部适配器会替代本地 `tcad-control.service`。每次 SSH 操作都必须短时返回；后台
执行和持久 `running`、`status`、`done` 记录由 VM Runner 负责。
该模式忽略 `TCAD_STATE_ROOT`：安装器不创建本地 TCAD 状态目录、不修改其权限，也不备份其中的数据库。

## 6. 验证

```bash
deploy/install.sh status

systemctl is-active \
  scidiscovery-control.service \
  scidiscovery-approval-ui.service

curl -fsS http://127.0.0.1:8765/ >/dev/null
pytest -q
```

外部 TCAD 首先应使用部署冒烟 profile 验证 SSH 和 Runner。服务健康不代表
Sentaurus 许可证或任何科学模型已经通过。

### 实例工作台与归档

参数区提供精简预览，以及按原件分页的完整参数入口；设计取值、文献依据和实现绑定分别说明，
不推断缺失的出处或不确定性。每页最多 8 条，超过 4 MiB 的原件保留下载入口。
安装 `curve_figure_evidence` 插件后，证据节点可展示已保存的论文局部图、CSV 数值重绘与
叠点审计图，并下载原 CSV。仅按同次调用与原清单精确关联；缺图、缺输出端口或清单不完整时
显示局部缺口，不重新提取或判定旧资料资格。历史正文翻译暂未实现，无需配置翻译服务。

资料归档功能本身沿用审批 UI 服务及原端口，不需要 VM 协议变更；TCAD 策略更新仍会同步 VM runner。
使用原部署配置执行重新安装并重启服务。已绑定会话可通过 `instance_current` 返回的管理 URL
取得实例浏览/维护入口；URL 过期后重新获取。审批链接仍仅授权原请求，不能代替实例维护凭据。

安装器仅为审批 UI 增加 `<SCID_WORKSPACE>/.scidiscovery-archive/instances` 的写权限，安全创建
该固定目录并拒绝符号链接；不开放整个工作区。管理页支持精确预览后归档、只读浏览、恢复及
清理本实例的 UI 缓存，本版没有永久删除入口。恢复后需重新选择实例，不自动重启任务或延长审批。
共享原件和无法确认归属的历史文件保留并列明；远端 VM 原文件不迁移。首版限制目标实例的
控制记录快照为 32 MiB、单个控制载荷读取为 16 MiB、文件清单为 50,000 项；超过目标维护上限
会在预览报告不支持，保留原资料。无法完整读取的外部引用按共享缺口保守保留，不推定无引用。

如果无法确认本地原生进程已经停止，归档预览显示 busy/unknown 并保留资料。当前
LocalTrusted 历史 Run 不能单凭终态证明无写入者，这个限制不影响工作台读取或原科学流程。
归档/恢复中断应在管理页继续或回退，不能删除维护日志强行解锁。回滚旧代码前须使用新版完成
恢复，或将已归档实例保留为旧版无法续跑的离线资料。安装、卸载不删除归档。

验收范围与未完成的生产现场项见[发布说明](RELEASE.zh-CN.md#current-status)。

## 7. 主要配置变量

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `SCID_WORKSPACE` | `<源码根>/workspace/default` | 已创建的独立项目工作区；不能是源码根 |
| `SCID_CODEX_LAUNCH_ROOT` | 未设置 | 可选的 Codex 实际启动目录；与源码根和 workspace 不同时生成同代项目配置 |
| `SCID_PYTHON` | `PATH` 中第一个 `python3` | base 解释器 |
| `SCID_SERVICE_USER` | `SUDO_USER` 或当前用户 | 服务账户 |
| `SCID_SERVICE_GROUP` | 服务用户主组 | socket 和文件组 |
| `SCID_PLATFORM` | `codex` | Codex 平台选择器；其他值会被拒绝 |
| `SCID_WORKER_BACKEND` | `local` | `local` 为可信本地原生工具路径；`hardened` 为纯 MCP 文件后端，值同时驱动 daemon、systemd、Codex profile 和安装验证 |
| `SCID_PLUGINS` | 空 | 逗号分隔的本地插件目录名；例如 `tcad_artifact,curve_score,curve_figure_evidence` |
| `SCID_INSTALL_ROOT` | `/opt/scidiscovery` | 应用安装目录 |
| `SCID_STATE_ROOT` | `/var/lib/scidiscovery` | 控制面状态 |
| `TCAD_STATE_ROOT` | `${SCID_STATE_ROOT}/tcad` | 本地适配器执行状态；外部 command 模式忽略此项 |
| `SCID_CONFIG_ROOT` | `/etc/scidiscovery` | 密钥和策略 |
| `SCID_APPROVAL_PORT` | `8765` | 本机审批端口 |
| `SCID_TCAD_COMMAND_CONFIG` | 未设置 | 外部 command adapter 配置 |

升级已使用独立状态目录的本地适配器时，显式设置 `TCAD_STATE_ROOT` 沿用原目录；安装器不自动迁移数据。

`hardened` 当前拒绝要求 shell、代码或 `view_image` 的 Operation，因此 TCAD Deck 作者第一版必须使用
`local`；这不会降低 Effect 的独立审批和 adapter 边界。

Runner 安装脚本还支持 `SCID_SSH_*`、`SCID_VMRUN_*` 和 `SCID_REMOTE_*` 变量。

## 8. 升级与卸载

重复执行 `install` 即可升级。应用包和生成的平台配置会被原子替换，旧配置备份到
`/var/backups/scidiscovery`。

```bash
sudo deploy/install.sh uninstall
```

卸载只移除服务，保留状态、配置和备份。只有在完成归档决策后才应手动删除它们。

从旧版 `artifact-agent-vnext` 或早期 `tcad-control` 迁移后，先审计旧服务目录和
用户级重复包：

```bash
deploy/cleanup_legacy_services.sh --dry-run
```

若审计确认当前服务正常，且当前配置没有引用旧目录，再删除旧版本并仅保留最近三份
SciDiscovery 安装备份：

```bash
sudo deploy/cleanup_legacy_services.sh clean
```

清理脚本不会删除 `/opt/scidiscovery`、`/etc/scidiscovery`、
`/var/lib/scidiscovery` 或 `/var/lib/scidiscovery-tcad`。

## 9. 常见问题

- **base Python 缺少依赖**：按照错误提示在选定的 Conda 或系统 Python 环境中
  安装依赖，再重新运行。部署过程本身不会联网解析第三方依赖。
- **MCP socket 不存在**：查看
  `journalctl -u scidiscovery-control.service -n 100`。
- **Agent Run 超时**：先读取 `run_status`；失败后创建新 Run，不覆盖旧结果或 current。
- **审批返回 403**：必须在同一主机打开精确 URL，并确认当前进程已绑定正确实例。
- **VM IP 变化**：只更新私有 transport 配置或使用 resolver，不要把新地址写进源码。
- **TCAD 作业似乎卡住**：使用短状态调用，不得用长连接 SSH 等待 Solver。

分析内受控补收集需要控制端、Worker 和 VM helper 匹配更新。使用随包 SSH 适配器时，主重装事务按选定的 command 配置同步 runner 代码和配置；策略 schema 更新不能只跑代码升级命令。保留原结果目录和显式管理员设置，重新加载 Codex 后再创建新分析 Run。独立维护的外部适配器需自行部署匹配版本。不要为了重新收集而重提原执行。

## Agent 执行配置

`run_list` 和 `scientific_inventory` 的整页 JSON 大小由公共
`agent-settings.json` 中的 `mcp_read` 控制：`summary_max_bytes` 默认 4096，
`detail_max_bytes` 默认 32768。`limit` 是条数上限；字节预算可能让本页更短，
须使用返回的 `next_before` 续读。摘要不含逐任务派发说明或模型配置，科学对象的
资格标记仍保留。单条对象超过预算时，返回 `entry_omitted` 标记、精确名称和
`read_request` 读取入口；`next_before` 可越过该标记，后续对象不会被挡住。
省略标记不代表科学结论，须用 `run_status` 或 `artifact_catalog` 按名读取原内容，
不能把未返回对象当作不存在。这是公共控制面设置，实例覆盖不适用。预算包含追加的 attention 通知，装不下的通知保持待交付。回滚到不认识 `mcp_read` 的旧版前，须从配置中移除该节。

安装器首次创建 `${SCID_CONFIG_ROOT}/agent-settings.json`（默认 `/etc/scidiscovery/agent-settings.json`），默认使用简体中文新报告、`gpt-6-sol`、`medium`。已有文件若与上一版安装器生成的默认内容完全相同，会在安装事务中升级为新默认值；自定义文件保持不变。控制服务与审批/管理页面通过相同的 `SCID_AGENT_SETTINGS_FILE` 读取它；修改公共文件后重启两个服务生效。缺文件沿用兼容默认；文件存在但内容错误时显示具体字段错误，不静默回退。
初始模型值来自随包提供的 `scidiscovery/default_agent_settings.json`；Agent Operation 声明不再固定模型。

当前 M7 配置与上一版生成的默认文件完全一致，重装时会自动把其中的 `defaults.model` 升级为 `gpt-6-sol`。其他安装若自定义文件仍使用旧模型，需要单独修改并重启控制服务与审批 UI 服务。实例级模型覆盖仍优先；已创建 Run 的执行 profile 保持冻结值。

```json
{
  "schema_version": 1,
  "defaults": {"narrative_language": "zh-CN", "model": "gpt-6-sol", "reasoning_effort": "medium"},
  "operations": {}
}
```

实例管理列表提供独立的“Agent 设置”和“资料整理与归档”入口。点击“Agent 设置”可取得编辑权限并覆盖语言、模型、推理强度和恢复链总 Run 数，也可按已安装 Operation ID 覆盖后三项。留空继承，清除全部覆盖恢复公共默认。只读访问不能保存；归档实例只读；保存仅影响新预检，不启动 Agent、仿真或审批，也不改变会话绑定。历史英文报告不翻译，历史 Run 未记录的配置不回填。

首次升级本功能需要正常安装、重新生成角色并重新加载 Codex，移除旧角色文件固定的模型；之后实例修改配置无需新 role 或重启。Worker 按每个 Run 的冻结配置工作，调度方须将预检返回的 `normalized_request` 原样用于 invoke，并使用返回的 `execution_profile.profile.model` / `reasoning_effort` 分发。旧独立 CLI 启动脚本支持 `--model` 与 `--reasoning-effort`，新动态角色要求这两项来自已排队 Run。配置是请求事实，不冒充实际模型遥测。VM runner 不变，本次无需同步 VM。回滚应使用安装事务中匹配的代码、角色、数据库和配置备份。

## 实验材料与读取预算

### 封存后的 UI 自动导读

阶段结论及最终 Run 结果封存后，控制层将展示整理请求写入既有 UI 缓存；审批 UI
服务串行处理。模型使用 `helpers.profiles.lightweight`（包默认 `gpt-6-luna` / `low`），
不占用 Worker 的原生 helper 次数，也不要求 Worker 或 Root 阅读、确认或审批。
原始结论、数值、图件、执行状态均保留；导读单独标注，可失败或缺失。
相同来源版本与配置只入队一次，重启后不自动重试已发起但状态不明的模型调用。

`agent-settings.json` 的 `presentation` 配置默认启用，支持：

```json
{"presentation":{"enabled":true,"command":["/absolute/path/to/codex"],
  "timeout_seconds":60,"interval_seconds":5,"max_source_bytes":1048576,
  "max_input_bytes":16384,"max_output_bytes":32768,"memory_limit_bytes":2147483648}}
```

使用 [Codex 非交互结构化输出](https://developers.openai.com/codex/noninteractive/)，每次为
独立临时会话，只传有界科学文本，不加载用户工具配置、项目说明或父会话，不提供
科学执行工具。`deploy/reinstall.sh` 在 sudo 前定位 Codex，以绝对路径
`SCID_CODEX_EXECUTABLE` 传给服务；可显式设置该变量覆盖自动发现。启动器同时解析
npm 入口旁的 Node，不要求 systemd 继承 nvm PATH。非默认 `codex` 的
`presentation.command` 自定义命令优先。

展示助手默认使用 `${SCID_WORKSPACE}/.scidiscovery-ui/display-codex` 作为持久 Codex 目录，
也可通过 `presentation.codex_home` 指定独立绝对路径。安装器以 `0700` 权限创建隐藏
父目录，并为审批 UI 服务开放该目录的写入权限；框架 Git 忽略规则已排除该目录。
首次仅从服务用户的 Codex
目录复制文件式认证缓存，并以私有权限保存；后续认证刷新保留在专用目录，不覆盖回
交互用户目录。不复制研究会话或用户配置；原 home 的旧式管理策略文件保持链接，
系统管理策略仍生效。若只使用系统 keyring，需单独为专用目录登录。
这采用官方支持的 [Codex 认证缓存复用](https://developers.openai.com/codex/auth/)；
该目录包含认证信息，须保持私有。

SQLite 索引也保存在专用目录。此前每次新建临时 SQLite 目录、却沿用研究会话目录，
会触发历史会话索引，造成额外内存消耗和启动超时。`memory_limit_bytes` 是每进程的
虚拟地址空间上限，不是实际 RSS；仍可配置，例如 `13000000000` 表示 13 GB。
部署修复后，旧失败条目也可通过显式“重新整理”使用修好的默认运行目录。
没有可用启动器或认证时回退原文，
不会影响封存和审批。无需更改 VM runner。

缓存位于 `ui/workbench.sqlite3` 的 `display_summaries` 表，按实例及精确 ArtifactRef
隔离；保存成功调用的 input/cached-input/output token、输入字节及耗时。
失败调用的实际 token 可能不可得，不能按零计费；完整错误进入受实例限制的工程诊断。
历史材料不会因打开页面而触发模型调用。全局禁用后重启服务可停止后台整理。
具有实例管理权限的页面提供“生成导读”（尚未生成）和“重新整理”（已完成或失败）；
按钮位于可折叠的“自动导读”区域外，折叠后仍可使用。阶段子节点按自己的确切封存
材料和版本读取、刷新和整理导读，不复用父实验报告。重新整理沿用该条目的整理配置，
不修改科学结果，不要求 Worker 核对。排队或运行期间按钮禁用，重复提交旧表单不会
多启动一次整理。归档、维护期间及全局禁用时不可操作。

“实验进展”也可折叠，自动刷新保留手动展开状态。审批页的“作废此审批请求”只对
未决策请求开放，仍需页面身份与提交校验；作废记录人工决定和回执，不授权执行，
不能覆盖已有批准或拒绝。

### 科学材料预算

完整实验的 `scientific_materials` 支持原始 PDF、图片、CSV、JSON、归档和网格文件；
`scientific_files` 也接受这些类型，显式采用按需文件交付。它们共用材料存储预算，
不再把 1 MB 当作论文或文件的准入上限。小文本可以直接读取，较大或二进制材料
只保留精确引用，通过 `worker_materialize_input(source_name=...)` 分块取到工作区。
实验 Worker 允许原生图像查看；启动材料不会携带原始文件内容或要求填写哈希等机械字段。

在 `${SCID_CONFIG_ROOT}/agent-settings.json` 中配置以下顶层字段（单位为字节）：

```json
{
  "input_materials": {
    "max_item_bytes": 2000000000,
    "max_total_bytes": 2000000000,
    "inline_max_bytes": 1048576,
    "inline_total_bytes": 8388608,
    "transfer_chunk_bytes": 1048576
  }
}
```

将该字段合并进现有配置，保留模型等其他设置；不要整文件覆盖。前两项限制绑定文件的
单项与合计大小，中间两项限制自动内存交付，最后一项控制流式复制块大小。
实例设置中已有的同名覆盖会继承并覆盖公共值；当前表单不提供这些字段的编辑控件。
`scid_describe` 返回实际生效的预算，新 Run 冻结预算和交付方式。超限诊断指出具体材料；
不得通过丢弃必要原始证据规避失败。该预算不替代 TCAD 执行授权与运行资源策略。
升级需要重新生成角色并重载 Codex；不要在旧合同任务运行期间切换服务版本。

## 历史未知提交的管理员恢复

升级时先更新并验证远端 runner，再启用新控制面；标准安装事务按此顺序执行。
部署探测必须通过能力、策略及 `tcad_submission_protocol` 三项只读检查。
仅替换 runner 文件不能停止已加载旧代码的 RPC/提交进程。

历史请求没有撤销支持标记时，普通取消不会自动释放预算。管理员应先停止本地旧提交者，
确认远端旧 RPC/提交进程及待处理提交已退出，并升级 runner。不要把“查询不到任务”作为此确认。
以控制服务账户，在已安装的 Python 环境中执行（替换精确执行 ID；安装路径按实际配置）：

```bash
PYTHONPATH="${SCID_INSTALL_ROOT:-/opt/scidiscovery}/site" "${SCID_PYTHON:-python3}" \
  -m scidiscovery.artifact_agent.interfaces.submission_recovery \
  --state-root "${SCID_STATE_ROOT:-/var/lib/scidiscovery}" \
  --execution-id EXACT_EXECUTION_ID \
  --confirm-old-submitters-stopped \
  --reason '管理员已确认旧提交进程全部退出，远端已升级；记录对应运维凭据'
```

命令拒绝仍持有本地提交锁的请求，并持久记录管理员 UID、理由、时间与精确提交摘要。
它不修改执行终态、不删除证据、不释放预算。恢复服务后，对原执行调用普通取消/放弃：
远端查到任务时接续该任务；只有原子撤销成功后才能释放预留。远端不可达时仍保留原状态和额度。
此命令不注册为 MCP 或 Worker 工具；同 UID 的技术隔离仍受已披露的 SEC-002 限制。

终态查询短暂失败不会立即按上限扣账。管理员可在 `agent-settings.json` 的 `lifecycle` 中设置
`terminal_observation_failure_limit`（默认 3，正整数）；连续失败达到阈值才估算，成功查询重置计数。

全局任务容量通过 `agent-settings.json` 的 `scheduling.max_active_runs` 配置，
默认 32，统计 queued 和 running 的任务；它与 Operation 类型、请求幂等无关。
等待中的父任务也占名额，因此应为它的独立审查任务保留余量。Native Worker
派发继续遵守调度器的串行策略。容量满时不会创建 Run，返回可修复的
`run_capacity_busy`，释放名额后可重试原请求。调低上限不会删除已有活动任务。

TCAD 在创建前因容量拒绝提交时返回绑定精确提交身份的结构化结果。安装时应
同时升级随包提供的远端 runner；旧 runner 的无类型错误仍保守视为提交未知。
实验可封存带限制说明的 blocked/inconclusive 报告，未决诊断与预算预留继续保留。
Worker 退出后，Root 可在 execution 面调用 `run_diagnostic`，提供所属 Run 名称和
精确 `diagnostic_name`，查询、取消、采集或分页读取。仅在精确远端封禁成功后释放未提交额度；
发现已有作业时取消原作业。旧预留若未冻结支持封禁的信息，继续保留额度并需要
管理员恢复；多次查询不到不能证明旧请求不会迟到。采集单独封存诊断交付，不改写最终科学报告。

旧未知诊断可使用已有管理员恢复命令：将 `--execution-id` 换为
`--diagnostic-ledger`、`--diagnostic-name` 和 `--run-id`。命令为
`python -m scidiscovery.artifact_agent.interfaces.submission_recovery
--state-root <state> --diagnostic-ledger <ledger> --diagnostic-name <name>
--run-id <owner> --confirm-old-submitters-stopped --reason <reason>`。
必须实际停止所有旧本地与远端提交者后才能确认；命令只持久记录 UID、理由和
精确提交摘要，不释放额度。之后仍由 `run_diagnostic(action="cancel")` 获取
远端封禁证明。该命令不暴露为 Agent/MCP 工具。

回滚到不支持 `scheduling` 的旧版前，需要移除该配置节。旧版还会重建按
Operation 全局唯一的索引，若同一 Operation 有多条 queued/running 任务，
不能直接回滚，应先结束这些任务。升级本身不改写冻结合同，也不删除已有 Run。

任务工作区具有持久准备完成门：所有准备与领域钩子完成后，queued 任务才提供派发指令。准备进程中断时，重复完全相同的 operation_invoke 请求即可恢复；Worker 打开也遵守同一门。升级时旧 queued 记录先重建后派发，running 记录不重建。

`run_diagnostic(action="collect")` 可在所属任务结束后采集迟到的原诊断终态，独立封存交付回执及文件，不启动新求解、不改写原科学报告。`action="read"` 通过 offset/max_bytes 分页读取回执或其中精确 file_name；默认 8192 字节、最大 32768 字节。未交付终态保留提醒，采集或发布失败后可按原身份重试。
