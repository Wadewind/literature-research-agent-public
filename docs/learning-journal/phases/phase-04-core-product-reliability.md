# Phase 4：Demo-ready Core 产品闭环、可靠性与评测

## 状态

- 当前状态：需求已收敛，待按切片开发
- 决策日期：2026-08-23
- 进入条件：Phase 3 固定 Review Workflow 已完成并通过阶段审计
- 关联决策：[ADR-0004：Demo-ready Core v1 的交付边界](../decisions/0004-demo-ready-core-v1-scope.md)

Phase 4 完成代表 **Demo-ready Core Research Backend v1** 完成。它是可在本地开发环境复现、演示、
诊断并具有评测证据的个人学习项目，不是公网生产产品，也不承诺长期数据保存、生产 SLA 或多租户部署。

## 1. 目标和用户可见结果

把 Project、个人文献库、RAG Chat 和固定 Review Workflow 收束成一套完整本地演示旅程。用户可以：

1. 创建 Project 并导入文献；
2. 对 Project/单篇/多篇论文进行带引用问答；
3. 创建 Review，观察来源导入和当前 Stage；
4. 在 Outline Interrupt 中 approve、结构化 edit 或 feedback；
5. 查看 Evidence Matrix、章节、Claim、Citation 和来源定位；
6. 下载 Markdown、Bibliography 等 Review Artifact；
7. 在失败、取消、等待、恢复或页面刷新后得到明确且可重放的结果。

Fake 模式必须完全离线、无费用且结果可重复；Real 模式用于显式的真实 arXiv、Docling 和 Provider
演示，不作为普通自动测试依赖。

## 2. 范围

### 2.1 包含

- Project 内统一的 Library、Chat、Reviews 导航和 Review 专用页面；
- Review List、Create、Detail、Stage、Sources、Outline HITL、Matrix、Sections、Citation 和 Artifact
  下载旅程；
- 结构化 Outline 表单，不以 JSON 编辑器作为普通用户主入口；
- Fake arXiv Search/Download 与仓库内小型论文 Fixture，保证 `./scripts/dev.sh --fake` 不联网；
- 继续使用 PostgreSQL/Valkey Compose 加宿主 API、Worker、Web 的本地开发启动方式，并审计 API/Worker
  共享 Storage、迁移、健康检查和错误提示；
- 标准库 `logging` 的 JSON 结构化日志、Correlation ID 和安全字段约束；
- 基于 `prometheus-client` 的小型 `/metrics`，覆盖 Run、Attempt、Outbox、Provider、Retrieval、Review
  Stage 和 Worker 活跃任务；
- 现有可靠性测试矩阵审计，只补重复投递、崩溃间隙、临时错误、取消竞争、SSE 重放、等待恢复和
  Checkpoint 损坏等缺失证据；
- 固定 Retrieval/Citation/Review 工程评测、显式真实 Provider 报告和一次可重复的本机性能基线；
- Phase 1–4 Playwright 核心旅程、离线演示 Fixture、README、模块笔记和发布复盘。

### 2.2 不包含

- 公网部署、单机服务器生产化、TLS、反向代理、高可用、多区域或 Kubernetes；
- OAuth、密码、Session/JWT、企业 SSO、复杂 RBAC 或面向不可信网络的身份系统；
- PostgreSQL/Storage 自动备份、恢复演练、RPO/RTO 承诺；
- Project/Paper 永久删除、异步 Storage GC、历史 Checkpoint/缓存自动清理；
- OpenTelemetry、OTLP Collector、Grafana、Loki、Tempo、Jaeger 或告警平台；
- 生产 SLA、错误预算、值班和生产容量承诺；
- 通用 Workflow Canvas、Prompt 编辑器、Matrix/Section 在线重写和单节点手工重跑；
- Deep Agents、ResearchAgentRuntime、MCP、Browser、Tool、Sandbox、任意代码执行或多 Agent。

不包含项不是“已经安全解决”。归档对象、孤儿缓存和历史 Checkpoint 会继续占用本地磁盘；本阶段只记录
限制，不开放可能破坏 Evidence/Citation/Artifact 历史的物理删除入口。

## 3. 交付形态

本阶段固定为“本地演示开发环境”，而不是完整本地安装包或单服务器产品：

```text
Docker Compose: PostgreSQL + Valkey
宿主进程:      FastAPI + ARQ Worker + Vite Web
共享事实:      PostgreSQL
共享文件:      同一个显式 AGENT_STORAGE_ROOT
启动入口:      ./scripts/dev.sh --fake | --real
```

全新环境仍需开发者预先安装 Python 3.13、uv、Node.js/npm 和 Docker Compose，并按 README 安装依赖。
Fake 数据损坏时允许重建演示环境；系统不承诺用户数据长期保存。

## 4. Review 前端契约

建议页面层级：

```text
Project
├─ Library
├─ Chat
└─ Reviews
   ├─ Review List
   ├─ Create Review
   └─ Review Detail
      ├─ Progress / Sources
      ├─ Outline HITL
      ├─ Evidence Matrix
      ├─ Sections / Citations
      ├─ Artifacts
      └─ Event Timeline
```

