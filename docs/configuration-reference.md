# 项目配置参考

本文统一说明当前项目的配置入口、默认值、作用域和安全边界。它描述的是当前实现，不替代阶段 Spec、
ADR 或模块笔记中的历史决策与验证证据。

项目采用三层配置模型：

```text
.env / 部署配置
  → 基础设施、Provider、Secret 与运行参数

PostgreSQL 中的用户选择
  → Project、RAG 范围、Agent Session 的 MCP/Skill 与每轮 Evidence Matrix

版本化 Profile/Catalog 与代码不变量
  → Workflow、Prompt、Schema、Tool、Sandbox、安全、权限和可靠性边界
```

## 1. 配置归属

| 类型 | 谁可以修改 | 保存位置 | 是否进入业务快照 |
|---|---|---|---|
| 部署配置 | 本地开发者/部署者 | `.env`、进程环境、OpenSandbox 配置 | 仅必要的版本/hash/引用进入 Run 事实，不保存 Secret |
| 用户业务选择 | 当前 owner | PostgreSQL，经 API/UI 修改 | 是；按 Conversation、Review Run、Agent Session 或 Turn 固化 |
| 平台 Catalog/Profile | 代码维护者 | 版本化源码与 ADR | 是；保存精确版本、配置 hash 与 Schema hash |
| 安全与可靠性不变量 | 不向用户开放 | Domain/Application/Adapter | 以状态、Event、唯一约束和策略快照体现 |

普通用户不能通过请求提交 Provider Key、MCP URL/command/env、Sandbox 镜像、网络策略、SDK Thread、
Workspace 物理路径或宿主执行配置。

## 2. `.env` 与启动配置

无密钥模板为仓库根目录 `.env.example`。复制后只在本机填写真实 Secret：

```bash
cp .env.example .env
chmod 600 .env
```

`.env` 已被 Git 忽略。`scripts/dev.sh --real` 会读取它；应用本身不自动加载 dotenv，手动启动 Worker 时
必须显式 `source`。可以通过 `AGENT_ENV_FILE=/absolute/path/to/provider.env` 让启动脚本读取其他文件；
这个变量只属于脚本，不进入 `Settings` 或业务快照。

### 2.1 基础设施与本地身份

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `AGENT_APP_NAME` | `Literature Review Agent` | 服务名称 |
| `AGENT_DATABASE_URL` | 本地 PostgreSQL | 业务事实与 LangGraph Checkpoint 数据库 |
| `AGENT_REDIS_URL` | 本地 Valkey DB 0 | ARQ Job 与实时通知，不是业务事实来源 |
| `AGENT_DEV_ACTOR_ID` | `dev-user` | 仅本地无认证演示的可信开发身份 |
| `AGENT_STORAGE_ROOT` | `data/storage` | PDF 与 Artifact 存储根目录；API/Worker 必须一致 |
| `AGENT_WORKER_METRICS_PORT` | `8001` | `0` 关闭；固定绑定 loopback |
| `AGENT_DEBUG` | `false` | 本地诊断开关，不是认证或安全模式 |

数据库 URL、Storage、开发身份和 Metrics 端口属于部署配置，不应出现在产品设置页面。

### 2.2 上传、后台执行与可靠性

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `AGENT_MAX_UPLOAD_SIZE_BYTES` | `52428800` | 单文件 50 MiB |
| `AGENT_PARSER_TIMEOUT_SECONDS` | `300` | PDF 解析超时 |
| `AGENT_WORKER_LEASE_SECONDS` | `600` | Worker Attempt lease |
| `AGENT_WORKER_HEARTBEAT_INTERVAL_SECONDS` | `30` | lease 心跳间隔 |
| `AGENT_WORKER_RECONCILE_INTERVAL_SECONDS` | `30` | 崩溃/孤儿任务对账间隔 |
| `AGENT_MAX_RUN_ATTEMPTS` | `3` | Run 最大 Attempt 数，包含首次执行 |
| `AGENT_OUTBOX_POLL_INTERVAL_SECONDS` | `1` | Outbox 派发轮询间隔 |
| `AGENT_OUTBOX_MAX_ATTEMPTS` | `10` | Outbox 派发尝试预算 |
| `AGENT_OUTBOX_DISPATCH_BATCH_SIZE` | `20` | 单轮派发批量 |

这些参数影响吞吐、恢复时间和重复执行窗口，只应由部署者调整。Provider Adapter 的 HTTP 重试与
`AGENT_MAX_RUN_ATTEMPTS` 是两个不同层级，不能相加后宣称 Exactly Once。

### 2.3 Parser、Embedding、RAG/Review Chat

