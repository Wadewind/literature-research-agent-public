import { type RefObject } from "react";

import { type AgentWorkspaceLayout, type AgentWorkspacePane } from "../agent/workspaceLayout";
import WorkspaceResizeSeparator from "./WorkspaceResizeSeparator";

interface AgentResizeSeparatorProps {
  pane: AgentWorkspacePane;
  layout: AgentWorkspaceLayout;
  containerRef: RefObject<HTMLDivElement | null>;
  onChange: (layout: AgentWorkspaceLayout) => void;
  onReset: () => void;
}

export default function AgentResizeSeparator({
  pane,
  layout,
  containerRef,
  onChange,
  onReset,
}: AgentResizeSeparatorProps) {
  return (
    <WorkspaceResizeSeparator
      pane={pane}
      layout={layout}
      containerRef={containerRef}
      label={pane === "left" ? "调整研究会话栏宽度" : "调整 Evidence Margin 宽度"}
      onChange={onChange}
      onReset={onReset}
    />
  );
}
