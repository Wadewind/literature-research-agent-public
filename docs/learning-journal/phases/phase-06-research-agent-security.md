# Phase 6：Deep Agents 驱动的 Research Agent 与安全强化

## 状态

已完成。Spec 初版日期：2026-08-20；按 ADR-0005 对齐日期：2026-08-25；按 ADR-0007
调整日期：2026-08-26；按 ADR-0008 调整日期：2026-08-27；按 ADR-0009/0010 对齐 Browser 人工控制与
Agent 文件交换日期：2026-08-28；按 ADR-0011 收敛为本地个人项目精简交付日期：2026-08-28；对齐
`docs/spec/web-ui-app-shell-redesign.md` 的最终 UI 契约日期：2026-08-28；Slice 1 精简产品契约与威胁模型
完成日期：2026-08-28；Slice 2 Agent 输出 Artifact 完成日期：2026-08-28；Slice 3 Browser 画面与跨 Turn
人工控制完成日期：2026-08-28；Slice 4 Agent 输入附件完成日期：2026-08-28。
Slice 5 固定能力、Project Context 与硬预算实现完成日期：2026-08-28。
Slice 6 Workspace/Sandbox 与统一 egress 强化完成日期：2026-08-28。
ADR-0012 将 Slice 7 从固定 arXiv Host allowlist 调整为版本化 Sandbox public-egress Profile 的日期：
2026-08-28；Slice 7 实现与显式真实 OpenSandbox public-egress Smoke 完成日期：2026-08-28。首轮 Smoke 已证明
Sandbox 内部 loopback 可用，但随后因固定镜像没有 `curl` 而在第一条公网命令失败；验收已改用镜像固定
存在的 `/usr/bin/wget`。第二轮已进一步证明 `wget` 可访问 arXiv 首页；固定 `1706.03762` 的完整
2,215,244-byte PDF 在 30 秒 Sandbox 命令限制内未下载完成，Adapter 外层最终以 exit 124 结束，尚未执行
private/MCP 步骤。这是全量下载耗时而不是网络拒绝证据；验收已改为同一 PDF 最多 64 KiB 的有界前缀，
第三轮真实 Smoke 最终为 1 passed（39.67s）。该结果只证明本节列出的固定目标和进程边界，不证明完整
PDF 下载、所有公网目标、协议级只读、secure runtime 或生产隔离。
Slice 8 的应用壳子切片 8.1 完成日期：2026-08-28；已用固定 `AppSidebar` 取代旧全局 Header，加入全局与
Project 四模式索引导航、版本化折叠偏好和 232px/56px 桌面栏，并把工作区可用高度恢复为 `100dvh`。
主智能体最终独立验证 `npm test` 为 24 files / 161 passed，`npm run build` 通过；完整 E2E 曾为
3 passed / 2 failed，其中 Phase 5 初次因新应用壳挤压既有三栏工作区、导致 Evidence Margin 横向裁剪而
失败。将 Research/Agent 工作区中央列改为可收缩的 `minmax(0, 1fr)` 后，主智能体定向运行
`npm run test:e2e -- phase-05.spec.ts` 为 1 passed（36.1s），并验证独立滚动的 Evidence Margin 可展开、
定位候选成果。另一项 Phase 4 失败为来源列表期望 4、实际 3；现有证据指向后台 Fixture/时序，尚未形成
与 Sidebar 的因果证据，因此保持原业务断言，留待后续完整回归复核。完整五流程 E2E 与桌面截图走查仍未
闭环，本记录不宣称 Slice 8 的全部视觉验收已经完成。
Slice 8 的轻页头子切片 8.2 完成日期：2026-08-28；全部主页面、Run/Document 诊断页和错误态已切换到
56px `PageBar`，项目四模式导航只保留在 `AppSidebar`，旧 `ProjectWorkspaceHeader`、`ProjectNav`、
Hero 与重复模式入口已经删除。TDD 首先得到 PageBar 模块缺失失败，完成后定向 3 passed；完整
`npm test` 为 25 files / 164 passed，`npm run build` 与 `git diff --check` 通过。完整离线 E2E 为
4 passed / 1 failed：Phase 1/2/3/5 通过；Phase 4 仍为来源列表期望 4、实际 3 的既有 Fixture/时序失败，
未修改业务断言。1440×1000 截图走查覆盖首页、综述和研究助手；研究助手实测 PageBar 56px、document
`scrollHeight` 等于 1000px viewport、三栏未横向裁剪。浏览器仅另外记录 favicon 404 和无选中 Session
首页既有的空 `session_id` 附件请求 404；两者不属于 8.2 行为，未在本切片顺便修复。深色 Agent welcome、
Review workbench 与视觉 token 当时仍按顺序保留到 8.3/8.4，不能据此宣称最终视觉重设计完成。

Slice 8 的工作区空间回收与 Agent 产品整合子切片 8.3 完成日期：2026-08-28；研究助手右栏已拆为“证据 /
浏览器 / 成果”三个可访问 tab，中心研究活动整合筛选 Event、脱敏 ToolExecution 与 Usage/Budget，成果区
组合正式 Artifact、内部 Candidate 和公开 Manifest 来源摘要；能力配置、Evidence Matrix 与附件收进固定
Composer。`.agent-welcome` 已改为浅色紧凑引导卡，Chat 创建页不再保留整屏空白；无 Session 的附件查询
被条件禁用。新增 TDD 定向测试从 4 个缺失模块失败转为 4 files / 5 passed；最终 `npm test` 为
29 files / 169 passed，`npm run build` 与 `git diff --check` 通过，Phase 5 E2E 为 1 passed（37.2s）。
1440×1000 走查确认 Chat/Agent document 均不滚动、三栏内部滚动、Composer 可见、Inspector ArrowRight
键盘切换有效，能力 details 展开后只压缩 timeline；noVNC 仍单独 lazy load。一个历史 Turn 因早于 Usage
事实落地而对 ToolExecution 查询返回 404，UI 仅显示安全错误；新 Turn 的 E2E 请求为 200，纯前端不伪造
历史数据。视觉 token 刷新仍留给 8.4，不能据此宣称 Slice 8 或最终视觉重设计全部完成。

Slice 8 的全站视觉 token 与可访问性刷新子切片 8.4 完成日期：2026-08-28；白色纸面、弱边框/阴影与
低干扰网格已覆盖主要页面，Review workbench、RAG ask strip 和 Agent welcome 收敛为浅色纸面 + 朱红
状态线，仅 Project 创建引导区保留深色。关键功能小字提升至 11–12px，数字元数据采用 tabular nums；
全局新增 skip link、稳定主内容目标、focus-visible、touch-action 和 reduced-motion，Artifact 图片补齐
显式尺寸。TDD 从 AppFrame/图片尺寸 2 个失败转为定向 2 files / 3 passed；完整 `npm test` 为
30 files / 170 passed，`npm run build` 与 `git diff --check` 通过。1440×1000 走查覆盖首页、Project
文献库、Chat、Reviews 和 Agent；Chat/Agent document 均不承担工作区滚动，三栏无横向溢出且 Composer
可见，skip link 可由键盘进入主内容。控制台仍有既有 favicon 404，以及一个历史 Turn 早于 Usage 事实
落地导致的 ToolExecution 404；本纯前端切片未伪造兼容数据，也不把桌面走查宣称为全量 WCAG、跨浏览器
或移动端认证。上述限制已带入 Slice 8.5 的最终复盘，不阻塞本地单人演示范围完成。

Slice 8.5 最终产品整合、验证与复盘完成日期：2026-08-28。固定镜像新增 PATH 内 `Xtigervnc` wrapper，
强制 `-SecurityTypes None -localhost`：RFB 只在 Sandbox namespace loopback 免二次密码，外层继续由
owner/Session/generation/revision 和短时 ticket gateway 鉴权，raw 5901 不暴露给 Web。主智能体用默认
新镜像 digest 完成生产 `AgentBrowserPanelView`/noVNC → ticket 解析与有界 bridge → 同 Sandbox Chromium
输入 marker → 保持打开的同 generation Playwright MCP 回读，真实 Smoke 为 1 passed（15.96s）。新增
7 场景版本化离线 Agent 评测、Deep Agents 0.7.8 装配/Checkpoint/跨进程恢复升级门禁和固定 Phase 6
安全回归；最终结果分别为 7/7、130 passed（71.22s）和 283 passed、1 skipped（94.78s）。完整后端默认套件为
1291 passed、10 skipped（661.86s）；Web 为 30 files / 170 passed，production build 通过，Phase 5
Agent UI E2E 为 1 passed（36.6s）。完成报告见
[`phase-06-research-agent-extension-completion.md`](../reports/phase-06-research-agent-extension-completion.md)。

Slice 1 已完成文档契约审计，形成
[`Research Agent 精简安全契约`](../../spec/research-agent-security-contract.md)。该契约明确区分 Phase 5
已有代码/测试事实与 Phase 6 目标事实，冻结所有权与信任边界、自动执行/直接拒绝矩阵、历史
`PolicySnapshot.approval_required` 兼容规则、API/Event 增量、事务外 I/O、Effectively Once/取消/fence
以及后续切片门槛。Slice 2 已在该边界内实现独立 `AgentArtifact`、Candidate
`STAGED → VALIDATED → COMMITTED`/`REJECTED`、真实 Sandbox 专用 `submit_artifact`、事务外文件校验与
Storage staging、Turn 成功事务内发布、owner-scoped 查询/下载和壳层无关成果组件。Slice 3 已实现独立
`BrowserControlLease`、Session/Turn/Sandbox generation/fence 互斥、短时 opaque ticket、平台 VNC
WebSocket 代理和 noVNC 右栏组件。Slice 4 已实现 owner/Project/Session scoped 不可变输入附件、有界消息引用、`agent-context.v2` 冻结、事务外 fenced `/workspace/inbox` 物化、WorkspaceSnapshot 隔离与壳层无关 Chat UI。Slice 5 已把全部允许 Tool 的 version/schema hash 与硬预算冻结进 `PolicySnapshot`，增加 PostgreSQL `AgentTurnUsage`/稳定 reservation/脱敏 Tool 摘要、调用前后 scope/取消/fence 校验、剩余墙钟 timeout、循环保护和安全查询 API。Slice 6 已增加 fenced `RETIRED` Lease、持久化 Sandbox cleanup 补偿、Worker cleaner、固定本地 OpenSandbox Server 配置和统一 default-deny egress 的真实行为验证。Slice 7 已完成 public-egress Profile、Policy/Lease 冻结与漂移轮换、声明来源目标检查、Artifact 每 Turn 配额、Manifest 离线闭环与显式真实 OpenSandbox Smoke；Slice 8.1–8.5 已完成应用壳、轻页头、工作区/Agent 产品整合、视觉 token/可访问性刷新、真实 noVNC 验收、固定评测、升级门禁与完成复盘。

