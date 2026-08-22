# Review Workflow 数据契约

## 模块解决的问题

Phase 3 的固定 Workflow 需要在 LangGraph 尚未接入前，先明确哪些数据属于 PostgreSQL 业务事实。
本模块建立 Review Run、Step、arXiv Source、依赖、版本化 Output、人工输入请求和 Artifact 的领域与
持久化契约，并提供一个可实际创建和投递 Review Run 的最小应用闭环。

## 边界与执行流程

创建入口是 `ReviewWorkflowService.create_review_run()`：

```text
可信 Actor + active Project + 研究问题 + Idempotency-Key
  → 校验 owner / Project 归档状态
  → 创建 Run(type=review, status=queued)
  → 创建 review_runs 扩展记录和版本/配置快照
  → 创建 review_run_created Event
  → 创建 PENDING Outbox
  → 保存 IdempotencyKey
  → 同一事务 commit
```

本切片没有 HTTP Route，也没有 Worker 执行器。后续 API 只能映射请求并调用该服务；后续 Worker 通过
已授权的 `run_id` 写入 Step、Source、Dependency 和 Output，不能绕过应用边界执行任意 SQL。

## 数据模型与事务

- `review_runs`：通用 `runs` 的一对一扩展，保存研究问题、版本快照、配置快照、固定统计计数和当前阶段；
- `run_steps`：按 Run 内 sequence 展示执行历史，幂等键用于节点重放复用；
- `review_sources`：保存自动纳入的 arXiv ID/version、rank、可信元数据快照和导入结果；
- `run_dependencies`：父 Review Run 对 Run、PaperVersion 或 ChunkSet 的受限依赖；
- `review_outputs`：按 type/key/version 追加写入结构化结果，不覆盖旧版本；
- `human_input_requests` / `human_inputs`：版本化请求与单次不可变用户输入；
- `artifacts`：保存 Storage 引用、哈希、大小、MIME 和来源 Output，不保存文件正文。

创建 Review Run 时，通用 Run、扩展记录、首个 Event、Outbox 和幂等记录只有一个 commit。任何写入
失败都会由会话事务整体回滚；PostgreSQL 集成测试覆盖了 Review 扩展写入异常后的无残留行为。

## 核心不变量

- `ReviewRun.run_id` 必须对应一个真实通用 Run，生命周期只在通用 Run 状态机中维护；
- Review 读取必须同时满足 owner、Project 和 run_id；不存在与越权使用相同空结果边界；
- ReviewOutput 只能追加新版本，数据库唯一约束拒绝重复版本和重复节点幂等键；
- 同一 HumanInputRequest 最多写入一个 HumanInput，Input 必须携带匹配的 request_version；
- 同一 Review Run 同时最多有一个开放的 HumanInputRequest；
- Artifact 只保存受控 metadata 与 Storage 引用，大型 Markdown/矩阵不能放进 JSONB；
- ReviewRun 统计摘要只允许来源数量、模型调用数和 token 数等固定计数，不承载任意运行数据；
- 数据库 FK 只证明引用目标存在；所有跨聚合配对仍须由已授权写服务在同一事务内校验所属 Review
  Run、Project 和 owner；
- 所有版本名使用稳定的 `name.vN`；运行时参数快照不能依赖部署进程内存。

## 关键决定与替代方案

### 为什么不用一个多态 `target_id`

PostgreSQL 不能让一个 `target_id` 根据类型可靠地引用三张表。当前实现使用三个显式 nullable FK，并用
Check 保证恰好一个目标与 `dependency_type` 匹配；三类目标各自有部分唯一索引。字段更多，但 FK、
查询和重复依赖行为都可由数据库验证。

### 为什么 Output 不直接保存最终 Markdown

Output 用于模型之间的小型结构化产物和版本追溯，领域层限制序列化 JSON 不超过 256 KiB。大正文和
导出文件进入 Artifact Storage，数据库只保存引用与哈希，从而避免 Graph State 和普通查询携带大型
内容。

### 为什么暂时复用通用 IdempotencyKey

现有表已经提供 `(owner_id, idempotency_key)` 唯一约束、请求指纹和 `run_id` 回放信息，足以支持
Review Run 创建，不需要为尚未实现的 API 再建一套幂等机制。代价是不同创建类 API 的幂等键共享
owner 命名空间，这与现有上传和 RAG 提问行为一致。

