import { describe, expect, it } from "vitest";

import type { PaperListItem } from "../api/types";
import { filterAndSortPapers, paperDisplayTitle } from "./paperCatalog";

function paper(
  id: string,
  title: string | null,
  filename: string,
  options: Partial<PaperListItem> = {},
): PaperListItem {
  return {
    paper_id: id,
    title,
    title_source: title ? "parsed_document" : null,
    created_at: "2026-08-30T00:00:00Z",
    archived_at: null,
    project_ids: [],
    version: {
      version_id: `version-${id}`,
      display_filename: filename,
      size_bytes: 1024,
      created_at: "2026-08-30T00:00:00Z",
      parse_ready: true,
      ingestion_run_id: null,
    },
    ...options,
  };
}

describe("论文目录投影", () => {
  it("优先显示论文标题，缺失时回退原始文件名", () => {
    expect(paperDisplayTitle(paper("1", "Attention Is All You Need", "1706.03762.pdf")))
      .toBe("Attention Is All You Need");
    expect(paperDisplayTitle(paper("2", null, "upload.pdf"))).toBe("upload.pdf");
  });

  it("按标题或文件名搜索，并组合解析状态和项目筛选", () => {
    const papers = [
      paper("1", "Alpha Planning", "2401.00001.pdf", { project_ids: ["project-a"] }),
      paper("2", "Beta Learning", "special-upload.pdf", {
        project_ids: ["project-b"],
        version: {
          version_id: "version-2",
          display_filename: "special-upload.pdf",
          size_bytes: 1024,
          created_at: "2026-08-30T00:00:00Z",
          parse_ready: false,
          ingestion_run_id: null,
        },
      }),
    ];

    expect(filterAndSortPapers(papers, {
      query: "special",
      status: "working",
      project: "project-b",
      sort: "recent",
    }).map((item) => item.paper_id)).toEqual(["2"]);
  });

  it("支持未收录筛选和按标题稳定排序", () => {
    const papers = [
      paper("2", "Zulu", "z.pdf"),
      paper("1", "Alpha", "a.pdf"),
      paper("3", "Project Paper", "p.pdf", { project_ids: ["project-a"] }),
    ];

    expect(filterAndSortPapers(papers, {
      query: "",
      status: "all",
      project: "unassigned",
      sort: "title",
    }).map((item) => item.paper_id)).toEqual(["1", "2"]);
  });

  it("归档文献只进入归档状态，不混入已解析筛选", () => {
    const archived = paper("1", "Archived", "archived.pdf", {
      archived_at: "2026-08-30T01:00:00Z",
    });

    expect(filterAndSortPapers([archived], {
      query: "",
      status: "ready",
      project: "all",
      sort: "recent",
    })).toEqual([]);
    expect(filterAndSortPapers([archived], {
      query: "",
      status: "archived",
      project: "all",
      sort: "recent",
    })).toEqual([archived]);
  });
});