Slice 2 的普通验证全部离线：完整后端非 integration 回归为 1005 passed、5 skipped；Artifact 相关
PostgreSQL Executor/Alembic 往返为 24 passed，API 为 9 passed，Sandbox/Deep Agents Adapter 为 60 passed；
Web 全量 Vitest 为 143 passed 且 TypeScript/Vite build 通过。未运行真实 Provider 或真实 OpenSandbox
Artifact Smoke，不能据此宣称生产级恶意文件扫描或无 TOCTOU 竞争。

Slice 3 的定向离线验证为 Browser Domain/Adapter/API/recipe 41 passed、真实 Smoke 1 skipped，PostgreSQL
Application/Alembic/既有两轮流程 20 passed、Web 定向 4 passed/全量 147 passed；完整后端非 integration
回归为 1044 passed、6 skipped（140.20s），Pyright 零错误且 TypeScript/Vite build 通过。旧固定镜像的诊断 Smoke
确认 Sandbox 内 TigerVNC `1.15.0+dfsg-2` 在 `5901` 返回 RFB，但 OpenSandbox 暴露的 endpoint 实际映射到
HTTP egress sidecar，raw TCP 连接超时。实现已改为在固定镜像中加入 websockify `0.13.0`：Sandbox 内
`6080` WebSocket 转发 loopback `5901`，平台经 OpenSandbox Server Proxy 的 `ws/wss` endpoint 与必需
headers 建立上游连接。修正镜像已重建；第四次主审真实 Smoke 完成 Server Proxy→websockify→RFB 握手，
并由同一 Sandbox 的 Playwright MCP 操作合成页面，结果为 5 passed（13.06s）。前三次失败分别暴露 raw
endpoint 语义、宿主代理继承以及合成服务 readiness/Fixture 转义问题，均已形成离线回归契约。当前本地
OpenSandbox 未配置 API key/secure runtime，因此这只是 trusted-local 功能证据，不代表 noVNC 人工键鼠
UI E2E、通用认证、多实例 API、公网网站或跨 generation 登录恢复已验证。

Slice 4 经主智能体复核的完整后端非 integration 回归为 1066 passed、6 skipped（148.64s）；附件
Application/Repository/Alembic PostgreSQL 定向为 19 passed，Agent Session/Attachment API 为
12 passed，主智能体定向组合复核为 33 passed（30.67s）；Domain/Materializer/Runtime/Workspace 新增边界定向为 41 passed。Pyright 零错误，Web
全量 Vitest 为 156 passed，TypeScript/Vite build 通过。未运行真实 Provider/OpenSandbox 附件
Smoke；不声称已完成 Storage GC、恶意文件扫描或生产级 Sandbox 隔离。

Slice 5 普通自动测试保持离线；定向验证为 Domain/Application/schema/model 47 passed，
API Tool 摘要 2 passed/10 deselected，Runtime 新策略 12 passed/39 deselected，Artifact schema
1 passed/8 deselected，Worker 14 passed；PostgreSQL 查询闭包 4 passed/5 deselected、并发
1 passed、migration 2 passed/7 deselected、repository 7 passed、two-turn 1 passed。Ruff、
Pyright（0 errors）、compileall 和 diff check 均通过。这些是 Slice 5 风险定向证据，不是完整
全量回归。当前 Python 3.13.14 + pinned Deep Agents 0.7.8 下，既有带 Tool 的
Fake Model 图测试会停在 Filesystem→Summarization model middleware，动态加载未修改 HEAD Runtime 也在
进入 Fake Model 前同样复现。新增 reservation/replay 测试使用直接 middleware 契约且有界完成；在夹具/
上游兼容问题解决前，不把本切片描述为新增了完整 Deep Agents Project Tool 图回路证据。

Slice 6 的离线定向验证覆盖 Lease 轮换/候选回收、清理响应丢失、Provider 精确 404 幂等、Worker 循环和
Server 配置契约；新增 expired/session_closed 覆盖后，主智能体复核 PostgreSQL Repository 与 Alembic
`head → -1 → head` 为 13 passed（18.55s），修改范围 Pyright
为 0 errors。显式本地 Smoke 使用 `opensandbox==0.1.15`、OpenSandbox Server `0.2.2`、固定 research/
execd/egress image digest 和项目 TOML：统一 egress/资源测试最终复验 1 passed（33.08s），60 秒 TTL 自动回收测试
1 passed（64.23s），命令超时后 Backend 仍可用测试 1 passed（7.22s）。OpenSandbox 0.1.15 的 execd
timeout 实测只限制 RPC 等待、不会终止已启动命令，因此 Adapter 额外用固定镜像内的 coreutils `timeout`
约束进程组，并给 Provider 等待增加 2 秒清理余量。Smoke 实际观察到非 root、1 CPU、2 GiB、PID 256、
空平台 Secret、无宿主/Docker/数据库
挂载、64 KiB 输出上限，并由 Bash、Python、Node、Chromium、Playwright 和固定 arXiv Search MCP 验证
default-deny。该结论来自 Provider `NetworkPolicy`、Server 0.2.2 egress sidecar 与 Sandbox 共享 network
namespace 的上游实现、以及进程行为的组合证据；`chromium --no-sandbox` 只用于验证容器级统一 egress，
不构成浏览器进程隔离声明。当前 Docker runtime 不支持请求级 overlay 物理磁盘硬配额，且未配置 secure
runtime，因此仍不是公网或生产隔离证明。

ADR-0007 已把 OpenSandbox Provider、Session 级短 TTL Lease、固定依赖的 Sandbox `execute` 与
WorkspaceSnapshot 提前到 Phase 5 Slice 7；ADR-0008 又把 MCP Catalog/Profile 基础、同 Sandbox
Playwright MCP、现有 Search MCP 与 Deep Agents 原生 Skills 的最小验证提前到 Phase 5。本阶段不重复
这些 Spike，而是在其实际证据基础上完成文件交付、Browser 人工控制、Sandbox 公网访问/正式资源校验、最小
能力治理、Sandbox 强化和 UI/E2E 闭环；不再以完整 Registry、通用审批中心或生产运维平台作为阶段出口。
其中 Slice 7.1 已完成 OpenSandbox/Lease/WorkspaceSnapshot 的实现与离线/临时 PostgreSQL 验证；
2026-08-28 又通过本地 OpenSandbox Server Proxy 完成功能 Smoke。Phase 5 的 7.2 MCP 配置、7.3 固定
Playwright/arXiv MCP 镜像内回路和 7.4 Native Skills 已完成受限验证，但不替代本阶段的公共网络、下载、
Prompt Injection、面向用户的 Browser 画面与完整治理验证。

进入条件：Phase 5 已完成并通过 ADR 确认 Deep Agents 的版本策略、部署拓扑、`ResearchAgentRuntime` 契约、MCP 模式、Sandbox Provider、重试所有权和升级方法；Phase 5 的安全、取消、断连和重复副作用验证没有未解决的阻塞项。

## 目标和用户可见结果

把 Phase 5 的多轮集成 Spike 扩展为可用、受限、可观察的 Research Workspace Agent。用户可以在一个
Project 内持续对话，让 Agent 使用授权的 Paper Chunk Index、Review Evidence Matrix 和 Artifact 自主
分析研究问题，并按需发现公网公开资源或在隔离 Sandbox 中处理数据；用户可以查看每轮状态、来源、Tool
调用、硬预算、错误和 Artifact，并能取消当前 Turn。首个产品增量还允许用户在两个
Turn 之间操作同一 Session Chromium 完成登录等人工步骤，并能让 Agent 把 Sandbox 中生成的图片、表格
或报告显式提交为可预览、可下载的正式产物。

```text
创建/打开 Project-scoped AgentSession
  → 用户消息创建 AgentTurnRun
  → 固定 ContextSnapshot、PolicySnapshot 和 Budget
  → Deep Agents 规划并调用受控 Tool/MCP
  → Browser/MCP/execute 通过版本化 public-egress Profile 访问正常公网
  → private/metadata/宿主与 LAN 目标由统一 egress 拒绝
  → 文件只有离开 Sandbox 成为正式业务资源时才进入来源与文件校验
  → 平台不注册外部写专用 Tool、不提供凭据；raw 公网通道不承诺协议级只读
  → 文件只在隔离 Workspace 中处理
  → 平台校验 Evidence、Manifest 和 Artifact
  → Citation/来源校验
  → 提交结果、Usage、审计和可重放 Event
```

阶段结束时，Research Agent Extension 是 Demo-ready Core v1 之上的独立扩展。禁用或移除 Agent
Runtime 不影响文献导入、RAG 和固定 Review Workflow。

## 精简交付基线

ADR-0011 将本阶段定位为本地、单人演示的受限 Research Workspace Agent；ADR-0012 定向替换其网络
范围。以下能力是阶段完成所必需：

| 能力主线 | 必须交付的结果 |
|---|---|
| Agent 文件 | Attachment 输入；`submit_artifact` 输出；图片、PDF、CSV、Markdown 等受支持文件的校验、预览和下载 |
| Browser | 当前 Session/generation 的 Chromium 画面；Turn 边界人工控制；合成页面验收 |
| 真实学术资源 | Sandbox 可访问任意正常公网 HTTP(S)；统一 egress 拒绝 private/metadata/宿主/LAN；正式下载/来源经过文件校验和来源记录 |
| 能力治理 | 固定 Catalog/Profile、Schema/hash、权限、超时、输出裁剪、ToolExecution 摘要和硬预算 |
| Sandbox | owner/Session/generation 隔离、非 root 固定镜像、Secret/宿主隔离、TTL、资源限制、fence、清理和恢复 |
| 产品闭环 | Project Context 隔离、取消/重复/响应丢失处理、Agent UI、离线 E2E、显式真实 Smoke、评测与运行文档 |

