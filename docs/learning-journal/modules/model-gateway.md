# Model Gateway 模块（Embedding/Chat Port + OpenAI-compatible Adapter + 调用记录）

Phase 2 切片 3 完成后成文；切片 5 起被 IndexingExecutor 经 Worker 接线实际使用。

## 解决的问题

RAG 需要调用外部 Embedding/Chat 模型，但模型调用不可预测：会限流、超时、返回畸形响应，且按 token 计费。本模块给业务层一对窄 Port，把 Provider 差异、错误分类、短重试和用量记录收敛在一处，保证「模型调用不发生在数据库事务内」和「日志不记录 Prompt」两条边界可执行。

## 边界与执行流程

```text
调用方（IndexingExecutor / Retriever / 后续 RagAnswerService）
  → ModelGateway（application/model_gateway.py）
      ├─ EmbeddingModel.embed(texts) / ChatModel.generate(messages, json_schema?)
      │    └─ Adapter：OpenAiCompatibleEmbedding / OpenAiCompatibleChat
      │         （httpx2 AsyncClient；429/5xx/超时最多 2 次短重试，退避 1s/2s）
      └─ 调用后独立短事务写 model_invocations
           （capability/provider/model/status/usage/latency_ms/error_type，含 run_id 接线位；
            记录失败只记日志，不影响调用结果）
```

- Port 只表达意图：`json_schema` 由 Adapter 映射为 OpenAI `response_format`（`json_schema` 优先，`json_schema_supported=False` 时降级 `json_object`）；Worker 由 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED` 显式配置能力（默认 true）；JSON 解析与业务 Schema 校验在上层（RAG 切片）；
- 空批量 `embed([])` 直接返回空结果，不发起请求；
- Port 暴露 `provider`/`model` 属性供调用记录使用。

## 状态、数据模型和事务

- `model_invocations`：`invocation_id`、`run_id`（可空 FK → runs）、`capability`（embedding/chat）、`provider`、`model`、`status`（succeeded/failed）、`prompt_tokens`/`completion_tokens`（可空）、`latency_ms`、`error_type`（可空）、`created_at`。**不存 Prompt 与响应内容**。
- 调用记录是独立短事务：模型调用本身不持有任何数据库事务，记录失败不反向影响业务调用结果。
- `ModelGateway` 不接进 lifespan 的 API 侧；Worker 装配时按 `AGENT_EMBEDDING_BACKEND` 选择 Fake 或真实 Adapter（切片 5）。Phase 4 起 Fake 模型栈同时固定 `unicode-word.v1` 离线 tokenizer，Indexing 与 RAG 共享该 profile；真实模型栈仍使用 `cl100k_base`。

## 关键决定与替代方案

- **窄 Port + 手写 OpenAI-compatible Adapter（httpx2）**：不引入 openai/LangChain SDK——依赖轻、mock 层薄（pytest-httpx2/RESPX 直接拦 HTTP），且智谱/DeepSeek 这类「OpenAI 兼容但细节有偏差」的端点用裸 HTTP 更可控；代价是要自己维护请求/响应形状解析。
- **错误六分两类**（`domain/model_errors.py`，接入既有 `is_permanent_error`）：临时——`ModelRateLimitError`（429）、`ModelServerError`（5xx、网络连接失败）、`ModelTimeoutError`；永久——`ModelAuthError`（401/403/缺 Key）、`ModelInvalidRequestError`（其余 4xx）、`ModelResponseError`（响应 JSON 畸形或缺字段）。Adapter 只对临时错误做最多 2 次短重试，耗尽交 Run 层预算重试，不叠加多层重试。
- **响应畸形属永久错误**：Adapter 层不做结构修复重试；「让模型修复自己的输出」是业务层策略（切片 8 的修复重试一次），不混进传输层。
- **缺 API Key 不在启动时崩溃**：本地开发默认 Fake backend；真实模式下首次调用抛 `ModelAuthError`，与「开发绕过必须显式」的安全基线一致。
- **Provider 默认值可替换**：智谱 `embedding-3`（`https://open.bigmodel.cn/api/paas/v4`，维度默认 1024 可选 256/512/2048）与 DeepSeek `deepseek-v4-flash`（`https://api.deepseek.com`）只是 Settings 默认值，base_url/key/model 全部环境变量可配（2026-08-20 与用户定稿）。

## 失败、重试、重复和取消行为

- 429/5xx/超时：Adapter 内最多 `AGENT_MODEL_MAX_RETRIES`（默认 2）次短重试（固定退避 1s/2s），耗尽后抛临时错误，由 Run 层按预算 RETRY_WAIT；
- 401/403/400/响应畸形：不重试，直接永久错误；
- 每次最终尝试（无论成败）产生一条 invocation 记录，重试中间过程不记录（记录在 Gateway 层，包住整个 Adapter 调用）；
- Provider 响应与本地持久化不是分布式原子事务：响应成功后、业务 Output 或 `ModelInvocation` 提交前
  崩溃仍可能导致重放再次调用；第一次远端调用也可能来不及留下 Invocation。多条记录可解释重复尝试，
  单条记录不能证明 Provider 只执行一次，系统不宣称外部调用 Exactly Once；
