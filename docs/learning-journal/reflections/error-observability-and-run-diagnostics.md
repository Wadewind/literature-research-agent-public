# 从一次 Real Review 失败反思错误可观测性与 Run 诊断

- 记录日期：2026-08-29
- 状态：Proposed / Deferred
- 触发事件：Real Review 在 `formulate_search_strategy` 阶段发生结构化输出校验失败
- 相关缺陷：[`P4-REAL-004`](../reports/phase-04-real-mode-defect-log.md#p4-real-004search-strategy-输出疑似触及-token-上限后-schema-校验失败)
- 当前决定：Search Strategy 输出预算已作最小缓解；通用 Failure 契约和 Run
  Diagnostic 方案继续延期，不纳入已经完成的 Phase 6 范围

## 为什么记录这次过程

这次问题表面上是一次 `SearchStrategyValidationError`，但实际排查暴露了更普遍的工程问题：系统能够可靠
保存 Run、Attempt、Step、Event 和 ModelInvocation，却仍需要开发者手工关联日志、跨表查询数据库并阅读
多层代码，才能接近根因。

因此，这份记录不把“提高某一步的 token 上限”当作全部结论，而是分别保留：

1. 具体 Real Provider 缺陷及其证据；
2. 当前错误处理与保存方式的评价；
3. 一套可复用的故障排查方法；
4. 尚未批准实施的统一错误契约和 Run Diagnostic 演进方案。

它属于对当前实现的反思，不是 ADR、Phase Spec 或已交付功能声明。未来决定开发时，应再从本文抽取正式
Spec，明确兼容性、API 和验收门槛。

## 触发事件与排查链

### 用户可见现象

Real Review 的模型请求日志显示 `model_request_completed`，紧接着 Worker 以
`SearchStrategyValidationError` 结束整个 Run。单看这两条日志，无法判断是 Provider 输出截断、JSON
格式错误、字段不符合业务 Schema，还是本地 arXiv query/维度规则拒绝。

### 实际排查顺序

```text
用户现象
  → 从日志取得 run_id / attempt_id / correlation_id
  → 查询 Run、Attempt、Step、Event、ModelInvocation、ReviewOutput
  → 确认精确失败阶段和持久化错误消息
  → 追踪 Search Strategy Service 与 Domain Validator
  → 对比请求 max_tokens 和实际 completion_tokens
  → 检查 Adapter 是否保留 finish_reason
  → 区分已确认事实、高概率推断和未知项
```

### 已确认事实

- Provider 请求成功，不是 HTTP、认证、限流或超时失败；
- `formulate_search_strategy` Step 失败，Attempt 保存
  `SearchStrategyValidationError / search_strategy_schema_invalid`；
- 模型调用记录为 `prompt_tokens=410`、`completion_tokens=2000`；
- `ReviewSearchStrategyService` 对这一失败请求当时固定传入 `max_tokens=2000`；
- 研究问题只有 18 个字符，不是超长输入；
- 错误发生在 Pydantic JSON Schema 解析阶段，尚未进入维度数量、维度字段或 arXiv query 业务校验；
- 本次没有生成 Search Strategy ReviewOutput，也没有执行受限 repair；
- OpenAI-compatible Adapter 没有读取或保存 Provider `finish_reason`，ModelInvocation 也不保存原始响应。

### 高概率推断

`completion_tokens` 恰好等于请求上限，并随后出现 JSON Schema 解析失败，因此当前最高概率解释是输出触及
上限后被截断。由于没有 `finish_reason` 或原始响应，这仍不能写成已经确认的 Provider 根因。

### 2026-08-29 最小预算缓解

- Search Strategy 的固定输出预算从 2000 提高到 8000，与 `review-default.v2` 的章节输出
  预算对齐；
- 保留 64 KiB 结构化输出字节上限、严格 Pydantic 校验和“校验失败不 repair”行为；
- 本次只是让 Real 测试先继续运行的低风险缓解，没有补齐 `finish_reason`、统一 Failure
  契约或 Run Diagnostic；
- 定向 Application 测试已锁定 `max_tokens=8000`，但尚未执行新的 Real Provider 回归，因此不改变
  上述根因推断边界。

### 尚未确认

- Provider 是否明确返回了 `finish_reason=length`；
- 返回内容是语法不完整，还是合法 JSON 但缺字段、字段类型错误或存在额外字段；
- Provider 的推理 token 是否占用了该接口报告的 completion token 预算；
- 提高上限、切换模型行为或增加一次 repair，哪一种在质量、费用和稳定性之间更合适。

### 2026-08-29 专项诊断落地

后续 Real Review `9b828e44-36ab-44b1-a6f1-5b862acf2d57` 在章节预算已提高到 8000 后，第三章节仍于
4905 completion tokens 后结构校验失败。这推翻了“提高上限即可解决”的隐含假设，但旧记录仍无法定位
具体 Validator 分支。

本次因此只落地模型结构化输出专项诊断，不提前实施下文的平台级 `FailureRecord`：

- ModelInvocation 保存请求上限、allowlist 化 finish reason、响应 UTF-8 字节数和 SHA-256；
- 章节 Step 使用截断、Schema、身份、字段、状态/Claim 和 Evidence 绑定稳定 `error_code`；Attempt 与
  `run_failed` Event 仍由既有异常信封记录类型，并在安全 message 中携带该分类；
- 不保存 Prompt、模型正文、论文正文或 Provider 原始负载；不增加自动 repair/retry；
- 历史记录不会被反向推断；新字段只能改善后续失败的诊断证据。

## 当前错误处理与保存方式

### 已经形成的统一部分

Run 执行顶层通过 `RunExecutionService` 捕获异常，并交给 `apply_run_failure` 统一决定：

```text
永久/临时分类
  → Attempt 预算
  → Run = RETRY_WAIT 或 FAILED
  → run_retry_scheduled 或 run_failed Event
  → 必要时重置 Outbox
```

Run、Attempt、Event 的终态和重试行为因此相对统一。结构化日志也使用 correlation ID、固定事件名和字段
白名单，并刻意避免保存 Secret、完整 Prompt、论文全文和原始模型响应。

### 尚未统一的部分

同一个错误会以不同粒度保存在多个位置：

| 位置 | 当前表达 | 问题 |
|---|---|---|
| `run_attempts.error` | 任意 `{type, message}` JSON | 没有共享类型或 Schema version |
| `events.payload.error` | 通常复制 Attempt error | 依赖调用方保持一致 |
| `run_steps.error_code` | 单个稳定字符串 | 可能比原异常更粗，缺少来源和可重试语义 |
| `model_invocations.error_type` | Provider 调用异常类型 | 只说明模型边界是否失败，不说明后续业务校验 |
| Runtime/Tool 表 | 各自的 code、kind 或摘要 | 子系统内较强，但尚未收敛为平台级错误契约 |
| HTTP API | Route 或模块局部翻译 | 机器码、中文描述和 `str(exc)` 并存 |

本次错误尤其清楚地展示了信息退化：

```text
Domain Exception message: search_strategy_schema_invalid
RunStep.error_code:        search_strategy_invalid
RunAttempt.error.type:     SearchStrategyValidationError
Worker log:                exception_type=SearchStrategyValidationError
```

数据没有丢失到完全不可查，但错误语义没有成为可直接聚合的统一事实。

## 对当前可观测性的评价

当前系统更准确的描述是：

> 业务审计和可靠执行事实较强，故障诊断与用户可行动错误较弱。

优点包括稳定 ID、持久 Run/Attempt/Step/Event、模型调用计量、结构化日志和安全脱敏；困难在于信息分散、
错误码不完全一致、缺少跨层聚合视图，以及部分关键边界元数据没有进入安全诊断记录。

必须区分两类改进：

- `finish_reason`、请求上限、响应字节数等属于模型调用及结构化输出的专项诊断；
- 统一 Failure 契约、错误码和 Run Diagnostic 属于 Provider、Storage、Queue、Workflow、Tool、Sandbox、
  权限和取消等错误都能复用的平台能力。

## 可复用的故障排查方法

### 1. 从用户现象和稳定 ID 开始

先确认用户实际看到的失败，再取得 `correlation_id`、`run_id`、`attempt_id`、Step 和外部 Invocation ID。
不要只根据最后一行异常日志猜测根因。

### 2. 还原跨服务时间线

日志适合回答请求是否到达、外部调用是否完成、耗时多久以及异常在哪个进程抛出。必须显式区分：

```text
外部调用成功
  ≠ 本地校验成功
  ≠ Step 成功
  ≠ 业务事务提交成功
  ≠ 整个 Run 成功
```

### 3. 查询业务事实来源

在本项目中，PostgreSQL 而不是 ARQ/Valkey 或进程日志是 Run、Attempt、Event 和 Artifact 的事实来源。
需要核对终态、重试、部分输出、副作用和事件顺序，不能只看一条实时日志。

### 4. 从稳定错误码反查代码条件

应优先追踪精确错误消息或业务码的产生位置，再检查其捕获、降级、重试和持久化路径。只搜索异常类名可能
把多个不同失败原因混在一起。

### 5. 对比边界参数与实际数据

常见根因位于 timeout、token、大小、预算、Schema、Profile 和版本边界。需要把“代码传入的上限”和
“Provider/数据库记录的实际值”放在一起分析。

### 6. 分开陈述事实与推断

诊断报告至少区分：

- 已确认事实：日志、数据库或代码直接证明；
- 高概率推断：多项证据一致，但缺少最后一项直接证据；
- 尚未排除：仍可能成立，需要额外观测或受控复现。

## 延期的候选演进方案

### 目标

不是预先枚举系统可能产生的全部错误，而是先建立通用错误信封，让后续模块错误逐步进入同一条链路。

候选结构示例：

```json
{
  "schema_version": "failure.v1",
  "code": "structured_output.schema_invalid",
  "category": "validation",
  "source": "model_output",
  "stage": "formulate_search_strategy",
  "retryable": false,
  "safe_message": "模型返回的检索策略结构无效",
  "details": {
    "reason": "json_schema_invalid"
  }
}
```

`details` 必须是按错误类型定义的低敏 allowlist，不能成为保存完整 Exception、Prompt、网页正文或模型
响应的逃生口。

### 渐进迁移顺序

1. 新增带版本的 `FailureRecord`、分类器和旧 `{type, message}` 兼容读取，不改变 API；
2. 先接入集中的 Run/Attempt/Event 失败路径，`RunStep` 暂时继续保存同一 `failure.code`；
3. 依次迁移 Review 结构化输出、Provider、Ingestion/Indexing、Runtime、Tool/MCP/Sandbox 和 Storage；
4. 增加只读 `RunDiagnosticView`，聚合 Run、Attempt、Step、Event、ModelInvocation、ToolExecution 和
   Artifact 状态；
5. 最后单独版本化 HTTP 错误响应和 UI 技术详情，避免把对外契约变化混入内部迁移；
6. 未映射异常统一收敛为安全的 `internal.unexpected`，通过指标观察并逐步补齐分类。

现有 `run_attempts.error` 和 `events.payload` 是 JSONB，第一轮可以保持数据库兼容；是否需要新增
`failure_id`、独立表或索引，应由真实查询需求决定，不能在 Reflection 中提前固定。

### 如何降低遗漏风险

- 将 Application 入口参数从松散 `dict` 收紧为 `FailureRecord`，让类型检查暴露未迁移调用点；
- 建立唯一 Exception → FailureRecord 分类入口，不允许新代码自行拼 `{type, message}`；
- 用契约测试保证 Attempt、Event、Step 和 Diagnostic View 的 `code` 一致；
- 维护版本化 Error Catalog，登记默认 category、retryable 和 public message；
- 通过静态搜索或 Semgrep 禁止新增松散错误字典和直接向用户返回 `str(exc)`；
- 统计 `internal.unexpected`，把实际运行中遗漏的分类变成可发现、可逐步收敛的问题；
- 保留旧 payload 兼容测试，确保历史 Run 仍可查询。

## 工具与角色边界

Sentry 可以聚合异常和堆栈；OpenTelemetry 加 Jaeger/Tempo 可以展示跨 API、Worker、数据库和外部调用的
Trace；Prometheus/Grafana 适合趋势和告警；Loki/ELK 适合集中日志；LangSmith/Langfuse 可辅助分析模型
与 Tool 调用。这些工具都不能替代应用主动定义的业务错误语义和安全边界。

真实团队通常由开发者定义错误码、重试语义和埋点，平台/SRE 维护采集、查询和告警，产品前端展示用户可
行动错误，运维维护 Runbook 和事故响应。个人项目由同一开发者承担这些角色，但不需要为了证明方法论而
立即引入整套重型观测基础设施。

## 当前不实施的原因与重新启动条件

Phase 6 已按本地单人精简范围完成。现在一次性统一所有历史异常、API 和 UI 会形成横切改造，并可能引入
对外契约、重试行为和数据兼容变化。因此当前只记录方案。

满足以下任一条件时，可以重新讨论正式 Spec：

- 再次出现需要手工跨表和阅读多层代码才能定位的高价值故障；
- 准备长期运行 Real Provider 评测或公开演示；
- 需要面向非开发者提供自助重试和技术详情；
- `internal.unexpected`、结构化输出失败或 Sandbox/Tool 故障开始形成稳定频率；
- 准备引入集中错误平台或 OpenTelemetry，需要先统一业务语义。

正式启动时建议新建 `docs/spec/failure-contract-and-run-diagnostics.md`，再决定字段、错误目录、历史兼容、
API 版本、UI、数据迁移和验收测试。本文继续保留为问题来源、排查证据和设计动机。

## 相关入口

- `backend/src/literature_agent/application/run_execution_service.py`
- `backend/src/literature_agent/application/failure_policy.py`
- `backend/src/literature_agent/domain/run_attempt.py`
- `backend/src/literature_agent/domain/review.py`
- `backend/src/literature_agent/observability.py`
- `backend/src/literature_agent/application/review_search_strategy_service.py`
- `backend/src/literature_agent/domain/review_search_strategy.py`
- `backend/src/literature_agent/infrastructure/models/openai_compatible.py`
- [`结构化日志与 Correlation`](../modules/structured-logging-and-correlation.md)
- [`Run/Event`](../modules/run-event.md)

## 60 秒面试说明

“一次 Real Review 中，模型 HTTP 调用成功，但 Search Strategy 校验失败。我没有把 Provider 成功误认为
业务成功，而是用 run/attempt/correlation ID 还原日志时间线，再查询 PostgreSQL 的 Run、Attempt、Step、
Event 和 ModelInvocation，最后沿代码定位 Pydantic 校验边界。completion tokens 恰好命中 2000 上限，
所以截断是高概率解释；但 Adapter 没保存 finish reason，因此我明确把它保留为推断。这个过程让我看到
系统的业务审计已经可靠，但错误语义分散、诊断仍依赖手工拼接。我因此设计了兼容旧 JSONB 的版本化
FailureRecord、统一分类入口和 Run Diagnostic 聚合方案，并在阶段完成后将它记录为 deferred proposal，
没有为了一个 Bug 仓促重写整条错误链。”
