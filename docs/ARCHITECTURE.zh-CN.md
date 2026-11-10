# SciBranch 架构：W 主导研究，控制层保持薄

简体中文 | [English](ARCHITECTURE.md)

SciBranch 从明确目标、选中的原始输入、声明的原生权限和有限预算运行科学研究循环。
W 拥有持续研究及结论；宿主保留准确身份、执行准入并记录实际发生的事实。
证据、科学资格和执行权限分别成立。

`scid-research` 是应用核心，`scid-experiment` 是受管执行入口。可选实例 UI
复用相同公共控制与读取视图，不增加调度器或状态权威。不存在 LLM Root 或 MCP 调度器。

<a id="责任与数据流"></a>

## 研究流程

```text
操作者：目标 + 准确输入 + 配置 + 有限预算
  -> prepare：冻结实例身份与生效设置
  -> 单 controller writer：准入、绑定输入、核对预算与权限
  -> W：观察 -> 选择问题 -> 实现 -> 执行 -> 比较 -> 调整
       |-> H：替代解释 -------------------------> W
       |-> E：有界证据 -------------------------> W
       |-> 普通 A：诊断建议 --------------------> W
       |-> 上下文交接 -------------------------> 新 W
       `-> decision -> 必需的 Final A
                         |-> 一次有界 W 补正 -> 一次再评估
                         |-> finish：不交付
                         `-> deliver 准确候选 -> submit -> confirm

W -> scid-experiment -> 冻结执行接口 -> Local / 领域 adapter
                                   -> 原调用 query/collect/cancel
UI / CLI -> 相同 controller 操作及封存结果视图
```

W 自主开始研究，初探可没有候选或成熟假设。每批受管实验前，W 声明准确机制和批次问题，
说明哪些观察会改变下一步。小任务由 W 或其获准原生子代理完成；W 自主决定何时咨询 H/E。
一次弱分数、一次调用完成或上下文变化本身不要求新增 A 周期。

普通 A 诊断有证据的局部锁定、忽略反证、目标偏移或严重逻辑缺陷，将建议交回 W；
W 决定如何响应。Final A 独立评价交付主张、复现、未满足目标和限制，可拒绝全部候选。
原额度可用时可要求一次原范围与预算内的具体补正，之后至多一次再评估。
耗尽时只从原件作最终报告，不新增探索。交付前必须经过 Final A，另行显式的正式资格
与执行门槛仍独立成立。

## 责任与边界

| Owner | 职责 |
| --- | --- |
| 操作者 | 选择目标、准确原件、原生权限、预算和显式控制请求。 |
| W | 研究方向、机制、实验设计、实现、适应调整与候选选择。 |
| H / E | 回答 W 的具体解释问题／有界证据问题，随后返回 W。 |
| 普通 A / Final A | 诊断建议／最终科学交付判断，二者权限不同。 |
| 控制器 | 单推进写者，不可变任务与调用身份、已接受决定、准入、预算和暂停。 |
| Backend | 完整原生生命周期、权限投影、观测、终态证据、恢复和作者事实。 |
| 执行 adapter | 选中的执行合同及每个外部原调用的生命周期。 |
| 结果 publisher / UI | 保留作者原件并展示派生导航，不代写科学结论。 |

正式独审绑定准确 subject、合同要求的完整贡献者证明、独立审查者和显式封存 verdict。
普通 A、测试通过、传输完成及用户聊天均不提供这些资格或授权。

## 后端与执行

Codex 是默认后端；显式 protocol 传输与实验性 Pi 实现同一完整 `Backend` 类。
控制层及 UI 经这一边界消费能力和限制，不新增启动、观测、恢复、作者证明的外部子协议。
后端特定 profile、权限、资源、Skills 与默认值由[研究配置](RESEARCH_CONFIGURATION.md)维护。

受管执行冻结所选 adapter 接口与准确已接受调用。宿主拥有的 Git 捕获绑定所选代码；
科学输入输出保留原件引用。Linux 只读挂载在启动时保护选中科学输入。
Worker 与宿主同用户进程仍可信：原生权限及挂载属于软边界，不保证对抗恶意同用户进程。
后端或 adapter 的能力不等于科学资格或执行授权。

