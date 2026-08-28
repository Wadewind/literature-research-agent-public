# 文献综述 Agent 系统：学习与开发实施指南

> 状态：Proposed v19
>
> 日期：2026-08-28
>
> 定位：面向单人、AI 辅助开发的总体实施文档；用于确定产品边界、总体架构、模块职责、阶段顺序和学习目标
>
> 技术方向：Python / FastAPI / LangGraph / PostgreSQL / pgvector / ARQ / Valkey / React
>
> v2 变更：将 RAG、固定 Workflow 和可靠后端定义为可独立交付的 Core v1；Research Agent 改为 Core 完成后通过成熟 SDK 接入的扩展里程碑
>
> v3 变更：将 Phase 4 交付固定为 Demo-ready Core v1，采用本地开发启动、离线 Fake 演示、最低
> Logs/Metrics 和可复现评测；公网生产、认证、备份恢复、永久删除/GC、OpenTelemetry 和 SLA 不属于
> 该里程碑
>
> v4 变更：将 Research Agent Extension 从一次性资源发现 Run 调整为绑定 Project 的持续研究对话，
> 固定 `AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1`，并要求 Phase 5 先验证
> 业务包装与恢复边界，再逐项接入 Deep Agents 的 MCP、Browser、Sandbox 和平台 Skills
>
> v5 变更：根据首个真实 Adapter Spike 增加 Runtime 部署与崩溃恢复门槛；Project Research Context
> 完成后必须先决定恢复 owner，并以真实第二 OS 进程验证 orphan `RUNNING` 接管和持久终态对账，才进入
> MCP、Browser、Sandbox、平台 Skills 与 Agent Chat UI
>
> v6 变更：ADR-0007 选择 OpenSandbox 作为可执行研究 Workspace；Slice 7 在每个 AgentSession/SDK
> Thread 专属的短 TTL Sandbox Lease 中开放 `execute`，并将能力顺序调整为真实 Runtime → OpenSandbox/
> WorkspaceSnapshot → Browser → MCP → Skill。宿主执行、用户自定义配置和默认开放网络仍然禁止。
>
> v7 变更：Slice 7.0 完成显式真实 Runtime enablement；默认 Fake，真实模式固定 ChatDeepSeek、持久
> Checkpointer、Project Context 和 RuntimeExecution control。`max_model_calls` 只覆盖 checkpoint 持久的
> 主 Agent Loop，不覆盖 summarization 内部重试或 Provider 在途窗口；预算 State 只保留当前 Turn，graph
> revision 升为 `deep-agent-graph.v2` 并拒绝恢复旧 v1；7.1 前仍需固定 Capability Profile。
>
> v8 变更：Slice 7.1 已实现固定 Capability Profile、OpenSandbox 0.1.15 薄 Adapter、Session Lease/
> generation/fence、`WorkspaceSnapshot`、统一 Tool 预算和 checkpoint pool/per-operation graph；graph
> revision 升为 `deep-agent-graph.v3`。真实 OpenSandbox Smoke 尚未运行，Browser/MCP/Skill 继续后置。
>
> v9 变更：ADR-0008 决定复用 `langchain-mcp-adapters`、Playwright MCP 与 Deep Agents 原生 Skills，
> 不再自研 Browser Tool 或 MCP Server；Slice 7.2–7.4 调整为 MCP 配置基础 → 同 Sandbox Playwright/
> 现有 Search MCP → 原生 Skills。用户只能配置平台安装 Catalog 与 owner-scoped 声明式 Skill；公共网络
> 和统一 egress 安全后移到 Phase 6。
>
> v10 变更：Slice 7.3 固定 `@playwright/mcp==0.0.79` 与
> `arxiv-mcp-server==0.6.2`，通过独立 npm/Python hash lock 预装到 Session Sandbox；生产 Loader 完整
> 分页发现后只投影审核 Tool 子集，当前 Lease resolver 不缓存 endpoint/client。无网络镜像内 MCP/
> Chromium/下载回路已验证。OpenSandbox 连接固定保留 Chrome `/entrypoint`，MCP 采用 loopback bootstrap
> → endpoint → exact Host 收敛；真实 OpenSandbox proxy Host/header、公共浏览和真实 arXiv 搜索仍未验证。
>
> v11 变更：Slice 7.4 已完成 Deep Agents 原生 Skills 业务包装。Session Skill Profile 只在首 Turn 前
> 以 CAS 配置并永久锁定；平台生成不可变、内容寻址的声明式 `SKILL.md`，每轮 Policy 冻结精确引用并
> 校验 required Tool 不扩权。`/skills/` 是 Sandbox `execute` 不可见的只读虚拟 Backend，Adapter 直接
> 使用 `create_deep_agent(skills=...)`；graph revision 升为 `deep-agent-graph.v5`。真实 Provider/
> OpenSandbox Smoke、附件/脚本 Skill、fork/rewind 与完整内容治理仍未完成。
>
> v12 变更：ADR-0009/0010 固定 Phase 6 的首批产品化增量。Browser 首版允许用户在两个 Turn 之间通过
> 平台鉴权画面操作同一 Session Chromium，不保存凭据、不与 Agent 并发控制，也不跨 Sandbox generation
> 恢复登录；Agent 文件区分 Attachment、WorkspaceSnapshot、Candidate 与 AgentArtifact，只有
> 显式 `submit_artifact` 的 `/workspace/outputs` 文件经过平台校验和业务成功提交后才能预览或下载。
>
> v13 变更：ADR-0011 将 Phase 6 收敛为适合本地个人项目的精简交付：保留 Agent 文件交换、Browser
> 人工控制、固定 Catalog/Profile、硬预算和 Sandbox 生命周期，并必须完成固定 `arxiv.org`/
> `export.arxiv.org` 的统一 egress、URL/SSRF 与 PDF 下载安全；完整 Approval Center、开放互联网、
> OAuth/Credential、通用 Registry、生产级 Sandbox 平台和精确计费延期。
>
> v14 变更：Phase 6 最终 UI 强制遵循 `web-ui-app-shell-redesign.md`：统一使用左侧 `AppSidebar` 与轻量
> `PageBar`，删除旧全局 Header、大 Hero 项目页头和重复模式入口。Phase 6 前置功能组件保持壳层无关；
> 最终整合前若重设计尚未实施，先按其 4 个纯前端切片分别完成并同步 UI 契约。
>
> v15 变更：Phase 6 Slice 1 形成 `research-agent-security-contract.md`，把 Phase 5 已验证事实与 Phase 6
> 目标事实分开，并冻结 owner/Project/Session/Turn/generation/Artifact 所有权、信任边界、自动执行/
> 直接拒绝矩阵、历史 Approval 字段兼容、事务外 I/O、Effectively Once、API/Event 增量和后续安全门槛。
>
> v16 变更：Phase 6 Slice 2 完成 Agent 输出 Artifact 垂直切片。真实 Sandbox Turn 固定装配
> `submit_artifact`，Candidate 经过事务外 regular-file/type/magic/hash/Storage 校验后进入 `VALIDATED`，
> 仅在 Turn 成功事务中发布独立不可变 AgentArtifact；Fake descriptor 不会伪造下载资源。公开 API 按
> owner/Project/Session/Turn 闭包列出并校验下载内容，Web 仅预览 PNG/JPEG，其余受支持类型默认下载；
> 固定 Tool/Policy 与 graph revision 分别提升到 v2/`deep-agent-graph.v6`，拒绝按旧能力契约恢复。
>
> v17 变更：Phase 6 Slice 3 完成 Browser 画面与跨 Turn 人工控制。平台以独立
> `BrowserControlLease` 固定 owner/Project/Session 与 Sandbox generation/fence，MANUAL 与 Agent Turn
> 互斥；业务 Lease 只持久化 MANUAL，不存在 ACTIVE 记录即为 Agent/idle。Web 通过短时 opaque ticket 和
> 平台 WebSocket 使用 noVNC 1.7.0；Adapter 在事务外经 OpenSandbox Server Proxy 连接 Sandbox 内固定
> websockify `6080`，再转发到 loopback TigerVNC `5901`，raw endpoint/headers 只短暂存在于 Adapter
> 内存。断线可在控制权 TTL 内重连，不保存凭据或跨 generation Chrome Profile。旧 raw TCP endpoint 的
> 诊断失败已修正，修正镜像已重建；Server Proxy→websockify→RFB 与同一 Sandbox Playwright 合成页完整
> Smoke 已通过。该证据仅为未配置 API key/secure runtime 的 trusted-local 功能验证，不代表 noVNC 人工
> 键鼠 UI E2E、通用认证、公网安全或跨 generation 登录恢复。
>
> v18 变更：Phase 6 Slice 4 完成 Agent 输入附件。用户文件作为 owner/Project/Session
> scoped 不可变 `AgentAttachment` 进入业务 Storage；消息幂等 hash 覆盖有序附件 ID，
> `agent-context.v2` 冻结精确版本/hash/大小/类型/名称。Runtime 在模型前事务外清空并
> 重验物化 `/workspace/inbox`，WorkspaceSnapshot 不得保存或恢复 inbox。不开放 Browser 任意
> 文件上传，Storage 孤儿 blob GC 与通用恶意文件扫描仍是已知限制。
>
> v19 变更：ADR-0012 仅取代 ADR-0011 的固定 arXiv Host allowlist，Phase 6 Slice 7 改为版本化
> `research-public-egress.v1`：Session Sandbox 可访问任意正常公网 HTTP(S)，统一拒绝 private、metadata、
> 宿主与 LAN。raw Workspace 下载不等于正式业务资源；文件带出 Sandbox 时才执行来源与文件校验。该能力
> 仍是 trusted-local 演示。Profile 只提供 L3/L4/FQDN 目标边界，不解析 HTTP method 或 Browser 业务
> 语义；平台不注册外部写 Tool 或提供凭据，但不宣称 raw Browser/Shell/MCP 协议级只读。

## 1. 文档用途

本文用于指导一个“文献综述撰写系统”从学习、设计到实现的完整过程。它不是详细编码任务清单，也不提前固定所有接口字段、数据库列、Prompt、LangGraph 节点和重试参数。

开发进入每个阶段前，应该在阶段 Spec 中进一步确定：

- 用户故事和验收场景；
- API 与 Event 契约；
- 状态机和数据模型变更；
- LangGraph State、Node、Edge 和 Checkpoint 边界；
- 失败语义、重试策略和取消点；
- 测试矩阵及学习笔记要求。

本文首先回答五个长期稳定的问题：

1. 系统要解决什么问题；
2. 系统由哪些模块构成；
3. 每个模块拥有什么状态和职责；
4. 应该按什么顺序学习和开发；
5. 达到什么条件才算完成一个阶段。

## 2. 项目目标

### 2.1 产品目标

系统面向需要阅读、整理和撰写技术文献综述的用户，围绕一个 Research Project 先完成两种核心执行模式：

1. **文献 RAG 问答**：针对项目内已经收录的文献进行有出处、可回跳原文的问答；
2. **综述 Workflow**：按照可观察、可暂停、可恢复的固定流程收集文献、提取证据、生成大纲并撰写带引用的综述。

在核心能力完成、经过评测并具备可靠运行证据后，再建设 **Research Agent Extension**：通过
`ResearchAgentRuntime` 适配边界接入基于 LangGraph 的 Deep Agents，提供绑定 Project 的持续研究
对话。Project 的论文 Chunk Index、Review Evidence Matrix 和 Artifact 为 Agent 提供精细上下文；Agent
在授权范围内分析、规划和产出可追溯结果，并逐步接入 MCP、Browser、隔离 Sandbox 与平台维护的
Research Skills。选型与会话模型分别见 ADR-0001 和 ADR-0005。

本项目不以自行重造通用 Agent Harness、浏览器自动化框架或 Sandbox 平台为目标。Agent SDK 负责通用 Agent Loop、上下文管理和环境操作；本系统负责研究领域状态、可靠执行、权限、证据追溯和用户可见历史。

系统的核心价值不是生成更多文本，而是：

> 让每个重要结论能够追溯到具体文献和证据，并让长时间运行的模型、检索和工具任务可恢复、可取消、可诊断。

### 2.2 学习目标

项目应帮助开发者掌握并能够在面试中独立解释：

- 异步 HTTP 服务和后台 Worker 的职责区别；
- Task、Run、Attempt、Step、Job、Session、Thread 和 Checkpoint 的区别；
- 状态机、Event Log 和当前状态投影的关系；
- 至少一次投递、幂等、重试、取消、Lease 和故障恢复；
- PostgreSQL 事务、锁、唯一约束和并发更新；
- SSE 实时推送、断线重连和事件重放；
- LangGraph 状态图、Checkpoint、Interrupt 和 Durable Execution；
- RAG 的文档解析、切分、检索、重排、上下文构建和评测；
- 引用、证据和生成文本之间的可追溯关系；
- 外部 Agent Runtime 与业务 Run、Event、Artifact、权限和恢复语义的集成边界；
- Browser、Tool 和 Sandbox 的权限、预算、副作用与 Prompt Injection 风险；
- 日志、指标、Trace 和业务 Event 各自解决什么问题。

### 2.3 简历项目目标

完成后的项目应该能演示以下完整链路：

