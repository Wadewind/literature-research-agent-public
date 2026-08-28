/** Agent 附件上传身份：响应丢失后重选同一文件必须复用 Idempotency-Key。 */

export interface AgentAttachmentFileIdentity {
  name: string;
  size: number;
  type: string;
  lastModified: number;
}

export interface AgentAttachmentUploadIntent {
  key: string;
  fileName: string;
  fileSize: number;
  fileType: string;
  lastModified: number;
}

export function ensureAgentAttachmentUploadIntent(
  current: AgentAttachmentUploadIntent | null,
  file: AgentAttachmentFileIdentity,
  createKey: () => string,
): AgentAttachmentUploadIntent {
  if (
    current?.fileName === file.name &&
    current.fileSize === file.size &&
    current.fileType === file.type &&
    current.lastModified === file.lastModified
  ) {
    return current;
  }
  return {
    key: createKey(),
    fileName: file.name,
    fileSize: file.size,
    fileType: file.type,
    lastModified: file.lastModified,
  };
}
