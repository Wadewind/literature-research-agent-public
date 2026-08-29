# Phase 5：Deep Agents 集成验证

## 状态

已完成（2026-08-27）。Spec 初版日期：2026-08-20；按 ADR-0005 重构日期：2026-08-25；切片 1
“契约与 Fake Runtime”于 2026-08-25 完成；切片 2“两轮离线业务闭环”于 2026-08-25
完成实现、主智能体审查与独立验证；切片 3“取消、恢复与对账”于 2026-08-25 完成实现、
主智能体审查与独立验证；切片 4“Deep Agents Adapter”已于 2026-08-25 完成实现、主智能体审查与
独立验证；切片 5“Project Research Context”已于 2026-08-26 完成实现、主智能体审查与独立验证。
2026-08-25 进一步对齐 Deep Agents 原生 Thread、消息管理、
上下文压缩、文件 Backend 与平台业务事实的所有权；该对齐不推翻切片 1/2 的实现。
切片 4 主审同时暴露了跨进程恢复所有权尚未闭合的三项耦合缺口；本 Spec 已在 Project Research
Context 之后增加一个独立的 Runtime 部署与崩溃恢复门槛，详见
[`phase-05-runtime-recovery-gap-log.md`](../reports/phase-05-runtime-recovery-gap-log.md)。
2026-08-26 已由 ADR-0006 决定 Deep Agents 在现有 ARQ Worker 内运行，使用独立 SDK-neutral
RuntimeExecution lease/fencing 控制同一 Execution 的跨进程恢复，并显式采用同步 checkpoint
durability；切片 6 已完成实现与真实 OS 进程验收。
2026-08-26 又由 ADR-0007 选择 OpenSandbox 作为 Slice 7 的远程可执行 Workspace，并决定在每个
AgentSession/SDK Thread 专属的短 TTL Lease 中向模型开放 Sandbox `execute`。
切片 7.0“Real Deep Agent Runtime Enablement”已完成实现：生产 Worker
默认继续使用 Fake，只有显式选择 `deep_agents` 才装配固定 DeepSeek 模型、持久 Checkpointer、Project
Context 与 RuntimeExecution control。切片 7.1“OpenSandbox/Lease/WorkspaceSnapshot”已于 2026-08-26
完成实现与离线/临时 PostgreSQL 验证；2026-08-28 又在本地 `opensandbox-server==0.2.2`、
Python SDK `opensandbox==0.1.15` 上完成显式 OpenSandbox 功能 Smoke。该结果验证本地 Docker Provider
回路和固定镜像运行，不验证 secure runtime、宿主/Secret 隔离或公网生产安全。
2026-08-27 由 ADR-0008 将剩余能力 Spike 调整为：先建立 MCP Configuration Foundation，再使用
Playwright MCP 连接同一 OpenSandbox Chromium 并适配一个现有 Search MCP，最后验证 Deep Agents 原生
Skills。Phase 5 不再自研 Browser Tool 或 MCP Server；公共网络与统一 egress 安全后移到 Phase 6。
切片 7.2“MCP Configuration Foundation”已于 2026-08-27 完成实现：固定
`langchain-mcp-adapters==0.3.2`，建立 SDK-neutral Catalog/Profile、逐 Turn 冻结引用、显式 MCP
ClientSession 生命周期、Schema/hash 校验与平台 interceptor。该切片完成时生产 Catalog 保持为空并 fail-closed；
真实 Playwright/Search MCP 条目、连接解析和 Sandbox Server 属于 7.3，不能由本切片的进程内 Fake MCP
测试替代。
切片 7.3“Playwright MCP 与 Search MCP Spike”已于 2026-08-27 完成实现与离线/无网络容器验证：
固定 `@playwright/mcp==0.0.79` 和 `arxiv-mcp-server==0.6.2`，将两个 Server 预装到
Session Sandbox，并把审核后的 Tool 子集接入生产 Catalog/Worker。2026-08-28 的显式 Smoke 已验证
API-key 鉴权的本地 OpenSandbox Server Proxy、同 Sandbox Chromium/Playwright MCP、arXiv MCP Schema
和 Workspace 下载；仍只形成受限通过结论，不宣称公共浏览、真实 arXiv 搜索或下载安全。
切片 7.4“Deep Agents Native Skills”已于 2026-08-27 完成实现与离线/临时 PostgreSQL 验证：首个
平台 Skill `evidence-led-synthesis` 与 owner-scoped 声明式 Skill 使用不可变版本和 SHA-256；Session
Skill Profile 只允许首 Turn 前配置并永久锁定，每轮 Policy 冻结精确引用；`/skills/` 使用只读虚拟
Backend，Deep Agents 原生 `SkillsMiddleware` 在同一 SDK Thread 复用 metadata，Sandbox `execute`
不可见且不能改写 Skill。未新增依赖，也未运行真实 Provider/OpenSandbox Smoke。
切片 8“最小 Agent Chat UI”已于 2026-08-27 完成实现与离线验收：新增 Project-scoped Session 列表和
带持久 Claim/Citation/Evidence 摘要的 Message 读模型，并用独立 React 路由提供 Session、能力配置、
两轮 Turn、通用 Run 恢复/取消、筛选后 Event、Evidence Margin 与 staged candidate 展示。默认
Playwright 旅程使用 Fake Runtime 且阻断非 localhost 请求；本切片未接入官方 Deep Agents UI、
Browser/noVNC、Workspace 文件管理、fork/rewind 或正式 Artifact 提交。
切片 8.1“Project 工作区信息架构统一”已于 2026-08-27 完成实现与离线验收：将 RAG 创建/历史迁入
canonical Project Chat，统一 Library/Chat/Review/Agent 的紧凑 Project chrome，并让 RAG Conversation
复用可调整的 viewport 三栏。
契约见 [`project-workspace-ui-contract.md`](../../spec/project-workspace-ui-contract.md)。

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
证明 Session/Turn、消息顺序、授权快照、Runtime Binding 和 staged candidate 的业务所有权。切片 3
随后完成取消、响应丢失、重复 Job 与业务崩溃对账；切片 4 使用 Deep Agents Fake Model 和确定性 Tool
验证真实 Adapter、原生上下文及成功 checkpoint 对账。能力存在于框架中不代表平台已经完成权限、
跨进程恢复、审计和安全集成；MCP、Browser、Sandbox 和平台 Skills 后续已在恢复门槛后逐项验证，结论
分别记录在 7.1–7.4。

## 范围

### 包含

- `AgentSession`、`AgentMessage`、`AgentTurnRun`、Runtime Binding、Context/Policy Snapshot 的最小契约；
- `ResearchAgentRuntime` Port 和完全确定性的 Fake Runtime；
- Project Chunk Index、Review Evidence Matrix 和既有 Artifact 的最小授权 Context Builder；
- 两轮会话、单活动 Turn、消息顺序、Run/Event/Outbox、取消、恢复和结果对账；
- Deep Agents Adapter 的最小闭环：Fake Chat Model、Thread、Checkpoint、流式事件和错误转换；
- Runtime 部署与崩溃恢复门槛：恢复所有权、持久终态、orphan `RUNNING` 识别和真实跨进程恢复；
- 后续独立 Spike：OpenSandbox/Lease/WorkspaceSnapshot、MCP 配置基础、同 Sandbox Playwright MCP 与
  现有 Search MCP、Deep Agents 原生 Skills；
- 候选 Artifact 的 staged、校验、内容哈希和幂等提交；
- 最小 Agent Chat/API 集成与完全离线的默认测试；
- 集成 ADR、Spike 证据和是否进入 Phase 6 的结论。

### 不包含

- 任意目标的通用 Agent 或 Coding Agent；
- 用户自定义系统 Prompt、Tool/MCP 代码、原始 MCP Server 连接、可执行 Skill、Sandbox、网络权限或 SDK
  配置；允许的用户配置仅限 owner/Session 范围内选择平台安装 Catalog、填写其声明的非敏感安全参数，
  以及创建只读声明式 Markdown/文本 Skill；
- 宿主 Shell、宿主 Python、开放网络、动态安装依赖；Sandbox `execute` 仅按 ADR-0007 在 Session 专属
  OpenSandbox 的固定镜像、固定依赖和默认禁网边界内开放；
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
`output_type=evidence_matrix`、`output_key=evidence-matrix`；不读取或复制 Matrix payload。切片 5 已通过
两个固定、平台自有的只读能力 `search_project_chunks` 与 `read_review_evidence_matrix` 接入正式
Retriever/Matrix Reader。模型只能提交 query 或空参数；owner、Project、Snapshot、ReviewOutput 与
ChunkSet 作用域均由稳定 `turn_run_id` 在平台侧反查。

`PolicySnapshot` 最小字段为 `snapshot_id`、`policy_version`、`owner_id`、`project_id`、`session_id`、
`turn_run_id`、`allowed_tool_names`、`allowed_skill_names`、`skill_refs`、`mcp_refs`、`network_enabled`、`sandbox_enabled`、
`approval_required`、`max_model_calls`、`max_tool_calls`、`snapshot_hash`、`created_at`。首版默认
Tool/Skill 为空、禁网、禁 Sandbox，并要求审批；只有平台可以构造和持久化策略快照。

