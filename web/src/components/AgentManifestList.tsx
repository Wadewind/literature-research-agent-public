import type { AgentArtifactManifest } from "../api/types";
import { formatCandidateSize } from "../agent/presentation";

interface AgentManifestListProps {
  manifest: AgentArtifactManifest | undefined;
  loading: boolean;
  error: boolean;
}

export default function AgentManifestList({
  manifest,
  loading,
  error,
}: AgentManifestListProps) {
  return (
    <section className="agent-manifest-list" aria-labelledby="agent-manifest-title">
      <header>
        <h3 id="agent-manifest-title">来源 Manifest</h3>
        <span className="mono">{String(manifest?.items.length ?? 0).padStart(2, "0")}</span>
      </header>
      {loading && <p className="muted" role="status">正在核对来源 Manifest…</p>}
      {error && <p className="error-text" role="alert">来源 Manifest 读取失败。</p>}
      {!loading && !error && (manifest?.items.length ?? 0) === 0 && (
        <p className="muted">本轮正式成果尚无可展示的来源 Manifest。</p>
      )}
      {manifest?.items.map((item) => (
        <article key={item.artifact_id}>
          <div>
            <strong>{item.name}</strong>
            <small>{item.media_type} · {formatCandidateSize(item.size_bytes)}</small>
          </div>
          <span className={`badge ${item.source_url ? "badge-ok" : "badge-pending"}`}>
            {item.source_url ? "来源已校验" : "未声明来源"}
          </span>
          <code title={item.content_hash}>SHA-256 {item.content_hash.slice(0, 10)}</code>
          {item.source_url && (
            <a href={item.source_url} target="_blank" rel="noreferrer">打开来源</a>
          )}
        </article>
      ))}
    </section>
  );
}
