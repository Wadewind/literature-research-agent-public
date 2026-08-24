# Phase 4 Real 模式体验缺陷台账

- 建立日期：2026-08-24
- 适用范围：Phase 4 关闭后，通过 `./scripts/dev.sh --real` 运行真实 Provider 旅程时发现的缺陷
- 维护方式：后续体验中发现的新缺陷按 `P4-REAL-NNN` 递增追加；修复后保留原记录并补充提交与验证证据

## 记录规则

- 将已确认事实、证据链和推断分开记录；证据不足时明确写“尚未确认”，不把高概率推断写成根因。
- 状态使用“调查中”“待修复”“已修复”“已验证”；“已修复”只表示代码已合入，“已验证”还需要对应
  Real 旅程或显式 Provider 回归证据。
- 记录能够帮助定位的 Project、Review、Run、Step、稳定错误码、token 计数和低敏校验类别。
- 不保存 API Key、Cookie、完整 Prompt、论文全文、模型完整响应或其他真实用户敏感数据。
- 候选修复如改变产品行为、成本、重试语义或架构边界，必须先完成决策，不能把候选项当作既定方案。
- 普通回归测试继续使用 Fake/HTTP Mock，不访问实时 arXiv 或真实付费 Provider；真实验证必须显式启用。

## P4-REAL-001：`json_object` fallback 丢失业务 Schema

- 发现日期：2026-08-24
- 状态：已修复；已有后续旅程证据，尚无独立 Provider 回归测试
- 影响 Run/Project：首次 Real Review 的完整 ID 未在本记录保留
- 影响范围：首次 Phase 4 Real Review 的 `formulate_search_strategy` Step
- 稳定错误码：`search_strategy_schema_invalid`
- 关联提交：`42193ea399889711d60650da090078634a9c84b2`（`fix: 保留 json_object 的结构化契约`）

### 现象

Provider 请求成功，但返回内容没有通过本地 `search-strategy.v1` 严格校验，Review 在制定检索策略阶段
终止。后续新建的 Review `57439d97-115b-4191-8c01-1fab4eaab98e` 已通过
`formulate_search_strategy`，这只能作为同一 Real 旅程中的后续证据，不等同于隔离、可重复的
Provider 回归测试。

### 已确认原因与证据链

当 `AGENT_CHAT_JSON_SCHEMA_SUPPORTED=false` 时，OpenAI-compatible Chat Adapter 只向 Provider
发送 `{"type": "json_object"}`，没有继续传递调用方提供的完整业务 JSON Schema。原 Prompt 也没有完整
约束 `normalized_question`、`arxiv_query` 以及 `dimensions[]` 内的嵌套字段，而本地 Pydantic 契约使用
`extra="forbid"`，因此“Provider 调用成功”不能保证返回值符合业务 Schema。

### 尚未确认项

- 不同真实 Provider/Model 对同一 Schema instruction 的遵循率和失败分布尚未形成固定评测。
- 当前后续成功只覆盖一次完整旅程中的该 Step，未形成显式、隔离的真实 Provider 回归用例。

### 已采用修复

在 `json_object` fallback 分支把完整、确定性序列化的 JSON Schema 注入 system instruction；保留本地
严格 Pydantic 校验，不放宽字段、不跳过 Validator，也不增加 repair/retry。严格 JSON Schema Provider
和自由文本请求行为保持不变。

### 所需回归测试

- Adapter 单元测试验证 fallback instruction 包含完整嵌套 Schema，且序列化顺序确定。
- 验证严格 JSON Schema 分支与自由文本分支不受影响。
- 普通测试使用 RESPX/Fake，显式 Provider 回归另行 opt-in，并记录 Provider、Model 与 Prompt/Profile
  版本，不记录完整请求或响应。

## P4-REAL-002：旧失败 ingestion Run 的 ARQ 重投递被误判为成功

- 发现日期：2026-08-24
- 状态：待修复
- 影响 Project：`a8a53cf8-32d6-48f2-b5f5-d915220394d0`
- 影响 Review：`0e76f1a9-07ce-4708-9137-61f99f89db09`
- 关联提交：无

### 现象

Review 检索到 10 篇论文，其中 5 篇进入 `ready`，另 5 篇长期停在 `importing`，Review 因而停在
`waiting_dependency`。这 5 篇复用了同一 Project 中此前创建的 ingestion Run；旧 attempt 因
`worker_crashed`/Worker lease 过期而失败。系统把业务 Run 恢复为 `queued` 并生成或重投 Outbox 后，
没有产生 attempt 2。

### 已确认原因与证据链

1. 新 Review 有新的 Review Run ID，但 PaperVersion 按 `owner_id + content_hash` 去重；命中已有版本时会
   复用其 `version.ingestion_run_id`。因此新的 Review 不会为同一内容自动创建新的 ingestion Run ID。
2. ingestion 的物理 ARQ Job ID 是稳定的 `run:<ingestion_run_id>`。旧失败 Job 的
   `arq:result:run:<run_id>` 仍保留在 Valkey。
