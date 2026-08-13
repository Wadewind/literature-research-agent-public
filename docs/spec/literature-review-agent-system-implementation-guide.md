# 文献综述 Agent 系统：学习与开发实施指南

> 状态：Proposed v1
>
> 日期：2026-08-10
>
> 定位：面向单人、AI 辅助开发的总体实施文档；用于确定产品边界、总体架构、模块职责、阶段顺序和学习目标
>
> 技术方向：Python / FastAPI / LangGraph / PostgreSQL / pgvector / ARQ / Valkey / React

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

系统面向需要阅读、整理和撰写技术文献综述的用户，围绕一个 Research Project 提供三种执行模式：

1. **文献 RAG 问答**：针对项目内已经收录的文献进行有出处、可回跳原文的问答；
2. **综述 Workflow**：按照可观察、可暂停、可恢复的固定流程收集文献、提取证据、生成大纲并撰写带引用的综述；
3. **Research Agent**：在步数、预算、工具和权限约束下，自主检索文献、读取证据、分析数据、生成图表和导出文件。

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
- Agent Tool 的 Schema、权限、预算、副作用和沙箱边界；
- 日志、指标、Trace 和业务 Event 各自解决什么问题。

### 2.3 简历项目目标

完成后的项目应该能演示以下完整链路：

```text
上传或检索文献
  → 异步解析与索引
  → 有引用的文献问答
  → 创建长时间综述任务
  → 查看步骤、事件、失败与重试
  → 人工确认文献或大纲
  → 恢复工作流
  → 生成带引用的 Markdown/DOCX 和图表
```

项目介绍应强调可靠性、证据追溯和工程边界，不应宣称自动生成的内容等同于经过专家审查的系统性文献综述。

## 3. 项目边界

### 3.1 首版范围

首版需要完成：

- Research Project 和文献库；
- PDF 上传、解析、切分和向量化；
- OpenAlex 文献检索和 Crossref DOI 元数据校验；
- 基于项目文献库的 RAG 对话；
- Evidence 和 Citation 可追溯；
- 固定的文献综述 LangGraph Workflow；
- Workflow 暂停、恢复和人工确认；
- 受限的 Research Agent 和 Tool Registry；
- Markdown、图片等 Artifact 管理；
- 通用 Run、Attempt、Step 和 Event 模型；
- ARQ 后台 Worker、重试和协作式取消；
- SSE 实时事件与断线重放；
- 单节点 Docker Compose 部署；
- 关键行为的自动测试、故障注入和学习笔记。

### 3.2 明确非目标

首版不实现：

- Dify 式通用 App Builder；
- 可视化 Workflow Canvas；
- 用户自定义任意 DAG Node；
- 插件市场；
- 多 Agent、Swarm、辩论或动态 Agent 团队；
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
- Research Agent Runs；
- Evidence、Citation 和 Artifact。

所有用户可见查询必须在 Project 和用户所有权范围内执行。

### 4.2 Paper 与 Paper Version

Paper 表示稳定的学术作品身份和书目信息，例如 DOI、标题、作者、年份和来源。

Paper Version 表示系统实际处理过的一份全文版本。重新上传、重新解析或更换解析器时，不应悄悄覆盖旧版本。Evidence 必须指向具体 Paper Version，避免文档重新解析后页码和 Chunk 变化导致引用失效。

### 4.3 Conversation 与 Agent Session

Conversation 保存用户和系统之间的对话历史。Agent Session 是某次 Agent 执行需要的工作上下文，可能关联 Conversation，但不等同于后台业务 Run。

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

首版采用单仓库、单节点、少量进程的模块化单体：

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
               │               │ workflow / agent │
               │               └────────┬─────────┘
               │                        │
               ▼                        ▼
┌──────────────────────────┐   ┌───────────────────────────┐
│ PostgreSQL + pgvector    │   │ External Adapters         │
│ business state / events │   │ LLM / OpenAlex / Crossref │
│ vectors / checkpoints   │   │ parser / sandbox          │
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

Sandbox、Caddy 和可观测性后端在后期通过可选 Compose Profile 加入。

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
  repositories / queue / model / parser / storage / sandbox
        ↓
