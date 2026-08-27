# Agent Chat UI 前后端接口契约

## 1. 目的与产品位置

本契约固定 Phase 5 Slice 8 的最小用户可见闭环。系统提供的是三种 Project 研究模式，而不是三套等价
Chat：

- **文献问答**：每个问题独立检索 Project/Paper 范围，展示可回查的 Claim/Citation；
- **综述**：用固定 Workflow 生产 Evidence Matrix、Section 与正式 Artifact；
- **研究助手**：在 Project-scoped AgentSession 中持续交互，每条用户消息创建独立 AgentTurnRun，并可
  使用指定 Evidence Matrix、Project Index 和冻结的 MCP/Skill 能力。

Slice 8 不直接接入或代理官方 Deep Agents UI。前端只消费平台 REST/SSE，不能访问 SDK Thread、
Checkpoint、Graph State、Sandbox endpoint 或原始 Tool 输出。Deep Agents UI 只作为消息、Tool 进度与
文件展示的交互参考。

> 后续边界：ADR-0009/0010 已把 Browser 画面/跨 Turn 人工控制和 Attachment/AgentArtifact 文件交付列入
> Phase 6。它们继续使用平台业务 API 与 owner/Session/generation 授权，不改变本契约“不连接 SDK UI、
> 不暴露 Runtime endpoint、WorkspaceSnapshot 不作为用户文件”的原则。

## 2. 信息架构与路由

Project 工作区导航固定为：

```text
文献库 | 文献问答 | 综述 | 研究助手
```

本切片新增：

```text
/projects/:projectId/agent                 AgentSession 列表与新建入口
/projects/:projectId/agent/:sessionId      Agent 研究工作区
```

Slice 8.1 已进一步将 RAG 创建/历史迁入 canonical `/projects/:projectId/chat` 工作区，并为 RAG 与
Agent 共用紧凑 Project chrome 和三栏 resize 规则。详细路由、兼容路径与信息架构以
[`project-workspace-ui-contract.md`](project-workspace-ui-contract.md) 为准；这不把 RAG Conversation
改造成有模型历史的对话，也不合并 Conversation 与 AgentSession 生命周期。

## 3. 桌面端交互契约

Agent 研究工作区采用三栏：

```text
Session 列表 | 用户/Assistant 消息与筛选后研究活动 | Evidence/Context/候选成果边栏
```

- 左栏按 `last_activity_at DESC, session_id DESC` 展示当前 Project 的 Session，可创建并进入 Session；
- 中栏只展示产品 User/Assistant Message。Tool、MCP、Sandbox 与 Runtime 进度投影为折叠的业务事件摘要，
  不展示思考过程、完整 Prompt、网页正文、论文全文、Secret 或大型 Tool 输出；
- 右栏展示当前 Turn 选定的 Evidence Matrix、Project Index 数量、Assistant Claim/Citation 和 staged
  candidate 元数据；Evidence 继续复用 Project-scoped Evidence/PDF 读取路径；
- 同一 Session 有活动 Turn 时禁用再次发送，显示状态并允许通过通用 Run 取消接口请求取消；
- 页面刷新后必须从 Session、Message、Turn 与 Run API 恢复，不以浏览器内存或 SSE Event 作为事实来源；
- 本切片以桌面端为验收主体。窄屏只要求主区域可访问且不出现阻断性溢出，不建设移动 Drawer 交互。

## 4. 首条消息前的能力配置

新建 Session 后、发送首条消息前，界面按以下顺序读取并保存配置：

1. 用户从当前 Project 的 Review 列表选择一次已生成的 Evidence Matrix；发送请求使用该 Output 的
   `output_id`，不使用 `review_run_id` 代替；
2. 读取平台 MCP Catalog 与当前 Session MCP Profile；用户只能选择 Catalog 条目并填写其声明的非敏感
   参数；
3. 读取可用 Skill 与当前 Session Skill Profile；首条 Message 创建后 Profile 永久锁定，更换 Skill
   需要新建 Session；
4. Sandbox、MCP endpoint、transport、command、env、认证信息和 SDK 配置均不进入用户表单。

Evidence Matrix 是逐 Turn 明确选择的授权输入，不永久绑定 Session。第二轮可以继续使用同一 Output，
也可以显式选择当前 Project 的另一个合法 Evidence Matrix。

