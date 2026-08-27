# ADR-0011：采用 Phase 6 精简交付范围

- 状态：已接受
- 日期：2026-08-28
- 决策者：项目维护者

## 背景

Phase 5 已验证 Project-scoped AgentSession/Turn、Deep Agents Runtime、OpenSandbox Workspace、固定 MCP、
Playwright Chromium 与原生 Skills 的集成边界。原 Phase 6 Spec 在此基础上规划了完整 Tool/MCP Registry、
OAuth/Credential、通用审批中心、开放资源类别、复杂预算和生产级运维强化，范围接近通用 Agent 安全产品，
超过个人简历项目验证核心架构与用户故事所需的交付规模。

另一方面，只验证默认禁网的合成页面无法完成“Agent 发现并下载真实论文资源”的核心研究故事。项目维护者
决定保留固定 arXiv 公网访问与下载，并为这一真实外部边界实现最低必要的 URL、SSRF、统一 egress 和文件
校验；其余安全能力按是否存在实际高风险功能决定，而不是为了完整名词列表预建平台。

## 决策

### 产品定位

Phase 6 交付一个适合本地、单人演示的受限 Research Workspace Agent，不建设公网多租户通用 Agent 平台。
Agent 继续只能绑定 Project，优先读取 Project Chunk Index 和显式选择的 Review Evidence Matrix，并可在
Session 专属 OpenSandbox 中处理数据、操作 Chromium、访问固定 arXiv 资源和提交正式 Artifact。

### 必须完成的四条能力主线

1. **文件交付**：区分 AgentAttachment、WorkspaceSnapshot、AgentArtifactCandidate 与 AgentArtifact；
   完成上传、`submit_artifact`、校验、预览和下载；
2. **浏览器交互**：展示当前 Session/generation 的 Chromium，并按 ADR-0009 在 Turn 边界交接人工控制；
3. **固定 arXiv 公网链路**：只允许平台固定的 arXiv 搜索、页面与 PDF 下载；实现 URL/DNS/IP/Redirect/
   SSRF 检查、覆盖 Sandbox 全部进程的统一 egress、下载大小/MIME/magic/hash 校验和来源记录；
4. **最小治理与可靠性**：复用 Phase 5 固定 Catalog/Profile，补齐调用前策略校验、ToolExecution 摘要、
   模型/Tool/时间/输出硬预算，以及 Sandbox 隔离、TTL、资源限制、generation/fence、清理和恢复。

### 首批公网范围

- 初始允许目标为精确主机 `arxiv.org` 和 `export.arxiv.org`，不接受通配子域名；
- 实现切片必须用真实 DNS、Redirect 和下载 Smoke 核对 arXiv 当前链路。若官方链路确实需要其他主机，
  先记录证据并更新版本化 allowlist，不能由模型、网页、用户输入或 MCP 动态扩大；
- 只允许 `https`；开发用 Sandbox 内合成页面可以显式使用隔离的 `http`；
- 统一 egress 必须覆盖 Chromium、Playwright MCP、arXiv MCP、Python、Shell 和 `curl` 等 Sandbox 全部
  进程。只检查 Tool 参数不构成网络隔离；
- DNS 结果、每次 Redirect 和最终连接目标都必须拒绝 loopback、private、link-local、multicast、
  unspecified、保留地址与云元数据地址；
- 下载先进入 `/workspace/downloads/` 的隔离临时区。只有受支持的 PDF 等文件通过大小、类型、文件头与
  哈希校验后，才能成为 Workspace 输入、Resource Manifest 条目或后续显式提交的 Artifact；
- 不绕过 robots、CAPTCHA、付费墙或授权，不提供任意互联网浏览，也不把“能访问 arXiv”表述为通用安全
  浏览器已经完成。

### 本阶段不建设完整 Approval 产品

精简交付只开放当前 Project 内只读检索、固定 arXiv 只读访问、Sandbox 离线计算和向当前 Project 提交受
支持类型的新 Artifact。这些能力采用固定策略自动执行，不逐次审批。

以下能力保持禁止，因此本阶段不实现通用 Approval/Interrupt/Resume UI：

