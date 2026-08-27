# Agent MCP Configuration Foundation

## 模块解决的问题

Phase 5 Slice 7.2 让绑定 Project 的 AgentSession 可以选择平台注册、固定版本的 MCP 能力，同时不允许用户
提交 MCP URL、transport、command、env、Secret、Sandbox 镜像或网络配置。它把业务配置、逐 Turn 授权
和 SDK client 生命周期分开：PostgreSQL 保存产品事实，`langchain-mcp-adapters` 只存在于 infrastructure。

本模块最初在 7.2 验证配置与调用基础，当时生产 Catalog 为空。7.3 已在不改变 Domain/Profile/Policy
契约的前提下安装固定 Playwright/arXiv MCP、填充生产 Catalog，并以当前 Sandbox Lease 解析连接；
真实能力和限制见 `agent-mcp-browser-search.md`。

## 边界和执行流程

```text
GET Catalog（平台静态、SDK-neutral）
  → 用户在自己的 AgentSession PUT Catalog ID + exact version + safe parameters
  → 短事务锁 AgentSession，owner scope + expected revision/CAS 保存 Profile
  → 用户提交下一条 Message
  → 同一短事务读取 Profile，解析精确 Catalog 版本
  → PolicySnapshot 冻结 catalog/version/config hash/tool/schema hash
  → Worker 在数据库事务外 acquire Session Sandbox
  → execute/resume 打开显式 MCP ClientSession
  → 分页遍历完整 list_tools，校验允许子集的 name/schema hash
  → 只转换允许 Tool（tool_name_prefix=True + platform interceptor）
  → create_deep_agent 使用本轮 Tool；结束或异常先关闭 MCP，再关闭 Sandbox
```

`reconcile_turn`、`collect_turn_result` 和无在线执行的 `cancel_turn` 只读取 Runtime control/checkpoint，不
解析连接或打开 MCP session。`ResearchAgentRuntime` 五方法 Port 没有改变，也没有 LangChain/MCP SDK 类型。

## 状态、数据模型和事务

- `McpCatalogEntry`：Catalog ID、精确版本、展示名、允许用户填写的字符串参数声明、原始 Tool 名与输入
  Schema SHA-256。静态 Registry 才拥有 transport/endpoint 等连接细节，公开 Catalog 不拥有这些字段。
- `McpProfile`：`profile_id`、owner、Session、revision、selections、整体 config hash 和时间戳；更新不会
  覆盖旧行，而是为同一 profile ID 新增 revision。数据库以 `(profile_id, revision)` 为复合主键、
  `(session_id, revision)` 唯一，Repository 查询同时验证 Profile owner 与 AgentSession owner。
- `McpProfileSelection`：Catalog ID、版本与已声明的非敏感参数；canonical JSON 产生稳定 config hash。
- `PolicySnapshot.mcp_refs`：profile ID/revision、Catalog ID、版本、selection config hash、prefixed Tool 名、输入 Schema hash；
  不含参数原文、连接配置、Secret 或 SDK 对象。Profile 后续更新不改变既有 Turn。
- `ToolExecution`：Project Tool 继续使用既有 `turn_run_id + tool_name + canonical arguments hash`；MCP
  Tool 则把 LangGraph `tool_call_id` 作为逻辑 invocation，`effect_id` 哈希 `turn_run_id + invocation_id`，
  `args_hash` 哈希 `invocation_id + canonical arguments`。数据库只见 opaque hash，不保存原始调用 ID 或
  参数；同一 ID 改 Tool/参数永久 fail-closed，不同 ID 即使参数相同也各占一次预算并各执行一次。

Profile 更新和 Turn/Policy 创建分别使用既有短事务。MCP `list_tools` 与实际 Tool 调用都在数据库事务外。
interceptor 的 `begin` 在一个短事务中认领 effect，外部调用完成后 `succeed` 在另一个短事务中条件提交；
因此 Runtime/MCP 成功不等于业务 Turn 或 ToolExecution 已成功提交。

## 关键决定与替代方案

- 精确依赖固定为 `langchain-mcp-adapters==0.3.2`。锁文件新增 `mcp==1.29.1`、
  `httpx-sse==0.4.3`、`sse-starlette==3.4.8`；既有 Deep Agents、LangChain Core、LangGraph 未升级。
- 使用 `MultiServerMCPClient(..., tool_name_prefix=True)` 与显式 `client.session(server_name)`，随后在同一
  session 上以公开 converter 只转换允许 Tool。没有采用为每次加载创建短暂 session 的 `get_tools()`，因为 stateful
  MCP 必须让 initialize 后的 session 贯穿本轮 Runtime execution。
- Tool 名前缀固定为 `{catalog_id}_{raw_tool_name}`，不同 Server 的同名 Tool 不冲突；加载前分页遍历
  `list_tools()` 完整发现结果，允许 Tool 必须存在且 canonical Schema hash 精确相等。Server 额外 Tool
  可以存在，但不会被转换或传给 Agent；重复名称、分页循环、允许 Tool 缺失或漂移均拒绝。
