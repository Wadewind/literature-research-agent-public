# Phase 6：Deep Agents 驱动的 Research Agent 与安全强化

## 状态

计划中，尚未开始实现。Spec 初版日期：2026-08-20；按 ADR-0005 对齐日期：2026-08-25；按 ADR-0007
调整日期：2026-08-26；按 ADR-0008 调整日期：2026-08-27。

ADR-0007 已把 OpenSandbox Provider、Session 级短 TTL Lease、固定依赖的 Sandbox `execute` 与
WorkspaceSnapshot 提前到 Phase 5 Slice 7；ADR-0008 又把 MCP Catalog/Profile 基础、同 Sandbox
Playwright MCP、现有 Search MCP 与 Deep Agents 原生 Skills 的最小验证提前到 Phase 5。本阶段不重复
这些 Spike，而是在其实际证据基础上完成完整 Registry、审批、公共网络/下载安全、UI 和运维强化。
其中 Slice 7.1 已完成 OpenSandbox/Lease/WorkspaceSnapshot 的实现与离线/临时 PostgreSQL 验证；真实
OpenSandbox proxy/Provider Smoke 仍未完成；Phase 5 的 7.2 MCP 配置、7.3 固定 Playwright/arXiv MCP
镜像内回路和 7.4 Native Skills 已完成受限离线验证，但不替代本阶段的公共网络、下载、Prompt
Injection 与完整治理验证。

进入条件：Phase 5 已完成并通过 ADR 确认 Deep Agents 的版本策略、部署拓扑、`ResearchAgentRuntime` 契约、MCP 模式、Sandbox Provider、重试所有权和升级方法；Phase 5 的安全、取消、断连和重复副作用验证没有未解决的阻塞项。

## 目标和用户可见结果

把 Phase 5 的多轮集成 Spike 扩展为可用、受限、可观察的 Research Workspace Agent。用户可以在一个
Project 内持续对话，让 Agent 使用授权的 Paper Chunk Index、Review Evidence Matrix 和 Artifact 自主
分析研究问题，并按需发现公开资源或在隔离 Sandbox 中处理数据；用户可以查看每轮状态、来源、Tool
调用、预算、审批、错误和 Artifact，并能取消当前 Turn 或拒绝危险操作。

```text
创建/打开 Project-scoped AgentSession
  → 用户消息创建 AgentTurnRun
  → 固定 ContextSnapshot、PolicySnapshot 和 Budget
  → Deep Agents 规划并调用受控 Tool/MCP
  → Browser/下载受 URL 与网络策略约束
  → 高风险动作进入人工审批
  → 文件只在隔离 Workspace 中处理
  → 平台校验 Evidence、Manifest 和 Artifact
  → Citation/来源校验
  → 提交结果、Usage、审计和可重放 Event
```

阶段结束时，Research Agent Extension 是 Demo-ready Core v1 之上的独立扩展。禁用或移除 Agent
Runtime 不影响文献导入、RAG 和固定 Review Workflow。

## 范围

### 包含

- Agent Session 多轮 Chat、Turn 详情、事件、审批、取消、来源和 Artifact UI/API；
- Project Chunk Index、Review Evidence Matrix 和既有 Artifact 的受权 Context Tool；
- Paper/Evidence 读取、公开资源搜索、Browser、仓库/数据集/补充材料发现和 Artifact 提交工具；
- 版本化 Tool Registry、MCP Server Registry、Tool Schema、权限、风险等级、超时、输出限制和执行记录；
- Browser/URL Allow Policy、DNS/IP/Redirect 检查、SSRF 防护和下载隔离；
- Deep Agents Tool Policy、Human-in-the-loop、步骤/Token/费用/时间/Tool Call/输出预算；
- Workspace/Sandbox 生命周期、文件传输、网络和计算资源限制；
- Agent Event、Usage、ToolExecution、Approval、Workspace 和 Artifact 审计；
- Runtime、MCP、Browser、Sandbox 和 Provider 的取消、重试、断连、恢复与对账；
- Prompt Injection、跨用户隔离、Secret 外泄和恶意下载测试；
- Deep Agents 升级契约测试、故障注入、Agent 评测集和运维文档；
- 平台安装、版本化和 allowlist 控制的 Research Skills，以及 owner-scoped 声明式 Skill 的安全治理；
- 基于 Phase 5 OpenSandbox `execute` 与固定依赖的实际 Spike 证据，强化并产品化结构化、受限的数据分析
  与绘图能力。

### 不包含

