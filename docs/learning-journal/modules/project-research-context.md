# Project Research Context

## 模块解决的问题

Phase 5 切片 5 让交互式 Research Agent 真正按当前 Turn 的授权快照读取 Phase 2 Project Chunk Index 和
Phase 3 Review Evidence Matrix，同时不把 Deep Agents SDK、模型可控 scope、全文或完整 Matrix 引入
业务契约。它还关闭两个关键可靠性缺口：Project Tool 重放不能重复检索/物化副作用，Agent 最终引用必须
成为当前 AgentTurn Run 可由既有 Citation Validator 验证的业务事实。

本切片没有扩大五方法 `ResearchAgentRuntime` Port，也没有把生产 Worker 从 Fake Runtime 切换到 Deep
Agents。两个 Project Tool 只在显式允许的 Application/Adapter/PostgreSQL 测试策略中组装。

## 边界和执行流程

```text
Deep Agents model
  ├─ search_project_chunks(query)
  └─ read_review_evidence_matrix()
       ↓ ToolRuntime 只注入稳定 turn_run_id
ProjectResearchContext Port（SDK-neutral）
       ↓ 短事务：锁 Run，复核 Turn/Session/Context/Policy，认领 ToolExecution
       ↓ 事务外：Retriever；或独立只读事务：读取指定 Matrix
       ↓ 短事务：再次锁定/检查取消，验证 scope，物化 Agent Evidence，提交 effect + Event
RuntimeTurnResult（Assistant 正文 + Evidence IDs）
       ↓ 正文标记/DTO/当前 Run Evidence/Citation Validator
单个成功事务：ClaimSet/Claim/Citation + Assistant Message + Binding + candidate + Event + Run
```

`ProjectResearchContext` 只暴露两个方法。Deep Adapter 内用 Deep Agents 0.7 的 `ToolRuntime` 从锁定的
Turn context 取得 `turn_run_id`；模型 schema 中 Search 只有 `query`，Matrix Reader 没有输入字段。
owner、Project、Snapshot、ReviewOutput、PaperVersion 和 ChunkSet ID 均不能由模型提交或覆盖。

## ContextSnapshot 与精确版本

每次 Tool 调用都沿以下闭包重新加载，而不是信任模型或 Adapter 内存：

```text
Run(agent_turn, owner, project, RUNNING)
  → AgentTurnRun
  → AgentSession(owner, project)
  → ContextSnapshot(owner, project, session, turn)
  → PolicySnapshot(owner, project, session, turn, allowlist, budget)
```

Search 复用现有 `Retriever.retrieve_for_scope`，但新增可选 `chunk_set_scope`。Agent 路径同时把 Snapshot
固化的 `(paper_id, paper_version_id)` 和精确 `chunk_set_id` 下推到 semantic/FTS SQL；同一 Version 下
另一个 READY ChunkSet 不会漂入本轮。既有 RAG/Review 不传该参数，行为不变。Embedding 和检索发生在
数据库事务外；检索前和物化前都检查 Run 状态，`CANCEL_REQUESTED` 后不能提交结果。

Matrix Reader 只读取 Snapshot 指定的 `ReviewOutput.output_id`，复核：

- owner/Project 与 Review Run；
- `output_type=evidence_matrix`、`output_key=evidence-matrix`、`version=1`、
  `schema_version=evidence-matrix.v1`；
- `rows`、`paper_failures`、`summary` 的 keys、类型、去重、计数与长度上限；
- 所有成功/证据不足/失败 Paper 都属于 Snapshot，成功和失败不能重叠；
- source Evidence 属于该 Review Run，且其 Chunk 属于 Snapshot 固化的 ChunkSet。

Reader 在施加返回预算前先校验全部 source Evidence 的 Review Run、Project、PaperVersion 与精确
ChunkSet 闭包；随后按 `(paper_id, dimension_key)` 稳定排序并施加 12 行/8000 字符结果预算，再只复制
实际返回行引用的 Evidence。未返回的第 13 行若携带越权 Evidence 会使整个调用永久失败，但合法的未返回
行不会被物化到 Agent Run。该验证只使用当前持久聚合可重建的闭包，没有重新读取 Phase 3 Strategy
dimensions，因此不声称重新运行了完整
`validate_evidence_matrix`。

## ToolExecution 状态、事务与重放

`agent_tool_executions` 保存：

- `effect_id = SHA256(turn_run_id + tool_name + canonical args hash)`；
- Tool name、args hash、status、attempt、时间；
- 成功时有界 JSON result 与 result hash；失败时稳定 error kind/code/safe message。

