import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import PageBar, { PAGE_BAR_TITLE_MAX_PX } from "./PageBar";

function renderPageBar(actions?: React.ReactNode): string {
  return renderToStaticMarkup(
    createElement(
      MemoryRouter,
      undefined,
      createElement(PageBar, {
        breadcrumbs: [
          { label: "研究项目", to: "/" },
          { label: "事实性评估方法", to: "/projects/project-1" },
        ],
        title: "文献问答",
        actions,
      }),
    ),
  );
}

describe("PageBar", () => {
  it("使用语义化面包屑表达页面层级", () => {
    const html = renderPageBar();

    expect(html).toContain('<nav class="page-bar-breadcrumbs" aria-label="面包屑">');
    expect(html).toContain("<ol>");
    expect(html).toContain('href="/projects/project-1"');
    expect(html).toContain("事实性评估方法");
  });

  it("以唯一 h1 呈现不超过 20px 的页面标题", () => {
    const html = renderPageBar();

    expect(PAGE_BAR_TITLE_MAX_PX).toBeLessThanOrEqual(20);
    expect(html).toMatch(/<h1 class="page-bar-title"[^>]*>文献问答<\/h1>/);
    expect(html).toContain(`--page-bar-title-size:${PAGE_BAR_TITLE_MAX_PX}px`);
    expect(html.match(/<h1/g)).toHaveLength(1);
  });

  it("仅在传入页面操作时渲染可访问的 actions slot", () => {
    expect(renderPageBar()).not.toContain('aria-label="页面操作"');

    const html = renderPageBar(createElement("button", { type: "button" }, "归档 Project"));
    expect(html).toContain('<div class="page-bar-actions" role="group" aria-label="页面操作">');
    expect(html).toContain("归档 Project");
  });
});
