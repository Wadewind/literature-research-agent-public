import type { AgentArtifact, AgentMessage, AgentTurn, CitationSummary, ReviewOutput } from "../api/types";
import { formatCandidateSize, projectIndexLabel } from "../agent/presentation";
import AgentArtifactList from "./AgentArtifactList";
import AgentBrowserPanel from "./AgentBrowserPanel";

interface AgentEvidenceMarginProps {
  projectId: string;
  turn: AgentTurn | undefined;
  matrix: ReviewOutput | undefined;
  projectReadyIndexCount: number | undefined;
  projectIndexError: boolean;
  assistantMessages: AgentMessage[];
  selectedEvidence: CitationSummary | null;
  onSelectEvidence: (value: CitationSummary) => void;
  onClearEvidence: () => void;
  artifacts: AgentArtifact[] | undefined;
  artifactsLoading: boolean;
  artifactsError: boolean;
  sessionId: string;
  activeTurnRunId: string | null;
}

function pageLabel(citation: CitationSummary): string {
  if (citation.page_start === null) return "页码未知";
  if (citation.page_end && citation.page_end !== citation.page_start) {
    return `第 ${citation.page_start}–${citation.page_end} 页`;
  }
  return `第 ${citation.page_start} 页`;
}

export default function AgentEvidenceMargin({
  projectId,
  turn,
  matrix,
  projectReadyIndexCount,
  projectIndexError,
  assistantMessages,
  selectedEvidence,
  onSelectEvidence,
  onClearEvidence,
  artifacts,
  artifactsLoading,
  artifactsError,
  sessionId,
  activeTurnRunId,
}: AgentEvidenceMarginProps) {
  const claims = assistantMessages.flatMap((message) => message.claims ?? []);
  const activeMatrixId = matrix?.output_id ?? turn?.review_output_id;
  const indexScope = turn ? "turn" : "project";
  const indexCount = turn ? turn.project_index_refs.length : projectReadyIndexCount;

  return (
    <aside className="agent-evidence-margin" aria-label="Evidence Margin">
      <header>
        <div><p className="eyebrow">EVIDENCE MARGIN</p><h2>证据批注</h2></div>
        {selectedEvidence && <button className="button-plain" type="button" onClick={onClearEvidence}>关闭</button>}
      </header>
      <AgentBrowserPanel sessionId={sessionId} activeTurnRunId={activeTurnRunId} />
      <dl className="agent-context-ledger">
        <div><dt>Evidence Matrix</dt><dd className="mono">{activeMatrixId?.slice(0, 8) ?? "未选择"}</dd></div>
        <div>
          <dt>{turn ? "本轮索引快照" : "当前 Project 索引"}</dt>
          <dd className={projectIndexError && !turn ? "error-text" : undefined}>
            {projectIndexError && !turn
              ? "Project 索引读取失败"
              : projectIndexLabel(indexCount, indexScope)}
          </dd>
        </div>
        <div><dt>正式成果</dt><dd>{artifacts?.length ?? 0} 项 committed</dd></div>
      </dl>

      {selectedEvidence ? (
        <section className="agent-selected-evidence">
          <p className="mono">{selectedEvidence.section_path || "未标注章节"} · {pageLabel(selectedEvidence)}</p>
          <blockquote>{selectedEvidence.excerpt}</blockquote>
          <a
            href={`/api/v1/projects/${projectId}/paper-versions/${selectedEvidence.version_id}/file#page=${selectedEvidence.page_start ?? 1}`}
            target="_blank"
            rel="noreferrer"
          >
            打开来源 PDF
          </a>
        </section>
      ) : (
        <section className="agent-claim-margin">
          <h3>回答引用</h3>
          {claims.length === 0 && <p className="muted">完成带引用的研究 Turn 后，Claim 会排列在这里。</p>}
          {claims.map((claim, claimIndex) => (
            <article key={`${claim.text}:${claimIndex}`}>
              <span className="margin-index">E{String(claimIndex + 1).padStart(2, "0")}</span>
              <p>{claim.text}</p>
              <div>
                {claim.citations.map((citation) => (
                  <button
                    type="button"
                    className="citation-marker"
                    key={citation.evidence_id}
                    onClick={() => onSelectEvidence(citation)}
                  >
                    {pageLabel(citation)}
                  </button>
                ))}
              </div>
            </article>
          ))}
        </section>
      )}

      <AgentArtifactList artifacts={artifacts} loading={artifactsLoading} error={artifactsError} />

      <details className="agent-candidate-list">
        <summary>内部候选 · {turn?.candidates.length ?? 0}</summary>
        {turn?.candidates.length === 0 && <p className="muted">本轮没有暂存候选成果。</p>}
        {turn?.candidates.map((candidate) => (
          <article key={candidate.candidate_id}>
            <strong>{candidate.name}</strong>
            <small>{candidate.media_type} · {formatCandidateSize(candidate.size_bytes)}</small>
            <span className="badge badge-pending">{candidate.status}</span>
          </article>
        ))}
      </details>
    </aside>
  );
}
