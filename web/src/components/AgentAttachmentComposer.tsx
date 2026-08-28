import type { ChangeEvent } from "react";

import type { AgentAttachment } from "../api/types";

interface Props {
  attachments: AgentAttachment[];
  selectedIds: string[];
  disabled: boolean;
  uploading: boolean;
  error: string | null;
  onUpload: (file: File) => void;
  onToggle: (attachmentId: string) => void;
  onDelete: (attachmentId: string) => void;
}

export default function AgentAttachmentComposer({
  attachments,
  selectedIds,
  disabled,
  uploading,
  error,
  onUpload,
  onToggle,
  onDelete,
}: Props) {
  const available = attachments.filter((item) => item.status === "available");
  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) onUpload(file);
    event.target.value = "";
  };
  return (
    <>
      <div className="agent-attachments" aria-label="本轮附件">
        <label className="agent-attachment-upload">
          <span>{uploading ? "上传中…" : "添加附件"}</span>
          <input
            type="file"
            accept=".png,.jpg,.jpeg,.svg,.pdf,.csv,.md,.txt,.json"
            disabled={disabled || selectedIds.length >= 5 || uploading}
            onChange={handleFile}
          />
        </label>
        {available.map((item) => {
          const selected = selectedIds.includes(item.attachment_id);
          return (
            <span
              className={`agent-attachment-chip${selected ? " is-selected" : ""}`}
              key={item.attachment_id}
            >
              <button
                type="button"
                disabled={disabled || uploading}
                onClick={() => onToggle(item.attachment_id)}
              >
                {selected ? "✓ " : "+ "}{item.display_name}
              </button>
              {!selected && (
                <button
                  type="button"
                  aria-label={`删除 ${item.display_name}`}
                  disabled={disabled || uploading}
                  onClick={() => onDelete(item.attachment_id)}
                >
                  ×
                </button>
              )}
            </span>
          );
        })}
      </div>
      {error && <small className="error-text">{error}</small>}
    </>
  );
}
