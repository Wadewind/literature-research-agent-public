# Agent Runtime 业务契约与 Fake Runtime

## 模块解决的问题

Phase 5 先建立 Research Agent 的平台业务边界，再接 Deep Agents。该模块定义绑定 Project 的持续
`AgentSession`、一条用户消息触发的 `AgentTurnRun`、不可变上下文/策略快照、opaque Runtime Binding，
以及一个不泄漏 SDK 类型的最小 `ResearchAgentRuntime` Port。

切片 1 同时提供完全离线的 `FakeResearchAgentRuntime`，用于在数据库、API 和 Worker 接入前验证稳定
Session/Turn 映射、重复执行、取消、恢复、对账和结果重复收集语义。

## 边界和执行流程

```text
AgentSession（owner + Project）
  ├─ 有序 AgentMessage
  ├─ 同时最多一个 active_turn_run_id
  └─ RuntimeSessionBinding（binding_id + generation + opaque Thread/Workspace ID）

AgentTurnRun（一条用户消息）
  ├─ ContextSnapshot（授权数据版本引用）
  ├─ PolicySnapshot（能力、审批与预算）
  └─ RuntimeTurnBinding（session_binding_id + opaque Execution/Checkpoint ID）
```

`ResearchAgentRuntime` 只有五个操作：

1. `execute_turn`：返回项目 `RuntimeEvent` 异步迭代器；
2. `resume_turn`：沿用同一 Execution/Checkpoint 恢复；
3. `cancel_turn`：阻止后续 Runtime 操作；
4. `reconcile_turn`：返回 Runtime 自身状态与 Binding；
5. `collect_turn_result`：成功后重复读取相同结果。

没有单独的 `stream_turn`：执行和恢复本身就是异步增量流。也没有提前加入通用 `close`、Thread、Store、
Workspace 或 Deep Agents 配置操作。Runtime 成功和结果可收集不代表业务 Message、Evidence、Artifact 或
Run 已经提交；该事务闭环属于后续切片。

该 Port 传入的 `RuntimeTurnRequest` 只包含本轮新用户消息和不可变 Context/Policy Snapshot，不携带完整
产品历史。真实 Deep Agents Adapter 应在同一 Session Binding 上复用同一 SDK Thread，只追加新消息，
并让 `create_deep_agent` 原生 Message、Checkpoint、上下文压缩和文件卸载维护模型工作上下文。

## 状态、数据模型和不变量

- `AgentSession` 创建后 owner/Project 不可变；冻结 dataclass 防止原地修改；
- `AgentSession` 字段为 `session_id`、`owner_id`、`project_id`、`title`、`status`、
  `active_turn_run_id`、`created_at`、`last_activity_at`；
- `AgentMessage` 字段为 `message_id`、`session_id`、`sequence`、`role`、`content`、`turn_run_id`、
  `idempotency_key`、`created_at`、可空 `claim_set_id`；后者只在平台已验证引用的 Agent 回答上指向通用
  ClaimSet；
- `AgentTurnRun` 字段为 `turn_run_id`、`session_id`、`user_message_id`、`context_snapshot_id`、
  `policy_snapshot_id`；
- `claim_active_turn` 只允许一个活动 Turn，同一 `turn_run_id` 重复认领幂等；
- `create_agent_message` 根据调用方从持久化 Session 锁定后取得的 `last_sequence` 唯一生成下一序号；
  切片 2 已使用 Session 行锁分配 sequence，并以数据库唯一约束兜底；
- `AgentMessage.idempotency_key` 是消息提交的稳定幂等事实；相同提交不能生成第二条消息或第二个 Turn；
  切片 2 已实现对应事务、条件更新和唯一约束；
- `AgentTurnRun` 只保存通用 Run 与 Session、用户 Message、两个 Snapshot 的稳定关联；通用 Run 新增
  `run_type=agent_turn`，未修改状态机；
- `ContextSnapshot` 固化 owner/Project/Session/Turn、用户消息、历史边界、Paper/PaperVersion/ChunkSet、
  可选 `review_output_id`、Artifact ID/内容哈希；不保存论文正文、Chunk、Matrix payload 或 SDK State；
- 其中 `history_through_sequence` 是产品消息历史的审计、对账与受控重建水位，不是每轮 Prompt 重放
  指令；PostgreSQL `AgentMessage` 是产品事实，Deep Agents Message/摘要/Checkpoint 是 Runtime 工作状态；
- Evidence Matrix 绑定具体 `ReviewOutput.output_id`。后续 Context Builder 必须校验
  `output_type=evidence_matrix`、`output_key=evidence-matrix` 和 owner/Project/Review Run 所有权闭包；
