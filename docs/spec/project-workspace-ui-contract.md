# Project 工作区 UI 契约

## 1. 目的与产品位置

本契约固定 Phase 5 Slice 8.1 的 Project-scoped 信息架构。Project 是一项研究的工作空间，文献库是三种
研究模式共享的资源底座，而不是第四种研究模式：

```text
Project
  ├─ 文献库：收录、解析、索引与版本固定
  ├─ 文献问答：对 Project / 单篇 / 选中文献做独立 RAG 问答
  ├─ 综述：运行固定、可暂停恢复的 Review Workflow
  └─ 研究助手：在 AgentSession 中持续研究
```

三种研究模式平级进入；它们复用 Project 文献、Evidence 和 Citation，但不合并业务生命周期。RAG
`Conversation` 仍是逐问题检索，Research Agent 仍是持续 `AgentSession/AgentTurnRun`。

## 2. 路由与兼容性

Project 工作区 canonical 路由为：

```text
/projects/:projectId                         文献库
/projects/:projectId/chat                    文献问答首页、范围选择与历史
/projects/:projectId/chat/:conversationId    文献问答详情
/projects/:projectId/reviews                 综述
/projects/:projectId/agent                   研究助手
```

既有 `/projects/:projectId/conversations/:conversationId` 继续可直接访问，避免旧书签失效；产品内部新链接
统一生成 `/chat/:conversationId`。兼容路由不复制页面或状态。

## 3. 共享应用壳与轻页头

全站使用固定 `AppSidebar`；进入 `/projects/:projectId/*` 后，Sidebar 读取 owner-scoped Project 查询并增加
当前项目分区。项目的“文献库 / 文献问答 / 综述 / 研究助手”四个入口只在这个分区呈现，Library 页面不再
复制模式 tabs 或三卡入口：

- Sidebar 桌面宽 232px，用户可折叠为 56px icon rail；折叠偏好只进入版本化 `localStorage` UI 状态，
  不保存 Project、Session 或其他业务事实；
- 四个入口使用 canonical route 和 `aria-current="page"` 表达当前位置；当前 Project 名只来自平台查询，
  不信任 URL 或本地缓存伪造所有权；
- 所有主页面自行渲染统一 `PageBar`：语义化 breadcrumb、唯一页面 `h1`（≤20px）和可选 actions，
  高度不超过 56px；Project 名出现在 breadcrumb，当前模式出现在标题，不再叠加营销 Hero、副标题和 tabs；
- 归档/修改/取消等页面级操作放在 `PageBar` actions；工作区页把 PageBar 置于
  `.viewport-workspace-page` 首行，整个页面保持 `100dvh`，剩余高度交给三栏内容；
- 错误、加载、Run Detail 与 Document 等诊断页也保留同一 PageBar，避免状态切换时页面 identity 跳动；
- 保留既有冷灰纸面、墨蓝、朱红、Inter / IBM Plex Mono 与零圆角视觉语言。

## 4. 文献问答首页与范围

`/chat` 在三栏工作区中提供新建和历史：

```text
Conversation rail | 新建问答与范围选择 | Scope / Evidence Margin
```

- URL 无论文参数时默认整个 Project；
- `paper_id` 可重复出现，用于从文献库带入单篇或多篇预选；
- 预选 ID 只有存在于当前 Project `GET /papers` 结果时才生效，重复和跨 Project ID 被省略；
- 首页上方提供三个固定、范围中立的推荐问题，中央工作区底部使用唯一 Composer 承载推荐或自由问题
  草稿与当前范围摘要；推荐问题是显式选择草稿，不能在点击时创建 Conversation 或自动提交模型 Run；
- 选择推荐问题会预填底部 Composer 并打开中央“确认检索边界”Dialog；自由问题由用户点击“创建问答”
  后进入同一 Dialog。Dialog 内可选择整个 Project、单篇或多篇固定范围，取消只丢弃本次范围修改并保留
  问题草稿，只有“确认并创建问答”才调用既有 Conversation API；
- 空问题不能通过主操作创建 Conversation；范围摘要按钮可随时重开 Dialog，URL 带入的合法论文预选
  必须作为初始范围呈现；
- 创建成功后通过固定 `question_template` ID 或一次性 route state 把问题草稿交给新 Conversation
  Composer，Composer 只预填、不自动发送；初始化后必须从当前 history entry 消费该交接状态；
- 创建仍调用既有 Project-scoped Conversation API，不新增后端接口；
- 归档 Project 只读，历史可访问但不能创建新问答。

文献库不再读取或渲染 Conversation 历史，也不直接创建 Conversation。论文行的“询问此篇”和选中论文
操作只导航到 `/chat` 并携带预选参数。

## 5. 文献问答详情工作区

`/chat/:conversationId` 使用桌面 viewport 三栏：

```text
Conversation rail | 消息时间线 + 固定 Composer | Evidence Margin
```

