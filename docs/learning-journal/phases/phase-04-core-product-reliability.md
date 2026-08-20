# Phase 4：Core Research Backend 产品闭环、可靠性与评测

## 状态

待开始。本阶段在 Phase 3 固定 Review Workflow 完成后进入；完成即代表 Core Research Backend v1 完成。

## 目标和用户可见结果

把文献导入、RAG 问答和固定 Review Workflow 收束成可演示、可恢复、可观测且有固定评测基线的完整个人产品。用户可以从 Project 创建一路完成文献导入、带引用问答和综述 Artifact 导出，并在失败、取消或重启后得到明确结果。

## 范围

### 包含

- 统一 Project、Library、RAG Chat、Review Run、Evidence、Citation 和 Artifact 用户路径；
- 完整 Docker Compose 或等价、明确的本地部署闭环，确保 API 与 Worker 共享 Storage；
- Run/Step/Event/Attempt、取消、重试、恢复和 SSE 的跨模块故障注入；
- Retrieval、Citation 和 Review 输出的小型固定评测集与回归阈值；
- 结构化日志、基础 Metrics、错误码和运行手册；
- 数据迁移、备份恢复、孤儿 Storage/Artifact 对账和资源上限；
- 在完整引用检查和备份策略下实现 Project/Paper 永久删除与异步 Storage GC；
- Playwright 核心旅程和可重复的演示 Fixture；
- Core v1 安全复核和学习文档收尾。

### 不包含

- Deep Agents、开放式 Agent 规划、MCP、Browser 或 Sandbox；
- 任意代码执行、多 Agent、用户自定义 Tool 或 Workflow Canvas；
- 面向大规模生产部署的 Kubernetes、多区域或高可用架构。

## 关键不变量

- PostgreSQL 仍是 Run、Event、Evidence、Citation 和 Artifact 的事实来源；
- 所有 Retrieval、Evidence、Artifact 和下载路径必须限制在当前 owner/Project；
- API、Worker 重启和重复投递不能产生第二份业务副作用；
- 评测失败不能通过降低断言或隐藏证据不足来绕过；
- Phase 4 不为了未来 Agent Extension 提前污染 Core 领域模型。

## 实现切片方向

1. 统一 Core v1 用户旅程和导航；
2. 完整本地部署与共享 Storage；
3. 跨模块恢复、取消和故障注入；
4. Retrieval/Citation/Review 固定评测；
5. 日志、Metrics、资源对账和运行手册；
6. Playwright E2E、演示 Fixture 和发布复盘。

## 完成条件

- 全新环境可按文档启动并完成导入、RAG、Review 和 Artifact 下载；
- 核心旅程、越权、取消、重试、重启恢复和引用跳转有自动测试；
- 固定评测集达到已记录阈值，失败样例可解释；
- 阶段 Spec、模块笔记、部署说明和已知限制与实现一致；
- 禁用所有 Research Agent 相关能力时，Core v1 仍完整可用。
