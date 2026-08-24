# Phase 4 本机性能基线（2026-08-24）

## 环境与方法

- WSL2 Linux 6.18.33.2，x86_64；Intel Core i7-14650HX，容器可见 4 vCPU；内存 5.8 GiB；
- Python 3.13.14；FastAPI 0.141.1；Starlette 1.6.0；SQLAlchemy 2.0.52；psycopg 3.3.4；ARQ 0.28.0；
- PostgreSQL 18.6（`pgvector/pgvector:pg18`）；Valkey 9.1.1（`valkey/valkey:9`）；
- Fake Embedding 1024 维、Fake Chat；Phase 2 使用 Pypdf fallback，完整 Review 使用生产 Fake Parser 和
  `review-demo.v1` Fake arXiv；无外部 HTTP、零 Provider 费用；
- Phase 2 为冷一次性数据库和 Storage，4 篇、16 Elements、8 Chunks；完整 Review 为 4 个 Fixture
  Source（3 ready + 1 stable failed）；
- 每项仅一轮。GNU time 的 RSS 为对应测试/runner 进程峰值；完整 Review 则以 50 ms 间隔读取新启动
  Worker 的 `/proc/<pid>/status`，采样 RSS 并复核进程 VmHWM，不包含 PostgreSQL/Valkey 容器内存。

## 观察值

| 路径 | 数据与方法 | 实测 |
|---|---|---:|
| 存活端点开销 | TestClient `/health/live`，20 warmup + 500 次 | p50 0.834 ms；p95 1.032 ms；max 1.962 ms |
| 解析/索引 | 4 篇冷库，正式 Ingestion+Pypdf+Indexing | 总计 0.575 s |
| Retrieval | 14 题正式 hybrid Retriever | p50 14.041 ms；p95 14.927 ms；max 15.734 ms |
| RAG | 14 题 Conversation→终态（含 Retrieval/Fake Chat/提交） | p50 144.163 ms；p95 154.562 ms；max 155.751 ms |
| Review 领域阶段 | 3 个实际问题、3–4 篇，Matrix/Citation/Section Validator + 导出 | 三场景总计 0.970 ms |
| Worker 非 Review 路径 | 真实 PG+Valkey+ARQ，ingestion/indexing 与 RAG 两项 | 2 passed / 12.77 s；峰值 152,688 KiB |
| 完整 Review | 正式 API+PG+Valkey/ARQ Worker+Runtime；4 Sources；脚本自动 feedback/approve | wall 5.372 s；active 4.371 s；自动 HITL pause 1.000 s；Worker RSS/VmHWM 133,220 KiB |

存活端点 runner 本身峰值 155,060 KiB；Phase 2 runner wall 6.65 s、峰值 103,424 KiB。TestClient 不含
TCP 或业务 Repository，不能外推为 Project-scoped API 性能。Review 领域阶段实际消费问题和语料，
但不含模型、PG、Worker、HITL 或 Artifact Storage，只能描述确定性生产 Domain 路径。此前记录的
Review `6.08 s / 133,832 KiB` 是 56 个 pytest 的套件开销，已作废；它既未消费声明语料，也不是业务
路径。第四次完整旅程从 Review API 创建开始，到 Run `succeeded`、Stage `finalize` 为止：4 Sources
收敛为 3 ready + 1 failed；13 个 Step 全部 succeeded；22 个 Event；两轮脚本 HITL；6 个 Artifact
共 8,646 bytes 且逐一可读。wall 包含两段各 0.5 秒的自动 HITL pause；active 是创建/feedback/approve
后三段直到目标状态的 wall 之和。所有观察值均不是 SLA。
原始单轮样本为 wall 5.371904 s、active 4.371150 s、自动 pause 1.000000 s；Fake 模式实际外部 HTTP 和
付费 Provider 调用均为 0。

## 复现命令

首次完整 Review runner 实跑在构造 `httpx.Client` 时发现宿主存在 SOCKS proxy、虚拟环境却未安装
`socksio`，因此在发出任何请求前失败，未形成性能样本。loopback runner 随后固定
`trust_env=False`，避免本机 API 基线读取与目标无关的代理环境；该失败不会被记录为产品旅程结果。
第二次运行已成功请求 `/health/ready`，但 runner 错把生产成功值 `status=ok` 预期为 `ready`，因此仍
在创建 Project/Review 前退出。修正后 runner 同时校验 `status=ok` 与 PostgreSQL/Valkey dependency
均为 `ok`；第二次失败同样不属于业务路径样本。
第三次运行已完成 4 个 Fixture Source 的导入收敛（3 ready + 1 failed）和 Dependency wait，但 Review
恢复为 `queued/build_evidence_matrix` 后 180 秒内没有第二个 Worker Attempt。只读检查确认 ARQ 以
`run:<run_id>` 作为稳定 Job ID，同时默认保留首次 PAUSED Job Result；恢复 Outbox 的同 ID 重投因此被
ARQ 拒绝。修复保持稳定 ID 的并发去重，但把 `execute_run` 注册为 `func(..., keep_result=0)`，使已结束
Job 不留下阻塞合法恢复的 Result。该次超时是实际发现的产品可靠性缺口，不计为成功性能样本。真实
PostgreSQL+Valkey 回归 3/3 证明 PAUSED 后同一稳定 Job ID 可创建第二 Attempt，且执行中重复投递仍为
单效果。重启 Worker 后，第四次以全新 Project 完成上述正式旅程；报告只保留低敏汇总，不记录业务 UUID。

```bash
cd backend
.venv/bin/python tests/performance/run_phase4_api_baseline.py \
  --requests 500 --warmup 20 --json-output /tmp/phase-04-api-performance.json
/usr/bin/time -v .venv/bin/python tests/evaluation/run_phase2_eval.py \
  --json-output /tmp/phase-02-evaluation-phase4.json
.venv/bin/python tests/evaluation/run_phase4_review_eval.py \
  --json-output /tmp/phase-04-review-evaluation.json
/usr/bin/time -v .venv/bin/pytest -q \
  tests/integration/test_queue_worker.py::test_ingestion_then_indexing_completes_end_to_end \
  tests/integration/test_queue_worker.py::test_rag_answer_completes_end_to_end
# 另一终端先在仓库根目录运行 ./scripts/dev.sh --fake，并取得该次新 Worker PID
.venv/bin/python tests/performance/run_phase4_review_baseline.py \
  --worker-pid <fresh-worker-pid> \
  --json-output /tmp/phase-04-full-review-performance.json
```

目标规模仅 8 Chunks，精确 pgvector Retrieval 不是已证明瓶颈；没有依据提出 HNSW/IVFFlat，更不会在
本切片提前实现 ANN。若未来扩大语料，必须在相同业务 scope 和 recall 门下重新测量。
