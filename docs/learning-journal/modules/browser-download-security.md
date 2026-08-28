# Sandbox 公网与声明来源目标安全

## 解决的问题

Phase 6 Slice 7 让 Session 专属 OpenSandbox 访问正常公网，同时保留 Sandbox 内部 loopback 给
Chromium/CDP、VNC 和 MCP 本地服务，并阻止非-loopback private、link-local、reserved、metadata、宿主/LAN
和容器控制面目标。raw Workspace 下载仍是 Runtime 内部文件；只有显式提交并重新校验后才成为正式
Artifact。

## 边界与流程

`research-public-egress.v1` 是 Domain 中 SDK-neutral、canonical JSON 哈希的固定档案。新 Turn 的
`PolicySnapshot` 冻结 Profile ID/version/hash；`SandboxLease` 保存相同三元组。Lease 复用同时校验镜像、
状态、TTL、generation 时长和 Profile 三元组。新 v3 Turn 遇到历史 NULL Profile 或漂移会进入已有
rotation/cleanup 流程，绝不续租旧环境。已经运行的三种 v2 Snapshot 则只在其冻结的 default-deny/NULL
Profile 边界恢复，避免阶段升级破坏恢复语义；未知 default-deny Policy 仍 fail closed。

OpenSandbox Adapter 只把已知三元组翻译为 `defaultAction=allow` 和固定 CIDR deny；既有显式
default-deny Smoke 路径仅用于保留 Phase 5/Slice 6 历史证据。平台不解析 HTTP method，因此不声称 raw
Browser/Shell/MCP 协议级只读。

Agent 若把网络文件作为成果交付，必须从 `/workspace/outputs` 调用 `submit_artifact` v2，可选附带
`source_url`：

1. 事务外规范化有界 HTTP(S) URL，拒绝凭据、fragment、localhost 和非公网 IP literal；
2. Manifest URL 移除可能携带 Secret 的 query，完整规范化来源只参与 SHA-256；事务外解析 DNS，要求
   非空且所有回答均为公网地址；
3. 再校验当前 Runtime permit 与 Sandbox generation/fence；
4. 读取唯一普通文件，执行单文件 10 MiB、扩展名/MIME/magic/结构/hash 校验并写 Storage；短事务中再以
   锁定 Run 串行复核每轮最多 8 个、正式候选总量最多 50 MiB；
5. 短事务内写 Candidate/Event；Turn 成功事务才发布正式 AgentArtifact；
6. Manifest 只公开规范化 URL/hash、文件 hash/大小/MIME 和声明目标检查状态；
   `declared_public_target_checked` 不证明文件字节来自该 URL，也不公开网页/PDF 正文或 raw
   `/workspace/downloads`。

DNS 校验只约束声明来源目标登记，不能把任意 raw egress 变成 HTTP 代理，也不能完全消除 DNS rebinding；真正
需要协议级方法控制时需新增通用 egress proxy/Approval，当前精简交付不建设。

## 数据与迁移

迁移 `c7d2f9a4e1b8` 为 PolicySnapshot/Lease 增加 nullable Profile 三元组以兼容历史事实，并为 Candidate/
Artifact 增加成对 nullable `source_url/source_url_hash`。数据库 Check Constraint 防止半套引用。新能力档案
版本升为 `agent-policy.project-research-*.v3`，`submit_artifact` 固定契约升为 `artifact-tool.v2`。

## 测试与证据

离线测试覆盖 canonical hash、固定 CIDR、内部 loopback 未被 Profile deny、恶意 URL、混合 DNS、ORM
约束、Provider DTO 翻译、旧/漂移 Lease generation 轮换、v2 default-deny 恢复、每 Turn 8 项/50 MiB
锁内预算和 Tool Schema hash。2026-08-28 主智能体独立离线复核合计 137 passed，其中 PostgreSQL/
Alembic 26 passed；Ruff 全后端通过，Pyright 为 0 errors。真实 Smoke 由
`AGENT_RUN_OPENSANDBOX_PUBLIC_EGRESS_TESTS=1` 显式启用，覆盖 Sandbox 内 loopback、arXiv、非 arXiv、
wget/Python/Node/Chromium/Playwright/Search MCP、同一固定 arXiv PDF 最多 64 KiB 有界前缀的 HTTP
200/206、Content-Type、`%PDF`/SHA-256，以及 metadata/private/宿主 gateway 拒绝。首轮真实运行已确认
Sandbox 内部 loopback，但随后报告固定镜像 `curl: not found`，所以没有形成公网失败或拒绝证据。脚本
改用并先精确断言 `/usr/bin/wget` 存在；第二轮已通过 arXiv 首页，但完整 2,215,244-byte PDF 超过 30 秒
Sandbox 命令限制，Adapter 外层最终以 exit 124 结束，尚未进入 private/MCP 检查。这是全量下载耗时而非
网络拒绝。脚本现用 Python Range 请求最多读取 64 KiB 后主动关闭，不声称完整 PDF 下载已经验证；私网
请求保留非零 exit 与 timeout/refused/unreachable 等错误输出断言，避免工具缺失误判通过。第三轮显式
真实 Smoke 为 1 passed（39.67s），实际覆盖同一 Sandbox 内部 loopback、`wget` arXiv 首页、固定
`1706.03762` 最多 64 KiB 前缀的 HTTP 200/206、Content-Type、`%PDF` 与 SHA-256、Python/Node/Chromium
访问 `example.com`、Playwright MCP `browser_navigate`、arXiv Search MCP `search_papers`；metadata
`169.254.169.254`、Docker gateway `:8080` 与 `10.0.0.1` 均被拒绝。

## 代码入口

- `domain/agent_network.py`
- `domain/research_agent.py`
- `infrastructure/agent/opensandbox_backend.py`
- `infrastructure/agent/sandbox_workspace.py`
- `application/agent_artifact_service.py`
- `api/agent_sessions.py`

## 已知限制

- 仅适用于 trusted-local 个人演示，不是公网多租户安全浏览器；
- 没有 HTTP method/业务语义强制只读；
- 不提供 Cookie/OAuth/平台 Secret、安装 Tool 或用户自定义网络策略；固定 Prompt 要求不动态安装，但 raw
  Shell 在开放公网下仍可能下载并执行用户态文件，当前 L3/L4 egress 无法把这条产品策略变成完整技术
  强制；不得宣称已实现通用动态代码阻断；
- DNS 与 URL 校验只证明提交时的目标分类，不证明文件字节一定来自声明 URL；预算冲突发生在 Storage
  staging 后时可能留下内容寻址孤儿 blob，GC 仍延期；
- 真实 Smoke 只覆盖上述固定目标，不证明完整 PDF 下载、所有公网目标、协议级只读、secure runtime 或
  生产隔离。

## 60 秒面试说明

系统没有把“在沙箱里”当作完整网络安全。平台把公网策略做成版本化 canonical Profile，冻结到每轮策略和
物理租约；策略漂移会轮换 generation。Sandbox 的 raw 公网能力与正式业务资源是两条边界：raw 文件可在
Workspace 内继续研究，但离开 Sandbox 前必须重新做 URL/DNS、文件类型、magic、大小、hash 和 fence
校验，并通过 Candidate 到 Artifact 的 effectively-once 发布流程。这样复用了 Deep Agents 的 Browser、
MCP 和 execute，又没有让 SDK 内部文件直接成为平台事实。
