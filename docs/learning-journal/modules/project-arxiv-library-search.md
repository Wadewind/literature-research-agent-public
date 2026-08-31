# Project 文献库 arXiv 搜索与引入

## 解决的问题

论文来源发现原先只存在于固定 Review Workflow 内，用户必须创建 Review Run 才能搜索并导入 arXiv
论文。该模块将来源发现恢复为 Project 文献库能力，使上传、个人库复用和在线搜索共用同一个添加入口。

## 边界与执行流程

```text
用户检索词
  → Project/owner/归档校验
  → 受限 ArxivSearchQuery
  → 官方 arXiv Adapter
  → 返回不含 PDF URL 的公开元数据

用户选择 versioned_arxiv_id
  → Project/owner/归档校验
  → 确定性解析 arXiv ID 与版本
  → 按 ID 重新读取并校验官方 arXiv 标题
  → 构造官方 HTTPS PDF 地址
  → Adapter 下载并校验 PDF
  → IngestionService 写入 arXiv 标题、内容哈希、幂等、Project 关联与 Run 创建
  → Worker 继续解析和索引
```

Route 只处理 HTTP 输入输出和错误映射。`ProjectArxivLibraryService` 先在短数据库会话中授权，关闭会话后
再执行搜索或下载；PDF 内容继续由既有 `IngestionService` 管理，不复制 Paper、Version、Run、Event、
Outbox 或 Storage 逻辑。

## 状态、事务与失败

- 搜索结果是临时查询结果，不进入业务数据库；导入后的 Paper、PaperVersion、Run、Event 和 Outbox
  仍以 PostgreSQL 为事实来源。
- 客户端只能回传版本化 arXiv ID，不能提交任意 URL；服务端只构造官方 `https://arxiv.org/pdf/` 地址。
- Project 不存在、跨 owner 或已归档时，在外部 I/O 前拒绝。
- 临时 arXiv 错误映射为 `503`；非法选择、下载或文件校验错误返回稳定的 `4xx`；幂等冲突返回 `409`。
- 下载成功后的重复提交由现有 IdempotencyRecord 与文件哈希复用，不宣称分布式 Exactly Once。
- 每篇搜索结果独立跟踪导入请求；某篇请求进行中只禁用该行，其他候选仍可继续引入。
- 新 Paper 在异步解析前即保存 `arxiv_metadata` 标题；同哈希复用已有 Paper 时也会按标题来源优先级
  回填或升级标题，避免 UI 长期退化显示 `arxiv-<id>.pdf`。

## 安全与可观察性

- 搜索表达式沿用字段 allowlist、长度、控制字符与 URL 禁止规则。
- 下载沿用官方 Host、重定向次数、Content-Type、Content-Length、字节预算与 PDF magic 校验。
- API 不向浏览器暴露 Adapter 返回的 PDF URL。
- 导入继续携带 `Idempotency-Key` 与 `X-Correlation-ID`，后续 Run 可通过既有 Event 和状态接口观察。

## 代码入口

- 应用服务：`backend/src/literature_agent/application/project_arxiv_library_service.py`
- HTTP 路由：`backend/src/literature_agent/api/project_arxiv.py`
- 共享 Adapter 生命周期：`backend/src/literature_agent/infrastructure/lifespan.py`
- Project 文献库 UI：`web/src/pages/LibraryPage.tsx`

## 重要测试与验证

- `tests/application/test_project_arxiv_library_service.py`：跨 owner、归档前置拒绝、官方 URL 与 Ingestion
  复用。
- `tests/api/test_project_arxiv.py`：公开响应、幂等请求头和导入响应。
- 2026-08-31 并发与标题修复：应用服务与相关 API 共 29 项定向测试通过，相关 Ruff、Pyright 通过；
  前端 40 files / 197 tests 与生产 build 通过。`test_paper_files.py` 依赖本地 PostgreSQL/Valkey，沙箱内
  因网络隔离等待，改在已授权的本地环境运行后 7 项通过。
- 浏览器检查验证 URL `?add=search`、三种添加方式、搜索结果布局、文献研究首页/详情以及 760px 窄屏；
  当前已运行的后端进程未热加载新路由，因此真实浏览器搜索需重启开发环境后再点击确认，API 行为由 ASGI
  测试覆盖。

## 已知限制

- 当前只支持 arXiv，单次最多返回 20 条，UI 固定请求 10 条，不含分页和候选集合持久化。
- `review.v1` 为兼容历史重放仍会自动搜索并导入来源；本模块不会静默改变已冻结 Workflow Profile。
- 搜索结果尚未标注“已在当前项目”，重复引入依赖服务端内容哈希与项目关联幂等收敛。
- 独立引入会为每篇选中论文额外执行一次精确 ID 元数据查询，以保证标题来自服务端重新校验的官方来源；
  多篇并发请求仍受 arXiv Adapter 的单连接与请求间隔限制，不等同于并行轰击上游。

## 60 秒面试说明

我把 arXiv 来源发现从 Review UI 中抽成了 Project 文献库的独立垂直切片。服务先验证可信 actor 对 Project
的所有权和归档状态，然后在事务外调用已有受限 arXiv Adapter。浏览器只收到公开元数据，也只能回传经过
格式校验的版本化 arXiv ID，不能控制下载 URL。服务端构造官方 PDF 地址并把校验后的内容交回原有
IngestionService，所以内容哈希、幂等、Paper/Version、异步解析索引、Event 和 Outbox 都没有复制。
Review 路由仍保持兼容，但产品界面改称“文献研究”，并把 Evidence Matrix 提升为主要分析结果。
