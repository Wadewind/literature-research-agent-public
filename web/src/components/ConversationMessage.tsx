import type { ReactNode } from "react";

interface ConversationMessageProps {
  role: "user" | "assistant";
  createdAt: string;
  children?: ReactNode;
}

const messageTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

export default function ConversationMessage({
  role,
  createdAt,
  children,
}: ConversationMessageProps) {
  return (
    <article
      aria-label={role === "user" ? "你的消息" : "研究助手消息"}
      className={`message message-${role}`}
    >
      <div className="message-content">{children}</div>
      <time className="message-time" dateTime={createdAt}>
        {messageTimeFormatter.format(new Date(createdAt))}
      </time>
    </article>
  );
}
