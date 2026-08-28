# Web UI 应用壳与视觉重设计

> 状态：实施中。应用壳骨架、轻页头、工作区空间回收/Agent 产品整合与视觉 token/可访问性刷新均已完成；
> 最终产品整合与复盘仍留给后续切片。
> `docs/spec/project-workspace-ui-contract.md` 第 3 节已同步改为当前 `AppSidebar + PageBar` 契约。

## 1. 背景与要解决的痛点

当前前端（`web/`）存在两类问题，已通过截图走查与代码确认：

**结构问题**

- 存在两套页面框架：居中容器页（`.app-main { max-width: 1240px }`，首页/文献库/综述）与全宽工作区页
  （`.app-main:has(.viewport-workspace-page)`，问答/研究助手）。项目标题与四个模式 tabs 在两类页面间横向
  位移数百像素，入口"跳动"。
- 全局 header（76px）+ 项目页头（面包屑 + 大标题 + 副标题 + tabs，约 150–190px）合计约占 1/5 屏高，
  挤压会话与 PDF 预览空间；面包屑、标题、高亮 tab 三处重复表达同一位置。

**视觉问题**

- 层级扁平：面板、卡片、按钮、输入框全部是 1px 边框 + 直角 + 白底，无主次。
- 小字滥用：大量 9–10px 文本承载关键操作。
- `--canvas: #eef1f3` 与 `--paper: #f9faf9` 对比过弱，分区完全依赖发丝线。
- 深色 ink 大块泛滥（创建项目、综述 workbench、研究助手欢迎页），稀释强调效果。

## 2. 已确认决策（不可随意推翻）

1. **左侧固定 sidebar 应用壳**：全部导航（品牌、全局入口、项目内四个模式入口）收进左侧 sidebar，
   位置永不跳动；顶部不再有全局 header。
2. **全站统一轻页头**：包括首页在内，所有页面使用同一 `PageBar` 轻页头（≤56px），不保留任何
   大 hero 标题页头。
3. **桌面演示优先**：窄屏（<900px）时 sidebar 折叠为图标栏即可，不做移动端专门设计。
4. **保留浅色编辑风视觉语言**（纸面、墨蓝、朱红、Inter / IBM Plex Mono、零圆角），不切换深色主题。
   理由：核心动作是对着 PDF 页码核对证据，浅色纸面与 PDF 预览融合；深色主题是"对话优先"产品的皮肤，
   与本项目"文献/证据优先"的定位冲突。
5. **会话列表保持两级**：全局 sidebar 放模式入口，会话/问答 rail 留在工作区内部。不采用 Manus 式
   "所有任务混进全局 sidebar"的做法——问答（Conversation）与研究助手（AgentSession）是不同 Run
   类型，混入会丢失模式上下文。
6. **纯前端范围**：不改任何 API、数据库或后端契约。

## 3. 目标信息架构

```text
┌─────────────┬──────────────────────────────────────────────┐
│ AppSidebar  │ PageBar（轻页头：面包屑/页面标题 + 操作）      │
│ 232px       ├──────────────────────────────────────────────┤
│ 可折叠 56px │                                              │
│             │  页面内容（展示页滚动；工作区页全高三栏）      │
│ 品牌块       │                                              │
│ 项目         │                                              │
│ 个人文献库   │                                              │
│ ──────────  │                                              │
│ 当前项目名   │                                              │
│  文献库      │                                              │
│  文献问答    │                                              │
│  综述        │                                              │
│  研究助手    │                                              │
│ ──────────  │                                              │
│ SPIKE chip  │                                              │
└─────────────┴──────────────────────────────────────────────┘
```

- 路由表不变（见 `project-workspace-ui-contract.md` 第 2 节）。
- `PageBar` 由各页面自行渲染（操作是页面级的），不做全局统一顶栏组件；工作区页把它放在
  `.viewport-workspace-page` grid 的首行（现有 `grid-template-rows: auto minmax(0,1fr)` 正好容纳）。
