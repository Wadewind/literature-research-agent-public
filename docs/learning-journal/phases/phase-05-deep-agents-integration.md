# Phase 5：Deep Agents 集成验证

## 状态

计划中，尚未开始实现。Spec 初版日期：2026-08-20；按 ADR-0005 重构日期：2026-08-25。

进入条件：Phase 4 已完成，Demo-ready Core Research Backend v1 的文献导入、RAG、固定 Review
Workflow、Run/Event、Evidence、Artifact、最低 Logs/Metrics 和评测基线均可独立运行。Phase 4 的
P4-REAL-003 仍在独立调查，不属于本阶段范围。

Deep Agents 选型已经由 ADR-0001 确定，不重新讨论是否采用。ADR-0005 进一步确定产品模型：

- Research Agent 是绑定 Project 的持续研究对话，不是一次性资源发现器，也不是通用 Coding Agent；
- `AgentSession : SDK Thread = 1:1`；
- `AgentTurnRun : SDK Execution = 1:1`；
- 每条用户消息创建一个可执行、可取消、可恢复的业务 Turn Run；
- 每轮固化不可变 `ContextSnapshot` 和 `PolicySnapshot`；
- PostgreSQL 继续拥有业务事实，SDK Thread、Checkpoint、Store 和 Workspace 只是 Runtime 内部状态。

## 目标和用户可见结果

先验证业务包装，再验证框架能力。首个端到端用户故事固定为：

```text
创建绑定 Project 的 AgentSession
  → 用户发送第一条研究消息
  → 创建 AgentTurnRun + ContextSnapshot + PolicySnapshot
  → Fake Runtime 读取授权的 Project Chunk 与 Review Evidence Matrix
  → 持久化筛选后的 Event、Assistant Message、Evidence 引用和候选 Artifact
  → 用户发送第二条消息
  → 在同一 Session / SDK Thread 语义下创建新的 AgentTurnRun
  → 基于前一轮业务历史继续分析并完成
```

首个切片不要求真实 Deep Agents、MCP、Browser 或 Sandbox。它应让用户看到一个可持续交互的
Project-scoped Agent Chat，并证明 Session、Turn、消息、上下文、取消和恢复的业务所有权正确。

后续切片再依次验证 Deep Agents Fake Model、MCP、Browser、Sandbox 和平台 Skills。框架自带这些能力
不代表平台已经完成权限、恢复、审计和安全集成。

## 范围

### 包含

- `AgentSession`、`AgentMessage`、`AgentTurnRun`、Runtime Binding、Context/Policy Snapshot 的最小契约；
- `ResearchAgentRuntime` Port 和完全确定性的 Fake Runtime；
- Project Chunk Index、Review Evidence Matrix 和既有 Artifact 的最小授权 Context Builder；
- 两轮会话、单活动 Turn、消息顺序、Run/Event/Outbox、取消、恢复和结果对账；
- Deep Agents Adapter 的最小闭环：Fake Chat Model、Thread、Checkpoint、流式事件和错误转换；
- 后续独立 Spike：固定 MCP、受控 Browser/下载、隔离 Sandbox、平台维护的 Research Skill；
- 候选 Artifact 的 staged、校验、内容哈希和幂等提交；
- 最小 Agent Chat/API 集成与完全离线的默认测试；
- 集成 ADR、Spike 证据和是否进入 Phase 6 的结论。

### 不包含

- 任意目标的通用 Agent 或 Coding Agent；
- 用户自定义系统 Prompt、Tool、Skill、MCP Server、Sandbox、网络权限或 SDK 配置；
- 任意 Shell、宿主 Python、开放网络、自动安装未知依赖；
- 登录站点、付费墙、CAPTCHA、用户凭据委托或对外写操作；
- 多 Agent、开放式子 Agent 树、跨 Project Memory 或长期个性化 Memory；
- 完整 Browser 安全产品、复杂审批中心或通用 Agent 工作台；
- 替代 Phase 2/3 的 Retrieval、Evidence、Citation 或固定 Review Workflow；
- 修复 P4-REAL-003；
- 自行实现通用 Agent Loop、Sandbox 平台或复制 Deep Agents 内部数据模型。

## 产品与业务模型

### AgentSession

- 由平台生成稳定 ID，绑定一个 owner 和一个 Project，绑定后不可换 Project；
- 保存标题、状态、创建时间、最后活动时间和当前活动 Turn；
- 拥有有序的用户/助手业务 Message；
- 与一个 SDK Thread 稳定 1:1 映射，但 SDK Thread ID 不进入公开 API；
- 同时最多存在一个活动 Turn，避免并发写入破坏对话顺序。

