# Deep Agents Runtime Adapter

## 模块解决的问题

Phase 5 切片 4 验证既有五方法 `ResearchAgentRuntime` Port 能否承载 Deep Agents 原生 Harness，而不让
SDK Thread、Message、Checkpoint、StateBackend 或中间件类型扩散到 Domain、API 和业务表。实现精确使用
`deepagents==0.7.8`、完全离线 Fake Chat Model、确定性 Tool 与现有 PostgreSQL Checkpointer。

## 边界和执行流程

```text
RuntimeTurnRequest（只含本轮新消息 + Context/Policy Snapshot）
  → 稳定 session_id 映射 SDK thread_id / workspace_id
  → 稳定 turn_run_id 映射 execution_id
  → Checkpointer metadata 按 turn_run_id reconcile-first
  ├─ 已成功：从真实 checkpoint 重建结果并重放筛选 Event
  └─ 不存在：create_deep_agent 向同一 Thread 追加一个 HumanMessage
       → StateBackend 文件与 Message 由 Checkpoint 保存
       → 原生 SummarizationMiddleware 压缩并卸载旧历史
       → 最终 checkpoint_id 写入 SDK-neutral RuntimeTurnBinding
```

切片 7.0 已把 Adapter 接入生产 Worker composition root：默认仍装配 `FakeResearchAgentRuntime`，只有
显式 `AGENT_RESEARCH_RUNTIME_BACKEND=deep_agents` 才创建固定 `ChatDeepSeek` 与持久 Checkpointer。
Provider/SDK 类型没有进入业务 Port。切片 6 已由 ADR-0006 决定 Runtime 位于 ARQ Worker 进程内，并以独立
`RuntimeExecution` lease/fencing 闭合跨进程恢复控制；实现与真实进程证据见
[`agent-runtime-execution-recovery.md`](agent-runtime-execution-recovery.md)。

## 状态、数据模型和事务

- Session/Thread、Session/Workspace 和 Turn/Execution 使用稳定 SHA-256 opaque ID；业务层看不到 SDK
  类型或原始可配置 Thread；
- 每次 SDK 调用的 metadata 只保存 Turn/Session/Execution ID 和请求哈希，不保存用户消息正文、完整
  Prompt、论文正文或 Secret；请求哈希覆盖消息内容哈希与 Context/Policy Snapshot hash；
- `AsyncPostgresSaver.alist(config=None, filter={agent_runtime_turn_id: ...})` 跨 Thread 反查 Turn，最终
  checkpoint 的真实 `checkpoint_id` 进入 `RuntimeTurnBinding`；
- Deep Agents 0.7 的 DeltaChannel checkpoint 可能只保存消息增量，Adapter 必须用
  `graph.aget_state(checkpoint.config)` 重建完整 state，不能直接把单条 checkpoint `channel_values` 当完整
  Message 列表；
- Runtime 调用位置没有改变：`AgentTurnExecutor` 仍在数据库读事务结束后执行 Runtime，并在成功后开启
  独立短事务提交业务 Message/Binding/candidate/Event；Checkpoint 成功不等于业务结果提交成功。

## 上下文压缩与 Workspace

Adapter 用同名自定义 `SummarizationMiddleware` 替换 `create_deep_agent` 默认实例，因此仍使用 Deep
Agents 原生压缩逻辑，只把测试阈值降到可控值。强制压缩后：

- `_summarization_event` 保存 cutoff 与摘要消息；
- 被移出的完整历史写到 StateBackend 的 `/conversation_history/{internal_id}.md`；
- 第二 Turn 只传新的 `HumanMessage`，模型有效上下文由摘要加保留尾部组成；
- StateBackend 文件随同一 Thread checkpoint 保留，但它们仍是 Runtime 工作状态，不自动成为业务
  Message、WorkspaceSnapshot 或 Artifact。

## 工具与安全边界

- 读取并校验模型的 `ls_provider` 与 `ls_model_name`，只为精确 `provider:model` key 注册公开
  `HarnessProfile`；general-purpose subagent 设为 disabled，并显式传 `subagents=[]`，使 `task` 不进入
  当前模型且不污染同 Provider 的其他模型。重复构造只合并相同 set/scalar 配置，行为保持幂等；
- 使用公开 `FilesystemMiddleware(tools=...)` 只保留 `ls/read_file/write_file/edit_file/glob/grep`，不
  创建 `execute`；Harness Profile 同时排除 `execute` 作为纵深保护。Middleware 注入能力不等于本轮
  授权，文件 Tool 与自定义 Tool 都必须同时属于 Adapter 注册集合和当前
  `PolicySnapshot.allowed_tool_names` 才能对模型可见；