上述默认值是当前 Fake/切片 1–6 的已实现事实。Slice 7 由服务端从固定 Capability Profile 与平台安装
Catalog 构造 `sandbox_enabled`、`network_enabled`、Tool/MCP/Skill allowlist 和预算的不可变 Snapshot；
用户只能选择当前 owner/Session 可见条目及其声明的安全参数，不能直接切换 Sandbox、网络、`execute`
或提交原始 MCP 连接。7.2 已将 `mcp_refs` 固化为 Catalog ID、精确版本、配置哈希以及 prefixed Tool
名称/输入 Schema 哈希；它不保存安全参数原文、endpoint、transport、command、env、Secret 或 SDK
对象。安全参数只保存在 owner/Session 范围的不可变 `agent_mcp_profiles` revision，Policy 以
`profile_id/profile_revision` 精确引用，并以 Session 锁、expected revision 与 `(session_id, revision)`
唯一约束防止并发覆盖。
7.4 已把 `skill_refs` 固化为 Profile ID/revision、Skill ID/source/version/name、content hash 与 required
Tool 名；Event 不保存正文。Profile 首 Turn 后永久锁定，每轮重复冻结同一 manifest；required Tool 必须
是本轮最终 Tool allowlist 的子集。

### 切片 1 领域字段清单

以下字段与当前冻结 dataclass 完全一致：

- `AgentSession`：`session_id`、`owner_id`、`project_id`、`title`、`status`、
  `active_turn_run_id`、`created_at`、`last_activity_at`；
- `AgentMessage`：`message_id`、`session_id`、`sequence`、`role`、`content`、`turn_run_id`、
  `idempotency_key`、`created_at`、可空 `claim_set_id`；`claim_set_id` 只在经过引用校验的 Agent 回答上
  指向复用的通用 `ClaimSet`，旧 Fake/未启用 Project Context 的消息保持为空；
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
- 一个 AgentSession/SDK Thread 最多拥有一个短 TTL 物理 Sandbox Lease，跨 Turn 复用但不能无限期保活，
  也不能跨 owner/Session 共享；
- Sandbox 自有文件系统作为当前 Lease 的物理 Workspace，不能直接挂载 API/Worker 宿主目录；
- 临时文件在 Turn 结束后丢弃；需要跨 Turn 的内部研究笔记和中间文件保存为受控
  `WorkspaceSnapshot`，下一轮重建时恢复；
- 用户可见或可下载的正式产物才经过平台校验并提交为业务 Artifact；
- SDK Store/Workspace 不能成为唯一持久化位置。

Lease 过期、Provider 丢失、取消后环境污染或策略要求重置时，平台递增 Sandbox generation，从最近一次
允许的 WorkspaceSnapshot 与显式授权 Artifact 重建。OpenSandbox Provider 的 ID、endpoint 和凭据只在
Adapter/基础设施层存在，不进入公开 API、Prompt 或业务 Event。

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
Sandbox Lease               Session/SDK Thread 范围短 TTL 的物理执行环境
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

切片 5 新增 SDK-neutral `ProjectResearchContext` Port，但保持 `ResearchAgentRuntime` 五方法不变。
Deep Adapter 内的两个 `ToolRuntime` 工具只把稳定 `turn_run_id` 注入 Application；模型 schema 中
`search_project_chunks` 只有有界 `query`，`read_review_evidence_matrix` 没有参数，均不能提交 owner、
Project、Snapshot、ReviewOutput 或 ChunkSet ID。Application 每次调用都重新加载并复核
Run/AgentTurn/Session/ContextSnapshot/PolicySnapshot 闭包，并在外部 Retriever 调用前、结果物化前检查
Run 仍为 RUNNING。Retriever 新增可选 `chunk_set_scope`，Agent 路径同时下推 Snapshot 固化的
`(paper_id, version_id)` 与精确 ChunkSet ID；既有 RAG/Review 不传该参数时保持原行为。

平台新增 `agent_tool_executions` 保存稳定
`effect_id = hash(turn_run_id + tool_name + canonical_args_hash)`、状态、attempt、结果 hash 和有界安全
payload；原始 query/参数不入库。唯一约束收敛重复 effect，RUNNING 重复明确拒绝，temporary 失败可由同一
effect 增加 attempt 后重试且不重复占用 `max_tool_calls`，成功重复调用直接 replay。started/succeeded/
failed Event 只包含 tool name、effect ID、状态、attempt/hash 或安全错误 code，不保存 query、Chunk、
Matrix、Prompt 或 Tool 输出。数据库 CHECK 同时约束 running/succeeded/failed 的结果与错误字段一致性。

Chunk 检索结果和 Matrix 实际返回行引用的 Review Evidence 会被幂等复制为当前 AgentTurn Run 的
Evidence；完整 Matrix 与未暴露行不会复制。Matrix Reader 固定验证 Output type/key/version/schema、
owner/Project/Review Run、聚合 rows/failures/summary，并在返回截断前验证全部 source Evidence 的
Review Run、Project、PaperVersion 与精确 Snapshot ChunkSet 闭包，再以稳定排序、12 行和 8000 字符
预算返回部分聚合。它不重新取得 Phase 3 Strategy dimensions，
因此不是第二次运行完整 `validate_evidence_matrix`。

Agent 最终回答采用有界逐行 `文本 [evidence:<id>[,<id>...]]` 契约，或唯一固定的证据不足文本。正文
Evidence ID 必须与 Runtime result DTO 精确一致；随后复用既有 `validate_citations`、`ClaimSet`、Claim、
Citation。Assistant Message 的可空 `claim_set_id`、引用事实、Runtime Binding、candidate、安全 Event 和
Run 成功状态在同一个短事务中提交；伪造、跨 Run/Project 或缺失 Evidence 会回滚全部结果事实并走
permanent 失败路径。生产 Worker 仍以 `max_tool_calls=0` 装配 Fake Runtime，本切片没有假装生产已启用
Deep Agents Project Tool。

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
- 请求体不能提交 owner、SDK Thread、Workspace、原始 MCP Server 连接、Sandbox 或网络策略；后续专用
  配置 API 只能引用当前 owner 可见的平台注册 Catalog/Skill ID，并校验其声明的安全参数 Schema；
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
agent_tool_succeeded
agent_tool_failed
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

- Deep Agents 作为完整 Harness 组装 Backend、文件、`execute`、Project Tool、由 MCP 转换的 Browser/Search
  Tool 和 Skill；平台不复制 Agent Loop，但仍负责 owner/Project、Policy、预算、恢复、审计和业务提交；
- OpenSandbox 是 Slice 7 的 Sandbox Provider。OpenSandbox Backend 作为 `CompositeBackend` 默认 Backend，
  使文件工具、Sandbox `execute` 与 Browser 下载操作同一物理 Workspace；`/conversation_history/`、
  `/large_tool_results/` 等 Runtime 内部路径路由 `StateBackend`；
- 每个 AgentSession/SDK Thread 一个短 TTL Sandbox Lease，跨 Turn 复用且不跨 owner/Session 共享；Lease
  失效或环境污染时递增 generation，并从 WorkspaceSnapshot/授权 Artifact 重建；
- Sandbox 默认禁网。Phase 5 Browser Spike 只访问 Sandbox 内合成页面，不把公共网络作为通过条件；固定
  Browser 域名、URL/DNS/Redirect/SSRF 策略和覆盖 Chromium、Python、`curl` 等全部进程的统一 egress
  留到 Phase 6；
- Sandbox 使用非 root、固定镜像和 Python/pandas/numpy/matplotlib/字体等固定依赖，不允许动态安装包；
  限制 CPU、内存、PID、磁盘、文件数、墙钟、命令和 stdout/stderr/Tool 输出；
- 不挂载宿主源码、用户目录、数据库/Valkey/Docker Socket、Secret、Provider Key、OpenSandbox Key 或 MCP
  Token；
- `FilesystemBackend`、`LocalShellBackend` 或等价宿主执行能力不得作为生产方案；
- 平台通过受控文件传输向 Sandbox 注入授权输入，并取回 WorkspaceSnapshot 或候选 Artifact；模型不能
  指定宿主路径或绕过文件 Manifest；
- Slice 7 向模型开放的 `execute` 只落在当前 Session 专属 OpenSandbox；离线命令不逐条审批，但扩大网络、
  外部副作用和正式 Artifact 提交仍由平台策略或审批控制；
- Deep Agents 文件权限和文件工具 allowlist 只约束内置文件工具，不能授权或保护自定义 Tool、MCP、
  Sandbox `execute`、owner/Project、预算或副作用；这些边界继续由平台 Snapshot、Tool Adapter、应用服务
  和审计事实执行，不能用命令字符串 allowlist 冒充强隔离；
- Browser 不再由平台自研 `browser_*` Tool。固定版本的 Playwright MCP 预装并运行在当前 OpenSandbox，
  连接同一 Sandbox Chromium/CDP；Worker 通过 OpenSandbox opaque endpoint 使用 MCP client，模型、用户和
  公开 API 都不能提交或看到 CDP/MCP/VNC/noVNC endpoint；
- Phase 5 下载只验证本地合成页面产生的文件进入同一 `/workspace`，再复用 WorkspaceSnapshot/候选
  Artifact 边界；真实来源、最终 URL、MIME、恶意文件和公共下载策略留到 Phase 6；
- MCP 通过 `langchain-mcp-adapters` 转换为 LangChain Tool 后传入 `create_deep_agent`。平台维护安装、
  审核、锁定版本的 Catalog；用户可在 owner/Session 范围选择条目并配置 Catalog 声明的非敏感安全参数，
  但不能提交 URL、transport、command、env、包版本、认证信息、镜像或网络策略；
- 需要 stdio/本地进程的第三方 MCP 必须预装并运行在 Session OpenSandbox，不在 Worker 宿主启动。远程
  Streamable HTTP MCP 由 Worker client 外连，其网络、隐私、费用与 Secret 边界必须单独记录；
- MCP Tool 名称、输入 Schema、实现版本/哈希和 allowlist 必须 fail-closed 校验；平台 interceptor 负责
  owner/Project/Session/Turn scope、权限、取消、Runtime fence、预算、超时、输出限制和安全审计；
