# 无 Root 的科研 CLI

`scid-research` 直接启动新的 H/W/A/证据原生线程；控制程序执行科学角色选择的路由。
控制程序拥有原件绑定、预算和执行权限，H/W/A/E 拥有科学判断。CLI 是应用核心，实例 UI 是复用原实例状态与公共逻辑的可选入口；来源声明、正式审查和执行授权按准备配置启用。当前能力与资格边界见[架构](ARCHITECTURE.zh-CN.md)和[TCAD 资格](TCAD_QUALIFICATION_STATUS.zh-CN.md)。

## 安装入口

新部署默认 `SCID_DEPLOY_MODE=thin`，以当前用户安装，无需 sudo 或 root。
推荐 Python 3.12（包支持 3.10+）；基础 Python、pip、应用与安装工具依赖须预装，
一次性用户 venv 准备示例见[安装指南](INSTALL.zh-CN.md)。从源码 checkout 执行：

```sh
SCID_PYTHON=/absolute/python SCID_PLUGINS=cov_execution bash deploy/install.sh --dry-run
SCID_PYTHON=/absolute/python SCID_PLUGINS=cov_execution bash deploy/install.sh install
export PATH="$HOME/.local/bin:$PATH"
```

`--dry-run` 只预检，不写文件。安装离线使用 `--no-deps` 发布 `scid-research` /
`scid-experiment`，本身不创建 venv、不解析联网依赖；仅需核心时省略 `SCID_PLUGINS`。
默认 `SCID_INSTALL_ROOT=$HOME/.local/share/scidiscovery`（含 `site/` 和 `skills/`），
`SCID_BIN_ROOT=$HOME/.local/bin`，`SCID_BACKUP_ROOT=$HOME/.local/state/scidiscovery/install`。
安装按需创建命令目录；三个路径均可显式换成当前用户可写的绝对 canonical 目录。
模型/bootstrap 访问、Cov 训练数据与科学执行环境须另行准备。

薄安装不要求旧 `SCID_WORKSPACE`/`SCID_CODEX_LAUNCH_ROOT`，不初始化旧 SQL、密钥、
MCP/Root 角色、systemd 服务或默认实例。首次 `install` 拒绝已有 site 或薄 wrapper；
`reinstall` 在原路径先暂存验证后替换包和 Skill，发布失败恢复旧目录及命令。
更新前须停止使用该安装的控制进程、Worker 和 UI；安装器不停止旧 live 服务，
也不更新已冻结实例。`status` 只核本配置 wrapper 归属；
`uninstall` 仅删除匹配本配置的两个 wrapper，保留代码 prefix、实例及旧状态，
不能把卸载命令等同于所有 native/外部作业已停止。`reinstall.sh` 默认无 sudo 执行原目录更新，
无需更换 HOME 安装目录；参数、归属检查和失败回退见[安装指南](INSTALL.zh-CN.md)。

`SCID_DEPLOY_MODE=legacy` 已退役并在修改文件前拒绝。当前不提供旧 SQL reader 或协议迁移。安装不会自动运行以下命令或增加任何预算。

## 创建与运行

推荐通过单一 TOML 声明准备新实例；除要求准确绝对路径的 `project.base_root` 外，
声明路径相对该文件解析：

```sh
scid-research config-check --config /absolute/project/research.toml
scid-research config-inspect --config /absolute/project/research.toml
scid-research prepare --config /absolute/project/research.toml
scid-research config-inspect --root /absolute/project/research-instance
scid-research start --root /absolute/project/research-instance
scid-research operations --root /absolute/project/research-instance
```

`config-inspect --root INSTANCE` 默认 `--view frozen`，显示基础冻结设置。
`config-inspect --root INSTANCE --view current` 分开显示基础身份、active_revision、pending_revision
及各自的声明配置；待生效项只用于下一新节点，不改变在途作业。查看不会绑定修订、修订预算或提交工作。

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

已有 Git 工程可在完整 TOML 的对应表中明确配置标准 Codex 权限与工程目录，再使用同一
`config-check`、`prepare --config` 入口；省略 `[project]` 仍使用 `code/`。例如：

```toml
[agent]
launcher = ["codex"]
[permissions]
workspace = "workspace-write"
[project]
base_root = "/absolute/source-project"
editable = ["src", "tools"]
scratch = ["build"]
outputs = ["out"]
```

