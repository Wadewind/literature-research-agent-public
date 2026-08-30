import type { ScopeSelection } from "../conversations/scopeSelection";
import type { Conversation, Project } from "../api/types";

export const CHAT_QUESTION_TEMPLATE_PARAM = "question_template";

export function chatHomePath(projectId: string): string {
  return `/projects/${projectId}/chat`;
}

export function chatConversationPath(projectId: string, conversationId: string): string {
  return `${chatHomePath(projectId)}/${conversationId}`;
}

export function chatConversationPromptPath(
  projectId: string,
  conversationId: string,
  questionTemplateId: string,
): string {
  const search = new URLSearchParams({
    [CHAT_QUESTION_TEMPLATE_PARAM]: questionTemplateId,
  });
  return `${chatConversationPath(projectId, conversationId)}?${search.toString()}`;
}

export function chatPreselectionPath(projectId: string, paperIds: string[]): string {
  const search = new URLSearchParams();
  for (const paperId of new Set(paperIds)) search.append("paper_id", paperId);
  const query = search.toString();
  return query ? `${chatHomePath(projectId)}?${query}` : chatHomePath(projectId);
}

export function initialChatScope(
  search: URLSearchParams,
  availablePaperIds: string[],
): ScopeSelection {
  const available = new Set(availablePaperIds);
  const paperIds = search.getAll("paper_id").filter((paperId, index, values) =>
    available.has(paperId) && values.indexOf(paperId) === index
  );
  return { paperIds };
}

export function isConversationInProject(
  conversation: Pick<Conversation, "project_id">,
  projectId: string,
): boolean {
  return conversation.project_id === projectId;
}

export function canInteractWithConversation(
  project: Pick<Project, "project_id"> | undefined,
  conversation: Pick<Conversation, "project_id"> | undefined,
  projectId: string,
): boolean {
  return project?.project_id === projectId && conversation?.project_id === projectId;
}
