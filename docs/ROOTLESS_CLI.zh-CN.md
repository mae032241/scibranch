# 无 Root 的科研 CLI

`scid-research` 直接启动新的 H/W/A/证据原生线程；控制程序执行科学角色选择的路由。
控制程序拥有原件绑定、预算和执行权限，H/W/A/E 拥有科学判断。CLI 是应用核心，实例 UI 是复用原实例状态与公共逻辑的可选入口；来源声明、正式审查和执行授权按准备配置启用。当前能力与资格边界见[架构](ARCHITECTURE.zh-CN.md)和[TCAD 资格](TCAD_QUALIFICATION_STATUS.zh-CN.md)。

## 安装入口

新部署默认 `SCID_DEPLOY_MODE=thin`：`deploy/install.sh --dry-run` 只预检，
`sudo SCID_PYTHON=/absolute/python deploy/install.sh install` 离线安装应用，
发布 `scid-research` / `scid-experiment`。默认 prefix 为 `/opt/scidiscovery-thin`，
`SCID_BIN_ROOT` 为已存在的 `/usr/local/bin`；可显式换成其它绝对 canonical 目录。
基础 Python 及应用第三方依赖由操作者预装；不创建 venv、不解析联网依赖。

薄安装不要求旧 `SCID_WORKSPACE`/`SCID_CODEX_LAUNCH_ROOT`，不初始化旧 SQL、密钥、
MCP/Root 角色、systemd 服务或默认实例。已有 site 或任一薄 wrapper 会拒绝覆盖，
不停止旧 live 服务，也不更新已冻结实例。`status` 只核本配置 wrapper 归属；
`uninstall` 仅删除匹配本配置的两个 wrapper，保留代码 prefix、实例及旧状态，
不能把卸载命令等同于所有 native/外部作业已停止。升级/旧代码回收须另行确认原写者。

`SCID_DEPLOY_MODE=legacy` 已退役并在修改文件前拒绝。当前不提供旧 SQL reader 或协议迁移。安装不会自动运行以下命令或增加任何预算。

## 创建与运行

推荐通过单一 TOML 声明准备新实例，路径相对该文件解析：

```sh
scid-research config-check --config /absolute/project/research.toml
scid-research config-inspect --config /absolute/project/research.toml
scid-research prepare --config /absolute/project/research.toml
scid-research config-inspect --root /absolute/project/research-instance
scid-research start --root /absolute/project/research-instance
scid-research operations --root /absolute/project/research-instance
```

复制 [research.example.toml](../research.example.toml)，完整字段、凭据分离与 protocol 后端限制见[配置参考](RESEARCH_CONFIGURATION.md)。`config-check` 不建实例或提交模型；inspect 输出可能含宿主私有路径。`--config` 不能混入准备参数，修改 TOML 不改变已有实例。显式参数方式使用相同默认值与校验：

```sh
scid-research prepare \
  --root /absolute/path/new-instance \
  --objective /absolute/path/objective.md \
  --public-root /absolute/path/public-inputs \
  --native-launcher-json '["codex"]' \
  --max-tokens 8000000 --max-seconds 1800 --max-steps 3
scid-research run --root /absolute/path/new-instance
```

可重复传入 `--user-update /path/to/statement.md`，将用户补充和原始目标一起冻结。
运行中可通过下述 guidance 命令追加面向未来任务的用户方向；已冻结输入不改写。Codex 默认 `gpt-6.1-sol`、`high`、`default` 服务档位。
预算示例不保证完成一轮；三节点允许典型 H→W→A，但科学角色也可以选择证据提取或等待。
源码入口为 `PYTHONPATH=src python -m scidiscovery.research_loop.cli`。

可在 prepare 传入 `--role-profiles /absolute/path/role-profiles.json` 固定各角色的模型、推理力度和服务档位，例如：

```json
{"H":{"model":"gpt-6.1-sol"},"W":{"model":"gpt-6.1-sol"},"A":{"model":"gpt-5.6-sol"}}
```

省略的角色和字段继承 `--model`、`--reasoning-effort`、`--service-tier`；上例 E 继承全局默认。
显式配置文件须为 JSON 对象（`{}` 可用，`null` 和数组拒绝）；配置只接受 H/W/A/E 及 `model`、`reasoning_effort`、`service_tier` 非空字符串，模型名没有白名单。
准备时展开并冻结完整四角色配置；job、native intent 和实际 argv 使用同一 profile，原 session 的修复和 fragment 恢复也保持原值。
修改原配置文件不影响已准备实例；不传该选项时保留全局配置行为。未知角色、无效配置或原件绑定不一致在原生提交前拒绝，不自动换模型。
rootless cov launcher 的 operator 配置应声明相同全局值与 `role_profiles`（可为等价的稀疏配置）；它核对冻结实例，再生成匹配的 CODEX_HOME 配置，拒绝不一致的 model/effort/tier 参数。
这些记录证明请求参数一致，不代替服务端实际模型或计费观测。

## 后台推进与用户方向

`start --root ...` 后台启动或连接原实例控制进程，返回持久 `operation_id`；
`start --continue --root ...` 显式清除下一节点暂停再推进；`start --resume-step N --root ...`
后台恢复精确已知故障 job，仍检查原冻结续接门槛，不与 continue 合用。`run`、`continue` 是前台方式。
`operations --root ... --operation-id ID` 查看原操作回执，进程失踪须核对原调用，不自动重发。
`pause` 只阻止下一节点，不立即杀死已接受工作。

```sh
scid-research guidance-submit --root /absolute/instance --target W --scope next \
  --text '优先说明已有证据中尚未区分的机制。'
scid-research guidance-list --root /absolute/instance
scid-research guidance-edit --root /absolute/instance --guidance-id ID \
  --expected-revision 1 --text '先比较已有证据，再决定是否需要新实验。'
scid-research guidance-revoke --root /absolute/instance --guidance-id ID --expected-revision 2
```

`--target` 支持 global/H/W/A/E；`--scope next` 面向下一匹配新任务，research 面向后续匹配任务。
默认 advice 为可不采纳的科学建议；目标变更必须显式 `--kind goal_change --target global --effective-step N`，
自动采用 research 范围并保留原目标记录。待应用项须按当前 revision 修改/撤销；已应用任务输入
不改写，research 项可追加仅未来生效的撤销回执。建议不授结论、独审资格、预算或执行权限。

## 从历史任务创建分支

