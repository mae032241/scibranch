# Research configuration and agent backends / 研究配置与 Agent 后端

`scid-research` is the application core. The optional instance webpage uses the
same preparation defaults, frozen settings, controller, ledgers and read views;
research does not require a running webpage. This remains an engineering prototype:
an adapter or successful command does not qualify a model, browser or solver.

`scid-research` 是应用核心；实例网页是可选便利入口，复用相同准备默认值、冻结设置、
控制程序、账本和读取视图。研究不依赖网页常驻。当前仍为工程原型，接入 adapter 或命令成功
不授予模型、浏览器或 solver 资格。

## Prepare once / 一次准备

The independent workspace webpage starts with a blank structured form: enter the
task, public material directory, new instance directory and finite budgets. Role,
permission, execution, delivery and policy groups are optional advanced settings. Role models and stage targets
share a compact role table; related limits use grouped fields with units and expandable
help. Role budget shares and resume options use labelled controls. Plugin
structures retain an expandable typed editor for lossless import/edit/export. Opening
More research settings shows separately collapsible categories. Expanding a category
reveals compact grouped fields, including role shares and resume parameters. Plugin
entries use compact key/type/value rows. No
configuration file is required.
Check the complete effective preview, explicitly create the instance, then start
research separately. Editing the form after checking requires a new preview.
The form also imports and exports `[project]`, `skills.sets`, named Workers and
role `worker`/`skill_set` references. Declaring an optional set does not select it;
an explicit role or Worker selection enables use. Existing-instance edits retain
the original project and set inventory; operational revisions cannot replace them.

独立工作台从空白结构表单开始，可直接输入任务原文、公开材料目录、新实例目录和有限预算。
角色、权限、执行、交付与合同策略按用途分组；角色模型与阶段目标合并为紧凑的角色表，
相关数值并排排列并直接标明单位，详细说明按需展开。角色预算份额及恢复选项使用专用
控件。插件结构保留可展开的类型编辑器，导入和编辑不会
丢失未知值。“更多研究配置”内各分类可独立折叠，展开后使用紧凑分组表单，包括角色份额与恢复参数；
插件扩展按键名、类型和值紧凑排列。无需先写 TOML/JSON，也不自动读取工作区已有任务或配置。检查完整有效
预览后明确创建，再单独开始研究；修改参数后须重新预览。
表单同样支持 `[project]`、`skills.sets`、命名 Worker 与角色 `worker`/`skill_set` 引用的
导入及导出。声明可选集合本身不启用，须由角色或 Worker 显式选择。已有实例编辑保留
原工程和集合清单，不能通过运行配置修订替换。

TOML is an optional import/export and explicit save format. Imports populate the
same controls. Save/update writes only the explicitly selected configuration file;
creating an instance uses the current form without rewriting an imported original.
Export canonicalizes declared paths so saving in another directory preserves their
meaning. CLI users may instead copy [research.example.toml](../research.example.toml)
beside the objective and public inputs, edit its paths and finite budgets, then use:

将示例复制到目标与公开输入附近，填写路径及有限预算后运行：

```sh
scid-research config-check --config /absolute/project/research.toml
scid-research config-inspect --config /absolute/project/research.toml
scid-research prepare --config /absolute/project/research.toml
scid-research config-inspect --root /absolute/project/research-instance
scid-research start --root /absolute/project/research-instance
scid-research status --root /absolute/project/research-instance
scid-research operations --root /absolute/project/research-instance
```

`config-check` validates without preparing an instance or submitting model work.
`config-inspect --config` shows the effective declaration; `--root` defaults to
`--view frozen`, showing that exact instance's base settings. `--root ... --view current`
shows the base identity, active revision and configuration, and any separate pending
revision and configuration. Shared budget ceilings reflect audited amendments; pending
changes apply only at the next new node. Inspection binds no revision and launches no work. Inspect output is host-private and may contain paths;
keep it out of scientific evidence. `prepare --config` cannot mix preparation flags.
The explicit flag form in the [CLI guide](ROOTLESS_CLI.zh-CN.md) uses the same
defaulting and validation path. Editing TOML affects future instances only. Existing
instances retain their original settings and identities. Use the instance's
configuration page for operational changes from the next new node; never edit
`settings.json` manually. Cumulative budget amendments retain their ledger and gates.

`config-check` 只校验，不建实例、不提交模型工作。`config-inspect --config` 展示生效声明，
`--root` 默认 `--view frozen`，展示精确实例的基础冻结设置。`--root ... --view current`
分别展示基础身份、已生效 revision/配置及待生效 revision/配置；累计预算反映已审计修订，
待生效项仅用于下一新节点。在途节点仍用原绑定；查看不绑定修订、不追加控制记录或启动工作。
输出属宿主私有内容，可能含路径，不应作为科学证据。
`prepare --config` 不能混入准备参数；CLI 显式参数方式走同一默认值与校验路径。
修改 TOML 只影响未来新实例；已有实例通过当前研究配置页调整后续节点，不能手改
`settings.json`。累计预算修订沿用原账本和准入条件，也可使用 `budget-amend`。

