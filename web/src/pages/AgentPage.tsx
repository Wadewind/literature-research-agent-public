import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type {
  AgentMessage,
  AgentAttachment,
  AgentArtifact,
  AgentArtifactManifest,
  AgentSession,
  AgentSkill,
  AgentToolExecutionsResponse,
  AgentTurn,
  CitationSummary,
  McpCatalogEntry,
  McpProfile,
  McpProfileSelection,
  PostMessageResult,
  Project,
  ProjectAgentContextSummary,
  ReviewListItem,
  ReviewOutput,
  Run,
  RunEvent,
  SkillProfile,
  SkillProfileSelection,
} from "../api/types";
import {
  ensureAgentMessageIntent,
  type AgentMessageIntent,
} from "../agent/messageIntent";
import {
  ensureAgentAttachmentUploadIntent,
  type AgentAttachmentUploadIntent,
} from "../agent/attachmentUploadIntent";
import { canQueryAgentAttachments } from "../agent/sessionQueries";
import { agentWorkspaceKey } from "../agent/interactionIdentity";
import { eligibleEvidenceMatrices } from "../agent/matrixEligibility";
import { groupMessagesByTurn, mergeRunEvents } from "../agent/turnTimeline";
import {
  agentEventLabel,
  agentTurnFailureSummary,
  canInteractWithAgentSession,
  canSendAgentMessage,
  isSkillProfileLocked,
  isSessionInProject,
} from "../agent/presentation";
import AgentCapabilityPanel from "../components/AgentCapabilityPanel";
import AgentAttachmentComposer from "../components/AgentAttachmentComposer";
import AgentBrowserPanel from "../components/AgentBrowserPanel";
import AgentEvidenceMargin from "../components/AgentEvidenceMargin";
import AgentInspector, { type AgentInspectorTab } from "../components/AgentInspector";
import AgentResearchActivity from "../components/AgentResearchActivity";
import AgentTurnOutputs from "../components/AgentTurnOutputs";
import ConversationMessage from "../components/ConversationMessage";
import PageBar from "../components/PageBar";
import ResearchWorkspaceFrame from "../components/ResearchWorkspaceFrame";
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

interface AgentMessageItemProps {
  message: AgentMessage;
  onCitation: (citation: CitationSummary) => void;
}

function AgentMessageItem({ message, onCitation }: AgentMessageItemProps) {
  return (
    <ConversationMessage role={message.role} createdAt={message.created_at}>
      <p>{message.content}</p>
      {message.claims?.map((claim, claimIndex) => (
        <div className="agent-inline-claim" key={`${message.message_id}:${claimIndex}`}>
          <span className="mono">E{String(claimIndex + 1).padStart(2, "0")}</span>
          {claim.citations.map((citation) => (
            <button
              key={citation.evidence_id}
              type="button"
              className="citation-marker"
              onClick={() => onCitation(citation)}
            >
              p.{citation.page_start ?? "?"}
            </button>
          ))}
        </div>
      ))}
    </ConversationMessage>
  );
}

