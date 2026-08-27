import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { CreateReviewResult, Project, ReviewListItem } from "../api/types";
import ProjectWorkspaceHeader from "../components/ProjectWorkspaceHeader";
import { ensureReviewIntent, type ReviewIntent } from "../reviews/reviewIntent";
import { reviewListRefetchInterval } from "../reviews/reviewListRefresh";
import { stageLabel } from "../reviews/reviewPresentation";
import { statusLabel } from "../runs/runStatus";

function reviewBadge(status: string): string {
  if (status === "succeeded") return "badge badge-ok";
  if (status === "failed") return "badge badge-error";
  if (status === "cancelled") return "badge badge-muted";
  if (status.startsWith("waiting_")) return "badge badge-warn";
  return "badge badge-pending";
}

export default function ReviewsPage() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [researchQuestion, setResearchQuestion] = useState("");
  const [intent, setIntent] = useState<ReviewIntent | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const reviewsQuery = useQuery({
    queryKey: ["reviews", projectId],
    queryFn: () => apiFetch<ReviewListItem[]>(`/api/v1/projects/${projectId}/reviews`),
    refetchInterval: (query) => reviewListRefetchInterval(query.state.data),
  });
  const createMutation = useMutation({
    mutationFn: (input: ReviewIntent) =>
      apiFetch<CreateReviewResult>(`/api/v1/projects/${projectId}/reviews`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": input.key,
        },
        body: JSON.stringify({ research_question: input.researchQuestion }),
      }),
    onSuccess: (result) => {
      setIntent(null);
      setResearchQuestion("");
      void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
      navigate(`/projects/${projectId}/reviews/${result.run_id}`);
    },
  });

  const project = projectQuery.data;
  const archived = Boolean(project?.archived_at);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    const question = researchQuestion.trim();
    if (!question || archived) return;
    const nextIntent = ensureReviewIntent(intent, question, () => crypto.randomUUID());
    setIntent(nextIntent);
    createMutation.mutate(nextIntent);
  };

  if (projectQuery.isError) {
    return <section className="notice"><p className="error-text">{errorMessage(projectQuery.error)}</p><Link to="/">返回项目</Link></section>;
  }

  return (
    <div className="page-flow">
      <ProjectWorkspaceHeader
        projectId={projectId}
        project={project}
        active="reviews"
        eyebrow="综述"
        description="从检索、导入、Evidence Matrix 到 Artifact 追踪固定 Workflow。"
        actions={<div className="metric-block"><strong>{reviewsQuery.data?.length ?? "—"}</strong><span>Review Runs</span></div>}
      />

      <section className="review-workbench">
        <div>
          <p className="eyebrow">NEW REVIEW</p>
          <h2>定义这次综述的问题</h2>
          <p>系统将按固定 Workflow 搜索并自动导入 arXiv 来源；大纲确认将在后续阶段出现。</p>
        </div>
        <form className="review-create-form" onSubmit={submit}>
          <label htmlFor="research-question">研究问题</label>
          <textarea
            id="research-question"
            rows={4}
            maxLength={4000}
            value={researchQuestion}
            onChange={(event) => setResearchQuestion(event.target.value)}
            disabled={archived}
            placeholder="例如：可靠的长时 AI Workflow 如何处理暂停、恢复与重复投递？"
            required
          />
          {archived && <p className="readonly-note">该 Project 已归档。历史 Review 仍可查看，但不能创建新的 Workflow。</p>}
          {createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}。修改问题前可直接重试，系统会复用本次幂等意图。</p>}
          <button type="submit" disabled={archived || !researchQuestion.trim() || createMutation.isPending}>
            {createMutation.isPending ? "正在创建…" : "开始文献综述"}<span aria-hidden="true">→</span>
          </button>
        </form>
      </section>

      <section className="section-block" aria-labelledby="review-list-title">
        <div className="section-title-row"><div><p className="eyebrow">RUN LEDGER</p><h2 id="review-list-title">Review 历史</h2></div><span className="section-count">{String(reviewsQuery.data?.length ?? 0).padStart(2, "0")}</span></div>
        {reviewsQuery.isPending && <div className="skeleton-block">正在读取 Review…</div>}
        {reviewsQuery.isError && <p className="notice error-text">{errorMessage(reviewsQuery.error)}</p>}
        {reviewsQuery.data?.length === 0 && (
          <div className="empty-state"><span className="empty-glyph" aria-hidden="true">⇢</span><h3>还没有文献综述</h3><p>在上方写下一个可研究的问题，创建第一条固定 Workflow。</p></div>
        )}
        <div className="review-list">
          {reviewsQuery.data?.map((review) => (
            <Link key={review.run_id} className="review-list-row" to={`/projects/${projectId}/reviews/${review.run_id}`}>
              <span className="review-date">{new Date(review.created_at).toLocaleDateString()}</span>
              <span className="review-question"><strong>{review.research_question}</strong><small className="mono">{review.run_id.slice(0, 8)}</small></span>
              <span><span className={reviewBadge(review.status)}>{statusLabel(review.status)}</span></span>
              <span className="review-current-stage">{stageLabel(review.current_stage)}<span aria-hidden="true">→</span></span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