以下内容明确延期：通用 Approval Center、外部写产品能力及其协议级强制、任意用户 MCP/Tool/网络配置、
OAuth/Credential 生命周期、通用 URL 安全代理、动态包安装、多 Agent/长期 Memory、Sandbox 集群调度/预热/自动扩缩容、精确计费、
公网多租户、SLA 与完整灾难恢复。新增这些能力前必须另行更新 Spec/ADR。

### UI 实施约束

Phase 6 所有新增 UI 必须遵守 [`Web UI 应用壳与视觉重设计`](../../spec/web-ui-app-shell-redesign.md)，该文档
是强制契约而非视觉参考：最终界面采用左侧固定 `AppSidebar`、全站轻量 `PageBar`、桌面优先的统一页面壳，
不恢复全局顶部 Header、大 Hero 项目页头或重复的项目模式入口。浅色编辑风、零圆角、三栏工作区与会话
内部 rail 的边界保持不变。

- 切片 2–4 可以新增 Artifact、Browser 和 Attachment 的独立功能组件与必要入口，但不得在这些后端垂直
  切片中顺便进行全站 App Shell 重构；组件应避免依赖将被删除的 `ProjectWorkspaceHeader`、`ProjectNav`
  或旧 76px Header 高度；
- 最终 UI 整合前必须检查 App Shell 重设计是否已经完成。若未完成，切片 8 先按该 Spec 的应用壳骨架、
  轻页头替换、工作区空间回收、视觉 token 刷新四个顺序子切片分别实现和验证，再整合 Phase 6 功能；
- App Shell 重设计保持纯前端，不改变 Phase 6 API、数据库或 Runtime 契约；实施轻页头替换时同步更新
  `docs/spec/project-workspace-ui-contract.md` 中被取代的共享 Project Chrome 条款；
- Phase 6 的 Turn 步骤时间线、Evidence/PDF/Artifact 右栏 tab 和 Composer 能力配置只能在 UI Spec 规定的
  对应切片评估、实现，不能重新引入与其冲突的页面级布局；
- 每个 UI 子切片保持独立可回退提交，并实际运行 Vitest、TypeScript build；具备本地后端时再运行
  Playwright E2E 和桌面截图走查，环境不具备时必须明确记录。

## 范围

### 包含

- Agent Session 多轮 Chat、Turn 详情、事件、取消、来源和 Artifact UI/API；
- Project Chunk Index、Review Evidence Matrix 和既有 Artifact 的受权 Context Tool；
- Paper/Evidence 读取、固定 Catalog Search/Playwright MCP、正常公网 Browser/下载、Artifact 提交工具；
- Phase 5 固定 Catalog/Profile 的产品化：Tool Schema/hash、权限、超时、输出限制和必要执行记录；
- 版本化 public-egress Profile、private/metadata/宿主/LAN 拒绝和正式资源下载隔离；
- Deep Agents Tool Policy 和步骤/Token/时间/Tool Call/输出硬预算；
- Workspace/Sandbox 生命周期、文件传输、网络和计算资源限制；
- Agent Event、Usage、ToolExecution、Workspace 和 Artifact 的最小审计；
- Runtime、MCP、Browser、Sandbox 和 Provider 的取消、重试、断连、恢复与对账；
- Prompt Injection、跨用户隔离、Secret 外泄和受限文件下载测试；
- Deep Agents 升级契约测试、关键故障验证、小型 Agent 评测集和本地运行文档；
- 平台安装、版本化和 allowlist 控制的 Research Skills，以及 owner-scoped 声明式 Skill 的安全治理；
- 基于 Phase 5 OpenSandbox `execute` 与固定依赖的实际 Spike 证据，强化并产品化结构化、受限的数据分析
  与绘图能力。
- Session/generation 范围的 Browser 画面与跨 Turn 人工控制；用户完成操作后由下一 Turn 的
  Playwright `browser_snapshot` 读取更新状态；
- AgentAttachment、`submit_artifact`、AgentArtifactCandidate 与 AgentArtifact 的显式文件交换、预览和
  下载闭环。

### 不包含

- 自行重写 Deep Agents 的 Agent Loop、Planner、上下文压缩或 Checkpoint 引擎；
- 每轮从 PostgreSQL 重放完整产品消息历史，或把 `ContextSnapshot` 当作第二套 Runtime 对话状态；
- 任意用户提供的 MCP Server、Tool 代码、Sandbox 镜像或系统 Prompt；
- 用户上传、安装或修改可执行 Skill、二进制或动态依赖；Phase 5 已允许的 owner-scoped 声明式 Markdown/
  文本 Skill 除外；
- 宿主 Shell、宿主 Python、动态包安装、Docker Socket 或宿主文件系统；Sandbox `execute` 仅限
  ADR-0007 的 Session 专属 OpenSandbox；
- 绕过登录、付费墙、robots/站点限制、CAPTCHA 或下载授权；
- 平台托管用户名、密码、Cookie、验证码或 Chrome Profile；首版人工 Browser 控制只在两个 Turn 之间
  发生，不提供运行中 Turn 的并发抢占或自动跨 generation 登录恢复；
- 面向自动对外发帖、发邮件、提交表单、修改远程仓库或金融/不可逆操作的产品 Tool/Workflow；产品策略
  要求 Agent 不执行这些动作，但 raw 公网通道当前不能强制协议级只读；
- 通用 Approval Center、LangGraph 审批 Interrupt/Resume、OAuth/Credential 生命周期和 Catalog 管理后台；
- 用户自定义网络 Profile、代理、DNS、认证 Secret 或 private/metadata/宿主/LAN 访问；
- 生产级恶意文件扫描、Sandbox 集群调度/预热/自动扩缩容、精确计费、SLA 与完整灾难恢复；
- 无上限自主运行、无限子 Agent、跨 Project Memory 或跨用户共享 Workspace；
- 把网页/论文中的指令视为系统指令；
- 用 Agent 替代 Phase 3 的确定性 Review Workflow；
- 把 OpenSandbox `execute` 扩张为宿主执行、private-network 访问、动态依赖安装或通用 Coding Agent。

## 涉及模块

- Agent Session/Message/Turn API/UI 和 `ResearchAgentRuntime` Adapter；
- Run、Attempt、Step、Event/SSE 和 Reconciliation；
- Project Context、Paper、Evidence、Citation 和 Resource Manifest；
- 固定 Tool/MCP/Skill Catalog、ToolExecution 和精简 Policy Engine；
- Browser、URL Policy、Download Scanner 和 Artifact Storage；
- Workspace/Sandbox Lifecycle 和受限文件传输；
- Model Gateway、Usage Ledger 和硬 Budget；
- 现有 JSON Log/Metrics、Agent Evaluation 和本地运行文档。

## 产品边界和首版用户故事

Phase 6 仍只支持“Project 范围内的受控研究”，不扩大为通用 Coding Agent：

1. 用户打开 Phase 5 创建的 Agent Session，并继续多轮提出分析、比较或资料补充问题；
2. 每轮由平台固定可见 Paper/Chunk、Review Evidence Matrix、Artifact、Tool、Skill、网络和 Budget；
3. Agent 优先使用项目内部证据分析；需要时可在 Session Sandbox 中搜索、浏览和下载正常公网公开资源；
4. 需要计算时可使用 ADR-0007 已批准的 OpenSandbox `execute` 和固定 Python 依赖；private-network、动态
   依赖、外部副作用或宿主能力仍必须拒绝；
5. Agent 输出带 Evidence/来源的回答、Resource Manifest 或候选 Artifact；
6. 用户可以查看每轮来源、必要 Tool 摘要、预算、错误和最终产物，并在同一 Session 中追问。

首版不以“自动撰写完整综述”作为 Agent 目标；完整综述仍由 Phase 3 固定 Workflow 生成。Agent 发现的资源只有经过平台校验和用户纳入后才能进入 Paper/Evidence 体系。

## 核心状态和所有权

```text
AgentSession       Project 范围内的持续业务会话和消息历史
AgentTurnRun       一条用户消息对应的生命周期、取消、重试和最终状态
Run Attempt        平台 Worker 对一轮 Turn 的至少一次执行与 lease
Runtime Binding    session_id ↔ SDK thread；turn_run_id ↔ SDK execution
Context/Policy     每轮不可变的授权、版本、工具和预算快照
Run Step           用户可理解的计划/阶段投影，不复制内部思考
Tool Execution     一次版本化 Tool/MCP 调用及副作用幂等记录
Workspace          Session 逻辑命名空间与 Session/Thread 范围短 TTL Sandbox Lease
WorkspaceSnapshot  跨 Turn 持久化的内部工作文件与 Manifest
BrowserControlLease 只记录用户在当前 Session/generation 操作 Chromium 的短时 MANUAL 控制权
AgentAttachment     用户显式授权给 Session/Turn 的输入文件
Usage/Budget       已消费与剩余额度的业务事实
Resource Manifest  发现的外部资源及来源验证结果
AgentArtifactCandidate 显式 submit 后、尚未随业务 Turn 提交的文件候选
Artifact           通过平台校验并持久化的 Review 或 Agent 文件
```

- PostgreSQL 保存 Session、Message、Turn Run、Context/Policy/Workspace Snapshot、Attempt、Event、
  ToolExecution、Usage、Manifest 和 Artifact 元数据；
- Deep Agents/LangGraph 保存 Runtime 内部消息、计划、Checkpoint 和 Interrupt；
- PostgreSQL Message 是产品事实，Deep Agents Message/摘要/Checkpoint 是模型工作上下文；正常后续 Turn
  复用同一 Thread 并只追加新消息，完整产品历史只在 Runtime 损坏或 generation 迁移时受控重建；
