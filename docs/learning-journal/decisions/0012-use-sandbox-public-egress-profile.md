# ADR-0012：采用 Sandbox 公网 egress Profile

- 状态：已接受
- 日期：2026-08-28
- 决策者：项目维护者
- 取代范围：仅取代 ADR-0011 的“固定 arXiv 精确主机 allowlist”网络范围；ADR-0011 的精简交付、
  Project scope、固定能力、硬预算、文件提交和非生产声明继续有效

## 背景

ADR-0011 原计划只允许 `arxiv.org` 与 `export.arxiv.org`，并由平台对每个 URL、DNS 结果和 Redirect
执行精确 Host allowlist。该边界适合收敛真实网络风险，但会把 Phase 6 Slice 7 变成一个 arXiv 专用
网络安全代理，并限制 Playwright、Search MCP、Shell/Python 等 Research Agent 已有能力对公开项目页、
代码仓库、开放数据集和补充材料的演示价值。

本项目是本地、单人、个人简历演示，不是公网多租户 Agent 服务。维护者决定让 Session 专属
OpenSandbox 承担主要执行隔离，并在 Sandbox 内允许访问任意正常公网；平台不建设通用 URL Host
allowlist、逐次网络审批或站点 Catalog。

Sandbox 隔离并不等于网络隔离：一个可出网的 Sandbox 仍可能访问宿主、LAN、容器网络、云元数据或内部
管理面。因此本决定放宽的是“公网 Host 范围”，不是取消 private-network 与 Secret/宿主边界。

## 决策

### 版本化公网 Profile

Phase 6 Slice 7 引入版本化 `research-public-egress.v1`：

- Sandbox 内的 Browser、Playwright/Search MCP、Shell、Python、Node 和 `curl` 可以访问任意正常公网
  `https`，并可在确有公开站点兼容需要时访问普通公网 `http`；
- 不维护 arXiv 或其他公网 Host allowlist，不因 URL 来自模型、网页、用户或 MCP 而逐次等待批准；
- Sandbox network namespace 内部 loopback 必须保留，用于 Chromium CDP、Playwright/Search MCP、
  websockify/VNC 和其他固定本地服务；覆盖 Sandbox 全部进程的基础设施 egress 必须在非-loopback 出口
  拒绝 RFC1918/private、link-local、unspecified、multicast、reserved、云元数据以及宿主/LAN/容器
  控制面目标，拒绝不能只依靠 Tool 参数过滤；
- Sandbox 继续使用非 root 固定镜像、空平台 Secret、无宿主目录/数据库/Docker Socket 挂载、固定资源
  与墙钟预算、短 TTL、generation/fence 和幂等清理；动态安装和用户自定义网络策略继续由产品策略禁止；
- 不绕过 robots、CAPTCHA、付费墙或授权，不把人工登录凭据交给模型或平台持久化。

`research-public-egress.v1` 是 L3/L4/FQDN 目标边界，不是 HTTP 应用代理。它不解析 method、body、表单或
站点业务语义，因而不能证明网络请求“只读”。平台不注册发帖、提交表单、发送消息、上传外站或修改远端
资源的专用 Tool，也不向 Sandbox 提供平台凭据；系统策略只允许研究读取。但 raw Browser、Shell、MCP、
Python 或 `curl` 技术上仍可能发送 POST、提交表单或触发站点写操作，这是当前精简交付的已知风险，而
不是基础设施层已强制拒绝的能力。

允许的是当前 Sandbox network namespace 自身的 loopback，不是宿主 loopback。raw `execute`/Browser
可以访问同一 Sandbox 内由固定镜像或平台 recipe 启动的服务，这是 Browser/MCP 集成所需且已知的隔离
边界；它们不能借此访问宿主的 `127.0.0.1`。另一方面，平台正式 URL/source 输入不继承该例外：URL
校验必须拒绝 `localhost`、loopback 字面地址以及 DNS 解析到 loopback 的 Host，不能把同 Sandbox
loopback 当作可登记的外部来源。

`PolicySnapshot` 必须冻结 public-egress Profile ID、version 与 canonical hash；`SandboxLease` 必须绑定
同一 Profile/version/hash。Profile 变化或快照与 Lease 不一致时必须轮换 Sandbox generation，不能在旧
deny、旧 allowlist 或不同网络策略的物理环境上续租。

### Workspace 文件与正式业务资源

Browser、MCP 或 `execute` 下载的文件可以留在当前 Session Sandbox Workspace，受 Workspace 文件数、
单文件、总量、TTL 与 Snapshot 规则约束。raw Workspace 文件不是 Project Paper、Evidence、已验证来源
或可下载业务 Artifact，不能通过公开 API 直接取回。

