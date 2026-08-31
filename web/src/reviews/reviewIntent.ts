/** Review 创建交互意图：失败重试复用同一 Idempotency-Key。 */

export interface ReviewIntent {
  researchQuestion: string;
  paperVersionIds: string[];
  autoSearchCandidates: boolean;
  key: string;
}

export function ensureReviewIntent(
  current: ReviewIntent | null,
  researchQuestion: string,
  paperVersionIds: string[],
  autoSearchCandidates: boolean,
  keyFactory: () => string,
): ReviewIntent {
  const sameVersions =
    current?.paperVersionIds.length === paperVersionIds.length &&
    current.paperVersionIds.every((value, index) => value === paperVersionIds[index]);
  if (
    current?.researchQuestion === researchQuestion &&
    current.autoSearchCandidates === autoSearchCandidates &&
    sameVersions
  ) {
    return current;
  }
  return {
    researchQuestion,
    paperVersionIds: [...paperVersionIds],
    autoSearchCandidates,
    key: keyFactory(),
  };
}
