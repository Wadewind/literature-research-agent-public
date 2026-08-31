"""平台固定 Research Skill Catalog；内容变更必须新增版本。"""

from literature_agent.domain.skill_configuration import create_platform_skill

EVIDENCE_LED_SYNTHESIS_V1 = create_platform_skill(
    skill_id="evidence-led-synthesis",
    version=1,
    name="evidence-led-synthesis",
    description=(
        "使用 Project Paper Chunk Index 与 Review Evidence Matrix 进行证据优先的研究综合；"
        "适用于比较论文、提炼结论和生成带 Evidence 标记的回答。"
    ),
    instructions="""# Evidence-led synthesis

1. 先使用 `read_review_evidence_matrix` 理解主题、研究设计与证据缺口。
2. 对关键或冲突结论使用 `search_project_chunks` 回到授权的论文 Chunk 核验。
3. 区分论文直接报告的事实、跨论文综合推断与证据不足，不虚构作者、年份、DOI 或实验结果。
4. 每个非空论述独占一行，并严格以 `[evidence:<id>[,<id>...]]` 结尾。
5. 若当前授权证据不足，只输出“当前授权上下文证据不足。”。
""",
    required_tool_names=("read_review_evidence_matrix", "search_project_chunks"),
)

EVIDENCE_LED_SYNTHESIS = create_platform_skill(
    skill_id="evidence-led-synthesis",
    version=2,
    name="evidence-led-synthesis",
    description=(
        "使用 Project Paper Chunk Index 与 Review Evidence Matrix 进行证据优先的研究综合；"
        "适用于比较论文、提炼结论和生成带 Evidence 标记的回答。"
    ),
    instructions="""# Evidence-led synthesis

1. 先使用 `read_review_evidence_matrix` 理解主题、研究设计与证据缺口。
2. 对关键或冲突结论使用 `search_project_chunks` 回到授权的论文 Chunk 核验。
3. 区分论文直接报告的事实、跨论文综合推断与证据不足，不虚构作者、年份、DOI 或实验结果。
4. 每个有项目证据的非空论述独占一行，并以工具返回的真实 Evidence ID 构成的行尾标记结束。
   例如真实 ID 为 e-123 时写 `[evidence:e-123]`；多个真实 ID 使用英文逗号分隔。
5. 不得用省略号、尖括号或格式示例充当 Evidence ID；解释引用格式时只称为“Evidence 标记”。
   不要输出标记占位符。
6. 若当前授权证据不足，只输出“当前授权上下文证据不足。”。
""",
    required_tool_names=("read_review_evidence_matrix", "search_project_chunks"),
)

PLATFORM_SKILLS = (EVIDENCE_LED_SYNTHESIS_V1, EVIDENCE_LED_SYNTHESIS)
