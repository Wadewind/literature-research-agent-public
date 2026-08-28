import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import AgentAttachmentComposer from "./AgentAttachmentComposer";

const attachment = {
  attachment_id: "attachment-1",
  session_id: "session-1",
  version: 1,
  display_name: "notes.txt",
  media_type: "text/plain",
  content_hash: "a".repeat(64),
  size_bytes: 5,
  status: "available" as const,
  created_at: "2026-08-28T00:00:00Z",
};

describe("AgentAttachmentComposer", () => {
  it("renders only available attachments and hides delete for selected references", () => {
    const html = renderToStaticMarkup(createElement(AgentAttachmentComposer, {
      attachments: [attachment, { ...attachment, attachment_id: "deleted", status: "deleted" }],
      selectedIds: ["attachment-1"],
      disabled: false,
      uploading: false,
      error: null,
      onUpload: vi.fn(),
      onToggle: vi.fn(),
      onDelete: vi.fn(),
    }));

    expect(html).toContain("✓ notes.txt");
    expect(html).not.toContain("deleted");
    expect(html).not.toContain("删除 notes.txt");
  });

  it("disables upload at the five-reference boundary and renders safe error", () => {
    const html = renderToStaticMarkup(createElement(AgentAttachmentComposer, {
      attachments: [],
      selectedIds: ["1", "2", "3", "4", "5"],
      disabled: false,
      uploading: false,
      error: "文件类型不受支持",
      onUpload: vi.fn(),
      onToggle: vi.fn(),
      onDelete: vi.fn(),
    }));

    expect(html).toContain("disabled");
    expect(html).toContain("文件类型不受支持");
  });

  it("活动 Turn 或上传期间禁用选择和删除", () => {
    const render = (disabled: boolean, uploading: boolean) => renderToStaticMarkup(
      createElement(AgentAttachmentComposer, {
        attachments: [attachment],
        selectedIds: [],
        disabled,
        uploading,
        error: null,
        onUpload: vi.fn(),
        onToggle: vi.fn(),
        onDelete: vi.fn(),
      }),
    );

    for (const html of [render(true, false), render(false, true)]) {
      expect(html.match(/<button[^>]*disabled=""/g)).toHaveLength(2);
    }
  });
});