```text
上传或从 arXiv 检索文献
  → 异步解析与索引
  → 有引用的文献问答
  → 创建长时间综述任务
  → 查看步骤、事件、失败与重试
  → 人工确认大纲
  → 恢复工作流
  → 生成带引用的 Markdown/DOCX 和图表
```

项目介绍应强调可靠性、证据追溯和工程边界，不应宣称自动生成的内容等同于经过专家审查的系统性文献综述。

## 3. 项目边界

### 3.1 Demo-ready Core Research Backend v1 范围

Demo-ready Core Research Backend v1 需要完成：

- Research Project 和文献库；
- PDF 上传、解析、切分和向量化；
- Phase 3 使用 arXiv API 检索、下载并自动导入论文；
- 基于项目文献库的 RAG 对话；
- Evidence 和 Citation 可追溯；
- 固定的文献综述 LangGraph Workflow；
- Workflow 暂停、恢复和人工确认；
- Markdown、图片等 Artifact 管理；
- 通用 Run、Attempt、Step 和 Event 模型；
- ARQ 后台 Worker、重试和协作式取消；
- SSE 实时事件与断线重放；
- PostgreSQL/Valkey Compose 加宿主 API、Worker、Web 的可复现本地开发启动；
- 完全离线的 Fake Parser/Embedding/Chat/arXiv 演示与显式 Real 模式；
- JSON 日志、Correlation ID、小型 Prometheus Metrics、可靠性矩阵和实际评测/性能基线；
- 关键行为的自动测试、故障注入和学习笔记。

总体架构需要为后续 `ResearchAgentRuntime` Adapter 保留清晰边界，但 Demo-ready Core v1 不提前实现
未验证的通用 Agent、Tool Registry、浏览器或 Sandbox 抽象。

### 3.2 明确非目标

首版不实现：

- Dify 式通用 App Builder；
- 可视化 Workflow Canvas；
- 用户自定义任意 DAG Node；
- 插件市场；
- 多 Agent、Swarm、辩论或动态 Agent 团队；
- 自行实现通用 Agent Loop、上下文压缩、浏览器自动化框架或 Sandbox 平台；
- Demo-ready Core v1 中的开放互联网浏览、非固定学术 API 自动下载和任意代码执行；
- 任意 Shell 或直接访问宿主机的 Python；
- 自动抓取付费墙后的论文全文；
- Kubernetes、多地域和高可用；
- Kafka、Temporal、Elasticsearch、Qdrant 或 Milvus；
- 企业 SSO、复杂 RBAC 和计费系统；
- 公网身份认证、自动备份恢复、永久删除/Storage GC、OpenTelemetry 平台和生产 SLA；
- 自动替代研究者完成学术事实审查；
- 未经人工确认直接发表或提交生成结果。

### 3.3 范围控制原则

每增加一个功能，应回答：

1. 它是否直接改善文献检索、证据追溯、综述生成或可靠执行；
2. 它是否能形成明确的用户可见行为；
3. 当前阶段是否具备测试和解释它的能力；
4. 不实现它是否会阻塞当前阶段验收。

如果答案主要是“以后可能用到”，则不应在当前阶段实现。

## 4. 核心概念

### 4.1 Research Project

Research Project 是用户组织一次研究主题的顶层资源，拥有：

- 研究问题和说明；
- 文献库；
- RAG Conversations；
- Review Workflow Runs；
- 后续扩展的 Agent Sessions、Turn Runs 和 Messages；
- Evidence、Citation 和 Artifact。

所有用户可见查询必须在 Project 和用户所有权范围内执行。

### 4.2 Paper 与 Paper Version

Paper 表示稳定的学术作品身份和书目信息，例如 DOI、标题、作者、年份和来源。

Paper Version 表示系统实际处理过的一份全文版本。重新上传、重新解析或更换解析器时，不应悄悄覆盖旧版本。Evidence 必须指向具体 Paper Version，避免文档重新解析后页码和 Chunk 变化导致引用失效。

### 4.3 Conversation 与 Agent Session

RAG Conversation 保存轻量问答历史，并继续作为独立产品模式。Research Agent 使用单独的
`AgentSession` 保存 Project 范围内的持续交互；一条用户消息创建一个 `AgentTurnRun`，它是可执行、
可取消和可恢复的业务 Run。一个 Turn 覆盖从用户消息到最终 Assistant Message 的完整产品交互，内部
可以包含多次 LLM、Tool、Observation 和 Interrupt Step；这些内部 Step 不各自创建 Turn Run。每轮开始
时固化 `ContextSnapshot` 与 `PolicySnapshot`。

`AgentSession : SDK Thread = 1:1`，`AgentTurnRun : SDK Execution = 1:1`。SDK Thread、Checkpoint、Store
和 Workspace 都是 Runtime 内部状态，不能替代 PostgreSQL 中的 Session、Message、Run、权限、Event、
Evidence 和 Artifact。RAG 与 Agent 可复用 Retriever、Evidence 和 Citation 能力，但不共用同一会话
模型或生命周期。

PostgreSQL `AgentMessage` 保存 UI、权限、稳定消息 ID、审计和灾难恢复所需的产品对话事实；Deep Agents
Message、摘要和 Checkpoint 保存模型工作上下文。正常后续 Turn 复用同一 SDK Thread，只追加本轮新用户
消息，不在每轮从业务表重放完整历史。`ContextSnapshot` 是当前 Turn 的授权与版本 Manifest，其中产品
消息 sequence 仅作为审计、对账和 Runtime 损坏后的受控重建水位，不保存 Runtime Message、摘要或
Graph State。

### 4.4 Run、Attempt、Step 与 Job

```text
Run
├─ 用户能够查询和取消的业务执行
├─ 有稳定的状态、输入、结果、预算和事件
│
├─ Attempt 1
│  ├─ 一次实际执行尝试
│  └─ 可能因临时故障失败
│
└─ Attempt 2
   └─ 重试后新的执行尝试

Step
└─ Run 内一个可观察的业务阶段或 Workflow Node

Job
└─ Queue 用于通知 Worker 执行某个 Run 的投递记录
```

公开 API 不应暴露 ARQ Job 作为业务 Run。Queue 只传递稳定 ID，业务状态以 PostgreSQL 为准。

### 4.5 LangGraph Thread 与 Checkpoint

LangGraph Thread 标识一条可继续的图执行历史，Checkpoint 保存图在某个执行点的状态。它们属于 Agent/Workflow Runtime，不替代业务 Run、Event、权限和 Artifact 数据。

### 4.6 Evidence、Claim 与 Citation

- **Evidence**：来自特定 Paper Version、页码或章节的可定位证据；
- **Claim**：生成回答或综述中的一个可验证陈述；
- **Citation**：Claim 与一个或多个 Evidence/Paper 之间的引用关系。

系统应优先生成结构化 Claim/Evidence 关系，再渲染为 `[1]`、作者—年份或其他引用样式，而不是让模型自由编造引用字符串。

### 4.7 Artifact

Artifact 是一次 Run 产生的持久文件或结构化产物，例如：

- Markdown 综述；
- DOCX；
- BibTeX 或 CSL JSON；
- PNG/SVG 图表；
- Evidence Matrix；
- 检索结果清单；
- 运行报告。

## 5. 总体架构

### 5.1 物理部署

Demo-ready Core v1 采用单仓库、单节点、少量进程的模块化单体：

```text
                         ┌──────────────────┐
                         │    React Web     │
                         └────────┬─────────┘
                                  │ REST + SSE
                                  ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI API Process                   │
│ Project / Library / Conversation / Run / Event / Auth   │
└──────────────┬───────────────────────┬───────────────────┘
               │                       │ enqueue run_id
               │                       ▼
               │               ┌──────────────────┐
               │               │  Valkey + ARQ    │
               │               └────────┬─────────┘
               │                        │
               │                        ▼
               │               ┌──────────────────┐
               │               │ Python Worker    │
               │               │ ingestion / rag  │
               │               │ workflow         │
               │               └────────┬─────────┘
               │                        │
               ▼                        ▼
┌──────────────────────────┐   ┌───────────────────────────┐
│ PostgreSQL + pgvector    │   │ External Adapters         │
│ business state / events │   │ LLM / arXiv / parser      │
│ vectors / checkpoints   │   │ model / academic API      │
└──────────────────────────┘   └───────────────────────────┘
               │
               ▼
┌──────────────────────────┐
│ Artifact Storage         │
│ local → future S3        │
└──────────────────────────┘
```

Demo-ready Core v1 的默认本地启动边界为：

```text
Docker Compose: postgres / valkey
宿主进程:      api / worker / web
共享 Storage:  API 与 Worker 使用同一个显式 AGENT_STORAGE_ROOT
```

开发者需预装 Python、uv、Node.js/npm 和 Docker Compose。Phase 4 不要求把 Web/API/Worker 制作为完整
部署镜像，也不承诺公网服务器拓扑。Agent Runtime、Sandbox、Caddy 和可观测性后端不属于
Demo-ready Core v1；Core 也不要求部署 Agent Server 或 Sandbox。

Agent Extension 的部署边界为：

```text
AgentSession / Message / AgentTurnRun / Snapshot (本系统)
Event / Permission / Evidence / Artifact      (本系统)
                         │
                         ▼
              ResearchAgentRuntime Port
                         │
                         ▼
 SDK Thread / Execution / Checkpoint / Isolated Workspace
```

无论 Runtime 在 Worker 进程内还是独立部署，PostgreSQL 中的 Session、Message、业务 Run、Event、权限、
Evidence 和 Artifact 仍是产品事实来源。

### 5.2 逻辑分层

```text
Transport
  FastAPI routes / SSE / request schemas
        ↓
Application
  use cases / transaction orchestration / authorization
        ↓
Domain
  state machines / invariants / policies / value objects
        ↓
Ports
  repositories / queue / model / parser / storage / agent runtime
        ↓
Adapters
  PostgreSQL / ARQ / LangGraph / HTTPX2 / Docling / filesystem
  future Deep Agents / MCP / sandbox adapters
```

关键规则：

- Route 只处理 HTTP、认证上下文和输入输出映射；
- Route 不直接写 SQL、调用 LLM 或执行 LangGraph；
- Domain 不依赖 FastAPI、SQLAlchemy、ARQ 或 Provider SDK；
- Application Service 组织用例和事务；
- Adapter 实现外部技术细节；
- 外部网络调用不得发生在数据库事务中；
- 业务状态转换和对应 Event 必须在同一个短事务中提交。

## 6. 技术基线

| 领域 | 初始选择 | 作用 |
|---|---|---|
| Python | CPython 3.13、uv | 统一 API、Worker 和数据处理开发体验 |
| API | FastAPI、Pydantic v2、Uvicorn | REST、上传、SSE、边界校验 |
| Workflow | LangGraph 1.2.x | 固定综述图的 State、Checkpoint、Interrupt、Resume |
| Agent Extension | Deep Agents（Phase 5 起接入） | 基于 LangGraph，经 `ResearchAgentRuntime` 隔离 |
| Sandbox | OpenSandbox（Phase 5 Slice 7 起验证） | Session 专属远程可执行 Workspace；不替代平台权限与业务事实 |
| 数据访问 | SQLAlchemy 2.0 Async、psycopg 3 | ORM 映射、显式事务和 PostgreSQL 异步访问 |
| 迁移 | Alembic | 业务 Schema 版本化 |
| 数据库 | PostgreSQL 18、pgvector 0.8.2 | 业务状态、事件、全文检索、向量和 Checkpoint |
| 后台任务 | ARQ 0.28、Valkey 9.x | 异步 Worker、Job 投递和实时通知 |
| PDF 解析 | Docling，pypdf 回退 | 文档结构、页码、表格、阅读顺序 |
| 外部 HTTP | HTTPX2 | 模型和学术 API 调用 |
| 模型适配 | langchain-core、langchain-openai | 首个 OpenAI-compatible Chat/Embedding Adapter |
| 文件存储 | 本地 Storage Adapter | PDF、解析结果和 Artifact；未来替换 S3 |
| 前端 | React 19、Vite、TypeScript strict | 独立 Web UI |
| UI 数据 | TanStack Query、原生 EventSource | 服务端状态、SSE 和恢复 |
| 样式 | Tailwind CSS、shadcn/ui | 控制 UI 开发成本 |
| 测试 | pytest、pytest-asyncio、pytest-httpx2（基于 RESPX）、Hypothesis、Testcontainers | 行为、并发、边界和故障验证 |
| 质量 | Ruff、Pyright、ESLint、tsc | 格式、静态检查和类型约束 |
| 部署 | Docker Compose | 单机可复现环境 |

依赖必须在锁文件中固定精确版本。阶段开发期间不进行无关框架升级。

## 7. 模块划分

### 7.1 Identity 与 Access

负责：

- 用户身份和 Web Session；
- Project 所有权校验；
- Personal API Key 扩展边界；
- 跨用户、跨 Project 访问隔离。

不负责业务 Run 状态和 Agent 权限判断。后续 Agent Tool 权限由独立 Execution Policy 负责。

### 7.2 Project

负责 Research Project 的创建、更新、归档和成员边界。首版可以是单用户，但数据模型和 Repository 查询应从开始保留 `owner_id`。

### 7.3 Literature Library

负责：

- Paper 元数据；
- DOI、OpenAlex ID 等外部标识；
- 去重和版本管理；
- Paper 与 Project 的收录关系；
- 用户纳入、排除和标签；
- PDF 与 Paper Version 关联。

