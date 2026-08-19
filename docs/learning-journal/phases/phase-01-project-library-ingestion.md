# Phase 1：Project、文献库与可靠异步导入

## 状态

进行中。切片 1–6（工程基线、Project、Run/Event、上传与版本、可靠投递、Fake Parser 闭环）已完成，切片 7（真实 Parser）进行中。Spec 初版日期：2026-08-13。

本阶段是第一个正式业务阶段。

## 目标和用户可见结果

用户可以创建 Research Project、上传 PDF 论文，并在请求断开或页面刷新后继续查看导入任务的状态和历史事件。导入成功后，用户可以按文档结构查看解析后的 Element，并从 Element 定位回原始 PDF 页码；任务也可以明确地失败、重试或被取消。

```text
PDF
  → 校验、存储和版本识别
  → 异步版面解析
  → 版本化结构化文档
  → Element 与 PDF 页码/坐标的来源映射
```

Phase 1 交付可供后续模块复用的“文档内容层”。Phase 2 从该层生成 RAG ChunkSet 和检索索引；Phase 3 从同一层生成章节或 Workflow Reading Pack，二者都不重新解析 PDF。

## 范围决定：解析不等于检索索引

总体实施指南在 Document Ingestion 和 Phase 1 中使用了“Elements/Chunks”“向量化”和“索引”，而 Phase 2 又负责 Embedding、Chunk 和 Retrieval。为消除冲突，本阶段明确：

- “解析和索引”仅指解析结果入库，以及为所有权、版本、页码和章节查询建立普通数据库索引；
- `Element` 是解析得到的稳定文档单元，属于 Phase 1；
- Phase 1 不持久化通用 Markdown/HTML 阅读视图，预览直接根据 Element 渲染；
- Phase 1 不创建面向检索或模型上下文的 `Chunk`；
- Embedding、pgvector、全文检索、Hybrid Retrieval 和 tokenizer-aware Chunking 全部属于 Phase 2；
- Phase 1 不调用 LLM。

这是阶段 Spec 对总体指南暂缓决策的细化，不改变总体架构，因此当前不单独创建 ADR。

## 范围

### 包含

- 支撑本阶段所需的最小生产基线：FastAPI、配置与生命周期、live/ready、PostgreSQL/Valkey Compose、Alembic、ARQ Worker、测试和质量命令；
- 最小可信身份上下文、Project CRUD 中的创建/列表/详情和所有权过滤；
- Paper、Project 收录关系、不可变 Paper Version 和文件内容哈希；
- PDF 上传校验、受控文件存储和幂等提交；
- Ingestion Run、Attempt、Event、重试、取消和 Worker lease；
- 数据库提交与 Queue 投递之间的持久化 Outbox；
- Docling 主解析 Adapter 和必要时的 pypdf 降级 Adapter；
- Parser 原生结果、统一 Document Element、章节层级、阅读顺序和来源定位；
- Run/Event 查询、SSE 历史重放和实时通知；
- 最小 Project、文献上传、Run 详情和 Element/PDF 来源预览页面；
- 关键自动测试、阶段复盘和已完成模块的学习笔记。

### 不包含

- Embedding、pgvector 向量、全文检索、Hybrid Retrieval、RAG Chunk 和上下文预算；
- LLM 阅读、摘要、Evidence、Citation、RAG Conversation 和 Review Workflow；
- OpenAlex/Crossref、任意 URL 导入、自动下载、Browser、Tool 或 Sandbox；
- 完整登录、复杂成员/RBAC、DOCX 导出和通用 Workflow/Artifact 平台；
- 为后续阶段提前建设 Agent Runtime 或 Tool Registry。

## 核心模型和复用边界

### Paper、Version 和 Parse Revision

```text
Paper                         稳定的学术作品身份和书目信息
└─ Paper Version              系统收到的一份不可变 PDF 字节版本
   └─ Document Parse Revision parser/version/config 对该 PDF 的不可变解析结果
      └─ Elements + Source Locations
```

- 文件内容变化创建新的 Paper Version，旧版本不覆盖；
- PDF 不变但 Parser、配置或 OCR 策略变化时创建新的 Parse Revision；
- 相同 Paper Version 和 `parser_profile_hash` 已有成功结果时可以复用；
- 当前 Parse Revision 通过显式引用选择，历史结果仍可查询；
- Phase 2 的 ChunkSet 必须引用具体 Parse Revision，不能只引用 Paper。

