import type {
  AgentArtifact,
  AgentArtifactManifest,
  AgentCandidate,
} from "../api/types";
import { formatCandidateSize } from "../agent/presentation";
import AgentArtifactList from "./AgentArtifactList";
import AgentManifestList from "./AgentManifestList";

interface AgentTurnOutputsProps {
  artifacts: AgentArtifact[] | undefined;
  candidates: AgentCandidate[];
  manifest: AgentArtifactManifest | undefined;
  artifactsLoading: boolean;
  artifactsError: boolean;
  manifestLoading: boolean;
  manifestError: boolean;
}

export default function AgentTurnOutputs({
  artifacts,
  candidates,
  manifest,
  artifactsLoading,
  artifactsError,
  manifestLoading,
  manifestError,
}: AgentTurnOutputsProps) {
  return (
    <div className="agent-turn-outputs">
      <AgentArtifactList
        artifacts={artifacts}
        loading={artifactsLoading}
        error={artifactsError}
      />
      <details className="agent-candidate-list">
        <summary>内部候选 · {candidates.length}</summary>
        {candidates.length === 0 && <p className="muted">本轮没有暂存候选成果。</p>}
        {candidates.map((candidate) => (
          <article key={candidate.candidate_id}>
            <strong>{candidate.name}</strong>
            <small>{candidate.media_type} · {formatCandidateSize(candidate.size_bytes)}</small>
            <span className="badge badge-pending">{candidate.status}</span>
          </article>
        ))}
      </details>
      <AgentManifestList
        manifest={manifest}
        loading={manifestLoading}
        error={manifestError}
      />
    </div>
  );
}