- Phase 5 不自研 `search_arxiv_metadata` Server，而选择一个现有、固定版本的只读 Search MCP 作为适配
  样本；具体实现和运行位置须在新增依赖或镜像内容前单独确认；
- Skill 使用 Deep Agents 原生 `skills` 加载。用户可启用平台安装、固定版本的 Skill，也可创建
  owner-scoped 的声明式 Markdown/文本 Skill；`/skills/` 路由平台管理的只读 Backend，不进入可由
  Sandbox `execute` 改写的 `/workspace`，并按 owner/Session 固化版本/内容哈希；
- Skill 是提示与能力组合，不是权限边界；首版不接受 Skill 可执行脚本、二进制、动态依赖、任意路径或
  独立 Secret 配置，也不能扩大 Tool、MCP、网络、Sandbox、预算和 Project 权限。API 不提供 Secret 字段
  或注入机制，但本阶段不扫描 description/instructions 中误贴的 Secret；用户仍不得提交 Secret，文本扫描
  与内容审核留到 Phase 6；
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

## 实现切片顺序

1. **契约与 Fake Runtime（已完成）**：用失败测试固定 Session、Message、Turn、Snapshot、单活动 Turn、Port 和错误语义；
2. **两轮离线业务闭环（已完成）**：API → DB/Event/Outbox → Worker →
   Fake Runtime → Message/空 Evidence join/staged 候选 Artifact；
3. **取消、恢复与对账（已完成）**：覆盖重复 Job、Worker 崩溃、响应丢失、取消竞争
   和 Effectively Once；
4. **Deep Agents Adapter（已完成）**：说明新增依赖及锁文件影响后固定版本；真实调用 `create_deep_agent`，用 Fake
   Chat Model、确定性 Tool、StateBackend 与 PostgreSQL Checkpointer 验证同一 Thread 只追加新消息、
   Execution/Checkpoint、原生上下文压缩和文件卸载；不接真实模型、Sandbox、MCP、长期 Memory 或子 Agent；
5. **Project Research Context（已完成）**：正式接入 Project Retriever、Review Evidence Matrix
   Reader、稳定 ToolExecution effect 与 Citation Validator；
6. **Runtime 部署与崩溃恢复门槛（已完成）**：按 ADR-0006 在 ARQ Worker 内运行 Deep Agents，使用
   独立 Runtime Execution lease/fencing 明确 recovery owner；持久化可对账的失败/取消终态，
   识别并受控认领 orphan `RUNNING` checkpoint，沿同一 Execution/Checkpoint 恢复且不重新追加用户输入；
   用第二个真实 OS 进程验证恢复后不重复已持久确认的模型或 Tool 调用。该切片不顺便建设通用分布式调度器，
   也不把 Tool 已产生外部副作用但 checkpoint 尚未提交的窗口伪称为已解决；
7. **能力 Spike**：只有切片 6 门槛通过后才开始，且不捆绑验收：
   - **7.0 Real Deep Agent Runtime Enablement（已完成）**：接入固定 Provider/
     `BaseChatModel` factory、Secret/费用边界和 Worker `fake | deep_agents` 显式配置；默认保持 Fake；
     真实模式复用既有持久 Checkpointer、Project Context 与 RuntimeExecution control；
   - **7.1 OpenSandbox/Lease/WorkspaceSnapshot（已完成实现）**：验证 Session 级短 TTL Lease、可执行默认
     Backend、隔离文件与命令、物理 Sandbox 生命周期、Snapshot 取回和逻辑 Workspace 重建；真实
     OpenSandbox Smoke 仍须显式 opt-in；
   - **7.2 MCP Configuration Foundation（已完成实现）**：验证平台注册 Catalog/Profile、owner/Session 隔离、版本与
     配置快照、client 生命周期、Tool namespace、Schema/hash、interceptor、预算/取消/输出限制和 Fake
     MCP；实现前先报告 `langchain-mcp-adapters` 精确版本、传递依赖与锁文件影响；
   - **7.3 Playwright MCP 与 Search MCP Spike（已完成实现，受限通过）**：固定版本 Playwright MCP 在同一 OpenSandbox 中连接
     Chromium/CDP，操作本地合成页面并把下载写入 `/workspace`；再适配一个现有、固定版本的只读 Search
     MCP；不自研 MCP Server，不把公共网络与统一 egress 作为本切片通过条件；
   - **7.4 Native Skills（已完成实现）**：验证平台安装 Skill 与 owner-scoped 声明式 Skill 的只读 Backend、
     不可变版本/哈希、首 Turn 后 Session manifest 锁定、Sandbox 不可改写和权限不扩张；
8. **最小 Agent Chat UI（已完成）**：连续对话、活动 Turn、筛选后 Event、Evidence 与候选 Artifact；前后端接口、
   桌面三栏、Evidence Margin、首 Turn 前能力配置和非范围以
   [`agent-chat-ui-interface-contract.md`](../../spec/agent-chat-ui-interface-contract.md) 为准；不直接接入
   官方 Deep Agents UI，移动 Drawer 不作为本切片验收条件；
8.1. **Project 工作区信息架构统一（已完成）**：增加 canonical Project Chat 首页/详情，将 RAG 创建和历史
   从 Library 迁入 Chat，统一四个 Project 区域的紧凑 Header/Mode Nav，并让 RAG Conversation 复用
   viewport 三栏与版本化 resize 规则；不修改后端 Conversation/Retrieval 契约；
9. **ADR 与阶段复盘**：记录版本、部署、恢复所有权、能力通过/失败证据和 Phase 6 结论。

## 测试方式

- **Domain/Application**：所有权、消息顺序、单活动 Turn、幂等、状态转换、快照不可变、取消和预算；
- **Repository/Transaction**：Session/Message/Run/Event/Outbox 原子提交、唯一约束和并发条件更新；
- **Runtime Contract**：同一行为套件覆盖 Fake Runtime 与 Deep Agents Adapter；
- **Deep Agents**：Fake Chat Model + Fake Tool；同一 Thread 连续两轮只传新增消息，并通过可控低阈值至少
  强制触发一次原生 summarization 后仍能完成第二轮；默认不调用真实模型；
- **Context**：跨用户/Project 隔离、ChunkSet/Evidence Matrix 版本固定、token 限制和 Citation 校验；
- **Workspace/Sandbox**：文件传入/取回、Snapshot 重建、Session 跨 Turn 复用、跨 owner/Session 隔离、
  模型可见 `execute` 只能到当前 OpenSandbox、宿主/Secret 不可见、默认禁网、资源/输出上限、超时、
  销毁和 orphan 清理；统一 egress 属于 Phase 6；
- **MCP/Skill**：Catalog/Profile 与声明参数 Schema、owner/Session 隔离、client/Server 生命周期、Tool
  namespace/Schema/hash、取消/预算/输出限制、Playwright MCP 同 Sandbox CDP 与 Workspace、Skill
  版本/哈希/只读加载和权限不扩张；
- **故障注入**：重复 Job、Worker 崩溃、Runtime 断连、成功响应丢失、提交前后崩溃和取消竞争；切片 6
  必须启动第二个真实 OS 进程，而不是只用新连接或同进程新 Adapter 模拟重启；
- **安全**：未授权 Project、伪造 SDK ID、跨 owner/Session Catalog 或 Skill、未授权 Tool/Skill/MCP、
  原始 MCP URL/command/env、超限输出和 Secret 泄漏；公共 URL/SSRF 专项留到 Phase 6；
- **E2E**：固定 Project + Index + Review Matrix → 两轮 Agent Chat → 可追溯候选 Artifact。

普通自动测试必须完全离线，不访问真实模型、实时网站、外部 MCP 或付费 Sandbox。真实 Provider/Runtime
Smoke 必须显式启用、限制预算，并记录版本、命令、耗时和结果。

切片 8 实际验证（2026-08-27）：

- 后端 Session 列表先验证 owner/Project 再执行单次稳定倒序查询；Message 读模型只装配与当前
  Session 的 `turn_run_id/claim_set_id` 一致、且 `Evidence.project_id/run_id` 同时属于当前
  Project/Turn 的持久引用；Claim/Evidence Repository 是必需装配，不能静默降级为“无引用”；
- API/Application/Executor/PostgreSQL Repository 定向扩大回归 `30 passed`，Agent 两轮与可靠性集成
  回归 `4 passed`，后端非集成全量回归 `952 passed, 5 skipped`；生产代码全量 `pyright` 为
  `0 errors`，`ruff check src` 与本切片测试通过；
- 主审补强后的 Citation/Session/API/PostgreSQL 定向回归 `15 passed`，两轮集成 `1 passed`；同
  Project 跨 Turn 损坏引用测试在修复前精确返回多余 Evidence，增加 run 闭包后转绿；
- 前端 Vitest 全量 `128 passed`，`tsc -b && vite build` 通过；缺失 Agent 意图/展示模块时的首轮
  Vitest 得到 2 个模块解析失败，最小实现后转绿；
- Project/Session identity 变化通过 React key 创建新的交互状态边界，旧消息意图、Run、Matrix、
  Evidence 和能力草稿不会沿用；Session 响应不属于路由 Project 时停止子查询和渲染。能力双写使用
  `allSettled` 等待后统一失效 Profile，部分成功后按服务端 revision 重算 dirty，同时保留失败草稿；
- 主审修正后 Playwright Fake Runtime 旅程 `1 passed (36.2s)`，覆盖创建 Session、首轮前 Skill 配置、选择真实
  `review_output_id` 对应 Matrix、第一轮、刷新恢复、第二轮、Project Index 与 staged candidate；旅程
  阻断非 localhost 请求并断言无 page error；
