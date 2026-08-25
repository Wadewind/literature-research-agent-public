# Phase 5：Deep Agents 集成验证

## 状态

进行中。Spec 初版日期：2026-08-20；按 ADR-0005 重构日期：2026-08-25；切片 1
“契约与 Fake Runtime”于 2026-08-25 完成；切片 2“两轮离线业务闭环”于 2026-08-25
完成实现、主智能体审查与独立验证；切片 3“取消、恢复与对账”于 2026-08-25 完成实现、
主智能体审查与独立验证。2026-08-25 进一步对齐 Deep Agents 原生 Thread、消息管理、
上下文压缩、文件 Backend 与平台业务事实的所有权；该对齐不推翻切片 1/2 的实现。

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
  → Fake Runtime 先验证业务引用和两轮闭环；后续 Deep Agents Adapter 通过平台 Tool 按需读取授权内容
  → 持久化筛选后的 Event、Assistant Message、Evidence 引用和候选 Artifact
  → 用户发送第二条消息
  → 在同一 Session 创建新的 AgentTurnRun，并复用同一 SDK Thread
  → 只追加本轮新消息，由 Deep Agents 原生 Message/Checkpoint/压缩上下文继续分析并完成
```

切片 2 已通过独立 API、PostgreSQL、Run/Event/Outbox、Worker 与 Fake Runtime 完成两轮离线闭环，
证明 Session/Turn、消息顺序、授权快照、Runtime Binding 和 staged candidate 的业务所有权。取消、
恢复、响应丢失与崩溃对账仍按顺序属于切片 3，不能用本切片的正常成功路径代替。

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

### AgentMessage

- `sequence` 在 Session 内严格递增，用户与助手消息都显式关联产生该交互的 Turn Run。
- PostgreSQL `AgentMessage` 是产品消息事实，服务于 UI、权限、稳定消息 ID、审计和 Runtime 损坏后的
  受控重建；它不是每轮交给模型的 Prompt 缓冲区。
- Deep Agents Message、摘要、Tool Observation 和 Checkpoint 是 Runtime 工作上下文。正常后续 Turn
  复用同一 SDK Thread 并只追加本轮新用户消息，不从 PostgreSQL 重放完整产品历史。

### AgentTurnRun

- 一条用户消息对应一个业务 Run，切片 1 已新增 `run_type=agent_turn`；
- 该 Turn 覆盖从用户消息到最终 Assistant Message 的完整产品交互，内部可有多次 LLM/Tool Step、
  Observation 和 Interrupt；内部 Step 不各自创建 Turn Run；
- 拥有 Attempt、Event、Outbox、Usage、取消意图、终态和结果引用；
- 与一次 SDK Execution 1:1 对应；重试创建新 Attempt，不创建第二个业务 Turn；
- 只有 Turn Run 可以运行、取消、失败、恢复和计费，Session 本身不是长任务 Run。

Attempt、Event、Usage 和状态继续由通用 Run 聚合拥有，不在扩展记录中复制。

### ContextSnapshot 与 PolicySnapshot

每轮开始时固化授权与版本 Manifest：

- owner、project_id、session_id、turn_run_id；
- 用户消息引用和产品消息历史审计/重建水位；
- Project Index/ChunkSet 版本引用；
- 明确选择的 Review Evidence Matrix `ReviewOutput.output_id`；
- 允许读取的 Artifact 引用；
- 工具、Skill、网络、Sandbox、预算和审批策略版本。

快照保存稳定 ID、版本和小型摘要，不复制论文全文、全部 Chunk、Evidence Matrix 大对象或 SDK State。
运行时按快照引用通过受权应用 Port 读取内容，恢复不得静默扩大权限或切换到新版本上下文。
`ContextSnapshot` 不保存 Runtime Message、压缩摘要、Tool Observation 或 Graph State，也不负责正常多轮
Prompt 重建。`history_through_sequence` 只表示产品历史已提交到哪个 sequence，供审计、对账和 Runtime
binding generation 损坏后的受控重建使用；它不是“每轮加载到模型”的指令。

切片 1 已定稿 `ContextSnapshot` 的最小字段：
`snapshot_id`、`schema_version`、`owner_id`、`project_id`、`session_id`、`turn_run_id`、
`user_message_id`、`history_through_sequence`、`project_index_refs`、`review_output_id`、`artifact_refs`、
`snapshot_hash`、`created_at`。其中 `project_index_refs` 固化 Paper、PaperVersion 与 ChunkSet ID，
`artifact_refs` 固化 Artifact ID 与内容 SHA-256。`review_output_id` 是当前普通 Turn 的必填 Evidence
Matrix 绑定，不把 `review_run_id` 放进 Snapshot。切片 2 已沿
`ReviewOutput → ReviewRun → Run` 校验 owner/Project 闭包，并固定要求
`output_type=evidence_matrix`、`output_key=evidence-matrix`；不读取或复制 Matrix payload。切片 5 再接入
正式 Matrix Reader 与 Project Retriever。

`PolicySnapshot` 最小字段为 `snapshot_id`、`policy_version`、`owner_id`、`project_id`、`session_id`、
`turn_run_id`、`allowed_tool_names`、`allowed_skill_names`、`network_enabled`、`sandbox_enabled`、
`approval_required`、`max_model_calls`、`max_tool_calls`、`snapshot_hash`、`created_at`。首版默认
Tool/Skill 为空、禁网、禁 Sandbox，并要求审批；只有平台可以构造和持久化策略快照。

### 切片 1 领域字段清单

以下字段与当前冻结 dataclass 完全一致：

- `AgentSession`：`session_id`、`owner_id`、`project_id`、`title`、`status`、
  `active_turn_run_id`、`created_at`、`last_activity_at`；
- `AgentMessage`：`message_id`、`session_id`、`sequence`、`role`、`content`、`turn_run_id`、
  `idempotency_key`、`created_at`；
- `AgentTurnRun`：`turn_run_id`、`session_id`、`user_message_id`、`context_snapshot_id`、
  `policy_snapshot_id`。

`AgentMessage.idempotency_key` 是消息提交的稳定幂等事实；相同提交不能创建第二条消息或第二个 Turn。
领域工厂根据已知 `last_sequence` 生成下一序号；切片 2 已通过 Session 行 `FOR UPDATE` 分配游标与
`(session_id, sequence)` 唯一约束提供数据库并发安全，切片 1 的内存检查本身仍不是并发证据。

### Runtime Binding

切片 1 的 SDK-neutral Binding 字段为：

- `RuntimeSessionBinding`：`session_id`、稳定 `binding_id`、正整数 `generation`、opaque
  `runtime_thread_id`、opaque `runtime_workspace_id`；
- `RuntimeTurnBinding`：`session_id`、`turn_run_id`、`session_binding_id`、opaque
  `runtime_execution_id`、opaque `runtime_checkpoint_id`。

正常连续 Turn 复用同一代 Session Binding。Runtime 重置或升级允许创建新的 `binding_id` 并递增
`generation`，旧 Binding 留作审计；Turn 通过 `session_binding_id` 明确属于哪一代映射。Fake Runtime
当前固定 `generation=1`，不伪造重置/升级验证。

以上 AgentSession、AgentMessage、AgentTurnRun、Snapshot 和 Binding 是切片 1 的领域/Port 字段；切片
2 已将它们映射到下文列出的专用表、索引与约束，未增加 SDK 类型字段。

### Workspace

- 逻辑 Workspace 属于 Session，用于解释连续研究过程中的文件命名空间；
- 物理 Sandbox Lease 默认属于单个 Turn，或使用明确短 TTL，不能无限期保活；
- Sandbox 自有文件系统作为本轮物理 Workspace，不能直接挂载 API/Worker 宿主目录；
- 临时文件在 Turn 结束后丢弃；需要跨 Turn 的内部研究笔记和中间文件保存为受控
  `WorkspaceSnapshot`，下一轮重建时恢复；
- 用户可见或可下载的正式产物才经过平台校验并提交为业务 Artifact；
- SDK Store/Workspace 不能成为唯一持久化位置。

Deep Agents 默认文件空间中的对话历史卸载、大型 Tool 结果和临时研究文件属于 Runtime 工作状态，不自动
成为业务 Message、Evidence、WorkspaceSnapshot 或 Artifact。平台只持久化筛选后的产品事实；确需跨
Turn 且不能仅依赖 Runtime Checkpoint 的内部文件才进入 WorkspaceSnapshot，正式用户产物仍需 staged、
校验后提交为 Artifact。

## 与 Phase 2/3 的复用方式

Agent 不直接复用 RAG Conversation 表，也不读取 Review LangGraph 内部 State，而是复用已验证的领域能力：

| 既有能力 | Agent 中的使用方式 | 不允许的耦合 |
|---|---|---|
| Project Paper Chunk Index | 通过 Project-scoped Retriever/Reader Tool 检索 | 直接访问向量表或绕过 owner/Project 过滤 |
| Evidence 与 Citation | 返回稳定 Evidence ID，并用 Validator 校验输出 | 只靠 Prompt 约束引用 |
| Review Evidence Matrix | 通过明确的 `ReviewOutput.output_id` 读取，并校验聚合类型、稳定 key 与 owner/Project/Review Run 闭包 | 读取任意 Review、Matrix payload 或 LangGraph Checkpoint |
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
- 真实 Adapter 必须使用 `create_deep_agent` 原生 Harness 管理 Runtime Message、上下文压缩、文件卸载
  和 Checkpoint；不得退化为 `create_agent` 加平台自研等价机制；
- 同一 Session 的正常后续 Turn 只向同一 SDK Thread 追加本轮新用户消息；PostgreSQL 完整产品历史只在
  Runtime 损坏或 binding generation 迁移时作为受控重建来源；
- ARQ Job 只携带稳定 `turn_run_id`，不携带 Prompt、全文、SDK Thread 或 Workspace 内容；
- 模型、MCP、Browser、下载和 Sandbox 调用不得发生在数据库事务中；
- SDK 成功、Checkpoint 成功和 Workspace 文件存在均不等于业务 Turn 已提交成功；
- 持久业务 Event 与短暂 token stream 分离；不持久化完整思考过程；
- 不宣称 Exactly Once，通过稳定 ID、唯一约束、条件更新、内容哈希和 reconcile 实现 Effectively Once。

## ResearchAgentRuntime Port 方向

切片 1 已以行为测试固定为五个操作：

- `execute_turn(RuntimeTurnRequest) -> AsyncIterator[RuntimeEvent]`：幂等建立/启动一轮，重复输入重放同一
  逻辑 Execution 的确定性增量；
- `resume_turn(RuntimeResumeRequest) -> AsyncIterator[RuntimeEvent]`：从相同 Execution/Checkpoint 恢复；
- `cancel_turn(turn_run_id) -> RuntimeTurnReconciliation`：停止后续操作并返回 Runtime 状态；
- `reconcile_turn(turn_run_id) -> RuntimeTurnReconciliation`：查询 Runtime 状态与 opaque Binding；
- `collect_turn_result(turn_run_id) -> RuntimeTurnResult`：成功后可重复收集 Assistant Message、Evidence ID
  和候选 Artifact 描述，业务提交仍是后续独立步骤。

执行与恢复直接返回项目自有的异步增量迭代器，因此首版不增加独立 `stream_turn`。`close_turn`、
`close_session` 以及通用资源管理接口也不在切片 1 预建；后续只有出现已验证的生命周期需求时才扩展 Port。

Port 输入输出只包含项目自有 DTO、稳定 ID、小型结构化数据和错误分类。Adapter 负责 SDK 类型转换、
Thread/Execution 映射、事件筛选、版本兼容和异常归一化。

归一化状态为 `running`、`interrupted`、`succeeded`、`failed`、`cancelled`；错误最小分为
`temporary`、`permanent`、`cancelled`，并携带稳定 `code` 与安全描述。Runtime 未知 Turn、重复
`turn_run_id` 但输入不同、非法恢复属于 permanent；结果暂未就绪属于 temporary；取消后的恢复/结果
收集属于 cancelled。错误分类只指导后续业务重试策略，不替代 PostgreSQL Run 状态。

## 数据、API 和 Event 变化方向

### 数据

Phase 5 的数据库方向至少需要表达：

- Agent Session 的 owner、project_id、状态、活动 Turn 和 SDK Thread Binding；
- 有序 Message 的 role、content 摘要/引用、关联 Turn 和幂等键；
- Agent Turn Run 与 SDK Execution/Checkpoint/Deployment 的 Binding；
- ContextSnapshot、PolicySnapshot 的版本、引用和哈希；
- WorkspaceSnapshot 的 Session 所有权、版本、文件 Manifest、内容引用和哈希；
- 聚合 Usage、候选 Artifact、来源和提交状态；
- Session 级单活动 Turn 唯一约束或等价条件更新。

切片 2 已新增 `agent_sessions`、`agent_messages`、`agent_turn_runs`、
`agent_context_snapshots`、`agent_policy_snapshots`、`agent_runtime_session_bindings`、
`agent_runtime_turn_bindings` 和 `agent_artifact_candidates`。`AgentTurnRun` 是通用 `runs` 的一对一扩展；
Session 行在分配消息 sequence 时使用 `FOR UPDATE`，数据库唯一约束同时兜底
`(session_id, sequence)` 与消息幂等键。WorkspaceSnapshot、Usage 和 Approval 未提前加入。
Turn 到 User Message、ContextSnapshot、PolicySnapshot，以及 ContextSnapshot 到 User Message 的引用均由
明确命名、`use_alter` 的数据库外键闭合；应用按 Run → User Message → Snapshot → Turn 的顺序短暂
flush，避免循环引用影响插入。

发送普通用户消息在一个短事务内完成：校验 owner/Project/Matrix 与 READY ChunkSet → 锁定 Session →
分配 sequence → 保存 User Message、Run、两个 Snapshot、Turn 扩展、`run_created`、
`agent_message_accepted`、Outbox 和 Idempotency → 条件认领 active Turn。Run 输入只保存稳定 ID，不保存
完整 Prompt 或 Matrix/Chunk 正文。

Worker 先在只读事务中取得已授权业务事实，退出事务后先按稳定 `turn_run_id` reconcile；只有 Runtime
明确返回 `runtime_turn_not_found` 时才调用 execute，已有 RUNNING Execution 只进入业务重试等待，不
再次追加输入；已有 SUCCEEDED 且结果可用时直接 collect。Runtime execute/reconcile/collect/cancel 全部
位于数据库事务外。协调层在提交业务结果前验证 reconciliation 的 Turn、Session Binding、Turn Binding
闭包和 result `turn_run_id`；错配按安全 permanent Runtime 错误失败，不写入 Binding、Message 或 candidate；
Runtime 成功后再开启独立短事务，锁定并复核 Run 仍为 RUNNING，验证 owner/Project/Session/Turn 闭包，
幂等保存 Session/Turn Binding、Assistant Message 和 staged candidate，写筛选后的安全 Event，推进
SUCCEEDED 并释放 active Turn。Fake 本切片不返回授权 Evidence，因此 Evidence join 明确为空。
Session Binding 的冲突读取固定到请求的 `(session_id, generation)`，不会因较新 generation 存在而
漂移；candidate 冲突只允许同一 Turn、同一 hash 且 owner/project/session/name/MIME/content_ref/大小/
状态完全一致的事实收敛。Runtime descriptor 先经过非空、长度、小写 SHA-256 和 `0..1_000_000` 字节
领域校验，不一致或越权碰撞会回滚整个结果事务；同一 Runtime result 内重复的稳定 candidate 事实会在
提交前去重，只产生一条 staged Event。API 直接拒绝纯空白消息，Application 层也独立拒绝纯空白或超长
Idempotency-Key，避免内部 `ValueError` 泄漏为 500。

现有 Review `Artifact` 模型保持不变。切片 2 的 `agent_artifact_candidates` 只保存 Runtime 返回的小型
descriptor 和 staged 状态，不写 Storage、不提供下载，也不能成为正式 Artifact；后续提交行为必须另行
校验和设计。

### API

资源方向如下，具体路径与 Schema 在 API 切片确认：

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
- Deep Agents 文件权限和文件工具 allowlist 只约束内置文件工具，不能授权或保护自定义 Tool、MCP、
  Sandbox `execute`、owner/Project、预算或副作用；这些边界继续由平台 Snapshot、Tool Adapter、应用服务
  和审计事实执行；
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

1. **契约与 Fake Runtime（已完成）**：用失败测试固定 Session、Message、Turn、Snapshot、单活动 Turn、Port 和错误语义；
2. **两轮离线业务闭环（已完成）**：API → DB/Event/Outbox → Worker →
   Fake Runtime → Message/空 Evidence join/staged 候选 Artifact；
3. **取消、恢复与对账（已完成）**：覆盖重复 Job、Worker 崩溃、响应丢失、取消竞争
   和 Effectively Once；
4. **Deep Agents Adapter**：说明新增依赖及锁文件影响后固定版本；真实调用 `create_deep_agent`，用 Fake
   Chat Model、确定性 Tool、StateBackend 与 PostgreSQL Checkpointer 验证同一 Thread 只追加新消息、
   Execution/Checkpoint、原生上下文压缩和文件卸载；不接真实模型、Sandbox、MCP、长期 Memory 或子 Agent；
5. **Project Research Context**：正式接入 Project Retriever、Review Evidence Matrix Reader 与 Citation Validator；
6. **能力 Spike**：按 MCP → Browser/下载 → Sandbox 文件工具与 WorkspaceSnapshot → 平台 Skill 的顺序分别验证，不捆绑验收；
7. **最小 Agent Chat UI**：连续对话、活动 Turn、筛选后 Event、Evidence 与候选 Artifact；
8. **ADR 与阶段复盘**：记录版本、部署、恢复所有权、能力通过/失败证据和 Phase 6 结论。

## 测试方式

- **Domain/Application**：所有权、消息顺序、单活动 Turn、幂等、状态转换、快照不可变、取消和预算；
- **Repository/Transaction**：Session/Message/Run/Event/Outbox 原子提交、唯一约束和并发条件更新；
- **Runtime Contract**：同一行为套件覆盖 Fake Runtime 与 Deep Agents Adapter；
- **Deep Agents**：Fake Chat Model + Fake Tool；同一 Thread 连续两轮只传新增消息，并通过可控低阈值至少
  强制触发一次原生 summarization 后仍能完成第二轮；默认不调用真实模型；
- **Context**：跨用户/Project 隔离、ChunkSet/Evidence Matrix 版本固定、token 限制和 Citation 校验；
- **Workspace/Sandbox**：文件传入/取回、Snapshot 重建、跨 Turn 隔离、模型不可见 `execute`、超时和销毁；
- **故障注入**：重复 Job、Worker 崩溃、Runtime 断连、成功响应丢失、提交前后崩溃和取消竞争；
- **安全**：未授权 Project、伪造 SDK ID、未授权 Tool/Skill/MCP、内网 URL、超限输出和 Secret 泄漏；
- **E2E**：固定 Project + Index + Review Matrix → 两轮 Agent Chat → 可追溯候选 Artifact。

普通自动测试必须完全离线，不访问真实模型、实时网站、外部 MCP 或付费 Sandbox。真实 Provider/Runtime
Smoke 必须显式启用、限制预算，并记录版本、命令、耗时和结果。

切片 1 实际验证（2026-08-25）：

- `pytest` 定向领域/Application/Infrastructure 契约测试：18 passed；
- `ruff check` 定向新增代码与测试：通过；
- `pyright` 定向新增生产代码：0 errors、0 warnings、0 informations。

Fake 只使用本地哈希和内存状态，不导入 `deepagents`/LangGraph，不调用模型、网络、MCP 或 Sandbox。

切片 2 实际验证（2026-08-25）：

- Application、Repository、API、Fake Runtime、Worker/Dispatcher 分层行为测试及扩大回归通过；
- 最终切片定向扩大回归：`496 passed in 25.35s`；变更文件定向 `ruff check` 全通过，生产代码定向
  `pyright` 为 `0 errors, 0 warnings, 0 informations`；
- Testcontainers `pgvector/pgvector:pg18` 上的两轮闭环通过，覆盖两个 Run/Attempt/Event、四条严格有序
  Message、复用 Session Binding、独立 Turn Binding、快照引用、staged candidate、owner 隔离、精确
  generation 重放、candidate 收敛/碰撞拒绝与非法 descriptor 回滚；
- 临时 PostgreSQL 上实际完成 `alembic upgrade head → downgrade -1 → upgrade head → alembic check`；
- 包含最终分层/边界测试的后端全量回归：`832 passed, 4 skipped in 417.35s`；
- 普通路径完全离线、零模型/网络/MCP/Browser/Sandbox 费用。

切片 3 实际验证（2026-08-25）：

- TDD 红灯实际证明旧执行器会在 reconcile 前调用 execute，Runtime 成功响应丢失后的新 Attempt 会第二次
  调用 execute，运行中取消不会传播到 `runtime.cancel_turn`，QUEUED/FAILED 后 Session 活动指针不会
  主动释放，Runtime permanent 错误会被当作临时错误；
- 主审补强的失败测试实际得到 `8 failed, 6 passed`：状态 watcher 异常和外层任务取消会遗留 Runtime
  consumer，六类 reconciliation/result 错配未被一致地在提交前安全拒绝；修正后 Agent Application
  `14 passed`，Agent PostgreSQL 故障注入 `3 passed`；
- 对统一清理块做受控 mutation 后两条清理测试精确回到 `2 failed`（stream 未关闭、外层取消超时），恢复
  实现后 `2 passed`，证明测试不是仅依赖失败策略状态；
- Agent、RunExecution、RunReconcile、RunService 与 Fake Runtime 扩大定向分层回归
  `66 passed in 60.98s`；
- 后端非集成全量回归在沙箱外运行：`722 passed, 4 skipped in 62.16s`；受限沙箱内的 FastAPI
  `TestClient` 启动会假性卡住，已用同一单测沙箱外 `1 passed in 0.52s` 对照确认，不计作代码失败；
- 真实 PostgreSQL 注入结果事务 commit 失败后，首次 Attempt 进入 RETRY_WAIT；第二 Attempt 先 reconcile
  同一进程 Fake 的 SUCCEEDED Execution 并 collect，Runtime 逻辑 Execution 计数保持 1，Assistant
  Message、candidate 和成功 Event 各只提交一次；终态后的 ACK 重放由 Run 条件认领拒绝；
- 显式 `asyncio.Event` 控制 RUNNING 取消，证明 Worker 事务外调用 `cancel_turn`，取消后不 collect、不写
  Assistant Message/candidate；取消传播期间 Worker 崩溃由真实 Attempt lease 对账收敛 CANCELLED 并
  幂等释放 Session；
- `cancel_turn` temporary 失败的真实链路保持 Run=CANCEL_REQUESTED、Attempt=RUNNING 且停止心跳，不写
  FAILED/RETRY_WAIT/Assistant/candidate，随后由 lease Reconciler 收敛 CANCELLED；状态 watcher 异常和
  外层任务取消均通过统一 `finally` cancel+await Runtime consumer 与 watcher，异常仍传播给既有失败策略；
- reconciliation/result 的六项稳定映射错配均按 `runtime_scope_mismatch` permanent 失败，Attempt 只保存
  安全 code/描述，不保存 Runtime 原始输出；
- 后端 `ruff check src tests` 通过；全量 `pyright` 为
  `0 errors, 0 warnings, 0 informations`。普通测试继续完全离线且零费用。
- 主智能体独立复验高风险 Application/PostgreSQL/Run/Fake 组合为 `62 passed in 53.97s`，API/Worker
  装配回归为 `27 passed in 48.87s`；独立执行 `ruff check src tests` 通过，`pyright` 为
  `0 errors, 0 warnings, 0 informations`。

## 阶段完成条件

- 两轮 Project-scoped Agent Chat 可通过 Fake Runtime 完全离线运行；
- `AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1` 有契约与恢复测试；
- 正常后续 Turn 只追加新消息，Deep Agents 原生 Message/Checkpoint/压缩负责工作上下文，平台没有每轮
  重放完整产品历史或复制 Agent Harness；
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

1. Deep Agents 运行在 ARQ Worker 内还是独立 Runtime Deployment；
2. Checkpointer/Store 与业务 PostgreSQL 的数据库或 Schema 隔离方式；
3. 首个 Deep Agents Fake Tool、固定 MCP、Browser 测试目标和平台 Skill；
4. Phase 5 是否实现最小审批 API，还是只验证 Runtime Interrupt 契约；
5. staged Agent candidate 经何种校验和提交协议成为正式通用 Artifact。

## 已知预期限制

- Phase 5 只验证固定多轮用户故事，不代表通用 Research Agent 已达到产品质量；
- 首个 Fake Runtime 切片证明业务边界，不证明 Deep Agents、Browser 或 Sandbox 已安全可用；
- Deep Agents API 可能快速变化，必须依靠锁文件、Adapter 和契约测试隔离；
- Sandbox 不能自动消除 Prompt Injection 或网络外泄，平台策略与 Secret 隔离仍不可缺少；
- Session 级并发首版采用单活动 Turn，不提供分支、排队或多人协作；
- 实时网站、真实模型和 Sandbox Provider 不作为默认 CI 事实；
- 切片 2 Fake 只接收 Snapshot 引用，不读取 Chunk 或 Matrix 正文；候选只保存 descriptor，不写文件；
- Fake Runtime 仍只有进程内状态；切片 3 的 commit/响应丢失重放证明同一 Runtime 实例下的平台协议，
  不能冒充跨进程 Runtime 持久化。跨 Worker/进程 Thread 与 Checkpoint 恢复必须由切片 4 的 PostgreSQL
  Checkpointer + Deep Agents Fake Model 验证；
- 切片 3 的取消证明平台协调层停止消费 Fake 流、调用 Runtime cancel 并拒绝业务结果；尚未证明真实模型、
  Tool、Deep Agents 或远端 Provider 能立即中止已在途的外部调用；
- Demo-ready Core v1 即使不进入 Phase 6 仍保持完整可交付。

## 参考资料

- `../decisions/0001-select-deep-agents-runtime.md`
- `../decisions/0005-interactive-research-agent-session-model.md`
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Context Engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering)
- [Deep Agents Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)

外部资料只用于确定能力边界，不替代固定版本实验、威胁分析和本项目测试证据。
