# Literature Review Agent System

面向学习和简历展示的文献综述 Agent 系统。Phase 0 的技术学习与隔离验证已经完成，项目正在准备正式功能开发。

## 仓库布局

```text
agent-service/
├─ backend/   # Python、FastAPI、Worker、数据库迁移与后端测试
├─ web/       # React/Vite 前端
├─ docs/      # 总体规范、阶段 Spec、学习笔记与决策
└─ deploy/    # 后续加入的本地部署配置
```

当前后端仍是最小入口。API、Worker、数据库和前端工程将在对应垂直切片需要时逐步加入。

```bash
cd backend
uv sync
uv run agent-service
```

阶段目标和学习顺序见 [`docs/learning-journal/phases/phase-00-project-baseline.md`](docs/learning-journal/phases/phase-00-project-baseline.md)。