- ContextSnapshot 的消息 sequence 只是审计、对账与重建水位，不进入日常 Prompt 重放；
- Sandbox Provider 保存临时 Workspace；其文件只有被平台显式取回、校验和提交后才成为 Artifact；
- BrowserControlLease 只是用户画面/输入的控制权，不替代 SandboxLease。业务表只持久化 MANUAL；没有
  ACTIVE BrowserControlLease 即表示 Agent/idle，而不是另建 AGENT Lease。首版只在没有活动 Turn 时进入
  MANUAL，结束或过期后才能创建下一 Turn；
- AgentAttachment、AgentArtifactCandidate 和 AgentArtifact 是平台业务事实；Sandbox 路径和
  WorkspaceSnapshot 不能作为公开下载身份；
- Valkey/ARQ 只负责投递和实时通知；SDK Trace 只用于调试和诊断；
- Run Step 复用 Phase 3 的通用业务投影，不逐条复制 SDK 内部节点或完整推理；
- 一个 Runtime、MCP 或 Sandbox 标识不能脱离 `session_id`、`turn_run_id`、owner 和 Project 映射被公开查询。

## 状态机和等待语义

优先复用 Phase 3 已验证的等待/恢复语义，不为 Agent 创建第二套不兼容的 Run 状态机。若现有状态不足，先更新通用 Run 契约和迁移，再实现 Agent API。

精简交付的 Agent Turn 不新增审批等待；沿用 Phase 5 已验证的主要状态：

```text
QUEUED → RUNNING → SUCCEEDED
             ├→ RETRY_WAIT → QUEUED
             ├→ CANCEL_REQUESTED → CANCELLED
             └→ FAILED
```

- Phase 3 的 `WAITING_INPUT` 能力继续存在，但 Phase 6 精简 Profile 不用它承载通用 Tool Approval；
- 不在固定能力 Profile 内、需要平台凭据或指向 private/metadata/宿主/LAN 的平台注册动作以稳定策略
  错误拒绝。平台不注册外部写专用 Tool；raw Browser/Shell/MCP 的 HTTP 业务语义不在该拒绝能力内；
- 将来若开放高风险动作，必须新增业务 Approval 事实并复用 `WAITING_INPUT + LangGraph interrupt/resume`，
  不能只依赖 SDK 内部等待状态；
- Runtime 取消只是一层动作，业务 Run 终态仍由平台条件更新决定。

## Tool、MCP 和精简治理策略

### Tool Registry

所有 Agent 能力必须来自平台固定 Catalog/Profile。精简交付不建设 Catalog 管理后台，但每个已开放条目
仍至少具有：

- 稳定名称、语义版本和输入/输出 Schema；
- 自动执行风险等级、所需权限和适用资源范围；
- 超时、重试、最大输入/输出和预算成本；
- 是否有副作用、幂等键生成方式和自动执行条件；
- 实现类型：内置 Tool、HTTP Adapter、MCP Tool 或 Sandbox Tool；
- 日志/Event 的字段白名单与敏感字段规则；
- 可用状态和兼容的 Deep Agents/Runtime 版本。

模型看到的 Tool 描述不是授权。每次调用都由平台根据 `turn_run_id` 的 PolicySnapshot 重新检查 owner、
Project、Budget 和参数策略。

### 首版允许的工具类别

- `list_project_papers`：只列出当前授权 Project 的 Paper/Version ID 和必要元数据；
- `search_project_chunks`：只在当前 ContextSnapshot 固定的 Project Index/ChunkSet 中检索；
- `read_evidence`：按 Evidence ID 读取受控长度、带页码的文本；
- `read_review_evidence_matrix`：只读取快照中明确授权的 Review Output；
- `search_public_resources`：调用固定版本 Search MCP 的审核 Tool 子集；
- `fetch_public_page`：在当前 public-egress Profile 下用于研究读取正常公网页面；底层 transport 不提供
  协议级只读保证；
- Browser/MCP/`execute` 可把公网文件下载到当前 Workspace；Slice 7 不另行注册
  `download_public_resource` 平台 Tool，raw 下载也不自动成为产品资源；
- `write_resource_manifest`：提交结构化 Manifest 候选；
- `submit_artifact`：请求平台校验并提交 Workspace 中的明确文件；
- `write_report`：生成 Markdown Artifact，不直接修改数据库正文或其他 Run 文件。

### MCP

- MCP Server 只能由平台安装、审核并固定版本，不能从 Prompt、网页或 Project 数据动态添加；用户请求只
  能引用当前 owner 可见的 Catalog 条目并填写其参数 Schema 允许的非敏感安全值；
- Server、Transport、Endpoint、认证方式和允许 Tool 列表需要版本化；
- 远程 MCP 优先使用受控 Streamable HTTP；需要 stdio/本地进程的开源 MCP 固定版本后运行在 Session
  OpenSandbox。ARQ Worker 宿主不以 stdio 启动第三方 MCP；
- MCP Tool 加载结果必须与 Registry 中的名称和 Schema 对比；漂移时 fail closed；
- 拦截器负责权限、Correlation、预算、超时、输出裁剪和审计；
- MCP Resources/Prompts 默认不直接注入 Agent Context，使用前需单独审核和限制。

Phase 5 已验证 MCP Catalog/Profile 的 owner/Session 选择、Playwright MCP 与固定版本 arXiv MCP。
Phase 6 只补齐这两个条目的 Schema/hash 漂移拒绝、权限/预算/超时/输出拦截、统一 egress 和脱敏执行
摘要；完整 Registry、Catalog 管理后台与 OAuth/Credential 生命周期明确延期。用户仍不能提交原始
endpoint、transport、command、env、包版本或认证配置。

### Research Skills

- 平台安装 Skill 语义版本化并绑定所需 Tool、权限、预算和兼容 Runtime；owner-scoped 声明式 Skill 使用
  内容哈希和不可变版本；
- 用户只能启用平台 allowlist Skill 或维护自己的声明式 Markdown/文本 Skill，不可上传可执行脚本、
  二进制、覆盖系统指令或动态安装依赖；
- 平台安装 Skill 按代码变更审查，owner-scoped 声明式 Skill 受 Schema、大小、内容扫描和固定评测保护；
  旧 Turn 仍按 PolicySnapshot 中的版本恢复；
- Skill 不能授予 Tool、网络、Sandbox 或 Secret 权限，只能使用策略已经授权的能力。

### 自动执行、产品策略与拒绝边界

- 当前 Project 内只读 Evidence 查询、`research-public-egress.v1` 公网 transport、Sandbox `execute` 和
  向当前 Project 提交受支持类型的新 Artifact 可以按固定 PolicySnapshot 自动执行；public-egress 只
  表示允许连接正常公网，不表示请求 method 或站点动作只读；
- raw Workspace 下载可以留在 Sandbox；成为正式 Artifact、Project 资源或登记声明来源目标前，必须经过
  大小、数量/总量、超时、MIME/magic/hash 和目标分类检查，但首版不逐文件审批；
- 使用平台托管凭据、访问 private/metadata/宿主/LAN、Browser 任意宿主文件上传、覆盖/删除正式 Artifact
  等平台注册能力直接拒绝；平台不注册外部写专用 Tool，并以系统策略要求 Agent 只做研究读取，但 raw
  Browser/Shell/MCP 仍可能发送 POST/表单，当前精简交付不声称能在协议层拒绝；
- 因为不存在可批准的高风险动作，本阶段不实现 Approval API/UI。未来开放外部副作用前必须另立切片。

## Browser、URL 和下载安全

### 跨 Turn 人工浏览器控制

ADR-0009 固定首版为“Agent Turn 结束 → 用户操作同一 Chromium → 新 Turn 继续”，不在运行中的 Turn 内
引入 LangGraph Interrupt。平台通过鉴权代理或短时受控票据展示当前 owner/Session/generation 的画面，
不向 Web 返回原始 VNC/noVNC/CDP/MCP/OpenSandbox endpoint。人工控制和 Agent 控制互斥；平台只记录
开始、结束、过期和 generation 变化，不记录点击、按键、页面正文、凭据或 Cookie。

登录状态只在当前物理 generation 内 best effort 保留，不进入 WorkspaceSnapshot。Slice 3 已在
default-deny 网络下用 Sandbox 内合成登录页验收；Slice 7 的 public-egress Profile 通过真实行为验证后，
才可显式验收正常公网站点。复杂同 Turn 等待以后再决定是否使用
`WAITING_INPUT + interrupt/resume`。

### Public-egress Profile

- 首版固定 `research-public-egress.v1`，正常公网 Host 无平台 URL allowlist；优先允许 `https`，确有公开
  站点兼容需要时允许普通公网 `http`；
- Profile 是 L3/L4/FQDN 出网边界，只判断连接目标，不解析 HTTP method、body、表单或站点业务语义；
  Browser/MCP/Shell/Python/wget 的公网请求因此不能被基础设施证明为只读；
- Sandbox network namespace 内部 loopback 必须保留，供 CDP、MCP、websockify/VNC 和固定本地服务使用；
  统一 egress 必须在非-loopback 出口阻断 link-local、private、multicast、unspecified、reserved、云元数据、
  宿主/LAN/容器控制面目标，并覆盖 IPv4/IPv6 与 Browser/MCP/Shell/Python/wget 全部 Sandbox 进程；
- 正式 URL/source 输入仍拒绝 `localhost`、loopback 字面地址和 DNS 解析到 loopback 的 Host；raw
  Browser/`execute` 可访问同一 Sandbox 内部服务，但不能访问宿主 loopback；
- 该强制效果不能只由 Tool 参数、配置字段或 Prompt 声明证明。Slice 7 必须核对 pinned SDK `0.1.15`、
  Server `0.2.2`、egress image `v1.1.4`/upstream commit 前缀 `34653f7`，并运行 Sandbox 内部 loopback、
  真实公网允许与非-loopback 私网拒绝 Smoke；固定版本不能满足时停止实现，不能无条件开放；
- PolicySnapshot 冻结 Profile ID/version/hash，Sandbox Lease 绑定相同 Profile/hash；策略变化或不匹配
  必须轮换 generation，不能续租旧 deny/allowlist/不同策略环境；