Matrix 的可用性由 canonical aggregate Output 事实决定，不由父 Review 的整体状态替代：Review 即使因
后续章节生成失败而处于 `failed`，只要 owner/Project-scoped 的最新
`output_type=evidence_matrix + output_key=evidence-matrix` 已存在，该 Matrix 仍可供 Agent 选择；没有该
聚合 Output 的 Review 不可选。前端不得为列表中的每个 Review 逐项探测 Matrix。

## 5. REST 接口

### 5.1 新增：按 Project 列出 AgentSession

```http
GET /api/v1/projects/{project_id}/agent-sessions
```

响应：

```json
[
  {
    "session_id": "uuid",
    "project_id": "uuid",
    "title": "研究缺口分析",
    "status": "active",
    "active_turn_run_id": null,
    "created_at": "2026-08-27T00:00:00Z",
    "last_activity_at": "2026-08-27T00:00:00Z"
  }
]
```

不变量：

- owner 来自可信 Actor，不来自 Query/Body；
- Project 不存在、越权统一返回 404；
- 只返回该 owner/Project 的业务 Session，不返回 Runtime Binding；
- 使用单次 Project-scoped 查询并稳定倒序，不在 API 层逐 Session 查询；
- 空 Project 返回 `[]`。

### 5.2 保留：创建和读取 Session

```http
POST /api/v1/projects/{project_id}/agent-sessions
GET  /api/v1/agent-sessions/{session_id}
```

创建体仍为 `{ "title": string | null }`。不接受 Project owner、Runtime、Thread、Workspace、Sandbox 或
能力配置字段。

### 5.3 扩展：消息携带验证后的 Claim/Citation

```http
GET /api/v1/agent-sessions/{session_id}/messages
```

每条响应扩展为：

```json
{
  "message_id": "uuid",
  "session_id": "uuid",
  "sequence": 2,
  "role": "assistant",
  "content": "...",
  "turn_run_id": "uuid",
  "claim_set_id": "uuid-or-null",
  "created_at": "2026-08-27T00:00:00Z",
  "claims": [
    {
      "text": "...",
      "citations": [
        {
          "evidence_id": "uuid",
          "paper_id": "uuid",
          "version_id": "uuid",
          "section_path": "methods",
          "page_start": 3,
          "page_end": 4,
          "excerpt": "有界摘录"
        }
      ]
    }
  ]
}
```

用户消息和没有 ClaimSet 的 Assistant Message 返回 `claims: null`。服务端从已持久化 Claim/Citation/
Evidence 组装摘要；前端不得解析 Assistant 文本中的 `[evidence:...]` 标记作为授权或引用事实。Evidence
缺失时不制造引用，且所有读取继续受 owner/Project/Session 闭包约束。

### 5.4 保留：发送消息与 Turn 详情

```http
POST /api/v1/agent-sessions/{session_id}/messages
GET  /api/v1/agent-turn-runs/{run_id}
```

发送体固定为：

```json
{
  "content": "分析这些研究的主要方法差异",
  "review_output_id": "uuid"
}
```

请求必须携带稳定 `Idempotency-Key`。相同失败意图复用 Key；内容或 `review_output_id` 变化时生成新 Key；
成功后清除本地意图。`409 agent_session_busy` 不在前端创建排队消息。

Turn 详情继续返回冻结的 `review_output_id`、`project_index_refs`、状态与 staged candidates。候选仅展示
`candidate_id/name/media_type/content_hash/size_bytes/status`；Slice 8 不新增候选内容读取或正式 Artifact
提交协议。Phase 6 按 ADR-0010 另增 `submit_artifact`、AgentArtifact 内容 API 和附件引用，不回写
Slice 8 的历史完成范围。

### 5.5 复用：配置、Evidence、Run 与 Review

前端复用以下接口，不新增 SDK 专用代理：

- `GET/PUT /api/v1/agent-sessions/{session_id}/mcp-profile`；
- `GET /api/v1/agent-mcp-catalog`；
- `GET/PUT /api/v1/agent-sessions/{session_id}/skill-profile`；
- `GET /api/v1/agent-skills`；
- `GET /api/v1/projects/{project_id}/reviews` 与
  `GET /api/v1/projects/{project_id}/reviews/{run_id}/evidence-matrix`；
