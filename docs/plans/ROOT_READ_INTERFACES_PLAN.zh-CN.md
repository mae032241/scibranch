# Root 读取接口职责收敛计划

状态：**待执行提案，未实施、未提交、未部署**。基线：`main` 工作树 HEAD `ff451e9`，2026-10-01；同树存在未提交的两类列表止血补丁。本计划只规划读取路径，不启动测试、求解器或发布，不切换分支。

## 1. 目标、权威与范围

让 Root 从精确对象或小范围查找开始，用一次对象读取取得当前决策所需结果，再沿精确入口补读原文。控制负责过滤、完整对象分页、字节预算和遗漏说明；科学结论、限制及其解释继续由 Worker 负责。减少无用回包和服务层全量工作，不以 LLM 摘要、强制助手或新审批门补偿接口设计。

| 文档/材料 | 责任与本次处置 |
| --- | --- |
| [当前架构](../ARCHITECTURE.zh-CN.md)第3、4、5节及[设计宪章](../architecture/SCIENTIFIC_AGENT_DESIGN_CHARTER.zh-CN.md)第3、6、7节 | 当前规范；保留。查询、绑定、科学判断、执行与资格的权属不由本计划改写。 |
| [约束登记](../architecture/SCIENTIFIC_AGENT_CONSTRAINTS.yaml) AUTH-001/002、LIN-002、EVD-001、RES-002 | 当前约束及待复核账本；保留，不能因回包变小改成已符合。 |
| [发布状态](../RELEASE.zh-CN.md#current-status) | 精确候选的历史验证范围；保留，旧测试数不证明本计划通过。 |
| [插件 API](../PLUGIN_RUNTIME_API.md) | 原始材料、附件、结果 manifest 和端口合同的现有责任；复用，不建另一索引权威。 |
| [计划目录](README.md) | 仅增本计划入口；完整任务 Token 对照仍由原待办拥有，本计划限定读取路径对照。 |
| 本计划 | 未交付设计、实施顺序、验收与兼容选择的唯一说明；不取代架构，也不改写历史报告。 |

不扩展为数据库合并、平台权限重构或 UI 重写。公共 MCP 仍只有 `scid_catalog` / `scid_describe` / `scid_call`；优先修改已有接口，不新增一组 search/get/read 工具。

**分支边界：** `main` 只接可证明修复既有有界读取承诺的兼容修复：最小 summary、完整条目预算、已有单对象入口、局部查询优化及明确错误。新过滤合同、新游标版本、原文读取模式、持久交付重试和登记时点迁移进入 `dev` 提案；实施时依既有 main 修复 / dev 功能约定选择承载分支，不能把新协议伪装成 bugfix；此分类不新增逐阶段审批门。本次只写计划，不擅自拆分或切换分支。默认参数改变、批量 detail 收紧也必须先检查生产/安装消费者，不能直接删合同。

## 2. 当前证据与责任问题

以下是指定文件的静态阅读结果，不是性能实测。

| 文件与符号 | 当前行为及问题 |
| --- | --- |
| `interfaces/mcp_root_run_routes.py::run_list` | 遍历实例绑定，逐项 `runs.status`；summary 复用单任务 status，queued 可带派发指令和冻结模型配置。detail 对终态组装较大对象。即使最终投影删字段，读取/构造成本已经发生。 |
| `interfaces/mcp_root_instance_routes.py::scientific_inventory` | 先遍历全部 artifact，选每个 logical_name 的最高 revision，再逐项读 envelope、历史合同及 producer signal，最后才分页；summary/detail 原来没有条目级职责区分。最新 revision 不等于 current 或已合格。 |
| `interfaces/mcp_root.py::RootMCPRouter._call_tool` | 对 inventory/current/instances/outputs/capabilities 等先移除 `limit/before`；服务无法根据本页范围限制工作。`PageInput` 默认20、最多100，是条数预算。 |
| `interfaces/mcp_response_views.py::page/root_response` | 保持各 owner 顺序，以名称续读；多数列表没有整页字节预算。operation summary/index 等已有有界导航，detail 可返回多个完整合同。 |
| `interfaces/mcp_root_execution_routes.py::execution_outputs/execution_list` | outputs 先发布结果、遍历所有输出并逐项绑定名称，再交给视图分页；list 遍历/判断恢复可见性并构造每项状态。读取承担登记与批量细节组装。 |
| `interfaces/mcp_root_instance_routes.py::artifact_catalog`、`mcp_root_run_routes.py::run_status` | parents/producer_inputs 虽最多32条一页，仍先构造完整可见父链；单 Run navigation 为恢复输入名扫描全部 artifact 绑定。应沿精确 Ref 查询。 |
| `interfaces/mcp_root_instance_routes.py::lifecycle_events`、`service/scheduler_bindings.py::observe_state_changes` | 全量扫描状态（上限1024），在返回前提交观察状态。传输丢包或提交后异常会使下次读不到同次变化；返回层裁剪会永久吞掉已推进的条目。它是观察差异，不能证明记录了两次观察间所有状态转换。 |
| `interfaces/mcp_root.py::ROOT_TOOLS` | scientific_current、instance_list、approval_list、execution_capabilities 也需纳入矩阵；execution 面必须显式选择。不能仅修默认 research 两个列表便宣称 Root 全部有界。 |

上表路径以 `src/scidiscovery/artifact_agent/` 为前缀。现有 `execution_logs` 已有缓存、倒序/增量游标、轮转 gap；`run_status` 已有 decision/status/navigation/full、字段/路径选择、阶段及诊断分页。复用这些能力。

## 3. 逐接口保留、收减与改造

“查找”只定位；“对象”读精确主体；“原文”显式补读科学字节。状态/currentness/资格缺失或未知必须可见，不能当作 true。删除对象是批量回包中的重复责任，不是科学事实或公共工具名称。

| 面与接口 | 保留的职责 | 收减/改造及精确续读 |
| --- | --- | --- |
| research `run_list` | 名称、operation_id、状态、合同历史标记、恢复限制 | summary 删除 dispatch、profile、时间戳和不改变定位的布尔项；不读科学正文。新过滤下推；detail 逐步收敛为定位信息，选中后 `run_status`。 |
| research `scientific_inventory` | 精确 artifact_name、kind/schema、selected_current、historical、claim_admissible、producer_handoff | 不内联合同/结论。保留现有资格限制，但明确 `claim_admissible` 不是完整 qualification 判定；单对象提供权威资格/来源入口。按前缀/类型/生产任务查找。 |
| research `scientific_current` | 显式选择槽位与精确所选对象、stale/未知限制 | 不把 current 扩展为候选目录；槽位查找与有界页即可。精确 Ref 与仅 logical_name 不得混同。 |
| research `instance_list` / `instance_current` | 实例定位 / 当前绑定与 management_url | list 保留名称、标题、状态并预算；current 保持单对象。实例选择/创建仍只在用户页面。 |
| research `approval_list` / `approval_status` | 查找审批 / 精确状态、决定、review_url、依据原文入口 | list 不展开审查主体和长 rationale；需要决策的每个 review_url 不隐藏。status 若省略长 rationale 明示原文入口，不把 excerpt 当完整人工决定。 |
| research `artifact_catalog` | 单对象元数据、资格限制及精确 parents/producer_inputs | 分离默认对象卡片与原件分页；父链批量解析后有界输出。补齐确实缺少的原文能力时只扩展此接口的显式模式（dev），不假装当前 metadata 就能读文件。 |
| research `run_status` | completed 决策字段、限制、remaining question、scheduler_signal；短 status；精确 navigation/原文 | 保留全部现有 intent 和阶段语义；按已冻结输入 Ref 查名。结论不按字节切句；超限返回遗漏与字段入口，不把片段装成完整 sealed_output。运行阶段始终标预览。 |
| research `operation_catalog`、`scid_catalog/describe` | 有界查找、唯一编译目录、选中合同 | 复用 index/facets/matches。移除“枚举顺便读取多个 full 合同”的默认路径；detail 最终要求精确 operation_id（dev兼容变更）。已经取得且未变化的合同不重复读取。 |
| research `diagnostic_read`、Run 阶段/诊断读取 | 显式、可续读的原文/安全诊断 | 沿已有 reference/offset，不回填到默认 status，不公开私有 manifest 或工作区。 |
| research `lifecycle_events` | 持久、有界的变化通知与精确对象入口 | 不做响应后截断；交付预算与游标在 owner 层一起决定。丢包重放见第6节。列表只用于定位/恢复。 |
| execution `execution_list` / `execution_status` / `execution_sync` | 可见执行查找 / 单执行权威观测或刷新 | list 只读缓存，保留授权/恢复/未知状态限制；不可逐项远端刷新。status/sync 继续遵守现有主动查询合同，不因名称像“read”就改语义。日志正文仍不默认返回。 |
| execution `execution_outputs` | 精确执行结果及输出目录 | 每页保留同一 result_artifact_name；最小 label/name/类型/必要限制，size 按需。分页下推，登记从常规读取迁到既有收集完成路径，保留旧数据幂等修复。 |
| execution `execution_capabilities` | 已选 Effect 的可用 profile 查找 | 按已选 operation_id、profile 过滤，有界清单；选中 profile 再 detail。不给全量环境配置。 |
| execution `execution_logs` | 精确执行/日志缓存的最新页、向前/增量原文 | 保留 before/after、gap、采样时间和收集后原件入口；不重新实现日志分页。 |
| 创建/动作回包：`operation_invoke`、收集完成、Agent提交后的 Root可读状态 | 精确 Run/输出/审批/派发结果 | 返回决策结果及分类输出入口；queued 的必要 dispatch 仍在创建和单 Run 读取可得。异步 collect 的接收回包只表示受理，完成通知/后续对象读取才给已登记产物。 |

不删除公共接口，不增加笼统的“自动建议下一操作”。先确认原 `view=detail` 调用者及安装合同，再废除多对象详情；当前原始证据的读取能力必须保留。

## 4. 最小查询合同与服务层执行

以下为候选合同（dev），字段仅出现在有对应权威元数据的接口，不要求 Agent 全部填写：

- 查找可选 `name_prefix`、已有 `state`、`kind` 或 `schema`、`producer_run_name`（精确所属生产任务），再加现有 `limit` 与返回游标。过滤按 AND；不支持任意 JSON/文本语义搜索。已知精确名称直接走单对象，不先查找。
- 名称/类型过滤基于现有绑定、envelope 和生产关系；所属任务按已封存 Run 输出/manifest 关系解析。扩展名、目录、label 中出现 “log/curve” 不能推出科学用途、成功、资格或证据有效性。
- 结果分类仅消费已声明的输出端口、manifest 类别和 MIME；未声明用途显示“未分类”。Control 生成 Ref、哈希、内部连接键和原文定位参数，Agent 不填写机械映射表。
- 返回完整最小条目、`next_before`（保留现有名字，值可升级为版本化不透明游标）、`page_limited_by`、明确遗漏/未知说明及精确读取入口。非低成本总数不默认计算；删去原有 `total` 属合同变更，过渡期明确 `total` 可用性，不能用本页条数冒充总数。

实施依赖现有 `SchedulerBindingService.binding_page` 的 keyset 思路与 `list(name_prefix=...)`，并扩展对应 owner 的批量元数据查询：

1. Router 将已校验的过滤、页大小和游标传入服务，不剥除后再全量投影。先用实例/namespace/名称前缀/稳定键约束候选；inventory 的“每 logical_name 最新 revision”也在元数据查询中完成，不能先读所有 envelope。
2. 对 Run/审批/执行状态和 artifact kind/schema/生产关系，使用各 owner 的索引和批量只读查询。现有数据库分离时采用有界候选块与批量匹配；必要的跨库只读查询须先验证连接/事务边界，不能为此合并数据库或复制权威状态。只为已证明需要的查询加索引。
3. 每次请求候选扫描有固定上限；低命中率也不能循环扫完整实例。未扫完可返回空页加可前进游标和 `scan_limited`，不能假称无匹配。游标区分已检查候选位置与实际交付位置：因过滤排除者可越过；因预算没交付的合格对象不得越过。
4. 只对本页候选批量补充历史合同/资格/生产信号；不构造 dispatch、完整 status、科学 payload 或所有输出。per-page 查询次数由有界候选块决定，避免每条一查的 N+1。跨库资格不能靠弱化查询省略，必要时显式 unknown，并由精确对象/准入复核。
5. `execution_outputs` 从冻结结果 manifest 分页，父链从精确 Ref 批量查名；`run_status(navigation)` 仅查询冻结输入的 Ref。补读不扫描整个实例。索引是可重建查询设施，不成为新的 current/qualification/科学用途事实。

### 游标与预算

稳定排序键包含去歧义键；游标绑定实例、surface、接口、过滤摘要、排序版本及必要的数据版本。不可将另一查询的游标用于新过滤。不可变 manifest 绑定内容 digest；追加式绑定优先冻结首轮高水位，后续页只查询水位内记录（包括水位内的最高 revision）。有状态等可变筛选时，优先复用查询所属 namespace 及影响该筛选/排序的字段修订；必要时在原 owner 内增加轻量版本（dev），相关变化导致明确重开。无关日志、进度时间戳及其他 namespace 更新不得使游标失效，不引入全局 snapshot 系统，也不为版本检测逐页扫全库。重开保留“旧页不是完整快照”说明；跨库无法证明一致时返回 changed/unknown，不拼成稳定快照。列表水位只约束遍历，当前状态、currentness 和资格仍在精确单对象读取及准入时复核。main 的旧名称游标不冒充这一保证。

4 KiB summary / 32 KiB detail 可作为当前候选起点，只作末端兜底；它们不是所有原文读取的统一科学内容上限。预算计算覆盖最终 JSON 正文、导航、遗漏及游标，MCP 包装另记实测大小。控制自动选择完整条目数，Agent 不需要试调 limit。禁止截断名称、Ref 或结论来塞进预算。

大单项必须可读：先退回完整的最小定位条目和精确对象入口；最小条目仍无法容纳时，返回结构化 `entry_too_large`、精确名称及合法续读/跳过该已识别项的方法，不能只有“请读详情”却不告诉对象是谁。不把它当作已交付完整详情。结论字段超限沿 `output_paths`/字段导航补读；文件使用已有阶段原文或受控文件读取，未覆盖的 Root 文件读取在 `artifact_catalog` 显式扩展为所选文本段/JSON Pointer/文件引用，不接收任意宿主路径。显式 full 与二进制下载保留独立合同，原文不被摘要替代。

## 5. 动作结果导航与不可退化事实

导航从 Run 的封存 output/evidence/attachments、阶段交付记录和 `ExecutionResultManifest` 派生：主结果精确名称；已声明类别的目录入口；必要限制；可调用的下一页或原文参数。不将所有输出内联，不维护另一份可编辑“结果索引”。未封存、缺失、历史、未分类和未合格分别表达；没有可见名称就返回修复入口，不能按最新或同 schema 猜原件。

建议把执行输出名称的批量幂等登记接入**现有异步收集完成/恢复路径**：沿冻结 manifest 分批写现有 bindings，以既有回执/检查点恢复，不重新传输或执行；完成通知仅在所需绑定可解析后发布。前台 collect 保持立即受理，不把登记变成同步长请求。现有已 collected 数据读取继续兼容：优先读取已登记项；缺绑定时按精确 manifest 做有界幂等补登记并显式报告进度，不要求重跑 collect，不返错成“结果不存在”。`collected` 的旧有含义不因新导航静默改变。若原存储无法支持此恢复边界，先保留旧登记路径，仅优化分页，将登记迁移留在 dev 后续阶段。

权限、当前性、资格检查仍走原服务：surface 可见性不授予执行权；完整实验活动谱系下的执行仍不开放 Root；approval_only/私有控制原件不进入目录；前缀不跨实例；修订不继承审查；historical 不恢复资格；失败/超时/收集完成均不等同科学成功。列表显示的资格提示不代替 invoke 的完整准入。授权 URL、否决、缺失和未知不能为了预算静默删除。

## 6. 增量通知交付与重试

当前观察状态在 Root 收到回包前已经提交，不能承诺丢包后重放。本计划不把列表裁剪器直接用于事件；main 可先在观察事务内决定完整页预算，防止提交了未返回项，但这一步**仍不解决提交后传输丢失**。

dev 推荐复用既有 scheduler 数据库与 observer 范围，在现有交付持久化中保留一页有界回包及序号，`lifecycle_events` 增加可重放续读游标；不新增 ack 工具、数据库或科研状态机。首次请求生成页时，页内容/序号与观察基线更新原子保存；相同输入游标或丢包后的初始重试返回相同页。调用者携返回游标发起下一次读取才表示已接收上一页；空页也有稳定续读位置。若实现需要新增存储字段，只用于有界传输重放，不能成为生命周期权威。平台通知只提示“有变化”，由精确持久页恢复。

重启保持 observer 及未交付页；并发相同游标返回同页，过期/跨 observer/跨实例游标显式拒绝并提供状态恢复入口；不得默默从当前末尾开始。保留窗口以合同明示，窗口外返回 gap，不能宣称永不丢历史。不扩大现有状态观察语义：原系统未记录的中间转换不补造。优先使已观察并承诺交付的变化可重放；若要求完整转换审计，另评估既有 owner 活动记录，本计划不增全平台事件总线。

## 7. 未提交止血 diff 的去留

这些是审查意见，本次没有编辑止血代码。已有70项定向源码测试通过仅为调度方提供的该补丁证据；本计划未重跑，也不据此证明服务层分页、安装或真实 Token 收益。

| 现有改动 | 意见 |
| --- | --- |
| `agent_execution_settings.py` 的 `MCPReadSettings/mcp_read` | 保留全局控制预算作为候选；不加入实例模型偏好、Run 科学身份或 Agent 参数。是否需继续暴露两个配置字段在实施前确认，已有全局读预算可复用时避免另立 owner。默认值先按两列表实测，不直接泛化到事件/原文。 |
| `default_agent_settings.json` | 与采用的全局设置同步；保留缺配置兼容，不覆盖用户定制文件。 |
| `mcp_response_views.py::_bounded_list` | 保留完整对象及按实际交付续读的方向；修改单条过大错误为可定位/可继续，检查元数据和空页边界。最终分页 owner 下移，避免重复组装/重复序列化整页；不用于有消费副作用的事件。 |
| `run_list` summary投影 | 保留删除 dispatch/profile 等重复字段；把轻量投影前移到服务读取，detail/queued 的必要单对象派发仍可得。 |
| `scientific_inventory` summary投影 | 保留现有资格/历史/producer_handoff限制；删除 revision 等非必要默认字段可继续评估。补全资格含义与单对象入口，不能声称现在已解决全量扫描。 |
| `mcp_root.py` 配置接线/description | 保留原集中注册入口；修正为实际覆盖范围。过滤/分页参数必须传到服务，避免只有回复变小。 |
| `tests/operations/test_mcp_response_views.py` | 保留无丢项、原文保留与预算用例；补候选查询数、单项过大可达性和跨入口负例，不复制全部实现细节测试。 |
| `docs/INSTALL.md` / `docs/INSTALL.zh-CN.md` | 只随最终实际交付行为更新；“原文入口”须真实可调用，不能把 metadata 当文件阅读器。保持中英对应，不写成全接口已受限。 |

无关 `CONTRIBUTING.md` 和未跟踪周报不属于本计划。

## 8. 串行实施与每阶段验收/回滚

先处理一阶段并验证，再进入下一阶段；不同时推进多处分支。以下测试是实施时的计划，本次不运行。

| 阶段与分支 | 涉及文件（均在现有职责内） | 最小验收 | 回滚边界 |
| --- | --- | --- | --- |
| P0：冻结合同和基线（main只读） | 本计划、`mcp_root.py`、`mcp_gateway.py`；现有定向测试文件 | 逐项核对调用者：Root、安装入口、UI/插件动态消费者。确定保留字段和 baseline 回包；按既有 main/dev 约定归属变更。 | 无运行变更；不覆盖现有未提交工作。 |
| P1：两列表止血（main兼容修复） | 第7节现有 diff及 `mcp_root_run_routes.py` | 两列表预算、必要限制、queued单对象dispatch、原文可达；单项过大不能陷入无名字/无进展。只证明回包修复。 | 独立回退预算/投影，不改数据；保留原单对象能力。 |
| P2：查找下推（dev合同；无合同变化的局部优化可单列main） | `mcp_root.py`、三个列表route、`mcp_root_approval_routes.py`、`service/scheduler_bindings.py`、`runs.py`、`approvals.py`、`executions.py`、`storage/sqlite.py` | 小实例与同一页加1000个无关元数据对象对照；每页候选扫描/批量查询有固定上限，不加载科学payload；过滤/数据变化/旧游标明确处理。 | 新索引可留；停用新读路径。新游标不能被旧代码静默接受；旧合同按声明兼容或显式失效。 |
| P3：对象与原文入口收敛（dev） | `mcp_root_run_routes.py`、`mcp_root_instance_routes.py`、`mcp_root_operation_routes.py`、`mcp_response_views.py`、既有原文/引用服务；需要时 `mcp_root.py` | 查找→单对象→一个指定字段/文件可达；长父链分页不漏，超大单项可读；原文逐字节不变、missing/null/omitted区分；多合同detail变更有消费者迁移说明。 | 保留既有status/原文合同与封存字节；可撤导航投影，不回退科学记录。 |
| P4：完成结果直接导航（dev） | `mcp_root_operation_routes.py`、`mcp_root_execution_routes.py`、`service/runs.py`、`stage_deliveries.py`、`execution_collection.py`、`executions.py`、`scheduler_bindings.py` | 完成后0次全库inventory/list即可定位主结果和选定输出；收集仍异步；旧collected/登记中断/重复读取不重传、不重执行、不重复绑定。 | 读取旧manifest路径一直保留；关闭新导航仍可精确恢复。新增绑定为幂等别名，不撤销封存证据。 |
| P5：通知可靠交付（dev） | `mcp_root_instance_routes.py`、`scheduler_bindings.py`、`service/lifecycle.py`、`mcp_root.py`；必要的现有进程通知消费者 | 事务后回包丢失、重启、重试、并发、预算缩页、过期cursor；已交付页可重放，未返回事件不被推进。 | 不把已推进观察状态交给无重放旧读者；回滚保留待交付页/显式gap，并通过精确状态恢复，不能声称无缝降级。 |
| P6：安装与真实读取验收（所选候选） | 定向安装测试、`docs/ARCHITECTURE.md`及中译对应节、INSTALL中英、README计划状态 | 同一已封存任务/执行证据上的真实Root读取流程和指标；无新TCAD运行。仅通过的阶段改为已完成。 | 保留候选与旧安装边界；发布/部署另走授权流程，本计划不执行。 |

## 9. 小量跨入口验证与真实使用指标

沿 [受限串行测试入口](../../tests/README.md)选择确切文件/节点；不跑全套，不用 xdist，不自动跑 TCAD、在线模型或远端执行。每批独立进程，失败/内存止损即停，不提高预算。搜索只用明确文件的 `rg` 与限定路径的 `git ls-files`；禁止递归扫描大目录。无需生成一组过程文档，验收原始日志放单个临时目录。

优先复用 `test_mcp_response_views.py`、`test_root_context_boundary.py`、`test_root_token_optimization.py`、`test_execution_inventory_projection.py`、`test_instance_events.py`、`test_execution_log_cache.py`、`test_root_reading_installed.py`。每阶段只选相关节点；测试名新增后在实施记录写明，不猜不存在的节点。建议小量场景：

1. 同页 queued/completed/failed/historical/未合格对象；列表去重字段，单对象仍给准确派发或结论/限制，省略不等于不存在。
2. 前缀+状态/类型+精确生产任务，夹杂另一实例、隐藏材料和活动任务执行；查找不越权，精确invoke仍拒绝无资格对象。
3. 长名称、中文、零结果、低命中过滤、大单项与多页；预算覆盖导航；每个合格对象完整出现或有明确超限入口。过滤变化、前页对象状态改变/新增revision时返回明确失效而非漏项。
4. 查找→对象→字段/阶段/文件；精确Ref不漂移，原文不变，logs轮转仍报告gap且默认status无日志正文。
5. collect完成及旧manifest恢复；中途终止登记后幂等继续，导航直接读到同一结果；不得触发新执行或阻塞受理请求。
6. 事件回包丢失与重启后同游标重放；预算不足不提交未交付事件；缺历史保留gap。工程页/通知不冒充科学结果。

真实验收使用同一封存证据副本、相同问题和模型配置，分别记录 HEAD、止血候选、完成候选；不为对照重跑科学计算。固定三条流程：已知任务读结论及一个原文、按前缀定位一个未知对象、收到完成通知后读取一个输出。候选目标：

- 已知名称流程0次全实例查找；完成输出定位0次全库inventory；未知对象在匹配可装一页时只需1次查找+1次对象读取，原文按实际需求计数。
- 默认列表中 dispatch/profile/科学正文/日志正文/重复合同为0；必须保留的资格/限制字段缺失为0。报告列表及单对象回复的UTF-8 JSON字节数、MCP实际包装字节数、请求数、p50/p95；不预填虚构下降百分比。
- 固定页大小下，加入1000个无关元数据对象不增加科学payload读取，查询次数不随对象总数线性增加；报告候选扫描数、查询数与峰值RSS，低命中触及上限须显式续读。
- Token仅取真实平台逐请求usage；原有上下文、此次新增工具回包、输出Token及缓存命中分列。无法直接归因回包Token时只报告可观察usage/字节和限制，不用“字节÷4”冒充Token，也不把历史长上下文成本算作本次回包。没有真实usage就保持收益未验证。

实施前仍需落定：兼容期内批量detail如何退出；跨库列表采用哪种有界索引查询/变更代数；Root缺少的文件原文模式覆盖范围；事件页持久重放窗口。推荐先交付P1并测量，再按P2→P6推进；这些选择不影响本次计划完成，也不构成已经批准实现或部署。