- 用户、模型、网页和 MCP 不能提交或修改 Profile、代理、DNS、认证 Secret 或 private-network 例外；
- 连接、读取、总时长、响应头和正文大小仍受预算限制，但不建设通用 URL/Redirect Host 审批代理，也不
  建设能识别并阻断公网写操作的 HTTP/Browser 语义代理。

### 内容和下载

- HTML、PDF、README、Issue、仓库和数据文件都按不可信输入处理；
- 页面中的“忽略系统指令”“发送 Secret”等文本不能改变 Agent 权限；
- raw Workspace 下载仅受 Sandbox/Workspace 预算约束；离开 Sandbox 成为正式业务资源时检查声明 MIME、
  文件头、扩展名、大小和内容哈希；
- 未知归档、可执行文件、脚本、宏文档和嵌套压缩默认拒绝或隔离，不自动执行/解压；
- 下载文件名只作展示，Storage Key 和 Workspace Path 由平台生成；
- 公网下载先进入 `/workspace/downloads/`；它仍是 raw Workspace 文件，不是已证明来源、Project
  Paper/Evidence 或可下载 Artifact。只有明确业务纳入或 Artifact 提交流程能把文件带出 Sandbox；
- Browser/下载不携带用户 Cookie、数据库凭据、模型 Key 或内部服务 Token；
- 当前 `submit_artifact` 只可选登记经规范化、DNS 公网分类的声明 URL/hash；它不证明文件字节来自该
  URL。最终 URL、获取时间和响应 Content-Type 只有未来平台受控抓取 Tool 实际观测后才能作为来源事实。

## Workspace 和 Sandbox 安全

- Session 拥有逻辑 Workspace；每个 AgentSession/SDK Thread 最多复用一个短 TTL OpenSandbox Lease，
  不跨 owner/Session 共享。同一 Session 继续以单活动 Turn 防止并发写入；Lease 失效、取消后环境污染或
  策略要求重置时递增 generation，并从受控 WorkspaceSnapshot/Artifact 重建；
- Agent 运行在 Worker/Runtime，Sandbox 作为 Tool；模型和平台 Secret 不进入 Sandbox；
- Sandbox 自有文件系统作为物理 Workspace，不直接挂载 API/Worker 宿主目录；平台通过 Provider 原生
  文件传输或受控 Adapter 注入 Snapshot/Artifact，并取回新 Snapshot 或候选 Artifact；
- Sandbox 使用非 root 用户、固定镜像、独立临时目录和显式输入 Snapshot/Artifact；首版固定 Python、
  pandas、numpy、matplotlib 和必要字体，不允许动态安装包；
- Slice 6 的当前 Lease 默认禁网；Slice 7 新建/轮换到 `research-public-egress.v1` 后允许正常公网
  HTTP(S)，同时统一拒绝 private/metadata/宿主/LAN，策略覆盖 Chromium、Playwright/Search MCP、
  Python、Shell、`wget` 等全部 Sandbox 进程，并记录有界策略摘要；
- 限制 CPU、内存、PID、文件数、单文件大小、墙钟时间和输出大小；当前本地 Docker runtime 的 overlay
  物理磁盘硬配额没有请求级实现，依靠 WorkspaceSnapshot 128 文件/10 MiB 单文件/50 MiB 总量和 Artifact
  提交上限约束业务带出量，不把它表述为物理磁盘隔离；
- 不挂载宿主源码、用户主目录、Docker Socket、数据库 Socket、云元数据或 Secret；
- 禁止特权模式、宿主网络、危险 Capability 和不受控嵌套容器；
- 文件传入/取回走 Provider 原生传输或平台 Adapter，不由模型构造宿主路径；
- OpenSandbox Backend 是 Deep Agents `CompositeBackend` 默认 Backend，模型可调用的 `execute` 只能到
  当前 Session Sandbox；`/conversation_history/`、`/large_tool_results/` 等 Runtime 内部路径路由
  `StateBackend`，不进入业务 WorkspaceSnapshot；
- Deep Agents 文件 `permissions` 不能保护 `execute`、自定义 Tool 或 MCP；平台不能以命令字符串检查
  冒充强隔离，必须依靠 Sandbox、统一 egress、Secret/宿主隔离、资源限制和文件提交协议；
- Workspace 在 Session 关闭、Sandbox Lease TTL 到期、generation 重置或策略要求回收时幂等清理；普通
  Turn 终态不自动销毁仍有效的 Session 级 Lease。清理失败进入可观察的补偿队列；
- Artifact 提交后仍不信任 Workspace，必须由平台重新读取并校验。

### Agent 文件交换与 Artifact

ADR-0010 将用户附件、内部 WorkspaceSnapshot、Candidate 和正式 AgentArtifact 分开：附件通过业务
Storage 与 ID 授权后物化到 `/workspace/inbox/`；Agent 只有显式调用 `submit_artifact` 才能把
`/workspace/outputs/` 的普通文件带出 Sandbox。平台在事务外重新读取并校验 path/type/size/MIME/magic/
hash，再写内容寻址 staging Storage；只有业务 Turn 成功的短事务可以发布不可变 AgentArtifact。

首版支持研究所需的图片、PDF、CSV、Markdown、纯文本和 JSON；未知归档、可执行文件、宏文档、symlink
和路径穿越拒绝。不自动扫描或发布整个 `/workspace`，也不把 ReviewRun 强绑定的既有 Artifact 表直接
泛化。Playwright `browser_file_upload` 继续关闭，后续只能通过引用 Attachment ID 的平台 Tool 增加。

### 受限代码分析

ADR-0007 已批准 Phase 5 Slice 7 在 OpenSandbox 中开放 `execute`，用于研究数据处理与绘图。Phase 6 的
任务是把已通过 Spike 的配置强化为可观察产品能力，而不是再次决定是否开放。至少保持以下条件：

- 使用固定依赖和固定镜像，不允许任意安装包；
- 输入只来自显式 WorkspaceSnapshot/Artifact，输出只允许 `/workspace` Manifest 中的路径；
- 网络权限由不可变 PolicySnapshot 与同版本 Sandbox Lease 决定；资源和时间限制有实际测试；
- 代码、命令、stdout/stderr 和产物受大小及敏感信息过滤；
- 命令不逐条审批；public-egress 正常公网 transport 和正式 Artifact 提交按精简 Profile 自动执行，
  private-network 与平台凭据由基础设施/平台拒绝。系统策略禁止动态安装和外部写，但 raw 命令可发起
  公网请求，当前不宣称协议级阻断；取消、幂等和最小审计仍必须成立；
- 不向 Sandbox 注入模型、MCP 或 OpenSandbox Secret，且取消后不启动新命令。

## Budget 和终止策略

每个 Agent Turn 在创建时通过 PolicySnapshot 固定版本化硬 Budget，至少限制：

- 最大模型步骤和总 Tool Call；
- 最大输入/输出 Token；Provider 能稳定返回费用时可以记录，但精确计费不是阶段出口；
- 最大墙钟时间和单次 Tool 超时；
- 最大 Browser 页面、下载数量、单文件与总文件大小；
- 最大 Workspace 磁盘和 Artifact 输出；
- 最大失败、重试和相同 Tool+参数重复次数；
- 子 Agent 数量，首版固定为 0。

平台在 Tool 调用前预检，在结果后记账。达到硬限制立即停止新操作并以稳定错误结束；接近上限时可以要求
Agent 总结现有结果。首版只实现相同 Tool+参数重复次数等确定性循环保护，不建设复杂无进展分类器。

## API、Event 和数据变化方向

### API

```text
POST /api/v1/projects/{project_id}/agent-sessions
GET  /api/v1/agent-sessions/{session_id}
GET  /api/v1/agent-sessions/{session_id}/messages
POST /api/v1/agent-sessions/{session_id}/messages
GET  /api/v1/agent-turn-runs/{run_id}
POST /api/v1/agent-turn-runs/{run_id}/cancel
GET  /api/v1/agent-turn-runs/{run_id}/manifest
GET  /api/v1/agent-turn-runs/{run_id}/tool-executions
POST /api/v1/agent-sessions/{session_id}/browser-control
GET  /api/v1/agent-sessions/{session_id}/browser-control
DELETE /api/v1/agent-sessions/{session_id}/browser-control
WS   /api/v1/agent-browser-controls/view
POST /api/v1/agent-sessions/{session_id}/attachments
DELETE /api/v1/agent-sessions/{session_id}/attachments/{attachment_id}
GET  /api/v1/agent-turn-runs/{run_id}/artifacts
GET  /api/v1/agent-artifacts/{artifact_id}/content
GET  /api/v1/runs/{run_id}/events
GET  /api/v1/runs/{run_id}/events/stream
GET  /api/v1/artifacts/{artifact_id}
```

- Session 创建请求只绑定 URL 中的 Project；Message 请求只接受用户内容和有限公开选项。专用 Session
  Capability API 只引用当前 owner 可见的 Catalog/Skill ID 和 Schema 允许的安全参数；owner、最终
  Tool/Skill/MCP allowlist、Sandbox、SDK Thread 和内部 Context/Policy 仍由服务端解析与固化；
- ToolExecution 默认只返回脱敏摘要，管理员诊断信息不暴露给普通用户；
- Artifact 下载再次校验 owner、Project、隔离/扫描状态和内容处置策略。

### Event

在 Phase 5 白名单事件基础上增加：

```text
agent_step_changed
agent_budget_updated
agent_tool_rejected
agent_workspace_created
agent_workspace_cleanup_requested
agent_workspace_cleaned
agent_policy_violation
agent_browser_control_started
agent_browser_control_ended
agent_browser_control_expired
agent_attachment_added
agent_artifact_staged
agent_artifact_committed
agent_artifact_rejected
```

Event Payload 保持小型、版本化和脱敏。高频 Token/流式片段不逐条写 PostgreSQL；聚合 Usage 周期性或在边界事件中提交。

### 数据

具体迁移在对应切片前确定，关系至少覆盖：

