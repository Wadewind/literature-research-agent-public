import { type FormEvent } from "react";
import { Link } from "react-router-dom";

import type { AgentSession } from "../api/types";

interface AgentSessionRailProps {
  projectId: string;
  sessions: AgentSession[] | undefined;
  activeSessionId: string;
  title: string;
  pending: boolean;
  error: string | null;
  onTitleChange: (value: string) => void;
  onCreate: () => void;
}

export default function AgentSessionRail({
  projectId,
  sessions,
  activeSessionId,
  title,
  pending,
  error,
  onTitleChange,
  onCreate,
}: AgentSessionRailProps) {
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onCreate();
  };

  return (
    <aside className="agent-session-rail">
      <div className="agent-rail-heading">
        <p className="eyebrow">RESEARCH THREADS</p>
        <h2>研究会话</h2>
        <p>每条消息是独立、可取消的研究 Turn，会话负责持续上下文。</p>
      </div>
      <form className="agent-session-create" onSubmit={submit}>
        <label htmlFor="agent-session-title">新会话标题</label>
        <input
          id="agent-session-title"
          value={title}
          maxLength={200}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="例如：研究缺口分析"
        />
        <button type="submit" disabled={pending}>
          {pending ? "正在创建…" : "新建研究会话"}
        </button>
        {error && <p className="error-text">{error}</p>}
      </form>
      <nav className="agent-session-list" aria-label="项目内研究会话">
        {sessions === undefined && <p className="muted">正在读取会话…</p>}
        {sessions?.length === 0 && (
          <p className="agent-rail-empty">还没有研究会话。先创建一条，再配置研究上下文。</p>
        )}
        {sessions?.map((session) => (
          <Link
            key={session.session_id}
            className={session.session_id === activeSessionId ? "active" : ""}
            to={`/projects/${projectId}/agent/${session.session_id}`}
          >
            <strong>{session.title || "未命名研究会话"}</strong>
            <span>
              {session.active_turn_run_id ? "研究进行中" : "可继续"} ·{" "}
              {new Date(session.last_activity_at).toLocaleDateString()}
            </span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}