3. ARQ `enqueue_job` 在 Job key 或 Result key 已存在时返回 `None`，没有真正把新 Job 放入队列。
4. `ArqRunQueue.enqueue_run()` 忽略该返回值，`OutboxDispatchService` 因未收到异常而把 Outbox 误标为
   `dispatched`。
5. 最终 PostgreSQL 显示 Run 为 `queued`、Outbox 为 `dispatched`，Valkey 队列却没有对应 Job，Review
   永久等待依赖。

此前针对成功/暂停 Job 设置 `keep_result=0` 的修复，不能证明失败 Job 的 Result key 不会保留；两类
问题不能混为同一个已修复缺陷。

### 尚未确认项

- 物理 Job ID 的最终代次方案，以及 ARQ 对不同失败/重试路径的 Result TTL，需要在实现前用集成测试
  固化。
- 已卡住 Run 的受控恢复入口尚未确定，不能依赖手工批量删除 `arq:*` key。

### 候选修复（待决策）

- Queue Adapter 将 `enqueue_job()` 返回 `None` 视为“未接受投递”，Outbox 只有在确认 Job 已入队或
  存在可证明的活跃执行时才能标记 `dispatched`。
- 为每次投递代次生成不同的物理 Job ID，同时继续以 PostgreSQL 的条件领取、Run 状态和 attempt 约束
  保证业务上的 Effectively Once；需要明确模糊响应后的重复投递语义。
- 仅在数据库锁、业务状态和 lease 都确认没有活跃执行后，受控清理指定旧 Result key，再重新投递；该
  方案风险更高，不能扩大为通配删除。
- 为已经卡住的 Run 提供可审计、幂等的受控重投入口。

### 所需回归测试

- 使用真实 PostgreSQL + Valkey/ARQ 的集成测试构造 attempt 1 失败且 Result key 保留的场景。
- 对账恢复后必须实际入队、产生 attempt 2 并收敛，不能只断言 Outbox 状态。
- 覆盖重复分发、活跃 Job 已存在、Result key 残留、Worker lease 竞争和取消竞争。
- 断言 PostgreSQL 始终是业务事实来源，修复不依赖 Valkey 保存业务状态。

## P4-REAL-003：章节输出触及 token 上限后结构校验失败

- 发现日期：2026-08-24
- 状态：调查中（尚待修复）
- 影响 Project：`a8a53cf8-32d6-48f2-b5f5-d915220394d0`
- 影响 Review：`57439d97-115b-4191-8c01-1fab4eaab98e`
- 影响 Step：`draft_sections` 的第二章节 `solution_frameworks`
- 稳定错误码：`section_draft_invalid`
- 关联提交：无

### 现象

第一章节 `problems_and_challenges` 已持久化；第二章节请求的 prompt token 为 9995，completion token
为 4000，恰好达到 `section_output_token_limit=4000`。Provider HTTP 调用成功，随后本地抛出
`section_draft_invalid`，Review 终止。

### 已确认事实与证据链

- 第二章节调用已从 Provider 正常返回，不是 HTTP 或鉴权失败。
- 返回内容未通过 `parse_section_draft_json` 或 `validate_section_draft` 这一范围内的本地结构/业务校验。
- 当前没有持久化模型原始响应、Provider `finish_reason` 或足以区分失败分支的结构化校验类别，因此不能
  事后断言是 JSON 缺少结束括号。

### 高概率推断（尚未确认）

completion 恰好达到 4000 token，且第二章节 Prompt 明显大于第一章节，因此“输出被 token 上限截断，
形成不完整 JSON”是当前最高概率解释；它仍是推断，不是已经确认的根因。合法 JSON 但字段、Evidence
引用或长度约束失败也仍有可能。

### 候选修复（待决策）

- 只持久化 allowlist 内的 `finish_reason` 和结构化校验类别，不保存完整模型响应、Prompt 或论文正文。
- 检测 `finish_reason=length` 或明确的输出上限信号，并映射为稳定错误码，例如
  `section_output_truncated`，避免与其他 Schema/Evidence 错误混合。
- 对截断或可修复结构错误最多执行一次受限 repair/retry；这会改变成本和既有“立即失败”行为，需要先
  确认重试边界与幂等语义。
- 缩减章节上下文与期望输出，收紧 Claim/术语数量；或在评估 Provider 能力、成本与总预算后提高输出
  上限。
- 重新决定部分章节成功、后续章节失败时是终止整个 Review，还是保留部分结果并允许恢复；这是产品
  行为变化，需要单独确认。

### 所需回归测试

- 覆盖达到上限的截断 JSON，并与合法 JSON 但 Evidence/业务校验失败区分稳定错误码。
- 若引入 repair，断言最多执行一次，失败后稳定终止，不形成无限重试或重复费用。
- 恢复执行时不得重写已成功持久化的第一章节。
- 普通自动测试只用 Fake/HTTP Mock，不访问真实 Provider；显式 Real 回归另行记录 token、阶段与结果。