- 工作区页（问答/研究助手）内容区获得 `100dvh − PageBar` 的完整高度。

## 4. 页面级处理

- **首页（ProjectsPage）**：删 `.hero-grid` 大标题；`PageBar` 显示"研究项目"+ 计数，内容直接开始。
  保留 `.create-project` 深色块作为全站唯一深色强调。
- **项目文献库（LibraryPage）**：删 `ProjectWorkspaceHeader` 与三卡模式入口（`.project-mode-entry-grid`，
  入口已在 sidebar）；归档/修改信息等操作移入 `PageBar` 右侧或"⋯"菜单。
- **综述（ReviewsPage）**：同上；`.review-workbench` 深色块改浅色底 + 朱红左边条。
- **文献问答（ChatPage/ConversationPage）**：删大标题页头；创建页的 scope picker 下方不留整屏空白。
- **研究助手（AgentPage）**：删大标题页头；`.agent-welcome` 深色大块（`min-height: 520px`）改为浅色
  引导卡（保留朱红边条与简短说明），选中会话后让位给对话流。
- **个人文献库（PersonalLibraryPage）**：`.page-heading` 替换为 `PageBar`。

## 5. 借鉴经典 Agent 界面的三个零件（切片 3 已落地）

参考 Manus 类界面，以下三个模式与本项目兼容，浅色化后采用；其余（深色皮肤、任务混排 sidebar）明确不采用：

1. **Turn 研究活动**：研究助手对话流以可折叠区呈现已筛选 Event、脱敏 ToolExecution 以及 Usage/Budget
   摘要；不展示 raw args、完整 Prompt、网页正文、Secret 或大型 Tool 输出。
2. **右侧检查器 tab 化**：固定为“证据 / 浏览器 / 成果”三个可访问 tab。证据区显示 Context ledger 与
   Claim/Citation；浏览器复用既有 noVNC；成果区显示正式 Artifact、内部 Candidate 与正式 Manifest
   来源摘要。三个面板按当前选中 Turn 查询，tab 仅是本地 UI 状态。
3. **Composer 集成能力配置**：Evidence Matrix、附件和能力配置收进底部 composer；首轮 Skill 锁定、
   dirty 时禁止发送以及显式保存行为保持不变。

## 6. 视觉 token 刷新清单（均在 `web/src/styles.css`）

1. `--paper` 改为 `#ffffff`；卡片类（`.project-card`、`.message`、`.panel`、`.notice`）去边框或减淡，
   hover 阴影改为常驻更弱阴影。
2. 字号下限：9–10px 文本提升至 11–12px（`.paper-identity p`、`.conversation-list span`、
   `.agent-session-list span`、`.agent-composer small` 等逐处排查）；计数/日期/文件大小加
   `font-variant-numeric: tabular-nums`。
3. 深色块收敛：`.review-workbench`、`.ask-strip` 改浅色 + 朱红边条；仅保留 `.create-project` 深色。
4. 列表行（`.project-paper-row`、`.ledger-row` 等）补统一 hover 底色。
5. 增加 `prefers-reduced-motion` 媒体查询：关闭 skeleton sweep、`.progress-pulse`、卡片位移动画。
6. 主体网格底纹（`body` 的 48px 网格线）保留——统一轻页头后不再与工作区发丝线冲突；实施时复查
   工作区页实际效果，若干扰明显再取消。

## 7. 实施切片（每片独立可验证、可审查）

**切片 1：应用壳骨架**

- 新增 `web/src/components/AppSidebar.tsx`：品牌块、`NavLink` 全局入口（项目 `/`、个人文献库
  `/library`）、当前项目分区（`useParams`/`useLocation` 判断 `/projects/:projectId/*`，复用
  React Query 缓存的 project 查询渲染项目名与四个模式入口，按路由高亮）、底部 SPIKE chip、
  折叠为 56px 图标栏的切换。
- 新增 `AppSidebar.test.tsx`（失败测试先行）：项目路由外只渲染全局入口；项目路由内渲染四个模式
  入口且 active 正确；折叠按钮存在。