- 同一策略中间件在实际 Tool 调用 wrapper 再校验一次，避免模型伪造未展示的 Tool 名称绕过 schema
  可见性；空 allowlist 时文件与自定义 Tool 均不可见且不能执行；
- 切片 5 在 Adapter 内注册 `search_project_chunks` 与 `read_review_evidence_matrix`，但实现只依赖
  SDK-neutral `ProjectResearchContext` Port。两个工具用锁定的 `ToolRuntime` 注入 `turn_run_id`：模型
  schema 只能看到 query 或空参数，不能伪造 owner/Project/Snapshot/ReviewOutput/ChunkSet ID；
- Project Context 的 temporary/permanent/cancelled 安全错误会映射为既有 `RuntimeErrorKind`，最终回答
  Evidence 标记解析为小型 `RuntimeTurnResult.evidence_ids`；Application 仍负责 Citation 授权与事务提交；
- 切片 4 当时未接 MCP、Browser、Sandbox、网络、长期 Memory 或 Skill；7.1 后续已接 OpenSandbox，
  7.2 已在 Adapter 外围接入 LangChain MCP Adapter 的显式 session、Schema 校验和平台 interceptor。
  Playwright/Search MCP 与 Deep Agents Native Skills 仍分别属于 7.3/7.4；
- Runtime Event 只产生 `bound/started/assistant_delta/completed`，不输出模型思考、Tool 原始结果或 Graph
  State。

切片 7.0 增加逐 Turn 主 Agent Loop 模型预算：middleware 在主模型 node 前预留
`PolicySnapshot.max_model_calls` 并把私有计数写入 checkpoint；额度耗尽在 Provider 前永久失败，已确认
checkpoint 后恢复不返还额度。该预算不覆盖 Provider 在途不确定窗口，也不覆盖
`SummarizationMiddleware._summary_model.with_retry()` 最多 3 次内部 Provider 尝试，因此不是完整费用
硬上限。预算 State 只保留当前 `turn_run_id` 与计数，新 Turn 覆盖旧值；因此 graph revision 已从 v1
升为 `deep-agent-graph.v2`，旧 v1 RuntimeExecution/Checkpoint fail-closed。

7.1 把文件/`execute`/Project Tool 纳入统一 Tool 预算后 graph revision 升为 v3；7.2 又允许每个 Turn 从
不可变 `PolicySnapshot.mcp_refs` 注册经过校验的 MCP Tool，因此当前 revision 为
`deep-agent-graph.v4`。只有 execute/resume 打开显式 MCP ClientSession；session 包围 graph 执行并在
结束或异常时先于 Sandbox 关闭，collect/reconcile/cancel 离线路径不加载 MCP。版本或 Schema 漂移均
fail-closed，而不是沿旧 checkpoint 静默换 Tool。

## 失败、重复和取消

切片 6 补充：

- 图调用显式使用 `durability="sync"`；RuntimeExecution 持久保存最后确认 checkpoint、状态、版本和
  当前 Attempt/owner/fence；
- 新进程只认领已过期 lease，重新加载原 Context/Policy 后以 `resume_turn(response=None)` 和
  `astream(None, ...)` 沿同一 checkpoint 恢复；控制水位为空或落后于物理最新 Checkpoint 时都先选择
  物理最新状态，只有 Checkpointer 中确实不存在本 Turn 状态才重新提交首次消息；
- listing 和精确 checkpoint ID 都必须匹配 Turn、Session、Execution、request hash 与 runtime/graph
  revision，不能仅凭 thread/checkpoint ID 接受状态；
- 模型与 Tool middleware 在实际调用边界复核 permit，过期 owner 不能启动下一次调用或写 Runtime 终态；
- `FAILED/CANCELLED/SUCCEEDED` 均可跨 Adapter/进程对账；取消还必须先验证业务 Run 已进入取消路径；
- Runtime/Graph/Deep Agents/LangGraph revision 必须完全匹配，不兼容时 fail-closed；
- 真实进程测试已证明同步 checkpoint 确认的模型/Tool 不重放，但未确认的在途调用仍可能重试。

- 已存在 checkpoint 的相同请求只重放四个确定性 Event，不再次调用模型或 Tool；相同 Turn 的不同请求
  hash 返回 permanent `runtime_turn_conflict`；
- Provider/图异常归一化为不含原始内容的 temporary `runtime_execution_failed`；未授权 Tool 返回
  permanent `runtime_tool_not_allowed`；