Adapters
  PostgreSQL / ARQ / LangGraph / HTTPX2 / Docling / filesystem
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
| Python | CPython 3.13、uv | 统一 API、Worker、Agent 和数据处理开发体验 |
| API | FastAPI、Pydantic v2、Uvicorn | REST、上传、SSE、边界校验 |
| Agent/Workflow | LangGraph 1.2.x | 状态图、Checkpoint、Interrupt、Resume |
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

不负责业务 Run 状态和 Agent 权限判断。Tool 权限由 Tool Policy 模块负责。

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

这是三个模式共享的后端核心，负责：

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
  → 搜索文献
  → 元数据归一化与去重
  → 人工确认纳入/排除
  → 获取并解析全文
  → 提取 Evidence
  → 主题组织与大纲
  → 人工确认大纲
  → 分章节撰写
  → 引用和一致性校验
  → 导出 Artifact
```

首版图结构由代码定义并版本化，不提供用户自定义 Canvas。

### 7.12 Research Agent

负责：

- Agent Loop 的 LangGraph 表达；
- Tool 选择和 Observation；
- 最大步数、Token、成本和墙钟时间；
- 重复 Tool Call 检测；
- Steering/取消检查；
- 最终回答和 Artifact 生成。

Agent 只能调用注册工具，不能直接获得数据库、文件系统或宿主机访问权限。

### 7.13 Tool Registry 与 Tool Execution

负责：

- Tool 名称、版本、说明和 JSON Schema；
- Tool 参数验证；
- 用户、Project、Run 和 Policy Context；
- 幂等 Tool Call；
- 超时和输出大小限制；
- ToolExecution 记录；
- 危险操作审批；
- Sandbox Adapter。

首版工具应限定为文献领域和 Artifact 生成能力。

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

## 8. 三种模式的运行方式

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

### 8.3 Agent 模式

```text
用户目标
  → Agent State
  → Model Decision
  → Tool Call Schema Validation
  → Policy/Budget Check
  → Execute Tool
  → Persist ToolExecution + Artifact/Event
  → Observation
  → 下一轮或结束
```

Agent 模式比 Workflow 更开放，因此必须更严格限制：

- 可见工具集合；
- 最大步骤；
- 单 Tool 和总墙钟时间；
- Token 和费用；
- Tool 输出大小；
- 重复或无进展循环；
- 代码执行权限；
- 人工审批点。

## 9. 状态与持久化原则

### 9.1 业务事实来源

PostgreSQL 保存：

- 用户和 Project；
- Paper、文献版本和索引元数据；
- Conversation 和 Message；
- Run、Attempt、Step、Event；
- Evidence、Citation；
- ToolExecution；
- Artifact 元数据；
- Token/费用使用；
- 幂等记录。

### 9.2 LangGraph Checkpoint

LangGraph Checkpoint 保存：

- 当前图位置；
- 小型结构化 State；
- Node/Task 已完成结果；
- Interrupt 和 Resume 所需数据。

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
              ├→ WAITING_INPUT → QUEUED/RUNNING
              └→ CANCEL_REQUESTED → CANCELLED

QUEUED → CANCELLED
WAITING_INPUT → CANCELLED
```

最终状态为 `SUCCEEDED`、`FAILED`、`CANCELLED`。详细合法转换、并发优先级和数据库实现必须在 Run Control 阶段 Spec 中确定。

### 10.2 Event 类别

最低需要覆盖：

- Run 生命周期；
- Attempt 生命周期；
- Step/Node 生命周期；
- 文档解析和索引进度；
- 模型请求完成和 Usage；
- Retrieval 摘要；
- Tool Call 生命周期；
- Artifact 创建；
- 等待和完成人工输入；
- 重试、取消和错误。

Event Payload 只保存前端和审计需要的信息，不保存完整 Prompt、密钥、PDF 全文或敏感 Tool 参数。

### 10.3 至少一次与幂等

系统假设 Job 和外部调用可能重复。幂等至少分为：

1. API 提交幂等；
2. Job 执行幂等；
3. LangGraph Node/Task 幂等；
4. Tool 副作用幂等；
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

