# 产品决策 0003：Phase 3 固定文献综述 Workflow

- 状态：已接受
- 日期：2026-08-22
- 决策者：项目维护者
- 修订：[产品决策 0002](0002-archive-and-project-scoped-entrypoints.md) 中“搜索候选只有在人工纳入后才能新增 ProjectPaper”的 Phase 3 行为

## 背景

Phase 2 已完成带 Evidence 和 Citation 的 Project-scoped RAG。Phase 3 的核心学习目标是通过 LangGraph 实现可持久化的 interrupt/resume，并理解 checkpoint 与业务 Run、Attempt、Event、Outbox 和依赖 Run 的边界。

如果第一版同时建设多来源学术检索、开放获取解析和论文人工筛选，会显著扩大产品与安全范围，却不能直接加强这一学习目标。因此需要收敛论文来源、HITL 位置、Evidence 生成和恢复语义。

## 决策一：arXiv 单来源并自动导入

- 检索、元数据和 PDF 下载只使用 arXiv 官方 API/地址；
- 系统按排序自动导入前 N 篇论文到当前 Project，不设置候选论文人工筛选；
- N 是下载预算，初始默认 10，并保存在 Workflow Profile 快照中；
- 不接入 OpenAlex、Crossref、Unpaywall、多来源 OA 或任意用户 URL 下载；
- 部分论文失败时允许用其余成功论文继续，全部不可用时以稳定错误码终止。

该决定修订 ADR-0002 决策三中“搜索候选只有在人工纳入后才能新增 ProjectPaper”的描述。Project-scoped 授权、归档限制和历史可追溯性保持不变。

## 决策二：HITL 只用于大纲确认

固定 Workflow 只在大纲阶段调用 LangGraph `interrupt()`。用户可以批准、提交结构化编辑，或反馈后要求重新生成大纲。

Interrupt 节点在 `interrupt()` 前不执行模型、数据库写入或其他不可重复副作用，因为 Resume 会从该节点重新执行。HumanInput 必须先持久化和校验，再重新调度 Review Run。

`outline_generate.v1` 只接收研究问题、固定分析维度、已验证 Matrix 的受控摘要和覆盖统计。`outline.v1` 使用确定性 Schema/载荷 Validator；Outline 先版本化持久化，再以幂等短事务创建 OPEN Request 并把 Run/Step 推入等待。用户提交时，HumanInput、Request resolve、Run 重新排队、Event 与 Outbox 重置同事务提交。崩溃恢复继续使用空输入恢复 checkpoint；真正的 HITL Resume 使用只携带持久 `request_id`/`human_input_id` 的 `Command(resume=...)`，后继节点仍以业务数据库为事实来源。feedback 生成下一版本和 Request；第一版暂不设置反馈轮次上限，但文本载荷有界。

## 决策三：等待通过业务状态和可重置 Outbox 恢复

- Review Run 创建或复用 Ingestion/Indexing 子 Run，不在 Review Worker 内直接执行导入器；
- 父 Run 等待 PaperVersion 形成可用 ChunkSet；
- 等待时 Run 进入 `WAITING_DEPENDENCY` 或 `WAITING_INPUT`，当前 Attempt 进入 `PAUSED` 并释放 Worker；
- 每个 Run 继续只有一条可重置 Outbox 记录，不保存完整队列投递历史；
- 正常依赖完成或 HumanInput 使用 `schedule_again()` 将 Outbox 从 `DISPATCHED` 重置为 `PENDING`，不增加失败重试计数；
- Run 转为 `QUEUED`、追加原因 Event 和重置 Outbox 必须在同一事务中完成；
- Event 保存业务时间线，Attempt 保存 Worker 执行历史。

## 决策四：Evidence-first 的固定提取与章节上下文

第一版固定使用 `review-evidence-extraction.v1`：短论文提供按序全文 Chunk；长论文按每个分析维度调用 Phase 2 Retriever，再合并、去重和限额；每篇论文使用一次正常模型调用提取全部维度，输出非法时最多追加一次修复调用。

Evidence Matrix 行保存 Paper、维度、finding、limitations、状态和真实 Evidence ID。确定性 Validator 校验 Schema、范围、归属和引用闭包，失败时允许一次结构化修复；“证据不足”是合法结果。

章节生成只接收该章节关联维度的 Matrix 行及其 Evidence，不接收所有论文全文或完整 Matrix。最终全文生成 ClaimSet 并复用 Citation Validator。