- 自行重写 Deep Agents 的 Agent Loop、Planner、上下文压缩或 Checkpoint 引擎；
- 每轮从 PostgreSQL 重放完整产品消息历史，或把 `ContextSnapshot` 当作第二套 Runtime 对话状态；
- 任意用户提供的 MCP Server、Tool 代码、Sandbox 镜像或系统 Prompt；
- 用户上传、安装或修改可执行 Skill、二进制或动态依赖；Phase 5 已允许的 owner-scoped 声明式 Markdown/
  文本 Skill 除外；
- 宿主 Shell、宿主 Python、动态包安装、Docker Socket 或宿主文件系统；Sandbox `execute` 仅限
  ADR-0007 的 Session 专属 OpenSandbox；
- 绕过登录、付费墙、robots/站点限制、CAPTCHA 或下载授权；
- 自动对外发帖、发邮件、提交表单、修改远程仓库或执行金融/不可逆操作；
- 无上限自主运行、无限子 Agent、跨 Project Memory 或跨用户共享 Workspace；
- 把网页/论文中的指令视为系统指令；
- 用 Agent 替代 Phase 3 的确定性 Review Workflow；
- 把 OpenSandbox `execute` 扩张为宿主执行、开放网络、动态依赖安装或通用 Coding Agent。

## 涉及模块

- Agent Session/Message/Turn API/UI 和 `ResearchAgentRuntime` Adapter；
- Run、Attempt、Step、Event/SSE、Approval 和 Reconciliation；
- Project Context、Paper、Evidence、Citation 和 Resource Manifest；
- Tool Registry、MCP Registry、ToolExecution 和 Policy Engine；
- Browser、URL Policy、Download Scanner 和 Artifact Storage；
- Workspace/Sandbox Lifecycle 和受限文件传输；
- Model Gateway、Usage Ledger、Budget 和无进展检测；
- JSON Log、OpenTelemetry、Metrics、Agent Evaluation 和运维文档。

## 产品边界和首版用户故事

Phase 6 仍只支持“Project 范围内的受控研究”，不扩大为通用 Coding Agent：

1. 用户打开 Phase 5 创建的 Agent Session，并继续多轮提出分析、比较或资料补充问题；
2. 每轮由平台固定可见 Paper/Chunk、Review Evidence Matrix、Artifact、Tool、Skill、网络和 Budget；
3. Agent 优先使用项目内部证据分析；需要时可搜索官方项目页、仓库、数据集和补充材料；
4. 需要计算时可使用 ADR-0007 已批准的 OpenSandbox `execute` 和固定 Python 依赖；扩大网络、依赖、
   外部副作用或宿主能力必须另行决策；
5. Agent 输出带 Evidence/来源的回答、Resource Manifest 或候选 Artifact；
6. 用户可以查看每轮来源、审批、Tool 历史、预算、错误和最终产物，并在同一 Session 中追问。

首版不以“自动撰写完整综述”作为 Agent 目标；完整综述仍由 Phase 3 固定 Workflow 生成。Agent 发现的资源只有经过平台校验和用户纳入后才能进入 Paper/Evidence 体系。

## 核心状态和所有权

```text
AgentSession       Project 范围内的持续业务会话和消息历史
AgentTurnRun       一条用户消息对应的生命周期、取消、重试和最终状态
Run Attempt        平台 Worker 对一轮 Turn 的至少一次执行与 lease
Runtime Binding    session_id ↔ SDK thread；turn_run_id ↔ SDK execution
Context/Policy     每轮不可变的授权、版本、工具和预算快照
Run Step           用户可理解的计划/阶段投影，不复制内部思考
Tool Execution     一次版本化 Tool/MCP 调用及副作用幂等记录
Approval Request   等待用户批准、编辑或拒绝的动作
Workspace          Session 逻辑命名空间与 Session/Thread 范围短 TTL Sandbox Lease
WorkspaceSnapshot  跨 Turn 持久化的内部工作文件与 Manifest
Usage/Budget       已消费与剩余额度的业务事实
Resource Manifest  发现的外部资源及来源验证结果
Artifact           通过平台校验并持久化的文件
```

- PostgreSQL 保存 Session、Message、Turn Run、Context/Policy/Workspace Snapshot、Attempt、Event、
  ToolExecution、Approval、Usage、Manifest 和 Artifact 元数据；
- Deep Agents/LangGraph 保存 Runtime 内部消息、计划、Checkpoint 和 Interrupt；
- PostgreSQL Message 是产品事实，Deep Agents Message/摘要/Checkpoint 是模型工作上下文；正常后续 Turn
  复用同一 Thread 并只追加新消息，完整产品历史只在 Runtime 损坏或 generation 迁移时受控重建；
