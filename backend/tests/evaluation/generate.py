"""生成 Phase 2 RAG 评测用的合成英文 PDF 语料（确定性输出，可提交）。

运行方式：``uv run python tests/evaluation/generate.py``

产出（全部为完全合成的学术风格英文 PDF，不含任何真实论文内容）：

- ``corpus/gnn-survey.pdf``：消息传递图神经网络综述（植入 GraphWeave benchmark 等独有事实）；
- ``corpus/positional-encoding.pdf``：长上下文 Transformer 位置编码研究（植入 Helix-64 schedule）；
- ``corpus/gnn-molecular.pdf``：GNN 分子溶解度预测（植入 MolAnchor fingerprint / AquaSol-9，
  与 gnn-survey 主题相近，支撑跨篇综合题；含 Courier 排版表格）；
- ``corpus/rl-robotics.pdf``：四足机器人模型强化学习（植入 Zephyr-7 scheduler）。

生成机制与 ``tests/fixtures/pdfs/generate.py`` 相同：手写最小 PDF 对象结构
（Helvetica/Courier 标准字体、无压缩内容流），不引入新依赖。生成后脚本会用
pypdf 自检每篇页数与植入关键词所在页，保证 fixture 与 ``manifest.json`` 不漂移。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from pypdf import PdfReader

CORPUS_DIR = Path(__file__).parent / "corpus"

WRAP_WIDTH = 78
MAX_LINES_PER_PAGE = 44

# 一行排版文本：(字体, 内容)；字体为 "helv"（Helvetica 12）或 "mono"（Courier 10，用于表格）
Line = tuple[str, str]

# 每个语料文件需要自检的植入关键词：{页码（1 起）: [关键词, ...]}，与 manifest.json 保持一致
KEYWORDS: dict[str, dict[int, list[str]]] = {
    "gnn-survey.pdf": {
        2: ["GraphWeave benchmark suite", "nine synthetic tasks", "Weave-Depth protocol", "61.3%"],
        3: ["depth-saturation cliff"],
    },
    "positional-encoding.pdf": {
        2: ["Helix-64 rotary schedule", "131072 tokens", "perplexity drift below 2.1%"],
        3: ["Helix-64 (this work)", "2.1%", "4.8%"],
    },
    "gnn-molecular.pdf": {
        2: ["MolAnchor fingerprint", "AquaSol-9", "14237 curated molecules"],
        3: ["0.214 logS units", "14.1%", "improvement", "MolAnchor-GNN", "0.214"],
    },
    "rl-robotics.pdf": {
        2: ["TerraHound-4", "learned absolute positional", "256-step"],
        3: ["Zephyr-7 scheduler", "time by 38%", "14.2 hours"],
    },
}


def _escape(text: str) -> str:
    """转义 PDF 文本字符串中的特殊字符。"""
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _gap() -> tuple[str]:
    return ("gap",)


def _h(text: str) -> tuple[str, str]:
    return ("h", text)


def _p(text: str) -> tuple[str, str]:
    return ("p", text)


def _t(lines: list[str]) -> tuple[str, list[str]]:
    return ("t", lines)


def _compose(blocks: list[tuple]) -> list[Line]:
    """把段落/标题/表格块展开为排版行，并检查页容量。"""
    lines: list[Line] = []
    for block in blocks:
        kind = block[0]
        if kind == "gap":
            lines.append(("helv", ""))
        elif kind in ("h", "p"):
            lines.extend(("helv", line) for line in textwrap.wrap(block[1], WRAP_WIDTH))
        elif kind == "t":
            lines.extend(("mono", line) for line in block[1])
        else:
            msg = f"未知块类型: {kind}"
            raise ValueError(msg)
    if len(lines) > MAX_LINES_PER_PAGE:
        msg = f"单页行数超限: {len(lines)} > {MAX_LINES_PER_PAGE}"
        raise ValueError(msg)
    return lines


def _build_pdf(path: Path, pages: list[list[Line]]) -> None:
    """手写最小 PDF 对象结构，生成带可提取多行文本的多页 PDF（两字体）。

    与 ``tests/fixtures/pdfs/generate.py`` 同一生成机制，扩展为多行文本与双字体：
    对象 1/2 为 Catalog/Pages，3/4 为 Helvetica 与 Courier 字体，之后每页两个对象
    （Page + Content）。
    """
    objects: list[bytes] = []
    page_ids = [5 + i * 2 for i in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    for page_id, lines in zip(page_ids, pages, strict=True):
        ops = ["BT 72 720 Td 14 TL"]
        for font, text in lines:
            tag = "/F1 12 Tf" if font == "helv" else "/F2 10 Tf"
            ops.append(f"{tag} ({_escape(text)}) Tj T*")
        ops.append("ET")
        stream = " ".join(ops).encode()
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> "
            f"/Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_start = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))


# ---------------------------------------------------------------------------
# 语料内容（完全合成；人名、机构、数值、术语均为虚构）
# ---------------------------------------------------------------------------


def _gnn_survey() -> list[list[Line]]:
    """GraphWeave 综述：植入 GraphWeave benchmark 与 Weave-Depth 深度饱和事实。"""
    pages = [
        [
            _h("GraphWeave: A Synthetic Survey of Message-Passing Graph Neural Networks"),
            _gap(),
            _p("Ada Corvin, Mira Okafor, and Deniz Yilmaz"),
            _p(
                "Institute for Synthetic Graph Learning. All content is fictional and "
                "produced solely as a retrieval evaluation fixture."
            ),
            _gap(),
            _h("Abstract"),
            _p(
                "Message-passing graph neural networks are the dominant family of models "
                "for learning on relational data. This survey consolidates synthetic results "
                "reported across the GraphWeave research program, covering benchmark design, "
                "the message-passing design space, and the limits of deep architectures. We "
                "introduce the GraphWeave benchmark suite as a common yardstick, review "
                "aggregation and update functions, and analyze why depth saturates in "
                "practice. The survey closes with open problems in long-range dependency "
                "modelling and evaluation methodology."
            ),
            _gap(),
            _h("1 Introduction"),
            _p(
                "Graph-structured data appears in citation networks, molecules, traffic "
                "systems, and program analysis. Over the past decade, message-passing graph "
                "neural networks have become the default tool for such data, replacing "
                "hand-crafted kernel methods with learned neighbourhood aggregation."
            ),
            _p(
                "This survey is organized as follows. Section 2 introduces the GraphWeave "
                "benchmark suite. Section 3 reviews the message-passing design space. "
                "Section 4 analyzes depth-related failure modes. Section 5 lists open "
                "problems, and Section 6 concludes."
            ),
        ],
        [
            _h("2 The GraphWeave Benchmark"),
            _p(
                "The GraphWeave benchmark suite contains nine synthetic tasks spanning node "
                "classification, link prediction, and graph regression. Unlike collections "
                "assembled from real-world datasets, GraphWeave tasks are generated from "
                "controlled random graph processes, so difficulty can be varied along a "
                "single axis at a time. The suite is described here only to provide stable "
                "reference points for retrieval evaluation."
            ),
            _p(
                "The most informative probe in the suite is the Weave-Depth protocol, which "
                "re-trains the same architecture at increasing depths while holding "
                "parameter count constant. Under the Weave-Depth protocol, accuracy "
                "retention falls to 61.3% beyond twelve layers, even with residual "
                "connections and normalization. We refer to this behaviour throughout the "
                "survey as depth saturation."
            ),
            _h("3 The Message-Passing Design Space"),
            _p(
                "A message-passing layer is determined by three choices: the message "
                "function, the aggregation operator, and the update function. Sum "
                "aggregation preserves multiset information but couples the representation "
                "to node degree; mean aggregation is degree-invariant but lossy; "
                "attention-based aggregation learns weighting at the cost of additional "
                "parameters."
            ),
            _p(
                "Across GraphWeave tasks, sum aggregation with a two-layer MLP update is "
                "the most robust default. Learned edge features help only when the "
                "generator injects edge semantics, which confirms that design choices "
                "should follow the data-generating process rather than leaderboard "
                "fashion."
            ),
        ],
        [
            _h("4 Depth, Oversmoothing, and Saturation"),
            _p(
                "Stacking message-passing layers enlarges the receptive field but mixes "
                "node representations until they become indistinguishable, a phenomenon "
                "known as oversmoothing. The Weave-Depth protocol separates oversmoothing "
                "from optimization difficulty by holding capacity fixed; the resulting "
                "curves show that the depth-saturation cliff appears abruptly between "
                "twelve and sixteen layers on seven of nine GraphWeave tasks."
            ),
            _p(
                "Practical mitigations include residual connections, jumping knowledge "
                "style readouts, and virtual nodes. None of these remove the cliff "
                "entirely; they shift it by roughly two layers. Consequently, most "
                "successful applications surveyed here use between two and six "
                "message-passing layers."
            ),
            _h("5 Open Problems"),
            _p(
                "Three open problems recur across the surveyed literature. First, "
                "long-range dependencies require depth that current architectures cannot "
                "sustain. Second, evaluation protocols rarely separate expressivity from "
                "optimization. Third, synthetic benchmarks like GraphWeave remain the only "
                "way to obtain controlled difficulty axes, yet they are underused."
            ),
        ],
        [
            _h("6 Conclusions"),
            _p(
                "Message-passing graph neural networks are a mature but bounded technology. "
                "The GraphWeave benchmark suite provides controlled evidence that depth "
                "saturates quickly, that aggregation choice should follow the "
                "data-generating process, and that shallow, well-tuned models remain "
                "competitive. Future work should target long-range structure without "
                "relying on deeper message passing."
            ),
            _gap(),
            _h("References"),
            _p(
                "[1] A. Corvin and M. Okafor. The GraphWeave benchmark suite: controlled "
                "tasks for graph learning. Journal of Synthetic Graph Learning, 2026. "
                "Fictional reference."
            ),
            _p(
                "[2] D. Yilmaz. Depth saturation in message-passing networks. Synthetic "
                "Workshop on Graph Limits, 2025. Fictional reference."
            ),
            _p(
                "[3] R. Feld and S. Okonkwo. Aggregation operators revisited. Transactions "
                "on Synthetic Learning, 2024. Fictional reference."
            ),
        ],
    ]
    return [_compose(page) for page in pages]


def _positional_encoding() -> list[list[Line]]:
    """位置编码研究：植入 Helix-64 rotary schedule 事实，含结果表格。"""
    pages = [
        [
            _h(
                "Rotary and Learned Positional Encodings for Long-Context Transformers: "
                "A Synthetic Study"
            ),
            _gap(),
            _p("Priya Nair, Tomasz Wojcik, and Hana Lindqvist"),
            _p(
                "Synthetic Sequence Modelling Group. All content is fictional and produced "
                "solely as a retrieval evaluation fixture."
            ),
            _gap(),
            _h("Abstract"),
            _p(
                "Positional encodings determine how far a Transformer can extrapolate "
                "beyond its training context. This synthetic study compares learned "
                "absolute embeddings, sinusoidal encodings, and rotary variants on a "
                "controlled next-token prediction task with procedurally generated "
                "sequences. We introduce the Helix-64 rotary schedule and show that it "
                "extends stable extrapolation to 131072 tokens while keeping perplexity "
                "drift below 2.1% at the extrapolation frontier."
            ),
            _gap(),
            _h("1 Introduction"),
            _p(
                "Transformers are permutation-invariant unless positional information is "
                "injected. The choice of positional encoding therefore controls both "
                "in-distribution behaviour and length generalization, and it has become "
                "one of the most consequential design decisions in long-context "
                "modelling."
            ),
            _p(
                "This study is organized as follows. Section 2 reviews positional "
                "encoding families. Section 3 defines the Helix-64 schedule. Section 4 "
                "describes the experimental protocol, Section 5 reports results, and "
                "Sections 6 and 7 discuss limitations and conclusions."
            ),
        ],
        [
            _h("2 Families of Positional Encodings"),
            _p(
                "Learned absolute embeddings assign a trainable vector to each position; "
                "they are simple and effective within the training window but cannot "
                "represent positions never seen during training. Sinusoidal encodings "
                "replace learned vectors with fixed trigonometric functions, offering "
                "unbounded range with weak extrapolation. Rotary encodings apply "
                "position-dependent rotations to query and key vectors, making attention "
                "scores depend on relative offsets."
            ),
            _p(
                "A recurring limitation of standard rotary encodings is that "
                "high-frequency components wrap around quickly, so attention logits "
                "become noisy once sequences exceed the training length. Frequency "
                "scaling and interpolation mitigate this but distort short-range "
                "behaviour."
            ),
            _h("3 The Helix-64 Schedule"),
            _p(
                "The Helix-64 rotary schedule assigns rotation frequencies geometrically "
                "across 64 bands and anneals the highest band during training so that no "
                "band completes a full revolution within the training window. In our "
                "synthetic protocol, Helix-64 extends stable extrapolation to 131072 "
                "tokens, four times the training length of 32768 tokens, while keeping "
                "perplexity drift below 2.1% at the extrapolation frontier."
            ),
            _p(
                "The schedule adds no parameters and no inference-time cost relative to "
                "standard rotary encodings. Its only hyperparameter is the annealing "
                "horizon, which we fix at the final ten percent of training steps "
                "throughout this study."
            ),
        ],
        [
            _h("4 Experimental Protocol"),
            _p(
                "Training sequences are generated by a synthetic Markov grammar with "
                "controllable dependency range, so the required context length is known "
                "exactly. Models are 12-layer Transformers with hidden size 768, trained "
                "at context length 32768 and evaluated at lengths up to 131072. Each "
                "configuration is run with three seeds, and we report the median."
            ),
            _h("5 Results"),
            _t(
                [
                    "Table 1: Perplexity drift beyond the training length (synthetic).",
                    "Encoding                Drift at 4x     Drift at 8x",
                    "Learned absolute        fails           fails",
                    "Sinusoidal              18.7%           41.2%",
                    "Standard rotary         6.9%            22.5%",
                    "Helix-64 (this work)    2.1%            4.8%",
                ]
            ),
            _gap(),
            _p(
                "Helix-64 is the only encoding whose drift stays in single digits at eight "
                "times the training length. Learned absolute embeddings cannot be "
                "evaluated beyond the training window and are marked as failing by "
                "construction."
            ),
        ],
        [
            _h("6 Limitations"),
            _p(
                "Our sequences are generated by a Markov grammar, so the results measure "
                "length generalization under controlled dependencies rather than natural "
                "language. The annealing horizon was not tuned per encoding, which may "
                "understate competing methods. We also do not study interaction with "
                "attention sinks or sliding-window attention."
            ),
            _h("7 Conclusions"),
            _p(
                "Positional encoding choice dominates long-context extrapolation. The "
                "Helix-64 rotary schedule extends stable extrapolation to 131072 tokens "
                "with perplexity drift below 2.1% in our synthetic setting, at no "
                "additional cost. Future work should test the schedule on natural corpora "
                "and combine it with memory-augmented attention."
            ),
            _gap(),
            _h("References"),
            _p(
                "[1] P. Nair and H. Lindqvist. Frequency annealing in rotary encodings. "
                "Journal of Synthetic Sequence Modelling, 2026. Fictional reference."
            ),
            _p(
                "[2] T. Wojcik. Controlled benchmarks for length generalization. Synthetic "
                "Workshop on Long Contexts, 2025. Fictional reference."
            ),
            _p(
                "[3] E. Duarte. A taxonomy of positional information. Transactions on "
                "Synthetic Learning, 2024. Fictional reference."
            ),
        ],
    ]
    return [_compose(page) for page in pages]


def _gnn_molecular() -> list[list[Line]]:
    """GNN 分子溶解度预测：植入 MolAnchor / AquaSol-9 事实，含结果表格。"""
    pages = [
        [
            _h(
                "Message-Passing Graph Neural Networks for Molecular Solubility "
                "Prediction: A Synthetic Study"
            ),
            _gap(),
            _p("Lucia Ferrero, Ibrahim Haddad, and Wenjun Zhao"),
            _p(
                "Synthetic Molecular Informatics Lab. All content is fictional and "
                "produced solely as a retrieval evaluation fixture."
            ),
            _gap(),
            _h("Abstract"),
            _p(
                "Aqueous solubility is a key determinant of drug-likeness, and graph "
                "neural networks are natural candidates for predicting it from molecular "
                "structure. This synthetic study introduces the MolAnchor fingerprint and "
                "the AquaSol-9 dataset, and evaluates shallow message-passing "
                "architectures for solubility regression. Our MolAnchor-GNN achieves a "
                "mean absolute error of 0.214 logS units on the AquaSol-9 benchmark."
            ),
            _gap(),
            _h("1 Introduction"),
            _p(
                "Predicting aqueous solubility from molecular graphs is a standard "
                "benchmark task in molecular machine learning. Molecules map naturally "
                "onto graphs, with atoms as nodes and bonds as edges, which makes "
                "message-passing graph neural networks a default modelling choice."
            ),
            _p(
                "This study is organized as follows. Section 2 introduces the AquaSol-9 "
                "dataset and the MolAnchor fingerprint. Section 3 describes the model "
                "architecture, Section 4 reports results, and Sections 5 and 6 discuss "
                "limitations and conclusions."
            ),
        ],
        [
            _h("2 The AquaSol-9 Dataset and the MolAnchor Fingerprint"),
            _p(
                "The AquaSol-9 dataset contains 14237 curated molecules with synthetic "
                "solubility annotations in logS units, generated by a latent property "
                "model that mixes fragment contributions with mild non-linear "
                "interactions. The curation protocol removes salts, duplicates, and "
                "structures heavier than 900 daltons. Because the generating process is "
                "known, AquaSol-9 provides a controlled testbed for representation "
                "choices."
            ),
            _p(
                "The MolAnchor fingerprint augments each atom with a learned anchor "
                "embedding that summarizes its ring membership and hydrogen-bonding "
                "profile within two hops. Unlike circular fingerprints with fixed radius, "
                "MolAnchor anchors are trained jointly with the downstream network, so "
                "the effective radius adapts to the property being predicted."
            ),
            _h("3 Model Architecture"),
            _p(
                "The MolAnchor-GNN stacks four message-passing layers with sum "
                "aggregation and a jumping-knowledge readout. The depth of four layers "
                "was chosen deliberately: prior survey evidence on controlled graph "
                "benchmarks indicates that message-passing accuracy saturates quickly "
                "beyond twelve layers, so we keep the architecture shallow and invest "
                "capacity in the fingerprint instead. A two-layer MLP head predicts logS "
                "from the pooled graph representation."
            ),
        ],
        [
            _h("4 Results"),
            _p(
                "Table 1 reports test error on AquaSol-9 under a scaffold split. The "
                "MolAnchor-GNN achieves a mean absolute error of 0.214 logS units, a "
                "14.1% relative improvement over the strongest baseline. Ablations show "
                "that removing anchor embeddings accounts for most of the gap, while "
                "adding two more message-passing layers degrades performance, consistent "
                "with the depth-saturation behaviour reported in graph-learning "
                "surveys."
            ),
            _t(
                [
                    "Table 1: Solubility prediction error on AquaSol-9 (scaffold split).",
                    "Model                   MAE (logS)      RMSE (logS)",
                    "MolAnchor-GNN           0.214           0.402",
                    "Baseline GCN            0.249           0.468",
                    "Baseline MPNN           0.261           0.487",
                ]
            ),
            _gap(),
            _p(
                "Error analysis shows the largest residuals on molecules with long "
                "aliphatic chains, where the two-hop anchor radius cannot capture global "
                "hydrophobicity. Extending anchors to three hops recovers part of the "
                "error at a nine percent training-time cost."
            ),
        ],
        [
            _h("5 Limitations"),
            _p(
                "AquaSol-9 is synthetic, so absolute errors do not transfer to "
                "experimental solubility databases. The scaffold split is the only split "
                "we study, and we do not compare against three-dimensional "
                "conformer-based models. MolAnchor anchors require two-hop neighbourhood "
                "materialization, which is memory-heavy for dense graphs."
            ),
            _h("6 Conclusions"),
            _p(
                "Shallow message-passing networks with learned anchor fingerprints are "
                "strong solubility predictors in controlled settings. On AquaSol-9, the "
                "four-layer MolAnchor-GNN reaches 0.214 MAE, improving 14.1% over the "
                "strongest baseline while avoiding the depth-saturation failure mode "
                "documented in graph-learning surveys. Future work will extend anchors "
                "to longer ranges and evaluate on public solubility benchmarks."
            ),
            _gap(),
            _h("References"),
            _p(
                "[1] L. Ferrero and W. Zhao. Anchor embeddings for molecular graphs. "
                "Journal of Synthetic Molecular Informatics, 2026. Fictional reference."
            ),
            _p(
                "[2] I. Haddad. Controlled solubility benchmarks. Synthetic Workshop on "
                "Molecular Property Prediction, 2025. Fictional reference."
            ),
            _p(
                "[3] A. Corvin and M. Okafor. Depth limits of message passing. Journal of "
                "Synthetic Graph Learning, 2025. Fictional reference."
            ),
        ],
    ]
    return [_compose(page) for page in pages]


def _rl_robotics() -> list[list[Line]]:
    """四足机器人强化学习：植入 Zephyr-7 scheduler 与 TerraHound-4 事实。"""
    pages = [
        [
            _h("Model-Based Reinforcement Learning for Quadruped Locomotion: A Synthetic Study"),
            _gap(),
            _p("Kofi Mensah, Yuki Tanabe, and Petra Novak"),
            _p(
                "Synthetic Embodied Control Group. All content is fictional and produced "
                "solely as a retrieval evaluation fixture."
            ),
            _gap(),
            _h("Abstract"),
            _p(
                "Quadruped robots must adapt learned locomotion policies to hardware that "
                "differs from simulation. This synthetic study presents a model-based "
                "reinforcement learning pipeline for the TerraHound-4 platform, centred "
                "on the Zephyr-7 adaptation scheduler. Zephyr-7 reduces sim-to-real "
                "adaptation time by 38% relative to a fixed-schedule baseline while "
                "retaining 96% of asymptotic reward."
            ),
            _gap(),
            _h("1 Introduction"),
            _p(
                "Legged locomotion is a convenient testbed for model-based reinforcement "
                "learning because dynamics are continuous, contact-rich, and partially "
                "observable. Simulation provides cheap experience, but the sim-to-real "
                "gap means that adaptation after deployment dominates total training "
                "cost."
            ),
            _p(
                "This study is organized as follows. Section 2 describes the "
                "TerraHound-4 platform and policy architecture. Section 3 covers "
                "world-model training. Section 4 introduces the Zephyr-7 scheduler, "
                "Section 5 reports locomotion results, and Sections 6 and 7 discuss "
                "failure modes and conclusions."
            ),
        ],
        [
            _h("2 The TerraHound-4 Platform and Policy Architecture"),
            _p(
                "TerraHound-4 is a synthetic twelve-actuator quadruped with a nominal "
                "mass of 18 kg and a top speed of 3.2 m/s. Its simulator exposes "
                "randomized friction, actuator lag, and payload parameters; the physical "
                "platform is assumed to differ from the simulator along exactly these "
                "axes, which makes the sim-to-real gap controllable in our protocol."
            ),
            _p(
                "The policy is a six-layer Transformer encoder with learned absolute "
                "positional embeddings over 256-step proprioceptive windows, followed by "
                "a linear action head. We deliberately use learned positional embeddings "
                "rather than rotary variants because the window length is fixed at "
                "deployment, so long-context extrapolation is unnecessary and the learned "
                "table is the simpler option."
            ),
            _h("3 World-Model Training"),
            _p(
                "The world model predicts next-window proprioception and foot contacts "
                "from action-conditioned history. It is trained on 40 million simulated "
                "steps with randomized dynamics, then frozen. Planning uses "
                "short-horizon shooting with the learned model rather than learned value "
                "expansion, which we found more stable under contact discontinuities."
            ),
        ],
        [
            _h("4 The Zephyr-7 Adaptation Scheduler"),
            _p(
                "Adaptation after deployment alternates between collecting real rollouts "
                "and fine-tuning the world model and policy. The Zephyr-7 scheduler "
                "allocates these phases adaptively: it triggers a new collection phase "
                "only when model disagreement on the current replay buffer exceeds a "
                "calibrated threshold, and it lengthens fine-tuning phases geometrically "
                "as disagreement decays. Compared with a fixed alternating schedule, "
                "Zephyr-7 reduces sim-to-real adaptation time by 38% on TerraHound-4, "
                "from a median of 14.2 hours to 8.8 hours of onboard compute."
            ),
            _p(
                "The scheduler adds two hyperparameters, the disagreement threshold and "
                "the growth factor, and no additional models. Ablation with a "
                "disagreement-triggered but constant fine-tuning length recovers only "
                "half of the saving, indicating that phase lengthening contributes as "
                "much as triggering."
            ),
            _h("5 Locomotion Results"),
            _p(
                "After adaptation, policies retain 96% of their asymptotic simulated "
                "reward on flat terrain, 91% on randomized gravel, and 84% on wet "
                "low-friction surfaces. Emergency-stop latency after payload shifts of "
                "3 kg remains within 120 ms for all Zephyr-7 runs, matching the "
                "fixed-schedule baseline."
            ),
        ],
        [
            _h("6 Failure Modes"),
            _p(
                "Three failure modes dominate. First, disagreement calibration drifts "
                "when actuator lag exceeds the randomized range, causing "
                "under-collection. Second, the geometric phase lengthening occasionally "
                "locks in a suboptimal schedule after early lucky rollouts. Third, the "
                "learned positional table prevents window-length changes after "
                "deployment, so any control-frequency change requires retraining the "
                "policy head."
            ),
            _h("7 Conclusions"),
            _p(
                "Adaptive scheduling of real-world adaptation is the largest single lever "
                "in our sim-to-real budget. The Zephyr-7 scheduler cuts adaptation time "
                "by 38% on TerraHound-4 without extra models, while a fixed-window "
                "Transformer policy with learned positional embeddings suffices when "
                "deployment context length is constant. Future work targets online "
                "recalibration of disagreement thresholds."
            ),
            _gap(),
            _h("References"),
            _p(
                "[1] K. Mensah and P. Novak. Adaptive schedules for sim-to-real "
                "adaptation. Journal of Synthetic Embodied Control, 2026. Fictional "
                "reference."
            ),
            _p(
                "[2] Y. Tanabe. Disagreement-triggered data collection. Synthetic "
                "Workshop on Robot Learning, 2025. Fictional reference."
            ),
            _p(
                "[3] R. Alvarez. World models for contact-rich locomotion. Transactions "
                "on Synthetic Learning, 2024. Fictional reference."
            ),
        ],
    ]
    return [_compose(page) for page in pages]


def _self_check(path: Path, filename: str) -> None:
    """生成后用 pypdf 自检：植入关键词必须出现在声明的页码上。"""
    reader = PdfReader(path)
    page_texts = [page.extract_text() or "" for page in reader.pages]
    for page_no, keywords in KEYWORDS[filename].items():
        text = page_texts[page_no - 1]
        for keyword in keywords:
            if keyword not in text:
                msg = f"{filename} 第 {page_no} 页缺少植入关键词: {keyword!r}"
                raise AssertionError(msg)


def main() -> None:
    """重新生成全部语料 PDF 并自检（确定性输出）。"""
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "gnn-survey.pdf": _gnn_survey(),
        "positional-encoding.pdf": _positional_encoding(),
        "gnn-molecular.pdf": _gnn_molecular(),
        "rl-robotics.pdf": _rl_robotics(),
    }
    for filename, pages in outputs.items():
        path = CORPUS_DIR / filename
        _build_pdf(path, pages)
        _self_check(path, filename)
        print(f"generated {path} ({len(pages)} pages)")


if __name__ == "__main__":
    main()
