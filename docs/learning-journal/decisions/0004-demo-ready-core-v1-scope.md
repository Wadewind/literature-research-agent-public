# ADR-0004：固定 Demo-ready Core v1 的交付边界

- 状态：已接受
- 日期：2026-08-23
- 决策者：项目维护者

## 背景

Phase 3 已完成固定 Review Workflow。原总体指南把 Phase 4 同时定义为 UI 产品闭环、完整单机 Compose、
OpenTelemetry、备份恢复、永久删除与异步 GC、评测和发布收尾。对个人学习与简历展示项目而言，这会把
用户可见演示、可靠性学习和生产运维混成一个过大的里程碑，也容易把未实现的公网安全和数据耐久能力
包装成“Core v1 已生产可用”。

项目维护者确认 Phase 4 的目标是可重复的本地演示开发环境，并同意结构化 Outline 表单、完全离线的
Fake arXiv、标准库 JSON 日志加 `prometheus-client`，不在本阶段引入 OpenTelemetry。

## 决策

Phase 4 完成代表 **Demo-ready Core Research Backend v1** 完成，而不是生产版 Core v1。

Demo-ready Core v1 必须具备：

- Project、Library、带引用 RAG 和固定 Review Workflow 的完整本地 UI 旅程；
- Review Outline 结构化 approve/edit/feedback、Matrix/Section/Citation 查看和 Artifact 下载；
- `./scripts/dev.sh --fake` 完全离线、零费用且可重复，包含 Fake arXiv 和固定论文 Fixture；
- `--real` 只作为显式真实 arXiv、Docling 和 Provider Smoke/评测入口；
- 标准库 JSON 日志、Correlation ID、小型 Prometheus Metrics、可靠性矩阵和真实记录的评测/性能基线；
- Phase 1–4 Playwright、学习笔记、已知限制和发布复盘。

本阶段固定使用 PostgreSQL/Valkey Compose 加宿主 API、Worker、Web 的开发启动方式，不要求把全部服务
制作为可部署容器。开发者需预装 Python、uv、Node.js/npm 和 Docker Compose。

## 明确推迟

以下能力不属于 Demo-ready Core v1：

- 公网部署、TLS、反向代理、高可用和生产 SLA；
- OAuth、密码、Session/JWT 和面向不可信网络的身份系统；
- PostgreSQL/Storage 自动备份、恢复演练和数据耐久承诺；
- Project/Paper 永久删除、异步 Storage GC、Checkpoint/缓存保留清理；
- OpenTelemetry 及 Grafana/Loki/Tempo/Jaeger 等可观测性平台。

归档继续是唯一用户可见删除语义。物理删除与 GC 在引用闭包、备份和恢复策略明确前不得开放；它们不再
承诺由 Phase 4 实现，也不自动转移到 Phase 5/6。若未来要把系统部署给真实用户，必须重新立项并形成
生产化 ADR。

## 对 Phase 5/6 的影响

Phase 5 的进入条件改为 Demo-ready Core v1 已完成，且 Core 的导入、RAG、固定 Review、Run/Event、
Evidence、Artifact、最低可观测性和评测基线均能独立运行。Phase 5/6 不能假设 Core 已具备公网认证、
备份恢复、GC、SLA 或完整 Trace，也不能借 Agent Extension 静默补建或依赖这些能力。

Phase 6 若为了受控 Agent Runtime 的调试需要 OpenTelemetry，可以在自己的阶段 Spec 中独立决策；这不
改变 Demo-ready Core v1 的完成定义。

## 后果

正面影响：Phase 4 聚焦于可展示的用户旅程、可靠性、诊断和评测，范围适合个人项目；Fake/Real 职责
清晰；里程碑名称不会误导为生产就绪。

代价与风险：本地数据会随归档、缓存和 Checkpoint 持续增长；数据卷误删后没有恢复承诺；可信开发身份
不能抵御公网攻击；Metrics 和业务审计能够诊断问题，但没有跨进程分布式 Trace。README、演示说明和
简历叙事必须持续披露这些限制。

## 被否决的方案

- 保持原 Core v1 生产化范围：工程覆盖全面，但会让 Phase 4 远大于学习项目的核心目标；
- 只做前端、不做可靠性和评测：演示更快，但无法证明后台系统的主要工程价值；
- 在 Phase 4 引入完整 OpenTelemetry 平台：学习价值存在，但依赖与部署成本高于当前最低诊断需求；
- 把备份、GC、认证自动挪到 Phase 6：会污染 Research Agent Extension 的边界，并隐藏未完成的 Core
  生产化工作。
