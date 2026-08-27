import { describe, expect, it } from "vitest";

import {
  canInteractWithConversation,
  chatConversationPath,
  chatHomePath,
  chatPreselectionPath,
  initialChatScope,
  isConversationInProject,
} from "./projectWorkspace";

describe("Project workspace routes", () => {
  it("只生成 canonical Chat 首页与详情路径", () => {
    expect(chatHomePath("project-1")).toBe("/projects/project-1/chat");
    expect(chatConversationPath("project-1", "conversation-1")).toBe(
      "/projects/project-1/chat/conversation-1",
    );
    expect(chatPreselectionPath("project-1", ["paper 1", "paper 1", "paper-2"])).toBe(
      "/projects/project-1/chat?paper_id=paper+1&paper_id=paper-2",
    );
  });

  it("只接受当前 Project 中的 URL 论文预选并保持稳定顺序", () => {
    const search = new URLSearchParams(
      "paper_id=paper-2&paper_id=foreign&paper_id=paper-2&paper_id=paper-1",
    );

    expect(initialChatScope(search, ["paper-1", "paper-2"])).toEqual({
      paperIds: ["paper-2", "paper-1"],
    });
  });

  it("没有合法预选时默认整个 Project", () => {
    expect(initialChatScope(new URLSearchParams(), ["paper-1"])).toEqual({
      paperIds: [],
    });
    expect(
      initialChatScope(new URLSearchParams("paper_id=foreign"), ["paper-1"]),
    ).toEqual({ paperIds: [] });
  });

  it("Conversation 必须属于路由 Project", () => {
    expect(isConversationInProject({ project_id: "project-1" }, "project-1")).toBe(true);
    expect(isConversationInProject({ project_id: "project-2" }, "project-1")).toBe(false);
  });

  it("Project 与 Conversation 闭包确认前禁止交互", () => {
    const project = { project_id: "project-1" };
    const conversation = { project_id: "project-1" };

    expect(canInteractWithConversation(undefined, conversation, "project-1")).toBe(false);
    expect(canInteractWithConversation(project, undefined, "project-1")).toBe(false);
    expect(
      canInteractWithConversation(project, { project_id: "project-2" }, "project-1"),
    ).toBe(false);
    expect(canInteractWithConversation(project, conversation, "project-1")).toBe(true);
  });
});
