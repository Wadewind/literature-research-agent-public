import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentManifestList from "./AgentManifestList";

describe("AgentManifestList", () => {
  it("只显示公开 Manifest 元数据，并仅为存在的来源生成链接", () => {
    const html = renderToStaticMarkup(createElement(AgentManifestList, {
      loading: false,
      error: false,
      manifest: {
        run_id: "run-1",
        items: [
          {
            artifact_id: "artifact-1",
            name: "chart.png",
            media_type: "image/png",
            content_hash: "a".repeat(64),
            size_bytes: 120,
            source_url: "https://arxiv.org/abs/1234.5678",
            source_url_hash: "b".repeat(64),
            source_status: "declared_public_target_checked",
            created_at: "2026-08-28T00:00:00Z",
          },
          {
            artifact_id: "artifact-2",
            name: "notes.md",
            media_type: "text/markdown",
            content_hash: "c".repeat(64),
            size_bytes: 48,
            source_url: null,
            source_url_hash: null,
            source_status: "not_provided",
            created_at: "2026-08-28T00:00:00Z",
          },
        ],
      },
    }));

    expect(html).toContain("chart.png");
    expect(html).toContain("notes.md");
    expect(html).toContain("来源已校验");
    expect(html.match(/href=/g)).toHaveLength(1);
    expect(html).not.toContain("storage_key");
  });
});
