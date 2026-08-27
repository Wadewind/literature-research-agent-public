# 学习日志

本目录记录文献综述 Agent 系统各阶段的设计、实验、实现证据和复盘。学习完成的标准不是“看过资料”，而是能够解释状态归属、正常流程、失败行为、测试方法和方案限制。

跨阶段的当前运行配置入口、版本化 Profile/Catalog 与用户配置边界统一记录在
[项目配置参考](../configuration-reference.md)；阶段文档继续保留各切片完成当时的历史决定和验证证据。

## 目录

```text
learning-journal/
├─ phases/      # 阶段目标、范围、切片、验收和复盘
├─ modules/     # 核心模块完成后形成的学习笔记
├─ decisions/   # 需要保留背景、选项和后果的架构决策
└─ reports/     # 实际评测、性能与显式真实 Provider 运行记录
```

## 记录规则

- 进入阶段前先创建或更新对应的 Phase Spec。
- 每次只推进一个可验证的小型垂直切片。
- 模块真正完成后再写模块笔记，不预先生成空模板。
- 测试记录只写实际执行过的命令和结果。
- 设计与实现不一致时，先在 Phase Spec 或 ADR 中明确决定。
- 每份完成的模块笔记应包含代码入口、已知限制和 60 秒面试说明。

## 当前阶段

[Phase 0：项目基线与技术验证](phases/phase-00-project-baseline.md)、[Phase 1：Project、个人文献库与可靠异步导入](phases/phase-01-project-library-ingestion.md)和 [Phase 2：有引用的 RAG 问答](phases/phase-02-cited-rag-qa.md)已经完成。Phase 2 已通过固定 14 题管线评测、可靠性证据审计、Phase 1–2 Playwright E2E、真实 Docling 以及真实 Embedding/结构化 Chat 显式 Smoke；Fake 评测只证明工程闭环，真实 Provider 只证明最小调用契约，均不宣称生产模型质量。

[Phase 3：可暂停恢复的固定文献综述 Workflow](phases/phase-03-review-workflow.md)已于 2026-08-23
完成切片 1–10：等待/恢复基础、Review Workflow 数据契约、受限 arXiv 检索与幂等项目导入、论文依赖
对账恢复闭环、持久 LangGraph checkpoint/crash recovery、固定 Evidence Matrix、Outline HITL、
[综述章节写作、引用校验与一致性报告](modules/review-section-citation-consistency.md)，以及
[综述 Artifact 生成与生产执行闭环](modules/review-artifact-generation.md)。生产 Review Executor、固定图
终态、Project-scoped API、Artifact 下载、通用 SSE 重放和图外阶段可观察性均已通过最终审计；真实参数
校准和 Review 专用前端归入 Phase 4 产品闭环。

[Phase 4：Demo-ready Core 产品闭环、可靠性与评测](phases/phase-04-core-product-reliability.md)已于
2026-08-24 完成切片 1–9，包括离线 Fake arXiv、Review UI、JSON Logs/Correlation、
[低基数 Prometheus Metrics](modules/prometheus-metrics.md)和
[可靠性测试矩阵](modules/reliability-test-matrix.md)，以及
[固定评测与本机性能基线](modules/review-evaluation-and-performance.md)。固定基线已实际走通正式
API+PostgreSQL+Valkey/ARQ Worker 的 4 Source Fake Review、两轮 HITL 与六类 Artifact；Phase 1–4
Playwright 又从浏览器验证同一核心旅程、取消、归档和刷新恢复。完成证据与边界见
[Phase 4 发布复盘](reports/phase-04-release-retrospective.md)。发布后通过真实 Provider 体验发现的问题、
证据边界和候选修复持续记录在 [Phase 4 Real 模式体验缺陷台账](reports/phase-04-real-mode-defect-log.md)。
该阶段明确面向本地演示开发环境，
不包含公网认证、备份恢复、永久删除/GC、OpenTelemetry 或 SLA。

