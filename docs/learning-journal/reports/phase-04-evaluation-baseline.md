# Phase 4 固定评测基线（2026-08-24）

## 实际结果

Phase 2 正式管线再次运行 4 篇合成 PDF、14 题：answered Retrieval 8/8、目标条目与 Citation 11/11、
Validator 14/14、selected scope 3/3；Fake insufficient 仍为 0/6，总状态 8/14。后者是公开失败样例，
说明 Fake Chat 不能判断语义证据充分性。

Phase 4 的 3 个研究问题和 3/4/3 篇语料 ID 实际进入生产 Matrix/Citation/Section Validator 和 Review
导出器。正常场景为 Source 3 ready、Matrix 9/9、mapping 3/3；部分失败场景为 3 ready + 1 failed、
Matrix 9/9、mapping 3/3；证据不足场景为 3 ready、Matrix 9/9、mapping 0/0（不计 mapping 分母）。
合计 Matrix 27/27；场景 3/3、Citation 接受/跨 Run 拒绝 6/6、导出 mapping 6/6、Evidence 跨
Project/Run 拒绝 18/18、伪造 Evidence 拒绝 3/3，五项实测均为 1.0。Owner 隔离依赖
Project-scoped Application/PG 回归，是组合证据而不是领域指标。六类 Artifact 的
持久化、稳定生成和重放由固定 Application/PostgreSQL 回归证明，不把它们另算成领域质量百分比。

另有 12 个固定且唯一的回归节点实际 12/12 通过，覆盖 Application、feedback interrupt/resume、
checkpoint、Artifact 持久化、终态和重放。它们是组合回归证据，不转换为上述质量比例；节点删除后
实际通过数不等于 manifest 的固定期望数，整个 runner 失败。

100% 只表示实际领域场景的结构闭包，不表示 Citation precision、Groundedness、Coverage 或 Redundancy。
本切片没有人工语义评分，不报告这些指标。

## 复现

```bash
cd backend
.venv/bin/python tests/evaluation/run_phase2_eval.py \
  --json-output /tmp/phase-02-evaluation-phase4.json
.venv/bin/python tests/evaluation/run_phase4_review_eval.py \
  --json-output /tmp/phase-04-review-evaluation.json
```

普通运行使用 Fake Chat/Embedding 与合成语料，不读取 `.env`、不访问 arXiv 或付费 Provider。Review
runner 的 checkpoint 场景需要本机 Docker socket。

## 实际失败样例

1. v1 runner 曾把静态 `covers` 与 pytest 退出码错误投影为质量 1.0；主审拒绝后改为实际消费问题/
   语料的领域事实计数，并把 pytest 结果降级为固定组合回归。
2. 性能字段首次查询不存在的 `elements` 表，使完整 Phase 2 runner 在所有 14 题执行后仍退出 1；修正
   为 `document_elements` 并全量重跑成功后才记录结果。
3. Fake insufficient 0/6 是模型能力限制，不通过修改 Fixture 或降低断言掩盖。