Phase 1 允许 Paper 元数据不完整。PDF 中提取的标题等信息必须记录来源；DOI、作者和年份未经本地或外部元数据校验不能视为已验证事实。

### Element

Element 是规范化后的文档单元，例如标题、章节标题、段落、列表项、表格、公式、图片、题注、页眉和页脚。最低信息包括：

- `element_id`、所属 Parse Revision、类型和全文阅读顺序；
- 可选父 Element、`section_path`、规范化文本或受控结构化 Payload；
- 一个或多个来源定位：PDF 页码、可选 Bounding Box、Parser 原始引用和可选字符范围；
- 内容哈希及解析质量/警告。

业务代码依赖项目自己的 Element Schema，不直接暴露 Docling 类型。无法可靠获得坐标时保留更粗粒度定位并显式标记，不伪造精度。

### 阅读视图和序列化

Phase 1 不保存完整 Markdown，也不建立 Markdown Segment 映射。UI 根据 Element 类型、层级和阅读顺序直接渲染预览，稳定回溯链只有：

```text
Element ID → PDF page + optional bounding box
```

LLM 和 RAG 需要的是针对具体任务组织后的内容，而不是预先固定的 Markdown 文件。Phase 2 的 Chunk Builder 和 Phase 3 的 Workflow Reader 可以从 Element 生成各自的 `retrieval_text`、Markdown 风格章节或其他模型输入，并直接保存所引用的 Element ID。

如果后续出现下载、人工编辑、输入复现或已测量的缓存需求，再把 Markdown 作为带 Serializer/Profile 版本的派生产物加入对应阶段；在此之前不创建通用 `DocumentSerializer` 实现或持久化模型。

### Chunk

Chunk 是为检索、Embedding 或特定模型上下文组合一个或多个 Element 得到的消费单元，依赖 tokenizer、模型限制和检索目标。因此 Phase 1 不创建 Chunk 表，也不把单个 Element 改名为 Chunk。

### Parser、数据库和 Storage

`DocumentParser` Port 接受受控 Storage Object 引用和 Parse Profile，输出项目定义的规范化结果：

- Docling 是主 Parser；
- pypdf 仅作显式降级，缺少布局、表格或坐标的结果标记 `degraded`；
- PDF、完整 Parser JSON 和较大图片/表格保存到 Storage；
- PostgreSQL 保存业务状态、版本元数据、可查询 Element、来源映射和 Storage Key；
- PDF/全文不进入 Run/Event Payload 或 LangGraph State；
- Phase 1 不使用 LangGraph。

内部 `DocumentContentReader` Port 按授权上下文提供目录/章节树、Section 或 Page 的 Element 流，以及 Element 来源位置。Phase 2/3 通过此 Port 复用 Parse Revision，不能读取 Parser 原生 JSON、宿主路径或绕过所有权校验。

## 身份、去重和隐私

首版不建设完整登录，但所有 Repository 查询从一开始携带可信 `owner_id`：

- Actor Context 由可替换的身份依赖提供，请求体不能声明 owner；
- 本地开发使用显式配置的开发用户，生产配置不得意外启用；
- 测试至少注入两个用户验证隔离；
- 逻辑去重限定在用户可见范围，不泄漏其他用户是否上传过相同文件；
- 首版 Storage Key 按 owner 隔离，不优化跨用户物理 Blob 去重。

## 初步数据关系

具体字段和索引在每个切片前通过迁移和测试确定，关系至少包括：

```text
Project ── ProjectPaper ── Paper
                            └─ PaperVersion ── StoredObject(PDF)
                               └─ DocumentParseRevision
                                  ├─ DocumentElement
                                  └─ ElementSourceLocation

Run ── Attempt
 ├─ Event(run_id, sequence)
 └─ QueueOutbox
```

唯一约束至少保护 Project 收录、所有权范围内的 PDF 哈希、Parse Profile 结果、Element 顺序、`(run_id, sequence)`、API Idempotency Key、Outbox Effect 和 Worker 结果提交。

## API 和 Event 契约方向

具体 Pydantic 字段在对应切片测试前定稿，资源先确定为：

```text
GET    /health/live
GET    /health/ready
POST   /api/v1/projects
GET    /api/v1/projects
GET    /api/v1/projects/{project_id}
POST   /api/v1/projects/{project_id}/paper-files
GET    /api/v1/projects/{project_id}/papers
GET    /api/v1/projects/{project_id}/papers/{paper_id}
GET    /api/v1/projects/{project_id}/paper-versions/{version_id}/document
GET    /api/v1/projects/{project_id}/paper-versions/{version_id}/elements
GET    /api/v1/runs/{run_id}
POST   /api/v1/runs/{run_id}/cancel
GET    /api/v1/runs/{run_id}/events
GET    /api/v1/runs/{run_id}/events/stream
```

