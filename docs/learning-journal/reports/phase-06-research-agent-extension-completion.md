# Phase 6 Research Agent Extension 完成报告

日期：2026-08-28

## 结论

Phase 6 的本地个人项目精简交付已完成。系统在 Phase 5 的 `ResearchAgentRuntime` 适配边界上，补齐了
正式 Artifact、跨 Turn Browser 人工控制、输入附件、固定能力与硬预算、Sandbox 生命周期与清理、正常
公网 egress/private-network 拒绝，以及遵循 `web-ui-app-shell-redesign.md` 的统一产品界面。

这里的“完成”表示本地单人、简历展示范围达到阶段 Spec 的用户故事和验证门槛，不表示公网多租户生产、
通用认证、secure runtime、精确计费、备份恢复或任意 Coding Agent 已完成。

## 用户可见闭环

1. 用户在 Project 内完成文献库、RAG、Review Workflow 和 Research Agent 四模式切换；
2. AgentSession 持续保存产品消息，同一 SDK Thread 上每条用户消息创建独立、可取消的 AgentTurnRun；
3. 每轮可冻结 Project Chunk Index、指定 Review Evidence Matrix、附件、MCP/Skill Profile 和 Policy；
4. Agent 可在 Session 专属 OpenSandbox 中使用固定 `execute`、Playwright/Search MCP、Workspace 与 Skill；
5. 用户可在两个 Turn 之间通过右侧 noVNC 操作同一 Chromium，再让下一轮 Agent 继续读取页面状态；
6. Sandbox 中的 PNG/JPEG/SVG/PDF/CSV/Markdown/text/JSON 只有显式提交、重新校验并随 Turn 成功发布后，
   才成为可预览或下载的 AgentArtifact；
7. Inspector 展示筛选后的 Event、ToolExecution/Usage、Evidence、公开来源 Manifest、Browser 和成果，不
   暴露 Prompt、论文全文、Secret、raw endpoint 或大型 Tool 输出。

## 架构边界

- PostgreSQL 的 Session、Turn Run、Message、Snapshot、Evidence、Usage、Event 与 Artifact 是业务事实；
- Deep Agents Thread、Checkpoint、Store 与 Workspace 是 Runtime 内部状态，SDK 类型不进入 Domain/API；
- 外部模型、MCP、Browser、Sandbox、Storage 文件读取均在数据库事务外执行；
- Runtime 成功不等于业务提交成功，候选成果通过稳定 ID、hash、唯一约束和 reconcile 实现 effectively once；
- owner/Project/Session/generation/fence、取消、硬预算和能力 hash 在平台边界检查；
- Sandbox 可以访问正常公网 HTTP(S)，但非-loopback private、metadata、宿主和 LAN 目标由统一 egress
  拒绝；正式 URL/source 还会拒绝 localhost/loopback 及其 DNS 结果；
- TigerVNC 只监听 Sandbox namespace loopback，内部 RFB 使用 `SecurityTypes=None`；浏览器身份仍由
  平台短时 ticket gateway 校验，VNC 密码和 raw 5901 不进入前端。

## 完成证据

主智能体在最终工作树上实际运行：

| 门槛 | 结果 |
|---|---|
| 完整后端默认套件 | 1291 passed、10 skipped in 661.86s |
| Phase 6 固定安全回归 | 283 passed、1 skipped in 94.78s |
| Deep Agents 升级门禁 | 130 passed in 71.22s |
| 固定 Agent 评测 | 7/7，pass rate 1.000 |
| Python 静态检查 | Ruff 通过；Pyright 0 errors/0 warnings |
| Web 单元测试 | 30 files / 170 passed |
| Web production build | 171 modules transformed，构建通过 |
| Agent UI 用户旅程 | Phase 5 E2E 1 passed in 36.6s |
| noVNC 同 Sandbox 真实链路 | 1 passed in 15.96s |
| Slice 7 公网/private 真实链路 | 1 passed in 39.67s |

普通回归没有访问付费模型或实时网站。noVNC 与 public-egress 测试均需显式环境开关，创建临时 Sandbox
并在 `finally` 销毁。Agent 评测的 Fake Runtime 结果只证明平台契约，不代表真实模型质量。

## 运维与复现入口

- 默认离线演示：`./scripts/dev.sh --fake`；
- 真实 Provider：按 `.env.example` 配置后运行 `./scripts/dev.sh --real`；
- OpenSandbox：见 `docs/runbooks/local-opensandbox-server.md`；
- 配置边界：见 `docs/configuration-reference.md`；
- Phase 6 回归：`./scripts/test-phase6-regression.sh`；
- Deep Agents 升级前：`./scripts/test-deep-agents-upgrade.sh`；
- 固定评测：在 `backend/` 运行 `.venv/bin/python -m tests.evaluation.run_phase6_agent_eval`。

## 已知限制与非声明

- 当前身份仍是本地 `dev_actor_id`，没有公网认证、CSRF/Origin 完整策略或多实例 ticket key 管理；
- OpenSandbox 未配置 secure runtime，Docker overlay 物理磁盘硬配额、Storage staging orphan GC 未完成；
- public-egress 只约束目标网络边界，不解析 HTTP method；raw Browser/Shell/MCP 可能发出外部写请求；
- Agent 不获得平台外部写 Tool 或凭据，但公网 Shell 可下载用户态内容，属于 trusted-local 风险；
- noVNC 只支持同 generation、两个 Turn 之间单人控制，不保存 Cookie/Profile，不支持同 Turn interrupt；
- 固定 arXiv Smoke 验证了有界 PDF 前缀、类型、magic 和 hash，没有把慢速完整 PDF 下载宣称为通过；
- Fake Agent 评测不衡量真实模型 Groundedness；真实 Provider 质量和费用需显式、小预算评测；
- 不支持多 Agent、长期 Memory、用户自定义 MCP URL/命令/网络 Profile、动态安装或宿主执行。
- Run/Attempt/Event 已保存可追溯的业务事实，但平台尚无统一、版本化的 Failure 契约和
  Run Diagnostic 聚合视图；当前诊断仍可能需要手工联系日志、多张业务表和代码路径。问题证据、
  方法论和延期渐进方案见
  [错误可观测性与 Run 诊断反思](../reflections/error-observability-and-run-diagnostics.md)。

## 阶段提交索引

- `c9e2051`：Slice 1 精简安全契约；
- `0469fe9`：Slice 2 Agent 正式成果；
- `f3abdaf`：Slice 3 Browser 人工控制；
- `e181b76`：Slice 4 Agent 输入附件；
- `80c2b3c`：Slice 5 硬预算治理；
- `38230ed`：Slice 6 Sandbox 强化；
- `5150229`：Slice 7 Sandbox 公网能力；
- `d95905b`、`5140aac`、`dcd6df6`、`8418b13`：Slice 8.1–8.4 应用壳、轻页头、工作区整合与视觉刷新；
- Slice 8.5：本报告、真实 noVNC、固定评测、升级门禁和最终复盘所在提交。

阶段完成后的显式 Real 模式集成缺陷与修复证据另见
[Phase 6 Real 模式体验缺陷台账](phase-06-real-mode-defect-log.md)。