章节固定使用 `section.v1`，按批准 Outline 顺序生成。后续章节只额外接收前文短摘要和统一术语字典，
不接收前文章节全文。综述 ClaimSet 复用 Phase 2 表，但通过 `run_id`、Claim sequence 和 Citation 复合
主键的原子 get-or-add 收敛重放，并逐条复核 Matrix Paper 到 READY Source、PaperVersion、
ParseRevision 和 Evidence 的闭包。

`consistency_check.v1` 只生成结构化一致性报告。发现术语、矛盾或冗余 issue 是非阻断结果，第一版不
自动重写章节；模型调用、Scope 或 Schema 失败都会阻断当前执行，只有合法报告可以继续。Schema
失败稳定结束 Step，模型调用和 Scope 失败交给既有 Worker 错误分类/重试。这样一致性模型不会成为
事实正确性的通用 Judge，也不会引入未预算的修复循环。

## 决策五：固定版本和引用格式

- Workflow 使用 `review.v1`；
- Prompt 使用用途明确的 `name.v1`；
- Model Profile 使用 `review-default.v1`，Run 保存配置快照；
- Profile 快照包含 `section_output_token_limit=4000` 与
  `consistency_output_token_limit=2000`，并进入创建请求指纹；缺字段的早期 `review.v1` 开发 Run
  明确回退到同一默认值；
- Markdown 固定使用 `[1]` 数字引用，系统内部映射到精确 Evidence 和 PDF 定位；
- 第一版不提供引用样式切换。

编号按最终 Markdown 中论文的首次引用顺序分配，同一论文复用同一编号。导出同时持久化完整映射，
包含 Paper/PaperVersion、ReviewSource、arXiv ID/version、Claim、Evidence 及页码/章节定位；不能只把
不可回查的 References 文本写进 Markdown。

## 决策六：生产执行边界与 Artifact 提交

- `ReviewExecutor` 负责图外 Search Strategy、arXiv 导入和依赖等待；`WAITING_DEPENDENCY` 是业务 Run
  状态，不是 LangGraph Interrupt。持久图从 Outline 开始，Outline 是唯一 HITL，之后固定连接章节、
  引用、一致性、导出和 Finalize；
- Resume 前可以重复调用图外服务，但服务必须先复用持久 Step/Output/Source，保证零重复模型/arXiv
  调用和零重复业务副作用；只有明确没有 checkpoint 时才允许提交完整初始 State。损坏 checkpoint
  属永久错误，不能静默覆盖；
- 固定导出六类 Artifact：Markdown、Search Strategy、Source Manifest、Evidence Matrix、完整
  Bibliography 映射和 Run Summary；文件正文进入 Storage，PostgreSQL 只保存元数据、哈希、来源
  Output 与稳定幂等键；
- Storage 写入发生在数据库事务外，使用 owner/Run/content-hash 组成稳定 key。提交前重新持锁复核
  RUNNING Review；取消后不得新增 Artifact/Event/Stage。写文件后崩溃允许留下可复用缓存，重放以
  相同 key/hash 收敛，不做可能误删并发结果的补偿删除；
- Review API 按 owner+Project+Run 隔离。Project-scoped Event 列表用于游标读取，实时 SSE 复用通用
  Run Event Stream 与 `Last-Event-ID`，不另建一套 Review 通知事实来源。

## 后果

正面影响：Phase 3 能集中验证 LangGraph HITL、可靠等待恢复和 Evidence-first 生成；外部下载面更小；模型成本和章节上下文更可控；Run、Event、Attempt、Outbox 与 Checkpoint 的职责明确。

代价：第一版只覆盖 arXiv 论文，不能人工排除检索结果，也不等同于完整系统性综述流程。Phase 2 Retriever 的质量会影响长论文 Evidence Matrix；阈值、top K、下载预算和最小就绪论文数仍需通过测试校准。

## 被否决或延后的方案

- Phase 3 同时实现论文人工筛选：延后；当前 HITL 只服务于大纲。
- 多来源学术搜索和 OA 下载：延后到存在明确产品需求时重新决策。
- 每个维度分别把整篇论文交给模型：调用与 token 成本过高。
- 所有论文和 Matrix 一次性提供给章节模型：上下文过大且证据边界模糊。
- Append-only 队列投递历史：当前 Event 和 Attempt 已覆盖业务与执行审计，第一版不增加新表。
