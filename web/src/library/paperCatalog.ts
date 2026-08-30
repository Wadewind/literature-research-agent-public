import type { PaperListItem } from "../api/types";

export type PaperStatusFilter = "all" | "ready" | "working" | "archived";
export type PaperSort = "recent" | "title";

export interface PaperCatalogFilters {
  query: string;
  status: PaperStatusFilter;
  project: string;
  sort: PaperSort;
}

export function paperDisplayTitle(paper: PaperListItem): string {
  return paper.title?.trim() || paper.version.display_filename;
}

function matchesStatus(paper: PaperListItem, status: PaperStatusFilter): boolean {
  if (status === "all") return true;
  if (status === "archived") return Boolean(paper.archived_at);
  if (paper.archived_at) return false;
  return status === "ready" ? paper.version.parse_ready : !paper.version.parse_ready;
}

function matchesProject(paper: PaperListItem, project: string): boolean {
  if (project === "all") return true;
  if (project === "unassigned") return paper.project_ids.length === 0;
  return paper.project_ids.includes(project);
}

export function filterAndSortPapers(
  papers: readonly PaperListItem[],
  filters: PaperCatalogFilters,
): PaperListItem[] {
  const query = filters.query.trim().toLocaleLowerCase();
  const result = papers.filter((paper) => {
    const matchesQuery = !query || [
      paperDisplayTitle(paper),
      paper.version.display_filename,
    ].some((value) => value.toLocaleLowerCase().includes(query));
    return matchesQuery
      && matchesStatus(paper, filters.status)
      && matchesProject(paper, filters.project);
  });

  return result.sort((left, right) => {
    if (filters.sort === "title") {
      const byTitle = paperDisplayTitle(left).localeCompare(
        paperDisplayTitle(right),
        "zh-CN",
        { numeric: true, sensitivity: "base" },
      );
      return byTitle || left.paper_id.localeCompare(right.paper_id);
    }
    return right.created_at.localeCompare(left.created_at)
      || left.paper_id.localeCompare(right.paper_id);
  });
}