- 上传使用 `multipart/form-data` 和 `Idempotency-Key`，接受后返回 `202` 和稳定 `run_id`；
- 相同 Key/请求返回同一结果，相同 Key/不同请求返回稳定冲突错误；
- Element 查询支持受控的 Page、Section 和分页参数；
- SSE 以 Event Sequence 为游标，支持 `Last-Event-ID` 或等价参数；
- 不可见资源不泄漏所有权信息，错误包含稳定业务码和 Correlation ID。

Event Envelope 最低包含 `event_id`、`event_version`、`event_type`、`run_id`、`sequence`、`occurred_at`、`actor_type`、`correlation_id` 和小型 Payload。Event 覆盖 Run/Attempt 生命周期、解析阶段、降级警告和结果提交摘要，不保存 PDF、Element 全文、Parser JSON 或原始堆栈。

PostgreSQL Event 是历史事实；Valkey Pub/Sub 只发送可丢失的“有新事件”通知。

## Run 状态、失败和取消

```text
QUEUED → RUNNING → SUCCEEDED
   │         ├→ RETRY_WAIT → QUEUED
   │         ├→ FAILED
   │         └→ CANCEL_REQUESTED → CANCELLED
   └→ CANCELLED

RETRY_WAIT → CANCELLED
```

`SUCCEEDED`、`FAILED` 和 `CANCELLED` 是终态。转换使用条件更新或行锁；状态和对应 Event 在同一短事务提交。

Phase 1 不预建通用 Step 引擎，先用稳定进度 Event 表达 `file_validated`、`parse_started`、`parse_completed`、`normalize_completed` 和 `result_committed`。只有 UI/查询证明需要时才加入独立 Step。

失败语义：

- 非 PDF、超限、损坏/加密且无法处理属于永久输入或解析错误；
- 数据库、Valkey 或受控资源短暂不可用属于临时基础设施错误；
- Worker lease 过期由恢复任务对账，创建新 Attempt 或按预算失败；
- pypdf 得到可用文本可以降级成功，但必须记录能力缺失；
- 同一错误只有一层主导重试，ARQ Result 不作为业务事实。

取消语义：

- `QUEUED`/`RETRY_WAIT` 可以原子进入 `CANCELLED`；
- `RUNNING` 先原子写 `CANCEL_REQUESTED` 和 Event；
- Worker 在 Parser 前后、规范化前、渲染前和提交前检查取消；
- 已进入的 Parser 调用可能运行至超时或返回，但取消后不能提交新的当前结果；
- 成功提交与取消并发时由条件转换产生唯一终态。

## 事务、投递和安全不变量

- Route 不直接写 SQL；Application Service 明确管理事务；
- 上传流式执行硬大小限制、PDF Magic Bytes/MIME 校验和 SHA-256；文件名仅作清理后的展示信息；
- Storage Key 不可猜测，也不由用户文件名拼接；
- 创建 Run、首个 Event 和 Queue Outbox 在同一事务；Outbox 可以安全重复投递；
- ARQ Job 只携带 `run_id`，Worker 从 PostgreSQL 读取事实；
- Parser 等长操作不在数据库事务内，并有超时、资源和输出上限；
- 解析产物先 staged，Parse Revision/Element 元数据、Run 终态和最终 Event 原子提交，避免暴露半成品；
- 重复 Job、投递或响应丢失不能创建第二个当前 Parse Revision；
- Event Sequence 在 Run 内严格递增；
- 不宣称 Exactly Once，通过唯一约束、内容哈希、条件更新和幂等键实现 Effectively Once；
- 日志/Event 不记录论文全文、宿主路径或敏感配置；
- 测试只提交合成或公开许可 PDF，用户本地未跟踪论文不得加入仓库；
- Phase 1 不接受任意 URL，不引入 SSRF 面。

## 实现切片顺序

每个切片遵循“确认契约与不变量 → 失败测试 → 最小实现 → 重构 → 验证 → 更新进度”。

