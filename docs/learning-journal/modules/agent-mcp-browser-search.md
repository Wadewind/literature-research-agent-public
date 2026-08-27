# Agent Playwright/Search MCP

## 模块解决的问题

Phase 5 Slice 7.3 把 7.2 的 MCP 配置/调用基础接到两个真实且固定版本的第三方 Server，同时保持
`ResearchAgentRuntime` Port 和业务数据模型不变：

- `@playwright/mcp==0.0.79` 连接 Session Sandbox 中已有的 Chromium/CDP；
- 无 extras 的 `arxiv-mcp-server==0.6.2` 作为只读 Search MCP 适配样本；
- 两个进程、文件、浏览器和 `execute` 共用当前 Session Sandbox 与 `/workspace`，不在 Worker 宿主启动；
- 用户仍只能选择平台 Catalog 条目，不能提交 URL、transport、command、env、版本、Secret、镜像或网络。

默认 Session MCP Profile 仍为空，因此“生产 Catalog 有能力”不等于每个 Agent 自动获得能力。

## 供应链与镜像

`sandbox/research-agent/Dockerfile` 使用 pinned
`node:24.19.0-trixie-slim@sha256:ab3eebe934147fee049b5eb83c570f68c849a13c930bdfa482de99fcdfa3b3de`
构建阶段安装 Playwright MCP；独立
`mcp-node/package-lock.json` 锁定 4 个 npm 包，其中 `playwright` 与 `playwright-core` 为
`1.63.0-alpha-2026-08-05`。`requirements-mcp.lock` 使用 hashes 固定
`arxiv-mcp-server==0.6.2` 及其传递依赖，生成目标为 Python 3.13、x86_64 manylinux 2.39；没有安装
`pdf`/`pro` extras。

镜像构建实际执行了 `npm ci --omit=dev --ignore-scripts` 和 `pip --require-hashes`。设置
`PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`，复用 pinned OpenSandbox Chrome 基础镜像的 Chromium；根
`backend/pyproject.toml` 与 `backend/uv.lock` 不变。运行时禁止动态安装包。

## 连接与执行流程

```text
PolicySnapshot.mcp_refs（平台固定 ID/version/config hash/tool schema hash）
  → Worker acquire 当前 Session Sandbox Lease/generation
  → Resolver 校验 owner/Project/Session/Turn 与 ACTIVE Lease
  → 镜像内固定 recipe 先以仅 loopback allowlist 启动 MCP Server
  → 端口监听后 get_endpoint，提取 opaque URL 的精确 authority
  → 同一 recipe 锁内一次性重启/收敛到 exact authority
  → Worker 以 endpoint + 私有 header 建立本轮 Streamable HTTP ClientSession
  → 分页遍历完整 tools/list
  → 校验 Catalog 允许子集存在且 Schema/hash 精确一致
  → 只把允许 Tool 转换为带前缀的 LangChain Tool
  → create_deep_agent 使用本轮 Tool
  → graph 结束先关闭 MCP session，再释放 Sandbox Backend 连接
```

Playwright 固定使用端口 8931、`http://127.0.0.1:9222` CDP 和
`/workspace/downloads`；arXiv 固定使用端口 8932 和 `/workspace/arxiv-mcp`。recipe 只接受平台注册的
服务名与经过语法校验的 authority，不接受命令、环境或端口配置。

Resolver 每次从当前 Lease 解析 endpoint/header，不全局缓存旧 generation 的 client。连接细节只存在于
infrastructure 内存，`ResolvedMcpConnection.connection` 不参与 repr；Domain、数据库、Event、Prompt、
公开 API 和日志均不保存 endpoint/header。

## Catalog 与能力投影

真实固定包分别发现 24 个 Playwright Tool 和 14 个 arXiv Tool。发现结果只是运行时验真输入，不自动
成为权限。平台审核并冻结：

- Playwright：17 个导航、快照、交互、标签页、等待、控制台和网络观察 Tool；
- arXiv：`search_papers`、`get_abstract`；
- 明确不暴露 `browser_evaluate`、`browser_run_code_unsafe`、`browser_file_upload`、`browser_drop`、
  `browser_close` 和 `browser_network_request` 等能力。

