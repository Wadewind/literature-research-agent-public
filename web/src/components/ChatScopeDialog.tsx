import { useEffect, useRef, type FormEvent } from "react";

import type { PaperListItem } from "../api/types";
import type { ScopeSelection } from "../conversations/scopeSelection";
import PaperTitle from "./PaperTitle";

interface ChatScopeDialogProps {
  open: boolean;
  question: string;
  selection: ScopeSelection;
  papers: PaperListItem[] | undefined;
  papersPending: boolean;
  papersError: string | null;
  archived: boolean;
  creating: boolean;
  createError: string | null;
  onClose: () => void;
  onSelectProject: () => void;
  onTogglePaper: (paperId: string) => void;
  onCreate: () => void;
}

export default function ChatScopeDialog({
  open,
  question,
  selection,
  papers,
  papersPending,
  papersError,
  archived,
  creating,
  createError,
  onClose,
  onSelectProject,
  onTogglePaper,
  onCreate,
}: ChatScopeDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const projectScopeRef = useRef<HTMLButtonElement>(null);
  const selectedPaperIds = new Set(selection.paperIds);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    let focusFrame: number | null = null;
    if (open && !dialog.open) {
      dialog.showModal();
      focusFrame = window.requestAnimationFrame(() => projectScopeRef.current?.focus());
    } else if (!open && dialog.open) {
      dialog.close();
    }

    return () => {
      if (focusFrame !== null) window.cancelAnimationFrame(focusFrame);
    };
  }, [open]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!question || archived || creating) return;
    onCreate();
  };

  const scopeLabel = selection.paperIds.length === 0
    ? `整个 Project · ${papers?.length ?? "—"} 篇`
    : `固定文献 · ${selection.paperIds.length} 篇`;

  return (
    <dialog
      ref={dialogRef}
      className="chat-scope-dialog"
      aria-labelledby="chat-scope-dialog-title"
      aria-describedby="chat-scope-dialog-description"
      onCancel={(event) => {
        event.preventDefault();
        if (!creating) onClose();
      }}
      onClose={onClose}
      onClick={(event) => {
        if (event.target === dialogRef.current && !creating) onClose();
      }}
    >
      <form className="chat-scope-dialog-form" onSubmit={submit}>
        <header className="chat-scope-dialog-head">
          <div>
            <p className="eyebrow">EVIDENCE BOUNDARY</p>
            <h2 id="chat-scope-dialog-title">确认检索边界</h2>
            <p id="chat-scope-dialog-description">
              选择回答这个问题时允许检索的项目文献。
            </p>
          </div>
          <button
            type="button"
            className="create-dialog-close"
            aria-label="关闭检索边界"
            disabled={creating}
            onClick={onClose}
          >
            ×
          </button>
        </header>

        <section className="chat-scope-question" aria-label="问题草稿">
          <span>QUESTION DRAFT</span>
          <strong>{question || "尚未输入问题"}</strong>
        </section>

        <div className="chat-scope-dialog-body">
          <button
            ref={projectScopeRef}
            type="button"
            className={selection.paperIds.length === 0 ? "scope-choice active" : "scope-choice"}
            aria-pressed={selection.paperIds.length === 0}
            onClick={onSelectProject}
          >
            <strong>整个 Project</strong>
            <span>每次提问时从所有已就绪文献中检索</span>
          </button>

          <fieldset>
            <legend>或固定单篇 / 多篇文献</legend>
            {papersPending ? <p className="muted">正在读取项目文献…</p> : null}
            {papersError ? <p className="error-text" aria-live="polite">{papersError}</p> : null}
            {!papersPending && !papersError && papers?.length === 0 ? (
              <p className="muted">文献库尚无可选论文。先收录并完成索引。</p>
            ) : null}
            <div className="chat-paper-options">
              {papers?.map((paper) => (
                <label key={paper.paper_id}>
                  <input
                    type="checkbox"
                    checked={selectedPaperIds.has(paper.paper_id)}
                    disabled={Boolean(paper.archived_at)}
                    onChange={() => onTogglePaper(paper.paper_id)}
                  />
                  <span>
                    <strong><PaperTitle paper={paper} /></strong>
                    <small title={paper.version.display_filename}>
                      {paper.version.display_filename} · {paper.version.parse_ready ? "已解析" : "等待解析"}
                      {paper.archived_at ? " · 已归档" : ""}
                    </small>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
        </div>

        <footer className="chat-scope-dialog-actions">
          <div>
            <span className="context-chip">{scopeLabel}</span>
            <small>{question ? "创建后只预填问题，不会自动发送。" : "请先输入一个问题。"}</small>
          </div>
          <div className="chat-scope-dialog-buttons">
            <button type="button" className="button-plain" disabled={creating} onClick={onClose}>
              取消
            </button>
            <button type="submit" disabled={!question || archived || creating}>
              {creating ? "正在创建…" : "确认并创建问答"}<span aria-hidden="true">→</span>
            </button>
          </div>
          {createError ? <p className="error-text" aria-live="polite">{createError}</p> : null}
        </footer>
      </form>
    </dialog>
  );
}
