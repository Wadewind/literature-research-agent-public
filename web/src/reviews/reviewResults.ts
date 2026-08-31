import type { ReviewOutput } from "../api/types";

export interface OutlineSection {
  section_key: string;
  title: string;
  purpose: string;
  dimension_keys: string[];
}

export interface MatrixRow {
  paper_id: string;
  dimension_key: string;
  status: string;
  finding: string | null;
  limitations: string | null;
  evidence_ids: string[];
}

export interface SectionClaim {
  text: string;
  evidence_ids: string[];
}

export interface TerminologyEntry {
  term: string;
  definition: string;
}

export interface ReviewSection {
  section_key: string;
  title: string;
  status: string;
  summary: string;
  claims: SectionClaim[];
  terminology: TerminologyEntry[];
}

const SECTION_KEY_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;
const OUTLINE_FIELDS = new Set(["section_key", "title", "purpose", "dimension_keys"]);
const SECTION_FIELDS = new Set([
  "section_key",
  "title",
  "status",
  "summary",
  "claims",
  "terminology",
]);

const DIMENSION_LABELS: Record<string, string> = {
  baselines: "对比基线",
  classical_methods: "经典方法",
  contributions: "主要贡献",
  datasets: "数据集",
  evaluation_metrics: "评价指标",
  experimental_setup: "实验设置",
  future_work: "未来方向",
  learning_based: "学习方法",
  limitations: "研究局限",
  main_findings: "主要结论",
  methodology: "研究方法",
  optimization_based: "优化方法",
  reliability: "可靠性",
  sampling_based: "采样方法",
};

export function dimensionLabel(key: string): string {
  return DIMENSION_LABELS[key] ?? key.replaceAll("_", " ").replaceAll("-", " ");
}

function hasExactKeys(value: Record<string, unknown>, expected: Set<string>): boolean {
  const keys = Object.keys(value);
  return keys.length === expected.size && keys.every((key) => expected.has(key));
}

function strings(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function outlineSections(output: ReviewOutput | undefined): OutlineSection[] {
  const rows = output?.payload.sections;
  if (!Array.isArray(rows)) return [];
  const sections = rows.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (
      !hasExactKeys(row, OUTLINE_FIELDS) ||
      typeof row.section_key !== "string" ||
      typeof row.title !== "string" ||
      typeof row.purpose !== "string" ||
      !strings(row.dimension_keys)
    ) return [];
    return [{
      section_key: row.section_key,
      title: row.title,
      purpose: row.purpose,
      dimension_keys: [...row.dimension_keys],
    }];
  });
  return sections.length === rows.length ? sections : [];
}

export function visibleDimensionKeys(
  sections: OutlineSection[],
  rows: MatrixRow[],
): string[] {
  return [...new Set([
    ...sections.flatMap((section) => section.dimension_keys),
    ...rows.map((row) => row.dimension_key),
  ])];
}

export function outlineDraftIssues(
  sections: OutlineSection[],
  allowedDimensions: string[],
): string[] {
  const issues: string[] = [];
  if (sections.length < 1 || sections.length > 12) issues.push("大纲必须包含 1–12 个章节");
  const keys = sections.map((section) => section.section_key);
  if (keys.some((key) => !SECTION_KEY_PATTERN.test(key))) {
    issues.push("section_key 必须是 64 字符以内的 snake_case 标识符");
  }
  if (new Set(keys).size !== keys.length) issues.push("section_key 不得重复");
  const allowed = new Set(allowedDimensions);
  for (const [index, section] of sections.entries()) {
    const prefix = `第 ${index + 1} 节`;
    if (!section.title.trim() || section.title.length > 200) {
      issues.push(`${prefix}标题必须为 1–200 字符`);
    }
    if (!section.purpose.trim() || section.purpose.length > 1_000) {
      issues.push(`${prefix}目标必须为 1–1000 字符`);
    }
    if (
      section.dimension_keys.length < 1 ||
      section.dimension_keys.length > 6 ||
      new Set(section.dimension_keys).size !== section.dimension_keys.length ||
      section.dimension_keys.some((key) => !allowed.has(key))
    ) {
      issues.push(`${prefix}必须选择 1–6 个不重复的可见分析维度`);
    }
  }
  return issues;
}