| 领域 | 变量 | 当前选择/约束 |
|---|---|---|
| Parser | `AGENT_PARSER_BACKEND` | Fake 模式为 `fake`；Real 启动脚本固定选择 `docling` |
| Embedding | `AGENT_EMBEDDING_BACKEND` | `fake` 或 `openai_compatible` |
| Embedding Provider | `AGENT_EMBEDDING_BASE_URL/API_KEY/MODEL` | Real 默认智谱兼容端点与 `embedding-3` |
| Embedding 维度 | `AGENT_EMBEDDING_DIMENSIONS` | 必须为 `1024`，与 pgvector 列一致 |
| Chat | `AGENT_CHAT_BACKEND` | `fake` 或 `openai_compatible`，供 RAG 与 Review 使用 |
| Chat Provider | `AGENT_CHAT_BASE_URL/API_KEY/MODEL` | Real 默认 DeepSeek 兼容端点与 `deepseek-v4-flash` |
| 结构化输出 | `AGENT_CHAT_JSON_SCHEMA_SUPPORTED` | Provider 不支持 `json_schema` 时设为 `false`，仍执行本地 Schema 校验 |
| arXiv | `AGENT_ARXIV_BACKEND` | `fake` 或显式 `httpx` |

通用 Provider 调用参数：

- `AGENT_MODEL_TIMEOUT_SECONDS=60`；
- `AGENT_MODEL_MAX_RETRIES=2`。

它们影响单次 Adapter 调用，不会让结构化输出、Citation 或业务 Schema 非法自动变成可重试错误。

### 2.4 Chunk 与 RAG Profile

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `AGENT_CHUNK_MAX_TOKENS` | `512` | 单 Chunk token 上限 |
| `AGENT_CHUNK_OVERLAP_TOKENS` | `64` | 相邻 Chunk 重叠 |
| `AGENT_EMBEDDING_BATCH_SIZE` | `32` | 单次 Embedding 批量 |
| `AGENT_RETRIEVAL_TOP_K` | `20` | 语义/全文两路候选上限 |
| `AGENT_RETRIEVAL_PER_PAPER_LIMIT` | `8` | 最终结果中单篇论文上限 |
| `AGENT_RETRIEVAL_TOKEN_BUDGET` | `3000` | Evidence 上下文总预算 |
| `AGENT_ANSWER_MAX_OUTPUT_TOKENS` | `2048` | RAG 回答输出上限 |

Chunk 参数与 Parser/Embedding 身份共同参与 profile hash。修改后会产生新的 ParseRevision/ChunkSet，
不会原地重写既有成功索引。上述值是工程调优参数，不建议直接暴露给普通用户。

### 2.5 Research Agent 与 OpenSandbox

Research Agent Provider 与 RAG/Review Chat 独立：

- `AGENT_RESEARCH_RUNTIME_BACKEND=fake|deep_agents`；
- `AGENT_RESEARCH_MODEL_BASE_URL`；
- `AGENT_RESEARCH_MODEL_API_KEY`；
- `AGENT_RESEARCH_MODEL=deepseek-v4-flash`，当前固定且 thinking 关闭；
- `AGENT_RESEARCH_MODEL_MAX_OUTPUT_TOKENS=2048`。

Sandbox 部署配置：

- `AGENT_RESEARCH_SANDBOX_DOMAIN=127.0.0.1:8080`；
- `AGENT_RESEARCH_SANDBOX_PROTOCOL=http|https`；
- `AGENT_RESEARCH_SANDBOX_API_KEY`；
- `AGENT_RESEARCH_SANDBOX_IMAGE=agent-service/research-agent-sandbox@sha256:8ded4a3cfb5603efac3e297a09f79f4bdef798379728eeb96d563ae8f99f40d1`。

OpenSandbox Server 独立于 `scripts/dev.sh` 启动。项目版本化配置是
`config/opensandbox-server.phase6.toml`，入口是 `scripts/opensandbox-server.sh`；不会读取或修改用户 home
配置，也不能由 API 请求覆盖。运行前必须用环境变量提供 Server/Worker 共享的本地 API key。完整步骤见
[`本地 OpenSandbox Server`](runbooks/local-opensandbox-server.md)。当前配置固定 Server 0.2.2、loopback
控制面、Docker bridge、无 host volume、drop ALL、no-new-privileges、PID 256 和固定 execd/egress digest。
它提供本地 default-deny 行为证据，但未配置 secure runtime，不是公网多用户隔离方案。

固定镜像中的 TigerVNC 通过 PATH wrapper 强制 `-SecurityTypes None -localhost`。这只取消同一 Sandbox
namespace 内部 RFB 的二次密码，便于平台 noVNC gateway 连接；Web 仍必须使用 owner/Session/generation/
revision 绑定的短时 ticket，raw endpoint 与 5901 不返回给浏览器。真实画面回路只由开发者显式设置
`AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1` 启用，该变量不属于应用配置，也不得写入 `.env`。

