import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, errorMessage } from "../api/client";
import type { HumanInputRequest, ReviewOutput } from "../api/types";

interface SourceCandidate {
  source_id: string;
  arxiv_id: string;
  arxiv_version: string;
  title: string;
  abstract: string;
  authors: string[];
  published_at: string;
  page_count: number | null;
  pdf_url: string;
}

interface CandidatePayload {
  candidates: SourceCandidate[];
  ready_project_source_count: number;
  max_selected: number;
  source_limit: number;
}

function candidatePayload(output: ReviewOutput | undefined): CandidatePayload | null {
  const payload = output?.payload;
  if (!payload || !Array.isArray(payload.candidates)) return null;
  return payload as unknown as CandidatePayload;
}

export default function ReviewSourceSelection({
  projectId,
  runId,
  request,
}: {
  projectId: string;
  runId: string;
  request: HumanInputRequest | null;
}) {
  const queryClient = useQueryClient();
  const active = request?.request_kind === "source_selection";
  const [selected, setSelected] = useState<string[]>([]);
  const submissionKey = useRef(crypto.randomUUID());
  const outputQuery = useQuery({
    queryKey: ["review-source-candidates", projectId, runId],
    queryFn: () =>
      apiFetch<ReviewOutput>(
        `/api/v1/projects/${projectId}/reviews/${runId}/source-candidates`,
      ),
    enabled: active,
  });
  const payload = candidatePayload(outputQuery.data);

  useEffect(() => {
    setSelected([]);
    submissionKey.current = crypto.randomUUID();
  }, [request?.request_id]);

  const mutation = useMutation({
    mutationFn: () => {
      if (!request || !outputQuery.data) throw new Error("候选来源请求尚未就绪");
      return apiFetch(`/api/v1/projects/${projectId}/reviews/${runId}/source-selection`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": submissionKey.current,
        },
        body: JSON.stringify({
          request_id: request.request_id,
          request_version: request.request_version,
          candidate_output_id: outputQuery.data.output_id,
          selected_source_ids: selected,
        }),
      });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["review-sources", projectId, runId] });
    },
  });

  if (!active) return null;
  const readyCount = payload?.ready_project_source_count ?? 0;
  const canContinue = readyCount + selected.length > 0;

  return (
    <div className="source-selection-overlay" role="dialog" aria-modal="true" aria-labelledby="source-selection-title">
      <section className="source-selection-dialog">
        <header>
          <div>
            <p className="project-library-kicker">下载前人工筛选</p>
            <h2 id="source-selection-title">选择要引入的 arXiv 论文</h2>
            <p>已有 {readyCount} 篇项目论文；还可选择 {payload?.max_selected ?? "—"} 篇。未选论文不会下载或加入项目。</p>
          </div>
          <span className="section-count">{selected.length}/{payload?.max_selected ?? 0}</span>
        </header>
        {outputQuery.isPending && <p className="muted">正在读取候选论文…</p>}
        {outputQuery.isError && <p className="error-text">{errorMessage(outputQuery.error)}</p>}
        <ol className="source-candidate-list">
          {payload?.candidates.map((candidate, index) => {
            const checked = selected.includes(candidate.source_id);
            const disabled = !checked && selected.length >= payload.max_selected;
            return (
              <li key={candidate.source_id}>
                <label>
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled || mutation.isPending}
                    onChange={(event) => {
                      submissionKey.current = crypto.randomUUID();
                      setSelected((current) =>
                        event.target.checked
                          ? [...current, candidate.source_id]
                          : current.filter((value) => value !== candidate.source_id),
                      );
                    }}
                  />
                  <span className="source-rank">{String(index + 1).padStart(2, "0")}</span>
                  <span>
                    <strong>{candidate.title}</strong>
                    <small>
                      arXiv {candidate.arxiv_id}{candidate.arxiv_version} · {candidate.page_count ? `${candidate.page_count} 页` : "页数待下载后确认"}
                    </small>
                  </span>
                </label>
                <p>{candidate.abstract}</p>
                <small>{candidate.authors.join("、") || "作者信息缺失"}</small>
              </li>
            );
          })}
        </ol>
        {payload?.candidates.length === 0 && <p className="notice">本次检索没有返回候选论文。</p>}
        <footer>
          <p>确认后，系统只导入选中项，并继续生成 Evidence Matrix。</p>
          <button type="button" disabled={!canContinue || mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? "正在提交…" : selected.length > 0 ? `确认引入 ${selected.length} 篇` : "使用已有项目论文继续"}
          </button>
        </footer>
        {mutation.isError && <p className="error-text">{errorMessage(mutation.error)}</p>}
      </section>
    </div>
  );
}
