# Research Agent 固定评测与升级门禁

## 模块解决的问题

Phase 6 需要一个可重复、零费用、不会访问真实模型或公网的出口门槛，用来回答两类问题：平台的多轮、
授权、取消、幂等与成果契约是否仍然成立；升级 `deepagents` 后，项目实际依赖的公开装配面、Checkpoint
和 Adapter 行为是否发生破坏。该模块不尝试用 Fake Runtime 代替真实模型质量评测。

## 固定评测集

`backend/tests/evaluation/agent_manifest.json` 固定为 `phase6-agent-eval.v1`，包含 7 个场景：

- 同一 Session 的两轮执行复用 Thread 绑定，但使用不同 execution；
- Project Index 与指定 Review Evidence Matrix 被冻结到同一授权快照；
- 没有授权 Evidence 时明确回答证据不足且不伪造引用；
- 候选 Artifact 与正式公网来源规范化保持小事实、hash 和无 query URL；
- Prompt Injection canary 不进入返回值或筛选后的 Runtime Event，未授权 Tool 保持关闭；
- 重复同一 Turn 只重放结果，Tool 与重复调用预算保持冻结；
- interrupt/resume 复用 execution，取消后停止继续流式执行。

每个 evaluator 都调用 `FakeResearchAgentRuntime`、`create_context_snapshot`、
`create_policy_snapshot` 或正式来源规范化函数。汇总器拒绝空清单、重复 scenario ID、缺失类别、空
`production_path` 和任一失败布尔检查；出口阈值固定为 100%。Runner 可选择写 JSON 报告，但默认只输出
本次实际执行结果，不提交带时间戳的生成文件。

## Deep Agents 升级契约

锁文件当前固定 `deepagents==0.7.8`。`verify_deep_agents_upgrade_contract` 检查项目依赖的公开
`create_deep_agent` 参数：`model`、`tools`、`system_prompt`、`middleware`、`subagents`、`skills`、
`backend` 和 `checkpointer`。同时验证 `ResearchAgentRuntime` 仍只有 5 个 SDK-neutral 方法，不把
Deep Agents 或 LangGraph 类型暴露给 Domain/API。

`scripts/test-deep-agents-upgrade.sh` 不只检查函数签名，还运行真实项目 Adapter、MCP、Native Skills、
Tool/模型预算、PostgreSQL Checkpoint 和第二进程恢复测试。升级依赖前必须先更新 pin 和锁文件，再运行
该脚本；任何失败都阻止升级，不能通过删除断言或跳过恢复测试放行。

## 固定安全回归

`scripts/test-phase6-regression.sh` 汇总 Phase 6 的关键 Domain/Application/Adapter/API 回归：附件、来源
网络分类、Budget、Browser 控制、Skill、Workspace、Artifact、取消/重复、MCP、Sandbox 清理、Manifest
以及上述 7 场景评测。普通执行完全离线；需要 PostgreSQL 的测试通过 Testcontainers 运行，不启用真实
模型、实时网站或 OpenSandbox Smoke。

真实能力另行显式验证：noVNC Smoke 创建临时固定镜像 Sandbox，让生产 `AgentBrowserPanelView`/
noVNC 通过生产 ticket 解析和有界 bridge 输入 marker，再由保持打开的同 generation Playwright MCP
session 回读。它证明本地功能链路，不代表通用认证、secure runtime 或公网多租户安全。

## 实际结果

2026-08-28 主智能体实际运行：

- `python -m tests.evaluation.run_phase6_agent_eval`：7/7 场景通过，pass rate 1.000；
- `./scripts/test-deep-agents-upgrade.sh`：130 passed in 71.22s；
- `./scripts/test-phase6-regression.sh`：283 passed、1 skipped in 94.78s；skip 是需要显式 OpenSandbox
  开关的真实 noVNC 用例，文件内离线装配与 recipe 契约均已执行；
- 完整后端默认套件：1291 passed、10 skipped in 661.86s；其中一次全量审计发现旧 MCP 测试硬编码
  `-mcp.v2`，改为引用当前生产策略版本常量后定向 1 passed，并由上述完整重跑确认无失败；
- `pyright`：0 errors、0 warnings；新增 Python 范围 `ruff check` 通过；
- `AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1 ...test_real_opensandbox_vnc_and_playwright_share_one_sandbox`：
  1 passed in 15.96s；
- Web `npm test`：30 files / 170 passed；`npm run build`：171 modules transformed；
- `npm run test:e2e -- phase-05.spec.ts`：1 passed in 36.6s。

## 代码入口

- 清单与 evaluator：`backend/tests/evaluation/agent_manifest.json`、`agent_scenarios.py`；
- 汇总与 runner：`agent_metrics.py`、`run_phase6_agent_eval.py`；
- SDK 装配契约：`infrastructure/agent/deep_agents_upgrade_contract.py`；
- 固定脚本：`scripts/test-deep-agents-upgrade.sh`、`scripts/test-phase6-regression.sh`；
- 真实 Browser 验收：`test_opensandbox_browser_control_smoke.py`、`web/e2e/browser-control-*`。

## 已知限制

- 7 个 Fake Runtime 场景验证工程契约，不评判真实模型回答的 Groundedness、研究深度或语言质量；
- canary 场景证明当前筛选路径不回显该字符串，不等于 Prompt Injection 已被分类器彻底解决；真正后果由
  owner/Project scope、无平台 Secret、Sandbox 和网络边界限制；
- 真实 noVNC 验收只使用 Sandbox 内合成页面和本地单用户 ticket gateway，不使用真实账号；
- Deep Agents 的非公开内部实现仍可能变化，签名门槛不能替代完整 Adapter/Checkpoint 回归。

## 60 秒面试说明

我把 Agent 评测拆成两层。第一层是版本化、零费用的 7 场景平台契约集，实际调用 Fake Runtime、授权
Snapshot、来源规范化与取消/恢复路径，100% 才放行；它不冒充真实模型质量。第二层是 Deep Agents 升级
门禁，既检查 `create_deep_agent` 的公开装配面和 SDK-neutral Port，也运行项目的 Tool、Skill、MCP、
PostgreSQL Checkpoint 与跨进程恢复套件。noVNC、Sandbox 和公网能力则用显式真实 Smoke 单独证明，并在
报告中严格区分功能证据与生产安全声明。
