import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import ConversationMessage from "./ConversationMessage";

describe("ConversationMessage", () => {
  it("用统一语义呈现用户气泡且不显示头像或角色标题", () => {
    const html = renderToStaticMarkup(createElement(
      ConversationMessage,
      { role: "user", createdAt: "2026-08-30T10:00:00Z" },
      createElement("p", null, "比较这些方法"),
    ));

    expect(html).toContain('aria-label="你的消息"');
    expect(html).toContain('class="message message-user"');
    expect(html).not.toContain(">你<");
    expect(html).toContain("比较这些方法");
  });
});