- 每个 Profile revision 中 `catalog_id` 唯一，不能同时选择同一 Server 的两个版本；Catalog 构造时也
  要求 prefixed Tool 名不超过业务账本的 100 字符上限，避免配置成功后才在执行阶段失败。
- 没有把 Deep Agents `permissions` 当成 MCP 权限。平台 interceptor 与 Runtime 原有 Tool middleware
  分别在 MCP 调用和最终 Tool 执行边界复核不可变 Policy、Turn scope、Runtime permit、取消和预算。
- 没有在 7.2 自研 MCP Server，也没有把第三方 MCP 进程启动在 Worker 宿主。进程内 FastMCP 只作为普通
  自动测试 fixture；7.3 才决定具体 Playwright/Search Server 版本、镜像与连接实现。

## 失败、重试、重复和取消

- 未知 Catalog/版本/参数、缺少必填参数、并发 revision 冲突或跨 owner Session 均在平台侧拒绝。
- Runtime 按冻结引用精确解析；缺少 Resolver、Server 名不匹配、Tool 集合或 Schema 漂移时永久
  fail-closed，并关闭已打开的 MCP sessions。resolver/session/list/load/close 的普通 SDK 异常统一收敛为
  不带底层 cause 的安全 temporary 错误，不能把 endpoint 或 Secret 带过 Runtime Port。
- Loader 在 resolver、session、`list_tools` 与 load 的每个外部边界前重新检查业务取消；interceptor 在
  远端调用前检查 Runtime fence、业务 Run=RUNNING、未请求取消、逻辑 `tool_call_id` 和 Tool allowlist；
  取消、缺调用 ID 或预算耗尽时 handler 调用数为零。
- 每次调用限制 30 秒，结构化结果沿用既有 8,000 字符上限；超时/断连为安全 temporary 错误，Schema/
  allowlist/输出超限为 permanent，取消保持 cancelled。
- 相同成功 effect 返回持久有界结果，不再次调用 MCP；相同 RUNNING effect fail-safe 返回 temporary，
  不盲目重放；temporary FAILED 可用同 effect 增加 attempt 后重试，永久/取消失败不可自动重试。
- `succeed/fail` 终态前再次复核 RuntimeExecution fence。若 handler 已执行但旧 Worker 此时丢失 lease，旧
  owner 不得写成功或失败，保留 RUNNING 供对账；同一有效 fence 下的超时/异常才写入安全失败。
- MCP 已产生外部副作用、但 `ToolExecution.succeed` 尚未提交的崩溃窗口没有通用对账协议。本模块只实现
  Effectively Once 防线，不宣称 Exactly Once；具体第三方 Tool 仍需幂等键、查询或补偿能力。

## 安全与可观测性

- API body `extra=forbid`，只接受 Catalog selection；Domain 进一步拒绝 URL/endpoint/transport/command/
  env/network/image/auth/token/password/secret/key/cookie 类参数名和超限值。
- `agent_message_accepted` Event 只记录启用 MCP 条目数量；Tool Event 只记录 prefixed tool name、effect
  ID、状态、attempt、result hash 或安全 error kind/code，不保存完整参数、结果、endpoint 或 Secret。
- owner/Project/Session/Turn 闭包在 `McpToolExecutionService` 中由 Run、AgentTurn、AgentSession 与不可变
  Policy 交叉验证；模型不能提交这些 scope ID。
- RuntimeExecution permit 在 MCP interceptor 中再次检查，过期 Worker owner 不能开始下一次 MCP 调用。
- 本切片没有真实网络、外部 MCP、Provider、OpenSandbox 或付费调用，因此不形成第三方隐私、供应链、
  egress、Sandbox 隔离或性能结论。

## 重要测试和实际结果

- 首轮 Domain 红灯因 `mcp_configuration` 模块不存在得到 `ModuleNotFoundError`；最小实现后 6 passed。
  首轮真实 adapter 测试为 2 failed：重放结果包含 SDK 新生成的非稳定 content ID，且进程内 MCP 关闭
  异常被 `ExceptionGroup` 包装；测试改为验证业务内容并显式展开 fixture 关闭异常后 2 passed，随后补到
  当前 5 项边界测试。
- Domain：6 passed，覆盖 prefixed SDK-neutral 引用、配置 hash、未知版本/参数、连接/Secret 参数与 revision。
- 主审加固后的 Domain + 真实 adapter + 进程内 FastMCP 为 `35 passed in 1.23s`：除显式 session、
  命名空间、Schema 漂移和输出限制外，还通过真实 LangGraph `ToolNode` 验证 `tool_call_id` 注入、同 ID
  replay、同参数不同 ID 各调用一次、缺 ID/加载前取消零连接、底层连接错误脱敏，以及 handler 后丢
  fence 时零失败终态写入；resolver/session/list/load/close 五个 SDK 生命周期边界都验证底层 endpoint/
  token 文本和异常 cause 不越过安全错误边界。
