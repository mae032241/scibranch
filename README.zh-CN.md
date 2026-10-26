# SciDiscovery 科学发现 Agent

简体中文 | [English](README.md)

当前框架仅支持 Linux，依赖 POSIX 文件/锁语义及 Linux namespace；原生 Windows 不支持，WSL2 未验证。Skill 来源绑定与角色选择是通用合同，只读 namespace 挂载是 Linux backend 实现。

SciDiscovery 是以 CLI 为核心、提供 Codex 与显式 Agent 协议后端的科研框架。
它围绕明确目标与冻结输入运行 H/W/A/E 线程，提供有限共享预算、科学结果封存及原调用恢复。科学 Worker 负责模型选择与结论；
确定性控制负责身份、输入绑定、资格校验、预算和执行权限。

候选指标、审计回执和可用性供科学作者判断。控制保留观察顺序和全部候选；A 必须明确命名
交付对象，并以已绑定证据说明理由。控制不提供科学排名或默认选择。

本地结果 `candidates` 列出全部可由 A 选择的保留模型，每项须绑定不同的精确保留成果；身份由原调用和 artifact 路径决定，不随列表重排变化。指标和科学说明可缺失。可选说明与比较表不构成交付准入；
坏条目局部诊断并保留原件，紧凑预览提供完整留存内容的读取入口。

当前是工程原型。命令或求解器运行成功，只支持其精确输入、实验和验证合同实际建立的结论。

## 可以做什么

| 入口 | 用途 |
| --- | --- |
| `scid-research` | 准备实例，运行、查看、暂停和继续研究，收束并导出结果。 |
| `scid-research start` | 为明确实例启动或连接 detached 控制进程。 |
| `scid-research fork-preview` / `fork-create` | 从精确历史逻辑任务预览并创建暂停的新分支。 |
| `scid-research instance-delete` | 按精确身份永久删除静止实例；同一操作通过持久回执核对。 |
| `scid-research serve-ui` | 同一实例的可选网页，提供实例控制和封存结果分页。 |
| `scid-research archive-*` / `checkpoint-*` | 导出和核验原件、恢复指定材料，或执行限定的同宿主冷迁移。 |
| `scid-experiment` | 使用受管本地实验或声明的领域执行接口。 |

核心只安装本地执行接口。Cov 执行、回执、公开观察与交付由可选 `cov_execution` 插件提供，
通过 `SCID_PLUGINS=cov_execution` 显式安装；执行器标识保持 `cov_execution:experiment`。
可选 TCAD adapter 通过有界传输提供 detached solver 执行。确定性曲线评分和论文图提取迁为
自包含的 `curve-analysis`、`scientific-paper-evidence` Skill，与 `sentaurus-tcad-code`
一起持久安装到安装前缀的 `skills/`。按角色或命名 Worker 显式选择集合，实例冻结选中资产，
节点只投影本角色集合；见[配置参考](docs/RESEARCH_CONFIGURATION.md)。Skill 不授予执行权限或科学资格。
执行适配器合同见[接口参考](docs/PLUGIN_RUNTIME_API.md)。

## 安装与开始使用

提供 Python 3.10+ 和 [pyproject.toml](pyproject.toml) 声明的运行、构建依赖，离线安装应用：

```sh
SCID_PYTHON=/absolute/python deploy/install.sh --dry-run
sudo SCID_PYTHON=/absolute/python deploy/install.sh install
scid-research --help
```

安装发布 `scid-research`、`scid-experiment`，要求使用新安装前缀。通过 `SCID_PLUGINS`
明确选择可选插件；依赖、路径设置与卸载见[安装指南](docs/INSTALL.zh-CN.md)。

