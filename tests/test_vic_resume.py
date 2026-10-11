"""
VIC: a Cloudflare block part-way through is progress, not a failure.

Seen every night: VIC scrapes some tenders, Cloudflare then blocks it, and the
run was reported as blocked. With resume on, the new tenders go first, a block
after at least one tender is TENDER_PARTIAL, and tomorrow starts with whatever
is not stored yet. No browser, no network.
"""

import pytest

from error_scrapers import common
from error_scrapers.vic_buyingfor import scraper as vic


def _entries(n):
    return [{"url": f"https://vic.test/t{i}"} for i in range(n)]


class FakeSession:
    def __init__(self, headless=True):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self):
        return True


@pytest.fixture
def site(monkeypatch):
    seen = {"scraped": []}
    state = {"block_after": None, "entries": _entries(5)}

    monkeypatch.setattr(vic, "BrowserSession", FakeSession)
    monkeypatch.setattr(vic, "credentials", lambda: ("u", "p"))
    monkeypatch.setattr(vic, "collect_all_listing_entries",
                        lambda session, limit=0: list(state["entries"]))
    monkeypatch.setattr(vic.time, "sleep", lambda s: None)

    def scrape_opportunity(session, entry, output_dir):
        if state["block_after"] is not None and len(seen["scraped"]) >= state["block_after"]:
            raise vic.BotBlockedError("challenge")
        seen["scraped"].append(entry["url"])
        return common.SITE_SUCCESS, {"tender_id": entry["url"], "documents_gated": False}

    monkeypatch.setattr(vic, "scrape_opportunity", scrape_opportunity)
    seen["state"] = state
    return seen


def test_new_tenders_come_before_stored_ones():
    entries = _entries(4)
    ordered, new = vic.order_for_resume(entries, {entries[0]["url"], entries[2]["url"]})
    assert [e["url"] for e in ordered] == [entries[1]["url"], entries[3]["url"],
                                           entries[0]["url"], entries[2]["url"]]
    assert new == 2


def test_nothing_known_keeps_listing_order():
    entries = _entries(3)
    assert vic.order_for_resume(entries, None) == (entries, 3)


def test_block_after_some_tenders_is_a_success_when_resuming(site):
    site["state"]["block_after"] = 2
    code, tenders = vic._scrape_site(known_urls=set(), resume=True)
    assert code == common.SITE_SUCCESS  # the intended flow: tomorrow carries on
    assert len(tenders) == 2


def test_block_after_some_tenders_is_still_blocked_without_resume(site):
    site["state"]["block_after"] = 2
    code, tenders = vic._scrape_site()
    assert code == common.SITE_BOT_BLOCKED
    assert len(tenders) == 2


def test_block_before_any_tender_is_blocked_even_when_resuming(site):
    site["state"]["block_after"] = 0
    code, tenders = vic._scrape_site(known_urls=set(), resume=True)
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_resume_scrapes_the_new_tenders_first(site):
    entries = site["state"]["entries"]
    known = {entries[0]["url"], entries[1]["url"], entries[2]["url"]}
    site["state"]["block_after"] = 2  # only room for two before the block
    code, _ = vic._scrape_site(known_urls=known, resume=True)
    assert code == common.SITE_SUCCESS
    assert site["scraped"] == [entries[3]["url"], entries[4]["url"]]


def test_no_block_is_a_clean_run(site):
    code, tenders = vic._scrape_site(known_urls={"https://vic.test/t0"}, resume=True)
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 5


def test_retry_wrapper_passes_resume_through(site):
    site["state"]["block_after"] = 1
    code, tenders = vic._scrape_with_retries(known_urls=set(), resume=True)
    assert code == common.SITE_SUCCESS and len(tenders) == 1


def test_manager_hands_vic_the_stored_urls():
    # Read as text: importing manager needs the full GCP stack.
    import pathlib
    import re
    source = (pathlib.Path(__file__).parent.parent / "manager.py").read_text(encoding="utf-8")
    sources = re.search(r"CRAWL_RESUME_SOURCES\s*=\s*\{([^}]*)\}", source).group(1)
    assert '"vic_buyingfor"' in sources


def test_a_block_with_an_incomplete_tender_is_still_partial(site, monkeypatch):
    def scrape_opportunity(session, entry, output_dir):
        if len(site["scraped"]) >= 2:
            raise vic.BotBlockedError("challenge")
        site["scraped"].append(entry["url"])
        code = common.TENDER_PARTIAL if len(site["scraped"]) == 1 else common.SITE_SUCCESS
        return code, {"tender_id": entry["url"], "documents_gated": False}

    monkeypatch.setattr(vic, "scrape_opportunity", scrape_opportunity)
    code, _ = vic._scrape_site(known_urls=set(), resume=True)
    assert code == common.TENDER_PARTIAL
