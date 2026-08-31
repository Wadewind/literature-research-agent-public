export const FIXED_REVIEW_STAGES = [
  { key: "validate_request", label: "确认请求" },
  { key: "formulate_search_strategy", label: "制定检索策略" },
  { key: "search_arxiv", label: "检索 arXiv" },
  { key: "review_sources", label: "筛选候选" },
  { key: "import_arxiv_papers", label: "导入论文" },
  { key: "wait_for_ingestion", label: "等待解析与索引" },
  { key: "build_evidence_matrix", label: "构建证据矩阵" },
  { key: "propose_outline", label: "生成大纲" },
  { key: "review_outline", label: "确认大纲" },
  { key: "draft_sections", label: "撰写章节" },
  { key: "validate_sections", label: "校验引用" },
  { key: "consistency_check", label: "检查一致性" },
  { key: "export_review", label: "导出综述" },
  { key: "finalize", label: "完成" },
] as const;

export type StageRailState = "completed" | "current" | "waiting-current" | "failed" | "waiting";

export interface StageRailItem {
  key: string;
  label: string;
  state: StageRailState;
}

export function stageLabel(stage: string): string {
  if (stage === "persist_outline") return "保存大纲";
  return FIXED_REVIEW_STAGES.find((item) => item.key === stage)?.label ?? stage;
}

export function reviewStageRail(currentStage: string, runStatus: string): StageRailItem[] {
  const currentIndex = FIXED_REVIEW_STAGES.findIndex((stage) => stage.key === currentStage);
  return FIXED_REVIEW_STAGES.map((stage, index) => {
    let state: StageRailState = "waiting";
    if (runStatus === "succeeded" || (currentIndex >= 0 && index < currentIndex)) {
      state = "completed";
    } else if (index === currentIndex) {
      if (runStatus === "failed" || runStatus === "cancelled") state = "failed";
      else if (runStatus === "waiting_input" || runStatus === "waiting_dependency") {
        state = "waiting-current";
      } else state = "current";
    }
    return { ...stage, state };
  });
}

export type SourceTone = "waiting" | "importing" | "ready" | "failed" | "muted";

export function sourcePresentation(status: string): { label: string; tone: SourceTone } {
  switch (status) {
    case "importing": return { label: "正在导入", tone: "importing" };
    case "ready": return { label: "可用于综述", tone: "ready" };
    case "failed": return { label: "导入失败", tone: "failed" };
    case "rejected": return { label: "未选用", tone: "muted" };
    default: return { label: "等待导入", tone: "waiting" };
  }
}
