# Agent 输出 Artifact 交付

## 模块解决的问题

Deep Agents 可以在 Session 专属 OpenSandbox 中写文件，但 Sandbox 文件、WorkspaceSnapshot 和 Runtime
返回的 Candidate 都不是可直接下载的产品事实。本模块建立一条显式出口：模型调用固定
`submit_artifact`，平台重新读取并校验文件，Turn 业务成功时才发布独立、不可变的 `AgentArtifact`。
现有 Review Artifact 继续绑定 ReviewRun，没有被泛化或复用为 Agent 聚合。

## 边界和执行流程

```text
真实 Sandbox Turn
  → submit_artifact(path, name, media_type, source_url?)
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

`submit_artifact` v3 把 `media_type` 固定为枚举 Schema，并在可纠正错误中返回允许的 MIME/扩展名清单。
成功工具结果使用 `validated_not_published`，明确它仍是等待 Turn 成功提交的内部 Candidate。

## 状态、数据模型和事务

- Candidate 状态机只有 `STAGED → VALIDATED → COMMITTED` 与 `STAGED → REJECTED`；非法跨越由 Domain
  拒绝，数据库 CHECK 同时约束每种状态允许出现的 tool/storage/generation/fence/rejection/timestamp 字段，
  防止绕过领域对象写入自相矛盾的行。
- `candidate_id` 由 Turn + `tool_call_id` 稳定生成；正式 `artifact_id` 由 Candidate 稳定生成；数据库用
  Turn/tool-call、Candidate、Storage key 唯一约束和 Candidate CAS 收敛重复调用与响应丢失。
- `AgentArtifact` 独立保存 owner/Project/Session/Turn、名称、媒体类型、内容 hash、大小和内部 Storage
  key。Phase 6 Slice 7 起可成对保存已规范化的声明来源目标 URL/hash；query 不入库，避免 Manifest 泄漏
  token/signature。公开 DTO 不返回 Storage key 或 Sandbox path。
- Sandbox download、内容扫描、Storage write/read 都在数据库事务外；VALIDATED 登记和 Turn 成功发布各自
  只提交小型事实。Storage 成功而数据库失败只留下不可见、稳定 key 的 staging blob。
- Turn 成功事务先发布本轮 VALIDATED Candidate，再提交 Assistant Message、Event 与 Run CAS；任一步失败
  会整体回滚，不把 Runtime success 当作业务 success。

## 文件和下载策略

首版单文件上限与 Workspace 单文件限制一致，为 10 MiB。支持：

同一 Turn 最多 8 个非 REJECTED Candidate、总量最多 50 MiB；锁定 Run 后串行复核，避免并发提交绕过
业务配额。`GET /api/v1/agent-turn-runs/{run_id}/manifest` 只返回正式 Artifact 的有界声明目标、文件 hash、
MIME、大小和创建时间；`declared_public_target_checked` 只表示 URL/DNS 公网分类通过，不是 provenance
证明。raw `/workspace/downloads`、Candidate 与 WorkspaceSnapshot 均没有公开下载入口。

- PNG、JPEG：magic/基本结构校验，可通过同一受权 content API内联预览；
- SVG：XML 结构校验，并拒绝 script、foreignObject、事件属性、外部引用、DOCTYPE/ENTITY 等主动内容；
  UI 不内嵌，默认下载；
- PDF：校验 `%PDF-` 与尾部 EOF，默认下载；
- CSV、Markdown、纯文本、JSON、Python 源码：必须是 UTF-8、不得含 NUL；CSV/JSON 再做结构校验，默认
  下载。
- Python 源码只接受 `.py` + `text/x-python`，始终以 `attachment` + `nosniff` 下载，不进入图片预览路径。

归档、可执行文件、宏文档、动态依赖产物、目录、symlink/device、路径穿越和 `/workspace/outputs/` 外路径
均拒绝。下载每次重新校验 Storage bytes 的 size/hash，设置安全 `Content-Disposition`、`nosniff` 和
`no-store`。

## 失败、重复和取消

- 永久文件错误形成 `REJECTED` Candidate/Event，不保存文件正文或原始路径；Candidate 只保留清理过控制
  字符与路径分隔符的尝试名称、声明媒体类型和稳定 `rejection_code`，用于 UI 诊断。
- 取消、旧 Runtime permit 或旧 Sandbox generation/fence 在任何拒绝事实和 Storage write 之前检查，写入
  前后再次检查；迟到结果不能发布正式 Artifact。
- `RuntimeExecutionControlError` 保留平台分类：取消映射为 `CANCELLED`，显式 temporary 映射为
  `TEMPORARY`，其他 fence/权限错误映射为 `PERMANENT`，不会被降级成通用可重试 Tool 错误。
- 重复 `tool_call_id` 回读同一 Candidate；同 ID 不同内容冲突，不能覆盖已验证/提交事实。
- Storage 写入成功而 Candidate 事务失败时，重试复用相同内容寻址 key；孤儿 blob 后续由 GC 清理。
- Candidate 永远没有下载 API；跨 owner、Project、Session 或 Turn 的正式 Artifact 查询表现为 not found。
- 可选 `source_url` 在 Sandbox/Storage I/O 前完成 HTTP(S) 规范化和 DNS 全回答公网校验；localhost、
  loopback、private、link-local、reserved、metadata 或混合 DNS 回答稳定拒绝。该校验不证明文件字节必然
  来自声明 URL，也不把 raw egress 变成协议级只读代理。
- 数量/总量在锁定同一 Run 后统计；第 9 项或超过 50 MiB 的事务回滚，不留下 Candidate 行。同一
  `tool_call_id` 重放直接回读已存在 Candidate，不重复计数。由于 Storage write 先于短事务，预算冲突仍
  可能留下不可见的内容寻址 staging blob，等待后续 GC。
- 路径、文件名、扩展名/MIME、文件不存在/非普通文件、大小和文件内容结构等无副作用且模型可纠正的
  Artifact 校验错误：平台仍保存 REJECTED Candidate 和失败 ToolCall，但向当前 Agent Loop 返回有界
  error ToolMessage，要求修正参数或文件后以新 Tool Call 重试；不会原样自动重放失败调用。系统提示同时
  明确固定扩展名/MIME 对照。取消、fence、权限、预算、持久化、文件并发变化和临时基础设施错误仍按原
  分类终止或进入 Run 级重试。

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
- Slice 7 来源/预算增量定向套件：146 passed；其中 PostgreSQL 并发预算测试覆盖第 8/9 项竞争、50 MiB
  超限回滚与同一 Candidate 重放不重复计数；Ruff 全后端通过，Pyright 为 0 errors。

普通测试没有访问真实模型、公共网络或付费 Sandbox；本切片没有运行真实 OpenSandbox Artifact Smoke。
2026-08-30 Real 缺陷回归另验证 outputs 目录初始化、路径错误同轮纠正和提示契约；相关 Infrastructure
完整组合为 134 passed。
2026-08-30 扩展名/MIME 缺陷回归进一步验证模型可纠正校验错误白名单、权限错误 fail-closed 和固定类型
提示；Artifact/Agent Runtime 相关组合为 116 passed，Ruff 与修改实现文件 Pyright 均通过。

## 代码入口

- Domain：`domain/agent_artifact.py`、`domain/research_agent.py`
- Application：`application/agent_artifact_service.py`、`application/agent_artifact_publisher.py`
- Ports：`application/ports/agent_artifact_source.py`、`application/ports/agent_artifact_publisher.py`
- Adapter：`infrastructure/agent/artifact_tools.py`、`infrastructure/persistence/agent_repository.py`
- API：`api/agent_sessions.py`
- Web：`web/src/components/AgentArtifactList.tsx`
- Migration：`f3a6c8d1e2b4_add_agent_artifacts.py`
- Slice 7 来源/Profile 迁移：`c7d2f9a4e1b8_add_agent_public_egress_profile.py`

## 已知限制

- staging blob 的孤儿 GC、Session 总量配额和正式恶意文件扫描尚未实现；Turn 已有 8 项/50 MiB 业务
  配额，但不是 Sandbox 物理磁盘配额；
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
