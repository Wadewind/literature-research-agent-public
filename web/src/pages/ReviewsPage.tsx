import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { CreateReviewResult, Project, ReviewListItem } from "../api/types";
import PageBar from "../components/PageBar";
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
    return <div className="page-flow"><PageBar breadcrumbs={[{ label: "研究项目", to: "/" }]} title="文献研究" /><section className="notice"><p className="error-text">{errorMessage(projectQuery.error)}</p><Link to="/">返回项目</Link></section></div>;
  }

  return (
    <div className="page-flow literature-research-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "文献研究" }]}
        title={project?.name ?? "正在读取项目…"}
        actions={<span className="page-bar-stat"><strong>{reviewsQuery.data?.length ?? "—"}</strong> 个研究任务</span>}
      />

      <section className="review-workbench">
        <div className="review-workbench-intro">
          <p className="project-library-kicker">固定研究流程</p>
          <h2>从研究问题到证据矩阵</h2>
          <p>系统将补充 arXiv 来源，等待解析与索引，整理跨论文 Evidence Matrix，再生成大纲和简要综述。</p>
          <ol className="review-path-summary" aria-label="研究任务主要阶段">
            <li><span>01</span>收集与准备来源</li>
            <li><span>02</span>提取并组织证据</li>
            <li><span>03</span>生成研究产物</li>
          </ol>
          <Link className="text-link" to={`/projects/${projectId}?add=search`}>先到文献库搜索并引入论文 →</Link>
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
          <small>任务会保留来源、Evidence Matrix、结构化大纲和最终文件，过程可恢复、可取消。</small>
          {archived && <p className="readonly-note">该项目已归档。历史研究任务仍可查看，但不能创建新任务。</p>}
          {createMutation.isError && <p className="error-text">{errorMessage(createMutation.error)}。修改问题前可直接重试，系统会复用本次幂等意图。</p>}
          <button type="submit" disabled={archived || !researchQuestion.trim() || createMutation.isPending}>
            {createMutation.isPending ? "正在创建…" : "开始文献研究"}<span aria-hidden="true">→</span>
          </button>
        </form>
      </section>

      <section className="section-block" aria-labelledby="review-list-title">
        <div className="section-title-row"><div><p className="project-library-kicker">研究记录</p><h2 id="review-list-title">研究任务</h2></div><span className="section-count">{String(reviewsQuery.data?.length ?? 0).padStart(2, "0")}</span></div>
        {reviewsQuery.isPending && <div className="skeleton-block">正在读取研究任务…</div>}
        {reviewsQuery.isError && <p className="notice error-text">{errorMessage(reviewsQuery.error)}</p>}
        {reviewsQuery.data?.length === 0 && (
          <div className="empty-state"><span className="empty-glyph" aria-hidden="true">⇢</span><h3>还没有研究任务</h3><p>在上方写下研究问题，系统会从来源准备推进到 Evidence Matrix 和最终产物。</p></div>
        )}
        <div className="review-list">
          {reviewsQuery.data?.map((review) => (
            <Link key={review.run_id} className="review-list-row" to={`/projects/${projectId}/reviews/${review.run_id}`}>
              <span className="review-date">{new Date(review.created_at).toLocaleDateString()}</span>
              <span className="review-question"><strong>{review.research_question}</strong><small className="mono">{review.run_id.slice(0, 8)}</small></span>
              <span className="review-matrix-state">{review.evidence_matrix ? <><strong>{review.evidence_matrix.row_count}</strong><small>条 Matrix 记录</small></> : <small>等待 Evidence Matrix</small>}</span>
              <span><span className={reviewBadge(review.status)}>{statusLabel(review.status)}</span></span>
              <span className="review-current-stage">{stageLabel(review.current_stage)}<span aria-hidden="true">→</span></span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
