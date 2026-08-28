# Research Agent 精简安全契约

> 状态：Phase 6 Slice 1–6 已确认；Slice 7–8 的实施契约。
>
> 基线：`b0ec3f4`（2026-08-28）。本文把已经由 Phase 5 代码/测试证明的事实与 Phase 6 目标事实分开；
> “目标”“必须”“验收”不表示对应能力已经实现或经过真实环境验证。

## 1. 用途与适用范围

本文落实 [Phase 6 Spec](../learning-journal/phases/phase-06-research-agent-security.md)、
[ADR-0011](../learning-journal/decisions/0011-adopt-phase-06-lean-delivery.md) 的精简交付与
[ADR-0012](../learning-journal/decisions/0012-use-sandbox-public-egress-profile.md) 的 Sandbox 公网范围，供
后续代码切片直接实现和审查。它只适用于本地、单人演示的 Project-scoped Research Workspace Agent，
不是公网多租户、生产级安全浏览器或通用 Coding Agent 的安全声明。

规范词语含义如下：

- **已实现事实**：当前代码、迁移和既有测试能够直接证明的事实；
- **目标事实**：后续指定切片完成后必须成立的产品或安全行为；
- **直接拒绝**：不进入等待审批，不扩大策略，以稳定、安全且不含底层详情的错误结束该动作；
- **事务外 I/O**：模型、Runtime、MCP、Browser、DNS、HTTP、Sandbox、Storage 文件传输均不占用业务
  数据库事务；事务只提交已取得并校验的小型业务事实；
- **Effectively Once**：使用稳定 ID、唯一约束、内容哈希、条件更新、fence 和 reconcile 收敛业务结果，
  不宣称外部系统或分布式执行 Exactly Once。

## 2. 基线事实与契约缺口

### 2.1 当前已经实现并有既有测试覆盖

| 领域 | 当前事实 |
|---|---|
| Session/Turn | `AgentSession` 绑定 owner/Project；一条用户消息创建一个 `AgentTurnRun`；同一 Session 只有一个活动 Turn |
| Runtime Port | `ResearchAgentRuntime` 固定为 `execute_turn`、`resume_turn`、`cancel_turn`、`reconcile_turn`、`collect_turn_result` 五个方法；只使用项目 DTO，不暴露 Deep Agents/LangGraph 类型 |
| Runtime 映射 | Session 与 opaque SDK Thread binding 对应；Turn 与 opaque Runtime execution/checkpoint binding 对应；已有 `RuntimeExecution` lease/fence 和跨进程 reconcile-first 恢复 |
| 产品上下文 | `ContextSnapshot` 固化 owner/Project/Session/Turn、消息水位、精确 ChunkSet 引用、明确选择的 `ReviewOutput.output_id`、既有 Artifact 引用和本轮明确授权的 Attachment 版本/hash；不保存正文或 SDK State |
| 产品策略 | `PolicySnapshot` 固化 Tool/Skill/MCP 引用、网络/Sandbox 开关、`approval_required`、模型/工具次数；workspace Profile 为 `network_enabled=false`、`sandbox_enabled=true`、`approval_required=false` |
| Project Tool | 两个固定只读 Project Context Tool 已按 Turn/Context/Policy 作用域读取精确 ChunkSet 与指定 Evidence Matrix，并使用 `ToolExecution` 记录参数 hash、状态与有界结果 |
| Sandbox | 每个 Session 最多一个短 TTL OpenSandbox Lease；记录 owner/Project/Session、holder Turn、generation 和 fencing token；不跨 owner/Session 共享。过期/脏/关闭 Session 使用 fenced `RETIRED` 与 cleanup fact 做事务外幂等回收 |
| Workspace | `WorkspaceSnapshot` 有 `STAGED/STABLE`，只允许 STABLE 恢复；上限为 128 个文件、单文件 10 MiB、总计 50 MiB；内容在 Storage，元数据在 PostgreSQL |
| MCP/Skill | 固定 MCP Catalog/Profile、Tool 名称与 Schema hash、owner/Session 选择、调用拦截与 `ToolExecution` 已存在；平台 Skill 与 owner-scoped 声明式 Skill 使用不可变版本/hash 和只读 `/skills/` Backend |
| Browser Spike | 固定 Playwright MCP 能连接同一 Sandbox Chromium，在禁网环境操作合成页面并下载到同一 Workspace；未提供面向用户的画面或控制权 |
| Browser 人工控制 | 独立 BrowserControlLease、Turn 边界互斥、短时票据、固定 Server Proxy/websockify 平台代理和 noVNC 右栏组件已完成离线闭环；修正镜像的 RFB 与同一 Sandbox Playwright 合成页完整 trusted-local Smoke 已通过 |
| Agent Artifact | `AgentArtifactCandidate` 已实现 `STAGED → VALIDATED → COMMITTED` 与 `STAGED → REJECTED`；独立不可变 `AgentArtifact` 只由 Turn 成功事务发布。真实 Sandbox Turn 固定装配 `submit_artifact`，Fake Runtime 描述符仍只停留在 `STAGED`；单文件上限为 10 MiB |
| Agent Attachment | owner/Project/Session scoped 不可变 `AgentAttachment(version=1)` 已实现上传、列表、受限删除、消息有界有序引用、`agent-context.v2` 冻结和事务外 fenced `/workspace/inbox` 物化；已引用附件不可删除 |
| API/Event | 已有 Session/Message/Turn、MCP/Skill 配置和通用 Run cancel/Event API；Event 只保存筛选后的 Agent/Tool 生命周期摘要 |

这些事实不证明 OpenSandbox 是生产级隔离，不证明 Sandbox public-egress、公共下载或公网多租户安全已经
实现。Slice 2 的正式 Agent Artifact 与 Slice 4 的 AgentAttachment 都是本地受限文件能力，不代表已完成
生产级恶意文件扫描、隔离检疫、无 TOCTOU 竞争或 Storage GC；
Slice 3 的真实 Smoke 只证明未配置 API key/secure runtime 的 trusted-local OpenSandbox 功能链路，不是
noVNC 人工键鼠 UI E2E、通用认证或公网安全代理结论。

### 2.2 Phase 6 目标与责任切片

