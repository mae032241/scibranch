# SciBranch research configuration and agent backends / 研究配置与 Agent 后端

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

独立工作台从空白表单开始，只显示日常需要的字段：任务原文、公开材料目录、新实例目录、
后端 → 模型 → 推理力度 / 服务档位（按已安装后端的模型目录给出下拉选项，也可选“自定义”
填写精确 ID；Codex 读取 `models_cache.json`，Pi 读取 `pi --list-models`，读不到时退回文本框）、
三项有限预算，以及可选的角色模型、技能勾选、权限、自动方向复核（`policies.supervision.mode`）
和实验执行插件。模型目录只用于编辑时给出选项，准备、冻结与执行仍只校验写入的精确值。
其余设置统一在“高级设置（TOML）”文本框中编辑，格式与研究 TOML 文件相同；已有实例预填
当前生效的这部分配置。表单字段与 TOML 写同一个键时提示冲突；表单上没有的键原样保留。
TOML 无法表示的空值（null）随表单原样保留，写同名键即可覆盖。无需先写 TOML 文件，
也不自动读取工作区已有任务或配置。检查完整有效预览后明确创建，再单独开始研究；
修改参数后须重新预览。
表单同样支持 `[project]`、`skills.sets`、命名 Worker 与角色 `worker`/`skill_set` 引用的
导入及导出。声明可选集合本身不启用，须由角色或 Worker 显式选择。已有实例编辑保留
原工程和集合清单，不能通过运行配置修订替换。

TOML is an optional import/export and explicit save format. Imports populate the
same controls. Save/update writes only the explicitly selected configuration file;
creating an instance uses the current form without rewriting an imported original.
Export canonicalizes declared paths so saving in another directory preserves their
meaning. CLI users may instead copy [research.example.toml](../research.example.toml)
beside the objective and public inputs, edit its paths and finite budgets, then use:

