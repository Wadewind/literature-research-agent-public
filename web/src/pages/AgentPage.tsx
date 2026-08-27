import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  AgentMessage,
  AgentSession,
  AgentSkill,
  AgentTurn,
  CitationSummary,
  McpCatalogEntry,
  McpProfile,
  McpProfileSelection,
  PostMessageResult,
  Project,
  ReviewListItem,
  ReviewOutput,
  Run,
  SkillProfile,
  SkillProfileSelection,
} from "../api/types";
import {
  ensureAgentMessageIntent,
  type AgentMessageIntent,
} from "../agent/messageIntent";
import { agentWorkspaceKey } from "../agent/interactionIdentity";
import {
  agentEventLabel,
  canSendAgentMessage,
  isSkillProfileLocked,
  isSessionInProject,
} from "../agent/presentation";
import AgentCapabilityPanel from "../components/AgentCapabilityPanel";
import AgentEvidenceMargin from "../components/AgentEvidenceMargin";
import AgentSessionRail from "../components/AgentSessionRail";
import ProjectNav from "../components/ProjectNav";
import { isCancellable, isTerminal, statusLabel } from "../runs/runStatus";
import { useRunEvents } from "../runs/useRunEvents";

interface CapabilityDraft {
  mcp: McpProfileSelection[] | null;
  skills: SkillProfileSelection[] | null;
}

function sameValue(left: unknown, right: unknown): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

interface AgentWorkspaceProps {
  projectId: string;
  sessionId: string;
}