| 能力或尚缺目标 | 责任切片 |
|---|---|
| 独立 `AgentArtifact`、Candidate 完整生命周期、`submit_artifact`、预览和下载 | Slice 2（已完成） |
| `BrowserControlLease`、鉴权画面代理和跨 Turn 人工控制 | Slice 3（已完成；trusted-local RFB/同 Sandbox Playwright 合成页 Smoke 已通过；noVNC 人工输入 UI E2E 留到 Slice 8） |
| `AgentAttachment`、上传/删除、消息引用、ContextSnapshot 冻结与 `/workspace/inbox` 物化 | Slice 4（已完成） |
| 固定能力 Schema/hash 漂移拒绝、完整调用前 scope 检查、脱敏 Tool 摘要和硬预算 | Slice 5 |
| 实测资源限制、TTL/清理补偿、Workspace 重建与覆盖全部 Sandbox 进程的 default-deny egress | Slice 6（已完成；Docker overlay 物理磁盘硬配额除外） |
| 版本化 Sandbox public-egress、private/metadata/宿主/LAN 拒绝、正式资源校验/来源和 Prompt Injection 验证 | Slice 7 |
| App Shell、完整 UI/E2E、故障测试、评测、运行文档和完成复盘 | Slice 8 |

未完成切片不得为了方便把目标改写成已有能力，也不得用 Mock/配置字段存在代替真实强制效果。

## 3. 所有权、身份与信任边界

### 3.1 稳定所有权链

```text
可信 ActorContext.owner_id
  └─ Project.project_id
       └─ AgentSession.session_id
            ├─ SDK Thread binding（opaque，Runtime 内部）
            ├─ Logical Workspace（业务命名空间）
            ├─ SandboxLease generation/fence（物理环境）
            ├─ BrowserControlLease（当前 generation 的人工控制权）
            ├─ AgentAttachment（输入业务事实）
            └─ AgentTurnRun.turn_run_id
                 ├─ User/Assistant AgentMessage
                 ├─ ContextSnapshot / PolicySnapshot
                 ├─ Runtime Execution binding + lease/fence
                 ├─ ToolExecution
                 ├─ WorkspaceSnapshot
                 ├─ AgentArtifactCandidate
                 └─ AgentArtifact（正式输出）
```

- owner 只能来自可信认证上下文，不接受请求体 owner；Project、Session、Turn 和文件资源均沿该闭包查询；
- `AgentSession : SDK Thread = 1:1` 是逻辑映射；binding 损坏或升级可增加 generation，但旧 binding 保留审计；
- `AgentTurnRun : SDK Execution = 1:1`；一次 Worker 重试不能创建第二个逻辑 Execution 或把同一用户消息
  再次追加到 Thread；
- 一个 Session 同时最多一个活动 Turn；正常后续 Turn 复用同一 SDK Thread，只追加本轮新消息；
- `ContextSnapshot.history_through_sequence` 是审计/重建水位，不是每轮重放完整消息历史的指令；
- 逻辑 Workspace 属于 Session；物理 `/workspace` 属于当前 Sandbox generation。Sandbox ID、endpoint、
  CDP/VNC/MCP 地址都不是业务资源 ID；
- `WorkspaceSnapshot` 是内部恢复资源，不是用户文件列表或下载授权；
- Agent 输出只有在成为 `AgentArtifact` 后才是用户可见、可下载的业务事实；Review Artifact 继续绑定
  `ReviewRun`，不能被静默泛化或冒充 Agent Artifact。

### 3.2 信任区

| 区域 | 信任级别 | 可以持有 | 不得成为唯一事实来源 |
|---|---|---|---|
| PostgreSQL 业务层 | 业务事实来源 | 当前已有 owner/Project、Session/Message/Run、Context/Policy/Workspace Snapshot、Event、ToolExecution、Review Artifact、Candidate 生命周期、独立 AgentArtifact、BrowserControlLease 与 AgentAttachment；Slice 5 后增加 Agent 硬预算/Usage 事实 | SDK 对话状态、物理 Sandbox 文件 |
| API/Application | 可信策略执行者 | ActorContext、授权闭包、短事务和安全 DTO | 模型自行声明的 owner/Project/权限 |
| ARQ Worker/Runtime Adapter | 受信代码、外部结果不可信 | Provider Secret 的最小使用、opaque binding、SDK client | Runtime 成功不能直接等于业务成功 |
| Deep Agents/LangGraph | Runtime 内部状态 | Message、摘要、Checkpoint、内部文件卸载 | 业务权限、Run/Event、正式 Artifact |
| Session OpenSandbox | 不可信执行区 | 当前 generation 的 Workspace、Chromium、固定 MCP 进程和 `execute` | Secret、宿主路径、业务所有权、正式文件 |
| MCP/Browser/网页/arXiv | 外部不可信 | 有界响应和公开内容 | Tool 描述、网页文本或 Redirect 不能扩大策略 |
| Storage | 不可信字节存储、受信 Adapter 寻址 | 当前已有 WorkspaceSnapshot/Review Artifact、Agent Artifact staging 与 Attachment 内容寻址 blob | 元数据授权与业务可见性 |
| Web UI | 不可信客户端 | 当前已有业务 ID/公开 DTO与只驻留内存的短时 Browser view ticket | owner、raw endpoint、Sandbox path、策略配置 |

## 4. 威胁模型

### 4.1 需要保护的资产

- 当前已有的 owner 私有论文、Chunk、Evidence、Review Matrix、消息、Review Artifact、WorkspaceSnapshot、
  Candidate、正式 AgentArtifact 与 AgentAttachment；
- PostgreSQL、Valkey、Storage、宿主文件、Docker Socket、云元数据和内部管理 API；
- 模型、MCP、OpenSandbox、Storage 和 Browser 代理使用的 Secret；
- SDK Thread/Checkpoint、Sandbox generation、Workspace 与登录态的跨 Session 隔离；
- 当前已有 Run/Attempt/Event/ToolExecution 的完整性，以及取消边界；Slice 5 目标中的 Agent 硬预算/
  Usage 事实及其记账完整性；
- 正式 AgentArtifact 内容、元数据、可见性和下载授权；Candidate 仍不能被视为可下载资源；
- 文件内容、来源、MIME、hash、可见性和下载授权；
- CPU、内存、PID、磁盘、网络、Token、Tool Call 和墙钟等有限资源。

### 4.2 攻击者与不可信输入

首版至少假设以下输入可能恶意或错误：用户消息、owner-authored Skill 文本、上传文件、Project 论文正文、
Evidence 文本、模型输出、Tool 参数、MCP Tool 描述/Schema/结果、网页正文、HTTP Header、URL/Redirect、
DNS 结果、下载文件名/MIME/字节、Sandbox 生成文件和迟到的 Provider 响应。

