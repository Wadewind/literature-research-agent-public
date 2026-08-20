# 文档解析模块（Parse Revision + Element + Docling/pypdf 解析闭环）

## 解决的问题

Worker 认领 Ingestion Run 后，需要把 PDF 真正解析成可查询的结构化文档：版本化的解析结果（Parse Revision）、规范化文档单元（Element）和回到 PDF 页码/坐标的来源定位（SourceLocation）。主路径是 Docling 标准 PdfPipeline；损坏/加密等结构性失败自动降级 pypdf 并标记 `degraded`；超时和永久输入错误按分类进入明确终态。同时提供 `DocumentQueryService`，让后续阶段（RAG/Workflow）和前端能按授权上下文读取文档结构与来源定位。

## 边界与执行流程

```text
RunExecutionService.execute（认领 QUEUED → RUNNING + run_started）
  → IngestionExecutor.execute(run, correlation_id)
      ├─ 事务 A（_prepare）：
      │    按 (version_id, parser_profile_hash) 查找 Revision
      │    ├─ 已有 succeeded → 复用：设当前指针 + Run SUCCEEDED + result_committed(reused)
      │    ├─ 无 → 创建 RUNNING Revision 行
      │    ├─ failed → 复用同一行重置 RUNNING（唯一约束不允许第二行）
      │    └─ 写 parse_started 事件
      ├─ 事务外：DocumentParser.parse(storage_key, profile) → ParsedDocument
      │    Docling 主路径；InvalidPdfInputError → pypdf 降级（degraded）
      │    超时（asyncio.wait_for）/资源/未知异常 → 不降级
      │    失败 → Revision FAILED；失败策略决定 Run RETRY_WAIT 或 FAILED
      ├─ 事务 B：normalize_parsed_document 分配 element_id/内容哈希，
      │    批量写 parse_completed、normalize_completed 事件
      └─ 事务 C（原子提交）：Element + SourceLocation + Revision succeeded
           （degraded/warnings）+ current 指针 + Run SUCCEEDED + result_committed
           + 同事务创建 indexing Run + run_created + Outbox（Phase 2 切片 4 起）

DocumentQueryService（读路径）
  → 校验 owner → Project → ProjectPaper(selected_version) → PaperVersion
    （未被该 Project 收录的 Version 一律 404）
  → 按 current_parse_revision_id 读取概览 / Element 列表 + 来源定位
```

- `DocumentParser` Port 输入受控 Storage Key 和 `ParseProfile`，输出项目自己的 `ParsedDocument`，业务代码不暴露 Parser 原生类型；
- Parser 调用严格在数据库事务外；每个短事务只做一件事（准备/进度/提交）；
- `RunExecutionService` 只负责认领与兜底失败，终态由执行器负责，执行后读最终状态映射 COMPLETED/FAILED/SKIPPED；
- 取消检查分布在事务 A、B、C 入口：发现 `CANCEL_REQUESTED` 就推进 `CANCELLED`，不提交新结果。

## 状态、数据模型和事务

- `document_parse_revisions`：`revision_id`、`version_id`、`parser_name`、`parser_version`、`parser_profile_hash`、`status`（`running`/`succeeded`/`failed`）、`config`、`error`、`degraded`、`warnings`、`created_at`、`completed_at`；唯一约束 `(version_id, parser_profile_hash)` 保证同一版本同一配置只有一个解析结果。
- `document_elements`：`element_id`、`revision_id`、`element_type`、`sequence`、`parent_element_id`、`section_path`、`text`、`payload`、`content_hash`；唯一约束 `(revision_id, sequence)` 保证阅读顺序唯一。
- `element_source_locations`：`location_id`、`element_id`、`page`、`bbox`、`parser_ref`、`char_range`；Element 尽量提供来源定位，跨页元素可有多条，但解析器无法定位时允许为空，前端降级显示“无页码”。
- `paper_versions.current_parse_revision_id`：显式当前指针，历史 Revision 仍可查询。
- `parser_profile_hash` 由 `(parser_name, parser_version, config)` 的规范化 JSON 计算 sha256，相同输入必然相同。
- 事务 C 把产物、指针、Run 终态和 `result_committed` 事件放在同一事务，不暴露半成品；Run 状态推进始终用 `expected_status` 条件更新 + 行锁。

## 关键决定与替代方案

