import { describe, expect, it } from "vitest";

import { ensureHumanInputIntent, isHumanInputConflictStatus } from "./reviewHumanInput";

const submission = {
  requestId: "request-1",
  requestVersion: 2,
  outlineOutputId: "outline-2",
  action: "approve" as const,
  payload: {},
};

describe("ensureHumanInputIntent", () => {
  it("相同版本与语义失败重试时复用 Idempotency-Key", () => {
    const first = ensureHumanInputIntent(null, submission, () => "input-1");
    const retry = ensureHumanInputIntent(first, submission, () => "input-2");

    expect(retry).toBe(first);
  });

  it("Request 版本或结构化编辑改变时生成新意图", () => {
    const first = ensureHumanInputIntent(null, submission, () => "input-1");
    const changed = ensureHumanInputIntent(
      first,
      {
        ...submission,
        requestVersion: 3,
        action: "edit",
        payload: {
          sections: [
            {
              section_key: "methods",
              title: "方法",
              purpose: "比较方法",
              dimension_keys: ["reliability"],
            },
          ],
        },
      },
      () => "input-2",
    );

    expect(changed).toEqual({ signature: expect.any(String), key: "input-2" });
  });

  it("只把 409 识别为需要立即刷新服务端版本的冲突", () => {
    expect(isHumanInputConflictStatus(409)).toBe(true);
    expect(isHumanInputConflictStatus(500)).toBe(false);
    expect(isHumanInputConflictStatus(undefined)).toBe(false);
  });
});
