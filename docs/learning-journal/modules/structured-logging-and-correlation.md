# 结构化日志与 Correlation ID

Phase 4 切片 5 完成后成文（2026-08-23）。

## 解决的问题

API 请求、ARQ 投递、Worker Attempt、模型调用和业务 Event 分属不同执行边界。自由文本日志既难检索，
也容易因 `exc_info`、Prompt 或请求正文泄漏私有论文与用户问题。本模块提供最低可诊断链路：每行固定为
JSON，API 接受或生成 Correlation ID，Worker 根据自己的 Job 事实建立新关联上下文，再用 PostgreSQL
认领得到的 Run/Attempt 事实补齐上下文。

它不替代业务 Event、`run_attempts` 或 `model_invocations`，也不实现 Trace、日志平台、告警或 SLA。

## 边界与执行流程

```text
HTTP X-Correlation-ID（合法时接受，否则 UUID）
  → CorrelationMiddleware 绑定 service=api + correlation_id
  → Dependency 把 correlation_id 传给 mutation Application Service
  → Run/Event 在既有短事务中保存 correlation_id
  → 响应回显 X-Correlation-ID

ARQ execute_run(run_id)
  → job_id + run_id 的 SHA-256 摘要生成有界 worker correlation
  → RunExecutionService 从 PostgreSQL 条件认领 Run 并创建 Attempt
  → 绑定 project_id/run_id/run_type/attempt_id
  → 执行器、ModelGateway、heartbeat 继承当前 asyncio Context
  → 退出作用域后 contextvars token reset
```

- 中立模块 `backend/src/literature_agent/observability.py` 只使用标准库 logging/contextvars 和 ASGI
  协议，不进入 Domain，也不依赖 FastAPI/ARQ 类型；
- `CorrelationMiddleware` 只记录 HTTP method、路由模板/path、status 和 duration，不读取 query、body 或
  headers；合法客户端 ID 限定为 1–128 个 `[A-Za-z0-9._:-]` 字符；
- ARQ Job 仍只携带 `run_id`。API 进程的 contextvar 不跨进程传播；Worker correlation 是当前 Job 的
  本地诊断标识，API mutation 与 Run 的关系仍可经持久 Event correlation 查询；
- `configure_logging` 只维护一个项目 JSON Handler，不删除 pytest/宿主已有 Handler，重复
  `create_app()` 或 Worker startup 不会叠加项目 Handler；`AGENT_LOG_LEVEL` 经统一 `Settings` 严格解析，
  API、Worker 与 Uvicorn logger/handler 使用同一阈值。允许值为
  `DEBUG/INFO/WARNING/ERROR/CRITICAL`，大小写不敏感，默认 `INFO`，未知值使进程启动失败；
- `scripts/dev.sh` 使用 `--no-access-log` 关闭 Uvicorn 的逐请求 access log。HTTP 请求仍由
  `CorrelationMiddleware` 输出带路由模板、状态、耗时和 correlation 的安全结构化事件，避免同一请求
  同时出现无字段价值的 `unstructured_log` 与 `request_completed`。

## 日志契约与安全

每行必有：

```text
timestamp level event service correlation_id
```

事件按需允许：

```text
run_id project_id attempt_id run_type stage duration_ms error_code exception_type
status operation provider model method path status_code outbox_id count
semantic_count fulltext_count merged_count evidence_count
```

Formatter 是显式 allowlist，不会把任意 `LogRecord.extra` 整包倾倒。没有迁移的旧自由文本 logger 会
降级为固定 `unstructured_log`，其 message 也不序列化。异常只保留类型/稳定 error code，不格式化
异常 message 或 traceback。因此 Authorization、Cookie、API Key、完整 Prompt/响应、PDF/Chunk 全文、
用户问题和 feedback 即使误放入任意 extra，也不会进入 JSON。

这不是内容检测或日志平台侧脱敏：新增允许字段仍必须先证明低敏、有界且对诊断必要。`path` 只使用已
匹配的路由模板且不含 query string；404 等无匹配路由固定为 `[unmatched]`，不回退记录可能含论文名、
token 或其他敏感片段的原始 path。高基数 Run/Project/Attempt ID 只进入日志，不应在切片 6 变成
Metrics Label。

## 关键事件与失败行为

- API：`request_completed` / `request_failed`；未处理异常返回安全 500 并仍回显 Correlation ID；
- Run：`run_execution_started/completed/failed/retry/paused/skipped`，带持久 Run/Attempt 上下文和耗时；
- Model：`model_request_completed/failed`，仅 capability、provider、model、status、duration、error type；
- Queue/Event：`outbox_dispatch_completed/failed`、`event_notification_failed` 及三个 Worker loop 的固定
  completed/failed 事件；
- Retrieval：`retrieval_completed` 只记录候选计数，不记录 query、Chunk、分数或 Evidence 正文。

