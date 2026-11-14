"""Agent 2 - Verifier (powered by OpenCrawl MCP + You.com Contents).

Captures the live, rendered posting through OpenCrawl (agent session capture on) while cross-checking
with You.com Contents, then applies the verification rules: liveness / soft-404, Apply control,
JobPosting JSON-LD, Ashby public posting API, closing language + validThrough, and mirror conflict
detection. It never raises: failures become `uncertain_fields` and `actions_taken` entries.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Iterator, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from backend.agents.common import JS_RENDERED_PROVIDERS, find_closed_phrases, host_of, parse_ats_url, parse_dt
from backend.config import Settings, get_settings
from backend.logging_config import log
from backend.models import (
    ApplyButton, CitedClaim, Conflict, FinderOutput, JobPostingSchema, Timestamps, VerifierEvidence,
)
from backend.tools.opencrawl_client import CrawlResult, OpenCrawlClient
from backend.tools.you_client import ContentDoc, YouClient

logger = logging.getLogger("jobverify.verifier")
UA = "Mozilla/5.0 (compatible; JobVerifyBot/0.1; +https://github.com/jobverify)"
_APPLY_RE = re.compile(r"\bapply\b", re.I)
_GENERIC_PATHS = {"", "/", "/careers", "/jobs", "/careers/jobs", "/open-positions", "/positions"}


# --------------------------------------------------------------------------- pure parsing helpers
def match_final_url(canonical: str, final: Optional[str], req_id: Optional[str]) -> tuple[Optional[bool], bool]:
    """Return (final_url_matches | None if unknown, soft_404_redirect)."""
    if not final:
        return None, False
    c, f = urlparse(canonical), urlparse(final)
    cp, fp = c.path.rstrip("/").lower(), f.path.rstrip("/").lower()
    same_host = c.hostname and f.hostname and c.hostname.removeprefix("www.") == f.hostname.removeprefix("www.")
    if req_id and req_id.lower() in final.lower():
        return True, False
    if same_host and (fp == cp or fp.startswith(cp + "/")):
        return True, False
    c_depth, f_depth = len([p for p in cp.split("/") if p]), len([p for p in fp.split("/") if p])
    soft = fp in _GENERIC_PATHS or f_depth < c_depth
    return False, bool(soft)


def _disabled(el: Tag) -> bool:
    if el.has_attr("disabled") or str(el.get("aria-disabled", "")).lower() == "true":
        return True
    if "disabled" in " ".join(el.get("class", []) or []).lower():
        return True
    if el.name == "a" and not el.get("href"):
        return True
    if el.name == "form" and el.select_one("fieldset[disabled]"):
        return True
    return False


def detect_apply(soup: BeautifulSoup, session_id: Optional[str]) -> ApplyButton:
    cands: list[tuple[str, Tag]] = []
    for el in soup.select("#application-form"):
        cands.append(("#application-form", el))
    for el in soup.select("button"):
        if _APPLY_RE.search(el.get_text(" ", strip=True) + " " + str(el.get("aria-label", ""))):
            cands.append(("button", el))
    for el in soup.select("a[href*='apply' i]"):
        cands.append(("a[href*='apply']", el))
    seen = {id(el) for _, el in cands}
    for el in soup.find_all("a"):
        if id(el) not in seen and _APPLY_RE.search(el.get_text(" ", strip=True)):
            cands.append(("a (text=Apply)", el))
    if not cands:
        return ApplyButton(found=False, clickable=False, selector_or_receipt=f"opencrawl_session:{session_id}" if session_id else None)
    chosen = next(((d, e) for d, e in cands if not _disabled(e)), cands[0])
    desc, el = chosen
    label = el.get_text(" ", strip=True)[:40]
    return ApplyButton(found=True, clickable=not _disabled(el), selector_or_receipt=f"{desc} :: '{label}'" if label else desc)


def _iter_ld(data: Any) -> Iterator[dict[str, Any]]:
    if isinstance(data, list):
        for x in data:
            yield from _iter_ld(x)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _iter_ld(data["@graph"])
        yield data


def _loc(node: Any) -> Optional[str]:
    if isinstance(node, list):
        parts = [p for p in (_loc(n) for n in node) if p]
        return "; ".join(parts) or None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        addr = node.get("address", node)
        if isinstance(addr, str):
            return addr
        if isinstance(addr, dict):
            country = addr.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), country]
            return ", ".join(str(p) for p in parts if p) or node.get("name")
        return node.get("name")
    return None


def parse_job_posting(soup: BeautifulSoup) -> Optional[JobPostingSchema]:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except ValueError:
            continue
        for node in _iter_ld(data):
            types = node.get("@type")
            types = [types] if isinstance(types, str) else (types or [])
            if "JobPosting" not in types:
                continue
            org = node.get("hiringOrganization")
            return JobPostingSchema(
                title=node.get("title"),
                date_posted=parse_dt(node.get("datePosted")),
                valid_through=parse_dt(node.get("validThrough")),
                job_location=_loc(node.get("jobLocation")),
                hiring_organization=org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None),
            )
    return None


def _meta_dt(soup: BeautifulSoup, *names: str) -> Optional[datetime]:
    for n in names:
        tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n}) or soup.find("meta", attrs={"itemprop": n})
        if tag and tag.get("content"):
            dt = parse_dt(tag["content"])
            if dt:
                return dt
    return None


def visible_text_and_hash(soup: BeautifulSoup) -> tuple[str, str]:
    for t in soup(["script", "style", "noscript", "template"]):
        t.decompose()
    text = soup.get_text(" ", strip=True)
    main = soup.find("main") or soup.body or soup
    for t in main(["nav", "header", "footer"]):
        t.decompose()
    norm = re.sub(r"\s+", " ", main.get_text(" ", strip=True)).lower()[:20000]
    return text, hashlib.sha256(norm.encode()).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).lower()[:20000].encode()).hexdigest()


# --------------------------------------------------------------------------- orchestration
async def _safe(coro: Awaitable[Any], label: str) -> Any:
    try:
        return await coro
    except Exception as e:  # noqa: BLE001
        log(logger, logging.WARNING, "verifier_step_failed", step=label, error=repr(e))
        return None


async def _probe(client: httpx.AsyncClient, url: str) -> tuple[Optional[int], Optional[str]]:
    resp = await client.get(url, follow_redirects=True)
    return resp.status_code, str(resp.url)


async def _ashby_lookup(client: httpx.AsyncClient, org: str, job_id: str) -> tuple[str, Optional[dict[str, Any]]]:
    api = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    resp = await client.get(api, params={"includeCompensation": "false"})
    resp.raise_for_status()
    jobs = resp.json().get("jobs", [])
    job = next((j for j in jobs if j.get("id") == job_id or job_id in (j.get("jobUrl") or "") or job_id in (j.get("applyUrl") or "")), None)
    return api, job


async def run_verifier(
    finder: FinderOutput,
    *,
    you: YouClient,
    opencrawl: OpenCrawlClient | None,
    http: httpx.AsyncClient | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> VerifierEvidence:
    s = settings or get_settings()
    owns = http is None
    client = http or httpx.AsyncClient(timeout=s.http_timeout, headers={"User-Agent": UA})
    try:
        return await _verify(finder, you, opencrawl, client, now or datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001 - last-resort guard: the pipeline must keep going
        log(logger, logging.ERROR, "verifier_crashed", error=repr(e))
        return VerifierEvidence(http_status=0, final_url_matches=True,
                                apply_button=ApplyButton(found=False, clickable=False),
                                uncertain_fields=["verifier_error", "http_status", "apply_button"],
                                actions_taken=[f"Verifier aborted: {e!r}"])
    finally:
        if owns:
            await client.aclose()


async def _verify(finder: FinderOutput, you: YouClient, oc: OpenCrawlClient | None,
                  client: httpx.AsyncClient, now: datetime) -> VerifierEvidence:
    url = str(finder.canonical_ats_url)
    provider, org, job_id = parse_ats_url(url)
    req_id = finder.requisition_id or job_id
    receipts: list[CitedClaim] = []
    actions: list[str] = []
    uncertain: list[str] = []
    conflicts: list[Conflict] = []

    # 1) capture (OpenCrawl) + cross-check (You.com Contents) in parallel ---------------------------
    async def _crawl() -> Optional[CrawlResult]:
        return await oc.capture_page(url) if oc else None

    async def _contents() -> Optional[ContentDoc]:
        docs = await you.contents([url])
        return docs[0] if docs else None

    crawl, doc = await asyncio.gather(_safe(_crawl(), "opencrawl_capture"), _safe(_contents(), "you_contents"))
    crawl_ok = bool(crawl and (crawl.html or crawl.text))
    if oc is None:
        actions.append("OpenCrawl unavailable; verified with You.com Contents only")
    elif not crawl_ok:
        actions.append("OpenCrawl capture failed or empty; fell back to You.com Contents")
    if crawl_ok:
        actions.append(f"OpenCrawl live DOM capture (agent session {crawl.session_id or 'n/a'})")  # type: ignore[union-attr]
    if doc and (doc.html or doc.markdown):
        actions.append("You.com Contents cross-check of the posting")

    html = text = ""
    fetch_engine: Optional[str] = None
    dom_reliable = False
    if crawl_ok:
        html, text, fetch_engine = crawl.html, crawl.text, "OpenCrawl"  # type: ignore[union-attr]
        dom_reliable = bool(html)
    elif doc and (doc.html or doc.markdown):
        html, text, fetch_engine = doc.html, doc.markdown, "You.com"
        dom_reliable = bool(html) and provider not in JS_RENDERED_PROVIDERS
    else:
        uncertain.append("page_content")
    session_id = crawl.session_id if crawl else None

    # 2) liveness: status + final URL (OpenCrawl values first, direct probe only to fill gaps) ------------
    status = crawl.status if crawl else None
    final_url = crawl.final_url if crawl else None
    status_engine = "OpenCrawl" if status else "Direct HTTP"
    if status is None or final_url is None:
        probe = await _safe(_probe(client, url), "direct_probe")
        if probe:
            actions.append("Direct HTTP probe filled missing status/final URL")
            status = status or probe[0]
            final_url = final_url or probe[1]
    http_status = int(status or 0)
    if http_status == 0:
        uncertain.append("http_status")
    matches, soft = match_final_url(url, final_url, req_id)
    if matches is None:
        matches = True
        uncertain.append("final_url")
    page_head = (text or "")[:2000].lower()
    if re.search(r"\b(page not found|404 not found|error 404)\b", page_head):
        soft = True
    receipts.append(CitedClaim(
        claim=f"Page returned HTTP {http_status or 'unknown'}; final URL {'matches' if matches else 'does NOT match'} the canonical posting"
              + (" (soft-404 redirect suspected)" if soft else "") + (f" [session {session_id}]" if session_id else ""),
        source_url=url, engine=status_engine, signal="http_status"))  # type: ignore[arg-type]

    # 3) DOM-derived evidence ------------------------------------------------------------------------
    apply_btn = ApplyButton(found=False, clickable=False, selector_or_receipt=f"opencrawl_session:{session_id}" if session_id else None)
    posting: Optional[JobPostingSchema] = None
    content_hash: Optional[str] = None
    meta_pub = meta_upd = None
    dom_text = text
    if html:
        soup = BeautifulSoup(html, "html.parser")
        apply_btn = detect_apply(soup, session_id)
        posting = parse_job_posting(soup)
        meta_pub = _meta_dt(soup, "article:published_time", "datePublished")
        meta_upd = _meta_dt(soup, "article:modified_time", "og:updated_time", "dateModified")
        dom_text, content_hash = visible_text_and_hash(soup)
    elif text:
        content_hash = text_hash(text)
    if fetch_engine:
        if html:
            receipts.append(CitedClaim(
                claim=("Apply control found and enabled via " if apply_btn.clickable else
                       "Apply control found but DISABLED via " if apply_btn.found else
                       "No Apply control found in captured DOM")
                      + (apply_btn.selector_or_receipt or "") + ("" if dom_reliable else " (DOM not a rendered capture; low reliability)"),
                source_url=url, engine=fetch_engine, signal="apply_button"))  # type: ignore[arg-type]
        else:
            uncertain.append("apply_button")
    if not dom_reliable and "apply_button" not in uncertain:
        uncertain.append("apply_button")
    if posting:
        receipts.append(CitedClaim(
            claim=f"JobPosting schema: datePosted={posting.date_posted.date() if posting.date_posted else None}, "
                  f"validThrough={posting.valid_through.date() if posting.valid_through else None}, location={posting.job_location}",
            source_url=url, engine=fetch_engine or "OpenCrawl", signal="json_ld"))  # type: ignore[arg-type]

    # 4) Ashby public posting API (no keys) ------------------------------------------------------------
    ts = Timestamps(published_at=None, updated_at=meta_upd, valid_through=posting.valid_through if posting else None)
    ashby_listed: Optional[bool] = None
    closed: list[str] = []
    if provider == "ashby" and org and job_id:
        res = await _safe(_ashby_lookup(client, org, job_id), "ashby_api")
        if res:
            api_url, job = res
            actions.append("Queried Ashby public posting API (no credentials)")
            ashby_listed = bool(job.get("isListed", True)) if job else False
            if job:
                ts.published_at = parse_dt(job.get("publishedAt"))
                ts.source_type = "ats_api"
            if not ashby_listed:
                closed.append("ashby: job absent from public posting board (isListed=false)")
            receipts.append(CitedClaim(
                claim=(f"Ashby API: isListed={ashby_listed}, publishedAt={ts.published_at.isoformat() if ts.published_at else None}"
                       if job else "Ashby API: job id not present on the public posting board"),
                source_url=api_url, engine="Direct HTTP", signal="ashby_api"))
        else:
            uncertain.append("ashby_api")
    if ts.published_at is None:
        if posting and posting.date_posted:
            ts.published_at, ts.source_type = posting.date_posted, "json_ld"
        elif meta_pub:
            ts.published_at, ts.source_type = meta_pub, "meta_tag"
    if ts.valid_through and ts.source_type is None:
        ts.source_type = "json_ld"

    # 5) closing language + validThrough ----------------------------------------------------------------
    dom_phrases = find_closed_phrases(dom_text) if fetch_engine else []
    doc_text = (doc.text if doc else "") or ""
    doc_phrases = find_closed_phrases(doc_text) if doc_text else []
    for ph in dom_phrases:
        closed.append(ph)
    if dom_phrases and fetch_engine:
        receipts.append(CitedClaim(claim="Closing language on the live page: " + "; ".join(f"'{p}'" for p in dom_phrases),
                                   source_url=url, engine=fetch_engine, signal="closed_phrase"))  # type: ignore[arg-type]
    extra = [p for p in doc_phrases if p not in dom_phrases]
    if extra and crawl_ok:
        # The rendered OpenCrawl DOM is authoritative; a static-contents-only closing phrase is flagged, not scored.
        receipts.append(CitedClaim(claim="You.com Contents saw closing language the rendered DOM did not: " + "; ".join(f"'{p}'" for p in extra),
                                   source_url=url, engine="You.com", signal="engine_disagreement"))
    if crawl_ok and doc_text and bool(dom_phrases) != bool(doc_phrases):
        conflicts.append(Conflict(code="ENGINE_DISAGREEMENT", field="closing_language",
                                  official_value=f"OpenCrawl: {'closed' if dom_phrases else 'no closing language'}",
                                  mirror_value=f"You.com Contents: {'closed' if doc_phrases else 'no closing language'}",
                                  note="Engines disagree on closing language; JS-rendered DOM is trusted over static contents"))
        uncertain.append("closed_signals")
    if ts.valid_through and ts.valid_through < now:
        closed.append(f"validThrough passed ({ts.valid_through.date().isoformat()})")
        receipts.append(CitedClaim(claim=f"validThrough {ts.valid_through.date()} is in the past",
                                   source_url=url, engine=fetch_engine or "OpenCrawl", signal="valid_through"))  # type: ignore[arg-type]

    # 6) mirror conflict detection ------------------------------------------------------------------------
    official_closed = bool(closed) or http_status in (404, 410) or soft or not matches
    official_known = bool(fetch_engine) or http_status != 0
    if official_known:
        for m in finder.mirror_urls:
            if official_closed and not m.closed_in_snippet:
                conflicts.append(Conflict(
                    code="CONFLICT_CLOSED_ON_SOURCE", field="status", official_value="closed",
                    mirror_value="active" + (f" ({', '.join(m.snippet_signals)})" if m.snippet_signals else ""),
                    mirror_url=m.url, note="Mirror still appears live while the official ATS posting is closed"))
                receipts.append(CitedClaim(claim="CONFLICT_CLOSED_ON_SOURCE: mirror listing still appears active but the official ATS posting is closed",
                                           source_url=str(m.url), engine="You.com", signal="conflict"))
            elif not official_closed and m.closed_in_snippet:
                conflicts.append(Conflict(code="CONFLICT_CLOSED_ON_MIRROR", field="status", official_value="open",
                                          mirror_value="closed", mirror_url=m.url,
                                          note="Mirror snippet carries closing language but the official ATS looks open"))
                uncertain.append("mirror_status")

    return VerifierEvidence(
        http_status=http_status, final_url_matches=matches, apply_button=apply_btn, timestamps=ts,
        closed_signals_detected=closed, conflicts=conflicts, opencrawl_session_id=session_id,
        soft_404=soft, content_hash=content_hash, job_posting=posting, ashby_is_listed=ashby_listed,
        fetch_engine=fetch_engine, dom_reliable=dom_reliable,  # type: ignore[arg-type]
        receipts=receipts, actions_taken=actions, uncertain_fields=sorted(set(uncertain)),
    )