function AgentWorkspace({ projectId, sessionId }: AgentWorkspaceProps) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const [newSessionTitle, setNewSessionTitle] = useState("");
  const [content, setContent] = useState("");
  const [messageIntent, setMessageIntent] = useState<AgentMessageIntent | null>(null);
  const [uploadIntent, setUploadIntent] = useState<AgentAttachmentUploadIntent | null>(null);
  const [selectedAttachmentIds, setSelectedAttachmentIds] = useState<string[]>([]);
  const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);
  const [selectedReviewRunId, setSelectedReviewRunId] = useState("");
  const [selectedEvidence, setSelectedEvidence] = useState<CitationSummary | null>(null);
  const [capabilityDraft, setCapabilityDraft] = useState<CapabilityDraft>({
    mcp: null,
    skills: null,
  });
  const inspectorParam = searchParams.get("inspector");
  const inspectorTab: AgentInspectorTab =
    inspectorParam === "browser" || inspectorParam === "outputs"
      ? inspectorParam
      : "evidence";
  const inspectorOpen = inspectorParam === "evidence" ||
    inspectorParam === "browser" || inspectorParam === "outputs";

  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const uploadAttachmentMutation = useMutation({
    mutationFn: ({ file, intent }: { file: File; intent: AgentAttachmentUploadIntent }) => {
      const body = new FormData();
      body.append("file", file);
      return apiFetch<AgentAttachment>(`/api/v1/agent-sessions/${sessionId}/attachments`, {
        method: "POST",
        headers: { "Idempotency-Key": intent.key },
        body,
      });
    },
    onSuccess: (attachment) => {
      setUploadIntent(null);
      setSelectedAttachmentIds((current) =>
        current.length < 5 && !current.includes(attachment.attachment_id)
          ? [...current, attachment.attachment_id]
          : current,
      );
      setMessageIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["agent-attachments", sessionId] });
    },
  });
  const deleteAttachmentMutation = useMutation({
    mutationFn: (attachmentId: string) =>
      apiFetch<void>(`/api/v1/agent-sessions/${sessionId}/attachments/${attachmentId}`, {
        method: "DELETE",
      }),
    onSuccess: (_, attachmentId) => {
      setSelectedAttachmentIds((current) => current.filter((value) => value !== attachmentId));
      setMessageIntent(null);
      void queryClient.invalidateQueries({ queryKey: ["agent-attachments", sessionId] });
    },
  });
  const sessionsQuery = useQuery({
    queryKey: ["agent-sessions", projectId],
    queryFn: () =>
      apiFetch<AgentSession[]>(`/api/v1/projects/${projectId}/agent-sessions`),
  });
  const projectContextQuery = useQuery({
    queryKey: ["agent-context-summary", projectId],
    queryFn: () => apiFetch<ProjectAgentContextSummary>(
      `/api/v1/projects/${projectId}/agent-context-summary`,
    ),
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
  const canInteract = canInteractWithAgentSession(
    projectQuery.data,
    sessionQuery.data,
    projectId,
  );
  const attachmentsQuery = useQuery({
    queryKey: ["agent-attachments", sessionId],
    queryFn: () => apiFetch<AgentAttachment[]>(`/api/v1/agent-sessions/${sessionId}/attachments`),
    enabled: canQueryAgentAttachments(sessionId, canInteract),
  });
  const messagesQuery = useQuery({
    queryKey: ["agent-messages", sessionId],
    queryFn: () =>
      apiFetch<AgentMessage[]>(`/api/v1/agent-sessions/${sessionId}/messages`),
    enabled: Boolean(sessionId && canInteract),
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
    enabled: Boolean(sessionId && canInteract),
  });
  const mcpProfileQuery = useQuery({
    queryKey: ["agent-mcp-profile", sessionId],
    queryFn: () => apiFetch<McpProfile>(`/api/v1/agent-sessions/${sessionId}/mcp-profile`),
    enabled: Boolean(sessionId && canInteract),
  });
  const skillCatalogQuery = useQuery({
    queryKey: ["agent-skills"],
    queryFn: () => apiFetch<AgentSkill[]>("/api/v1/agent-skills"),
    enabled: Boolean(sessionId && canInteract),
  });
  const skillProfileQuery = useQuery({
    queryKey: ["agent-skill-profile", sessionId],
    queryFn: () => apiFetch<SkillProfile>(`/api/v1/agent-sessions/${sessionId}/skill-profile`),
    enabled: Boolean(sessionId && canInteract),
  });

  const messages = useMemo(() => messagesQuery.data ?? [], [messagesQuery.data]);
  const latestMessage = messages.at(-1);
  const candidateTurnRunId =
    canInteract
      ? submittedRunId ?? sessionQuery.data?.active_turn_run_id ?? latestMessage?.turn_run_id
      : undefined;
  const activityRunIds = useMemo(() => {
    const runIds = [...new Set(messages.map((message) => message.turn_run_id))];
    if (candidateTurnRunId && !runIds.includes(candidateTurnRunId)) runIds.push(candidateTurnRunId);
    return runIds;
  }, [candidateTurnRunId, messages]);
  const turnGroups = useMemo(
    () => groupMessagesByTurn(messages, candidateTurnRunId),
    [candidateTurnRunId, messages],
  );
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
  const artifactsQuery = useQuery({
    queryKey: ["agent-artifacts", candidateTurnRunId],
    queryFn: () =>
      apiFetch<AgentArtifact[]>(`/api/v1/agent-turn-runs/${candidateTurnRunId}/artifacts`),
    enabled: Boolean(candidateTurnRunId),
  });
  const toolExecutionQueries = useQueries({
    queries: activityRunIds.map((runId) => ({
      queryKey: ["agent-tool-executions", runId],
      queryFn: () => apiFetch<AgentToolExecutionsResponse>(
        `/api/v1/agent-turn-runs/${runId}/tool-executions`,
      ),
      enabled: canInteract,
      refetchInterval: runId === candidateTurnRunId && !isTerminal(runQuery.data?.status ?? "")
        ? 2_000
        : false,
    })),
  });
  const eventQueries = useQueries({
    queries: activityRunIds.map((runId) => ({
      queryKey: ["agent-run-events", runId],
      queryFn: () => apiFetch<RunEvent[]>(`/api/v1/runs/${runId}/events?limit=500`),
      enabled: canInteract,
      staleTime: runId === candidateTurnRunId ? 0 : 60_000,
    })),
  });
  const manifestQuery = useQuery({
    queryKey: ["agent-artifact-manifest", candidateTurnRunId],
    queryFn: () => apiFetch<AgentArtifactManifest>(
      `/api/v1/agent-turn-runs/${candidateTurnRunId}/manifest`,
    ),
    enabled: Boolean(candidateTurnRunId),
    refetchInterval: () => candidateTurnRunId && !isTerminal(runQuery.data?.status ?? "")
      ? 2_000
      : false,
  });
  const activeTurnRunId = candidateTurnRunId && !isTerminal(runQuery.data?.status ?? "")
    ? candidateTurnRunId
    : null;
  const eventStream = useRunEvents(candidateTurnRunId);

  useEffect(() => {
    const terminal = runQuery.data ? isTerminal(runQuery.data.status) : eventStream.closed;
    if (!candidateTurnRunId || !terminal) return;
    setSubmittedRunId(null);
    void queryClient.invalidateQueries({ queryKey: ["agent-session", sessionId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-sessions", projectId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-messages", sessionId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-turn", candidateTurnRunId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-artifacts", candidateTurnRunId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-tool-executions", candidateTurnRunId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-run-events", candidateTurnRunId] });
    void queryClient.invalidateQueries({ queryKey: ["agent-artifact-manifest", candidateTurnRunId] });
  }, [
    candidateTurnRunId,
    eventStream.closed,
    projectId,
    queryClient,
    runQuery.data,
    sessionId,
  ]);

  useEffect(() => {
    if (!inspectorOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSearchParams((current) => {
        const next = new URLSearchParams(current);
        next.delete("inspector");
        return next;
      });
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [inspectorOpen, setSearchParams]);

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
    mutationFn: (intent: AgentMessageIntent) => {
      if (!canInteract) {
        return Promise.reject(new Error("资源闭包尚未确认，暂时无法开始研究"));
      }
      return apiFetch<PostMessageResult>(`/api/v1/agent-sessions/${sessionId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": intent.key,
        },
        body: JSON.stringify({
          content: intent.content,
          review_output_id: intent.reviewOutputId,
          attachment_ids: intent.attachmentIds,
        }),
      });
    },
    onSuccess: (result) => {
      setSubmittedRunId(result.run_id);
      setContent("");
      setMessageIntent(null);
      setSelectedAttachmentIds([]);
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
    if (!canInteract) return;
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
      selectedAttachmentIds,
      () => crypto.randomUUID(),
    );
    setMessageIntent(intent);
    postMessageMutation.mutate(intent);
  };

  const openInspector = (tab: AgentInspectorTab) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("inspector", tab);
      return next;
    });
  };
  const closeInspector = () => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("inspector");
      return next;
    });
  };
  const selectCitation = (citation: CitationSummary) => {
    setSelectedEvidence(citation);
    openInspector("evidence");
  };

  const uploadAttachment = (file: File) => {
    const intent = ensureAgentAttachmentUploadIntent(
      uploadIntent,
      file,
      () => crypto.randomUUID(),
    );
    setUploadIntent(intent);
    uploadAttachmentMutation.mutate({ file, intent });
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
      <div className="viewport-workspace-page agent-page">
        <PageBar breadcrumbs={[{ label: "研究项目", to: "/" }, { label: "研究助手", to: `/projects/${projectId}/agent` }]} title="研究助手" />
        <section className="notice">
          <p className="error-text">
            {sessionProjectMismatch
              ? "资源不存在或无权访问"
              : errorMessage(projectQuery.error ?? sessionsQuery.error ?? sessionQuery.error)}
          </p>
          <Link to="/">返回项目</Link>
        </section>
      </div>
    );
  }

  const project = projectQuery.data;
  const turnActivities = new Map(activityRunIds.map((runId, index) => {
    const persistedEvents = eventQueries[index]?.data ?? [];
    const mergedEvents = mergeRunEvents(
      persistedEvents,
      runId === candidateTurnRunId ? eventStream.events : [],
    );
    const visibleEvents = mergedEvents.flatMap((event) => {
      const label = agentEventLabel(event.event_type);
      return label ? [{ ...event, label }] : [];
    });
    const failedEvent = [...mergedEvents]
      .reverse()
      .find((event) => event.event_type === "run_failed");
    const failure = failedEvent || (runId === candidateTurnRunId && runQuery.data?.status === "failed")
      ? agentTurnFailureSummary(failedEvent)
      : null;
    return [runId, {
      events: visibleEvents,
      toolExecutions: toolExecutionQueries[index]?.data,
      loading: Boolean(eventQueries[index]?.isPending || toolExecutionQueries[index]?.isPending),
      error: Boolean(eventQueries[index]?.isError || toolExecutionQueries[index]?.isError),
      failure,
      active: runId === activeTurnRunId,
    }] as const;
  }));
  const currentActivity = candidateTurnRunId ? turnActivities.get(candidateTurnRunId) : undefined;
  const turnFailure = currentActivity?.failure ?? null;
  const skillLocked = isSkillProfileLocked(messages.length);
  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const availableMatrices = eligibleEvidenceMatrices(reviewsQuery.data ?? []);
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
    <div className="viewport-workspace-page agent-page">
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: project?.name ?? "正在读取项目…", to: `/projects/${projectId}` }]}
        title="研究助手"
        actions={<span className="page-bar-stat"><strong>{sessionsQuery.data?.length ?? "—"}</strong> 个会话</span>}
      />

      <ResearchWorkspaceFrame
        kind="agent"
        inspectorOpen={inspectorOpen}
        main={<section className="agent-conversation" aria-label="研究助手对话">
          {!sessionId ? (
            <div className="agent-welcome">
              <span className="agent-welcome-mark" aria-hidden="true">A·01</span>
              <p className="eyebrow">START A RESEARCH THREAD</p>
              <h2>让项目材料成为持续研究上下文</h2>
              <p>创建会话后，通过简洁输入区选择证据、附件与研究能力。</p>
              <form
                className="agent-start-form"
                onSubmit={(event) => {
                  event.preventDefault();
                  createSessionMutation.mutate();
                }}
              >
                <label className="sr-only" htmlFor="agent-session-title">新会话标题</label>
                <input
                  id="agent-session-title"
                  value={newSessionTitle}
                  maxLength={200}
                  autoComplete="off"
                  onChange={(event) => setNewSessionTitle(event.target.value)}
                  placeholder="例如：研究缺口分析…"
                />
                <button type="submit" disabled={createSessionMutation.isPending}>
                  {createSessionMutation.isPending ? "正在创建…" : "新建研究会话"}
                </button>
              </form>
              {createSessionMutation.isError ? (
                <p className="error-text">{errorMessage(createSessionMutation.error)}</p>
              ) : null}
            </div>
          ) : (
            <>
              <header className="agent-chat-heading">
                <div><p className="eyebrow">CONTINUOUS RESEARCH</p><h2>{sessionQuery.data?.title || "未命名研究会话"}</h2></div>
                <div className="agent-chat-heading-actions">
                  <span className={
                    activeTurnRunId
                      ? "badge badge-pending"
                      : turnFailure
                        ? "badge badge-error"
                        : "badge badge-ok"
                  }>
                    {activeTurnRunId
                      ? statusLabel(runQuery.data?.status ?? "queued")
                      : turnFailure
                        ? "上一轮失败 · 可重新发起"
                        : "可继续"}
                  </span>
                  <div className="inspector-launcher" aria-label="打开研究检查器">
                    <button type="button" className="button-plain" onClick={() => openInspector("evidence")}>证据</button>
                    <button type="button" className="button-plain" onClick={() => openInspector("browser")}>浏览器</button>
                    <button type="button" className="button-plain" onClick={() => openInspector("outputs")}>成果</button>
                  </div>
                  <div className="agent-capability-menu">
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
                  </div>
                </div>
              </header>

              {(postMessageMutation.isError || cancelMutation.isError) && (
                <p className="notice error-text">{errorMessage(postMessageMutation.error ?? cancelMutation.error)}</p>
              )}

              <div className="agent-message-timeline" aria-live="polite">
                {messagesQuery.isPending && <p className="muted">正在恢复消息历史…</p>}
                {messagesQuery.isError && <p className="error-text">{errorMessage(messagesQuery.error)}</p>}
                {messages.length === 0 && !activeTurnRunId && (
                  <div className="agent-empty-turn"><h3>准备第一轮研究</h3><p>选择 Evidence Matrix，然后提出一个需要比较、综合或验证的研究问题。</p></div>
                )}
                {turnGroups.map((group) => {
                  const activity = turnActivities.get(group.turnRunId);
                  const userMessages = group.messages.filter((message) => message.role === "user");
                  const assistantMessagesForTurn = group.messages.filter((message) => message.role === "assistant");
                  return (
                    <section className="agent-turn-thread" key={group.turnRunId}>
                      {userMessages.map((message) => (
                        <AgentMessageItem key={message.message_id} message={message} onCitation={selectCitation} />
                      ))}
                      {activity && (
                        <AgentResearchActivity
                          events={activity.events}
                          toolExecutions={activity.toolExecutions}
                          loading={activity.loading}
                          error={activity.error}
                          active={activity.active}
                          failure={activity.failure}
                        />
                      )}
                      {assistantMessagesForTurn.map((message) => (
                        <AgentMessageItem key={message.message_id} message={message} onCitation={selectCitation} />
                      ))}
                    </section>
                  );
                })}
              </div>

              <form className="conversation-composer agent-composer" onSubmit={submitMessage}>
                <details className="composer-options">
                  <summary aria-label="添加本轮证据或附件">
                    <span aria-hidden="true">＋</span>
                    本轮上下文
                  </summary>
                  <div className="composer-options-popover agent-composer-options">
                    <div className="agent-composer-context">
                      <label htmlFor="agent-matrix">Evidence Matrix</label>
                      <select id="agent-matrix" aria-label="本轮 Evidence Matrix" value={selectedReviewRunId} onChange={(event) => { setSelectedReviewRunId(event.target.value); setMessageIntent(null); }} disabled={!canInteract || Boolean(activeTurnRunId)}>
                        <option value="">{turnQuery.data?.review_output_id ? "沿用上一轮 Evidence Matrix" : "请选择可用 Evidence Matrix"}</option>
                        {availableMatrices.map((review) => (
                          <option key={review.run_id} value={review.run_id}>
                            {review.research_question} · {review.evidence_matrix.valid_papers} valid / {review.evidence_matrix.failed_papers} failed / {review.evidence_matrix.row_count} rows{review.status === "failed" ? " · Review 后续失败" : ""}
                          </option>
                        ))}
                      </select>
                    </div>
                    <AgentAttachmentComposer
                      attachments={attachmentsQuery.data ?? []}
                      selectedIds={selectedAttachmentIds}
                      disabled={
                        !canInteract ||
                        Boolean(activeTurnRunId) ||
                        deleteAttachmentMutation.isPending
                      }
                      uploading={uploadAttachmentMutation.isPending}
                      error={
                        uploadAttachmentMutation.isError || deleteAttachmentMutation.isError ||
                        attachmentsQuery.isError
                          ? errorMessage(
                              uploadAttachmentMutation.error ?? deleteAttachmentMutation.error ??
                              attachmentsQuery.error,
                            )
                          : null
                      }
                      onUpload={uploadAttachment}
                      onToggle={(attachmentId) => {
                        setSelectedAttachmentIds((current) => current.includes(attachmentId) ? current.filter((value) => value !== attachmentId) : current.length < 5 ? [...current, attachmentId] : current);
                        setMessageIntent(null);
                      }}
                      onDelete={(attachmentId) => deleteAttachmentMutation.mutate(attachmentId)}
                    />
                    {(selectedReviewRunId && selectedMatrixQuery.isPending) ? <small>正在验证 Evidence Matrix…</small> : null}
                    {reviewsQuery.isError ? <small className="error-text">{errorMessage(reviewsQuery.error)}</small> : null}
                    {selectedMatrixQuery.isError ? <small className="error-text">{errorMessage(selectedMatrixQuery.error)}</small> : null}
                  </div>
                </details>
                <label className="sr-only" htmlFor="agent-message">研究消息</label>
                <textarea id="agent-message" rows={2} maxLength={16_000} value={content} onChange={(event) => { setContent(event.target.value); if (messageIntent && event.target.value.trim() !== messageIntent.content) setMessageIntent(null); }} disabled={!canInteract || Boolean(activeTurnRunId)} placeholder="输入研究问题…" />
                <div className="conversation-composer-toolbar">
                  <div className="context-chip-list" aria-label="本轮已选上下文">
                    <span className="context-chip">
                      {selectedReviewRunId
                        ? "已选择 Evidence Matrix"
                        : turnQuery.data?.review_output_id
                          ? "沿用 Evidence Matrix"
                          : "未选择 Evidence Matrix"}
                    </span>
                    {selectedAttachmentIds.length > 0 ? (
                      <span className="context-chip">附件 {selectedAttachmentIds.length}</span>
                    ) : null}
                  </div>
                  {activeTurnRunId ? (
                    <button
                      className="danger agent-stop-button"
                      type="button"
                      disabled={cancelMutation.isPending || !isCancellable(runQuery.data?.status ?? "queued")}
                      onClick={() => cancelMutation.mutate()}
                    >
                      {cancelMutation.isPending ? "正在停止…" : "停止本轮"}
                      <span aria-hidden="true">■</span>
                    </button>
                  ) : (
                    <button type="submit" disabled={!canSendAgentMessage(content, reviewOutputId, activeTurnRunId, !canInteract || postMessageMutation.isPending || mcpDirty || skillDirty)}>{postMessageMutation.isPending ? "正在提交…" : "发送"}<span aria-hidden="true">→</span></button>
                  )}
                </div>
              </form>
            </>
          )}
        </section>}
        inspector={<AgentInspector
          activeTab={inspectorTab}
          onTabChange={openInspector}
          onClose={closeInspector}
          evidence={(
            <AgentEvidenceMargin
              projectId={projectId}
              turn={turnQuery.data}
              matrix={selectedMatrixQuery.data}
              projectReadyIndexCount={projectContextQuery.data?.ready_index_count}
              projectIndexError={projectContextQuery.isError}
              assistantMessages={assistantMessages}
              selectedEvidence={selectedEvidence}
              onSelectEvidence={setSelectedEvidence}
              onClearEvidence={() => setSelectedEvidence(null)}
            />
          )}
          browser={<AgentBrowserPanel sessionId={sessionId} activeTurnRunId={activeTurnRunId} />}
          outputs={(
            <AgentTurnOutputs
              artifacts={artifactsQuery.data}
              candidates={turnQuery.data?.candidates ?? []}
              manifest={manifestQuery.data}
              artifactsLoading={artifactsQuery.isPending && Boolean(candidateTurnRunId)}
              artifactsError={artifactsQuery.isError}
              manifestLoading={manifestQuery.isPending && Boolean(candidateTurnRunId)}
              manifestError={manifestQuery.isError}
            />
          )}
        />}
      />
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