Phase 6 Slice 7 的目标网络配置由 ADR-0012 固定为 `research-public-egress.v1`，不是 `.env` 或用户设置：
Sandbox 内允许任意正常公网 HTTP(S)，保留 CDP/MCP/VNC 所需的 Sandbox namespace 内部 loopback，并统一
拒绝非-loopback private/link-local/reserved/metadata/宿主/LAN 出口。raw Browser/execute 可访问同一
Sandbox 内部服务，但不能访问宿主 loopback；正式 URL/source 输入仍拒绝 localhost/loopback 及解析到
loopback 的 Host。
`PolicySnapshot` 与 `SandboxLease` 将保存 Profile version/hash，策略变化必须轮换 generation。该 Profile
当前代码已实现 Profile/Lease/Provider 映射和离线契约。固定镜像包含
`/usr/bin/wget`、Python、Node 和 Chromium，但不包含 `curl`；Slice 7 网络 Smoke 使用 `wget`，不为测试
增加镜像依赖或改变 digest。第二轮完整下载固定 2,215,244-byte arXiv PDF 超过 30 秒 Sandbox 命令限制，
Adapter 外层以 exit 124 结束；这不是网络拒绝证据。PDF 验收现改为同一 URL 最多 64 KiB 有界前缀的
HTTP 200/206、Content-Type、`%PDF` magic 和 SHA-256 检查。第三轮显式真实 Smoke 为 1 passed
（39.67s），实际确认同一 Sandbox 内部 loopback、`wget` arXiv 首页、上述 PDF 前缀、Python/Node/Chromium
访问 `example.com`、Playwright MCP `browser_navigate`、arXiv Search MCP `search_papers`，并确认 metadata
`169.254.169.254`、Docker gateway `:8080`、`10.0.0.1` 被拒绝。它不证明完整 PDF 下载、所有公网目标、
协议级只读或生产隔离。用户不能提交 Host allowlist、代理、DNS、例外地址或认证 Secret。

真实验证需由开发者显式设置 `AGENT_RUN_OPENSANDBOX_PUBLIC_EGRESS_TESTS=1`；普通测试保持离线。这个开关
只启用 Smoke，不改变应用运行时策略，也不得提交到 `.env`。

该 Profile 只强制 L3/L4/FQDN 目标边界，不解析 HTTP method、body、表单或站点业务语义。平台不会注册
外部写专用 Tool，也不提供平台凭据，产品策略要求 Agent 只做研究读取；但 raw Browser/Shell/MCP 仍可能
发出 POST 等写请求，当前配置不能宣称协议级只读。

## 3. 版本化 Review Profile

新建 Review Run 使用 `review-default.v3`，并把以下配置连同 Profile/Prompt/Workflow 版本保存到不可变
Run 快照和创建请求指纹：

| 字段 | v3 值 | 用途 |
|---|---:|---|
| `source_limit` | `3` | 最多选择的 arXiv 来源，便于低成本 Real 验证 |
| `minimum_ready_papers` | `1` | 允许继续构建 Matrix 的最少 READY 来源 |
| `full_text_token_threshold` | `12000` | 全文/检索式 Evidence 提取分界 |
| `retrieval_top_k_per_dimension` | `5` | 每个分析维度检索候选数 |
| `evidence_context_token_limit` | `16000` | 单篇 Evidence 提取上下文预算 |
| `section_output_token_limit` | `8000` | 单章节结构化输出预算 |
| `consistency_output_token_limit` | `8000` | 一致性报告结构化输出预算 |

历史 `review-default.v1` Run 继续使用已经持久化的 10/4000/2000 等原配置，`review-default.v2` Run
也保留其 3/8000/2000 快照；执行服务同时支持 v1/v2/v3，
不会迁移或静默改写历史 Run。当前 UI 只收集研究问题和 Outline 决策，不让用户提交上述原始参数。
如果后续需要不同规模，优先新增“快速/标准”等版本化 Profile，而不是开放任意数字。

## 4. Agent MCP Catalog 与用户选择

平台 Catalog 当前固定：

| Catalog | 版本 | 用户可填参数 | 作用 |
|---|---:|---|---|
| `playwright` | `0.0.79` | 无 | 操作同一 Session Sandbox 中的 Chromium |
| `arxiv-search` | `0.6.2` | 无 | 搜索论文元数据与读取摘要 |

Tool 名称、输入 Schema hash、MCP path、transport、进程配方、endpoint/header 和 Sandbox 端口由平台
维护。用户只能在自己的 Agent Session 中启用或停用 Catalog 条目，不能提交 URL、command、env、
Secret、包版本或认证信息。

Session MCP Profile 保存在 PostgreSQL，使用 revision 做并发控制。每个 Turn 的 `PolicySnapshot` 冻结
当时的 Catalog 版本、配置 hash、Tool 名和 Schema hash；以后调整 Session Profile 不会改写历史 Turn。