### AgentTurnRun

- 一条用户消息对应一个业务 Run，建议新增 `run_type=agent_turn`，精确枚举在切片 1 定稿；
- 该 Turn 覆盖从用户消息到最终 Assistant Message 的完整产品交互，内部可有多次 LLM/Tool Step、
  Observation 和 Interrupt；内部 Step 不各自创建 Turn Run；
- 拥有 Attempt、Event、Outbox、Usage、取消意图、终态和结果引用；
- 与一次 SDK Execution 1:1 对应；重试创建新 Attempt，不创建第二个业务 Turn；
- 只有 Turn Run 可以运行、取消、失败、恢复和计费，Session 本身不是长任务 Run。

### ContextSnapshot 与 PolicySnapshot

每轮开始时固化：

- owner、project_id、session_id、turn_run_id；
- 用户消息和允许读取的历史消息范围；
- Project Index/ChunkSet 版本引用；
- 明确选择的 Review Run 与 Evidence Matrix/Output 引用；
- 允许读取的 Artifact 引用；
- 工具、Skill、网络、Sandbox、预算和审批策略版本。

快照保存稳定 ID、版本和小型摘要，不复制论文全文、全部 Chunk、Evidence Matrix 大对象或 SDK State。
运行时按快照引用通过受权应用 Port 读取内容，恢复不得静默扩大权限或切换到新版本上下文。

### Workspace

- 逻辑 Workspace 属于 Session，用于解释连续研究过程中的文件命名空间；
- 物理 Sandbox Lease 默认属于单个 Turn，或使用明确短 TTL，不能无限期保活；
- Sandbox 自有文件系统作为本轮物理 Workspace，不能直接挂载 API/Worker 宿主目录；
- 临时文件在 Turn 结束后丢弃；需要跨 Turn 的内部研究笔记和中间文件保存为受控
  `WorkspaceSnapshot`，下一轮重建时恢复；
- 用户可见或可下载的正式产物才经过平台校验并提交为业务 Artifact；
- SDK Store/Workspace 不能成为唯一持久化位置。

## 与 Phase 2/3 的复用方式

Agent 不直接复用 RAG Conversation 表，也不读取 Review LangGraph 内部 State，而是复用已验证的领域能力：

| 既有能力 | Agent 中的使用方式 | 不允许的耦合 |
|---|---|---|
| Project Paper Chunk Index | 通过 Project-scoped Retriever/Reader Tool 检索 | 直接访问向量表或绕过 owner/Project 过滤 |
| Evidence 与 Citation | 返回稳定 Evidence ID，并用 Validator 校验输出 | 只靠 Prompt 约束引用 |
| Review Evidence Matrix | 通过明确的 Review Run/Output 引用读取 | 读取任意 Review 或 LangGraph Checkpoint |
| Artifact Storage | 读取显式授权 Artifact，提交候选输出 | 把 Workspace 文件直接视为业务 Artifact |
| RAG Context Builder 模式 | 复用 token、去重、排序和来源约束 | 复用 RAG Conversation 生命周期 |
| Run/Event/Outbox/Worker | 复用可靠投递、状态和重放机制 | 把 ARQ 或 SDK Event 当事实来源 |

## 核心架构边界

```text
AgentSession                 PostgreSQL 中的持续业务会话
AgentMessage                 PostgreSQL 中的用户/助手可见历史
AgentTurnRun / Attempt       PostgreSQL 中的一轮执行及尝试
Context/Policy Snapshot      PostgreSQL 中的每轮授权事实
Run Event / Outbox           PostgreSQL 中的产品历史与可靠投递
SDK Thread                   Runtime 内部会话上下文
SDK Execution/Checkpoint     Runtime 内部单轮执行与恢复状态
SDK Store                    Runtime 内部辅助状态
Logical Workspace            Session 语义下的文件命名空间
WorkspaceSnapshot            跨 Turn 持久化的内部工作文件与 Manifest
Sandbox Lease               Turn 范围或短 TTL 的物理执行环境
Artifact                     平台校验后持久化的业务文件
```

