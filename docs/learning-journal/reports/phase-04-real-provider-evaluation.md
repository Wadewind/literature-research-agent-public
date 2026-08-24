# Phase 4 真实 Provider 评测记录

## 本切片状态

2026-08-24 当前进程没有显式提供评测凭证。本切片未读取 `.env`、未探测 Key、未访问真实 arXiv、
Docling 模型下载或付费 Provider，因此没有“本次真实 Review 质量”数字。

可引用的历史实跑只有 Phase 2 于 2026-08-21 完成的最小 Smoke：Docling 2 个契约测试通过（CPU fallback，
首次缓存约 506 MiB）；OpenAI-compatible Embedding 返回 1 个 1024 维向量且 usage 非空；结构化 Chat
返回合法 `insufficient_evidence`，当前模型需显式关闭 JSON Schema、使用 `json_object` 后仍由本地
Pydantic/Citation Validator 校验。历史记录没有保留精确 token、latency、调用次数报告，不能补写或
冒充 Phase 4 固定真实评测，也不能证明 Review groundedness。

## 显式 opt-in

调用者必须在当前 shell 显式导出所需 `AGENT_*` 凭证和 endpoint；普通测试不加载 `.env`：

```bash
cd backend
AGENT_RUN_PROVIDER_TESTS=1 .venv/bin/pytest -q \
  tests/infrastructure/test_provider_smoke.py
AGENT_RUN_DOCLING_TESTS=1 .venv/bin/pytest -q \
  tests/infrastructure/test_docling_parser.py
```

若以后运行完整真实 Review 报告，必须新增而不是覆盖本记录，并保存 Provider/Model、Prompt/Profile
版本、日期、调用次数、prompt/completion token、逐阶段耗时、人工评分和失败样例；不得写入 Key、完整
Prompt、论文全文、模型完整响应或真实用户数据。真实结果允许波动，不进入普通 CI 阻断门。