export function outlineDraftDirty(
  original: OutlineSection[],
  current: OutlineSection[],
): boolean {
  return JSON.stringify(original) !== JSON.stringify(current);
}

export function moveOutlineSection(
  sections: OutlineSection[],
  index: number,
  direction: -1 | 1,
): OutlineSection[] {
  const target = index + direction;
  if (index < 0 || index >= sections.length || target < 0 || target >= sections.length) {
    return sections;
  }
  const next = [...sections];
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}

export function nextOutlineSection(
  sections: OutlineSection[],
  allowedDimensions: string[],
): OutlineSection {
  let suffix = sections.length + 1;
  while (sections.some((section) => section.section_key === `section_${suffix}`)) suffix += 1;
  return {
    section_key: `section_${suffix}`,
    title: "",
    purpose: "",
    dimension_keys: allowedDimensions.length > 0 ? [allowedDimensions[0]] : [],
  };
}

export function matrixRows(output: ReviewOutput | undefined): MatrixRow[] {
  const rows = output?.payload.rows;
  if (!Array.isArray(rows)) return [];
  const parsed = rows.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    if (
      typeof row.paper_id !== "string" ||
      typeof row.dimension_key !== "string" ||
      typeof row.status !== "string" ||
      !(typeof row.finding === "string" || row.finding === null) ||
      !(typeof row.limitations === "string" || row.limitations === null) ||
      !strings(row.evidence_ids)
    ) return [];
    return [{
      paper_id: row.paper_id,
      dimension_key: row.dimension_key,
      status: row.status,
      finding: row.finding,
      limitations: row.limitations,
      evidence_ids: [...row.evidence_ids],
    }];
  });
  return parsed.length === rows.length ? parsed : [];
}

export function sectionResult(output: ReviewOutput): ReviewSection | null {
  const value = output.payload;
  if (
    !hasExactKeys(value, SECTION_FIELDS) ||
    typeof value.section_key !== "string" ||
    typeof value.title !== "string" ||
    (value.status !== "answered" && value.status !== "insufficient_evidence") ||
    typeof value.summary !== "string" ||
    !Array.isArray(value.claims) ||
    !Array.isArray(value.terminology)
  ) return null;
  if (
    !value.title.trim() ||
    !value.summary.trim() ||
    value.summary.length > 1_000 ||
    value.claims.length > 50 ||
    value.terminology.length > 50
  ) return null;
  const claims = value.claims.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const claim = item as Record<string, unknown>;
    if (
      !hasExactKeys(claim, new Set(["text", "evidence_ids"])) ||
      typeof claim.text !== "string" ||
      !claim.text.trim() ||
      claim.text.length > 4_000 ||
      !strings(claim.evidence_ids) ||
      claim.evidence_ids.length < 1 ||
      claim.evidence_ids.length > 10 ||
      new Set(claim.evidence_ids).size !== claim.evidence_ids.length
    ) return [];
    return [{ text: claim.text, evidence_ids: [...claim.evidence_ids] }];
  });
  if (claims.length !== value.claims.length) return null;
  if (
    (value.status === "answered" && claims.length === 0) ||
    (value.status === "insufficient_evidence" && claims.length > 0)
  ) return null;
  const seenTerms = new Set<string>();
  const terminology = value.terminology.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const term = item as Record<string, unknown>;
    if (
      !hasExactKeys(term, new Set(["term", "definition"])) ||
      typeof term.term !== "string" ||
      typeof term.definition !== "string" ||
      !term.term.trim() ||
      term.term.length > 100 ||
      !term.definition.trim() ||
      term.definition.length > 500 ||
      seenTerms.has(term.term)
    ) return [];
    seenTerms.add(term.term);
    return [{ term: term.term, definition: term.definition }];
  });
  if (terminology.length !== value.terminology.length) return null;
  return {
    section_key: value.section_key,
    title: value.title,
    status: value.status,
    summary: value.summary,
    claims,
    terminology,
  };
}

export function evidenceFileUrl(projectId: string, versionId: string, page: number | null): string {
  const base = `/api/v1/projects/${projectId}/paper-versions/${versionId}/file`;
  return page === null ? base : `${base}#page=${page}`;
}

export function artifactContentUrl(
  projectId: string,
  runId: string,
  artifactId: string,
): string {
  return `/api/v1/projects/${projectId}/reviews/${runId}/artifacts/${artifactId}/content`;
}
