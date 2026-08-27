import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { Conversation, PaperListItem, Project } from "../api/types";
import ChatWorkspaceFrame from "../components/ChatWorkspaceFrame";
import ConversationRail from "../components/ConversationRail";
import ProjectWorkspaceHeader from "../components/ProjectWorkspaceHeader";
import {
  createScopeSelection,
  scopeRequest,
  toggleScopePaper,
  type ScopeSelection,
} from "../conversations/scopeSelection";
import {
  chatConversationPath,
  initialChatScope,
} from "../workspace/projectWorkspace";

interface ChatWorkspaceHomeProps {
  projectId: string;
  search: string;
}

function ChatWorkspaceHome({ projectId, search }: ChatWorkspaceHomeProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [scopeDraft, setScopeDraft] = useState<ScopeSelection | null>(null);
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/api/v1/projects/${projectId}`),
  });
  const papersQuery = useQuery({
    queryKey: ["papers", projectId],
    queryFn: () => apiFetch<PaperListItem[]>(`/api/v1/projects/${projectId}/papers`),
  });
  const conversationsQuery = useQuery({
    queryKey: ["conversations", projectId],
    queryFn: () => apiFetch<Conversation[]>(`/api/v1/projects/${projectId}/conversations`),
  });
  const urlScope = initialChatScope(
    new URLSearchParams(search),
    papersQuery.data?.map((paper) => paper.paper_id) ?? [],
  );
  const selection = scopeDraft ?? urlScope;
  const project = projectQuery.data;
  const archived = Boolean(project?.archived_at);

  const createMutation = useMutation({
    mutationFn: (scope: ScopeSelection) =>
      apiFetch<Conversation>(`/api/v1/projects/${projectId}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scopeRequest(scope)),
      }),
    onSuccess: (conversation) => {
      void queryClient.invalidateQueries({ queryKey: ["conversations", projectId] });
      navigate(chatConversationPath(projectId, conversation.conversation_id));
    },
  });

  if (projectQuery.isError) {
    return (
      <section className="notice">
        <p className="error-text">{errorMessage(projectQuery.error)}</p>
        <Link to="/">返回项目</Link>
      </section>
    );
  }

  const selectedPapers = papersQuery.data?.filter((paper) =>
    selection.paperIds.includes(paper.paper_id)
  ) ?? [];
  const scopeDescription = selection.paperIds.length === 0
    ? "每个问题都在当前 Project 的可用索引中独立检索。"
    : `固定检索 ${selection.paperIds.length} 篇选中文献。`;

  return (
    <div className="viewport-workspace-page chat-page">
      <ProjectWorkspaceHeader
        projectId={projectId}
        project={project}
        active="chat"
        eyebrow="文献问答"
        description="用可回查的 Claim、Citation 与 Evidence 回答一个明确问题。"
        actions={<span className="workspace-context-chip">{conversationsQuery.data?.length ?? "—"} 条问答</span>}
      />
      <ChatWorkspaceFrame
        rail={
          <ConversationRail
            projectId={projectId}
            conversations={conversationsQuery.data}
            error={conversationsQuery.isError ? errorMessage(conversationsQuery.error) : null}
          />
        }
        conversation={
          <main className="conversation-main chat-create-main">
            <header className="chat-heading">
              <div>
                <p className="eyebrow">NEW CITED QUESTION</p>
                <h2>选择这次问答的证据范围</h2>
                <p>{scopeDescription}</p>
              </div>
            </header>
            <section className="chat-scope-picker" aria-labelledby="chat-scope-title">
              <div className="chat-scope-intro">
                <h3 id="chat-scope-title">检索范围</h3>
                <button
                  type="button"
                  className={selection.paperIds.length === 0 ? "scope-choice active" : "scope-choice"}
                  onClick={() => setScopeDraft(createScopeSelection())}
                >
                  <strong>整个 Project</strong>
                  <span>提问时检索全部已就绪的固定文献版本</span>
                </button>
              </div>
              <fieldset>
                <legend>或固定单篇 / 多篇文献</legend>
                {papersQuery.isPending ? <p className="muted">正在读取项目文献…</p> : null}
                {papersQuery.isError ? <p className="error-text">{errorMessage(papersQuery.error)}</p> : null}
                {papersQuery.data?.length === 0 ? (
                  <p className="muted">文献库尚无可选论文。先收录并完成索引。</p>
                ) : null}
                <div className="chat-paper-options">
                  {papersQuery.data?.map((paper) => (
                    <label key={paper.paper_id}>
                      <input
                        type="checkbox"
                        checked={selection.paperIds.includes(paper.paper_id)}
                        disabled={Boolean(paper.archived_at)}
                        onChange={() => setScopeDraft(toggleScopePaper(selection, paper.paper_id))}
                      />
                      <span>
                        <strong>{paper.version.display_filename}</strong>
                        <small>{paper.version.parse_ready ? "已解析" : "等待解析"}{paper.archived_at ? " · 已归档" : ""}</small>
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            </section>
            {archived ? <p className="readonly-note">该 Project 已归档。历史问答仍可查看，但不能创建新问答。</p> : null}
            {createMutation.isError ? <p className="error-text">{errorMessage(createMutation.error)}</p> : null}
            <footer className="chat-create-actions">
              <span>{selection.paperIds.length === 0 ? "Project scope" : `${selection.paperIds.length} papers selected`}</span>
              <button
                type="button"
                disabled={archived || createMutation.isPending}
                onClick={() => createMutation.mutate(selection)}
              >
                {createMutation.isPending ? "正在创建…" : "创建问答"}<span aria-hidden="true">→</span>
              </button>
            </footer>
          </main>
        }
        evidence={
          <aside className="evidence-drawer chat-scope-margin">
            <header><div><p className="eyebrow">SCOPE MARGIN</p><h2>本次上下文</h2></div></header>
            <dl className="evidence-meta">
              <div><dt>范围</dt><dd>{selection.paperIds.length === 0 ? "整个 Project" : "固定文献"}</dd></div>
              <div><dt>数量</dt><dd>{selection.paperIds.length === 0 ? papersQuery.data?.length ?? "—" : selection.paperIds.length} 篇</dd></div>
              <div><dt>方式</dt><dd>每个问题独立检索</dd></div>
            </dl>
            {selectedPapers.length > 0 ? (
              <ol className="scope-paper-summary">
                {selectedPapers.map((paper) => <li key={paper.paper_id}>{paper.version.display_filename}</li>)}
              </ol>
            ) : (
              <p className="muted">回答只会引用本次范围内经过校验的 Evidence。</p>
            )}
          </aside>
        }
      />
    </div>
  );
}

export default function ChatPage() {
  const { projectId = "" } = useParams();
  const { search } = useLocation();
  return <ChatWorkspaceHome key={`${projectId}:${search}`} projectId={projectId} search={search} />;
}
