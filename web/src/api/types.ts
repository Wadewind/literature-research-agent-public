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
  evidence_matrix: {
    output_id: string;
    version: number;
    row_count: number;
    valid_papers: number;
    failed_papers: number;
  } | null;
}

export interface ProjectAgentContextSummary {
  ready_index_count: number;
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

export interface ReviewOutput {
  output_id: string;
  review_run_id: string;
  output_type: string;
  output_key: string;
  version: number;
  schema_version: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface ReviewArtifact {
  artifact_id: string;
  review_run_id: string;
  project_id: string;
  artifact_type: string;
  content_hash: string;
  size_bytes: number;
  media_type: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AgentSession {
  session_id: string;
  project_id: string;
  title: string | null;
  status: string;
  active_turn_run_id: string | null;
  created_at: string;
  last_activity_at: string;
}

export interface AgentMessage {
  message_id: string;
  session_id: string;
  sequence: number;
  role: "user" | "assistant";
  content: string;
  turn_run_id: string;
  claim_set_id: string | null;
  created_at: string;
  claims: Claim[] | null;
  attachment_ids: string[];
}

export interface AgentAttachment {
  attachment_id: string;
  session_id: string;
  version: number;
  display_name: string;
  media_type: string;
  content_hash: string;
  size_bytes: number;
  status: "available" | "deleted";
  created_at: string;
}

export interface AgentCandidate {
  candidate_id: string;
  name: string;
  media_type: string;
  content_hash: string;
  size_bytes: number;
  status: string;
  rejection_code: string | null;
}

export interface AgentArtifact {
  artifact_id: string;
  turn_run_id: string;
  name: string;
  media_type: string;
  content_hash: string;
  size_bytes: number;
  previewable: boolean;
  created_at: string;
}

export interface AgentTurnUsage {
  max_model_calls: number;
  max_tool_calls: number;
  model_calls_reserved: number;
  tool_calls_reserved: number;
  wall_clock_limit_seconds: number;
  tool_timeout_seconds: number;
  execute_timeout_seconds: number;
  max_tool_output_bytes: number;
  max_repeated_tool_calls: number;
  max_input_tokens_per_model_call: number;
  max_output_tokens_per_model_call: number;
  input_tokens: number | null;
  output_tokens: number | null;
  started_at: string | null;
  deadline_at: string | null;
}

export interface AgentToolExecution {
  invocation_id: string;
  tool_name: string;
  tool_version: string;
  input_schema_hash: string;
  args_hash: string;
  status: string;
  input_size_bytes: number;
  input_preview: string | null;
  input_preview_truncated: boolean;
  output_size_bytes: number | null;
  output_preview: string | null;
  output_preview_truncated: boolean;
  result_hash: string | null;
  error_code: string | null;
  safe_message: string | null;
  duration_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface AgentToolExecutionsResponse {
  usage: AgentTurnUsage;
  items: AgentToolExecution[];
}

export interface AgentArtifactManifestItem {
  artifact_id: string;
  name: string;
  media_type: string;
  content_hash: string;
  size_bytes: number;
  source_url: string | null;
  source_url_hash: string | null;
  source_status: "not_provided" | "declared_public_target_checked";
  created_at: string;
}

export interface AgentArtifactManifest {
  run_id: string;
  items: AgentArtifactManifestItem[];
}

export interface BrowserControl {
  control_id: string;
  session_id: string;
  mode: "manual";
  status: "active" | "ended" | "expired";
  revision: number;
  sandbox_generation: number;
  started_at: string;
  expires_at: string;
  ended_at: string | null;
  end_reason: string | null;
  viewer_connected: boolean;
}

export interface BrowserControlStatus {
  control: BrowserControl | null;
}

export interface BrowserControlStart {
  control: BrowserControl;
  ticket: string;
  view_url: string;
}

export interface AgentTurn {
  run_id: string;
  session_id: string;
  project_id: string;
  status: string;
  user_message_id: string;
  context_snapshot_id: string;
  policy_snapshot_id: string;
  review_output_id: string;
  project_index_refs: Array<{
    paper_id: string;
    paper_version_id: string;
    chunk_set_id: string;
  }>;
  candidates: AgentCandidate[];
}

export interface McpCatalogParameter {
  name: string;
  required: boolean;
  max_length: number;
}

export interface McpCatalogEntry {
  catalog_id: string;
  version: string;
  display_name: string;
  parameters: McpCatalogParameter[];
  tools: Array<{ name: string; input_schema_hash: string }>;
}

export interface McpProfileSelection {
  catalog_id: string;
  version: string;
  parameters: Record<string, string>;
}

export interface McpProfile {
  session_id: string;
  revision: number;
  config_hash: string;
  selections: McpProfileSelection[];
}

export interface AgentSkill {
  skill_id: string;
  source: "platform" | "owner";
  version: number;
  name: string;
  description: string;
  instructions: string;
  required_tool_names: string[];
  content_hash: string;
}

export interface SkillProfileSelection {
  source: "platform" | "owner";
  skill_id: string;
  version: number;
}

export interface SkillProfile {
  session_id: string;
  revision: number;
  config_hash: string;
  selections: SkillProfileSelection[];
}
