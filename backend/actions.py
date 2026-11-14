"""Autonomous actions fired by the watcher: outreach drafting and outbound webhooks."""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx

from backend.config import Settings, get_settings
from backend.logging_config import log
from backend.models import FinderOutput, VerifierEvidence

logger = logging.getLogger("jobverify.actions")


def _sentences(text: str) -> list[str]:
    return re.findall(r"[^.!?]+[.!?]", text.strip())


def _age(delta_seconds: float) -> str:
    m = int(max(delta_seconds, 0) // 60)
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h = m // 60
    if h < 48:
        return f"{h} hour{'s' if h != 1 else ''}"
    return f"{h // 24} days"


def template_draft(finder: FinderOutput, evidence: VerifierEvidence, now: datetime) -> str:
    pub = evidence.timestamps.published_at
    co, role = finder.company_name, finder.role_title
    if pub:
        ts = pub.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
        s1 = (f"I noticed the {role} role at {co} was published on {ts} ({_age((now - pub).total_seconds())} ago), "
              "so I wanted to reach out while the posting is still fresh.")
    else:
        s1 = f"I noticed the {role} role at {co} is live on your official careers page, so I wanted to reach out directly."
    s2 = (f"My background in [your most relevant experience] maps closely to what the role calls for, and I'd love to "
          f"contribute to [a specific {co} product or goal].")
    s3 = "Would you be open to a 15-minute conversation this week about the team's priorities?"
    return f"Hi [Hiring Manager],\n\n{s1} {s2} {s3}"


async def _llm_draft(finder: FinderOutput, evidence: VerifierEvidence, now: datetime, s: Settings) -> Optional[str]:
    pub = evidence.timestamps.published_at
    if not (s.anthropic_api_key and pub):
        return None
    ts = pub.astimezone(timezone.utc).strftime("%b %d, %Y %H:%M UTC")
    prompt = (
        f"Write a cold outreach message to the hiring manager for the '{finder.role_title}' role at {finder.company_name}. "
        f"Exactly 3 sentences, no greeting, no sign-off. Sentence 1 must include the exact text '{ts}' as the posting's "
        "publish time. Do not invent facts about the sender: use [bracketed placeholders] for their experience. "
        "Sentence 3 asks for a short conversation. Output only the 3 sentences."
    )
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=s.anthropic_api_key)
        resp = await asyncio.wait_for(
            client.messages.create(model=s.anthropic_model, max_tokens=300, messages=[{"role": "user", "content": prompt}]), 10)
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    except Exception as e:  # noqa: BLE001
        log(logger, logging.WARNING, "llm_draft_failed", error=repr(e))
        return None
    if len(_sentences(text)) != 3 or ts not in text:
        log(logger, logging.WARNING, "llm_draft_rejected", sentences=len(_sentences(text)))
        return None
    return f"Hi [Hiring Manager],\n\n{text}"


async def draft_outreach(finder: FinderOutput, evidence: VerifierEvidence, *, now: datetime,
                         settings: Settings | None = None, use_llm: bool = True) -> str:
    """3-sentence cold outreach citing the posting's publishedAt. LLM when configured, else a deterministic template."""
    s = settings or get_settings()
    if use_llm:
        text = await _llm_draft(finder, evidence, now, s)
        if text:
            return text
    return template_draft(finder, evidence, now)


async def send_webhooks(text: str, settings: Settings | None = None) -> tuple[list[str], list[str]]:
    """POST to Discord/Slack if configured. Returns (channels_sent, errors). Never raises."""
    s = settings or get_settings()
    targets = [("Discord", s.discord_webhook_url, {"content": text[:1900]}),
               ("Slack", s.slack_webhook_url, {"text": text})]
    sent: list[str] = []
    errors: list[str] = []
    async with httpx.AsyncClient(timeout=10) as client:
        for name, url, payload in targets:
            if not url:
                continue
            try:
                r = await client.post(url, json=payload)
                r.raise_for_status()
                sent.append(name)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{name}: {e!r}")
                log(logger, logging.WARNING, "webhook_failed", target=name, error=repr(e))
    return sent, errors