基础工程须为研究专用的独立普通 clone，不支持 linked worktree；研究期间不要提交、切换分支或移动 HEAD。
整个基础目录只读可见，包含 untracked/ignored 文件，只遮蔽根 `.git`。声明、HEAD 身份和可写目录随实例冻结，fork 保留原值；冻结 HEAD 是代码身份，不提供恶意篡改检测。
全部 editable 根共享固定代码采集额度：每文件 1 MiB、总计 50 MiB、4096 个文件、8192 个扫描条目，当前 `[project]` 无覆盖项；只读 base 内容不受这些代码采集额度限制。
`config-check` 和 `prepare` 对新声明工程的初始 tracked editable seed 做文件 stat 与条目计数预检，目录计数包含 editable 根及隐含父目录；超额在准备时报告。
预检不读取文件内容，也不扫描 untracked/ignored 内容。首次 seed 仍检查额度，运行期采集仍检查全部实际 editable 内容（含 untracked/ignored 普通条目）；后续新增或变大的文件仍可能阻止节点或调用启动。
原路径写入、单 commit 采集、初始/后继草稿、输出和 TCAD 包边界见
[工程主合同](RESEARCH_CONFIGURATION.md#declared-projects--声明工程)。

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

## 可选 Skill 选择

Skill 来自实际 CLI 安装的唯一 `skills/` 目录，按所选目录原地只读绑定，不在实例复制字节，
也不要求安装和实例位于同一文件系统。不支持 `[skills].root` 或 `SCID_SKILLS_ROOT` 覆盖；
裸 wheel 不含外置 Skill 资产，须由部署在预期安装目录一次发布。
在科研 TOML 声明集合，再为角色或命名 Worker 显式选择，例如：

```toml
[skills.sets.analysis]
items = ["curve-analysis", "scientific-paper-evidence"]
[agent.roles.W]
skill_set = "analysis"
```

明确选中的执行插件可通过 `research_defaults.optional_skill_sets` 提供可选集合；安装插件
或提供集合本身不启用任何角色。未声明角色/Worker 的 `skill_set` 即不注入。Prepare 冻结
展开名称与来源绑定，资源字节仍来自安装；既有 job/intent 保持原选择。缺失所选目录时，
错误列出名称、准确预期路径与修复方式。仅支持 Skill schema v3 安装绑定；旧 manifest/对象
格式在读取资产前拒绝，fork 不提供旧格式兼容或迁移。

此选择不屏蔽其它原生用户、系统或插件来源，不改变原生权限。标准 Codex 保留其冻结的
原生 home 与认证；Cov 专用启动器及其认证复制路径已移除。完整来源、profile 与更新合同见
[配置参考](RESEARCH_CONFIGURATION.md#selected-framework-skills--按角色选择框架-skill)。

## 机制编号与精确引用

当前协议 29 中，首次 H 和每次 H 修订都以同次 `commitments` 的 1 起始编号选择机制：

```json
{"commitments":[{"label":"机制 P","minimal_test":"有界区分测量","existing_ref":null}],"focus":{"index":1,"action":"screen"}}
```

这是 `search_plan` 的简短字段示例，完整返回仍保留 `scope` 等科学内容。
`minimal_test` 接受非空文本、对象或列表。结构化说明只在控制投影中显示为 JSON 文本，科学原件不改写；真正的字段错误会指出准确条目及字段，不要求作者改动合法的路线选择。
`index:null` 只允许 `screen`，保持多机制浅筛；空 commitments 只能使用 null screen。
修订中要选择已有机制，须在同次 commitments 中显式填写其 canonical `existing_ref`，再用该项编号；
省略的机制仍保留广度。控制分配 `h001.m1` 等稳定引用，不随 UI 排序改变。后续 A 的
`next_focus.mechanism_ref` 和 `mechanism_findings[].mechanism` 使用任务给出的精确引用。
W 的 `--mechanism 1` 使用冻结目录中的正整数编号，控制绑定准确机制及其 H 来源；显示 label
不参与匹配。准入拒绝回执列出公开原因与当时可选编号，不因显示为 pending 就保证可执行。
跨机制引用已有比较实验时，可在 `mechanism_findings` 的 observed 条目中写明准确 `call_id`；同一比较调用可以支持多个机制。科学关联由作者声明，控制层不猜测；不要求重跑实验。

窄输出补正只修改诊断指出的 JSON/布局字段，返回完整原 JSON，其余科学计划保持原义。
提示中的 `remaining_observed_tokens`、`remaining_active_seconds` 与
`previous_consumed_observed_tokens` 是本次补正的剩余额度及既有消费，`token_usage_unknown`
保留计量未知；新的预算提示使用上述字段，不要求或授权重跑旧实验。协议 27 实例明确拒绝，
不提供兼容读取或迁移，也不能通过手改旧 `settings.json` 的版本继续运行。

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

### 有界自动接续

启动命令仅含一个标准 `codex` 可执行程序时，新配置默认启用独立的
`resume_policy.automatic_recovery`，保留原模型/profile、权限、native home 和认证来源。
原 `output_only` 仍只允许输出补填；自动接续当前未完成任务依靠新策略的独立许可，
不重定向 home 或复制认证。显式旧 resume 策略未声明此字段时保持原义；自定义、多参数
launcher 及 protocol 后端不支持此自动路径，直接 controller API 不隐式添加默认策略。

TOML 对应 `[policies.resume.automatic_recovery]`，CLI 通过原
`--worker-resume-policy /absolute/resume.json` 在完整恢复对象内声明。网页在“更多研究配置
→ 独立复核与原调用恢复 → 原调用恢复策略”提供同一组字段。默认 `enabled=true`、
`max_continues=2`、`max_retries=3`、`initial_backoff_seconds=30`、
`max_backoff_seconds=300`、`jitter_fraction=0.1`、`max_wait_seconds=1800`。
时间单位为秒，`max_wait_seconds` 是累计实际服务等待上限；时间参数须有限非负且
`initial_backoff_seconds <= max_backoff_seconds <= max_wait_seconds`。次数为 0 禁用对应动作，
`enabled=false` 关闭自动策略；完整字段约束见[配置参考](RESEARCH_CONFIGURATION.md#automatic-recovery--自动接续)。

原生结构化事件明确报告未完成的提前停止时，才有限追加同 session continue；明确临时
限流、容量、连接或服务故障才有限退避重试。启动重试须有证据明确证明原请求未被接受，
不能凭缺 session 推断。永久认证、配额、配置错误及未分类原因停止；原生失败 message
额外支持本机 Codex CLI 连接合成 loopback 响应实证的 429 retry-limit/quota 完整文本及
有界锚定 503 HTTP 信封，不扫描普通 agent/tool 正文。其他 message-only 错误及未知 CLI
格式保持 unknown。完整 CLI JSON 显示合成 HTTP 失败前已有 `thread.started` 和
`turn.started`；临时 429/503 在同一空 Codex home 用精确 ID 执行 `exec resume` 后仍
是原 thread，并再次到达同一 loopback provider，全部请求无 Authorization；永久 quota
只判 permanent 停止，不续接。既有 session 的重试不要求 `not_accepted` 字段；无 session
启动重试仍须明确未接受证据。本机取样未验证真实服务商推理恢复、用量覆盖或原生权限资格。
正常完成 turn 缺少或给出无效必需输出仍走既有输出补填，合法 fragment 保持原阶段接续；正式完成、显式等待和暂停不自动追加普通工作。
普通 Codex 任务从首轮起以简短提示要求 JSON，不发送原生 schema 参数，也不追加旧 Schema
全文；Protocol 携带当前最小回答 Schema。旧 task 和 Schema 原件保留，实际输出说明明确当前
必填项和条件广度规则。真正正式独审另用其严格合同。已有格式失败只有在完整原生流明确证明
工作开始前被接口拒绝时，才可按原自动策略消费一个 retry 名额协商一次。其他 400、普通文字、
未知外部执行或已有科学活动不因此重放。原失败回执、未知计量和消费保留；`--json` 仅指 Codex
事件流，详见[输出传输](RESEARCH_CONFIGURATION.md#response-schema-transport--输出-schema-传输)。
已完成 W 的必需 JSON 合法但精确 `work-evidence.json` 仍判定 `incomplete` 时，
可依靠独立自动许可续接同一 W 原完整任务，保留原 session 与观察，绑定原返回、outcome
及证据快照，与提前停止共用 `max_continues`。必需格式和条件广度声明缺失走窄输出补填；补成合法
返回后，科学不足可另经独立许可续工。例外是宿主核验的末次未接受机械请求拒绝：它在原有限
额度内优先于该返回的 decision/blocked，覆盖 screen、improve 及无 search 普通任务；后续已
接受请求会抑制补正。没有这种拒绝的有效 partial、科学障碍或 decision 仍交 A；完整或不适用
的交付判定不追加恢复动作。
强制 host review 仍优先沿原路交 A。continue 次数或原预算耗尽留下可核验 partial 出口，
不制造 finding 或科学资格，也不退还已用预算。
protected FINAL 与 closeout 禁用自动科学续工，原有有限输出补填不变；上述格式协商仅沿用同一
原 final claim 与上限，不新增科学任务。普通收口复用原 native collector，收集原 attempt，
并按原许可处理合格的 interrupted 或已接受未启动重入、输出补填及格式协商；其他失败、
未知或续接不可用保留准确未封存诊断，
不自动改派普通任务。`closeout_native_unsealed` 区分已知失败须显式 `worker-resume` 与
执行未知须核对同一原调用，未知绝不重放；fragment 续接不可用保留原诊断。纯输出补填
启动被拒绝时报告 `output_repair_unavailable` 及原原因。

continue/retry 在同一任务原 attempt 链中分别累计，推进、重启或配置修订均不清零。
既有 controller writer 保留原 attempt、原因、分别计数、累计实际服务等待和 UTC 下次尝试
时间，用单调时间实际等待；没有 controller 就不自行执行。停机及控制程序未运行的空闲
不增加服务等待，崩溃时未测尾段保持未知。每次等待只取一次新预算快照，按原 active 的
BOOTTIME 或 elapsed 的 Unix 时间口径本地维护共享时间截止，仍每秒检查暂停；启动前
重新检查原预算与权限准入。到期只允许重新仲裁，不直接启动。
服务端 retry hint 超过单次退避上限或累计剩余等待时停止，不截短提示绕过上限。
`pause` 可停止服务等待并保留原安排，不启动替代 native；已接受的活动工作仍按原规则收尾。
网页显示等待/停止原因、下次尝试、分别计数、服务等待及限制，服务等待不表示科学失败。

原 native 存活或进程身份未核清、原受管调用 running/unknown 时不启动替代 agent；沿既有
原调用入口核对，不重 submit 未知执行，不退款或清零预算。配置保存仍只影响后续新节点，
不修改正在恢复任务的原策略。上述自动恢复策略本身不改变科学输出合同或通用科学预算
时钟；协议 28 的持续组合判断与最终覆盖合同见下文“文件与职责”。实际平台恢复资格
须另有准确原生证据，不能由源码检查推断。

## 从历史任务创建分支

`fork-preview` 与 `fork-create` 接受严格静止、冻结协议 28 的明确父实例，以及精确逻辑步骤 N；
不要求最近 A 已返回 `finish` 或研究已完成。新分支从 N 首次冻结科学输入在新 session 重跑，
N 输出和后缀不作为输入继承，不能选择任意 attempt 断点。普通、FINAL、正式审查、工程修复
及已有 continuation 的任务边界见[架构中的分支语义](ARCHITECTURE.zh-CN.md#历史任务分支)。
缺必要控制、历史 cut 不足、身份歧义或协议不支持时明确拒绝；unknown native、受管执行、
准备或交付效果须回原调用对账，禁止换身份自动重提。真正消费科学原件时仍严格核对相关来源。

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
通用指引延后到合法后继。已选 run 的准确代码 A 经有界 Git 对象传输进入 child 自有私有 Git，
不依赖父 alternates、对象硬链接或持久 bundle；父私有 Git 删除后仍可消费已转移的代码。
当前 dirty/untracked 代码 B 另行复制，构建所选 child job 时恢复到可写 `code/`
（project 模式仅为 `project/` 下准确声明的 editable 草稿）；未封存交接
草稿留在已有 `private_snapshot` 条目中可读，不回填 child 的 native 执行命名空间，不自动
commit、seal、启动调用或继承科学资格与许可。科学可见范围仍由原任务准确输入 cut 限定。
必要 typed 控制由 child 持有，读取已复制控制不依赖父 jobs/control 继续存在。

科学数据、全局输入和节点产物直接引用其准确实际原件，不复制、硬链接正文或新建源冻结
缓存/归档包。源科学原件被删时，相应消费可以不可用；manifest/任务元数据读取不重开未选
科学正文。需要编辑的具体输入另存 `work/`。fork 不作全父目录 inventory，不以文件链接数
维护科学引用。工具、认证、模型 session、solver 和运行环境仍须满足自身声明与准入条件。
分支不会复制、重建或扩大已声明的外部文件读取范围、运行路径映射或交付目标；继续子实例前，
这些路径及目标仍须在原声明范围内有效可用。

创建响应丢失时，使用完全相同的 `fork-create` 请求和 `operation_id` 核对持久回执，保留原
`expected-preview-id` 与指引原文；不要另换 ID 或目标来猜测成功与否。已发布回执可在父目录消失后
核对。已确认发布的子实例即使删除或移走，同一操作仍只返回原创建回执，不会重新创建。
发布尚未确认且原 stage 与目标均消失时，`fork_publication_unknown` 保留原身份和回执，须核对
原操作；它不表示创建成功，也不会重建 child。同 ID 改请求会拒绝；预览过期时须重新预览，
已尝试创建须先核对原操作。目标父目录的私有 operation 记录不得随 child 一起手工清理；
无归属临时目录不会被通配扫描或自动删除。

网页在历史节点提供“从这里分叉”，使用同一 service，错误时保留草稿，创建后链接暂停的新实例；
打开新实例后点击“继续研究”才推进。
同 UI host/browser session 中可“核对原操作结果”；跨 host 重启的通用 Web 恢复尚未建立，必要时
用保存的完整 CLI 请求核对持久回执。分支共享使用当前版本的协作 lease 与只读原件，属于同用户
软边界；不得与升级前仍活动的旧 writer 混用，新 guard 不会追溯锁住旧版本进程。
全部合作宿主进程须共享稳定的 `/tmp/scidiscovery-owner-gates-<euid>` 目录、锁 inode 和实例
挂载，进程存活期间不得清理 gate。PrivateTmp 或容器私有 `/tmp` 必须由部署提供共同挂载；
互不可见的私有 gate 不受当前协作锁合同支持。

## 删除明确实例

`instance-delete` 永久移除明确实例的本地数据和证据，不提供撤销或安全擦除。命令必须同时指定
canonical 绝对路径、实例 ID、冻结 settings 文件引用和本次操作 ID；没有默认实例或强制绕过。请在目标实例目录之外执行。
先用 `status --root /absolute/instance` 核对 `goal_id` 与精确 settings 路径，
再将准确值填入请求；不要根据目录最新时间选择目标。`--settings-ref` 接收 JSON 对象，
引用合同及哈希白名单见[架构](ARCHITECTURE.zh-CN.md)。

```sh
scid-research instance-delete --root /absolute/instance \
  --instance-id EXACT_GOAL_ID --settings-ref '{"path":"/absolute/instance/settings.json"}' \
  --operation-id delete-instance-001
```

未使用的已准备实例、暂停实例和已完成实例均须先通过静止检查。仍在运行、正在被 fork 读取、
存在未知 native/受管执行或交付效果、清理状态不明的实例会被拒绝；删除不会取消、查询或重提
外部执行，也不会补写完成证据。挂载子树和不安全目标路径会拒绝；静止检查需要读取的控制记录命名空间不接受符号链接，
其他内部符号链接只删除链接本身，不访问外部目标。目标内若包含独立实例，包含仍可识别的损坏实例，
删除和清理重试均会拒绝；应先分别处理这些内层实例。prepare/fork 创建与删除通过目录链 gate
互斥；删除持有一个按原目录 inode 绑定的物理 gate，跨 rename 保护原路径与待删除路径。

静止检查核对必要 accepted、submission、terminal、authority 与 retirement 控制及原进程/
replay/交付停写证据，不要求未选科学正文、旧代码草稿或已清导出存在；必要控制损坏仍拒绝。
准入后先持久记录退役状态，阻止新写入，再把准确目录移入同一父目录下的待删除位置后清理。
只清理本实例自有 Git、临时代码导出与目录，不 chmod 或删除外部原件，不扫描邻实例求引用
计数。外部凭据、工具及原声明的实例外输入不随之删除。child 若引用本实例拥有的科学产物，
删除这些原件后对应消费可失效；child 已转移的 Git 与已复制控制仍独立持有。

成功返回 `instance_deleted`。少量删除回执保留在实例父目录的私有操作存储中；已有 legacy
owner 的外部 anchor 保留删除状态，旧 checkpoint 不重新激活 owner 代际。已有 frozen、
superseded、未知或未完成 owner 状态仍受原守卫约束，退役包命令不能绕过这些状态。

若中断、清理失败或响应丢失，保留并重放完全相同的请求和 `operation_id`，继续核对或清理原
待删除目录。不得换 ID 猜测结果，也不要手工删掉操作回执。原路径后来新建了另一个实例时，
原删除请求不会把它当成目标；清理期限不重置或退还科学预算。共享文件与同用户进程仍属于协作软边界。

## 当前进度与封存结果

```sh
scid-research progress --root /absolute/instance --step 2 --attempt 0 --limit-bytes 65536
```

续读时传上次返回的 `--cursor`；`--limit-bytes` 支持 1024–262144。进度只展示后端明确
公开的消息、工具名及状态、用量等有界类型化内容；草稿/日志与封存科学结果分开，不提供
私有推理、完整参数、原生 session 或 stderr。`stream_state` 可为 ready、partial_line、
missing、truncated、replaced、unavailable；截断或替换后须显式省略 cursor 重新读取。
进度入口从已接受的节点记录取得角色及任务身份，不读取完整任务正文；任务中材料较多不影响公开日志读取。
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
第二步的“研究技能”默认折叠，展开后展示已安装 Skill 的名称与说明，可逐项勾选给 H/W/A/E；默认均不启用。
导入的集合和角色绑定会保留，已有实例只能在已冻结的技能集合中调整角色选择。
也可明确选择 TOML 导入；首页和新建页不会读取或选择工作区的 `research.toml`。配置文件不是必填项。
表单可单独保存或导出配置；保存 TOML 只创建新文件，已有文件或符号链接均拒绝覆盖，可另选新路径或下载。检查后展示最终生效配置，再确认创建；仅本次创建使用的修改不会回写导入文件。
创建不调用模型，进入实例后须单独点击“开始研究”。“加载已有实例”按精确目录打开既有研究。
workspace 本身没有科学身份、预算或启动/恢复控件；准备新实例仍须显式给齐预算等必要输入。
`--root` 与 `--workspace` 二选一，既有 `--root` 用法保持不变。

密钥由操作者提供，须为现有普通文件、权限0600且至少32字节。命令监听 loopback 并输出独立只读/管理链接，重启后旧浏览凭据失效。普通本地实例无需启用正式审查或执行审批，也能查看节点状态、请求暂停和继续。暂停只阻止下一节点；已接受工作继续完成。继续复用原实例写锁，推进由 detached 控制进程拥有；关闭网页服务不停止已接受推进。页面提供实例状态、精确封存科学原文和成果入口，不提供任意路径或完整私有会话读取。

原生已完成但尚未封存时，页面显示“原生已完成，待收集封存”；管理页面提供“收集封存并继续研究”，复用既有恢复操作，并列出对应的 `worker-resume --root INSTANCE --step N` 命令。它先收集原结果；需要输出补正时仍按原 Worker 条件和预算处理，之后可继续研究。结果未知或实际失败的任务不会因此获得收集按钮或重提许可。页面显示退出码、已识别服务错误码和停止代码；管理页面给出原回执的本机相对位置。未分类的控制程序异常保存到 `.scid-ui/errors/<reference>.json`，页面显示准确诊断位置，不要求手工编辑控制 JSON，也不公开完整私有原生日志。

开始、恢复、继续与预算表单的同一次提交保留准确请求和原 operation ID；响应丢失后重复
提交只核对已保存的待处理或终态回执，不再次派发。重新打开表单才产生新意图；已接受请求
须先按原操作核对。浏览凭据仅在原 host/session 内有效，持久操作回执仍由原账本保存。

`--instance-root` 可重复传入其它明确实例，管理页也可按精确已有根打开；不扫描目录、不按最近时间选择。
加载项核对不可变 `goal_id`；同一路径改为另一实例后重新建立其 UI 会话，旧表单不能操作新实例。
工作台可从已加载实例的管理入口确认删除静止实例，确认页展示目标绝对路径、goal_id、settings_ref 和 operation_id，沿用精确实例删除服务与原操作回执。独立 `--root` UI 自身持有 `ui.lock` 时，分支与删除控件禁用并显示工作台操作路径，直接提交也会拒绝；须先关闭该 host，再由工作台加载准确目录操作，工作台不会释放其他持有者的锁。
实例“设置”集中提供研究配置、方向建议与阅读偏好；控制请求和执行审批单列为记录与操作。创建、加载及切换研究由工作台入口负责。普通研究的预算编辑统一使用研究配置；预算耗尽或已进入收尾时，概览提供明确的“恢复前追加预算”链接，进入独立 `/budget-recovery` 页面，继续沿用原预算修订与恢复条件；预算生效后返回总览开始或继续研究，提交预算本身不启动研究。
实例内 `/configuration` 可编辑当前研究的模型、角色、预算、权限、实验执行、交付及可选策略，并区分已生效与待生效配置。保存后从下一个新节点开始生效；已准备或运行的节点及其原调用继续使用原配置。修改总预算须填写调整说明，累计消费不会重置。实例路径、原始目标、来源记录及网页服务设置不通过此入口修改。
新建研究使用独立 `/new` 入口，支持可选配置导入、保存、导出、检查及新实例准备。独立工作台的 `/configuration` 保留为新建入口。
导入文件目录决定相对路径基准；配置校验及准备复用 CLI API，修改新建声明不改变已有实例。网页复用预算排队、用户方向、操作回执、后台
启动/恢复与有界进度，无需网页也能用对应 CLI 入口。配置显示 requested/effective/enforcement 与
limitations：标准 Codex danger-full-access 不实施离线要求，read-only 即使请求联网仍离线，
protocol/自定义 launcher 权限为 hint_only、实际访问未知；详见[配置参考](RESEARCH_CONFIGURATION.md)。
新建表单支持 `[project]`、`skills.sets`、命名 Worker 及角色的 `worker`/`skill_set` 声明，
导入、保存和导出沿用同一解析合同；可选插件提供集合本身不启用 Skill，须显式选择。
已有实例编辑保留原工程和 Skill 集合声明，不能通过配置修订替换这些冻结原件。

工作台已加载列表、实例总览、用量、当前阶段和最新结论通过同源 SSE 自动同步；断线后自动重连并读取完整状态，不刷新整页，也不清空正在填写的配置、预算或方向建议。`/progress` 自动跟随当前节点；`/nodes/<step>/progress?attempt=N` 固定查看指定历史节点，公开输出仍用原有有界、不透明游标增量读取。等待结论会显示等待原因；普通 A 的 `wait` 可由管理者明确点击“继续研究”请求重新决策，网页不会自动重试。
每轮进度追读至多四页，然后回到两秒轮询；页面最多保留200条事件，明确提示已省略、
尚待追读和显示窗口移除的内容。新结论封存后提供结论入口，当前公开输出不冒充封存结果。

工作台和结果页面直接展示已保存的结论、用量和控制记录；浏览与下载已封存结果不重新计算文件哈希，也不重跑封存、资格或历史输入核对。科学执行与提交仍使用其原有准入流程。
显示缓存复用前检查保存来源的元数据，并串行处理失效与淘汰；全历史摘要不受完整节点缓存
容量限制，仍须逐项核对历史元数据。缓存不参与科学准入。时间用量显示复用控制预算的区间
规则，受支持的跨 boot 区间继续累计，不可读区间明确未知，不重置消费。

实例内有“总览 / 研究过程 / 结果 / 设置”四个入口：

- **总览**：查看当前状态、下一步操作、用量及研究轨迹；运行中的“实时进度”可进入当前阶段的公开工作记录。轨迹中的轮次只用于分组展示已有阶段，不规定固定的控制循环；各阶段链接可查看对应结论或固定的历史进度。
- **研究过程**：按机制查看 H 声明、W 发现与指标、A 处置及理由，并随封存阶段自动同步；展开可查看中间过程与原文。轨迹仅汇集当前窗口内至多50个已封存阶段，历史截断会明确提示。下方仍可按时间倒序查找阶段摘要，阅读发现、局限、完整结论原文和研究文件。
- **结果**：查看最终综合结论、作者推荐与交付记录。A 阶段的候选比较只显示其输入范围内最后 W 提名与 A 选中的候选，去重后按首次观察顺序、按评估口径分组；只有主动点击组内排序按钮才按该口径数值排序，可恢复观察顺序。不同口径不能合并比较，数值排序不代表科学排序。
- **设置**：集中提供研究配置、方向建议、阅读偏好，操作记录与审批另列。方向建议提供可选填写示例，并说明接收角色与生效范围；下一适用阶段指下一项匹配角色的新任务，不会改写正在执行或已准备的任务。记录区区分待交付、已交给研究者及撤回后续，交付不代表科学采纳。创建与加载研究放在工作台；保存当前配置与方向建议一样，只影响后续新节点。普通预算修改使用当前配置表单；预算耗尽或进入收尾后的追加与恢复使用独立恢复页，调整说明随变更记录保存。

`/results?after=N` 每页列20个封存节点，`/results/<step>` 显示精确结果；历史分页使用随机不透明 token 绑定固定快照，不以科学内容摘要生成身份。静态 JS/CSS 从同源资产路径加载，不使用 CSP 内容哈希。原科学 JSON 与附件入口保留。候选事实展示使用该节点输入时冻结的候选摘要；本节点 A 推荐为 null 时不回填旧推荐。HTML/SVG 成果仍下载，不内嵌执行。
各阶段的“研究文件”按封存清单中的原目录逐层浏览，实验子目录保留各自层级；子目录列在文件前，并显示目录内含后代目录的文件总数。每页最多20项，只列当前目录的子目录与文件，面包屑和“上一级”可返回；页面区分当前目录项目数与本阶段文件总数。目录只是清单名称的展示分组，不扫描工作目录或移动文件，下载仍使用原封存文件编号。
推荐候选下载复用该候选的准确封存文件编号；受管输出按已保存调用映射读取原 `retained-experiments/<call_id>/outputs/` 文件，不转向作者副本或开放任意工作目录。
独立 `/candidates`“全候选历史”入口按首次观察顺序、每页20个列出可读封存来源中的全部候选，包含未推荐和失败记录，并按评估口径与机制分组。分页固定已封存前缀；不可读阶段或候选关联明确公开缺口，计数标明下界，不将缺失关联算作可用证据。

页面的“阅读与翻译”保留目标语言、后端、模型及高级参数配置，没有默认翻译模型。设置与缓存保存在工作台目录的 `.scid-ui/`（独立实例入口则为该实例的 `.scid-ui/`），重启后继续使用；工作台内打开的实例共享同一套设置与缓存。这些应用设置独立于冻结的科学配置、角色模型及研究预算，不授予科学执行权限。

临时翻译支持默认 `codex` 或单一绝对 `codex` 路径。管理员点击“翻译缺失内容”后才启动串行调用：冻结原生 `read-only`、`network=false`、`web=disabled`、`subagents=false`，忽略用户配置、禁止权限提升审批，在独立临时目录执行；启动前另设 `features.shell_tool=false`、`tools.view_image=false` 和 `shell_environment_policy.inherit="none"`。宿主只传入列明的运行、认证、代理和证书环境项，保留 HOME/CODEX_HOME 认证位置引用，不读取或复制认证文件，也不继承任意科学上下文环境。单批超时为 1–300 秒，输出有大小上限，超时或异常会终止进程组。检测到工具事件即停止并拒绝译文；这是额外的事后拒绝，不能证明全部原生工具不可见，也不构成同用户强隔离。`protocol` 和自定义 wrapper 仅提供权限提示，无法落实此临时防护，因此新调用仍返回 `unsupported`，设置与缓存仍可读取。启动时若支持状态已改变，页面显示原因并停止后续批次。失败只在管理员显式再次点击后重试，读取或刷新不重派。

页面加载与实时更新只读取缓存；“显示原文 / 显示译文”可切换原文与已有译文。切换目标语言后读取对应缓存，缺失时继续显示原文；译文按字段原文和翻译设置区分，原文变化不会套用旧译文。编号、指标、评估口径、链接、代码、原始 JSON 及下载原件保持原样。译文只帮助阅读，不构成作者结论或评审。只读会话可读取缓存，修改设置仍需管理会话及浏览器绑定的 CSRF 校验。

可选 `serve-ui --translation-config /path/to/reading.toml` 从独立 TOML 的 `[translation]` 表初始化保留的阅读设置；配置后仍须满足上述原生防护要求，不能用参数绕过 `unsupported`。

未知页面错误只显示固定原因、处理提示及已保存的诊断编号；原始异常留在宿主私有诊断中，
最多保留32份、每份16 KiB。诊断保存失败会明确提示，不触发工作重试。操作记录中的请求 ID
用于核对原回执，不能作为私有错误诊断编号。

准备实例时可选 `--source-restrictions` 冻结来源声明、`--enable-formal-review` 启用精确对象独立审查。正式审查不等于执行授权。另启 `--enable-execution-authority` 与 `--execution-resource-policy` 才接资源准入：资源策略显式声明 `{"version":1,"allowance":{"wall_time_seconds":N,"max_storage_bytes":N}}`（N为正整数），执行器必须支持冻结的授权合同；不支持不能静默绕过。人工决定在同一实例页面作出，浏览权限本身不授执行额度。

查看授权只核对不可变声明、身份、精确对象、范围及已声明的期限或撤销状态，不遍历执行结果。真实执行入口另行检查科学资格、原件完整性，以及当前预算、暂停和活动/未知执行的准入条件。结果损坏或缺失不抹去已接受意图和消费，也不阻止查看或撤销授权；新效果仍须通过这些检查，未能核实的终态或计量事实保持未知。已 accepted 调用的 `collect/cancel` 沿用原权限和身份，不取得新的提交授权，也不重发原调用，仍受原调用或显式有限维护窗口约束。

资源账复用原 control ledger，换候选/重试不重置消耗。`budget-amend --execution-wall-seconds N --execution-storage-bytes N --authorization-note ...` 仅修订有限资源上限，不代替精确调用的执行决定；不得和科学token/时间修订参数混用。未知调用不退款、不重发，已接受调用的查询/取消不因W结束而被当成新提交拒绝。

TCAD 支持可选编译、精确封存、正式审查与后继执行的非空输入绑定。工程接缝存在不证明真实 solver 已取得资格；所需证据见[TCAD 资格](TCAD_QUALIFICATION_STATUS.zh-CN.md)。旧实例不会静默升级或复制预算，本机 HTTP 与浏览器检查不能替代远端资格。

### 原调用恢复与节点保留

`execution-resume --root ... --step N --call-id ... --max-seconds N --authorization-note ...` 显式推进一个原 W 调用；操作窗口不增加原科学或 solver 额度。只有原封存已记录完整的未准备证据，才能首次准备；可能提交过的调用只查询/核验，不能重放。仍有效的精确决定复用；过期时可显式给 `--request-valid-seconds N` 创建有限的新请求，再由原审批入口决定，不能借续接绕过批准。旧封存缺证据、清理不完整或余额不足不自动补造/续额。

`execution-recover` 与已有终态的 `execution-resume` 核对原控制关系，不因未选科学正文、
当前代码草稿或临时代码导出缺失而重新执行。已有或新补记的终态恢复，在原 native、bridge、
backend 与 replay 停写门满足后，按同一 call 的已保存 observation 幂等清理其准确导出。
已有终态的这一清理不进入新的科学准入，也不新建恢复操作；失败保留准确路径与诊断供
原调用重试。unknown 或缺 observation 时不推断可删，不重 submit、不退已耗预算。

封存 node 的交接、结论及调用产物留在原位置，通过既有 `result`、`materials`、`artifact`
入口查看和引用；保留成果不收集输入正文、不再 seal 或生成独立包。节点自己产生的训练
checkpoint 也是原节点产物，后继只引用同一原件。删除 node 产物或来源后，不能凭输入引用、
结果索引或代码 commit 还原科学正文。

独立包命令 `archive-export`、`archive-inspect`、`archive-restore-material`、`checkpoint-enroll`、
`checkpoint-export`、`checkpoint-restore` 及对应 API 已退役。历史包与审计记录保持只读，
不自动导入、迁移、删除、恢复或重新激活 owner，也不作为补齐缺原件或新实例推进的依据。

`execution-resume ... --maintenance query|collect|cancel` 为已经 accepted 的原调用提供有限的宿主维护窗口，须给 `--max-seconds` 和 `--authorization-note`。即使原科学时钟跨 boot 无法继续，也可查询、取消或收集同一调用；此模式不能准备、提交、新建授权请求或增加额度，不能与 `--request-valid-seconds` 合用。精确终态由既有资源账本结算；暂不能结算时不生成退款或新额度。

内置 Local 已有完整终态、只缺 completion 时，查询或收集会在原写者停止并取得同一调用的写锁后补齐结果，复用已冻结输出，不重新执行命令。原写者仍在运行时只报告未完成。
内置 TCAD SSH 收集在同一 run 内保留收集进度；下载或收据发布中断后，仍收集原 run，不重跑 solver。已写出的终态收据优先复用，不能靠重查产生不同的历史收据。远端 worker 丢失后，只有原 worker/solver 的停止事实可验证时才结束原 run；活着的孤儿 solver 仍受原硬时限及取消约束。缺少停止证明的运行继续保留 unknown 和原槽位，不重新提交。

### 未发布准备快照的准确维护

`execution-preparation-maintain --root ... --step N --call-id ... --snapshot-token ...
--action inspect|discard-partial|publish-accepted --max-seconds N --authorization-note ...`
只处理明确实例、原调用及 token 对应的宿主准备快照。绑定保存在该调用记录目录的
`host-preparations/<call-id>/<snapshot-token>.json`，不是按最近目录猜测原调用。
有限维护窗口不增加科学预算，也不重做 prepare、提交或准入。

- `inspect` 在原写者静止且取得原锁后检查准确快照。缺原绑定、身份不符或仍有写者时拒绝。
- `publish-accepted` 仅对原准入已完成、完整 accepted 和输入均已持久写出、只差发布的快照，
  补完成原来的不覆盖发布。原调用身份与已消耗额度保持；外部状态仍须用原 query/collect/cancel 核对。
- `discard-partial` 只清理同一绑定 inode 的残缺快照，保留绑定、清理记录和可能已发生外部 prepare 的
  unknown。完整 accepted 必须走发布；损坏的 accepted 不当作可删的残缺文件。清理成功不授权重提，
  未决记录仍阻断新 prepare 和 fork，不把目录消失解释成从未准备。

内置本地执行在复制输入时被终止，或现场清理失败后，可能留下尚无调用绑定的准确目录。
此时须显式选择同一入口的本地分支，不传 `--call-id`：

```bash
scid-research execution-preparation-maintain --root /absolute/instance --step N \
  --unbound-local --snapshot-token EXACT_TOKEN --action discard-partial \
  --max-seconds 30 --authorization-note '清理明确的本地残缺快照'
```

也可先用 `--action inspect`。此分支在取得源实例、控制器及准确 records 写者锁后，核对该 step
所有保留 attempt 的配置原件与 interface 投影，要求它们完整、一致、均属于内置本地执行且无
执行 authority。仅当前配置为 local、缺少绑定或缺少进程文件均不构成证明；第三方、混合历史、
缺原件或不匹配的目标绑定都拒绝。`discard-partial` 只删除明确 token 的现存残缺目录，
不增加持久记录；完整 durable accepted 不可删除。若内置 Local 的 accepted 和输入已完整写出、
只差原绑定或目录发布，可在同一 `--unbound-local` 入口使用 `--action publish-accepted`，
补齐精确原绑定并发布同一 inode；绑定或 rename 后中断也可用同一 token 收口。accepted 原字节、
调用身份和预算不改，不重新 prepare、准入或提交。目标不存在且没有匹配原发布记录时拒绝，不能据此补造
上次清理已完成的结论。其他无绑定 `.prepared-*` 不自动清理或补造来源；入口不接受任意 records
路径或批量删除。
已发布历史绑定保留原字节和来源；现场维护仍核对原宿主、owner 和目录身份，历史可读性
不授予新维护权限。捕获/导出后、prepare 前若尚无 host-preparation 绑定或 accepted，既有
维护入口不能凭缺记录证明可安全清理；保留原 call/export，不扫描 refs/exports 或重提调用。

### 普通 checkpoint 的边界

普通 `checkpoint.capture/validate` 保留既有任务、停写事实与小控制恢复点，原调用的 query、
核对和续接仍使用原身份、权限及已耗预算。checkpoint 不封存当前草稿，不保存草稿内容，
也不还原删除或丢失的草稿；接续依赖既有工作区仍存在的草稿。它不替代已退役的整实例
cold 包，也不从当前草稿或另一 run 猜造历史科学原件。

跨宿主、外部 anchor 丢失、缺少必要实现依赖、未知执行或未清理 native 进程不自动获得
推进或恢复权限。支持边界见[当前架构](ARCHITECTURE.zh-CN.md)；源码与本地过程检查不证明
真实掉电、真实 Codex/namespace 或远端 solver 已通过验收。

## 受管本地实验

协议 28 的普通原生 Worker 同样需要实际可用的 Linux `bwrap` 只读挂载能力，以保护共享输入、
选定 Skill 与动态 collect/wake 目录。自定义 launcher 也须保护；准确注册的模块启动器
在宿主准备后，于最终科学进程施加同一挂载。其 provider/module 身份无论是否选择 Skill
均冻结；普通包装脚本不能据名字豁免。不能挂载时明确拒绝启动，
不回退可写共享视图或全量复制。工作文件和输出的既有可写范围、网络及其他原生权限保持。
独立宿主调用若没有原 host launch owner，新的本地实验进程明确 unsupported；原调用的
query/collect/reconcile 仍通过既有记录核对。已建立只读视图的旧式公开 compile 不能再向
可写 artifacts 发布共享原件；当前 Worker 私有 compile 路径不受此限制。


标准单项 `codex` launcher 新建实例默认冻结 `--experiment-mode local`。W 启动前由控制层准备本地执行配置，既有原生宿主在该 attempt 生命周期内服务普通文件提交槽，自动处理请求并写回响应，无需另起提交服务。
默认受管本地命令要求 Linux、`bwrap` 和所选 Codex 安装中可解析的原生二进制，不依赖当前 Codex 会话注入 PATH 的临时 helper alias。原生 Worker 与科学子进程共同遵守同一受支持的 legacy `workspace-write` 声明，包括网络允许或禁止及临时目录排除，并按各自工作目录、`TMPDIR` 和命名空间等义解释。原生 Worker 保留 `--sandbox workspace-write` 及捕获的 legacy 配置覆盖；只有后端 sandbox 使用表达同一声明的内部权限 profile，内部机械投影不增加用户配置项。
named/default permissions、选定配置 profile、managed policy、额外可写根等不能忠实投影的权限配置，使受管命令明确返回 unsupported；不会为运行而降权或放宽原生权限。
自定义 launcher 省略模式时冻结 `external`，执行配置和提交服务由 launcher 提供；任务简报说明普通 shell 命令没有自动记录。自定义 launcher 可显式选择 `local`，但无法证明后端权限时受管启动明确不支持；`capabilities`、`describe` 仍可读取合同。
Cov 执行器来自独立 `scidiscovery-cov-execution` 插件，标识为 `cov_execution:experiment`；其 observer、delivery、receipt 与同调用恢复仍保留。专用 Cov native launcher 与旧验收工具已退役。默认科学子进程命名空间当前仅支持通用 Local executor；其它接口缺少受支持的启动接缝时明确返回 unsupported，不转换到另一执行器或改变原权限。
模式在 settings/job 中冻结，当前原任务恢复沿用原选择。
原生 shell 和权限照常可用，临时分析无需逐项受管。

未声明 `[project]` 时，W 新编写的执行代码须位于当前科学工作区的 `code/`，数据、日志和输出放在该目录之外；
以下示例使用此默认模式。工程原路径与 editable 入口按[工程主合同](RESEARCH_CONFIGURATION.md#declared-projects--声明工程)处理。
Local 的 `--experiment-dir` 选择独立的 `experiments/<name>` 运行工作目录；它固定整个
`code/` 树，缺失的运行目录在正常路径与准入检查后自动创建，无需先 `mkdir`。
脚本由 `--script` 指定，其相对路径始终从任务工作区根目录解析，不随 `--experiment-dir` 改变。
例如先编写 `code/check-one/run.py` 及其代码模块，
再使用独立 cwd：

```sh
scid-experiment --config work/experiment-config.json run \
  --experiment-dir experiments/check-one --script code/check-one/run.py -- --seed 7
```

提交前停止修改选定代码，捕获期间的目录/文件变化会被拒绝。宿主在 `adapter.prepare` 前
将代码原始字节固定到实例私有 Git；默认每文件 1 MiB、每树 50 MiB、4,096 个文件，拒绝
符号链接、特殊文件和路径逃逸，不扫描整个工作区寻找依赖。实验 cwd、科学数据、日志和输出
不进入 Git；已有科学输入仍走原冻结/留存合同，复制到 cwd 不自动取得原件身份。
默认原生宿主以固定导出只读覆盖整个逻辑 `code/`，隐藏可变草稿；独立 cwd 可写，
`experiments/check-one/artifacts/` 映到本调用受管输出目录。脚本将输出写入 `artifacts/`。
仅 `--script code/check-one/run.py` 时只固定该脚本，并覆盖其整个逻辑父目录以屏蔽未捕获的
兄弟模块；保留原科学 cwd，不能据此宣称多文件依赖已固定。
默认原生宿主还把原任务冻结的 `public_root` 目录以同一绝对路径只读映入科学子进程；只映该目录，不映公共父目录或相邻私有文件。与实例控制、原生 home、运行时或科学快照等命名空间挂载重叠的公共根在启动前明确返回 unsupported。公共目录仍是 live 读取，不证明全部动态读取已冻结；需要精确输入原件时使用本任务已有冻结绑定，不能把 cwd 当作新的数据封存入口。
科学子进程只获得运行时文件、科学工作区、上述公共根和安全权限投影，`CODEX_HOME=/task-native` 不包含认证或 provider 配置；原 job、控制账本和提交槽不在该文件视图中。这是受管子进程的视图合同，不改变对原生 Worker 及同用户进程的信任边界。`experiments/<name>/work` 可作运行 scratch，成果写 `artifacts/`。
命令参数按 argv 原样传入。控制记录命令、参数、退出码、耗时和日志，保留已有科学输入与输出原件。
Git 导出是临时视图；同一调用的 query/result/collect/cleanup 不依赖已删除的导出，实际消费代码
仍须原 commit 与调用 pin 可用，并从原版本物化，不读回草稿。控制维护 `code_version`，W 无需
计算哈希或操作私有 Git。原授权、预算、期限和 unknown 原调用核对要求保持。
命令默认最多65秒且不超过实例原秒数上限，完整执行日志不按紧凑视图容量截断；任务和共享预算仍适用。
可在研究 TOML 的 `[budget.storage]` 明确配置 `version=1`、`enabled=true`，以及至少一项
正整数 `max_scan_entries`（含空目录条目）、`max_files`、`max_file_bytes`、`max_total_bytes`
或 `max_instance_bytes`。未列维度为 null、不限制；默认 `enabled=false`，保留字段数值也不激活限制。
不用 TOML 的 prepare 可用 `--storage-policy /absolute/policy.json` 传同一策略。
策略按原 job/call 配置冻结，后续配置修订不改写旧调用限制。显式额度约束受管扫描、快照、
结果保留、日志和物化；超限保留原调用身份及缺失/未知限制，不自动重提。
实例累计按实例内唯一 regular inode 完整大小计费，hardlink 只计一次，reflink 保守计完整大小；
并发受管物化及其临时量在实例存储锁内核对。关闭额度时不扫描实例全树计量。
它不是任意原生 Worker/scratch 写入的硬文件系统配额；原有限 deadline 和内存保护始终有效。
已派发进程/外部任务身份、终态及永久退休等必要控制回执不因内容额度耗尽而拒写，
其字节仍计入后续用量；不能为了守内容额度丢失原调用恢复事实。
新 Git 绑定的 Local 成果直接写入已有 `work/retained-experiments/<call>/outputs`；确认实际写者停写后
原地封存，W 沿既有只读动态目录读取同一原件。节点所属 raw/artifacts 原件同样先确认实际停写再原地封存。
重复 collect、观察、checkpoint 和历史视图保留首次目的地及已保存原件证明；实际内容消费严格
核对已选原件，不再复制或刷新。普通 catalogue/status/provenance 读取核对控制关系，不重开
未选科学正文或 Git pin。checkpoint 不封存当前草稿。成果处理错误不改写已知终态。
没有 `code_version` 的 Local 调用（包括旧 `entry` 分支）保留既有复制语义。
外部 global/declaration 及跨节点输入通过既有绑定只读映射准确实际原件，不在节点复制正文或
chmod 外部来源；公开逻辑路径在宿主上未必有文件 alias。历史正文复用首次已有目的地。
旧记录保留原 archive/硬链接解释。需要修改时将具体文件另存 `work/` 或自己的编写目录。
原件逻辑来源和科学路径保持精确，相同字节不合并科学身份；同用户访问仍是软边界。
未在所选目录中的导入、环境及外部文件没有被快照，不据此承诺完整环境复现。
脚本应使用相对路径读取受管输入；argv 不推断文件含义或改写绝对路径，原生访问本身不构成读取快照的证明。
仅 `--script` 的单文件入口仍可用；需要兄弟代码模块和受管成果时使用独立 `--experiment-dir`。

上述目录和 argv 能力属于通用 Local executor。Cov 执行适配器的 `run` 仅接受单个 `--script code/<name>/run.py`，由宿主 Git 固定该脚本；不支持 `--experiment-dir` 或 `--` 后的脚本参数，也不递归封存兄弟模块。可通过 `capabilities` 和 `describe --key cov_single_source --output <新文件>` 读取所选接口的完整合同。不支持的命令选项在准备、准入和提交前返回 `known_not_started`、`accepted: false` 和 `caller_error`；修正命令后仍须遵守原预算与调用身份。此合同随新实例和新执行身份冻结，不能热改旧冻结实例。

代码审阅视图保留原 compiler 引用、UUID 和七字段 `code_version`，仅选中消费时物化导出；
启动后须确认 native、bridge 及适用后端已停止才清理导出，未知则保留。交付沿既有 wire version 2
和 `object_refs` 以零字节挂载点及只读 bind 读取原件。Cov 保留既有
`runs_root/model_run<run_id>.pkl` 成果原件。新 TCAD command 采集将科学输出、既有科学
`tcad_log` 及 facts 首写到既有节点/call 输出目录，manifest、transport-derived 文件、bindings
和传输 spool 保持私有。新 socket preparation 在准入或提交前明确不支持，准确旧 call 查询仍受支持。

上述恢复、分支、退役与删除规则描述源码行为；真实 default native、registered Cov、
Codex/命名空间、replay/model、solver 及 SSH 验收留到 G3 后由用户独立 CLI 进行。
当前源码改动不授科学资格，也不构成真实 sandbox 保证。

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

启用 H search 时，prepare --candidate-extra-slots N 冻结可选 W 提名预览的额外数量建议，默认 2。
candidate_nominations 可选；representatives、extras、gaps 均为条目列表。代表项使用 mechanism_ref、candidate，额外项
candidate，缺口项 mechanism_ref；reason 为可选科学说明。候选沿现有科学引用或准确当前调用
的 call_id/artifact 定位。省略提名不触发补填，全部已登记候选仍在候选池可选。
默认预览及 lineage.indexes.nominations 保留有效声明及原结论入口；A 可直接选择预览外
已有模型，在普通科学正文解释准则和限制，不必重跑或覆写 W 原结论。
无法解析的非空提名在任务预览、索引和网页保留非阻断诊断及原结论位置；紧凑预览省略细节时保留诊断计数和完整索引入口，不显示成作者没有提名。不会据标签推断候选或自动读取声明中的文件路径。

执行状态与结果状态分别显示：退出码0只表示命令成功；结果文件缺失为 `missing`，JSON或协议基础形状无法解析为 `invalid`，采集失败为 `unavailable`。个别条目或说明不可用时，其余声明仍为 `available` 并带局部 `diagnostics`。
原件、cleanup或外部结果未知时保留 `unknown`，不得重发以代替查明原调用。
完整执行、结果、观察和封存记录不受紧凑视图的字节或累计项数上限约束；公开摘要和分页视图仍有界，明确标记省略并保留原件入口。视图超限不会使封存失败或丢失候选、调用与成果的权威关联。
使用 `lineage-status` 查看已有调用，或用其原 call ID 执行 `query`、`lookup`、`collect`；失败和缺失不会变成科学合格。
无外部交付 adapter 时可使用 `scid-research closeout --root /path/to/instance --export-only` 保留成果及限制，状态为 `exported_unsubmitted`。
本地成果封存及交接不产生 cov 审计资格或外部提交确认。
显式配置通用目录交付 adapter 后，A 的 `deliver` 可授权控制层解析精确输入和成果、封装并确认目标目录；该确认只证明本地目录交付。
新实例启动脚本须先创建明确配置的本地交付目录，例如 `mkdir -p -- /path/to/new-run/delivery`，
再执行 `config-check` 和 `prepare`。内置目录 adapter 的配置检查会拒绝不存在、非规范或暴露实例根的目标；
不自动创建远端 provider 目标，也不在未知交付的恢复中重建目录或重提原操作。
确定尚未启动交付就遇到的本地错误报告为 `failed` 并给出固定原因；对先前交付的确认仍可能是 unknown。
适配器可用可选 `effect_scope` 声明公开效果范围（非空公开文本，不设固定长度上限）；内置目录 descriptor 明确只复制并确认已留存文件，不训练、不执行任务原生提交或科学审计。custom 未声明时为 `unknown`，不根据 argv 猜测。新 job 冻结公开 `configured_delivery`，同一任务续跑保留原声明；该描述不授执行权限或科学资格。W 在临时文件消失前将任务实际需要的成果与原生回执保存到既有声明输出，而非保留所有中间物；A 对照原任务交付要求、留存成果和实际范围明确缺口。FINAL 仍可交付最佳已有部分成果，不新增完成度硬门槛，也不让目录适配器补做缺失的训练、原生提交或审计。

## 文件与职责

H 提出竞争机制和最小判别依据，决定是否请求 E。W 在用户约束与 H 范围内拥有实验设计和搜索责任，根据结果新建、改进、修复或回退候选。A 判断全局贡献、关键证据缺口和下一方向；它可要求关键反例，但不默认指定完整算法、参数或扰动矩阵。机制改变交回 H。

机制承诺与 A 处置列表不设固定四项或 128 个机制上限，科学说明不设固定文本长度。配置的初始调用机会、额外调用授权、已接受消费及有限任务/共享预算仍有效；未知原调用先核对，不因机制更多而重发。

H 应先比较可信的竞争机制，再说明集中深挖的理由；单机制范围的合理性由科学作者解释，不设机制数量下限。参数扫描次数不能代替不同机制的判别证据。A 的 `screened` 只表示已评价精确 W 证据，仍可深挖；`deferred` 暂缓并保留未决路线，`waived` 才明确排除。每个排除须在既有 reason/scientific_result 中给出针对原目标与约束、由证据支持的科学否定或不可行理由；暂时相对落后、缺输入或缺证据应说明暂缓，控制不按关键词、分数或运行次数判理由质量。

后继角色可从 `lineage.indexes.mechanisms` 读取所有机制；已采纳的 A 处置提供理由预览、截断标记及完整科学 JSON 原件入口，长原文不受导航页大小上限省略。同状态重述或遗漏不替换已采纳依据，H 修订会清掉当前旧依据，历史原件保留。所有 search 实例默认向 H/A 持续展示完整组合，包括 screened；局部 improve/repair/validate 不抹去全局视野。机制页展示实际 accepted 调用、初始/额外剩余机会、独立完整 W→A 投入及未服务周期；它们不代表科学覆盖或每机制资源成本。按稳定身份展示，不按指标或欠服务周期排序；说明理由与 H 修订不清零投入。`--search-policy` JSON 可用正整数 `initial_calls` 配置每机制初始受管调用机会；`--portfolio-review-after-cycles N` 控制额外提醒间隔。每次实质评估已完成 W 的 A，portfolio_review 只必填 main_line（准确机制引用或 null）、
reason（一段自由说明）、discriminating_work（null 或三键新工作声明），合并最强可行竞争解释、最低成本判别工作、本轮动作和暂缓
触发/再议条件；没有可行替代时说明理由。真正适用的缺项走同任务有限输出补填；
等待、无完成 W、明确无 search、正式独审豁免，FINAL 实际评估 W 时仍适用。

pending 幂等重入 unfinished，不清零调用、授权和投入。新 discriminating_work 只填
mechanism_refs、question、next_action；后续 work_updates 可选且稀疏，已填项使用 work_ref、
action（execute/defer/withdraw）、reason，遗漏保留原问题、状态、A 来源和准确后继响应。
显式 execute 仍须匹配实际路线与 focus；未被执行或资格消费的可选科学引文错误仅记诊断。
执行不自动解决问题。当前主线可以不同于下一次判别实验的机制，实际 focus 和权限仍检查。
portfolio 索引持续显示独立 W→A 周期、机制参与、待评估 W、未服务周期、明确主线连续次数
及活跃判别待办。恢复和补填不增周期；同周期多机制比例可重叠，fork 只取准确 cut。
token 参考轮数须有足够完整样本及已知余额；缺失或滞后如实不可估，不保证资源或强制轮换。

普通结束/交付和 protected FINAL 的 final_coverage，每机制只必填 mechanism_ref、
status（deep/screened_pending/ruled_out/unexplored）、reason，理由合并深度依据和未答问题；
limitations 保留输入与资源缺口。准确覆盖完整冻结机制集合，不从候选或调用成功推断科学深度，
也不授权新实验。当前协议 28 不支持更早协议的隐式读取或迁移。

广度预算应依据普通探索可用量及 `work/budget-status.json`；当前 protected FINAL 模式按 provider 已报告消费计算 token 余额，未知账单继续列为未知，不估算扣费，也不扣旧收口预留。旧预算内收口格式不受支持。真实执行未知仍须先按原调用核对。初始调用机会不预留实际资源，首次昂贵调用仍可能耗尽余额；使用已有执行配置 `max_seconds` 和 W 阶段时间/token 目标约束，控制不自动分配广度比例或强制科学机制数。
初始/额外机会只保护初筛公平，不是所有后续调用的总额度。相同 H 范围与既有约束允许时，A 可用 `continue_w` 请求下一有限阶段；问题、机制或约束需要变化时才修订 H，明确次数限制和真实执行准入继续适用。
H 只重写需要变更的 commitment；遗漏保留原承诺及已采纳 A 处置。修订同一 `existing_ref`
后仍可 repair 旧 W 的精确观测：稳定机制 ref 与当前新 H/A 授权分开绑定，旧 W/call/accepted/
terminal 仍以原 H seal 校验。来源须为精确直接 W，或通过已有 `implementation_selection` 选定的
原 accepted 输入；多个来源须精确选择，不能按 label、相同输出字节或最近对象推断。共享
归档的共用物理路径也不能证明逻辑输入身份，此类输出不能用于 repair 来源消歧。已知进程成功
也可能需科学修复；unknown、来源模糊、原件缺失/改变及 repair 次数不足仍拒绝。结构性来源问题
在作者回执提前诊断，Worker 仅见科学 ref、原 W/call 与当前授权步骤，不见私有控制绑定或路径。
旧筛选资格不继承，原投入不退款。H scope 未变时，A 可在 review 后通过 `continue_w` 加显式
局部 focus 请求下一有限 W stage，无需为了续配而重述 H；各 stage 原消费与总额度继续累计。

普通回答只必填 summary、scientific_result、limitations，H/A 另填 next_action、route_reason，
W 另填 stage_intent。条件广度内容按上文最小合同，其他科学内容开放，额外说明不套嵌套校验。
W 的真实缺交付仍走有界续工；不要求重复 finding 表。A 明确 screened 仍须同 H 版本的
准确已知观察。可选进展声明省略不制造进展，也不累计缺声明的 unknown 暂停。
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
    code/                        # W 新执行代码；由宿主固定选定版本
    work/                        # 本节点临时材料，不交接
    artifacts/                   # 必要交付成果
    experiments/<name>/
      work/                      # 独立运行 cwd 内的临时文件，不交接
      artifacts/                 # 子实验必要成果
      conclusion.md              # 可选的简短科学观察
    conclusion.json              # 控制层保存的最终科学回答
native-evidence/<node>/           # 私有原生轨迹、schema、引用与封装
```

角色使用原生文件工具。模型只做科学工作、选择必要成果，最终直接回答简短科学 JSON。
启动请求给出必填字段、类型和动作选项，普通任务文件不重复列字段清单；无需读 schema、
另存最终回答、制作索引、计算哈希或打包。
子实验不是额外 Agent，结论可以是 Markdown 或 JSON；无需逐次填写机械表单。

控制层自动采集节点 `artifacts/` 及子实验成果和可选结论，规范为直接前驱成果目录。
子实验模型不需要再复制到顶层。`work/`、输入和祖先材料不进入成果采集。
受管调用的 `experiments/<call_id>/` 发布目录由控制层独占；W 直接引用留存原件，新的比较报告和脚本放在该目录之外。
作者若在同一发布路径重复打包，控制层仅对冲突文件比较大小和逐块内容；完全相同时复用受管原件，封存时尽力删除冗余副本，并保留科学链接。
内容不同或占用其它受管路径时仍交回作者修正；不扫描其它路径寻找副本，不计算内容哈希，不重跑或重新提交原实验。
研究额度耗尽阻止新增研究；已完成的有效输出仍先封存，之后按现有路线结束或转入 FINAL，不为机械去重额外拉起模型，也不重置已消耗额度。
需要后继使用的报告和可复用诊断脚本也须放入 `artifacts/`，在结论里提到临时路径不等于交付。
默认交接包括全局文件、直接前驱结论、关键成果直达入口和成果目录，启动材料不逐项展开文件。
角色在科学结论里自然使用 `[比较结果](artifacts/comparison.csv)` 这类相对 Markdown 链接；
控制层验证它指向本节点已采集的成果，并解析为后继节点的实际路径。无需另填附件清单。
普通文字路径不自动推断；外部链接与前驱材料引用保持科学原文，不冒充本节点新成果。
封装、路径校验、来源记录、输出 schema 和封存回执只由控制层维护；科学内容不自动计算哈希。
旧科学结论通过 `history` 给出的只读分页入口在当前节点按需读取，默认只投递不超过 4096 字节的历史简报。
旧实现保留在原拥有节点。普通 A/H 的当前材料引用可沿转交链及准确 E 往返保留；
`implementation_selection:null` 保留当前集合，`[]` 清空，明确 ID 列表替换。
空集合不因路由还列有旧父节点而恢复。W 发布新结果后自动集合刷新，后续仍需使用的旧实现由 W
显式选择保留。选项列出 ID 和实际输入路径；compile 使用文件路径，ID 用于选择。
W 的 null 仅保留其原先明确选择的旧输入，不表示转交所有收到的材料。
私有任务及转交记录只保存已有产物清单的引用和所选 ID，不逐节点复制完整文件描述。
操作入口一次加载本次所选的生产者清单，再把事实传给消费者；普通控制记录读取不隐式展开材料或遍历祖先。
普通历史引用不自动挂载历史所有附件。角色不计算身份、不复制旧模型，也不继承祖先整包。

新实例的成果采集默认不设单文件或单节点累计存储大小限制（冻结值为 null）。操作者可显式传入正整数 `--max-output-item-bytes`、`--max-output-total-bytes` 限制采集和直接交接；已有实例冻结的整数配额仍生效，不自动扩大。
显式配额与模型紧凑视图限制分别执行，也不改变 token、时间或执行次数预算。声明目录之外的文件不自动获得证据资格。
若模型正常退出且科学输出已经固定，而控制层封存失败，修复交付后 `run` 可只重试封存。
不修改原科学结论，不重复执行模型；结果未知、模型失败和用量缺失仍拒绝自动重放。

## 路由与暂停

普通阶段建议沿现有输入选择保留公开比较方案、评价脚本和完整流程的准确引用，比较条件变化须说明限制。
有价值且可负担时，A 可在普通阶段请求少量竞争流程的共同条件比较；这不是配额，也不强制候选入池。
FINAL 仅使用已有证据收口；缺少比较或全部候选被拒绝不会新增科学完整性门槛。

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
再次 `run` 按任务引用、系统启动标识及进程启动身份核对同一 runner，并等待原完成通知，再封存和路由。
`status` 区分 `prepared / running / native_not_submitted / native_outcome_unsealed / seal_present_unverified / unknown`；
封存存在仍需 `run` 验证。原生成功但封存失败只重试封存；执行身份丢失、失败或缺量仍不自动重放。
实际模型提交前再查预算；已确证未提交时记录 `native_not_submitted` 和零用量。
控制层保留该节点及原意图；预算修订或重试不自动删除这些记录。普通 reentry 或 automatic recovery 已接受意图、尚无任何执行回执时，恢复沿用同一 attempt；已有执行回执则核对原调用，不另起替代调用。
暂停只阻止新节点，恢复时也允许原已接受节点完成和封存。
科学角色的 `wait` 仍表示停止等待。W 已接受且同一 external execution 明确 running 时，控制在科学封存前保留原 checkpoint 并进入 `waiting`；默认 `run` 自动监控原 call，终态后 reconcile 并在合格的原会话中续接 W 消费原件，再封存。等待不调用模型、不增加 native dispatch 或 `advance_seconds`，但真实执行的 `waiting_seconds` 仍消耗原授权总运行时限；旧 elapsed 口径继续按总经过时间计量。暂停阻止唤醒的新 native turn，允许核对原终态；控制进程同 boot 重启后从原 waiting 恢复，未知提交不重放。续接仍受原预算/有限次数与冻结 launcher/session 能力约束；能力不足保留 checkpoint 并报告 unavailable。

旧 W 已 sealed unknown 的事实保持不变。`scid-research execution-recover --root /absolute/path/instance --step N` 可对同 call 追加已验证终态；后继新 job 冻结其恢复事实前缀与材料，已准备 job 仍只读原输入。该动作不把旧科学结论改为 screened，也不重计机会、grant 或原 slice。

新建实例的 `--max-seconds` 是推进启用时长上限：控制推进与原生执行重叠只计一次。
暂停请求生效前已接受的工作和控制器断连后的原执行继续计时；无活动且进入暂停时停止消耗推进额度。
身份或终态无法核实时保守计时，后续核实从当前时刻停表，不回填旧终态时间返还消费。
若原回执确认模型未启动、没有 native 子进程或活动且原执行已结束，宿主准备故障退出后停止推进计时；原意图、已消费额度和未知账单保留，不自动重提。仍在运行、效果未知或清理未知的原调用继续按原规则计时。
`status.budget` 分别报告 `advance_seconds / waiting_seconds / paused_seconds / elapsed_seconds`，并显示有效上限、
`consumed_seconds / seconds_remaining` 和计时口径。`status` 只读，不追加计时事件；启动时钟倒退时拒绝推进和修订。
新计时事实绑定系统启动标识与 `CLOCK_BOOTTIME`，同次启动的开放区间按该单调时钟计量；
新 active 实例的 UTC 仅是展示时间，调速或回拨不停止计量，也不返还已按启动时钟消费的额度；账本按序号及同启动的单调时间排序。
CLI `status` 对已记录暂停终点的 active 实例只汇总已闭合消费，不采样当前时钟；尚未闭合的
暂停尾段及完整经过时间报告为未知，不影响已确定的推进与等待消费。开放运行区间和旧 elapsed
实例仍按原计时口径查询。已闭合消费可读，不替代新执行和实际预算写入对当前启动锚的校验。
active 实例跨系统重启时，保留已确认消费，开放区间依据旧启动最后已记录的事实和新启动的
`CLOCK_BOOTTIME` 续计，不用跨启动的 UTC 差计入系统停机时间。旧启动的已记录事实包括
原 native intent 保存的 `advance_seconds` 累计下界；不会因它高于最后一条 clock 记录而丢失
已确认消费。旧启动最后记录至关机前的尾段没有准确关机时刻，无法精确恢复且未计入；
新启动至续接前的空闲仍计入。预算快照显式报告这一限制，不能把余额理解为精确扣除了
所有停机时间。已有未采用此口径的闭合区间保留原计量，不追溯退款。跨启动本身不再阻断预算读取、推进或实际预算修订；
同启动时钟倒退仍拒绝，须先核对原时钟，不自动退款。配置页读取账本总上限、排队保存下一
节点配置不计算实时消费；保存不等于恢复或执行许可。工作台对仍无法核对的时间消费及余额
显示“无法核实”，不以零值补齐，也不据此判断时间预算耗尽；tokens、预算上限和已保存结果仍可查看。
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
时间与修订共用 `control-ledger/` 追加账本，记录序号、前记录引用、冻结 settings 引用、前后上限、时间和授权说明。
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

框架或工具升级不要求预先保留旧源码或填写实现 manifest。各 attempt 的已报告消费分别累计；缺失计量保持 unknown，不估算收费。后继入口追加 `host-recovery/author-closeout/NNN-successor.json`，绑定旧 permit 及失败 attempt 1 的任务、调用、用量和进程原件；不改旧 permit、settings、失败 outcome 或科学原件。追加授权记录本身不是执行：只有当前已报告累计 token、时间及控制步数满足本次作者授权门槛，才接受唯一 attempt 2；protected FINAL 的共享交付预留为零。接受后重复调用只收集它，不自动重试，也不授权 attempt 3；新 attempt 2 若真实执行或清理仍 unknown，仍须按原身份核对并阻断后继封存和交付；单纯计量覆盖未知只记录 unknown，不估算收费或单独阻断。

后继额度来自原有累计预算，不重置消费、不自动续额。每个失败 attempt 与新 turn 的已报告消费均计入原有累计账；操作者独立保留的评价额度及当前交付预留不得挪作新 turn。时间上限及已消费时间不因接续增加。新 W 先读当前 attempt 的紧凑机械事实索引，按需取完整科学原件；索引不替作者排名、判定方法多样性或推导审计平台期，科学结论仍由真实 W 编写。原实验、模型及旧 native attempt 只读；当前 attempt、必要 artifacts 与新上下文权限按原隔离启动器保留。这只授权作者完成既有工作，不授权新训练，受管入口仍拒绝 run、compile、submit、query、collect、cancel；同 uid 原生代码仍是信任的软边界，并非任意计算的强隔离保证。

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
当前协议 28 的 `--node-budget-mode` 仅接受 `soft`；API prepare 同样拒绝 hard。旧协议实例不受支持，不提供兼容读取或迁移。

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
A/H 在选择后续路线前、W 在大量读取或实现代码前，结合该既有观测考虑准备、后继角色和实验的成本，优先复用证据索引。余量不足时可报告未满足目标并选择收口；提示不改变原目标、权限或软预算，也不新增最低余额、保留比例或对实验成本的硬性估计。
续接不清除先前消费。
soft 节点目标仍是建议；启动前核对共享余额，不足则进入 FINAL。已启动的 H/W/A/E 不因共享 token 或 activeSeconds 阈值被运行中强杀；当前节点完成后超额完整记账，余额不足时不再启动下一普通节点。任务单次执行的硬限仍保留。

新受管执行仍须通过原共享窗口准入。在尚未接受调用、且可确认没有外部准备效果时，预算拒绝与正式执行审查分开返回：

| 返回状态 | `reason` | 含义 |
| --- | --- | --- |
| `budget_exhausted` | `shared_time_exhausted` | 原共享执行时间窗已截止。 |
| `budget_exhausted` | `shared_budget_exhausted` | 原共享额度或预留边界已到达，不据此猜测具体哪一维耗尽。 |
| `budget_unavailable` | `shared_usage_unknown` | 共享用量存在阻断性的未知，不能解释为零或已耗尽。 |

这些反馈带 `accepted=false`，`handoff.action=finalize_response` 要求 W 保留代码、设计和证据，正常返回科学 JSON，明确本次未执行及未满足目标；随后由控制层应用原预算和收口规则。它不进入 pending call 的等待或唤醒，不要求补审查，也不授权重试、增额或成功结论。正式审查缺失仍返回 `review_required`。已有 accepted 调用或外部准备效果不明时继续保留原身份与 unknown，按原调用核对。

author closeout 使用本次已验证 permit 的 token 软目标与有限时间，
不会把原 W 上限当作 author 额度；缺少本次额度时明确标记当前边界不可用。部分只读 author 视图不暴露动态更新，
以其明确的静态 token 软目标和有限时间为准。提示只能在文件可见且被读取时生效，不是授权、计量原件或科学证据。
每次请求已报告的全部 input 都计入用量，cached input 已包含在 input 中；未知计量保留覆盖未知，不补造零用量。
同 session 续接的终态可能报告整个 session 的累计用量；只有原 baseline、实时增量和终态三个计数逐项精确相加时，才按本 attempt 的增量消费，避免重复计入先前请求。差值在本次调用记录落盘时确定，旧错误累计回执不再派生重计量。原 usage、outcome 和 seal 不改写，时间、步数及未知覆盖不退款或清除。
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
调用的 token 参考目标与有限时长；不是从剩余额度另批预算。缺少该必需冻结策略的实例明确拒绝。

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
注册功能插件与 CLI 实例 UI/端口已接入，旧入口已退役。普通 checkpoint 与原调用核对、恢复仍保留；独立冷包恢复已退役，跨宿主完整灾备仍不支持。未知原生结果须核对原调用，不能自动重放。

## 可选候选事实观察

`prepare --history-observer-config /绝对路径/observer.json` 可冻结一个可信宿主只读 adapter。
Cov 观察器随独立 `cov_execution` 插件安装，下面为源码布局示例；安装环境应使用该插件
`cov_execution.observer.__file__` 指向的精确原件：

```json
{"adapter":"/绝对路径/plugins/cov_execution/cov_execution/observer.py","config":{"runs":"runs","evaluation_key":"cov-public-val-score-d78f5eb-v1"}}
```

默认不配置 adapter；历史仍维护 H/W/A/E 的原摘要、限制、H 范围与科学结论页。
通用控制层不识别 cov 字段、不读取隐藏评分。adapter 仅观察原件，不执行模型、反序列化模型或提交。
候选页按首次观察顺序展示候选事实、当前候选（无事实为 null）、A 推荐和实际提交观察；同分、未评分、失败和未知候选均保留，紧凑预览机械省略时标明数量并链接完整索引。
A 可在新协议最终科学 JSON 可选填写 `candidate_recommendation`（已列候选 ID；未知可省略或 null）。
推荐不改变提交；公开 harness 的实际提交观察也不证明外部评测接收或隐藏得分。
代理记录提供原科学结论页以回查适用条件与盲区，不能当成目标最优或模型已留存证明。

观察调用上限仍为 5 秒；完整输入、输出、候选/上下文清单及内部传输不再设置框架固定字节或累计项数上限。超时或格式问题保留明确不可用事实，不冒充完整观察。
恢复核对配置、封存原件的路径、producer 与声明大小，不提供内容篡改检测。公开台账等可变观察证据首次独立复制为宿主 UUID 对象；同一路径的设备、inode、大小和 mtime 未变时，核对已留存原件后复用，变化后才保存新版本，保留此前字节。模型产物直接引用准确的受管输出原件，只有 live 路径而无留存原件时保持不可用。上述保留不按内容去重或计算哈希，不设原单件 32MiB、总对象 128MiB 上限；既有 `digest_only` 或 `unavailable` 记录不因新规则被追认成可回读原件。
历史默认投递紧凑简报（最多 4096 字节），按需读取的历史分页每页最多 64000 字节；可选 overview 超限时明确省略，完整观察和索引仍保留。归档私有原件不因此全部挂给科学角色；读取仍按冻结来源与权限解析。
最后一个节点也更新 `host-history/overview.json` 投影；投影删失可由封存科学原件和观察快照重建，观察快照不是可删除缓存。


## 可选交付策略与最终收口

新实例可在 `prepare` 同时冻结 `--node-budget-policy` 和 `--delivery-policy`。
交付策略包含 `closeout_a` 的 token/秒上限、`inflight_tokens`、`cleanup_seconds`、
`submit_seconds`、`confirm_seconds` 和 `adapter`；各值由操作者明确配置，没有生产默认预算。
普通 A 明确 `finish` 即结束本轮并拒绝交付；明确 `deliver` 则只消费该 A 原封存的精确候选授权，不强制再起 FINAL A。启用 `selection_policy` 时，普通 A 原任务已经冻结并展示完整候选比较目录，交付核对该原目录及资格。失败或缺少资格保留未决状态，不自动改选、启动新 A 或生成 FINAL。只有预算阻断下一动作或显式 closeout 才按上文进入 FINAL；预算阻断先保留原待执行路线，显式增额并通过准入后可恢复。角色份额按当前账本有效总 token 授权计算；增额不抹去此前消费。当前格式要求最终 A 完成策略 v1，不预扣旧式收口预留。

旧协议、hard 或缺少当前必需策略的实例明确拒绝，不提供兼容读取或迁移。soft 提示不能保证在途节点及时返回；真实执行的有限时间与权限边界仍有效。

适配器是独立的领域接口。核心冻结其代码、启动器、输入原件及读取范围，不解释领域台账。
收口先冻结完整候选目录，`science-task.json` 给出目录文件；启用分类提名时默认只预览 W 提名并注明其余数量，完整目录保留全部精确候选 ID 和来源节点，不按紧凑摘要容量截断。控制层仅为 A 明确选择的操作解析并冻结原件；候选目录本身不启动 smoke、审计或自动排除候选。可用性与审计资格分别展示；精确原件的审计结果、工件匹配和适用范围沿历史传递，不额外制造未知泄漏待办。A 选择不等于审计通过。原生 `exp submit` 保留公共命令，但只使用 run 声明的路径，不解析历史跨节点原件；受管 wrapper 的 submit/help 会提示历史交付使用 A `deliver`，不会阻断或改写命令。
普通 A 已封存的 `deliver` 可直接进入控制交付；预算准入拒绝时进入既有 FINAL。预算耗尽进入的 FINAL 周期保留独立的一次最终 A；普通 A 的明确结束或交付不触发该周期。显式 `selection_policy` 版本 2 仅含 `{"version":2}`，冻结全部候选事实目录，再由精确 closeout A 负责比较。真实 audit/deliver 的 `candidate_recommendation` 必须命名目录中的精确可用候选（或绑定到同一已登记成果的实现引用）；普通科学建议的坏引用保留原文并诊断，不能成为执行选择。`candidate_comparison` 是可选科学说明，不以 `keep`、非空 `rationale`、`key_risk` 或 `evidence_steps` 格式决定提交准入；A 在 `scientific_result` 说明选择与证据。null 不授权交付。旧 `shortlist_k`、`evaluation_key`、`smoke_seconds` 配置拒绝接受。交付策略要求的公开审计必须完整通过才能更新实际提交；失败、跳过或未知不能冒充审计通过。未提供可选 audit/smoke 能力保持中性，不绕过已冻结的必需审计策略。普通研究中的审计、作者补填与精确工程修复按当前冻结策略及准入执行。FINAL 只接受一个最终 A，并消费其明确的现有候选选择，不追加科学角色或实验。A 必须逐项对照原目标和授权范围，区分直接测量、推断与假设、目标指标与代理及敏感性检查；按同一目标准则解释相关候选、冲突证据、不确定性和缺失输入。预算耗尽、finish、审计资格与交付均不证明未测量的目标已经完成。正常审计往返及提交前完整审计明确拒绝后的改选只消耗原总预算，不消耗错误补正次数。完整审计拒绝的同一候选工件不能再次提交；工程故障、未运行和未分类回执不冒充科学拒绝。未知操作绝不改选或重放。
未知提交始终只核对同一原操作，绝不重复提交；FINAL 的一次同操作确认不受共享余额否决。
A 在 scientific_result 说明候选准则、修订、证据及代价，selection_explanation 可选，
不再有独立嵌套补填门槛。准确候选身份、原件、审计资格与交付权限仍必须核对。
缺真正必填内容时，输出补正保留同一任务、累计用量和 FINAL 完成保护。

`adapter: null` 时 `submit_seconds` 和 `confirm_seconds` 必须为零，不授权外部交付；FINAL 仍由一次 A 给最终结论与未提交原因。

```bash
scid-research closeout --root /绝对路径/实例
scid-research closeout --root /绝对路径/实例 --export-only
```

已知硬停止留下未封存节点时，收口绑定停止前最后一个连续封存前缀；失败原件保留。
`--export-only` 不调用模型或外部适配器；它可记录本地收口事实，不是文件系统零写入命令。
原暂停、未知执行、清理与完整性检查继续生效；缺失用量保留未知账，单纯计费未知不估算扣费或单独阻止推进。预算修订仍须通过原 `budget-amend` 明确授权。FINAL 仅在最新修订严格提高阈值且完整准入足够、原效果已核对时，按保存的 pending normal 路由恢复。
没有冻结交付策略时不授权外部提交；不能原地追加适配器或改写原调用。

`delivery_confirmed` 仅表示 A 选定的确切工件已在声明目标确认交付；通用目录 adapter 只确认本地目录，不表示外部服务接收。`goal_completion` 仍为 `not_asserted`。
`exported_unsubmitted`、`closeout_unresolved`、`delivery_failed`、`delivery_unknown` 分别保留未提交、
未决、失败和未知，不等同科研任务完成。FINAL 不以共享余额不足拒绝最终 A，实际未提交仍给明确原因，不伪装成操作者 export_only。暂时来源准备失败返回 `closeout_preparation_unavailable`，保留失败原件，重复 `run/closeout` 可在原预算内重试准备并复用已封存 A；它不固化为操作者 `export_only`。清理未知继续阻止任何新执行。`status` 由相同原件重建状态和最新收口 A 推荐；此前实际提交观察与当前确认结果仍分开。真实科学效果须另行验收。

当前协议为 **26**，要求 worker/lineage view 1、soft 节点设置及最终 A 策略 v1。旧格式在解释任务前拒绝，不提供兼容读取、续接或迁移；新实例须重新 `prepare`。现存目录不由升级自动修改或删除。

代码审阅、工程检查及交付按当前准确代码或科学 packet 绑定消费原件；缺少声明输入、所需能力或资格时明确不可用，不推断未声明依赖，也不将工程检查视为科学审计通过。

### 审计工程故障与科学拒绝

进程退出 0、`leakage_flag=false` 都不足以证明审计通过。适配器核对精确候选、有限审计数值、完整执行回执及审计模型字节：`audited` 表示通过，`rejected` 才表示完整审计明确不合格；脚本、依赖、训练、进程或资源故障为 `engineering_error`，缺回执、无法可靠归因或原件身份不一致为 `unclassified`。后二者均不授予科学资格，也不自动要求换候选。

控制层先允许一次 scratch 审计工程恢复：重新从封存原件准备输入，写入独立不可变 intent/outcome，使用原共享余额与原单次上限并计步。它只重做未写真实交付目标的审计，不能重放 promote、confirm 或未知提交。真实执行、提交或进程清理未知仍阻止新效果及重放；仅用量未知不否决 FINAL 的最后 A 与已有封存 W 的原授权交付。重复 `run` 不重放已接受恢复。

确认是实现或未声明依赖问题时，可在原额度内建立一个精确 W repair，绑定故障回执、原候选、原 W 和封存输入。相同原候选 lineage 最多一个此类 W，不提高总额度或原次数上限。注册工具只允许对绑定 parent 运行一次新候选；不接受无 parent、新计划、换 parent 或重复调用。W 报告实现修复或需要科学改动，A 重新判断并授权新候选，旧授权不覆盖新字节。已封存结论不可覆写；只有未封存且符合既有恢复条件的任务可用 `worker-resume` 在原 session 续接。工件无法定位时不能重建并冒充历史原件。

选择策略在修复后保留旧目录原件，并按 repair W step 冻结新一代比较。原故障实现仍展示；A 必须根据新证据明确选择并说明理由。状态从当前代相同 intent/outcome 投影，工程故障和未分类早停不会显示成等待执行。

这套自动恢复针对控制适配器和注册工具的语义回执，不解析任意 shell 输出，也不把原生 `exp submit` 退出成功当作资格。冻结运行时/公共 harness 无法恢复的工程问题会明确停机；不会热改安装或递归捕获任意依赖。预算、输入容量不足或修复次数耗尽分别保留明确状态，不能静默增额或改派无限任务。

### 恢复能力的区别

控制器退出后可以重接仍活着的原 Worker；Worker 成功结束但未封存时，可以补封存原结果。
已封存节点的查验已经完成。普通交接、加载和恢复信任其保存的结果、观察与控制决定，
不全量重查历史文件，也不重跑历史科学门槛。路由和搜索游标沿用已接受的决定；派生游标
缺失时读取保存的事实重建，不重新授予资格。新增查验只针对当前节点和本次实际消费的
输入（包括恢复产物），不随历史节点数增加。历史导航及只读挂载沿用原文件绑定。
来源限制沿用封存的输入、输出事实，不沿祖先重查运行结果；缺失保持未知，受限来源不会因继承而变为无约束。
普通任务在系统重启时仍运行且没有终态，`run` / `continue` 会沿原任务、session、home
和工作目录重新接入，保留原生权限、已消费额度及原调用 ID。active 实例跨 boot 按启动标识
与 `CLOCK_BOOTTIME` 续计，不用跨 boot UTC 差计入停机时间；旧 elapsed 实例沿用总经过
时间口径。active 口径下，旧启动最后记录至关机前的未测量尾段未计入，新启动至续接前
的空闲仍计入。
不因 boot 变化拒绝；重入不消耗新科学节点或错误补正次数。
旧尝试保留中断记录，不伪造完成；恢复后的真实结果仍须正常封存。冻结的输出补填策略
不阻止原完整任务重入，但不会把已经受限的输出修复、作者收尾或结果唤醒变成普通探索。
本节的硬停收口转交 A，并不是续跑已停止的 Worker。原线程续接见下节；
本机 CLI 对合成 429/503 的精确同线程失败续接已核对，真实服务商的推理恢复仍未验证。
任意本地 shell 仍是软隔离；
框架管理的外部执行应按原执行身份恢复，不重发不确定提交。

## 可选的 Worker 原线程续接

标准单命令 `codex` 的 CLI `prepare` 在未传 `--worker-resume-policy` 且未请求
`--worker-context` 时，冻结最多两次同任务、同 session 的输出补填或原受管执行唤醒：
`output_repair: true`、`output_only: true`、`execution_wake: true`。
补填修正已确认完成的必需返回、实际动作消费的作者引用及明确诊断的交付布局冲突；
封存与补填共用消费校验，字段错误不另开 H/A 科学节点。原作者可删冗余副本或整理
自己的文件位置，受管原件直接引用、无需复制。起始 BOM 和完整唯一 JSON 围栏由统一科学
解析器处理，原始字节保留；散文夹杂或多个对象仍补正。窄唤醒只解释同一原受管调用的已核验终态并完成返回，
这两项窄权限本身不授权新实验、训练、失败任务续跑、`WorkRepairRequired` 科学修复或 W fragment。
新标准配置另有上文的独立 `automatic_recovery` 许可，可在原边界内续接提前停止、临时故障
和 completed 但科学交付不足的原任务；不借 `output_only` 扩权。
原 W 的末次 run/compile 若有明确 caller_error、accepted=false，且该拒绝造成工作未完成，
控制层沿原 task/session 有界拉回具体修正，覆盖 improve 和无 search 普通工作。后续已接受的
修正请求不再触发补正；未知命令的原参数交回作者自行修正，不猜测命令意图。准确的未接受
机械拒绝在原有限额度内优先修正；没有这种拒绝的科学 decision/blocked 仍交 A。已接受未知
调用只核对原调用。
新 Local 调用若以 Python `PermissionError`（EACCES）终止，回执会给出错误类别和已保留 stderr 的相对路径。
W 在当前回合的末次调用遇到这类错误后仍请求 review 时，已有自动续接许可可将问题有界交回同一 W；复用已产生的结果，不自动重跑原实验。
候选为空不是错误，作者可判断所有模型均不适用，或明确作出 decision/blocked；后续回合没有新文件错误时，不会因旧失败反复续接。原权限、预算和次数限制保持不变。
FINAL 与 closeout 沿同一 native collector 接续 interrupted 或已接受未启动的 attempt；
宿主明确的提交前预算拒绝转入 FINAL 时保留完整原 attempt 链与累计消费。
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
明确的 `caller_error` 且 `accepted=false` 返回 `correct_request`，要求修正指出的参数或输入后提交修正请求，同时保留任何独立已接受原调用。Local 不消费 `--parent-plan` 和 `--change-intent`，忽略这些说明并保留未建立父模型关系的事实；真正消费父模型的执行器仍检查准确引用。这次未接受请求没有需要查找的原调用。单凭缺少 call ID 不能证明未接受，其余未决事实仍按原调用核对。
对既有调用查询、收集或取消失败，不会抹去已核验的原执行终态。失败回执的 `status` 描述
本次操作，存在的 `state` 来自已核验原终态。除原件完整性错误外，明确发生在该操作开始前
的加载、资源或其他预检失败均报不可用，审查要求单独保留；宿主配置问题不授权 W 修改冻结
配置。真正提交后失联仍保留原 ID 与 unknown。后端异常原文只留宿主诊断，公开回执给
安全的操作、错误类别和下一步；客户端保留宿主 `handoff`。
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
合成进程和零模型打包检查仅验证所选本地输运及拒绝路径。本机实际 CLI 对合成 429/503
已保留完整 JSON，并在同一空 home 用原 ID 续接至同一 loopback provider；永久 quota
不续接。真实服务商的同线程推理恢复、增量用量与权限尚未验证。这些本地检查不能授予
实际 native 恢复资格，也不证明任意科学工作能够
安全续接。具体运行仍须保留原 attempt 记录、失败及未决限制，按该运行的实际证据判断。
