import type { ReviewListItem } from "../api/types";
import { isTerminal } from "../runs/runStatus";

export const ACTIVE_REVIEW_REFETCH_INTERVAL_MS = 5_000;

/** 仅在列表含活动 Review 时启用低频兜底刷新。 */
export function reviewListRefetchInterval(
  reviews: ReviewListItem[] | undefined,
): number | false {
  return reviews?.some((review) => !isTerminal(review.status))
    ? ACTIVE_REVIEW_REFETCH_INTERVAL_MS
    : false;
}