不要直接把所有 Chunk 拼成上下文后要求模型一次性生成完整综述。

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

首版优先调用固定学术 API，不提供任意 URL 抓取。后续增加 URL Tool 时必须考虑 SSRF、DNS 重绑定、Redirect 和内网地址阻断。

### 12.4 代码执行

首版先提供固定图表工具，不提供任意代码。

后期 `run_python_analysis` 必须通过 Sandbox Adapter，限制：

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

长任务创建返回 `202 Accepted` 和稳定 `run_id`。查询和事件接口使用业务 ID，不暴露 ARQ 或 LangGraph 内部表。

### 13.2 Web 页面

首版页面：

- Project 列表和详情；
- Literature Library；
- Paper 详情和 PDF 阅读；
- RAG Chat；
- Review 创建和 Run 详情；
- Research Agent；
- Artifact 查看和下载；
- 设置和模型配置。

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
tool_execution_id
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
- Sandbox 超时和拒绝。

### 14.4 Trace

使用 OpenTelemetry 连接 API、Queue、Worker、LangGraph Node、模型、Retrieval 和 Tool。LangSmith 可以在开发阶段用于 Graph 调试，但不作为系统唯一的业务审计或生产可观测性来源。

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
│  │  ├─ agents/
│  │  ├─ tools/
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
│  └─ tools/
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
- Tool Schema、权限和预算；
- 用户和 Project 隔离；
- Bug 修复。

文档、纯 UI 样式和探索性 Spike 不要求机械执行 Red-Green-Refactor，但 Spike 不能直接视为生产实现。

## 17. 分阶段实施路线

### Phase 0：项目重置、技术验证与开发基线

#### 目标

建立新的 Python 技术方向和快速反馈环境，不开发文献业务功能。

#### 需要学习

- FastAPI Lifespan 和依赖注入；
- asyncio Task、取消和资源生命周期；
- SQLAlchemy AsyncSession 和事务；
- ARQ Worker 基本执行模型；
- LangGraph State/Node/Edge/Checkpoint 的最小示例；
- Docker Compose 服务依赖和健康检查。

#### 交付内容

- Python/FastAPI 后端骨架；
- React/Vite Web 骨架；
- PostgreSQL/pgvector 和 Valkey Compose；
- Alembic 初始迁移；
- API 与 Worker 健康检查；
- Fake Chat、Fake Embedding、Fake Queue/Sandbox；
- 格式化、静态检查、单元和集成测试命令；
- CI；
- Phase 0 Spec 和学习复盘。

#### 不做

- Paper、RAG、Workflow 或 Agent；
- 真实 Provider 请求；
- 通用框架抽象；
- Sandbox。

#### 阶段出口

- API、Worker、PostgreSQL 和 Valkey 可通过 Compose 启动；
- CI 无外部模型 Key 也能通过；
- 数据库迁移可重复执行；
- 一个测试 Job 能被 Worker 消费；
- 一个最小 LangGraph 可以使用测试 Checkpointer 暂停和恢复；
- 开发者能画出 API、Queue、Worker、数据库和 LangGraph 的边界。

### Phase 1：Project、文献库与可靠异步导入

#### 目标

用户创建 Project、上传 PDF，并看到文档解析和索引任务从创建到成功、失败或取消的完整过程。

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
- OpenAlex/Crossref Adapter；
- Run Step；
- Human Input；
- Evidence Matrix；
- Review Artifact。

#### 首版 Workflow

```text
研究问题
  → 检索策略
  → 文献搜索/去重
  → 人工筛选
  → 全文准备
  → Evidence 提取
  → 主题与大纲
  → 人工确认大纲
  → 分节撰写
  → 引用/一致性校验
  → Markdown 导出
```

#### 阶段出口

- Workflow 可以在两个明确节点等待人工输入；
- Worker 重启后能从持久 Checkpoint 恢复；
- 恢复不会重复创建 Paper、Evidence 或 Artifact；
- 每个业务阶段在 Run Detail 中可观察；
- 失败节点提供稳定错误码和可重试判断；
- 最终综述每个主要 Claim 可追溯到 Evidence；
- 输出包含检索和纳入文献清单；
- 学习笔记能解释 Checkpoint 为什么不等同于业务 Run 数据。

