# Agent 输入附件

## 解决的问题

Research Agent 需要读取用户明确交付给本轮的小型研究文件，但不能把原始内容塞进消息、Run、Event 或 LangGraph State，也不能让跨 owner/Session 文件或上轮临时文件被静默继承。

## 边界与流程

1. API 在 10 MiB 上限内读取 multipart，检查文件名、扩展名、MIME、magic 和结构；
2. `AgentAttachmentService` 先在短事务中验证 Session/幂等键，在事务外写 Storage，再在短事务中以唯一约束收敛不可变事实；并发同请求返回同一附件及准确 replay 状态，不同请求复用键稳定返回 409；
3. 发送消息时最多引用 5 个 AVAILABLE 附件，按附件 ID 稳定顺序持有 scoped `FOR UPDATE` 行锁，直至有序 Message 引用和 `ContextSnapshot` 精确元数据一同提交；
4. Worker 取得当前 fenced Sandbox 后，在模型/Tool 前清空 `/workspace/inbox`，从 Storage 重验并物化本轮引用；
5. Deep Agents 只获得受控路径和必要元数据。`WorkspaceSnapshot` 暂存排除 inbox，恢复遇到 inbox 则拒绝。

## 状态、数据与事务

`AgentAttachment` 只有 `AVAILABLE -> DELETED`，首版固定 `version=1`。删除先锁定同一 scoped 附件行，再复核历史引用；因此消息先赢时删除等待后返回 referenced，删除先赢时消息等待后按不可用 fail closed。未引用删除只改业务状态，不在事务中删 Storage。上传使用 owner + `Idempotency-Key` 唯一约束和稳定 ID，消息 request hash 包含有序附件 ID。

Web 为文件名、大小、声明类型和 `lastModified` 组成的附件文件身份保存上传意图。响应丢失后重选同一文件复用 `Idempotency-Key`；成功或更换文件才换键。Project/Session key 变化会重建交互状态，清除附件选择、消息意图和上传意图。活动 Turn 或上传/删除 pending 时，附件选择与删除控件不可操作。

## 失败、取消与安全

跨 owner/Project/Session、删除状态、元数据/hash 漂移、Storage 缺失、路径穿越、非当前 Run、取消、旧 generation/fence、inbox 清空/上传失败都在模型前 fail closed。Event 只记录 `attachment_count`。本切片不为 Session 上传泛化 Run-bound Event，不提供 `browser_file_upload`。

## 已知限制

- Storage 写入成功、DB 失败会留下不可见孤儿 blob，尚无 GC；
- 删除只令未引用附件不可用，物理 blob 暂保留；
- 固定类型和基础结构检查不等于生产级恶意文件扫描；
- Project 论文全文仍优先使用 Project Library/Index，不经附件复制进 Graph State。

## 重要测试和实际结果

- 完整后端非 integration 回归：1066 passed、6 skipped；
- 附件 Application/Repository/Alembic PostgreSQL：19 passed，其中真实竞态覆盖同键上传和消息/删除两种锁赢家；
- Agent Session/Attachment API：12 passed；
- 主智能体定向组合复核：33 passed（30.67s）；
- Domain/Materializer/Runtime/Workspace 新增定向：41 passed；
- Web 全量 Vitest：156 passed；TypeScript/Vite production build 通过；
- Ruff 对本切片改动文件通过，Pyright 零错误。

## 代码入口

- `domain/agent_attachment.py`
- `application/agent_attachment_service.py`
- `application/agent_attachment_materializer.py`
- `api/agent_attachments.py`
- `infrastructure/agent/attachment_inbox.py`

## 60 秒面试说明

输入附件是业务授权事实，而不是 Sandbox 目录里“碰巧存在”的文件。平台把不可变元数据冻结进本轮 ContextSnapshot，在事务外从 Storage 重验，只向当前 fenced Sandbox 的每轮 inbox 上传。这样既复用 Deep Agents 的文件工具，又不把所有权、取消、恢复和审计交给 SDK。
