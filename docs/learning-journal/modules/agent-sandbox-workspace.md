# Agent Sandbox Workspace

## 解决的问题

Phase 5 Slice 7.1 把 Deep Agents 的文件工具和 `execute` 接到 Session 专属 OpenSandbox，同时不让
OpenSandbox SDK 类型进入 Domain、公开 API 或 `ResearchAgentRuntime` Port。PostgreSQL 保存 Lease 与
`WorkspaceSnapshot` 元数据，Storage 保存内容寻址 blob；物理 Sandbox 仍只是可轮换的 Runtime 内部资源。

## 固定能力与执行流程

服务端固定 Capability Profile 为 `agent-policy.project-research-workspace.v1`：允许两个 Project Tool、
Deep Agents 文件工具和 Sandbox `execute`，`max_model_calls=8`、`max_tool_calls=12`，Sandbox 开启、网络与
逐命令审批关闭，Browser/MCP/Skill/子 Agent 关闭。用户不能覆盖 Tool、镜像、网络或 Provider 配置。

```text
AgentTurnRun
  → 短事务读取/认领 Session Lease
  → 事务外 connect/renew 或 create OpenSandbox
  → 必要时从最近 stable WorkspaceSnapshot 校验并恢复 /workspace
  → 每次 operation 创建独立 AsyncPostgresSaver + create_deep_agent graph
  → CompositeBackend(default=OpenSandbox, internal routes=StateBackend)
  → Runtime 成功前取回 /workspace 普通文件并登记 STAGED Snapshot
  → 业务成功短事务内将对应 STAGED 发布为 STABLE
  → 失败/取消标记 DIRTY；下一 Turn 轮换 generation
```

`/conversation_history/` 和 `/large_tool_results/` 路由到 `StateBackend`，随 SDK Thread checkpoint 保存，
不会混入 `/workspace` Manifest。成功后的 `collect_turn_result` 和 `reconcile_turn` 只创建新的 Saver/graph
读取 PostgreSQL checkpoint，不连接、续租或创建 Sandbox。

## Lease、状态与事务

- 每个 `AgentSession` 最多一条 active Lease；记录 owner、Project、Session、当前 holder Turn、opaque
  sandbox ID、image、generation、fencing token、状态和期限；不进入 Prompt/Event/公开响应。同一 Turn 的
  crash/resume/retry 保留 generation/fence，新 Turn 才递增 fence，旧 holder 不能推进 stable Workspace。
- `execute_turn` 在 acquire 前先用 PostgreSQL control/checkpoint 离线对账；已知 Execution 的重投不连接、
  续租或抢占 Sandbox。初始 `NOT_FOUND` 竞态再由 Lease CAS 与 RuntimeExecution claim 收敛；CAS loser 只
  销毁自己创建的 Sandbox，本地 pre-event duplicate 只关闭自己的连接。
- create/connect/renew/destroy、模型、Tool 和 Storage I/O 均在业务数据库事务外。Repository 的每个方法
  自持短事务；Provider 成功和 Runtime 成功不等于业务 Message/Artifact 已提交。
- Runtime 已至少产生一个事件后失败或取消，才将 Lease 标为 `DIRTY`。preflight/claim rejection 不污染
  winner；下一 Turn 销毁旧 generation，并从最近 stable Snapshot 重建。
- 跨 Turn 复用同一物理 Sandbox 还必须验证上一 holder 的 Snapshot 已是该 Session 当前 latest
  `STABLE`，并再次核对 owner/Project/Session/Turn scope。同 Turn checkpoint/finalization retry 不要求先
  `STABLE`；但上一 holder 无 Snapshot、只有 `STAGED` 或已不是 latest 时，新 Turn 必须轮换 generation，
  且只恢复 latest `STABLE`。因此业务取消、引用校验失败或成功事务回滚留下的物理文件不会泄漏到下一 Turn。
- generation 轮换先创建并恢复候选 Sandbox，再以旧 fencing token CAS 替换 Lease；CAS loser 只销毁自己
  新建的 Sandbox，绝不销毁观察到的旧实例。CAS winner 提交新 Lease 后才 best-effort 销毁旧实例；回收
  失败不回滚新 Lease，也不销毁已生效的新 Sandbox，由 OpenSandbox TTL/Provider 回收作为兜底。
