// Mirrors backend/models.py (JSON as serialized by FastAPI / Pydantic v2).

export type Engine = "You.com" | "OpenCrawl" | "Direct HTTP";
export type JobStatus = "NEW" | "OPEN" | "CLOSED" | "GHOST_RISK";
export type Scenario = "early_window_drop" | "ghost_conflict_closed" | "breaking_hiring_freeze";

export interface CitedClaim {
  claim: string;
  source_url: string;
  engine: Engine;
  signal?: string | null;
}

export interface MirrorUrl {
  url: string;
  source: string;
  snippet_signals: string[];
  closed_in_snippet: boolean;
}

export interface CompanyNewsItem {
  headline: string;
  url: string;
  category: string;
  published_at?: string | null;
}

export interface HiringResearch {
  verdict: "EXPANDING" | "CONTRACTING" | "UNCLEAR";
  summary: string;
  sources: string[];
}

export interface FinderOutput {
  canonical_ats_url: string;
  ats_provider: string;
  requisition_id?: string | null;
  company_name: string;
  role_title: string;
  mirror_urls: MirrorUrl[];
  recent_company_news: CompanyNewsItem[];
  hiring_research?: HiringResearch | null;
  receipts: CitedClaim[];
  actions_taken: string[];
  uncertain_fields: string[];
}

export interface ApplyButton {
  found: boolean;
  clickable: boolean;
  selector_or_receipt?: string | null;
}

export interface Timestamps {
  published_at?: string | null;
  updated_at?: string | null;
  valid_through?: string | null;
  source_type?: string | null;
}

export interface Conflict {
  code?: string | null;
  field: string;
  official_value?: string | null;
  mirror_value?: string | null;
  mirror_url?: string | null;
  note?: string | null;
}

export interface JobPostingSchema {
  title?: string | null;
  date_posted?: string | null;
  valid_through?: string | null;
  job_location?: string | null;
  hiring_organization?: string | null;
}

export interface VerifierEvidence {
  http_status: number;
  final_url_matches: boolean;
  apply_button: ApplyButton;
  timestamps: Timestamps;
  closed_signals_detected: string[];
  conflicts: Conflict[];
  opencrawl_session_id?: string | null;
  soft_404: boolean;
  content_hash?: string | null;
  job_posting?: JobPostingSchema | null;
  ashby_is_listed?: boolean | null;
  fetch_engine?: Engine | null;
  dom_reliable: boolean;
  receipts: CitedClaim[];
  actions_taken: string[];
  uncertain_fields: string[];
}

export interface ScorerVerdict {
  open_confidence: number;
  apply_score: number;
  uncertain_fields: string[];
  recommendation: string;
  reasoning_with_citations: CitedClaim[];
  autonomous_action_taken?: string | null;
}

export interface LiveWireEvent {
  id?: number | null;
  job_id?: number | null;
  at: string;
  kind: string;
  message: string;
  simulated: boolean;
  data: Record<string, unknown>;
}

export interface JobView {
  id: number;
  url: string;
  company: string;
  role: string;
  status: JobStatus;
  content_hash?: string | null;
  baseline_hash?: string | null;
  last_verdict?: ScorerVerdict | null;
  last_finder?: FinderOutput | null;
  last_evidence?: VerifierEvidence | null;
  queue?: "PRIORITY" | "ARCHIVED" | null;
  is_demo: boolean;
  last_checked_at?: string | null;
  updated_at: string;
  recent_events: LiveWireEvent[];
}

export interface JobsResponse {
  jobs: JobView[];
  priority_queue: number[];
  archived: number[];
  events: LiveWireEvent[];
}

export interface VerifyResponse {
  outcome: Record<string, unknown>;
  job: JobView | null;
}

export interface SimulateResponse {
  summary: Record<string, unknown>;
  job: JobView | null;
}

export interface PollResponse {
  status: string;
  results: Array<Record<string, unknown>>;
}
