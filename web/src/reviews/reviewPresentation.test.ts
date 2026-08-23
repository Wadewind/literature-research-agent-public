import { describe, expect, it } from "vitest";

import {
  FIXED_REVIEW_STAGES,
  reviewStageRail,
  sourcePresentation,
} from "./reviewPresentation";

describe("reviewStageRail", () => {
  it("按固定 Workflow 顺序标记完成、当前与等待阶段", () => {
    const rail = reviewStageRail("build_evidence_matrix", "running");

    expect(rail.map((item) => item.key)).toEqual(FIXED_REVIEW_STAGES.map((item) => item.key));
    expect(rail.find((item) => item.key === "wait_for_ingestion")?.state).toBe("completed");
    expect(rail.find((item) => item.key === "build_evidence_matrix")?.state).toBe("current");
    expect(rail.find((item) => item.key === "propose_outline")?.state).toBe("waiting");
  });

  it("等待输入与失败状态只作用于真实 current_stage", () => {
    expect(reviewStageRail("review_outline", "waiting_input")[7].state).toBe("waiting-current");
    expect(reviewStageRail("draft_sections", "failed")[8].state).toBe("failed");
  });

  it("成功终态把所有固定阶段标记为完成", () => {
    expect(reviewStageRail("finalize", "succeeded").every((item) => item.state === "completed"))
      .toBe(true);
  });
});

describe("sourcePresentation", () => {
  it.each([
    ["discovered", "等待导入", "waiting"],
    ["importing", "正在导入", "importing"],
    ["ready", "可用于综述", "ready"],
    ["failed", "导入失败", "failed"],
  ] as const)("映射 %s 来源状态", (status, label, tone) => {
    expect(sourcePresentation(status)).toEqual({ label, tone });
  });
});