- ContextSnapshot 的消息 sequence 只是审计、对账与重建水位，不进入日常 Prompt 重放；
- Sandbox Provider 保存临时 Workspace；其文件只有被平台显式取回、校验和提交后才成为 Artifact；
- Valkey/ARQ 只负责投递和实时通知；SDK Trace 只用于调试和诊断；
- Run Step 复用 Phase 3 的通用业务投影，不逐条复制 SDK 内部节点或完整推理；
- 一个 Runtime、MCP 或 Sandbox 标识不能脱离 `session_id`、`turn_run_id`、owner 和 Project 映射被公开查询。

## 状态机和等待语义

优先复用 Phase 3 已验证的等待/恢复语义，不为 Agent 创建第二套不兼容的 Run 状态机。若现有状态不足，先更新通用 Run 契约和迁移，再实现 Agent API。

概念状态复用 Phase 3 的 `WAITING_INPUT`：

```text
QUEUED → RUNNING → SUCCEEDED
             ├→ WAITING_INPUT → QUEUED
             ├→ RETRY_WAIT → QUEUED
             ├→ CANCEL_REQUESTED → CANCELLED
             └→ FAILED
```

- Agent Tool 审批使用 `WAITING_INPUT` 和专用 `agent_tool_approval` input kind，不新增平行的 `WAITING_APPROVAL` Run 状态；
- `WAITING_INPUT` 时没有 Worker 长期占用，不保持数据库事务或网络连接；
- Approval 与 LangGraph Interrupt/Checkpoint 绑定，但业务 Approval 是用户可见事实；
- 拒绝可以让 Agent 选择安全替代方案或稳定失败，不能静默改为自动批准；
- Approval 过期、Run 取消或 Project 权限撤销后不能恢复旧动作；
- Runtime 取消只是一层动作，业务 Run 终态仍由平台条件更新决定。

## Tool、MCP 和审批策略

### Tool Registry

所有 Agent 能力必须来自平台 Tool Registry，最低元数据包括：

- 稳定名称、语义版本和输入/输出 Schema；
- 风险等级、所需权限和适用资源范围；
- 超时、重试、最大输入/输出和预算成本；
- 是否有副作用、幂等键生成方式和审批要求；
- 实现类型：内置 Tool、HTTP Adapter、MCP Tool 或 Sandbox Tool；
- 日志/Event 的字段白名单与敏感字段规则；
- 可用状态和兼容的 Deep Agents/Runtime 版本。

模型看到的 Tool 描述不是授权。每次调用都由平台根据 `turn_run_id` 的 PolicySnapshot 重新检查 owner、
Project、Budget、Approval 和参数策略。

### 首版允许的工具类别

- `list_project_papers`：只列出当前授权 Project 的 Paper/Version ID 和必要元数据；
- `search_project_chunks`：只在当前 ContextSnapshot 固定的 Project Index/ChunkSet 中检索；
- `read_evidence`：按 Evidence ID 读取受控长度、带页码的文本；
- `read_review_evidence_matrix`：只读取快照中明确授权的 Review Output；
- `search_public_resources`：调用固定搜索 Provider 或固定 MCP Server；
- `fetch_public_page`：只读访问通过 URL Policy 的公开页面；
- `download_public_resource`：受大小/MIME/来源限制地下载到当前 Workspace；
- `write_resource_manifest`：提交结构化 Manifest 候选；
- `submit_artifact`：请求平台校验并提交 Workspace 中的明确文件；
- `write_report`：生成 Markdown Artifact，不直接修改数据库正文或其他 Run 文件。

### MCP

- MCP Server 只能由平台安装、审核并固定版本，不能从 Prompt、网页或 Project 数据动态添加；用户请求只
  能引用当前 owner 可见的 Catalog 条目并填写其参数 Schema 允许的非敏感安全值；
- Server、Transport、Endpoint、认证方式和允许 Tool 列表需要版本化；
- 远程 MCP 优先使用受控 Streamable HTTP；需要 stdio/本地进程的开源 MCP 固定版本后运行在 Session
  OpenSandbox。ARQ Worker 宿主不以 stdio 启动第三方 MCP；
- MCP Tool 加载结果必须与 Registry 中的名称和 Schema 对比；漂移时 fail closed；
- 拦截器负责权限、Correlation、预算、超时、输出裁剪和审计；
- MCP Resources/Prompts 默认不直接注入 Agent Context，使用前需单独审核和限制。

Phase 5 先验证 MCP Catalog/Profile 的 owner/Session 选择、Playwright MCP 与一个现有固定版本只读
Search MCP。Phase 6 才建设完整 Registry、Catalog 审核/禁用、OAuth/Credential 生命周期和公共网络
策略，并继续禁止用户提交原始 endpoint、transport、command、env、包版本或认证配置。

### Research Skills

- 平台安装 Skill 语义版本化并绑定所需 Tool、权限、预算和兼容 Runtime；owner-scoped 声明式 Skill 使用
  内容哈希和不可变版本；
