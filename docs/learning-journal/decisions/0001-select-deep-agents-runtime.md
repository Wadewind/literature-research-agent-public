# ADR-0001：选择 Deep Agents 作为 Research Agent Runtime

- 状态：已接受
- 日期：2026-08-20
- 决策者：项目维护者

## 背景

Core Research Backend 先交付确定性的文献导入、RAG、引用校验和固定 Review Workflow。其上的 Research Agent Extension 需要在授权 Evidence 范围内规划研究任务、调用 MCP 工具，并在受限 Sandbox 中处理文件和生成 Artifact。

项目是个人项目，不适合自行实现一套通用 Agent Loop、Checkpoint、MCP 编排和 Sandbox 抽象；同时也不能让第三方 SDK 接管业务 Run、权限、预算、审计或 Artifact 归属。

## 决策

选择基于 LangGraph 的 Deep Agents 作为 Phase 5/6 的 Research Agent SDK，并通过项目自有的 `ResearchAgentRuntime` Port 接入。

选择它的主要原因是其能力方向与目标吻合：

- 延续项目既定的 LangGraph 技术路线，支持持久化执行、Interrupt/Resume 和运行上下文；
- 可接入 MCP 工具，适合论文相关公开资源发现和受控工具调用；
- 提供可插拔 Backend/Sandbox 方向，便于把文件操作和代码执行放入隔离环境；
- 避免个人项目重复实现通用 Agent 编排基础设施。

## 不变量与安全边界

- PostgreSQL 中的业务 Run、Event、Evidence、Usage、Approval 和 Artifact 始终是产品事实来源；Deep Agents Thread、Checkpoint、Store 和 Workspace 只是 Runtime 内部状态。
- Domain、公开 API 和业务数据库枚举不暴露 Deep Agents 类型；API 与 Worker 只依赖 `ResearchAgentRuntime`。
- MCP Server、Tool、网络目标和 Sandbox 配置由平台白名单提供，用户不能提交任意配置。
- `FilesystemBackend`、`LocalShellBackend` 或等价宿主执行能力不得用于生产 Agent。
- 权限、预算、审批、超时、网络策略、输出限制和 Artifact 校验由平台执行；Prompt、MCP 或 SDK 本身不是安全边界。
- Phase 5 默认关闭任意 Shell、子 Agent、长期 Memory 和开放网络，只验证一个固定研究故事。

## 后果

正面影响：技术路线集中在 LangGraph 生态，能够复用成熟的 Agent、MCP 和 Backend 能力，降低个人项目的实现范围，并保留可展示的可靠性与安全边界。

代价与风险：项目需要维护 SDK Adapter 和升级契约测试；Deep Agents 的事件、重试、Checkpoint 和 Sandbox 语义可能与业务模型不完全一致；具体 Sandbox Provider、部署拓扑和 MCP 会话模式仍需通过 Phase 5 Spike 验证。

## 验证门槛

Phase 5 必须验证取消与恢复、重复副作用、MCP 白名单、Sandbox 隔离、Artifact 取回、事件筛选和 SDK
升级契约。任何关键安全或恢复项无法满足时，不进入 Phase 6，但不推翻 Demo-ready Core Research
Backend v1。

## 被否决的方案

- 自研通用 Agent Loop：控制力高，但范围和维护成本不适合个人项目。
- 在业务层直接依赖 Deep Agents 类型：初期代码更少，但会污染 Domain 和持久化契约，使 SDK 升级与替换成本过高。
- 在 Demo-ready Core v1 阶段提前接入：会分散 Phase 1–4 的确定性产品闭环，因此选型现在记录，实现
  仍延后到 Phase 5。
