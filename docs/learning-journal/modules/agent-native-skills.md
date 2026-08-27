# Agent Native Skills

## 模块解决的问题

Phase 5 Slice 7.4 让 Project-scoped AgentSession 可以选择平台固定 Skill，或选择当前 owner 创建的声明式
Skill，同时继续使用 Deep Agents 0.7.8 原生 `SkillsMiddleware`。平台不重写 Skill Harness，也不把
Skill 当成权限：PostgreSQL 保存 owner 内容版本、Session Profile 和逐 Turn 冻结引用；Deep Agents 只在
Runtime 内读取本轮物化的只读 `SKILL.md`。

首个固定平台 Skill 是 `evidence-led-synthesis`。它指导 Agent 优先使用 Project Chunk Index、Review
Evidence Matrix 和 Evidence 引用格式，但不能自行授予这些 Tool。

## 边界和执行流程

```text
GET /agent-skills
  → owner 以 name/description/instructions/required_tool_names 创建声明式 Skill
  → 平台生成受控 SKILL.md、SHA-256 和不可变 version
  → 首个 Turn 前以 expected_revision 更新 Session Skill Profile
  → post_message 锁 AgentSession，解析精确版本并验证 required tools ⊆ 本轮 allowed tools
  → PolicySnapshot 冻结 SDK-neutral Skill refs
  → Worker 先用短 DB 读取复核冻结版本/hash，结束事务后才取得 Sandbox
  → /skills/ 路由只读虚拟 Backend，/workspace 与 execute 仍落在 Session OpenSandbox
  → create_deep_agent(skills=[稳定 source], backend=CompositeBackend(...))
  → 同一 SDK Thread 的后续 Turn 复用 checkpoint 中 skills_metadata
```

`ResearchAgentRuntime` Port 没有增加 SDK 类型。`SkillVersion`、`SkillPolicyRef` 和 Profile selection 都是
SDK-neutral 值；`BackendProtocol` 与 `SkillsMiddleware` 只存在于 infrastructure。

## 状态、数据模型和事务

- `agent_owner_skills` 保存 owner 范围的稳定身份与唯一 `(owner_id, name)`；并发同名创建使用数据库
  conflict-do-nothing 收敛为一个 winner。
- `agent_owner_skill_versions` 以 `(skill_id, version)` 为主键，保存 description、instructions、所需 Tool
  和内容 hash。更新先锁稳定 Skill 身份行，再按 `expected_version` 创建新行，避免并发插入绕过 CAS；旧行
  不覆盖，可供旧 Turn 重建。内容 hash 不是版本唯一键：当 v1=A、v2=B 后需要回退到 A 时会追加 v3=A，
  因而既保留完整演化历史，也能表达显式回退；仅与 latest 内容相同的重放返回 latest，不产生空版本。
- `agent_skill_profiles` 保存不可变 revision；`(session_id, revision)` 唯一。Profile 更新与
  `post_message` 都先 `SELECT ... FOR UPDATE` 同一 AgentSession 行，再检查是否已有业务 Message，因此
  “首 Turn 锁定”没有检查后写入窗口。Profile hash 按 `(source, skill_id, version)` 规范排序计算，不受
  API selection 顺序影响；持久化 selection 保留提交顺序用于审计，而 Runtime refs 始终规范排序。
- `PolicySnapshot.skill_refs` 固化 profile ID/revision、Skill ID/source/version/name/content hash 与所需
  Tool；Event 只保存 `skill_count`，不保存 Skill 正文。
- Policy version 保持向后兼容：无 MCP/Skill 使用 `project-research-workspace.v1`，仅 MCP 继续使用既有
  `project-research-workspace-mcp.v1`；只有 `skill_refs` 非空时才使用
  `project-research-capabilities.v1`，无论是否同时启用 MCP。

创建/更新 Profile、创建 Skill 版本和创建 Turn 都使用短 PostgreSQL 事务。Sandbox、模型、Deep Agents
graph 和文件读取不发生在这些事务内。Runtime 物化精确 owner 版本时另开一个短只读会话，结束后才构造
graph。

## 内容与权限不变量

- API 只接受 `name`、`description`、Markdown/text `instructions` 和 `required_tool_names`；请求体
  `extra=forbid`，不接受 owner、path、frontmatter、content hash、脚本、二进制、动态依赖、MCP、网络、
  Sandbox 或镜像配置，也不提供独立 Secret 字段或 Secret 注入机制。description/instructions 是用户文本，
  本切片不扫描其中误贴的 Secret；用户仍不得提交 Secret，任意文本扫描与内容审核留到 Phase 6。
- name 为 1..64 位小写字母、数字和单连字符；description 最多 1,024 字符，instructions 最多 32,000
  字符，所需 Tool 最多 32 个；单个 Profile 最多 8 个 Skill。
