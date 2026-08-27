# ADR-0009：采用跨 Turn 的人工浏览器控制

- 状态：已接受
- 日期：2026-08-28
- 决策者：项目维护者

## 背景

Phase 5 已验证固定 Playwright MCP 可以连接 AgentSession 专属 OpenSandbox 中的同一个 Chromium，操作
Sandbox 内合成页面并把下载写入 `/workspace`。当前产品 UI 尚未展示 Chromium 画面，也没有把 VNC、
noVNC 或 CDP endpoint 暴露给用户。

研究过程中经常需要由用户本人完成站点登录、验证码或账号选择，再让 Agent 继续只读研究。让平台接收并
保存账号、密码、Cookie 或验证码会扩大 Secret、认证委托和跨 generation 恢复范围；而可信本地个人项目
可以先让用户直接操作 Session Sandbox 中已有的 Chromium。用户操作完成后，Playwright MCP 下一次读取
DOM/页面快照即可看到新状态，不需要平台复制浏览器自动化协议。

## 决策

### 首版交互采用 Turn 边界交接

首版不在一个运行中的 AgentTurnRun 内引入 LangGraph Interrupt。固定流程为：

```text
Turn N：Agent 导航到需要人工操作的页面并结束回答
  → 用户在同一 AgentSession / Sandbox generation 的交互式浏览器视图中操作
  → 用户点击“完成操作”，结束人工控制
  → 用户发送“已完成，请继续”
  → Turn N+1 复用同一 SDK Thread、Sandbox Lease 和 Chromium
  → Agent 调用 browser_snapshot 读取操作后的页面并继续
```

人工操作不是 Agent Tool 调用，不记录每次点击、按键、页面正文或输入内容。平台只记录小型、脱敏的
`browser_control_started`、`browser_control_ended`、过期和 generation 变化事实。

### 单控制者与范围

- 新增 Session/generation 范围的 `BrowserControlLease` 业务状态，至少绑定 owner、Project、Session、
  Sandbox generation、模式、revision 和短期限；它不替代物理 `SandboxLease`；
- 模式只允许 `AGENT` 或 `MANUAL`。首版只有在 Session 没有活动 Turn 时才能进入 `MANUAL`，从而避免人和
  Agent 同时点击同一页面；人工控制结束或过期后才允许创建下一 Turn；
- 进入 `MANUAL` 必须绑定已经存在、健康且 generation 一致的 SandboxLease；首版不因打开画面静默创建或
  轮换 Sandbox。BrowserControlLease 的期限不得超过物理 Lease，画面心跳只能按平台上限受控续租；
- API 只接受业务 Session ID。浏览器视图通过平台鉴权代理或短时、单 Session 的受控票据访问，不能返回
  原始 OpenSandbox、VNC、noVNC、CDP 或 MCP endpoint；
- 浏览器控制权、画面和输入只属于当前 owner/Session/generation，generation 轮换立即使旧视图失效；
- 用户凭据直接输入 Sandbox Chromium。平台不提供密码/Cookie 字段，不把输入写入 PostgreSQL、Event、
  WorkspaceSnapshot、Artifact、Prompt 或日志。

### 登录状态边界

登录状态只在当前物理 Chromium/Sandbox generation 的生命周期内按 best effort 保留。首版不把 Chrome
Profile、Cookie 或 Local Storage 纳入 `/workspace` 快照，也不跨 generation 自动恢复登录。Lease 过期、
环境污染、取消后轮换或 Provider 丢失时，用户可能需要重新登录。

人工控制不绕过网络策略。当前 default-deny Sandbox 只能用于本地合成页面验收；访问真实固定站点必须先
完成 Phase 6 的统一 egress、URL/DNS/Redirect/SSRF 和域名策略。平台不自动绕过 CAPTCHA、付费墙、站点
限制，也不允许 Agent 借人工登录执行未审批的对外写操作。

## 实施顺序

1. **离线契约**：BrowserControlLease 状态机、owner/Session/generation 校验、单控制者、过期和 Event；
2. **画面通道**：核对 pinned OpenSandbox Chrome 镜像现有 VNC 能力，再决定并锁定 noVNC/websockify 或
   等价组件；通过平台代理建立短时视图，不暴露原始 endpoint；
3. **Agent UI**：在 Research Agent 右侧面板加入“打开浏览器/接管/完成操作”，并明确当前是人工还是
   Agent 控制；断线、刷新、过期和 generation 变化可恢复到稳定 UI 状态；
4. **本地真实验收**：只用 Sandbox 内合成登录页验证用户输入后下一 Turn 的 `browser_snapshot` 能识别；
5. **公共站点验收**：仅在统一 egress 和固定域名策略完成后，使用专用测试账号显式运行，不进入普通 CI。

第二阶段若确有“同一 Turn 等待用户”的必要，再单独引入 `WAITING_INPUT + BrowserTakeoverRequest +
LangGraph interrupt/resume`；它不是首版前置条件。

## 后果

正面影响：最大程度复用同一个 Chromium、Playwright MCP、Session Sandbox 和现有多轮 Thread；不需要平台
保存用户密码或重写 Browser Tool；实现与个人简历项目的本地演示定位相称。

代价与风险：需要新增浏览器画面代理、短时访问控制和控制权状态；人工操作无法像 ToolExecution 一样逐步
重放；当前 generation 丢失会丢失登录状态；公共登录仍依赖尚未完成的网络安全切片。

## 被否决的方案

- **首版在运行中 Turn 内直接与用户抢占同一浏览器**：会产生点击、导航和页面状态竞争；
- **把账号、密码或 Cookie 作为 Session 配置保存**：扩大 Secret 托管和跨租户泄漏风险；
- **把 Chrome Profile 放进 WorkspaceSnapshot**：会把认证凭据混入内部研究文件快照；
- **向 Web 直接返回 noVNC/VNC/CDP endpoint**：绕过平台 owner/Session/generation 授权；
- **首版强制使用 LangGraph Interrupt**：对“两个 Turn 之间用户操作”没有必要，并增加恢复状态。

## 验证门槛与非声明

- 离线测试必须覆盖跨 owner/Session/generation 拒绝、活动 Turn 拒绝、重复开始/结束、过期和旧票据失效；
- 浏览器测试必须证明 Agent 与用户看到并操作的是当前 Lease 的同一个 Chromium；
- Event、日志和数据库不得保存页面正文、凭据、Cookie、按键或原始连接 endpoint；
- 新增镜像或前端依赖前必须报告精确版本、传递依赖、镜像层与锁文件影响；
- 完成合成页面验收不代表公共登录、认证委托、Cookie 持久化或生产 noVNC 安全已经通过。
