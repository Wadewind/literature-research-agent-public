# Phase 4 Demo-ready Core v1 发布复盘

- 日期：2026-08-24
- 里程碑：Demo-ready Core Research Backend v1
- 范围依据：Phase 4 Spec 与 ADR-0004

## 交付结论

Phase 4 的九个切片已经闭环。Project/个人文献库、异步导入、Project-scoped RAG 与固定 Review 可以在
本地 Fake 模式独立运行；Review 从来源检索、依赖等待、Evidence Matrix、两轮 Outline HITL、分节写作、
引用校验到六类 Artifact 的用户旅程已由浏览器、正式 API、PostgreSQL、Valkey/ARQ Worker 和 Storage
共同验证。Research Agent、Browser、MCP、Tool 与 Sandbox 没有提前进入 Core。

该结论只表示“可在可信本地开发环境复现、演示、诊断并具有评测证据”。它不是公网生产发布，也不包含
认证、备份恢复、永久删除/GC、OpenTelemetry、集中日志、告警或 SLA。

## 浏览器发布门

`web/e2e/run.sh` 每次创建临时 Storage 与临时 PostgreSQL/Valkey 卷，执行迁移后启动宿主 API、Worker、
Vite 和 Chromium。它显式设置 Fake Parser、Embedding、Chat、arXiv，清除继承的 Provider Key，不读取
`.env`，浏览器测试还会阻断所有非 localhost 请求。

Phase 1–4 四条核心旅程分别证明：

1. 新 PDF 导入、Run/SSE、刷新、Element/PDF、跨 Project 内容复用、移出与重新收录；
2. Project RAG、Message/Claim/Citation 刷新恢复、Evidence/PDF 页码、单篇范围与 Project 归档只读；
3. 4 Source Review 的 3 ready + 1 stable failed、依赖等待、feedback 后第二次 HITL、approve、终态、
   Matrix/Section/Citation、受限 PDF 路径与六类 Artifact 下载可读；
4. Review 从 UI 请求取消并收敛为刷新后仍可恢复的 `cancelled`。

Review 旅程额外断言错误 Project 的 PDF 和 Artifact content 返回 404、没有 `pageerror`、关键 console
error 或外部请求。Outline/Matrix 在生成前返回 404 是既有“尚未提交”读契约，Chromium 会打印通用
资源错误；测试只在当前 Project/Run 的对应 GET 已实际返回 404、且 `ConsoleMessage.location().url`
精确匹配时消费该日志，并主动制造另一条本机 404 证明它仍会进入错误列表，不排除其他 404、5xx、
脚本异常或 console error。依赖等待不依靠瞬时 UI 命中，而由持久的 `dependency_wait_started` 和
`dependency_wait_completed` Event 证明。

## 实际失败与修正

- E2E harness 静态契约首次失败，因为未显式设置 Fake arXiv。增加 `AGENT_ARXIV_BACKEND=fake`、清除
  Provider Key 和关闭 Worker Metrics 端口后通过。
- Phase 4 首跑 `2 failed`。Artifact endpoint 故意使用安全通用下载名 `content.md/json`，测试错误地
  假设内部导出文件名会暴露到下载响应；修正为验证扩展名、字节、JSON 解析和 Markdown 研究问题。
  另一失败来自 Chromium 对预期 404 的通用 console 日志，随后按上文边界分类。
- Phase 4 重跑 `2 passed`。首次 Phase 1–4 全套为 `3 passed, 1 failed`：Phase 2 用例仍查找 Review UI
  引入统一 Project 工作区前的旧“返回项目文献库”链接。测试更新为当前 `文献库` 导航后，Phase 2
  单跑 `1 passed`，最终全套 `4 passed`。没有为通过测试而改变业务行为。

## 验证与发布限制

最终实际运行包括 `npm test`（118 passed）、`npm run build`、`npm run test:e2e`（4 passed，37.5s）、
E2E harness/开发脚本/Fake arXiv 后端测试（11 passed）、Review Application（3 passed）与 Review API
（6 passed）。`ruff check src tests`、`pyright`、`bash -n web/e2e/run.sh` 和 `git diff --check` 也通过；
具体计数同步记录在 Phase 4 Spec 与 Web UI 模块笔记。

Playwright 是少量高价值旅程，不替代可靠性矩阵：跨 owner、重复 Job、取消竞争、Checkpoint 损坏、
Storage/事务崩溃间隙和 SSE cursor 细节继续由分层自动测试证明。Fake 结果只证明确定性工程闭环，不证明
真实模型语义质量。Real Provider 报告仍是显式 opt-in 且不阻断普通测试。

## 发布后 Real 模式缺陷

Phase 4 关闭后的首次 Real Review 暴露了 `json_object` fallback 丢失完整业务 Schema 的缺陷。修复在
OpenAI-compatible Chat Adapter 中注入确定性序列化的 Schema instruction，保留严格本地校验，没有增加
repair/retry、放宽 Validator 或记录 Schema/messages。自动回归仍只使用 RESPX/Fake，不读取 `.env`、
不访问真实 Provider。后续新 Review 已通过 `formulate_search_strategy`，但它不是隔离的 Provider 回归
测试，不能扩大为完整真实质量结论。

后续 Real 旅程又发现旧失败 ingestion Run 的 ARQ 重投递假成功、章节输出触及 token 上限后
结构校验失败，以及 Search Strategy 输出触顶后 Schema 校验失败。四项缺陷的确认事实、推断边界、
影响 ID、候选修复和所需回归测试统一维护在
[Phase 4 Real 模式体验缺陷台账](phase-04-real-mode-defect-log.md)，后续体验问题继续按编号追加。本次排查引出的平台级
错误可观测性与 Run 诊断思考另行记录在
[错误可观测性与 Run 诊断反思](../reflections/error-observability-and-run-diagnostics.md)，当前为延期方案。

## 60 秒面试说明

“Phase 4 把前三阶段的后端能力收束成 Demo-ready Core。我用完全离线的四篇合成 arXiv Fixture 驱动真实
API、PostgreSQL、ARQ Worker、LangGraph checkpoint、两轮人工确认和 Artifact Storage，再用 Playwright
从浏览器验证刷新、SSE 失联后的 REST 恢复、部分来源失败、引用回跳、下载授权和取消。日志、Metrics、
可靠性矩阵、固定评测和本机性能基线各自提供不同证据。这个里程碑明确只是可信本地开发演示，不宣称
公网认证、备份、GC、OpenTelemetry 或 SLA。”
