import { useEffect, useRef, useState } from "react";

import type { AgentToolExecution, AgentToolExecutionsResponse } from "../api/types";
import {
  formatCandidateSize,
  type AgentTurnFailureSummary,
} from "../agent/presentation";

export interface ResearchActivityEvent {
  sequence: number;
  label: string;
  occurred_at: string;
}

interface AgentResearchActivityProps {
  events: ResearchActivityEvent[];
  toolExecutions: AgentToolExecutionsResponse | undefined;
  loading: boolean;
  error: boolean;
  active?: boolean;
  failure?: AgentTurnFailureSummary | null;
}

function toolStatusLabel(status: string): string {
  if (status === "succeeded") return "成功";
  if (status === "failed") return "失败";
  if (status === "running") return "执行中";
  return "等待执行";
}

function toolOutput(tool: AgentToolExecution): string {
  if (tool.output_preview) return tool.output_preview;
  if (tool.status === "running" || tool.status === "reserved") return "正在等待工具返回…";
  if (tool.safe_message) return tool.safe_message;
  return "本次调用没有可公开的输出预览。";
}

function activityDuration(events: ResearchActivityEvent[]): string | null {
  if (events.length < 2) return null;
  const startedAt = Date.parse(events[0].occurred_at);
  const completedAt = Date.parse(events.at(-1)?.occurred_at ?? "");
  if (!Number.isFinite(startedAt) || !Number.isFinite(completedAt) || completedAt < startedAt) {
    return null;
  }
  const seconds = Math.max(1, Math.round((completedAt - startedAt) / 1_000));
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
}

export default function AgentResearchActivity({
  events,
  toolExecutions,
  loading,
  error,
  active = false,
  failure,
}: AgentResearchActivityProps) {
  const usage = toolExecutions?.usage;
  const tools = toolExecutions?.items ?? [];
  const duration = activityDuration(events);
  const [open, setOpen] = useState(active);
  const previousActive = useRef(active);
  useEffect(() => {
    if (active === previousActive.current) return;
    previousActive.current = active;
    setOpen(active);
  }, [active]);
  if (events.length === 0 && !loading && !error && tools.length === 0 && !failure && !active) {
    return null;
  }

  return (
    <details
      className={`agent-research-disclosure${active ? " is-running" : failure ? " is-failed" : ""}`}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary role={active ? "status" : undefined}>
        <span
          className={active ? "agent-trace-spinner" : failure ? "agent-trace-failure" : "agent-trace-check"}
          aria-hidden="true"
        >
          {active ? "" : failure ? "!" : "✓"}
        </span>
        <strong>{active ? "正在研究" : failure ? "本轮研究未完成" : duration ? `用时 ${duration}` : "研究已完成"}</strong>
        <small>
          {active && events.at(-1)?.label ? `${events.at(-1)?.label} · ` : ""}
          {events.length} 个步骤 · {tools.length} 次工具调用
        </small>
      </summary>
      <div className="agent-research-content">
        {(failure || loading || error || usage) ? (
          <div className="agent-research-meta">
            {failure && (
              <section className="agent-research-failure" aria-label="本轮失败摘要">
                <strong>{failure.title}</strong>
                <p>{failure.detail}</p>
                <small className="mono">{failure.code}</small>
              </section>
            )}
            {usage && (
              <dl className="agent-budget-summary" aria-label="本轮用量与预算">
                <div><dt>模型调用</dt><dd>{usage.model_calls_reserved} / {usage.max_model_calls}</dd></div>
                <div><dt>工具调用</dt><dd>{usage.tool_calls_reserved} / {usage.max_tool_calls}</dd></div>
                <div><dt>Token</dt><dd>{usage.input_tokens ?? "—"} in · {usage.output_tokens ?? "—"} out</dd></div>
                <div><dt>时限</dt><dd>{Math.round(usage.wall_clock_limit_seconds / 60)} min</dd></div>
              </dl>
            )}
            {loading && <p className="muted" role="status">正在同步本轮工具记录…</p>}
            {error && <p className="error-text" role="alert">工具记录读取失败，研究步骤仍可用于核对进度。</p>}
          </div>
        ) : null}

        {tools.map((tool) => (
          <details
            className={`agent-tool-disclosure status-${tool.status}`}
            key={tool.invocation_id}
          >
            <summary>
              <span className="agent-tool-icon" aria-hidden="true">⌘</span>
              <strong>{tool.tool_name}</strong>
              <small>
                {tool.duration_ms === null ? "" : `${tool.duration_ms} ms · `}
                {toolStatusLabel(tool.status)}
              </small>
              <span className="agent-tool-state" aria-hidden="true">
                {tool.status === "failed" ? "!" : tool.status === "running" ? "…" : tool.status === "succeeded" ? "✓" : "·"}
              </span>
            </summary>
            <div className="agent-tool-content">
              <section>
                <h4>工具输入{tool.input_preview_truncated ? " · 已截断" : ""}</h4>
                <pre>{tool.input_preview ?? "本次调用没有可公开的输入预览。"}</pre>
              </section>
              <section>
                <h4>工具输出{tool.output_preview_truncated ? " · 已截断" : ""}</h4>
                <pre>{toolOutput(tool)}</pre>
              </section>
              <footer>
                <span className="mono">{tool.tool_version}</span>
                <span>{formatCandidateSize(tool.input_size_bytes)} in · {tool.output_size_bytes === null ? "—" : formatCandidateSize(tool.output_size_bytes)} out</span>
                {(tool.error_code || tool.safe_message) && (
                  <span className="error-text">{tool.safe_message ?? tool.error_code}</span>
                )}
              </footer>
            </div>
          </details>
        ))}
      </div>
    </details>
  );
}
