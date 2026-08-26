# ADR-0007：采用 OpenSandbox 可执行研究 Workspace

- 状态：已接受
- 日期：2026-08-26
- 决策者：项目维护者

## 背景

Phase 5 切片 1–6 已建立 `AgentSession`、逐轮 `AgentTurnRun`、`ResearchAgentRuntime`、Deep Agents
原生 Harness、Project Research Context 和 ARQ Worker 内的跨进程恢复边界。当前实现仍使用
`StateBackend`，生产 Worker 仍固定为 Fake Runtime，尚未接入真实模型、Sandbox、Browser、MCP、Skill
或 `WorkspaceSnapshot`。

上述是本 ADR 作出时的基线。切片 7.0 随后已经完成真实 Runtime enablement 的实现，默认仍为 Fake，
显式 `deep_agents` 模式才装配真实模型与持久 Checkpointer；OpenSandbox、Browser、MCP、Skill 和
`WorkspaceSnapshot` 仍未在 7.0 接入或验证。

后续能力 Spike 需要允许 Research Agent 在隔离环境中执行 Python 数据处理、生成图表，并让文件工具、
代码执行和浏览器下载操作同一个 Session Workspace。仅把 Sandbox 当作受限文件后端、同时隐藏
`execute`，会削弱 Deep Agents 完整 Harness 的价值，并迫使平台重新包装一套脚本执行能力。

另一方面，Deep Agents 的 `permissions` 主要约束内置文件工具，不能保护 Sandbox `execute`、自定义
Tool 或 MCP Tool。开放 `execute` 后，模型可以运行任意 Sandbox 内命令，也可能通过 Python、`curl` 等
绕过 Browser Tool 的 URL 校验。因此安全边界必须落在物理 Sandbox、网络、Secret、资源、Workspace
取回和业务提交协议，而不能只依赖 Prompt、Tool 描述、文件权限或命令字符串检查。

## 决策

### Runtime 与 Sandbox Provider

- Deep Agents 继续运行在 ADR-0006 确定的 ARQ Worker 内；Agent 不在 Sandbox 内启动，也不新增独立
  Agent Server；
- 选择 OpenSandbox 作为 Phase 5 Slice 7 的远程 Sandbox Provider。平台通过 SDK-neutral Adapter 管理
  OpenSandbox，业务 Port、Domain、公开 API 和数据库枚举不暴露 OpenSandbox 或 Deep Agents SDK 类型；
- Deep Agents 作为完整 Agent Harness，负责 Agent Loop、消息与压缩、Checkpoint、文件工具、
  `execute`、Tool/MCP/Skill 组合；平台负责身份、Project 授权、策略、预算、恢复、审计、Workspace
  快照和 Artifact 提交；
- OpenSandbox、Browser、MCP、Skill 和真实 Provider 调用都必须发生在业务数据库事务外。

### Session、Lease 与恢复

采用以下稳定映射：

```text
AgentSession / SDK Thread 1
  └─ active Sandbox Lease 0..1
       ├─ opaque sandbox_id
       ├─ monotonic generation
       ├─ pinned image revision
       ├─ expiry / status
       └─ physical /workspace
```

- 一个 `AgentSession/SDK Thread` 最多复用一个短 TTL Sandbox Lease，跨 Turn 保持 Workspace 连续性；
- Sandbox 不跨 owner 或 Session 共享。同一 Session 的单活动 Turn 规则继续防止并发修改 Workspace；
- Lease 过期、Provider 丢失、取消后环境不可信或策略要求重置时，平台递增 generation、创建新 Sandbox，
  并从最近一次允许的 `WorkspaceSnapshot` 和显式授权 Artifact 重建；
- Sandbox ID、endpoint、CDP/VNC 地址和 Provider 凭据都是内部信息，不进入公开 API、Prompt 或业务 Event；
- 创建、续租、销毁和孤儿清理必须可对账。Sandbox Provider 的成功不等于业务 Workspace 或 Turn 已提交。

### Backend 与 Workspace

