# SciBranch 测试分组与资源边界

当前优先保护薄层的真实职责：原调用身份、执行事实、恢复不重放、候选交付、预算与单写者。不要以旧测试数量或旧安装矩阵定义验收范围。

薄层和共享库各自的检查验证其声明行为；旧入口的历史检查数量不代表当前覆盖。采样止损不等于硬隔离，不能据此宣称不会 OOM。

## 功能与安装分开

| 组 | 选择 | 边界 |
| --- | --- | --- |
| 功能源码 | `--lane source` | 语义、准入、原件及负控；不构建、不安装、不建venv。 |
| 功能进程 | `--lane process` | 必要的真实CLI、子进程、锁竞争、恢复；不安装。 |
| 大输入 | `--lane stress` | 仅按缺陷选择跨历史阈值的必要用例，不默认扩大数据量。 |
| 在线平台 | `--lane live` | 明确外部资格与授权，选择组不授予预算或权限。 |
| 独立安装 | `--lane installed` | 仅 `tests/installation/`，显式选择；只验证包装、入口发现与资源依赖。 |

功能全量也不导入安装目录。显式把安装文件传给功能组（反之亦然）会报错；功能fixture若依赖旧安装fixture也报错，不再自动升级到安装组。根conftest在收集前隔离目录，不能靠执行阶段跳过来掩盖安装依赖。

“功能全量”须覆盖全部功能测试文件的 source 和 process 两组；仅运行默认 source 不代表功能全量。实际启动 CLI、shell、namespace 或锁竞争子进程的用例应标记 `process_e2e`，包括通过 fixture/helper 启动的情况；仅构造命令或模拟进程接口的用例仍可留在 source。安装、大输入和真实平台的验收状态分别报告，不用模拟成功结果替代真实平台证据。

安装组不运行科学实验、H/W/A、daemon业务、评分或恢复矩阵；这些检查回到对应功能组。一个选定cohort只建一次环境、安装一次，不切换环境删除后再重建。默认core验证无可选领域执行器；涉及Cov插件入口时显式选cov，涉及TCAD交付元数据时显式选tcad。其余旧包装组合未验证，不构造新矩阵补齐。

`tests/installation/check_source_release.py` 是**单独显式选择**的源码发布包检查，名称不匹配默认测试发现。它检查发布文件、手册原件字节与manifest，不运行发布包中的pytest或安装检查。源码发布与wheel是不同交付路径，不能以一个冒充另一个；不在默认安装烟顺带构建整份源码发布包。开发工具目录 `.agents/`、`.agent/` 与本地维护指令不进入源码发布；根目录 `skills/` 是运行时资产，随其脚本、引用和功能 fixture 保留。

默认 installed 组只发现 `test_packaging.py`；core cohort 同时验证已安装产物的旧平台入口、模块和文件退役，不再另外构建或解包同一个 core wheel。`check_source_release.py` 仍须按源码发布差异显式选择，属于独立交付路径，安装检查通过不代表源码发布清单已验证。

## 选择与证据

- 先查目标用例的传递fixture、子进程、数据规模，再决定是否运行。
- 一个检查必须说明能证伪哪个生产故障。固定成功JSON只证明传输时，不得当作科学质量或执行成功证据。
- 去掉无效常量补丁、固定文件数量断言和无必要笛卡尔积；保留不同副作用入口的真实负控。
- 修正后只重测受影响路径，不重建环境重跑整组功能。
- 控制准入性能检查使用一次局部观察的读取计数，并在相邻边界追加 usage/预算修订验证失效；UI memo 不进入准入。
- 角色提示检查捕获实际 launcher argv/request，核对规则只传一次及权限不漂移；配置 inspect 检查 active/pending 与原件无写入。
- 独立审查默认只读，不另起验收批次。禁止并行测试/安装；必要锁竞争用例中的子进程不等于并行测试批次。

串行入口为 `scripts/run_tests.py`。确认资源条件与执行范围后，使用精确目标和仓库本地持久日志目录。例如功能目标选择 `--lane source --output .trial/check-name tests/research_loop/<文件>::<用例>`；独立安装选择 `--lane installed --installation-cohort core --output .trial/check-name <精确目标>`；
Cov 独立插件使用同一目标和 `--installation-cohort cov`。日志属于本地检查证据，不进入源码跟踪。

`--per-file` 会重复启动解释器与session fixture，不作为默认全目录执行方式；安装组禁止该选项。数据/进程用例显式标记 `stress`/`process_e2e`。collect-only也会导入测试，不视为无成本静态检查。

