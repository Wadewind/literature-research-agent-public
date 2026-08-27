const AGENT_EVENT_LABELS: Readonly<Record<string, string>> = {
  agent_message_accepted: "研究请求已接受",
  run_started: "研究任务已开始",
  run_requeued: "临时故障，等待重试",
  run_retry_scheduled: "研究任务将重试",
  run_cancel_requested: "正在停止本轮研究",
  agent_runtime_bound: "研究环境已就绪",
  agent_tool_started: "正在使用研究工具",
  agent_tool_succeeded: "研究工具已完成",
  agent_tool_completed: "研究工具已完成",
  agent_tool_failed: "研究工具未能完成",
  agent_artifact_staged: "候选成果已暂存",
  agent_turn_succeeded: "本轮研究已完成",
  agent_turn_cancelled: "本轮研究已取消",
  run_failed: "本轮研究失败",
  run_cancelled: "本轮研究已取消",
};

export function agentEventLabel(eventType: string): string | null {
  return AGENT_EVENT_LABELS[eventType] ?? null;
}

export function canSendAgentMessage(
  content: string,
  reviewOutputId: string,
  activeTurnRunId: string | null,
  pending: boolean,
): boolean {
  return Boolean(content.trim() && reviewOutputId && !activeTurnRunId && !pending);
}

export function isSkillProfileLocked(messageCount: number): boolean {
  return messageCount > 0;
}

export function isSessionInProject(
  session: { project_id: string },
  projectId: string,
): boolean {
  return session.project_id === projectId;
}

interface SkillIdentity {
  source: string;
  skill_id: string;
  version: number;
}

export function isSkillSelectionSelected(
  selection: SkillIdentity,
  skill: SkillIdentity,
): boolean {
  return selection.source === skill.source &&
    selection.skill_id === skill.skill_id &&
    selection.version === skill.version;
}

export function projectIndexLabel(
  count: number | undefined,
  scope: "project" | "turn",
): string {
  if (count === undefined) return "正在读取 Project 索引…";
  return scope === "turn"
    ? `本轮索引快照 · ${count} 篇文献`
    : `当前 Project · ${count} 篇已索引文献`;
}

export function formatCandidateSize(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  return `${(sizeBytes / 1024).toFixed(1)} KB`;
}