OpenSandbox Backend 作为 Deep Agents `CompositeBackend` 的默认 Backend，使文件工具与 `execute` 都操作
同一个 Sandbox 文件系统。Deep Agents 内部路径，例如 `/conversation_history/` 和
`/large_tool_results/`，路由到 `StateBackend` 并随 SDK Thread Checkpoint 持久化，不混入用户研究
Workspace。

逻辑 Workspace 属于 Session；物理 `/workspace` 属于当前 Sandbox Lease。平台不挂载 API/Worker 宿主
目录，而通过 Provider 文件传输或受控 Adapter 注入和取回文件。只允许 Manifest 中的 `/workspace`
路径成为 `WorkspaceSnapshot`；临时文件可丢弃，正式用户产物必须离开 Sandbox 后重新校验来源、路径、
哈希、MIME、大小和 Project 所有权，才能进入 staged/committed Artifact 生命周期。

Provider 自带的持久卷或快照只可作为实现优化，不能成为 Session Workspace、Artifact 或业务恢复的唯一
事实来源。

### `execute` 与安全边界

Phase 5 Slice 7 允许模型调用 Deep Agents 的 Sandbox `execute`，但只允许在当前 Session 专属的
OpenSandbox 中执行。该能力不等于开放宿主 Shell、宿主 Python 或通用 Coding Agent。

- Sandbox 使用非 root 用户、固定镜像和固定依赖；首版预装 Python、pandas、numpy、matplotlib 和必要
  字体，允许研究数据处理与 PNG 等图表输出；
- 不允许动态安装依赖或让模型选择镜像；包管理器即使存在也必须由网络策略阻断或从 Agent 可用路径移除；
- 不挂载宿主源码、用户主目录、数据库/Valkey/Docker Socket、云凭据、Provider Key、OpenSandbox Key、
  MCP Token 或其他 Secret；
- 限制 CPU、内存、PID、磁盘、文件数、单文件大小、墙钟时间、命令时间和 stdout/stderr/Tool 输出；
- 离线 Sandbox 命令不逐条人工审批。扩大网络范围、外部副作用和正式 Artifact 提交仍由独立策略或审批
  控制；
- 每次模型或 Tool 边界继续检查业务取消、RuntimeExecution lease/fence 和预算；取消后不得启动新命令，
  已在途命令的晚到结果只能用于清理和对账；
- 不使用命令字符串 allowlist 冒充强隔离。`execute` 的主要安全边界是 Sandbox、网络、Secret 和资源
  配置；Deep Agents `permissions` 不是该能力的授权边界。

### 网络、Browser 与下载

- Sandbox 默认拒绝出站网络；只由平台为固定 Browser 研究目标配置 egress allowlist，模型、用户、网页
  或 Skill 不能扩大网络目标；
- egress policy 作用于 Sandbox 内全部进程，包括 Chromium、Python 和命令行客户端，不能只检查
  Browser Tool 参数；
- Browser 通过自定义 `browser_*` Tool 使用同一 Sandbox 中的 Chromium/CDP。模型不能看到或提交 CDP、
  VNC/noVNC endpoint；
- Browser 下载先进入 `/workspace` 的隔离 incoming 区域，经过来源、最终 URL、哈希、MIME、大小和文件
  策略校验后，才能进入 WorkspaceSnapshot 或候选 Artifact；
- noVNC 在 Phase 5 只可作为可信本地诊断能力。面向用户的画面需要浏览器鉴权代理和 owner/Session 映射，
  留到 Phase 6 UI/安全强化；
- 首版仍不支持登录站点、Cookie/用户凭据委托、付费墙、CAPTCHA 或对外写操作。

### MCP 与 Skills

- MCP 通过 `langchain-mcp-adapters` 转换为 LangChain Tool 后传入 `create_deep_agent`；
- MCP Server 只能来自平台维护、版本化的 Catalog。用户不能提交 endpoint、URL、transport、command、
  env、认证信息或任意 Server 配置；
- 优先使用受控 Streamable HTTP，开源 MCP 优先部署为独立容器并固定镜像/版本。Phase 5 不在 ARQ Worker
  宿主以 stdio 启动第三方 MCP 子进程，也不把 OpenSandbox 管理 MCP 暴露给模型；