同一篇 Paper 可以被多个 Project 收录，但用户私有文件和标注必须保持所有权隔离。

### 7.4 Document Ingestion

负责：

- 文件校验和内容哈希；
- PDF 异步解析；
- 标题、段落、表格和页码等结构化元素；
- 结构感知切分；
- Embedding；
- 索引版本；
- 失败、重试和重新解析。

该模块是第一个练习通用 Run/Event/Worker 能力的真实垂直切片。

### 7.5 Retrieval

负责：

- Query 规范化；
- Project/Paper 过滤；
- pgvector 语义检索；
- PostgreSQL 全文检索；
- Hybrid 合并；
- 文献多样性和上下文预算；
- Evidence 候选生成。

初期使用精确向量检索。只有基准数据证明需要时才加入 HNSW 和独立重排模型。

### 7.6 RAG Conversation

负责：

- 对话和 Message；
- 用户问题到 Retrieval 的调用；
- 有证据约束的回答生成；
- 引用验证；
- 流式输出；
- “证据不足”行为。

RAG Conversation 不负责后台队列和文档解析。

### 7.7 Run Control

这是 RAG、Workflow 和后续 Agent Extension 共享的后端核心，负责：

- Run 状态机；
- Attempt 和 Step；
- Event Log；
- 幂等提交；
- 取消请求；
- 重试分类；
- 预算和使用量；
- Worker 所有权与故障恢复；
- 用户可见错误码。

Run Control 不理解具体 Prompt，但必须知道执行是否还能继续。

### 7.8 Queue 与 Worker

负责：

- 将 `run_id` 投递给 ARQ；
- Worker 领取和执行；
- 控制并发；
- Worker 启停；
- 临时基础设施错误的 Run 级重试；
- 定期恢复异常 Run。

ARQ 的 Job Result 不作为业务结果；Worker 必须把结果写入 PostgreSQL。

### 7.9 Event 与 SSE

负责：

- 版本化 Event Envelope；
- Run 内严格递增的 Sequence；
- 历史重放；
- Valkey Pub/Sub 实时唤醒；
- SSE 心跳、重连和终态关闭；
- Event Payload 脱敏。

Valkey 通知可以丢失，PostgreSQL Event 不可以丢失。

### 7.10 Model Gateway

负责：

- Chat Model 和 Embedding Provider 接口；
- Provider 错误归一化；
- 超时、并发和短期重试；
- Token Usage；
- Structured Output；
- Fake Provider；
- 模型和 Prompt 版本记录。

首版只实现一个 OpenAI-compatible Adapter，不建设通用模型平台。

### 7.11 Review Workflow

负责固定的文献综述 LangGraph：

```text
定义研究问题
  → 制定检索策略
  → 从 arXiv 搜索并自动导入前 N 篇
  → 等待全文解析与索引
  → 提取 Evidence Matrix
  → 生成结构化大纲
  → 人工确认大纲
  → 分章节撰写
  → 引用和一致性校验
  → 导出 Artifact
```

首版图结构由代码定义并版本化，不提供用户自定义 Canvas。

生产 `ReviewExecutor` 将 Search Strategy、arXiv 检索/下载、子 Run 创建和
`WAITING_DEPENDENCY` 保留在 LangGraph 外的业务编排层；这是释放 Worker 并由数据库 Reconciler
恢复父 Run 所需的边界，不是第二种 Interrupt。持久 `review.v1` 图从唯一 HITL Outline 开始，固定
连接章节写作、引用校验、一致性、Artifact 导出和 Finalize。图外服务重放时先读取稳定 Step/Output/
Source，不能重复调用模型/arXiv 或重复创建业务副作用。

检索策略由固定 `search_strategy.v1` 模型调用生成，输出必须满足 `search-strategy.v1` 严格 Schema、
64 KiB 大小限制、arXiv 字段 allowlist 和 3–6 个唯一维度。它不使用 repair；非法输出稳定失败。

Phase 3 的 Evidence Matrix 节点固定从当前 Review Run 的 `search-strategy.v1` Output 加载 3–6 个
维度。短论文按序提供不超过 12,000 estimated tokens 的全部 Chunk；长论文对每个维度使用 Phase 2
Retriever 的精确 `(paper_id, version_id)` 范围取 top 5，再合并、去重、排序并限制为 16,000
tokens；每篇先使用一次 `review-evidence-extraction.v1` 正常模型调用，输出非法时最多追加一次修复
调用。Chunk 在进入 Prompt 前先固化为
属于当前 Review Run 的 Evidence，模型结果只能引用这些持久 ID。

### 7.12 Research Agent Runtime Integration（后续扩展）

本模块不自行实现通用 Agent Loop，而是在 Core Research Backend 完成后通过 `ResearchAgentRuntime` Port 接入 ADR 已选定的 Deep Agents。

本系统负责：

- 创建和授权绑定 owner 与 Project 的 `AgentSession`，并持久化业务 Message；
- 为每条用户消息创建一个 `AgentTurnRun`，保存 Attempt、取消意图、预算、审批、Event 和最终结果；
- 在每轮开始时固化 `ContextSnapshot` 与 `PolicySnapshot`，以最小授权方式提供 Project、Paper、
  Evidence Matrix、Chunk 和 Artifact；
- 把 SDK Thread 绑定到稳定 Session ID，把 SDK Execution、Workspace/Sandbox Lease 和 Event Cursor
  绑定到稳定 Turn Run ID；
- 管理 Session 逻辑 Workspace、跨 Turn `WorkspaceSnapshot` 与 AgentSession/SDK Thread 范围的短 TTL
  Sandbox Lease；
- 归一化 SDK 事件、错误、Usage 和 Artifact 提交；
- 在 Runtime 重试、恢复或响应丢失时进行幂等对账。

外部 Agent Runtime 负责：

- 通过 `create_deep_agent` 原生 Harness 提供通用 Agent Loop、规划、Runtime Message、Checkpoint、
  上下文压缩和大型结果文件卸载；平台不以 `create_agent` 加自研中间件复制这些能力；
- Tool 选择和运行时内部 Observation；
- 通过 OpenSandbox Backend 提供文件、Sandbox `execute` 和 Workspace 内部操作，并通过转换后的
  Playwright/Search MCP Tool 使用 Browser 与搜索能力；
- SDK 自身的流式事件与执行上下文。

SDK Thread、Checkpoint、Store、Workspace 和 Event 不能替代 PostgreSQL 中的 Session、Message、Run、
权限、Event、Evidence 和 Artifact。同一 Session 同时最多允许一个活动 Turn；ARQ、本系统 Run Control
与 SDK Runtime 之间必须明确重试、取消和恢复所有权，避免并发轮次破坏上下文或多层自动重试相乘。
正常后续 Turn 只把新增用户消息交给同一 SDK Thread；完整产品历史只在 Runtime binding generation
损坏或迁移时作为受控重建来源。

ADR-0007 进一步固定每个 AgentSession/SDK Thread 一个短 TTL OpenSandbox Lease，跨 Turn 复用但不跨
owner/Session 共享。OpenSandbox 作为 `CompositeBackend` 默认 Backend，使文件工具和 `execute` 操作同一
物理 Workspace；`/conversation_history/`、`/large_tool_results/` 等 Runtime 内部路径仍路由
`StateBackend`。Lease、endpoint 和 Provider SDK 类型只存在于 Adapter/基础设施层，Sandbox 丢失后从
平台 `WorkspaceSnapshot` 与授权 Artifact 重建。

ADR-0008 进一步要求通过 `langchain-mcp-adapters` 把平台注册的 MCP 转换为 LangChain Tool；固定版本
Playwright MCP 和需要本地进程的 Search MCP 运行在 Session OpenSandbox，用户只选择 Catalog 条目和
安全参数。Skills 使用 Deep Agents 原生 Backend 加载；虚拟 `/skills/` 路由平台管理的只读 Backend，
支持平台安装和 owner-scoped 声明式内容，但不能被 Sandbox `execute` 改写或扩大权限。

### 7.13 Agent Tool 与 Execution Policy（后续扩展）

Agent 扩展阶段负责：

- Runtime 可见工具或能力的版本、说明和输入 Schema；
- 用户、Project、Run 和 Policy Context；
- Tool 或外部副作用的幂等键；
- 超时、网络、输出大小和资源限制；
- ToolExecution 或等价审计记录；
- 危险操作和下载前审批；
- Sandbox 与 Artifact Workspace 边界。
- 平台安装 Skills 的 allowlist、版本、能力声明和审计，以及 owner-scoped 声明式 Markdown/文本 Skill
  的只读物化与内容哈希；用户不能上传可执行脚本、二进制或动态依赖。API 不提供独立 Secret 字段或注入
  机制，但普通文本 Secret 扫描与内容审核仍属于 Phase 6，用户不得在声明式文本中提交 Secret。
- 平台安装并固定版本的 MCP Catalog、owner/Session Profile 选择、名称/Schema/版本哈希校验和调用
  interceptor；用户只能填写 Catalog 声明的非敏感安全参数，不能提交 endpoint、transport、command、
  env、包版本、认证信息、Sandbox 镜像或网络配置。

Demo-ready Core v1 不提前建设通用 Tool Registry。RAG 和固定 Workflow 直接通过明确的应用 Port 调用
领域能力；只有 Agent SDK 集成验证证明需要统一 Tool 契约后，才在阶段 Spec 中确定具体模型。

### 7.14 Evidence 与 Citation

负责：

- Evidence 的文献版本、页码、章节和文本定位；
- Claim 与 Evidence 绑定；
- Citation 渲染；
- DOI/书目信息校验；
- 无来源 Claim 检测；
- 引用失效检测；
- Evidence Matrix 导出。

这是本项目区别于普通聊天 Agent 的核心领域模块。

### 7.15 Artifact

负责：

- 文件元数据和内容哈希；
- Storage Key；
- Run/Project 所有权；
- 下载和删除；
- Markdown、图片和后续 DOCX 导出；
- 临时 Workspace 到持久 Artifact 的提交。

WorkspaceSnapshot 与 Artifact 必须区分：前者保存 Agent 跨 Turn 继续工作所需的内部研究笔记、中间文件
和 Manifest，不默认对用户展示；后者是经过平台校验、具有业务所有权且可查看或下载的正式产物。
临时文件可以随 Sandbox Lease 丢弃。

Phase 6 的 Agent 文件交付进一步区分：用户输入使用业务 `AgentAttachment` ID，在事务外物化到
`/workspace/inbox/`；Agent 输出必须显式调用 `submit_artifact`，且只允许
`/workspace/outputs/` 的普通文件进入 Candidate。Slice 2 已实现 `STAGED → VALIDATED → COMMITTED` 与
`STAGED → REJECTED`：平台重新校验路径、大小、MIME/magic、哈希和当前 Runtime/Sandbox fence，事务外
写入内容寻址 staging Storage，只有 Turn 业务成功才发布独立 AgentArtifact。现有 Review Artifact 聚合
继续绑定 ReviewRun，不为 Agent 增量进行高风险泛化；两者复用 Storage Port，但保持独立业务聚合和授权
查询。首版单文件上限 10 MiB，支持 PNG/JPEG/SVG/PDF/CSV/Markdown/text/JSON；SVG 只下载不内嵌。
静态 symlink/device 拒绝与传输后 size/hash 校验不被宣称为无 TOCTOU 的生产级恶意文件扫描。

### 7.16 Observability

负责：

- 结构化日志；
- Request、Run、Attempt、Step 和 Tool Correlation；
- Metrics；
- Trace；
- 故障诊断入口；
- 敏感字段脱敏。

业务 Event 面向产品历史；日志、指标和 Trace 面向运行诊断，不能互相替代。

## 8. 核心模式与后续 Agent 扩展的运行方式

### 8.1 RAG 模式

```text
用户发送问题
  → 创建或更新 Conversation
  → 检索 Project 文献
  → 构造 Evidence Context
  → 模型生成结构化 Answer + Evidence IDs
  → Citation Validator
  → 流式返回回答
  → 持久化 Message 和 Citation
```

RAG 问答一般是交互式短执行，可以由 API 进程流式处理；如果请求涉及长时间检索、批量分析或大输出，则创建后台 Run。两条路径必须复用相同 Retrieval 和 Citation 逻辑。

### 8.2 Workflow 模式

```text
POST Review Run
  → Run=QUEUED + Event
  → enqueue run_id
  → Worker 启动 LangGraph
  → 每个阶段更新 Step/Event
  → Interrupt 等待人工确认
  → 用户提交 Decision
  → 新 Job 恢复同一 Thread
  → 校验并导出 Artifact
  → Run=SUCCEEDED
```

Workflow 是最主要的长时间任务，必须支持：

- HTTP 断开后继续执行；
- Worker 重启后从 Checkpoint 恢复；
- 已完成节点不因恢复被无意义重复执行；
- 外部副作用即使重复执行也不会产生重复结果；
- 用户可以查询、取消和恢复；
- 每个阶段有可理解的状态和失败原因。

### 8.3 Research Agent Extension（后续）