- API 和 Worker 只能通过 `ResearchAgentRuntime` Port 操作 Deep Agents；
- Domain、公开 API、业务 Event 和业务数据库类型不得暴露 Deep Agents SDK 类型；
- ARQ Job 只携带稳定 `turn_run_id`，不携带 Prompt、全文、SDK Thread 或 Workspace 内容；
- 模型、MCP、Browser、下载和 Sandbox 调用不得发生在数据库事务中；
- SDK 成功、Checkpoint 成功和 Workspace 文件存在均不等于业务 Turn 已提交成功；
- 持久业务 Event 与短暂 token stream 分离；不持久化完整思考过程；
- 不宣称 Exactly Once，通过稳定 ID、唯一约束、条件更新、内容哈希和 reconcile 实现 Effectively Once。

## ResearchAgentRuntime Port 方向

切片 1 先以行为测试固定语义，再确定精确 Python 签名。Port 至少表达：

- `execute_turn(binding, input_ref, context_snapshot_ref, policy_snapshot_ref)`：启动或恢复一轮；
- `stream_turn(turn_run_id, cursor)`：输出可归一化的结构化增量；
- `resume_turn(turn_run_id, input_or_decision)`：从相同 Execution/Checkpoint 恢复；
- `cancel_turn(turn_run_id)`：请求停止新模型与工具操作；
- `reconcile_turn(turn_run_id)`：查询 Runtime 状态并与业务事实对账；
- `collect_turn_result(turn_run_id)`：返回 Assistant Message、Evidence 引用和候选 Artifact 描述；
- `close_turn(turn_run_id)`：幂等释放本轮资源；
- `close_session(session_id)`：幂等关闭 Thread/Store 等会话资源。

Port 输入输出只包含项目自有 DTO、稳定 ID、小型结构化数据和错误分类。Adapter 负责 SDK 类型转换、
Thread/Execution 映射、事件筛选、版本兼容和异常归一化。

## 数据、API 和 Event 变化方向

### 数据

切片 1 在失败测试前定稿名称与字段，至少需要表达：

- Agent Session 的 owner、project_id、状态、活动 Turn 和 SDK Thread Binding；
- 有序 Message 的 role、content 摘要/引用、关联 Turn 和幂等键；
- Agent Turn Run 与 SDK Execution/Checkpoint/Deployment 的 Binding；
- ContextSnapshot、PolicySnapshot 的版本、引用和哈希；
- WorkspaceSnapshot 的 Session 所有权、版本、文件 Manifest、内容引用和哈希；
- 聚合 Usage、候选 Artifact、来源和提交状态；
- Session 级单活动 Turn 唯一约束或等价条件更新。

现有 Review `Artifact` 模型带有非空 `review_run_id`，不能直接假设支持 Agent Artifact；是否扩展为通用
owner，或新增 Agent 输出关联，应在对应迁移切片单独决策并测试，不能用可空字段绕过所有权。

### API

资源方向如下，具体路径与 Schema 在切片 1 确认：

```text
POST /api/v1/projects/{project_id}/agent-sessions
GET  /api/v1/agent-sessions/{session_id}
POST /api/v1/agent-sessions/{session_id}/messages
GET  /api/v1/agent-sessions/{session_id}/messages
GET  /api/v1/agent-turn-runs/{run_id}
POST /api/v1/agent-turn-runs/{run_id}/cancel
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
```

- 创建 Session 返回业务 Session；发送消息创建 Turn，返回 `202 Accepted` 和稳定 `run_id`；
- owner 来自可信身份上下文；不可见 Session/Turn 继续返回 404；
- 请求体不能提交 owner、SDK Thread、Workspace、MCP Server、Sandbox 或网络策略；
- 活动 Turn 未完成时的新消息采用明确的 `409 Conflict`，首版不做排队或并行分支；
- token 增量可走临时 stream，但刷新后的 Message、Run 和 Event 必须从 PostgreSQL 恢复。

### Event

候选业务 Event 使用版本化白名单 Payload：

```text
agent_session_created
agent_message_accepted
agent_turn_queued
agent_runtime_bound
agent_turn_started
agent_context_loaded
agent_tool_started
agent_tool_completed
agent_approval_required
agent_artifact_staged
agent_artifact_committed
agent_runtime_disconnected
agent_turn_cancelled
agent_turn_failed
agent_turn_succeeded
```

Event 只记录稳定业务 ID、版本、状态、时长和安全摘要，不逐条转存 SDK token、模型思考、网页正文、
论文全文、完整 Tool 参数或 Secret。

## Deep Agents 能力验证顺序与安全边界

### Fake Runtime 与 Deep Agents Fake Model

- 默认测试先用项目 Fake Runtime 验证业务行为，不依赖 `deepagents`；
- Adapter 契约稳定后，再固定 `deepagents` 版本并用 Fake Chat Model/确定性 Tool 验证 Thread、Checkpoint、
  Interrupt/Resume 和事件转换；
