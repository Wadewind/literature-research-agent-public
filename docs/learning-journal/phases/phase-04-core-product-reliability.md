# Phase 4：Demo-ready Core 产品闭环、可靠性与评测

## 状态

- 当前状态：切片 1–9 已完成，Phase 4 已完成
- 发布后维护：Real Review 的 `json_object` Schema 提示缺失已修复，尚待用户显式 Real 重建验证
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

### 切片进度

- 切片 1「阶段契约收敛」：已完成（`cd565b6`）；
- 切片 2「离线 Demo Fixture 与 Fake arXiv」：已完成实现与验证，生产 Worker 通过
  `AGENT_ARXIV_BACKEND=fake|httpx` 显式装配，默认 fail closed 到 `fake`；`dev.sh --fake` 同时固定
  Fake Parser/Embedding/Chat/arXiv，并以 `unicode-word.v1` 完成无需外部词表的 Chunk/RAG token 计数；
  `--real` 才选择 Docling、真实 Provider、HTTP arXiv 与原有 `cl100k_base` tokenizer；
- `review-demo.v1` 包含 4 条完全合成的版本化来源：3 条成功下载、1 条稳定永久失败。生产 Fake
  Adapter 在启动时按 manifest 固定的 size/SHA-256 校验成功 PDF，篡改、缺失或非法文件契约立即失败；
  与 `ArxivProjectImportService` 的应用测试验证重复导入收敛为 3 个 Ingestion Run 和 1 个稳定失败
  Source。Fake Matrix 的证据不足、Outline feedback 再次 interrupt、approve 后继续以及 Review 重放
  单效果由分层确定性测试共同覆盖，不声称由某一个测试完成完整 Review E2E；
- 切片 2 实际验证：离线定向 Backend（含 Fixture 完整性、无 tiktoken 缓存的 Fake Indexing/RAG、
  Fixture 导入、feedback interrupt 和 Review 重放）`113 passed`，其中显式空 `TIKTOKEN_CACHE_DIR` 的
  Fake Chunk/Indexing/RAG/Worker `45 passed`；完整非集成 `613 passed, 4 skipped`；
  arXiv 导入与 Queue Worker PostgreSQL/Testcontainers 集成 `7 passed`；`ruff check src tests`、`pyright`、
  `bash -n scripts/dev.sh` 和 `git diff --check` 通过。`uv build` 生成的 wheel/sdist 均包含 manifest/PDF，
  wheel 安装到独立 `/tmp` 目录后可读取 4 条来源和首篇 121-byte PDF。普通测试没有读取 `.env`、访问
  arXiv、下载 tiktoken 资源或调用付费 Provider。
- 切片 3「Review 前端基础旅程」：已完成。新增 Project-scoped Review List API，以单次
  `runs JOIN review_runs` 查询同时限制 owner、Project 与 `RunType.REVIEW`，按 `created_at DESC,
  run_id DESC` 稳定排序；列表只返回生命周期、研究问题、当前 Stage 和时间，不暴露配置快照、
  Checkpoint 或内部大载荷。跨 owner/Project 查询返回空列表。
- Web 以 Project 工作区导航统一 Library、Chat 与 Reviews；Review 创建失败重试复用同一
  `Idempotency-Key`，成功后才清除交互意图，归档 Project 明确只读。Detail 从 API 恢复 Run、
  `current_stage`、Step 和 Source，以真实固定 `review.v1` 顺序呈现 Stage rail；等待依赖、等待输入、
  失败、取消和终态都有明确状态。SSE 只使 Review detail/list/sources Query 失效，不承载业务结果；
  Review List 仅在存在非终态 Run 时每 5 秒低成本刷新，空列表或全终态列表不轮询；页面刷新仍完全
  读取 PostgreSQL 支撑的 REST API。具名 SSE 清单已与当前 Review 生产者逐项核对，包含搜索完成、
  Outline 提议、等待人工输入和 Section 草稿完成等现有事件。Outline、Matrix、Section、Citation 和
  Artifact 结果留给切片 4，不展示假数据。
