# Project 模块

## 解决的问题

提供研究活动的顶层授权和检索边界：用户可以创建、列出、查看、修改自己的 Project，并可以归档与恢复 Project。Run/Event 直接归属 Project；Paper 是 owner 个人文献库资产，通过固定 `selected_version_id` 的 `ProjectPaper` 被 Project 收录。该边界为后续按项目检索和引用校验提供隔离基础。

## 边界与执行流程

```text
HTTP Route (api/projects.py)
  → ProjectService (application/project_service.py)
    → create_project / list_projects(include_archived) / get_project
      / update_project / archive_project / restore_project
      → ProjectRepository Port
        → SqlalchemyProjectRepository (infrastructure/persistence/project_repository.py)
          → ProjectORM (infrastructure/persistence/models.py)
```

- Route 只处理请求/响应和 Actor 注入，不直接访问数据库。
- Service 负责事务编排和授权校验（`project.owner_id == actor.owner_id`）。
- Domain 只保存 `Project` 实体和创建/校验规则。
- Repository Port 让应用层可以在单元测试中使用 Fake，PostgreSQL 适配器处理 ORM 映射。

## 状态、数据模型和事务

- `Project` 是不可变 dataclass，字段：`project_id`、`owner_id`、`name`、`description`、`created_at`、`updated_at`、`archived_at`（Phase 2 切片 1 新增，None 表示 active）。
- 校验规则：`name` 非空且不超过 200 字符；`update_details` 复用同一校验。
- 归档/恢复为幂等领域操作（`archive()`/`restore()`），归档状态由 `archived_at` 派生，无独立 status 列。
- 数据库表 `projects` 以 `project_id` 为主键，`owner_id` 建立索引；`list_by_owner` 默认只返回 active，`include_archived=true` 时返回全部。
- 创建 Project 时在同一事务中 `repo.add` 后 `commit`；修改/归档/恢复经条件更新持久化并写 `updated_at`。
- 归档前置校验：存在非终态 Run（`ACTIVE_RUN_STATUSES`：QUEUED/RUNNING/RETRY_WAIT/CANCEL_REQUESTED，唯一定义在 `domain/run.py`）时拒绝归档，API 返回 409 `project_has_active_runs`；已归档 Project 的写入口（上传、收录、移除收录、改名）返回 409 `project_archived`，读接口保持可用。

## 关键决定与替代方案

- 不引入通用 RBAC：首版只保留 `owner_id`，后续成员权限通过单独阶段扩展。
- Actor Context 由 `get_actor` 依赖提供，当前从 `Settings.dev_actor_id` 读取，生产环境替换为真实认证依赖。
- Repository 返回 `None` 表示不存在，Service 将其统一转换为 `ProjectNotFoundError`，避免 Route 直接处理 `None`。

## 失败、重试、重复和取消行为

- 创建 Project 当前不是幂等接口：每次成功请求生成新的 UUID；相同名称也没有 owner 级唯一约束。
- 客户端重试创建请求可能产生两个 Project；如后续产品需要防重，应显式增加 Idempotency-Key，而不是依赖随机主键。
- 归档/恢复幂等：重复归档不刷新 `archived_at`，重复恢复为 no-op；归档/恢复不产生 Run/Event。
- 归档与非终态 Run 冲突返回 409，不产生部分状态。
- 无取消场景。

## 安全和可观测性

- 所有查询按 `owner_id` 过滤，不存在则返回 404，不泄漏所有权信息。
- 不记录敏感配置或用户数据。

## 重要测试和运行结果

- `tests/domain/test_project.py`：领域校验、归档/恢复幂等、`update_details`。
- `tests/application/test_project_service.py`：所有权隔离、归档幂等、非终态 Run 409、归档后写入口 409。
- `tests/api/test_projects.py`：HTTP 契约（PATCH、archive/restore、`include_archived`、稳定业务码）。
- `tests/integration/test_project_repository.py`：PostgreSQL 持久化、归档过滤与隔离。

切片 2 完成时的历史快照：`uv run pytest -v` 62 passed。当前测试基线以 Phase 1 进度记录为准。

## 代码入口

- 领域：`backend/src/literature_agent/domain/project.py`
- 端口：`backend/src/literature_agent/application/ports/project_repository.py`
- 服务：`backend/src/literature_agent/application/project_service.py`
- 适配器：`backend/src/literature_agent/infrastructure/persistence/project_repository.py`
- 路由：`backend/src/literature_agent/api/projects.py`

## 已知限制

- 当前 Actor 为开发用户硬编码，生产必须替换。
- 修改名称/描述、归档/恢复已在 Phase 2 切片 1 实现；仍无永久删除（Phase 4）、标签和成员管理。
- 归档/恢复暂无前端入口（延后到 Phase 2 切片 9 的 UI）。
- 没有按 Project 的配额或预算限制。

## 60 秒面试说明

"Project 模块是系统的资源边界。我们使用不可变领域实体、端口适配器模式，把 HTTP、应用服务、领域和 PostgreSQL 解耦。Service 负责事务和所有权校验，Repository 查询始终带 `owner_id`，找不到统一抛 404，避免越权信息泄漏。除创建、列表和详情外，Project 支持改名与幂等归档/恢复：归档状态由 `archived_at` 派生，存在非终态 Run 时拒绝归档，归档后所有写入口返回 409 而历史读接口保持可用——用归档而不是删除来保留后续 RAG 引用和 Workflow 历史的可追溯性。"