- **唯一约束即幂等键**：`(version_id, parser_profile_hash)` 让重复 Job/重复投递在数据库层只能产生一行 Revision，复用路径直接返回已有结果，天然 Effectively Once。
- **显式当前指针而不是"最新 succeeded"**：查询不依赖排序约定，重解析不影响已发布结果，回滚也只改指针。
- **失败行重跑复用同一行**：唯一约束下无法插第二行，重置为 RUNNING 保留因果链；RUNNING 遗留行视为上次崩溃残留，直接复用。
- **Fake Parser 先行**：执行器、事务边界、事件序列、API 全部用确定性 Fake 验证，真实 Parser 的风险隔离在 Adapter 内。Fake 输出固定 8 个 Element，覆盖两页、两个章节、表格+题注父子关系和跨页段落双定位。
- **进度事件批处理**：`parse_completed`/`normalize_completed` 在一个事务内顺序写入，sequence 严格递增；每写一个事件后以数据库为准重读 sequence，Fake/真实实现行为一致。
- **`IngestionExecutor` 泛型化（PEP 695）**：`class IngestionExecutor[TSession: Session]`，Application 层不依赖 SQLAlchemy Session 具体类型。
- **降级与运行重试是两层策略**：`InvalidPdfInputError`（损坏/加密/结构）才触发 pypdf 降级；超时、资源和未知异常不切换 Parser。一次解析尝试失败后，永久输入错误直接令 Run FAILED；超时、资源和未知异常在预算内进入 RETRY_WAIT，由 Outbox 退避重投。降级只发生一次，是否降级由 `degraded` + warnings 表达。
- **超时在执行器层**：`asyncio.wait_for` 统一施加 `parser_timeout_seconds`（默认 300s，`AGENT_PARSER_TIMEOUT_SECONDS`），适配器不各自实现超时；超时记 `error.type=parser_timeout`。注意线程内的 Docling 计算无法被 wait_for 杀死，只保证状态收束。
- **文档级警告两条来源**：Parser 自报（pypdf 的 `layout_missing`/`table_missing`、Docling 的 `partial_conversion`、figure 的 `figure_not_extracted`）+ 领域规则 `detect_document_warnings`（全文无文本 → `possibly_scanned`），提交事务内写入 Revision 并在 document API 暴露。
- **Element Payload 定稿**：table 存纯文本网格 `{"rows","cols","cells"}`（Docling TableItem 导出 DataFrame 再网格化）；figure 只存 `storage_key`（首版 null + warning）；formula 存 `{"latex": str|null}`；未知标签归 paragraph + `unmapped_label:<label>` warning。
- **Docling 标签映射与章节路径**：`iterate_items` 层级栈维护 `section_path`（如 `1`、`1.1`）；紧跟 table/figure 的 caption 挂为子元素；坐标统一转为左上角原点。
- **测试分组**：Docling 首次运行需下载布局模型，真实解析测试用 `AGENT_RUN_DOCLING_TESTS=1` 显式启用；默认套件用合成 Fixture 跑 pypdf 真实解析与 Stub 分类组合。

## 失败、重试、重复和取消行为

- Parser 抛错：Revision 标记 `failed`（记录错误类型和截断消息，不记堆栈）；永久输入错误令 Run 进入 `FAILED` + `run_failed`，临时错误在预算内进入 `RETRY_WAIT` + `run_retry_scheduled`，重跑复用同一 Revision 行。
- 重复 Job：`RunExecutionService` 只认领 `QUEUED`，已 RUNNING/终态直接跳过；Outbox 补投由 Job ID 去重。
- 执行期间取消：事务 A/B/C 入口检查 `CANCEL_REQUESTED` → `CANCELLED` + `run_cancelled`，已解析的产物不提交、指针不更新。
- 提交与取消并发：行锁 + 条件更新产生唯一终态，冲突抛 `RunConcurrentModificationError`。
- Worker 每次认领创建 Attempt 并周期写 heartbeat；崩溃后 lease 对账关闭旧 Attempt，并按同一失败策略重投或结束 Run。

## 安全和可观测性

- 查询路径完整校验 owner → Project → ProjectPaper → selected PaperVersion，越权与不存在统一 404，不因用户拥有该 Version 就允许从任意 Project 读取；
- Element 查询参数受控：page ≥ 1、limit 1–200、type 必须是合法枚举（非法返回 400）；
- Event/日志不记录 PDF 内容、宿主路径或解析堆栈，错误消息截断到 500 字符；
- Storage Key 由上传模块生成，Parser 只拿到 Key 不接触原始请求。

## 重要测试和运行结果