- 切片 3 实际验证：Web Vitest 全量 `100 passed`，`tsc -b && vite build` 通过；Backend 非集成全量
  `615 passed, 4 skipped`，PostgreSQL/Valkey integration 全量 `113 passed`；`ruff check`、`pyright`
  与 `git diff --check` 通过。普通测试没有访问实时 arXiv 或付费 Provider。本切片未启动 dev server，
  因此没有声称浏览器视觉/console 验证；完整 Review Playwright 旅程仍属于切片 9。
- 切片 4「Review HITL 与结果展示」：已完成。Review Detail 从真实 Project-scoped API 恢复 Outline、
  Evidence Matrix、最新 Section、Claim/Evidence Citation 与六类 Artifact。Outline 使用标题、目标与
  分析维度的结构化表单，并支持 section key、添加、删除与排序；客户端先按 `outline.v1` 的 1–12 节、
  snake_case、文本、唯一 key 和 1–6 个可见维度边界确定性校验。approve/edit/feedback 均提交
  Request/Outline 版本与
  `Idempotency-Key`；同一失败意图复用 Key，Request 或版本变化生成新意图，过期提交由后端拒绝。
  有未保存编辑时不能误批准服务端旧版本；只有 Run 确实为 `WAITING_INPUT` 且开放 Request 与 Outline
  匹配时显示操作。409 冲突立即刷新 Detail/Outline/Matrix，同时保留相同提交的 Key 供真正重试。
- 新增最小 `GET /projects/{project_id}/reviews/{run_id}/sections` 读契约。Repository 同时限制 owner、
  Project、RunType 与 `SECTION` 类型，每个 `output_key` 只返回最高版本并稳定排序；页面再按批准
  Outline 顺序展示。Evidence 点击后复用现有 Project-scoped Evidence API 读取 Version/页码并跳转
  PDF content endpoint；Artifact 下载只使用后端校验过的 content endpoint，不接触 Storage Key。
  SSE 只使 Outline/Matrix/Section/Artifact Query 失效，刷新仍以 REST/PostgreSQL 事实恢复。
- 切片 4 实际验证：Web Vitest 全量 `118 passed`，`tsc -b && vite build` 通过；Backend 非集成全量
  `618 passed, 4 skipped`，PostgreSQL/Valkey integration 全量 `114 passed`；`ruff check src tests`
  与 `pyright` 通过。普通测试没有访问实时 arXiv 或付费 Provider。本切片未启动 dev server，未声称
  浏览器视觉/console 验证；完整 Review Playwright 旅程仍属于切片 9。
- 切片 5「结构化日志与 Correlation ID」：已完成。新增标准库 JSON Formatter 与 contextvars 上下文，
  每行固定包含 UTC `timestamp/level/event/service/correlation_id`，其余字段使用显式 allowlist；任意 extra、
  旧自由文本 message、异常正文和 traceback 不会序列化。API 接受 1–128 个安全字符的
  `X-Correlation-ID`，缺失或非法时生成 UUID，响应（含安全 500）回显并在请求结束 reset。Run
  create/cancel、上传、Conversation message、Review create/cancel/outline-input 使用同一请求 ID 写入
  既有 Event/应用调用，不修改 HTTP body schema 或事务。
- Worker 仍只接收 `run_id`，以 job_id/run_id 摘要建立自己的有界 correlation；Run 认领并创建 Attempt
  后，执行上下文来自 PostgreSQL 的 Project/Run type/Attempt 事实。Run outcome、Worker loops、Outbox、
  Event notification、ModelGateway 和 Retrieval 摘要已迁移为固定结构化事件；Model 日志只含
  capability/provider/model/status/duration/error type，不含 Prompt、结果或 Provider 错误正文。日志不替代
  Event/Attempt/ModelInvocation，也未引入 Trace、日志平台或 Metrics。
- Phase 6 完成后的本地开发维护补充：新增统一 `AGENT_LOG_LEVEL`，默认 `INFO`，严格接受
  `DEBUG/INFO/WARNING/ERROR/CRITICAL` 并同步 API、Worker 与 Uvicorn 阈值；`scripts/dev.sh` 关闭与
  `CorrelationMiddleware` 重复的 Uvicorn access log，保留项目自身安全请求事件。未引入文件 Handler、
  集中日志平台或按模块等级。
