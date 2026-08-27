import { useRef, type KeyboardEvent, type PointerEvent, type RefObject } from "react";

import {
  WORKSPACE_LAYOUT_BOUNDS,
  resizeWorkspace,
  type WorkspaceLayout,
  type WorkspacePane,
} from "../workspace/workspaceLayout";

interface WorkspaceResizeSeparatorProps {
  pane: WorkspacePane;
  layout: WorkspaceLayout;
  containerRef: RefObject<HTMLDivElement | null>;
  label: string;
  onChange: (layout: WorkspaceLayout) => void;
  onReset: () => void;
}

export default function WorkspaceResizeSeparator({
  pane,
  layout,
  containerRef,
  label,
  onChange,
  onReset,
}: WorkspaceResizeSeparatorProps) {
  const drag = useRef<{ clientX: number; layout: WorkspaceLayout } | null>(null);
  const bounds = WORKSPACE_LAYOUT_BOUNDS[pane];
  const direction = pane === "left" ? 1 : -1;

  const resize = (delta: number, initial = layout) => {
    const width = containerRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    onChange(resizeWorkspace(initial, pane, delta, width));
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
      className="workspace-resize-separator"
      role="separator"
      aria-label={label}
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
