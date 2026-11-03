# SciBranch

**面向迭代式科学发现的开放框架。**

简体中文 | [English](README.md)

原创软件按 [MIT 许可证](LICENSE)开源。

SciBranch 将研究目标推进为一个迭代循环：探索多条假设路线、实施实验、独立评估证据，
再决定下一步研究方向。它连接科学 Agent 与受管执行，保留可追溯的输入和成果，
并支持查看、暂停、继续研究，以及从历史任务创建新分支。

科学 Worker 负责模型选择与结论。控制层负责精确身份、冻结输入、有限预算、执行权限和
已知调用的恢复。控制保留候选和证据，科学评估负责比较并选择交付对象。

当前是面向 Linux 的工程原型，依赖 POSIX 文件/锁语义与 Linux namespace。
原生 Windows 不支持，WSL2 未验证。命令或求解器运行成功，只支持其精确实验与验证合同
实际建立的结论。

## 从假设到证据

- **探索竞争路线。** 围绕明确目标与输入运行科学线程，由独立评估指引后续迭代。
- **运行真实实验。** 使用受管本地执行或显式选择的领域适配器，分别声明权限与有限执行限制。
- **查看研究过程和成果。** 通过 CLI 或可选实例网页读取封存结果、保留成果和限制说明。
- **有依据地继续或分支。** 暂停和续接实例、恢复已知调用，或从精确历史逻辑任务创建暂停分支，
  并为新分支声明新的有限预算。

受支持命令为管理研究实例的 `scid-research` 与管理实验的 `scid-experiment`。
`scid-research serve-ui` 通过可选网页提供同一实例的控制与结果读取；网页服务退出后，
detached 研究仍可继续。

## 安装

克隆 [SciBranch 仓库](https://github.com/mae032241/scibranch)：

```sh
git clone https://github.com/mae032241/scibranch.git
cd scibranch
```

推荐 Python 3.12，包支持 Python 3.10+。按[安装指南](docs/INSTALL.zh-CN.md)
准备带运行与构建依赖的 Python，再以当前用户离线安装：

```sh
SCID_PYTHON=/absolute/python bash deploy/install.sh --dry-run
SCID_PYTHON=/absolute/python bash deploy/install.sh install
export PATH="$HOME/.local/bin:$PATH"
scid-research --help
```

安装器发布两个命令与随附的运行时 Skill。默认代码与 Skill 前缀仍为
`$HOME/.local/share/scidiscovery`，命令目录为 `$HOME/.local/bin`，安装事务目录为
`$HOME/.local/state/scidiscovery/install`。Python 包名仍为 `scidiscovery`；
这些技术标识不随 SciBranch 项目名称改变。更新已有安装时，先停止其控制进程、Worker 和 UI，
再使用 `reinstall`。依赖、可选插件、更新与卸载见[安装指南](docs/INSTALL.zh-CN.md)。

## 开始研究

将 [research.example.toml](research.example.toml) 复制为 `research.toml`，声明目标、
精确公开输入、后端权限与有限预算，并创建声明所指向的文件。然后校验和准备实例：

```sh
scid-research config-check --config research.toml
scid-research prepare --config research.toml
scid-research config-inspect --root /absolute/path/to/research-instance
scid-research start --root /absolute/path/to/research-instance
```

`--root` 使用 `prepare` 创建的精确实例根目录。配置文件修改影响新实例，已有实例保留其冻结设置。
[配置参考](docs/RESEARCH_CONFIGURATION.md)说明后端、权限、预算与 Skill 选择；
[CLI 指南](docs/ROOTLESS_CLI.zh-CN.md)说明结果读取、UI、暂停与继续、历史任务分支及恢复。

核心提供 `local_execution:command`。通过 `SCID_PLUGINS=cov_execution` 显式选择可选 Cov 执行，
或通过 `SCID_PLUGINS=tcad_artifact` 选择 TCAD adapter。运行时 Skill 位于仓库根目录的 `skills/`，
并持久发布到安装前缀的 `skills/`，须按角色或命名 Worker 显式选择。
Skill 不授予执行权限或科学资格，详见[接口参考](docs/PLUGIN_RUNTIME_API.md)与
[配置参考](docs/RESEARCH_CONFIGURATION.md)。

## 文档与支持边界

[文档入口](docs/README.md) · [CLI 指南](docs/ROOTLESS_CLI.zh-CN.md) ·
[当前架构](docs/ARCHITECTURE.zh-CN.md) · [发布范围](docs/RELEASE.zh-CN.md) ·
[安全边界](SECURITY.md) · [贡献指南](CONTRIBUTING.md)

当前运行时只接受协议 **29** 的实例，不提供旧格式兼容读取或迁移。单节点 token 目标为软目标，
共享准入预算与有限执行限制仍受约束。完成、权限、候选与恢复的详细合同由
[架构](docs/ARCHITECTURE.zh-CN.md)定义。

Worker 与宿主同权限进程受到信任，控制层属于软边界。实际访问取决于声明的后端与权限投影。
科研预算、审查和用户陈述均不授予执行权限。未知外部提交须按原调用核对，不得自动重提。

浏览器、真实原生线程续接、TCAD solver 和实际掉电恢复需要精确候选及环境的证据，
源码测试不能建立这些资格；跨宿主完整灾备不在当前支持范围。历史包保持只读，
独立归档包与整实例冷检查点迁移命令已退役。

源码维护入口为 `src/scidiscovery/research_loop/` 中的控制器、`plugins/` 中的适配器，
以及 `skills/` 中的运行时科学工具和指导。按[测试分组](tests/README.md)选择精确目标并串行检查。
开发计划、审计、交接与试验输出作为本地工作资料保存。分发源码前，按
[分发声明](NOTICE.md)核对随附第三方资料的原有版权和分发条款。
