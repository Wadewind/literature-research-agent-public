import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PaperListItem } from "../api/types";
import PaperTitle from "./PaperTitle";

function paper(title: string | null): PaperListItem {
  return {
    paper_id: "paper-1",
    title,
    title_source: title ? "arxiv_metadata" : null,
    created_at: "2026-08-30T00:00:00Z",
    archived_at: null,
    project_ids: [],
    version: {
      version_id: "version-1",
      display_filename: "2401.00001v1.pdf",
      size_bytes: 1024,
      created_at: "2026-08-30T00:00:00Z",
      parse_ready: true,
      ingestion_run_id: null,
    },
  };
}

describe("PaperTitle", () => {
  it("显示标题并通过 title 属性保留完整文本", () => {
    const html = renderToStaticMarkup(createElement(PaperTitle, {
      paper: paper("A Very Long Research Paper Title"),
    }));

    expect(html).toContain('title="A Very Long Research Paper Title"');
    expect(html).toContain("A Very Long Research Paper Title");
  });

  it("标题未知时回退文件名并标记回退状态", () => {
    const html = renderToStaticMarkup(createElement(PaperTitle, {
      paper: paper(null),
    }));

    expect(html).toContain("2401.00001v1.pdf");
    expect(html).toContain("paper-title-fallback");
  });
});