攻击者可以是误操作用户、恶意文献/网页作者、被 Prompt Injection 影响的模型、漂移或被替换的 MCP、
失效 Worker、持有旧 fence/ticket 的请求，或同一部署中的另一 owner。首版不声称防御已取得宿主 root、
数据库管理员权限或 OpenSandbox 控制面的攻击者。

### 4.3 入口与主要威胁

| 入口 | 主要威胁 | 强制边界 |
|---|---|---|
| Session/Message/配置 API | IDOR、跨 Project 引用、任意 Runtime/MCP 配置、重复提交 | ActorContext、scoped repository、extra-forbid DTO、稳定幂等键 |
| Project Context Tool | 模型伪造 scope、跨 Snapshot 检索、全文泄漏 | 服务端注入 scope、精确 ContextSnapshot refs、有界 Evidence、Citation 校验 |
| MCP/Skill | Schema 漂移、供应链替换、Prompt Injection、Secret 泄漏 | 固定 Catalog/version/hash、allowlist 投影、只读 Skill、调用前策略复核 |
| Sandbox `execute` | 宿主逃逸、资源耗尽、网络绕过、读取 Secret、生成危险文件 | 非 root 固定镜像、无宿主挂载、资源上限、public-egress/private-network 边界、显式文件提交 |
| Browser/URL | Prompt Injection、访问 private/metadata/宿主/LAN、下载炸弹、凭据外泄、意外 POST/表单/远端写入 | 非-loopback 出口的 L3/L4/FQDN private deny、正式 URL/source 拒绝 localhost/loopback、无平台 Secret、无外部写专用 Tool、正式资源文件策略；不宣称协议级只读 |
| Browser 人工控制 | 跨 Session 画面、旧 generation 控制、人/Agent 竞争、凭据落库 | BrowserControlLease、短时代理票据、generation/fence、Turn 边界互斥 |
| Attachment/Artifact（均为已实现的本地受限业务事实） | 路径穿越、symlink、MIME 欺骗、越权下载、重复发布 | opaque ID/path、regular-file 校验、magic/hash/大小、staging 和成功 CAS |
| 重试/恢复 | 同一消息重复追加、重复 Tool/Artifact、旧 Worker 晚到提交 | reconcile-first、稳定 binding/effect/candidate ID、唯一约束、fence/CAS |
| Event/日志/Trace | Prompt、全文、Secret、endpoint 或大输出泄漏 | 字段白名单、hash/计数/安全摘要、大小限制和诊断信息分层 |

## 5. 数据流与事务边界

### 5.1 创建 Turn

```text
短事务：验证 owner/Project/Session/ReviewOutput/ChunkSet/配置
  → 锁 Session 并确认没有活动 Turn和人工 Browser 控制
  → 创建 User Message、Run、ContextSnapshot、PolicySnapshot、Event、Outbox
  → 提交
Worker：事务外 resolve Runtime/MCP/Skill/Sandbox/Attachment
  → execute 或 reconcile/resume 同一 Runtime Execution
短事务：记录筛选后的进展或 ToolExecution 边界
业务成功短事务：当前提交 Assistant Message/Evidence/Candidate/AgentArtifact/Event/Run CAS；Slice 5 后
  增加 Agent 硬预算 Usage 事实
```

### 5.2 强制事务外的操作

以下操作不得发生在数据库事务中：模型调用、Deep Agents graph 执行、MCP discovery/call、DNS/HTTP、
Browser/CDP、OpenSandbox create/renew/destroy/execute/file transfer、Storage put/get/delete、MIME/magic/hash
扫描。允许在短事务前后分别读取/写入控制事实，但不得持有数据库锁等待外部 I/O。

外部成功与业务提交必须区分：

```text
Runtime SUCCEEDED != AgentTurnRun SUCCEEDED
Tool/MCP returned != ToolExecution SUCCEEDED
Sandbox file exists != WorkspaceSnapshot STABLE
Storage put succeeded != Candidate/Artifact committed
Browser operation completed != Event/Run committed
```

数据库提交失败时不得用外部成功覆盖业务状态；应通过稳定 ID 对账同一效果，或留下不可见 staging/孤儿
供补偿清理。

## 6. 精简 Capability Profile 决策矩阵

本阶段没有“可等待审批”的平台注册动作。平台注册能力的策略结果只有 `AUTO_EXECUTE` 或 `DENY`；
`PolicySnapshot`、Catalog 和 Infrastructure 约束必须同时允许，任一层不匹配即 fail closed。

`research-public-egress.v1` 的 `AUTO_EXECUTE` 只表示 L3/L4/FQDN 层允许连接正常公网，不表示 HTTP method
或 Browser/MCP/Shell 业务动作只读。平台不注册外部写专用 Tool、不提供平台凭据，并以系统策略要求 Agent
只做研究读取；raw 公网通道仍可能发送 POST、提交表单或触发远端写入，当前不能归入可强制的 `DENY`。

