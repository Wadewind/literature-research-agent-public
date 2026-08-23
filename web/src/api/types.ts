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

export interface ReviewRun {
  run_id: string;
  research_question: string;
  workflow_version: string;
  model_profile_version: string;
  statistics_summary: Record<string, number>;
  current_stage: string;
  current_outline_output_id: string | null;
  final_artifact_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ReviewListItem {
  run_id: string;
  status: string;
  research_question: string;
  current_stage: string;
  created_at: string;
  updated_at: string;
}

export interface ReviewStep {
  step_id: string;
  run_id: string;
  step_key: string;
  sequence: number;
  status: string;
  error_code: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
}

export interface HumanInputRequest {
  request_id: string;
  request_version: number;
  outline_output_id: string;
  allowed_actions: string[];
  status: string;
  created_at: string;
}

export interface ReviewDetail {
  run: Run;
  review: ReviewRun;
  steps: ReviewStep[];
  open_human_input_request: HumanInputRequest | null;
}

export interface ReviewSource {
  source_id: string;
  review_run_id: string;
  arxiv_id: string;
  arxiv_version: string;
  rank: number;
  metadata_snapshot: Record<string, unknown>;
  status: string;
  paper_id: string | null;
  paper_version_id: string | null;
  failure_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateReviewResult {
  run_id: string;
  status: string;
  reused: boolean;
}
