import type { AgentMessage, RunEvent } from "../api/types";

export interface AgentTurnMessageGroup {
  turnRunId: string;
  messages: AgentMessage[];
}

/** 按首次出现顺序把产品消息归入各自 Turn，并保留尚未生成消息的活动 Turn。 */
export function groupMessagesByTurn(
  messages: AgentMessage[],
  activeTurnRunId?: string | null,
): AgentTurnMessageGroup[] {
  const groups: AgentTurnMessageGroup[] = [];
  const byRunId = new Map<string, AgentTurnMessageGroup>();
  for (const message of messages) {
    let group = byRunId.get(message.turn_run_id);
    if (!group) {
      group = { turnRunId: message.turn_run_id, messages: [] };
      byRunId.set(message.turn_run_id, group);
      groups.push(group);
    }
    group.messages.push(message);
  }
  if (activeTurnRunId && !byRunId.has(activeTurnRunId)) {
    groups.push({ turnRunId: activeTurnRunId, messages: [] });
  }
  return groups;
}

/** SSE 覆盖同 sequence 的 REST 快照，随后恢复稳定顺序。 */
export function mergeRunEvents(persisted: RunEvent[], live: RunEvent[]): RunEvent[] {
  const bySequence = new Map<number, RunEvent>();
  for (const event of persisted) bySequence.set(event.sequence, event);
  for (const event of live) bySequence.set(event.sequence, event);
  return [...bySequence.values()].sort((left, right) => left.sequence - right.sequence);
}
