import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AgentToolExecutionsResponse } from "../api/types";
import AgentResearchActivity from "./AgentResearchActivity";

describe("AgentResearchActivity", () => {
  it("只展示脱敏 ToolExecution 与 Usage/Budget 摘要", () => {
    const toolExecutions = {
      usage: {
        max_model_calls: 8,
        max_tool_calls: 12,
        model_calls_reserved: 2,
        tool_calls_reserved: 3,
        wall_clock_limit_seconds: 900,
        tool_timeout_seconds: 60,
        execute_timeout_seconds: 120,
        max_tool_output_bytes: 65536,
        max_repeated_tool_calls: 3,
        max_input_tokens_per_model_call: 12000,
        max_output_tokens_per_model_call: 8000,
        input_tokens: 1530,
        output_tokens: 410,
        started_at: "2026-08-28T00:00:00Z",
        deadline_at: "2026-08-28T00:15:00Z",
      },
      items: [{
        invocation_id: "invoke-1",
        tool_name: "search_project_evidence",
        tool_version: "v1",
        input_schema_hash: "a".repeat(64),
        args_hash: "b".repeat(64),
        status: "succeeded",
        input_size_bytes: 24,
        output_size_bytes: 128,
        result_hash: "c".repeat(64),
        error_code: null,
        safe_message: "返回 3 条证据摘要",
        duration_ms: 42,
        started_at: "2026-08-28T00:00:01Z",
        completed_at: "2026-08-28T00:00:02Z",
      }],
      raw_args: "SECRET_PROMPT_SHOULD_NOT_RENDER",
      webpage_body: "FULL_WEBPAGE_SHOULD_NOT_RENDER",
    } as AgentToolExecutionsResponse & { raw_args: string; webpage_body: string };

    const html = renderToStaticMarkup(createElement(AgentResearchActivity, {
      events: [{ sequence: 2, label: "研究任务已开始", occurred_at: "2026-08-28T00:00:00Z" }],
      toolExecutions,
      loading: false,
      error: false,
    }));

    expect(html).toContain("模型调用");
    expect(html).toContain("2 / 8");
    expect(html).toContain("工具调用");
    expect(html).toContain("3 / 12");
    expect(html).toContain("search_project_evidence");
    expect(html).toContain("返回 3 条证据摘要");
    expect(html).toContain("研究任务已开始");
    expect(html).not.toContain("SECRET_PROMPT_SHOULD_NOT_RENDER");
    expect(html).not.toContain("FULL_WEBPAGE_SHOULD_NOT_RENDER");
  });
});