开始研究时，复制 [research.example.toml](research.example.toml)，声明目标、公开输入、后端权限及有限预算，
依次运行 `scid-research config-check --config research.toml` 和 `scid-research prepare --config research.toml`。
再以 `config-inspect --root /absolute/instance` 查看冻结设置，增加 `--view current` 查看当前生效与下一新节点待生效的配置；
`start --root /absolute/instance` 后台推进。
配置文件只影响新实例；已有实例保留冻结设置，详见[配置与后端参考](docs/RESEARCH_CONFIGURATION.md)。
网页是复用同一状态与控制逻辑的可选入口；网页服务退出不会停止 detached 研究。
[UI 合同](docs/ARCHITECTURE.zh-CN.md#实例-ui-的控制与显示)说明表单重试、显示缓存与翻译边界。
`result`、`materials`、`artifact` 可通过 CLI 读取或下载精确封存结果。[CLI 指南](docs/ROOTLESS_CLI.zh-CN.md)
包含命令、策略示例、结果读取与恢复规则。单项 `codex` launcher 默认使用本地实验；W
运行期间由原生宿主自动处理普通文件提交并返回响应。受管本地命令需要受支持的原生权限投影，
前提及明确不支持的情形见 CLI 指南。external 执行仍由声明的 launcher 负责。
执行另需冻结配置及授权；科研预算、独立审查和用户陈述均不授予执行权限。

静止的当前协议实例可在精确逻辑步骤 N 处分支，不要求最近 A 已结束研究。新分支在新
session 中从该任务首次输入重跑，显式声明新的有限预算。必要控制、准确代码版本及当前
草稿分别转移；科学数据与输出保持对实际原件的直接引用，删除科学原件可能使相应消费不可用。
科学可见范围仍由所选原任务的冻结输入限定。创建后保持暂停，
须显式 `start --continue` 才推进。历史科学投入、来源限制及未知成本仍保留；既有审查或执行许可
不授权新对象。语义见[架构](docs/ARCHITECTURE.zh-CN.md#历史任务分支)，操作见
[CLI 指南](docs/ROOTLESS_CLI.zh-CN.md#从历史任务创建分支)。

H/W/A/E 单节点 token 预算统一为软目标：触线时提示收口，不仅因达线拒绝新受管实验或强制转 A，原节点可以完成原结果。实际 input+output 完整记账，不预付整个节点 token 额度。当前协议 26 仅接受 soft 节点设置及冻结的最终 A 完成策略。旧实例格式不受支持，不提供兼容读取或迁移。单次执行的有限时间、权限与清理限制仍有效。

新实例冻结一次最终 A 完成策略：共享 token、总时间或步数预算不足、为零或已经超出，
均不据此拒绝最终 A 的进入、完成及其自身明确授权的交付。仅计费用量缺失或未对齐也不阻挡
已核实的原始终态返回及该交付；未知用量保持未知，不补造零账。仍须原生完成事件、退出码 0
和已确认清理。已观测用量与未知部分分别记账，原共享上限不增加；
原单次调用的有限时间、执行权限及其他执行限制仍有效，详见[当前架构](docs/ARCHITECTURE.zh-CN.md)。
显式提高额度阈值后，下一次 run 可在完整准入足够、原执行及外部效果已知且静止时，从 FINAL 前保存的待执行 normal 路由恢复。新物理编号保留先前最终 A、提交和全部消费；再次预算不足时进入新的 FINAL 周期，各周期只运行一次最终 A。降低阈值或增加后仍不足不会恢复。

A 选择候选时须提供 `selection_explanation`，声明当前比较准则及是否修订。
修订须明确承接原准则、修改内容、可定位的证据理由与接受的代价。缺项通过冻结的
同 session 输出修复退回补填，FINAL 仍保留原完成保护与累计用量。
控制只检查完整性，科学充分性由 A 负责；没有候选选择的规划不需要编造说明。
标准 `codex` 的 CLI prepare 默认最多两次仅输出补填，保留原 native home、配置、权限与认证。
显式策略优先；自定义 launcher 与直接 controller API 调用仍须显式修复策略。
该默认值不授权失败任务或科学工作的续跑。

Worker 写普通科学文件，使用薄实验提交与等待接口。其提示词不增加哈希计算、比对、校验或
哈希清单工作；控制程序按[架构的哈希白名单](docs/ARCHITECTURE.zh-CN.md)在宿主私有空间保留精确路径、producer 与大小引用。
已有 Git 工程可按[工程配置主合同](docs/RESEARCH_CONFIGURATION.md#declared-projects--声明工程)
声明只读基础和准确 editable、scratch、output 目录；未声明 `[project]` 时沿用 `code/` 流程。

操作侧证据导出工具将默认省略元数据限制在 8 KiB 内，并按需链接到每页不超过 16 KiB
的完整科学详情；这一限制只针对省略部分，不针对整个证据索引。详情描述缺失材料，
`not_available_in_this_view` 不提供被省略原件。读取入口见[当前架构](docs/ARCHITECTURE.zh-CN.md)。

## 文档与开发

[文档入口](docs/README.md) · [当前架构](docs/ARCHITECTURE.zh-CN.md) ·
[发布范围](docs/RELEASE.zh-CN.md) · [安全边界](SECURITY.md) · [贡献指南](CONTRIBUTING.md)

现行控制器位于 `src/scidiscovery/research_loop/`，领域算法、合同与 adapter 位于 `plugins/`。
按[测试分组](tests/README.md)串行、受监督运行精确目标；功能测试不构建或安装包。
`scripts/build_git_release.py` 从显式选择的源码、当前参考文档、工具和功能 fixture
生成源码包及逐文件路径与大小 manifest。

开发计划、审计、交接与试验输出作为本地工作资料保存，不进入长期源码跟踪或源码发布。
当前用法和行为由代码及当前参考文档定义。

## 支持边界

当前信任 Worker 与宿主同权限进程，控制层属于软边界。浏览器、真实原生线程续接、TCAD
solver 和实际掉电恢复需要精确候选及环境的证据；源码测试不能建立这些资格。
默认受管本地子进程及显式 science launcher 提供各自声明的文件视图，不对泛原生执行
承诺统一物理隔离。权限摘要分别记录请求值、实际策略与实施方式；不受限 Codex 不能强制离线，
protocol launcher 的实际访问保持未知，详见[配置参考](docs/RESEARCH_CONFIGURATION.md)。
跨宿主完整灾备不在支持范围。冻结实例保留原实现身份；未知外部提交须先按原调用身份核对。

受支持的应用入口为上述两个命令。旧 `scid`、Root/MCP 应用、平台角色生成及 legacy systemd
应用安装不可用。分发源码包前，按[分发声明](NOTICE.md)核对软件许可和随附参考资料限制。
