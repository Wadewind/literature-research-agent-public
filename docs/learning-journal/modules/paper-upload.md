# 文献上传与版本模块

## 解决的问题

让用户可以把 PDF 安全上传到指定 Project，系统在校验、存储后创建稳定的 Paper/PaperVersion，并生成一个 Ingestion Run。通过 `Idempotency-Key` 保证同一请求不重复创建版本和 Run，支持断线重试。

## 边界与执行流程

```text
HTTP Route (api/paper_files.py)
  → IngestionService (application/ingestion_service.py)
    → 校验文件 → 计算 SHA-256 → 检查 Idempotency
      → 校验 Project 所有权
        → 创建 Paper + PaperVersion
          → 写入 Storage
            → 创建 Run + Event + Queue Outbox（同一事务）
              → 写入 IdempotencyKey
                → commit
```

- Route 读取 `UploadFile` 和 `Idempotency-Key` header，把字节流交给 Service。
- Service 负责所有业务编排：校验、哈希、幂等、Project 所有权、Run/Event 写入、文件存储。
- `Storage` Port 把文件保存到本地或未来替换为对象存储；key 由系统生成，不依赖用户文件名。
- `PaperRepository`、`PaperVersionRepository`、`IdempotencyRepository` 参与同一数据库事务。

## 状态、数据模型和事务

- `Paper`：`paper_id`、`owner_id`、`project_id`、`created_at`。
- `PaperVersion`：`version_id`、`paper_id`、`file_hash`、`storage_key`、`size_bytes`、`content_type`、`created_at`。
- `IdempotencyRecord`：`owner_id`、`idempotency_key`、`project_id`、`request_hash`、`run_id`。
- 数据库表：`papers`、`paper_versions`、`idempotency_keys`；`idempotency_keys` 以 `(owner_id, idempotency_key)` 为主键。
- 文件校验：必须提供 `Idempotency-Key`、大小不超过 `max_upload_size_bytes`、内容以 `%PDF-` 开头。
- 请求指纹 `request_hash` 由 `project_id + idempotency_key + file_hash + filename + content_type` 计算，用于检测同一 key 的不同请求。

## 关键决定与替代方案

- 使用系统生成的 `storage_key`（`owner_id/project_id/paper_id/paper.pdf`），避免文件名注入和路径穿越。
- 先写 Storage 再 commit DB：存储失败不创建业务记录；DB 回滚时可能遗留文件（当前接受，后续可对账清理）。
- Paper/PaperVersion 与 Run/Event/Queue Outbox 在同一事务创建，保证上传结果、任务历史和投递记录一致。
- 先查 Idempotency 记录再插入， race 场景由数据库唯一约束兜底。

## 失败、重试、重复和取消行为

- 非法文件/大小/缺失幂等键：`FileValidationError` → HTTP 400。
- Project 不存在或不属于当前 actor：`ProjectNotFoundError` → HTTP 404。
- 同一 `Idempotency-Key` 但不同请求指纹：`IdempotencyConflictError` → HTTP 409。
- 同一 key + 同一请求指纹：直接返回已创建 Run 的信息，业务上 Effectively Once。
- Run 创建后处于 `QUEUED`，由 Outbox 派发循环投递给 Worker（切片 5 起）；重复 Job 由 Worker 幂等执行兜底。

## 安全和可观测性

- 文件名只做展示，经清理后使用；不进入存储路径。
- 校验 PDF Magic Bytes，拒绝非 PDF 上传。
- 上传大小在读取完整内容后检查，由反向代理/服务器层做更外层限制。
- Storage Adapter 阻止 `..` 和越界路径。
- Event Payload 不保存文件内容或完整路径。

## 重要测试和运行结果

- `tests/application/test_ingestion_service.py`：合法上传、非法文件、超尺寸、缺失幂等键、Project 不存在、幂等命中、幂等冲突。
- `tests/api/test_paper_files.py`：HTTP 202/400/404/409 契约。
- `tests/integration/test_paper_repository.py`、`test_paper_version_repository.py`、`test_idempotency_repository.py`：PostgreSQL 持久化、唯一约束、外键隔离。

当前全部通过：`uv run pytest -q` 113 passed（切片 6 完成后）。

## 代码入口

- 领域：`backend/src/literature_agent/domain/paper.py`、`paper_version.py`、`exceptions.py`
- 端口：`backend/src/literature_agent/application/ports/storage.py`、`paper_repository.py`、`paper_version_repository.py`、`idempotency_repository.py`、`outbox_repository.py`
- 服务：`backend/src/literature_agent/application/ingestion_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/storage/local_storage.py`、`infrastructure/persistence/paper_repository.py`、`paper_version_repository.py`、`idempotency_repository.py`
- 路由：`backend/src/literature_agent/api/paper_files.py`
- 迁移：`backend/migrations/versions/3ce12fa8e5a5_create_papers_paper_versions_and_.py`

## 已知限制

- Storage 写入在 DB 事务内，若 DB 回滚可能产生孤儿文件。
- Worker 执行体已接入 `IngestionExecutor` + Fake Parser（切片 6），真实 PDF 解析在切片 7 接入。
- 未实现跨上传的内容去重；相同 PDF 多次上传会创建多个 PaperVersion。
- 未实现 SSE/实时通知，用户需轮询 Run 状态。
- 本地文件存储，未替换为 S3 等对象存储。

## 60 秒面试说明

"文献上传模块把 PDF 校验、内容哈希、系统存储、Paper/PaperVersion 元数据和 Ingestion Run 创建放在同一个事务里，并通过 Idempotency-Key 保证重复提交不会创建重复版本。Service 不依赖具体存储实现，Storage 是可替换的 Port；文件名只做展示，存储键由系统生成，避免路径安全问题。当前 Run 创建后停留在 QUEUED，下一步交给 Worker 消费。"
