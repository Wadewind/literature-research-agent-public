export function agentWorkspaceKey(projectId: string, sessionId: string): string {
  return `${projectId}:${sessionId || "new"}`;
}