- Deep adapter 的内部 `before_succeed` finalizer 固定 Runtime 内部顺序为：捕获文件与内容寻址 blob →
  登记不可见 `STAGED` → `RuntimeExecution.succeed` → `COMPLETED`。Snapshot 临时失败释放
  RuntimeExecution permit 供同 Turn/checkpoint 重试，但保留当前 Lease；Manifest/安全校验永久失败形成
  Runtime FAILED 并将 Lease 置 DIRTY。
- Runtime 成功不直接发布稳定 Workspace。AgentTurnExecutor 在写入 assistant/evidence/candidate/event、
  将业务 Run CAS 为 `SUCCEEDED` 并释放活动 Turn 的同一短事务内，通过 SDK-neutral publisher 校验 owner/
  Project/Session/Turn 与 RuntimeExecution 成功状态，再执行 `STAGED → STABLE`。Deep 模式缺少 `STAGED`
  时整个业务成功事务回滚；Fake Runtime 明确允许无 Snapshot。取消、引用校验和业务 CAS 失败产生的
  `STAGED` 对 latest/restore 不可见，可留待后续 GC。
- `RuntimeExecution.succeed` 已提交但响应丢失时，重试从 control/checkpoint 离线收敛，再由业务事务发布
  同一 `STAGED`；不重复模型/Tool，不 acquire Sandbox。只有 `STABLE` 才参与版本序列与恢复。

## WorkspaceSnapshot

Snapshot 分为不可恢复的 `STAGED` 与业务提交后可恢复的 `STABLE`。latest/restore 只读取 `STABLE`。
Snapshot 跳过正常目录，只接受规范化 `/workspace/<path>` 普通文件：最多 128 个、单文件 10 MiB、总计
50 MiB；在下载前用元数据拒绝超限，下载后要求 path/数量/唯一性与请求完全一致，并复核实际 size 与
SHA-256；symlink、device、unknown、路径穿越和重复路径均拒绝。同步 list/download/upload 通过工作线程
执行，恢复嵌套文件前显式创建父目录。相同 Turn 的唯一约束竞态会读取并返回 winner Snapshot。

## 预算、取消与副作用

Deep Agents `after_model` middleware 在 Tool node 前一次性预留该 AIMessage 的全部 Tool calls。Project
Tool、文件工具和 `execute` 共用同一 `max_tool_calls`；Project Tool 的持久 effect 计数是幂等与恢复防线，
不会回灌并二次扣减全局额度。未授权 Tool 在预算预留前 fail-closed。每个模型/Tool 边界继续检查
RuntimeExecution permit；取消后不得启动下一次命令，环境标记 DIRTY。

## OpenSandbox Adapter 与镜像

- Python 依赖精确固定为 `opensandbox==0.1.15`；上游没有官方 Deep Agents Adapter，本项目实现薄
  `BaseSandbox` Adapter。Checkpoint pool 直接依赖 `psycopg-pool==3.3.1`。
- Sandbox 固定 1 CPU、2 GiB、命令 60 秒、inline 输出 64 KiB、网络 default-deny、空 env/volume；
  Worker 不持有 Docker Socket，OpenSandbox server 是显式外部前提，不加入普通 compose。
- `OpenSandboxProvider.create` 固定传入 `entrypoint=['/entrypoint']`，避免 SDK 的空 entrypoint 默认值覆盖
  pinned Chrome 镜像负责启动 Chromium 与 execd 的 recipe；该值不是用户配置。
- `sandbox/research-agent/Dockerfile` 固定上游 Chrome image index digest，并在构建时预装固定版本的
  Python、numpy、pandas、matplotlib 与 CJK 字体；7.3 又以 pinned Node 24 构建阶段和独立 lock 预装
  Playwright/arXiv MCP，运行时不安装依赖。

## 重要测试与实际结果

