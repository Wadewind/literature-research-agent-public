/** SSE 事件归并 reducer 测试：去重、乱序、终态收束、重连重放。 */

import { describe, expect, it } from "vitest";

import type { RunEvent } from "../api/types";
import { applyEvent, createEventStreamState } from "./eventStore";

function makeEvent(sequence: number, eventType = "parse_started"): RunEvent {
  return {
    event_id: `e-${sequence}`,
    event_version: "1",
    event_type: eventType,
    run_id: "run-1",
    sequence,
    occurred_at: "2026-08-20T00:00:00Z",
    actor_type: "worker",
    correlation_id: "corr-1",
    payload: {},
  };
}

describe("applyEvent", () => {
  it("按 sequence 升序归并乱序到达的事件", () => {
    let state = createEventStreamState();
    state = applyEvent(state, makeEvent(3));
    state = applyEvent(state, makeEvent(1));
    state = applyEvent(state, makeEvent(2));

    expect(state.events.map((e) => e.sequence)).toEqual([1, 2, 3]);
    expect(state.lastSequence).toBe(3);
    expect(state.closed).toBe(false);
  });

  it("重复 sequence 被忽略（断线重放不产生重复）", () => {
    let state = createEventStreamState();
    state = applyEvent(state, makeEvent(1));
    state = applyEvent(state, makeEvent(2));
    // 模拟 Last-Event-ID 重连后重放到重复事件
    state = applyEvent(state, makeEvent(2));
    state = applyEvent(state, makeEvent(1));

    expect(state.events.map((e) => e.sequence)).toEqual([1, 2]);
  });

  it("收到终态事件后标记收束", () => {
    let state = createEventStreamState();
    state = applyEvent(state, makeEvent(1, "run_created"));
    expect(state.closed).toBe(false);
    state = applyEvent(state, makeEvent(2, "run_completed"));

    expect(state.closed).toBe(true);
  });

  it("终态后仍忽略重复事件且保持收束", () => {
    let state = createEventStreamState();
    state = applyEvent(state, makeEvent(5, "run_failed"));
    state = applyEvent(state, makeEvent(5, "run_failed"));

    expect(state.events).toHaveLength(1);
    expect(state.closed).toBe(true);
  });

  it.each(["run_completed", "run_failed", "run_cancelled"])(
    "终态事件类型 %s 触发收束",
    (eventType) => {
      const state = applyEvent(createEventStreamState(), makeEvent(1, eventType));
      expect(state.closed).toBe(true);
    },
  );
});
