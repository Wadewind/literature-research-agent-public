# arXiv 检索与项目导入

## 模块解决的问题

Review Workflow 需要把可能超时且会重复执行的外部论文发现，转换为当前 owner 与 Project 内可追踪的
Paper、PaperVersion、Ingestion Run 和依赖事实。本模块实现受限 arXiv 查询、官方 PDF 下载、内容
寻址缓存与幂等项目登记，并明确外部 I/O、Storage 和数据库事务的边界。

## 边界与执行流程

```text
Actor + Project + Review Run + 已验证 SearchQuery
  → 短事务：授权并复用 SEARCH_ARXIV 完成 Step
  → 事务外：官方 arXiv API 检索
  → 短事务：再次锁范围，保存 Source + Step + Event
  → 逐篇事务外：PDF 流式下载、校验、owner+SHA-256 缓存写入
  → 逐篇短事务：登记/复用 Paper、Version、ProjectPaper、Ingestion Run、Event、Outbox
  → 绑定 ReviewSource，建立 PaperVersion/Run 或已有 ChunkSet 依赖
```

搜索策略模型不属于本模块。后续图节点只能把模型输出解析为 `ArxivSearchQuery` 后调用 Adapter，不能
让模型提供 URL 或绕过允许字段、排序、分页和预算校验。

## HTTP、下载与安全

- API 固定为 `https://export.arxiv.org/api/query`，PDF 仅允许 `arxiv.org` 和
  `export.arxiv.org`；
- Feed 中的官方 `http` PDF link 规范化为 `https`；非官方 Host、凭据、错误路径或与 entry
  ID/version 不匹配的 link 立即拒绝；
- 下载关闭自动重定向，每一跳重新校验 scheme、Host、凭据和 `/pdf/` 路径；
- Atom Feed 与 PDF 都通过 streaming 累计读取；Feed 默认上限 2 MiB，PDF 单文件默认 50 MiB 并受
  Review 剩余总预算约束；
- MIME 必须是 `application/pdf`，Content-Length 必须是非负整数（允许缺失），正文必须以
  `%PDF-` 开头，最后计算 SHA-256；`DownloadedPdf` 自身也重新校验 MIME、magic 与摘要一致性，
  避免替换 Gateway 绕过内容寻址不变量；
- Adapter 的 Feed/PDF 上限、重定向次数、尝试次数和 PDF Host allowlist，以及 Service 总下载预算
  都在构造时 fail-fast；Host allowlist 只能取官方 Host 的非空子集，不能用配置扩成任意 Host；
- timeout、transport、429、5xx 最多尝试三次，耗尽后向 Run 层上抛；404、非法 PDF、重定向和超限
  属单篇永久失败；
- 普通测试全部使用 RESPX/httpx2 mock，不访问实时 arXiv，也不发送用户 Cookie 或凭据。

## 事务、缓存与幂等

外部搜索、PDF 下载和 `Storage.write()` 均不在数据库事务内。缓存 key 只由可信 owner ID 和内容哈希
组成：

```text
{owner_id}/arxiv-cache/sha256/{sha256}.pdf
```

数据库失败时不删除缓存，因为另一个并发事务可能已经引用同一内容；它作为可对账对象保留。搜索用
`SEARCH_ARXIV` 成功 Step 保存查询指纹，解决普通结果和“零结果没有 Source”两种回放。提交前再次锁
父 Run，因此并发搜索最多产生一组 Source、一个 Step 和一个 Event。

每篇登记先对 owner+SHA-256 获取 PostgreSQL transaction-level advisory lock，再查询既有 owner/hash
唯一索引。不同 Review Run 并发首次导入相同 PDF 时，后进入者会复用已提交 Version，不向业务暴露
唯一冲突。新建时 Paper、Version、ProjectPaper、Ingestion Run、`run_created` Event、Outbox、
ReviewSource 和依赖同事务提交；现有 Ingestion Worker 继续创建 Indexing Run，本服务不调用 Executor。
解析与索引的配置幂等继续使用既有 `(PaperVersion, ParseProfile hash)` 和
`(ParseRevision, ChunkProfile hash)`，而不是在 arXiv 导入层复制一个可能漂移的名义 Profile 版本。

## 归属、失败与恢复

