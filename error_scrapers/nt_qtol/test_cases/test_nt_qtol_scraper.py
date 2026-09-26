"""
Tests for the NT QTOL scraper, same discipline as
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

nt_public_list.html (the search-results fragment), nt_full_details.html and
nt_login_page.html (where an anonymous "Download tender" lands) are live
captures, trimmed (no scripts/styles/svg; the listing cut to 3 cards). The
structure-changed page is built at runtime by renaming every
"stepper-info-cell" class.

The logged-in document zip can't be captured without an account with a
business attached, so it is built in-test.
"""

import io
import os
import zipfile

import fitz
import httpx
import pytest

from error_scrapers import common
from error_scrapers.nt_qtol import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://tendersonline.nt.gov.au/Tender/Details/26426"


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _structure_changed() -> str:
    return _read_fixture("nt_full_details.html").replace("stepper-info-cell", "stepper-info-cell-CHANGED")


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _bundle_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("RFF26-0130/Request for Feedback.pdf", _pdf_bytes("Network management services"))
        zf.writestr("RFF26-0130/Pricing Schedule.csv", b"item,price\n")
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as izf:
            izf.writestr("Response Template.pdf", _pdf_bytes("Response template"))
        zf.writestr("Templates.zip", inner.getvalue())
        zf.writestr("../../escape.txt", b"should stay inside the folder")
    return buf.getvalue()


def _portal(bundle: bytes | None = None, list_pages=1):
    """A fake QTOL. bundle=None means the download redirects to the login form."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/Tender/SearchResults/Current":
            if int(request.url.params.get("page", "1")) <= list_pages:
                return httpx.Response(200, text=_read_fixture("nt_public_list.html"))
            return httpx.Response(200, text="<div id='tender-search-results'></div>")
        if path.startswith("/Tender/Details/"):
            return httpx.Response(200, text=_read_fixture("nt_full_details.html"))
        if path.startswith("/Tender/DownloadProfile/"):
            if bundle is None:
                return httpx.Response(302, headers={"Location": f"/Account/LogOn?ReturnUrl={path}"})
            return httpx.Response(200, content=bundle, headers={"content-type": "application/zip"})
        if path == "/Account/LogOn":
            return httpx.Response(
                200, text=_read_fixture("nt_login_page.html"),
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _client(transport):
    return httpx.Client(transport=transport, follow_redirects=True)


def _patch_client(monkeypatch, transport):
    real_client = httpx.Client
    monkeypatch.setattr(
        scraper.httpx, "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.fixture(autouse=True)
def _no_pause_no_cookie(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("NT_QTOL_COOKIE", raising=False)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_every_tender_card():
    links = scraper.parse_listing(_read_fixture("nt_public_list.html"))
    assert len(links) == 3
    assert all(link.startswith("https://tendersonline.nt.gov.au/Tender/Details/") for link in links)


def test_listing_links_drop_the_status_query_and_are_deduplicated():
    links = scraper.parse_listing(_read_fixture("nt_public_list.html"))
    assert not any("?" in link for link in links)
    assert len(links) == len(set(links))


def test_total_pages_comes_from_the_record_count():
    # the fixture reports 33 records; at 20 a page that is 2 pages
    assert scraper.total_pages(_read_fixture("nt_public_list.html")) == 2
    assert scraper.total_pages("<div></div>") is None


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_read_fixture("nt_full_details.html"))
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "Request for Feedback - Provision of Network Management Services"
    assert fields["reference"] == "RFF26-0130"
    assert fields["agency"] == "Department of Corporate and Digital Development"
    assert fields["release_date"] == "23/09/2026"
    assert fields["close_date"] == "09/10/2026 2:00 PM ACST"
    # "To be determined" is recorded as absent, not as a date
    assert fields["award_date"] is None
    assert fields["region"] == "Darwin, Palmerston, Litchfield"
    assert fields["enquiries_phone"] == "08 8924 3854"
    assert fields["description"].startswith("The Department of Corporate and Digital Development")


def test_site_structure_change_when_milestones_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_finds_the_download_link():
    url = scraper.find_download_url(_read_fixture("nt_full_details.html"))
    assert url == "https://tendersonline.nt.gov.au/Tender/DownloadProfile/26426"


def test_missing_download_link_does_not_crash():
    html = _read_fixture("nt_full_details.html").replace("/Tender/DownloadProfile/", "/Tender/Nothing/")
    assert scraper.find_download_url(html) is None


# ---------------------------------------------------------------------------
# Full tender scrape
# ---------------------------------------------------------------------------

def test_scrape_opportunity_end_to_end(tmp_path):
    with _client(_portal(_bundle_zip())) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert tender["tender_id"] == "RFF26-0130"
    assert tender["source_url"] == DETAIL_URL
    assert sorted(a["file_name"] for a in tender["attachments"]) == [
        "Pricing Schedule.csv",
        "Request for Feedback.pdf",
        "Response Template.pdf",
        "escape.txt",
    ]
    saved = sorted(os.listdir(tender["folder"]))
    assert saved == [
        "Pricing Schedule.csv",
        "Request for Feedback.pdf",
        "Request for Feedback.pdf.txt",
        "Response Template.pdf",
        "Response Template.pdf.txt",
        "__tender__RFF26-0130.txt",
        "escape.txt",
    ]
    # the zip-slip entry stayed inside the tender folder
    assert not (tmp_path / "escape.txt").exists()
    extracted = open(os.path.join(tender["folder"], "Request for Feedback.pdf.txt")).read()
    assert "Network management services" in extracted


def test_page_text_holds_the_detail_fields(tmp_path):
    with _client(_portal(_bundle_zip())) as client:
        _, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    text = open(os.path.join(tender["folder"], "__tender__RFF26-0130.txt")).read()
    assert "Request for Feedback - Provision of Network Management Services" in text
    assert "GPO Box 2391, Darwin NT 0801" in text
    assert "DESCRIPTION:" in text


def test_download_behind_login_gives_tender_partial_with_page_text_saved(tmp_path):
    with _client(_portal(bundle=None)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__RFF26-0130.txt"]


def test_a_corrupt_bundle_gives_tender_partial_and_leaves_no_zip_behind(tmp_path):
    with _client(_portal(b"this is not a zip")) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__RFF26-0130.txt"]


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(_bundle_zip()))
    code, tenders = scraper.run_scraper(limit=3, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 3


def test_run_walks_every_reported_page(monkeypatch):
    # the fixture reports 2 pages; both return the same cards here, so the
    # de-duplicated total is still 3 -- but page 2 must have been asked for
    pages_seen = []
    transport = _portal(_bundle_zip(), list_pages=5)

    def spy(request):
        if request.url.path == "/Tender/SearchResults/Current":
            pages_seen.append(request.url.params.get("page"))
        return transport.handle_request(request)

    with _client(httpx.MockTransport(spy)) as client:
        urls = scraper.collect_all_listing_urls(client)
    assert len(urls) == 3
    assert pages_seen == ["1", "2"]


def test_run_without_cookie_is_partial_when_downloads_are_gated(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(bundle=None))
    code, tenders = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) >= 1


def test_run_with_a_stale_cookie_is_login_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("NT_QTOL_COOKIE", ".AspNet.ApplicationCookie=expired")
    _patch_client(monkeypatch, _portal(bundle=None))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(tenders) == 1


def test_empty_first_search_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, text="<div id='tender-search-results'></div>")
    ))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tenders == []


def test_site_total_failure_on_unreachable_url(monkeypatch):
    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", exploding_get)
    code, tenders = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []


def test_rate_limited_on_429(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(lambda request: httpx.Response(429)))
    code, _ = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED
