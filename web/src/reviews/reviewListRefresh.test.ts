import { describe, expect, it } from "vitest";

import type { ReviewListItem } from "../api/types";
import {
  ACTIVE_REVIEW_REFETCH_INTERVAL_MS,
  reviewListRefetchInterval,
} from "./reviewListRefresh";

function review(status: string): ReviewListItem {
  return {
    run_id: `review-${status}`,
    status,
    research_question: "问题",
    current_stage: "formulate_search_strategy",
    created_at: "2026-08-23T00:00:00Z",
    updated_at: "2026-08-23T00:00:00Z",
  };
}

describe("reviewListRefetchInterval", () => {
  it("未加载或空列表不轮询", () => {
    expect(reviewListRefetchInterval(undefined)).toBe(false);
    expect(reviewListRefetchInterval([])).toBe(false);
  });

  it("全部终态时不轮询", () => {
    expect(
      reviewListRefetchInterval([
        review("succeeded"),
        review("failed"),
        review("cancelled"),
      ]),
    ).toBe(false);
  });

  it.each(["queued", "running", "retry_wait", "waiting_input", "waiting_dependency", "cancel_requested"])(
    "存在非终态 %s 时启用低频刷新",
    (status) => {
      expect(reviewListRefetchInterval([review("succeeded"), review(status)])).toBe(
        ACTIVE_REVIEW_REFETCH_INTERVAL_MS,
      );
    },
  );
});
