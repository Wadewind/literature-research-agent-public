import { describe, expect, it } from "vitest";

import { eligibleEvidenceMatrices } from "./matrixEligibility";

describe("Agent Evidence Matrix eligibility", () => {
  it("失败 Review 的 canonical Matrix 可选，没有 Matrix 的 Review 被排除", () => {
    const reviews = [
      {
        run_id: "failed-with-matrix",
        status: "failed",
        research_question: "保留聚合证据",
        evidence_matrix: {
          output_id: "output-1",
          version: 1,
          row_count: 20,
          valid_papers: 4,
          failed_papers: 6,
        },
      },
      {
        run_id: "failed-without-matrix",
        status: "failed",
        research_question: "没有聚合证据",
        evidence_matrix: null,
      },
    ];

    expect(eligibleEvidenceMatrices(reviews)).toEqual([reviews[0]]);
  });
});