| 能力/动作 | 决策 | 前置条件 |
|---|---|---|
| 当前 ContextSnapshot 内 Project Paper/Chunk/Evidence/Matrix 只读 | 自动执行 | owner/Project/Session/Turn 闭包、精确版本引用、有界输出 |
| 正常公网 HTTP(S) transport | 自动执行（Slice 7 后） | `research-public-egress.v1`、统一非-loopback private/metadata/宿主/LAN 拒绝、当前 Lease Profile/hash、预算全部通过；Sandbox 内部 loopback 仅用于固定本地服务，不代表协议级只读 |
| Sandbox `execute` 与固定依赖绘图 | 自动执行 | 当前 Lease generation/fence/Profile、取消/预算预检、资源上限、无动态安装 |
| 受支持类型的新 Agent Artifact | 自动执行 | 当前 Project/Turn、显式 `/workspace/outputs/` 路径、校验、不可变新 ID、Turn 成功 |
| 用户通过 Attachment ID 提供受支持输入 | 自动执行 | 当前 owner/Project/Session、ContextSnapshot 冻结、受控 inbox 路径和文件校验 |
| 用户在两个 Turn 之间控制当前 Chromium | 自动执行 | 无活动 Turn、健康且 generation 一致的 Lease、短 TTL 控制权、无原始 endpoint |
| 平台或 Agent 持有/注入用户名、密码、Cookie、验证码、OAuth Token | 直接拒绝 | 不进入 Approval |
| 平台外部写专用 Tool/Workflow | 不注册/不支持 | 产品策略禁止，平台不提供凭据；raw Browser/Shell/MCP 可能发起等价网络请求，当前无协议级强制 |
| 非-loopback private/metadata/宿主/LAN 出口，或用户/MCP/网页修改网络 Profile/代理/DNS | 直接拒绝 | 不以用户确认放宽；正式 URL/source 另行拒绝 localhost/loopback 输入与解析结果 |
| 用户提交 MCP endpoint/URL/transport/command/env/版本/认证 | 直接拒绝 | 只能引用平台固定 Catalog |
| 用户提交 Tool 代码、可执行 Skill、二进制 Skill、系统 Prompt | 直接拒绝 | owner Skill 仅声明式 Markdown/text |
| 选择 Sandbox 镜像、挂载宿主路径、使用宿主 Shell/Python | 直接拒绝 | Sandbox 配置只由部署者固定 |
| 动态 `pip/npm/apt` 安装或其他动态依赖 | 直接拒绝 | 使用固定镜像依赖 |
| 覆盖/删除既有正式 Artifact、自动发布整个 Workspace | 直接拒绝 | 输出只能创建不可变新 AgentArtifact |
| `browser_file_upload` 任意 Workspace/宿主路径 | 直接拒绝 | 未来若开放，只能由 Attachment ID 包装 Tool |
| 多 Agent、子 Agent、跨 Project Memory、长期 Memory | 直接拒绝 | 首版子 Agent 上限为 0 |

### `PolicySnapshot.approval_required` 兼容规则

- 字段是已持久化历史契约，本阶段不删除、不迁移、不改名；
- Phase 6 精简 Profile 创建的新 Turn 固定 `approval_required=false`；这表示 Profile 中没有可审批的动作，
  不是“高风险动作也可自动执行”；
- 旧 Snapshot 按创建时的值保持可读和可审计，不批量回写；
- `WAITING_INPUT` 继续服务既有通用 Run/Review 能力，Phase 6 不用它模拟 Tool Approval；
- 将来提供正式外部写、副作用或凭据委托产品能力前，必须新增能在受控 Tool 层强制的业务 Approval 聚合、一次性 decision token、
  `WAITING_INPUT`/resume、过期、取消和 Event 审计；Deep Agents 内部 interrupt 不能成为唯一审批事实。

## 7. API 契约：当前与目标增量

### 7.1 当前已存在

- Session/Turn：创建/列出/查询 Project AgentSession，查询消息，提交消息，查询 AgentTurnRun；
- Attachment：上传、列出和删除当前 Session 未引用附件；Message 可携带有界有序 Attachment ID；
- 配置：查询平台 MCP Catalog、查询/更新 Session MCP Profile，查询/创建声明式 Skill 与版本、查询/更新
  Session Skill Profile；
- 通用 Run：查询、取消、Event 列表与 SSE；
- 当前 Turn DTO 可返回 Candidate 的安全元数据与生命周期状态，但 Candidate 不是正式下载资源。

客户端只能提交业务内容、`review_output_id`、有界 Attachment ID、幂等键和已公开的 Catalog/Skill 选择；不能提交 owner、
SDK Thread/Checkpoint、Sandbox、Workspace、raw MCP 配置、网络策略或 fence。

### 7.2 已实现与后续增量

| Slice | API | 必须保持的授权/隐藏边界 |
|---|---|---|
| 2（已实现） | `GET /api/v1/agent-turn-runs/{run_id}/artifacts`；`GET /api/v1/agent-artifacts/{artifact_id}/content` | 每次 owner/Project/Session/Turn 授权；Candidate 不可下载；不返回 storage key/Sandbox path |
| 3（已实现） | `POST/GET/DELETE /api/v1/agent-sessions/{session_id}/browser-control`；`WS /api/v1/agent-browser-controls/view` | HTTP 开始/查询/结束只使用业务 Session ID；仅开始返回短时 ticket 和平台 view URL；WS 用 subprotocol 传 ticket；不返回 VNC/noVNC/CDP/MCP/OpenSandbox endpoint |
| 4（已实现） | `POST/GET/DELETE /api/v1/agent-sessions/{session_id}/attachments`；Message 增加有界 attachment ID 引用 | owner 来自 ActorContext；不接受物理路径；已被历史 Turn 引用的输入不可篡改 |
| 5（已实现） | `GET /api/v1/agent-turn-runs/{run_id}/tool-executions` 与预算/Usage 投影 | 仅安全摘要、版本/hash、计数、状态/时长；不返回原始参数/结果/endpoint |
| 7 | `GET /api/v1/agent-turn-runs/{run_id}/manifest` | 只返回规范化来源元数据和验证状态，不返回网页/PDF 全文或内部请求凭据 |

若实现需要改变路径或 DTO，责任切片必须先更新本文和 Phase 6 Spec。不得为后续功能扩大五方法
`ResearchAgentRuntime`；文件、Browser 和治理通过 Application Port、Tool Adapter 和持久业务事实组合，
SDK 类型继续停留在 infrastructure。

## 8. Event 与敏感数据契约

### 8.1 当前白名单事实

当前 Agent/Application 已产生 `agent_message_accepted`、`agent_runtime_bound`、`agent_turn_succeeded`、
`agent_turn_cancelled`、`agent_artifact_staged`、`agent_artifact_validated`、`agent_artifact_committed` 和
`agent_artifact_rejected`，以及 `agent_browser_control_started`、`agent_browser_control_ended`、
`agent_browser_control_expired`。Tool 事件按现有两条实现链路区分：

- Project Context Tool 当前产生 `agent_tool_started`、`agent_tool_succeeded`、`agent_tool_failed`；
- MCP `ToolExecution` 当前产生 `agent_tool_started`、`agent_tool_completed`、`agent_tool_failed`；
- 两条链路共用名称不表示它们已经拥有统一的公开 Tool Detail DTO；Slice 5 才负责收敛脱敏摘要、拒绝和
  硬预算事件，不能把目标事件反写为 Phase 5 现状。

通用 Run/Attempt/Event 继续记录队列和执行历史。

### 8.2 事件增量归属

