import type { AgentMessage, AgentTurn, CitationSummary, ReviewOutput } from "../api/types";
import { projectIndexLabel } from "../agent/presentation";

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
}: AgentEvidenceMarginProps) {
  const claims = assistantMessages.flatMap((message) => message.claims ?? []);
  const activeMatrixId = matrix?.output_id ?? turn?.review_output_id;
  const indexScope = turn ? "turn" : "project";
  const indexCount = turn ? turn.project_index_refs.length : projectReadyIndexCount;

  return (
    <section className="agent-evidence-panel" aria-label="证据批注">
      {selectedEvidence && (
        <button className="button-plain agent-evidence-close" type="button" onClick={onClearEvidence}>
          返回引用列表
        </button>
      )}
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

    </section>
  );
}