- 主智能体最终独立验证中，API/Application/引用闭包/PostgreSQL Repository/两轮集成合并定向回归为
  `31 passed in 80.51s`，定向 Ruff、全量 Pyright、前端 `128 passed` 与 production build 均通过；
  Phase 4 取消场景复测为 `1 passed (10.7s)`。有头 `playwright-cli` 在 1440×1000 下确认三栏实际为
  `220px / 586px / 350px`，工作区 `scrollWidth == clientWidth == 1192`，且能力面板没有暴露 MCP endpoint、
  transport、env 或 Secret；
- Matrix/工作区补强后，Review 列表以 canonical aggregate output 是否存在判断可用性，不依赖父
  Review 的最终状态；同 owner/Project 的最新版本用一次批量查询装配，摘要只读取 Matrix payload。
  当前 Project ready index 由单条 Project-scoped ChunkSet 查询返回，Turn 建立后则显示冻结快照；
- 有头真实数据验收发现，同一 Review 下不同类型 Output 可共享 version，批量查询若在外层只按
  `run_id + version` 连接会把 Section/per-paper Matrix 冒充为聚合 Matrix。修复后外层再次限定 canonical
  type/key；同 Run、同 version 的三类 Output PostgreSQL 回归先红后绿，相关 Repository/Application/API
  定向为 `11 passed`；
- 桌面 Agent route 使用 viewport 工作区，Session rail、消息区与 Evidence Margin 独立滚动；左右
  分隔条支持指针、方向键、双击复位并用版本化 localStorage 保存。能力配置改为浮层，保存后主动关闭，
  避免覆盖 composer。补强定向后端为 `28 passed`、前端全量为 `131 passed`、build/ruff/pyright 通过，
  Phase 5 离线 E2E 为 `1 passed (36.8s)`；
- 有头验收显示初版 composer 占用过高后，Matrix 选择压缩为横向 context row，消息标签保持可访问但
  视觉隐藏，textarea 默认高度降为 80px；前端 build、`131 passed` 与 Phase 5 E2E
  `1 passed (36.4s)` 复测通过；
- Fake 的固定结果为证据不足且执行过快，因此浏览器旅程没有稳定制造运行中取消或非空 Agent
  Citation；取消按钮/终态收束由通用 Run 单元测试覆盖，持久 Claim/Citation/Evidence 投影由后端
  cited-runtime 测试覆盖。有头检查发现的唯一 Console error 是既有 `/favicon.ico` 404，不影响业务
  请求或本切片 E2E，未在 Slice 8 中顺便扩大为站点资产修复。

切片 8.1 实际验证（2026-08-27）：

- 新增 `/projects/:projectId/chat` 与 `/chat/:conversationId` canonical 路由，旧
  `/conversations/:conversationId` 继续复用同一页面；Library 不再读取或创建 Conversation，只把整个
  Project、单篇或多篇选择带入 Chat 首页；URL `paper_id` 只有命中当前 Project Paper API 时才生效；
- Library 以资源底座展示三个平级研究模式；Library、Chat、Reviews 和 Agent 复用紧凑 Project Header/
  Mode Nav。RAG 与 Agent 复用 resize helper/separator，但使用独立 versioned localStorage key，业务事实
  不进入 Storage；
- RAG Conversation 使用 viewport 三栏，rail、消息时间线与 Evidence Margin 独立滚动，Composer 固定在
  中栏底部；Conversation 与路由 Project 不匹配时停止 Message/Evidence 查询并统一显示 404 语义；
- TDD 首轮两个 Vitest suite 因 canonical route/preselection 与 shared layout 模块不存在而失败；实现后
  前端全量 `18 files / 137 tests`，`npm run build` 通过；
- Phase 2 离线 E2E 经 canonical Chat 完成 Project/单篇 scope、刷新、Citation → Evidence → PDF 与归档
  只读，`1 passed (16.3s)`；Phase 5 Agent 配置/两轮/刷新/candidate 回归
  `1 passed (36.2s)`。两者均使用 Fake Adapter、隔离 PostgreSQL/Valkey，未访问真实模型、网站、MCP 或
  Sandbox。

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

切片 4 实现与主审证据（2026-08-25）：

- 依赖已单独固定为 `deepagents==0.7.8`；真实 Adapter 只在 infrastructure 内调用
  `create_deep_agent`，未修改五方法 Port、业务表、公开 API 或 Worker 生产装配；
- `AgentSession` 确定性映射一个 SDK Thread/StateBackend Workspace，Turn 确定性映射 Execution；最终
  `RuntimeTurnBinding.runtime_checkpoint_id` 来自真实 Checkpointer 最终 checkpoint，而不是预测 ID；
- Adapter 通过 Checkpoint metadata 的稳定 `turn_run_id`、request hash 与 session ID 反查执行；真实
  PostgreSQL 上关闭连接并创建新 Adapter 后可 `reconcile/collect/replay` 两轮成功结果，新 Adapter 的
  Fake Chat Model 与 Tool 均未再次调用。该测试模拟重启并消除 Adapter 内存依赖，但没有启动第二 OS
  进程，也不覆盖 Tool 执行后、checkpoint 提交前的崩溃窗口；
- 第二 Turn 只传本轮 `HumanMessage`。低阈值强制触发 Deep Agents 原生
  `SummarizationMiddleware`，StateBackend 中出现 `/conversation_history/*.md`，checkpoint 保存
  `_summarization_event`；最终 raw Graph State 中 `message-turn-1/message-turn-2` 各恰好一次，而第二轮
  模型有效上下文只看到摘要与保留尾部，证明没有重新提交第一轮 HumanMessage；
- 使用精确 `provider:model` 的公开 `HarnessProfile` 关闭当前模型的默认 general-purpose subagent，且不
  污染同 Provider 其他模型；Harness 不全局排除 `execute`，Adapter 根据 Backend 是否支持执行决定是否
  在精确 Profile 中保留并注册它，再由策略中间件取 Adapter 注册集合与本轮 Policy allowlist 的交集，
  并在模型 schema 与 Tool 实际执行边界强制执行；StateBackend 或空策略时 `execute` 均不可见，模型
  伪造隐藏 Tool 名称也不会执行；
- Runtime 只输出 `bound/started/assistant_delta/completed` 白名单，不转存 Tool 原始输出、完整 Prompt、
  Graph State 或思考过程；`STARTED` 前已形成真实 checkpoint，随后取消不再发起 Fake Model/Tool 调用；
- 首轮红灯为缺少真实 Adapter 的 `ModuleNotFoundError`；主审补强的权限、Profile 作用域和
  Checkpoint 异常归一化测试先得到 `9 failed, 6 passed`，修复后定向 Adapter 单测 `16 passed`，真实
  PostgreSQL 新连接/新 Adapter 恢复 `1 passed`；最终纵深拒绝同名自定义 `execute` 后，Adapter 单测为
  `17 passed`、扩大相关回归 `44 passed`、完整非集成测试 `739 passed, 4
  skipped`，完整 Ruff 通过，完整 Pyright 为 `0 errors, 0 warnings, 0 informations`。受控工具沙箱内曾
  出现 selector 假性等待，同一完全离线测试在沙箱外正常给出失败/通过结果；这不是产品 Sandbox 验证
  结论。
- 主智能体独立复验 Adapter 与真实 PostgreSQL 组合为 `18 passed in 4.59s`，既有
  Port/Executor/Fake/两轮/崩溃恢复组合为 `28 passed in 61.85s`，完整非集成回归为
  `739 passed, 4 skipped in 58.95s`；独立 Ruff 与 Pyright 同样通过。

切片 5 实现与验证（2026-08-26）：

- 首组 TDD 红灯为 Project Context 模块、ToolExecution 领域对象和 ORM 均不存在，定向 pytest
  `3 errors in 0.27s`；补强 parser 边界时得到 `4 failed, 8 passed`，canonical JSON 边界补强得到
  `6 failed, 2 passed`；
- Domain/Application/Schema/Retriever 非数据库契约最终为 `42 passed in 0.26s`，覆盖回答总字符、
  Claim/行/ID/引用数量、空行策略，NaN/Infinity/非对象/超大 Tool 参数，安全结果复制，Matrix
  rows/failures/summary 与长度边界、Tool 终态不可重写、Tool name/error code 数据库长度契约、单行唯一
  末尾 Evidence marker，及精确 ChunkSet scope 向两路 Retriever 下推；
- Deep Adapter 完全离线套件在非沙箱环境为 `21 passed in 1.18s`，覆盖 Search/Matrix 两个
  ToolRuntime schema 与 turn_run_id 注入、未授权拒绝、Project Context 安全错误转换和正文 Evidence
  提取；受控命令沙箱内连未修改 HEAD Adapter 也会在 `STARTED` 后、模型调用前受 seccomp 假性等待，
  因而使用已批准的非沙箱离线命令验证，这不是产品 Sandbox 结论；
- Project Context、精确 ChunkSet、引用提交和 AgentMessage Repository PostgreSQL 最终定向套件为
  `12 passed in 39.22s`，覆盖重复/并发/temporary
  effect、预算、调用前和检索在途取消、完整 Matrix 部分物化/replay、跨 owner Output 篡改、引用原子
  提交与跨 Run Evidence 回滚；受影响扩大 PostgreSQL 回归首次为 `29 passed, 1 failed, 1 error`，其中
  failure 是一个旧测试装配遗漏两个新 Repository factory，error 是 Testcontainers 容器被外部移除；
  修正装配后两项单独复跑 `2 passed in 8.28s`；
- 最终主审发现 Matrix 第 13 行可绕过 source Evidence 闭包；新增跨 Run Evidence 红灯为
  `1 failed in 3.67s`，修复为先校验全部 source Evidence、再只物化选中行后，单测
  `1 passed in 4.87s`，完整 Project Context PostgreSQL 回归 `8 passed in 26.43s`；
