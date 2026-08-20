/** 以 SSE 实时跟随 Run 事件的 Hook。

 * 原生 EventSource 断线重连时自动携带 Last-Event-ID（已收到的最大
 * sequence），后端据此重放历史，不重不漏。收到终态事件后主动关闭流，
 * 避免对已收束的 Run 无限重连。
 */

import { useEffect, useReducer } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { RunEvent } from "../api/types";
import {
  applyEvent,
  createEventStreamState,
  KNOWN_EVENT_TYPES,
  type EventStreamState,
} from "./eventStore";
import { isTerminalEventType } from "./runStatus";

type Action = { type: "event"; event: RunEvent } | { type: "reset" };

function reduce(state: EventStreamState, action: Action): EventStreamState {
  if (action.type === "reset") return createEventStreamState();
  return applyEvent(state, action.event);
}

export function useRunEvents(runId: string | undefined): EventStreamState {
  const [state, dispatch] = useReducer(reduce, undefined, createEventStreamState);
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!runId) return;
    dispatch({ type: "reset" });
    const source = new EventSource(`/api/v1/runs/${runId}/events/stream`);

    const onEvent = (message: MessageEvent<string>) => {
      const event = JSON.parse(message.data) as RunEvent;
      dispatch({ type: "event", event });
      if (isTerminalEventType(event.event_type)) {
        // 终态事件与终态同事务提交：收到即收束，主动关闭并刷新 Run 状态
        source.close();
        void queryClient.invalidateQueries({ queryKey: ["run", runId] });
      }
    };
    for (const type of KNOWN_EVENT_TYPES) {
      source.addEventListener(type, onEvent as EventListener);
    }
    source.onerror = () => {
      // EventSource 会自动带 Last-Event-ID 重连；这里只刷新 Run 状态兜底
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
    };

    return () => source.close();
  }, [runId, queryClient]);

  return state;
}
