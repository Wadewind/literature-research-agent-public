import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentTurnOutputs from "./AgentTurnOutputs";

describe("AgentTurnOutputs", () => {
  it("展示候选真实尝试名称、拒绝码与未发布状态", () => {
    const html = renderToStaticMarkup(createElement(AgentTurnOutputs, {
      artifacts: [],
      candidates: [
        {
          candidate_id: "candidate-rejected",
          name: "plot_quadratic.py",
          media_type: "text/plain",
          content_hash: "a".repeat(64),
          size_bytes: 0,
          status: "rejected",
          rejection_code: "artifact_extension_mismatch",
        },
        {
          candidate_id: "candidate-validated",
          name: "quadratic_plot.png",
          media_type: "image/png",
          content_hash: "b".repeat(64),
          size_bytes: 128,
          status: "validated",
          rejection_code: null,
        },
      ],
      manifest: undefined,
      artifactsLoading: false,
      artifactsError: false,
      manifestLoading: false,
      manifestError: false,
    }));

    expect(html).toContain("plot_quadratic.py");
    expect(html).toContain("artifact_extension_mismatch");
    expect(html).toContain("validated_not_published");
    expect(html).not.toContain("rejected.txt");
  });
});