- 新迁移的 `upgrade → downgrade -1 → upgrade → alembic check` 为 `2 passed in 4.94s`，`alembic heads`
  输出单 head `e7b4c2a9d6f1`；完整 `ruff check src tests` 通过，`pyright src` 为
  `0 errors, 0 warnings, 0 informations`。
- 主智能体独立复验非数据库 Domain/Application/Schema/Retriever 契约为 `42 passed in 0.25s`，Deep
  Adapter 完全离线套件为 `21 passed in 1.12s`；Project Context、精确 ChunkSet、引用提交和
  AgentMessage Repository 的聚焦 PostgreSQL 套件为 `12 passed in 40.16s`；
- 主智能体扩展受影响 PostgreSQL 回归得到 `31 passed, 1 error in 224.78s`；唯一 error 发生于
  Testcontainers 启动阶段，Docker 报待探测容器已被外部移除（`No such container`），没有应用断言失败，
  对应取消用例单独复跑为 `1 passed in 3.67s`；
- 主智能体直接调用虚拟环境 `pytest` 执行迁移测试时，首轮因子进程 `PATH` 未包含虚拟环境而得到
  `1 failed, 1 passed in 3.41s`（`FileNotFoundError: alembic`）；显式设置同一虚拟环境 `PATH` 后为
  `2 passed in 5.01s`，`alembic heads` 再次确认单 head `e7b4c2a9d6f1`；独立 `ruff check` 通过，
  `pyright src` 为 `0 errors, 0 warnings, 0 informations`；完整非集成后端回归为
  `779 passed, 4 skipped in 72.79s`。

切片 6 实现与验证（2026-08-26）：

- ADR-0006 固化 ARQ Worker 内运行、当前 PostgreSQL/checkpoint schema、独立 RuntimeExecution、同步
  durability 和严格版本兼容；生产默认 Fake 不变，未新增依赖、服务、队列或外部 Provider；
- 新增 `agent_runtime_executions`、Domain/Application/Repository Port 与 PostgreSQL Repository；Turn 与
  Execution 为 1:1，Attempt 与 Execution 语义分离，lease 绑定当前最新 RUNNING Attempt，fence 单调递增；
- claim 使用 Run/Execution 行锁与唯一约束，renew/checkpoint/终态以 owner+Attempt+fence CAS；所有
  Runtime/模型/Tool/checkpoint I/O 均在短事务外；过期 owner 和非取消业务状态不能改写 Runtime；
- Deep Adapter 的模型/Tool middleware 在真实调用边界复核 permit，所有 `astream` 显式
  `durability="sync"`；已有 checkpoint 使用 `resume_turn(response=None)` / `astream(None, ...)`，没有
  checkpoint 时才追加首次 HumanMessage；
- Runtime/Graph/Deep Agents 0.7.8/LangGraph 1.2.11 revision 完全匹配才恢复；不匹配安全拒绝为
  `runtime_version_incompatible`；FAILED/CANCELLED/SUCCEEDED 均可跨 Adapter 对账；
- 首轮 Domain/Application 红灯为 2 个 collection error；durability/resume 红灯为 2 failed；取消授权和
  过期 owner 补强分别为 1 failed 与 2 failed；稳定 Session/Execution 身份补强首轮 3 failed；同步
  Checkpoint 已写入但平台水位未推进的恢复窗口首轮 1 failed；活动性预检后 Attempt 失效的竞争测试首轮
  1 failed；非空旧水位 C1/物理最新 C2 与 checkpoint Session 身份篡改测试各首轮 1 failed，均已修复；
- RuntimeExecution Domain/Application 最终 12 passed；Deep Adapter 完全离线最终回归 30 passed in
  1.26s；水位与身份两项定向最终 2 passed in 0.80s；
  PostgreSQL 并发恢复者测试 1 passed；
- 真实 OS 进程测试 1 passed in 8.14s：第一个 spawn 进程在 Tool Step 已同步 checkpoint、下一模型调用
  在途时被 terminate，第二进程以 Attempt 2/fence 2 完成；已确认模型/Tool 各一次，未确认模型调用明确
  重试一次，业务 Assistant Message 只提交一次；
- Alembic 使用项目标准 `uv run pytest` 完成 head → downgrade -1 → head → check，最终复跑 3 passed in
  5.16s；直接虚拟环境 pytest 的首次运行仅因子进程 PATH 找不到 `alembic` 失败；新增 ORM 列断言首轮因
  测试误把 Column 对象与字符串比较得到 1 failed/2 passed，修正测试后通过；
- 切片相关 Domain/Application/Fake/Deep Adapter 回归 60 passed in 54.56s；Runtime/真实进程/Checkpoint/
  Agent PostgreSQL 回归 7 passed in 28.46s；Project Context 扩大回归 8 passed in 27.25s；
  水位补强后真实跨进程恢复定向复跑 1 passed in 8.41s；
- 主智能体独立复验 Domain/Application/Deep Adapter 42 passed in 1.27s，Runtime 控制与真实跨
  进程恢复 2 passed in 11.70s，迁移往返与 ORM 契约 3 passed in 5.02s，完整非集成后端回归
  800 passed, 4 skipped in 76.94s；
- `ruff check src tests`、`git diff --check` 通过，`pyright src` 为 0 errors；`alembic heads` 为单 head
  `a4c8e1f2b7d9`。一次受限沙箱内 `uv run alembic heads` 因 uv 不能写用户 cache 返回 read-only error，
  随后使用同一虚拟环境 `./.venv/bin/alembic heads` 成功；
- 模块细节见
  [`agent-runtime-execution-recovery.md`](../modules/agent-runtime-execution-recovery.md)。

切片 7.0 实现证据（2026-08-26）：

- 精确新增 `langchain-deepseek==1.1.0`；锁文件新增 `langchain-openai==1.6.0` 与 `openai==3.3.1`，
  既有依赖没有版本升级。`ChatDeepSeek` 固定使用 `deepseek-v4-flash`、
  `extra_body={"thinking":{"type":"disabled"}}`、Provider 输出 token 上限及通用 timeout/retry；
- `AGENT_RESEARCH_RUNTIME_BACKEND` 默认 `fake`。Fake 模式不读取 Agent Provider Key，也不构造模型或打开
  Checkpointer；显式 `deep_agents` 模式缺少专用 Key、模型漂移、输出上限非法或 backend 未知时启动前
  fail-closed，Secret 字段 `repr=False`；缺 Key 校验位于 Worker composition，使启动脚本可在 Worker fork
  后移除专用 Key，API、迁移和基础设施进程不持有 Agent Provider Secret；
- Worker 真实模式在一个资源生命周期内持有 `ChatDeepSeek` 和现有单 `AsyncConnection`/
  `AsyncPostgresSaver`，并装配生产 `ProjectResearchContextService`、`RuntimeExecutionControlService` 与
  `DeepAgentsResearchAgentRuntime`；关闭时释放 Checkpointer connection 与模型 HTTP client，异步模型
  client 关闭失败也不会跳过同步 client 清理；
- `PolicySnapshot.max_model_calls` 精确定义为当前 Turn 的**主 Agent Loop 模型调用预算**。中间件在主模型
  node 前把预留计数写入 LangGraph checkpoint，额度耗尽在 Provider 前永久失败；Tool node 失败后恢复的
  离线测试证明不会重新获得额度。Checkpoint 仅保留当前 Turn ID 与计数，新 Turn 覆盖旧值，避免长期
  Session 状态按 Turn 增长；因此 graph revision 升为 `deep-agent-graph.v2`，旧 v1 RuntimeExecution 与
  Checkpoint 均 fail-closed。Provider 已在途但 checkpoint/响应不确定的窗口不宣称 Exactly Once；
- Deep Agents `SummarizationMiddleware` 内部通过 `_summary_model.with_retry()` 发起的压缩请求不经过上述
  主循环预算，当前最多可能额外尝试 3 次 Provider，因此 7.0 尚不是覆盖所有 Provider 请求的费用硬上限；
  本切片不禁用原生压缩，真实 Smoke 约束为单 Turn 且不得触发 summarization；
- 本地 `langgraph-checkpoint-postgres==3.1.1` 源码确认 singleton saver 有实例级异步锁，可保证协程正确性，
  但所有 checkpoint I/O 串行且存在单连接故障面；pool + per-execution Saver/graph factory 留到 7.1；
- 首组 TDD 红灯：模型 factory 模块不存在导致 `ModuleNotFoundError`，Worker helper 不存在导致
  `ImportError`；预算恢复测试也先暴露 Provider 在途失败会恢复同一 graph task 的窗口，随后改用已确认
  checkpoint 后 Tool 失败的场景精确验证逻辑预算；
- 最终核心合并定向测试为 `64 passed in 2.40s`，覆盖配置、Provider factory/清理、锁定
  `ChatDeepSeek` 的离线真实构造、Worker 装配/生命周期、
  Deep Adapter 权限/恢复/预算；普通测试完全离线，未使用真实 Key、网络或付费 Provider；
- 加入 `dev.sh`/浏览器 E2E Fake 与 Secret 隔离静态契约后的合并测试为 `67 passed in 2.48s`；
- 受影响 Application/Runtime control/Fake/Deep Adapter/Worker 回归为 `83 passed in 63.13s`；本地
  Testcontainers PostgreSQL Checkpoint、RuntimeExecution 与真实双 OS 进程恢复为
  `3 passed in 16.57s`；最终完整非集成回归为 `822 passed, 4 skipped in 75.44s`；
- `uv lock --check` 成功解析 228 个包，完整 `ruff check src tests` 通过，`pyright src` 为
  `0 errors, 0 warnings, 0 informations`；
