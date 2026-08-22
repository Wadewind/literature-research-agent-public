# 学习日志

本目录记录文献综述 Agent 系统各阶段的设计、实验、实现证据和复盘。学习完成的标准不是“看过资料”，而是能够解释状态归属、正常流程、失败行为、测试方法和方案限制。

## 目录

```text
learning-journal/
├─ phases/      # 阶段目标、范围、切片、验收和复盘
├─ modules/     # 核心模块完成后形成的学习笔记
└─ decisions/   # 需要保留背景、选项和后果的架构决策
```

## 记录规则

- 进入阶段前先创建或更新对应的 Phase Spec。
- 每次只推进一个可验证的小型垂直切片。
- 模块真正完成后再写模块笔记，不预先生成空模板。
- 测试记录只写实际执行过的命令和结果。
- 设计与实现不一致时，先在 Phase Spec 或 ADR 中明确决定。
- 每份完成的模块笔记应包含代码入口、已知限制和 60 秒面试说明。

## 当前阶段

[Phase 0：项目基线与技术验证](phases/phase-00-project-baseline.md)、[Phase 1：Project、个人文献库与可靠异步导入](phases/phase-01-project-library-ingestion.md)和 [Phase 2：有引用的 RAG 问答](phases/phase-02-cited-rag-qa.md)已经完成。Phase 2 已通过固定 14 题管线评测、可靠性证据审计、Phase 1–2 Playwright E2E、真实 Docling 以及真实 Embedding/结构化 Chat 显式 Smoke；Fake 评测只证明工程闭环，真实 Provider 只证明最小调用契约，均不宣称生产模型质量。[Phase 3：可暂停恢复的固定文献综述 Workflow](phases/phase-03-review-workflow.md)正在按切片开发，目前已完成等待/恢复基础、Review Workflow 数据契约、受限 arXiv 检索与幂等项目导入、论文依赖对账恢复闭环、持久 LangGraph checkpoint/crash recovery 骨架，以及固定 Evidence Matrix 提取、范围 Validator 和幂等 Output/Event 闭环。生产 Review Executor 要等切片 7 的 Outline interrupt 形成完整图后再注册，避免半成品图虚假成功。
