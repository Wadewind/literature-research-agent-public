# Agent Browser 人工控制

## 模块解决的问题

Phase 5 已证明 Agent、Playwright MCP、`execute` 和文件工具可以共享一个 Session OpenSandbox，但用户还
看不到并操作同一个 Chromium。该模块实现最小产品闭环：一个 Agent Turn 结束后，用户临时接管当前
Session/generation 的浏览器，完成登录或页面操作，再结束控制并创建下一 Turn。它不是远程桌面平台，也
不支持运行中 Turn 的 interrupt。

## 边界与流程

```text
POST /api/v1/agent-sessions/{session_id}/browser-control
  → 短事务锁 owner-scoped AgentSession
  → 拒绝活动 Turn
  → 只读取既有 ACTIVE SandboxLease（不创建/续租/轮换）
  → 创建/重放 BrowserControlLease + 安全 Event
  → 返回 opaque ticket + 平台 view URL

noVNC 1.7.0
  → ticket 仅放 WebSocket subprotocol
  → 短事务认领唯一 viewer_connection_id
  → 事务外启动固定 websockify recipe（6080 → loopback 5901）
  → 解析 OpenSandbox Server Proxy ws/wss endpoint + headers
  → 有界 WebSocket ↔ WebSocket 二进制桥
  → 周期复核 owner/session/generation/fence/revision/TTL
  → 断线释放 viewer，但不自动结束业务控制权

DELETE /api/v1/agent-sessions/{session_id}/browser-control
  → 幂等结束 MANUAL
  → 下一 Turn 复用同 Session/Sandbox/Chromium
```

Domain/Application 只认识业务 ID、generation/fence 和安全投影，不依赖 OpenSandbox 或 noVNC 类型。raw
`sandbox_id`、上游 endpoint 和 headers 只在 Infrastructure Adapter 的局部内存中出现，Provider 解析与
上游 WebSocket connect 都发生在数据库事务外。

## 状态、数据模型与事务

`BrowserControlLease` 绑定 `owner_id/project_id/session_id/anchor_turn_run_id`，并记录物理 Sandbox 的
`generation/fencing_token`、模式 `MANUAL`、状态、单调 `revision`、最长 5 分钟 TTL、opaque ticket 的
SHA-256 digest、当前 viewer connection 和结束原因。业务 Lease 只记录 MANUAL；没有 ACTIVE 记录即表示
Agent/idle，不为普通 Agent 控制另建 AGENT Lease。

```text
ACTIVE ──user_completed────────────→ ENDED
   └────ttl/generation/key changed─→ EXPIRED
```

- PostgreSQL 部分唯一索引保证一个 Session 最多一个 ACTIVE 控制权；`session_id + revision` 和 digest 也
  唯一，check constraint 固定模式、状态、正 generation/fence/revision、TTL 和终态字段；
- Application 先锁 Session，再锁物理 Lease/控制权；活动 Turn 与 ACTIVE 控制权双向 fail closed；墙钟已
  过期但尚未 reconcile 的 ACTIVE 控制仍阻止新 Turn；
- 同一个签名 key 下重复开始确定性重放 ticket；API 进程重启导致 key 改变时，旧控制权先以
  `ticket_signing_key_changed` 失效，再创建新 revision，旧 ticket 不能重放；
- 画面认领通过行锁/CAS 保证单控制者。断线只清空 `viewer_connection_id`，允许刷新重连；结束和过期清空
  viewer 并使周期 fence 复核失败；
- 开始、结束或过期与小型 Event 在同一短事务提交；Event 只含 Session、generation、revision 和安全原因。

## 画面通道与安全

Web 不接收 `sandbox_id`、VNC/noVNC/CDP/MCP/OpenSandbox endpoint、Cookie 或 Secret。ticket 不放 URL，
不写 localStorage/sessionStorage，不打印日志，只驻留 React 内存并通过 `Sec-WebSocket-Protocol` 提交。
当前 HTTP 路由使用平台 actor；WebSocket 则从应用配置的 `dev_actor_id` 构造同一 actor，并结合 bearer
ticket 校验 owner、ticket digest、Session、current revision、TTL、generation/fence 和唯一 viewer。
因此当前只证明本地单用户的 owner 边界，不代表已经接入或验证通用认证上下文。