| Slice | 允许新增的业务事件 |
|---|---|
| 2（已实现） | `agent_artifact_validated`、`agent_artifact_committed`、`agent_artifact_rejected` |
| 3（已实现） | `agent_browser_control_started`、`agent_browser_control_ended`、`agent_browser_control_expired` |
| 4（已实现） | 不为上传泛化 Run-bound Event；`agent_message_accepted` 仅增加 `attachment_count`，不记录内容或物理路径 |
| 5（已实现） | 新 reservation 产生只含计数/上限的 `agent_budget_updated`；Tool/Policy 拒绝以稳定 Runtime 错误进入既有 Turn 失败事件链，不另存 raw 参数/结果 |
| 6 | `agent_workspace_created`、`agent_workspace_cleanup_requested`、`agent_workspace_cleaned` 及安全清理失败摘要 |
| 7 | public-egress Profile/拒绝摘要、正式资源校验与 Resource Manifest 提交结果；事件名称在实现前冻结 |

Event payload 只能包含稳定业务 ID、版本/hash、类型、状态、计数、大小、时长、策略错误码和安全说明。
Event、日志、Trace、ToolExecution 和公开 DTO 均不得保存或返回：

- 模型思考过程、完整 Prompt、Runtime Message/Graph State；
- 网页正文、论文/PDF 全文、完整 Chunk/Matrix 或大型 Tool 输出；
- Secret、Cookie、密码、验证码、Authorization Header、MCP 参数原文；
- VNC/noVNC/CDP/MCP/OpenSandbox/raw Storage endpoint；
- Sandbox 命令全文、脚本全文、stdout/stderr 全文或用户按键/点击；
- 未经处置的文件内容。

诊断需要底层详情时使用访问受限、短保留期且脱敏的运维日志；它不能替代 PostgreSQL 业务 Event。

## 9. 可靠性、取消与 Effectively Once

### 9.1 稳定身份与重复执行

- ARQ Job 只携带 `turn_run_id`；重复 Job 先读取业务 Run/Attempt 和 RuntimeExecution；
- 相同 Session/Turn 必须解析到同一 logical Thread/Execution；旧 binding generation 不得冒充当前绑定；
- Tool/MCP 使用稳定 effect/invocation ID 与 canonical args hash；同 ID 改 Tool/参数永久拒绝；
- Workspace 恢复只接受当前 Session latest `STABLE` Snapshot；`STAGED`、跨 scope 或旧 generation 不可恢复；
- Slice 2 Artifact 使用稳定 candidate/artifact/tool-call ID、内容 hash 和唯一约束；响应丢失回读同一事实，
  不重新发布；
- 外部只读 discovery 可以重复，但不能动态授权新 Tool；可能有副作用的动作若无法确认是否发生，应停止并
  对账，不能盲目重放。

### 9.2 fence 与晚到结果

每次 Runtime、Tool、Sandbox、Browser control 和文件提交都必须绑定当前 owner/Project/Session/Turn、
RuntimeExecution fencing token，以及适用时 Sandbox generation/fencing token。旧 fence、过期 ticket、
generation 轮换或 CAS loser 的结果只能用于清理/诊断，不能写 Assistant Message、稳定 Snapshot、正式
Artifact 或业务成功。

### 9.3 取消

- cancel 请求先在短事务写 `CANCEL_REQUESTED` 与 Event，再在事务外传播到 Runtime/Tool/MCP/Browser/
  Sandbox；
- 所有模型和 Tool 边界在发起前检查取消和 fence；取消成立后不得启动新的模型、MCP、Browser、HTTP、
  Sandbox 命令、Storage 发布或 Artifact commit；
- 已在途外部调用可能迟到；迟到结果不能把 Run 改为 SUCCEEDED，只能对账和回收；
- `cancel_turn` 返回的是 Runtime 状态，不是业务终态；业务 `CANCELLED` 仍由平台条件更新提交；
- 传播失败由 Attempt lease/reconciler 收敛，不把取消误作普通临时失败再次执行。

### 9.4 失败窗口

| 窗口 | 收敛要求 |
|---|---|
| Runtime 成功、响应丢失 | `reconcile_turn` + `collect_turn_result` 读取同一 Execution，不重新 execute |
| Tool 外部结果返回、账本未提交 | 先按 effect 对账；无法确认时 fail safe，不宣称 Exactly Once |
| Sandbox Snapshot 已上传、业务失败 | 保持 `STAGED` 不可恢复/不可见，后续 GC；不得推进 latest STABLE |
| Artifact blob 已 staging、Candidate/业务失败 | blob 不可下载，稳定 key 可重用，后续 GC |
| Artifact validated、Turn 成功 CAS 失败 | 不发布正式 Artifact；晚到处理不得覆盖 Run 终态 |
| Sandbox create 成功、Lease CAS 失败 | loser 只回收自己的候选 Sandbox；不得销毁 winner 当前 Lease |
| 过期/DIRTY/Session closed Lease 被扫描 | 同一短事务以 session/generation/fence/due guard CAS 为 `RETIRED` 并写 cleanup fact；CAS 失败不调用 Provider |
| destroy 成功、响应或 DB 提交丢失 | cleanup 保持可重试；重复 destroy 的精确 404 视为成功，其他异常进入安全摘要重试 |
| Cleaner 与续租/轮换竞争 | 同 generation 续租不能复活 `RETIRED`；只有 generation+1 可替换，旧 cleanup 固定到资源哈希，绝不重读新 Sandbox ID |
| Browser view 断线 | 只释放 viewer connection；MANUAL Lease 保持 ACTIVE 并允许 TTL 内使用当前 ticket 重连，不改变 Browser 内容事实 |
| Browser control 显式结束、TTL 到期或 generation/fence 失效 | 控制权收敛到非 MANUAL；旧 ticket 按对应 revision/TTL/generation/fence 边界失效，不改变 Browser 内容事实 |

## 10. 后续切片安全门槛

### Slice 2：Agent 输出 Artifact（已完成）

- 先以 Domain/Application 失败测试冻结 Candidate 状态机、AgentArtifact 不可变性、稳定 ID/唯一约束；
- Sandbox/Storage I/O 全在事务外；路径、regular file、symlink、大小、扩展名、MIME、magic、hash、scope、
  generation/fence 全部校验；
- 取消、重复 Job/Tool、响应丢失、Storage 成功/DB 失败和旧 fence 不产生重复或越权正式 Artifact；
- 普通测试用 Fake Sandbox/Storage；本切片未运行真实 OpenSandbox Smoke。Adapter 的静态 regular-file/
  symlink/device 检查与传输后 size/hash 复核不构成无 TOCTOU 竞争的生产级恶意文件扫描；孤儿 staging GC
  与总量配额后移。