```text
创建绑定 Project 的 AgentSession
  → 用户消息创建 AgentTurnRun + ContextSnapshot + PolicySnapshot + Event
  → Worker 通过 ResearchAgentRuntime Adapter 启动或恢复对应 SDK Execution
  → Runtime 在 Session Thread、授权 Project Context 与隔离 Workspace 中执行
  → SDK Event/Usage/Approval 被筛选并归一化为业务 Event
  → Assistant Message、Evidence 引用和候选 Artifact 持久化
  → Turn Run 完成，下一条消息继续同一 AgentSession
```

首个用户故事限定为：用户创建绑定 Project 的 Agent Session，连续进行两轮研究对话；Agent 第一轮
读取 Project Paper Chunk Index 和一个明确选择的 Review Evidence Matrix，形成可追溯分析，第二轮基于
同一 Session 上下文深化问题并生成一个小型候选 Artifact。该切片先用 Fake Runtime 验证业务包装、
事件、取消和恢复；MCP、Browser、Sandbox 与平台 Skills 在后续切片逐项启用，而不是首个切片的前置条件。

后续允许的能力包括发现公开项目页、仓库、数据集和补充材料，以及在 Session 专属 OpenSandbox 中使用
固定 Python 依赖执行研究数据处理和绘图。
首版 Agent 不绕过登录、付费墙或 CAPTCHA，不自动提交或发布内容。

Agent Runtime 比固定 Workflow 更开放，因此必须更严格限制：

- 可见工具、版本化网络 Profile 和凭据集合；
- 最大步骤；
- 单 Tool 和总墙钟时间；
- Token 和费用；
- Tool 输出大小；
- 重复或无进展循环；
- Sandbox `execute`、版本化网络 Profile、资源和 Secret 隔离；公共网络启用时必须验证覆盖全部进程的
  统一 egress 与 private/metadata/宿主/LAN 拒绝；
- 下载文件的大小、类型、哈希和隔离；
- 来自网页、论文和仓库内容的 Prompt Injection；
- 人工审批点。

业务 Event 只保存产品历史和必要摘要，不能原样持久化 SDK 的完整思考过程、网页正文、终端输出或敏感 Tool 参数。

## 9. 状态与持久化原则

### 9.1 业务事实来源

PostgreSQL 保存：

- 用户和 Project；
- Paper、文献版本和索引元数据；
- Conversation 和 Message；
- Run、Attempt、Step、Event；
- Evidence、Citation；
- Agent 扩展的 ToolExecution 或等价审计记录；
- Artifact 元数据；
- Token/费用使用；
- 幂等记录。

### 9.2 LangGraph Checkpoint

LangGraph Checkpoint 保存：

- 当前图位置；
- 小型结构化 State；
- Node/Task 已完成结果；
- Interrupt 和 Resume 所需数据。

固定 Review Workflow 使用 `review.v1:review-run:{review_run_id}` 作为稳定、版本化 Thread 映射；
LangGraph 根图的空 checkpoint namespace 保留给其子图内部命名。首次执行提交完整小型 State；崩溃恢复对同一 Thread 调用
`ainvoke(None)`，不能再次提交完整输入开启新一轮执行。PostgreSQL checkpoint schema 由 Alembic
管理，Runtime 不在 Worker 启动时隐式建表；反序列化禁止 pickle 和任意模块 allowlist。
执行器必须先明确查询 Thread 是否存在 checkpoint：只有“不存在”才能 `start(initial_state)`；读取
失败、损坏或非法 State 不能被 broad exception 当作首次执行覆盖。

PDF、图片、全文、表格、大型工具输出和最终文档必须存储在业务数据库或 Artifact Storage 中，Graph State 只保存引用。

### 9.3 Valkey

Valkey 保存：

- ARQ Job；
- 延迟重试信息；
- Event 实时通知；
- 可丢失的短期缓存或限流状态。

系统不应把 Valkey 中的数据当作永久业务记录。

### 9.4 文件存储

首版本地存储，代码只依赖 `ArtifactStorage` 接口。文件路径不能成为公开 API，也不能由用户输入直接拼接。

## 10. Run、可靠性与事件模型

### 10.1 建议状态机

```text
QUEUED → RUNNING → SUCCEEDED
              ├→ FAILED
              ├→ RETRY_WAIT → QUEUED
              ├→ WAITING_INPUT → QUEUED
              ├→ WAITING_DEPENDENCY → QUEUED | FAILED
              └→ CANCEL_REQUESTED → CANCELLED

QUEUED → CANCELLED
WAITING_INPUT → CANCELLED
WAITING_DEPENDENCY → CANCELLED
```

最终状态为 `SUCCEEDED`、`FAILED`、`CANCELLED`。详细合法转换、并发优先级和数据库实现必须在 Run Control 阶段 Spec 中确定。

等待人工输入或子依赖时，当前 Worker Attempt 以 `PAUSED` 正常结束并释放执行资源。恢复会创建新的 Attempt。Outbox 沿用“一条 Run 一条可重置投递记录”：正常 Resume 使用 `schedule_again()` 将 `DISPATCHED` 重置为 `PENDING`，不增加失败重试计数；业务 Run 转为 `QUEUED`、原因 Event 和 Outbox 重置在同一事务提交。Event 记录业务时间线，Attempt 记录 Worker 执行历史，本阶段不增加完整队列投递历史表。

Review 论文依赖由独立的数据库 Reconciler 汇总：指定 PaperVersion 存在 ready ChunkSet 才算可用，
并等待全部来源进入就绪或失败终态后再固定 Evidence 集。部分失败可继续；全部不可用以
`no_reviewable_papers` 终止。该业务终止不消耗 Worker 失败重试预算。

`CANCEL_REQUESTED` 优先由存活 Worker 协作收尾；若 Worker 随后崩溃，最新 Attempt lease 过期后，
Reconciler 在同一持锁事务将 Run/Attempt 收敛为 `CANCELLED` 并写 `run_cancelled`。取消恢复不进入
失败预算，也不重置 Outbox。

### 10.2 Event 类别

最低需要覆盖：

- Run 生命周期；
- Attempt 生命周期；
- Step/Node 生命周期；
- 文档解析和索引进度；
- 模型请求完成和 Usage；
- Retrieval 摘要；
- Agent 扩展的 Runtime/Tool Call 生命周期；
- Artifact 创建；
- 等待和完成人工输入或子依赖；
- 重试、取消和错误。

Event Payload 只保存前端和审计需要的信息，不保存完整 Prompt、密钥、PDF 全文或敏感 Tool 参数。

### 10.3 至少一次与幂等

系统假设 Job 和外部调用可能重复。幂等至少分为：

1. API 提交幂等；
2. Job 执行幂等；
3. LangGraph Node/Task 幂等；
4. Agent 扩展的 Tool/Browser 副作用幂等；
5. Artifact 提交幂等；
6. 文献和 Embedding 去重。

不得宣称实现严格的分布式 Exactly Once。应通过唯一约束、状态条件更新、内容哈希和幂等键获得业务上的 Effectively Once。

### 10.4 重试层次

重试分层：

- HTTP/Provider 层：短暂网络错误和限流；
- LangGraph Node 层：单步骤可恢复错误；
- ARQ Run 层：Worker 或基础设施级失败；
- 用户层：修正输入后显式恢复或重新运行。

同一个错误只应由一层主导重试，避免重试次数相乘。

### 10.5 取消

取消采用持久化的协作式协议：

```text
用户请求取消
  → 写 cancel_requested + Event
  → 尝试中止 Queue/HTTP
  → Worker 在安全点检查
  → 停止下一次模型或工具调用
  → 清理临时资源
  → 写 CANCELLED + Event
```

“请求取消”不等于“已经取消”。外部请求被本地取消也不证明远端没有执行副作用。

## 11. 引用可信性设计

### 11.1 基本约束

- 模型只能引用当前 Run 可见的 Paper/Evidence；
- Citation 必须指向稳定 Paper ID 和具体 Paper Version；
- Evidence 尽可能保存页码、章节和定位文本；
- 引用渲染前必须验证 Evidence 存在且属于当前 Project；
- 没有充分证据时，系统必须允许回答“不确定”或“证据不足”；
- 模型生成的 DOI、作者和年份不能未经外部元数据或本地文献库校验直接采用。

### 11.2 综述生成策略

推荐采用 Evidence-first：

```text
Paper
  → Evidence Extraction
  → Evidence Matrix
  → Topic/Outline
  → Section Claims
  → Claim-Evidence Binding
  → Draft Rendering
  → Citation Validation
```

不要直接把所有 Chunk 拼成上下文后要求模型一次性生成完整综述。Phase 3 对短论文按序提供全部 Chunk；长论文按分析维度检索后合并、去重并限额，每篇论文一次正常调用提取全部维度，仅在输出非法时最多追加一次修复。章节写作只读取当前章节对应的 Matrix 行及其 Evidence。

Matrix 结果必须经过确定性 Validator：严格 Schema、完整维度、状态组合，以及 Evidence 的
owner/Project/Review Run/Paper/PaperVersion 闭包；证据不足是合法输出。首次非法只允许在相同受控
上下文中修复一次。Evidence 与单篇/聚合 ReviewOutput 通过唯一约束和稳定幂等键实现 effectively
once；单篇修复后仍非法也会先持久化稳定失败 Output，后续论文临时失败导致聚合未提交时，重放不会
再次调用已永久失败论文。聚合 Output、总 Step 成功与 completed Event 同事务提交。Checkpoint 之后
发生崩溃时，节点重放先复核并返回既有聚合 Output，不重做模型副作用。

大纲生成只读取研究问题、固定分析维度、已验证 Matrix 的受控摘要和论文覆盖统计，不读取论文全文。
`outline_generate.v1` 的结构化结果经确定性 `outline.v1` Validator 后版本化保存；Request 与等待状态通过
幂等短事务推进。`review_outline` 节点在 `interrupt()` 前保持纯函数式边界。HumanInput 必须先按
owner/Project/Run、Request 版本和当前 Outline 校验并持久化，再与 Request resolve、Run 重新排队、
Event 和 Outbox 重置同事务提交。崩溃恢复使用空输入继续 checkpoint；HITL 恢复使用仅含持久 ID 的
`Command(resume=...)`，恢复后仍从业务数据库复核决定。approve/edit 固定批准的 Outline 版本，feedback
追加下一 Outline/Request 并再次暂停；第一版反馈文本有界，但轮次预算尚待 Profile 校准。

章节固定使用 `section.v1`，按批准 Outline 顺序生成。每次调用只读取当前章节维度命中的 Matrix 行、
这些行引用的 Evidence 定位与摘录、前文章节短摘要和受控术语字典；不读取整篇论文、完整 Matrix 或
前文全文。所有章节完成后复用统一 ClaimSet/Citation 表，并在 Phase 2 Validator 之上逐条校验
Matrix Paper、READY Source、PaperVersion、ParseRevision、Project/Run Evidence 闭包。ClaimSet、
Claim 和 Citation 使用数据库唯一约束上的原子 get-or-add，并在回读后比较完整稳定语义。

章节与一致性输出 token 预算分别由 Review Profile 快照的 `section_output_token_limit` 和
`consistency_output_token_limit` 控制。新建 Run 使用 `review-default.v2`，默认来源数为 3，预算为
8,000/2,000，并进入创建请求指纹；历史 `review-default.v1` Run 保留其持久化的 10 篇与
4,000/2,000 配置，缺字段的早期 v1 开发 Run 仍回退到 v1 默认值。原始模型 JSON 在 Schema 解析前
分别限制为 192/64 KiB。
章节节点还必须反查成功的 Matrix/Outline/Draft/Validate 业务 Step 闭包并固化新 Step input refs；
所有副作用提交前持锁复核 Run 仍为 RUNNING Review，取消后不得新增 Output/Event 或推进 Stage。

`consistency_check.v1` 产生小型版本化报告。术语、章节矛盾和冗余 issue 仅用于披露，不阻断导出且不
触发自动重写；调用失败、范围错误或 Schema 非法都会阻断当前执行，只有合法报告可以继续，其中
Schema 失败稳定结束 Step，模型调用和范围错误交给既有 Worker 错误分类/重试。第一版因此不把
一致性模型当作事实正确性的通用 LLM Judge。

最终导出从已验证 Section/ClaimSet/Citation/Evidence/READY ReviewSource 构建。Markdown 使用按全文
首次引用顺序分配的 `[1]` 编号，同一论文复用编号；完整引用映射保存 Paper/PaperVersion、Source、
arXiv version、Claim、Evidence 和 PDF 定位。固定生成 Markdown、Search Strategy、Source Manifest、
Evidence Matrix、Bibliography 映射、Run Summary 六类 Artifact。文件正文只进入 Storage，数据库只
保存元数据、SHA-256、大小、MIME、来源 Output 和稳定幂等键。完整引用映射只写 Bibliography
Artifact；有 256 KiB 上限的 Final Output 只保存引用计数与六类 Artifact manifest。

Artifact Storage 写入在事务外，key 由 owner/Run/content hash 稳定组成；提交前持锁复核 RUNNING
owner/Project Review。取消后不得新增 Artifact/Event/Stage；文件写入后数据库提交前崩溃只留下可
复用缓存，重放覆盖相同字节并收敛为一组业务记录。Review 的 Project-scoped Event API 负责历史游标
读取，实时断线恢复复用通用 owner-scoped Run SSE 与 `Last-Event-ID`。

### 11.3 评测维度