- 服务端状态继续由 TanStack Query 管理，SSE 只触发缓存更新/失效；刷新后必须从 API 恢复；
- Review Detail 复用通用 Run Event Stream，不在浏览器复制 Workflow 状态机；
- Outline edit 使用章节标题、目标和维度的结构化表单；提交仍使用版本化 HumanInput API 和
  `Idempotency-Key`；
- Citation 跳转必须能回到 Evidence 和现有 PDF 页码定位；
- Artifact 下载使用后端已校验的 Project-scoped content endpoint，不在前端拼 Storage 路径；
- 取消、等待依赖、等待输入、失败和终态必须有明确且可访问的呈现。

## 5. 离线 Fake 与显式 Real 模式

### 5.1 Fake 模式

- Fake Parser、Embedding、Chat 和 arXiv Adapter 全部由生产依赖组装边界选择；
- Fake arXiv 返回稳定、版本化的元数据和 PDF 字节，不访问实时学术 API；
- Fixture 至少覆盖成功 Review、部分来源失败、证据不足和 Outline feedback 再次 interrupt；
- 普通 pytest、Vitest 和 Playwright 不读取 `.env`，不访问网络，不产生模型费用。

### 5.2 Real 模式

- 只有 `./scripts/dev.sh --real` 或显式环境开关可以访问真实 arXiv、Docling 和 Provider；
- 报告固定 Provider/Model、Prompt/Profile 版本、日期、调用次数、token、耗时和失败样例；
- 真实结果用于校准和展示，不作为普通 CI 的稳定阻断条件。

## 6. 最低可观测性

### 6.1 JSON 日志与 Correlation

使用标准库 `logging` 和项目自有 JSON Formatter，不引入 `structlog`。API Middleware 创建或接受
Correlation ID，并通过 `contextvars` 在 API 进程内传播；Worker 从稳定 `run_id` 加载业务事实，并为
每次 Attempt 建立执行日志上下文，不依赖跨进程 `contextvars` 或在 ARQ Job 中传完整请求。日志字段按
事件需要选取：

```text
timestamp level event service correlation_id
run_id project_id attempt_id run_type stage duration_ms error_code
```

- Secret、完整 Prompt、PDF/Chunk 全文、完整模型输出和敏感用户输入不得进入日志；
- `run_id`、`project_id` 等高基数 ID 可以进入日志，但不能成为 Prometheus Label；
- Event 仍是用户可见业务历史，JSON Log 不替代 Run/Event/Attempt/ModelInvocation 事实。

### 6.2 Metrics

只引入 `prometheus-client` 并提供小型 `/metrics`。初始指标方向：

```text
agent_run_started_total{run_type}
agent_run_completed_total{run_type,status}
agent_run_duration_seconds{run_type}
agent_attempt_total{run_type,status}
agent_outbox_dispatch_total{status}
agent_model_request_total{operation,status}
agent_model_duration_seconds{operation}
agent_retrieval_duration_seconds{scope}
agent_retrieval_evidence_count{scope}
agent_review_stage_total{stage,status}
agent_worker_active_jobs
```

最终名称、Histogram Bucket 和采集点在对应切片以测试和 ADR 补充确定；Label 必须是稳定低基数枚举。
本阶段不实现 OpenTelemetry Trace。

## 7. 可靠性矩阵

先审计现有测试，再只补缺失证据。最低矩阵：

| 故障 | 期望结果 |
|---|---|
| 重复 ARQ Job | 不重复创建业务事实 |
| 外部调用后 Worker 崩溃 | 重放收敛；不可避免的重复模型调用有审计说明 |
| Provider 临时错误 | 进入受限 Retry，不直接永久失败 |
| Provider Schema/范围永久错误 | 稳定失败，不无限重试 |
| Storage 写入成功、数据库提交失败 | 只留下可复用缓存，不提交 Artifact/Event/Stage |
| 取消与 Output/Artifact 提交竞争 | Run 行锁和条件更新决定唯一结果 |
| API/Worker 重启 | PostgreSQL 和 Checkpoint 保留业务事实 |
| SSE 断线 | 使用 Event sequence/`Last-Event-ID` 重放 |
| Dependency/HumanInput 等待 | Attempt PAUSED，正常恢复不占失败预算 |
| Checkpoint 损坏 | 明确失败，不覆盖为新 Graph |

不引入 Chaos Engineering 平台；优先使用 Fake Adapter、可控异常、Testcontainers 和有限进程级测试。

## 8. 评测与性能基线

### 8.1 阻断型确定性评测

- Schema Validator、Citation/Evidence 范围闭包和伪造 Evidence 拒绝；
- Fake Review 完整运行、Interrupt/Resume、Artifact 稳定生成和重放单效果；
- owner/Project 隔离、Run/Step/Event 终态一致性；
- Citation scope validity、Artifact 引用映射完整率和关键闭包要求 100%。

### 8.2 固定语料质量回归

