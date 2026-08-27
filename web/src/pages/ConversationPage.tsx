/** RAG 对话页：消息、Run 进度、Evidence 详情与 PDF 页码回跳。 */

import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  CitationSummary,
  Conversation,
  ConversationMessage,
  EvidenceDetail,
  PostMessageResult,
  Project,
} from "../api/types";
import ChatWorkspaceFrame from "../components/ChatWorkspaceFrame";
import ConversationRail, { conversationScopeLabel } from "../components/ConversationRail";
import ProjectWorkspaceHeader from "../components/ProjectWorkspaceHeader";
import {
  ensureMessageIntent,
  type MessageIntent,
} from "../conversations/messageIntent";
import { useRunEvents } from "../runs/useRunEvents";
import {
  canInteractWithConversation,
  isConversationInProject,
} from "../workspace/projectWorkspace";

const PROGRESS_LABELS: Record<string, string> = {
  run_created: "回答任务已创建",
  run_started: "Worker 已开始处理",
  retrieval_started: "正在检索项目证据",
  retrieval_completed: "证据检索完成",
  model_generation_started: "正在组织有引用的回答",
  model_generation_completed: "回答草稿已生成",
  citation_validation_completed: "引用完整性校验完成",
  answer_committed: "回答与引用已保存",
  run_requeued: "临时故障，等待重试",
  run_cancel_requested: "正在取消回答任务",
  run_failed: "回答任务失败",
  run_cancelled: "回答任务已取消",
};

function pageLabel(citation: CitationSummary): string {
  if (citation.page_start === null) return "页码未知";
  if (citation.page_end && citation.page_end !== citation.page_start) {
    return `第 ${citation.page_start}–${citation.page_end} 页`;
  }
  return `第 ${citation.page_start} 页`;
}