- 切片 5 实际验证：最终定向 API/Application/Worker/日志测试 `103 passed`；Backend 完整非集成
  （安全修复前一轮，生产路径随后仅收紧日志序列化）`637 passed, 4 skipped`；相关 PostgreSQL/Valkey Run、Outbox、Worker、Event notifier、
  ModelInvocation 与 Retrieval 集成 `41 passed`；`ruff check src tests`、`pyright` 通过。普通测试未访问
  实时 arXiv 或付费 Provider。
- 切片 6「基础 Metrics」：已完成。仅新增 `prometheus-client`，API `/metrics` 与 Worker
  `127.0.0.1:8001/metrics` 使用各自进程内 Registry；Worker port 可用
  `AGENT_WORKER_METRICS_PORT=0` 关闭。指标覆盖 Run/Attempt、Outbox、Model、Retrieval、Review Stage
  与 Worker active jobs；所有 Label 使用固定低基数枚举，非法输入归一化为 `unknown`，不含任何业务 ID、
  correlation、Provider/Model 或用户内容。Metrics 更新和 Worker endpoint 启停失败均与业务隔离。
- Counter/Histogram 是进程内执行尝试观测：重启归零，至少一次重放可增加计数，不替代 PostgreSQL
  Run/Event/Attempt/Outbox/ModelInvocation。API endpoint 不声称包含 Worker 指标；两个 endpoint 无认证，
  只适用于可信本地开发。切片 6 实际验证：定向 API/Application/Workflow/Worker/Metrics `91 passed`；
  Backend 完整非集成 `655 passed, 4 skipped`；PostgreSQL/Valkey integration 完整 `114 passed`；
  `ruff check src tests`、`pyright`、`bash -n scripts/dev.sh` 与 `git diff --check` 通过。普通测试未读取
  `.env`、访问实时 arXiv 或付费 Provider。
- 切片 7「可靠性矩阵审计与补缺」：已完成。Phase 4 §7 实际包含十类故障，逐行复用既有
  Domain/Application/API/Workflow/PostgreSQL/Valkey 证据，并只补四个跨模块空白：两个不同物理
  ARQ Job 仍只携带同一 `run_id` 时，真实 Worker 只提交一个 Attempt、一组 Event 与解析事实；Storage
  成功后真实 PostgreSQL commit 失败时，Output/Artifact/Step/Event 全回滚且 Stage/Run sequence 不变，
  稳定缓存可由重放复用；取消先持 Run 行锁时导出只能拒绝且无部分业务效果；损坏 Checkpoint 在
  Review Executor 中保持永久错误，既不 `start` 也不 `resume`，不会覆盖为新图。
- Provider 响应与本地业务 Output/ModelInvocation 提交之间仍存在不可消除的崩溃窗口：重放可能再次
  调用 Provider，第一次远端调用也可能来不及留下 Invocation。矩阵明确只承诺 PostgreSQL 业务事实
  Effectively Once，不宣称外部调用或分布式 Exactly Once。API 重启采用无状态 API 契约、PostgreSQL
  Repository 与跨连接 Runtime 的组合证据，不夸大为双 Uvicorn 黑盒 E2E。完整证据、测试层级和未证明
  内容见 [可靠性测试矩阵](../modules/reliability-test-matrix.md)。新增定向 Application `1 passed`，新增
  PostgreSQL/Valkey 集成 `3 passed`；Backend 完整非集成 `656 passed, 4 skipped`，完整
  PostgreSQL/Valkey integration `117 passed`；`ruff check src tests`、`pyright` 与 `git diff --check`
  通过。普通测试未读取 `.env`、访问实时 arXiv 或付费 Provider。