- 用户只能启用平台 allowlist Skill 或维护自己的声明式 Markdown/文本 Skill，不可上传可执行脚本、
  二进制、覆盖系统指令或动态安装依赖；
- 平台安装 Skill 按代码变更审查，owner-scoped 声明式 Skill 受 Schema、大小、内容扫描和固定评测保护；
  旧 Turn 仍按 PolicySnapshot 中的版本恢复；
- Skill 不能授予 Tool、网络、Sandbox 或 Secret 权限，只能使用策略已经授权的能力。

### 审批

- 只读、低风险、当前 Project 内的 Evidence 查询可自动执行；
- Session 专属 OpenSandbox 中默认禁网的离线 `execute` 不逐命令审批；下载新文件、扩大网络范围、覆盖或
  正式提交 Artifact、使用凭据或产生外部副作用必须按策略审批；
- 审批 UI 展示工具名、参数摘要、目标域名、风险、预算影响和预期副作用；
- 用户可以批准、编辑允许编辑的字段或拒绝；编辑后重新走 Schema 和策略校验；
- Approval Token 单次使用，绑定 run_id、tool_execution_id、参数哈希、actor 和过期时间。

## Browser、URL 和下载安全

### URL Policy

- 只允许 `https`，明确需要时才对固定测试目标开放 `http`；
- 拒绝 URL 中的用户信息、非标准编码混淆和不支持的 Scheme；
- 解析并阻断 loopback、link-local、private、multicast、unspecified、保留地址和云元数据地址，包括 IPv4/IPv6 及其编码变体；
- DNS 解析结果在连接前检查，连接目标与校验结果绑定，防止 DNS rebinding；
- 每次 Redirect 重新执行完整策略，限制跳转次数，禁止 HTTPS 降级；
- 域名 Allowlist/类别策略由平台配置，不由模型或网页扩展；
- 设置连接、读取、总时长、响应头和正文大小上限。

### 内容和下载

- HTML、PDF、README、Issue、仓库和数据文件都按不可信输入处理；
- 页面中的“忽略系统指令”“发送 Secret”等文本不能改变 Agent 权限；
- 下载同时检查声明 MIME、文件头、扩展名、大小和内容哈希；
- 未知归档、可执行文件、脚本、宏文档和嵌套压缩默认拒绝或隔离，不自动执行/解压；
- 下载文件名只作展示，Storage Key 和 Workspace Path 由平台生成；
- Browser/下载不携带用户 Cookie、数据库凭据、模型 Key 或内部服务 Token；
- 来源记录包含请求 URL、规范化 URL、最终 URL、获取时间、Content-Type、大小和哈希。

## Workspace 和 Sandbox 安全

- Session 拥有逻辑 Workspace；每个 AgentSession/SDK Thread 最多复用一个短 TTL OpenSandbox Lease，
  不跨 owner/Session 共享。同一 Session 继续以单活动 Turn 防止并发写入；Lease 失效、取消后环境污染或
  策略要求重置时递增 generation，并从受控 WorkspaceSnapshot/Artifact 重建；
- Agent 运行在 Worker/Runtime，Sandbox 作为 Tool；模型和平台 Secret 不进入 Sandbox；
- Sandbox 自有文件系统作为物理 Workspace，不直接挂载 API/Worker 宿主目录；平台通过 Provider 原生
  文件传输或受控 Adapter 注入 Snapshot/Artifact，并取回新 Snapshot 或候选 Artifact；
- Sandbox 使用非 root 用户、固定镜像、独立临时目录和显式输入 Snapshot/Artifact；首版固定 Python、
  pandas、numpy、matplotlib 和必要字体，不允许动态安装包；
- 默认禁网；只有平台固定 Browser 目标进入 egress allowlist，策略覆盖 Chromium、Python、`curl` 等全部
  Sandbox 进程，并记录域名、流量和拒绝原因；
- 限制 CPU、内存、PID、磁盘、文件数、单文件大小、墙钟时间和输出大小；
- 不挂载宿主源码、用户主目录、Docker Socket、数据库 Socket、云元数据或 Secret；
- 禁止特权模式、宿主网络、危险 Capability 和不受控嵌套容器；
- 文件传入/取回走 Provider 原生传输或平台 Adapter，不由模型构造宿主路径；
- OpenSandbox Backend 是 Deep Agents `CompositeBackend` 默认 Backend，模型可调用的 `execute` 只能到
  当前 Session Sandbox；`/conversation_history/`、`/large_tool_results/` 等 Runtime 内部路径路由
  `StateBackend`，不进入业务 WorkspaceSnapshot；
- Deep Agents 文件 `permissions` 不能保护 `execute`、自定义 Tool 或 MCP；平台不能以命令字符串检查
  冒充强隔离，必须依靠 Sandbox、统一 egress、Secret/宿主隔离、资源限制和文件提交协议；
