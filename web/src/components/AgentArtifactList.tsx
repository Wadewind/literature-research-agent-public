import { agentArtifactContentUrl } from "../api/client";
import type { AgentArtifact } from "../api/types";
import { formatCandidateSize } from "../agent/presentation";

interface AgentArtifactListProps {
  artifacts: AgentArtifact[] | undefined;
  loading: boolean;
  error: boolean;
}

export default function AgentArtifactList({
  artifacts,
  loading,
  error,
}: AgentArtifactListProps) {
  return (
    <section className="agent-artifact-list" aria-labelledby="agent-artifact-title">
      <header>
        <div>
          <p className="eyebrow">TURN OUTPUTS</p>
          <h3 id="agent-artifact-title">正式成果</h3>
        </div>
        {artifacts && artifacts.length > 0 && (
          <span className="mono">{String(artifacts.length).padStart(2, "0")}</span>
        )}
      </header>

      {loading && <p className="muted" role="status">正在核对本轮正式成果…</p>}
      {error && (
        <p className="error-text" role="alert">
          成果列表读取失败。请刷新本轮；若仍失败，可在研究活动中核对 Turn 状态。
        </p>
      )}
      {!loading && !error && (artifacts?.length ?? 0) === 0 && (
        <p className="muted">
          本轮尚未发布正式成果。可让研究助手把图表或报告写入 outputs 并提交。
        </p>
      )}

      {artifacts?.map((artifact) => {
        const contentUrl = agentArtifactContentUrl(artifact.artifact_id);
        return (
          <article
            className={artifact.previewable ? undefined : "agent-artifact-no-preview"}
            key={artifact.artifact_id}
          >
            {artifact.previewable && (
              <a className="agent-artifact-preview" href={contentUrl} target="_blank" rel="noreferrer">
                <img
                  src={contentUrl}
                  alt={`${artifact.name} 预览`}
                  width={72}
                  height={54}
                  loading="lazy"
                />
              </a>
            )}
            <div className="agent-artifact-meta">
              <strong title={artifact.name}>{artifact.name}</strong>
              <small>{artifact.media_type} · {formatCandidateSize(artifact.size_bytes)}</small>
              <span className="mono">SHA-256 {artifact.content_hash.slice(0, 10)}</span>
            </div>
            <a className="button-plain agent-artifact-download" href={contentUrl} download={artifact.name}>
              下载
            </a>
          </article>
        );
      })}
    </section>
  );
}