- 主审补强 graph v1 拒绝、跨 Turn 常数空间预算和关闭失败清理测试；首轮因测试把第二 Turn 的一次模型
  调用误写为两次、并把 `AsyncMock` 异常挂在 client 而非 `close` 方法，实际为
  `2 failed, 48 passed in 2.10s`；修正测试装配后最终受影响定向回归为 `50 passed in 1.97s`；
- 主智能体独立验证：配置/factory/Deep Adapter/Runtime control/Worker/dev/e2e 定向回归
  `75 passed in 2.63s`；PostgreSQL Checkpoint、RuntimeExecution control 与真实双 OS 进程恢复
  `3 passed in 15.90s`；完整非集成回归 `824 passed, 4 skipped in 74.95s`；
- 主智能体独立执行 `ruff check src tests` 通过，`pyright src` 为
  `0 errors, 0 warnings`，`uv lock --check` 输出 `Resolved 228 packages`，
  `bash -n scripts/dev.sh web/e2e/run.sh` 与 `git diff --check` 均通过；
- 代码与边界详见
  [`real-deep-agent-runtime-enablement.md`](../modules/real-deep-agent-runtime-enablement.md)。

切片 7.1 实现证据（2026-08-26）：

- 服务端固定 `agent-policy.project-research-workspace.v1`：Sandbox 开启、网络和审批关闭，主模型预算 8、
  统一 Tool 预算 12；允许 Project Tool、文件工具与 `execute`，Browser/MCP/Skill/子 Agent 关闭；
- 精确新增 `opensandbox==0.1.15`，解析只新增该包且未升级其他包；把已锁定的
  `psycopg-pool==3.3.1` 提升为 direct dependency。上游没有官方 OpenSandbox/Deep Agents Adapter，
  本项目通过 infrastructure 内薄 `BaseSandbox` Adapter 隔离 SDK；
- 每个 Session 最多一个 active Lease，滑动 TTL 10 分钟、generation 最长 60 分钟、1 CPU/2 GiB、命令
  60 秒、inline 输出 64 KiB、网络 default-deny；generation/fencing CAS 阻止旧执行推进 Snapshot，失败或
  取消标记 DIRTY，下一 Turn 轮换并恢复最近 stable Snapshot；Lease 记录当前 holder Turn，同一 Turn 重试
  保留 fence，新 Turn 才递增；已知 Execution 在 acquire 前离线对账，pre-event duplicate 不污染 winner；
- 跨 Turn 复用 active 物理 Sandbox 以业务发布的 `STABLE` 为门槛：上一 holder 的 Snapshot 必须通过
  owner/Project/Session/Turn scope 校验，并且就是 Session 当前 latest `STABLE`；缺失、仅 `STAGED` 或非
  latest 时轮换 generation，只恢复 latest `STABLE`。同 Turn checkpoint/finalization retry 仍可复用当前
  Lease，业务取消、引用校验失败和事务回滚的物理文件不能进入下一 Turn；
- generation 轮换遵循 create/restore → Lease fencing CAS → best-effort destroy old：CAS loser 只销毁自己的
  候选 Sandbox，不能误销毁被并发续租的旧实例；winner 的旧实例回收失败不撤销新 Lease，由 Provider TTL
  兜底；
- `WorkspaceSnapshot` 仅接受 `/workspace` 普通文件，限制 128 文件、单文件 10 MiB、总计 50 MiB，并
  校验规范路径、类型、排序、size、内容 SHA-256 与 Manifest hash；元数据在 PostgreSQL，内容寻址 blob
  复用 Storage；正常目录会跳过，嵌套恢复先创建父目录，同步 Provider 文件 I/O 从事件循环卸载；下载前
  校验声明上限、下载后校验完整响应集合；重复完成/唯一约束竞态返回相同 Turn 的既有 Snapshot；
- 真实 Deep adapter 在形成 Runtime 成功终态前调用内部 Workspace finalizer，只取回文件、写入内容寻址
  blob 并登记不可见的 `STAGED` Snapshot；随后才执行 `RuntimeExecution.succeed` 并发出 `COMPLETED`。
  `STAGED` 不等于可恢复的稳定版本；只有 AgentTurnExecutor 写入 assistant/evidence/candidate/event、将业务
  Run CAS 为 `SUCCEEDED` 并释放活动 Turn 的同一短事务，才会在校验 scope 与 RuntimeExecution 成功后将其
  发布为 `STABLE`。Fake Runtime 不要求 Snapshot，Deep 模式缺少 `STAGED` 则整个业务成功事务回滚；
- 临时 Snapshot 捕获失败保留同 Turn Lease 并沿已成功 checkpoint 重试，永久 Manifest 失败 fail-closed/
  置 DIRTY；Runtime success 响应丢失时离线重放后由业务事务发布既有 `STAGED`，不重建 Sandbox，也不重复
  模型/Tool。取消、引用校验或业务 CAS 失败留下的 `STAGED` 对 latest/restore 不可见；
- Worker 使用 `AsyncConnectionPool(min_size=1,max_size=4)`，每次 Runtime operation 创建独立
  `AsyncPostgresSaver` 与 graph。可执行 Backend 自动包装为
  `CompositeBackend(default=OpenSandboxBackend, routes={internal: StateBackend})`；正常文件工具与
  `execute` 落到同一 Sandbox，conversation history/large results 不进入 `/workspace` Manifest；
- Deep Agents `after_model` middleware 在 Tool node 前一次性预留全部 Tool calls。Project Tool、内置文件
  Tool 和 `execute` 共用 `max_tool_calls`，Project effect 计数不回灌全局额度；因此 graph revision 升为
  `deep-agent-graph.v3`；测试证明额度 2 可各执行一次 Project/execute，额度 1 时 execute 副作用为零；
- 真实 `create_deep_agent` + 共享内存 checkpoint 测试证明 Sandbox 关闭后，使用新 Saver 和离线
  `StateBackend` graph 仍可 collect/reconcile，且不 acquire Sandbox；
- 定向 Lease/Workspace/OpenSandbox/Worker/checkpoint 离线回归为 `51 passed in 1.83s`；Deep Adapter
  统一预算扩大回归为 `35 passed in 1.90s`；临时 PostgreSQL Repository 与迁移往返为
  `5 passed in 8.44s`；`ruff check` 通过，`pyright` 为 0 errors；
- 主审并发与文件完整性加固后的定向回归为 `24 passed in 1.35s`，完整非集成回归为
  `1001 passed, 4 skipped in 707.51s`，临时 PostgreSQL Repository/迁移复验为
  `5 passed in 10.75s`；本切片范围 Ruff 通过，修改文件 Pyright 为 0 errors；
- Snapshot finalizer 顺序与失败恢复定向回归为 `61 passed in 2.86s`；加固后的完整非集成回归为
  `1007 passed, 4 skipped in 556.65s`，RuntimeExecution control/Workspace Repository PostgreSQL 子集为
  `2 passed in 7.17s`；
- 两阶段 Snapshot 发布边界加固后的相关定向回归为 `101 passed in 56.90s`，PostgreSQL migration/
  repository 为 `5 passed in 8.47s`，完整非集成回归为
  `1009 passed, 4 skipped in 557.41s`；
- 跨 Turn `STABLE` 复用门槛与 Lease 续租/轮换 CAS 回收顺序的最终定向回归为
  `19 passed in 1.02s`，最终完整非集成回归为 `1013 passed, 4 skipped in 596.88s`；
- 固定镜像 recipe 使用已核实的 `opensandbox/chrome` index digest，构建时预装固定 Python 数据分析依赖；
  OpenSandbox server 保持外部显式前提，不加入普通 compose，Worker 不获得 Docker Socket；
- 切片 7.1 完成时没有运行真实 OpenSandbox Smoke；2026-08-28 后续本地功能 Smoke 已验证镜像创建、
  文件/执行与销毁，但远端默认禁网、CPU/内存、宿主/Secret 不可见和丢失恢复仍不是实测安全结论；详见
  [`agent-sandbox-workspace.md`](../modules/agent-sandbox-workspace.md)。

切片 7.2 实现证据（2026-08-27）：

- 精确新增 `langchain-mcp-adapters==0.3.2`；锁文件只新增 `mcp==1.29.1`、
  `httpx-sse==0.4.3`、`sse-starlette==3.4.8` 三个传递包，既有 Deep Agents、LangChain Core 与
  LangGraph 版本未升级；
- 平台静态 `McpCatalog` 只公开 ID、版本、展示名、安全参数声明、Tool 名与 Schema hash；公开 API 只接受
  Catalog 选择、精确版本和声明参数，Pydantic 与 Domain 双层拒绝额外连接字段以及 URL/endpoint/
  transport/command/env/Secret 类参数；
- `agent_mcp_profiles` 以同一 `profile_id` 的不可变 revision、`(session_id, revision)` 唯一约束、owner
  双重过滤和 expected revision 隔离配置；每个新 Turn 在原有 Session 短事务内解析当前 Profile，将
  profile/revision、Catalog/版本/config hash/prefixed Tool/schema hash 写入不可变
  `PolicySnapshot.mcp_refs`，Event 只记录启用条目数量；同一 revision 内 Catalog ID 唯一，不能用两个
  版本制造相同 prefixed Tool 名，且 prefixed name 在 Catalog 构造时就受 ToolExecution 100 字符上限约束；
- infrastructure 使用 `MultiServerMCPClient(..., tool_name_prefix=True)`，但不走逐次临时 session 的
  `get_tools()`；每次 Runtime execute/resume 显式打开 `ClientSession`，验证 `list_tools()` 的完整名称与
  Schema hash 后加载 Tool，并在 graph 结束或异常时先关闭 MCP session、再关闭 Sandbox。离线
  reconcile/collect/cancel 不连接 MCP；