- Deep Agents 内部实现变化只能影响 Adapter 和契约测试，不能扩散到 Domain/API。

### MCP、Browser、Sandbox 与 Skills

- MCP 只连接平台配置、固定版本的测试 Server，只暴露白名单 Tool；用户不能提交 Server；
- Browser 只访问预先允许的公开 HTTPS 测试目标，平台执行 URL、DNS/IP、Redirect、大小、MIME 和超时策略；
- 下载先进入隔离 Workspace，校验来源、哈希、类型和大小后才能提交 Artifact；
- Sandbox 默认禁网，不挂载宿主源码、数据库/Docker Socket、Secret 或 Provider Key；
- `FilesystemBackend`、`LocalShellBackend` 或等价宿主执行能力不得作为生产方案；
- 平台通过受控文件传输向 Sandbox 注入授权输入，并取回 WorkspaceSnapshot 或候选 Artifact；模型不能
  指定宿主路径或绕过文件 Manifest；
- 第一版只向模型暴露受限 `ls/read_file/write_file/edit_file/glob/grep` 等文件工具，不开放
  `execute`、Shell、宿主 Python、自动装包或任意网络；若所选 Sandbox Backend 会自动暴露 `execute`，
  必须由 `ResearchAgentRuntime` 内部的受限 Backend Adapter 隐藏或拒绝；
- Skill 只能由平台维护、版本化和 allowlist 启用；Skill 是提示与能力组合，不是权限边界；
- 子 Agent 与长期 Memory 默认关闭，除非 Phase 6 通过新 ADR 明确开放。

以上能力每项都是后续独立 Spike。某项失败不应迫使业务 Session/Turn 契约返工，也不自动阻塞其他项。

## 关键不变量和失败行为

- Session 绑定 owner/Project 后不可更换，恢复时重新校验可见性；
- 同一 Session 同时最多一个活动 Turn；重复提交相同消息幂等键不得创建第二个 Turn；
- 同一 Session 只绑定一个有效 SDK Thread，同一 Turn 只绑定一个逻辑 SDK Execution；
- 重试新增 Attempt，并优先恢复/对账原 Execution，不盲目创建新副作用；
- Context/Policy Snapshot 创建后不可变，恢复不得自动读取新论文或扩大工具权限；
- Runtime 成功但本地响应丢失时，可以重新收集结果并只提交一次 Message/Artifact；
- 本地提交成功但 ACK 丢失时，重复 Job 不得创建重复 Assistant Message 或 Artifact；
- 取消后不再发起新模型/Tool 操作；在途调用可收束，但结果不能越过取消条件提交；
- Runtime 断连、模型限流和 Sandbox 短暂不可用是可重试候选；越权、策略拒绝和非法输出是永久失败；
- 候选 Artifact 必须 staged 后校验，使用稳定执行 ID、内容哈希和唯一约束去重；
- Session 关闭不等于删除业务历史；SDK 清理失败可重试且不改变已提交业务事实。
- WorkspaceSnapshot 只保存允许跨 Turn 的内部工作文件，不自动升级为用户可见 Artifact；Sandbox
  丢失后能够从 Snapshot 与已授权 Artifact 重建，不把 Provider 文件系统当唯一事实来源。

## 实现切片顺序

1. **契约与 Fake Runtime**：用失败测试固定 Session、Message、Turn、Snapshot、单活动 Turn、Port 和错误语义；
2. **两轮离线业务闭环**：API → DB/Event/Outbox → Worker → Fake Runtime → Message/Evidence/候选 Artifact；
3. **取消、恢复与对账**：覆盖重复 Job、Worker 崩溃、响应丢失、取消竞争和 Effectively Once；
4. **Deep Agents Adapter**：固定依赖版本，用 Fake Chat Model 和确定性 Tool 验证 Thread/Execution/Checkpoint；
5. **Project Research Context**：正式接入 Project Retriever、Review Evidence Matrix Reader 与 Citation Validator；
6. **能力 Spike**：按 MCP → Browser/下载 → Sandbox 文件工具与 WorkspaceSnapshot → 平台 Skill 的顺序分别验证，不捆绑验收；
7. **最小 Agent Chat UI**：连续对话、活动 Turn、筛选后 Event、Evidence 与候选 Artifact；
8. **ADR 与阶段复盘**：记录版本、部署、恢复所有权、能力通过/失败证据和 Phase 6 结论。

## 测试方式

