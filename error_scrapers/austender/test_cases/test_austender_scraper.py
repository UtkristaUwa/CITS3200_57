"""
Tests for the AusTender scraper, same discipline as
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

austender_public_list.html, austender_full_details.html and
austender_login_redirect.html are live captures (the last is what an
anonymous request for an ATM's documents page is redirected to).
austender_documents.html is the logged-in documents page, reproduced from
its documented markup since it can't be captured without an account.
The fixtures are trimmed (no scripts/styles, listing cut to 3 ATMs). The
structure-changed page is built at runtime by renaming every "list-desc" class.

Network is faked with httpx.MockTransport rather than a hand-rolled client,
so the real streaming download path is what gets exercised.
"""

import os

import fitz
import httpx
import pytest

from error_scrapers import common
from error_scrapers.austender import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://www.tenders.gov.au/Atm/Show/a64467be-bb82-4228-85e7-5009fe1f162c"


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _structure_changed() -> str:
    return _read_fixture("austender_full_details.html").replace("list-desc", "list-desc-CHANGED")


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _portal(documents_html=None, file_bytes=None, file_status=200):
    """
    A fake tenders.gov.au. documents_html=None means the documents page
    redirects to the login form, as it does for an anonymous visitor.
    """
    file_bytes = _pdf_bytes("Statement of requirements") if file_bytes is None else file_bytes

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/atm":
            if request.url.params.get("page") == "1":
                return httpx.Response(200, text=_read_fixture("austender_public_list.html"))
            return httpx.Response(200, text="<html><body>No results</body></html>")
        if path.startswith("/Atm/Show/"):
            return httpx.Response(200, text=_read_fixture("austender_full_details.html"))
        if path.startswith("/Atm/ViewDocuments/"):
            if documents_html is None:
                return httpx.Response(
                    302, headers={"Location": f"/RegisteredUser/Login?ReturnUrl={path}"}
                )
            return httpx.Response(200, text=documents_html)
        if path == "/RegisteredUser/Login":
            return httpx.Response(200, text=_read_fixture("austender_login_redirect.html"))
        if path.startswith("/Atm/Download"):
            return httpx.Response(
                file_status, content=file_bytes, headers={"content-type": "application/pdf"}
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
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("AUSTENDER_USERNAME", raising=False)
    monkeypatch.delenv("AUSTENDER_PASSWORD", raising=False)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_atm_links():
    links = scraper.parse_listing(_read_fixture("austender_public_list.html"))
    assert len(links) == 3
    assert all("/Atm/Show/" in link for link in links)


def test_listing_links_are_absolute_and_deduplicated():
    # every ATM is linked twice on the listing (reference and "Full Details")
    links = scraper.parse_listing(_read_fixture("austender_public_list.html"))
    assert all(link.startswith("https://www.tenders.gov.au/") for link in links)
    assert len(links) == len(set(links))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_read_fixture("austender_full_details.html"))
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "PNG: Western Province Partnership Mid-Term Review"
    assert fields["atm_id"] == "DFAT-1110"
    assert fields["agency"] == "Department of Foreign Affairs and Trade - Australian Aid Program"
    assert fields["close_date"].startswith("28-Sep-2026 10:00 am")
    assert fields["publish_date"]


def test_site_structure_change_when_fields_container_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_finds_the_documents_page_link():
    url = scraper.find_documents_url(_read_fixture("austender_full_details.html"))
    assert url == "https://www.tenders.gov.au/Atm/ViewDocuments/a64467be-bb82-4228-85e7-5009fe1f162c"


def test_missing_documents_link_does_not_crash():
    html = _read_fixture("austender_full_details.html").replace("/Atm/ViewDocuments/", "/Atm/Nothing/")
    assert scraper.find_documents_url(html) is None


def test_login_redirect_is_recognised_as_a_login_page():
    assert scraper.is_login_page(_read_fixture("austender_login_redirect.html"))
    assert not scraper.is_login_page(_read_fixture("austender_full_details.html"))


