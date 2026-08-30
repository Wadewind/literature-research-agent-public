import { useRef, useState, type CSSProperties, type ReactNode } from "react";

import {
  DEFAULT_WORKSPACE_LAYOUT,
  loadWorkspaceLayout,
  saveWorkspaceLayout,
  type WorkspaceKind,
  type WorkspaceLayout,
} from "../workspace/workspaceLayout";
import WorkspaceResizeSeparator from "./WorkspaceResizeSeparator";

interface ResearchWorkspaceFrameProps {
  kind: WorkspaceKind;
  main: ReactNode;
  inspector?: ReactNode;
  inspectorOpen?: boolean;
}

export default function ResearchWorkspaceFrame({
  kind,
  main,
  inspector,
  inspectorOpen = false,
}: ResearchWorkspaceFrameProps) {
  const workspaceRef = useRef<HTMLDivElement>(null);
  const [layout, setLayout] = useState<WorkspaceLayout>(() =>
    loadWorkspaceLayout(window.localStorage, kind)
  );
  const updateLayout = (next: WorkspaceLayout) => {
    setLayout(next);
    saveWorkspaceLayout(window.localStorage, kind, next);
  };
  const style = {
    "--workspace-right-width": `${layout.right}px`,
  } as CSSProperties;
  const inspectorLayout = { ...layout, left: 0 };

  return (
    <div
      className={`research-workspace ${kind}-workspace${inspectorOpen ? " has-inspector" : ""}`}
      ref={workspaceRef}
      style={style}
    >
      {main}
      {inspectorOpen ? (
        <>
          <WorkspaceResizeSeparator
            pane="right"
            layout={inspectorLayout}
            containerRef={workspaceRef}
            label="调整检查器宽度"
            onChange={(next) => updateLayout({ ...layout, right: next.right })}
            onReset={() => updateLayout(DEFAULT_WORKSPACE_LAYOUT)}
          />
          {inspector}
        </>
      ) : null}
    </div>
  );
}
