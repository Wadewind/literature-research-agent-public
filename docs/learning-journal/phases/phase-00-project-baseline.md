# Phase 0：项目基线与技术验证

## 状态

已完成。开始日期：2026-08-10；完成日期：2026-08-13。

本阶段按“概念学习与隔离技术验证”口径完成，相关实验代码和学习记录保存在 `notebook/`，不作为生产代码入口。API、Worker、数据库迁移、Docker Compose、自动测试和前端工程将在进入对应项目垂直切片时按需落地，不因 Phase 0 完成而视为已经具备生产工程基线。

## 目标和用户可见结果

建立无需真实模型密钥即可运行和验证的开发基线。阶段结束时，开发者可以通过 Docker Compose 启动 API、Worker、PostgreSQL/pgvector 和 Valkey，检查健康状态，投递并消费一个测试 Job，并演示一个最小 LangGraph 的暂停与恢复。

本阶段不提供文献业务功能。

## 范围

- Python 3.13 与 uv 项目基线；
- FastAPI 应用、Lifespan、依赖注入和健康检查；
- React/Vite 最小 Web 骨架；
- PostgreSQL/pgvector、Valkey 和 Docker Compose 健康检查；
- SQLAlchemy 2.0 Async、psycopg 3 和 Alembic 初始迁移；
- ARQ Worker 和一个测试 Job；
- 最小 LangGraph State/Node/Edge/Checkpoint/Interrupt/Resume 实验；
- Fake Chat、Fake Embedding，以及必要的 Fake Queue/Sandbox 边界；
- 格式化、静态检查、单元测试、集成测试和 CI 命令。

## 非范围

- Project、Paper、PDF 导入、RAG、综述 Workflow 和 Research Agent；
- 真实模型或实时学术 API 请求；
- 生产 Sandbox；
- 为未来阶段预建通用框架、完整领域目录或数据库模型。

## 涉及模块

- API 启动与依赖生命周期；
- 数据库连接和迁移；
- Queue/Worker；
- LangGraph 技术验证；
- 本地部署、测试和 CI。

## 状态、API、Event、数据库和 LangGraph 变化

- API：只定义存活与就绪检查；具体路径在实现切片前确定。
- 数据库：建立 Alembic 基线及用于验证连接/迁移的最小结构，不引入文献业务表。
- Worker：测试 Job 只携带稳定的小型标识，不承载业务正文。
- Event：本阶段不设计通用业务 Event 模型；只保留日志和测试证据。
- LangGraph：仅构建隔离的技术验证图，不把实验结构当作未来 Workflow 契约。

## 关键不变量和失败行为

- 普通测试和 CI 不需要真实模型 Key，也不发起付费模型请求。
- API 和 Worker 各自管理资源生命周期，API 请求断开不承担后台 Job 生命周期。
- 数据库事务必须显式、短小；外部调用不放在事务中。
- 健康检查区分进程存活与依赖就绪。
- Worker、数据库或 Valkey 不可用时，测试必须快速失败并给出可定位信息。
- Compose 启动顺序依赖健康状态，而不是固定等待时间。
- LangGraph State 只保存小型结构化数据；Checkpoint 不充当业务数据库。

## 第一阶段学习顺序

1. **系统边界**：画出 Web → FastAPI → PostgreSQL/Valkey → ARQ Worker，以及 LangGraph 在 Worker 内部的位置；能解释 API、Job、业务 Run 和 Checkpoint 不是同一个概念。
2. **asyncio 生命周期**：掌握协程、Task、TaskGroup、超时、取消传播和资源清理，重点理解 `CancelledError` 与协作式取消。
3. **FastAPI Lifespan 与依赖注入**：理解启动/关闭资源、请求级依赖、连接池生命周期，以及存活检查与就绪检查的区别。
4. **PostgreSQL 异步事务**：掌握 AsyncEngine、AsyncSession、事务提交/回滚、连接池和 Alembic；知道为什么不能跨外部 HTTP 调用持有事务。
5. **ARQ Worker 模型**：理解 enqueue、Worker、Job 参数、失败与重试；通过实验解释为什么 Queue Job 不是业务 Run、Job Result 不是事实来源。
6. **LangGraph 最小持久执行**：实现小型 State/Node/Edge 图，使用测试 Checkpointer 演示 Interrupt/Resume，并区分 Graph State 与 Checkpoint。
7. **Docker Compose 与健康检查**：掌握服务网络、依赖、volume、healthcheck 和可复现启动，不用无意义的 sleep 掩盖竞态。
8. **可测试边界**：用 Fake Chat/Embedding 和测试替身验证边界，建立 Ruff、Pyright、pytest 和集成测试的快速反馈循环。

