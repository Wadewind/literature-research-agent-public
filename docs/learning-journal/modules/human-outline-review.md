# 人工大纲确认与恢复

## 模块解决的问题

Review Workflow 在 Evidence Matrix 后需要生成结构化大纲、释放 Worker 等待用户，并在进程重启后从
同一 LangGraph Thread 恢复。难点不是调用一次 `interrupt()`，而是让模型 Output、人工请求、业务
Run、Attempt、Event、Outbox 和 Checkpoint 在重复执行、并发提交与崩溃窗口中保持可解释的一致性。

## 边界和执行流程

```text
ReviewOutlineService.propose_and_pause()
  → 短事务读取并复核 Search Strategy / Matrix / Evidence / Source
  → 事务外调用 outline_generate.v1
  → 独立短事务 get-or-add Outline Output + 完成首轮 Outline Steps
  → 持锁事务创建 OPEN Request、更新 current outline/stage
  → REVIEW_OUTLINE Step=PAUSED
  → Run RUNNING→WAITING_INPUT + 两个 Event
  → LangGraph review_outline 调用 interrupt()

用户提交 approve / edit / feedback
  → HumanOutlineInputService 持锁校验当前 Request 和范围
  → 保存不可变 HumanInput、解决 Request、推进 Step
  → human_input_submitted Event
  → Run WAITING_INPUT→QUEUED + Outbox DISPATCHED→PENDING
  → 新 Worker / 新 Attempt
  → Command(resume={request_id, human_input_id})
  → 后继 Node 从业务库复核持久事实
  ├─ approve/edit → approved_outline_output_id → Section/Artifact 固定后继图
  └─ feedback → feedback_round+1 → 新 Outline/Request → 再次 interrupt
```

本模块完成时 API、SSE 与生产接线留给切片 9；现在后端 API、通用 SSE、完整生产图和 Phase 4
结构化前端表单均已接通。前端不编辑 JSON：标题、目标和分析维度进入结构化字段，approve/edit/
feedback 仍映射到同一版本化 HumanInput API。

Phase 4 表单同时支持 section key、章节增删和顺序调整，并在浏览器侧按 Domain 相同的章节数量、key、
文本与维度边界给出确定性提示。只有业务 Run 为 `WAITING_INPUT`、开放 Request 与当前 Outline 匹配时
允许操作；dirty edit 禁止 approve 仍在服务端的旧版本。409 stale/conflict 会立即刷新服务端 Query，
但相同提交意图仍保留原 Key，避免响应丢失后的重试产生第二个 HumanInput。

## Outline 上下文和 Validator

模型只接收：

- 研究问题；
- 当前 `search-strategy.v1` 的 3–6 个分析维度；
- 再次通过 Evidence Matrix 确定性 Validator 的 finding/limitations/status 受控摘要；
- 有效/失败论文覆盖统计；
- feedback 轮次和从持久 HumanInput 读取的反馈文本。

模型不接收 PDF/Chunk 全文、Evidence ID、未知 arXiv metadata、完整 Prompt 历史或 Request payload。
Matrix 在进入 Prompt 前按 Paper 分组复用 `validate_evidence_matrix()`，重新验证完整维度、状态组合、
finding 边界和 Evidence 的 Run/Project/Paper/PaperVersion 闭包；失败清单、Source 和统计也必须闭合。
同一 Evidence 可以支持不同维度，但单行不能重复引用同一 ID。

`outline.v1` 固定：1–12 节；`section_key` 是最多 64 字符的 snake_case；title 最多 200 字符；purpose
最多 1,000 字符；每节包含 1–6 个不重复且属于 Search Strategy 的维度；整体 JSON 不超过 64 KiB。
feedback 去除首尾空白后为 1–4,000 字符。

## 数据和事务边界

模型调用不能放在数据库事务内。生成流程因此采用两个 crash-safe 边界：

1. Outline Output 以 `review_run_id + output_type/key/version` 和稳定幂等键追加提交；
2. 随后在持锁事务中提交 Request、ReviewRun 指针、Step、Run 和 Event。

若在 1 后、2 前崩溃，重放先回读并比较 Output 的完整稳定语义，不重调模型。若 2 已提交而 checkpoint
尚未写入，业务 Request 仍是事实来源；用户输入恢复时只把持久 ID 放进 Command，后继节点重新回查。

人工提交的单事务包含：

- 可选的 edit Outline 新版本；HumanInput 仅保存批准的 Output ID，不重复内联最多 64 KiB 的 Outline；
- HumanInput；
- Request `OPEN → RESOLVED` 和 `resolved_input_id`；
- 可选的 ReviewRun current outline 更新；
- `REVIEW_OUTLINE` Step 恢复/成功；feedback 则保持本轮循环可继续；
- `human_input_submitted` Event；
- Run `WAITING_INPUT → QUEUED`；
- Outbox `DISPATCHED → PENDING`。

任何一步失败都回滚全部效果。正常恢复复用 `WaitingRunResumeService.resume_in_session()`，不增加失败
Attempt 计数，也不消耗最大失败重试预算。

## LangGraph Interrupt、Resume 与 Graph State

`review_outline` Node 在 `interrupt()` 之前只校验 Graph State 中已有的 Request/Output ID，并构造小型
interrupt value；它不调用模型、数据库、Event 或通知。LangGraph Resume 会从该 Node 开头重执行，
因此这一约束避免了重复副作用。

Runtime 保留两条不同路径：

