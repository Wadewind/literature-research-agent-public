import { describe, expect, it } from "vitest";

import { ensureAgentMessageIntent } from "./messageIntent";

describe("ensureAgentMessageIntent", () => {
  it("同一消息和 Matrix 的失败重试复用幂等键", () => {
    const first = ensureAgentMessageIntent(null, "比较方法", "output-1", () => "key-1");
    expect(ensureAgentMessageIntent(first, "比较方法", "output-1", () => "key-2")).toBe(
      first,
    );
  });

  it("消息或 Matrix 变化时生成新意图", () => {
    const first = ensureAgentMessageIntent(null, "比较方法", "output-1", () => "key-1");
    expect(ensureAgentMessageIntent(first, "追问限制", "output-1", () => "key-2").key).toBe(
      "key-2",
    );
    expect(ensureAgentMessageIntent(first, "比较方法", "output-2", () => "key-3").key).toBe(
      "key-3",
    );
  });
});