只有文件离开 Sandbox、成为正式 `AgentArtifact`、Project 资源或平台“已验证下载/来源”时，平台才执行
大小、数量/总量、超时、扩展名、MIME、magic、hash 和来源校验。外部网络与 Sandbox/Storage I/O 保持在
数据库事务外；稳定 invocation/resource ID、effect ledger、唯一约束和 fence 用于 Effectively Once
收敛，不宣称 Exactly Once。

### 实现和验证门槛

Slice 7 必须先核对本地锁定的 Python SDK `opensandbox==0.1.15`、OpenSandbox Server `0.2.2`、egress
image `v1.1.4`（上游 commit 前缀 `34653f7`）的实际行为和上游实现。配置对象存在、通配 Host 能保存或
文档声称支持都不构成通过；必须由同一 Sandbox 中的 Browser/MCP/Shell/Python/curl 真实连接证明公网
可达、Sandbox 内部 loopback 可用且非-loopback private/metadata/宿主目标不可达。若固定版本不能同时
做到“公网允许、非-loopback 私网拒绝”，Slice 7 必须停止并记录证据，不能退化为无 private-network
边界的开放网络。

已确认的上游事实是：egress `v1.1.4` 在 `dns+nft` 模式可用 `defaultAction=allow` 与 IP/CIDR deny set
表达公网允许和私网拒绝，且 deny set 位于动态 DNS allow 之前；其 nft 规则同时会在 deny set 之前固定
`accept` loopback interface。项目选择保留这一行为，因为 CDP `127.0.0.1:9222`、VNC
`127.0.0.1:5901` 和 Sandbox 内 MCP/合成服务依赖同一 namespace loopback。备选方案是自研 egress 镜像
并重构这些本地通道，但会扩大镜像供应链、网络拓扑和验证范围，不符合个人项目的精简交付，因此拒绝。

普通自动测试继续完全离线，使用恶意 URL/DNS/Redirect/文件 Fixture 与 Fake Provider；真实公网 Smoke
必须显式启用，记录固定版本、Profile/hash、目标、预算、结果和限制，不调用真实付费模型。至少验证一个
arXiv 页面/PDF、一个非 arXiv 正常公网目标、Sandbox 内部 loopback 可用，以及一个被拒绝的非-loopback
private/metadata/宿主目标。正式 URL/source 的离线 Fixture 还必须证明 localhost/loopback 输入及解析
结果被平台拒绝。

### 仍然不属于 Phase 6

- 公网多租户、生产级零信任 Sandbox、组织 RBAC、SLA 或灾难恢复；
- 用户自定义 MCP endpoint、网络 Profile、Sandbox 镜像、代理、DNS 或认证 Secret；
- OAuth/Credential Vault、长期 Cookie/Profile 恢复或平台托管登录；
- 面向对外发帖、提交表单、上传文件、修改远端资源、购买或发布的产品 Tool/Workflow；当前策略要求不
  执行这些动作，但 raw 公网通道不提供协议级只读保证；
- 动态 `pip/npm/apt` 安装、通用 Coding Agent、多 Agent 或长期 Memory；
- 能解析 HTTP/Browser 业务动作并强制只读的通用 egress proxy、逐次网络 Approval Center、恶意文件
  扫描产品或所有互联网风险的防护声明。

Slice 8 的 UI/E2E 仍必须遵循 `docs/spec/web-ui-app-shell-redesign.md`；本 ADR 不把任何 UI 工作提前到
Slice 7。

## 被取代与保留的历史

- ADR-0011 的“首批公网范围”“固定 arXiv 公网链路”及对应非 allowlist 拒绝门槛由本 ADR 取代；
- Phase 5 与 Phase 6 Slice 6 的 default-deny 结论是当时已运行验证的历史事实，继续保留；
- Phase 3 固定 arXiv Workflow 的 Host allowlist 属于独立 Core Workflow，不受本 ADR 影响；
- Tool/MCP/Skill Catalog allowlist、Project/owner scope、Artifact 校验、硬预算和取消/fence 不变。

## 后果

正面影响：Research Agent 可以在隔离 Workspace 中直接使用 Deep Agents、Browser、MCP 与 `execute` 研究
任意公开资料，减少平台重复建设 Agent Harness 和站点 Catalog，更符合个人简历项目展示重点。

代价与风险：Prompt Injection、数据外传、意外外部写、站点条款、恶意响应和公网可用性风险显著高于
固定 arXiv allowlist；Sandbox compromise 的网络后果只能被 private-network deny、Secret/宿主隔离与
资源上限限制，不能被消除。项目只能宣称“trusted-local 演示中的受限公网能力”，不能宣称协议级只读、
生产级安全浏览器或公网多租户隔离。未来若必须强制外部写拒绝，需要能检查 HTTP/Browser 动作的统一
egress proxy，或在受控 Tool 层引入正式 Approval，不能继续依赖当前目标策略。
