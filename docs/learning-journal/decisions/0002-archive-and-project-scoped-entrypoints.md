# 产品决策 0002：归档模型与 Project-scoped RAG/Workflow 入口

- 状态：已接受
- 日期：2026-08-20
- 决策者：项目维护者
- 修订说明：决策三中“搜索候选只有在人工纳入后才能新增 ProjectPaper”的 Phase 3 行为，已由
  [产品决策 0003](0003-phase-3-fixed-review-workflow.md) 修订为“arXiv 前 N 篇自动导入，不设置
  论文人工筛选”。决策一原定 Phase 4 实现永久删除和 GC，现由
  [ADR-0004](0004-demo-ready-core-v1-scope.md) 调整为不属于 Demo-ready Core v1；归档、Project-scoped
  和历史追溯不变量保持有效。

## 背景

Phase 1 已提供 Project、owner 个人文献库和 `ProjectPaper` 收录关系，但还没有修改、归档或永久删除入口。Phase 2 将增加 Conversation、Evidence 和 Citation，Phase 3 将增加 Review Run、候选和 Artifact；如果先实现物理删除，后续很容易破坏历史问答、引用和 Workflow 结果。

产品同时需要支持“询问整个项目”和“询问某篇论文”，以及从已有 Project 或全局入口启动综述 Workflow。需要在进入 Phase 2 前固定统一的业务边界，避免形成两套授权、检索和运行模型。

## 决策一：归档优先，永久删除延后

### Project

- 支持修改名称和描述；
- 支持归档与恢复；默认列表只显示 active Project；
- 归档后历史文献、Run、Conversation、Citation、Workflow 和 Artifact 保持只读可见，但不能创建上传、RAG 或 Workflow Run；
- 存在非终态 Run 时归档返回冲突，用户需先等待或取消；
- 永久删除推迟到未来生产化阶段，在完整引用图、备份和恢复策略明确后另行决策；删除 Project 不得
  连带删除 owner 个人文献库中的 Paper。Demo-ready Core v1 不提供该入口；

### Paper 与 ProjectPaper

- `ProjectPaper` 是收录关系，“移出项目”继续直接删除关系，不引入关系归档；
- owner 个人文献库中的 Paper 支持归档与恢复；归档后从默认个人库列表和新增收录选择器隐藏；
- Paper 归档不改变已有 ProjectPaper，也不破坏历史引用。若不希望它参与某个 Project 的新检索，用户应从该 Project 移除；
- Paper 的展示名或后续书目元数据可以修改，但 Paper 身份、PDF 内容和 Version 不通过编辑静默替换；
- Paper 永久删除推迟到未来生产化阶段，只允许在没有 ProjectPaper、Run、Evidence、Citation、
  Workflow 或 Artifact 引用时执行；Storage 回收必须使用可观察的异步 GC。Demo-ready Core v1
  继续只提供归档；

Phase 2 的第一个支撑切片实现 Project 修改/归档/恢复和 Paper 归档/恢复；永久删除不属于 Phase 2–4
的 Demo-ready Core v1。

## 决策二：RAG 始终以 Project 为业务边界

用户界面提供三种范围：

```text
询问整个 Project
询问 Project 内的一篇 Paper
询问 Project 内选中的多篇 Paper
```

三者共用一套后端模型：

- Conversation 必须归属一个 `project_id`；
- Conversation 保存默认 `scope_mode=project|selected_papers`；选中文献保存 Project 内可见的 Paper/Version 引用；
- 每个 `rag_answer` Run 启动时把实际可见的 `selected_version_ids` 固化到输入快照，后续移出、换版或归档不改变历史回答；
- Paper 级入口只是预填 `selected_papers`，不创建脱离 Project 的 Paper Chat API；
- 从个人文献库点击“询问”时，用户必须选择一个已收录该 Paper 的 Project，或先完成收录；
- Citation Validator 仍校验 Evidence 属于本次 Run 的 Project 和 Version 快照。

## 决策三：Workflow 始终归属 Project

- `ReviewRun` 必须持有 `project_id`；
- 已有 Project 页面提供“开始文献综述”；
- 全局“新建综述”是产品向导：先创建 Project，再用相同后端接口创建 Review Run；
- 创建普通 Project 不自动启动 Workflow，Project 也可以只用于文献管理或 RAG；
- Workflow 启动时固化初始 ProjectPaper/Version 范围，后续搜索得到的候选只有在人工纳入后才能新增 ProjectPaper；
- 已归档 Project 不能创建或恢复新的 Workflow 执行。

## 后果

正面影响：用户可以从 Project 或 Paper 进入 RAG，也可以从已有 Project 或“新建综述”进入 Workflow，但后端始终只有 Project-scoped 授权、Run 和引用模型。归档保留历史可追溯性，降低早期删除设计的风险。

代价：个人文献库中的 Paper 若仍被 active Project 收录，即使 Paper 本身归档，也仍可能参与该 Project 的新检索；UI 必须明确提示“归档个人库资产”和“移出项目”是两个动作。真正释放 Storage 需要等引用检查和 GC 完成后实现。

## 被否决的方案

- 独立的 owner-scoped Paper Chat：会复制 Conversation 授权和引用校验模型，首版不采用。
- 创建 Project 后自动启动 Workflow：会把文献管理/RAG Project 强制变成 Review Project，首版不采用。
- 直接级联物理删除 Project/Paper：会破坏未来 Evidence、Citation、Workflow 和 Artifact 历史，延后处理。