复用 Phase 2 语料并增加 3–5 篇、至少 3 个研究问题的 Review Fixture，覆盖正常成功、部分来源失败、
证据不足和 feedback loop。Retrieval Recall、Claim Citation Coverage 等阈值只能在首次真实基线运行后
记录，不预先填写漂亮数字，也不能通过降低断言隐藏回归。

### 8.3 真实 Provider 报告

真实 Provider 评测显式启用、允许成本和波动，不阻断普通 CI。第一版使用人工评分表，不引入
LLM-as-a-Judge。

### 8.4 本机性能基线

记录硬件、软件版本、Chunk 数量、Embedding 维度、缓存状态和 Fake/Real Provider，测量 API、Retrieval、
解析/索引、RAG、3–5 篇 Review 和 Worker 峰值内存。结果是可重复观察值，不是 SLA。只有实测证明精确
向量检索在目标规模成为瓶颈时，才单独讨论 HNSW/IVFFlat。

## 9. 关键不变量和失败行为

- PostgreSQL 仍是 Run、Event、Evidence、Citation、Artifact 和 Checkpoint 业务关联的事实来源；
- 所有 Retrieval、Evidence、Artifact、HumanInput 和下载路径限制在当前 owner/Project；
- API、Worker 重启和至少一次重复投递不能产生第二份业务副作用；
- Fake 模式不得因遗漏 Adapter 而静默访问网络；Real 模式必须显式启用；
- Metrics 采集失败不能破坏业务流程，也不能包含高基数或敏感标签；
- 日志和 Metrics 不替代业务 Event，不把模型不可预测输出当作可信诊断字段；
- 评测失败不能通过降低断言、删除失败样例或隐藏“证据不足”绕过；
- Phase 4 不为 Phase 5/6 提前建设 Agent Runtime、Tool、Browser、MCP 或 Sandbox 抽象。

## 10. 实现切片

1. **阶段契约收敛**：更新 Phase Spec、总体指南、ADR 和后续阶段进入条件；
2. **离线 Demo Fixture 与 Fake arXiv**：保证 `--fake` 真正离线且可重复完成 Review；
3. **Review 前端基础旅程**：List、Create、Detail、Stage、Sources 和取消；
4. **Review HITL 与结果展示**：结构化 Outline 表单、Matrix、Sections、Citation 和 Artifact；
5. **结构化日志与 Correlation ID**：统一 API、Worker、Run 和 Provider 日志上下文；
6. **基础 Metrics**：`/metrics`、低基数指标、失败隔离和测试；
7. **可靠性矩阵审计与补缺**：复用已有测试，只补跨模块空白；
8. **固定评测与性能基线**：确定性质量门、显式真实报告和本机测量；
9. **Playwright 与发布收尾**：Phase 1–4 核心旅程、README、模块笔记、限制和完成复盘。

每个切片遵循：契约/失败测试 → 最小实现 → 集成测试 → 文档更新 → 独立 Git 提交。

## 11. 测试方式

- Backend：pytest 单元/Application/API、PostgreSQL/Testcontainers、Worker/Queue 和故障注入；
- Web：Vitest、TypeScript build 和 Playwright；
- Fake：默认离线、零费用、确定性；
- Real：显式 Docling、Embedding、Chat、arXiv 和端到端 Smoke/评测；
- 可观测性：日志脱敏/上下文传播、Metrics 名称/Label/失败隔离测试；
- 性能：独立脚本或显式 marker，不进入普通功能测试的稳定时间断言。

## 12. 阶段完成条件

- 全新开发环境按 README 安装依赖后，可用 `./scripts/dev.sh --fake` 离线启动并完成导入、RAG、Review、
  Outline HITL、引用跳转和 Artifact 下载；
- Review 核心旅程可从 UI 完成，刷新、SSE 重连、取消、等待和失败状态可恢复；
- `--real` 可显式完成受限真实 Smoke，报告不伪装成普通测试或稳定质量承诺；
- 可靠性矩阵每一项有现有或新增自动测试证据，跨用户/Project 越权路径被拒绝；
- JSON 日志可以用 Correlation ID 关联一次请求与后台 Run，`/metrics` 只暴露低基数安全指标；
- 固定评测结果和本机性能基线来自实际运行，环境、阈值、失败样例和限制可解释；
- Phase 1–4 Spec、ADR、模块笔记、README、Playwright 和发布复盘与实现一致；
- 禁用所有 Research Agent 能力时，Demo-ready Core v1 独立可用；
- 文档明确不包含公网生产、认证、备份恢复、永久删除/GC、OpenTelemetry 和 SLA。

## 13. 已确定事项与后续参数

架构和产品范围已经确定，无需在切片 2 前再次选择交付形态、Review 表单、Fake arXiv 或可观测性栈。
以下参数必须由对应切片的测试或真实基线得出，不属于当前待决架构问题：

- Metrics 最终名称、Histogram Bucket 和少量允许 Label；
- Retrieval/Review 的实际基线值和回归阈值；
- Fixture 的具体论文、问题和真实 Provider 运行预算；
- 性能测量的数据规模与是否有证据需要 pgvector ANN 索引。
