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
  if (events.length === 0 && !loading && !error && tools.length === 0 && !failure && !active) {
    return null;
  }

  return (
    <div className={`agent-research-trace${active ? " is-running" : ""}`}>
      <div className="agent-trace-status" role={active ? "status" : undefined}>
        <span
          className={active ? "agent-trace-spinner" : failure ? "agent-trace-failure" : "agent-trace-check"}
          aria-hidden="true"
        >
          {active ? "" : failure ? "!" : "✓"}
        </span>
        <span>
          {active ? "正在研究" : failure ? "本轮研究未完成" : "本轮研究完成"}
          <small> · {events.length} 个步骤 · {tools.length} 次工具调用</small>
        </span>
      </div>

      {(events.length > 0 || usage || failure || loading || error) && (
        <details className="agent-process-disclosure">
          <summary>
            <span aria-hidden="true">◇</span>
            <strong>研究过程</strong>
            <small>{events.at(-1)?.label ?? (active ? "正在准备研究环境" : "查看本轮过程")}</small>
          </summary>
          <div className="agent-process-content">
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
            {events.length > 0 && (
              <ol className="agent-event-timeline" aria-label="筛选后的研究事件">
                {events.map((event) => (
                  <li key={event.sequence}>
                    <span className="mono">{String(event.sequence).padStart(2, "0")}</span>
                    <span>{event.label}</span>
                    <time>{new Date(event.occurred_at).toLocaleTimeString()}</time>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </details>
      )}

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
  );
}
