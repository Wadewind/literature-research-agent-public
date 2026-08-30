import { describe, expect, it } from "vitest";

import { shouldShowCreatePanel } from "./createPanelVisibility";

describe("shouldShowCreatePanel", () => {
  it("查询进行中不自动展开，避免刷新时面板闪现", () => {
    expect(shouldShowCreatePanel(false, 0, true)).toBe(false);
  });

  it("查询返回且项目为空时常驻展示创建面板", () => {
    expect(shouldShowCreatePanel(false, 0, false)).toBe(true);
  });

  it("已有项目时默认收起创建面板", () => {
    expect(shouldShowCreatePanel(false, 1, false)).toBe(false);
    expect(shouldShowCreatePanel(false, 3, false)).toBe(false);
  });

  it("点击幽灵卡后立即展开，即使查询仍在进行", () => {
    expect(shouldShowCreatePanel(true, 2, false)).toBe(true);
    expect(shouldShowCreatePanel(true, 0, true)).toBe(true);
  });
});
