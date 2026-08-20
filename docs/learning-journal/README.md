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

[Phase 0：项目基线与技术验证](phases/phase-00-project-baseline.md)已经完成。[Phase 1：Project、个人文献库与可靠异步导入](phases/phase-01-project-library-ingestion.md)的切片 1–10 及用户可见闭环已经完成，当前保留切片 11 的 Compose Smoke、故障注入、Playwright E2E 和验收复盘；Phase 2 可进入方案讨论，但开始实现前仍需确认其 Paper 详情/版本查询前置契约。
