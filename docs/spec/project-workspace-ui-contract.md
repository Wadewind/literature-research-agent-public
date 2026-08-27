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

## 3. 共享 Project Chrome

文献库、文献问答、综述和研究助手使用同一个紧凑 `ProjectWorkspaceHeader` 与 `ProjectNav`：

- 标题、Project 状态和模式入口位置一致；
- 工作区标题不沿用营销 Hero 尺度，不挤占对话 viewport；
- 导航固定为“文献库 / 文献问答 / 综述 / 研究助手”；
- Library 页面突出三个研究模式入口，同时明确文献库是资源底座；
- 保留既有冷灰纸面、墨蓝、朱红、Inter / IBM Plex Mono 与零圆角视觉语言。

## 4. 文献问答首页与范围

`/chat` 在三栏工作区中提供新建和历史：

```text
Conversation rail | 新建问答与范围选择 | Scope / Evidence Margin
```

- URL 无论文参数时默认整个 Project；
- `paper_id` 可重复出现，用于从文献库带入单篇或多篇预选；
- 预选 ID 只有存在于当前 Project `GET /papers` 结果时才生效，重复和跨 Project ID 被省略；
- 用户仍可在新建面板切换整个 Project、单篇或多篇固定范围；
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

## 6. 状态、可访问性与窄屏

- TanStack Query 持有 Project、Paper、Conversation、Message 与 Evidence 服务端状态；独立查询并行启动；
- React 本地状态只保存 scope 草稿、输入、稳定幂等意图、当前 Evidence 和栏宽；
- 页面 identity 变化重建交互状态，旧幂等 Key、问题草稿和 Evidence 选择不得进入新 Conversation；
- separator 使用 `role="separator"`、垂直方向、当前/最小/最大值与键盘操作；表单保持可见 label 或
  视觉隐藏但可访问的 label；
- 桌面是验收主体；窄屏隐藏 separator 并改为可顺序访问的布局，不建设 Drawer。

## 7. 非范围

- 不修改 RAG 检索、Claim/Citation/Evidence、Conversation 或 Run 后端契约；
- 不把 RAG Conversation 改造成持续模型上下文；
- 不接入官方 Deep Agents UI，不合并 AgentSession 与 Conversation；
- 不新增 UI 框架、依赖、移动 Drawer 或全局 Dashboard。

## 8. 测试与完成条件

- Vitest 覆盖 canonical 路由、Project-scoped URL 预选过滤、scope 请求和版本化栏宽存储；
- production build 通过 TypeScript strict；
- Phase 2 E2E 经 canonical `/chat` 完成 Project/单篇范围、刷新恢复与 Evidence 回跳；
- Phase 5 E2E 证明共享 Project chrome 没有破坏 Agent 两轮旅程；
- 1440×1000 桌面验收应确认 document 不滚动、三栏独立滚动、Composer 位于中栏底部，separator 的 pointer/
  keyboard/reset 与宽度持久化可用。
