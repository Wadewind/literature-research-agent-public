import { useRef, useState, type CSSProperties, type ReactNode } from "react";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  loadWorkspaceLayout,
  saveWorkspaceLayout,
  type WorkspaceLayout,
} from "../workspace/workspaceLayout";
import WorkspaceResizeSeparator from "./WorkspaceResizeSeparator";

interface ChatWorkspaceFrameProps {
  rail: ReactNode;
  conversation: ReactNode;
  evidence: ReactNode;
}

export default function ChatWorkspaceFrame({
  rail,
  conversation,
  evidence,
}: ChatWorkspaceFrameProps) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<WorkspaceLayout>(() =>
    loadWorkspaceLayout(window.localStorage, "chat")
  );
  const updateLayout = (next: WorkspaceLayout) => {
    setLayout(next);
    saveWorkspaceLayout(window.localStorage, "chat", next);
  };
  const resetLayout = () => updateLayout(DEFAULT_WORKSPACE_LAYOUT);
  const style = {
    "--workspace-left-width": `${layout.left}px`,
    "--workspace-right-width": `${layout.right}px`,
  } as CSSProperties;

  return (
    <div className="research-workspace chat-workspace" ref={workspaceRef} style={style}>
      {rail}
      <WorkspaceResizeSeparator
        pane="left"
        layout={layout}
        containerRef={workspaceRef}
        label="调整问答历史栏宽度"
        onChange={updateLayout}
        onReset={resetLayout}
      />
      {conversation}
      <WorkspaceResizeSeparator
        pane="right"
        layout={layout}
        containerRef={workspaceRef}
        label="调整 Evidence Margin 宽度"
        onChange={updateLayout}
        onReset={resetLayout}
      />
      {evidence}
    </div>
  );
}
