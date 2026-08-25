# ADR-0005：采用交互式 Research Agent 会话模型

- 状态：已接受
- 日期：2026-08-25
- 决策者：项目维护者

## 背景

ADR-0001 已选择基于 LangGraph 的 Deep Agents 作为 `ResearchAgentRuntime` 内部实现。Phase 5 初版
Spec 随后把首个 Agent 用户故事限定为一次性的论文公开资源发现任务，并倾向于让一个业务 Agent Run
稳定映射一个 SDK Thread 和一个临时 Workspace。

该故事适合验证 Browser、下载和 Artifact，但不足以表达项目希望展示的最终产品：用户需要一个绑定
Research Project、能够持续对话、按需读取项目论文索引和 Review Evidence Matrix，并通过受控 Tool、
Browser、Sandbox 与科研 Skill 完成研究分析的工作空间。另一方面，把它直接扩张为通用 Coding Agent
会失去文献综述领域边界，并显著放大宿主执行、网络、依赖安装和任意工具风险。

现有 Phase 2 RAG Conversation 已验证“Message + Run + Event + Outbox + 单活跃 Run”的可靠交互模式，
但其每个问题独立检索、不把历史消息放入 Prompt，且 Message/ClaimSet 契约面向有引用的 RAG Answer。
交互式 Agent 还需要长期 SDK Thread、Workspace、Approval、Tool 和 Skill 状态，因此不能直接把现有
RAG Conversation 数据模型改造成 Agent Runtime 状态容器。

## 决策

将 Research Agent Extension 定位为 **Project-scoped Research Workspace Agent**。资源发现、研究
分析、结构化表格、受控科研脚本和 Artifact 生成是它的能力；它不是单一资源发现器，也不是通用
Coding Agent。

采用以下稳定所有权映射：

```text
AgentSession       业务长期会话，绑定 owner 与 Project
  └─ SDK Thread    Deep Agents 的持续对话与 Checkpoint 上下文

AgentTurnRun       一条用户消息触发的一次可取消、可重试业务执行
  └─ SDK Execution 同一 Thread 上的一次 invoke/resume

RunAttempt         ARQ Worker 对 AgentTurnRun 的一次至少一次执行尝试
ContextSnapshot    当前 Turn 固定可见的 PaperVersion、ReviewOutput 和 Evidence 范围
PolicySnapshot     当前 Turn 固定的 Tool、Skill、Budget、网络与审批策略
Logical Workspace  AgentSession 的受控研究工作区
WorkspaceSnapshot  跨 Turn 持久化的内部工作文件及 Manifest
Sandbox Lease      一个 Turn 或短 TTL 内的物理隔离环境，可重建和回收
```

具体决定如下：

- `AgentSession : SDK Thread = 1 : 1`。业务表只保存通用 Runtime Binding 和不透明引用，不暴露 Deep
  Agents SDK 类型；Runtime 升级、重置或损坏恢复可以为同一 Session 建立新 binding generation，但
  旧映射必须保留审计信息；
- `AgentTurnRun : SDK Execution = 1 : 1`。每条用户消息创建新的业务 Run、Attempt、ContextSnapshot、
  PolicySnapshot、Usage 和终态；Worker 崩溃重试恢复同一 Turn，不能把同一用户消息再次追加到 Thread；
- 一个 AgentTurnRun 表示从一条普通用户消息到最终 Assistant Message 的完整产品交互，内部可以包含多次
  LLM 调用、Tool Call、Observation 和 Interrupt；这些内部 Step 不各自创建 AgentTurnRun；
- 同一 AgentSession 同时最多有一个活跃 Turn。普通后续消息只有在上一 Turn 终态后才能提交；
  `WAITING_INPUT` 时只能提交与当前 Approval/Interrupt 对应的决定；
- AgentSession 长期绑定 Project，但每个 Turn 固定自己的授权快照。Project 后续新增、移出或换版论文
  不改变历史 Turn；新 Turn 可以读取新的 Project 状态；
- Runtime 初始上下文只包含稳定 ID、计数和小型摘要。Paper Chunk、Evidence 和 Matrix 按需通过平台
  Tool 读取；owner、Project、Session、Run 和 ContextSnapshot 由服务端注入，不能由模型参数指定；
- RAG Conversation 与 AgentSession 使用独立业务模型。Agent 复用 Phase 2 Retriever、Evidence 和
  Citation 能力以及 Phase 3 Matrix/Artifact 事实，不通过内部 HTTP 调用 RAG Conversation，也不让
  Deep Agents 直接访问 Repository 或数据库；
- PostgreSQL AgentMessage 是用户可恢复的产品对话事实。SDK Message 和 Checkpoint 负责 Runtime
  连续执行，但不能成为权限、消息历史、Usage、Approval、Event 或 Artifact 的唯一来源；
