"""Agent 1 - Finder (powered by You.com Search + Research).

Input: a noisy job URL (LinkedIn/Indeed/aggregator/company page/ATS) or a "company + role" query.
Output: FinderOutput with the canonical ATS URL, LinkedIn-style mirror signals, last-24-72h company
news and (only when hiring status is ambiguous) a You.com Research verdict. Every fact that came
from You.com is also recorded as a CitedClaim receipt so downstream agents can cite it.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Optional
from urllib.parse import urlparse

from pydantic import HttpUrl, TypeAdapter, ValidationError

from backend.agents.common import (
    ats_provider_for, find_closed_phrases, host_of, is_aggregator, parse_ats_url,
)
from backend.config import Settings, get_settings
from backend.logging_config import log
from backend.models import (
    CitedClaim, CompanyNewsItem, FinderOutput, HiringResearch, MirrorUrl,
)
from backend.tools.you_client import SearchHit, YouClient

logger = logging.getLogger("jobverify.finder")
_url = TypeAdapter(HttpUrl)

ATS_SITES = ["jobs.ashbyhq.com", "boards.greenhouse.io", "jobs.lever.co", "myworkdayjobs.com"]
_STOP = {"the", "and", "inc", "llc", "ltd", "corp", "co", "for", "with", "job", "jobs", "career", "careers", "at"}

SIGNAL_PATTERNS = [
    r"be an early applicant",
    r"over\s+\d[\d,]*\s+applicants?",
    r"\d[\d,]*\+?\s+applicants?",
    r"\d[\d,]*\s+people clicked apply",
    r"actively (?:recruiting|hiring)",
    r"reposted\s+\d+\s+\w+\s+ago",
]
NEWS_RULES: list[tuple[str, re.Pattern[str]]] = [  # order = priority
    ("hiring_freeze", re.compile(r"hiring freeze|freez\w*\s+hiring|paus\w*\s+hiring|halt\w*\s+hiring", re.I)),
    ("layoffs", re.compile(r"lay\s?offs?|laying off|lays off|job cuts|workforce reduction|redundanc|cuts? \d+[\d,]*\s+(?:jobs|roles|employees)", re.I)),
    ("acquisition", re.compile(r"acquir|acquisition|\bmerger\b|\bmerges\b", re.I)),
    ("funding", re.compile(r"\braises?\b|\braised\b|funding|series [a-h]\b|seed round|valuation", re.I)),
]


class FinderNotFound(RuntimeError):
    """No usable posting could be located from the input."""


# --------------------------------------------------------------------------- helpers
def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2 and t not in _STOP}


def _overlap(want: set[str], text: str) -> float:
    if not want:
        return 0.0
    have = _tokens(text)
    return len(want & have) / len(want)


def _humanize(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _short_host(url: str) -> str:
    h = host_of(url)
    return h[4:] if h.startswith("www.") else h


async def _safe(coro: Awaitable[Any], label: str, uncertain: list[str], field_name: str | None = None) -> Any:
    try:
        return await coro
    except Exception as e:  # noqa: BLE001 - one flaky call must never crash the pipeline
        log(logger, logging.WARNING, "finder_step_failed", step=label, error=repr(e))
        if field_name and field_name not in uncertain:
            uncertain.append(field_name)
        return None


def _parse_listing_title(title: str) -> tuple[Optional[str], Optional[str]]:
    """(company, role) from aggregator page titles. Heuristic; returns Nones when unsure."""
    t = re.sub(r"\s*[|\u2013-]\s*(LinkedIn|Indeed\.com|Indeed|Glassdoor).*$", "", title.strip(), flags=re.I)
    m = re.match(r"^(.+?)\s+hiring\s+(.+?)\s+in\s+.+$", t, re.I)  # LinkedIn
    if m:
        return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.+?)\s+-\s+(.+?)\s+-\s+.+$", t)  # Indeed: Role - Company - Location
    if m:
        return m.group(2).strip(), m.group(1).strip()
    m = re.match(r"^(.+?)\s+at\s+(.+)$", t, re.I)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    return None, None


def _company_role_from_slug(url: str) -> tuple[Optional[str], Optional[str]]:
    m = re.search(r"/view/(.+)-at-(.+?)-(\d{6,})", urlparse(url).path)
    if m:
        return _humanize(m.group(2)), _humanize(m.group(1))
    return None, None


def _split_ats_title(title: str, provider: str) -> tuple[Optional[str], Optional[str]]:
    """(company, role) from an ATS page title."""
    t = title.strip()
    m = re.match(r"^(?:Job Application for\s+)?(.+?)\s+(?:@|at)\s+(.+)$", t, re.I)
    if m:
        return m.group(2).strip(), m.group(1).strip()
    if provider == "lever":
        m = re.match(r"^(.+?)\s+-\s+(.+)$", t)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None, None


def _score_candidate(hit: SearchHit, company: set[str], role: set[str]) -> float:
    provider = ats_provider_for(hit.url)
    _, _, job_id = parse_ats_url(hit.url)
    score = 0.0
    if provider != "other":
        score += 2.0
        score += 1.0 if job_id else -2.0  # prefer job-level URLs over org board pages
    elif is_aggregator(hit.url):
        score -= 3.0
    blob = f"{hit.url} {hit.title} {hit.description}"
    score += 2.0 * _overlap(company, blob) + 3.0 * _overlap(role, blob)
    return score


def _extract_signals(text: str) -> list[str]:
    seen: list[str] = []
    for pat in SIGNAL_PATTERNS:
        for m in re.finditer(pat, text, re.I):
            val = m.group(0).strip()
            if val.lower() not in (s.lower() for s in seen):
                seen.append(val)
    return seen


def _categorize_news(text: str) -> Optional[str]:
    for cat, pat in NEWS_RULES:
        if pat.search(text):
            return cat
    return None


# --------------------------------------------------------------------------- main
async def run_finder(query: str, *, you: YouClient, settings: Settings | None = None) -> FinderOutput:
    s = settings or get_settings()
    q = query.strip()
    receipts: list[CitedClaim] = []
    actions: list[str] = []
    uncertain: list[str] = []

    # 1) Interpret the noisy input -------------------------------------------------
    seed_url = q if re.match(r"https?://", q, re.I) else None
    company: Optional[str] = None
    role: Optional[str] = None
    free_query = q
    if seed_url:
        company, role = _company_role_from_slug(seed_url)
        if is_aggregator(seed_url) and not (company and role):
            docs = await _safe(you.contents([seed_url]), "resolve_aggregator", uncertain)
            if docs:
                company, role = _parse_listing_title(docs[0].title)
                actions.append("Resolved aggregator link via You.com Contents API")
        elif ats_provider_for(seed_url) != "other":
            provider, org, _ = parse_ats_url(seed_url)
            docs = await _safe(you.contents([seed_url]), "resolve_ats_title", uncertain)
            if docs and docs[0].title:
                company, role = _split_ats_title(docs[0].title, provider)
            company = company or (_humanize(org) if org else None)
        else:
            company = _humanize((host_of(seed_url).split(".")[-2:-1] or [""])[0]) or None
        free_query = " ".join(x for x in (company, role) if x) or urlparse(seed_url).path.replace("/", " ").replace("-", " ")
    else:
        m = re.match(r"^(.+?)\s+at\s+(.+)$", q, re.I)
        if m:
            role, company = m.group(1).strip(), m.group(2).strip()

    # 2) Canonical ATS search (You.com Search) -----------------------------------------
    base = " ".join(x for x in (company, role) if x) or free_query
    queries = [f"{base} site:{site}" for site in ATS_SITES] + [f"{base} careers apply"]
    results = await asyncio.gather(*[_safe(you.search(qq, count=8), f"canonical:{qq}", uncertain) for qq in queries])
    actions.append("Searched You.com for canonical ATS URL (Ashby, Greenhouse, Lever, Workday + careers)")
    candidates: list[SearchHit] = [h for r in results if r for h in r if h.kind == "web"]
    if seed_url and not is_aggregator(seed_url):
        candidates.append(SearchHit(url=seed_url, title=""))

    company_tok, role_tok = _tokens(company or free_query), _tokens(role or "")
    best: Optional[SearchHit] = None
    best_score = -99.0
    for hit in candidates:
        sc = _score_candidate(hit, company_tok, role_tok)
        if sc > best_score:
            best, best_score = hit, sc
    if best is None or best_score < 3.0:
        if seed_url and not is_aggregator(seed_url):
            best = SearchHit(url=seed_url)
            uncertain.append("canonical_ats_url")
        else:
            raise FinderNotFound(f"no canonical posting found for {q!r}")

    canonical = best.url
    provider, org, req_id = parse_ats_url(canonical)
    t_company, t_role = _split_ats_title(best.title, provider) if best.title else (None, None)
    company = company or t_company or (_humanize(org) if org else None) or "Unknown company"
    role = role or t_role
    if not role:
        role = "Unknown role"
        uncertain.append("role_title")
    if not req_id:
        uncertain.append("requisition_id")
    receipts.append(CitedClaim(claim=f"Canonical {provider.title()} posting located for '{role}' at {company}",
                               source_url=canonical, engine="You.com", signal="canonical"))
    log(logger, logging.INFO, "finder_canonical", url=canonical, provider=provider, company=company, role=role)

    # 3) Mirrors (You.com Search) -------------------------------------------------------
    mirrors: list[MirrorUrl] = []
    mhits = await _safe(you.search(f"{company} {role} site:linkedin.com/jobs", count=8), "mirrors", uncertain, "mirror_urls")
    pool = list(mhits or [])
    if seed_url and "linkedin.com" in host_of(seed_url) and not any(h.url == seed_url for h in pool):
        pool.append(SearchHit(url=seed_url))
    seen_urls: set[str] = set()
    for h in pool:
        if "linkedin.com/jobs" not in h.url or h.url in seen_urls:
            continue
        if h.title and _overlap(_tokens(company) | _tokens(role), f"{h.title} {h.description}") < 0.5:
            continue
        seen_urls.add(h.url)
        signals = _extract_signals(h.text)
        try:
            mirrors.append(MirrorUrl(url=h.url, source="linkedin", snippet_signals=signals,
                                     closed_in_snippet=bool(find_closed_phrases(h.text))))
        except ValidationError:
            continue
        if signals:
            receipts.append(CitedClaim(claim=f"LinkedIn mirror snippet signals: {', '.join(signals)}",
                                       source_url=h.url, engine="You.com", signal="mirror"))
        if len(mirrors) >= 5:
            break

    # 4) Breaking company news (You.com Search, last 24-72h) -------------------------------
    news: list[CompanyNewsItem] = []
    nhits = await _safe(
        you.search(f'"{company}" (layoffs OR "hiring freeze" OR funding OR acquisition OR acquired)', count=10, freshness="week"),
        "news", uncertain, "recent_company_news",
    )
    cutoff = datetime.now(timezone.utc) - timedelta(hours=s.news_window_hours)
    name_tok = _tokens(company)
    for h in nhits or []:
        if name_tok and not (name_tok & _tokens(h.text)):
            continue  # unrelated company
        if h.published_at is None and not s.news_accept_undated:
            continue
        if h.published_at is not None and h.published_at < cutoff:
            continue
        cat = _categorize_news(h.text)
        if not cat:
            continue
        try:
            news.append(CompanyNewsItem(headline=h.title or h.description[:120], url=h.url,
                                        category=cat, published_at=h.published_at))  # type: ignore[arg-type]
        except ValidationError:
            continue
        receipts.append(CitedClaim(claim=f"Recent company news ({cat}): {h.title or h.description[:120]}",
                                   source_url=h.url, engine="You.com", signal="news"))
        if len(news) >= 5:
            break

    # 5) Research API only when hiring status is ambiguous -----------------------------------
    cats = {n.category for n in news}
    negative = bool(cats & {"layoffs", "hiring_freeze"})
    ambiguous = (negative and "funding" in cats) or "acquisition" in cats  # mixed or M&A signals
    research: Optional[HiringResearch] = None
    if ambiguous:
        prompt = (
            f"Is {company} actively expanding its team right now? Consider layoffs, hiring freezes, funding and "
            "acquisitions in the last few days, and whether it is still opening roles. Begin your answer with exactly "
            "one of: VERDICT: EXPANDING, VERDICT: CONTRACTING, VERDICT: UNCLEAR. Then give 2-3 sentences of evidence."
        )
        rr = await _safe(you.research(prompt), "research", uncertain, "hiring_research")
        actions.append(f"Hiring status ambiguous ({', '.join(sorted(cats))}); called You.com Research API")
        if rr and rr.content:
            m = re.search(r"VERDICT:\s*(EXPANDING|CONTRACTING|UNCLEAR)", rr.content, re.I)
            verdict = m.group(1).upper() if m else "UNCLEAR"
            summary = re.sub(r"VERDICT:\s*\w+\s*", "", rr.content, count=1, flags=re.I).strip()[:600]
            srcs: list[Any] = []
            for u in rr.sources[:5]:
                try:
                    srcs.append(_url.validate_python(u))
                except ValidationError:
                    pass
            research = HiringResearch(verdict=verdict, summary=summary, sources=srcs)  # type: ignore[arg-type]
            receipts.append(CitedClaim(claim=f"You.com Research: hiring status {verdict} - {summary[:200]}",
                                       source_url=str(srcs[0]) if srcs else canonical, engine="You.com",
                                       signal="hiring_research"))

    return FinderOutput(
        canonical_ats_url=canonical,  # type: ignore[arg-type]
        ats_provider=provider,  # type: ignore[arg-type]
        requisition_id=req_id,
        company_name=company,
        role_title=role,
        mirror_urls=mirrors,
        recent_company_news=news,
        hiring_research=research,
        receipts=receipts,
        actions_taken=actions,
        uncertain_fields=sorted(set(uncertain)),
    )
