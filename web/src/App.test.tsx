import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AppFrame, MAIN_CONTENT_ID } from "./App";

describe("AppFrame", () => {
  it("为键盘用户提供可聚焦的主内容跳转目标", () => {
    const html = renderToStaticMarkup(
      createElement(AppFrame, {
        navigation: createElement("nav", null, "导航"),
        children: createElement("p", null, "页面内容"),
      }),
    );

    expect(MAIN_CONTENT_ID).toBe("main-content");
    expect(html).toContain('class="skip-link"');
    expect(html).toContain('href="#main-content"');
    expect(html).toContain('id="main-content"');
    expect(html).toContain('tabindex="-1"');
    expect(html).toContain("跳到主内容");
  });
});