MCP Server 可以新增其他 Tool，但 Loader 会先遍历完整分页，再只转换 allowlist。允许 Tool 缺失、
Schema/hash 漂移、重复名称、游标循环或超过 32 页/256 Tool 时 fail-closed；未登记 Tool 不会进入
`create_deep_agent`。这避免了单页 discovery 和“Server 新增 Tool 即自动授权”两类绕过。

## Host、防重绑定与 generation

Playwright MCP 对未允许 Host 实际返回 403，而 OpenSandbox 只有在端口已监听后才能创建 endpoint。
本模块没有使用 `--allowed-hosts '*'`：recipe 先以仅 loopback allowlist bootstrap；Resolver 在端口监听后
取得 Server Proxy endpoint，并通过 OpenSandbox SDK 公共 API 额外解析同一 generation 的直连随机
authority。后者正是 Server Proxy 转发给 MCP 时使用的 `Host`，脚本锁只允许 bootstrap 一次收敛到该
exact authority。
最终 Server 只允许该 authority 和本地 loopback authority；相同 authority 重试幂等，之后 authority
改变则 fail-closed，不能继续默默复用旧进程。并发 resolver 最终也只能由脚本锁保留一个登记进程。

`opensandbox-server==0.2.2` 会丢弃外部 `Host`，并在 default-deny egress sidecar 下转发到宿主随机映射
authority；因此不能直接把 Server Proxy URL 的 authority 加入 allowlist。2026-08-28 的真实 Smoke 已
验证上述双 endpoint 解析，Playwright MCP 不再返回 403，且未用 wildcard 绕过。

## 事务、取消与副作用

endpoint 解析、Server 启动、MCP discovery 与实际调用均位于数据库事务外。MCP interceptor 继续复用
7.2 的 RuntimeExecution fence、业务取消、统一 Tool 预算、30 秒调用超时、8,000 字符输出上限和
`ToolExecution` invocation 账本；Runtime/MCP 成功不等于业务 Turn 或 Artifact 已提交。

Loader 在 resolver/session/discovery/conversion 边界检查取消；Tool 调用前后再检查业务 Run 与 execution
permit。Provider、recipe 与连接异常统一转换为不含原始 cause 的安全 temporary Runtime 错误。endpoint、
header、命令、Server log、Tool 参数和正文不进入 Event。

调用已产生副作用、但 `ToolExecution.succeed` 尚未提交的窗口仍没有通用 Exactly Once 解法；orphan
RUNNING 继续 fail-safe，不盲目重放。下载文件先留在 Workspace，只有通过既有 Snapshot/Artifact 校验与
业务事务后才成为正式产物。

## 重要测试与实际结果

- 固定 package discovery：Playwright 24 Tool、arXiv 14 Tool；Schema canonical hash 被审阅后写入 Catalog；
- Loader 离线测试包含多页 Server：允许 Tool 位于第二页仍可加载，第一页/其他页未登记 Tool 不进入结果；
- resolver/backend 离线测试覆盖 owner/Session/Project/Turn scope、版本/config hash、generation 不缓存、
  固定 service/port/authority、Provider/recipe 错误脱敏；
- recipe 状态机离线测试覆盖 bootstrap → exact 只重启一次、exact 幂等、authority 变化拒绝和并发
  configure 收敛；2026-08-28 重建的本地派生镜像 manifest list digest 为
  `sha256:b9961b04fe4d61c28c2ef0552c495c4ed249638fb9659de36d177d61b46e7366`；
- `--network none` 容器实际由 `/entrypoint` 启动 Chromium；4 路并发 bootstrap/configure 最终只保留一个
  Playwright MCP 进程，同 authority 重试成功，authority 变化按预期失败；
- 同一无网络容器中，Playwright MCP 通过 CDP navigate/click Sandbox 内合成页面，并把
  `paper.txt` 写入 `/workspace/downloads`；arXiv MCP 只验证启动、完整 discovery 和投影，不执行公网搜索；
