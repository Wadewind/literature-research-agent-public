import { describe, expect, it } from "vitest";

import type { ReviewOutput } from "../api/types";
import {
  artifactContentUrl,
  dimensionLabel,
  evidenceFileUrl,
  matrixRows,
  moveOutlineSection,
  nextOutlineSection,
  outlineDraftDirty,
  outlineDraftIssues,
  outlineSections,
  sectionResult,
  visibleDimensionKeys,
  type OutlineSection,
} from "./reviewResults";

function output(payload: Record<string, unknown>): ReviewOutput {
  return {
    output_id: "output-1",
    review_run_id: "review-1",
    output_type: "outline",
    output_key: "outline",
    version: 1,
    schema_version: "outline.v1",
    payload,
    created_at: "2026-08-23T00:00:00Z",
  };
}

describe("Review 结构化结果投影", () => {
  it("把常见分析维度转换为用户可读标签", () => {
    expect(dimensionLabel("classical_methods")).toBe("经典方法");
    expect(dimensionLabel("experimental_setup")).toBe("实验设置");
    expect(dimensionLabel("custom_dimension")).toBe("custom dimension");
  });

  it("只接受可由表单编辑的 Outline 结构", () => {
    expect(outlineSections(output({ sections: [{ section_key: "methods", title: "方法", purpose: "比较", dimension_keys: ["reliability"] }] }))).toHaveLength(1);
    expect(outlineSections(output({ sections: [{ section_key: "methods", title: 1 }] }))).toEqual([]);
  });

  it("保留证据不足 Matrix 行并解析 Section Claim", () => {
    expect(matrixRows(output({ rows: [{ paper_id: "paper-1", dimension_key: "limits", status: "insufficient_evidence", finding: null, limitations: null, evidence_ids: [] }] }))[0]?.status).toBe("insufficient_evidence");
    const section = sectionResult(output({ section_key: "methods", title: "方法", status: "answered", summary: "摘要", claims: [{ text: "结论", evidence_ids: ["evidence-1"] }], terminology: [{ term: "Lease", definition: "有时限的执行所有权" }] }));
    expect(section?.claims[0]?.evidence_ids).toEqual(["evidence-1"]);
    expect(section?.terminology).toEqual([{ term: "Lease", definition: "有时限的执行所有权" }]);
    expect(sectionResult(output({ section_key: "methods", title: "方法", status: "answered", summary: "摘要", claims: [{ text: "结论", evidence_ids: ["evidence-1"] }], terminology: [{ term: "缺少定义" }] }))).toBeNull();
    expect(sectionResult(output({ section_key: "methods", title: "方法", status: "answered", summary: "摘要", claims: [{ text: "结论", evidence_ids: ["evidence-1"] }], terminology: [], extra: true }))).toBeNull();
    expect(sectionResult(output({ section_key: "methods", title: "方法", status: "insufficient_evidence", summary: "摘要", claims: [{ text: "不应存在", evidence_ids: ["evidence-1"] }], terminology: [] }))).toBeNull();
  });

  it("Evidence 与 Artifact 只拼接已有 Project-scoped content endpoint", () => {
    expect(evidenceFileUrl("project-1", "version-1", 7)).toBe("/api/v1/projects/project-1/paper-versions/version-1/file#page=7");
    expect(artifactContentUrl("project-1", "review-1", "artifact-1")).toBe("/api/v1/projects/project-1/reviews/review-1/artifacts/artifact-1/content");
  });
});

const validSections: OutlineSection[] = [
  { section_key: "methods", title: "方法", purpose: "比较方法", dimension_keys: ["reliability"] },
  { section_key: "limits", title: "限制", purpose: "讨论限制", dimension_keys: ["limitations"] },
];

describe("Outline 结构化编辑", () => {
  it("使用 Matrix 与当前 Outline 维度的稳定并集", () => {
    expect(visibleDimensionKeys(validSections, [{ paper_id: "paper-1", dimension_key: "cost", status: "extracted", finding: "低成本", limitations: null, evidence_ids: ["evidence-1"] }])).toEqual(["reliability", "limitations", "cost"]);
  });

  it("接受合法草稿并检测本地未保存修改", () => {
    expect(outlineDraftIssues(validSections, ["reliability", "limitations"])).toEqual([]);
    expect(outlineDraftDirty(validSections, validSections)).toBe(false);
    expect(outlineDraftDirty(validSections, updateFirst({ title: "新标题" }))).toBe(true);
  });

  it.each([
    [[], ["reliability", "limitations"], "大纲必须包含"],
    [[...validSections, ...Array.from({ length: 11 }, (_, index) => ({ ...validSections[0], section_key: `extra_${index}` }))], ["reliability", "limitations"], "大纲必须包含"],
    [updateFirst({ section_key: "Bad-Key" }), ["reliability", "limitations"], "snake_case"],
    [updateFirst({ section_key: "limits" }), ["reliability", "limitations"], "不得重复"],
    [updateFirst({ title: " " }), ["reliability", "limitations"], "标题"],
    [updateFirst({ purpose: "x".repeat(1001) }), ["reliability", "limitations"], "目标"],
    [updateFirst({ dimension_keys: [] }), ["reliability", "limitations"], "1–6"],
    [updateFirst({ dimension_keys: ["reliability", "reliability"] }), ["reliability", "limitations"], "1–6"],
    [updateFirst({ dimension_keys: ["unknown"] }), ["reliability", "limitations"], "1–6"],
  ])("拒绝越过 domain 边界的草稿", (sections, dimensions, message) => {
    expect(outlineDraftIssues(sections as OutlineSection[], dimensions as string[]).join("；")).toContain(message);
  });

  it("支持新增、删除由组件执行，以及纯函数上移下移", () => {
    expect(nextOutlineSection(validSections, ["reliability"]).section_key).toBe("section_3");
    expect(moveOutlineSection(validSections, 1, -1).map((section) => section.section_key)).toEqual(["limits", "methods"]);
    expect(moveOutlineSection(validSections, 0, -1)).toBe(validSections);
  });
});

function updateFirst(change: Partial<OutlineSection>): OutlineSection[] {
  return [{ ...validSections[0], ...change }, validSections[1]];
}