- Worker 崩溃且没有人工输入：`resume(run_id)` → `ainvoke(None)`；
- 已持久化人工输入：`resume_human_input()` →
  `Command(resume={request_id, human_input_id})`。

Resume 字典本身不可信。`ReviewOutlineDecisionService` 校验 Request 已解决、HumanInput ID/版本/action
闭合，并复核 Request Outline 与 approve/edit 的批准 Outline 均属于当前 owner/Project/Run。

Graph State 只增加 `human_input_request_id`、`human_input_id`、`feedback_human_input_id`、
`outline_action`、`feedback_round` 和 Output ID。approve/edit 明确设置
`approved_outline_output_id`；feedback 单调增加 round，然后由 Node Adapter 使用持久 HumanInput ID
生成下一版本。

## 幂等、重复、并发和失败

- Output、Request 和 HumanInput 使用数据库唯一约束与 `ON CONFLICT DO NOTHING` 收敛并发；回读后
  必须比较稳定语义，不能仅因主键存在就视为成功；
- 同一提交者幂等键同语义返回原 HumanInput；同键异义或 Request resolve 闭包异常时拒绝；幂等键最多
  255 字符，与数据库列一致；edit 重放从 scoped Outline Output 读取完整结构后再比较；
- Request 行锁、状态条件更新和 `human_inputs.request_id` 唯一约束保证同一请求只解决一次；
- Request 版本、outline_output_id、allowed actions、ReviewRun current pointer/stage 任一过期都拒绝；
- 模型 Outline 非法时不创建 Request，也不让 Run 进入 WAITING_INPUT；
- Outline 范围/结构错误属于永久 Worker 错误；模型/数据库临时故障仍由既有错误层负责；
- Outbox 不是 `DISPATCHED` 时恢复事务失败，HumanInput、Request、Step、Run 和 Event 全部回滚；
- feedback 最大轮次尚未设置。当前只保证非负、单调、版本闭合和每次输入有界。

## 安全和可观测性

- owner/Project/Run 范围用于每次 Output、Request、HumanInput 和 Evidence 读取；
- Event 只保存 Request/Input/Output ID、action、版本和允许动作，不保存反馈全文、完整大纲或模型上下文；
- Graph Resume 只传稳定 ID，数据库事实决定真正 action 和批准 Output；
- `outline_proposed`、`human_input_requested`、`human_input_submitted` 构成用户可见时间线；Attempt 记录
  Worker 占用历史，Outbox 只表示当前投递需求；
- 进入 WAITING_INPUT 后 `RunExecutionService` 按既有语义把当前 Attempt 关闭为 PAUSED。

## 重要测试和运行结果

- Domain：Outline Schema、key/文本/维度/总大小、feedback 边界、Step pause/resume；
- Application：受控 Prompt、完整 Matrix 复核、非法模型无 Request、Output 后崩溃重放、approve/edit/
  feedback、幂等/过期/跨范围、反馈循环和持久 Decision 闭包；
- LangGraph：MemorySaver 真实 interrupt、approve/edit Resume、feedback 再次 interrupt、Runtime 重建；
- PostgreSQL：并发双提交只有一个业务效果；Outbox 失败时全部业务状态回滚；
- PostgresSaver：关闭首个连接/Runtime 后，新连接/Runtime 使用同一 Thread 和 Command Resume，副作用
  Node 不重复执行。

最终完整测试数量在切片提交报告与 Phase 3 §18.7 中记录。

## 代码入口

- Validator：`backend/src/literature_agent/domain/review_outline.py`
- 大纲与输入用例：`backend/src/literature_agent/application/review_outline_service.py`
- Repository Port/Adapter：`backend/src/literature_agent/application/ports/review_repository.py`、
  `backend/src/literature_agent/infrastructure/persistence/review_repository.py`
- Graph/Runtime：`backend/src/literature_agent/workflows/review_graph.py`
- Node Adapter：`backend/src/literature_agent/workflows/review_outline_nodes.py`

## 已知限制

- 尚未设置 feedback 最大轮次、模型调用和 token 总预算；后续按 Profile 校准；
- Outline 模型没有结构修复调用；非法输出稳定失败，避免本切片引入未讨论的额外模型成本；
- 只验证结构、范围与 Evidence 闭包，不自动判断大纲是否具有学术价值；
- 后端 API、SSE 和生产 Review Executor 已在切片 9 接线；Phase 4 切片 4 已接入结构化人工表单。
  浏览器交互意图只保存请求签名和 `Idempotency-Key`，相同失败提交复用，同一 Request 的不同 action、
  payload 或新版本生成新 Key；真正的 stale/version/owner/Project 判定继续完全属于后端。

## 60 秒面试说明

“我把大纲人工确认拆成业务事务和 LangGraph 恢复两层。模型在事务外生成 `outline.v1`，Output 先用
稳定版本键提交；随后 Request、ReviewRun 指针、WAITING_INPUT、Step 和两个 Event 在持锁事务中提交，
所以 Output 后崩溃只会重放并复用。用户输入保存、Request resolve、Run 重新排队、原因 Event 和
Outbox 重置又在同一事务中完成，正常 Resume 不算失败重试。`review_outline` Node 在 interrupt 前完全
无副作用；人工输入先进入 PostgreSQL，然后新 Runtime 用 `Command(resume={request_id,input_id})`
恢复。后继节点不信任 Command 内容，而是回查 Request/HumanInput/Outline 闭包。feedback 会生成下一
版本并再次 interrupt，approve/edit 则设置明确的 approved Output ID。”
