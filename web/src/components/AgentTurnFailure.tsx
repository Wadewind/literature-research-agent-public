import type { AgentTurnFailureSummary } from "../agent/presentation";

interface AgentTurnFailureProps {
  runId: string;
  summary: AgentTurnFailureSummary;
}

export default function AgentTurnFailure({ runId, summary }: AgentTurnFailureProps) {
  return (
    <section className="agent-turn-failure" role="alert" aria-label="本轮研究失败">
      <div>
        <span className="eyebrow">TURN FAILED</span>
        <h3>{summary.title}</h3>
        <p>{summary.detail}</p>
      </div>
      <small>
        <span className="mono">Run {runId.slice(0, 8)}</span>
        <span className="mono">{summary.code}</span>
        <span>可以调整问题后重新发起</span>
      </small>
    </section>
  );
}
