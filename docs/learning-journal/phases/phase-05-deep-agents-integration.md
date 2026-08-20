# Phase 5：Deep Agents 集成验证

## 状态

计划中，尚未开始实现。Spec 初版日期：2026-08-20。

进入条件：Phase 4 已完成，Core Research Backend v1 的文献导入、RAG、固定 Review Workflow、Run/Event、Evidence、Artifact、可观测性和评测基线均可独立运行。

2026-08-20 已确定使用基于 LangGraph 的 Deep Agents 作为 Research Agent SDK，选型理由和边界见 `../decisions/0001-select-deep-agents-runtime.md`。它提供与目标匹配的 Agent Runtime、MCP 接入和可插拔 Sandbox Backend 方向，但这些能力不能仅凭 SDK 宣称视为可用。Phase 5 通过受限 Spike 验证其能否遵守本项目的业务状态、权限、安全和恢复边界，并用后续集成 ADR 固化版本、Provider 和部署方式。若关键验证失败，停止进入 Phase 6；Core v1 不受影响。

## 目标和用户可见结果

围绕一个明确且受限的研究任务打通最小 Agent Run：用户选择 Project 和研究目标，Agent 只读取该 Project 已授权的 Paper/Evidence，发现论文官方项目页、公开代码仓库、开放数据集或补充材料，生成带来源的 Resource Manifest，并将一个满足策略的公开文件作为隔离 Artifact 交回系统。

```text
创建业务 Agent Run
  → ARQ Worker 调用 ResearchAgentRuntime
  → Deep Agents 获得最小授权的 Project/Evidence Context
  → 调用受控 Search/Browser/MCP Tool
  → 在隔离 Workspace 中生成 Resource Manifest
  → 下载一个受策略限制的公开文件
  → 平台校验并提交 Artifact
  → 归一化 Event、Usage 和终态
```

本阶段的成功标准不是“Agent 能自主做很多事”，而是能清楚回答：状态由谁拥有、权限在哪里执行、Runtime 断连后如何对账、重复执行如何去重、Workspace 和 Artifact 如何隔离、Deep Agents 升级如何被契约测试约束。

## 已确定的选型边界

- Agent SDK 使用 Python `deepagents`，精确版本在本阶段开始时通过 `uv.lock` 固定，不在 Spec 中预写未来版本号；
- Deep Agents 作为 `ResearchAgentRuntime` Adapter 的内部实现，不进入 Domain 类型、公开 API 或数据库业务枚举；
- 继续以 PostgreSQL 中的业务 Run、Event、Evidence、Artifact 和权限数据为事实来源；Deep Agents/LangGraph Thread、Checkpoint、Store 和 Workspace 只负责 Runtime 内部执行；
- MCP 通过 `langchain-mcp-adapters` 或 Deep Agents 经验证的等价官方接入方式使用；不允许用户提交任意 MCP Server 配置；
- Sandbox 使用 Deep Agents 的可插拔 Backend 能力，但 Provider 和部署方式由本阶段 Spike + ADR 决定；
- `FilesystemBackend` 和 `LocalShellBackend` 直接接触宿主环境，不得用于生产 Agent；
- Deep Agents 的 `execute`、子 Agent 和长期 Memory 均默认不向首个用户故事开放，只有真实需求和安全验证支持时才在 Phase 6 单独启用；
- Browser、MCP、Tool、Sandbox 和 Prompt 不是安全边界。权限、网络、预算、审批和输出校验必须由平台策略和基础设施执行。

## 范围

### 包含

