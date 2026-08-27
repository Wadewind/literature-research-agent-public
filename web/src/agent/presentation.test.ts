import { describe, expect, it } from "vitest";

import {
  agentEventLabel,
  canSendAgentMessage,
  isSkillProfileLocked,
  isSkillSelectionSelected,
  isSessionInProject,
  projectIndexLabel,
} from "./presentation";

describe("Agent UI 投影", () => {
  it("只投影允许的业务事件，不回显未知 payload", () => {
    expect(agentEventLabel("agent_tool_started")).toBe("正在使用研究工具");
    expect(agentEventLabel("agent_runtime_bound")).toBe("研究环境已就绪");
    expect(agentEventLabel("raw_model_delta")).toBeNull();
  });

  it("活动 Turn、空消息或未选择 Matrix 时禁止发送", () => {
    expect(canSendAgentMessage("问题", "output-1", null, false)).toBe(true);
    expect(canSendAgentMessage("问题", "output-1", "run-1", false)).toBe(false);
    expect(canSendAgentMessage(" ", "output-1", null, false)).toBe(false);
    expect(canSendAgentMessage("问题", "", null, false)).toBe(false);
    expect(canSendAgentMessage("问题", "output-1", null, true)).toBe(false);
  });

  it("首条产品消息后锁定 Skill，并使用 Project Index 数量文案", () => {
    expect(isSkillProfileLocked(0)).toBe(false);
    expect(isSkillProfileLocked(1)).toBe(true);
    expect(projectIndexLabel(3, "project")).toBe("当前 Project · 3 篇已索引文献");
    expect(projectIndexLabel(2, "turn")).toBe("本轮索引快照 · 2 篇文献");
    expect(projectIndexLabel(undefined, "project")).toBe("正在读取 Project 索引…");
  });

  it("Session 必须属于路由 Project，Skill 身份包含 source", () => {
    expect(isSessionInProject({ project_id: "project-1" }, "project-1")).toBe(true);
    expect(isSessionInProject({ project_id: "project-2" }, "project-1")).toBe(false);
    const selection = { source: "platform", skill_id: "synthesis", version: 1 };
    expect(isSkillSelectionSelected(selection, selection)).toBe(true);
    expect(
      isSkillSelectionSelected(selection, { ...selection, source: "owner" }),
    ).toBe(false);
  });
});