- Citation precision：引用是否真正支持 Claim；
- Citation completeness：重要 Claim 是否都有引用；
- Citation validity：文献、页码和 Evidence 是否存在；
- Retrieval recall：标准问题需要的 Evidence 是否被召回；
- Groundedness：回答是否超出提供证据；
- Coverage：综述是否覆盖预先定义的主题维度；
- Redundancy：章节之间是否存在明显重复。

自动指标只能作为辅助，核心样本必须人工审核。

## 12. 安全和隐私

### 12.1 文献与用户数据

- 用户上传 PDF 默认视为私有数据；
- 所有文献、Conversation、Run、Event 和 Artifact 查询必须有所有权过滤；
- 日志和 Trace 不记录完整论文、Prompt 或生成文档；
- 删除 Project 时必须定义文件、向量、Checkpoint 和 Artifact 的清理策略；
- 自动化测试使用公开许可或合成文献，不提交受限论文。

### 12.2 文件上传

需要控制：

- 文件大小；
- MIME 和文件头；
- 文件名路径穿越；
- PDF 解析超时；
- 压缩炸弹和异常对象；
- 解析 Worker 的资源上限。

### 12.3 外部 URL

Demo-ready Core v1 只调用固定学术 API，不提供任意 URL 抓取。后续 Agent 增加 Browser 或 URL Tool
时，必须考虑 SSRF、DNS 重绑定、Redirect、内网地址阻断、下载大小与类型、恶意文件、Prompt
Injection 和网络外泄。

Agent 浏览器自动路径首版只访问经过平台策略允许的资源，优先发现论文官方项目页、代码仓库、开放数据集
和补充材料。ADR-0009 允许用户本人在两个 Turn 之间操作同一 Session Chromium 完成固定页面登录；
账号、密码、Cookie 和验证码不交给 Agent 或平台存储，人工与 Agent 控制互斥，登录状态只在当前 Sandbox
generation 内 best effort 保留。该人工能力不允许自动绕过付费墙、CAPTCHA 或站点限制，也不批准对外
提交或不可逆操作；真实网站仍必须先通过 Phase 6 统一 egress 验收。ADR-0012 的 egress 只约束目标网络，
不能把这一产品策略变成 HTTP/Browser 业务语义级强制。

### 12.4 代码执行

Demo-ready Core v1 不提供任意代码执行。固定图表或导出能力应优先作为确定性的应用服务运行，并使用
结构化输入 Schema。

ADR-0007 已决定在 Phase 5 Slice 7 的 Session 专属 OpenSandbox 中向 Deep Agents 模型开放 Sandbox
`execute`，用于固定依赖的研究数据处理和绘图。它不是宿主 Shell/Python，也不把产品扩展为通用 Coding
Agent。实现前后必须验证：

- 非 root；
- 一个 AgentSession/SDK Thread 一个短 TTL Lease，不跨 owner/Session 共享；
- 独立 `/workspace`，不挂载宿主目录、数据库/Docker Socket 或 Secret；
- 默认禁网；
- Phase 5 Browser Spike 保持默认禁网，只访问 Sandbox 内合成页面；公共 Browser 域名与覆盖 Chromium、
  Python、命令行工具等全部 Sandbox 进程的统一 egress allowlist 在 Phase 6 验证；
- ADR-0012 后的 Phase 6 Slice 7 不再使用固定 Browser Host allowlist，而是以版本化 public-egress Profile
  允许正常公网并统一拒绝 private/metadata/宿主/LAN；这不改写前两项 Phase 5 历史事实；
- CPU、内存、进程、时间和输出上限；
- 固定镜像、Python/pandas/numpy/matplotlib/字体依赖，不允许动态安装包；
- 只读取显式传入的 WorkspaceSnapshot 或 Artifact；
- 只持久化 Manifest 允许的 WorkspaceSnapshot；正式 Artifact 离开 Sandbox 后重新校验；
- 每次模型/Tool 边界复核取消、Runtime lease/fence 和预算；取消后不启动新命令。

Deep Agents `permissions` 只保护其内置文件工具，不能保护 Sandbox `execute`、自定义 Tool 或 MCP。
离线命令不逐条审批；安全边界由 Sandbox 隔离、统一网络策略、Secret/宿主隔离、资源限制、Workspace
Manifest 和 Artifact 提交协议共同构成。平台不以命令字符串 allowlist 伪装为强隔离，也不向 Sandbox
注入模型、MCP 或 OpenSandbox 凭据。

## 13. API 与 Web 边界

### 13.1 API 资源类别

具体路径和 Payload 在阶段 Spec 中确定，但 API 应围绕以下资源组织：

```text
auth
projects
papers
paper-files
ingestion-runs
conversations
messages
review-runs
agent-sessions / agent-messages / agent-turn-runs
runs / attempts / steps / events
approvals or inputs
evidence / citations
artifacts
models
health / readiness / metrics
```

`agent-sessions`、`agent-messages` 和 `agent-turn-runs` 属于 Research Agent Extension，不是
Demo-ready Core v1 API 的完成条件。公开 API 只使用业务 ID，不接受 SDK Thread、Workspace、Sandbox、
原始 MCP Server 连接或网络权限配置；专用配置 API 只能引用 owner 可见的平台注册 Catalog/Skill ID。

长任务创建返回 `202 Accepted` 和稳定 `run_id`。查询和事件接口使用业务 ID，不暴露 ARQ 或 LangGraph 内部表。

### 13.2 Web 页面

Demo-ready Core v1 页面：

- Project 列表和详情；
- Literature Library；
- Paper 详情和 PDF 阅读；
- RAG Chat；
- Review List、创建和详情，包含 Stage、Sources、结构化 Outline HITL、Matrix、Sections 和 Citation；
- Artifact 查看和下载；

Research Agent 页面在业务 Session、Message、Turn 和 Event 契约稳定后加入，提供类似持续 Chat 的交互、
当前活动 Turn、审批、来源和 Artifact 展示；Browser、Workspace 与工具细节只展示筛选后的业务摘要，
不复制 SDK 自带 UI 或暴露其内部状态模型。

Phase 5 Slice 8 的最小 REST/SSE、桌面信息架构、Evidence Margin、首 Turn 前能力配置和非范围见
[`agent-chat-ui-interface-contract.md`](agent-chat-ui-interface-contract.md)。官方 Deep Agents UI 依赖直接
连接 LangGraph Deployment/Thread State，本项目不接入或代理该数据层；现有 Vite React 只消费平台业务
API。移动 Drawer、Browser/noVNC、Workspace 文件管理、fork/rewind 和候选 Artifact 正式提交均不属于
该切片。

Phase 6 按 ADR-0009/0010 增加两个平台业务 UI，而不是接入 SDK UI：右侧 Browser 面板通过 owner/Session/
generation 鉴权代理提供短时人工控制；成果区通过 AgentArtifact API 展示图片预览和稳定下载，并为消息
提供 Attachment ID 上传。Web 不接收原始 Sandbox 路径，也不看到 VNC/noVNC/CDP/MCP/OpenSandbox
endpoint；WorkspaceSnapshot 仍不是文件管理器或下载列表。Slice 2 的成果组件只消费正式 AgentArtifact
API，PNG/JPEG 可预览，其他类型展示安全元数据与下载入口；Candidate 只作为内部状态摘要且不可下载。
Browser 首版只在两个 Turn 之间接管已有 Session Sandbox，不因查看画面创建或轮换 Sandbox。一个 Session
同时只有一个 ACTIVE `BrowserControlLease` 和一个画面连接；ticket 不进入 URL 或浏览器持久存储，后端只
保存 digest；没有 ACTIVE 控制权时即为 Agent/idle，不持久化 AGENT Lease。后端在事务外经固定
websockify recipe 与 OpenSandbox Server Proxy 把 loopback VNC `5901` 桥接到平台 WebSocket，设置连接、idle、帧、总量、
总时长和周期 fence 边界。WebSocket 断线不等于业务控制结束，用户可在短 TTL 内刷新重连；结束后下一
Turn 继续复用同一 generation 的 Chromium。

Run Detail 是核心页面，应展示：

- 当前状态；
- Step Timeline；
- 实时 Event；
- 重试和错误；
- Token/成本；
- 等待人工输入；
- 取消操作；
- Evidence 和生成 Artifact。

### 13.3 前端状态规则

- TanStack Query 保存服务端查询状态；
- 本地 React State 只保存表单和交互状态；
- SSE Event 触发缓存更新或失效；
- 页面刷新必须能从 API 恢复全部重要状态；
- EventSource 断线后按 Sequence 重放；
- 不在浏览器长期保存 Provider Key。

## 14. 可观测性和诊断

### 14.1 Correlation

所有请求和后台执行应尽可能关联：

```text
request_id
trace_id           # 仅在后续阶段显式启用 Trace 时存在
project_id
run_id
attempt_id
step_id
thread_id
tool_execution_id  # 仅 Agent 扩展存在
```

高基数 ID 可以进入日志和 Trace，不能直接作为 Prometheus Label。

### 14.2 日志

Demo-ready Core v1 使用标准库 `logging` 和项目自有 JSON Formatter 输出结构化日志。API 进程通过
`contextvars` 传播 Correlation ID；Worker 以 `run_id/attempt_id` 建立自己的执行上下文，不依赖跨进程
Context。日志记录事件和诊断上下文，不记录完整 Prompt、Secret、PDF 文本和生成文档。

### 14.3 指标

Phase 4 只引入 `prometheus-client` 和小型 `/metrics`，至少覆盖：

- API 请求量和延迟；
- Queue 深度和等待时间；
- Run 成功、失败、取消和重试；
- 各 Workflow Step 延迟；
- Provider 错误和限流；
- Token/费用；
- Retrieval 延迟；
- PDF 解析失败；
- Agent Runtime、Tool 和 Sandbox 超时与拒绝（仅 Agent 扩展）。

### 14.4 Trace

Demo-ready Core v1 不引入 OpenTelemetry、Collector 或 Trace 后端。Run、Event、Attempt、Step、
ModelInvocation 和 Correlation Log 提供当前最低诊断链路。Phase 6 若因 Agent Runtime、Browser、Tool 或
Sandbox 调试需要 Trace，应在自己的阶段 Spec 中独立决策；LangSmith 或 SDK Trace 不能替代业务审计。

## 15. 建议仓库结构

```text
literature-agent/
├─ backend/
│  ├─ pyproject.toml
│  ├─ uv.lock
│  ├─ src/literature_agent/
│  │  ├─ api/                 # FastAPI、routes、schemas、SSE
│  │  ├─ application/         # use cases
│  │  ├─ domain/              # entities、state machines、policies
│  │  ├─ projects/
│  │  ├─ literature/
│  │  ├─ ingestion/
│  │  ├─ retrieval/
│  │  ├─ conversations/
│  │  ├─ runs/
│  │  ├─ events/
│  │  ├─ workflows/
│  │  ├─ agents/              # Phase 5/6 按验证结果创建
│  │  ├─ tools/               # Phase 5/6 按验证结果创建
│  │  ├─ evidence/
│  │  ├─ artifacts/
│  │  ├─ providers/
│  │  ├─ infrastructure/
│  │  └─ observability/
│  ├─ migrations/
│  └─ tests/
├─ web/
│  ├─ src/
│  ├─ tests/
│  └─ e2e/
├─ contracts/
│  ├─ openapi/
│  ├─ events/
│  └─ tools/                  # Agent Extension 契约，Demo-ready Core v1 不预建
├─ evals/
│  ├─ datasets/
│  ├─ fixtures/
│  └─ reports/
├─ data/                      # gitignored local artifacts
├─ deploy/compose/
├─ docs/
│  ├─ spec/
│  ├─ learning-journal/
│  │  ├─ phases/
│  │  ├─ modules/
│  │  └─ decisions/
│  └─ operations/
├─ AGENTS.md
├─ Makefile
└─ README.md
```

模块目录应按领域逐步创建，不要在 Phase 0 一次生成大量空目录和抽象。

## 16. 学习与开发工作方式

### 16.1 每阶段循环

```text
概念学习
  → 用自己的话写设计笔记
  → 编写阶段 Spec
  → 定义契约和不变量
  → 写失败测试
  → 实现最小垂直切片
  → 故障注入和集成测试
  → 更新证据与复盘
  → 进入下一阶段
```

### 16.2 概念学习的完成标准

学习一个概念后，不以“看完文章”为完成，而以能够回答下列问题为准：

- 它解决了什么失败场景；
- 状态归谁拥有；
- 正常流程是什么；
- 重复、超时、崩溃和取消时发生什么；
- 哪些行为需要事务或唯一约束；
- 如何测试；
- 如何观察和定位故障；
- 当前方案的限制及扩展路径是什么。

### 16.3 AI 协作规则

每次让智能体开发前，应提供或要求它读取：

- 本文；
- 当前阶段 Spec；
- 相关 ADR；
- 已完成模块笔记；
- 当前代码和测试。

智能体不得在阶段 Spec 没有确定关键状态、失败语义和验收方式时大规模编码。每次只实现一个可独立验证的垂直切片。

### 16.4 TDD 强度

严格测试优先用于：

- Run 和 Step 状态机；
- Event Sequence；
- 幂等和重复 Job；
- 重试分类；
- 取消竞争；
- LangGraph Interrupt/Resume；
- Citation Validator；
- Agent 扩展的 Runtime/Tool 契约、权限和预算；
- 用户和 Project 隔离；
- Bug 修复。

