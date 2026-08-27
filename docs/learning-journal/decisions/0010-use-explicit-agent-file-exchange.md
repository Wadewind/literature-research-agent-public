# ADR-0010：采用显式 Agent 文件交换与 Artifact 提交协议

- 状态：已接受
- 日期：2026-08-28
- 决策者：项目维护者

## 背景

Phase 5 已让 Agent 在 Session OpenSandbox 的 `/workspace` 中使用文件工具和 `execute`，并能用固定
Python/numpy/pandas/matplotlib 生成研究笔记、表格和图片。成功 Turn 会把内部工作文件保存为
WorkspaceSnapshot，但 WorkspaceSnapshot 是 Runtime 恢复资源，不是用户可见下载资源。

当前 `agent_artifact_candidates` 只保存 Fake Runtime 返回的 staged descriptor；Real Deep Agents Runtime
只从 Checkpoint 收集回答和 Evidence，不会把 Workspace 文件转换为 Candidate。现有 `artifacts` 表和下载
API又强绑定 ReviewRun，不能直接承担 AgentSession 文件交付。Agent Chat 也没有附件上传入口。

## 决策

### 输入、内部文件和输出分层

```text
AgentAttachment     用户显式上传、授权给 Session/Turn 的输入
WorkspaceSnapshot   Agent 跨 Turn 继续工作所需的内部文件，不默认展示
AgentArtifactCandidate  submit_artifact 已暂存但尚未随业务 Turn 提交的候选
AgentArtifact       通过平台校验、绑定 owner/Project/Session/Turn 的可下载正式产物
```

不扫描整个 `/workspace` 自动发布文件。只有用户显式附件和 Agent 显式调用 `submit_artifact` 的路径跨越产品
边界。Review Artifact 的既有表、状态和下载 API 保持不变；Agent 使用独立的 AgentArtifact 聚合和表，
两者复用 Storage Port、内容哈希和公开 DTO 语义，不在本切片重构成熟的 Review Workflow。

### Agent 输出采用 `submit_artifact`

- 平台向 Deep Agent 提供固定、SDK-neutral 的 `submit_artifact(path, name, media_type)` Tool；首版只接受
  `/workspace/outputs/` 下的普通文件，不接受目录、symlink、device、路径穿越或用户构造的宿主路径；
- Tool 在业务数据库事务外从当前 fenced Sandbox Lease 重新读取文件，校验 owner/Project/Session/Turn、
  regular file、大小、扩展名、声明 MIME、magic bytes 和 SHA-256；允许类型首版为 PNG、JPEG、SVG、PDF、
  CSV、Markdown、纯文本和 JSON，可执行文件、宏文档、未知归档和动态依赖产物拒绝；
- 内容以 owner/Session/Turn/hash 派生的稳定 staging key 写入 Storage，再以稳定 ToolExecution/candidate ID
  在短事务中登记 Candidate。响应丢失或重复 Job 回读相同事实，不重复执行或创建文件；
- Candidate 生命周期为 `STAGED → VALIDATED → COMMITTED`，永久非法进入 `REJECTED`。只有业务 Turn 成功
  的同一短事务才能创建不可变 AgentArtifact 并发布下载可见性；取消、引用校验失败或业务 CAS 失败留下
  的 staging 内容不可见，交给 GC；
- 受支持、仅写入当前 Project 的低风险产物首版无需逐文件审批。覆盖既有 Artifact、外部发布、未知类型或
  对外副作用仍禁止或进入后续 Approval；Artifact 永不原地覆盖，以新 ID/版本表达新输出。

### 用户输入采用 Attachment ID

- 新增 Session-scoped 上传 API，先把文件保存到业务 Storage 并创建 owner/Project/Session 范围的
  AgentAttachment；发送消息时只引用当前 owner 可见的 `attachment_id`，不接受物理路径；
- 每轮 ContextSnapshot 固化附件 ID、内容哈希、大小、媒体类型和版本。Runtime 在事务外把本轮显式附件
  物化到 `/workspace/inbox/<opaque-id>/<safe-name>`，Agent 只看到受控路径和必要元数据；
- 首版附件沿用受限类型和大小策略；项目论文优先继续通过 Project Library/Paper Version 授权，不把论文
  全文复制进消息或 Graph State；
- Playwright 的 `browser_file_upload` 继续关闭。后续若需要向网页上传，只能新增引用 Attachment ID 的平台
  包装 Tool，不能让模型选择任意 Workspace/宿主文件。

## 实施顺序

1. **输出契约与迁移**：Candidate 状态机、AgentArtifact、唯一约束、Storage key、下载授权和 GC 边界；
2. **Fake/Real `submit_artifact`**：先用 Fake Sandbox 做失败测试，再接当前 OpenSandbox Backend、
   ToolExecution、取消和 fence；让 Real Runtime 能稳定收集/对账当前 Turn 的 Candidate；
3. **输出 API/UI**：Turn 查询返回可见 Candidate/Artifact；右侧成果区支持图片预览、元数据和下载；
4. **输入附件**：上传/删除/消息引用、ContextSnapshot 冻结、`/workspace/inbox` 物化和 Agent Chat 上传 UI；
5. **端到端验收**：用户上传 CSV → Agent 在 Sandbox 生成 PNG → `submit_artifact` → Turn 成功 → 刷新后
   仍可预览和下载；重复 Job、响应丢失和取消不产生重复正式 Artifact。

输出下载闭环优先于输入附件，因为它可以先完成“让 AI 画图并交付下载”的直接用户故事，并且完全使用
已有 Project Context 或 Sandbox 生成数据。

## 后果

正面影响：用户能明确区分附件、内部 Workspace 和正式成果；Agent 不会误发布缓存、Cookie、临时脚本或
大型下载；内容哈希、稳定 ID 和条件提交延续现有 Effectively Once 设计。

代价与风险：需要新增 AgentArtifact 数据模型、迁移、下载 API、staging GC 和真实 Runtime Candidate
收集；文件 MIME/内容扫描需要固定实现；Storage 成功而数据库失败仍可能留下可回收的孤立 blob。

## 被否决的方案

- **把 WorkspaceSnapshot 直接列给用户下载**：会暴露内部笔记、缓存、登录状态或临时数据；
- **Turn 结束时自动发布 `/workspace` 全部文件**：无法区分意图，也扩大敏感文件泄漏面；
- **复用强绑定 ReviewRun 的 Artifact 表而立即泛化全部 Core 代码**：会把 Agent 增量变成高风险迁移；
- **只在 Assistant Message 中返回 Sandbox 路径**：路径不是持久授权资源，Sandbox 轮换后失效；
- **直接开放 `browser_file_upload` 读取任意路径**：模型可能把未授权 Workspace 文件提交到外部站点。

## 验证门槛与非声明

- Domain/Application 测试覆盖状态机、稳定 ID、重复提交、取消、业务提交失败、跨 owner/Project/Session、
  非法路径、symlink、MIME/magic/hash/大小不一致和孤立 staging；
- Adapter 测试证明 Sandbox/Storage I/O 不发生在数据库事务内，旧 generation/fence 不能提交文件；
- API/UI 测试证明未提交 Candidate 不可下载，下载再次校验 owner/Project，刷新后链接稳定；
- 普通测试完全离线；真实 OpenSandbox 测试显式启用且不访问公共网络或付费服务；
- 生成并下载一张 PNG 只证明文件交付闭环，不证明恶意文件扫描、公共下载来源或生产 Storage 已完成。