原始 query 和完整参数不入库。Canonical JSON 拒绝非对象、NaN/Infinity、不可序列化值和超过 4000
字符的输入；成功结果先 JSON round-trip 复制并限制为 8000 字符，调用者后续修改原 dict 不会改变事实。
数据库 CHECK 强制三种状态字段闭包，并用 `JSONB(none_as_null=True)` 保证 Python `None` 真正保存为 SQL
NULL。

同一 `(turn, tool, args)` 的成功调用直接 replay 持久结果，不再调用 Retriever 或复制 Evidence；RUNNING
重复明确返回 temporary in-progress；temporary 失败可由同一 effect 条件认领并增加 attempt，不作为新
effect 重复占用 `max_tool_calls`；permanent/cancelled 失败不能原样重做。领域状态机只允许 RUNNING
进入 succeeded/failed，并在 flush 前限制 100 字符 Tool name/error code。Run 行锁、唯一约束和条件更新
收敛并发。这里是 Effectively Once：Tool 外部效果完成后、成功记录提交前的崩溃窗口仍可能留下 orphan
RUNNING，切片 6 负责恢复所有权和 reconcile，不宣称 Exactly Once。

外部 Retriever 不在事务内。Matrix 只读和最终物化分别使用短事务；成功 effect、Agent Evidence、
`agent_tool_succeeded` Event 和 Run event sequence 在同一事务提交。Event 只保存 tool name、effect ID、
status、attempt/result hash 或安全错误 code，不保存 query、参数、Chunk/Matrix 正文、Prompt 或 Tool
输出。

## Evidence、最终回答与原子提交

Search Chunk 与 Matrix source Evidence 都通过 `get_or_add_many` 幂等物化为当前 `AgentTurnRun.run_id`
下的 Evidence 快照。这样 Agent 不能直接把 Review Run Evidence ID 当成本轮引用事实，也不能伪造跨
Run/Project Evidence。

Assistant Message 可以是包含标题、项目证据、世界知识、外部来源、操作结果和 Artifact 说明的混合富文本。
其中只有需要声明“由当前 Project Evidence 支持”的 Claim 使用以下严格行末标记：

```text
论述文本 [evidence:evidence-id-1,evidence-id-2]
```

整轮没有任何有效内容时也可以只输出固定文本 `当前授权上下文证据不足。`。总内容、显式 Claim 数、单条
Claim、每 Claim Evidence 数和 ID 长度均有上限；任何包含 `[evidence:` 的行都必须符合语法，不能用非法
占位标记降级为普通正文。显式 Claim 按首次出现顺序得到的唯一 Evidence IDs 必须与
`RuntimeTurnResult.evidence_ids` 精确一致。Application 随后只为这些 Claim 加载 Evidence，并复用既有
`validate_citations` 和通用 `ClaimSet/Claim/Citation`；未标记正文原样持久化，但没有平台验证的引用语义。

是否启用该契约只看当前 Turn 的最终 Assistant Message，不看同一 SDK Thread 中前序 Turn 是否曾调用
Project Context Tool。因此“上一轮检索项目论文，下一轮生成文件”不会被旧 ToolMessage 污染；同一轮也
可以组合 Project 检索、外部 Search/Browser、`execute`、文件写入和 Artifact 提交。

成功时 `ClaimSet/Claim/Citation`、带可空 `claim_set_id` 的 Assistant Message、Runtime Binding、staged
candidate、安全 Event、Run SUCCEEDED 与 Session active Turn 释放在同一短事务提交。引用缺失、伪造、
跨 Run/Project 或正文/DTO 不一致会在任何结果事实写入前归一化为 permanent 失败并回滚。旧生产 Fake
没有启用两个 Project Tool且不返回 Evidence，其 Assistant Message 继续保持 `claim_set_id=NULL`，不会
把任意 Fake 正文错误标记成“证据不足”。

## 失败、重复、取消和安全

- owner/Project/ReviewOutput 篡改由平台 scoped Repository 和闭包检查拒绝；
- Search 结果必须属于 Snapshot 的 PaperVersion/ChunkSet，重复 Chunk ID 在物化前去重；
- Matrix Paper failure、证据不足行也必须在 Snapshot 内，不能利用无 Evidence 行绕过 scope；
- 调用前已取消不创建 effect/检索；Retriever 在途后收到取消，返回时二次检查阻止 Evidence/成功结果；
- Tool 失败只持久稳定 code 和安全描述，原始 Provider/SQL/Secret 不进入 Tool Event；
- `max_tool_calls` 按稳定新 effect 计数；本切片未统一实现 `max_model_calls` 或其他内置 Tool 预算。

## 重要测试和运行结果

2026-08-26 开发智能体与主智能体实际运行；以下先记录开发智能体结果，主智能体独立结果见本节末尾及
Phase 5 Spec：