[Phase 5：Deep Agents 集成验证](phases/phase-05-deep-agents-integration.md)正在进行。契约与 Fake
Runtime、两轮离线业务闭环、取消恢复对账、受限 Deep Agents Adapter 及
[Project Research Context](modules/project-research-context.md) 已完成开发验证；Runtime 部署与崩溃恢复门槛
也已闭合，详见 [Agent Runtime Execution 恢复控制](modules/agent-runtime-execution-recovery.md)和
[恢复缺口台账](reports/phase-05-runtime-recovery-gap-log.md)。切片 7.0 的
[真实 Deep Agent Runtime Enablement](modules/real-deep-agent-runtime-enablement.md)已完成实现：
生产 Worker 默认仍为 Fake，显式真实模式才装配固定 Provider、持久 Checkpointer、Project Context 和
RuntimeExecution control。ADR-0007 已选择
[OpenSandbox 可执行研究 Workspace](decisions/0007-use-opensandbox-executable-workspace.md)，后续按
OpenSandbox/Lease/WorkspaceSnapshot 推进；ADR-0008 又将剩余顺序固定为
[MCP Configuration Foundation → 同 Sandbox Playwright/现有 Search MCP → Deep Agents Native Skills](decisions/0008-use-native-mcp-and-skills-capabilities.md)
→ Agent Chat UI。其中 7.1 已完成实现与离线/临时 PostgreSQL 验证，详见
[Agent Sandbox Workspace](modules/agent-sandbox-workspace.md)；真实 OpenSandbox 隔离/资源 Smoke 与
7.2 已完成 MCP Configuration Foundation；7.3 已完成固定 Playwright/arXiv MCP 的实现及无网络镜像内
回路验证，详见 [Agent Playwright/Search MCP](modules/agent-mcp-browser-search.md)。7.4 已完成
[Agent Native Skills](modules/agent-native-skills.md)：平台/owner 声明式 Skill 采用不可变版本、首 Turn 后
Session manifest 锁定和 `/skills/` 只读虚拟 Backend，并直接复用 Deep Agents SkillsMiddleware。真实
OpenSandbox proxy 的本地功能 Smoke 已于 2026-08-28 通过；真实 Provider、公共网络与生产安全验证仍待
完成。Phase 6 的首批产品化方向已由
[ADR-0009：跨 Turn 人工浏览器控制](decisions/0009-use-turn-boundary-browser-control.md)和
[ADR-0010：显式 Agent 文件交换](decisions/0010-use-explicit-agent-file-exchange.md)固定：先完成可下载的
Agent 绘图 Artifact 与同 generation 合成页面人工操作，再处理输入附件和公共网络安全；WorkspaceSnapshot
不直接成为用户文件，平台也不托管登录凭据。后续范围由
[ADR-0011：Phase 6 精简交付](decisions/0011-adopt-phase-06-lean-delivery.md)收敛：必须完成固定 arXiv 的统一
egress、URL/SSRF 与 PDF 下载安全，以及最小硬预算、Sandbox 清理和资源限制；通用 Approval Center、
开放互联网、OAuth/Credential、完整 Registry 和生产级 Sandbox 平台明确延期。

[Phase 6：Research Agent 与安全强化](phases/phase-06-research-agent-security.md)已进入实施。Slice 1 已完成
契约缺口审计并形成 [`Research Agent 精简安全契约`](../spec/research-agent-security-contract.md)：明确区分
Phase 5 已验证事实与后续目标，冻结 owner/Project/Session/Turn/Sandbox generation/Workspace/Artifact
所有权、信任与 Secret 边界、自动执行/直接拒绝矩阵、历史 Approval 字段兼容、事务外 I/O、取消/fence/
Effectively Once、API/Event 增量和 Slice 2–8 安全门槛。Slice 2 已完成
[Agent 输出 Artifact 交付](modules/agent-artifact-delivery.md)：真实 Sandbox Tool、Candidate 生命周期、
事务外 Storage/文件校验、Turn 成功原子发布、owner-scoped 下载与壳层无关成果组件均已落地；Fake
descriptor 仍不可下载，真实 OpenSandbox Artifact Smoke 与生产级扫描没有被伪称通过。下一切片为
Browser 画面与跨 Turn 人工控制。最终 UI 同时受
[`Web UI 应用壳与视觉重设计`](../spec/web-ui-app-shell-redesign.md)约束，前置功能组件必须保持壳层无关。
