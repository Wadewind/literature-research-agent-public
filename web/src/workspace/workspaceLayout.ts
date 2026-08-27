export type WorkspaceKind = "agent" | "chat";
export type WorkspacePane = "left" | "right";

export interface WorkspaceLayout {
  left: number;
  right: number;
}

interface StoredWorkspaceLayout extends WorkspaceLayout {
  version: 1;
}

export const DEFAULT_WORKSPACE_LAYOUT: WorkspaceLayout = { left: 260, right: 360 };
export const WORKSPACE_LAYOUT_BOUNDS = {
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

function storageKey(kind: WorkspaceKind): string {
  return `literature-agent:${kind}-workspace`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

function validWidth(value: unknown, pane: WorkspacePane): value is number {
  const bounds = WORKSPACE_LAYOUT_BOUNDS[pane];
  return Number.isInteger(value) && Number(value) >= bounds.min && Number(value) <= bounds.max;
}

export function loadWorkspaceLayout(
  storage: ReadStorage,
  kind: WorkspaceKind,
): WorkspaceLayout {
  try {
    const raw = storage.getItem(storageKey(kind));
    if (!raw) return DEFAULT_WORKSPACE_LAYOUT;
    const parsed = JSON.parse(raw) as Partial<StoredWorkspaceLayout>;
    if (
      parsed.version !== 1 ||
      !validWidth(parsed.left, "left") ||
      !validWidth(parsed.right, "right")
    ) return DEFAULT_WORKSPACE_LAYOUT;
    return { left: parsed.left, right: parsed.right };
  } catch {
    return DEFAULT_WORKSPACE_LAYOUT;
  }
}

export function saveWorkspaceLayout(
  storage: WriteStorage,
  kind: WorkspaceKind,
  layout: WorkspaceLayout,
): void {
  try {
    storage.setItem(
      storageKey(kind),
      JSON.stringify({ version: 1, left: layout.left, right: layout.right }),
    );
  } catch {
    // 浏览器禁用 Storage 时保留当前内存布局即可。
  }
}

export function resizeWorkspace(
  layout: WorkspaceLayout,
  pane: WorkspacePane,
  delta: number,
  containerWidth: number,
): WorkspaceLayout {
  const otherWidth = pane === "left" ? layout.right : layout.left;
  const bounds = WORKSPACE_LAYOUT_BOUNDS[pane];
  const availableMax = containerWidth - otherWidth -
    WORKSPACE_LAYOUT_BOUNDS.centerMin - WORKSPACE_LAYOUT_BOUNDS.separators;
  return {
    ...layout,
    [pane]: clamp(layout[pane] + delta, bounds.min, Math.min(bounds.max, availableMax)),
  };
}