- Deep Agents 最小运行实验：工具调用、流式事件、LangGraph Checkpoint、Interrupt/Resume、取消和错误传播；
- `ResearchAgentRuntime` Port、Deep Agents Adapter 和确定性 Fake Runtime；
- 业务 Run 与 SDK Thread/Checkpoint/Workspace 的稳定映射；
- 最小授权 Context Builder，只提供当前 Project 所需的 Paper/Evidence ID 和小型摘要；
- 一个固定 Research Tool、一个受控 MCP Tool 和一个受控公开资源下载路径；
- Sandbox Backend 的创建、复用、超时、销毁、文件传入和 Artifact 取回验证；
- Runtime Event、Usage、错误、审批请求和 Artifact 的筛选、归一化与持久化；
- 取消、超时、断连、重试、响应丢失和重复副作用验证；
- 最小 Agent Run API/Run Detail 集成，用于展示状态、事件、来源和 Artifact；
- Deep Agents 集成 ADR、契约测试、Spike 记录和是否进入 Phase 6 的结论。

### 不包含

- 面向用户的任意目标通用 Agent；
- 用户自定义 Prompt、Tool、MCP Server、Sandbox 镜像或网络权限；
- 登录站点、付费墙、CAPTCHA、用户凭据委托和对外写操作；
- 任意 Shell、宿主 Python、自动安装未知依赖或不受控代码执行；
- 多 Agent 编排、开放式子 Agent 树和跨 Run 长期 Memory；
- 完整 Browser 安全产品、复杂审批中心和大规模 Agent 评测；
- 替代 Phase 2/3 已有 Retrieval、Evidence、Citation 或固定 Workflow；
- 自行开发通用 Agent Loop、通用 Sandbox 平台或复制 Deep Agents 内部状态模型。

## 涉及模块

- Run Control、Attempt、Event/SSE 和 Worker Reconciliation；
- ResearchAgentRuntime Port、Deep Agents Adapter 和 Fake Runtime；
- Project Context、Paper/Evidence Reader 和权限校验；
- Model Gateway、Usage 和 Budget；
- Tool/MCP Adapter、Browser/Download Policy；
- Workspace/Sandbox Adapter；
- Resource Manifest 和 Artifact Storage；
- 最小 Agent Run API、Run Detail UI、Trace 和 Metrics；
- Deep Agents 契约测试、ADR 和阶段学习笔记。

## 核心架构边界

### 业务状态与 Runtime 状态

```text
业务 Run / Attempt / Event       PostgreSQL 事实、用户可见生命周期
ResearchAgentRuntime Session     平台到 SDK 的适配记录
Deep Agents Thread               Runtime 对话与执行标识
LangGraph Checkpoint             Deep Agents 暂停/恢复状态
Workspace / Sandbox              临时文件和受限执行环境
Tool Call / MCP Session          外部能力的一次调用或连接
Artifact                         平台校验后持久化的最终文件
```

- API 和 Worker 只通过 `ResearchAgentRuntime` 操作 Deep Agents，不在业务代码中依赖 SDK Message、Command 或 State 类型；
- ARQ Job 只携带稳定 `run_id`，不携带 Prompt、Evidence 正文、SDK Thread 或 Workspace 内容；
- Runtime 调用、MCP、Browser、模型和 Sandbox 操作不发生在数据库事务内；
- Checkpoint 成功不代表业务提交成功，SDK 返回成功也不代表 Artifact 已被平台接纳；
- 业务 Run 终态、最终 Event、Usage 摘要和 Artifact 引用在平台短事务中提交；
- Runtime 的完整思考过程、原始网页、完整论文、Secret 和大 Tool 输出不写入业务 Event。

### ResearchAgentRuntime Port 方向

具体 Python 签名在切片 1 测试前定稿，能力至少包括：

- `start(run_id, context_ref, goal, policy)`：创建或恢复一次 Runtime 执行；
- `stream(run_id)`：返回可归一化的结构化 Runtime Event；
- `resume(run_id, decision)`：从相同 Thread/Checkpoint 处理审批或恢复；
- `cancel(run_id)`：请求停止后续模型和 Tool 操作；
- `reconcile(run_id)`：查询 Runtime/Workspace 状态并与业务 Run 对账；
- `collect_outputs(run_id)`：只返回候选 Manifest、来源和待校验 Artifact 引用；
- `close(run_id)`：幂等清理临时 Runtime/Workspace 资源。

Port 不返回 Deep Agents 内部对象。Adapter 负责 SDK 类型转换、事件筛选、版本兼容和异常分类。