- 模型调用本身不可中断：取消由执行器在调用前后检查点处理（见 chunk-and-indexing 笔记）。

## 安全和可观测性

- API Key 只来自服务端 Settings（`AGENT_EMBEDDING_API_KEY`/`AGENT_CHAT_API_KEY`），不进入日志、Event、invocation 记录；
- 日志不记录完整 Prompt 与响应体；invocation 只记 profile/usage/延迟/错误类型；
- Phase 4 切片 5 起，Gateway 额外输出 `model_request_completed/failed` JSON 事件，只允许
  operation/provider/model/status/duration/run_id/error type；messages、texts、JSON Schema、模型结果和
  Provider 异常正文均不进入日志。日志记录失败不改变原有 ModelInvocation 独立短事务与异常传播语义。
- Phase 4 切片 6 增加 `agent_model_request_total{operation,status}` 和
  `agent_model_duration_seconds{operation}`。operation 只允许 embedding/chat/unknown；Provider、Model、
  run_id、Prompt、token 与费用都不是 Label，采集失败不影响模型结果或异常传播。
- 真实 Provider 测试默认跳过，`AGENT_RUN_PROVIDER_TESTS=1` 显式启用（仿 `AGENT_RUN_DOCLING_TESTS`）。

## 重要测试和运行结果

- RESPX 契约 `tests/infrastructure/test_openai_compatible_models.py`（16 例）：成功形状与请求体、usage 解析、空批量不发请求、429 重试后成功、429/5xx/超时耗尽、401/400 永久不重试、JSON 畸形与缺字段、json_schema/json_object response_format；
- Gateway `tests/application/test_model_gateway.py`（5 例）：成功/失败记录、error_type、记录失败不影响结果、不掩盖模型错误、run_id 可空；
- PostgreSQL 集成 `tests/integration/test_model_invocation_repository.py`（3 例）；
- 切片 3 完成时：非集成 216 passed + 4 skipped，integration 43 passed，ruff/pyright 全绿。
- 切片 10 真实 Smoke（2026-08-21）：`embedding-3` 返回 1 个 1024 维向量且 usage 非空；真实 Chat 在 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false` 的 `json_object` 模式返回符合 `RagAnswerOutput` 的 `insufficient_evidence`，usage 非空。普通入口仍为 2 skipped，不触网。初次实跑还验证了 401 正确映射认证错误、完整端点误作 Base URL 会形成 404，以及不支持 `json_schema` 时 400 被归为永久非法请求；修正配置后通过。

## 代码入口

- 领域：`domain/model_errors.py`、`domain/model_types.py`、`domain/model_invocation.py`、`domain/retry_policy.py`（分类注册）
- 端口：`application/ports/embedding_model.py`、`chat_model.py`、`model_invocation_repository.py`
- 服务：`application/model_gateway.py`
- 适配器：`infrastructure/models/openai_compatible.py`、`fake_models.py`（生产侧 Fake，切片 5 起 fake 为 bag-of-words 向量）
- 配置：`infrastructure/config.py`（`AGENT_EMBEDDING_*`/`AGENT_CHAT_*`/`AGENT_CHAT_JSON_SCHEMA_SUPPORTED`/`AGENT_MODEL_TIMEOUT_SECONDS`/`AGENT_MODEL_MAX_RETRIES`/`AGENT_EMBEDDING_BACKEND`）
- 迁移：`migrations/versions/d6e1f7a3b9c2_create_model_invocations_table.py`
- 测试：`tests/infrastructure/test_openai_compatible_models.py`、`test_provider_smoke.py`（显式启用）

## 已知限制

- Adapter 走 `trust_env=True` 以支持本机代理访问真实 Provider；代理为 SOCKS 时需要 `socksio`，否则构造客户端抛 ImportError（smoke 测试 docstring 已说明）；
- 网络连接失败（非超时）归类为 `ModelServerError`（临时），是三类具名临时错误之外的归类选择；
- 重试是固定退避，不读 `Retry-After`；无并发限制与配额管理（个人项目规模）；
- `model_invocations` 尚无查询 API，只作运维记录。
- `json_object` 降级不能由 Provider 强制 Schema，依赖 Prompt 明确字段形状；Pydantic 解析、一次修复和 Citation Validator 仍是不可省略的确定性边界。

## 60 秒面试说明

"Model Gateway 把外部模型的不确定性收敛到一对窄 Port 后面。传输层是手写的 OpenAI-compatible Adapter（httpx2，不引 SDK），好处是智谱、DeepSeek 这类兼容端点的偏差我能完全控制，测试用 RESPX 在 HTTP 层 mock。错误分六类两档：429/5xx/超时是临时错误，Adapter 最多两次短重试后交给 Run 层的预算重试，绝不叠加多层重试；认证失败、非法请求、响应畸形是永久错误，直接失败——响应结构修复是业务层策略，不属于传输层。每次调用经 Gateway 落一条 model_invocations 记录（usage、延迟、错误分类），但 Prompt 和响应体永不入库、不入日志。本地开发默认 Fake backend，真实 Provider 测试必须显式开启。"