全量执行前按真实分组和已有耗时记录准备精确目标清单，串行分成可在现有 300 秒时限内结束的批次；长文件可按 node ID 拆分。时限属于每批而非整轮，不应把所有文件合成一个超大批次或提高限额绕过失败。CI 当前仍逐文件执行两组，存在空组重复收集成本；调整分批必须同时核对完整覆盖与耗时，不能仅为减少启动次数取消边界。失败、超时、跳过和未运行分别保留记录。

五项生命周期缺陷的专项回归可直接运行 `python scripts/verify_lifecycle_fixes.py`，默认依次执行 `test_instance_delete.py`、`test_fork_consumer_recovery.py`、`test_remote_runner_recovery.py` 各自的 source/process，共六个小批次。`--list` 只静态列出范围，不导入 pytest；`--case delete|fork|remote` 可选择其中一组。脚本从自身位置定位仓库，可在其他工作目录使用绝对脚本路径调用；所有批次使用同一个 Python 解释器与现有 `scripts/run_tests.py` 默认资源限制，首个失败即停止，不自动重试。

默认日志目录为仓库内 `.trial/lifecycle-validation-<UTC时间>-<随机后缀>`；`--output .trial/<新目录>` 可指定仓库内的新目录，相对路径以仓库根目录为基准，既有目录拒绝覆盖。每批保留 `runner.log` 和 `guard/` 原始证据，聚合 `summary.json` 记录命令、退出码、原始 guard 结果及未运行批次。只有退出码为 0、guard 报告 passed 且清理完成才通过；Ctrl-C/TERM 会转交现有 guard 并等待收尾。这是五项专项回归，不是功能全量、真实模型 Worker、solver 或 SSH 验收。

## 历史原件与当前交接回归

`research_loop/fixtures/r6_handoff_examples.json` 保留从公开科学输出提取的交接样例与拓扑。
它是功能夹具，不复制科学资格、模型会话、认证或宿主路径。旧的 r6 costs、delivery、
file_bindings 检查依赖已退役的 H 来源字段、普通 A 调度和整链回放，已移除；不要以旧矩阵
恢复兼容入口，也不要改写原始夹具字节来适配当前路由。

当前历史检查验证接受的前缀、原件关系及读取所有权：已有事实不重做科学验收，当前输入
变化仍拒绝，冷恢复保留原调用和已用预算。独立原件核验的工作量须有界于所选清单和请求，
不以历史实测秒数作为机器相关门槛。按缺陷显式选择现有检查，例如：

```sh
python scripts/run_tests.py --lane source --output .trial/handoff-source \
  tests/research_loop/check_r6_handoff_incremental.py \
  tests/research_loop/test_handoff_passive_provenance.py
python scripts/run_tests.py --lane process --output .trial/original-execution-process \
  tests/research_loop/test_nonlocal_execution_observation.py::test_remote_unknown_is_preserved_without_replay_or_worker_qualification \
  tests/research_loop/test_native_transport_stall.py
```

纯传输夹具须声明其实际后端与能力。合成输出只能验证协议，不能证明标准 Codex 支持
元数据 hook、真实权限隔离或科学资格。实际 namespace、原生程序及 socket 目录的启动失败
保留精确 stderr；未到达行为断言就是未验证，不用 skip、xfail、裸宿主回退或权限降级冒充通过。

## 资源与支持边界

`scripts/run_tests.py` 现有RSS、可用内存采样和超时止损不是内核聚合硬内存限制。安装与功能分组不能替代资源隔离；是否具备资源域隔离须由具体运行环境证明，不能从测试通过推断。

薄层检查不依赖旧 Run/Artifact/session 宿主 fixture。旧 catalog/finalizer/Operation 平台及其测试已退役，
`tests/operations/` 仅保留真实执行、传输、预算和恢复负控；确定性算法检查在 `tests/skills/`，
使用随资产保留的功能 fixture，不需要旧宿主。Skill 选择、冻结与生命周期检查在 `test_skill_supply.py`。未运行的源码、安装、真实平台资格分别记为未验证，不将静态审查写成动态通过。

## 原生模型与离线用量核对

真实模型试验从 [CLI](../docs/ROOTLESS_CLI.zh-CN.md) 的显式 `prepare` / `run` 和冻结 launcher 开始，单独选定目标、公开输入、模型配置、实例根目录及有限预算；保留原生权限，不复制私有认证或修改共享配置。原生输出、计量、封存和科学结论分别记录。

`scripts/native_usage_audit.py` 仅离线、只读核对显式指定的确切原始线程日志，不扫描私有会话目录，不公开会话正文。缺测保持未知；cached input 已包含在 input 中，不重复累加。指定日志的离线汇总不代替实时预算，也不证明所有参与线程均已覆盖。