## 数据、API、Event 和 LangGraph 变化方向

### 数据

在实现切片前确定表名和字段，至少需要表达：

- 业务 Run 的 `run_type=research_agent` 及小型、可审计的目标和策略版本；
- `run_id` 到 runtime 名称/版本、Thread ID、Checkpoint/Deployment 标识和 Workspace ID 的一对一映射；
- Resource Manifest 及条目的规范化 URL、资源类型、来源页、内容哈希和验证状态；
- Artifact 的来源 URL、最终 URL、MIME、大小、SHA-256、Project 所有权和隔离状态；
- 聚合 Usage：模型、Token、费用（可得时）、步骤数、Tool Call 数和墙钟时间；
- 可选的最小审批记录，优先复用 Phase 3 的 `HumanInputRequest`；完整 ToolExecution 审计模型留到 Phase 6。

SDK 原始 State、完整消息历史、大型 Tool 输出和 Workspace 文件不直接复制进业务表。

### API

资源方向如下，具体 URL 和 Schema 在切片测试前确定：

```text
POST /api/v1/projects/{project_id}/agent-runs
GET  /api/v1/agent-runs/{run_id}
POST /api/v1/agent-runs/{run_id}/cancel
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/agent-runs/{run_id}/manifest
GET  /api/v1/artifacts/{artifact_id}
```

- 创建返回 `202 Accepted` 和稳定 `run_id`；
- 请求体不能提供 owner、SDK Thread、Workspace、MCP 地址或 Sandbox 配置；
- Agent Run 查询复用现有 Run/Event 所有权和不可见即 404 规则；
- Phase 5 UI 只需最小 Run Detail，不建设完整 Agent 工作台。

### Event

业务 Event 使用版本化、白名单式 Payload，候选类型包括：

```text
agent_run_created
agent_runtime_bound
agent_execution_started
agent_tool_started
agent_tool_completed
agent_approval_required
agent_artifact_staged
agent_artifact_committed
agent_runtime_disconnected
agent_run_cancelled
agent_run_failed
agent_run_succeeded
```

Tool Event 只记录工具注册名/版本、执行 ID、状态、时长、输出摘要或 Artifact ID，不记录完整参数、网页正文、模型思考或 Secret。SDK 事件不逐条原样透传。

### Deep Agents / LangGraph

- 使用 Deep Agents 自带的 LangGraph Runtime 能力，不在外层再包装一个重复的开放式 Agent Graph；
- 生产验证使用持久 Checkpointer；内存 Checkpointer 只用于单元测试；
- 业务 `run_id` 与 SDK `thread_id` 稳定映射，恢复必须使用同一映射；
- 大型 Context、下载文件和 Tool 输出放在 Evidence/Artifact/Workspace，Graph State 只保存 ID 和小型结构化状态；
- SDK 内部重试与平台重试只能有一层主导相同副作用；具体责任通过 ADR 定稿；
- Deep Agents 自带子 Agent 能力在 Phase 5 关闭，避免把单 Agent 集成验证扩张为多 Agent 系统。

## MCP、Browser 和 Sandbox 验证边界

### MCP

- 只连接固定、版本化、由平台配置的测试或项目 MCP Server；
- 只暴露白名单 Tool，加载后再次校验名称、Schema、风险等级和输出限制；
- MCP 会话默认按调用无状态；若必须使用有状态 Session，生命周期不得超过当前 Run，并在 ADR 中说明原因；
- 使用拦截器注入最小业务上下文、超时、Correlation ID 和审计信息，不把数据库凭据或平台 Secret 传给 MCP；
- MCP 不得自行决定 Project、owner、Artifact 归属或绕过审批。

### Browser/下载

- Phase 5 只访问预先允许的公开 HTTPS 目标和固定测试站点；
- URL 解析、DNS/IP 检查、Redirect、大小、MIME 和超时由平台策略控制；
- 网页内容按不可信输入处理，不允许其改变 Tool 权限、网络策略或系统指令；
- 下载先进入隔离 Workspace，经过内容哈希、类型、大小和来源校验后才进入 Artifact Storage；
- 不解压未知归档、不执行下载内容、不携带登录 Cookie。

