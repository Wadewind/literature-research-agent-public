/** Project 内 Chat 入口的纯选择状态。 */

export interface ScopeSelection {
  paperIds: string[];
}

export interface ScopeRequest {
  scope_mode: "project" | "selected_papers";
  paper_ids: string[] | undefined;
}

export function createScopeSelection(paperIds: string[] = []): ScopeSelection {
  return { paperIds: [...new Set(paperIds)] };
}

export function toggleScopePaper(
  selection: ScopeSelection,
  paperId: string,
): ScopeSelection {
  if (selection.paperIds.includes(paperId)) {
    return { paperIds: selection.paperIds.filter((id) => id !== paperId) };
  }
  return { paperIds: [...selection.paperIds, paperId] };
}

export function scopeRequest(selection: ScopeSelection): ScopeRequest {
  if (selection.paperIds.length === 0) {
    return { scope_mode: "project", paper_ids: undefined };
  }
  return { scope_mode: "selected_papers", paper_ids: selection.paperIds };
}
