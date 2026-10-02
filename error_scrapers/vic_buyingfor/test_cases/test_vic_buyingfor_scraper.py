"""
Tests for the Buying for Victoria scraper, same discipline as
error_scrapers/tenders_act/test_cases/test_act.py (the same tender platform).

vic_public_list.html, vic_full_details.html (PROCF23-149, ten documents,
anonymous view) and vic_cloudflare_block.html (what a plain HTTP client
gets) are live captures, trimmed (no scripts/styles/search dropdowns; the
listing cut to 3 rows). The structure-changed page is built at runtime by
renaming the #opportunityGeneral id.

No browser runs here: FakeSession stands in for BrowserSession, serving
fixture HTML by URL, so the tests need neither Chrome nor network.
"""

import io
import os
import zipfile

import fitz
import pytest

from error_scrapers import common
from error_scrapers.vic_buyingfor import scraper

FIXTURES = os.path.dirname(__file__)

DETAIL_URL = "https://www.tenders.vic.gov.au/tender/view?id=285775"
ENTRY = {
    "url": DETAIL_URL,
    "rfx": "PROCF23-149",
    "title": "Clinical Advisory Services Register",
    "opening_date": "Mon, 24 February 2025 5:00 pm",
    "closing_date": "Mon, 25 February 2030 5:00 pm",
}


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


def _pdf_bytes(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _signed_in_details() -> str:
    """The detail page as a signed-in supplier sees it: a Download Now link
    (markup as on the same platform's ACT page) instead of the login notice."""
    return _read_fixture("vic_full_details.html").replace(
        "You must be logged in to download documents.",
        '<a href="/secure/tender/downloadSpecDocs?tenderId=285775" class="btn btn-primary">'
        "Download Now</a>",
    )


def _structure_changed() -> str:
    return _read_fixture("vic_full_details.html").replace(
        'id="opportunityGeneral"', 'id="opportunityGeneral-CHANGED"'
    )


def _no_documents_details() -> str:
    return _read_fixture("vic_full_details.html").replace('class="specDoc"', 'class="gone"')


class FakeSession:
    """Stands in for BrowserSession: serves pages by URL, 'downloads' a zip."""

    def __init__(self, detail_html=None, list_html=None, zip_bytes=None,
                 blocked=False, login_ok=True):
        self.detail_html = detail_html or _read_fixture("vic_full_details.html")
        self.list_html = list_html or _read_fixture("vic_public_list.html")
        self.zip_bytes = zip_bytes
        self.blocked = blocked
        self.login_ok = login_ok
        self.visited = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, wait_selector=None):
        self.visited.append(url)
        if self.blocked:
            raise scraper.BotBlockedError(url)
        if "/tenders/open" in url:
            return self.list_html
        return self.detail_html

    def login(self):
        return self.login_ok

    def download_documents(self, url, folder):
        if self.zip_bytes is None:
            raise TimeoutError("no download")
        path = os.path.join(folder, "documents.zip")
        with open(path, "wb") as f:
            f.write(self.zip_bytes)
        return path


def _documents_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Service Specification.pdf", _pdf_bytes("Clinical advisory services"))
        zf.writestr("Invoice Template.xlsx", b"not really a spreadsheet")
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _no_pause_no_credentials(monkeypatch):
    monkeypatch.setattr(scraper, "PAUSE_SECONDS", 0)
    monkeypatch.delenv("VIC_USERNAME", raising=False)
    monkeypatch.delenv("VIC_PASSWORD", raising=False)


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(scraper, "BrowserSession", lambda **kwargs: session)


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_every_tender_row_with_its_dates():
    entries = scraper.parse_listing(_read_fixture("vic_public_list.html"))
    assert len(entries) == 3
    assert entries[1] == ENTRY


def test_listing_links_are_absolute_and_deduplicated():
    entries = scraper.parse_listing(_read_fixture("vic_public_list.html"))
    urls = [e["url"] for e in entries]
    assert all(u.startswith("https://www.tenders.vic.gov.au/tender/view?id=") for u in urls)
    assert len(urls) == len(set(urls))


def test_page_count_comes_from_the_record_summary():
    # "Records: 1 - 25 of 78"
    assert scraper.total_pages(_read_fixture("vic_public_list.html")) == 4
    assert scraper.total_pages("<html></html>") is None


def test_cloudflare_block_page_is_recognised():
    assert scraper.is_blocked("", _read_fixture("vic_cloudflare_block.html"))
    assert scraper.is_blocked("Just a moment...", "")
    assert not scraper.is_blocked("Current Tenders", _read_fixture("vic_public_list.html"))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    fields, code = scraper.parse_detail(_read_fixture("vic_full_details.html"))
    assert code == common.SITE_SUCCESS
    assert fields["title"] == "Clinical Advisory Services Register"
    assert fields["tender_code"] == "PROCF23-149"
    assert fields["agency"] == "Transport Accident Commission"
    assert fields["tender_type"] == "Expression of Interest"
    assert fields["tender_status"] == "Open"
    assert fields["contact_email"] == "clinical_register@tac.vic.gov.au"
    assert fields["description"].startswith("The TAC")


