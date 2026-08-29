# Agent 固定能力与硬预算

## 解决的问题

Research Agent 可以调用 Project Context、Deep Agents 文件/`execute`、固定 MCP 和 `submit_artifact`，但模型
看到 Tool 不等于获得授权。Phase 6 Slice 5 把每轮允许能力、Tool 契约和硬预算冻结到不可变
`PolicySnapshot`，并以 PostgreSQL `AgentTurnUsage`、模型 reservation 和脱敏 `AgentToolCall` 作为业务事实。

## 边界与流程

```text
Message 短事务
  → 冻结 PolicySnapshot（所有允许 Tool 的 version + input schema hash + Budget）
  → 创建 AgentTurnUsage
Worker/Runtime（数据库事务外）
  → 首次边界固定 300s deadline
  → 模型/Tool 前以短事务锁 Run + Usage，复核 owner/Project/Session/Context/Policy/取消
  → 用稳定 reservation 预留次数并提交安全 agent_budget_updated Event
  → fence 校验后调用 Provider/Tool
  → fence 再校验，保存 Provider Usage 或 Tool hash/size/status/时长
```

Deep Agents/LangGraph state 中原有计数仍保留为图内快速保护，但不能替代 PostgreSQL 事实。模型调用使用
`model:{turn_run_id}:{ordinal}`；Tool 使用 `tool:{turn_run_id}:{tool_call_id}`。同一 reservation 重放不重复
计数。可重放名称不按 `arxiv_`/`playwright_` 前缀猜测，而是只从本轮 `PolicySnapshot` 明确生成：两个
Project Context Tool、`submit_artifact` 和 `mcp_refs.tools`。它们也只有在各自下层稳定 effect cache 能
对账时才允许 handler 重放；普通自定义 Tool、文件和 `execute` 的 RUNNING/终态 effect 不重新执行。

模型 reservation 限制和去重的是平台可识别的**逻辑模型步骤**，不是物理 Provider 请求。若 Provider
已经接收请求，但响应或 Worker 在平台取得可判定结果前丢失，重试同一逻辑步骤仍可能再次发出付费请求，
且第一次请求的 `usage_metadata` 可能永久缺失。Checkpoint、稳定 reservation 和 reconcile 可以缩小并
对账这个窗口，但无法让外部 Provider 调用 Exactly Once，也不能把 8 次逻辑步骤上限解释为物理请求或
精确费用上限。

## 固定精简 Profile

| 限制 | 值 | 语义 |
|---|---:|---|
| 模型步骤 | 8 / Turn | 逻辑步骤调用前持久预留；不保证物理 Provider 请求至多 8 次 |
| Tool 调用 | 12 / Turn | 调用前持久预留 |
| Turn 墙钟 | 300 秒 | 从首次 Runtime 边界开始，重试不重置 |
| 固定 Tool/MCP | 60 秒 / 次 | 同时受剩余墙钟约束；MCP interceptor 提前 1 秒超时并收口 Effect |
| Sandbox `execute` | 60 秒 / 次 | 同时受剩余墙钟约束 |
| Tool 安全输出 | 64 KiB / 次 | 通用后置上限；MCP 原文先裁剪到 8,000 字符的持久化边界 |
| 相同 Tool + args hash | 2 次 / Turn | 第 3 个不同 invocation 在执行前拒绝 |
| 模型输入 | 约 60,000 Token / 次 | `count_tokens_approximately`，包含 system 与 Tool schemas |
| 模型输出 | 4,096 Token / 次 | Provider `max_tokens` 与不可变 PolicySnapshot 双重限制 |

输入 Token 是近似安全上限，不是 Provider 精确计费。Provider 返回 `usage_metadata` 时以可空字段渐进记录；
同一字段只允许 `NULL → value` 或同值重放。响应丢失时 usage 可能缺失，总 Token 和费用不可得时不做虚假
硬拒绝。Workspace 50 MiB、单文件/Artifact 10 MiB 沿用既有契约；下载次数与总量属于 Slice 7。

三种 Project Research Policy 曾随上述行为从 v3 提升到 v4；旧 Turn 继续使用冻结的 v3 数值，新 Turn
才获得 60 秒 Tool 预算和 MCP 超时分层。2026-08-30 又因单次模型输出预算从 2,048 提升到 4,096，
三种 Policy 进一步提升到 v5；已有 v4 Turn 仍按其不可变快照恢复。

