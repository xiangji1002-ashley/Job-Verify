"""FastAPI app for JobVerify / Live Wire.  Run from the repo root:  uvicorn backend.main:app --reload"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.config import get_settings
from backend.db import Database
from backend.logging_config import configure_logging
from backend.models import JobView, LiveWireEvent, WatchedJobState
from backend.pipeline import run_pipeline
from backend.watcher import NoEligibleJob, Watcher

logger = logging.getLogger("jobverify.api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db = Database(settings.database_path)
    await db.connect()
    watcher = Watcher(db, settings)
    app.state.db, app.state.watcher = db, watcher
    if await db.count_jobs() == 0:
        await watcher.seed()  # 3 demo entries so the dashboard has content on first load
    if settings.watcher_enabled:
        watcher.start()
    try:
        yield
    finally:
        await watcher.stop()
        await db.close()


app = FastAPI(title="JobVerify - Live Wire", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


class VerifyRequest(BaseModel):
    url: str = Field(min_length=3, description="Any job URL (LinkedIn/Indeed/ATS/company page) or 'role at company'")


class SimulateRequest(BaseModel):
    scenario: Literal["early_window_drop", "ghost_conflict_closed", "breaking_hiring_freeze"]
    job_id: Optional[int] = Field(default=None, description="Optional target; defaults to the best-fitting watched job")


def _db() -> Database:
    return app.state.db


def _watcher() -> Watcher:
    return app.state.watcher


async def _view(job: WatchedJobState, events: int = 8) -> JobView:
    recent = await _db().list_events(job_id=job.id, limit=events)
    return JobView(**job.model_dump(), recent_events=recent)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "jobs": await _db().count_jobs(), "watcher": settings.watcher_enabled}


@app.post("/api/verify")
async def verify(req: VerifyRequest) -> dict[str, Any]:
    """Run Finder -> Verifier -> Scorer live, then add/update the job in the watch list."""
    result = await run_pipeline(req.url.strip())
    if result.finder is None:
        raise HTTPException(status_code=422, detail={"message": "could not locate a posting", "errors": result.errors})
    outcome = await _watcher().ingest(result)
    job = await _db().get_job(outcome["job_id"]) if outcome.get("job_id") else None
    return {
        "outcome": outcome,
        "job": (await _view(job)).model_dump(mode="json") if job else None,
        "result": result.model_dump(mode="json"),
    }


@app.get("/api/jobs")
async def jobs() -> dict[str, Any]:
    items = await _db().list_jobs()
    views = [await _view(j) for j in items]
    events = await _db().list_events(limit=50)
    return {
        "jobs": [v.model_dump(mode="json") for v in views],
        "priority_queue": [j.id for j in items if j.queue == "PRIORITY"],
        "archived": [j.id for j in items if j.queue == "ARCHIVED"],
        "events": [e.model_dump(mode="json") for e in events],
    }


@app.post("/api/poll-now")
async def poll_now() -> dict[str, Any]:
    """Immediate live re-check of every watched job (seeded demo entries are skipped; use simulate-event)."""
    return await _watcher().poll_all()


@app.post("/api/simulate-event")
async def simulate_event(req: SimulateRequest) -> dict[str, Any]:
    """Inject a realistic delta through the real Scorer + Watcher. Events are tagged [SIMULATED]."""
    try:
        summary = await _watcher().simulate(req.scenario, req.job_id)
    except NoEligibleJob as e:
        raise HTTPException(status_code=409, detail=str(e))
    job = await _db().get_job(summary["job_id"])
    return {"summary": summary, "job": (await _view(job, events=12)).model_dump(mode="json") if job else None}


@app.post("/api/reset-demo")
async def reset_demo() -> dict[str, Any]:
    """Wipe the tracker and restore the 3 seeded demo jobs (handy between pitch run-throughs)."""
    await _db().reset()
    await _watcher().seed()
    return {"ok": True, "jobs": await _db().count_jobs()}


@app.get("/api/stream")
async def stream(request: Request) -> StreamingResponse:
    """Server-sent events: every Live Wire event as it happens (for the real-time dashboard)."""
    q = _watcher().subscribe()

    async def gen():
        try:
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _watcher().unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