每个主题应能回答：解决什么故障、状态由谁拥有、超时/取消/崩溃时怎样表现、如何测试和观察、当前方案有什么限制。

## 实现切片顺序

1. 固定项目布局、依赖和本地质量命令；
2. 建立 FastAPI 应用及存活/就绪测试；
3. 加入 PostgreSQL、SQLAlchemy 和可重复迁移；
4. 加入 Valkey、ARQ Worker 和测试 Job；
5. 完成最小 LangGraph 暂停/恢复实验；
6. 加入 Fake Provider 边界；
7. 建立 React/Vite 骨架；
8. 用 Compose 和 CI 验证完整基线；
9. 更新 Phase 0 复盘和实际测试证据。

## 测试方式

- 单元测试：asyncio 取消/清理、Fake Provider 和最小图行为；
- API 测试：存活与就绪状态；
- Repository 集成测试：真实 PostgreSQL 连接、事务回滚和迁移重复执行；
- Queue/Worker 集成测试：测试 Job 被消费，依赖故障能够被发现；
- LangGraph 测试：Interrupt 后保存状态并从同一线程恢复；
- Compose Smoke Test：所有必需服务健康；
- CI：在无模型密钥环境执行格式化检查、类型检查和自动测试。

具体测试命令随项目骨架确定后补充，只记录真实运行结果。

## 原计划工程验收项

以下内容保留为后续项目实现的验收清单，不再作为本次 Phase 0 学习阶段的完成阻塞项；进入相关垂直切片时必须实际实现并记录测试证据：

- API、Worker、PostgreSQL/pgvector 和 Valkey 可通过 Compose 启动；
- API 与 Worker 健康检查可以区分存活和依赖就绪；
- Alembic 迁移可在干净数据库执行，并可重复检查至最新版本；
- 一个测试 Job 能被 Worker 消费；
- 一个最小 LangGraph 能通过测试 Checkpointer 暂停并恢复；
- Fake Chat 和 Fake Embedding 可供后续测试使用；
- 格式、类型、单元和集成测试命令明确，CI 不依赖外部模型 Key；
- 开发者能独立画出并解释 API、Queue、Worker、数据库和 LangGraph 的边界；
- Phase 0 的测试证据、已知限制和复盘已更新。

## 待讨论问题

1. 健康检查端点、最小数据库结构和测试 Job 契约尚未确定，应在对应小切片开始前定稿。

## 已确定事项

- 2026-08-10：总体实施指南文件路径已与 `AGENTS.md` 统一为 `docs/spec/literature-review-agent-system-implementation-guide.md`。
- 2026-08-10：采用 `backend/` 与 `web/` 的单仓库布局。Python 项目使用 `backend/src/literature_agent/` 包结构；前端在进入对应垂直切片时再创建 React/Vite 骨架。
- 2026-08-13：Phase 0 以学习和 `notebook/` 中的隔离技术验证完成为准。容器、前端和生产工程骨架改为在实际功能切片需要时实现和验收，且不得把 notebook 实验直接视为生产实现。

## 阶段复盘与运行证据

Phase 0 的八个主题学习与隔离实验已完成，开发者已理解项目所需的主要技术边界。实验记录位于本地 `notebook/` 学习目录；该目录不作为生产源码、CI 或正式测试证据。

截至 2026-08-13，仓库中的正式后端仍是最小入口，尚未实现或运行 API、Worker、迁移、Compose 和前端的自动化验收。这些已作为已知限制迁移到后续项目垂直切片，届时只记录真实执行过的测试结果。