- 平台生成唯一受控 frontmatter；SHA-256 对最终 `SKILL.md` 字节计算，读取时复核冻结引用与业务内容。
- Skill Profile 绑定 Session。首个 Message/Turn 后永久锁定；要更换 manifest 必须创建新的 Session，未来
  fork 也应产生新 Session。
- Skill 所需 Tool 必须是本轮最终 `allowed_tool_names` 的子集。Skill 不能扩大 Tool、MCP、网络、Sandbox、
  预算、Project、owner 或 Session 权限。

## Deep Agents 与文件边界

Adapter 把 `/skills/` 加入现有 `CompositeBackend` routes，而不覆盖已有
`/conversation_history/`、`/large_tool_results/` 和 Sandbox default route。`create_deep_agent` 接收
稳定 source 列表并原生添加 `SkillsMiddleware`；没有自行解析、注入或缓存 Skill Prompt。

只读 Backend 支持 `ls/read/glob/grep/download_files`。`write/edit/upload/delete` 全部返回
`permission_denied` 且内容不变。虚拟 `/skills/` 不上传到物理 Sandbox，所以内置文件 Tool 可读，Sandbox
`execute` 的文件系统看不到该路径。相同 SDK Thread 的 checkpoint 已存在 `skills_metadata` 时，Deep
Agents 0.7.8 不重复下载 metadata；Profile 首 Turn 后锁定保证缓存的 manifest 与业务策略一致。

## 失败、重试、恢复和取消

- 未知版本、跨 owner 选择、hash/名称/required-tools 不匹配、权限扩张或缺少生产 materializer 都以永久
  安全错误 fail-closed，不打开新模型调用。
- Profile/版本 CAS 冲突返回 409；非法声明式内容返回 422；跨 owner 的 Session/Skill 使用既有 404
  不可见语义。
- Runtime 重试继续使用 Turn 的 `PolicySnapshot.skill_refs`，不会自动读取 Profile 新 revision 或最新
  Skill 版本；旧版本保留，因此可重建冻结 Turn。
- collect/reconcile 和不含在线 request 的恢复不 acquire Sandbox、不物化 Skill，也不读取外部资源。
  execute/resume 仍沿用既有 RuntimeExecution permit、取消、预算和 checkpoint 恢复边界；Skill 不新增
  Tool 副作用。

## 安全与可观测性

- owner 由可信 ActorContext 提供，不能来自请求体；Profile 查询同时验证 AgentSession owner。
- Skill 正文属于业务内容，但不进入 Run Event、日志、Prompt 审计或 Runtime Port DTO；业务
  Policy/Event 只保存稳定 ID、版本、hash、所需 Tool 和数量摘要。
- owner-authored instructions 仍是不可信提示，可能诱导模型误用已经授权的 Tool。只读 Backend 和权限子集
  校验限制它不能增加能力，但不能证明 Prompt Injection 已被消除。
- API 不提供 Skill Secret 注入通道，但不能可靠识别用户粘贴到普通文本里的凭据；日志/Event 不记录正文，
  Phase 6 仍需补内容扫描、审核与脱敏策略。
- 本切片没有真实 Provider、网站、外部 MCP 或 OpenSandbox 调用，测试完全离线且零费用。

## 关键决定与替代方案

- 选择 Session 级 manifest 并在首 Turn 后锁定，而不是每轮热切换。原因是 Deep Agents 0.7.8 会把
  `skills_metadata` 缓存在同一 Thread checkpoint；热切换会使业务 Profile 与 Runtime 缓存分裂。
- 选择平台生成 `SKILL.md`，而不是接受上传目录或 raw frontmatter。这样 path、可执行附件和依赖配置不
  进入产品输入面。
- 选择虚拟只读 Backend，而不是把 Skill 复制到 `/workspace`。这样模型可通过原生文件工具读取，Sandbox
  shell 不能修改；代价是 Skill 脚本与资源附件不在本阶段支持。
- 选择精确版本引用而不是“运行时取 latest”，保证重试和恢复不会因 owner 后续编辑而漂移。
- 选择版本主键表达演化、内容 hash 表达内容相等，而不把 hash 设为版本唯一键；因此内容回退追加新版本，
  不篡改历史版本。Profile hash 则先规范排序，避免 manifest 重排制造虚假差异。

## 重要测试和实际结果

- Domain 红灯最初为 `ModuleNotFoundError`；实现后覆盖不可变版本/hash、输入边界、Profile revision、
  owner 隔离和 required-tools 权限不扩张。
