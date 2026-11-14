"""Core schemas for the JobVerify 3-agent pipeline (Finder -> Verifier -> Scorer)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Shared enums / literals
# --------------------------------------------------------------------------- #
AtsProvider = Literal["ashby", "greenhouse", "lever", "workday", "other"]
Engine = Literal["You.com", "OpenCrawl", "Direct HTTP"]  # "Direct HTTP" = public ATS API / status probe (no sponsor engine)
Recommendation = Literal[
    "立即投递 / Apply Immediately",
    "谨慎投递 / Apply with Caution",
    "不建议投递 / Do Not Apply",
]


class JobStatus(str, Enum):
    NEW = "NEW"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    GHOST_RISK = "GHOST_RISK"


class CitedClaim(StrictModel):
    """One auditable receipt: a claim, where it came from, and which engine produced it."""

    claim: str
    source_url: HttpUrl
    engine: Engine
    signal: Optional[str] = Field(
        default=None,
        description="Machine tag (http_status, apply_button, closed_phrase, json_ld, ashby_api, "
        "mirror, news, hiring_research, conflict, ...) used by the Scorer to pick receipts",
    )


# --------------------------------------------------------------------------- #
# Agent 1: Finder
# --------------------------------------------------------------------------- #
class MirrorUrl(StrictModel):
    url: HttpUrl
    source: str = Field(description="e.g. linkedin, indeed, glassdoor")
    snippet_signals: list[str] = Field(
        default_factory=list,
        description='Raw snippet signals, e.g. "Be an early applicant"',
    )
    closed_in_snippet: bool = Field(
        default=False, description="Mirror snippet itself carries closing language"
    )


class CompanyNewsItem(StrictModel):
    headline: str
    url: HttpUrl
    category: Literal["layoffs", "hiring_freeze", "funding", "acquisition", "other"]
    published_at: Optional[datetime] = None


class HiringResearch(StrictModel):
    """Result of the You.com Research API call used when hiring status is ambiguous."""

    verdict: Literal["EXPANDING", "CONTRACTING", "UNCLEAR"]
    summary: str
    sources: list[HttpUrl] = Field(default_factory=list)


class FinderOutput(StrictModel):
    canonical_ats_url: HttpUrl
    ats_provider: AtsProvider
    requisition_id: Optional[str] = None
    company_name: str
    role_title: str
    mirror_urls: list[MirrorUrl] = Field(default_factory=list)
    recent_company_news: list[CompanyNewsItem] = Field(
        default_factory=list,
        description="Layoffs, hiring freezes, funding in the last 24-72h",
    )
    hiring_research: Optional[HiringResearch] = None
    receipts: list[CitedClaim] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Agent 2: Verifier
# --------------------------------------------------------------------------- #
class ApplyButton(StrictModel):
    found: bool
    clickable: bool
    selector_or_receipt: Optional[str] = Field(
        default=None,
        description="CSS selector matched, or OpenCrawl action receipt id",
    )


class Timestamps(StrictModel):
    published_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    valid_through: Optional[datetime] = None
    source_type: Optional[Literal["json_ld", "ats_api", "meta_tag", "visible_text", "mirror"]] = None


class JobPostingSchema(StrictModel):
    """Subset of schema.org JobPosting parsed from <script type="application/ld+json">."""

    title: Optional[str] = None
    date_posted: Optional[datetime] = None
    valid_through: Optional[datetime] = None
    job_location: Optional[str] = None
    hiring_organization: Optional[str] = None


class Conflict(StrictModel):
    code: Optional[str] = Field(
        default=None, description="e.g. CONFLICT_CLOSED_ON_SOURCE, CONFLICT_CLOSED_ON_MIRROR, ENGINE_DISAGREEMENT"
    )
    field: str
    official_value: Optional[str] = None
    mirror_value: Optional[str] = None
    mirror_url: Optional[HttpUrl] = None
    note: Optional[str] = None


class VerifierEvidence(StrictModel):
    http_status: int
    final_url_matches: bool = Field(
        description="Final URL after redirects still matches the canonical posting"
    )
    apply_button: ApplyButton
    timestamps: Timestamps = Field(default_factory=Timestamps)
    closed_signals_detected: list[str] = Field(
        default_factory=list,
        description='e.g. "job no longer available", "position filled"',
    )
    conflicts: list[Conflict] = Field(
        default_factory=list,
        description="Discrepancies between the official ATS and third-party mirrors",
    )
    opencrawl_session_id: Optional[str] = None
    # --- extra evidence used by the deterministic Scorer ---
    soft_404: bool = False
    content_hash: Optional[str] = Field(default=None, description="SHA-256 of normalized posting text")
    job_posting: Optional[JobPostingSchema] = None
    ashby_is_listed: Optional[bool] = None
    fetch_engine: Optional[Engine] = Field(default=None, description="Engine that produced the page DOM/text")
    dom_reliable: bool = Field(
        default=False,
        description="True when the DOM is a rendered capture (or static HTML on a non-JS ATS), "
        "so a missing Apply control is meaningful",
    )
    receipts: list[CitedClaim] = Field(default_factory=list)
    actions_taken: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Agent 3: Scorer
# --------------------------------------------------------------------------- #
class ScorerVerdict(StrictModel):
    open_confidence: int = Field(ge=0, le=100)
    apply_score: int = Field(ge=0, le=100)
    uncertain_fields: list[str] = Field(default_factory=list)
    recommendation: Recommendation
    reasoning_with_citations: list[CitedClaim] = Field(default_factory=list)
    autonomous_action_taken: Optional[str] = Field(
        default=None,
        description="e.g. 'Re-crawled ATS page', 'Added to watchlist'",
    )


class PipelineResult(StrictModel):
    query: str
    finder: Optional[FinderOutput] = None
    evidence: Optional[VerifierEvidence] = None
    verdict: Optional[ScorerVerdict] = None
    errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Persistence: watched jobs + Live Wire event log
# --------------------------------------------------------------------------- #
class LiveWireEvent(StrictModel):
    """One entry in the autonomous-action feed (also persisted in `job_events`)."""

    id: Optional[int] = None
    job_id: Optional[int] = None
    at: datetime = Field(default_factory=utcnow)
    kind: Literal[
        "seed", "checked", "status_change", "promoted", "outreach_draft",
        "webhook", "archived", "alert", "error", "simulated",
    ]
    message: str
    simulated: bool = False
    data: dict[str, Any] = Field(default_factory=dict)


class WatchedJobState(StrictModel):
    """Row model for the `watched_jobs` SQLite table (DDL lives in backend/db.py)."""

    id: Optional[int] = None
    url: str
    company: str
    role: str
    status: JobStatus = JobStatus.NEW
    content_hash: Optional[str] = None
    baseline_hash: Optional[str] = Field(default=None, description="First content hash seen; used for stale-repost detection")
    last_verdict: Optional[ScorerVerdict] = None
    last_finder: Optional[FinderOutput] = None
    last_evidence: Optional[VerifierEvidence] = None
    queue: Optional[Literal["PRIORITY", "ARCHIVED"]] = None
    is_demo: bool = Field(default=False, description="Seeded demo entry: never live-polled, only simulated")
    last_checked_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


class JobView(WatchedJobState):
    recent_events: list[LiveWireEvent] = Field(default_factory=list)
