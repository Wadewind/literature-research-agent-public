# 文献综述 Agent 系统：学习与开发实施指南

> 状态：Proposed v2
>
> 日期：2026-08-13
>
> 定位：面向单人、AI 辅助开发的总体实施文档；用于确定产品边界、总体架构、模块职责、阶段顺序和学习目标
>
> 技术方向：Python / FastAPI / LangGraph / PostgreSQL / pgvector / ARQ / Valkey / React
>
> v2 变更：将 RAG、固定 Workflow 和可靠后端定义为可独立交付的 Core v1；Research Agent 改为 Core 完成后通过成熟 SDK 接入的扩展里程碑

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

在核心能力完成、经过评测并具备可靠运行证据后，再建设 **Research Agent Extension**：通过 `ResearchAgentRuntime` 适配边界接入基于 LangGraph 的 Deep Agents，让 Agent 在受控 Workspace 中发现论文相关的公开项目页、代码仓库、数据集和补充材料，并把结果交回本系统的 Evidence、Artifact、Run 和 Event 体系。选型依据见 `docs/learning-journal/decisions/0001-select-deep-agents-runtime.md`。

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

### 3.1 Core Research Backend v1 范围

Core Research Backend v1 需要完成：

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
- 单节点 Docker Compose 部署；
- 关键行为的自动测试、故障注入和学习笔记。

总体架构需要为后续 `ResearchAgentRuntime` Adapter 保留清晰边界，但 Core v1 不提前实现未验证的通用 Agent、Tool Registry、浏览器或 Sandbox 抽象。

### 3.2 明确非目标

首版不实现：

- Dify 式通用 App Builder；
- 可视化 Workflow Canvas；
- 用户自定义任意 DAG Node；
- 插件市场；
- 多 Agent、Swarm、辩论或动态 Agent 团队；
- 自行实现通用 Agent Loop、上下文压缩、浏览器自动化框架或 Sandbox 平台；
- Core v1 中的开放互联网浏览、自动下载和任意代码执行；
- 任意 Shell 或直接访问宿主机的 Python；
- 自动抓取付费墙后的论文全文；
- Kubernetes、多地域和高可用；
- Kafka、Temporal、Elasticsearch、Qdrant 或 Milvus；
- 企业 SSO、复杂 RBAC 和计费系统；
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
- 后续扩展的 Research Agent Runs；
- Evidence、Citation 和 Artifact。

所有用户可见查询必须在 Project 和用户所有权范围内执行。

### 4.2 Paper 与 Paper Version

Paper 表示稳定的学术作品身份和书目信息，例如 DOI、标题、作者、年份和来源。

Paper Version 表示系统实际处理过的一份全文版本。重新上传、重新解析或更换解析器时，不应悄悄覆盖旧版本。Evidence 必须指向具体 Paper Version，避免文档重新解析后页码和 Chunk 变化导致引用失效。

### 4.3 Conversation 与 Agent Session

Conversation 保存用户和系统之间的对话历史。后续接入 Agent SDK 时，Agent Session 或 SDK Conversation 是一次 Agent 执行的运行时上下文，可能关联 Conversation，但不等同于后台业务 Run，也不能成为用户权限、业务结果和 Artifact 的唯一事实来源。

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

Core v1 采用单仓库、单节点、少量进程的模块化单体：

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

默认 Docker Compose 服务为：

```text
web
api
worker
postgres
valkey
```

Agent Runtime、Sandbox、Caddy 和可观测性后端在后期通过可选 Compose Profile 加入。Core v1 不要求部署 Agent Server 或 Sandbox。

Agent Extension 的部署边界为：

```text
Business AgentRun / Event / Permission / Artifact (本系统)
                         │
                         ▼
              ResearchAgentRuntime Port
                         │
                         ▼
       SDK Runtime / Agent Server / Isolated Workspace
```

无论 Runtime 在 Worker 进程内还是独立部署，PostgreSQL 中的业务 Run、Event、权限和 Artifact 仍是产品事实来源。

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

### 7.12 Research Agent Runtime Integration（后续扩展）

