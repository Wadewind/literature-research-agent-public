interface MatrixAvailability {
  evidence_matrix: object | null;
}

export function eligibleEvidenceMatrices<T extends MatrixAvailability>(
  reviews: readonly T[],
): Array<T & { evidence_matrix: NonNullable<T["evidence_matrix"]> }> {
  return reviews.filter(
    (review): review is T & { evidence_matrix: NonNullable<T["evidence_matrix"]> } =>
      review.evidence_matrix !== null,
  );
}