1. **工程基线**（已完成）：FastAPI、配置/Lifespan、live/ready、Compose、SQLAlchemy/Alembic、ARQ 和质量命令；
2. **Project 闭环**（已完成）：Actor Context、Domain/Repository/Application/API 和隔离测试；
3. **Run/Event 核心**（已完成）：状态机、Event Sequence、事务 Repository 和并发测试；
4. **上传与版本**（已完成）：流式校验、Storage、Paper/Paper Version、哈希和 API 幂等；
5. **可靠投递**（已完成）：Queue Outbox、`run_id` Job、重复 Job 和投递故障；
6. **Fake Parser 闭环**（已完成）：Run → Parse Revision → Element/来源定位 → 成功；
7. **真实 Parser**：Docling、pypdf 降级、PDF Fixtures、超时和质量标记；
8. **取消/恢复**：错误分类、Attempt、lease/heartbeat 和异常 Run 对账；
9. **Event/SSE**：历史分页、Sequence 游标、通知丢失和断线重放；
10. **最小 Web UI**：Project、Library、上传、Run Detail、取消和 Element/PDF 来源预览；
11. **验收复盘**：Compose Smoke、E2E、故障注入和模块学习笔记。

已完成切片的详细契约保留如下，作为实现记录和后续切片的参照。

### 切片 4：上传与版本

#### 目标

用户可通过 `POST /api/v1/projects/{project_id}/paper-files` 上传 PDF，服务端完成校验、存储、创建 Paper/PaperVersion、生成幂等的 Ingestion Run，并返回 `202 Accepted` 和稳定 `run_id`。本切片只把文件“收”进系统，不触发 Worker 解析。

#### 范围

- **包含**：multipart 上传、`Idempotency-Key` 处理、PDF Magic Bytes / MIME / 大小校验、文件名清理、SHA-256、本地 Storage Port/Adapter、`Paper`/`PaperVersion` 持久化、`IdempotencyKey` 持久化、Ingestion `Run` + `run_created` Event、API `202` 响应。
- **不包含**：Worker 消费、Queue Outbox、Parser、Element、跨用户物理去重、书目元数据提取、项目外 Paper 复用。

#### API 契约

```text
POST /api/v1/projects/{project_id}/paper-files
Headers:
  Idempotency-Key: <string>        # 必填，调用方提供的幂等键
Content-Type: multipart/form-data
Body:
  file: <PDF binary>

202 Accepted
{
  "run_id": "<uuid>",
  "paper_id": "<uuid>",
  "version_id": "<uuid>",
  "status": "queued"
}
```

错误响应：
- `400`：`Idempotency-Key` 缺失、文件非 PDF、超过大小限制；
- `404`：Project 不存在或不属于当前 actor；
- `409`：相同 `Idempotency-Key` 但请求指纹不同；
- `413`：请求体超过全局限制。

#### 数据模型

新增表：

- `papers`：`paper_id`（PK）、`owner_id`、`project_id`（FK）、`created_at`。
- `paper_versions`：`version_id`（PK）、`paper_id`（FK）、`file_hash`、`storage_key`、`size_bytes`、`content_type`、`created_at`。
- `idempotency_keys`：`owner_id` + `idempotency_key`（联合唯一）、`project_id`、请求指纹 `request_hash`、关联 `run_id`。

#### 关键不变量

1. 文件校验（Magic Bytes、大小）必须先于存储；
2. SHA-256 在事务外计算；
3. `Paper`、`PaperVersion`、`Run`、`Event`、`IdempotencyKey` 在同一短事务提交；
4. Storage Key 由系统生成，不由用户文件名拼接，且不暴露宿主路径；
5. 同一 `Idempotency-Key` + 相同请求指纹返回同一 `run_id`；不同指纹返回 `409`；
6. Project 所有权校验在事务内完成。

#### 实现顺序

1. 定义 `Storage` Port 与本地文件系统 Adapter；
2. 定义 `Paper`、`PaperVersion` 领域模型与异常；
3. 新增 ORM 与 Alembic 迁移；
4. 实现 Repository Port（Paper、PaperVersion、IdempotencyKey）与 PostgreSQL Adapter；
5. 实现 `IngestionService.upload_paper_file` 编排校验、存储、幂等和 Run 创建；
6. 实现 `POST /api/v1/projects/{project_id}/paper-files` 路由；
7. 配置 `storage_root`、`max_upload_size_bytes` 并在 lifespan 注入 Storage；
8. 编写 Domain/Application/API/PostgreSQL 集成测试。

#### 测试要点

- 合法 PDF 返回 `202`，生成 Paper/PaperVersion/Run/Event，文件落地；
- 非 PDF、超尺寸、缺失 `Idempotency-Key` 返回 `400`；
- 相同 key + 相同文件返回同一 `run_id`；
- 相同 key + 不同文件返回 `409`；
- Project 不存在或不属于当前 actor 返回 `404`；
- Repository 层唯一约束、跨用户隔离；
- Storage Adapter 路径穿越防护。