本模块不自行实现通用 Agent Loop，而是在 Core Research Backend 完成后通过 `ResearchAgentRuntime` Port 接入 ADR 已选定的 Deep Agents。

本系统负责：

- 创建和授权业务 `AgentRun`；
- 保存 Run、Attempt、用户取消意图、预算、审批、业务 Event 和最终结果；
- 将 Project、Paper、Evidence 和 Artifact 以最小授权上下文提供给 Runtime；
- 把 SDK Conversation/Thread、Workspace/Sandbox 和 Event Cursor 映射到稳定业务 ID；
- 归一化 SDK 事件、错误、Usage 和 Artifact 提交；
- 在 Runtime 重试、恢复或响应丢失时进行幂等对账。

外部 Agent Runtime 负责：

- 通用 Agent Loop、规划和上下文管理；
- Tool 选择和运行时内部 Observation；
- Browser、文件、命令和 Sandbox Workspace 内部操作；
- SDK 自身的流式事件与执行上下文。

SDK Conversation、Thread、Checkpoint、Workspace 和 Event 不能替代 PostgreSQL 中的业务 Run、权限、Event、Evidence 和 Artifact。选型阶段必须明确 ARQ、本系统 Run Control 与 SDK Runtime 之间的重试、取消和恢复所有权，避免多层自动重试相乘。

### 7.13 Agent Tool 与 Execution Policy（后续扩展）

Agent 扩展阶段负责：

- Runtime 可见工具或能力的版本、说明和输入 Schema；
- 用户、Project、Run 和 Policy Context；
- Tool 或外部副作用的幂等键；
- 超时、网络、输出大小和资源限制；
- ToolExecution 或等价审计记录；
- 危险操作和下载前审批；
- Sandbox 与 Artifact Workspace 边界。

Core v1 不提前建设通用 Tool Registry。RAG 和固定 Workflow 直接通过明确的应用 Port 调用领域能力；只有 Agent SDK 集成验证证明需要统一 Tool 契约后，才在阶段 Spec 中确定具体模型。

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
用户目标
  → 创建业务 AgentRun + Event
  → Worker 通过 ResearchAgentRuntime Adapter 启动或恢复 SDK Runtime
  → Runtime 在授权的 Project Context 与隔离 Workspace 中执行
  → SDK Event/Usage/Approval 被归一化为业务 Event
  → 下载内容先提交为隔离 Artifact，再按需进入现有 Ingestion Run
  → 最终报告绑定 Paper/Evidence/Resource Manifest
  → Run 完成并持久化 Artifact
```

首个候选用户故事限定为：基于 Project 已有论文和 Evidence，发现论文官方项目页、代码仓库、开放数据集与补充材料，生成可审计的 Resource Manifest 和研究报告。首版 Agent 不绕过登录、付费墙或 CAPTCHA，不自动提交或发布内容。

Agent Runtime 比固定 Workflow 更开放，因此必须更严格限制：

- 可见工具、网络目标和凭据集合；
- 最大步骤；
- 单 Tool 和总墙钟时间；
- Token 和费用；
- Tool 输出大小；
- 重复或无进展循环；
- 代码执行权限；
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

不要直接把所有 Chunk 拼成上下文后要求模型一次性生成完整综述。Phase 3 对短论文按序提供全部 Chunk；长论文按分析维度检索后合并、去重并限额，每篇论文一次调用提取全部维度。章节写作只读取当前章节对应的 Matrix 行及其 Evidence。

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

Core v1 只调用固定学术 API，不提供任意 URL 抓取。后续 Agent 增加 Browser 或 URL Tool 时，必须考虑 SSRF、DNS 重绑定、Redirect、内网地址阻断、下载大小与类型、恶意文件、Prompt Injection 和网络外泄。

Agent 浏览器首版只访问公开资源，优先发现论文官方项目页、代码仓库、开放数据集和补充材料。涉及登录、用户凭据、付费墙、CAPTCHA、对外提交或不可逆操作时必须拒绝或进入人工审批，不能通过自动化规避站点限制。

### 12.4 代码执行

Core v1 不提供任意代码执行。固定图表或导出能力应优先作为确定性的应用服务运行，并使用结构化输入 Schema。

是否开放 `run_python_analysis` 由 Deep Agents 集成 Spike 和后续 ADR 决定。若开放，必须通过经验证的 Sandbox Backend 或经评审的独立 Sandbox，限制：

- 非 root；
- 独立临时 Workspace；
- 默认禁网；
- CPU、内存、进程、时间和输出上限；
- 固定依赖；
- 不挂载宿主 Secret；
- 只读取显式传入的 Artifact；
- 只持久化显式输出的 Artifact。

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
agent-runs
runs / attempts / steps / events
approvals or inputs
evidence / citations
artifacts
models
health / readiness / metrics
```

