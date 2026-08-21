/** 一次提问的幂等意图：相同内容重试复用 Key，内容变化生成新 Key。 */

export interface MessageIntent {
  content: string;
  key: string;
}

export function ensureMessageIntent(
  current: MessageIntent | null,
  content: string,
  keyFactory: () => string,
): MessageIntent {
  if (current?.content === content) return current;
  return { content, key: keyFactory() };
}