def test_site_structure_change_when_field_section_missing():
    fields, code = scraper.parse_detail(_structure_changed())
    assert code == common.SITE_STRUCTURE_CHANGE
    assert fields == {}


def test_documents_are_listed_even_when_they_cannot_be_downloaded():
    html = _read_fixture("vic_full_details.html")
    documents = scraper.parse_documents(html)
    assert len(documents) == 10
    assert documents[0] == {
        "file_name": "Invitation to Register - Clinical Advisory Services (Individual).DOCX",
        "version": "Version 1 (24 Feb 2025)",
        "size": "106 KB",
    }
    assert scraper.documents_require_login(html)
    assert scraper.find_download_docs_url(html) is None


def test_signed_in_page_offers_the_download_form():
    url = scraper.find_download_docs_url(_signed_in_details())
    assert url == "https://www.tenders.vic.gov.au/secure/tender/downloadSpecDocs?tenderId=285775"


# ---------------------------------------------------------------------------
# Full tender scrape
# ---------------------------------------------------------------------------

def test_anonymous_scrape_saves_page_text_and_reports_documents_gated(tmp_path):
    code, tender = scraper.scrape_opportunity(FakeSession(), ENTRY, str(tmp_path))

    assert code == common.TENDER_PARTIAL
    assert tender["tender_id"] == "PROCF23-149"
    assert tender["documents_gated"] is True
    assert tender["attachments"] == []
    assert os.listdir(tender["folder"]) == ["__tender__PROCF23-149.txt"]

    text = open(os.path.join(tender["folder"], "__tender__PROCF23-149.txt")).read()
    assert "closing_date: Mon, 25 February 2030 5:00 pm" in text
    assert "SPECIFICATION DOCUMENTS:" in text
    assert "Service Specification" in text


def test_signed_in_scrape_unpacks_the_document_zip(tmp_path):
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=_documents_zip())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))

    # the fake .xlsx can't be read, so the tender is partial -- but kept
    assert code == common.TENDER_PARTIAL
    assert sorted(a["file_name"] for a in tender["attachments"]) == [
        "Invoice Template.xlsx", "Service Specification.pdf",
    ]
    saved = sorted(os.listdir(tender["folder"]))
    assert "documents.zip" not in saved
    assert "Service Specification.pdf.txt" in saved


def test_signed_in_scrape_with_readable_documents_is_success(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Service Specification.pdf", _pdf_bytes("Clinical advisory services"))
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=buf.getvalue())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert [a["file_name"] for a in tender["attachments"]] == ["Service Specification.pdf"]


def test_a_failed_download_gives_tender_partial_not_a_crash(tmp_path):
    session = FakeSession(detail_html=_signed_in_details(), zip_bytes=None)
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert os.listdir(tender["folder"]) == ["__tender__PROCF23-149.txt"]


def test_no_documents_is_success(tmp_path):
    session = FakeSession(detail_html=_no_documents_details())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert tender["documents_gated"] is False


def test_structure_change_on_detail_page_returns_no_tender(tmp_path):
    session = FakeSession(detail_html=_structure_changed())
    code, tender = scraper.scrape_opportunity(session, ENTRY, str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE
    assert tender == {}


# ---------------------------------------------------------------------------
# Site-level results
# ---------------------------------------------------------------------------

def test_run_scraper_respects_limit(tmp_path, monkeypatch):
    session = FakeSession(detail_html=_no_documents_details())
    _patch_session(monkeypatch, session)
    code, tenders = scraper.run_scraper(limit=2, output_dir=str(tmp_path))
    assert code == common.SITE_SUCCESS
    assert len(tenders) == 2


def test_run_walks_every_reported_page(monkeypatch):
    session = FakeSession()
    entries = scraper.collect_all_listing_entries(session)
    # every fixture page returns the same 3 rows, so page 2 adds nothing
    # new and the walk stops there instead of asking for pages 3 and 4
    assert len(entries) == 3
    assert session.visited == [scraper.LIST_URL, f"{scraper.LIST_URL}?page=2"]


def test_run_without_credentials_is_partial_when_documents_are_gated(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession())
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.TENDER_PARTIAL
    assert len(tenders) == 1


def test_failed_login_is_reported_but_page_text_is_still_scraped(tmp_path, monkeypatch):
    monkeypatch.setenv("VIC_USERNAME", "someone@example.com")
    monkeypatch.setenv("VIC_PASSWORD", "not-a-real-password")
    _patch_session(monkeypatch, FakeSession(login_ok=False))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_LOGIN_FAILED
    assert len(tenders) == 1


def test_cloudflare_block_is_site_bot_blocked(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(blocked=True))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_BOT_BLOCKED
    assert tenders == []


def test_empty_first_listing_page_is_a_structure_change(tmp_path, monkeypatch):
    _patch_session(monkeypatch, FakeSession(list_html="<html><body>nothing</body></html>"))
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_STRUCTURE_CHANGE


def test_browser_that_will_not_start_is_total_failure(tmp_path, monkeypatch):
    def no_browser(**kwargs):
        raise RuntimeError("chrome not found")

    monkeypatch.setattr(scraper, "BrowserSession", no_browser)
    code, tenders = scraper.run_scraper(limit=1, output_dir=str(tmp_path))
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []
