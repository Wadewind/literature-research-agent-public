# 综述章节写作、引用校验与一致性报告

## 模块解决的问题

Evidence Matrix 已把多篇论文压缩为可追溯中间事实，但仍不能把完整 Matrix 一次交给模型生成全文。
本模块按批准大纲顺序生成结构化 Section，将每个重要 Claim 绑定到当前章节可见 Evidence，最后形成
统一 ClaimSet 并做确定性闭包校验；全文一致性模型只报告问题，不自动改写内容。

## 边界与执行流程

```text
批准 outline.v1 + 验证过的 evidence-matrix.v1
  → 按 Outline 顺序逐节筛选 dimension rows
  → 只加载 rows 实际引用的 Evidence 定位/摘录
  → section_draft.v1 → section.v1 Validator
  → Section Output + section_draft_completed Event 同事务
  → 汇总 Claim 草稿
  → Citation Validator + Matrix Paper/Version/Revision 闭包
  → ClaimSet/Claim/Citation + Step/Event 同事务
  → consistency_check.v1 → consistency-report.v1
  → issues 非阻断 → EXPORT_REVIEW 安全边界
```

模块不渲染数字引用、不写 Markdown Artifact、不提供 Review API，也不注册生产 Review Executor。这些
属于切片 9；当前图在一致性检查后停在安全边界，不能把尚无 Artifact 的 Run 标记成功。

## `section.v1` 与模型上下文

每节 Output 保存 `section_key/title/status/summary/claims/terminology`。`answered` 最多 50 个 Claim，
每个 Claim 最多 4,000 字符并绑定 1–10 个不重复 Evidence；`insufficient_evidence` 必须
`claims=[]`。摘要最多 1,000 字符；术语最多 50 条且名称、定义有界。

一次模型调用只包含研究问题、当前 Section 的 title/purpose/dimension keys、命中的 Matrix rows、这些
rows 引用的 Evidence ID/PaperVersion/ParseRevision/章节/页码/摘录、前文短摘要、统一术语字典、固定
Schema、引用规则和 Profile 快照中的 token 输出预算。它不包含完整 Matrix、`paper_failures`、无关 Evidence、
论文全文或前文章节全文。术语字典以最早持久定义作为后续 Prompt 的统一输入；不同 Section 输出中的
定义冲突仍完整保留，交给一致性报告披露。

新 Review Run 快照固定保存 `section_output_token_limit=4000` 与
`consistency_output_token_limit=2000`，并进入创建请求指纹。切片 8 之前缺少字段的 `review.v1` 开发
Run 明确回退到相同默认值；显式字段必须是整数，范围分别为 256–16,000 和 256–8,000。模型原始 JSON
在 Pydantic 解析前先按 UTF-8 字节限制：Section 192 KiB、Consistency 64 KiB，给 ReviewOutput 的
256 KiB 总上限保留空间。

证据不足是合法章节：必须保留摘要以说明边界，但不得生成 Claim。模型输出非法不触发 repair 调用，
避免引入未讨论的额外成本和重写语义。

## ClaimSet 复用与幂等事务

Phase 2 的 ClaimSet 表没有限制 RunType，因此 Review 可安全复用“一 Run 一个 ClaimSet”约束。领域说明
由 RAG-only 泛化为生成结果，但 AnswerStatus 仍适用：至少一个 Claim 为 `answered`，全部章节证据不足
为 `insufficient_evidence`。

为解决至少一次执行和 PostgreSQL 并发赢家 ID，Repository 增加：

- ClaimSet：`INSERT ... ON CONFLICT(run_id) DO NOTHING` 后回读；
- Claim：`ON CONFLICT(claim_set_id, sequence)` 后按序回读数据库赢家的 `claim_id`；
- Citation：按 `(claim_id, evidence_id)` 复合主键收敛。

应用层随后比较 AnswerStatus、完整 `(sequence,text)` 列表和每个 Claim 的完整 Citation 集合。相同身份
不同语义不能被唯一约束静默吞掉；并发输家的随机 Claim ID 也不会用于 Citation FK。原有 Phase 2
`add_*` 方法保留，既有显式唯一约束和事务测试不改变。

章节 Output 与单节 Event 同事务；引用成功时 ClaimSet/Claim/Citation、Validate Step、Review Stage 和
Event 同事务。Output 已提交而 checkpoint 尚未写入时，重放先校验并复用 Output；ClaimSet 提交后重放
回读完整业务语义，不重复 Event。

三个新 Step 的 input refs 固化 Outline/Matrix、相关 Section IDs、ClaimSet 及 Prompt/Schema/Validator
版本；get-or-add 后比较完整身份与 input refs。入口同时反查成功的 Matrix/Outline 业务 Step，Validate
反查 Draft Step，Consistency 反查 Validate Step/ClaimSet，因此旧 Output 或伪造 checkpoint ID 不能
越过业务闭包。

## Citation Validator 和失败语义

校验分两层：

1. 复用 Phase 2 纯函数，校验 answered/insufficient 状态、空 Claim、零引用、重复引用、伪造 ID 和
   跨 Run Evidence；
2. 对 Matrix 每一行逐条校验，而不只比较 Evidence ID 集合：row.paper 必须对应唯一 READY Source；
   Evidence 必须属于同一 Run/Project/Paper/PaperVersion；PaperVersion 属于 owner；ParseRevision
   必须成功、属于该 Version，并且仍是 Version 当前 Revision。

失败不调用模型修复。Validate Step 以 `citation_validation_failed` 结束，并提交
`citation_validation_completed` Event；Event 只含 `passed=false` 与稳定 reason 计数，不记录 Claim、
Evidence 摘录或 Prompt。失败阻止一致性检查和导出。

## 一致性检查

`consistency_check.v1` 只读取各 Section 的 key、summary、Claim 文本和术语，输出
`consistent/issues_found` 以及最多 50 条 `terminology/contradiction/redundancy` issue。引用定位不再
重复发送给一致性模型。

合法 issue 是报告，不是失败，也不会触发自动重写；否则模型会成为未定义的通用 Judge，并引入额外
成本、引用漂移和新幂等副作用。Provider 调用失败、范围错误或 `consistency-report.v1` Schema 非法
都会阻断当前执行；Schema 非法稳定结束 Step，调用和 Scope 失败交给既有 Worker 错误分类/重试，
不会进入导出。

## 安全、可观察性和测试

- owner/Project/Run、批准 Outline、Matrix、READY Source、Version/Revision/Evidence 全链复核；
- Graph State 只保存 Section Output IDs、ClaimSet ID 和 Consistency Output ID；
- Event 不保存正文、完整 Prompt 或 Evidence 摘录；
- 每个副作用事务先锁定 Run 并复核仍为当前 owner/Project 的 RUNNING Review；模型返回后发生取消时
  不保存新 Output/Event、不完成 Step 或推进 Stage，取消终态留给切片 9 Executor；
- 新增 Citation Event 只在 commit 后通知 SSE，重放不重复事件或通知；
- Domain/Application 定向测试覆盖维度过滤、顺序摘要/术语、证据不足、伪造引用失败、逐行 Paper
  映射、重放零模型调用和非阻断 issues；
- PostgreSQL 测试覆盖并发 ClaimSet/Claim 收敛、异义回读，并回归 Phase 2 唯一约束与 Citation FK。

切片收尾时，完整非集成测试为 `575 passed, 4 skipped`，完整
PostgreSQL/Testcontainers 集成测试为 `111 passed`；`ruff check src tests` 与 `pyright`
均通过。

## 代码入口

- 领域 Schema/Validator：`backend/src/literature_agent/domain/review_section.py`
- 应用编排：`backend/src/literature_agent/application/review_section_service.py`
- ClaimSet get-or-add：
  `backend/src/literature_agent/infrastructure/persistence/claim_set_repository.py`
- Graph 与薄 Node：`backend/src/literature_agent/workflows/review_graph.py`、
  `backend/src/literature_agent/workflows/review_section_nodes.py`
- 测试：`backend/tests/domain/test_review_section.py`、
  `backend/tests/application/test_review_section_service.py`、
  `backend/tests/integration/test_claim_set_idempotency.py`

## 已知限制

- Section Claim 是结构化段落素材；切片 9 才按首次引用顺序渲染 Markdown `[1]` 和 References；
- Citation Validator 证明结构和范围闭包，不自动证明 Evidence 在语义上充分支持 Claim；
- 一致性 issue 不自动修复，用户需在最终 Artifact/评测中审阅；
- 默认章节 4,000、一致性 2,000 token 输出预算及 50 Claim/术语上限仍需真实小样本校准；
- Provider 已返回但 Section Output 尚未提交时若 Worker 崩溃，重放可能再次调用模型；数据库 Output、
  Event、Claim 和 Citation 仍通过稳定键收敛，不宣称外部模型调用 Exactly Once；
- LangGraph 的 Interrupt 节点和条件路由保持 async callable；若改回同步 callable，当前异步 Runtime 会把
  它们交给线程执行器，可能在 `Command(resume)` 后无法继续调度。approve 与 feedback 二次 interrupt
  均有真实 Resume 回归测试保护。

## 60 秒面试说明

“我没有把完整 Matrix 一次塞给模型，而是按人工批准大纲逐节筛维度，只给对应 Matrix rows 和它们引用
的 Evidence。每节输出结构化 Claim、短摘要和术语；后节只看到前文摘要和统一术语。完成后我复用
Phase 2 ClaimSet，但增加 PostgreSQL get-or-add：并发赢家的 Claim ID 会被回读，再用它写 Citation，
同时比较完整语义防止唯一键掩盖冲突。Citation Validator 除了零引用、伪造和跨 Run，还逐条核对
Matrix Paper 到 READY Source、Version、Revision 和 Evidence 的闭包。最后一致性模型只报告术语、
矛盾和冗余 issue，不自动重写或决定事实正确性。所有业务正文留在 Output/Claim 表，Graph State 只
放 ID；Artifact 未完成前图停在安全边界。”