- 重构 `App.tsx`：`.app-shell` 改 `grid-template-columns: auto minmax(0,1fr)`，删除 `.app-header`，
  保留 `ScrollToTop`。
- `styles.css`：删 `.app-header`/`.primary-nav`/`.phase-chip`，新增 `.app-sidebar` 样式组；
  `.app-main:has(.viewport-workspace-page)` 高度由 `calc(100dvh - 76px)` 改为 `100dvh`。
- 同步更新 `web/e2e/*.spec.ts` 中引用旧顶部导航的选择器。
- 验证：`npm test`、`npm run build`、五页截图走查。

**切片 2：轻页头替换**

- **已实现（2026-08-28）**：新增 `web/src/components/PageBar.tsx`（语义化面包屑 + 页面标题 20px +
  actions 插槽，固定高 56px）。
- Projects、Personal Library、Project Library、Chat、Conversation、Reviews、Agent、Review Detail，以及
  Run Detail、Document 和 Not Found 状态均已接入；删除 `ProjectNav.tsx`、`ProjectWorkspaceHeader.tsx` 及其 CSS
  （`.project-workspace-*`、`.project-nav`、`.page-heading`、`.hero-grid`、`.project-mode-entry-grid`）。
- 验证：PageBar TDD 先得到缺少模块的失败，再转为定向 3 passed；完整 `npm test` 为 25 files / 164 passed，
  `npm run build` 通过。完整离线 E2E 为 4 passed / 1 failed：Phase 1/2/3/5 通过，Phase 4 仍是已有的
  来源列表期望 4、实际 3 的 Fixture/时序问题，未放宽断言。1440×1000 截图走查覆盖首页、综述和研究助手；
  研究助手实测 PageBar 56px，document `scrollHeight` 与 viewport 均为 1000px，既有三栏没有横向裁剪。
- 已知限制：研究助手无选中 Session 的首页仍会发出既有空 `session_id` 附件请求并得到 404；该数据请求
  不属于轻页头切片，未在此顺便修改。深色 Agent welcome 和 Review workbench 按顺序留给切片 3/4。

**切片 3：工作区空间回收**

- **已实现（2026-08-28）**：`.agent-welcome` 改为浅色紧凑引导卡，问答创建页去除把操作区推到底部的
  大面积空白；现有三栏继续占满 `PageBar` 下方高度，document 不承担工作区滚动。
- Agent 右栏替换为“证据 / 浏览器 / 成果”检查器；inactive panel 以原生 `hidden` 退出布局，同时保持
  浏览器组件挂载，避免切 tab 重置 ticket/noVNC 状态。当前 Turn 才查询 ToolExecution 与 Manifest；成果
  分区复用正式 Artifact，并补充内部 Candidate 和 Manifest 的公开来源元数据。
- 中栏“研究活动”整合筛选 Event、脱敏 ToolExecution 与 Usage/Budget；公开类型不包含 raw args，Manifest
  只显示名称、媒体类型、hash、大小、来源状态和服务端实际返回的来源链接。
- 能力配置已移入 composer 的“研究设置/能力”区域，与 Evidence Matrix、附件共同构成紧凑设置区；无
  Session 时附件查询保持 disabled，选中 Session 后的真实错误仍正常暴露。
- TDD 先得到 4 个缺失模块失败，完成后定向为 4 files / 5 passed。完整 `npm test`、`npm run build` 与
  `git diff --check` 的最终结果记录在 Phase 6 Spec；Phase 5 E2E 在两次适配新 tab 语义的红灯后保持原
  业务断言并最终为 1 passed（37.1s）。
- 1440×1000 走查确认 Chat/Agent 的 document `scrollHeight` 等于 viewport；Agent timeline 与检查器独立
  `overflow: auto`，composer 始终在 viewport 内。键盘从“证据”按 ArrowRight 可聚焦“浏览器”；展开能力
  配置后 document 仍不滚动，timeline 缩小为内部滚动。Browser noVNC 继续是独立 lazy chunk。
