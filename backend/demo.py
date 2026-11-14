"""Demo support: synthetic snapshots for seeded jobs and simulated deltas.

Everything produced here is explicitly labelled (is_demo on seeded rows, "[SIMULATED]" in events and
actions_taken). The deltas run through the *real* Scorer and Watcher so the autonomous behaviour shown
on stage is the production code path, not a canned UI.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

from backend.agents.common import parse_ats_url
from backend.models import (
    ApplyButton, CitedClaim, CompanyNewsItem, Conflict, FinderOutput, HiringResearch, MirrorUrl,
    Timestamps, VerifierEvidence, WatchedJobState,
)

Scenario = Literal["early_window_drop", "ghost_conflict_closed", "breaking_hiring_freeze"]
SCENARIOS: tuple[str, ...] = ("early_window_drop", "ghost_conflict_closed", "breaking_hiring_freeze")


@dataclass
class Ident:
    url: str
    company: str
    role: str
    mirror_url: str


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def ident_from_finder(f: FinderOutput) -> Ident:
    url = str(f.canonical_ats_url)
    mirror = str(f.mirror_urls[0].url) if f.mirror_urls else f"https://www.linkedin.com/jobs/view/{int(_h(url)[:9], 16)}"
    return Ident(url=url, company=f.company_name, role=f.role_title, mirror_url=mirror)


def synth(
    i: Ident, *, now: datetime, mode: Literal["open", "filled", "removed"], published_at: Optional[datetime],
    mirror_signals: list[str], mirror_listed: bool = True, content_hash: str, note: str,
) -> tuple[FinderOutput, VerifierEvidence]:
    provider, org, job_id = parse_ats_url(i.url)
    f_receipts = [CitedClaim(claim=f"Canonical {provider.title()} posting located for '{i.role}' at {i.company}",
                             source_url=i.url, engine="You.com", signal="canonical")]
    mirror = MirrorUrl(url=i.mirror_url, source="linkedin", snippet_signals=mirror_signals, closed_in_snippet=not mirror_listed)
    if mirror_signals:
        f_receipts.append(CitedClaim(claim=f"LinkedIn mirror snippet signals: {', '.join(mirror_signals)}",
                                     source_url=i.mirror_url, engine="You.com", signal="mirror"))
    finder = FinderOutput(
        canonical_ats_url=i.url, ats_provider=provider, requisition_id=job_id,  # type: ignore[arg-type]
        company_name=i.company, role_title=i.role, mirror_urls=[mirror],
        receipts=f_receipts, actions_taken=[note],
    )

    ts = Timestamps(published_at=published_at,
                    source_type=("ats_api" if provider == "ashby" else "json_ld") if published_at else None)
    r: list[CitedClaim] = []
    closed: list[str] = []
    conflicts: list[Conflict] = []
    listed: Optional[bool] = None
    if mode == "open":
        status, apply_btn = 200, ApplyButton(found=True, clickable=True, selector_or_receipt="button :: 'Apply for this job'")
        r.append(CitedClaim(claim="Page returned HTTP 200; final URL matches the canonical posting", source_url=i.url, engine="OpenCrawl", signal="http_status"))
        r.append(CitedClaim(claim="Apply control found and enabled via button :: 'Apply for this job'", source_url=i.url, engine="OpenCrawl", signal="apply_button"))
        if provider == "ashby" and org:
            listed = True
            r.append(CitedClaim(claim=f"Ashby API: isListed=True, publishedAt={published_at.isoformat() if published_at else None}",
                                source_url=f"https://api.ashbyhq.com/posting-api/job-board/{org}", engine="Direct HTTP", signal="ashby_api"))
        elif published_at:
            r.append(CitedClaim(claim=f"JobPosting schema: datePosted={published_at.date()}", source_url=i.url, engine="OpenCrawl", signal="json_ld"))
    elif mode == "filled":
        status, apply_btn = 200, ApplyButton(found=False, clickable=False)
        closed = ["position filled"]
        r.append(CitedClaim(claim="Page returned HTTP 200; final URL matches the canonical posting", source_url=i.url, engine="OpenCrawl", signal="http_status"))
        r.append(CitedClaim(claim="Closing language on the live page: 'position filled'", source_url=i.url, engine="OpenCrawl", signal="closed_phrase"))
    else:  # removed
        status, apply_btn = 404, ApplyButton(found=False, clickable=False)
        r.append(CitedClaim(claim="Page returned HTTP 404 - posting removed from the official ATS", source_url=i.url, engine="OpenCrawl", signal="http_status"))
        if provider == "ashby" and org:
            listed = False
            closed = ["ashby: job absent from public posting board (isListed=false)"]
            r.append(CitedClaim(claim="Ashby API: job id not present on the public posting board",
                                source_url=f"https://api.ashbyhq.com/posting-api/job-board/{org}", engine="Direct HTTP", signal="ashby_api"))
        else:
            closed = ["this job is no longer available"]
            r.append(CitedClaim(claim="Closing language on the live page: 'this job is no longer available'", source_url=i.url, engine="OpenCrawl", signal="closed_phrase"))
    if mode != "open" and mirror_listed:
        conflicts.append(Conflict(code="CONFLICT_CLOSED_ON_SOURCE", field="status", official_value="closed",
                                  mirror_value="active" + (f" ({', '.join(mirror_signals)})" if mirror_signals else ""),
                                  mirror_url=i.mirror_url,  # type: ignore[arg-type]
                                  note="Mirror still appears live while the official ATS posting is closed"))
        r.append(CitedClaim(claim="CONFLICT_CLOSED_ON_SOURCE: mirror listing still appears active but the official ATS posting is closed",
                            source_url=i.mirror_url, engine="You.com", signal="conflict"))
    evidence = VerifierEvidence(
        http_status=status, final_url_matches=True, apply_button=apply_btn, timestamps=ts,
        closed_signals_detected=closed, conflicts=conflicts, ashby_is_listed=listed, fetch_engine="OpenCrawl",
        dom_reliable=True, content_hash=content_hash, receipts=r, actions_taken=[note],
    )
    return finder, evidence


# --------------------------------------------------------------------------- seeds
@dataclass
class SeedSpec:
    company: str
    role: str
    url: str
    mirror_url: str
    mode: Literal["open", "filled", "removed"]
    published_ago: Optional[timedelta]
    mirror_signals: list[str]
    queue: Optional[Literal["PRIORITY", "ARCHIVED"]] = None


SEEDS: list[SeedSpec] = [
    SeedSpec("Lumen Labs", "Senior Backend Engineer", "https://jobs.ashbyhq.com/lumenlabs/7d3f2c1e-5a4b-4c8e-9f21-0b6a8e4d1a90",
             "https://www.linkedin.com/jobs/view/4012345678", "open", timedelta(hours=30), ["Be an early applicant"], "PRIORITY"),
    SeedSpec("Northwind Robotics", "ML Platform Engineer", "https://boards.greenhouse.io/northwindrobotics/jobs/4821937",
             "https://www.linkedin.com/jobs/view/4023456789", "open", timedelta(days=9), ["Over 100 applicants"]),
    SeedSpec("Helix Payments", "Staff Frontend Engineer", "https://jobs.lever.co/helixpay/b91c0a3e-2d77-45f0-8a1e-3c5d9e7f6a12",
             "https://www.linkedin.com/jobs/view/4034567890", "filled", timedelta(days=41), ["Reposted 2 weeks ago"]),
]


def build_seed(spec: SeedSpec, now: datetime) -> WatchedJobState:
    from backend.agents.scorer import score, status_for

    note = "DEMO SEED: synthetic snapshot (no live crawl)"
    finder, evidence = synth(
        Ident(spec.url, spec.company, spec.role, spec.mirror_url), now=now, mode=spec.mode,
        published_at=(now - spec.published_ago) if spec.published_ago else None,
        mirror_signals=spec.mirror_signals, content_hash=_h(spec.url + "seed"), note=note)
    verdict = score(finder, evidence, now=now)
    return WatchedJobState(
        url=spec.url, company=spec.company, role=spec.role, status=status_for(verdict),
        content_hash=evidence.content_hash, baseline_hash=evidence.content_hash,
        last_verdict=verdict, last_finder=finder, last_evidence=evidence, queue=spec.queue,
        is_demo=True, last_checked_at=now, updated_at=now)


# --------------------------------------------------------------------------- scenarios
def apply_scenario(job: WatchedJobState, scenario: str, now: datetime) -> tuple[FinderOutput, VerifierEvidence]:
    """Build the post-delta (Finder, Evidence) pair for a scenario from the job's last stored snapshot."""
    assert job.last_finder is not None and job.last_evidence is not None
    f, e = job.last_finder, job.last_evidence
    ident = ident_from_finder(f)
    note = f"SIMULATED delta injected: {scenario}"
    if scenario == "early_window_drop":
        return synth(ident, now=now, mode="open", published_at=now - timedelta(minutes=7),
                     mirror_signals=["Be an early applicant"], content_hash=_h(ident.url + "early" + now.isoformat()), note=note)
    if scenario == "ghost_conflict_closed":
        return synth(ident, now=now, mode="removed", published_at=e.timestamps.published_at,
                     mirror_signals=["Over 200 applicants"], mirror_listed=True, content_hash=e.content_hash or _h(ident.url), note=note)
    if scenario == "breaking_hiring_freeze":
        slug = _h(ident.company)[:8]
        news_url = f"https://example.com/simulated-news/{slug}-hiring-freeze"
        item = CompanyNewsItem(headline=f"{ident.company} reportedly pauses engineering hiring", url=news_url,  # type: ignore[arg-type]
                               category="hiring_freeze", published_at=now - timedelta(hours=2))
        research = HiringResearch(verdict="CONTRACTING", sources=[news_url],  # type: ignore[list-item]
                                  summary="Simulated: reports indicate a company-wide pause on new engineering requisitions.")
        f2 = f.model_copy(update={
            "recent_company_news": [*f.recent_company_news, item],
            "hiring_research": research,
            "receipts": [*f.receipts,
                         CitedClaim(claim=f"Recent company news (hiring_freeze): {item.headline}", source_url=news_url, engine="You.com", signal="news"),
                         CitedClaim(claim="You.com Research: hiring status CONTRACTING - " + research.summary, source_url=news_url, engine="You.com", signal="hiring_research")],
            "actions_taken": [*f.actions_taken, note],
        })
        return f2, e
    raise ValueError(f"unknown scenario {scenario!r}")