### Sandbox

- 优先验证“Agent 在 Worker，Sandbox 作为 Tool”的分离模式，模型/API Key 留在 Sandbox 外；
- 每个业务 Run 使用独立 Workspace/Sandbox，禁止跨 owner、Project 或 Run 共享；
- Phase 5 不向模型暴露任意 `execute`；只验证受控文件操作、下载隔离和 Artifact 取回；
- Sandbox 不挂载宿主源码、数据库 Socket、Docker Socket、云凭据或 Provider Key；
- Provider 必须支持明确的创建/销毁、超时、文件传输和失败检测；网络与资源限制能力作为 ADR 选型条件；
- Sandbox 销毁失败可重试并告警，但不得阻止已原子提交的业务结果被读取。

## 关键不变量和失败行为

- Deep Agents、MCP 和 Sandbox 均不成为业务事实来源；
- Agent 只能读取创建 Run 时授权并固定的 Project Context；恢复时重新验证 owner 和 Project 可见性；
- 相同业务 Run 只能绑定一个有效 Runtime Thread 和一个当前 Workspace；
- 每次具有副作用的 Tool Call 都有稳定执行 ID；响应丢失后先对账，不能盲目重做；
- 候选 Artifact 必须先 staged，校验通过后再以内容哈希和幂等键提交；
- Runtime 成功但本地提交前崩溃时，恢复任务可以重新收集输出并完成一次提交；
- 本地提交成功但 ACK 丢失时，重复收集不得创建第二个 Artifact；
- 取消后 Runtime 不再发起新模型或 Tool 操作；已进入的外部调用允许收束，但结果不能越过取消条件提交；
- Runtime 断连、模型限流和 Sandbox 短暂不可用属于可重试候选；策略拒绝、越权、危险 URL 和不支持的文件类型属于永久失败；
- Event、日志和 Trace 不保存完整思考过程、论文全文、网页全文、完整 Prompt、Secret 或敏感 Tool 参数；
- 不宣称跨 PostgreSQL、Runtime、MCP 和 Sandbox 的 Exactly Once，通过条件更新、唯一约束、执行 ID、内容哈希和对账实现 Effectively Once。

## 实现切片顺序

1. **契约与 Fake Runtime**：确定用户故事、状态所有权、`ResearchAgentRuntime` Port、Fake Adapter 和业务 Run 映射测试；
2. **Deep Agents 最小闭环**：使用 Fake Chat Model 和确定性 Tool 验证 Thread、流式事件、Checkpoint、Interrupt/Resume 与输出转换；
3. **部署拓扑 Spike**：比较 Worker 内嵌 SDK 与独立 Runtime/Deployment，验证取消、断连、恢复、升级和运维边界；
4. **受控 MCP Tool**：接入一个固定 MCP Server，验证工具白名单、Schema、拦截器、超时、会话清理和输出限制；
5. **Sandbox 与 Artifact**：比较候选 Sandbox Backend，完成 Run 隔离 Workspace、固定 URL 下载、文件取回、平台校验和幂等 Artifact 提交；
6. **可靠性与对账**：覆盖重复 Job、Runtime 成功但响应丢失、Worker 崩溃、Sandbox 失联、取消竞争和清理重试；
7. **最小 API/UI**：创建 Agent Run，在 Run Detail 中查看筛选后的事件、来源、Manifest 和 Artifact；
8. **ADR 与阶段复盘**：记录 Deep Agents 版本、部署拓扑、MCP 模式、Sandbox Provider、被拒绝方案、运行证据和 Phase 6 进入结论。

## 测试方式

