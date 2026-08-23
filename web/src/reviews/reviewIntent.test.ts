import { describe, expect, it } from "vitest";

import { ensureReviewIntent } from "./reviewIntent";

describe("ensureReviewIntent", () => {
  it("相同研究问题失败重试时复用幂等键", () => {
    const first = ensureReviewIntent(null, "如何可靠恢复 Workflow？", () => "review-1");
    const retry = ensureReviewIntent(first, "如何可靠恢复 Workflow？", () => "review-2");

    expect(retry).toBe(first);
  });

  it("研究问题改变时生成新的幂等键", () => {
    const first = ensureReviewIntent(null, "问题 A", () => "review-1");
    const changed = ensureReviewIntent(first, "问题 B", () => "review-2");

    expect(changed).toEqual({ researchQuestion: "问题 B", key: "review-2" });
  });
});