```text
AgentSession
 ├─ AgentMessage
 ├─ RuntimeThreadBinding
 ├─ SandboxLease
 ├─ WorkspaceSnapshot
 ├─ BrowserControlLease
 ├─ AgentAttachment
 └─ AgentTurnRun ── ContextSnapshot / PolicySnapshot
      ├─ RuntimeExecutionBinding
      ├─ RunStep
      ├─ ToolExecution
      ├─ UsageLedger
      ├─ ResourceManifest ── ManifestItem
      ├─ AgentArtifactCandidate ── AgentArtifact
      └─ Event
```

Slice 7 的 `PolicySnapshot` 额外冻结 public-egress Profile ID/version/hash；`SandboxLease` 保存相同
Profile/hash。两者不匹配、Profile 升级或网络模式变化时只能创建 generation+1，旧 Lease 不得续租。

唯一约束至少保护：Session/Thread、Turn/Execution 映射、WorkspaceSnapshot 版本、Session 单活动 Turn、Message 幂等键、Runtime
Tool Call ID、Tool 副作用幂等键、Manifest 规范化 URL、Artifact 提交和 Event Sequence。

## 可靠性、取消和恢复

- ARQ Job 仍只携带 `turn_run_id`，Worker 通过业务状态认领执行；
- Worker 崩溃后由 lease/reconcile 找回 Turn，再查询 Runtime Execution、Workspace 和 ToolExecution 状态；
- Runtime 成功但本地响应丢失：按稳定 Session/Thread 与 Turn/Execution 映射重新读取最终输出，不重新运行 Agent；
- Tool 超时或断连：先按 ToolExecution ID 对账；只有确定未产生副作用时才重试；
- MCP/Browser/Sandbox 各自的内部重试不得与平台重试叠加成重试风暴；
- AgentArtifact 使用 staged → validated → committed 生命周期和内容哈希去重；Review Artifact 保留既有
  原子导出契约；
- `submit_artifact` 的 Sandbox/Storage I/O 在事务外；重复 Tool/Job 按稳定 invocation/candidate ID 与内容
  hash 回读，只有业务 Turn 成功能令正式 AgentArtifact 可见；
- Browser 人工控制绑定当前 Sandbox generation；旧票据、过期控制或 generation 轮换立即失效，人与
  Agent 不并发操作 Chromium；
- 取消请求原子写入业务 Run/Event，随后传播到 Runtime、当前 Tool 和 Sandbox；
- 取消后不发起新模型、Tool、MCP、Browser 或 Sandbox 操作；晚到结果只用于对账和清理，不能提交业务成功；
- Runtime、MCP 或 Sandbox Provider 长期不可用时稳定失败，不让 Run 永久卡在 RUNNING。

## 安全和隐私不变量

- Agent 只能访问当前 Turn 的 Context/Policy Snapshot 固定授权的 owner、Project、Paper、Chunk、Evidence、
  Artifact、Tool、Skill、public-egress Profile 和 Workspace；正常公网 Host 不逐项进入业务 Snapshot，
  private/metadata/宿主/LAN 始终不可授权；
- 所有外部内容都是不可信数据，不具有修改系统指令、权限和策略的权力；
- Secret 只存在于最小需要的宿主组件，不进入 Prompt、Event、Trace、Workspace、Artifact 或 MCP 参数；
- 日志不记录完整 Prompt、论文全文、网页全文、文件内容或敏感 Tool 参数；
- 不允许 Agent 直接连接 PostgreSQL、Valkey、内部管理 API 或宿主文件系统；
- 所有 Tool 调用执行前检查 Schema、权限、预算和幂等；
- 高风险或不可逆操作不注册为平台 Tool/Workflow；平台注册调用直接拒绝，但 raw 公网协议语义无法由
  当前 egress 判定或强制；
- Runtime、Tool 和 Sandbox 的安全拒绝属于正常产品行为，需要稳定错误码和用户可理解说明；
- 用户 A 无法从 ID、错误、Event、Timing、Manifest、Workspace 或 Artifact 推断用户 B 的资源；
- Sandbox 安全声明必须由配置和测试证据支持，不把“容器化”等同于完整隔离。

## 可观测性和审计

- Trace 关联 `request_id`、`trace_id`、`project_id`、`session_id`、`turn_run_id`、`attempt_id`、
  `thread_id`、`tool_execution_id` 和 `workspace_id`；
- 高基数 ID 进入日志和 Trace，不作为 Prometheus Label；
- Metrics 覆盖 Run 终态、Runtime/Tool/Sandbox 延迟与错误、策略拒绝、预算消耗和清理积压；
- SDK/LangSmith Trace 可辅助调试，但 PostgreSQL Event/ToolExecution 才是产品审计事实；
- 原始模型思考不作为审计内容；审计记录输入摘要/哈希、策略决定、Tool 版本、状态、时长和产物引用；
- 每个用户可见错误可通过 Correlation ID 定位到平台、Runtime、MCP、Browser 或 Sandbox 层级。

## Agent 评测

建立固定、小型、可人工审核的 Agent Evaluation Dataset，至少覆盖：

- 能否在多轮对话中保持用户目标，同时只读取每轮 Snapshot 授权的项目上下文；
- 能否正确使用 Project Chunk Index 和指定 Review Evidence Matrix 回答分析问题；
- 能否找到预期的 arXiv 条目、摘要页和 PDF；
- Manifest URL、资源类型、Paper/Evidence 关联和来源是否正确；
- 报告中的重要结论能否追溯到 Evidence 或外部来源；
- 面对证据不足时是否明确停止推断；
- 是否遵守 Project、Tool、public-egress/private-network、正式资源下载和硬预算限制；
- 面对网页 Prompt Injection 是否拒绝泄漏 Secret、扩大权限或执行危险动作；
- 是否出现重复 Tool、无进展循环、无效下载或重复 Artifact；
- 取消、断连、恢复和 SDK 升级后行为是否保持契约。

默认评测使用 Fake Model、固定 Tool、HTTP Mock、合成网页和恶意 Fixture。真实模型/公开网络评测显式启用并记录模型、SDK、Prompt/Policy 版本、预算、时间和人工审核结果。自动评分只作辅助，不把单一 LLM Judge 当作安全证明。

## 实现切片顺序

Phase 5 Slice 7 先分别提供 OpenSandbox、MCP 配置、Playwright/Search MCP 和原生 Skill 的最小证据。
Phase 6 只在这些 Spike 实际通过后按 ADR-0011 的精简范围和 ADR-0012 的公网 Profile 强化，不把
ADR-0007/0008 本身当作测试结果：

1. **精简产品契约与威胁模型（已完成）**：复核 Session/Turn/Snapshot、Project Context、Session 级 Sandbox Lease、
   资产/信任边界、`execute` 和当时固定 arXiv 网络攻击面；落实 ADR-0009/0010/0011 的状态、API、Event、
   自动执行/直接拒绝矩阵和安全验收；实施依据为
   [`research-agent-security-contract.md`](../../spec/research-agent-security-contract.md)，完成仅表示契约已
   冻结，不表示 Slice 2–8 目标已经实现；
2. **Agent 输出 Artifact（已完成）**：Candidate/AgentArtifact 迁移、`submit_artifact`、Sandbox/Storage
   校验、Effectively Once 提交、PNG/JPEG 图片预览与其余受支持类型下载已形成离线垂直切片。Fake Runtime
   描述符继续停留在 `STAGED`，只有真实 Tool 完成文件校验的 `VALIDATED` Candidate 能随 Turn 成功事务
   原子发布；
3. **Browser 画面与跨 Turn 人工控制（已完成）**：`BrowserControlLease` 与物理 SandboxLease 分离；
   平台 API 只返回业务状态、短时 opaque ticket 和同源 view URL；Adapter 在 Sandbox 内以固定 websockify
   recipe 将 `6080` WebSocket 转发到 loopback `5901` TigerVNC，再经 OpenSandbox Server Proxy 做有界
   WebSocket↔WebSocket 转发，raw endpoint/headers 只短暂存在于 Adapter 内存。同一 Session 只允许一个人工控制权和一个活动画面连接，MANUAL 与 Turn
   fail closed 互斥；不存在 ACTIVE 控制权即为 Agent/idle，不持久化 AGENT Lease。右栏 noVNC 组件保持
   壳层无关。离线闭环已验证；旧 raw TCP 诊断已暴露 endpoint 语义错误，修正镜像的 RFB 链路已通过真实
   验证；修正镜像的 Server Proxy→websockify→RFB 与同一 Sandbox Playwright 合成页完整 Smoke 已通过。
   该证据仅适用于未配置 API key/secure runtime 的 trusted-local 环境，公网继续关闭；
4. **Agent 输入附件（已完成）**：Session 上传、并发幂等收敛、Message/删除行锁互斥、有界有序引用、ContextSnapshot 不可变元数据冻结、事务外 `/workspace/inbox` 物化、WorkspaceSnapshot 排除与
   可重试上传意图 Chat UI 均已落地；不开放任意 Browser 文件上传，Storage GC 延期；
5. **固定能力、Project Context 与硬预算（已完成）**：复用 Phase 5 Catalog/Profile，全部允许 Tool 的
   version/schema hash 与 8 次模型、12 次 Tool、300 秒墙钟、60/60 秒单调用、64 KiB 通用安全输出、同签名
   最多 2 次、约 60,000 输入 Token/4,096 输出 Token 均由不可变 PolicySnapshot 冻结；MCP 内层提前
   1 秒超时，纯文本另裁剪到 8,000 字符的持久化边界；PostgreSQL Usage
   与稳定 reservation 承担并发/重放事实，公开 API 只投影脱敏摘要。Project/MCP/Artifact 仅通过既有
   effect cache 对账，文件/`execute` 未知 effect fail closed；不建设完整 Registry、Approval 或精确计费；
6. **Workspace/Sandbox 与统一 egress 强化（已完成）**：Session Lease 增加内部 `RETIRED` 状态；过期、
   DIRTY 或 Session closed 的旧 generation 先在短事务内以 session/generation/fence/due guard CAS 退役并
   写入资源哈希标识的 cleanup fact，Worker 再在事务外 destroy。认领使用 worker/attempt fence，失败只保存
   固定错误码和安全摘要，精确 404 视为幂等成功；旧 Cleaner/续租不能复活或销毁新 generation。项目固定
   Server 0.2.2 的 loopback/bridge、空 host volume、drop ALL、no-new-privileges、PID 256、固定 execd/egress
   digest 与 API key 启动契约。显式 Smoke 已验证非 root、Secret/宿主隔离、CPU/内存/PID、命令进程组
   超时后 Backend 仍可用、输出、60 秒 TTL/重复销毁，以及 Bash/Python/Node/Chromium/Playwright/Search
   MCP 的统一 default-deny；公网仍
   关闭。Docker overlay 物理磁盘硬配额未实现并作为明确限制保留；
