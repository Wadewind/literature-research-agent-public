/** 文献问答推荐问题与一次性草稿交接测试。 */

import { describe, expect, it } from "vitest";

import {
  CHAT_QUESTION_TEMPLATES,
  questionDraftFromHandoff,
  questionTemplateById,
} from "./questionTemplates";

describe("questionTemplates", () => {
  it("提供三个范围中立且互不重复的研究问题", () => {
    expect(CHAT_QUESTION_TEMPLATES).toHaveLength(3);
    expect(new Set(CHAT_QUESTION_TEMPLATES.map((template) => template.id)).size).toBe(3);
    expect(new Set(CHAT_QUESTION_TEMPLATES.map((template) => template.question)).size).toBe(3);
    expect(CHAT_QUESTION_TEMPLATES.every((template) => template.question.includes("文献") || template.question.includes("研究"))).toBe(true);
  });

  it("只解析固定模板 ID", () => {
    expect(questionTemplateById("methods")?.question).toContain("核心方法");
    expect(questionTemplateById("unknown")).toBeUndefined();
    expect(questionTemplateById(null)).toBeUndefined();
  });

  it("固定模板优先于 route state，自定义草稿必须是 4000 字以内的非空字符串", () => {
    expect(questionDraftFromHandoff("methods", { questionDraft: "自定义问题" })).toContain("核心方法");
    expect(questionDraftFromHandoff(null, { questionDraft: "  自定义问题  " })).toBe("自定义问题");
    expect(questionDraftFromHandoff(null, { questionDraft: " ".repeat(5) })).toBe("");
    expect(questionDraftFromHandoff(null, { questionDraft: "a".repeat(4001) })).toBe("");
    expect(questionDraftFromHandoff(null, { questionDraft: 123 })).toBe("");
  });
});
