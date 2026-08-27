export type AgentWorkspacePane = "left" | "right";

export interface AgentWorkspaceLayout {
  left: number;
  right: number;
}

interface StoredAgentWorkspaceLayout extends AgentWorkspaceLayout {
  version: 1;
}

export const AGENT_WORKSPACE_STORAGE_KEY = "literature-agent:agent-workspace";
export const AGENT_WORKSPACE_DEFAULT: AgentWorkspaceLayout = { left: 260, right: 360 };
export const AGENT_WORKSPACE_BOUNDS = {
  left: { min: 220, max: 420 },
  right: { min: 300, max: 520 },
  centerMin: 600,
  separators: 16,
} as const;

interface ReadStorage {
  getItem(key: string): string | null;
}

interface WriteStorage {
  setItem(key: string, value: string): void;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

function validWidth(value: unknown, pane: AgentWorkspacePane): value is number {
  const bounds = AGENT_WORKSPACE_BOUNDS[pane];
  return Number.isInteger(value) && Number(value) >= bounds.min && Number(value) <= bounds.max;
}

export function loadAgentWorkspaceLayout(storage: ReadStorage): AgentWorkspaceLayout {
  try {
    const raw = storage.getItem(AGENT_WORKSPACE_STORAGE_KEY);
    if (!raw) return AGENT_WORKSPACE_DEFAULT;
    const parsed = JSON.parse(raw) as Partial<StoredAgentWorkspaceLayout>;
    if (
      parsed.version !== 1 ||
      !validWidth(parsed.left, "left") ||
      !validWidth(parsed.right, "right")
    ) return AGENT_WORKSPACE_DEFAULT;
    return { left: parsed.left, right: parsed.right };
  } catch {
    return AGENT_WORKSPACE_DEFAULT;
  }
}

export function saveAgentWorkspaceLayout(
  storage: WriteStorage,
  layout: AgentWorkspaceLayout,
): void {
  try {
    storage.setItem(
      AGENT_WORKSPACE_STORAGE_KEY,
      JSON.stringify({ version: 1, left: layout.left, right: layout.right }),
    );
  } catch {
    // 浏览器禁用 Storage 时保留当前内存布局即可。
  }
}

export function resizeAgentWorkspace(
  layout: AgentWorkspaceLayout,
  pane: AgentWorkspacePane,
  delta: number,
  containerWidth: number,
): AgentWorkspaceLayout {
  const otherWidth = pane === "left" ? layout.right : layout.left;
  const bounds = AGENT_WORKSPACE_BOUNDS[pane];
  const availableMax = containerWidth - otherWidth -
    AGENT_WORKSPACE_BOUNDS.centerMin - AGENT_WORKSPACE_BOUNDS.separators;
  return {
    ...layout,
    [pane]: clamp(layout[pane] + delta, bounds.min, Math.min(bounds.max, availableMax)),
  };
}
