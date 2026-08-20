/** 与后端 API 响应对齐的 TypeScript 类型（切片 10 最小集）。 */

export interface Project {
  project_id: string;
  owner_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface Run {
  run_id: string;
  project_id: string;
  owner_id: string;
  run_type: string;
  status: string;
  input_payload: Record<string, unknown>;
  result_payload: Record<string, unknown>;
  event_sequence: number;
  created_at: string;
  updated_at: string;
}

export interface RunEvent {
  event_id: string;
  event_version: string;
  event_type: string;
  run_id: string;
  sequence: number;
  occurred_at: string;
  actor_type: string;
  correlation_id: string;
  payload: Record<string, unknown>;
}

export interface LatestVersion {
  version_id: string;
  display_filename: string;
  size_bytes: number;
  created_at: string;
  parse_ready: boolean;
}

export interface PaperListItem {
  paper_id: string;
  created_at: string;
  latest_version: LatestVersion | null;
}

export interface UploadResult {
  run_id: string;
  paper_id: string;
  version_id: string;
  status: string;
}

export interface SectionInfo {
  section_path: string;
  title: string;
}

export interface DocumentOverview {
  revision_id: string;
  parser_name: string;
  parser_version: string;
  parser_profile_hash: string;
  status: string;
  completed_at: string | null;
  element_count: number;
  degraded: boolean;
  warnings: string[];
  sections: SectionInfo[];
}

export interface SourceLocation {
  page: number;
  bbox: number[] | null;
  parser_ref: string | null;
  char_range: number[] | null;
}

export interface DocElement {
  element_id: string;
  element_type: string;
  sequence: number;
  parent_element_id: string | null;
  section_path: string | null;
  text: string | null;
  payload: Record<string, unknown>;
  content_hash: string;
  warnings: string[];
  locations: SourceLocation[];
}