文档、纯 UI 样式和探索性 Spike 不要求机械执行 Red-Green-Refactor，但 Spike 不能直接视为生产实现。

## 17. 分阶段实施路线

### Phase 0：技术学习与隔离验证（已完成）

#### 目标

完成 Python 后端方向所需的概念学习和隔离实验，不开发文献业务功能，也不把 Notebook 实验直接视为生产实现。

#### 需要学习

- FastAPI Lifespan 和依赖注入；
- asyncio Task、取消和资源生命周期；
- SQLAlchemy AsyncSession 和事务；
- ARQ Worker 基本执行模型；
- LangGraph State/Node/Edge/Checkpoint 的最小示例；
- Docker Compose 服务依赖和健康检查。

#### 交付内容

- 八个主题的学习记录和隔离实验；
- Python 3.13、uv 和最小后端包基线；
- Phase 0 Spec 和学习复盘；
- 后续生产切片需要验证的工程验收清单。

#### 不做

- Paper、RAG、Workflow 或 Agent；
- 真实 Provider 请求；
- 通用框架抽象；
- Sandbox。

#### 阶段出口

- 开发者能解释 API、Queue、Worker、数据库和 LangGraph 的状态所有权与失败边界；
- FastAPI、asyncio、SQLAlchemy、ARQ、LangGraph 和 Compose 的关键概念已通过隔离实验学习；
- 实验记录保存在本地学习目录，不进入生产源码和 CI；
- API、Worker、迁移、Compose、测试和前端骨架在后续实际垂直切片需要时实现并记录正式测试证据。

### Phase 1：Project、文献库与可靠异步导入

#### 目标

用户创建 Project、上传 PDF，并看到文档解析和索引任务从创建到成功、失败或取消的完整过程。

本阶段开始时先落地支撑该垂直切片所需的最小生产工程基线：FastAPI 存活/就绪检查、PostgreSQL/Valkey Compose、Alembic、Worker 启动、测试和质量命令。只实现当前切片需要的部分，不重新建设独立的“大而全 Phase 0”。

#### 需要学习

- Task/Run 状态机；
- Event Log；
- PostgreSQL 事务和条件更新；
- Job 至少一次投递；
- 幂等上传和内容哈希；
- Worker 崩溃、重试和取消；
- 文件存储边界。

#### 主要模块

- Project；
- Literature Library；
- Run Control；
- Queue/Worker；
- Event/SSE；
- Artifact Storage；
- Document Ingestion。

#### 垂直切片

```text
创建 Project
  → 上传 PDF
  → 创建 Ingestion Run
  → Worker 解析
  → 写入 Paper Version 和 Elements/Chunks
  → SSE 显示进度
  → 成功、失败、重试或取消
```

#### 阶段出口

- 重复上传同一文件不会创建重复内容；
- HTTP 断开不影响解析；
- 页面刷新后可以恢复 Run 状态和事件；
- Worker 在处理中退出后，任务不会永久停留在 RUNNING；
- 状态转换和 Event 原子提交；
- 用户取消后不会启动新的解析步骤；
- 解析结果可以按 Paper、Version、Page 和 Section 查询；
- 学习笔记能解释 Job 与 Run 的区别以及为何不能依赖 Queue Result。

### Phase 2：有引用的 RAG 文献问答

#### 目标

用户针对 Project 文献提问，获得带文献和页码引用、可跳转到 Evidence 的回答。

#### 需要学习

- Embedding 和向量距离；
- Chunk、Document Element 和 Evidence 的区别；
- Hybrid Retrieval；
- 上下文预算和文献多样性；
- RAG 评测；
- 流式回答和持久 Message；
- Citation precision/completeness。

#### 主要模块

- Retrieval；
- Model Gateway；
- Conversation；
- Evidence/Citation；
- PDF Viewer；
- Evaluation Dataset。

#### 垂直切片

```text
选择 Project
  → 提问
  → Hybrid Retrieval
  → 生成结构化 Answer/Evidence IDs
  → 引用校验
  → 流式显示
  → 点击引用跳到论文页码
```

#### 阶段出口

- 回答不能引用未检索或不属于 Project 的文献；
- 无证据问题返回明确的证据不足结果；
- 同一文献的多个 Chunk 不会无控制地占满上下文；
- 对话和引用在刷新后可恢复；
- 至少有一套固定问题、期望文献和 Evidence 的评测数据；
- Fake Provider 测试不访问真实模型；
- 学习笔记能解释 Retrieval、Evidence、Citation 和生成文本之间的关系。

### Phase 3：固定文献综述 Workflow

#### 目标

通过固定 LangGraph 完成从检索策略到带引用综述 Artifact 的长时间任务，并支持人工确认、暂停和恢复。

#### 需要学习

- 图状态与业务状态分离；
- Node 粒度；
- Checkpoint 和 Durable Execution；
- Interrupt/Resume；
- 确定性、非确定性 Task 和幂等；
- Workflow 失败恢复；
- 分阶段生成和 Citation Validation。

#### 主要模块

- Review Workflow；
- arXiv Search/Download Adapter；
- Run Step；
- Human Input；
- Evidence Matrix；
- Review Artifact。

#### 首版 Workflow

```text
研究问题
  → 检索策略
  → arXiv 搜索并自动导入前 N 篇
  → 等待 Ingestion/Indexing
  → Evidence Matrix 提取
  → 结构化大纲
  → 人工确认大纲
  → 分节撰写
  → 引用/一致性校验
  → Markdown 导出
```

#### 阶段出口

- Workflow 可以等待论文依赖，并在大纲节点等待人工输入；
- Worker 重启后能从持久 Checkpoint 恢复；
- 恢复不会重复创建 Paper、Evidence 或 Artifact；
- 每个业务阶段在 Run Detail 中可观察；
- 失败节点提供稳定错误码和可重试判断；
- 最终综述每个主要 Claim 可追溯到 Evidence；
- 输出包含 arXiv 检索、成功导入和失败文献清单；
- 学习笔记能解释 Checkpoint 为什么不等同于业务 Run 数据。

Phase 3 实际实现把 `ReviewRun.current_stage` 固定为详情 API 可观察的当前/下一业务阶段：创建事务已完成
请求校验，因此保存成功的 `VALIDATE_REQUEST` Step 后进入 `FORMULATE_SEARCH_STRATEGY`；图外
Strategy、arXiv Search/Import、Wait/Reconcile 和 Matrix 各自在 Step/Event 同一短事务中继续推进。
阶段条件更新拒绝节点重放把 Outline/Section 等后期 Stage 倒退。最终 Run Summary 的模型调用与 token
从 `model_invocations` 聚合；Final Output 只保存统计、引用数量和 Artifact manifest，完整引用映射只在
Bibliography Artifact 与 Claim/Citation/Evidence 事实中保存。

### Phase 4：Demo-ready Core 产品闭环、可靠性与评测

#### 目标

在引入开放式 Agent Runtime 之前，将文献导入、RAG 和固定 Review Workflow 做成可在本地开发环境
复现、演示、诊断和评测的独立产品。

Phase 4 完成即代表 **Demo-ready Core Research Backend v1** 完成；它不是公网生产产品，Research
Agent Extension 不阻塞该里程碑。

#### 需要学习

- JSON 结构化日志、Correlation 和 Prometheus 低基数指标设计；
- 高基数与 Correlation；
- RAG、Retrieval、Citation 和 Workflow 评测；
- E2E 测试边界；
- Lease/Heartbeat、故障注入和恢复对账；
- 本地开发启动、Fake/Real Adapter 边界；
- 性能基线和容量假设。

#### 主要内容

- 完整 Project、Library、Chat、Review、Run 和 Artifact UI；
- 结构化 Outline 表单、完全离线的 Fake arXiv 和可重复演示 Fixture；
- 标准库 JSON 日志、Correlation ID 和小型 Prometheus Metrics；
- 固定 Retrieval/Citation/Workflow 评测集及结果报告；
- Queue、Worker、数据库、Provider、Checkpoint 和 Event 通知故障注入；
- 用户与 Project 隔离测试；
- PostgreSQL/Valkey Compose 加宿主 API/Worker/Web 的开发启动与共享 Storage 审计；
- 关键 E2E、项目架构说明和面试笔记。

#### 阶段出口

- 文献导入、RAG 和 Review Workflow 三条核心用户旅程可以从 UI 完成；
- Worker 崩溃、重复 Job、Provider 临时错误、取消竞争和 SSE 断线均有恢复测试；
- 用户 A 无法读取用户 B 的文献、Run、Evidence 和 Artifact；
- 每个用户可见错误可以通过 Correlation ID 诊断；
- 有真实执行得到的性能和评测基线，不使用虚构指标；
- 全新开发环境安装依赖后能够按文档离线启动和运行；
- 真实 arXiv/Provider 只通过显式模式运行，普通测试无网络和费用；
- 每个 Core 模块有学习笔记、已知限制和 60 秒面试说明。

公网部署、登录认证、自动备份恢复、永久删除/GC、OpenTelemetry 和 SLA 明确不属于该阶段；完整边界
见 ADR-0004 和 Phase 4 Spec。

### Phase 5：Deep Agents 集成验证

#### 目标

先建立交互式 Research Agent 的业务包装，再对已选定的 Deep Agents 进行受限 Spike。通过
`ResearchAgentRuntime` Adapter 打通 `AgentSession`、逐轮 `AgentTurnRun` 与 SDK Thread/Execution 的
映射，验证多轮上下文、取消、恢复、事件和结果对账；随后再逐项验证 MCP、Browser、Sandbox 和平台
Skills。该阶段不重新选型、不开发通用 Agent Harness，也不以完整 Agent 产品为目标。

#### 候选用户故事

用户创建一个绑定 Project 的 Agent Session，连续发送两条研究消息。Agent 基于 Project Paper Chunk
Index 和指定 Review Evidence Matrix 回答第一轮，在第二轮继续同一上下文并生成一个带 Evidence 来源的
小型候选 Artifact。首个业务验收切片完全使用 Fake Runtime；Deep Agents Adapter 切片随后使用
`create_deep_agent`、Fake Chat Model 和确定性 Tool 验证同一 Thread 的原生多轮消息与压缩，两者都不
依赖真实模型、网站、MCP 或 Sandbox。

Phase 5 切片 4 已将首个真实 Adapter 精确固定到 `deepagents==0.7.8`。该 Spike 使用 StateBackend 与
PostgreSQL Checkpointer 验证成功 Execution 的新连接/新 Adapter 结果恢复、同 Thread 增量消息、原生摘要与
`/conversation_history/*.md` 卸载；它没有把 Worker 生产装配切离 Fake Runtime，也没有接入 Sandbox、
MCP、Browser、长期 Memory、Skill 和真实 Project Tool。该切片当时尚未启动第二 OS 进程，也不证明
orphan `RUNNING` checkpoint 自动 resume 或 Tool 执行后、checkpoint 提交前崩溃窗口的 Effectively Once；
正式 Project Tool 需要稳定 call/effect ID 与持久调用记录。

Phase 5 切片 5 已以 SDK-neutral `ProjectResearchContext` 接入固定的
`search_project_chunks`/`read_review_evidence_matrix`。Deep Agents `ToolRuntime` 只注入稳定
`turn_run_id`；模型不能选择 owner、Project、Snapshot、ReviewOutput 或 ChunkSet。平台按每轮
ContextSnapshot 精确下推 PaperVersion/ChunkSet，验证指定 Matrix 的 Output/Run/Evidence 闭包，并把实际
暴露的来源 Evidence 幂等物化到当前 AgentTurn Run。稳定 `ToolExecution` effect、唯一约束、状态 CHECK、
条件更新和平台预算支持成功 replay、并发拒绝与 temporary 同 effect 重试；安全 Event 不保存 query、正文
或完整结果。最终回答的行内 Evidence 标记与 Runtime DTO 必须一致，再复用通用 ClaimSet/Claim/Citation
校验和原子提交。生产 Worker 仍使用 Fake Runtime；该证据不替代后续部署与崩溃恢复门槛。

切片 4 同时暴露了三项耦合缺口：没有真实第二 OS 进程恢复证据，失败/取消终态与 orphan `RUNNING`
缺少持久对账事实，生产部署与 Runtime Execution lease/recovery owner 尚未决定。切片 6 已由 ADR-0006
选择 ARQ Worker 内运行，并增加 SDK-neutral RuntimeExecution lease/fencing、同步 checkpoint durability、
严格版本兼容和真实双 OS 进程恢复证据，三项缺口均已闭合。只承诺不重复已持久确认的调用，不宣称在途
外部调用 Exactly Once。平台 `last_checkpoint_id` 只表示已观察水位；无论水位为空还是停在旧 C1，恢复
都必须先选择并校验物理最新 Checkpoint，以 `astream(None)` 继续，不能重新追加用户消息或重放 C2 前
已确认 Step。Checkpoint 必须匹配 Turn/Session/Execution/request hash/runtime+graph revision。证据和非声明边界见
[`phase-05-runtime-recovery-gap-log.md`](../learning-journal/reports/phase-05-runtime-recovery-gap-log.md)。

