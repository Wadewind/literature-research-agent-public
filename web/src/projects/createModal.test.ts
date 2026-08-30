import { describe, expect, it } from "vitest";

import { shouldAutoOpenCreateModal } from "./createModal";

describe("shouldAutoOpenCreateModal", () => {
  it("查询进行中不自动打开，避免刷新时 Modal 闪现", () => {
    expect(shouldAutoOpenCreateModal(false, 0, true)).toBe(false);
  });

  it("查询返回且项目为空时自动打开一次", () => {
    expect(shouldAutoOpenCreateModal(false, 0, false)).toBe(true);
  });

  it("已有项目时不自动打开", () => {
    expect(shouldAutoOpenCreateModal(false, 2, false)).toBe(false);
  });

  it("自动打开过一次后不再重复打开", () => {
    expect(shouldAutoOpenCreateModal(true, 0, false)).toBe(false);
  });
});