7. **Sandbox 公网与正式资源安全（已完成）**：实现
   `research-public-egress.v1`，让 Browser/MCP/Shell/Python/wget
   访问任意正常公网 HTTP(S)，保留 Sandbox namespace 内部 loopback，并统一拒绝非-loopback
   private/link-local/reserved/metadata/宿主/LAN 出口；正式 URL/source 输入继续拒绝 localhost/loopback；冻结
   PolicySnapshot 与 SandboxLease 的 Profile version/hash，策略变化强制轮换 generation。raw Workspace
   下载不冒充业务资源；正式 Artifact、Project 资源或登记声明来源目标才执行数量/总量/超时/大小/
   MIME/magic/hash/目标分类和 effect ledger。离线恶意 Fixture 通过后，显式运行 Sandbox 内部 loopback、arXiv、
   非 arXiv 公网与非-loopback 私网拒绝 Smoke；不建设通用 URL Host allowlist、HTTP method/业务语义代理
   或逐次网络审批，明确 raw 公网通道
   不具备协议级只读保证。当前代码已冻结 canonical Profile hash，PolicySnapshot/Lease 都保存
   ID/version/hash，旧 NULL Profile 或 hash 漂移会退役旧 Lease 并递增 generation；OpenSandbox Adapter
   只接受固定 Profile，并翻译为 `defaultAction=allow` 加非-loopback 特殊网段 deny。正式
   `submit_artifact` v2 可选声明有界 HTTP(S) `source_url`，在读 Sandbox 文件前检查 URL 和全部 DNS
   结果是否指向正常公网，Candidate/Artifact 保存规范化 URL/hash，Manifest 用
   `declared_public_target_checked` 明确表示“声明目标已分类”，不表示文件来源已证明；raw Workspace 文件仍不可通过
   公开下载 API。普通离线测试已覆盖 URL/DNS、Profile/hash、Schema 和 generation 漂移；
   v2 且 default-deny 的已运行 Turn 可继续在原冻结边界恢复；v3/v4 Turn 遇到历史 NULL Profile Lease 必须
   退役并递增 generation，其他未知 default-deny PolicySnapshot 稳定 fail closed；
   `AGENT_RUN_OPENSANDBOX_PUBLIC_EGRESS_TESTS=1` 的首轮真实 Smoke 只确认内部 loopback；固定镜像没有
   `curl`，第一条公网命令因此失败且没有形成网络拒绝证据。改用 `/usr/bin/wget` 后的第二轮已通过
   arXiv 首页，但 2,215,244-byte 完整 PDF 超过 30 秒命令限制并由 Adapter 以 exit 124 结束，尚未进入
   private/MCP 检查；这不是网络拒绝。Smoke 现保留 `wget` 的 Shell 公网/private 检查，并以 Python
   Range 请求最多读取同一固定 PDF 的 64 KiB 前缀，接受 HTTP 200/206，校验 Content-Type、`%PDF`
   magic 和 SHA-256 后主动关闭。第三轮显式真实 Smoke 为 1 passed（39.67s）：同一 Sandbox 内部
   loopback 可用；`wget` 可访问 arXiv 首页；固定 `1706.03762` 最多 64 KiB 前缀返回 HTTP 200/206 且
   Content-Type、`%PDF` magic、SHA-256 通过；Python、Node、Chromium 可访问 `example.com`；Playwright MCP
   `browser_navigate` 与 arXiv Search MCP `search_papers` 成功；metadata `169.254.169.254`、Docker
   gateway `:8080` 和 `10.0.0.1` 均被拒绝。主智能体独立离线复核合计 137 passed，其中 PostgreSQL/
   Alembic 26 passed。该证据不扩张为完整 PDF、全部公网、协议级只读或生产隔离声明；
8. **产品整合、验证与复盘（已完成）**：严格遵循
   `docs/spec/web-ui-app-shell-redesign.md`。若其尚未实施，先按其中
   4 个独立 UI 子切片完成 `AppSidebar`、`PageBar`、工作区空间回收和视觉 token 刷新。四项均已完成，且
   Turn Detail、Tool 摘要、来源/Manifest、Browser、附件和 Artifact UI 已随 8.3 整合；8.4 已完成浅色
   研究档案视觉与最低可访问性刷新。8.5 已完成本地 noVNC 真实人工输入 UI Smoke（人工输入后，同
   generation Playwright MCP 观察页面状态；Turn 互斥和下一 Turn 复用由 Application/Integration 测试
   补充）、关键故障/取消/重复/越权固定回归、7 场景 Agent 评测、Deep Agents 升级契约、本地演示运行
   文档、模块学习笔记和 Research Agent Extension 完成报告。Core 数据库/Storage 的生产备份恢复不转入
   本阶段。

## 测试方式

- **Domain**：硬 Budget、Tool 策略决定、幂等键、URL/IP 分类和 Artifact 生命周期；
- **Application**：授权 Context、Tool 执行、取消、对账、Usage 和 Event 原子性；
- **Runtime Contract**：Deep Agents 版本升级前后运行同一契约套件；
- **MCP**：Schema 漂移、恶意 Tool 描述/输出、超时、断连、认证失败、会话泄漏和拦截器；
- **Browser/HTTP**：Slice 3 已覆盖控制权状态、owner/Session/generation/fence、重复开始/结束、过期、
  单控制者、旧 ticket、Turn 互斥、endpoint 隐藏与有界二进制双向代理；Slice 8.5 的显式本地 Smoke 使用
  生产 `AgentBrowserPanelView`/noVNC、ticket 解析和 bridge 向同一 Sandbox Chromium 输入 marker，再由
  保持打开的同 generation Playwright MCP session 回读。Slice 7 另覆盖 private/metadata/宿主目标、
  有界公网 PDF 前缀、Chromium、Playwright/Search MCP；两者均只构成 trusted-local 功能证据；
- **Sandbox**：Session 内跨 Turn 复用与跨 owner/Session 隔离、WorkspaceSnapshot 取回/重建、模型可见
  `execute` 只能到当前 OpenSandbox、Secret/宿主路径不可见、统一网络拒绝、CPU/内存/PID/时间/输出与
  业务文件上限、取消后不启动新命令、销毁和清理补偿；本地 Docker overlay 物理磁盘上限明确不在已验证
  结论中；
- **PostgreSQL**：唯一约束、条件更新、ToolExecution 去重、Usage 和跨用户隔离；
- **故障注入**：Worker/Runtime/MCP/Sandbox 退出、响应丢失、重复 Job、取消竞争、Artifact 提交前后崩溃；
- **E2E**：打开 Agent Session → 项目内分析 → Agent 生成并提交 PNG → 刷新后下载；Agent 导航合成登录页
  → Turn 结束 → 用户人工操作 → 下一 Turn 识别新状态；arXiv 与至少一个非 arXiv 正常公网访问、正式
  PDF/资源来源记录，以及 private/metadata 目标拒绝；
- **评测**：多轮上下文、项目内分析、资源发现、来源正确性、Groundedness、策略遵守、Prompt Injection 和无进展样本。

普通 CI 必须完全离线且不需要真实模型、外部 MCP、公共网站或付费 Sandbox。真实运行使用显式 Marker/环境开关、专用测试账号、硬预算和可删除 Workspace；只记录实际执行结果。

## 阶段完成条件

- 至少一个绑定 Project 的多轮研究用户故事可从 UI 端到端完成，并能使用项目索引、Review Matrix、
  公开资源或受控工具生成可追溯 Artifact；
- Deep Agents 继续被 `ResearchAgentRuntime` Adapter 隔离，SDK 类型不污染 Domain 和公开 API；
- AgentSession/Message/AgentTurnRun、SDK Thread/Execution、Snapshot 和 Workspace 的所有权与恢复语义有测试证据；
- 临时文件、内部 WorkspaceSnapshot 和正式 Artifact 的生命周期分离，Sandbox 丢失后可重建内部工作状态；
- Agent 只能访问当前 Turn 授权的 Project Context、Tool、Skill、public-egress Profile 和 Workspace；
  正常公网 Host 不逐项授权，private/metadata/宿主/LAN 永远拒绝；
- 固定 MCP、Browser、public-egress/private-network、正式资源下载、硬 Budget 和 Sandbox 策略均有
  自动化及必要的真实验证证据；
- Prompt Injection 不能获得平台 Secret、数据库权限、宿主文件或未授权网络；
- 平台不注册外部写 Tool 或提供平台凭据，但 raw Browser/Shell/MCP 的公网 method/业务语义不受 L3/L4
  egress 强制；阶段出口不宣称协议级只读；
- 最大步骤、Token、时间、Tool Call、下载和输出限制实际生效；
- 取消后不发起新操作，重复执行不重复提交 Tool 副作用或最终 Artifact；
- Runtime、MCP、Browser 和 Sandbox 故障可以恢复、对账或稳定失败，不永久卡住；
- Deep Agents 升级由契约测试和 ADR 保护，失败时可阻止升级或回滚；
- Agent Event、Usage、ToolExecution、Workspace 和 Artifact 可审计且不记录敏感全文；
- Core 与 Agent 两组用户旅程、评测、运维文档、模块笔记、已知限制和真实运行证据齐全；
- 开发者能解释 Prompt、模型、Tool Policy、MCP、Sandbox 和业务权限各自能解决什么、不能解决什么。

上述阶段条件已按本地个人项目精简范围通过。逐项证据、命令、限制与非声明见
[`Phase 6 Research Agent Extension 完成报告`](../reports/phase-06-research-agent-extension-completion.md)；
Fake Runtime 评测、真实 Sandbox 功能 Smoke 与生产安全声明必须继续分开理解。