### Phase 4：受限 Research Agent 与 Artifact 工具

#### 目标

用户提供开放研究任务，Agent 可以在限制内选择文献工具、分析数据、生成图表并输出文件。

#### 需要学习

- Agent Loop 的图表示；
- Tool Schema 和 Tool Registry；
- Tool 副作用与幂等；
- Agent 预算；
- 重复调用和无进展检测；
- Artifact Workspace；
- Tool Event 和可观察性。

#### 初始工具

- 搜索文献；
- 获取并校验文献元数据；
- 检索 Project Evidence；
- 读取 Evidence；
- 创建或更新大纲；
- 写 Markdown Artifact；
- 使用固定 Schema 生成图表；
- 导出引用数据。

#### 阶段出口

- Agent 只能看到注册且授权的工具；
- 所有 Tool 参数经过 JSON Schema 校验；
- ToolExecution 可查询并与 Run/Step 关联；
- 最大步骤、Token、时间和 Tool Call 数生效；
- 重复调用达到阈值后终止或要求调整；
- Artifact 写入使用受控 Workspace 和稳定 ID；
- Agent 取消后不再发起新的 Tool Call；
- 学习笔记能解释为什么 Agent Runtime 不能直接访问数据库和宿主文件系统。

### Phase 5：Sandbox、安全与可靠性强化

#### 目标

在不扩大 Agent 权限的前提下加入受限 Python 分析能力，并系统验证重试、崩溃、取消和隔离行为。

#### 需要学习

- 容器和真正 Sandbox 的区别；
- 系统调用、网络和资源限制；
- SSRF；
- Tool 审批；
- Transactional Outbox 或等价一致性方案；
- Lease/Heartbeat；
- 故障注入；
- 安全日志和 Secret 管理。

#### 主要内容

- Dify Sandbox Adapter 或经评审的等价服务；
- `run_python_analysis` Tool；
- 代码、输入文件和输出 Artifact 契约；
- Tool 风险等级和审批；
- API/Worker/Provider/Sandbox 故障注入；
- Event 通知丢失恢复；
- 安全和跨用户隔离测试。

#### 阶段出口

- 代码执行默认禁网、限制资源且不接触宿主 Secret；
- Sandbox 超时和异常有稳定错误分类；
- 重复 Sandbox Job 不会重复提交最终 Artifact；
- Queue、Worker、数据库、Provider 和通知故障均有测试证据；
- 用户 A 无法读取用户 B 的文献、Run、Evidence 和 Artifact；
- 学习笔记能解释 Exactly Once 的限制和当前 Effectively Once 方案。

### Phase 6：产品闭环、可观测性与评测

#### 目标

完成可演示、可诊断、可复盘的端到端产品，并形成简历和面试材料。

#### 需要学习

- OpenTelemetry Trace；
- Prometheus 指标设计；
- 高基数问题；
- RAG/Agent 评测集；
- E2E 测试边界；
- 单机部署、备份和恢复；
- 性能基线和容量假设。

#### 主要内容

- 完整 Project、Library、Chat、Review、Agent、Run 和 Artifact UI；
- JSON 日志、Metrics 和 Trace；
- 固定评测集及结果报告；
- Docker Compose 部署和运维文档；
- PostgreSQL 和 Artifact 备份恢复演练；
- 关键 E2E；
- 项目架构说明和面试笔记。

#### 阶段出口

