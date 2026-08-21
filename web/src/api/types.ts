/** 与后端 API 响应对齐的 TypeScript 类型。 */

export interface Project {
  project_id: string;
  owner_id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
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

export interface VersionSummary {
  version_id: string;
  display_filename: string;
  size_bytes: number;
  created_at: string;
  parse_ready: boolean;
  ingestion_run_id: string | null;
}

export interface PaperListItem {
  paper_id: string;
  created_at: string;
  version: VersionSummary;
  project_ids: string[];
  archived_at: string | null;
}

export interface UploadResult {
  run_id: string | null;
  paper_id: string;
  version_id: string;
  status: string;
  reused: boolean;
  already_added: boolean;
  paper_archived: boolean;
}

export interface ProjectPaperResult {
  project_id: string;
  paper_id: string;
  selected_version_id: string;
  already_added: boolean;
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

export interface IndexStatus {
  revision_id: string;
  chunk_set: {
    chunk_set_id: string;
    status: string;
    chunk_count: number;
    embedded_count: number;
    profile_hash: string;
  } | null;
  indexing_run_id: string | null;
}

export interface ScopePaper {
  paper_id: string;
  version_id: string;
}

export interface Conversation {
  conversation_id: string;
  project_id: string;
  owner_id: string;
  title: string | null;
  scope_mode: "project" | "selected_papers";
  active_run_id: string | null;
  created_at: string;
  scope_papers: ScopePaper[];
}

export interface CitationSummary {
  evidence_id: string;
  paper_id: string;
  version_id: string;
  section_path: string | null;
  page_start: number | null;
  page_end: number | null;
  excerpt: string;
}

export interface Claim {
  text: string;
  citations: CitationSummary[];
}

export interface ConversationMessage {
  message_id: string;
  conversation_id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  run_id: string | null;
  claim_set_id: string | null;
  created_at: string;
  claims: Claim[] | null;
}

export interface PostMessageResult {
  user_message_id: string;
  run_id: string;
  status: string;
}

export interface EvidenceDetail extends CitationSummary {
  run_id: string;
  project_id: string;
  parse_revision_id: string;
  chunk_id: string;
  created_at: string;
}