可选遥测缺失时保持未知；已知用量下界仍保留，账单缺失本身不阻断普通工作。
必要身份与准入证据仍严格核对。按块读取取消原始 RPC／selector／session 字节配额；
完整 JSON 值的解析内存仍与该值大小成比例。

## 状态与恢复

settings、job、作者输出、accepted call、receipt 与控制账本是原件。
读取视图、lineage、候选导航及 UI cache 是其投影。每次操作由一个 owner 加载并接受
各项不可变事实，再显式传给消费者；可变状态在实际 writer／准入边界刷新。
控制器的恢复 cursor 和单步推进复用已接受决定；内部不可变 `Route` 值不新增持久 schema
或另一状态权威。`guidance.py` 拥有新任务指导文字，恢复不重新生成留存任务说明。

有界的 `execution_facts` 视图从已接受值描述受管调用，复用于现有调用回执、W 接续和
Final A。accepted-prefix publisher 保留其冻结历史，不扫描旧日志。作用域、结果缺失和
省略量明确展示；执行成功不证明科学结论有效。覆盖范围与读取限制见
[执行事实视图](RESEARCH_LOOP_CONTRACTS.zh-CN.md#execution-facts)。

新节点绑定准确运行配置 revision。保存修改只排队未来设置，不改已有任务、不清零预算，
也不启动研究。暂停阻止新工作，已接受原件继续遵守原限制；类型化暂停诊断保留已准入的
resume 动作。原生续接、输出补填、执行唤醒与服务恢复分别需要自己的冻结许可，
保留原任务、home/session、调用和消费。未知外部提交只能核对同一原调用，不能自动重发。
fork 与删除按明确身份及静止门槛操作，不新增资格，也不恢复缺失科学正文。

受支持的科学合同为 strategy 3 与 advisory scope 1；监督为 v4 或 `None`，
启用的 fragment 使用 handoff v2，也可为 `None`。冻结 v4 必须含布尔
`require_fresh_input`，新默认 `true`，可显式设 `false`。旧科学策略及缺必要标记的格式
明确拒绝，不迁移。协议 33 和 worker／lineage／final-completion 的格式 version 1
仍受支持。自动恢复新默认共用 8 次尝试、5–300 秒退避；显式原生 profile 和原任务已消费
限制保持有效。准确配置字段及限制由配置参考维护。

## 实现与参考入口

下列文件位于 `src/scidiscovery/research_loop/`，另有说明者除外。

| 边界 | 主要 owner |
| --- | --- |
| 准备、路由与推进 | `research_config.py`、`controller.py`、`runtime.py`、`instance_control.py` |
| 原生生命周期与恢复 | `backends.py`、`native.py`、`execution.py`、`continuation.py`、`native_recovery.py` |
| 科学合同与指导 | `contracts.py`、`semantic.py`、`guidance.py`、`instance_science.py` |
| 调用、代码与执行权限 | `experiments.py`、`local_experiments.py`、`code_versions.py`、`execution_inputs.py`、`execution_authority.py` |
| 资格与交付 | `qualification.py`、`formal_review.py`、`selection.py`、`delivery.py` |
| 控制、生命周期及 UI | `control_operations.py`、`configuration_updates.py`、`user_guidance.py`、`fork_service.py`、`instance_delete.py`、`instance_ui.py` |

- [研究循环合同](RESEARCH_LOOP_CONTRACTS.zh-CN.md)：准确路由、材料、身份、预算、最终报告、fork／删除和恢复规则。
- [研究配置](RESEARCH_CONFIGURATION.md)：声明、后端 profile、权限限制、工程、Skills 和恢复字段。
- [CLI 指南](ROOTLESS_CLI.zh-CN.md)：准备、运行及原调用维护。
- [执行 adapter API](PLUGIN_RUNTIME_API.md)：保留的执行扩展边界。
- [发布范围](RELEASE.md)与 [TCAD 资格边界](../plugins/tcad_artifact/README.md#qualification-boundary)：
  已实现支持及更强主张所需证据。源码、合成与传输检查不证明真实科学结果合格。
