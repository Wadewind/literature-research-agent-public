/** Run 详情页：SSE 实时事件时间线、状态徽标与取消。 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { Run } from "../api/types";
import { useRunEvents } from "../runs/useRunEvents";
import { isCancellable, isTerminal, statusLabel } from "../runs/runStatus";

function statusBadgeClass(status: string): string {
  if (status === "succeeded") return "badge badge-ok";
  if (status === "failed") return "badge badge-error";
  if (status === "cancelled") return "badge badge-muted";
  return "badge badge-pending";
}

export default function RunDetailPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const stream = useRunEvents(runId);

  const runQuery = useQuery({
    queryKey: ["run", runId],
    queryFn: () => apiFetch<Run>(`/api/v1/runs/${runId}`),
    // 未达终态时轮询兜底（SSE 只携带事件，不携带最终状态字段）
    refetchInterval: (query) =>
      query.state.data && isTerminal(query.state.data.status) ? false : 2000,
  });

  const cancelMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/api/v1/runs/${runId}/cancel`, { method: "POST" }),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", runId] });
    },
  });

  const run = runQuery.data;
  const versionId =
    run && typeof run.input_payload.version_id === "string"
      ? run.input_payload.version_id
      : null;

  if (runQuery.isError) {
    return (
      <section className="panel">
        <p className="error-text">{errorMessage(runQuery.error)}</p>
        <Link to="/">返回 Project 列表</Link>
      </section>
    );
  }

  return (
    <div className="stack">
      <section className="panel">
        <p className="breadcrumb">
          {run && <Link to={`/projects/${run.project_id}`}>文献库</Link>} / Run{" "}
          <span className="mono">{runId.slice(0, 8)}</span>
        </p>
        <h1>
          导入 Run <span className="mono muted">{runId.slice(0, 8)}</span>
        </h1>
        {run && (
          <div className="run-meta">
            <span className={statusBadgeClass(run.status)}>{statusLabel(run.status)}</span>
            <span className="muted">
              类型 {run.run_type} · 事件 {stream.lastSequence} 条 ·{" "}
              {stream.closed ? "流已收束" : "实时跟随中"}
            </span>
            {isCancellable(run.status) && (
              <button
                type="button"
                className="danger"
                onClick={() => cancelMutation.mutate()}
                disabled={cancelMutation.isPending}
              >
                {cancelMutation.isPending ? "取消中…" : "取消 Run"}
              </button>
            )}
          </div>
        )}
        {cancelMutation.isError && (
          <p className="error-text">{errorMessage(cancelMutation.error)}</p>
        )}
        {run?.status === "failed" && (
          <p className="error-text">
            Run 失败：
            {typeof run.result_payload.error === "string"
              ? run.result_payload.error
              : "详见事件时间线中的错误记录"}
          </p>
        )}
        {run?.status === "succeeded" && versionId && (
          <p className="success-text">
            解析完成：
            <Link to={`/projects/${run.project_id}/versions/${versionId}/document`}>
              查看文档结构预览
            </Link>
          </p>
        )}
      </section>

      <section className="panel">
        <h2>事件时间线</h2>
        {stream.events.length === 0 && <p className="muted">等待事件…</p>}
        <ol className="timeline">
          {stream.events.map((event) => (
            <li key={event.sequence} className="timeline-item">
              <span className="mono timeline-seq">{String(event.sequence).padStart(3, "0")}</span>
              <span className="timeline-type">{event.event_type}</span>
              <span className="muted">{new Date(event.occurred_at).toLocaleTimeString()}</span>
              {Object.keys(event.payload).length > 0 && (
                <code className="timeline-payload">{JSON.stringify(event.payload)}</code>
              )}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