### 切片 5：可靠投递（Queue Outbox + ARQ Worker）

#### 目标

Run 创建后能被可靠地交给后台 Worker 执行：数据库提交与队列投递之间有持久化 Outbox，Worker 崩溃、队列故障或重复投递不产生重复执行或丢失任务。本切片执行体为占位实现（QUEUED → RUNNING → SUCCEEDED/FAILED），真实解析在切片 6 接入。

#### 范围

- **包含**：`QueueOutbox` 领域模型/ORM/迁移；上传事务内写入 Outbox；`RunQueue` Port 与 ARQ 适配器；Outbox 派发服务（轮询、退避、最大尝试次数）；`RunExecutionService` 幂等执行；`python -m literature_agent.worker` Worker 入口（ARQ Job + 派发循环）；lifespan 注入队列；Compose 增加 valkey 与 worker。
- **不包含**：真实 Parser 执行体、Attempt/lease、Worker 崩溃后 RUNNING Run 的对账恢复（切片 8）、SSE 通知。

#### 数据模型

新增表 `queue_outbox`：

- `outbox_id`（PK）、`run_id`（FK → runs，唯一）、`status`（`pending`/`dispatched`/`failed`）；
- `attempt_count`、`scheduled_at`（下一次允许派发的最早时间）、`dispatched_at`、`created_at`、`updated_at`；
- `run_id` 唯一约束保证一个 Run 最多一条投递记录。

#### 关键不变量

1. Run、`run_created` Event 和 Outbox 记录在同一事务提交；
2. ARQ Job 只携带 `run_id`，并以 `run:<run_id>` 作为 Job ID 在队列内去重；
3. 外部队列调用不发生在数据库事务内；每条 Outbox 记录的标记独立提交；
4. 投递成功但标记前崩溃 → 记录保持 `pending`，下一轮补投，重复 Job 安全；
5. 投递失败按指数退避（1s 起、上限 60s）推迟，达到 `outbox_max_attempts` 后进入 `failed` 终态；
6. Worker 只认领 `QUEUED` 的 Run，重复 Job 跳过；执行期间并发取消不产生第二个终态；
7. ARQ `max_tries = 1`，重试只有 Outbox 退避一层主导，ARQ Result 不作为业务事实。

#### 测试要点

- Domain：Outbox 创建、标记投递、失败退避和终态；
- Application：派发成功/失败/退避/上限、崩溃后补投、执行体成功/失败/重复 Job/已取消跳过；
- PostgreSQL：唯一约束、外键、到期查询排序、`try_mark_dispatched` 条件更新；
- Queue/Worker 集成（Valkey + ARQ）：Outbox → ARQ → Worker → Run SUCCEEDED 闭环、队列故障恢复补投、相同 Job ID 去重。

### 切片 6：Fake Parser 闭环

#### 目标

Worker 收到 Ingestion Run 后真正走完解析流水线：`DocumentParser` Port 由确定性 Fake Parser 实现，产出 `DocumentParseRevision`、`DocumentElement` 和 `ElementSourceLocation` 并原子提交；用户可通过 API 按页码/章节查询 Element 并回溯到 PDF 页码。真实 Docling/pypdf 在切片 7 替换 Fake Parser。

#### 范围

- **包含**：`DocumentParseRevision`/`DocumentElement`/`ElementSourceLocation` 领域模型、ORM 与迁移；`paper_versions.current_parse_revision_id` 显式当前指针；`ParseProfile` 与确定性 `parser_profile_hash`；`DocumentParser` Port 与 Fake Parser 适配器；`IngestionExecutor`（进度 Event、事务外解析、原子提交、复用已有成功 Revision、提交前取消检查）；`RunExecutionService` 重构为"认领 + 执行器负责终态"；`GET .../paper-versions/{version_id}/document` 与 `GET .../elements` 查询 API；Worker 接线真实执行器。
- **不包含**：Docling/pypdf 真实解析、OCR、降级标记真实产生、Attempt/lease、SSE、Markdown 序列化。

#### 数据模型