- `PolicySnapshot` 固化 allowlist、网络/Sandbox 开关、审批要求和模型/工具调用预算；默认能力关闭；
- Snapshot 内容使用规范 JSON 计算 SHA-256，Snapshot ID 和创建时间不参与哈希；所有集合使用 tuple，
  避免冻结对象中嵌入可变容器；
- `RuntimeSessionBinding` 保存稳定 `binding_id`、正整数 `generation` 及 opaque Thread/Workspace ID；
  `RuntimeTurnBinding.session_binding_id` 让每个 Turn 明确引用具体一代 Session Binding；
- Runtime 重置/升级可创建新 `binding_id` 并递增 generation，旧映射留作审计；Fake 当前只验证固定
  generation 1；
- Binding 只使用项目字符串 DTO 保存 opaque ID，不导入或公开 Deep Agents/LangGraph 类型；
- 上述均为领域/Port 字段；切片 2 已完成专用表、数据库映射、索引与约束。

## Fake Runtime 语义

- Session ID 通过本地 SHA-256 确定性映射一个 Thread ID 和 Workspace ID；
- Session ID 同时确定性映射一个 `binding_id`，Fake 固定 `generation=1`；
- Turn Run ID 确定性映射一个 Execution ID 和 Checkpoint ID；
- 同 Session 的多个 Turn 复用同一 Binding，且各自通过 `session_binding_id` 显式引用它；
- 同一请求重复 `execute_turn` 重放相同事件和结果，不增加 Binding 或逻辑 Execution；
- 同一 `turn_run_id` 携带不同输入会得到 permanent `runtime_turn_conflict`；
- 可由测试构造参数让固定 Turn 在 `interrupted` 停止；`resume_turn` 沿用原 Binding，重复相同恢复输入
  幂等，不同恢复输入冲突；
- 取消后正在消费的流不再产生新事件，也不能收集结果；已经成功的终态取消是幂等 no-op；
- `reconcile_turn` 只报告 Fake 的 Runtime 状态、已实际发出的最后事件序号和结果可用性，不改变业务 Run；
- 错误只携带 `temporary/permanent/cancelled`、稳定 code 和安全消息。

Fake 不调用模型、网络、MCP、Browser 或 Sandbox，不读取环境密钥，也不依赖 `deepagents`。它证明的是
平台 Port 语义，不是 Deep Agents、数据库事务、崩溃恢复或外部副作用安全已经通过验证。

切片 4 已在不修改该 Port 的前提下新增真实 `DeepAgentsResearchAgentRuntime`；其 SDK 行为与测试证据见
`deep-agents-runtime-adapter.md`。成功 Turn 不再依赖进程内记录：Adapter 通过 PostgreSQL Checkpoint
metadata 反查稳定 Turn/Session/请求哈希，并从真实 checkpoint 重建结果与 opaque Binding。
切片 6 已在不增加第六个 Port 方法的前提下加入 SDK-neutral `RuntimeExecution` 控制记录；第二 OS 进程
可以认领 orphan、用 `resume_turn(response=None)` 沿同一 checkpoint 恢复，失败/取消/成功终态也可跨
Adapter 对账。详见 [`agent-runtime-execution-recovery.md`](agent-runtime-execution-recovery.md)。

## 关键决定与替代方案

- 采用 `AgentSession : Runtime Thread = 1:1`、`AgentTurnRun : Runtime Execution = 1:1`；没有复用 RAG
  Conversation 生命周期；
- Port 使用项目 DTO 和异步迭代器，避免为 SDK 的 Thread/Checkpoint/Event 类型建立领域镜像；
- 保留 PostgreSQL 产品消息并不意味着平台管理模型上下文；真实 Adapter 不得每轮重放完整历史，也不得
  用 `create_agent` 加自研中间件复制 Deep Agents Harness；
- `review_output_id` 是 Matrix 的主要绑定，不把 `review_run_id` 当作 Snapshot 输入；Review Run 由平台
  沿 ReviewOutput 所有权闭包反查校验；
- 没有为切片 2 预建 Repository、数据库模型或事务接口，也没有为后续 MCP/Browser/Sandbox/Skill 增加
  专用 Port 方法。

## 失败、重试、重复和取消

`RuntimeErrorKind.TEMPORARY` 表示外层可按业务 Attempt 预算重试；`PERMANENT` 表示相同输入重复无效；
`CANCELLED` 表示取消条件已成立。是否创建新 Attempt、何时从 `retry_wait` 恢复以及如何提交业务终态仍由
平台 Run/Application Service 决定，Runtime 不能自行修改 PostgreSQL 事实。

