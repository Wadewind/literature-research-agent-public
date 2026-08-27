# Web UI 应用壳与视觉重设计

> 状态：设计已确认，待实施。本文档供后续 code agent 直接执行；实施时同步更新
> `docs/spec/project-workspace-ui-contract.md` 中被取代的条款（见第 8 节）。

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

## 5. 借鉴经典 Agent 界面的三个零件（切片 3 及以后逐项评估）

参考 Manus 类界面，以下三个模式与本项目兼容，浅色化后采用；其余（深色皮肤、任务混排 sidebar）明确不采用：

1. **Turn 步骤时间线**：研究助手对话流中以"思考 / 使用技能 xxx / 执行了 N 个步骤 ▸ + 耗时"条目呈现
   AgentTurnRun 内部步骤（数据来自 Phase 5 已有的 Turn/Step）。
2. **右侧面板 tab 化**：Evidence Margin / PDF 预览 / Artifact 在同一右栏内 tab 切换，替代一次只显示
   一种内容。
3. **Composer 集成能力配置**：Evidence Matrix 选择器、能力开关收进底部 composer 区域（"输入 / 选择"式），
   替代独立配置面板。

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

- 新增 `web/src/components/PageBar.tsx`（面包屑 + 页面标题 ≤20px + actions 插槽，高约 48–56px）。
- 七个页面逐一接入；删除 `ProjectNav.tsx`、`ProjectWorkspaceHeader.tsx` 及其 CSS
  （`.project-workspace-*`、`.project-nav`、`.page-heading`、`.hero-grid`、`.project-mode-entry-grid`）。
- 验证：`npm test` + e2e 断言更新 + 截图走查。

**切片 3：工作区空间回收**

- 问答/研究助手全高化收尾；`.agent-welcome` 浅色化；问答创建页空态去空白。
- 可选子项（当轮再定）：Evidence Margin 折叠按钮（持久化到现有 `workspaceLayout` localStorage）、
  右栏 tab 化、composer 集成（第 5 节零件 2、3）。
- 验证：`npm test` + 问答/研究助手两页截图对比。

**切片 4：视觉 token 刷新**

- 执行第 6 节清单，一次提交；验证：`npm run build` + 五页截图对比。

## 8. 与现有契约的关系

- 本文档取代 `project-workspace-ui-contract.md` 第 3 节"共享 Project Chrome"（`ProjectWorkspaceHeader`
  + `ProjectNav` 方案）。实施切片 2 时必须同步改写该节为 sidebar + PageBar 描述，保持契约与代码一致。
- 该契约其余各节（路由、问答范围、三栏工作区、resize 规则）继续有效，本次改造不触碰。
- `AGENTS.md` 的技术基线、测试要求不变；本改造不引入新依赖。

## 9. 现状关键事实（实施前请重新核对，行号可能漂移）

- `web/src/App.tsx:19-29`：现有全局 header。
- `web/src/components/ProjectNav.tsx`、`ProjectWorkspaceHeader.tsx`：将被删除；使用方为
  `LibraryPage.tsx`、`ChatPage.tsx`、`ConversationPage.tsx`、`ReviewsPage.tsx`、`AgentPage.tsx`。
- `web/src/styles.css:28`：header 的宽度敏感 padding；`styles.css:329`：工作区全高规则（依赖 76px
  顶栏高，必须同步重写）；`styles.css:428`：`.agent-welcome`。
- `web/src/components/ChatWorkspaceFrame.tsx` 与 `.agent-workspace`：三栏与 resize 逻辑保留复用。
- 测试：`web/` 下 `npm test`（vitest）、`npm run build`（tsc）、`npm run test:e2e`（Playwright，
  需本地后端；环境不具备时如实报告未运行，不得声称通过）。

## 10. 非范围

- 后端/API/数据库任何改动；
- 深色主题、移动端专门设计；
- 会话自动命名（若做需后端写 title，另行讨论）；
- Turn 步骤时间线等第 5 节零件的最终落地（切片 3 时逐项确认再实施）。
