"""OpenCrawl hosted-MCP client (https://app.getopencrawl.com/mcp).

OpenCrawl's tool names/schemas are discovered at runtime via `list_tools`, so this adapter does not
hardcode them. Override with OPENCRAWL_TOOL if the heuristic picks the wrong tool. Run
`python -m backend.tools.opencrawl_client` to list the tools your key can see.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.config import Settings, get_settings
from backend.logging_config import configure_logging, log

logger = logging.getLogger("jobverify.opencrawl")


class OpenCrawlError(RuntimeError):
    pass


@dataclass
class CrawlResult:
    url: str
    html: str = ""
    text: str = ""
    status: Optional[int] = None
    final_url: Optional[str] = None
    session_id: Optional[str] = None
    tool: str = ""
    raw: Any = field(default=None, repr=False)


_HTML_KEYS = ("html", "dom", "rendered_html", "renderedHtml", "outerHTML")
_TEXT_KEYS = ("markdown", "text", "content", "body")
_STATUS_KEYS = ("status", "status_code", "statusCode", "http_status", "httpStatus")
_FINAL_KEYS = ("final_url", "finalUrl", "resolved_url", "page_url")
_SESSION_KEYS = ("session_id", "sessionId", "capture_id", "captureId", "trace_id", "traceId", "run_id")
_CAPTURE_HINTS = ("capture", "record", "trace", "persist", "save")
_TOOL_HINTS = ("crawl", "scrape", "fetch", "extract", "render", "page", "navigate", "browse")


def _deep_find(obj: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj[k] not in (None, ""):
                return obj[k]
        for v in obj.values():
            found = _deep_find(v, keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _deep_find(v, keys)
            if found not in (None, ""):
                return found
    return None


def pick_tool(tools: list[Any], override: str = "") -> Any:
    if override:
        for t in tools:
            if t.name == override:
                return t
        raise OpenCrawlError(f"OPENCRAWL_TOOL={override!r} not found; available: {[t.name for t in tools]}")

    def has_url(t: Any) -> bool:
        props = (getattr(t, "inputSchema", None) or {}).get("properties", {})
        return any(k in props for k in ("url", "urls", "uri", "link"))

    scored = []
    for t in tools:
        if not has_url(t):
            continue
        name = t.name.lower()
        score = sum(2 for h in _TOOL_HINTS if h in name) + sum(1 for h in _TOOL_HINTS if h in (t.description or "").lower())
        scored.append((score, t))
    if not scored:
        raise OpenCrawlError(f"no URL-taking tool found; available: {[t.name for t in tools]}")
    scored.sort(key=lambda x: -x[0])
    return scored[0][1]


def build_args(tool: Any, url: str) -> dict[str, Any]:
    props = (getattr(tool, "inputSchema", None) or {}).get("properties", {})
    args: dict[str, Any] = {}
    for key in ("url", "uri", "link"):
        if key in props:
            args[key] = url
            break
    else:
        if "urls" in props:
            args["urls"] = [url]
    for key, spec in props.items():  # enable agent session capture / tracing where exposed
        if any(h in key.lower() for h in _CAPTURE_HINTS) and (spec or {}).get("type") == "boolean":
            args[key] = True
    for key, spec in props.items():  # ask for rendered DOM when a format option exists
        if key in ("format", "formats", "output", "output_format"):
            enum = (spec or {}).get("enum") or []
            if "html" in enum:
                args[key] = "html"
    return args


def parse_result(result: Any, url: str, tool: str) -> CrawlResult:
    payloads: list[Any] = []
    structured = getattr(result, "structuredContent", None)
    if structured:
        payloads.append(structured)
    texts: list[str] = []
    for block in getattr(result, "content", None) or []:
        t = getattr(block, "text", None)
        if not t:
            continue
        texts.append(t)
        try:
            payloads.append(json.loads(t))
        except ValueError:
            pass
    merged: Any = payloads if payloads else {}
    html = _deep_find(merged, _HTML_KEYS)
    text = _deep_find(merged, _TEXT_KEYS)
    if not html and texts and "<html" in texts[0][:2000].lower():
        html = texts[0]
    if not text and texts and not html:
        text = "\n".join(texts)
    status = _deep_find(merged, _STATUS_KEYS)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    session = _deep_find(merged, _SESSION_KEYS)
    return CrawlResult(
        url=url,
        html=html if isinstance(html, str) else "",
        text=text if isinstance(text, str) else "",
        status=status,
        final_url=_deep_find(merged, _FINAL_KEYS),
        session_id=str(session) if session else None,
        tool=tool,
        raw=merged,
    )


class OpenCrawlClient:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self._tool: Any = None

    async def __aenter__(self) -> "OpenCrawlClient":
        if not self.s.opencrawl_api_key:
            raise OpenCrawlError("OPENCRAWL_API_KEY is not set")
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
        except ImportError as e:  # pragma: no cover
            raise OpenCrawlError("`mcp` package not installed") from e
        stack = AsyncExitStack()
        try:
            read, write, _ = await asyncio.wait_for(
                stack.enter_async_context(
                    streamablehttp_client(self.s.opencrawl_url, headers={"Authorization": f"Bearer {self.s.opencrawl_api_key}"})
                ),
                self.s.opencrawl_timeout,
            )
            self._session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(self._session.initialize(), self.s.opencrawl_timeout)
            tools = (await self._session.list_tools()).tools
            self._tool = pick_tool(tools, self.s.opencrawl_tool)
            log(logger, logging.INFO, "opencrawl_ready", tool=self._tool.name, tools=[t.name for t in tools])
        except BaseException as e:
            await stack.aclose()
            raise OpenCrawlError(f"could not connect to OpenCrawl MCP: {e!r}") from e
        self._stack = stack
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack:
            try:
                await self._stack.aclose()
            except Exception as e:  # never let teardown crash the pipeline
                log(logger, logging.WARNING, "opencrawl_close_failed", error=repr(e))

    async def capture_page(self, url: str) -> CrawlResult:
        """Full-page rendered DOM extraction with agent session capture enabled (where exposed)."""
        if not self._session or not self._tool:
            raise OpenCrawlError("client not connected")
        args = build_args(self._tool, url)
        try:
            result = await asyncio.wait_for(self._session.call_tool(self._tool.name, args), self.s.opencrawl_timeout)
        except Exception as e:
            raise OpenCrawlError(f"call_tool failed: {e!r}") from e
        if getattr(result, "isError", False):
            raise OpenCrawlError(f"tool returned error: {getattr(result, 'content', None)!r}")
        crawl = parse_result(result, url, self._tool.name)
        log(logger, logging.INFO, "opencrawl_capture", url=url, tool=crawl.tool, status=crawl.status,
            html_chars=len(crawl.html), text_chars=len(crawl.text), session_id=crawl.session_id)
        return crawl


async def _list_tools_cli() -> None:
    configure_logging()
    async with OpenCrawlClient() as oc:
        for t in (await oc._session.list_tools()).tools:
            print(t.name, "-", (t.description or "")[:100], "\n   schema:", json.dumps(t.inputSchema)[:300])


if __name__ == "__main__":
    asyncio.run(_list_tools_cli())
