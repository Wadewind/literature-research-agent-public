import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";

import { apiFetch, errorMessage } from "../api/client";
import type { Conversation, PaperListItem, Project } from "../api/types";
import ChatScopeDialog from "../components/ChatScopeDialog";
import PageBar from "../components/PageBar";
import QuestionStarterList from "../components/QuestionStarterList";
import ResearchWorkspaceFrame from "../components/ResearchWorkspaceFrame";
import {
  CHAT_QUESTION_DRAFT_MAX_LENGTH,
  questionTemplateById,
  type ChatQuestionTemplateId,
  type QuestionDraftHandoff,
} from "../conversations/questionTemplates";
import {
  createScopeSelection,
  scopeRequest,
  toggleScopePaper,
  type ScopeSelection,
} from "../conversations/scopeSelection";
import {
  chatConversationPath,
  chatConversationPromptPath,
  initialChatScope,
} from "../workspace/projectWorkspace";

interface ChatWorkspaceHomeProps {
  projectId: string;
  search: string;
}

interface CreateConversationInput {
  scope: ScopeSelection;
  questionDraft: string;
  templateId: ChatQuestionTemplateId | null;
}

function ChatWorkspaceHome({ projectId, search }: ChatWorkspaceHomeProps) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [scopeDraft, setScopeDraft] = useState<ScopeSelection | null>(null);
  const [questionDraft, setQuestionDraft] = useState("");
  const [selectedTemplateId, setSelectedTemplateId] =
    useState<ChatQuestionTemplateId | null>(null);
  const [scopeOpen, setScopeOpen] = useState(false);
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
    mutationFn: (input: CreateConversationInput) =>
      apiFetch<Conversation>(`/api/v1/projects/${projectId}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(scopeRequest(input.scope)),
      }),
    onSuccess: (conversation, input) => {
      void queryClient.invalidateQueries({ queryKey: ["conversations", projectId] });
      const selectedTemplate = questionTemplateById(input.templateId);
      if (selectedTemplate?.question === input.questionDraft) {
        navigate(
          chatConversationPromptPath(
            projectId,
            conversation.conversation_id,
            selectedTemplate.id,
          ),
        );
        return;
      }
      const path = chatConversationPath(projectId, conversation.conversation_id);
      if (input.questionDraft) {
        const state: QuestionDraftHandoff = { questionDraft: input.questionDraft };
        navigate(path, { state });
        return;
      }
      navigate(path);
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

  const scopeDescription = selection.paperIds.length === 0
    ? "每个问题都在当前 Project 的可用索引中独立检索。"
    : `固定检索 ${selection.paperIds.length} 篇选中文献。`;
  const scopeLabel = selection.paperIds.length === 0
    ? `整个 Project · ${papersQuery.data?.length ?? "—"} 篇`
    : `固定文献 · ${selection.paperIds.length} 篇`;
  const openScopeDialog = () => {
    createMutation.reset();
    setScopeDraft(null);
    setScopeOpen(true);
  };
  const closeScopeDialog = () => {
    if (createMutation.isPending) return;
    setScopeOpen(false);
    setScopeDraft(null);
  };
  const selectQuestionTemplate = (templateId: ChatQuestionTemplateId) => {
    const template = questionTemplateById(templateId);
    if (!template) return;
    setSelectedTemplateId(template.id);
    setQuestionDraft(template.question);
    openScopeDialog();
  };

  const updateQuestionDraft = (value: string) => {
    setQuestionDraft(value);
    const selectedTemplate = questionTemplateById(selectedTemplateId);
    if (selectedTemplate?.question !== value) setSelectedTemplateId(null);
  };

  const createConversation = () => {
    const question = questionDraft.trim();
    if (!question || archived || createMutation.isPending) return;
    createMutation.mutate({
      scope: selection,
      questionDraft: question,
      templateId: selectedTemplateId,
    });
  };

  const submitQuestionDraft = (event: FormEvent) => {
    event.preventDefault();
    if (!questionDraft.trim() || archived) return;
    openScopeDialog();
  };

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
            <header className="chat-heading chat-create-heading">
              <div>
                <p className="eyebrow">NEW CITED QUESTION</p>
                <h2 id="chat-create-title">向项目文献提问，获取可验证的答案</h2>
                <p>选择一个研究切入点，或写下自己的问题，再确认用于检索的文献范围。</p>
              </div>
            </header>
            <div className="chat-create-stage">
              <section className="chat-create-card" aria-labelledby="chat-create-title">
                <QuestionStarterList
                  selectedId={selectedTemplateId}
                  disabled={archived}
                  onSelect={selectQuestionTemplate}
                />
              </section>
              {archived ? <p className="readonly-note chat-create-readonly">该 Project 已归档。历史问答仍可查看，但不能创建新问答。</p> : null}
            </div>

            <form className="conversation-composer chat-create-composer" onSubmit={submitQuestionDraft}>
              <label className="sr-only" htmlFor="chat-question-draft">写下自己的问题</label>
              <textarea
                id="chat-question-draft"
                name="question"
                value={questionDraft}
                onChange={(event) => updateQuestionDraft(event.target.value)}
                placeholder="提出一个需要文献证据回答的问题…"
                rows={2}
                maxLength={CHAT_QUESTION_DRAFT_MAX_LENGTH}
                disabled={archived}
                aria-describedby="chat-question-draft-help"
                autoComplete="off"
              />
              <div className="conversation-composer-toolbar">
                <div className="chat-create-composer-context">
                  <button
                    type="button"
                    className="context-chip chat-scope-trigger"
                    aria-haspopup="dialog"
                    aria-expanded={scopeOpen}
                    disabled={archived}
                    onClick={openScopeDialog}
                  >
                    {scopeLabel}
                  </button>
                  <small id="chat-question-draft-help" aria-live="polite">
                    {selectedTemplateId ? "已选择推荐问题，可继续修改。" : scopeDescription}
                  </small>
                </div>
                <button type="submit" disabled={archived || !questionDraft.trim()}>
                  创建问答<span aria-hidden="true">→</span>
                </button>
              </div>
            </form>

            <ChatScopeDialog
              open={scopeOpen}
              question={questionDraft.trim()}
              selection={selection}
              papers={papersQuery.data}
              papersPending={papersQuery.isPending}
              papersError={papersQuery.isError ? errorMessage(papersQuery.error) : null}
              archived={archived}
              creating={createMutation.isPending}
              createError={createMutation.isError ? errorMessage(createMutation.error) : null}
              onClose={closeScopeDialog}
              onSelectProject={() => setScopeDraft(createScopeSelection())}
              onTogglePaper={(paperId) => setScopeDraft(toggleScopePaper(selection, paperId))}
              onCreate={createConversation}
            />
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
