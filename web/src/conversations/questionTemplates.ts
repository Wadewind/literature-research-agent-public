/** 文献问答首页的固定问题模板与一次性草稿交接。 */

export const CHAT_QUESTION_DRAFT_MAX_LENGTH = 4000;

export const CHAT_QUESTION_TEMPLATES = [
  {
    id: "methods",
    label: "方法结构",
    code: "METHOD",
    question: "当前范围内的文献采用了哪些核心方法？",
  },
  {
    id: "experiments",
    label: "实验对照",
    code: "EVIDENCE",
    question: "这些研究的实验设置与评价指标有何差异？",
  },
  {
    id: "limits",
    label: "结论边界",
    code: "BOUNDARY",
    question: "现有文献支持哪些主要结论，还存在哪些局限？",
  },
] as const;

export type ChatQuestionTemplateId = (typeof CHAT_QUESTION_TEMPLATES)[number]["id"];

export interface QuestionDraftHandoff {
  questionDraft: string;
}

export function questionTemplateById(id: string | null | undefined) {
  return CHAT_QUESTION_TEMPLATES.find((template) => template.id === id);
}

export function questionDraftFromHandoff(
  templateId: string | null | undefined,
  navigationState: unknown,
): string {
  const template = questionTemplateById(templateId);
  if (template) return template.question;
  if (
    navigationState === null ||
    typeof navigationState !== "object" ||
    !("questionDraft" in navigationState)
  ) {
    return "";
  }
  const draft = (navigationState as { questionDraft?: unknown }).questionDraft;
  if (typeof draft !== "string") return "";
  const normalized = draft.trim();
  if (!normalized || normalized.length > CHAT_QUESTION_DRAFT_MAX_LENGTH) return "";
  return normalized;
}
