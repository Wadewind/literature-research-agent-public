/** Run 状态判断测试：终态与可取消状态表。 */

import { describe, expect, it } from "vitest";

import { isCancellable, isTerminal, statusLabel } from "./runStatus";

describe("isTerminal", () => {
  it.each(["succeeded", "failed", "cancelled"])("终态：%s", (status) => {
    expect(isTerminal(status)).toBe(true);
  });

  it.each(["queued", "running", "retry_wait", "cancel_requested"])(
    "非终态：%s",
    (status) => {
      expect(isTerminal(status)).toBe(false);
    },
  );
});

describe("isCancellable", () => {
  it.each(["queued", "running", "retry_wait"])("可取消：%s", (status) => {
    expect(isCancellable(status)).toBe(true);
  });

  it.each(["succeeded", "failed", "cancelled", "cancel_requested"])(
    "不可取消：%s",
    (status) => {
      expect(isCancellable(status)).toBe(false);
    },
  );
});

describe("statusLabel", () => {
  it("已知状态有中文文案", () => {
    expect(statusLabel("succeeded")).toBe("成功");
    expect(statusLabel("cancel_requested")).toBe("取消中");
  });

  it("未知状态原样返回", () => {
    expect(statusLabel("some_future_status")).toBe("some_future_status");
  });
});
