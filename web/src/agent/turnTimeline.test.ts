import { describe, expect, it } from "vitest";

import type { AgentMessage, RunEvent } from "../api/types";
import { groupMessagesByTurn, mergeRunEvents } from "./turnTimeline";

function message(
  messageId: string,
  turnRunId: string,
  sequence: number,
  role: AgentMessage["role"],
): AgentMessage {
  return {
    message_id: messageId,
    session_id: "session-1",
    sequence,
    role,
    content: messageId,
    turn_run_id: turnRunId,
    claim_set_id: null,
    created_at: `2026-08-30T00:00:0${sequence}Z`,
    claims: null,
    attachment_ids: [],
  };
}

function event(runId: string, sequence: number, eventId: string): RunEvent {
  return {
    event_id: eventId,
    event_version: "1.0",
    event_type: "agent_tool_started",
    run_id: runId,
    sequence,
    occurred_at: "2026-08-30T00:00:00Z",
    actor_type: "system",
    correlation_id: "test",
    payload: {},
  };
}

describe("Agent Turn 消息时间线", () => {
  it("按 turn 保留历史消息，并把没有消息的活动 turn 放到末尾", () => {
    const groups = groupMessagesByTurn([
      message("user-1", "run-1", 1, "user"),
      message("assistant-1", "run-1", 2, "assistant"),
      message("user-2", "run-2", 3, "user"),
    ], "run-3");

    expect(groups.map((group) => group.turnRunId)).toEqual(["run-1", "run-2", "run-3"]);
    expect(groups[0].messages.map((item) => item.message_id)).toEqual(["user-1", "assistant-1"]);
    expect(groups[1].messages.map((item) => item.message_id)).toEqual(["user-2"]);
    expect(groups[2].messages).toEqual([]);
  });

  it("合并历史 REST 与当前 SSE 事件并按 sequence 去重排序", () => {
    const merged = mergeRunEvents(
      [event("run-1", 1, "event-1"), event("run-1", 2, "event-old")],
      [event("run-1", 2, "event-new"), event("run-1", 3, "event-3")],
    );

    expect(merged.map((item) => item.event_id)).toEqual(["event-1", "event-new", "event-3"]);
  });
});
