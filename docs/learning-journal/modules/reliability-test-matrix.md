# 可靠性测试矩阵

## 模块解决的问题

Phase 4 切片 7 不增加新的可靠性机制，而是把至少一次投递、崩溃恢复、错误分类、取消竞争、事件重放、
等待恢复和 Checkpoint 损坏等既有语义逐行绑定到可执行证据。审计先复用已有测试，只为真实跨模块空白
补测试。Phase 4 Spec §7 实际包含十行故障场景；本笔记按十行记录，不把多个场景合并成“九项”。

## 证据矩阵

| 故障 | 主要自动测试（层级） | 已证明 | 未证明或限制 |
|---|---|---|---|
| 重复 ARQ Job | `backend/tests/integration/test_queue_worker.py::test_distinct_physical_jobs_create_one_business_effect`（PostgreSQL + Valkey + ARQ Worker 集成）；`backend/tests/application/test_run_execution_service.py::test_execute_duplicate_job_is_skipped`（Application） | 两个不同物理 Job ID 都只携带同一 `run_id` 时，只有一个 Attempt、一组 Event、一个 ParseRevision 和一组 Element；第二次执行由 PostgreSQL Run 状态认领拒绝 | ARQ 的相同 Job ID 去重只是第一层优化，不是业务正确性的来源；不宣称队列 Exactly Once |
| 外部调用后 Worker 崩溃 | `backend/tests/integration/test_review_checkpoint_recovery.py::test_checkpoint_recovers_across_connections_and_isolates_threads`（PostgreSQL Checkpoint 集成）；`backend/tests/workflows/test_review_graph.py::test_resume_uses_pending_checkpoint_and_reuses_idempotent_side_effect`（Workflow）；`backend/tests/application/test_review_outline_service.py::test_crash_after_outline_output_replays_without_second_model_call`、`backend/tests/application/test_review_evidence_matrix_service.py::test_partial_failure_final_output_crash_replay_skips_failed_paper_model`、`backend/tests/application/test_rag_answer_executor.py::test_existing_claim_set_completes_idempotently`（Application） | 新连接和新 Runtime 能从同一 Thread 恢复；已持久化的 Step/Output/ClaimSet 由稳定键复用，不重复提交业务事实 | Provider 已执行成功到本地 Output/ModelInvocation 提交之间仍有不可消除的崩溃窗口，重放可能再次调用 Provider；见下方“模型调用审计边界” |
| Provider 临时错误 | `backend/tests/infrastructure/test_openai_compatible_models.py::test_embedding_429_retry_then_success`、`::test_embedding_429_exhausted`、`::test_embedding_5xx_exhausted`、`::test_embedding_timeout_exhausted`（HTTP Adapter）；`backend/tests/application/test_run_execution_service.py::test_execute_transient_error_schedules_retry`、`backend/tests/application/test_rag_answer_executor.py::test_temporary_chat_error_retries_and_keeps_claim`（Application） | 429、5xx、超时先由 Adapter 有界短重试；耗尽后由 Run 层在预算内进入 `RETRY_WAIT`，不直接永久失败 | 不读取 `Retry-After`；无生产 SLA 或无限重试 |
| Provider Schema/范围永久错误 | `backend/tests/infrastructure/test_openai_compatible_models.py::test_embedding_400_no_retry`、`::test_embedding_malformed_json`、`::test_chat_missing_choices`（HTTP Adapter）；`backend/tests/domain/test_model_errors.py::test_permanent_model_errors`、`backend/tests/domain/test_retry_policy.py::test_permanent_input_errors`（Domain）；`backend/tests/application/test_run_execution_service.py::test_execute_permanent_error_marks_failed`、`backend/tests/application/test_review_evidence_matrix_service.py::test_existing_evidence_same_chunk_with_different_scope_is_rejected`（Application） | 非法请求、畸形响应和确定性范围错误稳定归类为永久失败，不重置 Outbox、不无限重试 | 业务层允许的“一次结构修复”属于受控新模型调用，不是传输层自动重试 |
| Storage 写入成功、数据库提交失败 | `backend/tests/integration/test_review_artifact_idempotency.py::test_commit_failure_leaves_only_reusable_storage_cache`（PostgreSQL 集成）；`backend/tests/application/test_review_export_service.py::test_crash_after_storage_write_reuses_cache_and_converges_on_retry`（Application） | 真实 session 在 flush 后 commit 失败时，新 session 看到 Output/Artifact/Step/Event 均未提交、Stage/Run sequence 未推进；稳定 Storage Key 的缓存保留，重放收敛为一组业务事实 | Demo-ready Core 不做孤儿缓存 GC；缓存本身不是已发布 Artifact |
| 取消与 Output/Artifact 提交竞争 | `backend/tests/integration/test_review_artifact_idempotency.py::test_cancel_lock_wins_export_commit_without_partial_effects`（PostgreSQL 行锁集成）；同文件 `::test_concurrent_artifact_write_converges_and_final_pointer_is_scoped`（PostgreSQL 并发）；`backend/tests/application/test_review_export_service.py::test_cancel_after_storage_write_commits_no_artifact_or_event`（Application） | 无 sleep 的显式锁顺序证明取消先取得 Run 行锁后，导出只能拒绝，除取消 Event 外无新 Output/Artifact/Step/Stage；并发 Artifact 写由唯一约束收敛 | 已进入远端模型/Storage 的操作不能被数据库锁撤销；取消是协作式，不承诺立即中止底层调用 |
| API/Worker 重启 | `backend/tests/integration/test_review_checkpoint_recovery.py::test_checkpoint_recovers_across_connections_and_isolates_threads`、`::test_outline_interrupt_resumes_with_command_after_runtime_restart`（PostgreSQL Checkpoint 集成）；`backend/tests/integration/test_queue_worker.py::test_dispatch_to_worker_completes_run`（PG + Valkey + Worker） | Worker/Runtime 重建后，业务 Run/Event 与图位置从 PostgreSQL/Checkpoint 恢复；API 查询契约和 Repository 集成测试共同证明 API 进程没有内存业务事实 | 没有单独启动两个 Uvicorn 进程的黑盒测试；“API 重启”是无状态 API + PostgreSQL Repository/API 契约的组合证据，不夸大为单体 E2E |
| SSE 断线 | `backend/tests/api/test_run_events_stream.py::test_sse_resumes_from_last_event_id`、`::test_sse_poll_converges_without_notification`、`::test_sse_replays_history_and_closes_on_terminal`（API）；`backend/tests/integration/test_run_concurrency.py::test_concurrent_start_run_only_one_succeeds`（PostgreSQL sequence） | `Last-Event-ID` 只重放更大 sequence；Valkey 通知丢失时轮询 PostgreSQL 收敛；终态历史先重放再关闭 | SSE API 测试使用 Fake Repository/Notifier；PostgreSQL Event 唯一序号由独立集成测试证明，未组合为浏览器级断线测试 |
| Dependency/HumanInput 等待 | `backend/tests/application/test_run_execution_service.py::test_execute_waiting_run_pauses_attempt`（Application）；`backend/tests/integration/test_waiting_run_resume_transaction.py::test_resume_commits_run_event_and_outbox_together`、`backend/tests/integration/test_review_dependency_reconciler.py::test_ready_source_and_resume_commit_atomically`、`backend/tests/integration/test_human_outline_input_transaction.py::test_concurrent_human_inputs_have_one_business_effect`（PostgreSQL 集成） | 等待使 Attempt 正常 `PAUSED`；恢复创建新投递但 `schedule_again` 不增加失败计数；Run/Event/Outbox/输入或依赖事实同事务提交且重复恢复单效果 | Attempt 与 Run 等待状态提交之间的 best-effort 关闭间隙由 Reconciler 补偿，不是一个跨表大事务 |
| Checkpoint 损坏 | `backend/tests/workflows/test_review_graph.py::test_checkpoint_existence_distinguishes_first_start_from_corrupt_data`（Workflow）；`backend/tests/application/test_review_executor.py::test_corrupt_checkpoint_is_not_replaced_with_new_graph`（Application）；同文件 `::test_missing_resume_checkpoint_is_permanent_data_error`、`::test_postgres_checkpoint_failure_is_classified_as_temporary`（Workflow） | 损坏/非法内容是永久 `CheckpointDataError`；执行器原样传播且 `start`/`resume` 均不调用，不会用新初始 State 覆盖旧图；数据库暂时不可用单独归为临时错误 | 本阶段不提供 Checkpoint 修复、回滚或清理入口，损坏需人工诊断 |

