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

## 如何在后续切片运行评测

评测 harness 属后续切片（切片 10 验收复盘实跑）。预期流程：

1. 创建一个专用评测 Project，把 4 篇语料 PDF 经正常上传/解析/索引链路导入，
   等待 ChunkSet ready；
2. 建立语料 ID（如 `gnn-survey`）到实际 `paper_id` / `version_id` 的映射；
3. 逐题创建 Conversation（按 `scope` 设置 project / selected_papers 模式）并提交问题，
   等待 `rag_answer` Run 终态；
4. 对照 `expected` 计算指标，只报告实跑结果：
   - **Retrieval Recall@K**：期望 paper/页面对应的 Chunk 是否进入 Top-K 候选；
   - **Citation validity**：引用是否通过 Citation Validator（结构/范围合法）；
   - **Citation completeness**：`must_cite` 声明的 paper 是否都被实际引用；
   - **人工 Groundedness**：少量样本人工核对 Claim 是否被 Evidence 语义支持；
   - `unanswerable` / `scope_boundary` 题以 `answer_status == insufficient_evidence` 为通过。

Fixture 改动后必须重跑 `generate.py` 与
`pytest tests/infrastructure/test_evaluation_fixtures.py`，保证 PDF 与 manifest 不漂移。

## 已知限制

- PDF 为手写最小结构（标准 14 字体、无压缩流），段落按固定宽度折行，长单词可能在
  行尾断开；关键词自检已规避折行拆分，但新增植入事实时应选择短关键词；
- 表格为 Courier 文本排版而非真实表格结构，Docling/Element 层的表格识别行为需在
  索引切片实跑时确认；
- 语料规模小（4 篇），只覆盖管线正确性，不代表真实检索质量。