- 使用平台托管凭据或把用户凭据交给 Agent；
- 对外发帖、提交表单、发送消息、上传文件或修改远程资源；
- 覆盖或删除既有正式 Artifact；
- 用户提供任意 MCP endpoint、Tool 代码、网络目标、Sandbox 镜像或动态依赖；
- 金融、购买、发布及其他不可逆操作。

未来若加入上述任一外部副作用，必须先恢复 Approval 风险矩阵、业务状态、单次 Token、恢复与审计切片，
不能把 Deep Agents 内部中断直接当作产品审批事实。

### 本阶段不建设完整 Registry 和生产运维平台

Phase 5 的版本化 MCP/Skill Catalog/Profile 作为事实来源继续使用。本阶段只补齐真实用户故事所需的固定
条目、Schema/hash 漂移拒绝、权限/预算/超时/输出拦截和脱敏执行摘要，不建设 Catalog 管理后台、任意
用户配置、OAuth/Credential 生命周期或通用 Tool Marketplace。

Sandbox 必须具备实际可验证的 Session 隔离、非 root 固定镜像、Secret/宿主隔离、TTL、CPU/内存/PID/
磁盘/文件/墙钟/输出限制、generation/fence、幂等销毁和最小清理补偿。多节点调度、预热池、自动扩缩容、
精确计费、SLA、生产备份与完整容灾不属于本阶段。

## 实施顺序

1. 精简产品契约与威胁模型；
2. Agent 输出 Artifact；
3. Browser 画面与跨 Turn 人工控制，仅合成页面；
4. Agent 输入附件；
5. 固定能力治理、Project Context 与硬预算；
6. Sandbox 资源、TTL、清理和统一 egress 强化；
7. 固定 arXiv 公网访问、下载、来源与 Prompt Injection 验证；
8. UI/E2E、故障验证、评测、运行文档和复盘；最终 UI 必须遵循
   `docs/spec/web-ui-app-shell-redesign.md`。若该重设计尚未完成，先按其 4 个前端切片分别完成 App Shell、
   PageBar、工作区空间回收和视觉 token 刷新，再整合 Phase 6 功能 UI。

每个切片必须保持普通 CI 离线。真实 OpenSandbox、真实模型和真实 arXiv 只在显式 Smoke 中运行，并记录
固定版本、配置、预算、实际结果与限制。

App Shell 重设计是纯前端契约，不改变本 ADR 的 API、数据库、Runtime 或安全边界；Phase 6 前置功能切片
不得依赖即将删除的旧全局 Header、`ProjectWorkspaceHeader` 或 `ProjectNav`。

## 后果

正面影响：Phase 6 有一条清晰、可演示的研究故事，同时保留真实网络和模型执行所需的关键安全边界；项目
可以展示业务事实、SDK Runtime、Sandbox、MCP、Browser 与 Artifact 的职责分离，而不被通用安全平台的
长尾功能拖住。

代价与风险：只允许固定 arXiv，不能宣称开放网络或生产安全；不具备通用审批，因此任何新的外部写操作都
必须继续拒绝；精简预算以硬上限为主，不提供精确费用核算；清理补偿和运行环境只满足本地演示证据，不是
高可用承诺。

## 验证门槛与非声明

- URL/IP/DNS/Redirect 单元测试和代理集成测试必须覆盖 IPv4/IPv6、编码变体、DNS rebinding、HTTPS
  降级和重定向到私网；
- 真实 arXiv Smoke 必须证明搜索或页面访问、PDF 下载、来源记录和文件校验，同时证明非 allowlist 目标被
  拒绝；
- Sandbox 测试必须证明跨 owner/Session/generation 隔离、资源上限、取消后不启动新操作和过期清理；
- Agent Artifact 测试必须证明重复 Job/Tool、响应丢失、取消和业务提交失败不会重复发布文件；
- 完成上述验证只表示本地受限 Research Agent 精简交付，不表示公网多租户、任意浏览、恶意文件扫描、
  通用审批、OAuth/Credential、SLA 或灾难恢复已经完成。
