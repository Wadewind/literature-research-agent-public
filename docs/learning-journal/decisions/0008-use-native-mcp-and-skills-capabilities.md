# ADR-0008：复用 Deep Agents 原生 MCP 与 Skills 能力

- 状态：已接受
- 日期：2026-08-27
- 决策者：项目维护者

## 背景

ADR-0007 已选择 OpenSandbox 作为 Session 专属可执行 Workspace，并把 Browser、MCP 与 Skill 留给
Phase 5 Slice 7.2–7.4 验证。原计划分别开发自定义 `browser_*` Tool、固定
`search_arxiv_metadata` MCP Server 和仅由平台维护的 Research Skill。

本地锁定的 `deepagents==0.7.8` 已通过 `create_deep_agent(tools=..., skills=..., backend=...)`
提供 Tool、Skill 与 Sandbox Backend 组合能力，但没有直接接收 MCP Server 配置的参数。LangChain
官方方式是使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient` 把 MCP Tool 转换成 LangChain
Tool，再传给 `create_deep_agent`。Playwright MCP 官方实现可以通过 CDP endpoint 连接既有 Chromium，
因此没有必要在平台内重新实现浏览器操作协议。

项目目标是展示对成熟 Agent Harness 的可靠业务包装，而不是自研浏览器 Tool 或 MCP Server。与此同时，
“SDK 能加载用户配置”不等于平台可以安全执行任意 URL、命令或包；owner/Session 隔离、供应链、Secret、
SSRF、预算、取消和审计仍属于平台责任。

## 决策

### MCP 接入方式

- 平台引入 SDK-neutral 的 MCP Catalog/Profile 业务配置；用户可以在自己的 AgentSession 中选择、启用、
  禁用并填写 Catalog 明确声明的非敏感安全参数；每轮把条目 ID、版本、配置哈希、Tool 名称/Schema 哈希
  固化到 `PolicySnapshot` 或其版本化引用；
- Catalog 条目由平台安装、审核并固定实现版本、transport、运行位置和允许 Tool。用户不能提交任意 MCP
  URL/endpoint、transport、stdio command、env、包版本、认证信息、Sandbox 镜像或网络配置；
- Worker Adapter 使用 `langchain-mcp-adapters` 建立每次 Runtime execution 可关闭的 client/session，
  加载 Tool 后校验 Catalog 名称、Schema 与哈希，再把经过平台 interceptor 包装的 LangChain Tool 传给
  `create_deep_agent`；Deep Agents SDK 类型不进入 Domain、公开 API 或业务数据库枚举；
- interceptor 在实际调用处再次校验 owner/Project/Session/Turn、不可变策略、取消、Runtime fence、预算、
  超时、输出大小、错误分类和安全 Event；MCP 自带 Tool 描述或权限配置不能替代这些检查；
- 普通测试使用进程内或本地确定性 Fake MCP，不访问实时网站、外部 MCP 或付费服务。

### Sandbox 内 MCP 与 Browser

- 需要 stdio 或本地进程的第三方 MCP 必须预装并运行在当前 Session 专属 OpenSandbox 中，不能作为 ARQ
  Worker 宿主子进程运行；其文件、bash/Python 与下载均只作用于同一 Sandbox `/workspace`；
- 派生镜像预装精确版本的 Playwright MCP 和 Chromium。Playwright MCP 在 Sandbox 内连接本地 Chromium
  CDP，并以固定 Streamable HTTP 端口提供 MCP；Worker 只通过 OpenSandbox 的 opaque endpoint 连接，
  endpoint 不进入 Prompt、公开 API、业务 Event 或用户配置；
- Phase 5 Browser Spike 只需证明 `Deep Agent → MCP client → Playwright MCP → 同 Sandbox Chromium`
  可以操作本地合成页面、返回有界结果，并把下载写入同一 `/workspace`；不再开发自定义 `browser_*`
  LangChain Tool；
- 真实公共网站浏览、URL/DNS/Redirect/SSRF 策略、统一 egress allowlist、登录凭据、下载扫描和面向用户的
  noVNC 鉴权不作为该 Spike 的通过条件，留到 Phase 6。普通路径继续默认禁网；任何无统一 egress 的
  公网 Smoke 都必须显式标记为仅本地、不安全、非生产验证。

### 现有 Search MCP

- Phase 5 不自行开发 `search_arxiv_metadata` MCP Server；选择一个平台审核并固定版本的现有只读 Search
  MCP 作为第二个适配样本；
- 如果该 Server 是 stdio/本地进程，则预装并运行在 OpenSandbox；如果是远程 Streamable HTTP，则由
  Worker MCP client 连接外部服务，并显式记录其外部网络、可用性、隐私和费用边界；
- Phase 5 只验证 Catalog 选择、Tool 转换、命名空间、Schema/hash、会话隔离、interceptor 和确定性
  Fake；具体 Search MCP 与版本在新增依赖或镜像内容前单独报告并确认。

### Skills 接入方式

- 使用 Deep Agents 原生 `skills` 加载机制，不在平台重写 Skill Harness。本地 `0.7.8` 的
  `SkillsMiddleware` 只通过 Backend API 读取虚拟路径，因此 `CompositeBackend` 将 `/skills/` 路由到
  平台管理的只读 Backend，不暴露给 Sandbox `execute`；`/workspace` 仍由 OpenSandbox Backend 管理；
- Skill 内容按 owner/Session 隔离，并在每轮固化 Skill ID、版本、内容哈希和所需能力；
- 用户可以启用/禁用平台安装、固定版本的 Skill，也可以在首版创建 owner-scoped 的声明式 Markdown/
  文本 Skill；首版不接受可执行脚本、二进制、动态依赖、任意路径挂载或由 Skill 携带的 Secret；
- Skill 只能指导模型使用当前 PolicySnapshot 已允许的 Tool/MCP/Sandbox 能力，不能授予网络、权限、
  预算、Secret 或跨 Project/Session 访问。

### Slice 7 新顺序

剩余能力验证按三个独立、可回退切片推进：

1. **7.2 MCP Configuration Foundation**：MCP Catalog/Profile、owner/Session 隔离、版本快照、client
   生命周期、Tool 命名空间、Schema/hash、interceptor、预算/取消/输出限制和 Fake MCP；
2. **7.3 Playwright MCP 与 Search MCP Spike**：在同一 OpenSandbox 中连接 Playwright MCP 与 Chromium，
   验证本地页面/下载；再适配一个现有、固定版本的只读 Search MCP。该切片不自研 MCP Server；
3. **7.4 Native Skills**：验证平台安装 Skill 与 owner-scoped 声明式 Skill 的只读物化、版本/哈希、
   Session 隔离和权限不扩张。

上述顺序取代 ADR-0007 中“7.2 自定义 Browser/下载 → 7.3 固定自研 MCP → 7.4 仅平台 Skill”的部分，
不改变 ADR-0007 的 OpenSandbox Lease、WorkspaceSnapshot、宿主隔离、固定镜像和 `execute` 决策。

## 后果

正面影响：最大程度复用 Deep Agents、LangChain MCP Adapter 与 Playwright MCP；平台代码聚焦业务授权、
配置隔离、可靠性和审计；Browser、bash/Python、文件和本地 MCP 共用 Session Sandbox，减少双重 Workspace
与自定义浏览器协议。

代价与风险：需要新增并锁定 `langchain-mcp-adapters`，派生镜像需要固定 Playwright/Search MCP 的包与
传递依赖；MCP client、Server 与 Sandbox endpoint 增加生命周期和故障面；owner-authored Skill 仍可能
包含 Prompt Injection，因此必须是只读声明式内容且不能获得新权限。延后统一 egress 意味着 Phase 5
不能宣称公共浏览、下载或开放网络已达到生产安全。

## 被否决的方案

- **平台自研 `browser_*` Tool 和浏览器协议**：会重复 Playwright MCP 已有能力并增加维护面；
- **平台自研首个 arXiv MCP Server**：不能验证接入第三方 MCP 的真实价值，且偏离本阶段集成目标；
- **把用户提供的 stdio command 运行在 Worker 宿主**：第三方进程可接触 Worker 文件、环境和 Secret；
- **允许用户直接提交远程 MCP URL 或认证信息**：引入 SSRF、凭据存储、供应链和租户隔离问题，超过
  Phase 5 范围；
- **只允许平台代码仓库中的固定 Skill**：安全面较小，但不能验证 Deep Agents 原生 Skill 的用户隔离与
  配置价值；首版改为允许受限的 owner-scoped 声明式 Skill。

## 验证门槛与非声明

- 7.2 必须证明不同 owner/Session 的 MCP Profile、client、Tool 与调用记录不混用；重复 Job、取消和输出
  超限稳定失败；普通测试完全离线；
- 7.3 必须证明 Playwright MCP 操作的是当前 Lease 内同一个 Chromium 与 `/workspace`，endpoint 不泄漏，
  Sandbox 丢失或轮换时 client 不复用旧连接；现有 Search MCP 的版本与运行位置有明确记录；
- 7.4 必须证明 Skill 内容按 owner/Session/version/hash 隔离、只读 Backend 不可由 Sandbox `execute`
  改写，且不能扩大 Tool/MCP/网络权限；
- 新增 `langchain-mcp-adapters`、Playwright MCP 或 Search MCP 前，必须先报告候选精确版本、传递依赖和
  Python/镜像锁文件影响；不得顺便升级 Deep Agents 或无关依赖；
- 未完成 Phase 6 网络与下载安全前，不宣称真实公共浏览、统一 egress、下载安全或任意用户 MCP/Skill
  配置达到生产可用。

## 参考资料

- [Deep Agents Tools](https://docs.langchain.com/oss/python/deepagents/tools)
- [Deep Agents Customization](https://docs.langchain.com/oss/python/deepagents/customization)
- [LangChain MCP Adapter](https://github.com/langchain-ai/langchain-mcp-adapters)
- [Playwright MCP](https://github.com/microsoft/playwright-mcp)