- 左、中、右栏独立滚动，document 不承担对话历史滚动；
- 中栏只有消息时间线滚动，Composer 固定在底部；
- 左右 separator 可聚焦，支持 pointer、方向键和双击复位；
- 宽度只用带版本的最小 `localStorage` 记录保存 UI 偏好，不保存 Conversation、Evidence 或其他业务事实；
- Evidence Margin 继续只从 Project-scoped Evidence API 读取持久引用，前端不解析回答正文制造引用；
- Conversation 响应必须属于路由 Project，否则统一显示“资源不存在或无权访问”，不得继续提交或读取
  Project-scoped Evidence。

三栏 resize 规则与 Research Agent 共用小型 UI helper/component；两种业务仍使用独立 storage key，避免
一个模式的宽度偏好意外覆盖另一个模式。

## 6. Research Agent 工作区与 Turn 检查器

`/agent/:sessionId` 使用桌面 viewport 三栏：

```text
AgentSession rail | 消息时间线 + 研究活动 + 固定 Composer | Turn 检查器
```

- 检查器固定为“证据 / 浏览器 / 成果”三个 `role="tab"`；支持方向键、Home/End 和 roving tabIndex，
  当前 tab 只属于本地 UI 状态，不写入 Session、Turn 或其他业务事实；
- 证据区只显示当前 Turn 的 ContextSnapshot 索引水位、Evidence Matrix 与 Claim/Citation；浏览器区复用
  当前 Session/generation 的 noVNC 组件；成果区组合正式 AgentArtifact、内部 Candidate 和当前 Turn 的
  Artifact Manifest；inactive panel 使用 `hidden` 退出布局，但保持浏览器组件挂载以避免切换时丢失连接；
- ToolExecution 与 Manifest 只在存在当前 `candidateTurnRunId` 时查询。研究活动仅展示 Tool 名称/版本、
  状态、安全摘要、耗时、输入/输出字节数和 Usage/Budget；不得显示 raw args、完整 Prompt、网页正文、
  Secret 或大型输出；
- Manifest 只显示公开元数据、`source_status`、hash、大小和服务端返回的 `source_url`；未返回 URL 时前端
  不构造链接，不把 Candidate 或 Sandbox 路径冒充正式来源；
- Composer 内统一放置 Evidence Matrix、研究能力与附件；保持首轮 Skill 锁定、配置 dirty 时禁止发送和
  显式保存行为。无 `sessionId` 或不可交互时不得发出附件查询，但选中 Session 后的真实请求错误不能隐藏；
- 左、中、右栏独立滚动，document 不承担历史、Tool 列表或检查器内容滚动；能力 details 展开时只压缩
  timeline 的可用高度，不能遮挡或推出固定 Composer。

## 7. 状态、可访问性与窄屏

- TanStack Query 持有 Project、Paper、Conversation、AgentSession/Turn、Message、Evidence、ToolExecution、
  Manifest 与 Artifact 服务端状态；独立查询按业务 ID 与 enabled 条件启动；
- React 本地状态只保存 scope 草稿、输入、稳定幂等意图、当前 Evidence/Inspector tab 和栏宽；
- 页面 identity 变化重建交互状态，旧幂等 Key、问题草稿和 Evidence 选择不得进入新 Conversation；唯一
  例外是 Chat 首页创建成功时显式绑定到新 `conversation_id` 的单次问题草稿交接，目标 Composer 初始化
  后必须消费，后续刷新或进入其他 Conversation 不得再次预填；
- separator 使用 `role="separator"`、垂直方向、当前/最小/最大值与键盘操作；表单保持可见 label 或
  视觉隐藏但可访问的 label；
- 桌面是验收主体；窄屏隐藏 separator 并改为可顺序访问的布局，不建设 Drawer。

## 8. 非范围

- 不修改 RAG 检索、Claim/Citation/Evidence、Conversation 或 Run 后端契约；
- 不把推荐问题点击变成隐式模型调用，也不在前端串联 Conversation 创建与首条 Message 提交；
- 不把 RAG Conversation 改造成持续模型上下文；
- 不接入官方 Deep Agents UI，不合并 AgentSession 与 Conversation；
- 不新增 UI 框架、依赖、移动 Drawer 或全局 Dashboard。

## 9. 测试与完成条件

- Vitest 覆盖 canonical 路由、Project-scoped URL 预选过滤、scope 请求和版本化栏宽存储；
- production build 通过 TypeScript strict；
- Phase 2 E2E 经 canonical `/chat` 完成 Project/单篇范围、刷新恢复与 Evidence 回跳；
- Phase 5 E2E 证明共享 AppSidebar + PageBar 没有破坏 Agent 两轮旅程；
- Vitest 覆盖 Agent Inspector tab/键盘语义、Tool/Usage/Manifest 的安全投影和空 Session 附件查询条件；
- Phase 5 E2E 在成果 tab 定位 Candidate/Artifact，并切回证据 tab 验证下一 Turn 的 ContextSnapshot；
- 1440×1000 桌面验收应确认 document 不滚动、三栏独立滚动、Composer 位于中栏底部，separator 的 pointer/
  keyboard/reset 与宽度持久化可用；Agent 还需确认 Inspector tab 键盘切换、能力 details 展开不遮挡
  timeline/composer，Browser/noVNC 保持 lazy chunk。