## 5. Agent Skill Catalog 与用户选择

平台当前提供：

| Skill | 版本 | 作用 | 所需平台 Tool |
|---|---:|---|---|
| `evidence-led-synthesis` | `1` | 使用 Project Chunk Index 与 Review Evidence Matrix 做证据优先综合 | `read_review_evidence_matrix`、`search_project_chunks` |

用户可以在首条产品消息前为 Session 选择平台 Skill；首轮开始后 Profile 锁定，更换研究方法需创建新
Session。平台也已支持 owner-scoped、只读声明式 Markdown/文本 Skill 的后端契约，但它不能包含可执行
文件、动态依赖或扩大 Tool/MCP/网络权限，当前 UI 重点仍是选择平台 Skill。

## 6. 当前用户可以配置什么

产品 UI/API 当前允许用户控制：

- Project 名称、描述、论文收录/移出/归档；
- RAG 使用整个 Project 或选中的 Paper；
- Review 研究问题、Outline feedback/edit/approve、取消和导出；
- Agent Session 标题；
- Session MCP Catalog 选择；
- 首 Turn 前的 Skill 选择；
- 每个 Agent Turn 绑定或沿用哪个可授权 Evidence Matrix；
- 每轮研究消息和取消当前 Turn。

Evidence Matrix 的 UI 选择最终绑定具体 `review_output_id`；Project Index 由当前授权的 READY ChunkSet
构造，用户不直接提交 ChunkSet、PaperVersion 或 SDK Workspace ID。

## 7. 平台固化且不向用户开放的边界

- owner/Project/Paper/Evidence/Artifact 权限校验；
- PostgreSQL 业务 Run、Attempt、Event、Message、Evidence、Artifact 的事实来源地位；
- AgentSession/SDK Thread、AgentTurnRun/SDK Execution 的稳定映射；
- 同一 Session 只有一个活动 Turn；
- Workflow 状态机、Prompt/Schema/Profile 版本和 Citation Validator；
- 外部模型、MCP、Browser、下载和 Sandbox 调用不得发生在数据库事务内；
- 取消后禁止开始新的模型或 Tool 调用；
- Tool/Artifact 的稳定 ID、唯一约束、内容 hash、条件更新和 reconcile；
- Event/日志不保存 Secret、完整 Prompt、网页正文、论文全文或大型 Tool 输出；
- 禁止宿主 Shell/Python、`LocalShellBackend`、Docker Socket 和宿主工作区挂载；
- Sandbox 镜像、资源/网络策略、MCP transport/endpoint/command/env 与 Tool Schema。

正常公网 Host 不作为逐项用户配置；平台固化的是 public-egress Profile 及 private/metadata/宿主/LAN
拒绝规则。Browser/MCP/execute 的 raw Workspace 下载不是正式业务资源，只有带出 Sandbox 成为 Artifact、
Project 资源或登记声明来源目标时才执行平台文件与目标分类检查。`source_url` 仅表示声明 URL/DNS 已检查，
不证明文件字节来自该 URL。

Deep Agents `permissions`、Skill 或 MCP 自身配置不能替代上述平台校验。

## 8. 修改配置时的判断规则

1. 只影响部署环境且不进入产品语义：修改 `.env`，重启 API/Worker；Secret 不写文档和 Git。
2. 会改变检索、生成、Workflow 或 Tool 语义：新增 Profile/Catalog/Prompt/Schema 版本并保存快照；不要在
   旧版本名下静默漂移。
3. 会改变数据库类型、权限、安全、费用或部署拓扑：先更新 ADR/阶段 Spec，再实现和迁移。
4. 只是希望用户更容易理解：优先提供只读运行配置摘要，不直接开放底层数字。
5. 普通自动测试继续使用 Fake；真实 Provider、OpenSandbox 或网络验证必须显式启用并记录实际范围。

## 9. 代码与文档入口

- 环境变量契约：`backend/src/literature_agent/infrastructure/config.py`
- 无密钥模板：`.env.example`
- 一键启动：`scripts/dev.sh`
- Review Profile：`backend/src/literature_agent/application/review_workflow_service.py`
- MCP Catalog：`backend/src/literature_agent/infrastructure/agent/mcp_catalog.py`
- Skill Catalog：`backend/src/literature_agent/infrastructure/agent/skill_catalog.py`
- Review 决策：`docs/learning-journal/decisions/0003-phase-3-fixed-review-workflow.md`
- Sandbox 决策：`docs/learning-journal/decisions/0007-use-opensandbox-executable-workspace.md`
- MCP/Skill 决策：`docs/learning-journal/decisions/0008-use-native-mcp-and-skills-capabilities.md`
