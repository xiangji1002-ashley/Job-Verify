"""Deterministic scorer tests (no network). Run: pytest -q"""
from datetime import datetime, timedelta, timezone

from backend.agents.scorer import REC_APPLY, REC_CAUTION, REC_NO, score
from backend.models import ApplyButton, CompanyNewsItem, FinderOutput, MirrorUrl, Timestamps, VerifierEvidence

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
URL = "https://jobs.ashbyhq.com/acme/1234"


def finder(**kw):
    return FinderOutput(canonical_ats_url=URL, ats_provider="ashby", requisition_id="1234",
                        company_name="Acme", role_title="Engineer", **kw)


def evidence(**kw):
    base = dict(http_status=200, final_url_matches=True, dom_reliable=True, fetch_engine="OpenCrawl",
                apply_button=ApplyButton(found=True, clickable=True, selector_or_receipt="button :: 'Apply'"),
                timestamps=Timestamps(published_at=NOW - timedelta(days=5)))
    base.update(kw)
    return VerifierEvidence(**base)


def test_healthy_posting_is_apply_immediately():
    v = score(finder(), evidence(), now=NOW)
    assert (v.open_confidence, v.apply_score, v.recommendation) == (100, 100, REC_APPLY)


def test_404_zeroes_everything():
    v = score(finder(), evidence(http_status=404), now=NOW)
    assert (v.open_confidence, v.apply_score, v.recommendation) == (0, 0, REC_NO)


def test_closing_phrase_zeroes():
    v = score(finder(), evidence(closed_signals_detected=["position filled"]), now=NOW)
    assert v.open_confidence == 0 and v.recommendation == REC_NO


def test_expired_valid_through_zeroes():
    ts = Timestamps(published_at=NOW - timedelta(days=5), valid_through=NOW - timedelta(days=1))
    assert score(finder(), evidence(timestamps=ts), now=NOW).open_confidence == 0


def test_soft_redirect_zeroes():
    assert score(finder(), evidence(final_url_matches=False, soft_404=True), now=NOW).open_confidence == 0


def test_missing_apply_button_minus_40():
    v = score(finder(), evidence(apply_button=ApplyButton(found=False, clickable=False)), now=NOW)
    assert v.open_confidence == 60


def test_unreliable_dom_does_not_penalize_but_flags_uncertain():
    v = score(finder(), evidence(dom_reliable=False, apply_button=ApplyButton(found=False, clickable=False)), now=NOW)
    assert v.open_confidence == 100 and "apply_button" in v.uncertain_fields


def test_fresh_boost_caps_at_100_and_early_applicant_boosts():
    f = finder(mirror_urls=[MirrorUrl(url="https://www.linkedin.com/jobs/view/1", source="linkedin",
                                      snippet_signals=["Be an early applicant"])])
    e = evidence(apply_button=ApplyButton(found=False, clickable=False))  # oc=60
    assert score(f, e, now=NOW).apply_score == 75
    fresh = evidence(timestamps=Timestamps(published_at=NOW - timedelta(hours=3)))
    assert score(finder(), fresh, now=NOW).apply_score == 100


def test_stale_identical_hash_minus_35():
    e = evidence(content_hash="abc", timestamps=Timestamps(published_at=NOW - timedelta(days=45)))
    v = score(finder(), e, baseline_hash="abc", now=NOW)
    assert v.apply_score == 65 and v.recommendation == REC_CAUTION
    assert "content_hash_baseline" in score(finder(), e, now=NOW).uncertain_fields


def test_layoff_news_minus_45():
    news = [CompanyNewsItem(headline="Acme lays off 20%", url="https://news.example.com/a", category="layoffs")]
    v = score(finder(recent_company_news=news), evidence(), now=NOW)
    assert v.apply_score == 55 and v.recommendation == REC_CAUTION
    assert any(c.signal == "news" and c.engine == "You.com" for c in v.reasoning_with_citations)


def test_every_citation_has_a_known_engine():
    v = score(finder(), evidence(), now=NOW)
    assert v.reasoning_with_citations and all(c.engine in ("You.com", "OpenCrawl", "Direct HTTP") for c in v.reasoning_with_citations)
