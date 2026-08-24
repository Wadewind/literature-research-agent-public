# Phase 2 RAG 评测资产

本目录存放 Phase 2（有引用的 RAG 文献问答）的固定评测语料与问题集，用于后续切片的
Retrieval/Citation 评测。所有内容完全合成，不含任何真实论文、真实数据或版权内容，可提交进仓库。

## 目录结构

```text
tests/evaluation/
├─ corpus/                  # 4 篇合成英文 PDF（提交进仓库）
│  ├─ gnn-survey.pdf            # 消息传递 GNN 综述（4 页）
│  ├─ positional-encoding.pdf   # 长上下文位置编码研究（4 页，含表格）
│  ├─ gnn-molecular.pdf         # GNN 分子溶解度预测（4 页，含表格）
│  └─ rl-robotics.pdf           # 四足机器人模型强化学习（4 页）
├─ generate.py              # 确定性再生成脚本
├─ manifest.json            # 固定问题集（14 题）+ 语料映射与植入事实声明
├─ metrics.py               # 指标纯函数与汇总数据结构
├─ test_metrics.py          # 指标边界单元测试
├─ run_retrieval_eval.py    # 切片 6 的检索参数校准入口
├─ run_phase2_eval.py       # 切片 10 的完整确定性管线评测入口
├─ review_manifest.json     # Phase 4 的 3 个固定 Review 研究问题与 100% 结构门
├─ review_metrics.py        # Review 结构门纯汇总逻辑
├─ run_phase4_review_eval.py # 编排正式测试证据的离线 Review 工程门
└─ README.md
```

一致性由 `tests/infrastructure/test_evaluation_fixtures.py` 保证：用 pypdf 解析每篇 PDF，
断言页数、植入关键词与章节标题出现在 manifest 声明的页码上。不调用 Docling、不调用模型。

## 语料设计

检索语料是英文论文，FTS 使用 PostgreSQL `english` 配置，因此评测语料与问题集全部为英文，
避免跨语言带来的分词干扰（2026-08-20 定稿，见阶段 Spec）。

生成机制复用 Phase 1 的 `tests/fixtures/pdfs/generate.py`：手写最小 PDF 对象结构
（Helvetica/Courier 标准字体、无压缩内容流），不引入新依赖。重复运行输出字节一致：

```bash
cd backend && uv run python tests/evaluation/generate.py
```

脚本生成后会用 pypdf 自检植入关键词所在页，自检失败则生成失败。

每篇 PDF 含标题、虚构作者、摘要、编号章节、段落与虚构参考文献；
`positional-encoding.pdf` 与 `gnn-molecular.pdf` 各含一个 Courier 排版的表格。

每篇植入**独有的事实性陈述**（虚构术语与数值），使答案来源在语料中唯一确定：

| 语料 ID | 植入事实 | 位置 |
|---|---|---|
| `gnn-survey` | GraphWeave benchmark suite 含 nine synthetic tasks；Weave-Depth protocol 下超过 12 层 accuracy retention 降至 61.3%；depth-saturation cliff | p2 §2、p3 §4 |
| `positional-encoding` | Helix-64 rotary schedule 稳定外推至 131072 tokens、frontier perplexity drift < 2.1%；Table 1（4x drift 2.1%） | p2 §3、p3 §5 |
| `gnn-molecular` | AquaSol-9 含 14237 curated molecules；MolAnchor fingerprint；MolAnchor-GNN MAE 0.214 logS、相对最强 baseline 提升 14.1%（Table 1） | p2 §2、p3 §4 |
| `rl-robotics` | TerraHound-4 平台；policy 用 learned absolute positional embeddings（256-step 窗口）；Zephyr-7 scheduler 将 sim-to-real 适配时间减少 38%（14.2h → 8.8h） | p2 §2、p3 §4 |

`gnn-survey` 与 `gnn-molecular` 主题相近（后者明确引用前者的深度饱和结论），
`positional-encoding` 与 `rl-robotics` 通过位置编码选择形成较弱关联，支撑跨篇综合题。

## 问题集（manifest.json）

`manifest.json` 自描述：`corpus` 段把稳定语料 ID 映射到 PDF 文件名、标题、页数和植入事实
（关键词 + 页码 + 章节）；`questions` 段为 14 道固定问题，字段：

- `id`：稳定问题 ID；
- `category`：`single_paper_fact` / `cross_paper_synthesis` / `unanswerable` / `scope_boundary`；
- `question`：英文问题；
- `scope`：`{"mode": "project"}` 或 `{"mode": "selected_papers", "papers": [<语料 ID>, ...]}`；
- `expected`：`{"answer_status": "answered", "must_cite": [{"paper", "pages", "sections"}]}`
  或 `{"answer_status": "insufficient_evidence"}`；
- `notes`：期望答案要点与设计意图（中文）。

