"""Live Wire: the autonomous state-change watcher.

A background asyncio task re-runs the pipeline for every watched job and detects status transitions:
  Transition A  NEW|CLOSED -> OPEN with apply_score >= 85  => Priority Apply Queue + webhook + outreach draft
  Transition B  OPEN -> CLOSED|GHOST_RISK                  => archive in tracker + alert with the reason
Simulated deltas (demo) go through exactly the same `apply_verdict` path as live polls.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlparse

from backend.actions import draft_outreach, send_webhooks
from backend.agents.scorer import score, status_for
from backend.config import Settings, get_settings
from backend.db import Database
from backend.demo import SCENARIOS, SEEDS, apply_scenario, build_seed
from backend.logging_config import log
from backend.models import (
    FinderOutput, JobStatus, LiveWireEvent, PipelineResult, ScorerVerdict, VerifierEvidence, WatchedJobState,
)
from backend.pipeline import run_pipeline

logger = logging.getLogger("jobverify.watcher")
PRIORITY_THRESHOLD = 85
Runner = Callable[..., Awaitable[PipelineResult]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NoEligibleJob(LookupError):
    pass


class Watcher:
    def __init__(self, db: Database, settings: Settings | None = None, runner: Runner = run_pipeline):
        self.db = db
        self.s = settings or get_settings()
        self.runner = runner
        self._task: Optional[asyncio.Task[None]] = None
        self._stop = asyncio.Event()
        self._poll_lock = asyncio.Lock()
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="jobverify-live-wire")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _loop(self) -> None:
        log(logger, logging.INFO, "live_wire_started", interval=self.s.poll_interval)
        while not self._stop.is_set():
            try:
                await self.poll_all()
            except Exception as e:  # noqa: BLE001 - the loop must survive anything
                log(logger, logging.ERROR, "poll_cycle_failed", error=repr(e))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.s.poll_interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------ pub/sub for SSE
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)

    async def event(self, job: Optional[WatchedJobState], kind: str, message: str, *,
                    simulated: bool = False, data: Optional[dict[str, Any]] = None) -> LiveWireEvent:
        ev = await self.db.add_event(LiveWireEvent(job_id=job.id if job else None, kind=kind,  # type: ignore[arg-type]
                                                   message=message, simulated=simulated, data=data or {}))
        payload = ev.model_dump(mode="json")
        for q in list(self._subs):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass
        return ev

    # ------------------------------------------------------------------ polling
    async def poll_all(self) -> dict[str, Any]:
        if self._poll_lock.locked():
            return {"status": "already_running", "results": []}
        async with self._poll_lock:
            jobs = await self.db.list_jobs()
            sem = asyncio.Semaphore(2)

            async def one(j: WatchedJobState) -> dict[str, Any]:
                async with sem:
                    return await self.check_job(j)

            results = await asyncio.gather(*[one(j) for j in jobs])
        return {"status": "ok", "results": list(results)}

    async def check_job(self, job: WatchedJobState) -> dict[str, Any]:
        if job.is_demo:
            return {"job_id": job.id, "outcome": "skipped_demo", "detail": "seeded demo entry; use /api/simulate-event"}
        try:
            res = await self.runner(job.url, baseline_hash=job.baseline_hash)
        except Exception as e:  # noqa: BLE001
            await self.event(job, "error", f"Live check crashed: {e!r}")
            return {"job_id": job.id, "outcome": "error", "detail": repr(e)}
        return await self.ingest(res, job=job)

    async def ingest(self, result: PipelineResult, job: Optional[WatchedJobState] = None) -> dict[str, Any]:
        """Apply a live PipelineResult to the tracker. Infrastructure failures never flip a status."""
        if result.finder is None or result.evidence is None:
            if job:
                await self.event(job, "error", "Live check failed: " + "; ".join(result.errors or ["no result"]))
            return {"job_id": job.id if job else None, "outcome": "error", "detail": result.errors}
        finder, evidence = result.finder, result.evidence
        if job is None:
            canonical = str(finder.canonical_ats_url)
            job = await self.db.get_by_url(canonical) or WatchedJobState(
                url=canonical, company=finder.company_name, role=finder.role_title)
        if evidence.http_status == 0 and evidence.fetch_engine is None:
            job.last_checked_at = _now()
            job = await self.db.save_job(job)
            await self.event(job, "error", "Page unreachable via OpenCrawl, You.com Contents and direct HTTP; status left unchanged")
            return {"job_id": job.id, "outcome": "unverifiable", "status": job.status.value}
        verdict = score(finder, evidence, baseline_hash=job.baseline_hash)  # re-score against the stored baseline
        return await self.apply_verdict(job, finder, evidence, verdict)

    # ------------------------------------------------------------------ core transition logic
    async def apply_verdict(self, job: WatchedJobState, finder: FinderOutput, evidence: VerifierEvidence,
                            verdict: ScorerVerdict, *, simulated: bool = False,
                            now: Optional[datetime] = None) -> dict[str, Any]:
        now = now or _now()
        tag = "[SIMULATED] " if simulated else ""
        prev, prev_checked = job.status, job.last_checked_at
        new = status_for(verdict)

        job.company, job.role = finder.company_name, finder.role_title
        job.content_hash = evidence.content_hash or job.content_hash
        job.baseline_hash = job.baseline_hash or evidence.content_hash
        job.last_verdict, job.last_finder, job.last_evidence = verdict, finder, evidence
        job.status, job.last_checked_at, job.updated_at = new, now, now
        job = await self.db.save_job(job)

        await self.event(job, "checked",
                         f"{tag}{job.role} @ {job.company}: open_confidence {verdict.open_confidence}, "
                         f"apply_score {verdict.apply_score} -> {verdict.recommendation}",
                         simulated=simulated, data={"open_confidence": verdict.open_confidence, "apply_score": verdict.apply_score})
        if prev != new:
            await self.event(job, "status_change", f"{tag}{prev.value} -> {new.value}", simulated=simulated,
                             data={"from": prev.value, "to": new.value})

        transition: Optional[str] = None
        if prev in (JobStatus.NEW, JobStatus.CLOSED) and new == JobStatus.OPEN and verdict.apply_score >= PRIORITY_THRESHOLD:
            transition = "A"
            await self._transition_a(job, finder, evidence, verdict, simulated=simulated, now=now)
        elif prev == JobStatus.OPEN and new in (JobStatus.CLOSED, JobStatus.GHOST_RISK):
            transition = "B"
            await self._transition_b(job, finder, evidence, verdict, new, prev_checked, simulated=simulated, now=now)
        return {"job_id": job.id, "outcome": "checked", "previous_status": prev.value, "status": new.value,
                "transition": transition, "open_confidence": verdict.open_confidence, "apply_score": verdict.apply_score}

    async def _transition_a(self, job: WatchedJobState, finder: FinderOutput, evidence: VerifierEvidence,
                            verdict: ScorerVerdict, *, simulated: bool, now: datetime) -> None:
        tag = "[SIMULATED] " if simulated else ""
        pub = evidence.timestamps.published_at
        job.queue = "PRIORITY"
        job = await self.db.save_job(job)
        await self.event(job, "promoted",
                         f"{tag}Promoted to Priority Apply Queue: {job.role} @ {job.company} (apply_score {verdict.apply_score}"
                         + (f", published {pub.isoformat()}" if pub else "") + ")",
                         simulated=simulated, data={"queue": "PRIORITY", "published_at": pub.isoformat() if pub else None})
        draft = await draft_outreach(finder, evidence, now=now, settings=self.s)
        await self.event(job, "outreach_draft", f"{tag}Cold outreach draft generated for the hiring manager",
                         simulated=simulated, data={"draft": draft})
        text = (f"{tag}Priority Apply: {job.role} @ {job.company}\nApply score {verdict.apply_score}/100 "
                f"({verdict.recommendation})\n{job.url}\n\nDraft outreach:\n{draft}")
        sent, errors = await send_webhooks(text, self.s)
        if sent:
            await self.event(job, "webhook", f"{tag}Webhook sent: {', '.join(sent)}", simulated=simulated, data={"channels": sent})
        elif not errors:
            await self.event(job, "webhook", f"{tag}Webhook skipped: DISCORD_WEBHOOK_URL / SLACK_WEBHOOK_URL not set", simulated=simulated)
        for err in errors:
            await self.event(job, "error", f"{tag}Webhook failed - {err}", simulated=simulated)

    async def _transition_b(self, job: WatchedJobState, finder: FinderOutput, evidence: VerifierEvidence,
                            verdict: ScorerVerdict, new: JobStatus, prev_checked: Optional[datetime],
                            *, simulated: bool, now: datetime) -> None:
        tag = "[SIMULATED] " if simulated else ""
        reason = explain_exit(finder, evidence, verdict, new, prev_checked, now)
        job.queue = "ARCHIVED"
        job = await self.db.save_job(job)
        await self.event(job, "archived", f"{tag}Archived in tracker: {job.role} @ {job.company} ({new.value})",
                         simulated=simulated, data={"queue": "ARCHIVED", "status": new.value})
        await self.event(job, "alert", f"{tag}ALERT: {reason}", simulated=simulated, data={"reason": reason, "status": new.value})
        log(logger, logging.WARNING, "live_wire_alert", job_id=job.id, status=new.value, reason=reason, simulated=simulated)

    # ------------------------------------------------------------------ demo
    @staticmethod
    def pick_target(jobs: list[WatchedJobState], scenario: str, job_id: Optional[int] = None) -> Optional[WatchedJobState]:
        usable = [j for j in jobs if j.last_finder and j.last_evidence]
        if job_id is not None:
            return next((j for j in usable if j.id == job_id), None)
        if scenario == "early_window_drop":
            pred: Callable[[WatchedJobState], bool] = lambda j: j.status in (JobStatus.NEW, JobStatus.CLOSED)
        elif scenario == "ghost_conflict_closed":
            pred = lambda j: j.status == JobStatus.OPEN and bool(j.last_finder and j.last_finder.mirror_urls)
        else:
            pred = lambda j: j.status == JobStatus.OPEN
        return next((j for j in usable if pred(j)), None)

    async def simulate(self, scenario: str, job_id: Optional[int] = None) -> dict[str, Any]:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {scenario!r}")
        target = self.pick_target(await self.db.list_jobs(), scenario, job_id)
        if target is None:
            raise NoEligibleJob(f"no job in a state that fits '{scenario}'; POST /api/reset-demo to restore the seeds")
        now = _now()
        finder, evidence = apply_scenario(target, scenario, now)
        verdict = score(finder, evidence, baseline_hash=target.baseline_hash, now=now)
        await self.event(target, "simulated", f"[SIMULATED] Injected scenario '{scenario}' into {target.role} @ {target.company}",
                         simulated=True, data={"scenario": scenario})
        summary = await self.apply_verdict(target, finder, evidence, verdict, simulated=True, now=now)
        return {"scenario": scenario, **summary}

    async def seed(self) -> None:
        now = _now()
        for spec in SEEDS:
            job = await self.db.save_job(build_seed(spec, now))
            v = job.last_verdict
            await self.event(job, "seed", f"[DEMO SEED] {job.role} @ {job.company}: {job.status.value} "
                                          f"(open_confidence {v.open_confidence}, apply_score {v.apply_score})" if v else "[DEMO SEED]")
            if job.queue == "PRIORITY" and job.last_finder and job.last_evidence:
                draft = await draft_outreach(job.last_finder, job.last_evidence, now=now, settings=self.s, use_llm=False)
                await self.event(job, "promoted", f"[DEMO SEED] In Priority Apply Queue: {job.role} @ {job.company}")
                await self.event(job, "outreach_draft", "[DEMO SEED] Cold outreach draft", data={"draft": draft})


def explain_exit(finder: FinderOutput, evidence: VerifierEvidence, verdict: ScorerVerdict, new: JobStatus,
                 prev_checked: Optional[datetime], now: datetime) -> str:
    provider = finder.ats_provider.title()
    who = f"{finder.company_name} - {finder.role_title}"
    seen = f"; last seen open {max(int((now - prev_checked).total_seconds() // 60), 0)} min ago" if prev_checked else ""
    if new == JobStatus.CLOSED:
        if evidence.http_status in (404, 410):
            head = f"Official {provider} page now returns HTTP {evidence.http_status}"
        elif evidence.ashby_is_listed is False:
            head = "Official Ashby posting is no longer listed"
        elif evidence.soft_404 or not evidence.final_url_matches:
            head = f"Official {provider} page now redirects to a generic careers page"
        elif evidence.closed_signals_detected:
            head = f"Official {provider} page shows closing language ('{evidence.closed_signals_detected[0]}')"
        else:
            head = f"Official {provider} posting is closed"
        mirror = next((c for c in evidence.conflicts if c.code == "CONFLICT_CLOSED_ON_SOURCE"), None)
        tail = ""
        if mirror:
            host = (urlparse(str(mirror.mirror_url)).hostname or "mirror").removeprefix("www.").split(".")[0]
            name = {"linkedin": "LinkedIn", "indeed": "Indeed"}.get(host, host.title())
            tail = f" while the {name} mirror still lists it"
        return f"{who}: {head}{seen}{tail}."
    reasons = [c.claim for c in verdict.reasoning_with_citations
               if c.claim.startswith(("apply_score -", "open_confidence -")) and "-> 0" not in c.claim]
    return f"{who}: flagged GHOST_RISK (apply_score {verdict.apply_score}/100){seen}. " + " | ".join(reasons[:3])
