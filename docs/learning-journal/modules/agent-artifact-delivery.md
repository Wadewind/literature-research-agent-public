# Agent 输出 Artifact 交付

## 模块解决的问题

Deep Agents 可以在 Session 专属 OpenSandbox 中写文件，但 Sandbox 文件、WorkspaceSnapshot 和 Runtime
返回的 Candidate 都不是可直接下载的产品事实。本模块建立一条显式出口：模型调用固定
`submit_artifact`，平台重新读取并校验文件，Turn 业务成功时才发布独立、不可变的 `AgentArtifact`。
现有 Review Artifact 继续绑定 ReviewRun，没有被泛化或复用为 Agent 聚合。

## 边界和执行流程

```text
真实 Sandbox Turn
  → submit_artifact(path, name, media_type)
  → Runtime permit + owner/Project/Session/Turn + Sandbox generation/fence
  → 事务外读取 /workspace/outputs/ 普通文件
  → 扩展名/MIME/magic/结构/10 MiB/hash 校验
  → 事务外写内容寻址 staging Storage
  → 短事务登记 VALIDATED Candidate 与安全 Event
  → Runtime 返回并进入 Turn 成功提交
  → 同一业务短事务 CAS 为 COMMITTED、创建 AgentArtifact、提交 Message/Event/Run
  → owner-scoped API 列出或回读校验后下载
```

Fake Runtime 原有 descriptor 仍可形成 `STAGED` Candidate，用于离线业务测试；没有经过 Sandbox 文件读取与
Storage 校验时不会伪造正式下载资源。`ResearchAgentRuntime` 继续保持五方法，LangChain `ToolRuntime` 与
Deep Agents Tool 只存在于 infrastructure adapter。

## 状态、数据模型和事务

- Candidate 状态机只有 `STAGED → VALIDATED → COMMITTED` 与 `STAGED → REJECTED`；非法跨越由 Domain
  拒绝，数据库 CHECK 同时约束每种状态允许出现的 tool/storage/generation/fence/rejection/timestamp 字段，
  防止绕过领域对象写入自相矛盾的行。
- `candidate_id` 由 Turn + `tool_call_id` 稳定生成；正式 `artifact_id` 由 Candidate 稳定生成；数据库用
  Turn/tool-call、Candidate、Storage key 唯一约束和 Candidate CAS 收敛重复调用与响应丢失。
- `AgentArtifact` 独立保存 owner/Project/Session/Turn、名称、媒体类型、内容 hash、大小和内部 Storage
  key。公开 DTO 不返回 Storage key 或 Sandbox path。
- Sandbox download、内容扫描、Storage write/read 都在数据库事务外；VALIDATED 登记和 Turn 成功发布各自
  只提交小型事实。Storage 成功而数据库失败只留下不可见、稳定 key 的 staging blob。
- Turn 成功事务先发布本轮 VALIDATED Candidate，再提交 Assistant Message、Event 与 Run CAS；任一步失败
  会整体回滚，不把 Runtime success 当作业务 success。

## 文件和下载策略

首版单文件上限与 Workspace 单文件限制一致，为 10 MiB。支持：

- PNG、JPEG：magic/基本结构校验，可通过同一受权 content API内联预览；
- SVG：XML 结构校验，并拒绝 script、foreignObject、事件属性、外部引用、DOCTYPE/ENTITY 等主动内容；
  UI 不内嵌，默认下载；
- PDF：校验 `%PDF-` 与尾部 EOF，默认下载；
- CSV、Markdown、纯文本、JSON：必须是 UTF-8、不得含 NUL；CSV/JSON 再做结构校验，默认下载。

归档、可执行文件、宏文档、动态依赖产物、目录、symlink/device、路径穿越和 `/workspace/outputs/` 外路径
均拒绝。下载每次重新校验 Storage bytes 的 size/hash，设置安全 `Content-Disposition`、`nosniff` 和
`no-store`。

## 失败、重复和取消

- 永久文件错误形成仅含稳定错误码的 `REJECTED` Candidate/Event，不保存文件正文或原始路径。
- 取消、旧 Runtime permit 或旧 Sandbox generation/fence 在任何拒绝事实和 Storage write 之前检查，写入
  前后再次检查；迟到结果不能发布正式 Artifact。
