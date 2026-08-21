/** Project 内提问范围选择状态测试。 */

import { describe, expect, it } from "vitest";

import {
  createScopeSelection,
  scopeRequest,
  toggleScopePaper,
} from "./scopeSelection";

describe("scopeSelection", () => {
  it("整个 Project 入口不携带 paper_ids", () => {
    expect(scopeRequest(createScopeSelection())).toEqual({
      scope_mode: "project",
      paper_ids: undefined,
    });
  });

  it("单篇入口与多选入口都使用 selected_papers", () => {
    const one = toggleScopePaper(createScopeSelection(), "paper-1");
    const many = toggleScopePaper(one, "paper-2");

    expect(scopeRequest(one)).toEqual({
      scope_mode: "selected_papers",
      paper_ids: ["paper-1"],
    });
    expect(scopeRequest(many)).toEqual({
      scope_mode: "selected_papers",
      paper_ids: ["paper-1", "paper-2"],
    });
  });

  it("再次选择同一论文会取消，并恢复整个 Project 范围", () => {
    const selected = toggleScopePaper(createScopeSelection(), "paper-1");
    const empty = toggleScopePaper(selected, "paper-1");
    expect(scopeRequest(empty).scope_mode).toBe("project");
  });
});
