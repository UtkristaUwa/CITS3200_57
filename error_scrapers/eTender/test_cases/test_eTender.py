"""
Tests for the QBuild eTender scraper, same discipline as
error_scrapers/nt_qtol/test_cases/test_nt_qtol_scraper.py.

eTender_public_list.html (list page 1) and eTender_detail.html are live
captures, logged out.

eTender_page2.html is really page 1 again (the pager is a postback, and every
save came out as page 1), so it is not used. _page2() builds one from page 1 by
swapping the tenders and the pager; replace it with a real capture when we
have one. The structure-changed page renames the header table's id.
"""

import os
import re

import httpx
import pytest

from error_scrapers import common, run_notes
from error_scrapers.eTender import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://etender.hpw.qld.gov.au/public/TenderDetailsActive.aspx?listingPage=Current&tid=16897"


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _page1() -> str:
    return _read_fixture("eTender_public_list.html")


def _page2() -> str:
    """Page 1 with its tenders renumbered and the pager/"Page n of 2" moved to page 2."""
    html = re.sub(r"tid=(\d+)", lambda m: f"tid=9{m.group(1)}", _page1())
    html = html.replace("Page 1 of 2", "Page 2 of 2")
    html = re.sub(r"<td><span>1</span></td><td><a href=\"javascript:__doPostBack\(&#39;([^&]+)&#39;,&#39;Page\$2&#39;\)\">2</a></td>",
                  r"<td><a href=\"javascript:__doPostBack(&#39;\1&#39;,&#39;Page$1&#39;)\">1</a></td><td><span>2</span></td>",
                  html)
    return html


def _structure_changed() -> str:
    return _read_fixture("eTender_detail.html").replace('id="tenderHeader"', 'id="tenderHeader-CHANGED"')


