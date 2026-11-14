"""Async You.com client: Search, Contents and Research.

Response parsing is deliberately tolerant (legacy `hits`/`news` and newer `results.web`/`results.news`
shapes) because the hackathon-era API surface has moved. Endpoints are env-configurable.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from backend.agents.common import parse_dt
from backend.config import Settings, get_settings
from backend.logging_config import log

logger = logging.getLogger("jobverify.you")


class YouError(RuntimeError):
    pass


@dataclass
class SearchHit:
    url: str
    title: str = ""
    description: str = ""
    snippets: list[str] = field(default_factory=list)
    published_at: Optional[datetime] = None
    kind: str = "web"  # web | news

    @property
    def text(self) -> str:
        return " ".join([self.title, self.description, *self.snippets]).strip()


@dataclass
class ContentDoc:
    url: str
    title: str = ""
    markdown: str = ""
    html: str = ""

    @property
    def text(self) -> str:
        return self.markdown or self.html


@dataclass
class ResearchResult:
    content: str
    sources: list[str] = field(default_factory=list)


_REL = re.compile(r"(\d+)\s*(minute|min|hour|hr|day|week)s?\s*ago", re.I)


def _parse_age(value: Any) -> Optional[datetime]:
    dt = parse_dt(value)
    if dt:
        return dt
    if isinstance(value, str):
        m = _REL.search(value)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            delta = {"minute": timedelta(minutes=n), "min": timedelta(minutes=n),
                     "hour": timedelta(hours=n), "hr": timedelta(hours=n),
                     "day": timedelta(days=n), "week": timedelta(weeks=n)}[unit]
            return datetime.now(timezone.utc) - delta
    return None


def _hit(raw: dict[str, Any], kind: str) -> Optional[SearchHit]:
    url = raw.get("url") or raw.get("link")
    if not url:
        return None
    snippets = raw.get("snippets") or ([raw["snippet"]] if raw.get("snippet") else [])
    when = None
    for key in ("page_age", "published_date", "published_at", "date", "age"):
        if raw.get(key):
            when = _parse_age(raw[key])
            if when:
                break
    return SearchHit(
        url=url,
        title=raw.get("title") or "",
        description=raw.get("description") or "",
        snippets=[s for s in snippets if isinstance(s, str)],
        published_at=when,
        kind=kind,
    )


def parse_search_response(data: dict[str, Any]) -> list[SearchHit]:
    hits: list[SearchHit] = []
    results = data.get("results")
    web = data.get("hits") or (results.get("web") if isinstance(results, dict) else None) or []
    news_block = data.get("news")
    news = (news_block.get("results") if isinstance(news_block, dict) else news_block) or \
           (results.get("news") if isinstance(results, dict) else None) or []
    for raw in web:
        h = _hit(raw, "web")
        if h:
            hits.append(h)
    for raw in news:
        h = _hit(raw, "news")
        if h:
            hits.append(h)
    return hits


class YouClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.s = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "YouClient":
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.s.http_timeout, headers={"X-API-Key": self.s.you_api_key})
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()

    async def _request(self, method: str, url: str, **kw: Any) -> Any:
        if not self.s.you_api_key:
            raise YouError("YOU_API_KEY is not set")
        assert self._client is not None, "use `async with YouClient()`"
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.request(method, url, headers={"X-API-Key": self.s.you_api_key}, **kw)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=resp.request, response=resp)
                resp.raise_for_status()
                return resp.json()
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                last = e
                retryable = not isinstance(e, httpx.HTTPStatusError) or e.response.status_code in (429, 500, 502, 503, 504)
                log(logger, logging.WARNING, "you_request_failed", url=url, attempt=attempt + 1, error=repr(e))
                if not retryable:
                    break
                await asyncio.sleep(0.6 * (2 ** attempt))
            except ValueError as e:  # bad JSON
                raise YouError(f"invalid JSON from {url}") from e
        raise YouError(f"{method} {url} failed: {last!r}")

    async def search(self, query: str, *, count: int = 10, freshness: str | None = None) -> list[SearchHit]:
        params: dict[str, Any] = {"query": query, "count": count}
        if freshness:
            params["freshness"] = freshness  # day | week | month
        data = await self._request("GET", self.s.you_search_url, params=params)
        hits = parse_search_response(data if isinstance(data, dict) else {})
        log(logger, logging.INFO, "you_search", query=query, hits=len(hits))
        return hits

    async def contents(self, urls: list[str]) -> list[ContentDoc]:
        data = await self._request("POST", self.s.you_contents_url, json={"urls": urls, "formats": ["markdown", "html"]})
        rows = data if isinstance(data, list) else (data.get("results") or data.get("contents") or [])
        docs = [
            ContentDoc(url=r.get("url", ""), title=r.get("title") or "",
                       markdown=r.get("markdown") or r.get("text") or r.get("content") or "",
                       html=r.get("html") or "")
            for r in rows if isinstance(r, dict)
        ]
        log(logger, logging.INFO, "you_contents", urls=urls, docs=len(docs))
        return docs

    async def research(self, question: str, *, effort: str = "standard") -> ResearchResult:
        data = await self._request("POST", self.s.you_research_url, json={"input": question, "research_effort": effort})
        out = data.get("output", data) if isinstance(data, dict) else {}
        content = out.get("content") or out.get("answer") or ""
        sources = [s.get("url") for s in (out.get("sources") or []) if isinstance(s, dict) and s.get("url")]
        log(logger, logging.INFO, "you_research", chars=len(content), sources=len(sources))
        return ResearchResult(content=content, sources=sources)