- 2026-08-28 以本地 `opensandbox-server==0.2.2`、`execd:v1.0.21`、`egress:v1.1.4` 和 Python SDK
  `opensandbox==0.1.15` 运行 `AGENT_RUN_OPENSANDBOX_MCP_TESTS=1`，真实回路结果为
  `1 passed in 12.13s`；未调用模型或公网 arXiv。相关离线 Adapter/MCP/Runtime/Worker 回归为
  `52 passed in 2.18s`。
- PostgreSQL Application/API/Workspace Repository 相关回归为 `10 passed in 17.40s`，确认非空生产
  Catalog 没有改变 Profile owner/Session/CAS、逐 Turn Policy 冻结和 Workspace 持久边界。

## 代码入口

- Catalog：`backend/src/literature_agent/infrastructure/agent/mcp_catalog.py`
- Loader/interceptor：`backend/src/literature_agent/infrastructure/agent/mcp_tools.py`
- Lease connection resolver：`backend/src/literature_agent/infrastructure/agent/sandbox_mcp.py`
- OpenSandbox adapter：`backend/src/literature_agent/infrastructure/agent/opensandbox_backend.py`
- Worker 装配：`backend/src/literature_agent/worker.py`
- 镜像与 recipe：`sandbox/research-agent/Dockerfile`、`start-mcp-service`
- recipe 状态机测试：`backend/tests/infrastructure/test_mcp_service_recipe.py`
- 显式真实 Smoke：`backend/tests/infrastructure/test_opensandbox_mcp_smoke.py`

## 已知限制

- 已验证本地 Docker OpenSandbox proxy Host/header 和单 generation endpoint；未验证远程/Kubernetes
  Provider、跨主机 endpoint、secure runtime 或长时间稳定性；
- Sandbox 仍默认禁网，未验证真实 arXiv 查询、公共浏览、redirect/SSRF、统一 egress 或下载扫描；
- OpenSandbox 创建固定传入 `entrypoint=['/entrypoint']`，以保留 pinned Chrome 镜像的 Chromium/execd
  启动 recipe；该值是平台镜像契约，不允许用户配置；
- 用户不能配置任意开源 MCP，只能选择平台固定的两个 Catalog 条目；OAuth/Secret 委托不在 Phase 5；
- recipe/PID/authority 是同 Sandbox 的运行协调，不是抵御已控制该 Sandbox 的安全边界；真正边界仍是
  Session/Secret/宿主隔离、default-deny 网络、平台 Policy 和结果校验；
- graph 创建前的只读 resolver/session/discovery 仍可能因重复 Job 重复执行，但不缓存跨 generation 连接，
  Tool 副作用仍受 invocation 账本约束。
- 当前派生镜像和 Web UI 没有面向用户的 noVNC 画面、Browser 控制权状态或鉴权代理；用户不能操作
  Session Chromium，也没有 Cookie/Profile 跨 generation 恢复。ADR-0009 已将首版固定为两个 Turn
  之间、同一 generation 的人工控制，先以 Sandbox 内合成登录页验证；公共登录仍由 Phase 6 统一 egress
  和 URL 安全阻塞。
- `browser_file_upload` 继续不在 Catalog allowlist。用户附件和网页上传必须经过 ADR-0010 的业务
  Attachment ID 与后续平台包装 Tool，不能直接让模型选择 Workspace 路径。

## 60 秒面试说明

“我没有把任意 MCP 配置交给 Deep Agents。平台固定两个第三方 Server 的版本和镜像供应链，用户只选择
Catalog 条目；每轮从当前 Session Sandbox lease 解析私有 endpoint，完整分页发现能力，然后只校验和
转换审核 allowlist。Playwright MCP 连接同一个 Sandbox Chromium，下载进入同一 Workspace；arXiv 只暴露
两个只读 Tool。Server 新增 Tool 不会自动授权，Schema 漂移会 fail-closed。所有 MCP 调用继续经过平台的
owner scope、取消、fence、预算和 effect 账本。本地 OpenSandbox Proxy、同 Chromium 和下载回路已经
通过，但 secure runtime、公共网络与生产安全仍未验证，所以我把功能受限通过和安全结论严格分开。”
