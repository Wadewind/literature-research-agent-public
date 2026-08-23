/** Review 创建交互意图：失败重试复用同一 Idempotency-Key。 */

export interface ReviewIntent {
  researchQuestion: string;
  key: string;
}

export function ensureReviewIntent(
  current: ReviewIntent | null,
  researchQuestion: string,
  keyFactory: () => string,
): ReviewIntent {
  if (current?.researchQuestion === researchQuestion) return current;
  return { researchQuestion, key: keyFactory() };
}
