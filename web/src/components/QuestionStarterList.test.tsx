import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import QuestionStarterList from "./QuestionStarterList";

describe("QuestionStarterList", () => {
  it("使用三个可访问按钮呈现推荐问题与选中状态", () => {
    const html = renderToStaticMarkup(
      createElement(QuestionStarterList, {
        selectedId: "methods",
        disabled: false,
        onSelect: () => undefined,
      }),
    );

    expect(html.match(/<button/g)).toHaveLength(3);
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('aria-pressed="false"');
    expect(html).toContain("核心方法");
    expect(html).toContain("实验设置");
    expect(html).toContain("主要结论");
  });
});
