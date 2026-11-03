# SciBranch TCAD 执行插件

简体中文 | [English](README.md)

薄科研 CLI 加载 `tcad_execution` 执行接口，冻结选定 adapter、传输配置与实现输入，
再通过宿主可信执行门执行副作用。安装只注册插件，不连接或验证远端 solver。
配置与调用见[CLI 指南](../../docs/ROOTLESS_CLI.zh-CN.md)和
[传输合同](../../docs/tcad_transport_contract.md)。

选择 `tcad_execution:tcad` 时必须显式填写 `config_file`；不接受内联 TCAD 配置，也不自动查找部署默认值。插件文件支持 JSON/TOML 对象，包含 `transport = "command"` 与 `command_config_path`，或 `transport = "socket"` 与 `socket_path`。本地引用按各自配置文件所在目录解析。准备阶段仅本地检查命令/SSH schema、引用文件与可选 `implementation_compilation.capability_snapshot_path`，不联系后端、不创建 adapter 状态。有效配置随实例冻结；修改顶层插件文件不会修改既有实例。
新 socket-backed preparation 在准入或提交前明确拒绝；准确旧调用的查询仍受支持。
新受管执行使用 command transport。

适配器保留执行 schema、项目物化、传输和策略检查。曲线、PLX 与日志分析由
[curve-analysis Skill](../../skills/curve-analysis/SKILL.md) 提供；接口见[执行合同](../../docs/PLUGIN_RUNTIME_API.md)。
科学 Worker 编写 solver 项目并判断结果；确定性代码解析精确源码、case anchors、capability、
文件绑定与资源策略，不发明物理模型或从不完整请求生成求解器代码。

## 执行后端

`tcad_control` 管理运行目录与 detached 进程状态，提供 capability/policy 查询、原提交精确
查询、提交、状态、取消、检查和收集。相同冻结描述符的提交幂等。响应不确定时核对原身份；
查询不可用不授予另一次提交权限。

`TCADJobSpec` 绑定归档描述符、成员清单、部署方工具 profile、可信执行用途、资源限制和
预期输出。任务协议 schema version 5 包含 `collect_generated_outputs`，以及墙钟、CPU、
内存、输出和总存储限制。控制端与 runner 必须使用兼容协议。
Solver capability snapshot 使用 `tcad.solver-capability.v2`；私有 executable 路径、环境和
固定参数不进入公开 snapshot，计数与摘要绑定私有设置，公开参数须管理员逐项选择。
改变科学输入形成新实现，适用资格必须重新建立。

有界 SSH command transport 通过已有授权 OpenSSH 连接调用 `remote_runner_py36.py`，
`transfer_chunk_bytes` 默认 1 MiB。应用安装器与 `deploy/reinstall.sh` 发布本地应用代码；
远端 runner 部署是独立管理员动作，须明确配置和授权，参见仓库根目录
`deploy/install_ssh_tcad_runner.sh`。更新本地代码不更新远端协议，示例及安装成功均不授予执行权限。

## 策略、计量与恢复

管理员配置须包含合法 `agent_execution_policy`、`runner` 和 `debug`，参见
[策略示例](config/execution-policy.example.json)和[runner 示例](config/remote-runner.example.json)。
缺失或非法策略拒绝。示例阈值可配置，与 RAM 和传输额度独立。策略授权绑定精确请求、执行包、
编译身份、预算及 policy digest；独立审查不构成执行决定。Runner 提交时重新核对授权，随后冻结资源设置。
独立远程 runner 从创建已接受运行目录起占用并发名额，包含 Worker 尚未启动的阶段。
未完成或结果不明的启动保留名额，直到有终态完成事实；重试相同描述符不会再启动 Worker。

Worker 输出收集以非阻塞方式拒绝 FIFO 等非普通文件。首次收集、错误后收集及诊断收尾
共用已有的 `collection_total_seconds` 时限并检查取消。收集停止时保留已捕获记录，
发布失败或取消终态，不产生另一次执行权限。