- `GET /api/v1/projects/{project_id}/evidence/{evidence_id}` 及 Project-scoped PDF endpoint；
- `GET /api/v1/runs/{run_id}`、`GET /api/v1/runs/{run_id}/events/stream`、
  `POST /api/v1/runs/{run_id}/cancel`。

Review 列表每项附加可空 `evidence_matrix` 摘要：

```json
{
  "output_id": "uuid",
  "version": 1,
  "row_count": 20,
  "valid_papers": 4,
  "failed_papers": 6
}
```

该字段由同一 Project/owner-scoped 列表读路径批量组装最新 canonical aggregate Output；`null` 表示当前
没有可用于 Agent 的聚合 Matrix。摘要只读取现有 `payload.rows` 和 `payload.summary` 的有界计数，不把
Matrix 正文复制进列表。

Agent 首页另读取最小 Project Context 摘要：

```http
GET /api/v1/projects/{project_id}/agent-context-summary
```

响应 `{ "ready_index_count": 25 }`，只统计当前 owner/Project 收录版本中存在 ready ChunkSet 的文献。
无 Turn 时 Evidence Margin 标为“当前 Project 索引”；有 Turn 时改用冻结的
`project_index_refs` 并标为“本轮索引快照”。两者不能用同一个隐含的 `0` 代替未知或尚未加载状态。

## 6. Event 展示与安全

前端 EventSource 增加 Agent 已登记业务事件，但只把下列语义投影给用户：

- 消息已接受、Run 已开始/重试/取消；
- Project/MCP Tool 开始、完成或安全失败摘要；
- Runtime 已绑定（只显示“研究环境已就绪”，不显示 binding ID）；
- 候选成果已暂存；
- Turn 成功、失败或取消。

Event payload 仍是诊断与进度提示，不能替代 Message、Turn、Candidate 或 Run REST 事实。UI 不直接展开
`binding_id`、原始 Tool 参数/结果、endpoint、Prompt、Workspace 内容或 Runtime 错误 cause。

## 7. 前端状态与视觉约束

- TanStack Query 保存 Session、Message、Profile、Review Output、Turn、Run 与 Evidence 查询状态；
- React 本地 State 只保存表单、当前选中的 Matrix/Evidence、折叠状态与可重试消息意图；
- 独立查询并行启动，SSE 只触发相关 Query 失效；
- 继续使用现有 Vite React、CSS token、Inter/IBM Plex Mono、零圆角和冷灰纸面，不新增 UI 框架；
- Agent 页唯一签名元素是右侧 **Evidence Margin**：回答引用与论文页码按 Claim 排列，表达“研究证据
  批注”而不是通用 Coding IDE；
- 桌面 Agent route 使用 viewport workspace。Session、Message、Evidence 三栏独立滚动，中栏只有消息
  时间线滚动、Composer 固定在底部；Matrix 选择使用紧凑 context row，默认消息输入约 72–88px，避免
  Composer 挤占对话历史；能力配置展开为中栏内浮层，不改变消息区高度；
- 左右栏由可聚焦的垂直 separator 调整宽度，支持鼠标拖动、方向键和双击恢复默认值。宽度只以带版本
  的最小 localStorage 记录保存，非法/旧版本数据回退默认值；业务事实不进入 localStorage；
- `PHASE 04` Header 标记更新为不误导的 `RESEARCH AGENT · SPIKE`，避免暗示公网生产可用。

## 8. 测试与完成条件

- Application/API/PostgreSQL：Session 列表排序与 owner/Project 隔离；Message Claim/Citation 摘要及
  Evidence 闭包；不存在/越权统一 404；
- Vitest：消息幂等意图、Agent Event 映射、活动 Turn 禁发、配置锁提示、Evidence/Candidate 投影；
- Playwright E2E：使用 Fake Runtime 稳定覆盖“创建 Session → 配置 → 选择 Matrix → 第一轮 → 刷新恢复
  → 第二轮 → Evidence/候选成果”子集，不访问真实模型、网站、外部 MCP 或付费 Sandbox；Fake Turn
  完成过快时不以等待竞争制造取消覆盖，停止按钮与取消收束改由通用 Run 纯规则、后端取消测试验证；
- 主智能体使用有头 `playwright-cli` 检查桌面三栏、空态、活动 Turn、终态、Evidence Margin、Console 与
  Network；移动端不作为本切片独立验收故事。
