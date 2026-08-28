import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { ReviewDetail, ReviewSource } from "../api/types";
import PageBar from "../components/PageBar";
import ReviewResults from "../components/ReviewResults";
import {
  reviewStageRail,
  sourcePresentation,
  stageLabel,
  type StageRailState,
} from "../reviews/reviewPresentation";
import { isCancellable, isTerminal, statusLabel } from "../runs/runStatus";
import { useRunEvents } from "../runs/useRunEvents";

function badgeClass(status: string): string {
  if (status === "succeeded") return "badge badge-ok";
  if (status === "failed") return "badge badge-error";
  if (status === "cancelled") return "badge badge-muted";
  if (status.startsWith("waiting_")) return "badge badge-warn";
  return "badge badge-pending";
}

const STAGE_STATE_LABEL: Record<StageRailState, string> = {
  completed: "已完成",
  current: "当前阶段",
  "waiting-current": "正在等待",
  failed: "在此停止",
  waiting: "尚未开始",
};

function sourceTitle(source: ReviewSource): string {
  const title = source.metadata_snapshot.title;
  return typeof title === "string" && title.trim() ? title : `arXiv ${source.arxiv_id}`;
}

function stepStatusLabel(status: string): string {
  if (status === "pending") return "尚未开始";
  if (status === "paused") return "已暂停";
  return statusLabel(status);
}

function runNotice(detail: ReviewDetail): { tone: string; title: string; text: string } {
  const { run, review } = detail;
  if (run.status === "waiting_input") return { tone: "waiting", title: "等待大纲确认", text: "Workflow 已安全暂停，不占用 Worker。请在下方批准、编辑或提交反馈。" };
  if (run.status === "waiting_dependency") return { tone: "waiting", title: "等待来源就绪", text: "系统正在等待所有已发现来源完成解析、索引或稳定失败后再固定证据边界。" };
  if (run.status === "retry_wait") return { tone: "waiting", title: "等待受限重试", text: "临时错误已记录，后台会按既定策略重新排队。" };
  if (run.status === "failed") {
    const code = typeof run.result_payload.error_code === "string" ? run.result_payload.error_code : "review_failed";
    return { tone: "failed", title: "Review 未完成", text: `稳定错误码：${code}` };
  }
  if (run.status === "cancelled" || run.status === "cancel_requested") return { tone: "stopped", title: run.status === "cancelled" ? "Review 已取消" : "正在安全取消", text: "已保存的来源、步骤和事件会保留；系统不会开始新的外部操作。" };
  if (run.status === "succeeded") return { tone: "ready", title: "Review 已完成", text: "Workflow 已提交结构化章节、引用与可下载 Artifact。" };
  return { tone: "active", title: stageLabel(review.current_stage), text: "后台正在推进当前固定阶段；页面刷新后仍会从 API 恢复最新事实。" };
}

