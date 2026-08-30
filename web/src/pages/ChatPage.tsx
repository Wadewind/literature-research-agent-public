import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { Conversation, PaperListItem, Project } from "../api/types";
import PageBar from "../components/PageBar";
import PaperTitle from "../components/PaperTitle";
import ResearchWorkspaceFrame from "../components/ResearchWorkspaceFrame";
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
      <div className="viewport-workspace-page chat-page">
        <PageBar breadcrumbs={[{ label: "研究项目", to: "/" }]} title="文献问答" />
        <section className="notice">
          <p className="error-text">{errorMessage(projectQuery.error)}</p>
          <Link to="/">返回项目</Link>
        </section>
      </div>
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
      <PageBar
        breadcrumbs={[{ label: "研究项目", to: "/" }, { label: project?.name ?? "正在读取项目…", to: `/projects/${projectId}` }]}
        title="文献问答"
        actions={<span className="page-bar-stat"><strong>{conversationsQuery.data?.length ?? "—"}</strong> 条问答</span>}
      />
      <ResearchWorkspaceFrame
        kind="chat"
        main={
          <main className="conversation-main chat-create-main">
            <header className="chat-heading">
              <div>
                <p className="eyebrow">NEW CITED QUESTION</p>
                <h2>新建文献问答</h2>
                <p>先确定引用范围，进入会话后即可持续追问。</p>
              </div>
            </header>
            <section className="chat-create-card" aria-labelledby="chat-scope-title">
              <div className="chat-create-summary">
                <span className="context-chip">
                  {selection.paperIds.length === 0
                    ? `整个 Project · ${papersQuery.data?.length ?? "—"} 篇`
                    : `固定文献 · ${selection.paperIds.length} 篇`}
                </span>
                <p>{scopeDescription}</p>
              </div>
              <details className="composer-options">
                <summary id="chat-scope-title">
                  <span aria-hidden="true">＋</span>
                  选择证据范围
                </summary>
                <div className="composer-options-popover chat-scope-picker">
                  <button
                    type="button"
                    className={selection.paperIds.length === 0 ? "scope-choice active" : "scope-choice"}
                    onClick={() => setScopeDraft(createScopeSelection())}
                  >
                    <strong>整个 Project</strong>
                    <span>每次提问时从所有已就绪文献中检索</span>
                  </button>
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
                            <strong><PaperTitle paper={paper} /></strong>
                            <small title={paper.version.display_filename}>{paper.version.display_filename} · {paper.version.parse_ready ? "已解析" : "等待解析"}{paper.archived_at ? " · 已归档" : ""}</small>
                          </span>
                        </label>
                      ))}
                    </div>
                  </fieldset>
                </div>
              </details>
              {selectedPapers.length > 0 ? (
                <div className="context-chip-list" aria-label="已选择文献">
                  {selectedPapers.map((paper) => (
                    <span className="context-chip" key={paper.paper_id}><PaperTitle paper={paper} /></span>
                  ))}
                </div>
              ) : null}
              {archived ? <p className="readonly-note">该 Project 已归档。历史问答仍可查看，但不能创建新问答。</p> : null}
              {createMutation.isError ? <p className="error-text">{errorMessage(createMutation.error)}</p> : null}
              <footer className="chat-create-actions">
                <span>创建后范围保持不变</span>
                <button
                  type="button"
                  disabled={archived || createMutation.isPending}
                  onClick={() => createMutation.mutate(selection)}
                >
                  {createMutation.isPending ? "正在创建…" : "创建问答"}<span aria-hidden="true">→</span>
                </button>
              </footer>
            </section>
          </main>
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
