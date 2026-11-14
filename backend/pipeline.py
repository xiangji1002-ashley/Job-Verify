"""Finder -> Verifier -> Scorer orchestration. One flaky step never crashes the run.

CLI:  python -m backend.pipeline "https://www.linkedin.com/jobs/view/..." [--baseline <sha256>]
`emit` is an optional async callback receiving {"stage", "status", ...} dicts (feed for the SSE dashboard).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

from backend.agents.finder import FinderNotFound, run_finder
from backend.agents.scorer import score
from backend.agents.verifier import run_verifier
from backend.logging_config import configure_logging, log
from backend.models import PipelineResult
from backend.tools.opencrawl_client import OpenCrawlClient, OpenCrawlError
from backend.tools.you_client import YouClient

logger = logging.getLogger("jobverify.pipeline")
Emit = Callable[[dict[str, Any]], Awaitable[None]]


async def run_pipeline(query: str, *, baseline_hash: Optional[str] = None, emit: Emit | None = None) -> PipelineResult:
    result = PipelineResult(query=query)

    async def _emit(**event: Any) -> None:
        if emit:
            try:
                await emit(event)
            except Exception as e:  # noqa: BLE001
                log(logger, logging.WARNING, "emit_failed", error=repr(e))

    async with YouClient() as you:
        await _emit(stage="finder", status="started", engine="You.com")
        try:
            result.finder = await run_finder(query, you=you)
        except FinderNotFound as e:
            result.errors.append(f"finder: {e}")
        except Exception as e:  # noqa: BLE001
            log(logger, logging.ERROR, "finder_crashed", error=repr(e))
            result.errors.append(f"finder: {e!r}")
        if result.finder is None:
            await _emit(stage="finder", status="failed", errors=result.errors)
            return result
        await _emit(stage="finder", status="done", output=result.finder.model_dump(mode="json"))

        await _emit(stage="verifier", status="started", engine="OpenCrawl + You.com Contents")
        oc: OpenCrawlClient | None = OpenCrawlClient()
        try:
            await oc.__aenter__()  # type: ignore[union-attr]
        except OpenCrawlError as e:
            log(logger, logging.WARNING, "opencrawl_unavailable", error=str(e))
            result.errors.append(f"opencrawl: {e}")
            oc = None
        try:
            result.evidence = await run_verifier(result.finder, you=you, opencrawl=oc)
        finally:
            if oc is not None:
                await oc.__aexit__(None, None, None)
        await _emit(stage="verifier", status="done", output=result.evidence.model_dump(mode="json"))

    result.verdict = score(result.finder, result.evidence, baseline_hash=baseline_hash)
    await _emit(stage="scorer", status="done", output=result.verdict.model_dump(mode="json"))
    return result


def _main() -> None:
    configure_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--baseline", default=None, help="previous content hash from WatchedJobState")
    args = ap.parse_args()
    res = asyncio.run(run_pipeline(args.query, baseline_hash=args.baseline))
    print(res.model_dump_json(indent=2))


if __name__ == "__main__":
    _main()