`agent-runs` 属于 Research Agent Extension，不是 Core v1 API 的完成条件。

长任务创建返回 `202 Accepted` 和稳定 `run_id`。查询和事件接口使用业务 ID，不暴露 ARQ 或 LangGraph 内部表。

### 13.2 Web 页面

Core v1 页面：

- Project 列表和详情；
- Literature Library；
- Paper 详情和 PDF 阅读；
- RAG Chat；
- Review 创建和 Run 详情；
- Artifact 查看和下载；
- 设置和模型配置。

Research Agent、Browser、Workspace 和审批详情页面在 Agent SDK 完成选型并验证事件契约后加入，不提前复制 SDK 自带 UI 或内部状态模型。

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
trace_id
project_id
run_id
attempt_id
step_id
thread_id
tool_execution_id  # 仅 Agent 扩展存在
```

高基数 ID 可以进入日志和 Trace，不能直接作为 Prometheus Label。

### 14.2 日志

使用 structlog 输出结构化 JSON。日志记录事件和诊断上下文，不记录完整 Prompt、Secret、PDF 文本和生成文档。

### 14.3 指标

后期至少覆盖：

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

Core v1 使用 OpenTelemetry 连接 API、Queue、Worker、LangGraph Node、模型和 Retrieval。Agent 扩展再连接 Runtime、Browser、Tool 和 Sandbox Span。LangSmith 或 SDK 自带 Trace 可以用于运行时调试，但不作为系统唯一的业务审计或生产可观测性来源。

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
│  └─ tools/                  # Agent Extension 契约，Core v1 不预建
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

### Phase 4：Core Research Backend 产品闭环、可靠性与评测

#### 目标

在引入开放式 Agent Runtime 之前，将文献导入、RAG 和固定 Review Workflow 做成可演示、可诊断、可恢复并具有评测证据的独立产品。

Phase 4 完成即代表 **Core Research Backend v1** 完成；Research Agent Extension 不阻塞该里程碑。

#### 需要学习

- OpenTelemetry Trace 和 Prometheus 指标设计；
- 高基数与 Correlation；
- RAG、Retrieval、Citation 和 Workflow 评测；
- E2E 测试边界；
- Lease/Heartbeat、故障注入和恢复对账；
- 单机部署、备份与恢复；
- 性能基线和容量假设。

#### 主要内容

- 完整 Project、Library、Chat、Review、Run 和 Artifact UI；
- JSON 日志、Metrics 和 Trace；
- 固定 Retrieval/Citation/Workflow 评测集及结果报告；
- Queue、Worker、数据库、Provider、Checkpoint 和 Event 通知故障注入；
- 用户与 Project 隔离测试；
- Docker Compose 部署和运维文档；
- PostgreSQL 和 Artifact 备份恢复演练；
- 关键 E2E、项目架构说明和面试笔记。

#### 阶段出口

- 文献导入、RAG 和 Review Workflow 三条核心用户旅程可以从 UI 完成；
- Worker 崩溃、重复 Job、Provider 临时错误、取消竞争和 SSE 断线均有恢复测试；
- 用户 A 无法读取用户 B 的文献、Run、Evidence 和 Artifact；
- 每个用户可见错误可以通过 Correlation ID 诊断；
- 有真实执行得到的性能和评测基线，不使用虚构指标；
- 全新环境能够按文档启动和运行；
- 备份和恢复流程至少演练一次；
- 每个 Core 模块有学习笔记、已知限制和 60 秒面试说明。

### Phase 5：Deep Agents 集成验证

#### 目标

围绕一个明确研究任务，对已选定的 Deep Agents 进行受限 Spike，并通过 `ResearchAgentRuntime` Adapter 打通最小端到端 Agent Run。本阶段验证而不是重新选型：重点确认 MCP、Sandbox Backend、Checkpoint、取消、恢复和事件语义能否遵守平台边界，不自行开发通用 Agent Harness，也不以完整 Agent 产品为目标。

#### 候选用户故事

用户选择一个 Project 和研究目标，Agent 基于已有 Paper/Evidence 发现论文官方项目页、代码仓库、开放数据集和补充材料，生成 Resource Manifest 与带来源报告，并将一个公开资源作为隔离 Artifact 交回现有导入或 Artifact 流程。

#### 需要学习和验证

- Deep Agents 的运行模型、许可、精确版本和部署边界；
- SDK Conversation/Thread/Checkpoint/Workspace 与业务 Run 的区别；
- Runtime 事件、Usage、错误、审批和 Artifact 的归一化；
- ARQ、本系统 Run Control 与 SDK 内部重试/恢复的所有权；
- Browser、MCP、自定义 Tool 和 Sandbox 能力；
- Runtime 取消、超时、断连和结果对账；
- Prompt Injection、网络外泄和下载风险。

#### 最小垂直切片

```text
创建 AgentRun
  → Worker 调用 ResearchAgentRuntime Adapter
  → Runtime 读取最小授权的 Project/Evidence Context
  → 浏览一个允许访问的公开资源
  → 生成 Resource Manifest
  → 下载一个受策略限制的公开文件
  → 提交隔离 Artifact 和来源信息
  → 平台归一化 Event 并完成 Run