- `BOUND` 后启动图，Deep Agents 固定 `before_agent` 首次更新先形成真实 checkpoint，再发出
  `STARTED`。此后取消可返回真实 checkpoint binding，并在进入下一模型/Tool 边界前停止；测试证明
  STARTED 后取消时 Fake Model/Tool 调用数均为 0；
- 已成功并提交最终 checkpoint 的结果可以在新连接/新 Adapter 中重复收集，模拟本地响应丢失时不会再次
  调用模型或 Tool；切片 6 进一步启动第二个 OS 进程并真实终止第一个执行进程；
- 上述证据不等于任意 Tool 副作用的 Effectively Once。Tool 已执行、其 checkpoint 尚未成功提交时的
  崩溃窗口未验证；正式 Project Tool 必须在切片 5 使用稳定 call/effect ID、唯一约束或调用记录保护；
- 失败/取消终态和 orphan `RUNNING` 已由切片 6 的持久 RuntimeExecution 对账；仍不宣称可立即中止已发出
  的真实远端请求，也不重放效果未知的 orphan `ToolExecution=RUNNING`；
- Checkpoint 列举或 state reconstruction 的 SDK/数据库/Serializer 异常统一归一化为安全 temporary
  Port 错误，原始异常内容不进入 safe message；`CancelledError` 保持取消语义并直接传播。

## 重要测试和运行结果

2026-08-25 实际运行：

- 首轮 TDD：Adapter 模块不存在，定向 pytest 得到 `ModuleNotFoundError`，`1 error in 0.17s`；
- Adapter 离线测试：`17 passed in 0.98s`，覆盖稳定 Binding、真实 checkpoint ID、成功结果 replay 不重复
  模型/Tool 调用、两轮 HumanMessage 稳定 ID 各一次、强制摘要/文件卸载、精确模型 Profile、文件/自定义 Tool 策略、
  协作取消、冲突及 Checkpoint 错误归一化；
- Testcontainers PostgreSQL：`1 passed in 4.37s`，关闭第一条 Checkpointer connection 后，新
  Adapter 仅凭 turn_run_id 恢复两个 Turn、复用一个 Thread、重复收集第二轮结果且模型/Tool 调用不增加；
- 扩大相关回归（Port/Fake/Executor/两轮/取消恢复）：`44 passed in 59.06s`；
- 完整非集成测试：`739 passed, 4 skipped in 59.61s`；
- 完整 `ruff check src tests` 通过；完整 Pyright 为 `0 errors, 0 warnings, 0 informations`；
- `git diff --check` 通过。
- 主智能体独立复验 Adapter 与真实 PostgreSQL 为 `18 passed in 4.59s`，既有
  Port/Executor/Fake/两轮/崩溃恢复为 `28 passed in 61.85s`，完整非集成为
  `739 passed, 4 skipped in 58.95s`；独立 Ruff 与 Pyright 同样通过。

切片 5 补充验证（2026-08-26）：开发智能体完全离线 Adapter 套件为 `21 passed in 1.15s`，主智能体
独立复验为 `21 passed in 1.12s`；新增覆盖两个 Project Tool 的 ToolRuntime scope 注入、模型 schema 隐藏平台 ID、
未授权 Tool 拒绝、安全错误分类与 Evidence ID 提取。受控命令沙箱内，未修改 HEAD Adapter 也会在
`STARTED` 后、模型调用前出现 selector 假性等待；同一命令在已批准非沙箱环境正常通过，因此该现象仍
只记录为开发工具环境限制。

切片 7.0 补充验证（2026-08-26）：Adapter 完全离线套件为
`32 passed in 1.80s`，增加 Provider 前零额度拒绝、Tool checkpoint 失败后恢复不返还主调用额度，以及
异步图包装安全错误的分类；配置/factory/Worker/Adapter 合并为 `64 passed in 2.40s`，其中锁定
`ChatDeepSeek` 完成离线构造和关闭，但未调用真实 Provider。

切片 7.2 补充验证（2026-08-27）：真实 `langchain-mcp-adapters` + 进程内 FastMCP Adapter
测试最终 `16 passed in 1.34s`，Sandbox/MCP 生命周期测试最终 12 passed；覆盖
prefixed Tool、Schema 漂移、显式 client 关闭、LangGraph `tool_call_id` 成功重放/冲突、取消/
缺调用 ID/预算零调用、SDK 生命周期错误脱敏、输出超限、handler 后 fence 丢失不写
旧 owner 终态，以及 graph 工厂异常清理。该结论记录 7.2 当时生产 Catalog 为空；7.3 已接入固定
Playwright/arXiv Catalog 与 Sandbox resolver，但仍未运行真实 OpenSandbox proxy 回路。

