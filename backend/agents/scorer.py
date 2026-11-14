"""Agent 3 - Scorer: deterministic, explainable scoring with auditable receipts.

No LLM and no network: the same (FinderOutput, VerifierEvidence, baseline_hash, now) always yields the
same verdict. Every scoring step is emitted as a CitedClaim tagged with the engine that produced the
underlying evidence (You.com / OpenCrawl; "Direct HTTP" only for the public Ashby API and status probe).

open_confidence: start 100
    -> 0 on HTTP 404/410, soft-404 / redirect away from the posting, expired validThrough, closing language
       (incl. Ashby isListed=false)
    -40 if the Apply control is missing/disabled (only when the DOM is reliable; otherwise flagged uncertain)
    -40 for any other non-2xx status (page unverified; flagged uncertain)
apply_score: start at open_confidence (0 stays 0)
    +15 (cap 100) if published < 48h ago or a mirror snippet says "Be an early applicant"
    -35 if posted > 30 days ago and the content hash equals the stored baseline hash (stale repost)
    -45 if last-72h news reports layoffs or a hiring freeze
Recommendation thresholds (ours, tunable): Do Not Apply if open_confidence == 0 or apply_score < 40;
Apply Immediately if apply_score >= 75, open_confidence >= 80 and no conflicts; otherwise Apply with Caution.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from backend.models import CitedClaim, FinderOutput, JobStatus, ScorerVerdict, VerifierEvidence

FRESH_WINDOW = timedelta(hours=48)
STALE_WINDOW = timedelta(days=30)
REC_APPLY = "立即投递 / Apply Immediately"
REC_CAUTION = "谨慎投递 / Apply with Caution"
REC_NO = "不建议投递 / Do Not Apply"
GHOST_RISK_BELOW = 70  # open but apply_score under this => GHOST_RISK (stale repost, hiring freeze, missing Apply...)

# Evidence that always comes from one engine, whatever fetched the page. Used when no matching receipt exists
# (e.g. a FinderOutput built without receipts), so a news/mirror step is never mislabelled with the page-fetch engine.
SIGNAL_ENGINE: dict[str, str] = {
    "canonical": "You.com",
    "mirror": "You.com",
    "news": "You.com",
    "hiring_research": "You.com",
    "conflict": "You.com",
    "ashby_api": "Direct HTTP",
}


def score(
    finder: FinderOutput,
    evidence: VerifierEvidence,
    *,
    baseline_hash: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ScorerVerdict:
    now = now or datetime.now(timezone.utc)
    url = str(finder.canonical_ats_url)
    pool = [*evidence.receipts, *finder.receipts]
    default_engine = evidence.fetch_engine or "Direct HTTP"
    uncertain: set[str] = set(finder.uncertain_fields) | set(evidence.uncertain_fields)
    steps: list[CitedClaim] = []

    def cite(text: str, signal: str, engine: str | None = None) -> CitedClaim:
        r = next((x for x in pool if x.signal == signal), None)
        eng = r.engine if r else (engine or SIGNAL_ENGINE.get(signal) or default_engine)
        return CitedClaim(claim=text, source_url=str(r.source_url) if r else url,
                          engine=eng, signal=signal)  # type: ignore[arg-type]

    # ---------------------------------------------------------------- open_confidence
    oc = 100
    zero: list[tuple[str, str]] = []  # (reason, signal)
    st = evidence.http_status
    if st in (404, 410):
        zero.append((f"HTTP {st} on the canonical posting", "http_status"))
    elif st == 0:
        uncertain.add("http_status")
    elif not (200 <= st < 300) and not (300 <= st < 400):
        oc -= 40
        uncertain.add("http_status")
        steps.append(cite(f"open_confidence -40: HTTP {st} - posting could not be verified", "http_status"))
    if evidence.soft_404 or not evidence.final_url_matches:
        zero.append(("page redirected away from the posting (soft-404 / generic careers page)", "http_status"))
    vt = evidence.timestamps.valid_through
    if vt and vt < now:
        zero.append((f"validThrough {vt.date().isoformat()} has passed", "valid_through"))
    if evidence.closed_signals_detected:
        sig = "ashby_api" if evidence.ashby_is_listed is False else "closed_phrase"
        zero.append(("closing signals detected: " + "; ".join(evidence.closed_signals_detected), sig))
    if zero:
        for reason, signal in zero:
            steps.append(cite(f"open_confidence -> 0: {reason}", signal))
        oc = 0
    elif evidence.dom_reliable and not (evidence.apply_button.found and evidence.apply_button.clickable):
        oc -= 40
        state = "disabled" if evidence.apply_button.found else "missing"
        steps.append(cite(f"open_confidence -40: Apply control {state}", "apply_button"))
    elif not evidence.dom_reliable:
        uncertain.add("apply_button")
    if oc == 100:
        steps.append(cite("open_confidence 100: live page, matching URL, enabled Apply control, no closing signals", "apply_button"))
    oc = max(0, min(100, oc))

    # ---------------------------------------------------------------- apply_score
    published = evidence.timestamps.published_at
    if published is None:
        uncertain.add("published_at")
    if oc == 0:
        ap = 0
        steps.append(cite("apply_score 0: posting is not open", "http_status"))
    else:
        ap = oc
        early_sig = next((sig for m in finder.mirror_urls for sig in m.snippet_signals if "early applicant" in sig.lower()), None)
        fresh = published is not None and (now - published) < FRESH_WINDOW
        if fresh or early_sig:
            ap = min(100, ap + 15)
            why = (f"published {published.isoformat()} (<48h)" if fresh else "") + (" and " if fresh and early_sig else "")
            why += f"mirror signal '{early_sig}'" if early_sig else ""
            steps.append(cite(f"apply_score +15: {why}", "ashby_api" if fresh and evidence.timestamps.source_type == "ats_api"
                              else ("mirror" if early_sig and not fresh else "json_ld")))
        if published is not None and (now - published) > STALE_WINDOW:
            if baseline_hash and evidence.content_hash == baseline_hash:
                ap -= 35
                steps.append(cite(f"apply_score -35: posted {(now - published).days} days ago and content hash is unchanged since baseline (stale repost risk)",
                                  "ashby_api" if evidence.timestamps.source_type == "ats_api" else "json_ld"))
            elif not baseline_hash:
                uncertain.add("content_hash_baseline")
        bad = [n for n in finder.recent_company_news if n.category in ("layoffs", "hiring_freeze")]
        if bad:
            ap -= 45
            steps.append(cite(f"apply_score -45: recent news reports {', '.join(sorted({n.category for n in bad}))} at {finder.company_name}: {bad[0].headline}", "news"))
        ap = max(0, min(100, ap))

    if finder.hiring_research:
        steps.append(cite(f"Context (no score change): You.com Research hiring verdict {finder.hiring_research.verdict}", "hiring_research", "You.com"))
    for c in evidence.conflicts:
        steps.append(cite(f"Conflict {c.code}: official={c.official_value}; other={c.mirror_value}", "conflict", "You.com"))

    # ---------------------------------------------------------------- recommendation
    if oc == 0 or ap < 40:
        rec = REC_NO
    elif ap >= 75 and oc >= 80 and not evidence.conflicts:
        rec = REC_APPLY
    else:
        rec = REC_CAUTION

    # receipts: scoring steps first, then the remaining raw evidence (deduplicated)
    seen = {s.claim for s in steps}
    extras = [r for r in pool if r.claim not in seen]
    actions = list(dict.fromkeys([*finder.actions_taken, *evidence.actions_taken]))
    return ScorerVerdict(
        open_confidence=oc,
        apply_score=ap,
        uncertain_fields=sorted(uncertain),
        recommendation=rec,  # type: ignore[arg-type]
        reasoning_with_citations=[*steps, *extras],
        autonomous_action_taken="; ".join(actions) or None,
    )


def status_for(verdict: ScorerVerdict) -> JobStatus:
    """Map a verdict to the persisted watch status (our mapping): CLOSED if open_confidence == 0, GHOST_RISK if apply_score < 70."""
    if verdict.open_confidence == 0:
        return JobStatus.CLOSED
    if verdict.apply_score < GHOST_RISK_BELOW:
        return JobStatus.GHOST_RISK
    return JobStatus.OPEN
