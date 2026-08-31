import { describe, expect, it } from "vitest";

import {
  agentEventLabel,
  agentTurnFailureSummary,
  canSendAgentMessage,
  canInteractWithAgentSession,
  isSkillProfileLocked,
  isSkillSelectionSelected,
  isSessionInProject,
  projectIndexLabel,
  visibleSkillVersions,
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

  it("研究方法默认只展示最新版，已有会话继续展示所选旧版本", () => {
    const catalog = [
      { source: "platform", skill_id: "synthesis", version: 1, name: "方法" },
      { source: "platform", skill_id: "synthesis", version: 2, name: "方法" },
      { source: "owner", skill_id: "comparison", version: 1, name: "对比" },
    ];

    expect(visibleSkillVersions(catalog, [])).toEqual([catalog[1], catalog[2]]);
    expect(
      visibleSkillVersions(catalog, [
        { source: "platform", skill_id: "synthesis", version: 1 },
      ]),
    ).toEqual([catalog[0], catalog[2]]);
  });

  it("Project 与 Session 闭包确认前禁止交互", () => {
    const project = { project_id: "project-1" };
    const session = { project_id: "project-1" };

    expect(canInteractWithAgentSession(undefined, session, "project-1")).toBe(false);
    expect(canInteractWithAgentSession(project, undefined, "project-1")).toBe(false);
    expect(
      canInteractWithAgentSession(project, { project_id: "project-2" }, "project-1"),
    ).toBe(false);
    expect(canInteractWithAgentSession(project, session, "project-1")).toBe(true);
  });

  it("将 Sandbox metadata 失败投影为稳定安全说明且不回显 SDK 明细", () => {
    const summary = agentTurnFailureSummary({
      event_type: "run_failed",
      payload: {
        error: {
          type: "SandboxApiException",
          message: "secret endpoint and INVALID_METADATA_LABEL provider detail",
        },
      },
    });

    expect(summary.code).toBe("runtime_sandbox_metadata_invalid");
    expect(summary.title).toBe("研究环境未能启动");
    expect(summary.detail).toContain("未进入模型或工具执行");
    expect(JSON.stringify(summary)).not.toContain("secret endpoint");
    expect(JSON.stringify(summary)).not.toContain("provider detail");
  });

  it("将回答引用格式失败投影为可恢复说明且不回显模型输出", () => {
    const summary = agentTurnFailureSummary({
      event_type: "run_failed",
      payload: {
        error: {
          type: "runtime_output_invalid",
          message: "模型原始回答包含 [evidence:…]",
        },
      },
    });

    expect(summary.code).toBe("runtime_output_invalid");
    expect(summary.title).toBe("回答引用格式未通过校验");
    expect(summary.detail).toContain("未写入会话");
    expect(JSON.stringify(summary)).not.toContain("模型原始回答");
    expect(JSON.stringify(summary)).not.toContain("[evidence:…]");
  });

  it("未知失败只返回通用稳定错误码，不回显事件 payload", () => {
    const summary = agentTurnFailureSummary({
      event_type: "run_failed",
      payload: { error: { type: "UnexpectedThing", message: "raw private detail" } },
    });

    expect(summary.code).toBe("agent_turn_failed");
    expect(summary.title).toBe("本轮研究未能完成");
    expect(JSON.stringify(summary)).not.toContain("raw private detail");
  });
});