Fake 的重复执行只验证内存中的逻辑去重和稳定 ID。切片 2 已验证正常成功路径中独立 Runtime 调用与
业务结果事务、稳定 Binding 和 staged candidate。切片 3 将协调顺序固定为 reconcile-first：只有稳定
`turn_run_id` 在 Runtime 中明确不存在时才 execute；已有 RUNNING Execution 不再次追加输入，已有
SUCCEEDED/result_available 直接 collect。`temporary` 进入既有 Attempt/Outbox 重试预算，`permanent`
直接失败；错误 Event/Attempt 只保存稳定 code 与安全描述。

业务提交前还必须验证 Runtime 返回闭包：reconciliation `turn_run_id`、Session Binding `session_id`、
Turn Binding 的 `session_id`/`turn_run_id`/`session_binding_id` 必须匹配本轮 request，result
`turn_run_id` 必须匹配业务 Run。任一错配统一为安全 permanent `runtime_scope_mismatch`，不保存 Runtime
原始输出、错误 Binding、Assistant Message 或 candidate。

Runtime 成功后、业务结果 commit 前失败会回滚 Message/candidate/Event，并由新 Attempt 对账同一
Execution；业务提交后 ACK 丢失则由 PostgreSQL 终态认领拒绝重复 Job。这里承诺的是业务事实的
Effectively Once，不是 Runtime/Provider Exactly Once。

RUNNING 取消由 `AgentTurnExecutor` 并行观察 PostgreSQL `CANCEL_REQUESTED`，在事务外调用
`cancel_turn`、停止消费 Runtime 流，再以 Run 行锁原子提交 `run_cancelled`、`agent_turn_cancelled` 与
Session 活动指针释放。Runtime 取消传播失败时保留停止心跳的 RUNNING Attempt，由 lease Reconciler
收敛，不能把取消误作临时失败重试。QUEUED 取消和 FAILED/CANCELLED 终态通过幂等终态回调释放 Session；
RETRY_WAIT 继续持有同一活动 Turn。
Runtime stream consumer 与取消状态 watcher 由统一 `finally` 管理；状态观察异常、`cancel_turn` 异常或
外层 Worker task 取消都会 cancel+await 两个子任务，原始异常继续交给既有失败/lease 策略，不允许后台
consumer 在业务已重试或取消后继续模型/Tool 语义操作。

## 安全和可观测性

`RuntimeEvent` 是待筛选的短暂增量，不是持久 `RunEvent`。当前 DTO 只允许稳定 ID、顺序、白名单 kind、
文本增量或安全摘要；后续 Adapter/Application 仍必须禁止持久化思考过程、完整 Prompt、网页/论文正文、
Secret 和大型 Tool 输出。

所有 owner/Project/Evidence/Artifact 授权必须在平台 Context Builder 中完成。Fake 仍只接收已构造
Snapshot；切片 5 的 `ProjectResearchContext` 已对两个固定只读工具实现每次调用的 Run/Turn/Session/
Context/Policy 闭包复核，但这不把 Fake 测试冒充为授权证据。

## 重要测试和运行结果

2026-08-25 实际运行：

- 定向 `pytest`：18 passed；覆盖 Session/Message/Turn、单活动 Turn、消息 sequence、Snapshot 不可变与
  内容哈希、`review_output_id`、Port 方法集合与 SDK 类型隔离、Fake 稳定映射、重复执行、取消、恢复、
  Binding generation/Turn 引用、对账、结果收集和错误分类；
- 定向 `ruff check`：通过；
- 定向 `pyright`（新增生产代码）：0 errors、0 warnings、0 informations。

切片 3 实际运行（2026-08-25）：

- 主审补强红灯：`8 failed, 6 passed`；修正后 Agent Application `14 passed`、Agent PostgreSQL 故障
  注入 `3 passed`；
- 临时移除统一清理块的受控 mutation：两条清理测试 `2 failed`；恢复后 `2 passed`；
- Agent/Run/Fake 扩大定向分层回归：`66 passed in 60.98s`；
- 后端非集成全量回归：`722 passed, 4 skipped in 62.16s`；
- 覆盖 reconcile-first、既有 RUNNING 不 execute、成功响应丢失、真实结果 commit 失败、ACK 重放、
  temporary/permanent 分类、QUEUED/RUNNING 取消、取消时无 Message/candidate、Attempt lease 崩溃收敛、
  取消传播 temporary 失败、子任务无泄漏、Runtime 作用域错配、SUCCEEDED/FAILED/CANCELLED 释放及
  RETRY_WAIT 保持活动 Turn；