- `document_parse_revisions`：`revision_id`（PK）、`version_id`（FK）、`parser_name`、`parser_version`、`parser_profile_hash`、`status`（`running`/`succeeded`/`failed`）、`config`（JSONB）、`error`（JSONB 可空）、`created_at`、`completed_at`；唯一约束 `(version_id, parser_profile_hash)`。
- `paper_versions` 新增 `current_parse_revision_id`（可空 FK → `document_parse_revisions`）。
- `document_elements`：`element_id`（PK）、`revision_id`（FK）、`element_type`、`sequence`（全文阅读顺序）、`parent_element_id`（可空自引用）、`section_path`、`text`、`payload`（JSONB）、`content_hash`；唯一约束 `(revision_id, sequence)`。
- `element_source_locations`：`location_id`（PK）、`element_id`（FK）、`page`、`bbox`（JSONB 可空）、`parser_ref`、`char_range`（JSONB 可空）。

#### 关键不变量

1. `parser_profile_hash` 由 `(parser_name, parser_version, config)` 的规范化 JSON 计算，相同输入必须相同；
2. 相同 `(version_id, parser_profile_hash)` 已有 `succeeded` Revision 时复用，不重复解析（Effectively Once）；
3. Parser 调用在数据库事务外；Revision/Element/来源定位、当前指针、Run 终态和 `result_committed` Event 在同一事务原子提交，不暴露半成品；
4. Element `sequence` 在 Revision 内唯一且连续；每个 Element 至少一个带来源页码的 SourceLocation；
5. 执行器在提交前检查取消：`CANCEL_REQUESTED` 时推进 `CANCELLED`，不提交新结果；
6. 进度 Event：`parse_started` → `parse_completed` → `normalize_completed` → `result_committed`，与状态同事务、Sequence 严格递增。

#### API 契约

```text
GET /api/v1/projects/{project_id}/paper-versions/{version_id}/document
GET /api/v1/projects/{project_id}/paper-versions/{version_id}/elements?page=&section=&type=&limit=&offset=
```

- `document` 返回当前 Revision 元数据与章节概览；`elements` 返回 Element 及来源定位，支持页码/章节前缀/类型过滤与 `limit/offset` 分页；
- 无当前 Revision 时 `document` 返回 404（`document_not_ready`）；越权或不存在资源一律 404。

#### 测试要点

- Domain：Profile 哈希确定性、Element 顺序/内容哈希、Revision 状态；
- Application：执行器全链路事件序列、复用路径、取消竞争、Parser 失败 → FAILED；
- PostgreSQL：唯一约束、当前指针、按页/章节查询；
- API：契约、过滤分页、越权 404；
- 端到端：上传 → Worker 消费 → elements 可查（Fake Parser，不依赖真实 PDF 解析）。

### 切片 7：真实 Parser（Docling + pypdf 降级）

#### 目标

Worker 用真实 PDF 解析替换 Fake Parser：Docling 标准 Pipeline 产出带章节、表格和来源定位的 Element；损坏/加密等结构性失败自动降级 pypdf 并标记 `degraded`；超时、永久输入错误按分类进入明确终态。用户上传真实 PDF 后能通过 document/elements API 看到真实解析结果与降级/警告信息。

#### 范围

- **包含**：`parser_timeout_seconds=300` 等新 Settings（上传 50 MB 已是默认值）；解析错误分类（可降级输入错误/超时/资源/未知）；Docling 适配器（标准 PdfPipeline、默认不开 OCR、`config.ocr_enabled` 开关）；pypdf 降级适配器（页级定位、纯文本）；Fallback 组合（按异常类型决定是否降级）；`document_parse_revisions` 增加 `degraded`/`warnings` 字段并在 document API 暴露；Element Payload 定稿落地；文本长度为 0 → `possibly_scanned` 警告；执行器层统一施加 Parser 超时；合成 PDF Fixtures 与 Parser 契约测试；Worker 通过配置选择 Parser（fake/docling）。
- **不包含**：OCR 实际启用路径的质量验证（只留开关）；图片抽取与 figure 的 Storage 写入；Attempt/lease 与崩溃对账（切片 8）；前端（切片 10）。

#### 数据模型

- `document_parse_revisions` 新增 `degraded`（bool，默认 false）与 `warnings`（JSONB 字符串列表，默认空）；执行器在提交事务内把 `ParsedDocument` 的文档级标记写入 Revision。
- Element Payload 定稿：`table` 为 `{"rows": int, "cols": int, "cells": [[str]]}`；`figure` 为 `{"storage_key": str|null}`；`formula` 为 `{"latex": str|null}`；其余类型只用 `text`。
- document API 响应增加 `degraded` 与 `warnings` 字段。

#### 错误分类与降级