def test_documents_page_names_come_from_title_query_or_fallback():
    documents = scraper.parse_documents(_read_fixture("austender_documents.html"))
    assert [d["file_name"] for d in documents] == [
        "Approach to Market - Example Tender.docx",
        "Attachment A - Statement of Requirements.pdf",
        "Addendum 1.pdf",
        "document.bin",
    ]
    assert all(d["url"].startswith("https://www.tenders.gov.au/Atm/Download") for d in documents)


# ---------------------------------------------------------------------------
# Full ATM scrape
# ---------------------------------------------------------------------------

DOCS_ONE_PDF = """
<html><body>
<a title="Statement of Requirements.pdf"
   href="/Atm/DownloadSoftCopy/a64467be?fileName=Statement%20of%20Requirements.pdf">Download</a>
</body></html>
"""


def test_scrape_opportunity_end_to_end(tmp_path):
    with _client(_portal(DOCS_ONE_PDF)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.SITE_SUCCESS
    assert tender["tender_id"] == "DFAT-1110"
    assert tender["source_url"] == DETAIL_URL
    assert [a["file_name"] for a in tender["attachments"]] == ["Statement of Requirements.pdf"]
    assert tender["attachments"][0]["size_bytes"] > 0

    saved = sorted(os.listdir(tender["folder"]))
    assert saved == [
        "Statement of Requirements.pdf",
        "Statement of Requirements.pdf.txt",
        "__tender__DFAT-1110.txt",
    ]
    page_text = open(os.path.join(tender["folder"], "__tender__DFAT-1110.txt")).read()
    assert "PNG: Western Province Partnership Mid-Term Review" in page_text
    extracted = open(os.path.join(tender["folder"], "Statement of Requirements.pdf.txt")).read()
    assert "Statement of requirements" in extracted


def test_documents_behind_login_give_tender_partial_with_page_text_saved(tmp_path):
    with _client(_portal(documents_html=None)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__DFAT-1110.txt"]


def test_no_documents_page_is_success_with_no_attachments(tmp_path):
    html = _read_fixture("austender_full_details.html").replace("/Atm/ViewDocuments/", "/Atm/Nothing/")

    def handler(request):
        return httpx.Response(200, text=html)

    with _client(httpx.MockTransport(handler)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []


def test_a_failed_download_gives_tender_partial_not_a_crash(tmp_path):
    with _client(_portal(DOCS_ONE_PDF, file_status=500)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert tender["attachments"] == []


def test_a_corrupt_pdf_is_kept_but_reported_partial(tmp_path):
    with _client(_portal(DOCS_ONE_PDF, file_bytes=b"not a pdf")) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    # the original is still kept -- someone can open it by hand
    assert [a["file_name"] for a in tender["attachments"]] == ["Statement of Requirements.pdf"]


def test_structure_change_on_detail_page_returns_no_tender(tmp_path):
    def handler(request):
        return httpx.Response(200, text=_structure_changed())

    with _client(httpx.MockTransport(handler)) as client:
        code, tender = scraper.scrape_opportunity(client, DETAIL_URL, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tender == {}


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(DOCS_ONE_PDF))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 1
    assert tenders[0]["attachments"]


def test_run_without_credentials_is_partial_when_documents_are_gated(tmp_path, monkeypatch):
    _patch_client(monkeypatch, _portal(documents_html=None))
    code, tenders = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) >= 1


def test_run_with_credentials_that_do_not_take_is_login_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUSTENDER_USERNAME", "someone@example.com")
    monkeypatch.setenv("AUSTENDER_PASSWORD", "not-a-real-password")
    _patch_client(monkeypatch, _portal(documents_html=None))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    # the page text is still worth keeping
    assert len(tenders) == 1


def test_empty_first_listing_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_client(monkeypatch, httpx.MockTransport(
        lambda request: httpx.Response(200, text="<html><body>nothing</body></html>")
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
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_RATE_LIMITED
