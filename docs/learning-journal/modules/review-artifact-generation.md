# 综述 Artifact 生成与生产执行闭环

## 模块解决的问题

本模块把 Phase 3 前八个切片形成的独立能力接成可由 Worker 实际执行的 Review 闭环，并把已验证的
章节、Claim 和 Evidence 导出为用户可下载且可追溯的 Markdown。它同时补齐此前故意未注册的生产
入口：固定 Search Strategy、`ReviewExecutor`、LangGraph Export/Finalize、Project-scoped API 和
Review Event/SSE 读取。

本模块不做论文人工筛选、多来源下载、引用样式切换、Word/LaTeX 导出或通用 Workflow/Agent。

## 边界与执行流程

```text
RunExecutionService 认领 QUEUED Review
  → ReviewExecutor
      → SearchStrategy（持久结果优先）
      → arXiv search/import（持久 Step/Source 优先）
      → 未完成依赖：Run=WAITING_DEPENDENCY，释放 Worker
      → READY Source：Evidence Matrix（持久结果优先）
      → review.v1 checkpoint
          → Outline propose → interrupt
          → HumanInput Command resume
          → Section → Citation → Consistency
          → Export → Finalize
  → RunExecutionService 按 Run 终态关闭 Attempt
```

依赖等待属于业务 Run，不属于 LangGraph Interrupt。只有 Outline 是 HITL。图外阶段在 HumanInput
恢复后会再次经过应用服务，但 Strategy、arXiv 和 Matrix 都先读取稳定 Step/Output/Source，因此不会
重复出网、调用模型或创建 Paper/Run/Event。`ReviewWorkflowRuntime.has_checkpoint()` 明确区分首次
Thread 与既有 Thread；损坏 checkpoint 不会被当作首次执行覆盖。

## 数据、状态与事务

固定图只在 State 中传递 `review_run_id/project_id` 及 Search Strategy、Matrix、Outline、Section、
ClaimSet、Consistency、Final Output 和 Artifact ID，不保存正文。

Search Strategy 使用 `search_strategy.v1` Prompt、`search-strategy.v1` Schema 和版本化 Review
Profile；新 Run 使用 `review-default.v3`，历史 `review-default.v1/v2` 继续受支持。输出限制 64 KiB，
禁止额外字段，只允许有效 arXiv 查询、3–6 个唯一
snake_case 维度；非法输出不 repair。模型调用在事务外，返回后持锁复核 owner/Project/RUNNING
Review，再以稳定 Step/Output/Event 提交。

详情 API 的 `current_stage` 由持久业务服务推进：创建时成功固化 Validate Step 并进入 Strategy；
Strategy/Search/Import/Wait/Reconcile/Matrix 在各自 Step/Event 事务中进入下一阶段，固定图继续推进
Outline 到 Finalize。条件更新只接受预期前置 Stage，重放不会把后期 Stage 倒退。

导出前重新加载并验证：成功的 Consistency Step 输入引用、Section Output、ClaimSet/Claim/Citation、
Evidence 和 READY ReviewSource/PaperVersion 闭包。导出成功事务同时保存/复用 Final Review Output、
六条 Artifact 元数据、Review Stage、Export Step 和 `review_artifact_created` Event。Finalize 事务
同时完成 Finalize Step、Run `RUNNING → SUCCEEDED` 和 `run_succeeded` Event。外层执行服务只关闭
Attempt，不再生成第二个终态 Event。

## Markdown 与引用映射

Markdown 按章节顺序渲染。每个 Claim 后附 `[1][2]`；编号按论文在全文中的首次引用顺序分配，同一
论文始终复用编号。文末 References 采用持久 Source 元数据，不信任模型生成 DOI、作者或年份。

Bibliography Artifact 保存完整引用映射：编号、Paper/PaperVersion/ReviewSource、arXiv ID/version、
标题/作者/发布日期、Evidence IDs、Claim IDs，以及每条 Evidence 的页码、章节、Chunk 和 Parse
Revision 定位。Final Review Output 保存引用数量和六类 Artifact manifest，不复制可能增长的
完整映射，以遵守 ReviewOutput 256 KiB 上限；完整事实由 Bibliography Artifact 与
Claim/Citation/Evidence 表提供。Markdown 因而不依赖隐藏 Prompt 才能解释来源。

固定生成：

1. `review_markdown`；
2. `search_strategy`；
3. `source_manifest`；
4. `evidence_matrix`；
5. `bibliography`；
6. `run_summary`。

Run Summary 保存 Workflow/Prompt/Profile/配置快照、受控统计、来源成功/失败和已知限制。
其中模型调用与 token 从 Model Gateway 已持久的 `model_invocations` 审计事实聚合，来源
计数从 ReviewSource 聚合；同一统计同时写入 Final Output 和
`review_runs.statistics_summary`。它是可复现说明，不是完整 Worker 日志或模型 Prompt。

## 重复、崩溃、重试与取消

- Storage Key 固定为 `{owner}/reviews/{run}/{sha256}/{filename}`；同内容重放覆盖相同字节；
- Artifact 按 Run+类型+导出版本使用稳定幂等键，PostgreSQL 唯一约束和 get-or-add 收敛并发；
- Storage 写入先于数据库短事务。文件写完后崩溃会留下安全缓存；重试复用缓存并只提交一组
  Output/Artifact/Event，不做可能删除并发赢家文件的补偿删除；
- 每次副作用提交前持锁复核 Run 仍为当前 owner/Project 的 RUNNING Review。取消发生在 Storage 后、
  DB 前时，不新增 Artifact、Event 或 Stage；