- interceptor 在调用前复核冻结 allowlist、Turn scope、Runtime fence、业务取消和统一 Tool 预算，调用
  期间施加 30 秒超时与既有 8,000 字符安全结果上限；复用 `ToolExecution` 的稳定 effect/唯一约束/
  条件更新完成成功 replay、并发拒绝和安全错误记录。外部 MCP 调用位于 begin/succeed 两个短事务之间，
  Event 不保存参数、结果、endpoint、Secret 或原始 `tool_call_id`。MCP `effect_id` 使用
  `turn_run_id + tool_call_id` 的 opaque hash，`args_hash` 包含调用 ID 与 canonical 参数；同 ID 改 Tool/
  参数永久拒绝，不同 ID 的相同参数各执行一次并各占预算。旧 Project Tool 的 args-based effect 不变；
- Loader 在 resolver/session/list/load 前检查取消，外部 SDK/连接/close 普通异常收敛为无底层 cause 的
  安全 temporary Runtime 错误；interceptor 在 succeed/fail 前重验 fence，handler 后丢 lease 的旧 owner
  不写终态。仍不宣称 Exactly Once；
- 主审加固后，Domain + 真实 `langchain-mcp-adapters` + 进程内 FastMCP 的完全离线测试为
  `35 passed in 1.23s`，通过真实 LangGraph `ToolNode` 验证 invocation ID 注入、同 ID replay、同参数不同
  ID 两次调用、缺 ID/加载前取消零连接、五个 SDK 生命周期边界错误脱敏和 handler 后 fence 丢失零失败
  终态写入；Profile/快照/
  ToolExecution PostgreSQL 测试为 `4 passed in 13.84s`，单项 MCP API 测试为 `1 passed in 0.49s`，Sandbox/MCP
  生命周期测试最终为 `12 passed in 1.15s`，包括 MCP 关闭失败时仍尝试释放 Sandbox；迁移在临时 PostgreSQL 完成
  `head → downgrade -1 → head → check`，该文件最终 `5 passed in 4.79s`；旧 Project Context PostgreSQL
  回归 `8 passed in 26.43s`，确认其 effect 语义不变；全部主审测试后的 Domain/Adapter/Sandbox/
  RuntimeExecution/单项 API 合并回归为 `93 passed in 2.78s`；定向 Ruff 通过，定向 Pyright 为 0 errors；
- 不可变 Profile revision 加固后，Application 与迁移组合为 `8 passed in 15.82s`：rev1 Turn 创建后把
  Profile 更新为 rev2，重新从数据库读取的 Turn 仍精确引用 rev1；旧 revision 查询必须同时匹配 owner、
  Session、profile ID 和 revision，错 owner/Session 均不可读；
- 生产 `PLATFORM_MCP_CATALOG` 当前故意为空，连接解析器会以 `runtime_mcp_catalog_unavailable`
  fail-closed；因此本切片只证明配置、隔离、冻结、Adapter 与调用边界，不证明真实第三方 MCP、远程网络、
  Playwright/CDP 或 Search 能力。Graph 创建前还没有本轮 graph permit，重复 Job 可能重复只读 MCP 连接与
  capability discovery；业务取消会在这些边界前拒绝，Tool effect 由 invocation 账本去重，但 client 创建
  不宣称 Exactly Once。详见
  [`agent-mcp-configuration.md`](../modules/agent-mcp-configuration.md)。

切片 7.3 实现证据（2026-08-27）：

- 派生镜像构建阶段固定 `node:24.19.0-trixie-slim` image index digest 与
  `@playwright/mcp==0.0.79`；独立 npm lock 锁定其 `playwright`/`playwright-core`
  `1.63.0-alpha-2026-08-05`。独立 Python hash lock 固定无 extras 的
  `arxiv-mcp-server==0.6.2` 及传递依赖，镜像内 `npm ci` 和 `pip --require-hashes` 均实际通过；
  根 `pyproject.toml`/`uv.lock` 未变化；
- 两个 MCP 由镜像内固定 recipe 在当前 Sandbox 惰性、幂等启动，固定端口分别为 8931/8932；由于
  OpenSandbox 只为已监听端口返回 endpoint，Resolver 先以仅 loopback allowlist bootstrap，再解析
  endpoint，并在脚本锁内一次性收敛到 exact authority；不使用 wildcard；
  Playwright 连接 `127.0.0.1:9222` 的同一 Chromium，下载目录固定为
  `/workspace/downloads`。Worker 从当前 Lease generation 的 OpenSandbox endpoint 解析连接，不缓存
  endpoint/client；endpoint/header 不进入 Domain、数据库、Event、Prompt 或公开 API；
- 真实固定 Server 分别发现 24/14 个 Tool。Catalog 只投影审核的 17 个 Playwright 非代码 Tool 和
  arXiv 的 `search_papers`、`get_abstract`；Loader 遍历完整分页并逐项校验允许子集的 Schema/hash，
  缺失或漂移 fail-closed，未登记 Tool 不转换为 LangChain Tool。`browser_evaluate`、
  `browser_run_code_unsafe`、文件上传和单请求 body 读取 `browser_network_request` 等未进入模型能力；
- Playwright MCP 默认 Host 防护对未登记 Host 返回 403。recipe 不使用 wildcard，而把当前 opaque
  endpoint 的精确 authority 固定到进程；相同 authority 重试幂等，变化时 fail-closed，要求重新解析/
  轮换而不是默默复用旧 allowlist。Provider/recipe 异常统一脱敏为安全 Runtime 错误；
- `--network none` 的真实派生容器完成 MCP 启动幂等、完整 discovery/schema、合成页面 navigate/click
  和 `/workspace/downloads/paper.txt` 下载；其中 4 路并发 bootstrap/configure 最终收敛到单一进程，
  exact authority 变化按预期 fail-closed；未调用公网 arXiv。最终 Domain/Adapter/Sandbox/Worker
  定向离线回归为 `109 passed, 1 skipped in 2.29s`。2026-08-28 又以
  `AGENT_RUN_OPENSANDBOX_MCP_TESTS=1` 实际运行真实 OpenSandbox 回路，结果为
  `1 passed in 12.13s`；相关 Adapter/MCP/Runtime/Worker 回归为 `52 passed in 2.18s`。该 Smoke 不访问
  公网 arXiv、不调用模型，且与 secure runtime/公共网络安全验证分开陈述。详见
  [`agent-mcp-browser-search.md`](../modules/agent-mcp-browser-search.md)。

切片 7.4 实现证据（2026-08-27）：

- Domain/API 只接受 name、description、Markdown/text instructions 与 required Tool 名，由平台生成
  `SKILL.md`；拒绝 owner、path、frontmatter、脚本、二进制、动态依赖、MCP/网络/Sandbox 配置与调用方
  伪造的 content hash，且不提供独立 Secret 字段/注入机制。普通文本中的 Secret 扫描未实现，用户仍不得
  提交，治理留到 Phase 6。平台 Catalog 固定 `evidence-led-synthesis` v1；owner 编辑创建新 version，
  不覆盖旧内容；A→B→A 回退会追加 v3=A，hash 相同不合并历史版本；
- PostgreSQL 增加 owner Skill identity/version、Session Skill Profile 和 Policy `skill_refs`。并发同名
  创建由唯一约束收敛；版本 CAS 锁稳定 identity；Profile 更新与首条 Message 都锁同一 AgentSession
  行，因此首 Turn 后永久锁定无检查后写入竞态。Policy 每轮固化 profile/revision/id/source/version/name/
  content hash/required tools；Profile hash 对 selection 规范排序，持久化仍保留提交顺序用于审计；Event
  只保存 `skill_count`；
- Policy version 不统一覆盖既有契约：无扩展能力保持 `project-research-workspace.v1`，MCP-only 保持
  `project-research-workspace-mcp.v1`，仅在 `skill_refs` 非空时使用 `project-research-capabilities.v1`；
- Runtime 在业务事务外按冻结引用复核旧版本和 hash，物化为 `/skills/` 只读虚拟 Backend，并把稳定 source
  直接交给 `create_deep_agent(skills=...)`。现有 Composite routes 与 Sandbox default 不被覆盖；
  write/edit/upload/delete 明确拒绝，物理 Sandbox `execute` 看不到 `/skills/`；
- 真实 `create_deep_agent` + Fake Chat Model + MemorySaver 两轮离线测试分别重建 Runtime/graph、只共享
  checkpointer/thread 与后端，证明 `skills_metadata` 只下载一次且内容可读；Materializer 还验证 exact
  owner/version/hash/name/required-tools 漂移 fail-closed；Sandbox wrapper 缺少 materializer 时
  fail-closed 并关闭连接。
  普通测试未访问真实模型、网站、外部 MCP 或 OpenSandbox；根 `pyproject.toml`/`uv.lock` 未变化；
- 本切片把 graph revision 提升为 `deep-agent-graph.v5`，防止用新 Skill State/中间件语义恢复旧 graph。
  最终 Domain/Application/API/Fake/Deep Adapter/Sandbox/Worker 定向回归为 `123 passed in 19.84s`；
  PostgreSQL Application/Repository、两轮可靠性与迁移回归为 `13 passed in 28.52s`，owner composite FK 加固后
  单独复跑迁移为 `6 passed in 5.05s`。详细边界见
  [`agent-native-skills.md`](../modules/agent-native-skills.md)。

## 阶段完成条件

- 两轮 Project-scoped Agent Chat 可通过 Fake Runtime 完全离线运行；
- `AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1` 有契约与恢复测试；
- 正常后续 Turn 只追加新消息，Deep Agents 原生 Message/Checkpoint/压缩负责工作上下文，平台没有每轮
  重放完整产品历史或复制 Agent Harness；
