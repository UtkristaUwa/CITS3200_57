"""
Tests for the Tenders ACT scraper, written test-first -- every function
here exists to satisfy a specific test, same discipline as
error_scrapers/grant_connect/test_cases/test_grantconnect.py and
error_scrapers/buy_nsw/test_cases/test_buynsw.py.

ACT needs a login step (like GrantConnect) plus a two-stage document
flow buy.nsw didn't have: the tender detail page links to a
"download docs" page listing every document as a checked checkbox,
and *that* page's form is POSTed back (with the selected ids) to
actually receive the zip.
"""

import io
import os
import zipfile

import pytest

from error_scrapers import common
from error_scrapers.tenders_act import scraper

FIXTURES = os.path.dirname(__file__)


def _read_fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_page_has_no_error():
    html = _read_fixture("act_login_page.html")
    assert scraper.login_failed(html) is False


def test_login_failed_detects_error_message():
    html = _read_fixture("act_login_failed.html")
    assert scraper.login_failed(html) is True


def test_login_form_fields_extracted_correctly():
    html = _read_fixture("act_login_page.html")
    fields = scraper.parse_login_form(html)
    assert fields["_csrf"] == "test-csrf-token"
    assert fields["businessType"] == "SUPPLIER"
    assert fields["tenantCode"] == "act"


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_listing_finds_tender_links():
    html = _read_fixture("act_public_list.html")
    links = scraper.parse_listing(html)
    assert len(links) == 22
    assert all("/tender/view?id=" in link for link in links)


def test_listing_links_are_absolute_and_deduplicated():
    html = _read_fixture("act_public_list.html")
    links = scraper.parse_listing(html)
    assert all(link.startswith("http") for link in links)
    assert len(links) == len(set(links))


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------