- **Domain/Application**：所有权、消息顺序、单活动 Turn、幂等、状态转换、快照不可变、取消和预算；
- **Repository/Transaction**：Session/Message/Run/Event/Outbox 原子提交、唯一约束和并发条件更新；
- **Runtime Contract**：同一行为套件覆盖 Fake Runtime 与 Deep Agents Adapter；
- **Deep Agents**：Fake Chat Model + Fake Tool，默认不调用真实模型；
- **Context**：跨用户/Project 隔离、ChunkSet/Evidence Matrix 版本固定、token 限制和 Citation 校验；
- **Workspace/Sandbox**：文件传入/取回、Snapshot 重建、跨 Turn 隔离、模型不可见 `execute`、超时和销毁；
- **故障注入**：重复 Job、Worker 崩溃、Runtime 断连、成功响应丢失、提交前后崩溃和取消竞争；
- **安全**：未授权 Project、伪造 SDK ID、未授权 Tool/Skill/MCP、内网 URL、超限输出和 Secret 泄漏；
- **E2E**：固定 Project + Index + Review Matrix → 两轮 Agent Chat → 可追溯候选 Artifact。

普通自动测试必须完全离线，不访问真实模型、实时网站、外部 MCP 或付费 Sandbox。真实 Provider/Runtime
Smoke 必须显式启用、限制预算，并记录版本、命令、耗时和结果。

## 阶段完成条件

- 两轮 Project-scoped Agent Chat 可通过 Fake Runtime 完全离线运行；
- `AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1` 有契约与恢复测试；
- Deep Agents 只存在于 Adapter 内，Domain、API、Event 和业务表不泄漏 SDK 类型；
- 每轮 ContextSnapshot/PolicySnapshot 可审计，Agent 可受限使用 Project Index 与指定 Evidence Matrix；
- Session 单活动 Turn、消息顺序、取消、断连、Worker 崩溃和响应丢失均有实际验证；
- Runtime Event 被筛选，业务 Message/Event 不保存完整思考过程或敏感内容；
- MCP、Browser、Sandbox 和 Skill 各自有明确的通过、受限或失败结论，不以 SDK 自带能力代替验证；
- WorkspaceSnapshot 与 Artifact 的用途分离，Sandbox 丢失后可恢复允许跨 Turn 的内部工作文件；
- 任何提交的 Agent Artifact 都有来源、哈希、Project 所有权与幂等保证；
- 集成 ADR 记录 Deep Agents 版本、部署拓扑、Checkpoint/Store、重试所有权和能力 Spike 证据；
- 明确记录进入或不进入 Phase 6 的结论；关键权限或恢复条件未通过时不得进入 Phase 6。

## 实现前仍需确定

以下问题不会改变 ADR-0005 的核心映射，可在对应切片通过测试决定：

1. Session、Message、Runtime Binding、Snapshot 的精确表名、字段和保留策略；
2. `AgentTurnRun` 使用通用 Run 扩展表还是专用详情表；
3. Assistant Message 与候选 Artifact 在 Turn 终态事务中的提交边界；
4. 现有 Review 专属 Artifact 模型如何演进为不削弱所有权的通用模型；
5. Deep Agents 运行在 ARQ Worker 内还是独立 Runtime Deployment；
6. Checkpointer/Store 与业务 PostgreSQL 的数据库或 Schema 隔离方式；
7. 首个 Deep Agents Fake Tool、固定 MCP、Browser 测试目标和平台 Skill；
8. Phase 5 是否实现最小审批 API，还是只验证 Runtime Interrupt 契约。

## 已知预期限制

- Phase 5 只验证固定多轮用户故事，不代表通用 Research Agent 已达到产品质量；
- 首个 Fake Runtime 切片证明业务边界，不证明 Deep Agents、Browser 或 Sandbox 已安全可用；
- Deep Agents API 可能快速变化，必须依靠锁文件、Adapter 和契约测试隔离；
- Sandbox 不能自动消除 Prompt Injection 或网络外泄，平台策略与 Secret 隔离仍不可缺少；
- Session 级并发首版采用单活动 Turn，不提供分支、排队或多人协作；
- 实时网站、真实模型和 Sandbox Provider 不作为默认 CI 事实；
- Demo-ready Core v1 即使不进入 Phase 6 仍保持完整可交付。

## 参考资料

- `../decisions/0001-select-deep-agents-runtime.md`
- `../decisions/0005-interactive-research-agent-session-model.md`
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)

外部资料只用于确定能力边界，不替代固定版本实验、威胁分析和本项目测试证据。
