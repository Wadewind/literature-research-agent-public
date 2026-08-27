import {
  DEFAULT_WORKSPACE_LAYOUT,
  WORKSPACE_LAYOUT_BOUNDS,
  loadWorkspaceLayout,
  resizeWorkspace,
  saveWorkspaceLayout,
  type WorkspaceLayout,
  type WorkspacePane,
} from "../workspace/workspaceLayout";

export type AgentWorkspacePane = WorkspacePane;
export type AgentWorkspaceLayout = WorkspaceLayout;

export const AGENT_WORKSPACE_STORAGE_KEY = "literature-agent:agent-workspace";
export const AGENT_WORKSPACE_DEFAULT = DEFAULT_WORKSPACE_LAYOUT;
export const AGENT_WORKSPACE_BOUNDS = WORKSPACE_LAYOUT_BOUNDS;

interface ReadStorage {
  getItem(key: string): string | null;
}

interface WriteStorage {
  setItem(key: string, value: string): void;
}

export function loadAgentWorkspaceLayout(storage: ReadStorage): AgentWorkspaceLayout {
  return loadWorkspaceLayout(storage, "agent");
}

export function saveAgentWorkspaceLayout(
  storage: WriteStorage,
  layout: AgentWorkspaceLayout,
): void {
  saveWorkspaceLayout(storage, "agent", layout);
}

export function resizeAgentWorkspace(
  layout: AgentWorkspaceLayout,
  pane: AgentWorkspacePane,
  delta: number,
  containerWidth: number,
): AgentWorkspaceLayout {
  return resizeWorkspace(layout, pane, delta, containerWidth);
}