| 分类 | 触发 | 行为 |
|---|---|---|
| 可降级输入错误 | Docling 抛损坏/加密/结构类异常 | 尝试 pypdf；成功 → `degraded` + 能力缺失 warning；也失败 → 永久输入错误 FAILED |
| 超时 | `parser.parse` 超过 `parser_timeout_seconds` | 不降级，FAILED（`error.type=parser_timeout`） |
| 资源类 | 内存/进程等资源异常 | 不降级，FAILED |
| 未知异常 | 其他 | 不降级，FAILED（保守，不掩盖 bug） |

- 降级只发生一次（Docling → pypdf），pypdf 不再降级，同一错误只有一层主导重试；
- pypdf 结果只有页级定位：文本按页切成 `paragraph` Element，`bbox` 为 null 并标记 `degraded`，不伪造精度；
- 全文文本长度为 0（主路径或降级）：追加 `possibly_scanned` warning 提示用 OCR 重跑，解析本身仍算成功（空文档是合法结果）。

#### 关键不变量

1. 超时通过 `asyncio.wait_for` 在执行器层统一施加，Parser 适配器不自行实现超时；
2. 所有解析路径产出同一 `ParsedDocument` 契约：每个 Element 至少一个带页码的 SourceLocation；
3. `ocr_enabled` 参与 `parser_profile_hash`，切换 OCR 产生新 Revision，旧结果不受影响；
4. 普通测试不强制下载 Docling 模型：Docling 真实解析测试归入显式启用组（环境变量开启）；默认套件用 pypdf 真实解析与 Stub 验证分类组合逻辑；
5. Fixtures 只提交合成或公开许可 PDF，用户本地论文不进仓库。

#### 测试要点

- Adapter：Docling 标签映射（未知标签归 paragraph + warning）、table/figure/formula Payload 形状、`degraded`/`warnings` 传播；
- pypdf 契约：多页文本 PDF → 每页 paragraph + 页级定位；加密/损坏 PDF → 可降级输入错误分类；空白 PDF → `possibly_scanned`；
- Fallback 组合：Docling 结构异常 → pypdf 成功且 degraded；超时/资源异常 → 直接 FAILED；pypdf 也失败 → FAILED；
- 执行器：小超时值触发 `parser_timeout` → FAILED；`degraded`/`warnings` 写入 Revision 并在 document API 可见；
- 冒烟：本机真实 Docling 解析一个合成 PDF（允许首次下载模型），结果记入模块笔记。

## 测试方式

- **Domain**：Run 合法/非法转换、终态、取消竞争、错误分类和确定性 ID/Profile；
- **Application**：所有权、Idempotency、Run/Event/Outbox 原子性、事务外 Parser、完整结果可见性；
- **PostgreSQL**：干净迁移、唯一约束、Event Sequence、条件更新和跨用户/Project 隔离；
- **Queue/Worker**：正常及重复 Job、enqueue 前后崩溃、Worker 各阶段退出、lease 恢复、重试和取消；
- **Parser Contract**：文本、多栏/标题、表格/题注、扫描/OCR、损坏/加密样本，以及 Element 类型、层级、阅读顺序、页码、坐标和降级标记；
- **API/SSE/UI**：`202`、非法上传、刷新恢复、Sequence 重放、通知丢失、终态收束、越权拒绝和最小 E2E。

普通测试不调用真实 LLM、Embedding 或学术 API。单元测试使用 Fake Parser/Queue/Storage/Clock/ID；Parser 使用固定本地 Fixture；PostgreSQL/Valkey 集成测试与快速测试分组。第三方 Parser 测试优先断言项目契约和关键结构，不做脆弱的全文逐字符快照。

## 阶段完成条件

- 全新环境可以启动 API、Worker、PostgreSQL 和 Valkey，live/ready 能区分存活和依赖就绪；
- 用户可创建 Project、上传 PDF，并在断线/刷新后查看 Run 和可重放 Event；
- 重复上传、Job、Outbox 投递或响应丢失不产生重复版本或当前解析结果；
- Worker 退出后 Run 能恢复或稳定失败，不永久停在 `RUNNING`；
- 取消后不启动新阶段，且并发终态规则有测试；
- 状态转换和 Event 原子提交；
- 结果可按 Paper Version、Parse Revision、Page 和 Section 查询；
- Element 能回到页码及可用 Bounding Box，UI 可按结构预览并跳转来源；
- RAG/Workflow 能通过 `DocumentContentReader` 复用结果，无需重解析 PDF；
- Phase 1 没有 Embedding、RAG Chunk 或 LLM 调用；
- 跨用户/Project 隔离、关键故障、阶段 Spec、真实测试证据、复盘和已完成模块笔记齐全。

