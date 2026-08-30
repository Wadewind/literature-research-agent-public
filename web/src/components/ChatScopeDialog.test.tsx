import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { PaperListItem } from "../api/types";
import { createScopeSelection, toggleScopePaper } from "../conversations/scopeSelection";
import ChatScopeDialog from "./ChatScopeDialog";

const paper: PaperListItem = {
  paper_id: "paper-1",
  title: "可验证的强化学习实验",
  title_source: "parsed_document",
  created_at: "2026-08-31T00:00:00Z",
  version: {
    version_id: "version-1",
    display_filename: "paper.pdf",
    size_bytes: 1024,
    created_at: "2026-08-31T00:00:00Z",
    parse_ready: true,
    ingestion_run_id: null,
  },
  project_ids: ["project-1"],
  archived_at: null,
};

describe("ChatScopeDialog", () => {
  it("呈现问题摘要、已选文献和最终创建动作", () => {
    const html = renderToStaticMarkup(
      createElement(ChatScopeDialog, {
        open: true,
        question: "这些研究的实验设置有何差异？",
        selection: toggleScopePaper(createScopeSelection(), paper.paper_id),
        papers: [paper],
        papersPending: false,
        papersError: null,
        archived: false,
        creating: false,
        createError: null,
        onClose: () => undefined,
        onSelectProject: () => undefined,
        onTogglePaper: () => undefined,
        onCreate: () => undefined,
      }),
    );

    expect(html).toContain('aria-labelledby="chat-scope-dialog-title"');
    expect(html).toContain("确认检索边界");
    expect(html).toContain("这些研究的实验设置有何差异？");
    expect(html).toContain("可验证的强化学习实验");
    expect(html).toContain('checked=""');
    expect(html).toContain("确认并创建问答");
  });
});