## 失败、重复、取消和恢复

- 同键同请求返回原 Review Run，不产生第二个 Event 或 Outbox；同键不同请求抛幂等冲突；
- Project 不存在、跨 owner 或已归档时不创建任何记录；
- 重复 Step、Source、Dependency、Output、HumanInput 和 Artifact 由对应唯一约束兜底；
- 本切片只保存这些状态所需的数据，不推进依赖或人工输入状态；
- 取消仍由通用 Run 状态机负责；依赖恢复属于切片 4，HITL 提交与恢复属于切片 7。

## 安全和可观测性

研究问题保存在业务表和 Run 的小型输入快照，不写入 Event；创建 Event 只包含状态和 Workflow 版本。
Prompt 正文、论文全文、生成 Markdown 和 Secret 均不进入 Event 或本模块的普通 metadata。Artifact 的
Storage Key 是应用生成的相对键，领域创建函数拒绝绝对路径与 `..` 路径段。

Run Detail 后续可以通过 Step sequence、Source rank、Dependency status、Output version 和 Event
sequence 组合出可理解的时间线；这些数据不能由 LangGraph Checkpoint 替代。

## 重要测试和运行结果

- 领域测试覆盖版本格式、受限枚举、依赖目标、不重复解决 HumanInputRequest、Output 大小和 Artifact
  哈希/路径边界；
- 应用测试覆盖原子创建 bundle、Project/owner/归档边界、幂等回放和冲突；
- PostgreSQL 集成测试覆盖所有实体往返、owner/Project 查询隔离、追加版本、依赖与 HumanInput 唯一
  约束，以及创建中途失败后的事务回滚；
- Alembic 在独立临时 PostgreSQL 数据库实际通过 `upgrade head → downgrade -1 → upgrade head`。

实际验证结果：定向领域/应用测试 `11 passed`、定向 PostgreSQL 集成测试 `7 passed`；Backend 完整
非集成测试 `398 passed, 4 skipped`、完整 PostgreSQL/Testcontainers integration `93 passed`；
`ruff check src tests` 与 `pyright` 通过。本切片没有前端改动，因此未重复运行 Web 测试与构建。

## 代码入口

- `backend/src/literature_agent/domain/review.py`
- `backend/src/literature_agent/application/review_workflow_service.py`
- `backend/src/literature_agent/application/ports/review_repository.py`
- `backend/src/literature_agent/infrastructure/persistence/review_repository.py`
- `backend/src/literature_agent/infrastructure/persistence/models.py`
- `backend/migrations/versions/a8c3e5f7b9d1_create_review_workflow_contracts.py`

## 已知限制与扩展路径

- 尚无 Review HTTP API 和前端页面，`Idempotency-Key` 只在应用服务契约中生效；
- arXiv 导入切片已为 Source 增加 scoped 行锁与受控状态保存，并能追加检索 Step 和导入依赖；其他
  Step、Dependency 对账和状态推进仍由后续切片实现；
- Request 的数据库约束可以阻止第二条 Input，但切片 7 仍必须用行锁和条件更新原子解决 Request；
- 数据库只保证目标存在和主要唯一性，不自动保证跨聚合归属：ReviewRun 当前 Output/Artifact、
  ReviewSource 的 Paper/PaperVersion 配对、Request 的 resolved Input、Artifact 的
  Project/owner/来源 Output，都必须由后续写服务同事务校验并补跨 Run/Project/owner 拒绝测试；
- Artifact Storage 文件写入、内容哈希后提交和重复文件对账留到切片 9。

## 60 秒面试说明

Phase 3 开始时，我没有先把 LangGraph 状态当作所有业务数据，而是先建立 PostgreSQL 契约。通用 Run
保存生命周期和 Project/owner，`review_runs` 保存研究问题与版本快照，Step、Source、Dependency 和
Output 分别解决可观察进度、论文来源、子任务等待和产物版本化。依赖没有使用无法建立真实外键的
多态 ID，而是三个显式 FK 加 Check 和部分唯一索引。Human Input 用请求版本、单开放请求和单输入
唯一约束支持后续可靠 Resume。最终文件不进 JSONB 或 Graph State，只在 Artifact 表保存 Storage
引用和哈希。创建 Review Run 时 Run、Event、Outbox、扩展记录和幂等键同事务提交，因此 HTTP 响应
丢失或队列至少一次投递都不会重复产生业务 Run。