提交前预留资源；重命名或修订科学项目不产生新额度。终态观测按累计墙钟和存储高水位结算，
观测缺失和未知提交保留预留。策略收紧不清除已消费量。只有绑定原调用和精确描述符的
权威永久未提交证明，由原预算主体消费后，才可释放预留；查询无结果、取消、超时或未知状态
均不构成这种证明。诊断尝试同样保留配置界限与持久身份。

总存储包含逻辑输入、中间文件、输出和日志，输入删除仍计费；stdout 保留镜像保守计入。
Runner 采样目录大小，超限停止进程组；manifest 记录观测高水位、采样周期与超调，
不声称瞬时峰值或文件系统硬配额。科学输入物化、归档、传输与收集采用文件流；
小 JSON 和预览使用独立限制，扩大传输额度不授权更大作业。

SDevice grid 输入绑定精确科学文件原件与实现 slot，物化保留原字节和来源。
缓存响应本身不是可信执行证据；恢复须核对原提交与文件身份、保留计量，先核对同一调用再采取后续动作。

## 信任与资格

后端负责执行事实，科研控制器负责科学绑定、预算与权限决定。本地域 daemon 分开进程和故障，
仍采用同用户信任边界；同用户 shell 可能访问控制文件、socket 与密钥，见[安全说明](../../SECURITY.md)。

Solver 项目声明物理代码、输入、参数与原始 TDR/PLX/PLT/log；解析器和评分生成观测与指标，
科学结论属于 Worker。真实 solver、SSH 及科学链资格须绑定精确实现和环境。
插件不分发 Synopsys executable 或许可证。

## 资格边界

本节拥有当前 TCAD 资格边界。[架构](../../docs/ARCHITECTURE.zh-CN.md)拥有控制职责；
[传输合同](../../docs/tcad_transport_contract.md)拥有领域执行和收集行为。

薄科研链能够冻结所选 TCAD 执行接口，保留精确准备输入和收集原件，请求精确对象的独立
审查，绑定执行权限并累计有限资源消耗。可选原生实现编译提供非空输入链。自包含 V2
package 与 observer 声明来源可在冻结的声明范围内提供经核验的输入绑定和作者关系。
来源缺失或明确不完整仍保留不确定性，不能据此证明任意 native 或 solver 文件外读已经封闭。

定向源码、合成进程和 loopback HTTP 检查覆盖所选绑定、审查、预算、原调用恢复及收集路径，
不证明所有组合、已安装 solver 行为、物理模型或科学主张已经合格。
删除旧 Run/SQL 宿主及 wheel/source 打包检查不增加 solver 资格。

已维护的证据尚不能证明真实 Sentaurus SProcess 或 SDevice 的执行—收集—解析闭环、
合格 TDR metadata provider 或具体图中科学主张。这是证据边界，不是对当前机器的
可执行文件、许可证或远端 runner 的实时探测。mock 输出、合成 PLX/PLT fixture、
历史 solver 结果和工程测试通过数量均不能建立这些资格。

提升资格必须提供绑定精确候选与受控 runner 的证据：

- 提供可用 solver 许可证，冻结 solver capability、release 证据、获准的 executable/arguments、
  环境和精确输入 package；执行前拒绝 capability 漂移。
- 完成经授权的真实 SProcess smoke，贯穿执行、收集与解析。保留精确输入/输出原件及其
  绑定、原始日志、release 和 parser 证据、失败、限制及所选合同要求的独立审查。
  控制层采用架构的引用合同；本步骤不要求科学文件哈希。
- 单独验证 SDevice，具备可用许可证、真实 TDR 证据和合格 metadata provider；
  SProcess 成功不能授予该资格。
- 物理模型或图中主张须基于精确科学证据及适用条件判断；执行成功和 parser 接受本身
  不能证明科学正确。

执行权限来自冻结策略或精确人工决定。科学审查、solver 资格与执行授权相互独立。
其他尚未取得资格的部署和恢复路径见[发布范围](../../docs/RELEASE.zh-CN.md)。