### Slice 3：Browser 画面与人工控制（已完成）

- PostgreSQL `BrowserControlLease` 独立于物理 SandboxLease，绑定 owner/Project/Session、anchor Turn、
  generation/fencing token、单调 revision、最长 5 分钟 TTL、ticket digest 和终态原因；每个 Session 由
  部分唯一约束限制一个 ACTIVE 控制权。该表只持久化 MANUAL；不存在 ACTIVE 记录即表示 Agent/idle，
  不创建 AGENT Lease；
- 开始控制必须已有 ACTIVE 且未过期的当前 Sandbox Lease，并且没有活动 Turn；打开画面不会创建、续租或
  轮换 Sandbox。ACTIVE 控制即使墙钟已过期但尚未 reconcile，也会阻止新 Turn；结束、过期和重复开始均
  幂等收敛；
- HTTP 只返回业务状态、短时 opaque ticket 和平台 view URL。ticket 通过 WebSocket subprotocol 传输，
  数据库只保存 SHA-256 digest；旧 ticket、终态、TTL、generation/fence、revision 或当前连接不匹配时
  fail closed；API 进程签名 key 变化会先终结旧控制权再发布新 revision。当前 WebSocket 从应用配置的
  `dev_actor_id` 构造本地 actor，再结合 bearer ticket 校验 owner/Session，仅证明单用户本地边界；尚未
  实现或验证通用认证上下文；
- Adapter 在数据库事务外启动 Sandbox 内固定 websockify recipe（`6080` WebSocket → loopback `5901`
  TigerVNC），再解析 OpenSandbox Server Proxy 的 `ws/wss` endpoint 与必需 headers，进行有帧大小、单向
  总量、idle、连接、总时长和周期 fence 边界的 WebSocket↔WebSocket 二进制转发。raw endpoint、VNC 数据、按键、页面、
  Cookie 和凭据不进入 DTO、数据库、Event 或日志；断线只释放单连接占用，不自动结束业务控制权；
- Web 使用精确版本 noVNC `1.7.0` 的动态 import，ticket 只进入 React 内存和 WebSocket subprotocol，不写
  URL、localStorage/sessionStorage 或日志。右栏显示 Agent/Manual、连接、断线、过期和旧 generation，
  组件不依赖待替换的全局壳层；
- 离线 Fake 通道、PostgreSQL 约束/并发和 Web 状态测试已通过。旧镜像诊断确认 Sandbox 内 TigerVNC
  1.15.0/RFB 可用，同时证明旧 `5901` OpenSandbox endpoint 指向 HTTP egress sidecar，不能直连 raw TCP；
  因此固定镜像 recipe 新增 websockify 0.13.0。仓库包含默认跳过、由
  `AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1` 显式启用的本地 OpenSandbox 合成页 Smoke，用于经 Server
  Proxy/websockify 的 RFB 握手与同一 Sandbox Playwright MCP 关联验证。修正镜像已重建，完整 Smoke
  `5 passed in 13.06s`；前三次失败形成 endpoint、代理和 readiness/Fixture 回归测试。该 trusted-local
  证据不覆盖 noVNC 人工键鼠 E2E、通用认证、API key/secure runtime、公网或跨 generation 登录恢复。

### Slice 4：Agent 输入附件（已完成）

- 已实现不可变 `AgentAttachment(version=1)`，固定单文件 10 MiB，每消息最多 5 个不重复引用并保留请求顺序。上传只接受 PNG/JPEG/SVG/PDF/CSV/Markdown/plain text/JSON，同时检查安全文件名、扩展名、声明 MIME、magic/UTF-8/JSON/CSV 结构；
- `POST/GET/DELETE /api/v1/agent-sessions/{session_id}/attachments` 的 owner 只来自 `ActorContext`，公开 DTO 不含 `storage_key`、Sandbox 路径或内容。上传 `Idempotency-Key` 稳定绑定 owner/Session/元数据/内容 hash；同请求并发收敛到同一事实和准确 replay 状态，不同请求复用键稳定 409。Web 以 name/size/type/lastModified 固定上传意图，响应丢失后重选同一文件复用键；
- 消息按附件 ID 稳定顺序获取 scoped `FOR UPDATE` 行锁并持有到 Message/引用/ContextSnapshot 提交；删除先获取同一行锁，再复核历史引用后标记 `DELETED`。因此消息与删除竞态只有“引用提交后删除 409”或“删除提交后消息 not-found”两个稳定结果；
- `AgentMessage` 仅保存有序 ID 引用；`agent-context.v2` 额外冻结 `id/version/hash/size/media_type/display_name`。消息幂等 hash 覆盖有序附件 ID，Event 只记录 `attachment_count`，文件内容和物理路径不进入 Message/Run/Event/Graph State；
- Runtime 在数据库事务外每轮先复核 owner/Project/Session/Run 取消状态、不可变事实与 Sandbox generation/fence，清空 inbox，再从 Storage 读取、重验 hash/类型并上传到 `/workspace/inbox/<attachment-id>/<safe-name>`。清空、读取、验证、上传、取消或 fence 任一失败都在模型/Tool 前 fail closed 并将环境标脏；
- `WorkspaceSnapshot` 暂存时排除 inbox，恢复时拒绝含 inbox 的旧/异常快照；Deep Agents 只看到受控路径和必要元数据。不开放 `browser_file_upload`，Project PDF 仍优先通过 Project Library/Index 提供；
- Storage 写入成功后 DB 提交失败可产生内部孤儿 blob；本切片保持不可见且可用同一幂等键重试，不声称已实现 Storage GC。

### Slice 5：固定能力、Project Context 与硬预算（已完成）

- `PolicySnapshot` 冻结全部允许 Tool 的 version/input schema hash，以及模型 8 次、Tool 12 次、首次 Runtime
  后 300 秒墙钟、固定 Tool/MCP 30 秒、`execute` 60 秒、单次 64 KiB 安全输出、同 Tool+args hash 最多
  2 个 invocation、每次约 60,000 输入 Token 与 Provider 2,048 输出 Token；snapshot hash 覆盖这些字段；
- 每个 Turn 同事务创建 `AgentTurnUsage`，模型使用稳定 ordinal reservation，Tool 使用稳定 tool_call ID；
  PostgreSQL 行锁、唯一约束和条件更新保证重放不重复计数/认领。Provider Usage 允许可空字段逐步
  `NULL → value`，总 Token/费用不可得时不做虚假硬拒绝；
