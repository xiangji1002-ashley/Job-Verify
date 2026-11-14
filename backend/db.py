"""SQLite persistence (aiosqlite) for watched jobs and the Live Wire event log."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Optional

import aiosqlite

from backend.models import (
    FinderOutput, JobStatus, LiveWireEvent, ScorerVerdict, VerifierEvidence, WatchedJobState,
)

DDL = """
CREATE TABLE IF NOT EXISTS watched_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL UNIQUE,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','OPEN','CLOSED','GHOST_RISK')),
    content_hash TEXT,
    baseline_hash TEXT,
    last_verdict_json TEXT,
    finder_json TEXT,
    evidence_json TEXT,
    queue TEXT CHECK (queue IS NULL OR queue IN ('PRIORITY','ARCHIVED')),
    is_demo INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    simulated INTEGER NOT NULL DEFAULT 0,
    data_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, id DESC);
"""


def _dt(v: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(v) if v else None


class Database:
    def __init__(self, path: str):
        self.path = path
        self._db: aiosqlite.Connection | None = None
        self._write = asyncio.Lock()

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(DDL)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Database.connect() not called"
        return self._db

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _to_state(r: aiosqlite.Row) -> WatchedJobState:
        return WatchedJobState(
            id=r["id"], url=r["url"], company=r["company"], role=r["role"], status=JobStatus(r["status"]),
            content_hash=r["content_hash"], baseline_hash=r["baseline_hash"],
            last_verdict=ScorerVerdict.model_validate_json(r["last_verdict_json"]) if r["last_verdict_json"] else None,
            last_finder=FinderOutput.model_validate_json(r["finder_json"]) if r["finder_json"] else None,
            last_evidence=VerifierEvidence.model_validate_json(r["evidence_json"]) if r["evidence_json"] else None,
            queue=r["queue"], is_demo=bool(r["is_demo"]),
            last_checked_at=_dt(r["last_checked_at"]), updated_at=datetime.fromisoformat(r["updated_at"]),
        )

    async def count_jobs(self) -> int:
        async with self.db.execute("SELECT COUNT(*) FROM watched_jobs") as c:
            return (await c.fetchone())[0]

    async def list_jobs(self) -> list[WatchedJobState]:
        async with self.db.execute("SELECT * FROM watched_jobs ORDER BY id") as c:
            return [self._to_state(r) for r in await c.fetchall()]

    async def get_job(self, job_id: int) -> Optional[WatchedJobState]:
        async with self.db.execute("SELECT * FROM watched_jobs WHERE id = ?", (job_id,)) as c:
            r = await c.fetchone()
        return self._to_state(r) if r else None

    async def get_by_url(self, url: str) -> Optional[WatchedJobState]:
        async with self.db.execute("SELECT * FROM watched_jobs WHERE url = ?", (url,)) as c:
            r = await c.fetchone()
        return self._to_state(r) if r else None

    async def save_job(self, job: WatchedJobState) -> WatchedJobState:
        vals = (
            job.url, job.company, job.role, job.status.value, job.content_hash, job.baseline_hash,
            job.last_verdict.model_dump_json() if job.last_verdict else None,
            job.last_finder.model_dump_json() if job.last_finder else None,
            job.last_evidence.model_dump_json() if job.last_evidence else None,
            job.queue, int(job.is_demo),
            job.last_checked_at.isoformat() if job.last_checked_at else None,
            job.updated_at.isoformat(),
        )
        async with self._write:
            if job.id is None:
                cur = await self.db.execute(
                    "INSERT INTO watched_jobs (url, company, role, status, content_hash, baseline_hash, last_verdict_json,"
                    " finder_json, evidence_json, queue, is_demo, last_checked_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    vals)
                job.id = cur.lastrowid
            else:
                await self.db.execute(
                    "UPDATE watched_jobs SET url=?, company=?, role=?, status=?, content_hash=?, baseline_hash=?,"
                    " last_verdict_json=?, finder_json=?, evidence_json=?, queue=?, is_demo=?, last_checked_at=?, updated_at=?"
                    " WHERE id=?", (*vals, job.id))
            await self.db.commit()
        return job

    async def reset(self) -> None:
        async with self._write:
            await self.db.execute("DELETE FROM job_events")
            await self.db.execute("DELETE FROM watched_jobs")
            await self.db.commit()

    # ------------------------------------------------------------------ events
    async def add_event(self, ev: LiveWireEvent) -> LiveWireEvent:
        async with self._write:
            cur = await self.db.execute(
                "INSERT INTO job_events (job_id, ts, kind, message, simulated, data_json) VALUES (?,?,?,?,?,?)",
                (ev.job_id, ev.at.isoformat(), ev.kind, ev.message, int(ev.simulated), json.dumps(ev.data, default=str)))
            await self.db.commit()
            ev.id = cur.lastrowid
        return ev

    async def list_events(self, job_id: Optional[int] = None, limit: int = 50) -> list[LiveWireEvent]:
        sql, args = "SELECT * FROM job_events", []
        if job_id is not None:
            sql += " WHERE job_id = ?"
            args.append(job_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        async with self.db.execute(sql, args) as c:
            rows = await c.fetchall()
        return [LiveWireEvent(id=r["id"], job_id=r["job_id"], at=datetime.fromisoformat(r["ts"]), kind=r["kind"],
                              message=r["message"], simulated=bool(r["simulated"]), data=json.loads(r["data_json"]))
                for r in rows]
