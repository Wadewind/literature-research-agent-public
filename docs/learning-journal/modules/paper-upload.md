# 文献上传、复用与 Project 收录

## 解决的问题

用户上传 PDF 后，系统需要安全存储、可恢复地解析，并避免同一用户在多个 Project 中反复上传和解析相同 PDF。当前模型将“物理文献资产”与“Project 收录”分开：

- `Paper` / `PaperVersion` 属于 owner 级个人文献库；
- `ProjectPaper` 表示 Project 收录某个 Paper，并固定 `selected_version_id`；
- 同一 owner + SHA-256 在顺序请求中自动复用 canonical PaperVersion；不进行跨 owner 复用。

## 执行流程

```text
POST /projects/{project_id}/paper-files
  → 文件校验 + SHA-256 + Idempotency-Key
  → 校验 Project 所有权
  → 查询 owner + file_hash
      ├─ 命中：补充 ProjectPaper
      │    ├─ 已解析：立即返回，run_id = null
      │    └─ 处理中：返回原 ingestion_run_id
      └─ 未命中：创建 Paper + Version + Run + Event + Outbox + ProjectPaper
```

用户也可通过 `POST /api/v1/projects/{project_id}/papers` 把个人文献库的已有 Version 直接收录到 Project。`DELETE /api/v1/projects/{project_id}/papers/{paper_id}` 只移除关系，不删除 Paper、Version、PDF 或 Parse Revision。

## 数据、事务与不变量

- `papers`：`paper_id`、`owner_id`、`merged_into_paper_id`、`created_at`。历史重复 Paper 通过 `merged_into_paper_id` 无损归并，不物理删除。
- `paper_versions`：包含 `owner_id`、`file_hash`、`ingestion_run_id`、`is_deduplication_canonical` 和解析指针。部分唯一索引仅限 canonical 行，保留旧重复版本的同时约束新写入。
- `project_papers`：复合主键 `(project_id, paper_id)`，并持有 `selected_version_id`。
- 新文件的 Paper/Version/Run/Event/Outbox/ProjectPaper/Idempotency 在同一短事务提交。Storage 写入先于数据库 commit，回滚可能遗留孤儿文件。
- 已有 Version 的复用不写 Storage、不创建新 Run、不重新解析。
- Paper 支持幂等归档/恢复（Phase 2 切片 1，`PaperService`）：归档后从默认个人库列表与收录选择中隐藏，已有 ProjectPaper 与历史引用不受影响；同哈希上传命中已归档 Paper 的 canonical Version 时正常复用，`UploadResult` 返回 `paper_archived: true` 提示，不自动恢复归档；向 Project 收录已归档 Paper 返回 409 `paper_archived`。
- 跨 owner 查询、收录与文件读取均返回 404，不暴露资源存在性。

## 历史数据迁移

`c84f2d7a91e6` 迁移优先选择“已有 Parse Revision，其次创建时间最早”的 Version 作为 canonical，将旧 Project 收录指向 canonical Paper/Version。重复 PaperVersion、Parse Revision、Element 与 Storage 文件均保留，只标记为非 canonical，因此迁移不会为实现去重而删除用户历史数据。

## 失败、重试与幂等

- 非 PDF、超大或 Magic Bytes 非法：HTTP 400；Project 不存在/越权：404。
- 同一 `Idempotency-Key` + 同一请求指纹：返回已保存响应；不同指纹：409。
- 已在 Project 中的 Paper 再次收录为幂等成功，`already_added=true`。
- 两个不同 Idempotency-Key 的同哈希请求并发时，PostgreSQL canonical 部分唯一索引保证不会重复落库；当前 loser 会回滚并返回冲突异常，尚未自动回读 winner 的 canonical Version，调用方需要重试。

## 重要测试

- Domain/Application：新上传、同 Project 幂等、跨 Project 复用、已就绪时无新 Run、移除收录不删资产。
- API：个人库/Project 列表、收录/移除、Project 成员关系限制的 PDF 读取。
- PostgreSQL：`ProjectPaperRepository`、owner/hash canonical 唯一性、owner 隔离。
- 迁移：已在本地含历史重复 PDF 的 PostgreSQL 数据上执行成功，重复资产保留。

## 代码入口

- 应用服务：`application/ingestion_service.py`、`project_library_service.py`、`paper_query_service.py`
- 领域：`domain/paper.py`、`paper_version.py`、`project_paper.py`
- 路由：`api/paper_files.py`、`api/papers.py`
- 持久化：`infrastructure/persistence/*paper*_repository.py`、`models.py`
- 迁移：`migrations/versions/c84f2d7a91e6_新增个人文献库与_project_收录.py`

## 已知限制

- 归并后的历史重复 Storage 文件与解析记录暂不自动回收；待后续有可观测的 GC 机制再处理。
- 目前不做 DOI/标题/作者模糊合并，不同二进制内容即是不同 Paper。
- 每条 ProjectPaper 当前固定一个 Version，尚无前端版本切换功能。
- 同哈希并发新写入的 loser 尚不能在同一请求内转换为复用成功。

## 60 秒面试说明

“我把 Paper 从 Project 直接子资源改成 owner 级个人文献资产，Project 通过显式关系收录它并固定 Version。上传用 owner + SHA-256 查重：命中就复用已有解析或正在运行的 Run，不命中才原子创建 Paper、Version、Run、Event 和 Outbox。迁移面对旧重复数据采用无损 canonical 标记和部分唯一索引，不会为去重删掉用户的历史解析数据。”