- 首个 MCP 固定为只读 `search_arxiv_metadata`；默认 stateless。普通测试连接本地确定性 Server，真实
  arXiv Smoke 必须显式启用；
- MCP Tool 加载后必须按名称、输入 Schema、版本/哈希和 allowlist fail-closed 校验；平台 interceptor
  注入稳定 Turn scope，并执行权限、取消、预算、超时、输出限制、Secret 过滤和 ToolExecution 审计；
- Skill 只来自平台维护的固定版本 Catalog。Skill 不能授予 Tool、网络、Sandbox 或 Secret 权限；如需
  脚本，脚本必须随固定 Sandbox 镜像或受控只读路径发布，执行输出只写 `/workspace`。

### Slice 7 顺序与阶段边界

Slice 7 调整为五个独立、可回退提交：

1. 7.0 Real Deep Agent Runtime Enablement；
2. 7.1 OpenSandbox、Sandbox Lease 与 WorkspaceSnapshot；
3. 7.2 同 Sandbox Browser 与下载；
4. 7.3 固定平台 MCP；
5. 7.4 平台 Research Skill。

### Slice 7.0 已固定的 Provider 与费用边界

- 精确使用 `langchain-deepseek==1.1.0`；解析后的新增传递依赖为
  `langchain-openai==1.6.0`、`openai==3.3.1`，不升级既有版本；
- Worker 通过独立的 Agent 配置选择 `fake | deep_agents`，默认 Fake 不读取 Provider Key、不构造模型、
  不打开 Agent Checkpointer；真实模式缺少专用 Key 时启动前 fail-fast；
- 真实模型固定 `deepseek-v4-flash`、thinking 关闭、输出 token 上限，复用通用 timeout/retry；模型 SDK
  类型只存在于 infrastructure，Worker 业务执行仍只依赖 `ResearchAgentRuntime`；
- `PolicySnapshot.max_model_calls` 只定义为逐 Turn 的主 Agent Loop 调用预算，在主模型 node 前预留并随
  checkpoint 持久化。它不覆盖 Provider 已在途的不确定窗口，也不覆盖
  `SummarizationMiddleware._summary_model.with_retry()` 最多 3 次内部 Provider 尝试，因此当前不是完整
  Provider 费用硬上限；不为此禁用 Deep Agents 原生压缩。Checkpoint 只保存当前 Turn ID 与预留计数，
  新 Turn 覆盖旧值，避免长期 Session 按 Turn 无界增长；新增 State 将 graph revision 固定为
  `deep-agent-graph.v2`，旧 v1 状态拒绝自动恢复；
- 切片 7.0 继续使用单 `AsyncConnection` 与 singleton `AsyncPostgresSaver`。本地版本的 Saver 实例锁保证
  协程正确性，但 checkpoint I/O 串行且有单连接故障面；pool 与 per-execution Saver/graph factory 留到
  7.1 验证；
- Slice 1 的固定 Policy 仍是单主模型调用且无 Tool。7.0 只启用无 Tool 单 Turn 的真实路径；7.1 前需要
  增加并验证服务端固定 Capability Profile，不能据此宣称真实 Project Tool 研究回路已完成。

这会把 OpenSandbox `execute`、Session 级 Lease、最小网络/资源边界和 WorkspaceSnapshot 从原 Phase 6
计划提前到 Phase 5 Spike。Phase 6 仍负责完整 Registry、用户从已审核 Catalog 中选择、OAuth/
Credential Vault、复杂审批中心、更广网络策略、Prompt Injection/恶意文件专项测试、Agent Chat/noVNC
鉴权 UI、运行监控和公网多租户强化。

## 被替代或修订的既有决定

- ADR-0001 中“Phase 5 默认关闭任意 Shell”的表述只适用于 Slice 1–6；Slice 7 起由本 ADR 的隔离
  Sandbox `execute` 决定替代；
- ADR-0005 中“物理 Sandbox 以 Turn 或短 TTL 使用”和“首版隐藏 `execute`”被修订为 Session/Thread
  级短 TTL Lease 跨 Turn 复用，并在 OpenSandbox 中开放 `execute`；其 Session/Turn/Thread/Workspace
  所有权决定继续有效；
