import { Link } from "react-router-dom";

import type { Conversation } from "../api/types";
import { chatConversationPath, chatHomePath } from "../workspace/projectWorkspace";

interface ConversationRailProps {
  projectId: string;
  conversations: Conversation[] | undefined;
  activeConversationId?: string;
  error?: string | null;
}

export function conversationScopeLabel(
  conversation: Pick<Conversation, "scope_mode"> &
    Partial<Pick<Conversation, "scope_papers">>,
): string {
  if (conversation.scope_mode === "project") return "整个项目";
  const paperCount = conversation.scope_papers?.length ?? 0;
  return paperCount > 0 ? `${paperCount} 篇文献` : "所选文献";
}

export default function ConversationRail({
  projectId,
  conversations,
  activeConversationId,
  error,
}: ConversationRailProps) {
  return (
    <aside className="conversation-sidebar">
      <div className="conversation-rail-heading">
        <p className="eyebrow">CITED RAG</p>
        <h2>文献问答</h2>
        <Link className="button-link" to={chatHomePath(projectId)}>新建问答</Link>
      </div>
      {conversations === undefined && !error ? <p className="muted">正在读取对话…</p> : null}
      {error ? <p className="error-text">{error}</p> : null}
      <nav className="conversation-list" aria-label="项目内对话">
        {conversations?.length === 0 ? (
          <p className="conversation-rail-empty">还没有问答记录。先选择一个检索范围。</p>
        ) : null}
        {conversations?.map((item) => (
          <Link
            key={item.conversation_id}
            className={item.conversation_id === activeConversationId ? "active" : ""}
            to={chatConversationPath(projectId, item.conversation_id)}
          >
            <strong>{item.title || "未命名对话"}</strong>
            <span>{conversationScopeLabel(item)}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}
