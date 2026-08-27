import { describe, expect, it } from "vitest";

import type { Conversation } from "../api/types";
import { conversationScopeLabel } from "./ConversationRail";

function conversationScope(
  scopeMode: Conversation["scope_mode"],
  scopePapers?: Conversation["scope_papers"],
): Conversation {
  return { scope_mode: scopeMode, scope_papers: scopePapers } as Conversation;
}

describe("Conversation scope 标签", () => {
  it("区分整个 Project、明确数量与未展开的 selected scope", () => {
    expect(conversationScopeLabel(conversationScope("project", []))).toBe("整个项目");
    expect(conversationScopeLabel(conversationScope("selected_papers", [
      { paper_id: "paper-1", version_id: "version-1" },
      { paper_id: "paper-2", version_id: "version-2" },
    ]))).toBe("2 篇文献");
    expect(conversationScopeLabel(conversationScope("selected_papers", []))).toBe("所选文献");
    expect(conversationScopeLabel(conversationScope("selected_papers"))).toBe("所选文献");
  });
});
