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

Adapter 由分层/集成测试显式构造，生产 Worker 仍装配 `FakeResearchAgentRuntime`。因此本切片没有决定
Runtime 位于 ARQ Worker 进程内还是独立 Deployment，也没有新增真实 Provider 配置。这三项相互依赖的
恢复缺口统一记录在
[`phase-05-runtime-recovery-gap-log.md`](../reports/phase-05-runtime-recovery-gap-log.md)，由 Phase 5
切片 6 在 Project Context 接入后闭合。

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
- 未接 MCP、Browser、Sandbox、网络、长期 Memory 或 Skill；
- Runtime Event 只产生 `bound/started/assistant_delta/completed`，不输出模型思考、Tool 原始结果或 Graph
  State。

## 失败、重复和取消

- 已存在 checkpoint 的相同请求只重放四个确定性 Event，不再次调用模型或 Tool；相同 Turn 的不同请求
  hash 返回 permanent `runtime_turn_conflict`；
- Provider/图异常归一化为不含原始内容的 temporary `runtime_execution_failed`；未授权 Tool 返回
  permanent `runtime_tool_not_allowed`；
- `BOUND` 后启动图，Deep Agents 固定 `before_agent` 首次更新先形成真实 checkpoint，再发出
  `STARTED`。此后取消可返回真实 checkpoint binding，并在进入下一模型/Tool 边界前停止；测试证明
  STARTED 后取消时 Fake Model/Tool 调用数均为 0；
- 已成功并提交最终 checkpoint 的结果可以在新连接/新 Adapter 中重复收集，模拟本地响应丢失时不会再次
  调用模型或 Tool；该测试消除了对 Adapter 内存状态的依赖，但没有启动第二个 OS 进程；
- 上述证据不等于任意 Tool 副作用的 Effectively Once。Tool 已执行、其 checkpoint 尚未成功提交时的
  崩溃窗口未验证；正式 Project Tool 必须在切片 5 使用稳定 call/effect ID、唯一约束或调用记录保护；
- 在途取消与失败状态目前保存在 Adapter 协作状态中。虽然其最近 checkpoint ID 可对账，但本切片不宣称
  CANCELLED/FAILED Runtime 终态标记本身可跨进程恢复，也不宣称可立即中止真实远端 Provider 的在途
  请求。新 Adapter 遇到 orphan `RUNNING` checkpoint 也不会自动 resume；部署拓扑与 Runtime lease
  所有权确定前，不能宣称 Worker 在执行途中崩溃后可恢复；
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

受控命令沙箱内运行 Deep Agents 异步链时曾出现 selector 假性等待；相同完全离线命令在沙箱外会正常
给出断言失败或通过结果。该现象只描述开发工具环境，不是产品 Sandbox 的能力或安全验证。

## 代码入口

- Adapter：`backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- 离线脚本模型：`backend/tests/fakes/deep_agent_model.py`
- Adapter 测试：`backend/tests/infrastructure/test_deep_agents_research_agent_runtime.py`
- PostgreSQL 恢复：`backend/tests/integration/test_deep_agents_runtime_checkpoint.py`
- Checkpointer：`backend/src/literature_agent/infrastructure/workflow/postgres_checkpoint.py`

## 已知限制

- 生产 Worker 仍使用 Fake Runtime，真实 Adapter 只完成受限 Spike；
- `resume_turn` 保留五方法 Port 语义，但本切片没有配置 Deep Agents HITL Interrupt，调用会明确返回
  `runtime_turn_not_interrupted`；
- 成功 Execution 已有新连接/新 Adapter 恢复证据，证明不依赖 Adapter 内存状态，但没有启动第二个 OS
  进程；失败/取消终态没有独立持久 Runtime registry，orphan `RUNNING` checkpoint 不会自动 resume；
- Runtime 部署拓扑与 Execution lease/recovery owner 尚未决定；这是切片 6 的显式门槛，不由 Adapter
  默认假设。门槛通过前不能把同进程测试描述为执行中 Worker 崩溃恢复；
- 没有真实模型、Usage、流式 token、统一模型/Tool 动态预算、MCP、Browser、Sandbox、Skill、正式
  Artifact 或 WorkspaceSnapshot；两个 Project Tool 已由平台按稳定 effect 强制 `max_tool_calls`，但
  Adapter 的其他内置/自定义 Tool 与 `max_model_calls` 尚未统一计数；
- Project Tool 成功后的重放、并发和 temporary retry 已有持久 effect 证据；Tool 外部调用完成后、
  ToolExecution 成功记录提交前的崩溃窗口仍没有 Exactly Once 证据，orphan RUNNING 留给切片 6；
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
同时通过 Harness Profile、文件工具 allowlist 和 Tool 执行 wrapper 关闭 task/execute 与未授权工具。
PostgreSQL 仍拥有业务 Run、权限、Event 和 Artifact，SDK 成功从不直接等于业务提交成功。”
