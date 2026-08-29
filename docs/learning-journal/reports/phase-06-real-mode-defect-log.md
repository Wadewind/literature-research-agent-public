# Phase 6 Real 模式体验缺陷台账

日期：2026-08-29

本文记录 Phase 6 完成后、显式 Real 模式体验中发现的 Research Agent 集成缺陷。它补充阶段完成报告，
不把一次缺陷修复扩大为新的阶段范围。

## P6-REAL-001：OpenSandbox metadata label 阻止 Agent Turn 启动

### 现象

- AgentSession 可以创建，用户消息和对应 AgentTurnRun 也已持久化；
- 页面只显示用户消息，没有 Assistant Message，研究活动显示失败后仍误标为“可继续”；
- 每个 Turn 发生三次失败 Attempt，模型调用数和工具调用数均为零；
- OpenSandbox Server 返回 `INVALID_METADATA_LABEL`，指出 metadata value 超过 63 字符。

页面 URL 中的 UUID 是 `AgentSession.session_id`，不是 Run ID。真实 Turn Run ID 可从
`AgentMessage.turn_run_id`、Session 最新消息或 AgentTurnRun 查询获得。

### 根因

`SandboxWorkspaceManager` 同时用两种方式把 Network Profile 传给 Provider：

1. 独立的 `network_profile_id/version/hash` 参数，用于平台固定档案校验和 Lease 持久化；
2. OpenSandbox metadata label，用于非权威的运行环境标记。

其中完整 SHA-256 `network_profile_hash` 长度为 64，却被重复写入 OpenSandbox metadata value；
OpenSandbox 0.1.15 Server 按 Kubernetes label 规则把 value 限制为最多 63 字符，因此 Sandbox 在模型或
工具执行前创建失败。离线 Fake Provider 不校验 metadata，既有真实 Smoke 又直接传入短 metadata，导致
组合缺陷未被覆盖。

### 最小修复

- 完整 Network Profile hash 继续保存在 PolicySnapshot、SandboxLease，并通过独立 Provider 参数参与固定
  档案校验；只从 OpenSandbox metadata 中移除这份重复值；
- `OpenSandboxProvider` 在 SDK I/O 前校验 metadata key/value 的长度、字符和保留前缀边界；
- SDK 若仍返回 `INVALID_METADATA_LABEL`，归一化为永久的
  `runtime_sandbox_metadata_invalid`，避免配置错误发生三次无意义重试；
- Agent UI 对最新失败 Turn 重放终态 Event，显示安全失败卡片、短 Run ID 和稳定错误码；原始 Provider
  message、endpoint 或内部异常不进入页面；
- 历史失败 Run 保持终态，不原地改写。修复并重启服务后，应新建 Session 或发送新 Turn 验证。

### 回归边界

- Workspace/Provider 组合测试确认 64 位 hash 不再进入 metadata，但仍传入独立档案参数；
- Adapter 测试确认非法 label 在 SDK 调用前失败，并确认 SDK 同类错误被归一化为永久错误；
- UI 测试确认失败说明绑定短 Run ID，且不会回显事件中的原始错误明细；
- 普通测试保持离线，不创建真实 Sandbox，也不调用付费模型或公网。

### 已知限制

本次只修复 metadata 组合契约与对应 UI 诊断。平台仍没有统一的 Run Diagnostic 聚合 API；更普遍的
FailureRecord 和诊断视图继续按
[错误可观测性与 Run 诊断反思](../reflections/error-observability-and-run-diagnostics.md) 延期处理。

## P6-REAL-002：Sandbox `execute` 被全局 Harness Profile 隐藏

### 现象与根因

固定 PolicySnapshot 已允许 `execute`，OpenSandbox Backend 也实现了执行协议，但 Adapter 为精确模型注册的
Harness Profile 无条件把 `execute` 加入 `excluded_tools`。Deep Agents 0.7.8 在最终模型调用前过滤这项
能力，因此模型看不到 Tool Schema；已有测试只证明伪造的同名 Tool call 可以到达 Tool node，没有证明
真实模型能看到并自主选择 `execute`。

### 最小修复

- Harness Profile 关闭默认 general-purpose subagent，并按 Backend 能力处理 `execute`：StateBackend
  精确排除，Session 专属可执行 Backend 保留；不再对所有 Backend 无条件排除；
- `execute` 仍需同时满足 Backend 实现执行协议、Adapter 注册该能力、PolicySnapshot 允许三项条件；
- StateBackend 和未授权 Turn 继续由 `_RuntimeToolPolicyMiddleware` 隐藏并在实际调用边界拒绝 `execute`；
- 增加 Harness Profile 回归测试，防止后续升级再次把 Sandbox `execute` 全局排除。

本修复不开放宿主 Shell/Python，也不改变 Sandbox 镜像、网络 Profile、超时、预算或动态安装依赖边界。

## P6-REAL-003：三个连续 Turn 暴露 MCP、Browser 与引用输出组合缺陷

### 已确认事实

Session `60c1afa7-44b2-46f1-8370-ee1900081017` 的三个 Turn 分别失败于：arXiv Search MCP 序列化结果
超过 8,000 字符、Playwright `browser_navigate` 在 30 秒外层 Tool 边界超时、Deep Agents 已完成但富文本
不符合严格逐行 Evidence 契约。三轮分别只使用 1/12、1/12、6/12 次 Tool，不是 Tool 总预算耗尽。

第三轮 Checkpoint 的最终输出包含 17 个非空 Markdown 行和 8 个 Evidence 标记；有效标记前缺少契约要求
的空格，另有非法占位 ID。Runtime 状态为 succeeded，但业务事务正确拒绝提交，因此 PostgreSQL 没有
Assistant Message。第二轮还留下 `ToolExecution=RUNNING`，而对应 Sandbox generation 已因 dirty 清理。
第一轮实际把“今年”解析为 `date_from=2025-01-01`，说明模型没有可靠的平台日期基准。

### 修复

- MCP 纯文本超限时只保留带截断说明的有界前缀，不保存超大原文；非文本超限降级为有界 Tool error；
- 普通 Tool 超时提升到 60 秒，Project Research Policy 升至 v4；MCP 内部边界提前 1 秒，并在外层取消时
  尝试把 Effect 收口为 temporary failure；
- 本轮实际使用 Project Context Tool 时，从 SDK 富文本中仅提取并规范化合法带引用 Claim，仍由
  Application 校验 Evidence 所属 Run/Project；未读取项目证据的浏览器/文件/执行任务不强制 RAG 格式；
- 每轮消息注入 `ContextSnapshot.created_at` 的 UTC 时间基准，约束“今年”等相对日期。

普通回归保持完全离线。本修复不证明百度或任意公网目标一定能在 60 秒内完成，也没有新增 Browser trace；
若目标持续不可达，将以可重试 MCP 超时和已收口 Effect 失败，而不是悬空副作用记录结束。