- 切片 8「固定评测与性能基线」：已完成。复用 Phase 2 的 4 篇合成 PDF、14 题和正式
  Ingestion/Indexing/Retriever/RAG/Citation runner，并增加 3 个固定 Review 研究问题的版本化组合工程门。
  三个问题及 3/4/3 篇语料实际进入生产 Matrix/Citation/Section Validator 和确定性导出器；首次实跑
  固定五项 100% 阻断阈值：场景 3/3、Citation 接受/跨 Run 拒绝 6/6、导出引用映射 6/6、Evidence
  跨 Project/Run 拒绝 18/18 和伪造 Evidence 拒绝 3/3。Owner 隔离由 Project-scoped Application/PG
  组合回归证明，不折算成领域质量比例。
  feedback/HITL、持久化、Run/Step/Event 终态和重放是独立固定 12 节点组合回归，实跑 `12/12`，不
  折算为质量比例；100% 不代表语义 Groundedness。Phase 2
  再跑仍为 answered Retrieval 8/8、Citation 11/11、Validator 14/14、scope 3/3，Fake insufficient
  继续 0/6 并作为已知失败保留。
- 本机基线为 WSL2/4 vCPU/Python 3.13.14/PostgreSQL 18.6/Valkey 9.1.1，冷库 4 篇、16 Elements、
  8 Chunks、Fake Embedding 1024 维：解析索引总计 0.575 s；Retrieval p95 14.927 ms；RAG p95
  154.562 ms；TestClient 存活端点 p95 1.032 ms；3–4 篇 Review 生产 Domain Validator/导出三场景
  总计 0.970 ms（不含 PG/Worker/HITL/Storage）；真实 PG+Valkey+ARQ 非 Review Worker 路径
  `2 passed / 12.77 s`、测试进程峰值 152,688 KiB。正式 API+PG+Valkey/ARQ Worker+Runtime 的 4 Source
  完整 Review wall 5.372 s、active 4.371 s、自动 HITL pause 1.000 s，Worker RSS/VmHWM 133,220 KiB；
  3 ready + 1 failed，13 Steps 全成功、22 Events、两轮 HITL、6 个 Artifact/8,646 bytes 均可读。
  结果是一轮观察值，不是 SLA；8 Chunk 规模没有证据需要 ANN。
  本切片无显式 Provider 凭证，未读 `.env` 或发网；报告只引用 Phase 2 历史最小 Smoke，并明确本次
  未重跑及未来 opt-in 必须记录的字段。完整结果、失败样例和复现命令见
  [固定评测报告](../reports/phase-04-evaluation-baseline.md)、
  [性能基线](../reports/phase-04-performance-baseline.md)与
  [真实 Provider 记录](../reports/phase-04-real-provider-evaluation.md)。
  首次完整 Fake Review 性能旅程还暴露稳定 ARQ Job ID 的恢复缺口：首次 PAUSED Job 默认保留 Result，
  Dependency/HITL 恢复的同 ID 重投被 ARQ 去重，Run 停在 `queued` 且没有第二 Attempt。Worker 现仅对
  `execute_run` 使用 `func(..., keep_result=0)`；执行中重复仍按稳定 ID 去重，结束后同一 Run 可合法
  创建新 Attempt，业务单效果继续由 PostgreSQL 条件认领保证。真实 PG+Valkey 修复回归 `3 passed`；
  重启 Worker 后第四次全新 Project 完整旅程成功，且外部 HTTP/付费 Provider 调用为零。
  切片 8 实跑：评测指标单元 `6 passed`，Review 固定组合回归 `12 passed`；性能/评测/Worker/Application
  最终定向 `44 passed`；ARQ Result 修复的真实 PostgreSQL+Valkey 集成 `3 passed`。修复前已跑 Backend
  完整非集成 `660 passed, 4 skipped` 和完整 PostgreSQL/Valkey integration `117 passed`；修复后全量
  非集成重跑在本地测试进程无输出挂起后中止，不报告伪造结果。最终 `ruff check src tests`、`pyright`
  与 `git diff --check` 通过。