- Workspace 在 Session 关闭、Sandbox Lease TTL 到期、generation 重置或策略要求回收时幂等清理；普通
  Turn 终态不自动销毁仍有效的 Session 级 Lease。清理失败进入可观察的补偿队列；
- Artifact 提交后仍不信任 Workspace，必须由平台重新读取并校验。

### 受限代码分析

ADR-0007 已批准 Phase 5 Slice 7 在 OpenSandbox 中开放 `execute`，用于研究数据处理与绘图。Phase 6 的
任务是把已通过 Spike 的配置强化为可观察产品能力，而不是再次决定是否开放。至少保持以下条件：

- 使用固定依赖和固定镜像，不允许任意安装包；
- 输入只来自显式 WorkspaceSnapshot/Artifact，输出只允许 `/workspace` Manifest 中的路径；
- 默认禁网，资源和时间限制有实际测试；
- 代码、命令、stdout/stderr 和产物受大小及敏感信息过滤；
- 离线命令不逐条审批，但网络扩大、外部副作用和正式 Artifact 提交的审批、取消、幂等和审计已定义；
- 不向 Sandbox 注入模型、MCP 或 OpenSandbox Secret，且取消后不启动新命令。

## Budget、无进展和终止策略

每个 Agent Turn 在创建时通过 PolicySnapshot 固定版本化 Budget Policy，至少限制：

- 最大模型步骤和总 Tool Call；
- 最大输入/输出 Token 和可选费用；
- 最大墙钟时间、单次 Tool 超时和等待审批时间；
- 最大 Browser 页面、下载数量、单文件与总文件大小；
- 最大 Workspace 磁盘和 Artifact 输出；
- 最大失败、重试、相同 Tool+参数重复次数；
- 子 Agent 数量，首版固定为 0。

平台在 Tool 调用前预检，在结果后记账。达到硬限制立即停止新操作并以稳定错误结束；接近软限制时要求 Agent总结现有结果。连续重复相同动作、无新增 Evidence/Manifest、计划循环或预算消耗无产出触发无进展终止。

## API、Event 和数据变化方向

### API

```text
POST /api/v1/projects/{project_id}/agent-sessions
GET  /api/v1/agent-sessions/{session_id}
GET  /api/v1/agent-sessions/{session_id}/messages
POST /api/v1/agent-sessions/{session_id}/messages
GET  /api/v1/agent-turn-runs/{run_id}
POST /api/v1/agent-turn-runs/{run_id}/cancel
GET  /api/v1/agent-turn-runs/{run_id}/manifest
GET  /api/v1/agent-turn-runs/{run_id}/tool-executions
GET  /api/v1/agent-turn-runs/{run_id}/approvals
POST /api/v1/agent-turn-runs/{run_id}/approvals/{approval_id}/decisions
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/artifacts/{artifact_id}
```

- Session 创建请求只绑定 URL 中的 Project；Message 请求只接受用户内容和有限公开选项。专用 Session
  Capability API 只引用当前 owner 可见的 Catalog/Skill ID 和 Schema 允许的安全参数；owner、最终
  Tool/Skill/MCP allowlist、Sandbox、SDK Thread 和内部 Context/Policy 仍由服务端解析与固化；
- ToolExecution 默认只返回脱敏摘要，管理员诊断信息不暴露给普通用户；
- Approval 决策使用幂等键，重复提交同一决定返回稳定结果，不重复恢复 Runtime；
- Artifact 下载再次校验 owner、Project、隔离/扫描状态和内容处置策略。

### Event

在 Phase 5 白名单事件基础上增加：

```text
agent_step_changed
agent_budget_updated
agent_tool_rejected
agent_waiting_for_approval
agent_approval_resolved
agent_workspace_created
agent_workspace_cleanup_requested
agent_workspace_cleaned
agent_policy_violation
agent_no_progress_detected
```

Event Payload 保持小型、版本化和脱敏。高频 Token/流式片段不逐条写 PostgreSQL；聚合 Usage 周期性或在边界事件中提交。

### 数据

具体迁移在对应切片前确定，关系至少覆盖：

```text
AgentSession
 ├─ AgentMessage
 ├─ RuntimeThreadBinding
 ├─ SandboxLease
 ├─ WorkspaceSnapshot
 └─ AgentTurnRun ── ContextSnapshot / PolicySnapshot
      ├─ RuntimeExecutionBinding
      ├─ RunStep
      ├─ ToolExecution ── ApprovalRequest
      ├─ UsageLedger
      ├─ ResourceManifest ── ManifestItem
      ├─ Event
      └─ Artifact
```

