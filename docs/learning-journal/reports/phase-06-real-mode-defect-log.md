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
