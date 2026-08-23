# 离线 Demo Fixture 与 Fake arXiv

## 模块解决的问题

Phase 3 虽已有 Fake Parser、Embedding 和 Chat，但 Worker 始终装配真实 `HttpxArxivGateway`，因此
“Fake 模式”仍可能访问实时 arXiv。本模块补齐最后一个外部 Adapter，并提供版本化合成论文，使本地
Review 演示确定、零费用且不依赖网络。

## 边界与执行流程

`Settings.arxiv_backend` 默认 `fake`。Worker 生产组装边界按值选择：

```text
fake  → FixtureArxivGateway → review-demo.v1 manifest/PDF（本地只读）
httpx → HttpxArxivGateway   → 官方 arXiv API/PDF
其它  → ValueError，Worker 启动失败
```

`scripts/dev.sh --fake` 不读取 `.env`，同时导出四个 Fake backend；`--real` 才读取 `.env` 并显式选择
Docling、OpenAI-compatible Embedding/Chat 与 HTTP arXiv。Fake Adapter 只实现既有 `ArxivGateway`
Port，没有引入新业务抽象。

## Fixture 与数据契约

`review-demo.v1` 是仓库内完全合成语料，manifest 固定 arXiv ID/version、元数据、顺序、场景以及每个
成功 PDF 的 size/SHA-256。Adapter 构造时即校验文件存在、大小、hash 和 PDF 基本契约，漂移或缺失
会 fail fast。前三条返回稳定 PDF 字节，第四条返回永久错误 `fake_arxiv_pdf_unavailable`。分层自动
测试共同证明部分来源失败仍继续、Fake Chat 产生合法 `insufficient_evidence` Matrix 行，以及 Outline
feedback 后再次 interrupt 并可继续；不把这些独立测试表述为某一个测试跑完了完整 Review E2E。

PDF 由 Fake Parser 处理，Fixture 不承担 Docling/pypdf 质量评测；真实 Parser 仍使用单独的合成契约
语料并需显式测试开关。

## 事务、幂等、失败和取消

Fake Adapter 只在事务外读取版本化文件。后续仍由 `ArxivProjectImportService` 在短事务中校验
owner/Project/Run、写入 Paper/Version/ProjectPaper/Ingestion Run/Event/Outbox/Dependency。Adapter
不保存状态；重复 search/import 继续复用持久 Step、Source 和唯一约束。单篇永久失败写入稳定 Source
错误并继续，其余来源的至少一次、取消和依赖恢复语义没有变化。

Fake 模型栈的 ChunkProfile 固定使用 `unicode-word.v1` 计数器，它只依赖 Python Unicode 规则，因而
空 tiktoken 用户缓存且禁网时也能完成 Indexing/RAG。真实模型栈仍显式使用 `cl100k_base`，没有改变
真实 ChunkProfile 或 tokenizer 语义。

## 安全和可观测性

- 默认配置 fail closed 到 `fake`，拼错 backend 不回退真实网络；
- Fixture URL 使用内部 `fixture://` 标识，只由 Fake Adapter 解析，不接受用户路径；
- manifest/PDF 不含真实论文、用户数据、Secret、Prompt 或 Provider 响应；
- 普通测试不读取 `.env`，Fake Adapter 不创建 HTTP Client，也没有费用路径。

## 重要测试和运行证据

- `tests/infrastructure/test_fake_arxiv.py`：固定排序、版本、PDF hash、预算、稳定失败，以及篡改、缺文件
  和非法 manifest 的启动失败；
- `tests/test_worker.py`、`tests/infrastructure/test_config.py`：默认 Fake、显式 Real、未知值 fail closed；
- `tests/application/test_arxiv_import_service.py`：生产 Fixture 接入真实应用服务，首次与重放均为 3 个
  成功来源、1 个失败来源和 3 个 Ingestion Run；
- `tests/application/test_review_outline_service.py`：feedback 生成 v2 Outline 并再次 interrupt，随后
  approve；
- `tests/application/test_review_executor.py`：重复执行/人工恢复后 Review 到 `SUCCEEDED`，图外模型、
  arXiv 和 Matrix 外部效果各只发生一次。

构建验证使用 `uv build` 在 `/tmp` 生成 wheel/sdist；两种归档均包含 manifest 和三个 PDF，从 wheel
安装到独立 `/tmp` 目录后能实例化 Adapter、搜索四条来源并读取首篇 121-byte PDF。测试总数与静态
检查结果记录在 Phase 4 Spec 的切片进度中。

## 代码入口

- `backend/src/literature_agent/infrastructure/fake_arxiv.py`
- `backend/src/literature_agent/infrastructure/fixtures/review/v1/`
- `backend/src/literature_agent/infrastructure/config.py`
- `backend/src/literature_agent/worker.py`
- `scripts/dev.sh`

## 已知限制

- Fake 搜索不模拟查询相关性、分页变化、限流或临时网络错误；对应行为由真实 Adapter 的 HTTP Mock
  测试覆盖；
- Fake Parser 有意忽略 PDF 正文，因此 Fixture 不能代表真实解析或内容质量；
- 完整 UI Playwright 演示属于 Phase 4 切片 9，本切片只提供后端离线闭环与确定性自动测试证据。

## 60 秒面试说明

我把 arXiv 和 Parser、Embedding、Chat 一样放在生产依赖组装边界选择，并让 arXiv 默认 fail closed
到 Fake。Fake Adapter 不创建 HTTP Client，只读仓库内版本化的四篇合成论文：三篇成功、一篇稳定
失败；manifest 用 size/hash 防止同版本语料静默漂移。导入应用测试与既有 Review/HITL 测试分层证明
部分失败、证据不足、feedback 和重放语义，且 Fake tokenizer 不需要联网下载词表。Paper、Run、Event
和 Outbox 仍由 PostgreSQL 事务与原有唯一约束拥有；真实 arXiv 与真实 tokenizer 语义只由显式 Real
配置启用。