标准 logging 是本地同步输出，但接入点不增加外部网络 IO，也不改变已有事务顺序。ModelInvocation、
Event notifier、Attempt close 和 heartbeat 的“记录失败不破坏业务”语义保持不变。Formatter 只接受
字符串、布尔、整数、有限浮点和 `null`；其他值统一为 `[invalid]`，绝不调用对象的 `str()` 或 JSON
自定义编码器，`NaN/Infinity` 也不会形成非标准 JSON。`log_event` 隔离 Handler/Formatter 故障，调用方
仍只应提供允许的小型标量。

## 重要测试和实际结果

- `tests/test_observability.py`：合法单行 JSON、UTC、严格 allowlist、异常脱敏、嵌套恢复、asyncio 并发
  隔离、重复配置及 Uvicorn 等级同步；
- `tests/infrastructure/test_config.py`：日志等级默认值、大小写归一化和未知值 fail-fast；
- `tests/test_dev_script.py`：本地启动关闭重复 Uvicorn access log；
- `tests/api/test_correlation.py`：header 接受/拒绝/生成/回显、安全 500、query/header 不入日志、退出
  reset；Run、Upload、Conversation、Review API 测试验证客户端 correlation 进入 Event 或服务调用；
- Run/Worker/Model 测试：持久 Run/Attempt 上下文、各既有 outcome、Worker 有界确定 correlation、
  Model 成功/失败日志不含 Prompt、结果或异常正文；
- 2026-08-29 日志等级维护验证：配置、Formatter、开发脚本与 Worker 定向测试 `45 passed`；
  `ruff check src tests`、`pyright`、`bash -n scripts/dev.sh` 与 `git diff --check` 通过；
- 切片 5 最终定向测试：`103 passed`；完整非集成（安全修复前一轮，生产路径随后仅收紧日志序列化）：
  `637 passed, 4 skipped`；相关 PostgreSQL/Valkey
  Run/Outbox/Worker/Event/ModelInvocation/Retrieval 集成：`41 passed`；`ruff check src tests` 与
  `pyright` 通过。普通测试未访问实时 arXiv 或付费 Provider。

## 代码入口

- 核心：`backend/src/literature_agent/observability.py`
- API：`main.py`、`api/dependencies.py` 及 Run/Upload/Conversation/Review mutation routes
- Worker/应用：`worker.py`、`application/run_execution_service.py`、`model_gateway.py`、
  `outbox_dispatch_service.py`、`event_notification.py`、`retriever.py`
- 测试：`backend/tests/test_observability.py`、`tests/api/test_correlation.py` 及上述应用测试

## 已知限制

- 没有 OpenTelemetry Trace、集中日志采集、保留策略、搜索 UI 或告警；本地 stdout/stderr 是唯一输出；
- 当前只有一个统一阈值，没有按模块独立等级或“文件保留 INFO、终端只显示 WARNING”的双 Handler；将
  `AGENT_LOG_LEVEL` 提高到 `WARNING` 会同时丢弃 API 与 Worker 的全部 INFO 日志，但不会影响数据库中的
  Event、Run/Attempt、ModelInvocation 或 Prometheus Metrics；
- API correlation 不作为 ARQ payload 跨进程传播；API Event correlation 和 Worker 自有 correlation 通过
  同一 `run_id` 关联，而不是伪装成分布式 Trace；
- 尚未逐行机械迁移全仓所有历史 logger；Formatter 会安全降级旧消息，关键 Worker/Run/Provider/Outbox/
  Event/Retrieval 路径已经迁移；
- 未记录 step/thread 等未来可选上下文，Phase 4 当前最低诊断不需要提前扩展；
- Phase 4 切片 6 已在独立的 `prometheus-metrics.md` 落地进程级 Metrics。Correlation/Run/Project 等
  高基数日志上下文不会成为 Label；日志与 Metrics 都不替代 PostgreSQL 业务事实。

## 60 秒面试说明

“我用标准库 logging 和自己的 JSON Formatter 做了最低可观测性。Formatter 不是把 `extra` 全 dump，
而是严格字段白名单；旧文本消息、异常 message 和 traceback 都不输出，所以 Prompt、PDF 正文或 Provider
错误体不会因为 `exc_info` 泄漏。API 中间件接受 1–128 字符的安全 Correlation ID，否则生成 UUID，
用 contextvars 在请求内传播并回显响应头；mutation 同一个 ID 会进入持久 Event。Worker 不假设
contextvar 能跨进程，也不往 ARQ Job 塞请求数据，而是从 job_id/run_id 建自己的 correlation，认领 Run
后再绑定数据库中的 Project、Run type 和 Attempt。Context 用 token 精确 reset，测试覆盖嵌套和并发
隔离。日志用于诊断，Event、Attempt、ModelInvocation 仍分别是产品历史和持久事实。”