`fork-preview` 与 `fork-create` 只接受已完成且静止、冻结协议 23 的明确父实例，以及精确逻辑
步骤 N。新分支从 N 首次科学输入在新 session 重跑，N 输出和后缀不继承；不能选择任意 attempt
断点。普通、FINAL、正式审查、工程修复及已有 continuation 的任务边界见
[架构中的分支语义](ARCHITECTURE.zh-CN.md#历史任务分支)。缺真实原件、历史 cut 不足或协议不支持
时明确拒绝；unknown 外部效果须回原调用对账，禁止换身份自动重提。

以下路径、步骤和预算仅为命令示例；操作者须自行选择准确原件与有限新额度，本文不构成科学
运行授权。目标根须为独立 canonical 绝对路径，其父目录已存在，且不能位于父实例内或包含父实例。
可选 `--guidance-file` 读取 UTF-8 准确原文，至多 256000 字节；不提供时省略该选项。

```sh
scid-research fork-preview --root /absolute/project/parent --step 7 \
  --child-root /absolute/project/branch \
  --max-tokens 100000 --max-seconds 3600 --max-steps 12 \
  --guidance-file /absolute/project/fork-guidance.txt
```

核对回包中的 `source`、`cut`、`native`、`budget`、`inherited_costs`、`limitations` 与 `blockers`。
只有 `fork_preview_ready` 才创建；将其 `preview_id` 原样填入 `--expected-preview-id`，自行选定一次
操作 ID，保留整条请求供对账。create 的来源、步骤、目标、新预算和指引内容须与预览一致。

```sh
scid-research fork-create --root /absolute/project/parent --step 7 \
  --child-root /absolute/project/branch \
  --max-tokens 100000 --max-seconds 3600 --max-steps 12 \
  --guidance-file /absolute/project/fork-guidance.txt \
  --expected-preview-id PREVIEW_ID --operation-id fork-branch-001
scid-research status --root /absolute/project/branch
```

创建返回 `fork_created`、新身份及 paused 状态；预览和创建不运行模型或 solver。启动子实例须再
显式清除暂停：

```sh
scid-research start --continue --root /absolute/project/branch
```

新有限预算只供子实例新工作使用，历史科学投入与机会不清零，未知历史成本不填零；旧审查和
执行许可不授权新 subject 或调用。指引按原文作为可不采纳的 advice；正式审查仍用中立输入，
通用指引延后到合法后继。创建通过硬链接共享所需只读原件，保留原身份并支持嵌套分支；
仍可写的旧原件先在父实例私有快照存储中封存一次，后续分支复用。不会修改原文件权限。
各分支目录项持有引用，文件系统维护链接数；删除父实例不影响子实例。父实例快照缓存随其
存储保留，最后一个链接移除后释放数据，不需要手工维护引用计数。
源文件与目标必须在同一文件系统，不支持硬链接时明确报错，不回退全量复制；需要新快照时
父实例私有存储须可写。后续执行任务仍创建正常的节点输入副本。工具、认证、模型 session、
solver 和运行环境仍须满足自身声明与准入条件。
分支不会复制、重建或扩大已声明的外部文件读取范围、运行路径映射或交付目标；继续子实例前，
这些路径及目标仍须在原声明范围内有效可用。

创建响应丢失时，使用完全相同的 `fork-create` 请求和 `operation_id` 核对持久回执，保留原
`expected-preview-id` 与指引原文；不要另换 ID 或目标来猜测成功与否。已发布回执可在父目录消失后
核对。同 ID 改请求会拒绝；预览过期时须重新预览，已尝试创建须先核对原操作。

网页在历史节点提供“从这里分叉”，使用同一 service，错误时保留草稿，创建后链接暂停的新实例；
打开新实例后点击“继续研究”才推进。
同 UI host/browser session 中可“核对原操作结果”；跨 host 重启的通用 Web 恢复尚未建立，必要时
用保存的完整 CLI 请求核对持久回执。分支共享使用当前版本的协作 lease 与只读原件，属于同用户
软边界；不得与升级前仍活动的旧 writer 混用，新 guard 不会追溯锁住旧版本进程。

## 删除明确实例

`instance-delete` 永久移除明确实例的本地数据和证据，不提供撤销或安全擦除。命令必须同时指定
canonical 绝对路径、实例 ID、冻结 settings 文件摘要和本次操作 ID；没有默认实例或强制绕过。请在目标实例目录之外执行。
先用 `status --root /absolute/instance` 核对 `goal_id`，并用
`sha256sum /absolute/instance/settings.json` 核对冻结 settings 摘要。确认这正是要删除的实例，
将准确值填入请求；不要根据目录最新时间选择目标。

```sh
scid-research instance-delete --root /absolute/instance \
  --instance-id EXACT_GOAL_ID --settings-sha256 EXACT_SETTINGS_SHA256 \
  --operation-id delete-instance-001
```

未使用的已准备实例、暂停实例和已完成实例均须先通过静止检查。仍在运行、正在被 fork 读取、
存在未知 native/受管执行或交付效果、清理状态不明的实例会被拒绝；删除不会取消、查询或重提
外部执行，也不会补写完成证据。挂载子树和不安全目标路径会拒绝；静止检查需要读取的控制记录命名空间不接受符号链接，
其他内部符号链接只删除链接本身，不访问外部目标。目标内若包含独立实例，包含仍可识别的损坏实例，
删除和清理重试均会拒绝；应先分别处理这些内层实例。prepare/fork 创建、冷恢复与删除通过目录链锁互斥。

准入后先持久记录退役状态，阻止新写入，再把准确目录移入同一父目录下的待删除位置后清理。
共享原件只解除本实例持有的硬链接；父、子和兄弟分支的其他引用仍有效，最后一个文件引用
和打开的文件描述符释放后才可回收数据。外部凭据、工具及原声明的实例外输入不随之删除。

成功返回 `instance_deleted`。少量删除回执保留在实例父目录的私有操作存储中；已登记冷迁移
owner 的外部 anchor 也保留删除状态，不会通过旧 checkpoint 自动复活。冷迁移已冻结或未完成
的实例须先按原迁移流程处理，删除不会绕过其所有权。

若中断、清理失败或响应丢失，保留并重放完全相同的请求和 `operation_id`，继续核对或清理原
待删除目录。不得换 ID 猜测结果，也不要手工删掉操作回执。原路径后来新建了另一个实例时，
原删除请求不会把它当成目标。共享文件与同用户进程仍属于协作软边界。

## 当前进度与封存结果

```sh
scid-research progress --root /absolute/instance --step 2 --attempt 0 --limit-bytes 65536
```

续读时传上次返回的 `--cursor`；`--limit-bytes` 支持 1024–262144。进度只展示后端明确
公开的消息、工具名及状态、用量等有界类型化内容；草稿/日志与封存科学结果分开，不提供
私有推理、完整参数、原生 session 或 stderr。`stream_state` 可为 ready、partial_line、
missing、truncated、replaced、unavailable；截断或替换后须显式省略 cursor 重新读取。
当前节点、草稿和控制进程结束均不代表科学目标已完成；以 A 的目标覆盖、证据缺口、明确
交付选择和原操作回执判断实际结果。

无需网页即可读取精确封存科学原文与成果：

```sh
scid-research result --root /absolute/instance --step 3 > result.json
scid-research materials --root /absolute/instance --step 3 --after 0 --limit 20
scid-research artifact --root /absolute/instance --step 3 --index 1 --output ./model.bin
```

`result` 向 stdout 输出原科学 JSON 字节，不包元数据；`materials` 默认每页20项，limit 支持1–100，
`artifact` 使用该页精确成员 index，核对原件并拒绝覆盖已有目标。三者与网页复用同一精确读取 API，
不启动模型或提供科学资格。

## 插件选择与实例管理

`prepare --execution-config /path/selection.json` 显式选择已注册执行器；选择文件支持 JSON/TOML，形状为 `{"executor":"plugin:binding","config_file":"./plugin.toml"}`。相对 `config_file` 按选择文件所在目录解析。支持内联配置的插件仍可使用 `{"executor":"plugin:binding","configuration":{...}}`，两种来源不能混用。仅用于宿主配置的 `--experiment-mode local`；这里 local 表示配置归属，不表示只支持本机计算。不传时沿用默认本地执行器。插件文件由插件定义 schema；宿主身份和授权字段不能由插件配置提供。私有传输设置只保存在宿主配置中，不作为科学证据。

科研 TOML 和网页中的“实验执行插件”“插件配置文件”使用相同入口：

```toml
[executor.config]
executor = "tcad_execution:tcad"
config_file = "./tcad.toml"
```

这里相对路径按科研 TOML（网页草稿为其声明基目录）解析。TCAD 必须显式提供独立配置文件，不自动寻找已有地址或配置；例如 `tcad.toml` 可包含 `transport = "command"` 与 `command_config_path = "./command.json"`。TCAD 本地检查运行配置、命令/SSH 配置与可选能力快照；嵌套本地引用按各自文件目录解析。预览不构造 adapter、不联系 SSH 或求解器，也不创建结果目录。缺失文件或无效字段会给出需要修改的配置位置。

新实例冻结有效配置；之后修改或删除顶层插件配置文件不会修改既有实例，也不会在启动/检查既有实例时重新读取它。既有嵌套传输原件和科学原件继续遵循各自的完整性规则。框架、插件及工具源码可正常升级，不再按安装包或工具源码哈希拒绝启动、查询、交付或恢复。任务、输入和成果原件、声明权限、预算及原调用身份保持精确校验；升级不重置消费，也不自动重放未知调用。

实例页面复用同一控制状态：

```sh
scid-research serve-ui --workspace /absolute/path/workspace \
  --approval-secret-file /absolute/path/operator-owned.key --port 8123

scid-research serve-ui --root /absolute/path/instance \
  --approval-secret-file /absolute/path/operator-owned.key \
  --instance-root /absolute/path/another-instance
```

尚未创建研究实例时，用 `--workspace` 指定已有目录即可打开独立研究工作台。
“新建研究实例”直接填写任务正文、实例目录、材料目录、模型和有限总预算，其他参数在分组表单中修改。
也可明确选择 TOML 导入；首页和新建页不会读取或选择工作区的 `research.toml`。配置文件不是必填项。
表单可单独保存或导出配置；检查后展示最终生效配置，再确认创建。仅本次创建使用的修改不会回写导入文件。
创建不调用模型，进入实例后须单独点击“开始研究”。“加载已有实例”按精确目录打开既有研究。
workspace 本身没有科学身份、预算或启动/恢复控件；准备新实例仍须显式给齐预算等必要输入。
`--root` 与 `--workspace` 二选一，既有 `--root` 用法保持不变。

密钥由操作者提供，须为现有普通文件、权限0600且至少32字节。命令监听 loopback 并输出独立只读/管理链接，重启后旧浏览凭据失效。普通本地实例无需启用正式审查或执行审批，也能查看节点状态、请求暂停和继续。暂停只阻止下一节点；已接受工作继续完成。继续复用原实例写锁，推进由 detached 控制进程拥有；关闭网页服务不停止已接受推进。页面提供实例状态、精确封存科学原文和成果入口，不提供任意路径或完整私有会话读取。

`--instance-root` 可重复传入其它明确实例，管理页也可按精确已有根打开；不扫描目录、不按最近时间选择。
实例“设置”集中提供研究配置、方向建议与阅读偏好；控制请求和执行审批单列为记录与操作。创建、加载及切换研究由工作台入口负责。普通研究的预算编辑统一使用研究配置；预算耗尽或已进入收尾时，概览提供明确的“恢复前追加预算”链接，进入独立 `/budget-recovery` 页面，继续沿用原预算修订与恢复条件；预算生效后返回总览开始或继续研究，提交预算本身不启动研究。
实例内 `/configuration` 可编辑当前研究的模型、角色、预算、权限、实验执行、交付及可选策略，并区分已生效与待生效配置。保存后从下一个新节点开始生效；已准备或运行的节点及其原调用继续使用原配置。修改总预算须填写调整说明，累计消费不会重置。实例路径、原始目标、来源记录及网页服务设置不通过此入口修改。
新建研究使用独立 `/new` 入口，支持可选配置导入、保存、导出、检查及新实例准备。独立工作台的 `/configuration` 保留为新建入口。
导入文件目录决定相对路径基准；配置校验及准备复用 CLI API，修改新建声明不改变已有实例。网页复用预算排队、用户方向、操作回执、后台
启动/恢复与有界进度，无需网页也能用对应 CLI 入口。配置显示 requested/effective/enforcement 与
limitations：标准 Codex danger-full-access 不实施离线要求，read-only 即使请求联网仍离线，
protocol/自定义 launcher 权限为 hint_only、实际访问未知；详见[配置参考](RESEARCH_CONFIGURATION.md)。

工作台已加载列表、实例总览、用量、当前阶段和最新结论通过同源 SSE 自动同步；断线后自动重连并读取完整状态，不刷新整页，也不清空正在填写的配置、预算或方向建议。`/progress` 自动跟随当前节点；`/nodes/<step>/progress?attempt=N` 固定查看指定历史节点，公开输出仍用原有有界、不透明游标增量读取。等待结论会显示等待原因；普通 A 的 `wait` 可由管理者明确点击“继续研究”请求重新决策，网页不会自动重试。

工作台和结果页面直接展示已保存的结论、用量和控制记录；浏览与下载已封存结果不重新计算文件哈希，也不重跑封存、资格或历史输入核对。科学执行与提交仍使用其原有准入流程。

实例内有“总览 / 研究过程 / 结果 / 设置”四个入口：

- **总览**：查看当前状态、下一步操作、用量及研究轨迹；运行中的“实时进度”可进入当前阶段的公开工作记录。轨迹中的轮次只用于分组展示已有阶段，不规定固定的控制循环；各阶段链接可查看对应结论或固定的历史进度。
- **研究过程**：按机制查看 H 声明、W 发现与指标、A 处置及理由，并随封存阶段自动同步；展开可查看中间过程与原文。轨迹仅汇集当前窗口内至多50个已封存阶段，历史截断会明确提示。下方仍可按时间倒序查找阶段摘要，阅读发现、局限、完整结论原文和研究文件。
- **结果**：查看最终综合结论、作者推荐与交付记录。A 阶段的候选比较只显示其输入范围内最后 W 提名与 A 选中的候选，去重后按首次观察顺序、按评估口径分组；只有主动点击组内排序按钮才按该口径数值排序，可恢复观察顺序。不同口径不能合并比较，数值排序不代表科学排序。
- **设置**：集中提供研究配置、方向建议、阅读偏好，操作记录与审批另列。方向建议提供可选填写示例，并说明接收角色与生效范围；下一适用阶段指下一项匹配角色的新任务，不会改写正在执行或已准备的任务。记录区区分待交付、已交给研究者及撤回后续，交付不代表科学采纳。创建与加载研究放在工作台；保存当前配置与方向建议一样，只影响后续新节点。普通预算修改使用当前配置表单；预算耗尽或进入收尾后的追加与恢复使用独立恢复页，调整说明随变更记录保存。

`/results?after=N` 每页列20个封存节点，`/results/<step>` 显示精确结果；原科学 JSON 与附件入口保留。候选事实展示使用该节点输入时冻结的候选摘要；本节点 A 推荐为 null 时不回填旧推荐。HTML/SVG 成果仍下载，不内嵌执行。

页面的“阅读与翻译”可在创建研究前打开，并设置目标语言、翻译后端与模型；高级设置提供启动参数、推理力度、服务档位和超时。没有默认翻译模型。设置与翻译缓存保存在工作台目录的 `.scid-ui/`（独立实例入口则为该实例的 `.scid-ui/`），重启后继续使用；工作台内打开的实例共享同一套设置与缓存。这些应用设置独立于冻结的科学配置、角色模型及研究预算，不授予科学执行权限。

页面加载、实时更新与翻译处理中轮询只读取缓存。点击“翻译缺失内容”才会提交当前显示且未缓存或失败的自然语言字段；处理期间或失败时保留原文，可再次点击重试。“显示原文 / 显示译文”切换阅读内容。目标语言可在网页修改；保存后读取对应语言的缓存，缺失译文仍需显式点击翻译，切回旧语言会复用已有缓存。译文按字段原文文本与翻译设置区分，原文变化不会套用旧译文。编号、指标、评估口径、链接、代码、原始 JSON 及下载原件保持原样。译文只帮助阅读，不构成作者结论或评审。

可选的 `serve-ui --translation-config /path/to/reading.toml` 从独立 TOML 的 `[translation]` 表初始化上述应用设置，例如：

```toml
[translation]
enabled = true
backend = "codex"
model = "填写本机可用的模型名称"
target_language = "zh-CN"
timeout_seconds = 120
```

`backend` 可选 `codex` 或 `protocol`；`launcher_argv` 可提供本机启动命令数组，`reasoning_effort` 与 `service_tier` 可选。超时至多 300 秒。认证沿用本机后端已有登录，不在网页填写 API 凭据。只读浏览会话可读取已有译文；修改设置和开始翻译需要管理会话及浏览器绑定的 CSRF 校验。

准备实例时可选 `--source-restrictions` 冻结来源声明、`--enable-formal-review` 启用精确对象独立审查。正式审查不等于执行授权。另启 `--enable-execution-authority` 与 `--execution-resource-policy` 才接资源准入：资源策略显式声明 `{"version":1,"allowance":{"wall_time_seconds":N,"max_storage_bytes":N}}`（N为正整数），执行器必须支持冻结的授权合同；不支持不能静默绕过。人工决定在同一实例页面作出，浏览权限本身不授执行额度。

查看授权只核对不可变声明、身份、精确对象、范围及已声明的期限或撤销状态，不遍历执行结果。真实执行入口另行检查科学资格、原件完整性，以及当前预算、暂停和活动/未知执行的准入条件。结果损坏或缺失不抹去已接受意图和消费，也不阻止查看或撤销授权；新效果仍须通过这些检查，未能核实的终态或计量事实保持未知。已 accepted 调用的 `collect/cancel` 沿用原权限和身份，不取得新的提交授权，也不重发原调用，仍受原调用或显式有限维护窗口约束。

资源账复用原 control ledger，换候选/重试不重置消耗。`budget-amend --execution-wall-seconds N --execution-storage-bytes N --authorization-note ...` 仅修订有限资源上限，不代替精确调用的执行决定；不得和科学token/时间修订参数混用。未知调用不退款、不重发，已接受调用的查询/取消不因W结束而被当成新提交拒绝。

TCAD 支持可选编译、精确封存、正式审查与后继执行的非空输入绑定。工程接缝存在不证明真实 solver 已取得资格；所需证据见[TCAD 资格](TCAD_QUALIFICATION_STATUS.zh-CN.md)。旧实例不会静默升级或复制预算，本机 HTTP 与浏览器检查不能替代远端资格。

### 原调用恢复与只读归档

`execution-resume --root ... --step N --call-id ... --max-seconds N --authorization-note ...` 显式推进一个原 W 调用；操作窗口不增加原科学或 solver 额度。只有原封存已记录完整的未准备证据，才能首次准备；可能提交过的调用只查询/核验，不能重放。仍有效的精确决定复用；过期时可显式给 `--request-valid-seconds N` 创建有限的新请求，再由原审批入口决定，不能借续接绕过批准。旧封存缺证据、清理不完整或余额不足不自动补造/续额。

`archive-export --root ... --output ...` 仅在可证明静止时导出到新的只读目录包；不会取消活动作业或创建可推进副本。`archive-inspect --package ... --after N --limit N` 检查包并分页展示清单，也可用 `--section dependencies` 查看范围缺口；每页仍验证全包，不应当作廉价轮询。原件流式保留，`native-homes` 与审批密钥/私有定位文件按范围排除，缺外部依赖明确标注。这两个入口不提供可写恢复资格。

`archive-restore-material --root ... --package ... --step N --index N` 只把一个缺失的普通封存成果恢复到原实例的原位置；index 取该节点科学资料页的封存成员序号。须保留当前 settings、job/科学原文/result/seal、ledger、审批与调用证据，且所有原 owner 静止；包须包含同一当前 producer seal 与精确路径/hash/大小的成果。仅支持普通 artifacts 和 nested experiment 成果，已有相同字节幂等，不同文件或链接拒绝；不修 call/control/managed 证据，不新建实例、不覆盖旧消费、不自动继续或续权，也不替代后续预算/资格准入。该命令本身不提供完整灾备或可推进恢复；跨 boot 补回字节也不解除原 clock 或 unknown 限制。

`execution-resume ... --maintenance query|collect|cancel` 为已经 accepted 的原调用提供有限的宿主维护窗口，须给 `--max-seconds` 和 `--authorization-note`。即使原科学时钟跨 boot 无法继续，也可查询、取消或收集同一调用；此模式不能准备、提交、新建授权请求或增加额度，不能与 `--request-valid-seconds` 合用。精确终态由既有资源账本结算；暂不能结算时不生成退款或新额度。

### 显式同宿主冷 checkpoint

仅用于原实例完全静止、active 时间预算已暂停的情况：

```bash
scid-research checkpoint-enroll --root /absolute/instance --anchor-directory /absolute/owner-anchors
scid-research checkpoint-export --root /absolute/instance --output /absolute/checkpoint
# 保管外部 owner anchor；明确移动旧根到备份位置后，才恢复到原绝对路径。
scid-research checkpoint-restore --root /absolute/instance --package /absolute/checkpoint
```

anchor 目录须已存在、独立于实例。export 冻结旧写者；restore 不覆盖/删除旧根，不复制密钥或 native sessions，不自动继续。恢复保留原 settings、账本和资源消耗，新 generation 成为唯一写者并保持暂停；跨 boot 已证实的暂停间隔标为未测量，不伪造墙钟耗时。成功恢复后的重复命令只确认原事务，不再次暂停已经继续运行的实例。恢复中断保持 fenced，可重试同一事务。

普通只读包不能追认恢复权限；跨宿主、外部 anchor 丢失、缺少必要实现依赖、未知执行或未清理的 native 进程不能自动变成可推进恢复。这里提供同宿主冷恢复，不承诺任意灾备。支持边界见[当前架构](ARCHITECTURE.zh-CN.md)；本地过程测试不证明真实掉电或远端 solver。

## 受管本地实验

标准单项 `codex` launcher 新建实例默认冻结 `--experiment-mode local`。W 启动前由控制层准备本地执行配置，既有原生宿主在该 attempt 生命周期内服务普通文件提交槽，自动处理请求并写回响应，无需另起提交服务。
默认受管本地命令要求 Linux、`bwrap` 和所选 Codex 安装中可解析的原生二进制，不依赖当前 Codex 会话注入 PATH 的临时 helper alias。原生 Worker 与科学子进程共同遵守同一受支持的 legacy `workspace-write` 声明，包括网络允许或禁止及临时目录排除，并按各自工作目录、`TMPDIR` 和命名空间等义解释。原生 Worker 保留 `--sandbox workspace-write` 及捕获的 legacy 配置覆盖；只有后端 sandbox 使用表达同一声明的内部权限 profile，内部机械投影不增加用户配置项。
named/default permissions、选定配置 profile、managed policy、额外可写根等不能忠实投影的权限配置，使受管命令明确返回 unsupported；不会为运行而降权或放宽原生权限。
自定义 launcher 省略模式时按兼容规则冻结 `external`，执行配置和提交服务由 launcher 提供；任务简报会说明普通 shell 命令没有自动记录。自定义 launcher 可显式选择 `local`，但其后端权限无法证明时受管启动明确不支持；`capabilities`、`describe` 等只读合同入口仍可用。已有 cov launcher 使用 `external`，不会被默认 local 配置覆盖。
该 cov launcher 只读共享当前节点的 `jobs/<step>.json` 与 `settings-identity.json`，供受管命令核对原任务和未注册冷 owner 的状态；这些元数据可能包含宿主来源路径，不构成科学证据或写权限。其它节点 job、`settings.json`、控制账本及外部 owner anchor 不挂载。此 launcher 不支持已注册冷 checkpoint owner 的实例：宿主先验证原 owner，并在进入原生命名空间前明确拒绝已注册、缺失、损坏或 fenced 的 owner 状态；不把命名空间内缺文件当作未注册。普通宿主 CLI 的冷 owner lease 行为不变。
`scripts/acceptance/rootless_cov_launcher.py` 在其 operator JSON 中默认选择 `cov_engineering_check_reserve_seconds=15`，可显式设为 `0` 关闭预留。它经原 `cov_lineage.configure` 冻结到 Cov 执行配置；配置只影响新调用，不改变已接受调用。原单调用65秒不增加：支持 `public_runner_main` 的新调用请求实验至多50秒、检查至多10秒、反馈留2秒，原任务剩余窗口还可进一步裁剪；`exp run` 前的短提示仅说明请求上界，实际是否 `reserved` 及原绝对截止仍看回包的 `engineering_check.time_allocation`。仅接受0或大于2且小于65的有限秒数；`backend_file` 无该检查能力时明确不承诺切分。默认通用 Local 不受此 launcher 配置影响。
模式在 settings/job 中冻结，恢复沿用原选择；已有实例缺少此字段时保持原配置路径。
原生 shell 和权限照常可用，临时分析无需逐项受管。

W 可在 `experiments/<name>/` 编写脚本及输入，通过已有执行 CLI 正式运行：

```sh
scid-experiment --config work/experiment-config.json run \
  --experiment-dir experiments/check-one --script experiments/check-one/run.py -- --seed 7
```

控制层只快照明确选择的目录，排除该目录原有的 `artifacts/`，在接受执行前验证文件原件并原子保留。
完整输入快照不设框架固定文件数、单件或累计字节上限；仍拒绝符号链接和路径逃逸，不扫描整个工作区寻找依赖。
默认原生宿主将已接受快照只读映回所选目录的原科学路径，以该路径为工作目录运行；`artifacts/` 单独映到受管输出目录，脚本将输出写在其中。仅 `--script` 时，封存脚本只读映回原科学源码路径，保留原科学工作目录。
默认原生宿主还把原任务冻结的 `public_root` 目录以同一绝对路径只读映入科学子进程；只映该目录，不映公共父目录或相邻私有文件。与实例控制、原生 home、运行时或科学快照等命名空间挂载重叠的公共根在启动前明确返回 unsupported。公共目录仍是 live 读取，不属于所选实验输入快照，不证明全部动态读取已冻结；需要精确输入原件时应显式放入所选实验目录。
科学子进程只获得运行时文件、科学工作区、上述公共根和安全权限投影，`CODEX_HOME=/task-native` 不包含认证或 provider 配置；原 job、控制账本和提交槽不在该文件视图中。这是受管子进程的视图合同，不改变对原生 Worker 及同用户进程的信任边界。`experiments/<name>/work` 用于原生编写时的 scratch；目录受管执行时其快照只读，运行输出写 `artifacts/`。
命令参数按 argv 原样传入；原目录不接收目录调用的运行输出。控制记录命令、参数、退出码、耗时和日志，保留输入及输出快照。
命令默认最多65秒且不超过实例原秒数上限，完整执行日志不按紧凑视图容量截断；任务和共享预算仍适用。
未在所选目录中的导入、环境及外部文件没有被快照，不据此承诺完整环境复现。
脚本应使用相对路径读取受管输入；argv 不推断文件含义或改写绝对路径，原生访问本身不构成读取快照的证明。
仅 `--script` 的原单文件入口仍可用；需要输入、成果自动快照时使用 `--experiment-dir`。

上述目录和 argv 能力属于通用 Local executor。Cov launcher 的 `exp run` 仅接受单个 `--script`，封存为 `source.py`；不支持 `--experiment-dir` 或 `--` 后的脚本参数，也不递归封存兄弟模块。可通过 `capabilities` 和 `describe --key cov_single_source --output <新文件>` 读取所选接口的完整合同。不支持的命令选项在准备、准入和提交前返回 `known_not_started`、`accepted: false` 和 `caller_error`；修正命令后仍须遵守原预算与调用身份。此合同随新实例和新执行身份冻结，不能热改旧冻结实例。

科学代码可生成可选的 `artifacts/result.json`：

```json
{"version":1,"metrics":[{"name":"loss","value":0.2,"unit":null,"evaluation_key":"public-v1","direction":"min"}],"outputs":[{"path":"artifacts/comparison.csv","label":"比较数据"}]}
```

`metrics` 的可用条目要求不同的非空名称和明确有限数值；`unit`、`evaluation_key`、`direction` 及 `label` 是可选科学说明，可省略或为 null，也可保留其他可序列化 JSON 说明。控制不强制 `min/max`、科学文本长度或优化含义。
`outputs` 声明本次已保留的 `artifacts/` 相对路径，科学标签可缺失；完整指标和成果清单不设框架固定项数上限。科学代码只写结果，不填哈希、原件ID或审计结论。
控制保留原 JSON 和成果，自动采集指标，在后继角色的 `lineage.indexes.experiments` 提供分页入口，并按现有成果机制交接文件。
结果可另声明可选候选列表，例如 `"candidates":[{"artifact":"artifacts/comparison.csv"},{"artifact":"artifacts/model.bin","label":"保留模型","metric":"loss"}]`；每项可附 `label` 和精确指标名 `metric`，且须在 `outputs` 中明确声明各自成果。一次调用保留的可选模型由 W 全部显式列出，空列表或省略列表只记录测量，不产生候选。
各项 `artifact` 不得重复，且必须唯一对应已声明并保留的成果；控制由原 scope、call 和已声明 artifact 路径生成稳定候选身份，重排列表不改变身份。A 通过 `W002-C001/artifacts/model.bin` 一类原调用与成员引用看到完整列表，可显式选择任意成员及其精确原件交付；控制不排序、不默认选首项，也不从其他 outputs 推断模型。指标缺失、拼写无法关联或值不可用时，候选仍保留，指标及其比较说明为 unknown/null，不猜取首项、方向或零分。
未声明的旧单值 `candidate` 字段不登记候选；当前 version 1 合同仅使用 `candidates` 列表，不迁移旧格式。多个指标而未声明候选时只记录测量；只有冻结接口明确声明本地结果协议时才解析候选字段，其他 adapter 的私有结果保持原件。
坏指标或成果声明只诊断相关条目；坏 artifact 只阻该候选登记，不清空其他有效指标或成果。无法写入严格 JSON 的说明（例如嵌套数字指数溢出）只在投影中省略相关字段并链接原 `result.json`，原始字节仍保留。摘要超长时给预览和原件引用。
指标名不赋予目标含义，同口径比较不代替 A 的科学判断；A 的推荐可为 null，null 不授权交付。

启用 H search 时，`prepare --candidate-extra-slots N` 明确冻结 W 分类提名的额外名额，默认 2。
W 在科学返回的 `candidate_nominations` 填写 `representatives`、`extras`、`gaps`：
代表项为 `{mechanism_ref,candidate,reason}`，额外项为 `{candidate,reason}`，缺口项为
`{mechanism_ref,reason}`。`candidate` 使用历史科学引用，或当前原调用的 `{call_id,artifact}`。
每个未 waived 的机制（含 deferred、screened）须有一个代表或明确缺口；同模型代表多个
机制须逐项说明科学含义。额外备选不得与代表或其他额外项重复，至多 N 项。
五个机制各有不同代表且额外名额为 2 时，可提名七个模型；框架不固定总数为五，
不按分数排序或自动截断。无成功适用模型应写缺口，仍可交回 partial 或最终结果。
暂时相对低分不足以取消机制，只有 A 明确 waived 并给科学否定或不可行理由才免覆盖。
默认视图及 `lineage.indexes.nominations` 展示最近封存 W 的分类提名；长理由链接原结论。
完整候选池仍在 `lineage.indexes.candidates` 通过 `next_page` 读取，所有已登记原件继续可选。
A 可直接选择预览外的已有模型，并按原 `selection_explanation` 说明，不用重跑实验、
新增登记步骤或覆写 W 原结论。无 search 承诺的任务不强迫填写机制。

执行状态与结果状态分别显示：退出码0只表示命令成功；结果文件缺失为 `missing`，JSON或协议基础形状无法解析为 `invalid`，采集失败为 `unavailable`。个别条目或说明不可用时，其余声明仍为 `available` 并带局部 `diagnostics`。
原件、cleanup或外部结果未知时保留 `unknown`，不得重发以代替查明原调用。
完整执行、结果、观察和封存记录不受紧凑视图的字节或累计项数上限约束；公开摘要和分页视图仍有界，明确标记省略并保留原件入口。视图超限不会使封存失败或丢失候选、调用与成果的权威关联。
使用 `lineage-status` 查看已有调用，或用其原 call ID 执行 `query`、`lookup`、`collect`；失败和缺失不会变成科学合格。
无外部交付 adapter 时可使用 `scid-research closeout --root /path/to/instance --export-only` 保留成果及限制，状态为 `exported_unsubmitted`。
本地成果封存及交接不产生 cov 审计资格或外部提交确认。
显式配置通用目录交付 adapter 后，A 的 `deliver` 可授权控制层解析精确输入和成果、封装并确认目标目录；该确认只证明本地目录交付。
适配器可用可选 `effect_scope` 声明公开效果范围（非空公开文本，不设固定长度上限）；内置目录 descriptor 明确只复制并确认已留存文件，不训练、不执行任务原生提交或科学审计。custom 未声明时为 `unknown`，不根据 argv 猜测。新 job 冻结公开 `configured_delivery`，同一任务续跑保留原声明；该描述不授执行权限或科学资格。W 在临时文件消失前将任务实际需要的成果与原生回执保存到既有声明输出，而非保留所有中间物；A 对照原任务交付要求、留存成果和实际范围明确缺口。FINAL 仍可交付最佳已有部分成果，不新增完成度硬门槛，也不让目录适配器补做缺失的训练、原生提交或审计。

## 文件与职责

H 提出竞争机制和最小判别依据，决定是否请求 E。W 在用户约束与 H 范围内拥有实验设计和搜索责任，根据结果新建、改进、修复或回退候选。A 判断全局贡献、关键证据缺口和下一方向；它可要求关键反例，但不默认指定完整算法、参数或扰动矩阵。机制改变交回 H。

机制承诺与 A 处置列表不设固定四项或 128 个机制上限，科学说明不设固定文本长度。配置的初始调用机会、额外调用授权、已接受消费及有限任务/共享预算仍有效；未知原调用先核对，不因机制更多而重发。

H 应先比较可信的竞争机制，再说明集中深挖的理由；单机制范围的合理性由科学作者解释，不设机制数量下限。参数扫描次数不能代替不同机制的判别证据。A 的 `screened` 只表示已评价精确 W 证据，仍可深挖；`deferred` 暂缓并保留未决路线，`waived` 才明确排除。每个排除须在既有 reason/scientific_result 中给出针对原目标与约束、由证据支持的科学否定或不可行理由；暂时相对落后、缺输入或缺证据应说明暂缓，控制不按关键词、分数或运行次数判理由质量。

后继角色可从 `lineage.indexes.mechanisms` 读取所有机制；已采纳的 A 处置提供理由预览、截断标记及完整科学 JSON 原件入口，长原文不受导航页大小上限省略。同状态重述或遗漏不替换已采纳依据，H 修订会清掉当前旧依据，历史原件保留。所有 search 实例默认向 H/A 持续展示完整组合，包括 screened；局部 improve/repair/validate 不抹去全局视野。机制页展示实际 accepted 调用、初始/额外剩余机会、独立完整 W→A 投入及未服务周期；它们不代表科学覆盖或每机制资源成本。按稳定身份展示，不按指标或欠服务周期排序；说明理由与 H 修订不清零投入。A 在既有 scientific_result / route_reason 中说明比较、集中、暂缓和排除。`--search-policy` JSON 可用正整数 `initial_calls` 配置每机制初始受管调用机会；`--portfolio-review-after-cycles N` 只冻结可选提醒间隔，覆盖所有未 waived 路线，不要求 portfolio_review 表单、不强迫换路线、不阻科学封存或 FINAL。当前协议 23 冻结这些提示与谱系合同；旧协议实例保持原件，拒绝按新协议继续运行。

广度预算应依据普通探索可用量及 `work/budget-status.json`；当前 protected FINAL 模式按 provider 已报告消费计算 token 余额，未知账单继续列为未知，不估算扣费，也不扣旧收口预留。旧预算内收口实例仍用原冻结预留。真实执行未知仍须先按原调用核对。初始调用机会不预留实际资源，首次昂贵调用仍可能耗尽余额；使用已有执行配置 `max_seconds` 和 W 阶段时间/token 目标约束，控制不自动分配广度比例或强制科学机制数。
H 只重写需要变更的 commitment；遗漏保留原承诺及已采纳 A 处置。修订同一 `existing_ref`
后仍可 repair 旧 W 的精确观测：稳定机制 ref 与当前新 H/A 授权分开绑定，旧 W/call/accepted/
terminal 仍以原 H seal 校验。来源须为精确直接 W，或通过已有 `implementation_selection` 选定的
原 accepted 输入；多个来源须精确选择，不能按 label、相同输出字节或最近对象推断。内容去重
归档的共用物理路径也不能证明逻辑输入身份，此类输出不能用于 repair 来源消歧。已知进程成功
也可能需科学修复；unknown、来源模糊、原件缺失/改变及 repair 次数不足仍拒绝。结构性来源问题
在作者回执提前诊断，Worker 仅见科学 ref、原 W/call 与当前授权步骤，不见控制 hash 或私有路径。
旧筛选资格不继承，原投入不退款。H scope 未变时，A 可在 review 后通过 `continue_w` 加显式
局部 focus 请求下一有限 W stage，无需为了续配而重述 H；各 stage 原消费与总额度继续累计。

任务只把实际启用且被配置消费的辅助字段放进原生回答合同；额外科学字段保留原文。启用的可选说明形状无法读取时局部诊断并保留 unknown，不猜 progress 或机制范围。W 的显式非空 findings 若形状损坏则以 partial 交 A，不产生 screened 证明；正常缺失、合法但漏机制、空 finding 或重复绑定仍执行机制覆盖约束。
真实 action、FINAL 路由、资格与权限继续校验。输入选择的 null 保留、`[]` 清空、有效列表替换；坏列表保持 unresolved，不部分应用或静默继承。已登记候选和其精确封存实现可在原 call/output/material 绑定一致时互用可读引用；相同字节而不同来源不合并，任意实现不自动登记为候选。

新实例将 A→W/H 的路由原话标为带来源的决策背景；具体执行方法是建议，用户原文和 H 的真实约束仍有效。控制层不判定科学语义，不删改原始路由；旧已冻结任务保持原投影。证据提取、控制补正和收口指令保留各自含义。新任务把原生角色指引冻结，启动与恢复使用同一份文字。

```text
project/
  global/objective.md
  global/updates/001.md
  nodes/001/
    science-task.json             # 控制层生成的短导航
    inputs/global/               # 本节点绑定的全局文件，只读
    inputs/parents/<node>/
      conclusion.json            # 直接前驱的科学结论
      artifacts/                 # 直接前驱的成果
    work/                        # 本节点临时材料，不交接
    artifacts/                   # 必要交付成果
    experiments/<name>/
      work/                      # 子实验临时文件，不交接
      artifacts/                 # 子实验必要成果
      conclusion.md              # 可选的简短科学观察
    conclusion.json              # 控制层保存的最终科学回答
native-evidence/<node>/           # 私有原生轨迹、schema、哈希与封装
```

角色使用原生文件工具。模型只做科学工作、选择必要成果，最终直接回答简短科学 JSON。
`final_response` 给出字段名；无需读 schema、另存最终回答、制作索引、计算哈希或打包。
子实验不是额外 Agent，结论可以是 Markdown 或 JSON；无需逐次填写机械表单。

控制层自动采集节点 `artifacts/` 及子实验成果和可选结论，规范为直接前驱成果目录。
子实验模型不需要再复制到顶层。`work/`、输入和祖先材料不进入成果采集。
默认交接包括全局文件、直接前驱结论、关键成果直达入口和成果目录，启动材料不逐项展开文件。
角色在科学结论里自然使用 `[比较结果](artifacts/comparison.csv)` 这类相对 Markdown 链接；
控制层验证它指向本节点已采集的成果，并解析为后继节点的实际路径。无需另填附件清单。
普通文字路径不自动推断；外部链接与前驱材料引用保持科学原文，不冒充本节点新成果。
哈希、封装、路径校验、来源记录、输出 schema 和封存回执只由控制层维护。
旧科学结论通过 `history` 给出的只读分页入口在当前节点按需读取，默认只投递不超过 4096 字节的历史简报。
旧实现保留在原拥有节点。角色需要给后继复用时，在科学结论明确链接
`[旧实现](history/nodes/002/artifacts/candidate.py#reuse-implementation)`，或标记当前已绑定的输入路径。
控制层核对原件后仅转交该文件；普通历史引用不转交文件，历史结论里旧附件链接也不自动挂载。
角色不计算身份、不复制旧材料，不继承祖先整包。旧实现虚拟入口并不表示当前节点已能读取代码。

新实例的成果采集默认不设单文件或单节点累计存储大小限制（冻结值为 null）。操作者可显式传入正整数 `--max-output-item-bytes`、`--max-output-total-bytes` 限制采集和直接交接；已有实例冻结的整数配额仍生效，不自动扩大。
显式配额与模型紧凑视图限制分别执行，也不改变 token、时间或执行次数预算。声明目录之外的文件不自动获得证据资格。
若模型正常退出且科学输出已经固定，而控制层封存失败，修复交付后 `run` 可只重试封存。
不修改原科学结论，不重复执行模型；结果未知、模型失败和用量缺失仍拒绝自动重放。

## 路由与暂停

H 选择 `experiment / analyze / extract / wait`；W 固定交 A；
A 选择 `continue_w / revise_h / extract / deliver / wait / finish`，冻结启用候选审计能力时还可选 `audit`；E 返回请求它的 H 或 A。禁用时 `audit_capability.available=false`，audit 请求返回明确不可用回执且不执行外部调用；已接受原件仍按原身份计步并检查未知状态。
`next_action` 是枚举，理由单独写 `route_reason`。`deliver` 明确授权适配器交付所选候选；`finish` 结束科学探索，收口时表示不授权交付。两者都不宣称全局目标已完成。`extract` 仅向 E 请求科学证据提取；精确原件定位、复制、封装、提交由控制层和交付适配器完成。

```sh
scid-research status --root /absolute/path/new-instance
scid-research pause --root /absolute/path/new-instance
scid-research continue --root /absolute/path/new-instance
```

同实例只有一个运行写者；status 不调用模型。pause 允许当前角色完成并封存，在下一节点前停止。
N1a 已增加本地持久 runner：前台控制器退出后，已接受的原生节点继续运行并记录日志、用量和终态。
再次 `run` 按任务哈希、系统启动标识及进程启动身份核对同一 runner，并等待原完成通知，再封存和路由。
`status` 区分 `prepared / running / native_not_submitted / native_outcome_unsealed / seal_present_unverified / unknown`；
封存存在仍需 `run` 验证。原生成功但封存失败只重试封存；执行身份丢失、失败或缺量仍不自动重放。
实际模型提交前再查预算；已确证未提交时记录 `native_not_submitted` 和零用量。
控制层保留该节点及原意图；预算修订或重试不自动删除这些记录。独立工程审查以精确原件和正向源码路径证明确未提交模型时，新安装的兼容 runtime 可显式执行 `native-startup-successor --root INSTANCE --step N --reconciliation REPORT --reconciliation-sha256 SHA256 --native-home ORIGINAL_HOME --authorization-statement STATEMENT`。该入口冻结工程报告及其局限、原调用归属、原权限与最终 A claim，只追加唯一的新上下文 attempt 1；旧失败、unknown 原件、已消费时间和 dispatch 保留。它仅允许同一逻辑最终 A 的首次实际模型提交，不授权新的科学循环或新额度；重复命令只收集同一 attempt，后继未知时不再提交。空日志、空 thread 或操作者文字本身均不构成未提交证明，既有外部未知效果仍阻断恢复。框架及工具升级不要求保留旧安装；启用正式审查时仍核对原审查合同的科学语义，不迁移审查 verdict 或产生新资格。原有交付资格及提交内必要机械审计保持有效。
已封存最终 A 后，若独立工程原件以正向调用顺序证明确切原交付 intent 尚未首次调用，可在当前 host runtime 使用 `native-startup-collect --root INSTANCE --step N --reconciliation REPORT --reconciliation-sha256 SHA256 --authorization-statement STATEMENT`。它追加精确工程核对原件和原操作绑定，只执行已有科学选择的 host 交付与同操作确认，不进入 native 或科学路由。首次实际调用沿用原声明的有限调用时长；原全局消费、失败、unknown 和身份均保留。收集 intent 一旦持久化，重复命令只核对同操作，绝不再次 submit。授权引用校验、当前准入和结果/科学资格校验分开；旧交付/审查合同原件保持不变。
暂停只阻止新节点，恢复时也允许原已接受节点完成和封存。
科学角色的 `wait` 仍表示停止等待。W 已接受且同一 external execution 明确 running 时，控制在科学封存前保留原 checkpoint 并进入 `waiting`；默认 `run` 自动监控原 call，终态后 reconcile 并在合格的原会话中续接 W 消费原件，再封存。等待不调用模型、不增加 native dispatch 或 `advance_seconds`，但真实执行的 `waiting_seconds` 仍消耗原授权总运行时限；旧 elapsed 口径继续按总经过时间计量。暂停阻止唤醒的新 native turn，允许核对原终态；控制进程同 boot 重启后从原 waiting 恢复，未知提交不重放。续接仍受原预算/有限次数与冻结 launcher/session 能力约束；能力不足保留 checkpoint 并报告 unavailable。

旧 W 已 sealed unknown 的事实保持不变。`scid-research execution-recover --root /absolute/path/instance --step N` 可对同 call 追加已验证终态；后继新 job 冻结其恢复事实前缀与材料，已准备 job 仍只读原输入。该动作不把旧科学结论改为 screened，也不重计机会、grant 或原 slice。

新建实例的 `--max-seconds` 是推进启用时长上限：控制推进与原生执行重叠只计一次。
暂停请求生效前已接受的工作和控制器断连后的原执行继续计时；无活动且进入暂停时停止消耗推进额度。
身份或终态无法核实时保守计时，后续核实从当前时刻停表，不回填旧终态时间返还消费。
`status.budget` 分别报告 `advance_seconds / waiting_seconds / paused_seconds / elapsed_seconds`，并显示有效上限、
`consumed_seconds / seconds_remaining` 和计时口径。`status` 只读，不追加计时事件；启动时钟倒退时拒绝推进和修订。
新计时事实绑定系统启动标识与 `CLOCK_BOOTTIME`，同次启动的开放区间按该单调时钟计量；
新 active 实例的 UTC 仅是展示时间，调速或回拨不停止计量，也不返还已按启动时钟消费的额度；账本按序号及同启动的单调时间排序。
CLI `status` 对已记录暂停终点的 active 实例只汇总已闭合消费，不采样当前时钟；尚未闭合的
暂停尾段及完整经过时间报告为未知，不影响已确定的推进与等待消费。开放运行区间和旧 elapsed
实例仍按原计时口径查询。已闭合消费可读，不替代新执行和实际预算写入对当前启动锚的校验。
跨系统重启或启动时钟倒退导致无法核对开放区间时，实时预算读取、
推进和实际预算修订仍拒绝，须先核对并恢复原时钟，不自动退款。配置页读取账本总上限、
排队保存下一节点配置不计算实时消费，不因该时钟问题被阻断；保存不等于恢复或执行许可。
工作台对无法核对的时间消费及余额显示“无法核实”，不以跨启动的 UTC 差或零值补齐，
也不据此判断时间预算耗尽；tokens、预算上限和已保存结果仍可查看。已记录为暂停的
active 实例只展示可核对的已记录推进与等待消费，不为消费计算采样当前时钟。
旧实例没有 `time_budget_basis` 标记时继续按首次运行起算的总经过时间执行原墙钟上限，暂停仍计时。
已有 BOOTTIME 账本的旧 elapsed 实例保留原 UTC 回拨与相对时钟检查，不把新 active 口径覆盖到旧实例。
旧计时账本之前的推进/暂停分布记为 `unclassified_seconds`，不推测历史。新实例未另设总经过时间截止。
`continue` 不增加上限，也不清零已消费时间或 token。

外部操作者可明确修订 token、秒数或累计控制步数上限；科学目标、模型、启动器和已有节点保持冻结：

```sh
scid-research budget-amend --root /absolute/path/instance \
  --max-tokens 8000000 --max-seconds 2400 \
  --authorization-note '操作者明确批准本次上限修订及其原因'
```

修订须持有实例写锁，并确认没有活动或未知执行。仅变更声明上限，不重置消费、不清除原意图、不自动启动模型。
时间与修订共用 `control-ledger/` 追加账本，记录序号、前记录哈希、冻结 settings 哈希、前后上限、时间和授权说明。
累计控制步数包括已准备节点、原生续接、审计和交付动作。修订步数须额外提供 `--max-steps N --authorization-statement '用户授权原话'`；账本记录前后上限及差额，`status.budget.original_max_steps / max_steps` 分别展示原始和有效上限，不修改原 settings 或重置已用步数。普通 `continue` 仍不续额。 合法降低有效上限不隐藏此前的原件：历史节点、累计用量、未知执行及恢复核对仍按冻结 settings 与完整修订链中曾授权的最高步数验证。这个历史范围不授予新额度；普通新的 native turn、续接和审计仍须满足当前有效上限及余额，FINAL 的最后 A 与已有封存 W 的原授权交付不因共享余额不足被挡。

token 记账只累计 provider 已报告的真实消费：`usage.observed_tokens` 与 `accounted_tokens`
使用同一累计值，`unknown_steps` 保留计量覆盖未知，不估算补费、不以未知账单单独阻止推进。
历史计提 ledger 仍验证并只读留存，不作为当前消费，也不新增计提。真实原生进程、清理或外部效果
未知仍须按原身份核对，不能以计量覆盖未知与否代替执行安全检查。

`worker-closeout --root ... --step N --max-tokens N --max-seconds N --authorization-note ...` 在当前可核对的账本余额内显式授权一次有限的新上下文 W 收口，不是通用的 `worker-resume`。仅接受尚未封存、原 attempt 0 因 `token_limit` 停止且已确认清理的 W；已有续接目录、科学后继、`pause.request` 或已开始收口时拒绝。保留的受管调用必须有可验证的 `known_success / known_failed` 原始终态，当前余额仍须通过本次显式授权的原预算门槛；未报告终态用量继续标记 unknown，不估算补费。

如果上述唯一作者收口 attempt 1 因有限时间触发 `author_closeout_wall_limit` 停止且已确认清理，不能重复普通命令来取得新 turn；普通 `worker-closeout` 仍只收集已接受的 attempt，冻结的 `resume_policy=None` 不变。只有操作者另行明确授权后，才可追加一次 attempt 1→2 作者后继：

```bash
scid-research worker-closeout --root /精确原实例 --step N \
  --max-tokens N --max-seconds N --successor \
  --authorization-note '这一次作者后继的范围与预算' \
  --authorization-statement '操作者授权原话'
```

框架或工具升级不要求预先保留旧源码或填写实现 manifest。各 attempt 的已报告消费分别累计；缺失计量保持 unknown，不估算收费。后继入口追加 `host-recovery/author-closeout/NNN-successor.json`，绑定旧 permit 及失败 attempt 1 的任务、调用、用量和进程原件；不改旧 permit、settings、失败 outcome 或科学原件。追加授权记录本身不是执行：只有当前已报告累计 token、时间及控制步数满足本次作者授权门槛，才接受唯一 attempt 2；protected FINAL 的共享交付预留为零，旧预算内模式仍保留原预留。接受后重复调用只收集它，不自动重试，也不授权 attempt 3；新 attempt 2 若真实执行或清理仍 unknown，仍须按原身份核对并阻断后继封存和交付；单纯计量覆盖未知只记录 unknown，不估算收费或单独阻断。

后继额度来自原有累计预算，不重置消费、不自动续额。每个失败 attempt 与新 turn 的已报告消费均计入原有累计账；操作者独立保留的评价额度及当前交付预留不得挪作新 turn。时间上限及已消费时间不因接续增加。新 W 先读当前 attempt 的紧凑机械事实索引，按需取完整科学原件；索引不替作者排名、判定方法多样性或推导审计平台期，科学结论仍由真实 W 编写。原实验、模型及旧 native attempt 只读；当前 attempt、必要 artifacts 与新上下文权限按原隔离启动器保留。这只授权作者完成既有工作，不授权新训练，受管入口仍拒绝 run、compile、submit、query、collect、cancel；同 uid 原生代码仍是信任的软边界，并非任意计算的强隔离保证。

以下 `worker-output-accept` 只说明历史 token-stop 原件的接纳记录。协议 23 的作者 token 目标统一为 soft，越线后原返回正常完成，不会生成 `author_closeout_token_limit`；旧协议原件只读，不能据此继续运行。有限时间停止不能按 token 超额接纳为完成。

```sh
scid-research worker-output-accept --root /精确原实例 --step N --attempt 2 \
  --pending-sha256 精确最终pendingSHA256 --session /精确原session.jsonl \
  --authorization-note '仅接纳这份已完整返回的原文' \
  --authorization-statement '操作者授权原话'
```

仅接受原返回码为 0、进程已清理、最终消息先于唯一完成事件且完成后没有新的 turn/item 活动、该消息与 pending 字节一致、终态用量和精确 session 的实时计数完全匹配，并且没有同时越过原作者 wall 上限的 author attempt 2。未知、部分返回、其他 stop、活动写者、错误任务/原件或不合法的 W 结果不能由此变成成功。当前累计账、时间、控制步数和交付预留仍须足够；不增加额度、不抹去历史 unknown、不接受新 unknown，不授权 attempt 3 或任何新训练。

此入口先追加绑定精确 successor 和操作者授权的 `NNN-output-upgrade.json`；该记录不要求保留旧框架源码，本身不接纳输出，也不授权封存。当前准入通过后才追加 `NNN-output-acceptance.json`，绑定精确 job、intent、outcome、完整用量/原生流/session、process、pending 和授权原件。pending 精确复制为 `science-output.json`，原 pending、outcome、局部上限及 stop marker 全部保留。接纳记录记录已发生的局部超额及限制，封存引用该记录提供的 raw SHA，仍执行原有输入、schema、科学路由、受管实验、材料和来源检查，不改写 W 结论，不宣称科学资格或审计通过。重复操作只能核对同一接纳和既有后继，不创建新作者 turn。

这一例外仅用于原 W 收口并转入 `purpose=closeout` 的 A 审查、审计与交付；不放宽普通 search/correction 的历史直接来源资格，不重新开放探索。同 uid 信任和隔离启动器的软边界不变。实施记录及具体阶段额度保留在本地 ignored 原件中，不作为长期产品默认值。

它复用精确历史实验、原模型和声明权限，不授权新训练；收口的受管入口拒绝新执行，原实验及模型原件只读。真实 W 返回科学 JSON 并封存后进入收口审查。该次续接仍消费累计控制步，不替代科学资格或执行决定，也不清除任何未知原件事实。同 uid 原生进程仍属于信任的软边界；这些合同和受管入口限制不保证阻止任意同用户代码进行计算或修改其有权写入的位置。

授权说明是审计声明，不是身份认证。控制账本不加入科学输入或新增原生可写路径；
使用经验证的隔离启动器限制科学角色修改宿主控制记录。直接 `codex` 的软隔离不能证明人类身份或这种权限隔离。
原生用量保留已收到的 `turn.completed` 及精确绑定 session 的已观测计数；实时下界不等于完整账单，运行中可能缺量或滞后，token 上限不是精确账单保证。
工作台用量卡同时列出累计输入、缓存输入、非缓存输入、输出及缓存命中率；命中率为全部已记录缓存输入之和 / 全部已记录输入之和，输入为零时不可用。缓存输入已包含在输入中，总用量仍为输入 + 输出，不重复收费。数值沿用后端已记录的计量；历史或未提供缓存字段的终态报告可能已归一为零，无法据此区分真实零命中与未报告缓存。
“最近一次上报上下文”只使用当前最新精确阶段 / attempt 的后端 `last_token_usage.total_tokens / model_context_window`，不把累计消费除以上下文窗口；压缩后占比可以下降。后端未提供完整字段、观测异常或尚无当前 attempt 上报时显示不可用，不继承父线程或上一阶段的上下文。可用时显示上报值、占比与已知上报时间；运行中请求及平台上报延迟仍未计入，上报时间缺失则明确未知。可选遥测仅保存在 `usage-observation.json`，`usage.json` 的精确计量格式及预算、准入规则不变。
原生会话首次定位按每次轮询最多 256 个目录项分批完成；大共享 home 可能需要多个轮询周期，界面显示“正在定位会话计量”。完成全树唯一性核对并绑定精确线程与工作目录后，只增量读取固定 session 文件，不重复扫描目录。后续更新仍取决于后端何时写出计量事件；公开进度按精确阶段和 attempt 显示同一用量拆分。
runner 收养主进程退出后的普通子进程，并按真实归属和启动身份清理；清理无法确认时停止封存。
runner 意外退出时，本地 watchdog 仅清理能精确核对的标准进程组成员；无法核对时保留 `cleanup_unknown`。
启动器应自行保证脱离进程组的后代清理；现有隔离启动器的原权限和配置协议保持原样。

## 可选的全角色节点预算

新实例可在 `prepare` 添加 `--node-budget-policy /absolute/path/node-policy.json`。
JSON 必须恰有 `H/W/A/E` 四个对象，每个对象恰有正整数 `max_observed_tokens`
和正有限数 `max_seconds`。没有节点策略默认数值；操作者必须为每个角色明确冻结两项目标。
应保留参数所依据的已观察成本或试用假设；历史最大完成事件不是下一次请求的成本保证。
当前协议 23 的 `--node-budget-mode` 仅接受 `soft`；API prepare 同样拒绝 hard。旧协议实例只读，不追加策略、不迁移或改写冻结设置。

H/W/A/E 单节点 token 目标统一为软上限，W 的 token/time 目标分别取该策略与原 search policy 的较小值。
启动前核对共享余额与原节点锚点，不预付整个节点 token 额度。运行中的节点达到 input+output
目标时提示收口；节点和 W 阶段目标不拒绝新的受管实验、不强迫转 A review，原节点可以完成并封存原结果；cached input 已包含
在 input 内，不重复累加。实际消费完整记账，不增加或退还共享额度。单次真实执行、FINAL A
和 author closeout 的有限时长、清理及权限限制仍有效。

`native-intent.json` 的 `budget_slice` 冻结角色、任务身份、同 boot 推进起点和有效上限；W
搜索统计复用这个起点。`status.nodes` 显示原锚点、终态 `budget_report` 和 `stop_reason`。
完成事件前 token 可能完全缺失，一次事件也可能大幅越限；时间检测的轮询与终止/清理开销
同样计费。节点阈值不是精确在途账单或严格零超额保证。普通节点无 completed usage、boot 失配、
未知提交或无法确认清理时保留失败/未知原件，不重放、不退款；硬停止输出不封存为科学成功。
仅计费未知的精确最终 A 适用下述例外，不把账单缺口等同于原生终态或清理未知。

所有角色均尝试发布小于等于 4096 字节的 `work/budget-status.json`。
其中共享余额来自既有上限减 provider 已报告累计消费，不扣估算未知用量；计量覆盖未知仍显式列出，这不是实际费用上限。
续接不清除先前消费。
soft 节点目标仍是建议；启动前核对共享余额，不足则进入 FINAL。已启动的 H/W/A/E 不因共享 token 或 activeSeconds 阈值被运行中强杀；当前节点完成后超额完整记账，余额不足时不再启动下一普通节点。任务单次执行的硬限仍保留。
author closeout 使用本次已验证 permit 的 token 软目标与有限时间，
不会把原 W 上限当作 author 额度；缺少本次额度时明确标记当前边界不可用。部分只读 author 视图不暴露动态更新，
以其明确的静态 token 软目标和有限时间为准。提示只能在文件可见且被读取时生效，不是授权、计量原件或科学证据。
每次请求已报告的全部 input 都计入用量，cached input 已包含在 input 中；未知计量保留覆盖未知，不补造零用量。
运行中请求及报告滞后仍未计量，当前接口没有逐模型请求前的预算准入。
预算提示缺失或陈旧不授予新额度，也不成为执行拒绝；原身份校验与独立执行授权仍适用。
提示不能替代运行时边界或证明实际 token 节省。

新建实例冻结 `final_a_completion_policy={"version":1}`。普通 state 只检查剩余额度能否启动
下一动作；该模式不再为 protected FINAL 预扣共享 token、时间或步数，也不要求启动前余额容纳
整个节点 token 目标。provider 已报告 token 余额为正即可通过普通 token 启动检查，原有限时间、
步数、完整性与真实执行边界继续检查。soft 节点和 W 阶段目标达线只提示收口，不停止接受新受管调用或强迫转 A，
不杀原节点；H/W/A/E 及 author closeout 均无单节点 token 阈值强杀。不能启动便在现有控制账本持久进入
`FINAL`（`closeout_begin` 的 `final` 模式）。
FINAL 只有一次精确原始 A：基于已有已接受 W 给结论、明确选择，并在
`summary / scientific_result / limitations` 说明未完成目标及缺失证据；共享 token、总时间或步数
不足、为零或超额，以及仅用量未知，均不否决这次 A 或已有封存 W 的原授权交付；未知用量仍保留记账。若已有候选原件及交付权限，A 必须选择并授权原 host 提交，
控制层输出真实的确认、失败或未知回执，不能由 A 编造提交成功。缺少 W、候选原件或交付权限时，
A 仍须给出结论和限制，host 明确输出未提交及原因。

这次 A 若已观察到原 `turn.completed`、退出码 0、原进程组确认清理且科学输出完整，
缺少 usage 或终态 usage 与实时计数不一致不会阻止封存或该 A 的原授权 submit/confirm。
未知账单继续列为 unknown，已有观测下界保留，不写零用量替代，也不另起 A。缺少完成事件、
非零退出、清理未知、实际执行或外部提交未知仍不能由此变成成功，须按原身份核对，禁止重放。

同一 FINAL 周期不再启动 W/E、独立科学审查、补正或另一轮 A；原 host 提交内的必要机械资格检查保留。
未知外部操作只对同一原调用核对，不重新提交。实际用量、cached input 一次计量、时间、步数与
`final_overrun` 完整保留，原共享额度不增加、不退款。单次调用、清理和提交确认的有限时间、
资源与执行权限仍约束原执行。未配置可选节点预算时，使用原冻结全局 token/时间设置作为最终
调用的 token 参考目标与有限时长；不是从剩余额度另批预算。旧实例缺少该冻结策略时保留原行为。

若用户之后明确提高额度阈值且足以启动原待执行 normal 动作，下一次 run/continue 可从 FINAL 前保存的
逻辑路由继续；`budget-amend` 本身只记账，不启动模型。使用单调递增的新物理节点编号，原最终 A、
交付、用量和原始记录全部保留，绝不重跑已封存节点或退还消费。运行中的最终 A 先收集同一原执行，
未知外部操作先按原调用核对；阈值降低或提高后仍不足，不恢复、不重复最终 A。之后再次耗尽共享额度，
进入新的 FINAL 周期，各周期只运行一次最终 A。没有待恢复 normal 路由的主动 closeout 保持终态。

`pause` 只阻止下一节点，已经接受的任务继续计时并按原上限执行。科学 `budget-amend` 返回
持久操作回执，可先排队并在下一新任务前的安全边界应用；accepted 不等于已应用。运行中/未知
原调用不因修订而获重发资格。累计消费、未知用量、既有节点策略、锚点和终态消费报告均保留。
protected FINAL 不增加普通探索额度；外部交付仍需显式配置。节点停止不代表已交 A、已交付或科学目标完成，
也不保证剩余额度足够完成后续节点。

## 启动边界

launcher 使用 argv，不拼接 shell。可复用经过验证的外部隔离启动器，但须适配上述目录和私有 schema。
Codex 后端是软边界：关闭项目 AGENTS 自动加载，默认关闭 native 子 Agent 和 fast，按冻结声明投影模型与权限。可显式声明 subagents 或 fast，但不因此取得完整作者、用量或正式独审资格。认证保留在原 home，无需手改用户配置；显式运行设置忽略用户配置覆盖。框架不注入退役 MCP 配置。protocol 后端由 launcher 实施权限，不强制套用 Codex 目录 sandbox；不支持原线程恢复或完整作者证明。
当前执行审批信任 Worker 及其本地进程：HMAC、精确调用绑定、有效期和预算用于防误操作与状态混淆，不阻止同权限进程读取密钥或伪造批准。密钥路径不显示、0600或另起同用户进程均不构成强隔离。需要防恶意 Worker 的部署仍须单独建立真实权限边界。
其他配置仍可能继承本机环境；涉及隐藏基准资料应使用已经验证的隔离启动器。
注册功能插件与 CLI 实例 UI/端口已接入，旧入口已退役。支持上文限定的同宿主冷 checkpoint 恢复；跨宿主完整灾备仍不支持。未知原生结果须核对原调用，不能自动重放。

## 可选候选事实观察

`prepare --history-observer-config /绝对路径/observer.json` 可冻结一个可信宿主只读 adapter：

```json
{"adapter":"/绝对路径/scripts/observe_covshift_candidates.py","config":{"runs":"runs","evaluation_key":"cov-public-val-score-d78f5eb-v1"}}
```

默认不配置 adapter；历史仍维护 H/W/A/E 的原摘要、限制、H 范围与科学结论页。
通用控制层不识别 cov 字段、不读取隐藏评分。adapter 仅观察原件，不执行模型、反序列化模型或提交。
候选页按首次观察顺序展示候选事实、当前候选（无事实为 null）、A 推荐和实际提交观察；同分、未评分、失败和未知候选均保留，紧凑预览机械省略时标明数量并链接完整索引。
A 可在新协议最终科学 JSON 可选填写 `candidate_recommendation`（已列候选 ID；未知可省略或 null）。
推荐不改变提交；公开 harness 的实际提交观察也不证明外部评测接收或隐藏得分。
代理记录提供原科学结论页以回查适用条件与盲区，不能当成目标最优或模型已留存证明。

观察调用上限仍为 5 秒；完整输入、输出、候选/上下文清单及内部传输不再设置框架固定字节或累计项数上限。超时或格式问题保留明确不可用事实，不冒充完整观察。
配置、封存绑定和归档原件篡改仍拒绝恢复。完整公开台账及提交模型的原观察字节由宿主按内容去重封存，使用流式哈希和复制，不设原单件 32MiB、总对象 128MiB 上限；既有 `digest_only` 或 `unavailable` 记录不因新规则被追认成可回读原件。
历史默认投递紧凑简报（最多 4096 字节），按需读取的历史分页每页最多 64000 字节；可选 overview 超限时明确省略，完整观察和索引仍保留。归档私有原件不因此全部挂给科学角色；读取仍按冻结来源与权限解析。
最后一个节点也更新 `host-history/overview.json` 投影；投影删失可由封存科学原件和观察快照重建，观察快照不是可删除缓存。


## 可选交付策略与旧实例的预算内收口

新实例可在 `prepare` 同时冻结 `--node-budget-policy` 和 `--delivery-policy`。
交付策略包含 `closeout_a` 的 token/秒上限、`inflight_tokens`、`cleanup_seconds`、
`submit_seconds`、`confirm_seconds` 和 `adapter`；各值由操作者明确配置，没有生产默认预算。
当前冻结 `final_a_completion_policy` 的实例遵循上文 FINAL：普通状态按剩余额度准入，每周期一次最终 A，host 保留真实提交回执；显式提高阈值后可按已保存的待执行路由恢复。以下预留及多轮收口规则只描述缺少该冻结策略的历史实例。

旧实例的预留来自原总额度，不是追加额度。默认 soft 模式预留一次收口 A 目标、在途缓冲、清理及提交/确认；有预留之外的正额度即可准入探索，不要求剩余额度容纳完整下一节点目标。进入收口前为下一次 A 保留声明的 token/时间目标与机械交付底线；接受后的 soft A 仍按共享剩余预算运行。额外 A 逐次准入，可选审计另保留一个后续 A 的完整目标。
旧协议实例（包括 hard 或未记录模式）仅保留只读原件，不能按协议 23 继续运行，也不自动迁移。soft 提示不能保证在途节点及时返回；真实执行的有限时间与权限边界仍有效。

适配器是独立的领域接口。核心冻结其代码、启动器、输入原件及读取范围，不解释领域台账。
收口先冻结完整候选目录，`science-task.json` 给出目录文件；启用分类提名时默认只预览 W 提名并注明其余数量，完整目录保留全部精确候选 ID 和来源节点，不按紧凑摘要容量截断。控制层仅为 A 明确选择的操作解析并冻结原件；候选目录本身不启动 smoke、审计或自动排除候选。可用性与审计资格分别展示；精确原件的审计结果、工件匹配和适用范围沿历史传递，不额外制造未知泄漏待办。A 选择不等于审计通过。原生 `exp submit` 保留公共命令，但只使用 run 声明的路径，不解析历史跨节点原件；受管 wrapper 的 submit/help 会提示历史交付使用 A `deliver`，不会阻断或改写命令。
旧预算内收口可复用普通 A 已封存的 `deliver` 直接进入控制交付，不经过 E；无选择策略时可保持 H/W/A 三节点。当前 FINAL 策略则保留独立的一次最终 A。显式 `selection_policy` 版本 2 仅含 `{"version":2}`，冻结全部候选事实目录，再由精确 closeout A 负责比较。真实 audit/deliver 的 `candidate_recommendation` 必须命名目录中的精确可用候选（或绑定到同一已登记成果的实现引用）；普通科学建议的坏引用保留原文并诊断，不能成为执行选择。`candidate_comparison` 是可选科学说明，不以 `keep`、非空 `rationale`、`key_risk` 或 `evidence_steps` 格式决定提交准入；A 在 `scientific_result` 说明选择与证据。null 不授权交付。旧 `shortlist_k`、`evaluation_key`、`smoke_seconds` 配置拒绝接受。交付策略要求的公开审计必须完整通过才能更新实际提交；失败、跳过或未知不能冒充审计通过。未提供可选 audit/smoke 能力保持中性，不绕过已冻结的必需审计策略。旧预算内收口 A 用 `deliver` 授权适配器交付，用 `audit` 请求资格，用 `finish` 拒绝交付，用 `wait` 暂停。`extract / continue_w / revise_h` 的原话保留并触发最多两次作者补正，不会静默转换成交付授权，不会据此重新开放收口探索；当前协议只为 A 明确选定的已绑定工程故障允许一次精确 W repair；旧冻结协议保持原件并拒绝按新协议运行。A 必须逐项对照原目标和授权范围，区分直接测量、推断与假设、目标指标与代理及敏感性检查；按同一目标准则解释相关候选、冲突证据、不确定性和缺失输入。预算耗尽、finish、审计资格与交付均不证明未测量的目标已经完成。正常审计往返及提交前完整审计明确拒绝后的改选只消耗原总预算，不消耗错误补正次数。完整审计拒绝的同一候选工件不能再次提交；工程故障、未运行和未分类回执不冒充科学拒绝。未知操作绝不改选或重放。
未知提交始终只核对同一原操作，绝不重复提交；旧预算内模式仍要求原余额足够，当前 FINAL 的一次同操作确认不受共享余额否决。
A 明确填写 `candidate_recommendation` 时，普通与最终 A 都须提供 `selection_explanation`。
`criteria` 写明当前比较准则，`criteria_change.status` 明确为 `unchanged` 或 `revised`。
修订时 `prior_criteria` 承接原设计/报告准则，`changes` 声明改了什么，`evidence`
给出可定位的证据与修订理由，`accepted_tradeoffs` 声明接受的代价；未修订时这些详情为 null。
A 须对照此前 H/W 设计与报告的比较条件、门槛和回退规则；放宽任一条件即应标记为
`revised`，不能因当前报告已经换用新规则就称为 `unchanged`。
首次选择没有前序准则可如实声明；没有候选推荐的规划 A 可填写 null。
框架只检查声明是否完整，不能以非空文字证明科学充分性或真伪，也不自动排序、决定默认候选。
缺项在封存前通过已冻结的 `resume_policy.output_repair` 退回同一 A 补填，不另开 A 或重做实验。
FINAL 补填继续绑定同一最终返回授权、session 与累计用量，保留原有限次数及单次时长；
共享余额不足不撤销其完成与原授权提交，未知执行不会因此自动重试。

`adapter: null` 时 `submit_seconds` 和 `confirm_seconds` 必须为零，不授权外部交付；当前 FINAL 仍由一次 A 给最终结论与未提交原因，旧预算内模式只作本地导出。

```bash
scid-research closeout --root /绝对路径/实例
scid-research closeout --root /绝对路径/实例 --export-only
```

已知硬停止留下未封存节点时，收口绑定停止前最后一个连续封存前缀；失败原件保留。
`--export-only` 不调用模型或外部适配器；它可记录本地收口事实，不是文件系统零写入命令。
原暂停、未知执行、清理与完整性检查继续生效；缺失用量保留未知账，单纯计费未知不估算扣费或单独阻止推进。旧预算内模式额度不足时只能导出现有状态；
新预算仍须通过原 `budget-amend` 明确授权。旧预算内收口不重新开放探索；当前 FINAL 仅在最新修订严格提高阈值且完整准入足够、原效果已核对时，按保存的 pending normal 路由恢复。
旧实例没有冻结交付策略时，只支持本地导出，不能原地追加适配器或修改原设置。

`delivery_confirmed` 仅表示 A 选定的确切工件已在声明目标确认交付；通用目录 adapter 只确认本地目录，不表示外部服务接收。`goal_completion` 仍为 `not_asserted`。
`exported_unsubmitted`、`closeout_unresolved`、`delivery_failed`、`delivery_unknown` 分别保留未提交、
未决、失败和未知，不等同科研任务完成。旧预算内模式配置交付但预算不足时返回 `closeout_budget_unavailable`，保留 model 边界；当前 FINAL 不以共享余额不足拒绝最终 A，实际未提交仍给明确原因，不伪装成操作者 export_only。暂时来源准备失败返回 `closeout_preparation_unavailable`，保留失败原件，重复 `run/closeout` 可在原预算内重试准备并复用已封存 A；它不固化为操作者 `export_only`。清理未知继续阻止任何新执行。`status` 由相同原件重建状态和最新收口 A 推荐；此前实际提交观察与当前确认结果仍分开。真实科学效果须另行验收。

当前写协议为 **23**。协议 22 及更早的真实场和证据保持归档只读，不能用新代码续接、重解释或原地迁移；新场必须重新 `prepare`。此变更不增加预算，不对冻结旧场执行任何恢复。

适配器只冻结声明的脚本 `source.py`、模型和静态输入。受管 call 的封存脚本可来自旧 W scratch；审计台账局部改写为 `/inputs/source.py` 与 `/inputs/artifact`，旧 W workspace 不挂载到审计命名空间。脚本的任意本地兄弟模块或未声明依赖不会递归捕获；缺依赖属于工程故障，不能据此声明审计已完成或所有脚本都可交付。

### 审计工程故障与科学拒绝

进程退出 0、`leakage_flag=false` 都不足以证明审计通过。适配器核对精确候选、有限审计数值、完整执行回执及审计模型字节：`audited` 表示通过，`rejected` 才表示完整审计明确不合格；脚本、依赖、训练、进程或资源故障为 `engineering_error`，缺回执、无法可靠归因或原件身份不一致为 `unclassified`。后二者均不授予科学资格，也不自动要求换候选。

控制层先允许一次 scratch 审计工程恢复：重新从封存原件准备输入，写入独立不可变 intent/outcome，使用原共享余额与原单次上限并计步。它只重做未写真实交付目标的审计，不能重放 promote、confirm 或未知提交。真实执行、提交或进程清理未知仍阻止新效果及重放；仅用量未知不否决 FINAL 的最后 A 与已有封存 W 的原授权交付。重复 `run` 不重放已接受恢复。

确认是实现或未声明依赖问题时，可在原额度内建立一个精确 W repair，绑定故障回执、原候选、原 W 和封存输入。相同原候选 lineage 最多一个此类 W，不提高总额度或原次数上限。注册工具只允许对绑定 parent 运行一次新候选；不接受无 parent、新计划、换 parent 或重复调用。W 报告实现修复或需要科学改动，A 重新判断并授权新候选，旧授权不覆盖新字节。已封存结论不可覆写；只有未封存且符合既有恢复条件的任务可用 `worker-resume` 在原 session 续接。工件无法定位时不能重建并冒充历史原件。

选择策略在修复后保留旧目录原件，并按 repair W step 冻结新一代比较。原故障实现仍展示；A 必须根据新证据明确选择并说明理由。状态从当前代相同 intent/outcome 投影，工程故障和未分类早停不会显示成等待执行。

这套自动恢复针对控制适配器和注册工具的语义回执，不解析任意 shell 输出，也不把原生 `exp submit` 退出成功当作资格。冻结运行时/公共 harness 无法恢复的工程问题会明确停机；不会热改安装或递归捕获任意依赖。预算、输入容量不足或修复次数耗尽分别保留明确状态，不能静默增额或改派无限任务。

### 恢复能力的区别

控制器退出后可以重接仍活着的原 Worker；Worker 成功结束但未封存时，可以补封存原结果。
本节的硬停收口转交 A，并不是续跑已停止的 Worker。原线程续接见下节；
实际 Codex 同线程恢复仍未验证。任意本地 shell 仍是软隔离；
框架管理的外部执行应按原执行身份恢复，不重发不确定提交。

## 可选的 Worker 原线程续接

标准单命令 `codex` 的 CLI `prepare` 在未传 `--worker-resume-policy` 且未请求
`--worker-context` 时，冻结最多两次同任务、同 session 的输出补填或原受管执行唤醒：
`output_repair: true`、`output_only: true`、`execution_wake: true`。
补填只修已确认完成的返回；窄唤醒只解释同一原受管调用的已核验终态并完成返回，
不授权新实验、训练、失败任务续跑、`WorkRepairRequired` 科学修复或 W fragment。
launcher 的绝对调用路径及其指向的原文件被冻结，保留符号链接调用语义。
原 `CODEX_HOME`（未设置时为 `~/.codex`）仅在宿主私有策略中绑定，配置、认证和权限来源不变，
不复制凭据；没有节点预算文件时，各角色软目标引用原全局 token/时间上限，不预扣或增加共享额度。
自定义 launcher 没有默认补填授权；直接 controller API 仍须显式策略。

W 的 `science-task.json` 会报告 `managed_execution_wait.wake_authorized`。
原受管调用仍 pending/running/queued/cancelling 且回执指示 `return_control` 时，W 应正常返回科学 JSON 并保留原调用及限制，
由宿主保存未封存 checkpoint 后监控同一调用；不要仅为轮询而占住 native turn，也不要虚构终态。
这依赖 W 正常交回，框架不会强行终止活 W 并伪造成成功 checkpoint。
有冻结唤醒权限且原终态已验证时，宿主在原 home/session 追加有限 W turn；实际 session、权限、
累计预算、pause 及原调用绑定仍逐项核验。窄唤醒期间宿主拒绝新增受管调用，包括使用旧客户端提交；
原调用 query/collect/reconcile 不因此被禁，同用户 shell 仍是软边界。
旧 output-only 策略缺 `execution_wake` 或显式为 false 不会获得新权限；
缺能力在进入宿主监控时即明确停止，保留等待 checkpoint，不等终态后才发现无法续接。
旧显式完整 resume 策略未声明该标志时保留已有唤醒能力，显式 false 关闭唤醒。

`exp run/query/lookup/collect/reconcile/cancel` 的回执增加宿主生成的 `handoff` 投影，
与宿主等待使用同一状态判定规则，不改原 accepted/terminal/seal：
阻塞式执行器在调用内等待后返回回执，W 仍按返回的 `handoff` 行动，无需另选阻塞分支。
`action=return_control` 表示原调用可查且已冻结唤醒能力，W 正常交回当前科学 JSON；
`poll_original` 要求在同一 native turn 按 `poll_interval_seconds`（默认 1 秒）持续等待、
查询同一原 ID；仍 pending 且可查时持续到交接指示改变，或实际原预算、时间、宿主停止
限制触发。没有查询次数上限，也不因查询达到任意次数而交回；实际限制触发时正常交回并保留 pending 限制。
`consume_terminal` 仅在原终态已核验时要求消费原结果；
`reconcile_or_stop` 要求核对同一原调用或报告障碍，禁止盲轮询和重提交。
终态字符串本身不会得到消费指示；尚未核验时先用原 ID reconcile。
这些是执行器与 W 的交接合同，不是新的执行许可、额度或对任意模型的强制调度保证。

显式策略文件优先，包括 `output_repair: false` 或 JSON null 关闭默认补填。
若需要已知失败后的普通 Worker 原线程续接，新 `prepare` 须同时传
`--node-budget-policy /绝对路径/node.json` 和 `--worker-resume-policy /绝对路径/resume.json`，格式为：

```json
{"max_attempts":2,"protocol":"codex_exec_resume","usage_scope":"turn"}
```

`max_attempts` 只能为 1 或 2，表示初次执行之外最多新增的已接受续接次数。
必须使用支持 resume 的冻结 launcher；策略声明和文件身份校验不等于实际 native 资格。
旧未启用实例或 fresh-only wrapper 不支持，不原地改 settings，也不绕 wrapper 直接启动模型。

```bash
scid-research worker-resume --root /绝对路径/实例 --step 2
```

命令继续同一科学节点、真实 thread、home 和 workspace，追加 attempt 原件，保留 checkpoint；
原 job、初次 intent/outcome/usage/raw 不覆盖。已接受续接先收集原工作，重复命令不重派；
成功但未封存的输出优先补原 seal。仅接受非零数字 returncode、无 stop_reason、有效
completed meter 且清理可确认的已知本地故障；缺 session/thread/meter、执行或清理 unknown、
身份失配、原 pause 均不放行；收口继续阻止普通探索任务续接，但允许对该收口 A 的已知故障使用同一 `worker-resume --step` 和原 session。原模型、launcher、权限、attempt 上限、累计用量及交付底线均保留，不将预算硬停止当可无额续接。

所有 attempt 成本累计，原 node/W cap 不重置、不退款；续接使用当前有效共享授权，并占一个
原执行名额、保留交付预留。合法总额修订不自动扩节点 cap；命令没有续额参数。
受管理执行按原 ID 恢复，不重发不确定提交；普通 shell 仍由角色判断 checkpoint，不承诺任意脚本幂等。

本节的恢复边界由上述冻结策略、原件身份、累计用量和拒绝条件共同限定。源码审查与
合成进程和零模型打包检查仅验证所选本地输运及拒绝路径；实际 Codex 同线程有效性、增量用量
与权限尚未验证。零模型检查不能授予实际 native 恢复资格，也不证明任意科学工作能够
安全续接。具体运行仍须保留原 attempt 记录、失败及未决限制，按该运行的实际证据判断。