Sandbox recipe 只允许固定 websockify `6080` 转发 loopback TigerVNC `5901`，不接受用户端口或静态 Web
目录；Provider 只解析固定 `6080` 的 `http/https` Server Proxy URL 并转换为 `ws/wss`。桥接只接受二进制
帧，限制单帧 1 MiB、每方向 64 MiB、idle 60 秒、上游连接 10 秒和总时长不超过业务 Lease，并每秒复核 fence。VNC 帧、按键、页面正文和凭据不
被解析、记录或写入 Event。平台没有新增模型工具、网络目标或 egress 权限。

旧固定镜像诊断确认 `tigervnc-standalone-server 1.15.0+dfsg-2` 在 Sandbox 内 `5901` 返回 RFB，同时发现
OpenSandbox 暴露的旧 `5901` endpoint 映射到 HTTP egress sidecar，不能当作 raw TCP。修正后的固定镜像
将 websockify 0.13.0 与其 `requests`、`jwcrypto`、`redis` 等传递依赖统一锁入 Sandbox requirements，固定
recipe 在 `6080` 提供 WebSocket 转发。noVNC 1.7.0 仍仅是 Web 的精确直接依赖且无传递运行依赖；Vite
将其生成为独立懒加载 chunk。修正镜像的 Server Proxy/websockify/RFB 已由主审真实验证。

## 失败、重复、取消与恢复

- 没有物理 Lease、物理 Lease 到期/变脏、scope 或 generation/fence 不一致、活动 Turn、另一个 viewer、
  旧/过期 ticket 均直接拒绝；
- 画面掉线不销毁 Sandbox、不结束业务 Lease、不改变页面状态；用户可在 TTL 内重新请求 ticket 并连接；
- generation/fence 变化立即令 WebSocket 周期检查失败，查询/开始/结束时把业务控制权 reconcile 为
  `EXPIRED`；
- 本切片不强制中断 running Turn，因为 MANUAL 只能在 Turn 边界开始；取消仍由既有 Turn 机制负责；
- 不保存 Chrome Profile/Cookie 到 WorkspaceSnapshot，generation 变化后登录态不恢复。

## 测试与实际结果

2026-08-28 实际运行：

- `pytest -q tests/domain/test_browser_control.py tests/infrastructure/test_browser_gateway.py tests/infrastructure/test_opensandbox_backend.py tests/infrastructure/test_browser_proxy_recipe.py tests/api/test_agent_browser.py tests/infrastructure/test_opensandbox_browser_control_smoke.py`：41 passed、1 skipped；
- `pytest -q tests/application/test_browser_control_service.py tests/application/test_agent_session_service.py tests/integration/test_agent_migration.py tests/integration/test_agent_two_turn_flow.py`：20 passed；
- `ruff check src tests migrations/versions/a4c9e2f7b1d5_add_browser_control_leases.py`：通过；
- `pyright`：0 errors；
- `npm test -- AgentBrowserPanel.test.ts`：4 passed；
- `npm test`：21 files / 147 passed；
- `npm run build`：通过，noVNC 生成独立约 187 kB（gzip 约 57 kB）chunk；
- 完整后端非 integration 回归：1044 passed、6 skipped in 140.20s；
- `pytest -q tests/infrastructure/test_opensandbox_browser_control_smoke.py`：4 passed、1 skipped（默认未启用
  真实 OpenSandbox）；
- `AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1 pytest -q tests/infrastructure/test_opensandbox_browser_control_smoke.py`：
  5 passed in 13.06s；