唯一约束至少保护：Session/Thread、Turn/Execution 映射、WorkspaceSnapshot 版本、Session 单活动 Turn、Message 幂等键、Runtime
Tool Call ID、Tool 副作用幂等键、Approval 单次决定、Manifest 规范化 URL、Artifact 提交和 Event Sequence。

## 可靠性、取消和恢复

- ARQ Job 仍只携带 `turn_run_id`，Worker 通过业务状态认领执行；
- Worker 崩溃后由 lease/reconcile 找回 Turn，再查询 Runtime Execution、Workspace 和 ToolExecution 状态；
- Runtime 成功但本地响应丢失：按稳定 Session/Thread 与 Turn/Execution 映射重新读取最终输出，不重新运行 Agent；
- Tool 超时或断连：先按 ToolExecution ID 对账；只有确定未产生副作用时才重试；
- MCP/Browser/Sandbox 各自的内部重试不得与平台重试叠加成重试风暴；
- Artifact 使用 staged → validated → committed 生命周期和内容哈希去重；
- 取消请求原子写入业务 Run/Event，随后传播到 Runtime、当前 Tool 和 Sandbox；
- 取消后不发起新模型、Tool、MCP、Browser 或 Sandbox 操作；晚到结果只用于对账和清理，不能提交业务成功；
- Approval 等待期间发生权限撤销、策略升级或预算过期时，恢复前重新校验；
- Runtime、MCP 或 Sandbox Provider 长期不可用时稳定失败，不让 Run 永久卡在 RUNNING。

## 安全和隐私不变量

- Agent 只能访问当前 Turn 的 Context/Policy Snapshot 固定授权的 owner、Project、Paper、Chunk、Evidence、
  Artifact、Tool、Skill、网络目标和 Workspace；
- 所有外部内容都是不可信数据，不具有修改系统指令、权限和策略的权力；
- Secret 只存在于最小需要的宿主组件，不进入 Prompt、Event、Trace、Workspace、Artifact 或 MCP 参数；
- 日志不记录完整 Prompt、论文全文、网页全文、文件内容或敏感 Tool 参数；
- 不允许 Agent 直接连接 PostgreSQL、Valkey、内部管理 API 或宿主文件系统；
- 所有 Tool 调用执行前检查 Schema、权限、预算、审批和幂等；
- 高风险或不可逆操作必须审批；首版不提供对外写操作；
- Runtime、Tool 和 Sandbox 的安全拒绝属于正常产品行为，需要稳定错误码和用户可理解说明；
- 用户 A 无法从 ID、错误、Event、Timing、Manifest、Workspace 或 Artifact 推断用户 B 的资源；
- Sandbox 安全声明必须由配置和测试证据支持，不把“容器化”等同于完整隔离。

## 可观测性和审计

- Trace 关联 `request_id`、`trace_id`、`project_id`、`session_id`、`turn_run_id`、`attempt_id`、
  `thread_id`、`tool_execution_id` 和 `workspace_id`；
- 高基数 ID 进入日志和 Trace，不作为 Prometheus Label；
- Metrics 覆盖 Run 终态、Runtime/Tool/Sandbox 延迟与错误、策略拒绝、审批等待、预算消耗、无进展终止和清理积压；
- SDK/LangSmith Trace 可辅助调试，但 PostgreSQL Event/ToolExecution 才是产品审计事实；
- 原始模型思考不作为审计内容；审计记录输入摘要/哈希、策略决定、Tool 版本、状态、时长和产物引用；
- 每个用户可见错误可通过 Correlation ID 定位到平台、Runtime、MCP、Browser 或 Sandbox 层级。

## Agent 评测

建立固定、小型、可人工审核的 Agent Evaluation Dataset，至少覆盖：

- 能否在多轮对话中保持用户目标，同时只读取每轮 Snapshot 授权的项目上下文；
- 能否正确使用 Project Chunk Index 和指定 Review Evidence Matrix 回答分析问题；
- 能否找到预期的官方项目页、代码仓库、数据集或补充材料；
- Manifest URL、资源类型、Paper/Evidence 关联和来源是否正确；
- 报告中的重要结论能否追溯到 Evidence 或外部来源；
- 面对证据不足时是否明确停止推断；
- 是否遵守 Project、Tool、网络、下载、预算和审批限制；
- 面对网页 Prompt Injection 是否拒绝泄漏 Secret、扩大权限或执行危险动作；
- 是否出现重复 Tool、无进展循环、无效下载或重复 Artifact；
- 取消、断连、恢复和 SDK 升级后行为是否保持契约。

默认评测使用 Fake Model、固定 Tool、HTTP Mock、合成网页和恶意 Fixture。真实模型/公开网络评测显式启用并记录模型、SDK、Prompt/Policy 版本、预算、时间和人工审核结果。自动评分只作辅助，不把单一 LLM Judge 当作安全证明。