- 首组红灯：`3 errors in 0.27s`，缺少 Answer/ToolExecution/ORM；parser 补强红灯
  `4 failed, 8 passed`；canonical JSON 补强红灯 `6 failed, 2 passed`；终态重写/数据库长度补强红灯
  `2 failed, 8 passed`；多 marker 语法补强红灯 `1 failed, 12 passed`；
- 非数据库契约：`42 passed in 0.26s`；
- Deep Adapter 完全离线：非沙箱运行 `21 passed in 1.18s`；
- Project Context、精确 ChunkSet、Executor 引用与 Message Repository PostgreSQL 最终定向回归：
  `12 passed in 39.22s`；
- 主审补强的截断范围外跨 Run Evidence 用例先得到 `1 failed in 3.67s`，修复后单测
  `1 passed in 4.87s`，完整 Project Context PostgreSQL 回归 `8 passed in 26.43s`；
- 受影响扩大 PostgreSQL 首次 `29 passed, 1 failed, 1 error in 226.10s`；failure 为旧测试漏装两个
  Repository factory，error 为 Testcontainers 容器被外部移除；修正/单独复跑 `2 passed in 8.28s`；
- Alembic `upgrade → downgrade -1 → upgrade → check`：`2 passed in 4.94s`；单 head
  `e7b4c2a9d6f1`；
- `ruff check src tests migrations/...` 通过；`pyright src` 为
  `0 errors, 0 warnings, 0 informations`。
- 主智能体独立复验：非数据库契约 `42 passed in 0.25s`，Deep Adapter 完全离线
  `21 passed in 1.12s`，聚焦 PostgreSQL 套件 `12 passed in 40.16s`，迁移往返在正确虚拟环境 `PATH`
  下 `2 passed in 5.01s`，完整非集成回归 `779 passed, 4 skipped in 72.79s`，Ruff 通过，Pyright
  `src` 0 errors；扩展 PostgreSQL 回归的 31 个用例通过后，唯一 error 为 Testcontainers 容器在启动
  探测期间被外部移除，对应用例单独复跑 `1 passed in 3.67s`。

## 代码入口

- `backend/src/literature_agent/application/ports/project_research_context.py`
- `backend/src/literature_agent/application/project_research_context_service.py`
- `backend/src/literature_agent/domain/tool_execution.py`
- `backend/src/literature_agent/domain/agent_answer.py`
- `backend/src/literature_agent/application/agent_turn_executor.py`
- `backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- `backend/src/literature_agent/infrastructure/persistence/tool_execution_repository.py`
- `backend/migrations/versions/e7b4c2a9d6f1_add_agent_project_context_facts.py`
- `backend/tests/integration/test_project_research_context.py`
- `backend/tests/infrastructure/test_deep_agents_research_agent_runtime.py`

## 已知限制

- 本切片完成时生产 Worker 仍使用 Fake Runtime；切片 7.0 随后已支持显式 Deep 模式，但 Slice 1 固定
  Policy 仍是 `max_tool_calls=0`，因此不代表真实 Project Tool 回路已开放；
- orphan RUNNING、Tool 外部效果后/记录前窗口、第二 OS 进程接管、失败/取消 Runtime 终态持久对账留给
  切片 6；
- Matrix Reader 不重跑依赖 Strategy dimensions 的完整 Phase 3 validator，只验证持久聚合闭包；
- Matrix 返回的是稳定、有界的部分聚合，不是完整 Matrix 导出接口；
- 没有接 MCP、Browser、Sandbox、网络、Skill、WorkspaceSnapshot 或正式 Artifact；
- 切片 7.0 已实现 checkpoint 持久的主 Agent Loop `max_model_calls`；summarization 内部调用、Provider
  在途窗口和非 Project Tool 的统一预算仍不在该边界内。

## 60 秒面试说明

“我把 Project 数据作为两个平台只读 Tool 提供给 Deep Agents，但没有让模型提交 owner、Project 或
ChunkSet。Deep Adapter 只通过 ToolRuntime 注入 turn_run_id，Application 每次从 PostgreSQL 重建并复核
Run、Turn、Session、Context/Policy Snapshot。检索精确固定 PaperVersion 与 ChunkSet，Matrix 只复制
实际返回行的 Evidence 到当前 Agent Run。每个 Tool 参数生成稳定 effect，成功重放不再检索，RUNNING
并发拒绝，temporary 失败沿同一 effect 重试，安全 Event 不保存 query 或正文。最终回复完整保存，只有
显式行内 Evidence Claim 复用 Citation Validator 和 ClaimSet，并在一个事务中提交 Message、Citation、
Binding 和 Run 终态；未标记内容可以表达外部研究与工具结果，但不会伪装成平台验证事实。这样复用了
Deep Agents 的 Agent Harness，同时 PostgreSQL 仍拥有权限、审计、引用和可靠性；尚未解决的 orphan
RUNNING 和记录前崩溃窗口明确留在下一恢复门槛。”