- PostgreSQL Application 测试覆盖并发同名创建、并发版本 CAS、Profile owner/CAS/首 Turn 锁定、旧版本
  恢复、A→B→A 内容回退追加 v3、逐 Turn Policy 冻结和 Event 正文过滤。
- Domain/Application 兼容测试覆盖 workspace、MCP-only 与 Skill 三种 Policy version 分支，防止接入 Skill
  后无意改写既有 MCP-only Snapshot 契约。
- FastAPI 测试覆盖声明式创建、Profile 选择，并拒绝 owner/path/frontmatter/scripts/content hash 等
  额外配置。
- 真实 `create_deep_agent` + Fake Chat Model + MemorySaver 的两轮离线测试证明：分别重建两个 Runtime/
  graph 实例、只共享持久 checkpointer/thread 和后端时，第二轮不重复下载 `skills_metadata`；内置文件工具
  可读取 Skill，写/改/上传/删除均拒绝，物理 Sandbox `execute` 看不到 `/skills/`。
- Materializer 离线契约测试证明 owner ref 只按 Policy owner 查询精确版本，版本缺失或 hash/name/
  required-tools 漂移均返回不含正文的永久 `runtime_skill_version_invalid`。
- Sandboxed wrapper 测试覆盖 materializer/capability factory 生命周期、缺失装配在 Sandbox acquire 前
  fail-closed，以及成功后离线 collect/reconcile 不重新物化或 acquire；Alembic 在临时 PostgreSQL完成
  head → downgrade -1 → head → check。
- 最终 Domain/Application/API/Fake/Deep Adapter/Sandbox/Worker 定向回归为 `123 passed in 19.84s`；
  PostgreSQL Application/Repository、两轮可靠性与迁移回归为 `13 passed in 28.52s`；owner composite FK 加固后
  单独复跑迁移为 `6 passed in 5.05s`。
- 本切片定向 Ruff 通过，修改生产文件的 Pyright 为 0 errors、0 warnings、0 informations。

## 代码入口

- Domain：`backend/src/literature_agent/domain/skill_configuration.py`
- Application：`backend/src/literature_agent/application/skill_configuration_service.py`、
  `backend/src/literature_agent/application/agent_session_service.py`
- API：`backend/src/literature_agent/api/agent_sessions.py`
- 平台 Catalog：`backend/src/literature_agent/infrastructure/agent/skill_catalog.py`
- 只读物化：`backend/src/literature_agent/infrastructure/agent/skill_backend.py`
- Deep Agents Adapter：`backend/src/literature_agent/infrastructure/agent/deep_agents_research_agent_runtime.py`
- Repository/ORM：`backend/src/literature_agent/infrastructure/persistence/skill_repository.py`、
  `backend/src/literature_agent/infrastructure/persistence/models.py`
- Migration：`backend/migrations/versions/b7d3e1f9a5c2_add_agent_native_skills.py`

## 已知限制

- 首版只支持纯文本 `SKILL.md`，不支持 Skill 目录附件、脚本、二进制、动态依赖或资源 bundle。
- 平台 Catalog 当前只有 `evidence-led-synthesis` v1；没有平台管理 UI、删除/归档、配额或内容审核工作流。
- 声明式文本没有 Secret 扫描或内容审核；API 虽无独立 Secret 注入字段，用户仍可能误把凭据粘贴进普通
  description/instructions，治理留到 Phase 6。
- Profile 首 Turn 后不能编辑；fork/rewind 尚未建设，因此更换 Skill 需新建 Session。
- Skill metadata 缓存行为只对锁定的 `deepagents==0.7.8` 通过离线测试；SDK 升级仍须契约回归并提升 graph
  revision。当前为 `deep-agent-graph.v5`，不提供跨 graph revision 自动迁移。
- 没有真实 Provider/OpenSandbox Smoke、Prompt Injection 专项评测或用户可见 Agent Chat UI；Phase 6 仍需
  完成网络、下载、Sandbox 强制效果与完整 Skill 治理。

## 60 秒面试说明

“我复用了 Deep Agents 原生 Skills，而没有重新写 Skill Harness。平台让用户只提交结构化文本与所需
Tool，生成不可变、内容寻址的 SKILL.md；Session 在首 Turn 后锁定 Skill manifest，每轮 Policy 都冻结同一
版本和 hash。Worker 在事务外把精确版本物化到 `/skills/` 只读虚拟 Backend，create_deep_agent 原生加载
并把 metadata 缓存在同一 Thread checkpoint。内置文件工具能读 Skill，但 Sandbox execute 看不到也不能
修改它。最重要的是 required tools 必须是本轮权限的子集，所以 Skill 只是研究方法提示，不是权限来源。
PostgreSQL 仍拥有版本、Profile 和审计事实，SDK 类型只留在 Adapter。”
