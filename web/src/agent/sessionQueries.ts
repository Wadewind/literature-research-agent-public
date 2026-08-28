/** 只有资源闭包确认后的真实 Session 才允许读取附件。 */
export function canQueryAgentAttachments(
  sessionId: string,
  canInteract: boolean,
): boolean {
  return Boolean(sessionId && canInteract);
}