切片 6 当时只通过显式依赖注入构造真实 Deep Adapter。切片 7.0 随后已完成 Real Deep Agent Runtime
Enablement 的实现：生产 Worker 默认保持 Fake，只有显式 `deep_agents` 才使用
`langchain-deepseek==1.1.0` 构造固定关闭 thinking 的 `deepseek-v4-flash`，并装配既有持久
Checkpointer、Project Context 与 RuntimeExecution control；真实模式缺少专用 Key 时启动前失败。
本切片尚未执行真实 Provider Smoke。切片 7.1 随后已实现 OpenSandbox/Lease/WorkspaceSnapshot 的
SDK-neutral 边界、统一 Tool 预算与每 operation Saver/graph。ADR-0008 已将后续调整为 MCP 配置基础、
同 Sandbox Playwright MCP/现有 Search MCP 和 Deep Agents 原生 Skills；不再自研 Browser Tool 或 MCP
Server。该切片完成时真实 OpenSandbox Smoke 仍未运行。
Slice 7.3 已进一步完成固定 Playwright/arXiv MCP 的生产 Catalog、Sandbox recipe 与 Worker resolver，
并在无网络派生容器验证同 Chromium 合成页面和 Workspace 下载。由于开发环境没有运行 OpenSandbox
server，opaque endpoint/header 与代理 Host 语义仍是显式 Smoke 门槛；该证据不扩张为公共浏览、真实
arXiv 搜索或下载安全结论。
Slice 7.4 已完成平台 `evidence-led-synthesis` 与 owner-scoped 声明式 Skill：PostgreSQL 保存不可变版本、
Session Profile 和逐 Turn 冻结引用；首个 Message 后 Profile 永久锁定；Worker 在事务外把精确版本物化到
`/skills/` 只读 Backend，直接交给 Deep Agents 原生 SkillsMiddleware。同一 Thread 两轮 metadata 缓存、
写入拒绝、Sandbox execute 不可见和 required Tool 不扩权均通过离线测试；两轮测试会重建 Runtime/graph
并仅共享 checkpointer/thread。版本允许以 A→B→A 追加回退，Profile hash 对 selection 规范排序。该证据
不包含真实 Provider/OpenSandbox Smoke、可执行/附件 Skill、Prompt Injection 专项或完整治理产品。
Policy version 继续兼容既有分支：workspace-only 和 MCP-only 分别沿用原版本，只有 `skill_refs` 非空时
切换到 `project-research-capabilities.v1`。

`PolicySnapshot.max_model_calls` 在 7.0 精确定义为逐 Turn 主 Agent Loop 模型调用预算：调用前预留计数并
随同步 checkpoint 持久化，已确认 checkpoint 后恢复不返还额度。该预算不覆盖 Provider 在途不确定窗口，
也不覆盖 `SummarizationMiddleware._summary_model.with_retry()` 最多 3 次内部 Provider 尝试，所以当前
不是完整费用硬上限。Slice 7.1 已用固定 Capability Profile 把 Project、文件和 `execute` Tool 纳入
统一逐 Turn 预算；这仍不等于真实 Provider/OpenSandbox 回路或费用上限已经 Smoke。

#### 需要学习和验证

- Deep Agents 的运行模型、许可、精确版本和部署边界；
- `AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1` 的契约；
- ContextSnapshot、PolicySnapshot、Checkpoint、Store 和 Workspace 的生命周期；
- PostgreSQL 产品消息与 Deep Agents Runtime Message/摘要/Checkpoint 的双层所有权，以及正常 Turn
  只追加新消息、Runtime 损坏时才受控重建的边界；
- `create_deep_agent` 原生 summarization 和大型结果文件卸载；
- Runtime 事件、Usage、错误、审批和 Artifact 的归一化；
- ARQ、本系统 Run Control 与 SDK 内部重试/恢复的所有权；
- Agent Worker 的每个新 Attempt 必须先按稳定 Turn ID reconcile；只有 Runtime 明确未知时才 execute，
  已有 RUNNING Execution 不重复追加输入，已有成功结果直接 collect 并 Effectively Once 提交；
- 业务提交前校验 Runtime reconciliation/result 的 Session、Turn 与 Binding 稳定映射；错配安全失败且不
  持久化错误 Binding/Message/candidate；Runtime consumer 与取消 watcher 在异常或外层取消时统一清理；
- Deep Agents Fake Model/Fake Runtime 的可测试集成；
- MCP 配置、Playwright/Search MCP、平台 Tool/原生 Skill 和 Sandbox 能力的独立 Spike；
- Runtime 取消、超时、断连和结果对账；
- Runtime 部署拓扑与 Execution 恢复 owner；第二个 OS 进程对 orphan `RUNNING` 的条件认领、同一
  Checkpoint 恢复，以及失败/取消终态的持久对账；
- Prompt Injection、网络外泄和下载风险。

#### 实现顺序门槛

```text
Project Research Context
  → Runtime 部署与崩溃恢复门槛
  → 7.0 Real Deep Agent Runtime Enablement
  → 7.1 OpenSandbox / Lease / WorkspaceSnapshot
  → 7.2 MCP Configuration Foundation
  → 7.3 同 Sandbox Playwright MCP / 现有 Search MCP
  → 7.4 Deep Agents Native Skills
  → 最小 Agent Chat UI
  → 集成 ADR 与阶段复盘
```

恢复门槛已根据 Project Tool 的调用记录和副作用边界决定 ARQ Worker 内运行，并固定 lease/recovery
owner。验收测试实际启动第二个 OS 进程，沿用同一 Execution/Checkpoint 恢复且不重新追加用户输入；
同进程新 Adapter 仅作为较低层测试。切片 7.0 已提供可显式启用的 Worker Deep 模式，但在真实 Provider
Smoke、Capability Profile 和后续 Sandbox/Tool 门槛通过前，只能称为真实 Runtime enablement，不能描述
为完整 Research Agent 生产能力。

#### 最小垂直切片

```text
创建 Project-scoped AgentSession
  → 第一条消息创建 AgentTurnRun 与不可变 Context/Policy Snapshot
  → Worker 调用 Fake ResearchAgentRuntime
  → Runtime 读取受限 Paper Chunk 与 Review Evidence Matrix
  → 平台归一化 Event、Message、Evidence 引用和候选 Artifact
  → 第二条消息在同一 Session 创建新的 AgentTurnRun
  → 复用同一 SDK Thread，只追加新增消息并验证原生 Checkpoint/压缩恢复
```

#### 阶段出口

- ADR-0001 与 ADR-0005 分别记录 Runtime 选型和交互式会话模型；本阶段集成 ADR 记录实验证据、版本、Provider、部署方式和失败项；
- `ResearchAgentRuntime` 契约不泄漏具体 SDK 类型到 Domain 和公开 API；
- AgentSession、AgentTurnRun 与 SDK Thread/Execution 有稳定映射，同一 Session 同时最多一个活动 Turn；
- 真实 Adapter 使用 `create_deep_agent`，正常后续 Turn 不重放完整 PostgreSQL 历史，并通过强制触发
  summarization 的离线测试证明原生上下文管理仍能完成第二轮；
- 每轮 ContextSnapshot 和 PolicySnapshot 可审计，Project 索引与 Evidence Matrix 不被复制成 SDK 事实来源；
- 临时文件、内部 WorkspaceSnapshot 与正式 Artifact 的生命周期分离，Sandbox 丢失后可重建允许跨 Turn 的工作文件；
- SDK 事件被筛选并映射为版本化业务 Event，不保存完整思考过程和敏感输出；
- 取消、超时、Runtime 断连和“SDK 成功但本地响应丢失”至少各有一次验证；
- 部署拓扑、Runtime Execution lease/recovery owner 已由集成 ADR 固定；真实第二 OS 进程可以受控认领
  orphan `RUNNING`、恢复同一 Checkpoint，并对账持久的失败/取消终态；
- 两轮 Fake Runtime 用户故事可完全离线运行；后续能力 Spike 的 Artifact 具有来源、内容哈希、大小、类型和 Project 所有权；
- 明确是否进入 Phase 6；若用例或运行时不成立，Demo-ready Core v1 仍保持完整可交付。

### Phase 6：Deep Agents 驱动的 Research Agent 与安全强化

#### 目标

基于 Phase 5 验证通过的会话与 Runtime 边界，将 Project-scoped Agent Chat 扩展为可用、受限、可观察的
Research Workspace Agent，并以一个可演示的固定用户故事验证 Browser、MCP、Tool、版本化 Skills、
Workspace、Sandbox、arXiv 访问和文件交付的安全与可靠性；不建设通用 Agent 安全平台。

本阶段的强制实施边界见
[`Research Agent 精简安全契约`](research-agent-security-contract.md)。该契约中的“目标事实”和“安全门槛”
只有在责任切片通过相应离线测试及显式真实 Smoke 后才可声明完成；配置、Mock 或 Phase 5 Spike 不能替代。

#### 主要内容

- Agent Session 多轮 Chat、Turn 详情、事件、取消、来源和 Artifact UI；
- Project Chunk Index、Review Evidence Matrix 与 Artifact Context 工具；
- Paper/Evidence、固定 Catalog Search/Playwright MCP、正常公网页面和正式资源下载；
- `research-public-egress.v1`、覆盖 Sandbox 全部进程的统一公网允许与 private/metadata/宿主/LAN 拒绝、
  正式资源下载隔离和来源记录；
- 固定 Catalog/Profile、Runtime Tool Policy、硬预算和确定性循环保护；
- Workspace/Sandbox 生命周期、文件传输和资源限制；
- Agent Event、Usage、必要 ToolExecution 摘要和 Artifact 审计；
- Runtime 升级兼容测试、关键故障验证和小型 Agent 评测集；
- 平台安装、版本化、allowlist 控制的 Research Skills 与 owner-scoped 声明式 Skill 治理；
- 在 ADR-0007 已验证的 OpenSandbox `execute` 基础上强化隔离、网络、资源、审计和用户可见治理；
- AgentAttachment、WorkspaceSnapshot、AgentArtifactCandidate 与 AgentArtifact 的显式输入/内部状态/输出
  边界，以及 `submit_artifact` 预览下载闭环。
- 最终 Agent UI 遵循 [`Web UI 应用壳与视觉重设计`](web-ui-app-shell-redesign.md)：左侧固定 Sidebar、轻量
  PageBar、桌面优先三栏工作区和浅色编辑风；功能切片不得重新依赖将被删除的旧 Header、
  `ProjectWorkspaceHeader` 或 `ProjectNav`。

完整 MCP/Tool Registry、Catalog 管理后台、OAuth/Credential、通用审批中心、正式外部写产品能力及其
协议级强制、用户自定义网络策略、通用 URL 安全代理、动态依赖、多 Agent/长期 Memory、公网多租户和
生产级 Sandbox 运维平台不属于精简交付。平台不注册外部写专用 Tool 并要求 Agent 只做研究读取，但
L3/L4/FQDN egress 不能保证 raw Browser/Shell/MCP 不发送 POST 或表单。

#### 阶段出口

- Agent 只能访问当前 Session/Turn 授权的 Project Context、工具、public-egress Profile 和 Workspace；
  正常公网 Host 不逐项授权，private/metadata/宿主/LAN 始终拒绝；
- 多轮 Message、Turn、ContextSnapshot、Thread 与 Artifact 的所有权和恢复语义有测试证据；
- 不绕过登录、付费墙或 CAPTCHA；不提供外部写和不可逆操作的产品 Tool/Workflow，文件只有离开 Sandbox
  成为正式业务资源时才通过文件与来源策略；raw 公网通道不具备协议级只读保证；
- 网页、论文和仓库内容按不可信输入处理，Prompt Injection 不会获得平台 Secret 或数据库权限；
- 最大步骤、Token、墙钟时间、Tool Call、下载和输出大小限制生效；
- Runtime 取消后不再发起新操作，重复执行不会重复提交最终 Artifact；
- Sandbox 默认不接触宿主文件和 Secret，网络与资源策略有测试证据；
- SDK 升级由契约测试保护，公开 API 不暴露 SDK 内部模型；
- App Shell 重设计按其独立前端切片完成，`project-workspace-ui-contract.md` 被取代条款同步更新，最终
  Browser、附件、Artifact、Tool 和 Evidence UI 不与新壳层冲突；
- Core 与 Agent 两组用户旅程、评测、运维文档和已知限制均完成。

## 18. 学习笔记规划

建议在模块完成后形成以下笔记：

```text
docs/learning-journal/modules/
├─ 01-async-api-and-worker-boundary.md
├─ 02-run-state-machine-and-events.md
├─ 03-postgres-transactions-and-idempotency.md
├─ 04-document-ingestion-and-versioning.md
├─ 05-hybrid-retrieval-and-pgvector.md
├─ 06-evidence-and-citation-integrity.md
├─ 07-langgraph-checkpoint-and-resume.md
├─ 08-human-in-the-loop.md
├─ 09-sse-replay-and-frontend-state.md
├─ 10-observability-and-evaluation.md
├─ 11-agent-runtime-sdk-integration.md
└─ 12-browser-sandbox-and-side-effects.md
```

前 10 份属于 Core Research Backend；第 11、12 份只在 Agent Extension 对应模块实际完成后编写。

每份笔记至少说明：

- 问题和模块角色；
- 输入、输出、状态和依赖；
- 执行流程；
- 数据模型和事务边界；
- 核心不变量；
- 关键设计与被拒绝方案；
- 正常、超时、重复、取消和崩溃行为；
- 安全和隐私；
- 测试和运行证据；
- 日志、指标和 Trace；
- 重要代码入口；
- 已知限制和扩展路径；
- 60 秒面试回答和可能追问。

