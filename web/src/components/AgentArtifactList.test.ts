import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentArtifactList from "./AgentArtifactList";

describe("AgentArtifactList", () => {
  it("只预览安全位图，并为正式成果提供下载入口", () => {
    const html = renderToStaticMarkup(
      createElement(AgentArtifactList, {
        loading: false,
        error: false,
        artifacts: [
          {
            artifact_id: "artifact-png",
            turn_run_id: "turn-1",
            name: "chart.png",
            media_type: "image/png",
            content_hash: "a".repeat(64),
            size_bytes: 24,
            previewable: true,
            created_at: "2026-08-28T00:00:00Z",
          },
          {
            artifact_id: "artifact-svg",
            turn_run_id: "turn-1",
            name: "diagram.svg",
            media_type: "image/svg+xml",
            content_hash: "b".repeat(64),
            size_bytes: 120,
            previewable: false,
            created_at: "2026-08-28T00:00:00Z",
          },
        ],
      }),
    );

    expect(html.match(/<img/g)).toHaveLength(1);
    expect(html).toContain("chart.png 预览");
    expect(html).toContain("diagram.svg");
    expect(html.match(/>下载<\/a>/g)).toHaveLength(2);
  });

  it("空态和错误态给出下一步", () => {
    const empty = renderToStaticMarkup(
      createElement(AgentArtifactList, {
        artifacts: [],
        loading: false,
        error: false,
      }),
    );
    const error = renderToStaticMarkup(
      createElement(AgentArtifactList, {
        artifacts: undefined,
        loading: false,
        error: true,
      }),
    );
    expect(empty).toContain("写入 outputs 并提交");
    expect(error).toContain("请刷新本轮");
  });
});