## 已确定事项

- 2026-08-13：Phase 1 只建立结构化文档层，不做 Embedding 或检索索引；
- 2026-08-13：Element 是结构化事实；Phase 1 不持久化 Markdown/HTML 阅读视图或 Segment 映射；
- 2026-08-13：RAG Chunk 和 Workflow Reading Pack 可采用不同策略，但复用同一 Parse Revision；
- 2026-08-13：Paper Version 表示 PDF 内容版本，Parser/配置变化表示为 Parse Revision；
- 2026-08-13：Docling 为主 Parser，pypdf 仅作显式降级；
- 2026-08-13：保留 owner 和可替换 Actor Context，但不建设完整登录；
- 2026-08-13：PostgreSQL 保存业务事实，Storage 保存大内容，Valkey 不作事实来源。
- 2026-08-20：开发默认值定稿——上传上限 50 MB；Parser 超时 300 s；Worker lease 600 s / heartbeat 30 s（切片 8 实现）；Outbox 重试维持现状（1s 起指数退避、上限 60s、最多 10 次）。全部走 Settings 环境变量，测试用小值验证超时路径。
- 2026-08-20：解析策略定稿——主路径 Docling 标准 PdfPipeline，默认不开 OCR（`ParseProfile.config` 保留 `ocr_enabled` 开关，扫描件由用户显式选择）；降级按异常类型分类：Docling 抛文件损坏/加密/结构类异常 → 尝试 pypdf，成功标记 `degraded` 并记录能力缺失 warning；超时/资源类异常不降级直接 FAILED；pypdf 也失败则视为永久输入错误；兜底：文本长度为 0 时给 `possibly_scanned` 警告提示用 OCR 重跑。
- 2026-08-20：Element 最低 Payload 定稿——枚举维持现有 ElementType，Docling 标签映射不到的归 `paragraph` 并记 warning；`table` 存纯文本网格 `{"rows","cols","cells"}`；`figure` 只存 `storage_key`（首版可 null + warning，不抽图片）；`formula` 存 `{"latex": str|null}`；其余只用 `text` 字段。复杂结构推迟到 Phase 2 有真实消费方再扩展。

## 实现前仍需确定

以下问题不改变阶段边界，在对应切片开始前通过测试或小实验确定：

1. Element API 对复杂表格、公式和图片内容的返回形式；
2. 元数据不完整时的展示名和同一 owner 跨 Project 复用交互；
3. SSE 心跳、分页、终态关闭和本地 Storage 回收策略；
4. Phase 1 UI 的具体 Element 渲染组件和视觉样式。

如果实现需要改变 Run/Event 事实来源、Paper/Version/Parse Revision 所有权、数据隐私或跨用户去重策略，先更新本 Spec；达到 `AGENTS.md` 的触发条件时再写 ADR。

## 已知限制

- Element 结构识别质量受 PDF 排版、OCR 和 Parser 能力影响，降级结果可能只有页级定位；
- Phase 1 不保证书目信息已经过 DOI 或外部元数据校验；
- 没有 Embedding/检索 API，只能按资源、页码、章节和顺序读取；
- 本地 Storage 只面向单节点开发，S3 替换需要 ADR；
- 协作式取消不保证立即终止已进入 Parser 的底层计算；
- 前端仅覆盖本阶段演示闭环。

## 学习笔记和下一步

已完成模块的学习笔记（随实现同步更新，内容以实际代码和测试为准）：

- `docs/learning-journal/modules/project.md`：Project 闭环与 Actor Context；
- `docs/learning-journal/modules/run-event.md`：Run 状态机与 Event 顺序；
- `docs/learning-journal/modules/paper-upload.md`：上传校验、版本与幂等；
- `docs/learning-journal/modules/queue-outbox.md`：Queue Outbox + ARQ Worker 可靠投递；
- `docs/learning-journal/modules/document-parsing.md`：Parse Revision、Element 与 Fake Parser 闭环。

后续模块笔记在对应切片真正完成后撰写，不预建空模板。

当前进行切片 7（真实 Parser）：三项前置决策已于 2026-08-20 定稿（见“已确定事项”），契约见上方切片 7 章节。实现顺序：Settings 与依赖 → Revision degraded/warnings 与错误分类 → pypdf 降级适配器与 Fixture 契约测试 → Docling 主适配器与 Fallback 组合 → 执行器超时接线与 Worker 配置切换。
