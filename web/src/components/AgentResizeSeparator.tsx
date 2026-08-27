import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from "react";

import {
  AGENT_WORKSPACE_BOUNDS,
  type AgentWorkspaceLayout,
  type AgentWorkspacePane,
  resizeAgentWorkspace,
} from "../agent/workspaceLayout";

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
  const drag = useRef<{ clientX: number; layout: AgentWorkspaceLayout } | null>(null);
  const bounds = AGENT_WORKSPACE_BOUNDS[pane];
  const direction = pane === "left" ? 1 : -1;

  const resize = (delta: number, initial = layout) => {
    const width = containerRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    onChange(resizeAgentWorkspace(initial, pane, delta, width));
  };

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    drag.current = { clientX: event.clientX, layout };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const handlePointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (!drag.current) return;
    resize((event.clientX - drag.current.clientX) * direction, drag.current.layout);
  };
  const handlePointerUp = (event: PointerEvent<HTMLDivElement>) => {
    drag.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };
  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const physicalDelta = event.key === "ArrowRight" ? 16 : -16;
    resize(physicalDelta * direction);
  };

  return (
    <div
      className="agent-resize-separator"
      role="separator"
      aria-label={pane === "left" ? "调整研究会话栏宽度" : "调整 Evidence Margin 宽度"}
      aria-orientation="vertical"
      aria-valuemin={bounds.min}
      aria-valuemax={bounds.max}
      aria-valuenow={layout[pane]}
      tabIndex={0}
      onDoubleClick={onReset}
      onKeyDown={handleKeyDown}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerCancel={handlePointerUp}
    >
      <span aria-hidden="true" />
    </div>
  );
}