- **Domain/Application**：所有权、状态转换、取消、预算、Runtime 映射、Event 白名单、错误分类和 Artifact 幂等；
- **Runtime Contract**：同一套测试运行 Fake Runtime 和 Deep Agents Adapter，验证 start/stream/resume/cancel/reconcile/collect/close；
- **Deep Agents**：Fake Chat Model + Fake Tool 验证 Thread/Checkpoint/Interrupt，不在普通测试调用真实模型；
- **MCP**：本地确定性测试 Server 验证 Tool 发现、Schema、超时、错误、恶意大输出、会话清理和拦截器；
- **Sandbox**：Fake Backend 覆盖普通测试；真实 Provider 测试显式启用，验证 Run 隔离、文件传入/取回、超时和销毁；
- **故障注入**：Worker 崩溃、Runtime 断连、响应丢失、重复 Tool Call、Artifact 提交前后崩溃和取消竞争；
- **安全**：跨用户/Project 读取、未授权 MCP Tool、内网 URL、Redirect、超限下载、Secret/宿主路径泄漏；
- **E2E**：固定 Project/Evidence → Agent Run → Resource Manifest → 一个隔离 Artifact，全程可从 PostgreSQL Run/Event 恢复。

普通自动测试不得依赖真实模型、实时公共网站、付费 Sandbox 或外部 MCP。真实 Provider/Runtime Smoke 必须显式启用、限制预算并记录版本、命令、耗时和结果；本 Spec 不预写未执行的通过数量。

## 阶段完成条件

- Deep Agents 只通过 `ResearchAgentRuntime` Adapter 接入，Domain、公开 API 和业务表不暴露 SDK 类型；
- 一个受限资源发现用户故事端到端完成并产生可验证 Manifest 与 Artifact；
- 业务 Run、SDK Thread、Checkpoint、Workspace、Tool Call 和 Artifact 的所有权可解释且可查询；
- MCP Tool 白名单、Sandbox 隔离、下载校验和 Project 权限均有自动测试；
- 取消、超时、Runtime 断连、Worker 崩溃和 SDK 成功但本地响应丢失均有实际验证；
- SDK Event 被筛选并映射为版本化业务 Event，不保存完整思考过程和敏感内容；
- ADR 固化 Deep Agents 版本策略、部署拓扑、MCP 模式、Sandbox Provider、重试所有权和升级方法；
- 有真实但受预算限制的集成运行证据，也有完全离线的默认测试；
- 学习笔记能解释 Deep Agents Thread/Checkpoint/Backend 与业务 Run/Event/Artifact 的区别；
- 明确记录进入或不进入 Phase 6 的结论。关键安全或恢复条件未通过时不得进入 Phase 6。

## 实现前仍需确定

1. Deep Agents 运行在 ARQ Worker 内，还是通过独立 LangGraph/Agent Deployment 访问；
2. 生产 Checkpointer、Store 与现有 PostgreSQL 的数据库/Schema 隔离方式；
3. MCP 使用 HTTP 还是 stdio，以及是否存在必须使用的有状态 Session；
4. Sandbox Provider、部署位置、网络控制能力、计费和本地开发替身；
5. 第一个固定 Research Tool 与允许访问的公开测试资源；
6. Runtime Usage 和模型费用不可得时的降级记录方式；
7. Phase 5 是否需要最小审批 API，还是只验证 Runtime Interrupt 契约。

以上问题通过小实验和测试解决，并写入 ADR；不得仅根据 SDK 示例代码直接定稿。

## 已知预期限制

- Deep Agents 和相关部署/Provider API 可能快速变化，必须依靠锁文件、Adapter 和契约测试隔离升级；
- Sandbox 能隔离宿主，但不能自动阻止 Prompt Injection 或网络外泄，网络策略和 Secret 隔离仍由平台负责；
- MCP Tool 的业务权限不能仅依赖 Tool 描述或 Deep Agents 文件权限；
- Phase 5 只验证单一用户故事，不代表通用 Research Agent 已达到产品质量；
- 实时公共网站、模型和 Sandbox Provider 的结果可能不稳定，不作为默认 CI 事实；
- Core v1 即使不进入 Phase 6 仍保持完整可交付。

## 参考资料

- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)

这些资料用于确定能力边界，不替代本阶段的固定版本实验、威胁分析和测试证据。