```

#### 阶段出口

- 选型 ADR 已记录选择理由；本阶段集成 ADR 记录实验证据、版本、Provider、部署方式和失败项；
- `ResearchAgentRuntime` 契约不泄漏具体 SDK 类型到 Domain 和公开 API；
- 业务 `AgentRun` 与 SDK Conversation/Thread/Workspace ID 有稳定映射；
- SDK 事件被筛选并映射为版本化业务 Event，不保存完整思考过程和敏感输出；
- 取消、超时、Runtime 断连和“SDK 成功但本地响应丢失”至少各有一次验证；
- 下载 Artifact 具有来源、内容哈希、大小、类型和 Project 所有权；
- 明确是否进入 Phase 6；若用例或运行时不成立，Core v1 仍保持完整可交付。

### Phase 6：Deep Agents 驱动的 Research Agent 与安全强化

#### 目标

基于 Phase 5 验证通过的 Deep Agents 集成，将研究任务扩展为可用、受限、可观察的 Research Agent，并系统验证 Browser、MCP、Tool、Workspace 和 Sandbox 的安全与可靠性。

#### 主要内容

- Research Agent 创建、详情、事件、审批、取消和 Artifact UI；
- Paper/Evidence、公开项目页、代码仓库、数据集和补充材料工具；
- Browser/URL Allow Policy、Redirect/SSRF 防护和下载隔离；
- Runtime Tool Policy、预算、重复或无进展检测；
- Workspace/Sandbox 生命周期、文件传输和资源限制；
- Agent Event、Usage、ToolExecution 和 Artifact 审计；
- Runtime 升级兼容测试、故障注入和 Agent 评测集；
- 如确有用户价值，再通过 ADR 加入受限 `run_python_analysis`。

#### 阶段出口

- Agent 只能访问当前 Run 授权的 Project Context、工具、网络目标和 Workspace；
- 不绕过登录、付费墙或 CAPTCHA，危险下载和不可逆操作必须审批；
- 网页、论文和仓库内容按不可信输入处理，Prompt Injection 不会获得平台 Secret 或数据库权限；
- 最大步骤、Token、费用、墙钟时间、Tool Call 和输出大小限制生效；
- Runtime 取消后不再发起新操作，重复执行不会重复提交最终 Artifact；
- Sandbox 默认不接触宿主文件和 Secret，网络与资源策略有测试证据；
- SDK 升级由契约测试保护，公开 API 不暴露 SDK 内部模型；
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

以下实现决策明确推迟到 Phase 5/6，而不是在 Core v1 中预先固定：

- Agent 的最终用户故事和是否进入正式产品；
- Deep Agents 的精确版本、升级策略和兼容范围；
- Runtime 部署在 Worker 内、独立 Agent Server 还是托管服务；
- SDK Thread、Checkpoint、Conversation 和 Workspace 的生命周期；
- Browser、MCP、Tool、网络和下载策略；
- Sandbox Provider、生命周期和最终部署参数；
- 是否开放受限 `run_python_analysis`。

这些决定应基于当前阶段的真实需求、实验和测试，而不是在尚未实现基础链路时猜测。

## 22. 完成定义

### 22.1 Core Research Backend v1

Core v1 完成需要同时满足：

- 文献导入、有引用 RAG 和固定 Review Workflow 三条路径可运行；
- 长任务可查询、重试、取消、暂停和恢复；
- Worker/HTTP 进程重启不会破坏业务事实；
- Event 可重放，页面刷新可恢复；
- 重要 Claim 的引用可定位到 Paper Version 和 Evidence；
- 用户和 Project 数据隔离有自动测试；
- 关键行为不依赖真实模型或实时学术 API 即可测试；
- Docker Compose 可复现启动；
- 关键日志、指标和 Trace 可用于诊断；
- Retrieval、Citation 和 Workflow 评测结果来自真实运行且可复现；
- Phase 1 至 Phase 4 的阶段 Spec、测试证据、模块笔记和复盘完成；
- 开发者能够不依赖 AI 解释核心架构、失败语义和设计取舍。

达到以上条件即可将项目作为完整的 Core Research Backend 交付，Research Agent Extension 不阻塞该结论。

### 22.2 Research Agent Extension

若 Phase 5 集成验证通过并继续 Phase 6，Agent 扩展完成还需要：

- 至少一个明确的论文相关资源发现用户故事端到端可运行；
- Agent SDK 通过 `ResearchAgentRuntime` Adapter 接入，不污染 Domain 和公开 API；
- 业务 Run、SDK Runtime、Workspace、Event 和 Artifact 的所有权清晰；
- Browser、Tool、下载和代码执行受 Schema、权限、审批、预算、网络与资源策略限制；
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
| 6 | Observability、E2E、评测、故障恢复 | 可部署、可演示、可复盘的 Core v1 |
| 7 | Agent SDK、Runtime Adapter、所有权 | 有 ADR 和运行证据的 Agent 集成 Spike |
| 8 | Browser、Tool、Sandbox、安全 | 受限且可观察的 Research Agent Extension |

如果某阶段无法用自己的话解释状态所有权和失败行为，应暂停增加功能，先完成实验和笔记。

## 24. 最终面试叙事

Core Research Backend 完成后，推荐使用下面的主线介绍：

> 我实现了一个面向文献综述的可靠研究后端。FastAPI 负责 API 和 SSE，ARQ/Valkey 负责异步任务投递，PostgreSQL 保存业务 Run、Event、文献、Evidence 和向量，LangGraph 管理固定综述 Workflow 的 Checkpoint、Interrupt 和 Resume。系统将业务 Run 与 LangGraph State 分离，按至少一次执行设计，通过条件更新、唯一约束、幂等键和内容哈希避免重复副作用。RAG 和综述生成采用 Evidence-first，重要 Claim 绑定具体论文版本、页码和证据，并在输出前进行引用校验。系统可以演示任务重试、取消、SSE 重放、人工确认、Worker 崩溃恢复和可复现评测。

Research Agent Extension 完成后，可以追加：

> 在核心后端稳定后，我没有重新实现通用 Agent Harness，而是通过 `ResearchAgentRuntime` Adapter 接入经过 Spike 和 ADR 选定的 Agent SDK。SDK 负责规划、浏览器和隔离 Workspace，本系统继续拥有业务 Run、权限、Event、Evidence 和 Artifact，从而让开放式 Agent 也能复用相同的可靠执行和审计能力。

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
- 业务 Run 与 SDK Conversation/Thread/Workspace 如何映射；
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