- 模型 reservation 约束的是逻辑模型步骤。Provider 已接收请求后若响应或 Worker 在结果可判定前丢失，
  重试仍可能产生重复付费请求且 `usage_metadata` 可能缺失；checkpoint/reconcile 只能缩小和对账窗口，
  不保证物理 Provider 调用 Exactly Once，也不把逻辑步骤上限宣称为物理调用或精确费用硬上限；
- Runtime 对有限 canonical JSON args、实际 Tool schema、owner/Project/Session/Context/Policy、取消与
  fence 在外部调用边界 fail closed，并把剩余墙钟纳入 graph/model/tool timeout；外部 I/O 不在事务中；
- Project Context、MCP 与 `submit_artifact` 只有下层 stable effect cache 可对账时才进入 handler 重放；
  文件/`execute` 的 RUNNING 或终态 effect 禁止重执行；成功 hash/size 和失败安全错误冲突均拒绝；
- 公开 Tool API 与 `agent_budget_updated` Event 只含安全摘要。Workspace 50 MiB、单文件/Artifact 10 MiB
  沿用既有契约；下载数量/总量必须在 Slice 7 实现，不能由本切片宣称完成。

### Slice 6：Workspace/Sandbox 与统一 egress（已完成）

- `agent_sandbox_leases` 的内部状态为 `ACTIVE/DIRTY/RETIRED`。调度器只在同一短事务内以
  session/generation/fence 和 expired/DIRTY/session-closed 条件 CAS 退役旧 Lease，并插入
  `agent_sandbox_cleanups`；Provider `destroy`、`connect`、`inspect` 不进入事务；
- cleanup ID 包含 opaque resource hash，认领以 worker/attempt fence 条件完成或重试。失败只保存固定
  `provider_destroy_failed` 与安全摘要，不进入公开 API/Event，不保存命令、输出、endpoint 或 Secret；
- 轮换 Lease 与旧 cleanup 同事务提交，候选 CAS loser 先持久化自己的 cleanup 再销毁。OpenSandbox SDK
  的精确 HTTP 404 被 Adapter 视为幂等成功，其他异常必须保留给补偿重试；
- 项目固定 `opensandbox-server==0.2.2` 本地配置：仅 loopback 控制面、Docker bridge、无 host volume、
  drop capability ALL、no-new-privileges、PID 256、固定端口范围、固定 execd/egress image digest、API key
  必填；普通 compose 与用户 home 配置均不被修改；
- `OpenSandboxProvider` 固定 1 CPU、2 GiB、空 env/volume、非 root research image、命令上限 60 秒、inline
  输出 64 KiB，并提交 `NetworkPolicy(default deny, egress=[])`。OpenSandbox 0.1.15 的 execd timeout
  实测不会终止已启动命令，因此 Adapter 以固定镜像的 coreutils `timeout` 约束整个进程组，Provider
  timeout 只保留为多 2 秒的 RPC 等待上限。Server 0.2.2 在有 NetworkPolicy 时创建
  egress sidecar，Sandbox 共享其 network namespace，因此策略覆盖 Shell、Python、Node、Chromium、
  Playwright 和固定 Search MCP，而不依赖 Tool 参数过滤；
- 显式 `AGENT_RUN_OPENSANDBOX_SECURITY_TESTS=1` Smoke 已实际验证以上资源/Secret/挂载/网络边界、同
  Sandbox loopback 自有服务、命令超时后 Backend 仍可用、60 秒 TTL 自动回收和重复销毁。
  `chromium --no-sandbox` 只证明容器级统一
  egress，不证明 Chromium 进程级 Sandbox；
- 当前 Server Docker resource 请求只解析 CPU/memory/GPU，不支持请求级 overlay 物理磁盘硬配额。
  WorkspaceSnapshot 的 128 文件、单文件 10 MiB、总 50 MiB 与 Artifact 上限属于业务提交边界，不冒充
  物理磁盘隔离；本地 Server 未配置 secure runtime，不能据此声称生产隔离。

### Slice 7：Sandbox 公网与正式资源

- 固定 `research-public-egress.v1` 允许任意正常公网 HTTP(S)，不维护平台 URL Host allowlist 或逐次网络
  审批；保留 Sandbox network namespace 内部 loopback 供 CDP/MCP/websockify/VNC 与固定本地服务使用，
  统一 egress 必须在非-loopback 出口拒绝 private/link-local/unspecified/multicast/reserved、云元数据、
  宿主/LAN/容器控制面；raw Browser/execute 可访问同 Sandbox 服务但不能访问宿主 loopback；
- 正式 URL/source 输入不享受内部 loopback 例外，必须拒绝 `localhost`、loopback 字面地址与 DNS 解析到
  loopback 的 Host；
- Profile 只提供 L3/L4/FQDN 目标边界，不检查 HTTP method、body、表单或站点业务语义；平台不注册外部
  写 Tool/Workflow、不提供平台凭据并要求研究读取，但验收不得把 raw Browser/MCP/Shell 描述为只读；
- `PolicySnapshot` 冻结 Profile ID/version/hash，`SandboxLease` 保存相同 Profile/hash；Profile 变化、
  deny→public 或任意不匹配都必须轮换 generation，不能续租旧环境；
- 网络策略覆盖 Browser、MCP、Shell、Python、Node 和 curl。必须核对 pinned `opensandbox==0.1.15`、
  Server `0.2.2`、egress image `v1.1.4`/upstream commit 前缀 `34653f7` 的上游与本地行为；配置字段存在
  不能替代真实强制证据。上游 nft 规则已确认在 CIDR deny set 前固定 `accept` loopback interface；项目
  选择保留现有 CDP/MCP/VNC 拓扑，而不自研 egress 镜像。固定版本做不到 public allow + 非-loopback
  private deny 时停止实现；
- Browser/MCP/execute 下载可留在 `/workspace/downloads/`，但 raw Workspace 文件不是正式业务资源，
  不可直接由公开 API 下载；只有成为 `AgentArtifact`、Project 资源或平台已验证来源时，才检查数量/
  总量、超时、大小、扩展名、MIME、magic、hash 和来源；
- 外部网络、Sandbox 和 Storage I/O 不进入数据库事务；稳定 invocation/resource ID、effect ledger、唯一
  约束和 fence 收敛重复/响应丢失，不宣称 Exactly Once；