G1/G3 工程检查通过 `scripts/verify_g1_g3.py` 显式选择。`python scripts/verify_g1_g3.py --list` 只静态列出精确节点，不导入 pytest/产品或启动子进程。显式文件 `tests/research_loop/check_g1_g3_cli_validation.py` 不参与默认发现；脚本 `SOURCE_NODES` 列出九个纯 source 节点。进程、组件批次及真实模型入口分别选择，真实模型需要独立范围、权限和预算。所有动态选择串行复用现有 `run_tests.py` 默认资源限制，首个失败即停，不自动重试；不自动扩大到安装、全量或 stress 检查。

`python scripts/verify_g1_g3.py --output .trial/<新目录>` 的默认 `all` 仍只有 source 报告逻辑和 sandbox 启动依赖检查，不调用模型。`--case sandbox-startup` 自行构造 bwrap 与惰性 payload，只证明依赖层；不能覆盖完整 production namespace 或真实 W。`--case components` 依次选择五个限定批次，也可单选 `git-component`、`cleanup-component`、`fork-component`、`retirement-component`、`delete-component`。这些批次引用现有精确节点，报告写明真实 Git、argv 或合成控制记录的层级：同进程捕获变稿不等于 V2 两进程屏障，模拟 replay 不等于真实工程检查，CLI/API 六入口退役不等于 HTTP/UI 动态验收。

真实 W/受管工具入口必须显式选择 `--case worker-managed`，并给出原有 codex executable、模型、reasoning effort、完整原生权限声明与有限预算。它只支持原生产入口已支持的单 executable Codex、workspace-write、无额外 writable roots；不支持的原声明会拒绝，不降 sandbox 或绕审批。`--native-options` 是现有公开声明的 JSON，必须明确包含 `workspace`、`network`、`web`、`subagents`、`sandbox_workspace_write`；后者明确包含 `writable_roots`、`network_access`、`exclude_tmpdir_env_var`、`exclude_slash_tmp`，network 两处一致。不得把 auth、session、token 或整份环境放进 JSON。下例各变量由操作员明确选择，不从当前实例、最近文件或 HOME 猜测：

```bash
python scripts/verify_g1_g3.py --case worker-managed \
  --output "$SCID_G3_NEW_OUTPUT" \
  --native-launcher "$SCID_G3_ORIGINAL_CODEX" \
  --model "$SCID_G3_ORIGINAL_MODEL" \
  --reasoning-effort "$SCID_G3_ORIGINAL_REASONING" \
  --native-options "$SCID_G3_ORIGINAL_NATIVE_OPTIONS" \
  --max-tokens "$SCID_G3_CHOSEN_MAX_TOKENS" \
  --max-seconds "$SCID_G3_CHOSEN_MAX_SECONDS"
```

预算输入无默认值：token target 为 1..50000，active seconds 为 30..180，每个 Local 调用最多 35 秒；原 guard 的 300 秒批次限制保持。token 是原有 observed soft target，不承诺请求级硬上限。原 HOME 认证由 native 继承，不复制认证/config、不更改用户权限或已有科学角色 profile。探针在这次明确新 output 内创建并保留工程实例，运行一个真实 W turn；W 的固定 helper 使用原 bridge CLI，两个调用由原 Local namespace 执行。v1 到达 backend 屏障后才把草稿改 v2/新增模块；它检查固定 v1、下一调用 v2、helper/resource/执行位、未捕获模块负控、Git 对象/ref/index/config 及实例内部父目录 rename 拒绝、合法写入正控、同 submission 缓存、query/collect、原件/产物实体和终态清理。它不执行真实用户数据、H/A、训练、solver、SSH，也不重新提交任何 unknown。

缓存探针先保存已知成功原 run 的准确 ref/bytes，完成该原 call 的只读 query 后，再回放原 ref；不把 watcher 对连续相同 bytes 的忽略当成缓存响应，也不重提 unknown。`execute_native` 返回的已封存科学结果保持原协议；启动、usage、权限和 cleanup 只取准确原 `native-outcome.json`，不从科学结果推断宿主事实。

每批保留 `runner.log`、guard 原件和结果报告；真实 W fixture 留在 `guard/batch-001/fixture-logs/worker-managed-fixture`，失败/unknown 也保留。报告只 allowlist 原 launcher/backend argv、原权限、call/step/commit/逻辑入口和直接可观察事实，原 native stdout/stderr 由既有 guard 限量计入；不回显 auth/session/env。源码身份只记录准确 HEAD 和明确列出的相关文件 SHA-256/有界 tracked 差异，不扫描工作区或未追踪文件；dirty 是报告限制，不是运行准入门槛。只有全部必需断言、实际到达层、退出码及 guard cleanup 均完整才可得到 `engineering_passed`。这不是完整 G1/G3 验收；完整注册 Cov、H→W→A、V2 捕获两进程屏障、取消/延迟 replay/崩溃注入和 V12 跨文件系统仍为 `not_run`。报告不供运行时消费，不授科学资格、预算或执行权限。