- 统一预算测试覆盖 Project Tool + `execute`：额度 2 各执行一次；额度 1 时第二个 `execute` 在副作用前
  被拒绝。
- 真实 `create_deep_agent` + 内存共享 checkpoint 测试证明：Sandbox 执行完成并关闭后，换新 Saver 和
  `StateBackend` graph 仍可 collect/reconcile，且不再次 acquire Sandbox。
- 初始 Lease/Workspace/OpenSandbox/Worker/checkpoint 定向离线回归：51 passed；并发 holder/preflight、
  线程卸载、目录/下载完整性与快照竞态加固定向回归：24 passed。
- Snapshot finalizer 顺序、fresh/resume checkpoint、control 响应丢失和失败分类定向回归：61 passed。
- finalizer 加固后的完整非集成回归：1007 passed、4 skipped；RuntimeExecution control 与 Workspace
  Repository PostgreSQL 子集：2 passed。
- `STAGED → 业务成功事务内 STABLE` 加固后的相关定向回归：101 passed；PostgreSQL migration/repository
  往返与发布条件：5 passed；完整非集成回归：1009 passed、4 skipped。
- 跨 Turn `STABLE` 复用门槛及续租/轮换 CAS 回收顺序的最终定向回归：19 passed；最终完整非集成回归：
  1013 passed、4 skipped。
- 本切片范围 Ruff 通过，修改文件 Pyright 0 errors。

## 已知限制

- 7.3 已实际构建派生镜像，并在 `--network none` Docker 容器及本地 OpenSandbox Server Proxy 完成
  MCP/Chromium/下载回路；仍不能宣称 secure runtime、CPU/内存强制、宿主/Secret 不可见或远端销毁
  补偿已在生产环境验证。
- OpenSandbox Python SDK 与自定义 Adapter 仍是 alpha Spike；没有孤儿 Lease 定时清理器、磁盘/PID
  运行时测量、公开部署或生产 SLA。
- 7.3 已把固定 Playwright/arXiv MCP 接到当前 Lease，execute/resume 的 MCP session 包围 graph，并在
  Sandbox Backend 前关闭；离线 collect/reconcile/cancel 不连接 MCP。本地 OpenSandbox 代理 Smoke 已
  完成，noVNC/公共下载仍未完成；7.4 已把 Native Skills 放在 Sandbox `execute` 不可见的只读虚拟 Backend，
  没有改变物理 Workspace。公共网络与统一 egress 后移到 Phase 6。
- `max_model_calls` 仍不覆盖 SummarizationMiddleware 内部重试或已在途 Provider 不确定窗口。
- WorkspaceSnapshot 仍只服务 Runtime 跨 Turn 恢复，不是用户文件列表。Real Deep Agents Runtime 尚未把
  `/workspace` 文件转换为可下载 Candidate，当前 Agent UI 也没有附件上传或 Candidate 内容下载。ADR-0010
  已决定由 Phase 6 增加 `/workspace/inbox` Attachment 物化、`/workspace/outputs` 显式
  `submit_artifact`、独立 AgentArtifact 和业务成功条件提交；不会自动发布整个 Snapshot。

## 代码入口

- `domain/workspace_snapshot.py`
- `infrastructure/agent/opensandbox_backend.py`
- `infrastructure/agent/sandbox_workspace.py`
- `infrastructure/agent/sandboxed_research_agent_runtime.py`
- `infrastructure/persistence/sandbox_workspace_repository.py`
- `infrastructure/workflow/postgres_checkpoint.py`

## 60 秒面试说明

我没有把 Sandbox 当成业务数据库。AgentSession 稳定拥有逻辑 Workspace，物理 OpenSandbox 通过短 TTL、
generation 和 fence 随时可轮换；成功 Turn 才把受限 `/workspace` 普通文件提交为内容寻址 Snapshot，失败
或取消则丢弃未提交变化。Deep Agents 原生管理消息、压缩、文件和 execute，平台在外层管理 owner/
Project 授权、统一预算、取消、Lease 和 Effectively Once。每次 Runtime operation 使用独立
AsyncPostgresSaver/graph，完成后的结果对账完全不依赖活 Sandbox。
