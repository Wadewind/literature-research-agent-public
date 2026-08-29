import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import AgentTurnFailure from "./AgentTurnFailure";

describe("AgentTurnFailure", () => {
  it("把失败说明绑定到短 Run ID，并引导用户重新发起", () => {
    const html = renderToStaticMarkup(createElement(AgentTurnFailure, {
      runId: "d298fb7b-3185-4878-808d-818f1f0dcf9d",
      summary: {
        title: "研究环境未能启动",
        detail: "Sandbox 配置未通过校验，本轮未进入模型或工具执行。",
        code: "runtime_sandbox_metadata_invalid",
      },
    }));

    expect(html).toContain("研究环境未能启动");
    expect(html).toContain("d298fb7b");
    expect(html).toContain("runtime_sandbox_metadata_invalid");
    expect(html).toContain("可以调整问题后重新发起");
  });
});