## 19. 测试与评测总则

### 19.1 测试层次

- Domain Unit Test：状态机、Validator、策略和错误分类；
- Application Test：用例编排和事务边界；
- Repository Integration Test：真实 PostgreSQL 和约束；
- Queue/Worker Integration Test：重复 Job、失败和恢复；
- LangGraph Test：Node、Route、Interrupt、Resume 和 Checkpoint；
- Contract Test：HTTP、Event 和模型结构化输出；Agent 扩展再增加 Runtime/Tool 契约；
- UI Component Test：用户可见状态和无障碍；
- E2E：少量关键旅程；
- Evaluation：RAG、Citation 和综述质量数据集。

### 19.2 确定性

默认测试不得依赖真实 LLM、实时学术 API 或付费服务。使用：

- Fake Chat Model；
- Fake Embedding；
- HTTPX2 Mock/pytest-httpx2（必要时直接使用 RESPX）；
- 固定 PDF Fixture；
- 注入 Clock、ID Generator 和随机源；
- Testcontainers PostgreSQL/Valkey。

真实 Provider 只用于显式启用的 Smoke/Evaluation，不进入普通 CI 成功条件。

### 19.3 故障测试

Core Research Backend 至少覆盖：

- Worker 在步骤前后退出；
- Provider 超时、429 和 5xx；
- 外部请求成功但本地响应丢失；
- 同一 Job 重复投递；
- 相同 Idempotency Key 不同请求；
- Event 通知丢失；
- SSE 断线重连；
- 用户取消与步骤成功并发；
- Checkpoint 后恢复；
- Parser 超时；
- 跨用户访问；
- 无效或伪造 Citation。

Agent Extension 另需覆盖：

- Runtime 断连、超时、取消和升级不兼容；
- SDK 成功但本地响应丢失；
- Browser Redirect、SSRF、Prompt Injection 和网络外泄；
- 下载大小、类型、恶意文件和重复 Artifact；
- Sandbox 超时、资源耗尽和跨 Workspace 访问；
- Tool 审批、预算和重复副作用。

## 20. 架构决策触发条件

以下变化必须先写 ADR：

- ARQ/Valkey 更换为 Celery、RabbitMQ、Temporal 或其他队列；
- PostgreSQL 向量检索更换为专用向量数据库；
- 本地文件存储更换为 S3；
- 模块化单体拆分为独立服务；
- 更换 Deep Agents 或改变 `ResearchAgentRuntime` 选型边界；
- 引入外部 Agent Server、Browser 或 Sandbox 服务；
- 修改业务 Run 与 SDK Conversation/Thread/Checkpoint/Workspace 的所有权或映射；
- 修改 SDK 原始 Event 到业务 Event 的归一化规则；
- 扩大 Agent 网络、凭据、文件或下载权限；
- 引入任意代码执行；
- Workflow 从固定图升级为用户自定义图；
- 新增多 Agent；
- 修改核心 Run/Event/Checkpoint 所有权；
- 增加破坏兼容性的 API、Event 或 Tool 版本；
- 更改文献、Prompt、Trace 和用户数据的隐私策略。

## 21. 暂缓到阶段 Spec 的决策

本文有意不提前固定：

- 每张表的完整列和索引；
- API 的完整 URL、Payload 和错误码；
- Event Payload 的全部字段；
- Run 状态的数据库并发实现；
- ARQ 重试次数、超时和 Worker 并发；
- Chunk 长度、Overlap 和结构规则；
- Embedding 和 Chat Model 的具体名称；
- Hybrid Retrieval 的权重和 Top-K；
- LangGraph 的精确 State 与 Node 列表；
- Prompt 文本；
- 大纲确认的 UI 细节；
- DOCX 模板；Phase 3 Markdown 已固定为 `[1]` 数字引用；
- Metrics Bucket 和告警阈值。

以下实现决策明确推迟到 Phase 5/6，而不是在 Demo-ready Core v1 中预先固定：

- 是否在 Phase 5 Spike 通过后进入正式 Agent 产品；
- Deep Agents 的升级策略和兼容范围（首个 Adapter 版本已固定为 `0.7.8`）；
- ADR-0006 已固定 ARQ Worker 内 Runtime；仍需确定真实 Provider/Sandbox 的进程资源和部署参数；
- SDK Checkpoint/Store/压缩的升级策略，以及已固定 TTL/generation/fence 后的孤儿 Lease 清理；
- Phase 5 固定 Playwright/arXiv MCP 进入精简产品 Profile；ADR-0012 已取代 ADR-0011 的固定 arXiv
  公网范围，Slice 7 需确定 public-egress/private-network 的统一 egress 实现、Profile/hash/Lease 轮换和
  正式资源限制参数；
- 首批平台 Research Skills 与 owner-scoped 声明式 Skill 的基础版本/Profile 已由 Slice 7.4 固定；内容
  审核、归档/删除、配额、附件/脚本与 Prompt Injection 专项治理仍推迟；
- OpenSandbox derived image 发布 digest、Server 部署和已固定 TTL/资源参数的真实强制效果；
- Phase 5 `execute` Spike 通过后，ADR-0012 已固定 Sandbox 正常公网 transport 和受支持新 Artifact 可
  自动执行；平台不注册外部写专用 Tool、不提供凭据，也不在本阶段建设审批矩阵，但 raw 公网通道不
  具备协议级只读保证。

Agent 产品形态和核心映射不再属于推迟项：ADR-0005 已固定 Project-scoped 持续研究对话、
`AgentSession : SDK Thread = 1:1`、`AgentTurnRun : SDK Execution = 1:1`，以及每轮 ContextSnapshot 和
PolicySnapshot。正常 Turn 只向同一 Thread 追加新消息、由 `create_deep_agent` 原生维护模型工作上下文也
已固定；精确压缩阈值仍由后续实验决定。OpenSandbox Backend 组装和损坏重建协议已由 7.1 固定，其余
决定应基于 Phase 5 的真实实验
和测试，而不是在尚未实现基础链路时猜测。Sandbox Provider、Session 级短 TTL Lease、固定依赖的
Sandbox `execute` 与默认禁网已由 ADR-0007 固定，MCP/Skill 接入方式和 Slice 7.2–7.4 顺序已由
ADR-0008 固定，不再属于推迟项；精确依赖、第三方 MCP/Skill 版本和部署参数仍必须在新增依赖或镜像内容
前单独核对。

## 22. 完成定义

### 22.1 Demo-ready Core Research Backend v1

Demo-ready Core v1 完成需要同时满足：

- 文献导入、有引用 RAG 和固定 Review Workflow 三条路径可运行；
- 长任务可查询、重试、取消、暂停和恢复；
- Worker/HTTP 进程重启不会破坏业务事实；
- Event 可重放，页面刷新可恢复；
- 重要 Claim 的引用可定位到 Paper Version 和 Evidence；
- 用户和 Project 数据隔离有自动测试；
- 关键行为不依赖真实模型或实时学术 API 即可测试；
- 全新开发环境安装依赖后，可通过 PostgreSQL/Valkey Compose 和 `scripts/dev.sh` 完全离线复现启动；
- 标准库 JSON 日志、Correlation ID 和低基数 Prometheus Metrics 可用于最低诊断；
- Retrieval、Citation 和 Workflow 评测结果来自真实运行且可复现；
- Phase 1 至 Phase 4 的阶段 Spec、测试证据、模块笔记和复盘完成；
- 开发者能够不依赖 AI 解释核心架构、失败语义和设计取舍。

达到以上条件即可将项目作为 Demo-ready Core Research Backend 交付，Research Agent Extension 不阻塞
该结论。公网部署、认证、自动备份恢复、永久删除/GC、OpenTelemetry 和 SLA 不因该里程碑而被视为
完成。

### 22.2 Research Agent Extension

若 Phase 5 集成验证通过并继续 Phase 6，Agent 扩展完成还需要：

- 至少一个绑定 Project、可连续多轮交互并生成可追溯 Artifact 的研究用户故事端到端可运行；
- Agent SDK 通过 `ResearchAgentRuntime` Adapter 接入，不污染 Domain 和公开 API；
- Adapter 原生使用 `create_deep_agent` 的消息、Checkpoint、上下文压缩和文件卸载能力，正常多轮不从
  PostgreSQL 重放完整产品历史；
- AgentSession、Message、AgentTurnRun、SDK Thread/Execution、Workspace、Event 和 Artifact 的所有权清晰；
- Agent 能以最小授权方式使用 Project Chunk Index 与 Review Evidence Matrix；
- Browser、Tool、正式资源下载和代码执行受 Schema、权限、硬预算、public-egress/private-network 与
  资源策略限制；不提供外部写产品能力，但不宣称 raw 公网请求协议级只读；
- Runtime 重试、取消、断连、恢复和重复副作用有测试证据；
- Agent 不能接触未授权 Project 数据、平台数据库、宿主文件或 Secret；
- Agent 评测、运维文档、模块笔记和已知限制完成。

## 23. 建议学习节奏

不建议以固定周数作为硬性承诺。建议采用“一个概念主题 + 一个可演示垂直切片”的节奏：

| 顺序 | 学习主题 | 对应产物 |
|---|---|---|
| 1 | FastAPI Async、Worker、事务 | 可消费测试 Job 的开发基线 |
| 2 | Run、Event、幂等、取消 | 可恢复的 PDF 导入任务 |
| 3 | 文档结构、Embedding、检索 | 可评测的文献检索 |
| 4 | Evidence、Citation、SSE | 有引用的 RAG 问答 |
| 5 | LangGraph、Checkpoint、Interrupt | 可暂停恢复的综述 Workflow |
| 6 | Logs/Metrics、E2E、评测、故障恢复 | 本地可演示、可复盘的 Demo-ready Core v1 |
| 7 | Agent Session、Turn Run、Runtime Adapter、所有权 | 可离线验证的多轮 Agent 集成 Spike |
| 8 | Browser、MCP、Skill、Sandbox、安全 | 受限且可观察的 Research Workspace Agent |

如果某阶段无法用自己的话解释状态所有权和失败行为，应暂停增加功能，先完成实验和笔记。

## 24. 最终面试叙事

Core Research Backend 完成后，推荐使用下面的主线介绍：

> 我实现了一个面向文献综述的可靠研究后端。FastAPI 负责 API 和 SSE，ARQ/Valkey 负责异步任务投递，PostgreSQL 保存业务 Run、Event、文献、Evidence 和向量，LangGraph 管理固定综述 Workflow 的 Checkpoint、Interrupt 和 Resume。系统将业务 Run 与 LangGraph State 分离，按至少一次执行设计，通过条件更新、唯一约束、幂等键和内容哈希避免重复副作用。RAG 和综述生成采用 Evidence-first，重要 Claim 绑定具体论文版本、页码和证据，并在输出前进行引用校验。系统可以演示任务重试、取消、SSE 重放、人工确认、Worker 崩溃恢复和可复现评测。

Research Agent Extension 完成后，可以追加：

> 在核心后端稳定后，我没有重新实现通用 Agent Harness，而是通过 `ResearchAgentRuntime` Adapter
> 接入 Deep Agents。产品把持续研究对话建模为 Project-scoped AgentSession，并把每条用户消息建模为
> 独立 AgentTurnRun；SDK Thread 与 Session 对应、一次 SDK Execution 与 Turn 对应。SDK 负责通用规划、
> 原生消息压缩、工具编排和隔离 Workspace，本系统继续拥有产品 Message、Run、权限、Context Snapshot、
> Event、Evidence 和 Artifact。正常后续轮只向同一 Thread 追加新消息，因此既不重复实现 Agent Harness，
> 又能让开放式 Agent 安全复用项目索引、Evidence Matrix 与既有可靠执行能力。

后续追问应能够展开：

- 为什么不直接用 FastAPI BackgroundTasks；
- ARQ Job 和业务 Run 有什么区别；
- LangGraph Checkpoint 为什么不能代替业务数据库；
- 如何处理重复 Job；
- 为什么不承诺 Exactly Once；
- Event 和当前状态为什么都需要；
- 取消为何是协作式；
- Citation 如何防止模型伪造来源；
- 为什么第一版不用独立向量数据库；
- 为什么第一版不实现通用 Workflow Builder；
- 为什么不自行开发通用 Agent Loop；
- AgentSession、AgentTurnRun 与 SDK Thread/Execution/Workspace 如何映射；
- Sandbox 能解决什么、不能解决哪些 Prompt Injection 和网络风险。

## 25. 技术参考

- [FastAPI](https://fastapi.tiangolo.com/)
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)
- [LangGraph Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph Checkpointer Integrations](https://docs.langchain.com/oss/python/integrations/checkpointers/index)
- [ARQ](https://arq-docs.helpmanual.io/)
- [PostgreSQL](https://www.postgresql.org/docs/)
- [pgvector](https://github.com/pgvector/pgvector)
- [Valkey](https://valkey.io/)
- [Docling](https://github.com/docling-project/docling)
- [arXiv API User's Manual](https://info.arxiv.org/help/api/user-manual.html)
- [Deep Agents](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [LangChain MCP Adapter](https://github.com/langchain-ai/langchain-mcp-adapters)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