- ADR-0006 的 ARQ Worker 内 Runtime、RuntimeExecution lease/fencing、同步 checkpoint 和版本兼容决定
  不变。Sandbox Lease 是远程执行环境生命周期，不替代 RuntimeExecution lease。

## 后果

正面影响：充分复用 Deep Agents 的原生 Backend、文件与执行能力；Browser 下载、Python 分析、绘图和
Workspace 文件使用同一隔离环境；平台无需自研第二套脚本 Harness；Session 跨 Turn 研究体验更连续。

代价与风险：OpenSandbox 成为新增外部基础设施和供应链依赖；任意 Sandbox 命令显著扩大 Prompt
Injection 后果；网络策略必须覆盖全部 Sandbox 进程；需要处理 Lease 泄漏、Workspace 冲突、镜像升级、
资源耗尽、文件取回和命令在途崩溃窗口。Sandbox 隔离不等于 Prompt Injection、恶意文件、Provider
可用性或公网多租户安全已经解决。

## 被否决的方案

- **继续使用 StateBackend 作为默认 Backend 并隐藏 `execute`**：安全表面较小，但无法自然支持同一
  Workspace 中的 Python 分析、绘图和 Browser 下载，且会重复包装执行能力；
- **把 Agent 本身运行在 Sandbox 内**：隔离更彻底，但会把 ARQ、数据库连接、模型 Secret 和 Runtime
  恢复拓扑一并迁移，超出当前个人项目范围；
- **每个 Turn 新建 Sandbox**：隔离清晰，但需要每轮上传/下载完整 Snapshot，弱化持续 Workspace 体验；
- **一个 owner 共享一个长期 Sandbox**：成本低，但会造成跨 Project/Session 状态混用和并发风险；
- **挂载宿主 Workspace**：实现简单，但失去宿主文件隔离，明确禁止；
- **让模型调用 OpenSandbox 管理 MCP**：能够动态创建和管理 Sandbox，但会绕过平台 Lease、策略和清理
  所有权；
- **在 Worker 宿主用 stdio 启动任意开源 MCP**：部署方便，但第三方进程可读取 Worker 文件和环境变量，
  不符合 Secret 与宿主隔离边界；
- **动态 `pip install`**：灵活，但引入网络、供应链、不可复现和持久化污染风险。

## 验证门槛与非声明

本 ADR 记录已接受的计划，不表示以下能力已经实现或验证。每个 Slice 必须分别形成测试和真实 Smoke
证据：

- 7.0：配置/factory/Worker 生命周期和主模型预算可由离线测试验证；真实 Provider Smoke 必须显式
  opt-in、单 Turn、有界且不触发 summarization。当前实现未使用真实 Key 或发出真实请求；
- 7.1：owner/Session 隔离、稳定 Lease/generation、跨 Turn 复用、TTL/销毁/reconcile、Sandbox 丢失后
  Snapshot 重建、宿主/Secret 不可见、默认禁网、资源/输出限制、取消后不启动新命令；
- 7.2：Browser/CDP 与文件工具指向同一 Sandbox，导航/redirect/egress/下载边界、文件取回和 endpoint
  不泄漏；
- 7.3：MCP Catalog、Schema 漂移拒绝、stateless 会话、interceptor、预算/取消/输出限制和执行记录；
- 7.4：Skill 版本/哈希、能力依赖、只读加载和权限不扩张。

普通自动测试继续完全离线、确定性、零模型/网站/外部 MCP/付费 Sandbox 费用。真实 Provider、
OpenSandbox、Browser 和 arXiv MCP Smoke 必须显式启用、限制预算并记录准确版本、配置、命令、耗时和
结果。未通过对应门槛前，不得宣称 OpenSandbox 隔离、网络限制、跨 Turn Workspace、Browser、MCP、
Skill 或代码执行已经达到生产安全。

## 参考资料

- [Deep Agents Comparison](https://docs.langchain.com/oss/python/deepagents/comparison)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Deep Agents Skills](https://docs.langchain.com/oss/python/deepagents/skills)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)