- 2026-08-24 Real 体验继续暴露失败路径的同类缺口：ARQ `max tries exceeded` 等提前失败使用 Worker
  默认 `keep_result=3600`，不会读取 `execute_run` 的函数级 `keep_result=0`；同时 Queue Adapter 忽略
  `enqueue_job()` 的 `None`，使旧失败 Result 阻塞合法重投时 Outbox 被假标 `dispatched`。现增加 Worker
  级 `keep_result=0`，并把 Queue 正常返回收紧为“新 Job 已创建或同 ID 活跃 Job 已确认”；旧 complete
  Result 只按精确 key 清理后有界重投，无法确认则抛错并保留 Outbox `pending`。真实业务中已错误标记
  `dispatched` 的存量 Run 不会由代码变更自动修复，仍需另行受控恢复；Real 新旅程验证尚未执行。
  本次定向单元/Application `22 passed`，完整 Queue/Worker PostgreSQL + Valkey/ARQ 集成 `9 passed`；
  `ruff check src tests`、`pyright` 与 `git diff --check` 通过。
- 切片 9「Playwright 与发布收尾」：已完成。隔离 harness 显式选择 Fake Parser/Embedding/Chat/arXiv、
  清除 Provider Key、使用临时 PostgreSQL/Valkey/Storage 且不读取 `.env`；浏览器阻断所有非 localhost
  请求。新增成功 Review 与取消两条旅程：成功旅程实际观察 3 ready + 1 stable failed Source，并从持久
  Event 验证 `dependency_wait_started`/`dependency_wait_completed`、
  首轮 Outline、feedback 后 `outline.v2`/Request v2、approve、最终 succeeded/finalize、Matrix 的证据不足、
  Section/Claim/Citation、Evidence → Project-scoped PDF 页码、六类 Artifact 下载/JSON 或 Markdown 可读，
  并在首轮 HITL、第二轮 HITL、终态和取消终态刷新验证 REST/PostgreSQL 恢复；错误 Project 的 PDF 与
  Artifact content 均为 404。SSE 只促使 Query 重载，不作为事实来源。两条旅程均无 `pageerror`、关键
  console error 或外部请求。仅当当前 Project/Run 的 Outline/Matrix GET 已实际返回 404 且
  `ConsoleMessage.location().url` 精确匹配时，才消费对应通用日志；另一条主动触发的本机 404 必须进入
  错误列表，因此不会隐藏其他 404、5xx 或脚本错误。
- 首次 Phase 4 实跑为 `2 failed`：下载端点按安全通用文件名返回 `content.md/json`，测试误期待内部业务
  文件名；等待结果的 404 被浏览器打印为通用 console error。修正测试契约后 Phase 4 `2 passed`。
  首次 Phase 1–4 全套又暴露 Phase 2 旧导航选择器，3 passed/1 failed；更新为当前 Project 工作区
  `文献库` 导航后 Phase 2 单跑 `1 passed`，最终全套 `4 passed (37.5s)`。Web Vitest `118 passed`、
  TypeScript/Vite build 均通过；E2E harness/开发脚本/Fake arXiv `11 passed`、Review Application
  `3 passed`、Review API `6 passed`。`ruff check src tests`、`pyright`、`bash -n web/e2e/run.sh` 与
  `git diff --check` 也均通过。完整范围、失败样例和未承诺内容见
  [Phase 4 发布复盘](../reports/phase-04-release-retrospective.md)。

## 11. 测试方式

### 发布后 Real 模式修复

首次 `./scripts/dev.sh --real` Review 在制定检索策略时暴露了结构化输出兼容缺陷：配置
`AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false` 时，OpenAI-compatible Adapter 虽发送
`response_format={"type":"json_object"}`，却丢弃了调用方传入的 `search-strategy.v1` JSON Schema。
因此 Provider 调用本身成功，业务 Step 仍以 `search_strategy_schema_invalid` 永久失败。

修复保持 `json_object` response format，并把同一完整 Schema 以确定性 system instruction 注入请求；
调用方消息对象和相对顺序不变，严格 `json_schema` 路径及 `json_schema=None` 自由文本路径请求形状不变。
没有加入宽松 JSON repair、额外重试、Validator 放宽或依赖。普通测试通过 HTTP mock 验证，不读取 `.env`、
不访问 arXiv 或真实 Provider；原失败 Run 是终态，新的 Real Review 由用户自行重建验证。

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
