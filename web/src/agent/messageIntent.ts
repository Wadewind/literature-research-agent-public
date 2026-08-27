export interface AgentMessageIntent {
  key: string;
  content: string;
  reviewOutputId: string;
}

export function ensureAgentMessageIntent(
  current: AgentMessageIntent | null,
  content: string,
  reviewOutputId: string,
  createKey: () => string,
): AgentMessageIntent {
  if (
    current?.content === content &&
    current.reviewOutputId === reviewOutputId
  ) {
    return current;
  }
  return { key: createKey(), content, reviewOutputId };
}