- 重复 ARQ Job 无法再次认领终态/等待 Run。副作用提交后图 checkpoint 前崩溃时，节点重放复用业务
  Step/Output/Artifact；
- 零 arXiv 结果或所有来源永久失败抛出永久 `no_reviewable_papers`，不会进入无限依赖等待；
- 策略 Schema、章节范围/输出、Citation 和导出闭包错误属于确定性永久错误，不消耗
  Worker 临时错误重试预算；
- Artifact 下载重新校验数据库大小与 SHA-256；不一致时拒绝返回损坏内容。

## API、安全与可观测性

`/api/v1/projects/{project_id}/reviews` 提供创建、详情、取消、sources、evidence-matrix、outline、
outline-input、artifacts、Artifact content 和 events。Route 不执行 SQL/模型/图；读取统一由
`ReviewQueryService` 校验 owner+Project+Run，越权与不存在均返回 404。创建和 HumanInput 强制
`Idempotency-Key`；下载返回内容哈希 `ETag`。

Project-scoped `/events` 按 Event sequence 游标读取。实时流复用
`/api/v1/runs/{run_id}/events/stream`：先用通用 RunService 做 owner 隔离，再按 `Last-Event-ID` 从
PostgreSQL 重放；Valkey 只负责可丢失唤醒。Event 只保存 ID、计数和稳定错误摘要，不保存论文全文、
Prompt 或 Secret。

## 重要测试与实际结果

测试覆盖：首次引用编号与完整映射、伪造/错序 Claim/Citation 拒绝、六类 Artifact 幂等、Storage 后
取消、Storage 后 DB 前崩溃重放、PostgreSQL 并发 get-or-add/final pointer、零/失败来源、checkpoint
存在与损坏区分、完整图 Resume 到 Export/Finalize、API 三元隔离、SSE Review owner 隔离和
`Last-Event-ID` 重放。

Phase 4 切片 7 进一步以真实 PostgreSQL session 在 flush 后确定性注入 commit 失败：新 session
确认 Output/Artifact/Step/Event 全部回滚、Stage 与 Run sequence 未推进，而稳定 Storage 缓存保留；
正常 session 重放后只提交一组事实。取消竞争测试用显式 `asyncio.Event` 控制锁顺序，不使用 sleep：
取消先持有 Run 行锁时，导出随后只能拒绝，除 `run_cancel_requested` 外没有导出业务效果。

实际结果：导出/策略/执行器/完整图定向测试 `26 passed`，Review Finalize/Attempt 收尾定向测试
`1 passed`，Review API 与通用 SSE 定向测试 `10 passed`，新增 PostgreSQL Artifact 集成测试
`1 passed`；Backend 完整非集成回归 `596 passed, 4 skipped`，完整
PostgreSQL/Valkey/Testcontainers 集成回归 `112 passed`；
`ruff check src tests` 与 `pyright` 通过。普通测试使用 Fake Model、HTTP mock 和本地 Storage，不访问
真实 arXiv 或付费模型。

## 代码入口

- `backend/src/literature_agent/application/review_executor.py`
- `backend/src/literature_agent/application/review_search_strategy_service.py`
- `backend/src/literature_agent/domain/review_search_strategy.py`
- `backend/src/literature_agent/domain/review_export.py`
- `backend/src/literature_agent/application/review_export_service.py`
- `backend/src/literature_agent/workflows/review_graph.py`
- `backend/src/literature_agent/workflows/review_export_nodes.py`
- `backend/src/literature_agent/application/review_query_service.py`
- `backend/src/literature_agent/api/reviews.py`
- `backend/src/literature_agent/worker.py`

## 已知限制

- 只支持 arXiv、v3 固定前 3 篇默认预算和 Markdown；不支持人工筛选或其他引用格式；
- `review-default.v3` 当前总下载预算由单文件上限乘固定来源数得到，仍需真实小规模试验校准；历史
  v1/v2 Run 保留各自快照；
- 一致性报告不是事实 Judge；引用闭包证明引用存在且范围正确，不证明 Claim 一定被 Evidence 语义蕴含；
- Phase 3 切片 10 已完成阶段验收，并补齐图外 `current_stage` 的 Step/Event 原子推进；
- Phase 4 切片 4 已把 Artifact 元数据和下载接入 Review Detail。浏览器只构造已授权的 Project-scoped
  Artifact content endpoint，不读取或拼接 `storage_key`；正文完整性仍由后端读取时复核大小和哈希。

## 60 秒面试说明

“我把一个带 HITL 的文献综述长任务拆成业务状态和 LangGraph 两层。arXiv 下载与子任务等待放在图外，
因为等待要释放 Worker；只有大纲确认使用 LangGraph interrupt。恢复时所有模型、下载和 Artifact 节点
都先查业务 Step/Output，checkpoint 只决定图位置，所以至少一次投递不会重复业务效果。最终导出不是
直接拼模型文本：它从已验证 Claim/Citation/Evidence 闭包确定性渲染 Markdown，按首次引用顺序编号，
并保存完整 PaperVersion 和 PDF 定位映射。文件先按内容哈希写 Storage，再持锁提交元数据、Stage 和
Event；如果取消或崩溃发生在中间，只留下可复用缓存，重放会收敛。API 和 SSE 都复用 owner/Project
隔离与 PostgreSQL Event 重放，因此 Checkpoint、队列和实时通知都不是业务事实来源。”
