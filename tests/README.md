# 测试分组与资源边界

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

安装组不运行科学实验、H/W/A、daemon业务、评分或恢复矩阵；这些检查回到对应功能组。一个选定cohort只建一次环境、安装一次，不切换环境删除后再重建。默认core，只有涉及TCAD交付元数据时显式选tcad；其余旧包装组合未验证，不构造新矩阵补齐。

`tests/installation/check_source_release.py` 是**单独显式选择**的源码发布包检查，名称不匹配默认测试发现。它检查发布文件、原件hash与manifest，不运行发布包中的pytest或安装检查。源码发布与wheel是不同交付路径，不能以一个冒充另一个；不在默认安装烟顺带构建整份源码发布包。

`tests/installation/check_retired_entrypoints.py` 也须显式选择，用于安装产物的退役入口检查；默认 installed 组只发现 `test_packaging.py`。两项 `check_*.py` 按各自发布变更范围运行，不能把默认安装烟的通过写成它们已验证。

## 选择与证据

- 先查目标用例的传递fixture、子进程、数据规模，再决定是否运行。
- 一个检查必须说明能证伪哪个生产故障。固定成功JSON只证明传输时，不得当作科学质量或执行成功证据。
- 去掉无效常量补丁、固定文件数量断言和无必要笛卡尔积；保留不同副作用入口的真实负控。
- 修正后只重测受影响路径，不重建环境重跑整组功能。
- 独立审查默认只读，不另起验收批次。禁止并行测试/安装；必要锁竞争用例中的子进程不等于并行测试批次。

串行入口为 `scripts/run_tests.py`。确认资源条件与执行范围后，使用精确目标和仓库本地持久日志目录。例如功能目标选择 `--lane source --output .trial/check-name tests/research_loop/<文件>::<用例>`；独立安装选择 `--lane installed --installation-cohort core --output .trial/check-name <精确目标>`。日志属于本地检查证据，不进入源码跟踪。

`--per-file` 会重复启动解释器与session fixture，不作为默认全目录执行方式；安装组禁止该选项。数据/进程用例显式标记 `stress`/`process_e2e`。collect-only也会导入测试，不视为无成本静态检查。

全量执行前按真实分组和已有耗时记录准备精确目标清单，串行分成可在现有 300 秒时限内结束的批次；长文件可按 node ID 拆分。时限属于每批而非整轮，不应把所有文件合成一个超大批次或提高限额绕过失败。CI 当前仍逐文件执行两组，存在空组重复收集成本；调整分批必须同时核对完整覆盖与耗时，不能仅为减少启动次数取消边界。失败、超时、跳过和未运行分别保留记录。

五项生命周期缺陷的专项回归可直接运行 `python scripts/verify_lifecycle_fixes.py`，默认依次执行 `test_instance_delete.py`、`test_fork_consumer_recovery.py`、`test_remote_runner_recovery.py` 各自的 source/process，共六个小批次。`--list` 只静态列出范围，不导入 pytest；`--case delete|fork|remote` 可选择其中一组。脚本从自身位置定位仓库，可在其他工作目录使用绝对脚本路径调用；所有批次使用同一个 Python 解释器与现有 `scripts/run_tests.py` 默认资源限制，首个失败即停止，不自动重试。

默认日志目录为仓库内 `.trial/lifecycle-validation-<UTC时间>-<随机后缀>`；`--output .trial/<新目录>` 可指定仓库内的新目录，相对路径以仓库根目录为基准，既有目录拒绝覆盖。每批保留 `runner.log` 和 `guard/` 原始证据，聚合 `summary.json` 记录命令、退出码、原始 guard 结果及未运行批次。只有退出码为 0、guard 报告 passed 且清理完成才通过；Ctrl-C/TERM 会转交现有 guard 并等待收尾。这是五项专项回归，不是功能全量、真实模型 Worker、solver 或 SSH 验收。

## 资源与支持边界

`scripts/run_tests.py` 现有RSS、可用内存采样和超时止损不是内核聚合硬内存限制。安装与功能分组不能替代资源隔离；是否具备资源域隔离须由具体运行环境证明，不能从测试通过推断。

薄层检查不依赖旧 Run/Artifact/session 宿主 fixture；共享库检查可以使用其合同所需的独立存储原语。未运行的源码、安装、真实平台资格分别记为未验证，不将静态审查写成动态通过。
