# 发布与验证范围

简体中文 | [English](RELEASE.md)

<a id="current-status"></a>
## 支持范围

应用提供 `scid-research`、`scid-experiment` 与按需启动的实例 UI。科研控制器使用原生科学线程、
明确实例根目录、冻结输入和有限预算。可复用的 Operation 编译器声明、schema、回调及领域
adapter 作为库保留；[Plugin API](PLUGIN_RUNTIME_API.md)说明其当前职责。
[当前架构](ARCHITECTURE.zh-CN.md)和[CLI 指南](ROOTLESS_CLI.zh-CN.md)定义行为、权限及恢复边界。

源码、HTTP 与合成进程检查只建立其实际覆盖的行为，不证明真实原生线程续接、浏览器、TCAD
solver、实际掉电或跨宿主恢复资格。当前信任 Worker 与宿主同权限进程；[安全说明](../SECURITY.md)
描述软边界，[TCAD 资格](TCAD_QUALIFICATION_STATUS.zh-CN.md)描述领域限制。
资格判定只适用于精确实现、输入与环境，新代码不能继承历史判定。

<a id="publication-scope"></a>
## 源码发布

`scripts/build_git_release.py` 是显式源码发布清单。它复制当前源码、部署工具、受支持插件目录、
功能测试及 fixture、当前参考文档和选定开发工具，规范化文本与文件权限并扫描结果，然后写入
`MANIFEST.sha256` 和确定性 `.tar.gz` 归档。覆盖已有输出必须显式提供 `--force`；
需要保留候选时使用新输出目录。

计划、审计、阶段结果、交接、旧规范与试验输出作为本地资料保存，不进入长期跟踪或源码发布。
功能 fixture 位于 `tests/fixtures/` 或 `scripts/acceptance/fixtures/`，与消费者一同发布。
研究数据库、凭据、私有数据、平台生成配置及本地 Agent 指令不发布。
随附参考手册具有独立分发限制，见[分发声明](../NOTICE.md)；发布扫描和校验清单不授予再分发权利。

源码包含独立 `cov_execution` 插件及其消费者；核心 wheel 不注册 Cov 执行入口。

## 检查与保留

按精确 diff 及[测试分组](../tests/README.md)，在串行监督器下选择检查。
`tests/installation/check_source_release.py` 是显式源码发布检查，只校验文件、链接和 manifest，
不安装应用或运行包中的功能测试。涉及 wheel 或安装入口时另选对应检查；源码归档不证明 wheel 完整性。

模型、浏览器和 solver 检查分别需要明确范围、环境与预算。RSS 和时间采样止损不提供内核聚合硬隔离。
检查证据必须绑定精确候选并保留未验证边界，不累加不同版本的通过数作为全套资格。

安装、回退与卸载边界见[安装指南](INSTALL.zh-CN.md)。冻结实现保留原身份，未知提交须按原调用核对。
恢复不能重置已消费预算，也不能用旧账本覆盖已发生的外部副作用。