## 实现切片顺序

Phase 5 Slice 7 先分别提供 OpenSandbox、MCP 配置、Playwright/Search MCP 和原生 Skill 的最小证据。
Phase 6 只在这些 Spike 实际通过后按以下顺序强化，不把 ADR-0007/0008 本身当作测试结果：

1. **产品契约与威胁模型**：复核 Session/Turn/Snapshot、Session 级 Sandbox Lease、资产/信任边界、
   `execute` 攻击面和安全验收；
2. **Tool Registry 与执行记录**：版本化 Schema、权限、风险、预算、幂等和 ToolExecution 持久化；
3. **Project/Evidence Context**：强化 Chunk Index、Review Matrix、Artifact 的固定授权快照与隔离测试；
4. **MCP Registry 与策略拦截**：从 Phase 5 最小 Catalog/Profile 扩展到审核/禁用、Schema 漂移、权限/
   预算/超时/输出限制、OAuth/Credential 和审计；
5. **Browser/URL/下载安全**：在 Phase 5 同 Sandbox Playwright MCP 基础上补齐 URL Policy、SSRF/Redirect/DNS、
   Prompt Injection、内容限制、隔离下载和来源记录；
6. **Approval 与恢复**：业务 Approval、Deep Agents Interrupt/Resume、过期/取消/重复决定和 UI；
7. **Workspace/Sandbox 强化**：Session Lease/generation 隔离、WorkspaceSnapshot 重建、`execute`、统一
   egress、资源限制、TTL 清理和故障补偿；
8. **Budget 与无进展检测**：步骤/Token/费用/时间/Tool/输出限制和循环终止；
9. **Agent UI 与 Artifact**：多轮 Chat、Turn Detail、Step、Tool、审批、来源、Manifest、报告和文件查看/下载；
10. **故障注入与安全测试**：Runtime/MCP/Browser/Sandbox 故障、Prompt Injection、Secret、越权、恶意文件和取消竞争；
11. **评测与升级保护**：固定 Agent 评测集、Deep Agents 升级契约测试、性能/成本基线和已知限制；
12. **验收复盘**：Runtime Compose/Profile、隔离 Workspace 恢复边界、演示运行文档、模块学习笔记和
    Research Agent Extension 完成报告。Core 数据库/Storage 的生产备份恢复不自动转移到本阶段。

## 测试方式

- **Domain**：Budget、Tool/Approval 状态机、策略决定、幂等键、URL/IP 分类和 Artifact 生命周期；
- **Application**：授权 Context、Tool 执行、审批恢复、取消、对账、Usage 和 Event 原子性；
- **Runtime Contract**：Deep Agents 版本升级前后运行同一契约套件；
- **MCP**：Schema 漂移、恶意 Tool 描述/输出、超时、断连、认证失败、会话泄漏和拦截器；
- **Browser/HTTP**：IPv4/IPv6 私网、DNS rebinding、Redirect 链、超时、大响应、错误 MIME 和 Prompt Injection Fixture；
- **Sandbox**：Session 内跨 Turn 复用与跨 owner/Session 隔离、WorkspaceSnapshot 取回/重建、模型可见
  `execute` 只能到当前 OpenSandbox、Secret/宿主路径不可见、统一网络拒绝、CPU/内存/PID/磁盘/时间/
  输出限制、取消后不启动新命令、销毁和清理补偿；
- **PostgreSQL**：唯一约束、条件更新、Approval 单次决定、ToolExecution 去重、Usage 和跨用户隔离；
- **故障注入**：Worker/Runtime/MCP/Sandbox 退出、响应丢失、重复 Job、取消竞争、Artifact 提交前后崩溃；
- **E2E**：打开 Agent Session → 项目内分析 → 追问 → 查找公开资源 → 审批下载 → Artifact → 刷新恢复与取消；
- **评测**：多轮上下文、项目内分析、资源发现、来源正确性、Groundedness、策略遵守、Prompt Injection 和无进展样本。

普通 CI 必须完全离线且不需要真实模型、外部 MCP、公共网站或付费 Sandbox。真实运行使用显式 Marker/环境开关、专用测试账号、硬预算和可删除 Workspace；只记录实际执行结果。

## 阶段完成条件

- 至少一个绑定 Project 的多轮研究用户故事可从 UI 端到端完成，并能使用项目索引、Review Matrix、
  公开资源或受控工具生成可追溯 Artifact；