- 离线恶意 Fixture 全部通过后，才显式运行 Sandbox 内部 loopback、arXiv 页面/PDF、至少一个非 arXiv
  公网目标以及非-loopback private/metadata/宿主拒绝 Smoke。真实 Smoke 不进入普通 CI，不调用真实模型，
  不使用用户 Cookie，也不绕过
  CAPTCHA/付费墙/robots/授权；Smoke 只证明目标网络边界，不证明 HTTP 业务动作只读。

### Slice 8：产品整合

- 故障、取消、响应丢失、重复、跨 owner/Project/Session/generation、Prompt Injection 和预算 E2E 必须覆盖；
- UI 只消费平台业务 API，不直连 LangGraph Deployment/Thread State 或 Provider endpoint；
- 真实 Provider、OpenSandbox、arXiv Smoke 显式启用并记录精确版本、预算、耗时、结果与限制；
- 按 ADR-0009 完成本地 noVNC 真实人工输入 UI E2E：人在画面中操作后结束 MANUAL，下一 Turn 由同一
  generation 的 Playwright MCP 观察该页面状态；该验收不能由 Slice 3 的协议/RFB/合成页工具 Smoke 替代；
- 完成报告逐条区分离线证据、真实功能证据和未验证安全声明。

## 11. UI 强制契约

[Web UI 应用壳与视觉重设计](web-ui-app-shell-redesign.md) 是 Phase 6 UI 的强制契约：最终使用左侧固定
`AppSidebar`、≤56px 的 `PageBar`、桌面优先统一壳和浅色编辑风；问答/Research Agent 保持全高三栏与
工作区内部会话 rail。

- Slice 2–4 新增 Artifact、Browser、Attachment 组件必须壳层无关，不依赖旧全局 Header、
  `ProjectWorkspaceHeader`、`ProjectNav` 或固定 76px 顶栏；
- Slice 8 若 App Shell 尚未完成，必须先按其四个独立 UI 子切片实施，再整合 Evidence/PDF/Artifact tab、
  Browser、Attachment、Tool/Turn 信息；
- 实施轻页头时同步更新 `project-workspace-ui-contract.md` 被取代的共享 Project Chrome 条款；
- UI 不显示 Sandbox path、Storage key、raw endpoint、内部 Thread/Checkpoint、完整 Prompt/思考或未提交
  Candidate 下载入口。

## 12. 非声明与延期项

Phase 6 完成后仍不得宣称：

- 公网多租户、零信任部署、生产级恶意文件扫描、SLA、完整备份/灾难恢复已经完成；
- 支持 private/metadata/宿主/LAN、用户自定义网络策略、任意 MCP/Tool/Skill、动态依赖、通用 Coding
  Agent、多 Agent 或长期 Memory；
- 提供通用 Approval Center、OAuth/Credential Vault 或正式外部写/不可逆产品能力；raw 公网通道可能
  发起写请求属于已知风险，不得反向表述为已强制拒绝；
- OpenSandbox、容器、Prompt 或 Deep Agents `permissions` 单独构成完整安全边界；
- Runtime/Tool/Storage/Sandbox/Artifact 实现 Exactly Once；
- 人工登录态会跨 Sandbox generation 恢复，或平台能安全托管用户凭据；
- arXiv 下载自动成为 Project Paper/Evidence，或 Agent 输出等同经过人工审核的系统性文献综述；
- 合成页面、Fake MCP、Fake Model 或本地 Docker Smoke 能证明真实公网/Provider 的全部行为。

以下能力明确延期：完整 Registry/Catalog 管理后台、通用 Approval/Interrupt UI、OAuth/Credential、正式
外部写产品能力及其强制、能解析 HTTP/Browser 动作的通用 egress proxy、Browser 任意文件上传、Artifact
覆盖/删除、可执行 Skill、动态包安装、Sandbox 集群调度/
预热/扩缩容、精确计费、组织 RBAC、公网多租户、SLA 和完整容灾。

## 13. 审查清单

后续每个切片提交前，主审至少确认：

- 没有扩大五方法 `ResearchAgentRuntime` 或泄漏 SDK 类型；
- owner/Project/Session/Turn/Context/generation/fence 在平台侧验证，模型参数不能选择 scope；
- 外部 I/O 不在数据库事务中，外部成功与业务提交没有合并；
- 重复 Job、响应丢失和 Worker 崩溃不会重复追加消息、Tool effect 或正式 Artifact；
- 取消后不会发起新模型/Tool/MCP/Browser/Sandbox/Storage 发布；
- Event/日志/Trace/API 没有 Prompt、思考、全文、Secret、raw endpoint 或大型输出；
- 普通测试离线、确定性、零外部费用；真实 Smoke 明确 opt-in；
- public-egress 测试只据实声明目标网络允许/拒绝，不把 L3/L4/FQDN 结果写成 HTTP/Browser 协议级只读；
- 没有混入后续切片、无关重构、依赖升级或延期范围；
- 新 UI 组件遵循 App Shell 强制契约。

## 14. 依据

- [ADR-0005：交互式 Research Agent 会话模型](../learning-journal/decisions/0005-interactive-research-agent-session-model.md)
- [ADR-0007：OpenSandbox 可执行 Workspace](../learning-journal/decisions/0007-use-opensandbox-executable-workspace.md)
- [ADR-0008：Deep Agents 原生 MCP 与 Skills](../learning-journal/decisions/0008-use-native-mcp-and-skills-capabilities.md)
- [ADR-0009：跨 Turn 人工浏览器控制](../learning-journal/decisions/0009-use-turn-boundary-browser-control.md)
- [ADR-0010：显式 Agent 文件交换](../learning-journal/decisions/0010-use-explicit-agent-file-exchange.md)
- [ADR-0011：Phase 6 精简交付](../learning-journal/decisions/0011-adopt-phase-06-lean-delivery.md)
- [ADR-0012：Sandbox 公网 egress Profile](../learning-journal/decisions/0012-use-sandbox-public-egress-profile.md)
- [Agent Runtime 业务契约笔记](../learning-journal/modules/agent-runtime-contract.md)
- [Agent Sandbox Workspace 笔记](../learning-journal/modules/agent-sandbox-workspace.md)
- [Agent MCP 配置笔记](../learning-journal/modules/agent-mcp-configuration.md)
- [Agent MCP Browser/Search 笔记](../learning-journal/modules/agent-mcp-browser-search.md)
- [Agent Native Skills 笔记](../learning-journal/modules/agent-native-skills.md)
