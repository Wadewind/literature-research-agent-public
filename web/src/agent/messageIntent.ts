export interface AgentMessageIntent {
  key: string;
  content: string;
  reviewOutputId: string;
  attachmentIds: string[];
}

export function ensureAgentMessageIntent(
  current: AgentMessageIntent | null,
  content: string,
  reviewOutputId: string,
  attachmentIds: string[],
  createKey: () => string,
): AgentMessageIntent {
  if (
    current?.content === content &&
    current.reviewOutputId === reviewOutputId &&
    current.attachmentIds.join("\0") === attachmentIds.join("\0")
  ) {
    return current;
  }
  return { key: createKey(), content, reviewOutputId, attachmentIds: [...attachmentIds] };
}
