/** SSE 事件流归并：按 sequence 去重排序，识别终态事件。

 * 后端 SSE 以 sequence 为游标（id 字段），断线重连通过 Last-Event-ID
 * 重放其后全部事件；因此归并必须幂等：重复或乱序到达的事件不产生重复。
 */

import type { RunEvent } from "../api/types";
import { isTerminalEventType, TERMINAL_EVENT_TYPES } from "./runStatus";

export interface EventStreamState {
  /** 已归并的事件，按 sequence 升序。 */
  events: RunEvent[];
  /** 已收到的最大 sequence（重连游标）。 */
  lastSequence: number;
  /** 是否已收到终态事件（应主动关闭流）。 */
  closed: boolean;
}

export function createEventStreamState(): EventStreamState {
  return { events: [], lastSequence: 0, closed: false };
}

/** 归并一条事件：重复 sequence 忽略，乱序插入保持升序。 */
export function applyEvent(state: EventStreamState, event: RunEvent): EventStreamState {
  if (state.events.some((e) => e.sequence === event.sequence)) {
    return state;
  }
  const events = [...state.events, event].sort((a, b) => a.sequence - b.sequence);
  return {
    events,
    lastSequence: Math.max(state.lastSequence, event.sequence),
    closed: state.closed || isTerminalEventType(event.event_type),
  };
}

/** 后端会发送的具名事件类型；EventSource 需要按类型逐个订阅。 */
export const KNOWN_EVENT_TYPES = [
  "run_created",
  "run_started",
  "run_requeued",
  "run_cancel_requested",
  "parse_started",
  "parse_completed",
  "normalize_completed",
  "result_committed",
  "indexing_started",
  "chunking_completed",
  "embedding_completed",
  "retrieval_started",
  "retrieval_completed",
  "model_generation_started",
  "model_generation_completed",
  "citation_validation_completed",
  ...TERMINAL_EVENT_TYPES,
] as const;