- 高频 Assistant token、Browser 进度和 Sandbox 输出使用可丢失的临时流；Turn、Tool、Approval、
  Assistant Message、Artifact 和终态使用 PostgreSQL 中的版本化业务 Event。原始思考过程不进入任一
  用户可见流；
- Logical Workspace 属于 AgentSession，但不能只存在于 Sandbox Provider。物理 Sandbox 以 Turn 或
  短 TTL Lease 使用，销毁后从平台允许的 WorkspaceSnapshot/Artifact 重建；临时文件在 Turn 后丢弃，
  需要跨 Turn 的内部研究笔记和中间文件进入受控 WorkspaceSnapshot，只有用户可见或可下载的正式业务
  产物经过平台校验后才提交为 Artifact；
- Sandbox 文件系统是物理 Workspace，而不是宿主目录挂载。平台通过受控文件传输为其注入输入、取回
  Snapshot 或候选 Artifact；首版模型只看到受限文件工具，不看到 `execute`、Shell、包管理器或任意网络；
- Skills 只能来自平台版本化 Catalog，Session 绑定 `skill_id + version + content_hash + required
  capabilities`。用户不能上传任意 Skill、MCP Server、Tool 代码、Sandbox 镜像或系统 Prompt；包含
  脚本的 Skill 只能在满足其声明策略的隔离 Sandbox 中执行；
- Phase 5 先验证业务包装、多轮 Thread、Context 和恢复边界，再逐项验证 Deep Agents 已有或可集成的
  MCP、Browser、Sandbox 和 Skills 能力。SDK 提供能力不等于平台已经授权或完成安全验证。

## 首个受限验证故事

Phase 5 的首个故事固定为两轮 Project-scoped Research Agent 对话：

1. 用户创建绑定 Project 的 AgentSession；
2. 第一轮要求 Agent 基于当前 Project 索引和一个明确选择的 Review Evidence Matrix 完成结构化研究分析；
3. 第二轮在同一 SDK Thread 中追问或要求调整结果，证明消息、Thread 和 Context 能连续恢复；
4. Agent 生成一个小型、可追溯的候选 Artifact；
5. 默认测试使用 Fake Runtime、Fake Chat Model 和确定性 Tool，不访问真实模型、公共网站、外部 MCP 或
   付费 Sandbox。

Browser 下载、受控 MCP、Sandbox 脚本和平台 Skill 仍在 Phase 5 后续独立 Spike 中验证，不是切片 1
的进入条件，也不改变首个故事的固定授权范围。

## 后果

正面影响：产品形态能够持续复用 Project 文献上下文，展示比一次性任务更完整的 Agent 体验；业务
Run、至少一次执行、取消、恢复和 SSE 能继续复用 Core 的可靠性模式；Deep Agents Thread 与业务
Conversation 的角色更清楚；资源发现不再限制产品叙事，Sandbox 中的代码又仍服务于科研任务。

代价与风险：Session、Turn、Thread、Workspace 和 Sandbox Lease 的生命周期更复杂；取消或损坏 Turn
后能否安全继续同一 Thread 必须通过 Spike 验证；SDK 成功但业务 Message 未提交、临时 Token 流断线、
Workspace 重建和 Runtime 升级都会增加对账场景；Phase 5 初版 Spec 和 Phase 6 方向需要整体对齐。

## 被否决的方案

- **继续以单次资源发现 AgentRun 为产品中心**：安全范围清晰，但无法表达持续研究交互，RAG Index 和
  Evidence Matrix 只能成为一次性输入；
- **每个 Turn 创建独立 SDK Thread**：恢复简单，但会失去 Deep Agents 的原生多轮上下文，并需要平台
  重放完整对话和 Workspace；
- **一个 Session 只使用一个永久业务 Run**：接近 SDK Thread，但无法为每轮消息独立表达取消、重试、
  Attempt、预算、终态和故障恢复；
- **直接扩展现有 RAG Conversation**：可以复用表和 API，但会把 RAG ClaimSet 契约、Agent Workspace
  和 SDK Thread 生命周期耦合到同一聚合；
- **定位为通用 Coding Agent**：能力广，但与文献综述项目的领域价值和安全边界不匹配；
- **在切片 1 同时启用 MCP、Browser、Sandbox 和 Skills**：能快速展示 SDK 功能，但无法先证明业务
  所有权、权限和恢复边界，失败时也难以定位责任层。

## 与其他 ADR 的关系

- ADR-0001 继续决定 Deep Agents 选型与 `ResearchAgentRuntime` 隔离边界；
- 本 ADR 决定交互式产品模型以及 AgentSession、AgentTurnRun、SDK Thread 和 Workspace 的所有权；
- ADR-0004 的 Demo-ready Core v1 边界不变，Research Agent Extension 仍可独立禁用。