function AgentWorkspace({ projectId, sessionId }: AgentWorkspaceProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [newSessionTitle, setNewSessionTitle] = useState("");
  const [content, setContent] = useState("");
  const [messageIntent, setMessageIntent] = useState<AgentMessageIntent | null>(null);
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);
  const [selectedReviewRunId, setSelectedReviewRunId] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<CitationSummary | null>(null);
  const [capabilityDraft, setCapabilityDraft] = useState<CapabilityDraft>({
    mcp: null,
    skills: null,
  });

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const sessionsQuery = useQuery({
    queryKey: ["agent-sessions", projectId],
    queryFn: () =>
      apiFetch<AgentSession[]>(`/api/v1/projects/${projectId}/agent-sessions`),
  });
  const sessionQuery = useQuery({
    queryKey: ["agent-session", sessionId],
    queryFn: () => apiFetch<AgentSession>(`/api/v1/agent-sessions/${sessionId}`),
    enabled: Boolean(sessionId),
    refetchInterval: (query) => query.state.data?.active_turn_run_id ? 2_000 : false,
  });
  const sessionMatchesRoute = Boolean(
    sessionQuery.data && isSessionInProject(sessionQuery.data, projectId),
  );
  const messagesQuery = useQuery({
    queryKey: ["agent-messages", sessionId],
    queryFn: () =>
      apiFetch<AgentMessage[]>(`/api/v1/agent-sessions/${sessionId}/messages`),
    enabled: Boolean(sessionId && sessionMatchesRoute),
  });
  const reviewsQuery = useQuery({
    queryKey: ["reviews", projectId],
    queryFn: () =>
      apiFetch<ReviewListItem[]>(`/api/v1/projects/${projectId}/reviews`),
  });
  const selectedMatrixQuery = useQuery({
    queryKey: ["review-matrix", projectId, selectedReviewRunId],
    queryFn: () =>
      apiFetch<ReviewOutput>(
        `/api/v1/projects/${projectId}/reviews/${selectedReviewRunId}/evidence-matrix`,
      ),
    enabled: Boolean(selectedReviewRunId),
  });
  const mcpCatalogQuery = useQuery({
    queryKey: ["agent-mcp-catalog"],
    queryFn: () => apiFetch<McpCatalogEntry[]>("/api/v1/agent-mcp-catalog"),
    enabled: Boolean(sessionId && sessionMatchesRoute),
  });
  const mcpProfileQuery = useQuery({
    queryKey: ["agent-mcp-profile", sessionId],
    queryFn: () => apiFetch<McpProfile>(`/api/v1/agent-sessions/${sessionId}/mcp-profile`),
    enabled: Boolean(sessionId && sessionMatchesRoute),
  });
  const skillCatalogQuery = useQuery({
    queryKey: ["agent-skills"],
    queryFn: () => apiFetch<AgentSkill[]>("/api/v1/agent-skills"),
    enabled: Boolean(sessionId && sessionMatchesRoute),
  });
  const skillProfileQuery = useQuery({
    queryKey: ["agent-skill-profile", sessionId],
    queryFn: () => apiFetch<SkillProfile>(`/api/v1/agent-sessions/${sessionId}/skill-profile`),
    enabled: Boolean(sessionId && sessionMatchesRoute),
  });

  const messages = messagesQuery.data ?? [];
  const latestMessage = messages.at(-1);
  const candidateTurnRunId =
    sessionMatchesRoute
      ? submittedRunId ?? sessionQuery.data?.active_turn_run_id ?? latestMessage?.turn_run_id
      : undefined;
  const runQuery = useQuery({
    queryKey: ["run", candidateTurnRunId],
    queryFn: () => apiFetch<Run>(`/api/v1/runs/${candidateTurnRunId}`),
    enabled: Boolean(candidateTurnRunId),
    refetchInterval: (query) =>
      query.state.data && !isTerminal(query.state.data.status) ? 2_000 : false,
  });
  const turnQuery = useQuery({
    queryKey: ["agent-turn", candidateTurnRunId],
    queryFn: () =>
      apiFetch<AgentTurn>(`/api/v1/agent-turn-runs/${candidateTurnRunId}`),
    enabled: Boolean(candidateTurnRunId),
  });
  const activeTurnRunId = candidateTurnRunId && !isTerminal(runQuery.data?.status ?? "")
    ? candidateTurnRunId
    : null;
  const eventStream = useRunEvents(activeTurnRunId ?? undefined);

  useEffect(() => {
    const terminal = runQuery.data ? isTerminal(runQuery.data.status) : eventStream.closed;
    if (!candidateTurnRunId || !terminal) return;
    setSubmittedRunId(null);
    void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-messages", sessionId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-turn", candidateTurnRunId] });
  }, [
    candidateTurnRunId,
    eventStream.closed,
    projectId,
    queryClient,
    runQuery.data,
    sessionId,
  ]);

  const createSessionMutation = useMutation({
    mutationFn: () =>
      apiFetch<AgentSession>(`/api/v1/projects/${projectId}/agent-sessions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newSessionTitle.trim() || null }),
      }),
    onSuccess: (created) => {
      setNewSessionTitle("");
      void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
      navigate(`/projects/${projectId}/agent/${created.session_id}`);
    },
  });

  const serverMcpSelections = mcpProfileQuery.data?.selections ?? [];
  const serverSkillSelections = skillProfileQuery.data?.selections ?? [];
  const mcpSelections = capabilityDraft.mcp ?? serverMcpSelections;
  const skillSelections = capabilityDraft.skills ?? serverSkillSelections;
  const mcpDirty = !sameValue(mcpSelections, serverMcpSelections);
  const skillDirty = !sameValue(skillSelections, serverSkillSelections);
  const capabilityMutation = useMutation({
    mutationFn: async () => {
      const requests: Promise<unknown>[] = [];
      if (mcpDirty && mcpProfileQuery.data) {
        requests.push(
          apiFetch<McpProfile>(`/api/v1/agent-sessions/${sessionId}/mcp-profile`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              expected_revision: mcpProfileQuery.data.revision,
              selections: mcpSelections,
            }),
          }),
        );
      }
      if (skillDirty && skillProfileQuery.data) {
        requests.push(
          apiFetch<SkillProfile>(`/api/v1/agent-sessions/${sessionId}/skill-profile`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              expected_revision: skillProfileQuery.data.revision,
              selections: skillSelections,
            }),
          }),
        );
      }
      const results = await Promise.allSettled(requests);
      const rejected = results.find(
        (result): result is PromiseRejectedResult => result.status === "rejected",
      );
      if (rejected) throw rejected.reason;
    },
    onSuccess: () => {
      setCapabilityDraft({ mcp: null, skills: null });
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: ["agent-mcp-profile", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["agent-skill-profile", sessionId] });
    },
  });

  const reviewOutputId =
    selectedMatrixQuery.data?.output_id ??
    (selectedReviewRunId ? "" : turnQuery.data?.review_output_id ?? "");
  const postMessageMutation = useMutation({
    mutationFn: (intent: AgentMessageIntent) =>
      apiFetch<PostMessageResult>(`/api/v1/agent-sessions/${sessionId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": intent.key,
        },
        body: JSON.stringify({
          content: intent.content,
          review_output_id: intent.reviewOutputId,
        }),
      }),
    onSuccess: (result) => {
      setSubmittedRunId(result.run_id);
      setContent("");
      setMessageIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["agent-messages", sessionId] });
    },
  });
  const cancelMutation = useMutation({
    mutationFn: () =>
      apiFetch<{ status: string }>(`/api/v1/runs/${activeTurnRunId}/cancel`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["run", activeTurnRunId] });
    },
  });

  const submitMessage = (event: FormEvent) => {
    event.preventDefault();
    const normalized = content.trim();
    if (
      !canSendAgentMessage(
        normalized,
        reviewOutputId,
        activeTurnRunId,
        postMessageMutation.isPending || mcpDirty || skillDirty,
      )
    ) return;
    const intent = ensureAgentMessageIntent(
      messageIntent,
      normalized,
      reviewOutputId,
      () => crypto.randomUUID(),
    );
    setMessageIntent(intent);
    postMessageMutation.mutate(intent);
  };

  const handleMcpToggle = (entry: McpCatalogEntry, enabled: boolean) => {
    setCapabilityDraft((current) => {
      const values = current.mcp ?? serverMcpSelections;
      return {
        ...current,
        mcp: enabled
          ? [...values, { catalog_id: entry.catalog_id, version: entry.version, parameters: {} }]
          : values.filter((item) => item.catalog_id !== entry.catalog_id),
      };
    });
  };
  const handleMcpParameter = (catalogId: string, name: string, value: string) => {
    setCapabilityDraft((current) => ({
      ...current,
      mcp: (current.mcp ?? serverMcpSelections).map((selection) =>
        selection.catalog_id === catalogId
          ? { ...selection, parameters: { ...selection.parameters, [name]: value } }
          : selection,
      ),
    }));
  };
  const handleSkillToggle = (skill: AgentSkill, enabled: boolean) => {
    setCapabilityDraft((current) => {
      const values = current.skills ?? serverSkillSelections;
      return {
        ...current,
        skills: enabled
          ? [...values, { source: skill.source, skill_id: skill.skill_id, version: skill.version }]
          : values.filter(
              (item) => item.source !== skill.source ||
                item.skill_id !== skill.skill_id ||
                item.version !== skill.version,
            ),
      };
    });
  };

  const sessionProjectMismatch = Boolean(
    sessionId && sessionQuery.data && !sessionMatchesRoute,
  );
  if (projectQuery.isError || sessionsQuery.isError || sessionQuery.isError || sessionProjectMismatch) {
    return (
      <section className="notice">
        <p className="error-text">
          {sessionProjectMismatch
            ? "资源不存在或无权访问"
            : errorMessage(projectQuery.error ?? sessionsQuery.error ?? sessionQuery.error)}
        </p>
        <Link to="/">返回项目</Link>
      </section>
    );
  }

  const project = projectQuery.data;
  const skillLocked = isSkillProfileLocked(messages.length);
  const researchEvents = eventStream.events.flatMap((event) => {
    const label = agentEventLabel(event.event_type);
    return label ? [{ ...event, label }] : [];
  });
  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const succeededReviews = reviewsQuery.data?.filter((review) => review.status === "succeeded") ?? [];
  const capabilityLoading = mcpCatalogQuery.isPending || mcpProfileQuery.isPending ||
    skillCatalogQuery.isPending || skillProfileQuery.isPending;
  const capabilityError = capabilityMutation.isError
    ? errorMessage(capabilityMutation.error)
    : mcpCatalogQuery.isError || mcpProfileQuery.isError ||
        skillCatalogQuery.isError || skillProfileQuery.isError
      ? errorMessage(
          mcpCatalogQuery.error ?? mcpProfileQuery.error ??
          skillCatalogQuery.error ?? skillProfileQuery.error,
        )
      : null;

  return (
    <div className="page-flow agent-page">
      <header className="project-heading agent-heading">
        <div>
          <p className="breadcrumb"><Link to="/">研究项目</Link><span>/</span>研究助手</p>
          <p className="eyebrow">PROJECT-SCOPED RESEARCH AGENT</p>
          <h1>{project?.name ?? "正在读取…"}</h1>
          <p>在同一研究会话中持续分析项目论文，并把每轮执行、证据与候选成果保留为平台事实。</p>
        </div>
        <div className="metric-block"><strong>{sessionsQuery.data?.length ?? "—"}</strong><span>Agent Sessions</span></div>
      </header>
      <ProjectNav projectId={projectId} active="agent" />

      <div className="agent-workspace">
        <AgentSessionRail
          projectId={projectId}
          sessions={sessionsQuery.data}
          activeSessionId={sessionId}
          title={newSessionTitle}
          pending={createSessionMutation.isPending}
          error={createSessionMutation.isError ? errorMessage(createSessionMutation.error) : null}
          onTitleChange={setNewSessionTitle}
          onCreate={() => createSessionMutation.mutate()}
        />

        <section className="agent-conversation" aria-label="研究助手对话">
          {!sessionId ? (
            <div className="agent-welcome">
              <span className="agent-welcome-mark" aria-hidden="true">A·01</span>
              <p className="eyebrow">START A RESEARCH THREAD</p>
              <h2>让项目材料成为持续研究上下文</h2>
              <p>创建会话后，先选择一次 Evidence Matrix，并按需启用平台维护的研究能力。</p>
            </div>
          ) : (
            <>
              <header className="agent-chat-heading">
                <div><p className="eyebrow">CONTINUOUS RESEARCH</p><h2>{sessionQuery.data?.title || "未命名研究会话"}</h2></div>
                <span className={activeTurnRunId ? "badge badge-pending" : "badge badge-ok"}>
                  {activeTurnRunId ? statusLabel(runQuery.data?.status ?? "queued") : "可继续"}
                </span>
              </header>

              <AgentCapabilityPanel
                mcpCatalog={mcpCatalogQuery.data ?? []}
                mcpSelections={mcpSelections}
                skillCatalog={skillCatalogQuery.data ?? []}
                skillSelections={skillSelections}
                skillLocked={skillLocked}
                mcpDirty={mcpDirty}
                skillDirty={skillDirty}
                pending={capabilityMutation.isPending}
                loading={capabilityLoading}
                error={capabilityError}
                onMcpToggle={handleMcpToggle}
                onMcpParameter={handleMcpParameter}
                onSkillToggle={handleSkillToggle}
                onSave={() => capabilityMutation.mutate()}
              />

              {activeTurnRunId && (
                <section className="agent-turn-progress" aria-live="polite">
                  <span className="progress-pulse" aria-hidden="true" />
                  <div>
                    <strong>{researchEvents.at(-1)?.label ?? "研究任务正在执行"}</strong>
                    <small>Run {activeTurnRunId.slice(0, 8)} · {researchEvents.length} 条筛选后活动</small>
                  </div>
                  {isCancellable(runQuery.data?.status ?? "queued") && (
                    <button className="danger" type="button" disabled={cancelMutation.isPending} onClick={() => cancelMutation.mutate()}>
                      {cancelMutation.isPending ? "正在停止…" : "停止本轮"}
                    </button>
                  )}
                </section>
              )}
              {(postMessageMutation.isError || cancelMutation.isError) && (
                <p className="notice error-text">{errorMessage(postMessageMutation.error ?? cancelMutation.error)}</p>
              )}

              <div className="agent-message-timeline" aria-live="polite">
                {messagesQuery.isPending && <p className="muted">正在恢复消息历史…</p>}
                {messagesQuery.isError && <p className="error-text">{errorMessage(messagesQuery.error)}</p>}
                {messages.length === 0 && (
                  <div className="agent-empty-turn"><h3>准备第一轮研究</h3><p>选择 Evidence Matrix，然后提出一个需要比较、综合或验证的研究问题。</p></div>
                )}
                {messages.map((message) => (
                  <article className={`message message-${message.role}`} key={message.message_id}>
                    <header><strong>{message.role === "user" ? "你" : "研究助手"}</strong><time>{new Date(message.created_at).toLocaleTimeString()}</time></header>
                    <p>{message.content}</p>
                    {message.claims?.map((claim, claimIndex) => (
                      <div className="agent-inline-claim" key={`${message.message_id}:${claimIndex}`}>
                        <span className="mono">E{String(claimIndex + 1).padStart(2, "0")}</span>
                        {claim.citations.map((citation) => (
                          <button key={citation.evidence_id} type="button" className="citation-marker" onClick={() => setSelectedEvidence(citation)}>
                            p.{citation.page_start ?? "?"}
                          </button>
                        ))}
                      </div>
                    ))}
                  </article>
                ))}
              </div>

              {researchEvents.length > 0 && (
                <details className="agent-research-ledger">
                  <summary>研究活动 · {researchEvents.length} 步</summary>
                  <ol>{researchEvents.map((event) => <li key={event.sequence}><span className="mono">{String(event.sequence).padStart(2, "0")}</span><span>{event.label}</span><time>{new Date(event.occurred_at).toLocaleTimeString()}</time></li>)}</ol>
                </details>
              )}

              <form className="agent-composer" onSubmit={submitMessage}>
                <label htmlFor="agent-matrix">本轮 Evidence Matrix</label>
                <select id="agent-matrix" value={selectedReviewRunId} onChange={(event) => { setSelectedReviewRunId(event.target.value); setMessageIntent(null); }} disabled={Boolean(activeTurnRunId)}>
                  <option value="">{turnQuery.data?.review_output_id ? "沿用上一轮 Evidence Matrix" : "请选择已完成的综述"}</option>
                  {succeededReviews.map((review) => <option key={review.run_id} value={review.run_id}>{review.research_question}</option>)}
                </select>
                {selectedReviewRunId && selectedMatrixQuery.isPending && <small>正在验证 Evidence Matrix…</small>}
                {reviewsQuery.isError && <small className="error-text">{errorMessage(reviewsQuery.error)}</small>}
                {selectedMatrixQuery.isError && <small className="error-text">{errorMessage(selectedMatrixQuery.error)}</small>}
                <label htmlFor="agent-message">研究消息</label>
                <textarea id="agent-message" rows={5} maxLength={16_000} value={content} onChange={(event) => { setContent(event.target.value); if (messageIntent && event.target.value.trim() !== messageIntent.content) setMessageIntent(null); }} disabled={Boolean(activeTurnRunId)} placeholder="例如：基于这些证据，比较各研究的方法差异并指出尚未解决的研究缺口。" />
                <div>
                  <small>{skillLocked ? "研究方法已锁定" : "发送首条消息后将锁定研究方法"} · 每条消息创建独立 Turn</small>
                  <button type="submit" disabled={!canSendAgentMessage(content, reviewOutputId, activeTurnRunId, postMessageMutation.isPending || mcpDirty || skillDirty)}>{postMessageMutation.isPending ? "正在提交…" : "开始本轮研究"}</button>
                </div>
              </form>
            </>
          )}
        </section>

        <AgentEvidenceMargin
          projectId={projectId}
          turn={turnQuery.data}
          matrix={selectedMatrixQuery.data}
          assistantMessages={assistantMessages}
          selectedEvidence={selectedEvidence}
          onSelectEvidence={setSelectedEvidence}
          onClearEvidence={() => setSelectedEvidence(null)}
        />
      </div>
    </div>
  );
}

export default function AgentPage() {
  const { projectId = "", sessionId = "" } = useParams();
  return (
    <AgentWorkspace
      key={agentWorkspaceKey(projectId, sessionId)}
      projectId={projectId}
      sessionId={sessionId}
    />
  );
}