- Deep Agents 继续被 `ResearchAgentRuntime` Adapter 隔离，SDK 类型不污染 Domain 和公开 API；
- AgentSession/Message/AgentTurnRun、SDK Thread/Execution、Snapshot 和 Workspace 的所有权与恢复语义有测试证据；
- 临时文件、内部 WorkspaceSnapshot 和正式 Artifact 的生命周期分离，Sandbox 丢失后可重建内部工作状态；
- Agent 只能访问当前 Turn 授权的 Project Context、Tool、Skill、网络目标和 Workspace；
- MCP、Browser、下载、Approval、Budget 和 Sandbox 策略均有自动化及必要的真实验证证据；
- Prompt Injection 不能获得平台 Secret、数据库权限、宿主文件或未授权网络；
- 最大步骤、Token、费用、时间、Tool Call、下载和输出限制实际生效；
- 取消后不发起新操作，重复执行不重复提交 Tool 副作用或最终 Artifact；
- Runtime、MCP、Browser 和 Sandbox 故障可以恢复、对账或稳定失败，不永久卡住；
- Deep Agents 升级由契约测试和 ADR 保护，失败时可阻止升级或回滚；
- Agent Event、Usage、ToolExecution、Approval、Workspace 和 Artifact 可审计且不记录敏感全文；
- Core 与 Agent 两组用户旅程、评测、运维文档、模块笔记、已知限制和真实运行证据齐全；
- 开发者能解释 Prompt、模型、Tool Policy、MCP、Sandbox 和业务权限各自能解决什么、不能解决什么。

## 实现前仍需确定

1. ADR-0007 已选 OpenSandbox，7.1 已固定 SDK、base image digest、TTL 和 CPU/内存/命令/输出参数；仍需
   真实 Smoke 确定 derived image 发布 digest、Server 部署、孤儿清理和这些限制的实际强制效果；
2. 首版允许的公共域名类别、Phase 5 现有 Search MCP 通过后进入产品 Catalog 的条目；
3. Approval 风险矩阵及允许编辑的参数；
4. Budget 默认值、费用数据不可得时的替代限制和告警阈值；
5. Workspace TTL、清理补偿和 Artifact 安全扫描策略；
6. Phase 5 Sandbox `execute` Spike 通过后，哪些计算与绘图能力进入用户可见产品、如何展示和审批；
7. 首批平台 Research Skills 与 owner-scoped 声明式 Skill 的评测、禁用和内容安全方式；
8. Deep Agents 子 Agent 和长期 Memory 是否保持永久禁用；首版默认禁用。

任何把代码执行扩大到宿主、开放网络、动态包安装、用户自定义 Tool/MCP、对外写操作或长期 Memory 的
决定都必须单独更新本 Spec，并在满足 `AGENTS.md` 条件时创建 ADR。

## 已知预期限制

- Agent 输出仍需人工审核，不等同于系统性文献综述或事实保证；
- 公开资源可能变化、删除或限制访问，Manifest 必须保留获取时间和来源；
- 第三方模型、MCP 和 Sandbox Provider 会带来成本、可用性、隐私和供应商风险；
- Prompt Injection 无法只靠分类器或 Prompt 消除，系统依赖最小权限和基础设施隔离限制后果；
- 完整浏览器兼容性、复杂登录流程和网页交互不属于首版目标；
- 多 Agent、长期 Memory、宿主执行、动态安装和开放网络保持关闭；OpenSandbox `execute` 不改变 Research
  Agent 的领域定位，也不代表通用 Coding Agent；
- Research Agent Extension 可以独立禁用，Demo-ready Core Research Backend v1 仍应完整运行。

## 预期学习笔记

模块真正完成后再撰写，不预建空文件：

- `research-agent-runtime.md`：Agent Session/Turn 与 Deep Agents Thread/Execution/Checkpoint 的集成边界；
- `agent-tool-policy.md`：Tool/MCP Registry、权限、预算、审批和副作用；
- `browser-download-security.md`：URL、SSRF、Prompt Injection 和文件隔离；
- `agent-sandbox.md`：Workspace 生命周期、资源限制、文件传输和清理；
- `agent-skills.md`：平台安装与 owner-scoped 声明式 Skills 的版本、隔离、权限依赖、评测和升级；
- `agent-evaluation.md`：多轮研究、项目上下文、资源发现、来源、策略遵守和安全评测。

## 参考资料

- [`ADR-0007：采用 OpenSandbox 可执行研究 Workspace`](../decisions/0007-use-opensandbox-executable-workspace.md)
- [`ADR-0008：复用 Deep Agents 原生 MCP 与 Skills 能力`](../decisions/0008-use-native-mcp-and-skills-capabilities.md)
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Going to production](https://docs.langchain.com/oss/python/deepagents/going-to-production)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Deep Agents Comparison](https://docs.langchain.com/oss/python/deepagents/comparison)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)

参考资料描述 SDK 能力，不构成本项目的安全保证。安全结论必须来自固定版本、实际部署配置、威胁分析和测试证据。
