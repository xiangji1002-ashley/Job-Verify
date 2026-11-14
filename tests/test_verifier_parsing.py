"""Offline tests for DOM parsing helpers. Run: pytest -q"""
from bs4 import BeautifulSoup

from backend.agents.verifier import detect_apply, match_final_url, parse_job_posting

HTML = """<html><head><script type="application/ld+json">
{"@context":"https://schema.org","@type":"JobPosting","title":"Engineer","datePosted":"2026-11-10",
 "validThrough":"2026-12-31T00:00:00Z","hiringOrganization":{"name":"Acme"},
 "jobLocation":{"address":{"addressLocality":"NYC","addressRegion":"NY","addressCountry":"US"}}}
</script></head><body><main><button disabled>Apply now</button><a href="/x/apply">Apply</a></main></body></html>"""


def test_json_ld_job_posting():
    p = parse_job_posting(BeautifulSoup(HTML, "html.parser"))
    assert p and p.title == "Engineer" and p.job_location == "NYC, NY, US" and p.valid_through.year == 2026


def test_apply_prefers_enabled_control():
    a = detect_apply(BeautifulSoup(HTML, "html.parser"), "sess1")
    assert a.found and a.clickable and "apply" in a.selector_or_receipt.lower()


def test_disabled_only_is_not_clickable():
    a = detect_apply(BeautifulSoup("<button disabled>Apply</button>", "html.parser"), None)
    assert a.found and not a.clickable


def test_final_url_matching_and_soft_404():
    u = "https://jobs.ashbyhq.com/acme/1234"
    assert match_final_url(u, u + "/application", "1234") == (True, False)
    assert match_final_url(u, "https://jobs.ashbyhq.com/acme", "1234") == (False, True)
    assert match_final_url(u, None, "1234") == (None, False)