- `RuntimeExecutionControlError` 保留平台分类：取消映射为 `CANCELLED`，显式 temporary 映射为
  `TEMPORARY`，其他 fence/权限错误映射为 `PERMANENT`，不会被降级成通用可重试 Tool 错误。
- 重复 `tool_call_id` 回读同一 Candidate；同 ID 不同内容冲突，不能覆盖已验证/提交事实。
- Storage 写入成功而 Candidate 事务失败时，重试复用相同内容寻址 key；孤儿 blob 后续由 GC 清理。
- Candidate 永远没有下载 API；跨 owner、Project、Session 或 Turn 的正式 Artifact 查询表现为 not found。

## 安全与可观测性

`agent_artifact_validated`、`agent_artifact_committed`、`agent_artifact_rejected` Event 只保存 Candidate ID、
hash、大小、媒体类型或安全错误码。API 和 Event 不暴露 Sandbox 路径、Storage key、文件正文、Prompt、
Secret 或 Tool 原始输出。

Sandbox Adapter 会先读取文件清单并拒绝静态 symlink/device，再下载并复核声明大小；当前实现不宣称在恶意
并发写入下消除了所有 TOCTOU 竞争，也不是生产级恶意内容扫描器。正式环境仍需要不可跟随 symlink 的原子
文件句柄/Provider 能力、隔离扫描、staging 总量配额与 GC。

## 重要测试和实际结果

2026-08-28 实际运行：

- Domain/Application 非数据库核心：59 passed（其中提交服务 4 passed）；
- AgentTurnExecutor 与 Alembic upgrade/downgrade PostgreSQL 测试：24 passed；
- Artifact API owner/download 完整性测试：9 passed；
- Sandbox Tool、Deep Agents 与真实 Sandbox Runtime Adapter 离线测试：60 passed；
- 完整后端非 integration 回归：1005 passed、5 skipped；
- Web 全量 Vitest：143 passed（其中 API client 与成果组件定向 17 passed）；Vite TypeScript build 通过。

普通测试没有访问真实模型、公共网络或付费 Sandbox；本切片没有运行真实 OpenSandbox Artifact Smoke。

## 代码入口

- Domain：`domain/agent_artifact.py`、`domain/research_agent.py`
- Application：`application/agent_artifact_service.py`、`application/agent_artifact_publisher.py`
- Ports：`application/ports/agent_artifact_source.py`、`application/ports/agent_artifact_publisher.py`
- Adapter：`infrastructure/agent/artifact_tools.py`、`infrastructure/persistence/agent_repository.py`
- API：`api/agent_sessions.py`
- Web：`web/src/components/AgentArtifactList.tsx`
- Migration：`f3a6c8d1e2b4_add_agent_artifacts.py`

## 已知限制

- staging blob 的孤儿 GC、Session/Turn 总量配额和正式恶意文件扫描尚未实现；
- SVG/PDF 只下载不预览；没有 PDF 深度解析、杀毒或内容无害化；
- 当前 regular-file 检查不是 race-hardened scanner；
- 没有运行真实 OpenSandbox + 真实 Provider 的“画图并下载”Smoke；
- Artifact 尚未自动注入后续 Turn 的 ContextSnapshot，该输入路径属于后续产品整合。

## 60 秒面试说明

我没有把 Agent Sandbox 中的文件直接暴露给用户，也没有复用 Review Artifact 表。模型只能在真实 Sandbox
Turn 中调用固定 `submit_artifact`；平台用 Runtime 与 Sandbox 两层 fence 校验 scope，在事务外读取文件、
检查类型和 hash 并写 staging Storage。Candidate 用稳定 Tool call ID 从 STAGED 推进到 VALIDATED，只有
Turn 成功的同一数据库事务才 CAS 为 COMMITTED 并创建不可变 AgentArtifact。查询沿
owner→Project→Session→Turn 闭包授权，下载再次校验 size/hash；Fake descriptor、失败 Turn 和 Candidate
都不可下载。这用 Effectively Once 和明确事务边界解决了响应丢失、重复调用和“外部成功不等于业务成功”。