export default function ReviewDetailPage() {
  const { projectId = "", runId = "" } = useParams();
  const queryClient = useQueryClient();
  const stream = useRunEvents(runId);

  const detailQuery = useQuery({
    queryKey: ["review", projectId, runId],
    queryFn: () => apiFetch<ReviewDetail>(`/api/v1/projects/${projectId}/reviews/${runId}`),
    refetchInterval: (query) => query.state.data && isTerminal(query.state.data.run.status) ? false : 5000,
  });
  const sourcesQuery = useQuery({
    queryKey: ["review-sources", projectId, runId],
    queryFn: () => apiFetch<ReviewSource[]>(`/api/v1/projects/${projectId}/reviews/${runId}/sources`),
  });

  useEffect(() => {
    if (stream.lastSequence === 0) return;
    void queryClient.invalidateQueries({ queryKey: ["review", projectId, runId] });
    void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["review-sources", projectId, runId] });
  }, [projectId, queryClient, runId, stream.lastSequence]);

  const cancelMutation = useMutation({
    mutationFn: () => apiFetch<{ status: string }>(`/api/v1/projects/${projectId}/reviews/${runId}/cancel`, { method: "POST" }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["review", projectId, runId] });
      void queryClient.invalidateQueries({ queryKey: ["reviews", projectId] });
    },
  });

  if (detailQuery.isError) {
    return <div className="page-flow"><PageBar breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "综述", to: `/projects/${projectId}/reviews` }]} title="Review 不可用" /><section className="notice"><p className="error-text">{errorMessage(detailQuery.error)}</p><Link to={`/projects/${projectId}/reviews`}>返回 Reviews</Link></section></div>;
  }
  if (!detailQuery.data) return <div className="page-flow"><PageBar breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "综述", to: `/projects/${projectId}/reviews` }]} title="正在读取 Review…" /><div className="skeleton-block">正在恢复 Review 事实…</div></div>;

  const detail = detailQuery.data;
  const { run, review, steps } = detail;
  const notice = runNotice(detail);
  const rail = reviewStageRail(review.current_stage, run.status);

  return (
    <div className="page-flow review-detail-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "综述", to: `/projects/${projectId}/reviews` }, { label: run.run_id.slice(0, 8) }]}
        title={review.research_question}
        actions={<div className="page-bar-action-group"><span className={badgeClass(run.status)}>{statusLabel(run.status)}</span><span className="page-bar-meta">当前：{stageLabel(review.current_stage)}</span><span className="mono page-bar-meta">review.v1</span>{isCancellable(run.status) ? <button className="danger" type="button" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>{cancelMutation.isPending ? "正在请求取消…" : "取消 Review"}</button> : null}</div>}
      />
      {cancelMutation.isError && <p className="notice error-text">{errorMessage(cancelMutation.error)}</p>}

      <section className={`review-state-notice ${notice.tone}`} aria-live="polite"><span className="status-dot" aria-hidden="true"/><div><strong>{notice.title}</strong><p>{notice.text}</p></div></section>

      <section className="stage-section" aria-labelledby="stage-title">
        <div className="section-title-row"><div><p className="eyebrow">STAGE RAIL</p><h2 id="stage-title">研究阶段脊柱</h2></div><span className="section-count">{String(rail.findIndex((item) => item.key === review.current_stage) + 1).padStart(2, "0")}/{rail.length}</span></div>
        <ol className="stage-rail">
          {rail.map((item) => <li key={item.key} className={`stage-${item.state}`} aria-current={item.state === "current" || item.state === "waiting-current" ? "step" : undefined}><span className="stage-node" aria-hidden="true"/><span><strong>{item.label}</strong><small>{STAGE_STATE_LABEL[item.state]}</small></span></li>)}
        </ol>
      </section>

      <div className="review-detail-grid">
        <section className="section-block" aria-labelledby="steps-title">
          <div className="section-title-row"><div><p className="eyebrow">PERSISTED STEPS</p><h2 id="steps-title">执行记录</h2></div><span className="section-count">{String(steps.length).padStart(2, "0")}</span></div>
          <ol className="review-step-list">
            {steps.map((step) => <li key={step.step_id}><span className={`step-state step-${step.status}`} aria-hidden="true"/><span><strong>{stageLabel(step.step_key)}</strong><small>{step.status === "failed" && step.error_code ? `${stepStatusLabel(step.status)} · ${step.error_code}` : stepStatusLabel(step.status)}</small></span><time>{step.completed_at ? new Date(step.completed_at).toLocaleString() : "—"}</time></li>)}
          </ol>
        </section>

        <section className="section-block" aria-labelledby="sources-title">
          <div className="section-title-row"><div><p className="eyebrow">ARXIV SOURCES</p><h2 id="sources-title">来源状态</h2></div><span className="section-count">{String(sourcesQuery.data?.length ?? 0).padStart(2, "0")}</span></div>
          {sourcesQuery.isError && <p className="notice error-text">{errorMessage(sourcesQuery.error)}</p>}
          {sourcesQuery.isPending && <p className="muted">正在读取来源…</p>}
          {sourcesQuery.data?.length === 0 && <div className="empty-state compact"><h3>尚未发现来源</h3><p>检索完成后，来源会按 arXiv 排名在这里出现。</p></div>}
          <ol className="review-source-list">
            {sourcesQuery.data?.map((source) => {
              const presentation = sourcePresentation(source.status);
              return <li key={source.source_id}><span className="source-rank">{String(source.rank).padStart(2, "0")}</span><span className="source-identity"><strong>{sourceTitle(source)}</strong><small>arXiv {source.arxiv_id} {source.arxiv_version}</small></span><span className={`source-status source-${presentation.tone}`}>{presentation.label}</span>{source.failure_code && <small className="source-failure">{source.failure_code}</small>}</li>;
            })}
          </ol>
        </section>
      </div>

      <ReviewResults
        projectId={projectId}
        runId={runId}
        request={detail.open_human_input_request}
        eventSequence={stream.lastSequence}
        citationsValidated={steps.some(
          (step) => step.step_key === "validate_sections" && step.status === "succeeded",
        )}
        canSubmitHumanInput={run.status === "waiting_input"}
      />

      {stream.events.length > 0 && <details className="review-event-details"><summary>最近事件（{stream.events.length}）</summary><ol>{stream.events.slice(-8).map((event) => <li key={event.sequence}><span className="mono">#{event.sequence}</span><strong>{event.event_type}</strong><time>{new Date(event.occurred_at).toLocaleTimeString()}</time></li>)}</ol></details>}
    </div>
  );
}