def test_success_parses_full_details_page():
    html = _read_fixture("act_tender_detail.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_SUCCESS
    assert fields["title"] == (
        "Public Housing Disability Modification and Domestic Violence "
        "Upgrade Services Multi Use List (MUL)"
    )
    assert fields["agency"] == "ACT Government"
    assert fields["tender_code"] == "PIMA0014211"
    assert fields["tender_type"] == "Other Arrangements"


def test_site_structure_change_when_general_section_missing():
    # act_structure_changed_detail.html is act_tender_detail.html with
    # id="opportunityGeneral" renamed to id="opportunityGeneral_CHANGED"
    # -- the exact container parse_detail() selects fields from.
    html = _read_fixture("act_structure_changed_detail.html")
    fields, code = scraper.parse_detail(html)
    assert code == common.SITE_STRUCTURE_CHANGE


def test_finds_the_download_docs_url():
    html = _read_fixture("act_tender_detail.html")
    url = scraper.find_download_docs_url(html)
    assert url is not None
    assert "downloadSpecDocs?tenderId=330643" in url


def test_missing_download_link_does_not_crash():
    html = _read_fixture("act_tender_detail.html").replace(
        "Download Now", "Nothing to see here"
    )
    url = scraper.find_download_docs_url(html)
    assert url is None


# ---------------------------------------------------------------------------
# Download-docs page parsing
# ---------------------------------------------------------------------------

def test_parses_download_form_action_and_hidden_fields():
    html = _read_fixture("act_download_docs_page.html")
    form = scraper.parse_download_form(html)
    assert form["action"] == "/secure/tender/downloadSpecDocs"
    assert form["opportunityId"] == "330643"
    assert form["_csrf"] == "test-csrf-token"


def test_collects_every_checked_document_id():
    html = _read_fixture("act_download_docs_page.html")
    form = scraper.parse_download_form(html)
    # 4 Original Documents + 2 Addenda, all checked by default
    assert len(form["ids"]) == 6
    assert "2522065" in form["ids"]
    assert "2532527" in form["ids"]


def test_site_structure_change_when_checkboxes_missing():
    # act_structure_changed_downloads.html is act_download_docs_page.html
    # with name="ids[]" renamed to name="ids_CHANGED[]" -- the exact
    # attribute parse_download_form() relies on.
    html = _read_fixture("act_structure_changed_downloads.html")
    form = scraper.parse_download_form(html)
    assert form["code"] == common.SITE_STRUCTURE_CHANGE


# ---------------------------------------------------------------------------
# Full opportunity scrape (mocked network)
# ---------------------------------------------------------------------------

def _build_fake_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("MUL_Attachment A -Categories of Service.pdf", b"%PDF-1.4 fake a")
        zf.writestr("PIMA0014211 - Addendum 2.pdf", b"%PDF-1.4 fake b")
    buf.seek(0)
    return buf.read()


class _FakeResponse:
    def __init__(self, text="", content=b"", status_code=200):
        self.text = text
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeClient:
    """Stands in for httpx.Client -- routes by method + URL."""

    def __init__(self, detail_html, download_docs_html, zip_bytes,
                 login_should_fail=False):
        self.detail_html = detail_html
        self.download_docs_html = download_docs_html
        self.zip_bytes = zip_bytes
        self.login_should_fail = login_should_fail
        self.logged_in = False

    def get(self, url, **kwargs):
        if "downloadSpecDocs" in url:
            return _FakeResponse(text=self.download_docs_html)
        if "/login" in url:
            return _FakeResponse(text=_read_fixture("act_login_page.html"))
        return _FakeResponse(text=self.detail_html)

    def post(self, url, **kwargs):
        if "downloadSpecDocs" in url:
            return _FakeResponse(content=self.zip_bytes)
        if "/login" in url:
            if self.login_should_fail:
                return _FakeResponse(text=_read_fixture("act_login_failed.html"))
            self.logged_in = True
            return _FakeResponse(text="<html>logged in</html>")
        return _FakeResponse()


def test_login_success_returns_true(monkeypatch):
    client = _FakeClient("", "", b"")
    assert scraper.login(client) is True


def test_login_failure_returns_false():
    client = _FakeClient("", "", b"", login_should_fail=True)
    assert scraper.login(client) is False


def test_scrape_opportunity_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(scraper.common, "extract_pdf", lambda path: "fake text")
    detail_html = _read_fixture("act_tender_detail.html")
    download_docs_html = _read_fixture("act_download_docs_page.html")
    client = _FakeClient(detail_html, download_docs_html, _build_fake_zip())

    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["title"] == (
        "Public Housing Disability Modification and Domestic Violence "
        "Upgrade Services Multi Use List (MUL)"
    )
    assert len(tender["attachments"]) == 2


def test_scrape_opportunity_still_succeeds_with_no_download_link(tmp_path):
    detail_html = _read_fixture("act_tender_detail.html").replace(
        "Download Now", "Nothing to see here"
    )
    client = _FakeClient(detail_html, "", b"")
    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.SITE_SUCCESS
    assert tender["attachments"] == []


def test_a_corrupt_zip_download_gives_tender_partial_not_a_crash(tmp_path):
    detail_html = _read_fixture("act_tender_detail.html")
    download_docs_html = _read_fixture("act_download_docs_page.html")
    client = _FakeClient(detail_html, download_docs_html, b"not a valid zip")

    code, tender = scraper.scrape_opportunity(
        client, "https://www.tenders.act.gov.au/tender/view?id=330643", str(tmp_path)
    )
    assert code == common.TENDER_PARTIAL


# ---------------------------------------------------------------------------
# Site-level failures
# ---------------------------------------------------------------------------

def test_site_login_failed_stops_the_run():
    client = _FakeClient("", "", b"", login_should_fail=True)
    code, tenders = scraper.run_scraper(client_override=client)
    assert code == common.SITE_LOGIN_FAILED
    assert tenders == []


def test_site_total_failure_on_unreachable_url(monkeypatch):
    import httpx

    def exploding_get(*args, **kwargs):
        raise httpx.ConnectError("connection failed")

    monkeypatch.setattr(httpx.Client, "get", exploding_get)
    monkeypatch.setattr(httpx.Client, "post", exploding_get)
    code, tenders = scraper.run_scraper()
    assert code == common.SITE_TOTAL_FAILURE
    assert tenders == []