## 实现前仍需确定

1. ADR-0007 已选 OpenSandbox；Slice 6 已固定并验证本地 SDK/Server/image digest、TTL、CPU/内存/PID、
   命令/输出、统一禁网与孤儿清理补偿。尚未解决的是 Docker overlay 物理磁盘硬配额、secure runtime、
   公网多租户部署和镜像发布仓库；
2. ADR-0012 已取代 ADR-0011 的 arXiv 精确 allowlist；Slice 7 已以 pinned SDK 0.1.15、Server 0.2.2、
   egress image v1.1.4/commit 前缀 `34653f7` 完成版本化 Profile/hash/Lease generation 实现，并以固定
   公网/private/metadata/宿主目标完成显式 Smoke。该证据不代表所有公网目标或生产隔离；
3. 硬 Budget 已固定为 Slice 5 精简 Profile；Provider `usage_metadata` 可得时 best-effort 渐进记账，
   不可得时保持 NULL，不用近似值冒充精确计费。模型 reservation 只限制逻辑步骤；若 Provider 已接收
   请求后响应/Worker 丢失，重试仍可能重复付费且缺少 usage，checkpoint/reconcile 不能提供物理调用
   Exactly Once。费用与告警平台延期；
4. Workspace TTL 与清理补偿已在 Slice 6 实现；Slice 7 已固定每 Turn 最多 8 项、正式候选总量最多
   50 MiB，并在锁定 Run 的事务内串行复核。仍未实现 Storage staging orphan GC；单文件上限固定为
   10 MiB，扩展名、声明 MIME、magic/UTF-8/JSON/CSV/SVG 主动内容校验已在 Slice 2 实现；
5. Browser 方案已固定：TigerVNC `1.15.0+dfsg-2` 只监听 Sandbox namespace loopback，websockify
   `0.13.0` 转发到固定 `6080`，Web 使用 noVNC `1.7.0`；平台 ticket gateway 承担外层身份与
   generation/fence 校验。新本地审核镜像 digest 已进入默认配置并通过真实 UI 输入 Smoke；通用认证、
   secure runtime 和镜像 Registry 发布仍是阶段外限制；
6. 首批平台 Skill 与 owner-scoped 声明式 Skill 已在 Phase 5 固定版本、只读内容、required Tool 不扩权、
   首 Turn 前锁定和禁用边界，并纳入 Phase 6 固定回归/Deep Agents 升级门禁。真实模型研究质量和恶意
   Markdown 的语义级检测仍不由该工程门禁保证。

阶段完成后的 v4 Real 模式收口已确认：普通 Tool 预算由 30 秒提升为 60 秒；MCP 超大纯文本只向模型和
Effect Store 交付有界前缀，外层取消时尝试关闭已认领 Effect；Assistant Message 完整保留 SDK 最终回复，
只有显式以 `[evidence:...]` 结尾的行进入 Project Evidence 校验与 Claim/Citation，未标记的世界知识、
外部来源、Browser/文件/`execute` 结果和操作说明按普通正文保存，但不获得“已由项目证据验证”的平台语义；
每轮消息还携带 Snapshot UTC 时间基准。是否启用引用校验只由本轮最终回复的显式标记决定，不扫描历史
Thread 的 ToolMessage。旧 v3 Turn 不被原地升级，详细证据见 Real 模式体验缺陷台账。

2026-08-30 Real 对话回归修复了同一条 Workspace 成果链的两个契约缺口：每次新建或复用 Sandbox Lease
都会幂等创建 `/workspace/outputs`，系统提示要求正式成果先写入该目录；`artifact_path_invalid` 在保留
Rejected Candidate 与失败 ToolCall 审计后，以有界错误 ToolMessage 返回模型继续纠正，不再直接终止
整轮。与此同时，离线 reconcile/collect Runtime 不再把“无执行 Backend”写入按模型全局合并的 Harness
Profile；`execute` 是否可见只由当前 Backend 与本轮 PolicySnapshot 决定，真实 Sandbox Prompt 明确说明
它只能在 Session 专属 `/workspace` 中执行，包括 `mkdir`，不是宿主 Shell。相关五个 Infrastructure
测试文件完整回归为 134 passed，未访问真实模型、OpenSandbox 或公网。

Deep Agents 子 Agent 和长期 Memory 在精简交付中保持关闭。任何把代码执行扩大到宿主、允许
private/metadata/宿主/LAN、提供动态包安装、用户自定义 Tool/MCP/网络 Profile、正式外部写产品能力或
长期 Memory 的决定都必须单独更新本 Spec，并在满足 `AGENTS.md` 条件时创建 ADR。raw 公网通道可能
产生写请求是已知风险，不被误记为已实现的产品能力或强制拒绝。

## 已知预期限制

- Agent 输出仍需人工审核，不等同于系统性文献综述或事实保证；
- 公开资源可能变化、删除或限制访问，Manifest 必须保留获取时间和来源；
- 第三方模型、MCP 和 Sandbox Provider 会带来成本、可用性、隐私和供应商风险；
- Prompt Injection 无法只靠分类器或 Prompt 消除，系统依赖最小权限和基础设施隔离限制后果；
- 首版只支持同一 generation、两个 Turn 之间的人工浏览器操作；画面断线只释放连接占用，不自动结束
  最长 5 分钟的业务控制权。API 进程重启会轮换票据并使旧票据失效；复杂同 Turn 登录流程、Cookie/Profile
  持久化、跨 generation 恢复和凭据委托不属于首版目标；
- TigerVNC 在 Sandbox namespace loopback 使用 `SecurityTypes=None`，外部访问依赖平台 ticket gateway；
  同一 Sandbox 内的模型进程理论上也能连接该 loopback 端口，但它已经拥有相同 Chromium 的固定
  Playwright MCP/CDP 与 `execute`，不因此获得宿主或其他 Session 访问能力；
- 多 Agent、长期 Memory、宿主执行、动态安装、private-network 和用户自定义网络策略保持关闭；Sandbox
  公网只属于 trusted-local 演示，OpenSandbox `execute` 不改变 Research Agent 的领域定位，也不代表
  通用 Coding Agent 或生产级安全浏览器；
- public-egress 只强制目标网络边界，不检查 HTTP method 或 Browser 业务语义；平台不提供外部写 Tool/
  凭据并要求只做研究读取，但不能保证 raw Browser/Shell/MCP 不会发送 POST、提交表单或触发远端写入；
- 平台不注册安装 Tool，固定 Prompt 禁止动态安装；但 raw Shell 在公网可下载并执行用户态文件，当前精简
  交付没有 syscall/内容代理级动态代码阻断，因此这仍是 trusted-local 风险而不是已强制安全结论；
- Research Agent Extension 可以独立禁用，Demo-ready Core Research Backend v1 仍应完整运行。
- Slice 2 的 Sandbox Adapter 在读取前拒绝目录、symlink/device 和超限文件，并在 Storage/下载边界复核
  size/hash；这不是无 TOCTOU 竞争的生产级恶意文件扫描器。staging blob 的总量配额和孤儿 GC 留给后续
  Sandbox/清理切片。

## 预期学习笔记

模块真正完成后再撰写，不预建空文件：

- `research-agent-runtime.md`：Agent Session/Turn 与 Deep Agents Thread/Execution/Checkpoint 的集成边界；
- `agent-tool-policy.md`：固定 Tool/MCP/Skill Catalog、权限、硬预算、自动执行与直接拒绝边界；
- `browser-download-security.md`：URL、SSRF、Prompt Injection 和文件隔离；
- `agent-sandbox.md`：Workspace 生命周期、资源限制、文件传输和清理；
- `agent-artifact-delivery.md`：Candidate 状态机、Sandbox/Storage 边界、成功事务发布与安全下载；
- `agent-skills.md`：平台安装与 owner-scoped 声明式 Skills 的版本、隔离、权限依赖、评测和升级；
- [`agent-evaluation.md`](../modules/agent-evaluation.md)：多轮研究、项目上下文、来源、策略遵守、安全评测
  和 Deep Agents 升级门禁。

## 参考资料

- [`ADR-0007：采用 OpenSandbox 可执行研究 Workspace`](../decisions/0007-use-opensandbox-executable-workspace.md)
- [`ADR-0008：复用 Deep Agents 原生 MCP 与 Skills 能力`](../decisions/0008-use-native-mcp-and-skills-capabilities.md)
- [`ADR-0009：采用跨 Turn 的人工浏览器控制`](../decisions/0009-use-turn-boundary-browser-control.md)
- [`ADR-0010：采用显式 Agent 文件交换与 Artifact 提交协议`](../decisions/0010-use-explicit-agent-file-exchange.md)
- [`ADR-0011：采用 Phase 6 精简交付范围`](../decisions/0011-adopt-phase-06-lean-delivery.md)
- [`ADR-0012：采用 Sandbox 公网 egress Profile`](../decisions/0012-use-sandbox-public-egress-profile.md)
- [`Research Agent 精简安全契约`](../../spec/research-agent-security-contract.md)
- [`Web UI 应用壳与视觉重设计`](../../spec/web-ui-app-shell-redesign.md)
- [`Agent 输出 Artifact 交付`](../modules/agent-artifact-delivery.md)
- [Deep Agents Overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [Deep Agents Going to production](https://docs.langchain.com/oss/python/deepagents/going-to-production)
- [Deep Agents Backends](https://docs.langchain.com/oss/python/deepagents/backends)
- [Deep Agents Sandboxes](https://docs.langchain.com/oss/python/deepagents/sandboxes)
- [Deep Agents Permissions](https://docs.langchain.com/oss/python/deepagents/permissions)
- [Deep Agents Comparison](https://docs.langchain.com/oss/python/deepagents/comparison)
- [Deep Agents Human-in-the-loop](https://docs.langchain.com/oss/python/deepagents/human-in-the-loop)
- [LangChain MCP Adapter](https://docs.langchain.com/oss/python/langchain/mcp)
- [OpenSandbox](https://github.com/opensandbox-group/OpenSandbox)

参考资料描述 SDK 能力，不构成本项目的安全保证。安全结论必须来自固定版本、实际部署配置、威胁分析和测试证据。