function ConversationWorkspace() {
  const { projectId = "", conversationId = "" } = useParams();
  const queryClient = useQueryClient();
  const [content, setContent] = useState("");
  const [intent, setIntent] = useState<MessageIntent | null>(null);
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);
  const [selectedEvidenceId, setSelectedEvidenceId] = useState<string | null>(null);
  const [pdfPage, setPdfPage] = useState<number | null>(null);

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const conversationsQuery = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () =>
      apiFetch<Conversation[]>(`/api/v1/projects/${projectId}/conversations`),
  });
  const conversationQuery = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => apiFetch<Conversation>(`/api/v1/conversations/${conversationId}`),
  });
  const conversationMatchesRoute = Boolean(
    conversationQuery.data && isConversationInProject(conversationQuery.data, projectId),
  );
  const canInteract = canInteractWithConversation(
    projectQuery.data,
    conversationQuery.data,
    projectId,
  );
  const messagesQuery = useQuery({
    queryKey: ["conversation-messages", conversationId],
    queryFn: () =>
      apiFetch<ConversationMessage[]>(
        `/api/v1/conversations/${conversationId}/messages`,
      ),
    enabled: canInteract,
  });
  const activeRunId = canInteract
    ? submittedRunId ?? conversationQuery.data?.active_run_id ?? undefined
    : undefined;
  const stream = useRunEvents(activeRunId);

  const evidenceQuery = useQuery({
    queryKey: ["evidence", projectId, selectedEvidenceId],
    queryFn: () =>
      apiFetch<EvidenceDetail>(
        `/api/v1/projects/${projectId}/evidence/${selectedEvidenceId}`,
      ),
    enabled: selectedEvidenceId !== null && canInteract,
  });

  useEffect(() => {
    const hasSettled = stream.events.some((event) =>
      ["answer_committed", "run_failed", "run_cancelled"].includes(event.event_type),
    );
    if (!hasSettled) return;
    void queryClient.invalidateQueries({ queryKey: ["conversation-messages", conversationId] });
    void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
    void queryClient.invalidateQueries({ queryKey: ["conversations", projectId] });
  }, [conversationId, projectId, queryClient, stream.events]);

  useEffect(() => {
    const page = evidenceQuery.data?.page_start ?? null;
    setPdfPage(page);
  }, [evidenceQuery.data]);

  const postMutation = useMutation({
    mutationFn: (input: MessageIntent) => {
      if (!canInteract) {
        return Promise.reject(new Error("资源闭包尚未确认，暂时无法发送问题"));
      }
      return apiFetch<PostMessageResult>(`/api/v1/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": input.key,
        },
        body: JSON.stringify({ content: input.content }),
      });
    },
    onSuccess: (result) => {
      setSubmittedRunId(result.run_id);
      setContent("");
      setIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["conversation-messages", conversationId] });
      void queryClient.invalidateQueries({ queryKey: ["conversation", conversationId] });
    },
  });

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (!canInteract) return;
    const question = content.trim();
    if (!question) return;
    const nextIntent = ensureMessageIntent(intent, question, () => crypto.randomUUID());
    setIntent(nextIntent);
    postMutation.mutate(nextIntent);
  };

  if (
    conversationQuery.isError ||
    projectQuery.isError ||
    (conversationQuery.data && !conversationMatchesRoute)
  ) {
    return (
      <section className="panel">
        <p className="error-text">
          {conversationQuery.data && !conversationMatchesRoute
            ? "资源不存在或无权访问"
            : errorMessage(conversationQuery.error ?? projectQuery.error)}
        </p>
        <Link to={`/projects/${projectId}/chat`}>返回文献问答</Link>
      </section>
    );
  }

  const conversation = conversationQuery.data;
  const project = projectQuery.data;
  const lastProgress = stream.events.at(-1);
  const busy = Boolean(activeRunId) && !stream.closed;
  const evidence = evidenceQuery.data;
  const fileUrl = evidence
    ? `/api/v1/projects/${projectId}/paper-versions/${evidence.version_id}/file`
    : null;

  return (
    <div className="viewport-workspace-page chat-page">
      <ProjectWorkspaceHeader
        projectId={projectId}
        project={project}
        active="chat"
        eyebrow="文献问答"
        description="每个问题独立检索固定范围，并沿引用回到 Evidence 与原文。"
        actions={<span className="workspace-context-chip">{conversation ? conversationScopeLabel(conversation) : "读取范围…"}</span>}
      />
      <ChatWorkspaceFrame
        rail={
          <ConversationRail
            projectId={projectId}
            conversations={conversationsQuery.data}
            activeConversationId={conversationId}
            error={conversationsQuery.isError ? errorMessage(conversationsQuery.error) : null}
          />
        }
        conversation={<main className="conversation-main">
        <header className="chat-heading">
          <div>
            <p className="eyebrow">CITED RAG / {conversation ? conversationScopeLabel(conversation) : "读取中"}</p>
            <h1>{conversation?.title || "新对话"}</h1>
            <p>{project?.name} · 回答中的每个段落都必须绑定已验证 Evidence。</p>
          </div>
          {project?.archived_at && <span className="badge badge-warn">已归档 · 只读</span>}
        </header>

        {busy && (
          <section className="rag-progress" aria-live="polite">
            <span className="progress-pulse" aria-hidden="true" />
            <div>
              <strong>
                {lastProgress
                  ? PROGRESS_LABELS[lastProgress.event_type] ?? lastProgress.event_type
                  : "等待回答任务开始"}
              </strong>
              <small>Run {activeRunId?.slice(0, 8)} · 已收到 {stream.events.length} 个事件</small>
            </div>
            {activeRunId && <Link to={`/runs/${activeRunId}`}>查看 Run</Link>}
          </section>
        )}

        <section className="message-timeline" aria-label="消息时间线">
          {messagesQuery.isPending && <p className="muted">正在恢复消息…</p>}
          {messagesQuery.isError && <p className="error-text">{errorMessage(messagesQuery.error)}</p>}
          {messagesQuery.data?.length === 0 && (
            <div className="empty-state compact">
              <h2>从一个可验证的问题开始</h2>
              <p>系统只使用当前范围内已完成索引的文献，并在证据不足时明确说明。</p>
            </div>
          )}
          {messagesQuery.data?.map((message) => (
            <article key={message.message_id} className={`message message-${message.role}`}>
              <header>
                <strong>{message.role === "user" ? "你" : "Literature Atlas"}</strong>
                <time dateTime={message.created_at}>
                  {new Date(message.created_at).toLocaleString()}
                </time>
              </header>
              {message.role === "assistant" && message.claims ? (
                <div className="claim-list">
                  {message.claims.map((claim, claimIndex) => (
                    <p key={`${message.message_id}-${claimIndex}`}>
                      {claim.text}{" "}
                      <span className="citation-cluster">
                        {claim.citations.map((citation, citationIndex) => (
                          <button
                            key={citation.evidence_id}
                            type="button"
                            className="citation-marker"
                            title={`${citation.section_path ?? "未标章节"} · ${pageLabel(citation)}`}
                            onClick={() => setSelectedEvidenceId(citation.evidence_id)}
                          >
                            [{claimIndex + 1}.{citationIndex + 1}]
                          </button>
                        ))}
                      </span>
                    </p>
                  ))}
                </div>
              ) : (
                <p>{message.content}</p>
              )}
            </article>
          ))}
        </section>

        <form className="question-composer" onSubmit={onSubmit}>
          <label htmlFor="rag-question">继续提问</label>
          <textarea
            id="rag-question"
            value={content}
            onChange={(event) => setContent(event.target.value)}
            placeholder="提出一个需要文献证据回答的问题…"
            rows={3}
            maxLength={4000}
            disabled={!canInteract || busy || Boolean(project?.archived_at)}
          />
          <div>
            <small>
              {!canInteract
                ? "正在确认 Project 与对话范围…"
                : busy
                  ? "当前对话一次只处理一个问题"
                  : "Enter 换行；提交后可实时跟随检索与引用校验"}
            </small>
            <button
              type="submit"
              disabled={
                !canInteract ||
                busy ||
                postMutation.isPending ||
                !content.trim() ||
                Boolean(project?.archived_at)
              }
            >
              {postMutation.isPending ? "正在提交…" : "发送问题"}<span aria-hidden="true">→</span>
            </button>
          </div>
          {postMutation.isError && (
            <p className="error-text">{errorMessage(postMutation.error)}</p>
          )}
        </form>
      </main>}
        evidence={<aside className={`evidence-drawer ${selectedEvidenceId ? "open" : ""}`} aria-live="polite">
        <header>
          <div><p className="eyebrow">EVIDENCE TRACE</p><h2>来源证据</h2></div>
          {selectedEvidenceId && (
            <button type="button" className="button-plain" onClick={() => setSelectedEvidenceId(null)}>关闭</button>
          )}
        </header>
        {!selectedEvidenceId && <p className="muted">点击回答后的引用标记，查看摘录、章节与 PDF 页码。</p>}
        {evidenceQuery.isPending && <p className="muted">正在读取 Evidence…</p>}
        {evidenceQuery.isError && <p className="error-text">{errorMessage(evidenceQuery.error)}</p>}
        {evidence && (
          <>
            <dl className="evidence-meta">
              <div><dt>章节</dt><dd>{evidence.section_path ?? "未标章节"}</dd></div>
              <div><dt>页码</dt><dd>{pageLabel(evidence)}</dd></div>
              <div><dt>Paper</dt><dd className="mono">{evidence.paper_id.slice(0, 8)}</dd></div>
            </dl>
            <blockquote>{evidence.excerpt}</blockquote>
            {fileUrl && (
              <>
                <div className="evidence-pdf-actions">
                  <strong>来源 PDF</strong>
                  {evidence.page_start !== null && (
                    <button type="button" className="button-quiet" onClick={() => setPdfPage(evidence.page_start)}>跳到第 {evidence.page_start} 页</button>
                  )}
                </div>
                <iframe
                  key={pdfPage ?? "cover"}
                  title="Evidence 来源 PDF"
                  className="pdf-frame evidence-pdf"
                  src={pdfPage === null ? fileUrl : `${fileUrl}#page=${pdfPage}`}
                />
              </>
            )}
          </>
        )}
      </aside>}
      />
    </div>
  );
}

export default function ConversationPage() {
  const { projectId = "", conversationId = "" } = useParams();
  return <ConversationWorkspace key={`${projectId}:${conversationId}`} />;
}
