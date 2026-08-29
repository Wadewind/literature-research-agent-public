import type { AgentToolExecutionsResponse } from "../api/types";
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
  failure?: AgentTurnFailureSummary | null;
}

export default function AgentResearchActivity({
  events,
  toolExecutions,
  loading,
  error,
  failure,
}: AgentResearchActivityProps) {
  const usage = toolExecutions?.usage;
  const tools = toolExecutions?.items ?? [];
  if (events.length === 0 && !loading && !error && tools.length === 0 && !failure) return null;

  return (
    <details className="agent-research-ledger">
      <summary>
        <span>研究活动</span>
        <small>{events.length} 步 · {tools.length} 次工具执行</small>
      </summary>
      <div className="agent-research-content">
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
        {loading && <p className="muted" role="status">正在核对工具执行摘要…</p>}
        {error && <p className="error-text" role="alert">工具执行摘要读取失败，事件时间线仍可用于核对进度。</p>}
        {tools.length > 0 && (
          <section className="agent-tool-executions" aria-labelledby="agent-tool-executions-title">
            <h3 id="agent-tool-executions-title">脱敏工具记录</h3>
            <ol>
              {tools.map((tool) => (
                <li key={tool.invocation_id}>
                  <div>
                    <strong>{tool.tool_name}</strong>
                    <span className="mono">{tool.tool_version} · {tool.status}</span>
                  </div>
                  <p>{tool.safe_message ?? tool.error_code ?? "未提供公开结果摘要"}</p>
                  <small>
                    {tool.duration_ms === null ? "耗时 —" : `${tool.duration_ms} ms`}
                    {" · "}{formatCandidateSize(tool.input_size_bytes)} in
                    {" · "}{tool.output_size_bytes === null ? "—" : formatCandidateSize(tool.output_size_bytes)} out
                  </small>
                </li>
              ))}
            </ol>
          </section>
        )}
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
  );
}