- PostgreSQL Application：`4 passed in 13.84s`，覆盖 owner/Session/CAS、逐 Turn 版本/配置/Schema
  冻结、同 Catalog 多版本不产生 revision、MCP invocation 冲突、12 个 distinct invocation 预算和 Event
  内容过滤；旧 Project Tool 回归 `8 passed in 26.43s`，确认其 args-based effect 行为未改变。
- FastAPI：5 passed in 0.58s，其中 MCP API 拒绝额外 endpoint 与 token 参数。
- Sandboxed Runtime：最终 12 passed in 1.15s，覆盖 MCP session 包围 graph、正常/工厂异常关闭顺序、
  MCP 关闭失败仍释放 Sandbox，以及
  offline reconcile/collect 不重新取得 Sandbox。默认受限命令环境会在既有 `asyncio.to_thread` executor
  退出时挂起；相同完全离线文件在批准的外部执行环境正常完成，记录为开发执行环境限制。
- 加入全部主审测试后的 Domain/Adapter/Sandbox/RuntimeExecution/单项 API 合并回归为
  `93 passed in 2.78s`。
- Migration：临时 `pgvector/pgvector:pg18` 完成 head → downgrade -1 → head → check，`5 passed in 4.79s`。
  首次迁移文件运行是 4 passed、1 failed，失败原因是测试子进程使用裸 `alembic` 而当前执行 PATH 不含
  venv；改用 `sys.executable -m alembic` 后完成标准往返，不是通过删除断言绕过。
- 不可变 revision 加固后 Application + Migration 组合为 8 passed in 15.82s，覆盖 rev2 更新后旧 Turn
  仍精确读取 rev1，并要求 owner/Session/profile ID/revision 四项全部匹配。
- 本切片定向 Ruff 通过；定向 Pyright 为 0 errors、0 warnings、0 informations。

## 代码入口

- Domain：`backend/src/literature_agent/domain/mcp_configuration.py`
- Profile Application：`backend/src/literature_agent/application/mcp_configuration_service.py`
- 调用账本：`backend/src/literature_agent/application/mcp_tool_execution_service.py`
- API：`backend/src/literature_agent/api/agent_sessions.py`
- MCP Adapter/interceptor：`backend/src/literature_agent/infrastructure/agent/mcp_tools.py`
- Runtime 生命周期：`backend/src/literature_agent/infrastructure/agent/sandboxed_research_agent_runtime.py`
- Profile Repository/ORM：`backend/src/literature_agent/infrastructure/persistence/mcp_profile_repository.py`、
  `backend/src/literature_agent/infrastructure/persistence/models.py`
- Migration：`backend/migrations/versions/f6a2c9d4e7b1_add_agent_mcp_profiles.py`

## 已知限制

- 生产 Catalog 现包含固定 Playwright/arXiv 两项，但默认 Session Profile 仍为空；用户必须显式选择平台
  条目。切片完成时尚未运行真实 OpenSandbox endpoint/header 代理回路；2026-08-28 后续本地功能 Smoke
  已验证认证 Server Proxy 与精确直连 authority 配置，但不把它当作真实 Provider、公共网络或生产安全验证。
- Profile 参数首版只支持有界字符串；复杂枚举、数字或对象 Schema 留到真实 Catalog 有证据时扩展。
- 没有 stateful 第三方 Server、Sandbox 内 stdio/HTTP Server、连接轮换、真实断连或多 Server 压力测试。
- 没有公共网络、OAuth/Credential Vault、Secret 委托、统一 egress 或第三方隐私/费用治理。
- ToolExecution orphan RUNNING 仍 fail-safe 拒绝自动重放，没有通用副作用对账与补偿。
- Graph/RuntimeExecution permit 在 MCP Tool 实际调用时已存在；但首次 graph 构造前的 resolver/session/
  discovery 只有业务取消门槛，还没有该次 execution 的 graph permit。跨进程重复 Job 可能重复只读连接与
  capability discovery，不能声称 client 创建 Exactly Once；实际 MCP effect 仍由 invocation 账本去重。

## 60 秒面试说明

“我没有把 MCP 配置直接交给 Deep Agents，更没有让用户提交 URL 或 command。平台先维护固定版本 Catalog，
用户只能在自己的 Session 选择条目和安全参数；每个 Turn 把 Catalog 版本、配置 hash、带命名空间的 Tool
名和 Schema hash 冻结到 Policy。Worker 在事务外为这一轮打开显式 MCP ClientSession，校验 Server 实际
Tool 集合后才传给 create_deep_agent，并在实际调用处再检查 owner scope、取消、fence、预算、超时和
输出。稳定 ToolExecution effect 让成功结果可重放，Event 只存 hash 和安全状态。SDK 类型和 endpoint 都
留在 infrastructure。7.3 又把 discovery 改成完整分页与允许子集投影，所以第三方 Server 新增 Tool 不会
被动态信任；生产已有两个固定条目，但默认 Profile 为空，真实 OpenSandbox 代理 Smoke 仍是独立门槛。”