- `tests/domain/test_parsing_domain.py`：Profile 哈希确定性、Revision 状态机（含 degraded/warnings）、Element 规范化、`detect_document_warnings`。
- `tests/application/test_ingestion_executor.py`：全链路事件序列、复用路径、取消竞争、Parser 失败 → FAILED、失败行重跑、小超时触发 `parser_timeout`、degraded/warnings 落库、空文本 → `possibly_scanned`。
- `tests/infrastructure/test_pypdf_parser.py`：合成 Fixture 真实解析——两页文本、空白（possibly_scanned）、损坏/加密 → `InvalidPdfInputError`。
- `tests/infrastructure/test_fallback_parser.py`：降级触发条件、资源/未知异常不降级、降级失败即永久。
- `tests/infrastructure/test_docling_parser.py`：Docling 真实解析契约（默认跳过，`AGENT_RUN_DOCLING_TESTS=1` 启用；本机 2 passed，含模型首次下载共 147s）。
- `tests/integration/test_parse_revision_repository.py`、`test_queue_worker.py`、`tests/api/test_documents.py`：持久化、端到端与 API 契约（document 响应含 degraded/warnings）。

切片 7 完成时的历史快照：`uv run pytest -q` 127 passed + 2 skipped（Docling 显式组），`ruff check` 与 `pyright` 无告警。当前测试基线以 Phase 1 进度记录为准。

宿主机冒烟（2026-08-19，API + Worker 真实 Docling）：两页文本 PDF → succeeded、docling 2.120.3、跨页段落双定位；加密 PDF → 降级也失败、FAILED（`InvalidPdfInputError: PDF 已加密`）；空白 PDF → succeeded + `possibly_scanned`。

## 代码入口

- 领域：`backend/src/literature_agent/domain/parse_profile.py`、`parse_revision.py`、`document_element.py`、`exceptions.py`
- 端口：`backend/src/literature_agent/application/ports/document_parser.py`、`parse_revision_repository.py`、`element_repository.py`
- 服务：`backend/src/literature_agent/application/ingestion_executor.py`、`document_query_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/parsing/docling_parser.py`、`pypdf_parser.py`、`fallback_parser.py`、`fake_parser.py`、`infrastructure/persistence/parse_revision_repository.py`、`element_repository.py`
- Worker 接线：`backend/src/literature_agent/worker.py`（`AGENT_PARSER_BACKEND` 选择 fake/docling）
- 路由：`backend/src/literature_agent/api/documents.py`
- 迁移：`backend/migrations/versions/8865966463a6_create_parse_revision_and_element_tables.py`、`13497f1b8554_document_parse_revisions_增加_degraded_与_.py`
- Fixtures：`backend/tests/fixtures/pdfs/`（`generate.py` 可重新生成）

## 已知限制

- 超时时 `asyncio.wait_for` 无法杀死线程内的 Docling 计算，只保证 Run 状态收束；线程泄漏由 ARQ 进程生命周期兜底。
- figure 不抽取图片（`storage_key` 为 null + warning）；OCR 开关存在但未做质量验证；公式不识别 LaTeX。
- `document_elements.text` 直接入库，大字段正文未拆分 Storage。
- Element 查询没有游标分页，只有 limit/offset。
- SSE 已接入，但单连接仍有 1s PostgreSQL 轮询兜底，连接规模扩大后需要评估读扩散。
- Worker 容器镜像包含 torch，体积显著变大；Docling 首次运行需联网下载布局模型（容器内运行需预置模型缓存）。

## 60 秒面试说明

"文档解析模块把一次导入 Run 推进成可查询的结构化文档。关键设计是唯一约束撑起来的 Effectively Once：`(version_id, parser_profile_hash)` 保证同一份 PDF 同一套解析配置只有一个 Revision，重复 Job 进来直接复用；`(revision_id, sequence)` 保证阅读顺序唯一；当前结果用 paper_versions 上的显式指针选择，而不是'最新成功'这种隐式约定。执行上严格分层——Parser 调用在事务外，准备、进度、提交各是一个短事务，提交事务把 Element、来源定位、Revision 状态、当前指针、Run 终态和事件一次性原子写入，所以任何时刻用户要么看到完整的旧结果，要么看到完整的新结果，永远看不到半成品。可靠性上按异常类型分类：只有损坏/加密这类输入问题才降级到 pypdf 并标记 degraded，超时和资源问题不降级直接失败，超时在执行器层统一施加。整个闭环先由确定性 Fake Parser 验证事务与事件契约，真实 Docling/pypdf 只实现同一个 Port，替换不动业务层。"