## 模型调用审计边界

`ModelGateway` 在 Provider 调用完成后，以独立短事务写 `ModelInvocation`。它记录 capability、Provider、
Model、状态、usage、耗时和错误类型，不保存 Prompt 或响应正文。这个设计能说明本地已观察并完成记录的
调用，但不能形成与远端 Provider 的分布式原子事务：

1. Provider 成功响应后、业务 Output 提交前崩溃，重放可能再次调用模型；
2. Provider 成功响应后、`ModelInvocation` 提交前崩溃，第一次远端调用甚至可能没有本地调用记录；
3. 因此多条 Invocation 可以解释重复尝试，但“只有一条 Invocation”不能证明 Provider 只执行过一次。

业务层只承诺 Step、Output、ClaimSet、Artifact、Event 等 PostgreSQL 事实通过状态条件、唯一约束、内容
哈希和稳定幂等键实现 Effectively Once，不宣称外部调用 Exactly Once。

## 关键决定与替代方案

- 不为测试增加新队列、锁服务、Chaos 平台或生产开关；故障使用真实 PostgreSQL/Valkey、可控 session
  commit 失败和 `asyncio.Event` 显式锁顺序注入。
- 取消竞争不使用 `sleep` 猜调度顺序。测试让取消事务先持有 Run 行锁，再允许导出进入加锁路径，结果
  由数据库锁和状态复核决定。
