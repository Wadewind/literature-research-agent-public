/** Outline HumanInput 的本地交互意图；服务端事实仍由 API 保存。 */

export interface HumanInputIntent {
  signature: string;
  key: string;
}

export interface HumanInputSubmission {
  requestId: string;
  requestVersion: number;
  outlineOutputId: string;
  action: "approve" | "edit" | "feedback";
  payload: Record<string, unknown>;
}

export function humanInputSignature(submission: HumanInputSubmission): string {
  return JSON.stringify(submission);
}

export function ensureHumanInputIntent(
  current: HumanInputIntent | null,
  submission: HumanInputSubmission,
  keyFactory: () => string,
): HumanInputIntent {
  const signature = humanInputSignature(submission);
  if (current?.signature === signature) return current;
  return { signature, key: keyFactory() };
}

export function isHumanInputConflictStatus(status: number | undefined): boolean {
  return status === 409;
}