Reading the configuration and queuing edits use verified ledger ceilings, without
calculating current time consumption. They remain available when the current clock
cannot verify an open interval. Applying changes to a new node, actual budget
amendments and execution still require the existing clock and budget checks;
saving a configuration does not recover the clock or reset consumption.

读取配置与排队保存修改使用经过校验的账本总上限，不计算当前时间消费；即使当前时钟
无法核对未闭合区间，这些操作仍可使用。新节点应用配置、实际预算修订和执行仍须通过
原有时钟与预算校验；保存配置不会恢复时钟或重置消费。

## Declaration fields / 声明字段

Unknown tables and fields are rejected. Except for the exact absolute
`project.base_root`, paths listed below resolve relative to the TOML file,
including `~` expansion; changing the caller's working directory does not
change them. Only a path-like `agent.launcher[0]` is resolved this way; remaining
argv elements are literal native arguments, so use absolute paths for their inputs.

未知表和字段会拒绝。除要求准确绝对路径的 `project.base_root` 外，下列路径相对 TOML
所在目录解析，也支持 `~`。launcher 只有带路径的
首项如此解析，后续 argv 是原生参数，不猜测其路径含义，所需文件宜用绝对路径。

| Table / 表 | Supported fields / 字段与含义 |
| --- | --- |
| top level / 顶层 | `version = 1` (default / 默认 1) |
| `research` | Required path / 必填路径: `root`, `public_root`; exactly one / 二选一: `objective` (original path / 原件路径), `objective_text` (inline original text / 任务原文); optional / 可选: `user_updates` (path array / 路径数组), `source_restrictions` (existing declaration objects; each `source` is a path / 原声明数组，`source` 为路径) |
| `project` | Optional exact Git worktree `base_root`; required nonempty relative directory array `editable`; optional arrays `scratch`, `outputs` (default empty). See [declared projects](#declared-projects--声明工程). / 可选工程声明：准确 Git 工作树根 `base_root`、非空相对目录数组 `editable`，以及默认空数组的 `scratch`、`outputs`。 |
| `agent` | `backend` (`codex` or `protocol`), `launcher` (argv array), `model`, `reasoning_effort`, `service_tier`, `roles`, `native` |
| `agent.roles.H`, `.W`, `.A`, `.E` | Optional `model`, `reasoning_effort`, `service_tier`; omitted roles/fields inherit the global profile / 省略项继承全局配置 |
| `permissions` | `workspace`: `read-only`, `workspace-write` (default), or `danger-full-access`; `network`: boolean (default false); `web`: `disabled` (default), `cached`, or `live`; `subagents`: boolean (default false) |
| `agent.native.sandbox_workspace_write` | Codex-specific / Codex 专用: `writable_roots` (path array), `network_access`, `exclude_tmpdir_env_var`, `exclude_slash_tmp`; network declarations must agree / 网络声明须一致 |
| `agent.native.features` | Codex-specific / Codex 专用: `fast_mode` (boolean) |
| `budget` | Required finite / 必填有限值: `max_tokens`, `max_seconds`, `max_steps`; optional / 可选: `max_output_item_bytes`, `max_output_total_bytes`, `candidate_extra_slots`, `portfolio_review_after_cycles`, `node_targets`, `role_shares`, `breadth`, `correction` |
| `budget.node_targets.H`, `.W`, `.A`, `.E` | Complete role targets with `max_observed_tokens` and `max_seconds`; advisory, no increase to totals / 四角色完整目标，软提示，不增加总额 |
| `budget.breadth` | `max_w_seconds`, `max_w_observed_tokens`, `max_requested_repairs`; optional positive integer / 可选正整数 `initial_calls` |
| `budget.correction` | `review_after`, `pause_after` (explicit optional correction policy / 显式可选补正策略) |
| `executor` | `mode` (`local` or `external`), `config` (existing `executor`/`configuration` object), `resource_policy` (existing cumulative allowance contract) |
| `delivery` | Existing frozen delivery-policy object; optional adapter paths `adapter.adapter`, `adapter.source_roots` and path-like `adapter.launcher_argv[0]` resolve relative to TOML / 既有交付策略对象，上述路径相对 TOML |
| `policies` | Boolean / 布尔值: `formal_review`, `execution_authority`, `candidate_audit`, `decision_contract`; existing contract objects / 既有合同对象: `selection`, `resume`, `history_observer`; `worker_context`: `same_session` or `new_context` |
| `ui` | `port` (0–65535; default 0), `open_browser` (default false), `approval_secret_file` (host-private path reference / 宿主私有路径引用) |

`budget.max_output_total_bytes` caps the aggregate collected output of each node.
The same cap applies separately to the combined input materials handed to the next
node; it does not accumulate output across the research lifetime. Omit it to leave
this byte cap unset. `budget.candidate_extra_slots` caps the extras beyond the
representatives in W's nomination preview, and applies only when search mechanism
nominations are enabled. The complete retained candidate pool remains available to
A. The default is 2 extras.

`budget.max_output_total_bytes` 限制每个节点收集输出的合计体积；交给下一节点的输入材料
集合也单独受同一上限约束，不累计整个研究的输出。省略时不设此字节限制。
`budget.candidate_extra_slots` 限制 W 提名预览中代表候选之外的额外名额，仅在启用搜索
机制提名时使用；完整保留的候选池仍可供 A 选择。默认 2 个额外名额。

Codex defaults are `gpt-6.1-sol`, reasoning effort `high`, service tier `default`
and launcher `["codex"]`. A standard single-command Codex launcher defaults to
host-configured local experiments and at most two output-only repairs in the original
native home; without explicit node targets each role references the original total
ceilings. Protocol requires an explicit launcher and model, and does not invent
Codex reasoning-effort or service-tier defaults. Custom launchers default to external
experiment ownership unless explicitly configured. The sample expands commonly used
settings; optional policy shapes are described in the CLI guide and existing contracts.

Codex 默认模型、推理力度、档位和 launcher 如上。标准单命令 Codex 默认使用宿主管理的
local 实验，并在原生原 home 最多执行两次仅输出补填；未指定节点目标时引用原总上限。
protocol 必须显式提供 launcher 和 model，不套用 Codex 推理或服务档位默认值。
自定义 launcher 默认由 external 路径拥有实验执行，除非另行配置。

## Declared projects / 声明工程

Use an explicit TOML declaration to work at an existing project's original paths.
Omitting `[project]` preserves the existing `code/` workflow. Configure the
corresponding tables in a complete research declaration, with the standard
single-command Codex launcher and an explicit workspace permission:

通过显式 TOML 声明，可在已有工程的原路径工作；省略 `[project]` 时沿用原有 `code/`
流程。在完整科研声明的对应表中配置以下字段，使用标准单命令 Codex launcher 并明确工作区权限：

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

`base_root` must be the exact canonical absolute root of an ordinary Git worktree,
with no symlink ancestors; relative paths, `~`, bare repositories and linked
worktrees are unsupported. `editable` names directories, including empty roots;
the other arrays default to empty. Roots must be canonical relative directory
paths, without `.`, `..`, backslashes or `.git` components. Duplicates and any
ancestor/descendant overlap within or across the arrays are rejected. The base
cannot overlap instance, public, native-home or protected runtime paths; declared
mounts cannot overlap exact bound scientific inputs.

`base_root` 必须是普通 Git 工作树准确的 canonical 绝对根路径，全部祖先不得为符号链接；
不支持相对路径、`~`、bare 仓库或 linked worktree。`editable` 指定目录，允许空根目录，
其余数组默认空。各项必须是规范相对目录，不含 `.`、`..`、反斜杠或 `.git` 组件；
数组内及数组间重复、祖先/后代交叠均拒绝。基础工程不得与实例、公开材料、native home
或受保护运行时路径交叠；声明挂载目录不得与准确绑定的科学输入交叠。

Preparation freezes these declarations and the base's object format and HEAD
commit. SHA-1 and SHA-256 bases are supported. Existing-instance edits and forks
retain that exact identity. The base view contains only HEAD-tracked ordinary
files, read-only at their original paths; `.git`, untracked and ignored files are
absent. Dependencies must therefore be tracked. Bounded Git index/tree enumeration
rejects staged changes and tracked symlinks/gitlinks. Ordinary Git worktree checks
reject dirty visible sources; Git's normal filters, assume-unchanged and
skip-worktree flags apply. Control does not open or hash every base file.
Only visible mount sources receive file/type, executable-mode and parent-directory
stat race checks. W and runs exclude editable, scratch and output roots from the
base check; read-only roles exclude scratch and output roots. Changes to these
hidden sources do not invalidate their view. Implicit fetch and remote helpers
are disabled. Base files are not copied into the instance and no second file
manifest is retained. Missing or dirty visible base sources prevent a later use that needs them; they do not
rewrite known terminal outcomes or block metadata-only historical queries.

准备时冻结上述声明、基础仓库对象格式与 HEAD commit；支持 SHA-1 和 SHA-256 基础仓库。
已有实例的配置修改及 fork 均保留原身份。基础视图只在原路径只读暴露 HEAD 追踪的普通
文件，不暴露 `.git`、untracked 或 ignored 文件，因此依赖须明确纳入 Git。有界 Git
索引/树枚举拒绝 staged 修改与追踪 symlink/gitlink；普通 Git 工作树检查拒绝可见来源的
dirty 状态，采用 Git 通常的 filter、assume-unchanged 和 skip-worktree 语义。控制程序
不逐个打开或 hash 基础文件，只对可见挂载来源做文件类型、执行位及父目录 stat race 检查。
W 与 run 的基础检查排除 editable、scratch 和 outputs；只读角色排除 scratch 和 outputs，
这些隐藏来源的变化不使该视图失效。隐式 fetch 和 remote helper 均禁用。基础文件不复制
进实例，也不持久保留第二份文件清单。可见来源缺失或 dirty 只阻止后续真正需要这些来源的
消费，不改写已知终态，也不阻断只读历史元数据查询。

W edits explicit `project/<editable>` drafts mapped to the original base paths.
The first draft copies only tracked editable code under the existing shared code
limits. A uniquely selected parent run or compiled implementation supplies its
exact retained commit; absent a unique selection, the draft starts from frozen
base code with that fallback reported. No latest-call or dirty-draft inference is
used, and rebuilding an existing job preserves its current draft. A fork keeps
selected commits and current drafts distinct and still depends on the original
shared base. Each local project run captures all editable roots in one directory
`code_version`, including only declared code paths; initial seed, capture and
exports retain the existing code quotas.

W 编辑 `project/<editable>` 草稿，并映射到基础工程原路径。初始草稿只复制追踪的 editable
代码，全部根共享现有代码额度。唯一明确选中的父 run 或 compiled implementation 提供准确
留存 commit；没有唯一选择时回到冻结基础代码并说明该回退。不按最新调用或未提交草稿
推断来源，已有 job 重建保留当前草稿。fork 区分准确 commit 与当前草稿，并继续依赖原共享
基础工程。每次本地 project run 将全部 editable 根聚合为一个目录 `code_version`，只含
声明代码路径；初始 seed、采集及导出继续受现有代码额度约束。

There is no overlay filesystem. W can write only its declared editable, scratch
and node-output targets in the base view; other roles see the read-only base, not
W's draft. A run mounts its exact code version read-only, with read-only tmpfs for
empty editable roots. It may use the exact base root as cwd. Each scratch root is
temporary tmpfs; run outputs retain their full relative paths under the original
call's artifact directory and use the existing stop-writer/seal/collect lifecycle.
W's node outputs are drafts, not future call originals. Writes elsewhere fail;
declare the exact needed scratch/output directory rather than making the base
writable. Existing scientific input mounts remain read-only.

不使用 overlay 文件系统。W 在基础视图中只可写声明的 editable、scratch 与节点输出目标；
其他角色看到只读基础工程，不读取 W 草稿。run 将准确代码版本只读挂载，空 editable 根
用只读 tmpfs 表示，可使用准确 base 根作 cwd。每个 scratch 根为临时 tmpfs；run 输出在
原调用 artifact 目录下保留完整相对路径，沿用原停写、封存与采集生命周期。W 节点输出
仍是草稿，不是未来调用原件。其他位置写入失败；需要工作目录或输出时，应声明准确
scratch/output 目录。科学输入挂载仍只读。

Project projection requires Linux namespace support and an explicitly configured
standard Codex `workspace-write` policy. Inherited or unspecified native workspace
modes, `read-only` or `danger-full-access`, native named/default or selected
configuration permission profiles, managed policies, additional writable roots
and custom/protocol/registered launchers are unsupported and rejected before
launch. Native permission profiles here mean access-policy selections; role and
named Worker model/Skill profiles retain their existing contract. The framework
does not switch native permissions to make a project run. Network, web and
subagent declarations remain unchanged; existing Skill, authentication, review
and execution-authorization limits apply. The same-user host boundary remains soft.

工程投影要求 Linux namespace 支持，以及显式配置的标准 Codex `workspace-write` 策略。
继承或未明确声明的原生 workspace 模式、`read-only` 或 `danger-full-access`、原生 named/default
或选定配置中的权限 profile、managed policy、额外可写根，以及自定义/protocol/registered
launcher 均不支持，在启动前明确拒绝。此处原生权限 profile 指访问策略选择；角色或命名
Worker 的模型/Skill profile 继续沿用现有合同。不为运行工程而替换原生权限。网络、web 和
子 Agent 声明保持原值；现有 Skill、认证、审查与执行授权限制继续适用，同用户宿主访问
仍属软边界。

TCAD supports a self-contained implementation JSON inside an editable root through
the existing compiler, independent review and bounded package path. The scientific
author still closes dependencies with DeckFiles and typed inputs. The base tree is
not automatically inserted into a package, and remote execution does not inherit
the host's absolute project paths. A remote project-directory run requiring that
unsupported view is rejected before provider preparation. Project support grants
no scientific qualification or model/solver validation.

TCAD 支持将自包含 implementation JSON 放在 editable 根内，继续经过原 compiler、独立审查
与有界打包路径；科学作者仍用 DeckFiles 和 typed inputs 明确依赖闭包。不自动把整个基础
工程注入包，也不默认远端保留宿主绝对工程路径；需要该不受支持视图的远端工程目录运行
在 provider preparation 前拒绝。工程支持不授科学资格，也不证明真实模型或 solver 已验收。

## Credentials and permissions / 凭据与权限

Keep credentials outside TOML. Native authentication stays in the inherited original
backend environment/home; configuring a run requires no hand edits to Codex's user
configuration. Explicit Codex settings are projected into its invocation and ignore
user configuration for run settings. The UI key field stores a source reference;
preparation does not read or copy the key bytes. At serving time the operator key
must be an existing regular 0600 file of at least 32 bytes. A file reference or
same-user separation is a soft boundary, not hostile-process isolation.

凭据不写入 TOML。原生认证沿用后端原环境/home，无需手改 Codex 用户配置；显式运行
配置投影到调用参数，运行设置不再由用户配置覆盖。网页密钥字段仅保存来源引用，准备时
不读取或复制密钥；启动网页时须为已有普通 0600 文件，至少 32 字节。同用户仍是软边界。

Frozen `native_permissions` distinguishes `requested`, `effective`, `enforcement`
and `limitations`; configuration inspection and the webpage show the distinction.
A requested value alone is not an enforced permission or proof of tool availability.
The framework does not force all agents into one directory sandbox.

冻结的 `native_permissions` 分别记录请求值、实际策略、实施方式与限制；配置检查和网页
如实显示，不能把请求值当作已强制实施的权限或工具可用证明。框架不统一强制目录 sandbox。

| Backend/mode / 后端与模式 | Effective policy / 实际策略 |
| --- | --- |
| Standard Codex `workspace-write` | Network follows the frozen `network` declaration through its native sandbox. / 网络按冻结声明由原生 sandbox 实施。 |
| Standard Codex `read-only` | Effective network is false even when `network = true` was requested. / 即使请求联网，实际仍离线。 |
| Standard Codex `danger-full-access` | Filesystem and network are unrestricted; effective network is true. `network = false` remains recorded intent and cannot be enforced in this mode. / 文件与网络无 sandbox；离线请求仅保留为意图，不能实施。 |
| `protocol` or a custom launcher / 自定义 launcher | Effective access is unknown; enforcement is `hint_only`, owned by the launcher. / 实际访问未知，权限仅提示，由 launcher 实施。 |

The default managed local command executor has narrower supported permission
projections and may explicitly reject a declaration it cannot preserve; choosing a
native permission mode does not establish managed-executor support. See the CLI
guide's managed local experiment requirements. `network`, `web` and `subagents` are
separate declarations. Allowing subagents does not establish complete usage accounting,
contributor identity or independent review.

默认受管本地执行器只支持更窄的等义权限投影，无法保留时明确拒绝；原生权限可选不等于
受管执行器已支持。网络、网页检索和子 Agent 分别声明；开启子 Agent 不证明用量完整、
作者完整或正式独审。

## Backend contract / 后端合同

| Backend / 后端 | Transport and limits / 输运与限制 |
| --- | --- |
| `codex` | Adapts native `exec` JSON events, model profiles, explicit permissions and usage. Qualified same-session output repair uses the frozen original home and policy. Complete participant scope is supported only for a standard single-agent launcher with subagents disabled. / 适配原生 exec、profile、权限和用量；原线程补填受原策略约束，完整参与者范围仅限标准单 Agent launcher。 |
| `protocol` | Runs the exact supplied argv, sends one JSON request on stdin, reads JSONL events. Permissions are declarations with `enforcement = "hint_only"`; the launcher owns actual access controls. No native session resume or complete native authorship attestation; formal independent review is unavailable. / 精确 argv、stdin JSON、stdout JSONL；权限仅作提示，由 launcher 实施，不支持原线程恢复或完整作者证明，不能正式独审。 |

The protocol request identifies `protocol = "scidiscovery-agent-v1"` and contains
`prompt`, `workspace`, `profile`, `response_schema` and `permissions`. Events use
`session` with `session_id`, `usage` with cumulative invocation `usage`, `result`
with the scientific JSON object in `result`, and explicit `completed`. Usage counters
are `input_tokens`, `cached_input_tokens` and `output_tokens`; absent or invalid
usage remains unknown rather than becoming zero. Optional public `message` and
`tool` events feed the bounded progress projection. A result alone is not a completed
call: terminal evidence, process exit, cleanup, exact originals and control checks
still apply. This adapter is an integration boundary; it does not supply or qualify
a separate vendor harness. New harnesses need their own exact integration evidence.

协议请求与事件字段如上，usage 为本次调用的累计数。缺失或非法计量保持未知，不补零。
可选 message/tool 进入有界进度投影；单独 result 不等于调用完成，终态、退出、清理、
原件和控制检查仍有效。adapter 只提供接入边界，不附带或证明另一厂商 harness 的资格。

## Control while running / 运行中控制

The instance configuration page edits models, role profiles, permissions, node
targets, experiment execution, delivery and optional policies. Saving queues the
configuration for the next new node and subsequent nodes; an existing prepared or
running node, its retries, and its original external calls keep their exact settings.
The page distinguishes active and pending settings. A stale browser form must be
reloaded before saving over a newer revision. Changing total budgets requires an
adjustment note and preserves consumption and outstanding execution facts.

当前研究配置页可编辑模型、角色、权限、节点目标、实验执行、交付和可选策略。
保存后从下一个新节点开始持续生效；已经准备或运行的节点、其重试及原外部调用继续
使用原配置。页面区分已生效与待生效配置；其他页面已保存更新时，旧表单须重新加载。
调整总预算须填写调整说明，并保留累计消费和未决执行事实。

Instance paths, original objectives and source records remain immutable. Goal and
direction changes use guidance. Saving configuration does not launch a node, reopen
a closed research phase, or grant an exact experiment or delivery call permission.
Backend enforcement and existing qualification checks still apply.

实例路径、原始目标和来源记录保持不变，目标与方向变化使用方向建议。保存配置不启动
节点、不重新开启已结束的研究阶段，也不授予精确实验或交付调用的权限；后端实施限制及
原有资格检查继续适用。

`start` launches or reconnects to a detached controller under the original writer
lock; its operation receipt survives the requesting CLI or webpage process exiting.
`start --continue` explicitly clears a next-node pause before advancing.
`start --resume-step N` detaches recovery of one exact known failed job through its
original frozen continuation gates; it cannot combine with `--continue` and does
not add backend resume support. `run` and `continue` remain foreground alternatives. `pause` lets accepted work finish and
prevents the next node; it is not an immediate kill. `operations --operation-id ID`
reads the exact receipt. A disappeared controller requires original-call
reconciliation, never automatic resubmission.

`start` 使用原写锁启动或连接 detached 控制进程；CLI 或网页进程退出不撤销该操作。
`start --continue` 显式清除下一节点暂停；`start --resume-step N` 后台恢复精确已知故障
job，仍通过原冻结续接门槛，不能与 continue 合用，也不增加后端恢复能力。
`run`/`continue` 仍可前台运行。
`pause` 等已接受工作完成后阻止下一节点，并非立即杀进程；进程失踪须核对原调用，不能重发。

Scientific `budget-amend` requests receive durable operation IDs and apply at a safe
boundary before another new task, retaining all consumption, reservations and unknown
usage. `accepted` is a queued request, not an applied ceiling. Execution-resource
amendments remain separate and grant no exact-call permission. Soft node/W targets
request a bounded conclusion without forcing A or rejecting a new managed experiment
solely for reaching the target. Shared admission and finite execution permissions
remain enforced. The protected final A and its authorized original delivery retain
their completion policy; see [architecture](ARCHITECTURE.md).

科学预算修订先收取持久 operation ID，在下一新任务前的安全边界应用，保留全部消费、
预留和未知用量。accepted 仅表示排队；执行资源修订另行办理且不授精确调用权限。
节点/W 软目标只提示收口，不仅因达线拒绝新受管实验或强制转 A。共享准入、单次有限执行
及 protected FINAL 原策略继续有效。

User direction uses `guidance-submit`, `guidance-list`, `guidance-edit` and
`guidance-revoke`. Targets are `global`, `H`, `W`, `A`, `E`; `--scope next` binds
the next matching new task, while `--scope research` applies to matching future
tasks. Pending edits/revocations require the current `--expected-revision`. Applied
task inputs are immutable; revoking applied research guidance records a future-only
revocation. `--kind advice` is optional scientific advice. An explicit `goal_change`
requires `--target global --effective-step N` and has research scope. Guidance grants
no scientific verdict, qualification or execution permission and does not reset the
original goal, identities or budgets.

用户方向通过上述 guidance 命令管理，支持全局或指定角色、下一匹配任务或整项研究范围。
待应用项可按当前 revision 修改/撤销；已应用任务输入不可改写，整项研究建议的撤销只对未来
生效。advice 是可不采纳的科学建议；显式目标变更须给全局目标和生效节点，范围为后续研究。
建议及目标变更均不授结论、资格或执行权限，不重置原目标记录、身份与预算。


## Results and optional webpage / 结果与可选网页

Read and download sealed scientific originals without starting a webpage:

无需网页即可读取和下载封存科学原件：

```sh
scid-research result --root /absolute/instance --step 3 > result.json
scid-research materials --root /absolute/instance --step 3 --after 0 --limit 20
scid-research artifact --root /absolute/instance --step 3 --index 1 --output ./model.bin
```

`result` writes exact checked scientific JSON bytes to stdout, without a metadata
wrapper. `materials` pages exact sealed members (`limit` 1–100, default 20); use its
member index for `artifact`. Artifact download checks the same original as the
webpage and refuses an existing destination. These reads do not advance research or
qualify the science. A's goal coverage, missing inputs, delivery choice and original
operation receipts determine the actual outcome; a completed flow is not a claim
that the scientific objective was achieved.

`result` 原样输出经核验的科学 JSON，不增加元数据包装。`materials` 分页列封存成员，
以其精确 index 下载 artifact；下载核对与网页相同原件，拒绝覆盖已有目标文件。读取不推进
研究、不授科学资格；须结合 A 的目标覆盖、缺失输入、交付选择和原回执判断实际结果，
控制流程结束不等于科学目标达成。

`serve-ui --workspace /absolute/workspace --approval-secret-file /absolute/operator.key --port 8123`
opens a management webpage before any research instance exists. The workspace must
already exist; its independent home creates or loads explicitly selected instances
without requiring a research declaration. Loaded choices last for the host session;
after restart they can be loaded again by exact directory. The workspace has no
scientific identity, budget or research controls. The new-instance page starts blank and never reads a workspace declaration
automatically. Required finite budgets remain empty until explicitly entered.
Preview uses the same document parser, defaulting and preparation validation as
the CLI, without writing task files, creating an instance or submitting models.
Inline task text becomes the instance-owned immutable public original at
`project/global/objective.md` only during explicit creation. Creating rejects an
existing root and does not start research.
`--workspace` and `--root` are mutually exclusive.

尚无研究实例时，可用上述 `--workspace` 命令打开网页；已有目录仅承载管理入口，没有科学身份、
预算或研究启动按钮。首页是独立的研究工作台，即使没有配置也可以加载已有实例。已加载列表仅保留于本次会话，
重启后可按目录重新加载。选择“新建实例”后，页面从空白任务和预算开始，不隐式读取 `research.toml`。表单中的
声明项由 CLI 相同的默认引擎处理，未填写的可选项沿用默认值。用户检查预览后明确创建，
任务原文才写入实例 `project/global/objective.md` 并冻结；创建拒绝已有目录且不启动模型，
随后须另点“开始研究”。可选的导入、导出与明确保存配置共用同一组控件，保存与本次创建
分开；创建不自动回写原配置。两种入口二选一。

`serve-ui --root /absolute/instance --instance-root /absolute/other-instance` adds
explicit instance choices; repeat `--instance-root` for more. The management page
can open another exact existing root; it never scans directories or chooses by
recency. Inside an instance, `/configuration` edits operational settings for the next
new node and shows active settings. `/new` prepares a separate instance;
the independent workspace retains `/configuration` as a creation entry. Both creation
entries use the same research-configuration API. Optional explicit TOML import defines its
relative path base; exporting or saving keeps declared path meanings. Editing
an imported declaration for creation affects only the new instance. The
webpage also exposes queued budget changes, guidance, operation receipts, detached
start/resume and bounded progress. CLI commands remain available independently.
These implemented surfaces have no implied live-browser or backend qualification.
UI intent replay, display caching and translation limits are defined in the
[architecture](ARCHITECTURE.md#instance-ui-control-and-display); the
[CLI guide](ROOTLESS_CLI.zh-CN.md) describes their user controls.

`serve-ui` 可重复 `--instance-root` 添加明确实例；管理页也可按精确已有根打开，不扫描
目录、不按新旧推断。实例内 `/configuration` 编辑后续节点配置并展示当前生效设置；
`/new` 独立创建新研究，工作台的 `/configuration` 保留新建入口。新建入口通过
相同 API 检查和准备实例；TOML 为可选导入、导出及明确保存格式。导入文件目录决定其相对路径基准，
导出与另存保留路径含义；新建声明编辑不修改已有实例。网页还提供预算排队、
用户方向、操作回执、后台启动/恢复和有界进度。这些入口不额外授予真实浏览器或后端资格。
UI 意图重试、显示缓存及翻译限制由[架构](ARCHITECTURE.zh-CN.md#实例-ui-的控制与显示)
定义，具体操作见 [CLI 指南](ROOTLESS_CLI.zh-CN.md)。

Cov execution is provided by the optional `scidiscovery-cov-execution` distribution
(`SCID_PLUGINS=cov_execution`). Its executor key remains `cov_execution:experiment`;
new configuration freezes the registration's observer/delivery defaults. Explicit
operator policies still apply; installing a provider grants no execution authority.
Cov 执行来自独立插件，核心安装不含 Cov；注册提供的新实例默认观察与交付配置仍须冻结，
安装本身不授予执行权限。

## Selected framework Skills / 按角色选择框架 Skill

Default role selections are empty. Declare finite named sets, then bind a role or
named Worker profile explicitly. Assets come from the actual CLI installation's
single `skills/` tree: beside `src/` in the source release, beside `site/` in the
thin installation, or at `sys.prefix/skills` for the supported standard wheel layout.
`[skills].root` and `SCID_SKILLS_ROOT` are not supported. A bare wheel does not
include these external assets; deployment must publish the required bundles once
in that installation's expected `skills/` directory.

默认各角色不启用 Skill。先声明有限集合，再显式绑定角色或命名 Worker。资产唯一来源是实际
加载的 CLI 安装中的 `skills/`：源码发行与 `src/` 相邻，薄安装与 `site/` 相邻，受支持的
标准 wheel 布局使用 `sys.prefix/skills`。不支持 `[skills].root` 或 `SCID_SKILLS_ROOT`
覆盖。裸 wheel 不包含这些外置资产；部署须在该安装预期的 `skills/` 中一次发布所需 bundle。

```toml
[skills.sets.tcad]
items = ["sentaurus-tcad-code"]
[skills.sets.analysis]
items = ["curve-analysis", "scientific-paper-evidence"]
[agent.workers.analysis]
model = "gpt-6.1-sol"
reasoning_effort = "high"
service_tier = "default"
skill_set = "analysis"
[agent.roles.W]
skill_set = "tcad"
[agent.roles.A]
worker = "analysis"
```

A role's explicit fields override its named Worker's model fields and `skill_set`.
The explicitly selected execution provider may offer sets through
`research_defaults.optional_skill_sets`, using the same `{name: {items: [names]}}`
shape. Installing a plugin or declaring a set enables no role. Only a role's
`skill_set`, including one inherited from its explicitly named Worker, selects it.
An identical explicit/provider set is accepted; conflicting members are rejected.
See the [provider API](PLUGIN_RUNTIME_API.md#registration-and-configuration).

角色显式字段覆盖命名 Worker 的模型字段和 `skill_set`。明确选中的执行插件可以通过
`research_defaults.optional_skill_sets` 提供同形的可选集合。安装插件或声明集合本身不启用
任何角色；只有角色的 `skill_set`（含明确引用的命名 Worker 所声明的值）才启用。显式声明
与插件提供的同名集合相同则接受，成员冲突则拒绝。

Prepare freezes expanded set names and installation source bindings, not bundle
bytes. Only actually selected directories and their `SKILL.md` metadata are checked;
unselected bundles are not read. Native discovery uses a validated, rebuildable
`.agents/skills` projection and read-only directory binds to the selected originals.
No Skill bytes, object store or byte manifest is created inside a new instance.
Source and instance may be on different filesystems: no hardlink or copy fallback
is needed. Bundle resources remain live installation assets; changing their bytes
at the bound path changes what a later consumer reads. Missing or linked selected
directories/manuals report the Skill name, exact expected path and repair action.

Prepare 冻结展开后的集合名称和安装来源绑定，不冻结 bundle 字节。只检查实际选中目录及其
`SKILL.md` 元数据，未选资产不读取。原生发现使用可校验、可重建的 `.agents/skills` 投影，
并将所选原目录原地只读 bind；新实例内不创建 Skill 字节、对象库或字节 manifest。安装与
实例可以跨文件系统，不依赖硬链接或复制回退。资源仍是安装中的实时资产；修改绑定路径中的
字节会改变后续消费者读到的内容。选中目录或手册缺失、链接时，诊断列出 Skill 名称、准确
预期路径和修复方式。

Configuration revisions may change role/Worker selections within already frozen
sets; changing the set inventory requires a new instance. Existing jobs and
native intents retain their exact selection. Retained execution/resume does not
re-query provider defaults. A fork also preserves the expanded original selection,
including no selection, rather than importing newer optional offers.

配置修订可在已冻结集合内更改角色/Worker 选择；改变集合清单要求新实例。既有 job 和 native
intent 保持准确原选择，执行/续接不重取插件默认值。fork 同样保留原展开选择，包括原本没有
启用 Skill 的状态，不引入插件后来新增的可选声明。

Standard Codex supports this projection directly. A provider-declared module
launcher may implement the same exact contract; Cov uses
`python -m cov_execution.launcher --config /exact/operator.json`.
Other script/protocol/custom launchers reject nonempty selected sets without a
verified loading contract. Empty or unselected sets preserve existing behavior.
This framework selection does not suppress native user/system/plugin sources or
change native home, sandbox, shell, network, web or subagent permissions. A Skill
grants no execution permission, independent-review verdict or scientific qualification.
Cov still copies its configured `auth.json` into its separate native home; this
Skill supply contract does not establish shared authentication, refresh or logout.

标准 Codex 直接发现投影；适配器声明的模块 launcher 可实现相同合同，Cov 使用上述模块入口。
缺少可核实加载合同的任意脚本、protocol 或自定义 launcher 对实际选中的非空集合明确拒绝；
空集合或未选集合维持原行为。框架选择不屏蔽原生用户、系统、插件来源，不改变原生 home、
sandbox、shell、网络、web 或子代理权限。Skill 不授执行权限、独审结论或科学资格。
Cov 仍把配置的 `auth.json` 复制到独立 native home；此 Skill 合同未实现共享认证、刷新或登出。

Retained Skill schema v2 records keep their existing read-only manifest/object
interpretation. A fork without its own legacy store reads the exact registered
parent original after identity checks; it creates no new Skill copies. That parent
store must remain available, and a missing source reports its exact manifest path.
This compatibility does not migrate scientific protocols or restore retired
archive/cold-package operations. Read-only mounts remain a soft boundary for
trusted same-user processes.

保留的 Skill schema v2 记录继续按原只读 manifest/对象解释。fork 若无本地旧库，则核对身份后
读取准确登记的父原件，不创建新的 Skill 副本；父库须继续存在，来源缺失时明确报告准确
manifest 路径。这不迁移科学协议，也不恢复已退役的归档/cold 包入口。只读挂载仍属同用户软边界。
