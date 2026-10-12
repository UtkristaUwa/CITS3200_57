"""
A listing page that fails part-way keeps the pages before it, and the login
page request is asked for again after a blip (the login itself never is).
"""

import logging
from pathlib import Path

import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.austender import scraper as austender
from error_scrapers.buy_nsw import crawl as buynsw_crawl
from error_scrapers.grant_connect import scraper as grantconnect
from error_scrapers.nt_qtol import scraper as nt
from error_scrapers.qld_qtenders import scraper as qld

ROOT = Path(__file__).parent.parent / "error_scrapers"
log = logging.getLogger("test")


def fixture(folder, name):
    return (ROOT / folder / "test_cases" / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(common, "PAGE_RETRY_SECONDS", 0)
    monkeypatch.setattr(austender, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(nt, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(qld, "PAUSE_SECONDS", 0)
    run_notes.reset()
    yield
    run_notes.reset()


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _fail_from(page_param, first_bad, good_html, error):
    """Handler: pages before `first_bad` are `good_html` (new links each page are not needed), then `error`."""
    calls = []

    def handler(request):
        page = int(request.url.params.get(page_param, "1"))
        calls.append(page)
        if page >= first_bad:
            if isinstance(error, int):
                return httpx.Response(error)
            raise error("The read operation timed out", request=request)
        return httpx.Response(200, text=good_html)

    handler.calls = calls
    return handler


# --- the helper -------------------------------------------------------------

def test_a_later_page_timing_out_is_kept_and_noted():
    problems = []
    exc = httpx.ReadTimeout("slow")
    assert common.listing_page_failed(log, 7, exc, 60, problems) is True
    assert problems == ["page 7"]


def test_the_first_page_failing_still_stops_the_run():
    problems = []
    assert common.listing_page_failed(log, 1, httpx.ReadTimeout("slow"), 0, problems) is False
    assert problems == []


def test_a_block_or_bug_on_a_later_page_still_stops_the_run():
    request = httpx.Request("GET", "https://x")
    blocked = httpx.HTTPStatusError("blocked", request=request, response=httpx.Response(429, request=request))
    assert common.listing_page_failed(log, 4, blocked, 30, []) is False
    assert common.listing_page_failed(log, 4, ValueError("bug"), 30, []) is False


def test_noting_listing_problems_marks_the_dashboard():
    assert common.note_listing_problems(log, []) is False
    assert run_notes.snapshot().partials == {}
    assert common.note_listing_problems(log, ["page 7"]) is True
    partials = run_notes.snapshot().partials
    assert partials["listing"] == [(run_notes.LISTING, "page 7")]


# --- GrantConnect -----------------------------------------------------------

def test_grantconnect_keeps_earlier_pages_when_a_later_one_times_out():
    html = fixture("grant_connect", "grantconnect_public_list.html")
    handler = _fail_from("page", 2, html, httpx.ReadTimeout)
    problems = []
    with _client(handler) as client:
        urls = grantconnect.collect_all_listing_urls(client, problems=problems)
    assert len(urls) > 0
    assert problems == ["page 2"]


def test_grantconnect_first_page_failure_still_raises():
    handler = _fail_from("page", 1, "", httpx.ReadTimeout)
    with _client(handler) as client, pytest.raises(httpx.ReadTimeout):
        grantconnect.collect_all_listing_urls(client, problems=[])


# --- AusTender --------------------------------------------------------------

def test_austender_keeps_earlier_pages_on_a_server_error():
    html = fixture("austender", "austender_public_list.html")
    handler = _fail_from("page", 2, html, 503)
    problems = []
    with _client(handler) as client:
        urls = austender.collect_all_listing_urls(client, problems=problems)
    assert len(urls) > 0
    assert problems == ["page 2"]


# --- NT ---------------------------------------------------------------------

def test_nt_keeps_earlier_pages_when_a_later_one_drops():
    html = fixture("nt_qtol", "nt_public_list.html")
    handler = _fail_from("page", 2, html, httpx.ConnectError)
    problems = []
    with _client(handler) as client:
        urls = nt.collect_all_listing_urls(client, problems=problems)
    assert len(urls) > 0
    assert problems == ["page 2"]


# --- QLD --------------------------------------------------------------------

def _qld_page(page):
    return {"totalPages": 5, "tenders": [
        {"vpReference": f"P{page}-{i}", "tenderPreviewUrl": f"https://x/{page}/{i}"} for i in range(3)]}


def test_qld_keeps_earlier_pages_when_a_later_one_fails():
    calls = []

    def handler(request):
        import json
        page = json.loads(request.content)
        page = page.get("page") or page.get("pageNumber") or page.get("currentPage")
        calls.append(page)
        if len(calls) >= 3:
            return httpx.Response(502)
        return httpx.Response(200, json=_qld_page(len(calls)))

    problems = []
    with _client(handler) as client:
        tenders = qld.collect_all_tenders(client, problems=problems)
    assert len(tenders) == 6
    assert len(problems) == 1 and problems[0].startswith("page 3")


# --- buy.nsw ----------------------------------------------------------------

def test_buynsw_crawl_keeps_tenders_and_the_cursor_when_a_listing_page_times_out():
    pages = {1: ["u1", "u2"], 2: ["u3"]}

    def fetch_page(page):
        if page == 3:
            raise httpx.ReadTimeout("slow")
        return 200, pages[page]

    res = buynsw_crawl.CrawlResult(next_page=1)
    buynsw_crawl.crawl(fetch_page, lambda url: (0, {"id": url}), frozenset(), 1, 0, log, res)
    assert [t["id"] for t in res.tenders] == ["u1", "u2", "u3"]
    assert res.listing_failed is True
    assert res.next_page == 3


def test_buynsw_crawl_still_raises_on_a_bug():
    def fetch_page(page):
        raise ValueError("bug")

    with pytest.raises(ValueError):
        buynsw_crawl.crawl(fetch_page, lambda url: (0, {}), frozenset(), 1, 0, log,
                           buynsw_crawl.CrawlResult(next_page=1))


# --- login page request -----------------------------------------------------

def _login_handler(login_html, failures, posts):
    state = {"gets": 0}

    def handler(request):
        if request.method == "POST":
            posts.append(1)
            return httpx.Response(200, text="<html></html>")
        if "ogin" in request.url.path:
            state["gets"] += 1
            if state["gets"] <= failures:
                raise httpx.ReadTimeout("slow", request=request)
            return httpx.Response(200, text=login_html)
        return httpx.Response(200, text="<html></html>")

    handler.state = state
    return handler


def test_grantconnect_login_page_blip_is_retried_and_the_login_is_sent_once(monkeypatch):
    monkeypatch.setenv("GRANTCONNECT_USERNAME", "a@example.com")
    monkeypatch.setenv("GRANTCONNECT_PASSWORD", "pw")
    posts = []

    def fake_submit(client, url, payload):
        posts.append(1)
        return type("R", (), {"text": fixture("grant_connect", "grantconnect_login_failure.html")})()

    monkeypatch.setattr(common, "submit_login", fake_submit)
    handler = _login_handler(fixture("grant_connect", "grantconnect_login_failure.html"), 1, [])
    with _client(handler) as client:
        assert grantconnect.login(client) is False  # rejected password, but it got that far
    assert handler.state["gets"] == 2
    assert len(posts) == 1


def test_austender_login_page_blip_is_retried_and_the_login_is_sent_once(monkeypatch):
    monkeypatch.setenv("AUSTENDER_USERNAME", "a@example.com")
    monkeypatch.setenv("AUSTENDER_PASSWORD", "pw")
    posts = []
    handler = _login_handler(fixture("austender", "austender_login_redirect.html"), 1, posts)
    with _client(handler) as client:
        austender.login(client)
    assert handler.state["gets"] == 2
    assert len(posts) == 1