分类统计：单篇事实型 5 题、跨篇综合型 3 题（must_cite 恰好两篇）、明确无答案型 3 题、
范围边界型 3 题（selected_papers 排除答案所在 paper，期望 `insufficient_evidence`）。

## 如何运行完整评测

```bash
cd backend
.venv/bin/python tests/evaluation/run_phase2_eval.py \
  --json-output /tmp/phase-02-evaluation.json
```

Runner 使用一次性 `pgvector/pgvector:pg18` 数据库，并复用正式的导入服务、
`IngestionExecutor`、`IndexingExecutor`、`Retriever`、`RagAnswerExecutor` 和
Citation Validator：

1. 创建一个专用评测 Project，把 4 篇语料 PDF 经正常上传/解析/索引链路导入，
   等待 ChunkSet ready；
2. 建立语料 ID（如 `gnn-survey`）到实际 `paper_id` / `version_id` 的映射；
3. 逐题创建 Conversation（按 `scope` 设置 project / selected_papers 模式）并提交问题，
   等待 `rag_answer` Run 终态；
4. 对照 `expected` 计算指标并输出非敏感 JSON 报告：
   - **Retrieval Recall@K**：期望 paper/页面对应的 Chunk 是否进入 Top-K 候选；
   - **Citation validity**：引用是否通过 Citation Validator（结构/范围合法）；
   - **Citation completeness**：`must_cite` 声明的 paper 是否都被实际引用；
   - `unanswerable` / `scope_boundary` 题以 `answer_status == insufficient_evidence` 为通过。

2026-08-21 使用本地 `pypdf 6.16.1`、Fake Embedding、Fake Chat 和默认参数
`512/64、Top-K 20、per-paper 8、budget 3000` 实跑：answered 题 Retrieval
Recall 为 8/8、must-cite 条目为 11/11，Citation completeness 11/11，Validator
14/14，selected scope 边界 3/3；回答状态总匹配 8/14，其中 answered 8/8、
insufficient 0/6。后一个结果是 Fake Chat 的能力边界：只要存在任意 Evidence 就返回
answered，不能判断语义证据是否充分，不能解释成真实模型质量。完整说明见
`docs/learning-journal/modules/rag-evaluation.md`。

Fixture 改动后必须重跑 `generate.py` 与
`pytest tests/infrastructure/test_evaluation_fixtures.py`，保证 PDF 与 manifest 不漂移。

## Phase 4 Review 工程门

`review_manifest.json` 复用同一份 Phase 2 合成语料，固定 3 个研究问题。每个问题和 3–4 个语料 ID
实际传入生产 Matrix/Citation/Section Validator 与确定性 Review 导出器，计算 ready/failed Source、
Matrix 行、Citation scope、导出引用映射和伪造 Evidence 拒绝的事实计数。Runner 另行执行 12 个固定
Application/LangGraph/PostgreSQL 回归节点；这些节点证明 partial source、feedback interrupt/resume、
持久化、终态和重放，但不会被折算为质量比例：

```bash
cd backend
.venv/bin/python tests/evaluation/run_phase4_review_eval.py \
  --json-output /tmp/phase-04-review-evaluation.json
```

门限来自 2026-08-24 首次成功实跑，而非预填：场景 3/3、Citation 接受/跨 Run 拒绝 6/6、导出
citation mapping 6/6、Evidence 跨 Project/Run 拒绝 18/18、伪造 Evidence 拒绝 3/3，五项均须
`1.0`；固定回归必须严格为 12/12。Owner 隔离由 Project-scoped Application/PG 回归证明，不冒充
领域对象自身能够证明的百分比。这里的 100% 只证明领域结构闭包，不证明 Claim 被 Evidence 语义蕴含，也不报告
Groundedness、Coverage 或 Redundancy。详细结果与首轮环境失败见
`docs/learning-journal/reports/phase-04-evaluation-baseline.md`。

完整 Review 性能不由这个质量 runner 推导。另用
`tests/performance/run_phase4_review_baseline.py` 驱动正式 API、PostgreSQL、Valkey/ARQ Worker、两轮自动
HITL 和 Artifact 读取；实际低敏结果与前三次失败样例见 Phase 4 性能报告。

## 已知限制

- PDF 为手写最小结构（标准 14 字体、无压缩流），段落按固定宽度折行，长单词可能在
  行尾断开；关键词自检已规避折行拆分，但新增植入事实时应选择短关键词；
- 表格为 Courier 文本排版而非真实表格结构，Docling/Element 层的表格识别行为需在
  索引切片实跑时确认；
- 语料规模小（4 篇），只覆盖管线正确性，不代表真实检索质量。
- 完整评测使用正式 `PypdfDocumentParser` 而非生产 Fake Parser：Fake Parser 有意忽略
  输入字节并返回固定中文文档，无法验证 manifest 的论文、章节和页码事实；真实 Docling
  由独立 opt-in Smoke 覆盖。
- 不报告 Groundedness、性能或 Provider 质量；未实际运行的指标不会写入报告。