- 旧镜像诊断：确认 Sandbox 内 TigerVNC/RFB 可用，同时复现 OpenSandbox 旧 `5901` endpoint raw TCP 超时；
  该失败推动通道改为 Server Proxy + websockify。后续两次分别暴露宿主代理继承与合成服务
  readiness/Fixture 转义，均已形成局部 client factory、固定轮询、安全日志与 compile 契约测试；第四次
  主审在重建镜像上完成整条 Smoke。

普通测试没有访问真实模型、公网或付费 Sandbox。仓库新增显式设置
`AGENT_RUN_OPENSANDBOX_BROWSER_TESTS=1` 才运行的本地 Smoke：它创建一个固定 Sandbox，经 Server
Proxy/websockify 核验 RFB 握手，并让 Playwright MCP 在同一 Sandbox 操作合成页面。RFB 前半程已真实
通过，同一 Sandbox Playwright 合成页后半程也已完成。该环境未配置 OpenSandbox API key/secure runtime，
所以只构成 trusted-local 功能证据；不证明 noVNC 人工键鼠 UI E2E、通用认证、浏览器登录跨 generation
延续或生产网络代理可用。

## 代码入口

- Domain：`domain/browser_control.py`
- Application：`application/browser_control_service.py`、`application/ports/browser_control_repository.py`
- PostgreSQL：`infrastructure/persistence/browser_control_repository.py`、迁移 `a4c9e2f7b1d5`
- Provider/桥：`infrastructure/agent/opensandbox_backend.py`、`infrastructure/agent/browser_gateway.py`
- API：`api/agent_browser.py`
- Web：`components/AgentBrowserPanel.tsx`

## 已知限制

- 本地开发身份仍来自 `dev_actor_id`；本模块没有建设公网认证、CSRF/Origin 策略或多实例共享 ticket key；
- API 进程重启会使旧 ticket 失效并在下次开始时轮换 revision；不是跨进程稳定会话票据；
- 连接异常若服务进程无法执行 `finally`，`viewer_connection_id` 最迟随最长 5 分钟业务 Lease 过期，当前无
  独立后台 sweeper；
- 不支持多人共享控制、view-only observer、同 Turn interrupt、浏览器文件上传、录屏或跨 generation 登录；
- 本模块完成时公网仍为 default-deny；当前 Phase 6 Slice 7 已由 ADR-0012 调整为版本化 public-egress、
  Sandbox namespace 内部 loopback 保留、非-loopback private/metadata/宿主/LAN 拒绝与正式资源校验。
  内部 loopback 是 websockify→VNC 与 CDP 所必需，不等于宿主 loopback 或正式 URL/source 获得授权。该
  后续决定不改写本模块已运行 Smoke 的历史范围，也不提供 HTTP/Browser 业务语义级只读保证。
- 当前本地 OpenSandbox 未配置 API key/secure runtime，真实 Smoke 仅是 trusted-local 功能证据。
- OpenSandbox Server 0.1.15 在客户端正常以 WebSocket code 1000 关闭后仍可能记录
  `Unexpected websocket proxy failure`/`ClientDisconnected`；这是当前 trusted-local 上游 noisy shutdown
  log，不能单凭该日志判定画面链路失败，后续依赖升级契约需复核。

## 60 秒面试说明

我没有把 VNC endpoint 直接交给浏览器，而是把人工控制建模成独立 PostgreSQL 业务 Lease。开始控制时平台
锁 Session，确认没有 Agent Turn，并把控制权绑定到当前 Sandbox generation/fence；反向路径也在创建
Turn 前拒绝 ACTIVE 人工控制。Web 只拿到 5 分钟以内的 opaque ticket，通过 WebSocket subprotocol 连接
平台代理。Adapter 在事务外启动 Sandbox 内固定 websockify，并经 OpenSandbox Server Proxy 建立上游
WebSocket，做有界双向转发并周期复核 fence；数据库和 Event 不记录画面、
按键、凭据或 raw endpoint。这样既复用了同一个 Chromium，又保持了平台业务状态、权限和 SDK/Provider
状态的分离。