受控命令沙箱内运行 Deep Agents 异步链时曾出现 selector 假性等待；相同完全离线命令在沙箱外会正常
给出断言失败或通过结果。该现象只描述开发工具环境，不是产品 Sandbox 的能力或安全验证。

## 代码入口

- Adapter：`backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- 离线脚本模型：`backend/tests/fakes/deep_agent_model.py`
- Adapter 测试：`backend/tests/infrastructure/test_deep_agents_research_agent_runtime.py`
- PostgreSQL 恢复：`backend/tests/integration/test_deep_agents_runtime_checkpoint.py`
- Runtime lease/fencing：`backend/src/literature_agent/application/runtime_execution_control.py`
- 真实 OS 进程恢复：`backend/tests/integration/test_agent_runtime_process_recovery.py`
- Checkpointer：`backend/src/literature_agent/infrastructure/workflow/postgres_checkpoint.py`

## 已知限制

- 生产 Worker 默认仍使用 Fake Runtime；切片 7.0 已提供显式 Deep 模式并装配固定 Provider、Project
  Context、RuntimeExecution control 与持久 Checkpointer，但尚未执行真实 Provider Smoke；
- `resume_turn` 保留五方法 Port 语义，但本切片没有配置 Deep Agents HITL Interrupt，调用会明确返回
  `runtime_turn_not_interrupted`；
- 成功、失败、取消及 orphan RUNNING 已有持久 RuntimeExecution 和第二 OS 进程恢复证据；只允许相同
  Runtime/Graph/SDK revision 自动恢复，跨版本迁移尚未实现；
- 没有真实 Provider/OpenSandbox Smoke、Usage 账单闭环、流式 token、Native Skill 或正式 Artifact。
  7.3 已在无网络派生容器验证真实 Playwright/arXiv MCP 与本地 Browser/下载回路，但没有验证公共网络或
  OpenSandbox proxy；7.1 已用固定 Capability Profile 和 checkpoint State 对 Project/文件/execute Tool 强制统一
  `max_tool_calls`，主 Agent Loop 已强制 `max_model_calls`，但 summarization 内部调用与 Provider 在途窗口
  不在模型预算内；Native Skills 仍属于 7.4；
- Worker 已使用 checkpoint pool，并为每次 Runtime operation 创建独立 Saver/graph；完成后的 collect/
  reconcile 不依赖活 Sandbox。实际数据库容量与故障切换未做生产评测；
- Project Tool 成功后的重放、并发和 temporary retry 已有持久 effect 证据；Tool 外部调用完成后、
  ToolExecution 成功记录提交前的崩溃窗口仍没有 Exactly Once 证据，orphan RUNNING 当前 fail-safe 拒绝
  自动重放，需随具体外部 Tool 设计幂等/查询/补偿；
- StateBackend 对话历史文件仍依赖 Thread checkpoint，不能替代业务 Artifact/WorkspaceSnapshot 的保留、
  权限和清理策略。

恢复证据、待决选项与切片 6 验收条件见
[`Phase 5 Runtime 部署与崩溃恢复缺口台账`](../reports/phase-05-runtime-recovery-gap-log.md)。

## 60 秒面试说明

“我没有把 Deep Agents Thread 或 Checkpoint 放进业务 API，而是在 infrastructure 里用
`create_deep_agent` 实现既有五方法 Port。Session 和 Turn 分别确定性映射 Thread 与 Execution，真实
checkpoint ID 只作为 opaque Binding 返回。正常第二轮只追加新 HumanMessage；低阈值测试强制触发原生
summarization，并证明旧历史进入 StateBackend 的 conversation_history 文件后第二轮仍能完成。成功响应
丢失时，新连接/新 Adapter 用 PostgreSQL metadata 反查同一 Turn 并重复收集结果，模型和 Tool 不会再
执行；这不覆盖 Tool 执行后 checkpoint 提交前的崩溃窗口。
同时通过 Harness Profile、固定 Capability Profile 和 Tool middleware 关闭 task/未授权工具；`execute`
只在 Session 专属 OpenSandbox Backend 存在且本轮策略授权时可见，并与 Project/文件 Tool 共用统一预算。
PostgreSQL 仍拥有业务 Run、权限、Event 和 Artifact，SDK 成功从不直接等于业务提交成功。”