- 三条核心用户旅程可以从 UI 完成；
- 每个用户可见错误可以通过 Correlation ID 诊断；
- 有真实执行得到的性能和评测基线，不使用虚构指标；
- 全新环境能够按文档启动和运行；
- 备份和恢复流程至少演练一次；
- 每个核心模块有学习笔记和 60 秒面试说明；
- 已知限制、技术债和下一步扩展明确记录。

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
├─ 09-tool-runtime-and-budget.md
├─ 10-sandbox-and-side-effects.md
├─ 11-sse-replay-and-frontend-state.md
└─ 12-observability-and-evaluation.md
```

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
- Contract Test：HTTP、Event、Tool 和模型结构化输出；
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

至少覆盖：

- Worker 在步骤前后退出；
- Provider 超时、429 和 5xx；
- 外部请求成功但本地响应丢失；
- 同一 Job 重复投递；
- 相同 Idempotency Key 不同请求；
- Event 通知丢失；
- SSE 断线重连；
- 用户取消与步骤成功并发；
- Checkpoint 后恢复；
- Parser 和 Sandbox 超时；
- 跨用户访问；
- 无效或伪造 Citation。

## 20. 架构决策触发条件

以下变化必须先写 ADR：

- ARQ/Valkey 更换为 Celery、RabbitMQ、Temporal 或其他队列；
- PostgreSQL 向量检索更换为专用向量数据库；
- 本地文件存储更换为 S3；
- 模块化单体拆分为独立服务；
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
- 人工筛选和大纲确认的 UI；
- Sandbox 的最终部署参数；
- Citation Style 和 DOCX 模板；
- Metrics Bucket 和告警阈值。

这些决定应基于当前阶段的真实需求、实验和测试，而不是在尚未实现基础链路时猜测。

## 22. 完成定义

项目总体完成需要同时满足：

- 文献导入、RAG、Workflow 和 Agent 三种路径可运行；
- 长任务可查询、重试、取消、暂停和恢复；
- Worker/HTTP 进程重启不会破坏业务事实；
- Event 可重放，页面刷新可恢复；
- 引用可定位到 Paper Version 和 Evidence；
- Tool 和代码执行受 Schema、权限、预算和资源限制；
- 用户数据隔离有自动测试；
- 关键行为不依赖真实模型即可测试；
- Docker Compose 可复现启动；
- 关键日志、指标和 Trace 可用于诊断；
- 评测结果来自真实运行且可复现；
- 每个阶段完成 Spec、测试证据、模块笔记和复盘；
- 开发者能够不依赖 AI 解释核心架构、失败语义和设计取舍。

## 23. 建议学习节奏

不建议以固定周数作为硬性承诺。建议采用“一个概念主题 + 一个可演示垂直切片”的节奏：

| 顺序 | 学习主题 | 对应产物 |
|---|---|---|
| 1 | FastAPI Async、Worker、事务 | 可消费测试 Job 的开发基线 |
| 2 | Run、Event、幂等、取消 | 可恢复的 PDF 导入任务 |
| 3 | 文档结构、Embedding、检索 | 可评测的文献检索 |
| 4 | Evidence、Citation、SSE | 有引用的 RAG 问答 |
| 5 | LangGraph、Checkpoint、Interrupt | 可暂停恢复的综述 Workflow |
| 6 | Tool、预算、副作用 | 受限 Research Agent |
| 7 | Sandbox、安全、故障注入 | 受控 Python 分析和可靠性报告 |
| 8 | Observability、E2E、评测 | 可部署、可演示、可复盘的项目 |

如果某阶段无法用自己的话解释状态所有权和失败行为，应暂停增加功能，先完成实验和笔记。

## 24. 最终面试叙事

项目完成后，推荐使用下面的主线介绍：

> 我实现了一个面向文献综述的单节点 Agent 后端。FastAPI 负责 API 和 SSE，ARQ/Valkey 负责异步任务投递，PostgreSQL 保存业务 Run、事件、文献和向量，LangGraph 管理综述工作流与 Agent 的 Checkpoint 和暂停恢复。系统将业务 Run 与 LangGraph State 分离，按至少一次执行设计，通过状态条件更新、唯一约束、幂等键和内容哈希避免重复副作用。生成过程采用 Evidence-first，所有主要 Claim 都绑定具体论文版本、页码和证据，并在导出前进行引用校验。Agent 只能调用注册工具，代码分析通过受限沙箱执行。整个系统可以演示任务重试、取消、SSE 重放、人工确认和 Worker 崩溃恢复。

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
- Sandbox 与普通 Docker 容器有什么差别。

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
- [OpenAlex API](https://developers.openalex.org/api-reference/introduction)
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
- [Dify Sandbox](https://github.com/langgenius/dify-sandbox)