- 已知限制：走查所选的一个历史 Turn 早于 Usage 事实落地，`tool-executions` 返回 404；UI 显示安全错误，
  新创建 Turn 的 Phase 5 E2E 返回 200。该兼容问题不在纯前端切片中通过伪造数据掩盖。

**切片 4：视觉 token 刷新**

- **已实现（2026-08-28）**：`--paper` 统一为纯白，网格降至 64px 极低对比度；卡片、消息、Panel 和
  Notice 使用弱边框/弱阴影建立纸面层级。Review workbench、RAG ask strip 与 Agent welcome 均已改为
  白色纸面 + 朱红左边条，全站仅 Project 创建引导区保留大面积深色。
- 关键 9–10px 功能文本提升至 11–12px；日期、计数、大小、时间与 hash 等元数据统一采用
  `tabular-nums`。列表补充克制的 hover/focus 反馈，保留直角、墨蓝/蓝色与朱红识别，不引入模板化圆角。
- `AppFrame` 新增键盘可见的“跳到主内容”链接和稳定 `main-content` 目标；全局 `:focus-visible`、
  `touch-action: manipulation` 与 `prefers-reduced-motion` 规则已落地。Artifact 图片补齐显式尺寸，未使用
  `transition: all`。
- TDD 先得到 AppFrame 缺失和图片尺寸缺失 2 个失败，完成后定向 2 files / 3 passed；完整 `npm test`
  为 30 files / 170 passed，`npm run build` 与 `git diff --check` 通过。
- 1440×1000 走查覆盖首页、Project 文献库、Chat、Reviews 与 Agent：首页/Reviews/Agent 的工作区均为
  白色研究档案风；Chat/Agent document 高度等于 viewport，三栏无横向溢出，Agent composer 始终可见。
  Skip link 通过 Tab 可见、Enter 后焦点进入 `main-content`。已知控制台噪声仍包括既有 favicon 404，
  以及所选历史 Agent Turn 早于 Usage 事实落地导致的 ToolExecution 404；未在本纯前端切片伪造数据。

## 8. 与现有契约的关系

- 本文档取代 `project-workspace-ui-contract.md` 第 3 节"共享 Project Chrome"（`ProjectWorkspaceHeader`
  + `ProjectNav` 方案）。实施切片 2 时必须同步改写该节为 sidebar + PageBar 描述，保持契约与代码一致。
- 该契约其余各节（路由、问答范围、三栏工作区、resize 规则）继续有效，本次改造不触碰。
- `AGENTS.md` 的技术基线、测试要求不变；本改造不引入新依赖。

## 9. 当前实现锚点

- `web/src/App.tsx`：`AppFrame` 提供 skip link、稳定主内容目标与 `AppSidebar + main` 应用壳。
- `web/src/components/AppSidebar.tsx`、`PageBar.tsx`：承担全局/Project 导航与统一轻页头；旧
  `ProjectNav`、`ProjectWorkspaceHeader` 已删除。
- `web/src/styles.css`：集中维护白色纸面 token、弱网格、全局 focus/reduced-motion、展示页和
  viewport 工作区规则。
- `web/src/components/ChatWorkspaceFrame.tsx` 与 `.agent-workspace`：继续负责三栏和独立 resize/滚动；
  视觉刷新不复制或移动业务状态。
- `web/src/components/AgentArtifactList.tsx`：正式 Artifact 缩略图具有显式宽高和 lazy loading。
- 验证入口：`web/` 下 `npm test`、`npm run build`、`npm run test:e2e`（需本地后端）。

## 10. 非范围

- 后端/API/数据库任何改动；
- 深色主题、移动端专门设计；
- 会话自动命名（若做需后端写 title，另行讨论）；
- 独立设计系统或 UI 组件框架重写；
- 将本轮桌面截图走查宣称为全量 WCAG、跨浏览器或移动端认证。