Codex remains the default backend. That example uses standard Codex. For Pi, explicitly select it and copy the separate
[research.pi.example.toml](../research.pi.example.toml), supply an available exact
provider/model and explicit thinking/permissions, then use the same CLI sequence.
See [Pi's supported profile](#experimental-pi--common-research-path--实验性-pi公共研究路径).

默认后端仍为 Codex。原示例使用标准 Codex；Pi 须显式选择并改用独立的 [research.pi.example.toml](../research.pi.example.toml)，
填写实际可用的准确 provider/model、显式 thinking 与权限，再使用相同 CLI 流程：

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

### Current scientific policy / 当前科学策略

Current instances use protocol 33, scientific strategy 3 and advisory scope 1.
These are the only supported scientific behavior versions. Worker and lineage
view 1, final-A completion policy 1 and selection policy 2 remain current format
markers, not older scientific policies. Earlier protocols, strategies 1/2,
unmarked scientific settings/jobs, supervision 1/2/3, frozen v4 missing its
boolean freshness field, and fragment handoff 1 or a missing handoff marker are
rejected before scientific interpretation or new effects. There is no migration,
compatibility reader or supported manual settings-version edit. Configuration
revisions, recovery and fork cannot upgrade retained originals.

当前实例使用协议 33、科学策略 3 与 advisory scope 1，只支持这一科学行为。
worker／lineage view 1、final-A completion policy 1 和 selection policy 2 仍是当前
格式标记，不表示旧科学策略。旧协议、策略 1/2、缺科学职责标记的 settings/job、监督 1/2/3、
冻结 v4 缺布尔 freshness 字段及 fragment handoff 1 或缺 handoff 标记的格式，在科学解释
或新效果前拒绝。不兼容读取、不迁移、不手改版本；配置修订、恢复和 fork 不升级留存原件。

W starts research autonomously, may explore without a candidate or mature
hypothesis, and declares exact mechanisms and batch questions through
`--research JSON_FILE`. H offers explanations, E answers bounded evidence
questions, and ordinary A returns diagnostic advice to W. W's `decision` requires
Final A before delivery. Final A may accept an exact candidate, reject all or,
while original allowance remains, request one bounded W correction followed by
at most one reassessment. Exhaustion permits final reporting from originals only.
Formal independent qualification and execution permission remain separate gates.
Optional research memory, material selection, routing and final-reporting rules
are maintained in [Research-loop contracts](RESEARCH_LOOP_CONTRACTS.md).

W 自主开始研究，可在尚无候选或成熟假设时探索，通过 `--research JSON_FILE` 声明准确机制
和批次问题。H 提供解释、E 回答有界证据问题，普通 A 将诊断建议交回 W。
W 的 `decision` 在交付前请求 Final A。Final A 可接受准确候选、全部拒绝，或在原额度
可用时要求一次有界 W 补正，之后至多一次再评估；耗尽时只从原件作最终报告。
正式独立资格与执行权限仍分别核对。可选研究记忆、材料选择、路由及最终报告的准确规则
由[研究循环合同](RESEARCH_LOOP_CONTRACTS.zh-CN.md)维护。

### Automatic supervision / 自动监督

Supervision is v4 or `None` (direct API opt-out). New preparations default to the
following policy. `require_fresh_input` remains tunable: `true` gates automatic
review on changed accepted authored questions or exact receipt frontiers;
`false` allows operational opportunities without that gate. Public declarations
may omit it to use the new default, but frozen v4 settings/jobs must contain the
explicit boolean. Calls, scores, time and engineering failures alone establish no
scientific stagnation or benefit.

监督仅支持 v4 或 `None`（直接 API 关闭）。新准备默认如下策略。
`require_fresh_input` 仍可调：`true` 要求已接受作者问题或准确收据边界变化才自动评估；
`false` 允许不经过该门槛的运行检查机会。公开声明省略时采用新默认，冻结 v4 settings/job
则必须显式保存布尔值。调用、分数、时间及工程失败本身不证明科学停滞或收益。

```toml
[policies.supervision]
version = 4
mode = "review"
require_fresh_input = true
consecutive_calls = 4
min_budget_fraction = 0.10
budget_checkpoints = []
# max_automatic_reviews omitted: no count cap; an integer adds an emergency cap.

[policies.supervision.automatic_budget_fraction]
tokens = 0.25
seconds = 0.25
```

`mode="off"` disables automatic A; `notice` retains advisory behavior. Both
automatic budget fractions must be positive and at most 1; the minimum consumption
fraction must be positive and applies to the first review and later spacing.
V4 requires empty fixed checkpoints. An optional nonnegative
`max_automatic_reviews` adds an emergency count cap. Actual automatic-A cost
accumulates tokens and active seconds across all attempts, independently of the
verdict, in each finite shared dimension. Unknown or incomplete metering retains
known lower bounds and suppresses only additional automatic A. The soft budget
or count cap also suppresses only additional automatic A, without cancelling
accepted work or blocking W and original query/collect. Final A, formal review
and W's proactive consultations keep separate scopes. Identities, consumed budgets,
review counters and waiting survive configuration changes and recovery.

`mode="off"` 关闭自动 A，`notice` 保留提醒。两个自动预算比例均须为正且不超过 1；
最低消费比例须为正，同时约束首次与后续间隔；v4 的固定检查点必须为空。
可选非负 `max_automatic_reviews` 是紧急次数帽。自动 A 在各有限共享预算维度按全部尝试
实际 token／活动秒累计费用，与结论无关。缺测保留未知及已知下界，只抑制额外自动 A；
软预算帽或次数帽也只抑制额外自动 A，不取消已接受工作、不锁住 W 或原 query／collect。
Final A、正式独审及 W 主动咨询保持各自范围；修订和恢复保留身份、预算消费、评估计数及等待。

### Context handoff / 上下文交接

Enabled fragments require `context_handoff_version=2` (public field
`policies.worker_context_handoff_version=2`). The direct API may use
`worker_fragment_policy=None`. New supported Codex/Pi declarations default to one
complete window; `policies.worker_context_windows` accepts 1 or 2. Window count is
one initial window plus accepted completed compactions. In the last complete
window, occupancy at or above 90% only advises a checkpoint. Handoff requires
windows started above the maximum and fresh current occupancy strictly above
70%; compaction/count alone and unavailable occupancy do not stop work. It seals
a partial result for a new W, without automatic A or resetting original budgets.
Consultation may create a new logical W job while reusing a verified native
session; job numbering alone does not prove context rotation. Precise native
continuity and observation requirements are described below and in the contracts.

启用 fragment 必须使用 `context_handoff_version=2`，公开字段为
`policies.worker_context_handoff_version=2`；直接 API 可用 `worker_fragment_policy=None`。
新受支持 Codex／Pi 声明默认一个完整窗口，`policies.worker_context_windows` 接受 1 或 2。
窗口数为一个初始窗口加已接受完成压缩次数。最后完整窗口占用达到 90% 只提醒 checkpoint；
只有窗口数超额且当前新鲜占用严格大于 70% 才交接。压缩／计数本身或占用未知不停止工作。
交接封存部分结果并交新 W，不自动 A，也不重置原预算。咨询后新逻辑 W 任务可能续接经验证
的原生 session，编号变化本身不证明窗口轮换；准确续接与观测条件见下文及研究循环合同。

Native subagent permission can be declared as a separate boolean in
`[agent.roles.W]` (or an explicitly selected named Worker profile). Role values
take priority over explicit `[permissions].subagents`; otherwise new W defaults
to enabled only when the selected Backend supports native subagents. Explicit
false remains effective unless the user supplies a distinct explicit role choice.
Unsupported explicit enablement is refused; unsupported defaults do not create a
new role override. Other roles retain their original declarations. Job/intent
freeze `native_subagents` independently of model-only `native_profile`, and export
retains the role selection. Old settings/jobs lacking this field keep their
original permission on revision, recovery and fork.

W prefers supported, permitted native subagents for small evidence extraction or
scripts. Without that capability or permission W does the small work itself,
without an extra role or W handoff. W considers E independently for large or
complex evidence work. Native Pi does not support subagents and gains no emulated
path. Exact main-thread consultation resume does not establish complete child
authorship or usage: those gaps remain unsupported/unknown, observed tokens remain
lower bounds, and existing formal-review and execution gates still apply.

原生子代理权限可在 `[agent.roles.W]` 或明确选中的具名 Worker profile 中单独声明 bool。
角色值优先于显式 `[permissions].subagents`；否则，新 W 只在所选 Backend 支持时默认开启。
显式 false 保持生效，除非用户另给明确角色值。能力不支持时，显式启用会拒绝，默认不额外创建
角色 override；其他角色保留原声明。job/intent 以 `native_subagents` 独立冻结权限，模型
`native_profile` 保持原三字段；导出保留角色选择，旧无字段设置/任务在修订、恢复和 fork 后
仍保持原权限。支持且允许时简单证据提取、脚本优先子代理；不支持或禁用时 W 自己做小任务，
不额外交接；规模大或复杂时才自主考虑 E。原生 Pi 不支持子代理，不补造路径。准确主线程续接
不证明子代理完整作者或费用覆盖；未知保留，观测 token 仍是下界，正式独审及执行门槛不变。

## Declaration fields / 声明字段

Unknown tables and fields are rejected. Fields and defaults below remain subject
to the selected backend's capability checks; Pi requires the explicit permissions
for its selected resource profile below, not the general permission defaults.
Except for the exact absolute
`project.base_root`, paths listed below resolve relative to the TOML file,
including `~` expansion; changing the caller's working directory does not
change them. Only a path-like `agent.launcher[0]` is resolved this way; remaining
argv elements are literal native arguments, so use absolute paths for their inputs.

未知表和字段会拒绝；下列字段及默认值仍须满足所选后端能力校验。Pi 须显式声明下文所选
资源 profile 的权限组合，不能使用通用权限默认值。除要求准确绝对路径的 `project.base_root` 外，下列路径相对 TOML
所在目录解析，也支持 `~`。launcher 只有带路径的
首项如此解析，后续 argv 是原生参数，不猜测其路径含义，所需文件宜用绝对路径。

| Table / 表 | Supported fields / 字段与含义 |
| --- | --- |
| top level / 顶层 | `version = 1` (default / 默认 1) |
| `research` | Required path / 必填路径: `root`, `public_root`; exactly one / 二选一: `objective` (original path / 原件路径), `objective_text` (inline original text / 任务原文); optional / 可选: `user_updates` (path array / 路径数组), `source_restrictions` (existing declaration objects; each `source` is a path / 原声明数组，`source` 为路径) |
| `project` | Optional exact Git worktree `base_root`; required nonempty relative directory array `editable`; optional arrays `scratch`, `outputs` (default empty). See [declared projects](#declared-projects--声明工程). / 可选工程声明：准确 Git 工作树根 `base_root`、非空相对目录数组 `editable`，以及默认空数组的 `scratch`、`outputs`。 |
| `agent` | `backend` (registered provider id: `codex`, `protocol`, experimental / 实验性 `pi`; Pi restrictions below / Pi 限制见下文), `launcher` (argv array), `model`, `reasoning_effort`, `service_tier`, `roles`, `native` |
| `agent.roles.H`, `.W`, `.A`, `.E` | Optional `model`, `reasoning_effort`, `service_tier`; omitted roles/fields inherit the global profile / 省略项继承全局配置 |
| `permissions` | `workspace`: `read-only`, `workspace-write` (default), or `danger-full-access`; `network`: boolean (default false); `web`: `disabled` (default), `cached`, or `live`; `subagents`: boolean (default false) |
| `agent.native.sandbox_workspace_write` | Codex-specific / Codex 专用: `writable_roots` (path array), `network_access`, `exclude_tmpdir_env_var`, `exclude_slash_tmp`; network declarations must agree / 网络声明须一致 |
| `agent.native.features` | Codex-specific / Codex 专用: `fast_mode` (boolean) |
| `agent.native.resource_mode` | Pi-specific / Pi 专用: `closed` (default when omitted / 省略时默认) or explicit / 显式 `configured`; see native resources and permissions below / 原生资源与权限见下文。 |
| `budget` | Required finite / 必填有限值: `max_tokens`, `max_seconds`, `max_steps`; optional / 可选: `candidate_extra_slots`, `portfolio_review_after_cycles`, `node_targets`, `role_shares`, `breadth`, `correction`, `storage`; legacy `max_output_item_bytes`, `max_output_total_bytes` remain accepted/frozen without file-byte enforcement / 旧字节字段仍接受并冻结，不限制文件字节 |
| `budget.node_targets.H`, `.W`, `.A`, `.E` | Complete positive finite role targets with `max_observed_tokens` and `max_seconds`; token targets are advisory, as is W time under its context-window policy; saved legacy fragments retain their time review cadence / 四角色完整正有限目标；token 为建议，W 上下文窗口策略下时间目标也为建议，旧冻结 fragment 保留原时间复核节奏，不增加总额 |
| `budget.breadth` | `max_w_seconds`, `max_w_observed_tokens`, `max_requested_repairs`; optional positive integer / 可选正整数 `initial_calls` |
| `budget.correction` | `review_after`, `pause_after` (explicit optional correction policy / 显式可选补正策略) |
| `budget.storage` | Existing storage-policy object: `version = 1`, `enabled`, optional positive count limits `max_scan_entries`, `max_files`; disabled by default. Legacy positive `max_file_bytes`, `max_total_bytes`, `max_instance_bytes` remain accepted/frozen without byte enforcement. See [managed retention](ROOTLESS_CLI.zh-CN.md#受管本地实验). / 默认关闭的既有存储策略；启用须有明确正整数项，文件/条目数继续有效，旧字节字段只接受并冻结。 |
| `executor` | `mode` (`local` or `external`), `config` (existing `executor`/`configuration` object), `resource_policy` (existing cumulative allowance contract) |
| `delivery` | Existing frozen delivery-policy object; optional adapter paths `adapter.adapter`, `adapter.source_roots` and path-like `adapter.launcher_argv[0]` resolve relative to TOML / 既有交付策略对象，上述路径相对 TOML |
| `policies` | Boolean / 布尔值: `formal_review`, `execution_authority`, `candidate_audit`, `decision_contract`; existing contract objects / 既有合同对象: `selection`, `resume`, `history_observer`; `worker_context`: `same_session` or `new_context`; `worker_context_windows`: integer 1 or 2 / 整数 1 或 2 |
| `policies.resume.automatic_recovery` | Shared bounded recovery for capable Backends, including standard Codex and Pi; fields and defaults below. / 按 Backend 能力提供的共用有界自动接续策略，支持标准 Codex 与 Pi，字段及默认值见下文。 |
| `ui` | `port` (0–65535; default 0), `open_browser` (default false), `approval_secret_file` (host-private path reference / 宿主私有路径引用) |

`budget.max_output_item_bytes` and `budget.max_output_total_bytes` retain their
legacy configuration syntax and frozen values, without per-file or aggregate byte
quotas on originals, input materials, arrays, artifacts, code or logs. The same
applies to storage and provider file/total/log/result byte fields; the UI does not
offer them as active file-size controls. Compact handoffs, declared-result documents,
manifests, control metadata and public views remain bounded. Exact `size_bytes`,
real truncation detection, finite IO/execution windows, file/entry counts, path
safety and explicit permissions remain effective. `budget.candidate_extra_slots` caps the extras beyond the
representatives in W's nomination preview, and applies only when search mechanism
nominations are enabled. The complete retained candidate pool remains available to
A. The default is 2 extras.

`budget.max_output_item_bytes`、`budget.max_output_total_bytes` 保留旧语法和冻结值，
不再对原件、输入材料、数组、成果、代码或日志限制单文件或累计字节；存储和 provider 的
file/total/log/result 字节字段同样不在 UI 作为有效文件大小控件。紧凑交接、声明结果、manifest、
控制元数据和公共视图仍有界；准确 `size_bytes`、实际截断检测、有限 IO/执行窗口、文件/条目数、
路径安全和明确权限继续有效。
`budget.candidate_extra_slots` 限制 W 提名预览中代表候选之外的额外名额，仅在启用搜索
机制提名时使用；完整保留的候选池仍可供 A 选择。默认 2 个额外名额。

Codex defaults are `gpt-6.1-sol`, reasoning effort `high`, service tier `default`
and launcher `["codex"]`. A standard single-command Codex launcher defaults to
host-configured local experiments and at most two output-only repairs in the original
native home, plus a separate bounded automatic-recovery policy. Without explicit
node targets each role references the original total ceilings. Protocol requires an explicit launcher and model, and does not invent
Codex reasoning-effort or service-tier defaults. Custom launchers default to external
experiment ownership unless explicitly configured. The sample expands commonly used
settings; optional policy shapes are described in the CLI guide and existing contracts.

Codex 默认模型、推理力度、档位和 launcher 如上。标准单命令 Codex 默认使用宿主管理的
local 实验，在原生原 home 最多执行两次仅输出补填，并启用独立的有界自动接续策略；
未指定节点目标时引用原总上限。
protocol 必须显式提供 launcher 和 model，不套用 Codex 推理或服务档位默认值。
自定义 launcher 默认由 external 路径拥有实验执行，除非另行配置。

New standard single-command Codex and supported Pi declarations default to
`policies.worker_context_windows = 1`; only integers 1 and 2 are accepted. This
freezes `worker_fragment_policy.max_context_windows`. Same-session fragments keep
one W job and allowance. Window use is one initial window plus completed
compactions. New declarations also freeze `policies.worker_context_handoff_version=2`
as `worker_fragment_policy.context_handoff_version=2`: 90% occupancy in the last
complete window only advises a checkpoint; compaction then continues work in the
next window. Handoff requires windows started above the maximum and fresh current
occupancy strictly above 70%, so max=1 allows 1 complete window plus 70% of the next,
and max=2 allows 2 complete windows plus 70% of the next. Missing/stale occupancy
adds no stop; compaction alone does not hand off. Only version 2 is supported; version 1 and a missing frozen marker are rejected. Handoff
does not automatically request A. An explicit `handoff` also starts a new W; marked W's `extract` requests E evidence consultation, while `revise_h` and `review` request
serial H and A consultation respectively. These H/A/E routes require a concrete
nonblank `consultation_question` and return to a successor W job. Current jobs may retain the original native W session through the selected
Backend capability and original/successor validation; unsupported or unavailable
continuity explicitly uses a new context. Requested resume is never reported as
observed success, and unknown submission is not replayed. Pi currently does not
support `consultation_resume`: H/A/E consultation returns explicitly use
`new_context`, while same-logical-task fragment/repair/wake can append to the
original Pi session under their own gates. W uses `decision` for pre-delivery Final A. Ordinary consultation grants no assessment cycle,
main-line change, recommendation or qualification. Formal independent review
retains its exact-subject contract and neutral inputs.

`policies.worker_context` still selects `same_session` or `new_context`.
Same-session resume preserves the count and deduplicates accepted completed
compactions. On standard Codex, required `PostCompact`/`PostModelRequest` hooks supply small control
facts through the existing native helper; native stdout carries those facts and
bounded assistant text, without observing tool/input/output bodies or scanning
private rollouts. Unavailable required hooks are explicitly unsupported before
launch, rather than repeatedly starting fresh W jobs with unknown coverage. Pi uses its qualified persisted-boundary observer instead of those Codex hooks.
This describes supported launch paths, not every backend. Original-call query,
collect and reconciliation remain available. Existing owners retain time and
call state. Context renewal does not reset global active time, tokens,
steps, execution consumption, permissions or unknown usage. W role/breadth time
and cumulative-token targets remain advisory; shared and per-experiment limits
remain binding. Direct API `worker_fragment_policy=None` still disables ordinary
fragments. Next-new-node configuration revisions preserve already accepted jobs.
Protocol 29, 30, 31 and 32 instances require their matching old runtime; the current runtime
rejects them without migration or settings edits.

新标准单命令 Codex 及受支持 Pi 默认 `policies.worker_context_windows = 1`，仅接受整数 1 或 2，
冻结为 `worker_fragment_policy.max_context_windows`。同 session fragment 保留同一 W
任务和额度；窗口数为一个初始窗口加已完成压缩次数。新声明另冻结
`policies.worker_context_handoff_version=2`，对应内部 `context_handoff_version=2`：最后完整窗口
达到 90% 只提醒收口，压缩后继续工作；已启动窗口数超过冻结额度且当前新鲜占用严格大于 70%
才交新 W，不自动 A。因此 max=1 为一个完整窗加下个窗 70%，max=2 为两个完整窗加下个窗 70%。
缺失或过期占比不新增停止条件，压缩本身不交接。仅支持版本 2；版本 1 或缺冻结标记时拒绝。显式 `handoff` 同样交新 W；`revise_h`
和 `review` 分别请求串行 H/A 咨询，有职责标记的 W 还可用 `extract` 咨询 E，三条路线均须具体非空 `consultation_question`，结束后交后继 W 任务；当前任务可通过统一 `Backend` 校验后续接原生 W 会话，不支持或原连续性不可用时明确新上下文，未确认续接不当成功，也不重放未知提交。Pi 当前不支持 `consultation_resume`，H/A/E 咨询返回明确使用 `new_context`；同一逻辑任务 fragment／补填／唤醒仍可按各自门槛追加原 Pi session。W 用 `decision` 请求提交前 Final A。
咨询不消费正式 A 周期，不改变主线、推荐或资格；正式独审保持精确 subject 和中性输入。

`policies.worker_context` 仍选择 `same_session` 或 `new_context`。同 session 续接保留
窗口计数，已接受的完成压缩事件去重。标准 Codex 必需的 `PostCompact`/`PostModelRequest` hooks 通过
既有 native helper 提供小型控制事实；native stdout 只含这些事实和有界助手文字，不观察
工具/输入/输出正文，不扫描私有 rollout。缺少必需 hooks 时启动前明确 unsupported，不能在
未知覆盖下反复新建 W；Pi 改由其合格的持久化边界 observer 提供事实。该能力仅适用于受支持启动路径，不保证所有后端。时间和原调用状态
沿用既有 owner，原调用仍可 query/collect/核对。新 W 不重置全局活动时间、token、步骤、执行消费、
权限或未知量；W role/breadth 时间及累计 token 目标为建议，真实共享和单实验限制继续
有效。直接 API `worker_fragment_policy=None` 仍可禁用；下一新节点配置修订保留已接受
任务。协议 29、30、31、32 实例保留匹配旧 runtime；新版拒绝读取，不迁移、不手改版本。

Ordinary research declares or revises mechanisms through `--research JSON_FILE`
within the current W session. Bounded delivery correction keeps only its frozen
source catalog and uses `--mechanism N`; it cannot introduce live declarations.

普通研究通过 `--research JSON_FILE` 在同一 W session 声明或修订机制。
有限交付补正只使用冻结来源目录及 `--mechanism N`，不新增 live 声明。

## Experimental Pi — common research path / 实验性 Pi：公共研究路径

Pi implements the common `Backend`: ordinary H/W/A/E, current prompt schemas,
Local, project/Skill consumers and forks reuse the existing controller, execution
bridge, ledgers and original-reference rules. Use the separate
[research.pi.example.toml](../research.pi.example.toml), not the Codex example.
Provide an actually available exact `provider/model-id` and explicit
`reasoning_effort`: `off`, `minimal`, `low`, `medium`, `high`, `xhigh` or `max`.
Every resolved role and named Worker, including unused profiles, is checked.
No fuzzy model selection, substitution or thinking clamp is allowed. The launcher
is one executable without arguments (default `["pi"]`). Omit `service_tier` and
Codex-specific `agent.native` settings; native response-schema flags are unsupported.

Pi 实现公共 `Backend`；普通 H/W/A/E、当前 prompt Schema、Local、工程／Skill 消费及 fork
复用既有控制程序、执行 bridge、账本与原件引用规则。请用独立的
[research.pi.example.toml](../research.pi.example.toml)，不要套用 Codex 示例。模型须为实际可用的
准确 `provider/model-id`，thinking 从上述级别显式选择；全部解析角色与命名 Worker（含未使用者）
均校验，不模糊匹配、不替换或截档。launcher 仅一个可执行程序、不带参数，默认 `["pi"]`；
省略 `service_tier` 与 Codex 专用 `agent.native`，不支持 native response-schema flag。

### Defaults and permissions / 默认值与权限

Pi defaults to Local and `resume = {max_attempts=2, protocol="pi_rpc_v1",
usage_scope="turn", output_repair=true, output_only=true, execution_wake=true}`.
Leave this policy omitted to use current defaults; do not handwire Codex protocol
IDs. This supplies finite same-session output repair and original managed-execution
wake, not Codex's default automatic scientific recovery. The shared cadence remains
`same_session`, W `max_context_windows=1`, and finite H/W/A/E node targets inherited
from the original global ceilings when none are declared. Explicit recovery,
fragment and other policies remain subject to their common owners and actual
capabilities; they cannot infer permission from a completed transport.

Pi 默认 Local 及上述有限 `pi_rpc_v1`、turn 计量策略；省略策略表即可使用当前默认值，
不要手工套用 Codex 协议 ID。默认提供同 session 有限输出补填与原受管执行唤醒，不是 Codex
默认的自动科学续工。公共节奏仍是 same_session、W 一个上下文窗口；未声明节点目标时，
H/W/A/E 使用原有限全局上限作为有限参考目标。显式恢复、fragment 及其他策略继续由公共
owner 按真实能力判断，输运完成不授予权限。

All four permissions must be explicit. Default/implied Local and projects require
`workspace-write` before preparation effects. For ordinary read-only scientific
work, explicitly choose `workspace="read-only"` **and** `[executor] mode="external"`.
This does not enable tool-less auxiliary translation or stronger NoNetwork.
The resource profile selects the required web/subagent declarations:

| Pi resource mode | Required explicit permissions | Native resources |
| --- | --- | --- |
| omitted or `closed` | `network=true`, `web="disabled"`, `subagents=false`; workspace as above | Controlled resources; nonempty native `packages` refused. |
| `configured` | `network=true`, `web="live"`, `subagents=false`; workspace as above | Trusted configured extensions and installed packages, cache-only. Extension behavior is not fully constrained; complete native authorship is unsupported. |

四项权限须全部显式声明。默认／隐含 Local 与工程在准备副作用前要求 workspace-write；
普通只读科学工作须同时显式选择 read-only 和 external，不等于支持无工具辅助翻译或更强禁网。
省略 `agent.native.resource_mode` 或显式 `closed` 保留原受控 profile：network=true、
web=disabled、subagents=false，非空原生 packages 会拒绝。显式 `configured` 必须同时声明
network=true、web=live、subagents=false，workspace 同上。显式 mode 授权可信扩展，false 表示
不请求 Backend 原生委派；不保证扩展不能自行创建子调用或进程。实际 `effective.subagents`
为 unknown（null），enforcement 为 `extension_dependent`。Pi 原生子代理能力仍 unsupported，
小任务由 W 自己完成，大或复杂证据由 W 自主考虑 E；closed 仍须 web=disabled。

```toml
[agent]
backend = "pi"
model = "provider/exact-model-id"
reasoning_effort = "high"

[permissions]
workspace = "workspace-write"
network = true
web = "disabled"
subagents = false
# executor omitted: Local; use the full example for paths and finite budgets.
```

To load already installed native packages, replace the permissions above and add
this Pi-only table in the complete declaration; do not duplicate existing TOML tables:

```toml
[agent.native]
resource_mode = "configured"

[permissions]
workspace = "workspace-write"
network = true
web = "live"
subagents = false
```

要加载已安装原生包，在完整声明中加入上述 Pi 专用表并替换原权限表，不要重复 TOML 表。
只读科学工作改为 `workspace="read-only"`，并显式声明 `[executor] mode="external"`。

Linux mounts are required with no unsandboxed fallback. The framework-owned top
level is read-only; workspace-write opens `work/`, `artifacts/`, `experiments/`,
current W code and declared project slots. Exact originals, published metadata,
selected live assets and known aliases remain read-only after write overlays.
Published `work/` controls are protected, but absent names within that writable
slot cannot be reserved by mounts. Original `HOME`, `PI_CODING_AGENT_DIR` and auth
stay unchanged for Native, with only narrow runtime/session writable exceptions.
The existing scientific Local child gets limited runtime, empty HOME and no native
auth/session/control storage; it does not require a Codex binary. Same-user access
remains a soft boundary.

必须有 Linux 挂载能力，无无沙箱回退。框架顶层只读，只开放 work/artifacts/experiments、
当前 W 代码及声明工程槽；写覆盖之后，准确原件、已发布元数据、所选 live 资产及已知别名
仍只读。work 内已发布控制文件受保护，但挂载不能保留该可写槽内不存在的名称。原 HOME、
PI_CODING_AGENT_DIR、认证只供 Native 沿用，只开放窄 runtime/session 可写例外。既有 Local
科学子进程只有有限运行时、空 HOME，没有原生认证／session／控制存储，不依赖 Codex
二进制；同用户访问仍是软边界。

### Native resources / 原生资源

Live Skill schema v3 freezes selection/source bindings, not asset bytes, copies
or a hash registry. Native `get_commands` must prove each exact selected manual
loaded valid native frontmatter, with original local/temporary/top-level provenance
and its own base directory before the sole prompt. Python YAML metadata alone is
not loading proof. Live assets and aliases remain read-only. Exact source-original
overlays are applied last, after write grants including the native auth directory.
In `closed`, arbitrary user extensions are unsupported because hooks can mutate
tools/permissions/model calls; only the known read-only framework observer is
admitted by exact command-source proof and private startup framing. In
`configured`, additional native extension commands are allowed, while exact selected
Skill bindings and the framework observer's source/start proof remain required.
The observer itself registers no tool/provider and adds no model call, prompt or continuation.

Live Skill schema v3 冻结选择与来源，不冻结或复制字节、不建 hash registry。唯一 prompt
前由 native get_commands 核对每份准确手册、有效原生 frontmatter、原 local/temporary/top-level
来源及自身 base directory；Python YAML 元数据不等于加载证明。live 资产与别名保持只读；
准确来源原件在可写授权（包括 native auth 目录例外）之后最后只读覆盖。closed 因 hook 可改变
工具／权限／模型调用而不支持任意用户扩展，只准入有准确 command 来源及私有启动帧证明的
已知只读框架 observer。configured 允许额外扩展 command，仍须校验所选 Skill 的准确绑定及
框架 observer 的来源／启动证明。observer 自身不注册工具／provider，不新增模型调用、prompt 或续接。

`<agent-dir>` means inherited `PI_CODING_AGENT_DIR`, or `$HOME/.pi/agent` when
unset; `<workspace>` is the exact scientific task workspace. Before any native
startup, both `<agent-dir>/settings.json` and `<workspace>/.pi/settings.json`
require absent/empty `packages` in `closed`. In `configured`, the original SDK
resolves and loads already installed packages from those settings; the framework
does not install or refresh packages, rewrite settings, or copy authentication.
Only `--no-extensions` is removed: `--offline`, `--no-approve`, `--no-context-files`,
`--no-skills`, `--no-mcp`, `--no-prompt-templates` and `--no-themes` remain.
Package Skills, prompts and themes are not automatically imported; explicit
framework-selected Skills still use the common selection and exact-source checks.
Native package loading errors remain errors. A command inventory establishes
required bindings, not that every configured package loaded successfully.
Both profiles reject custom `npmCommand`. The four exact paths `<agent-dir>/SYSTEM.md`,
`<agent-dir>/APPEND_SYSTEM.md`, `<workspace>/.pi/SYSTEM.md` and
`<workspace>/.pi/APPEND_SYSTEM.md` must be absent. `--no-context-files` does not
suppress those prompt sources; offline mode does not disable package resolution.
The framework neither edits user configuration nor copies auth. `PI_OFFLINE`
controls automatic resource fetch/catalog activity, not explicit provider or bash
network access: network stays true, and `web=disabled` is not network isolation.

`<agent-dir>` 指继承的 PI_CODING_AGENT_DIR，未设时为 `$HOME/.pi/agent`；`<workspace>`
指准确科学任务工作区。closed 在任何 Native 启动前要求两处准确 settings.json 的 packages
缺失／为空。configured 将已安装包交原 SDK 按原配置解析／加载，不安装或刷新，不改写 settings
或复制认证。仅去掉 --no-extensions；--offline、--no-approve、--no-context-files、--no-skills、
--no-mcp、--no-prompt-templates、--no-themes 仍保留。包自身的 Skills、prompts、themes 不自动
导入；框架显式所选 Skills 仍走统一选择与准确来源校验。包加载失败保留原生错误，command
清单只校验所需绑定，不证明全部包已成功加载。例如已安装的 `npm:pi-web-access`、
`npm:pi-guard-sandbox`、`npm:pi-gauge` 可保留在原全局 packages 中，无须另改全局配置。
两种 profile 都拒绝自定义 npmCommand；上述四个准确 SYSTEM.md／APPEND_SYSTEM.md 路径均须不存在。--no-context-files 不抑制这些
隐藏 prompt，offline 不禁用 package resolution；框架不修改用户配置或复制认证。
PI_OFFLINE 只管理自动资源获取／目录活动，不限制显式 provider 或 bash 联网；network 仍为
true，web=disabled 不构成禁网。

### Observations, recovery and limits / 观测、恢复与限制

Qualified metadata uses the actual persisted native user-entry ID, exact session,
window and freshness; compactions are unique new appends after the retained cursor,
not matches to equal old summaries. Occupancy is an SDK estimate, not exact tokens
or cumulative billing. Native null after compaction stays unavailable until new
usage. Exhaustive `native_calls` is unsupported because auxiliary summaries,
retries and warming are not fully receipted; persisted response counts are only
lower bounds, including for optional caller policies. Private native IDs, paths
and nonces are not public telemetry or scientific evidence.
In both profiles the framework observer covers the main Pi session only and
retains the common 90% notice / strictly above 70% handoff rule. Configured
extensions may make additional calls whose coverage is unknown; observed tokens
remain lower bounds, without a complete provider-call or author census.

合格元数据使用真实持久化 native user-entry ID、准确 session／窗口／新鲜度；压缩取保存
cursor 后唯一新追加项，不按相同旧摘要匹配。占用为 SDK 估计，不是准确 token 或累计账单；
压缩后 native null 在新用量前保持不可用。辅助摘要、重试与 warming 回执不完整，完整
native_calls 不支持，response 计数仅为下界，可选调用者策略也不能假装完整。私有 native ID、
路径、nonce 不进入公开遥测或科学证据。
两种 profile 的框架 observer 都只覆盖主 Pi session，上下文／压缩计量沿用公共的 90% 提醒及
严格超过 70% 才交接规则。configured 扩展的额外调用覆盖未知，观测 token 保留下界，不能
据此声称完整 provider-call 或作者普查。

The parent synchronously publishes existing control budget/review/admission facts
before acknowledging required metadata. Delivery faults stop required-policy
work. Independent bounded advice appends at most 200 UTF-8 bytes at an existing
top-level tool-result boundary, once per notice kind per logical node across resumes,
without another LLM turn or changes to original result/error/details/usage.
Read-only marker failure honestly leaves advice unavailable. Completion requires
exact profile binding, idle settlement, RPC drain, clean exit and group cleanup.
Current response JSON Schema is prompt-only; final original UTF-8, including bad
JSON, stays intact for common validation/finite repair. Unconditional post-exit
billing reconciliation retains auxiliary usage, lower bounds and unknown coverage,
independently of metadata success. Exact-session resume preserves previous spend
and windows. Native session contents may change; stable identity and backend
bindings govern resume, while accounting gaps remain unknown (see the
[recovery contract](RESEARCH_LOOP_CONTRACTS.md)). Dead PIDs never authorize replay.
`caller.unknown` and any unknown external
submission require same-call reconciliation, never automatic resubmission.

父控制层在确认必需元数据前同步发布既有预算／复核／准入事实；交付故障停止依赖该策略的
工作。独立建议在既有顶层 tool result 边界追加最多 200 UTF-8 字节，按种类每逻辑节点一次，
跨 resume 保留，不增加 LLM turn 或改变原结果／错误／details／用量。只读 marker 失败时
如实保留建议不可用。完成须准确 profile、空闲终态、RPC 排空、正常退出及进程组清理。
当前 JSON Schema 只随 prompt 发送，最终原始 UTF-8（含坏 JSON）完整保留，公共校验与
有限补填继续生效。退出后无条件核对账单，独立于元数据成功与否，保留辅助用量、下界和
未知覆盖。同 session 续接保留既有消费和窗口；会话内容可变，按稳定身份及后端绑定接续，
计费缺口保持 unknown，见[恢复合同](RESEARCH_LOOP_CONTRACTS.zh-CN.md)。PID 死亡不授权重放。
caller.unknown 与任何未知外部提交须同调用核对，绝不自动重提。

Common formal-review, execution-authority, resource, selection, audit, delivery
and observer policies are not blanket-vetoed by Pi. Their common owners validate
and gate actual use. New supported `closed` Pi preparations freeze controlled
single-native-session authorship support. `configured` freezes complete native
authorship as false because extension participants and calls are not fully covered;
it gains no exact-subject formal qualification that requires complete proof.
Actual retained launch/resource and
observer-source/start proof, accepted prompt, persisted user/branch/response facts,
terminal delivery and stopped-writer process/prefix evidence establish the native
participant. A capability declaration or session-bound event alone is insufficient.
The SDK session is the stable author: repair/resume, new attempts or different
private homes do not make the same actor an independent reviewer. Retained
validation consumes this proof without reopening live native sessions; it remains
usable after private-home deletion and in framework historical-task forks,
including nested forks. This does not authorize live resume without its originals.
Existing frozen false capabilities and old unsupported receipts remain unsupported.

Ordinary W/A/Final A does not require complete authorship or strict independence
proof. Current supported historical-task forks do not promise migration or
resumption of older paused or not-yet-dispatched forks.

The existing formal A workflow can qualify only the exact subject, under its
explicit frozen review policy, with complete author and reviewer evidence,
disjoint participants and a sealed `pass`; pass text alone cannot qualify
self-review. Formal A uses the ordinary native lifecycle with declared permissions
and read-only exact-subject inputs, not the auxiliary tool-less read-only service.
The proof covers actual controlled native participants, not arbitrary shell
activity; same-user access remains a trusted soft boundary. No automatic verdict,
execution permission or scientific-study qualification follows. Chat, transport
completion and tests are not scientific approval. Exhaustive provider-call census,
stronger NoNetwork, tool-less auxiliary read-only calls, service tier and native
response-schema flags remain unsupported. This is not a claim of full scientific
qualification or backend parity. See the [architecture](ARCHITECTURE.md).

Pi 不一概否决公共正式审查、执行授权、资源、选择、审计、交付及 observer 策略；真实使用由
公共 owner 校验及把关。新受支持 closed Pi 准备冻结受控单原生 session 作者证明能力；
configured 因扩展参与者与调用覆盖不完整，冻结完整原生作者能力为 false，不能获得要求完整
证明的准确 subject 正式资格。实际留存的
启动／资源、observer 来源／启动证明、已接受 prompt、持久化 user／branch／response 事实、
终态交付及已停止 writer 的进程／前缀证据共同确认原生参与者；能力声明或 session-bound
事件本身不够。SDK session 是稳定作者，补填／续接、新 attempt 或不同私有 home 不会把
同一 actor 变成独立审查者。留存校验消费这些证明，不重新打开 live native session；私有
home 删除后及框架历史任务 fork（含嵌套 fork）中仍可使用，但不授权缺失原件时的 live
resume。既有冻结 false 能力与旧 unsupported 回执仍不受支持。

普通 W/A/Final A 不以完整作者或严格独立性证明为硬门槛。当前受支持历史任务 fork 不承诺
旧暂停或尚未首派工 fork 的迁移续跑。

既有正式 A 工作流须有显式冻结审查策略，仅在准确 subject 的作者与审查者证据均完整、
参与者集合不相交且封存 verdict 为 `pass` 时赋予该 subject 审查资格；仅有 pass 文本不能
使自审合格。正式 A 使用普通原生生命周期、声明权限与准确 subject 的只读输入，不走
无工具辅助只读服务。证明只覆盖实际受控原生参与者，不覆盖任意 shell 活动；同用户访问
仍是可信软边界。这不自动产生 verdict、执行许可或科学研究资格，聊天、输运完成、测试
也不构成科学批准。完整 provider-call 普查、更强禁网、无工具辅助只读、service 档位及
native response-schema flag 仍不支持。不宣称科学完整资格或后端完全等价，详见
[架构](ARCHITECTURE.zh-CN.md)。

## Scientific Python dependencies / 科学 Python 依赖

The control interpreter running `scid-research` and the scientific interpreter
running experiments can be different existing environments. For Local or Cov,
declare task dependencies inside the existing executor configuration:

控制框架和运行科学实验的 Python 可以是不同的既有环境。Local 或 Cov 的任务依赖在既有
执行器配置中声明，例如：

```toml
[executor]
mode = "local"
[executor.config]
executor = "local_execution:command"
[executor.config.configuration]
run_argv = ["/absolute/existing/scientific-env/bin/python"]
max_seconds = 120
python_requirements = [
  { requirement = "numpy>=1.26,<3", import_name = "numpy" },
  { requirement = "scikit-learn>=1.4,<2", import_name = "sklearn" },
]
```

`config-check`, `config-inspect --config` and `prepare` check these declarations
through the selected factory's configure callback. The interpreter is exactly
`run_argv[0]`, not the control Python; it must be an absolute Python executable
path. Local accepts only supported interpreter flags after it (`-B`, `-I`, `-E`,
`-s`, `-u`, `-S`), without a script or wrapper. Cov accepts its supported Python
runner invocation. The distribution name and import name are separate: for example,
`scikit-learn` installs the `sklearn` module.

这些声明由所选 factory 的配置入口在 `config-check`、`config-inspect --config`、
`prepare` 时检查。解释器准确取自 `run_argv[0]`，须为 Python 可执行文件的绝对路径，
不会改用控制 Python。Local 后续只接受上述解释器参数，不接受脚本或包装器；
Cov 使用其支持的 Python runner 调用。发行包名与导入名分别填写，例如
`scikit-learn` 对应 `sklearn`。

The declaration permits at most 32 entries; each requirement is a PEP 508 string
of at most 512 characters and each import name is a module path of at most 128
characters. Version constraints and environment markers use the target Python.
Extras, direct URLs and extra markers are unsupported. The bounded probe shares a
20-second deadline, limits each output stream to 32 KiB, and checks installed
metadata plus imports in isolated mode with numerical thread counts set to one.
It reads no task datasets and runs no task script. Missing distributions, version
mismatches and failed imports produce field-specific configuration errors. It
does not install packages, create an environment or change the interpreter.

最多声明 32 项；每项 PEP 508 requirement 最多 512 字符，import_name 为最多 128 字符的
模块路径。版本及环境 marker 按目标 Python 判定；不支持 extras、直接 URL 和 extra marker。
探针共用 20 秒时限，每路输出最多 32 KiB，数值库线程设为 1，通过隔离模式检查已安装元数据和导入。
它不读任务数据、不运行任务脚本；缺包、版本不符、导入失败会报到具体配置字段，
不会安装包、建环境或换解释器。

This is an installed-package preflight, not qualification of native permissions,
mounts, task-relative imports or scientific execution. Packages available only
through user-site or `PYTHONPATH` do not satisfy this isolated check. A standard
Local native attempt records the selected Python's prefix and base prefix once
in its private permission snapshot and mounts the necessary directories read-only,
subject to the existing private-path overlap rules. Querying retained results and
resuming an existing attempt do not rerun the dependency probe. Omitting the
declaration or using an empty list performs no dependency probe; other executors
retain their own preflight contracts. Installation checks only application/plugin
dependencies, so installing Cov alone does not declare arbitrary task libraries.

该预检只检查隔离模式下的已安装库；用户 site 或 `PYTHONPATH` 独有的包不算通过。
原生权限、挂载、任务相对导入及科学执行仍须各自成立。标准 Local 的新 native attempt
会把所选 Python 的 prefix 和 base prefix 一次记录在既有私有权限快照中，
按原有私有路径重叠规则只读挂载所需目录。读取保留结果、恢复已有 attempt 不重新检查依赖。
省略声明或使用空数组不做依赖探测；其他执行器沿用各自的预检合同。
应用安装只检查框架和插件依赖，安装 Cov 本身不会声明任意任务的科学库。

## Response schema transport / 输出 Schema 传输

Ordinary Codex tasks request one JSON final response through a short prompt from
the first attempt, including tasks with an older frozen strict schema. They do not
send `--output-schema` or append that old schema. Protocol requests carry the current
minimal response schema. Pi includes the owner-provided current response JSON
Schema in its sole prompt, without a native schema flag; original final UTF-8,
including invalid JSON, is retained for common validation and finite repair.
The original task and schema files remain unchanged;
outbound instructions state the current required fields and conditional breadth
rules. Other scientific content stays open. Exact inputs, execution permissions,
budgets and delivery qualification retain their own checks.

This is a prompt instruction, not a provider JSON-mode guarantee; Codex `--json`
describes its event stream. Invalid required output uses finite output-only repair.
An actual formal independent-review task retains its separate strict contract;
schema shape alone never makes an ordinary task a formal review.
`native_schema_transport` records the actual invocation mode separately from frozen
launcher capabilities.

Scientific final-output parsing tolerates one leading BOM or one whole plain/JSON
Markdown fence, retaining the original bytes. Prose and multiple objects still use
bounded repair. Required-field and author-owned delivery-layout errors can return
to the same author under its frozen repair policy. Managed originals are retained
and referenced directly; repairing a duplicate copy grants no new scientific run.

普通 Codex 任务首轮就通过简短提示要求一个 JSON 最终回答，已有旧严格 Schema 的普通任务也
一样，不发送 `--output-schema`，不追加旧 Schema 全文。Protocol 请求携带当前最小回答
Schema。Pi 把 owner 提供的当前 JSON Schema 放入唯一 prompt，不传 native schema flag；
最终原始 UTF-8（含非法 JSON）完整保留，供公共校验与有限补填。原 task 和 Schema 文件
保持不变；实际出站说明明确当前必填项和条件广度规则，
其余科学内容开放。准确输入、执行权限、预算和交付资格仍分别校验。

这是提示约束，不是服务端 JSON-mode 保证；Codex `--json` 描述事件流。
必需输出不合格走有限的仅输出补填。真正的正式独审任务保持独立严格合同，不能根据
Schema 形状把普通任务当作正式独审。`native_schema_transport` 单独记录实际调用方式，
不改写冻结的 launcher 能力。

科学最终输出容忍一次起始 BOM 或完整唯一的普通/json Markdown 围栏，原始字节不改；夹杂
散文或多个对象仍有界补正。必填字段及作者自有交付布局错误沿冻结补填策略交回同一作者。
受管原件持续保留并直接引用，整理重复副本不授权新实验。

For an already-recorded native format rejection, the existing enabled recovery
policy can negotiate once only when the complete original stream positively proves
rejection before model or tool activity. It consumes one `max_retries` slot without
service backoff and retains the exact task, profile, home, permissions and budgets.
The ordinary retry uses the current short JSON instructions. Unknown external
execution, other HTTP 400 errors, ordinary prose, authentication/quota errors,
incomplete streams or scientific activity do not authorize resubmission.

对于已记录的原生格式拒绝，只有完整原流明确证明拒绝发生在模型与工具活动之前，才可按
已启用的恢复策略协商一次，消费一个 `max_retries` 名额，不加服务退避；原任务、profile、
home、权限和预算保持。普通任务随后使用当前简短 JSON 说明。未知外部执行、其他 400、
普通文字、认证/配额错误、不完整流或已有科学活动均不授予重提许可。

Protected FINAL and closeout may negotiate only this same-task format failure,
retaining their exact original claim and finite limits. Their ordinary scientific
work-recovery exclusions remain. Special author-closeout/startup-successor/wake
grants do not acquire broad automatic retry, nor does an already-started narrow
output/work repair acquire a new full-task recovery grant. Original receipts,
missing meters and stopped schedules remain traceable; positive format facts are
rechecked from the original stream rather than rewriting an old failure diagnosis.

protected FINAL 与 closeout 仅可协商同一任务的这一格式失败，保留精确原 claim 与有限上限，
普通科学续工限制不变。专用 author-closeout/startup-successor/wake 授权不因此取得广泛自动重试；
已经启动的窄输出/工作补填也不获得新的完整任务续工许可。原回执、缺失计量与停止安排保持可追溯；
格式拒绝事实从完整原始流重新核对，不改写原失败诊断。

## Automatic recovery / 自动接续

New preparations for capable Backends, including standard single-executable Codex
and Pi, supply `resume_policy.automatic_recovery` through the same defaults used
by TOML, CLI and the webpage. TOML declares it as `[policies.resume.automatic_recovery]`;
the existing CLI `--worker-resume-policy /absolute/resume.json` accepts it inside
the complete resume object. In the webpage, edit the same
`[policies.resume.automatic_recovery]` table under Advanced settings (TOML).
`max_attempts` controls the total automatic recovery cap; the same table retains
the per-action limits.
Saved changes affect future nodes, preserving the current accepted task and its receipts.
Output repair and recovery of unfinished work remain separate permissions.

支持自动恢复的 Backend（包括标准单可执行程序 Codex 和 Pi）新准备入口通过 TOML、CLI、网页共用的默认引擎提供
`resume_policy.automatic_recovery`。TOML 对应 `[policies.resume.automatic_recovery]`；
CLI 的既有 `--worker-resume-policy /absolute/resume.json` 在完整恢复对象内接受该字段。
网页在“高级设置（TOML）”中编辑同一 `[policies.resume.automatic_recovery]` 表。
运行中保存仅影响未来节点，不改写当前任务与原回执；
输出补填与未完成任务接续仍是不同许可。

TOML and form normalization fill omitted `protocol` and `usage_scope` from the
selected Backend in a partial `[policies.resume]` object. Its `max_attempts` is
still required; explicit protocol conflicts are rejected. These defaults do not
qualify an unsupported launcher or change an existing frozen task.

TOML 与表单共用的规范化会为部分填写的 `[policies.resume]` 对象，按所选 Backend 补齐省略的
`protocol` 与 `usage_scope`。该对象仍须明确填写 `max_attempts`；显式协议冲突会拒绝。
这些默认值不为不受支持的启动器授予能力，也不修改已有冻结任务。

Changing Backend in the webpage refreshes only the selected provider's model
suggestions and option schema, retaining the draft without previewing or creating
research. An exact built-in launcher spelling follows the new Backend; custom
launcher arguments remain explicit and are validated separately.

网页切换 Backend 时，只按需刷新所选后端的模型建议与选项，保留草稿，不预览或创建研究。
精确使用内置默认拼写的启动命令随新后端切换；自定义启动参数保持显式声明，另行校验。

| Field / 字段 | Default / 默认 | Meaning / 含义 |
| --- | --- | --- |
| `enabled` | `true` | Independent recovery permission; boolean. / 独立自动接续许可，布尔值。 |
| `max_attempts` | `8` for new defaults / 新默认为 `8` | Total automatic retries plus continues across native failures, work completion and format fallback; null or integer 0–100. Existing nonempty explicit policies omitting it retain null. / 原生失败、工作补填与格式降级共用的自动 retry 与 continue 总次数；null 或整数 0–100。显式非空旧策略省略时保留 null。 |
| `max_continues` | `8` | Cumulative same-task early-stop or incomplete-W continuations; integer 0–100. / 同一任务提前停止或 W 交付不足的累计接续次数，整数 0–100。 |
| `max_retries` | `8` | Cumulative same-task service retries, including at most one format fallback; integer 0–100. / 同一任务服务重试累计次数，含至多一次格式降级，整数 0–100。 |
| `initial_backoff_seconds` | `5` | Initial finite nonnegative service backoff. / 首次有限非负服务退避秒数。 |
| `max_backoff_seconds` | `300` | Finite nonnegative per-backoff limit. / 每次退避的有限非负秒数上限。 |
| `jitter_fraction` | `0` | Jitter fraction, 0–1. / 退避抖动比例，0–1。 |
| `max_wait_seconds` | `1800` | Cumulative actual service-wait limit, finite and nonnegative. / 实际累计服务等待的有限非负秒数上限。 |

The time values must satisfy `initial_backoff_seconds <= max_backoff_seconds <=
max_wait_seconds`. Zero attempts disable the corresponding recovery action.
Fresh defaults cap the combined automatic retries and continues at eight, including
native failures, work completion and format fallback. Zero disables all automatic modes
without changing their admission scopes. With the total cap
active, the combined consumed count also advances backoff: 5, 10, 20, 40, 80, 160,
300 and 300 seconds, totaling 915 seconds without provider hints. Hints may extend
this schedule within the single-wait and cumulative bounds. An explicitly supplied
nonempty automatic-recovery table that omits `max_attempts` retains null and the
historical independent action counters and backoff cadence; other omitted fields default.
`enabled = false` disables recovery. An explicit older resume policy that omits this
table gains no automatic-recovery permission. `output_only` still grants only
output repair; it does not grant scientific work recovery. Custom/multi-argument
Codex launchers and the protocol backend do not gain this recovery capability. Original
model profiles, permissions, native home and authentication remain unchanged;
authentication is neither copied nor redirected.

时间参数须满足上述大小关系；分动作次数为 0 禁用对应动作。新默认的自动 retry 与 continue
合计最多八次，原生失败、工作补填和格式降级共用此额度；总次数为 0 禁用所有自动模式，不改变其准入范围。
总次数也推进退避顺序，正常八次等待合计 915 秒；服务商提示可延长等待，但不绕过单次或累计上限。
显式非空旧自动策略省略 `max_attempts` 时保留 null、分动作计数与退避节奏；其他省略字段采用表中默认值。
`enabled = false` 禁用策略。显式旧 resume 策略未声明此表时不新增自动
接续许可。`output_only` 仍只授权输出补填，不授权科学工作接续。自定义、多参数
Codex launcher 及 protocol 后端不因此获得该恢复能力。原模型 profile、权限、native home 和认证来源保持，
不复制或重定向认证。

Recovery retains the native diagnostic category: temporary, incomplete, permanent
or unknown. When the original session is safely available, each category may use
bounded backoff under the same task; permanent or unknown classification alone
does not force an immediate stop. This does not repair authentication, quota or
configuration. Native structured codes and bounded error envelopes remain diagnostic
facts; permanent quota evidence still takes precedence over generic capacity signals.
The Pi adapter recognizes exact `fetch failed` at native failure boundaries as a
connection error. Ordinary agent/tool text never supplies failure classification.
A startup retry without an original session still requires explicit native temporary
failure and non-acceptance evidence; missing identity or decoded acceptance prose
is insufficient. Existing-session retry requires no `not_accepted` claim.
Unknown external executions require reconciliation of the exact original call and
are never automatically resubmitted. Cleanup, immutable identity, permissions,
pause and shared budgets remain mandatory gates. Known completion is not reopened
as a native failure. Only the separately verified format negotiation above may
downgrade transport; recovery never supplies scientific qualification or extra permissions.
Known completed turns with missing or invalid required output use existing finite
output repair; legal fragments retain their existing continuation semantics.
A completed W screen return with valid required JSON may separately continue the
original full W task when its exact `work-evidence.json` assessment is `incomplete`.
This uses the independent automatic permission, original session and exact response,
outcome and evidence bindings, and shares `max_continues` with early-stop recovery.
Required-format and conditional breadth omissions use output repair; that permission alone
does not authorize scientific work. A repaired valid response can independently
qualify for work continuation. Valid partial findings and explicit blocks keep
their declared stage intent; `decision` requests pre-delivery Final A. Complete or inapplicable delivery adds no
recovery action. An enforced host review keeps its existing priority. Exhausting the
automatic continue cap or the original budget records a verified partial exit rather
than inventing completed findings or refunding consumption.

恢复保留 temporary、incomplete、permanent 或 unknown 原生诊断。若原 session 可安全续接，
各类失败均可在同一任务内有限退避恢复，不仅凭 permanent/unknown 分类立即停止；等待本身
不能修复认证、配额或配置。结构化错误码与有界错误信封保留诊断意义，永久 quota 证据仍优先
于泛化容量信号。Pi 适配器仅把原生失败边界的精确 `fetch failed` 识别为连接故障，
不扫描普通 agent/tool 正文。既有 session 重试不要求 `not_accepted`；无 session 启动重试
仍须原生临时故障与明确未接受证据，不能凭缺身份或解码出的 acceptance 正文授予重放。
未知外部执行必须先对账原调用，绝不自动重提。清理、不可变身份、权限、暂停和共享预算门槛
保持，已知完成不因失败恢复重新执行；仅上述单独核验的格式协商可降级传输。
自动接续不构成科学资格或额外执行许可。
已知完成 turn 缺少或给出无效必需输出时，走既有有限输出补填；合法 fragment 保持既有阶段接续语义。
已完成 W screen 返回的必需 JSON 合法，但精确 `work-evidence.json` 判定仍为 `incomplete` 时，
可依靠独立自动许可续接原 W 完整任务；沿用原 session，绑定原返回、outcome 和证据快照，
与提前停止共用 `max_continues`。必需格式和条件广度声明缺失走输出补填，该许可本身不授权科学续工；
补成合法返回后可另行符合独立续工条件。有效 partial 和明确障碍保留作者的 stage_intent；
`decision` 请求提交前 Final A，
其他交接或咨询沿其明确路线。完整或不适用的交付判定
不追加恢复动作。宿主强制边界仍优先于科学续工，不把上下文续接变成自动 A。continue 次数或原预算耗尽记录可核验的 partial 出口，
不虚构已完成 finding，不退还已用预算。

Both counters and cumulative wait belong to the original task's attempt chain;
progress, restarts and configuration revisions do not reset them. The existing
controller writer performs monotonic waits and persists the reason, original
attempt, counters, actual wait and UTC next-attempt time. Downtime and idle time
without a controller add no service wait; an unmeasured crash tail stays uncertain.
An elapsed UTC deadline only permits re-arbitration under the original budget,
identity and permission gates. Pause stops service waiting without launching a
replacement. Each wait takes one fresh budget snapshot and maintains the shared-time
deadline locally using the original active/elapsed clock basis. It still checks pause
each second, measures service wait monotonically and performs fresh launch admission.
The webpage shows recovery reason, scope, next attempt, separate
counts, accumulated wait and limitations; service waiting is not scientific failure.
Server retry hints are not shortened to bypass a bound: a hint exceeding the
per-backoff limit or remaining cumulative wait stops recovery.

两个计数及累计等待归属原任务的 attempt 链，推进、重启或配置修订均不清零。既有 controller
writer 用单调时间等待，持久保存原因、原 attempt、分别计数、实际等待及 UTC 下次尝试时间。
停机与没有 controller 的空闲不新增服务等待，崩溃时未测尾段不伪造精确计量。UTC 到期只获得
在原预算、身份和权限门槛下重新仲裁的资格。暂停会停止服务等待，不启动替代工作。网页显示
原因、范围、下次尝试、分别计数、累计等待及限制；服务等待不表示科学失败。每次等待只取一次
新预算快照，按原 active/elapsed 时钟口径本地维护共享时间截止；仍每秒检查暂停、用单调时间
计服务等待，并在启动前重新准入。
服务端 retry hint 超过单次退避上限或累计剩余等待时停止恢复，不截短提示来绕过上限。

Live or unresolved native processes and running/unknown managed calls prevent a
replacement native attempt; use the existing original-call reconciliation paths.
Recovery never resubmits unknown external work or refunds consumption. Formal
completion and explicit waits retain their existing boundaries. Automatic recovery
of scientific work is disabled for protected FINAL/closeout; their existing output-repair and completion
policies remain binding. Their exact original claim may use the bounded format
negotiation described above. Other failed or unknown closeout originals remain unsealed with
a specific same-original recovery diagnostic; unavailable fragment continuation or
output repair retains its own reason, without acquiring automatic work permission.
No background recovery runs without a controller. This configuration changes no
required scientific output or protocol version and adds no general clock repair.

原 native 仍存活或身份未核清、受管调用 running/unknown 时不启动替代 native；须用既有原调用
核对入口。恢复不重新提交未知外部工作、不退款。正式完成和显式等待保留原边界；protected
FINAL/closeout 禁用自动科学续工，原输出补填及完成策略继续有效；精确原 claim 可使用上述有限格式协商。
其他失败或未知收口原任务保持未封存，
给出核对同一原件的明确诊断；fragment 续接或输出补填不可用保留准确原因，不新增自动续工许可。
没有 controller 就不自行后台执行。
本配置不新增必填科学输出或提升协议，也不提供通用科学预算时钟修复。

## Declared projects / 声明工程

Use an explicit TOML declaration to work at an existing project's original paths.
Omitting `[project]` preserves the existing `code/` workflow. Configure the
corresponding tables in a complete research declaration, with a supported Codex
or Pi launcher and explicit workspace permission (the fragment uses Codex):

通过显式 TOML 声明，可在已有工程的原路径工作；省略 `[project]` 时沿用原有 `code/`
流程。在完整科研声明的对应表中配置以下字段，使用受支持 Codex 或 Pi launcher 并明确工作区权限（片段使用 Codex）：

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

Use an independent ordinary clone dedicated to this research. Do not commit,
switch branches or move HEAD during the research. `base_root` must be the exact
canonical absolute root of that ordinary Git worktree, with no symlink ancestors;
relative paths, `~`, bare repositories and linked worktrees are unsupported.
`editable` names directories, including empty roots;
the other arrays default to empty. Roots must be canonical relative directory
paths, without `.`, `..`, backslashes or `.git` components. Duplicates and any
ancestor/descendant overlap within or across the arrays are rejected. The base
cannot overlap instance, public, native-home or protected runtime paths; declared
mounts cannot overlap exact bound scientific inputs.

请使用研究专用的独立普通 clone；研究期间不要提交、切换分支或移动 HEAD。`base_root`
必须是该普通 Git 工作树准确的 canonical 绝对根路径，全部祖先不得为符号链接；不支持
相对路径、`~`、bare 仓库或 linked worktree。`editable` 指定目录，允许空根目录，
其余数组默认空。各项必须是规范相对目录，不含 `.`、`..`、反斜杠或 `.git` 组件；
数组内及数组间重复、祖先/后代交叠均拒绝。基础工程不得与实例、公开材料、native home
或受保护运行时路径交叠；声明挂载目录不得与准确绑定的科学输入交叠。

Preparation freezes these declarations and the base's object format and HEAD
commit. SHA-1 and SHA-256 bases are supported. Existing-instance edits and forks
retain that exact identity. The whole base directory is mounted read-only at its
original path, including untracked and ignored files; its root `.git` directory
is masked. The frozen Git identity covers tracked state, while the remaining
base contents are the host's current files. Frozen HEAD is a code identity, not
an adversarial content-integrity check. Bounded Git index/tree enumeration
rejects staged changes and tracked symlinks/gitlinks. Ordinary Git worktree checks
reject dirty tracked sources; Git's normal filters, assume-unchanged and
skip-worktree flags apply. Control does not open, hash or stat every base file
for a namespace launch. Git metadata verification still scales with tracked
entries. W and runs exclude editable, scratch and output roots from the dirty
check; read-only roles exclude scratch and output roots. Changes to these
hidden sources do not invalidate their view. Implicit fetch and remote helpers
are disabled. Base files are not copied into the instance and no second file
manifest is retained. Missing or dirty required sources prevent a later use that needs them; they do not
rewrite known terminal outcomes or block metadata-only historical queries.

准备时冻结上述声明、基础仓库对象格式与 HEAD commit；支持 SHA-1 和 SHA-256 基础仓库。
已有实例的配置修改及 fork 均保留原身份。整个基础目录在原路径只读挂载，包含 untracked
和 ignored 文件；只遮蔽根 `.git` 目录。冻结 Git 身份覆盖追踪状态，其余基础内容采用
宿主当前文件。冻结 HEAD 用于代码身份，不提供恶意内容篡改检测。有界 Git 索引/树枚举
拒绝 staged 修改与追踪 symlink/gitlink；普通 Git 工作树检查拒绝追踪来源的 dirty 状态，
采用 Git 通常的 filter、assume-unchanged 和
skip-worktree 语义。命名空间启动不逐个打开、hash 或 stat 基础文件；Git 元数据校验
仍随追踪条目数量增长。W 与 run 的 dirty 检查排除 editable、scratch 和 outputs；只读角色排除 scratch 和 outputs，
这些隐藏来源的变化不使该视图失效。隐式 fetch 和 remote helper 均禁用。基础文件不复制
进实例，也不持久保留第二份文件清单。所需来源缺失或 dirty 只阻止后续真正需要这些来源的
消费，不改写已知终态，也不阻断只读历史元数据查询。

W edits explicit `project/<editable>` drafts mapped to the original base paths.
The first draft copies only tracked editable code under the existing shared file
and entry counts, finite deadline and path checks. A uniquely selected parent run or compiled implementation supplies its
exact retained commit; absent a unique selection, the draft starts from frozen
base code with that fallback reported. No latest-call or dirty-draft inference is
used, and rebuilding an existing job preserves its current draft. A fork keeps
selected commits and current drafts distinct and still depends on the original
shared base. Each local project run captures all editable roots in one directory
`code_version`, including only declared code paths; initial seed, capture and
exports share limits of 4,096 files and 8,192 scan entries, without a per-file or
aggregate code-byte quota; `[project]` currently offers no count override.
These code-capture counts do not apply to the
read-only base contents. For a new declared project, `config-check` and `prepare`
preflight the initial tracked editable seed with file stat metadata and entry
counts, including editable roots and implicit parent directories, and report limit
violations during preparation. This count/identity preflight reads no file contents and
does not scan untracked/ignored contents. Initial seed retains its limit checks;
runtime capture checks all actual editable contents, including ordinary
untracked/ignored entries. Later additions can still exceed counts; changed files,
unsafe paths or an expired deadline can still prevent a node or call from starting.

W 编辑 `project/<editable>` 草稿，并映射到基础工程原路径。初始草稿只复制追踪的 editable
代码，全部根共享既有文件/条目数、有限期限及路径检查。唯一明确选中的父 run 或 compiled implementation 提供准确
留存 commit；没有唯一选择时回到冻结基础代码并说明该回退。不按最新调用或未提交草稿
推断来源，已有 job 重建保留当前草稿。fork 区分准确 commit 与当前草稿，并继续依赖原共享
基础工程。每次本地 project run 将全部 editable 根聚合为一个目录 `code_version`，只含
声明代码路径；初始 seed、采集及导出共享 4096 个文件、8192 个扫描条目，不限制代码
单文件或累计字节；当前 `[project]` 没有数量覆盖项。只读 base 内容不受这些代码采集数量限制。对新声明工程，`config-check` 和
`prepare` 用文件 stat 及条目计数预检初始 tracked editable seed，目录计数包含 editable
根及隐含父目录，超额在准备时报告。预检不读取文件内容，也不扫描 untracked/ignored
内容。首次 seed 仍检查额度；运行期采集检查全部实际 editable 内容，含 untracked/ignored
普通条目。后续新增可能超过数量，文件变化、不安全路径或期限届满仍可能阻止节点或调用启动。

There is no overlay filesystem. W can write only its declared editable, scratch
and node-output targets in the base view; other roles see the read-only base, not
W's draft. A run mounts its exact code version read-only, with read-only tmpfs for
empty editable roots. It may use the exact base root as cwd. Each scratch root is
temporary tmpfs; run outputs retain their full relative paths under the original
call's artifact directory and use the existing stop-writer/seal/collect lifecycle.
W's node outputs are drafts, not future call originals. Writes elsewhere fail;
declare the exact needed scratch/output directory rather than making the base
writable. Existing scientific input mounts remain read-only.
Missing declared mountpoints and their necessary parents are created as empty
host directories before launch; this changes directory metadata without copying
base contents. Pre-create those directories when the host base is not writable.

不使用 overlay 文件系统。W 在基础视图中只可写声明的 editable、scratch 与节点输出目标；
其他角色看到只读基础工程，不读取 W 草稿。run 将准确代码版本只读挂载，空 editable 根
用只读 tmpfs 表示，可使用准确 base 根作 cwd。每个 scratch 根为临时 tmpfs；run 输出在
原调用 artifact 目录下保留完整相对路径，沿用原停写、封存与采集生命周期。W 节点输出
仍是草稿，不是未来调用原件。其他位置写入失败；需要工作目录或输出时，应声明准确
scratch/output 目录。科学输入挂载仍只读。
声明挂载点缺失时，启动前会在宿主创建空目录及必要父目录，只改变目录元数据，不复制
基础内容；宿主基础目录不可写时，应预先建好这些目录。

Project projection requires Linux namespace support and an explicitly configured
workspace-write policy: standard Codex, or Pi's supported explicit permission
profile described above (without a custom launcher contract). Pi uses its own
mount projection, not the Codex binary or configuration. The following native
configuration exclusions describe the Codex path. Inherited or unspecified native workspace
modes, `read-only` or `danger-full-access`, native named/default or selected
configuration permission profiles, managed policies, additional writable roots
and custom/protocol/registered launchers are unsupported and rejected before
launch. Native permission profiles here mean access-policy selections; role and
named Worker model/Skill profiles retain their existing contract. The framework
does not switch native permissions to make a project run. Network, web and
subagent declarations remain unchanged; existing Skill, authentication, review
and execution-authorization limits apply. The same-user host boundary remains soft.

工程投影要求 Linux namespace 支持和显式 workspace-write：标准 Codex，或上述 Pi 支持的
显式权限组合（无 custom launcher contract）。Pi 使用自身挂载投影，不依赖 Codex 二进制或配置。
下列原生配置排除项描述 Codex 路径。
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
| Pi `closed` | Explicit read-only/external or workspace-write, network=true, web=disabled, subagents=false; provider/bash network remains unrestricted. Linux mounts protect exact originals; no unsandboxed fallback. / 显式只读 external 或 workspace-write，network=true、web=disabled、subagents=false；provider/bash 仍联网，Linux 挂载保护准确原件，无无沙箱回退。 |
| Pi `configured` | Same workspace/network declarations, web=live, subagents=false; trusted extensions may add capabilities. Effective subagents is unknown (`null`), enforcement is `extension_dependent`; false requests no Backend delegation, without proving extension-created child calls/processes are forbidden. / 工作区／联网声明同上，web=live、subagents=false；可信扩展可增加能力，实际 subagents 为 unknown（null），实施方式为 extension_dependent；false 不请求 Backend 委派，不证明扩展子调用／进程已禁止。 |
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

Every provider implements the complete `Backend` abstract class, including preparation, native options and typed UI fields, permissions, launch/resume, basic observations, terminal evidence, recovery and authorship. Control and UI dispatch through these methods; a new backend must declare unsupported capabilities explicitly. Host time, budgets, scientific conclusions and managed execution interfaces keep their existing owners.

每个 provider 实现完整 `Backend` 抽象类，包括准备、原生选项与 UI 字段定义、权限、启动／续接、基础观测、终态证据、恢复和作者证明。控制层与 UI 通过这些方法调用；新后端明确声明不支持的能力。宿主时间、预算、科学结论和受管执行接口保留既有 owner。

The auxiliary read-only translation service uses the same provider boundary for capability checks, launch arguments/environment and final response acquisition. Saving a launcher preserves its invoked alias; the separate launcher binding still verifies its resolved target.

辅助只读翻译也通过同一 provider 边界检查能力、构造启动参数／环境及取得最终回答。保存 launcher 保留调用入口名称；独立的 launcher 绑定仍核对解析后的准确目标。

The standard Codex path obtains optional occupancy from genuine `thread/tokenUsage/updated`: latest `last.totalTokens / modelContextWindow`, bound to the current thread/turn. Compaction or failed metadata invalidates old occupancy. Completed-compaction counts and per-request token facts still use qualified small hooks. Only the public preview is bounded; the complete scientific final is streamed separately without a framework byte quota. Tool/input bodies do not enter the control observer.

标准 Codex 从真实 `thread/tokenUsage/updated` 采集可选占比，即绑定当前 thread／turn 的 `last.totalTokens / modelContextWindow`；压缩或元数据故障使旧占比失效。完成压缩次数与逐请求 token 事实仍使用已核对的小型 hooks。公开预览保持紧凑，完整科学回答独立流式保存，不设框架字节配额；工具和输入正文不进入控制观测器。

| Backend / 后端 | Transport and limits / 输运与限制 |
| --- | --- |
| `codex` | Standard launches own an app-server connection and exact thread/turn event projection; custom launchers retain their declared transport. Model profiles, permissions, original home and finite recovery remain frozen. Complete participant scope requires the qualified single-agent path with subagents disabled. / 标准启动持有 app-server 连接并投影准确 thread／turn 事件，自定义 launcher 保留所声明输运；profile、权限、原 home 与有限恢复保持冻结。完整作者范围须使用已核对的单 Agent 路径并关闭子 Agent。 |
| `pi` | Exact model/thinking RPC with one prompt per invocation, finite exact-session resume, common H/W/A/E and Local/project/Skill consumers; prompt schema, original UTF-8 and separate billing/metadata. New supported `closed` preparations retain controlled single-native-session authorship for the shared exact-subject independent formal A gates. Explicit `configured` loads installed native extensions/packages cache-only and has no complete authorship proof. Frozen unsupported capabilities/receipts stay unsupported. Full native calls, native subagents and auxiliary tool-less read-only remain unsupported. / 准确模型与 thinking RPC，每 invocation 一次 prompt，有限同 session 续接，复用公共角色及 Local/工程/Skill 消费；prompt Schema、原始 UTF-8、账单与元数据分开。新受支持 closed 准备留存受控单原生 session 作者证明，沿公共准确 subject 独立正式 A 门槛；显式 configured 只加载缓存中已安装的原生扩展／包，不具完整作者证明。冻结 unsupported 能力／回执不升级。完整 native-call、原生子代理与无工具辅助只读仍不支持。 |
| `protocol` | Runs the exact supplied argv, sends one JSON request on stdin, reads JSONL events. Permissions are declarations with `enforcement = "hint_only"`; the launcher owns actual access controls. No native session resume or complete native authorship attestation; formal independent review is unavailable. / 精确 argv、stdin JSON、stdout JSONL；权限仅作提示，由 launcher 实施，不支持原线程恢复或完整作者证明，不能正式独审。 |

The protocol request identifies `protocol = "scidiscovery-agent-v1"` and contains
`prompt`, `workspace`, `profile`, `response_schema` and `permissions`. Events use
`session` with `session_id`, `usage` with cumulative invocation `usage`, `result`
with the scientific JSON object in `result`, and explicit `completed`. Usage counters
are `input_tokens`, `cached_input_tokens` and `output_tokens`; absent or invalid
usage remains unknown rather than becoming zero. Optional public `message` events feed the bounded text projection; `tool` events
retain activity without decoding their tool/input/output bodies. A result alone is not a completed
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

A positively proven never-submitted prelaunch failure offers explicit `worker-resume --step N --expected-attempt K` (or `start --resume-step N --expected-attempt K`). It rechecks capabilities on a linked new attempt under the same job/profile/permissions and remaining budgets; repeated submission with the same expected attempt collects the accepted replacement. This is separate from session resume and never permits uncertain submission replay. The technical-pause UI uses the exact same action and attempt binding.

有正面证据证明尚未提交的启动失败，可显式使用上述带 `--expected-attempt` 的命令；它在同一 job、profile、权限和剩余额度下追加关联 attempt 并重查能力。相同 expected attempt 重复提交只收集已接受的替代 attempt。此路径独立于 session 续接，不允许重放未知提交；技术暂停网页按钮使用同一操作及 attempt 绑定。
`pause` 等已接受工作完成后阻止下一节点，并非立即杀进程；进程失踪须核对原调用，不能重发。

Scientific `budget-amend` requests receive durable operation IDs and apply at a safe
boundary before another new task, retaining all consumption, reservations and unknown
usage. `accepted` is a queued request, not an applied ceiling. Execution-resource
amendments remain separate and grant no exact-call permission. Soft node/W targets
request a bounded conclusion without forcing A or rejecting a new managed experiment
solely for reaching the target. W's enabled context-window gate remains separate. Notices start at 80% of the advisory node token or
time target, at token balance no higher than the existing W+A advice for H or A
advice for W, or at strictly less than 10% of shared active time remaining. Each
logical node receives one token-risk and one time-risk notice across resumes, at
most two risk notices; ordinary W has a separate context-review notice. Token advice
does not suppress later time advice. Notices arrive
at the next supported local tool boundary without interrupting the current request.
For marked ordinary A, notices close only the diagnosis and return advice to W;
they do not widen its action set. Final A concludes from existing originals when shared allowance is exhausted,
with no new H/W/E research. These are advisory thresholds, not new
quotas, reservations, authorization or defaults. Missing billing coverage alone
does not block advancement; reported lower bounds and unknown gaps remain, without
refund, zero replacement or replay. Unknown execution still requires original-call
reconciliation. Shared admission and finite execution permissions remain enforced.
The protected final A and its authorized original delivery retain
their completion policy; see [architecture](ARCHITECTURE.md).

科学预算修订先收取持久 operation ID，在下一新任务前的安全边界应用，保留全部消费、
预留和未知用量。accepted 仅表示排队；执行资源修订另行办理且不授精确调用权限。
节点/W 软目标只提示收口，不仅因达线拒绝新受管实验或强制转 A；W 启用的上下文窗口门禁另行保留。节点建议 token 或时间目标
达到 80%、token 余额不高于 H 后续 W+A 或 W 后续 A 的既有建议量、或共享剩余 active time
严格低于总时限的 10% 时提醒；每逻辑节点的 token、time 风险各一次，跨 resume 去重，最多
两次风险提醒，普通 W 另有独立的上下文复核提醒。token 提醒不抑制后续 time 提醒；
在下一受支持本地工具边界生效，不打断当前请求。
普通 A 只收口诊断并返回 W，提醒不扩大动作集。共享额度耗尽时，Final A 从原件给出结论，
不新开 H/W/E 研究。阈值均为建议，不新增配额、预留、授权或默认配置。仅计费未知
不阻断推进，已报下界与未知缺口保留，不补零、不退款、不授重放；真实执行未知仍须按原调用
核对。共享准入、单次有限执行及 protected FINAL 原策略继续有效。

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
[research-loop contracts](RESEARCH_LOOP_CONTRACTS.md#instance-ui-control-and-display); the
[CLI guide](ROOTLESS_CLI.zh-CN.md) describes their user controls.

`serve-ui` 可重复 `--instance-root` 添加明确实例；管理页也可按精确已有根打开，不扫描
目录、不按新旧推断。实例内 `/configuration` 编辑后续节点配置并展示当前生效设置；
`/new` 独立创建新研究，工作台的 `/configuration` 保留新建入口。新建入口通过
相同 API 检查和准备实例；TOML 为可选导入、导出及明确保存格式。导入文件目录决定其相对路径基准，
导出与另存保留路径含义；新建声明编辑不修改已有实例。网页还提供预算排队、
用户方向、操作回执、后台启动/恢复和有界进度。这些入口不额外授予真实浏览器或后端资格。
UI 意图重试、显示缓存及翻译限制由[研究循环合同](RESEARCH_LOOP_CONTRACTS.zh-CN.md#实例-ui-的控制与显示)
定义，具体操作见 [CLI 指南](ROOTLESS_CLI.zh-CN.md)。

Cov execution is provided by the optional `scidiscovery-cov-execution` distribution
(`SCID_PLUGINS=cov_execution`). Its executor key remains `cov_execution:experiment`;
new configuration freezes the registration's observer/delivery defaults. Explicit
operator policies still apply; installing a provider grants no execution authority.
Cov 执行来自独立插件，核心安装不含 Cov；注册提供的新实例默认观察与交付配置仍须冻结，
安装本身不授予执行权限。

## Selected framework Skills / 按角色选择框架 Skill

The new-research webpage lists installed Skills by name and description in the
second step's initially collapsed Research Skills section. Expand it to check
each Skill for the roles that should use it; discovery enables
no role by default. Existing named sets and Worker inheritance remain available.
The picker saves the existing set/role declaration format, so TOML import and
export retain the same selections. Existing-instance settings can select only
from that instance's frozen sets.

新建研究的第二步在默认折叠的“研究技能”中列出已安装 Skill 的名称与说明，展开后按角色勾选；默认均不启用。
已有具名集合与命名研究者的继承关系继续保留，导入、导出 TOML 后选择保持一致。
已有实例的配置页只允许选择该实例已冻结的集合。

Listing reads only each installed bundle's `SKILL.md` metadata in the installation
Skill directory, without traversing bundle resources. A missing or invalid bundle
is reported alongside usable entries; selecting it still requires normal
preparation validation. This display-time discovery is separate from preparation's
selected-bundle checks below.

列表只读取安装目录下一层各 Skill 的 `SKILL.md` 元数据，不遍历资源目录。缺失或格式
有误的 Skill 显示诊断，不妨碍选择其它可用项；实际选中后仍须通过正常准备检查。
网页展示时的发现与下述准备阶段的已选资产检查分别进行。

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

Standard Codex supports this projection directly. Pi supports exact explicit
manual loading with native `get_commands` proof under its resource profile above;
its live assets and aliases are read-only. Exact source-original overlays follow
write grants, including the native auth-directory exception.
A provider-declared module
launcher may implement the same exact contract. The Cov execution adapter no
longer supplies a dedicated native launcher.
Other script/protocol/custom launchers reject nonempty selected sets without a
verified loading contract. Empty or unselected sets preserve existing behavior.
On Codex, this framework selection does not suppress native user/system/plugin
sources. Pi filters automatic loading and accepts only exact selected manuals and
the controlled observer as described above. Neither selection changes declared
native home, sandbox, shell, network, web or subagent permissions. A Skill
grants no execution permission, independent-review verdict or scientific qualification.
Standard Codex retains its frozen native home and authentication; Skill selection
does not create another authentication copy.

标准 Codex 直接发现投影；Pi 按上述资源 profile 显式加载准确手册，并用 native get_commands
证明，live 资产及别名保持只读，准确来源原件在含 native auth 的可写授权之后只读覆盖。适配器声明的模块 launcher 可实现相同合同。Cov 执行适配器
不再提供专用原生启动器。
缺少可核实加载合同的任意脚本、protocol 或自定义 launcher 对实际选中的非空集合明确拒绝；
空集合或未选集合维持原行为。Codex 的框架选择不屏蔽原生用户、系统、插件来源；Pi 则过滤
自动加载，只接受上述准确所选手册及受控 observer。两者都不改变声明的原生 home、
sandbox、shell、网络、web 或子代理权限。Skill 不授执行权限、独审结论或科学资格。
标准 Codex 保留冻结的原生 home 与认证；Skill 选择不会创建另一份认证副本。

Only Skill schema v3 installation bindings are supported. Schema v2 manifest/object
bindings are rejected before asset access, including during fork preparation.
No compatibility reader or migration is provided. Read-only mounts remain a soft
boundary for trusted same-user processes.

仅支持 Skill schema v3 安装来源绑定。v2 manifest/对象绑定在读取资产前拒绝，fork 准备也
遵守同一条件；不提供兼容读取或迁移。只读挂载仍属同用户软边界。