def _portal(list_pages=2):
    """A fake eTender: GET = page 1, POST with Page$n = page n."""
    state = {"posts": [], "details": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/public/TendersListingCurrent.aspx":
            if request.method == "POST":
                body = request.content.decode()
                state["posts"].append(body)
                if "Page%2429" in body or "Page%242" in body and list_pages >= 2:
                    return httpx.Response(200, text=_page2())
                return httpx.Response(200, text=_page1())
            return httpx.Response(200, text=_page1())
        if path == "/public/TenderDetailsActive.aspx":
            state["details"] += 1
            return httpx.Response(200, text=_read_fixture("eTender_detail.html"))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    transport.state = state
    return transport


def _patch_client(monkeypatch, transport):
    real_client = httpx.Client
    monkeypatch.setattr(
        scraper.httpx, "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    run_notes.reset()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_every_tender_once_although_each_is_linked_twice():
    links = scraper.parse_listing(_page1())
    assert len(links) == 10  # 10 tenders per page, each linked twice
    assert links[0].endswith("TenderDetailsActive.aspx?listingPage=Current&tid=16856")
    assert len(set(links)) == len(links)


def test_total_pages_comes_from_page_n_of_m():
    assert scraper.total_pages(_page1()) == 2
    assert scraper.total_pages("<html></html>") is None


def test_next_page_form_replays_hidden_fields_and_names_the_pager_target():
    form = scraper.next_page_form(_page1(), 2)
    assert form["__EVENTTARGET"] == "ctl00$ContentPlaceHolder1$gvTenders"
    assert form["__EVENTARGUMENT"] == "Page$2"
    assert form["__VIEWSTATE"] and form["__EVENTVALIDATION"]


def test_next_page_form_is_none_when_the_pager_has_no_such_page():
    assert scraper.next_page_form(_page1(), 9) is None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

def test_success_parses_the_detail_page():
    fields, code = scraper.parse_detail(_read_fixture("eTender_detail.html"))
    assert code == common.SITE_SUCCESS
    assert fields["reference"] == "94714ELE2"
    assert fields["closing_date"] == "27/10/2026 14:00 AEST"
    assert fields["category"] == "Contractor"
    assert fields["regions"] == "Brisbane"
    assert fields["federal_electorates"] == "Blair"
    assert fields["local_authorities"] == "Ipswich City Council"
    assert fields["contact_name"] == "Procurement Services"
    assert fields["contact_phone"] == "3008 2332"
    assert fields["title"].startswith("12A RIVER ROAD, BUNDAMBA")
    assert fields["pqc_level"] is None  # empty on the page


def test_site_structure_change_when_the_header_table_is_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_page_text_holds_the_fields_and_the_source_url(tmp_path):
    client = httpx.Client(transport=_portal(), follow_redirects=True)
    code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS
    text = open(os.path.join(tender["folder"], "__tender__94714ELE2.txt"), encoding="utf-8").read()
    assert "closing_date: 27/10/2026 14:00 AEST" in text
    assert "Bundamba TAFE Electrical Upgrades" in text
    assert f"Detail URL: {DETAIL_URL}" in text
    assert tender["attachments"] == []


def test_a_tender_with_no_documents_is_complete_not_partial(tmp_path):
    client = httpx.Client(transport=_portal(), follow_redirects=True)
    code, _ = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS


def test_the_scraper_never_posts_to_a_detail_page(tmp_path, monkeypatch):
    seen = []
    base = _portal()

    def handler(request):
        if request.url.path.endswith("TenderDetailsActive.aspx"):
            seen.append(request.method)
        return base.handler(request)

    _patch_client(monkeypatch, httpx.MockTransport(handler))
    scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert seen and set(seen) == {"GET"}  # no Login / Apply / Add to Watch List


# ---------------------------------------------------------------------------
# Whole run
# ---------------------------------------------------------------------------

def test_run_walks_both_pages_through_the_postback(tmp_path, monkeypatch):
    transport = _portal()
    _patch_client(monkeypatch, transport)
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_SUCCESS
    assert result.count == 20  # 10 on page 1 + 10 on (derived) page 2
    assert len(transport.state["posts"]) == 1
    assert "__VIEWSTATE=" in transport.state["posts"][0]


def test_run_respects_limit(tmp_path, monkeypatch):
    transport = _portal()
    _patch_client(monkeypatch, transport)
    result = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert result.count == 2
    assert transport.state["posts"] == []  # page 1 was enough


def test_empty_first_list_page_is_a_structure_change(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, text="<html><body>nothing</body></html>")
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert result.code == common.SITE_STRUCTURE_CHANGE


def test_site_total_failure_on_unreachable_url(monkeypatch, tmp_path):
    def handler(request):
        raise httpx.ConnectError("down")
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(output_dir=str(tmp_path)).code == common.SITE_TOTAL_FAILURE


@pytest.mark.parametrize("status, expected", [
    (429, common.SITE_RATE_LIMITED),
    (403, common.SITE_BOT_BLOCKED),
])
def test_429_and_403_on_the_list_stop_the_run_without_retry(status, expected, tmp_path, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(output_dir=str(tmp_path)).code == expected
    assert len(calls) == 1


def test_403_on_a_detail_page_stops_the_run(tmp_path, monkeypatch):
    base = _portal()

    def handler(request):
        if request.url.path.endswith("TenderDetailsActive.aspx"):
            return httpx.Response(403)
        return base.handler(request)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    assert scraper.run_scraper(limit=2, output_dir=str(tmp_path)).code == common.SITE_BOT_BLOCKED


def test_a_detail_page_that_times_out_is_asked_for_again(tmp_path, monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None, raising=False)
    base = _portal()
    attempts = {"n": 0}

    def handler(request):
        if request.url.path.endswith("TenderDetailsActive.aspx") and attempts["n"] == 0:
            attempts["n"] += 1
            raise httpx.ReadTimeout("slow")
        return base.handler(request)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS and tender


def test_a_list_page_that_fails_is_asked_for_again_and_no_tender_is_lost(tmp_path, monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None, raising=False)
    transport = _portal()
    failures = {"n": 0}

    def handler(request):
        if request.method == "POST" and failures["n"] == 0:
            failures["n"] += 1
            return httpx.Response(503, text="try later")
        return transport.handler(request)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert failures["n"] == 1
    assert result.code == common.SITE_SUCCESS
    assert result.count == 20  # page 2 arrived on the second try


def test_a_429_on_the_list_pager_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None, raising=False)
    transport = _portal()
    posts = {"n": 0}

    def handler(request):
        if request.method == "POST":
            posts["n"] += 1
            return httpx.Response(429, text="slow down")
        return transport.handler(request)
    _patch_client(monkeypatch, httpx.MockTransport(handler))
    result = scraper.run_scraper(output_dir=str(tmp_path))
    assert posts["n"] == 1
    assert result.code == common.SITE_RATE_LIMITED


def test_run_scraper_returns_a_scrape_result(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal())
    result = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert isinstance(result, common.ScrapeResult)
    assert result.site == "qbuild_etender"