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

export interface AgentTurnFailureSummary {
  title: string;
  detail: string;
  code: string;
}

interface AgentFailureEvent {
  event_type: string;
  payload: Record<string, unknown>;
}

export function agentTurnFailureSummary(
  event: AgentFailureEvent | undefined,
): AgentTurnFailureSummary {
  const error = event?.payload.error;
  const errorType = error && typeof error === "object" && "type" in error
    ? (error as { type?: unknown }).type
    : undefined;
  const errorMessage = error && typeof error === "object" && "message" in error
    ? (error as { message?: unknown }).message
    : undefined;
  if (
    errorType === "runtime_sandbox_metadata_invalid" ||
    (
      errorType === "SandboxApiException" &&
      typeof errorMessage === "string" &&
      errorMessage.includes("INVALID_METADATA_LABEL")
    )
  ) {
    return {
      title: "研究环境未能启动",
      detail: "Sandbox 配置未通过校验，本轮未进入模型或工具执行。修正配置后可以重新发起。",
      code: "runtime_sandbox_metadata_invalid",
    };
  }
  if (errorType === "runtime_output_invalid") {
    return {
      title: "回答引用格式未通过校验",
      detail: "模型已完成研究，但最终回复的 Evidence 标记格式无效，因此未写入会话。可以重新发起本轮研究。",
      code: "runtime_output_invalid",
    };
  }
  return {
    title: "本轮研究未能完成",
    detail: "本轮没有生成研究助手回复。可以调整问题后重新发起，并在研究活动中核对失败阶段。",
    code: "agent_turn_failed",
  };
}

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

export function canInteractWithAgentSession(
  project: { project_id: string } | undefined,
  session: { project_id: string } | undefined,
  projectId: string,
): boolean {
  return project?.project_id === projectId && session?.project_id === projectId;
}

interface SkillIdentity {
  source: string;
  skill_id: string;
  version: number;
}

export function visibleSkillVersions<T extends SkillIdentity>(
  skills: readonly T[],
  selections: readonly SkillIdentity[],
): T[] {
  return skills.filter((skill) => {
    const selected = selections.find((item) =>
      item.source === skill.source && item.skill_id === skill.skill_id
    );
    if (selected) return selected.version === skill.version;
    return !skills.some((candidate) =>
      candidate.source === skill.source &&
      candidate.skill_id === skill.skill_id &&
      candidate.version > skill.version
    );
  });
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