- 搜索前、提交时和单篇登记时都校验 Review Run、Project 与 owner；
- ready ChunkSet 通过 Revision→Version 连接查询，不能绑定另一 Version 的 ChunkSet；
- 历史 Ingestion Run 只有 owner、Project 和 RunType 都匹配才成为 RUN 依赖；跨 Project 时只保留
  PaperVersion 事实给切片 4 对账；
- 已归档 Paper 不自动恢复，以 `review_source_paper_archived` 失败，避免生成 RAG 不可见的 ready
  Source；
- 单篇永久错误保存稳定 failure code 并继续；临时错误保持 Source `discovered` 并上抛供 Run retry；
- 重复 Job 对 `importing/ready/failed` Source 直接复用，不重复下载或建子 Run；
- 已有 ready ChunkSet 直接形成 satisfied PaperVersion/ChunkSet 依赖；其余来源由切片 4 Reconciler
  对账并恢复父 Run。

Event 只记录 Source、Paper、Version ID、数量和错误码，不记录 PDF、Feed 正文或摘要全文；PDF 不进入
Graph State。

## 重要测试和运行结果

- 领域：查询规范化、允许字段、URL/控制字符拒绝、现代与旧式 versioned ID；
- HTTP Mock：Atom 映射、顺序、去重、截断、URL 注入、重定向 Host、临时重试、404、MIME、
  Content-Length、magic bytes、流式大小/总预算和 SHA-256；
- 应用：零结果完成事实、出网前权限、事务外 Storage、部分失败、临时失败、重复 Job、ready ChunkSet、
  跨 Project Run 和归档 Paper；
- PostgreSQL：两个 Review Run 并发首次导入同 hash 收敛为一套导入 bundle；注入失败验证 Paper、
  Version、ProjectPaper、Ingestion Run、Run Event、Outbox 与 Dependency 整体回滚而缓存保留。

实际结果：最终补强后的定向领域/Adapter/应用测试 `47 passed`，定向 PostgreSQL 集成测试
`2 passed`；Backend
非集成全量 `446 passed, 4 skipped`，PostgreSQL/Valkey 集成全量 `95 passed`；Ruff、Pyright 和
diff check 通过。

## 代码入口

- `backend/src/literature_agent/domain/arxiv.py`
- `backend/src/literature_agent/application/ports/arxiv_gateway.py`
- `backend/src/literature_agent/infrastructure/arxiv.py`
- `backend/src/literature_agent/application/arxiv_import_service.py`
- `backend/src/literature_agent/infrastructure/persistence/review_repository.py`
- `backend/src/literature_agent/infrastructure/persistence/paper_version_repository.py`
- `backend/src/literature_agent/infrastructure/persistence/chunk_set_repository.py`

## 已知限制

- Service 尚未接入 Review Worker/LangGraph/API；切片 4 已接入独立依赖对账循环并完成父 Run
  等待/恢复，具体 Review Executor 由切片 5 接线；
- 当前只顺序下载，不实现多来源、任意 URL、候选人工筛选或用户可调并发；
- 事务失败可能留下未引用缓存，尚无孤立缓存清理器；删除前必须先做引用对账；
- Phase 1 上传入口仍沿用既有事务内 `Storage.write()`；本切片没有静默改变其 storage key/API。Phase 3
  arXiv 路径已满足文件写入不在数据库事务内，Phase 1 重构应是独立可靠性变更；
- Adapter 参数尚未接入 Worker 依赖组装，后续接线从 Review Profile 快照传入。

## 60 秒面试说明

我没有让 Review Worker 下载 PDF 后直接调用解析器，也没有在数据库事务里执行网络和文件 I/O。
系统先验证 Project/owner/Review Run，再用只允许官方 Host 的 Adapter 检索和流式下载；每次重定向都
检查 Host，PDF 受 MIME、magic、50 MiB、总预算和 SHA-256 约束。文件先写入 owner 隔离的内容寻址
缓存，再以短事务登记 Paper、Version、ProjectPaper、Ingestion Run、Event、Outbox、ReviewSource 和
依赖。并发首次导入相同内容通过 advisory lock 加 owner/hash 唯一索引收敛，数据库失败也不会危险
删除并发可复用缓存。已有 ready ChunkSet 按 Version 归属复用，否则只保存依赖事实交给 Reconciler，
因此至少一次投递不会重复产生业务效果，PDF 和大型响应也不会进入 Event 或 Graph State。