- API 重启不新增重型进程测试。FastAPI 不持有业务状态，重启证据来自 API 查询契约、PostgreSQL
  Repository 和跨连接 Worker/Checkpoint 集成；该组合证据及其限制在矩阵中明示。
- 没有测试暴露生产缺陷，因此切片 7 不修改生产实现、迁移或依赖。

## 安全和可观测性

所有新增测试使用 Fake Parser/Model、内存 Storage 与 Testcontainers PostgreSQL/Valkey，不读取 `.env`、
不访问实时 arXiv 或付费 Provider。ARQ Job 仍只携带 `run_id`。Storage 调用继续位于数据库事务外；Run
状态与 Event、Outbox 或 Review Output/Artifact/Stage 的业务变化仍在各自短事务提交。

## 重要测试和运行结果

- 新增定向 Application：`1 passed`；
- 新增 PostgreSQL/Valkey 集成：`3 passed`；
- Backend 完整非集成：`656 passed, 4 skipped`；
- PostgreSQL/Valkey/Testcontainers 完整集成：`117 passed`；
- `ruff check src tests`、`pyright` 与 `git diff --check` 通过。

## 代码入口

- 执行认领：`backend/src/literature_agent/application/run_execution_service.py`
- Outbox/Worker：`backend/src/literature_agent/application/outbox_dispatch_service.py`、
  `backend/src/literature_agent/worker.py`
- Review 导出：`backend/src/literature_agent/application/review_export_service.py`
- Checkpoint：`backend/src/literature_agent/workflows/review_graph.py`
- SSE：`backend/src/literature_agent/api/runs.py`
- 新增测试：`backend/tests/integration/test_queue_worker.py`、
  `backend/tests/integration/test_review_artifact_idempotency.py`、
  `backend/tests/application/test_review_executor.py`

## 已知限制

- 本地 Artifact 缓存、历史 Checkpoint 和归档数据没有自动 GC；ADR-0004 明确不属于本阶段。
- 没有公网认证、备份恢复、OpenTelemetry、告警或 SLA；可靠性证据只适用于 Demo-ready 本地开发边界。
- Provider 副作用和本地事实无法做分布式原子提交，重复模型调用窗口只能审计和披露。
- API 重启和 SSE 的证据是分层组合测试；完整浏览器断线与本地演示旅程留给切片 9。

## 60 秒面试说明

“我没有把可靠性写成一句‘支持重试’，而是把十种故障逐行绑定到自动测试。两个不同 ARQ Job 真正经过
Valkey 和 Worker 后，只能有一个 PostgreSQL Attempt 和一组解析事实；Storage 写完但数据库 commit
失败时，只留下内容哈希缓存，Output、Artifact、Event 和 Stage 全回滚；取消与导出用 Run 行锁决定
唯一赢家；损坏 Checkpoint 会永久失败，绝不被当作新图覆盖。等待输入或依赖以 PAUSED 正常释放 Worker，
SSE 从 PostgreSQL sequence 重放。边界上我明确承认 Provider 响应和本地持久化之间仍可能重复调用，
ModelInvocation 也有记录间隙，所以系统承诺的是业务 Effectively Once，不是分布式 Exactly Once。”