- Deep Agents 只存在于 Adapter 内，Domain、API、Event 和业务表不泄漏 SDK 类型；
- 每轮 ContextSnapshot/PolicySnapshot 可审计，Agent 可受限使用 Project Index 与指定 Evidence Matrix；
- Session 单活动 Turn、消息顺序、取消、断连、Worker 崩溃和响应丢失均有实际验证；
- Runtime 部署拓扑与 Execution 恢复所有权已明确；第二个 OS 进程可识别并受控认领 orphan
  `RUNNING` checkpoint，沿同一 Execution/Checkpoint 恢复，不重新追加用户输入或重复已提交调用；
- Runtime 失败/取消终态可由新进程安全对账，不依赖旧 Adapter 的进程内协作状态；
- Runtime Event 被筛选，业务 Message/Event 不保存完整思考过程或敏感内容；
- MCP 配置、Playwright/Search MCP、Sandbox 和原生 Skill 各自有明确的通过、受限或失败结论，不以 SDK
  自带能力或第三方 Server 存在代替平台隔离与恢复验证；
- WorkspaceSnapshot 与 Artifact 的用途分离，Sandbox 丢失后可恢复允许跨 Turn 的内部工作文件；
- 任何提交的 Agent Artifact 都有来源、哈希、Project 所有权与幂等保证；
- 集成 ADR 记录 Deep Agents 版本、部署拓扑、Checkpoint/Store、重试所有权和能力 Spike 证据；
- 明确记录进入或不进入 Phase 6 的结论；关键权限或恢复条件未通过时不得进入 Phase 6。

## 实现前仍需确定

以下问题不会改变 ADR-0005 的核心映射，可在对应切片通过测试决定：

1. Phase 5 是否实现最小审批 API，还是只验证 Runtime Interrupt 契约；离线 Sandbox `execute` 已决定不
   逐命令审批。

原第 2 项已于 2026-08-28 由 ADR-0010 决定：Phase 5 的 staged descriptor 保持当前受限契约；Phase 6
通过显式 `submit_artifact`、独立 AgentArtifact、内容寻址 staging 和业务成功条件提交完成正式下载，
不把 WorkspaceSnapshot 或 ReviewRun Artifact 直接复用为用户文件。ADR-0009 同时决定 Browser/noVNC
首版采用两个 Turn 之间的人工控制，不回填 Phase 5 Slice 8，也不要求首版 LangGraph Interrupt。

OpenSandbox Python SDK 已在 7.1 固定为 `opensandbox==0.1.15`；固定 base image digest、10 分钟 TTL、
60 分钟 generation、1 CPU/2 GiB、60 秒命令与 64 KiB inline 输出已经实现。derived image 的发布 digest
和生产部署 digest 仍需在部署验证时记录；本地开发镜像的功能 Smoke 不替代发布制品签名或生产验证。

切片 6 的部署、数据库和恢复决策已由 ADR-0006 固化：ARQ Worker 内运行、当前 PostgreSQL/checkpoint
schema、独立 RuntimeExecution lease/fencing、同步 durability、相同 Runtime/Graph revision 才自动恢复。

## 已知预期限制

- Phase 5 只验证固定多轮用户故事，不代表通用 Research Agent 已达到产品质量；
- 首个 Fake Runtime 切片证明业务边界，不证明 Deep Agents、Browser 或 Sandbox 已安全可用；
- Deep Agents API 可能快速变化，必须依靠锁文件、Adapter 和契约测试隔离；
- Sandbox 不能自动消除 Prompt Injection 或网络外泄，平台策略与 Secret 隔离仍不可缺少；
- Session 级并发首版采用单活动 Turn，不提供分支、排队或多人协作；
- 实时网站、真实模型和 Sandbox Provider 不作为默认 CI 事实；
- 7.1 已用离线 Adapter/真实 PostgreSQL 验证 Lease、fence、默认禁网配置和 WorkspaceSnapshot 重建契约，
  并用本地 OpenSandbox Docker 回路验证创建、执行、文件与销毁；Server 启动日志明确显示未配置
  secure runtime，因此不得宣称强隔离、资源强制或宿主/Secret 不可见已实测。
  Playwright MCP/CDP 与本地下载属于 7.3；公共浏览、统一 egress、来源/下载安全属于 Phase 6；
- 切片 2 Fake 只接收 Snapshot 引用，不读取 Chunk 或 Matrix 正文；候选只保存 descriptor，不写文件；
- Worker 生产默认仍使用 Fake Runtime，但切片 7.0 已增加显式 `deep_agents` 模式并装配真实 Provider、
  持久 Checkpointer、Project Context 与 RuntimeExecution control；本切片只证明无 Tool、单 Turn 的真实
  Runtime enablement，未执行真实 Provider Smoke，也未证明完整 Project Tool 回路；
- 成功/失败/取消/orphan RUNNING 已可借 RuntimeExecution 和 PostgreSQL Checkpoint 跨 OS 进程对账；
  该结论要求相同 Runtime/Graph/SDK revision，不扩张为跨版本迁移或公网生产 SLA；
- 切片 5 已用稳定 effect ID、唯一约束、条件状态更新和持久调用记录证明成功 Project Tool replay、
  RUNNING 并发拒绝、temporary 同 effect 重试与平台 `max_tool_calls` 预算；但 Tool 外部效果完成后、
  ToolExecution 成功记录提交前的崩溃窗口仍不宣称 Exactly Once，orphan RUNNING 当前 fail-safe 拒绝
  自动重放，需按未来具体外部 Tool 设计幂等、查询或补偿；
- 切片 7.1 已对 Project、文件和 `execute` Tool 强制 checkpoint 持久的统一逐 Turn `max_tool_calls`，但
  `max_model_calls` 仍不覆盖 `SummarizationMiddleware` 最多 3 次内部 Provider 尝试，也不消除已在途
  Provider/Tool 请求的不确定窗口；
- 切片 7.3 已让生产 Catalog/Resolver 支持固定 Playwright/arXiv MCP，并在 default-deny Docker
  容器及真实本地 OpenSandbox Server Proxy 中运行 Playwright/CDP、本地页面、Schema 校验和 Workspace
  下载；没有运行公共网络、真实 arXiv 搜索或付费服务。成功外部调用与
  ToolExecution 成功提交之间仍有在途不确定窗口，重复时对 orphan RUNNING fail-safe 拒绝而非盲目重放；
  graph 构造前的只读 session/discovery 尚无 graph RuntimeExecution permit，跨进程重复 Job 可能重复该
  discovery，但每个边界前会检查业务取消，且不会绕过 Tool invocation effect 去重；
- 切片 7.4 已验证平台/owner 声明式 Skill 的版本、Session Profile、冻结 Policy、原生 metadata 缓存与
  虚拟只读文件边界；它不支持附件、脚本、二进制、动态依赖、Profile 热切换、fork/rewind、内容审核 UI
  或真实 Provider/OpenSandbox Smoke。owner instructions 仍是不可信提示，权限子集校验不等于已消除
  Prompt Injection；
- Browser 画面、人工控制、Agent Attachment、Real Runtime Candidate 收集和可下载 AgentArtifact 均按
  ADR-0009/0010 进入 Phase 6；当前 UI 的 staged Candidate 仍只有元数据，不能据此宣称文件交付已完成；
- Phase 6 后续范围先由 ADR-0011 收敛，后由 ADR-0012 仅取代固定 arXiv 网络范围：Slice 7 改为版本化
  public-egress/private-network 边界和正式资源校验；该边界不解析 HTTP/Browser 业务语义，不能宣称 raw
  公网请求协议级只读。硬预算与 Sandbox 最小治理继续是必须项，通用
  Approval、用户自定义网络策略、OAuth/Credential、完整 Registry 和生产级 Sandbox 平台不作为交付条件；
- Worker 已改为 1..4 连接的 checkpoint pool，并为每个 Runtime operation 创建独立 Saver/graph；这解决
  singleton Saver 的全局实例锁串行，不代表数据库容量、性能或故障切换已完成生产评测；
- Matrix Reader 验证可由既有持久事实重建的 Output/聚合/Paper/Evidence/ChunkSet 闭包，并只返回部分有界
  聚合；它没有读取 Phase 3 Strategy dimensions 后重跑完整 Matrix validator；
- 切片 3 的取消证明平台协调层停止消费 Fake 流、调用 Runtime cancel 并拒绝业务结果；尚未证明真实模型、
  Tool、Deep Agents 或远端 Provider 能立即中止已在途的外部调用；
- Demo-ready Core v1 即使不进入 Phase 6 仍保持完整可交付。

## 参考资料

- `../decisions/0001-select-deep-agents-runtime.md`
- `../decisions/0005-interactive-research-agent-session-model.md`
- `../decisions/0006-run-deep-agents-inside-arq-worker.md`
- `../decisions/0007-use-opensandbox-executable-workspace.md`
- `../decisions/0008-use-native-mcp-and-skills-capabilities.md`
- `../decisions/0009-use-turn-boundary-browser-control.md`
- `../decisions/0010-use-explicit-agent-file-exchange.md`
- `../decisions/0011-adopt-phase-06-lean-delivery.md`
- `../decisions/0008-use-native-mcp-and-skills-capabilities.md`
- [`Phase 5 Runtime 恢复缺口台账`](../reports/phase-05-runtime-recovery-gap-log.md)
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Context Engineering](https://docs.langchain.com/oss/python/deepagents/context-engineering)
- [Deep Agents Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)

外部资料只用于确定能力边界，不替代固定版本实验、威胁分析和本项目测试证据。
