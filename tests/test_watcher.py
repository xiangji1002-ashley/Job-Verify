"""Offline Live Wire tests: seeds + simulated scenarios through the real Scorer and Watcher."""
import asyncio

from backend.config import Settings
from backend.db import Database
from backend.models import JobStatus
from backend.watcher import NoEligibleJob, Watcher


def run(coro):
    return asyncio.run(coro)


async def _setup():
    db = Database(":memory:")
    await db.connect()
    w = Watcher(db, Settings(anthropic_api_key="", discord_webhook_url="", slack_webhook_url=""))
    await w.seed()
    return db, w


def test_seed_creates_three_demo_jobs():
    async def go():
        db, _ = await _setup()
        jobs = await db.list_jobs()
        assert [j.status for j in jobs] == [JobStatus.OPEN, JobStatus.OPEN, JobStatus.CLOSED]
        assert all(j.is_demo for j in jobs) and jobs[0].queue == "PRIORITY"
    run(go())


def test_early_window_drop_triggers_transition_a():
    async def go():
        db, w = await _setup()
        out = await w.simulate("early_window_drop")
        assert out["transition"] == "A" and out["previous_status"] == "CLOSED" and out["status"] == "OPEN"
        job = await db.get_job(out["job_id"])
        assert job.queue == "PRIORITY"
        kinds = {e.kind for e in await db.list_events(job_id=job.id)}
        assert {"promoted", "outreach_draft", "webhook"} <= kinds
        draft = next(e for e in await db.list_events(job_id=job.id) if e.kind == "outreach_draft").data["draft"]
        assert "Hi [Hiring Manager]" in draft and "minutes ago" in draft
    run(go())


def test_ghost_conflict_triggers_transition_b_with_mirror_reason():
    async def go():
        db, w = await _setup()
        out = await w.simulate("ghost_conflict_closed")
        assert out["transition"] == "B" and out["status"] == "CLOSED"
        job = await db.get_job(out["job_id"])
        assert job.queue == "ARCHIVED"
        alert = next(e for e in await db.list_events(job_id=job.id) if e.kind == "alert")
        assert "HTTP 404" in alert.message and "mirror still lists it" in alert.message and alert.simulated
    run(go())


def test_hiring_freeze_flags_ghost_risk():
    async def go():
        db, w = await _setup()
        out = await w.simulate("breaking_hiring_freeze")
        assert out["transition"] == "B" and out["status"] == "GHOST_RISK" and out["apply_score"] == 55
    run(go())


def test_no_eligible_job_raises():
    async def go():
        db, w = await _setup()
        await w.simulate("early_window_drop")
        try:
            await w.simulate("early_window_drop")
        except NoEligibleJob:
            return
        raise AssertionError("expected NoEligibleJob")
    run(go())


def test_demo_jobs_are_skipped_by_live_polling():
    async def go():
        db, w = await _setup()
        res = await w.poll_all()
        assert {r["outcome"] for r in res["results"]} == {"skipped_demo"}
    run(go())
