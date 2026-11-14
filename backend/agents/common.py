"""Shared helpers for the agents: ATS URL parsing, closing phrases, small utilities."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

# Phrases that indicate a posting is closed. Matched case-insensitively against page text.
CLOSED_PHRASES: tuple[str, ...] = (
    "job no longer available",
    "this job is no longer available",
    "position filled",
    "position has been filled",
    "no longer accepting applications",
    "this position is closed",
    "this job is closed",
    "job is no longer open",
    "is no longer open",
    "posting has expired",
    "job has expired",
)

# host substring -> provider
ATS_HOSTS: dict[str, str] = {
    "jobs.ashbyhq.com": "ashby",
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "myworkdayjobs.com": "workday",
}

AGGREGATOR_HOSTS: tuple[str, ...] = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com", "simplyhired.com",
    "monster.com", "builtin.com", "wellfound.com", "jooble.org", "talent.com", "adzuna.com",
)

JS_RENDERED_PROVIDERS = {"ashby", "workday"}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def ats_provider_for(url: str) -> str:
    host = host_of(url)
    for needle, provider in ATS_HOSTS.items():
        if needle in host:
            return provider
    return "other"


def is_aggregator(url: str) -> bool:
    host = host_of(url)
    return any(host == a or host.endswith("." + a) for a in AGGREGATOR_HOSTS)


def parse_ats_url(url: str) -> tuple[str, Optional[str], Optional[str]]:
    """Return (provider, org_slug, requisition_id) best-effort from an ATS URL."""
    provider = ats_provider_for(url)
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)
    org = job_id = None
    if provider in ("ashby", "lever"):
        org = parts[0] if parts else None
        job_id = parts[1] if len(parts) > 1 else None
    elif provider == "greenhouse":
        org = parts[0] if parts else None
        if "jobs" in parts:
            i = parts.index("jobs")
            job_id = parts[i + 1] if len(parts) > i + 1 else None
        job_id = job_id or (qs.get("gh_jid") or [None])[0]
    elif provider == "workday":
        org = (u.hostname or "").split(".")[0] or None
        m = re.search(r"_(R?-?\d[\w-]*)$", parts[-1]) if parts else None
        job_id = m.group(1) if m else None
    return provider, org, job_id


def parse_dt(value: object) -> Optional[datetime]:
    """Tolerant datetime parser (ISO strings, date-only, epoch). Always returns aware UTC or None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / (1000 if value > 1e11 else 1), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(value.strip(), fmt)
                break
            except ValueError:
                dt = None  # type: ignore[assignment]
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def find_closed_phrases(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for phrase in CLOSED_PHRASES:
        if phrase in low and not any(phrase in f for f in found):
            found.append(phrase)
    # drop phrases subsumed by a longer match already found
    return [p for p in found if not any(p != q and p in q for q in found)]