- 后端 `ruff check src tests` 通过，全量 `pyright` 为
  `0 errors, 0 warnings, 0 informations`。
- 主智能体独立复验高风险 Application/PostgreSQL/Run/Fake 组合 `62 passed in 53.97s`、API/Worker 装配
  回归 `27 passed in 48.87s`；独立 Ruff 与 Pyright 同样通过。

## 代码入口

- `backend/src/literature_agent/domain/research_agent.py`
- `backend/src/literature_agent/application/ports/research_agent_runtime.py`
- `backend/src/literature_agent/application/agent_turn_executor.py`
- `backend/src/literature_agent/application/project_research_context_service.py`
- `backend/src/literature_agent/application/agent_turn_lifecycle_service.py`
- `backend/src/literature_agent/infrastructure/agent/fake_research_agent_runtime.py`
- `backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- `backend/tests/domain/test_research_agent.py`
- `backend/tests/application/test_research_agent_runtime_contract.py`
- `backend/tests/infrastructure/test_fake_research_agent_runtime.py`
- `backend/tests/application/test_agent_turn_executor.py`
- `backend/tests/integration/test_agent_turn_reliability.py`

## 切片 2 落地补充

- Port 仍保持五方法且未泄漏 SDK 类型；Worker 生产装配暂时注册完全离线的
  `FakeResearchAgentRuntime`，明确不是 Deep Agents 生产 Adapter；
- Fake 成功结果确定性包含一个 Markdown candidate descriptor；相同 Turn 重复结果保持相同 ID、哈希、
  大小、MIME 与 `content_ref`；
- `agent_artifact_candidates` 只保存 staged 元数据，与 Review `artifacts` 完全分离；
- Runtime candidate 先经领域边界校验；相同 Turn/hash 只有稳定 scope/元数据完全一致才可收敛，跨
  Turn/owner 的 candidate ID 碰撞按并发修改拒绝，不能借 Runtime ID 绕过平台作用域；
- Session Binding 重放按请求的 `(session_id, generation)` 精确收敛，不读取“当前最新”代替历史事实；
- Runtime execute/reconcile/collect 全部在数据库事务外，成功结果通过新的短事务提交；提交前重新锁定并
  检查业务 Run 仍为 RUNNING，Runtime 成功不直接等同业务成功；
- 每个普通 Turn 显式绑定且校验具体 Evidence Matrix `ReviewOutput.output_id`，并固化当前 READY
  ChunkSet 引用；Fake 尚不读取其正文。

## 切片 5 落地补充

- 五方法 `ResearchAgentRuntime` Port 未扩大；新增的 `ProjectResearchContext` 是由 Deep Adapter 内部
  Tool 调用的 SDK-neutral Application Port，Tool schema 不暴露业务 scope ID；
- `ContextSnapshot.project_index_refs` 的 `(paper_id, paper_version_id, chunk_set_id)` 被精确下推到两路
  Retrieval SQL，避免同一 Version 的另一个 READY ChunkSet 漂入恢复后的 Turn；
- Platform `ToolExecution` 记录稳定 effect/hash/status/attempt 与有界安全结果，执行 Event 不包含 query、
  Chunk/Matrix 正文或完整参数/输出；
- Project Tool 实际暴露的 Evidence 被复制为当前 AgentTurn Run 的快照，最终回答再通过正文标记、Runtime
  DTO 和既有 Citation Validator 三重闭包校验；Assistant Message 可空绑定通用 ClaimSet；
- 引用结果与 Runtime Binding、候选 Artifact、安全 Event、Run 终态在同一短事务提交；旧生产 Fake 没有
  Project Tool/Evidence 时继续写 `claim_set_id=NULL`，不把任意正文误标为“证据不足”。

## 切片 6 落地补充

- 五方法 Port 仍未扩大；`RuntimeResumeRequest.turn_request` 只携带平台重新加载并复核的原 Context/Policy，
  `response=None` 表示崩溃恢复，不引入 SDK Command 类型；
- `RuntimeTurnReconciliation.resume_available` 只在业务 Run/最新 Attempt 可运行且 Runtime lease 已
  orphan 时为真；Application reconcile-first 后才选择 resume，不再次调用 execute；
- RunAttempt 继续表达业务投递，RuntimeExecution 单独表达 Turn 级唯一 Execution、checkpoint、版本、
  lease/fence 和 Runtime 终态；两者不能合并；
- Runtime 调用、模型/Tool 和 checkpoint 仍在数据库事务外；Runtime SUCCEEDED 后由 Application 另开短
  事务提交产品 Message/Event，响应丢失可重复 collect 而不重复业务提交；
- 生产 Worker 固定 Fake 不变，没有环境变量可启用真实 Deep 模式；真实 Adapter 只通过显式 DI 测试。
  Provider/`BaseChatModel` factory、Secret/费用与 Worker runtime 配置属于切片 7.0；
- 无新 Provider、MCP、Browser、Sandbox、Skill 或依赖。

## 切片 7.2 落地补充

- 五方法 `ResearchAgentRuntime` Port 继续不变；MCP Catalog/Profile/Policy 引用均为 SDK-neutral 数据，
  LangChain/MCP SDK 类型、endpoint、transport 与 Secret 只允许存在于 infrastructure 私有连接解析边界；
- `PolicySnapshot.mcp_refs` 冻结 Catalog ID、精确版本、配置哈希、prefixed Tool 名和输入 Schema 哈希，
  Profile 更新不会改变已创建 Turn，也不会把安全参数原文复制到 Policy/Event；
- execute/resume 在同一个 Runtime operation 内持有显式 MCP ClientSession，外部 MCP 调用位于
  ToolExecution begin/succeed 两个短事务之间；collect/reconcile/cancel 的离线路径不连接 MCP；
- Runtime 成功、MCP 成功、ToolExecution 成功与业务 Turn 成功仍是四个不同事实；稳定 effect、唯一约束、
  条件更新和 response replay 提供 Effectively Once 防线，但外部副作用与账本提交之间不宣称 Exactly Once；
- 生产 Catalog 当前为空且 Resolver fail-closed；7.2 的进程内 FastMCP 只验证 Adapter 契约，真实
  Playwright/Search MCP 与 Native Skills 仍属于 7.3/7.4。

## 已知限制

- Fake 状态仍不跨进程，不能据此宣称恢复；真实 Deep Adapter 已由切片 6 用 PostgreSQL Checkpoint、
  RuntimeExecution 和两个真实 OS 进程验证执行途中 Worker 崩溃接管；
- 两个固定 Project Tool 已可读取精确 ChunkSet 和指定 Evidence Matrix；正式 Artifact 仍只有授权稳定引用；
- 只有 Artifact candidate staged，没有文件 Storage、正式 Artifact commit 或下载；
- 生产 Fake 不返回 Evidence；显式启用 Project Tool 的 Deep/Application 测试已持久化 Agent Evidence 与
  Claim/Citation，但生产 Worker 尚未切换；
- Worker 生产默认尚未切到 Deep Agents；真实 Adapter 已在 ARQ Worker 进程拓扑下完成跨进程恢复 Spike，
  并可组装两个真实 Project Tool、OpenSandbox/WorkspaceSnapshot 与 MCP 配置基础；生产 MCP Catalog 仍为空，
  Browser、真实 Playwright/Search MCP 与 Skill 尚未接入；
- Fake 状态仍位于进程内；真实恢复结论只适用于显式配置持久 RuntimeExecution + PostgreSQL Checkpointer
  的 Deep Adapter，不反向改变 Fake 语义；
- 已验证平台能停止消费 Fake 流、事务外传播取消并拒绝结果；尚未验证真实 Deep Agents、模型/Tool 或
  远端 Provider 对已在途调用的立即中止能力。
- 未确认的外部 Provider/Tool 调用仍可能重试，Project Tool orphan RUNNING 当前拒绝自动重放；这不是
  Exactly Once，后续外部能力必须分别设计幂等或补偿。

## 60 秒面试说明

我先没有直接把 Deep Agents SDK 放进 API 或领域层，而是定义了一个五方法的
`ResearchAgentRuntime` Port。持续 Session 和逐消息 Turn Run 是 PostgreSQL 将拥有的业务事实，SDK
Thread、Execution、Checkpoint 和 Workspace 只通过 opaque Binding 出现在内部契约里。每个 Turn 固化
Context/Policy Snapshot，Context 只存 Project Index、具体 ReviewOutput 和 Artifact 的版本引用，不复制
正文或 Runtime 对话。一个完全离线 Fake 用稳定哈希证明 Session/Turn 映射、重复执行、恢复、取消、对账
与结果重复收集语义。后续真实 Adapter 复用同一 SDK Thread，只追加新消息，并把模型工作上下文交给
`create_deep_agent` 原生 Message、Checkpoint 和压缩；SDK 版本变化只影响 Adapter，但数据库事务、权限
和崩溃恢复仍必须单独验证，不能由 Fake 测试冒充。
