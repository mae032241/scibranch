# TCAD 执行与传输合同

本页拥有当前 TCAD 领域执行接口、transport 和收集边界。控制职责见
[架构](ARCHITECTURE.zh-CN.md)，真实 solver 资格见[资格状态](TCAD_QUALIFICATION_STATUS.zh-CN.md)。

## 1. 接口与权限

`tcad_artifact` 通过 `scidiscovery.execution_interfaces` 注册 `tcad_execution`。
薄实例在准备时冻结精确配置、实现和能力；`TCADManagedExecution` 消费可信宿主 gate，
没有 gate 时拒绝 production。配置选择 `socket` 或 `command` transport。
这些领域 daemon/MCP/SSH 后端提供执行能力，不承担科研调度。

当前受管接口接受经校验的 execution package V2 或 V3。V3 原生实现与非空输入
通过宿主编译及精确冻结原件绑定；缺少所需输入能力时拒绝，不猜测文件来源。
prepare、submit 和 collect 均核验原权限回执；准备/提交需要有效 admission，
收集需要原剩余收集上下文。正式审查、精确执行权限与资源准入分别由宿主控制层拥有。
领域 adapter 不登记科学资格、不解释科学结论，也不能批准自己。

公开 solver capability 是安全投影，不提供完整 executable、环境、SSH、许可证或私有参数。
控制层、package、JobSpec 和 runner 绑定同一 capability 摘要；executable、参数、环境、
solver kind 或发行证据漂移均在启动前拒绝。宿主及同权限 Worker 仍为可信主体；
接口不暴露凭据不等于阻止同 UID 原生进程读取，安全边界见[安全说明](../SECURITY.md)。

## 2. 外部 transport

任务载荷为 `TCADJobSpec` v5，含不透明的 32 位小写十六进制 `request_id`；外层请求/响应为 transport v2。
任务必须包含 `collect_generated_outputs` 和 `max_storage_bytes`。
控制端、适配器和远端 runner 必须使用匹配的 wire/config；不在线迁移旧任务。

`CommandTCADExecutorAdapter` 每次调用一个管理员配置的短命令。请求和响应为有界规范 JSON：

```json
{"schema_version":2,"operation":"capabilities|execution_policy|submission_protocol|prepare|lookup_submission|submit_retirable|retire_submission|status|cancel|collect","payload":{}}
```

| 操作 | 责任 |
| --- | --- |
| `capabilities` | 读取管理员冻结的 solver capability |
| `execution_policy` | 读取管理员策略与 runner/debug 配置 |
| `submission_protocol` | 只读确认远端支持版本 2 的提交/原子撤销协议 |
| `prepare` | 物化精确 package 与 JobSpec |
| `lookup_submission` | 按原不透明 `submission_id` 权威查回既有外部任务 |
| `submit_retirable` | 按原 `request_id` 幂等提交，服从原 `submission_id` 的永久撤销标记 |
| `retire_submission` | 在提交锁内返回现有任务，或持久撤销尚不存在的精确请求 |
| `status` | 一次短查询 |
| `cancel` | 请求取消并短时返回 |
| `collect` | 仅在终态后收集有界原始结果 |

transport 不等待 solver、不循环轮询、不生成或修改 deck、不批准请求、不写科研控制状态。
提交成功、领域终态和结果收集是三个事实，不能压成一个 `succeeded`。

`lookup_submission` 与 `retire_submission` 消费原 `submission_id`，它对应已冻结
JobSpec 的 `request_id`，不从科学载荷、归档或描述符内容计算。`submit_retirable` 必须
对同一已准备请求幂等。首次新提交先验证远端协议，再权威
`lookup_submission`。结果未知后只能核对原请求；`found:false` 不能证明在途提交已经
停止，也不能作为重发或释放预算的依据。`retire_submission` 的否定结果必须持久阻止
该精确请求今后被接受；反复查询为空、超时或端点不可用不构成撤销证明。
不允许回退旧 submit 端点绕过撤销协议。

## 3. 收集与资源

状态/日志与科学产物分开查询。领域收集器按原调用身份启动有界工作进程组，私有监督
进程持锁直到该组实际停止；所有权不能只依赖 SSH 进程继承锁。adapter 不拥有第二份
调度队列或重试预算。宿主对未知结果只核对原调用，不增加额度或重提。

正式收集默认总预算 600 秒，内部预留 `min(2 秒, 总预算 10%)` 用于停止和回收，
单文件 120 秒、无字节进展 30 秒；状态查询默认 5 秒。具体冻结配置可收紧或修改默认值。
`collect_with_budget(external_run_id, *, context)` 消费共同 `CollectionContext` 的单调时钟
截止点、文件/无进展预算和进度回调。command/socket 传递同一剩余预算，不逐项重置。
只实现 `collect(external_run_id)` 的调用者仍须由有界监督进程封装，不能在 Worker
线程直接调用无界旧方法。`inspect_outputs(..., *, deadline_monotonic=None)` 使用其原 IO
和任务剩余时间，不自动继承正式收集预算。

Job 的存储、内存、输出和时间限额分别约束；多 case 共用原 job deadline。
管理员策略可配置限额、outside-limits 处置、采样/终止宽限、分块、预览/诊断和收集预算。
策略直接授权不伪造人工决定，状态须显示授权来源。资源高水位和超调是采样观测，
不能作为硬磁盘或内存配额证明。

科学文件使用流式上传、带明确大小上限的 `get_to` 与路径/大小原件登记，
不经 JSON/base64，也不逐文件计算内容哈希。capability 与 policy 的审批绑定摘要保留，
具体白名单及同 UID 软边界见[架构](ARCHITECTURE.zh-CN.md)。传输配置必须覆盖获准总存储额度；小 JSON/预览仍独立有界。
预览上限不能代替科学文件存储上限。

## 4. 注入式开发调试与后端

保留的 `LocalTCADDebugService` 和 `TCADDevelopmentDebugAdapter` 是注入式领域库合同，
不因其存在就在薄 CLI 注册 Operation Worker 工具或启动旧 Run 宿主。调用者须提供
精确任务上下文、候选快照、权限、有限预算和收集监督；插件共享合同见
[Plugin API](PLUGIN_RUNTIME_API.md)。

开发模式仅为 `preflight|smoke|initialization`，实际参数由冻结 release、solver kind
和 entrypoint 决定。科学作者不能以该接口提供任意 shell、环境、凭据、网络目标或
工作区外路径。同名动作绑定首次模式及候选，后续只查询或收集同一调用。
诊断结果标记 `development_only=true`、`scientific_claim_admissible=false`；
preflight 成功不成为 production 执行、科学资格或下游正式证据。
调用者不得把未完成的草稿或未知外部状态当作成功结果，也不得假定可跨进程自动续接。

本地 adapter 可通过受限 socket 调用独立 TCAD controller。远端 Windows/VMware 配置
可复用管理员配置的 OpenSSH 和无第三方依赖的 Python 3.6 runner；不要求 VM 安装
SciDiscovery 服务或新增 sudo/端口代理。
真实调试与 production 仍需要可用许可证、管理员私有 profile 和可达 transport。
确定性 executable 或合成 transport 只证明所选工程行为，不证明 Sentaurus 可用、
deck 科学正确或仿真结果准确。