## 安全与公开投影

Runtime 对 Tool 参数只接受 finite canonical JSON，不使用对象 `str()` 兜底。实际 Tool schema 每次与
`PolicySnapshot.tool_refs` 比较；MCP loader 和 Skill materializer 仍分别复核 MCP schema 与 Skill
version/content hash。转换后的 MCP `StructuredTool.args_schema` 若为 dict，契约哈希直接使用原始 MCP
`inputSchema`，不把 Tool description 混入。权限闭包和 Runtime lease/fence 在外部调用前后检查；持久
Usage middleware 位于 policy guard 外层，只有后置 fence 校验通过才记录成功，外部 I/O 不发生在数据库事务内。

`GET /api/v1/agent-turn-runs/{run_id}/tool-executions` 只返回 owner-scoped Usage 与以下摘要：Tool 名称/
版本、schema/args/result hash、输入输出大小、状态、时长和安全错误。它不读取旧 `ToolExecution.result_payload`，
也不返回 raw args/result、MCP endpoint、Prompt、正文或 Secret。

## 失败、重复、取消

- Budget、deadline、schema 漂移、未授权、第三次相同调用均在外部 effect 前 fail closed；
- Tool claim 使用条件更新；未知 RUNNING effect 不由新 Worker 重新执行；
- 终态成功/失败只接受 hash/size 或安全错误完全相同的重放，冲突永久拒绝；
- 取消或 fence 失效后不开始新 Provider/Tool；若外部调用期间发生变化，后置检查阻止后续步骤。MCP
  handler 被外层超时取消时会先尝试写 temporary failure；若 fence 已失效而不能安全写回，仍交给
  reconcile，不由旧 Worker 越权完成；
- `agent_budget_updated` 只记录计数与上限；拒绝由稳定 Runtime 错误进入既有 Turn 失败事件链。

## 重要测试与入口

- Domain/Application：`backend/tests/domain/test_agent_usage.py`、
  `backend/tests/application/test_agent_usage_service.py`；
- Adapter/API/Schema：`backend/tests/infrastructure/test_deep_agents_research_agent_runtime.py`、
  `backend/tests/api/test_agent_sessions.py`、`backend/tests/infrastructure/test_agent_usage_schema_contract.py`；
- 代码入口：`application/agent_usage_service.py`、`domain/agent_usage.py`、
  `infrastructure/persistence/agent_usage_repository.py` 和 `DeepAgentsResearchAgentRuntime`。

当前固定 Deep Agents 0.7.8 在 Python 3.13.14 环境下，既有带 Tool 的 Fake Model 图测试会停在
Filesystem→Summarization model middleware、尚未进入 Fake Model；动态加载 HEAD 原实现同样复现。Slice 5
新增的 Tool reservation/replay 契约因此使用直接 middleware 离线测试，不能把它表述为新的完整 Deep Agents
Project Tool 图回路证据。既有真实 Project/MCP Effectively Once 测试仍需在该夹具兼容问题解决后复跑。
迁移不会改写旧 Turn 的不可变 Policy hash，也不会为旧 Turn 伪造 Usage；升级前已经终态的历史 Turn 继续
通过原 Event/ToolExecution 查看，新 Tool Usage API 只保证 Slice 5 后创建的 Turn。升级时仍活动且缺少
Usage 的旧 Turn 会 fail closed，需要受控重新发消息，而不是带着不完整预算恢复。

## 60 秒面试说明

我没有把 LangGraph checkpoint 当作费用和副作用事实。每个 Turn 在 PostgreSQL 冻结 Tool 契约和预算，
每个逻辑模型步骤/Tool 调用先用稳定键原子预留，再在事务外执行，最后只写回脱敏摘要。Checkpoint replay
命中同一逻辑 reservation 不重复计数，但响应丢失后的物理 Provider 请求仍不保证 Exactly Once；只有拥有
下层 effect cache 的 Tool 可以对账重放，Shell/文件这类未知副作用 fail closed。这样既复用 Deep Agents
的 Agent harness，也保留平台权限、取消、审计和恢复边界。
