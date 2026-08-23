# 基础 Prometheus Metrics

Phase 4 切片 6 完成后成文（2026-08-24）。

## 解决的问题

JSON Log 能追查一次具体请求或 Run，但不适合快速回答“最近执行了多少次、成功率如何、耗时分布怎样”。
本模块用 `prometheus-client` 提供一组小型、低基数、每进程内存指标。它用于本地演示诊断，不替代
PostgreSQL 中的 Run、Event、Attempt、Outbox、ModelInvocation 或 Review 事实。

## 进程边界与暴露方式

```text
API 进程    GET http://127.0.0.1:8000/metrics
              └─ 只导出 API 进程自己的 Registry

Worker 进程 GET http://127.0.0.1:8001/metrics
              └─ 只导出 Worker 进程自己的 Registry
```

API 与 Worker 是两个宿主进程，因此不能让 API `/metrics` 冒充 Worker 指标聚合器。Worker scrape server
固定绑定 `127.0.0.1`，默认端口 8001；`AGENT_WORKER_METRICS_PORT=0` 可关闭。端口占用或 Metrics server
启动失败只记录安全日志，Worker 仍继续；shutdown 会关闭 server、释放 socket 并 join thread。没有
Pushgateway、共享 multiprocess 目录、Valkey/PG 指标存储或新部署服务。

两个端点均无认证，只适用于可信本地开发网络，不应直接暴露到公网。

## 指标、Label 与 Bucket

| 指标 | Label | 含义 |
|---|---|---|
| `agent_run_started_total` | `run_type` | 成功认领的 Run |
| `agent_run_completed_total` | `run_type,status` | 已认领执行尝试的结果 |
| `agent_run_duration_seconds` | `run_type` | 已认领执行尝试耗时 |
| `agent_attempt_total` | `run_type,status` | Worker 本次执行结束时观察到的 Attempt 结果 |
| `agent_outbox_dispatch_total` | `status` | dispatched/failed/dropped |
| `agent_model_request_total` | `operation,status` | embedding/chat 请求结果 |
| `agent_model_duration_seconds` | `operation` | 模型请求耗时 |
| `agent_retrieval_duration_seconds` | `scope` | 成功检索耗时 |
| `agent_retrieval_evidence_count` | `scope` | 成功检索候选数量 |
| `agent_review_stage_total` | `stage,status` | Review Stage 执行尝试 |
| `agent_worker_active_jobs` | 无 | 当前 Worker 进程内 ARQ Job 数 |

- `run_type` 固定为 `ingestion/indexing/rag_answer/review/unknown`；
- `operation` 固定为 `embedding/chat/unknown`；
- `scope` 固定为 `project/selected_papers/version_snapshot/unknown`；
- `stage` 固定为 `ReviewStage` 的 13 个值或 `unknown`；
- status 只使用各指标定义的固定结果枚举。任何非法输入都 fail closed 到单一 `unknown`。

禁止 Label：owner/project/run/attempt/correlation/outbox ID、Provider/Model 名、query、Prompt、论文或用户
输入。Run Histogram buckets 为 `0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600` 秒；模型和检索为
`0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60` 秒；候选数为
`0, 1, 2, 3, 5, 8, 13, 20, 30, 50`。

## 采集边界与失败语义

- Run 只在条件认领成功后计 started；重复、缺失或已结束 Job 不计。已认领 Attempt 正常退出时计一次
  completed/duration/attempt；取消、暂停和受限重试分别归一化。Attempt close 是 best effort，即使持久
  close 失败，执行结果观测仍可能计数，所以该 Counter 不等于 PostgreSQL `run_attempts` 行统计；
- Worker Gauge 包住完整 ARQ Job 并在 `finally` 归还；
- Outbox 在投递结果完成持久标记之后采集；Model 在外部调用返回/异常边界采集；Retriever 在外部模型和
  只读查询都结束、结果已经形成之后采集；
- Review 记录服务或 LangGraph 节点的“执行尝试”，不是业务 Stage 唯一事实。feedback、恢复或至少一次
  重放导致的真实再次执行会增加计数；
- 所有更新由 facade 捕获异常。指标失败不改变业务返回、异常、事务顺序或外部调用位置。

Counter/Histogram/Gauge 都是当前进程内的易失观测：进程重启会归零，多 Worker 也各自拥有不同值。
至少一次投递和重放可能增加执行尝试计数，因此不能从这些 Counter 推导业务 Exactly Once；业务结果必须
查询 PostgreSQL。

## 测试与代码入口

- 核心：`backend/src/literature_agent/metrics.py`；API：`api/metrics.py`、`main.py`；Worker：`worker.py`；
- 采集点：`run_execution_service.py`、`outbox_dispatch_service.py`、`model_gateway.py`、`retriever.py`、
  `review_executor.py`、`workflows/review_graph.py`；
- 测试：`tests/test_metrics.py`、`test_health.py`、`test_worker.py` 及对应 Application/Workflow 测试；
- 实际验证：定向测试 `91 passed`，完整非集成 `655 passed, 4 skipped`，完整 PostgreSQL/Valkey
  integration `114 passed`；Ruff、Pyright、`bash -n scripts/dev.sh` 和 diff check 通过。

## 已知限制

- 没有跨进程汇聚、持久化、告警、Dashboard、OpenTelemetry 或 SLA；
- Worker 端口占用时不会尝试动态改端口，业务继续但该进程不可 scrape；
- 本切片不伪造当前无法可靠采集的 Queue depth/等待时间、token/费用、PDF 失败或通用 Step latency；这些
  不属于 Phase 4 当前最小指标契约；
- 指标端点没有公网认证或 TLS，只能绑定/访问可信本机环境。

## 60 秒面试说明

“我没有把业务 ID 塞进 Prometheus label，也没有让 API 假装汇聚 Worker。API 和 Worker 各自持有进程内
Registry，分别在 8000 `/metrics` 和 loopback 8001 暴露。所有 label 先通过固定枚举归一化，非法值只会
进入一个 `unknown` series；Run ID、Project、Provider/Model、Prompt 都不会成为 label。采集点放在事务
或外部调用的结果边界，并用 best-effort facade 隔离失败。Counter 表达执行尝试，重启会归零、重放会加
计数，PostgreSQL 的 Run/Event/Attempt 才是业务事实。”
